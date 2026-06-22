from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

from app.ingest import file_hash_and_size, probe_media
from app.settings import settings
from app.store import AppStore
from app.subtitles.ass import write_ass_subtitles
from app.subtitles.contracts import SubtitleResult
from app.subtitles.registry import get_subtitle_provider, subtitle_model_for_profile
from app.subtitles.timing import normalize_subtitle_timeline, shift_subtitle_timeline
from app.video_crop import build_reframe_x_expr


def _segment_reframe_x(segment: dict, preset: dict, source: dict) -> str | None:
    """Build a smoothed crop-x expression for a segment, or None when smart
    reframing is off, there's no focus track, or the source has no horizontal
    slack (already ≤ the target aspect)."""
    if not preset.get("smart_reframe"):
        return None
    focus = segment.get("focus") or []
    if not focus:
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
    if eff_h <= 0 or out_h <= 0 or (eff_w / eff_h) <= (out_w / out_h) + 1e-3:
        return None
    remapped: list[dict] = []
    for point in focus:
        try:
            fx = float(point["x"])
            t = float(point["t"])
        except (KeyError, TypeError, ValueError):
            continue
        if crop:
            fx = (fx - float(crop["x"])) / (float(crop["w"]) or 1.0)
        remapped.append({"t": t, "x": min(1.0, max(0.0, fx))})
    duration = float(segment.get("end_sec", 0)) - float(segment.get("start_sec", 0))
    return build_reframe_x_expr(remapped, duration, out_w)


class ClipRenderService:
    def __init__(self, store: AppStore) -> None:
        self.store = store

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
    ) -> dict:
        segment = self.store.get_ai_segment(segment_id)
        source = self.store.get_source(segment["source_id"])
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
        )
        if subtitle_profile_id is None:
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
        output_path = settings.clip_dir / f"{render_id}.mp4"
        has_post = bool(subtitle_profile_id) or bool(preset.get("music_track_id"))
        base_output_path = settings.clip_dir / f"{render_id}.base.mp4" if has_post else output_path
        temp_paths: list[Path] = []
        if has_post:
            temp_paths.append(base_output_path)
        try:
            banner = self.store.get_banner(preset["banner_id"]) if preset.get("banner_id") else None
            args = build_ffmpeg_render_args(
                source_path=Path(source["local_path"]),
                output_path=base_output_path,
                start_sec=float(segment["start_sec"]),
                end_sec=float(segment["end_sec"]),
                preset=preset,
                banner=banner,
                source_crop=source.get("content_crop"),
                reframe_x=_segment_reframe_x(segment, preset, source),
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

    def _preset(self, preset_id: int | None) -> dict:
        if preset_id is not None:
            return self.store.get_ffmpeg_preset(preset_id)
        presets = self.store.list_ffmpeg_presets()
        if presets:
            return presets[-1]
        return self.store.create_ffmpeg_preset("Default vertical")

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
    ) -> dict:
        if not segment_ids:
            raise ValueError("at least one segment is required")
        segments = [self.store.get_ai_segment(segment_id) for segment_id in segment_ids]
        source_ids = {segment["source_id"] for segment in segments}
        if len(source_ids) != 1:
            raise ValueError("all montage segments must belong to the same source")
        source = self.store.get_source(segments[0]["source_id"])
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
        )
        if subtitle_profile_id is None:
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
        output_path = settings.clip_dir / f"{render_id}.mp4"
        has_post = bool(subtitle_profile_id) or bool(preset.get("music_track_id"))
        base_output_path = settings.clip_dir / f"{render_id}.base.mp4" if has_post else output_path
        temp_dir = settings.tmp_dir / f"montage-{render_id}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_paths: list[Path] = []
        if has_post:
            temp_paths.append(base_output_path)
        try:
            banner = self.store.get_banner(preset["banner_id"]) if preset.get("banner_id") else None
            rendered_parts: list[Path] = []
            for index, segment in enumerate(segments):
                part_path = temp_dir / f"part-{index:03d}.mp4"
                _run_ffmpeg(
                    build_ffmpeg_render_args(
                        source_path=Path(source["local_path"]),
                        output_path=part_path,
                        start_sec=float(segment["start_sec"]),
                        end_sec=float(segment["end_sec"]),
                        preset=preset,
                        banner=banner,
                        source_crop=source.get("content_crop"),
                        reframe_x=_segment_reframe_x(segment, preset, source),
                    ),
                    timeout=60 * 30,
                )
                rendered_parts.append(part_path)
            concat_list_path = temp_dir / "concat.txt"
            concat_list_path.write_text(
                "\n".join(_concat_file_line(path) for path in rendered_parts) + "\n",
                encoding="utf-8",
            )
            _run_ffmpeg(build_ffmpeg_concat_args(concat_list_path, base_output_path), timeout=60 * 30)
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
        output_path = settings.clip_dir / f"{render_id}.mp4"
        has_post = bool(subtitle_profile_id) or bool(preset.get("music_track_id"))
        base_output_path = settings.clip_dir / f"{render_id}.base.mp4" if has_post else output_path
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
            for path in temp_paths:
                path.unlink(missing_ok=True)
            return clip
        except Exception as exc:  # noqa: BLE001 - render failures are persisted as clip state
            clip = self.store.finish_clip_render(clip["id"], status="failed", error=_safe_error(exc))
            output_path.unlink(missing_ok=True)
            for path in temp_paths:
                path.unlink(missing_ok=True)
            return clip

    def render_clip_plan(
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
    ) -> dict:
        plan = self.store.get_clip_plan(clip_plan_id)
        segments = plan.get("segments") or []
        if not segments:
            raise ValueError("clip plan has no segments")
        self.store.update_clip_plan_status(clip_plan_id, "rendering")
        segment_ids = [segment["id"] for segment in segments]
        if len(segment_ids) == 1:
            clip = self.render_segment(
                segment_ids[0],
                ffmpeg_preset_id=ffmpeg_preset_id,
                subtitle_profile_id=subtitle_profile_id,
                banner_id=banner_id,
                clip_plan_id=clip_plan_id,
                music_track_id=music_track_id,
                music_volume=music_volume,
                subtitle_offset_sec=subtitle_offset_sec,
                subtitle_provider=subtitle_provider,
                banner_height_frac=banner_height_frac,
                banner_y_frac=banner_y_frac,
                subtitle_margin_v=subtitle_margin_v,
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
                subtitle_offset_sec=subtitle_offset_sec,
                subtitle_provider=subtitle_provider,
                banner_height_frac=banner_height_frac,
                banner_y_frac=banner_y_frac,
                subtitle_margin_v=subtitle_margin_v,
            )
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

    def _render_subtitles(
        self,
        clip_id: int,
        input_path: Path,
        output_path: Path,
        subtitle_profile_id: int,
        preset: dict,
    ) -> None:
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
    ) -> dict:
        if music_track_id is None and music_volume is None:
            return preset
        preset = dict(preset)
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
        """
        if (
            banner_height_frac is None
            and banner_y_frac is None
            and not subtitle_provider
            and subtitle_margin_v is None
        ):
            return preset
        preset = dict(preset)
        if banner_height_frac is not None:
            preset["banner_height_frac"] = max(0.02, min(0.6, float(banner_height_frac)))
        if banner_y_frac is not None:
            preset["banner_y_frac"] = max(0.0, min(0.97, float(banner_y_frac)))
        if subtitle_provider:
            preset["subtitle_provider_override"] = str(subtitle_provider).strip().lower()
        if subtitle_margin_v is not None:
            preset["subtitle_margin_v_override"] = max(0, int(subtitle_margin_v))
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
        if music:
            passes.append("music")
        temp_paths: list[Path] = []
        current = base_output_path
        for index, name in enumerate(passes):
            is_last = index == len(passes) - 1
            dest = output_path if is_last else settings.clip_dir / f"{render_id}.{name}.mp4"
            if name == "music":
                music_settings = dict(music)
                music_settings["duration_sec"] = probe_media(current).duration_sec
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


def build_ffmpeg_render_args(
    source_path: Path,
    output_path: Path,
    start_sec: float,
    end_sec: float,
    preset: dict,
    banner: dict | None = None,
    source_crop: dict | None = None,
    reframe_x: str | None = None,
) -> list[str]:
    if end_sec <= start_sec:
        raise ValueError("end_sec must be greater than start_sec")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    width = int(preset.get("output_width") or 1080)
    height = int(preset.get("output_height") or 1920)
    fps = float(preset.get("fps") or 30)
    duration = end_sec - start_sec
    args = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        _seconds(start_sec),
        "-t",
        _seconds(duration),
        "-i",
        str(source_path),
    ]
    if banner:
        args.extend(_banner_input_args(banner))
    args.extend(
        [
            "-filter_complex",
            _filter_complex(width, height, fps, preset, banner, source_crop, reframe_x),
            "-map",
            "[vout]",
            "-c:v",
            str(preset.get("video_codec") or "libx264"),
            "-c:a",
            str(preset.get("audio_codec") or "aac"),
            "-ac",
            "2",
            "-ar",
            "48000",
            "-shortest",
            "-movflags",
            "+faststart",
        ]
    )
    audio_map = _audio_map(preset)
    output_codec_index = args.index("-c:v", args.index("[vout]"))
    args[output_codec_index:output_codec_index] = ["-map", audio_map]
    video_bitrate = str(preset.get("video_bitrate") or "").strip()
    audio_bitrate = str(preset.get("audio_bitrate") or "").strip()
    if video_bitrate:
        args.extend(["-b:v", video_bitrate])
    if audio_bitrate:
        args.extend(["-b:a", audio_bitrate])
    extra = _json_dict(preset.get("extra_json"))
    crf = extra.get("crf")
    if crf is not None:
        args.extend(["-crf", str(crf)])
    args.append(str(output_path))
    return args


def build_ffmpeg_extract_audio_args(
    input_path: Path,
    output_path: Path,
    start_sec: float | None = None,
    duration_sec: float | None = None,
) -> list[str]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
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


def build_ffmpeg_concat_args(concat_list_path: Path, output_path: Path) -> list[str]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    return [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list_path),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(output_path),
    ]


def build_ffmpeg_music_args(input_path: Path, output_path: Path, music: dict) -> list[str]:
    """Mix a background music track over an already-rendered clip.

    The clip video is stream-copied (untouched); only audio is rebuilt. The
    music can loop to fill the clip, fade in/out, and duck under speech via a
    sidechain compressor keyed on the original audio.
    """

    output_path.parent.mkdir(parents=True, exist_ok=True)
    track_path = Path(music["track_path"])
    loop = bool(music.get("loop"))
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
            "-shortest",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )
    return args


def _music_filter(music: dict) -> str:
    volume = max(0.0, float(music.get("volume") if music.get("volume") is not None else 0.25))
    fade_in = max(0.0, float(music.get("fade_in_sec") or 0))
    fade_out = max(0.0, float(music.get("fade_out_sec") or 0))
    duration = max(0.0, float(music.get("duration_sec") or 0))
    duck = bool(music.get("duck"))
    duck_amount = max(0.0, min(1.0, float(music.get("duck_amount") if music.get("duck_amount") is not None else 0.6)))

    music_chain = [f"[1:a]volume={_ff_number(volume)}"]
    if fade_in > 0:
        music_chain.append(f"afade=t=in:st=0:d={_ff_number(fade_in)}")
    if fade_out > 0 and duration > 0:
        start = max(0.0, duration - fade_out)
        music_chain.append(f"afade=t=out:st={_ff_number(start)}:d={_ff_number(fade_out)}")
    music_str = ",".join(music_chain) + "[music]"

    if duck:
        # Gentle, musical ducking: only noticeable speech pulls the music down and
        # the music stays clearly audible underneath. duck_amount tunes the depth.
        ratio = _ff_number(1.5 + duck_amount * 3.5)
        return (
            f"{music_str};"
            "[0:a]asplit=2[spk][key];"
            f"[music][key]sidechaincompress=threshold=0.1:ratio={ratio}:attack=20:release=400:makeup=1[ducked];"
            "[spk][ducked]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,aresample=48000[aout]"
        )
    return (
        f"{music_str};"
        "[0:a][music]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,aresample=48000[aout]"
    )


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
        "loop": bool(preset.get("music_loop", True)),
        "fade_in_sec": preset.get("music_fade_in_sec"),
        "fade_out_sec": preset.get("music_fade_out_sec"),
        "duck": bool(preset.get("music_duck", True)),
        "duck_amount": preset.get("music_duck_amount"),
    }


def _filter_complex(
    width: int,
    height: int,
    fps: float,
    preset: dict,
    banner: dict | None,
    source_crop: dict | None = None,
    reframe_x: str | None = None,
) -> str:
    filters = [_render_filter(width, height, fps, preset, banner, source_crop, reframe_x)]
    audio_filter = _audio_filter(preset)
    if audio_filter:
        filters.append(audio_filter)
    return ";".join(filters)


def _crop_prefix(source_crop: dict | None, copies: int) -> tuple[str, list[str]]:
    """Build the optional content-crop filter and the input label(s) to consume.

    An input pad (``0:v``) can feed several filters, but a filter *output* label
    can be read only once — so when the crop is active and we need the frame more
    than once (blur background) we ``split`` it.
    """
    if not source_crop:
        return "", ["0:v"] * copies
    c = source_crop
    crop = f"[0:v]crop=iw*{c['w']:.5f}:ih*{c['h']:.5f}:iw*{c['x']:.5f}:ih*{c['y']:.5f}"
    if copies == 1:
        return f"{crop}[vsrc];", ["vsrc"]
    labels = [f"vsrc{i}" for i in range(copies)]
    return f"{crop},split={copies}{''.join(f'[{label}]' for label in labels)};", labels


def _render_filter(
    width: int,
    height: int,
    fps: float,
    preset: dict,
    banner: dict | None,
    source_crop: dict | None = None,
    reframe_x: str | None = None,
) -> str:
    mode = preset.get("scale_mode") or "cover"
    anchor = preset.get("crop_anchor") or "center"
    if mode == "blur_background":
        prefix, (bg_in, fg_in) = _crop_prefix(source_crop, 2)
        base = (
            f"{prefix}[{bg_in}]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},boxblur=24:2[bg];"
            f"[{fg_in}]scale={width}:{height}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(main_w-overlay_w)/2:(main_h-overlay_h)/2,"
            f"fps={_fps(fps)},format=yuv420p[vbase]"
        )
    elif mode == "contain":
        prefix, (vin,) = _crop_prefix(source_crop, 1)
        base = (
            f"{prefix}[{vin}]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
            f"fps={_fps(fps)},format=yuv420p[vbase]"
        )
    else:
        crop_y = {
            "top": "0",
            "bottom": f"ih-{height}",
            "center": f"(ih-{height})/2",
        }.get(anchor, f"(ih-{height})/2")
        # Dynamic reframe overrides the horizontal crop offset with a smoothed x(t).
        crop_x = reframe_x if reframe_x else f"(iw-{width})/2"
        prefix, (vin,) = _crop_prefix(source_crop, 1)
        base = (
            f"{prefix}[{vin}]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}:{crop_x}:{crop_y},"
            f"fps={_fps(fps)},format=yuv420p[vbase]"
        )
    style = _video_style_filter(preset)
    source_label = "vbase"
    if style:
        base = f"{base};[vbase]{style}[vstyled]"
        source_label = "vstyled"
    if not banner:
        return f"{base};[{source_label}]copy[vout]"
    opacity = max(0, min(1, float(banner.get("opacity") if banner.get("opacity") is not None else 1)))
    x_expr, y_expr = _banner_position_expr(banner)
    # Per-render vertical position overrides the banner asset's saved placement so
    # it matches the preview band.
    y_frac = preset.get("banner_y_frac")
    if y_frac is not None:
        y_expr = str(int(round(float(y_frac) * height)))
    banner_chain = f"[1:v]format=rgba,colorchannelmixer=aa={opacity}"
    bh_frac = preset.get("banner_height_frac")
    if bh_frac:
        banner_h = max(1, int(round(float(bh_frac) * height)))
        banner_chain += f",scale=-1:{banner_h}:flags=lanczos"
    banner_chain += "[banner]"
    return (
        f"{base};"
        f"{banner_chain};"
        f"[{source_label}][banner]overlay={x_expr}:{y_expr}:shortest=1,format=yuv420p[vout]"
    )


def _video_style_filter(preset: dict) -> str:
    """Build the color-grade / vignette / film-grain filter chain for a preset.

    Returns a comma-joined ffmpeg filter string (no leading/trailing comma), or
    an empty string when the preset asks for no look. Strength scales the
    intensity of the parametric color styles so the same preset can be subtle or
    heavy. Vignette and grain are added on top of the color grade.
    """

    parts: list[str] = []
    style = str(preset.get("color_style") or "none").strip().lower()
    strength = _clamp(preset.get("color_strength"), default=1.0, low=0.0, high=2.0)
    if style != "none" and strength > 0:
        parts.extend(_color_style_filters(style, strength))
    vignette = _clamp(preset.get("vignette"), default=0.0, low=0.0, high=1.0)
    if vignette > 0:
        # Larger angle = darker corners. Map 0..1 onto a tasteful range.
        angle = 0.62 + vignette * 0.95
        parts.append(f"vignette=angle={angle:.4f}")
    grain = _clamp(preset.get("grain"), default=0.0, low=0.0, high=1.0)
    if grain > 0:
        strength_px = max(1, int(round(grain * 22)))
        parts.append(f"noise=alls={strength_px}:allf=t")
    return ",".join(parts)


def _color_style_filters(style: str, strength: float) -> list[str]:
    if style == "noir":
        contrast = 1 + 0.25 * strength
        brightness = -0.02 * strength
        return [f"hue=s=0", f"eq=contrast={_ff_number(contrast)}:brightness={_ff_number(brightness)}"]
    if style == "vintage":
        # ffmpeg's built-in vintage curve, eased toward the original by strength.
        eq = f"eq=saturation={_ff_number(1 - 0.15 * strength)}:contrast={_ff_number(1 + 0.05 * strength)}"
        return ["curves=preset=vintage", eq]
    if style == "warm":
        balance = f"colorbalance=rm={_ff_number(0.08 * strength)}:gm={_ff_number(0.02 * strength)}:bm={_ff_number(-0.06 * strength)}"
        eq = f"eq=contrast={_ff_number(1 + 0.06 * strength)}:saturation={_ff_number(1 + 0.08 * strength)}"
        return [balance, eq]
    if style == "cold":
        balance = f"colorbalance=rm={_ff_number(-0.06 * strength)}:bm={_ff_number(0.08 * strength)}"
        eq = f"eq=contrast={_ff_number(1 + 0.05 * strength)}:saturation={_ff_number(1 + 0.05 * strength)}"
        return [balance, eq]
    if style == "vibrant":
        return [f"eq=saturation={_ff_number(1 + 0.35 * strength)}:contrast={_ff_number(1 + 0.08 * strength)}"]
    # cinematic teal-orange: push shadows toward teal, highlights toward orange.
    balance = (
        f"colorbalance=rs={_ff_number(-0.06 * strength)}:bs={_ff_number(0.06 * strength)}:"
        f"rh={_ff_number(0.06 * strength)}:bh={_ff_number(-0.06 * strength)}"
    )
    eq = f"eq=contrast={_ff_number(1 + 0.08 * strength)}:saturation={_ff_number(1 + 0.06 * strength)}"
    return [balance, eq]


def _clamp(value, *, default: float, low: float, high: float) -> float:
    if value is None or str(value).strip() == "":
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _banner_input_args(banner: dict) -> list[str]:
    path = Path(banner["file_path"])
    args = ["-stream_loop", "-1"]
    if path.suffix.lower() == ".webm":
        args.extend(["-c:v", "libvpx-vp9"])
    args.extend(["-i", str(path)])
    return args


def _audio_filter(preset: dict) -> str:
    mode = str(preset.get("audio_mix_mode") or "primary").strip().lower()
    primary_stream = _stream_index(preset.get("audio_primary_stream"), 0)
    primary_volume = _volume(preset.get("audio_primary_volume"), 1)
    secondary_stream = _stream_index(preset.get("audio_secondary_stream"), 1)
    secondary_volume = _volume(preset.get("audio_secondary_volume"), 1)
    if mode == "mix":
        return (
            f"[0:a:{primary_stream}]volume={_ff_number(primary_volume)}[a_primary];"
            f"[0:a:{secondary_stream}]volume={_ff_number(secondary_volume)}[a_secondary];"
            "[a_primary][a_secondary]amix=inputs=2:duration=longest:dropout_transition=0,"
            "aresample=48000[aout]"
        )
    if mode == "secondary":
        return f"[0:a:{secondary_stream}]volume={_ff_number(secondary_volume)},aresample=48000[aout]"
    if primary_stream != 0 or primary_volume != 1:
        return f"[0:a:{primary_stream}]volume={_ff_number(primary_volume)},aresample=48000[aout]"
    return ""


def _audio_map(preset: dict) -> str:
    mode = str(preset.get("audio_mix_mode") or "primary").strip().lower()
    primary_stream = _stream_index(preset.get("audio_primary_stream"), 0)
    primary_volume = _volume(preset.get("audio_primary_volume"), 1)
    if mode in {"mix", "secondary"} or primary_stream != 0 or primary_volume != 1:
        return "[aout]"
    return f"0:a:{primary_stream}?"


def _banner_position_expr(banner: dict) -> tuple[str, str]:
    if banner.get("position") == "custom":
        return str(int(banner.get("x") or 0)), str(int(banner.get("y") or 0))
    x_expr = "(main_w-overlay_w)/2"
    if banner.get("position") == "top":
        return x_expr, "0"
    if banner.get("position") == "center":
        return x_expr, "(main_h-overlay_h)/2"
    return x_expr, "main_h-overlay_h"


def _seconds(value: float) -> str:
    return f"{max(0, value):.3f}"


def _fps(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _stream_index(value, default: int) -> int:
    if value is None or str(value).strip() == "":
        return default
    return max(0, int(value))


def _volume(value, default: float) -> float:
    if value is None or str(value).strip() == "":
        return default
    return max(0.0, min(4.0, float(value)))


def _ff_number(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _json_dict(value) -> dict:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _filter_filename(filename: str) -> str:
    return str(Path(filename).name).replace("\\", "/").replace(":", r"\:")


def _concat_file_line(path: Path) -> str:
    escaped = path.resolve().as_posix().replace("'", "'\\''")
    return f"file '{escaped}'"


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


def _run_ffmpeg(args: list[str], timeout: int, cwd: Path | None = None) -> None:
    proc = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        cwd=cwd,
    )
    if proc.returncode != 0:
        raise RuntimeError(_safe_error(proc.stderr or proc.stdout or "ffmpeg failed"))


def _safe_error(exc) -> str:
    text = str(exc or "").strip()
    return text[:1000] or "render failed"


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
