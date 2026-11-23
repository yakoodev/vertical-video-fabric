# -*- coding: utf-8 -*-
"""Contact sheet (24 frames) + sync check: vocal-stem waveform and spectrogram strip with word onsets (red) and the
frame each lyric becomes visible (green), for the clip window.

python check.py PLAN.json VIDEO.mp4 VOCAL_STEM.wav STEM_T0 OUT_DIR
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import wave

import cv2
import numpy as np


def contact(video: str, out: str, n: int = 24, cols: int = 6):
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", video],
                               capture_output=True, text=True).stdout)
    tiles = []
    for i in range(n):
        t = (i + 0.5) * dur / n
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", video, "-frames:v", "1", "-vf",
                            "scale=270:480", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], capture_output=True)
        im = np.frombuffer(r.stdout, np.uint8).reshape(480, 270, 3).copy()
        lab = f"{t:5.2f}s"
        cv2.putText(im, lab, (6, 22), 0, 0.55, (0, 0, 0), 3); cv2.putText(im, lab, (6, 22), 0, 0.55, (255, 255, 255), 1)
        tiles.append(im)
    rows = [np.hstack(tiles[i:i + cols]) for i in range(0, n, cols)]
    cv2.imwrite(out, np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 90])


def load(path):
    w = wave.open(path); sr, n, ch, sw = w.getframerate(), w.getnframes(), w.getnchannels(), w.getsampwidth()
    x = np.frombuffer(w.readframes(n), np.int16 if sw == 2 else np.int32).astype(np.float32)
    x /= 32768 if sw == 2 else 2 ** 31
    return x.reshape(-1, ch).mean(1), sr


def sync(plan: dict, stem: str, t0: float, out: str):
    x, sr = load(stem)
    a = int((plan["start"] - t0) * sr); b = a + int(plan["duration"] * sr)
    seg = x[a:b]
    Wpx, Hw, Hs = 3000, 260, 220
    img = np.full((Hw + Hs + 120, Wpx, 3), 18, np.uint8)
    # waveform (peak envelope per pixel column)
    cols = np.array_split(seg, Wpx)
    pk = np.array([np.abs(c).max() if len(c) else 0 for c in cols]); pk /= pk.max() + 1e-9
    for i, p in enumerate(pk):
        hgt = int(p * (Hw / 2 - 8))
        cv2.line(img, (i, Hw // 2 - hgt), (i, Hw // 2 + hgt), (150, 200, 230), 1)
    # spectrogram strip
    hop, nfft = sr // 200, 2048
    pad = np.pad(seg, (nfft // 2, nfft // 2))
    fr = np.lib.stride_tricks.sliding_window_view(pad, nfft)[::hop] * np.hanning(nfft)
    S = np.abs(np.fft.rfft(fr, axis=1)); f = np.fft.rfftfreq(nfft, 1 / sr)
    idx = np.searchsorted(f, np.geomspace(80, 5000, 160))
    Sl = np.log1p(50 * S[:, idx]); Sl /= np.percentile(Sl, 99.5)
    sp = cv2.resize(np.clip(Sl.T[::-1], 0, 1), (Wpx, Hs))
    img[Hw:Hw + Hs] = cv2.applyColorMap((sp * 255).astype(np.uint8), cv2.COLORMAP_INFERNO)
    X = lambda t: int(t / plan["duration"] * Wpx)  # noqa: E731
    for s in range(0, int(plan["duration"]) + 1):
        cv2.line(img, (X(s), Hw + Hs), (X(s), Hw + Hs + 12), (220, 220, 220), 1)
        cv2.putText(img, f"{s}s", (X(s) + 3, Hw + Hs + 26), 0, 0.5, (220, 220, 220), 1)
    k = 0
    for tx in plan["texts"]:
        words = tx.get("words") or [w for ln in tx.get("lines", []) for w in ln]
        for wd in words:
            if wd.get("on") is None:
                continue
            on = wd["on"]
            vis = (np.floor(on * plan["fps"]) - 1) / plan["fps"]
            cv2.line(img, (X(on), 0), (X(on), Hw + Hs), (60, 60, 255), 2)
            cv2.line(img, (X(vis), Hw - 40), (X(vis), Hw + 10), (80, 230, 80), 2)
            y = Hw + Hs + 48 + 22 * (k % 3)
            cv2.putText(img, wd["text"], (X(on) + 3, y), 0, 0.6, (120, 120, 255), 1, cv2.LINE_AA)
            k += 1
    cv2.putText(img, "red = vocal onset on the demucs stem; green = word fully visible (1-2 frames before)", (10, 20), 0, 0.6,
                (230, 230, 230), 1, cv2.LINE_AA)
    cv2.imwrite(out, img)


if __name__ == "__main__":
    plan = json.load(open(sys.argv[1], encoding="utf-8"))
    os.makedirs(sys.argv[5], exist_ok=True)
    contact(sys.argv[2], os.path.join(sys.argv[5], "contact_sheet.jpg"))
    sync(plan, sys.argv[3], float(sys.argv[4]), os.path.join(sys.argv[5], "sync_check.png"))
    print("ok")
