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
from app.render_music import (  # noqa: F401
    _music_chain, _music_filter, _music_hold, _music_mix_fields, _music_sidechain, _resolve_music_settings,
    build_ffmpeg_music_args, MAX_MUSIC_START_SEC, MUSIC_NOMINAL_VOLUME, music_stem_filter, MUSIC_UNDER_SPEECH_DB,
    music_volume_for, music_wants_sidechain,
)
from app.render_presets import (  # noqa: F401
    _base_crop, _is_canvas, _keyed_render, _needs_sar_fix, _piece_retime, _preset_for_segment,
    _preset_with_music_automation, _preset_with_output, _preset_with_source_audio, _segment_reframe_x, _source_gaps,
    _with_background_zones, banded_pinned, effective_preset, KEY_EDGE_INSET, layout_camera_regions, pin_band,
    PresetsRender, REFRAME_MAX_SMOOTH_SEC,
)
from app.render_subs import (  # noqa: F401
    _apply_subtitle_timing_offset, _source_looks_like_anime, _subtitle_prompt_for_clip, _subtitle_timing_offset,
    build_ffmpeg_burn_subtitle_args, build_ffmpeg_extract_audio_args, SubtitlesRender,
)
from app.render_sound import SoundRender  # noqa: F401


class ClipRenderService(PresetsRender, SubtitlesRender, SoundRender):
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

    def _build_part_args(self, **kwargs: Any) -> list[str]:
        return build_ffmpeg_render_args(**kwargs)

    def _build_join_args(self, *args: Any, **kwargs: Any) -> list[str]:
        return build_join_args(*args, **kwargs)

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


# Части рендера: подмена имени на этом модуле доходит и до них.
_PARTS = tuple(sys.modules[f"app.{name}"] for name in (
    "render_exec", "render_loudness", "render_filters", "render_args", "render_music", "render_presets", "render_subs",
    "render_sound",
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
