"""Процедурные фоны: анимированные и зацикленные, рисует компоновщик моушна.

Владелец: «когда нужен фон — не используй статичную заливку … анимированный и
зацикленный». Фон нужен в трёх местах: за персонажем на зелёнке
(``render_settings.backdrop``), под/над видео раскладки meme (``layout.bg``) и
сверху/снизу куска «шире окна» (``segment.fit.bg``). Рисует его код
``frontend/src/motion/backgrounds.ts`` — тот же в превью редактора и в рендере
(node/motion-render.ts), без видеофайлов. Здесь — нормализация (зеркало TS) и
геометрия: где на кадре основы видео, а где фон.

Фон::

    {"kind": "aurora" | "grid" | "bokeh" | "waves" | "gradient" | "solid",
     "colors": [...],       # до трёх #rrggbb: основа, акцент, второй акцент; [] — в цветах клипа
     "speed": 1.0,          # 0.1..4, цикл — 10 / speed секунд, бесшовный
     "intensity": 0.7}      # яркость свечения 0..1

Обратная совместимость: старый ``{color, color2}`` и строка ``#rrggbb`` — заливка и
градиент как раньше; старый цвет по умолчанию (``#0b0d14`` без второго) — его никто
не выбирал, это анимированный фон по умолчанию.

ffmpeg-основа под анимированным фоном заливается его первым цветом
(``fill_color``) — если компоновщик упадёт, останется спокойная заливка в тон.
"""

from __future__ import annotations

import re
from typing import Any

BACKGROUND_KINDS = ("aurora", "grid", "bokeh", "waves", "gradient", "solid")
ANIMATED_KINDS = ("aurora", "grid", "bokeh", "waves")
BACKGROUND_PERIOD = 10.0
LEGACY_DEFAULT_COLOR = "#0b0d14"
DEFAULT_SPEED = 1.0
DEFAULT_INTENSITY = 0.7
DEFAULT_KIND = "aurora"
# Запас внутрь видео по внутренним краям дыры: ffmpeg округляет размер видео до
# чётного, пара пикселей фона на краю видео не видна, а щель заливки — видна.
HOLE_INSET = 2

_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _color(value: Any) -> str | None:
    return value.strip().lower() if isinstance(value, str) and _COLOR_RE.match(value.strip()) else None


def _num(value: Any, default: float, lo: float, hi: float) -> float:
    if value is None or value == "" or isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:
        return default
    return min(hi, max(lo, number))


def default_background(colors: list[str] | None = None, kind: str = DEFAULT_KIND) -> dict:
    """Анимированный фон по умолчанию; ``colors`` пусто — в цветах клипа."""
    return _finish(kind, list(colors or []), None, None)


def _finish(kind: str, colors: list[str], speed: Any, intensity: Any) -> dict:
    colors = colors[:3]
    if kind == "solid":
        colors = colors[:1] or [LEGACY_DEFAULT_COLOR]
    elif kind == "gradient":
        if not colors:
            colors = [LEGACY_DEFAULT_COLOR, "#1a1f33"]
        elif len(colors) == 1:
            colors = [colors[0], LEGACY_DEFAULT_COLOR]
    return {
        "kind": kind,
        "colors": colors,
        "speed": round(_num(speed, DEFAULT_SPEED, 0.1, 4.0), 2),
        "intensity": round(_num(intensity, DEFAULT_INTENSITY, 0.0, 1.0), 2),
    }


def normalize_background(raw: Any, fallback: dict | None = None) -> dict:
    """Фон в полной форме (зеркало ``normalizeBackground`` в TS).

    ``{kind, colors?, speed?, intensity?}`` — как есть; имя стиля строкой — этот стиль
    в цветах клипа; старый ``{color, color2}`` / ``#rrggbb`` — заливка или градиент;
    мусор и старый цвет по умолчанию — ``fallback`` (анимированный по умолчанию)."""
    fb = fallback if fallback is not None else default_background()

    def copy() -> dict:
        return {**fb, "colors": list(fb.get("colors") or [])}

    if isinstance(raw, str):
        s = raw.strip().lower()
        if s in BACKGROUND_KINDS:
            return _finish(s, [], None, None)
        color = _color(s)
        if color and color != LEGACY_DEFAULT_COLOR:
            return _finish("solid", [color], None, None)
        return copy()
    if not isinstance(raw, dict):
        return copy()
    kind = raw.get("kind")
    if isinstance(kind, str) and kind in BACKGROUND_KINDS:
        colors_raw = raw.get("colors") if isinstance(raw.get("colors"), list) else []
        colors = [c for c in (_color(v) for v in colors_raw) if c]
        return _finish(kind, colors, raw.get("speed"), raw.get("intensity"))
    c1, c2 = _color(raw.get("color")), _color(raw.get("color2"))
    if not c1 or (c1 == LEGACY_DEFAULT_COLOR and not c2):
        return copy()
    return _finish("gradient", [c1, c2], None, None) if c2 else _finish("solid", [c1], None, None)


def is_animated(bg: Any) -> bool:
    return isinstance(bg, dict) and bg.get("kind") in ANIMATED_KINDS


def fill_color(bg: Any) -> str:
    """Цвет заливки ffmpeg-основы под фоном: первый цвет фона (у анимированного без
    своих цветов — тёмная основа). Строка ``#rrggbb`` — она сама."""
    if isinstance(bg, str):
        return _color(bg) or LEGACY_DEFAULT_COLOR
    if isinstance(bg, dict):
        colors = bg.get("colors") or []
        if colors and _color(colors[0]):
            return _color(colors[0]) or LEGACY_DEFAULT_COLOR
    return LEGACY_DEFAULT_COLOR


def loop_seconds(bg: dict) -> float:
    """Длина бесшовного цикла фона, с."""
    return BACKGROUND_PERIOD / max(0.1, float(bg.get("speed") or DEFAULT_SPEED))


def phase(bg: dict, t: float) -> float:
    """Фаза цикла 0..1 (как ``backgroundPhase`` в TS): всё состояние фона — функция от неё."""
    x = max(0.0, float(t)) * max(0.1, float(bg.get("speed") or DEFAULT_SPEED)) / BACKGROUND_PERIOD
    return x - int(x)


# ---------------------------------------------------------------- геометрия зон


def _even(value: float) -> int:
    return max(2, int(round(value / 2.0)) * 2)


def _content_aspect(source: dict | None, crop: dict | None) -> float:
    """Высота / ширина картинки исходника после обрезки полей (неизвестно — 16:9)."""
    src = source or {}
    try:
        w = float(src.get("width") or 0)
        h = float(src.get("height") or 0)
    except (TypeError, ValueError):
        w = h = 0.0
    if w <= 0 or h <= 0:
        w, h = 16.0, 9.0
    c = crop or {"w": 1.0, "h": 1.0}
    return (h * float(c.get("h") or 1.0)) / max(1e-6, w * float(c.get("w") or 1.0))


def _hole(x: float, y: float, w: float, h: float, width: int, height: int) -> dict | None:
    """Прямоугольник видео в кадре, обрезанный по кадру и чуть ужатый по внутренним краям.
    None — видео закрывает весь кадр (фона не видно)."""
    x0, y0 = max(0, int(round(x))), max(0, int(round(y)))
    x1, y1 = min(width, int(round(x + w))), min(height, int(round(y + h)))
    if x1 <= x0 or y1 <= y0:
        return {"x": 0, "y": 0, "w": 0, "h": 0}
    if x0 == 0 and y0 == 0 and x1 == width and y1 == height:
        return None
    if x0 > 0:
        x0 += HOLE_INSET
    if y0 > 0:
        y0 += HOLE_INSET
    if x1 < width:
        x1 -= HOLE_INSET
    if y1 < height:
        y1 -= HOLE_INSET
    return {"x": x0, "y": y0, "w": max(0, x1 - x0), "h": max(0, y1 - y0)}


def meme_hole(layout: dict, source: dict | None, crop: dict | None, width: int, height: int) -> dict | None:
    """Где видео в раскладке meme — как ``render._layout_filter``: во всю ширину
    (× ``video_scale``, по центру), верх на ``video_y``."""
    top = _even(float(layout.get("video_y") or 0) * height) if float(layout.get("video_y") or 0) > 0 else 0
    video_w = _even(width * float(layout.get("video_scale") or 1.0))
    video_h = _even(video_w * _content_aspect(source, crop))
    return _hole((width - video_w) // 2, top, video_w, video_h, width, height)


def fit_hole(fit: dict, source: dict | None, crop: dict | None, width: int, height: int) -> dict | None:
    """Где видео на куске «шире окна» — как ``render._piece_fit_filter``: полоса во всю
    ширину посередине по вертикали."""
    w = min(1.0, max(0.05, float(fit.get("w") or 1.0)))
    video_w = _even(width / w)
    video_h = min(height, _even(video_w * _content_aspect(source, crop)))
    return _hole(0, (height - video_h) / 2, width, video_h, width, height)


def piece_zone(layout: dict | None, segment: dict, source: dict | None, crop: dict | None,
               width: int, height: int) -> dict | None:
    """Анимированный фон куска основы: ``{"bg", "hole"}`` или None (фона нет, он
    размытый/статичный — его рисует ffmpeg, или видео закрывает весь кадр)."""
    from app.video_crop import normalize_fit

    fit = normalize_fit(segment.get("fit"))
    if fit:
        bg = fit["bg"]
        if not is_animated(bg):
            return None
        hole = fit_hole(fit, source, crop, width, height)
    elif isinstance(layout, dict) and layout.get("mode") == "meme":
        bg = normalize_background(layout.get("bg"))
        if not is_animated(bg):
            return None
        hole = meme_hole(layout, source, crop, width, height)
    else:
        return None
    if hole is None:  # видео закрывает весь кадр — фона не видно
        return None
    return {"bg": bg, "hole": hole}


def needs_zones(layout: Any, segments: list[dict]) -> bool:
    """Нужен ли компоновщик ради анимированного фона основы (meme или кусок «шире окна»)."""
    from app.stream_layout import normalize_layout
    from app.video_crop import normalize_fit

    for segment in segments or []:
        fit = normalize_fit(segment.get("fit"))
        if fit and is_animated(fit["bg"]):
            return True
    if isinstance(layout, dict) and layout.get("mode") == "meme":
        return is_animated(normalize_layout(layout)["bg"])
    return False


# Хвост последней зоны: готовый ролик держит последний кадр чуть дольше кусков.
_TAIL_SEC = 30.0


def background_zones(
    layout: dict | None,
    parts: list[dict],
    overlaps: list[float],
    source: dict | None,
    crop: dict | None,
    width: int,
    height: int,
) -> list[dict]:
    """Зоны фона во времени готового клипа: ``[{t0, t1, bg, hole}]``.

    ``parts`` — то, что рендерится подряд (куски или части после вырезов), ``overlaps`` —
    их склейки (``transitions.plan_joins``). Граница двух частей — середина перехода
    (``join_times``); соседние одинаковые зоны сливаются."""
    from app.transitions import join_times

    if not parts:
        return []
    durations = [max(0.0, float(p["end_sec"]) - float(p["start_sec"])) for p in parts]
    joins = join_times(durations, list(overlaps or []))
    total = sum(durations) - sum(overlaps or [])
    bounds = [0.0, *joins, total + _TAIL_SEC]
    zones: list[dict] = []
    for index, part in enumerate(parts):
        zone = piece_zone(layout, part, source, crop, width, height)
        t0, t1 = round(bounds[index], 3), round(bounds[index + 1], 3)
        if zone is None or t1 <= t0:
            continue
        last = zones[-1] if zones else None
        if last and last["t1"] == t0 and last["bg"] == zone["bg"] and last["hole"] == zone["hole"]:
            last["t1"] = t1
            continue
        zones.append({"t0": t0, "t1": t1, **zone})
    return zones
