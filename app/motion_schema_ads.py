"""Моушн-слои рекламного пакета: схемы props (облако пузырей, карусель, волна голоса, стена плиток,
схема сделки) и поля одометра у counter. Подключаются в ``app/motion_schema.PROPS``.

Движок — frontend/src/motion/layers/{bubbles,carousel,waveform,tilewall,flow,odometer}.ts, типы —
frontend/src/motion/adtypes.ts. Значки — свои векторные (``AD_ICONS``) или 1–3 буквы; картинки — id файла
из «Файлов для монтажа» или ``sticker:<pack>/<file>``. Всё необязательное — умолчания знает движок.
"""

from __future__ import annotations

from app.motion_fields import AssetRef, Choice, Color, Colors, Flag, ListOf, Num, Obj, Opt, Text
from app.motion_limits import (
    AD_THEMES, BUBBLE_STYLES, BUBBLES_MAX, CAROUSEL_MAX, FLOW_LAYOUTS, FLOW_MAX, MAX_TIME_SEC, TILE_WALL_MAX_IMAGES,
    WAVE_MOTIONS, WAVE_SOURCES, WAVE_STYLES,
)

FONT = Text("", 60)
PICTURE_REF = AssetRef(prefixes=("sticker",))
# Свой значок по имени (AD_ICONS) или 1–3 буквы.
ICON = Text("", 24)

# Одометр у counter: каждая цифра — барабан, разряды лесенкой через stagger с, у младших — laps лишних оборотов.
COUNTER_ROLL: dict = {
    "roll": Opt(Flag(False)),
    "stagger": Opt(Num(0.06, 0, 0.5, 3)),
    "laps": Opt(Num(2, 0, 6, integer=True)),
}

BUBBLES: dict = {
    "items": ListOf(Obj({
        "label": Opt(Text("", 40)),
        "value": Opt(Num(0, 0, 1e12, 2)),
        "prefix": Opt(Text("", 8)),
        "suffix": Opt(Text("", 8)),
        "icon": Opt(ICON),
        "image": Opt(PICTURE_REF),
        "color": Opt(Color(None, nullable=True)),
    }), BUBBLES_MAX),
    "w": Opt(Num(960, 100, 2160, 1)),
    "h": Opt(Num(960, 100, 3840, 1)),
    "minSize": Opt(Num(220, 40, 1000, 1)),
    "maxSize": Opt(Num(380, 40, 1200, 1)),
    "gap": Opt(Num(22, 0, 200, 1)),
    "seed": Opt(Num(7, 0, 999999, integer=True)),
    "delay": Opt(Num(0.15, 0, 30, 3)),
    "stagger": Opt(Num(0.12, 0, 3, 3)),
    "float": Opt(Num(12, 0, 80, 1)),
    "focus": Opt(Num(-1, -1, BUBBLES_MAX - 1, integer=True)),
    "focusAt": Opt(Num(0, 0, MAX_TIME_SEC, 3)),
    "focusScale": Opt(Num(1.35, 1, 2, 3)),
    "style": Opt(Choice("glass", BUBBLE_STYLES)),
    "theme": Opt(Choice("dark", AD_THEMES)),
    "accent": Opt(Color("#6c5cff")),
    "font": Opt(FONT),
}

CAROUSEL: dict = {
    "cards": ListOf(Obj({
        "image": Opt(PICTURE_REF),
        "title": Opt(Text("", 40)),
        "subtitle": Opt(Text("", 60)),
        "meta": Opt(Text("", 60)),
        "metaIcon": Opt(ICON),
        "color": Opt(Color(None, nullable=True)),
    }), CAROUSEL_MAX),
    "w": Opt(Num(1080, 200, 4000, 1)),
    "cardW": Opt(Num(600, 120, 2000, 1)),
    "cardH": Opt(Num(780, 160, 3000, 1)),
    "spacing": Opt(Num(516, 50, 3000, 1)),
    "start": Opt(Num(0, 0, CAROUSEL_MAX - 1, integer=True)),
    "switchAt": Opt(ListOf(Num(0, 0, MAX_TIME_SEC, 3), 60)),
    "interval": Opt(Num(1.8, 0, 60, 3)),
    "switchDur": Opt(Num(0.65, 0.1, 5, 3)),
    "ease": Opt(Text("inOutCubic", 60)),
    "sideScale": Opt(Num(0.8, 0.3, 1, 3)),
    "sideOpacity": Opt(Num(0.5, 0, 1, 3)),
    "halo": Opt(Flag(True)),
    "wrap": Opt(Flag(True)),
    "radius": Opt(Num(40, 0, 400, 1)),
    "theme": Opt(Choice("dark", AD_THEMES)),
    "accent": Opt(Color("#6c5cff")),
    "font": Opt(FONT),
}

WAVEFORM: dict = {
    "source": Opt(Choice("auto", WAVE_SOURCES)),
    # id звукового слоя клипа (audio) — волна по его звуку; или файл asset_id (секунда = from + время слоя).
    "audio_layer": Opt(Text("", 32)),
    "asset_id": Opt(AssetRef()),
    "from": Opt(Num(0, 0, MAX_TIME_SEC, 3)),
    "style": Opt(Choice("bars", WAVE_STYLES)),
    "motion": Opt(Choice("center", WAVE_MOTIONS)),
    "bars": Opt(Num(36, 4, 128, integer=True)),
    "w": Opt(Num(820, 40, 2160, 1)),
    "h": Opt(Num(220, 20, 1920, 1)),
    "barWidth": Opt(Num(0.56, 0.1, 1, 3)),
    "color": Opt(Color("#6c5cff")),
    "gradient": Opt(Colors(None, 2, 2, nullable=True)),
    "glow": Opt(Flag(True)),
    "sensitivity": Opt(Num(1.2, 0.3, 3, 3)),
    "minLevel": Opt(Num(0.06, 0, 1, 3)),
    "smooth": Opt(Num(0.12, 0, 1, 3)),
    "span": Opt(Num(1.6, 0.2, 10, 3)),
    "seed": Opt(Num(5, 0, 999999, integer=True)),
}

TILE_WALL: dict = {
    "images": Opt(ListOf(PICTURE_REF, TILE_WALL_MAX_IMAGES)),
    "colors": Opt(Colors(None, 1, 8)),
    "w": Opt(Num(1080, 100, 4000, 1)),
    "h": Opt(Num(1920, 100, 4000, 1)),
    "tile": Opt(Num(230, 40, 1000, 1)),
    "gap": Opt(Num(34, 0, 400, 1)),
    "radius": Opt(Num(0.24, 0, 0.5, 3)),
    "tiltX": Opt(Num(26, -60, 60, 2)),
    "tiltZ": Opt(Num(-16, -180, 180, 2)),
    "speed": Opt(Num(46, 0, 1000, 1)),
    "angle": Opt(Num(-45, -360, 360, 2)),
    "overlay": Opt(Num(0.55, 0, 1, 3)),
    "overlayColor": Opt(Color("#06070c")),
    "seed": Opt(Num(3, 0, 999999, integer=True)),
}

FLOW_DIAGRAM: dict = {
    "nodes": ListOf(Obj({
        "label": Opt(Text("", 40)),
        "sub": Opt(Text("", 60)),
        "icon": Opt(ICON),
        "image": Opt(PICTURE_REF),
    }), FLOW_MAX),
    "center": Opt(Num(1, 0, FLOW_MAX - 1, integer=True)),
    "lock": Opt(Flag(True)),
    "layout": Opt(Choice("row", FLOW_LAYOUTS)),
    "w": Opt(Num(1000, 200, 2160, 1)),
    "h": Opt(Num(620, 200, 3840, 1)),
    "delay": Opt(Num(0.3, 0, 30, 3)),
    "stepGap": Opt(Num(0.8, 0.2, 10, 3)),
    "done": Opt(Text("Готово", 60)),
    "doneAt": Opt(Num(0, 0, MAX_TIME_SEC, 3)),
    "doneColor": Opt(Color("#22c55e")),
    "bend": Opt(Num(46, -600, 600, 1)),
    "theme": Opt(Choice("dark", AD_THEMES)),
    "accent": Opt(Color("#6c5cff")),
    "font": Opt(FONT),
}

AD_PROPS: dict[str, dict] = {
    "bubbles": BUBBLES,
    "carousel": CAROUSEL,
    "waveform": WAVEFORM,
    "tile_wall": TILE_WALL,
    "flow_diagram": FLOW_DIAGRAM,
}

# Точка наклона/наезда группы (доли кадра): rotX/rotY и ключи scale действуют вокруг неё.
PIVOT = Obj({"x": Num(0.5, -2, 3, 4), "y": Num(0.5, -2, 3, 4)}, nullable=True)
