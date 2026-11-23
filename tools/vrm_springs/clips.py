# -*- coding: utf-8 -*-
"""Test clips for the hair check, in the site's clip format (app/anim3d.py / tools/anim_retarget.py):
``{fps, frames, legLength, spine: "hips", bones: {vrmBone: [[x,y,z,w]...]}, root: [[x,y,z]...]}``.

Sources: the project's library (D:/vvf-data/runtime/anim3d: test1_idle, test2_walk, the approved pilot_stairs) and the
CC0 pack clips already converted for node/vrm (D:/vvf-offload/vtubika/vrm/vendor/anim: run = Quaternius Sprint_Loop,
head_yes, head_no, idle_breathe). Composed here: a quick 180° turn, a fast head shake and the video sequence
run -> sudden stop -> head shake -> hold.

    python -m tools.vrm_springs.clips OUT_DIR
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

LIB = Path('D:/vvf-data/runtime/anim3d')
PACK = Path('D:/vvf-offload/vtubika/vrm/vendor/anim')
FPS = 30.0


def load(path) -> dict:
    return json.loads(Path(path).read_text(encoding='utf-8'))


def qnorm(q):
    return q / np.linalg.norm(q, axis=-1, keepdims=True)


def slerp(a, b, t):
    a = np.asarray(a, float)
    b = np.asarray(b, float).copy()
    d = float(np.dot(a, b))
    if d < 0:
        b, d = -b, -d
    if d > 0.9995:
        return qnorm(a + (b - a) * t)
    th = math.acos(d)
    return (math.sin((1 - t) * th) * a + math.sin(t * th) * b) / math.sin(th)


def qmul(a, b):
    x1, y1, z1, w1 = a
    x2, y2, z2, w2 = b
    return np.array([w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2, w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
                     w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2, w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2])


def yaw(deg):
    h = math.radians(deg) / 2
    return np.array([0.0, math.sin(h), 0.0, math.cos(h)])


class Src:
    """a clip sampled at any time (looping or clamped), root scaled to a common leg length"""

    def __init__(self, clip: dict, loop: bool, leg: float, speed: float = 1.0):
        self.c = clip
        self.loop = loop
        self.speed = speed
        self.k = leg / clip['legLength'] if clip.get('legLength') else 1.0
        n = clip['frames']
        h = clip['bones']['hips']
        self.period = n - 1 if loop and abs(np.dot(h[0], h[-1])) > 0.99999 else n

    def at(self, t: float):
        f = t * self.c['fps'] * self.speed
        n = self.c['frames']
        if self.loop:
            f %= self.period
            i = int(math.floor(f))
            j = (i + 1) % self.period
        else:
            f = min(max(f, 0), n - 1)
            i = int(math.floor(f))
            j = min(n - 1, i + 1)
        u = f - i
        bones = {}
        for b, rows in self.c['bones'].items():
            a = rows[min(i, len(rows) - 1)]
            bb = rows[min(j, len(rows) - 1)]
            bones[b] = slerp(a, bb, u)
        r = self.c['root']
        ra, rb = np.array(r[min(i, len(r) - 1)]), np.array(r[min(j, len(r) - 1)])
        return bones, (ra + (rb - ra) * u) * self.k


def blend(pa, pb, u):
    """two poses (bones, root) mixed with weight u of the second"""
    ba, ra = pa
    bb, rb = pb
    out = {}
    for b in set(ba) | set(bb):
        qa = ba.get(b, np.array([0, 0, 0, 1.0]))
        qb = bb.get(b, np.array([0, 0, 0, 1.0]))
        out[b] = slerp(qa, qb, u)
    return out, ra + (rb - ra) * u


def smooth(u):
    u = min(max(u, 0.0), 1.0)
    return u * u * (3 - 2 * u)


def sequence(parts, leg: float, name: str, hips_yaw=None, in_place=True) -> dict:
    """parts: [(Src, seconds, fade_in seconds)] played one after another, each from its own t=0."""
    total = sum(p[1] for p in parts)
    n = int(round(total * FPS)) + 1
    bones = {}
    root = []
    starts = np.cumsum([0] + [p[1] for p in parts])
    for f in range(n):
        t = f / FPS
        k = min(len(parts) - 1, int(np.searchsorted(starts, t, side='right') - 1))
        src, _, fade = parts[k]
        pose = src.at(t - starts[k])
        if k > 0 and fade > 0 and t - starts[k] < fade:
            prev = parts[k - 1][0].at(t - starts[k - 1])
            pose = blend(prev, pose, smooth((t - starts[k]) / fade))
        b, r = pose
        if hips_yaw is not None:
            b = dict(b)
            b['hips'] = qnorm(qmul(yaw(hips_yaw(t)), b['hips']))
        if in_place:
            r = np.array([0.0, r[1], 0.0])
        for kname, q in b.items():
            bones.setdefault(kname, []).append([round(float(x), 5) for x in q])
        root.append([round(float(x), 5) for x in r])
    return {'name': name, 'fps': FPS, 'frames': n, 'source': 'tools/vrm_springs/clips.py', 'spine': 'hips',
            'legLength': leg, 'bones': bones, 'root': root}


def build(out_dir) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    idle_t = load(LIB / 'test1_idle.json')
    walk_t = load(LIB / 'test2_walk.json')
    stairs = load(LIB / 'pilot_stairs.json')
    run = load(PACK / 'run.json')
    yes = load(PACK / 'head_yes.json')
    no = load(PACK / 'head_no.json')
    idle_b = load(PACK / 'idle_breathe.json')
    leg = run['legLength']
    I = Src(idle_b, True, leg)
    clips = {}
    clips['idle'] = idle_t
    clips['walk'] = walk_t
    clips['run'] = sequence([(Src(run, True, leg), 4.0, 0)], leg, 'run')
    # quick 180-degree turn on the spot: 0.55 s turn, hold, turn back
    def turn(t):
        def ramp(t0):
            return smooth((t - t0) / 0.55)
        return 180.0 * ramp(0.8) - 180.0 * ramp(2.8)
    clips['turn180'] = sequence([(I, 4.6, 0)], leg, 'turn180', hips_yaw=turn)
    clips['nod'] = sequence([(I, 0.6, 0), (Src(yes, False, leg), 2.5, 0.2), (I, 1.2, 0.3)], leg, 'nod')
    clips['shake'] = sequence([(I, 0.6, 0), (Src(no, True, leg, speed=1.6), 2.4, 0.15), (I, 1.2, 0.25)], leg, 'shake')
    clips['stairs'] = dict(stairs)          # the approved climb, as the site plays it (first 15 s are measured)
    # the video: run -> sudden stop -> quick head shake -> hold
    clips['seq'] = sequence([(Src(run, True, leg), 2.4, 0), (I, 0.9, 0.22), (Src(no, True, leg, speed=1.7), 2.3, 0.15),
                             (I, 1.6, 0.2)], leg, 'seq')
    for k, c in clips.items():
        (out / ('%s.json' % k)).write_text(json.dumps(c, separators=(',', ':')), encoding='utf-8')
    return {k: dict(frames=c['frames'], fps=c['fps'], seconds=round((c['frames'] - 1) / c['fps'], 2)) for k, c in clips.items()}


if __name__ == '__main__':
    print(json.dumps(build(sys.argv[1]), indent=1))
