"""Content-crop detection (strip letter/pillarbox bars) and dynamic reframe math.

``detect_content_crop`` probes a few seconds with ffmpeg ``cropdetect`` and returns
a normalized rect. ``build_reframe_x_expr`` turns a sparse point-of-interest track
into a smooth, velocity-limited ffmpeg crop-x expression (see Stage B).
"""

from __future__ import annotations

import math
import re
import subprocess
from collections import Counter
from pathlib import Path

_CROP_RE = re.compile(r"crop=(\d+):(\d+):(\d+):(\d+)")
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

# «Вписать шире окна» для куска: двое по краям широкого плана в окно 9:16 (≈0.32
# ширины 16:9) не влезают, а камера только приближает (zoom ≥ 1). Кусок с ``fit``
# показывает долю ``w`` ширины исходника (1 — весь кадр), сверху и снизу — фон.
FIT_MIN_W = 0.35
FIT_BGS = ("blur", "black")


def normalize_fit(value: object) -> dict | None:
    """``fit`` куска → ``{"w": 0.35..1, "bg": "blur" | "black" | "#rrggbb"}`` или None.

    Число — сокращение для ``{"w": число, "bg": "blur"}``; пусто/0/False — обычный кадр."""
    if value in (None, "", False, 0):
        return None
    if isinstance(value, str):
        try:
            import json

            value = json.loads(value)
        except ValueError:
            return None
        if not value:
            return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = {"w": value}
    if not isinstance(value, dict):
        return None
    try:
        w = float(value.get("w", 1.0))
    except (TypeError, ValueError):
        return None
    if w != w:
        return None
    bg = str(value.get("bg") or "blur").strip()
    if bg not in FIT_BGS and not _HEX_RE.match(bg):
        bg = "blur"
    return {"w": round(min(1.0, max(FIT_MIN_W, w)), 3), "bg": bg.lower()}


def fit_problem(value: object) -> str | None:
    """Почему ``fit`` из файла клипа не годится (None — годится или пусто): доля вне
    FIT_MIN_W..1 — ошибка, а не молчаливая поправка."""
    if value in (None, False, 0):
        return None
    raw = value.get("w", 1.0) if isinstance(value, dict) else value
    try:
        w = float(raw)
    except (TypeError, ValueError):
        return "нужна доля ширины числом"
    if not FIT_MIN_W <= w <= 1.0:
        return f"w={w:g} — доля ширины исходника {FIT_MIN_W}..1"
    if isinstance(value, dict) and value.get("bg") not in (None, "", *FIT_BGS) and not _HEX_RE.match(str(value["bg"])):
        return "bg — blur, black или #rrggbb"
    return None if normalize_fit(value) else "нужно {w, bg} или доля ширины"


def window_share(source: dict, out_w: int = 1080, out_h: int = 1920) -> float | None:
    """Какую долю ширины исходника (после обрезки полей) видно в окне 9:16 без ``fit``."""
    sw, sh = float(source.get("width") or 0), float(source.get("height") or 0)
    if sw <= 0 or sh <= 0 or out_h <= 0:
        return None
    crop = source.get("content_crop") or None
    eff_w = sw * (float(crop["w"]) if crop else 1.0)
    eff_h = sh * (float(crop["h"]) if crop else 1.0)
    if eff_w <= 0:
        return None
    return round(min(1.0, (out_w / out_h) * eff_h / eff_w), 3)


def detect_content_crop(source: dict) -> dict | None:
    """Return a normalized {x,y,w,h} content rect, or None if the frame is full."""
    local = str(source.get("local_path") or "")
    sw = int(source.get("width") or 0)
    sh = int(source.get("height") or 0)
    duration = float(source.get("duration_sec") or 0)
    if not local or not Path(local).exists():
        raise ValueError("source has no playable file")
    if sw <= 0 or sh <= 0:
        raise ValueError("source dimensions are unknown")

    start = max(0.0, duration * 0.3) if duration else 0.0
    proc = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-ss",
            f"{start:.2f}",
            "-t",
            "4",
            "-i",
            local,
            "-vf",
            "cropdetect=24:2:0",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    matches = _CROP_RE.findall(proc.stderr or "")
    if not matches:
        return None
    w, h, x, y = (int(v) for v in Counter(matches).most_common(1)[0][0])
    if w <= 0 or h <= 0:
        return None
    crop = {"x": x / sw, "y": y / sh, "w": w / sw, "h": h / sh}
    # Near-full frame → there were no real bars worth cropping.
    if crop["w"] >= 0.985 and crop["h"] >= 0.985:
        return None
    return {k: round(v, 5) for k, v in crop.items()}


def _smooth_damp_series(
    xs: list[float],
    dt: float,
    smooth_time: float,
    rubber: float = 2.2,
    cut_idx: set[int] | None = None,
    deadzone: float = 0.012,
) -> list[float]:
    """Critically-damped follow (SmoothDamp) with a rubber-band feel: eases toward
    each target with no hard switches, but the farther the target the shorter the
    effective settle time, so big jumps catch up faster while small moves stay gentle.

    ``cut_idx`` marks hard scene cuts: there the camera teleports (a cut is a cut —
    easing across it would slide the crop over a shot change). ``deadzone`` holds the
    frame still for sub-pixel target wobble so it doesn't breathe.
    """
    if smooth_time <= 0 or len(xs) < 2:
        return xs
    cuts = cut_idx or set()
    cur = xs[0]
    vel = 0.0
    out = [cur]
    for i in range(1, len(xs)):
        target = xs[i]
        if i in cuts:
            # New shot: jump, and kill inherited velocity so we don't drift after.
            cur = target
            vel = 0.0
            out.append(cur)
            continue
        if abs(cur - target) < deadzone:
            vel = 0.0
            out.append(cur)
            continue
        st = smooth_time / (1.0 + rubber * abs(cur - target))  # rubber band: far → snappier
        omega = 2.0 / st
        x = omega * dt
        exp = 1.0 / (1.0 + x + 0.48 * x * x + 0.235 * x * x * x)
        change = cur - target
        temp = (vel + omega * change) * dt
        vel = (vel - omega * temp) * exp
        cur = target + (change + temp) * exp
        out.append(cur)
    return out


def _simplify_indices(xs: list[float], limit: int, tolerance: float = 0.003) -> set[int]:
    """Индексы опор ломаной, которая отклоняется от ``xs`` не больше ``tolerance`` (доли
    ширины), но не больше ``limit`` штук — при нехватке допуск растёт."""
    n = len(xs)

    def rdp(tol: float) -> set[int]:
        keep = {0, n - 1}
        stack = [(0, n - 1)]
        while stack:
            a, b = stack.pop()
            if b - a < 2:
                continue
            worst, worst_i = -1.0, -1
            for i in range(a + 1, b):
                line = xs[a] + (xs[b] - xs[a]) * (i - a) / (b - a)
                err = abs(xs[i] - line)
                if err > worst:
                    worst, worst_i = err, i
            if worst > tol:
                keep.add(worst_i)
                stack.append((a, worst_i))
                stack.append((worst_i, b))
        return keep

    tol = tolerance
    keep = rdp(tol)
    while len(keep) > limit and tol < 0.2:
        tol *= 1.6
        keep = rdp(tol)
    return keep


def build_reframe_x_expr(
    focus_points: list[dict],
    duration: float,
    output_width: int,
    *,
    max_anchors: int = 120,
    smooth_time: float = 1.1,
    rubber: float = 2.2,
    deadzone: float = 0.012,
) -> str | None:
    """Turn sparse focus points into a smooth ffmpeg crop-x expression.

    ``focus_points`` are ``{t, x}`` with ``t`` seconds from the segment start and
    ``x`` the normalized horizontal centre (0..1) in the scaled frame. The path is
    resampled and run through a critically-damped smoother (SmoothDamp) so the camera
    eases in and out and never snaps to a new coordinate, then emitted as a piecewise
    expression clamped to the valid crop range. Returns None if there's nothing to
    pan to. Commas are escaped for embedding inside a filtergraph.
    """
    if duration <= 0:
        return None
    pts: list[tuple[float, float, bool]] = []
    for p in focus_points:
        try:
            t = float(p["t"])
            x = float(p["x"])
        except (KeyError, TypeError, ValueError):
            continue
        pts.append((min(duration, max(0.0, t)), min(1.0, max(0.0, x)), bool(p.get("cut"))))
    if not pts:
        return None
    pts.sort(key=lambda a: a[0])

    # No hard centre-gate: real focus comes from the detector / manual points (the
    # LLM now emits a single point), so we trust the track and let the box low-pass +
    # hysteresis hold below absorb jitter while still following the subject.
    dt = 0.2
    n = max(2, int(round(duration / dt)) + 1)

    def interp(t: float) -> float:
        if t <= pts[0][0]:
            return pts[0][1]
        if t >= pts[-1][0]:
            return pts[-1][1]
        for i in range(len(pts) - 1):
            t0, x0, _ = pts[i]
            t1, x1, cut1 = pts[i + 1]
            if t0 <= t <= t1:
                if cut1:
                    # Hard cut: hold the old shot's framing, then step to the new one.
                    # Ramping across a cut would slide the crop over a shot change.
                    return x0 if t < t1 else x1
                return x0 if t1 == t0 else x0 + (x1 - x0) * (t - t0) / (t1 - t0)
        return pts[-1][1]

    xs = [interp(i * dt) for i in range(n)]
    # Dense indices where a cut lands — the smoother teleports there instead of easing.
    cut_idx = {min(n - 1, max(0, math.ceil(t / dt - 1e-9))) for (t, _x, cut) in pts if cut}
    xs = _smooth_damp_series(xs, dt, smooth_time, rubber=rubber, cut_idx=cut_idx, deadzone=deadzone)

    if n > max_anchors:
        # Опоры там, где кривая меняется (Дуглас–Пекер), а не через равные 1.3 с: при
        # равномерном прореживании быстрый переход на говорящего растягивался на весь
        # промежуток между опорами (владелец: «фокус надо делать быстрее но плавно»).
        idxs = _simplify_indices(xs, max_anchors)
    else:
        idxs = set(range(n))
    # Склейка кадра должна остаться ступенькой: прореживание до max_anchors
    # выкидывало точки у стыка, и между соседними опорами шла интерполяция —
    # кадр «проезжал» через смену плана за 0.3–0.8 с. Держим обе точки стыка.
    cut_time = {min(n - 1, max(0, math.ceil(t / dt - 1e-9))): t for (t, _x, cut) in pts if cut}
    for c in cut_time:
        idxs.update({max(0, c - 1), c})
    order = sorted(idxs)
    anchors = [(round(i * dt, 3), round(xs[i], 5)) for i in order]

    # Build the normalized X(t) as a nested piecewise-linear expression.
    expr = f"{anchors[-1][1]:.5f}"
    for i in range(len(anchors) - 2, -1, -1):
        t0, x0 = anchors[i]
        t1, x1 = anchors[i + 1]
        if order[i + 1] in cut_time:
            # Стык: старый кадр до точного времени склейки, дальше — новый.
            at = max(t0, cut_time[order[i + 1]])
            expr = f"if(lt(t\\,{at:.3f})\\,{x0:.5f}\\,{expr})"
            continue
        seg = max(1e-3, t1 - t0)
        lerp = f"({x0:.5f}+({x1 - x0:.5f})*(t-{t0:.3f})/{seg:.3f})"
        expr = f"if(lt(t\\,{t1:.3f})\\,{lerp}\\,{expr})"

    half_w = output_width / 2.0
    return f"clip(({expr})*iw-{half_w:.1f}\\,0\\,iw-{output_width})"
