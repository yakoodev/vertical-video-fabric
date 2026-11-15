"""Логотип, который пишется кистью: отдельный ролик с альфой или вставка поверх видео.

Слой ``logo_reveal`` движка (frontend/src/motion/layers/logoreveal.ts) рисует компоновщик
(node/motion-render.mjs — тот же код, что превью редактора); мазки извлекает
app/logo_vectorize.py. Звук — настоящие записи пака freesound (CC0): короткий взмах кисти на
начало письма и удар на ``hit``; громкость — правило сервиса (motion_render.effect_gain_db:
эффекты на 10 дБ тише громких мест звука ролика).

Примеры:

    # Ролик с альфой (ProRes 4444 .mov; .webm — VP9 с альфой)
    python tools/logo_reveal.py --logo logo.png --out D:/x/logo_alpha.mov --size 1080x1080

    # Поверх первых 3 с фильма (интро) и последних 3 с (аутро)
    python tools/logo_reveal.py --logo logo.png --over movie.mp4 --at start --out D:/x/intro.mp4
    python tools/logo_reveal.py --logo logo.png --over movie.mp4 --at end --out D:/x/outro.mp4

Параметры слоя — ``--props '{"glow": "#ff6a2a", "exit": {"kind": "slash"}}'`` (поверх пресета ``--preset``).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.logo_vectorize import cached_vectorize  # noqa: E402

COMPOSITOR = ROOT / "node" / "motion-render.mjs"
SFX_DIRS = [ROOT / "vendor" / "packs" / "freesound", ROOT.parents[2] / "vendor" / "packs" / "freesound"
            if ROOT.parent.name == "worktrees" else ROOT / "vendor" / "packs" / "freesound"]

# Пресеты интро/аутро: параметры слоя, когда кисть пишет и когда удар (секунды слоя).
PRESETS: dict[str, dict] = {
    # A — как у владельца: рог, потом буквы кистью, брызги, мягкий удар, уход стиранием кистью.
    "brush": {"reveal": "brush", "draw": 1.35, "delay": 0.1, "splatter": 0.55, "brush": 0.65, "glowSize": 30,
              "glowStrength": 1.0, "hit": {"scale": 1.06, "flash": 0.7, "burst": 0.8}, "exit": {"kind": "wipe", "dur": 0.4}},
    # B — агрессивно: быстрое письмо, удар с тряской и вспышкой, уход росчерком.
    "slash": {"reveal": "brush", "draw": 0.75, "delay": 0.05, "splatter": 0.9, "brush": 0.8, "glowSize": 34,
              "glowStrength": 1.3, "hit": {"scale": 1.12, "flash": 1.0, "burst": 1.0, "shake": 26},
              "exit": {"kind": "slash", "dur": 0.42, "angle": -14}},
    # C — пятно чернил/дым собирается в логотип, удар, уход растворением.
    "ink": {"reveal": "ink", "draw": 1.0, "delay": 0.05, "splatter": 0.7, "glowSize": 40, "glowStrength": 1.2,
            "hit": {"scale": 1.07, "flash": 0.9, "burst": 1.0}, "exit": {"kind": "fade", "dur": 0.35}},
    # boil — как у владельца (одобрено 16.11): эскиз контуром на двойках, рывок царапинами, живой логотип, уход царапинами.
    "boil": {"reveal": "brush", "draw": 2.0, "delay": 0.0, "boil": 3, "sketch": True, "burst": 0.3, "loop": 6, "splatter": 0.3,
             "glow": "#ffffff", "glowSize": 26, "glowStrength": 0.6, "hit": {"scale": 1.04, "flash": 0.9, "burst": 0.5},
             "exit": {"kind": "scratch", "dur": 0.3}},
    # D — полоса света по уже проступающим буквам.
    "sweep": {"reveal": "sweep", "draw": 0.9, "delay": 0.1, "splatter": 0.4, "glowSize": 30, "glowStrength": 1.1,
              "hit": {"scale": 1.05, "flash": 0.8, "burst": 0.6}, "exit": {"kind": "fade", "dur": 0.3}},
}


def run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise SystemExit(f"команда упала ({proc.returncode}): {' '.join(cmd[:4])}…\n{proc.stderr[-2000:]}")


def probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height,r_frame_rate:format=duration",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    data = json.loads(out)
    v = next(s for s in data["streams"] if s["codec_type"] == "video")
    num, den = (int(x) for x in v["r_frame_rate"].split("/"))
    return {"w": int(v["width"]), "h": int(v["height"]), "fps": num / max(1, den), "dur": float(data["format"]["duration"]),
            "audio": any(s["codec_type"] == "audio" for s in data["streams"])}


def sfx(name: str) -> Path:
    for d in SFX_DIRS:
        p = d / f"{name}.ogg"
        if p.exists():
            return p
    raise SystemExit(f"нет звука {name}.ogg в паке freesound (tools/fetch_packs.py)")


def build_layer(logo_path: Path, vector: dict, props: dict, start: float, end: float, x: float, y: float) -> dict:
    p = {"asset_id": "logo", "logo": vector, "w": 900, "glow": "#ff6a2a", **props}
    return {"id": "logo", "type": "logo_reveal", "start": start, "end": end, "x": x, "y": y, "props": p}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--logo", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--over", type=Path, help="видео, поверх которого класть (иначе — ролик с альфой)")
    ap.add_argument("--at", choices=("start", "end"), default="start", help="кусок видео: начало (интро) или конец (аутро)")
    ap.add_argument("--span", type=float, default=3.0, help="длина куска видео, с")
    ap.add_argument("--dur", type=float, default=1.9, help="длина слоя логотипа, с")
    ap.add_argument("--offset", type=float, default=0.2, help="когда слой начинается в куске, с")
    ap.add_argument("--size", default="1080x1080", help="кадр ролика с альфой, WxH")
    ap.add_argument("--preset", choices=sorted(PRESETS), default="brush")
    ap.add_argument("--props", default="{}", help="JSON поверх пресета")
    ap.add_argument("--first", default=None, help="JSON рамок [[x0,y0,x1,y1]] (доли ширины): эти мазки — первыми")
    ap.add_argument("--x", type=float, default=0.5)
    ap.add_argument("--y", type=float, default=0.5)
    ap.add_argument("--w", type=float, default=None, help="ширина логотипа, опорные px")
    ap.add_argument("--dim", type=float, default=0.35, help="затемнение кадра под логотипом 0..1 (поверх видео)")
    ap.add_argument("--fps", type=float, default=None)
    ap.add_argument("--swoosh", default="whoosh-1")
    ap.add_argument("--hit-sfx", default="impact-low")
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--keep", type=Path, default=None, help="папка для comp.json и промежуточных файлов")
    args = ap.parse_args(argv)

    props = json.loads(json.dumps(PRESETS[args.preset]))
    extra = json.loads(args.props)
    for key, val in extra.items():
        if isinstance(val, dict) and isinstance(props.get(key), dict):
            props[key] = {**props[key], **val}
        else:
            props[key] = val
    if args.w:
        props["w"] = args.w
    first = json.loads(args.first) if args.first else None
    work = Path(tempfile.mkdtemp(prefix="logo-", dir=str(args.keep) if args.keep else None))
    vector = cached_vectorize(args.logo, work.parent if args.keep else work, first=first)

    base = None
    if args.over:
        info = probe(args.over)
        t0 = 0.0 if args.at == "start" else max(0.0, info["dur"] - args.span)
        base = work / "base.mp4"
        run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t0:.3f}", "-i", str(args.over), "-t", f"{args.span:.3f}",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "14", "-c:a", "aac", "-b:a", "256k", str(base)])
        width, height, fps, duration = info["w"], info["h"], args.fps or info["fps"], args.span
    else:
        width, height = (int(v) for v in args.size.lower().split("x"))
        fps, duration = args.fps or 30.0, args.dur + args.offset
    portrait = height >= width
    ref = (1080, 1920) if portrait else (1920, 1080)
    start = args.offset
    end = min(duration, start + args.dur)
    layers = []
    if base and args.dim > 0:
        layers.append({"id": "dim", "type": "shape", "start": start, "end": end, "x": 0.5, "y": 0.5,
                       "opacity": args.dim, "in": {"type": "fade", "duration": 0.25}, "out": {"type": "fade", "duration": 0.35},
                       "props": {"kind": "rect", "w": ref[0] * 1.1, "h": ref[1] * 1.1, "fill": "#000000"}})
    layer = build_layer(args.logo, vector, props, start, end, args.x, args.y)
    if not args.w:
        layer["props"]["w"] = round(ref[0] * (0.86 if portrait else 0.52))
    layers.append(layer)
    comp = {"width": width, "height": height, "fps": fps, "duration": duration, "layers": layers,
            "refWidth": ref[0], "refHeight": ref[1]}
    (work / "comp.json").write_text(json.dumps(comp), "utf-8")
    (work / "assets.json").write_text(json.dumps({"logo": str(args.logo.resolve())}), "utf-8")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    video_out = args.out if (not base or args.no_audio) and args.out.suffix.lower() != ".webm" else work / ("motion.mov" if not base else "motion.mp4")
    cmd = ["node", str(COMPOSITOR), "--comp", str(work / "comp.json"), "--assets", str(work / "assets.json"),
           "--output", str(video_out), "--ffmpeg", "ffmpeg", "--crf", "16"]
    if base:
        cmd += ["--input", str(base)]
    run(cmd)
    if not base and args.out.suffix.lower() == ".webm":
        run(["ffmpeg", "-v", "error", "-y", "-i", str(video_out), "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0",
             "-crf", "28", "-auto-alt-ref", "0", str(args.out)])
    if base and not args.no_audio:
        mix_audio(base, video_out, args.out, props, start, args.swoosh, args.hit_sfx)
    print(json.dumps({"out": str(args.out), "work": str(work), "strokes": len(vector["strokes"]),
                      "coverage": vector.get("coverage")}, ensure_ascii=False))
    return 0


def mix_audio(base: Path, video: Path, out: Path, props: dict, start: float, swoosh: str, hit: str) -> None:
    """Звук куска + взмах кисти на начало письма + удар на hit. Громкость эффектов — правило сервиса:
    на 10 дБ тише громких мест звука ролика (здесь это музыка фильма)."""
    from app.motion_render import audio_levels, effect_gain_db

    lv = audio_levels(base, seconds=None)
    loud = lv[0] if lv and lv[0] > -70 else None
    draw_at = start + float(props.get("delay", 0))
    hit_cfg = props.get("hit") or {}
    hit_at = start + float(hit_cfg.get("at", float(props.get("delay", 0)) + float(props.get("draw", 1.1)))) if props.get("hit") is not None else None
    parts = [(sfx(swoosh), draw_at)]
    if hit_at is not None and hit:
        parts.append((sfx(hit), max(0.0, hit_at - 0.02)))
    inputs = ["-i", str(video), "-i", str(base)]
    chains = []
    labels = ["[1:a]"]
    for i, (path, at) in enumerate(parts):
        gain = effect_gain_db(str(path), loud, speechless=False)
        inputs += ["-i", str(path)]
        ms = int(round(at * 1000))
        chains.append(f"[{i + 2}:a]aresample=48000,volume={gain:.2f}dB,adelay={ms}|{ms}[s{i}]")
        labels.append(f"[s{i}]")
    chains.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:duration=first[a]")
    run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", ";".join(chains), "-map", "0:v", "-map", "[a]",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", str(out)])


if __name__ == "__main__":
    sys.exit(main())
