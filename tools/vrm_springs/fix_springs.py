# -*- coding: utf-8 -*-
"""Rebuild the spring bones of a VRoid VRM 0.x (see springs.py) and write a copy.

    python -m tools.vrm_springs.fix_springs IN.vrm OUT.vrm [--profile profile.json] [--key hero] [--report r.json]
    python -m tools.vrm_springs.fix_springs IN.vrm OUT.vrm --break floppy|unstable     # negative controls

profile.json: {"<key>": {"params": {"back": {"gravityPower": 1.0}}, "tails": [...]}, ...}; the key defaults to the
file name without extension, then to its character prefix (``hero__hair_tied`` -> ``hero``).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .glb import Glb
from . import springs as S


def profile_for(profiles: dict, name: str) -> dict:
    out = {}
    char = name.split('__')[0]
    for key in (char, name):                     # character defaults, then the file's own entry on top
        p = profiles.get(key)
        if not p:
            continue
        for k, v in p.items():
            if k == 'params':
                for cls, pv in v.items():
                    out.setdefault('params', {}).setdefault(cls, {}).update(pv)
            else:
                out[k] = v
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('src')
    ap.add_argument('dst')
    ap.add_argument('--profile', default='')
    ap.add_argument('--key', default='')
    ap.add_argument('--report', default='')
    ap.add_argument('--break', dest='brk', default='')
    a = ap.parse_args(argv)
    g = Glb.load(a.src)
    if a.brk:
        S.break_springs(g, a.brk)
        rep = {'broken': a.brk}
    else:
        profiles = json.loads(Path(a.profile).read_text(encoding='utf-8')) if a.profile else {}
        prof = profile_for(profiles, a.key or Path(a.dst).stem.replace('_phys', ''))
        rep = S.fix(g, prof)
        rep['profile'] = prof
    Path(a.dst).parent.mkdir(parents=True, exist_ok=True)
    g.save(a.dst)
    if a.report:
        Path(a.report).write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps({k: rep[k] for k in rep if k in ('hair', 'tails', 'broken')}, ensure_ascii=False)[:2000])


if __name__ == '__main__':
    sys.exit(main())
