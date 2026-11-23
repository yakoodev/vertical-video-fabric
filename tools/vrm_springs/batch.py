# -*- coding: utf-8 -*-
"""«Невезучий»: hair / cloth springs for every base and variation of the 3D preview (project 14), checked before and
after on the preview's own solver, plus side-by-side videos and a sheet.

    python -m tools.vrm_springs.batch fix        # -> OUT/vrm/*.vrm (+ reports), OUT/negative/*.vrm
    python -m tools.vrm_springs.batch metrics [--only name,name] [--which before,after,negative]
    python -m tools.vrm_springs.batch report     # -> OUT/summary.json, OUT/summary.md
    python -m tools.vrm_springs.batch videos [--only ...]
    python -m tools.vrm_springs.batch sheet

Heavy steps run one at a time (one headless Chrome). Sources are only read.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from . import analyze as AN
from . import clips as CL
from .fix_springs import main as fix_main

VROID = Path('D:/vvf-offload/series_unlucky/vroid')
REWORK = VROID / 'rework_2211' / 'vrm'
OUT = VROID / 'physics_2311'
ROOT = Path(__file__).resolve().parents[2]
SIM = ROOT / 'tools' / 'vrm_springs' / 'sim' / 'run_sim.mjs'
PROFILES = ROOT / 'tools' / 'vrm_springs' / 'profiles.json'
CLIPS = ['idle', 'walk', 'run', 'turn180', 'nod', 'shake', 'stairs']
SECONDS = 'stairs=9'   # the climb until the first landing; at 9.5 s the in-place ground correction jumps the body
# 3D-preview assets of project 14 these files are (README rework_2211); the hero base is the OLD hero (asset 1)
ASSET = {'hero.vrm': 1}
_REWORK_ORDER = ['hero__base', 'hero__hair_tied', 'hero__hair_wet', 'hero__hair_messy', 'hero__outfit_stage',
                 'hero__outfit_home', 'hero__hair_tied__outfit_home', 'guitarist__base', 'guitarist__sweater2',
                 'guitarist__sweater3', 'guitarist__hair_bun', 'guitarist__hair_short', 'guitarist__outfit_stage',
                 'guitarist__outfit_home', 'drummer__base', 'drummer__hair_bleach', 'drummer__hair_crop',
                 'drummer__outfit_stage', 'drummer__outfit_winter', 'bassist__base', 'bassist__hair_pony',
                 'bassist__hair_pigtails', 'bassist__outfit_stage', 'bassist__outfit_winter']
for _i, _n in enumerate(_REWORK_ORDER):
    ASSET[_n + '.vrm'] = 7 + _i
NEGATIVE = [('hero.vrm', 'floppy'), ('hero.vrm', 'unstable'), ('bassist__hair_pony.vrm', 'floppy')]
VIDEOS = [('hero.vrm', '34'), ('guitarist__base.vrm', '34'), ('drummer__hair_crop.vrm', '34'), ('bassist__base.vrm', '34'),
          ('hero__hair_tied.vrm', 'back34'), ('guitarist__hair_bun.vrm', 'back34'),
          ('bassist__hair_pony.vrm', 'back34'), ('bassist__hair_pigtails.vrm', 'back34')]


def models() -> list:
    return list(ASSET)


def src_of(name: str) -> Path:
    return VROID / 'hero.vrm' if name == 'hero.vrm' else REWORK / name


def rel(p: Path) -> str:
    return p.relative_to(VROID).as_posix()


def log(m):
    print(time.strftime('%H:%M:%S'), m, flush=True)


def cmd_fix(a):
    (OUT / 'vrm').mkdir(parents=True, exist_ok=True)
    (OUT / 'work' / 'reports').mkdir(parents=True, exist_ok=True)
    (OUT / 'negative').mkdir(parents=True, exist_ok=True)
    CL.build(OUT / 'work' / 'clips')
    for n in models():
        if a.only and n not in a.only:
            continue
        stem = n[:-4]
        key = 'hero' if n == 'hero.vrm' else stem
        fix_main([str(src_of(n)), str(OUT / 'vrm' / n), '--profile', str(PROFILES), '--key', key,
                  '--report', str(OUT / 'work' / 'reports' / (stem + '.json'))])
    for n, mode in NEGATIVE:
        fix_main([str(OUT / 'vrm' / n), str(OUT / 'negative' / ('%s__%s.vrm' % (n[:-4], mode))), '--break', mode])


def run_metrics(model_rel: str, out: Path, clips=CLIPS):
    out.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(['node', str(SIM), 'metrics', '--packs', str(VROID), '--model', model_rel, '--clips', ','.join(clips),
                        '--clipdir', 'physics_2311/work/clips', '--out', str(out), '--seconds', SECONDS],
                       cwd=str(ROOT), capture_output=True, text=True, encoding='utf-8', errors='replace')
    if r.returncode != 0:
        raise RuntimeError('%s: %s' % (model_rel, r.stderr[-800:]))


def cmd_metrics(a):
    which = a.which.split(',')
    jobs = []
    for n in models():
        if a.only and n not in a.only:
            continue
        if 'before' in which:
            jobs.append((rel(src_of(n)), OUT / 'work' / 'metrics' / ('%s__before.json' % n[:-4])))
        if 'after' in which:
            jobs.append((rel(OUT / 'vrm' / n), OUT / 'work' / 'metrics' / ('%s__after.json' % n[:-4])))
    if 'negative' in which:
        for n, mode in NEGATIVE:
            nm = '%s__%s' % (n[:-4], mode)
            jobs.append((rel(OUT / 'negative' / (nm + '.vrm')), OUT / 'work' / 'metrics' / ('%s__negative.json' % nm)))
    for i, (m, out) in enumerate(jobs):
        if out.exists() and not a.force:
            continue
        t0 = time.time()
        run_metrics(m, out)
        log('[%d/%d] %s %.0f s' % (i + 1, len(jobs), out.name, time.time() - t0))


def numbers(path: Path):
    return AN.model_numbers(json.loads(path.read_text(encoding='utf-8'))) if path.exists() else None


KEYS = ['swing_cm', 'sag_cm', 'face_frames', 'face_max_cm', 'body_frames', 'body_max_cm', 'rigid_body_frames',
        'cloth_frames', 'jumps', 'jitter_cm', 'nan', 'ok']


def cmd_report(a):
    names = json.loads((REWORK / 'names.json').read_text(encoding='utf-8'))
    M = OUT / 'work' / 'metrics'
    rows = {}
    for n in models():
        b, f = numbers(M / ('%s__before.json' % n[:-4])), numbers(M / ('%s__after.json' % n[:-4]))
        rep = OUT / 'work' / 'reports' / (n[:-4] + '.json')
        rows[n] = dict(asset=ASSET[n], name=('Герой (старая база)' if n == 'hero.vrm' else names.get(n, {}).get('name', n)),
                       before=b and b['total'], after=f and f['total'],
                       before_clips=b and b['clips'], after_clips=f and f['clips'],
                       springs=json.loads(rep.read_text(encoding='utf-8')) if rep.exists() else None)
    neg = {}
    for n, mode in NEGATIVE:
        nm = '%s__%s' % (n[:-4], mode)
        r = numbers(M / ('%s__negative.json' % nm))
        neg[nm] = r and r['total']
    (OUT / 'summary.json').write_text(json.dumps(dict(models=rows, negative=neg, limits=AN.LIMITS, clips=CLIPS,
                                                      seconds=SECONDS), ensure_ascii=False, indent=1), encoding='utf-8')
    lines = ['| модель | № | качание, см | отход от формы, см | волосы в лице, кадров | в теле, кадров | одежда в теле | рывки | дрожь, см | NaN | ок |',
             '|---|---|---|---|---|---|---|---|---|---|---|']

    def cell(b, f, k, fmt='%s'):
        if not b or not f:
            return '—'
        return '%s → **%s**' % (fmt % b.get(k, '—'), fmt % f.get(k, '—'))
    for n, r in rows.items():
        b, f = r['before'], r['after']
        lines.append('| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |' % (
            r['name'], r['asset'], cell(b, f, 'swing_cm'), cell(b, f, 'sag_cm'), cell(b, f, 'face_frames'),
            cell(b, f, 'body_frames'), cell(b, f, 'cloth_frames'), cell(b, f, 'jumps'), cell(b, f, 'jitter_cm'),
            cell(b, f, 'nan'), cell(b, f, 'ok')))
    lines += ['', '| негативный контроль | качание | в лице | в теле | одежда | рывки | дрожь | ok |', '|---|---|---|---|---|---|---|---|']
    for nm, t in neg.items():
        if t:
            lines.append('| %s | %s | %s | %s | %s | %s | %s | %s |' % (nm, t['swing_cm'], t['face_frames'], t['body_frames'],
                                                                      t['cloth_frames'], t['jumps'], t.get('jitter_cm'), t['ok']))
    (OUT / 'summary.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print('\n'.join(lines))


def cmd_videos(a):
    vd = OUT / 'video'
    vd.mkdir(parents=True, exist_ok=True)
    tmp = OUT / 'work' / 'video'
    tmp.mkdir(parents=True, exist_ok=True)
    font = r'C\:/Windows/Fonts/arial.ttf'
    for n, view in VIDEOS:
        if a.only and n not in a.only:
            continue
        stem = n[:-4]
        parts = []
        for v in (view, 'side' if view == '34' else '34'):
            part = tmp / ('%s__%s.mp4' % (stem, v))
            subprocess.run(['node', str(SIM), 'video', '--packs', str(VROID), '--models',
                            '%s,%s' % (rel(src_of(n)), rel(OUT / 'vrm' / n)), '--clip', 'physics_2311/work/clips/seq.json',
                            '--view', v, '--dist', '1.7', '--size', '1280x720', '--out', str(part)],
                           cwd=str(ROOT), check=True, capture_output=True)
            parts.append(part)
        out = vd / ('%s__before_after.mp4' % stem)
        lab = ("drawtext=fontfile='%s':text='%s':x=%s:y=20:fontsize=34:fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=8")
        vf = ('[0][1]vstack=2,%s,%s,drawbox=x=638:y=0:w=4:h=1440:color=white@0.9:t=fill' %
              (lab % (font, 'до', '24'), lab % (font, 'после', '664')))
        subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', str(parts[0]), '-i', str(parts[1]),
                        '-filter_complex', vf, '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '20', str(out)], check=True)
        log('video %s' % out)


def compare_video(a_rel: str, b_rel: str, labels, out: Path, rows=(('34', 1.45), ('side', 1.45), ('34', 0.75)),
                  clip='physics_2311/work/clips/seq.json'):
    """one model in two versions, side by side (a | b), stacked views: 3/4, side, a close crop of the head"""
    tmp = OUT / 'work' / 'video'
    tmp.mkdir(parents=True, exist_ok=True)
    parts = []
    for k, (view, dist) in enumerate(rows):
        part = tmp / ('%s__row%d.mp4' % (out.stem, k))
        subprocess.run(['node', str(SIM), 'video', '--packs', str(VROID), '--models', '%s,%s' % (a_rel, b_rel),
                        '--clip', clip, '--view', view, '--dist', str(dist), '--size', '1280x640', '--out', str(part)],
                       cwd=str(ROOT), check=True, capture_output=True)
        parts.append(part)
    font = r'C\:/Windows/Fonts/arial.ttf'
    lab = "drawtext=fontfile='%s':text='%s':x=%s:y=16:fontsize=32:fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=8"
    n = len(parts)
    vf = ''.join('[%d]' % i for i in range(n)) + 'vstack=%d,%s,%s,drawbox=x=638:y=0:w=4:h=%d:color=white@0.9:t=fill' % (
        n, lab % (font, labels[0], '20'), lab % (font, labels[1], '660'), 640 * n)
    cmd = ['ffmpeg', '-y', '-loglevel', 'error']
    for p_ in parts:
        cmd += ['-i', str(p_)]
    subprocess.run(cmd + ['-filter_complex', vf, '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '20', str(out)], check=True)
    log('video %s' % out)


def cmd_sheet(a):
    """all models: before | after at the sudden stop and in the head shake (3/4 view), one grid"""
    from PIL import Image, ImageDraw, ImageFont
    tmp = OUT / 'work' / 'sheet'
    tmp.mkdir(parents=True, exist_ok=True)
    names = json.loads((REWORK / 'names.json').read_text(encoding='utf-8'))
    ats = ['2.62', '4.30']
    tiles = []
    for n in models():
        stem = n[:-4]
        pref = tmp / stem
        if not (tmp / ('%s_%s.jpg' % (stem, ats[-1]))).exists() or a.force:
            subprocess.run(['node', str(SIM), 'still', '--packs', str(VROID), '--models',
                            '%s,%s' % (rel(src_of(n)), rel(OUT / 'vrm' / n)), '--clip', 'physics_2311/work/clips/seq.json',
                            '--view', 'back34' if any(k in n for k in ('tied', 'pony', 'pigtails', 'bun')) else '34',
                            '--dist', '1.45', '--size', '960x540', '--at', ','.join(ats), '--out', str(pref)],
                           cwd=str(ROOT), check=True, capture_output=True)
        title = 'Герой (старая база)' if n == 'hero.vrm' else names.get(n, {}).get('name', n)
        tiles.append((title, ASSET[n], [tmp / ('%s_%s.jpg' % (stem, t)) for t in ats]))
        log('sheet tile %s' % stem)
    tw, th = 480, 270
    cols = 2
    rows = (len(tiles) + cols - 1) // cols
    W = cols * (2 * tw + 16)
    H = rows * (th + 36) + 60
    sheet = Image.new('RGB', (W, H), (245, 245, 248))
    d = ImageDraw.Draw(sheet)
    try:
        f = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 18)
        fb = ImageFont.truetype('C:/Windows/Fonts/arialbd.ttf', 22)
    except OSError:
        f = fb = ImageFont.load_default()
    d.text((12, 14), 'Физика волос: до | после в каждой половине кадра. Слева — резкая остановка после бега, справа — '
           'быстрое «нет» головой', font=fb, fill=(20, 20, 20))
    for i, (title, asset, ims) in enumerate(tiles):
        c, r = i % cols, i // cols
        x0, y0 = c * (2 * tw + 16), 60 + r * (th + 36)
        d.text((x0 + 8, y0 + 4), '%s  (№%d)' % (title, asset), font=f, fill=(20, 20, 20))
        for k, p in enumerate(ims):
            im = Image.open(p).resize((tw, th))
            sheet.paste(im, (x0 + k * tw, y0 + 30))
    sheet.save(OUT / 'sheet_before_after.jpg', quality=88)
    log('sheet %s' % (OUT / 'sheet_before_after.jpg'))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['fix', 'metrics', 'report', 'videos', 'sheet'])
    ap.add_argument('--only', default='')
    ap.add_argument('--which', default='before,after,negative')
    ap.add_argument('--force', action='store_true')
    a = ap.parse_args(argv)
    a.only = set(x if x.endswith('.vrm') else x + '.vrm' for x in a.only.split(',') if x)
    {'fix': cmd_fix, 'metrics': cmd_metrics, 'report': cmd_report, 'videos': cmd_videos, 'sheet': cmd_sheet}[a.cmd](a)


if __name__ == '__main__':
    sys.exit(main())
