"""Кадры проверки перекомпоновки 9:16 → 1:1 / 4:5 (app/aspect.py) через компоновщик.

Рисует набор слоёв в нужных форматах тем же движком, что и рендер (``--frame --png``):
фон холста (аврора), слои и камера после ``reframe_motion``. Без сервиса и базы — только файлы.

    python tools/aspect_frames.py OUT_DIR                       # синтетика: телефон, титр, кнопка
    python tools/aspect_frames.py OUT_DIR --layers clip.json --assets DIR --at 3 20 37

``--layers`` — файл клипа (vvf.py pull / GET /api/clip-plans/{id}/spec; берутся и ключи камеры) или список слоёв;
``--assets`` — папка с файлами слоёв по id (``139.mp4``, ``128.png``…). Нужен собранный
компоновщик (npm run build:motion) и ffmpeg. Рядом кладёт лист ``sheet.png`` (форматы в ряд).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SYNTHETIC_BACKDROP = {"kind": "aurora", "colors": ["#0b1018", "#ff6a2b", "#8b5cf6"], "speed": 0.45, "intensity": 0.55}


def synthetic_layers() -> list[dict]:
    """Промо-кадр: титр сверху, телефон по центру, кнопка-призыв снизу, касание по экрану."""
    from app.motion import normalize_layers

    return normalize_layers([
        {"type": "image", "name": "затемнение", "start": 0, "end": 10, "x": 0.5, "y": 0.12,
         "props": {"asset_id": 902, "w": 1080}},
        {"type": "text", "name": "титр", "start": 0, "end": 10, "x": 0.5, "y": 0.15, "track": 3,
         "props": {"text": "Задачи команды —\nв одном месте", "size": 76, "weight": 900, "maxWidth": 960}},
        {"type": "video", "name": "телефон", "start": 0, "end": 10, "x": 0.5, "y": 0.53, "track": 1,
         "props": {"asset_id": 901, "w": 520, "device": "phone"},
         "shadow": {"color": "#000000", "blur": 70, "x": 0, "y": 34, "opacity": 0.55}},
        {"type": "shape", "name": "касание", "start": 0, "end": 10, "x": 0.61, "y": 0.45, "track": 2,
         "props": {"kind": "ellipse", "w": 96, "h": 96, "fill": "#ffffff66", "stroke": {"color": "#ffffff", "width": 6}}},
        {"type": "text", "name": "кнопка", "start": 0, "end": 10, "x": 0.5, "y": 0.885, "track": 3,
         "props": {"text": "Попробовать бесплатно →", "size": 58, "weight": 900, "maxWidth": 980,
                   "bg": {"color": "#ff6a2b", "opacity": 1, "radius": 46, "padX": 50, "padY": 24}}},
    ])


def _green_base(path: Path, width: int, height: int, seconds: float) -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x00ff00:s={width}x{height}:r=2:d={seconds:g}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )


def render_frames(layers: list[dict], assets: dict[str, str], backdrop: dict, times: list[float],
                  out_dir: Path, formats: list[str], tag: str, camera: list[dict] | None = None) -> list[Path]:
    from app import render as _render
    from app.aspect import ASPECTS, asset_sizes, reframe_motion, reframe_plan
    from app.motion_render import build_compositor_args, prepare_video_frames, write_comp

    out_dir.mkdir(parents=True, exist_ok=True)
    sizes = asset_sizes(layers, assets)
    made: list[Path] = []
    duration = max([float(layer.get("end") or 0) for layer in layers] + [max(times) + 0.5])
    for aspect in formats:
        width, height = ASPECTS[aspect]
        framed, cam = reframe_motion(layers, camera or [], width, height, sizes)
        if aspect != "9:16":
            plan = reframe_plan(layers, width, height, sizes, camera=camera)
            parts = ", ".join(f"{p['start']:g}–{p['end']:g} с: рамка {p['box']}" for p in plan["scenes"])
            print(f"{tag} {aspect}: масштаб {plan['scale']}; сцены: {parts}")
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            base = work / "base.mp4"
            _green_base(base, width, height, duration + 1)
            (work / "assets.json").write_text(json.dumps(assets, ensure_ascii=False), encoding="utf-8")
            comp = write_comp(
                work / "comp.json", width=width, height=height, fps=30, duration=duration, layers=framed, camera=cam,
                key={"color": "#00ff00", "similarity": 0.25, "blend": 0.08, "despill": "green"}, background=backdrop,
            )
            for t in times:
                videos, _notes = prepare_video_frames(framed, assets, work / f"v{t:g}", fps=30, out_width=width, at=t)
                png = out_dir / f"{tag}-{aspect.replace(':', 'x')}-t{t:g}.png"
                _render._run_compositor(
                    build_compositor_args(comp, work / "assets.json", input_path=base, frame=t, png_path=png,
                                          videos_path=videos),
                    timeout=180,
                )
                made.append(png)
    return made


def contact_sheet(paths: list[Path], out: Path, height: int = 540) -> None:
    """Кадры в ряд одной высоты (разные форматы рядом) — глазами сравнить."""
    args = ["ffmpeg", "-v", "error", "-y"]
    for path in paths:
        args += ["-i", str(path)]
    graph = ";".join(f"[{i}:v]scale=-2:{height},pad=iw+16:ih:0:0:color=0x202020[s{i}]" for i in range(len(paths)))
    graph += ";" + "".join(f"[s{i}]" for i in range(len(paths))) + f"hstack=inputs={len(paths)}[out]"
    subprocess.run([*args, "-filter_complex", graph, "-map", "[out]", "-frames:v", "1", str(out)], check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path)
    parser.add_argument("--layers", type=Path, help="файл клипа или JSON-список слоёв")
    parser.add_argument("--assets", type=Path, help="папка с файлами слоёв по id")
    parser.add_argument("--at", type=float, nargs="+", default=[1.0])
    parser.add_argument("--formats", nargs="+", default=["9:16", "4:5", "1:1"])
    args = parser.parse_args()

    from app.motion import normalize_camera, normalize_layers

    camera: list[dict] = []
    if args.layers:
        data = json.loads(args.layers.read_text(encoding="utf-8"))
        render = data.get("render") if isinstance(data, dict) else None
        raw = (render or {}).get("layers") if render else data
        layers = [layer for layer in normalize_layers(raw) if not layer["hidden"] and layer["type"] != "audio"]
        backdrop = (render or {}).get("backdrop") or SYNTHETIC_BACKDROP
        camera = normalize_camera((render or {}).get("camera") or [])
        assets = {}
        for path in sorted((args.assets or Path(".")).glob("*")):
            assets[path.stem] = str(path.resolve())
        tag = args.layers.stem
    else:
        work = args.out / "synthetic-assets"
        work.mkdir(parents=True, exist_ok=True)
        phone = work / "phone.mp4"
        band = work / "band.png"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=585x1266:r=15:d=12",
                        "-vf", "scale=1170:2532", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                        str(phone)], check=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black@0.0:s=1080x760,format=rgba",
                        "-vf", "geq=r=0:g=0:b=0:a='200*(1-Y/H)'", "-frames:v", "1", str(band)], check=True)
        layers = synthetic_layers()
        assets = {"901": str(phone.resolve()), "902": str(band.resolve())}
        backdrop = SYNTHETIC_BACKDROP
        tag = "synthetic"
    made = render_frames(layers, assets, backdrop, args.at, args.out, args.formats, tag, camera)
    for t in args.at:
        row = [p for p in made if p.name.endswith(f"-t{t:g}.png")]
        sheet = args.out / f"{tag}-sheet-t{t:g}.png"
        contact_sheet(row, sheet)
        print(sheet)


if __name__ == "__main__":
    main()
