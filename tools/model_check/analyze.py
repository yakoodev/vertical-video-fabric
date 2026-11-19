# -*- coding: utf-8 -*-
"""Model check, analysis side: reads the renders of render_views.py and writes the report.

    python tools/model_check/analyze.py DIR [--min-poke-px 4] [--eps-mm 0.9] [--no-images]

Writes DIR/report/report.json (findings grouped across views, severity, area, objects, 3D location, worst views,
overall score), DIR/report/overlays/<pose>__<view>.png (beauty | heatmap of the worst views) and
DIR/report/contact_<pose>.png (all views with hotspots marked) + DIR/report/summary.txt.
Needs numpy + Pillow (the repo .venv has both; Python 3.6+).
"""
import argparse
import glob
import json
import os
import sys
import time

import numpy as np

try:
    from . import analysis as A
except (ImportError, ValueError):                      # run as a script
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import analysis as A  # noqa: E402

COLORS = {'poke_through': (255, 30, 30), 'contact': (255, 150, 40), 'zfight': (255, 230, 0),
          'id_shimmer': (0, 220, 255), 'texture_flicker': (255, 0, 255)}


def load_png(path):
    from PIL import Image
    if not os.path.exists(path):
        return None
    return np.asarray(Image.open(path).convert('RGBA'), np.float32) / 255.0


def part_name(parts_by_id, pid):
    p = parts_by_id.get(pid)
    return '%s/%s' % (p['object'], p['material']) if p else '#%s' % pid


def count_parts(ids, mask, parts_by_id):
    out = {}
    for pid, cnt in A.top_values(ids[mask], 3):
        out[part_name(parts_by_id, pid)] = cnt
    return out


def analyze_pose(pdir, params_over, want_images):
    meta = json.load(open(os.path.join(pdir, 'meta.json'), encoding='utf-8'))
    pose = meta['pose']
    p = A.default_params(meta['height'], meta['pixel_size'])
    p.update(params_over)
    parts_by_id = {q['id']: q for q in meta['parts']}
    res = meta['res']
    findings = []
    masks = {}                                         # view -> {type: bool mask} for the pictures
    backface = {}                                      # part -> [bf pixels, all pixels] (flipped-normal hint)
    for v in meta['views']:
        vn = v['name']
        allz = np.load(os.path.join(pdir, 'views', vn + '_all.npz'))
        ids, ids_j = allz['id'].astype(np.int32), allz['id_j'].astype(np.int32)
        bf_all = allz['bf']
        for pid in np.unique(ids):
            if pid:
                m = ids == pid
                acc = backface.setdefault(int(pid), [0, 0])
                acc[0] += int(bf_all[m].sum())
                acc[1] += int(m.sum())
        dirs = A.ray_dirs(v, res, res)
        lay = {}
        for layer in meta['layers']:
            f = os.path.join(pdir, 'views', '%s_%s.npz' % (vn, layer))
            if os.path.exists(f):
                z = np.load(f)
                lay[layer] = (z['d'].astype(np.float64), z['id'].astype(np.int32), z['bf'])
        vm = {}

        def add(kind, mask):
            vm[kind] = vm.get(kind, np.zeros(ids.shape, bool)) | mask

        # a) + b) poke-through and near-coincident surfaces per layer pair
        for inner, outer in meta['pairs']:
            if inner not in lay or outer not in lay:
                continue
            d_i, id_i, _ = lay[inner]
            d_o, id_o, bf_o = lay[outer]
            vis_in = (ids == id_i) & (id_i > 0)
            vis_any = vis_in | ((ids == id_o) & (id_o > 0))
            pokes, zfs = A.poke_and_zfight(d_i, d_o, bf_o, p, vis_in, vis_any)
            for b in pokes + zfs:
                kind = b['kind']
                loc_d = d_i if kind != 'zfight' else d_o
                pos = A.unproject_mean(b['mask'], loc_d, v, dirs)
                bone, bd = A.nearest_bone(pos, meta.get('bones'))
                parts = count_parts(id_i, b['mask'], parts_by_id)
                parts.update(count_parts(id_o, b['mask'], parts_by_id))
                f = {'type': kind, 'pose': pose, 'view': vn, 'area': b['area'], 'pos': pos, 'bone': bone,
                     'inner_layer': inner, 'outer_layer': outer, 'parts': parts}
                for k in ('enclosure', 'crossing', 'penetration', 'gap'):
                    if k in b:
                        f[k] = b[k]
                f['_mask'] = b['mask']
                findings.append(f)
                add(kind, b['mask'])
        # b2) id shimmer between theta and theta + jitter
        d_all = allz['d'].astype(np.float64) if 'd' in allz.files else None
        for b in A.id_shimmer(ids, ids_j, p):
            pos = A.unproject_mean(b['mask'], d_all, v, dirs) if d_all is not None else None
            bone, _ = A.nearest_bone(pos, meta.get('bones'))
            vals = np.concatenate([ids[A.dilate(b['mask'], 1)], ids_j[A.dilate(b['mask'], 1)]])
            parts = {part_name(parts_by_id, pid): c for pid, c in A.top_values(vals, 3)}
            findings.append({'type': 'id_shimmer', 'pose': pose, 'view': vn, 'area': b['area'], 'pos': pos,
                             'bone': bone, 'parts': parts, '_mask': b['mask']})
            add('id_shimmer', b['mask'])
        # c) texture flicker on the beauty frames
        ba = load_png(os.path.join(pdir, 'beauty', vn + '.png'))
        bb = load_png(os.path.join(pdir, 'beauty', vn + '_j.png'))
        if ba is not None and bb is not None:
            for b in A.texture_flicker(ba, bb, p):
                pid, _ = A.majority(ids[b['mask']])
                pos = A.unproject_mean(b['mask'], d_all, v, dirs) if d_all is not None else None
                bone, _ = A.nearest_bone(pos, meta.get('bones'))
                findings.append({'type': 'texture_flicker', 'pose': pose, 'view': vn, 'area': b['area'],
                                 'pos': pos, 'bone': bone, 'part': part_name(parts_by_id, pid),
                                 'parts': {part_name(parts_by_id, pid): b['area']}, 'strength': b['strength'],
                                 '_mask': b['mask']})
                add('texture_flicker', b['mask'])
        if vm:
            masks[vn] = vm
    clusters = A.cluster(findings, p['cluster_r'])
    for i, c in enumerate(clusters, 1):
        c['id'] = '%s#%d' % (pose, i)
        bones = [f.get('bone') for f in c['_members'] if f.get('bone')]
        c['near_bone'] = max(set(bones), key=bones.count) if bones else None
    notes = []
    for pid, (nb, na) in sorted(backface.items()):
        if na > 200 and nb / float(na) > 0.25:
            notes.append('%s: %.0f%% of its visible pixels are back faces (flipped normals or an open shell seen '
                         'from inside)' % (part_name(parts_by_id, pid), 100.0 * nb / na))
    return meta, p, clusters, masks, notes


# ----------------------------------------------------------------------------- pictures
def _font(size):
    from PIL import ImageFont
    for f in ('arial.ttf', 'DejaVuSans.ttf', 'segoeui.ttf'):
        try:
            return ImageFont.truetype(f, size)
        except Exception:
            pass
    return ImageFont.load_default()


def heat_image(beauty, vm, clusters_here, scale=1):
    """Dimmed greyscale beauty + coloured finding masks + circles/labels."""
    from PIL import Image, ImageDraw
    if beauty is None:
        beauty = np.zeros((512, 512, 4), np.float32)
    c, a = A.composite(beauty, 0.15)
    g = (c.mean(2, keepdims=True) * 0.55 + 0.05)
    img = np.repeat(g, 3, 2)
    for kind, m in vm.items():
        col = np.array(COLORS.get(kind, (255, 255, 255)), np.float32) / 255.0
        grow = A.dilate(m, 1)
        img[grow] = img[grow] * 0.25 + col * 0.75
    im = Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8))
    if scale != 1:
        im = im.resize((im.width * scale, im.height * scale), Image.NEAREST)
    dr = ImageDraw.Draw(im)
    fnt = _font(max(11, 7 * scale))
    for c_, m in clusters_here:
        ys, xs = np.nonzero(m)
        if len(ys) == 0:
            continue
        x0, x1, y0, y1 = xs.min() * scale, (xs.max() + 1) * scale, ys.min() * scale, (ys.max() + 1) * scale
        pad = 6 * scale
        col = COLORS.get(c_['type'], (255, 255, 255))
        dr.ellipse([x0 - pad, y0 - pad, x1 + pad, y1 + pad], outline=col, width=max(1, scale))
        dr.text((x1 + pad + 2, max(0, y0 - pad)), c_['id'].split('#')[1], fill=col, font=fnt)
    return im


def write_overlays(pdir, meta, clusters, masks, out_dir):
    from PIL import Image, ImageDraw
    os.makedirs(out_dir, exist_ok=True)
    written = {}
    by_view = {}
    for c in clusters:
        if c['severity'] == 'info':
            continue
        for f in c['_members'][:1]:                      # the worst view of each cluster
            by_view.setdefault(f['view'], []).append((c, f['_mask']))
    for vn, items in by_view.items():
        beauty = load_png(os.path.join(pdir, 'beauty', vn + '.png'))
        heat = heat_image(beauty, masks.get(vn, {}), items, scale=2)
        W, H = heat.size
        left = Image.new('RGB', (W, H), (40, 40, 40))
        if beauty is not None:
            cb, _ = A.composite(beauty, 0.16)
            left = Image.fromarray((cb * 255).astype(np.uint8)).resize((W, H), Image.LANCZOS)
        sheet = Image.new('RGB', (W * 2, H + 26 + 18 * len(items)), (20, 20, 20))
        sheet.paste(left, (0, 0))
        sheet.paste(heat, (W, 0))
        dr = ImageDraw.Draw(sheet)
        fnt = _font(14)
        dr.text((6, H + 4), '%s  %s' % (meta['pose'], vn), fill=(230, 230, 230), font=fnt)
        for k, (c, _) in enumerate(items):
            txt = '#%s %s [%s] %s | %d views, %d px%s' % (
                c['id'].split('#')[1], c['type'], c['severity'], ', '.join(list(c['parts'])[:3]), c['n_views'],
                c['max_area_px'], (', %.1f mm' % c['max_penetration_mm']) if 'max_penetration_mm' in c else '')
            dr.text((6, H + 24 + 18 * k), txt, fill=COLORS.get(c['type'], (255, 255, 255)), font=fnt)
        path = os.path.join(out_dir, '%s__%s.png' % (meta['pose'], vn))
        sheet.save(path)
        for c, _ in items:
            written.setdefault(c['id'], []).append(path)
    return written


def write_contact(pdir, meta, clusters, masks, path, thumb=192, max_cols=12):
    from PIL import Image, ImageDraw
    els = sorted({v['el'] for v in meta['views']})
    azs = sorted({v['az'] for v in meta['views']})
    worst = {}
    for c in clusters:
        if c['severity'] != 'info':
            worst.setdefault(c['_members'][0]['view'], []).append(c)
    cols = min(len(azs), max_cols)
    wraps = (len(azs) + cols - 1) // cols
    sheet = Image.new('RGB', (thumb * cols, (thumb + 16) * len(els) * wraps + 24), (18, 18, 18))
    dr = ImageDraw.Draw(sheet)
    fnt = _font(12)
    dr.text((6, 4), 'pose %s   red=poke  orange=contact  yellow=z-fight  cyan=id shimmer  magenta=texture flicker'
            % meta['pose'], fill=(230, 230, 230), font=fnt)
    for v in meta['views']:
        r, k = els.index(v['el']), azs.index(v['az'])
        beauty = load_png(os.path.join(pdir, 'beauty', v['name'] + '.png'))
        vm = {kk: m for kk, m in masks.get(v['name'], {}).items() if kk != 'contact'}
        im = heat_image(beauty, {kk: A.dilate(m, 2) for kk, m in vm.items()}, []).resize((thumb, thumb),
                                                                                          Image.BILINEAR)
        x, y = (k % cols) * thumb, 24 + (r * wraps + k // cols) * (thumb + 16)
        sheet.paste(im, (x, y))
        lab = v['name']
        if v['name'] in worst:
            dr.rectangle([x, y, x + thumb - 1, y + thumb - 1], outline=(255, 60, 60), width=2)
            lab += ' #' + ','.join(c['id'].split('#')[1] for c in worst[v['name']])
        dr.text((x + 3, y + thumb + 1), lab, fill=(200, 200, 200), font=fnt)
    sheet.save(path)


# ----------------------------------------------------------------------------- main
def clean_cluster(c, overlays):
    out = {k: v for k, v in c.items() if not k.startswith('_')}
    out['overlays'] = overlays.get(c['id'], [])
    return out


def run(directory, params_over=None, want_images=True):
    t0 = time.time()
    params_over = params_over or {}
    rep_dir = os.path.join(directory, 'report')
    os.makedirs(rep_dir, exist_ok=True)
    for old in glob.glob(os.path.join(rep_dir, 'overlays', '*.png')) + glob.glob(os.path.join(rep_dir, '*.png')):
        os.remove(old)                                 # stale pictures of an earlier analysis
    model = {}
    mp = os.path.join(directory, 'model.json')
    if os.path.exists(mp):
        model = json.load(open(mp, encoding='utf-8'))
    all_clusters, poses, notes = [], [], []
    for pdir in sorted(glob.glob(os.path.join(directory, 'pose_*'))):
        if not os.path.exists(os.path.join(pdir, 'meta.json')):
            continue
        meta, p, clusters, masks, nts = analyze_pose(pdir, params_over, want_images)
        overlays = {}
        if want_images:
            overlays = write_overlays(pdir, meta, clusters, masks, os.path.join(rep_dir, 'overlays'))
            write_contact(pdir, meta, clusters, masks, os.path.join(rep_dir, 'contact_%s.png' % meta['pose']))
        poses.append({'pose': meta['pose'], 'score': A.score(clusters), 'clean': A.is_clean(clusters),
                      'summary': A.summarize(clusters), 'n_views': len(meta['views']),
                      'pixel_size_mm': round(1000 * meta['pixel_size'], 2),
                      'params': {k: (round(v, 6) if isinstance(v, float) else v) for k, v in p.items()},
                      'contact_sheet': os.path.join(rep_dir, 'contact_%s.png' % meta['pose']) if want_images else None,
                      'warnings': meta.get('warnings', [])})
        notes.extend('%s: %s' % (meta['pose'], n) for n in nts)
        all_clusters.extend(clean_cluster(c, overlays) for c in clusters)
    order = {'high': 0, 'medium': 1, 'low': 2, 'info': 3}
    all_clusters.sort(key=lambda c: (order[c['severity']], -c['max_area_px']))
    report = {
        'model': model.get('model'), 'score': A.score(all_clusters), 'clean': A.is_clean(all_clusters),
        'summary': A.summarize(all_clusters), 'poses': poses, 'findings': all_clusters, 'notes': notes,
        'parts': model.get('parts'), 'pairs': model.get('pairs'), 'render_timings': model.get('timings'),
        'analysis_s': round(time.time() - t0, 1),
    }
    with open(os.path.join(rep_dir, 'report.json'), 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=1)
    with open(os.path.join(rep_dir, 'summary.txt'), 'w', encoding='utf-8') as f:
        f.write(summary_text(report))
    return report


def summary_text(r):
    lines = ['model: %s' % r['model'], 'score: %s  clean: %s  %s' % (r['score'], r['clean'], r['summary'])]
    for p in r['poses']:
        lines.append('  pose %-12s score %5s  %s' % (p['pose'], p['score'], p['summary']))
    lines.append('')
    for c in r['findings']:
        if c['severity'] == 'info':
            continue
        extra = ''
        if 'max_penetration_mm' in c:
            extra = ' pen %.1f mm, encl %.2f, cross %.2f' % (c['max_penetration_mm'], c['enclosure'], c['crossing'])
        if 'mean_gap_mm' in c:
            extra = ' gap %.2f mm' % c['mean_gap_mm']
        lines.append('%-16s %-15s %-6s %2d views %5d px  near %-18s %s  %s%s' % (
            c['id'], c['type'], c['severity'], c['n_views'], c['max_area_px'], c.get('near_bone') or '-',
            A.fmt_loc(c['location']), ', '.join(list(c['parts'])[:3]), extra))
        if c.get('overlays'):
            lines.append('%16s overlay: %s' % ('', c['overlays'][0]))
    n_info = sum(1 for c in r['findings'] if c['severity'] == 'info')
    lines.append('')
    lines.append('%d info-level contacts not listed (see report.json)' % n_info)
    for n in r.get('notes', []):
        lines.append('note: ' + n)
    return '\n'.join(lines) + '\n'


def main(argv=None):
    ap = argparse.ArgumentParser(prog='analyze.py')
    ap.add_argument('dir')
    ap.add_argument('--eps-mm', type=float, default=None, help='poke / z-fight tolerance (default 0.06%% of height)')
    ap.add_argument('--min-poke-px', type=int, default=None)
    ap.add_argument('--min-zfight-px', type=int, default=None)
    ap.add_argument('--tex-thr', type=float, default=None)
    ap.add_argument('--no-images', action='store_true')
    a = ap.parse_args(argv)
    over = {}
    if a.eps_mm is not None:
        over['eps_poke'] = over['eps_zfight'] = a.eps_mm / 1000.0
    if a.min_poke_px is not None:
        over['min_poke_px'] = a.min_poke_px
    if a.min_zfight_px is not None:
        over['min_zfight_px'] = a.min_zfight_px
    if a.tex_thr is not None:
        over['tex_thr'] = a.tex_thr
    r = run(a.dir, over, not a.no_images)
    sys.stdout.write(summary_text(r))
    return r


if __name__ == '__main__':
    main()
