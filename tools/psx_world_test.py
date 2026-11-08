"""Проба «Мира от зрителей» (проект 10) в стиле PS1: стопкадры, ролик облёта, лист.

    python tools/psx_world_test.py [node/vrm/examples/psx_world.json] [--out D:/vvf-offload/vtubika/psx]
        [--packs D:/vvf-offload/vtubika/vrm/vendor] [--gl gpu] [--only stills|video]

1. Сборка ``npm run build:psx`` (бандл ``node/vrm/dist/psx.js``, мир — ``node/vrm/src/psx``).
2. Стопкадры: ``spec.stills`` → ``node/vrm/stills.mjs`` → ``<out>/<имя>.png``.
3. Ролик: ``spec.shots`` → ``node/scene3d/render.mjs`` (один браузер: пружины волос считаются подряд) →
   ``<out>/psx_test.mp4``. Проверка присутствия героини — в странице на каждом кадре (иначе рендер падает),
   чёрные кадры — render.mjs.
4. Проверка склеек: оценка смены сцены ffmpeg ``scdet`` по каждому кадру; склейка, которой нет в ``spec.cuts``, —
   ошибка (облёт должен идти без рывков).
5. Лист ``<out>/sheet.jpg``: стопкадры сверху, кадры ролика снизу.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCD = re.compile(r"lavfi\.scd\.score=([0-9.]+)")


def scene_scores(video: Path) -> list[float]:
    """Оценка смены сцены (0..100) для каждого кадра, начиная со второго (ffmpeg scdet)."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(video), "-vf",
                        "scale=270:480:flags=area,scdet=threshold=100,metadata=print:key=lavfi.scd.score", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    return parse_scores(r.stderr)


def parse_scores(log: str) -> list[float]:
    return [float(m.group(1)) for m in SCD.finditer(log)]


def find_cuts(scores: list[float], fps: float, threshold: float = 14.0) -> list[float]:
    """Время кадров, где картинка сменилась скачком (оценка выше порога и заметно выше соседей)."""
    cuts = []
    for i, s in enumerate(scores):
        near = [scores[j] for j in (i - 2, i - 1, i + 1, i + 2) if 0 <= j < len(scores)]
        if s >= threshold and s > 2.5 * (max(near) if near else 0):
            cuts.append(round((i + 1) / fps, 3))
    return cuts


def unexpected_cuts(found: list[float], declared: list[float], fps: float) -> list[float]:
    return [t for t in found if not any(abs(t - d) <= 1.5 / fps for d in declared)]


def build() -> None:
    subprocess.run(["npm", "run", "build:psx"], cwd=ROOT, check=True, shell=sys.platform == "win32")


def stills(spec: dict, out: Path, a: argparse.Namespace) -> list[Path]:
    with tempfile.TemporaryDirectory() as tmp:
        sp = Path(tmp) / "stills.json"
        sp.write_text(json.dumps({**spec, "shots": spec["stills"]}, ensure_ascii=False), "utf-8")
        subprocess.run(["node", str(ROOT / "node/vrm/stills.mjs"), "--spec", str(sp), "--out", str(out), "--bundle",
                        str(ROOT / "node/vrm/dist/psx.js"), "--packs", a.packs, "--gl", a.gl, "--no-sheet"], check=True)
    return [out / f"{s['name']}.png" for s in spec["stills"]]


def video(spec_path: Path, spec: dict, out: Path, a: argparse.Namespace) -> Path:
    mp4 = out / "psx_test.mp4"
    r = subprocess.run(["node", str(ROOT / "node/scene3d/render.mjs"), "--spec", str(spec_path), "--out", str(mp4),
                        "--bundle", str(ROOT / "node/vrm/dist/psx.js"), "--packs", a.packs, "--gl", a.gl, "--workers", "1",
                        "--format", "png", "--crf", "14"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SystemExit(f"рендер упал: {r.stderr[-1500:]}")
    res = json.loads(r.stdout.strip().splitlines()[-1])
    print(f"ролик: {res['frames']} кадров, чёрных {len(res['black_frames'])}, яркость мин {res['luma_min']}")
    fps = float(spec.get("fps", 30))
    scores = scene_scores(mp4)
    found = find_cuts(scores, fps)
    bad = unexpected_cuts(found, spec.get("cuts", []), fps)
    print(f"склейки: найдено {found}, объявлено {spec.get('cuts', [])}, макс. оценка {max(scores):.1f}")
    if bad:
        raise SystemExit(f"склейки не по плану: {bad}")
    return mp4


def sheet(stills_png: list[Path], mp4: Path | None, duration: float, out: Path) -> None:
    """Лист: ряд стопкадров (по 360×640) и ряд кадров ролика."""
    with tempfile.TemporaryDirectory() as tmp:
        tiles = list(stills_png)
        if mp4:
            for i, t in enumerate([0.4, duration * 0.33, duration * 0.6, duration - 0.2]):
                f = Path(tmp) / f"v{i}.png"
                subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t:.2f}", "-i", str(mp4),
                                "-frames:v", "1", str(f)], check=True)
                tiles.append(f)
        cols = 4
        inputs = [x for f in tiles for x in ("-i", str(f))]
        scale = ";".join(f"[{i}:v]scale=360:640:flags=neighbor[s{i}]" for i in range(len(tiles)))
        layout = "|".join(f"{(i % cols) * 360}_{(i // cols) * 640}" for i in range(len(tiles)))
        filt = f"{scale};{''.join(f'[s{i}]' for i in range(len(tiles)))}xstack=inputs={len(tiles)}:layout={layout}:fill=black"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *inputs, "-filter_complex", filt,
                        "-frames:v", "1", "-q:v", "2", str(out)], check=True)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("spec", type=Path, nargs="?", default=ROOT / "node/vrm/examples/psx_world.json")
    ap.add_argument("--out", type=Path, default=Path("D:/vvf-offload/vtubika/psx"))
    ap.add_argument("--packs", default="D:/vvf-offload/vtubika/vrm/vendor")
    ap.add_argument("--gl", default="gpu")
    ap.add_argument("--only", choices=["stills", "video"])
    a = ap.parse_args(argv)
    spec = json.loads(a.spec.read_text("utf-8"))
    a.out.mkdir(parents=True, exist_ok=True)
    build()
    pngs = stills(spec, a.out, a) if a.only != "video" else [a.out / f"{s['name']}.png" for s in spec["stills"]]
    mp4 = video(a.spec.resolve(), spec, a.out, a) if a.only != "stills" else None
    if mp4 is None and (a.out / "psx_test.mp4").is_file():
        mp4 = a.out / "psx_test.mp4"
    sheet([p for p in pngs if p.is_file()], mp4, float(spec.get("duration", 8)), a.out / "sheet.jpg")
    print(a.out / "sheet.jpg")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
