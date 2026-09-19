from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from app.render_qc import parse_qc, run_qc

LOG = """
[blackdetect @ 0x1] black_start:0 black_end:0.8 black_duration:0.8
[freezedetect @ 0x2] lavfi.freezedetect.freeze_start: 4.2
[freezedetect @ 0x2] lavfi.freezedetect.freeze_duration: 3.1
[silencedetect @ 0x3] silence_end: 12.5 | silence_duration: 2.5
[Parsed_ebur128_1 @ 0x4] t: 1.0 M: -30.0 S: -30.0 I: -30.0 LUFS
[Parsed_ebur128_1 @ 0x4] Summary:
  Integrated loudness:
    I:         -23.4 LUFS
  True peak:
    Peak:       -0.2 dBFS
"""


def test_parse_reads_the_summary_not_the_running_log():
    qc = parse_qc(LOG, duration_sec=20, width=1080, height=1920)
    assert qc["loudness_lufs"] == -23.4
    assert qc["true_peak_dbfs"] == -0.2
    assert qc["black"] == [{"start": 0.0, "duration": 0.8}]
    assert qc["freezes"] == [{"start": 4.2, "duration": 3.1}]
    assert qc["silences"] == [{"start": 10.0, "duration": 2.5}]
    codes = {i["code"] for i in qc["issues"]}
    assert codes == {"black", "freeze", "silence", "quiet", "peak"}
    assert qc["ok"] is False


def test_clean_clip_is_ok():
    log = "    I:         -14.1 LUFS\n    Peak:       -2.0 dBFS\n"
    qc = parse_qc(log, duration_sec=30, width=1080, height=1920)
    assert qc["ok"] is True and qc["issues"] == []


def test_long_and_silent_file_flags():
    qc = parse_qc("", duration_sec=200, width=720, height=1280)
    codes = {i["code"] for i in qc["issues"]}
    assert {"no_audio", "long", "size"} <= codes


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_real_file_with_black_start_and_silence(tmp_path: Path):
    out = tmp_path / "qc.mp4"
    # 1 s black then colour; 3 s of silence then a tone.
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=black:s=108x192:r=25:d=1",
            "-f", "lavfi", "-i", "testsrc=s=108x192:r=25:d=5",
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo:d=3",
            "-f", "lavfi", "-i", "sine=f=440:d=3:sample_rate=48000",
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v];[3:a]aformat=channel_layouts=stereo[t];[2:a][t]concat=n=2:v=0:a=1[a]",
            "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(out),
        ],
        check=True,
    )
    qc = run_qc(out, duration_sec=6, width=108, height=192)
    assert qc is not None
    codes = {i["code"] for i in qc["issues"]}
    assert "black" in codes and "silence" in codes
