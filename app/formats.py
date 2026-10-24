"""Форматы вывода: 9:16 (как всегда), 1:1, 4:5 и 16:9 — один реестр на весь сервис.

Формат — настройка клипа ``render_settings.format`` (по умолчанию ``"9:16"``). Старое
поле ``aspect`` (квадрат/4:5 холстов, app/aspect.py) читается как ``format``.

У формата два размера:

* ``frame_size`` — кадр готового ролика (px);
* ``ref_size`` — опорный кадр моушна: в нём заданы px слоёв (кегль, ширина плашки,
  телефон), а ``x``/``y`` слоёв — доли кадра в любом формате. Движок умножает px слоя
  на ширину кадра / ширину опорного. Короткая сторона у всех — 1080, так что кегль 96
  на любом формате — те же 96 px. У 9:16 опорный — ровно 1080×1920: всё старое
  рисуется как раньше.

Слои клипа размечены в одном формате — ``render_settings.layers_format`` (нет — 9:16).
Если рендерится другой формат, слои и камера перекомпонуются (app/aspect.py,
``reframe_motion``) из опорного кадра ``layers_format`` в опорный кадр ``format``.

Зеркало для превью и компоновщика — frontend/src/motion/formats.ts.
"""

from __future__ import annotations

from typing import Any

DEFAULT_FORMAT = "9:16"

FORMATS: dict[str, dict] = {
    "9:16": {"label": "Вертикаль 9:16", "frame": (1080, 1920), "ref": (1080, 1920)},
    "1:1": {"label": "Квадрат 1:1", "frame": (1080, 1080), "ref": (1080, 1080)},
    "4:5": {"label": "4:5", "frame": (1080, 1350), "ref": (1080, 1350)},
    "16:9": {"label": "Горизонталь 16:9", "frame": (1920, 1080), "ref": (1920, 1080)},
}
FORMAT_IDS = tuple(FORMATS)

_ALIASES = {
    "9x16": "9:16", "vertical": "9:16", "вертикаль": "9:16", "portrait": "9:16",
    "1x1": "1:1", "square": "1:1", "квадрат": "1:1",
    "4x5": "4:5",
    "16x9": "16:9", "horizontal": "16:9", "landscape": "16:9", "горизонталь": "16:9",
}

# Исходник уже (выше) кадра горизонтального формата больше, чем во столько раз, — не
# режем его до 16:9 (от вертикали осталась бы полоска), а вписываем по высоте на размытую
# подложку (render._render_filter, «fit»). 4:3 (1.33) ещё режется: срез 25 % высоты.
FIT_SOURCE_RATIO = 0.75


def normalize_format(value: Any) -> str:
    """'9:16' | '1:1' | '4:5' | '16:9' (и синонимы square, 16x9, landscape…); пусто — 9:16.
    Другое — ValueError (для запросов: агенту нужна понятная ошибка)."""
    if value is None or value == "":
        return DEFAULT_FORMAT
    text = str(value).strip().lower().replace(" ", "")
    text = _ALIASES.get(text, text)
    if text not in FORMATS:
        raise ValueError(f"format — один из {', '.join(FORMATS)}")
    return text


def settings_format(value: Any) -> str:
    """Для настроек клипа: мусор — 9:16 (нормализация настроек не падает)."""
    try:
        return normalize_format(value)
    except ValueError:
        return DEFAULT_FORMAT


def frame_size(fmt: Any) -> tuple[int, int]:
    """Кадр готового ролика (ширина, высота), px."""
    return FORMATS[normalize_format(fmt)]["frame"]


def ref_size(fmt: Any) -> tuple[int, int]:
    """Опорный кадр моушна формата (ширина, высота): в нём px слоёв."""
    return FORMATS[normalize_format(fmt)]["ref"]


def ratio(fmt: Any) -> float:
    """Ширина / высота кадра формата."""
    w, h = frame_size(fmt)
    return w / h


def is_landscape(fmt: Any) -> bool:
    w, h = frame_size(fmt)
    return w > h


def format_of_size(width: Any, height: Any) -> str | None:
    """Формат по размеру кадра (точное совпадение с реестром); нет — None."""
    try:
        size = (int(width), int(height))
    except (TypeError, ValueError):
        return None
    return next((fmt for fmt, info in FORMATS.items() if info["frame"] == size), None)


def settings_render_format(render: dict | None) -> str:
    """Формат клипа из его render_settings: ``format``, у старых — ``aspect``; нет — 9:16."""
    render = render if isinstance(render, dict) else {}
    value = render.get("format")
    if value in (None, ""):
        value = render.get("aspect")
    return settings_format(value)


def settings_layers_format(render: dict | None) -> str:
    """В каком формате размечены слои клипа (``layers_format``); нет — 9:16."""
    render = render if isinstance(render, dict) else {}
    return settings_format(render.get("layers_format"))


def comp_ref(fmt: Any) -> dict:
    """Поля опорного кадра для comp.json компоновщика: только если ширина опорного не
    1080 (16:9). У 9:16, 1:1 и 4:5 движок считает px слоёв от 1080, как всегда, — их
    comp.json и кадры остаются прежними."""
    w, h = ref_size(fmt)
    return {} if w == 1080 else {"refWidth": w, "refHeight": h}


def source_strategy(fmt: Any, source: dict | None, crop: dict | None = None) -> str:
    """Как основа ложится в кадр формата: ``"cover"`` — умный кроп окном пропорции кадра
    (с фокусом x(t), как всегда у 9:16) или ``"fit"`` — исходник целиком по высоте на
    размытой подложке (вертикальный/квадратный исходник в 16:9).

    Только горизонтальный формат и только исходник заметно уже кадра дают ``fit``:
    у 9:16, 1:1, 4:5 — всегда ``cover`` (прежний рендер)."""
    if not is_landscape(fmt):
        return "cover"
    source = source or {}
    sw, sh = float(source.get("width") or 0), float(source.get("height") or 0)
    if sw <= 0 or sh <= 0:
        return "cover"
    crop = crop if isinstance(crop, dict) else source.get("content_crop") or None
    if crop:
        sw *= float(crop.get("w") or 1.0)
        sh *= float(crop.get("h") or 1.0)
    if sh <= 0:
        return "cover"
    return "fit" if (sw / sh) < ratio(fmt) * FIT_SOURCE_RATIO else "cover"


def describe(fmt: Any) -> dict:
    """Формат для API/агента: id, подпись, кадр и опорный кадр."""
    fmt = normalize_format(fmt)
    info = FORMATS[fmt]
    return {"format": fmt, "label": info["label"], "frame": list(info["frame"]), "ref": list(info["ref"])}
