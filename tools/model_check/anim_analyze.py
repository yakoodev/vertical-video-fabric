# -*- coding: utf-8 -*-
"""Run every animation check (``anim_checks.py``) on a dump from ``anim_dump.py``.

    python tools/model_check/anim_analyze.py DUMP_DIR [--out report_dir] [--negative freeze|leak|foot_spin]

Writes ``anim_report.json`` and ``anim_summary.txt`` (into ``--out`` or DUMP_DIR). ``--negative`` breaks the real
dump on purpose first (the run must then fail that check): ``freeze`` holds frame 1 for the whole shot (static),
``leak`` gives belly vertices 30 % of the left upper arm (weight_leak), ``foot_spin`` turns the left foot by 170 deg
on one frame (rotation_jumps). Needs only numpy.
"""
import argparse
import json
import os
import re
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if __package__ in (None, ''):
    sys.path.insert(0, HERE)
    import anim_checks as AC  # noqa: E402
else:
    from . import anim_checks as AC

FEET_HANDS = ('L_Foot', 'R_Foot', 'L_Toes', 'R_Toes', 'L_Hand', 'R_Hand')


def _mesh_ids(D):
    return list(range(len(D['info']['meshes'])))


def perturb(D, kind):
    """A deliberately broken copy of the real dump (negative control on real data)."""
    D = dict(D)
    if kind == 'freeze':
        for k in ('pose', 'arm_world'):
            D[k] = np.broadcast_to(D[k][:1], D[k].shape).copy()
        for m in _mesh_ids(D):
            for k in ('m%d_ev_co' % m, 'm%d_ev_no' % m):
                D[k] = np.broadcast_to(D[k][:1], D[k].shape).copy()
    elif kind == 'leak':
        b = AC.bone_index(D, 'L_UpperArm')
        hips = AC.bone_index(D, 'hips')
        co = D['m0_rest_co']
        head = D['rest_local'][hips, :3, 3]
        belly = (np.abs(co[:, 0] - head[0]) < 0.10) & (co[:, 2] > head[2] + 0.08) & (co[:, 2] < head[2] + 0.25)
        idx, val = D['m0_w_idx'].copy(), D['m0_w_val'].copy()
        idx[belly, 3] = b
        val[belly] *= 0.7
        val[belly, 3] = 0.3
        D['m0_w_idx'], D['m0_w_val'] = idx, val
        D['negative_note'] = 'belly vertices %d get 30%% of %s' % (int(belly.sum()), D['info']['bones'][b])
    elif kind == 'foot_spin':
        b = AC.bone_index(D, 'L_Foot')
        pose = D['pose'].copy()
        t = len(pose) // 3
        c, s = np.cos(np.radians(170)), np.sin(np.radians(170))
        Rz = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]])
        M = pose[t, b].astype(np.float64)
        M[:3, :3] = M[:3, :3] @ Rz
        pose[t, b] = M
        D['pose'] = pose
        D['negative_note'] = 'left foot turned 170 deg on frame %d' % D['info']['frames'][t]
    else:
        raise SystemExit('unknown --negative %r' % kind)
    return D


def run(D, skip_secondary=AC.SECONDARY, normals_skip=r'Face'):
    info = D['info']
    frames = info['frames']
    cuts = info.get('cuts') or []
    names = info['bones']
    deform = info['deform']
    W = AC.world_pose(D)
    skin = np.einsum('fbij,bjk->fbik', W, np.linalg.inv(D['rest_local'].astype(np.float64)))
    res = []
    main_bones = [b for b, n in enumerate(names) if deform[b] and not re.search(skip_secondary, n)]
    heads = W[:, main_bones][..., :3, 3]
    labels = [names[b] for b in main_bones]
    j_b = AC.jumps(heads, frames, cuts, labels=labels)
    j_b['check'] = 'jumps_bones'
    res.append(j_b)
    dom0 = D['m0_w_idx'][:, 0]
    sub0 = D['m0_sub']
    vlabels = [names[int(i)] if i >= 0 else '?' for i in dom0[sub0]]
    keep = [k for k, n in enumerate(vlabels) if not re.search(skip_secondary, n)]
    j_v = AC.jumps(D['m0_ev_co'][:, keep], frames, cuts, labels=['%s v%d' % (vlabels[k], int(sub0[k])) for k in keep])
    j_v['check'] = 'jumps_vertices'
    res.append(j_v)
    rb = [(r, AC.bone_index(D, r)) for r in FEET_HANDS]
    rb = [(r, b) for r, b in rb if b is not None]
    res.append(AC.rotation_jumps(W[:, [b for _r, b in rb], :3, :3], frames, cuts, labels=[r for r, _b in rb]))
    res.append(AC.bend_plane(W, frames, AC.default_chains(D)))
    depths = {k[len('floor_'):]: D[k] for k in D if k.startswith('floor_') and not k.endswith('_at')
              and k != 'floor_selftest'}
    where = {k[len('floor_'):-3]: D[k] for k in D if k.startswith('floor_') and k.endswith('_at')}
    if info.get('floor'):
        fc = AC.floor_contact(depths, frames, where=where)
        if 'floor_selftest' in D:
            # the dump's own negative control: the lowest sole point 3 cm lower is inside the floor on stance frames
            st = np.asarray(D['floor_selftest'])
            fc['metrics']['selftest_caught_pct'] = round(100 * float((st > 0.02).mean()), 1)
            if (st > 0.02).mean() < 0.2:
                fc['ok'] = False
                fc['errors'].insert(0, {'frame': None, 'what': 'floor probe blind',
                                        'caught_pct': fc['metrics']['selftest_caught_pct']})
                fc['n_errors'] += 1
        res.append(fc)
    for ci, c in enumerate(info.get('contacts') or []):
        res.append(AC.hand_contact(c['name'], D['contact%d_gap' % ci], D['contact%d_pen' % ci], D['contact%d_w' % ci],
                                   frames))
    # normals + skinning self-check (numpy LBS of the exported weights against Blender's evaluated subset)
    for m, mi in enumerate(info['meshes']):
        sub = D['m%d_sub' % m]
        idx, val = D['m%d_w_idx' % m][sub], D['m%d_w_val' % m][sub]
        exp_no = np.zeros(D['m%d_ev_no' % m].shape)
        err = 0.0
        for t in range(len(frames)):
            exp_no[t] = AC.lbs_normals(D['m%d_rest_no' % m][sub], idx, val, skin[t])
            if t % 40 == 0:
                p = AC.lbs(D['m%d_rest_co' % m][sub], idx, val, skin[t])
                err = max(err, float(np.percentile(np.linalg.norm(p - D['m%d_ev_co' % m][t], axis=1), 99)))
        r = AC.normals(D['m%d_ev_no' % m], exp_no, frames, labels=[names[int(i)] if i >= 0 else '?' for i in idx[:, 0]])
        r['check'] = 'normals_%s' % mi['name'].split('.')[0]
        r['metrics']['skin_selfcheck_p99_cm'] = round(100 * err, 2)
        if re.search(normals_skip, mi['name']):
            r['metrics']['note'] = 'shape keys deform this mesh: reported, not judged'
            r['warnings'] = r['errors'][:20] + r['warnings']
            r['n_warnings'] += r['n_errors']
            r['errors'], r['n_errors'], r['ok'] = [], 0, True
        res.append(r)
    res.append(AC.import_twist(D))
    tracks = {info.get('armature') or 'armature': W[:, [b for b in range(len(names)) if deform[b]]][..., :3, 3]}
    res.append(AC.static(tracks, frames, still_sec=8.0, fps=float(info.get('fps') or 24)))
    res.append(AC.weight_leak(D['m0_rest_co'], D['m0_w_idx'], D['m0_w_val'], D['rest_local'], info['parent'], names,
                              deform))
    props = [{'name': p['name'], 'prop': D['prop%d_m' % i], 'socket': D['prop%d_socket' % i], 'max_gap': p.get('max_gap'),
              'axis': p.get('axis')} for i, p in enumerate(info.get('props') or [])]
    if props:
        res.append(AC.attached_props(props, frames))
    out = {'dump': info.get('meta'), 'frames': len(frames), 'ok': all(r['ok'] for r in res), 'checks': res}
    if D.get('negative_note'):
        out['negative'] = D['negative_note']
    return out


def summary_text(rep):
    lines = ['animation checks: %s (%d frames)%s' % ('PASS' if rep['ok'] else 'FAIL', rep['frames'],
                                                     ('  [negative control: %s]' % rep['negative']) if rep.get('negative') else '')]
    for r in rep['checks']:
        lines.append('  %-22s %s  errors %4d  warnings %4d' % (r['check'], 'ok  ' if r['ok'] else 'FAIL', r['n_errors'],
                                                               r['n_warnings']))
        for e in r['errors'][:4]:
            lines.append('      ! ' + json.dumps(e, ensure_ascii=False))
        for e in r['warnings'][:2]:
            lines.append('      ~ ' + json.dumps(e, ensure_ascii=False))
        lines.append('      metrics ' + json.dumps(r['metrics'], ensure_ascii=False)[:600])
    return '\n'.join(lines) + '\n'


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('dump')
    ap.add_argument('--out')
    ap.add_argument('--negative')
    a = ap.parse_args(argv)
    D = AC.load(a.dump)
    if a.negative:
        D = perturb(D, a.negative)
    rep = run(D)
    out = a.out or a.dump
    os.makedirs(out, exist_ok=True)
    tag = ('_neg_' + a.negative) if a.negative else ''
    with open(os.path.join(out, 'anim_report%s.json' % tag), 'w', encoding='utf-8') as fh:
        json.dump(rep, fh, ensure_ascii=False, indent=1)
    txt = summary_text(rep)
    with open(os.path.join(out, 'anim_summary%s.txt' % tag), 'w', encoding='utf-8') as fh:
        fh.write(txt)
    sys.stdout.write(txt)
    return 0 if rep['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
