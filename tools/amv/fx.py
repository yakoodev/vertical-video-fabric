# -*- coding: utf-8 -*-
"""Edit effects for AMV renders.

Envelopes (pure, unit-tested): velocity rate curve and its integral (source time), zoom punch, shake, flash.
Image ops (cv2/numpy): affine punch+shake, directional (whip) blur, flash, chromatic split, grade LUT, vignette."""
from __future__ import annotations

import math

import numpy as np

SQRT_PI = math.sqrt(math.pi)


# ----------------------------------------------------------------------------- velocity (speed ramps)
def _peaks(ramp: dict) -> list[tuple[float, float, float]]:
    """(time, extra rate above base, width) of every burst. Accepts {"peak", "ta", "w"} (one burst) and/or
    {"bursts": [{"t", "amp", "w"}]} (amp = extra rate above base)."""
    out = []
    if ramp.get("peak", 0) > 0:
        out.append((ramp["ta"], ramp["peak"] - ramp["base"], ramp["w"]))
    for b in ramp.get("bursts", []):
        if b["amp"] > 0:
            out.append((b["t"], b["amp"], b["w"]))
    return out


def rate_at(t: float, ramp: dict) -> float:
    """Playback rate at song time t: base + Gaussian bursts (velocity edit). No bursts → constant base."""
    r = ramp["base"]
    for ta, amp, w in _peaks(ramp):
        r += amp * math.exp(-((t - ta) / w) ** 2)
    return r


def rate_integral(t0: float, t1: float, ramp: dict) -> float:
    """Source seconds consumed between song times t0 and t1 (closed form of rate_at)."""
    out = ramp["base"] * (t1 - t0)
    for ta, amp, w in _peaks(ramp):
        out += amp * w * SQRT_PI / 2 * (math.erf((t1 - ta) / w) - math.erf((t0 - ta) / w))
    return out


def src_time(t: float, seg: dict) -> float:
    """Source time shown at song time t inside a planned segment (seg: t0, src_in, ramp)."""
    return seg["src_in"] + rate_integral(seg["t0"], t, seg["ramp"])


# ----------------------------------------------------------------------------- hit envelopes
def punch_scale(t: float, hits: list[dict]) -> float:
    """1 + sum of decaying zoom kicks. hit = {"t", "amp", "tau"}; the kick rises over 2 frames' worth (35 ms)."""
    s = 0.0
    for h in hits:
        d = t - h["t"]
        if d < -0.035 or d > 6 * h["tau"]:
            continue
        rise = min(1.0, (d + 0.035) / 0.035)
        s += h["amp"] * rise * math.exp(-max(0.0, d) / h["tau"])
    return 1.0 + s


def shake_offset(t: float, hits: list[dict]) -> tuple[float, float, float]:
    """(dx, dy, rot_deg) — damped pseudo-random shake; dx/dy as fractions of the frame width.

    hit = {"t", "amp", "tau", "seed"}. Deterministic: same input → same output."""
    dx = dy = rot = 0.0
    for h in hits:
        d = t - h["t"]
        if d < 0 or d > 5 * h["tau"]:
            continue
        env = h["amp"] * math.exp(-d / h["tau"])
        s = h.get("seed", 0)
        dx += env * (math.sin(2 * math.pi * 23 * d + s) + 0.5 * math.sin(2 * math.pi * 37 * d + 2.1 * s))
        dy += env * (math.sin(2 * math.pi * 19 * d + 1.7 * s) + 0.5 * math.sin(2 * math.pi * 31 * d + 0.3 * s))
        rot += env * 60 * math.sin(2 * math.pi * 13 * d + 0.9 * s)
    return dx, dy, rot


def flash_alpha(t: float, flashes: list[dict], fps: float) -> tuple[float, tuple[int, int, int]]:
    """Strongest active flash: alpha 0..1 and its BGR colour. flash = {"t", "frames", "color", "amp"}.
    Full strength on the first frame, linear decay over `frames` frames."""
    best = (0.0, (255, 255, 255))
    for f in flashes:
        d = (t - f["t"]) * fps
        if -0.5 <= d < f["frames"]:
            a = f.get("amp", 1.0) * (1.0 - max(0.0, d) / max(1, f["frames"]))
            if a > best[0]:
                best = (a, tuple(f.get("color", (255, 255, 255))))
    return best


def whip_amount(t: float, whips: list[dict], fps: float) -> tuple[float, tuple[float, float]]:
    """Directional blur strength 0..1 around a whip cut (2 frames before, 2 after) and its direction."""
    for w in whips:
        d = (t - w["t"]) * fps
        n = w.get("frames", 2)
        if -n <= d < n:
            a = 1.0 - abs(d + 0.5) / (n + 0.5)
            return max(0.0, a), tuple(w["dir"])
    return 0.0, (1.0, 0.0)


# ----------------------------------------------------------------------------- image ops
def affine(img: np.ndarray, scale: float, dx: float, dy: float, rot: float, cx: float = 0.5,
           cy: float = 0.5) -> np.ndarray:
    import cv2
    h, w = img.shape[:2]
    if abs(scale - 1) < 1e-4 and abs(dx) < 1e-5 and abs(dy) < 1e-5 and abs(rot) < 1e-3:
        return img
    M = cv2.getRotationMatrix2D((w * cx, h * cy), rot, scale)
    M[0, 2] += dx * w
    M[1, 2] += dy * w
    return cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT101)


def dir_blur(img: np.ndarray, d: tuple[float, float], L: float) -> np.ndarray:
    """Motion blur of length L px along direction d (computed at half resolution)."""
    import cv2
    L = int(L)
    if L < 3:
        return img
    h, w = img.shape[:2]
    small = cv2.resize(img, (w // 2, h // 2), interpolation=cv2.INTER_AREA)
    n = max(3, L // 2) | 1
    k = np.zeros((n, n), np.float32)
    c = (n - 1) / 2
    dn = np.array(d, np.float32)
    dn /= max(1e-6, float(np.linalg.norm(dn)))
    for i in range(n):
        u = i / (n - 1) - 0.5
        k[int(round(c + dn[1] * u * (n - 1))), int(round(c + dn[0] * u * (n - 1)))] = 1
    k /= k.sum()
    small = cv2.filter2D(small, -1, k)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


def whip(img: np.ndarray, a: float, d: tuple[float, float], tint: tuple[int, int, int] | None = None) -> np.ndarray:
    """Whip-pan look: directional blur + push along d + slight colour cast, strength a (0..1)."""
    if a <= 0.01:
        return img
    h, w = img.shape[:2]
    out = dir_blur(img, d, a * 0.12 * max(w, h))
    out = affine(out, 1.0 + 0.04 * a, d[0] * 0.05 * a, d[1] * 0.05 * a, 0.0)
    if tint is not None:
        out = np.clip(out.astype(np.float32) * (1 - 0.25 * a) + np.array(tint, np.float32) * 0.25 * a, 0, 255).astype(np.uint8)
    return out


def flash(img: np.ndarray, a: float, color=(255, 255, 255)) -> np.ndarray:
    if a <= 0.01:
        return img
    c = np.array(color, np.float32)
    # screen-like: bright parts go first, so 1-frame flashes keep a ghost of the image
    f = img.astype(np.float32)
    out = f + (c - f) * min(1.0, a)
    return np.clip(out, 0, 255).astype(np.uint8)


def chroma_split(img: np.ndarray, px: float) -> np.ndarray:
    """Shift the R channel right and B left by px pixels (RGB split on hits)."""
    p = int(round(px))
    if p < 1:
        return img
    out = img.copy()
    out[:, p:, 2] = img[:, :-p, 2]
    out[:, :-p, 0] = img[:, p:, 0]
    return out


def look_curve(x: np.ndarray) -> np.ndarray:
    """Filmic S-curve with a crushed-but-not-clipped black (0..1 → 0..1)."""
    s = 1 / (1 + np.exp(-(x - 0.47) * 7.0))
    s0, s1 = 1 / (1 + math.exp(0.47 * 7.0)), 1 / (1 + math.exp(-(1 - 0.47) * 7.0))
    s = (s - s0) / (s1 - s0)
    y = 0.75 * s + 0.25 * x
    return 0.02 + y * (0.985 - 0.02)


def grade_lut(stats: dict, target: dict | None = None, strength: float = 0.6) -> np.ndarray:
    """Per-shot 256x1x3 BGR LUT: luma/contrast pulled toward target, cast neutralized, then the look curve and a
    teal-shadow / warm-highlight split-tone. stats = {"lum", "lstd", "mean": [b, g, r]}."""
    target = target or {"lum": 92.0, "lstd": 58.0}
    x = np.arange(256, dtype=np.float32)
    # dark shots: a gamma lift first (moves the mean toward the target without clipping highlights)
    lum0 = max(4.0, stats["lum"])
    g = float(np.clip(math.log(target["lum"] / 255) / math.log(lum0 / 255), 0.6, 1.0)) if lum0 < target["lum"] else 1.0
    xg = 255.0 * (x / 255.0) ** g
    lum = 255.0 * (lum0 / 255.0) ** g
    lstd = stats["lstd"] * g * (lum / lum0) if lum0 > 0 else stats["lstd"]
    k = float(np.clip(target["lstd"] / max(lstd, 1.0), 0.8, 1.35)) ** strength
    dl = (target["lum"] - lum) * strength * 0.7
    m = np.array(stats["mean"], np.float32)
    cast = m - m.mean()
    chans = []
    for c in range(3):
        v = (xg - lum) * k + lum + dl - cast[c] * strength * 0.6
        chans.append(np.clip(v, 0, 255) / 255.0)
    y = look_curve(np.stack(chans, 1))
    l = look_curve(x / 255.0)
    sh = np.clip(1 - l / 0.45, 0, 1) ** 1.5
    hi = np.clip((l - 0.55) / 0.45, 0, 1) ** 1.2
    y[:, 0] += 0.06 * sh - 0.04 * hi
    y[:, 1] += 0.025 * sh + 0.006 * hi
    y[:, 2] += -0.04 * sh + 0.045 * hi
    return (np.clip(y, 0, 1) * 255).round().astype(np.uint8).reshape(1, 256, 3)


def frame_stats(img: np.ndarray) -> dict:
    a = img[::4, ::4].reshape(-1, 3).astype(np.float32)
    lum = a @ np.array([0.114, 0.587, 0.299], np.float32)
    return {"lum": float(lum.mean()), "lstd": float(lum.std()), "mean": a.mean(0).tolist()}


def saturate(img: np.ndarray, k: float) -> np.ndarray:
    import cv2
    if abs(k - 1) < 1e-3:
        return img
    g = cv2.cvtColor(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
    return cv2.addWeighted(img, k, g, 1 - k, 0)


_vig: dict = {}


def vignette(img: np.ndarray, strength: float = 0.35) -> np.ndarray:
    h, w = img.shape[:2]
    key = (h, w, strength)
    if key not in _vig:
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        r = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2) / math.sqrt(2)
        _vig[key] = (1 - strength * np.clip(r, 0, 1) ** 2.2)[..., None].astype(np.float32)
    return (img.astype(np.float32) * _vig[key]).astype(np.uint8)
