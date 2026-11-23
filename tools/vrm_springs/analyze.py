# -*- coding: utf-8 -*-
"""Numbers from a hair-check run (sim/run_sim.mjs metrics -> JSON): per clip and in total.

  swing_cm     how far the hair moves: spring-driven hair vertices in the head frame, distance from their position at
               the clip start (after settling), 95th percentile over the vertices, largest over the frames
  tip_swing_cm the same for the spring tails (bone tips, incl. the 7 cm virtual leaf tails)
  sag_cm       look change at rest: settled idle hair vs the modelled rest shape (95th percentile; mean in sag_mean_cm)
  face_frames  frames with >= MIN_VERTS spring-driven hair vertices > 2 mm inside the face (skin + eyes); target 0
  body_frames  same against the body / clothes near the head (> 4 mm); target 0
  rigid_body   frames where hair glued to the head (no spring weight) is inside the body: a pose effect, springs
               cannot change it (reported, not judged)
  cloth_frames cloth spring tails (hood, strings, hems) deeper in the body than at rest; target 0
  jumps        tools/model_check/anim_checks.jumps on all spring tails (head / anchor frame): pops, runaway
  jitter_cm    RMS of the 2nd difference of the hair tips in the idle clip (shaking at rest), 95th percentile
  nan          a spring bone rotation became NaN
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

from tools.model_check import anim_checks as AC

MIN_VERTS = 2
LIMITS = dict(face_frames=0, body_frames=0, cloth_frames=0, jumps=0, jitter_cm=0.35, nan=False)


def _arr(frames, key='tails'):
    return np.array([f[key] for f in frames], float)


def clip_numbers(info: dict, frames: list) -> dict:
    joints = info['joints']
    hair = np.array([('Hair' in j['name']) for j in joints], bool)
    leaf = np.array([j['leaf'] for j in joints], bool)
    T = _arr(frames)                                    # (F, J, 3)
    out = {}
    nan = any(f['nan'] for f in frames) or not np.isfinite(T).all()
    out['nan'] = bool(nan)
    T = np.nan_to_num(T, nan=0.0)
    sel = hair & leaf
    if sel.any():
        d = np.linalg.norm(T[:, sel] - T[0:1, sel], axis=-1).max(0)
        out['tip_swing_cm'] = round(100 * float(np.percentile(d, 95)), 2)
        out['swing_max_cm'] = round(100 * float(d.max()), 2)
    if frames and 'moveP95' in frames[0]:
        out['swing_cm'] = round(100 * max(f['moveP95'] for f in frames), 2)
    fr = np.arange(len(frames))
    j = AC.jumps(T, fr, labels=[x['name'] for x in joints], min_jump=0.03, ratio=4.0, hard=0.25)
    out['jumps'] = j['n_errors']
    out['jumps_worst'] = j['errors'][:2]
    out['max_step_cm'] = j['metrics']['max_step_cm']
    face = np.array([f['face'] for f in frames])
    body = np.array([f['body'] for f in frames])
    out['face_frames'] = int((face >= MIN_VERTS).sum())
    out['face_max_cm'] = round(100 * max(f['faceDeep'] for f in frames), 2)
    out['face_max_verts'] = int(face.max())
    out['body_frames'] = int((body >= MIN_VERTS).sum())
    out['body_max_cm'] = round(100 * max(f['bodyDeep'] for f in frames), 2)
    out['rigid_body_frames'] = int((np.array([f.get('bodyRigid', 0) for f in frames]) >= MIN_VERTS).sum())
    out['cloth_frames'] = int((np.array([f['cloth'] for f in frames]) >= 1).sum())
    out['cloth_max_cm'] = round(100 * max(f['clothDeep'] for f in frames), 2)
    out['frames'] = len(frames)
    return out


def model_numbers(run: dict) -> dict:
    info = run['info']
    joints = info['joints']
    hair = np.array([('Hair' in j['name']) for j in joints], bool)
    leaf = np.array([j['leaf'] for j in joints], bool)
    sel = hair & leaf
    clips = {k: clip_numbers(info, v) for k, v in run['clips'].items()}
    tot = dict(hair_chains=int(sel.sum()), spring_joints=len(joints))
    for k in ('face_frames', 'body_frames', 'rigid_body_frames', 'cloth_frames', 'jumps', 'frames'):
        tot[k] = sum(c[k] for c in clips.values())
    for k in ('face_max_cm', 'body_max_cm', 'cloth_max_cm', 'swing_max_cm', 'max_step_cm'):
        tot[k] = max((c.get(k, 0) for c in clips.values()), default=0)
    tot['nan'] = any(c['nan'] for c in clips.values())
    sw = [c['swing_cm'] for c in clips.values() if 'swing_cm' in c]
    tot['swing_cm'] = round(float(np.mean(sw)), 2) if sw else 0.0
    if 'idle' in run['clips'] and sel.any():
        fr = run['clips']['idle']
        T = np.nan_to_num(_arr(fr)[:, sel])
        rest = np.array(info['restTails'], float)[sel]
        tot['tip_sag_cm'] = round(100 * float(np.percentile(np.linalg.norm(T[0] - rest, axis=-1), 95)), 2)
        if 'shapeRest' in fr[0]:
            tot['sag_cm'] = round(100 * fr[0]['shapeRest'], 2)
            tot['sag_mean_cm'] = round(100 * fr[0]['shapeRestMean'], 2)
        acc = T[2:] - 2 * T[1:-1] + T[:-2]
        rms = np.sqrt((acc ** 2).sum(-1).mean(0))
        tot['jitter_cm'] = round(100 * float(np.percentile(rms, 95)), 3)
    tot['ok'] = bool(tot['face_frames'] == 0 and tot['body_frames'] == 0 and tot['cloth_frames'] == 0
                     and tot['jumps'] == 0 and not tot['nan'] and tot.get('jitter_cm', 0) <= LIMITS['jitter_cm'])
    return dict(total=tot, clips=clips)


def main(argv=None):
    argv = argv or sys.argv[1:]
    run = json.loads(Path(argv[0]).read_text(encoding='utf-8'))
    res = model_numbers(run)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    if len(argv) > 1:
        Path(argv[1]).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
