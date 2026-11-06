"""Моушн-слои: описатели полей — что поле принимает и как чинит значение (из ``app/motion.py``)."""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from typing import Any

from app.motion_limits import (
    MAX_TRACK, COLOR_RE, SHORT_COLOR_RE, PACK_REF_RE, STATUS_BAR_AUTO, STATUS_BAR_MAX, VOICE_SPEECH, VOICE_AUTO,
    VOICE_THRESHOLD_DEFAULT
)

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
    nullable: bool = False

    def norm(self, value: Any, rep: _Report, where: str) -> str | None:
        if value is None and self.nullable:
            return None
        if value in self.options:
            return str(value)
        rep(where, f"{value!r} не из списка: {', '.join(self.options)}")
        return self.default

    def fill(self) -> str:
        return self.default

    def describe(self) -> dict:
        out = {"type": "enum", "default": self.default, "options": list(self.options)}
        if self.nullable:
            out["nullable"] = True
        return out


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


@dataclass(frozen=True)
class StatusBar:
    """Строка статуса телефона: true/"auto" — 0.14, число — доля ширины экрана 0..0.4, false — 0 (нет)."""

    def norm(self, value: Any, rep: _Report, where: str) -> float:
        if value is True or value == "auto":
            return STATUS_BAR_AUTO
        if value is None or value is False:
            return 0
        return Num(STATUS_BAR_AUTO, 0, STATUS_BAR_MAX, 3).norm(value, rep, where)

    def fill(self) -> float:
        return 0

    def describe(self) -> dict:
        return {"type": "number", "default": 0, "min": 0, "max": STATUS_BAR_MAX,
                "description": f"доля ширины экрана; true или \"auto\" — {STATUS_BAR_AUTO}"}


@dataclass(frozen=True)
class OnOff:
    """Объект-настройка с краткой записью: true — с умолчаниями, false/null — выключено."""

    spec: Any

    def norm(self, value: Any, rep: _Report, where: str) -> dict | None:
        if value is True:
            return self.spec.norm({}, rep, where)
        if value is False:
            return None
        return self.spec.norm(value, rep, where)

    def fill(self) -> None:
        return None

    def describe(self) -> dict:
        return {**self.spec.describe(), "shorthand": "true — с умолчаниями"}


@dataclass(frozen=True)
class VoiceTrack:
    """Дорожка голоса PNGtuber: номер звуковой дорожки источника 0..7 или "speech" — дорожки
    распознавания речи источника (speech_tracks; не заданы — смесь рендера)."""

    def norm(self, value: Any, rep: _Report, where: str) -> int | str:
        if value is None or (isinstance(value, str) and value.strip() == VOICE_SPEECH):
            return VOICE_SPEECH
        number = _finite(value.strip() if isinstance(value, str) else value)
        if number is not None and number.is_integer() and 0 <= number <= MAX_TRACK:
            return int(number)
        rep(where, f"{value!r} — нужен номер дорожки 0..{MAX_TRACK} или \"{VOICE_SPEECH}\"")
        return VOICE_SPEECH

    def fill(self) -> str:
        return VOICE_SPEECH

    def describe(self) -> dict:
        return {"type": "track", "default": VOICE_SPEECH, "min": 0, "max": MAX_TRACK,
                "description": f"номер звуковой дорожки источника или \"{VOICE_SPEECH}\" (speech_tracks)"}


@dataclass(frozen=True)
class DbOrAuto:
    """Порог в дБ или "auto" — по шуму дорожки (app/pngtuber.auto_threshold)."""

    default: float
    lo: float
    hi: float

    def norm(self, value: Any, rep: _Report, where: str) -> float | str:
        if value == VOICE_AUTO:
            return VOICE_AUTO
        return Num(self.default, self.lo, self.hi, 1).norm(value, rep, where)

    def fill(self) -> float:
        return self.default

    def describe(self) -> dict:
        return {"type": "number", "default": VOICE_THRESHOLD_DEFAULT, "min": self.lo, "max": self.hi,
                "description": f"дБ или \"{VOICE_AUTO}\" (по умолчанию) — по шуму дорожки"}


@dataclass(frozen=True)
class FxChain:
    """Цепочка звуковых эффектов (app/audio_fx.py): ``[{fx, …}]`` или имя пресета."""

    def norm(self, value: Any, rep: _Report, where: str) -> list[dict]:
        from app.audio_fx import normalize_chain

        problems: list[str] = []
        chain = normalize_chain(value, problems)
        for problem in problems:
            rep(where, problem)
        return chain

    def fill(self) -> list:
        return []

    def describe(self) -> dict:
        return {"type": "fx_chain", "default": [],
                "description": "цепочка звуковых эффектов [{fx, …}] или имя пресета — audio_fx_reference"}


def _norm_fields(src: dict, fields: dict, rep: _Report, where: str) -> dict:
    """Нормализовать известные поля; отсутствующие — умолчанием, лишние — выбросить."""
    out: dict = {}
    for name, spec in fields.items():
        if name in src:
            out[name] = spec.norm(src[name], rep, f"{where}.{name}")
        elif not isinstance(spec, Opt):
            out[name] = copy.deepcopy(spec.fill())
    return out
