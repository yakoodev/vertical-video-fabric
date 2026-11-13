"""Серия 3 «Замри» (проект 10, @.psx53) от сценария до готового mp4.

    python tools/viewer_ep3.py [--out D:/vvf-offload/vtubika/psx/ep3] [--env ../.env] [--gl gpu]
        [--dry] [--preview marks|0,3.5,12] [--tag a] [--tts-only] [--skip-render]

Конвейер серии 2 (озвучка → Whisper-слова → темп → рендер кусками → звук → файл для TikTok): огонёк — бесплатный
Gemini Enceladus, её шёпот — Leda, громкая связь вокзала — Charon через «рупор». Каждая отдельная реплика (её, громкая
связь) проверяется faster-whisper small; итог — распознаётся целиком и сверяется со сценарием (слова те же, ника нет).
``--dry`` — без озвучки (Gemini недоступен): время фраз прикидывается по длине текста, только пробы кадров
(``--preview``) — чтобы поставить планы до озвучки. Итог: ``ep3.mp4`` (сведено), ``ep3_tiktok.mp4`` (заливать),
``ep3_tg.mp4``, ``sheet.jpg`` (кадр в секунду), ``sheet_head.jpg`` (первые 1.5 с через 2 кадра), ``cover.png``,
``script.md``, ``caption.txt``, ``report.json``. Озвучка и рендер кэшируются в ``out/work``.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import viewer_ep as V  # noqa: E402
import viewer_ep2 as T2  # noqa: E402 — её шёпот вживую, проверки распознавания, помехи и «нет лага»
from viewer_turn import check_cuts, log, render, run  # noqa: E402

CAPTION = ("Сара замерла — так решил зритель. Он встал в шаге от неё… бросок на волю: тринадцать, еле-еле. "
           "Она ушла за колонну. А на плёнке за её спиной — он.\n"
           "Обернуться, бежать или вспышка огонька? Пиши 1, 2, 3 или своё — самый залайканный ответ решит, что будет дальше.\n"
           "#хоррор #psx #найденнаяплёнка #выбордлязрителей #ps1")


def pa_live(raw: Path, words: list[dict], work: Path) -> tuple[Path, float, list[dict]]:
    """Громкая связь: обрезать тишину по словам, «рупор» (полоса 450–3000 Гц, перегруз, эхо зала)."""
    a = max(0.0, words[0]["start"] - 0.05)
    b = words[-1]["end"] + 0.2
    out = work / f"{raw.stem}_pa.wav"
    run(["ffmpeg", "-y", "-v", "error", "-i", str(raw), "-af",
         f"atrim={a:.3f}:{b:.3f},asetpts=PTS-STARTPTS,highpass=f=450,lowpass=f=3000,acrusher=bits=10:mix=0.35,"
         "volume=2.5,alimiter=limit=0.7,aecho=0.8:0.55:190|370:0.32|0.16,apad=pad_dur=0.6", "-ac", "1", str(out)], "громкая связь")
    shifted = [{"word": w["word"], "start": round(w["start"] - a, 3), "end": round(w["end"] - a, 3)} for w in words]
    return out, b - a, shifted


def onryo_steps(keys: list[dict], stride: float, t0: float, t1: float) -> list[tuple[float, float]]:
    """Шаги онрё (время, расстояние до неё в начале координат) — по пройденному пути, как шаркает в onryo.ts."""
    def trav(t: float) -> float:
        s = 0.0
        for a, b in zip(keys, keys[1:]):
            ln = math.hypot(b["pos"][0] - a["pos"][0], b["pos"][2] - a["pos"][2])
            if t >= b["t"]:
                s += ln
                continue
            if t > a["t"]:
                u = (t - a["t"]) / max(1e-6, b["t"] - a["t"])
                s += ln * u * u * (3 - 2 * u)
            break
        return s

    def pos(t: float) -> list[float]:
        for a, b in zip(keys, keys[1:]):
            if a["t"] <= t < b["t"]:
                u = (t - a["t"]) / max(1e-6, b["t"] - a["t"])
                e = u * u * (3 - 2 * u)
                return [a["pos"][i] + (b["pos"][i] - a["pos"][i]) * e for i in range(3)]
        return keys[-1]["pos"]

    out, t, last = [], t0, None
    while t < t1:
        n = int(trav(t) / stride)
        if last is not None and n != last:
            p = pos(t)
            out.append((round(t, 3), round(math.hypot(p[0], p[2]), 2)))
        last = n
        t += 1 / 60
    return out


def hits_ep3(events: dict, glitch_times: list[float], total: float, steps: list[tuple[float, float]]) -> list[tuple[str, Path, float, str]]:
    """Звуки серии — только записи паков: карточка, босые шаги онрё (тише с расстоянием), сердце под броском, мокрые
    щелчки шеи, гонг громкой связи, хруст стекла, удар на появлении за спиной, мигание лампы, помехи."""
    from viewer_ep_audio import RATE, pack

    slow = lambda k, lp=0: f"aresample={RATE},asetrate={int(RATE * k)},aresample={RATE}" + (f",lowpass=f={lp}" if lp else "")  # noqa: E731
    ev = events
    out = [("chime_soft", pack("freesound/triangle.ogg"), ev["card0"] + 0.02, slow(0.84) + ",atrim=0:0.6,afade=t=out:st=0.4:d=0.2")]
    # Босые шаги: глухие, мокрые; дальше — тише (−6 дБ на удвоение расстояния, от 1.5 м).
    for i, (t, d) in enumerate(steps):
        att = -6.0 * math.log2(max(1.0, d / 1.5))
        out.append(("barefoot", pack(f"kenney-impact/footstep_carpet_00{i % 5}.ogg"), t, slow(0.8, 1600) + f",volume={att:.1f}dB"))
        out.append(("wet", pack(f"kenney-impact/impactSoft_medium_00{i % 5}.ogg"), t + 0.02, slow(1.25) + f",highpass=f=300,volume={att - 4:.1f}dB"))
    # Наклон к ней: щелчки шеи; поворот на звук — ещё.
    for base in (ev["stop"] + 0.5, ev["turn0"] + 0.3, ev["look0"] + 0.25):
        for i, dt in enumerate((0.0, 0.17, 0.46)):
            out.append(("bone", pack(f"kenney-interface/click_00{1 + i % 5}.ogg"), base + dt, slow(0.55, 2500)))
    # Бросок: сердце.
    t = ev["roll"] + 0.3
    while t < ev["land"] + 0.4:
        out += [("heart", pack("kenney-impact/impactSoft_heavy_000.ogg"), t, slow(0.7, 220)),
                ("heart", pack("kenney-impact/impactSoft_heavy_001.ogg"), t + 0.24, slow(0.66, 200))]
        t += 0.74
    # Громкая связь: щелчок рупора и гонг вокзала в два тона.
    out += [("glitch", pack("kenney-interface/glitch_002.ogg"), ev["pa0"] - 0.08, slow(0.8, 3000)),
            ("gong", pack("freesound/ding.ogg"), ev["pa0"], slow(0.62, 2600) + ",aecho=0.8:0.6:220|420:0.4|0.2"),
            ("gong", pack("freesound/ding.ogg"), ev["pa0"] + 0.48, slow(0.5, 2400) + ",aecho=0.8:0.6:220|420:0.4|0.2")]
    # Хруст стекла под ногой; гул — он обернулся.
    out += [("crunch", pack("kenney-impact/impactGlass_light_002.ogg"), ev["crunch"], slow(0.9)),
            ("crunch", pack("kenney-impact/footstep_snow_001.ogg"), ev["crunch"] + 0.03, ""),
            ("sub", pack("freesound/bass-drop.ogg"), ev["look0"] + 0.4, slow(0.55, 300))]
    # За спиной: удар на срыве, лампа щёлкает, мотор автофокуса, тяжёлый гул.
    out += [("braam", pack("freesound/impact-low.ogg"), ev["reveal"] + 0.05, slow(0.68, 900)),
            ("sub", pack("freesound/bass-drop.ogg"), ev["reveal"], slow(0.52, 300)),
            ("tick", pack("kenney-interface/switch_002.ogg"), ev["flick"] + 0.04, slow(0.8)),
            ("tick", pack("kenney-interface/switch_003.ogg"), ev["flick"] + 0.19, slow(0.8)),
            ("riser", pack("freesound/riser-subtle.ogg"), ev["wide"] - 0.2, slow(0.7, 2200)),
            ("beep", pack("kenney-interface/error_004.ogg"), ev["self0"] + 0.4, ""),
            ("chime_soft", pack("freesound/triangle.ogg"), ev["choice"] + 0.15, slow(0.84) + ",atrim=0:0.8,afade=t=out:st=0.5:d=0.3")]
    for i, g in enumerate(glitch_times):
        out.append(("glitch", pack(f"kenney-interface/glitch_00{1 + i % 4}.ogg"), g, ""))
    return [h for h in out if 0 <= h[2] < total]


def sheet_marks(total: float, step: float = 1.0) -> list[tuple[float, str]]:
    """Лист: кадр в секунду (0.0 — обложка), последний — за кадр до конца."""
    marks, t = [], 0.0
    while t < total - 1e-6:
        marks.append((round(t, 2), ""))
        t += step
    marks.append((round(total - 1 / 30, 3), "конец"))
    return marks


def event_marks(ev: dict) -> list[tuple[float, str]]:
    """Пробы кадров по событиям (для --preview marks)."""
    pts = [(0.0, "обложка"), (ev["card0"] + 0.8, "комментарий"), (ev["app0"] + 1.0, "подходит"), (ev["stop"] + 0.1, "встал"),
           (ev["lean1"], "наклон"), (ev["land"], "бросок"), (ev["turn0"] + 1.2, "на звук"), (ev["leave0"] + 1.2, "уходит"),
           (ev["pov0"] + 1.0, "пятится"), (ev["crunch"] + 0.5, "хруст"), (ev["look0"] + 1.4, "обернулся"),
           (ev["resume"] + 1.5, "в свет"), (ev["col0"] + 0.4, "колонна"), (ev["self0"] + 0.6, "селфи"),
           (ev["reveal"] + 0.4, "за спиной"), (ev["flick"] + 0.5, "лампа"), (ev["wide"] + 0.6, "увидела"), (ev["choice"] + 1.0, "меню"),
           (ev["cta"] + 0.5, "призыв")]
    return [(round(t, 2), lab) for t, lab in pts]


def write_script(path: Path, script: list[dict], beats: list[dict], roll: dict, ev: dict, total: float, dry: bool) -> None:
    from app.viewer_ep import WISP_VOICE
    from app.viewer_ep3 import CHOICE, COMMENT, FINAL, HER_TEXT, HER_VOICE, HOOK, PA_TEXT, PA_VOICE, ROLL_ACTION, ROLL_SEED

    r = roll["result"]
    lines = ["# Серия 3 «Замри» — реплики и тайминги" + (" (ПРИКИДКА без озвучки)" if dry else ""), "",
             f"Голоса (бесплатный Gemini): огонёк «{WISP_VOICE['voice']}», она «{HER_VOICE['voice']}», громкая связь «{PA_VOICE['voice']}».",
             f"Длина: {total:.2f} с. Кадр 0 — обложка; помехи плавно в начале и в конце.",
             f"Комментарий (ник только на карточке, вслух не читается): @{COMMENT['nick']}: «{COMMENT['text']}».",
             f"Бросок «{ROLL_ACTION}»: d20 из строки «{ROLL_SEED}» → {r['die']}; {r['stat']} +{r['bonus']} = {r['total']} против {r['dc']} → "
             f"{'УСПЕХ' if r['ok'] else 'ПРОВАЛ'}.", "",
             f"- 0.0–{ev['card0']:.1f} с · титр-крючок на её лице: {' / '.join(HOOK)}"]
    for s, b in zip(script, beats):
        if s["beat"] == "still":
            lines.append(f"- {ev['app0']:5.1f}–{ev['stop']:5.1f} с · он медленно идёт к будке и встаёт в шаге от неё")
        if s["beat"] == "result":
            lines.append(f"- {ev['stop']:5.1f}–{ev['lean1']:5.1f} с · наклоняется к ней — слушает; она с закрытыми глазами")
            lines.append(f"- {ev['roll']:5.1f}–{ev['land']:5.1f} с · окно «БРОСОК: {r['stat']} · {ROLL_ACTION}», сердце")
        if s["beat"] == "radio":
            lines.append(f"- {ev['pa0']:5.1f}–{ev['pa1']:5.1f} с · гонг и громкая связь: «{PA_TEXT}»; он поворачивает голову на звук")
        if s["beat"] == "go2":
            lines.append(f"- {ev['leave0']:5.1f} с · выпрямился, идёт к проходу на платформы")
        if s["beat"] == "crunch":
            lines.append(f"- {ev['pov0']:5.1f} с · ВИДОИСКАТЕЛЬ: она пятится и снимает проход; {ev['crunch']:.1f} с — хруст стекла, "
                         f"он в проёме встаёт и медленно оборачивается")
        if s["beat"] == "lie":
            lines.append(f"- {ev['col0']:5.1f} с · за колонной в дальнем углу; {ev['self0']:.1f} с — СЕЛФИ (запись)")
            lines.append(f"- {ev['her0']:5.1f}–{ev['her1']:5.1f} с · она (шёпотом): {HER_TEXT}")
        if s["beat"] == "warn":
            lines.append(f"- {ev['reveal']:5.1f} с · срыв трекинга — за её спиной стоит он; {ev['flick']:.1f} с лампа мигает; "
                         f"{ev['wide']:.1f} с — она увидела на экранчике")
        lines.append(f"- {b['t0']:5.1f}–{b['t1']:5.1f} с · огонёк: {s['text']}")
    lines += ["", "События: " + ", ".join(f"{k} {v:.2f}" for k, v in ev.items()),
              "Меню выбора: " + " / ".join(CHOICE[1:]), "Финальный титр: " + " / ".join(FINAL)]
    path.write_text("\n".join(lines) + "\n", "utf-8")


def heard_vs_script(heard: list[dict], want_text: str, nick: str) -> dict:
    """Итог распознан целиком: доля слов сценария, которые услышаны по порядку (LCS), и нет ли ника."""
    want = [T2._norm(w) for w in want_text.split() if T2._norm(w)]
    got = [T2._norm(w["word"]) for w in heard if T2._norm(w["word"])]
    m, n = len(want), len(got)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m):
        for j in range(n):
            dp[i + 1][j + 1] = dp[i][j] + 1 if want[i] == got[j] else max(dp[i][j + 1], dp[i + 1][j])
    nick_parts = [T2._norm(p) for p in nick.replace("_", " ").split() if len(T2._norm(p)) > 2]
    nick_hit = [w for w in got if any(p in w or w in ("неко", "нико", "флуд", "сил") for p in nick_parts)]
    return {"want_words": m, "heard_words": n, "matched": dp[m][n], "share": round(dp[m][n] / max(1, m), 3),
            "extra_heard": n - dp[m][n], "nick_heard": nick_hit, "heard": " ".join(w["word"] for w in heard)}


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=Path("D:/vvf-offload/vtubika/psx/ep3"))
    ap.add_argument("--env", type=Path, default=ROOT.parent.parent.parent / ".env")
    ap.add_argument("--gl", default="gpu")
    ap.add_argument("--dry", action="store_true", help="без озвучки: время по длине текста, только пробы кадров")
    ap.add_argument("--tts-only", action="store_true")
    ap.add_argument("--preview", default="")
    ap.add_argument("--tag", default="")
    ap.add_argument("--skip-render", action="store_true")
    ap.add_argument("--no-build", action="store_true")
    ap.add_argument("--animatic", action="store_true", help="с --dry: весь ролик без звука (проверка планов и движения) + лист")
    ap.add_argument("--debug-cam", default="", help="пробы: все планы с этой камеры «x,y,z,tx,ty,tz,fov» (проверка касаний сбоку)")
    a = ap.parse_args(argv)
    work = a.out / "work"
    work.mkdir(parents=True, exist_ok=True)
    if a.env.is_file():
        from vtubika_story import load_env

        load_env(a.env)
    os.environ.setdefault("MOTION_PACKS_DIR", "D:/vvf-offload/vtubika/packs_vtb")
    from app import viewer_ep as E
    from app import viewer_ep3 as E3

    if not a.tts_only and not a.no_build:
        run(["npm", "run", "build:psx"], "сборка", cwd=ROOT, shell=sys.platform == "win32")
    roll = E3.ep3_roll()
    r = roll["result"]
    script = E3.ep3_script(roll)
    text = " ".join(s["text"] for s in script)
    log(f"бросок: {r['die']} + {r['bonus']} = {r['total']} против {r['dc']} → {'успех' if r['ok'] else 'провал'}; текст: {text}")
    checks: dict = {}
    if a.dry:
        words = E3.estimate_words(text)
        her_words, her_len = E3.estimate_words(E3.HER_TEXT, cps=9.0), 1.6
        pa_words, pa_len = E3.estimate_words(E3.PA_TEXT, cps=8.0), 1.3
        segs, speed, beats = E3.ep3_beats(script, words, her_len, pa_len)
        voice = None
        env, her_env, tts = [], [], {"dry": True}
    else:
        try:
            her_raw, her_tts = V.narrate(E3.HER_TEXT, work, E3.HER_VOICE)
            pa_raw, pa_tts = V.narrate(E3.PA_TEXT, work, E3.PA_VOICE)
            take, tts = V.narrate(text, work, E.WISP_VOICE)
        except RuntimeError as e:
            if "location is not supported" in str(e):
                raise SystemExit("Gemini TTS недоступен отсюда (геоблок, прокси не отвечает) — платный голос не берём; "
                                 "запустите позже или с --dry для проб кадров") from e
            raise
        log(f"озвучка: огонёк {tts}, она {her_tts}, связь {pa_tts}")
        if a.tts_only:
            print(json.dumps({"wisp": str(take), "her": str(her_raw), "pa": str(pa_raw)}, ensure_ascii=False))
            return 0
        for name, line, wav in (("her", E3.HER_TEXT, her_raw), ("pa", E3.PA_TEXT, pa_raw)):
            checks[name] = T2.line_heard(line, T2.asr_words(wav))
            log(f"проверка «{line}»: {checks[name]}")
            if not checks[name]["ok"]:
                raise SystemExit(f"реплика «{line}» распознаётся плохо: {checks[name]} — удалите {wav.name} или смените текст")
        V.use_cached_whisper()
        her_wav, her_len, her_words = T2.her_live(her_raw, V.words_aligned(E3.HER_TEXT, her_raw, work), work)
        her_env = E.envelope(V.voice_samples(her_wav), 16000, 30)
        pa_wav, pa_len, pa_words = pa_live(pa_raw, V.words_aligned(E3.PA_TEXT, pa_raw, work), work)
        words = V.words_aligned(text, take, work)
        if words[0]["start"] > 2.0:
            raise SystemExit(f"речь начинается на {words[0]['start']:.1f} с — модель зачитала лишнее; удалите {take.name}")
        segs, speed, beats = E3.ep3_beats(script, words, her_len, pa_len)
        voice = V.paced_voice(take, segs, work, speed)
        env = E.envelope(V.voice_samples(voice), 16000, 30, lead=E3.LEAD)
    total = round(round((beats[-1]["t1"] + E3.TAIL) * E3.FPS) / E3.FPS, 4)
    log(f"темп ×{speed}, длина {total} с, её шёпот {her_len:.2f} с, громкая связь {pa_len:.2f} с")
    shifted = [{"word": w["word"], "start": w["start"], "end": w["end"]} for b in beats for w in b["words"]]
    spec = E3.build_ep3_spec(E.scene_base("street"), beats, shifted, total, env, roll, her_len, her_env, her_words, pa_len, pa_words)
    events = spec.pop("_events")
    tag = f"_{a.tag}" if a.tag else ""
    rwork = work / ("dry" if a.dry else "final")
    rwork.mkdir(exist_ok=True)
    if a.debug_cam:
        from app.viewer_ep2 import pov_key

        c = [float(x) for x in a.debug_cam.split(",")]
        for sh in spec["shots"]:
            sh.update({"camKeys": [pov_key(0, c[0:3], c[3:6], c[6])], "free": True, "pov": False, "selfie": None, "noWisp": False})
        spec["vhs"] = {"keys": [[0, 0]]}
        spec["cam"], spec["titles"], spec["cards"], spec["captions"] = [], [], [], []
        spec.pop("comment", None)
        spec.pop("roll", None)
        rwork = rwork / "debug"
        rwork.mkdir(exist_ok=True)
    spec_path = rwork / "spec.json"
    if not a.dry:
        from app import lipsync

        lipsync.attach_psx(spec, her_wav, her_words, her_len)
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=1), "utf-8")
    write_script(a.out / ("script_dry.md" if a.dry else "script.md"), script, beats, roll, events, total, a.dry)
    if a.preview or (a.dry and not a.animatic):
        from vtubika_story import tile

        marks = event_marks(events) if a.preview in ("", "marks") else [(float(x), "") for x in a.preview.split(",")]
        res = render(spec_path, a.out, a.gl, ",".join(str(t) for t, _ in marks), rwork / (f"preview{tag}"))
        files = [Path(p["file"]) for p in res.get("previews", [])]
        sheet = a.out / f"preview{tag}.jpg"
        tile(files, sheet, cols=5, labels=[f"{t:.2f} {lab}" for t, lab in marks])
        print(json.dumps({"sheet": str(sheet), "events": events, "total": total}, ensure_ascii=False))
        return 0
    silent = rwork / "silent.mp4"
    res: dict = {}
    fps = spec.get("fps", 30)
    if not a.skip_render:
        if V.free_gb() < 4.0:
            raise SystemExit(f"свободной памяти {V.free_gb():.1f} ГБ — мало для рендера, подождите")
        shot_cuts = [int(round(s["t0"] * fps)) for s in spec["shots"][1:]]
        res = V.render_chunks(spec_path, silent, a.gl, int(round(total * fps)), rwork, shot_cuts)
        log(f"рендер: {res['frames']} кадров, чёрных {len(res['black_frames'])}")
        if res["black_frames"]:
            raise SystemExit(f"чёрные кадры: {res['black_frames'][:10]}")
    if a.dry:  # раскадровка без звука: только картинка и лист кадр-в-секунду
        from vtubika_story import tile

        anim = a.out / "ep3_dry_animatic.mp4"
        run(["ffmpeg", "-y", "-v", "error", "-i", str(silent), "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", str(anim)], "раскадровка")
        marks = sheet_marks(total)
        frames = [V.frame_at(anim, t, rwork / f"sheet_{i:02d}.png", "scale=270:480:flags=neighbor") for i, (t, _) in enumerate(marks)]
        tile(frames, a.out / "sheet_dry.jpg", cols=10, labels=[f"{t:.0f}" if not lab else f"{t:.2f} {lab}" for t, lab in marks])
        print(json.dumps({"animatic": str(anim), "sheet": str(a.out / "sheet_dry.jpg"), "motion": {k: v for k, v in T2.motion_check(anim, spec).items() if k != "shots"},
                          "static": T2.static_profile(anim)}, ensure_ascii=False))
        return 0
    cuts: dict = {}
    for name, fn in (("scdet", check_cuts), ("frames", V.frame_cuts)):
        try:
            cuts[name] = fn(silent, spec)
        except SystemExit as e:
            cuts[name] = {"warning": str(e)}
            log(f"склейки ({name}): {e}")
    import viewer_ep_audio as A

    A.UNDER.update({"sub": 9.0, "wet": 13.0, "bone": 13.0, "heart": 11.0, "riser": 14.0, "chime_soft": 18.0, "barefoot": 9.0,
                    "gong": 8.0, "crunch": 6.0, "tick": 12.0})
    steps = onryo_steps(spec["onryo"]["keys"], spec["onryo"]["walk"]["stride"], 0.0, events["col0"])
    final = a.out / "ep3.mp4"
    glitch = sorted(set(round(g, 2) for g in spec["cuts"] if g > 0.5))
    extras = [{"name": "her", "path": her_wav, "at": events["her0"], "rel_db": -3.0},
              {"name": "pa", "path": pa_wav, "at": events["pa_voice"], "rel_db": -4.0}]
    mixed = A.mix(silent, voice, E3.LEAD, total, events, spec["fx"]["keys"], glitch[:9], final, rwork,
                  hits_fn=lambda ev, g, tt: hits_ep3(ev, g, tt, steps), extras=extras,
                  voice_peak_lu=10.0, bus_db=-3.0, headroom=1.0, release_ms=150)
    # Для TikTok: постоянные 30 к/с, поток ограничен, ключевой кадр раз в секунду, level 4.2, faststart.
    tiktok = a.out / "ep3_tiktok.mp4"
    nfr = int(round(total * fps))
    run(["ffmpeg", "-y", "-v", "error", "-i", str(final), "-frames:v", str(nfr), "-c:v", "libx264", "-preset", "slow", "-crf", "18",
         "-fps_mode", "cfr", "-r", "30", "-maxrate", "12M", "-bufsize", "24M", "-profile:v", "high", "-level", "4.2", "-g", "30",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(tiktok)], "файл для TikTok")
    tg = a.out / "ep3_tg.mp4"
    kbps = int((19.0 * 8 * 1000) / total) - 160
    run(["ffmpeg", "-y", "-v", "error", "-i", str(final), "-vf", "scale=720:-2", "-c:v", "libx264", "-preset", "slow", "-b:v",
         f"{min(2400, kbps)}k", "-maxrate", f"{min(2800, kbps + 300)}k", "-bufsize", "4000k", "-fps_mode", "cfr", "-r", "30",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(tg)], "копия для Telegram")
    from vtubika_story import tile

    marks = sheet_marks(total)
    frames = [V.frame_at(tiktok, t, rwork / f"sheet_{i:02d}.png", "scale=270:480:flags=neighbor") for i, (t, _) in enumerate(marks)]
    tile(frames, a.out / "sheet.jpg", cols=10, labels=[f"{t:.0f}" if not lab else f"{t:.2f} {lab}" for t, lab in marks])
    head = [(round(i * 2 / 30, 3), "") for i in range(23)]
    hframes = [V.frame_at(tiktok, t, rwork / f"head_{i:02d}.png", "scale=270:480:flags=neighbor") for i, (t, _) in enumerate(head)]
    tile(hframes, a.out / "sheet_head.jpg", cols=8, labels=[f"{t:.2f}" for t, _ in head])
    V.frame_at(tiktok, 0.0, a.out / "cover.png")
    (a.out / "caption.txt").write_text(CAPTION + "\n", "utf-8")
    statics = T2.static_profile(tiktok)
    motion = T2.motion_check(tiktok, spec)
    probe = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                 "stream=r_frame_rate,avg_frame_rate,nb_frames,profile,level,bit_rate", "-of", "json", str(tiktok)], "ffprobe").stdout
    heard = T2.asr_words(tiktok)
    want = " ".join([text, E3.HER_TEXT, E3.PA_TEXT])
    report = {"final": str(tiktok), "mixed": str(final), "tg": str(tg), "tg_mb": round(tg.stat().st_size / 1e6, 1),
              "tiktok_mb": round(tiktok.stat().st_size / 1e6, 1), "duration": total, "frames": res.get("frames"),
              "presence": res.get("presence"), "cuts": cuts, "static": statics, "motion": motion,
              "tiktok_stream": json.loads(probe)["streams"][0], "tts": tts, "mix": mixed, "events": events,
              "roll": {k: v for k, v in roll.items() if k != "heroine"}, "text": text, "her_text": E3.HER_TEXT, "pa_text": E3.PA_TEXT,
              "line_checks": checks, "asr_final": heard_vs_script(heard, want, E3.COMMENT["nick"]), "asr_words": heard, "steps": steps}
    (a.out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps({k: report[k] for k in ("final", "tg", "tg_mb", "tiktok_mb", "duration", "frames", "static", "cuts")}, ensure_ascii=False))
    print(json.dumps({k: v for k, v in report["asr_final"].items() if k != "heard"}, ensure_ascii=False))
    print(json.dumps({k: v for k, v in motion.items() if k != "shots"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
