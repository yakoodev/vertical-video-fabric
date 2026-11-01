"""Где на кадре стрима аватар стримера: VTuber/PNGtuber-модель или вебка — глазами Gemini.

QA 01.11 (клип 889, sareamura): заказ «в кадре только аватар Муры», а рамка героя
(``subject_box``) поймала человека из ютуб-клипа на экране стрима — агент поставил окно 9:16 на
него, аватара в ролике не было ни секунды. Детектор лиц (``detect-facecam``) нарисованную модель не
видит, а Gemini видит: показываем ему ЦЕЛЫЕ кадры исходника (не кроп 9:16) с кусков клипа и
просим рамку аватара, отличая его от людей внутри видео, игры и браузера на экране.

Ответ — прямоугольник ``cam`` в долях ВСЕГО кадра исходника (как ``facecam`` и ``layout.cam``),
с запасом по краям: раскладка ``avatar_full`` (app/stream_layout.py) ставит его на весь кадр.
Аватар может переезжать между сценами (Мура: крупно в центре на загрузке, потом в углу) — берём
место, где он чаще всего в кадрах клипа, и говорим об этом.

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

PROMPT = (
    "Это {n} кадров стрима (целиком, по порядку). Найди на каждом АВАТАР СТРИМЕРА: нарисованную "
    "VTuber/PNGtuber-модель или вебку с лицом стримера — она поверх игры/браузера, обычно сбоку или в углу, "
    "иногда крупно. Люди и персонажи ВНУТРИ видео, ютуба, игры, фильма, браузера на экране — НЕ аватар, "
    "даже если они крупнее. Рамка — вся видимая модель (голова и тело). Ответь ТОЛЬКО JSON: "
    "{{\"frames\": [{{\"x0\":…,\"y0\":…,\"x1\":…,\"y1\":…}}, …]}} — по рамке на КАЖДЫЙ кадр в том же порядке, "
    "доли кадра 0..1; кадр без аватара — {{}}."
)


def _cache_key(plan: dict) -> str:
    key = [(round(float(s["start_sec"]), 2), round(float(s["end_sec"]), 2)) for s in plan.get("segments") or []]
    return hashlib.sha256(json.dumps(key).encode()).hexdigest()[:16]


def _cache_path(clip_plan_id: int, plan: dict) -> Path:
    folder = settings.runtime_dir / "avatar-box"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{int(clip_plan_id)}-{_cache_key(plan)}-v1.json"


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
    boxes = [_box(item) for item in frames[:n]]
    return boxes + [None] * (n - len(boxes))


def _iou(a: dict, b: dict) -> float:
    ix = max(0.0, min(a["x1"], b["x1"]) - max(a["x0"], b["x0"]))
    iy = max(0.0, min(a["y1"], b["y1"]) - max(a["y0"], b["y0"]))
    inter = ix * iy
    union = (a["x1"] - a["x0"]) * (a["y1"] - a["y0"]) + (b["x1"] - b["x0"]) * (b["y1"] - b["y0"]) - inter
    return inter / union if union > 0 else 0.0


def place_avatar(boxes: list[dict | None]) -> dict:
    """Рамки по кадрам → ``{cam, box, frames_with_avatar, moves, places}``: место, где аватар чаще всего
    (объединение рамок этого места + запас PAD), в долях кадра исходника."""
    found = [b for b in boxes if b]
    if not found:
        return {"cam": None, "box": None, "frames_with_avatar": 0, "moves": False, "places": 0}
    groups: list[list[dict]] = []
    for box in found:
        for group in groups:
            if _iou(group[0], box) >= SAME_PLACE_IOU:
                group.append(box)
                break
        else:
            groups.append([box])
    best = max(groups, key=len)
    union = {"x0": min(b["x0"] for b in best), "y0": min(b["y0"] for b in best),
             "x1": max(b["x1"] for b in best), "y1": max(b["y1"] for b in best)}
    w, h = union["x1"] - union["x0"], union["y1"] - union["y0"]
    x0, y0 = max(0.0, union["x0"] - w * PAD), max(0.0, union["y0"] - h * PAD)
    x1, y1 = min(1.0, union["x1"] + w * PAD), min(1.0, union["y1"] + h * PAD)
    cam = {"x": round(x0, 4), "y": round(y0, 4), "w": round(x1 - x0, 4), "h": round(y1 - y0, 4)}
    return {"cam": cam, "box": {k: round(v, 4) for k, v in union.items()}, "frames_with_avatar": len(found),
            "moves": len(groups) > 1, "places": len(groups)}


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
    out = {**place_avatar(boxes), "frames": len(parts), "times": used,
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
