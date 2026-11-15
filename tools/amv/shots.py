# -*- coding: utf-8 -*-
"""Shot detection and per-shot visual-interest scoring.

scan_video()  — one pass over a video at a small size: per-frame colour-histogram distance (cuts), luma/contrast/
                saturation, DIS optical flow split into camera motion (affine: pan, zoom, rotation) and the residual
                (characters moving), plus where that residual motion is (centroid, for 9:16 reframing).
detect_cuts() — adaptive threshold on the histogram distance (PySceneDetect «adaptive» idea, no dependency).
shots_from()  — cut list → shots with interest score, action peaks and a reframe centre.
Pure functions (detect_cuts, flash_frames, shot_features, interest) work on arrays and are unit-tested."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

AW, AH = 192, 108  # analysis size (16:9); other aspects keep AW and scale AH


def hsv_hist(bgr: np.ndarray) -> np.ndarray:
    import cv2
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h = cv2.calcHist([hsv], [0], None, [24], [0, 180]).ravel()
    s = cv2.calcHist([hsv], [1], None, [8], [0, 256]).ravel()
    v = cv2.calcHist([hsv], [2], None, [12], [0, 256]).ravel()
    out = np.concatenate([h / (h.sum() + 1e-9), s / (s.sum() + 1e-9), v / (v.sum() + 1e-9)])
    return out.astype(np.float32)


def hist_dist(a: np.ndarray, b: np.ndarray) -> float:
    """0 (same) .. 1 (disjoint), averaged over the H, S, V blocks."""
    return float(0.5 * np.abs(a - b).sum() / 3.0)


def affine_from_flow(flow: np.ndarray, step: int = 4) -> tuple[np.ndarray, np.ndarray]:
    """Least-squares affine motion of a dense flow field. Returns (params [tx, ty, zoom, rot], residual magnitude map).

    Coordinates are centred and in pixels; zoom/rot are per-frame relative (0.01 = 1 % scale change)."""
    h, w = flow.shape[:2]
    ys, xs = np.mgrid[0:h:step, 0:w:step]
    x = (xs - w / 2).ravel().astype(np.float32)
    y = (ys - h / 2).ravel().astype(np.float32)
    u = flow[0:h:step, 0:w:step, 0].ravel()
    v = flow[0:h:step, 0:w:step, 1].ravel()
    A = np.stack([np.ones_like(x), x, y], 1)
    pu, *_ = np.linalg.lstsq(A, u, rcond=None)
    pv, *_ = np.linalg.lstsq(A, v, rcond=None)
    zoom = (pu[1] + pv[2]) / 2
    rot = (pv[1] - pu[2]) / 2
    yy, xx = np.mgrid[0:h, 0:w]
    xx = (xx - w / 2).astype(np.float32)
    yy = (yy - h / 2).astype(np.float32)
    pred_u = pu[0] + pu[1] * xx + pu[2] * yy
    pred_v = pv[0] + pv[1] * xx + pv[2] * yy
    res = np.hypot(flow[..., 0] - pred_u, flow[..., 1] - pred_v)
    return np.array([pu[0], pv[0], zoom, rot], np.float32), res


def scan_video(path: str | Path, fps: float | None = None, t0: float = 0.0, dur: float | None = None,
               w: int = AW, h: int | None = None, progress: bool = False) -> dict:
    """Per-frame features of a video (arrays of equal length; flow values are fractions of the frame width)."""
    import cv2
    from . import media

    info = media.probe(path)
    if h is None:
        h = max(16, int(round(w * info["h"] / max(1, info["w"]) / 2)) * 2)
    rate = fps or info["fps"] or 24.0
    dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_FAST)
    feats = {k: [] for k in ("hist_d", "pix_d", "luma", "contrast", "sat", "tx", "ty", "zoom", "rot",
                             "local", "cx", "cy", "white", "black")}
    prev_h = prev_g = None
    n = 0
    for img in media.frames(path, w, h, fps=rate if fps else None, t0=t0, dur=dur):
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        hh = hsv_hist(img)
        hsv_s = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)[..., 1]
        feats["luma"].append(float(g.mean()))
        feats["contrast"].append(float(g.std()))
        feats["sat"].append(float(hsv_s.mean()))
        feats["white"].append(float((g > 235).mean()))
        feats["black"].append(float((g < 18).mean()))
        if prev_g is None:
            for k in ("hist_d", "pix_d", "tx", "ty", "zoom", "rot", "local"):
                feats[k].append(0.0)
            feats["cx"].append(0.5)
            feats["cy"].append(0.5)
        else:
            feats["hist_d"].append(hist_dist(hh, prev_h))
            feats["pix_d"].append(float(np.abs(g.astype(np.int16) - prev_g.astype(np.int16)).mean()))
            flow = dis.calc(prev_g, g, None)
            p, res = affine_from_flow(flow)
            feats["tx"].append(float(p[0] / w))
            feats["ty"].append(float(p[1] / w))
            feats["zoom"].append(float(p[2]))
            feats["rot"].append(float(p[3]))
            feats["local"].append(float(res.mean() / w))
            wsum = float(res.sum())
            if wsum > 1e-6:
                yy, xx = np.mgrid[0:h, 0:w]
                feats["cx"].append(float((res * xx).sum() / wsum / w))
                feats["cy"].append(float((res * yy).sum() / wsum / h))
            else:
                feats["cx"].append(0.5)
                feats["cy"].append(0.5)
        prev_h, prev_g = hh, g
        n += 1
        if progress and n % 2000 == 0:
            print(f"  {Path(path).name}: {n} frames", flush=True)
    out = {k: np.array(v, np.float32) for k, v in feats.items()}
    out["fps"] = np.float32(rate)
    out["t0"] = np.float32(t0)
    return out


def detect_cuts(hist_d: np.ndarray, fps: float, abs_min: float = 0.18, rel: float = 3.0, win_s: float = 1.0,
                min_shot_s: float = 0.25, strong: float = 0.55) -> list[int]:
    """Frame indices i where a new shot starts (between frame i-1 and i).

    A cut is a local maximum of the histogram distance that is above abs_min and rel x the median of its
    neighbourhood (without itself); a very strong jump (>= strong) always counts. Shots shorter than min_shot_s are
    merged into the previous one (the stronger boundary wins)."""
    d = np.asarray(hist_d, np.float32)
    n = len(d)
    r = max(2, int(win_s * fps / 2))
    cand = []
    for i in range(1, n):
        lo, hi = max(1, i - r), min(n, i + r + 1)
        if d[i] < d[max(1, i - 1):min(n, i + 2)].max():
            continue
        nb = np.concatenate([d[lo:i], d[i + 1:hi]])
        med = float(np.median(nb)) if nb.size else 0.0
        if d[i] >= strong or (d[i] >= abs_min and d[i] >= rel * max(med, 0.02)):
            cand.append(i)
    min_len = max(1, int(round(min_shot_s * fps)))
    cuts: list[int] = []
    for i in cand:
        if cuts and i - cuts[-1] < min_len:
            if d[i] > d[cuts[-1]]:
                cuts[-1] = i
            continue
        cuts.append(i)
    return cuts


def flash_frames(luma: np.ndarray, jump: float = 45.0, white: float = 200.0, max_len: int = 4) -> list[tuple[int, int]]:
    """Short luma spikes: frames that jump up by >= jump (or exceed white) and come back within max_len frames.

    Returns [(first_frame, length)]."""
    L = np.asarray(luma, np.float32)
    out = []
    i = 1
    while i < len(L):
        base = L[i - 1]
        if L[i] - base >= jump or (L[i] >= white and base < white - 20):
            k = i
            while k < len(L) and k - i < max_len and (L[k] - base >= jump * 0.5 or L[k] >= white):
                k += 1
            if k < len(L) and k - i < max_len + 1 and L[k] - base < jump * 0.6:
                out.append((i, k - i))
                i = k
                continue
        i += 1
    return out


def smooth(v: np.ndarray, n: int) -> np.ndarray:
    v = np.asarray(v, np.float32)
    if n <= 1 or v.size < 2:
        return v.copy()
    k = np.hanning(n + 2)[1:-1]
    k /= k.sum()
    p = n // 2
    return np.convolve(np.pad(v, (p, n - 1 - p), mode="edge"), k, mode="valid")


def shot_features(F: dict, a: int, b: int) -> dict:
    """Stats of frames [a, b) of a scan (see scan_video). Flow values per second (fractions of width / s)."""
    fps = float(F["fps"])
    sl = slice(a + 1, b) if b - a > 2 else slice(a, b)   # the first frame's flow crosses the cut
    loc = F["local"][sl] * fps
    cam = np.hypot(F["tx"][sl], F["ty"][sl]) * fps
    zoom = F["zoom"][sl] * fps
    sm = smooth(loc, max(1, int(fps * 0.25)))
    pk = int(np.argmax(sm)) + sl.start if sm.size else a
    q = np.percentile(sm, [10, 90]) if sm.size else np.array([0.0, 0.0])
    cx = F["cx"][sl]
    wts = loc + 1e-6
    return {
        "a": int(a), "b": int(b), "dur": round((b - a) / fps, 3),
        "luma": round(float(F["luma"][a:b].mean()), 1), "contrast": round(float(F["contrast"][a:b].mean()), 1),
        "sat": round(float(F["sat"][a:b].mean()), 1),
        "motion": round(float(loc.mean()) if loc.size else 0.0, 4),
        "motion_p90": round(float(q[1]), 4), "motion_p10": round(float(q[0]), 4),
        "camera": round(float(cam.mean()) if cam.size else 0.0, 4),
        "zoom": round(float(np.abs(zoom).mean()) if zoom.size else 0.0, 4),
        "peak": int(pk), "peak_val": round(float(sm.max()) if sm.size else 0.0, 4),
        "cx": round(float((cx * wts).sum() / wts.sum()) if cx.size else 0.5, 3),
        "white": round(float(F["white"][a:b].mean()), 3), "black": round(float(F["black"][a:b].mean()), 3),
    }


def interest(s: dict, faces: float = 0.0) -> float:
    """Visual-interest score 0..~1: character motion and its peak dominate; camera motion, contrast, saturation
    and visible faces add; near-black or washed-out shots and very short ones lose."""
    m = 1 - math.exp(-s["motion"] / 0.06)
    pk = 1 - math.exp(-s["peak_val"] / 0.10)
    cam = 1 - math.exp(-s["camera"] / 0.25)
    con = min(1.0, s["contrast"] / 60.0)
    sat = min(1.0, s["sat"] / 120.0)
    sc = 0.32 * m + 0.22 * pk + 0.12 * cam + 0.14 * con + 0.08 * sat + 0.12 * min(1.0, faces)
    if s["luma"] < 25 or s["black"] > 0.6:
        sc *= 0.35
    if s["white"] > 0.5:
        sc *= 0.5
    if s["dur"] < 0.5:
        sc *= 0.6
    return round(float(sc), 4)


def count_faces(path: str | Path, ts: list[float]) -> float:
    """Mean number of frontal faces (Haar, OpenCV bundled) on a few frames — weak on stylized CG, still a hint."""
    import cv2
    from . import media
    casc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    n = []
    for t in ts:
        fr = next(iter(media.frames(path, 480, 270, t0=max(0.0, t), dur=0.2)), None)
        if fr is None:
            continue
        g = cv2.equalizeHist(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY))
        n.append(len(casc.detectMultiScale(g, 1.15, 5, minSize=(24, 24))))
    return float(np.mean(n)) if n else 0.0


def shots_from(F: dict, cuts: list[int], src: str = "", faces: bool = False, path: str | Path | None = None,
               min_dur: float = 0.0) -> list[dict]:
    fps = float(F["fps"])
    t0 = float(F["t0"])
    n = len(F["luma"])
    edges = [0] + [c for c in cuts if 0 < c < n] + [n]
    out = []
    for a, b in zip(edges[:-1], edges[1:]):
        if (b - a) / fps < min_dur:
            continue
        s = shot_features(F, a, b)
        s["src"] = src
        s["t_in"] = round(t0 + a / fps, 4)
        s["t_out"] = round(t0 + b / fps, 4)
        s["t_peak"] = round(t0 + s["peak"] / fps, 4)
        fc = 0.0
        if faces and path is not None:
            fc = count_faces(path, [s["t_in"] + s["dur"] * q for q in (0.25, 0.5, 0.75)])
        s["faces"] = round(fc, 2)
        s["score"] = interest(s, fc)
        out.append(s)
    return out


def motion_curve(F: dict, a: int, b: int) -> list[float]:
    """Smoothed local+camera motion per frame of a shot (fractions of width / s), for ramp placement."""
    fps = float(F["fps"])
    v = (F["local"][a:b] + 0.5 * np.hypot(F["tx"][a:b], F["ty"][a:b])) * fps
    if b - a > 1:
        v[0] = v[1]
    return [round(float(x), 4) for x in smooth(v, max(1, int(fps * 0.2)))]
