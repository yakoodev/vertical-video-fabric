# -*- coding: utf-8 -*-
"""Шаг analyze: участок песни → вокальный стем (demucs) → атаки слогов, сетка долей, слова Whisper, привязка.

Пишет в OUT/work/: audio.wav (участок, 48 кГц), sep/…/vocals.wav, analysis.json, words.json, spec_*.png.
Whisper (faster-whisper) только распознаёт слова; время слова = атака слога на вокальном стеме."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from audio import beat_grid, load_wav, snap_words, vocal_onsets  # noqa: E402


def run(cmd: list[str]) -> None:
    print("  $", " ".join(str(c) for c in cmd[:6]), "…" if len(cmd) > 6 else "", flush=True)
    subprocess.run([str(c) for c in cmd], check=True)


def section(song: str, work: Path, start: float, end: float | None) -> tuple[float, float]:
    """Вырезает участок [start, end] в audio.wav (48 кГц стерео, для финального звука) и mix44.wav (для анализа)."""
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", song],
                               capture_output=True, text=True, check=True).stdout.strip())
    end = dur if end is None else min(end, dur)
    for name, ar in (("audio.wav", 48000), ("mix44.wav", 44100)):
        run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", song,
             "-ar", str(ar), "-ac", "2", str(work / name)])
    return start, end


def demucs(work: Path, demucs_cmd: str) -> Path:
    stem = work / "sep" / "htdemucs" / "mix44" / "vocals.wav"
    if stem.exists():
        print("  вокальный стем уже есть:", stem)
        return stem
    cmd = demucs_cmd.split("|") if "|" in demucs_cmd else [demucs_cmd]
    run([*cmd, "-n", "htdemucs", "--two-stems", "vocals", "-j", "1", "-o", str(work / "sep"), str(work / "mix44.wav")])
    return stem


def whisper(stem: Path, lang: str, prompt: str | None, model: str, t0: float) -> list[dict]:
    from faster_whisper import WhisperModel
    m = WhisperModel(model, device="cpu", compute_type="int8")
    segs, _ = m.transcribe(str(stem), language=lang, word_timestamps=True, vad_filter=False,
                           initial_prompt=prompt or None, beam_size=5, condition_on_previous_text=False)
    out = []
    for s in segs:
        print(f"   [{s.start + t0:6.2f}-{s.end + t0:6.2f}] {s.text}", flush=True)
        for w in s.words:
            txt = w.word.strip().strip(",.!?…«»\"—-").strip()
            if txt:
                out.append({"w": txt, "s": round(w.start + t0, 3), "e": round(w.end + t0, 3), "p": round(w.probability, 2)})
    return out


def spec_plot(stem_x: np.ndarray, sr: int, t0: float, a: float, b: float, words: list[dict], onsets: list, out: Path,
              row_len: float = 4.0) -> None:
    """Спектрограмма вокала (80–5000 Гц, лог) + огибающая; жёлтые — атаки, красные — слова (время атаки),
    серые — сырое время Whisper. Секунды песни на шкале."""
    import cv2
    hop, nfft = max(1, sr // 400), 2048
    pad = np.pad(stem_x, (nfft // 2, nfft // 2))
    fr = np.lib.stride_tricks.sliding_window_view(pad, nfft)[::hop] * np.hanning(nfft)
    S = np.abs(np.fft.rfft(fr, axis=1)); f = np.fft.rfftfreq(nfft, 1 / sr)
    idx = np.searchsorted(f, np.geomspace(80, 5000, 200))
    Sl = np.log1p(50 * S[:, idx]); Sl /= np.percentile(Sl, 99.5)
    fps = sr / hop
    rows = int(np.ceil((b - a) / row_len))
    Wpx, Hr = 2000, 300
    img = np.zeros((rows * Hr, Wpx, 3), np.uint8)
    for r in range(rows):
        ra, rb = a + r * row_len, a + (r + 1) * row_len
        i0, i1 = int((ra - t0) * fps), int((rb - t0) * fps)
        sp = np.clip(Sl[max(0, i0):max(i0 + 1, i1)].T[::-1], 0, 1)
        if sp.shape[1] < 2:
            continue
        y0 = r * Hr
        img[y0:y0 + 200] = cv2.applyColorMap((cv2.resize(sp, (Wpx, 200)) * 255).astype(np.uint8), cv2.COLORMAP_INFERNO)
        X = lambda s: int((s - ra) / row_len * Wpx)  # noqa: E731
        for s in np.arange(np.ceil(ra * 10) / 10, rb, 0.1):
            big = abs(s - round(s)) < 0.01
            cv2.line(img, (X(s), y0 + 200), (X(s), y0 + (222 if big else 208)), (200, 200, 200), 1)
            if big:
                cv2.putText(img, f"{s:.0f}", (X(s) + 2, y0 + 222), 0, 0.5, (220, 220, 220), 1)
        for o in onsets:
            if ra <= o[0] < rb:
                cv2.line(img, (X(o[0]), y0 + 120), (X(o[0]), y0 + 200), (0, 230, 255), 1)
        for k, w in enumerate(words):
            if ra <= w["s"] < rb:
                cv2.line(img, (X(w["s"]), y0 + 230), (X(w["s"]), y0 + 250), (150, 150, 150), 2)
            if ra <= w["on"] < rb:
                cv2.line(img, (X(w["on"]), y0), (X(w["on"]), y0 + 120), (60, 60, 255), 2)
                cv2.putText(img, f"{k}:{w['w']}", (X(w["on"]) + 3, y0 + 268 + 14 * (k % 2)), 0, 0.5, (90, 90, 255), 1)
    # кириллицу cv2 не рисует — подписи слов в analysis.json; на картинке номер слова
    cv2.imwrite(str(out), img)


def main(song: str, out: Path, start: float = 0.0, end: float | None = None, lang: str = "ru", prompt: str | None = None,
         demucs_cmd: str = "demucs", model: str = "mobiuslabsgmbh/faster-whisper-large-v3-turbo") -> dict:
    work = out / "work"; work.mkdir(parents=True, exist_ok=True)
    t0, t1 = section(song, work, start, end)
    stem = demucs(work, demucs_cmd)
    x, sr = load_wav(str(stem))
    mix, msr = load_wav(str(work / "mix44.wav"))
    ons = vocal_onsets(x, sr, t0)
    grid = beat_grid(mix, msr, 0.0, t1 - t0)
    grid["beats"] = [round(b + t0, 3) for b in grid["beats"]]
    grid["downbeats"] = [round(b + t0, 3) for b in grid["downbeats"]]
    print(f"  темп {grid['bpm']} BPM, долей {len(grid['beats'])}, атак вокала {len(ons['onsets'])}")
    words_path = work / "whisper_words.json"
    if words_path.exists():
        raw = json.load(open(words_path, encoding="utf-8"))
    else:
        raw = whisper(stem, lang, prompt, model, t0)
        json.dump(raw, open(words_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    words = snap_words(raw, ons["onsets"])
    json.dump(words, open(work / "words.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    an = {"song": os.path.abspath(song), "start": t0, "end": t1, "duration": round(t1 - t0, 3), **grid,
          "onsets": ons["onsets"], "words": words}
    json.dump(an, open(work / "analysis.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for k in range(int(np.ceil((t1 - t0) / 24))):
        a = t0 + k * 24
        spec_plot(x, sr, t0, a, min(t1, a + 24), words, ons["onsets"], work / f"spec_{k:02d}.png")
    print("  слова (время атаки на вокале, сдвиг от Whisper):")
    for k, w in enumerate(words):
        print(f"   {k:3d} {w['on']:7.2f} ({w['snap'] if w['snap'] is not None else '—':>6}) p={w['p']:.2f} {w['w']}")
    shutil.copy(work / "analysis.json", out / "analysis.json")
    return an


def retranscribe(out: Path, windows: list[tuple[float, float]], lang: str = "ru",
                 model: str = "mobiuslabsgmbh/faster-whisper-large-v3-turbo") -> None:
    """Заменить слова Whisper в окнах [a, b) распознаванием коротких кусков стема без подсказки.

    Нужно, когда длинный прогон «залип» (повторы, галлюцинации на припевах). Окна не должны перекрываться:
    из каждого окна берутся слова, начало которых попало в него. После замены заново привязываются атаки."""
    from faster_whisper import WhisperModel
    work = out / "work"
    an = json.load(open(work / "analysis.json", encoding="utf-8"))
    raw = json.load(open(work / "whisper_words.json", encoding="utf-8"))
    x, sr = load_wav(str(work / "sep" / "htdemucs" / "mix44" / "vocals.wav"))
    t0 = an["start"]
    m = WhisperModel(model, device="cpu", compute_type="int8")
    for a, b in windows:
        pad = 0.4
        seg = x[max(0, int((a - pad - t0) * sr)):int((b + pad - t0) * sr)]
        n16 = int(len(seg) * 16000 / sr)
        seg16 = np.interp(np.linspace(0, len(seg) - 1, n16), np.arange(len(seg)), seg).astype(np.float32)
        seg16 = np.concatenate([np.zeros(8000, np.float32), seg16, np.zeros(8000, np.float32)])
        segs, _ = m.transcribe(seg16, language=lang, word_timestamps=True, vad_filter=False, beam_size=5,
                               condition_on_previous_text=False)
        base = max(t0, a - pad) - 0.5
        new = []
        for s in segs:
            for w in s.words:
                txt = w.word.strip().strip(",.!?…«»\"—-").strip()
                st = round(base + w.start, 3)
                if txt and a <= st < b:
                    new.append({"w": txt, "s": st, "e": round(base + w.end, 3), "p": round(w.probability, 2)})
        raw = [w for w in raw if not (a <= w["s"] < b)] + new
        print(f"  [{a:.2f}-{b:.2f}]", " ".join(f"{w['w']}@{w['s']:.2f}" for w in new), flush=True)
    raw.sort(key=lambda w: w["s"])
    json.dump(raw, open(work / "whisper_words.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    words = snap_words(raw, an["onsets"])
    an["words"] = words
    json.dump(words, open(work / "words.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(an, open(work / "analysis.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    shutil.copy(work / "analysis.json", out / "analysis.json")
