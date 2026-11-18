"""Пробник «Blender против three.js»: кадры серии лифта из готового задания — стоп-кадры Blender (как в серии, пресет
задания) + выгрузка тех же кадров для three.js (GLB с позой + json источников/камеры/взвеси).

    python tools/probe3d_blender.py --job D:/vvf-offload/render3d/job-2/job.json --frames 673 922 \
        --out D:/vvf-offload/tmp/probe3d/blender [--quality fast] [--repeat 2]

Один процесс Blender. Дальше — ``node node/scene3d/probe_compare.mjs`` (растр и трассировка путей в three.js).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import series3d_blender as B  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    ap.add_argument("--frames", type=int, nargs="+", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--quality", default=None)
    ap.add_argument("--repeat", type=int, default=2, help="рендеров каждого кадра (первый — с компиляцией шейдеров)")
    ap.add_argument("--packs", default=os.environ.get("MOTION_PACKS_DIR", "D:/vvf-offload/packs3d"))
    a = ap.parse_args()
    job = json.loads(Path(a.job).read_text("utf-8"))
    if a.quality:
        job["quality"] = a.quality
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    perf = B.perform(job)
    keys = B.chunk_keys(job, perf)
    (out / "job.json").write_text(json.dumps(job, ensure_ascii=False), "utf-8")
    cfg = {**B.scene_config(job, out, out / "cache", Path(a.packs)), "chunks": keys, "todo": {},
           "stills": a.frames, "still_fx": True, "still_repeat": a.repeat,
           "export_frames": a.frames, "export_dir": str(out / "export")}
    (out / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=1), "utf-8")
    exe = B.find_blender()
    t0 = time.time()
    with (out / "blender.log").open("w", encoding="utf-8", errors="replace") as log:
        code = subprocess.call([exe, "-b", "--factory-startup", "-P", str(ROOT / "tools" / "blender" / "series_scene.py"),
                                "--", "--config", str(out / "config.json")], stdout=log, stderr=subprocess.STDOUT)
    rep = json.loads((out / "report.json").read_text("utf-8")) if (out / "report.json").exists() else {}
    print(json.dumps({"code": code, "wall_sec": round(time.time() - t0, 1), "setup_sec": rep.get("setup_sec"),
                      "still_sec": rep.get("still_sec"), "exported": rep.get("exported"),
                      "preset": B.preset(job)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
