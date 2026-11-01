"""Scene-cut timestamps for a whole source via ffmpeg scenedetect.

Used by the "cuts" refinement hypothesis to pull clip boundaries onto real shot
changes. One cheap ffmpeg pass; the caller caches the result on the source.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_PTS_RE = re.compile(r"pts_time:(\d+(?:\.\d+)?)")


def detect_scene_cuts(local_path: str | None, threshold: float = 0.35, *, start: float | None = None,
                      duration: float | None = None) -> list[float]:
    """Return scene-change times (seconds of the source), or [] on any failure.

    ``start``/``duration`` — only this window of the source (seconds still of the whole source):
    avatar_box looks for the shot change between two frames without decoding the whole stream."""
    if not local_path:
        return []
    path = Path(local_path)
    if not path.exists():
        return []
    window: list[str] = []
    offset = 0.0
    if start is not None:
        offset = max(0.0, float(start))
        window += ["-ss", f"{offset:.3f}"]
    if duration is not None:
        window += ["-t", f"{max(0.05, float(duration)):.3f}"]
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-nostats", *window, "-i", str(path),
                "-filter_complex", f"select='gt(scene,{threshold})',showinfo",
                "-f", "null", "-",
            ],
            capture_output=True,
            text=True,
            timeout=60 * 20,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    # showinfo writes to stderr.
    times = sorted({round(float(m) + offset, 3) for m in _PTS_RE.findall(proc.stderr or "")})
    return times
