"""Серия 2 «Сара» (проект 10, @.psx53) от сценария до готового mp4.

    python tools/viewer_ep2.py [--out D:/vvf-offload/vtubika/psx/ep2] [--env ../.env] [--gl gpu]
        [--monster] [--tts-only] [--preview 0,3.5,12] [--tag a] [--skip-render]

``--monster`` — только проба нового Сборщика: 8 стопкадров в зале → ``monster_sheet.jpg`` (до рендера серии).
``--tts-only`` — только озвучка (бесплатный Gemini: огонёк Enceladus, она — Leda), без Whisper и рендера.
Конвейер серии 1 v2 (озвучка → Whisper-слова → темп → рендер кусками → склейки → звук), плюс: ровно 10 кадров помех
после кадра-обложки и 12 в конце (проверяется по кадрам), файл для TikTok — CFR 30 с ограничением потока,
копия ≤ 20 МБ для Telegram. Озвучка и рендер кэшируются в ``out/work``. Итог: ``ep2.mp4`` (сведено), ``ep2_tiktok.mp4``
(заливать), ``ep2_tg.mp4``, ``sheet.jpg``, ``cover.png``, ``script.md``, ``caption.txt``, ``report.json``.
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
from viewer_turn import check_cuts, log, render, run  # noqa: E402

CAPTION = ("Её зовут Сара — так решил один зритель. Она спряталась и сняла Сборщика… на плёнке у него её лицо.\n"
           "Бежать или замереть? Пиши 1, 2 или своё в комменты — самый залайканный ответ решит, что будет дальше.\n"
           "#хоррор #psx #найденнаяплёнка #выбордлязрителей #ps1")


def monster_sheet(out: Path, gl: str, tag: str) -> Path:
    from app import viewer_ep as E
    from app import viewer_ep2 as E2
    from vtubika_story import tile

    work = out / "work" / "monster"
    work.mkdir(parents=True, exist_ok=True)
    spec, marks = E2.monster_spec(E.scene_base("street"))
    sp = work / "spec.json"
    sp.write_text(json.dumps(spec, ensure_ascii=False, indent=1), "utf-8")
    res = render(sp, out, gl, ",".join(str(t) for t, _ in marks), work / (tag or "stills"))
    files = [Path(p["file"]) for p in res["previews"]]
    sheet = out / "monster_sheet.jpg"
    tile(files, sheet, cols=4, labels=[f"{t:.1f} {lab}" for t, lab in marks])
    log(f"лист Сборщика: {sheet}; яркость {[p['luma'] for p in res['previews']]}")
    return sheet


def her_live(raw: Path, words: list[dict], work: Path) -> tuple[Path, float, list[dict]]:
    """Её шёпот вживую (не плёнка): обрезать тишину по словам, убрать низ и верх, мягко сжать, чуть тише."""
    a = max(0.0, words[0]["start"] - 0.06)
    b = words[-1]["end"] + 0.25
    out = work / f"{raw.stem}_live.wav"
    dur = b - a
    run(["ffmpeg", "-y", "-v", "error", "-i", str(raw), "-af",
         f"atrim={a:.3f}:{b:.3f},asetpts=PTS-STARTPTS,highpass=f=140,lowpass=f=6500,acompressor=threshold=-24dB:ratio=3,"
         f"afade=t=out:st={max(0.0, dur - 0.12):.3f}:d=0.12", "-ac", "1", str(out)], "её шёпот")
    shifted = [{"word": w["word"], "start": round(w["start"] - a, 3), "end": round(w["end"] - a, 3)} for w in words]
    return out, dur, shifted


def hits_ep2(events: dict, glitch_times: list[float], total: float) -> list[tuple[str, Path, float, str]]:
    """Звуки серии — только записи паков: карточка, кнопка записи, сердце, шаги и щелчки Сборщика (ниже, медленнее,
    «мокрые»), гул на появлении, мотор наезда, писк батареи, хруст шеи на рывке, помехи."""
    from viewer_ep_audio import RATE, pack

    slow = lambda k, lp=0: f"aresample={RATE},asetrate={int(RATE * k)},aresample={RATE}" + (f",lowpass=f={lp}" if lp else "")  # noqa: E731
    ev = events
    out = [("chime_soft", pack("freesound/triangle.ogg"), ev["card0"] + 0.05, slow(0.84) + ",aecho=0.8:0.6:160:0.3"),
           ("tick", pack("kenney-interface/click_002.ogg"), ev["pov1"] - 0.02, ""),
           ("sub", pack("freesound/bass-drop.ogg"), ev["pov1"] - 0.1, slow(0.6, 320)),
           ("braam", pack("freesound/impact-low.ogg"), ev["pov1"] + 0.05, slow(0.7, 900))]
    # Рывок вперёд в коридоре: мокрые щелчки суставов на шагах стоп-моушена и тяжёлый шаг.
    for i, dt in enumerate((0.0, 0.14, 0.43, 0.71)):
        out.append(("wet", pack(f"kenney-impact/impactSoft_medium_00{i % 5}.ogg"), ev["lurch"] + dt, slow(1.3) + ",highpass=f=250"))
        out.append(("bone", pack(f"kenney-interface/click_00{1 + i % 5}.ogg"), ev["lurch"] + dt + 0.03, slow(0.55, 2500)))
    out.append(("stomp2", pack("kenney-impact/footstep_concrete_002.ogg"), ev["lurch"] + 0.45, slow(0.75, 1100)))
    # Пока крутится кубик: сердце (тук-тук) и неровные шаги за кадром — он идёт к лампе.
    t = ev["roll"] + 0.3
    while t < ev["land"] + 0.3:
        out += [("heart", pack("kenney-impact/impactSoft_heavy_000.ogg"), t, slow(0.7, 220)),
                ("heart", pack("kenney-impact/impactSoft_heavy_001.ogg"), t + 0.24, slow(0.66, 200))]
        t += 0.78
    for i, dt in enumerate((0.7, 1.25, 1.55, 2.3)):
        if ev["roll"] + dt < ev["pov2"] - 0.2:
            out.append(("stomp", pack(f"kenney-impact/footstep_concrete_00{(i * 3) % 5}.ogg"), ev["roll"] + dt, slow(0.75, 1000)))
    # Под лампой: гул, мотор наезда, удар на лице.
    out += [("sub", pack("freesound/bass-drop.ogg"), ev["pov2"] - 0.05, slow(0.52, 300)),
            ("riser", pack("freesound/riser.ogg"), ev["zoom0"], slow(0.7, 2200)),
            ("braam", pack("freesound/drama-boom.ogg"), ev["zoom1"] - 0.15, slow(0.72, 1100))]
    # Батарея: писк-писк; рывок головы: хруст и удар; ближе — тяжёлый шаг.
    out += [("beep", pack("kenney-interface/error_004.ogg"), ev["beep"], ""),
            ("beep", pack("kenney-interface/error_004.ogg"), ev["beep"] + 0.24, ""),
            ("bone", pack("kenney-impact/impactWood_heavy_000.ogg"), ev["snap"], slow(1.35)),
            ("wet", pack("kenney-impact/impactSoft_medium_002.ogg"), ev["snap"] + 0.05, slow(1.2)),
            ("braam", pack("freesound/impact-low.ogg"), ev["snap"] + 0.02, slow(0.66, 800)),
            ("stomp3", pack("kenney-impact/footstep_concrete_004.ogg"), ev["final"] + 0.35, slow(0.72, 1000))]
    for i, g in enumerate(glitch_times):
        out.append(("glitch", pack(f"kenney-interface/glitch_00{1 + i % 4}.ogg"), g, ""))
    return [h for h in out if 0 <= h[2] < total]


def sheet_marks(ev: dict, total: float, step: float = 2.0) -> list[tuple[float, str]]:
    """Кадр каждые ~step секунд (и 0.0 — обложка); подпись — последнее событие до кадра."""
    names = [(0.0, "обложка"), (0.04, "помехи"), (0.37, "титр"), (ev["card0"], "комментарий"), (ev["her0"], "её шёпот"),
             (ev["raise"], "за будкой"), (ev["pov1"], "видоискатель"), (ev["roll"], "бросок"), (ev["pov2"], "под лампой"),
             (ev["zoom0"], "наезд"), (ev["zoom1"], "её лицо"), (ev["eye"], "глазами"), (ev["pov3"], "видоискатель"),
             (ev["beep"], "БАТАРЕЯ"), (ev["snap"], "рывок"), (ev["tear"], "срыв"), (ev["final"], "ближе"), (ev["choice"], "меню"),
             (ev["cta"], "призыв"), (ev["static_end"], "помехи")]
    names.sort()
    marks, t = [(0.0, "обложка"), (0.17, "помехи")], 1.0
    while t < total - 0.2:
        marks.append((round(t, 2), next(n for tt, n in reversed(names) if tt <= t + 1e-6)))
        t += step
    for extra, lab in ((ev["zoom1"] + 0.2, "её лицо"), (ev["snap"] + 0.25, "рывок"), (total - 0.15, "конец")):
        marks.append((round(extra, 2), lab))
    return sorted(marks)


def static_frames(mp4: Path, n_head: int = 16, n_tail: int = 18) -> dict:
    """Сколько кадров помех в начале (после обложки) и в конце: кадр-помеха — мелкий шум по всему кадру (большая
    разница соседних пикселей) при средней яркости ~ серой."""
    import subprocess

    import numpy as np

    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(mp4), "-vf", "scale=90:160:flags=neighbor,format=gray", "-f", "rawvideo", "-"],
                         capture_output=True, check=True).stdout
    fr = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 160, 90).astype(np.float32)
    hf = np.abs(np.diff(fr, axis=2)).mean(axis=(1, 2))  # мелкий шум по горизонтали
    is_static = hf > 30.0
    head = [int(i) for i in np.nonzero(is_static[:n_head])[0]]
    tail = [int(i) for i in np.nonzero(is_static[-n_tail:])[0] + len(fr) - n_tail]
    return {"frames": int(len(fr)), "head": head, "tail": tail, "head_count": len(head), "tail_count": len(tail),
            "hf_cover": round(float(hf[0]), 1), "hf_static_min": round(float(hf[is_static].min()) if is_static.any() else 0, 1)}


def write_script(path: Path, script: list[dict], beats: list[dict], roll: dict, ev: dict, total: float) -> None:
    from app.viewer_ep import WISP_VOICE
    from app.viewer_ep2 import CHOICE, COMMENT, FINAL, HER_TEXT, HER_VOICE, HOOK, ROLL_SEED

    r = roll["result"]
    lines = ["# Серия 2 «Сара» — реплики и тайминги", "",
             f"Голос огонька: Gemini «{WISP_VOICE['voice']}» (бесплатно); её голос: Gemini «{HER_VOICE['voice']}» (бесплатно).",
             f"Длина: {total:.2f} с. Кадр 0 — обложка, кадры 1–10 — помехи, последние 12 кадров — помехи.",
             f"Комментарий: @{COMMENT['nick']} ♥{COMMENT['likes']}: «{COMMENT['text']}».",
             f"Бросок: d20 из строки «{ROLL_SEED}» → {r['die']}; {r['stat']} +{r['bonus']} = {r['total']} против {r['dc']} → "
             f"{'УСПЕХ' if r['ok'] else 'ПРОВАЛ'}.", "",
             f"- 0.0–{ev['card0']:.1f} с · титр-крючок на лице: {' / '.join(HOOK)}"]
    for s, b in zip(script, beats):
        if s["beat"] == "advice1":
            lines.append(f"- {ev['her0']:5.1f}–{ev['her1']:5.1f} с · она (шёпотом): {HER_TEXT}")
        if s["beat"] == "result":
            lines.append(f"- {ev['pov1']:5.1f}–{ev['roll']:5.1f} с · ВИДОИСКАТЕЛЬ: он в проходе на фоне света, два рывка вперёд, замер")
            lines.append(f"- {ev['roll']:5.1f}–{ev['land']:5.1f} с · её лицо в свете экранчика; окно «БРОСОК: {r['stat']}», "
                         "за кадром — неровные шаги и сердце")
        if s["beat"] == "face":
            lines.append(f"- {ev['zoom0']:5.1f}–{ev['zoom1']:5.1f} с · наезд видоискателя на голову: на маске — её лицо")
        if s["beat"] == "sound":
            lines.append(f"- {ev['beep']:5.1f} с · камера пищит, «БАТАРЕЯ» мигает; {ev['snap']:.1f} с — голова рывком к объективу; "
                         f"{ev['tear']:.1f} с — срыв в помехи")
        lines.append(f"- {b['t0']:5.1f}–{b['t1']:5.1f} с · огонёк: {s['text']}")
    lines += ["", "События: " + ", ".join(f"{k} {v:.2f}" for k, v in ev.items()),
              "Меню выбора: " + " / ".join(CHOICE[1:]), "Финальный титр: " + " / ".join(FINAL)]
    path.write_text("\n".join(lines) + "\n", "utf-8")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=Path("D:/vvf-offload/vtubika/psx/ep2"))
    ap.add_argument("--env", type=Path, default=ROOT.parent.parent.parent / ".env")
    ap.add_argument("--gl", default="gpu")
    ap.add_argument("--monster", action="store_true")
    ap.add_argument("--tts-only", action="store_true")
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
    from app import viewer_ep2 as E2

    if not a.tts_only:
        run(["npm", "run", "build:psx"], "сборка", cwd=ROOT, shell=sys.platform == "win32")
    if a.monster:
        print(monster_sheet(a.out, a.gl, a.tag))
        return 0
    roll = E2.ep2_roll()
    r = roll["result"]
    script = E2.ep2_script(roll)
    text = " ".join(s["text"] for s in script)
    log(f"бросок: {r['die']} + {r['bonus']} = {r['total']} против {r['dc']} → {'успех' if r['ok'] else 'провал'}; текст: {text}")
    her_raw, her_tts = V.narrate(E2.HER_TEXT, work, E2.HER_VOICE)
    voice, tts = V.narrate(text, work, E.WISP_VOICE)
    log(f"озвучка: огонёк {tts}, она {her_tts}")
    if a.tts_only:
        print(json.dumps({"wisp": str(voice), "her": str(her_raw), "tts": tts, "her_tts": her_tts}, ensure_ascii=False))
        return 0
    V.use_cached_whisper()
    her_words_raw = V.words_aligned(E2.HER_TEXT, her_raw, work)
    her_wav, her_len, her_words = her_live(her_raw, her_words_raw, work)
    her_env = E.envelope(V.voice_samples(her_wav), 16000, 30)
    words = V.words_aligned(text, voice, work)
    if words[0]["start"] > 2.0:
        raise SystemExit(f"речь начинается на {words[0]['start']:.1f} с — модель зачитала указание к голосу; удалите {voice.name}")
    segs, speed, beats = E2.ep2_beats(script, words, her_len)
    voice = V.paced_voice(voice, segs, work, speed)
    total = round(round((beats[-1]["t1"] + E2.TAIL) * E2.FPS) / E2.FPS, 4)
    log(f"темп ×{speed}, длина {total} с, её шёпот {her_len:.2f} с")
    env = E.envelope(V.voice_samples(voice), 16000, 30, lead=E2.LEAD)
    shifted = [{"word": w["word"], "start": w["start"], "end": w["end"]} for b in beats for w in b["words"]]
    spec = E2.build_ep2_spec(E.scene_base("street"), beats, shifted, total, env, roll, her_len, her_env, her_words)
    events = spec.pop("_events")
    spec_path = work / "spec.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=1), "utf-8")
    write_script(a.out / "script.md", script, beats, roll, events, total)
    if a.preview:
        pts = a.preview if a.preview != "marks" else ",".join(str(t) for t, _ in sheet_marks(events, total))
        res = render(spec_path, a.out, a.gl, pts, work / (f"preview_{a.tag}" if a.tag else "preview"))
        print(json.dumps({"previews": [p["file"] for p in res.get("previews", [])], "events": events, "total": total}, ensure_ascii=False))
        return 0
    silent = work / "silent.mp4"
    res: dict = {}
    fps = spec.get("fps", 30)
    if not a.skip_render:
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
    import viewer_ep_audio as A

    A.UNDER.update({"sub": 9.0, "wet": 12.0, "bone": 13.0, "heart": 11.0, "riser": 14.0})
    final = a.out / "ep2.mp4"
    glitch = sorted(set(round(g, 2) for g in spec["cuts"] if g > 0.5))
    extras = [{"name": "her", "path": her_wav, "at": events["her0"], "rel_db": -3.0}]
    mixed = A.mix(silent, voice, E2.LEAD, total, events, spec["fx"]["keys"], glitch[:9], final, work, hits_fn=hits_ep2, extras=extras)
    # Для TikTok: постоянные 30 к/с, поток ограничен (серия 1 без этого лагала), ключевой кадр раз в секунду.
    tiktok = a.out / "ep2_tiktok.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-i", str(final), "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-fps_mode", "cfr",
         "-r", "30", "-maxrate", "12M", "-bufsize", "24M", "-profile:v", "high", "-level", "4.2", "-g", "30", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(tiktok)], "файл для TikTok")
    tg = a.out / "ep2_tg.mp4"
    kbps = int((19.0 * 8 * 1000) / total) - 160  # ≤ 19 МБ: видео + 128 кбит/с звука
    run(["ffmpeg", "-y", "-v", "error", "-i", str(final), "-vf", "scale=720:-2", "-c:v", "libx264", "-preset", "slow", "-b:v",
         f"{min(2400, kbps)}k", "-maxrate", f"{min(2800, kbps + 300)}k", "-bufsize", "4000k", "-fps_mode", "cfr", "-r", "30",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(tg)], "копия для Telegram")
    from vtubika_story import tile

    marks = sheet_marks(events, total)
    frames = [V.frame_at(tiktok, t, work / f"sheet_{i:02d}.png", "scale=360:640:flags=neighbor") for i, (t, _) in enumerate(marks)]
    tile(frames, a.out / "sheet.jpg", cols=6, labels=[f"{t:.2f} {lab}" for t, lab in marks])
    V.frame_at(tiktok, 0.0, a.out / "cover.png")
    (a.out / "caption.txt").write_text(CAPTION + "\n", "utf-8")
    statics = static_frames(tiktok)
    probe = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                 "stream=r_frame_rate,avg_frame_rate,nb_frames,profile,level,bit_rate", "-of", "json", str(tiktok)], "ffprobe").stdout
    report = {"final": str(tiktok), "mixed": str(final), "tg": str(tg), "tg_mb": round(tg.stat().st_size / 1e6, 1),
              "tiktok_mb": round(tiktok.stat().st_size / 1e6, 1), "duration": total, "frames": res.get("frames"),
              "presence": res.get("presence"), "cuts": cuts, "static": statics, "tiktok_stream": json.loads(probe)["streams"][0],
              "tts": tts, "her_tts": her_tts, "mix": mixed, "events": events,
              "roll": {k: v for k, v in roll.items() if k != "heroine"}, "text": text, "her_text": E2.HER_TEXT}
    (a.out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps({k: report[k] for k in ("final", "tg", "tg_mb", "tiktok_mb", "duration", "frames", "static", "cuts")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
