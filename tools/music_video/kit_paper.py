# -*- coding: utf-8 -*-
"""Стиль «paper» (пресет song15): процедурная бумага — зерно, рваные края, буквы-вырезки, полоски подписей."""
import math
from pathlib import Path
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONTS = Path(__file__).resolve().parents[2] / "vendor" / "fonts"


def font(name, size):
    return ImageFont.truetype(str(FONTS / name), size)


def vnoise(h, w, rng, octaves=5, base=4, persist=0.55):
    """Fractal value noise in [-1,1]."""
    out = np.zeros((h, w), np.float32)
    amp, tot = 1.0, 0.0
    for o in range(octaves):
        gh, gw = max(2, int(base * 2 ** o * h / max(h, w)) + 2), max(2, int(base * 2 ** o * w / max(h, w)) + 2)
        g = rng.uniform(-1, 1, (gh, gw)).astype(np.float32)
        out += amp * cv2.resize(g, (w, h), interpolation=cv2.INTER_CUBIC)
        tot += amp
        amp *= persist
    return out / tot


def noise1d(n, rng, octaves=6, base=6, persist=0.6):
    out = np.zeros(n, np.float32)
    amp, tot = 1.0, 0.0
    for o in range(octaves):
        k = int(base * 2 ** o) + 2
        g = rng.uniform(-1, 1, k)
        xs = np.linspace(0, k - 1, n)
        out += amp * np.interp(xs, np.arange(k), g)
        tot += amp
        amp *= persist
    return out / tot


def paper_texture(h, w, rng, base=(232, 226, 211), dark=False, stains=True):
    """RGB float32 0..1 paper with mottling, fibers, grain."""
    b = np.array(base, np.float32) / 255
    img = np.ones((h, w, 3), np.float32) * b
    mott = vnoise(h, w, rng, 6, 3) * (0.035 if not dark else 0.02)
    img += mott[..., None]
    # fibers
    fib = np.zeros((h, w), np.float32)
    nf = int(h * w / 900)
    for _ in range(nf):
        x, y = rng.uniform(0, w), rng.uniform(0, h)
        ang = rng.uniform(0, math.pi)
        ln = rng.uniform(4, 22)
        pts = []
        for t in np.linspace(0, 1, 5):
            ang += rng.normal(0, 0.25)
            pts.append((x + math.cos(ang) * ln * t, y + math.sin(ang) * ln * t))
        cv2.polylines(fib, [np.int32(pts)], False, float(rng.choice([-1, 1]) * rng.uniform(0.3, 1)), 1, cv2.LINE_AA)
    fib = cv2.GaussianBlur(fib, (0, 0), 0.6)
    img += fib[..., None] * (0.03 if not dark else 0.05)
    # grain
    gr = cv2.GaussianBlur(rng.normal(0, 1, (h, w)).astype(np.float32), (0, 0), 0.7)
    img += gr[..., None] * (0.022 if not dark else 0.03)
    if stains and not dark:
        st = np.clip(vnoise(h, w, rng, 4, 2) * 1.6 - 0.6, 0, 1)
        img -= st[..., None] * np.array([0.02, 0.04, 0.08], np.float32)
    # subtle crease
    if rng.random() < 0.6:
        x0 = rng.uniform(0.2, 0.8) * w
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        a = rng.uniform(-0.3, 0.3)
        d = (xx - x0) - a * (yy - h / 2)
        img += (np.exp(-(d / 2.5) ** 2) * 0.05 - np.exp(-((d - 3) / 4) ** 2) * 0.03)[..., None]
    return np.clip(img, 0, 1)


def torn_polygon(cx_pts, rng, rough=6.0, step=3.0, jag=0.35):
    """Densify a closed polygon and displace along normals with 1D fractal noise + jagged spikes."""
    pts = np.array(cx_pts, np.float32)
    dense = []
    for i in range(len(pts)):
        a, b = pts[i], pts[(i + 1) % len(pts)]
        n = max(2, int(np.linalg.norm(b - a) / step))
        for t in np.linspace(0, 1, n, endpoint=False):
            dense.append(a + (b - a) * t)
    dense = np.array(dense)
    n = len(dense)
    nz = noise1d(n, rng, 7, max(3, n // 60)) * rough + rng.normal(0, rough * jag * 0.35, n)
    # normals
    nxt = np.roll(dense, -1, 0); prv = np.roll(dense, 1, 0)
    tg = nxt - prv
    nr = np.stack([tg[:, 1], -tg[:, 0]], 1)
    nr /= (np.linalg.norm(nr, axis=1, keepdims=True) + 1e-6)
    return dense + nr * nz[:, None]


def rasterize(poly, h, w, ss=2):
    m = np.zeros((h * ss, w * ss), np.uint8)
    cv2.fillPoly(m, [np.int32(np.round(poly * ss))], 255, cv2.LINE_AA)
    return cv2.resize(m, (w, h), interpolation=cv2.INTER_AREA).astype(np.float32) / 255


def fiber_rim(mask, rng, width=3.0):
    """Torn paper shows a lighter fibrous core outside the printed surface: dilate with noise."""
    h, w = mask.shape
    dil = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (int(width * 2) | 1, int(width * 2) | 1)))
    spk = (rng.random((h, w)) < 0.35).astype(np.float32)
    spk = cv2.GaussianBlur(spk, (0, 0), 0.6)
    nz = np.clip(vnoise(h, w, rng, 5, 30) * 2 + 0.6, 0, 1)
    rim = np.clip(dil - mask, 0, 1) * np.clip(spk * 1.4 + nz * 0.5, 0, 1)
    return rim


def compose_rgba(rgb, mask, rim, rng, rim_col=(0.97, 0.96, 0.93), shadow=True, sh_off=(6, 9), sh_blur=7, sh_op=0.55):
    h, w = mask.shape
    a = np.clip(mask + rim, 0, 1)
    col = rgb * mask[..., None] + np.array(rim_col, np.float32) * rim[..., None]
    col = col / np.maximum(a[..., None], 1e-4)
    if shadow:
        M = np.float32([[1, 0, sh_off[0]], [0, 1, sh_off[1]]])
        sh = cv2.warpAffine(a, M, (w, h))
        sh = cv2.GaussianBlur(sh, (0, 0), sh_blur) * sh_op
        out_a = a + sh * (1 - a)
        col = col * (a / np.maximum(out_a, 1e-4))[..., None]  # shadow is black
        a = out_a
    rgba = np.dstack([np.clip(col, 0, 1), a])
    return (rgba * 255 + 0.5).astype(np.uint8)


def text_mask(txt, fnt, pad=0, jitter=0.0, rng=None):
    l, t, r, b = fnt.getbbox(txt)
    w, h = r - l + 2 * pad, b - t + 2 * pad
    im = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(im)
    if jitter and rng is not None and len(txt) > 1:
        x = pad - l
        for ch in txt:
            dy = rng.normal(0, jitter)
            d.text((x, pad - t + dy), ch, font=fnt, fill=int(255 * rng.uniform(0.82, 1.0)))
            x += fnt.getlength(ch)
    else:
        d.text((pad - l, pad - t), txt, font=fnt, fill=255)
    return np.asarray(im).astype(np.float32) / 255


def ink(mask, rng, rough=0.35, bleed=0.6):
    """Printed / typed ink: slight bleed + broken coverage."""
    m = cv2.GaussianBlur(mask, (0, 0), bleed)
    h, w = m.shape
    hole = np.clip(vnoise(h, w, rng, 4, max(8, w // 4)) * 2.0 + 1.0, 0, 1)
    sp = cv2.GaussianBlur((rng.random((h, w)) > rough * 0.25).astype(np.float32), (0, 0), 0.5)
    return np.clip(m * (0.9 + 0.1 * hole) * (0.88 + 0.12 * sp), 0, 1)


def ransom_letter(ch, rng, height=420, style="white", fontname="PTSerif-Bold.ttf"):
    """A single letter printed on its own torn paper chip."""
    fsz = int(height * rng.uniform(0.9, 1.12))
    f = font(fontname, fsz)
    lm = text_mask(ch, f)
    lh, lw = lm.shape
    px = int(lw * rng.uniform(0.14, 0.3)) + 14
    py = int(lh * rng.uniform(0.12, 0.24)) + 14
    W, H = lw + 2 * px + 40, lh + 2 * py + 40
    rng2 = rng
    # chip polygon: a slightly irregular quad
    j = lambda s: rng2.uniform(-s, s)
    x0, y0, x1, y1 = 20, 20, W - 20, H - 20
    quad = [(x0 + j(10), y0 + j(10)), (x1 + j(10), y0 + j(10)), (x1 + j(10), y1 + j(10)), (x0 + j(10), y1 + j(10))]
    poly = torn_polygon(quad, rng2, rough=rng2.uniform(3.5, 6.5), step=2.5)
    mask = rasterize(poly, H, W)
    if style == "white":
        base, inkc = (rng.uniform(222, 240), rng.uniform(214, 232), rng.uniform(196, 214)), (0.07, 0.065, 0.06)
    elif style == "grey":
        v = rng.uniform(150, 185); base, inkc = (v, v - 3, v - 8), (0.04, 0.04, 0.04)
    elif style == "black":
        base, inkc = (26, 25, 24), (0.9, 0.88, 0.84)
    else:  # red
        base, inkc = (128, 14, 18), (0.95, 0.92, 0.88)
    paper = paper_texture(H, W, rng, base=base, dark=style in ("black", "red"))
    im = ink(lm, rng)
    full = np.zeros((H, W), np.float32)
    full[py + 20:py + 20 + lh, px + 20:px + 20 + lw] = im
    rgb = paper * (1 - full[..., None]) + np.array(inkc, np.float32) * full[..., None]
    rim = fiber_rim(mask, rng, 3.5)
    return compose_rgba(rgb, mask, rim, rng, sh_off=(8, 12), sh_blur=9, sh_op=0.6)


def cutout_letter(ch, rng, height=460, fontname="DelaGothicOne-Regular.ttf", tint=(236, 232, 222)):
    """A letter torn out of white paper (no chip): rough outline + fiber rim + paper grain + soft fold shading."""
    fsz = int(height * rng.uniform(0.92, 1.1))
    f = font(fontname, fsz)
    lm = text_mask(ch, f, pad=40)
    H, W = lm.shape
    # roughen outline: threshold a noise-warped version
    nx = vnoise(H, W, rng, 5, 18) * 3.2
    ny = vnoise(H, W, rng, 5, 18) * 3.2
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    warped = cv2.remap(lm, xx + nx, yy + ny, cv2.INTER_LINEAR)
    warped = cv2.GaussianBlur(warped, (0, 0), 1.2)
    edge_n = vnoise(H, W, rng, 6, 60) * 0.18
    mask = np.clip((warped - 0.5 + edge_n) * 6 + 0.5, 0, 1)
    paper = paper_texture(H, W, rng, base=tint)
    # fold shading: a diagonal light/dark split
    ang = rng.uniform(0, math.pi)
    d = (xx - W / 2) * math.cos(ang) + (yy - H / 2) * math.sin(ang)
    paper *= (1 + np.tanh(d / 12) * rng.uniform(0.03, 0.07))[..., None]
    rim = fiber_rim(mask, rng, 3.0)
    return compose_rgba(paper, mask, rim, rng, sh_off=(9, 13), sh_blur=10, sh_op=0.65)


def caption_word(word, rng, cap=88, fontname="PTSerif-Regular.ttf"):
    """A typewriter-ish word on its own torn paper strip."""
    f = font(fontname, int(cap * 1.38))
    lm = text_mask(word, f, jitter=1.6, rng=rng)
    lh, lw = lm.shape
    px, py = int(cap * rng.uniform(0.35, 0.6)), int(cap * rng.uniform(0.32, 0.45))
    W, H = lw + 2 * px + 30, lh + 2 * py + 30
    j = lambda s: rng.uniform(-s, s)
    x0, y0, x1, y1 = 15, 15, W - 15, H - 15
    quad = [(x0 + j(4), y0 + j(3)), (x1 + j(4), y0 + j(3)), (x1 + j(4), y1 + j(3)), (x0 + j(4), y1 + j(3))]
    poly = torn_polygon(quad, rng, rough=3.2, step=2.0)
    mask = rasterize(poly, H, W)
    v = rng.uniform(218, 238)
    paper = paper_texture(H, W, rng, base=(v, v - 5, v - 18))
    im = ink(lm, rng, rough=0.5, bleed=0.7)
    full = np.zeros((H, W), np.float32)
    full[py + 15:py + 15 + lh, px + 15:px + 15 + lw] = im
    rgb = paper * (1 - full[..., None] * 0.93) + np.array([0.05, 0.045, 0.04]) * full[..., None] * 0.93
    rim = fiber_rim(mask, rng, 2.5)
    return compose_rgba(rgb, mask, rim, rng, sh_off=(5, 7), sh_blur=5, sh_op=0.55)


def torn_sheet_alpha(h, w, rng, margin=0.012):
    """Alpha for a printed photo sheet with torn/deckled edges and fiber rim."""
    m = int(min(h, w) * margin) + 6
    quad = [(m, m), (w - m, m), (w - m, h - m), (m, h - m)]
    poly = torn_polygon(quad, rng, rough=7, step=4)
    mask = rasterize(poly, h, w, ss=1)
    mask = cv2.GaussianBlur(mask, (0, 0), 0.7)
    rim = fiber_rim(mask, rng, 6)
    return mask, rim
