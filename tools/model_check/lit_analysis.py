# -*- coding: utf-8 -*-
"""Light-orbit shadow metrics (numpy only): how clean is the cel shadow on a face when the key light goes round it.

Input per frame (from light_orbit.py): `lit` (0..1, the material's lit factor, AOV npr_lit), the region code map
(face skin / face decal / other parts) and optionally `zone` (the scripted "always shadow" weight).
The face region is face skin plus the decals on it (eyes, brows, mouth): decal pixels are unlit by design, so their
lit/shadow value is filled in from the nearest face-skin pixels (they would otherwise cut a shadow band in two).

Metrics of one frame (shadow S = region & lit < 0.5):
  components      8-connected shadow components
  tiny_share      share of those components smaller than `tiny` (default 0.1 %) of the face area
  tiny_area       share of the shadow area that sits in tiny components
  perim_ratio     total contour length of all components / perimeter of the convex hull of the shadow
                  (sub-pixel marching-squares length; 1.0 = one convex blob, a ragged or speckled shadow is far above)
Across frames:
  iou             IoU of the shadow of an expression vs the neutral face at the same light angle (common region; also
                  with the eye / mouth zone left out)
  nesting         as the light goes from the front to the back the shadow may only grow (0..180 deg), on the way back
                  only shrink: a pair of neighbouring angles is nested when < `nest_tol` of the face breaks the rule
"""
import numpy as np

try:
    from . import analysis as A
except (ImportError, ValueError):                      # run as a script / from Blender
    import analysis as A  # noqa: E402

FACE_ID = 255
DECAL_ID = 128


# ----------------------------------------------------------------------------- region / fill
def regions(code):
    """region code map (uint8 0..255) -> (face skin, decal) boolean masks"""
    c = np.asarray(code)
    return c == FACE_ID, c == DECAL_ID


def box_count(m, r=1):
    """number of True pixels in the (2r+1)^2 window (zero padding)"""
    a = np.pad(m.astype(np.int32), r)
    c = np.cumsum(np.cumsum(a, 0), 1)
    c = np.pad(c, ((1, 0), (1, 0)))
    k = 2 * r + 1
    H, W = m.shape
    return c[k:k + H, k:k + W] - c[0:H, k:k + W] - c[k:k + H, 0:W] + c[0:H, 0:W]


def fill_from_neighbours(value, known, todo, max_iter=200):
    """value (bool) is defined on `known`; give every `todo` pixel the majority value of the known pixels next to it,
    growing inwards ring by ring. Returns (filled value, filled known mask)."""
    v = np.asarray(value, bool) & known
    k = np.asarray(known, bool).copy()
    todo = np.asarray(todo, bool) & ~k
    for _ in range(max_iter):
        if not todo.any():
            break
        nk = box_count(k)
        ring = todo & (nk > 0)
        if not ring.any():
            break
        nv = box_count(v & k)
        v = np.where(ring, nv * 2 >= nk, v)
        k = k | ring
        todo = todo & ~ring
    return v, k


def face_shadow(lit, code, thr=0.5):
    """-> (shadow mask over the face region incl. decals, region mask, decal mask)"""
    face, decal = regions(code)
    sh = face & (np.asarray(lit) < thr)
    sh, reg = fill_from_neighbours(sh, face, decal)
    return sh & reg, reg, decal


# ----------------------------------------------------------------------------- shape measures
def _soften(m, c=0.6):
    """centre weight c, the rest over the 4 neighbours: rounds the pixel staircase, a single pixel survives"""
    m = m.astype(np.float64)
    p = np.pad(m, 1)
    k = (1.0 - c) / 4.0
    return c * m + k * (p[:-2, 1:-1] + p[2:, 1:-1] + p[1:-1, :-2] + p[1:-1, 2:])


def contour_length(mask, t=0.5):
    """sub-pixel marching-squares contour length of a boolean mask (pixel units; the image border counts as outside).
    The mask is softened a little first, so a pixel staircase along a slanted edge is not counted as zig-zag
    (a disc comes out ~3 % long, an axis-aligned square exact)."""
    f = np.pad(_soften(np.asarray(mask, bool)), 1)
    a, b, c, d = f[:-1, :-1], f[:-1, 1:], f[1:, 1:], f[1:, :-1]          # corners tl, tr, br, bl of every cell

    def cross(p, q, P, Q):
        s = (p > t) != (q > t)
        w = np.where(s, (t - p) / np.where(s, q - p, 1.0), 0.0)
        return P[0] + (Q[0] - P[0]) * w, P[1] + (Q[1] - P[1]) * w, s

    E = [cross(a, b, (0, 0), (0, 1)), cross(b, c, (0, 1), (1, 1)), cross(d, c, (1, 0), (1, 1)), cross(a, d, (0, 0), (1, 0))]
    ys = np.stack([e[0] for e in E]); xs = np.stack([e[1] for e in E]); s = np.stack([e[2] for e in E])
    n = s.sum(0)
    idx = np.argsort(~s, axis=0, kind='stable')[:2]
    y0 = np.take_along_axis(ys, idx[:1], 0)[0]; x0 = np.take_along_axis(xs, idx[:1], 0)[0]
    y1 = np.take_along_axis(ys, idx[1:2], 0)[0]; x1 = np.take_along_axis(xs, idx[1:2], 0)[0]
    L = np.where(n == 2, np.hypot(y1 - y0, x1 - x0), 0.0)
    L4 = np.hypot(ys[0] - ys[3], xs[0] - xs[3]) + np.hypot(ys[1] - ys[2], xs[1] - xs[2])
    return float(np.where(n == 4, L4, L).sum())


def contour_points(mask):
    """the marching-squares contour vertices (midpoints between an inside and an outside pixel), (N, 2) as (y, x)"""
    m = np.pad(np.asarray(mask, bool), 1)
    pts = []
    h = m[:, :-1] != m[:, 1:]                     # horizontal neighbours differ -> vertex between them
    ys, xs = np.nonzero(h)
    pts.append(np.stack([ys - 1.0, xs - 0.5], 1))
    v = m[:-1, :] != m[1:, :]
    ys, xs = np.nonzero(v)
    pts.append(np.stack([ys - 0.5, xs - 1.0], 1))
    return np.concatenate(pts, 0) if pts else np.zeros((0, 2))


def row_extremes(points):
    """only the left- and rightmost point of every row: the same convex hull, far fewer points"""
    p = np.asarray(points, np.float64)
    if len(p) < 3:
        return p
    o = np.lexsort((p[:, 1], p[:, 0]))
    p = p[o]
    first = np.r_[True, p[1:, 0] != p[:-1, 0]]
    last = np.r_[p[1:, 0] != p[:-1, 0], True]
    return p[first | last]


def hull_perimeter(points):
    """perimeter of the convex hull (monotone chain) of (N, 2) points"""
    p = np.unique(np.asarray(points, np.float64), axis=0)
    if len(p) < 2:
        return 0.0
    if len(p) == 2:
        return 2.0 * float(np.hypot(*(p[1] - p[0])))
    p = p[np.lexsort((p[:, 1], p[:, 0]))]

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for q in p:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], q) <= 0:
            lower.pop()
        lower.append(tuple(q))
    for q in p[::-1]:
        while len(upper) >= 2 and cross(upper[-2], upper[-1], q) <= 0:
            upper.pop()
        upper.append(tuple(q))
    h = np.array(lower[:-1] + upper[:-1])
    d = np.diff(np.vstack([h, h[:1]]), axis=0)
    return float(np.hypot(d[:, 0], d[:, 1]).sum())


def frame_metrics(shadow, region, tiny=0.001, edge=2):
    """metrics of one shadow mask inside the face region (see the module doc). A terminator that meets the outline
    of the region at a grazing angle (light from the front) leaves sub-pixel slivers there that break into pieces in
    any renderer: a component smaller than `tiny` that touches the `edge`-px band along the outline is not counted.
    Tiny islands inside the face are counted (that is the noise this check is for)."""
    region = np.asarray(region, bool)
    area = int(region.sum())
    shadow = np.asarray(shadow, bool) & region
    lab, n = A.label(shadow)
    min_px = max(1.0, tiny * area)
    if n:
        sizes = np.bincount(lab.ravel(), minlength=n + 1)
        band = region & ~A.erode(region, edge) if edge else np.zeros_like(region)
        touch = np.bincount(lab[band].ravel(), minlength=n + 1) > 0
        drop = (sizes < min_px) & touch
        drop[0] = False
        if drop.any():
            shadow = shadow & ~drop[lab]
            lab, n = A.label(shadow)
    sizes = np.bincount(lab.ravel(), minlength=n + 1)[1:] if n else np.zeros(0, int)
    small = sizes < min_px
    sh_area = int(shadow.sum())
    if n:
        per = contour_length(shadow)                # = the sum over the components (they do not share contour)
        hp = hull_perimeter(row_extremes(contour_points(shadow)))
        ratio = per / hp if hp > 0 else 1.0
    else:
        ratio = 1.0
    return dict(components=int(n), tiny=int(small.sum()), tiny_share=float(small.mean()) if n else 0.0,
                tiny_area=float(sizes[small].sum() / sh_area) if sh_area else 0.0,
                perim_ratio=float(ratio), shadow_frac=float(sh_area / area) if area else 0.0, face_px=area)


# ----------------------------------------------------------------------------- across frames
def iou(a, b, valid=None):
    a = np.asarray(a, bool); b = np.asarray(b, bool)
    if valid is not None:
        a = a & valid; b = b & valid
    u = int((a | b).sum())
    return 1.0 if u == 0 else float((a & b).sum()) / u


def expected_growth(az):
    """True if the shadow should grow from az to az + step (light moving from the front towards the back)"""
    a = az % 360.0
    return a < 180.0 - 1e-6


def nesting(shadows, angles, region, tol=0.002):
    """shadows: list of masks in angle order (full circle, equal steps). For each neighbour pair (a, a+step): the
    pixels that break 'grows on the way to the back, shrinks on the way to the front', as a share of the face.
    -> dict(pairs, nested (share of pairs below tol), break_px_share (mean share of the face), worst)"""
    area = max(1, int(np.asarray(region, bool).sum()))
    out = []
    n = len(shadows)
    for i in range(n):
        a, b = shadows[i], shadows[(i + 1) % n]
        if expected_growth(angles[i]):
            bad = int((a & ~b).sum())             # shadow that disappears while it should only grow
        else:
            bad = int((b & ~a).sum())             # shadow that appears while it should only shrink
        out.append(bad / area)
    out = np.array(out)
    return dict(pairs=int(n), nested=float((out <= tol).mean()) if n else 1.0, break_share=float(out.mean()) if n else 0.0,
                worst=float(out.max()) if n else 0.0, worst_angle=float(angles[int(out.argmax())]) if n else 0.0)


def summarize(values):
    v = np.asarray([x for x in values if x is not None], np.float64)
    if not len(v):
        return dict(n=0)
    return dict(n=int(len(v)), mean=float(v.mean()), median=float(np.median(v)), p90=float(np.percentile(v, 90)),
                min=float(v.min()), max=float(v.max()))


def orbit_metrics(frames, angles, exprs, neutral=None, eye_mouth_r=6, tiny=0.001, nest_tol=0.002, min_union=0.01, edge=2):
    """frames[(expr, angle)] = (shadow, region, decal). -> dict with per-frame lists and the aggregates."""
    neutral = neutral or exprs[0]
    per = {}
    for key, (sh, reg, dec) in frames.items():
        per[key] = frame_metrics(sh, reg, tiny, edge)
    comps = [m['components'] for m in per.values()]
    agg = dict(
        frames=len(per),
        components=summarize(comps),
        single_share=float(np.mean([c <= 1 for c in comps])) if comps else 0.0,
        tiny_share=summarize([m['tiny_share'] for m in per.values()]),
        tiny_area=summarize([m['tiny_area'] for m in per.values()]),
        perim_ratio=summarize([m['perim_ratio'] for m in per.values() if m['components'] > 0]),
        shadow_frac=summarize([m['shadow_frac'] for m in per.values()]))
    # IoU vs neutral
    ious, ious_out = {}, {}
    for e in exprs:
        if e == neutral:
            continue
        li, lo = [], []
        for a in angles:
            if (e, a) not in frames or (neutral, a) not in frames:
                continue
            sa, ra, da = frames[(e, a)]
            sb, rb, db = frames[(neutral, a)]
            common = ra & rb
            if (sa | sb).sum() < min_union * max(1, common.sum()):
                continue                         # (almost) no shadow in either: IoU says nothing
            li.append(iou(sa, sb, common))
            keep = common & ~A.dilate(da | db, eye_mouth_r)
            lo.append(iou(sa, sb, keep))
        ious[e] = summarize(li); ious_out[e] = summarize(lo)
    agg['iou'] = ious
    agg['iou_outside_eyes_mouth'] = ious_out
    allm = [v['mean'] for v in ious.values() if v.get('n')]
    allo = [v['mean'] for v in ious_out.values() if v.get('n')]
    agg['iou_mean'] = float(np.mean(allm)) if allm else None
    agg['iou_min'] = float(min(v['min'] for v in ious.values() if v.get('n'))) if allm else None
    agg['iou_out_mean'] = float(np.mean(allo)) if allo else None
    agg['iou_out_min'] = float(min(v['min'] for v in ious_out.values() if v.get('n'))) if allo else None
    # nesting per expression (full circle only)
    nest = {}
    for e in exprs:
        seq = [frames[(e, a)][0] for a in angles if (e, a) in frames]
        if len(seq) != len(angles) or len(angles) < 3:
            continue
        reg = frames[(e, angles[0])][1]
        nest[e] = nesting(seq, angles, reg, nest_tol)
    agg['nesting'] = nest
    agg['nesting_share'] = float(np.mean([v['nested'] for v in nest.values()])) if nest else None
    return per, agg


def verdict(agg, targets=None):
    """targets of the brief: 1 component, 100 % nesting, IoU between expressions >= 0.98, perimeter ratio <= 1.1"""
    T = dict(components=1.0, nesting=1.0, iou=0.98, perim=1.1)
    T.update(targets or {})
    c = agg['components'].get('max', 0)
    out = dict(
        components_ok=bool(c <= T['components']),
        nesting_ok=bool(agg.get('nesting_share') is not None and agg['nesting_share'] >= T['nesting'] - 1e-9),
        iou_ok=bool(agg.get('iou_min') is not None and agg['iou_mean'] >= T['iou']),
        perim_ok=bool(agg['perim_ratio'].get('median', 1.0) <= T['perim']))
    out['all_ok'] = all(out.values())
    return out


def noisy_score(agg):
    """one number for 'how noisy is this shadow' (0 = clean): mean extra components + tiny share + ratio excess"""
    c = agg['components'].get('mean', 0.0)
    return float(max(0.0, c - 1.0) + agg['tiny_share'].get('mean', 0.0) * 10.0
                 + max(0.0, agg['perim_ratio'].get('median', 1.0) - 1.0) * 10.0)
