# -*- coding: utf-8 -*-
"""Pure logic of the AMV toolkit (tools/amv): beats/drops, cuts, flashes, ramps, assembly, captions, checks."""
import math

import numpy as np
import pytest

from tools.amv import audio, captions, check, fx, library, plan, refscan, render, shots


# ----------------------------------------------------------------------------- audio
def click_env(bpm=120.0, dur=20.0, fps=200.0, phase=0.1, accent_every=0):
    env = np.zeros(int(dur * fps), np.float32)
    p = 60 / bpm
    for k, t in enumerate(np.arange(phase, dur, p)):
        env[int(t * fps)] = 1.0 if not accent_every or k % accent_every == 0 else 0.6
    return env


def test_pick_peaks_finds_impulses_and_merges_close_ones():
    fps = 200.0
    env = np.zeros(2000, np.float32)
    for t in (1.0, 2.0, 2.03, 3.5):
        env[int(t * fps)] = 1.0
    env[int(2.03 * fps)] = 0.5
    pk = audio.pick_peaks(env, fps, min_gap=0.07)
    assert [round(t, 2) for t, _ in pk] == [1.0, 2.0, 3.5]
    assert all(0 < s <= 1 for _, s in pk)


def test_tempo_and_dp_beats_follow_a_click_track():
    fps = 100.0
    env = click_env(128.0, 30.0, fps, phase=0.2)
    bpm = audio.tempo_autocorr(env, fps, (70, 180))
    assert abs(bpm - 128) < 1.5
    beats = audio.track_beats(env, fps, 60 / bpm)
    d = np.diff(beats)
    assert abs(np.median(d) - 60 / 128) < 0.02
    # every tracked beat sits on a click (±1 envelope frame)
    clicks = np.arange(0.2, 30, 60 / 128)
    assert np.mean([np.min(np.abs(clicks - b)) <= 0.011 for b in beats]) > 0.95


def test_tempo_curve_follows_a_tempo_change():
    fps = 100.0
    a = click_env(100.0, 24.0, fps, phase=0.1)
    b = click_env(150.0, 24.0, fps, phase=0.05)
    tc, tb = audio.tempo_curve(np.concatenate([a, b]), fps, win_s=6, hop_s=2, switch=1.0)
    assert abs(np.median(tb[tc < 18]) - 100) < 3
    assert abs(np.median(tb[tc > 30]) - 150) < 4


def test_detect_drops_and_levels():
    e = np.array([1, 1, 1, 1, 1, 3, 3, 3, 3, 1, 1, 1, 3.2, 3, 3], float)
    assert audio.detect_drops(e, ratio=1.45, min_gap_bars=3) == [5, 12]
    lv = audio.levels(np.arange(8.0))
    assert lv.tolist() == [0, 0, 1, 1, 2, 2, 3, 3]


def test_nearest_signed_offset():
    i, off = audio.nearest([1.0, 2.0, 3.0], 2.04)
    assert i == 1 and abs(off - 0.04) < 1e-9


# ----------------------------------------------------------------------------- shots
def test_detect_cuts_adaptive_and_min_shot():
    rng = np.random.default_rng(0)
    d = rng.uniform(0.01, 0.05, 300).astype(np.float32)
    for i in (50, 120, 123, 200):
        d[i] = 0.6
    d[123] = 0.4
    cuts = shots.detect_cuts(d, fps=25.0, min_shot_s=0.25)
    assert cuts == [50, 120, 200]


def test_detect_cuts_ignores_steady_high_motion():
    d = np.full(200, 0.2, np.float32)          # constant churn (fast camera) is not a cut
    d[100] = 0.75
    assert shots.detect_cuts(d, fps=25.0) == [100]


def test_flash_frames():
    L = np.full(50, 60.0)
    L[10] = 240
    L[30:32] = 200
    fl = shots.flash_frames(L)
    assert fl == [(10, 1), (30, 2)]


def test_affine_from_flow_recovers_pan_and_zoom():
    h, w = 60, 100
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    x, y = xx - w / 2, yy - h / 2
    flow = np.stack([2.0 + 0.03 * x, -1.0 + 0.03 * y], -1).astype(np.float32)
    p, res = shots.affine_from_flow(flow, step=2)
    assert abs(p[0] - 2.0) < 1e-3 and abs(p[1] + 1.0) < 1e-3
    assert abs(p[2] - 0.03) < 1e-4 and abs(p[3]) < 1e-4
    assert res.mean() < 1e-3


def test_interest_prefers_motion_and_penalizes_dark():
    base = dict(motion=0.1, peak_val=0.2, camera=0.2, contrast=50, sat=90, luma=90, black=0.1, white=0.0, dur=1.5)
    calm = dict(base, motion=0.005, peak_val=0.01)
    dark = dict(base, luma=15, black=0.8)
    assert shots.interest(base) > shots.interest(calm) > 0
    assert shots.interest(dark) < shots.interest(base) * 0.5


# ----------------------------------------------------------------------------- fx
def test_rate_integral_matches_numeric_integration():
    ramp = {"base": 0.55, "peak": 3.2, "ta": 1.3, "w": 0.1, "bursts": [{"t": 1.0, "amp": 1.5, "w": 0.07}]}
    ts = np.linspace(1.0, 1.8, 20001)
    num = np.trapezoid([fx.rate_at(t, ramp) for t in ts], ts) if hasattr(np, "trapezoid") else \
        np.trapz([fx.rate_at(t, ramp) for t in ts], ts)
    assert abs(fx.rate_integral(1.0, 1.8, ramp) - num) < 1e-4
    assert fx.rate_at(1.3, ramp) > 3.0 and fx.rate_at(1.6, ramp) < 0.6


def test_constant_ramp_and_src_time():
    seg = {"t0": 10.0, "src_in": 5.0, "ramp": {"base": 0.8, "peak": 0.0, "ta": 10.0, "w": 0.1}}
    assert abs(fx.src_time(11.0, seg) - 5.8) < 1e-9


def test_punch_shake_flash_envelopes():
    hits = [{"t": 1.0, "amp": 0.1, "tau": 0.2}]
    assert fx.punch_scale(0.5, hits) == 1.0
    assert fx.punch_scale(1.0, hits) == pytest.approx(1.1)
    assert 1.0 < fx.punch_scale(1.3, hits) < fx.punch_scale(1.1, hits)
    sh = [{"t": 2.0, "amp": 0.01, "tau": 0.1, "seed": 1.0}]
    assert fx.shake_offset(1.9, sh) == (0.0, 0.0, 0.0)
    assert fx.shake_offset(2.05, sh) == fx.shake_offset(2.05, sh)      # deterministic
    assert max(abs(v) for v in fx.shake_offset(2.05, sh)[:2]) > 0
    fl = [{"t": 3.0, "frames": 2, "color": (1, 2, 3)}]
    a0, c = fx.flash_alpha(3.0, fl, 30)
    a1, _ = fx.flash_alpha(3.0 + 1 / 30, fl, 30)
    a2, _ = fx.flash_alpha(3.0 + 2 / 30, fl, 30)
    assert a0 == 1.0 and 0 < a1 < 1 and a2 == 0 and c == (1, 2, 3)


def test_grade_lut_lifts_dark_shots_monotonic():
    lut = fx.grade_lut({"lum": 30.0, "lstd": 20.0, "mean": [30, 30, 30]})
    assert lut.shape == (1, 256, 3)
    g = lut[0, :, 1].astype(int)
    assert np.all(np.diff(g) >= 0)
    assert g[30] > 45          # mean of a dark shot is lifted


# ----------------------------------------------------------------------------- plan
def make_song(bpm=150.0, dur=40.0, levels=None, drops=()):
    p = 60 / bpm
    beats = list(np.round(np.arange(0.1, dur, p), 4))
    nb = len(beats) // 4
    lv = levels or [2] * nb
    bars = [{"t": beats[4 * i], "beat0": 4 * i, "level": lv[i % len(lv)], "end": beats[min(len(beats) - 1, 4 * i + 4)]}
            for i in range(nb)]
    onsets = [(b, 0.6 if k % 4 == 0 else 0.4) for k, b in enumerate(beats)]
    return {"beats": beats, "period": p, "bpm": bpm, "bars": bars, "onsets": onsets, "drops": list(drops),
            "kicks": [(b, 0.7) for b in beats[::2]], "duration": dur}


def test_cut_grid_on_beats_density_and_spacing():
    song = make_song(levels=[0, 0, 3, 3])
    cuts = plan.cut_grid(song, 0.1, 30.0)
    p = song["period"]
    beats = np.array(song["beats"])
    half = np.sort(np.concatenate([beats, beats + p / 2]))
    assert all(np.min(np.abs(half - c["t"])) < 1e-3 for c in cuts)
    assert all(b["t"] - a["t"] >= 0.5 * p - 1e-3 for a, b in zip(cuts, cuts[1:]))
    calm = [c for c in cuts if c["level"] == 0]
    hot = [c for c in cuts if c["level"] == 3]
    assert len(hot) > len(calm) * 2


def test_cut_grid_forces_drops_and_vocal_hits():
    song = make_song(drops=[song_t := 0.1 + 32 * 0.4])
    cuts = plan.cut_grid(song, 0.1, 30.0, forced=[20.05])
    kinds = {c["kind"] for c in cuts}
    assert "drop" in kinds and "vocal" in kinds
    assert any(abs(c["t"] - song_t) < 1e-3 and c["kind"] == "drop" for c in cuts)


def test_fit_ramp_places_motion_peak_on_accent():
    seg = {"i": 1, "t0": 10.0, "t1": 10.8, "dur": 0.8, "level": 3, "drop": False, "ta": 10.4, "accent": 0.7}
    clip = {"t_in": 100.0, "t_out": 104.0, "t_peak": 102.0, "energy": 0.9}
    r0 = plan.ramp_for(seg, clip, 0.4)
    src_in, r = plan.fit_ramp(seg, clip, r0)
    s = {"t0": seg["t0"], "src_in": src_in, "ramp": r}
    assert abs(fx.src_time(seg["ta"], s) - clip["t_peak"]) < 1e-3
    assert clip["t_in"] <= src_in and fx.src_time(seg["t1"], s) <= clip["t_out"]


def test_fit_ramp_shrinks_or_rejects_short_clips():
    seg = {"i": 0, "t0": 0.0, "t1": 2.0, "dur": 2.0, "level": 3, "drop": True, "ta": 0.0, "accent": 1.0}
    short = {"t_in": 0.0, "t_out": 0.5, "t_peak": 0.2}
    assert plan.fit_ramp(seg, short, plan.ramp_for(seg, short, 0.4)) is None


def clip(id_, film, t_in, char=0.8, energy=0.7, d=(0.0, 0.0), dur=3.0):
    return {"id": id_, "film": film, "src": film + ".mp4", "t_in": t_in, "t_out": t_in + dur, "t_peak": t_in + 1.0,
            "score": 0.6, "energy": energy, "impact": 0.5, "char": char, "dir_in": d, "dir_out": d}


def test_assign_clips_no_reuse_and_abstract_only_as_short_accents():
    song = make_song(levels=[1, 2, 3, 2])
    cuts = plan.cut_grid(song, 0.1, 20.0)
    segs = plan.make_segments(song, 0.1, 20.0, cuts)
    clips = [clip(f"a{i}", "A", 20.0 * i) for i in range(30)] + \
            [clip(f"b{i}", "B", 20.0 * i) for i in range(30)] + \
            [clip(f"x{i}", "X", 20.0 * i, char=0.0) for i in range(30)]
    out = plan.assign_clips(segs, clips, song["period"])
    ids = [s["clip"] for s in out if s["clip"]]
    assert len(ids) == len(set(ids)) == len(segs)
    for a, b in zip(out, out[1:]):
        assert not (a["abstract"] and b["abstract"])
    for s in out:
        if s["abstract"]:
            assert s["dur"] <= 1.1 * song["period"] + 1e-6 and s["level"] >= 2


def test_assign_clips_builds_same_scene_runs():
    song = make_song(levels=[2])
    cuts = plan.cut_grid(song, 0.1, 16.0)
    segs = plan.make_segments(song, 0.1, 16.0, cuts)
    clips = [clip(f"{f}{i}", f, 4.0 * i) for f in "ABCD" for i in range(40)]
    out = plan.assign_clips(segs, clips, song["period"])
    runs = plan._runs(out)
    assert np.mean(runs) >= 1.8 and max(runs) <= 5


def test_direction_continuity_bonus():
    seg = {"i": 1, "t0": 0, "t1": 0.8, "dur": 0.8, "level": 2, "drop": False, "ta": 0, "accent": 0}
    prev = clip("p", "A", 0, d=(0.3, 0.0))
    same = clip("s", "B", 0, d=(0.3, 0.0))
    opp = clip("o", "B", 0, d=(-0.3, 0.0))
    assert plan.clip_score(seg, same, prev, 3, 3) > plan.clip_score(seg, opp, prev, 3, 3)


def test_fx_events_budget_and_drop_hits():
    song = make_song(levels=[3], drops=[0.1 + 16 * 0.4])
    p = plan.build_plan(song, [clip(f"c{i}", "ABCDEFG"[i % 7], 10.0 * i) for i in range(80)], 0.1, 30.0)
    drop = song["drops"][0]
    assert any(abs(f["t"] - drop) < 0.05 and f["frames"] >= 2 for f in p["flashes"])
    assert any(abs(h["t"] - drop) < 0.05 and h["amp"] >= 0.1 for h in p["punches"])
    per_min = len(p["flashes"]) / (30.0 - 0.1) * 60
    assert 10 <= per_min <= 25
    st = plan.plan_stats(p, song)
    assert st["missing_clips"] == 0 and st["cuts_on_beat"] > 0.6


# ----------------------------------------------------------------------------- captions / checks / misc
def test_cues_from_words_whitelist_snap_and_ends():
    words = [{"w": "Pump", "s": 10.02, "e": 10.3, "p": 0.9}, {"w": "it,", "s": 10.3, "e": 10.5, "p": 0.9},
             {"w": "dude?", "s": 12.0, "e": 12.3, "p": 0.8}, {"w": "kid", "s": 12.5, "e": 12.6, "p": 0.9},
             {"w": "yeah", "s": 14.0, "e": 14.2, "p": 0.1}]
    cues = captions.cues_from_words(words, {"pump": "PUMP IT", "dude": "DUDE", "yeah": "YEAH"},
                                    onsets=[[9.95, 1.0, -10], [12.05, 1.0, -10]], min_prob=0.3,
                                    extra=[{"text": "LOUDER", "t": 12.4, "hard": True}])
    assert [c["text"] for c in cues] == ["PUMP IT", "DUDE", "LOUDER"]
    assert cues[0]["t"] == 9.95 and cues[1]["t"] == 12.05
    assert cues[1]["end"] < cues[2]["t"]
    assert captions.spring(0) == 0 and captions.spring(100) == pytest.approx(1, abs=0.01)


def test_frozen_stretches():
    d = np.r_[np.full(30, 5.0), np.full(20, 0.1), np.full(30, 5.0)]
    assert check.frozen_stretches(d, 30.0, 0.6, 0.5) == [(1.0, 0.667)]
    assert check.frozen_stretches(d, 30.0, 0.6, 1.0) == []


def test_best_window_and_crop_box():
    prof = np.zeros(100)
    prof[70:80] = 1
    assert abs(library.best_window(prof, 0.2, prior=0.0) - 0.75) < 0.06
    cw, ch, px, py = render.crop_box((0, 0, 1920, 804), 9 / 16, 0.95)
    assert ch == 804 and abs(cw / ch - 9 / 16) < 0.01 and px + cw == 1920


def test_refscan_beat_and_accent_stats():
    beats = list(np.arange(0, 20, 0.5))
    st = refscan.beat_offsets([1.0, 1.25, 2.02, 3.3], beats, 0.5)
    assert st["within_1f_beat"] == 0.5 and st["on_half_beat"] == 0.25
    onsets = [(t, 0.8) for t in np.arange(0.3, 20, 2.0)]
    acc = refscan.accent_alignment([t for t, _ in onsets], onsets, 20.0, levels=(0.5,))
    assert acc["0.5"]["real"] == 1.0 and acc["0.5"]["lift"] > 5
