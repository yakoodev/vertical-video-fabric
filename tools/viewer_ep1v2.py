"""Серия 1, версия 2 («Никто и камера») от сценария до готового mp4.

    python tools/viewer_ep1v2.py [--out D:/vvf-offload/vtubika/psx/ep1v2] [--env ../.env] [--gl gpu]
        [--preview 0,3.5,12] [--tag a] [--skip-render]

Конвейер серии 0 и v1 (озвучка Gemini, Whisper-слова, темп, рендер кусками, проверка склеек) — но: настоящий кубик,
камера в её руках (селфи), запись на вокзале (экранчик → весь кадр), другой темп. Озвучка и рендер кэшируются в
``out/work``: повторный запуск продолжает. Итог: ``ep1.mp4``, ``ep1_tg.mp4`` (≤20 МБ), ``sheet.jpg``, ``cover.png``,
``script.md``, ``report.json``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import viewer_ep as V  # noqa: E402
from viewer_ep1 import her_tape, hiss  # noqa: E402
from viewer_turn import check_cuts, log, render, run  # noqa: E402


def hum(total: float, t0: float, t1: float, work: Path) -> Path:
    """Гул люминесцентных ламп вокзала: 100 Гц и гармоники с неровным дрожанием, включается и гаснет с записью."""
    out = work / "station_hum.wav"
    d = t1 - t0
    run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=f=100:sample_rate=48000:duration={d:.2f}", "-f", "lavfi", "-i",
         f"sine=f=300:sample_rate=48000:duration={d:.2f}", "-f", "lavfi", "-i",
         f"anoisesrc=color=brown:sample_rate=48000:amplitude=0.4:seed=5:duration={d:.2f}", "-filter_complex",
         "[0]volume=0.5[a];[1]volume=0.18,tremolo=f=7:d=0.5[b];[2]lowpass=f=500[c];[a][b][c]amix=inputs=3:normalize=0,"
         f"afade=t=in:d=0.25,afade=t=out:st={max(0.0, d - 0.3):.2f}:d=0.3", "-ac", "1", str(out)], "гул вокзала")
    return out


def hits_ep2(events: dict, glitch_times: list[float], total: float) -> list[tuple[str, Path, float, str]]:
    """Звуки серии (только записи паков): огонёк, подбрасывание и катящийся кубик, камера, шаги Сборщика, помехи."""
    from viewer_ep_audio import RATE, pack

    out = [("chime", pack("freesound/triangle.ogg"), 0.2, "aecho=0.8:0.6:140|300:0.35|0.18")]
    # Кубик: стук о землю на каждом отскоке (после подбрасывания), потом доскатывается.
    t0, T = events["throw"], events["land"] - events["throw"]
    for i, k in enumerate((0.45, 0.72, 0.88, 0.96)):
        out.append(("tick", pack(f"kenney-impact/impactGeneric_light_00{i % 5}.ogg"), t0 + T * k, ""))
    for i in range(6):  # тики прокатывания
        out.append(("tick", pack(f"kenney-interface/click_00{1 + i % 5}.ogg"), t0 + T * (0.5 + 0.08 * i), ""))
    out += [("ding", pack("kenney-interface/confirmation_001.ogg"), events["land"] + 0.15, ""),
            ("dropitem", pack("kenney-interface/drop_002.ogg"), events["drop"], "aecho=0.8:0.6:120:0.3"),
            ("beep", pack("kenney-interface/confirmation_003.ogg"), events["self"] + 0.05, ""),
            ("beep", pack("kenney-interface/maximize_004.ogg"), events["lcd0"] + 0.02, ""),
            ("beep", pack("kenney-interface/maximize_004.ogg"), events["full0"] + 0.02, ""),
            ("braam", pack("freesound/impact-low.ogg"), events["c0"] - 0.05,
             f"aresample={RATE},asetrate={int(RATE * 0.72)},aresample={RATE},lowpass=f=900")]
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
    names = [(0.0, "обложка"), (ev["name"], "имя"), (ev["throw"], "кубик"), (ev["land"], "выпало"), (ev["drop"], "камера"),
             (ev["take"], "берёт"), (ev["self"], "селфи REC"), (ev["out"], "смотрит"), (ev["lcd0"], "запись на экранчике"),
             (ev["full0"], "запись PLAY"), (ev["her0"], "её голос"), (ev["tear"], "обрыв"), (ev["full1"], "в свете экранчика"),
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
    from app.viewer_ep1v2 import CHOICE, DATE_LIVE, DATE_TAPE, FINAL, HER_TEXT, HER_VOICE, HOOK, ROLL_SEED, STAMP_LIVE, STAMP_TAPE

    lines = ["# Серия 1, версия 2 «Никто и камера» — реплики и тайминги", "",
             f"Голос огонька: Gemini «{WISP_VOICE['voice']}» (бесплатно); её голос на плёнке: Gemini «{HER_VOICE['voice']}» (бесплатно).",
             f"Длина ролика: {total:.1f} с. Кубик: d20 из «{ROLL_SEED}» → {roll['die']} → набор «{PRESETS[roll['preset']]['title']}».", "",
             f"- 0.0–2.6 с · титр-крючок (первый кадр = обложка): {' / '.join(HOOK)}"]
    marks = {"item1": (ev["throw"], ev["land"], f"кубик подброшен огоньком, кувыркается, подпрыгивает, ложится выпавшей гранью ({roll['die']})")}
    for s, b in zip(script, beats):
        if s["beat"] == "item1":
            lines.append(f"- {ev['throw']:5.1f}–{ev['land']:5.1f} с · ТИШИНА: настоящий d20 катится по асфальту, окно «БРОСОК» сверху")
        lines.append(f"- {b['t0']:5.1f}–{b['t1']:5.1f} с · огонёк: {s['text']}")
        if s["beat"] == "item2":
            lines.append(f"- {ev['take']:5.1f} с · она берёт камеру в руки; {ev['self']:.1f}–{ev['out']:.1f} с селфи на вытянутой руке (REC, {STAMP_LIVE} {DATE_LIVE})")
        if s["beat"] == "old":
            lines.append(f"- {ev['out']:5.1f}–{ev['full0']:5.1f} с · она смотрит на откидной экранчик (из-за плеча, наезд); "
                         f"с {ev['lcd0']:.1f} с на нём идёт запись с вокзала")
            lines.append(f"- {ev['full0']:5.1f}–{ev['full1']:5.1f} с · запись на весь кадр: вокзал, ▶ PLAY, {STAMP_TAPE} {DATE_TAPE}, фонарик; "
                         f"её голос {ev['her0']:.1f}–{ev['her1']:.1f} с: {HER_TEXT}; за ней в проходе движется Сборщик; обрыв в помехи {ev['tear']:.1f} с")
    lines += ["", "События: " + ", ".join(f"{k} {v:.1f}" for k, v in ev.items()),
              "Меню выбора: " + " / ".join(CHOICE[1:]), "Финальный титр: " + " / ".join(FINAL)]
    path.write_text("\n".join(lines) + "\n", "utf-8")
    del marks


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=Path("D:/vvf-offload/vtubika/psx/ep1v2"))
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
    from app import viewer_ep1v2 as E2

    run(["npm", "run", "build:psx"], "сборка", cwd=ROOT, shell=sys.platform == "win32")
    roll = E2.ep1_roll()
    script = E2.ep2_script(roll)
    text = " ".join(s["text"] for s in script)
    log(f"кубик {roll['die']} → набор {roll['preset']}; текст: {text}")
    her_raw, her_tts = V.narrate(E2.HER_TEXT, work, E2.HER_VOICE)
    V.use_cached_whisper()
    her_words_raw = V.words_aligned(E2.HER_TEXT, her_raw, work)
    her_wav, her_len, her_words = her_tape(her_raw, her_words_raw, work)
    her_env = E.envelope(V.voice_samples(her_wav), 16000, 30)
    log(f"её запись: {her_len:.1f} с, {her_tts}")
    voice, tts = V.narrate(text, work, E.WISP_VOICE)
    log(f"озвучка огонька: {tts}, {V.audio_len(voice):.1f} с")
    words = V.words_aligned(text, voice, work)
    if words[0]["start"] > 2.0:
        raise SystemExit(f"речь начинается на {words[0]['start']:.1f} с — модель зачитала указание к голосу; удалите {voice.name}")
    segs, speed, beats = E2.ep2_beats(script, words, her_len)
    voice = V.paced_voice(voice, segs, work, speed)
    total = round(beats[-1]["t1"] + E2.TAIL, 2)
    log(f"темп: ×{speed}, длина {total} с")
    env = E.envelope(V.voice_samples(voice), 16000, 30, lead=E2.LEAD)
    shifted = [{"word": w["word"], "start": w["start"], "end": w["end"]} for b in beats for w in b["words"]]
    spec = E2.build_ep2_spec(E.scene_base("street"), beats, shifted, total, env, roll, her_len, her_env, her_words)
    events = spec.pop("_events")
    spec_path = work / "spec.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=1), "utf-8")
    if a.preview:
        res = render(spec_path, a.out, a.gl, a.preview, work / (f"preview_{a.tag}" if a.tag else "preview"))
        print(json.dumps({"previews": [p["file"] for p in res.get("previews", [])], "events": events, "total": total}, ensure_ascii=False))
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
    try:
        cuts["scdet"] = check_cuts(silent, spec)
    except SystemExit as e:
        cuts["scdet"] = {"warning": str(e)}
        log(f"склейки (scdet): {e}")
    try:
        cuts["frames"] = V.frame_cuts(silent, spec)
    except SystemExit as e:
        cuts["frames"] = {"warning": str(e)}
        log(f"склейки (разница кадров): {e}")
    from viewer_ep_audio import mix

    final = a.out / "ep1.mp4"
    glitch = [k["t"] for k in spec["fx"]["keys"] if k.get("static", 0) >= 0.3 and k["t"] > 0.5]
    extras = [{"name": "her", "path": her_wav, "at": events["her0"], "rel_db": -1.0},
              {"name": "hiss", "path": hiss(total, work), "at": events["self"], "rel_db": -19.0, "af": ""},
              {"name": "hum", "path": hum(total, events["full0"], events["full1"], work), "at": events["full0"], "rel_db": -17.0}]
    mixed = mix(silent, voice, E2.LEAD, total, events, spec["fx"]["keys"], sorted(set(round(g, 2) for g in glitch))[:6], final, work,
                hits_fn=hits_ep2, extras=extras)
    tg = a.out / "ep1_tg.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-i", str(final), "-vf", "scale=720:-2", "-c:v", "libx264", "-b:v", "2000k", "-maxrate",
         "2600k", "-bufsize", "4000k", "-preset", "slow", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags",
         "+faststart", str(tg)], "копия для Telegram")
    from vtubika_story import tile

    marks = sheet_marks(events, total)
    frames = [V.frame_at(final, t, work / f"sheet_{i:02d}.png", "scale=360:640:flags=neighbor") for i, (t, _) in enumerate(marks)]
    tile(frames, a.out / "sheet.jpg", cols=6, labels=[f"{t:.1f} {lab}" for t, lab in marks])
    V.frame_at(final, 0.0, a.out / "cover.png")
    write_script(a.out / "script.md", script, beats, roll, events, total, her_len)
    report = {"final": str(final), "tg": str(tg), "tg_mb": round(tg.stat().st_size / 1e6, 1), "size_mb": round(final.stat().st_size / 1e6, 1),
              "duration": total, "frames": res.get("frames"), "presence": res.get("presence"), "cuts": cuts, "tts": tts,
              "her_tts": her_tts, "mix": mixed, "events": events, "roll": {k: v for k, v in roll.items() if k != "heroine"},
              "text": text, "her_text": E2.HER_TEXT}
    (a.out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps({k: report[k] for k in ("final", "tg", "tg_mb", "duration", "frames", "cuts")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
