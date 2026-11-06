"""Рендер: какой пресет получает кусок. Кадр и кадрирование, ключ, раскладка, формат, разовые настройки
(``app/render.py``)."""

from __future__ import annotations

from app.backgrounds import background_zones
from app.focus_presets import get_focus_preset
from app.render_filters import _video_style_filter
from app.render_music import MAX_MUSIC_START_SEC
from app.store import AppStore
from app.stream_layout import gameplay_height, layout_active, normalize_layout
from app.video_crop import build_reframe_x_expr


def _source_gaps(segments: list[dict]) -> list[float | None]:
    """Сколько секунд исходника выброшено между соседними кусками.

    Маленькая дырка — это jump-cut внутри одной сцены, и склейка должна быть
    встык: наплыв посреди фразы читается как брак. Куски могут идти и не по
    порядку (хук вперёд) — тогда дырки нет, это смена сцены.
    """

    gaps: list[float | None] = []
    for left, right in zip(segments, segments[1:]):
        gap = float(right["start_sec"]) - float(left["end_sec"])
        gaps.append(gap if gap >= 0 else None)
    return gaps


REFRAME_MAX_SMOOTH_SEC = 0.45


def _segment_reframe_x(segment: dict, preset: dict, source: dict) -> str | None:
    """Build a smoothed crop-x expression for a segment, or None when smart
    reframing is off, there's no focus track, or the source has no horizontal
    slack (already ≤ the target aspect).

    The focus track is the single source of truth for framing — from autofocus
    or the user's manual keyframes / drag. A one-shot manual frame is written as
    a constant track (see the candidates UI), so it never fights the keyframes.
    """
    if not preset.get("smart_reframe"):
        return None
    sw = float(source.get("width") or 0)
    sh = float(source.get("height") or 0)
    if sw <= 0 or sh <= 0:
        return None
    crop = source.get("content_crop") or None
    eff_w = sw * (float(crop["w"]) if crop else 1.0)
    eff_h = sh * (float(crop["h"]) if crop else 1.0)
    out_w = int(preset.get("output_width") or 1080)
    out_h = int(preset.get("output_height") or 1920)
    # В split окно геймплея ниже кадра (W×(1-ratio)·H) — шире по пропорции, но
    # ширина кропа та же out_w, так что выражение x(t) строится как обычно.
    out_h = gameplay_height(preset.get("layout"), out_w, out_h)
    if eff_h <= 0 or out_h <= 0 or (eff_w / eff_h) <= (out_w / out_h) + 1e-3:
        return None
    duration = float(segment.get("end_sec", 0)) - float(segment.get("start_sec", 0))

    def remap_x(fx: float) -> float:
        if crop:
            fx = (fx - float(crop["x"])) / (float(crop["w"]) or 1.0)
        return min(1.0, max(0.0, fx))

    focus = segment.get("focus") or []
    if not focus:
        return None
    remapped: list[dict] = []
    for point in focus:
        try:
            fx = float(point["x"])
            t = float(point["t"])
        except (KeyError, TypeError, ValueError):
            continue
        remapped.append({"t": t, "x": remap_x(fx), "cut": bool(point.get("cut"))})
    # Movement feel (settle time / rubber / deadzone) comes from the source's
    # autofocus preset so an interview glides and an action clip snaps.
    cfg = get_focus_preset(source.get("focus_preset"))
    return build_reframe_x_expr(
        remapped,
        duration,
        out_w,
        # Владелец: «фокус будто бы надо делать быстрее но плавно» — пресеты держали
        # 0.6–1.6 с на подход, переход на говорящего тянулся секунды.
        smooth_time=min(float(cfg["smooth_time"]), REFRAME_MAX_SMOOTH_SEC),
        rubber=float(cfg["rubber"]),
        deadzone=float(cfg["deadzone"]),
    )


def _preset_for_segment(preset: dict, segment: dict) -> dict:
    """Пресет основы для одного куска: у куска с ``fit`` (двое по краям широкого плана)
    кадр «вписан шире окна» — доля ``fit.w`` ширины исходника, сверху и снизу фон.
    Раскладка клипа (split/pip/meme) на этот кусок не действует."""
    from app.piece_color import normalize_fix
    from app.video_crop import normalize_fit

    fix = normalize_fix(segment.get("color_fix"))
    if fix:
        # Цветокоррекция куска (app/piece_color.py) — до лука пресета, в _video_style_filter.
        preset = {**preset, "piece_color": fix}
    layout = preset.get("layout")
    if isinstance(layout, dict) and layout.get("mode") == "avatar_full" and layout.get("cams"):
        # Аватар в разных местах по ходу клипа (layout.cams, секунды исходника): основа части —
        # рамкой, что занимает её большую часть, другие рамки — по своим секундам части.
        from app.stream_layout import avatar_cam_spans, avatar_freezes

        main, spans = avatar_cam_spans(layout, float(segment["start_sec"]), float(segment["end_sec"]))
        preset = {**preset, "layout": {**layout, **main}, "avatar_spans": spans}
        freezes = avatar_freezes(layout, float(segment["start_sec"]), float(segment["end_sec"]))
        if freezes:
            # Наплыв смены сцены — стоп-кадром старой сцены, без кадров с чужой (QA 5, клип 897).
            preset["avatar_freezes"] = freezes
    fit = normalize_fit(segment.get("fit"))
    if not fit or preset.get("landscape"):
        # В горизонтальном кадре (16:9) широкий план и так виден целиком — «шире окна» не нужно.
        return preset
    return {**preset, "layout": None, "piece_fit": fit}


def _needs_sar_fix(parts: list[dict]) -> bool:
    """Куски клипа надо приводить к SAR 1:1: среди них есть «шире окна» (``fit``) — его
    основа другая, и SAR обычного куска (10240:10239 от scale) с ним не склеивался
    («concat … SAR 10240:10239»). Без ``fit`` рендер прежний (эталоны 9:16)."""
    from app.video_crop import normalize_fit

    return len(parts) > 1 and any(normalize_fit(part.get("fit")) for part in parts)


def _with_background_zones(
    motion: dict | None, preset: dict, parts: list[dict], overlaps: list[float], source: dict
) -> dict | None:
    """Зоны анимированного фона основы (meme, кусок «шире окна») — в моушн для
    компоновщика (app/backgrounds.py). С хромакеем фон один на весь кадр (backdrop)."""
    if not motion or motion.get("key"):
        return motion
    zones = background_zones(
        preset.get("layout"), parts, overlaps, source, source.get("content_crop"),
        int(preset.get("output_width") or 1080), int(preset.get("output_height") or 1920),
    )
    return {**motion, "backgrounds": zones} if zones else motion


KEY_EDGE_INSET = 0.006  # доля кадра с каждого края


def _base_crop(source: dict, motion: dict | None) -> dict | None:
    """Обрезка полей источника; на зелёнке — ещё и тонкая рамка внутрь от неё.

    Края зелёнки после кодека темнее (первая строка — 0,72,0 вместо 0,254,0), у
    записи бывают и чёрные полосы: ключ их не берёт, и над персонажем остаётся
    тёмная линия. Поэтому с ключом рамка срезается всегда, и поверх полос тоже.
    """
    crop = source.get("content_crop")
    if not (motion or {}).get("key"):
        return crop
    c = crop or {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
    dx = c["w"] * KEY_EDGE_INSET
    dy = c["h"] * KEY_EDGE_INSET
    return {"x": c["x"] + dx, "y": c["y"] + dy, "w": c["w"] - 2 * dx, "h": c["h"] - 2 * dy}


def _keyed_render(preset: dict, motion: dict | None) -> tuple[dict, dict | None]:
    """С хромакеем основа остаётся на зелёнке, а лук уходит в компоновщик.

    Цвет, зерно и зеркало поверх зелёнки сдвинули бы её цвет — ключ бы
    промахнулся; поля и фон раскладки красятся в цвет ключа, чтобы вырезаться
    вместе с ним. Виньетки с ключом нет: ffmpeg кладёт её с непрозрачной альфой.
    """
    key = (motion or {}).get("key")
    if not key:
        return preset, motion
    base = {**preset, "mirror": False, "color_style": "none", "vignette": 0, "grain": 0, "key_fill": key["color"]}
    layout = preset.get("layout")
    if isinstance(layout, dict) and layout.get("mode") == "meme":
        base["layout"] = {**layout, "bg": key["color"]}
    elif isinstance(layout, dict) and layout.get("mode") == "avatar_full":
        # Аватар на зелёнке: поля — цветом ключа, вырежутся вместе с ним (сзади — backdrop клипа).
        base["layout"] = {**layout, "bg": key["color"], "bg_blur": False}
    grade = _video_style_filter({**preset, "vignette": 0})
    return base, {**motion, "key": {**key, "grade": grade}}


def effective_preset(store: AppStore, preset_id: int | None) -> dict | None:
    """Пресет, которым отрендерится клип: свой ``preset_id`` или (null) тот же, что берёт
    ``ClipRenderService._preset`` — но без создания нового (для показа в get_clip)."""
    if preset_id is not None:
        try:
            return store.get_ffmpeg_preset(int(preset_id))
        except (KeyError, TypeError, ValueError):
            return None
    presets = store.list_ffmpeg_presets()
    return presets[-1] if presets else None


def layout_camera_regions(preset: dict, source: dict | None, width: int, height: int) -> dict | None:
    """Окно игры и окошко вебки раскладки (app/stream_layout.camera_regions) для камеры
    компоновщика: зум двигает игру, окошко pip / полоса вебки стоят на месте."""
    from app.stream_layout import camera_regions

    layout = preset.get("layout")
    if not layout_active(layout):
        return None
    source = source or {}
    return camera_regions(layout, width, height, source.get("width"), source.get("height"),
                          mirror=bool(preset.get("mirror")))


def pin_band(preset: dict, source: dict | None, motion: dict | None) -> dict | None:
    """Полоса под основой для окошка раскладки, которое камера не двигает (``pinned``).

    Раньше основа приходила в компоновщик уже с окошком (килфид / вебка pip): зум камеры
    увеличивал весь кадр вместе с окошком, а поверх компоновщик клал то же окошко из
    незумленного кадра — окно раздваивалось (большая копия ехала с камерой). Теперь при
    камере основа ``W×(H+h)``: сверху игра без окошка (её и зумит камера), снизу — полоса
    ``h`` строк собранного кадра с окошком (строки ``y..y+h``); компоновщик берёт
    окошко из полосы (``pinned[].sy``) и кладёт на место. Окошко стоит, игра под ним
    зумится. ``{"y", "h"}`` px выхода или None (нет камеры / окошка, хромакей)."""
    motion = motion or {}
    if not motion.get("camera") or motion.get("key"):
        return None
    width = int(preset.get("output_width") or 1080)
    height = int(preset.get("output_height") or 1920)
    pinned = (layout_camera_regions(preset, source, width, height) or {}).get("pinned") or []
    if not pinned:
        return None
    top = min(int(p["y"]) for p in pinned)
    top -= top % 2
    bottom = max(int(p["y"]) + int(p["h"]) for p in pinned)
    h = min(height - top, bottom - top + (bottom - top) % 2)
    return {"y": top, "h": h} if h > 0 else None


def banded_pinned(pinned: list[dict] | None, band: dict | None, height: int) -> list[dict] | None:
    """Окошки ``pinned`` с ``sy`` — где их строки в полосе под кадром (см. ``pin_band``)."""
    if not pinned or not band:
        return pinned
    return [{**p, "sy": int(height) + int(p["y"]) - int(band["y"])} for p in pinned]


def _is_canvas(source: dict | None) -> bool:
    from app.canvas import is_canvas

    return is_canvas(source)


def _preset_with_output(
    preset: dict, source: dict | None, aspect: str | None, end_hold_sec: float = 0.0,
    layers_format: str | None = None,
) -> dict:
    """Формат вывода (app/formats.py: размер кадра, как основа ложится в кадр, раскладка
    для 16:9, перекомпоновка слоёв в _apply_motion) и стоп-кадр в конце (музыка доигрывает
    на нём — проход музыки). 9:16 со слоями 9:16 без стоп-кадра — пресет как есть.

    Ключи пресета: ``format`` (и старое ``aspect``) — формат не 9:16; ``layers_format`` —
    формат разметки слоёв (всегда, когда пресет меняется: 16:9 → 16:9 без него читался
    бы как 9:16 и слои сжимались колонкой; читает app/formats.motion_formats); ``base_fit`` — «fit»,
    когда исходник вписывается на подложку; ``landscape`` — горизонтальный кадр."""
    from app.formats import (
        DEFAULT_FORMAT,
        frame_size,
        is_landscape,
        normalize_format,
        settings_format,
        source_strategy,
    )

    fmt = normalize_format(aspect)
    layers_fmt = settings_format(layers_format)
    hold = max(0.0, float(end_hold_sec or 0.0))
    if fmt == DEFAULT_FORMAT and layers_fmt == DEFAULT_FORMAT and hold <= 0:
        return preset
    preset = dict(preset)
    if hold > 0:
        preset["end_hold_sec"] = hold
    preset["layers_format"] = layers_fmt
    if fmt != DEFAULT_FORMAT:
        width, height = frame_size(fmt)
        preset["format"] = fmt
        preset["aspect"] = fmt
        preset["output_width"] = width
        preset["output_height"] = height
        if source_strategy(fmt, source) == "fit":
            preset["base_fit"] = "fit"
        if is_landscape(fmt):
            from app.stream_layout import layout_for_format

            preset["landscape"] = True
            preset["layout"] = layout_for_format(preset.get("layout"), fmt, source)
    return preset


def _preset_with_source_audio(preset: dict, source: dict) -> dict:
    """Дорожки звука, выбранные у источника (app/source_audio.py), — поверх пресета.

    Не выбраны — пресет как был (первая дорожка или его собственное смешивание)."""
    from app.source_audio import mix_storage, source_mix

    # Дорожки со своей громкостью [{track, gain_db}] (тихий микрофон поднят над игрой);
    # без усилений — прежний список номеров.
    tracks = mix_storage(source_mix(source))
    return {**preset, "source_audio_tracks": tracks} if tracks else preset


def _preset_with_music_automation(preset: dict, audio_edit: dict | None) -> dict:
    """Ключи громкости, ручной ducking и провалы музыки (app/audio_edit.py) — в проход музыки;
    сведение клипа (``mix``: громкость трека, sidechain, эффекты голоса/музыки — app/audio_mix.py)."""
    music = (audio_edit or {}).get("music")
    mix = (audio_edit or {}).get("mix")
    if music:
        preset = {**preset, "music_automation": music}
    return {**preset, "mix": mix} if mix else preset


def _piece_retime(segment: dict, source: dict) -> dict | None:
    """Скорость/реверс части для build_ffmpeg_render_args (app/piece_speed.py); None — как всегда."""
    from app.piece_speed import is_retimed, seg_speed
    from app.source_audio import _streams_from_metadata

    if not is_retimed(segment):
        return None
    # Дорожек в метаданных не видно — верим, что звук есть (как render_plan).
    streams = _streams_from_metadata(source.get("metadata_json") or source.get("metadata"))
    has_audio = streams is None or any(s.get("codec_type") == "audio" for s in streams)
    return {"speed": seg_speed(segment), "has_audio": has_audio, "segment": segment}


class PresetsRender:
    def _output_preset(self, preset: dict, source: dict | None, aspect: str | None, end_hold_sec: float = 0.0,
                       layers_format: str | None = None) -> dict:
        return _preset_with_output(preset, source, aspect, end_hold_sec, layers_format)

    def _preset(self, preset_id: int | None) -> dict:
        if preset_id is not None:
            return self.store.get_ffmpeg_preset(preset_id)
        presets = self.store.list_ffmpeg_presets()
        if presets:
            return presets[-1]
        return self.store.create_ffmpeg_preset("Default vertical")

    def _preset_with_banner(self, preset: dict, banner_id: int | None) -> dict:
        if banner_id is None:
            return preset
        if banner_id <= 0:
            preset = dict(preset)
            preset["banner_id"] = None
            return preset
        self.store.get_banner(banner_id)
        preset = dict(preset)
        preset["banner_id"] = banner_id
        return preset

    def _preset_with_music(
        self,
        preset: dict,
        music_track_id: int | None,
        music_volume: float | None = None,
        music_start_sec: float | None = None,
    ) -> dict:
        if music_track_id is None and music_volume is None and not music_start_sec:
            return preset
        preset = dict(preset)
        if music_start_sec:
            # С какой секунды трека играет музыка на 0 с клипа (сразу с барабанов).
            preset["music_start_sec"] = max(0.0, min(MAX_MUSIC_START_SEC, float(music_start_sec)))
        if music_track_id is not None:
            if music_track_id <= 0:
                preset["music_track_id"] = None
            else:
                self.store.get_audio_track(music_track_id)
                preset["music_track_id"] = music_track_id
        if music_volume is not None:
            preset["music_volume_override"] = max(0.0, min(4.0, float(music_volume)))
        return preset

    def _preset_with_subtitle_offset(self, preset: dict, subtitle_offset_sec: float | None) -> dict:
        if subtitle_offset_sec is None:
            return preset
        preset = dict(preset)
        preset["subtitle_offset_override"] = max(-2.0, min(2.0, float(subtitle_offset_sec)))
        return preset

    def _preset_with_render_overrides(
        self,
        preset: dict,
        *,
        banner_height_frac: float | None = None,
        banner_y_frac: float | None = None,
        subtitle_provider: str | None = None,
        subtitle_margin_v: int | None = None,
        mirror: bool = False,
        layout: dict | None = None,
    ) -> dict:
        """Stash per-render overrides on the preset dict so they reach the ffmpeg
        builder / subtitle pass without changing the saved preset or profile.

        - banner_height_frac scales the banner overlay to a fraction of the output
          height (preserving aspect).
        - banner_y_frac pins the banner's top edge to a fraction of the output
          height (0 = top), overriding the banner asset's saved position.
        - subtitle_provider swaps the transcription engine (e.g. whisper vs gemini)
          regardless of what the style profile saved.
        - subtitle_margin_v pins the subtitle band vertical position (ASS units).
        - layout (app/stream_layout.py) — split / pip_cam; single changes nothing.
        """
        layout = normalize_layout(layout) if layout else None
        if not layout_active(layout):
            layout = None
        if (
            banner_height_frac is None
            and banner_y_frac is None
            and not subtitle_provider
            and subtitle_margin_v is None
            and not mirror
            and layout is None
        ):
            return preset
        preset = dict(preset)
        if layout is not None:
            preset["layout"] = layout
        if banner_height_frac is not None:
            preset["banner_height_frac"] = max(0.02, min(0.6, float(banner_height_frac)))
        if banner_y_frac is not None:
            preset["banner_y_frac"] = max(0.0, min(0.97, float(banner_y_frac)))
        if subtitle_provider:
            preset["subtitle_provider_override"] = str(subtitle_provider).strip().lower()
        if subtitle_margin_v is not None:
            preset["subtitle_margin_v_override"] = max(0, int(subtitle_margin_v))
        if mirror:
            preset["mirror"] = True
        return preset
