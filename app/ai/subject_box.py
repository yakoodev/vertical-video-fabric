"""Где на кадре персонаж: рамка главного героя клипа глазами Gemini.

Субтитры и титры нельзя класть на персонажа (владелец: «субтитры прям на
персонаже»). Детектор людей (YOLOX) аниме-модель VTuber'а не видит, а Gemini
видит что угодно: показываем ему кадры готового вида (кроп, раскладка) с каждого
куска и просим рамку героя — лица/модели стримера — на каждый кадр. Рамка клипа —
объединение, плюс рамка каждого куска (``subject_boxes``). Дальше ``text_positions``
выбирает свободную зону над или под ним.

Рамки кэшируются по составу кусков, кадрированию и раскладке: модель зовём один раз.
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
    "На {n} кадрах вертикального видео 9:16 (по порядку) найди главного героя — лицо/модель стримера "
    "(в том числе нарисованную аниме-модель VTuber), а если его нет — главный объект; в диалоге — того, "
    "кто в кадре крупнее. Ответь ТОЛЬКО JSON: {{\"frames\": [{{\"x0\":…,\"y0\":…,\"x1\":…,\"y1\":…}}, …]}} — "
    "по рамке на КАЖДЫЙ кадр в том же порядке, доли кадра 0..1 (y0 — верх головы, y1 — низ видимого "
    "тела). Кадр без героя — {{}}."
)
# Кадров на кусок: 3 на одном-двух кусках, 2 на трёх, дальше по одному (кадр — прогон компоновщика,
# а рамки пересчитываются после каждой смены кадрирования); всего не больше 12.
MAX_FRAMES = 12
# Как назвать кадр формата клипа в вопросе модели (кадры рендерятся в формате клипа).
_FRAME_WORDS = {"9:16": "вертикального видео 9:16", "1:1": "квадратного видео 1:1", "4:5": "видео 4:5",
                "16:9": "горизонтального видео 16:9"}


def prompt_for_format(fmt: str, n: int) -> str:
    """Вопрос модели под кадр формата клипа; 9:16 — прежний текст."""
    text = PROMPT.format(n=n)
    words = _FRAME_WORDS.get(fmt)
    return text.replace("вертикального видео 9:16", words, 1) if words else text


def _plan_format(plan: dict) -> str:
    from app.formats import settings_render_format

    return settings_render_format(plan.get("render_settings"))


def _cache_key(plan: dict) -> str:
    render = plan.get("render_settings") or {}
    key = {"pieces": [(s["start_sec"], s["end_sec"], s.get("focus") or [], s.get("fit")) for s in plan.get("segments") or []],
           "layout": render.get("layout"), "mirror": render.get("mirror")}
    fmt = _plan_format(plan)
    if fmt != "9:16":  # другой кадр — другие рамки; у 9:16 ключ прежний (кэш не теряется)
        key["format"] = fmt
    raw = json.dumps(key, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def subject_box(store: Any, clip_plan_id: int, client: Any = None) -> dict | None:
    """{x0,y0,x1,y1} героя в долях кадра формата клипа (объединение по всему клипу) или None."""
    return subject_boxes(store, clip_plan_id, client)["union"]


def _frame_times(store: Any, plan: dict) -> list[tuple[int, float]]:
    """(номер куска, время клипа) кадров для зрения — по кадру-два с каждого куска."""
    from app.clip_settings import normalize_render_settings
    from app.motion import clip_timeline

    segments = plan.get("segments") or []
    spans = [(float(s["start_sec"]), float(s["end_sec"])) for s in segments]
    starts, _total = clip_timeline(spans, normalize_render_settings(plan.get("render_settings") or {})["transition"])
    if not segments:
        return []
    per = max(1, min(3, 6 // len(segments)))
    fractions = {1: (0.5,), 2: (0.3, 0.7), 3: (0.2, 0.5, 0.8)}[per]
    out = []
    for index, ((a, b), start) in enumerate(zip(spans, starts)):
        if len(out) >= MAX_FRAMES:
            break
        out.extend((index, start + (b - a) * f) for f in fractions)
    return out[:MAX_FRAMES]


def subject_boxes(store: Any, clip_plan_id: int, client: Any = None) -> dict:
    """Рамки героя ``{"union": box|None, "pieces": [box|None по кускам]}``.

    Агент-монтажёр: одна рамка на весь клип вводит в заблуждение на многоплановой
    сцене (Рик слева в одном куске, Морти справа в другом). Поэтому кадры берём с
    каждого куска и спрашиваем рамку на каждый кадр; рамка куска — объединение его
    кадров. Нет Gemini — всё None."""
    plan = store.get_clip_plan(clip_plan_id)
    segments = plan.get("segments") or []
    empty = {"union": None, "pieces": [None] * len(segments)}
    cache_dir = settings.runtime_dir / "subject-box"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / f"{clip_plan_id}-{_cache_key(plan)}-v2.json"
    if cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if isinstance(data, dict) and len(data.get("pieces") or []) == len(segments):
                return {"union": data.get("union") or None, "pieces": [b or None for b in data["pieces"]]}
        except (OSError, ValueError):
            pass
    if client is None:
        if not settings.gemini_api_key:
            return empty
        from app.ai.gemini import GeminiClient

        client = GeminiClient()
    from app.motion_render import render_frame_png

    frames = _frame_times(store, plan)
    if not frames:
        return empty
    parts: list[dict] = [{"text": prompt_for_format(_plan_format(plan), len(frames))}]
    for _index, t in frames:
        png = render_frame_png(store, clip_plan_id, t, layers=[], camera=[])
        parts.append({"inlineData": {"mimeType": "image/png", "data": base64.b64encode(_shrink(png)).decode()}})
    response = client.generate_content(
        settings.gemini_text_model,
        {"contents": [{"role": "user", "parts": parts}],
         "generationConfig": {"responseMimeType": "application/json", "temperature": 0}},
    )
    text = "".join(
        p.get("text", "") for p in ((response.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
    )
    boxes = _parse_boxes(text, len(frames))
    pieces: list[dict | None] = []
    for index in range(len(segments)):
        pieces.append(_union([b for (i, _t), b in zip(frames, boxes) if i == index]))
    out = {"union": _union(boxes), "pieces": pieces}
    try:
        cache.write_text(json.dumps(out), encoding="utf-8")
    except OSError:
        pass
    return out


def _union(boxes: list[dict | None]) -> dict | None:
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    return {"x0": min(b["x0"] for b in boxes), "y0": min(b["y0"] for b in boxes),
            "x1": max(b["x1"] for b in boxes), "y1": max(b["y1"] for b in boxes)}


def _parse_boxes(text: str, n: int) -> list[dict | None]:
    """Ответ модели → рамка на каждый из ``n`` кадров. Старый ответ одной рамкой —
    она на все кадры (лучше, чем ничего)."""
    try:
        start, end = text.index("{"), text.rindex("}") + 1
        data = json.loads(text[start:end])
    except ValueError:
        return [None] * n
    if isinstance(data, dict) and isinstance(data.get("frames"), list):
        boxes = [_box_of(item) for item in data["frames"][:n]]
        return boxes + [None] * (n - len(boxes))
    return [_box_of(data)] * n


def _box_of(data: Any) -> dict | None:
    try:
        box = {k: min(1.0, max(0.0, float(data[k]))) for k in ("x0", "y0", "x1", "y1")}
    except (ValueError, KeyError, TypeError):
        return None
    if box["y1"] - box["y0"] < 0.05 or box["x1"] - box["x0"] < 0.03:
        return None
    return box


def _shrink(png: bytes) -> bytes:
    """1080×1920 PNG → JPEG 360×640: для рамки героя хватает, а токенов меньше. Другой
    формат — та же длинная сторона 640 с его пропорцией (16:9 — 640×360, 1:1 — 640×640)."""
    try:
        import cv2
        import numpy as np

        img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
        height, width = img.shape[:2]
        scale = 640 / max(height, width)
        img = cv2.resize(img, (max(2, round(width * scale)), max(2, round(height * scale))), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".png", img)
        return buf.tobytes() if ok else png
    except Exception:  # noqa: BLE001 - без cv2 отдаём как есть
        return png


def _parse_box(text: str) -> dict | None:
    try:
        start, end = text.index("{"), text.rindex("}") + 1
        return _box_of(json.loads(text[start:end]))
    except ValueError:
        return None


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
