from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.transitions import (
    TRANSITIONS,
    build_join_args,
    effective_duration,
    is_plain_concat,
    join_times,
    normalize_transition,
)


def test_normalize_fills_defaults_and_rejects_junk():
    t = normalize_transition({"type": "nope", "duration": "x", "audio": "loud", "sfx": "boom", "sfx_volume": 9})
    assert t == {"type": "cut", "duration": 0.35, "audio": "smooth", "sfx": "none", "sfx_volume": 1.0}
    assert normalize_transition(None)["type"] == "cut"


def test_duration_is_clamped_and_flash_is_forced_short():
    assert normalize_transition({"type": "fade", "duration": 99})["duration"] == 1.5
    assert normalize_transition({"type": "fade", "duration": 0})["duration"] == 0.08
    assert normalize_transition({"type": "flash", "duration": 1.0})["duration"] == 0.16


def test_plain_concat_only_for_hard_cut_hard_sound_no_sfx():
    assert is_plain_concat(normalize_transition({"type": "cut", "audio": "hard"}))
    # Default audio is smooth: the join gets a de-click, so it is NOT a plain concat.
    assert not is_plain_concat(normalize_transition({"type": "cut"}))
    assert not is_plain_concat(normalize_transition({"type": "cut", "audio": "hard", "sfx": "click"}))
    assert not is_plain_concat(normalize_transition({"type": "fade", "audio": "hard"}))


def test_transition_never_eats_more_than_40_percent_of_a_part():
    t = normalize_transition({"type": "fade", "duration": 1.0})
    assert effective_duration(t, [10.0, 1.0, 10.0]) == pytest.approx(0.4)
    assert effective_duration(normalize_transition({"type": "cut"}), [5.0, 5.0]) == 0.0


def test_join_times_account_for_overlap():
    # Parts 4s, 5s, 3s with 0.5s transitions: joins at 4-0.25 and 9-1.0+0.25.
    assert join_times([4.0, 5.0, 3.0], 0.5) == pytest.approx([3.75, 8.25])
    assert join_times([4.0, 5.0], 0.0) == pytest.approx([4.0])


def test_xfade_offsets_are_cumulative():
    t = normalize_transition({"type": "fade", "duration": 0.5})
    args = build_join_args([Path("a.mp4"), Path("b.mp4"), Path("c.mp4")], [4.0, 5.0, 3.0], Path("out.mp4"), t, has_audio=True)
    graph = args[args.index("-filter_complex") + 1]
    assert "xfade=transition=fade:duration=0.500:offset=3.500" in graph
    assert "xfade=transition=fade:duration=0.500:offset=8.000" in graph
    assert "acrossfade=d=0.500" in graph


def test_no_audio_means_no_audio_chain():
    t = normalize_transition({"type": "fade"})
    args = build_join_args([Path("a.mp4"), Path("b.mp4")], [3.0, 3.0], Path("o.mp4"), t, has_audio=False)
    assert "-an" in args
    assert "acrossfade" not in args[args.index("-filter_complex") + 1]


# ---- real ffmpeg: every transition must actually run on the installed ffmpeg ----

FFMPEG = shutil.which("ffmpeg")


def _make_part(path: Path, color: str, seconds: float) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c={color}:s=108x192:r=30:d={seconds}",
            "-f", "lavfi", "-i", f"sine=f=440:d={seconds}",
            "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
        ],
        check=True,
    )


def _duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", str(path)],
        check=True, capture_output=True, text=True,
    )
    return float(json.loads(out.stdout)["format"]["duration"])


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not installed")
@pytest.mark.parametrize("kind", sorted(TRANSITIONS))
@pytest.mark.parametrize("audio", ["smooth", "hard"])
def test_every_transition_renders(tmp_path: Path, kind: str, audio: str):
    parts = []
    for i, color in enumerate(["red", "green", "blue"]):
        p = tmp_path / f"p{i}.mp4"
        _make_part(p, color, 2.0)
        parts.append(p)
    t = normalize_transition({"type": kind, "duration": 0.4, "audio": audio, "sfx": "whoosh"})
    out = tmp_path / "out.mp4"
    durations = [_duration(p) for p in parts]
    subprocess.run(build_join_args(parts, durations, out, t, has_audio=True), check=True)
    overlap = effective_duration(t, durations)
    expected = sum(durations) - 2 * overlap
    assert _duration(out) == pytest.approx(expected, abs=0.15)
