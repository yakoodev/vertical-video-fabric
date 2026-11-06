from __future__ import annotations

import contextvars
import json
import logging
import math
import shutil
import subprocess
import sys
import threading
import time
import types
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from app.focus_presets import get_focus_preset
from app.heavy_queue import heavy, heavy_slot
from app.ingest import file_hash_and_size, probe_media
from app.settings import settings
from app.store import AppStore
from app.subtitles.ass import write_ass_subtitles
from app.subtitles.contracts import SubtitleResult
from app.subtitles.registry import get_subtitle_provider, subtitle_model_for_profile
from app.subtitles.timing import normalize_subtitle_timeline, shift_subtitle_timeline
from app.clip_settings import normalize_cover
from app.cover import build_burn_args, build_frame_args, build_image_args, cover_output_time
from app.render_qc import run_qc
from app.stream_layout import (
    even_px,
    ff_color,
    gameplay_height,
    layout_active,
    normalize_layout,
    scaled_px,
    split_geometry,
)
from app.transitions import build_join_args, is_plain_concat, normalize_transition, part_overlaps, plan_joins
from app.backgrounds import background_zones, fill_color, needs_zones
from app.video_crop import build_reframe_x_expr
from app.piece_speed import seg_length

# Рендер разложен по этапам (app/render_*.py); все имена остаются доступны и отсюда.
from app.render_exec import (  # noqa: F401
    _clamp, _concat_file_line, _ff_number, _filter_filename, _fps, _has_audio_stream, _json_dict, _run_compositor,
    _run_ffmpeg, _safe_error, _seconds, _stream_index, _video_duration, _volume, RENDER_RUNNER,
)
from app.render_loudness import (  # noqa: F401
    _compressor, _measure_loudness, audible, COMPRESSOR_ABOVE_TARGET_DB, correct_loudness_plan, END_AUDIO_FADE_SEC,
    end_fade_filter, FINAL_AUDIO_BITRATE, finished_loudness_lift, fit_loudness, LIMITER_IDLE_DB, LIMITER_LOSS_LU,
    LIMITER_ROOM_DB, linear_gain_db, loudness_filters, LOUDNESS_LIFT_MARGIN_DB, LOUDNESS_LIMITER, LOUDNESS_PEAK_DB,
    loudness_plan, LOUDNESS_TOLERANCE_LU, loudnorm_needed, LOUDNORM_SILENCE_LUFS, MAKEUP_EFFICIENCY, MAX_EXTRA_BOOST_DB,
    MAX_LOUDNESS_BOOST_DB, MAX_MAKEUP_DB, MUSIC_TAIL_MIN_SEC, music_tail_sec, OUTPUT_SAMPLE_RATE, SILENT_LUFS,
    stream_durations, TRUE_PEAK_FLOOR_DB, TRUE_PEAK_LIMIT_DB, true_peak_limiter, TRUE_PEAK_OVERSAMPLE,
    true_peak_retry_ceiling, TRUE_PEAK_RETRY_MARGIN_DB, TRUE_PEAK_TOLERANCE_DB,
)
from app.render_filters import (  # noqa: F401
    _audio_filter, _audio_map, _banner_overlay, _banner_position_expr, _color_style_filters, _crop_prefix,
    _filter_complex, _fit_box, _freeze_source, _layout_filter, _piece_fit_filter, _render_filter, _retimed,
    _source_edit, _video_style_filter, BANNER_MAX_WIDTH_SHARE,
)
from app.render_args import (  # noqa: F401
    _banner_input_args, build_audio_handle_args, build_ffmpeg_concat_args, build_ffmpeg_render_args, build_unband_args,
    build_voice_fx_args,
)


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


class ClipRenderService:
    def __init__(self, store: AppStore) -> None:
        self.store = store
        # Per-source render-cancel marks (monotonic timestamps). A batch loop
        # records when it started and stops before its next clip once a cancel
        # was requested after that — so a later batch on the same source is
        # unaffected. In-memory is enough: all renders run in this process.
        self._cancel_marks: dict[int, float] = {}
        self._cancel_lock = threading.Lock()

    @staticmethod
    def batch_clock() -> float:
        """Clock for batch start / cancel marks. perf_counter, not monotonic:
        on Windows monotonic ticks every ~16 ms, so a cancel and a batch start
        in the same tick compared equal and the cancel was silently missed."""
        return time.perf_counter()

    def request_render_cancel(self, source_id: int) -> None:
        with self._cancel_lock:
            self._cancel_marks[int(source_id)] = self.batch_clock()

    def render_batch_cancelled(self, source_id: int, started_at: float) -> bool:
        with self._cancel_lock:
            return self._cancel_marks.get(int(source_id), -1.0) > started_at

    @heavy("render")
    def render_segment(
        self,
        segment_id: int,
        ffmpeg_preset_id: int | None = None,
        subtitle_profile_id: int | None = None,
        banner_id: int | None = None,
        clip_plan_id: int | None = None,
        music_track_id: int | None = None,
        music_volume: float | None = None,
        subtitle_offset_sec: float | None = None,
        subtitle_provider: str | None = None,
        banner_height_frac: float | None = None,
        banner_y_frac: float | None = None,
        subtitle_margin_v: int | None = None,
        mirror: bool = False,
        cover: dict | None = None,
        motion: dict | None = None,
        layout: dict | None = None,
        music_start_sec: float | None = None,
        aspect: str | None = None,
        end_hold_sec: float = 0.0,
        layers_format: str | None = None,
        freezes: list[dict] | None = None,
        audio_edit: dict | None = None,
    ) -> dict:
        """``motion`` — видимые слои и камера (``visual_motion``); None — как раньше.
        ``aspect`` — формат вывода (app/formats.py), ``layers_format`` — в каком формате
        размечены слои (другой — перекомпоновка), ``end_hold_sec`` — стоп-кадр в конце,
        который потом добавит ``_normalize_loudness``: музыка доигрывает на нём.
        ``audio_edit`` — звуковой монтаж клипа (app/audio_edit.render_plan); None — как раньше."""
        from app.audio_edit import part_edit

        segment = self.store.get_ai_segment(segment_id)
        source = self.store.get_source(segment["source_id"])
        preset = self._preset(ffmpeg_preset_id)
        preset = self._preset_with_banner(preset, banner_id)
        preset = self._preset_with_music(preset, music_track_id, music_volume, music_start_sec)
        preset = self._preset_with_subtitle_offset(preset, subtitle_offset_sec)
        preset = self._preset_with_render_overrides(
            preset,
            banner_height_frac=banner_height_frac,
            banner_y_frac=banner_y_frac,
            subtitle_provider=subtitle_provider,
            subtitle_margin_v=subtitle_margin_v,
            mirror=mirror,
            layout=layout,
        )
        preset = _preset_with_source_audio(preset, source)
        preset = self._output_preset(preset, source, aspect, end_hold_sec, layers_format)
        preset = _preset_with_music_automation(preset, audio_edit)
        if freezes:
            preset = {**preset, "freezes": freezes}  # стоп-кадры посреди ролика (app/freeze.py)
        if subtitle_profile_id is None and not _is_canvas(source):
            # У холста основа немая — профиль субтитров пресета прогнал бы распознавание
            # по тишине и мог бы выдумать текст; явный subtitle_profile_id уважаем.
            subtitle_profile_id = preset.get("subtitle_profile_id")
        clip = self.store.create_clip(
            source["id"],
            segment_id=segment_id,
            clip_plan_id=clip_plan_id,
            ffmpeg_preset_id=preset["id"],
            subtitle_profile_id=subtitle_profile_id,
        )
        self.store.update_ai_segment_status(segment_id, "rendering")
        self.store.mark_clip_rendering(clip["id"])
        render_id = uuid4().hex
        output_path = self._clip_dir() / f"{render_id}.mp4"
        has_post = bool(subtitle_profile_id) or bool(preset.get("music_track_id"))
        base_output_path = self._clip_dir() / f"{render_id}.base.mp4" if has_post else output_path
        temp_paths: list[Path] = []
        if has_post:
            temp_paths.append(base_output_path)
        try:
            banner = self.store.get_banner(preset["banner_id"]) if preset.get("banner_id") else None
            base_preset, motion = _keyed_render(preset, motion)
            motion = _with_background_zones(motion, preset, [segment], [], source)
            band = pin_band(preset, source, motion)
            if band:
                motion = {**motion, "pin_band": band}
            piece_preset = _preset_for_segment(base_preset, segment)
            if band:
                piece_preset = {**piece_preset, "pin_band": band}
            edit = part_edit(audio_edit, 0, float(segment["start_sec"]), float(segment["end_sec"]))
            if edit:
                piece_preset = {**piece_preset, "audio_edit": edit}
            args = self._build_part_args(
                source_path=Path(source["local_path"]),
                output_path=base_output_path,
                start_sec=float(segment["start_sec"]),
                end_sec=float(segment["end_sec"]),
                preset=piece_preset,
                # С моушном баннер кладётся после камеры и слоёв, а не в основу.
                banner=None if motion else banner,
                source_crop=_base_crop(source, motion),
                reframe_x=_segment_reframe_x(segment, _preset_for_segment(preset, segment), source),
            )
            _run_ffmpeg(args, timeout=60 * 30)
            self._apply_freezes(base_output_path, preset)
            motion_notes = self._apply_motion(render_id, base_output_path, preset, banner, motion, source) if motion else []
            final_output_path, post_temps = self._finalize_render(
                clip_id=clip["id"],
                render_id=render_id,
                base_output_path=base_output_path,
                output_path=output_path,
                preset=preset,
                subtitle_profile_id=subtitle_profile_id,
            )
            temp_paths.extend(post_temps)
            self._apply_cover(
                clip["id"], render_id, final_output_path, preset, cover,
                [float(segment["end_sec"]) - float(segment["start_sec"])], None,
            )
            metadata = probe_media(final_output_path)
            _sha256, size_bytes = file_hash_and_size(final_output_path)
            clip = self.store.finish_clip_render(
                clip["id"],
                status="succeeded",
                output_path=final_output_path,
                preview_path=final_output_path,
                duration_sec=metadata.duration_sec,
                width=metadata.width,
                height=metadata.height,
                size_bytes=size_bytes,
            )
            if motion_notes:
                clip = self._note_clip_error(clip["id"], "; ".join(motion_notes))
            clip = self._attach_qc(clip, final_output_path, metadata)
            self.store.update_ai_segment_status(segment_id, "rendered")
            for path in temp_paths:
                path.unlink(missing_ok=True)
            return clip
        except Exception as exc:  # noqa: BLE001 - render failures are persisted as clip state
            clip = self.store.finish_clip_render(
                clip["id"],
                status="failed",
                error=_safe_error(exc),
            )
            self.store.update_ai_segment_status(segment_id, "candidate")
            output_path.unlink(missing_ok=True)
            for path in temp_paths:
                path.unlink(missing_ok=True)
            return clip

    # Точки подключения предпросмотра-трансляции (app/stream_preview.py): обычный рендер здесь ничего не меняет.
    def _clip_dir(self) -> Path:
        return settings.clip_dir

    def _tmp_dir(self) -> Path:
        return settings.tmp_dir

    def _output_preset(self, preset: dict, source: dict | None, aspect: str | None, end_hold_sec: float = 0.0,
                       layers_format: str | None = None) -> dict:
        return _preset_with_output(preset, source, aspect, end_hold_sec, layers_format)

    def _build_part_args(self, **kwargs: Any) -> list[str]:
        return build_ffmpeg_render_args(**kwargs)

    def _build_join_args(self, *args: Any, **kwargs: Any) -> list[str]:
        return build_join_args(*args, **kwargs)

    def _preset(self, preset_id: int | None) -> dict:
        if preset_id is not None:
            return self.store.get_ffmpeg_preset(preset_id)
        presets = self.store.list_ffmpeg_presets()
        if presets:
            return presets[-1]
        return self.store.create_ffmpeg_preset("Default vertical")

    @heavy("render")
    def render_montage(
        self,
        segment_ids: list[int],
        ffmpeg_preset_id: int | None = None,
        subtitle_profile_id: int | None = None,
        banner_id: int | None = None,
        clip_plan_id: int | None = None,
        title: str = "Montage",
        description: str = "",
        music_track_id: int | None = None,
        music_volume: float | None = None,
        subtitle_offset_sec: float | None = None,
        subtitle_provider: str | None = None,
        banner_height_frac: float | None = None,
        banner_y_frac: float | None = None,
        subtitle_margin_v: int | None = None,
        mirror: bool = False,
        transition: dict | None = None,
        cover: dict | None = None,
        motion: dict | None = None,
        layout: dict | None = None,
        cut_parts: list[dict] | None = None,
        jump_joins: list[bool] | None = None,
        music_start_sec: float | None = None,
        aspect: str | None = None,
        end_hold_sec: float = 0.0,
        layers_format: str | None = None,
        freezes: list[dict] | None = None,
        audio_edit: dict | None = None,
    ) -> dict:
        """``cut_parts``/``jump_joins`` — клип с джамп-катами (app/cuts.py): части
        рендерятся вместо кусков, склейки ``jump_joins`` — встык с микрофейдом.
        ``audio_edit`` — звуковой монтаж (app/audio_edit.render_plan): операции звука частей,
        J/L на склейках, автоматизация музыки; None — как раньше."""
        from app.audio_edit import part_edit

        if not segment_ids:
            raise ValueError("at least one segment is required")
        join = normalize_transition(transition)
        segments = [self.store.get_ai_segment(segment_id) for segment_id in segment_ids]
        source_ids = {segment["source_id"] for segment in segments}
        if len(source_ids) != 1:
            raise ValueError("all montage segments must belong to the same source")
        source = self.store.get_source(segments[0]["source_id"])
        preset = self._preset(ffmpeg_preset_id)
        preset = self._preset_with_banner(preset, banner_id)
        preset = self._preset_with_music(preset, music_track_id, music_volume, music_start_sec)
        preset = self._preset_with_subtitle_offset(preset, subtitle_offset_sec)
        preset = self._preset_with_render_overrides(
            preset,
            banner_height_frac=banner_height_frac,
            banner_y_frac=banner_y_frac,
            subtitle_provider=subtitle_provider,
            subtitle_margin_v=subtitle_margin_v,
            mirror=mirror,
            layout=layout,
        )
        preset = _preset_with_source_audio(preset, source)
        preset = self._output_preset(preset, source, aspect, end_hold_sec, layers_format)
        preset = _preset_with_music_automation(preset, audio_edit)
        if freezes:
            preset = {**preset, "freezes": freezes}  # стоп-кадры посреди ролика (app/freeze.py)
        if subtitle_profile_id is None and not _is_canvas(source):
            # У холста основа немая — профиль субтитров пресета прогнал бы распознавание
            # по тишине и мог бы выдумать текст; явный subtitle_profile_id уважаем.
            subtitle_profile_id = preset.get("subtitle_profile_id")
        clip = self.store.create_clip(
            source["id"],
            segment_id=None,
            clip_plan_id=clip_plan_id,
            ffmpeg_preset_id=preset["id"],
            subtitle_profile_id=subtitle_profile_id,
            title=title,
            description=description,
        )
        self.store.mark_clip_rendering(clip["id"])
        render_id = uuid4().hex
        output_path = self._clip_dir() / f"{render_id}.mp4"
        has_post = bool(subtitle_profile_id) or bool(preset.get("music_track_id"))
        base_output_path = self._clip_dir() / f"{render_id}.base.mp4" if has_post else output_path
        temp_dir = self._tmp_dir() / f"montage-{render_id}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_paths: list[Path] = []
        if has_post:
            temp_paths.append(base_output_path)
        try:
            banner = self.store.get_banner(preset["banner_id"]) if preset.get("banner_id") else None
            base_preset, motion = _keyed_render(preset, motion)
            # Без вырезов части — это сами куски (рендер ровно прежний).
            parts = cut_parts or segments
            jumps = list(jump_joins or []) if cut_parts else None
            motion = _with_background_zones(motion, preset, parts, part_overlaps(parts, join, jumps), source)
            sar_fix = _needs_sar_fix(parts)
            band = pin_band(preset, source, motion)
            if band:
                motion = {**motion, "pin_band": band}
            rendered_parts: list[Path] = []
            for index, segment in enumerate(parts):
                part_path = temp_dir / f"part-{index:03d}.mp4"
                piece_preset = _preset_for_segment(base_preset, segment)
                if sar_fix:
                    piece_preset = {**piece_preset, "normalize_sar": True}
                if band:
                    piece_preset = {**piece_preset, "pin_band": band}
                edit = part_edit(audio_edit, index, float(segment["start_sec"]), float(segment["end_sec"]))
                if edit:
                    piece_preset = {**piece_preset, "audio_edit": edit}
                retime = _piece_retime(segment, source)
                if retime:
                    piece_preset = {**piece_preset, "piece_retime": retime}
                _run_ffmpeg(
                    self._build_part_args(
                        source_path=Path(source["local_path"]),
                        output_path=part_path,
                        start_sec=float(segment["start_sec"]),
                        end_sec=float(segment["end_sec"]),
                        preset=piece_preset,
                        banner=None if motion else banner,
                        source_crop=_base_crop(source, motion),
                        reframe_x=_segment_reframe_x(segment, _preset_for_segment(preset, segment), source),
                    ),
                    timeout=60 * 30,
                )
                rendered_parts.append(part_path)
            shifts = (audio_edit or {}).get("shifts") if len(rendered_parts) > 1 else None
            if is_plain_concat(join) and not any(jumps or []) and not any(shifts or []):
                # Hard cut on picture and sound, nothing on the join: the old
                # stream-copy concat is exact and much faster than re-encoding.
                concat_list_path = temp_dir / "concat.txt"
                concat_list_path.write_text(
                    "\n".join(_concat_file_line(path) for path in rendered_parts) + "\n",
                    encoding="utf-8",
                )
                _run_ffmpeg(build_ffmpeg_concat_args(concat_list_path, base_output_path), timeout=60 * 30)
            else:
                probes = [probe_media(path) for path in rendered_parts]
                has_audio = all(
                    any(st.get("codec_type") == "audio" for st in ((m.raw or {}).get("streams") or []))
                    for m in probes
                )
                extra = _json_dict(preset.get("extra_json"))
                # По картинке, а не по контейнеру: звук подгоняется под неё.
                durations = [_video_duration(m) for m in probes]
                jl = None
                if has_audio and any(shifts or []):
                    jl = self._jl_handles(source, base_preset, parts, durations, join, jumps, shifts,
                                          audio_edit, temp_dir)
                _run_ffmpeg(
                    self._build_join_args(
                        rendered_parts,
                        durations,
                        base_output_path,
                        join,
                        has_audio=has_audio,
                        gaps=_source_gaps(parts),
                        video_codec=str(preset.get("video_codec") or "libx264"),
                        video_bitrate=str(preset.get("video_bitrate") or "").strip(),
                        crf=extra.get("crf"),
                        audio_codec=str(preset.get("audio_codec") or "aac"),
                        audio_bitrate=str(preset.get("audio_bitrate") or "").strip(),
                        width=int(preset.get("output_width") or 1080),
                        fps=float(preset.get("fps") or 30),
                        jump_joins=jumps,
                        jl=jl,
                    ),
                    timeout=60 * 30,
                )
            self._apply_freezes(base_output_path, preset)
            motion_notes = self._apply_motion(render_id, base_output_path, preset, banner, motion, source) if motion else []
            final_output_path, post_temps = self._finalize_render(
                clip_id=clip["id"],
                render_id=render_id,
                base_output_path=base_output_path,
                output_path=output_path,
                preset=preset,
                subtitle_profile_id=subtitle_profile_id,
            )
            temp_paths.extend(post_temps)
            self._apply_cover(
                clip["id"], render_id, final_output_path, preset, cover,
                [seg_length(seg) for seg in parts], join,
                gaps=_source_gaps(parts),
                jump_joins=jumps,
            )
            metadata = probe_media(final_output_path)
            _sha256, size_bytes = file_hash_and_size(final_output_path)
            clip = self.store.finish_clip_render(
                clip["id"],
                status="succeeded",
                output_path=final_output_path,
                preview_path=final_output_path,
                duration_sec=metadata.duration_sec,
                width=metadata.width,
                height=metadata.height,
                size_bytes=size_bytes,
            )
            if motion_notes:
                clip = self._note_clip_error(clip["id"], "; ".join(motion_notes))
            clip = self._attach_qc(clip, final_output_path, metadata)
            for segment in segments:
                self.store.update_ai_segment_status(segment["id"], "rendered")
            for path in temp_paths:
                path.unlink(missing_ok=True)
            return clip
        except Exception as exc:  # noqa: BLE001 - render failures are persisted as clip state
            clip = self.store.finish_clip_render(
                clip["id"],
                status="failed",
                error=_safe_error(exc),
            )
            output_path.unlink(missing_ok=True)
            for path in temp_paths:
                path.unlink(missing_ok=True)
            return clip
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    @heavy("render")
    def render_uploaded_clip(
        self,
        clip_id: int,
        ffmpeg_preset_id: int | None = None,
        subtitle_profile_id: int | None = None,
        banner_id: int | None = None,
        music_track_id: int | None = None,
        music_volume: float | None = None,
        subtitle_offset_sec: float | None = None,
        subtitle_provider: str | None = None,
        banner_height_frac: float | None = None,
        banner_y_frac: float | None = None,
        subtitle_margin_v: int | None = None,
        mirror: bool = False,
    ) -> dict:
        """Render a fresh clip from a directly-uploaded clip's pristine source.

        Standalone clips (source_type='clip_upload') keep their untouched upload
        as the backing source. This re-frames that source with the preset and
        applies the optional banner, subtitle and music passes, producing a NEW
        clip so the original upload stays intact and re-renders never compound.
        """

        origin = self.store.get_clip(clip_id)
        source = self.store.get_source(origin["source_id"])
        if str(source.get("source_type") or "") != "clip_upload":
            raise ValueError("only directly-uploaded clips can be rendered this way")
        input_path = Path(source["local_path"])
        if not input_path.exists():
            raise ValueError("original upload is no longer available; re-upload the clip")
        duration = float(source.get("duration_sec") or 0) or probe_media(input_path).duration_sec
        if duration <= 0:
            raise ValueError("could not determine clip duration")
        preset = self._preset(ffmpeg_preset_id)
        preset = self._preset_with_banner(preset, banner_id)
        preset = self._preset_with_music(preset, music_track_id, music_volume)
        preset = self._preset_with_subtitle_offset(preset, subtitle_offset_sec)
        preset = self._preset_with_render_overrides(
            preset,
            banner_height_frac=banner_height_frac,
            banner_y_frac=banner_y_frac,
            subtitle_provider=subtitle_provider,
            subtitle_margin_v=subtitle_margin_v,
            mirror=mirror,
        )
        if subtitle_profile_id is None:
            subtitle_profile_id = preset.get("subtitle_profile_id")
        clip = self.store.create_clip(
            source["id"],
            ffmpeg_preset_id=preset["id"],
            subtitle_profile_id=subtitle_profile_id,
            title=origin.get("title") or "",
            description=origin.get("description") or "",
        )
        self.store.mark_clip_rendering(clip["id"])
        render_id = uuid4().hex
        output_path = self._clip_dir() / f"{render_id}.mp4"
        has_post = bool(subtitle_profile_id) or bool(preset.get("music_track_id"))
        base_output_path = self._clip_dir() / f"{render_id}.base.mp4" if has_post else output_path
        temp_paths: list[Path] = []
        if has_post:
            temp_paths.append(base_output_path)
        try:
            banner = self.store.get_banner(preset["banner_id"]) if preset.get("banner_id") else None
            args = build_ffmpeg_render_args(
                source_path=input_path,
                output_path=base_output_path,
                start_sec=0.0,
                end_sec=duration,
                preset=preset,
                banner=banner,
            )
            _run_ffmpeg(args, timeout=60 * 30)
            final_output_path, post_temps = self._finalize_render(
                clip_id=clip["id"],
                render_id=render_id,
                base_output_path=base_output_path,
                output_path=output_path,
                preset=preset,
                subtitle_profile_id=subtitle_profile_id,
            )
            temp_paths.extend(post_temps)
            metadata = probe_media(final_output_path)
            _sha256, size_bytes = file_hash_and_size(final_output_path)
            clip = self.store.finish_clip_render(
                clip["id"],
                status="succeeded",
                output_path=final_output_path,
                preview_path=final_output_path,
                duration_sec=metadata.duration_sec,
                width=metadata.width,
                height=metadata.height,
                size_bytes=size_bytes,
            )
            clip = self._attach_qc(clip, final_output_path, metadata)
            for path in temp_paths:
                path.unlink(missing_ok=True)
            return clip
        except Exception as exc:  # noqa: BLE001 - render failures are persisted as clip state
            clip = self.store.finish_clip_render(clip["id"], status="failed", error=_safe_error(exc))
            output_path.unlink(missing_ok=True)
            for path in temp_paths:
                path.unlink(missing_ok=True)
            return clip

    def _apply_cover(  # noqa: PLR0913 - обложке нужен весь контекст рендера
        self,
        clip_id: int,
        render_id: str,
        video: Path,
        preset: dict,
        cover: dict | None,
        durations: list[float],
        join: dict | None,
        gaps: list[float | None] | None = None,
        jump_joins: list[bool] | None = None,
    ) -> Path | None:
        """Make the clip's cover and, if asked, burn it in as the first frame.

        A cover problem never fails the render: the clip is still good, it just
        comes without a cover (the error is logged on the clip for the UI).
        """
        c = normalize_cover(cover)
        if c["mode"] == "none":
            return None
        width = int(preset.get("output_width") or 1080)
        height = int(preset.get("output_height") or 1920)
        out = settings.clip_dir / f"{render_id}.cover.jpg"
        try:
            if c["mode"] == "frame":
                overlaps = plan_joins(join, durations, gaps, jump_joins) if join else 0.0
                t = cover_output_time(durations, overlaps, c["piece"], c["offset"])
                _run_ffmpeg(build_frame_args(video, t, out), timeout=120)
            else:
                image = settings.data_dir / "covers" / c["image"]
                if not image.exists():
                    raise ValueError(f"cover image not found: {c['image']}")
                _run_ffmpeg(build_image_args(image, width, height, out), timeout=120)
            if c["burn"]:
                burned = video.with_name(video.stem + ".cover.mp4")
                extra = _json_dict(preset.get("extra_json"))
                _run_ffmpeg(
                    build_burn_args(
                        video, out, burned,
                        width=width, height=height, burn_sec=c["burn_sec"],
                        video_codec=str(preset.get("video_codec") or "libx264"),
                        video_bitrate=str(preset.get("video_bitrate") or "").strip(),
                        crf=extra.get("crf"),
                    ),
                    timeout=60 * 20,
                )
                burned.replace(video)
        except Exception as exc:  # noqa: BLE001 - the clip itself is fine
            out.unlink(missing_ok=True)
            self.store.set_clip_cover(clip_id, None, error=_safe_error(exc))
            return None
        self.store.set_clip_cover(clip_id, out)
        return out

    def _attach_qc(self, clip: dict, path: Path, metadata) -> dict:
        """Quality report for a finished render (never fails the render itself)."""
        qc = run_qc(path, duration_sec=metadata.duration_sec, width=metadata.width, height=metadata.height)
        if qc is None:
            return clip
        qc = self._qc_subtitle_sync(clip, path, qc)
        self.store.set_clip_qc(clip["id"], qc)
        return self.store.get_clip(clip["id"])

    def _qc_subtitle_sync(self, clip: dict, path: Path, qc: dict) -> dict:
        """Совпадают ли сохранённые субтитры клипа с речью в готовом файле.

        Ловит класс ошибок «обрезали кусок, а слова не сдвинули»: субтитры
        уезжают на секунды, и на глаз это видно только на просмотре.
        """
        from app.render_qc import estimate_subtitle_offset, subtitle_sync_issue

        try:
            plan_id = clip.get("clip_plan_id")
            if not plan_id:
                return qc
            plan = self.store.get_clip_plan(int(plan_id))
            cut_plan = self._cut_plan(plan, clip.get("ffmpeg_preset_id"))
            voice = self._qc_sync_on_voice(plan, cut_plan)
            if voice is not None:
                # Своя дорожка речи (микрофон): сверяем по голосу на ней, а не по громкости
                # готового звука, где выстрелы игры похожи на речь.
                offset, basis = voice, "дорожке речи"
                qc = {**qc, "subtitle_offset_sec": offset, "subtitle_sync_basis": "speech_track"}
                issue = subtitle_sync_issue(offset, basis)
                if issue:
                    qc["issues"] = [*qc.get("issues", []), issue]
                    qc["ok"] = False
                return qc
            # Ровно те тайминги слов, что рендер прожёг/отдал слою «субтитры»:
            # вырезы, потом настоящие overlap склеек после вырезов.
            if cut_plan is not None:
                from app.cuts import cut_caption_words

                final = cut_caption_words(self.store, plan, cut_plan)
            else:
                from app.motion_render import plan_caption_words

                final = plan_caption_words(self.store, plan)
            words = [{"start": w["t0"], "end": w["t1"]} for w in final]
            offset = estimate_subtitle_offset(path, words)
            if offset is None:
                return qc
            qc = {**qc, "subtitle_offset_sec": offset}
            issue = subtitle_sync_issue(offset)
            if issue:
                qc["issues"] = [*qc.get("issues", []), issue]
                qc["ok"] = False
        except Exception:  # noqa: BLE001 - QC никогда не ломает рендер
            return qc
        return qc

    def _qc_sync_on_voice(self, plan: dict, cut_plan=None) -> float | None:
        """Сдвиг субтитров по дорожке речи источника (``speech_tracks``), или None — дорожки
        речи отдельно нет / сверить нельзя. Звук — части клипа подряд (после вырезов, без
        наплывов), слова — в том же времени."""
        from app.clip_subtitles import build_clip_audio_args
        from app.cuts import remap_subtitles
        from app.render_qc import estimate_offset_on_voice
        from app.source_audio import separate_speech, speech_mix
        from app.speech_onsets import wav_pcm

        source = self.store.get_source(int(plan["source_id"]))
        if not separate_speech(source):
            return None
        payload = self.store.get_clip_plan_subtitles(int(plan["id"])) or {}
        if not payload.get("words"):
            return None
        if cut_plan is not None:
            payload = remap_subtitles(payload, cut_plan.mapper)
            parts = cut_plan.parts
        else:
            parts = plan.get("segments") or []
        pieces = [(float(p["start_sec"]), float(p["end_sec"])) for p in parts]
        source_path = Path(str(source.get("local_path") or ""))
        if not pieces or not source_path.exists():
            return None
        wav = settings.tmp_dir / f"qc-voice-{uuid4().hex}.wav"
        try:
            _run_ffmpeg(build_clip_audio_args(source_path, pieces, wav, speech_mix(source)), timeout=60 * 5)
            pcm = wav_pcm(wav)
        finally:
            wav.unlink(missing_ok=True)
        return estimate_offset_on_voice(pcm, 16000, payload.get("words") or [])

    def render_clip_plan(self, clip_plan_id: int, *args: Any, **kwargs: Any) -> dict:
        """Рендер клипа — по одному на весь сервис.

        Рендер тяжёлый (ffmpeg + компоновщик моушна). Два одновременных — два агента
        или агент и кнопка в редакторе — уже трижды роняли Docker по памяти; очередь
        дешевле. Очередь общая с распознаванием и автофокусом (app/heavy_queue.py); рендер
        может позвать рендер в том же потоке (подборка) — вложенный вызов места не ждёт."""
        with heavy_slot("render", f"клип {clip_plan_id}"):
            from app.clip_groups import capture_quiet, snapshot_render

            stamp = self._plan_fingerprint(clip_plan_id)
            state = capture_quiet(self.store, clip_plan_id)
            clip = self._render_clip_plan_unlocked(clip_plan_id, *args, **kwargs)
            if isinstance(clip, dict) and clip.get("id"):
                # Вечный снимок монтажа этой версии: откат с экрана клипа (история правок — всего 20 шагов).
                snapshot_render(self.store, clip_plan_id, int(clip["id"]), state)
            if stamp and isinstance(clip, dict) and clip.get("id"):
                # С какого состояния клипа сделан файл: lint_clip сверяет, правили ли клип после.
                try:
                    self.store.set_clip_plan_fingerprint(int(clip["id"]), stamp)
                    clip = {**clip, "plan_fingerprint": stamp}
                except Exception:  # noqa: BLE001 - отпечаток — удобство, рендер важнее
                    pass
        return self._after_render(clip)

    def _plan_fingerprint(self, clip_plan_id: int) -> str | None:
        try:
            from app.quality_lint import plan_fingerprint

            return plan_fingerprint(self.store, clip_plan_id)
        except Exception:  # noqa: BLE001
            return None

    def _after_render(self, clip: dict) -> dict:
        """После рендера (уже вне очереди тяжёлых): заголовок, описание и хэштеги клипа, если их
        ещё нет (app/ai/publish_meta.auto_meta_after_render). Никогда не ломает рендер."""
        if not isinstance(clip, dict) or clip.get("status") != "succeeded":
            return clip
        try:
            from app.ai.publish_meta import auto_meta_after_render

            result = auto_meta_after_render(self.store, clip)
            if result.get("status") in ("generated", "inherited"):
                return self.store.get_clip(int(clip["id"]))
        except Exception:  # noqa: BLE001 - метаданные — не повод сорвать рендер
            logging.getLogger(__name__).warning("auto publish meta failed for clip %s", clip.get("id"), exc_info=True)
        return clip

    def _render_clip_plan_unlocked(
        self,
        clip_plan_id: int,
        ffmpeg_preset_id: int | None = None,
        subtitle_profile_id: int | None = None,
        banner_id: int | None = None,
        music_track_id: int | None = None,
        music_volume: float | None = None,
        subtitle_offset_sec: float | None = None,
        subtitle_provider: str | None = None,
        banner_height_frac: float | None = None,
        banner_y_frac: float | None = None,
        subtitle_margin_v: int | None = None,
        mirror: bool = False,
        transition: dict | None = None,
        cover: dict | None = None,
        layout: dict | None = None,
        music_start_sec: float | None = None,
        aspect: str | None = None,
        end_hold_sec: float | None = None,
    ) -> dict:
        """``end_hold_sec`` — стоп-кадр в конце для этого рендера (0..1.5); None — из настроек
        клипа (render_settings.end_hold_sec), там нет — RENDER_END_HOLD_SEC."""
        plan = self.store.get_clip_plan(clip_plan_id)
        segments = plan.get("segments") or []
        if not segments:
            raise ValueError("clip plan has no segments")
        # Формат вывода (app/formats.py: 9:16 / 1:1 / 4:5 / 16:9): из запроса или из настроек
        # клипа (format, у старых — aspect); проверка до статуса «rendering», как и у вырезов.
        from app.formats import normalize_format, settings_layers_format, settings_render_format

        if aspect is None:
            aspect = settings_render_format(plan.get("render_settings"))
        aspect = normalize_format(aspect)
        layers_format = settings_layers_format(plan.get("render_settings"))
        saved_format = settings_render_format(plan.get("render_settings"))
        if subtitle_margin_v is not None and saved_format != aspect:
            # Отступ субтитров из настроек — доля высоты кадра СОХРАНЁННОГО формата
            # (settings_to_render_kwargs); разовый рендер в другом формате — та же доля его кадра.
            from app.formats import frame_size

            subtitle_margin_v = round(subtitle_margin_v * frame_size(aspect)[1] / frame_size(saved_format)[1])
        end_hold = self._end_hold_sec(plan, end_hold_sec)
        # Джамп-каты (app/cuts.py): нет вырезов — None, и всё ниже как раньше.
        # До статуса «rendering»: вырезы на весь клип — ошибка запроса, а не зависший клип.
        cut_plan = self._cut_plan(plan, ffmpeg_preset_id)
        self.store.update_clip_plan_status(clip_plan_id, "rendering")
        segment_ids = [segment["id"] for segment in segments]
        # Моушн, как и вставки, живёт в настройках самого клипа. Нет слоёв и
        # камеры — рендер ровно прежний (те же команды ffmpeg).
        from app.motion import has_pngtuber, plan_motion, react_sources, visual_motion

        motion = plan_motion(plan.get("render_settings"))
        visual = visual_motion(motion)
        if visual:
            from app.motion_render import attach_host_data

            # Слоям «субтитры» и «чат» нужны данные клипа: слова речи и сообщения чата.
            # Берутся во времени клипа ДО вырезов (чат фильтруется по исходным
            # слоям), а сдвигаются ниже — вместе со слоями.
            visual = attach_host_data(self.store, plan, visual)
            if visual and visual.get("words") and subtitle_profile_id is not None:
                # Анимированные субтитры-слой заменяют прожигаемые: два текста речи
                # друг на друге — брак.
                subtitle_profile_id = None
        if cut_plan is not None:
            # Слои, камера и чат — во времени ГОТОВОГО клипа (после переходов),
            # вырезы — во времени кусков подряд: переводим туда и обратно.
            visual = self._cut_motion(plan, cut_plan, visual)
            from app.cuts import remap_motion

            motion = remap_motion(motion, self._finished_cut_mapper(plan, cut_plan))
        visual = self._with_chroma_key(plan, visual)
        if visual and has_pngtuber(visual.get("layers")):
            # PNGtuber: рот по голосу дорожки — состояния по кадрам во времени готового ролика
            # (слои уже после вырезов), секунды источника — через ClipClock (app/pngtuber.py).
            from app.pngtuber import attach_voice

            voice_fps = float(self._preset(ffmpeg_preset_id).get("fps") or 30)
            visual = attach_voice(self.store, plan, visual, voice_fps, "final")
        if visual and react_sources(visual.get("layers")):
            # Реакция слоёв на звук: огибающие громкости по кадрам готового ролика (app/audio_react.py);
            # музыка — та, что ляжет в этот рендер (выбор запроса или настройки клипа).
            from app.audio_react import attach_levels

            rs = plan.get("render_settings") or {}
            music = None
            if music_track_id is not None:
                music = {"track_id": music_track_id,
                         "start_sec": music_start_sec if music_start_sec is not None else rs.get("music_start_sec")}
            visual = attach_levels(self.store, plan, visual, float(self._preset(ffmpeg_preset_id).get("fps") or 30),
                                   "final", music)
        # Стоп-кадры посреди ролика (app/freeze.py): основа получит их в render_*; здесь —
        # слои «continue» сдвигаются за вставку, звуки слоёв — за любую вставку.
        freezes = self._freezes(plan)
        if freezes:
            from app.cuts import remap_motion
            from app.freeze import FreezeMap, holds_layers, shift_voice

            def running(f: dict) -> bool:
                return not holds_layers(f)

            if visual:
                moved = remap_motion(visual, FreezeMap(freezes, only=running))
                if visual.get("voice"):
                    moved = {**moved, "voice": shift_voice(visual["voice"], freezes, only=running)}
                if visual.get("levels"):
                    from app.audio_react import shift_levels

                    moved = {**moved, "levels": shift_levels(visual["levels"], freezes, only=running)}
                visual = moved
            motion = remap_motion(motion, FreezeMap(freezes))
        # Раскладка стрима — тоже из настроек клипа (single = прежний рендер).
        if layout is None:
            layout = (plan.get("render_settings") or {}).get("layout")
        if music_start_sec is None:
            # Смещение старта музыки — из настроек клипа, как раскладка.
            music_start_sec = (plan.get("render_settings") or {}).get("music_start_sec")
        if not (visual or {}).get("key") and needs_zones(layout, segments):
            # Анимированный фон meme / куска «шире окна» рисует компоновщик — он нужен
            # и клипу без слоёв (зоны фона считает рендер, app/backgrounds.py).
            visual = visual or {"layers": [], "camera": []}
        # Звуковой монтаж (app/audio_edit.py): дорожки, эффекты, J/L, ключи — None, если его нет.
        audio_plan = self._audio_plan(plan, cut_plan)
        # Сведение (app/audio_mix.py: auto_mix / set_mix) — в проходы музыки, звуков слоёв и громкости.
        from app.audio_mix import normalize_mix

        mix = normalize_mix((plan.get("render_settings") or {}).get("mix"))
        pass_mix = dict(mix)
        if pass_mix.get("voice_fx"):
            # Цепочка голоса: у холста — на слоях-голосе (проход звуков слоёв), у клипа с исходником —
            # своим проходом по звуку основы (_finalize_render, до музыки).
            from app.canvas import is_canvas

            try:
                canvas_clip = is_canvas(self.store.get_source(int(plan["source_id"])))
            except (KeyError, TypeError, ValueError):
                canvas_clip = True
            if canvas_clip:
                pass_mix.pop("voice_fx")
        if pass_mix:
            audio_plan = {**(audio_plan or {"notes": []}), "mix": pass_mix}
        from app.piece_color import has_fix
        from app.piece_speed import is_retimed

        # Кусок со скоростью/реверсом (app/piece_speed.py) или своей цветокоррекцией
        # (app/piece_color.py) — путь монтажа: части несут скорость и поправку цвета.
        retimed_parts = (list(segments) if cut_plan is None
                         and any(is_retimed(s) or has_fix(s) for s in segments) else None)
        if retimed_parts is not None:
            clip = self.render_montage(
                segment_ids,
                ffmpeg_preset_id=ffmpeg_preset_id,
                subtitle_profile_id=subtitle_profile_id,
                banner_id=banner_id,
                clip_plan_id=clip_plan_id,
                title=plan["title"],
                description=plan["description"],
                music_track_id=music_track_id,
                music_volume=music_volume,
                music_start_sec=music_start_sec,
                subtitle_offset_sec=subtitle_offset_sec,
                subtitle_provider=subtitle_provider,
                banner_height_frac=banner_height_frac,
                banner_y_frac=banner_y_frac,
                subtitle_margin_v=subtitle_margin_v,
                mirror=mirror,
                transition=transition,
                cover=cover,
                motion=visual,
                layout=layout,
                cut_parts=retimed_parts,
                aspect=aspect,
                end_hold_sec=end_hold,
                layers_format=layers_format,
                freezes=freezes,
                **({"audio_edit": audio_plan} if audio_plan else {}),
            )
        elif cut_plan is not None:
            # Куски делятся на части в обход вырезов — это всегда путь монтажа.
            clip = self.render_montage(
                segment_ids,
                ffmpeg_preset_id=ffmpeg_preset_id,
                subtitle_profile_id=subtitle_profile_id,
                banner_id=banner_id,
                clip_plan_id=clip_plan_id,
                title=plan["title"],
                description=plan["description"],
                music_track_id=music_track_id,
                music_volume=music_volume,
                music_start_sec=music_start_sec,
                subtitle_offset_sec=subtitle_offset_sec,
                subtitle_provider=subtitle_provider,
                banner_height_frac=banner_height_frac,
                banner_y_frac=banner_y_frac,
                subtitle_margin_v=subtitle_margin_v,
                mirror=mirror,
                transition=transition,
                cover=cut_plan.remap_cover(cover),
                motion=visual,
                layout=layout,
                cut_parts=cut_plan.parts,
                jump_joins=cut_plan.jump_joins,
                aspect=aspect,
                end_hold_sec=end_hold,
                layers_format=layers_format,
                freezes=freezes,
                **({"audio_edit": audio_plan} if audio_plan else {}),
            )
        elif len(segment_ids) == 1:
            clip = self.render_segment(
                segment_ids[0],
                ffmpeg_preset_id=ffmpeg_preset_id,
                subtitle_profile_id=subtitle_profile_id,
                banner_id=banner_id,
                clip_plan_id=clip_plan_id,
                music_track_id=music_track_id,
                music_volume=music_volume,
                music_start_sec=music_start_sec,
                subtitle_offset_sec=subtitle_offset_sec,
                subtitle_provider=subtitle_provider,
                banner_height_frac=banner_height_frac,
                banner_y_frac=banner_y_frac,
                subtitle_margin_v=subtitle_margin_v,
                mirror=mirror,
                cover=cover,
                motion=visual,
                layout=layout,
                aspect=aspect,
                end_hold_sec=end_hold,
                layers_format=layers_format,
                freezes=freezes,
                **({"audio_edit": audio_plan} if audio_plan else {}),
            )
        else:
            clip = self.render_montage(
                segment_ids,
                ffmpeg_preset_id=ffmpeg_preset_id,
                subtitle_profile_id=subtitle_profile_id,
                banner_id=banner_id,
                clip_plan_id=clip_plan_id,
                title=plan["title"],
                description=plan["description"],
                music_track_id=music_track_id,
                music_volume=music_volume,
                music_start_sec=music_start_sec,
                subtitle_offset_sec=subtitle_offset_sec,
                subtitle_provider=subtitle_provider,
                banner_height_frac=banner_height_frac,
                banner_y_frac=banner_y_frac,
                subtitle_margin_v=subtitle_margin_v,
                mirror=mirror,
                transition=transition,
                cover=cover,
                motion=visual,
                layout=layout,
                aspect=aspect,
                end_hold_sec=end_hold,
                layers_format=layers_format,
                freezes=freezes,
                **({"audio_edit": audio_plan} if audio_plan else {}),
            )
        if clip["status"] == "succeeded" and (audio_plan or {}).get("notes"):
            clip = self._note_clip_error(clip["id"], "; ".join(audio_plan["notes"]))
        if clip["status"] == "succeeded":
            clip = self._apply_mute(clip, plan, cut_plan, audio_plan)
            clip = self._apply_inserts(clip, plan, cut_plan)
            if motion:
                clip = self._apply_audio_layers(clip, motion, plan)
            # Цель клипа (mix.target_lufs) — только если задана: без неё вызов ровно прежний.
            clip = self._normalize_loudness(clip, hold=end_hold, **({"target": mix["target_lufs"]}
                                                                   if mix.get("target_lufs") is not None else {}))
        if clip["title"] != plan["title"] or clip["description"] != plan["description"]:
            clip = self.store.update_clip(
                clip["id"],
                title=plan["title"],
                description=plan["description"],
            )
        self.store.update_clip_plan_status(
            clip_plan_id,
            "rendered" if clip["status"] == "succeeded" else "failed",
        )
        return clip

    def _with_chroma_key(self, plan: dict, visual: dict | None) -> dict | None:
        """Источник на зелёнке — ключ и фон клипа едут в компоновщик (app/chroma_key.py)."""
        from app.chroma_key import normalize_backdrop

        segments = plan.get("segments") or []
        try:
            key = self.store.get_source(int(segments[0]["source_id"])).get("chroma_key") if segments else None
        except (KeyError, ValueError, TypeError):
            key = None
        if not key:
            return visual
        backdrop = normalize_backdrop((plan.get("render_settings") or {}).get("backdrop"))
        return {**(visual or {"layers": [], "camera": []}), "key": key, "background": backdrop}

    def _cut_plan(self, plan: dict, ffmpeg_preset_id: int | None = None, fps: float | None = None):
        """План джамп-катов клипа (app/cuts.py) или None, если вырезов нет.

        Пресет читается только при вырезах: без них рендер не делает ни
        одного лишнего шага."""
        from app.cuts import normalize_cuts, plan_cuts

        cuts = normalize_cuts((plan.get("render_settings") or {}).get("cuts"))
        if not cuts:
            return None
        if fps is None:
            fps = float(self._preset(ffmpeg_preset_id).get("fps") or 30)
        return plan_cuts(plan.get("segments") or [], cuts, fps)

    @staticmethod
    def _freezes(plan: dict | None) -> list[dict]:
        """Стоп-кадры посреди ролика из настроек клипа (app/freeze.py)."""
        from app.freeze import normalize_freezes

        return normalize_freezes(((plan or {}).get("render_settings") or {}).get("freezes"))

    def _final_mapper(self, plan: dict, cut_plan=None):
        """Время готового ролика без вырезов и стоп-кадров → в готовом ролике (None — то же)."""
        from app.freeze import chain

        cut = self._finished_cut_mapper(plan, cut_plan) if cut_plan is not None else None
        return chain(cut, self._freezes(plan))

    @staticmethod
    def _finished_cut_mapper(plan: dict, cut_plan):
        """Время готового клипа без вырезов → с вырезами (app/cuts.FinishedCutMap)."""
        transition = (plan.get("render_settings") or {}).get("transition")
        return cut_plan.finished_mapper(plan.get("segments") or [], transition)

    def _cut_motion(self, plan: dict, cut_plan, visual: dict | None) -> dict | None:
        """Видимый моушн (слои, камера, слова, чат) — во время клипа после вырезов."""
        from app.cuts import cut_caption_words, remap_motion

        if not visual:
            return visual
        had_words = bool(visual.get("words"))
        mapper = self._finished_cut_mapper(plan, cut_plan)
        fresh = remap_motion(visual, mapper)
        if had_words:
            # Слова пересчитываются из субтитров: вырез — до сдвига на переходы.
            fresh = {**fresh, "words": cut_caption_words(self.store, plan, cut_plan)}
            # Подсветка слов — номера в словах ДО вырезов: после них слово с тем же номером
            # уже другое (app/caption_marks.py) — переставляем на то же слово.
            from app.caption_marks import rebind_layers

            old = [{"w": w["w"], "t": float(w["t0"])} for w in visual.get("words") or []]
            new = [{"w": w["w"], "t": float(w["t0"])} for w in fresh.get("words") or []]
            layers, _lost = rebind_layers(fresh.get("layers") or [], old, new, time_map=mapper)
            fresh = {**fresh, "layers": layers}
        # Слой целиком в вырезе пропал — может не остаться ничего видимого.
        return fresh if (fresh.get("layers") or fresh.get("camera") or fresh.get("end_screen")) else None

    def _plan_subtitles_for_clip(self, clip_id: int, fps: float | None = None):
        """Субтитры, сделанные в редакторе до рендера (если есть)."""
        from app.clip_subtitles import result_from_payload
        from app.cuts import plan_joins_setting, subtitles_to_final

        try:
            clip = self.store.get_clip(clip_id)
            plan_id = clip.get("clip_plan_id")
            if not plan_id:
                return None
            payload = self.store.get_clip_plan_subtitles(int(plan_id))
            if not payload or not (payload.get("lines") or payload.get("words")):
                return None
            plan = self.store.get_clip_plan(int(plan_id))
            transition = (plan.get("render_settings") or {}).get("transition")
            cut_plan = self._cut_plan(plan, clip.get("ffmpeg_preset_id"), fps)
            # Слова лежат в склейке кусков: сначала вырезы, потом сдвиг на
            # настоящие overlap склеек (как их спланирует рендер).
            final = subtitles_to_final(payload, plan.get("segments") or [], transition, cut_plan,
                                       plan_joins_setting(plan))
            freezes = self._freezes(plan)
            remap = None
            if freezes:
                # Вставленный стоп-кадр: речь после него звучит позже (app/freeze.py).
                from app.cuts import remap_subtitles
                from app.freeze import FreezeMap

                fmap = FreezeMap(freezes)
                final = remap_subtitles(final, fmap)
                remap = lambda p: remap_subtitles(p, fmap)  # noqa: E731
            result = result_from_payload(final)
            # Перевод с оригиналом (app/translation.py): строки оригинала — вторым стилем ASS.
            from app.translation import secondary_lines

            extra = secondary_lines(payload, plan.get("segments") or [], transition, cut_plan, remap,
                                    joins=plan_joins_setting(plan))
            if extra:
                from dataclasses import replace

                result = replace(result, response={**result.response, "secondary_lines": extra})
            return result
        except (KeyError, ValueError, TypeError):
            return None

    def _apply_mute(self, clip: dict, plan: dict, cut_plan=None, audio_plan: dict | None = None) -> dict:
        """Тишина и громкость звука исходника на отрезках (``render_settings.mute`` и ``gain``,
        app/audio_mute.py) — одним проходом.

        До вставок и звуков слоёв: они ложатся поверх и играют. Сбой не теряет клип."""
        from app.audio_mute import build_source_volume_args, final_pass_mutes, normalize_gain, span_floor

        settings_ = plan.get("render_settings") or {}
        # Отрезки одной дорожки (track) и мат со стилем-эффектом (писк, шум, реверс…) уже
        # наложены при рендере кусков (app/audio_edit.py); не наложены (холст) — тишиной.
        fx_rendered = any(op.get("by") == "censor" for op in (audio_plan or {}).get("ops") or [])
        mutes = final_pass_mutes(settings_.get("mute"), fx_rendered)
        gains = [g for g in normalize_gain(settings_.get("gain")) if g.get("track") is None]
        path = Path(clip.get("output_path") or "")
        if not (mutes or gains) or not path.exists() or not _has_audio_stream(path):
            return clip
        spans = [(m["start"], m["end"], span_floor(m)) for m in mutes]
        boosts = [(g["start"], g["end"], g["gain_db"]) for g in gains]
        mapper = self._final_mapper(plan, cut_plan)
        if mapper is not None:
            spans = [(*r, k) for r, k in ((mapper.range(a, b), k) for a, b, k in spans) if r]
            boosts = [(*r, g) for r, g in ((mapper.range(a, b), g) for a, b, g in boosts) if r]
        try:
            tmp = path.with_name(path.stem + ".mute" + path.suffix)
            args = build_source_volume_args(path, tmp, spans, boosts)
            if args is None:
                return clip
            _run_ffmpeg(args, timeout=60 * 20)
            tmp.replace(path)
            return self.store.update_clip(clip["id"], size_bytes=path.stat().st_size)
        except Exception as exc:  # noqa: BLE001 - тишина и громкость — надстройка, клип важнее
            return self._note_clip_error(clip["id"], f"тишина/громкость не наложены: {str(exc)[:200]}")

    # ---- звуковой монтаж: J/L, дорожки, ключи, эффекты (app/audio_edit.py) ----

    def _audio_plan(self, plan: dict, cut_plan=None) -> dict | None:
        """План звукового монтажа клипа для рендера; None — рендер звука прежний."""
        from app.audio_edit import render_plan, wants_render_plan

        if not wants_render_plan(plan.get("render_settings") or {}):
            return None
        try:
            source = self.store.get_source(int(plan["source_id"]))
        except (KeyError, TypeError, ValueError):
            return None
        return render_plan(plan, source, self._final_mapper(plan, cut_plan), cut_plan)

    def _jl_handles(self, source: dict, preset: dict, parts: list[dict], durations: list[float], join: dict,
                    jumps: list[bool] | None, shifts: list[float], audio_edit: dict | None, temp_dir: Path) -> dict:
        """J/L: «ручки» звука из исходника (секунды до начала / после конца части) — тем же
        графом звука, что у частей (дорожки, операции). Не прочиталась — тишина нужной длины."""
        from app.audio_edit import handle_windows, part_edit, plan_jl
        from app.transitions import plan_joins

        overlaps = plan_joins(join, durations, _source_gaps(parts), jumps)
        plan = plan_jl(durations, overlaps, shifts, join["audio"], jumps)
        clip0 = list((audio_edit or {}).get("clip0") or [])
        handles = []
        for item in handle_windows(parts, plan):
            k, start, length = item["part"], item["start"], item["length"]
            path = temp_dir / f"handle-{k:03d}-{item['side']}.wav"
            edit = part_edit(audio_edit, k, max(0.0, start), max(0.0, start) + length)
            if edit and k < len(clip0):
                edit["clip0"] = round(clip0[k] + (max(0.0, start) - float(parts[k]["start_sec"])), 3)
            piece_preset = {**preset, "audio_edit": edit} if edit else preset
            try:
                _run_ffmpeg(build_audio_handle_args(Path(source["local_path"]), path, start, length, piece_preset),
                            timeout=60 * 5)
            except Exception:  # noqa: BLE001 - ручки нет (конец записи) — тишина, склейка цела
                _run_ffmpeg(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                             "anullsrc=r=48000:cl=stereo", "-t", f"{length:.3f}", str(path)], timeout=60)
            handles.append((k, item["side"], path))
        return {"plan": plan, "handles": handles}

    def _apply_inserts(self, clip: dict, plan: dict, cut_plan=None) -> dict:
        """Overlay the clip's «Файлы для монтажа» inserts on the finished render.

        One extra ffmpeg pass on the output file; a failure keeps the render
        without inserts and says so in the clip's error, never loses the clip.
        """
        from app.montage_assets import build_inserts_args

        inserts, assets = self._final_inserts(plan, cut_plan)
        path = Path(clip.get("output_path") or "")
        if not inserts or not path.exists():
            return clip
        try:
            meta = probe_media(path)
            has_audio = _has_audio_stream(path)
            tmp = path.with_name(path.stem + ".inserts" + path.suffix)
            args = build_inserts_args(
                path, tmp, inserts, assets, meta.width, meta.height, meta.duration_sec, clip_has_audio=has_audio
            )
            if args is None:
                return clip
            _run_ffmpeg(args, timeout=60 * 20)
            tmp.replace(path)
            meta = probe_media(path)
            clip = self.store.update_clip(clip["id"], size_bytes=path.stat().st_size, duration_sec=meta.duration_sec)
            return self._attach_qc(clip, path, meta)
        except Exception as exc:  # noqa: BLE001 - inserts are an extra, never lose the clip
            return self._note_clip_error(clip["id"], f"вставки не наложены: {str(exc)[:200]}")

    def _final_inserts(self, plan: dict, cut_plan=None) -> tuple[list[dict], dict]:
        """Вставки клипа в секундах готового ролика и их файлы."""
        from app.montage_assets import normalize_inserts

        inserts = normalize_inserts((plan.get("render_settings") or {}).get("inserts"))
        mapper = self._final_mapper(plan, cut_plan)
        if mapper is not None and inserts:
            from app.cuts import remap_inserts

            inserts = remap_inserts(inserts, mapper)
        assets = {}
        for ins in inserts:
            try:
                assets[ins["asset_id"]] = self.store.get_montage_asset(ins["asset_id"])
            except KeyError:
                pass
        return inserts, assets

    @staticmethod
    def _apply_freezes(base: Path, preset: dict) -> None:
        """Стоп-кадры посреди ролика (app/freeze.py) — в основу, до компоновщика, субтитров и
        музыки: музыка играет и на вставленном стоп-кадре, слои и субтитры ложатся уже на него."""
        from app.freeze import build_freeze_args

        freezes = preset.get("freezes") or []
        if not freezes:
            return
        meta = probe_media(base)
        tmp = base.with_name(base.stem + ".freeze" + base.suffix)
        args = build_freeze_args(base, tmp, freezes, float(preset.get("fps") or 30), _has_audio_stream(base),
                                 float(meta.duration_sec or 0))
        if args is None:
            return
        _run_ffmpeg(args, timeout=60 * 20)
        tmp.replace(base)

    def _note_clip_error(self, clip_id: int, text: str) -> dict:
        """Дописать пометку в ошибку удачного клипа (моушн, вставки), не затирая прежние."""
        current = str(self.store.get_clip(clip_id).get("error") or "").strip()
        merged = f"{current}; {text}" if current else text
        return self.store.update_clip(clip_id, error=merged[:1000])

    def _apply_motion(
        self,
        render_id: str,
        video: Path,
        preset: dict,
        banner: dict | None,
        motion: dict,
        source: dict | None = None,
    ) -> list[str]:
        """Камера и слои поверх основы (на месте ``video``), затем баннер.

        Основа отрендерена без баннера — он ложится тут, поверх слоёв, чтобы
        камера его не зумила. Падение компоновщика не теряет клип: остаётся
        основа (с баннером), а причина — в пометках для ошибки клипа.
        Возвращает пометки.
        """
        from app.end_screen import comp_end_screen
        from app.formats import comp_ref, motion_formats
        from app.freeze import comp_freezes, freeze_zones
        from app.motion_render import (
            build_compositor_args,
            build_motion_mux_args,
            prepare_video_frames,
            reframe_for_output,
            resolve_layer_assets,
            unit_width,
            write_comp,
        )

        notes: list[str] = []
        work = self._tmp_dir() / f"motion-{render_id}"
        work.mkdir(parents=True, exist_ok=True)
        try:
            layers, assets, missing = resolve_layer_assets(self.store, motion.get("layers") or [])
            notes.extend(missing)
            notes.extend(motion.get("host_notes") or [])
            assets.update(motion.get("chat_assets") or {})
            camera = motion.get("camera") or []
            band = motion.get("pin_band")
            drawn = video
            if layers or camera or motion.get("key") or motion.get("backgrounds") or motion.get("end_screen"):
                try:
                    meta = probe_media(video)
                    comp_w = meta.width or int(preset.get("output_width") or 1080)
                    comp_h = meta.height or int(preset.get("output_height") or 1920)
                    if band:
                        comp_h = int(preset.get("output_height") or 1920)  # основа выше на полосу окошка
                    regions = layout_camera_regions(preset, source, comp_w, comp_h) if camera else None
                    # Слои размечены в другом формате (9:16 → квадрат / 16:9 и т.п.) —
                    # переносим в новый опорный кадр одним масштабом по содержимому
                    # (app/aspect.py), камеру — тем же переносом; тот же формат — как есть.
                    # Тот же путь, что у кадра агента (view_frame).
                    out_format = motion_formats(preset)[0]
                    layers, camera = reframe_for_output(layers, camera, preset, assets)
                    comp = write_comp(
                        work / "comp.json",
                        width=comp_w,
                        height=comp_h,
                        ref=comp_ref(out_format),
                        fps=float(preset.get("fps") or 30),
                        duration=meta.duration_sec,
                        layers=layers,
                        camera=camera,
                        words=motion.get("words"),
                        chat=motion.get("chat"),
                        voice=motion.get("voice"),
                        levels=motion.get("levels"),
                        key=motion.get("key"),
                        background=motion.get("background"),
                        backgrounds=freeze_zones(motion.get("backgrounds"), preset.get("freezes")),
                        camera_box=(regions or {}).get("box"),
                        pinned=banded_pinned((regions or {}).get("pinned"), band, comp_h),
                        base_height=comp_h + int(band["h"]) if band else None,
                        freezes=comp_freezes(preset.get("freezes")),
                        end_screen=comp_end_screen(motion.get("end_screen"), layers, meta.duration_sec),
                        motion_blur=motion.get("motion_blur"),
                    )
                    assets_path = work / "assets.json"
                    assets_path.write_text(json.dumps(assets, ensure_ascii=False), encoding="utf-8")
                    # Видео-слои: кадры их отрезков заранее (удаляются вместе с work).
                    videos_path, video_notes = prepare_video_frames(
                        layers, assets, work, fps=float(preset.get("fps") or 30),
                        out_width=unit_width(comp_w, out_format), clip_duration=meta.duration_sec,
                        fmt=out_format,
                    )
                    notes.extend(video_notes)
                    # Анимации с альфой (PNGtuber, GIF в image): PNG-кадры (удаляются вместе с work).
                    from app.anim_frames import prepare_anim_frames

                    anims_path, anim_notes = prepare_anim_frames(layers, assets, work,
                                                                 out_width=unit_width(comp_w, out_format))
                    notes.extend(anim_notes)
                    out = work / "motion.mp4"
                    _run_compositor(
                        build_compositor_args(
                            comp, assets_path, input_path=video, output_path=out,
                            crf=_json_dict(preset.get("extra_json")).get("crf"), videos_path=videos_path,
                            anims_path=anims_path,
                        ),
                        timeout=settings.motion_timeout_seconds,
                    )
                    if not out.is_file() or out.stat().st_size == 0:
                        raise RuntimeError("компоновщик не записал видео")
                    drawn = out
                except Exception as exc:  # noqa: BLE001 - моушн — надстройка, клип важнее
                    notes.append(f"моушн не наложен: {_safe_error(exc)[:300]}")
            if drawn is video and band:
                # Компоновщик не справился — собрать прежний кадр: окошко из полосы на место.
                drawn = work / "unbanded.mp4"
                _run_ffmpeg(build_unband_args(video, drawn, band, int(preset.get("output_height") or 1920)),
                            timeout=60 * 20)
            if drawn is video and not banner:
                return notes
            muxed = work / "muxed.mp4"
            try:
                _run_ffmpeg(build_motion_mux_args(drawn, video, muxed, preset, banner), timeout=60 * 20)
            except Exception as exc:  # noqa: BLE001
                if drawn is video:
                    raise  # баннер на голую основу не лёг — это настоящая ошибка рендера
                notes.append(f"моушн не наложен: {_safe_error(exc)[:300]}")
                if not banner:
                    return notes
                _run_ffmpeg(build_motion_mux_args(video, video, muxed, preset, banner), timeout=60 * 20)
            shutil.move(str(muxed), str(video))
            return notes
        finally:
            shutil.rmtree(work, ignore_errors=True)

    @staticmethod
    def _end_hold_sec(plan: dict | None = None, override: float | None = None) -> float:
        """Стоп-кадр в конце ролика; его кладёт финальный проход звука — без него
        (RENDER_LOUDNORM=0) стоп-кадра нет. Сколько: ``override`` (параметр рендера), иначе
        настройка клипа ``end_hold_sec`` (мему — 0: концовка без «замирания»), иначе
        RENDER_END_HOLD_SEC. Свои значения клипа — в рамках 0..END_HOLD_MAX_SEC."""
        if not settings.render_loudnorm:
            return 0.0
        from app.clip_settings import normalize_end_hold

        value = normalize_end_hold(override)
        if value is None and plan is not None:
            value = normalize_end_hold((plan.get("render_settings") or {}).get("end_hold_sec"))
        if value is None:
            value = float(settings.render_end_hold_sec or 0)
        return max(0.0, value)

    def _apply_audio_layers(self, clip: dict, motion: dict, plan: dict | None = None) -> dict:
        """Звуки ``audio``-слоёв и звук ``video``-слоёв с ``props.audio`` в готовый клип —
        последним проходом, видео копией.

        Последним, а не в моушн-проходе: субтитры распознаются по звуку основы,
        и бум/свист поверх речи мешал бы распознаванию (как и музыка). Ошибка не
        теряет клип — пишется в его ошибку.
        """
        from app.motion import has_layer_sound
        from app.motion_render import build_audio_layers_args, resolve_layer_assets

        layers = [layer for layer in motion.get("layers") or [] if has_layer_sound(layer)]
        path = Path(clip.get("output_path") or "")
        if not layers or not path.exists():
            return clip
        try:
            layers, assets, notes = resolve_layer_assets(self.store, layers)
            meta = probe_media(path)
            tmp = path.with_name(path.stem + ".sfx" + path.suffix)
            from app.audio_mix import normalize_mix
            from app.motion_render import plan_is_speechless, speech_level_db

            has_audio = _has_audio_stream(path)
            # Без речи (игра) «голос» — громкие места клипа: эффект чуть ниже них, а не на 10 дБ.
            speechless = bool(plan) and has_audio and plan_is_speechless(self.store, plan)
            effects = any(layer["type"] == "audio" and not layer["props"].get("mode") == "track" for layer in layers)
            args = build_audio_layers_args(
                path, tmp, layers, assets, meta.duration_sec, clip_has_audio=has_audio,
                # Уровень голоса нужен только эффектам (дорожки без автоподгонки) — без них не меряем.
                speech_db=speech_level_db(path) if has_audio and effects else None, speechless=speechless,
                notes=notes, mix=normalize_mix(((plan or {}).get("render_settings") or {}).get("mix")),
            )
            if args is not None:
                _run_ffmpeg(args, timeout=60 * 20)
                tmp.replace(path)
                meta = probe_media(path)
                clip = self.store.update_clip(clip["id"], size_bytes=path.stat().st_size, duration_sec=meta.duration_sec)
                clip = self._attach_qc(clip, path, meta)
            if notes:
                clip = self._note_clip_error(clip["id"], "; ".join(notes))
            return clip
        except Exception as exc:  # noqa: BLE001 - звуки слоёв — надстройка, клип важнее
            return self._note_clip_error(clip["id"], f"звуки слоёв не наложены: {str(exc)[:200]}")

    def _normalize_loudness(self, clip: dict, hold: float | None = None, target: float | None = None) -> dict:
        """Громкость под площадки: −14 LUFS и пик не выше −1.5 dBFS.

        Тихий рэп выходил на −20 LUFS, а клип со звуками — с пиком выше нуля:
        TikTok/Shorts всё равно приводят громкость к своей, только тихий ролик
        тогда звучит хуже соседей, а громкий хрипит. Два прохода loudnorm:
        замер, потом линейная поправка по замеру — без «качелей» громкости.
        Заодно — финал: стоп-кадр ``hold`` (None — RENDER_END_HOLD_SEC; у клипа — его
        end_hold_sec, ``_end_hold_sec``) и затухание звука на нём.
        Пики — по true peak (лимитер на ×4 частоте), готовый файл проверяется замером
        и при перебое пересобирается с потолком ниже.
        Сбой не теряет клип.
        """
        if not settings.render_loudnorm:  # RENDER_LOUDNORM=0 выключает весь финальный проход звука
            return clip
        path = Path(clip.get("output_path") or "")
        if not path.exists() or not _has_audio_stream(path):
            return clip
        # Цель клипа (render_settings.mix.target_lufs, app/audio_mix.py) или сервиса (−14).
        target = float(target) if target is not None else settings.render_loudnorm_target
        try:
            measured = _measure_loudness(path, target)
            if not audible(measured):
                return clip  # немой клип: ни громкость, ни затухание не нужны
            meta = probe_media(path)
            # Музыка уже доиграна на стоп-кадр (проход музыки, _music_filter с hold_sec):
            # звук длиннее картинки — стоп-кадр ровно на этот хвост, тишину не добавляем.
            tail = music_tail_sec(meta)
            hold = tail if tail > 0 else (self._end_hold_sec() if hold is None else max(0.0, float(hold)))
            pad = [f"apad=pad_dur={hold:.3f}"] if hold > 0 and tail <= 0 else []
            import logging

            needed = loudnorm_needed(measured, target)
            # Пики за потолком (ударные без голоса): контрольный замер цепочки и, если
            # лимитер съел громкость, компрессор с подъёмом (fit_loudness).
            plan = fit_loudness(measured, target, lambda f: _measure_loudness(path, target, f)) if needed else None
            logging.getLogger("uvicorn.error").info(
                "loudness clip=%s input_i=%s input_tp=%s needed=%s plan=%s tail=%s",
                clip.get("id"), measured.get("input_i"), measured.get("input_tp"), needed, plan, tail,
            )
            # Затухание — на конце самого звука: без музыки это конец речи (стоп-кадр
            # после — тишина, иначе на речь приходилась 0.1 с и звук обрывался), с
            # музыкой — конец её хвоста на стоп-кадре, то есть конец файла.
            fade = end_fade_filter(float(meta.duration_sec or 0))
            # Стоп-кадр в конце: последний кадр держится, пока затухает звук —
            # владелец: «сейчас она просто обрывается жёстко». Тогда картинку надо
            # перекодировать (tpad), иначе — копия без потерь.
            video = (["-vf", f"tpad=stop_mode=clone:stop_duration={hold:.3f}", "-c:v", "libx264", "-crf", "18",
                      "-preset", "veryfast", "-pix_fmt", "yuv420p"] if hold > 0 else ["-c:v", "copy"])
            tmp = path.with_name(path.stem + ".loud" + path.suffix)
            ceiling = TRUE_PEAK_LIMIT_DB
            attempts = 3
            for attempt in range(attempts):
                filters = list(pad)
                if plan:
                    # Только линейная поправка (одно число на весь клип) + лимитер на пики.
                    # loudnorm второго прохода, когда линейно не влезает в пик, молча
                    # переходит в динамический режим и вытягивает тихие места на 10–14 дБ:
                    # фоновые звуки и эффекты становились громче речи («звуки пердежа
                    # во всю громкость»). Компрессор — только когда пики упираются в
                    # потолок сильнее, чем лимитер срежет без потерь (loudness_plan).
                    filters.extend(loudness_filters({**plan, "ceiling_db": ceiling}))
                if fade:
                    filters.append(fade)
                if not filters:
                    return clip
                af = ",".join(filters + [f"aresample={OUTPUT_SAMPLE_RATE}", "aformat=sample_fmts=fltp:channel_layouts=stereo"])
                _run_ffmpeg(
                    ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(path), "-map", "0:v:0",
                     "-map", "0:a:0", *video, "-af", af, "-c:a", "aac", "-b:a", FINAL_AUDIO_BITRATE,
                     "-movflags", "+faststart",
                     str(tmp)],
                    timeout=60 * 10,
                )
                # Проверка готового файла — true peak после AAC: пробил потолок —
                # ещё один проход с лимитером ниже на перебор. Тише цели при запасе по
                # пикам — ещё один проход с подъёмом после лимитера (finished_loudness_lift).
                after = _measure_loudness(tmp, target)
                retry = true_peak_retry_ceiling(after.get("input_tp"), ceiling)
                if retry is None:
                    lift = finished_loudness_lift(after, plan, target)
                    if not lift or attempt == attempts - 1:
                        break
                    logging.getLogger("uvicorn.error").info(
                        "loudness clip=%s finished %s LUFS at %s dBTP: lift %.2f dB", clip.get("id"),
                        after.get("input_i"), after.get("input_tp"), lift,
                    )
                    plan = {**plan, "post_gain_db": round(float(plan.get("post_gain_db") or 0.0) + lift, 2)}
                    continue
                if attempt == attempts - 1:
                    break
                logging.getLogger("uvicorn.error").info(
                    "loudness clip=%s true peak over %.1f dBTP: limiter %.2f -> %.2f", clip.get("id"),
                    LOUDNESS_PEAK_DB, ceiling, retry,
                )
                plan = plan or {"gain_db": 0.0, "compressor": None, "makeup_db": 0.0}
                ceiling = retry
            tmp.replace(path)
            meta = probe_media(path)
            clip = self.store.update_clip(clip["id"], size_bytes=path.stat().st_size, duration_sec=meta.duration_sec)
            return self._attach_qc(clip, path, meta)
        except Exception as exc:  # noqa: BLE001 - громкость — надстройка, клип важнее
            return self._note_clip_error(clip["id"], f"громкость/затухание не применены: {str(exc)[:200]}")

    def _render_subtitles(
        self,
        clip_id: int,
        input_path: Path,
        output_path: Path,
        subtitle_profile_id: int,
        preset: dict,
    ) -> None:
        profile = self._subtitle_profile_for_render(clip_id, subtitle_profile_id, preset)
        ready = self._plan_subtitles_for_clip(clip_id, float(preset.get("fps") or 30))
        provider = get_subtitle_provider(profile["provider"])
        model = subtitle_model_for_profile(profile)
        track = self.store.create_subtitle_track(
            clip_id,
            provider=provider.provider,
            subtitle_profile_id=subtitle_profile_id,
            model=model,
            status="running",
        )
        self.store.update_clip(clip_id, subtitle_track_id=track["id"])
        return self._burn_subtitles(clip_id, input_path, output_path, profile, ready, provider, model, track, preset)

    def _subtitle_profile_for_render(self, clip_id: int, subtitle_profile_id: int, preset: dict) -> dict:
        """Стиль субтитров этого рендера: поправки запроса и отступ под формат кадра."""
        profile = self.store.get_subtitle_profile(subtitle_profile_id)
        profile["prompt"] = _subtitle_prompt_for_clip(self.store, clip_id)
        # A per-render nudge (set on the preset) overrides the profile's saved
        # timing offset, so a single drifting clip can be pulled into sync without
        # changing the shared subtitle style.
        offset_override = preset.get("subtitle_offset_override")
        if offset_override is not None:
            profile = {**profile, "timing_offset_sec": offset_override}
        # Per-render engine swap: pick whisper/gemini independently of the style.
        provider_override = preset.get("subtitle_provider_override")
        if provider_override:
            profile = {**profile, "provider": provider_override}
        # Per-render vertical position so the burned subtitles match the preview band.
        margin_override = preset.get("subtitle_margin_v_override")
        if margin_override is not None:
            profile = {**profile, "margin_v": margin_override}
        elif preset.get("format"):
            # Формат не 9:16: отступ стиля задан в px кадра 1920 — та же доля высоты кадра формата.
            height = int(preset.get("output_height") or 1920)
            profile = {**profile, "margin_v": round(int(profile.get("margin_v") or 160) * height / 1920)}
        return profile

    def _burn_subtitles(self, clip_id: int, input_path: Path, output_path: Path, profile: dict, ready: Any,
                        provider: Any, model: Any, track: dict, preset: dict) -> None:
        audio_path = settings.tmp_dir / f"{uuid4().hex}.wav"
        ass_path = settings.subtitle_dir / f"{uuid4().hex}.ass"
        try:
            input_duration = probe_media(input_path).duration_sec
            # Always transcribe the exact audio of the clip we are about to burn
            # onto, in a single pass. Because the timestamps come from the same
            # (already cut, possibly stitched) audio they are rendered over, the
            # karaoke highlight stays locked to the speech. Per-part chunked
            # transcription used to re-seek into the stitched file and could land a
            # couple of seconds off on real concatenated media, drifting the words.
            if ready is not None:
                # Прошли правку в редакторе — жжём ровно их, без новой транскрипции.
                result = ready
                result = normalize_subtitle_timeline(result, input_duration)
            else:
                _run_ffmpeg(build_ffmpeg_extract_audio_args(input_path, audio_path), timeout=60 * 10)
                result = provider.transcribe(audio_path, profile, model)
                audio_duration = probe_media(audio_path).duration_sec
                result = normalize_subtitle_timeline(result, min(audio_duration, input_duration))
            result = _apply_subtitle_timing_offset(result, profile, input_duration)
            ass_path = write_ass_subtitles(
                result,
                profile,
                ass_path,
                width=int(preset.get("output_width") or 1080),
                height=int(preset.get("output_height") or 1920),
                # Формат вывода (только не 9:16): кегль, поля и отступ под кадр (app/subtitles/ass.py).
                fmt=preset.get("format"),
            )
            _run_ffmpeg(
                build_ffmpeg_burn_subtitle_args(input_path, output_path, ass_path.name, preset),
                timeout=60 * 20,
                cwd=ass_path.parent,
            )
            self.store.update_subtitle_track(
                track["id"],
                status="succeeded",
                transcript=result.transcript_json(),
                usage=result.usage,
                ass_path=ass_path,
                model=str(result.usage.get("model") or model),
            )
        except Exception as exc:
            self.store.update_subtitle_track(
                track["id"],
                status="failed",
                error=_safe_error(exc),
                model=model,
            )
            raise
        finally:
            audio_path.unlink(missing_ok=True)

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

    def _finalize_render(
        self,
        clip_id: int,
        render_id: str,
        base_output_path: Path,
        output_path: Path,
        preset: dict,
        subtitle_profile_id: int | None,
    ) -> tuple[Path, list[Path]]:
        """Apply optional subtitle and music passes after the base clip exists.

        Each pass reads the previous step's output and writes a new file; the
        final pass writes the canonical output_path.

        Subtitles run FIRST, so transcription always reads the clean speech-only
        audio of the base clip. If music were mixed in first, the recogniser
        would hear speech + music and place/time the words poorly. Music is mixed
        last; it ducks against the (still clean) speech track and the subtitle
        burn's video is stream-copied, so burned captions are preserved.
        Returns the final path and the list of intermediate temp files to clean.
        """

        music = _resolve_music_settings(self.store, preset)
        passes: list[str] = []
        if subtitle_profile_id:
            passes.append("subtitle")
        voice_fx = (preset.get("mix") or {}).get("voice_fx")
        if voice_fx:
            # Цепочка голоса клипа (mix.voice_fx, app/audio_fx.py) — по звуку основы после распознавания
            # субтитров (оно слышит чистую речь) и до музыки (эффект не ложится на трек).
            passes.append("voice_fx")
        if music:
            passes.append("music")
        temp_paths: list[Path] = []
        current = base_output_path
        for index, name in enumerate(passes):
            is_last = index == len(passes) - 1
            dest = output_path if is_last else self._clip_dir() / f"{render_id}.{name}.mp4"
            if name == "voice_fx":
                _run_ffmpeg(build_voice_fx_args(current, dest, voice_fx), timeout=60 * 20)
            elif name == "music":
                music_settings = dict(music)
                music_settings["duration_sec"] = probe_media(current).duration_sec
                # Стоп-кадр в конце: музыка доигрывает и затухает на нём, а не до него.
                music_settings["hold_sec"] = float(preset.get("end_hold_sec") or 0.0)
                music_settings["volume"] = music_volume_for(current, music_settings)
                _run_ffmpeg(build_ffmpeg_music_args(current, dest, music_settings), timeout=60 * 20)
            else:
                self._render_subtitles(
                    clip_id=clip_id,
                    input_path=current,
                    output_path=dest,
                    subtitle_profile_id=int(subtitle_profile_id),
                    preset=preset,
                )
            if current is not base_output_path:
                temp_paths.append(current)
            current = dest
        return current, temp_paths


def build_ffmpeg_extract_audio_args(
    input_path: Path,
    output_path: Path,
    start_sec: float | None = None,
    duration_sec: float | None = None,
    tracks: Any = None,
) -> list[str]:
    """``tracks`` — звуковые дорожки исходника (app/source_audio.py, номера или
    ``[{track, gain_db}]``); None — первая."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    from app.source_audio import is_default_mix

    if tracks and not is_default_mix(tracks):
        from app.source_audio import extract_args

        return extract_args(input_path, output_path, list(tracks), start=start_sec, duration=duration_sec)
    args = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
    ]
    if start_sec is not None:
        args.extend(["-ss", _seconds(float(start_sec))])
    args.extend(
        [
            "-i",
            str(input_path),
        ]
    )
    if duration_sec is not None:
        args.extend(["-t", _seconds(float(duration_sec))])
    args.extend(
        [
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(output_path),
        ]
    )
    return args


def build_ffmpeg_burn_subtitle_args(
    input_path: Path,
    output_path: Path,
    ass_filename: str,
    preset: dict,
) -> list[str]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        "-vf",
        f"ass={_filter_filename(ass_filename)}",
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-c:v",
        str(preset.get("video_codec") or "libx264"),
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
    ]
    video_bitrate = str(preset.get("video_bitrate") or "").strip()
    if video_bitrate:
        args.extend(["-b:v", video_bitrate])
    extra = _json_dict(preset.get("extra_json"))
    crf = extra.get("crf")
    if crf is not None:
        args.extend(["-crf", str(crf)])
    args.append(str(output_path))
    return args


def build_ffmpeg_music_args(input_path: Path, output_path: Path, music: dict) -> list[str]:
    """Mix a background music track over an already-rendered clip.

    The clip video is stream-copied (untouched); only audio is rebuilt. The
    music can loop to fill the clip, fade in/out, and duck under speech via a
    sidechain compressor keyed on the original audio.
    """

    output_path.parent.mkdir(parents=True, exist_ok=True)
    track_path = Path(music["track_path"])
    loop = bool(music.get("loop"))
    held = _music_hold(music) > 0
    args = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
    ]
    if loop:
        args.extend(["-stream_loop", "-1"])
    args.extend(["-i", str(track_path)])
    args.extend(
        [
            "-filter_complex",
            _music_filter(music),
            "-map",
            "0:v:0",
            "-map",
            "[aout]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            # Со стоп-кадром звук длиннее картинки на hold_sec (хвост музыки): -shortest
            # его бы срезал. Картинку до той же длины дотянет финальный проход (tpad).
            *([] if held else ["-shortest"]),
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )
    return args


# Смещение старта музыки — не дальше часа трека.
MAX_MUSIC_START_SEC = 3600.0

# Фон — на столько тише голоса клипа (по громким местам), ещё до приглушения под
# речь. Громкость дорожки (volume 0.5 — обычный фон) сдвигает это относительно.
MUSIC_UNDER_SPEECH_DB = 16.0
MUSIC_NOMINAL_VOLUME = 0.5


def music_volume_for(clip_path: Path, music: dict) -> float:
    """Множитель громкости музыки от голоса ЭТОГО клипа, а не абсолютный.

    С абсолютным 0.5 готовый трек (−14 LUFS) под тихой записью голоса (−32 LUFS)
    выходил громче речи — как эффекты (см. motion_render.EFFECT_UNDER_SPEECH_DB).
    """
    from app.motion_render import audio_levels, speech_level_db

    if music.get("fixed_db") is not None:
        # Громкость выставило сведение (auto_mix / set_mix music_db): ровно она, без подгонки.
        return 10 ** (max(-40.0, min(12.0, float(music["fixed_db"]))) / 20)
    raw = music.get("volume")
    user = max(0.0, float(raw if raw is not None else MUSIC_NOMINAL_VOLUME))
    if user <= 0:
        return 0.0
    speech = speech_level_db(clip_path)
    # Громкость — того куска трека, что зазвучит (со смещением старта).
    track = audio_levels(music["track_path"], seconds=60, start=float(music.get("start_sec") or 0))
    if speech is None or not track:
        return user
    gain_db = speech - MUSIC_UNDER_SPEECH_DB - track[0] + 20 * math.log10(user / MUSIC_NOMINAL_VOLUME)
    return 10 ** (max(-40.0, min(6.0, gain_db)) / 20)


def _music_hold(music: dict) -> float:
    """Стоп-кадр, на котором доигрывает музыка (0 — без него или длина клипа неизвестна)."""
    try:
        hold = float(music.get("hold_sec") or 0)
        duration = float(music.get("duration_sec") or 0)
    except (TypeError, ValueError):
        return 0.0
    return hold if hold > 0 and duration > 0 and math.isfinite(hold) else 0.0


def _music_sidechain(music: dict, music_label: str, key_label: str, out_label: str) -> str:
    """Мягкий ducking музыки под речью (sidechain по звуку клипа): только заметная речь тянет
    музыку вниз, под ней она остаётся слышна. ``duck_amount`` — глубина."""
    duck_amount = max(0.0, min(1.0, float(music.get("duck_amount") if music.get("duck_amount") is not None else 0.6)))
    ratio = _ff_number(1.5 + duck_amount * 3.5)
    return (f"[{music_label}][{key_label}]sidechaincompress=threshold=0.1:ratio={ratio}:attack=20:release=400:"
            f"makeup=1[{out_label}]")


def music_wants_sidechain(music: dict) -> bool:
    """Sidechain под речью: включён в пресете (duck) и не снят сведением (mix.sidechain false)."""
    return bool(music.get("duck")) and music.get("sidechain") is not False


def music_stem_filter(music: dict) -> str:
    """Только музыка — как она прозвучит в ролике (громкость, старт, ключи, ducking по звуку
    клипа ``[0:a]``), без самого звука клипа: стем для замера сведения (app/audio_mix.py).
    Выход ``[aout]``."""
    _voice_chain, _voice, music_str = _music_chain({**music, "hold_sec": 0})
    if music_wants_sidechain(music):
        return f"{music_str};[0:a]aresample=48000[key];" + _music_sidechain(music, "music", "key", "aout")
    return f"{music_str};[music]anull[aout]"


def _music_filter(music: dict) -> str:
    """Граф музыки поверх клипа. ``hold_sec`` — стоп-кадр в конце: голос клипа
    затухает на своём конце (как end_fade_filter) и дотягивается тишиной, а музыка
    играет дальше и затухает к концу стоп-кадра — фейд кончается в самом конце файла."""
    _voice_chain, voice, music_str = _music_chain(music)
    if music_wants_sidechain(music):
        return (
            f"{music_str};"
            f"{voice}asplit=2[spk][key];"
            + _music_sidechain(music, "music", "key", "ducked") + ";"
            "[spk][ducked]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,aresample=48000[aout]"
        )
    return (
        f"{music_str};"
        f"{voice}[music]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,aresample=48000[aout]"
    )


def _music_chain(music: dict) -> tuple[str, str, str]:
    """(граф голоса со стоп-кадром — или пусто, метка голоса, граф музыки до ``[music]``; граф
    голоса уже входит в граф музыки)."""
    volume = max(0.0, float(music.get("volume") if music.get("volume") is not None else 0.25))
    fade_in = max(0.0, float(music.get("fade_in_sec") or 0))
    fade_out = max(0.0, float(music.get("fade_out_sec") or 0))
    duration = max(0.0, float(music.get("duration_sec") or 0))
    hold = _music_hold(music)
    voice = "[0:a]"
    voice_chain = ""
    if hold > 0:
        total = duration + hold
        speech_fade = end_fade_filter(duration)
        voice_chain = "[0:a]" + (speech_fade + "," if speech_fade else "") + f"apad=whole_dur={_ff_number(total)}[voice];"
        voice = "[voice]"
        # Затухание музыки — на весь стоп-кадр (или дольше, если так задано в пресете).
        fade_out = max(fade_out, hold)
        duration = total

    start_sec = max(0.0, float(music.get("start_sec") or 0))
    music_chain = [f"volume={_ff_number(volume)}"]
    if music.get("fx"):
        # Цепочка эффектов музыки клипа (render_settings.mix.music_fx, app/audio_fx.py): эквалайзер
        # (освободить место голосу), фильтр, ревёрб… — статично, на весь трек.
        from app.audio_fx import chain_filters

        music_chain.append(chain_filters(music["fx"], "mu"))
    if start_sec > 0:
        # Музыка с секунды трека start_sec: начало отрезается (с петлёй — только у первого круга).
        music_chain[:0] = [f"atrim=start={_ff_number(start_sec)}", "asetpts=PTS-STARTPTS"]
    if music.get("automation"):
        from app.audio_edit import music_volume_expr

        music_chain.append(f"volume='{music_volume_expr(music['automation'])}':eval=frame")
    music_chain[0] = "[1:a]" + music_chain[0]
    if fade_in > 0:
        music_chain.append(f"afade=t=in:st=0:d={_ff_number(fade_in)}")
    if fade_out > 0 and duration > 0:
        start = max(0.0, duration - fade_out)
        music_chain.append(f"afade=t=out:st={_ff_number(start)}:d={_ff_number(fade_out)}")
    return voice_chain, voice, voice_chain + ",".join(music_chain) + "[music]"


def _resolve_music_settings(store: AppStore, preset: dict) -> dict | None:
    track_id = preset.get("music_track_id")
    if not track_id or int(track_id) <= 0:
        return None
    track = store.get_audio_track(int(track_id))
    # Volume lives on the track itself; a per-render override (stashed by
    # _preset_with_music) wins when present.
    override = preset.get("music_volume_override")
    volume = override if override is not None else track.get("volume")
    if volume is None:
        volume = 0.5
    return {
        "track_path": track["file_path"],
        "volume": volume,
        "start_sec": preset.get("music_start_sec") or 0.0,
        "loop": bool(preset.get("music_loop", True)),
        "fade_in_sec": preset.get("music_fade_in_sec"),
        "fade_out_sec": preset.get("music_fade_out_sec"),
        "duck": bool(preset.get("music_duck", True)),
        "duck_amount": preset.get("music_duck_amount"),
        # Ключи, ручной ducking, провалы перед ударом (app/audio_edit.py), секунды готового ролика.
        "automation": preset.get("music_automation"),
        **_music_mix_fields(preset.get("mix")),
    }


def _music_mix_fields(mix: dict | None) -> dict:
    """Сведение клипа → поля прохода музыки: ``fixed_db`` (громкость трека к файлу, без подгонки под
    голос), ``sidechain`` false (ducking — отрезками), ``fx`` (цепочка эффектов музыки)."""
    out: dict = {}
    if not mix:
        return out
    if mix.get("music_db") is not None:
        out["fixed_db"] = float(mix["music_db"])
    if mix.get("sidechain") is False:
        out["sidechain"] = False
    if mix.get("music_fx"):
        out["fx"] = mix["music_fx"]
    return out


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


def _apply_subtitle_timing_offset(result: SubtitleResult, profile: dict, input_duration: float) -> SubtitleResult:
    offset = _subtitle_timing_offset(profile)
    if abs(offset) < 0.001:
        return result
    return shift_subtitle_timeline(result, offset, input_duration)


def _subtitle_timing_offset(profile: dict) -> float:
    try:
        offset = float(profile.get("timing_offset_sec") or 0)
    except (TypeError, ValueError):
        return 0.0
    return max(-2.0, min(2.0, offset))


def _subtitle_prompt_for_clip(store: AppStore, clip_id: int) -> str:
    saved_prompt = str(store.get_app_setting_value("subtitle_prompt", "", include_secret=False) or "").strip()
    if saved_prompt:
        return saved_prompt
    clip = store.get_clip(clip_id)
    try:
        source = store.get_source(clip["source_id"])
    except KeyError:
        source = {}
    label = "Anime subtitles" if _source_looks_like_anime(source) else ""
    if label:
        for preset in store.list_prompt_presets("subtitle"):
            if str(preset.get("label") or "").strip().lower() == label.lower():
                return str(preset.get("prompt") or "").strip()
    preset = store.get_default_prompt_preset("subtitle")
    return str((preset or {}).get("prompt") or "").strip()


def _source_looks_like_anime(source: dict) -> bool:
    source_type = str(source.get("source_type") or "").lower()
    original_url = str(source.get("original_url") or "").lower()
    original_filename = str(source.get("original_filename") or "").lower()
    haystack = f"{source_type} {original_url} {original_filename}"
    return any(marker in haystack for marker in ("smotvibe", "yummyani", "anime"))


# Части рендера: подмена имени на этом модуле доходит и до них.
_PARTS = tuple(sys.modules[f"app.{name}"] for name in (
    "render_exec", "render_loudness", "render_filters", "render_args",
))


class _RenderModule(types.ModuleType):
    """Подмена имени на ``app.render`` видна коду всех частей.

    До разбора рендер был одним модулем, и подмены (``monkeypatch.setattr("app.render._run_ffmpeg", ...)``, а ещё
    ``probe_media``, ``run_qc``, ``uuid4``, ``fit_loudness``, ...) ставятся сюда. Функции частей читают имена из
    глобальных своего модуля, поэтому значение дублируется в каждую часть, где имя есть. Снятие подмены идёт тем же
    путём."""

    def __setattr__(self, name: str, value: Any) -> None:
        super().__setattr__(name, value)
        for part in _PARTS:
            if name in vars(part):
                setattr(part, name, value)


sys.modules[__name__].__class__ = _RenderModule
