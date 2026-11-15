"""Интро и аутро логотипа на весь фильм: рисованный логотип (tools/logo_boil.py) поверх начала и конца.

Фильм не удлиняется и песня не сдвигается: интро ложится на первые секунды, аутро — на последние.
Логотип рендерится роликами с альфой (ProRes 4444) в размер фильма, потом один проход ffmpeg:
два наложения по времени + звуки пака freesound (громкость — правило сервиса, от громкости
музыки в этих местах) → новый фильм; по желанию — лёгкая копия для Telegram (≤ ~45 МБ).

    python tools/logo_bumpers.py --movie D:/…/movie5v41_9x16.mp4 --logo D:/…/yakoodev_master.png \
        --out D:/…/movie5v42_9x16.mp4 --tg D:/…/movie5v42_9x16_tg.mp4
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from logo_boil import VARIANTS, Renderer, probe, run, sfx, sound_cues, write_frames  # noqa: E402

# Раскладка по форматам: ширина логотипа (доля кадра) и центр по высоте. 9:16 — крупно (≈ 78 % ширины);
# аутро — ниже середины, чтобы не закрыть финальную плашку фильма («ЧЕМПИОН»).
LAYOUT = {
    "portrait": {"intro": (0.78, 0.45), "outro": (0.78, 0.79)},
    "landscape": {"intro": (0.5, 0.47), "outro": (0.42, 0.79)},
}


def render_alpha(logo: Path, meta: dict, w: int, h: int, fps: float, width: float, y: float, timing, span: float,
                 out: Path, cache: Path, seed: int) -> None:
    r = Renderer(logo, w, h, width, y, timing, seed, meta, cache)
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{w}x{h}",
                             "-r", f"{fps:g}", "-i", "-", "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le",
                             str(out)], stdin=subprocess.PIPE)
    write_frames(r, fps, span, timing.start, proc.stdin, True)
    proc.stdin.close()
    if proc.wait() != 0:
        raise SystemExit(f"ffmpeg (альфа {out.name}) упал")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--movie", required=True, type=Path)
    ap.add_argument("--logo", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--tg", type=Path, help="лёгкая копия для Telegram (720p, ~2 Мбит/с)")
    ap.add_argument("--variant", default="L", choices=sorted(VARIANTS))
    ap.add_argument("--intro", type=float, default=3.5, help="длина интро, с (лишнее/недостающее — в петлю живого логотипа)")
    ap.add_argument("--outro", type=float, default=3.5, help="длина аутро, с: логотип живёт до последнего кадра")
    ap.add_argument("--intro-start", type=float, default=0.15)
    ap.add_argument("--crf", type=int, default=16)
    ap.add_argument("--work", type=Path, default=None)
    ap.add_argument("--seed", type=int, default=3)
    args = ap.parse_args(argv)

    info = probe(args.movie)
    w, h, fps, dur = info["w"], info["h"], info["fps"], info["dur"]
    lay = LAYOUT["portrait" if h >= w else "landscape"]
    meta_path = args.logo.with_suffix(".json")
    meta = json.loads(meta_path.read_text("utf-8")) if meta_path.exists() else {}
    work_root = args.work or args.out.parent
    work_root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="bumpers-", dir=str(work_root)))
    cache = work_root / "logo-vectors"

    base = VARIANTS[args.variant]
    fixed = base.sketch + base.arc + base.burst
    # Интро: длина задаётся петлёй живого логотипа (hold), остальное — как одобрено.
    intro_t = replace(base, start=args.intro_start, hold=max(0.0, args.intro - args.intro_start - fixed - base.exit))
    intro_span = intro_t.start + intro_t.total + 0.05
    # Аутро: тот же рисунок, без ухода — логотип живёт до конца фильма.
    outro_start = 0.1
    outro_span = args.outro + outro_start
    outro_t = replace(base, start=outro_start, hold=max(0.0, outro_span - outro_start - fixed - 0.02), exit=0.02)
    outro_at = max(0.0, dur - outro_span)

    intro_mov, outro_mov = work / "intro.mov", work / "outro.mov"
    render_alpha(args.logo, meta, w, h, fps, *lay["intro"], intro_t, intro_span, intro_mov, cache, args.seed)
    render_alpha(args.logo, meta, w, h, fps, *lay["outro"], outro_t, outro_span, outro_mov, cache, args.seed + 1)

    # Громкость эффектов — от музыки в этих местах фильма (на 10 дБ тише громких мест).
    from app.motion_render import audio_levels, effect_gain_db

    def loud_at(start: float, length: float) -> float | None:
        lv = audio_levels(args.movie, seconds=length, start=start)
        return lv[0] if lv and lv[0] > -70 else None

    cues = [(n, at, ln, loud_at(0.0, intro_span)) for n, at, ln in sound_cues(intro_t, True)]
    cues += [(n, outro_at + at, ln, loud_at(outro_at, outro_span)) for n, at, ln in sound_cues(outro_t, False)]
    inputs = ["-i", str(args.movie), "-i", str(intro_mov), "-i", str(outro_mov)]
    chains = [
        "[1:v]format=yuva444p10le[iv]",
        # Аутро сдвинуто прозрачными кадрами в начале: наложение точно попадает в конец фильма.
        f"[2:v]format=rgba,tpad=start_duration={outro_at:.4f}:color=black@0.0[ov]",
        "[0:v][iv]overlay=eof_action=pass:format=auto[v1]",
        "[v1][ov]overlay=eof_action=pass:format=auto,format=yuv420p[v]",
    ]
    labels = ["[0:a]"]
    for i, (name, at, length, loud) in enumerate(cues):
        path = sfx(name)
        gain = effect_gain_db(str(path), loud, speechless=False)
        inputs += ["-i", str(path)]
        ms = int(round(at * 1000))
        trim = f"atrim=0:{length:.3f},afade=t=out:st={max(0.0, length - 0.08):.3f}:d=0.08," if length > 0 else ""
        chains.append(f"[{i + 3}:a]aresample=48000,{trim}volume={gain:.2f}dB,adelay={ms}|{ms}[s{i}]")
        labels.append(f"[s{i}]")
    chains.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:duration=first[a]")
    run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", ";".join(chains), "-map", "[v]", "-map", "[a]",
         "-c:v", "libx264", "-preset", "medium", "-crf", str(args.crf), "-profile:v", "high", "-r", f"{fps:g}",
         "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart", str(args.out)])
    if args.tg:
        scale = "scale=720:-2" if h >= w else "scale=-2:720"
        run(["ffmpeg", "-v", "error", "-y", "-i", str(args.out), "-vf", f"{scale}:flags=lanczos", "-c:v", "libx264", "-preset", "medium",
             "-b:v", "2000k", "-maxrate", "2600k", "-bufsize", "4000k", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
             str(args.tg)])
    for p in (intro_mov, outro_mov):
        p.unlink(missing_ok=True)
    print(json.dumps({"out": str(args.out), "tg": str(args.tg) if args.tg else None, "intro": round(intro_t.start + intro_t.total, 2),
                      "outro_at": round(outro_at, 2), "work": str(work)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
