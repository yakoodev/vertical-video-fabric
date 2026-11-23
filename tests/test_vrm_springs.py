# -*- coding: utf-8 -*-
"""tools/vrm_springs: rig edits keep the mesh, the rebuilt springs follow the rules (root below the scalp, stiff
fringe, own chain for a ponytail, colliders that do not push anything at rest), and the analysis flags broken
springs (negative controls) while passing clean motion."""
import json
import math

import numpy as np
import pytest

from tools.vrm_springs import analyze as AN
from tools.vrm_springs import clips as CL
from tools.vrm_springs import springs as S
from tools.vrm_springs.glb import Glb


# ----------------------------------------------------------------------------- a tiny VRoid-like VRM 0.x
def _sphere(c, r, n=10, m=14):
    pts, tris = [], []
    for i in range(n + 1):
        th = math.pi * i / n
        for j in range(m):
            ph = 2 * math.pi * j / m
            pts.append([c[0] + r * math.sin(th) * math.cos(ph), c[1] + r * math.cos(th), c[2] + r * math.sin(th) * math.sin(ph)])
    for i in range(n):
        for j in range(m):
            a, b = i * m + j, i * m + (j + 1) % m
            c2, d = a + m, b + m
            tris += [[a, c2, b], [b, c2, d]]
    P = np.array(pts, np.float32)
    N = (P - np.array(c, np.float32)) / r
    return P, N.astype(np.float32), np.array(tris, np.uint32)


def make_vrm(ponytail=False):
    """Root/Hips/Spine/Chest/UpperChest/Neck/Head + shoulders/arms; hair: front chain + back chain (3 bones each);
    meshes Face (head sphere, skin), Body (torso sphere), Hair (strips weighted to the chains, + a ponytail)."""
    nodes = []

    def add(name, pos, parent=None):
        nodes.append({'name': name, 'translation': list(pos), 'children': []})
        i = len(nodes) - 1
        if parent is not None:
            ppos = np.array(WP[parent])
            nodes[i]['translation'] = list(np.array(pos) - ppos)
            nodes[parent]['children'].append(i)
        WP[i] = pos
        return i
    WP = {}
    root = add('Root', (0, 0, 0))
    hips = add('J_Bip_C_Hips', (0, 0.9, 0), root)
    spine = add('J_Bip_C_Spine', (0, 1.0, 0), hips)
    chest = add('J_Bip_C_Chest', (0, 1.15, 0), spine)
    uc = add('J_Bip_C_UpperChest', (0, 1.28, 0), chest)
    neck = add('J_Bip_C_Neck', (0, 1.40, 0), uc)
    head = add('J_Bip_C_Head', (0, 1.48, 0), neck)
    ls = add('J_Bip_L_Shoulder', (0.04, 1.36, 0), uc)
    la = add('J_Bip_L_UpperArm', (0.16, 1.36, 0), ls)
    rs = add('J_Bip_R_Shoulder', (-0.04, 1.36, 0), uc)
    ra = add('J_Bip_R_UpperArm', (-0.16, 1.36, 0), rs)
    # front (fringe) chain: root on the scalp in front, hanging; back chain at the back
    f1 = add('J_Sec_Hair1_01', (0, 1.62, -0.085), head)
    f2 = add('J_Sec_Hair2_01', (0, 1.59, -0.095), f1)
    f3 = add('J_Sec_Hair3_01', (0, 1.56, -0.10), f2)
    b1 = add('J_Sec_Hair1_02', (0, 1.58, 0.09), head)
    b2 = add('J_Sec_Hair2_02', (0, 1.54, 0.10), b1)
    b3 = add('J_Sec_Hair3_02', (0, 1.50, 0.105), b2)
    for n in nodes:
        if not n['children']:
            del n['children']
    joints = list(range(len(nodes)))
    g = Glb({'asset': {'version': '2.0'}, 'nodes': nodes, 'meshes': [], 'materials': [], 'accessors': [],
             'bufferViews': [], 'buffers': [{'byteLength': 0}], 'skins': [], 'scenes': [{'nodes': [root]}],
             'extensionsUsed': ['VRM']}, b'')
    ibm = []
    for j in joints:
        M = np.eye(4)
        M[:3, 3] = -np.array(WP[j])
        ibm.append(M.T.reshape(16))
    ibm = np.array(ibm, np.float32)

    def mesh(name, mat, P, N, T, J, W):
        g.json['materials'].append({'name': mat})
        acc = dict(POSITION=g.add_accessor(P, minmax=True), NORMAL=g.add_accessor(N),
                   JOINTS_0=g.add_accessor(J.astype(np.uint16)), WEIGHTS_0=g.add_accessor(W.astype(np.float32)))
        ia = g.add_accessor(T.reshape(-1).astype(np.uint32))
        g.json['meshes'].append({'name': name, 'primitives': [{'attributes': acc, 'indices': ia,
                                                               'material': len(g.json['materials']) - 1}]})
        g.json['skins'].append({'joints': joints, 'inverseBindMatrices': g.add_accessor(ibm)})
        g.json['nodes'].append({'name': name, 'mesh': len(g.json['meshes']) - 1, 'skin': len(g.json['skins']) - 1})
        g.json['scenes'][0]['nodes'].append(len(g.json['nodes']) - 1)

    def one(n, j):
        J = np.zeros((n, 4), int)
        J[:, 0] = j
        W = np.zeros((n, 4))
        W[:, 0] = 1
        return J, W
    P, N, T = _sphere((0, 1.58, 0.0), 0.085)
    mesh('Face', 'N00_000_00_Face_00_SKIN (Instance)', P, N, T, *one(len(P), head))
    P, N, T = _sphere((0, 1.15, 0.0), 0.16)
    mesh('Body', 'N00_000_00_Body_00_SKIN (Instance)', P, N, T, *one(len(P), uc))
    # hair strips: fringe in front of the face, back hair, (ponytail hanging behind the neck)
    pts, Js = [], []
    for z0, chain in ((-0.105, (f1, f2, f3)), (0.11, (b1, b2, b3))):
        for k in range(12):
            y = 1.64 - 0.012 * k
            for x in (-0.03, 0.03):
                pts.append([x, y, z0])
                Js.append(chain[min(2, k // 4)])
    if ponytail:
        for k in range(20):
            for x in (-0.012, 0.012):
                pts.append([x, 1.46 - 0.008 * k, 0.12])
                Js.append(head)
    P = np.array(pts, np.float32)
    N = np.tile(np.array([0, 0, 1], np.float32), (len(P), 1))
    T = np.array([[i, i + 1, i + 2] for i in range(0, len(P) - 2, 2)], np.uint32)
    J = np.zeros((len(P), 4), int)
    J[:, 0] = Js
    W = np.zeros((len(P), 4))
    W[:, 0] = 1
    mesh('Hair', 'N00_000_Hair_00_HAIR (Instance)', P, N, T, J, W)
    human = [{'bone': b, 'node': i} for b, i in (('hips', hips), ('spine', spine), ('chest', chest), ('upperChest', uc),
                                                   ('neck', neck), ('head', head), ('leftUpperArm', la),
                                                   ('rightUpperArm', ra))]
    g.json['extensions'] = {'VRM': {'humanoid': {'humanBones': human}, 'secondaryAnimation': {
        'boneGroups': [{'comment': 'Hair', 'stiffiness': 0.75, 'gravityPower': 0, 'gravityDir': {'x': 0, 'y': -1, 'z': 0},
                        'dragForce': 0.4, 'center': -1, 'hitRadius': 0.002, 'bones': [f1], 'colliderGroups': []},
                       {'comment': 'Hair', 'stiffiness': 0.75, 'gravityPower': 0, 'gravityDir': {'x': 0, 'y': -1, 'z': 0},
                        'dragForce': 0.4, 'center': -1, 'hitRadius': 0.002, 'bones': [b1], 'colliderGroups': []}],
        'colliderGroups': []}}}
    return Glb.load_bytes(g.to_bytes()) if hasattr(Glb, 'load_bytes') else _reload(g)


def _reload(g):
    import tempfile
    import os
    fd, p = tempfile.mkstemp(suffix='.vrm')
    os.close(fd)
    g.save(p)
    out = Glb.load(p)
    os.remove(p)
    return out


def mesh_bytes(g):
    """bytes of every POSITION / NORMAL / TEXCOORD / indices accessor (the look)"""
    out = []
    for me in g.json['meshes']:
        for pr in me['primitives']:
            for k in ('POSITION', 'NORMAL'):
                out.append(g.accessor(pr['attributes'][k]).tobytes())
            out.append(g.accessor(pr['indices']).tobytes())
    return out


# ----------------------------------------------------------------------------- glb
def test_glb_roundtrip_keeps_everything():
    g = make_vrm()
    before = mesh_bytes(g)
    g2 = _reload(g)
    assert mesh_bytes(g2) == before
    assert g2.json['nodes'] == g.json['nodes']


# ----------------------------------------------------------------------------- the fix
def test_fix_reroots_and_stiffens_fringe_and_keeps_mesh():
    g = make_vrm()
    before = mesh_bytes(g)
    rep = S.fix(g)
    sa = g.vrm['secondaryAnimation']
    names = {g.json['nodes'][r]['name']: grp for grp in sa['boneGroups'] for r in grp['bones']}
    # the root segment stays on the head: groups start one joint lower
    assert set(names) == {'J_Sec_Hair2_01', 'J_Sec_Hair2_02'}
    front, back = names['J_Sec_Hair2_01'], names['J_Sec_Hair2_02']
    assert front['comment'] == 'Hair_front' and back['comment'] == 'Hair_back'
    assert front['stiffiness'] > 3 * back['stiffiness']           # the fringe is far stiffer
    assert front['gravityPower'] < back['gravityPower']
    assert back['gravityPower'] > 0 and back['dragForce'] >= 0.6  # the back hair has weight and damping
    assert mesh_bytes(g) == before                                # the look is untouched
    assert rep['colliders'], 'head / neck / chest colliders were added'


def test_colliders_do_not_push_anything_at_rest():
    g = make_vrm()
    S.fix(g)
    rig = S.Rig(g)
    sa = g.vrm['secondaryAnimation']
    for grp in sa['boneGroups']:
        tails = np.concatenate([rig.tails(r) for r in grp['bones']])
        for ci in grp['colliderGroups']:
            cg = sa['colliderGroups'][ci]
            for col in cg['colliders']:
                o = col['offset']
                c = rig.P[cg['node']] + np.array([o['x'], o['y'], -o['z']])
                gap = np.linalg.norm(tails - c, axis=1).min() - col['radius'] - grp['hitRadius']
                assert gap > 0, (grp['comment'], gap)


def test_collider_spheres_are_inside_the_body():
    g = make_vrm()
    rep = S.fix(g)
    rig = S.Rig(g)
    for c in rep['colliders']:
        centre = rig.P[rig.idx[c['bone']]] + np.array(c['offset'])
        # head sphere r=0.085 at y=1.58, body sphere r=0.16 at y=1.15
        d_head = np.linalg.norm(centre - [0, 1.58, 0]) + c['radius']
        d_body = np.linalg.norm(centre - [0, 1.15, 0]) + c['radius']
        assert d_head <= 0.086 or d_body <= 0.161, c


def test_ponytail_gets_own_chain_and_weights():
    g = make_vrm(ponytail=True)
    before = mesh_bytes(g)
    prof = {'tails': [{'name': 'J_Sec_HairTail', 'label': 'ponytail', 'tie': [0, -0.02, 0.12], 'dir': [0, -1, 0],
                       'radius': 0.03, 'joints': 3}]}
    rep = S.fix(g, prof)
    assert rep['tails'] and rep['tails'][0]['verts'] == 40
    sa = g.vrm['secondaryAnimation']
    tail_groups = [grp for grp in sa['boneGroups'] if grp['comment'] == 'Hair_ponytail']
    assert len(tail_groups) == 1
    root = g.json['nodes'][tail_groups[0]['bones'][0]]
    assert root['name'] == 'J_Sec_HairTail_01'
    # the new joints sit in the hair skin with matching inverse binds; weights still sum to 1
    hm = next(m for m in g.json['meshes'] if m['name'] == 'Hair')
    hn = next(n for n in g.json['nodes'] if n.get('mesh') == g.json['meshes'].index(hm))
    skin = g.json['skins'][hn['skin']]
    assert g.json['accessors'][skin['inverseBindMatrices']]['count'] == len(skin['joints'])
    W = g.accessor(hm['primitives'][0]['attributes']['WEIGHTS_0'])
    assert np.allclose(W.sum(1), 1.0, atol=1e-5)
    J = g.accessor(hm['primitives'][0]['attributes']['JOINTS_0'])
    tail_nodes = set(rep['tails'][0]['joints'])
    on_tail = [skin['joints'][j] in tail_nodes for j in J[:, 0]]
    assert sum(on_tail) == 40                                      # exactly the ponytail vertices moved over
    assert mesh_bytes(g) == before


def test_negative_controls_break_the_springs():
    g = make_vrm()
    S.fix(g)
    S.break_springs(g, 'floppy')
    for grp in g.vrm['secondaryAnimation']['boneGroups']:
        assert grp['stiffiness'] < 0.1 and grp['colliderGroups'] == [] and grp['dragForce'] == 0
    g = make_vrm()
    S.break_springs(g, 'unstable')
    assert all(grp['dragForce'] < 0 for grp in g.vrm['secondaryAnimation']['boneGroups'])


# ----------------------------------------------------------------------------- analysis
def _frames(n=90, wobble=0.0, face=0, body=0, nan_at=None, spike_at=None):
    info = {'joints': [{'name': 'J_Sec_Hair3_01', 'leaf': True}, {'name': 'J_Sec_Hair3_02', 'leaf': True},
                       {'name': 'J_Sec_C_Hood_01', 'leaf': False}],
            'restTails': [[0, -0.1, -0.1], [0, -0.1, 0.1], [0, -0.1, 0.12]]}
    fr = []
    for i in range(n):
        t = i / 30
        dx = wobble * math.sin(2 * math.pi * 1.5 * t)
        tails = [[dx, -0.1, -0.1], [dx, -0.1, 0.1], [0, -0.1, 0.12]]
        if spike_at is not None and i == spike_at:
            tails[1] = [0.4, 0.3, 0.1]
        f = dict(t=t, tails=tails, face=face, faceDeep=0.004 if face else 0, body=body, bodyDeep=0.01 if body else 0,
                 bodyRigid=0, cloth=0, clothDeep=0, nan=(nan_at == i), shapeRest=0.002, shapeRestMean=0.001,
                 moveP95=abs(dx))
        fr.append(f)
    return info, fr


def _run(**kw):
    info, fr = _frames(**kw)
    return {'info': info, 'clips': {'idle': fr, 'run': _frames(**kw)[1]}}


def test_analysis_passes_clean_motion():
    r = AN.model_numbers(_run(wobble=0.02))
    assert r['total']['ok'], r['total']
    assert 1.5 < r['total']['swing_cm'] < 2.5


@pytest.mark.parametrize('kw,flag', [({'face': 5}, 'face_frames'), ({'body': 9}, 'body_frames'),
                                     ({'spike_at': 40}, 'jumps'), ({'nan_at': 10}, 'nan')])
def test_analysis_negative_controls(kw, flag):
    r = AN.model_numbers(_run(wobble=0.02, **kw))
    assert not r['total']['ok']
    assert r['total'][flag]


def test_analysis_flags_jitter():
    info, fr = _frames()
    for i, f in enumerate(fr):                                     # a tip that flips 1.5 cm every frame
        f['tails'][0][0] = 0.015 * (1 if i % 2 else -1)
    r = AN.model_numbers({'info': info, 'clips': {'idle': fr}})
    assert r['total']['jitter_cm'] > AN.LIMITS['jitter_cm'] and not r['total']['ok']


# ----------------------------------------------------------------------------- clips
def test_sequence_clip_is_in_place_and_normalized():
    def clip(q, n=10):
        return {'fps': 30, 'frames': n, 'legLength': 0.8, 'bones': {'hips': [q] * n, 'head': [q] * n},
                'root': [[0.1 * i, 0.0, 0.2 * i] for i in range(n)]}
    a = CL.Src(clip([0, 0, 0, 1]), True, 0.8)
    b = CL.Src(clip([0, math.sin(0.4), 0, math.cos(0.4)]), True, 0.8)
    s = CL.sequence([(a, 0.5, 0), (b, 0.5, 0.2)], 0.8, 'x', hips_yaw=lambda t: 90 * t)
    assert s['frames'] == 31
    for r in s['root']:
        assert r[0] == 0 and r[2] == 0
    for q in s['bones']['hips'] + s['bones']['head']:
        assert abs(np.linalg.norm(q) - 1) < 1e-3


# ----------------------------------------------------------------------------- VRMC_springBone (per-joint springs)
def test_vrmc_progressive_springs_with_real_tips():
    from tools.vrm_springs import vrmc as VC
    g = make_vrm()
    before = mesh_bytes(g)
    rep = S.fix(g)
    out = VC.build(g, rep, {})
    ext = g.json['extensions']['VRMC_springBone']
    assert 'VRMC_springBone' in g.json['extensionsUsed'] and ext['specVersion'] == '1.0'
    assert out['head_spheres'] >= 1
    hair = [s for s in ext['springs'] if s['name'].startswith('Hair_')]
    assert len(hair) == 2
    for sp in hair:
        joints = sp['joints']
        assert g.json['nodes'][joints[-1]['node']]['name'].endswith('_tip')      # a real tip, no 7 cm stick
        st = [j['stiffness'] for j in joints[:-1]]
        assert st == sorted(st, reverse=True) and st[0] > st[-1]                 # stiff root, soft tip
        gp = [j['gravityPower'] for j in joints[:-1]]
        assert gp == sorted(gp) and gp[-1] > gp[0]                               # the tip carries the weight
    # no collider pushes a hair tail at rest: hit radius <= the rest gap to every head sphere
    rig = S.Rig(g)
    cols = ext['colliders']
    for sp in hair:
        heads = [c for gi in sp['colliderGroups'] for c in ext['colliderGroups'][gi]['colliders']]
        for a, b in zip(sp['joints'][:-1], sp['joints'][1:]):
            tail = rig.P[b['node']]
            for ci in heads:
                c = cols[ci]
                ctr = rig.P[c['node']] + np.array(c['shape']['sphere']['offset'])
                assert np.linalg.norm(tail - ctr) - c['shape']['sphere']['radius'] >= a['hitRadius'] - 1e-6
    assert mesh_bytes(g) == before


def test_vrmc_negative_control_inward():
    from tools.vrm_springs import vrmc as VC
    g = make_vrm()
    VC.build(g, S.fix(g), {})
    S.break_springs(g, 'inward')
    for sp in g.json['extensions']['VRMC_springBone']['springs']:
        assert sp['colliderGroups'] == []
        for j in sp['joints'][:-1]:
            assert j['gravityDir'][1] > 0 and j['stiffness'] < 0.1
