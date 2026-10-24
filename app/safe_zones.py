"""Безопасные зоны кадра по площадкам — одна таблица для линта слоёв, субтитров и подсказок агенту.

Зона — доли кадра формата (0..1): текст и важное держим внутри ``top``..``bottom`` по высоте и
``left``..``right`` по ширине. Что закрывает края:

* **9:16** — YouTube Shorts, Instagram Reels, TikTok: сверху шапка площадки (10 %), снизу подпись,
  кнопки и прогресс-бар (20 %). По бокам текст не проверяем (как всегда). Субтитры-слой
  (``captions``) не опускается ниже 0.82 кадра.
* **1:1 и 4:5** — лента Instagram / Telegram: интерфейса поверх кадра нет, но превью режут углы
  (скругление, обрезка сетки профиля) — поля 5 % со всех сторон.
* **16:9** — YouTube: поля 5 % со всех сторон (title safe телевизоров и плеера); сверху и снизу
  интерфейса нет — плеер прячет его, пока ролик идёт. В правом нижнем углу — водяной знак канала
  (если включён в YouTube Studio): ``watermark``, туда титры не ставим.

Зеркало для превью и компоновщика — ``SAFE_ZONES`` в frontend/src/motion/formats.ts.
"""

from __future__ import annotations

from typing import Any

from app.formats import DEFAULT_FORMAT, settings_format

ZONES: dict[str, dict] = {
    "9:16": {
        "platforms": "YouTube Shorts / Instagram Reels / TikTok",
        "top": 0.10, "bottom": 0.80, "left": 0.0, "right": 1.0,
        "captions_bottom": 0.82, "watermark": None,
        "note": "сверху шапка площадки, снизу подпись, кнопки и прогресс-бар",
        "top_reason": "там шапка площадки",
        "bottom_reason": "там подпись и кнопки площадки",
    },
    "1:1": {
        "platforms": "лента Instagram / Telegram",
        "top": 0.05, "bottom": 0.95, "left": 0.05, "right": 0.95,
        "captions_bottom": 0.94, "watermark": None,
        "note": "интерфейса поверх кадра нет; превью режут углы — поля 5 %",
        "top_reason": "у края кадра превью ленты режет текст",
        "bottom_reason": "у края кадра превью ленты режет текст",
    },
    "4:5": {
        "platforms": "лента Instagram / Telegram",
        "top": 0.05, "bottom": 0.95, "left": 0.05, "right": 0.95,
        "captions_bottom": 0.94, "watermark": None,
        "note": "интерфейса поверх кадра нет; превью режут углы — поля 5 %",
        "top_reason": "у края кадра превью ленты режет текст",
        "bottom_reason": "у края кадра превью ленты режет текст",
    },
    "16:9": {
        "platforms": "YouTube",
        "top": 0.05, "bottom": 0.95, "left": 0.05, "right": 0.95,
        "captions_bottom": 0.93,
        # Водяной знак канала (YouTube Studio → Брендинг): квадрат в правом нижнем углу.
        "watermark": {"x0": 0.86, "y0": 0.78, "x1": 0.985, "y1": 0.97},
        "note": "поля 5 % со всех сторон; интерфейса сверху и снизу нет; в правом нижнем углу — "
                "водяной знак канала (если включён)",
        "top_reason": "за полями 5 % плеер и ТВ режут край",
        "bottom_reason": "за полями 5 % плеер и ТВ режут край",
    },
}


def safe_zone(fmt: Any = None) -> dict:
    """Зона формата (мусор и пусто — 9:16)."""
    return ZONES[settings_format(fmt)]


def checks_sides(fmt: Any = None) -> bool:
    """Проверять ли текст у левого/правого края: у 9:16 — нет (как всегда)."""
    zone = safe_zone(fmt)
    return zone["left"] > 0.0 or zone["right"] < 1.0


def describe(fmt: Any = None) -> dict:
    """Зона для ответов инструментов (``frame.safe_zone``): доли кадра и площадки."""
    zone = safe_zone(fmt)
    out = {"top": zone["top"], "bottom": zone["bottom"], "platforms": zone["platforms"], "note": zone["note"]}
    if settings_format(fmt) != DEFAULT_FORMAT:
        out.update(left=zone["left"], right=zone["right"], captions_bottom=zone["captions_bottom"])
    if zone["watermark"]:
        out["watermark"] = dict(zone["watermark"])
    return out


def in_watermark(fmt: Any, x0: float, y0: float, x1: float, y1: float) -> bool:
    """Прямоугольник (доли кадра) заходит в угол водяного знака площадки."""
    mark = safe_zone(fmt)["watermark"]
    if not mark:
        return False
    return x1 > mark["x0"] and x0 < mark["x1"] and y1 > mark["y0"] and y0 < mark["y1"]
