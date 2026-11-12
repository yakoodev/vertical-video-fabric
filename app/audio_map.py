"""Карта звука для агента-монтажёра (MCP ``audio_map``): что, где и насколько громко звучит.

Агент монтировал по субтитрам и ``listen``: паузы, удары, смех, музыку и место склейки он не
видел, а ``find_noises`` / ``source_loudness`` дают по одному срезу. Здесь — одна компактная
карта клипа (шкала clip, как у слоёв и mute_audio) или окна исходника (шкала source):

* громкость по времени — LUFS-подобная (K-взвешивание по спектру, окно 0.4 с) с шагом 0.1–0.5 с:
  целыми числами и строкой-«спарклайном» (``▁▂▃▄▅▆▇█``), без мегабайтных массивов;
* отрезки: речь (Silero VAD из faster-whisper; нет — по энергии), тишина, громкий звук без речи,
  крик, смех?, музыка? (эвристики по спектру — с вопросительным знаком);
* удары — всплески атак (spectral flux) со скачком громкости; паузы речи (и «перед панчлайном?»);
* кандидаты склеек — конец фразы + 3 кадра, начало фразы − 2 кадра; кандидаты J/L на стыках
  кусков — по речи в звуке исходника за краями кусков;
* сетка долей (BPM, первая доля, сильная доля) — музыки клипа (set_music) и музыки в звуке;
* то же по дорожкам записи, если их несколько (игра / микрофон).

Тяжёлое (декодирование, VAD) — в очереди тяжёлых задач (app/heavy_queue.py), итог кешируется
по файлу исходника, кускам, дорожкам и операциям звука. Чистая часть (``analyze_pcm`` и
помощники) работает на numpy-массиве и проверяется на синтетике.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any
from uuid import uuid4

VERSION = 1
RATE = 16000
HOP = 0.05
WIN = 0.1
NFFT = 2048
SILENCE_DB = -50.0
LOUD_OVER_BG_DB = 8.0
HIT_JUMP_DB = 6.0
HIT_MIN_SEP = 0.3
MAX_HITS = 15
MIN_PAUSE = 0.25
MAX_POINTS = 240
SPARK = "▁▂▃▄▅▆▇█"
MAX_SOURCE_WINDOW = 300.0
JL_PROBE_SEC = 1.5
FRAME_SEC = 1 / 30


# --------------------------------------------------------------------------- #
# Признаки по кадрам
# --------------------------------------------------------------------------- #


def _k_weight(freqs: Any) -> Any:
    """K-взвешивание BS.1770 по мощности, приближённо: срез ниже ~38 Гц и полка +4 дБ выше ~1.5 кГц."""
    import numpy as np

    f2 = freqs * freqs
    highpass = (f2 * f2) / (f2 * f2 + 38.0 ** 4)
    shelf = 10 ** ((4.0 * f2 / (f2 + 1500.0 ** 2)) / 10)
    return np.asarray(highpass * shelf)


def frame_features(x: Any, rate: int = RATE, hop: float = HOP, win: float = WIN) -> dict:
    """Признаки по кадрам ``hop`` (окно ``win``): rms_db, lufs (окно 0.4 с), flat (плоскость спектра
    100–6000 Гц: 1 — шум, ~0 — тон), centroid (Гц), flux (атаки, дБ), voice (доля 300–3400 Гц)."""
    import numpy as np

    x = np.asarray(x, dtype=np.float32)
    step, size = int(round(rate * hop)), int(round(rate * win))
    count = max(0, (len(x) - size) // step + 1)
    if count == 0:
        empty = np.zeros(0)
        return {k: empty for k in ("rms_db", "lufs", "flat", "centroid", "flux", "voice")} | {"hop": hop}
    freqs = np.fft.rfftfreq(NFFT, 1 / rate)
    kw = _k_weight(freqs)
    window = np.hanning(size).astype(np.float32)
    band = (freqs >= 100) & (freqs <= 6000)
    voice_band = (freqs >= 300) & (freqs <= 3400)
    edges = np.geomspace(80, 7000, 17)
    band_idx = [(freqs >= lo) & (freqs < hi) for lo, hi in zip(edges, edges[1:])]
    rms_db = np.empty(count)
    kms = np.empty(count)
    flat = np.empty(count)
    centroid = np.empty(count)
    voice = np.empty(count)
    bands = np.empty((count, len(band_idx)))
    chunk = 512
    for c0 in range(0, count, chunk):
        idx = np.arange(c0, min(count, c0 + chunk))
        frames = np.stack([x[i * step: i * step + size] for i in idx])
        ms = np.mean(frames.astype(np.float64) ** 2, axis=1)
        spec = np.abs(np.fft.rfft(frames * window, NFFT, axis=1)) ** 2 + 1e-12
        total = spec.sum(axis=1)
        rms_db[idx] = 10 * np.log10(ms + 1e-12)
        kms[idx] = ms * (spec * kw).sum(axis=1) / total
        sb = spec[:, band]
        flat[idx] = np.exp(np.mean(np.log(sb), axis=1)) / np.mean(sb, axis=1)
        centroid[idx] = (spec * freqs).sum(axis=1) / total
        voice[idx] = spec[:, voice_band].sum(axis=1) / total
        for j, m in enumerate(band_idx):
            bands[idx, j] = 10 * np.log10(spec[:, m].sum(axis=1) + 1e-12)
    span = max(1, int(round(0.4 / hop)))
    kernel = np.ones(span) / span
    lufs = -0.691 + 10 * np.log10(np.convolve(kms, kernel, mode="same") + 1e-12)
    flux = np.zeros(count)
    if count > 1:
        flux[1:] = np.maximum(0.0, np.diff(bands, axis=0)).mean(axis=1)
    silent = rms_db < SILENCE_DB
    flux[silent] = 0.0
    return {"rms_db": rms_db, "lufs": lufs, "flat": flat, "centroid": centroid, "flux": flux,
            "voice": voice, "hop": hop}


# --------------------------------------------------------------------------- #
# Речь
# --------------------------------------------------------------------------- #


def vad_segments(x: Any, rate: int = RATE) -> tuple[list[tuple[float, float]], str]:
    """Речь: Silero VAD (тот же, что у faster-whisper в распознавании), нет — по энергии.
    → ([(start, end)], "silero" | "energy")."""
    import numpy as np

    audio = np.asarray(x, dtype=np.float32)
    if not len(audio):
        return [], "energy"
    try:
        from faster_whisper.vad import VadOptions, get_speech_timestamps

        opts = VadOptions(threshold=0.5, min_speech_duration_ms=150, min_silence_duration_ms=150, speech_pad_ms=30)
        found = get_speech_timestamps(audio, opts, sampling_rate=rate)
        return [(round(s["start"] / rate, 3), round(s["end"] / rate, 3)) for s in found], "silero"
    except Exception:  # noqa: BLE001 - нет модели/onnxruntime — энергия
        from app.speech_onsets import FRAME_SEC as VF, voiced_frames

        pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
        return _runs([bool(v) for v in voiced_frames(pcm, rate)], VF, min_len=0.15, gap=0.15), "energy"


def _runs(mask: Any, hop: float, min_len: float = 0.0, gap: float = 0.0, t0: float = 0.0) -> list[tuple[float, float]]:
    """Подряд идущие True → отрезки (с), разрывы ≤ gap сливаются, короче min_len — прочь."""
    spans: list[list[float]] = []
    start = None
    for i, on in enumerate(list(mask) + [False]):
        if on and start is None:
            start = i
        elif not on and start is not None:
            a, b = t0 + start * hop, t0 + i * hop
            if spans and a - spans[-1][1] <= gap + 1e-9:
                spans[-1][1] = b
            else:
                spans.append([a, b])
            start = None
    return [(round(a, 3), round(b, 3)) for a, b in spans if b - a >= min_len - 1e-9]


def _mask(spans: list[tuple[float, float]], count: int, hop: float) -> Any:
    import numpy as np

    out = np.zeros(count, dtype=bool)
    for a, b in spans:
        out[max(0, int(a / hop)): max(0, min(count, int(math.ceil(b / hop))))] = True
    return out


# --------------------------------------------------------------------------- #
# События
# --------------------------------------------------------------------------- #


def find_hits(feat: dict, speech: Any = None, top: int = MAX_HITS) -> list[dict]:
    """Удары: атака (flux) выше медианы + 4·MAD и скачок громкости ≥ HIT_JUMP_DB за 0.15 с."""
    import numpy as np

    flux, rms, hop = feat["flux"], feat["rms_db"], feat["hop"]
    n = len(flux)
    if n < 4:
        return []
    med = float(np.median(flux))
    mad = float(np.median(np.abs(flux - med))) * 1.4826 + 1e-6
    thr = med + 4 * mad
    back = max(1, int(round(0.15 / hop)))
    sep = max(1, int(round(HIT_MIN_SEP / hop)))
    cands = []
    for i in range(1, n):
        if flux[i] < thr or rms[i] < SILENCE_DB:
            continue
        lo = max(0, i - back)
        jump = float(rms[min(n - 1, i + 1)] - rms[lo:i].min()) if i > lo else 0.0
        if jump < HIT_JUMP_DB:
            continue
        if flux[i] < flux[max(0, i - sep): min(n, i + sep + 1)].max():
            continue
        cands.append((jump, i))
    picked: list[int] = []
    for _jump, i in sorted(cands, reverse=True):
        if all(abs(i - j) >= sep for j in picked):
            picked.append(i)
        if len(picked) >= top:
            break
    out = []
    for i in sorted(picked):
        item = {"t": round(i * hop, 2), "jump_db": round(float(rms[min(n - 1, i + 1)] - rms[max(0, i - back):i].min()), 1),
                "db": round(float(rms[min(n - 1, i + 1)]), 1)}
        if speech is not None and len(speech) == n:
            item["in_speech"] = bool(speech[i])
        out.append(item)
    return out


def find_pauses(speech_spans: list[tuple[float, float]], duration: float, feat: dict | None = None,
                hits: list[dict] | None = None) -> list[dict]:
    """Паузы речи ≥ MIN_PAUSE между фразами. ``before_punch`` — 0.3–1.0 с, а после неё фраза громче
    прошлой (+3 LU) или удар: место для «тишины перед ударом». ``event`` — удар внутри паузы."""
    import numpy as np

    out = []
    spans = sorted(speech_spans)
    for (a0, a1), (b0, b1) in zip(spans, spans[1:]):
        gap = b0 - a1
        if gap < MIN_PAUSE:
            continue
        item: dict = {"start": round(a1, 2), "end": round(b0, 2), "len": round(gap, 2)}
        inside = [h for h in hits or [] if a1 <= h["t"] <= b0 + 0.3]
        if inside:
            item["event"] = f"удар {inside[0]['t']:.2f}"
        if feat is not None and len(feat["lufs"]):
            hop = feat["hop"]
            lufs = feat["lufs"]

            def level(lo: float, hi: float) -> float:
                part = lufs[int(lo / hop): max(int(lo / hop) + 1, int(hi / hop))]
                return float(np.max(part)) if len(part) else -70.0

            louder = level(b0, min(b1, b0 + 1.0)) - level(max(a0, a1 - 1.0), a1) >= 3.0
            if 0.3 <= gap <= 1.0 and (louder or inside):
                item["before_punch"] = True
        out.append(item)
    if spans and duration - spans[-1][1] >= MIN_PAUSE:
        out.append({"start": round(spans[-1][1], 2), "end": round(duration, 2),
                    "len": round(duration - spans[-1][1], 2), "tail": True})
    if spans and spans[0][0] >= MIN_PAUSE:
        out.insert(0, {"start": 0.0, "end": round(spans[0][0], 2), "len": round(spans[0][0], 2), "head": True})
    return out


def cut_points(speech_spans: list[tuple[float, float]], words: list[dict] | None, duration: float,
               top: int = 14) -> list[dict]:
    """Где резать: конец фразы + 3 кадра (0.1 с, не в следующее слово), начало фразы − 2 кадра.
    По словам субтитров, если есть (точнее VAD), иначе по речи. Лучшие — с длинной паузой."""
    ends: list[tuple[float, float]] = []  # (конец речи, пауза после)
    starts: list[tuple[float, float]] = []
    clean = sorted((float(w["start"]), float(w["end"])) for w in words or []
                   if isinstance(w, dict) and w.get("start") is not None and w.get("end") is not None)
    if clean:
        for (a0, a1), (b0, _b1) in zip(clean, clean[1:]):
            if b0 - a1 >= 0.15:
                ends.append((a1, b0 - a1))
                starts.append((b0, b0 - a1))
        if clean:
            ends.append((clean[-1][1], duration - clean[-1][1]))
    else:
        spans = sorted(speech_spans)
        for (a0, a1), (b0, _b1) in zip(spans, spans[1:]):
            ends.append((a1, b0 - a1))
            starts.append((b0, b0 - a1))
        if spans:
            ends.append((spans[-1][1], duration - spans[-1][1]))
    out = []
    for t, gap in ends:
        cut = min(t + 3 * FRAME_SEC, t + max(0.0, gap - 0.04))
        if 0 < cut < duration:
            out.append({"t": round(cut, 2), "why": f"конец фразы +3 кадра (пауза {gap:.2f} с)", "gap": round(gap, 2)})
    for t, gap in starts:
        cut = t - min(2 * FRAME_SEC, max(0.0, gap - 0.04))
        if 0 < cut < duration and gap >= 0.3:
            out.append({"t": round(cut, 2), "why": f"начало фразы −2 кадра (пауза {gap:.2f} с)", "gap": round(gap, 2)})
    out.sort(key=lambda c: -c["gap"])
    return sorted(out[:top], key=lambda c: c["t"])


def label_spans(feat: dict, speech: Any) -> list[tuple[float, float, str]]:
    """Отрезки по смыслу звука: речь / крик / смех? / музыка? / громко / тишина. Эвристики."""
    import numpy as np

    rms, lufs, flat, cen, flux, hop = (feat[k] for k in ("rms_db", "lufs", "flat", "centroid", "flux", "hop"))
    n = len(rms)
    if n == 0:
        return []
    speech = np.asarray(speech if speech is not None and len(speech) == n else np.zeros(n, bool))
    silent = rms < SILENCE_DB
    labels = np.array([""] * n, dtype=object)
    labels[silent] = "тишина"
    labels[speech & ~silent] = "речь"
    other = ~speech & ~silent
    # Фон — нижняя треть звука без речи: громкие события сами фон не задирают.
    bg = float(np.percentile(rms[other], 30)) if other.any() else float(np.median(rms))
    loud = other & (rms > bg + LOUD_OVER_BG_DB)
    labels[loud] = "громко"
    if speech.any():
        s_lufs = float(np.median(lufs[speech]))
        s_cen = float(np.median(cen[speech]))
        shout = speech & (lufs > s_lufs + 7) & (cen > s_cen)
        labels[shout] = "крик"
    # Смех: пульсация громкости 3.5–6.5 Гц в окне 1 с, шумный (придыхание) спектр, не тихо.
    span = int(round(1.0 / hop))
    ref = float(np.median(lufs[speech])) if speech.any() else bg
    for i0 in range(0, max(0, n - span), max(1, span // 4)):
        seg = rms[i0:i0 + span]
        if np.mean(silent[i0:i0 + span]) > 0.3 or float(np.max(lufs[i0:i0 + span])) < ref - 6:
            continue
        env = seg - seg.mean()
        spec = np.abs(np.fft.rfft(env)) ** 2
        freqs = np.fft.rfftfreq(len(env), hop)
        total = spec[1:].sum() + 1e-9
        pulse = spec[(freqs >= 3.5) & (freqs <= 6.5)].sum() / total
        if pulse > 0.5 and float(np.median(flat[i0:i0 + span])) > 0.2 and float(np.std(seg)) > 3:
            labels[i0:i0 + span][labels[i0:i0 + span] != "тишина"] = "смех?"
    # Музыка: тональный ровный звук без речи с пульсом атак — окна 6 с.
    mspan = int(round(6.0 / hop))
    for i0 in range(0, max(0, n - mspan + 1), max(1, mspan // 6)):
        sl = slice(i0, i0 + mspan)
        if np.mean(silent[sl]) > 0.3 or np.mean(speech[sl]) > 0.6:
            continue
        tonal = float(np.median(flat[sl])) < 0.12
        steady = float(np.std(rms[sl][~silent[sl]])) < 4.0 if (~silent[sl]).any() else False
        fx = flux[sl] - flux[sl].mean()
        norm = float(np.dot(fx, fx)) + 1e-9
        lags = range(int(0.33 / hop), int(1.0 / hop) + 1)
        beat = max((float(np.dot(fx[:-lag], fx[lag:])) / norm for lag in lags if lag < len(fx)), default=0.0)
        if tonal and (beat > 0.3 or steady):
            mark = labels[sl]
            mark[(mark == "") | (mark == "громко")] = "музыка?"
            labels[sl] = mark
    labels[labels == ""] = "звук"
    out: list[list] = []
    for i, lab in enumerate(labels):
        if out and out[-1][2] == lab and abs(out[-1][1] - i * hop) < 1e-6:
            out[-1][1] = (i + 1) * hop
        else:
            out.append([i * hop, (i + 1) * hop, lab])
    # Обрывки < 0.2 с — к соседу.
    merged: list[list] = []
    for a, b, lab in out:
        if merged and (b - a < 0.2 or merged[-1][2] == lab):
            merged[-1][1] = b
        else:
            merged.append([a, b, lab])
    return [(round(a, 2), round(b, 2), lab) for a, b, lab in merged]


# --------------------------------------------------------------------------- #
# Доли
# --------------------------------------------------------------------------- #


def beat_grid(x: Any, rate: int = RATE, t0: float = 0.0) -> dict | None:
    """Сетка долей: BPM (app.music_energy.estimate_bpm), первая доля и сильная доля (каждая 4-я) по
    огибающей атак 10 мс. None — пульса не слышно (или звука меньше 8 с)."""
    import numpy as np

    from app.music_energy import estimate_bpm

    x = np.asarray(x, dtype=np.float32)
    if len(x) < rate * 8:
        return None
    x8 = x[:: max(1, rate // 8000)]
    bpm = estimate_bpm(x8, 8000)
    if not bpm:
        return None
    hop = 0.01
    step = int(rate * hop)
    count = len(x) // step
    blocks = x[: count * step].astype(np.float64).reshape(count, step)
    energy = np.log10(np.mean(blocks * blocks, axis=1) + 1e-10)
    onset = np.maximum(0.0, np.diff(energy, prepend=energy[0]))
    period = 60.0 / bpm / hop
    best, best_phase = -1.0, 0.0
    for phase in np.arange(0, period, 0.5):
        idx = np.round(np.arange(phase, count, period)).astype(int)
        idx = idx[idx < count]
        score = float(onset[idx].sum()) / max(1, len(idx))
        if score > best:
            best, best_phase = score, float(phase)
    beats = np.arange(best_phase, count, period)
    strong, strong_k = -1.0, 0
    for k in range(4):
        idx = np.round(beats[k::4]).astype(int)
        idx = idx[idx < count]
        score = float(onset[idx].sum() + energy[idx].sum() * 0.01) / max(1, len(idx))
        if score > strong:
            strong, strong_k = score, k
    mean = float(onset.mean()) + 1e-9
    first = t0 + best_phase * hop
    return {"bpm": bpm, "period": round(60.0 / bpm, 4), "first_beat": round(first, 3),
            "first_downbeat": round(first + strong_k * 60.0 / bpm, 3), "downbeat_every": 4,
            "confidence": round(min(1.0, best / mean / 4), 2)}


# --------------------------------------------------------------------------- #
# Сжатие
# --------------------------------------------------------------------------- #


def choose_step(duration: float) -> float:
    """Шаг громкости: не больше MAX_POINTS значений, 0.1 / 0.25 / 0.5 / 1 с."""
    for step in (0.1, 0.25, 0.5, 1.0, 2.0, 5.0):
        if duration / step <= MAX_POINTS:
            return step
    return 10.0


def sparkline(values: list[float], lo: float, hi: float) -> str:
    out = []
    for v in values:
        u = 0.0 if hi <= lo else (min(hi, max(lo, v)) - lo) / (hi - lo)
        out.append(SPARK[min(len(SPARK) - 1, int(u * len(SPARK)))])
    return "".join(out)


def resample(values: Any, hop: float, step: float, to_src: Any = None, duration: float | None = None) -> list[float]:
    """Значения по кадрам ``hop`` → по шагу ``step`` (максимум в шаге). ``to_src(t)`` — перевод
    времени выхода во время массива (шкала clip → куски подряд)."""
    import numpy as np

    arr = np.asarray(values)
    n = len(arr)
    total = duration if duration is not None else n * hop
    out = []
    t = 0.0
    while t < total - 1e-9:
        a, b = t, min(total, t + step)
        if to_src is not None:
            a, b = to_src(a), to_src(b)
            if b <= a:
                b = a + hop
        i0, i1 = int(a / hop), max(int(a / hop) + 1, int(math.ceil(b / hop)))
        part = arr[max(0, min(n - 1, i0)): max(1, min(n, i1))]
        out.append(float(np.max(part)) if len(part) else -70.0)
        t += step
    return out


# --------------------------------------------------------------------------- #
# Анализ одного звука
# --------------------------------------------------------------------------- #


def analyze_pcm(x: Any, rate: int = RATE, *, speech: list[tuple[float, float]] | None = None,
                speech_audio: Any = None, words: list[dict] | None = None) -> dict:
    """Карта звука массива ``x`` (моно float, ``rate`` Гц) — время от его начала.

    ``speech`` — готовые отрезки речи (тесты, свой VAD); иначе VAD по ``speech_audio`` (дорожка
    голоса) или по ``x``. ``words`` — слова субтитров в том же времени (для точек склейки).
    Ответ — сырой (секунды этого звука, массивы по кадрам ``feat``); ``build_view`` сжимает."""
    import numpy as np

    feat = frame_features(x, rate)
    duration = len(np.asarray(x)) / rate
    vad = "given"
    if speech is None:
        speech, vad = vad_segments(speech_audio if speech_audio is not None else x, rate)
    n = len(feat["rms_db"])
    mask = _mask(speech, n, feat["hop"])
    hits = find_hits(feat, mask)
    spans = label_spans(feat, mask)
    pauses = find_pauses(speech, duration, feat, hits)
    cuts = cut_points(speech, words, duration)
    music_sec = sum(b - a for a, b, lab in spans if lab == "музыка?")
    beats = beat_grid(x, rate) if music_sec >= 8 else None
    loud = feat["lufs"]
    heard = loud[feat["rms_db"] >= SILENCE_DB]
    info = {
        "integrated_lufs": round(float(-0.691 + 10 * np.log10(np.mean(10 ** ((heard + 0.691) / 10)))), 1)
        if len(heard) else -70.0,
        "speech_lufs": round(float(np.median(loud[mask])), 1) if mask.any() else None,
        "peak_db": round(float(20 * np.log10(np.max(np.abs(np.asarray(x))) + 1e-9)), 1) if duration else -90.0,
        "speech_share": round(float(mask.mean()), 2) if n else 0.0,
    }
    return {"duration": round(duration, 3), "feat": feat, "speech": speech, "vad": vad, "hits": hits,
            "spans": spans, "pauses": pauses, "cuts": cuts, "beats": beats, "info": info}


def build_view(raw: dict, *, to_out: Any = None, to_src: Any = None, duration: float | None = None,
               step: float | None = None) -> dict:
    """Сжатая карта для агента: времена через ``to_out`` (куски подряд → шкала ответа), громкость —
    шагом ``step`` по шкале ответа (``to_src`` — обратно)."""

    def out_t(t: float) -> float:
        return round(float(t if to_out is None else to_out(t)), 2)

    total = float(duration if duration is not None else raw["duration"])
    step = step or choose_step(total)
    lufs = resample(raw["feat"]["lufs"], raw["feat"]["hop"], step, to_src, total)
    lo, hi = -50.0, max(-10.0, max(lufs, default=-10.0))
    spans = []
    for a, b, lab in raw["spans"]:
        a2, b2 = out_t(a), out_t(b)
        if b2 - a2 >= 0.05:
            if spans and spans[-1][2] == lab and a2 - spans[-1][1] < 0.05:
                spans[-1][1] = b2
            else:
                spans.append([a2, b2, lab])
    view = {
        "duration": round(total, 2),
        "loudness": {"step": step, "lufs": [int(round(v)) if math.isfinite(v) else -70 for v in lufs],
                     "line": sparkline(lufs, lo, hi), "line_range_lufs": [lo, round(hi)], **raw["info"]},
        "segments": spans[:80],
        "speech": [[out_t(a), out_t(b)] for a, b in raw["speech"]][:80],
        "pauses": [{**p, "start": out_t(p["start"]), "end": out_t(p["end"])} for p in raw["pauses"]][:40],
        "hits": [{**h, "t": out_t(h["t"])} for h in raw["hits"]],
        "cut_points": [{"t": out_t(c["t"]), "why": c["why"]} for c in raw["cuts"]],
        "vad": raw["vad"],
    }
    if raw.get("beats"):
        beats = dict(raw["beats"])
        for key in ("first_beat", "first_downbeat"):
            beats[key] = out_t(beats[key])
        view["beats"] = beats
    return view


def track_view(raw: dict, *, to_src: Any = None, duration: float | None = None, step: float | None = None) -> dict:
    """Дорожка записи коротко: спарклайн громкости, медиана, доля речи, громкие места."""
    import numpy as np

    total = float(duration if duration is not None else raw["duration"])
    step = step or choose_step(total)
    lufs = resample(raw["feat"]["lufs"], raw["feat"]["hop"], step, to_src, total)
    rms = raw["feat"]["rms_db"]
    heard = rms[rms >= SILENCE_DB]
    return {"line": sparkline(lufs, -50.0, max(-10.0, max(lufs, default=-10.0))),
            "median_db": round(float(np.median(heard)), 1) if len(heard) else -90.0,
            "speech_share": raw["info"]["speech_share"], "integrated_lufs": raw["info"]["integrated_lufs"]}


# --------------------------------------------------------------------------- #
# J/L-кандидаты на стыках кусков
# --------------------------------------------------------------------------- #


def jl_candidates(joins: list[dict]) -> list[dict]:
    """По каждому стыку: речь у краёв и за краями кусков (в исходнике) → совет J или L.

    ``joins`` — ``[{join, at, piece_a, piece_b, a_tail_speech, b_head_speech, after_a, before_b,
    b_hit}]``: ``a_tail_speech`` — сколько секунд до конца куска A ещё говорят (0 — тишина в конце),
    ``b_head_speech`` — через сколько секунд после начала B начинается речь (None — нет речи в
    первых 1.5 с), ``after_a`` — сколько секунд речь продолжается в исходнике после конца A,
    ``before_b`` — сколько секунд речь звучит в исходнике до начала B, ``b_hit`` — удар в первые
    0.4 с куска B (секунды от его начала)."""
    out = []
    for j in joins:
        item = {"join": j["join"], "at": j["at"]}
        a_speaks = (j.get("a_tail_speech") or 0) > 0
        b_onset = j.get("b_head_speech")
        after_a, before_b = float(j.get("after_a") or 0), float(j.get("before_b") or 0)
        if a_speaks and after_a >= 0.15 and (b_onset is None or b_onset >= min(1.5, after_a + 0.1)):
            lag = round(min(1.5, after_a + 0.1), 2)
            item["suggest"] = {"piece": j["piece_a"], "lag": lag}
            item["why"] = (f"фраза куска {j['piece_a']} обрывается на стыке и в исходнике идёт ещё {after_a:.2f} с — "
                           f"L: договорит под картинкой куска {j['piece_b']}")
        elif b_onset is not None and b_onset < 0.12 and before_b >= 0.15 and not a_speaks:
            lead = round(min(1.5, before_b), 2)
            item["suggest"] = {"piece": j["piece_b"], "lead": lead}
            item["why"] = (f"фраза куска {j['piece_b']} началась в исходнике на {before_b:.2f} с раньше — J: "
                           "начало слышно под концом прошлого куска")
        elif j.get("b_hit") is not None and not a_speaks:
            lead = round(min(0.6, float(j["b_hit"]) + 0.15), 2)
            item["suggest"] = {"piece": j["piece_b"], "lead": lead}
            item["why"] = "в начале куска удар — J: звук события раньше картинки, стык читается мягче"
        elif not a_speaks and b_onset is not None and b_onset < 0.4:
            item["suggest"] = {"piece": j["piece_b"], "lead": 0.25}
            item["why"] = "кусок A кончается тишиной, B сразу с речи — J 0.2–0.3 с: голос раньше картинки"
        elif a_speaks and b_onset is not None and b_onset < 0.3:
            item["why"] = "речь с обеих сторон стыка — J/L закроет слова; лучше trim_piece или без J/L"
        else:
            item["why"] = "стык по паузе — J/L не нужен"
        item["a_tail_speech"] = j.get("a_tail_speech")
        item["b_head_speech"] = b_onset
        out.append(item)
    return out


# --------------------------------------------------------------------------- #
# Чтение звука, кеш, очередь
# --------------------------------------------------------------------------- #


def _pcm_f32(path: Path) -> Any:
    import numpy as np

    from app.speech_onsets import wav_pcm

    raw = wav_pcm(path)
    return np.frombuffer(raw[: len(raw) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0


def _extract(source_path: Path, pieces: list[tuple[float, float]], tracks: Any, ops: list[dict]) -> Any:
    from app.clip_subtitles import build_clip_audio_args
    from app.render import _run_ffmpeg
    from app.settings import settings

    out = settings.tmp_dir / f"audiomap-{uuid4().hex}.wav"
    try:
        _run_ffmpeg(build_clip_audio_args(source_path, pieces, out, tracks, ops), timeout=60 * 10)
        return _pcm_f32(out)
    finally:
        out.unlink(missing_ok=True)


def _cache_file(key: dict) -> Path:
    from app.settings import settings

    digest = hashlib.sha256(json.dumps(key, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:20]
    folder = settings.runtime_dir / "audio_map"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{digest}.json"


def _file_stamp(path: Path) -> list:
    try:
        stat = path.stat()
        return [str(path), stat.st_size, int(stat.st_mtime)]
    except OSError:
        return [str(path)]


def _cached(key: dict, compute: Any) -> dict:
    path = _cache_file(key)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    value = compute()
    try:
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return value


def _mix_tracks(source: dict) -> list[dict]:
    from app.source_audio import source_mix

    return source_mix(source) or [{"track": 0, "gain_db": 0.0}]


def clip_audio_map(store: Any, clip_plan_id: int, *, from_sec: float | None = None, to_sec: float | None = None,
                   want_image: bool = False) -> tuple[dict, bytes | None]:
    """Карта звука клипа в шкале clip (до вырезов, с наплывами). → (карта, PNG или None)."""
    from app.audio_edit import plan_source_ops
    from app.heavy_queue import heavy_slot
    from app.source_audio import separate_speech, speech_mix
    from app.timebase import ClipClock, scale_note

    plan = store.get_clip_plan(clip_plan_id)
    source = store.get_source(int(plan["source_id"]))
    source_path = Path(str(source.get("local_path") or ""))
    if not source_path.is_file():
        raise ValueError("видео недоступно")
    segments = plan.get("segments") or []
    if not segments:
        raise ValueError("у клипа нет кусков")
    render = plan.get("render_settings") or {}
    clock = ClipClock(segments, render.get("transition"), render.get("cuts"))
    pieces = [(float(s["start_sec"]), float(s["end_sec"])) for s in segments]
    mix = _mix_tracks(source)
    ops = plan_source_ops(plan, source)
    words = (store.get_clip_plan_subtitles(clip_plan_id) or {}).get("words") or []
    speech_tracks = speech_mix(source) if separate_speech(source) else None
    from app.canvas import is_canvas

    canvas = is_canvas(source)
    key = {"v": VERSION, "kind": "clip", "file": _file_stamp(source_path), "pieces": pieces, "mix": mix,
           "speech": speech_tracks, "ops": ops, "words": len(words),
           "w": [round(float(w.get("end") or 0), 2) for w in words[:400]]}
    if canvas:
        # Холст: звук — слои (озвучка, звуки, музыка), основа молчит (app/canvas_audio.py).
        from app.canvas_audio import signature

        mix, ops, speech_tracks = [{"track": 0, "gain_db": 0.0}], [], None
        key.update(canvas=signature(store, plan), plan=int(clip_plan_id), mix=mix, ops=ops, speech=None)

    def compute() -> dict:
        import numpy as np

        with heavy_slot("audio_map", f"клип {clip_plan_id}"):
            if canvas:
                from app.canvas_audio import canvas_audio

                with canvas_audio(store, plan, "mix") as mixed:
                    x = _extract(mixed, pieces, None, [])
                with canvas_audio(store, plan, "speech") as speech:
                    voice = _extract(speech, pieces, None, [])
            else:
                x = _extract(source_path, pieces, mix, ops)
                voice = _extract(source_path, pieces, speech_tracks, ops) if speech_tracks else None
            raw = analyze_pcm(x, words=words, speech_audio=voice)
            tracks = {}
            if len(mix) > 1:
                for m in mix:
                    tracks[str(m["track"])] = analyze_pcm(_extract(source_path, pieces, [m], ops))
            probes = [] if canvas else _join_probes(source, source_path, segments, clock, raw, speech_tracks or mix, ops)
        plain_to_clip = clock.plain_to_clip
        view = build_view(raw, to_out=plain_to_clip, to_src=clock.clip_to_plain, duration=clock.clip_duration)
        if tracks:
            view["tracks"] = {t: track_view(r, to_src=clock.clip_to_plain, duration=clock.clip_duration)
                              for t, r in tracks.items()}
        if probes:
            view["jl"] = jl_candidates(probes)
        rms = raw["feat"]["rms_db"]
        view["_image"] = {"rms": [round(float(v), 1) for v in np.asarray(rms)[::2]], "hop": raw["feat"]["hop"] * 2,
                          "speech": raw["speech"], "hits": [h["t"] for h in raw["hits"]]}
        return view

    view = _cached(key, compute)
    image_data = view.pop("_image", None)
    out = {"time": "clip", "time_note": scale_note("clip"), "clip_duration": clock.clip_duration,
           "finished_duration": clock.final_duration, **_window(view, from_sec, to_sec)}
    music = _music_beats(store, render, clock)
    if music:
        out["music_beats"] = music
    out["note"] = NOTE
    png = _image(image_data, [clock.clip_starts[i] for i in range(1, len(segments))], clock.clip_to_plain,
                 clock.clip_duration, from_sec, to_sec) if want_image and image_data else None
    return out, png


def source_audio_map(store: Any, source_id: int, from_sec: float | None, to_sec: float | None,
                     track: int | None = None, want_image: bool = False) -> tuple[dict, bytes | None]:
    """Карта звука окна исходника (шкала source, ≤ 5 мин за вызов)."""
    from app.heavy_queue import heavy_slot
    from app.source_audio import separate_speech, speech_mix

    source = store.get_source(int(source_id))
    source_path = Path(str(source.get("local_path") or ""))
    if not source_path.is_file():
        raise ValueError("файла видео нет")
    total = float(source.get("duration_sec") or 0)
    lo = max(0.0, float(from_sec or 0.0))
    hi = float(to_sec) if to_sec is not None else lo + 60.0
    if total:
        hi = min(hi, total)
    notes = []
    if hi - lo > MAX_SOURCE_WINDOW:
        hi = lo + MAX_SOURCE_WINDOW
        notes.append(f"окно урезано до {MAX_SOURCE_WINDOW:g} с — дальше следующим вызовом с from_sec={hi:.0f}")
    if hi - lo < 1.0:
        raise ValueError("окно короче секунды")
    mix = [{"track": int(track), "gain_db": 0.0}] if track is not None else _mix_tracks(source)
    speech_tracks = speech_mix(source) if separate_speech(source) and track is None else None
    key = {"v": VERSION, "kind": "source", "file": _file_stamp(source_path), "lo": round(lo, 2), "hi": round(hi, 2),
           "mix": mix, "speech": speech_tracks}

    def compute() -> dict:
        import numpy as np

        with heavy_slot("audio_map", f"исходник {source_id}"):
            x = _extract(source_path, [(lo, hi)], mix, [])
            voice = _extract(source_path, [(lo, hi)], speech_tracks, []) if speech_tracks else None
            raw = analyze_pcm(x, speech_audio=voice)
            tracks = {}
            if len(mix) > 1:
                for m in mix:
                    tracks[str(m["track"])] = analyze_pcm(_extract(source_path, [(lo, hi)], [m], []))
        view = build_view(raw, to_out=lambda t: lo + t, to_src=lambda t: t - lo, duration=hi - lo)
        view["duration"] = round(hi - lo, 2)
        if tracks:
            view["tracks"] = {t: track_view(r) for t, r in tracks.items()}
        view["_image"] = {"rms": [round(float(v), 1) for v in np.asarray(raw["feat"]["rms_db"])[::2]],
                          "hop": raw["feat"]["hop"] * 2, "speech": raw["speech"], "hits": [h["t"] for h in raw["hits"]]}
        return view

    view = _cached(key, compute)
    image_data = view.pop("_image", None)
    out = {"time": "source", "source_id": source["id"], "project_id": source["id"], "from_sec": round(lo, 2), "to_sec": round(hi, 2), **view,
           "note": NOTE.replace("шкала clip", "шкала source").split(". Склейки")[0]
           + ". Время — секунды исходника (create_clip, trim_piece)" + ("; " + "; ".join(notes) if notes else "")}
    png = _image(image_data, [], lambda t: t - lo, hi - lo, lo, hi, offset=lo) if want_image and image_data else None
    return out, png


NOTE = ("шкала clip: времена годятся прямо в mute_audio, set_audio_fx, set_audio_keys, apply_cuts. loudness.lufs — "
        "громкость (LUFS, окно 0.4 с) шагом step, line — то же значками ▁…█; segments — [start, end, что звучит] "
        "(«?» — эвристика, проверь listen); hits — удары (атака и скачок громкости jump_db) — места для титра/SFX/"
        "склейки; pauses — паузы речи (before_punch — пауза перед более громкой фразой или ударом: её не режь, "
        "под неё — music_dips); cut_points — где резать (конец фразы +3 кадра). Склейки кусков: jl — совет J/L "
        "(set_audio_joins). beats/music_beats — сетка долей (bpm, first_beat, first_downbeat каждые 4 доли)")


def _window(view: dict, lo: float | None, hi: float | None) -> dict:
    """Окно from..to: события только из него, громкость — её отрезок."""
    if lo is None and hi is None:
        return view
    a, b = float(lo or 0.0), float(hi if hi is not None else view["duration"])
    out = dict(view)

    def inside(t: float) -> bool:
        return a - 1e-6 <= t <= b + 1e-6

    step = view["loudness"]["step"]
    i0, i1 = int(a / step), int(math.ceil(b / step))
    loud = dict(view["loudness"])
    loud["lufs"] = loud["lufs"][i0:i1]
    loud["line"] = loud["line"][i0:i1]
    loud["from"] = round(i0 * step, 2)
    out["loudness"] = loud
    out["segments"] = [s for s in view["segments"] if s[1] > a and s[0] < b]
    out["speech"] = [s for s in view["speech"] if s[1] > a and s[0] < b]
    for name in ("pauses",):
        out[name] = [p for p in view[name] if p["end"] > a and p["start"] < b]
    for name in ("hits", "cut_points"):
        out[name] = [p for p in view[name] if inside(p["t"])]
    if "jl" in view:
        out["jl"] = [j for j in view["jl"] if inside(j["at"])]
    out["window"] = [round(a, 2), round(b, 2)]
    return out


def _speech_in(x: Any, rate: int = RATE) -> list[tuple[float, float]]:
    return vad_segments(x, rate)[0] if len(x) else []


def _join_probes(source: dict, source_path: Path, segments: list[dict], clock: Any, raw: dict, tracks: Any,
                 ops: list[dict]) -> list[dict]:
    """Речь у краёв кусков и за ними (в исходнике) — входы ``jl_candidates``."""
    out = []
    plain_starts = clock.pre.plain_starts
    for k in range(len(segments) - 1):
        a, b = segments[k], segments[k + 1]
        a_end, b_start = float(a["end_sec"]), float(b["start_sec"])
        bound = plain_starts[k + 1]
        # Сколько из последних 0.3 с куска A звучит речь (0 — кончается паузой).
        a_tail = round(sum(max(0.0, min(e, bound) - max(s, bound - 0.3)) for s, e in raw["speech"]), 2)
        head = [(s, e) for s, e in raw["speech"] if s < bound + JL_PROBE_SEC and e > bound]
        b_head = round(max(0.0, min(s for s, _e in head) - bound), 2) if head else None
        after = _speech_in(_extract(source_path, [(a_end, a_end + JL_PROBE_SEC)], tracks, ops))
        after_a = round(after[0][1], 2) if after and after[0][0] < 0.08 else 0.0
        start = max(0.0, b_start - JL_PROBE_SEC)
        before = _speech_in(_extract(source_path, [(start, b_start)], tracks, ops)) if b_start > 0.2 else []
        length = b_start - start
        before_b = round(length - before[-1][0], 2) if before and before[-1][1] > length - 0.08 else 0.0
        hit = next((h["t"] - bound for h in raw["hits"] if bound <= h["t"] <= bound + 0.4), None)
        out.append({"join": k, "at": round(clock.clip_starts[k + 1], 2), "piece_a": k, "piece_b": k + 1,
                    "a_tail_speech": a_tail, "b_head_speech": b_head, "after_a": after_a, "before_b": before_b,
                    "b_hit": None if hit is None else round(hit, 2)})
    return out


def _music_beats(store: Any, render: dict, clock: Any) -> dict | None:
    """Сетка долей фоновой музыки клипа (set_music) — в секундах готового ролика (кеш по треку)."""
    if not render.get("music_on") or not render.get("track_id"):
        return None
    try:
        track = store.get_audio_track(int(render["track_id"]))
    except (KeyError, TypeError, ValueError):
        return None
    path = Path(str(track.get("file_path") or ""))
    if not path.is_file():
        return None
    start = float(render.get("music_start_sec") or 0.0)
    length = min(120.0, max(10.0, clock.final_duration + 1.0))
    key = {"v": VERSION, "kind": "music", "file": _file_stamp(path), "start": round(start, 2), "len": round(length, 1)}

    def compute() -> dict:
        from app.render import _run_ffmpeg
        from app.settings import settings

        out = settings.tmp_dir / f"beats-{uuid4().hex}.wav"
        try:
            _run_ffmpeg(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-t",
                         f"{length:.3f}", "-i", str(path), "-vn", "-ac", "1", "-ar", str(RATE), str(out)], timeout=120)
            grid = beat_grid(_pcm_f32(out)) or {}
        finally:
            out.unlink(missing_ok=True)
        return grid

    grid = _cached(key, compute)
    if not grid:
        return None
    return {**grid, "time": "final", "track_id": int(render["track_id"]),
            "note": "доли музыки в секундах ГОТОВОГО ролика: first_beat + k·period; сильные — first_downbeat + 4k·period"}


def _image(data: dict, joins: list[float], to_src: Any, duration: float, lo: float | None, hi: float | None,
           offset: float = 0.0) -> bytes | None:
    """Картинка карты (numpy + zlib, без OpenCV): громкость столбиками (−60…0 дБ), речь — зелёная
    полоса, удары — красные риски, стыки кусков — жёлтые, внизу засечки каждую секунду (длинные — 5 с).
    Время речи/ударов — куски подряд (``to_src`` переводит шкалу ответа туда)."""
    import numpy as np

    width, height = 1200, 260
    img = np.full((height, width, 3), 24, np.uint8)
    a = float(lo if lo is not None else 0.0) - (offset if lo is not None else 0.0)
    b = float(hi - offset if hi is not None else duration)
    if b - a <= 0:
        return None
    rms, hop = np.asarray(data["rms"], dtype=float), float(data["hop"])
    base = height - 40
    for px in range(width):
        i = int(to_src(a + (b - a) * px / (width - 1)) / hop)
        if 0 <= i < len(rms):
            v = (max(-60.0, min(0.0, float(rms[i]))) + 60) / 60
            img[int(base - v * (base - 10)):base, px] = (90, 170, 200)

    def cols(t0: float, t1: float) -> slice:
        """Столбцы картинки для отрезка кусков подряд [t0, t1] (через шкалу ответа)."""
        xs = [px for px in range(width) if t0 <= to_src(a + (b - a) * px / (width - 1)) <= t1]
        return slice(xs[0], xs[-1] + 1) if xs else slice(0, 0)

    for s, e in data.get("speech") or []:
        img[base + 8:base + 18, cols(s, e)] = (90, 200, 90)
    for t in data.get("hits") or []:
        sl = cols(t - hop, t + hop)
        img[10:base, sl.start:sl.start + 2] = (230, 60, 60)
    for t in joins:
        x = int((t - a) / (b - a) * (width - 1))
        if 0 <= x < width:
            img[:, x] = (255, 220, 0)
    t = math.ceil(a)
    while t <= b:
        x = int((t - a) / (b - a) * (width - 1))
        long = round(t + offset) % 5 == 0
        img[height - (16 if long else 8):height, x] = (220, 220, 220) if long else (140, 140, 140)
        t += 1.0 if b - a <= 120 else 10.0
    return _png(img)


def _png(img: Any) -> bytes:
    """RGB-массив → PNG (без сторонних библиотек)."""
    import struct
    import zlib

    height, width = img.shape[:2]
    raw = b"".join(b"\x00" + img[y].tobytes() for y in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
