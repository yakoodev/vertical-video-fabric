"""Content-crop detection (strip letter/pillarbox bars) and dynamic reframe math.

``detect_content_crop`` probes a few seconds with ffmpeg ``cropdetect`` and returns
a normalized rect. ``build_reframe_x_expr`` turns a sparse point-of-interest track
into a smooth, velocity-limited ffmpeg crop-x expression (see Stage B).
"""

from __future__ import annotations

import re
import subprocess
from collections import Counter
from pathlib import Path

_CROP_RE = re.compile(r"crop=(\d+):(\d+):(\d+):(\d+)")


def detect_content_crop(source: dict) -> dict | None:
    """Return a normalized {x,y,w,h} content rect, or None if the frame is full."""
    local = str(source.get("local_path") or "")
    sw = int(source.get("width") or 0)
    sh = int(source.get("height") or 0)
    duration = float(source.get("duration_sec") or 0)
    if not local or not Path(local).exists():
        raise ValueError("source has no playable file")
    if sw <= 0 or sh <= 0:
        raise ValueError("source dimensions are unknown")

    start = max(0.0, duration * 0.3) if duration else 0.0
    proc = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-ss",
            f"{start:.2f}",
            "-t",
            "4",
            "-i",
            local,
            "-vf",
            "cropdetect=24:2:0",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    matches = _CROP_RE.findall(proc.stderr or "")
    if not matches:
        return None
    w, h, x, y = (int(v) for v in Counter(matches).most_common(1)[0][0])
    if w <= 0 or h <= 0:
        return None
    crop = {"x": x / sw, "y": y / sh, "w": w / sw, "h": h / sh}
    # Near-full frame → there were no real bars worth cropping.
    if crop["w"] >= 0.985 and crop["h"] >= 0.985:
        return None
    return {k: round(v, 5) for k, v in crop.items()}


def _smooth_damp_series(xs: list[float], dt: float, smooth_time: float, rubber: float = 2.2) -> list[float]:
    """Critically-damped follow (SmoothDamp) with a rubber-band feel: eases toward
    each target with no hard switches, but the farther the target the shorter the
    effective settle time, so big jumps catch up faster while small moves stay gentle.
    """
    if smooth_time <= 0 or len(xs) < 2:
        return xs
    cur = xs[0]
    vel = 0.0
    out = [cur]
    for i in range(1, len(xs)):
        target = xs[i]
        st = smooth_time / (1.0 + rubber * abs(cur - target))  # rubber band: far → snappier
        omega = 2.0 / st
        x = omega * dt
        exp = 1.0 / (1.0 + x + 0.48 * x * x + 0.235 * x * x * x)
        change = cur - target
        temp = (vel + omega * change) * dt
        vel = (vel - omega * temp) * exp
        cur = target + (change + temp) * exp
        out.append(cur)
    return out


def build_reframe_x_expr(
    focus_points: list[dict],
    duration: float,
    output_width: int,
    *,
    max_anchors: int = 40,
    smooth_time: float = 1.1,
) -> str | None:
    """Turn sparse focus points into a smooth ffmpeg crop-x expression.

    ``focus_points`` are ``{t, x}`` with ``t`` seconds from the segment start and
    ``x`` the normalized horizontal centre (0..1) in the scaled frame. The path is
    resampled and run through a critically-damped smoother (SmoothDamp) so the camera
    eases in and out and never snaps to a new coordinate, then emitted as a piecewise
    expression clamped to the valid crop range. Returns None if there's nothing to
    pan to. Commas are escaped for embedding inside a filtergraph.
    """
    if duration <= 0:
        return None
    pts: list[tuple[float, float]] = []
    for p in focus_points:
        try:
            t = float(p["t"])
            x = float(p["x"])
        except (KeyError, TypeError, ValueError):
            continue
        pts.append((min(duration, max(0.0, t)), min(1.0, max(0.0, x))))
    if not pts:
        return None
    pts.sort(key=lambda a: a[0])

    # No hard centre-gate: real focus comes from the detector / manual points (the
    # LLM now emits a single point), so we trust the track and let the box low-pass +
    # hysteresis hold below absorb jitter while still following the subject.
    dt = 0.2
    n = max(2, int(round(duration / dt)) + 1)

    def interp(t: float) -> float:
        if t <= pts[0][0]:
            return pts[0][1]
        if t >= pts[-1][0]:
            return pts[-1][1]
        for i in range(len(pts) - 1):
            t0, x0 = pts[i]
            t1, x1 = pts[i + 1]
            if t0 <= t <= t1:
                return x0 if t1 == t0 else x0 + (x1 - x0) * (t - t0) / (t1 - t0)
        return pts[-1][1]

    xs = [interp(i * dt) for i in range(n)]
    xs = _smooth_damp_series(xs, dt, smooth_time)

    if n > max_anchors:
        idxs = [round(i * (n - 1) / (max_anchors - 1)) for i in range(max_anchors)]
    else:
        idxs = list(range(n))
    anchors = [(round(i * dt, 3), round(xs[i], 5)) for i in idxs]

    # Build the normalized X(t) as a nested piecewise-linear expression.
    expr = f"{anchors[-1][1]:.5f}"
    for i in range(len(anchors) - 2, -1, -1):
        t0, x0 = anchors[i]
        t1, x1 = anchors[i + 1]
        seg = max(1e-3, t1 - t0)
        lerp = f"({x0:.5f}+({x1 - x0:.5f})*(t-{t0:.3f})/{seg:.3f})"
        expr = f"if(lt(t\\,{t1:.3f})\\,{lerp}\\,{expr})"

    half_w = output_width / 2.0
    return f"clip(({expr})*iw-{half_w:.1f}\\,0\\,iw-{output_width})"
