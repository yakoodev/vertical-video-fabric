# -*- coding: utf-8 -*-
"""Measure a light-orbit run (light_orbit.py output): face shadow components, tiny islands, perimeter ratio, IoU
between expressions, nesting across angles; lamp invariance; per-part components of the full pass.

    python tools/model_check/light_report.py DIR [--no-images] [--tiny 0.001] [--edge 2]

-> DIR/report/light_orbit.json, DIR/report/summary.txt, DIR/report/masks_sheet.png (shadow per expression x angle)
"""
import argparse
import glob
import json
import os
import sys

import numpy as np

try:
    from . import lit_analysis as LA
except (ImportError, ValueError):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import lit_analysis as LA  # noqa: E402


def load(path):
    from PIL import Image
    a = np.asarray(Image.open(path).convert('RGB'))
    return a[..., 0] / 255.0, a[..., 1], a[..., 2] / 255.0


def az_key(a):
    return '%05.1f' % a


def run(directory, tiny=0.001, edge=2, images=True):
    meta = json.load(open(os.path.join(directory, 'meta.json'), encoding='utf-8'))
    angles, exprs = meta['angles'], meta['exprs']
    frames = {}
    for e in exprs:
        for a in angles:
            p = os.path.join(directory, 'masks', e, az_key(a) + '.png')
            if os.path.exists(p):
                lit, code, zone = load(p)
                frames[(e, a)] = LA.face_shadow(lit, code)
    per, agg = LA.orbit_metrics(frames, angles, exprs, tiny=tiny, edge=edge)
    out = dict(meta=dict((k, meta[k]) for k in ('model', 'setup_args', 'lit_source', 'res', 'elev', 'noise_normals', 'noise_map',
                                                'mask_s_per_frame', 'beauty_s_per_frame') if k in meta),
               face=agg, verdict=LA.verdict(agg), noisy_score=LA.noisy_score(agg),
               per_frame={'%s@%s' % (e, az_key(a)): m for (e, a), m in per.items()})
    # by expression: mean components
    out['face_by_expr'] = {e: LA.summarize([per[(e, a)]['components'] for a in angles if (e, a) in per]) for e in exprs}
    # ---- lamp invariance
    lamp = {}
    for var in ('power2', 'orange'):
        ious = []
        for p in sorted(glob.glob(os.path.join(directory, 'lamp', var, '*.png'))):
            a = float(os.path.basename(p)[:-4])
            base = frames.get((exprs[0], a))
            if base is None:
                continue
            s, reg, _ = LA.face_shadow(*load(p)[:2])
            ious.append(LA.iou(s, base[0], reg & base[1]))
        if ious:
            lamp[var] = LA.summarize(ious)
    if lamp:
        out['lamp_invariance'] = lamp
    # ---- full pass: per region components (hair, sleeves, ...)
    full = sorted(glob.glob(os.path.join(directory, 'full', '*.png')))
    if full:
        names = {v: k for k, v in meta['regions'].items()}
        parts = {}
        for p in full:
            lit, code, _ = load(p)
            for rid in np.unique(code):
                if rid == 0:
                    continue
                reg = code == rid
                if reg.sum() < 200:
                    continue
                m = LA.frame_metrics(reg & (lit < 0.5), reg, tiny, edge)
                parts.setdefault(int(rid), []).append(m)
        out['parts'] = {names.get(rid, str(rid)): dict(components=LA.summarize([m['components'] for m in ms]),
                                                        tiny_share=LA.summarize([m['tiny_share'] for m in ms]),
                                                        perim_ratio=LA.summarize([m['perim_ratio'] for m in ms if m['components']]))
                        for rid, ms in sorted(parts.items())}
    rdir = os.path.join(directory, 'report'); os.makedirs(rdir, exist_ok=True)
    json.dump(out, open(os.path.join(rdir, 'light_orbit.json'), 'w', encoding='utf-8'), indent=1)
    open(os.path.join(rdir, 'summary.txt'), 'w', encoding='utf-8').write(summary_text(out))
    if images:
        sheet(frames, angles, exprs, per, os.path.join(rdir, 'masks_sheet.png'))
    return out


def summary_text(r):
    f = r['face']
    L = ['light orbit: %s  %s' % (r['meta'].get('model'), r['meta'].get('setup_args', '')),
         'lit mask from: %s' % r['meta'].get('lit_source'),
         'frames %d | shadow components mean %.2f median %.1f max %d | single-component frames %.0f%%' % (
             f['frames'], f['components'].get('mean', 0), f['components'].get('median', 0), f['components'].get('max', 0),
             100 * f['single_share']),
         'tiny islands (<0.1%% face): share of components mean %.3f, share of shadow area mean %.4f' % (
             f['tiny_share'].get('mean', 0), f['tiny_area'].get('mean', 0)),
         'perimeter / hull perimeter: median %.3f p90 %.3f' % (f['perim_ratio'].get('median', 0), f['perim_ratio'].get('p90', 0)),
         'IoU vs neutral: mean %s min %s | outside eyes/mouth mean %s min %s' % tuple(
             '%.3f' % v if v is not None else '-' for v in (f['iou_mean'], f['iou_min'], f['iou_out_mean'], f['iou_out_min'])),
         'nesting: %s of neighbour pairs nested' % ('%.1f%%' % (100 * f['nesting_share']) if f['nesting_share'] is not None else '-'),
         'verdict %s, noisy score %.2f' % (r['verdict'], r['noisy_score'])]
    for e, s in r.get('face_by_expr', {}).items():
        L.append('  %-20s components mean %.2f max %d' % (e, s.get('mean', 0), s.get('max', 0)))
    for var, s in r.get('lamp_invariance', {}).items():
        L.append('lamp %-7s IoU with the base lamp: mean %.4f min %.4f' % (var, s['mean'], s['min']))
    for nm, s in r.get('parts', {}).items():
        L.append('part %-34s components mean %.2f max %d, ratio median %.2f' % (
            nm, s['components'].get('mean', 0), s['components'].get('max', 0), s['perim_ratio'].get('median', 0) if s['perim_ratio'].get('n') else 0))
    return '\n'.join(L) + '\n'


def sheet(frames, angles, exprs, per, path, thumb=64, every=2):
    from PIL import Image, ImageDraw
    cols = angles[::every]
    W, H = thumb * len(cols), thumb * len(exprs) + 14
    im = Image.new('RGB', (W + 150, H), (40, 40, 44))
    dr = ImageDraw.Draw(im)
    for j, a in enumerate(cols):
        dr.text((150 + j * thumb + 2, 1), '%d' % a, fill=(200, 200, 200))
    for i, e in enumerate(exprs):
        dr.text((2, 14 + i * thumb + thumb // 2 - 6), e[:22], fill=(220, 220, 220))
        for j, a in enumerate(cols):
            if (e, a) not in frames:
                continue
            sh, reg, dec = frames[(e, a)]
            rgb = np.full(sh.shape + (3,), 60, np.uint8)
            rgb[reg] = (238, 232, 222); rgb[sh] = (92, 64, 140)
            t = Image.fromarray(rgb).resize((thumb, thumb), Image.NEAREST)
            im.paste(t, (150 + j * thumb, 14 + i * thumb))
            n = per[(e, a)]['components']
            dr.text((150 + j * thumb + 2, 14 + i * thumb + thumb - 12), str(n), fill=(255, 80, 80) if n > 1 else (90, 200, 90))
    im.save(path)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('dir')
    ap.add_argument('--no-images', action='store_true')
    ap.add_argument('--tiny', type=float, default=0.001)
    ap.add_argument('--edge', type=int, default=2)
    a = ap.parse_args(argv)
    r = run(a.dir, a.tiny, a.edge, not a.no_images)
    sys.stdout.write(summary_text(r))
    return 0


if __name__ == '__main__':
    sys.exit(main())
