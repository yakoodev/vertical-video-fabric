"""Интро логотипа «как у владельца»: рисованная покадровая анимация на двойках (12 рисунков в секунду).

Язык его исходной анимации (After Effects, «steps animation» и «final»):

1. эскиз — контур букв от руки растёт мазок за мазком (рог, Y, A, K, o, o, d, e, v), каждый рисунок
   чуть другой («кипит»), у кончика пера вспыхивают искры и точки; потом дуга-росчерк над словом
   садится знаком «×»;
2. рывок — 6–8 кадров белых диагональных царапин и смаза, рваные полосы сдвигов, и из них — залитый
   кистевой логотип;
3. жив — логотип не замирает: каждый рисунок слегка перерисован;
4. уход — царапины стирают логотип со смазом.

Мазки — app/logo_vectorize.py (тот же порядок и расписание, что у слоя logo_reveal движка).
Звук — записи пака freesound (CC0), громкость — правило сервиса (на 10 дБ тише громких мест ролика).

    python tools/logo_boil.py --logo D:/…/yakoodev_master.png --over movie.mp4 --variant A --out D:/…/intro2_A.mp4
    python tools/logo_boil.py --logo … --alpha-out D:/…/intro_alpha.mov   # только логотип, ProRes 4444
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.logo_vectorize import cached_vectorize, load_rgba  # noqa: E402

BOIL_FPS = 12.0
SS = 2  # суперсэмплинг: рисуем вдвое крупнее, отдаём уменьшенным


@dataclass(frozen=True)
class Timing:
    sketch: float  # эскиз мазками, с
    arc: float  # дуга-росчерк и знак «×»
    burst: float  # царапины и заливка
    hold: float  # живой логотип
    exit: float  # уход царапинами
    start: float = 0.15  # когда начинается в куске видео
    boil: float = 1.0  # сила «кипения» (1 — как в исходнике)
    shake: float = 0.0  # тряска на рывке, px кадра
    scratches: int = 6  # царапин на рисунок рывка
    flash: float = 0.8
    loop: int = 6  # живой логотип — петля из стольких рисунков (6 = 0.5 с), длина hold любая

    @property
    def total(self) -> float:
        return self.sketch + self.arc + self.burst + self.hold + self.exit


VARIANTS = {
    # A — бережно к исходнику: спокойный эскиз, дуга, рывок 0.3 с, полсекунды живого логотипа.
    "A": Timing(sketch=1.15, arc=0.32, burst=0.3, hold=0.6, exit=0.28),
    # B — быстрее и жёстче: эскиз за 0.7 с, короткая дуга, рывок с тряской и вспышкой, уход резче.
    "B": Timing(sketch=0.7, arc=0.22, burst=0.26, hold=0.55, exit=0.2, boil=1.35, shake=9, scratches=9, flash=1.0),
    # L — длинная версия A (≈4 с, одобрена по анимации 16.11): эскиз 2 с, логотип живёт 1.2 с.
    "L": Timing(sketch=2.0, arc=0.4, burst=0.3, hold=1.2, exit=0.3),
}

# Звук по меткам: (запись пака freesound, где, длина или 0 — целиком). Владелец 16.11: без треска и глитч-текстур —
# мягкий взмах кисти на эскиз, хлыст на дугу «×», один глубокий удар на рывок, вжух на уход.
SOUND = {"sketch": ("whoosh-3", 0.0), "arc": ("whip", 0.0), "hit": ("impact-low", 0.0), "exit": ("whoosh-1", 0.0)}


# ---------------------------------------------------------------- шум и кипение


def _rng(*keys: int) -> np.random.Generator:
    return np.random.default_rng([abs(int(k)) for k in keys])


def smooth_noise(shape: tuple[int, int], cell: float, seed: int) -> np.ndarray:
    """Гладкий шум −1..1: случайная сетка с шагом ``cell`` px, растянутая кубически."""
    h, w = shape
    gh, gw = max(2, int(h / cell) + 3), max(2, int(w / cell) + 3)
    grid = _rng(seed, 11).uniform(-1, 1, (gh, gw)).astype(np.float32)
    return cv2.resize(grid, (w, h), interpolation=cv2.INTER_CUBIC)


def boil(alpha: np.ndarray, seed: int, amp: float, edge: float = 0.22) -> np.ndarray:
    """Перерисовать: сдвиг гладким полем (рука ведёт линию чуть иначе) + новая рваность края."""
    h, w = alpha.shape
    dx = smooth_noise((h, w), 38 * SS, seed) * amp
    dy = smooth_noise((h, w), 38 * SS, seed + 1) * amp
    xs, ys = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    moved = cv2.remap(alpha, xs + dx, ys + dy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    if edge <= 0:
        return moved
    fine = smooth_noise((h, w), 3.2 * SS, seed + 2) * edge
    soft = cv2.GaussianBlur(moved, (0, 0), 0.9 * SS)
    return np.clip((soft + fine - 0.5) * 3.2 + 0.5, 0, 1)


def contour(alpha: np.ndarray, seed: int, amp: float) -> np.ndarray:
    """Эскиз: контур от руки — две неточные обводки (вторая тоньше и бледнее) с разрывами пера."""
    out = np.zeros_like(alpha)
    for k, (gain, width) in enumerate(((1.0, 3), (0.6, 2))):
        a = boil(alpha, seed * 7 + k * 101, amp * (1.0 + 0.6 * k), edge=0.3) > 0.5
        a8 = a.astype(np.uint8)
        kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * width * SS - 1, 2 * width * SS - 1))
        ring = (cv2.dilate(a8, kern) - cv2.erode(a8, kern)).astype(np.float32)
        gaps = smooth_noise(alpha.shape, 9 * SS, seed * 13 + k) > -0.55  # перо местами отрывается
        out = np.maximum(out, ring * gaps * gain)
    out = cv2.GaussianBlur(out, (0, 0), 0.6 * SS)
    # Лёгкая «тушёвка» внутри — эскиз не пустой.
    inside = boil(alpha, seed * 3 + 5, amp, edge=0.0) * 0.1
    return np.clip(np.maximum(out, inside), 0, 1)


# ---------------------------------------------------------------- расписание мазков


def schedule(strokes: list[dict], draw: float) -> list[tuple[float, float]]:
    """Как logoSchedule в frontend/src/motion/layers/logoreveal.ts: время ∝ длина^0.75, паузы между буквами."""
    lens = []
    for s in strokes:
        p = np.asarray(s["p"], float).reshape(-1, 3)
        lens.append(float(np.hypot(*np.diff(p[:, :2], axis=0).T).sum()) if len(p) > 1 else 0.0)
    wts = [max(x, 0.015) ** 0.75 for x in lens]
    gap = (sum(wts) / max(1, len(wts))) * 0.35
    gaps = sum(gap for i in range(1, len(strokes)) if strokes[i].get("g", 0) != strokes[i - 1].get("g", 0))
    unit = draw / max(1e-6, sum(wts) + gaps)
    out, t = [], 0.0
    for i, w in enumerate(wts):
        if i and strokes[i].get("g", 0) != strokes[i - 1].get("g", 0):
            t += gap * unit
        out.append((t, t + w * unit))
        t += w * unit
    return out


def brush_ease(x: float) -> float:
    p = min(1.0, max(0.0, x))
    return 0.55 * p + 0.45 * (0.5 - 0.5 * math.cos(math.pi * p))


def reveal_mask(shape, strokes, times, t, scale, offset, seed, spread=1.35, pad=0.004, heads=None):
    """Открытая часть мазков к моменту t (как drawStrokeMask движка) + рваный «ворс» на кончике."""
    h, w = shape
    m = np.zeros((h, w), np.uint8)
    ox, oy = offset
    for si, (s, (t0, t1)) in enumerate(zip(strokes, times)):
        if t <= t0:
            continue
        frac = brush_ease((t - t0) / max(1e-6, t1 - t0))
        p = np.asarray(s["p"], float).reshape(-1, 3)
        xy = p[:, :2] * scale + (ox, oy)
        rr = (p[:, 2] * spread + pad) * scale
        seg = np.hypot(*np.diff(xy, axis=0).T) if len(xy) > 1 else np.zeros(0)
        total = seg.sum()
        left = total * frac
        cv2.circle(m, tuple(np.round(xy[0]).astype(int)), max(1, int(rr[0])), 255, -1)
        hx, hy, hr, ang = xy[0][0], xy[0][1], rr[0], 0.0
        for i in range(1, len(xy)):
            k = min(1.0, left / seg[i - 1]) if seg[i - 1] > 0 else 1.0
            e = xy[i - 1] + (xy[i] - xy[i - 1]) * k
            r = rr[i - 1] + (rr[i] - rr[i - 1]) * k
            cv2.line(m, tuple(np.round(xy[i - 1]).astype(int)), tuple(np.round(e).astype(int)), 255, max(1, int(2 * r)))
            hx, hy, hr = e[0], e[1], r
            ang = math.atan2(xy[i][1] - xy[i - 1][1], xy[i][0] - xy[i - 1][0])
            left -= seg[i - 1]
            if left <= 0:
                break
        if frac < 1 and heads is not None:
            heads.append((hx, hy, hr, ang))
            g = _rng(seed, si, 5)
            for b in range(8):
                across = ((b + 0.5) / 8 - 0.5) * 2 * hr * 0.9
                reach = g.uniform(-0.3, 1.0) * hr * 0.8
                cx = hx - math.sin(ang) * across + math.cos(ang) * reach
                cy = hy + math.cos(ang) * across + math.sin(ang) * reach
                cv2.circle(m, (int(cx), int(cy)), max(1, int(hr * g.uniform(0.2, 0.45))), 255, -1)
    return m.astype(np.float32) / 255


# ---------------------------------------------------------------- царапины


def scratch_layer(shape, seed, count, box, angle_deg=-58.0, width=(5, 22), strength=1.0, texture=None):
    """Белые диагональные царапины сухой кистью: сужающиеся полосы поперёк логотипа."""
    h, w = shape
    out = np.zeros((h, w), np.float32)
    x0, y0, x1, y1 = box
    g = _rng(seed, 77)
    for i in range(count):
        ang = math.radians(angle_deg + g.uniform(-9, 9))
        cx, cy = g.uniform(x0, x1), g.uniform(y0, y1)
        length = g.uniform(0.25, 0.75) * (x1 - x0)
        wd = g.uniform(*width) * SS
        ux, uy = math.cos(ang), math.sin(ang)
        nx, ny = -uy, ux
        n = 14
        pts_l, pts_r = [], []
        for k in range(n + 1):
            u = k / n
            taper = math.sin(math.pi * min(1.0, u * 1.15)) ** 0.7  # острый хвост, тупая голова
            px = cx + ux * (u - 0.5) * length
            py = cy + uy * (u - 0.5) * length
            ww = wd * taper * (0.75 + 0.5 * g.random())
            pts_l.append((px + nx * ww / 2, py + ny * ww / 2))
            pts_r.append((px - nx * ww / 2, py - ny * ww / 2))
        poly = np.array(pts_l + pts_r[::-1], np.int32)
        layer = np.zeros((h, w), np.uint8)
        cv2.fillPoly(layer, [poly], 255)
        out = np.maximum(out, layer.astype(np.float32) / 255 * g.uniform(0.6, 1.0))
    if texture is not None:
        out *= texture
    return np.clip(out * strength, 0, 1)


def streak_texture(shape, seed, angle_deg=-58.0):
    """Волокна сухой кисти вдоль царапин: шум, размазанный линией под углом."""
    h, w = shape
    noise = _rng(seed, 9).random((h, w)).astype(np.float32)
    size = 31
    kern = np.zeros((size, size), np.float32)
    ang = math.radians(angle_deg)
    for k in range(size):
        u = k - size // 2
        x = int(round(size // 2 + math.cos(ang) * u))
        y = int(round(size // 2 + math.sin(ang) * u))
        kern[y, x] = 1
    kern /= kern.sum()
    fib = cv2.filter2D(noise, -1, kern)
    fib = (fib - fib.mean()) / (fib.std() + 1e-6)
    return np.clip(0.55 + fib * 0.45, 0, 1)


def band_shift(img: np.ndarray, seed: int, amount: float) -> np.ndarray:
    """Рваные полосы: горизонтальные куски сдвинуты в стороны (глитч)."""
    h, w = img.shape
    out = img.copy()
    g = _rng(seed, 31)
    y = 0
    while y < h:
        bh = int(g.uniform(0.02, 0.09) * h) + 1
        if g.random() < 0.55:
            sh = int(g.uniform(-1, 1) * amount)
            out[y:y + bh] = np.roll(img[y:y + bh], sh, axis=1)
        y += bh
    return out


def smear(img: np.ndarray, angle_deg: float, length: float, steps: int = 6) -> np.ndarray:
    """Смаз вдоль диагонали: копии со сдвигом и затуханием (max)."""
    out = img.copy()
    ang = math.radians(angle_deg)
    for k in range(1, steps + 1):
        d = length * k / steps
        m = np.float32([[1, 0, math.cos(ang) * d], [0, 1, math.sin(ang) * d]])
        moved = cv2.warpAffine(img, m, (img.shape[1], img.shape[0]))
        out = np.maximum(out, moved * (1 - k / (steps + 1)) * 0.7)
    return out


# ---------------------------------------------------------------- кадр


class Renderer:
    def __init__(self, logo_path: Path, out_w: int, out_h: int, logo_frac: float, cy: float, timing: Timing, seed: int = 3,
                 meta: dict | None = None, cache_dir: Path | None = None):
        self.t = timing
        self.seed = seed
        self.out_w, self.out_h = out_w, out_h
        rgba = load_rgba(logo_path)
        lw = int(out_w * logo_frac * SS)
        scale = lw / rgba.shape[1]
        lh = int(rgba.shape[0] * scale)
        alpha = cv2.resize(rgba[..., 3], (lw, lh), interpolation=cv2.INTER_AREA).astype(np.float32) / 255
        # Холст — во всю ширину кадра, по высоте с полем под царапины и искры.
        self.W = out_w * SS
        self.H = int(min(out_h, lh / SS * 2.1) * SS) // 2 * 2
        self.ox = (self.W - lw) // 2
        self.oy = (self.H - lh) // 2
        self.alpha = np.zeros((self.H, self.W), np.float32)
        self.alpha[self.oy:self.oy + lh, self.ox:self.ox + lw] = alpha
        self.box = (self.ox, self.oy, self.ox + lw, self.oy + lh)
        self.lw, self.lh, self.scale_px = lw, lh, lw  # координаты мазков — доли ширины → px
        self.pos_y = int(out_h * cy - self.H / SS / 2)
        meta = meta or {}
        mark = meta.get("mark")
        vec = cached_vectorize(logo_path, cache_dir or Path(tempfile.gettempdir()) / "logo-vectors",
                               first=[[0.0, 0.0, 0.33, 0.165]])
        strokes = vec["strokes"]
        if mark:
            def in_mark(s):
                p = np.asarray(s["p"], float).reshape(-1, 3)
                ins = (p[:, 0] >= mark[0] - 0.01) & (p[:, 0] <= mark[2] + 0.01) & (p[:, 1] >= mark[1] - 0.01) & (p[:, 1] <= mark[3] + 0.01)
                return ins.mean() > 0.6
            self.mark_strokes = [s for s in strokes if in_mark(s)]
            self.strokes = [s for s in strokes if not in_mark(s)]
            self.mark_box = mark
        else:
            self.mark_strokes, self.strokes, self.mark_box = [], strokes, None
        self.times = schedule(self.strokes, timing.sketch)
        self.mark_times = [(timing.sketch + timing.arc * 0.55 + k * 0.08, timing.sketch + timing.arc * 0.95 + k * 0.05)
                           for k in range(len(self.mark_strokes))]
        self.texture = streak_texture((self.H, self.W), seed)
        self.cache: dict[int, tuple[np.ndarray, np.ndarray]] = {}

    def drawing(self, k: int) -> tuple[np.ndarray, np.ndarray]:
        """Рисунок k (12 в секунду): альфа белого и альфа тёмной туши (эскиз) 0..1."""
        tm = self.t
        t = k / BOIL_FPS
        t_hold0 = tm.sketch + tm.arc + tm.burst
        if t_hold0 <= t < t_hold0 + tm.hold:
            # Середина — бесшовная петля: те же loop рисунков по кругу, hold растягивается как угодно.
            kk = int(round((t - t_hold0) * BOIL_FPS)) % max(1, tm.loop)
            key = -1 - kk
            if key not in self.cache:
                a = boil(self.alpha, self.seed * 1000 + 50000 + kk, 1.6 * SS * tm.boil * 0.8)
                self.cache[key] = (a, np.zeros_like(a))
            return self.cache[key]
        if k in self.cache:
            return self.cache[k]
        seed = self.seed * 1000 + k
        amp = 1.6 * SS * tm.boil
        t_arc = tm.sketch
        t_burst = tm.sketch + tm.arc
        t_hold = t_burst + tm.burst
        t_exit = t_hold + tm.hold
        out = np.zeros((self.H, self.W), np.float32)
        dark = np.zeros((self.H, self.W), np.float32)
        if t < t_burst:
            heads: list = []
            mask = reveal_mask((self.H, self.W), self.strokes, self.times, t, self.scale_px, (self.ox, self.oy), seed, heads=heads)
            if self.mark_strokes:
                mask = np.maximum(mask, reveal_mask((self.H, self.W), self.mark_strokes, self.mark_times, t, self.scale_px,
                                                    (self.ox, self.oy), seed, heads=heads))
            if t >= tm.sketch:
                # Остаток зубцов проявляется; место знака «×» — только когда он сел.
                settle = np.full((self.H, self.W), min(1.0, (t - tm.sketch) / 0.12), np.float32)
                if self.mark_box and t < tm.sketch + tm.arc * 0.95:
                    mx = self.mark_box
                    x0, y0 = int(self.ox + (mx[0] - 0.01) * self.lw), int(self.oy + (mx[1] - 0.01) * self.lw)
                    x1, y1 = int(self.ox + (mx[2] + 0.01) * self.lw), int(self.oy + (mx[3] + 0.01) * self.lw)
                    settle[max(0, y0):y1, max(0, x0):x1] = 0
                mask = np.maximum(mask, settle)
            sk = contour(self.alpha, seed, amp) * mask
            out = np.maximum(out, sk)
            dark = boil(self.alpha, seed + 17, amp, edge=0.25) * mask * 0.62
            self._sparks(out, heads, seed)
            if t_arc <= t < t_burst and self.mark_box:
                self._arc(out, (t - t_arc) / max(1e-6, tm.arc), seed)
        elif t < t_hold:
            e = (t - t_burst) / max(1e-6, tm.burst)
            fill = boil(self.alpha, seed, amp * 1.6)
            g = 1 - e  # сила рывка гаснет к концу
            fill = band_shift(fill, seed, 46 * SS * g)
            fill = smear(fill, -58 + 180, 70 * SS * g)
            sc = scratch_layer((self.H, self.W), seed, tm.scratches, self._wide_box(0.15), texture=self.texture,
                               strength=0.95 * g + 0.15)
            out = np.maximum(fill, sc)
        elif t < t_exit:
            out = boil(self.alpha, seed, amp * 0.8)
        else:
            e = min(1.0, (t - t_exit) / max(1e-6, tm.exit))
            fill = boil(self.alpha, seed, amp)
            fill = smear(fill, -58, 90 * SS * e)
            # Царапины стирают: чем дальше, тем больше и шире.
            erase = scratch_layer((self.H, self.W), seed + 5, int(6 + 18 * e), self._wide_box(0.1), width=(10, 40 + 60 * e))
            erase = np.clip(erase * 1.6 * e + (e > 0.75) * (e - 0.75) * 4, 0, 1)
            sc = scratch_layer((self.H, self.W), seed, max(2, tm.scratches // 2), self._wide_box(0.15), texture=self.texture,
                               strength=0.8 * (1 - e))
            out = np.maximum(fill * (1 - erase) * (1 - e ** 3), sc)
        # Память: держим только последние рисунки (кадр 60 fps попадает в рисунок 5 раз подряд) и петлю.
        for old in [key for key in self.cache if key >= 0 and key < k - 1]:
            del self.cache[old]
        self.cache[k] = (out, dark)
        return out, dark

    def _wide_box(self, grow: float):
        x0, y0, x1, y1 = self.box
        gx, gy = (x1 - x0) * grow, (y1 - y0) * grow * 2
        return (x0 - gx, y0 - gy, x1 + gx, y1 + gy)

    def _sparks(self, out: np.ndarray, heads: list, seed: int) -> None:
        """Искры у кончика пера: точки и короткие штрихи, на каждом рисунке новые."""
        for hi, (hx, hy, hr, ang) in enumerate(heads):
            g = _rng(seed, hi, 3)
            for _ in range(g.integers(2, 6)):
                d = g.uniform(1.2, 4.0) * hr
                a = g.uniform(0, 2 * math.pi)
                x, y = hx + math.cos(a) * d, hy + math.sin(a) * d
                if g.random() < 0.6:
                    cv2.circle(out, (int(x), int(y)), int(g.uniform(1.0, 2.6) * SS), 1.0, -1)
                else:
                    l = g.uniform(4, 10) * SS
                    cv2.line(out, (int(x), int(y)), (int(x + math.cos(a) * l), int(y + math.sin(a) * l)), 1.0, max(1, SS))

    def _arc(self, out: np.ndarray, e: float, seed: int) -> None:
        """Дуга-росчерк над словом: от рога вверх и вниз к знаку «×» — короткий хвост кометы."""
        x0, y0 = self.ox + 0.29 * self.lw, self.oy + 0.03 * self.lw
        mx = self.mark_box
        x2 = self.ox + (mx[0] + mx[2]) / 2 * self.lw
        y2 = self.oy + (mx[1] + mx[3]) / 2 * self.lw
        x1, y1 = (x0 + x2) / 2, min(y0, y2) - 0.09 * self.lw
        head = min(1.0, e / 0.6)
        tail = max(0.0, head - 0.35)
        pts = []
        for k in range(16):
            u = tail + (head - tail) * k / 15
            bx = (1 - u) ** 2 * x0 + 2 * (1 - u) * u * x1 + u * u * x2
            by = (1 - u) ** 2 * y0 + 2 * (1 - u) * u * y1 + u * u * y2
            pts.append((bx, by))
        g = _rng(seed, 41)
        for k in range(1, len(pts)):
            w = max(1, int((1.5 + 5.5 * k / len(pts)) * SS))
            jit = g.normal(0, 0.8 * SS, 2)
            cv2.line(out, (int(pts[k - 1][0] + jit[0]), int(pts[k - 1][1] + jit[1])), (int(pts[k][0]), int(pts[k][1])), 1.0, w)

    def frame(self, t: float) -> tuple[np.ndarray, tuple[int, int]]:
        """RGBA кадра (уменьшенный холст) и сдвиг тряски, px кадра."""
        tm = self.t
        k = int(math.floor(t * BOIL_FPS))
        a, dark = self.drawing(k)
        t_burst = tm.sketch + tm.arc
        # Свечение: мягкое всегда, вспышка — на рывке.
        flash = tm.flash * math.exp(-max(0.0, t - t_burst) * 7) if t >= t_burst else 0.0
        small = cv2.resize(np.maximum(a, dark), (self.W // 4, self.H // 4), interpolation=cv2.INTER_AREA)
        small_w = cv2.resize(a, (self.W // 4, self.H // 4), interpolation=cv2.INTER_AREA)
        glow = cv2.resize(cv2.GaussianBlur(small_w, (0, 0), 3.0), (self.W, self.H), interpolation=cv2.INTER_LINEAR)
        glow2 = cv2.resize(cv2.GaussianBlur(small_w, (0, 0), 9.0), (self.W, self.H), interpolation=cv2.INTER_LINEAR)
        shadow = cv2.resize(cv2.GaussianBlur(small, (0, 0), 4.0), (self.W, self.H), interpolation=cv2.INTER_LINEAR)
        g_alpha = np.clip(glow * (0.35 + 0.9 * flash) + glow2 * (0.25 + 1.2 * flash), 0, 1)
        sh_alpha = np.clip(shadow * 0.75, 0, 1)
        # Склейка: тень (чёрная) под свечением (белое) под буквами (белые) — «над» по альфе.
        col = np.zeros((self.H, self.W), np.float32)
        al = sh_alpha.copy()
        for layer_a, value in ((dark, 0.0), (g_alpha, 1.0), (a, 1.0)):
            col = value * layer_a + col * al * (1 - layer_a)
            al = layer_a + al * (1 - layer_a)
            col = np.where(al > 1e-5, col / np.maximum(al, 1e-5), 0)
        rgba = np.zeros((self.H, self.W, 4), np.float32)
        rgba[..., 0] = rgba[..., 1] = rgba[..., 2] = col
        rgba[..., 3] = al
        out = cv2.resize(rgba, (self.W // SS, self.H // SS), interpolation=cv2.INTER_AREA)
        shake = (0, 0)
        if tm.shake > 0 and t_burst <= t < t_burst + tm.burst + 0.15:
            g = _rng(self.seed, int(t * 60), 8)
            amp = tm.shake * math.exp(-(t - t_burst) * 6)
            shake = (int(g.uniform(-1, 1) * amp), int(g.uniform(-1, 1) * amp))
        return (np.clip(out, 0, 1) * 255).astype(np.uint8), shake


# ---------------------------------------------------------------- вывод


def run(cmd: list[str], **kw) -> None:
    proc = subprocess.run(cmd, capture_output=True, **kw)
    if proc.returncode != 0:
        raise SystemExit(f"упало: {' '.join(map(str, cmd[:6]))}…\n{proc.stderr[-1500:].decode('utf-8', 'replace') if isinstance(proc.stderr, bytes) else proc.stderr}")


def probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height,r_frame_rate:format=duration",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    d = json.loads(out)
    v = next(s for s in d["streams"] if s["codec_type"] == "video")
    n, m = (int(x) for x in v["r_frame_rate"].split("/"))
    return {"w": int(v["width"]), "h": int(v["height"]), "fps": n / m, "dur": float(d["format"]["duration"])}


def write_frames(r: Renderer, fps: float, dur: float, start: float, out_frames, full: bool) -> None:
    """Кадры RGBA во весь кадр (full) или холст логотипа, с start по start+total; вне — прозрачно."""
    n = int(round(dur * fps))
    blank = None
    for i in range(n):
        t = i / fps - start
        if full:
            canvas = np.zeros((r.out_h, r.out_w, 4), np.uint8)
            if 0 <= t < r.t.total:
                img, (sx, sy) = r.frame(t)
                y = r.pos_y + sy
                h = img.shape[0]
                y0, y1 = max(0, y), min(r.out_h, y + h)
                src = img[y0 - y:y1 - y]
                if sx:
                    src = np.roll(src, sx, axis=1)
                canvas[y0:y1] = src
            out_frames.write(canvas.tobytes())
        else:
            if 0 <= t < r.t.total:
                img, _ = r.frame(t)
            else:
                blank = blank if blank is not None else np.zeros((r.H // SS, r.W // SS, 4), np.uint8)
                img = blank
            out_frames.write(img.tobytes())


def sfx(name: str) -> Path:
    for d in (ROOT / "vendor" / "packs" / "freesound", ROOT.parents[2] / "vendor" / "packs" / "freesound"):
        if (d / f"{name}.ogg").exists():
            return d / f"{name}.ogg"
    raise SystemExit(f"нет звука {name}.ogg")


def sound_cues(timing: Timing, with_exit: bool = True) -> list[tuple[str, float, float]]:
    """Звуковые метки от начала куска: взмах на эскиз, хлыст на дугу «×», удар на рывок, вжух на уход."""
    s = timing.start
    tb = s + timing.sketch + timing.arc
    te = tb + timing.burst + timing.hold
    cues = [(SOUND["sketch"][0], s, SOUND["sketch"][1]), (SOUND["arc"][0], s + timing.sketch + timing.arc * 0.4, SOUND["arc"][1]),
            (SOUND["hit"][0], tb - 0.02, SOUND["hit"][1])]
    if with_exit:
        cues.append((SOUND["exit"][0], te, SOUND["exit"][1]))
    return cues


def mix(base: Path, video: Path, out: Path, cues: list[tuple[str, float, float]]) -> None:
    """Звук куска + эффекты по меткам (имя, секунда, длина или 0 — целиком); громкость — правило сервиса."""
    from app.motion_render import audio_levels, effect_gain_db

    lv = audio_levels(base, seconds=None)
    loud = lv[0] if lv and lv[0] > -70 else None
    inputs = ["-i", str(video), "-i", str(base)]
    chains, labels = [], ["[1:a]"]
    for i, (name, at, length) in enumerate(cues):
        path = sfx(name)
        gain = effect_gain_db(str(path), loud, speechless=False)
        inputs += ["-i", str(path)]
        ms = int(round(at * 1000))
        trim = f"atrim=0:{length:.3f},afade=t=out:st={max(0.0, length - 0.08):.3f}:d=0.08," if length > 0 else ""
        chains.append(f"[{i + 2}:a]aresample=48000,{trim}volume={gain:.2f}dB,adelay={ms}|{ms}[s{i}]")
        labels.append(f"[s{i}]")
    chains.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:duration=first[a]")
    run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", ";".join(chains), "-map", "0:v", "-map", "[a]",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", str(out)])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--logo", required=True, type=Path)
    ap.add_argument("--variant", choices=sorted(VARIANTS), default="A")
    ap.add_argument("--over", type=Path, help="видео; кусок берётся с начала (интро) или с конца (--at end)")
    ap.add_argument("--at", choices=("start", "end"), default="start")
    ap.add_argument("--span", type=float, default=None, help="длина куска, с (поверх видео — 3; ролик с альфой — длина анимации)")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--alpha-out", type=Path, help="только логотип с альфой (.mov ProRes 4444 или .webm VP9)")
    ap.add_argument("--size", default="1080x1920", help="кадр ролика с альфой без --over")
    ap.add_argument("--width", type=float, default=0.78, help="ширина логотипа, доля кадра")
    ap.add_argument("--y", type=float, default=0.45, help="центр логотипа по высоте, доля кадра")
    ap.add_argument("--start", type=float, default=None, help="когда начинается, с от начала куска")
    ap.add_argument("--fps", type=float, default=None)
    ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("--hold", type=float, default=None, help="сколько живёт залитый логотип, с (петля; по умолчанию — у варианта)")
    ap.add_argument("--loop", type=int, default=None, help="рисунков в петле живого логотипа (12 в секунду)")
    ap.add_argument("--work", type=Path, default=None, help="папка промежуточных файлов")
    ap.add_argument("--no-exit", action="store_true", help="без ухода: держать до конца куска (аутро)")
    args = ap.parse_args(argv)
    timing = VARIANTS[args.variant]
    if args.start is not None:
        timing = replace(timing, start=args.start)
    if args.hold is not None:
        timing = replace(timing, hold=max(0.0, args.hold))
    if args.loop is not None:
        timing = replace(timing, loop=max(1, args.loop))
    meta_path = args.logo.with_suffix(".json")
    meta = json.loads(meta_path.read_text("utf-8")) if meta_path.exists() else {}
    work_root = args.work or (args.out.parent if args.out else args.alpha_out.parent)
    work_root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="logo-boil-", dir=str(work_root)))
    if args.over:
        info = probe(args.over)
        w, h, fps = info["w"], info["h"], args.fps or info["fps"]
        span = args.span or 3.0
        t0 = 0.0 if args.at == "start" else max(0.0, info["dur"] - span)
    else:
        w, h = (int(v) for v in args.size.split("x"))
        fps, t0, span = args.fps or 30.0, 0.0, args.span or (timing.total + timing.start + 0.1)
    if args.no_exit:
        timing = replace(timing, hold=max(timing.hold, span - timing.start - timing.sketch - timing.arc - timing.burst - 0.02), exit=0.02)
    r = Renderer(args.logo, w, h, args.width, args.y, timing, args.seed, meta, work.parent / "logo-vectors")
    if args.alpha_out:
        alpha_path = args.alpha_out
        codec = ["-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le"] if alpha_path.suffix.lower() == ".mov" else \
            ["-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", "26", "-auto-alt-ref", "0"]
        proc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{w}x{h}",
                                 "-r", f"{fps:g}", "-i", "-", *codec, str(alpha_path)], stdin=subprocess.PIPE)
        write_frames(r, fps, span, timing.start, proc.stdin, True)
        proc.stdin.close()
        if proc.wait() != 0:
            raise SystemExit("ffmpeg (альфа) упал")
    if args.over and args.out:
        base = work / "base.mp4"
        run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t0:.3f}", "-i", str(args.over), "-t", f"{span:.3f}", "-c:v", "libx264",
             "-preset", "veryfast", "-crf", "14", "-c:a", "aac", "-b:a", "256k", str(base)])
        silent = work / "v.mp4"
        proc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-i", str(base), "-f", "rawvideo", "-pix_fmt", "rgba",
                                 "-s", f"{w}x{h}", "-r", f"{fps:g}", "-i", "-", "-filter_complex",
                                 "[0:v][1:v]overlay=0:0:format=auto[v]", "-map", "[v]", "-c:v", "libx264", "-preset", "medium",
                                 "-crf", "17", "-pix_fmt", "yuv420p", "-an", str(silent)], stdin=subprocess.PIPE)
        write_frames(r, fps, span, timing.start, proc.stdin, True)
        proc.stdin.close()
        if proc.wait() != 0:
            raise SystemExit("ffmpeg (наложение) упал")
        mix(base, silent, args.out, sound_cues(timing, not args.no_exit))
    print(json.dumps({"out": str(args.out) if args.out else None, "alpha": str(args.alpha_out) if args.alpha_out else None,
                      "total": round(timing.total, 2), "strokes": len(r.strokes), "mark_strokes": len(r.mark_strokes),
                      "work": str(work)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
