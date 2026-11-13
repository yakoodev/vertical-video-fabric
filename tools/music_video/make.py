# -*- coding: utf-8 -*-
"""Музыкальный клип одной командой (пресет song15 «paper» / song2 «comic»). Инструкция: docs/music-video.md.

    python tools/music_video/make.py --song SONG.mp3 --full --style comic --out D:/vvf-offload/songX
    python tools/music_video/make.py --song SONG.mp3 --start 15 --end 36 --style paper --out DIR --stage analyze,plan
    python tools/music_video/make.py --out DIR --stage images --max-rub 60          # платный шаг — только с --max-rub
    python tools/music_video/make.py --out DIR --stage prep,stills --stills 30,300,900
    python tools/music_video/make.py --out DIR --stage prep,build,encode,check

Шаги: analyze → plan → images → prep → build → encode → check (all = все, кроме images без --max-rub).
  analyze  вокальный стем (demucs) → атаки слогов, сетка долей, слова Whisper с привязкой к атакам → analysis.json
  plan     черновик plan.json (если его ещё нет) — дальше правится руками
  images   картинки nano-banana через контейнер сервиса, расход в work/spend.log
  prep     план → work/scene.json + текстуры (фоны, вырезки, буквы, плашки, вспышки)
  stills   отдельные кадры из Blender (--stills), лист work/stills_sheet.jpg
  build    рендер всех кадров в Blender (Eevee), work/frames
  encode   OUT/<имя>.mp4 (+ _tg.mp4 при > 48 МБ)
  check    OUT/sheet.jpg (24 кадра), застывшие отрезки, OUT/sync_check.mp4

Blender — внешняя зависимость: --blender ПУТЬ или переменная MV_BLENDER. demucs — --demucs или MV_DEMUCS."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

STAGES = ["analyze", "plan", "images", "prep", "stills", "build", "encode", "check"]


def free_gb() -> float:
    try:
        if os.name == "nt":
            out = subprocess.run(["powershell", "-NoProfile", "-c", "(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory"],
                                 capture_output=True, text=True, timeout=30).stdout.strip()
            return int(out) / 1024 / 1024
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemAvailable"):
                    return int(line.split()[1]) / 1024 / 1024
    except Exception:
        pass
    return 99.0


def wait_ram(need_gb: float, max_wait: int = 1800) -> None:
    t = time.time()
    while (g := free_gb()) < need_gb:
        if time.time() - t > max_wait:
            raise SystemExit(f"  свободно {g:.1f} ГБ < {need_gb} ГБ уже {max_wait // 60} мин — рендер не запускаю")
        print(f"  свободно {g:.1f} ГБ, жду {need_gb} ГБ…", flush=True)
        time.sleep(30)


def find_blender(arg: str | None) -> str:
    for c in [arg, os.environ.get("MV_BLENDER"), shutil.which("blender"),
              *sorted(Path("D:/vvf-offload/tools/blender").glob("blender-*/blender.exe"), reverse=True)]:
        if c and Path(c).exists():
            return str(c)
    raise SystemExit("  Blender не найден: --blender ПУТЬ или MV_BLENDER")


def blender(args, out: Path, extra: list[str]) -> None:
    exe = find_blender(args.blender)
    wait_ram(args.min_ram)
    cmd = [exe, "-b", "--factory-startup", "--python-exit-code", "1", "-P", str(HERE / "blender_build.py"), "--",
           str(out / "work" / "scene.json"), *extra]
    log = out / "work" / "render.log"
    print("  Blender:", " ".join(extra), "→ лог", log, flush=True)
    with open(log, "w", encoding="utf-8", errors="replace") as fh:
        p = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT)
    if p.returncode:
        raise SystemExit(f"  Blender упал ({p.returncode}), см. {log}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--song")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float)
    ap.add_argument("--full", action="store_true", help="вся песня (игнорирует --start/--end)")
    ap.add_argument("--style", default="comic", choices=["comic", "paper"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--plan", help="план (по умолчанию OUT/plan.json)")
    ap.add_argument("--stage", default="all")
    ap.add_argument("--lang", default="ru")
    ap.add_argument("--lyrics", help="примерный текст песни — подсказка Whisper")
    ap.add_argument("--rewhisper", help="окна для повторного распознавания: 12-16,16-20.5")
    ap.add_argument("--max-rub", type=float, help="потолок трат на картинки (без него шаг images не платит)")
    ap.add_argument("--container", default="vertical-video-fabric-app-1")
    ap.add_argument("--force", help="перегенерировать эти картинки: id,id (платно)")
    ap.add_argument("--only", help="генерировать только эти картинки: id,id")
    ap.add_argument("--blender")
    ap.add_argument("--demucs", default=os.environ.get("MV_DEMUCS", "demucs"),
                    help="команда demucs; аргументы через |, напр. python|-m|demucs")
    ap.add_argument("--min-ram", type=float, default=6.0, help="ГБ свободной памяти перед рендером")
    ap.add_argument("--stills", default="")
    ap.add_argument("--frames", help="диапазон кадров A-B для build")
    ap.add_argument("--draft", action="store_true", help="половинное разрешение, 8 сэмплов")
    ap.add_argument("--name", help="имя итогового файла (по умолчанию имя папки OUT)")
    args = ap.parse_args()

    out = Path(args.out); (out / "work").mkdir(parents=True, exist_ok=True)
    stages = STAGES if args.stage == "all" else args.stage.split(",")
    if args.stage == "all":
        stages = [s for s in stages if s != "stills" and (s != "images" or args.max_rub)]
    name = args.name or out.name
    plan_path = Path(args.plan) if args.plan else out / "plan.json"

    if "analyze" in stages:
        import analyze
        if not args.song:
            raise SystemExit("  нужен --song")
        analyze.main(args.song, out, 0.0 if args.full else args.start, None if args.full else args.end, args.lang,
                     args.lyrics, args.demucs)
        if args.rewhisper:
            analyze.retranscribe(out, [tuple(float(v) for v in w.split("-")) for w in args.rewhisper.split(",")], args.lang)
    an = json.load(open(out / "analysis.json", encoding="utf-8")) if (out / "analysis.json").exists() else None
    if "plan" in stages:
        import plan as planmod
        planmod.main(an, out, args.style)
    plan = json.load(open(plan_path, encoding="utf-8")) if plan_path.exists() else None
    if "images" in stages:
        import images
        images.main(plan, out, args.max_rub, args.container, only=args.only.split(",") if args.only else None,
                    force=args.force.split(",") if args.force else None)
    if "prep" in stages:
        import resolve
        resolve.resolve(plan, an, out)
    if "stills" in stages:
        st = out / "work" / "stills"
        shutil.rmtree(st, ignore_errors=True)
        fr = [int(x) for x in args.stills.split(",") if x]
        blender(args, out, ["--stills", args.stills, "--frames", f"{min(fr)}-{max(fr)}", "--out", str(st),
                            *(["--draft"] if args.draft else [])])
        import check
        check.sheet_from_dir(st, out / "work" / "stills_sheet.jpg")
    if "build" in stages:
        fdir = out / "work" / "frames"
        if not args.frames:
            shutil.rmtree(fdir, ignore_errors=True)
        blender(args, out, ["--out", str(fdir), *(["--frames", args.frames] if args.frames else []),
                            *(["--draft"] if args.draft else [])])
    video = out / f"{name}.mp4"
    if "encode" in stages:
        import encode
        encode.encode(out / "work" / "frames", out / "work" / "audio.wav", video, plan.get("style", args.style),
                      plan.get("fps", 30), args.draft)
    if "check" in stages:
        import check
        import sync_check
        check.check_video(video, out / "sheet.jpg", 24)
        sync_check.make(out, video, out / "sync_check.mp4")


if __name__ == "__main__":
    main()
