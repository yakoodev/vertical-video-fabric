"""Фоторедактор документа обложки: фильтры картинки, «кромка» и подчёркивание текста, готовые стили.

Документ (app/cover_docs.py) рисует один код на холсте редактора и на сервере (frontend/src/motion/coverdoc.ts),
поэтому всё здесь — только данные: проверка полей из чужих рук и пресеты, которые редактор и агент кладут в слой.

Фильтры картинки (``filters`` слоя image), порядок применения — как в coverdoc.ts:

* ``saturation`` и ``accent`` — «ЧБ с акцентом»: насыщенность остального кадра ``rest`` (0 — чистый ЧБ), а цвета
  в диапазонах оттенков ``ranges`` (градусы 0–360, ``minSat`` — с какой насыщенности пиксель считается цветом)
  остаются; так из кадра игры остаются огонь и хитмаркер;
* ``brightness`` (−1…1, сдвиг яркости), ``contrast`` (−1…1, от средней яркости кадра, как PIL ImageEnhance);
* ``blur`` (размытие, px кадра документа), ``sharpen`` (резкость, сила нерезкой маски);
* ``shade`` — затемнение от края (``side`` left/right/top/bottom, ``size`` доля стороны, ``strength``,
  ``curve`` — крутизна спада, ``hold`` — доля полосы, залитая целиком), ``vignette`` — затемнение по краям
  кадра; цвет затемнения — ``shadeColor``.

Текст: ``edge`` — цветная кромка (копия текста левым цветом влево и правым вправо на ``offset`` px, под заливкой),
``underline`` — тонкая черта под каждой строкой (``width`` толщина, ``gap`` от базовой линии, ``inset`` с краёв).
"""

from __future__ import annotations

import math
import re
from typing import Any

COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?$")
SIDES = ("left", "right", "top", "bottom")
MAX_RANGES = 4
MAX_SHADES = 4

# Оранжевый и красный огня/хитмаркера (как в превью мувика BURN, 17.11): красный — только насыщенный,
# иначе остаются кожа и ржавчина.
FIRE_RANGES = [{"from": 10, "to": 56, "minSat": 0.0}, {"from": 344, "to": 8, "minSat": 0.55}]
DEFAULT_ACCENTS = ("#9646ff", "#ff781e")  # фиолетовый и оранжевый: кромка слова, подчёркивание, свечение


def _num(value: Any, default: float, low: float, high: float) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(num):
        return default
    return round(min(high, max(low, num)), 3)


def _color(value: Any, default: str) -> str:
    return value if isinstance(value, str) and COLOR_RE.match(value) else default


def _ranges(raw: Any) -> list[dict]:
    out = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict):
            out.append({"from": _num(item.get("from"), 0, 0, 360), "to": _num(item.get("to"), 0, 0, 360),
                        "minSat": _num(item.get("minSat"), 0, 0, 1)})
        if len(out) >= MAX_RANGES:
            break
    return out


def _shades(raw: Any) -> list[dict]:
    out = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        side = item.get("side") if item.get("side") in SIDES else "left"
        out.append({"side": side, "size": _num(item.get("size"), 0.4, 0.01, 1), "strength": _num(item.get("strength"), 0.7, 0, 1),
                    "curve": _num(item.get("curve"), 1.4, 0.2, 6), "hold": _num(item.get("hold"), 0, 0, 0.95)})
        if len(out) >= MAX_SHADES:
            break
    return out


def sanitize_filters(raw: Any) -> dict | None:
    """Фильтры слоя-картинки в известных полях и границах; пустые — None (картинка как есть)."""
    if not isinstance(raw, dict):
        return None
    accent = raw.get("accent")
    out = {
        "brightness": _num(raw.get("brightness"), 0, -1, 1),
        "contrast": _num(raw.get("contrast"), 0, -1, 1),
        "saturation": _num(raw.get("saturation"), 0, -1, 1),
        "accent": ({"ranges": _ranges(accent.get("ranges")), "rest": _num(accent.get("rest"), 0.06, 0, 1)}
                   if isinstance(accent, dict) else None),
        "blur": _num(raw.get("blur"), 0, 0, 60),
        "sharpen": _num(raw.get("sharpen"), 0, 0, 3),
        "vignette": _num(raw.get("vignette"), 0, 0, 1),
        "shade": _shades(raw.get("shade")),
        "shadeColor": _color(raw.get("shadeColor"), "#000000"),
    }
    plain = (not out["accent"] and not out["shade"] and not any(out[k] for k in
             ("brightness", "contrast", "saturation", "blur", "sharpen", "vignette")))
    return None if plain else out


def sanitize_edge(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    return {"left": _color(raw.get("left"), DEFAULT_ACCENTS[0] + "e6"), "right": _color(raw.get("right"), DEFAULT_ACCENTS[1] + "e6"),
            "offset": _num(raw.get("offset"), 7, 0, 200)}


def sanitize_underline(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    return {"color": _color(raw.get("color"), DEFAULT_ACCENTS[1]), "width": _num(raw.get("width"), 6, 1, 200),
            "gap": _num(raw.get("gap"), 18, -400, 400), "inset": _num(raw.get("inset"), 4, -400, 400)}


# --------------------------------------------------------------------------- #
# Готовые «виды» картинки (looks) и стили текста — одни и те же у редактора, агента и «как в ролике»
# --------------------------------------------------------------------------- #


def look_filters(look: str, side: str = "left") -> dict | None:
    """Фильтры кадра под вид: ``movie`` — ЧБ с огнём, тёмная сторона под текст, верхняя полоса, виньетка
    (превью мувика BURN); ``color`` — свои цвета, лёгкий контраст и затемнение под текст; ``none`` — как есть."""
    text_side = side if side in ("left", "right") else "left"
    if look == "movie":
        return sanitize_filters({
            "contrast": 0.25, "sharpen": 0.8, "vignette": 0.47, "shadeColor": "#050507",
            "accent": {"ranges": FIRE_RANGES, "rest": 0.06},
            "shade": [{"side": text_side, "size": 0.48, "strength": 0.84, "curve": 1.4},
                      {"side": "top", "size": 0.125, "strength": 1.0, "curve": 0.8, "hold": 0.42}],
        })
    if look == "color":
        return sanitize_filters({
            "contrast": 0.12, "saturation": 0.08, "sharpen": 0.5, "vignette": 0.3, "shadeColor": "#050507",
            "shade": [{"side": text_side, "size": 0.42, "strength": 0.7, "curve": 1.4}],
        })
    return None


LOOKS = [
    {"id": "movie", "name": "ЧБ с акцентом", "note": "Как в мувике: ЧБ, огонь и красное остаются, тень под текст"},
    {"id": "color", "name": "Цвет", "note": "Свои цвета кадра, лёгкий контраст, тень под текст"},
    {"id": "none", "name": "Без обработки", "note": "Кадр как есть"},
]


def text_style(style: str, size: float, accents: tuple[str, str] = DEFAULT_ACCENTS) -> dict:
    """Поля текстового слоя под стиль; ``size`` — кегль (от него толщины и сдвиги).

    ``movie-word`` — ударное слово мувика: белые разреженные капсы, фиолетовая/оранжевая кромка, оранжевое
    свечение и тонкое подчёркивание; ``movie-caps`` — строка над/под словом: разреженные капсы без украшений;
    ``hook-yellow``/``hook-white`` — прежние хуки обложек с обводкой (для вертикалок)."""
    k = size / 190.0
    if style == "movie-word":
        return {"font": "Montserrat", "weight": 800, "color": "#ffffff", "uppercase": True,
                "letterSpacing": round(14 * k, 2), "lineHeight": 1.0, "stroke": None, "shadow": None, "plate": None,
                "gradient": None,
                "edge": {"left": accents[0] + "e6", "right": accents[1] + "e6", "offset": round(7 * k, 2)},
                "glow": {"color": accents[1] + "52", "blur": round(44 * k, 2)},
                "underline": {"color": accents[1], "width": max(2, round(6 * k, 2)), "gap": round(18 * k, 2),
                              "inset": round(4 * k, 2)}}
    if style == "movie-caps":
        return {"font": "Montserrat", "weight": 700, "color": "#ffffff", "uppercase": True,
                "letterSpacing": round(size * 0.24, 2), "lineHeight": 1.0, "stroke": None, "shadow": None,
                "plate": None, "gradient": None, "edge": None, "glow": None, "underline": None}
    if style == "hook-white":
        return {"font": "Montserrat", "weight": 900, "color": "#ffffff", "uppercase": True, "letterSpacing": 0,
                "stroke": {"color": "#000000", "width": round(size * 0.11, 2)}, "edge": None, "underline": None,
                "glow": None, "plate": None, "gradient": None,
                "shadow": {"color": "#000000b3", "blur": round(size * 0.3, 2), "x": 0, "y": round(size * 0.1, 2)}}
    return {"font": "Montserrat", "weight": 900, "color": "#ffe600", "uppercase": True, "letterSpacing": 0,
            "stroke": {"color": "#000000", "width": round(size * 0.11, 2)}, "edge": None, "underline": None,
            "glow": None, "plate": None, "gradient": None,
            "shadow": {"color": "#000000b3", "blur": round(size * 0.3, 2), "x": 0, "y": round(size * 0.1, 2)}}


TEXT_STYLES = [
    {"id": "movie-word", "name": "Слово мувика", "note": "Белые разреженные капсы, кромка двумя цветами, свечение, черта"},
    {"id": "movie-caps", "name": "Строка мувика", "note": "Мелкие разреженные капсы над или под словом"},
    {"id": "hook-white", "name": "Белый хук", "note": "Крупные белые капсы с чёрной обводкой"},
    {"id": "hook-yellow", "name": "Жёлтый хук", "note": "Жёлтые капсы с обводкой (вертикалки)"},
]


def library_extras() -> dict:
    """Что редактору показать в «Стилях»: виды картинки с их фильтрами и стили текста (кегль 100 — редактор
    пересчитывает под свой)."""
    return {"looks": [{**look, "filters": look_filters(look["id"])} for look in LOOKS],
            "text_styles": [{**style, "fields": text_style(style["id"], 100)} for style in TEXT_STYLES]}
