# -*- coding: utf-8 -*-
"""Model check in one go: render (Blender, headless) then analyse.

    python tools/model_check/run_check.py --model X.blend --out DIR [--poses poses.json] [--res 512] [...]

Every argument except --blender / --min-free-gb / --skip-render is passed to render_views.py. Before Blender starts,
the free commit memory is checked (Windows: GlobalMemoryStatusEx); below --min-free-gb it waits and retries.
Blender: --blender, else $MODEL_CHECK_BLENDER, else the repo's usual D:\\vvf-offload path, else 'blender' on PATH.
"""
import argparse
import ctypes
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import analyze  # noqa: E402

DEFAULT_BLENDER = r'D:\vvf-offload\tools\blender\blender-4.5.15-windows-x64\blender.exe'


def free_memory_gb():
    """(free physical, free commit) in GB, or (None, None) off Windows."""
    if os.name != 'nt':
        return None, None

    class MS(ctypes.Structure):
        _fields_ = [('dwLength', ctypes.c_ulong), ('dwMemoryLoad', ctypes.c_ulong),
                    ('ullTotalPhys', ctypes.c_ulonglong), ('ullAvailPhys', ctypes.c_ulonglong),
                    ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong),
                    ('ullTotalVirtual', ctypes.c_ulonglong), ('ullAvailVirtual', ctypes.c_ulonglong),
                    ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]
    m = MS()
    m.dwLength = ctypes.sizeof(MS)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.ullAvailPhys / 2.0 ** 30, m.ullAvailPageFile / 2.0 ** 30


def wait_for_memory(min_gb, max_wait_s=1800, step_s=30):
    t0 = time.time()
    while True:
        phys, commit = free_memory_gb()
        if commit is None or commit >= min_gb:
            if commit is not None:
                print('[run_check] free RAM %.1f GB, free commit %.1f GB' % (phys, commit), flush=True)
            return True
        if time.time() - t0 > max_wait_s:
            print('[run_check] free commit %.1f GB < %.1f GB for %d s, giving up' % (commit, min_gb, max_wait_s))
            return False
        print('[run_check] free commit %.1f GB < %.1f GB, waiting...' % (commit, min_gb), flush=True)
        time.sleep(step_s)


def blender_path(arg):
    for p in (arg, os.environ.get('MODEL_CHECK_BLENDER'), DEFAULT_BLENDER):
        if p and os.path.exists(p):
            return p
    return 'blender'


def main(argv=None):
    ap = argparse.ArgumentParser(prog='run_check.py', add_help=False)
    ap.add_argument('--blender', default=None)
    ap.add_argument('--min-free-gb', type=float, default=4.0)
    ap.add_argument('--skip-render', action='store_true', help='only re-run the analysis on DIR')
    ap.add_argument('--out', required=True)
    ap.add_argument('-h', '--help', action='store_true')
    a, rest = ap.parse_known_args(argv)
    if a.help:
        print(__doc__)
        return 0
    if not a.skip_render:
        if not wait_for_memory(a.min_free_gb):
            return 2
        cmd = [blender_path(a.blender), '-b', '--factory-startup', '-P', os.path.join(HERE, 'render_views.py'), '--',
               '--out', a.out] + rest
        t0 = time.time()
        proc = subprocess.Popen(cmd)
        print('[run_check] blender pid %d' % proc.pid, flush=True)
        rc = proc.wait()
        print('[run_check] render %.0f s, exit %d' % (time.time() - t0, rc), flush=True)
        if rc != 0 or not os.path.exists(os.path.join(a.out, 'model.json')):
            return rc or 1
    r = analyze.run(a.out)
    sys.stdout.write(analyze.summary_text(r))
    return 0


if __name__ == '__main__':
    sys.exit(main())
