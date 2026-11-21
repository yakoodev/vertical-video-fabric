# -*- coding: utf-8 -*-
"""Light-orbit shadow metrics (tools/model_check/lit_analysis.py) on synthetic faces: a disc 'face' lit by a moving
terminator is clean (1 component, nested, IoU 1); the negative controls - speckle from noisy normals, a shadow that
jumps back, an expression that moves the shadow - must be caught. With $MODEL_CHECK_BLENDER set, the Blender rig runs
on a synthetic sphere face with clean and with deliberately noisy normals."""
import json
import os
import subprocess
import sys

import numpy as np
import pytest

from tools.model_check import lit_analysis as LA

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(os.path.dirname(HERE), 'tools', 'model_check')


def disc(n=160, r=60):
    y, x = np.mgrid[0:n, 0:n] - n / 2.0
    return (x * x + y * y) <= r * r, x / r, y / r


def sphere_shadow(az_deg, n=160, r=60, noise=0.0, seed=0, elev=0.0):
    """shadow of a lit sphere seen from the front; light azimuth az (0 = from the camera)"""
    face, x, y = disc(n, r)
    z = np.sqrt(np.clip(1 - x * x - y * y, 0, 1))            # towards the camera
    nx, ny, nz = x, -y, z
    if noise:
        rng = np.random.default_rng(seed)
        nx = nx + rng.normal(0, noise, nx.shape); ny = ny + rng.normal(0, noise, ny.shape); nz = nz + rng.normal(0, noise, nz.shape)
    a = np.radians(az_deg); e = np.radians(elev)
    L = (np.sin(a) * np.cos(e), np.sin(e), np.cos(a) * np.cos(e))
    lit = nx * L[0] + ny * L[1] + nz * L[2] > 0
    return face & ~lit, face


def code_of(face, decal=None):
    c = np.zeros(face.shape, np.uint8)
    c[face] = LA.FACE_ID
    if decal is not None:
        c[decal] = LA.DECAL_ID
    return c


# ----------------------------------------------------------------------------- shape measures
def test_contour_length_square_and_disc():
    m = np.zeros((50, 50), bool)
    m[10:30, 10:30] = True
    assert abs(LA.contour_length(m) - 80) < 3              # 4 x 20, the corners are cut by half a diagonal
    d, _, _ = disc(200, 60)
    assert abs(LA.contour_length(d) / (2 * np.pi * 60) - 1) < 0.05


def test_hull_perimeter_of_convex_shape_matches_contour():
    d, _, _ = disc(200, 60)
    p = LA.hull_perimeter(LA.row_extremes(LA.contour_points(d)))
    assert abs(LA.contour_length(d) / p - 1) < 0.04


def test_clean_terminator_is_one_component_ratio_near_one():
    for az in (30, 60, 90, 120):
        sh, face = sphere_shadow(az)
        m = LA.frame_metrics(sh, face)
        assert m['components'] == 1, az
        assert m['tiny_share'] == 0
        assert m['perim_ratio'] < 1.25, (az, m['perim_ratio'])          # a crescent is a little concave


def test_negative_control_noisy_normals_scores_badly():
    sh, face = sphere_shadow(70, noise=0.35, seed=3)
    m = LA.frame_metrics(sh, face)
    assert m['components'] > 20
    assert m['tiny_share'] > 0.8
    assert m['perim_ratio'] > 2.0


def test_decal_hole_does_not_split_the_shadow():
    sh, face = sphere_shadow(90)
    eye = np.zeros_like(face)
    eye[60:100, 70:90] = True                              # a decal across the terminator
    lit = np.where(sh, 0.0, 1.0)
    lit[eye] = 1.0                                         # decals are unlit (emission): no shadow of their own
    s, reg, dec = LA.face_shadow(lit, code_of(face & ~eye, eye & face))
    assert LA.frame_metrics(s, reg)['components'] == 1
    assert LA.iou(s, sh, face) > 0.97


def test_fill_from_neighbours_majority():
    known = np.ones((5, 5), bool); known[2, 2] = False
    v = np.zeros((5, 5), bool); v[1:4, 1:4] = True
    out, k = LA.fill_from_neighbours(v, known, ~known)
    assert out[2, 2] and k.all()


# ----------------------------------------------------------------------------- across frames
def orbit(noise=0.0, elev=0.0, step=5, exprs=('neutral', 'smile'), shift=0):
    angles = list(range(0, 360, step))
    frames = {}
    for e in exprs:
        for a in angles:
            sh, face = sphere_shadow(a, noise=noise, seed=a * 7 + len(e), elev=elev)
            if e != exprs[0] and shift:
                sh = np.roll(sh, shift, axis=0) & face       # the shadow slides with the expression
            frames[(e, a)] = (sh, face, np.zeros_like(face))
    return frames, angles, list(exprs)


def test_orbit_clean_sphere_meets_the_targets():
    frames, angles, exprs = orbit()
    per, agg = LA.orbit_metrics(frames, angles, exprs)
    assert agg['components']['max'] <= 1
    assert agg['nesting_share'] == 1.0
    assert agg['iou_mean'] == 1.0
    v = LA.verdict(agg)
    assert v['components_ok'] and v['nesting_ok'] and v['iou_ok'], v


def test_orbit_negative_controls():
    frames, angles, exprs = orbit(noise=0.3)
    _, agg = LA.orbit_metrics(frames, angles, exprs)
    v = LA.verdict(agg)
    assert not v['components_ok'] and not v['nesting_ok'] and not v['iou_ok']
    assert LA.noisy_score(agg) > 5
    frames, angles, exprs = orbit(shift=8)                  # the expression moves the shadow: IoU must drop
    _, agg = LA.orbit_metrics(frames, angles, exprs)
    assert agg['iou_mean'] < 0.95 and not LA.verdict(agg)['iou_ok']


def test_nesting_catches_a_shadow_that_jumps_back():
    frames, angles, exprs = orbit(exprs=('neutral',))
    seq = [frames[('neutral', a)][0] for a in angles]
    face = frames[('neutral', 0)][1]
    seq[10] = seq[4]                                       # at 50 deg the shadow of 20 deg comes back: smaller
    r = LA.nesting(seq, angles, face)
    assert r['nested'] < 1.0 and r['worst'] > 0.01


def test_iou_empty_is_one():
    z = np.zeros((4, 4), bool)
    assert LA.iou(z, z) == 1.0


# ----------------------------------------------------------------------------- Blender rig end to end
BLENDER = os.environ.get('MODEL_CHECK_BLENDER')


@pytest.mark.skipif(not (BLENDER and os.path.exists(BLENDER)), reason='set MODEL_CHECK_BLENDER to blender.exe')
def test_rig_on_synthetic_face_clean_vs_noisy(tmp_path):
    res = {}
    for name, extra in (('clean', []), ('noisy', ['--noise-normals', '0.5'])):
        out = str(tmp_path / name)
        cmd = [BLENDER, '-b', '--factory-startup', '-P', os.path.join(TOOL, 'light_orbit.py'), '--',
               '--synth', '--out', out, '--angles', '24', '--res', '160', '--exprs', 'neutral'] + extra
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
        r = subprocess.run([sys.executable, os.path.join(TOOL, 'light_report.py'), out, '--no-images'],
                           capture_output=True, text=True, timeout=600)
        assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
        res[name] = json.load(open(os.path.join(out, 'report', 'light_orbit.json'), encoding='utf-8'))
    c, n = res['clean']['face'], res['noisy']['face']
    assert c['components']['max'] <= 2, c['components']
    assert n['components']['mean'] > 5 * max(1.0, c['components']['mean'])
    assert LA.noisy_score(n) > LA.noisy_score(c) + 3
