"""Глитч на отрезке основы (MCP ``add_glitch``, шаблон «Глитч на отрезке» в редакторе).

Не переход, а эффект на времени клипа: слой ``fx`` выбранного стиля поверх видео и под слоями
(титры чистые) или над всеми (``over_layers``). Сила «сбоит» рывками — ключи ``scale`` слоя
(масштаб эффекта кадра умножает его силу, frontend/src/motion/layers/fx.ts). Всё рисует моушн-движок:
превью = рендер, кадр агента (``view_frame``) — тот же.

Стили → вид ``fx``: ``rgb_split`` (R/B рывками в стороны), ``tear`` (разрывы строк вбок с переносом),
``vhs`` (кассета: расслоение, полосы, трекинг, зерно), ``datamosh_lite`` (макроблоки текут и
пикселизуются), ``pixel_sort_lite`` (штрихи-подтёки цвета). Звук — только настоящие записи пака
freesound v2 (``GLITCH_SFX``; цифровые «глитч»-звуки владелец из пака убрал как дешёвые), слой
``audio``; нет файла в паке — без звука, с пометкой.
"""

from __future__ import annotations

from typing import Any

GLITCH_STYLES = ("rgb_split", "tear", "vhs", "datamosh_lite", "pixel_sort_lite")
GLITCH_ALIASES = {
    "rgb": "rgb_split", "rgb_shift": "rgb_split", "split": "rgb_split", "chromatic": "rgb_split",
    "tearing": "tear", "lines": "tear", "datamosh": "datamosh_lite", "mosh": "datamosh_lite",
    "blocks": "datamosh_lite", "pixel_sort": "pixel_sort_lite", "pixelsort": "pixel_sort_lite",
    "sort": "pixel_sort_lite", "tape": "vhs", "vcr": "vhs",
}
# Записи пака freesound v2 (tools/fetch_sfx.py): удар-переход, скретч пластинки, бас-дроп, вжух.
GLITCH_SFX = {
    "rgb_split": "sfx:freesound/hit-transition.ogg",
    "tear": "sfx:freesound/record-scratch.ogg",
    "vhs": "sfx:freesound/record-scratch.ogg",
    "datamosh_lite": "sfx:freesound/bass-drop.ogg",
    "pixel_sort_lite": "sfx:freesound/whoosh-4.ogg",
}
SFX_MAX_SEC = 1.6  # звук глитча — не длиннее 1.6 с (затухание 0.15 с)
MIN_DURATION = 0.1
MAX_DURATION = 10.0
DEFAULT_DURATION = 0.8
DEFAULT_INTENSITY = 0.8


class GlitchError(ValueError):
    pass


def glitch_style(value: Any) -> str:
    style = str(value or "rgb_split").strip().lower().replace("-", "_").replace(" ", "_")
    style = GLITCH_ALIASES.get(style, style)
    if style not in GLITCH_STYLES:
        raise GlitchError(f"style — одно из {', '.join(GLITCH_STYLES)}")
    return style


def _r3(v: float) -> float:
    # Как Math.round(v * 1000) / 1000 у движка (половины — вверх).
    import math

    return math.floor(v * 1000 + 0.5) / 1000


def glitch_keys(duration: float) -> list[dict]:
    """Ключи силы (``keys.scale``) на отрезке: удар, провал, второй удар, третий, затухание.
    Зеркало frontend/src/motion/layers/fx.ts ``glitchKeys``."""
    length = max(0.1, float(duration))
    attack = min(0.05, length * 0.1)
    if length < 0.4:
        return [{"t": 0, "v": 0.3, "ease": "linear"}, {"t": _r3(attack), "v": 1, "ease": "linear"},
                {"t": _r3(length), "v": 0.2}]
    return [
        {"t": 0, "v": 0.25, "ease": "linear"},
        {"t": _r3(attack), "v": 1, "ease": "linear"},
        {"t": _r3(length * 0.3), "v": 0.45, "ease": "step"},
        {"t": _r3(length * 0.42), "v": 1, "ease": "linear"},
        {"t": _r3(length * 0.7), "v": 0.6, "ease": "step"},
        {"t": _r3(length * 0.82), "v": 0.95, "ease": "outCubic"},
        {"t": _r3(length), "v": 0.15},
    ]


def build_glitch(at: Any, duration: Any = None, style: Any = None, intensity: Any = None, *,
                 sfx: Any = False, sfx_volume: Any = None, speed: Any = None, seed: Any = None,
                 clip_duration: float | None = None) -> dict:
    """Слои глитча: ``{"fx": слой fx, "audio": слой звука или None, "style", "range"}``. Ничего не сохраняет.

    ``sfx``: False — без звука; True — запись под стиль (``GLITCH_SFX``); строка ``sfx:<pack>/<file>`` — своя.
    """
    try:
        t0 = max(0.0, float(at))
    except (TypeError, ValueError):
        raise GlitchError("at — число секунд клипа") from None
    try:
        d = DEFAULT_DURATION if duration in (None, "") else float(duration)
    except (TypeError, ValueError):
        raise GlitchError("duration — число секунд") from None
    if not (MIN_DURATION <= d <= MAX_DURATION):
        raise GlitchError(f"duration {d:g} с — нужно {MIN_DURATION:g}..{MAX_DURATION:g}")
    if clip_duration:
        if t0 >= clip_duration:
            raise GlitchError(f"at {t0:g} с — после конца клипа ({clip_duration:.2f} с)")
        d = max(MIN_DURATION, min(d, clip_duration - t0))
    name = glitch_style(style)
    try:
        power = DEFAULT_INTENSITY if intensity in (None, "") else float(intensity)
    except (TypeError, ValueError):
        raise GlitchError("intensity — число 0..1") from None
    power = round(min(1.0, max(0.05, power)), 3)
    t1 = _r3(t0 + d)
    props: dict = {"kind": name, "intensity": power, "speed": 1 if speed in (None, "") else float(speed)}
    if seed not in (None, ""):
        props["seed"] = int(seed)
    fx = {"type": "fx", "name": "Глитч", "start": _r3(t0), "end": t1, "track": 0,
          "keys": {"scale": glitch_keys(t1 - _r3(t0))}, "props": props}
    audio = None
    if sfx:
        ref = sfx if isinstance(sfx, str) and sfx.startswith("sfx:") else GLITCH_SFX[name]
        try:
            volume = 0.7 if sfx_volume in (None, "") else float(sfx_volume)
        except (TypeError, ValueError):
            raise GlitchError("sfx_volume — число 0..1") from None
        end = _r3(t0 + max(0.6, min(SFX_MAX_SEC, d + 0.3)))
        if clip_duration:
            end = min(end, _r3(clip_duration))
        audio = {"type": "audio", "name": "Звук глитча", "start": _r3(t0), "end": max(end, _r3(t0 + 0.1)),
                 "props": {"asset_id": ref, "volume": round(min(1.0, max(0.0, volume)), 3), "fadeOut": 0.15}}
    return {"fx": fx, "audio": audio, "style": name, "range": [_r3(t0), t1]}


__all__ = ["GLITCH_SFX", "GLITCH_STYLES", "GlitchError", "build_glitch", "glitch_keys", "glitch_style"]
