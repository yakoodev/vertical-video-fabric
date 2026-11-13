# -*- coding: utf-8 -*-
"""sync_check.mp4: готовый клип сверху + бегущая спектрограмма вокального стема снизу с метками.

Жёлтые линии — атаки слов (по ним анимация), красные — кадры удара больших слов из scene.json,
серые — сырое время Whisper, синие засечки — доли. Белая вертикаль — текущий момент."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import cv2
import numpy as np

from audio import load_wav


def make(out_dir: Path, video: Path, dst: Path, span: float = 3.0) -> Path:
    work = out_dir / "work"
    an = json.load(open(work / "analysis.json", encoding="utf-8"))
    sc = json.load(open(work / "scene.json", encoding="utf-8"))
    fps, t0 = sc["fps"], an["start"]
    x, sr = load_wav(str(work / "sep" / "htdemucs" / "mix44" / "vocals.wav"))
    hop, nfft = max(1, sr // 300), 2048
    fr = np.lib.stride_tricks.sliding_window_view(np.pad(x, (nfft // 2, nfft // 2)), nfft)[::hop] * np.hanning(nfft)
    S = np.abs(np.fft.rfft(fr, axis=1)).astype(np.float32)
    f = np.fft.rfftfreq(nfft, 1 / sr)
    idx = np.searchsorted(f, np.geomspace(80, 5000, 260))
    Sl = np.log1p(50 * S[:, idx]); Sl = np.clip(Sl / np.percentile(Sl, 99.5), 0, 1)
    spec = cv2.applyColorMap((Sl.T[::-1] * 255).astype(np.uint8), cv2.COLORMAP_INFERNO)
    env = np.sqrt((S[:, (f > 200) & (f < 3500)] ** 2).sum(1)); env = np.convolve(env, np.hanning(9) / 4.5, mode="same")
    env = env / (np.percentile(env, 99) + 1e-9)
    cps = sr / hop
    del S, fr
    lands = sorted({L["land"] for s in sc["scenes"] for it in s["items"] if it["type"] == "word" for L in it["letters"]})
    hero_t = [t0 + k / fps for k in lands]
    words = an["words"]
    beats = an["beats"]
    cap = cv2.VideoCapture(str(video))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    ff = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", "1080x1920", "-r", str(fps),
                           "-i", "-", "-i", str(work / "audio.wav"), "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-crf", "24",
                           "-preset", "fast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-shortest",
                           "-movflags", "+faststart", str(dst)], stdin=subprocess.PIPE)
    y0 = 1190
    for i in range(n):
        ok, frame = cap.read()
        if not ok:
            break
        canvas = np.zeros((1920, 1080, 3), np.uint8)
        canvas[0:1152, 216:864] = cv2.resize(frame, (648, 1152), interpolation=cv2.INTER_AREA)
        t = t0 + i / fps
        a = t - span / 2
        c0, c1 = int((a - t0) * cps), int((a + span - t0) * cps)
        sub = spec[:, max(0, c0):max(c0 + 2, c1)]
        if sub.shape[1] > 1:
            strip = cv2.resize(sub, (1080, 420), interpolation=cv2.INTER_LINEAR)
            if c0 < 0:
                strip[:, :int(-c0 / (c1 - c0) * 1080)] = 0
            canvas[y0:y0 + 420] = strip
        e = env[max(0, c0):max(0, c1)]
        cv2.rectangle(canvas, (0, y0 + 430), (1079, y0 + 700), (25, 25, 25), -1)
        if len(e) > 1:
            off = int(max(0, -c0) / (c1 - c0) * 1080)
            pts = np.array([[off + int(j / (c1 - c0) * 1080), y0 + 690 - int(min(1.3, v) * 200)] for j, v in enumerate(e)], np.int32)
            cv2.polylines(canvas, [pts], False, (80, 255, 80), 2)

        def X(s):
            return int((s - a) / span * 1080)
        for b in beats:
            if a <= b <= a + span:
                cv2.line(canvas, (X(b), y0 + 430), (X(b), y0 + 470), (255, 150, 60), 2)
        for w in words:
            if a <= w["s"] <= a + span:
                cv2.line(canvas, (X(w["s"]), y0 + 380), (X(w["s"]), y0 + 700), (120, 120, 120), 1)
            if a <= w["on"] <= a + span:
                cv2.line(canvas, (X(w["on"]), y0 - 10), (X(w["on"]), y0 + 420), (0, 230, 255), 2)
        for h in hero_t:
            if a <= h <= a + span:
                cv2.line(canvas, (X(h), y0 + 600), (X(h), y0 + 700), (0, 60, 255), 3)
        cv2.line(canvas, (540, y0 - 20), (540, y0 + 710), (255, 255, 255), 2)
        cv2.putText(canvas, f"song {t:6.2f}s  video {i / fps:5.2f}s  f{i}", (20, 1180), 0, 0.8, (255, 255, 255), 2)
        cv2.putText(canvas, "yellow = sung onset  red = big-letter hit  grey = whisper  blue = beat", (20, 1910), 0, 0.62,
                    (200, 200, 200), 1)
        ff.stdin.write(canvas.tobytes())
    ff.stdin.close(); ff.wait()
    print("  sync_check:", dst)
    return dst
