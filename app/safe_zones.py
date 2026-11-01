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


# Пороги линта по формату/площадке (agent_tools.lint_layers, promo_lint.final_text): кегль в px
# опорного кадра формата (короткая сторона — 1080 у всех). 9:16, 1:1, 4:5 смотрят с телефона:
# мельче 44 px не прочитать (лучше ≥ 56), больше 3 слоёв графики разом — каша. 16:9 — YouTube /
# презентация на мониторе или ТВ: кадр шире, текст читается с экрана — 28 px (≈ 2.6 % высоты,
# кегль субтитров плеера) ещё читается (лучше ≥ 36), а слайду с титром, подписью и двумя медиа
# 5 слоёв графики — норма.
LINT_LIMITS: dict[str, dict] = {
    "9:16": {"min_font": 44, "good_font": 56, "max_graphics": 3, "screen": "телефона",
             "final_font": 44, "final_good": "56–64"},
    "1:1": {"min_font": 44, "good_font": 56, "max_graphics": 3, "screen": "телефона",
            "final_font": 44, "final_good": "56–64"},
    "4:5": {"min_font": 44, "good_font": 56, "max_graphics": 3, "screen": "телефона",
            "final_font": 44, "final_good": "56–64"},
    "16:9": {"min_font": 28, "good_font": 36, "max_graphics": 5, "screen": "экрана YouTube/презентации",
             "final_font": 32, "final_good": "40–56"},
}


def lint_limits(fmt: Any = None) -> dict:
    """Пороги линта формата (мусор и пусто — 9:16)."""
    return LINT_LIMITS[settings_format(fmt)]


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


# Интерфейс площадок поверх вертикального ролика (9:16, опорный кадр 1080×1920) — для линта
# готового клипа (app/quality_lint.py, ``lint_clip``). Общая зона ``ZONES["9:16"]`` выше — одна на
# все три площадки; здесь — где именно у каждой лежат кнопки и подпись, чтобы сказать «под лайками
# TikTok» или «в подписи Reels», а не просто «у края».
#
# Источник — ПРИБЛИЖЕНИЕ: замеры скриншотов приложений (Android/iOS, 2025–2026, телефон 19.5:9,
# ролик вписан по ширине) и шаблоны «safe zone» площадок для рекламы. Интерфейс меняется от
# версии к версии и зависит от длины подписи (1–2 строки описания) — поэтому зоны с запасом
# в 10–20 px, а не по пикселю. Доли кадра (x — слева направо, y — сверху вниз):
#
# * ``right`` — колонка кнопок справа (аватар/подписка, лайк, комментарии, избранное, репост,
#   диск звука): от ``x0`` до правого края, по высоте ``y0``..``y1``;
# * ``bottom`` — подпись внизу (ник, описание 1–2 строки, звук/музыка): от ``y0`` до низа по всей
#   ширине; ``captions_y0`` — граница для субтитров (им можно чуть ниже титров: подпись площадки
#   мелкая и полупрозрачная, строка субтитров по центру ложится над её первой строкой);
# * ``top`` — шапка (вкладки «Подписки | Рекомендации», поиск, камера): от верха до ``y1``.
#
# Reels: подпись выше, чем у TikTok и Shorts (ник с кнопкой «Подписаться», описание, звук и метки
# людей — ≈ 460 px). Рекламные Reels (кнопка CTA) Meta просит держать свободными нижние 35 %
# кадра — это ещё выше; для обычных роликов берём органическую раскладку.
PLATFORM_UI: dict[str, dict] = {
    "tiktok": {
        "name": "TikTok",
        "right": {"x0": 0.875, "y0": 0.42, "y1": 0.87},  # иконки ~96 px по центру x≈1010; диск звука у низа
        "bottom": {"y0": 0.80, "captions_y0": 0.82},  # ник, описание, музыка ≈ 385 px
        "top": {"y1": 0.075},  # вкладки, LIVE, поиск ≈ 145 px
    },
    "reels": {
        "name": "Instagram Reels",
        "right": {"x0": 0.885, "y0": 0.50, "y1": 0.92},  # лайк, комменты, репост, ещё, диск звука
        "bottom": {"y0": 0.76, "captions_y0": 0.79},  # ник + «Подписаться», описание, звук ≈ 460 px
        "top": {"y1": 0.07},  # «Reels», камера ≈ 135 px
    },
    "shorts": {
        "name": "YouTube Shorts",
        "right": {"x0": 0.875, "y0": 0.45, "y1": 0.88},  # лайк, дизлайк, комменты, репост, ремикс, звук
        "bottom": {"y0": 0.80, "captions_y0": 0.82},  # название, канал + «Подписаться», звук ≈ 385 px
        "top": {"y1": 0.07},  # поиск, меню ≈ 135 px
    },
}
# Имена площадок публикации (autopilot.PLATFORMS) → раскладка интерфейса вертикального ролика.
PLATFORM_ALIASES = {"tiktok": "tiktok", "tt": "tiktok", "instagram": "reels", "reels": "reels", "ig": "reels",
                    "youtube": "shorts", "shorts": "shorts", "yt": "shorts"}


def platform_ui(names: Any = None) -> dict[str, dict]:
    """Раскладки интерфейса площадок: ``names`` — tiktok/reels/shorts (или youtube/instagram,
    строкой через запятую или списком); пусто или ничего не узнали — все три."""
    if isinstance(names, str):
        names = names.replace(";", ",").split(",")
    picked: list[str] = []
    for raw in names if isinstance(names, (list, tuple, set)) else []:
        key = PLATFORM_ALIASES.get(str(raw).strip().lower())
        if key and key not in picked:
            picked.append(key)
    return {key: PLATFORM_UI[key] for key in (picked or list(PLATFORM_UI))}
