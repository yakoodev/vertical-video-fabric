from __future__ import annotations

import json
import math
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.focus_presets import get_focus_preset
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
from app.transitions import build_join_args, is_plain_concat, normalize_transition, plan_joins
from app.video_crop import build_reframe_x_expr


# Тише этого (LUFS) — тишина: loudnorm намерит -inf и второй проход упадёт.
LOUDNORM_SILENCE_LUFS = -70.0

# Один рендер клипа на весь сервис (см. ClipRenderService.render_clip_plan).
_RENDER_LOCK = threading.RLock()


# Тихая запись голоса (втубер на зелёнке, −32 LUFS) с потолком +12 выходила на −21.
MAX_LOUDNESS_BOOST_DB = 20.0
# Тише этого — почти тишина или шум: поднимать нечего, не превращаем её в шипение.
SILENT_LUFS = -45.0


def linear_gain_db(measured: dict, target: float) -> float:
    """Одна поправка громкости на весь клип: к цели по LUFS, но не больше потолка
    (+20 дБ; почти тишину не поднимаем вовсе) — пики дальше держит лимитер."""
    measured_i = float(measured["input_i"])
    gain = target - measured_i
    if measured_i < SILENT_LUFS:
        gain = min(gain, 0.0)
    return max(-20.0, min(MAX_LOUDNESS_BOOST_DB, gain))


def loudnorm_needed(measured: dict, target: float) -> bool:
    """Нужен ли второй проход loudnorm по замеру первого.

    Тихий/немой клип: input_i = "-inf" (или ниже -70) — поднимать нечего, а
    ffmpeg с measured_I=-inf падает. Тогда звук оставляем как есть.
    """
    try:
        current = float(measured["input_i"])
        peak = float(measured["input_tp"])
        lra = float(measured["input_lra"])
        thresh = float(measured["input_thresh"])
        offset = float(measured["target_offset"])
    except (KeyError, TypeError, ValueError):
        return False
    if not all(math.isfinite(v) for v in (current, peak, lra, thresh, offset)):
        return False
    if current < LOUDNORM_SILENCE_LUFS:
        return False
    return not (abs(current - target) < 1.0 and peak <= -1.0)


# Короткое затухание звука в самом конце ролика: финал не «щёлкает» обрывом
# слова, даже если последний кусок резали вплотную к речи.
END_AUDIO_FADE_SEC = 0.35  # затухание кончается ровно на последнем звуке клипа; стоп-кадр после — тишина


def audible(measured: dict) -> bool:
    """Есть ли в клипе слышимый звук (по замеру loudnorm)."""
    try:
        current = float(measured["input_i"])
    except (KeyError, TypeError, ValueError):
        return False
    return math.isfinite(current) and current >= LOUDNORM_SILENCE_LUFS


def end_fade_filter(duration: float, fade: float = END_AUDIO_FADE_SEC) -> str:
    """afade на последние ``fade`` секунд (пусто, если ролик слишком короткий)."""
    if duration <= fade * 4 or fade <= 0:
        return ""
    return f"afade=t=out:st={duration - fade:.3f}:d={fade:.3f}"


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
        smooth_time=float(cfg["smooth_time"]),
        rubber=float(cfg["rubber"]),
        deadzone=float(cfg["deadzone"]),
    )


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
    ) -> dict:
        """``motion`` — видимые слои и камера (``visual_motion``); None — как раньше."""
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
            mirror=mirror,
            layout=layout,
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
            base_preset, motion = _keyed_render(preset, motion)
            args = build_ffmpeg_render_args(
                source_path=Path(source["local_path"]),
                output_path=base_output_path,
                start_sec=float(segment["start_sec"]),
                end_sec=float(segment["end_sec"]),
                preset=base_preset,
                # С моушном баннер кладётся после камеры и слоёв, а не в основу.
                banner=None if motion else banner,
                source_crop=_base_crop(source, motion),
                reframe_x=_segment_reframe_x(segment, preset, source),
            )
            _run_ffmpeg(args, timeout=60 * 30)
            motion_notes = self._apply_motion(render_id, base_output_path, preset, banner, motion) if motion else []
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
        mirror: bool = False,
        transition: dict | None = None,
        cover: dict | None = None,
        motion: dict | None = None,
        layout: dict | None = None,
        cut_parts: list[dict] | None = None,
        jump_joins: list[bool] | None = None,
    ) -> dict:
        """``cut_parts``/``jump_joins`` — клип с джамп-катами (app/cuts.py): части
        рендерятся вместо кусков, склейки ``jump_joins`` — встык с микрофейдом."""
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
        preset = self._preset_with_music(preset, music_track_id, music_volume)
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
            base_preset, motion = _keyed_render(preset, motion)
            # Без вырезов части — это сами куски (рендер ровно прежний).
            parts = cut_parts or segments
            jumps = list(jump_joins or []) if cut_parts else None
            rendered_parts: list[Path] = []
            for index, segment in enumerate(parts):
                part_path = temp_dir / f"part-{index:03d}.mp4"
                _run_ffmpeg(
                    build_ffmpeg_render_args(
                        source_path=Path(source["local_path"]),
                        output_path=part_path,
                        start_sec=float(segment["start_sec"]),
                        end_sec=float(segment["end_sec"]),
                        preset=base_preset,
                        banner=None if motion else banner,
                        source_crop=_base_crop(source, motion),
                        reframe_x=_segment_reframe_x(segment, preset, source),
                    ),
                    timeout=60 * 30,
                )
                rendered_parts.append(part_path)
            if is_plain_concat(join) and not any(jumps or []):
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
                _run_ffmpeg(
                    build_join_args(
                        rendered_parts,
                        # По картинке, а не по контейнеру: звук подгоняется под неё.
                        [_video_duration(m) for m in probes],
                        base_output_path,
                        join,
                        has_audio=has_audio,
                        gaps=_source_gaps(parts),
                        video_codec=str(preset.get("video_codec") or "libx264"),
                        video_bitrate=str(preset.get("video_bitrate") or "").strip(),
                        crf=extra.get("crf"),
                        audio_codec=str(preset.get("audio_codec") or "aac"),
                        audio_bitrate=str(preset.get("audio_bitrate") or "").strip(),
                        jump_joins=jumps,
                    ),
                    timeout=60 * 30,
                )
            motion_notes = self._apply_motion(render_id, base_output_path, preset, banner, motion) if motion else []
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
                [float(seg["end_sec"]) - float(seg["start_sec"]) for seg in parts], join,
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
            # Ровно те тайминги слов, что рендер прожёг/отдал слою «субтитры»:
            # вырезы, потом настоящие overlap склеек после вырезов.
            cut_plan = self._cut_plan(plan, clip.get("ffmpeg_preset_id"))
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

    def render_clip_plan(self, clip_plan_id: int, *args: Any, **kwargs: Any) -> dict:
        """Рендер клипа — по одному на весь сервис.

        Рендер тяжёлый (ffmpeg + компоновщик моушна). Два одновременных — два агента
        или агент и кнопка в редакторе — уже трижды роняли Docker по памяти; очередь
        дешевле. RLock: рендер может позвать рендер в том же потоке (подборка)."""
        with _RENDER_LOCK:
            return self._render_clip_plan_unlocked(clip_plan_id, *args, **kwargs)

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
    ) -> dict:
        plan = self.store.get_clip_plan(clip_plan_id)
        segments = plan.get("segments") or []
        if not segments:
            raise ValueError("clip plan has no segments")
        # Джамп-каты (app/cuts.py): нет вырезов — None, и всё ниже как раньше.
        # До статуса «rendering»: вырезы на весь клип — ошибка запроса, а не зависший клип.
        cut_plan = self._cut_plan(plan, ffmpeg_preset_id)
        self.store.update_clip_plan_status(clip_plan_id, "rendering")
        segment_ids = [segment["id"] for segment in segments]
        # Моушн, как и вставки, живёт в настройках самого клипа. Нет слоёв и
        # камеры — рендер ровно прежний (те же команды ffmpeg).
        from app.motion import plan_motion, visual_motion

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
        # Раскладка стрима — тоже из настроек клипа (single = прежний рендер).
        if layout is None:
            layout = (plan.get("render_settings") or {}).get("layout")
        if cut_plan is not None:
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
                subtitle_offset_sec=subtitle_offset_sec,
                subtitle_provider=subtitle_provider,
                banner_height_frac=banner_height_frac,
                banner_y_frac=banner_y_frac,
                subtitle_margin_v=subtitle_margin_v,
                mirror=mirror,
                cover=cover,
                motion=visual,
                layout=layout,
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
                mirror=mirror,
                transition=transition,
                cover=cover,
                motion=visual,
                layout=layout,
            )
        if clip["status"] == "succeeded":
            clip = self._apply_mute(clip, plan, cut_plan)
            clip = self._apply_inserts(clip, plan, cut_plan)
            if motion:
                clip = self._apply_audio_layers(clip, motion)
            clip = self._normalize_loudness(clip)
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
        fresh = remap_motion(visual, self._finished_cut_mapper(plan, cut_plan))
        if had_words:
            # Слова пересчитываются из субтитров: вырез — до сдвига на переходы.
            fresh = {**fresh, "words": cut_caption_words(self.store, plan, cut_plan)}
        # Слой целиком в вырезе пропал — может не остаться ничего видимого.
        return fresh if (fresh.get("layers") or fresh.get("camera")) else None

    def _plan_subtitles_for_clip(self, clip_id: int, fps: float | None = None):
        """Субтитры, сделанные в редакторе до рендера (если есть)."""
        from app.clip_subtitles import result_from_payload
        from app.cuts import subtitles_to_final

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
            return result_from_payload(subtitles_to_final(payload, plan.get("segments") or [], transition, cut_plan))
        except (KeyError, ValueError, TypeError):
            return None

    def _apply_mute(self, clip: dict, plan: dict, cut_plan=None) -> dict:
        """Тишина на месте звука исходника (``render_settings.mute``, app/audio_mute.py).

        До вставок и звуков слоёв: они ложатся поверх и играют. Сбой не теряет клип."""
        from app.audio_mute import build_mute_args, normalize_mute

        mutes = normalize_mute((plan.get("render_settings") or {}).get("mute"))
        path = Path(clip.get("output_path") or "")
        if not mutes or not path.exists() or not _has_audio_stream(path):
            return clip
        spans = [(m["start"], m["end"]) for m in mutes]
        if cut_plan is not None:
            mapper = self._finished_cut_mapper(plan, cut_plan)
            spans = [r for r in (mapper.range(a, b) for a, b in spans) if r]
        try:
            tmp = path.with_name(path.stem + ".mute" + path.suffix)
            args = build_mute_args(path, tmp, spans)
            if args is None:
                return clip
            _run_ffmpeg(args, timeout=60 * 20)
            tmp.replace(path)
            return self.store.update_clip(clip["id"], size_bytes=path.stat().st_size)
        except Exception as exc:  # noqa: BLE001 - тишина — надстройка, клип важнее
            return self._note_clip_error(clip["id"], f"тишина не наложена: {str(exc)[:200]}")

    def _apply_inserts(self, clip: dict, plan: dict, cut_plan=None) -> dict:
        """Overlay the clip's «Файлы для монтажа» inserts on the finished render.

        One extra ffmpeg pass on the output file; a failure keeps the render
        without inserts and says so in the clip's error, never loses the clip.
        """
        from app.montage_assets import build_inserts_args, normalize_inserts

        inserts = normalize_inserts((plan.get("render_settings") or {}).get("inserts"))
        if cut_plan is not None and inserts:
            from app.cuts import remap_inserts

            inserts = remap_inserts(inserts, self._finished_cut_mapper(plan, cut_plan))
        path = Path(clip.get("output_path") or "")
        if not inserts or not path.exists():
            return clip
        assets = {}
        for ins in inserts:
            try:
                assets[ins["asset_id"]] = self.store.get_montage_asset(ins["asset_id"])
            except KeyError:
                pass
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
    ) -> list[str]:
        """Камера и слои поверх основы (на месте ``video``), затем баннер.

        Основа отрендерена без баннера — он ложится тут, поверх слоёв, чтобы
        камера его не зумила. Падение компоновщика не теряет клип: остаётся
        основа (с баннером), а причина — в пометках для ошибки клипа.
        Возвращает пометки.
        """
        from app.motion_render import (
            build_compositor_args,
            build_motion_mux_args,
            resolve_layer_assets,
            write_comp,
        )

        notes: list[str] = []
        work = settings.tmp_dir / f"motion-{render_id}"
        work.mkdir(parents=True, exist_ok=True)
        try:
            layers, assets, missing = resolve_layer_assets(self.store, motion.get("layers") or [])
            notes.extend(missing)
            notes.extend(motion.get("host_notes") or [])
            assets.update(motion.get("chat_assets") or {})
            camera = motion.get("camera") or []
            drawn = video
            if layers or camera or motion.get("key"):
                try:
                    meta = probe_media(video)
                    comp = write_comp(
                        work / "comp.json",
                        width=meta.width or int(preset.get("output_width") or 1080),
                        height=meta.height or int(preset.get("output_height") or 1920),
                        fps=float(preset.get("fps") or 30),
                        duration=meta.duration_sec,
                        layers=layers,
                        camera=camera,
                        words=motion.get("words"),
                        chat=motion.get("chat"),
                        key=motion.get("key"),
                        background=motion.get("background"),
                    )
                    assets_path = work / "assets.json"
                    assets_path.write_text(json.dumps(assets, ensure_ascii=False), encoding="utf-8")
                    out = work / "motion.mp4"
                    _run_compositor(
                        build_compositor_args(
                            comp, assets_path, input_path=video, output_path=out,
                            crf=_json_dict(preset.get("extra_json")).get("crf"),
                        ),
                        timeout=settings.motion_timeout_seconds,
                    )
                    if not out.is_file() or out.stat().st_size == 0:
                        raise RuntimeError("компоновщик не записал видео")
                    drawn = out
                except Exception as exc:  # noqa: BLE001 - моушн — надстройка, клип важнее
                    notes.append(f"моушн не наложен: {_safe_error(exc)[:300]}")
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

    def _apply_audio_layers(self, clip: dict, motion: dict) -> dict:
        """Звуки ``audio``-слоёв в готовый клип — последним проходом, видео копией.

        Последним, а не в моушн-проходе: субтитры распознаются по звуку основы,
        и бум/свист поверх речи мешал бы распознаванию (как и музыка). Ошибка не
        теряет клип — пишется в его ошибку.
        """
        from app.motion_render import build_audio_layers_args, resolve_layer_assets

        layers = [layer for layer in motion.get("layers") or [] if layer["type"] == "audio"]
        path = Path(clip.get("output_path") or "")
        if not layers or not path.exists():
            return clip
        try:
            layers, assets, notes = resolve_layer_assets(self.store, layers)
            meta = probe_media(path)
            tmp = path.with_name(path.stem + ".sfx" + path.suffix)
            from app.motion_render import speech_level_db

            has_audio = _has_audio_stream(path)
            args = build_audio_layers_args(
                path, tmp, layers, assets, meta.duration_sec, clip_has_audio=has_audio,
                speech_db=speech_level_db(path) if has_audio else None,
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

    def _normalize_loudness(self, clip: dict) -> dict:
        """Громкость под площадки: −14 LUFS и пик не выше −1.5 dBFS.

        Тихий рэп выходил на −20 LUFS, а клип со звуками — с пиком выше нуля:
        TikTok/Shorts всё равно приводят громкость к своей, только тихий ролик
        тогда звучит хуже соседей, а громкий хрипит. Два прохода loudnorm:
        замер, потом линейная поправка по замеру — без «качелей» громкости.
        Заодно — финал: стоп-кадр RENDER_END_HOLD_SEC и затухание звука на нём.
        Сбой не теряет клип.
        """
        if not settings.render_loudnorm:  # RENDER_LOUDNORM=0 выключает весь финальный проход звука
            return clip
        path = Path(clip.get("output_path") or "")
        if not path.exists() or not _has_audio_stream(path):
            return clip
        target = settings.render_loudnorm_target
        try:
            probe = subprocess.run(
                ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn",
                 "-af", f"loudnorm=I={target}:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"],
                capture_output=True, text=True, timeout=60 * 10,
            )
            tail = probe.stderr[probe.stderr.rfind("{"):]
            measured = json.loads(tail[: tail.find("}") + 1])
            if not audible(measured):
                return clip  # немой клип: ни громкость, ни затухание не нужны
            hold = max(0.0, float(settings.render_end_hold_sec or 0))
            filters = [f"apad=pad_dur={hold:.3f}"] if hold > 0 else []
            import logging

            logging.getLogger("uvicorn.error").info(
                "loudness clip=%s input_i=%s input_tp=%s needed=%s gain=%.2f",
                clip.get("id"), measured.get("input_i"), measured.get("input_tp"),
                loudnorm_needed(measured, target), linear_gain_db(measured, target),
            )
            if loudnorm_needed(measured, target):
                # Только линейная поправка (одно число на весь клип) + лимитер на пики.
                # loudnorm второго прохода, когда линейно не влезает в пик, молча
                # переходит в динамический режим и вытягивает тихие места на 10–14 дБ:
                # фоновые звуки и эффекты становились громче речи («звуки пердежа
                # во всю громкость»).
                filters.append(f"volume={linear_gain_db(measured, target):.2f}dB")
                filters.append("alimiter=limit=0.84:attack=5:release=80:level=disabled")
            # Затухание — на конце самого звука, а не на добавленной тишине стоп-кадра:
            # иначе на речь приходилась 0.1 с и звук обрывался.
            fade = end_fade_filter(float(probe_media(path).duration_sec or 0))
            if fade:
                filters.append(fade)
            if not filters:
                return clip
            tmp = path.with_name(path.stem + ".loud" + path.suffix)
            af = ",".join(filters + ["aresample=48000", "aformat=sample_fmts=fltp:channel_layouts=stereo"])
            # Стоп-кадр в конце: последний кадр держится, пока затухает звук —
            # владелец: «сейчас она просто обрывается жёстко». Тогда картинку надо
            # перекодировать (tpad), иначе — копия без потерь.
            video = (["-vf", f"tpad=stop_mode=clone:stop_duration={hold:.3f}", "-c:v", "libx264", "-crf", "18",
                      "-preset", "veryfast", "-pix_fmt", "yuv420p"] if hold > 0 else ["-c:v", "copy"])
            _run_ffmpeg(
                ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(path), "-map", "0:v:0", "-map", "0:a:0",
                 *video, "-af", af, "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(tmp)],
                timeout=60 * 10,
            )
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
        profile = self.store.get_subtitle_profile(subtitle_profile_id)
        profile["prompt"] = _subtitle_prompt_for_clip(self.store, clip_id)
        ready = self._plan_subtitles_for_clip(clip_id, float(preset.get("fps") or 30))
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
            # Ровно столько кадров, сколько в куске: иначе fps-фильтр добавляет кадр
            # на каждый кусок, и при 18 вырезах картинка уезжала от звука на 0.57 с.
            "-frames:v",
            str(max(1, int(duration * fps + 0.5))),
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

    raw = music.get("volume")
    user = max(0.0, float(raw if raw is not None else MUSIC_NOMINAL_VOLUME))
    if user <= 0:
        return 0.0
    speech = speech_level_db(clip_path)
    track = audio_levels(music["track_path"], seconds=60)
    if speech is None or not track:
        return user
    gain_db = speech - MUSIC_UNDER_SPEECH_DB - track[0] + 20 * math.log10(user / MUSIC_NOMINAL_VOLUME)
    return 10 ** (max(-40.0, min(6.0, gain_db)) / 20)


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
    layout = preset.get("layout")
    if layout_active(layout):
        # Раскладка стрима (вебка + геймплей): своя основа, дальше — как всегда.
        base = _layout_filter(width, height, fps, mode, anchor, normalize_layout(layout), source_crop, reframe_x)
    elif mode == "blur_background":
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
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:{ff_color(preset['key_fill']) if preset.get('key_fill') else 'black'},"
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
    return f"{base};{_banner_overlay(banner, preset, height, '1:v', source_label)}"


def _fit_box(
    mode: str,
    anchor: str,
    width: int,
    height: int,
    source_crop: dict | None,
    reframe_x: str | None,
    out: str,
) -> str:
    """Геймплей в коробку ``width``×``height`` тем же способом, что и обычная
    основа (cover с x(t) рефрейма / contain / blur_background) → ``[out]``."""
    if mode == "blur_background":
        prefix, (bg_in, fg_in) = _crop_prefix(source_crop, 2)
        return (
            f"{prefix}[{bg_in}]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},boxblur=24:2[{out}bg];"
            f"[{fg_in}]scale={width}:{height}:force_original_aspect_ratio=decrease[{out}fg];"
            f"[{out}bg][{out}fg]overlay=(main_w-overlay_w)/2:(main_h-overlay_h)/2,setsar=1[{out}]"
        )
    prefix, (vin,) = _crop_prefix(source_crop, 1)
    if mode == "contain":
        return (
            f"{prefix}[{vin}]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1[{out}]"
        )
    crop_y = {"top": "0", "bottom": f"ih-{height}"}.get(anchor, f"(ih-{height})/2")
    crop_x = reframe_x if reframe_x else f"(iw-{width})/2"
    return (
        f"{prefix}[{vin}]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}:{crop_x}:{crop_y},setsar=1[{out}]"
    )


def _layout_filter(
    width: int,
    height: int,
    fps: float,
    mode: str,
    anchor: str,
    layout: dict,
    source_crop: dict | None,
    reframe_x: str | None,
) -> str:
    """Основа ``[vbase]`` для раскладки стрима (app/stream_layout.py).

    Вебка вырезается из ВСЕГО кадра исходника (``cam`` — доли исходника, до
    content-crop), геймплей идёт обычным путём (content-crop, рефрейм x(t)).
    Зеркало, лук, баннер, моушн и склейки работают уже с собранным кадром.
    """
    cam = layout["cam"]
    cam_crop = (
        f"[0:v]crop=iw*{cam['w']:.5f}:ih*{cam['h']:.5f}:iw*{cam['x']:.5f}:ih*{cam['y']:.5f}"
    )
    border = scaled_px(float(layout["border"]["width"]), height)
    color = ff_color(layout["border"]["color"])
    tail = f"fps={_fps(fps)},format=yuv420p[vbase]"
    if layout["mode"] == "split":
        cam_h, gap, game_h = split_geometry(layout, height)
        top = layout["cam_pos"] != "bottom"
        game = _fit_box(mode, anchor, width, game_h, source_crop, reframe_x, "lgame")
        # cover: вебка заполняет полосу целиком, лишнее срезается по центру.
        cam_chain = (
            f"{cam_crop},scale={width}:{cam_h}:force_original_aspect_ratio=increase,"
            f"crop={width}:{cam_h},setsar=1"
        )
        if border:
            cam_chain += f",drawbox=x=0:y=0:w=iw:h=ih:color={color}:t={border}"
        if gap:
            cam_chain += f",pad={width}:{cam_h + gap}:0:{0 if top else gap}:color={color}"
        order = "[lcam][lgame]" if top else "[lgame][lcam]"
        return f"{game};{cam_chain}[lcam];{order}vstack=inputs=2,{tail}"
    if layout["mode"] == "meme":
        # Видео целиком (после обрезки полей) во всю ширину, сверху; ниже — фон.
        prefix, (vin,) = _crop_prefix(source_crop, 1)
        # video_scale > 1 — видео шире кадра по центру (персонаж на зелёнке крупнее).
        top = even_px(float(layout["video_y"]) * height) if layout["video_y"] > 0 else 0
        video_w = even_px(width * float(layout.get("video_scale") or 1.0))
        video = f"{prefix}[{vin}]scale={video_w}:-2,setsar=1[lvid]"
        background = f"color=c={ff_color(layout['bg'])}:s={width}x{height}:r={_fps(fps)}[lbg]"
        return f"{video};{background};[lbg][lvid]overlay=x={(width - video_w) // 2}:y={top}:shortest=1,{tail}"
    # pip_cam: обычный кадр + окошко вебки (центр и ширина — доли выхода).
    pip = layout["pip"]
    game = _fit_box(mode, anchor, width, height, source_crop, reframe_x, "lgame")
    box_w = min(width, even_px(float(pip["w"]) * width))
    inner = max(2, box_w - 2 * border)
    cam_chain = f"{cam_crop},scale={inner}:-2,setsar=1"
    if border:
        cam_chain += f",pad=iw+{2 * border}:ih+{2 * border}:{border}:{border}:color={color}"
    pos_x = f"clip({float(pip['x']):.4f}*W-w/2\\,0\\,W-w)"
    pos_y = f"clip({float(pip['y']):.4f}*H-h/2\\,0\\,H-h)"
    return f"{game};{cam_chain}[lcam];[lgame][lcam]overlay=x={pos_x}:y={pos_y},{tail}"


def _banner_overlay(banner: dict, preset: dict, height: int, banner_input: str, source_label: str) -> str:
    """Баннер поверх ``source_label`` → ``[vout]``.

    Отдельно от основы, потому что с моушном баннер кладётся позже — поверх
    камеры и слоёв (см. app/motion_render.py), а выглядеть должен так же.
    """
    opacity = max(0, min(1, float(banner.get("opacity") if banner.get("opacity") is not None else 1)))
    x_expr, y_expr = _banner_position_expr(banner)
    # Per-render vertical position overrides the banner asset's saved placement so
    # it matches the preview band.
    y_frac = preset.get("banner_y_frac")
    if y_frac is not None:
        y_expr = str(int(round(float(y_frac) * height)))
    banner_chain = f"[{banner_input}]format=rgba,colorchannelmixer=aa={opacity}"
    bh_frac = preset.get("banner_height_frac")
    if bh_frac:
        banner_h = max(1, int(round(float(bh_frac) * height)))
        banner_chain += f",scale=-1:{banner_h}:flags=lanczos"
    banner_chain += "[banner]"
    return (
        f"{banner_chain};"
        f"[{source_label}][banner]overlay={x_expr}:{y_expr}:shortest=1,format=yuv420p[vout]"
    )


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
    grade = _video_style_filter({**preset, "vignette": 0})
    return base, {**motion, "key": {**key, "grade": grade}}


def _video_style_filter(preset: dict) -> str:
    """Build the color-grade / vignette / film-grain filter chain for a preset.

    Returns a comma-joined ffmpeg filter string (no leading/trailing comma), or
    an empty string when the preset asks for no look. Strength scales the
    intensity of the parametric color styles so the same preset can be subtle or
    heavy. Vignette and grain are added on top of the color grade.
    """

    parts: list[str] = []
    # Horizontal mirror (swap left/right) — applied after the 9:16 crop so only the
    # final frame is flipped. Handy for making reposts look different.
    if preset.get("mirror"):
        parts.append("hflip")
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


def _video_duration(meta: Any) -> float:
    """Длина видеодорожки куска (контейнер берёт самую длинную дорожку)."""
    for stream in (getattr(meta, "raw", None) or {}).get("streams") or []:
        if stream.get("codec_type") == "video":
            try:
                value = float(stream.get("duration") or 0)
            except (TypeError, ValueError):
                value = 0.0
            if value > 0:
                return value
    return float(getattr(meta, "duration_sec", 0) or 0)


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


def _run_compositor(args: list[str], timeout: int) -> None:
    """Node-компоновщик моушна. Строки ``progress i/n`` в stderr — не ошибка, их
    в сообщение не тащим: прогресса рендера в сервисе нет, а причину падения
    из-за них не было бы видно."""
    proc = subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        lines = [line for line in (proc.stderr or "").splitlines() if not line.startswith("progress ")]
        detail = "\n".join(lines[-15:]).strip() or (proc.stdout or "").strip()[-500:]
        raise RuntimeError(_safe_error(detail or f"компоновщик завершился с кодом {proc.returncode}"))


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


def _has_audio_stream(path: Path) -> bool:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return bool(proc.stdout.strip())
