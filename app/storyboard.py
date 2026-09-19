"""Storyboard thumbnails: a strip of stop-frames sampled across a source video.

Frames are extracted lazily with per-frame fast-seek ffmpeg calls (cheap even for
long videos, unlike the `fps` filter which decodes the whole file) and cached on
disk under ``settings.storyboard_dir/<source_id>/``. The frontend plays them as a
hover slideshow / scrubber.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from app.settings import settings

THUMB_WIDTH = 320
MIN_FRAMES = 8
MAX_FRAMES = 24
SECONDS_PER_FRAME = 12.0


def _frame_count(duration: float) -> int:
    if duration <= 0:
        return MIN_FRAMES
    return max(MIN_FRAMES, min(MAX_FRAMES, round(duration / SECONDS_PER_FRAME)))


def storyboard_dir(source_id: int) -> Path:
    return settings.storyboard_dir / str(source_id)


def frame_path(source_id: int, index: int) -> Path:
    return storyboard_dir(source_id) / f"{index}.jpg"


def ensure_storyboard(source: dict) -> dict:
    """Generate (once) and return storyboard metadata for a source.

    Returns ``{"count", "interval_sec", "width"}``. Raises ``ValueError`` if the
    source has no playable file or duration.
    """
    source_id = int(source["id"])
    out_dir = storyboard_dir(source_id)
    meta_file = out_dir / "meta.json"
    if meta_file.exists():
        try:
            return json.loads(meta_file.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass  # corrupt cache — regenerate

    local_path = str(source.get("local_path") or "")
    duration = float(source.get("duration_sec") or 0)
    if not local_path or not Path(local_path).exists():
        raise ValueError("source has no playable file")
    if duration <= 0:
        raise ValueError("source duration is unknown")

    out_dir.mkdir(parents=True, exist_ok=True)
    count = _frame_count(duration)
    interval = duration / count
    for i in range(count):
        # Sample the middle of each segment so frames feel evenly spread.
        t = min(duration - 0.05, i * interval + interval / 2.0)
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                f"{max(0.0, t):.3f}",
                "-i",
                local_path,
                "-frames:v",
                "1",
                "-vf",
                f"scale={THUMB_WIDTH}:-2",
                "-q:v",
                "4",
                str(frame_path(source_id, i)),
            ],
            capture_output=True,
            timeout=30,
            check=False,
        )

    # Keep only frames that were actually written (a seek past EOF can fail).
    real = count
    while real > 0 and not frame_path(source_id, real - 1).exists():
        real -= 1
    if real == 0:
        raise ValueError("ffmpeg produced no frames")

    meta = {"count": real, "interval_sec": round(interval, 3), "width": THUMB_WIDTH}
    meta_file.write_text(json.dumps(meta), encoding="utf-8")
    return meta


PLAN_THUMB_HEIGHT = 480


def plan_thumb(source: dict, t: float, focus_x: float) -> Path:
    """A 9:16 still of a moment: the frame at ``t`` cropped around ``focus_x``.

    Cached per (t, x) so editing a moment's timing or focus yields a fresh
    thumbnail while repeat views stay a plain file read.
    """
    source_id = int(source["id"])
    local_path = str(source.get("local_path") or "")
    if not local_path or not Path(local_path).exists():
        raise ValueError("source has no playable file")
    x = min(1.0, max(0.0, float(focus_x)))
    out = storyboard_dir(source_id) / "plans" / f"{t:.2f}_{x:.3f}.jpg"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    # Crop a full-height 9:16 window centred on x (clamped to the frame), then
    # scale down. Sources narrower than 9:16 just keep their full width.
    crop = (
        "crop=w='min(iw,ih*9/16)':h=ih:"
        f"x='max(0,min(iw-min(iw,ih*9/16),iw*{x:.4f}-min(iw,ih*9/16)/2))':y=0"
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{max(0.0, t):.3f}",
            "-i",
            local_path,
            "-frames:v",
            "1",
            "-vf",
            f"{crop},scale=-2:{PLAN_THUMB_HEIGHT}",
            "-q:v",
            "5",
            str(out),
        ],
        capture_output=True,
        timeout=30,
        check=False,
    )
    if not out.exists():
        raise ValueError("ffmpeg produced no frame")
    return out
