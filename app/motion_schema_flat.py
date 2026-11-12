"""Моушн-слой ``ui`` — плоский интерфейс на токенах темы: схема props. Подключается в ``app/motion_schema.PROPS``.

Движок — frontend/src/motion/flat (токены тем ``tokens.ts``, значки Lucide ``icons.ts``, компоненты
``components.ts``/``cards.ts``) и переходник frontend/src/motion/layers/flatui.ts; те же компоненты рисуют
плоские экраны в 3D (node/scene3d). Всё необязательное — умолчания знает движок (палитра темы, кегли, ширины).
Документация — docs/motion/flat-ui.md.
"""

from __future__ import annotations

from app.motion_fields import Choice, Color, Flag, ListOf, Num, Obj, Opt, Text
from app.motion_limits import FLAT_COMPONENTS, FLAT_ITEMS_MAX, FLAT_REVEALS, FLAT_THEMES, MAX_TIME_SEC

FONT = Text("", 60)
# Имя значка Lucide (eye, hourglass, circle-check…) или русский алиас («глаз», «галочка»); неизвестное — без значка.
ICON = Text("", 40)

FLAT_ITEM = Obj({
    "text": Opt(Text("", 160)),
    "sub": Opt(Text("", 200)),
    "note": Opt(Text("", 40)),
    "icon": Opt(ICON),
    "value": Opt(Num(0, -1e12, 1e12, 4)),
    "max": Opt(Num(0, 0, 1e12, 4)),
    "color": Opt(Color(None, nullable=True)),
    "dim": Opt(Flag(False)),
    "at": Opt(Num(0, 0, MAX_TIME_SEC, 3)),
    "done": Opt(Flag(True)),
    "from": Opt(Choice("them", ("me", "them"))),
})

UI: dict = {
    "component": Choice("stat", FLAT_COMPONENTS),
    "theme": Opt(Choice("dark", FLAT_THEMES)),
    "accent": Opt(Color(None, nullable=True)),
    "plate": Opt(Color(None, nullable=True)),
    "variant": Opt(Text("", 20)),
    "w": Opt(Num(900, 120, 2160, 1)),
    "h": Opt(Num(0, 0, 3840, 1)),
    "size": Opt(Num(76, 12, 400, 1)),
    "label": Opt(Text("", 80)),
    "title": Opt(Text("", 200)),
    "text": Opt(Text("", 500)),
    "icon": Opt(ICON),
    "value": Opt(Num(0, -1e12, 1e12, 4)),
    "from": Opt(Num(0, -1e12, 1e12, 4)),
    "decimals": Opt(Num(0, 0, 3, integer=True)),
    "prefix": Opt(Text("", 12)),
    "suffix": Opt(Text("", 12)),
    "unit": Opt(Text("", 40)),
    "delta": Opt(Text("", 20)),
    "progress": Opt(Num(0, 0, 1, 4)),
    "items": Opt(ListOf(FLAT_ITEM, FLAT_ITEMS_MAX)),
    "marker": Opt(Num(0, 0, 1, 4)),
    "markerLabel": Opt(Text("", 20)),
    "url": Opt(Text("", 120)),
    "time": Opt(Text("", 20)),
    "badge": Opt(Num(0, 0, 999, integer=True)),
    "letter": Opt(Text("", 2)),
    "delay": Opt(Num(0.5, 0, MAX_TIME_SEC, 3)),
    "stagger": Opt(Num(0.6, 0, 30, 3)),
    "reveal": Opt(Choice("check", FLAT_REVEALS)),
    "font": Opt(FONT),
    "bodyFont": Opt(FONT),
}

FLAT_PROPS: dict[str, dict] = {"ui": UI}
