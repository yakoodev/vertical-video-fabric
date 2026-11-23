# -*- coding: utf-8 -*-
"""VRMC_springBone (the VRM 1.0 spring format) written into a VRoid VRM 0.x, next to its secondaryAnimation.

Why: VRM 0.x gives one stiffness / weight / damping / hit radius per bone GROUP, so a strand cannot be stiff at the
root and soft at the tip, and its last bone swings on a virtual 7 cm stick. VRMC_springBone sets them per JOINT and
ends a chain on a real tip node. three-vrm (the site's 3D preview, node/vrm renders) reads VRMC_springBone first when
the file declares it; VRM 0.x tools that do not know it keep using secondaryAnimation (left in place as the fallback).

Coordinates: glTF space, nodes carry no rotation; VRMC collider offsets are node-local (no Z flip, unlike VRM 0.x).

    build(glb, rep, profile)   -> report; rep = springs.fix() report of the same glb (tails, rigid chains, colliders)
"""
from __future__ import annotations

import copy

import numpy as np

from . import springs as S

EXT = 'VRMC_springBone'

# per class: values for joint 0 (root, on the scalp), 1 (middle), 2 (tip segment); progressive = stiff at the root,
# soft and heavy at the tip, damped so it lags and settles instead of flicking
HAIR = {
    'front': dict(stiffness=(4.0, 2.0, 1.3), gravity=(0.05, 0.15, 0.25), drag=(0.6, 0.64, 0.7)),
    'side': dict(stiffness=(3.4, 1.6, 0.9), gravity=(0.06, 0.1, 0.16), drag=(0.58, 0.62, 0.7)),
    'crown': dict(stiffness=(4.0, 2.2, 1.2), gravity=(0.05, 0.12, 0.2), drag=(0.6, 0.62, 0.65)),
    'back': dict(stiffness=(3.0, 1.3, 0.75), gravity=(0.06, 0.12, 0.18), drag=(0.55, 0.6, 0.68)),
    'tail': dict(stiffness=(2.2, 1.2, 0.8, 0.55), gravity=(0.2, 0.35, 0.5, 0.6), drag=(0.55, 0.58, 0.6, 0.62)),
    'bun': dict(stiffness=(3.5, 2.5), gravity=(0.1, 0.2), drag=(0.65, 0.65)),
}
HEAD_NEAR = ('J_Bip_C_Head', 'J_Bip_C_Neck')
BODY = ('J_Bip_C_UpperChest', 'J_Bip_C_Chest', 'J_Bip_C_Spine', 'J_Bip_L_Shoulder', 'J_Bip_R_Shoulder')
GAP = 0.0          # a tail may not come any closer to the head spheres than it is modelled


def _per(v, i):
    return v[min(i, len(v) - 1)]


# ----------------------------------------------------------------------------- the head: many spheres in the skull
def head_surface(rig):
    """scalp (the hair cap) + face skin, the head-rigid part, with normals"""
    pts, nor = [], []
    for mesh, key in (('Face', 'Face_00_SKIN'), ('Body', 'HairBack')):
        P, ids = rig.mesh_vertices(mesh, key)
        if not len(P):
            continue
        N = rig.g.accessor(rig.meshes[mesh]['prims'][0]['attrs']['NORMAL']).astype(float)[ids]
        _, J, W = rig.skin_of(mesh)
        dom = J[ids, :][np.arange(len(ids)), W[ids].argmax(1)]
        keep = dom == rig.human['head']
        pts.append(P[keep])
        nor.append(N[keep])
    return np.concatenate(pts), np.concatenate(nor)


def fit_head(rig, k=10, k_face=8, tol=0.005, step=0.008):
    """spheres inside the skull: first the scalp + face as a whole, then the face on its own (cheeks, brow, nose
    bridge), so a soft fringe tip meets the face where it is, not a sphere a centimetre behind it"""
    pts, nor = head_surface(rig)
    lo, hi = pts.min(0) + 0.01, pts.max(0) - 0.01
    cands = S.grid_inside(lo, hi, step)
    sph, cov = S.fit_spheres(rig, (pts, nor), 'J_Bip_C_Head', cands, k, pts, tol=tol, rmin=0.02)
    F, _ = rig.mesh_vertices('Face', 'Face_00_SKIN')
    face = F[F[:, 1] > rig.head[1] + 0.02]
    if len(face):
        sph2, _ = S.fit_spheres(rig, (pts, nor), 'J_Bip_C_Head', cands, k_face, face, tol=0.004, rmin=0.012)
        sph += sph2
    return sph, cov


# ----------------------------------------------------------------------------- chains
def tip_node(rig, chain, name):
    """a real tip for the last bone: along its direction, as far as the hair it carries reaches (2-12 cm)"""
    g = rig.g
    a, b = rig.P[chain[-2]], rig.P[chain[-1]]
    d = b - a
    d = d / max(np.linalg.norm(d), 1e-9)
    ext = 0.04
    if 'Hair' in rig.meshes:
        X, J, W = rig.skin_of('Hair')
        dom = J[np.arange(len(J)), W.argmax(1)]
        sel = dom == chain[-1]
        if sel.sum() > 3:
            ext = float(np.percentile((X[sel] - b) @ d, 95))
    ext = float(np.clip(ext, 0.02, 0.12))
    pos = b + d * ext
    g.json['nodes'].append({'name': name, 'translation': (pos - b).tolist()})
    ni = len(g.json['nodes']) - 1
    g.json['nodes'][chain[-1]].setdefault('children', []).append(ni)
    return ni, pos


def build(glb, rep: dict, profile: dict | None = None) -> dict:
    profile = profile or {}
    params = copy.deepcopy(HAIR)
    for k, v in profile.get('vrmc', {}).items():
        params.setdefault(k, {}).update({kk: tuple(vv) for kk, vv in v.items()})
    rig = S.Rig(glb)
    nodes = glb.json['nodes']
    out = dict(springs=0, joints=0, head_spheres=0, hair={}, clothes={})
    # ---- colliders: the head fit + every VRM 0.x sphere converted (node-local offsets, Z un-flipped)
    colliders, groups = [], []

    def add_group(name, items):
        ids = []
        for node, off, r in items:
            colliders.append({'node': int(node), 'shape': {'sphere': {'offset': [float(x) for x in off], 'radius': float(r)}}})
            ids.append(len(colliders) - 1)
        groups.append({'name': name, 'colliders': ids})
        return len(groups) - 1
    head_sph, cov = fit_head(rig)
    hi = rig.human['head']
    # VRoid puts some hair bones under the scalp: a sphere may not contain any rest tail (it would push it out and
    # change the look), so each sphere shrinks to clear every hair joint / tail by 3 mm; tiny ones are dropped
    hair_pts = []
    for i, n in enumerate(nodes):
        if n.get('name', '').startswith('J_Sec_Hair'):
            hair_pts.append(rig.P[i])
    hair_pts = np.array(hair_pts) if hair_pts else np.zeros((0, 3))
    head_items = []
    for _, c, r in head_sph:
        if len(hair_pts):
            r = min(r, float(np.linalg.norm(hair_pts - c, axis=1).min()) - 0.003)
        if r >= 0.01:
            head_items.append((hi, c - rig.P[hi], r))
    g_head = add_group('head', head_items)
    out['head_spheres'] = len(head_items)
    out['head_coverage'] = round(cov, 3)
    sa = rig.sa
    old_map = {}
    for ci, cg in enumerate(sa['colliderGroups']):
        items = [(cg['node'], (c['offset']['x'], c['offset']['y'], -c['offset']['z']), c['radius']) for c in cg['colliders']]
        old_map[ci] = add_group(nodes[cg['node']]['name'], items)
    head_c = [(rig.P[hi] + np.array(o), r) for _, o, r in head_items]

    def sphere_list(gi):
        out_ = []
        for c in groups[gi]['colliders']:
            col = colliders[c]
            o = col['shape']['sphere']['offset']
            out_.append((rig.P[col['node']] + np.array(o), col['shape']['sphere']['radius']))
        return out_

    def clearance(p, sph):
        return min((np.linalg.norm(p - c) - r for c, r in sph), default=1.0)

    springs = []

    def hair_spring(name, chain, cls, extra_groups):
        p = params[cls]
        tip, tip_pos = tip_node(rig, chain, nodes[chain[-1]]['name'] + '_tip')
        P = [rig.P[i] for i in chain] + [tip_pos]
        joints = []
        for i, ni in enumerate(chain):
            tail = P[i + 1]
            # the joint's tail may not get closer to the skull than it is modelled: hit = rest gap - 1 mm
            hit = float(np.clip(clearance(tail, head_c) - GAP, 0.002, 0.03))
            joints.append({'node': int(ni), 'hitRadius': float(np.floor(hit * 1e4) / 1e4), 'stiffness': _per(p['stiffness'], i),
                           'gravityPower': _per(p['gravity'], i), 'gravityDir': [0.0, -1.0, 0.0],
                           'dragForce': _per(p['drag'], i)})
        joints.append({'node': int(tip)})
        cgs = [g_head]
        for gi in extra_groups:
            sph = sphere_list(gi)
            if all(clearance(P[i + 1], sph) > joints[i]['hitRadius'] + 0.002 for i in range(len(chain))):
                cgs.append(gi)
        springs.append({'name': name, 'joints': joints, 'colliderGroups': cgs})
        return joints

    near = [old_map[ci] for ci, cg in enumerate(sa['colliderGroups']) if nodes[cg['node']]['name'] in HEAD_NEAR]
    body = [old_map[ci] for ci, cg in enumerate(sa['colliderGroups']) if nodes[cg['node']]['name'] in BODY]
    rigid = set(rep.get('hair', {}).get('rigid', []))
    tail_roots = {t['root'] for t in rep.get('tails', [])}
    for i, n in enumerate(nodes):
        nm = n.get('name', '')
        if not nm.startswith('J_Sec_Hair1_') or nm in rigid:
            continue
        chain = rig.chain(i)
        cls = rig.classify(chain)
        js = hair_spring('Hair_%s_%s' % (cls, nm[-2:]), chain, cls, near + (body if cls in ('back', 'side') else []))
        out['hair'].setdefault(cls, []).append([j.get('hitRadius') for j in js[:-1]])
    for t, spec in zip(rep.get('tails', []), profile.get('tails', [])):
        cls = spec.get('kind', 'tail')
        hair_spring('Hair_%s' % spec.get('label', cls), t['joints'], cls, near + body)
        out['hair'][spec.get('label', cls)] = len(t['joints'])
    # ---- clothes: the VRM 0.x groups as they are (fix() tuned them), each chain with a 7 cm tip like VRM 0.x
    for gr in sa['boneGroups']:
        if any(S.HAIR.match(nodes[r].get('name', '')) for r in gr['bones']) or any(r in tail_roots for r in gr['bones']):
            continue
        for r in gr['bones']:
            chain = rig.chain(r)
            if len(chain) < 2:
                d = rig.P[chain[-1]] - rig.P[rig.par[chain[-1]]]
            else:
                d = rig.P[chain[-1]] - rig.P[chain[-2]]
            d = d / max(np.linalg.norm(d), 1e-9)
            nodes.append({'name': nodes[chain[-1]]['name'] + '_tip', 'translation': (d * S.VIRTUAL_TAIL).tolist()})
            tip = len(nodes) - 1
            nodes[chain[-1]].setdefault('children', []).append(tip)
            joints = [{'node': int(ni), 'hitRadius': gr['hitRadius'], 'stiffness': gr['stiffiness'],
                       'gravityPower': gr['gravityPower'],
                       'gravityDir': [gr['gravityDir']['x'], gr['gravityDir']['y'], gr['gravityDir']['z']],
                       'dragForce': gr['dragForce']} for ni in chain] + [{'node': tip}]
            springs.append({'name': gr.get('comment', 'cloth'), 'joints': joints,
                            'colliderGroups': [old_map[c] for c in gr.get('colliderGroups', [])]})
            out['clothes'][gr.get('comment', 'cloth')] = out['clothes'].get(gr.get('comment', 'cloth'), 0) + 1
    glb.json.setdefault('extensions', {})[EXT] = {'specVersion': '1.0', 'colliders': colliders,
                                                  'colliderGroups': groups, 'springs': springs}
    used = glb.json.setdefault('extensionsUsed', [])
    if EXT not in used:
        used.append(EXT)
    out['springs'] = len(springs)
    out['joints'] = sum(len(s['joints']) - 1 for s in springs)
    return out
