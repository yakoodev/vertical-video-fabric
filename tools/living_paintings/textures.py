# -*- coding: utf-8 -*-
"""Procedural textures for «living paintings»: aged parchment, damask museum wall, canvas weave.

python textures.py OUT_DIR
Writes parchment.png (RGBA, ragged burnt edge), wall.png (damask, tileable), canvas.png (weave, grey, tileable)."""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

RNG = np.random.default_rng(7)


def fbm(h: int, w: int, octaves: int = 6, base: int = 4, seed: int = 0, tile: bool = False) -> np.ndarray:
    rng = np.random.default_rng(seed)
    out = np.zeros((h, w), np.float32)
    amp, tot = 1.0, 0.0
    for o in range(octaves):
        n = base * 2 ** o
        g = rng.random((n + (0 if tile else 1), n + (0 if tile else 1))).astype(np.float32)
        if tile:
            g = np.pad(g, ((0, 1), (0, 1)), mode="wrap")
        out += amp * cv2.resize(g, (w, h), interpolation=cv2.INTER_CUBIC)
        tot += amp
        amp *= 0.55
    out /= tot
    return (out - out.min()) / (out.max() - out.min() + 1e-6)


def parchment(w: int = 2048, h: int = 1100) -> np.ndarray:
    n1 = fbm(h, w, 7, 3, 1)
    n2 = fbm(h, w, 5, 12, 2)
    base = np.array([150, 205, 236], np.float32)      # BGR warm cream
    dark = np.array([86, 128, 160], np.float32)
    col = base[None, None] * (1 - 0.22 * n1[..., None] * np.array([1.5, 1.0, 0.7], np.float32))
    col *= (0.93 + 0.07 * n2[..., None])
    # fibres
    fib = cv2.GaussianBlur(RNG.random((h, w)).astype(np.float32), (0, 0), sigmaX=6, sigmaY=0.6)
    col *= (0.96 + 0.08 * (fib - fib.mean())[..., None] / (fib.std() + 1e-6) * 0.25)
    # stains
    for _ in range(9):
        cx, cy, r = RNG.integers(0, w), RNG.integers(0, h), RNG.integers(60, 260)
        m = np.zeros((h, w), np.float32)
        cv2.circle(m, (int(cx), int(cy)), int(r), 1.0, -1)
        m = cv2.GaussianBlur(m, (0, 0), r * 0.35) * (0.5 + 0.5 * n2)
        ring = cv2.GaussianBlur(np.abs(cv2.Laplacian(m, cv2.CV_32F)), (0, 0), 3)
        ring /= ring.max() + 1e-6
        col *= (1 - (0.10 * m + 0.12 * ring)[..., None] * np.array([1.6, 1.0, 0.6], np.float32))
    # edge darkening + ragged burnt alpha
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    d = np.minimum.reduce([xx, w - 1 - xx, yy, h - 1 - yy])
    edge_noise = fbm(h, w, 6, 8, 3) * 46 + fbm(h, w, 3, 40, 4) * 14
    dd = d - edge_noise
    alpha = np.clip(dd / 3.0, 0, 1)
    burn = np.clip(1 - (dd - 0) / 70, 0, 1) ** 1.6
    col = col * (1 - 0.55 * burn[..., None]) + np.array([30, 50, 70], np.float32) * 0.55 * burn[..., None] * 0.6
    vign = np.clip(1 - 0.22 * ((xx / w - 0.5) ** 2 + (yy / h - 0.5) ** 2) * 4, 0.6, 1)
    col *= vign[..., None]
    out = np.dstack([np.clip(col, 0, 255), alpha * 255]).astype(np.uint8)
    return out


def damask(size: int = 1024) -> np.ndarray:
    """Deep green silk-damask wall, tileable: a stylised fleur medallion pattern in slightly glossier tone."""
    s = size
    pat = np.zeros((s, s), np.float32)
    # medallions on a diamond lattice
    def motif(img, cx, cy, sc):
        pts = []
        for k in range(48):
            a = k / 48 * 2 * np.pi
            r = sc * (0.55 + 0.25 * np.cos(4 * a) + 0.12 * np.cos(8 * a))
            pts.append((cx + r * np.sin(a) * 0.72, cy - r * np.cos(a)))
        cv2.fillPoly(img, [np.array(pts, np.int32)], 1.0, lineType=cv2.LINE_AA)
        cv2.ellipse(img, (int(cx), int(cy)), (int(sc * 0.16), int(sc * 0.32)), 0, 0, 360, 0.0, -1, cv2.LINE_AA)
        cv2.ellipse(img, (int(cx), int(cy - sc * 0.05)), (int(sc * 0.07), int(sc * 0.16)), 0, 0, 360, 1.0, -1, cv2.LINE_AA)
        for sx in (-1, 1):
            cv2.ellipse(img, (int(cx + sx * sc * 0.42), int(cy + sc * 0.55)), (int(sc * 0.20), int(sc * 0.08)),
                        sx * 35, 0, 360, 1.0, -1, cv2.LINE_AA)
    big = np.zeros((s * 3, s * 3), np.float32)
    for gx in range(-1, 4):
        for gy in range(-1, 4):
            motif(big, gx * s / 2 + s, gy * s / 2 + s, s * 0.2)
            motif(big, gx * s / 2 + s / 4 + s, gy * s / 2 + s / 4 + s, s * 0.11)
    pat = big[s:2 * s, s:2 * s]
    pat = cv2.GaussianBlur(pat, (0, 0), 2.0) * (0.6 + 0.4 * fbm(s, s, 3, 8, 11, tile=True))
    n = fbm(s, s, 6, 4, 9, tile=True)
    weave = (np.sin(np.arange(s) / 1.3)[None, :] * 0.5 + 0.5) * 0.04
    base = np.array([38, 56, 34], np.float32)   # BGR deep green
    hi = np.array([46, 68, 44], np.float32)
    col = base[None, None] * (1 - pat[..., None]) + hi * pat[..., None]
    col *= (0.85 + 0.25 * n[..., None]) * (1 + weave[..., None])
    return np.clip(col, 0, 255).astype(np.uint8)


def canvas_weave(size: int = 512) -> np.ndarray:
    x = np.arange(size, dtype=np.float32)
    a = 0.5 + 0.5 * np.sin(x / size * 2 * np.pi * 96)
    g = 0.5 * (a[None, :] + a[:, None]) + 0.12 * (fbm(size, size, 4, 16, 5, tile=True) - 0.5)
    g = (g - g.min()) / (g.max() - g.min())
    return (g * 255).astype(np.uint8)


if __name__ == "__main__":
    out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out / "parchment.png"), parchment())
    cv2.imwrite(str(out / "wall.png"), damask())
    cv2.imwrite(str(out / "canvas.png"), canvas_weave())
    print("ok", out)
