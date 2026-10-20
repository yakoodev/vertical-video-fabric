"""Моушн-слои клипа: ``render.layers`` и ``render.camera`` (контракт — docs/motion/spec.md).

Слои правят три стороны: редактор в браузере, ИИ-ассистент и внешние агенты
через файл клипа. Каждый пишет немного по-своему, поэтому здесь одна точка,
которая приводит всё к одной форме:

* ``normalize_layers`` / ``normalize_camera`` — «строго, но прощая»: неизвестные
  поля выбрасываются, числа зажимаются в рамки спецификации, мусорные значения
  заменяются умолчаниями, слои неизвестного типа отбрасываются, у слоя без id
  появляется id. Результат всегда можно отдать компоновщику как есть.
* ``validate_layers`` / ``validate_camera`` — то же самое, но вслух: список
  человеческих проблем для файла клипа и для ИИ-агента («слой 3: end ≤ start»).

Нормализация и проверка — один и тот же код: нормализатор умеет докладывать о
каждой своей правке. Поэтому проверка нормализованных слоёв всегда пуста, и
правила не разъезжаются между двумя копиями.

Схема полей описана декларативно (``LAYER_FIELDS``, ``PROPS``) — из неё же
строится машиночитаемое описание для ``GET /api/motion/schema``.
"""

from __future__ import annotations

import copy
import math
import re
import secrets
from dataclasses import dataclass
from typing import Any, Callable

# ---------------------------------------------------------------- пределы

MAX_LAYERS = 60
MAX_CAMERA_KEYS = 40
MAX_KEYS_PER_PROP = 64
MAX_TRACK = 7  # дорожки графики 0..7
MIN_LAYER_SEC = 0.1
DEFAULT_LAYER_SEC = 3.0
MAX_TIME_SEC = 36000.0  # 10 часов: дальше — явная ошибка в данных
MAX_TEXT = 400
MAX_CHART_ITEMS = 12
MAX_NAME = 80
MIN_ANIM_SEC = 0.05
MAX_ANIM_SEC = 3.0
LAYER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
SHORT_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{3}$")
# Ссылки на пакеты (эмодзи/стикеры/звуки) — только безопасные символы и без «..»:
# по ним ищется файл на диске.
# Своя кривая: bezier(x1,y1,x2,y2) — как CSS cubic-bezier.
BEZIER_RE = re.compile(r"^bezier\(\s*([-+0-9.eE\s,]+)\)$")
PACK_REF_RE = re.compile(r"^(emoji|sticker|sfx):[A-Za-z0-9_\-./]{1,160}$")

LAYER_TYPES = (
    "text", "shape", "image", "emoji", "counter", "progress", "chart", "callout", "captions", "chat", "audio",
)
VISUAL_TYPES = tuple(t for t in LAYER_TYPES if t != "audio")
EASINGS = (
    "linear", "inQuad", "outQuad", "inOutQuad", "inCubic", "outCubic", "inOutCubic",
    "inBack", "outBack", "inOutBack", "outElastic", "outBounce", "step",
)
DEFAULT_EASING = "inOutCubic"
ANIMATIONS = (
    "none", "fade", "pop", "zoom", "slideUp", "slideDown", "slideLeft", "slideRight",
    "drop", "spin", "blur", "wipe", "typewriter", "words", "draw",
)
TEXT_ONLY_ANIMATIONS = ("typewriter", "words")
# «draw» — обводка рисуется; у формы это линия, стрелка и круг (эллипс).
DRAW_SHAPE_KINDS = ("line", "arrow", "ellipse")
LOOPS = ("none", "pulse", "float", "shake", "wiggle", "spin", "blink")
BLENDS = ("normal", "multiply", "screen", "overlay", "add")
SHAPE_KINDS = ("rect", "ellipse", "line", "arrow", "triangle", "star", "burst")
CHART_KINDS = ("bar", "hbar", "line", "pie", "donut")
CALLOUT_STYLES = ("bar", "box", "pill", "neon")
ALIGNS = ("left", "center", "right")
# Анимированные субтитры: пресеты вида (CAPTION_STYLES движка) и анимации слов.
CAPTION_STYLES = (
    "hormozi", "mrbeast", "box", "karaoke", "neon", "minimal", "typewriter", "comic", "gradient", "outline",
)
DEFAULT_CAPTION_STYLE = "hormozi"
CAPTION_ANIMATIONS = ("pop", "bounce", "fade", "karaoke", "typewriter", "none")
# Где субтитры по умолчанию: выше интерфейса площадки (нижние ~18% кадра заняты).
DEFAULT_CAPTIONS_Y = 0.68
MAX_CAPTION_MARKS = 200
# Чат стрима: вид колонки сообщений (CHAT_STYLES движка).
CHAT_STYLES = ("twitch-dark", "bubbles", "minimal", "neon")
MAX_CHAT_HIGHLIGHT_USERS = 50
# Анимируемые свойства и рамки значений ключевых кадров. У масштаба в ключах
# разрешён 0 — «вырасти из точки» делают именно так.
ANIMATABLE: dict[str, tuple[float, float]] = {
    "x": (-2.0, 3.0),
    "y": (-2.0, 3.0),
    "scale": (0.0, 10.0),
    "rotation": (-3600.0, 3600.0),
    "opacity": (0.0, 1.0),
    "progress": (0.0, 1.0),
}


# ---------------------------------------------------------------- описания полей


class _Report:
    """Куда нормализатор докладывает о правках; без приёмника — молча."""

    def __init__(self, sink: list[str] | None) -> None:
        self.sink = sink

    def __call__(self, where: str, message: str) -> None:
        if self.sink is not None:
            self.sink.append(f"{where}: {message}")


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _fmt(value: float) -> str:
    return f"{value:g}"


@dataclass(frozen=True)
class Num:
    default: float
    lo: float
    hi: float
    digits: int = 3
    integer: bool = False

    def norm(self, value: Any, rep: _Report, where: str) -> float | int:
        number = _finite(value)
        if number is None:
            rep(where, f"нужно число, пришло {value!r}")
            number = float(self.default)
        elif number < self.lo or number > self.hi:
            rep(where, f"{_fmt(number)} вне допустимого {_fmt(self.lo)}..{_fmt(self.hi)}")
            number = min(self.hi, max(self.lo, number))
        if self.integer:
            return int(round(number))
        return round(number, self.digits)

    def fill(self) -> Any:
        return int(self.default) if self.integer else self.default

    def describe(self) -> dict:
        return {"type": "integer" if self.integer else "number", "default": self.fill(), "min": self.lo, "max": self.hi}


@dataclass(frozen=True)
class Text:
    default: str
    max: int

    def norm(self, value: Any, rep: _Report, where: str) -> str:
        if value is None:
            return self.default
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            value = _fmt(float(value))
        if not isinstance(value, str):
            rep(where, f"нужна строка, пришло {type(value).__name__}")
            return self.default
        if len(value) > self.max:
            rep(where, f"длиннее {self.max} символов — обрезано")
            value = value[: self.max]
        return value

    def fill(self) -> str:
        return self.default

    def describe(self) -> dict:
        return {"type": "string", "default": self.default, "maxLength": self.max}


@dataclass(frozen=True)
class Flag:
    default: bool

    def norm(self, value: Any, rep: _Report, where: str) -> bool:
        if value is True or value == 1 or value in ("1", "true"):
            return True
        if value is None:
            return self.default
        if value is False or value == 0 or value in ("0", "false"):
            return False
        rep(where, f"нужно true/false, пришло {value!r}")
        return self.default

    def fill(self) -> bool:
        return self.default

    def describe(self) -> dict:
        return {"type": "boolean", "default": self.default}


@dataclass(frozen=True)
class Choice:
    default: str
    options: tuple[str, ...]

    def norm(self, value: Any, rep: _Report, where: str) -> str:
        if value in self.options:
            return str(value)
        rep(where, f"{value!r} не из списка: {', '.join(self.options)}")
        return self.default

    def fill(self) -> str:
        return self.default

    def describe(self) -> dict:
        return {"type": "enum", "default": self.default, "options": list(self.options)}


def _color(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if COLOR_RE.match(value):
        return value.lower()
    if SHORT_COLOR_RE.match(value):  # #rgb — прощаем, разворачиваем в #rrggbb
        return "#" + "".join(ch * 2 for ch in value[1:]).lower()
    return None


@dataclass(frozen=True)
class Color:
    default: str | None
    nullable: bool = False

    def norm(self, value: Any, rep: _Report, where: str) -> str | None:
        if value is None and self.nullable:
            return None
        color = _color(value)
        if color is None:
            rep(where, f"цвет нужен как #rrggbb или #rrggbbaa, пришло {value!r}")
            return self.default
        return color

    def fill(self) -> str | None:
        return self.default

    def describe(self) -> dict:
        return {"type": "color", "default": self.default, "nullable": self.nullable}


@dataclass(frozen=True)
class Colors:
    """Список цветов: градиент [c1, c2] или палитра."""

    default: tuple | None
    min: int
    max: int
    nullable: bool = False

    def norm(self, value: Any, rep: _Report, where: str) -> list[str] | None:
        if value is None and self.nullable:
            return None
        if not isinstance(value, list):
            rep(where, f"нужен список цветов ({self.min}..{self.max})")
            return self.fill()
        out: list[str] = []
        for index, item in enumerate(value[: self.max]):
            color = _color(item)
            if color is None:
                rep(f"{where}[{index}]", f"цвет нужен как #rrggbb или #rrggbbaa, пришло {item!r}")
                continue
            out.append(color)
        if len(value) > self.max:
            rep(where, f"не больше {self.max} цветов — лишние отброшены")
        if len(out) < self.min:
            rep(where, f"нужно хотя бы {self.min} цвета")
            return self.fill()
        return out

    def fill(self) -> list[str] | None:
        return list(self.default) if self.default is not None else None

    def describe(self) -> dict:
        return {"type": "colors", "default": self.fill(), "minItems": self.min, "maxItems": self.max,
                "nullable": self.nullable}


@dataclass(frozen=True)
class Paint:
    """Заливка: один цвет или градиент [c1, c2]."""

    default: str

    def norm(self, value: Any, rep: _Report, where: str) -> str | list[str]:
        if isinstance(value, list):
            return Colors(None, 2, 2).norm(value, rep, where) or self.default
        return Color(self.default).norm(value, rep, where) or self.default

    def fill(self) -> str:
        return self.default

    def describe(self) -> dict:
        return {"type": "paint", "default": self.default, "description": "цвет или градиент [c1, c2]"}


@dataclass(frozen=True)
class Obj:
    fields: dict
    nullable: bool = False
    # Для nullable-объекта: что подставить, если поля нет (None — «выключено»).
    default_on: bool = False

    def norm(self, value: Any, rep: _Report, where: str) -> dict | None:
        if value is None and self.nullable:
            return None
        if not isinstance(value, dict):
            rep(where, "нужен объект" + (" или null" if self.nullable else ""))
            return self.fill()
        return _norm_fields(value, self.fields, rep, where)

    def fill(self) -> dict | None:
        if self.nullable and not self.default_on:
            return None
        return {
            name: copy.deepcopy(spec.fill()) for name, spec in self.fields.items() if not isinstance(spec, Opt)
        }

    def describe(self) -> dict:
        return {
            "type": "object",
            "nullable": self.nullable,
            "default": self.fill(),
            "fields": {name: spec.describe() for name, spec in self.fields.items()},
        }


@dataclass(frozen=True)
class ListOf:
    item: Any
    max: int

    def norm(self, value: Any, rep: _Report, where: str) -> list:
        if value is None:
            return []
        if not isinstance(value, list):
            rep(where, "нужен список")
            return []
        if len(value) > self.max:
            rep(where, f"не больше {self.max} элементов — лишние отброшены")
        return [self.item.norm(item, rep, f"{where}[{index}]") for index, item in enumerate(value[: self.max])]

    def fill(self) -> list:
        return []

    def describe(self) -> dict:
        return {"type": "list", "maxItems": self.max, "items": self.item.describe()}


@dataclass(frozen=True)
class AssetRef:
    """Файл из «Файлов для монтажа» или ссылка на пакет (``sfx:…``).

    id файла хранится строкой цифр («7») — так его типизирует движок
    (``asset_id?: string``) и так он ищется в assets.json компоновщика.
    """

    prefixes: tuple[str, ...] = ()
    allow_id: bool = True

    def norm(self, value: Any, rep: _Report, where: str) -> str | None:
        if value is None or value == "":
            return None
        if self.allow_id and not isinstance(value, bool):
            if isinstance(value, int) and value > 0:
                return str(value)
            if isinstance(value, str) and value.strip().isdigit() and int(value) > 0:
                return str(int(value))
            if isinstance(value, float) and value.is_integer() and value > 0:
                return str(int(value))
        if isinstance(value, str) and self.prefixes and PACK_REF_RE.match(value) and ".." not in value:
            if value.split(":", 1)[0] in self.prefixes:
                return value
        kinds = (["id файла для монтажа"] if self.allow_id else []) + [f"{p}:…" for p in self.prefixes]
        rep(where, f"{value!r} — ожидается {' или '.join(kinds)}")
        return None

    def fill(self) -> None:
        return None

    def describe(self) -> dict:
        return {"type": "asset", "default": None, "montage_asset_id": self.allow_id, "prefixes": list(self.prefixes)}


@dataclass(frozen=True)
class Opt:
    """Необязательное поле: нет во входе — нет и в выходе.

    Для свойств, у которых умолчание знает только движок (``panel`` графика —
    «тёмное стекло», а null — «без подложки»): подставив своё, мы бы изменили вид.
    """

    spec: Any

    def norm(self, value: Any, rep: _Report, where: str) -> Any:
        return self.spec.norm(value, rep, where)

    def describe(self) -> dict:
        return {**self.spec.describe(), "optional": True}


def _norm_fields(src: dict, fields: dict, rep: _Report, where: str) -> dict:
    """Нормализовать известные поля; отсутствующие — умолчанием, лишние — выбросить."""
    out: dict = {}
    for name, spec in fields.items():
        if name in src:
            out[name] = spec.norm(src[name], rep, f"{where}.{name}")
        elif not isinstance(spec, Opt):
            out[name] = copy.deepcopy(spec.fill())
    return out


# ---------------------------------------------------------------- схема слоёв

STROKE = Obj({"color": Color("#000000"), "width": Num(6, 0, 60, 1)}, nullable=True)
BORDER = Obj({"color": Color("#ffffff"), "width": Num(6, 0, 60, 1)}, nullable=True)
TEXT_BG = Obj(
    {
        "color": Color("#000000"),
        "opacity": Num(0.6, 0, 1, 3),
        "radius": Num(16, 0, 400, 1),
        "padX": Num(24, 0, 400, 1),
        "padY": Num(12, 0, 400, 1),
        "skew": Opt(Num(0, -30, 30, 2)),
        "gradient": Opt(Colors(None, 2, 2, nullable=True)),
    },
    nullable=True,
)
HIGHLIGHT = Obj(
    {
        "words": ListOf(Num(0, 0, 999, integer=True), 100),
        "color": Color("#ffd400"),
        "bg": Color(None, nullable=True),
    },
    nullable=True,
)
SHADOW = Obj(
    {
        "color": Color("#000000"),
        "blur": Num(0, 0, 200, 1),
        "x": Num(0, -500, 500, 1),
        "y": Num(0, -500, 500, 1),
        "opacity": Num(0.6, 0, 1, 3),
    },
    nullable=True,
)
# Пустое семейство — «шрифт по умолчанию» компоновщика (список — /api/motion/fonts).
FONT = Text("", 60)

TEXT_STYLE: dict = {
    "font": FONT,
    "weight": Num(800, 100, 900, integer=True),
    "size": Num(96, 12, 400, 1),
    "color": Color("#ffffff"),
    "gradient": Colors(None, 2, 2, nullable=True),
    "stroke": STROKE,
    "bg": TEXT_BG,
    "align": Choice("center", ALIGNS),
    "maxWidth": Num(920, 40, 2160, 1),
    # Фиксированная ширина блока: строки выравниваются по align внутри неё (ТОП-3, цитата).
    "boxWidth": Opt(Num(0, 40, 2160, 1)),
    "lineHeight": Num(1.15, 0.8, 2, 3),
    "letterSpacing": Num(0, -20, 100, 1),
    "uppercase": Flag(False),
    "italic": Flag(False),
    "highlight": HIGHLIGHT,
}

PROPS: dict[str, dict] = {
    "text": {"text": Text("", MAX_TEXT), **TEXT_STYLE},
    "shape": {
        "kind": Choice("rect", SHAPE_KINDS),
        "w": Num(400, 1, 4000, 1),
        "h": Num(400, 1, 4000, 1),
        "fill": Color("#ffffff", nullable=True),
        "stroke": STROKE,
        "radius": Num(0, 0, 2000, 1),
        "points": Num(5, 3, 24, integer=True),
        "dash": Flag(False),
        "bend": Opt(Num(0, -2000, 2000, 1)),
    },
    "image": {
        "asset_id": AssetRef(),
        "src": AssetRef(prefixes=("emoji", "sticker"), allow_id=False),
        "w": Num(600, 10, 4000, 1),
        "radius": Num(0, 0, 2000, 1),
        "border": BORDER,
        "crop": Obj(
            {"x": Num(0, 0, 1, 4), "y": Num(0, 0, 1, 4), "w": Num(1, 0.01, 1, 4), "h": Num(1, 0.01, 1, 4)},
            nullable=True,
        ),
        "flipX": Flag(False),
    },
    "emoji": {"char": Text("", 32), "size": Num(160, 12, 1000, 1)},
    "counter": {
        "from": Num(0, -1e12, 1e12, 4),
        "to": Num(100, -1e12, 1e12, 4),
        "decimals": Num(0, 0, 3, integer=True),
        "prefix": Text("", 20),
        "suffix": Text("", 20),
        "thousands": Text("", 3),
        **TEXT_STYLE,
    },
    "progress": {
        "w": Num(800, 20, 2160, 1),
        "h": Num(40, 4, 400, 1),
        "value": Num(1, 0, 1, 4),
        "track": Color("#ffffff33"),
        "fill": Paint("#ff3b30"),
        "radius": Num(20, 0, 200, 1),
        "label": Flag(False),
        "font": FONT,
        "labelColor": Color("#ffffff"),
        "glow": Opt(Flag(True)),
    },
    "chart": {
        "kind": Choice("bar", CHART_KINDS),
        "data": ListOf(
            Obj({"label": Text("", 40), "value": Num(0, -1e12, 1e12, 4), "color": Color(None, nullable=True)}),
            MAX_CHART_ITEMS,
        ),
        "w": Num(800, 50, 2160, 1),
        "h": Num(600, 50, 3840, 1),
        "palette": Colors((), 0, 12),
        "font": FONT,
        "labelColor": Color("#ffffff"),
        "showValues": Flag(True),
        "title": Text("", 100),
        "panel": Opt(Color(None, nullable=True)),
        "glow": Opt(Flag(True)),
        "centerLabel": Opt(Text("", 40)),
    },
    "callout": {
        "title": Text("", 100),
        "subtitle": Text("", 160),
        "accent": Color("#ffcc00"),
        "style": Choice("bar", CALLOUT_STYLES),
        "font": FONT,
        "w": Num(900, 100, 2160, 1),
        "skew": Opt(Num(0, -30, 30, 2)),
        "icon": Opt(Text("", 32)),
    },
    # Всё, кроме style, необязательно: не задано — берётся из пресета style в движке.
    # Подставь мы здесь свои умолчания, пресет бы не работал.
    "captions": {
        "style": Choice(DEFAULT_CAPTION_STYLE, CAPTION_STYLES),
        "font": Opt(FONT),
        "weight": Opt(Num(900, 100, 900, integer=True)),
        "size": Opt(Num(88, 12, 400, 1)),
        "color": Opt(Color("#ffffff")),
        "dimColor": Opt(Color(None, nullable=True)),
        "activeColor": Opt(Color("#ffe81f")),
        "activeBg": Opt(Color(None, nullable=True)),
        "gradient": Opt(Colors(None, 2, 2, nullable=True)),
        "stroke": Opt(STROKE),
        "glow": Opt(Color(None, nullable=True)),
        "bg": Opt(TEXT_BG),
        "uppercase": Opt(Flag(True)),
        "punctuation": Opt(Flag(False)),
        "maxWords": Opt(Num(3, 1, 8, integer=True)),
        "maxLines": Opt(Num(2, 1, 3, integer=True)),
        "maxWidth": Opt(Num(900, 100, 2160, 1)),
        "lineHeight": Opt(Num(1.12, 0.8, 2, 3)),
        "letterSpacing": Opt(Num(0, -20, 100, 1)),
        "highlightWords": Opt(
            ListOf(Obj({"i": Num(0, 0, 100000, integer=True), "color": Color("#2bff6b")}), MAX_CAPTION_MARKS)
        ),
        "emoji": Opt(ListOf(Obj({"i": Num(0, 0, 100000, integer=True), "char": Text("", 32)}), MAX_CAPTION_MARKS)),
        # Цвет слов второго, третьего… говорящего (метка s у слова в comp.words: A, B, C…).
        # Не задано — свой цвет на каждого, кроме главного (A).
        "speakerColors": Opt(ListOf(Obj({"s": Text("B", 8), "color": Color("#7dd3fc")}), 8)),
        # Чьи слова показывает слой: [] — всех; ["A"] — только главного, ["B", "C", "D"] — остальных
        # (второй слой субтитров другим местом и мельче — голоса не мешают друг другу).
        "speakers": Opt(ListOf(Text("A", 8), 8)),
        "animation": Opt(Choice("pop", CAPTION_ANIMATIONS)),
        "wordScale": Opt(Num(1.12, 1, 1.4, 3)),
        "tilt": Opt(Num(0, -20, 20, 2)),
    },
    # Сообщения берутся не из слоя, а из чата клипа (comp.chat) — слой задаёт вид и фильтры.
    "chat": {
        "style": Choice("twitch-dark", CHAT_STYLES),
        "w": Num(760, 200, 1080, 1),
        "maxMessages": Num(6, 1, 12, integer=True),
        "fontSize": Num(34, 14, 80, 1),
        "font": Opt(FONT),
        "showBadges": Flag(True),
        "showEmotes": Flag(True),
        "filter": Obj(
            {
                "minLength": Num(0, 0, 200, integer=True),
                "hideLinks": Flag(True),
                "hideCommands": Flag(True),
            }
        ),
        "highlightUsers": ListOf(Text("", 40), MAX_CHAT_HIGHLIGHT_USERS),
        "delay": Num(0, 0, 30, 2),
        "bg": Num(0.75, 0, 1, 3),
        "ttl": Num(15, 0, 600, 1),
    },
    "audio": {
        "asset_id": AssetRef(prefixes=("sfx",)),
        "volume": Num(1, 0, 2, 3),
        "fadeIn": Num(0, 0, 10, 3),
        "fadeOut": Num(0, 0, 10, 3),
        "offset": Num(0, 0, MAX_TIME_SEC, 3),
        "duck": Flag(False),
    },
}

ANIM = {"type": Choice("none", ANIMATIONS), "duration": Num(0.3, MIN_ANIM_SEC, MAX_ANIM_SEC, 3)}
LOOP = {"type": Choice("none", LOOPS), "speed": Num(1, 0.1, 5, 3), "amount": Num(1, 0, 3, 3)}

# Общие поля любого слоя (кроме id/type/start/end/keys/in/out/loop/props —
# у них своя логика ниже).
LAYER_FIELDS: dict = {
    "name": Text("", MAX_NAME),
    "track": Num(0, 0, MAX_TRACK, integer=True),
    "hidden": Flag(False),
    "locked": Flag(False),
    "x": Num(0.5, -2, 3, 4),
    "y": Num(0.5, -2, 3, 4),
    "scale": Num(1, 0.05, 10, 4),
    "rotation": Num(0, -3600, 3600, 2),
    "opacity": Num(1, 0, 1, 4),
    "shadow": SHADOW,
    "blend": Choice("normal", BLENDS),
}

CAMERA_FIELDS: dict = {
    "t": Num(0, 0, MAX_TIME_SEC, 3),
    "zoom": Num(1, 1, 4, 4),
    "x": Num(0.5, 0, 1, 4),
    "y": Num(0.5, 0, 1, 4),
}


# ---------------------------------------------------------------- нормализация


def new_layer_id(taken: set[str] | None = None) -> str:
    taken = taken or set()
    while True:
        candidate = f"l_{secrets.token_hex(3)}"
        if candidate not in taken:
            return candidate


def _label(index: int, raw: Any) -> str:
    """«layers[3] (l_7f3a «Панчлайн»)» — чтобы агент сразу нашёл слой."""
    if not isinstance(raw, dict):
        return f"layers[{index}]"
    bits = []
    if isinstance(raw.get("id"), str) and raw["id"]:
        bits.append(raw["id"][:32])
    if isinstance(raw.get("name"), str) and raw["name"].strip():
        bits.append(f"«{raw['name'].strip()[:40]}»")
    return f"layers[{index}]" + (f" ({' '.join(bits)})" if bits else "")


def _norm_bezier(value: str, rep: _Report, where: str) -> str | None:
    """``bezier(x1,y1,x2,y2)`` как CSS cubic-bezier: x в 0..1, y в -1..2 (перелёт)."""
    match = BEZIER_RE.match(value)
    if not match:
        return None
    numbers = [_finite(part) for part in match.group(1).split(",")]
    if len(numbers) != 4 or any(n is None for n in numbers):
        return None
    clean: list[float] = []
    for index, number in enumerate(numbers):
        lo, hi = (0.0, 1.0) if index % 2 == 0 else (-1.0, 2.0)
        if number < lo or number > hi:  # type: ignore[operator]
            rep(where, f"bezier: {'x' if index % 2 == 0 else 'y'}{index // 2 + 1}={_fmt(number)} "
                       f"вне {_fmt(lo)}..{_fmt(hi)}")
        clean.append(round(min(hi, max(lo, number)), 4))  # type: ignore[type-var]
    return "bezier(" + ",".join(_fmt(n) for n in clean) + ")"


def _norm_ease(value: Any, rep: _Report, where: str) -> str | None:
    if value in EASINGS:
        return str(value)
    if isinstance(value, str):
        bezier = _norm_bezier(value.strip(), rep, where)
        if bezier:
            return bezier
    rep(where, f"{value!r} — нет такого easing; есть: {', '.join(EASINGS)} или bezier(x1,y1,x2,y2)")
    return None


def _norm_keys(raw: Any, rep: _Report, where: str) -> dict:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        rep(where, "нужен объект {свойство: [точки]}")
        return {}
    out: dict = {}
    for prop, points in raw.items():
        if prop not in ANIMATABLE:
            rep(f"{where}.{prop}", f"не анимируется; можно: {', '.join(ANIMATABLE)}")
            continue
        if not isinstance(points, list):
            rep(f"{where}.{prop}", "нужен список точек [{t, v, ease?}]")
            continue
        lo, hi = ANIMATABLE[prop]
        value_spec = Num(max(lo, min(hi, 0.0)), lo, hi, 4)
        if len(points) > MAX_KEYS_PER_PROP:
            rep(f"{where}.{prop}", f"не больше {MAX_KEYS_PER_PROP} точек — лишние отброшены")
        clean: list[dict] = []
        for index, point in enumerate(points[:MAX_KEYS_PER_PROP]):
            at = f"{where}.{prop}[{index}]"
            if not isinstance(point, dict):
                rep(at, "нужен объект {t, v, ease?}")
                continue
            if _finite(point.get("t")) is None or _finite(point.get("v")) is None:
                rep(at, "t и v должны быть числами")
                continue
            item = {
                "t": Num(0, 0, MAX_TIME_SEC, 3).norm(point["t"], rep, f"{at}.t"),
                "v": value_spec.norm(point["v"], rep, f"{at}.v"),
            }
            if "ease" in point and point["ease"] is not None:
                ease = _norm_ease(point["ease"], rep, f"{at}.ease")
                if ease:
                    item["ease"] = ease
            clean.append(item)
        if clean:
            # Точки по времени: интерполяция ищет отрезок, в котором t.
            out[prop] = sorted(clean, key=lambda p: p["t"])
    return out


def _norm_anim(raw: Any, rep: _Report, where: str, layer: dict) -> dict:
    if isinstance(raw, str):  # «"in": "pop"» — прощаем краткую запись
        raw = {"type": raw}
    anim = Obj(ANIM).norm(raw, rep, where) if raw is not None else Obj(ANIM).fill()
    kind = anim["type"]
    ltype = layer["type"]
    if kind in TEXT_ONLY_ANIMATIONS and ltype != "text":
        rep(f"{where}.type", f"«{kind}» бывает только у текста — заменено на fade")
        anim["type"] = "fade"
    elif kind == "draw" and not (ltype == "shape" and layer["props"].get("kind") in DRAW_SHAPE_KINDS):
        rep(f"{where}.type", "«draw» бывает только у формы line/arrow/ellipse — заменено на fade")
        anim["type"] = "fade"
    half = round((layer["end"] - layer["start"]) / 2, 3)
    # У «без анимации» длительность ничего не значит — не придираемся к ней.
    if anim["type"] != "none" and anim["duration"] > half:
        rep(f"{where}.duration", f"{_fmt(anim['duration'])} с — больше половины длины слоя ({_fmt(half)} с)")
        anim["duration"] = max(MIN_ANIM_SEC, half)
    return anim


def _normalize_layer(raw: Any, index: int, taken: set[str], rep: _Report) -> dict | None:
    where = _label(index, raw)
    if not isinstance(raw, dict):
        rep(where, "слой должен быть объектом — отброшен")
        return None
    ltype = raw.get("type")
    if ltype not in LAYER_TYPES:
        rep(where, f"тип {ltype!r} неизвестен — слой отброшен; есть: {', '.join(LAYER_TYPES)}")
        return None

    raw_id = raw.get("id")
    if isinstance(raw_id, str) and LAYER_ID_RE.match(raw_id) and raw_id not in taken:
        layer_id = raw_id
    else:
        if raw_id not in (None, ""):
            reason = "повторяется" if raw_id in taken else "допустимы A-Z a-z 0-9 _ - (до 32)"
            rep(f"{where}.id", f"{raw_id!r} — {reason}; выдан новый id")
        layer_id = new_layer_id(taken)
    taken.add(layer_id)

    start = Num(0, 0, MAX_TIME_SEC, 3).norm(raw.get("start", 0), rep, f"{where}.start")
    if "end" in raw:
        end = Num(start + DEFAULT_LAYER_SEC, 0, MAX_TIME_SEC + DEFAULT_LAYER_SEC, 3).norm(
            raw.get("end"), rep, f"{where}.end"
        )
    elif ltype == "captions":
        # Субтитры без end — до конца клипа: показываются только слова речи.
        end = MAX_TIME_SEC
    else:
        end = round(start + DEFAULT_LAYER_SEC, 3)
    if end - start < MIN_LAYER_SEC - 1e-9:
        rep(f"{where}.end", f"{_fmt(end)} — нужно end ≥ start + {MIN_LAYER_SEC} (start {_fmt(start)})")
        end = round(start + MIN_LAYER_SEC, 3)

    layer: dict = {"id": layer_id, "type": ltype, "start": start, "end": end}
    layer.update(_norm_fields(raw, LAYER_FIELDS, rep, where))
    if ltype == "captions" and "y" not in raw:
        layer["y"] = DEFAULT_CAPTIONS_Y
    props_raw = raw.get("props")
    if props_raw is not None and not isinstance(props_raw, dict):
        rep(f"{where}.props", "нужен объект")
        props_raw = None
    layer["props"] = _norm_fields(props_raw or {}, PROPS[ltype], rep, f"{where}.props")
    layer["keys"] = _norm_keys(raw.get("keys"), rep, f"{where}.keys")
    layer["in"] = _norm_anim(raw.get("in"), rep, f"{where}.in", layer)
    layer["out"] = _norm_anim(raw.get("out"), rep, f"{where}.out", layer)
    loop = raw.get("loop")
    if isinstance(loop, str):
        loop = {"type": loop}
    layer["loop"] = Obj(LOOP).norm(loop, rep, f"{where}.loop") if loop is not None else Obj(LOOP).fill()
    _check_required(layer, rep, where)
    return layer


def _check_required(layer: dict, rep: _Report, where: str) -> None:
    """Без чего слой ничего не покажет (не выкидываем — говорим)."""
    props = layer["props"]
    ltype = layer["type"]
    if ltype == "text" and not props["text"].strip():
        rep(f"{where}.props.text", "пустой текст — слой ничего не покажет")
    elif ltype == "image" and props["asset_id"] is None and props["src"] is None:
        rep(f"{where}.props", "у картинки нужен asset_id (файл для монтажа) или src (emoji:… / sticker:…)")
    elif ltype == "emoji" and not props["char"].strip():
        rep(f"{where}.props.char", "не указан эмодзи")
    elif ltype == "audio" and props["asset_id"] is None:
        rep(f"{where}.props.asset_id", "у звука нужен asset_id (файл для монтажа) или sfx:<пак>/<файл>")
    elif ltype == "chart" and not props["data"]:
        rep(f"{where}.props.data", "у графика нет данных")
    elif ltype == "callout" and not props["title"].strip() and not props["subtitle"].strip():
        rep(f"{where}.props", "у плашки пустые title и subtitle")


def _normalize_layers(raw: Any, rep: _Report) -> list[tuple[int, dict]]:
    """Нормализованные слои вместе с их номером во входном списке (для сообщений)."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        rep("layers", "нужен список слоёв")
        return []
    if len(raw) > MAX_LAYERS:
        rep("layers", f"не больше {MAX_LAYERS} слоёв — лишние ({len(raw) - MAX_LAYERS}) отброшены")
    taken: set[str] = set()
    out: list[tuple[int, dict]] = []
    for index, item in enumerate(raw[:MAX_LAYERS]):
        layer = _normalize_layer(item, index, taken, rep)
        if layer is not None:
            out.append((index, layer))
    return out


def _normalize_camera(raw: Any, rep: _Report) -> list[dict]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        rep("camera", "нужен список ключей камеры")
        return []
    if len(raw) > MAX_CAMERA_KEYS:
        rep("camera", f"не больше {MAX_CAMERA_KEYS} ключей — лишние отброшены")
    by_t: dict[float, dict] = {}
    for index, item in enumerate(raw[:MAX_CAMERA_KEYS]):
        where = f"camera[{index}]"
        if not isinstance(item, dict):
            rep(where, "нужен объект {t, zoom, x, y, ease?, shake?}")
            continue
        if _finite(item.get("t")) is None:
            rep(f"{where}.t", "нужно число — ключ отброшен")
            continue
        key = _norm_fields(item, CAMERA_FIELDS, rep, where)
        if item.get("ease") is not None:
            ease = _norm_ease(item["ease"], rep, f"{where}.ease")
            if ease:
                key["ease"] = ease
        if item.get("shake") not in (None, 0):
            shake = Num(0, 0, 1, 3).norm(item["shake"], rep, f"{where}.shake")
            if shake > 0:
                key["shake"] = shake
        if key["t"] in by_t:
            rep(where, f"второй ключ на t={_fmt(key['t'])} — остаётся последний")
        by_t[key["t"]] = key
    return [by_t[t] for t in sorted(by_t)]


def normalize_layers(raw: Any) -> list[dict]:
    """Слои в каноническом виде: безопасно хранить и отдавать компоновщику."""
    return [layer for _index, layer in _normalize_layers(raw, _Report(None))]


def normalize_camera(raw: Any) -> list[dict]:
    """Ключи камеры по времени; один ключ на момент."""
    return _normalize_camera(raw, _Report(None))


# ---------------------------------------------------------------- проверка


def validate_layers(
    layers: Any,
    clip_duration: float | None = None,
    *,
    asset_exists: Callable[[int], bool] | None = None,
    ref_exists: Callable[[str], bool] | None = None,
) -> list[str]:
    """Все проблемы слоёв по-человечески (пусто — всё хорошо).

    ``clip_duration`` — проверить, что слои вообще попадают в клип.
    ``asset_exists`` / ``ref_exists`` — проверить, что файлы есть (для агента).
    """
    problems: list[str] = []
    clean = _normalize_layers(layers, _Report(problems))
    for index, layer in clean:
        where = _label(index, layers[index])
        if clip_duration is not None and clip_duration > 0 and layer["start"] >= clip_duration:
            problems.append(
                f"{where}: начинается на {_fmt(layer['start'])} с — после конца клипа "
                f"({clip_duration:.2f} с), его не будет видно"
            )
        for ref in layer_asset_refs(layer):
            if isinstance(ref, int):
                if asset_exists is not None and not asset_exists(ref):
                    problems.append(f"{where}: файла для монтажа {ref} нет")
            elif ref_exists is not None and not ref_exists(ref):
                problems.append(f"{where}: {ref} не найден в пакетах")
    return problems


def validate_camera(camera: Any, clip_duration: float | None = None) -> list[str]:
    problems: list[str] = []
    clean = _normalize_camera(camera, _Report(problems))
    if clip_duration is not None and clip_duration > 0:
        for key in clean:
            if key["t"] > clip_duration + 0.05:
                problems.append(f"camera t={_fmt(key['t'])}: после конца клипа ({clip_duration:.2f} с)")
    return problems


# ---------------------------------------------------------------- ресурсы слоёв


def emoji_ref(char: str) -> str | None:
    """«🔥» → ``emoji:1f525`` — имя файла Twemoji.

    Правило Twemoji: вариационный селектор FE0F выбрасывается, если в
    последовательности нет ZWJ (200D) — иначе он часть имени.
    """
    text = (char or "").strip()
    if not text:
        return None
    if "‍" not in text:
        text = text.replace("️", "")
    code = "-".join(f"{ord(ch):x}" for ch in text)
    return f"emoji:{code}" if code else None


def _as_ref(value: Any) -> int | str | None:
    """id файла для монтажа (строка цифр или число) → int; ссылка на пакет — как есть."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    return int(text) if text.isdigit() else text


def layer_asset_refs(layer: dict) -> list[int | str]:
    """Какие файлы нужны слою: id файлов для монтажа (int) и ссылки на пакеты (str)."""
    props = layer.get("props") or {}
    ltype = layer.get("type")
    refs: list[int | str] = []
    if ltype == "image":
        ref = _as_ref(props.get("asset_id")) or _as_ref(props.get("src"))
        if ref is not None:
            refs.append(ref)
    elif ltype == "audio":
        ref = _as_ref(props.get("asset_id"))
        if ref is not None:
            refs.append(ref)
    elif ltype == "emoji":
        ref = emoji_ref(props.get("char") or "")
        if ref:
            refs.append(ref)
    elif ltype == "callout" and props.get("icon"):
        # Значок плашки рисуется Twemoji — ему нужен тот же файл, что и эмодзи.
        ref = emoji_ref(props["icon"])
        if ref:
            refs.append(ref)
    elif ltype == "captions":
        # Эмодзи над субтитрами — тоже Twemoji; без файла рисуется символом, слой не пропадает.
        for mark in props.get("emoji") or []:
            ref = emoji_ref(mark.get("char") or "") if isinstance(mark, dict) else None
            if ref and ref not in refs:
                refs.append(ref)
    return refs


def asset_key(ref: Any) -> str:
    """Ключ в assets.json компоновщика: id файла строкой («7») или сама ссылка."""
    parsed = _as_ref(ref)
    return str(parsed) if parsed is not None else ""


# ---------------------------------------------------------------- время клипа


def clip_timeline(pieces: list[tuple[float, float]], transition: Any) -> tuple[list[float], float]:
    """Где начинается каждый кусок на таймлайне готового клипа и его длина.

    Переходы съедают по своему overlap на каждой склейке — так же, как в рендере
    (``plan_joins`` с теми же дырками между кусками).
    """
    from app.transitions import normalize_transition, plan_joins

    durations = [max(0.0, float(end) - float(start)) for start, end in pieces]
    if not durations:
        return [], 0.0
    gaps: list[float | None] = []
    for (_ls, left_end), (right_start, _re) in zip(pieces, pieces[1:]):
        gap = float(right_start) - float(left_end)
        gaps.append(gap if gap >= 0 else None)
    overlaps = plan_joins(normalize_transition(transition), durations, gaps)
    starts: list[float] = []
    elapsed = 0.0
    for index, duration in enumerate(durations):
        starts.append(elapsed)
        elapsed += duration - (overlaps[index] if index < len(overlaps) else 0.0)
    return starts, round(sum(durations) - sum(overlaps), 3)


def has_captions(layers: Any) -> bool:
    """Есть ли видимый слой анимированных субтитров (тогда компоновщику нужны слова)."""
    return any(
        isinstance(layer, dict) and layer.get("type") == "captions" and not layer.get("hidden")
        for layer in layers or []
    )


def has_chat(layers: Any) -> bool:
    """Есть ли видимый слой чата (тогда компоновщику нужны сообщения и картинки эмоутов)."""
    return any(
        isinstance(layer, dict) and layer.get("type") == "chat" and not layer.get("hidden")
        for layer in layers or []
    )


def caption_words(payload: Any) -> list[dict]:
    """Слова субтитров клипа (``{word, start, end}``) → ``comp.words`` (``{t0, t1, w}``)."""
    out: list[dict] = []
    raw = payload.get("words") if isinstance(payload, dict) else None
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        t0, t1 = _finite(item.get("start")), _finite(item.get("end"))
        text = str(item.get("word") or "").strip()
        if t0 is None or t1 is None or not text:
            continue
        word = {"t0": round(max(0.0, t0), 3), "t1": round(max(t0, t1), 3), "w": text[:60]}
        speaker = str(item.get("speaker") or "").strip()[:8]
        if speaker:
            word["s"] = speaker  # кто говорит (app.ai.speakers): свой цвет и своя страница
        out.append(word)
    out.sort(key=lambda w: w["t0"])
    return out


def plan_motion(render_settings: Any) -> dict | None:
    """Что из моушна реально попадёт в рендер (скрытые слои — нет). None — ничего."""
    src = render_settings if isinstance(render_settings, dict) else {}
    layers = [layer for layer in normalize_layers(src.get("layers")) if not layer["hidden"]]
    camera = normalize_camera(src.get("camera"))
    if not layers and not camera:
        return None
    return {"layers": layers, "camera": camera}


def visual_motion(motion: dict | None) -> dict | None:
    """Только то, что рисует компоновщик: видимые слои (без звуков) и камера.

    Клип с одними звуковыми слоями картинку не трогает — основа рендерится
    по-старому (с баннером в первом проходе), звуки ложатся отдельно.
    """
    if not motion:
        return None
    layers = [layer for layer in motion.get("layers") or [] if layer.get("type") != "audio"]
    camera = list(motion.get("camera") or [])
    if not layers and not camera:
        return None
    return {"layers": layers, "camera": camera}


# ---------------------------------------------------------------- описание для UI/агента


def schema_summary() -> dict:
    """Машиночитаемое описание слоёв — для ``GET /api/motion/schema``."""
    return {
        "version": 1,
        "spec": "docs/motion/spec.md",
        "frame": {"width": 1080, "height": 1920, "units": "x/y — доли кадра, размеры — опорные px 1080×1920"},
        "limits": {
            "max_layers": MAX_LAYERS,
            "max_camera_keys": MAX_CAMERA_KEYS,
            "max_keys_per_prop": MAX_KEYS_PER_PROP,
            "max_track": MAX_TRACK,
            "min_layer_sec": MIN_LAYER_SEC,
            "default_layer_sec": DEFAULT_LAYER_SEC,
            "max_text": MAX_TEXT,
            "max_chart_items": MAX_CHART_ITEMS,
            "anim_duration": [MIN_ANIM_SEC, MAX_ANIM_SEC],
            "layer_id_pattern": LAYER_ID_RE.pattern,
            "color_pattern": COLOR_RE.pattern,
            "pack_ref_pattern": PACK_REF_RE.pattern,
        },
        "types": list(LAYER_TYPES),
        "captions": {
            "styles": list(CAPTION_STYLES),
            "default_style": DEFAULT_CAPTION_STYLE,
            "animations": list(CAPTION_ANIMATIONS),
            "default_y": DEFAULT_CAPTIONS_Y,
            "words": "comp.words = [{t0, t1, w}] — слова субтитров клипа в клиповом времени; "
                     "их подставляет сервер (рендер) и редактор (превью)",
        },
        "chat": {
            "styles": list(CHAT_STYLES),
            "messages": "comp.chat = [{t, user, color, text, fragments, badges}] — чат клипа "
                        "(GET /api/clip-plans/{id}/chat); подставляет сервер и редактор",
        },
        "easings": list(EASINGS),
        "custom_easing": "bezier(x1,y1,x2,y2) — CSS cubic-bezier, x 0..1, y -1..2",
        "default_easing": DEFAULT_EASING,
        "animations": list(ANIMATIONS),
        "text_only_animations": list(TEXT_ONLY_ANIMATIONS),
        "draw_shape_kinds": list(DRAW_SHAPE_KINDS),
        "loops": list(LOOPS),
        "blends": list(BLENDS),
        "animatable": {name: list(bounds) for name, bounds in ANIMATABLE.items()},
        "layer": {
            "id": {"type": "string", "pattern": LAYER_ID_RE.pattern, "description": "без id сервер выдаст свой"},
            "type": {"type": "enum", "options": list(LAYER_TYPES)},
            "start": {"type": "number", "min": 0, "description": "секунды готового клипа"},
            "end": {"type": "number", "description": f"> start, длина ≥ {MIN_LAYER_SEC}; нет — start + {DEFAULT_LAYER_SEC}"},
            **{name: spec.describe() for name, spec in LAYER_FIELDS.items()},
            "keys": {"type": "object", "description": "{свойство: [{t (время слоя), v, ease?}]}",
                     "props": list(ANIMATABLE)},
            "in": Obj(ANIM).describe(),
            "out": Obj(ANIM).describe(),
            "loop": Obj(LOOP).describe(),
        },
        "props": {ltype: {name: spec.describe() for name, spec in fields.items()} for ltype, fields in PROPS.items()},
        "camera": {
            **{name: spec.describe() for name, spec in CAMERA_FIELDS.items()},
            "ease": {"type": "enum", "options": list(EASINGS), "optional": True},
            "shake": {"type": "number", "min": 0, "max": 1, "optional": True},
        },
    }
