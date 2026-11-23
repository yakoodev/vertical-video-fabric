# -*- coding: utf-8 -*-
"""Grade + encode: warm museum look (gold highlights glow, warm shadows), vignette, soft film grain, drifting dust;
song section muxed untouched (only 10 ms click guards at the cut points).

python post.py PLAN.json FRAMES_DIR OUT.mp4 [--tg OUT_tg.mp4 --tg-mb 19.5]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess

import cv2
import numpy as np


def rain_layer(f: int, w: int, h: int, n: int = 160, seed: int = 5) -> np.ndarray:
    """Wind-driven rain streaks (storm painting): thin diagonal strokes, three depths, fast."""
    r = np.random.default_rng(seed)
    lay = np.zeros((h // 2, w // 2), np.float32)
    for i in range(n):
        depth = r.random()
        sp = 38 + 50 * depth
        x0, y0 = r.uniform(0, w // 2 + 300), r.uniform(0, h // 2)
        x = (x0 - f * sp * 0.55) % (w // 2 + 300) - 150
        y = (y0 + f * sp) % (h // 2 + 200) - 100
        ln = 18 + 40 * depth
        cv2.line(lay, (int(x), int(y)), (int(x - ln * 0.5), int(y + ln)), 0.10 + 0.22 * depth, 1, cv2.LINE_AA)
    lay = cv2.GaussianBlur(lay, (0, 0), 0.7)
    return cv2.resize(lay, (w, h), interpolation=cv2.INTER_LINEAR)


def grade(img: np.ndarray, f: int, rng: np.random.Generator, dust: list, museum: bool, rain: float = 0.0) -> np.ndarray:
    x = img.astype(np.float32) / 255.0
    lum = x @ np.array([0.114, 0.587, 0.299], np.float32)
    # warm balance + gentle S-curve
    x = x * np.array([0.90, 1.0, 1.06], np.float32)             # BGR: warm
    x = x + (np.array([0.0, 0.012, 0.03], np.float32) * (1 - lum)[..., None] ** 2)
    x = np.clip(x, 0, 1)
    x = x + 0.35 * (x - x * x) * (2 * x - 1)                    # gentle S-curve
    # highlight glow (gold sheen, moon, parchment)
    hi = np.clip((x - 0.62) / 0.38, 0, 1) ** 1.5
    small = cv2.resize(hi, (x.shape[1] // 4, x.shape[0] // 4), interpolation=cv2.INTER_AREA)
    glow = cv2.resize(cv2.GaussianBlur(small, (0, 0), 9), (x.shape[1], x.shape[0]), interpolation=cv2.INTER_LINEAR)
    x = 1 - (1 - x) * (1 - 0.22 * glow * np.array([0.7, 0.9, 1.0], np.float32))
    # dust motes (additive, warm), stronger in the museum light
    h, w = lum.shape
    layer = np.zeros((h, w), np.float32)
    for d in dust:
        px = (d["x"] + d["vx"] * f + 18 * np.sin(f * d["w"] + d["ph"])) % (w + 80) - 40
        py = (d["y"] + d["vy"] * f + 10 * np.sin(f * d["w"] * 0.7 + d["ph"] * 2)) % (h + 80) - 40
        a = d["a"] * (0.6 + 0.4 * np.sin(f * 0.11 + d["ph"]))
        cv2.circle(layer, (int(px), int(py)), d["r"], float(a), -1, cv2.LINE_AA)
    layer = cv2.GaussianBlur(layer, (0, 0), 1.6)
    k = 1.0 if museum else 0.45
    x = x + (layer * k)[..., None] * np.array([0.55, 0.78, 1.0], np.float32)
    if rain > 0:
        x = x + (rain_layer(f, w, h) * rain)[..., None] * np.array([0.95, 0.92, 0.85], np.float32)
    # vignette
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    r2 = ((xx / w - 0.5) / 0.62) ** 2 + ((yy / h - 0.5) / 0.75) ** 2
    vig = np.clip(1 - 0.26 * r2 ** 1.4, 0.55, 1)
    x = x * vig[..., None]
    # grain (luma, slightly soft)
    g = rng.standard_normal((h // 2, w // 2)).astype(np.float32)
    g = cv2.resize(g, (w, h), interpolation=cv2.INTER_LINEAR)
    x = x + (0.016 * (0.5 + 0.8 * (1 - lum)))[..., None] * g[..., None]
    return (np.clip(x, 0, 1) * 255 + 0.5).astype(np.uint8)


def make_dust(n: int, w: int, h: int, seed: int = 3) -> list:
    r = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        depth = r.random()
        out.append({"x": r.uniform(0, w), "y": r.uniform(0, h), "vx": r.uniform(-0.25, 0.35) * (0.4 + depth),
                    "vy": r.uniform(-0.6, -0.1) * (0.4 + depth), "w": r.uniform(0.02, 0.06), "ph": r.uniform(0, 6.28),
                    "r": int(1 + depth * 3.5), "a": 0.10 + 0.32 * depth})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan"); ap.add_argument("frames"); ap.add_argument("out")
    ap.add_argument("--tg"); ap.add_argument("--tg-mb", type=float, default=19.5)
    ap.add_argument("--museum", default="0-59,318-449")
    ap.add_argument("--rain", default="144-210")
    a = ap.parse_args()
    plan = json.load(open(a.plan, encoding="utf-8"))
    nf, fps = plan["frames"], plan["fps"]
    museum = set()
    for part in a.museum.split(","):
        s, e = part.split("-"); museum |= set(range(int(s), int(e) + 1))
    wav = os.path.splitext(a.out)[0] + "_audio.wav"
    dur = nf / fps
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{plan['start']:.3f}", "-t", f"{dur:.3f}", "-i", plan["song"],
                    "-af", f"afade=t=in:d=0.01,afade=t=out:st={dur - 0.02:.3f}:d=0.02", "-ar", "48000", "-ac", "2", wav],
                   check=True)
    W, H = 1080, 1920
    ff = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
                           "-r", str(fps), "-i", "-", "-i", wav, "-map", "0:v", "-map", "1:a",
                           "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-pix_fmt", "yuv420p",
                           "-profile:v", "high", "-c:a", "aac", "-b:a", "256k", "-shortest", "-movflags", "+faststart",
                           a.out], stdin=subprocess.PIPE)
    rng = np.random.default_rng(11)
    dust = make_dust(70, W, H)
    for f in range(nf):
        img = cv2.imread(os.path.join(a.frames, f"{f:04d}.png"))
        if img is None:
            raise SystemExit(f"missing frame {f}")
        r0, r1 = (int(v) for v in a.rain.split("-"))
        rain = 0.0 if not (r0 <= f <= r1) else 0.75 * min(1.0, (f - r0) / 4, (r1 - f) / 2 + 0.3)
        ff.stdin.write(grade(img, f, rng, dust, f in museum, rain).tobytes())
        if f % 50 == 0:
            print("  graded", f, flush=True)
    ff.stdin.close(); ff.wait()
    if a.tg:
        kbps = int(a.tg_mb * 8 * 1024 / dur) - 200
        base = ["ffmpeg", "-v", "error", "-y", "-i", a.out, "-c:v", "libx264", "-preset", "slow", "-b:v", f"{kbps}k",
                "-pix_fmt", "yuv420p"]
        pl = os.path.splitext(a.tg)[0] + "_2pass"
        subprocess.run(base + ["-pass", "1", "-passlogfile", pl, "-an", "-f", "mp4", os.devnull], check=True)
        subprocess.run(base + ["-pass", "2", "-passlogfile", pl, "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
                               a.tg], check=True)
        for ext in ("-0.log", "-0.log.mbtree"):
            if os.path.exists(pl + ext):
                os.remove(pl + ext)
    print("ok", a.out)


if __name__ == "__main__":
    main()
