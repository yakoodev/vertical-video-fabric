# -*- coding: utf-8 -*-
"""Animation checks (tools/model_check/anim_checks.py): every check on a clean synthetic rig passes and on a
deliberately broken one (the negative control) fails."""
import json
import math

import numpy as np
import pytest

from tools.model_check import anim_analyze as AA
from tools.model_check import anim_checks as AC

# Z up, the character faces -Y, its left is +X (VRoid in Blender after glTF import)
BONES = [('Root', None, (0, 0, 0), False),
         ('J_Bip_C_Hips', 'Root', (0, 0, 1.0), True),
         ('J_Bip_C_Spine', 'J_Bip_C_Hips', (0, 0, 1.15), True),
         ('J_Bip_C_Head', 'J_Bip_C_Spine', (0, 0, 1.6), True),
         ('J_Bip_L_UpperLeg', 'J_Bip_C_Hips', (0.1, 0, 0.95), True),
         ('J_Bip_L_LowerLeg', 'J_Bip_L_UpperLeg', (0.1, 0, 0.5), True),
         ('J_Bip_L_Foot', 'J_Bip_L_LowerLeg', (0.1, 0, 0.08), True),
         ('J_Bip_L_ToeBase', 'J_Bip_L_Foot', (0.1, -0.14, 0.02), True),
         ('J_Bip_R_UpperLeg', 'J_Bip_C_Hips', (-0.1, 0, 0.95), True),
         ('J_Bip_R_LowerLeg', 'J_Bip_R_UpperLeg', (-0.1, 0, 0.5), True),
         ('J_Bip_R_Foot', 'J_Bip_R_LowerLeg', (-0.1, 0, 0.08), True),
         ('J_Bip_R_ToeBase', 'J_Bip_R_Foot', (-0.1, -0.14, 0.02), True),
         ('J_Bip_L_UpperArm', 'J_Bip_C_Spine', (0.2, 0, 1.45), True),
         ('J_Bip_L_LowerArm', 'J_Bip_L_UpperArm', (0.45, 0, 1.45), True),
         ('J_Bip_L_Hand', 'J_Bip_L_LowerArm', (0.7, 0, 1.45), True),
         ('J_Bip_R_UpperArm', 'J_Bip_C_Spine', (-0.2, 0, 1.45), True),
         ('J_Bip_R_LowerArm', 'J_Bip_R_UpperArm', (-0.45, 0, 1.45), True),
         ('J_Bip_R_Hand', 'J_Bip_R_LowerArm', (-0.7, 0, 1.45), True)]
NAMES = [b[0] for b in BONES]
IX = {n: i for i, n in enumerate(NAMES)}
PARENT = [IX[p] if p else -1 for _n, p, _h, _d in BONES]
DEFORM = [d for *_x, d in BONES]


def rot(axis, deg):
    a = np.asarray(axis, float)
    a /= np.linalg.norm(a)
    K = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    t = math.radians(deg)
    return np.eye(3) + math.sin(t) * K + (1 - math.cos(t)) * K @ K


def rest_local():
    R = np.zeros((len(BONES), 4, 4))
    for i, (_n, _p, h, _d) in enumerate(BONES):
        R[i] = np.eye(4)
        R[i, :3, 3] = h
    return R


def fk(local_rots, root_xy=(0.0, 0.0)):
    """local_rots: {bone: 3x3} -> (B, 4, 4) armature-space pose."""
    rest = rest_local()
    P = np.zeros_like(rest)
    for i in range(len(BONES)):
        L = np.eye(4)
        L[:3, :3] = local_rots.get(NAMES[i], np.eye(3))
        if PARENT[i] < 0:
            P[i] = rest[i] @ L
            P[i, :2, 3] += root_xy
        else:
            P[i] = P[PARENT[i]] @ np.linalg.inv(rest[PARENT[i]]) @ rest[i] @ L
    return P


def walk(n=48, knee=lambda t: 20 + 15 * math.sin(t / 4), extra=None):
    """A clean walk: root moves forward, knees flex about their hinge (X axis: rotation about +X brings the shin back,
    i.e. toward +Y), elbows flex forward (about the arm's hinge), feet turn smoothly."""
    poses = []
    for t in range(n):
        r = {'J_Bip_L_UpperLeg': rot((1, 0, 0), -15 * math.sin(t / 4)),
             'J_Bip_L_LowerLeg': rot((1, 0, 0), knee(t)),
             'J_Bip_R_LowerLeg': rot((1, 0, 0), knee(t + 8)),
             'J_Bip_L_Foot': rot((1, 0, 0), -5 * math.sin(t / 4)),
             'J_Bip_L_LowerArm': rot((0, 0, -1), 20 + 10 * math.sin(t / 5)),     # left forearm folds toward -Y
             'J_Bip_R_LowerArm': rot((0, 0, 1), 20 + 10 * math.sin(t / 5))}
        if extra:
            extra(t, r)
        poses.append(fk(r, root_xy=(0.0, -0.02 * t)))
    return np.stack(poses)


def dump(pose):
    F = len(pose)
    return {'rest_local': rest_local(), 'pose': pose, 'arm_world': np.broadcast_to(np.eye(4), (F, 4, 4)).copy(),
            'info': {'bones': NAMES, 'parent': PARENT, 'deform': DEFORM, 'frames': list(range(1, F + 1)), 'cuts': []}}


FR = list(range(1, 49))


# ============================================================================ jumps
def test_jumps_find_a_17cm_hip_pop_and_ignore_twos_and_cuts():
    t = np.arange(48)
    smooth = np.stack([np.zeros(48), -0.02 * t, np.ones(48)], axis=1)[:, None, :]
    twos = smooth.copy()
    twos[1::2] = twos[0:-1:2]                                   # on twos: 0, 4 cm, 0, 4 cm ...
    assert AC.jumps(twos, FR)['ok']
    cut = twos.copy()
    cut[30:] += [0.0, 0.8, 0.0]                                 # the next shot starts elsewhere
    assert AC.jumps(cut, FR, cuts=[31])['ok']
    pop = twos.copy()
    pop[20] += [0.17, 0.0, 0.0]                                 # negative control: hip jumps 17 cm for one frame
    r = AC.jumps(pop, FR, labels=['hips'])
    assert not r['ok'] and r['errors'][0]['what'] == 'hips' and r['errors'][0]['step_cm'] >= 16


def test_rotation_jumps_catch_a_174_degree_foot_spin():
    clean = walk()
    W = AC.world_pose(dump(clean))
    foot = IX['J_Bip_L_Foot']
    assert AC.rotation_jumps(W[:, [foot], :3, :3], FR, labels=['L_Foot'])['ok']
    bad = walk(extra=lambda t, r: r.update({'J_Bip_L_Foot': rot((0, 0, 1), 174)}) if t == 25 else None)
    r = AC.rotation_jumps(AC.world_pose(dump(bad))[:, [foot], :3, :3], FR, labels=['L_Foot'])
    assert not r['ok'] and r['errors'][0]['turn_deg'] > 150 and r['errors'][0]['frame'] in (26, 27)


# ============================================================================ hinge
def test_bend_plane_clean_walk_passes_and_side_or_reverse_knee_fails():
    D = dump(walk())
    chains = AC.default_chains(D)
    assert {c['name'] for c in chains} == {'L_knee', 'R_knee', 'L_elbow', 'R_elbow'}
    r = AC.bend_plane(AC.world_pose(D), FR, chains)
    assert r['ok'], r['errors'][:3]
    assert r['metrics']['L_knee']['knee_off_plane_cm_max'] < 1.0
    # negative control 1: the knee bends sideways (about the forward axis)
    side = dump(walk(extra=lambda t, r: r.update({'J_Bip_L_LowerLeg': rot((0, 1, 0), 45)}) if 10 <= t < 14 else None))
    rs = AC.bend_plane(AC.world_pose(side), FR, AC.default_chains(side))
    assert not rs['ok'] and any(e['what'].startswith('L_knee') and e['frame'] in (11, 12, 13, 14) for e in rs['errors'])
    # negative control 2: the knee bends backwards (hyperextension)
    rev = dump(walk(extra=lambda t, r: r.update({'J_Bip_L_LowerLeg': rot((1, 0, 0), -30)}) if t == 5 else None))
    rr = AC.bend_plane(AC.world_pose(rev), FR, AC.default_chains(rev))
    assert any(e.get('reverse_deg', 0) > 25 and e['frame'] == 6 for e in rr['errors'])
    # negative control 3: the elbow bends the wrong way
    el = dump(walk(extra=lambda t, r: r.update({'J_Bip_L_LowerArm': rot((0, 0, 1), 40)}) if t == 7 else None))
    re_ = AC.bend_plane(AC.world_pose(el), FR, AC.default_chains(el))
    assert any(e['what'] == 'L_elbow' and e.get('reverse_deg', 0) > 30 for e in re_['errors'])


# ============================================================================ contacts
def test_floor_contact_flags_a_foot_3cm_inside_a_step():
    ok = {'L_foot': np.full(48, 0.002), 'R_shin': np.zeros(48)}
    assert AC.floor_contact(ok, FR)['ok']
    bad = {'L_foot': np.full(48, 0.002), 'R_shin': np.zeros(48)}
    bad['R_shin'][12] = 0.03
    r = AC.floor_contact(bad, FR)
    assert not r['ok'] and r['errors'][0] == {'frame': 13, 'what': 'R_shin', 'depth_cm': 3.0}


def test_hand_contact_needs_touch_without_going_in():
    w = np.r_[np.zeros(10), np.ones(38)]
    gap = np.full(48, 0.005)
    pen = np.zeros(48)
    assert AC.hand_contact('rail', gap, pen, w, FR)['ok']
    off = gap.copy()
    off[20] = 0.06                                              # negative: the hand floats 6 cm above the rail
    r = AC.hand_contact('rail', off, pen, w, FR)
    assert not r['ok'] and r['errors'][0]['gap_cm'] == 6.0
    inside = pen.copy()
    inside[30] = 0.025                                          # negative: fingers 2.5 cm inside the rail
    assert not AC.hand_contact('rail', gap, inside, w, FR)['ok']


def test_attached_prop_drift_and_flip_are_caught():
    F = 48
    sock = np.stack([np.eye(4) for _ in range(F)])
    sock[:, :3, 3] = np.c_[np.linspace(0, 1, F), np.zeros(F), np.ones(F)]
    for f in range(F):
        sock[f, :3, :3] = rot((0, 0, 1), f * 3)
    off = np.eye(4)
    off[:3, 3] = [0.0, 0.03, 0.0]
    prop = np.einsum('fij,jk->fik', sock, off)
    good = [{'name': 'phone', 'prop': prop, 'socket': sock, 'axis': ['Z', 'Z']}]
    assert AC.attached_props(good, FR)['ok']
    drift = prop.copy()
    drift[25:, :3, 3] += [0.0, 0.0, 0.06]                        # negative: the phone slides 6 cm off the hand
    assert not AC.attached_props([{'name': 'phone', 'prop': drift, 'socket': sock}], FR)['ok']
    flip = prop.copy()
    flip[:, :3, :3] = np.einsum('fij,jk->fik', flip[:, :3, :3], rot((1, 0, 0), 180))   # negative: sword turned back
    r = AC.attached_props([{'name': 'sword', 'prop': flip, 'socket': sock, 'axis': ['Z', 'Z']}], FR)
    assert not r['ok'] and any('axis_off_deg' in e for e in r['errors'])


# ============================================================================ skin
def limb_mesh():
    """Points along both arms (weighted to them) and a belly patch (weighted to the spine)."""
    pts, idx, val = [], [], []
    for x in np.linspace(0.25, 0.68, 12):
        for a in range(8):
            ang = 2 * math.pi * a / 8
            pts.append([x, 0.04 * math.cos(ang), 1.45 + 0.04 * math.sin(ang)])
            b = 'J_Bip_L_UpperArm' if x < 0.45 else 'J_Bip_L_LowerArm'
            idx.append([IX[b], -1, -1, -1])
            val.append([1.0, 0, 0, 0])
    for x in np.linspace(-0.12, 0.12, 6):
        for z in np.linspace(1.05, 1.3, 6):
            pts.append([x, -0.12, z])
            idx.append([IX['J_Bip_C_Spine'], -1, -1, -1])
            val.append([1.0, 0, 0, 0])
    return np.array(pts), np.array(idx, np.int16), np.array(val, np.float32)


def test_weight_leak_names_the_arm_that_pulls_the_belly():
    co, idx, val = limb_mesh()
    rest = rest_local()
    r = AC.weight_leak(co, idx, val, rest, PARENT, NAMES, DEFORM)
    assert r['ok'], r['errors']
    belly = co[:, 1] < -0.1
    idx2, val2 = idx.copy(), val.copy()
    idx2[belly, 1] = IX['J_Bip_L_UpperArm']                     # negative control: 30 % of the arm on the belly
    val2[belly, 0], val2[belly, 1] = 0.7, 0.3
    r2 = AC.weight_leak(co, idx2, val2, rest, PARENT, NAMES, DEFORM)
    assert not r2['ok'] and r2['errors'][0]['what'] == 'J_Bip_L_UpperArm'
    assert r2['errors'][0]['verts'] >= 20 and r2['errors'][0]['zone_of_worst'] == 'J_Bip_C_Spine'


def test_lbs_and_normals_rest_is_identity_and_a_crease_is_caught():
    co, idx, val = limb_mesh()
    rest = rest_local()
    skin = np.einsum('bij,bjk->bik', fk({'J_Bip_L_UpperArm': rot((0, 1, 0), 30)}), np.linalg.inv(rest))
    p = AC.lbs(co, idx, val, skin)
    arm = idx[:, 0] == IX['J_Bip_L_UpperArm']
    assert np.allclose(p[~arm & (idx[:, 0] == IX['J_Bip_C_Spine'])], co[idx[:, 0] == IX['J_Bip_C_Spine']])
    no = np.tile([0.0, 0.0, 1.0], (len(co), 1))
    exp = AC.lbs_normals(no, idx, val, skin)
    F = 10
    act = np.broadcast_to(exp, (F,) + exp.shape).copy()
    expected = act.copy()
    assert AC.normals(act, expected, list(range(F)))['ok']
    act[4, :6] = AC.lbs_normals(np.tile([1.0, 0.0, 0.0], (6, 1)), idx[:6], val[:6], skin)   # negative: 90 deg chips
    r = AC.normals(act, expected, list(range(F)), labels=[NAMES[i] for i in idx[:, 0]])
    assert not r['ok'] and r['errors'][0]['frame'] == 4 and r['errors'][0]['verts_over'] == 6


def test_import_twist_catches_a_bone_rolled_120_degrees_on_frame_0():
    D = dump(walk())
    assert AC.import_twist(D)['ok']
    bad = dump(walk(extra=lambda t, r: r.update({'J_Bip_L_UpperArm': rot((1, 0, 0), 120)}) if t == 0 else None))
    r = AC.import_twist(bad)
    assert not r['ok'] and r['errors'][0]['what'] == 'J_Bip_L_UpperArm' and abs(r['errors'][0]['twist_deg']) > 110
    ok = dump(walk(extra=lambda t, r: r.update({'J_Bip_L_UpperArm': rot((1, 0, 0), 40)}) if t == 0 else None))
    assert AC.import_twist(ok)['ok']


def test_static_character_that_should_move():
    D = dump(walk())
    W = AC.world_pose(D)
    assert AC.static({'hero': W[:, :, :3, 3]}, FR)['ok']
    frozen = np.broadcast_to(W[:1], W.shape)
    r = AC.static({'horse': frozen[:, :, :3, 3]}, FR)
    assert not r['ok'] and r['errors'][0]['what'] == 'horse'


# ============================================================================ end to end (analyzer + real-data perturbations)
def synthetic_dump(tmp_path, pose):
    co, idx, val = limb_mesh()
    F = len(pose)
    D = dump(pose)
    rest = rest_local()
    skin = np.einsum('fbij,bjk->fbik', AC.world_pose(D), np.linalg.inv(rest))
    no = np.tile([0.0, 0.0, 1.0], (len(co), 1))
    ev_co = np.stack([AC.lbs(co, idx, val, skin[t]) for t in range(F)])
    ev_no = np.stack([AC.lbs_normals(no, idx, val, skin[t]) for t in range(F)])
    arrays = {'rest_local': rest, 'pose': pose, 'arm_world': D['arm_world'], 'bone_len': np.ones(len(NAMES)),
              'm0_rest_co': co, 'm0_rest_no': no, 'm0_w_idx': idx, 'm0_w_val': val, 'm0_tri': np.zeros((0, 3), int),
              'm0_sub': np.arange(len(co)), 'm0_ev_co': ev_co, 'm0_ev_no': ev_no,
              'floor_L_foot': np.zeros(F), 'floor_L_foot_at': np.zeros((F, 3))}
    np.savez_compressed(tmp_path / 'anim.npz', **arrays)
    info = {**D['info'], 'meshes': [{'name': 'Body', 'verts': len(co)}], 'fps': 24, 'armature': 'hero', 'floor': True,
            'contacts': [], 'props': []}
    (tmp_path / 'anim.json').write_text(json.dumps(info), 'utf-8')
    return tmp_path


def test_analyzer_passes_clean_and_each_real_data_negative_fails(tmp_path):
    d = synthetic_dump(tmp_path, walk())
    rep = AA.run(AC.load(str(d)))
    bad = [r['check'] for r in rep['checks'] if not r['ok']]
    assert rep['ok'], (bad, [r['errors'][:2] for r in rep['checks'] if not r['ok']])
    assert {r['check'] for r in rep['checks']} >= {'jumps_bones', 'jumps_vertices', 'rotation_jumps', 'bend_plane',
                                                   'floor_contact', 'normals_Body', 'import_twist', 'static',
                                                   'weight_leak'}
    by = lambda rep_, name: next(r for r in rep_['checks'] if r['check'] == name)  # noqa: E731
    assert by(rep, 'normals_Body')['metrics']['skin_selfcheck_p99_cm'] < 0.01
    for kind, check in (('freeze', 'static'), ('foot_spin', 'rotation_jumps')):
        neg = AA.run(AA.perturb(AC.load(str(d)), kind))
        assert not by(neg, check)['ok'], kind
    assert 'FAIL' in AA.summary_text(AA.run(AA.perturb(AC.load(str(d)), 'freeze')))


@pytest.mark.parametrize('deg,ok', [(30, True), (95, False)])
def test_twist_threshold(deg, ok):
    D = dump(walk(extra=lambda t, r: r.update({'J_Bip_R_LowerLeg': rot((0, 0, 1), deg)}) if t == 0 else None))
    assert AC.import_twist(D)['ok'] is ok


def test_normals_ignore_a_vertex_that_is_always_off_but_catch_a_new_crease():
    F, N = 12, 400
    exp = np.tile([0.0, 0.0, 1.0], (F, N, 1))
    act = exp.copy()
    act[:, :3] = [1.0, 0.0, 0.0]          # thin geometry: these 3 normals are off on every frame (not the motion)
    r = AC.normals(act, exp, list(range(F)))
    assert r['ok'] and r['metrics']['always_off_verts'] == 3
    act[7, 10:20] = [0.0, 1.0, 0.0]       # negative control: a crease on frame 7
    r = AC.normals(act, exp, list(range(F)))
    assert not r['ok'] and [e['frame'] for e in r['errors']] == [7]


def test_jumps_report_one_finding_per_frame():
    t = np.arange(48)
    base = np.stack([np.zeros(48), -0.01 * t, np.ones(48)], axis=1)
    pts = np.stack([base + [0.01 * k, 0, 0] for k in range(5)], axis=1)
    pts[30] += [0.0, 0.0, 0.2]            # the whole limb pops 20 cm
    r = AC.jumps(pts, FR)
    # out and back: two transitions, each one finding with all 5 points
    assert sorted(e['frame'] for e in r['errors']) == [31, 32] and all(e['points'] == 5 for e in r['errors'])
