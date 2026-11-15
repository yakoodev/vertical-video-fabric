# -*- coding: utf-8 -*-
"""Reference-edit analysis: how a finished AMV is cut against its own song.

ref_stats() is pure (scan arrays + song analysis → numbers); scan_reference() runs the scan and writes
work/<name>.ref.json plus contact sheets. Numbers it reports:
  shot lengths (s, frames, beats), cut-to-beat offsets, cut density per song intensity level and around drops,
  flashes, zoom punches, shake (camera jitter), in-shot speed variation (velocity proxy), motion matching across
  cuts (direction / eye-trace vs a shuffled baseline), luma/contrast/saturation."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from . import audio, check, media, shots


def _events(v: np.ndarray, thr: float, min_gap: int) -> list[int]:
    idx = []
    for i in range(1, len(v) - 1):
        if v[i] >= thr and v[i] >= v[i - 1] and v[i] >= v[i + 1]:
            if idx and i - idx[-1] < min_gap:
                if v[i] > v[idx[-1]]:
                    idx[-1] = i
                continue
            idx.append(i)
    return idx


def beat_offsets(times: list[float], beats: list[float], period: float) -> dict:
    """Offsets (ms) of event times to the nearest beat and to the nearest half-beat."""
    if not times:
        return {"n": 0}
    b = np.asarray(beats)
    half = np.sort(np.concatenate([b, b + period / 2]))
    off = np.array([audio.nearest(b, t)[1] for t in times]) * 1000
    offh = np.array([audio.nearest(half, t)[1] for t in times]) * 1000
    on_beat = np.abs(off) <= 45
    on_half = (np.abs(offh) <= 45) & ~on_beat
    return {"n": len(times), "median_abs_ms": round(float(np.median(np.abs(off))), 1),
            "mean_ms": round(float(off.mean()), 1),
            "within_1f_beat": round(float(on_beat.mean()), 3), "on_half_beat": round(float(on_half.mean()), 3),
            "off_grid": round(float(1 - on_beat.mean() - on_half.mean()), 3)}


def onset_offsets(times: list[float], onsets: list[tuple[float, float]], min_strength: float = 0.25) -> dict:
    ts = [t for t, s in onsets if s >= min_strength]
    if not times or not ts:
        return {"n": 0}
    off = np.array([audio.nearest(ts, t)[1] for t in times]) * 1000
    return {"n": len(times), "median_abs_ms": round(float(np.median(np.abs(off))), 1),
            "within_45ms": round(float((np.abs(off) <= 45).mean()), 3),
            "early_frac": round(float((off < -10).mean()), 3), "late_frac": round(float((off > 10).mean()), 3)}


def accent_alignment(times: list[float], onsets: list, duration: float, tol: float = 0.045,
                     levels: tuple[float, ...] = (0.1, 0.2, 0.35, 0.5), seed: int = 0) -> dict:
    """Share of events within tol of an onset of strength >= level, against uniformly random times (chance)."""
    rng = np.random.default_rng(seed)
    rnd = rng.uniform(0.5, max(1.0, duration - 0.5), 4000)
    out = {}
    for lv in levels:
        ts = np.array([t for t, s in onsets if s >= lv])
        if ts.size == 0 or not times:
            continue
        real = float(np.mean([np.min(np.abs(ts - t)) <= tol for t in times]))
        ch = float(np.mean(np.min(np.abs(ts[None, :] - rnd[:, None]), 1) <= tol))
        out[str(lv)] = {"real": round(real, 3), "chance": round(ch, 3), "lift": round(real / max(ch, 1e-6), 2)}
    return out


def match_across_cuts(F: dict, cuts: list[int], k: int = 3, seed: int = 0) -> dict:
    """Motion continuity across cuts: cosine of the mean (tx, ty) before/after each cut and the distance of the
    motion centroid; compared to random shot pairs (baseline)."""
    fps = float(F["fps"])
    n = len(F["tx"])
    vec = np.stack([F["tx"], F["ty"]], 1)
    cen = np.stack([F["cx"], F["cy"]], 1)

    def side(a, b):
        a, b = max(0, a), min(n, b)
        v = vec[a:b].mean(0) if b > a else np.zeros(2)
        c = cen[a:b].mean(0) if b > a else np.full(2, 0.5)
        return v, c

    pairs = []
    for c in cuts:
        if c - k - 1 < 0 or c + k + 1 >= n:
            continue
        pairs.append((side(c - k, c), side(c + 1, c + 1 + k)))

    def stats(ps):
        cos, dist, both = [], [], 0
        for (va, ca), (vb, cb) in ps:
            na, nb = np.linalg.norm(va) * fps, np.linalg.norm(vb) * fps
            dist.append(float(np.linalg.norm(ca - cb)))
            if na > 0.05 and nb > 0.05:
                cos.append(float(va @ vb / (np.linalg.norm(va) * np.linalg.norm(vb))))
                both += 1
        return {"pairs": len(ps), "moving_pairs": both,
                "dir_cos_mean": round(float(np.mean(cos)), 3) if cos else None,
                "same_dir_frac": round(float(np.mean(np.array(cos) > 0.5)), 3) if cos else None,
                "centroid_dist_mean": round(float(np.mean(dist)), 3) if dist else None}

    real = stats(pairs)
    rng = np.random.default_rng(seed)
    if len(pairs) > 3:
        perm = rng.permutation(len(pairs))
        rnd = stats([(pairs[i][0], pairs[j][1]) for i, j in zip(range(len(pairs)), perm)])
    else:
        rnd = {}
    return {"cuts": real, "shuffled_baseline": rnd}


def ref_stats(F: dict, song: dict, cuts: list[int] | None = None) -> dict:
    fps = float(F["fps"])
    n = len(F["luma"])
    cuts = cuts if cuts is not None else shots.detect_cuts(F["hist_d"], fps)
    fl = shots.flash_frames(F["luma"])
    flash_frames_set = {i + d for i, L in fl for d in range(L + 1)}
    # a cut that is just the edge of a flash is not a shot change
    cuts = [c for c in cuts if c not in flash_frames_set and c - 1 not in flash_frames_set]
    edges = [0] + cuts + [n]
    lens = np.diff(edges) / fps
    period = song["period"]
    cut_t = [c / fps for c in cuts]
    beats = song["beats"]
    # shot length histogram (seconds) and in beats
    bins = [0, 0.2, 0.35, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0, 1e9]
    hist, _ = np.histogram(lens, bins=bins)
    in_beats = lens / period
    bh, _ = np.histogram(in_beats, bins=[0, 0.375, 0.625, 0.875, 1.25, 1.75, 2.5, 3.5, 6, 1e9])
    # cut density per bar intensity level
    bars = song["bars"]
    dens = {}
    for lv in range(4):
        sel = [b for b in bars if b.get("level") == lv]
        if not sel:
            continue
        cnt = sum(sum(1 for t in cut_t if b["t"] <= t < b.get("end", b["t"] + 4 * period)) for b in sel)
        dens[str(lv)] = round(cnt / len(sel), 2)
    # around drops: cuts per beat in the 4 beats before and 8 after; flash at the drop?
    drops = []
    fl_t = [i / fps for i, _ in fl]
    for d in song["drops"]:
        before = sum(1 for t in cut_t if d - 4 * period <= t < d - 0.05)
        after = sum(1 for t in cut_t if d - 0.05 <= t < d + 8 * period)
        drops.append({"t": d, "cuts_per_beat_before": round(before / 4, 2), "cuts_per_beat_after": round(after / 8, 2),
                      "cut_at_drop": any(abs(t - d) < 0.06 for t in cut_t),
                      "flash_at_drop": any(abs(t - d) < 0.1 for t in fl_t)})
    # zoom punches and shake
    zoom = F["zoom"].astype(np.float32)
    zp = _events(np.abs(zoom), 0.02, int(0.15 * fps))
    zp = [i for i in zp if i not in cuts and i - 1 not in cuts and i not in flash_frames_set]
    jit = (np.abs(F["tx"] - shots.smooth(F["tx"], 5)) + np.abs(F["ty"] - shots.smooth(F["ty"], 5))).astype(np.float32)
    for c in cuts:
        jit[max(0, c - 2):c + 3] = 0
    shake_fr = jit > 0.012
    shake_runs = []
    i = 0
    while i < n:
        if shake_fr[i]:
            k = i
            while k < n and (shake_fr[k] or (k + 1 < n and shake_fr[k + 1])):
                k += 1
            if k - i >= 3:
                shake_runs.append((i, k - i))
            i = k
        else:
            i += 1
    # in-shot speed variation (velocity proxy): p90/p10 of the smoothed motion inside shots >= 0.6 s
    ramps = []
    for a, b in zip(edges[:-1], edges[1:]):
        if (b - a) / fps < 0.6:
            continue
        s = shots.shot_features(F, a, b)
        mc = np.array(shots.motion_curve(F, a, b))
        if mc.size < 6 or s["motion"] < 0.02:
            continue
        lo, hi = np.percentile(mc[2:], [10, 90])
        ramps.append({"t": round(a / fps, 2), "dur": s["dur"], "ratio": round(float(hi / max(lo, 0.005)), 2),
                      "peak_t": round(float(a / fps + (int(np.argmax(mc)) / fps)), 3)})
    peak_off = beat_offsets([r["peak_t"] for r in ramps if r["ratio"] > 3], beats, period)
    sat = F["sat"]
    return {
        "frames": n, "fps": fps, "duration": round(n / fps, 2), "bpm": song["bpm"],
        "shots": len(edges) - 1,
        "shot_len_s": {"mean": round(float(lens.mean()), 3), "median": round(float(np.median(lens)), 3),
                       "p10": round(float(np.percentile(lens, 10)), 3), "p90": round(float(np.percentile(lens, 90)), 3),
                       "hist_bins_s": bins[:-1], "hist": hist.tolist()},
        "shot_len_beats_hist": {"bins": ["<3/8", "1/2", "3/4", "1", "1.5", "2", "3", "4-6", ">6"], "n": bh.tolist()},
        "cuts_per_min": round(len(cuts) / (n / fps) * 60, 1),
        "cut_vs_beat": beat_offsets(cut_t, beats, period),
        "cut_vs_onset": onset_offsets(cut_t, song["onsets"], 0.1),
        "cut_vs_accent": accent_alignment(cut_t, song["onsets"], n / fps),
        "cut_vs_beat_chance": round(float(np.mean([abs(audio.nearest(beats, t)[1]) <= 0.045 for t in np.random.default_rng(1).uniform(1, n / fps - 1, 3000)])), 3),
        "cuts_per_bar_by_level": dens,
        "drops": drops,
        "flashes": {"n": len(fl), "per_min": round(len(fl) / (n / fps) * 60, 1),
                    "len_frames": [L for _, L in fl][:60], "vs_beat": beat_offsets(fl_t, beats, period),
                    "with_cut": round(float(np.mean([any(abs(i - c) <= 2 for c in cuts) for i, _ in fl])), 3) if fl else 0},
        "zoom_punches": {"n": len(zp), "per_min": round(len(zp) / (n / fps) * 60, 1),
                         "vs_beat": beat_offsets([i / fps for i in zp], beats, period)},
        "shake": {"runs": len(shake_runs), "frames_frac": round(float(sum(L for _, L in shake_runs) / n), 3),
                  "mean_len_frames": round(float(np.mean([L for _, L in shake_runs])), 1) if shake_runs else 0,
                  "vs_beat": beat_offsets([i / fps for i, _ in shake_runs], beats, period)},
        "speed_variation": {"shots": len(ramps), "ratio_median": round(float(np.median([r["ratio"] for r in ramps])), 2) if ramps else None,
                            "ramped_frac(ratio>3)": round(float(np.mean([r["ratio"] > 3 for r in ramps])), 3) if ramps else None,
                            "ramp_peak_vs_beat": peak_off},
        "motion_match": match_across_cuts(F, cuts),
        "look": {"luma_mean": round(float(F["luma"].mean()), 1), "contrast_mean": round(float(F["contrast"].mean()), 1),
                 "sat_mean": round(float(sat.mean()), 1), "black_frac": round(float(F["black"].mean()), 3),
                 "white_frames": int((F["luma"] > 200).sum())},
        "cut_times": [round(t, 3) for t in cut_t],
        "flash_times": [round(t, 3) for t in fl_t],
        "zoom_times": [round(i / fps, 3) for i in zp],
    }


def scan_reference(path: str | Path, work: str | Path, name: str | None = None, bpm_hint: float | None = None) -> dict:
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    name = name or Path(path).stem
    cache = work / f"{name}.scan.npz"
    if cache.exists():
        F = dict(np.load(cache))
    else:
        F = shots.scan_video(path)
        np.savez_compressed(cache, **F)
    x = media.load_audio(path, 22050)
    song = audio.analyze_song(x, 22050, bpm_hint=bpm_hint)
    (work / f"{name}.song.json").write_text(json.dumps(song), encoding="utf-8")
    st = ref_stats(F, song)
    st["name"] = name
    (work / f"{name}.ref.json").write_text(json.dumps(st, indent=1, ensure_ascii=False), encoding="utf-8")
    # contact sheets: one frame per shot (middle) and the whole thing every 0.5 s
    fps = float(F["fps"])
    edges = [0.0] + st["cut_times"] + [st["duration"]]
    mids = [(a + b) / 2 for a, b in zip(edges[:-1], edges[1:])]
    check.sheet_times(path, mids[:160], work / f"{name}.shots.jpg", cols=10, tw=192,
                      labels=[f"#{i} {b - a:.2f}s" for i, (a, b) in enumerate(zip(edges[:-1], edges[1:]))][:160])
    return st
