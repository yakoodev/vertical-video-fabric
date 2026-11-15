# -*- coding: utf-8 -*-
"""Song analysis for AMV edits: onset envelopes per band, onsets, tempo, a constant beat grid, bar energy,
intensity levels per bar and drops.

All the math is plain numpy (no librosa). Pure functions take arrays; analyze_song() wires them to a file."""
from __future__ import annotations

import numpy as np

HOP_S = 0.005  # 5 ms envelope step

BANDS = {"low": (30, 150), "mid": (150, 2500), "high": (2500, 11000)}


def stft_mag(x: np.ndarray, sr: int, hop: int, nfft: int = 2048) -> tuple[np.ndarray, np.ndarray]:
    pad = np.pad(x.astype(np.float32), (nfft // 2, nfft // 2))
    n = 1 + (len(pad) - nfft) // hop
    fr = np.lib.stride_tricks.as_strided(pad, shape=(n, nfft), strides=(pad.strides[0] * hop, pad.strides[0]))
    S = np.abs(np.fft.rfft(fr * np.hanning(nfft).astype(np.float32), axis=1)).astype(np.float32)
    return S, np.fft.rfftfreq(nfft, 1 / sr)


def band_flux(S: np.ndarray, f: np.ndarray, lo: float, hi: float, gain: float = 100.0) -> np.ndarray:
    """Half-wave rectified log-spectral flux inside [lo, hi) Hz."""
    m = (f >= lo) & (f < hi)
    L = np.log1p(gain * S[:, m])
    return np.concatenate([[0.0], np.maximum(0, np.diff(L, axis=0)).sum(1)]).astype(np.float32)


def normalize_env(v: np.ndarray, fps: float, win_s: float = 0.16) -> np.ndarray:
    """Remove the slow trend (moving average), rectify, scale to 0..1."""
    nb = max(1, int(win_s * fps))
    v = v - np.convolve(v, np.ones(nb) / nb, mode="same")
    v = np.maximum(v, 0)
    return v / (v.max() + 1e-9)


def pick_peaks(env: np.ndarray, fps: float, min_gap: float = 0.07, rel: float = 1.4, floor: float = 0.04,
               base_s: float = 0.5) -> list[tuple[float, float]]:
    """Local maxima above rel x (moving average) and above floor (of the max); merged closer than min_gap.

    Returns [(time_s, strength 0..1)]."""
    env = np.asarray(env, np.float32)
    if env.size < 3:
        return []
    mx = float(env.max()) or 1.0
    nb = max(1, int(base_s * fps))
    base = np.convolve(env, np.ones(nb) / nb, mode="same")
    r = max(1, int(round(0.03 * fps)))
    out: list[tuple[int, float]] = []
    for i in range(1, len(env) - 1):
        lo, hi = max(0, i - r), min(len(env), i + r + 1)
        if env[i] < env[lo:hi].max() or env[i] <= 0:
            continue
        if env[i] < rel * base[i] or env[i] < floor * mx:
            continue
        if out and (i - out[-1][0]) / fps < min_gap:
            if env[i] > out[-1][1]:
                out[-1] = (i, float(env[i]))
            continue
        out.append((i, float(env[i])))
    return [(round(i / fps, 4), round(v / mx, 4)) for i, v in out]


def tempo_autocorr(env: np.ndarray, fps: float, bpm_range: tuple[float, float] = (70, 180),
                   prefer: tuple[float, float] = (85, 165)) -> float:
    """Tempo (BPM) by autocorrelation of an onset envelope, with octave check (1x + 0.5 * 2x lag)."""
    seg = np.asarray(env, np.float64) - float(np.mean(env))
    ac = np.correlate(seg, seg, mode="full")[len(seg) - 1:]
    best = None
    for bpm in np.arange(bpm_range[0], bpm_range[1], 0.1):
        lag = 60.0 / bpm * fps
        i, j = int(round(lag)), int(round(2 * lag))
        if j >= len(ac):
            continue
        sc = ac[i] + 0.5 * ac[j]
        if prefer[0] <= bpm <= prefer[1]:
            sc *= 1.05
        if best is None or sc > best[0]:
            best = (sc, bpm)
    if best is None:
        raise ValueError("envelope too short for the tempo range")
    return float(best[1])


def tempo_prior(bpm: np.ndarray | float, center: float = 130.0, width: float = 0.9) -> np.ndarray:
    """Log-normal tempo preference (octave errors are resolved toward ~center BPM)."""
    return np.exp(-0.5 * (np.log2(np.asarray(bpm, np.float64) / center) / width) ** 2)


def tempo_curve(env: np.ndarray, fps: float, win_s: float = 8.0, hop_s: float = 2.0,
                bpm_range: tuple[float, float] = (60, 200), switch: float = 2.0) -> tuple[np.ndarray, np.ndarray]:
    """Local tempo over time: windowed autocorrelation → candidate scores per window, then a Viterbi path that
    pays `switch` per octave of tempo change (mashups and tempo shifts are followed, jitter is not).

    Returns (window centre times, bpm per window)."""
    env = np.asarray(env, np.float64)
    bpms = np.arange(bpm_range[0], bpm_range[1], 0.5)
    w, h = int(win_s * fps), max(1, int(hop_s * fps))
    starts = list(range(0, max(1, len(env) - w + 1), h)) or [0]
    obs = []
    for s in starts:
        seg = env[s:s + w] - env[s:s + w].mean()
        ac = np.correlate(seg, seg, mode="full")[len(seg) - 1:]
        ac = ac / (ac[0] + 1e-9)
        sc = []
        for b in bpms:
            lag = 60.0 / b * fps
            i, j = int(round(lag)), int(round(2 * lag))
            sc.append((ac[i] if i < len(ac) else 0) + 0.5 * (ac[j] if j < len(ac) else 0))
        sc = np.maximum(np.array(sc), 0) * tempo_prior(bpms)
        obs.append(sc / (sc.max() + 1e-9))
    obs = np.array(obs)
    lb = np.log2(bpms)
    trans = -switch * np.abs(lb[:, None] - lb[None, :])
    score = np.log(obs[0] + 1e-3)
    back = []
    for k in range(1, len(obs)):
        m = score[:, None] + trans
        bi = m.argmax(0)
        back.append(bi)
        score = m[bi, np.arange(len(bpms))] + np.log(obs[k] + 1e-3)
    path = [int(score.argmax())]
    for bi in reversed(back):
        path.append(int(bi[path[-1]]))
    path.reverse()
    centers = np.array([(s + w / 2) / fps for s in starts])
    return centers, bpms[path]


def track_beats(env: np.ndarray, fps: float, period_s: np.ndarray | float, tightness: float = 120.0) -> np.ndarray:
    """Dynamic-programming beat tracker (Ellis 2007): beats sit on envelope peaks while consecutive intervals stay
    close to the local period. period_s — scalar or one value per envelope frame. Returns beat times (s)."""
    env = np.asarray(env, np.float64)
    n = len(env)
    P = np.broadcast_to(np.asarray(period_s, np.float64) * fps, (n,)).astype(np.float64)
    score = env.copy()
    back = np.full(n, -1, int)
    for t in range(n):
        p = P[t]
        lo, hi = int(t - 2 * p), int(t - p / 2)
        if hi <= 0:
            continue
        lo = max(0, lo)
        tau = np.arange(lo, hi)
        pen = -tightness * np.log((t - tau) / p) ** 2
        cand = score[lo:hi] + pen
        k = int(np.argmax(cand))
        if cand[k] > 0:
            score[t] = env[t] + cand[k]
            back[t] = lo + k
    # end: best score in the last period
    tail = int(max(1, P[-1]))
    t = int(np.argmax(score[n - tail:])) + n - tail
    beats = [t]
    while back[t] >= 0:
        t = back[t]
        beats.append(t)
    return np.array(beats[::-1], np.float64) / fps


def fit_grid(env: np.ndarray, fps: float, bpm: float, t0: float, t1: float, tol: float = 0.006,
             phase_step: float = 0.004) -> tuple[float, float]:
    """Constant beat grid (period, phase) maximizing envelope sum on beats. Returns (period_s, first_beat_s)."""
    period = 60.0 / bpm
    top = None
    for p in np.linspace(period * (1 - tol), period * (1 + tol), 25):
        for ph in np.arange(0, p, phase_step):
            bt = np.arange(t0 + ph, t1, p)
            idx = np.clip((bt * fps).round().astype(int), 0, len(env) - 1)
            sc = float(env[idx].sum() + 0.5 * env[np.clip(idx + 1, 0, len(env) - 1)].sum()
                       + 0.5 * env[np.clip(idx - 1, 0, len(env) - 1)].sum())
            if top is None or sc > top[0]:
                top = (sc, p, t0 + ph)
    return float(top[1]), float(top[2])


def downbeat_phase(low_env: np.ndarray, fps: float, beats: np.ndarray, per_bar: int = 4) -> int:
    """Which beat (0..per_bar-1) starts the bar: the phase with the most low-band (kick) energy."""
    idx = np.clip((beats * fps).astype(int), 0, len(low_env) - 1)
    acc = [low_env[idx[k::per_bar]].sum() for k in range(per_bar)]
    return int(np.argmax(acc))


def energy_per_beat(rms: np.ndarray, fps: float, beats: np.ndarray) -> np.ndarray:
    out = []
    for k, b in enumerate(beats):
        e = beats[k + 1] if k + 1 < len(beats) else b + (beats[1] - beats[0] if len(beats) > 1 else 0.5)
        a, z = int(b * fps), max(int(b * fps) + 1, int(e * fps))
        out.append(float(np.sqrt(np.mean(rms[a:z] ** 2))) if a < len(rms) else 0.0)
    return np.array(out, np.float32)


def levels(values: np.ndarray, n: int = 4) -> np.ndarray:
    """Quantize to 0..n-1 by quantiles of the values (relative intensity)."""
    v = np.asarray(values, np.float64)
    if v.size == 0:
        return np.zeros(0, int)
    qs = np.quantile(v, np.linspace(0, 1, n + 1)[1:-1])
    return np.searchsorted(qs, v, side="right").astype(int)


def detect_drops(bar_energy: np.ndarray, bar_low: np.ndarray | None = None, ratio: float = 1.45,
                 look_back: int = 2, look_ahead: int = 2, min_gap_bars: int = 4) -> list[int]:
    """Bars where the music «drops in»: mean energy of the next bars / previous bars >= ratio, and the new level is
    at least the median. bar_low (kick energy) sharpens it — a drop brings the bass back. Returns bar indices."""
    e = np.asarray(bar_energy, np.float64)
    if bar_low is not None:
        lo = np.asarray(bar_low, np.float64)
        e = 0.6 * e / (e.max() + 1e-9) + 0.4 * lo / (lo.max() + 1e-9)
    med = float(np.median(e)) if e.size else 0.0
    cand = []
    for i in range(look_back, len(e)):
        prev = e[i - look_back:i].mean()
        nxt = e[i:i + look_ahead].mean()
        if nxt >= med and nxt >= ratio * max(prev, 1e-9):
            cand.append((nxt / max(prev, 1e-9), i))
    cand.sort(reverse=True)
    picked: list[int] = []
    for _, i in cand:
        if all(abs(i - j) >= min_gap_bars for j in picked):
            picked.append(i)
    return sorted(picked)


def analyze_song(x: np.ndarray, sr: int, bpm_hint: float | None = None, bpm_range=(60, 200), grid: str = "dp") -> dict:
    """Everything the assembler needs, in seconds of the song."""
    hop = max(1, int(round(sr * HOP_S)))
    fps = sr / hop
    S, f = stft_mag(x, sr, hop)
    env = {k: normalize_env(band_flux(S, f, lo, hi), fps) for k, (lo, hi) in BANDS.items()}
    full = normalize_env(band_flux(S, f, 30, 11000), fps)
    on = full + 0.6 * env["low"] + 0.3 * env["high"]
    on = on / (on.max() + 1e-9)
    pad = np.pad(x.astype(np.float32), (1024, 1024))
    n = 1 + (len(pad) - 2048) // hop
    fr = np.lib.stride_tricks.as_strided(pad, shape=(n, 2048), strides=(pad.strides[0] * hop, pad.strides[0]))
    rms = np.sqrt((fr ** 2).mean(1)).astype(np.float32)
    low_rms = np.sqrt((S[:, (f >= 30) & (f < 150)] ** 2).sum(1)).astype(np.float32)
    dur = len(x) / sr
    # tempo on a decimated envelope (10 ms) keeps the autocorrelation cheap
    dec = 2
    on_d = on[: len(on) // dec * dec].reshape(-1, dec).mean(1)
    if grid == "const":
        bpm = bpm_hint or tempo_autocorr(on_d, fps / dec, bpm_range)
        period, first = fit_grid(on, fps, bpm, 0.0, dur)
        while first - period > 0:
            first -= period
        beats = np.arange(first, dur, period)
        tempo = {"t": [0.0], "bpm": [round(60 / period, 2)]}
    else:
        if bpm_hint:
            tc, tb = np.array([0.0]), np.array([float(bpm_hint)])
        else:
            tc, tb = tempo_curve(on_d, fps / dec, bpm_range=bpm_range)
        tf = np.arange(len(on_d)) / (fps / dec)
        per = 60.0 / np.interp(tf, tc, tb)
        beats = track_beats(on_d, fps / dec, per)
        period = float(np.median(np.diff(beats))) if len(beats) > 2 else 60.0 / float(tb[0])
        tempo = {"t": [round(float(v), 2) for v in tc], "bpm": [round(float(v), 2) for v in tb]}
    k0 = downbeat_phase(env["low"], fps, beats)
    downbeats = beats[k0::4]
    e_beat = energy_per_beat(rms, fps, beats)
    l_beat = energy_per_beat(low_rms, fps, beats)
    bars = []
    for i, b in enumerate(downbeats):
        j = k0 + 4 * i
        sel = slice(j, min(len(beats), j + 4))
        end = float(beats[j + 4]) if j + 4 < len(beats) else float(b + 4 * period)
        bars.append({"t": round(float(b), 4), "end": round(end, 4), "beat0": j,
                     "energy": round(float(e_beat[sel].mean()), 5), "low": round(float(l_beat[sel].mean()), 5)})
    be = np.array([b["energy"] for b in bars])
    bl = np.array([b["low"] for b in bars])
    lv = levels(be)
    for b, v in zip(bars, lv):
        b["level"] = int(v)
    drops = detect_drops(be, bl)
    onsets = pick_peaks(on, fps)
    kicks = pick_peaks(env["low"], fps, min_gap=0.12, rel=1.6, floor=0.15)
    snares = pick_peaks(env["mid"] * 0.5 + env["high"] * 0.5, fps, min_gap=0.12, rel=1.6, floor=0.2)
    # beat strength = onset envelope around each beat (±25 ms)
    r = int(0.025 * fps)
    bstr = [float(on[max(0, int(b * fps) - r): int(b * fps) + r + 1].max()) if int(b * fps) < len(on) else 0.0
            for b in beats]
    return {"duration": round(dur, 4), "bpm": round(60 / period, 3), "period": round(period, 5),
            "beats": [round(float(b), 4) for b in beats], "beat_strength": [round(v, 3) for v in bstr],
            "beat_energy": [round(float(v), 5) for v in e_beat],
            "downbeat_phase": k0, "tempo": tempo, "bars": bars, "drops": [bars[i]["t"] for i in drops],
            "onsets": onsets, "kicks": kicks, "snares": snares,
            "env_fps": fps, "rms_db": [round(float(20 * np.log10(v + 1e-6)), 1) for v in rms[::10]]}


def nearest(ts: list[float] | np.ndarray, t: float) -> tuple[int, float]:
    """(index, signed offset t - ts[index]) of the nearest grid time."""
    a = np.asarray(ts, np.float64)
    if a.size == 0:
        return -1, float("inf")
    i = int(np.argmin(np.abs(a - t)))
    return i, float(t - a[i])
