"""Где на кадре стрима аватар стримера: VTuber/PNGtuber-модель или вебка — глазами Gemini.

QA 01.11 (клип 889, sareamura): заказ «в кадре только аватар Муры», а рамка героя
(``subject_box``) поймала человека из ютуб-клипа на экране стрима — агент поставил окно 9:16 на
него, аватара в ролике не было ни секунды. Детектор лиц (``detect-facecam``) нарисованную модель не
видит, а Gemini видит: показываем ему ЦЕЛЫЕ кадры исходника (не кроп 9:16) с кусков клипа и
просим рамку аватара, отличая его от людей внутри видео, игры и браузера на экране.

Ответ — прямоугольник ``cam`` в долях ВСЕГО кадра исходника (как ``facecam`` и ``layout.cam``),
с запасом по краям: раскладка ``avatar_full`` (app/stream_layout.py) ставит его на весь кадр.
Аватар может переезжать между сценами (QA 02.11, клип 892: Мура крупно левее до 281 с, потом в углу
справа) — ``cam`` — место, где он чаще всего, а ``places`` — ВСЕ места с отрезками исходника (граница —
склейка плана между кадрами): set_layout avatar_full кладёт их в ``layout.cams``, рамка меняется по ходу.

Кэш по составу кусков клипа: модель зовём один раз.
"""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from app.settings import settings

MAX_FRAMES = 6
PAD = 0.06  # запас вокруг рамки модели (волосы, уши, руки) — доля её размера с каждой стороны
SAME_PLACE_IOU = 0.35  # рамки разных кадров с таким перекрытием — одно место аватара
SAMPLE_W = 640
MAX_CUT_WINDOW_SEC = 120.0  # склейку между кадрами двух мест ищем ffmpeg-ом в окне не длиннее

PROMPT = (
    "Это {n} кадров стрима (целиком, по порядку). Найди на каждом АВАТАР СТРИМЕРА: нарисованную "
    "VTuber/PNGtuber-модель или вебку с лицом стримера — она поверх игры/браузера, обычно сбоку или в углу, "
    "иногда крупно. Люди и персонажи ВНУТРИ видео, ютуба, игры, фильма, браузера на экране — НЕ аватар, "
    "даже если они крупнее. Рамка — вся видимая модель (голова и тело). Ещё отметь окна ЧАТА, текста и "
    "интерфейса стрима (оверлеи, донаты, подписи, рамки) РЯДОМ с аватаром — в ui. Ответь ТОЛЬКО JSON: "
    "{{\"frames\": [{{\"x0\":…,\"y0\":…,\"x1\":…,\"y1\":…, \"ui\": [{{\"x0\":…,\"y0\":…,\"x1\":…,\"y1\":…}}]}}, …]}} — "
    "по рамке на КАЖДЫЙ кадр в том же порядке, доли кадра 0..1; кадр без аватара — {{}}."
)


def _cache_key(plan: dict) -> str:
    key = [(round(float(s["start_sec"]), 2), round(float(s["end_sec"]), 2)) for s in plan.get("segments") or []]
    return hashlib.sha256(json.dumps(key).encode()).hexdigest()[:16]


def _cache_path(clip_plan_id: int, plan: dict) -> Path:
    folder = settings.runtime_dir / "avatar-box"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{int(clip_plan_id)}-{_cache_key(plan)}-v2.json"


def cached_avatar(store: Any, clip_plan_id: int) -> dict | None:
    """Уже найденный аватар клипа (без вызова модели) или None."""
    try:
        plan = store.get_clip_plan(int(clip_plan_id))
        data = json.loads(_cache_path(clip_plan_id, plan).read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError):
        return None
    return data if isinstance(data, dict) else None


def source_times(plan: dict, n: int = MAX_FRAMES) -> list[float]:
    """Секунды исходника для кадров: равномерно по кускам клипа (без самых краёв)."""
    spans = [(float(s["start_sec"]), float(s["end_sec"])) for s in plan.get("segments") or []
             if float(s["end_sec"]) > float(s["start_sec"])]
    total = sum(b - a for a, b in spans)
    if total <= 0:
        return []
    out = []
    for i in range(n):
        at = total * (i + 0.5) / n
        for a, b in spans:
            if at <= b - a:
                out.append(round(a + at, 3))
                break
            at -= b - a
    return out


def _grab(path: str, t: float, out: Path) -> bytes | None:
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, t):.3f}", "-i", path,
                    "-frames:v", "1", "-vf", f"scale={SAMPLE_W}:-2", "-q:v", "4", str(out)],
                   capture_output=True, timeout=60)
    return out.read_bytes() if out.exists() and out.stat().st_size else None


def _box(data: Any) -> dict | None:
    try:
        box = {k: min(1.0, max(0.0, float(data[k]))) for k in ("x0", "y0", "x1", "y1")}
    except (KeyError, TypeError, ValueError):
        return None
    if box["x1"] - box["x0"] < 0.03 or box["y1"] - box["y0"] < 0.05:
        return None
    return box


def parse_boxes(text: str, n: int) -> list[dict | None]:
    try:
        start, end = text.index("{"), text.rindex("}") + 1
        data = json.loads(text[start:end])
    except ValueError:
        return [None] * n
    frames = data.get("frames") if isinstance(data, dict) else None
    if not isinstance(frames, list):
        return [_box(data)] * n
    boxes = []
    for item in frames[:n]:
        box = _box(item)
        if box is not None and isinstance(item, dict) and isinstance(item.get("ui"), list):
            ui = [u for u in (_ui_box(x) for x in item["ui"][:8]) if u]
            if ui:
                box["ui"] = ui
        boxes.append(box)
    return boxes + [None] * (n - len(boxes))


def _ui_box(data: Any) -> dict | None:
    """Окно чата/текста/интерфейса рядом с аватаром — строка чата бывает тонкой, порог меньше, чем у модели."""
    try:
        box = {k: min(1.0, max(0.0, float(data[k]))) for k in ("x0", "y0", "x1", "y1")}
    except (KeyError, TypeError, ValueError):
        return None
    return box if box["x1"] - box["x0"] >= 0.01 and box["y1"] - box["y0"] >= 0.01 else None


EDGE_EPS = 0.01


def trim_ui(cam: tuple[float, float, float, float], union: dict, ui: list[dict]) -> tuple[tuple, list[str]]:
    """Рамка с запасом ``cam`` (x0, y0, x1, y1) без окон чата/интерфейса ``ui`` (QA 02.11, клип 892: запас PAD
    захватил обрывки чата «SaZiann…» в углу кадра). Окно по одну сторону от самого аватара (``union``) —
    край рамки отодвигается до окна, но не внутрь аватара. → (рамка, срезанные стороны)."""
    x0, y0, x1, y1 = cam
    sides: list[str] = []
    for u in ui:
        if not (u["x0"] < x1 and x0 < u["x1"] and u["y0"] < y1 and y0 < u["y1"]):
            continue
        # Сторона — где центр окна вне аватара; рамка режется до окна, но не глубже края модели.
        cx, cy = (u["x0"] + u["x1"]) / 2, (u["y0"] + u["y1"]) / 2
        if cy < union["y0"] - EDGE_EPS:
            y0, side = max(y0, min(u["y1"], union["y0"])), "top"
        elif cy > union["y1"] + EDGE_EPS:
            y1, side = min(y1, max(u["y0"], union["y1"])), "bottom"
        elif cx < union["x0"] - EDGE_EPS:
            x0, side = max(x0, min(u["x1"], union["x0"])), "left"
        elif cx > union["x1"] + EDGE_EPS:
            x1, side = min(x1, max(u["x0"], union["x1"])), "right"
        else:
            continue  # окно на самом аватаре — рамку внутрь модели не режем
        if side not in sides:
            sides.append(side)
    return (x0, y0, x1, y1), sides


def _iou(a: dict, b: dict) -> float:
    ix = max(0.0, min(a["x1"], b["x1"]) - max(a["x0"], b["x0"]))
    iy = max(0.0, min(a["y1"], b["y1"]) - max(a["y0"], b["y0"]))
    inter = ix * iy
    union = (a["x1"] - a["x0"]) * (a["y1"] - a["y0"]) + (b["x1"] - b["x0"]) * (b["y1"] - b["y0"]) - inter
    return inter / union if union > 0 else 0.0


def _cam_of(group: list[dict]) -> tuple[dict, dict]:
    """Рамки одного места → (объединение, cam с запасом PAD) в долях кадра исходника."""
    union = {"x0": min(b["x0"] for b in group), "y0": min(b["y0"] for b in group),
             "x1": max(b["x1"] for b in group), "y1": max(b["y1"] for b in group)}
    w, h = union["x1"] - union["x0"], union["y1"] - union["y0"]
    x0, y0 = max(0.0, union["x0"] - w * PAD), max(0.0, union["y0"] - h * PAD)
    x1, y1 = min(1.0, union["x1"] + w * PAD), min(1.0, union["y1"] + h * PAD)
    ui = [u for b in group for u in b.get("ui") or []]
    if ui:
        (x0, y0, x1, y1), _sides = trim_ui((x0, y0, x1, y1), union, ui)
    cam = {"x": round(x0, 4), "y": round(y0, 4), "w": round(x1 - x0, 4), "h": round(y1 - y0, 4)}
    return {k: round(v, 4) for k, v in union.items()}, cam


def place_avatar(boxes: list[dict | None], times: list[float] | None = None) -> dict:
    """Рамки по кадрам → ``{cam, box, frames_with_avatar, moves, place_count, places}``.

    ``cam`` — место, где аватар чаще всего (объединение рамок этого места + запас PAD), в долях
    кадра исходника. ``places`` — ВСЕ места по времени: ``[{place, cam, box, frames, src_from,
    src_to}]`` подряд идущими отрезками (место 0 — самое частое); с ``times`` (секунды исходника
    кадров) граница двух отрезков — посередине между их соседними кадрами (detect_avatar уточняет
    её склейкой плана). QA 02.11 (клип 892): вернули одну рамку, и 4 с клипа были без лица."""
    found = [(i, b) for i, b in enumerate(boxes) if b]
    if not found:
        return {"cam": None, "box": None, "frames_with_avatar": 0, "moves": False, "place_count": 0, "places": []}
    groups: list[list[tuple[int, dict]]] = []
    for item in found:
        for group in groups:
            if _iou(group[0][1], item[1]) >= SAME_PLACE_IOU:
                group.append(item)
                break
        else:
            groups.append([item])
    groups.sort(key=lambda g: (-len(g), g[0][0]))  # место 0 — самое частое, при равенстве — раньше
    looks = [_cam_of([b for _i, b in group]) for group in groups]
    place_of = {i: g for g, group in enumerate(groups) for i, _b in group}
    runs: list[dict] = []
    for i, _box in found:
        g = place_of[i]
        if runs and runs[-1]["place"] == g:
            runs[-1]["frames"] += 1
            runs[-1]["last"] = i
            continue
        runs.append({"place": g, "first": i, "last": i, "frames": 1})
    timed = times is not None and len(times) >= len(boxes)
    places = []
    for k, run in enumerate(runs):
        box, cam = looks[run["place"]]
        place = {"place": run["place"], "cam": cam, "box": box, "frames": run["frames"]}
        if timed:
            place["src_from"] = round(float(times[run["first"]]) if k == 0 else
                                      (float(times[runs[k - 1]["last"]]) + float(times[run["first"]])) / 2, 3)
            place["src_to"] = round(float(times[run["last"]]) if k == len(runs) - 1 else
                                    (float(times[run["last"]]) + float(times[runs[k + 1]["first"]])) / 2, 3)
            place["frame_times"] = [round(float(times[i]), 3) for i, _b in found if run["first"] <= i <= run["last"]]
        places.append(place)
    box, cam = looks[0]
    return {"cam": cam, "box": box, "frames_with_avatar": len(found), "moves": len(groups) > 1,
            "place_count": len(groups), "places": places}


def refine_boundaries(places: list[dict], cuts: list[float]) -> list[dict]:
    """Граница двух отрезков мест — на склейку плана (секунды исходника) между их соседними кадрами,
    ближайшую к середине; склейки нет — середина как была."""
    out = [dict(p) for p in places]
    for left, right in zip(out, out[1:]):
        lo = (left.get("frame_times") or [left.get("src_to")])[-1]
        hi = (right.get("frame_times") or [right.get("src_from")])[0]
        if lo is None or hi is None:
            continue
        inside = [float(c) for c in cuts if float(lo) < float(c) < float(hi)]
        if inside:
            mid = (float(lo) + float(hi)) / 2
            at = round(min(inside, key=lambda c: abs(c - mid)), 3)
            left["src_to"], right["src_from"] = at, at
            right["from_shot_cut"] = True
    return out


def settle_transitions(places: list[dict], finder: Any) -> list[dict]:
    """Граница мест по самой смене сцены: ``finder(lo, hi, hint)`` → ``{start, end, mid, hard}``
    (``scene_cuts.transition_span``) между соседними кадрами мест; ``hint`` — склейка из
    ``refine_boundaries``.

    QA 02.11 (клип 895): в стриме сцена сменилась наплывом ≈0.4 с, граница встала на его конец
    (склейку ffmpeg) — рамка старого места 0.35 с показывала ютуб. Жёсткая склейка — граница на первом
    кадре новой сцены; наплыв — на его середине: каждая рамка видит чужую сцену не больше чем
    наполовину прозрачной и только половину наплыва (кросс-фейд рамок дал бы не меньше чужого)."""
    out = [dict(p) for p in places]
    for left, right in zip(out, out[1:]):
        lo = (left.get("frame_times") or [left.get("src_to")])[-1]
        hi = (right.get("frame_times") or [right.get("src_from")])[0]
        if lo is None or hi is None or float(hi) <= float(lo):
            continue
        hint = float(right["src_from"]) if right.get("from_shot_cut") and right.get("src_from") is not None else None
        try:
            span = finder(float(lo), float(hi), hint)
        except Exception:  # noqa: BLE001 - не нашли — граница остаётся, где была
            span = None
        if not span or not (float(lo) < float(span["start"]) <= float(span["end"]) < float(hi)):
            continue
        at = round(float(span["start"] if span.get("hard") else span["mid"]), 3)
        left["src_to"], right["src_from"] = at, at
        right["from_shot_cut"] = True
        if not span.get("hard"):
            right["transition"] = {"start": float(span["start"]), "end": float(span["end"])}
    return out


def _boundary_cuts(store: Any, source: dict, local: str, places: list[dict]) -> list[float]:
    """Склейки планов исходника между кадрами соседних мест: кэш источника (get_shots), иначе
    ffmpeg только по этим окнам (секунды, не весь стрим)."""
    windows = []
    for left, right in zip(places, places[1:]):
        lo = (left.get("frame_times") or [left.get("src_to")])[-1]
        hi = (right.get("frame_times") or [right.get("src_from")])[0]
        if lo is not None and hi is not None and float(hi) > float(lo):
            windows.append((float(lo), float(hi)))
    if not windows:
        return []
    cached = source.get("scene_cuts")
    if cached is None:
        try:
            from app.store import _parse_transcript

            row = store.db.query_one("SELECT scene_cuts_json FROM sources WHERE id = ?", (int(source["id"]),))
            cached = _parse_transcript(row["scene_cuts_json"]) if row else None
        except Exception:  # noqa: BLE001 - нет кэша — посчитаем по окнам
            cached = None
    if cached:
        return [float(c) for c in cached]
    from app.scene_cuts import detect_scene_cuts

    out: list[float] = []
    for lo, hi in windows:
        if hi - lo <= MAX_CUT_WINDOW_SEC:
            out += detect_scene_cuts(local, start=lo, duration=hi - lo)
    return out


def detect_avatar(store: Any, clip_plan_id: int, client: Any = None, *, refresh: bool = False) -> dict:
    """Аватар стримера на кусках клипа: ``{cam, box, frames, frames_with_avatar, moves, places, times}``.
    ``cam`` None — аватара не нашлось (или нет Gemini). Кэш по составу кусков."""
    plan = store.get_clip_plan(int(clip_plan_id))
    cache = _cache_path(clip_plan_id, plan)
    if not refresh and cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
    source = store.get_source(int(plan["source_id"]))
    local = str(source.get("local_path") or "")
    if not local or not Path(local).exists():
        raise ValueError("у источника нет файла видео")
    times = source_times(plan)
    if not times:
        raise ValueError("в клипе нет кусков")
    if client is None:
        if not settings.gemini_api_key:
            raise ValueError("нужна Gemini (GEMINI_API_KEY) — без неё аватар не найти; задай cam руками")
        from app.ai.gemini import GeminiClient

        client = GeminiClient()
    parts: list[dict] = []
    used: list[float] = []
    with tempfile.TemporaryDirectory() as folder:
        for i, t in enumerate(times):
            data = _grab(local, t, Path(folder) / f"a{i}.jpg")
            if data:
                used.append(t)
                parts.append({"inlineData": {"mimeType": "image/jpeg", "data": base64.b64encode(data).decode()}})
    if not parts:
        raise ValueError("ffmpeg не отдал ни одного кадра исходника")
    response = client.generate_content(
        settings.gemini_text_model,
        {"contents": [{"role": "user", "parts": [{"text": PROMPT.format(n=len(parts))}, *parts]}],
         "generationConfig": {"responseMimeType": "application/json", "temperature": 0}},
    )
    text = "".join(p.get("text", "") for p in
                   ((response.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
    boxes = parse_boxes(text, len(parts))
    found = place_avatar(boxes, used)
    places = found["places"]
    if len(places) > 1:
        try:
            places = refine_boundaries(places, _boundary_cuts(store, source, local, places))
        except Exception:  # noqa: BLE001 - склейку не нашли — граница посередине между кадрами
            pass
        from app.scene_cuts import transition_span

        # Наплыв ffmpeg-склейка ловит только концом (или не ловит совсем) — граница по самой смене.
        places = settle_transitions(places, lambda lo, hi, hint: transition_span(local, lo, hi, hint=hint))
    spans = [(float(s["start_sec"]), float(s["end_sec"])) for s in plan.get("segments") or []]
    if places and spans:
        # Первое и последнее место — до краёв клипа (кадры берутся не с самых краёв).
        places[0]["src_from"] = round(min(a for a, _b in spans), 3)
        places[-1]["src_to"] = round(max(b for _a, b in spans), 3)
    out = {**found, "places": places, "frames": len(parts), "times": used,
           "per_frame": [b for b in boxes]}
    try:
        cache.write_text(json.dumps(out), encoding="utf-8")
    except OSError:
        pass
    return out


def avatar_center_in_window(cam: dict, x: float, window_w: float) -> bool:
    """Центр аватара (доли исходника) внутри окна шириной ``window_w`` с центром ``x``."""
    cx = float(cam["x"]) + float(cam["w"]) / 2
    return x - window_w / 2 - 1e-6 <= cx <= x + window_w / 2 + 1e-6
