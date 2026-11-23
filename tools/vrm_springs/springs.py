# -*- coding: utf-8 -*-
"""VRM 0.x spring bones (``extensions.VRM.secondaryAnimation``) for the VRoid characters of «Невезучий»:
read the rig, classify the hair chains, rebuild the bone groups and colliders, add own chains for tied hair.

Nothing here moves a vertex: positions, normals, UVs, materials and textures keep their bytes. The only mesh-side edit
is the skin of tied hair (a ponytail / pigtails / bun gets its own bone chain, its vertices are re-weighted to it),
which changes how the hair moves, not how it looks at rest.

Coordinates: glTF space of a VRoid VRM 0.x — Y up, the character faces -Z, nodes carry no rotation. VRM 0.x stores
collider offsets with Z negated (three-vrm and UniVRM flip it back), gravityDir is stored as is.

How a VRM 0.x spring works (three-vrm / UniVRM): every node under a listed root is a joint; a joint rotates so that
its tail (the first child, or a virtual 7 cm tail along the bone for a leaf) follows
    next = cur + (cur - prev) * (1 - drag) + restDir * stiffness * dt + gravityDir * gravityPower * dt
then the tail is put back at bone length and pushed out of every collider sphere (radius + group hitRadius).
So a root listed one joint lower keeps the first segment rigid on the head; colliders only see tails.
"""
from __future__ import annotations

import copy
import math
import re

import numpy as np

from .glb import Glb

HAIR = re.compile(r'^J_Sec_Hair')
VIRTUAL_TAIL = 0.07          # three-vrm / UniVRM: a leaf joint gets a 7 cm tail along its bone


# ============================================================================ the rig
class Rig:
    def __init__(self, glb: Glb):
        self.g = glb
        self.nodes = glb.json['nodes']
        self.P = glb.world_positions()
        self.par = glb.parents()
        self.idx = {n.get('name'): i for i, n in enumerate(self.nodes)}
        self.human = {h['bone']: h['node'] for h in glb.vrm['humanoid']['humanBones']}
        self.sa = glb.vrm['secondaryAnimation']
        self.head = self.P[self.human['head']]
        self.meshes = self._meshes()
        self._head_frame()

    # ---------------------------------------------------------------- meshes
    def _meshes(self) -> dict:
        out = {}
        for ni, n in enumerate(self.nodes):
            if 'mesh' not in n:
                continue
            me = self.g.json['meshes'][n['mesh']]
            prims = []
            for pr in me['primitives']:
                mat = self.g.json['materials'][pr['material']]['name'] if 'material' in pr else ''
                prims.append(dict(mat=mat, attrs=pr['attributes'], indices=pr.get('indices')))
            out[n.get('name', str(ni))] = dict(node=ni, skin=n.get('skin'), prims=prims)
        return out

    def mesh_vertices(self, mesh: str, mat_key: str | None = None, exclude: str | None = None):
        """-> (positions (V,3), vertex ids) of the vertices used by the matching primitives of a mesh."""
        m = self.meshes.get(mesh)
        if m is None:
            return np.zeros((0, 3)), np.zeros(0, int)
        ids = set()
        pos_acc = None
        for pr in m['prims']:
            if mat_key and mat_key not in pr['mat']:
                continue
            if exclude and re.search(exclude, pr['mat']):
                continue
            pos_acc = pr['attrs']['POSITION']
            ids.update(np.unique(self.g.accessor(pr['indices']).ravel()).tolist())
        if pos_acc is None:
            return np.zeros((0, 3)), np.zeros(0, int)
        X = self.g.accessor(pos_acc).astype(float)
        ids = np.array(sorted(ids), int)
        return X[ids], ids

    def skin_of(self, mesh: str):
        """-> (positions, joints (V,4) as NODE indices, weights (V,4)) of the mesh's first primitive."""
        m = self.meshes[mesh]
        pr = m['prims'][0]
        joints = self.g.json['skins'][m['skin']]['joints']
        J = self.g.accessor(pr['attrs']['JOINTS_0']).astype(int)
        W = self.g.accessor(pr['attrs']['WEIGHTS_0']).astype(float)
        X = self.g.accessor(pr['attrs']['POSITION']).astype(float)
        return X, np.array(joints)[J], W

    def _head_frame(self):
        """skull centre C and head radius R from the face skin (the eyes' height, the face's width)."""
        F, _ = self.mesh_vertices('Face', 'Face_00_SKIN')
        if len(F) == 0:
            self.C = self.head + np.array([0, 0.08, 0.0])
            self.R = 0.08
            return
        lo, hi = F.min(0), F.max(0)
        self.R = 0.5 * (hi[0] - lo[0])
        self.C = np.array([0.5 * (lo[0] + hi[0]), self.head[1] + 0.95 * self.R, lo[2] + 1.15 * self.R])

    # ---------------------------------------------------------------- chains
    def chain(self, root: int) -> list:
        c = [root]
        while self.nodes[c[-1]].get('children'):
            c.append(self.nodes[c[-1]]['children'][0])
        return c

    def tails(self, root: int) -> np.ndarray:
        """rest tail of every joint three-vrm makes under this root (children + virtual leaf tails)."""
        out = []
        stack = [root]
        while stack:
            i = stack.pop()
            kids = self.nodes[i].get('children', [])
            if kids:
                out.append(self.P[kids[0]])
            else:
                d = self.P[i] - self.P[self.par[i]]
                n = np.linalg.norm(d)
                out.append(self.P[i] + (d / n if n > 1e-9 else np.array([0, -1, 0])) * VIRTUAL_TAIL)
            stack.extend(kids)
        return np.array(out)

    def hair_chains(self) -> list:
        """every hair root of the current groups -> dict(root, chain, cls)."""
        out = []
        for g in self.sa['boneGroups']:
            for r in g['bones']:
                if HAIR.match(self.nodes[r].get('name', '')):
                    out.append(dict(root=r, chain=self.chain(r), cls=self.classify(self.chain(r)), group=g))
        return out

    def classify(self, chain: list) -> str:
        """front (fringe over the forehead) / side (locks by the cheeks) / back / crown (lies flat on the scalp)."""
        r = self.P[chain[0]] - self.C
        if len(chain) > 1:
            d = self.P[chain[1]] - self.P[chain[0]]
            ang = math.degrees(math.acos(max(-1.0, min(1.0, -d[1] / max(1e-9, np.linalg.norm(d))))))
            if ang > 60.0 and r[1] > 0.6 * self.R:
                return 'crown'
        if r[2] < -0.45 * self.R and abs(r[0]) < 0.62 * self.R:
            return 'front'
        if r[2] < 0.15 * self.R and abs(r[0]) >= 0.5 * self.R:
            return 'side'
        if r[2] < -0.45 * self.R:
            return 'side'
        return 'back'


# ============================================================================ colliders
def nearest(pts: np.ndarray, query: np.ndarray, chunk: int = 512):
    """-> (distance, index) of the nearest of pts for every query point (brute force; no scipy in the venv)."""
    out = np.empty(len(query))
    idx = np.empty(len(query), int)
    p2 = (pts ** 2).sum(1)
    for i in range(0, len(query), chunk):
        q = query[i:i + chunk]
        d2 = (q ** 2).sum(1)[:, None] - 2 * q @ pts.T + p2[None, :]
        k = d2.argmin(1)
        idx[i:i + chunk] = k
        out[i:i + chunk] = np.sqrt(np.maximum(d2[np.arange(len(q)), k], 0))
    return out, idx


def body_surface(rig: Rig) -> np.ndarray:
    """skin + clothes near the hair (no hair cap, no spring-driven cloth): what hair must not go into."""
    X, Jn, W = rig.skin_of('Body')
    m = rig.meshes['Body']
    Nb = rig.g.accessor(m['prims'][0]['attrs']['NORMAL']).astype(float)
    keep = np.ones(len(X), bool)
    _, cap = rig.mesh_vertices('Body', 'HairBack')
    keep[cap] = False
    dom = Jn[np.arange(len(Jn)), W.argmax(1)]
    keep &= np.array([not rig.nodes[d].get('name', '').startswith('J_Sec_') for d in dom])
    F, fid = rig.mesh_vertices('Face', 'Face_00_SKIN')
    Nf = rig.g.accessor(rig.meshes['Face']['prims'][0]['attrs']['NORMAL']).astype(float)[fid]
    return np.concatenate([X[keep], F]), np.concatenate([Nb[keep], Nf])


def fit_spheres(rig: Rig, surf: np.ndarray, bone: str, centers: np.ndarray, k: int, region: np.ndarray,
                tol: float = 0.012, rmin: float = 0.02):
    """Greedy inscribed spheres on a bone: each candidate centre gets the largest radius that stays inside the
    surface; pick the k that best cover the region points (|p - c| within tol of r)."""
    pts, nor = surf
    rad, near_i = nearest(pts, centers)
    inside = ((centers - pts[near_i]) * nor[near_i]).sum(1) < 0        # behind the nearest surface vertex = inside the body
    ok = (rad >= rmin) & inside
    centers, rad = centers[ok], rad[ok]
    covered = np.zeros(len(region), bool)
    picked = []
    for _ in range(k):
        best, gain = None, 0
        for i, (c, r) in enumerate(zip(centers, rad)):
            d = np.linalg.norm(region - c, axis=1)
            cov = (~covered) & (d < r + tol)
            if cov.sum() > gain:
                best, gain = i, cov.sum()
        if best is None or gain < max(5, 0.01 * len(region)):
            break
        c = centers[best].copy()
        r = float(rad[best])
        mid = rig.P[rig.human['hips']][0]
        if abs(c[0] - mid) > 0.008:                      # keep it symmetric: the mirror twin, both at the smaller size
            cm = c.copy()
            cm[0] = 2 * mid - c[0]
            rm, ni = nearest(pts, cm[None])
            if ((cm - pts[ni[0]]) @ nor[ni[0]]) < 0 and rm[0] >= rmin:
                r = min(r, float(rm[0]))
                picked.append((bone, cm, r))
                covered |= np.linalg.norm(region - cm, axis=1) < r + tol
        covered |= np.linalg.norm(region - c, axis=1) < r + tol
        picked.append((bone, c, r))
    return picked, float(covered.mean()) if len(region) else 1.0


def grid_inside(lo, hi, step):
    ax = [np.arange(lo[i], hi[i] + 1e-9, step) for i in range(3)]
    return np.stack(np.meshgrid(*ax, indexing='ij'), -1).reshape(-1, 3)


def design_colliders(rig: Rig, hair_pts: np.ndarray) -> list:
    """-> [(bone name, centre (world), radius)] on head, neck, upper chest and shoulders."""
    pts, nor = body_surface(rig)
    reach = np.linalg.norm(hair_pts - rig.C, axis=1).max() + 0.05 if len(hair_pts) else 0.3
    m = np.linalg.norm(pts - rig.C, axis=1) < reach
    surf = (pts[m], nor[m])
    near = pts[m]
    P = rig.P
    hn = rig.human
    out = []
    # head: inscribed in face skin + scalp (approx: head-weighted body skin) -> skull, forehead, cheeks, jaw
    F, _ = rig.mesh_vertices('Face', 'Face_00_SKIN')
    head_reg = near[(near[:, 1] > P[hn['neck']][1] + 0.02)]
    lo, hi = F.min(0) - [0, 0, 0], F.max(0)
    lo = np.array([lo[0] + 0.01, P[hn['head']][1] + 0.01, lo[2] + 0.01])
    hi = np.array([hi[0] - 0.01, hi[1] + 0.06, hi[2] + 0.06])
    sph, cov_h = fit_spheres(rig, surf, 'J_Bip_C_Head', grid_inside(lo, hi, 0.01), 6, head_reg)
    out += sph
    # neck: a few spheres along neck -> head
    a, b = P[hn['neck']], P[hn['head']]
    cands = np.array([a + (b - a) * t + off for t in np.linspace(0, 1, 6)
                      for off in ([0, 0, 0], [0, 0, 0.01], [0, 0, -0.01])])
    neck_reg = near[(near[:, 1] > a[1] - 0.03) & (near[:, 1] < b[1] + 0.01)]
    sph, _ = fit_spheres(rig, surf, 'J_Bip_C_Neck', cands, 2, neck_reg)
    out += sph
    # upper chest + shoulders: candidates on the grid of the shoulder girdle
    uc = P[hn.get('upperChest', hn['chest'])]
    ls, rs = P[hn['leftUpperArm']], P[hn['rightUpperArm']]
    lo = np.array([min(ls[0], rs[0]), uc[1] - 0.12, uc[2] - 0.12])
    hi = np.array([max(ls[0], rs[0]), a[1] + 0.02, uc[2] + 0.14])
    chest_reg = near[(near[:, 1] < a[1]) & (near[:, 1] > uc[1] - 0.12)]
    sph, _ = fit_spheres(rig, surf, 'J_Bip_C_UpperChest', grid_inside(lo, hi, 0.015), 5, chest_reg)
    for bone, c, r in sph:                               # spheres over a shoulder ride the shoulder bone
        side = None
        if abs(c[0] - uc[0]) > 0.06:
            side = 'J_Bip_L_Shoulder' if (c[0] - uc[0]) * (ls[0] - uc[0]) > 0 else 'J_Bip_R_Shoulder'
        out.append((side if side and side in rig.idx else bone, c, r))
    rig.collider_coverage = dict(head=round(cov_h, 3))
    return out


# ============================================================================ tied hair: own chains
def tail_vertices(rig: Rig, spec: dict):
    """hair vertices of a tail: beyond the tie along the axis, within the radius of the axis line."""
    X, Jn, W = rig.skin_of('Hair')
    T = rig.head + np.array(spec['tie'], float)
    a = np.array(spec['dir'], float)
    a /= np.linalg.norm(a)
    s = (X - T) @ a
    radial = np.linalg.norm((X - T) - np.outer(s, a), axis=1)
    sel = (s > -spec.get('above', 0.01)) & (radial < spec['radius']) & (s < spec.get('max_len', 0.6))
    if 'outside' in spec:                                  # drop scalp hair: keep vertices away from the skull
        sel &= np.linalg.norm(X - rig.C, axis=1) > spec['outside'] * rig.R
    return sel, s, T, a


def add_tail_chain(rig: Rig, spec: dict, name: str) -> dict:
    """New joints under the head along the tail axis; the tail vertices are re-weighted to them."""
    g = rig.g
    sel, s, T, a = tail_vertices(rig, spec)
    if sel.sum() < 20:
        raise ValueError('tail %s: only %d vertices selected' % (name, sel.sum()))
    L = float(np.percentile(s[sel], 99)) if 'length' not in spec else spec['length']
    n = int(spec.get('joints', 4))
    seg = L / n
    head_i = rig.human['head']
    new = []
    parent = head_i
    parent_pos = rig.P[head_i]
    for k in range(n):
        pos = T + a * (seg * k)
        node = {'name': '%s_%02d' % (name, k + 1), 'translation': (pos - parent_pos).tolist()}
        g.json['nodes'].append(node)
        ni = len(g.json['nodes']) - 1
        g.json['nodes'][parent].setdefault('children', []).append(ni)
        new.append(ni)
        parent, parent_pos = ni, pos
    # skin: append the joints to the hair mesh's skin (inverse bind = inverse rest world matrix, no rotation)
    hm = rig.meshes['Hair']
    skin = g.json['skins'][hm['skin']]
    base = len(skin['joints'])
    skin['joints'] += new
    ibm = g.accessor(skin['inverseBindMatrices']).reshape(-1, 4, 4)   # glTF column-major
    add = []
    for k, ni in enumerate(new):
        pos = T + a * (seg * k)
        M = np.eye(4)
        M[:3, 3] = -pos
        add.append(M.T.reshape(16))                        # column-major
    skin['inverseBindMatrices'] = g.add_accessor(np.concatenate([ibm.reshape(-1, 16), np.array(add)]).astype(np.float32))
    # weights: triangular falloff around segment middles along the axis
    pr = hm['prims'][0]
    J = g.accessor(pr['attrs']['JOINTS_0']).astype(np.uint16)
    W = g.accessor(pr['attrs']['WEIGHTS_0']).astype(np.float32)
    u = np.clip(s[sel] / seg, 0.0, n - 0.5)
    WT = np.stack([np.clip(1.0 - np.abs(u - (k + 0.5)), 0, 1) for k in range(n)], 1)
    WT[u <= 0.5, 0] = 1.0
    WT /= WT.sum(1, keepdims=True)
    top2 = np.argsort(-WT, 1)[:, :2]
    rows = np.where(sel)[0]
    newJ = np.zeros((len(rows), 4), np.uint16)
    newW = np.zeros((len(rows), 4), np.float32)
    newJ[:, :2] = base + top2
    newW[:, :2] = np.take_along_axis(WT, top2, 1)
    newW /= newW.sum(1, keepdims=True)
    # the band just above the tie blends from the old weights into the tail root
    above = spec.get('above', 0.01)
    blend = np.clip((s[sel] + above) / max(above, 1e-6), 0, 1)[:, None] if above > 0 else np.ones((len(rows), 1))
    mixedJ, mixedW = [], []
    for r_, j2, w2, b in zip(rows, newJ, newW, blend[:, 0]):
        if b >= 0.999:
            mixedJ.append(j2)
            mixedW.append(w2)
            continue
        acc = {}
        for jj, ww in zip(J[r_], W[r_]):
            acc[int(jj)] = acc.get(int(jj), 0) + float(ww) * (1 - b)
        for jj, ww in zip(j2, w2):
            acc[int(jj)] = acc.get(int(jj), 0) + float(ww) * b
        top = sorted(acc.items(), key=lambda kv: -kv[1])[:4]
        jj = np.zeros(4, np.uint16)
        wv = np.zeros(4, np.float32)
        for q, (k_, v_) in enumerate(top):
            jj[q], wv[q] = k_, v_
        mixedJ.append(jj)
        mixedW.append(wv / wv.sum())
    J[rows] = np.array(mixedJ)
    W[rows] = np.array(mixedW)
    ja = g.add_accessor(J, target=34962)
    wa = g.add_accessor(W.astype(np.float32), target=34962)
    for p in g.json['meshes'][rig.nodes[hm['node']]['mesh']]['primitives']:
        p['attributes']['JOINTS_0'] = ja
        p['attributes']['WEIGHTS_0'] = wa
    # refresh rig caches for the new nodes
    rig.nodes = g.json['nodes']
    rig.P = g.world_positions()
    rig.par = g.parents()
    rig.idx = {nd.get('name'): i for i, nd in enumerate(rig.nodes)}
    return dict(name=name, root=new[0], joints=new, verts=int(sel.sum()), length=round(L, 3))


def dominant_counts(rig: Rig) -> dict:
    """hair vertices per dominant joint (node index)."""
    if 'Hair' not in rig.meshes:
        return {}
    _, Jn, W = rig.skin_of('Hair')
    dom = Jn[np.arange(len(Jn)), W.argmax(1)]
    u, c = np.unique(dom, return_counts=True)
    return dict(zip(u.tolist(), c.tolist()))


# ============================================================================ the plan
DEFAULTS = {
    # Start: the pilot's v6 lessons (weight 1.1, damping 0.62, no wind, softer away from the root). In three-vrm terms
    # "softer from the root" = the root segment stays on the head (the group is listed one joint lower) and the
    # hanging part swings. Then tuned on the preview's own solver (tools/vrm_springs/sim): with v6 weight the back
    # hair sagged 3-6 cm off its modelled shape and tips went through the shoulders, and any fringe softer than
    # ~14 dipped 2-9 mm into the face on footfalls of a run. So: back hair keeps weight and damping, the fringe and the
    # side locks are near-rigid (they still follow a shake a little), the crown never sags.
    'front': dict(stiffiness=14.0, gravityPower=0.03, dragForce=0.92, hitRadius=0.012, reroot=True),
    'side': dict(stiffiness=6.0, gravityPower=0.12, dragForce=0.85, hitRadius=0.012, reroot=True),
    'crown': dict(stiffiness=4.0, gravityPower=0.1, dragForce=0.8, hitRadius=0.008, reroot=True),
    'back': dict(stiffiness=1.2, gravityPower=0.45, dragForce=0.62, hitRadius=0.03, reroot=True),
    # under a hood the back tips sank into the hood bottom / shoulders with full weight: lighter and stiffer there
    'back_hood': dict(stiffiness=1.8, gravityPower=0.25, dragForce=0.65),
    'tail': dict(stiffiness=0.9, gravityPower=0.6, dragForce=0.55, hitRadius=0.02),
    'bun': dict(stiffiness=3.0, gravityPower=0.2, dragForce=0.7, hitRadius=0.015),
    # clothes: damping so nothing rings (several had drag 0), a little weight, colliders that clear the rest pose
    # (measured: extra weight on strings / bust / hems pushed their tips into the body, leg colliders made a sweater
    # hem flap on every knee lift; so they keep their own weight and get damping only)
    'HoodString': dict(dragForce=0.45, hitRadius=0.01),
    'Hood': dict(dragForce=0.45),
    'TopsUpperArm': dict(dragForce=0.85, stiffiness=4.0),
    'Bust': dict(keep=True),
    'Sleeve': dict(keep=True),          # (more damping only made the sleeve ends flick: their own setup is kept)
    'cloth': dict(dragForce=0.4),
}
CLOTH_COLLIDE = {'HoodString': ('J_Bip_C_UpperChest', 'J_Bip_C_Spine', 'J_Bip_C_Chest'),
                 'Hood': ('J_Bip_C_Neck', 'J_Bip_C_Head', 'J_Bip_C_UpperChest')}


def _vec(d):
    return {'x': float(d[0]), 'y': float(d[1]), 'z': float(d[2])}


def fix(glb: Glb, profile: dict | None = None) -> dict:
    """Rebuild the spring setup in place. profile: {"params": {cls: {...}}, "tails": [spec...], "drop_back": bool}
    -> report dict."""
    profile = profile or {}
    params = copy.deepcopy(DEFAULTS)
    for k, v in profile.get('params', {}).items():
        params.setdefault(k, {}).update(v)
    rig = Rig(glb)
    sa = rig.sa
    if any(g.get('comment') == 'Hood' for g in sa['boneGroups']):
        params['back'] = dict(params['back'], **params['back_hood'])
    rep = dict(groups_before=len(sa['boneGroups']), tails=[], hair={}, clothes={}, colliders=[])
    hair = rig.hair_chains()
    has_hair = 'Hair' in rig.meshes
    # --- tied hair: own chains first (they change the skin, so the counts below see the new weights)
    tails = []
    for k, spec in enumerate(profile.get('tails', [])):
        nm = spec.get('name', 'J_Sec_HairTail%d' % (k + 1))
        tails.append((spec, add_tail_chain(rig, spec, nm)))
        rep['tails'].append(tails[-1][1])
    counts = dominant_counts(rig)
    # --- colliders: fitted spheres, one collider group each (bone groups pick the ones they clear at rest)
    hair_pts = rig.skin_of('Hair')[0] if has_hair else np.zeros((0, 3))
    spheres = design_colliders(rig, hair_pts) if (has_hair or sa['boneGroups']) else []
    old_cg = sa['colliderGroups']
    base_cg = len(old_cg)
    new_cg = []
    for bone, c, r in spheres:
        bi = rig.idx[bone]
        off = c - rig.P[bi]
        new_cg.append({'node': bi, 'colliders': [{'offset': _vec((off[0], off[1], -off[2])), 'radius': round(r, 4)}]})
        rep['colliders'].append(dict(bone=bone, offset=[round(x, 4) for x in off], radius=round(r, 4)))
    sa['colliderGroups'] = old_cg + new_cg

    def usable(tails_xyz, hit, which):
        """collider groups (indices) whose spheres clear every rest tail by hit + 3 mm"""
        out = []
        for gi, cg in enumerate(sa['colliderGroups']):
            if which is not None and rig.nodes[cg['node']]['name'] not in which:
                continue
            ok = True
            for col in cg['colliders']:
                o = col['offset']
                c = rig.P[cg['node']] + np.array([o['x'], o['y'], -o['z']])
                if len(tails_xyz) and np.min(np.linalg.norm(tails_xyz - c, axis=1)) < col['radius'] + hit + 0.003:
                    ok = False
                    break
            if ok:
                out.append(gi)
        return out

    hair_bones = ('J_Bip_C_Head', 'J_Bip_C_Neck', 'J_Bip_C_UpperChest', 'J_Bip_L_Shoulder', 'J_Bip_R_Shoulder',
                  'J_Bip_C_Chest', 'J_Bip_C_Spine')

    # stiff front / side / crown locks only meet the head and neck: a shoulder sphere rising into their 7 cm virtual
    # tails snapped them sideways for 2-3 frames in a walk (measured: 7 cm tip steps)
    head_bones = ('J_Bip_C_Head', 'J_Bip_C_Neck')

    def head_clearance(tails_xyz):
        """smallest gap between the rest tails and the new head / face spheres"""
        gap = 1.0
        for cg in new_cg:
            if rig.nodes[cg['node']]['name'] != 'J_Bip_C_Head':
                continue
            for col in cg['colliders']:
                o = col['offset']
                c = rig.P[cg['node']] + np.array([o['x'], o['y'], -o['z']])
                gap = min(gap, float(np.min(np.linalg.norm(tails_xyz - c, axis=1))) - col['radius'])
        return gap

    def hair_hit(tails_xyz, want):
        """hit radius = the rest gap to the face / skull spheres (minus 1.5 mm): at rest nothing is pushed, and in
        motion a strand cannot get any closer to the face than it is modelled; at least `want` when there is room"""
        gap = head_clearance(tails_xyz) - 0.0015
        return float(min(max(gap, 0.002), max(want, 0.03))) if gap < 1.0 else want
    groups = []
    # --- loose hair: one bone group per class
    by_cls = {}
    tail_nodes = set()
    for _, t in tails:
        tail_nodes.update(t['joints'])
    for h in hair:
        chain = h['chain']
        verts = sum(counts.get(i, 0) for i in chain)
        if tails and profile.get('drop_back', True) and h['cls'] == 'back' and verts < profile.get('min_verts', 40):
            rep['hair'].setdefault('rigid', []).append(rig.nodes[chain[0]]['name'])
            continue                                         # its hair went into the tail: stays on the head
        p = params[h['cls']]
        root = chain[1] if p.get('reroot') and len(chain) >= 3 else chain[0]
        by_cls.setdefault(h['cls'], []).append(root)
    for cls, roots in by_cls.items():
        p = params[cls]
        ncol, hits = [], []
        for r in roots:                                  # one group per chain: each gets every collider it clears
            tl = rig.tails(r)
            hit = hair_hit(tl, p['hitRadius'])
            groups.append({'comment': 'Hair_' + cls, 'stiffiness': p['stiffiness'], 'gravityPower': p['gravityPower'],
                           'gravityDir': _vec((0, -1, 0)), 'dragForce': p['dragForce'], 'center': -1,
                           'hitRadius': round(hit, 4), 'bones': [r],
                           'colliderGroups': usable(tl, hit - 0.0031, hair_bones if cls == 'back' else head_bones)})
            ncol.append(len(groups[-1]['colliderGroups']))
            hits.append(round(hit, 4))
        rep['hair'][cls] = dict(roots=[rig.nodes[r]['name'] for r in roots], colliders=[min(ncol), max(ncol)],
                                hit=[min(hits), max(hits)], **{k: v for k, v in p.items() if k not in ('reroot', 'hitRadius')})
    for spec, t in tails:
        cls = spec.get('kind', 'tail')
        p = dict(params[cls])
        p.update(spec.get('params', {}))
        tl = rig.tails(t['root'])
        hit = hair_hit(tl, p['hitRadius'])
        p['hitRadius'] = round(hit, 4)
        groups.append({'comment': 'Hair_' + spec.get('label', cls), 'stiffiness': p['stiffiness'],
                       'gravityPower': p['gravityPower'], 'gravityDir': _vec((0, -1, 0)), 'dragForce': p['dragForce'],
                       'center': -1, 'hitRadius': p['hitRadius'], 'bones': [t['root']],
                       'colliderGroups': usable(tl, hit - 0.0031, hair_bones)})
        rep['hair'][spec.get('label', cls)] = dict(joints=len(t['joints']), verts=t['verts'], length=t['length'],
                                                   colliders=len(groups[-1]['colliderGroups']), **p)
    # --- clothes: keep the groups, fix damping / weight, add colliders that clear the rest pose
    for g in sa['boneGroups']:
        if any(HAIR.match(rig.nodes[r].get('name', '')) for r in g['bones']):
            continue
        g2 = dict(g)
        key = g.get('comment', '')
        p = dict(params['cloth'])
        p.update(params.get(key, {}))
        if p.get('keep'):
            groups.append(g2)
            rep['clothes'][key] = 'unchanged'
            continue
        g2['dragForce'] = max(g['dragForce'], p['dragForce'])
        g2['gravityPower'] = max(g['gravityPower'], p.get('gravityPower', 0))
        if 'stiffiness' in p:
            g2['stiffiness'] = p['stiffiness']
        if 'hitRadius' in p:
            g2['hitRadius'] = p['hitRadius']
        if key in CLOTH_COLLIDE:
            tl = np.concatenate([rig.tails(r) for r in g['bones']])
            g2['colliderGroups'] = sorted(set(g.get('colliderGroups', [])) |
                                          set(usable(tl, g2['hitRadius'], CLOTH_COLLIDE[key])))
        groups.append(g2)
        rep['clothes'][key] = dict(stiffiness=g2['stiffiness'], gravityPower=g2['gravityPower'],
                                   dragForce=g2['dragForce'], hitRadius=g2['hitRadius'],
                                   colliders=len(g2.get('colliderGroups', [])))
    # drop the new collider groups nobody uses (old ones keep their indices)
    used = sorted({c for g in groups for c in g.get('colliderGroups', [])} | set(range(base_cg)))
    remap = {o: n for n, o in enumerate(used)}
    sa['colliderGroups'] = [sa['colliderGroups'][i] for i in used]
    for g in groups:
        g['colliderGroups'] = [remap[c] for c in g.get('colliderGroups', [])]
    rep['colliders'] = [c for i, c in enumerate(rep['colliders']) if base_cg + i in remap]
    sa['boneGroups'] = groups
    rep['groups_after'] = len(groups)
    rep['collider_coverage'] = getattr(rig, 'collider_coverage', {})
    return rep


def break_springs(glb: Glb, mode: str = 'floppy') -> None:
    """Negative controls the checks must flag.
    floppy:   no stiffness, heavy, undamped, no colliders -> hair falls into the face and the body;
    unstable: negative damping (each step adds energy) -> the strands whip and spin (jitter / runaway)."""
    for g in glb.vrm['secondaryAnimation']['boneGroups']:
        if mode == 'unstable':
            g['dragForce'] = -0.6
            g['stiffiness'] = max(g['stiffiness'], 0.5)
            continue
        g['stiffiness'] = 0.02
        g['gravityPower'] = 3.0
        g['dragForce'] = 0.0
        g['hitRadius'] = 0.0
        g['colliderGroups'] = []
