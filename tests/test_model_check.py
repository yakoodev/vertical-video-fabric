# -*- coding: utf-8 -*-
"""Model check (tools/model_check): pure analysis on synthetic maps, layer/bone naming, and - when a Blender is
given in $MODEL_CHECK_BLENDER - the end-to-end synthetic scene (poke-through + coplanar z-fight found, clean = 100)."""
import json
import os
import subprocess
import sys

import numpy as np
import pytest

from tools.model_check import analysis as A
from tools.model_check import layers, poses

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(os.path.dirname(HERE), 'tools', 'model_check')
P = A.default_params(1.55, 0.003)          # Mio-like scale: 1.55 m, 3 mm pixels


def yx(n=96):
    return np.mgrid[0:n, 0:n].astype(np.float64)


def jacket_and_skin(n=96):
    """Outer layer: a flat surface at 2.000 m over a square; inner layer 5 mm behind it everywhere."""
    d_o = np.zeros((n, n))
    d_o[8:-8, 8:-8] = 2.0
    d_i = np.where(d_o > 0, 2.005, 0.0)
    return d_i, d_o


# ----------------------------------------------------------------------------- morphology / labelling
def test_label_counts_8_connected_blobs():
    m = np.zeros((10, 10), bool)
    m[1, 1] = m[2, 2] = True            # diagonal neighbours -> one blob
    m[5:7, 5:8] = True
    m[9, 0] = True
    lab, n = A.label(m)
    assert n == 3
    assert lab[1, 1] == lab[2, 2] != 0
    assert len({lab[5, 5], lab[6, 7]}) == 1


def test_label_u_shape_merges_late():
    m = np.zeros((6, 7), bool)
    m[0:5, 1] = True
    m[0:5, 5] = True
    m[4, 1:6] = True                    # the two arms only meet in the last row
    lab, n = A.label(m)
    assert n == 1 and lab[0, 1] == lab[0, 5]


def test_dilate_erode_opening():
    m = np.zeros((9, 9), bool)
    m[4, 4] = True
    assert A.dilate(m, 1).sum() == 9
    assert A.erode(A.dilate(m, 1), 1).sum() == 1
    line = np.zeros((9, 9), bool)
    line[4, :] = True
    assert not A.opening(line, 1).any()          # thin lines vanish, areas survive
    sq = np.zeros((9, 9), bool)
    sq[2:7, 2:7] = True
    assert A.opening(sq, 1).sum() == 25


def test_blobs_grouping_counts_speckles():
    m = np.zeros((40, 40), bool)
    m[10:20:2, 10:20:2] = True                   # 25 isolated speckles in a 10x10 patch
    m[35, 35] = True                             # one stray speckle
    blobs = A.blobs_from_mask(m, min_px=8, group_r=2)
    assert len(blobs) == 1 and blobs[0].sum() == 25


# ----------------------------------------------------------------------------- poke-through / z-fight
def test_skin_poking_through_jacket_is_found():
    d_i, d_o = jacket_and_skin()
    y, x = yx()
    g = np.exp(-((y - 48) ** 2 + (x - 48) ** 2) / (2 * 9.0 ** 2))
    d_i = np.where(d_o > 0, d_i - 0.015 * g, 0)      # a knee bump reaching 10 mm through the jacket
    pokes, zfs = A.poke_and_zfight(d_i, d_o, None, P)
    assert len(pokes) == 1
    b = pokes[0]
    assert b['kind'] == 'poke_through'
    assert b['mask'][48, 48] and b['area'] > 50
    assert 0.009 < b['penetration'] < 0.011
    assert b['enclosure'] > 0.9 and b['crossing'] > 0.6


def test_hand_floating_in_front_is_contact_not_poke():
    d_i, d_o = jacket_and_skin()
    y, x = yx()
    hand = ((y - 48) ** 2 + (x - 48) ** 2) < 12 ** 2
    d_i = np.where(hand, 1.982, d_i)                  # 18 mm in front, with a depth step at its edge
    pokes, _ = A.poke_and_zfight(d_i, d_o, None, P)
    assert pokes and all(b['kind'] == 'contact' for b in pokes)
    assert max(b['crossing'] for b in pokes) < 0.35


def test_hand_far_in_front_is_ignored():
    d_i, d_o = jacket_and_skin()
    y, x = yx()
    hand = ((y - 48) ** 2 + (x - 48) ** 2) < 12 ** 2
    d_i = np.where(hand, 1.80, d_i)                   # 20 cm in front: simply in front
    pokes, zfs = A.poke_and_zfight(d_i, d_o, None, P)
    assert pokes == [] and zfs == []


def test_poke_seen_through_an_opening_backface_is_ignored():
    d_i, d_o = jacket_and_skin()
    d_i = np.where(d_o > 0, 1.99, 0)                  # inner in front of the far inside wall of a sleeve
    bf = (d_o > 0).astype(np.uint8)
    pokes, _ = A.poke_and_zfight(d_i, d_o, bf, P)
    assert pokes == []


def test_hidden_poke_is_not_reported():
    d_i, d_o = jacket_and_skin()
    y, x = yx()
    g = np.exp(-((y - 48) ** 2 + (x - 48) ** 2) / (2 * 9.0 ** 2))
    d_i = np.where(d_o > 0, d_i - 0.015 * g, 0)
    hidden = np.zeros(d_i.shape, bool)                # a third part (an arm) covers everything
    pokes, _ = A.poke_and_zfight(d_i, d_o, None, P, vis_in=hidden, vis_any=hidden)
    assert pokes == []


def test_coplanar_patch_is_zfight_and_clean_pair_is_clean():
    d_i, d_o = jacket_and_skin()
    pokes, zfs = A.poke_and_zfight(d_i, d_o, None, P)
    assert pokes == [] and zfs == []                  # a 5 mm gap is fine
    d_i[30:60, 30:60] = 2.0 + 0.0001                  # a decal lying in the surface
    pokes, zfs = A.poke_and_zfight(d_i, d_o, None, P)
    assert pokes == []
    assert len(zfs) == 1 and zfs[0]['area'] >= 26 * 26


# ----------------------------------------------------------------------------- shimmer / flicker
def test_id_shimmer_flags_speckle_not_subpixel_motion():
    n = 80
    ids = np.zeros((n, n), np.int32)
    ids[10:70, 10:70] = 1
    ids[10:70, 40:70] = 2
    moved = np.zeros_like(ids)
    moved[10:70, 10:70] = 1
    moved[10:70, 41:70] = 2                           # boundary moved by a pixel: explainable
    assert A.id_shimmer(ids, moved, P) == []
    rng = np.random.RandomState(0)
    a, b = ids.copy(), moved.copy()
    zone = (slice(20, 35), slice(15, 30))
    a[zone] = np.where(rng.rand(15, 15) < 0.3, 3, 1)  # decal (3) fighting with the base (1)
    b[zone] = np.where(rng.rand(15, 15) < 0.3, 3, 1)
    sh = A.id_shimmer(a, b, P)
    assert len(sh) == 1
    ys, xs = np.nonzero(sh[0]['mask'])
    assert 18 <= ys.min() and ys.max() <= 36 and 13 <= xs.min() and xs.max() <= 31


def test_texture_flicker_ignores_smooth_shift_flags_noise():
    n = 64
    y, x = yx(n)
    base = np.zeros((n, n, 4), np.float32)
    base[..., 3] = 1
    a, b = base.copy(), base.copy()
    for c in range(3):
        a[..., c] = 0.5 + 0.4 * np.sin(x / 6.0)
        b[..., c] = 0.5 + 0.4 * np.sin((x + 0.5) / 6.0)
    assert A.texture_flicker(a, b, P) == []
    rng = np.random.RandomState(1)
    noise_a = rng.rand(16, 16) > 0.5
    noise_b = rng.rand(16, 16) > 0.5
    for c in range(3):
        a[20:36, 20:36, c] = noise_a * 0.9
        b[20:36, 20:36, c] = noise_b * 0.9
    fl = A.texture_flicker(a, b, P)
    assert len(fl) >= 1 and fl[0]['mask'][20:36, 20:36].any()


def test_range_violation_zero_for_neighbour_values():
    a = np.zeros((5, 5, 3))
    a[:, 2:] = 1.0
    b = np.zeros((5, 5, 3))
    b[:, 1:] = 1.0                                    # edge moved by one pixel
    assert A.range_violation(a, b, 1).max() == 0


# ----------------------------------------------------------------------------- geometry, clustering, score
def test_ray_dirs_and_unproject_roundtrip():
    view = {'cam_matrix': np.eye(4).tolist(),
            'frame': [[0.5, 0.5, -1], [0.5, -0.5, -1], [-0.5, -0.5, -1], [-0.5, 0.5, -1]]}
    dirs = A.ray_dirs(view, 4, 4)
    assert np.allclose(np.linalg.norm(dirs, axis=2), 1)
    assert dirs[0, 0, 0] < 0 and dirs[0, 0, 1] > 0        # top-left pixel looks left and up
    m = np.zeros((4, 4), bool)
    m[1:3, 1:3] = True
    dist = np.full((4, 4), 2.0)
    pos = A.unproject_mean(m, dist, view, dirs)
    assert abs(pos[0]) < 1e-9 and abs(pos[1]) < 1e-9 and pos[2] < -1.9


def test_nearest_bone():
    bones = {'thigh.L': [[0.1, 0, 0.9], [0.1, 0, 0.5]], 'head': [[0, 0, 1.4], [0, 0, 1.6]]}
    assert A.nearest_bone([0.15, 0, 0.7], bones)[0] == 'thigh.L'


def _f(view, pos, area=30, t='poke_through'):
    return {'type': t, 'pose': 'rest', 'view': view, 'area': area, 'pos': pos, 'inner_layer': 'skin',
            'outer_layer': 'outer', 'parts': {'Body/Skin': area}, 'penetration': 0.004, 'enclosure': 1,
            'crossing': 0.8}


def test_cluster_groups_close_findings_across_views():
    fs = [_f('a', [0, 0, 1]), _f('b', [0.01, 0, 1]), _f('c', [0.02, 0, 1.01]), _f('d', [0.5, 0, 1], area=5)]
    cl = A.cluster(fs, 0.06 * 1.55)
    assert len(cl) == 2
    big = cl[0]
    assert big['n_views'] == 3 and big['severity'] == 'high' and big['worst_views'][0] in 'abc'
    assert cl[1]['severity'] == 'low'
    assert A.score(cl) == 100 - 12 - 1
    assert not A.is_clean(cl)
    assert A.score([]) == 100 and A.is_clean([])


def test_cluster_keeps_different_layer_pairs_apart():
    f1, f2 = _f('a', [0, 0, 1]), _f('a', [0, 0, 1])
    f2['outer_layer'] = 'hair'
    assert len(A.cluster([f1, f2], 0.1)) == 2


# ----------------------------------------------------------------------------- names
@pytest.mark.parametrize('obj,mat,want', [
    ('Body', 'Body_00_SKIN', 'skin'), ('Body', 'Tops_01_CLOTH', 'outer'), ('Body', 'Bottoms_01_CLOTH', 'bottoms'),
    ('Body', 'Shoes_01_CLOTH', 'footwear'), ('Face', 'FaceEyeline_00_FACE', 'face_detail'),
    ('Face', 'FaceEyelash_00_FACE', 'face_detail'), ('Face', 'EyeIris_00_EYE', 'eyes'), ('Face', 'Face_00_SKIN', 'skin'),
    ('Hair', 'HairBack_00_HAIR', 'hair'), ('HairPins', 'PinYellow', 'hair_acc'), ('Hoodie', 'Hoodie', 'outer'),
    ('HoodieDetails', 'Coral', 'accessory'), ('Trousers', 'Trousers', 'bottoms'), ('Hand.L', 'Skin', 'skin'),
    ('Sole.L', 'Welt', 'footwear'), ('Hoodie', 'Outline', 'ignore'), ('Armature', '', 'other'),
    ('Knee', 'Skin', 'skin'), ('Sticker', 'Sticker', 'accessory'),
])
def test_layer_inference(obj, mat, want):
    assert layers.infer_layer(obj, mat) == want


def test_layer_hints_override_and_pairs():
    hints = {'assign': {'Body/Tops_*': 'inner', 'Cape': 'outer'}, 'extra_pairs': [['hair', 'outer']]}
    got = layers.assign_layers([('Body', 'Tops_01_CLOTH'), ('Cape', 'Red'), ('Body', 'Body_SKIN')], hints)
    assert got[('Body', 'Tops_01_CLOTH')] == 'inner' and got[('Cape', 'Red')] == 'outer'
    pairs = layers.layer_pairs({'skin', 'inner', 'outer', 'hair'}, hints)
    assert ('skin', 'inner') in pairs and ('hair', 'outer') in pairs and ('skin', 'bottoms') not in pairs


@pytest.mark.parametrize('role,names,want', [
    ('L_UpperArm', ['J_Bip_C_Hips', 'J_Bip_L_UpperArm', 'J_Bip_R_UpperArm'], 'J_Bip_L_UpperArm'),
    ('L_UpperArm', ['upper_arm.L', 'upper_arm.R', 'forearm.L'], 'upper_arm.L'),
    ('R_LowerArm', ['upper_arm.R', 'forearm.R'], 'forearm.R'),
    ('L_UpperLeg', ['thigh.L', 'shin.L'], 'thigh.L'),
    ('R_LowerLeg', ['mixamorig:RightUpLeg', 'mixamorig:RightLeg'], 'mixamorig:RightLeg'),
    ('L_UpperArm', ['mixamorig:LeftShoulder', 'mixamorig:LeftArm'], 'mixamorig:LeftArm'),
    ('chest', ['J_Bip_C_Spine', 'J_Bip_C_Chest', 'J_Bip_C_UpperChest'], 'J_Bip_C_Chest'),
    ('upper_chest', ['spine', 'chest'], None),
    ('hips', ['hips', 'spine'], 'hips'),
    ('custom_bone', ['custom_bone'], 'custom_bone'),
])
def test_resolve_bone(role, names, want):
    assert poses.resolve_bone(role, names) == want


def test_default_poses_load():
    ps = poses.load_poses()
    names = [p['name'] for p in ps]
    assert names[0] == 'rest' and {'arms_up', 'deep_crouch', 'twist'} <= set(names)
    for p in ps:
        for e in p['bones']:
            assert 'aim' in e or 'rot' in e


# ----------------------------------------------------------------------------- end to end (needs Blender)
BLENDER = os.environ.get('MODEL_CHECK_BLENDER')


@pytest.mark.skipif(not BLENDER or not os.path.exists(BLENDER), reason='set MODEL_CHECK_BLENDER to a blender.exe')
def test_synthetic_scene_end_to_end(tmp_path):
    subprocess.check_call([BLENDER, '-b', '--factory-startup', '-P', os.path.join(TOOL, 'synth_scene.py'), '--',
                           '--out', str(tmp_path)])
    results = {}
    for name in ('synth_dirty', 'synth_clean'):
        out = tmp_path / ('run_' + name)
        subprocess.check_call([sys.executable, os.path.join(TOOL, 'run_check.py'), '--blender', BLENDER,
                               '--model', str(tmp_path / (name + '.blend')), '--out', str(out), '--poses', 'rest',
                               '--az', '8', '--el=-15,15'])
        results[name] = json.load(open(str(out / 'report' / 'report.json'), encoding='utf-8'))
    dirty, clean = results['synth_dirty'], results['synth_clean']
    assert clean['score'] == 100 and clean['clean']
    types = {(c['type'], c['severity']) for c in dirty['findings']}
    poke = [c for c in dirty['findings'] if c['type'] == 'poke_through']
    zf = [c for c in dirty['findings'] if c['type'] == 'zfight']
    assert poke and poke[0]['severity'] == 'high' and 'Knee/Skin' in poke[0]['parts']
    assert abs(poke[0]['location'][0] - 0.28) < 0.04                  # the knee sits at +X on the jacket surface
    assert zf and zf[0]['severity'] == 'high' and 'Sticker/Sticker' in zf[0]['parts']
    assert dirty['score'] < 80 and not dirty['clean'], types
