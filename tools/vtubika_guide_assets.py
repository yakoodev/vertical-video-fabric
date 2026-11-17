"""Примеры аватаров для мини-гайдов «Витубики» из своего каста VRM (CC0, app/vtubika_vrm_stage.py) — без чужих превью.

    python tools/vtubika_guide_assets.py pngtuber --base-spec D:/vvf-offload/vtubika/cast/work/specs/bibi_stand.json
        --out D:/vvf-offload/vtubika/guide1_model_types/assets/png_varya
    python tools/vtubika_guide_assets.py turntable --base-spec D:/vvf-offload/vtubika/cast/work/specs/vita_stand.json
        --out D:/vvf-offload/vtubika/guide1_model_types/assets/turn_asya [--frames 48] [--fps 12]

* ``pngtuber`` — «картинка PNGtuber» из 3D-модели: бюст анфас, кадры ``idle`` (молчит), ``talk`` (говорит, рот
  открыт), ``blink`` (моргнула) — PNG с прозрачным фоном (фон-хромакей ``KEY`` вырезается ffmpeg-ом), обрезаны по
  общей рамке. Слой гайда ``pngtuber`` (app/vtubika_guide.py) переключает их по словам озвучки.
* ``turntable`` — модель в полный рост, камера обходит её по кругу: ``f_001.png…`` (прозрачный фон) и ``turn.mp4``
  (на цвете ``--bg``) — пример «3D-модель поворачивается».

База — спека стоячей модели каста (``node/vrm/stills.mjs``: модель, образ, поза рук, свет); кадры — ``node/vrm/stills.mjs``.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKS = r"D:\vvf-offload\vtubika\vrm\vendor"
KEY = "#ff00ff"  # хромакей фона: у каста нет чистой мадженты (у зелёного — фартук Вари, у синего — Ася)


def _run(args: list[str], what: str) -> None:
    proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
                          stdin=subprocess.DEVNULL)
    if proc.returncode != 0:
        raise RuntimeError(f"{what}: {proc.stderr.strip()[-800:]}")


def _stills(spec: dict, out: Path, gl: str) -> None:
    with tempfile.TemporaryDirectory(prefix="vtb-guide-") as tmp:
        path = Path(tmp) / "spec.json"
        path.write_text(json.dumps(spec, ensure_ascii=False), "utf-8")
        _run(["node", str(ROOT / "node" / "vrm" / "stills.mjs"), "--spec", str(path), "--out", str(out), "--packs", PACKS,
              "--gl", gl, "--no-sheet"], "кадры VRM")


def _shot(base: dict, name: str, cam: dict, expr: dict, look: list[float]) -> dict:
    sh = copy.deepcopy(base["shots"][0])
    sh.update({"name": name, "cam": cam, "plate": {"kind": "flat", "color": KEY}})
    sh["pose"] = {**sh["pose"], "expr": expr, "look": look}
    return sh


def _key(src: Path, dst: Path, crop: str | None = None) -> None:
    """Маджента → прозрачность (мягкий край, без розовой каймы)."""
    vf = f"colorkey=0xff00ff:0.32:0.08,format=rgba{',' + crop if crop else ''}"
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf", vf, str(dst)], "хромакей")


def _bbox(files: list[Path], pad: int = 24, max_aspect: float | None = None) -> str:
    """Общая рамка непрозрачного по всем кадрам → фильтр crop."""
    from PIL import Image

    box = None
    for f in files:
        b = Image.open(f).getchannel("A").point(lambda v: 255 if v > 24 else 0).getbbox()
        if b:
            box = b if box is None else (min(box[0], b[0]), min(box[1], b[1]), max(box[2], b[2]), max(box[3], b[3]))
    if box is None:
        raise RuntimeError("кадры пустые: модель не отрисовалась")
    w, h = Image.open(files[0]).size
    x0, y0 = max(0, box[0] - pad), max(0, box[1] - pad)
    x1, y1 = min(w, box[2] + pad), min(h, box[3] + pad)
    if max_aspect:  # бюст: срез по груди — не выше max_aspect × ширина (как у картинок PNGtuber)
        y1 = min(y1, y0 + round((x1 - x0) * max_aspect))
    return f"crop={x1 - x0}:{y1 - y0}:{x0}:{y0}"


def pngtuber(base: dict, out: Path, gl: str, head: float) -> dict:
    cam = {"pos": {"bone": "head", "add": [0.08, -0.12, 2.6]}, "target": {"bone": "head", "add": [0, -0.2, 0]}, "fov": 22}
    look = [0.08, head, 1.6]
    spec = copy.deepcopy(base)
    spec["shots"] = [_shot(base, "idle", cam, {"relaxed": 0.35}, look),
                     _shot(base, "talk", cam, {"happy": 0.45, "aa": 0.8}, look),
                     _shot(base, "blink", cam, {"relaxed": 0.35, "blink": 1.0}, look)]
    raw = out / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    _stills(spec, raw, gl)
    keyed = []
    for n in ("idle", "talk", "blink"):
        k = raw / f"{n}_key.png"
        _key(raw / f"{n}.png", k)
        keyed.append(k)
    crop = _bbox(keyed, max_aspect=1.1)
    for n, k in zip(("idle", "talk", "blink"), keyed):
        _run(["ffmpeg", "-y", "-v", "error", "-i", str(k), "-vf", crop, str(out / f"{n}.png")], "обрезка")
    return {"files": [str(out / f"{n}.png") for n in ("idle", "talk", "blink")], "crop": crop}


def turntable(base: dict, out: Path, gl: str, head: float, frames: int, fps: int, bg: str) -> dict:
    full = base["shots"][0]["cam"]
    tgt = list(full["target"])
    r = math.hypot(full["pos"][0], full["pos"][2])
    y = float(full["pos"][1])
    spec = copy.deepcopy(base)
    spec["shots"] = []
    for i in range(frames):
        a = 2 * math.pi * i / frames  # полный круг: кадр 0 — анфас
        pos = [round(r * math.sin(a), 4), y, round(r * math.cos(a), 4)]
        spec["shots"].append(_shot(base, f"f_{i + 1:03d}", {**full, "pos": pos, "target": tgt}, {"relaxed": 0.4, "happy": 0.2},
                                   [pos[0] * 0.5, head, pos[2] * 0.5]))
    raw = out / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    _stills(spec, raw, gl)
    keyed = []
    for i in range(frames):
        k = raw / f"f_{i + 1:03d}_key.png"
        _key(raw / f"f_{i + 1:03d}.png", k)
        keyed.append(k)
    crop = _bbox(keyed)
    for i, k in enumerate(keyed):
        _run(["ffmpeg", "-y", "-v", "error", "-i", str(k), "-vf", crop, str(out / f"f_{i + 1:03d}.png")], "обрезка")
    _run(["ffmpeg", "-y", "-v", "error", "-framerate", str(fps), "-i", str(out / "f_%03d.png"), "-filter_complex",
          f"color=c={bg}:s=16x16[c];[c][0]scale2ref[c2][v];[c2][v]overlay=format=auto,format=yuv420p,scale=trunc(iw/2)*2:trunc(ih/2)*2",
          "-c:v", "libx264", "-crf", "16", "-r", str(fps), str(out / "turn.mp4")], "ролик поворота")
    return {"frames": frames, "fps": fps, "crop": crop, "video": str(out / "turn.mp4")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="примеры аватаров для мини-гайдов из каста VRM")
    ap.add_argument("kind", choices=["pngtuber", "turntable"])
    ap.add_argument("--base-spec", required=True, help="спека стоячей модели каста (node/vrm/stills.mjs)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--head", type=float, help="высота головы модели, м (взгляд в камеру); нет — по спеке")
    ap.add_argument("--frames", type=int, default=48)
    ap.add_argument("--fps", type=int, default=12)
    ap.add_argument("--bg", default="#20232b", help="цвет фона turn.mp4")
    ap.add_argument("--gl", default="gpu", choices=["gpu", "swiftshader"])
    args = ap.parse_args(argv)
    base = json.loads(Path(args.base_spec).read_text("utf-8"))
    base["stream"] = {**(base.get("stream") or {}), "chat": [], "noChat": True}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    head = args.head or float(base["shots"][0]["pose"].get("look", [0, 1.4, 0])[1])
    res = (pngtuber(base, out, args.gl, head) if args.kind == "pngtuber"
           else turntable(base, out, args.gl, head, args.frames, args.fps, args.bg))
    (out / "assets.json").write_text(json.dumps({"kind": args.kind, "base": args.base_spec, **res}, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

