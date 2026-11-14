"""Плёнка PSX-серий под TikTok (app/psx_look.py): короткие помехи и файл для заливки (lag_report.md серии 2)."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app import psx_look as L

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def test_burst_is_short_soft_and_on_frame_grid():
    keys = L.static_burst(2.0, peak=0.9)
    lv = L.static_levels(keys, 120)
    runs = L.static_runs(lv)
    assert len(runs) == 1 and runs[0]["frames"] == L.BURST_FRAMES
    assert runs[0]["start"] == 61  # кадр t (60) ещё чистый
    assert runs[0]["peak"] == L.BURST_PEAK_MAX  # 0.9 срезано до потолка: сквозь помехи виден кадр
    assert lv[60] == 0.0 and lv[61 + L.BURST_FRAMES] == 0.0
    assert all(k["t"] == round(k["t"] * 30) / 30 for k in keys)  # ключи ровно на кадрах (кадр / fps)
    assert L.static_runs(L.static_levels(L.static_burst(1.0, frames=20), 90))[0]["frames"] == L.BURST_FRAMES  # длиннее нельзя


def test_burst_shape_rises_fast_and_fades():
    s = L.burst_shape(6)
    assert len(s) == 6 and max(s) == 1.0 and s.index(1.0) == 1
    assert all(a >= b for a, b in zip(s[1:], s[2:]))  # после пика только гаснет
    assert L.burst_shape(1) == [1.0]


def test_cut_burst_peaks_on_the_cut_frame():
    lv = L.static_levels(L.cut_burst(3.0, peak=0.4), 150)
    assert max(range(150), key=lambda f: lv[f]) == 90
    assert lv[90] == pytest.approx(0.4)


def test_tail_static_is_last_frames_only():
    total = 50.0
    n = int(total * 30)
    lv = L.static_levels(L.tail_static(total), n)
    runs = L.static_runs(lv)
    assert runs == [{"start": n - L.TAIL_FRAMES, "frames": L.TAIL_FRAMES, "peak": L.TAIL_PEAK}]
    assert all(b > a for a, b in zip(lv[n - L.TAIL_FRAMES:], lv[n - L.TAIL_FRAMES + 1:]))  # нарастает к последнему кадру


def test_static_levels_match_fx_ts_rules():
    keys = [{"t": 0.0, "static": 0.0}, {"t": 1.0, "static": 1.0}, {"t": 0.5, "black": 1.0}]
    lv = L.static_levels(keys, 40)
    assert lv[0] == 0.0 and lv[15] == pytest.approx(0.5) and lv[39] == 1.0  # за последним ключом — его уровень
    assert L.static_levels([], 3) == [0.0, 0.0, 0.0]
    assert L.static_levels([{"t": 0, "static": 0.9}], 2, base=0.5) == [1.0, 1.0]


def test_tiktok_args_has_the_upload_rules():
    args = L.tiktok_args(Path("in.mp4"), Path("out.mp4"), 1500, audio=Path("mix.mp4"))
    s = " ".join(args)
    for part in ("-c:v libx264", "-profile:v high", "-level:v 4.0", "-pix_fmt yuv420p", "-crf 20", "-maxrate 8M", "-bufsize 16M",
                 "-g 30", "-keyint_min 30", "-sc_threshold 0", "-fps_mode cfr", "-r 30", "-c:a aac", "-b:a 192k", "-ar 48000",
                 "-movflags +faststart", "-frames:v 1500", "-map 0:v:0", "-map 1:a:0", "apad,atrim=0:50.000000", "-t 50.000000"):
        assert part in s, part
    assert "-map 0:a:0" in " ".join(L.tiktok_args(Path("a.mp4"), Path("b.mp4"), 30))


@pytest.mark.skipif(not HAS_FFMPEG, reason="нет ffmpeg")
def test_tiktok_export_passes_check_and_bad_file_does_not(tmp_path):
    # Исходник «как после сведения»: 25 к/с, ключевые кадры редко, без faststart, звук длиннее картинки.
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=270x480:rate=25:duration=2.4",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=3", "-c:v", "libx264", "-g", "250",
                    "-profile:v", "high", "-level:v", "4.2", "-c:a", "aac", str(src)], check=True)
    bad = L.check_tiktok(src)
    assert not bad["ok"]
    joined = " ".join(bad["issues"])
    assert "к/с" in joined and "faststart" in joined and "Гц" in joined and "длины" in joined and "level" in joined
    out = tmp_path / "tt.mp4"
    subprocess.run(L.tiktok_args(src, out, 60), check=True)
    rep = L.check_tiktok(out)
    assert rep["ok"], rep["issues"]
    assert rep["frames"] == 60 and rep["gop_frames"] <= 30 and rep["faststart"] and rep["level"] <= 40
    assert abs(rep["audio_s"] - rep["video_s"]) <= L.AAC_FRAME_S + 1e-3


def test_moov_first_reads_atoms(tmp_path):
    f = tmp_path / "x.mp4"
    box = lambda kind, body=b"": (8 + len(body)).to_bytes(4, "big") + kind + body  # noqa: E731
    f.write_bytes(box(b"ftyp", b"isom0000") + box(b"moov", b"\0" * 8) + box(b"mdat", b"\0" * 16))
    assert L.moov_first(f)
    f.write_bytes(box(b"ftyp", b"isom0000") + box(b"mdat", b"\0" * 16) + box(b"moov", b"\0" * 8))
    assert not L.moov_first(f)


def test_tts_wait_only_on_passing_errors():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import viewer_ep3 as T3

    assert T3.tts_unavailable(RuntimeError("Gemini TTS 400: User location is not supported for the API use."))
    assert T3.tts_unavailable(RuntimeError("Gemini TTS не ответил: сеть: ConnectError"))
    assert T3.tts_unavailable(ValueError("Gemini TTS: дневной лимит бесплатного тарифа исчерпан"))
    assert not T3.tts_unavailable(RuntimeError("Gemini TTS: ключ не подошёл (403: denied)"))
