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
from app.subtitles.registry import get_subtitle_provider, subtitle_model_for_profile


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
    ) -> dict:
        segment = self.store.get_ai_segment(segment_id)
        source = self.store.get_source(segment["source_id"])
        preset = self._preset(ffmpeg_preset_id)
        preset = self._preset_with_banner(preset, banner_id)
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
        base_output_path = settings.clip_dir / f"{render_id}.base.mp4" if subtitle_profile_id else output_path
        temp_paths: list[Path] = []
        if subtitle_profile_id:
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
            )
            _run_ffmpeg(args, timeout=60 * 30)
            final_output_path = base_output_path
            if subtitle_profile_id:
                final_output_path = output_path
                self._render_subtitles(
                    clip_id=clip["id"],
                    input_path=base_output_path,
                    output_path=final_output_path,
                    subtitle_profile_id=subtitle_profile_id,
                    preset=preset,
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
        base_output_path = settings.clip_dir / f"{render_id}.base.mp4" if subtitle_profile_id else output_path
        temp_dir = settings.tmp_dir / f"montage-{render_id}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_paths: list[Path] = []
        if subtitle_profile_id:
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
            final_output_path = base_output_path
            if subtitle_profile_id:
                final_output_path = output_path
                self._render_subtitles(
                    clip_id=clip["id"],
                    input_path=base_output_path,
                    output_path=final_output_path,
                    subtitle_profile_id=subtitle_profile_id,
                    preset=preset,
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

    def render_clip_plan(
        self,
        clip_plan_id: int,
        ffmpeg_preset_id: int | None = None,
        subtitle_profile_id: int | None = None,
        banner_id: int | None = None,
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
        profile["prompt"] = self.store.get_app_setting_value("subtitle_prompt", "", include_secret=False)
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
            _run_ffmpeg(build_ffmpeg_extract_audio_args(input_path, audio_path), timeout=60 * 10)
            result = provider.transcribe(audio_path, profile, model)
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
                model=model,
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


def build_ffmpeg_render_args(
    source_path: Path,
    output_path: Path,
    start_sec: float,
    end_sec: float,
    preset: dict,
    banner: dict | None = None,
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
            _filter_complex(width, height, fps, preset, banner),
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


def build_ffmpeg_extract_audio_args(input_path: Path, output_path: Path) -> list[str]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    return [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
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


def _filter_complex(width: int, height: int, fps: float, preset: dict, banner: dict | None) -> str:
    filters = [_render_filter(width, height, fps, preset, banner)]
    audio_filter = _audio_filter(preset)
    if audio_filter:
        filters.append(audio_filter)
    return ";".join(filters)


def _render_filter(width: int, height: int, fps: float, preset: dict, banner: dict | None) -> str:
    mode = preset.get("scale_mode") or "cover"
    anchor = preset.get("crop_anchor") or "center"
    if mode == "blur_background":
        base = (
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},boxblur=24:2[bg];"
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(main_w-overlay_w)/2:(main_h-overlay_h)/2,"
            f"fps={_fps(fps)},format=yuv420p[vbase]"
        )
    elif mode == "contain":
        base = (
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
            f"fps={_fps(fps)},format=yuv420p[vbase]"
        )
    else:
        crop_y = {
            "top": "0",
            "bottom": f"ih-{height}",
            "center": f"(ih-{height})/2",
        }.get(anchor, f"(ih-{height})/2")
        base = (
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}:(iw-{width})/2:{crop_y},"
            f"fps={_fps(fps)},format=yuv420p[vbase]"
        )
    if not banner:
        return f"{base};[vbase]copy[vout]"
    opacity = max(0, min(1, float(banner.get("opacity") if banner.get("opacity") is not None else 1)))
    x_expr, y_expr = _banner_position_expr(banner)
    return (
        f"{base};"
        f"[1:v]format=rgba,colorchannelmixer=aa={opacity}[banner];"
        f"[vbase][banner]overlay={x_expr}:{y_expr}:shortest=1,format=yuv420p[vout]"
    )


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
