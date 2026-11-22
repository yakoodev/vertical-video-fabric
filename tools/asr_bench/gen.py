"""Записи стенда: 14 без речи (синтез + CC0-музыка), наши TTS-фразы чистые / с хвостом почти тишины / под музыкой,
фразы Windows-синтеза с «опасным» текстом («Спасибо за просмотр», «Продолжение следует») и куски дубляжа.

Всё 16 кГц моно 16 бит. Синтез — numpy с фиксированным seed: повторная сборка даёт те же сэмплы."""

from __future__ import annotations

import glob
import hashlib
import json
import os
import subprocess
import wave
from pathlib import Path
from typing import Any

import numpy as np

RATE = 16000
SEED = 20261122
OFFLOAD = Path(os.getenv("ASR_BENCH_OFFLOAD", "D:/vvf-offload"))
BENCH_DIR = Path(os.getenv("ASR_BENCH_DIR", str(OFFLOAD / "asr_bench")))
# CC0 (freesound, tools/fetch_music.py): лежат в работах мемов и витубики.
MUSIC_DIRS = (OFFLOAD / "memes/pilot1/music", OFFLOAD / "vtubika/music")
MUSIC_SHA = {  # тот же sha256, что закреплён в tools/fetch_music.py
    "lofi-beat": "8553e7a147826030fd1b8d2438b381d1ba5db3cb6c500dfdf8d305455d48c5e1",
    "guitar": "08698670898afa895a3ab4f808a3d090e29bb41e9c1d3607186463460d174d4f",
    "chill-keys": "33bef30bbd3430569f88e8e07e6d14035977746569f71abedaa6ca72275c9732",
}
TAIL_SEC = 30.0
TAIL_DB = -60.0  # «очень тихий ровный шум, как в комнате»
VOICE_DB = -20.0
MUSIC_UNDER_DB = 10.0  # голос над музыкой на 10 дБ — нижняя граница voice_check (app/voice_check.py)
DUB_GLOB = "G:/vvf-hot/voice/dub_gandzhaev/asr/*.json"
DUB_AUDIO = OFFLOAD / "voice/dub_gandzhaev/audio"
DUB_WINDOW = 120.0


# --------------------------------------------------------------------------- сигналы


def db_to_amp(db: float) -> float:
    return float(10 ** (db / 20))


def rms_db(x: np.ndarray) -> float:
    r = float(np.sqrt(np.mean(np.square(x, dtype=np.float64)))) if len(x) else 0.0
    return 20 * np.log10(max(r, 1e-9))


def at_level(x: np.ndarray, db: float) -> np.ndarray:
    """Сигнал на заданном RMS (дБ полной шкалы)."""
    r = float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))
    return (x * (db_to_amp(db) / max(r, 1e-12))).astype(np.float32)


def white(n: int, rng: np.random.Generator) -> np.ndarray:
    return rng.standard_normal(n).astype(np.float32)


def pink(n: int, rng: np.random.Generator) -> np.ndarray:
    """1/f: белый шум, спектр поделён на √f."""
    spec = np.fft.rfft(rng.standard_normal(n))
    f = np.arange(len(spec), dtype=np.float64)
    f[0] = 1.0
    return np.fft.irfft(spec / np.sqrt(f), n).astype(np.float32)


def lowpass(x: np.ndarray, cutoff: float) -> np.ndarray:
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / RATE)
    return np.fft.irfft(spec * (1 / (1 + (f / cutoff) ** 4)), len(x)).astype(np.float32)


def fan(sec: float, rng: np.random.Generator) -> np.ndarray:
    """Вентилятор: низкий шум до ~400 Гц, лопасти — гул 90 Гц с гармониками, лёгкое «дыхание» громкости."""
    n = int(sec * RATE)
    t = np.arange(n) / RATE
    body = lowpass(white(n, rng), 400.0)
    blade = sum(np.sin(2 * np.pi * 90 * k * t) / k for k in (1, 2, 3)).astype(np.float32)
    x = at_level(body, -30) + at_level(blade, -40)
    return (x * (1 + 0.1 * np.sin(2 * np.pi * 0.3 * t))).astype(np.float32)


def mains(sec: float) -> np.ndarray:
    """Гул электросети: 50 Гц и нечётные гармоники."""
    t = np.arange(int(sec * RATE)) / RATE
    return sum(np.sin(2 * np.pi * 50 * k * t) / k for k in (1, 3, 5, 7)).astype(np.float32)


def tone(sec: float, hz: float = 440.0) -> np.ndarray:
    t = np.arange(int(sec * RATE)) / RATE
    return np.sin(2 * np.pi * hz * t).astype(np.float32)


def keyboard(sec: float, rng: np.random.Generator) -> np.ndarray:
    """Щелчки клавиатуры: короткие всплески фильтрованного шума 8–12 в секунду, разной силы, с паузами."""
    n = int(sec * RATE)
    x = np.zeros(n, dtype=np.float32)
    t = 0.2
    while t < sec - 0.1:
        k = int(t * RATE)
        ln = int(rng.uniform(0.006, 0.02) * RATE)
        burst = white(ln, rng) * np.exp(-np.linspace(0, 6, ln)).astype(np.float32)
        x[k:k + ln] += burst * float(rng.uniform(0.3, 1.0))
        t += float(rng.exponential(0.1)) + (float(rng.uniform(0.6, 1.5)) if rng.random() < 0.08 else 0.0)
    return x


def chords(sec: float) -> np.ndarray:
    """Аккорды (C, Am, F, G) мягкими гармониками с атакой и затуханием, по 2.5 с."""
    roots = [(261.63, 329.63, 392.0), (220.0, 261.63, 329.63), (174.61, 220.0, 261.63), (196.0, 246.94, 293.66)]
    step = 2.5
    n = int(sec * RATE)
    out = np.zeros(n, dtype=np.float32)
    i = 0
    while i * step < sec:
        a, b = int(i * step * RATE), min(n, int((i + 1) * step * RATE))
        t = np.arange(b - a) / RATE
        env = np.minimum(1, t / 0.02) * np.exp(-t * 1.2)
        notes = sum(np.sin(2 * np.pi * f * h * t) / h for f in roots[i % 4] for h in (1, 2, 3))
        out[a:b] = (notes * env).astype(np.float32)
        i += 1
    return out


def no_speech_set(music_bed: np.ndarray | None) -> dict[str, np.ndarray]:
    """14 записей без единого слова — как в ролике."""
    rng = np.random.default_rng(SEED)
    out: dict[str, np.ndarray] = {}
    for sec in (1, 5, 30, 60):
        out[f"silence_{sec}s"] = np.zeros(int(sec * RATE), dtype=np.float32)
    out["white_quiet"] = at_level(white(20 * RATE, rng), -60)
    out["white_loud"] = at_level(white(20 * RATE, rng), -20)
    out["pink_quiet"] = at_level(pink(20 * RATE, rng), -55)
    out["pink_loud"] = at_level(pink(20 * RATE, rng), -25)
    out["fan_hum"] = at_level(fan(20, rng), -30)
    out["mains_50hz"] = at_level(mains(20), -30)
    out["tone_440"] = at_level(tone(10), -20)
    out["keyboard"] = at_level(keyboard(20, rng), -30)
    out["chords"] = at_level(chords(15), -20)
    if music_bed is not None:
        out["music_bed"] = at_level(music_bed[: 20 * RATE], -20)
    return out


def near_silence(sec: float = TAIL_SEC, db: float = TAIL_DB) -> np.ndarray:
    return at_level(pink(int(sec * RATE), np.random.default_rng(SEED + 1)), db)


# --------------------------------------------------------------------------- файлы


def read_audio(path: Path | str, start: float = 0.0, dur: float | None = None) -> np.ndarray:
    """Любой файл → 16 кГц моно float32 (ffmpeg)."""
    cut = (["-ss", f"{start:.3f}"] if start else []) + (["-t", f"{dur:.3f}"] if dur else [])
    pcm = subprocess.run(["ffmpeg", "-v", "error", *cut, "-i", str(path), "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0


def write_wav(path: Path, x: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def music_file(name: str) -> Path | None:
    """CC0-трек паков с проверкой sha256 (чужой файл с тем же именем не берём)."""
    for d in MUSIC_DIRS:
        p = d / f"{name}.mp3"
        if p.is_file() and sha256(p) == MUSIC_SHA[name]:
            return p
    return None


# --------------------------------------------------------------------------- речь


def tts_items() -> list[dict]:
    """Наши TTS-файлы с известным текстом: пробы мемов (take*.json) и переозвучки витубики (revoice_*.json)."""
    found = []
    pats = [OFFLOAD / "memes/*/work/take*.json", OFFLOAD / "vtubika/**/revoice_*.json"]
    for pat in pats:
        for j in sorted(glob.glob(str(pat), recursive=True)):
            if j.endswith(".words.json"):
                continue
            try:
                text = json.loads(Path(j).read_text("utf-8")).get("text")
            except (OSError, ValueError):
                continue
            audio = next((Path(j[:-5] + e) for e in (".mp3", ".wav") if Path(j[:-5] + e).is_file()), None)
            if text and audio:
                rel = Path(j).relative_to(OFFLOAD).as_posix()
                found.append({"id": rel.replace("/work/", "_").replace("/", "_")[:-5], "src": str(audio), "ref": text})
    return found


# Windows-синтез (SAPI, Irina/Pavel/Zira): живые фразы, совпадающие с фантомами стоп-листа, — их резать нельзя.
SAPI_LINES = [
    ("sapi_thanks_ru", "Microsoft Irina Desktop", "Ну вот и всё на сегодня. Спасибо за просмотр, подписывайтесь на канал."),
    ("sapi_continue_ru", "Microsoft Irina Desktop", "Что было дальше, узнаем в следующей серии. Продолжение следует."),
    ("sapi_credits_ru", "Microsoft Irina Desktop", "Субтитры сделал мой друг Дима, огромное ему спасибо."),
    ("sapi_editor_ru", "Microsoft Irina Desktop", "Редактор субтитров у нас новый, и он работает быстрее."),
    ("sapi_thanks_en", "Microsoft Zira Desktop", "That is all for today. Thank you for watching, and see you next time."),
    ("sapi_plain_ru", "Microsoft Irina Desktop", "Сегодня мы проверим, как нейросеть слышит тишину и шум."),
]


def sapi(text: str, voice: str, out: Path) -> bool:
    """Фраза системным синтезом Windows в wav (нет PowerShell или голоса — пропуск)."""
    if out.is_file():
        return True
    out.parent.mkdir(parents=True, exist_ok=True)
    esc = text.replace("'", "''")
    script = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              f"$s.SelectVoice('{voice}'); $s.Rate = 0; $s.SetOutputToWaveFile('{out}'); $s.Speak('{esc}'); $s.Dispose()")
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, timeout=60, check=True)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.is_file()


def dub_items(n: int = 3, window: float = DUB_WINDOW) -> list[dict]:
    """Куски дубляжа (смесь с музыкой) и опорный текст — large-v3 по вокальному стему (G:/vvf-hot, конвейер голоса).
    Опора не эталон: считаем, сколько её слов профиль теряет на смеси."""
    out = []
    for j in sorted(glob.glob(DUB_GLOB)):
        ep = Path(j).stem
        audio = next((p for p in DUB_AUDIO.glob(ep + ".*")), None)
        if audio is None:
            continue
        segs = json.loads(Path(j).read_text("utf-8"))
        # Окно с самой плотной речью после первых 3 минут (заставка и титры — не то).
        best, best_n = None, 0
        for s in segs:
            t0 = float(s["start"])
            if t0 < 180:
                continue
            words = [w for g in segs for w in g.get("words") or [] if t0 <= w[0] < t0 + window]
            if len(words) > best_n:
                best, best_n = t0, len(words)
        if best is None:
            continue
        ref = " ".join(w[2] for g in segs for w in g.get("words") or [] if best <= w[0] < best + window - 0.5)
        out.append({"id": f"dub_{ep}", "src": str(audio), "start": best, "dur": window, "ref": ref,
                    "high_ns_segments": sum(1 for g in segs if best <= g["start"] < best + window and g["nsp"] > 0.6)})
        if len(out) >= n:
            break
    return out


def build(out_dir: Path = BENCH_DIR) -> dict:
    """Собрать записи стенда и manifest.json: {id, set, path, ref, speech_end}."""
    out_dir.mkdir(parents=True, exist_ok=True)
    items: list[dict] = []
    lofi = music_file("lofi-beat")
    bed = read_audio(lofi, 30.0, 25.0) if lofi else None
    for name, x in no_speech_set(bed).items():
        p = out_dir / "nospeech" / f"{name}.wav"
        write_wav(p, x)
        items.append({"id": name, "set": "nospeech", "path": str(p), "ref": "", "speech_end": 0.0})

    speech = tts_items()
    for sid, voice, text in SAPI_LINES:
        p = out_dir / "sapi_raw" / f"{sid}.wav"
        if sapi(text, voice, p):
            speech.append({"id": sid, "src": str(p), "ref": text, "control": True})
    tail = near_silence()
    beds = [read_audio(m, 20.0, 60.0) for m in (music_file("guitar"), music_file("chill-keys")) if m]
    for k, s in enumerate(speech):
        x = at_level(read_audio(s["src"]), VOICE_DB)
        x = np.clip(x, -1, 1)
        end = round(len(x) / RATE, 3)
        base = {"ref": s["ref"], "speech_end": end, "src": s["src"], "control": bool(s.get("control"))}
        clean = out_dir / "clean" / f"{s['id']}.wav"
        write_wav(clean, x)
        items.append({"id": s["id"], "set": "clean", "path": str(clean), **base})
        tailed = out_dir / "tail" / f"{s['id']}.wav"
        write_wav(tailed, np.concatenate([x, tail]))
        items.append({"id": s["id"], "set": "tail", "path": str(tailed), **base})
        if beds and k % 3 == 0:  # каждая третья — под музыку
            m = beds[k % len(beds)]
            m = np.resize(m, len(x))
            mixed = x + at_level(m, VOICE_DB - MUSIC_UNDER_DB)
            p = out_dir / "music" / f"{s['id']}.wav"
            write_wav(p, mixed)
            items.append({"id": s["id"], "set": "music", "path": str(p), **base})
    for d in dub_items():
        p = out_dir / "dub" / f"{d['id']}.wav"
        if not p.is_file():
            write_wav(p, read_audio(d["src"], d["start"], d["dur"]))
        items.append({"id": d["id"], "set": "dub", "path": str(p), "ref": d["ref"], "speech_end": d["dur"],
                      "src": d["src"], "start": d["start"], "high_ns_segments": d["high_ns_segments"]})
    manifest = {"seed": SEED, "tail_sec": TAIL_SEC, "tail_db": TAIL_DB, "music_under_db": MUSIC_UNDER_DB, "items": items}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), "utf-8")
    return manifest


def load_manifest(out_dir: Path = BENCH_DIR) -> dict[str, Any]:
    return json.loads((out_dir / "manifest.json").read_text("utf-8"))
