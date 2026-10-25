"""Куски клипа с «шире окна» (fit) склеиваются: у всех кусков SAR 1:1.

Обычный кусок (scale …:force_original_aspect_ratio) выходит с SAR 10240:10239, кусок
fit — 1:1; concat-фильтр склейки таких не принимает. NUT хранит SAR как есть (mkv округляет)."""

import shutil
import subprocess

import pytest

from app.render import _needs_sar_fix, _render_filter

HAS_FFMPEG = bool(shutil.which("ffmpeg"))
COVER = {"output_width": 1080, "output_height": 1920, "fps": 30}
FIT = {**COVER, "layout": None, "piece_fit": {"w": 0.6, "bg": "blur"}}


def test_sar_fix_only_when_a_fit_piece_is_in_the_clip():
    assert _needs_sar_fix([{"start_sec": 0, "end_sec": 3}, {"start_sec": 5, "end_sec": 8, "fit": {"w": 0.6}}])
    assert not _needs_sar_fix([{"start_sec": 0, "end_sec": 3}, {"start_sec": 5, "end_sec": 8}])
    assert not _needs_sar_fix([{"start_sec": 0, "end_sec": 3, "fit": {"w": 0.6}}])  # один кусок — склейки нет


def test_normalize_sar_adds_setsar_before_output():
    plain = _render_filter(1080, 1920, 30, COVER, None)
    fixed = _render_filter(1080, 1920, 30, {**COVER, "normalize_sar": True}, None)
    assert "setsar" not in plain  # без fit — прежняя команда
    assert "[vbase]setsar=1[vsar]" in fixed and fixed.endswith("[vsar]copy[vout]")
    styled = _render_filter(1080, 1920, 30, {**COVER, "normalize_sar": True, "saturation": 1.2}, None)
    assert "[vbase]setsar=1[vsar]" in styled


def _piece(tmp_path, name, preset):
    out = tmp_path / f"{name}.nut"
    fc = _render_filter(1080, 1920, 30, preset, None)
    proc = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=1920x1080:r=30:d=0.3,setsar=1",
         "-filter_complex", fc, "-map", "[vout]", "-c:v", "ffv1", str(out)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-400:]
    return out


def _concat(a, b):
    return subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "error", "-i", str(a), "-i", str(b),
         "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-f", "null", "-"],
        capture_output=True, text=True, timeout=120,
    )


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg не установлен")
def test_cover_and_fit_pieces_concat_with_real_ffmpeg(tmp_path):
    fit = _piece(tmp_path, "fit", {**FIT, "normalize_sar": True})
    cover_old = _piece(tmp_path, "cover_old", COVER)
    cover = _piece(tmp_path, "cover", {**COVER, "normalize_sar": True})
    broken = _concat(cover_old, fit)
    assert broken.returncode != 0 and "SAR" in broken.stderr  # воспроизведение: SAR куска ≠ 1:1
    fixed = _concat(cover, fit)
    assert fixed.returncode == 0, fixed.stderr[-400:]
