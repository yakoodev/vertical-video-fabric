"""Сетка кадров превью (PNG из render.mjs --preview) в одну картинку с подписями секунд — смотреть глазами.

    python tools/meme3d_grid.py DIR out.jpg [--cols 5] [--width 360]
"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("out")
    ap.add_argument("--cols", type=int, default=5)
    ap.add_argument("--width", type=int, default=360)
    a = ap.parse_args()
    files = sorted(Path(a.dir).glob("frame_*.png"), key=lambda p: float(re.sub(r"frame_(\d+)_(\d+)\.png", r"\1.\2", p.name)))
    w, h = a.width, a.width * 16 // 9
    inputs: list[str] = []
    chains = []
    for i, f in enumerate(files):
        inputs += ["-i", str(f)]
        t = re.sub(r"frame_(\d+)_(\d+)\.png", r"\1.\2", f.name)
        chains.append(f"[{i}:v]scale={w}:{h},drawtext=text='{t}s':x=6:y=6:fontsize=22:fontcolor=white:box=1:boxcolor=black@0.6[v{i}]")
    n = len(files)
    layout = "|".join(f"{(i % a.cols) * w}_{(i // a.cols) * h}" for i in range(n))
    graph = ";".join(chains) + ";" + "".join(f"[v{i}]" for i in range(n)) + f"xstack=inputs={n}:layout={layout}:fill=white[o]"
    subprocess.run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", graph, "-map", "[o]", "-frames:v", "1",
                    "-q:v", "3", a.out], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
