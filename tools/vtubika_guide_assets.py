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
* ``faces`` — бюст анфас, мимика по кадрам (``app.vtubika_facetrack``): ``--mode stiff`` — «вебка» (рот еле
  открывается, брови стоят), ``--mode lively`` — «айфон» (рот, голова, брови/щёки/прищур/язык на ``--events``).
  Кадры ``f_001.png…`` с прозрачным фоном — слой гайда ``seq``. Модель — ``--hero`` из каста (по умолчанию Лёша:
  52 формы ARKit) поверх позы и света ``--base-spec``::

      python tools/vtubika_guide_assets.py faces --base-spec D:/vvf-offload/vtubika/cast/work/specs/basem_stand.json
          --mode lively --dur 12 --fps 15 --events '[{"t": 7.1, "what": "brows"}]' --out D:/…/assets/face_lively

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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
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


def faces(base: dict, out: Path, gl: str, mode: str, dur: float, fps: float, events: list[dict], seed: int,
          hero: str | None, fov: float) -> dict:
    from app import vtubika_facetrack as F

    spec = copy.deepcopy(base)
    if hero:
        from app.vtubika_vrm_stage import HEROINES

        her = HEROINES[hero]
        spec["model"], spec["look"] = her["model"], her["look"]
    spec["width"], spec["height"] = 810, 1440
    cam = {"pos": {"bone": "head", "add": [0.05, 0.05, 2.6]}, "target": {"bone": "head", "add": [0, 0.05, 0]}, "fov": fov}
    pose0 = base["shots"][0]["pose"]
    hx, _hy, hz = (pose0.get("bones") or {}).get("head", [0, 0, 0])
    head_y = float(pose0.get("look", [0, 1.4, 0])[1])
    frames = F.track(mode, dur, fps, events, seed)
    spec["shots"] = []
    for i, fr in enumerate(frames):
        look = [0.05 + fr["look"][0], head_y + fr["look"][1], 1.6]
        sh = _shot(base, f"f_{i + 1:03d}", cam, fr["expr"], look)
        h = fr["head"]
        sh["pose"]["bones"] = {**(sh["pose"].get("bones") or {}), "head": [hx + h[0], h[1], hz + h[2]]}
        spec["shots"].append(sh)
    raw = out / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    for old in out.glob("f_*.png"):
        old.unlink()
    _stills(spec, raw, gl)
    n = len(frames)
    # Хромакей и обрезка — одним вызовом ffmpeg на весь ряд (кадров сотни).
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(raw / "f_%03d.png"), "-vf", "colorkey=0xff00ff:0.32:0.08,format=rgba",
          str(raw / "k_%03d.png")], "хромакей")
    keyed = [raw / f"k_{i + 1:03d}.png" for i in range(n)]
    crop = _bbox(keyed, max_aspect=1.12)
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(raw / "k_%03d.png"), "-vf", crop, str(out / "f_%03d.png")], "обрезка")
    (out / "track.json").write_text(json.dumps({"mode": mode, "fps": fps, "events": events, "frames": frames},
                                               ensure_ascii=False), "utf-8")
    return {"mode": mode, "frames": n, "fps": fps, "dur": dur, "events": events, "crop": crop,
            "pattern": str(out / "f_{:03d}.png")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="примеры аватаров для мини-гайдов из каста VRM")
    ap.add_argument("kind", choices=["pngtuber", "turntable", "faces"])
    ap.add_argument("--base-spec", required=True, help="спека стоячей модели каста (node/vrm/stills.mjs)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--head", type=float, help="высота головы модели, м (взгляд в камеру); нет — по спеке")
    ap.add_argument("--frames", type=int, default=48)
    ap.add_argument("--fps", type=int, default=12)
    ap.add_argument("--bg", default="#20232b", help="цвет фона turn.mp4")
    ap.add_argument("--gl", default="gpu", choices=["gpu", "swiftshader"])
    ap.add_argument("--mode", default="lively", choices=["stiff", "lively"], help="faces: «вебка» или «айфон»")
    ap.add_argument("--dur", type=float, default=6.0, help="faces: длина ряда кадров, с")
    ap.add_argument("--events", default="[]", help="faces: JSON [{t, what}] — мимика на секундах ряда (или @файл)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--hero", default="lyosha", help="faces: герой каста (app/vtubika_vrm_stage.HEROINES)")
    ap.add_argument("--fov", type=float, default=12.0)
    args = ap.parse_args(argv)
    base = json.loads(Path(args.base_spec).read_text("utf-8"))
    base["stream"] = {**(base.get("stream") or {}), "chat": [], "noChat": True}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    head = args.head or float(base["shots"][0]["pose"].get("look", [0, 1.4, 0])[1])
    if args.kind == "faces":
        ev = Path(args.events[1:]).read_text("utf-8") if args.events.startswith("@") else args.events
        res = faces(base, out, args.gl, args.mode, args.dur, float(args.fps), json.loads(ev), args.seed, args.hero or None, args.fov)
    elif args.kind == "pngtuber":
        res = pngtuber(base, out, args.gl, head)
    else:
        res = turntable(base, out, args.gl, head, args.frames, args.fps, args.bg)
    (out / "assets.json").write_text(json.dumps({"kind": args.kind, "base": args.base_spec, **res}, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

