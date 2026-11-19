# -*- coding: utf-8 -*-
"""Pure image analysis of the model-check renders (numpy only, Python 3.6+).

Per view the Blender script leaves, for every compared layer, a depth map (distance along the camera ray, 0 = empty),
a part-id map (one id per object+material) and a backface flag; plus the all-layers id map at the view angle theta
and at theta + jitter, and two EEVEE beauty frames (theta, theta + jitter).

Detectors (all return blobs = dicts with a boolean pixel mask and measurements):
  poke_and_zfight  - inner layer in front of the outer one inside the outer silhouette (poke-through), and the two
                     within eps of each other over an area (near-coincident surfaces -> z-fighting)
  id_shimmer       - part ids that change between theta and theta+jitter in a way a sub-pixel move cannot explain
                     (new id not present in the 3x3 neighbourhood), or isolated single-pixel ids (speckle)
  texture_flicker  - the same neighbourhood test on the beauty colours: texture-level shimmering
Then cluster() groups the per-view findings of one pose in 3D and score() turns clusters into one number.
"""
import numpy as np


# ----------------------------------------------------------------------------- morphology (no scipy)
def _shift(a, dy, dx, fill):
    """out[y, x] = a[y + dy, x + dx], `fill` outside."""
    H, W = a.shape[:2]
    out = np.empty_like(a)
    out[...] = fill
    ys, yd = (slice(dy, H), slice(0, H - dy)) if dy >= 0 else (slice(0, H + dy), slice(-dy, H))
    xs, xd = (slice(dx, W), slice(0, W - dx)) if dx >= 0 else (slice(0, W + dx), slice(-dx, W))
    out[yd, xd] = a[ys, xs]
    return out


def dilate(mask, r=1):
    """Square (2r+1) dilation of a boolean mask (separable)."""
    m = mask.astype(bool)
    if r <= 0 or not m.any():
        return m.copy()
    out = m.copy()
    for k in range(1, r + 1):
        out |= _shift(m, 0, k, False) | _shift(m, 0, -k, False)
    m2 = out.copy()
    for k in range(1, r + 1):
        out |= _shift(m2, k, 0, False) | _shift(m2, -k, 0, False)
    return out


def erode(mask, r=1):
    m = mask.astype(bool)
    if r <= 0:
        return m.copy()
    out = m.copy()
    for k in range(1, r + 1):
        out &= _shift(m, 0, k, False) & _shift(m, 0, -k, False)
    m2 = out.copy()
    for k in range(1, r + 1):
        out &= _shift(m2, k, 0, False) & _shift(m2, -k, 0, False)
    return out


def opening(mask, r=1):
    return dilate(erode(mask, r), r)


def label(mask):
    """8-connected component labelling via row runs + union-find. Returns (labels int32, n). 0 = background."""
    mask = np.asarray(mask, bool)
    H, W = mask.shape
    labels = np.zeros((H, W), np.int32)
    if not mask.any():
        return labels, 0
    m = mask.astype(np.int8)
    z = np.zeros((H, 1), np.int8)
    d = np.diff(np.concatenate([z, m, z], 1), axis=1)
    rs, cs = np.nonzero(d == 1)
    _, ce = np.nonzero(d == -1)
    n = len(rs)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    # runs are in row-major order; walk row r against row r-1 with two pointers
    row_start = np.searchsorted(rs, np.arange(H + 1))
    for r in range(1, H):
        a0, a1 = row_start[r], row_start[r + 1]
        b0, b1 = row_start[r - 1], row_start[r]
        if a0 == a1 or b0 == b1:
            continue
        j = b0
        for i in range(a0, a1):
            s1, e1 = cs[i], ce[i]
            while j < b1 and ce[j] < s1:          # previous-row run ends before this one starts (8-conn: ce >= s1)
                j += 1
            k = j
            while k < b1 and cs[k] <= e1:         # starts before this one ends (+1 diagonal)
                ri, rk = find(i), find(k)
                if ri != rk:
                    parent[max(ri, rk)] = min(ri, rk)
                k += 1
    roots = {}
    for i in range(n):
        root = find(i)
        lab = roots.get(root)
        if lab is None:
            lab = roots[root] = len(roots) + 1
        labels[rs[i], cs[i]:ce[i]] = lab
    return labels, len(roots)


def blobs_from_mask(mask, min_px=1, group_r=0, count_mask=None):
    """Connected blobs of `mask` (optionally grouped: components of dilate(mask, group_r)), keeping those with at
    least `min_px` pixels of `count_mask` (defaults to mask). Returns list of boolean masks (of the mask pixels)."""
    mask = np.asarray(mask, bool)
    if not mask.any():
        return []
    cm = mask if count_mask is None else count_mask
    lab, n = label(dilate(mask, group_r) if group_r else mask)
    if n == 0:
        return []
    counts = np.bincount(lab[cm], minlength=n + 1)
    out = []
    for k in np.nonzero(counts >= max(1, min_px))[0]:
        if k == 0:
            continue
        out.append((lab == k) & mask)
    return out


# ----------------------------------------------------------------------------- per-pixel helpers
def depth_edges(d, valid, jump):
    """Pixels whose 4-neighbour is empty or differs in depth by more than `jump` (silhouettes and occlusion
    boundaries)."""
    e = np.zeros(d.shape, bool)
    for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        nd = _shift(d, dy, dx, 0.0)
        nv = _shift(valid, dy, dx, False)
        e |= valid & (~nv | (np.abs(d - nd) > jump))
    return e


def nbhd_has(a, b, r=1):
    """True where the value b[p] occurs somewhere in the (2r+1)^2 neighbourhood of a around p."""
    out = np.zeros(a.shape, bool)
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            out |= _shift(a, dy, dx, -1) == b
    return out


def isolated(ids):
    """Non-zero id pixels with no 8-neighbour of the same id (single-pixel speckle)."""
    same = np.zeros(ids.shape, bool)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy or dx:
                same |= _shift(ids, dy, dx, -1) == ids
    return (ids > 0) & ~same


def range_violation(a, b, r=1):
    """How far colour b[p] lies outside the per-channel min..max of a's (2r+1)^2 neighbourhood (0 = explainable by
    a sub-pixel move). a, b: float (H, W, C)."""
    lo = a.copy()
    hi = a.copy()
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dy or dx:
                s = _shift(a, dy, dx, np.nan)
                lo = np.fmin(lo, s)
                hi = np.fmax(hi, s)
    v = np.maximum(b - hi, lo - b)
    return np.clip(np.nanmax(v, axis=2), 0, None)


def box_mean(m, r=3):
    """Mean of a (boolean/float) map over a (2r+1)^2 window (integral image; edges padded with 0)."""
    a = np.pad(np.asarray(m, np.float64), r + 1, mode='constant')
    s = a.cumsum(0).cumsum(1)
    k = 2 * r + 1
    H, W = m.shape
    tot = s[k:k + H, k:k + W] - s[0:H, k:k + W] - s[k:k + H, 0:W] + s[0:H, 0:W]
    return tot / float(k * k)


def majority(values, exclude=0):
    v = values[values != exclude]
    if v.size == 0:
        return None, 0.0
    u, c = np.unique(v, return_counts=True)
    k = int(np.argmax(c))
    return int(u[k]), float(c[k]) / float(v.size)


def top_values(values, n=2, exclude=0):
    v = values[values != exclude]
    if v.size == 0:
        return []
    u, c = np.unique(v, return_counts=True)
    order = np.argsort(-c)[:n]
    return [(int(u[i]), int(c[i])) for i in order]


# ----------------------------------------------------------------------------- parameters
def default_params(model_height, pixel_size):
    """Thresholds in world units, scaled to the model. pixel_size = world size of one pixel at the model centre."""
    H = float(model_height)
    px = float(pixel_size)
    return {
        'eps_poke': 0.0006 * H,          # inner closer than outer by more than this = poke-through (~0.9 mm at 1.55 m)
        'eps_zfight': 0.0006 * H,        # |inner - outer| below this = near-coincident
        'max_poke': 0.05 * H,            # farther in front than this = it is simply in front (a hand before a jacket)
        'jump': max(3.0 * px, 0.006 * H),  # depth step treated as an occlusion boundary
        't_cross': 0.012 * H,            # ring pixels with inner just behind outer by less than this = surfaces cross
        'min_poke_px': 4,
        'min_zfight_px': 12,
        'min_speckle': 8,                # speckle pixels per grouped blob
        'speckle_group_r': 2,
        'tex_thr': 0.12,                 # colour outside the neighbourhood range by this much (0..1)
        'dense_thr': 0.2,                # colour change counted for the density test
        'dense_frac': 0.4,               # share of changed pixels in a 7x7 window = dense shimmer (an edge
                                         # moving by a sub-pixel step changes ~1-2 px of 7)
        'cluster_r': 0.06 * H,
    }


# ----------------------------------------------------------------------------- detectors
def poke_and_zfight(d_in, d_out, bf_out, p, vis_in=None, vis_any=None):
    """d_in/d_out: distance maps of the inner and outer layer (0 = empty); bf_out: 1 where the outer pixel is a back
    face (seen through an opening) or None. vis_in: where the inner layer is what the full render shows (a poke
    hidden behind a third part, e.g. an arm, is not reported for that view); vis_any: where either layer is.
    Returns (poke_blobs, zfight_blobs)."""
    vi = d_in > 0
    vo = d_out > 0
    both = vi & vo
    if not both.any():
        return [], []
    ei = depth_edges(d_in, vi, p['jump'])
    eo = depth_edges(d_out, vo, p['jump'])
    core = both & ~dilate(ei, 1) & ~dilate(eo, 1)
    diff = np.where(both, d_out - d_in, 0.0)          # > 0: inner is closer to the camera than outer
    front = np.ones(d_in.shape, bool) if bf_out is None else (bf_out == 0)
    poke = core & front & (diff > p['eps_poke']) & (diff < p['max_poke'])
    zf = opening(core & (np.abs(diff) <= p['eps_zfight']), 1)
    if vis_in is not None:
        poke &= vis_in
    if vis_any is not None:
        zf &= vis_any

    pokes = []
    for b in blobs_from_mask(poke, p['min_poke_px']):
        # ring around the blob, without pixels where the inner layer is in front as well (the far-protruding
        # centre beyond max_poke, neighbouring pokes): those say nothing about how the blob is bounded
        ring = dilate(b, 2) & ~b & ~(both & (diff > p['eps_poke']))
        nring = max(int(ring.sum()), 1)
        # enclosure: around the blob the outer layer is the visible one (inner absent, or behind / level with it)
        outer_vis = vo & (~vi | (diff <= p['eps_poke']))
        enclosure = float((ring & outer_vis).sum()) / nring
        # crossing: around the blob the inner surface continues just behind the outer one (the two intersect),
        # rather than ending at its own silhouette / depth step (a hand resting on a jacket)
        cont = vi & vo & ~dilate(ei, 1) & ~dilate(eo, 1) & (diff <= p['eps_poke']) & (diff > -p['t_cross'])
        crossing = float((ring & cont).sum()) / nring
        pen = float(diff[b].max())
        kind = 'poke_through' if (enclosure >= 0.5 and crossing >= 0.35) else 'contact'
        pokes.append({'mask': b, 'area': int(b.sum()), 'enclosure': round(enclosure, 3),
                      'crossing': round(crossing, 3), 'penetration': pen, 'kind': kind})
    zfs = []
    for b in blobs_from_mask(zf, p['min_zfight_px']):
        zfs.append({'mask': b, 'area': int(b.sum()), 'kind': 'zfight',
                    'gap': float(np.abs(diff[b]).mean())})
    return pokes, zfs


def id_shimmer(id_a, id_b, p):
    """Part-id maps of the same view at theta and theta + jitter (0 = background)."""
    bg = dilate((id_a == 0) | (id_b == 0), 2)
    chg = ~nbhd_has(id_a, id_b, 1) | ~nbhd_has(id_b, id_a, 1)
    diff = id_a != id_b
    dense = diff & (box_mean(diff, 3) > p['dense_frac'])     # 50/50 fighting: both ids in every neighbourhood
    m = (chg | dense | isolated(id_a) | isolated(id_b)) & ~bg & (id_a > 0) & (id_b > 0)
    out = []
    for b in blobs_from_mask(m, p['min_speckle'], group_r=p['speckle_group_r']):
        out.append({'mask': b, 'area': int(b.sum()), 'kind': 'id_shimmer'})
    return out


def composite(rgba, bg=0.5):
    a = rgba[..., 3:4]
    return rgba[..., :3] * a + bg * (1 - a), rgba[..., 3]


def texture_flicker(rgba_a, rgba_b, p):
    """Beauty frames (float 0..1 RGBA) at theta and theta + jitter."""
    ca, aa = composite(rgba_a)
    cb, ab = composite(rgba_b)
    sil = dilate((aa < 0.995) | (ab < 0.995), 1)
    v = np.maximum(range_violation(ca, cb, 1), range_violation(cb, ca, 1))
    big = np.abs(ca - cb).max(2) > p['dense_thr']
    dense = big & (box_mean(big, 3) > p['dense_frac'])      # pixel-frequency texture: changes everywhere at once
    m = ((v > p['tex_thr']) | dense) & ~sil
    out = []
    for b in blobs_from_mask(m, p['min_speckle'], group_r=p['speckle_group_r']):
        out.append({'mask': b, 'area': int(b.sum()), 'kind': 'texture_flicker', 'strength': float(v[b].mean())})
    return out


# ----------------------------------------------------------------------------- geometry
def ray_dirs(view, W, H):
    """World-space unit ray directions (H, W, 3) from the camera frame stored by render_views.py."""
    M = np.array(view['cam_matrix'], np.float64)
    tr, br, bl, tl = [np.array(c, np.float64) for c in view['frame']]
    u = ((np.arange(W) + 0.5) / W)[None, :, None]
    v = ((np.arange(H) + 0.5) / H)[:, None, None]
    d = tl[None, None, :] + u * (tr - tl)[None, None, :] + v * (bl - tl)[None, None, :]
    d = d @ M[:3, :3].T
    return d / np.linalg.norm(d, axis=2, keepdims=True)


def unproject_mean(mask, dist, view, dirs=None):
    """Mean world position of the pixels of `mask` using the distance map `dist`."""
    ys, xs = np.nonzero(mask & (dist > 0))
    if len(ys) == 0:
        return None
    H, W = dist.shape
    if dirs is None:
        dirs = ray_dirs(view, W, H)
    cam = np.array(view['cam_matrix'], np.float64)[:3, 3]
    P = cam[None, :] + dirs[ys, xs] * dist[ys, xs][:, None]
    return [float(x) for x in P.mean(0)]


def nearest_bone(pos, bones):
    """bones: {name: [head, tail]} -> (name, distance) of the closest bone segment."""
    if pos is None or not bones:
        return None, None
    q = np.array(pos, np.float64)
    best = (None, 1e18)
    for name, (h, t) in bones.items():
        a = np.array(h, np.float64)
        b = np.array(t, np.float64)
        ab = b - a
        tt = float(np.clip(np.dot(q - a, ab) / max(np.dot(ab, ab), 1e-12), 0, 1))
        dd = float(np.linalg.norm(q - (a + ab * tt)))
        if dd < best[1]:
            best = (name, dd)
    return best


# ----------------------------------------------------------------------------- clustering + scoring
def _key(f):
    if f['type'] in ('poke_through', 'contact', 'zfight'):
        return (f['type'], f['inner_layer'], f['outer_layer'])
    if f['type'] == 'id_shimmer':
        return (f['type'],)
    return (f['type'], f.get('part'))


def cluster(findings, radius):
    """Group the findings of one pose: same type (+ layer pair / part) and 3D locations within `radius`.
    Returns clusters (dicts) sorted by severity, then area."""
    n = len(findings)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            fi, fj = findings[i], findings[j]
            if _key(fi) != _key(fj) or fi['pose'] != fj['pose']:
                continue
            if fi.get('pos') is None or fj.get('pos') is None:
                close = fi.get('pos') is None and fj.get('pos') is None
            else:
                close = float(np.linalg.norm(np.subtract(fi['pos'], fj['pos']))) <= radius
            if close:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[max(ri, rj)] = min(ri, rj)
    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(findings[i])
    clusters = []
    for members in groups.values():
        members.sort(key=lambda f: -f['area'])
        worst = members[0]
        area = sum(f['area'] for f in members)
        pos = None
        pts = [(f['pos'], f['area']) for f in members if f.get('pos') is not None]
        if pts:
            w = np.array([a for _, a in pts], np.float64)
            P = np.array([q for q, _ in pts], np.float64)
            pos = [round(float(x), 4) for x in (P * w[:, None]).sum(0) / w.sum()]
        parts = {}
        for f in members:
            for pname, cnt in f.get('parts', {}).items():
                parts[pname] = parts.get(pname, 0) + cnt
        views = sorted({f['view'] for f in members})
        c = {
            'type': worst['type'], 'pose': worst['pose'],
            'inner_layer': worst.get('inner_layer'), 'outer_layer': worst.get('outer_layer'),
            'n_views': len(views), 'views': views,
            'worst_views': _unique([f['view'] for f in members])[:3],
            'max_area_px': worst['area'], 'total_area_px': area, 'location': pos,
            'parts': dict(sorted(parts.items(), key=lambda kv: -kv[1])[:6]),
        }
        if worst['type'] in ('poke_through', 'contact'):
            c['max_penetration_mm'] = round(1000.0 * max(f.get('penetration', 0.0) for f in members), 1)
            c['enclosure'] = round(float(np.mean([f.get('enclosure', 0) for f in members])), 2)
            c['crossing'] = round(float(np.mean([f.get('crossing', 0) for f in members])), 2)
        if worst['type'] == 'zfight':
            c['mean_gap_mm'] = round(1000.0 * float(np.mean([f.get('gap', 0.0) for f in members])), 2)
        c['severity'] = severity(c)
        c['_members'] = members
        clusters.append(c)
    order = {'high': 0, 'medium': 1, 'low': 2, 'info': 3}
    clusters.sort(key=lambda c: (order[c['severity']], -c['max_area_px']))
    return clusters


def _unique(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def severity(c):
    t, nv, a = c['type'], c['n_views'], c['max_area_px']
    if t == 'poke_through':
        if nv >= 3 and a >= 25:
            return 'high'
        if nv >= 2 or a >= 25:
            return 'medium'
        return 'low'
    if t == 'contact':
        return 'low' if (c.get('enclosure', 0) >= 0.8 and nv >= 4 and a >= 40) else 'info'
    if t == 'zfight':
        if nv >= 3 and a >= 40:
            return 'high'
        if nv >= 2 or a >= 40:
            return 'medium'
        return 'low'
    # id_shimmer / texture_flicker
    if nv >= 4 and a >= 20:
        return 'medium'
    return 'low'


WEIGHTS = {'high': 12.0, 'medium': 4.0, 'low': 1.0, 'info': 0.0}


def score(clusters):
    """100 = clean. Each cluster costs by severity; never below 0."""
    pen = sum(WEIGHTS[c['severity']] for c in clusters)
    return max(0.0, round(100.0 - pen, 1))


def summarize(clusters):
    out = {}
    for c in clusters:
        k = '%s/%s' % (c['type'], c['severity'])
        out[k] = out.get(k, 0) + 1
    return out


def is_clean(clusters):
    return not any(c['severity'] in ('high', 'medium') for c in clusters)


def mm(v):
    return None if v is None else round(1000.0 * v, 2)


def fmt_loc(pos):
    if pos is None:
        return '-'
    return '(%s)' % ', '.join('%.3f' % x for x in pos)


def angle_name(el, az):
    return 'e%+03d_a%03d' % (int(round(el)), int(round(az)) % 360)

