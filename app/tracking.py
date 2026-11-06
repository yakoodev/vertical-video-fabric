"""Трекинг: слой (эмодзи, стрелка, плашка, текст, стикер) следует за лицом, объектом или точкой.

Как это устроено (docs/motion/spec.md, «Трекинг слоя»):

1. **Кадры основы клипа.** Цель ищется не в исходнике, а в том же кадре, что видит зритель: основа
   клипа собирается тем же графом ffmpeg, что у рендера и ``view_frame`` (рефрейм x(t), кроп полей,
   раскладка стрима split/pip/meme, «шире окна», зеркало, хромакей, формат вывода), только
   5–10 кадров в секунду и в половину размера. Поэтому координаты цели сразу в долях кадра клипа —
   переводить из исходника нечего, и любая будущая раскладка тоже «бесплатна».
2. **Цель.** ``face`` — лица (YuNet, тот же детектор, что у автофокуса); ``object`` — объект класса
   COCO (YOLOX: человек, машина, собака…); ``point`` — точка/область кадра, указанная вручную:
   шаблон, который ищется в окне вокруг прогноза нормированной корреляцией (cv2.matchTemplate,
   без OpenCV — то же через numpy FFT). Детекция по кадрам связывается в один трек по близости к
   прогнозу (скорость), пропуски детекции держатся и экстраполируются до ``MAX_GAP_SEC``; дольше —
   цель потеряна (стоит на последнем месте, интервал попадает в отчёт). Цель видна меньше чем в
   ``FAIL_FOUND_SHARE`` кадров — отказ с понятным сообщением.
3. **Сглаживание** — Калман с постоянной скоростью + проход назад (RTS): без запаздывания и без
   «притяжения» краёв; сэмплы-прогнозы и потери тянут слабо. Отдельно на каждом куске.
4. **Ключи слоя** ``x``/``y`` (и ``scale`` по размеру цели) — во времени слоя, с ``ease: linear``,
   прорежены Дугласом–Пекером до ≤ 200 точек; на стыке кусков — ступенька (``step``). Слой получает
   пометку ``tracking`` — повторный запуск заменяет ключи, ``untrack`` их снимает; вырезы, стоп-кадры
   и правка кусков двигают ключи такого слоя вместе с содержимым (app/cuts.remap_layers).
5. **Камера.** Ключи — координаты на основе ДО наезда; слой получает ``followCamera`` (едет и
   зумится с видео), если цель не в окошке раскладки, которое камера не двигает (pip / полоса вебки).

Тяжёлое (кадры + детекция) — в очереди тяжёлых задач (app/heavy_queue.py), результат кэшируется по
источнику, кадрированию, цели и диапазону: смена отступа или сглаживания не гоняет детекцию заново.

Где что лежит. Здесь — кадры основы и расчёт трека: они читают ``iter_base_frames``, который подменяют тесты, — а также задачи в фоне. Вынесено: вход и сглаживание
``app/tracking_normalize.py``, поиск объекта ``app/tracking_detect.py``, ключи слоя и проверка ``app/tracking_keys.py``. Те модули этот не импортируют, а он их — поэтому все имена по-прежнему доступны отсюда.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterator

import numpy as np

from app.tracking_normalize import (  # noqa: F401
    KINDS, MODES, KIND_ALIASES, OBJECT_ALIASES, FPS_RANGE, TRACKED_PROPS, PRESETS, TrackError, _num, object_class,
    normalize_target, normalize_offset, normalize_follow, DEFAULT_SMOOTH, normalize_smooth, normalize_tracking,
    smooth_series, simplify,
)
from app.tracking_detect import (  # noqa: F401
    MAX_GAP_SEC, _integral, _box_sums, ncc_map, _peak, TemplateTracker, _sharpness, _drop_blurred, face_detector,
    object_detector, _center, pick_seed, associate, _box_xywh,
)
from app.tracking_keys import (  # noqa: F401
    TOLERANCE, MAX_KEYS, COVER_SHARE, NOISE_BY_STATUS, screen_to_base, _inside, follows_camera, _ref_size,
    track_values, keys_for, apply_track, untrack_layer, preset_offset, cover_warnings, check_times,
)

log = logging.getLogger(__name__)


DEFAULT_FPS = {"face": 8.0, "object": 8.0, "point": 10.0}


MAX_RANGE_SEC = 120.0


FAIL_FOUND_SHARE = 0.4
DETECT_MAX_SIDE = 960  # кадр детекции: 540×960 у 9:16
POINT_BOX = 0.12  # сторона шаблона точки по умолчанию — доля ширины кадра


CACHE_VERSION = 2


FOUND_STATUSES = ("d", "w")


# ---------------------------------------------------------------- кадры основы клипа


def _frame_setup(store: Any, plan: dict) -> dict:
    """Пресет и размеры кадра клипа — как у ``view_frame`` (motion_render.frame_preset)."""
    from app.clip_settings import normalize_render_settings
    from app.motion_render import frame_preset

    rs = normalize_render_settings(plan.get("render_settings") or {})
    preset, out_format, layers_format = frame_preset(store, plan, rs)
    if out_format != layers_format:
        raise TrackError(
            f"слои клипа размечены в {layers_format}, а выводится {out_format} — трек лёг бы не в те координаты",
            ["сначала «Разметить слои в этом формате» (или set_format с перекомпоновкой слоёв), потом трек"])
    width = int(preset.get("output_width") or 1080)
    height = int(preset.get("output_height") or 1920)
    scale = min(1.0, DETECT_MAX_SIDE / max(width, height))
    dw = max(2, int(round(width * scale / 2)) * 2)
    dh = max(2, int(round(height * scale / 2)) * 2)
    return {"rs": rs, "preset": preset, "format": out_format, "width": width, "height": height, "dw": dw, "dh": dh}


def piece_spans(plan: dict, rs: dict) -> list[tuple[int, float, float]]:
    """(кусок, начало, конец) на шкале clip: зона наплыва — следующему куску (как ClipClock)."""
    from app.motion import clip_timeline

    segments = plan.get("segments") or []
    spans = [(float(s["start_sec"]), float(s["end_sec"]), float(s.get("speed") or 1)) for s in segments]
    starts, total = clip_timeline(spans, rs.get("transition"))
    out = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else total
        out.append((i, float(start), float(end)))
    return out


def _piece_graph(store: Any, plan: dict, setup: dict, segment: dict, fps: float) -> tuple[str, Path]:
    from app import render as _render

    source = store.get_source(int(segment.get("source_id") or plan["source_id"]))
    path = Path(source.get("local_path") or "")
    if not path.is_file():
        raise TrackError("нет файла исходника — кадры для трекинга брать неоткуда",
                         ["трекинг работает по исходнику (как view_frame); файл записи удалён?"])
    preset, keyed = _render._keyed_render(setup["preset"], {"key": source.get("chroma_key")})
    keyed = keyed or {}
    piece = _render._preset_for_segment(preset, segment)
    reframe = _render._segment_reframe_x(segment, piece, source)
    graph = _render._render_filter(setup["width"], setup["height"], fps, piece, None,
                                   _render._base_crop(source, keyed), reframe)
    return graph, path


def base_frames_args(graph: str, path: Path, seek: float, offset: float, count: int, fps: float,
                     dw: int, dh: int) -> list[str]:
    """ffmpeg: ``count`` кадров основы с секунды куска ``offset`` (исходник с ``seek``) → raw BGR.

    Прореживание до ``fps`` — сразу на входе (граф основы не масштабирует лишние 60 кадров в
    секунду), а время сдвигается на ``offset``: выражение рефрейма x(t) считает от начала куска."""
    uses = graph.count("[0:v]")
    labels = [f"tin{k}" for k in range(max(1, uses))]
    for label in labels:
        graph = graph.replace("[0:v]", f"[{label}]", 1)
    head = f"[0:v]fps={fps:g},setpts=PTS+{offset:.6f}/TB"
    head += (f",split={len(labels)}" if len(labels) > 1 else "") + "".join(f"[{label}]" for label in labels)
    # На выходе время — снова с нуля: иначе выход cfr «дорисует» дубли первого кадра от 0 до offset.
    full = f"{head};{graph};[vout]setpts=PTS-STARTPTS,scale={dw}:{dh}:flags=area,format=bgr24[trk]"
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-ss", f"{max(0.0, seek):.3f}", "-t", f"{count / fps + 0.5:.3f}", "-i", str(path),
        "-filter_complex", full, "-map", "[trk]", "-frames:v", str(count), "-f", "rawvideo", "-",
    ]


def iter_base_frames(store: Any, plan: dict, setup: dict, start: float, end: float, fps: float,
                     on_frame: Callable[[float], None] | None = None) -> Iterator[dict]:
    """Кадры основы клипа на шкале clip от ``start`` до ``end`` с шагом 1/``fps``:
    ``{t, piece, img}`` (BGR ``dh×dw``)."""
    segments = plan.get("segments") or []
    dw, dh = setup["dw"], setup["dh"]
    size = dw * dh * 3
    for index, a_clip, b_clip in piece_spans(plan, setup["rs"]):
        lo, hi = max(start, a_clip), min(end, b_clip)
        if hi - lo < 1e-6:
            continue
        segment = segments[index]
        # Кадры на сетке 1/fps от начала куска: fps в графе основы их не двигает.
        offset = math.ceil((lo - a_clip) * fps - 1e-6) / fps
        count = max(1, int(math.floor((hi - a_clip - offset) * fps - 1e-6)) + 1)
        graph, path = _piece_graph(store, plan, setup, segment, fps)
        args = base_frames_args(graph, path, float(segment["start_sec"]) + offset, offset, count, fps, dw, dh)
        got = 0
        with tempfile.TemporaryFile() as errlog:
            proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=errlog)
            try:
                assert proc.stdout is not None
                while got < count:
                    buf = proc.stdout.read(size)
                    if not buf or len(buf) < size:
                        break
                    t = a_clip + offset + got / fps
                    if on_frame:
                        on_frame(t)
                    yield {"t": round(t, 4), "piece": index, "img": np.frombuffer(buf, np.uint8).reshape(dh, dw, 3)}
                    got += 1
            finally:
                if proc.poll() is None and got < count:
                    proc.kill()  # потребитель бросил кадры (ошибка) — ffmpeg дальше не нужен
                if proc.stdout:
                    proc.stdout.close()
                try:
                    proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    proc.kill()
            if got == 0:
                errlog.seek(0)
                err = errlog.read().decode(errors="replace")[-300:]
                raise TrackError(f"ffmpeg не отдал кадры куска {index}: {err}")


def _gray(img: np.ndarray) -> np.ndarray:
    return (img[..., 0] * 0.114 + img[..., 1] * 0.587 + img[..., 2] * 0.299).astype(np.float32)


# ---------------------------------------------------------------- расчёт трека


def _cache_key(store: Any, plan: dict, setup: dict, target: dict, start: float, end: float, fps: float) -> str:
    rs = setup["rs"]
    sources = {}
    for segment in plan.get("segments") or []:
        sid = int(segment.get("source_id") or plan["source_id"])
        if sid not in sources:
            source = store.get_source(sid)
            path = Path(source.get("local_path") or "")
            try:
                stat = path.stat()
                stamp = [stat.st_size, int(stat.st_mtime)]
            except OSError:
                stamp = None
            sources[sid] = {"path": str(path), "stamp": stamp, "crop": source.get("content_crop"),
                            "key": source.get("chroma_key"), "w": source.get("width"), "h": source.get("height"),
                            "focus_preset": source.get("focus_preset")}
    raw = {
        "v": CACHE_VERSION,
        "pieces": [(s.get("source_id"), s["start_sec"], s["end_sec"], s.get("focus") or [], s.get("fit"))
                   for s in plan.get("segments") or []],
        "sources": {str(k): v for k, v in sources.items()},
        "render": {k: rs.get(k) for k in ("preset_id", "layout", "mirror", "format", "transition")},
        "frame": [setup["width"], setup["height"], setup["dw"], setup["dh"]],
        "target": target, "range": [round(start, 3), round(end, 3)], "fps": fps,
    }
    return hashlib.sha256(json.dumps(raw, sort_keys=True, default=str).encode()).hexdigest()[:24]


def _cache_dir() -> Path:
    from app.settings import settings

    path = settings.runtime_dir / "tracking"
    path.mkdir(parents=True, exist_ok=True)
    return path


def compute_track(store: Any, clip_plan_id: int, start: float, end: float, target: Any, fps: float | None = None,
                  progress: Callable[[float, str], None] | None = None,
                  detector: Callable[[np.ndarray], list[dict]] | None = None) -> dict:
    """Трек цели на шкале clip ``start..end``: ``{samples: [{t, piece, x, y, w, h, s}], fps, frame,
    found, lost, regions, notes}`` — рамка цели в долях кадра клипа (основа до камеры), ``s`` —
    d (найдена) / w (слабо: цель частично закрыта) / p (прогноз в пропуске) / l (потеряна, стоит). Кэш — по источнику, кадрированию,
    цели и диапазону. ``detector`` — подмена детектора (тесты)."""
    target = normalize_target(target)
    plan = store.get_clip_plan(clip_plan_id)
    if not plan.get("segments"):
        raise TrackError("в клипе нет кусков")
    setup = _frame_setup(store, plan)
    spans = piece_spans(plan, setup["rs"])
    total = spans[-1][2] if spans else 0.0
    start = max(0.0, float(start))
    end = min(total, float(end))
    if end - start < 0.1:
        raise TrackError(f"диапазон трека {start:.2f}–{end:.2f} с вне клипа (длина {total:.2f} с)")
    if end - start > MAX_RANGE_SEC:
        raise TrackError(f"трек длиннее {MAX_RANGE_SEC:g} с — раздели слой или укажи start/end")
    fps = float(fps or DEFAULT_FPS[target["kind"]])
    fps = min(FPS_RANGE[1], max(FPS_RANGE[0], fps))
    key = _cache_key(store, plan, setup, target, start, end, fps)
    cache = _cache_dir() / f"{clip_plan_id}-{key}.json"
    if detector is None and cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            data["cached"] = True
            return data
        except (OSError, ValueError):
            pass
    if progress:
        progress(0.0, "в очереди тяжёлых задач")
    from app.heavy_queue import heavy_slot

    with heavy_slot("tracking", f"клип {clip_plan_id}"):
        started = time.monotonic()
        data = _compute(store, plan, setup, target, start, end, fps, progress, detector)
        data["seconds"] = round(time.monotonic() - started, 2)
    if detector is None:
        try:
            cache.write_text(json.dumps(data), encoding="utf-8")
        except OSError:
            pass
    data["cached"] = False
    return data


def _compute(store: Any, plan: dict, setup: dict, target: dict, start: float, end: float, fps: float,
             progress: Callable[[float, str], None] | None,
             detector: Callable[[np.ndarray], list[dict]] | None) -> dict:
    rs = setup["rs"]
    width, height, dw, dh = setup["width"], setup["height"], setup["dw"], setup["dh"]
    aspect = width / height
    from app import render as _render

    source = store.get_source(int(plan["source_id"]))
    regions = _render.layout_camera_regions(setup["preset"], source, width, height)
    camera = rs.get("camera") or []
    span = max(1e-6, end - start)
    notes: list[str] = []

    def tick(t: float, stage: str) -> None:
        if progress:
            progress(min(0.99, max(0.0, (t - start) / span)), stage)

    t_seed = min(end, max(start, float(target.get("t", start))))
    if target["kind"] == "point":
        samples = _track_point(store, plan, setup, target, start, end, fps, t_seed, camera, regions, tick)
    else:
        if detector is None:
            detector = face_detector() if target["kind"] == "face" else object_detector(target["class"])
        frames = []
        for frame in iter_base_frames(store, plan, setup, start, end, fps,
                                      on_frame=lambda t: tick(t, "ищу цель по кадрам")):
            frames.append({"t": frame["t"], "piece": frame["piece"], "dets": detector(frame["img"])})
        if not frames:
            raise TrackError("кадров для трекинга нет")
        seed_target = dict(target)
        if seed_target.get("mode") == "near":
            seed_target["x"], seed_target["y"] = screen_to_base(target["x"], target["y"], t_seed, camera, regions,
                                                                width, height)
        order = sorted(range(len(frames)), key=lambda i: abs(frames[i]["t"] - t_seed))
        seed_index, seed_box = None, None
        for i in order:
            box = pick_seed(frames[i]["dets"], seed_target, aspect)
            if box is not None:
                seed_index, seed_box = i, box
                break
        what = "лиц" if target["kind"] == "face" else f"объектов «{target['class']}»"
        if seed_index is None:
            raise TrackError(f"в кадрах {start:.1f}–{end:.1f} с {what} не найдено",
                             ["аниме-модель VTuber детектор лиц не видит — отследи точку: target {kind: point, x, y, t}",
                              "проверь view_frame: цель в кадре?"])
        if abs(frames[seed_index]["t"] - t_seed) > 1.0:
            notes.append(f"в {t_seed:.2f} с цели нет — трек начат с {frames[seed_index]['t']:.2f} с")
        samples = associate(frames, seed_index, seed_box, aspect)
    found = sum(1 for s in samples if s["s"] in FOUND_STATUSES) / max(1, len(samples))
    lost = _intervals([s for s in samples], "l", 1.0 / fps)
    if found < FAIL_FOUND_SHARE:
        where = ", ".join(f"{a:.1f}–{b:.1f} с" for a, b in lost[:4]) or "почти везде"
        raise TrackError(f"цель видна только в {found:.0%} кадров — потеряна: {where}",
                         ["сузь диапазон слоя до места, где цель видна", "укажи цель точнее (mode near / point)",
                          "закрытое лицо, разворот, уход из кадра — трек тут не поможет"])
    if lost:
        notes.append("цель потеряна на " + ", ".join(f"{a:.1f}–{b:.1f} с" for a, b in lost[:4])
                     + " — там слой стоит на последнем месте")
    return {"samples": [{k: (round(v, 5) if isinstance(v, float) else v) for k, v in s.items()} for s in samples],
            "fps": fps, "frame": {"w": width, "h": height, "dw": dw, "dh": dh}, "found": round(found, 3),
            "lost": [[round(a, 2), round(b, 2)] for a, b in lost], "regions": regions, "target": target,
            "range": [round(start, 3), round(end, 3)], "notes": notes}


def _track_point(store: Any, plan: dict, setup: dict, target: dict, start: float, end: float, fps: float,
                 t_seed: float, camera: list[dict], regions: dict | None,
                 tick: Callable[[float, str], None]) -> list[dict]:
    """Ручная точка: шаблон с кадра ``t_seed`` вперёд до ``end`` (кадры идут потоком), затем
    назад до ``start`` (в памяти только эта часть)."""
    width, height, dw, dh = setup["width"], setup["height"], setup["dw"], setup["dh"]
    bx, by = screen_to_base(target["x"], target["y"], t_seed, camera, regions, width, height)
    tw = int(round(float(target.get("w") or POINT_BOX) * dw))
    th = int(round(float(target["h"]) * dh)) if target.get("h") else tw
    box_w, box_h = tw / dw, th / dh

    seed: dict = {}

    def run(frames: Iterator[dict], tracker: TemplateTracker | None, last: dict | None) -> list[dict]:
        out: list[dict] = []
        missed = 0.0
        for frame in frames:
            gray = _gray(frame["img"])
            if tracker is None:  # кадр-семя: шаблон берётся с него
                tracker = TemplateTracker(gray, bx * dw, by * dh, tw, th)
                seed.update(gray=gray, t=frame["t"], piece=frame["piece"])
                out.append(_point_sample(frame, tracker, box_w, box_h, "d", dw, dh))
                last = frame
                continue
            dt = abs(frame["t"] - last["t"]) if last else 0.0
            boundary = bool(last) and frame["piece"] != last["piece"]
            if boundary:
                tracker.vx = tracker.vy = 0.0
            got = tracker.step(gray, dt, wide=boundary or missed > MAX_GAP_SEC)
            if got is None:
                missed += dt
                status = "p" if missed <= MAX_GAP_SEC else "l"
                if status == "l":
                    tracker.vx = tracker.vy = 0.0
            else:
                missed = 0.0
                # Слабое совпадение (цель частично закрыта) — в треке, но сглаживание ему верит меньше.
                status = "d" if got[2] >= tracker.STRONG else "w"
            out.append(_point_sample(frame, tracker, box_w, box_h, status, dw, dh))
            last = frame
        return out

    # Вперёд от кадра-семени — потоком; назад — только кусок до него (он в памяти).
    forward = run(iter_base_frames(store, plan, setup, t_seed, end, fps,
                                   on_frame=lambda t: tick(t, "веду точку по кадрам")), None, None)
    if not forward:
        raise TrackError("кадров для трекинга нет")
    first_t = forward[0]["t"]
    if first_t - start < 0.5 / fps:
        return forward
    before = [f for f in iter_base_frames(store, plan, setup, start, first_t - 0.5 / fps, fps)]
    if not before:
        return forward
    back_tracker = TemplateTracker(seed["gray"], bx * dw, by * dh, tw, th)
    back = run(iter(reversed(before)), back_tracker, {"t": seed["t"], "piece": seed["piece"]})
    return list(reversed(back)) + forward


def _point_sample(frame: dict, tracker: TemplateTracker, box_w: float, box_h: float, status: str,
                  dw: int, dh: int) -> dict:
    cx, cy = tracker.cx / dw, tracker.cy / dh
    return {"t": frame["t"], "piece": frame["piece"], "x": cx - box_w / 2, "y": cy - box_h / 2,
            "w": box_w, "h": box_h, "s": status}


def _intervals(samples: list[dict], status: str, step: float) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for s in samples:
        if s["s"] != status:
            continue
        if out and s["t"] - out[-1][1] <= step * 1.5:
            out[-1] = (out[-1][0], s["t"])
        else:
            out.append((s["t"], s["t"]))
    return out


def track_layer(store: Any, clip_plan_id: int, layer: dict, target: Any, offset: Any = None, follow: Any = None,
                smooth: Any = None, fps: float | None = None, preset: str | None = None,
                progress: Callable[[float, str], None] | None = None,
                detector: Callable[[np.ndarray], list[dict]] | None = None) -> tuple[dict, dict]:
    """Посчитать трек под окно слоя и вернуть ``(новый слой, отчёт)``. Ничего не сохраняет."""
    from app.clip_settings import normalize_render_settings

    plan = store.get_clip_plan(clip_plan_id)
    rs = normalize_render_settings(plan.get("render_settings") or {})
    fmt = rs.get("layers_format")
    if layer.get("type") in ("audio", "fx", "captions"):
        raise TrackError(f"слой «{layer.get('type')}» не двигается по кадру — трекать нечего")
    target_n = normalize_target(target)
    if "t" not in target_n:
        target_n["t"] = float(layer["start"])
    p_offset, p_follow, patch = preset_offset(preset, layer, fmt)
    base_layer = {**layer, **patch}
    offset_n = normalize_offset({**p_offset, **(offset if isinstance(offset, dict) else {})})
    follow_n = normalize_follow({**p_follow, **(follow if isinstance(follow, dict) else {})})
    if target_n["kind"] == "point" and follow_n["scale"]:
        follow_n["scale"] = False  # у ручной точки размер не меряется
    track = compute_track(store, clip_plan_id, float(layer["start"]), float(layer["end"]), target_n, fps,
                          progress, detector)
    fresh = apply_track(base_layer, track, offset_n, follow_n, smooth, fmt, preset)
    notes = list(track.get("notes") or [])
    if rs.get("camera"):
        notes.append("наезды камеры: слой " + ("едет с камерой (followCamera)" if fresh.get("followCamera")
                                              else "в окошке раскладки — стоит, как окошко"))
        if any(k.get(f) for k in rs["camera"] for f in ("z", "rotX", "rotY")):
            notes.append("3D-камера: параллакс основы трек не повторяет — проверь кадры")
    report = {
        "target": track.get("target"), "range": track.get("range"), "fps": track.get("fps"),
        "found": track.get("found"), "lost": track.get("lost"), "samples": len(track.get("samples") or []),
        "keys": {p: len((fresh.get("keys") or {}).get(p) or []) for p in fresh["tracking"]["props"]},
        "follow_camera": fresh.get("followCamera"), "cached": track.get("cached"),
        "seconds": track.get("seconds"), "notes": notes, "warnings": cover_warnings(fresh, track, fmt),
        "check_times": check_times(fresh, track),
    }
    return fresh, report


# ---------------------------------------------------------------- фоновые задачи (прогресс для UI)


_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()
_MAX_JOBS = 40


def start_job(run: Callable[[Callable[[float, str], None]], dict]) -> str:
    """Запустить ``run(progress)`` в потоке; ``job_status(id)`` — ход и результат."""
    job_id = uuid.uuid4().hex[:12]
    job = {"id": job_id, "state": "queued", "progress": 0.0, "stage": "в очереди", "result": None, "error": None,
           "hints": [], "started": time.time()}
    with _JOBS_LOCK:
        _JOBS[job_id] = job
        for old in sorted(_JOBS, key=lambda k: _JOBS[k]["started"])[:-_MAX_JOBS]:
            _JOBS.pop(old, None)

    def progress(value: float, stage: str) -> None:
        job.update(state="running", progress=round(float(value), 3), stage=stage)

    def worker() -> None:
        try:
            job["result"] = run(progress)
            job.update(state="done", progress=1.0, stage="готово")
        except TrackError as exc:
            job.update(state="error", error=str(exc), hints=exc.hints)
        except Exception as exc:  # noqa: BLE001 - любая ошибка — в ответ задачи, а не в лог потока
            log.exception("трекинг упал")
            job.update(state="error", error=f"трекинг не удался: {exc}")

    threading.Thread(target=worker, name=f"track-{job_id}", daemon=True).start()
    return job_id


def job_status(job_id: str) -> dict | None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job else None
