"""Фирменный стиль: нормализация и проверка (из ``app/brand_kit.py``)."""

from __future__ import annotations

import math
from typing import Any

from app import motion

SFX_POLICIES = ("none", "minimal")


LOGO_CORNERS = ("top-right", "top-left")


CTA_SEC_DEFAULT = 3.0
CTA_SEC_MIN = 1.5
CTA_SEC_MAX = 8.0


DEFAULT_COLORS = {"text": "#ffffff", "accent": "#ffd600", "plate": "#0b0d14"}

# Плашка канала: те же списки, что у слоя nameplate (app/motion.py, layers/nameplate.ts).
NAMEPLATE_PLATFORMS = motion.NAMEPLATE_PLATFORMS
NAMEPLATE_STYLES = motion.NAMEPLATE_STYLES


NAMEPLATE_POSITIONS = ("auto", "top-left", "top-right", "under-split-left", "under-split-center")
NAMEPLATE_OUTROS = ("auto", "collapse", "none")


NAMEPLATE_SIZE_MIN, NAMEPLATE_SIZE_MAX = 40.0, 160.0


DEFAULT_HOOK_STROKE = {"color": "#000000", "width": 10}


# --------------------------------------------------------------------------- #
# Нормализация: что бы ни прислали, в базе — полная и безопасная форма
# --------------------------------------------------------------------------- #


def _color(value: Any) -> str | None:
    """#rrggbb (или #rgb) → #rrggbb в нижнем регистре; прозрачность у бренда не нужна."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if motion.SHORT_COLOR_RE.match(value):
        value = "#" + "".join(ch * 2 for ch in value[1:])
    if motion.COLOR_RE.match(value) and len(value) == 7:
        return value.lower()
    return None


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value).split())[:limit] if isinstance(value, (str, int, float)) else ""


def _num(value: Any, default: float | None, lo: float, hi: float) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return round(min(hi, max(lo, number)), 3)


def _opt_id(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _stroke(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None
    color = _color(value.get("color"))
    width = _num(value.get("width"), 6.0, 0.0, 60.0)
    if not color or not width:
        return None
    return {"color": color, "width": width}


def _plate_bg(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None
    color = _color(value.get("color"))
    if not color:
        return None
    return {"color": color, "opacity": _num(value.get("opacity"), 0.6, 0.0, 1.0)}


def _normalize_caption_style(raw: Any) -> dict:
    """Стиль слоя captions + переопределения. Пустой font/color/activeColor — из бренда
    (шрифт, цвет текста, акцент); stroke/bg null — как в пресете стиля."""
    src = raw if isinstance(raw, dict) else {}
    style = src.get("style")
    return {
        "style": style if style in motion.CAPTION_STYLES else motion.DEFAULT_CAPTION_STYLE,
        "font": _text(src.get("font"), 60),
        "color": _color(src.get("color")),
        "activeColor": _color(src.get("activeColor")),
        "stroke": _stroke(src.get("stroke")),
        "bg": _plate_bg(src.get("bg")),
    }


def _normalize_hook_style(raw: Any) -> dict:
    """Заголовок-хук: шрифт (пусто — шрифт бренда), цвет (пусто — акцент), кегль (пусто —
    как поставил автомонтаж под длину текста), плашка цвета plate, обводка."""
    src = raw if isinstance(raw, dict) else {}
    # Нет ключа — обводка по умолчанию; явный null — без обводки (на плашке она не нужна).
    stroke = _stroke(src["stroke"]) if "stroke" in src else dict(DEFAULT_HOOK_STROKE)
    uppercase = src.get("uppercase", True)
    return {
        "font": _text(src.get("font"), 60),
        "color": _color(src.get("color")),
        "size": _num(src.get("size"), None, 40.0, 160.0) if src.get("size") not in (None, "", 0) else None,
        "plate": src.get("plate") is True or src.get("plate") in (1, "1", "true"),
        "stroke": stroke,
        "uppercase": uppercase is True or uppercase in (1, "1", "true"),
    }


CTA_STYLES = ("plate", "banner")


def _normalize_cta(raw: Any) -> dict:
    """Призыв в конце. ``style: "banner"`` — концовкой-баннером (app/end_screen.py: видео в размытие,
    анимированный баннер ``template`` live|schedule|neon, ``blur``); по умолчанию — плашка (поля нет)."""
    from app.end_screen import BLUR_MAX, TEMPLATE_DEFAULT, TEMPLATES

    src = raw if isinstance(raw, dict) else {}
    out = {
        "text": _text(src.get("text"), 60),
        "url_text": _text(src.get("url_text"), 60),
        "duration_sec": _num(src.get("duration_sec"), CTA_SEC_DEFAULT, CTA_SEC_MIN, CTA_SEC_MAX),
    }
    if src.get("style") == "banner":
        template = src.get("template")
        out["style"] = "banner"
        out["template"] = template if template in TEMPLATES else TEMPLATE_DEFAULT
        out["blur"] = _num(src.get("blur"), 18.0, 0.0, BLUR_MAX)
    return out


def normalize_nameplate(raw: Any) -> dict:
    """Плашка канала бренда. Пустой ник и platform none — плашки нет (по умолчанию).
    accent None — цвет площадки (у none — акцент бренда при применении)."""
    src = raw if isinstance(raw, dict) else {}
    platform, style, position = src.get("platform"), src.get("style"), src.get("position")
    handle = _text(src.get("handle"), 40)
    fade, live = src.get("fade"), src.get("live")
    anim, outro = src.get("anim"), src.get("outro")
    return {
        "platform": platform if platform in NAMEPLATE_PLATFORMS else "none",
        "handle": handle,
        "style": style if style in NAMEPLATE_STYLES else "glass",
        "accent": _color(src.get("accent")),
        "position": position if position in NAMEPLATE_POSITIONS else "auto",
        "size": _num(src.get("size"), float(motion.NAMEPLATE_SIZE), NAMEPLATE_SIZE_MIN, NAMEPLATE_SIZE_MAX),
        "fade": fade is True or fade in (1, "1", "true"),
        # Своя анимация слоя: вход от значка + редкий блик/тик (subtle), ярче (lively), стоит (none).
        "anim": anim if anim in motion.NAMEPLATE_ANIMS else "subtle",
        "live": live is True or live in (1, "1", "true"),
        # Свёртка в значок: auto — перед призывом бренда (если он есть), collapse — в конце ролика.
        "outro": outro if outro in NAMEPLATE_OUTROS else "auto",
    }


def normalize_brand(raw: Any) -> dict:
    """Бренд в полной форме (без id и дат) — так он хранится и так отдаётся API."""
    src = raw if isinstance(raw, dict) else {}
    colors_src = src.get("colors") if isinstance(src.get("colors"), dict) else {}
    colors = {k: _color(colors_src.get(k)) or v for k, v in DEFAULT_COLORS.items()}
    # Второй цвет бренда — подсветка ключевых слов субтитров (необязательный).
    if _color(colors_src.get("highlight")):
        colors["highlight"] = _color(colors_src.get("highlight"))
    corner = src.get("logo_corner")
    policy = src.get("sfx_policy")
    return {
        "name": _text(src.get("name"), 80),
        "font": _text(src.get("font"), 60),
        "colors": colors,
        "logo_asset_id": _opt_id(src.get("logo_asset_id")),
        "logo_corner": corner if corner in LOGO_CORNERS else LOGO_CORNERS[0],
        "caption_style": _normalize_caption_style(src.get("caption_style")),
        "hook_style": _normalize_hook_style(src.get("hook_style")),
        "cta": _normalize_cta(src.get("cta")),
        "music_track_id": _opt_id(src.get("music_track_id")),
        # Фон за персонажем и под видео meme — анимированный в цветах бренда (app/backgrounds.py).
        "backdrop": brand_backdrop(src.get("backdrop"), colors),
        "sfx_policy": policy if policy in SFX_POLICIES else "minimal",
        # Плашка канала (значок площадки + ник) на весь ролик; пустая — не ставится.
        "nameplate": normalize_nameplate(src.get("nameplate")),
    }


def _brand_palette(colors: dict) -> list[str]:
    return [colors["plate"], colors["accent"]] + ([colors["highlight"]] if colors.get("highlight") else [])


def brand_backdrop(raw: Any, colors: dict) -> dict:
    """Фон бренда, как он хранится: по умолчанию анимированный (аврора), цвета ``[]`` —
    «цвета бренда» (плашка, акцент, подсветка; правка цветов бренда меняет и фон).
    Свой ``{kind, colors, …}`` — как есть; заливка/градиент — только если выбраны явно.
    Старый ``{color, color2}`` без ``kind`` — тоже анимированный: его цвет — основа."""
    from app.backgrounds import normalize_background

    src = raw if isinstance(raw, dict) else {}
    if src.get("kind"):
        return normalize_background(src)
    base = _color(src.get("color"))
    custom = [base, *_brand_palette(colors)[1:]] if base and base != colors["plate"] else []
    return normalize_background({"kind": "aurora", "colors": custom})


def resolved_backdrop(brand: dict) -> dict:
    """Фон бренда для клипа: цвета «бренда» подставлены явно."""
    bg = dict(brand["backdrop"])
    if not bg.get("colors") and bg.get("kind") not in ("solid", "gradient"):
        bg["colors"] = _brand_palette(brand["colors"])
    return bg


def merge_brand(current: dict, patch: dict) -> dict:
    """Правка бренда: вложенные объекты (colors, cta, …) сливаются на один уровень,
    чтобы PATCH {"colors": {"accent": …}} не стирал остальные цвета."""
    out = dict(current)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = {**out[key], **value}
        else:
            out[key] = value
    return normalize_brand(out)


def brand_problems(store: Any, brand: dict) -> list[str]:
    """Что в бренде ссылается в пустоту: шрифт не из пака, логотип/трек удалены."""
    problems: list[str] = []
    if not brand.get("name"):
        problems.append("нужно название бренда")
    families = font_families()
    fonts = {brand.get("font"), brand["caption_style"].get("font"), brand["hook_style"].get("font")} - {""}
    if families:
        for font in sorted(f for f in fonts if f):
            if font not in families:
                problems.append(f"шрифта «{font}» нет в паке (list_fonts)")
    if brand.get("logo_asset_id"):
        try:
            asset = store.get_montage_asset(int(brand["logo_asset_id"]))
            if asset.get("kind") not in ("image", "gif"):
                problems.append(f"логотип #{brand['logo_asset_id']} — не картинка ({asset.get('kind')})")
        except KeyError:
            problems.append(f"логотипа #{brand['logo_asset_id']} нет в «Файлах для монтажа»")
    if brand.get("music_track_id"):
        try:
            store.get_audio_track(int(brand["music_track_id"]))
        except KeyError:
            problems.append(f"трека #{brand['music_track_id']} нет в фоновой музыке")
    return problems


def font_families() -> set[str]:
    """Семейства шрифтов пака (пусто — пак недоступен, шрифт тогда не проверяем)."""
    from app.agent_tools import _font_manifest

    return {str(f.get("family") or "").strip() for f in _font_manifest()} - {""}
