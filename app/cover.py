"""Clip covers: a still for the clip's preview, optionally burned in as its first frame.

Two sources for the cover:

* ``frame`` — a moment of the clip itself, chosen as (piece, offset into the
  piece). Taken from the RENDERED output, so it already has the look, subtitles
  and banner — exactly what a viewer would see at that moment.
* ``image`` — an uploaded picture (``POST /api/covers``), cover-fitted to the
  output size.

``burn`` overlays the cover on the first ``burn_sec`` of the video. Platforms that
take the preview from the first frame (Reels, most TikTok clients, feeds that
autoplay muted) then show the cover. Duration and sound are untouched.

Argument builders are pure (tested without media); the render service runs them.
"""

from __future__ import annotations

from pathlib import Path


def cover_output_time(
    durations: list[float], overlap: float | list[float], piece: int, offset: float
) -> float:
    """Where (piece, offset) lands on the output timeline.

    Каждая склейка перед ``piece`` укоротила выход на длину своего перехода.
    Переходы теперь бывают разной длины (где-то встык, где-то эффект), поэтому
    ``overlap`` — это либо одно число на все склейки, либо список по склейкам.
    Время зажимается внутри куска и внутри видео.
    """
    if not durations:
        return 0.0
    joins = len(durations) - 1
    per_join = [float(overlap)] * joins if isinstance(overlap, (int, float)) else list(overlap)
    per_join = (per_join + [0.0] * joins)[:joins]
    piece = min(max(0, piece), len(durations) - 1)
    start = sum(durations[:piece]) - sum(per_join[:piece])
    inside = min(max(0.0, offset), max(0.0, durations[piece] - 0.05))
    total = sum(durations) - sum(per_join)
    return round(min(max(0.0, start + inside), max(0.0, total - 0.05)), 3)


def build_frame_args(video: Path, t: float, out: Path) -> list[str]:
    """One full-quality frame of the rendered video at ``t``."""
    return [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{max(0.0, t):.3f}", "-i", str(video),
        "-frames:v", "1", "-q:v", "2", str(out),
    ]


def build_image_args(image: Path, width: int, height: int, out: Path) -> list[str]:
    """An uploaded picture fitted to the output: fill the frame, crop the excess."""
    return [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(image),
        "-vf", f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1",
        "-frames:v", "1", "-q:v", "2", str(out),
    ]


def build_burn_args(
    video: Path,
    cover: Path,
    out: Path,
    *,
    width: int,
    height: int,
    burn_sec: float,
    video_codec: str = "libx264",
    video_bitrate: str = "",
    crf: object = None,
) -> list[str]:
    """Overlay the cover on the first ``burn_sec`` seconds; sound is copied as is."""
    args = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(video),
        "-loop", "1", "-t", f"{burn_sec + 0.2:.3f}", "-i", str(cover),
        "-filter_complex",
        f"[1:v]scale={width}:{height},setsar=1,format=yuv420p[c];"
        f"[0:v][c]overlay=0:0:eof_action=pass:enable='lt(t,{burn_sec:.3f})'[v]",
        "-map", "[v]", "-map", "0:a?", "-c:a", "copy",
        "-c:v", video_codec or "libx264",
    ]
    if video_bitrate:
        args.extend(["-b:v", video_bitrate])
    if crf is not None:
        args.extend(["-crf", str(crf)])
    args.extend(["-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)])
    return args
