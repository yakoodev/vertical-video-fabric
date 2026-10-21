"""Смена кадра на склейке плана — ступенька в точное время, а не проезд."""

import re

from app.video_crop import build_reframe_x_expr

BS = chr(92)  # обратная косая: ffmpeg экранирует ею запятые


def _x_at(expr: str, t: float) -> float:
    plain = expr.replace(BS + ",", ",")
    body = re.match(r"clip\(\((.*)\)\*iw-[\d.]+,0,iw-\d+\)$", plain).group(1)
    py = re.sub(r"if\(lt\(t,([\d.]+)\),", r"IF(t<\1,", body)
    return eval(py, {"IF": lambda c, a, b: a if c else b, "t": t})  # noqa: S307 - своё выражение


def test_hard_cut_is_a_step_at_the_exact_time_on_a_long_piece():
    # 25 с — опор больше лимита: раньше стык терялся при прореживании и кадр «проезжал».
    expr = build_reframe_x_expr([{"t": 0, "x": 0.2}, {"t": 5.07, "x": 0.8, "cut": True}], 25.0, 1080)
    assert abs(_x_at(expr, 5.03) - 0.2) < 0.02  # до склейки — старый кадр
    assert abs(_x_at(expr, 5.08) - 0.8) < 0.02  # сразу после — новый, без проезда
    assert abs(_x_at(expr, 20.0) - 0.8) < 0.02
