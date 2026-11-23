# -*- coding: utf-8 -*-
"""Painting → 2.5D depth layers for Blender.

Each layer k is "everything at depth ≥ b_k, with the nearer layers cut out and inpainted":
  - color_k  = painting with the nearer region (dilated) inpainted, alpha = smoothstep at b_k (guided by the color),
  - depth_k  = layer's own depth, filled outward and under the nearer region, clamped to [b_k - m, b_{k+1} + m], blurred
              (so the mesh never stretches across a silhouette — the "rubber sheet" smear).
Layer 0 is the full background plate (alpha 1).

The mesh of a layer is a grid whose vertices lie on the camera rays of their pixels: seen from the world origin
(the "painting camera") the layer stack reproduces the painting exactly; moving the camera gives parallax.

python layers.py CONFIG.json     (see examples/test15.json)
Writes into OUT/<name>/: L<k>.png (RGBA), L<k>_mesh.npz (verts, uv, tris), anim.png (RGB masks), meta.json."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np


def smoothstep(a: float, b: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - a) / (b - a + 1e-9), 0, 1)
    return t * t * (3 - 2 * t)


def guided(I: np.ndarray, p: np.ndarray, r: int, eps: float) -> np.ndarray:
    """He et al. guided filter, grey guide I (0..1), input p (0..1)."""
    box = lambda x: cv2.boxFilter(x, -1, (2 * r + 1, 2 * r + 1))  # noqa: E731
    mI, mp = box(I), box(p)
    cov = box(I * p) - mI * mp
    var = box(I * I) - mI * mI
    a = cov / (var + eps)
    b = mp - a * mI
    return box(a) * I + box(b)


def inpaint(img: np.ndarray, hole: np.ndarray) -> np.ndarray:
    """Fill hole (bool) of an 8-bit BGR image: smooth push-pull base (no Telea streaks) + texture detail mirrored
    across the hole boundary (so foliage/sky keep their grain). Original kept outside the hole."""
    h, w = hole.shape
    valid = (~hole).astype(np.float32)
    f = img.astype(np.float32)
    base = np.dstack([fill_outside(f[..., c], valid) for c in range(3)])
    # mirror fill: p -> nearest valid q -> q + (q - p)
    dist, lab = cv2.distanceTransformWithLabels(hole.astype(np.uint8), cv2.DIST_L2, 5, labelType=cv2.DIST_LABEL_PIXEL)
    zy, zx = np.nonzero(~hole)
    lut_y = np.zeros(lab.max() + 1, np.float32); lut_x = np.zeros(lab.max() + 1, np.float32)
    zl = lab[zy, zx]
    lut_y[zl] = zy; lut_x[zl] = zx
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    qy, qx = lut_y[lab], lut_x[lab]
    ry, rx = np.clip(2 * qy - yy, 0, h - 1), np.clip(2 * qx - xx, 0, w - 1)
    refl = cv2.remap(f, rx, ry, cv2.INTER_LINEAR)
    detail = refl - cv2.GaussianBlur(refl, (0, 0), 5)
    k = np.clip(1.0 - dist / 160.0, 0.35, 1.0)[..., None]
    fill = base + detail * k
    m = cv2.GaussianBlur(hole.astype(np.float32), (0, 0), 1.5)[..., None]
    out = f * (1 - m) + fill * m
    return np.clip(out, 0, 255).astype(np.uint8)


def fill_outside(val: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Push-pull fill: values where valid, smooth extension elsewhere."""
    levels = []
    v, m = val * valid, valid.astype(np.float32)
    while min(v.shape) > 8:
        levels.append((v, m))
        v = cv2.pyrDown(v); m = cv2.pyrDown(m)
    f = np.where(m > 1e-4, v / np.maximum(m, 1e-4), float((val * valid).sum() / max(valid.sum(), 1)))
    for v, m in reversed(levels):
        up = cv2.resize(f, (v.shape[1], v.shape[0]), interpolation=cv2.INTER_LINEAR)
        w = np.clip(m * 4, 0, 1)
        f = np.where(m > 1e-4, v / np.maximum(m, 1e-4), up) * w + up * (1 - w)
    return f.astype(np.float32)


def grid_mesh(depth: np.ndarray, step: int, sigma: float, cu: float, cv: float, zref: float, alpha_d: float,
              alpha_map: np.ndarray) -> dict:
    """Vertices on camera rays: pixel (u, v) → P_ref = ((u-cu)σ, zref, -(v-cv)σ); P = P_ref * Zd/zref,
    Zd = zref * (1 - alpha_d * depth). Triangles fully transparent are dropped."""
    H, W = depth.shape
    us = np.arange(0, W + step - 1, step).clip(0, W - 1).astype(np.float32)
    vs = np.arange(0, H + step - 1, step).clip(0, H - 1).astype(np.float32)
    us[-1], vs[-1] = W - 1, H - 1
    U, V = np.meshgrid(us, vs)
    D = cv2.remap(depth, U, V, cv2.INTER_LINEAR)
    k = 1 - alpha_d * D
    X = (U + 0.5 - cu) * sigma * k
    Y = zref * k
    Z = -(V + 0.5 - cv) * sigma * k
    verts = np.stack([X, Y, Z], -1).reshape(-1, 3)
    uv = np.stack([(U + 0.5) / W, 1 - (V + 0.5) / H], -1).reshape(-1, 2)
    ny, nx = U.shape
    idx = np.arange(ny * nx).reshape(ny, nx)
    a, b, c, d = idx[:-1, :-1], idx[:-1, 1:], idx[1:, :-1], idx[1:, 1:]
    # drop quads with no alpha (max-pool of alpha over the quad)
    am = cv2.dilate(alpha_map, np.ones((step * 2 + 1, step * 2 + 1), np.uint8))
    A = cv2.remap(am, U, V, cv2.INTER_NEAREST)
    keep = (A[:-1, :-1] > 0.003) | (A[1:, 1:] > 0.003) | (A[:-1, 1:] > 0.003) | (A[1:, :-1] > 0.003)
    quads = np.stack([a, c, d, b], -1)[keep]
    return {"verts": verts.astype(np.float32), "uv": uv.astype(np.float32), "quads": quads.astype(np.int32)}


def poly_mask(shape, polys, blur: float) -> np.ndarray:
    m = np.zeros(shape, np.float32)
    for p in polys:
        cv2.fillPoly(m, [np.array(p, np.int32)], 1.0, cv2.LINE_AA)
    return cv2.GaussianBlur(m, (0, 0), blur) if blur > 0 else m


def build(cfg: dict, pcfg: dict, out_root: Path) -> dict:
    name = pcfg["name"]
    out = out_root / name; out.mkdir(parents=True, exist_ok=True)
    img = cv2.imread(pcfg["image"], cv2.IMREAD_COLOR)
    dep = np.load(pcfg["depth"]).astype(np.float32)
    m = pcfg.get("crop_margin", 0)
    if m:
        img, dep = img[m:-m, m:-m], dep[m:-m, m:-m]
    W = pcfg["work_width"]
    H = int(round(img.shape[0] * W / img.shape[1]))
    s = W / img.shape[1]
    img = cv2.resize(img, (W, H), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
    dep = cv2.resize(dep, (W, H), interpolation=cv2.INTER_LINEAR)
    dep = cv2.bilateralFilter(dep, 7, 0.08, 5)
    cv2.imwrite(str(out / "flat.png"), img)
    grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255
    # border of the depth map is unreliable (canvas edge) — replicate from inside
    bb = max(4, int(0.015 * W))
    dep = cv2.copyMakeBorder(dep[bb:-bb, bb:-bb], bb, bb, bb, bb, cv2.BORDER_REPLICATE)
    # pad the world beyond the canvas edge (mirrored) so a turning camera never sees the end of the world
    W0, H0 = W, H
    px, py = int(pcfg.get("pad", [0.3, 0.15])[0] * W0), int(pcfg.get("pad", [0.3, 0.15])[1] * H0)
    img = cv2.copyMakeBorder(img, py, py, px, px, cv2.BORDER_REFLECT_101)
    dep = cv2.copyMakeBorder(dep, py, py, px, px, cv2.BORDER_REFLECT_101)
    H, W = dep.shape
    grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255
    ps = pcfg.get("poly_scale", 1.0)   # polygons are drawn on a preview: src = preview * ps - margin
    to_w = lambda poly: [((x * ps - m) * s + px, (y * ps - m) * s + py) for x, y in poly]  # noqa: E731
    f = pcfg.get("feather", 0.04)
    mg = pcfg.get("margin", 0.06)
    specs = pcfg["layers"]                 # [{name}, {name, depth_min, poly?, relief_gain?}, ...] far → near
    K = len(specs)
    regions = [np.ones((H, W), np.float32)]
    for sp in specs[1:]:
        r = smoothstep(sp["depth_min"] - f, sp["depth_min"] + f, dep)
        if sp.get("poly"):
            pm = poly_mask((H, W), [to_w(p) for p in sp["poly"]], sp.get("poly_blur", 2) * ps * s)
            r = r * pm
        r = np.clip(guided(grey, r, 4, 1e-3), 0, 1)
        regions.append(np.clip((r - 0.04) / 0.92, 0, 1).astype(np.float32))
    # mirrored copies of the subjects in the pad must not survive in the depth-sliced layers
    subj = np.zeros((H, W), np.float32)
    for k, sp in enumerate(specs):
        if sp.get("poly"):
            subj = np.maximum(subj, regions[k])
    core = subj[py:py + H0, px:px + W0]
    subj_ref = cv2.copyMakeBorder(core, py, py, px, px, cv2.BORDER_REFLECT_101)
    subj_ref[py:py + H0, px:px + W0] = 0
    subj_ref = cv2.dilate((subj_ref > 0.2).astype(np.uint8), np.ones((25, 25), np.uint8))
    for k, sp in enumerate(specs):
        if k > 0 and not sp.get("poly"):
            regions[k] = regions[k] * (1 - cv2.GaussianBlur(subj_ref.astype(np.float32), (0, 0), 4))
    alphas = [None] * K
    acc = np.zeros((H, W), np.float32)
    for k in range(K - 1, -1, -1):
        acc = np.maximum(acc, regions[k])
        alphas[k] = acc.copy()
    b = [sp.get("depth_min", 0.0) for sp in specs]
    # crop for the switch view
    # the "switch view": what the main camera sees at the moment it passes through the canvas — a 9:16 window
    # slightly inside the canvas (crop_h_frac of its height) so the frame's lip is not in shot
    crop_w = H0 * pcfg.get("crop_h_frac", 0.94) * 9 / 16
    cu = pcfg.get("crop_cx", W0 / 2 / s) * s          # unpadded painting px
    cv_ = pcfg.get("crop_cy", H0 / 2 / s) * s
    sigma = cfg["view_width_at_zref"] / crop_w
    zref, ad = cfg["zref"], pcfg.get("depth_scale", 0.5)
    np.save(out / "depth.npy", dep.astype(np.float16))
    layers, geo = [], []
    grid = pcfg.get("grid_step", 6)
    for k in range(K):
        sp = specs[k]
        nearer = alphas[k + 1] if k + 1 < K else np.zeros((H, W), np.float32)
        hole = cv2.dilate((nearer > 0.35).astype(np.uint8), np.ones((2 * pcfg.get("hole_dilate", 8) + 1,) * 2, np.uint8)) > 0
        hole |= subj_ref > 0
        col = inpaint(img, hole) if hole.any() else img.copy()
        own = (regions[k] > 0.5) & ~hole
        if not own.any():
            own = ~hole
        lo = np.percentile(dep[own], 2) if k > 0 else 0.0
        hi = np.percentile(dep[own], 98)
        if k > 0 and not sp.get("poly"):
            lo = max(lo, b[k] - mg)
        if k + 1 < K and not specs[k + 1].get("poly"):
            hi = min(hi, b[k + 1] + mg)
        dk = fill_outside(np.clip(dep, lo, hi), own.astype(np.float32))
        dk = np.clip(dk, lo, hi)
        if sp.get("relief_gain") is not None:
            med = float(np.median(dep[own]))
            dk = med + (dk - med) * sp["relief_gain"]
        dk = cv2.GaussianBlur(dk, (0, 0), pcfg.get("relief_blur", 6) * (2 if sp.get("poly") else 1))
        a8 = (alphas[k] * 255).astype(np.uint8)
        rgba = np.dstack([col, a8])
        cv2.imwrite(str(out / f"L{k}.png"), rgba)
        geo.append(dk)
        layers.append({"k": k, "name": sp["name"], "png": str(out / f"L{k}.png"), "mesh": str(out / f"L{k}_mesh.npz"),
                       "range": [float(lo), float(hi)]})
        print(f"  {name} L{k} {sp['name']}: hole {hole.mean():.2%}, depth {lo:.2f}..{hi:.2f}", flush=True)
    # depth order: wherever a nearer layer is present, every farther layer stays behind it (else a subject with
    # compressed relief sinks behind its own ground — e.g. the Storm feet vanished)
    env = np.full((H, W), 9.0, np.float32)
    gap = pcfg.get("order_gap", 0.03)
    for k in range(K - 1, -1, -1):
        if k < K - 1:
            near = cv2.dilate((alphas[k + 1] > 0.02).astype(np.uint8), np.ones((2 * grid + 1,) * 2, np.uint8)) > 0
            geo[k] = np.where(near, np.minimum(geo[k], env - gap), geo[k])
            geo[k] = cv2.GaussianBlur(geo[k], (0, 0), 1.5)
            geo[k] = np.where(near, np.minimum(geo[k], env - gap * 0.5), geo[k])
        env = np.where(alphas[k] > 0.02, np.minimum(env, geo[k]), env)
    for k in range(K):
        mesh = grid_mesh(geo[k], grid, sigma, cu + px, cv_ + py, zref, ad, alphas[k])
        np.savez_compressed(out / f"L{k}_mesh.npz", **mesh)
        np.save(out / f"L{k}_depth.npy", geo[k].astype(np.float16))
    # animation masks (R: sway/rotate region, G: flutter region, B: soft light/glow)
    anim = np.zeros((H, W, 3), np.float32)
    for ch, key in enumerate(("mask_r", "mask_g", "mask_b")):
        spec = pcfg.get(key)
        if not spec:
            continue
        mm = poly_mask((H, W), [to_w(p) for p in spec["polys"]], spec.get("blur", 20) * ps * s)
        if spec.get("depth_min") is not None:
            mm *= cv2.GaussianBlur(smoothstep(spec["depth_min"] - 0.05, spec["depth_min"] + 0.05, dep), (0, 0), 6)
        if spec.get("hue"):
            hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.float32)
            h0, h1, smin = spec["hue"]
            hm = ((hsv[..., 0] >= h0) & (hsv[..., 0] <= h1) & (hsv[..., 1] >= smin)).astype(np.float32)
            hm = cv2.GaussianBlur(cv2.dilate(hm, np.ones((9, 9), np.uint8)), (0, 0), 10)
            mm *= np.clip(hm * 1.5, 0, 1)
        if spec.get("dilate"):
            r = max(1, int(spec["dilate"] * pcfg.get("poly_scale", 1.0) * s))
            mm = cv2.GaussianBlur(cv2.dilate(mm, np.ones((2 * r + 1, 2 * r + 1), np.uint8)), (0, 0), r * 0.6)
        anim[..., ch] = np.clip(mm, 0, 1)
    cv2.imwrite(str(out / "anim.png"), (anim[..., ::-1] * 255).astype(np.uint8))
    meta = {"name": name, "W": W0, "H": H0, "Wp": W, "Hp": H, "pad": [px, py], "sigma": sigma, "cu": cu, "cv": cv_, "zref": zref, "depth_scale": ad,
            "crop_w": crop_w, "layers": layers, "flat": str(out / "flat.png"), "anim": str(out / "anim.png"),
            "scale_from_src": s, "crop_margin": m,
            # painting rect at zref (world units), for the portal camera and for placing things
            "rect": [(0 - cu) * sigma, (W0 - cu) * sigma, -(H0 - cv_) * sigma, -(0 - cv_) * sigma]}
    json.dump(meta, open(out / "meta.json", "w"), indent=1)
    return meta


if __name__ == "__main__":
    cfg = json.load(open(sys.argv[1], encoding="utf-8"))
    out_root = Path(cfg["out"])
    only = sys.argv[2].split(",") if len(sys.argv) > 2 else None
    for p in cfg["paintings"]:
        if only and p["name"] not in only:
            continue
        build(cfg, p, out_root)
