"""Пробный кадр 3D-серии в Blender: лифт, герой KayKit в жёлтом дождевике, блэкаут (10 с, 1080×1920, 30 к/с).

    python tools/hero_shot.py [--out D:/vvf-offload/tmp/scene3d/hero_shot.mp4] [--quality final|preview]

Шаги: Blender (``tools/blender/hero_shot.py``, Eevee Next) рендерит кадры PNG по таймлайну
``app/motion3d.py`` → ffmpeg собирает mp4 (лёгкое зерно против бандинга в темноте) и подкладывает
звук из паков (гул кабины, щелчок кнопки, рубильник, гул шахты, тревожный фон, щелчок фонарика) →
лист кадров (8 кадров с таймкодами, включая крупные планы лица).

Нужны: Blender 4.5 LTS (``BLENDER_BIN`` или ``D:/vvf-offload/tools/blender/...``), паки ``kaykit-adventurers``,
``kaykit-animations``, ``tension`` (``tools/fetch_packs.py``); ``kenney-interface`` — по возможности (щелчки).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCENE = ROOT / "tools" / "blender" / "hero_shot.py"
DEFAULT_BLENDER = [
    os.environ.get("BLENDER_BIN", ""),
    "D:/vvf-offload/tools/blender/blender-4.5.15-windows-x64/blender.exe",
    "/usr/bin/blender",
]
FONTS = {
    "font_display": ["C:/Windows/Fonts/bahnschrift.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf"],
    "font_ui": ["C:/Windows/Fonts/bahnschrift.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"],
    "font_symbols": ["C:/Windows/Fonts/seguisym.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"],
}


def find_blender(explicit: str | None) -> str:
    for c in [explicit or "", *DEFAULT_BLENDER]:
        if c and Path(c).exists():
            return c
    hit = shutil.which("blender")
    if hit:
        return hit
    raise SystemExit("нет Blender: задай --blender или BLENDER_BIN (portable 4.5 LTS с blender.org)")


def find_asset(packs: list[Path], rel: str, required: bool = True) -> Path | None:
    for p in packs:
        f = p / rel
        if f.exists():
            return f
    if required:
        raise SystemExit(f"нет {rel} в {', '.join(map(str, packs))} — python tools/fetch_packs.py --only "
                         f"{rel.split('/')[0]}")
    return None


def sound_plan(report: dict, packs: list[Path], dur: float) -> list[dict]:
    """Звуки по событиям таймлайна: что, когда, громкость (дБ), затухания."""
    L = report["light"]
    snd = []

    def add(rel, t, db, fade_in=0.0, fade_out=0.0, until=None, loop=False):
        f = find_asset(packs, rel, required=False)
        if f:
            snd.append({"file": str(f), "t": max(0.0, t), "db": db, "fade_in": fade_in, "fade_out": fade_out,
                        "until": until, "loop": loop})

    add("tension/cabin-hum.ogg", 0.0, -9.0, fade_in=0.4, fade_out=0.25, until=L["t_off"] + 0.05, loop=True)
    add("tension/bed-ominous-deep.ogg", L["t_flicker"] - 0.4, -10.0, fade_in=2.5, fade_out=0.6, until=dur, loop=True)
    add("kenney-interface/click_002.ogg", L["t_press"] - 0.02, -6.0)
    add("tension/power-down.ogg", L["t_off"] - 0.35, -5.0)
    add("tension/breaker.ogg", L["t_off"] - 0.02, -2.0)
    add("tension/shaft-rumble.ogg", L["t_off"] + 0.1, -10.0, fade_in=1.2, fade_out=0.6, until=dur, loop=True)
    add("kenney-interface/click_004.ogg", L["t_red"] - 0.02, -14.0)
    add("tension/clank.ogg", L["t_red"] + 0.9, -16.0)
    add("kenney-interface/click_001.ogg", L["t_flash"] - 0.02, -8.0)
    return snd


def mix_audio(ffmpeg: str, plan: list[dict], dur: float, out: Path) -> Path | None:
    if not plan:
        return None
    args = [ffmpeg, "-y", "-loglevel", "error"]
    chains = []
    for i, s in enumerate(plan):
        if s["loop"]:
            args += ["-stream_loop", "-1"]
        args += ["-i", s["file"]]
        until = s["until"] if s["until"] is not None else dur
        length = max(0.05, until - s["t"])
        f = [f"[{i}:a]aformat=sample_rates=48000:channel_layouts=stereo", f"atrim=0:{length:.3f}", "asetpts=N/SR/TB",
             f"volume={s['db']}dB"]
        if s["fade_in"]:
            f.append(f"afade=t=in:d={s['fade_in']}")
        if s["fade_out"]:
            f.append(f"afade=t=out:st={max(0.0, length - s['fade_out']):.3f}:d={s['fade_out']}")
        ms = int(s["t"] * 1000)
        f.append(f"adelay={ms}|{ms}")
        chains.append(",".join(f) + f"[a{i}]")
    graph = ";".join(chains) + ";" + "".join(f"[a{i}]" for i in range(len(plan))) + \
        f"amix=inputs={len(plan)}:normalize=0:duration=longest,atrim=0:{dur},volume=9dB,alimiter=limit=0.7:level=0[out]"
    args += ["-filter_complex", graph, "-map", "[out]", "-c:a", "pcm_s16le", str(out)]
    subprocess.run(args, check=True)
    return out


def encode(ffmpeg: str, frames: Path, audio: Path | None, out: Path) -> None:
    args = [ffmpeg, "-y", "-loglevel", "error", "-framerate", "30", "-i", str(frames / "f_%04d.png")]
    if audio:
        args += ["-i", str(audio)]
    # лёгкое зерно — против полос на тёмных градиентах (красный свет, туман) после сжатия
    args += ["-vf", "noise=alls=3:allf=t,format=yuv420p", "-c:v", "libx264", "-preset", "slow", "-crf", "16",
             "-profile:v", "high", "-movflags", "+faststart"]
    if audio:
        args += ["-c:a", "aac", "-b:a", "192k", "-shortest"]
    subprocess.run(args + [str(out)], check=True)


def sheet(ffmpeg: str, frames: Path, report: dict, out: Path) -> list[int]:
    """8 кадров 2×4 с таймкодом и названием плана: проход, нажатие, мигание, испуг крупно, взгляд вверх, телефон, луч."""
    L = report["light"]
    picks = [0.9, L["t_press"] + 0.05, L["t_off"] - 0.35, L["t_off"] + 0.55, L["t_red"] + 0.6, L["t_red"] + 1.5,
             L["t_flash"] + 0.6, report["duration"] - 0.25]
    nums = [max(1, min(int(round(t * 30)) + 1, report["frames"])) for t in picks]
    args = [ffmpeg, "-y", "-loglevel", "error"]
    for n in nums:
        args += ["-i", str(frames / f"f_{n:04d}.png")]
    parts = []
    for i, n in enumerate(nums):
        tc = f"{(n - 1) / 30:05.2f}s"
        parts.append(f"[{i}]scale=405:720,drawtext=text='{tc}':x=10:y=10:fontsize=24:fontcolor=white:"
                     f"box=1:boxcolor=black@0.55:boxborderw=6[v{i}]")
    graph = ";".join(parts) + ";" + "".join(f"[v{i}]" for i in range(4)) + "hstack=4[r0];" + \
        "".join(f"[v{i}]" for i in range(4, 8)) + "hstack=4[r1];[r0][r1]vstack[out]"
    subprocess.run(args + ["-filter_complex", graph, "-map", "[out]", "-q:v", "2", str(out)], check=True)
    return nums


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default="D:/vvf-offload/tmp/scene3d/hero_shot.mp4")
    ap.add_argument("--sheet", default=None, help="лист кадров (по умолчанию hero_sheet.jpg рядом с --out)")
    ap.add_argument("--work", default=None, help="папка кадров (по умолчанию <out>_frames)")
    ap.add_argument("--packs", action="append", default=[], help="папки паков (можно несколько)")
    ap.add_argument("--blender", default=None)
    ap.add_argument("--quality", choices=["final", "preview"], default="final")
    ap.add_argument("--samples", type=int, default=64)
    ap.add_argument("--duration", type=float, default=10.0)
    ap.add_argument("--reuse-frames", action="store_true", help="не рендерить заново, собрать из готовых кадров")
    ap.add_argument("--frames", default=None, help="перерендерить только диапазон A-B (кадры 1…) в готовую папку")
    ap.add_argument("--export-gltf", default=None, help="заодно выгрузить сцену (glb) — для сравнения с three.js")
    args = ap.parse_args(argv)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    work = Path(args.work or out.with_name(out.stem + "_frames"))
    packs = [Path(p) for p in args.packs] or [ROOT / "vendor" / "packs"]
    env_packs = os.environ.get("MOTION_PACKS_DIR")
    if env_packs:
        packs.append(Path(env_packs))
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    cfg = {
        "out_dir": str(work), "quality": args.quality, "samples": args.samples, "duration": args.duration,
        "character": str(find_asset(packs, "kaykit-adventurers/Rogue_Hooded.glb")),
        "animations": [str(find_asset(packs, "kaykit-animations/Rig_Medium_General.glb")),
                       str(find_asset(packs, "kaykit-animations/Rig_Medium_MovementBasic.glb"))],
        **FONTS,
    }
    if args.export_gltf:
        cfg["export_gltf"] = args.export_gltf
    t0 = time.time()
    if args.frames:
        a, b = (int(x) for x in args.frames.split("-"))
        cfg["frame_range"] = [a, b]
    if not args.reuse_frames:
        if work.exists() and not args.frames:
            shutil.rmtree(work)
        work.mkdir(parents=True, exist_ok=True)
        cfg_path = work / "config.json"
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), "utf-8")
        blender = find_blender(args.blender)
        proc = subprocess.run([blender, "-b", "--factory-startup", "-P", str(SCENE), "--", "--config", str(cfg_path)],
                              capture_output=True, text=True, errors="replace")
        log = (proc.stdout or "") + (proc.stderr or "")
        (work / "blender.log").write_text(log, "utf-8")
        if proc.returncode != 0 or not (work / "report.json").exists():
            print(log[-4000:], file=sys.stderr)
            raise SystemExit("Blender упал — см. blender.log")
    report = json.loads((work / "report.json").read_text("utf-8"))
    report.setdefault("duration", args.duration)
    report.setdefault("frames", int(round(args.duration * 30)))
    audio = mix_audio(ffmpeg, sound_plan(report, packs, args.duration), args.duration, work / "mix.wav")
    encode(ffmpeg, work, audio, out)
    sheet_path = Path(args.sheet) if args.sheet else out.with_name("hero_sheet.jpg")
    nums = sheet(ffmpeg, work, report, sheet_path)
    result = {"ok": True, "out": str(out), "sheet": str(sheet_path), "sheet_frames": nums,
              "render_sec": report.get("render_sec"), "per_frame_sec": round((report.get("render_sec") or 0) / report["frames"], 2),
              "press_error_mm": report.get("press_error_mm"), "total_sec": round(time.time() - t0, 1),
              "size_mb": round(out.stat().st_size / 1e6, 1)}
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
