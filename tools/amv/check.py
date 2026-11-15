# -*- coding: utf-8 -*-
"""Visual checks: contact sheets (per shot / per interval / frame strips) and the frozen-stretch scan."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from . import media


def label(img: np.ndarray, text: str, scale: float = 0.45) -> np.ndarray:
    import cv2
    cv2.putText(img, text, (4, 14), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(img, text, (4, 14), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 255, 255), 1, cv2.LINE_AA)
    return img


def grab(path: str | Path, t: float, w: int, h: int) -> np.ndarray | None:
    return next(iter(media.frames(path, w, h, t0=max(0.0, t), dur=0.1)), None)


def tile(imgs: list[np.ndarray], cols: int) -> np.ndarray:
    h, w = imgs[0].shape[:2]
    rows = (len(imgs) + cols - 1) // cols
    sheet = np.zeros((rows * h, cols * w, 3), np.uint8)
    for i, im in enumerate(imgs):
        r, c = divmod(i, cols)
        sheet[r * h:(r + 1) * h, c * w:(c + 1) * w] = im
    return sheet


def sheet_times(path: str | Path, times: list[float], out: str | Path, cols: int = 8, tw: int = 240,
                labels: list[str] | None = None) -> Path:
    import cv2
    info = media.probe(path)
    th = int(round(tw * info["h"] / max(1, info["w"]))) // 2 * 2
    imgs = []
    for i, t in enumerate(times):
        im = grab(path, t, tw, th)
        if im is None:
            im = np.zeros((th, tw, 3), np.uint8)
        im = im.copy()
        label(im, labels[i] if labels else f"{t:.2f}s")
        imgs.append(im)
    cv2.imwrite(str(out), tile(imgs, cols), [cv2.IMWRITE_JPEG_QUALITY, 85])
    return Path(out)


def sheet_every(path: str | Path, out: str | Path, step: float = 1.0, cols: int = 10, tw: int = 192) -> Path:
    d = media.probe(path)["duration"]
    ts = list(np.arange(step / 2, d, step))
    return sheet_times(path, ts, out, cols=cols, tw=tw)


def strip(path: str | Path, t0: float, dur: float, out: str | Path, cols: int = 12, tw: int = 160) -> Path:
    """Every frame of [t0, t0 + dur) — for reading transitions/flashes frame by frame."""
    import cv2
    info = media.probe(path)
    th = int(round(tw * info["h"] / max(1, info["w"]))) // 2 * 2
    imgs = []
    for k, im in enumerate(media.frames(path, tw, th, t0=t0, dur=dur)):
        im = im.copy()
        label(im, f"{t0 + k / info['fps']:.2f}", 0.38)
        imgs.append(im)
    cv2.imwrite(str(out), tile(imgs, cols), [cv2.IMWRITE_JPEG_QUALITY, 85])
    return Path(out)


def frozen_stretches(pix_d: np.ndarray, fps: float, thresh: float = 0.6, min_s: float = 0.5) -> list[tuple[float, float]]:
    """Runs where the mean absolute frame difference (0..255 gray) stays below thresh for >= min_s.

    Returns [(start_s, length_s)]."""
    d = np.asarray(pix_d, np.float32)
    out = []
    i = 0
    n = len(d)
    while i < n:
        if d[i] < thresh:
            k = i
            while k < n and d[k] < thresh:
                k += 1
            if (k - i) / fps >= min_s:
                out.append((round(i / fps, 3), round((k - i) / fps, 3)))
            i = k
        else:
            i += 1
    return out


def scan_frozen(path: str | Path, thresh: float = 0.6, min_s: float = 0.5) -> list[tuple[float, float]]:
    import cv2  # noqa: F401 (frames are gray already)
    info = media.probe(path)
    prev = None
    d = []
    for g in media.frames(path, 160, 90 if info["w"] > info["h"] else 284, gray=True):
        if prev is not None:
            d.append(float(np.abs(g.astype(np.int16) - prev.astype(np.int16)).mean()))
        else:
            d.append(99.0)
        prev = g
    return frozen_stretches(np.array(d), info["fps"], thresh, min_s)
