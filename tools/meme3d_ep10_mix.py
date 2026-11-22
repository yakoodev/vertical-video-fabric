"""Мем-серия 10 «Сделай за ночь» — субтитры (оба голоса), надписи часов, звук из паков, музыка с приседанием, проверки и
выпуск ролика (планы, герой и камера — meme3d_ep10_anim.py; разметка — meme3d_ep10.py).

Субтитры — у обоих голосов: Клод жёлтым, юзер голубым (говорящий «B», как в серии 8). Надписи часов («23:00»…«07:00»)
— крупно на плане часов и маленькой плашкой в углу до следующего часа. Музыка приседает под речь, на «где кнопка?»
обрывается (запись «скретч»), в паузе — только тиканье часов, после панчлайна возвращается.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import meme3d_ep6_anim as A6  # noqa: E402
import meme3d_ep8_anim as A8  # noqa: E402
import meme3d_ep9_anim as A9  # noqa: E402
import meme3d_ep10 as E  # noqa: E402
import meme3d_ep10_anim as A  # noqa: E402
import meme3d_pilot as P1  # noqa: E402

FPS = A.FPS
DUR = A.DUR
SH = A.SH
AT, END = E.AT, E.END
TOP_CARD = "Будни Клода"
USER_COLOR = A8.USER_COLOR

# --------------------------------------------------------------------------- субтитры и надписи


def caption_words(lines: list[dict]) -> list[dict]:
    """Слова субтитров обоих голосов по времени; у юзера — говорящий «B» (свой цвет)."""
    out = []
    for ln in sorted(lines, key=lambda ln: ln["start"]):
        ws = P1.caption_words([ln])
        if ln["user"]:
            ws = [{**w, "s": "B"} for w in ws]
        out += ws
    return out


def hour_windows() -> list[tuple[str, float, float, float]]:
    """(время, начало, конец крупной надписи — план часов/утра, конец угловой плашки — до следующего часа)."""
    names = {E.CLOCK_23: "clock23", E.CLOCK_01: "clock01", E.CLOCK_03: "clock03", E.CLOCK_05: "clock05", E.MORNING: "morning"}
    out = []
    for i, (t, label) in enumerate(E.HOURS):
        a, b = SH[names[t]]
        nxt = SH[names[E.HOURS[i + 1][0]]][0] if i + 1 < len(E.HOURS) else DUR
        out.append((label, a, b, nxt))
    return out


HOUR_Y, CHIP = 0.2, (0.15, 0.052)


def caption_layers() -> list[dict]:
    out = [
        {"id": "hook", "type": "text", "start": 0.0, "end": SH["cover"][1], "x": 0.5, "y": 0.15,
         "props": {"text": E.HOOK, "font": "Montserrat", "weight": 900, "size": 128, "color": "#fff6e8", "align": "center", "uppercase": True,
                   "lineHeight": 1.0, "stroke": {"color": "#2b1810", "width": 16},
                   "bg": {"color": "#d8342c", "opacity": 1, "radius": 36, "padX": 44, "padY": 26}}},
        {"id": "top", "type": "text", "start": SH["cover"][1], "end": 2.4, "x": 0.5, "y": 0.105,
         "props": {"text": TOP_CARD, "font": "Montserrat", "weight": 800, "size": 46, "color": "#2b1810", "align": "center",
                   "bg": {"color": "#f6e6cf", "opacity": 1, "radius": 14, "padX": 30, "padY": 14}, "stroke": {"color": "#f6e6cf", "width": 0}},
         "out": {"type": "fade", "duration": 0.2}},
    ]
    for label, a, b, nxt in hour_windows():
        morning = label == "07:00"
        out.append({"id": f"hour_{label[:2]}", "type": "text", "start": round(a + 0.05, 3), "end": round(b, 3), "x": 0.5, "y": HOUR_Y,
                    "props": {"text": label, "font": "Montserrat", "weight": 900, "size": 150, "color": "#2b1810" if morning else "#fff6e8",
                              "align": "center", "letterSpacing": 4, "stroke": {"color": "#fff6e8" if morning else "#141a33", "width": 12}},
                    "in": {"type": "pop", "duration": 0.22}})
        out.append({"id": f"chip_{label[:2]}", "type": "text", "start": round(b, 3), "end": round(nxt, 3), "x": CHIP[0], "y": CHIP[1],
                    "props": {"text": label, "font": "Montserrat", "weight": 800, "size": 40, "color": "#fff6e8", "align": "center",
                              "bg": {"color": "#141a33" if not morning else "#d97757", "opacity": 0.85, "radius": 14, "padX": 20, "padY": 10},
                              "stroke": {"color": "#141a33", "width": 0}}})
    out.append({"id": "caps", "type": "captions", "start": 0.0, "end": DUR, "x": 0.5, "y": 0.8,
                "props": {"style": "hormozi", "size": 78, "activeColor": "#FFD23F", "uppercase": True, "maxWords": 1, "maxLines": 1,
                          "stroke": {"color": "#2b1810", "width": 11}, "speakerColors": [{"s": "B", "color": USER_COLOR}]}})
    return out


def overlay_share() -> dict:
    """Доля кадра под надписями-плашками (крючок, крупное время, угловая плашка) — по метрикам шрифта; потолок 40%."""
    from PIL import ImageFont

    font = ROOT / "vendor" / "fonts" / "Montserrat-Black.ttf"
    out = {}
    for ly in caption_layers():
        if ly["type"] != "text":
            continue
        p = ly["props"]
        f = ImageFont.truetype(str(font), int(p["size"]))
        x0, y0, x1, y1 = f.getbbox(p["text"].upper() if p.get("uppercase") else p["text"])
        bg = p.get("bg") or {}
        w = x1 - x0 + 2 * bg.get("padX", 0) + 2 * (p.get("stroke") or {}).get("width", 0)
        h = y1 - y0 + 2 * bg.get("padY", 0) + 2 * (p.get("stroke") or {}).get("width", 0)
        out[ly["id"]] = round(w * h / (1080 * 1920), 3)
    return out


def captions(video: Path, words: list[dict], out: Path, work: Path) -> None:
    from app.motion_render_comp import build_compositor_args, write_comp

    comp = write_comp(work / "comp.json", width=1080, height=1920, fps=FPS, duration=DUR, layers=caption_layers(), camera=[], words=words)
    (work / "assets.json").write_text("{}", "utf-8")
    proc = subprocess.run(build_compositor_args(comp, work / "assets.json", input_path=video, output_path=out), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=False, stdin=subprocess.DEVNULL)
    if proc.returncode != 0 or not out.is_file():
        raise RuntimeError(f"субтитры: {proc.stderr.strip()[-1200:]}")


# --------------------------------------------------------------------------- звук


def sfx_list() -> list[dict]:
    """Только настоящие записи (freesound CC0, kenney); громкость — здесь, сведение ставит их под голос."""
    fs, ki, km = P1.PACKS / "freesound", P1.PACKS / "kenney-interface", P1.PACKS / "kenney-impact"
    out = [
        {"t": 0.02, "path": fs / "pop-cartoon.ogg", "volume": 0.35},
        {"t": E.TYPE_ASK, "path": fs / "typing.ogg", "volume": 0.16, "len": E.ASK - E.TYPE_ASK},
        {"t": E.ASK, "path": fs / "pop.ogg", "volume": 0.35},
        {"t": SH["c_cu"][0] - 0.03, "path": fs / "whoosh-1.ogg", "volume": 0.25},
        {"t": AT["c1c"], "path": fs / "pop.ogg", "volume": 0.22},
        # Ночь: клавиатура в паузах и негромко под речью.
        {"t": SH["clock23"][0] + 0.15, "path": fs / "typing.ogg", "volume": 0.2, "len": 0.9},
        {"t": SH["tree1"][0], "path": fs / "typing.ogg", "volume": 0.1, "len": 1.2},
        {"t": SH["c_side"][0], "path": fs / "typing.ogg", "volume": 0.14, "len": 0.85},
        {"t": SH["clock05"][0] + 0.1, "path": fs / "typing.ogg", "volume": 0.16, "len": 0.8},
        # Сорок семь: счётчик добежал.
        {"t": E.T47, "path": fs / "pop.ogg", "volume": 0.3},
        # Утро, «где кнопка?» — музыку обрывает скретч, в паузе тикают часы.
        {"t": E.MORNING + 0.05, "path": fs / "triangle.ogg", "volume": 0.2, "len": 1.2},
        {"t": END["u2"] + 0.08, "path": fs / "record-scratch.ogg", "volume": 0.2, "len": 0.5},
        {"t": A.TEA + 0.32, "path": ki / "glass_001.ogg", "volume": 0.2, "len": 0.4},
    ]
    # Часы перескакивают: «вжух» и тиканье стрелок.
    for t, _h in E.HOURS[1:]:
        out.append({"t": round(t + 0.08, 3), "path": fs / "whip.ogg", "volume": 0.18, "len": 0.5})
        for j in range(5):
            out.append({"t": round(t + 0.12 + j * 0.09, 3), "path": ki / "tick_002.ogg", "volume": 0.26, "len": 0.1})
    # Листы документации шлёпаются на стол: мягкий удар на каждый пятый, последний — тяжелее.
    n = 30
    for i in range(0, n, 5):
        t = E.DOCS_LAND[0] + (E.DOCS_LAND[1] - E.DOCS_LAND[0]) * i / (n - 1)
        out.append({"t": round(t, 3), "path": km / f"impactSoft_medium_00{i // 5 % 5}.ogg", "volume": 0.16, "len": 0.3})
    out.append({"t": E.DOCS_LAND[1], "path": km / "impactSoft_heavy_000.ogg", "volume": 0.2, "len": 0.4})
    # Пауза после «где кнопка?»: только часы; финал — тоже.
    t = END["u2"] + 0.5
    while t < AT["c7a"] - 0.15:
        out.append({"t": round(t, 3), "path": ki / "tick_001.ogg", "volume": 0.3, "len": 0.25})
        t += 0.5
    t = END["c7b"] + 0.4
    while t < DUR - 0.2:
        out.append({"t": round(t, 3), "path": ki / "tick_001.ogg", "volume": 0.26, "len": 0.25})
        t += 1.0
    return out


MUSIC_OFF = END["u2"] + 0.1  # «где кнопка?» — музыка обрывается
MUSIC_BACK = END["c7b"] + 0.15  # после панчлайна — снова


def ducked_music(music: Path, spans: list[tuple[float, float]], gain_db: float, out: Path) -> Path:
    """Музыка ролика одним файлом: 0…MUSIC_OFF, тишина в паузе и на панчлайне, с MUSIC_BACK — с начала трека; громкость
    по A9.duck_curve (приседает под оба голоса) + ``gain_db``. Стерео 48 кГц."""
    import wave

    sr = 48000
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(music), "-ac", "2", "-ar", str(sr), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    src = np.frombuffer(pcm, dtype=np.float32).reshape(-1, 2)
    n = int(DUR * sr)

    def take(k: int) -> np.ndarray:
        reps = int(math.ceil(k / max(1, len(src))))
        return np.tile(src, (reps, 1))[:k]

    y = np.zeros((n, 2), dtype=np.float32)
    a1, b0 = int(MUSIC_OFF * sr), int(MUSIC_BACK * sr)
    y[:a1] = take(a1)
    y[b0:] = take(n - b0)
    t = np.arange(n) / sr
    edge = np.clip(t / 0.3, 0, 1)
    edge *= np.where(t < MUSIC_BACK, np.clip((MUSIC_OFF - t) / 0.12, 0, 1), np.clip((t - MUSIC_BACK) / 0.3, 0, 1))
    edge *= np.clip((DUR - t) / 0.6, 0, 1)
    curve = A9.duck_curve(spans, DUR, A9.MUSIC_GAP_LU, A9.MUSIC_SPEECH_LU)
    db = np.interp(t, np.arange(len(curve)) / 100, curve) + gain_db
    y *= (edge * 10 ** (db / 20)).astype(np.float32)[:, None]
    q = (np.clip(y, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(q.tobytes())
    return out


def mix(video: Path, voice: Path, out: Path, work: Path, report: dict) -> None:
    from app.audio_mix_model import ebur128
    from app.motion import asset_key
    from app.motion_render import build_audio_layers_args, speech_level_db
    from app.stories_3d import voice_lufs

    music = P1.MUSIC_DIR / "lofi-beat.mp3"
    if not music.is_file():
        music = P1.MUSIC_DIR / "chill-keys.mp3"
    voice_db = voice_lufs(voice)
    m_lufs = float(ebur128(music)["i"])
    gain = round(voice_db - m_lufs, 2)
    spans = [(at, at + (b - a)) for _l, _tk, a, b, at, _tx in E.LINES]
    music_file = ducked_music(music, spans, gain, work / "music_ducked.wav")
    layers = [{"id": "voice", "type": "audio", "start": 0.0, "end": DUR, "props": {"asset_id": 9001, "mode": "track", "volume": 1.0, "role": "voice"}},
              {"id": "music", "type": "audio", "start": 0.0, "end": DUR,
               "props": {"asset_id": 9002, "mode": "track", "volume": 1.0, "gain_db": 0.0, "role": "music"}}]
    assets = {asset_key(9001): str(voice), asset_key(9002): str(music_file)}
    report["music"] = {"file": music.name, "lufs": m_lufs, "voice_lufs": voice_db, "under_speech_lu": A9.MUSIC_SPEECH_LU, "in_gaps_lu": A9.MUSIC_GAP_LU,
                       "off": [round(MUSIC_OFF, 2), round(MUSIC_BACK, 2)]}
    notes: list[str] = []
    for n, s in enumerate(sfx_list()):
        if not Path(s["path"]).is_file():
            notes.append(f"нет звука {s['path']}")
            continue
        key = 9100 + n
        assets[asset_key(key)] = str(s["path"])
        end = s["t"] + float(s.get("len") or 2.5)
        layers.append({"id": f"sfx{n}", "type": "audio", "start": round(s["t"], 3), "end": round(min(DUR, end), 3),
                       "props": {"asset_id": key, "volume": s["volume"], "mode": "effect", **({"fadeOut": 0.05} if s.get("len") else {})}})
    pre = work / "mixed.mp4"
    args = build_audio_layers_args(video, pre, layers, assets, DUR, clip_has_audio=False, speech_db=speech_level_db(voice), notes=notes)
    P1._run(args, "сведение")
    P1._run(["ffmpeg", "-y", "-v", "error", "-i", str(pre), "-c:v", "copy", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "192k",
             "-ar", "48000", str(out)], "громкость")
    report["mix_notes"] = notes


# --------------------------------------------------------------------------- проверки

NUMS = {"30": ["тридцать"], "47": ["сорок", "семь"], "2": ["второй"]}


def _tokens(text: str) -> list[str]:
    """Слова → нормализованные; числа Whisper («30», «47») — словами, дефисы и тире — пробелы."""
    out = []
    for x in text.lower().replace("ё", "е").replace("—", " ").replace("-", " ").split():
        w = re.sub(r"[^а-яa-z0-9]", "", x)
        if w:
            out += NUMS.get(w, [w])
    return out


def script_words() -> list[str]:
    return [w for ln in sorted(E.LINES, key=lambda ln: ln[4]) for w in _tokens(ln[5])]


def check_words(final: Path, work: Path) -> dict:
    """faster-whisper по готовому ролику — сверка со сценарием обоих голосов (слова по порядку)."""
    import difflib

    import meme3d_voice as V

    probe = work / "final_check.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(final), "-ac", "1", "-ar", "16000", str(probe)], check=True)
    probe.with_suffix(".words.json").unlink(missing_ok=True)
    heard = [w for w in V.words(probe) if w["t0"] < END["c7b"] + 0.3 and w["t1"] - w["t0"] > 0.02]
    want = script_words()
    got = [w for h in heard for w in _tokens(h["w"])]
    sm = difflib.SequenceMatcher(a=want, b=got, autojunk=False)
    diffs = [(op, " ".join(want[i1:i2]), " ".join(got[j1:j2])) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal"]
    return {"ratio": round(sm.ratio(), 3), "script_words": len(want), "heard_words": len(got), "diffs": diffs, "text": " ".join(w["w"] for w in heard)}


def sub_sync_both(words: list[dict], work: Path) -> dict:
    """Субтитры против сказанного — отдельно по голосам (A8.sub_sync по готовому ролику)."""
    return {"claude": A8.sub_sync([w for w in words if w.get("s") != "B"], work), "user": A8.sub_sync([w for w in words if w.get("s") == "B"], work)}


def voice_provenance(work: Path) -> dict:
    """Непрерывность голосов по происхождению: каждая реплика Клода — из его одной пробы с голосом callum, каждая реплика
    юзера — из пробы liam (метаданные синтеза рядом с пробой). Тон (A9.voice_identity) у сонного юзера и Клода бывает
    одинаковым, поэтому он в отчёте — справочно, а этот разбор решает."""
    issues, per = [], {}
    for lid, take, *_r in E.LINES:
        meta = work / Path(take).with_suffix(".json").name
        voice = json.loads(meta.read_text("utf-8")).get("voice") if meta.is_file() else None
        want = E.USER_VOICE if E.is_user(lid) else "callum"
        per[lid] = voice
        if voice != want:
            issues.append(f"{lid}: голос {voice}, ждали {want}")
    takes = {take for lid, take, *_r in E.LINES if not E.is_user(lid)}
    if len(takes) != 1:
        issues.append(f"Клод из нескольких проб: {sorted(takes)}")
    return {"voices": per, "claude_takes": sorted(takes), "issues": issues}

TEXT_BANDS = {"caps": (0.2, 0.755, 0.8, 0.845), "hook": (0.08, 0.06, 0.92, 0.24), "hour": (0.25, 0.13, 0.75, 0.27),
              "chip": (0.06, 0.025, 0.24, 0.08)}


def text_over_face(samples: list[tuple[float, str, dict]]) -> list[dict]:
    """Для каждого момента с текстом — пересекает ли его полоса лицо Клода (A9.face_box)."""
    bad = []
    for t, band, insp in samples:
        fb = A9.face_box(t, insp)
        if not fb:
            continue
        bx0, by0, bx1, by1 = TEXT_BANDS[band]
        ox, oy = min(bx1, fb[2]) - max(bx0, fb[0]), min(by1, fb[3]) - max(by0, fb[1])
        if ox > 0 and oy > 0.01:
            bad.append({"t": t, "band": band, "face": [round(v, 3) for v in fb], "overlap_y": round(oy, 3)})
    return bad


def text_samples(words: list[dict]) -> list[tuple[float, str]]:
    """Моменты с текстом на кадре: середины слов, крючок обложки, крупное время, угловая плашка (середина каждого плана)."""
    out = [(0.3, "hook")] + [(round((w["t0"] + w["t1"]) / 2, 3), "caps") for w in words]
    for _label, a, b, nxt in hour_windows():
        out.append((round((a + b) / 2, 3), "hour"))
        for s0, s1, _n in A.SHOTS:
            if s0 >= b - 1e-6 and s1 <= nxt + 1e-6:
                out.append((round((s0 + s1) / 2 + 0.013, 3), "chip"))
    return out


def scene_checks(render: list[str], words: list[dict], work: Path) -> dict:
    texts = text_samples(words)
    sip_t = [round(A.TEA + i / FPS, 3) for i in range(int((DUR - A.TEA) * FPS))]
    r = P1._run([*render, "--inspect-list", ",".join(f"{t:.3f}" for t in sorted({t for t, _ in texts} | set(sip_t)))], "осмотр")
    got = {round(x["t"], 3): x for x in json.loads(r.stdout.strip().splitlines()[-1])["inspect"]}
    seen = [(t, band, got[t]) for t, band in texts if t in got]
    res = {"text_over_face": text_over_face(seen), "text_samples": len(seen),
           "text_samples_with_face": sum(A9.face_box(t, s) is not None for t, _b, s in seen),
           "hands": A9.hand_checks([got[t] for t in sip_t if t in got], A.SIP_UP, shots=A.SHOTS)}
    (work / "checks.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), "utf-8")
    return res


# --------------------------------------------------------------------------- выпуск


def release(render: list[str], work: Path, out_dir: Path, name: str, words: list[dict], voice: Path, *, skip_render: bool = False) -> int:
    report: dict = {}
    raw = work / "scene.mp4"
    if not skip_render:
        A.wait_commit(4.0)
        t0 = time.time()
        r = P1._run([*render, "--out", str(raw), "--crf", "14"], "рендер")
        report["render"] = json.loads(r.stdout.strip().splitlines()[-1])
        report["render_sec"] = round(time.time() - t0, 1)
    capped = work / "caps.mp4"
    captions(raw, words, capped, work)
    mixed = work / "final_mix.mp4"
    mix(capped, voice, mixed, work, report)
    final, tg = out_dir / f"{name}.mp4", out_dir / f"{name}_tg.mp4"
    A9.encode(mixed, final, tg, work, dur=DUR)
    times = [round(a0 + (b0 - a0) / 2, 2) for a0, b0, _ in A.SHOTS]
    P1.frame_sheet(final, times, [f"{t:.1f}s {n}" for t, (_, _, n) in zip(times, A.SHOTS)], out_dir / "contact_sheet.jpg", cols=8)
    x = P1.decode(voice)
    report.update({"video": str(final), "tg": str(tg), "size_mb": round(final.stat().st_size / 2**20, 2), "tg_mb": round(tg.stat().st_size / 2**20, 2),
                   "duration": DUR, "shots": len(A.SHOTS), "caption_check": str(A6.caption_check(final, words, out_dir)),
                   "whisper": check_words(final, work), "voice_joins": A9.voice_joins(x, lines=E.LINES),
                   "voice_identity_f0": A9.voice_identity(x, lines=E.LINES), "voice_provenance": voice_provenance(work),
                   "overlay_share": overlay_share()})
    report["sub_sync"] = sub_sync_both(words, work)
    checks = work / "checks.json"
    if checks.is_file():
        report["scene_checks"] = json.loads(checks.read_text("utf-8"))
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1)[:6000])
    return 0
