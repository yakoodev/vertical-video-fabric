# -*- coding: utf-8 -*-
"""Звук для музыкального клипа: чтение wav, вокальные слоги (onsets) по стему demucs, сетка долей.

Тайминг слов берётся ТОЛЬКО отсюда (атаки слогов на чистом вокале); Whisper даёт лишь, какие это слова."""
from __future__ import annotations

import wave

import numpy as np


def load_wav(path: str) -> tuple[np.ndarray, int]:
    """Моно float32 [-1, 1] и частота. Поддерживает 16/32-бит PCM."""
    w = wave.open(str(path))
    sr, n, ch, sw = w.getframerate(), w.getnframes(), w.getnchannels(), w.getsampwidth()
    raw = w.readframes(n)
    if sw == 2:
        x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768
    elif sw == 4:
        x = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648
    else:
        raise ValueError(f"{path}: {sw * 8}-бит wav не поддерживается")
    return x.reshape(-1, ch).mean(1), sr


def _stft_mag(x: np.ndarray, sr: int, hop: int, nfft: int = 2048, with_rms: bool = False):
    pad = np.pad(x, (nfft // 2, nfft // 2))
    fr = np.lib.stride_tricks.sliding_window_view(pad, nfft)[::hop] * np.hanning(nfft)
    S, f = np.abs(np.fft.rfft(fr, axis=1)).astype(np.float32), np.fft.rfftfreq(nfft, 1 / sr)
    if with_rms:
        return S, f, np.sqrt((fr ** 2).mean(1))
    return S, f


def vocal_onsets(stem: np.ndarray, sr: int, t0: float = 0.0, min_db: float = -38.0, gap: float = 0.07) -> dict:
    """Атаки слогов на вокальном стеме: спектральный поток 120–6000 Гц, шаг 5 мс, адаптивный порог.

    Возвращает {"onsets": [[t, сила 0..1, dB], ...], "fps": кадров огибающей в секунду, "flux": [...], "db": [...]}.
    t — секунды песни (t0 — время начала стема)."""
    hop = max(1, int(round(sr * 0.005)))
    S, f, rms = _stft_mag(stem, sr, hop, with_rms=True)
    band = (f > 120) & (f < 6000)
    L = np.log1p(200 * S[:, band])
    flux = np.concatenate([[0], np.maximum(0, np.diff(L, axis=0)).sum(1)])
    db = 20 * np.log10(rms + 1e-6)          # громкость кадра (окно Ханна), как в утверждённом song15
    fps = sr / hop
    k = np.hanning(9); k /= k.sum()
    fl = np.convolve(flux, k, mode="same")
    nb = max(1, int(0.4 * fps))
    base = np.convolve(fl, np.ones(nb) / nb, mode="same")
    peaks = []
    for i in range(10, len(fl) - 10):
        if fl[i] == fl[i - 8:i + 9].max() and fl[i] > base[i] * 1.35 and db[min(len(db) - 1, i + 6)] > min_db:
            peaks.append(i)
    merged: list[int] = []
    for i in peaks:
        if merged and (i - merged[-1]) / fps < gap:
            if fl[i] > fl[merged[-1]]:
                merged[-1] = i
            continue
        merged.append(i)
    mx = float(fl.max()) or 1.0
    ons = [[round(float(i / fps + t0), 3), round(float(fl[i] / mx), 3), round(float(db[min(len(db) - 1, i + 6)]), 1)]
           for i in merged]
    return {"onsets": ons, "fps": fps, "t0": t0, "flux": [round(float(v), 3) for v in fl],
            "db": [round(float(v), 1) for v in db]}


def beat_grid(mix: np.ndarray, sr: int, t0: float, t1: float, bpm_range=(70, 180)) -> dict:
    """Темп автокорреляцией огибающей атак + фаза, максимизирующая сумму атак на долях.

    Возвращает {"bpm", "period", "beats": [секунды песни], "downbeats": [каждая 4-я доля с самой сильной фазой]}."""
    hop, nfft = 256, 2048
    S, f = _stft_mag(mix, sr, hop, nfft)
    L = np.log1p(100 * S)
    flux = np.concatenate([[0], np.maximum(0, np.diff(L, axis=0)).sum(1)])
    low = np.concatenate([[0], np.maximum(0, np.diff(L[:, f < 150], axis=0)).sum(1)])
    fps = sr / hop

    def norm(v):
        v = v - np.convolve(v, np.ones(32) / 32, mode="same")
        v = np.maximum(v, 0)
        return v / (v.max() + 1e-9)

    on = norm(flux) + 0.5 * norm(low)
    a, b = int(t0 * fps), min(len(on) - 1, int(t1 * fps))
    seg = on[a:b] - on[a:b].mean()
    ac = np.correlate(seg, seg, mode="full")[len(seg) - 1:]
    best = []
    for bpm in np.arange(bpm_range[0], bpm_range[1], 0.1):
        lag = 60 / bpm * fps
        i, j = int(round(lag)), int(round(2 * lag))
        if j >= len(ac):
            continue
        best.append((ac[i] + 0.5 * ac[j], bpm))
    best.sort(reverse=True)
    bpm = float(best[0][1])
    period = 60 / bpm
    # уточняем период и фазу по всей длине (дрейф темпа живой записи не учитываем — ищем лучший постоянный)
    top = None
    for p in np.linspace(period * 0.995, period * 1.005, 21):
        for ph in np.arange(0, p, 0.004):
            bt = np.arange(t0 + ph, t1, p)
            idx = (bt * fps).astype(int)
            idx = idx[idx < len(on)]
            sc = on[idx].sum() + 0.5 * on[np.clip(idx + 1, 0, len(on) - 1)].sum()
            if top is None or sc > top[0]:
                top = (sc, ph, p)
    _, ph, period = top
    beats = np.arange(t0 + ph, t1, period)
    lowv = norm(low)
    acc = [lowv[(beats[k::4] * fps).astype(int).clip(0, len(lowv) - 1)].sum() for k in range(4)]
    k0 = int(np.argmax(acc))
    return {"bpm": round(60 / period, 3), "period": round(float(period), 5),
            "beats": [round(float(x), 3) for x in beats], "downbeats": [round(float(x), 3) for x in beats[k0::4]],
            "rms_per_beat": [round(float(np.sqrt((mix[int(x * sr):int((x + period) * sr)] ** 2).mean() + 1e-12)), 4)
                             for x in beats]}


VOWELS = set("аеёиоуыэюяaeiouy")


def syllables(word: str) -> int:
    return max(1, sum(ch in VOWELS for ch in word.lower()))


def snap_words(words: list[dict], onsets: list, look_back: float = 0.30, loud_db: float = -28.0) -> list[dict]:
    """Каждому слову Whisper — ближайшая атака на вокале (тайминг), плюс атаки следующих слогов внутри слова.

    Атака не может принадлежать двум словам; ищем в [s - look_back, конец слова]."""
    ts = [o[0] for o in onsets]
    used: set[int] = set()
    out = []
    for i, w in enumerate(words):
        nxt = words[i + 1]["s"] if i + 1 < len(words) else w["e"] + 1
        lo, hi = w["s"] - look_back, max(w["s"] + 0.12, min(w["e"], nxt - 0.02))
        cand = [k for k, t in enumerate(ts) if lo <= t <= hi and k not in used]
        n = syllables(w["w"])
        row = dict(w)
        loud = [k for k in cand if onsets[k][2] >= loud_db]
        cand = loud or cand          # тихие атаки (дыхание, хвосты, хай-хэт сквозь стем) — только если нет громких
        if cand:
            first = min(cand, key=lambda k: abs(ts[k] - w["s"]) - (0.08 if ts[k] <= w["s"] else 0) - 0.1 * onsets[k][1])
            row["on"] = ts[first]
            syl = [k for k in cand if k >= first][:n]
            used.update(syl)
            row["syl"] = [ts[k] for k in syl]
            row["snap"] = round(ts[first] - w["s"], 3)
        else:
            row["on"], row["syl"], row["snap"] = w["s"], [w["s"]], None
        out.append(row)
    return out
