# -*- coding: utf-8 -*-
"""Animation checks on a dumped shot (``anim_dump.py``): pure numpy, every check has a negative control in
``tests/test_anim_checks.py`` (a deliberately broken input it must catch).

Each check returns ``{'check', 'ok', 'errors': [...], 'warnings': [...], 'metrics': {...}}``; a finding is a dict with
``frame`` (the shot frame number), ``what`` and the measured numbers. Units: metres and degrees, world Z up.

Checks:
  jumps            a bone or vertex moves between two frames much more than its own local speed (spike / pop);
                   cuts are skipped (a cut may move anything).
  rotation_jumps   foot / hand turns between two frames by more than a hard limit, or by much more than usual.
  bend_plane       knee and elbow bend only about their hinge (in the limb plane) and in the right direction;
                   for knees also the knee distance from the plane "hip -> ankle along the foot" (cm).
  floor_contact    no foot or knee point under the floor / a step (depths measured in Blender by the dump).
  hand_contact     a hand that should hold / slide on a prop touches it (gap) and does not go into it.
  normals          a vertex normal turns > 40 deg away from its rest normal carried by the skinning: dents, chips.
  import_twist     on the first frame a bone is twisted > 90 deg about its own axis against the rest pose.
  static           a character that should move does not (bones do not change over the shot).
  weight_leak      rotating a deform bone +-45 deg moves vertices outside its zone (an arm pulling the belly).
  attached_props   a prop on a socket bone drifts from it (gap) or turns against it (orientation).
"""
import json
import math
import os
import re

import numpy as np

SECONDARY = r'J_Sec_|Hair|hair|Bust|Skirt|Coat|Hood|_end$'


# ============================================================================ loading and transforms
def load(dump_dir):
    """-> dict with the info of anim.json and every array of anim.npz."""
    with open(os.path.join(dump_dir, 'anim.json'), encoding='utf-8') as fh:
        info = json.load(fh)
    z = np.load(os.path.join(dump_dir, 'anim.npz'))
    data = {k: z[k] for k in z.files}
    data['info'] = info
    return data


def world_pose(D):
    """(F, B, 4, 4) world matrices of the pose bones."""
    return np.einsum('fij,fbjk->fbik', D['arm_world'].astype(np.float64), D['pose'].astype(np.float64))


def skin_matrices(D):
    """(F, B, 4, 4) linear-blend skinning matrices: armature-space rest -> world posed."""
    inv_rest = np.linalg.inv(D['rest_local'].astype(np.float64))
    return np.einsum('fbij,bjk->fbik', world_pose(D), inv_rest)


def lbs(rest_co, w_idx, w_val, skin):
    """Positions (V, 3) for one frame of skinning matrices (B, 4, 4); rest_co in armature space."""
    co = np.concatenate([rest_co, np.ones((len(rest_co), 1))], axis=1)
    out = np.zeros((len(rest_co), 3))
    for k in range(w_idx.shape[1]):
        idx = w_idx[:, k].astype(np.int64)
        ok = idx >= 0
        M = skin[np.where(ok, idx, 0)]
        p = np.einsum('vij,vj->vi', M, co)[:, :3]
        out += np.where(ok[:, None], p * w_val[:, k:k + 1], 0.0)
    return out


def lbs_normals(rest_no, w_idx, w_val, skin):
    """Rest normals carried by the blended rotation (what the normal 'should' be without dents)."""
    out = np.zeros((len(rest_no), 3))
    for k in range(w_idx.shape[1]):
        idx = w_idx[:, k].astype(np.int64)
        ok = idx >= 0
        R = skin[np.where(ok, idx, 0), :3, :3]
        Rn = np.linalg.inv(R).transpose(0, 2, 1)
        n = np.einsum('vij,vj->vi', Rn, rest_no)
        out += np.where(ok[:, None], n * w_val[:, k:k + 1], 0.0)
    return out / np.maximum(1e-9, np.linalg.norm(out, axis=1, keepdims=True))


def _angle(a, b):
    """Angle in degrees between rows of two direction arrays."""
    a = a / np.maximum(1e-12, np.linalg.norm(a, axis=-1, keepdims=True))
    b = b / np.maximum(1e-12, np.linalg.norm(b, axis=-1, keepdims=True))
    return np.degrees(np.arccos(np.clip(np.sum(a * b, axis=-1), -1.0, 1.0)))


def _rot_angle(R1, R2):
    """Angle in degrees of the rotation R1^T R2 (orthonormalised)."""
    def ortho(R):
        u, _s, vt = np.linalg.svd(R)
        return u @ vt
    Q = np.einsum('...ji,...jk->...ik', ortho(R1), ortho(R2))
    tr = np.trace(Q, axis1=-2, axis2=-1)
    return np.degrees(np.arccos(np.clip((tr - 1.0) / 2.0, -1.0, 1.0)))


def _ortho(R):
    u, _s, vt = np.linalg.svd(R)
    return u @ vt


def _result(check, errors, warnings, metrics, limit=40):
    return {'check': check, 'ok': not errors, 'errors': errors[:limit], 'warnings': warnings[:limit],
            'n_errors': len(errors), 'n_warnings': len(warnings), 'metrics': metrics}


def _cut_mask(frames, cuts):
    """True at transition t (frames[t-1] -> frames[t]) that is a cut or a gap in frame numbers."""
    frames = np.asarray(frames)
    cuts = set(int(c) for c in cuts or ())
    m = np.zeros(len(frames), bool)
    m[0] = True
    for t in range(1, len(frames)):
        m[t] = int(frames[t]) in cuts or int(frames[t]) != int(frames[t - 1]) + 1
    return m


def _local_speed(d, valid, t, win):
    """Usual moving step of a series around t (excluding t-1..t+1, holds skipped): the 90th percentile, so a
    smooth speed-up or the 0 / 2v rhythm of drawings on twos is 'usual', a one-frame pop is not."""
    lo, hi = max(1, t - win), min(len(d), t + win + 1)
    idx = [i for i in range(lo, hi) if abs(i - t) > 1 and valid[i]]
    if not idx:
        return None
    w = d[idx]
    moving = w[w > 1e-5]
    return float(np.percentile(moving, 90)) if len(moving) >= 2 else 0.0


def _poses():
    try:
        from . import poses as PO
    except ImportError:  # run as a script from this folder
        import poses as PO
    return PO


def bone_index(D, role):
    names = D['info']['bones']
    b = _poses().resolve_bone(role, names)
    return names.index(b) if b else None


# ============================================================================ jumps
def jumps(pos, frames, cuts=(), labels=None, min_jump=0.03, ratio=3.0, win=8, hard=0.5):
    """Spikes in a set of point tracks ``pos`` (F, N, 3): a step larger than ``min_jump`` (m) and ``ratio`` times the
    point's own usual moving step around it (``win`` frames each side; the drawing-on-twos rhythm and a fast swing
    foot are part of that usual step), or any step above ``hard`` m. Cuts are skipped. One finding per frame: the
    worst point and how many points jumped with it."""
    pos = np.asarray(pos, np.float64)
    F, N = pos.shape[:2]
    cut = _cut_mask(frames, cuts)
    d = np.zeros((F, N))
    d[1:] = np.linalg.norm(pos[1:] - pos[:-1], axis=-1)
    valid = ~cut
    per_frame, points = {}, set()
    for t, n in np.argwhere((d > min_jump) & valid[:, None]):
        base = _local_speed(d[:, n], valid, int(t), win) or 0.0
        if d[t, n] > hard or d[t, n] > ratio * max(base, 1e-4):
            what = labels[n] if labels is not None else int(n)
            points.add(int(n))
            f = per_frame.setdefault(int(t), {'frame': int(frames[t]), 'what': what, 'step_cm': 0.0, 'usual_cm': 0.0,
                                              'points': 0})
            f['points'] += 1
            if 100 * d[t, n] > f['step_cm']:
                f.update(what=what, step_cm=round(100 * float(d[t, n]), 1), usual_cm=round(100 * base, 2))
    errors = sorted(per_frame.values(), key=lambda e: -e['step_cm'])
    m = {'points': int(N), 'frames': int(F), 'max_step_cm': round(100 * float(d[valid].max()), 1) if valid.any() else 0,
         'flagged_points': len(points), 'flagged_frames': len(errors)}
    return _result('jumps', errors, [], m)


def rotation_jumps(rots, frames, cuts=(), labels=None, hard=60.0, soft=20.0, ratio=3.0, win=8):
    """Orientation pops of bones ``rots`` (F, N, 3, 3): a turn between two frames above ``hard`` degrees is an error;
    above ``soft`` and ``ratio`` times the bone's usual turn per moving step is a warning. Cuts are skipped."""
    rots = np.asarray(rots, np.float64)
    F, N = rots.shape[:2]
    cut = _cut_mask(frames, cuts)
    a = np.zeros((F, N))
    a[1:] = _rot_angle(rots[:-1], rots[1:])
    valid = ~cut
    errors, warnings = [], []
    per = {}
    for t, n in np.argwhere((a > soft) & valid[:, None]):
        loc = _local_speed(a[:, n], valid, int(t), win) or 0.0
        what = labels[n] if labels is not None else int(n)
        f = {'frame': int(frames[t]), 'what': what, 'turn_deg': round(float(a[t, n]), 1), 'usual_deg': round(loc, 2)}
        if a[t, n] > hard:
            errors.append(f)
        elif a[t, n] > ratio * max(loc, 0.5):
            warnings.append(f)
    for n in range(N):
        what = labels[n] if labels is not None else int(n)
        col = a[valid, n] if valid.any() else np.zeros(1)
        per[str(what)] = {'max_deg': round(float(col.max()), 1), 'p99_deg': round(float(np.percentile(col, 99)), 1)}
    errors.sort(key=lambda e: -e['turn_deg'])
    warnings.sort(key=lambda e: -e['turn_deg'])
    return _result('rotation_jumps', errors, warnings, {'per_bone': per, 'hard_deg': hard, 'soft_deg': soft})


# ============================================================================ hinge joints
def character_frame(rest_heads, roles):
    """(left, up, forward) unit vectors of the character at rest from bone heads (dict role -> index)."""
    left = rest_heads[roles['L_UpperArm']] - rest_heads[roles['R_UpperArm']]
    up = rest_heads[roles['head']] - rest_heads[roles['hips']]
    left = left / np.linalg.norm(left)
    up = up - left * np.dot(up, left)
    up = up / np.linalg.norm(up)
    fwd = np.cross(left, up)
    return left, up, fwd


def hinge_axes(rest_local, upper, lower, end, flex_dir):
    """Hinge axis of a joint in the UPPER bone's rest-local frame: perpendicular to the upper limb and to the
    direction the lower limb folds toward (forward for elbows, backward for knees), right-handed so that a correct
    flexion is a positive rotation about it."""
    h = rest_local[:, :3, 3]
    u = h[lower] - h[upper]
    u = u / np.linalg.norm(u)
    ax = np.cross(u, flex_dir)
    ax = ax / max(1e-9, np.linalg.norm(ax))
    R = _ortho(rest_local[upper, :3, :3].astype(np.float64))
    return R.T @ ax


def bend_plane(W, frames, chains, off_warn=20.0, off_err=30.0, reverse_err=10.0, min_flex=12.0,
               knee_warn_cm=4.0, knee_err_cm=8.0):
    """Hinge check per chain ``{'name', 'upper', 'lower', 'end', 'axis_local', optional 'toe'}`` on world pose
    ``W`` (F, B, 4, 4): the lower limb stays perpendicular to the hinge axis (carried by the upper bone, so a twist
    of the upper limb turns the plane with it) and folds to the positive side. With ``toe`` (knees): the knee
    distance from the plane through hip and ankle along the foot direction, the measure the pilot v4 fix uses."""
    errors, warnings, metrics = [], [], {}
    for ch in chains:
        up_i, lo_i, end_i = ch['upper'], ch['lower'], ch['end']
        S = W[:, up_i, :3, 3]
        E = W[:, lo_i, :3, 3]
        Wr = W[:, end_i, :3, 3]
        R = np.stack([_ortho(m) for m in W[:, up_i, :3, :3]])
        h = np.einsum('fij,j->fi', R, ch['axis_local'])
        u = (E - S) / np.maximum(1e-9, np.linalg.norm(E - S, axis=1, keepdims=True))
        v = (Wr - E) / np.maximum(1e-9, np.linalg.norm(Wr - E, axis=1, keepdims=True))
        off = np.degrees(np.arcsin(np.clip(np.abs(np.sum(v * h, axis=1)), 0, 1)))
        flex = np.degrees(np.arctan2(np.sum(np.cross(u, v) * h, axis=1), np.sum(u * v, axis=1)))
        bent = np.abs(flex) > min_flex
        name = ch['name']
        m = {'max_off_deg': round(float(off[bent].max()), 1) if bent.any() else 0.0,
             'min_flex_deg': round(float(flex.min()), 1), 'max_flex_deg': round(float(flex.max()), 1)}
        for t in np.where(bent & (off > off_warn))[0]:
            f = {'frame': int(frames[t]), 'what': name, 'off_plane_deg': round(float(off[t]), 1),
                 'flex_deg': round(float(flex[t]), 1)}
            (errors if off[t] > off_err else warnings).append(f)
        for t in np.where(flex < -reverse_err)[0]:
            errors.append({'frame': int(frames[t]), 'what': name, 'reverse_deg': round(float(-flex[t]), 1)})
        if ch.get('toe') is not None:
            T = W[:, ch['toe'], :3, 3]
            leg = Wr - S
            ln = np.maximum(1e-9, np.linalg.norm(leg, axis=1, keepdims=True))
            ul = leg / ln
            fd = T - Wr
            fd = fd - ul * np.sum(fd * ul, axis=1, keepdims=True)
            fdn = np.linalg.norm(fd, axis=1)
            fd = fd / np.maximum(1e-9, fdn[:, None])
            n = np.cross(ul, fd)
            k = E - S
            dist = np.abs(np.sum(k * n, axis=1))
            ok = fdn > 1e-3
            m['knee_off_plane_cm_max'] = round(100 * float(dist[ok].max()), 1) if ok.any() else None
            m['knee_off_plane_cm_p95'] = round(100 * float(np.percentile(dist[ok], 95)), 1) if ok.any() else None
            for t in np.where(ok & (dist > knee_warn_cm / 100))[0]:
                f = {'frame': int(frames[t]), 'what': name + ' knee vs foot plane', 'off_cm': round(100 * float(dist[t]), 1)}
                (errors if dist[t] > knee_err_cm / 100 else warnings).append(f)
        metrics[name] = m
    errors.sort(key=lambda e: -max(e.get('off_plane_deg', 0), e.get('reverse_deg', 0), 10 * e.get('off_cm', 0)))
    return _result('bend_plane', errors, warnings, metrics)


def default_chains(D):
    """Knees and elbows of a humanoid rig with hinge axes from its rest pose."""
    rest = D['rest_local'].astype(np.float64)
    heads = rest[:, :3, 3]
    r = {k: bone_index(D, k) for k in ('L_UpperArm', 'R_UpperArm', 'head', 'hips')}
    if any(v is None for v in r.values()):
        return []
    _left, _up, fwd = character_frame(heads, r)
    out = []
    for S in 'LR':
        ul, ll, ft, toe = (bone_index(D, '%s_%s' % (S, x)) for x in ('UpperLeg', 'LowerLeg', 'Foot', 'Toes'))
        if None not in (ul, ll, ft):
            out.append({'name': '%s_knee' % S, 'upper': ul, 'lower': ll, 'end': ft, 'toe': toe,
                        'axis_local': hinge_axes(rest, ul, ll, ft, -fwd)})
        ua, la, hd = (bone_index(D, '%s_%s' % (S, x)) for x in ('UpperArm', 'LowerArm', 'Hand'))
        if None not in (ua, la, hd):
            out.append({'name': '%s_elbow' % S, 'upper': ua, 'lower': la, 'end': hd,
                        'axis_local': hinge_axes(rest, ua, la, hd, fwd)})
    return out


# ============================================================================ contacts
def floor_contact(depths, frames, max_depth=0.01, warn_depth=0.004, where=None):
    """``depths``: {probe name: (F,) depth under the floor in m}. Deeper than ``max_depth`` = error."""
    errors, warnings, metrics = [], [], {}
    for name, d in depths.items():
        d = np.asarray(d, np.float64)
        metrics[name] = {'max_cm': round(100 * float(d.max()), 2) if len(d) else 0.0,
                         'frames_over': int((d > max_depth).sum())}
        for t in np.where(d > warn_depth)[0]:
            f = {'frame': int(frames[t]), 'what': name, 'depth_cm': round(100 * float(d[t]), 1)}
            if where is not None and name in where:
                f['at'] = [round(float(c), 3) for c in where[name][t]]
            (errors if d[t] > max_depth else warnings).append(f)
    errors.sort(key=lambda e: -e['depth_cm'])
    warnings.sort(key=lambda e: -e['depth_cm'])
    return _result('floor_contact', errors, warnings, metrics)


def hand_contact(name, gap, pen, weight, frames, w_min=0.5, max_gap=0.02, max_pen=0.012, free_pen=0.012):
    """Hand vs prop: where the expected-contact ``weight`` >= ``w_min`` the nearest hand point is within ``max_gap``
    of the prop and not deeper than ``max_pen`` inside it; elsewhere the hand does not go into it by ``free_pen``."""
    gap = np.asarray(gap, np.float64)
    pen = np.asarray(pen, np.float64)
    w = np.asarray(weight, np.float64)
    errors, warnings = [], []
    hold = w >= w_min
    g = np.where(np.isnan(gap), 9.0, gap)
    for t in np.where(hold & (g > max_gap))[0]:
        errors.append({'frame': int(frames[t]), 'what': name, 'gap_cm': round(100 * float(min(g[t], 9.0)), 1)})
    for t in np.where(pen > max_pen)[0]:
        f = {'frame': int(frames[t]), 'what': name, 'inside_cm': round(100 * float(pen[t]), 1), 'holding': bool(hold[t])}
        if hold[t] or pen[t] > free_pen:
            errors.append(f)
        else:
            warnings.append(f)
    m = {'hold_frames': int(hold.sum()),
         'gap_cm_p95': round(100 * float(np.percentile(g[hold], 95)), 2) if hold.any() else None,
         'gap_cm_max': round(100 * float(g[hold].max()), 2) if hold.any() else None,
         'inside_cm_max': round(100 * float(pen.max()), 2) if len(pen) else 0.0}
    return _result('hand_contact', errors, warnings, {name: m})


def attached_props(props, frames, max_drift=0.02, max_turn=15.0, axis_tol=30.0):
    """``props``: [{'name', 'prop' (F,4,4), 'socket' (F,4,4), optional 'max_gap' (m from the socket origin),
    'axis': [prop axis 'X|Y|Z|-X..', socket axis]}]. The prop keeps its place and orientation on the socket bone."""
    errors, warnings, metrics = [], [], {}
    axes = {'X': 0, 'Y': 1, 'Z': 2}
    for p in props:
        rel = np.einsum('fij,fjk->fik', np.linalg.inv(np.asarray(p['socket'], np.float64)), np.asarray(p['prop'], np.float64))
        t = rel[:, :3, 3]
        R = np.stack([_ortho(m) for m in rel[:, :3, :3]])
        ref_t, ref_R = np.median(t, axis=0), R[0]
        drift = np.linalg.norm(t - ref_t, axis=1)
        turn = _rot_angle(np.broadcast_to(ref_R, R.shape), R)
        gap = np.linalg.norm(t, axis=1)
        name = p['name']
        metrics[name] = {'drift_cm_max': round(100 * float(drift.max()), 2), 'turn_deg_max': round(float(turn.max()), 1),
                         'gap_cm_max': round(100 * float(gap.max()), 2)}
        for k in np.where(drift > max_drift)[0]:
            errors.append({'frame': int(frames[k]), 'what': name, 'drift_cm': round(100 * float(drift[k]), 1)})
        for k in np.where(turn > max_turn)[0]:
            errors.append({'frame': int(frames[k]), 'what': name, 'turn_deg': round(float(turn[k]), 1)})
        if p.get('max_gap') is not None:
            for k in np.where(gap > float(p['max_gap']))[0]:
                errors.append({'frame': int(frames[k]), 'what': name, 'gap_cm': round(100 * float(gap[k]), 1)})
        if p.get('axis'):
            pa, sa = p['axis']
            sp, ss = (-1.0 if pa.startswith('-') else 1.0), (-1.0 if sa.startswith('-') else 1.0)
            v_prop = R[:, :, axes[pa.strip('-')]] * sp          # prop axis in socket space
            v_sock = np.zeros(3)
            v_sock[axes[sa.strip('-')]] = ss
            ang = _angle(v_prop, np.broadcast_to(v_sock, v_prop.shape))
            metrics[name]['axis_deg_max'] = round(float(ang.max()), 1)
            for k in np.where(ang > axis_tol)[0]:
                errors.append({'frame': int(frames[k]), 'what': name, 'axis_off_deg': round(float(ang[k]), 1)})
    return _result('attached_props', errors, warnings, metrics)


# ============================================================================ skin
def normals(actual, expected, frames, max_deg=40.0, max_share=0.003, min_count=4, labels=None):
    """``actual``/``expected`` (F, N, 3): share of vertices whose normal turns > ``max_deg`` away from the skinned
    rest normal (beyond that vertex's own median over the shot); a frame over ``max_share`` (and at least
    ``min_count`` vertices) is an error with its frame number."""
    raw = _angle(np.asarray(actual, np.float64), np.asarray(expected, np.float64))
    # a vertex that is off on most frames is a rest-data / thin-geometry quirk, not a crease of the motion:
    # judge each vertex against its own median over the shot
    base = np.median(raw, axis=0)
    unstable = int((base > max_deg).sum())
    ang = np.maximum(0.0, raw - base)
    bad = ang > max_deg
    share = bad.mean(axis=1)
    errors, warnings = [], []
    for t in range(len(frames)):
        n = int(bad[t].sum())
        if not n:
            continue
        f = {'frame': int(frames[t]), 'what': 'normals', 'verts_over': n, 'share_pct': round(100 * float(share[t]), 2),
             'max_deg': round(float(ang[t].max()), 1)}
        if labels is not None:
            vals, cnt = np.unique(np.asarray(labels)[bad[t]], return_counts=True)
            f['bones'] = [str(v) for v in vals[np.argsort(-cnt)][:3]]
        (errors if share[t] > max_share and n >= min_count else warnings).append(f)
    errors.sort(key=lambda e: -e['share_pct'])
    warnings.sort(key=lambda e: -e['share_pct'])
    m = {'verts': int(ang.shape[1]), 'share_pct_max': round(100 * float(share.max()), 3),
         'deg_p999': round(float(np.percentile(ang, 99.9)), 1), 'always_off_verts': unstable, 'frames_over': int(((share > max_share)
                                                                                      & (bad.sum(1) >= min_count)).sum())}
    return _result('normals', errors, warnings, m)


def _twist(Q, axis):
    """Twist angle (deg, signed) of rotations Q (..., 3, 3) about unit ``axis`` (swing-twist decomposition)."""
    tr = np.trace(Q, axis1=-2, axis2=-1)
    w = np.sqrt(np.maximum(0.0, 1.0 + tr)) / 2.0
    x = (Q[..., 2, 1] - Q[..., 1, 2])
    y = (Q[..., 0, 2] - Q[..., 2, 0])
    z = (Q[..., 1, 0] - Q[..., 0, 1])
    v = np.stack([x, y, z], axis=-1) / np.maximum(1e-9, 4.0 * w[..., None])
    p = np.sum(v * axis, axis=-1)
    return np.degrees(2.0 * np.arctan2(p, w))


def local_rotations(D):
    """(F, B, 3, 3) bone rotation relative to the parent, relative to the rest pose."""
    rest = D['rest_local'].astype(np.float64)
    pose = D['pose'].astype(np.float64)
    par = D['info']['parent']
    out = np.zeros(pose.shape[:2] + (3, 3))
    for b, p in enumerate(par):
        Rr = _ortho(rest[b, :3, :3]) if p < 0 else _ortho(rest[p, :3, :3]).T @ _ortho(rest[b, :3, :3])
        for f in range(pose.shape[0]):
            Rp = _ortho(pose[f, b, :3, :3]) if p < 0 else _ortho(pose[f, p, :3, :3]).T @ _ortho(pose[f, b, :3, :3])
            out[f, b] = Rr.T @ Rp
    return out


def bone_axes(D):
    """Unit direction of each bone toward its first child joint (else head -> tail), in its own rest frame."""
    rest = D['rest_local'].astype(np.float64)
    par = D['info']['parent']
    out = np.zeros((len(par), 3))
    kids = {}
    for b, p in enumerate(par):
        kids.setdefault(p, []).append(b)
    for b in range(len(par)):
        d = None
        for c in kids.get(b, []):
            v = rest[c, :3, 3] - rest[b, :3, 3]
            if np.linalg.norm(v) > 1e-4:
                d = v
                break
        R = _ortho(rest[b, :3, :3])
        local = R.T @ d if d is not None else np.array([0.0, 1.0, 0.0])
        out[b] = local / max(1e-9, np.linalg.norm(local))
    return out


def import_twist(D, max_deg=90.0, skip=SECONDARY, frame_index=0):
    """On the first frame no deform bone is twisted about its own axis by more than ``max_deg`` against rest (an
    animation that came in 'twisted' at import). Top deform bones (hips: the character's facing) are skipped."""
    names = D['info']['bones']
    par = D['info']['parent']
    deform = D['info']['deform']
    L = local_rotations({'rest_local': D['rest_local'], 'pose': D['pose'][frame_index:frame_index + 1],
                         'info': D['info']})[0]
    ax = bone_axes(D)
    errors, metrics = [], {}
    for b, n in enumerate(names):
        if not deform[b] or re.search(skip, n) or par[b] < 0 or not deform[par[b]]:
            continue
        tw = float(_twist(L[b], ax[b]))
        metrics[n] = round(tw, 1)
        if abs(tw) > max_deg:
            errors.append({'frame': int(D['info']['frames'][frame_index]), 'what': n, 'twist_deg': round(tw, 1)})
    errors.sort(key=lambda e: -abs(e['twist_deg']))
    worst = sorted(metrics.items(), key=lambda kv: -abs(kv[1]))[:5]
    return _result('import_twist', errors, [], {'worst': dict(worst), 'max_deg': max_deg})


def static(tracks, frames, min_range=0.01, still_sec=None, fps=24.0):
    """``tracks``: {name: (F, N, 3) point tracks of a thing that should move}. Range under ``min_range`` m over
    the shot is an error ("should move but static"); with ``still_sec``, a run of identical frames that long is a
    warning."""
    errors, warnings, metrics = [], [], {}
    for name, pos in tracks.items():
        pos = np.asarray(pos, np.float64)
        rng = float(np.linalg.norm(pos - pos[:1], axis=-1).max()) if len(pos) else 0.0
        step = np.zeros(len(pos))
        step[1:] = np.linalg.norm(pos[1:] - pos[:-1], axis=-1).max(axis=1)
        run, best, best_at = 0, 0, None
        for t in range(1, len(pos)):
            run = run + 1 if step[t] < 1e-5 else 0
            if run > best:
                best, best_at = run, t - run + 1
        metrics[name] = {'range_cm': round(100 * rng, 2), 'longest_still_frames': int(best)}
        if rng < min_range:
            errors.append({'frame': int(frames[0]), 'what': name, 'range_cm': round(100 * rng, 2)})
        elif still_sec and best >= still_sec * fps:
            warnings.append({'frame': int(frames[best_at]), 'what': name, 'still_sec': round(best / fps, 2)})
    return _result('static', errors, warnings, metrics)


# ============================================================================ weights
def _seg_dist(p, a, b):
    ab = b - a
    t = np.clip(((p - a) @ ab) / max(1e-12, ab @ ab), 0.0, 1.0)
    return np.linalg.norm(p - (a + t[:, None] * ab), axis=1)


TORSO = r'Hips|Spine|Chest|Neck|Shoulder'


def weight_leak(rest_co, w_idx, w_val, rest_local, parent, names, deform, angle=45.0, max_move=0.03,
                margin=0.12, margin_k=0.5, skip=SECONDARY, info_only=TORSO):
    """Rotate each deform bone by +-``angle`` about the two axes across it and about its own axis; a vertex outside
    the bone's zone (its nearest bone segment is neither this bone nor below it in the chain) and farther than
    ``margin`` (or ``margin_k`` bone lengths) from every segment of that chain that moves more than ``max_move`` m is
    a leak (an arm pulling the belly). Smooth blending at a joint or between fingers stays within the margin.
    Torso bones (``info_only``) blend widely by design (VRoid): measured and listed, not judged."""
    rest_co = np.asarray(rest_co, np.float64)
    heads = rest_local[:, :3, 3].astype(np.float64)
    kids = {}
    for b, p in enumerate(parent):
        kids.setdefault(p, []).append(b)
    used = sorted({int(i) for i in np.unique(w_idx) if i >= 0})
    # bone segments: head -> first child head (else a short stub along the bone's axis)
    tails = heads.copy()
    for b in range(len(names)):
        ks = [c for c in kids.get(b, []) if np.linalg.norm(heads[c] - heads[b]) > 1e-4]
        tails[b] = heads[ks[0]] if ks else heads[b] + rest_local[b, :3, 1] * 0.05
    segs = [b for b in used if deform[b]]
    dist = np.stack([_seg_dist(rest_co, heads[b], tails[b]) for b in segs], axis=1)
    near = np.asarray(segs)[np.argmin(dist, axis=1)]

    def below(b):
        out, stack = {b}, [b]
        while stack:
            for c in kids.get(stack.pop(), []):
                if c not in out:
                    out.add(c)
                    stack.append(c)
        return out

    errors, metrics = [], {}
    for b in segs:
        if re.search(skip, names[b]):
            continue
        sub = below(b)
        wsum = np.zeros(len(rest_co))
        for k in range(w_idx.shape[1]):
            wsum += np.where(np.isin(w_idx[:, k], list(sub)), w_val[:, k], 0.0)
        moving = wsum > 1e-4
        if not moving.any():
            continue
        L = float(np.linalg.norm(tails[b] - heads[b]))
        zone = np.isin(near, list(sub))
        chain = np.min(np.stack([_seg_dist(rest_co, heads[c], tails[c]) for c in sub if deform[c]] or
                                [_seg_dist(rest_co, heads[b], tails[b])], axis=1), axis=1)
        far = chain > max(margin, margin_k * L)
        out = moving & ~zone & far
        if not out.any():
            metrics[names[b]] = 0.0
            continue
        R0 = _ortho(rest_local[b, :3, :3].astype(np.float64))
        axis_b = (tails[b] - heads[b]) / max(1e-9, L)
        a1 = R0[:, 0] - axis_b * (R0[:, 0] @ axis_b)
        a1 = a1 / max(1e-9, np.linalg.norm(a1))
        a2 = np.cross(axis_b, a1)
        q = rest_co[out] - heads[b]
        worst = np.zeros(int(out.sum()))
        for ax in (a1, a2, axis_b):
            for sgn in (1.0, -1.0):
                Rm = _axis_angle(ax, sgn * math.radians(angle))
                worst = np.maximum(worst, wsum[out] * np.linalg.norm(q @ Rm.T - q, axis=1))
        mv = float(worst.max())
        metrics[names[b]] = round(100 * mv, 2)
        bad = worst > max_move
        if bad.any() and not re.search(info_only, names[b]):
            j = int(np.argmax(worst))
            errors.append({'frame': None, 'what': names[b], 'verts': int(bad.sum()), 'max_move_cm': round(100 * mv, 1),
                           'worst_vertex': int(np.where(out)[0][j]),
                           'zone_of_worst': names[int(near[np.where(out)[0][j]])],
                           'at': [round(float(c), 3) for c in rest_co[np.where(out)[0][j]]]})
    errors.sort(key=lambda e: -e['max_move_cm'])
    top = dict(sorted(metrics.items(), key=lambda kv: -kv[1])[:8])
    return _result('weight_leak', errors, [], {'bones': len(segs), 'worst_cm': top})


def _axis_angle(axis, ang):
    a = np.asarray(axis, np.float64)
    a = a / max(1e-12, np.linalg.norm(a))
    K = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    return np.eye(3) + math.sin(ang) * K + (1 - math.cos(ang)) * K @ K
