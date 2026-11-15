# -*- coding: utf-8 -*-
"""Beat-matched AMV assembly (pure logic, no I/O).

1. cut_grid()      — cut points for a song section. Each bar gets k cuts by its intensity level; positions are
                     chosen among the eighth-note slots by onset strength (+ beat / downbeat bonus) with a
                     minimum spacing, then snapped to a nearby strong onset. Drops are forced cuts.
2. make_segments() — segments between cuts with level, accent time (where the hit lands) and drop flag.
3. assign_clips()  — greedy clip choice per segment: interest x intensity fit, motion-direction continuity,
                     film runs (themed groups), no reuse, impact clips on drops.
4. fit_ramp()      — velocity ramp per segment; the clip's motion peak is placed on the segment accent.
5. fx_events()     — flashes, zoom punches, shake bursts, whip cuts from the song's hits.
build_plan() runs all of it and returns a JSON-able plan."""
from __future__ import annotations

import math
import random

import numpy as np

from . import fx

# cuts per bar by intensity level (0 calm … 3 peak) and the minimum spacing in beats
CUTS_PER_BAR = {0: 1, 1: 2, 2: 2, 3: 3}
MIN_GAP_BEATS = {0: 2.0, 1: 1.0, 2: 1.0, 3: 0.5}
ENERGY_TARGET = {0: 0.25, 1: 0.45, 2: 0.7, 3: 0.9}


def beat_level(song: dict) -> list[int]:
    """Intensity level of every beat (from the bar it belongs to)."""
    beats = song["beats"]
    lv = [0] * len(beats)
    for b in song["bars"]:
        j = b.get("beat0")
        if j is None:
            j = int(np.argmin(np.abs(np.asarray(beats) - b["t"])))
        for k in range(j, min(len(beats), j + 4)):
            lv[k] = b.get("level", 0)
    # beats before the first downbeat take the first bar's level
    first = song["bars"][0].get("beat0", 0) if song["bars"] else 0
    for k in range(0, min(first, len(lv))):
        lv[k] = song["bars"][0].get("level", 0)
    return lv


def onset_strength_at(onsets: list, t: float, tol: float) -> tuple[float, float]:
    """(strength, time) of the strongest onset within ±tol of t; (0, t) if none."""
    best = (0.0, t)
    for ot, s in onsets:
        if abs(ot - t) <= tol and s > best[0]:
            best = (float(s), float(ot))
    return best


def cut_grid(song: dict, t0: float, t1: float, cuts_per_bar: dict | None = None,
             min_gap_beats: dict | None = None, snap_tol: float = 0.045, snap_min: float = 0.15,
             forced: list[float] | None = None) -> list[dict]:
    """Cut times inside (t0, t1). Returns [{"t", "kind": beat|half|drop, "level", "strength"}] sorted."""
    cpb = cuts_per_bar or CUTS_PER_BAR
    mgb = min_gap_beats or MIN_GAP_BEATS
    beats = np.asarray(song["beats"], np.float64)
    lv = beat_level(song)
    period = float(song["period"])
    onsets = song.get("onsets", [])
    drops = [d for d in song.get("drops", []) if t0 + 0.05 < d < t1 - 0.05]
    cuts: list[dict] = []
    for d in drops:
        k = int(np.argmin(np.abs(beats - d)))
        cuts.append({"t": float(beats[k]), "kind": "drop", "level": lv[k], "strength": 1.0})
    for f in forced or []:       # vocal hits (typography lands on a cut, as in the reference)
        if t0 + 0.25 < f < t1 - 0.25 and all(abs(f - c["t"]) > 0.5 * period for c in cuts):
            k = int(np.argmin(np.abs(beats - f)))
            cuts.append({"t": round(float(f), 4), "kind": "vocal", "level": lv[k], "strength": 1.0})
    bars = song["bars"]
    for bi, bar in enumerate(bars):
        j = bar.get("beat0", int(np.argmin(np.abs(beats - bar["t"]))))
        if j >= len(beats):
            continue
        bt = beats[j:j + 4]
        if bt[0] >= t1 or (bt[-1] + period) <= t0:
            continue
        level = bar.get("level", 0)
        k = cpb.get(level, 2)
        gap = mgb.get(level, 1.0) * period - 1e-3
        slots = []
        for q, b in enumerate(bt):
            for half in (0, 1):
                t = b + half * period / 2
                if half and level < 2:
                    continue
                if not (t0 + 0.5 * period <= t <= t1 - 0.5 * period):
                    continue
                s, ot = onset_strength_at(onsets, t, snap_tol)
                score = s + (0.25 if not half else 0.0) + (0.35 if q == 0 and not half else 0.0)
                slots.append((score, t, "half" if half else "beat", s, ot))
        slots.sort(reverse=True)
        chosen = 0
        for score, t, kind, s, ot in slots:
            if chosen >= k:
                break
            if any(abs(t - c["t"]) < gap for c in cuts):
                continue
            tt = ot if s >= snap_min else t
            cuts.append({"t": round(float(tt), 4), "kind": kind, "level": level, "strength": round(s, 3)})
            chosen += 1
    cuts.sort(key=lambda c: c["t"])
    # safety: enforce the absolute minimum length of half a beat
    out: list[dict] = []
    for c in cuts:
        if out and c["t"] - out[-1]["t"] < 0.5 * period - 1e-3:
            if c["kind"] in ("drop", "vocal") and out[-1]["kind"] not in ("drop", "vocal"):
                out[-1] = c
            continue
        if c["t"] - t0 < 0.5 * period - 1e-3 or t1 - c["t"] < 0.5 * period - 1e-3:
            continue
        out.append(c)
    return out


def make_segments(song: dict, t0: float, t1: float, cuts: list[dict]) -> list[dict]:
    lv = beat_level(song)
    beats = np.asarray(song["beats"], np.float64)
    onsets = song.get("onsets", [])
    edges = [t0] + [c["t"] for c in cuts] + [t1]
    kinds = ["start"] + [c["kind"] for c in cuts]
    segs = []
    for i, (a, b) in enumerate(zip(edges[:-1], edges[1:])):
        mid = (a + b) / 2
        k = int(np.argmin(np.abs(beats - mid)))
        level = lv[k]
        # accent: strongest onset inside a longer segment (after the first half-beat, before the last quarter),
        # the cut itself for short segments or when nothing stands out
        period = float(song["period"])
        inside = [(s, t) for t, s in onsets if a + 0.4 * period <= t <= b - 0.25 * period] if b - a >= 1.4 * period else []
        ta, sa = a, 0.0
        if inside:
            s, t = max(inside)
            if s >= 0.25:
                ta, sa = t, s
        segs.append({"i": i, "t0": round(a, 4), "t1": round(b, 4), "dur": round(b - a, 4), "level": int(level),
                     "drop": kinds[i] == "drop", "ta": round(float(ta), 4), "accent": round(float(sa), 3)})
    return segs


# ----------------------------------------------------------------------------- velocity
def ramp_for(seg: dict, clip: dict | None = None, period: float = 0.4) -> dict:
    """Velocity ramp for a segment: a slow base with Gaussian bursts (see fx.rate_at).

    Main burst on the segment accent (the clip's motion peak is placed there by fit_ramp); in high bars a short
    decaying burst right after the cut and, every other segment, a rising burst into the next cut — the
    «fast-through-the-cut, slow-in-the-middle» velocity feel."""
    lv = seg["level"]
    motion = (clip or {}).get("energy", 0.5)
    t0, t1, ta = seg["t0"], seg["t1"], seg["ta"]
    if seg["drop"]:
        r = {"base": 0.42, "peak": 3.4, "ta": ta, "w": 0.11, "bursts": []}
        if seg["dur"] > 1.5 * period:
            r["bursts"].append({"t": t1, "amp": 1.4, "w": 0.07})
        return r
    if lv >= 2 and seg["dur"] >= 0.55 * period:
        peak = 2.6 + 0.8 * (lv == 3) - 0.6 * (motion < 0.3)
        r = {"base": 0.55, "peak": round(peak, 2), "ta": ta, "w": 0.10 + 0.03 * (seg["dur"] > 2 * period),
             "bursts": []}
        if ta - t0 > 0.15:
            r["bursts"].append({"t": t0, "amp": 1.0 + 0.6 * (lv == 3), "w": 0.07})
        if lv == 3 and seg["i"] % 2 == 1 and t1 - ta > 0.2:
            r["bursts"].append({"t": t1, "amp": 1.5, "w": 0.07})
        return r
    if lv == 1:
        return {"base": 0.9, "peak": 0.0, "ta": ta, "w": 0.1}
    return {"base": 0.8, "peak": 0.0, "ta": ta, "w": 0.1}


def fit_ramp(seg: dict, clip: dict, ramp: dict, margin: float = 0.06) -> tuple[float, dict] | None:
    """Source in-point so that the clip's motion peak lands on the accent; shrinks the ramp if the clip is short.

    Returns (src_in, ramp) or None if even a constant 0.5x does not fit."""
    lo, hi = clip["t_in"] + margin, clip["t_out"] - margin
    r = dict(ramp)
    for _ in range(6):
        span = fx.rate_integral(seg["t0"], seg["t1"], r)
        if span <= hi - lo:
            break
        if r.get("bursts"):
            r["bursts"] = [dict(b, amp=round(b["amp"] * 0.6, 3)) for b in r["bursts"] if b["amp"] * 0.6 > 0.4]
        elif r.get("peak", 0) > 0:
            r["peak"] = max(0.0, r["peak"] * 0.7) if r["peak"] * 0.7 > r["base"] * 1.5 else 0.0
        else:
            r["base"] *= 0.8
    span = fx.rate_integral(seg["t0"], seg["t1"], r)
    if span > hi - lo or r["base"] < 0.38:
        return None
    to_peak = fx.rate_integral(seg["t0"], r["ta"], r)
    s0 = clip.get("t_peak", (lo + hi) / 2) - to_peak
    s0 = min(max(s0, lo), hi - span)
    return round(float(s0), 4), r


# ----------------------------------------------------------------------------- clip choice
def _cos(a, b) -> float:
    na, nb = math.hypot(*a), math.hypot(*b)
    if na < 1e-6 or nb < 1e-6:
        return 0.0
    return (a[0] * b[0] + a[1] * b[1]) / (na * nb)


def character(clip: dict) -> float:
    """0..1 «a character is on screen»: person/animal/face detector presence (library.detect_subjects);
    hand boosts count."""
    v = float(clip.get("char", min(1.0, clip.get("skin", 0.0) / 0.025)))
    if clip.get("boosted"):
        v = max(v, 0.6)
    return v


def is_abstract(clip: dict) -> bool:
    return character(clip) < 0.25


def same_scene(a: dict, b: dict, gap: float = 45.0) -> bool:
    """Same film and close in source time (and not jumping far back) — a story-coherent run."""
    return a.get("film") == b.get("film") and -8.0 <= b["t_in"] - a["t_out"] <= gap


def clip_score(seg: dict, clip: dict, prev: dict | None, run_len: int, run_target: int,
               period: float = 0.4) -> float:
    """How well a clip fits a segment. Characters and action dominate (AMVs live on faces and bodies);
    abstract shots (no character) only as 1-beat accents in high bars, never two in a row."""
    e_t = ENERGY_TARGET.get(seg["level"], 0.5)
    fit = 1.0 - abs(clip.get("energy", 0.5) - e_t)
    ch = character(clip)
    sc = 0.35 * clip.get("score", 0.3) + 0.30 * fit + 0.45 * ch
    if seg["drop"]:
        sc += 0.6 * clip.get("impact", 0.0) + 0.2 * ch
    elif seg["accent"] >= 0.4 and seg["level"] >= 2:
        sc += 0.2 * clip.get("impact", 0.0)
    if is_abstract(clip):
        if seg["dur"] > 1.1 * period or seg["level"] < 2 or seg["drop"]:
            sc -= 0.8
        else:
            sc -= 0.1
        if prev is not None and is_abstract(prev):
            sc -= 0.8
    if prev is not None:
        m_out, m_in = prev.get("dir_out", (0, 0)), clip.get("dir_in", (0, 0))
        if math.hypot(*m_out) > 0.03 and math.hypot(*m_in) > 0.03:
            sc += 0.30 * _cos(m_out, m_in)
        if same_scene(prev, clip):
            sc += 0.35 if run_len < run_target else -0.5
        elif clip.get("film") == prev.get("film"):
            sc -= 0.25 if run_len < run_target else 0.55
        elif run_len < 2:
            sc -= 0.2
    return sc


def assign_clips(segs: list[dict], clips: list[dict], period: float, seed: int = 7,
                 near_s: float = 6.0) -> list[dict]:
    """Greedy left-to-right choice. Each clip is used once; clips of the same film within near_s seconds of a
    used one are penalized (no visible repeats). Returns segments with "clip" and "src_in"/"ramp" filled."""
    rng = random.Random(seed)
    used: set = set()
    used_spans: dict[str, list[tuple[float, float]]] = {}
    out = []
    prev = None
    run_len, run_target = 0, rng.randint(2, 4)
    for seg in segs:
        best = None
        for c in clips:
            if c["id"] in used:
                continue
            r0 = ramp_for(seg, c, period)
            fit = fit_ramp(seg, c, r0)
            if fit is None:
                continue
            sc = clip_score(seg, c, prev, run_len, run_target, period)
            for a, b in used_spans.get(c["src"], []):
                if c["t_in"] < b + near_s and c["t_out"] > a - near_s:
                    sc -= 0.5
                    break
            sc += rng.uniform(0, 0.04)
            if best is None or sc > best[0]:
                best = (sc, c, fit)
        s = dict(seg)
        if best is None:
            s["clip"] = None
            out.append(s)
            continue
        _, c, (src_in, ramp) = best
        used.add(c["id"])
        span = fx.rate_integral(seg["t0"], seg["t1"], ramp)
        used_spans.setdefault(c["src"], []).append((src_in, src_in + span))
        if prev is not None and same_scene(prev, c):
            run_len += 1
        else:
            run_len, run_target = 1, rng.randint(2, 4)
        s.update({"clip": c["id"], "src": c["src"], "film": c.get("film"), "src_in": src_in, "ramp": ramp,
                  "src_span": round(span, 4), "cx": c.get("cx", 0.5),
                  "cx_916": c.get("cx_916", c.get("cx", 0.5)), "cx_169": c.get("cx_169", 0.5), "dir_out": c.get("dir_out", (0, 0)),
                  "dir_in": c.get("dir_in", (0, 0)), "t_peak": c.get("t_peak"),
                  "abstract": is_abstract(c), "character": round(character(c), 2)})
        prev = c
        out.append(s)
    return out


# ----------------------------------------------------------------------------- effects
PALETTE = [(255, 255, 255), (60, 200, 255), (255, 220, 120), (80, 80, 255), (255, 255, 255)]  # BGR


def fx_events(song: dict, segs: list[dict], t0: float, t1: float, flashes_per_min: float = 20.0,
              seed: int = 3) -> dict:
    """Flashes, punches, shakes and whips (song times). Calm bars (level < 2) get almost nothing."""
    rng = random.Random(seed)
    lv = beat_level(song)
    beats = np.asarray(song["beats"], np.float64)
    period = float(song["period"])

    def level_at(t):
        return lv[int(np.argmin(np.abs(beats - t)))] if len(beats) else 0

    flashes, punches, shakes, whips = [], [], [], []
    cut_ts = [s["t0"] for s in segs[1:]]
    for s in segs:
        if s["drop"]:
            flashes.append({"t": s["t0"], "frames": 3, "color": (255, 255, 255), "amp": 1.0})
            punches.append({"t": s["t0"], "amp": 0.12, "tau": 0.2})
            shakes.append({"t": s["t0"], "amp": 0.022, "tau": 0.14, "seed": rng.uniform(0, 6.28)})
        elif s["i"] > 0 and s["level"] == 3:
            punches.append({"t": s["t0"], "amp": 0.05, "tau": 0.12})
    # accents inside high bars: kicks → shake + small punch, strongest → flash
    budget = int(round(flashes_per_min * (t1 - t0) / 60.0)) - len(flashes)
    acc = []
    for t, st in song.get("onsets", []):
        if not (t0 + 0.1 < t < t1 - 0.1) or level_at(t) < 2:
            continue
        if any(abs(t - c) < 0.08 for c in cut_ts):
            continue
        acc.append((st, t))
    for t, st in song.get("kicks", []):
        if t0 + 0.1 < t < t1 - 0.1 and level_at(t) >= 2 and st >= 0.35:
            shakes.append({"t": t, "amp": 0.008 + 0.008 * st, "tau": 0.09, "seed": rng.uniform(0, 6.28)})
            if st >= 0.6:
                punches.append({"t": t, "amp": 0.03, "tau": 0.1})
    acc.sort(reverse=True)
    k = 0
    for st, t in acc:
        if k >= max(0, budget):
            break
        if any(abs(t - f["t"]) < 2 * period for f in flashes):
            continue
        col = PALETTE[k % len(PALETTE)]
        flashes.append({"t": round(t, 4), "frames": 1 if st < 0.6 else 2, "color": col, "amp": 0.75 + 0.25 * st})
        k += 1
    # whips: cuts inside high bars where both sides move in a similar direction (or the outgoing moves fast)
    for a, b in zip(segs[:-1], segs[1:]):
        if b["level"] < 2 or b["drop"] or a.get("clip") is None or b.get("clip") is None:
            continue
        d = a.get("dir_out", (0, 0))
        if math.hypot(*d) < 0.05:
            continue
        if rng.random() < 0.45:
            whips.append({"t": b["t0"], "dir": (round(d[0], 3), round(d[1], 3)), "frames": 2})
    flashes.sort(key=lambda f: f["t"])
    return {"flashes": flashes, "punches": sorted(punches, key=lambda p: p["t"]),
            "shakes": sorted(shakes, key=lambda p: p["t"]), "whips": whips}


def build_plan(song: dict, clips: list[dict], t0: float, t1: float, seed: int = 7,
               forced: list[float] | None = None, **kw) -> dict:
    cuts = cut_grid(song, t0, t1, forced=forced)
    segs = make_segments(song, t0, t1, cuts)
    segs = assign_clips(segs, clips, float(song["period"]), seed=seed)
    ev = fx_events(song, segs, t0, t1, seed=seed + 1, **kw)
    return {"t0": t0, "t1": t1, "bpm": song["bpm"], "period": song["period"], "segments": segs, **ev}


def plan_stats(plan: dict, song: dict) -> dict:
    """Self-check numbers in the same terms as the reference analysis."""
    segs = plan["segments"]
    lens = np.array([s["dur"] for s in segs])
    beats = np.asarray(song["beats"])
    cut_t = [s["t0"] for s in segs[1:]]
    on_beat = [abs(beats[np.argmin(np.abs(beats - t))] - t) <= 0.045 for t in cut_t]
    ratios = [s["ramp"]["peak"] / s["ramp"]["base"] for s in segs if s.get("ramp") and s["ramp"].get("peak", 0) > 0]
    dur = plan["t1"] - plan["t0"]
    return {"segments": len(segs), "missing_clips": sum(1 for s in segs if s.get("clip") is None),
            "cuts_per_min": round(len(cut_t) / dur * 60, 1),
            "shot_len_median": round(float(np.median(lens)), 3), "shot_len_p10": round(float(np.percentile(lens, 10)), 3),
            "cuts_on_beat": round(float(np.mean(on_beat)), 3) if on_beat else None,
            "ramped_segments": len(ratios), "ramp_ratio_median": round(float(np.median(ratios)), 2) if ratios else None,
            "flashes_per_min": round(len(plan["flashes"]) / dur * 60, 1), "whips": len(plan["whips"]),
            "films_used": sorted({s.get("film") for s in segs if s.get("film")}),
            "abstract_segments": sum(1 for s in segs if s.get("abstract")),
            "same_scene_runs_mean": round(float(np.mean(_runs(segs))), 2) if segs else 0}


def _runs(segs: list[dict]) -> list[int]:
    runs, n = [], 0
    for a, b in zip([None] + segs[:-1], segs):
        if a is not None and a.get("film") == b.get("film") and b.get("src_in") is not None and \
                a.get("src_in") is not None and -8 <= b["src_in"] - (a["src_in"] + a.get("src_span", 0)) <= 45:
            n += 1
        else:
            if n:
                runs.append(n)
            n = 1
    runs.append(n)
    return runs
