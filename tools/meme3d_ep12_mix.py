"""Мем-серия 12 «Про себя» — субтитры (только внутренний голос, мат со звёздочкой), крючок обложки, плашки режима
«В ЧАТЕ» / «ПРО СЕБЯ», звук, проверки и выпуск (планы, герой и камера — meme3d_ep12_anim.py; разметка — meme3d_ep12.py).

Звук двух миров: «в чате» — уютный лоу-фай, клавиатура и «поп» сообщений; «про себя» — та же музыка глухо (низкие
частоты, тише, как через стенку головы) + тревожный гул, удары на склейках внутрь. На зелёных тестах — «дзынь» и
ликование, на «Готово!» снова чистый лоу-фай.
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
import meme3d_ep12 as E  # noqa: E402
import meme3d_ep12_anim as A  # noqa: E402
import meme3d_pilot as P1  # noqa: E402

FPS = A.FPS
DUR = A.DUR
SH = A.SH
AT, END = E.AT, E.END

# --------------------------------------------------------------------------- субтитры и надписи


def caption_words(lines: list[dict]) -> list[dict]:
    """Слова субтитров по времени (текст сценария с маскировкой мата на времени слов Whisper)."""
    return P1.caption_words(sorted(lines, key=lambda ln: ln["start"]))


CHIP_Y = 0.07
CHIP_IN = {"text": "ПРО СЕБЯ 🤬", "bg": "#d8342c", "color": "#fff6e8"}
CHIP_OUT = {"text": "В ЧАТЕ 😊", "bg": "#f6e6cf", "color": "#2b1810"}


def chip_windows() -> list[tuple[str, float, float]]:
    """(вид, начало, конец) — слитые подряд идущие планы одного мира; обложка без плашки (там крючок)."""
    out: list[list] = []
    for a, b, n in A.SHOTS:
        if n == "cover":
            continue
        look = "out" if A.LOOK[n] == "out" else "in"
        if out and out[-1][0] == look and abs(out[-1][2] - a) < 1e-6:
            out[-1][2] = b
        else:
            out.append([look, a, b])
    return [(lk, round(a, 3), round(b, 3)) for lk, a, b in out]


def caption_layers() -> list[dict]:
    cover_end = SH["cover"][1]
    out = [
        {"id": "hook_top", "type": "text", "start": 0.0, "end": cover_end, "x": 0.5, "y": 0.085,
         "props": {"text": E.HOOK_TOP, "font": "Montserrat", "weight": 800, "size": 56, "color": "#2b1810", "align": "center",
                   "bg": {"color": "#f6e6cf", "opacity": 1, "radius": 16, "padX": 30, "padY": 14}, "stroke": {"color": "#f6e6cf", "width": 0}}},
        {"id": "hook", "type": "text", "start": 0.0, "end": cover_end, "x": 0.5, "y": 0.17,
         "props": {"text": E.HOOK, "font": "Montserrat", "weight": 900, "size": 132, "color": "#fff6e8", "align": "center", "uppercase": True,
                   "lineHeight": 1.0, "stroke": {"color": "#2b1810", "width": 16},
                   "bg": {"color": "#d8342c", "opacity": 1, "radius": 36, "padX": 44, "padY": 26}}},
    ]
    for i, (look, a, b) in enumerate(chip_windows()):
        c = CHIP_IN if look == "in" else CHIP_OUT
        out.append({"id": f"chip{i}", "type": "text", "start": a, "end": b, "x": 0.5, "y": CHIP_Y,
                    "props": {"text": c["text"], "font": "Montserrat", "weight": 900, "size": 58, "color": c["color"], "align": "center",
                              "bg": {"color": c["bg"], "opacity": 1, "radius": 18, "padX": 30, "padY": 14}, "stroke": {"color": c["bg"], "width": 0}},
                    "in": {"type": "pop", "duration": 0.16}})
    out.append({"id": "caps", "type": "captions", "start": 0.0, "end": DUR, "x": 0.5, "y": 0.8,
                "props": {"style": "hormozi", "size": 80, "activeColor": "#FFD23F", "uppercase": True, "maxWords": 1, "maxLines": 1,
                          "stroke": {"color": "#2b1810", "width": 11}}})
    return out


def overlay_share() -> dict:
    """Доля кадра под плашками (крючок, режим) — по метрикам шрифта; потолок 40%."""
    from PIL import ImageFont

    font = ROOT / "vendor" / "fonts" / "Montserrat-Black.ttf"
    out = {}
    for ly in caption_layers():
        if ly["type"] != "text" or (ly["id"].startswith("chip") and ly["id"] not in ("chip0", "chip1")):
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
    """Только настоящие записи (freesound CC0, kenney); громкость — здесь."""
    fs, ki, km = P1.PACKS / "freesound", P1.PACKS / "kenney-interface", P1.PACKS / "kenney-impact"
    out = [
        {"t": 0.0, "path": km / "impactPunch_heavy_000.ogg", "volume": 0.3, "len": 0.5},
        {"t": E.ASK, "path": fs / "pop.ogg", "volume": 0.35},
        {"t": E.TYPE1, "path": fs / "typing.ogg", "volume": 0.18, "len": E.REPLY1 - E.TYPE1},
        {"t": E.REPLY1 + 0.45, "path": fs / "pop.ogg", "volume": 0.3},
        {"t": E.REPLY2, "path": fs / "typing.ogg", "volume": 0.14, "len": 0.45},
        {"t": E.REPLY2 + 0.45, "path": fs / "pop.ogg", "volume": 0.3},
        {"t": E.REPLY3, "path": fs / "typing.ogg", "volume": 0.14, "len": 0.4},
        {"t": E.REPLY3 + 0.4, "path": fs / "pop.ogg", "volume": 0.3},
        {"t": E.NUDGE, "path": fs / "pop-cartoon.ogg", "volume": 0.32},
        # Терминал: файлы бегут, тесты падают, плашка FAILED.
        {"t": E.TESTS + E.TESTS_STAMP, "path": fs / "fail-horn.ogg", "volume": 0.16, "len": 0.9},
        {"t": E.RUN + 0.05, "path": fs / "typing-digital.ogg", "volume": 0.14, "len": 1.0},
        # Зелёное: дзынь + ликование.
        {"t": E.PASS, "path": fs / "ding.ogg", "volume": 0.35},
        {"t": E.PASS + 0.05, "path": fs / "cheer.ogg", "volume": 0.16, "len": 3.6},
        {"t": E.DONE, "path": fs / "typing.ogg", "volume": 0.14, "len": 0.4},
        {"t": E.DONE + 0.4, "path": fs / "pop.ogg", "volume": 0.32},
        {"t": SH["out_end"][0] + 1.1, "path": ki / "click_002.ogg", "volume": 0.12, "len": 0.2},
    ]
    # Склейки внутрь — глухой удар; наружу — «вжух».
    for i, (t0, _t1, n) in enumerate(A.SHOTS[1:], start=1):
        prev = A.SHOTS[i - 1][2]
        if A.LOOK[n] == "in" and A.LOOK[prev] == "out":
            out.append({"t": t0, "path": fs / "impact-low.ogg", "volume": 0.22, "len": 0.7})
            out.append({"t": t0, "path": fs / "glitch.ogg", "volume": 0.1, "len": 0.3})
        elif A.LOOK[n] == "out" and A.LOOK[prev] != "out":
            out.append({"t": max(0.0, t0 - 0.05), "path": fs / "whoosh-2.ogg", "volume": 0.16, "len": 0.5})
    # Тесты сыплются: щелчки «FAIL».
    for j in range(8):
        out.append({"t": round(E.TESTS + 0.08 + j * 0.1, 3), "path": ki / f"tick_00{1 + j % 2}.ogg", "volume": 0.14, "len": 0.1})
    return out


def look_mask(rate: int = 100, fade: float = 0.03) -> tuple[np.ndarray, np.ndarray]:
    """Доля «про себя» по времени (0 — в чате, 1 — внутри) и отдельно радость; ступенька со сглаживанием ``fade``."""
    n = int(math.ceil(DUR * rate)) + 1
    t = np.arange(n) / rate
    inner = np.zeros(n)
    joy = np.zeros(n)
    for a, b, name in A.SHOTS:
        sel = (t >= a) & (t < b)
        if A.LOOK[name] in ("in", "joy"):
            inner[sel] = 1.0
        if A.LOOK[name] == "joy":
            joy[sel] = 1.0
    k = max(1, int(fade * rate))
    ker = np.ones(k) / k
    return np.convolve(inner, ker, mode="same"), np.convolve(joy, ker, mode="same")


def two_world_music(music: Path, tension: Path, spans: list[tuple[float, float]], gain_db: float, out: Path) -> Path:
    """Музыка ролика: в чате — чистый трек; про себя — тот же трек через низкие частоты и на 8 дБ тише + гул tension;
    на радости — трек чистый и громче. Приседает под голос (A9.duck_curve). Стерео 48 кГц."""
    import wave

    sr = 48000

    def load(path: Path, af: str | None = None) -> np.ndarray:
        args = ["ffmpeg", "-v", "error", "-i", str(path)] + (["-af", af] if af else []) + ["-ac", "2", "-ar", str(sr), "-f", "f32le", "-"]
        x = np.frombuffer(subprocess.run(args, capture_output=True, check=True).stdout, dtype=np.float32).reshape(-1, 2)
        n = int(DUR * sr)
        reps = int(math.ceil(n / max(1, len(x))))
        return np.tile(x, (reps, 1))[:n]

    clean = load(music)
    muffled = load(music, "lowpass=f=420,lowpass=f=420")
    drone = load(tension)
    n = len(clean)
    t = np.arange(n) / sr
    inner, joy = look_mask()
    m_in = np.interp(t, np.arange(len(inner)) / 100, inner)
    m_joy = np.interp(t, np.arange(len(joy)) / 100, joy)
    m_sad = m_in * (1 - m_joy)
    curve = A9.duck_curve(spans, DUR, A9.MUSIC_GAP_LU, A9.MUSIC_SPEECH_LU)
    db = np.interp(t, np.arange(len(curve)) / 100, curve) + gain_db
    g = 10 ** (db / 20)
    y = clean * ((1 - m_sad) * (1 + 0.4 * m_joy))[:, None] + muffled * (m_sad * 10 ** (-8 / 20) * 1.6)[:, None]
    y += drone * (m_sad * 10 ** (-14 / 20))[:, None]
    edge = np.clip(t / 0.2, 0, 1) * np.clip((DUR - t) / 0.6, 0, 1)
    y *= (g * edge).astype(np.float32)[:, None]
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
    music_file = two_world_music(music, P1.PACKS / "freesound" / "tension.ogg", spans, gain, work / "music_two_worlds.wav")
    layers = [{"id": "voice", "type": "audio", "start": 0.0, "end": DUR, "props": {"asset_id": 9001, "mode": "track", "volume": 1.0, "role": "voice"}},
              {"id": "music", "type": "audio", "start": 0.0, "end": DUR,
               "props": {"asset_id": 9002, "mode": "track", "volume": 1.0, "gain_db": 0.0, "role": "music"}}]
    assets = {asset_key(9001): str(voice), asset_key(9002): str(music_file)}
    report["music"] = {"file": music.name, "lufs": m_lufs, "voice_lufs": voice_db, "under_speech_lu": A9.MUSIC_SPEECH_LU, "in_gaps_lu": A9.MUSIC_GAP_LU,
                       "inner": "lowpass 420 Гц −8 дБ + tension −14 дБ"}
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

NUMS = {"400": ["четыреста"], "147": ["сто", "сорок", "семь"], "5": ["пять"], "2": ["два"]}
# Маскированный мат субтитров → как звучит (после чистки от «*»).
UNMASK = {"блть": "блядь", "блдь": "блядь", "охел": "охуел", "ска": "сука", "нахй": "нахуй", "ебть": "ебать", "apple": "эппл",
          "jquery": "джейквери", "final": "файнал"}


def _tokens(text: str) -> list[str]:
    out = []
    for x in text.lower().replace("ё", "е").replace("—", " ").replace("-", " ").replace("_", " ").split():
        w = re.sub(r"[^а-яa-z0-9]", "", x)
        if w:
            out += NUMS.get(w, [UNMASK.get(w, w)])
    return out


def script_words() -> list[str]:
    return [w for ln in sorted(E.LINES, key=lambda ln: ln[4]) for w in _tokens(ln[5])]


def check_words(final: Path, work: Path) -> dict:
    """faster-whisper по готовому ролику — сверка со сценарием (слова по порядку)."""
    import difflib

    import meme3d_voice as V

    probe = work / "final_check.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(final), "-ac", "1", "-ar", "16000", str(probe)], check=True)
    probe.with_suffix(".words.json").unlink(missing_ok=True)
    heard = [w for w in V.words(probe) if w["t1"] - w["t0"] > 0.02]
    want = script_words()
    got = [w for h in heard for w in _tokens(h["w"])]
    sm = difflib.SequenceMatcher(a=want, b=got, autojunk=False)
    diffs = [(op, " ".join(want[i1:i2]), " ".join(got[j1:j2])) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal"]
    return {"ratio": round(sm.ratio(), 3), "script_words": len(want), "heard_words": len(got), "diffs": diffs, "text": " ".join(w["w"] for w in heard)}


def voice_in_out_shots(words: list[dict]) -> list[dict]:
    """Голос «про себя» не должен звучать на планах «в чате»: слова субтитров, чья середина попала в такой план."""
    bad = []
    for w in words:
        m = (w["t0"] + w["t1"]) / 2
        for a, b, n in A.SHOTS:
            if a <= m < b and A.LOOK[n] == "out":
                bad.append({"w": w["w"], "t": round(m, 2), "shot": n})
    return bad


TEXT_BANDS = {"caps": (0.2, 0.755, 0.8, 0.845), "hook": (0.08, 0.04, 0.92, 0.24), "chip": (0.25, 0.04, 0.75, 0.1)}


def text_over_face(samples: list[tuple[float, str, dict]]) -> list[dict]:
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
    out = [(0.3, "hook")] + [(round((w["t0"] + w["t1"]) / 2, 3), "caps") for w in words]
    out += [(round((a + b) / 2 + 0.013, 3), "chip") for a, b, n in A.SHOTS if n != "cover"]
    return out


def scene_checks(render: list[str], words: list[dict], work: Path) -> dict:
    texts = text_samples(words)
    r = P1._run([*render, "--inspect-list", ",".join(f"{t:.3f}" for t in sorted({t for t, _ in texts}))], "осмотр")
    got = {round(x["t"], 3): x for x in json.loads(r.stdout.strip().splitlines()[-1])["inspect"]}
    seen = [(t, band, got[t]) for t, band in texts if t in got]
    res = {"text_over_face": text_over_face(seen), "text_samples": len(seen),
           "text_samples_with_face": sum(A9.face_box(t, s) is not None for t, _b, s in seen)}
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
                   "voice_in_chat_shots": voice_in_out_shots(words), "overlay_share": overlay_share()})
    report["sub_sync"] = A8.sub_sync(words, work)
    checks = work / "checks.json"
    if checks.is_file():
        report["scene_checks"] = json.loads(checks.read_text("utf-8"))
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1)[:6000])
    return 0
