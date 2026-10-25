"""Выражение x(t) кадрирования разбирается ffmpeg на любом числе опор."""

import re
import shutil
import struct
import subprocess

import pytest

from app.video_crop import _piecewise_expr, build_reframe_x_expr

BS = chr(92)  # обратная косая: ffmpeg экранирует ею запятые
HAS_FFMPEG = bool(shutil.which("ffmpeg"))


def _eval(body: str, t: float) -> float:
    py = re.sub(r"if\(lt\(t,([\d.]+)\),", r"IF(t<\1,", body.replace(BS + ",", ","))
    return eval(py, {"IF": lambda c, a, b: a if c else b, "t": t})  # noqa: S307 - своё выражение


def _depth(expr: str) -> int:
    depth = best = 0
    for ch in expr:
        if ch == "(":
            depth += 1
            best = max(best, depth)
        elif ch == ")":
            depth -= 1
    return best


def _wavy_anchors(count: int) -> list[tuple[float, float]]:
    return [(round(i * 0.2, 3), round(0.5 + 0.3 * ((i * 7) % 11 - 5) / 5, 5)) for i in range(count)]


def test_120_anchors_shallow_tree_matches_anchors():
    anchors = _wavy_anchors(120)
    expr = _piecewise_expr(anchors, [None] * 119)
    assert expr.count("if(") == 119  # по сравнению на отрезок, но деревом, а не цепочкой
    assert _depth(expr) <= 12
    for t, x in anchors:
        assert abs(_eval(expr, t) - x) * 1920 < 1.0  # в пределах 1 px на ширине 1920
    t0, x0 = anchors[10]
    t1, x1 = anchors[11]
    assert abs(_eval(expr, (t0 + t1) / 2) - (x0 + x1) / 2) < 1e-4  # между опорами — линейно


def test_single_focus_point_on_20s_piece_is_flat_and_short():
    # Раньше: 101 опора без прореживания → 100 вложенных if → ffmpeg «Missing ')'».
    for dur in (20.0, 21.5, 24.0):
        expr = build_reframe_x_expr([{"t": 0, "x": 0.3}], dur, 1080)
        assert expr.count("if(") <= 2, dur


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg не установлен")
@pytest.mark.parametrize("dur", [20.0, 24.0])
def test_ffmpeg_parses_reframe_expr(dur):
    single = [{"t": 0, "x": 0.3}]
    # «Живой» трек из 120 точек — опор много, цепочкой вложенность была бы > 100.
    track = [{"t": i * dur / 119, "x": 0.5 + 0.35 * ((i * 7) % 11 - 5) / 5} for i in range(120)]
    for pts in (single, track):
        expr = build_reframe_x_expr(pts, dur, 1080)
        vf = f"scale=-2:1920,crop=1080:1920:{expr}:0"
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=640x360:d=0.2:r=5",
             "-vf", vf, "-f", "null", "-"],
            capture_output=True, text=True, timeout=60,
        )
        assert proc.returncode == 0, proc.stderr[-400:]


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg не установлен")
def test_ffmpeg_values_match_anchors_within_a_pixel():
    """Реальный ffmpeg считает то же дерево на 120 опорах (aevalsrc с переменной t, по
    сэмплу на 0.1 с): значения совпадают с опорами в пределах 1 px на ширине 1920."""
    anchors = _wavy_anchors(120)
    expr = _piecewise_expr(anchors, [None] * 119)
    dur = anchors[-1][0] + 0.1
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "error", "-f", "lavfi", "-i", f"aevalsrc={expr}:s=10:d={dur:.1f}",
         "-f", "f64le", "-"],
        capture_output=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr[-400:]
    got = struct.unpack(f"<{len(proc.stdout) // 8}d", proc.stdout)
    assert len(got) >= 2 * len(anchors) - 1
    for i, (_t, x) in enumerate(anchors):
        assert abs(got[2 * i] - x) * 1920 < 1.0, (i, got[2 * i], x)
