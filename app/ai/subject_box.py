"""Где на кадре персонаж: рамка главного героя клипа глазами Gemini.

Субтитры и титры нельзя класть на персонажа (владелец: «субтитры прям на
персонаже»). Детектор людей (YOLOX) аниме-модель VTuber'а не видит, а Gemini
видит что угодно: показываем ему 3 кадра готового вида (кроп, раскладка) и
просим рамку героя — лица/модели стримера. Дальше ``text_positions`` выбирает
свободную зону над или под ним.

Рамка кэшируется по составу кусков и раскладке: модель зовём один раз на клип.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from app.settings import settings

HOOK_Y = 0.13
SAFE_TOP = 0.2  # ниже хука
SAFE_BOTTOM = 0.8  # выше интерфейса площадок
DEFAULT_CAPTION_Y = 0.72

PROMPT = (
    "На трёх кадрах вертикального видео 9:16 найди главного героя — лицо/модель стримера "
    "(в том числе нарисованную аниме-модель VTuber), а если его нет — главный объект. "
    "Ответь ТОЛЬКО JSON: {\"x0\":…,\"y0\":…,\"x1\":…,\"y1\":…} — объединённая рамка героя на всех "
    "кадрах в долях кадра 0..1 (y0 — верх головы, y1 — низ видимого тела). Если героя нет — {}."
)


def _cache_key(plan: dict) -> str:
    render = plan.get("render_settings") or {}
    raw = json.dumps(
        {"pieces": [(s["start_sec"], s["end_sec"]) for s in plan.get("segments") or []],
         "layout": render.get("layout"), "mirror": render.get("mirror")},
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def subject_box(store: Any, clip_plan_id: int, client: Any = None) -> dict | None:
    """{x0,y0,x1,y1} героя в долях кадра 9:16 или None (нет Gemini, не нашёл)."""
    plan = store.get_clip_plan(clip_plan_id)
    cache_dir = settings.runtime_dir / "subject-box"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / f"{clip_plan_id}-{_cache_key(plan)}.json"
    if cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            return data or None
        except (OSError, ValueError):
            pass
    if client is None:
        if not settings.gemini_api_key:
            return None
        from app.ai.gemini import GeminiClient

        client = GeminiClient()
    from app.motion_render import render_frame_png

    duration = sum(float(s["end_sec"]) - float(s["start_sec"]) for s in plan.get("segments") or [])
    if duration <= 0:
        return None
    parts: list[dict] = [{"text": PROMPT}]
    for f in (0.2, 0.5, 0.8):
        png = render_frame_png(store, clip_plan_id, duration * f, layers=[], camera=[])
        parts.append({"inlineData": {"mimeType": "image/png", "data": base64.b64encode(_shrink(png)).decode()}})
    response = client.generate_content(
        settings.gemini_text_model,
        {"contents": [{"role": "user", "parts": parts}],
         "generationConfig": {"responseMimeType": "application/json", "temperature": 0}},
    )
    text = "".join(
        p.get("text", "") for p in ((response.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
    )
    box = _parse_box(text)
    try:
        cache.write_text(json.dumps(box or {}), encoding="utf-8")
    except OSError:
        pass
    return box


def _shrink(png: bytes) -> bytes:
    """1080×1920 PNG → JPEG 360×640: для рамки героя хватает, а токенов меньше."""
    try:
        import cv2
        import numpy as np

        img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
        img = cv2.resize(img, (360, 640), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".png", img)
        return buf.tobytes() if ok else png
    except Exception:  # noqa: BLE001 - без cv2 отдаём как есть
        return png


def _parse_box(text: str) -> dict | None:
    try:
        start, end = text.index("{"), text.rindex("}") + 1
        data = json.loads(text[start:end])
        box = {k: min(1.0, max(0.0, float(data[k]))) for k in ("x0", "y0", "x1", "y1")}
    except (ValueError, KeyError, TypeError):
        return None
    if box["y1"] - box["y0"] < 0.05 or box["x1"] - box["x0"] < 0.03:
        return None
    return box


def text_positions(box: dict | None, fallback_caption_y: float = DEFAULT_CAPTION_Y) -> dict:
    """Куда ставить субтитры и слово-панчлайн, чтобы не лечь на героя.

    Свободные зоны — над героем (от хука до макушки) и под ним (от низа тела до
    интерфейса площадок); берём ту, где больше места. Нет рамки — привычные места.
    """
    if not box:
        return {"captions_y": fallback_caption_y, "punchline_y": 0.3, "subject": None}
    above = (SAFE_TOP, box["y0"])
    below = (box["y1"], SAFE_BOTTOM)
    size_above = max(0.0, above[1] - above[0])
    size_below = max(0.0, below[1] - below[0])
    if size_below >= 0.1 and size_below >= size_above * 0.8:
        captions_y = round(min(0.78, below[0] + min(0.06, size_below / 2)), 3)
    elif size_above >= 0.08:
        captions_y = round(max(0.24, above[1] - min(0.06, size_above / 2)), 3)
    else:
        # Герой во весь кадр: лучше по ногам, чем по лицу.
        captions_y = 0.78
    # Панчлайн — над субтитрами, если те тоже над героем (иначе наложатся), или в
    # середине зоны над героем.
    if captions_y < box["y0"]:
        punch_y = round(max(0.2, captions_y - 0.12), 3)
    elif size_above >= 0.08:
        punch_y = round(max(0.24, min(0.4, (above[0] + above[1]) / 2)), 3)
    else:
        punch_y = 0.3
    return {"captions_y": captions_y, "punchline_y": punch_y, "subject": box}


def zoom_anchor(box: dict | None, captions_y: float) -> dict | None:
    """Точка кадра, которая при наезде стоит на месте.

    Камера по умолчанию зумит в центр кадра — герой снизу растёт вверх и
    заезжает под субтитры над ним (владелец: «субтитры опять на персонаже»).
    Держим на месте край героя со стороны текста: макушку, если субтитры над
    ним, или низ тела, если под ним; по горизонтали — центр героя.
    """
    if not box:
        return None
    ay = box["y0"] if captions_y < box["y0"] else box["y1"]
    return {"x": round((box["x0"] + box["x1"]) / 2, 4), "y": round(ay, 4)}


def camera_center(anchor: dict | None, zoom: float) -> dict:
    """x, y ключа камеры (точка, встающая в центр кадра), при которых ``anchor``
    при этом ``zoom`` остаётся на своём месте: 0.5 + (a − c)·zoom = a."""
    if not anchor or zoom <= 1.0:
        return {"x": 0.5, "y": 0.5}
    return {k: round(anchor[k] - (anchor[k] - 0.5) / zoom, 4) for k in ("x", "y")}
