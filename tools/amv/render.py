# -*- coding: utf-8 -*-
"""Plan → video. Per segment: read the needed source range (cropped to the output aspect around the shot's
motion centre, letterbox removed), resample it along the velocity curve (motion-blur averaging when fast,
optical-flow in-betweens when slow so nothing freezes), grade, then punch/shake/whip/flash/chroma per frame,
optional captions, and pipe to the encoder with the song section."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from . import fx, media


def crop_box(box: tuple[int, int, int, int], aspect: float, cx: float, cy: float = 0.45) -> tuple[int, int, int, int]:
    """Largest window of the given aspect (w/h) inside the content box, centred on (cx, cy) (0..1 of the box)."""
    x, y, w, h = box
    if w / h > aspect:
        cw, ch = int(round(h * aspect)) // 2 * 2, h
    else:
        cw, ch = w, int(round(w / aspect)) // 2 * 2
    px = int(round(x + cx * w - cw / 2))
    py = int(round(y + cy * h - ch / 2))
    px = min(max(px, x), x + w - cw)
    py = min(max(py, y), y + h - ch)
    return cw, ch, px, py


class FlowInterp:
    """Optical-flow in-between frames (DIS, computed at half size)."""

    def __init__(self):
        import cv2
        self.cv2 = cv2
        self.dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_FAST)
        self.cache: dict = {}

    def flows(self, i: int, a: np.ndarray, b: np.ndarray):
        if i not in self.cache:
            cv2 = self.cv2
            h, w = a.shape[:2]
            ga = cv2.cvtColor(cv2.resize(a, (w // 2, h // 2)), cv2.COLOR_BGR2GRAY)
            gb = cv2.cvtColor(cv2.resize(b, (w // 2, h // 2)), cv2.COLOR_BGR2GRAY)
            f01 = cv2.resize(self.dis.calc(ga, gb, None), (w, h)) * 2
            f10 = cv2.resize(self.dis.calc(gb, ga, None), (w, h)) * 2
            self.cache = {i: (f01, f10)}
        return self.cache[i]

    def mid(self, i: int, a: np.ndarray, b: np.ndarray, t: float) -> np.ndarray:
        cv2 = self.cv2
        h, w = a.shape[:2]
        f01, f10 = self.flows(i, a, b)
        if (h, w) not in getattr(self, "grid", {}):
            yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
            self.grid = {(h, w): (xx, yy)}
        xx, yy = self.grid[(h, w)]
        # Super SloMo approximation of the intermediate flows
        ft0 = -(1 - t) * t * f01 + t * t * f10
        ft1 = (1 - t) * (1 - t) * f01 - t * (1 - t) * f10
        w0 = cv2.remap(a, xx + ft0[..., 0], yy + ft0[..., 1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        w1 = cv2.remap(b, xx + ft1[..., 0], yy + ft1[..., 1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        return cv2.addWeighted(w0, 1 - t, w1, t, 0)


def sample(frames: list[np.ndarray], pos: float, step: float, interp: FlowInterp | None) -> np.ndarray:
    """Source image at fractional frame position pos; step = source frames per output frame."""
    n = len(frames)
    pos = min(max(pos, 0.0), n - 1.0)
    if step > 1.8:   # fast: a light velocity blur — the centre frame plus its neighbours at half weight
        i = int(round(pos))
        lo, hi = max(0, i - 1), min(n - 1, i + 1)
        if hi > lo:
            acc = frames[i].astype(np.float32) * 2 + frames[lo] + frames[hi]
            return (acc / 4).astype(np.uint8)
    i = int(math.floor(pos))
    f = pos - i
    if i >= n - 1 or f < 0.12:
        return frames[min(i, n - 1)]
    if f > 0.88:
        return frames[i + 1]
    if interp is not None and step < 0.9:
        return interp.mid(i, frames[i], frames[i + 1], f)
    import cv2
    return cv2.addWeighted(frames[i], 1 - f, frames[i + 1], f, 0)


def render(plan: dict, boxes: dict, song: str | Path, out: str | Path, W: int, H: int, fps: float = 30.0,
           captions=None, sat: float = 1.18, afade: tuple[float, float] = (0.0, 0.5), progress: bool = True,
           crf: int = 18, vfade: tuple[float, float] = (0.0, 0.0)) -> dict:
    """boxes: {src_path: (x, y, w, h) content box}. Returns render log (frames per segment, source times)."""
    import cv2
    T0, T1 = plan["t0"], plan["t1"]
    n_total = int(round((T1 - T0) * fps))
    enc = media.Encoder(out, W, H, fps, audio=song, a_t0=T0, a_dur=T1 - T0, crf=crf, afade=afade)
    log = []
    aspect = W / H
    k = 0
    interp = FlowInterp()
    black = np.zeros((H, W, 3), np.uint8)
    for si, seg in enumerate(plan["segments"]):
        k_end = n_total if si == len(plan["segments"]) - 1 else int(round((seg["t1"] - T0) * fps))
        ts = [T0 + (kk + 0.5) / fps for kk in range(k, k_end)]
        if not ts:
            continue
        if seg.get("clip") is None:
            for _ in ts:
                enc.write(black)
            k = k_end
            continue
        src = seg["src"]
        info = media.probe(src)
        sfps = info["fps"] or 24.0
        s_list = [fx.src_time(t, seg) for t in ts]
        r_list = [fx.rate_at(t, seg["ramp"]) for t in ts]
        f0 = max(0, int(math.floor(min(s_list) * sfps)) - 2)
        f1 = int(math.ceil(max(s_list) * sfps)) + 3
        box = tuple(boxes.get(src) or (0, 0, info["w"], info["h"]))
        cx = seg.get("cx_916" if H > W else "cx_169", seg.get("cx", 0.5))
        cw, ch, px, py = crop_box(box, aspect, cx)
        frames = media.read_clip(src, (f0 - 0.25) / sfps, (f1 - f0 + 0.5) / sfps, W, H,
                                 vf_pre=f"crop={cw}:{ch}:{px}:{py}", flags="lanczos")
        if not frames:
            for _ in ts:
                enc.write(black)
            k = k_end
            continue
        lut = fx.grade_lut(fx.frame_stats(frames[len(frames) // 2]))
        interp.cache = {}
        for t, s, r in zip(ts, s_list, r_list):
            img = sample(frames, s * sfps - f0, r * sfps / fps, interp)
            img = cv2.LUT(img, lut)
            img = fx.saturate(img, sat)
            sc = fx.punch_scale(t, plan["punches"])
            dx, dy, rot = fx.shake_offset(t, plan["shakes"])
            wa, wd = fx.whip_amount(t, plan["whips"], fps)
            img = fx.affine(img, sc * (1.0 + 0.02 * (abs(dx) + abs(dy) > 0)), dx, dy, rot)
            if wa > 0:
                img = fx.whip(img, wa, wd)
            cs = (sc - 1.0) * 70 * W / 1280
            img = fx.chroma_split(img, cs)
            img = fx.vignette(img, 0.32)
            fa, fc = fx.flash_alpha(t, plan["flashes"], fps)
            img = fx.flash(img, fa, fc)
            if captions is not None:
                img = captions.draw(img, t)
            fade = 1.0
            if vfade[0] > 0 and t - T0 < vfade[0]:
                fade = (t - T0) / vfade[0]
            if vfade[1] > 0 and T1 - t < vfade[1]:
                fade = min(fade, (T1 - t) / vfade[1])
            if fade < 1.0:
                img = (img.astype(np.float32) * max(0.0, fade)).astype(np.uint8)
            enc.write(img)
        log.append({"seg": si, "frames": len(ts), "src": Path(src).name, "s0": round(s_list[0], 3),
                    "s1": round(s_list[-1], 3), "rate_min": round(min(r_list), 2), "rate_max": round(max(r_list), 2)})
        if progress and si % 10 == 0:
            print(f"  seg {si}/{len(plan['segments'])}  t={ts[0]:.1f}", flush=True)
        k = k_end
        del frames
    enc.close()
    return {"frames": k, "segments": log}
