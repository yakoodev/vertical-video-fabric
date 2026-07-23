"""Scene-cut timestamps for a whole source via ffmpeg scenedetect.

Used by the "cuts" refinement hypothesis to pull clip boundaries onto real shot
changes. One cheap ffmpeg pass; the caller caches the result on the source.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_PTS_RE = re.compile(r"pts_time:(\d+(?:\.\d+)?)")


def detect_scene_cuts(local_path: str | None, threshold: float = 0.35) -> list[float]:
    """Return scene-change times (seconds), or [] on any failure."""
    if not local_path:
        return []
    path = Path(local_path)
    if not path.exists():
        return []
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
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
    times = sorted({round(float(m), 3) for m in _PTS_RE.findall(proc.stderr or "")})
    return times
