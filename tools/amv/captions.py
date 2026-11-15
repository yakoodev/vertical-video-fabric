# -*- coding: utf-8 -*-
"""Vocal-hit typography in our «B2» lyric style (movie5 lyrics8), reduced to what AMVs use: one big word at a time.

cues_from_words() — pure: Whisper words (text only trusted) + vocal-stem onsets (timing) → cues.
Captions.draw()   — per frame: the word enters at its onset with a spring scale 1.45→1 (one overshoot), skew
                    14°→0 and blur-in; it glows in a palette colour, gets an R/B split on its first frames and
                    a small shake on kicks; exit = quick scale-up + fade, finished before the next cue.

cues JSON: [{"text": "PUMP IT", "t": 26.86, "end": 27.5, "hard": false}, ...]  (song seconds)."""
from __future__ import annotations

import math
import re
from pathlib import Path

import numpy as np

FONT_DIR = Path(__file__).resolve().parents[2] / "vendor" / "fonts"
FONT = "RobotoCondensed-Black.ttf"
GLOW = [(60, 200, 255), (255, 220, 120), (80, 80, 255), (255, 255, 255)]   # BGR: amber, cyan, red, white


def spring(age_frames: float, dur: float = 7.0) -> float:
    """0 → 1 with one overshoot (age in frames)."""
    if age_frames <= 0:
        return 0.0
    u = min(1.0, age_frames / dur)
    return 1 - math.exp(-5.5 * u) * math.cos(7.5 * u)


def snap(t: float, onsets: list, tol: float = 0.12) -> float:
    """Nearest vocal onset within tol (Whisper's word start is only a hint), else t."""
    best = None
    for o in onsets:
        ot = o[0] if isinstance(o, (list, tuple)) else o
        if abs(ot - t) <= tol and (best is None or abs(ot - t) < abs(best - t)):
            best = ot
    return float(best) if best is not None else float(t)


def cues_from_words(words: list[dict], keep: dict[str, str], onsets: list | None = None,
                    min_prob: float = 0.3, merge_gap: float = 0.35, max_hold: float = 0.9,
                    extra: list[dict] | None = None) -> list[dict]:
    """Keep only whitelisted words (keep: lowercase word → shown text) with probability >= min_prob, snap their
    start to a vocal onset, merge «PUMP» + «IT» style pairs closer than merge_gap, and set each end to the next
    cue (or + max_hold). extra — hand-placed cues (e.g. taken from the reference edit) added as they are."""
    out = []
    for w in words:
        key = re.sub(r"[^a-zа-яё']", "", w["w"].lower())
        if key not in keep or w.get("p", 1.0) < min_prob:
            continue
        t = snap(w["s"], onsets or [])
        txt = keep[key]
        if out and out[-1]["text"] == txt and t - out[-1]["t"] < merge_gap:
            continue
        if txt:
            out.append({"text": txt, "t": round(t, 3)})
    for e in extra or []:
        out.append({"text": e["text"], "t": round(snap(e["t"], onsets or []), 3), **({"hard": e["hard"]} if "hard" in e else {})})
    out.sort(key=lambda c: c["t"])
    dedup = []
    for c in out:
        if dedup and abs(c["t"] - dedup[-1]["t"]) < 0.08:
            continue
        dedup.append(c)
    for i, c in enumerate(dedup):
        nxt = dedup[i + 1]["t"] if i + 1 < len(dedup) else c["t"] + max_hold
        c["end"] = round(min(c["t"] + max_hold, nxt - 0.03), 3)
        c.setdefault("hard", c["text"].endswith("!") or len(c["text"]) <= 4)
    return dedup


class Captions:
    def __init__(self, cues: list[dict], W: int, H: int, fps: float, song: dict | None = None):
        from PIL import ImageFont
        self.cues = cues
        self.W, self.H, self.fps = W, H, fps
        self.tall = H > W
        self.px = int((0.17 if self.tall else 0.14) * min(W, H) * (1.9 if self.tall else 1.0))
        self.font = ImageFont.truetype(str(FONT_DIR / FONT), self.px)
        self.kicks = [k[0] for k in (song or {}).get("kicks", []) if k[1] >= 0.35]
        self.cache: dict = {}

    def _sprite(self, text: str, idx: int):
        """Text sprite: white letters + coloured glow (BGRA float), cached per cue."""
        import cv2
        from PIL import Image, ImageDraw
        if text in self.cache:
            return self.cache[text]
        size = self.px
        maxw = int(self.W * 0.9)
        font = self.font
        while True:
            l, t, r, b = font.getbbox(text)
            if r - l <= maxw or size < 20:
                break
            size = int(size * 0.9)
            from PIL import ImageFont
            font = ImageFont.truetype(str(FONT_DIR / FONT), size)
        pad = int(size * 0.5)
        w, h = r - l + 2 * pad, b - t + 2 * pad
        im = Image.new("L", (w, h), 0)
        ImageDraw.Draw(im).text((pad - l, pad - t), text, font=font, fill=255)
        a = np.asarray(im, np.float32) / 255.0
        stroke = cv2.dilate(a, np.ones((5, 5), np.uint8))
        glow = cv2.GaussianBlur(a, (0, 0), size * 0.12)
        col = np.array(GLOW[idx % len(GLOW)], np.float32)
        self.cache[text] = (a, stroke, glow, col)
        return self.cache[text]

    def draw(self, img: np.ndarray, t: float) -> np.ndarray:
        import cv2
        for i, c in enumerate(self.cues):
            if not (c["t"] - 0.5 / self.fps <= t < c["end"]):
                continue
            age = (t - c["t"]) * self.fps
            left = (c["end"] - t) * self.fps
            a, stroke, glow, col = self._sprite(c["text"], i)
            sc = 1.45 - 0.45 * spring(age + 1)
            skew = math.radians(14) * max(0.0, 1 - spring(age + 1))
            alpha = min(1.0, (age + 1) / 2.0)
            if left < 4:                      # exit
                sc *= 1 + 0.08 * (4 - left)
                alpha *= max(0.0, left / 4)
            h, w = a.shape
            kick = 0.0
            for k in self.kicks:
                if 0 <= t - k < 0.12:
                    kick = 1 - (t - k) / 0.12
            cx = self.W / 2 + kick * 6 * math.sin(age * 2.3)
            cy = self.H * (0.42 if self.tall else 0.5) - (1 - spring(age + 1)) * self.H * 0.03
            M = np.float32([[sc, -math.tan(skew) * sc, 0], [0, sc, 0]])
            M[0, 2] = cx - (M[0, 0] * w / 2 + M[0, 1] * h / 2)
            M[1, 2] = cy - (M[1, 1] * h / 2)
            warp = lambda m: cv2.warpAffine(m, M, (self.W, self.H), flags=cv2.INTER_LINEAR)
            A, S, G = warp(a), warp(stroke), warp(glow)
            if age < 2:                       # blur-in
                A = cv2.GaussianBlur(A, (0, 0), 3 - age)
            f = img.astype(np.float32)
            gl = (0.9 + 0.6 * (age < 4)) * alpha
            f = f + G[..., None] * col * gl                                   # glow (additive)
            f = f * (1 - 0.65 * S[..., None] * alpha)                         # dark outline
            f = f * (1 - A[..., None] * alpha) + A[..., None] * alpha * 250   # white letters
            img = np.clip(f, 0, 255).astype(np.uint8)
            if age < 3 or (c.get("hard") and age < 5):                        # R/B split on the hit
                p = int(round((5 - age) * self.W / 720 * 2))
                if p > 0:
                    from .fx import chroma_split
                    img = chroma_split(img, p)
        return img
