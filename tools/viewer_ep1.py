"""Серия 1 соло-ДнД «Мира от зрителей» (проект 10) от сценария до готового mp4 — «Никто и камера».

    python tools/viewer_ep1.py [--out D:/vvf-offload/vtubika/psx/ep1] [--env ../.env] [--gl gpu]
        [--preview 0,3.5,12] [--tag a] [--skip-render]

Тот же конвейер, что у серии 0 (tools/viewer_ep.py: озвучка Gemini, Whisper-слова, темп, рендер кусками, проверка
склеек), но: огонёк + её запись другим голосом (HER_VOICE), камера с VHS-эффектом (node/vrm/src/psx/vhs.ts, cam.ts),
звук с шипением ленты и шагами Сборщика. Рендер и озвучка кэшируются в ``out/work``: повторный запуск продолжает.
Итог: ``ep1.mp4``, ``ep1_tg.mp4`` (≤20 МБ), ``sheet.jpg``, ``cover.png`` (первый кадр), ``script.md``, ``report.json``.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import viewer_ep as V  # noqa: E402
from viewer_turn import check_cuts, log, render, run  # noqa: E402


def her_tape(raw: Path, words: list[dict], work: Path) -> tuple[Path, float, list[dict]]:
    """Её голос «с плёнки»: обрезать тишину по словам, полоса телефона, дрожание скорости (wow/flutter), 11 кГц, сжатие.
    Возвращает (файл, длина, слова от начала файла)."""
    a = max(0.0, words[0]["start"] - 0.06)
    b = words[-1]["end"] + 0.3
    out = work / f"{raw.stem}_tape.wav"
    dur = b - a
    run(["ffmpeg", "-y", "-v", "error", "-i", str(raw), "-af",
         f"atrim={a:.3f}:{b:.3f},asetpts=PTS-STARTPTS,highpass=f=300,lowpass=f=3200,acompressor=threshold=-22dB:ratio=4,"
         f"vibrato=f=5.4:d=0.2,aresample=11025,aresample=48000,afade=t=out:st={max(0.0, dur - 0.14):.3f}:d=0.14", "-ac", "1",
         str(out)], "её голос на плёнке")
    shifted = [{"word": w["word"], "start": round(w["start"] - a, 3), "end": round(w["end"] - a, 3)} for w in words]
    return out, dur, shifted


def hiss(total: float, work: Path) -> Path:
    """Шипение ленты: розовый шум в полосе 1.8–9 кГц с медленной дрожью уровня (эфир, как радиопомехи серии 0)."""
    out = work / "tape_hiss.wav"
    run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
         f"anoisesrc=color=pink:sample_rate=48000:amplitude=0.5:seed=11:duration={total:.2f}", "-af",
         "highpass=f=1800,lowpass=f=9000,tremolo=f=0.35:d=0.3", "-ac", "1", str(out)], "шипение ленты")
    return out


def hits_ep1(events: dict, glitch_times: list[float], total: float) -> list[tuple[str, Path, float, str]]:
    """Звуки серии 1 (только записи паков): огонёк, тики кубика, предмет, включение камеры, шаги Сборщика, помехи."""
    from viewer_ep_audio import RATE, pack

    out = [("chime", pack("freesound/triangle.ogg"), events["wisp"], "aecho=0.8:0.6:140|300:0.35|0.18")]
    t0 = events["roll"] + 0.3
    for i in range(9):  # тики: интервалы растут (кубик замедляется), как на экране
        u = i / 8
        out.append(("tick", pack(f"kenney-interface/click_00{1 + i % 5}.ogg"), t0 + 1.4 * (1 - (1 - u) ** 2) * 0.96, ""))
    out += [("ding", pack("kenney-interface/confirmation_001.ogg"), events["landed"], ""),
            ("dropitem", pack("kenney-interface/drop_002.ogg"), events["drop"], f"aecho=0.8:0.6:120:0.3"),
            ("beep", pack("kenney-interface/confirmation_003.ogg"), events["cam"] + 0.1, ""),
            ("beep", pack("kenney-interface/maximize_004.ogg"), events["old0"] + 0.02, ""),
            ("braam", pack("freesound/impact-low.ogg"), events["c0"] - 0.05,
             f"aresample={RATE},asetrate={int(RATE * 0.72)},aresample={RATE},lowpass=f=900")]
    # Шаги Сборщика: тяжёлые, медленнее и ниже обычных, раз в ~0.95 с, ближе — громче.
    t, i = events["steps"] + 0.1, 0
    while t < total - 0.7:
        out.append((("stomp", "stomp", "stomp2", "stomp2", "stomp3")[min(4, i // 2)],
                    pack(f"kenney-impact/footstep_concrete_00{(i * 3) % 5}.ogg"), t,
                    f"aresample={RATE},asetrate={int(RATE * 0.78)},aresample={RATE},lowpass=f=1100"))
        t += 0.95 - min(0.2, i * 0.02)
        i += 1
    for i, g in enumerate(glitch_times):
        out.append(("glitch", pack(f"kenney-interface/glitch_00{1 + i % 4}.ogg"), g, ""))
    return [h for h in out if 0 <= h[2] < total]


def sheet_marks(ev: dict, total: float, step: float = 2.5) -> list[tuple[float, str]]:
    """Кадр каждые ~step секунд; подпись — последнее событие до этого кадра."""
    names = [(0.0, "обложка"), (ev["name"], "имя"), (ev["roll"], "бросок"), (ev["landed"] + 0.8, "результат"), (ev["drop"], "предмет"),
             (ev["cam"], "камера REC"), (ev["old0"], "старая запись"), (ev["her1"], "обрыв"), (ev["old1"] + 0.4, "это ты?"),
             (ev["c0"], "сборщик"), (ev["choice"], "меню"), (ev["final"], "призыв")]
    names.sort()
    marks, t = [], 0.0
    while t < total - 0.2:
        marks.append((round(t, 2), next(n for tt, n in reversed(names) if tt <= t + 1e-6)))
        t += step
    marks.append((round(total - 0.15, 2), "конец"))
    return marks


def write_script(path: Path, script: list[dict], beats: list[dict], roll: dict, ev: dict, total: float, her_len: float) -> None:
    from app.viewer_dnd import PRESETS
    from app.viewer_ep import WISP_VOICE
    from app.viewer_ep1 import CHOICE, DATE, FINAL, HER_TEXT, HER_VOICE, HOOK, ROLL_SEED, STAMP

    lines = ["# Серия 1 «Никто и камера» — реплики и тайминги", "",
             f"Голос огонька: Gemini «{WISP_VOICE['voice']}» (бесплатно); её голос на плёнке: Gemini «{HER_VOICE['voice']}» (бесплатно).",
             f"Длина ролика: {total:.1f} с. Кубик: d20 из «{ROLL_SEED}» → {roll['die']} → набор «{PRESETS[roll['preset']]['title']}».",
             "", f"- 0.0–2.6 с · титр-крючок (первый кадр = обложка): {' / '.join(HOOK)}"]
    for s, b in zip(script, beats):
        lines.append(f"- {b['t0']:5.1f}–{b['t1']:5.1f} с · огонёк: {s['text']}")
        if s["beat"] == "old":
            lines.append(f"- {ev['her0']:5.1f}–{ev['her1']:5.1f} с · она на старой записи: {HER_TEXT}  (штамп {STAMP}, {DATE})")
    lines += ["", "События: " + ", ".join(f"{k} {v:.1f}" for k, v in ev.items()),
              "Меню выбора: " + " / ".join(CHOICE[1:]), "Финальный титр: " + " / ".join(FINAL)]
    path.write_text("\n".join(lines) + "\n", "utf-8")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=Path("D:/vvf-offload/vtubika/psx/ep1"))
    ap.add_argument("--env", type=Path, default=ROOT.parent.parent.parent / ".env")
    ap.add_argument("--gl", default="gpu")
    ap.add_argument("--preview", default="")
    ap.add_argument("--tag", default="")
    ap.add_argument("--skip-render", action="store_true")
    a = ap.parse_args(argv)
    work = a.out / "work"
    work.mkdir(parents=True, exist_ok=True)
    if a.env.is_file():
        from vtubika_story import load_env

        load_env(a.env)
    os.environ.setdefault("MOTION_PACKS_DIR", "D:/vvf-offload/vtubika/packs_vtb")
    from app import viewer_ep as E
    from app import viewer_ep1 as E1

    run(["npm", "run", "build:psx"], "сборка", cwd=ROOT, shell=sys.platform == "win32")
    roll = E1.ep1_roll()
    script = E1.ep1_script(roll)
    text = " ".join(s["text"] for s in script)
    log(f"кубик {roll['die']} → набор {roll['preset']}; текст: {text}")
    # Её запись: озвучка → слова → «с плёнки».
    her_raw, her_tts = V.narrate(E1.HER_TEXT, work, E1.HER_VOICE)
    V.use_cached_whisper()
    her_words_raw = V.words_aligned(E1.HER_TEXT, her_raw, work)
    her_wav, her_len, her_words = her_tape(her_raw, her_words_raw, work)
    her_env = E.envelope(V.voice_samples(her_wav), 16000, 30)
    log(f"её запись: {her_len:.1f} с, {her_tts}")
    voice, tts = V.narrate(text, work, E.WISP_VOICE)
    log(f"озвучка огонька: {tts}, {V.audio_len(voice):.1f} с")
    words = V.words_aligned(text, voice, work)
    if words[0]["start"] > 2.0:
        raise SystemExit(f"речь начинается на {words[0]['start']:.1f} с — модель зачитала указание к голосу; удалите {voice.name}")
    segs, speed, beats = E1.ep1_beats(script, words, her_len)
    voice = V.paced_voice(voice, segs, work, speed)
    total = round(beats[-1]["t1"] + E1.TAIL, 2)
    log(f"темп: ×{speed}, длина {total} с")
    env = E.envelope(V.voice_samples(voice), 16000, 30, lead=E1.LEAD)
    shifted = [{"word": w["word"], "start": w["start"], "end": w["end"]} for b in beats for w in b["words"]]
    spec = E1.build_ep1_spec(E.scene_base("street"), beats, shifted, total, env, roll, her_len, her_env, her_words)
    events = spec.pop("_events")
    spec_path = work / "spec.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=1), "utf-8")
    if a.preview:
        res = render(spec_path, a.out, a.gl, a.preview, work / (f"preview_{a.tag}" if a.tag else "preview"))
        print(json.dumps({"previews": [p["file"] for p in res.get("previews", [])], "events": events, "total": total},
                         ensure_ascii=False))
        return 0
    silent = work / "silent.mp4"
    res: dict = {}
    if not a.skip_render:
        fps = spec.get("fps", 30)
        shot_cuts = [int(round(s["t0"] * fps)) for s in spec["shots"][1:]]
        res = V.render_chunks(spec_path, silent, a.gl, int(round(total * fps)), work, shot_cuts)
        log(f"рендер: {res['frames']} кадров, чёрных {len(res['black_frames'])}")
        if res["black_frames"]:
            raise SystemExit(f"чёрные кадры: {res['black_frames'][:10]}")
    cuts: dict = {}
    try:  # scdet на VHS-плёнке видит и срывы трекинга: расхождение — в отчёт, а не падение после рендера
        cuts["scdet"] = check_cuts(silent, spec)
    except SystemExit as e:
        cuts["scdet"] = {"warning": str(e)}
        log(f"склейки (scdet): {e}")
    try:  # у VHS-серии срывы трекинга и подскоки кадра тоже дают скачки: расхождение — в отчёт, а не падение после рендера
        cuts["frames"] = V.frame_cuts(silent, spec)
    except SystemExit as e:
        cuts["frames"] = {"warning": str(e)}
        log(f"склейки (разница кадров): {e}")
    from viewer_ep_audio import mix

    final = a.out / "ep1.mp4"
    glitch = [k["t"] for k in spec["fx"]["keys"] if k.get("static", 0) >= 0.3 and k["t"] > 0.5]
    extras = [{"name": "her", "path": her_wav, "at": events["her0"], "rel_db": -1.0},
              {"name": "hiss", "path": hiss(total, work), "at": events["cam"], "rel_db": -19.0}]
    mixed = mix(silent, voice, E1.LEAD, total, events, spec["fx"]["keys"], sorted(set(round(g, 2) for g in glitch))[:5], final, work,
                hits_fn=hits_ep1, extras=extras)
    tg = a.out / "ep1_tg.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-i", str(final), "-vf", "scale=720:-2", "-c:v", "libx264", "-b:v", "2000k", "-maxrate",
         "2600k", "-bufsize", "4000k", "-preset", "slow", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags",
         "+faststart", str(tg)], "копия для Telegram")
    from vtubika_story import tile

    marks = sheet_marks(events, total)
    frames = [V.frame_at(final, t, work / f"sheet_{i:02d}.png", "scale=360:640:flags=neighbor") for i, (t, _) in enumerate(marks)]
    tile(frames, a.out / "sheet.jpg", cols=6, labels=[f"{t:.1f} {lab}" for t, lab in marks])
    V.frame_at(final, 0.0, a.out / "cover.png")
    crops = a.out / "crops"
    crops.mkdir(exist_ok=True)
    for name, t, box in (("cam", events["cam"] + 1.0, "crop=1080:1080:0:300"), ("collector", total - 1.2, "crop=1080:1200:0:300"),
                         ("menu", events["choice"] + 1.5, "crop=1080:900:0:100"), ("roll", events["landed"] + 1.2, "crop=1080:900:0:300")):
        V.frame_at(final, t, crops / f"{name}.png", box)
    write_script(a.out / "script.md", script, beats, roll, events, total, her_len)
    report = {"final": str(final), "tg": str(tg), "tg_mb": round(tg.stat().st_size / 1e6, 1), "size_mb": round(final.stat().st_size / 1e6, 1),
              "duration": total, "frames": res.get("frames"), "presence": res.get("presence"), "cuts": cuts, "tts": tts,
              "her_tts": her_tts, "mix": mixed, "events": events, "roll": {k: v for k, v in roll.items() if k != "heroine"},
              "text": text, "her_text": E1.HER_TEXT}
    (a.out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps({k: report[k] for k in ("final", "tg", "tg_mb", "duration", "frames", "cuts")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
