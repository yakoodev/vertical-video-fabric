"""2D-аниматор слоёв для нарисованных нейросетью кадров: не слайд-шоу, а «живая» картинка.

Что умеет (numpy + OpenCV, всё в премультиплицированной альфе, float32 0..1):

* ``key_green`` — вырезать персонажа с зелёнки (#00FF00) с подавлением зелёного ореола;
* ``FaceRig`` — варианты лица, нарисованные правкой той же картинки («та же картинка, только глаза закрыты»):
  берём из варианта лишь растушёванную область (эллипс глаз/лица), так что мелкие расхождения линий
  вне неё не видны; веса вариантов меняются по кадрам — моргание, зевок, улыбка;
* ``Body`` — деформация слоя персонажа по кадрам: дыхание (вертикальный масштаб от низа торса),
  поворот и сдвиг головы вокруг шеи с мягким весом (плечи не трогаются), всё одним ``cv2.remap``
  вместе с камерой;
* ``Camera`` — наезд/сдвиг камеры с параллаксом слоёв (фон медленнее, передний план быстрее);
* ``Dust`` — пылинки и боке в свете монитора; ``twinkle_layer`` — мерцание гирлянды на фоне;
* мелочи: ``ease``/``smooth``/``env`` (огибающие), ``over`` (наложение), ``blur``.

Пример — tools/vtubika_ai2d.py (история «Без лица», тест AI-картинок).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np

# ---------------------------------------------------------------- огибающие


def clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def smooth(x: float) -> float:
    """Smoothstep 0..1."""
    x = clamp01(x)
    return x * x * (3 - 2 * x)


def ease_out_back(x: float, k: float = 1.6) -> float:
    x = clamp01(x) - 1
    return 1 + (k + 1) * x ** 3 + k * x ** 2


def env(t: float, a0: float, a1: float, b0: float, b1: float) -> float:
    """Трапеция: 0 до a0, растёт к 1 на a1, держит до b0, падает к 0 на b1 (плавно)."""
    if t <= a0 or t >= b1:
        return 0.0
    if t < a1:
        return smooth((t - a0) / max(1e-6, a1 - a0))
    if t <= b0:
        return 1.0
    return 1.0 - smooth((t - b0) / max(1e-6, b1 - b0))


def keys(t: float, pts: list[tuple[float, float]]) -> float:
    """Плавная интерполяция по ключам [(t, value), ...] (smoothstep между соседями)."""
    if t <= pts[0][0]:
        return pts[0][1]
    for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
        if t <= t1:
            return v0 + (v1 - v0) * smooth((t - t0) / max(1e-6, t1 - t0))
    return pts[-1][1]


def noise1(t: float, seed: float = 0.0) -> float:
    """Медленный «ручной» шум −1..1 (сумма синусов с несоизмеримыми частотами)."""
    return (math.sin(t * 1.31 + seed) * 0.5 + math.sin(t * 2.17 + seed * 1.7) * 0.3
            + math.sin(t * 0.73 + seed * 2.3) * 0.2)


def blink_weight(t: float, times: list[float], dur: float = 0.16) -> float:
    """Моргание: закрыто 0→1→0 за ``dur`` с быстрым закрытием и чуть более медленным открытием."""
    w = 0.0
    for b in times:
        x = (t - b) / dur
        if 0 <= x <= 1:
            w = max(w, smooth(x / 0.35) if x < 0.35 else 1 - smooth((x - 0.35) / 0.65))
    return w


# ---------------------------------------------------------------- картинки


def load_rgb(path: str) -> np.ndarray:
    img = cv2.imread(path, cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(path)
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0


def to_rgba(rgb: np.ndarray, alpha: np.ndarray | None = None) -> np.ndarray:
    a = np.ones(rgb.shape[:2], np.float32) if alpha is None else alpha.astype(np.float32)
    return np.dstack([rgb * a[..., None], a])


def key_green(rgb: np.ndarray, lo: float = 0.12, hi: float = 0.32, erode: int = 1) -> np.ndarray:
    """Зелёнка → RGBA (премультиплицированная). Зелень = g − max(r, b); despill прижимает g к max(r, b)."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    green = g - np.maximum(r, b)
    alpha = 1.0 - np.clip((green - lo) / (hi - lo), 0, 1)
    if erode:
        k = np.ones((2 * erode + 1, 2 * erode + 1), np.uint8)
        alpha = cv2.erode(alpha, k)
    alpha = cv2.GaussianBlur(alpha, (0, 0), 0.8)
    out = rgb.copy()
    spill = np.clip(green, 0, None)
    out[..., 1] = g - spill * np.clip(1.4 - alpha, 0, 1) - np.where(alpha < 0.98, spill, 0) * 0.5
    out[..., 1] = np.minimum(out[..., 1], np.maximum(np.maximum(r, b), g * (alpha > 0.995)))
    out = np.clip(out, 0, 1)
    return np.dstack([out * alpha[..., None], alpha]).astype(np.float32)


def ellipse_mask(shape: tuple[int, int], center: tuple[float, float], axes: tuple[float, float],
                 feather: float) -> np.ndarray:
    """Растушёванный эллипс (1 внутри, 0 снаружи), ``feather`` — ширина края в пикселях."""
    h, w = shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    d = np.sqrt(((xx - center[0]) / axes[0]) ** 2 + ((yy - center[1]) / axes[1]) ** 2)
    edge = feather / max(axes)
    return np.clip((1.0 + edge * 0.5 - d) / edge, 0, 1).astype(np.float32)


def over(dst: np.ndarray, src: np.ndarray, opacity: float = 1.0) -> np.ndarray:
    """``src`` (RGBA премульт.) поверх ``dst`` (RGB или RGBA премульт.), на месте."""
    a = src[..., 3:4] * opacity
    dst[..., :3] = dst[..., :3] * (1 - a) + src[..., :3] * opacity
    if dst.shape[2] == 4:
        dst[..., 3:4] = dst[..., 3:4] * (1 - a) + a
    return dst


def over_at(dst: np.ndarray, src: np.ndarray, x: int, y: int, opacity: float = 1.0) -> None:
    """Наложить небольшой RGBA-спрайт в точку (x, y) кадра с обрезкой по краям."""
    h, w = src.shape[:2]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(dst.shape[1], x + w), min(dst.shape[0], y + h)
    if x0 >= x1 or y0 >= y1:
        return
    over(dst[y0:y1, x0:x1], src[y0 - y:y1 - y, x0 - x:x1 - x], opacity)


def blur(img: np.ndarray, sigma: float) -> np.ndarray:
    return cv2.GaussianBlur(img, (0, 0), sigma) if sigma > 0.05 else img


def affine(scale: float, cx: float, cy: float, out_w: int, out_h: int, rot_deg: float = 0.0) -> np.ndarray:
    """Матрица «точка мира (cx, cy) в центр кадра, масштаб, поворот» (2×3, мир → кадр)."""
    a = math.radians(rot_deg)
    c, s = math.cos(a) * scale, math.sin(a) * scale
    return np.array([[c, -s, out_w / 2 - c * cx + s * cy], [s, c, out_h / 2 - s * cx - c * cy]], np.float32)


def warp(img: np.ndarray, m: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    return cv2.warpAffine(img, m, size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def warp_rgba(img: np.ndarray, m: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    return cv2.warpAffine(img, m, size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)


# ---------------------------------------------------------------- камера


@dataclass
class Camera:
    """Камера в координатах «мира» (пиксели исходных картинок). ``base`` — масштаб мир→кадр при z=1."""

    out_w: int
    out_h: int
    base: float
    home: tuple[float, float]

    def matrix(self, cx: float, cy: float, z: float, parallax: float = 1.0, rot: float = 0.0,
               layer_scale: float = 1.0) -> np.ndarray:
        """Матрица слоя с параллаксом: p<1 — дальний (меньше наезд и сдвиг), p>1 — ближний.
        ``layer_scale`` — пикселей картинки слоя на пиксель мира (0.5 — слой в половинном разрешении)."""
        zl = 1.0 + (z - 1.0) * parallax
        hx, hy = self.home
        lx, ly = hx + (cx - hx) * parallax, hy + (cy - hy) * parallax
        return affine(self.base * zl / layer_scale, lx * layer_scale, ly * layer_scale, self.out_w, self.out_h,
                      rot)


# ---------------------------------------------------------------- лицо и тело


@dataclass
class FaceRig:
    """База + варианты лица. ``variants[name] = (rgba, mask)``; ``compose`` смешивает по весам в области
    ``box`` (x0, y0, x1, y1), за её пределами всегда база — правки нейросети там не участвуют."""

    base: np.ndarray
    box: tuple[int, int, int, int]
    variants: dict[str, tuple[np.ndarray, np.ndarray]] = field(default_factory=dict)

    def add(self, name: str, rgba: np.ndarray, mask: np.ndarray) -> None:
        x0, y0, x1, y1 = self.box
        self.variants[name] = (rgba[y0:y1, x0:x1].copy(), mask[y0:y1, x0:x1, None].copy())

    def compose(self, weights: dict[str, float], jaw: dict[str, tuple[float, float]] | None = None) -> np.ndarray:
        """``jaw[name] = (pivot_y, sy)`` — растянуть вариант по вертикали ниже ``pivot_y`` (челюсть:
        рот зевка открывается и «дышит» плавно, а не одним рисунком)."""
        active = [(n, w) for n, w in weights.items() if w > 1e-3 and n in self.variants]
        if not active:
            return self.base
        out = self.base.copy()
        x0, y0, x1, y1 = self.box
        reg = out[y0:y1, x0:x1]
        for name, w in active:  # порядок важен: более «сильные» варианты — последними
            rgba, mask = self.variants[name]
            if jaw and name in jaw:
                rgba = stretch_below(rgba, jaw[name][0] - y0, jaw[name][1])
            k = mask * clamp01(w)
            reg[:] = reg * (1 - k) + rgba * k
        return out


def stretch_below(img: np.ndarray, pivot_y: float, sy: float, span: float = 190.0,
                  fade: float = 130.0) -> np.ndarray:
    """Вертикальное растяжение (sy>1) или сжатие полосы ``span`` ниже строки ``pivot_y`` (рот и
    подбородок); ниже сдвиг гаснет на ``fade`` пикселях — шея и капюшон совпадают с базой."""
    if abs(sy - 1) < 1e-3:
        return img
    h, w = img.shape[:2]
    yy = np.arange(h, dtype=np.float32)
    rel = yy - pivot_y
    shift = np.clip(rel, 0, span) * (1 - 1 / sy)  # насколько выше брать пиксель (у sy<1 — ниже)
    tail = 1 - np.clip((rel - span) / fade, 0, 1)
    shift = np.where(rel > span, span * (1 - 1 / sy) * tail, shift)
    src_y = (yy - shift).astype(np.float32)
    my = np.repeat(src_y[:, None], w, 1)
    mx = np.repeat(np.arange(w, dtype=np.float32)[None, :], h, 0)
    return cv2.remap(img, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


@dataclass
class Body:
    """Деформация персонажа: голова вокруг ``pivot`` (шея), вес головы плавно гаснет ниже линии шеи
    (по краям линия выше — плечи остаются на месте), дыхание — вертикальный масштаб от ``floor_y``."""

    shape: tuple[int, int]
    pivot: tuple[float, float]
    neck_y: float
    neck_soft: float
    floor_y: float
    shoulder_curve: float = 0.0015

    def __post_init__(self) -> None:
        h, w = self.shape
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        self.xx, self.yy = xx, yy
        neck = self.neck_y - self.shoulder_curve * (xx - self.pivot[0]) ** 2
        self.w_head = (1.0 - np.clip((yy - (neck - self.neck_soft)) / (2 * self.neck_soft), 0, 1)) ** 1.5

    def maps(self, cam_m: np.ndarray, out_size: tuple[int, int], head_rot: float = 0.0,
             head_dx: float = 0.0, head_dy: float = 0.0, breath: float = 0.0,
             shoulders: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
        """Карты ``cv2.remap``: кадр → исходник с учётом камеры и деформации (обратная — первого порядка).
        ``breath`` — доля вертикального растяжения торса (0.01 = 1 %), ``shoulders`` — ширины."""
        ow, oh = out_size
        inv = cv2.invertAffineTransform(cam_m)
        u, v = np.meshgrid(np.arange(ow, dtype=np.float32), np.arange(oh, dtype=np.float32))
        qx = inv[0, 0] * u + inv[0, 1] * v + inv[0, 2]
        qy = inv[1, 0] * u + inv[1, 1] * v + inv[1, 2]
        # вес головы в точке исходника (берём по ближайшему пикселю карты весов)
        h, w = self.shape
        ix = np.clip(qx, 0, w - 1).astype(np.int32)
        iy = np.clip(qy, 0, h - 1).astype(np.int32)
        wh = self.w_head[iy, ix]
        px, py = self.pivot
        a = math.radians(head_rot)
        ca, sa = math.cos(a), math.sin(a)
        rx, ry = qx - px, qy - py
        dx = wh * ((ca * rx - sa * ry) - rx + head_dx)
        dy = wh * ((sa * rx + ca * ry) - ry + head_dy)
        dy += (qy - self.floor_y) * breath
        dx += (qx - px) * shoulders * (1 - wh)
        return qx - dx, qy - dy


def remap_rgba(src: np.ndarray, mx: np.ndarray, my: np.ndarray) -> np.ndarray:
    return cv2.remap(src, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)


# ---------------------------------------------------------------- частицы и свет


def _sprite(radius: float, soft: float) -> np.ndarray:
    r = int(math.ceil(radius * 2.2)) + 1
    yy, xx = np.mgrid[-r:r + 1, -r:r + 1].astype(np.float32)
    d = np.sqrt(xx * xx + yy * yy)
    disk = np.clip((radius - d) / max(0.7, radius * soft) + 0.5, 0, 1)
    halo = np.exp(-(d / max(1.0, radius * 1.4)) ** 2) * 0.35
    return np.clip(disk + halo, 0, 1).astype(np.float32)


@dataclass
class Dust:
    """Пылинки в свете монитора (мелкие, медленно плывут вверх с покачиванием) и крупное мягкое боке."""

    w: int
    h: int
    n: int = 46
    seed: int = 7
    color: tuple[float, float, float] = (0.70, 0.90, 1.0)
    bokeh: int = 7

    def __post_init__(self) -> None:
        rng = np.random.default_rng(self.seed)
        self.p = rng.random((self.n, 2)) * [self.w, self.h]
        self.r = rng.uniform(1.4, 4.2, self.n)
        self.sp = rng.uniform(8, 26, self.n)
        self.ph = rng.random(self.n) * 6.28
        self.sprites = {}
        self.bp = rng.random((self.bokeh, 2)) * [self.w, self.h]
        self.br = rng.uniform(40, 95, self.bokeh)
        self.bph = rng.random(self.bokeh) * 6.28
        self.bcol = [(1.0, 0.62, 0.30), (0.45, 0.85, 1.0), (0.85, 0.55, 1.0)]

    def _spr(self, r: float, soft: float) -> np.ndarray:
        key = (round(r * 2) / 2, soft)
        if key not in self.sprites:
            self.sprites[key] = _sprite(key[0], soft)
        return self.sprites[key]

    def draw(self, frame: np.ndarray, t: float, shift: tuple[float, float] = (0, 0), amount: float = 1.0,
             bokeh_amount: float = 1.0) -> None:
        for i in range(self.n):
            x = (self.p[i, 0] + math.sin(t * 0.6 + self.ph[i]) * 18 + shift[0]) % self.w
            y = (self.p[i, 1] - t * self.sp[i] + shift[1]) % self.h
            tw = 0.55 + 0.45 * math.sin(t * 2.3 + self.ph[i] * 3)
            spr = self._spr(self.r[i], 0.6)
            _add(frame, spr, x, y, np.array(self.color, np.float32) * 0.55 * tw * amount)
        for i in range(self.bokeh):
            x = (self.bp[i, 0] + math.sin(t * 0.25 + self.bph[i]) * 30 + shift[0] * 1.6) % self.w
            y = (self.bp[i, 1] + math.cos(t * 0.21 + self.bph[i]) * 22 + shift[1] * 1.6) % self.h
            col = np.array(self.bcol[i % 3], np.float32)
            spr = self._spr(self.br[i], 0.08)
            _add(frame, spr * 0.16, x, y, col * bokeh_amount * (0.75 + 0.25 * math.sin(t * 1.1 + i)))


def _add(frame: np.ndarray, spr: np.ndarray, x: float, y: float, col: np.ndarray) -> None:
    h, w = spr.shape
    x0, y0 = int(x - w / 2), int(y - h / 2)
    fx0, fy0, fx1, fy1 = max(0, x0), max(0, y0), min(frame.shape[1], x0 + w), min(frame.shape[0], y0 + h)
    if fx0 >= fx1 or fy0 >= fy1:
        return
    s = spr[fy0 - y0:fy1 - y0, fx0 - x0:fx1 - x0, None]
    reg = frame[fy0:fy1, fx0:fx1, :3]
    reg += s * col  # аддитивно (свет)


def bulb_labels(rgb: np.ndarray, reach: int = 41) -> tuple[np.ndarray, int, np.ndarray]:
    """Лампочки гирлянды: тёплые яркие пятна → (метки с запасом на ореол, число, маска ядер 0..1)."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    m = ((r > 0.82) & (g > 0.45) & (b < 0.62) & (r - b > 0.3)).astype(np.uint8)
    m = cv2.dilate(m, np.ones((5, 5), np.uint8))
    n, lab = cv2.connectedComponents(m)
    lab_wide = cv2.dilate(lab.astype(np.float32), np.ones((reach, reach), np.uint8)).astype(np.int32)
    return lab_wide, n, m.astype(np.float32)


def twinkle_layer(lab: np.ndarray, n: int, t: float, glow: np.ndarray, seed: int = 3) -> np.ndarray:
    """Аддитивный слой мерцания: каждая лампочка со своей фазой; ``glow`` — заранее размытая маска ламп."""
    rng = np.random.default_rng(seed)
    ph = rng.random(n) * 6.28
    fr = rng.uniform(1.2, 3.4, n)
    gain = (0.5 + 0.5 * np.sin(t * fr + ph)).astype(np.float32) ** 3
    gain[0] = 0
    g = gain[lab]
    return glow * g[..., None]
