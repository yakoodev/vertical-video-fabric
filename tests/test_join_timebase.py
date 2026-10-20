"""Склейка «встык + переход»: xfade после concat не падает на разных шкалах времени."""

import subprocess

from app.transitions import build_join_args, normalize_transition, plan_joins
from test_api_pipeline import _require_ffmpeg


def _part(path, color):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color={color}:s=108x192:r=30:d=2",
         "-f", "lavfi", "-i", "sine=f=440:d=2", "-shortest", "-video_track_timescale", "15360",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)],
        check=True,
    )


def test_contiguous_join_then_whip_renders(tmp_path):
    _require_ffmpeg()
    parts = [tmp_path / f"p{k}.mp4" for k in range(3)]
    for path, color in zip(parts, ("red", "green", "blue")):
        _part(path, color)
    transition = normalize_transition({"type": "whip", "duration": 0.14, "smart": True, "audio": "smooth"})
    gaps = [0.0, 5.0]  # первая пара — встык (тот же кусок исходника), вторая — смена сцены
    assert plan_joins(transition, [2.0] * 3, gaps)[0] == 0 and plan_joins(transition, [2.0] * 3, gaps)[1] > 0
    out = tmp_path / "out.mp4"
    args = build_join_args(parts, [2.0] * 3, out, transition, has_audio=True, gaps=gaps)
    proc = subprocess.run(args, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[-800:]
    assert out.stat().st_size > 1000
