"""Starter pack seeded on startup so a fresh install already has usable looks and
subtitle styles. Idempotent by label — only missing items are created, so user
edits are never clobbered and new pack items appear on upgrade.
"""

from __future__ import annotations

import logging

_log = logging.getLogger(__name__)

# Версия пака: растёт, когда в пак добавляют вещи (17.11 — образы и стили субтитров пяти карточек стилей ролика,
# app/style_cards.json). Сеется по-прежнему по подписи: недостающее создаётся, правки владельца не трогаются, так что
# и старая установка получает новое при запуске; версия записывается в настройку DEFAULT_PACK_KEY — видно, до
# какой версии установка уже дошла.
DEFAULT_PACK_VERSION = 2
DEFAULT_PACK_KEY = "default_pack_version"

# FFmpeg render presets ("looks"/filters). Fields map to create_ffmpeg_preset.
DEFAULT_FFMPEG_PRESETS = [
    {"label": "Чистый 9:16", "scale_mode": "cover", "color_style": "none"},
    {"label": "Кинематограф", "scale_mode": "cover", "color_style": "cinematic", "color_strength": 1.0, "vignette": 0.3},
    {"label": "Тёплый", "scale_mode": "cover", "color_style": "warm", "color_strength": 1.0},
    {"label": "Холодный", "scale_mode": "cover", "color_style": "cold", "color_strength": 1.0},
    {"label": "Сочный", "scale_mode": "cover", "color_style": "vibrant", "color_strength": 1.1},
    {"label": "Винтаж", "scale_mode": "cover", "color_style": "vintage", "color_strength": 1.0, "grain": 0.3},
    {"label": "Нуар", "scale_mode": "cover", "color_style": "noir", "color_strength": 1.0, "vignette": 0.4},
    {"label": "Плёнка (недоэдит)", "scale_mode": "cover", "color_style": "vintage", "color_strength": 1.2, "grain": 0.55, "vignette": 0.45},
    {"label": "Размытый фон", "scale_mode": "blur_background", "color_style": "none"},
    # Пак v2: образы карточек стилей (app/style_cards.json → apply.render_look). Цвет — мягко: главное в стиле
    # делают фон, шрифт и эффект слоем, образ только подравнивает видео под него.
    {"label": "Стиль: Новости", "scale_mode": "cover", "color_style": "vibrant", "color_strength": 0.5, "vignette": 0.15},
    {"label": "Стиль: Бумага", "scale_mode": "cover", "color_style": "warm", "color_strength": 0.5, "grain": 0.1},
    {"label": "Стиль: Рисограф", "scale_mode": "cover", "color_style": "vintage", "color_strength": 0.6, "grain": 0.2},
    {"label": "Стиль: 1-бит", "scale_mode": "cover", "color_style": "noir", "color_strength": 1.4},
    {"label": "Стиль: Чертёж", "scale_mode": "cover", "color_style": "cold", "color_strength": 0.8},
]

# Subtitle styles. Fields map to create_subtitle_profile.
DEFAULT_SUBTITLE_PROFILES = [
    {
        "label": "Классика",
        "font_family": "Arial", "font_size": 64,
        "primary_color": "#FFFFFF", "active_word_color": "#FACC15",
        "outline_color": "#111827", "back_color": "#000000",
        "alignment": 2, "margin_v": 160, "max_words_per_line": 5,
        "uppercase": 0, "outline_width": 5, "shadow": 1,
    },
    {
        "label": "TikTok жёлтый",
        "font_family": "Arial", "font_size": 74,
        "primary_color": "#FFFFFF", "active_word_color": "#FFE000",
        "outline_color": "#000000", "back_color": "#000000",
        "alignment": 2, "margin_v": 230, "max_words_per_line": 4,
        "uppercase": 1, "outline_width": 6, "shadow": 1,
    },
    {
        "label": "Караоке крупный",
        "font_family": "Arial", "font_size": 86,
        "primary_color": "#FFFFFF", "active_word_color": "#22D3EE",
        "outline_color": "#0B1220", "back_color": "#000000",
        "alignment": 2, "margin_v": 210, "max_words_per_line": 3,
        "uppercase": 0, "outline_width": 6, "shadow": 2,
    },
    {
        "label": "Минимал",
        "font_family": "Arial", "font_size": 54,
        "primary_color": "#FFFFFF", "active_word_color": "#FFFFFF",
        "outline_color": "#000000", "back_color": "#000000",
        "alignment": 2, "margin_v": 150, "max_words_per_line": 6,
        "uppercase": 0, "outline_width": 3, "shadow": 0,
    },
    {
        "label": "Неон",
        "font_family": "Arial", "font_size": 76,
        "primary_color": "#F5F3FF", "active_word_color": "#A78BFF",
        "outline_color": "#2E1065", "back_color": "#000000",
        "alignment": 2, "margin_v": 210, "max_words_per_line": 4,
        "uppercase": 1, "outline_width": 6, "shadow": 2,
    },
    # Пак v2: субтитры дорожкой карточек стилей (app/style_cards.json → apply.subtitle_profile); шрифты — из пака.
    {
        "label": "Стиль: Новости",
        "font_family": "Oswald", "font_size": 80,
        "primary_color": "#FFFFFF", "active_word_color": "#E11D2E",
        "outline_color": "#0B1020", "back_color": "#000000",
        "alignment": 2, "margin_v": 230, "max_words_per_line": 3,
        "uppercase": 1, "outline_width": 5, "shadow": 0,
    },
    {
        "label": "Стиль: Маркер",
        "font_family": "Caveat", "font_size": 92,
        "primary_color": "#1F1B16", "active_word_color": "#E94F37",
        "outline_color": "#FFF6D6", "back_color": "#000000",
        "alignment": 2, "margin_v": 220, "max_words_per_line": 4,
        "uppercase": 0, "outline_width": 3, "shadow": 0,
    },
    {
        "label": "Стиль: Рисограф",
        "font_family": "Rubik Mono One", "font_size": 70,
        "primary_color": "#FF4F8B", "active_word_color": "#2546C2",
        "outline_color": "#F6EEDF", "back_color": "#000000",
        "alignment": 2, "margin_v": 220, "max_words_per_line": 3,
        "uppercase": 1, "outline_width": 4, "shadow": 0,
    },
    {
        "label": "Стиль: 1-бит",
        "font_family": "Press Start 2P", "font_size": 48,
        "primary_color": "#FFFFFF", "active_word_color": "#FFFFFF",
        "outline_color": "#000000", "back_color": "#000000",
        "alignment": 2, "margin_v": 220, "max_words_per_line": 3,
        "uppercase": 1, "outline_width": 6, "shadow": 0,
    },
    {
        "label": "Стиль: Чертёж",
        "font_family": "Roboto Condensed", "font_size": 70,
        "primary_color": "#E8F1FF", "active_word_color": "#FFD166",
        "outline_color": "#0E3A78", "back_color": "#000000",
        "alignment": 2, "margin_v": 220, "max_words_per_line": 4,
        "uppercase": 1, "outline_width": 4, "shadow": 0,
    },
]


def seed_music(store) -> None:
    """Фоновая музыка из пака (tools/fetch_packs.fetch_music) → дорожки сервиса, если их нет."""
    import json
    import shutil
    from pathlib import Path

    from app.settings import settings

    meta = Path(settings.motion_packs_dir) / "music" / "items.json"
    if not meta.exists():
        return
    try:
        items = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    have = {t.get("label") for t in store.list_audio_tracks()}
    for item in items:
        label = f"Фон: {item['name']}"
        if label in have:
            continue
        src = meta.parent / item["file"]
        dest = Path(settings.audio_dir) / f"pack-{item['file']}"
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
            store.create_audio_track(
                label, dest, original_filename=item["file"], mime_type="audio/mpeg",
                duration_sec=float(item.get("duration") or 0), volume=0.5,
            )
        except Exception as exc:  # noqa: BLE001 - never let seeding break startup
            _log.warning("default pack: music %r not seeded: %s", label, exc)


# Пример бренда: реклама магазина VTuber-моделей. Без логотипа и трека — их владелец
# выберет из своей библиотеки; шрифт из пака (vendor/fonts).
DEFAULT_BRAND = {
    "name": "VTubika",
    "font": "Montserrat",
    "colors": {"text": "#ffffff", "accent": "#ff6b2c", "plate": "#0b0d14"},
    "logo_asset_id": None,
    "caption_style": {"style": "hormozi", "stroke": {"color": "#0b0d14", "width": 8}},
    "hook_style": {"plate": True, "stroke": None, "uppercase": True},
    "cta": {"text": "Каталог в профиле", "url_text": "vtubika.store", "duration_sec": 3.0},
    "music_track_id": None,
    # Анимированный фон в цветах бренда (app/backgrounds.py); цвета пусто — плашка + акцент.
    "backdrop": {"kind": "aurora", "colors": []},
    "sfx_policy": "minimal",
}
BRAND_SEED_KEY = "brand_seed_v1"


def seed_default_brand(store) -> None:
    """Бренд-пример — только на чистой установке (брендов нет) и один раз: удалённый
    владельцем пример при перезапуске не воскресает."""
    try:
        if store.get_app_setting_value(BRAND_SEED_KEY):
            return
        if not store.list_brands():
            spec = {k: v for k, v in DEFAULT_BRAND.items() if k != "name"}
            try:
                store.create_brand(DEFAULT_BRAND["name"], **spec)
            except ValueError:  # шрифта нет в паке — пример без своего шрифта
                store.create_brand(DEFAULT_BRAND["name"], **{**spec, "font": ""})
        store.set_app_setting(BRAND_SEED_KEY, True)
    except Exception as exc:  # noqa: BLE001 - never let seeding break startup
        _log.warning("default pack: brand %r not seeded: %s", DEFAULT_BRAND["name"], exc)


def seed_default_pack(store) -> None:
    """Create any missing default render presets and subtitle profiles."""
    have_presets = {p.get("label") for p in store.list_ffmpeg_presets()}
    for spec in DEFAULT_FFMPEG_PRESETS:
        if spec["label"] in have_presets:
            continue
        try:
            store.create_ffmpeg_preset(spec["label"], **{k: v for k, v in spec.items() if k != "label"})
        except Exception as exc:  # noqa: BLE001 - never let seeding break startup
            _log.warning("default pack: render look %r not seeded: %s", spec["label"], exc)

    have_subs = {s.get("label") for s in store.list_subtitle_profiles()}
    for spec in DEFAULT_SUBTITLE_PROFILES:
        if spec["label"] in have_subs:
            continue
        try:
            store.create_subtitle_profile(spec["label"], **{k: v for k, v in spec.items() if k != "label"})
        except Exception as exc:  # noqa: BLE001 - never let seeding break startup
            # Раньше ошибка глоталась молча — и на чистой установке стилей не было вовсе.
            _log.warning("default pack: subtitle style %r not seeded: %s", spec["label"], exc)
    try:
        if int(store.get_app_setting_value(DEFAULT_PACK_KEY) or 0) < DEFAULT_PACK_VERSION:
            store.set_app_setting(DEFAULT_PACK_KEY, DEFAULT_PACK_VERSION)
    except Exception as exc:  # noqa: BLE001 - never let seeding break startup
        _log.warning("default pack: version not recorded: %s", exc)
