"""Треки «в мире игры» для слоёв ``tracked_text`` (слово на поверхности) и ``trail`` (след броска).

Сервисная обвязка над чистыми алгоритмами (app/surface_track.py — плоскость, app/object_track.py — летящий
объект и камера):

* кадры — основа клипа, как у трекинга слоёв (app/tracking.iter_base_frames: тот же граф ffmpeg, что у
  рендера и ``view_frame``, рефрейм, кроп, куски) в размере детекции (540×960 у 9:16), шкала clip;
* тяжёлое — в очереди тяжёлых задач (``heavy_slot("tracking")``);
* трек — JSON в ``data/tracks/<id>.json`` (``trk_…``), слой ссылается на него ``props.track``; в рендер, кадр
  агента и превью он попадает компактным ``props.trackData`` (``attach_track_data`` в
  app/motion_render.resolve_layer_assets, ``GET /api/motion/tracks/{id}`` для превью);
* ``suggest_surfaces`` — окна под слово рядом с центром кадра (фактурные, плоские, не небо и не HUD).

Время в треке — шкала clip (до вырезов, как у слоёв). Правка кусков после трека (trim, вырезы) его не
двигает: трек считается заново (``track_surface`` с теми же параметрами — кэш по кадрам не нужен, это
секунды работы).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import time
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np

from app.tracking_normalize import TrackError

log = logging.getLogger(__name__)

TRACK_ID_RE = re.compile(r"^trk_[0-9a-f]{12}$")
FPS_RANGE = (10.0, 60.0)
DEFAULT_FPS = 30.0
MAX_RANGE_SEC = 20.0
SURFACE_OK = 0.6  # доля «держится» (ok/weak) под словом, ниже — слово на экране, а не на поверхности
STATUS_CODE = {"ok": "o", "weak": "w", "pred": "p", "lost": "l", "d": "d", "w": "w", "p": "p", "l": "l", "e": "e"}


def tracks_dir() -> Path:
    from app.settings import settings

    path = Path(settings.data_dir) / "tracks"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _num(value: Any, default: float | None = None) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


# ---------------------------------------------------------------- хранилище треков


def save_track(data: dict) -> str:
    raw = json.dumps({k: data.get(k) for k in ("kind", "plan_id", "seed", "range", "fps")}, sort_keys=True, default=str)
    track_id = "trk_" + hashlib.sha1((raw + str(time.time_ns())).encode()).hexdigest()[:12]
    data = {**data, "id": track_id, "created": round(time.time(), 1)}
    (tracks_dir() / f"{track_id}.json").write_text(json.dumps(data), encoding="utf-8")
    return track_id


def load_track(track_id: Any) -> dict | None:
    text = str(track_id or "")
    if not TRACK_ID_RE.match(text):
        return None
    path = tracks_dir() / f"{text}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def engine_data(track: dict) -> dict:
    """Компактный трек для движка (frontend/src/motion/types.ts TrackData)."""
    samples = track.get("samples") or []
    out: dict = {"kind": track.get("kind"), "t": [round(float(s["t"]), 4) for s in samples],
                 "s": "".join(STATUS_CODE.get(str(s.get("s")), "l") for s in samples)}
    if track.get("kind") == "surface":
        out["q"] = [round(float(v), 5) for s in samples for v in s["q"]]
        if track.get("aspect"):
            out["aspect"] = round(float(track["aspect"]), 4)
    else:
        out["p"] = [round(float(v), 5) for s in samples for v in (s["x"], s["y"])]
        out["g"] = [round(float(v), 6) for s in samples for v in s["g"]]
    return out


def attach_track_data(layer: dict, notes: list[str] | None = None) -> dict | None:
    """Слою ``tracked_text``/``trail`` — данные его трека в ``props.trackData``; трека нет — None (слой пропустить)."""
    if layer.get("type") not in ("tracked_text", "trail"):
        return layer
    props = layer.get("props") or {}
    if isinstance(props.get("trackData"), dict) and props["trackData"].get("t"):
        return layer
    track = load_track(props.get("track"))
    if not track:
        if notes is not None:
            notes.append(f"слой {layer.get('id')}: трек {props.get('track')!r} не найден — пропущен "
                         "(track_surface / track_object заново)")
        return None
    want = "surface" if layer["type"] == "tracked_text" else "object"
    if track.get("kind") != want:
        if notes is not None:
            notes.append(f"слой {layer.get('id')}: трек {props.get('track')} — {track.get('kind')}, а слою нужен {want}")
        return None
    return {**layer, "props": {**props, "trackData": engine_data(track)}}


def quad_aspect(quad: list, w: float, h: float) -> float:
    q = np.asarray(quad, np.float64).reshape(4, 2) * np.array([w, h])
    top = np.linalg.norm(q[1] - q[0]) + np.linalg.norm(q[2] - q[3])
    side = np.linalg.norm(q[3] - q[0]) + np.linalg.norm(q[2] - q[1])
    return float(top / max(1e-6, side))


# ---------------------------------------------------------------- вход


def normalize_quad(args: dict, aspect_wh: float) -> list[list[float]]:
    """``quad`` [[x,y]×4] или ``box`` {x, y, w, h, angle?} (центр и размер в долях кадра) → 4 угла."""
    from app.surface_track import rect_quad

    quad = args.get("quad")
    if quad is not None:
        try:
            flat = [float(v) for v in np.asarray(quad, dtype=np.float64).reshape(-1)]
        except (TypeError, ValueError) as exc:
            raise TrackError("quad — 4 точки [[x, y], …] в долях кадра") from exc
        if len(flat) != 8 or not all(-0.5 <= v <= 1.5 for v in flat):
            raise TrackError("quad — 4 точки [[x, y], …] в долях кадра (0..1)")
        from app.surface_pose import order_quad

        # Углы по часовой с левого верхнего, как бы их ни дали: обход у семени всегда один (иначе текст зеркалится).
        return [[round(float(a), 5), round(float(b), 5)] for a, b in order_quad(np.array(flat).reshape(4, 2))]
    box = args.get("box") if isinstance(args.get("box"), dict) else args
    x, y = _num(box.get("x")), _num(box.get("y"))
    w, h = _num(box.get("w")), _num(box.get("h"))
    if x is None or y is None or w is None:
        raise TrackError("нужна поверхность: quad [[x,y]×4] или box {x, y, w, h} (центр и размер, доли кадра)",
                         ["места — suggest_surfaces(t, text)"])
    if h is None:
        h = w * 0.3 * aspect_wh
    angle = _num(box.get("angle"), 0.0) or 0.0
    q = rect_quad(x, y, w, h, 0.0)
    if angle:
        # Поворот — в px кадра (ширина кадра = aspect_wh, высота = 1): у 9:16 доли по осям разные.
        c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
        out = []
        for px, py in q:
            dx, dy = (px - x) * aspect_wh, py - y
            out.append([x + (dx * c - dy * s) / aspect_wh, y + (dx * s + dy * c)])
        q = out
    return [[round(a, 5), round(b, 5)] for a, b in q]


# ---------------------------------------------------------------- кадры клипа


def band_regions(store: Any, plan: dict, setup: dict) -> dict[int, dict]:
    """Раскладка «полоса» (meme: видео полосой на фоне): где видео каждого куска в кадре, доли кадра
    ``{кусок: {x, y, w, h}}``. Нет полосы (другая раскладка, кусок «шире окна», видео во весь кадр) — пусто."""
    from app.backgrounds import meme_hole
    from app.stream_layout import normalize_layout
    from app.video_crop import normalize_fit

    layout = (setup.get("preset") or {}).get("layout")
    if not isinstance(layout, dict) or layout.get("mode") != "meme":
        return {}
    lay = normalize_layout(layout)
    width, height = int(setup["width"]), int(setup["height"])
    out: dict[int, dict] = {}
    for index, segment in enumerate(plan.get("segments") or []):
        if normalize_fit(segment.get("fit")):
            continue  # кусок «шире окна» — своя основа, раскладка на него не действует
        source = store.get_source(int(segment.get("source_id") or plan["source_id"]))
        hole = meme_hole(lay, source, source.get("content_crop"), width, height)
        if hole and hole["w"] > 0 and hole["h"] > 0:
            out[index] = {"x": hole["x"] / width, "y": hole["y"] / height, "w": hole["w"] / width,
                          "h": hole["h"] / height}
    return out


def crop_to_band(frames: list[dict], regions: dict[int, dict]) -> list[dict]:
    """Кадры куска с полосой — только видео полосы (детекция не смотрит в чёрное поле и титры);
    ``full`` — кадр целиком (рисовать подсказки), ``region`` — где полоса (доли кадра)."""
    out = []
    for frame in frames:
        region = regions.get(frame.get("piece"))
        if not region:
            out.append(frame)
            continue
        img = frame["img"]
        h, w = img.shape[:2]
        x0, y0 = int(round(region["x"] * w)), int(round(region["y"] * h))
        x1, y1 = int(round((region["x"] + region["w"]) * w)), int(round((region["y"] + region["h"]) * h))
        out.append({**frame, "img": np.ascontiguousarray(img[y0:y1, x0:x1]), "full": img,
                    "region": {"x": x0 / w, "y": y0 / h, "w": (x1 - x0) / w, "h": (y1 - y0) / h}})
    return out


def to_band(region: dict | None, x: float, y: float) -> tuple[float, float]:
    if not region:
        return x, y
    return (x - region["x"]) / region["w"], (y - region["y"]) / region["h"]


def from_band(region: dict | None, x: float, y: float) -> tuple[float, float]:
    if not region:
        return x, y
    return region["x"] + x * region["w"], region["y"] + y * region["h"]


def _map_quad(quad: Any, region: dict | None, fn: Callable) -> Any:
    if quad is None or not region:
        return quad
    flat = np.asarray(quad, dtype=np.float64).reshape(-1, 2)
    pts = [fn(region, float(a), float(b)) for a, b in flat]
    if isinstance(quad, list) and quad and isinstance(quad[0], (list, tuple)):
        return [[round(a, 5), round(b, 5)] for a, b in pts]
    return [round(v, 5) for p in pts for v in p]


def clip_frames(store: Any, plan_id: int, start: float, end: float, fps: float,
                progress: Callable[[float], None] | None = None, band: bool = False) -> tuple[list[dict], dict]:
    """Кадры основы клипа ``[{t, piece, img}]`` (размер детекции) и настройки кадра (app/tracking._frame_setup).
    ``band`` — в раскладке «полоса» кадры обрезаны до видео полосы (``crop_to_band``)."""
    from app import tracking

    plan = store.get_clip_plan(plan_id)
    if not plan.get("segments"):
        raise TrackError("в клипе нет кусков")
    setup = tracking._frame_setup(store, plan)
    spans = tracking.piece_spans(plan, setup["rs"])
    total = spans[-1][2] if spans else 0.0
    start, end = max(0.0, float(start)), min(total, float(end))
    if end - start < 0.1:
        raise TrackError(f"диапазон {start:.2f}–{end:.2f} с вне клипа (длина {total:.2f} с)")
    if end - start > MAX_RANGE_SEC:
        raise TrackError(f"трек длиннее {MAX_RANGE_SEC:g} с — дроби по словам/броскам")
    span = max(1e-6, end - start)
    frames = list(tracking.iter_base_frames(store, plan, setup, start, end, fps,
                                            on_frame=(lambda t: progress((t - start) / span)) if progress else None))
    if not frames:
        raise TrackError("кадров для трека нет")
    if band:
        regions = band_regions(store, plan, setup)
        if regions:
            frames = crop_to_band(frames, regions)
    return frames, setup


def _fps(value: Any) -> float:
    return min(FPS_RANGE[1], max(FPS_RANGE[0], _num(value, DEFAULT_FPS) or DEFAULT_FPS))


def _nearest(frames: list[dict], t: float) -> int:
    return int(np.argmin([abs(float(f["t"]) - t) for f in frames]))


def _piece_runs(frames: list[dict], seed: int) -> tuple[int, int]:
    """Трек не переходит склейку: границы куска, в котором кадр-семя."""
    piece = frames[seed].get("piece")
    a = seed
    while a > 0 and frames[a - 1].get("piece") == piece:
        a -= 1
    b = seed
    while b + 1 < len(frames) and frames[b + 1].get("piece") == piece:
        b += 1
    return a, b


def surface_check(track: dict, rotation: float = 0.0, show: Any = None) -> dict:
    """Проверка позы трека под слово (app/surface_pose.check_surface): перевороты, угол текста на экране с поворотом
    слоя, шаг и разброс поворота, дрожь углов (px кадра 1080)."""
    from app.surface_pose import check_surface

    fr = track.get("frame") or {}
    w = float(fr.get("dw") or 540)
    h = float(fr.get("dh") or 960)
    return check_surface(track.get("samples") or [], frame=(w, h), rotation=float(rotation or 0.0), show=show,
                         out_width=float(fr.get("w") or 1080))


def surface_report(track: dict, rotation: float = 0.0, show: Any = None) -> dict:
    return {"track_id": track.get("id"), "kind": "surface", "range": track.get("range"), "fps": track.get("fps"),
            "seed": track.get("seed"), "held": track.get("found"), "ok_share": track.get("ok_share"),
            "err_px": track.get("err_px"), "lost": track.get("lost"), "rejects": track.get("rejects") or {},
            "orient": track.get("orient"),
            "check": surface_check(track, rotation, show), "notes": track.get("notes") or []}


def orient_quad(frames: list[dict], seed_t: float, box: dict) -> tuple[list, dict]:
    """Четырёхугольник по ориентации поверхности в окне ``box`` кадра-семени (app/surface_orient.py)."""
    from app.surface_orient import plane_quad
    from app.surface_track import to_gray

    return plane_quad(to_gray(frames[_nearest(frames, seed_t)]["img"]), box)


def compute_surface(frames: list[dict], seed_t: float, quad: list | None = None, *, smooth: float = 0.35,
                    box: dict | None = None, perspective: str = "full") -> dict:
    """Трек плоскости по готовым кадрам (кадры одного куска режутся по склейкам). ``box`` вместо ``quad`` —
    четырёхугольник строится по линиям поверхности (точки схода), слово ляжет вдоль стены в глубину."""
    from app.surface_track import track_plane

    orient: dict | None = None
    if quad is None:
        if box is None:
            raise TrackError("нужна поверхность: quad или box")
        quad, orient = orient_quad(frames, seed_t, box)
    seed = _nearest(frames, seed_t)
    a, b = _piece_runs(frames, seed)
    part = frames[a:b + 1]
    try:
        res = track_plane(part, seed - a, quad, smooth=smooth, perspective=perspective)
    except ValueError as exc:
        raise TrackError(str(exc), ["другое место: suggest_surfaces(t, text)"]) from exc
    notes = []
    if a > 0 or b < len(frames) - 1:
        notes.append(f"склейка: трек только в куске {part[0]['t']:.2f}–{part[-1]['t']:.2f} с")
    h, w = part[0]["img"].shape[:2]
    if orient and orient.get("fallback"):
        notes.append(f"ориентация поверхности не найдена ({orient['fallback']}) — прямоугольник окна")
    out = {**res, "kind": "surface", "aspect": round(quad_aspect(quad, w, h), 4),
           "range": [part[0]["t"], part[-1]["t"]], "notes": notes, "seed_quad": quad}
    if orient is not None:
        out["orient"] = orient
    return out


def track_surface(store: Any, plan_id: int, *, t: float, start: float, end: float, quad: list | None = None,
                  fps: Any = None, smooth: Any = None, progress: Callable[[float], None] | None = None,
                  box: dict | None = None, perspective: str = "full") -> dict:
    """Плоскость под слово: трек ``start..end`` (clip) от кадра ``t`` с поверхностью ``quad`` (доли кадра в момент t)."""
    from app.heavy_queue import heavy_slot

    fps_v = _fps(fps)
    if not (start - 1e-3 <= t <= end + 1e-3):
        raise TrackError(f"t={t:.2f} вне диапазона трека {start:.2f}–{end:.2f}")
    with heavy_slot("tracking", f"клип {plan_id}: поверхность"):
        frames, setup = clip_frames(store, plan_id, start, end, fps_v, progress, band=True)
        # Раскладка «полоса»: поверхность ищем в видео полосы, а не в собранном кадре с чёрным полем —
        # quad/box (доли кадра) в доли полосы, трек обратно в доли кадра (слой рисуется в кадре).
        region = frames[_nearest(frames, t)].get("region")
        if region:
            quad = _map_quad(quad, region, to_band)
            if box is not None:
                bx, by = to_band(region, float(box["x"]), float(box["y"]))
                box = {**box, "x": bx, "y": by, "w": float(box["w"]) / region["w"], "h": float(box["h"]) / region["h"]}
        res = compute_surface(frames, t, quad, smooth=_num(smooth, 0.35) or 0.0, box=box, perspective=perspective)
        if region:
            for sample in res.get("samples") or []:
                if sample.get("q") is not None:
                    sample["q"] = _map_quad(sample["q"], region, from_band)
            res["seed_quad"] = _map_quad(res.get("seed_quad"), region, from_band)
            quad = _map_quad(quad, region, from_band)
            res["band"] = {k: round(v, 4) for k, v in region.items()}
            res.setdefault("notes", []).append("раскладка «полоса»: поверхность искалась в видео полосы")
    res.update(plan_id=plan_id, fps=fps_v, seed={"t": round(t, 3), "quad": res.get("seed_quad") or quad},
               frame={"w": setup["width"], "h": setup["height"], "dw": setup["dw"], "dh": setup["dh"]})
    res["id"] = save_track(res)
    return res


def object_report(track: dict) -> dict:
    return {"track_id": track.get("id"), "kind": "object", "range": track.get("range"), "fps": track.get("fps"),
            "seed": track.get("seed"), "found": track.get("found"), "lost_at": track.get("lost_at"),
            "points": sum(1 for s in track.get("samples") or [] if s.get("s") in ("d", "w")),
            "notes": track.get("notes") or []}


def compute_object(frames: list[dict], seed_t: float, x: float, y: float, *, w: float = 0.035, h: float | None = None,
                   hints: Iterable[dict] | None = None, back: bool = False, lead_in: float = 0.0,
                   stop_at_rest: bool = True) -> dict:
    from app.object_track import track_point

    seed = _nearest(frames, seed_t)
    a, b = _piece_runs(frames, seed)
    part = frames[a:b + 1]
    res = track_point(part, seed - a, x, y, w=w, h=h, hints=list(hints or []), back=back, lead_in=lead_in,
                      stop_at_rest=stop_at_rest)
    if not res.get("range"):
        raise TrackError("объект не найден в кадре-семени", ["точнее x/y (view_frame в момент t) или подсказки hints"])
    return {**res, "kind": "object", "notes": []}


def normalize_hints(raw: Any) -> list[dict]:
    out = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        t, x, y = _num(item.get("t")), _num(item.get("x")), _num(item.get("y"))
        if t is None or x is None or y is None or not (-0.2 <= x <= 1.2 and -0.2 <= y <= 1.2):
            continue
        out.append({"t": t, "x": x, "y": y})
    return sorted(out, key=lambda h: h["t"])[:40]


def track_object(store: Any, plan_id: int, *, t: float, x: float, y: float, start: float, end: float,
                 w: Any = None, h: Any = None, hints: Any = None, fps: Any = None, back: bool = False,
                 lead_in: float = 0.0, stop_at_rest: bool = True,
                 progress: Callable[[float], None] | None = None) -> dict:
    from app.heavy_queue import heavy_slot

    fps_v = _fps(fps if fps is not None else 60.0)
    ww = min(0.3, max(0.008, _num(w, 0.035) or 0.035))
    hh = _num(h)
    with heavy_slot("tracking", f"клип {plan_id}: бросок"):
        frames, setup = clip_frames(store, plan_id, start, end, fps_v, progress)
        res = compute_object(frames, t, x, y, w=ww, h=hh, hints=normalize_hints(hints), back=back,
                             lead_in=min(0.5, max(0.0, float(lead_in or 0.0))), stop_at_rest=stop_at_rest)
    res.update(plan_id=plan_id, fps=fps_v, seed={"t": round(t, 3), "x": x, "y": y, "w": ww},
               frame={"w": setup["width"], "h": setup["height"], "dw": setup["dw"], "dh": setup["dh"]})
    res["id"] = save_track(res)
    return res


# ---------------------------------------------------------------- где поставить слово


def text_aspect(text: str, lines: int = 1) -> float:
    """Ширина/высота строки текста узкими капсами (Oswald ~0.5 em на букву)."""
    rows = [r for r in str(text or "").split("\n") if r.strip()] or ["WORD"]
    longest = max(len(r) for r in rows)
    return max(1.0, longest * 0.5 / (1.05 * max(lines, len(rows))))


def suggest_in_frames(frames: list[dict], t: float, text: str = "", n: int = 3, width: float = 0.5) -> list[dict]:
    from app.surface_track import suggest_surfaces

    i = _nearest(frames, t)
    a, b = _piece_runs(frames, i)
    part = frames[max(a, i - 8): min(b, i + 8) + 1]
    return suggest_surfaces(part, i - max(a, i - 8), aspect=text_aspect(text), width=width, n=n)


def suggest(store: Any, plan_id: int, t: float, text: str = "", n: int = 3, width: float = 0.5) -> tuple[list[dict], dict, np.ndarray]:
    """Окна под слово у момента t (clip) и кадр детекции с ними (BGR)."""
    from app.heavy_queue import heavy_slot

    with heavy_slot("tracking", f"клип {plan_id}: места под слово"):
        frames, setup = clip_frames(store, plan_id, max(0.0, t - 0.3), t + 0.3, 30.0, band=True)
        cands = suggest_in_frames(frames, t, text, n, width)
    seed = frames[_nearest(frames, t)]
    region = seed.get("region")
    if region:
        # Раскладка «полоса»: окна искались в видео полосы — в доли всего кадра (как view_frame и слои).
        for c in cands:
            c["x"], c["y"] = (round(v, 4) for v in from_band(region, c["x"], c["y"]))
            c["w"], c["h"] = round(c["w"] * region["w"], 4), round(c["h"] * region["h"], 4)
            c["quad"] = _map_quad(c["quad"], region, from_band)
        setup = {**setup, "band": {k: round(v, 4) for k, v in region.items()}}
    img = (seed.get("full") if region else seed["img"]).copy()
    return cands, setup, img


def draw_candidates(img: np.ndarray, cands: list[dict]) -> bytes:
    import cv2

    out = img.copy()
    h, w = out.shape[:2]
    colors = [(0, 255, 255), (0, 160, 255), (255, 120, 0), (180, 0, 255), (0, 255, 0)]
    for k, c in enumerate(cands):
        q = (np.asarray(c["quad"]) * [w, h]).astype(np.int32)
        cv2.polylines(out, [q], True, colors[k % len(colors)], 3)
        cv2.putText(out, str(k + 1), (int(q[0][0]) + 6, int(q[0][1]) + 30), cv2.FONT_HERSHEY_SIMPLEX, 1.0,
                    colors[k % len(colors)], 3)
    ok, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return buf.tobytes() if ok else b""


__all__ = ["track_surface", "track_object", "suggest", "attach_track_data", "load_track", "engine_data",
           "compute_surface", "compute_object", "suggest_in_frames", "text_aspect"]
