"""Рендер: куски и склейка, готовый загруженный клип (``app/render.py``)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.heavy_queue import heavy
from app.ingest import file_hash_and_size, probe_media
from app.piece_speed import seg_length
from app.render_args import build_ffmpeg_concat_args, build_ffmpeg_render_args
from app.render_exec import _concat_file_line, _json_dict, _run_ffmpeg, _safe_error, _video_duration
from app.render_presets import (
    _base_crop, _is_canvas, _keyed_render, _needs_sar_fix, _piece_retime, _preset_for_segment,
    _preset_with_music_automation, _preset_with_source_audio, _segment_reframe_x, _source_gaps, _with_background_zones,
    pin_band,
)
from app.settings import settings
from app.transitions import build_join_args, is_plain_concat, normalize_transition, part_overlaps


class MontageRender:
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
            clip_plan_id=origin.get("clip_plan_id"),
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
