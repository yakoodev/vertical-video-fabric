"""Короткая история в кубическом мире на движке 3D-серий (Blender ставит позы, three.js рисует) — без озвучки и
платных вызовов: план с камерой автора → рендер → звук из записей паков → титр в конце → mp4 + лёгкая копия + лист.

    python tools/series3d_voxel_story.py OUT_DIR [--quality fast|preview|draft] [--only 0,3] [--snd DIR]
        [--cache DIR] [--no-render] [--tg-mb 19.5]

История по умолчанию — «Не открывай» (``STORY``): ночь, герой у факела в кубическом домике, три стука в дверь, к
окну — в поле пусто, оборачивается — в тёмном углу высокий силуэт с белыми глазами, резко в чёрное, строка
«Не открывай после полуночи». Своя история — JSON того же вида (``--story FILE``): ``shots`` (как в задании серии:
action/target/look/emotion/location, камера автора ``cam``), ``events`` (стук ``knock`` со своей записью ``sfx``,
появления ``appear``), ``sound`` (фон, музыка, акценты), ``end_card`` (строки, секунды).

Звук — только настоящие записи паков (``sfx:пак/файл``), файлы ищутся в ``--snd`` по имени (копии паков сервиса).
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import series3d_blender as SB  # noqa: E402

FPS = SB.FPS


def _shot(n, t0, t1, action, cam, **kw):
    return {"n": n, "t0": t0, "t1": t1, "text": kw.pop("text", ""), "role": "", "sentences": [n], "action": action,
            "target": kw.pop("target", "center"), "button": "1", "emotion": kw.pop("emotion", "calm"),
            "camera": cam.get("kind", "detail"), "speaker": "narrator", "by": "author", "frame": "", "planned": True,
            "location": "blockhouse", "decal": "", "props": [], "speaks": False, "show_none": True, "posture": "stand",
            "stand_first": kw.pop("stand_first", False), "cam": cam, **kw}


WOOD = "sfx:kenney-impact/impactWood_heavy_00{}.ogg"
STEP = "sfx:kenney-impact/footstep_wood_00{}.ogg"
# «Не открывай»: 26 с картинки + 3 с титра
STORY = {
    "title": "Не открывай",
    "duration": 26.0,
    "shots": [
        # 0: снаружи — домик в поле, светится окно, квадратная луна над крышей; медленный наезд
        _shot(0, 0.0, 3.2, "sit_corner", {"kind": "overhead", "pos": [-1.3, -6.8, 1.0], "to": [-0.9, -5.1, 0.92],
                                          "point": [0.0, -0.6, 1.3], "face_w": 0.0, "lens": 20, "fstop": 8,
                                          "probe": [0.0, -4.0, 1.0]}),
        # 1: у факела — наезд на лицо, свет огня сбоку
        _shot(1, 3.2, 6.8, "wait", {"kind": "face", "pos": [-0.08, -0.50, 0.62], "to": [0.12, -0.40, 0.60],
                                    "point": "face", "face_w": 1.0, "lens": 22, "fstop": 4.0, "face_y": 0.42}),
        # 2: три стука — через плечо на дверь
        _shot(2, 6.8, 10.0, "listen", {"kind": "ots", "pos": [0.80, 0.02, 0.80], "to": [0.68, -0.12, 0.76],
                                       "point": [-0.46, -1.18, 0.42], "face_w": 0.0, "lens": 22, "fstop": 5.6, "focus": "point"},
              look="door", emotion="scared"),
        # 3: встаёт и идёт к окну — общий из дальнего угла
        _shot(3, 10.0, 14.9, "approach", {"kind": "overhead", "pos": [-0.98, -0.02, 0.98], "to": [-0.86, -0.14, 0.95],
                                          "point": "window", "face_w": 0.6, "lens": 18, "fstop": 5.6, "face_y": 0.45},
              target="window", stand_first=True, stand_walk=True, emotion="worried"),
        # 4: у окна через плечо — тёмное поле
        _shot(4, 14.9, 17.8, "wait", {"kind": "ots", "pos": [0.80, 0.02, 0.98], "to": [0.74, -0.14, 0.95],
                                      "point": [0.46, -1.7, 0.62], "face_w": 0.10, "lens": 24, "fstop": 5.6,
                                      "focus": "point"}, look="field", emotion="worried"),
        # 5: его глазами — в поле пусто
        _shot(5, 17.8, 19.6, "wait", {"kind": "detail", "pos": [0.35, -1.00, 0.80], "to": [0.35, -1.10, 0.80],
                                      "point": [0.0, -9.0, 0.55], "face_w": 0.0, "lens": 26, "fstop": 8,
                                      "focus": "point", "probe": [0.4, -3.0, 0.8]}, look="field", emotion="worried"),
        # 6: оборачивается — через плечо в комнату, в углу загораются глаза
        _shot(6, 19.6, 22.8, "approach", {"kind": "ots", "pos": [0.99, -1.00, 0.84], "to": [0.92, -0.90, 0.84],
                                          "point": "corner", "face_w": 0.30, "lens": 18, "fstop": 8, "focus": "point"},
              target="room", look="corner", look_at=1.2, emotion="worried"),
        # 7: лицо — испуг, замер
        _shot(7, 22.8, 24.6, "flinch", {"kind": "face", "pos": [0.38, 0.12, 0.78], "to": [0.34, -0.04, 0.76],
                                        "point": "face", "face_w": 1.0, "lens": 24, "fstop": 2.8, "face_y": 0.44},
              look="corner", emotion="startled"),
        # 8: глаза гостя — быстрый наезд, обрыв в чёрное
        _shot(8, 24.6, 26.0, "wait", {"kind": "detail", "pos": [0.12, -0.38, 0.80], "to": [-0.38, 0.06, 1.02],
                                      "point": [-0.84, 0.42, 1.13], "face_w": 0.0, "lens": 32, "fstop": 4.0,
                                      "focus": "point"}, look="corner", emotion="startled"),
    ],
    "events": [
        {"kind": "knock", "t": 7.50, "sfx": WOOD.format(0), "volume": 1.0, "label": "стук"},
        {"kind": "knock", "t": 7.92, "sfx": WOOD.format(2), "volume": 1.0, "label": "стук"},
        {"kind": "knock", "t": 8.34, "sfx": WOOD.format(3), "volume": 1.0, "label": "стук"},
        {"kind": "appear", "what": "guest", "t": 19.6},
        {"kind": "appear", "what": "eyes", "t": 21.0, "sfx": "sfx:freesound/sting.ogg", "volume": 1.0, "label": "акцент"},
    ],
    # звук: фон ночи, тихая тревожная музыка, шаги по доскам — по шагам режиссёра, обрыв в чёрное
    "sound": {
        "beds": [{"ref": "sfx:tension/bed-space-drone.ogg", "t": 0.0, "end": 26.0, "db": -10, "fade_in": 1.0, "fade_out": 0.05},
                 {"ref": "sfx:tension/bed-ominous-deep.ogg", "t": 3.0, "end": 26.0, "db": -14, "fade_in": 3.0, "fade_out": 0.05},
                 {"ref": "sfx:tension/bed-space-drone.ogg", "t": 26.0, "end": 29.0, "db": -18, "fade_in": 0.3, "fade_out": 1.2}],
        "steps": {"ref": STEP, "db": -3},
        "hits": [{"ref": "sfx:freesound/impact-low.ogg", "t": 26.0, "db": 2}],
        "db": {"knock": 4, "sting": 2},
    },
    "end_card": {"lines": ["Не открывай", "после полуночи"], "sec": 3.0},
    "three_spec": {"envIntensity": 1.3, "haze": 0.8, "fixDynamic": True,
                   "post": {"bloom": {"strength": 0.38, "radius": 0.45, "threshold": 1.0}, "vignette": 0.5, "grain": 0.02}},
}


def build_job(story: dict, quality: str) -> dict:
    return {"version": SB.SCENE_VERSION, "fps": FPS, "duration": float(story["duration"]), "title": story["title"],
            "shots": story["shots"], "events": story["events"], "words": [], "director": "author", "speech": {},
            "director_error": "", "envelope": [], "lipsync": "off", "quality": quality, "look": {}, "seed": 7,
            "render_backend": "threejs", "hero": "blocky", "three_spec": story.get("three_spec") or {},
            "ambience": []}


# ---------------------------------------------------------------- звук

def _find(snd: Path, ref: str) -> Path:
    name = ref.split("/", 1)[-1]
    hits = list(snd.rglob(name))
    if not hits:
        raise SystemExit(f"нет записи {ref} в {snd}")
    return hits[0]


def step_times(perf: dict) -> list[float]:
    """Шаги героя: по окнам клипа ходьбы режиссуры (шаг — полцикла Walking_C), первый — через четверть шага."""
    half = SB.CLIP_LEN[SB.WALK_CLIP] / 2
    out = []
    for sg in perf["track"]:
        if sg["clip"] != SB.WALK_CLIP:
            continue
        end = sg["end"] if sg.get("end") is not None else sg["start"] + half
        t = sg["start"] + half * 0.55
        while t < end - 0.05:
            out.append(round(t, 3))
            t += half
    return out


def mix_audio(story: dict, job: dict, perf: dict, snd: Path, out_wav: Path, total: float) -> list[dict]:
    """Дорожка звука: фоны (петлёй, с наплывами), события режиссуры с их записями, шаги, акценты. → список того, что
    положено (для отчёта)."""
    cues = []
    so = story.get("sound") or {}
    dbs = so.get("db") or {}
    for b in so.get("beds") or []:
        cues.append({"ref": b["ref"], "t": b["t"], "end": b["end"], "db": b["db"], "fade_in": b.get("fade_in", 0.5),
                     "fade_out": b.get("fade_out", 0.5), "loop": True, "label": "фон"})
    for s in SB.sfx_for(job, perf):
        if s.get("role") == "ambience":
            continue
        db = dbs.get(s["kind"], dbs.get("knock" if s["kind"] == "knock" else "sting", -3))
        cues.append({"ref": s["ref"], "t": s["t"], "db": db, "label": s["label"]})
    st = so.get("steps")
    if st:
        for i, t in enumerate(step_times(perf)):
            cues.append({"ref": st["ref"].format(i % 5), "t": t, "db": st["db"] - (i % 2) * 1.5, "label": "шаг"})
    for h in so.get("hits") or []:
        cues.append({"ref": h["ref"], "t": h["t"], "db": h["db"], "label": "удар"})
    args = ["ffmpeg", "-y", "-v", "error"]
    filt = []
    for i, c in enumerate(cues):
        path = _find(snd, c["ref"])
        if c.get("loop"):
            args += ["-stream_loop", "-1"]
        args += ["-i", str(path)]
        chain = f"[{i}:a]aformat=sample_rates=48000:channel_layouts=stereo"
        if c.get("loop"):
            dur = c["end"] - c["t"]
            chain += (f",atrim=0:{dur:.3f},afade=t=in:d={c['fade_in']}"
                      f",afade=t=out:st={max(0.0, dur - c['fade_out']):.3f}:d={c['fade_out']}")
        chain += f",volume={c['db']}dB,adelay={int(c['t'] * 1000)}|{int(c['t'] * 1000)}[a{i}]"
        filt.append(chain)
    filt.append("".join(f"[a{i}]" for i in range(len(cues))) + f"amix=inputs={len(cues)}:normalize=0:dropout_transition=0,"
                f"atrim=0:{total:.3f},loudnorm=I=-17:LRA=16:TP=-1.5,aresample=48000[out]")
    args += ["-filter_complex", ";".join(filt), "-map", "[out]", "-ac", "2", "-ar", "48000", str(out_wav)]
    subprocess.run(args, check=True)
    return [{k: c[k] for k in ("ref", "t", "db", "label")} for c in cues]


# ---------------------------------------------------------------- титр

def end_card(lines: list[str], sec: float, out_mp4: Path, work: Path) -> None:
    """Чёрный экран, строка пиксельными буквами (свой растр: шрифт системы без сглаживания, увеличение «по пикселю»),
    проявляется ступенями, как в игре."""
    from PIL import Image, ImageDraw, ImageFont

    font = None
    for f in ("C:/Windows/Fonts/consolab.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"):
        if Path(f).is_file():
            font = ImageFont.truetype(f, 13)
            break
    font = font or ImageFont.load_default()
    small_w, small_h = 1080 // 5, 1920 // 5
    frames = work / "card"
    if frames.exists():
        shutil.rmtree(frames)
    frames.mkdir(parents=True)
    n = int(round(sec * FPS))
    for k in range(n):
        t = k / FPS
        # проявление ступенями: 0 → 35 → 70 → 100 % за 0.6 с, уход — за последние 0.5 с
        a = min(1.0, math.floor(max(0.0, t - 0.25) / 0.2 + 1e-6) / 3.0) if t >= 0.25 else 0.0
        a *= min(1.0, max(0.0, (sec - t) / 0.5)) if t > sec - 0.5 else 1.0
        im = Image.new("RGB", (small_w, small_h), (0, 0, 0))
        dr = ImageDraw.Draw(im)
        dr.fontmode = "1"
        y = small_h // 2 - 16
        for line in lines:
            w = dr.textlength(line, font=font)
            x = int((small_w - w) // 2)
            c = int(225 * a)
            dr.text((x + 1, y + 1), line, font=font, fill=(int(c * 0.25), int(c * 0.22), int(c * 0.3)))
            dr.text((x, y), line, font=font, fill=(c, c, int(c * 0.97)))
            y += 17
        im.resize((1080, 1920), Image.NEAREST).save(frames / f"c_{k:04d}.png")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-framerate", str(FPS), "-i", str(frames / "c_%04d.png"), "-c:v", "libx264",
                    "-preset", "slow", "-crf", "16", "-pix_fmt", "yuv420p", str(out_mp4)], check=True)
    shutil.rmtree(frames, ignore_errors=True)


# ---------------------------------------------------------------- лист кадров

def contact_sheet(video: Path, out_jpg: Path, story: dict) -> None:
    """Лист: по кадру из середины каждого плана + начало/конец планов с движением, подписи — номер плана и время."""
    times = []
    for s in story["shots"]:
        a, b = s["t0"], s["t1"]
        times += [a + 0.25, (a + b) / 2, b - 0.2]
    times.append(story["duration"] + 1.2)
    tmp = out_jpg.parent / "_sheet"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir()
    for i, t in enumerate(times):
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1", "-vf",
                        f"scale=270:480,drawtext=text='{t:.1f}s':x=6:y=6:fontsize=20:fontcolor=white:box=1:boxcolor=black@0.5",
                        str(tmp / f"s_{i:03d}.jpg")], check=True)
    cols = 7
    rows = math.ceil(len(times) / cols)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-framerate", "1", "-i", str(tmp / "s_%03d.jpg"), "-vf",
                    f"tile={cols}x{rows}:padding=4:color=0x202020", "-frames:v", "1", "-q:v", "3", str(out_jpg)], check=True)
    shutil.rmtree(tmp, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="история в кубическом мире: план → рендер → звук → mp4")
    ap.add_argument("out_dir")
    ap.add_argument("--story", help="JSON истории (по умолчанию — «Не открывай»)")
    ap.add_argument("--name", default="mc_test")
    ap.add_argument("--quality", default="fast", choices=list(SB.QUALITY))
    ap.add_argument("--only", default="", help="номера планов через запятую — только они (проверка)")
    ap.add_argument("--snd", default="", help="папка с записями паков (по умолчанию <out_dir>/snd)")
    ap.add_argument("--cache", default="")
    ap.add_argument("--tg-mb", type=float, default=19.5)
    a = ap.parse_args(argv)
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    story = json.loads(Path(a.story).read_text("utf-8")) if a.story else STORY
    job = build_job(story, a.quality)
    work = out / "work"
    cache = Path(a.cache) if a.cache else out / "cache"
    only = [int(x) for x in a.only.split(",") if x.strip()] or None
    raw = out / f"{a.name}_video.mp4"

    def prog(p, note):
        print(f"[{p * 100:5.1f}%] {note}", flush=True)
    res = SB.render_local(job, raw, work=work, cache=cache, progress=prog, only=only)
    print(json.dumps({k: res.get(k) for k in ("frames", "rendered", "seconds", "backend")}, ensure_ascii=False))
    perf = SB.perform(job)
    (out / "perf_shots.json").write_text(json.dumps({"cameras": perf["cameras"], "track": perf["track"]}, ensure_ascii=False,
                                                    default=str, indent=1), "utf-8")
    if only:
        return 0
    snd = Path(a.snd) if a.snd else (out / "snd" if (out / "snd").is_dir() else out.parent / "snd")
    card = story.get("end_card") or {}
    total = float(story["duration"]) + float(card.get("sec", 0.0))
    wav = out / f"{a.name}.wav"
    cues = mix_audio(story, job, perf, snd, wav, total)
    (out / "sound_cues.json").write_text(json.dumps(cues, ensure_ascii=False, indent=1), "utf-8")
    parts = [raw]
    if card:
        cm = out / f"{a.name}_card.mp4"
        end_card(card["lines"], float(card["sec"]), cm, out)
        parts.append(cm)
    lst = out / "concat.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), "utf-8")
    final = out / f"{a.name}.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(wav), "-map", "0:v",
                    "-map", "1:a", "-c:v", "libx264", "-preset", "slow", "-crf", "17", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-b:a", "192k", "-movflags", "+faststart", "-shortest", str(final)], check=True)
    # лёгкая копия для Telegram (≤ tg-mb): двухпроходное по битрейту
    tg = out / f"{a.name}_tg.mp4"
    if final.stat().st_size <= a.tg_mb * 2**20:
        shutil.copyfile(final, tg)
        contact_sheet(final, out / f"{a.name}_sheet.jpg", story)
        print(json.dumps({"final": str(final), "mb": round(final.stat().st_size / 2**20, 2), "tg": str(tg)}, ensure_ascii=False))
        return 0
    kbps = int(a.tg_mb * 8 * 1024 / total * 0.93) - 128
    log = out / "x264_2pass"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(final), "-c:v", "libx264", "-preset", "slow", "-b:v", f"{kbps}k",
                    "-pass", "1", "-passlogfile", str(log), "-an", "-f", "mp4", "NUL" if sys.platform == "win32" else "/dev/null"],
                   check=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(final), "-c:v", "libx264", "-preset", "slow", "-b:v", f"{kbps}k",
                    "-maxrate", f"{int(kbps * 1.6)}k", "-bufsize", f"{kbps * 2}k", "-pass", "2", "-passlogfile", str(log),
                    "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(tg)], check=True)
    for p_ in out.glob("x264_2pass*"):
        p_.unlink(missing_ok=True)
    contact_sheet(final, out / f"{a.name}_sheet.jpg", story)
    print(json.dumps({"final": str(final), "mb": round(final.stat().st_size / 2**20, 2), "tg": str(tg),
                      "tg_mb": round(tg.stat().st_size / 2**20, 2)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
