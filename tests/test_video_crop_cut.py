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


def test_speaker_switch_stays_fast_after_thinning():
    """52 с куска, на 20 с переход на другого говорящего: кривая доходит до цели быстро,
    и прореживание опор не растягивает переход (раньше — опора раз в 1.3 с)."""
    from app.render import REFRAME_MAX_SMOOTH_SEC
    from app.video_crop import _simplify_indices, _smooth_damp_series

    dt = 0.2
    xs = [0.3 if i * dt < 20 else 0.7 for i in range(int(52 / dt) + 1)]
    smooth = _smooth_damp_series(xs, dt, REFRAME_MAX_SMOOTH_SEC)
    reached = next(i * dt for i, x in enumerate(smooth) if i * dt >= 20 and x >= 0.68)
    assert reached - 20 <= 1.0  # быстрее секунды, но не скачком
    assert smooth[int(20.2 / dt)] < 0.68  # плавно: не прыжок за один шаг
    keep = sorted(_simplify_indices(smooth, 120))
    near = [i * dt for i in keep if 19.5 <= i * dt <= 21.5]
    assert len(near) >= 4  # переход описан несколькими опорами, а не одной парой через 1.3 с
