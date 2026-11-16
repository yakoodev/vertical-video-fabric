"""Переходы фраг-мувика кадрами: слеш-вайп, диагональные панели, сноп искр (numpy + OpenCV).

Владелец 15.11 про вспышки и зум-панчи: «переходы всё равно хуйня», нужны «как в рекламе / клипах» — и одобрил
мувик BURN v4 с переходами по рефу ``D:\\vvf-offload\\memes\\movie2\\ref\\ref_transitions.mp4``. Здесь — те же
переходы из ручных скриптов (``movie5/scripts/transitions5.py``), только для окна склейки сервиса: A — последние
``n`` кадров уходящей части, B — первые ``n`` кадров входящей (окно = наплыв склейки, его середина — точка склейки,
она и ставится в долю). Кадр ``j`` окна: в начале — почти A, в конце — ровно B (без скачка на стыке с частями).

* ``slash`` — диагональный срез со светящейся кромкой (белое ядро, цвет кромки, широкий розовый ореол) идёт по
  направлению ``d`` — движению камеры на склейке (``motion_vector``/``pick_direction``); обе картинки уносит по
  ``d`` со смазом.
* ``panels`` — 2–3 косые панели: входящая (и кадр из её же продолжения) въезжают по длинной оси, держатся,
  потом панель B раздвигается на весь кадр; швы светятся.
* ``sparks`` — искры разлетаются от прицела (центр кадра), уходящий кадр гаснет в тлеющий красный, входящий
  вырастает из снопа с рваным светящимся краем. Никакой заливки кадра белым.

Кадры — float32 RGB (h, w, 3) 0..255. Чтение/запись окна — ``app/transitions_fx_render.py``.
"""

from __future__ import annotations

import math
from typing import Sequence

import cv2
import numpy as np

WARM = (255.0, 190.0, 110.0)    # кромка среза по умолчанию — янтарь (как в одобренном мувике)
HALO = (255.0, 70.0, 210.0)     # широкий ореол — розово-маджентовый
EMBER = (60.0, 18.0, 10.0)      # уходящий кадр под искрами тлеет красным, а не белеет
RIM = (255.0, 150.0, 60.0)      # край кадра, вырастающего из снопа
# Направления среза по очереди, когда камера стоит (BURN v4, «swing»).
SWING_DIRS = ((-1.0, 0.42), (1.0, -0.35), (0.5, 1.0), (-0.9, -0.5), (1.0, 0.55), (-0.45, 1.0))
# Ниже — камера «стоит»: px за кадр 60 к/с на шкале 1920 (как flow_cuts.py мувика).
FLOW_MIN = 4.0
SLANT_DEG = 38.0                # срез наклонён к направлению движения
SPARKS = 900

_GRID: dict[tuple[int, int], tuple[np.ndarray, np.ndarray]] = {}
_SIMS: dict[tuple, list] = {}


def hex_rgb(color: str | None, default: tuple[float, float, float] = WARM) -> tuple[float, float, float]:
    text = str(color or "").strip().lstrip("#")
    if len(text) != 6:
        return default
    try:
        return (float(int(text[0:2], 16)), float(int(text[2:4], 16)), float(int(text[4:6], 16)))
    except ValueError:
        return default


# --------------------------------------------------------------------------- #
# Цвет: окно читается и пишется в yuv444p, без матрицы swscale туда-обратно —
# кадры окна не меняют цвет относительно частей (матрица BT.709, ограниченный диапазон).
# --------------------------------------------------------------------------- #


def yuv_to_rgb(yuv: np.ndarray) -> np.ndarray:
    """Плоскости yuv444p (3, h, w) uint8 → float32 RGB (h, w, 3) 0..255 (вне гаммы — как есть, без обрезки)."""
    y = (yuv[0].astype(np.float32) - 16.0) / 219.0
    u = (yuv[1].astype(np.float32) - 128.0) / 224.0
    v = (yuv[2].astype(np.float32) - 128.0) / 224.0
    rgb = np.empty(y.shape + (3,), np.float32)
    rgb[..., 0] = y + 1.5748 * v
    rgb[..., 1] = y - 0.187324 * u - 0.468124 * v
    rgb[..., 2] = y + 1.8556 * u
    rgb *= 255.0
    return rgb


def rgb_to_yuv(rgb: np.ndarray) -> np.ndarray:
    """float32 RGB (h, w, 3) → плоскости yuv444p (3, h, w) uint8; RGB обрезается в 0..255 (белое ядро — белое)."""
    c = np.clip(rgb, 0.0, 255.0) / 255.0
    r, g, b = c[..., 0], c[..., 1], c[..., 2]
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    out = np.empty((3,) + y.shape, np.float32)
    out[0] = 16.0 + 219.0 * y
    out[1] = 128.0 + 224.0 * (b - y) / 1.8556
    out[2] = 128.0 + 224.0 * (r - y) / 1.5748
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)


# --------------------------------------------------------------------------- #
# Общее
# --------------------------------------------------------------------------- #


def ease(p: float) -> float:
    return 0.5 - 0.5 * math.cos(math.pi * min(1.0, max(0.0, p)))


def grid(h: int, w: int) -> tuple[np.ndarray, np.ndarray]:
    """Координаты пикселей от центра кадра (кэш по размеру)."""
    if (h, w) not in _GRID:
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        _GRID[(h, w)] = (xx - w / 2, yy - h / 2)
    return _GRID[(h, w)]


def _scale(h: int, w: int) -> float:
    """Толщины заданы для 1920×1080 / 1080×1920."""
    return w / 1920 if w > h else w / 1080


def _unit(d: Sequence[float]) -> np.ndarray:
    v = np.array([float(d[0]), float(d[1])], np.float32)
    norm = float(np.linalg.norm(v))
    return v / norm if norm > 1e-6 else np.array([1.0, 0.0], np.float32)


def shift(img: np.ndarray, dx: float, dy: float) -> np.ndarray:
    if abs(dx) < 0.5 and abs(dy) < 0.5:
        return img
    m = np.float32([[1, 0, dx], [0, 1, dy]])
    return cv2.warpAffine(img, m, (img.shape[1], img.shape[0]), borderMode=cv2.BORDER_REPLICATE)


def dir_blur(img: np.ndarray, d: Sequence[float], length: float) -> np.ndarray:
    """Смаз вдоль ``d`` на ``length`` px (на половинном размере — быстро и мягко)."""
    size = int(length)
    if size < 3:
        return img
    k = np.zeros((size, size), np.float32)
    c = (size - 1) / 2
    for i in range(size):
        t = i / (size - 1) - 0.5
        k[int(round(c + d[1] * t * (size - 1))), int(round(c + d[0] * t * (size - 1)))] = 1
    h, w = img.shape[:2]
    small = cv2.resize(img, (max(1, w // 2), max(1, h // 2)), interpolation=cv2.INTER_AREA)
    side = max(3, size // 2) | 1
    kk = cv2.resize(k, (side, side), interpolation=cv2.INTER_AREA)
    kk /= max(1e-6, float(kk.sum()))
    small = cv2.filter2D(small, -1, kk)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


def window_progress(j: int, n: int) -> float:
    """Кадр ``j`` из ``n`` → 0..1 без концов: середина окна (точка склейки, доля) — ровно 0.5."""
    return (j + 1) / (n + 1)


# --------------------------------------------------------------------------- #
# Направление по движению камеры
# --------------------------------------------------------------------------- #


def small_gray(y_plane: np.ndarray, longest: int = 480) -> np.ndarray:
    h, w = y_plane.shape[:2]
    k = longest / max(h, w)
    return cv2.resize(y_plane, (max(8, int(w * k)), max(8, int(h * k))), interpolation=cv2.INTER_AREA)


def motion_vector(grays: Sequence[np.ndarray], fps: float, longest: int = 480) -> tuple[float, float]:
    """Движение картинки (медиана оптического потока) — px за кадр 60 к/с на шкале 1920. Пусто — (0, 0)."""
    vs = []
    for a, b in zip(grays, grays[1:]):
        flow = cv2.calcOpticalFlowFarneback(a, b, None, 0.5, 3, 21, 3, 5, 1.1, 0)
        vs.append(np.median(flow.reshape(-1, 2), 0))
    if not vs:
        return (0.0, 0.0)
    v = np.mean(vs, 0) * (1920.0 / longest) * (float(fps or 60.0) / 60.0)
    return (round(float(v[0]), 2), round(float(v[1]), 2))


def pick_direction(out_v: Sequence[float], in_v: Sequence[float], index: int,
                   angle: float | None = None) -> tuple[tuple[float, float], str]:
    """Куда идёт срез: заданный угол; иначе рывок камеры в конце уходящей части или в начале входящей
    (что сильнее, от FLOW_MIN); камера стоит — по очереди ``SWING_DIRS``. Ответ — (вектор, почему)."""
    if angle is not None:
        a = math.radians(float(angle))
        return (round(math.cos(a), 4), round(math.sin(a), 4)), f"угол {float(angle):g}°"
    mo, mi = math.hypot(*out_v), math.hypot(*in_v)
    if mo >= mi and mo >= FLOW_MIN:
        return (float(out_v[0]), float(out_v[1])), "камера в конце куска"
    if mi >= FLOW_MIN:
        return (float(in_v[0]), float(in_v[1])), "камера в начале следующего"
    return SWING_DIRS[index % len(SWING_DIRS)], "камера стоит — по очереди"


# --------------------------------------------------------------------------- #
# Слеш-вайп
# --------------------------------------------------------------------------- #


def slash(a_img: np.ndarray, b_img: np.ndarray, p: float, d: Sequence[float], *, slant: float = SLANT_DEG,
          carry: float = 0.16, color: tuple[float, float, float] = WARM,
          halo: tuple[float, float, float] = HALO) -> np.ndarray:
    """Кромка идёт поперёк наклонённой к ``d`` линии, за ней — B; обе картинки уносит по ``d`` со смазом."""
    h, w = a_img.shape[:2]
    sc = _scale(h, w)
    dv = _unit(d)
    ang = math.radians(slant)
    nv = np.array([dv[0] * math.cos(ang) - dv[1] * math.sin(ang), dv[0] * math.sin(ang) + dv[1] * math.cos(ang)],
                  np.float32)
    xx, yy = grid(h, w)
    s = xx * nv[0] + yy * nv[1]
    smax = abs(float(nv[0])) * w / 2 + abs(float(nv[1])) * h / 2
    q = ease(p)
    edge = -smax - 40 * sc + (2 * smax + 80 * sc) * q
    span = max(w, h)
    o_out = dv * (q ** 1.6) * carry * span
    o_in = -dv * ((1 - q) ** 1.6) * carry * span
    blur = 70 * sc * math.sin(math.pi * min(1.0, max(0.0, p)))
    ai = dir_blur(shift(a_img, float(o_out[0]), float(o_out[1])), dv, blur)
    bi = dir_blur(shift(b_img, float(o_in[0]), float(o_in[1])), dv, blur)
    m = np.clip((edge - s) / (3.0 * sc) + 0.5, 0, 1)[:, :, None]
    out = ai * (1 - m) + bi * m
    if 0.0 < p < 1.0:
        dist = np.abs(s - edge)
        k = math.sin(math.pi * p) ** 0.5
        core = np.exp(-(dist / (2.2 * sc + 1)) ** 2)[:, :, None]
        g1 = np.exp(-(dist / (14 * sc)) ** 2)[:, :, None]
        g2 = np.exp(-(dist / (46 * sc)) ** 2)[:, :, None]
        out = out + (core * np.float32(255 * 1.2) + g1 * np.array(color, np.float32) * 0.9
                     + g2 * np.array(halo, np.float32) * 0.45) * k
    return out


# --------------------------------------------------------------------------- #
# Диагональные панели
# --------------------------------------------------------------------------- #

PANEL_SLIDE, PANEL_TAKE = 0.35, 0.6   # 0–0.35 въезд, до 0.6 держатся, дальше B забирает кадр


def panels(a_img: np.ndarray, b_img: np.ndarray, c_img: np.ndarray | None, p: float, *, count: int = 3,
           color: tuple[float, float, float] = WARM) -> np.ndarray:
    """2–3 косые полосы: A остаётся, B (и C — дальше по той же части) въезжают по длинной оси с разных сторон,
    держатся, потом полоса B раздвигается на весь кадр. Вертикальный кадр — полосы друг над другом."""
    h, w = a_img.shape[:2]
    tall = h >= w
    sc = w / 1080 if tall else w / 1920
    xx, yy = grid(h, w)
    if tall:
        u = (yy + xx * math.tan(math.radians(-14))) / h + 0.5
        size, along = h, w
    else:
        u = (xx + yy * math.tan(math.radians(16))) / w + 0.5
        size, along = w, h
    three = count >= 3 and c_img is not None
    umin, umax = float(u.min()) - 0.02, float(u.max()) + 0.02
    b1, b2 = (1 / 3, 2 / 3) if three else (0.5, umax)
    slide = 1 - ease(p / PANEL_SLIDE) if p < PANEL_SLIDE else 0.0
    take = ease((p - PANEL_TAKE) / (1 - PANEL_TAKE)) if p > PANEL_TAKE else 0.0
    lo = b1 * (1 - take) + umin * take
    hi = b2 * (1 - take) + umax * take
    f = 2.0 * sc
    m_a = np.clip((lo - u) * size / f + 0.5, 0, 1)
    m_c = np.clip((u - hi) * size / f + 0.5, 0, 1) if three else np.zeros_like(u)
    travel = along * 1.1 * slide
    axis = xx if tall else yy
    # Въехавшая панель заходит за край кадра на 2 сглаживания — крайний столбец не остаётся полупрозрачным.
    in_b = np.clip((axis - travel + along / 2 + 2 * f) / f + 0.5, 0, 1)   # B въезжает с «+» стороны
    in_c = np.clip((along / 2 + 2 * f - axis - travel) / f + 0.5, 0, 1)   # C — с «−»
    m_b = np.clip(1 - m_a - m_c, 0, 1) * in_b
    m_c = m_c * in_c
    m_a = 1 - m_b - m_c

    def place(img: np.ndarray, centre: float, along_off: float) -> np.ndarray:
        off = (centre - 0.5) * size
        return shift(img, along_off, off) if tall else shift(img, off, along_off)

    centre_b = 0.5 + ((lo + hi) / 2 - 0.5) * (1 - take)
    out = a_img * m_a[:, :, None] + place(b_img, centre_b, travel) * m_b[:, :, None]
    if three:
        out = out + place(c_img, (hi + umax) / 2, -travel) * m_c[:, :, None]
    k = 1.0 if p < 0.85 else max(0.0, (1 - p) / 0.15)
    seams = [(lo, in_b)] + ([(hi, in_c)] if three else [])
    for bound, inside in seams:
        dist = np.abs(u - bound) * size
        glow = (np.exp(-(dist / (2.5 * sc)) ** 2)[:, :, None] * np.float32(255)
                + np.exp(-(dist / (16 * sc)) ** 2)[:, :, None] * np.array(color, np.float32) * 0.6)
        out = out + glow * (inside[:, :, None] * k)
    return out


# --------------------------------------------------------------------------- #
# Сноп искр
# --------------------------------------------------------------------------- #


def _spark_sim(seed: int, n: int, h: int, w: int, fps: float, cx: float, cy: float) -> list:
    """Искры по кадрам окна: (предыдущее положение, текущее, живые, возраст 0..1). Физика задана для 60 к/с."""
    key = (seed, n, h, w, round(fps, 3), round(cx, 3), round(cy, 3))
    if key in _SIMS:
        return _SIMS[key]
    r = np.random.default_rng(seed)
    diag = math.hypot(w, h)
    k = 60.0 / max(1.0, float(fps))
    half = n / 2.0
    t0 = r.uniform(0, max(1.0, 0.6 * half), SPARKS)
    ang = r.uniform(0, 2 * math.pi, SPARKS)
    spd = r.uniform(0.006, 0.055, SPARKS) ** 0.9 * diag * k
    life = r.uniform(0.55, 1.0, SPARKS) * n
    rad0 = r.uniform(0, 0.04, SPARKS) * diag
    pos = np.stack([cx + np.cos(ang) * rad0, cy + np.sin(ang) * rad0], 1).astype(np.float32)
    vel = np.stack([np.cos(ang) * spd, np.sin(ang) * spd * (0.9 if w > h else 1.0)], 1).astype(np.float32)
    damp = np.float32(0.86 ** k)
    grav = np.float32(0.0009 * h * k * k)
    frames = []
    for j in range(n):
        alive = (j >= t0) & (j < t0 + life)
        prev = pos.copy()
        mov = j >= t0
        pos[mov] += vel[mov]
        vel[mov] *= damp
        vel[mov, 1] += grav
        frames.append((prev, pos.copy(), alive, np.clip((j - t0) / life, 0, 1)))
    if len(_SIMS) > 32:
        _SIMS.clear()
    _SIMS[key] = frames
    return frames


def sparks(a_img: np.ndarray, b_img: np.ndarray, j: int, n: int, *, seed: int = 0, fps: float = 60.0,
           centre: tuple[float, float] = (0.5, 0.5)) -> np.ndarray:
    """Кадр ``j`` из ``n``: до середины окна (точки склейки) уходящий кадр гаснет, искры летят; с середины B
    растёт из снопа рваным кругом, к концу окна искры догорают — последний кадр ровно B."""
    h, w = a_img.shape[:2]
    sc = _scale(h, w)
    cx, cy = centre[0] * w, centre[1] * h
    half = n / 2.0
    dk = ease(min(1.0, (j + 1) / half))
    a_dark = a_img * (1 - 0.72 * dk) + np.array(EMBER, np.float32) * dk
    xx, yy = grid(h, w)
    xc = xx + w / 2 - cx
    yc = yy + h / 2 - cy
    rr = np.sqrt(xc ** 2 + yc ** 2)
    th = np.arctan2(yc, xc)
    ph = np.random.default_rng(seed + 1).uniform(0, 2 * math.pi, 3)
    rag = 1 + 0.10 * np.sin(th * 5 + ph[0]) + 0.06 * np.sin(th * 11 + ph[1]) + 0.04 * np.sin(th * 17 + ph[2])
    q = ease((j - (half - 1)) / max(1.0, n - half))
    corner = max(math.hypot(cx, cy), math.hypot(w - cx, cy), math.hypot(cx, h - cy), math.hypot(w - cx, h - cy))
    rad = q * corner * 1.3
    m = np.clip((rad * rag - rr) / (14 * sc) + 0.5, 0, 1)[:, :, None]
    out = a_dark * (1 - m) + b_img * m
    if 0 < q < 1:
        rim = np.exp(-((rr - rad * rag) / (22 * sc)) ** 2)[:, :, None]
        out = out + rim * np.array(RIM, np.float32) * 1.1 * (1 - q)
    env = min(1.0, max(0.0, (n - 1 - j) / max(1.0, 0.4 * n)))
    if env <= 0:
        return out
    prev, cur, alive, age = _spark_sim(seed, n, h, w, fps, cx, cy)[j]
    canvas = np.zeros((h, w, 3), np.uint8)
    for i in np.nonzero(alive)[0]:
        a = float(age[i])
        col = (255, int(255 * max(0.30, 0.95 - a * 0.9)), int(255 * max(0.08, 0.75 - a * 1.4)))  # белое → жёлтое → красное
        thick = max(1, int((2.6 - 2.0 * a) * sc * 1.5))
        p0 = (int(prev[i, 0]), int(prev[i, 1]))
        p1 = (int(cur[i, 0]), int(cur[i, 1]))
        cv2.line(canvas, p0, p1, col, thick, cv2.LINE_AA)
    glow = cv2.GaussianBlur(canvas, (0, 0), 7 * sc + 1)
    # Широкая дымка — на четверти размера (среднее сохраняется), во весь кадр сигма 40 слишком дорогая.
    quarter = cv2.resize(canvas.astype(np.float32), (max(1, w // 4), max(1, h // 4)), interpolation=cv2.INTER_AREA)
    haze = cv2.resize(cv2.GaussianBlur(quarter, (0, 0), (40 * sc + 1) / 4), (w, h), interpolation=cv2.INTER_LINEAR)
    light = canvas.astype(np.float32) * 0.9 + glow.astype(np.float32) * 0.7 + haze * 0.45
    return out + light * env


# --------------------------------------------------------------------------- #
# Один кадр окна
# --------------------------------------------------------------------------- #


def compose(kind: str, a_img: np.ndarray, b_img: np.ndarray, c_img: np.ndarray | None, j: int, n: int, *,
            direction: Sequence[float] = (1.0, 0.0), color: tuple[float, float, float] = WARM,
            halo: tuple[float, float, float] = HALO, count: int = 3, seed: int = 0, fps: float = 60.0) -> np.ndarray:
    """Кадр ``j`` окна перехода ``kind`` (slash / panels / sparks), float32 RGB."""
    if kind == "slash":
        return slash(a_img, b_img, window_progress(j, n), direction, color=color, halo=halo)
    if kind == "panels":
        return panels(a_img, b_img, c_img, window_progress(j, n), count=count, color=color)
    if kind == "sparks":
        return sparks(a_img, b_img, j, n, seed=seed, fps=fps)
    raise ValueError(f"нет перехода кадрами «{kind}»")
