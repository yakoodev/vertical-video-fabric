import subprocess

from app.audio_mute import build_mute_args, mute_volume_expr, normalize_mute
from app.clip_settings import normalize_render_settings
from test_api_pipeline import _require_ffmpeg


def test_normalize_mute_merges_and_drops_junk():
    raw = [{"start": 3, "end": 4}, {"start": 1, "end": 2}, {"start": 1.5, "end": 2.5}, {"start": 5, "end": 5.01},
           {"start": "x", "end": 1}, "junk"]
    assert normalize_mute(raw) == [{"start": 1.0, "end": 2.5}, {"start": 3.0, "end": 4.0}]
    assert normalize_render_settings({"mute": raw})["mute"] == normalize_mute(raw)
    assert normalize_render_settings({})["mute"] == []


def test_mute_expr_is_zero_inside_and_one_outside():
    expr = mute_volume_expr([(1.0, 2.0)])

    def value(t):
        # То же выражение, что считает ffmpeg: clip/max и переменная t.
        return eval(expr, {"clip": lambda v, lo, hi: min(hi, max(lo, v)), "max": max, "t": t})

    assert value(1.5) == 0
    assert value(0.5) == 1 and value(2.5) == 1
    assert 0 < value(2.015) < 1  # рампа, без щелчка


def _rms(path, start, dur):
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-ss", str(start), "-t", str(dur), "-i", str(path),
         "-af", "astats=metadata=0", "-f", "null", "-"], capture_output=True, text=True,
    ).stderr
    line = [l for l in out.splitlines() if "RMS level dB" in l][-1]
    return float(line.rsplit(":", 1)[1])


def test_mute_pass_silences_only_the_range(tmp_path):
    _require_ffmpeg()
    src = tmp_path / "c.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=64x64:d=3",
                    "-f", "lavfi", "-i", "sine=f=440:d=3", "-shortest", "-c:v", "libx264", "-c:a", "aac", str(src)],
                   check=True)
    out = tmp_path / "m.mp4"
    subprocess.run(build_mute_args(src, out, [(1.0, 2.0)]), check=True)
    assert _rms(out, 1.2, 0.6) < -60
    assert _rms(out, 0.2, 0.6) > -30 and _rms(out, 2.2, 0.6) > -30
