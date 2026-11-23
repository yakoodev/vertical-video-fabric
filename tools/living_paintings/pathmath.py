# -*- coding: utf-8 -*-
"""Shared camera math (plain numpy; used by planner.py and, inside Blender, by scene.py)."""
from __future__ import annotations

import math

import numpy as np

TANH = 0.45                 # tan(horizontal half-FOV): 36 mm sensor, 40 mm lens
TANV = TANH * 16 / 9


def hermite(p0, p1, m0, m1, s):
    s2, s3 = s * s, s * s * s
    return (2 * s3 - 3 * s2 + 1) * p0 + (s3 - 2 * s2 + s) * m0 + (-2 * s3 + 3 * s2) * p1 + (s3 - s2) * m1


def spline(keys, frames):
    """keys: [(frame, value, vel per frame | None)] → values at frames (Catmull-Rom where vel is None)."""
    fs = [k[0] for k in keys]
    vals = [np.array(k[1], float) for k in keys]
    vels = []
    for i, k in enumerate(keys):
        if k[2] is not None:
            vels.append(np.array(k[2], float))
        elif 0 < i < len(keys) - 1:
            vels.append((vals[i + 1] - vals[i - 1]) / (fs[i + 1] - fs[i - 1]))
        else:
            vels.append(np.zeros_like(vals[i]))
    out = []
    for f in frames:
        j = max(0, min(len(fs) - 2, int(np.searchsorted(fs, f, side="right")) - 1))
        d = fs[j + 1] - fs[j]
        s = (f - fs[j]) / d
        out.append(hermite(vals[j], vals[j + 1], vels[j] * d, vels[j + 1] * d, s))
    return np.array(out)


def basis(yaw_deg, pitch_deg):
    """Camera basis (forward, right, up) for yaw (right +) and pitch (up +); base looks +Y, up +Z."""
    y, p = math.radians(yaw_deg), math.radians(pitch_deg)
    fwd = np.array([math.sin(y) * math.cos(p), math.cos(y) * math.cos(p), math.sin(p)])
    right = np.array([math.cos(y), -math.sin(y), 0.0])
    up = np.cross(right, fwd)
    return fwd, right, up


def project(pt, cam, yaw, pitch):
    """World point → screen (x, y in -1..1 of the half-extent, depth). x right, y up."""
    fwd, right, up = basis(yaw, pitch)
    d = np.asarray(pt, float) - cam
    z = d @ fwd
    if z <= 1e-4:
        return None
    return (d @ right) / (z * TANH), (d @ up) / (z * TANV), z
