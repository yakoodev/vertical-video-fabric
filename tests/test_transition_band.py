"""Переход-наплыв только в полосе видео (раскладка «полоса», meme): вспышка не заливает поля и место титров.

Пилот 17.11: белая вспышка на склейке шла во весь кадр — чёрные поля и зона субтитров мигали белым.
Теперь ``transition.scope`` (по умолчанию band при полосе) — xfade вырезается прямоугольником полосы поверх
жёсткой склейки посередине перехода. Настоящий ffmpeg на двух частях 64×128 по 1 с.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from app.transitions import build_join_args, join_picture_filters, normalize_transition, transition_region

BAND = {"x": 0, "y": 20, "w": 64, "h": 40}


def test_scope_normalized_only_when_given():
    assert "scope" not in normalize_transition({"type": "flash"})
    assert normalize_transition({"type": "flash", "scope": "полоса"})["scope"] == "band"
    assert normalize_transition({"type": "whip", "scope": "весь кадр"})["scope"] == "frame"
    assert "scope" not in normalize_transition({"type": "whip", "scope": "куда-то"})


def test_region_from_meme_layout():
    meme = {"mode": "meme", "video_y": 0.1}
    src = {"width": 1920, "height": 1080}
    t = normalize_transition({"type": "flash"})
    assert transition_region(t, meme, 1080, 1920, src) == {"x": 0, "y": 192, "w": 1080, "h": 608}
    assert transition_region({**t, "scope": "frame"}, meme, 1080, 1920, src) is None
    assert transition_region(t, {"mode": "split"}, 1080, 1920, src) is None
    assert transition_region(t, meme, 1080, 1920, src, parts=[{"fit": {"w": 0.5}}]) is None


def test_band_graph_keeps_length_and_overlays_band():
    t = normalize_transition({"type": "flash", "smart": False})
    plain = join_picture_filters(2, [1.0, 1.0], [0.12], t)
    banded = join_picture_filters(2, [1.0, 1.0], [0.12], t, region=BAND)
    assert "crop" not in ";".join(plain)
    graph = ";".join(banded)
    assert "xfade=transition=fadewhite:duration=0.120:offset=0.880,crop=64:40:0:20" in graph
    assert "trim=end=0.9400" in graph and "trim=start=0.0600" in graph and "overlay=x=0:y=20" in graph
    assert banded[-1].endswith("[vout]")


pytestmark_media = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="нужен ffmpeg")


def _part(path: Path, band_color: str) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=64x128:r=25:d=1",
         "-vf", f"drawbox=x={BAND['x']}:y={BAND['y']}:w={BAND['w']}:h={BAND['h']}:color={band_color}:t=fill",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)],
        check=True, timeout=60)
    return path


def _pixel(video: Path, t: float, x: int, y: int) -> tuple[int, int, int]:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1",
         "-vf", f"format=rgb24,crop=1:1:{x}:{y}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True, timeout=60).stdout
    return raw[0], raw[1], raw[2]


@pytestmark_media
@pytest.mark.parametrize("region", [None, BAND])
def test_flash_stays_inside_band(tmp_path, region):
    a = _part(tmp_path / "a.mp4", "0x404040")
    b = _part(tmp_path / "b.mp4", "0x2040c0")
    out = tmp_path / ("band.mp4" if region else "frame.mp4")
    t = normalize_transition({"type": "flash", "smart": False})
    args = build_join_args([a, b], [1.0, 1.0], out, t, has_audio=False, width=64, fps=25, region=region)
    subprocess.run(args, check=True, timeout=120)
    inside = _pixel(out, 0.94, 32, 40)  # середина вспышки — в полосе белое
    below = _pixel(out, 0.94, 32, 100)  # поле под полосой (место титров)
    assert min(inside) > 90, inside  # светлее обеих частей (серая 0x40 и синяя) — вспышка
    if region:
        assert max(below) < 40, f"вспышка вылезла из полосы: {below}"
    else:
        assert min(below) > 90, f"без полосы вспышка во весь кадр (воспроизведение): {below}"
    probe = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                            "stream=nb_read_frames", "-of", "csv=p=0", str(out)], capture_output=True, text=True)
    assert abs(int(probe.stdout.strip()) - 47) <= 1  # 2 × 25 кадров − наплыв 0.12 с (3 кадра)
