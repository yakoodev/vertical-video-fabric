# -*- coding: utf-8 -*-
"""Reference-priority AMV planning: what makes «Warriors「AMV」Anime Mix» (TumpyGFX, 14.2M views; analysed in
D:/vvf-offload/amv/analysis_refs2.md) feel premium next to our round-2 edit, turned into plan rules.

1. Colour order — runs of 3–8 shots in one colour, small Lab steps cut to cut, a palette per song part
   (target: colour jump across cuts ≤ 0.65 of a shuffled order, colour runs ≥ 20 % of the runtime).
2. Pacing contrast — sections: story (calm phrases: a cut per 2–4 bars, long native-speed shots, dissolves
   0.2–1.0 s), action (beat cuts at native speed), climax (the last drop: one film, long same-scene runs).
   Target: cuts/min in 10 s windows p10 ≤ 20, p90 70–90.
3. Black breaths — the quiet dip before a drop fades to black (4–6 frames), the picture comes back on the hit;
   a 1-frame blink before the final hit; a fade out at the song end.
4. The source's own impacts (white-outs, slash and light frames) on accents instead of synthetic flashes
   (≤ 4/min, only on drops whose shot has none).
5. Speed by section — native 1.0× action, a burst only on drops, constant 0.4–0.6× only on hero beats
   (the first shot of a calm part, the pose before a drop).
6. Motif match cuts (overrides "motif": hand, coin, sword_draw, eyes, falling, sky, fire…).
7. Single-film climax.  8. A strobe burst of 2–3-frame inserts on the final drop.
9. Restraint — no whips, shake only in bursts after drops (≤ 12 % of frames), punches only on drops.

build_plan() returns the plan format of plan.build_plan (render.py reads it) plus "sections", "blacks", "vfade";
stats() measures a plan in the reference's terms with pass/fail checks. Pure logic, no I/O."""
from __future__ import annotations

import math
import random

import numpy as np

from . import fx, plan

PLAN_OPTIONS = ("strobe", "breaths", "fade_out", "flash_budget", "colour_weight", "hero_speed", "fps")
MODES = ("story", "action", "climax")

# Reference «Warriors» numbers the plan aims at (analysis_refs2.md, «What to implement»).
TARGETS = {"colour_jump": 0.65, "colour_runs": 0.20, "window_p10": 20.0, "window_p90": (70.0, 90.0),
           "flashes_per_min": 6.0, "shake_frac": 0.12, "native_share": 0.70}

# target cuts per minute by bar level, per section mode (the reference: calm 12–25, drops ~79, final strobe higher);
# mode_grid() turns them into cuts per bar and a minimum spacing for the song's tempo
MODE_CPM = {
    "story": {0: 12.0, 1: 14.0, 2: 16.0, 3: 18.0},
    "action": {0: 26.0, 1: 40.0, 2: 55.0, 3: 75.0},
    "climax": {0: 40.0, 1: 55.0, 2: 75.0, 3: 90.0},
}


def mode_grid(mode: str, period: float) -> tuple[dict, dict]:
    """(cuts_per_bar, min_gap_beats) for plan.cut_grid that give MODE_CPM at this beat period."""
    bar = 4.0 * period
    cpb, gap = {}, {}
    for lv, cpm in MODE_CPM[mode].items():
        cpb[lv] = max(1, int(round(cpm * bar / 60.0)))
        gap[lv] = float(np.clip(0.8 * (60.0 / cpm) / period, 0.5, 8.0))
    return cpb, gap


def musical(song: dict, max_bpm: float = 150.0) -> dict:
    """The song at its felt tempo: a tracker that locked onto the eighth-note pulse (e.g. 188 BPM) gives bars of two
    real beats — plan on every other beat and every other bar instead (onsets, drops and kicks stay as they are)."""
    if float(song["bpm"]) <= max_bpm or float(song["bpm"]) / 2 < 70 or len(song.get("bars") or []) < 2:
        return song
    beats = song["beats"]
    p = int(song["bars"][0].get("beat0", 0)) % 2
    bars = []
    for i in range(0, len(song["bars"]) - 1, 2):
        a, b = song["bars"][i], song["bars"][i + 1]
        bars.append(dict(a, beat0=(int(a.get("beat0", 0)) - p) // 2, end=b.get("end", b["t"]),
                         level=max(a.get("level", 0), b.get("level", 0))))
    return dict(song, beats=beats[p::2], period=float(song["period"]) * 2, bpm=round(float(song["bpm"]) / 2, 3),
                bars=bars, half_time=True)

# named palettes for song parts → CIE Lab target (L, a, b); None = that channel is free
COLOURS = {
    "red": (None, 55.0, 35.0), "orange": (None, 35.0, 55.0), "yellow": (None, 0.0, 70.0),
    "green": (None, -45.0, 40.0), "teal": (None, -30.0, -10.0), "blue": (None, 10.0, -45.0),
    "purple": (None, 40.0, -35.0), "pink": (None, 55.0, -5.0), "warm": (None, 20.0, 30.0),
    "cool": (None, -5.0, -25.0), "mono": (None, 0.0, 0.0), "dark": (25.0, None, None), "bright": (75.0, None, None),
}
SAME_COLOUR = 14.0      # Lab distance under which consecutive shots continue a colour run


def lab_dist(a, b) -> float:
    """Colour distance of two CIE Lab means; lightness counts half (a dark → bright step is softer than a hue jump)."""
    return math.sqrt(0.5 * (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def colour_target(value):
    """A part's colour: a COLOURS name or [L, a, b] → (L|None, a|None, b|None); None if not given or unknown."""
    if isinstance(value, str):
        return COLOURS.get(value.strip().lower())
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return tuple(None if v is None else float(v) for v in value)
    return None


def target_dist(lab, tgt) -> float:
    d = 0.0
    for v, t, w in zip(lab, tgt, (0.5, 1.0, 1.0)):
        if t is not None:
            d += w * (v - t) ** 2
    return math.sqrt(d)


def shot_of(c: dict) -> str:
    return str(c.get("shot") or c["id"].rsplit(":", 1)[0])


# ----------------------------------------------------------------------------- sections
def _level_at(song: dict, t: float) -> int:
    beats = np.asarray(song["beats"], np.float64)
    if not len(beats):
        return 0
    return plan.beat_level(song)[int(np.argmin(np.abs(beats - t)))]


def _snap_beat(song: dict, t: float) -> float:
    beats = np.asarray(song["beats"], np.float64)
    if not len(beats):
        return t
    b = float(beats[int(np.argmin(np.abs(beats - t)))])
    return b if abs(b - t) <= 0.6 * float(song["period"]) else t


def _auto_parts(song: dict, a: float, b: float, min_calm_bars: int = 4) -> list[dict]:
    """Music-driven parts of [a, b): runs of calm bars (level ≤ 1, ≥ min_calm_bars) → story, the rest → action,
    split at drops."""
    bars = [x for x in song["bars"] if a - 1e-6 <= x["t"] < b]
    runs: list[list] = []
    for x in bars:
        calm = x.get("level", 0) <= 1
        if runs and runs[-1][0] == calm:
            runs[-1][1].append(x)
        else:
            runs.append([calm, [x]])
    for r in runs:                       # short calm runs are breaths inside the action, not story parts
        if r[0] and len(r[1]) < min_calm_bars:
            r[0] = False
    parts: list[dict] = []
    for calm, items in runs:
        mode = "story" if calm else "action"
        if parts and parts[-1]["mode"] == mode:
            continue
        parts.append({"t0": float(items[0]["t"]), "mode": mode})
    if not parts:
        return [{"t0": a, "t1": b, "mode": "action", "auto": True}]
    parts[0]["t0"] = a
    drops = [d for d in song.get("drops", []) if a + 1.0 < d < b - 1.0]
    for d in drops:
        if all(abs(p["t0"] - d) > 0.5 for p in parts):
            mode = next((p["mode"] for p in reversed(parts) if p["t0"] < d), "action")
            parts.append({"t0": float(d), "mode": "action" if mode == "story" else mode})
    parts.sort(key=lambda p: p["t0"])
    for p, q in zip(parts, parts[1:] + [{"t0": b}]):
        p["t1"] = q["t0"]
        p["auto"] = True
    return [p for p in parts if p["t1"] - p["t0"] > 0.3]


def sections(song: dict, t0: float, t1: float, story: list[dict] | None = None) -> list[dict]:
    """[{t0, t1, mode, colour, acts, note}] covering [t0, t1]. Story parts may set "mode" (story|action|climax) and
    "colour" (COLOURS name or [L, a, b]); parts without a mode, and the gaps between parts, get one from the music
    (calm bars → story, else action). The part that starts at — or holds — the last drop of the song (in its last
    45 %) and has no explicit mode becomes the climax. Inner boundaries snap to the nearest beat."""
    parts: list[dict] = []
    for p in sorted(story or [], key=lambda x: float(x["t0"])):
        a, b = max(t0, float(p["t0"])), min(t1, float(p["t1"]))
        if b - a > 0.3:
            parts.append({"t0": a, "t1": b, "mode": p.get("mode") if p.get("mode") in MODES else None,
                          "colour": p.get("colour"), "acts": list(p.get("acts") or []) or None,
                          "note": p.get("note", "")})
    out: list[dict] = []
    cur = t0
    for p in parts + [{"t0": t1, "t1": t1}]:
        if p["t0"] - cur > 0.3:
            out += _auto_parts(song, cur, p["t0"])
        if p["t1"] > p["t0"]:
            if p["mode"] is None:
                bars = [x.get("level", 0) for x in song["bars"] if p["t0"] - 1e-6 <= x["t"] < p["t1"]]
                p = dict(p, mode="story" if bars and float(np.mean(bars)) <= 1.0 else "action", auto=True)
            out.append(p)
        cur = max(cur, p["t1"])
    drops = [d for d in song.get("drops", []) if t0 <= d < t1 and d >= 0.55 * float(song.get("duration") or t1)]
    if drops:
        last = drops[-1]
        for p in out:
            if p.get("auto") and p["t0"] - 0.6 <= last < p["t1"] - 1.0:
                if abs(p["t0"] - last) > 0.6:           # the climax starts on the drop itself
                    q = dict(p, t1=last)
                    p["t0"] = last
                    out.insert(out.index(p), q)
                p["mode"] = "climax"
                break
    for p, q in zip(out, out[1:]):                      # snap inner boundaries
        q["t0"] = p["t1"] = round(_snap_beat(song, p["t1"]), 4)
    out = [p for p in out if p["t1"] - p["t0"] > 0.3]
    for k, p in enumerate(out):
        p["k"] = k
        p.setdefault("colour", None)
        p.setdefault("acts", None)
    return out


def section_at(secs: list[dict], t: float) -> dict:
    for s in secs:
        if s["t0"] - 1e-6 <= t < s["t1"]:
            return s
    return secs[-1]


def section_cuts(song: dict, secs: list[dict], forced: list[float] | None = None) -> list[dict]:
    """Cut grid per section mode, plus a cut on every section boundary (a drop there stays a drop cut)."""
    period = float(song["period"])
    drops = song.get("drops", [])
    cuts: list[dict] = []
    for k, s in enumerate(secs):
        cpb, mgb = mode_grid(s["mode"], period)
        f = [x for x in forced or [] if s["t0"] < x < s["t1"]]
        for c in plan.cut_grid(song, s["t0"], s["t1"], cuts_per_bar=cpb, min_gap_beats=mgb, forced=f):
            room = 0.75 * mgb.get(c["level"], 1.0) * period     # the section's first and last shots keep their length
            if c["kind"] in ("drop", "vocal") or (c["t"] - s["t0"] >= room and s["t1"] - c["t"] >= 0.5 * room):
                cuts.append(c)
        if k:
            kind = "drop" if any(abs(d - s["t0"]) < 0.08 for d in drops) else "section"
            cuts.append({"t": s["t0"], "kind": kind, "level": _level_at(song, s["t0"]), "strength": 1.0})
    cuts.sort(key=lambda c: c["t"])
    out: list[dict] = []
    for c in cuts:
        if out and c["t"] - out[-1]["t"] < 0.5 * period - 1e-3:
            if c["kind"] in ("drop", "section") and out[-1]["kind"] not in ("drop", "section"):
                out[-1] = c
            continue
        out.append(c)
    return out


# ----------------------------------------------------------------------------- segments
def merged_shots(clips: list[dict]) -> list[dict]:
    """Whole long shots from their ≤ 3 s library windows — story parts need 3–8 s at native speed."""
    by: dict[str, list[dict]] = {}
    for c in clips:
        by.setdefault(shot_of(c), []).append(c)
    out = []
    for key, items in by.items():
        if len(items) < 2:
            continue
        items.sort(key=lambda c: c["t_in"])
        best = max(items, key=lambda c: c.get("score", 0.0))
        labs = [c["lab"] for c in items if c.get("lab") is not None]
        m = dict(best)
        m.update(id=f"{key}:all", merged=True, shot=key, t_in=items[0]["t_in"], t_out=items[-1]["t_out"],
                 dur=round(items[-1]["t_out"] - items[0]["t_in"], 3), dir_in=items[0].get("dir_in", (0, 0)),
                 dir_out=items[-1].get("dir_out", (0, 0)),
                 energy=round(float(np.mean([c.get("energy", 0.5) for c in items])), 3),
                 char=max(c.get("char", 0.0) for c in items),
                 static_frac=round(float(np.mean([c.get("static_frac", 0.0) for c in items])), 3),
                 impacts=sorted({t for c in items for t in c.get("impacts") or []}),
                 lab=[round(float(v), 1) for v in np.mean(labs, 0)] if labs else None)
        m.pop("dup", None)
        out.append(m)
    return out


def _supply(clips: list[dict]) -> float:
    """Typical long-shot length the library can give at native speed (p80 of whole shots), 1.6–8 s."""
    spans: dict[str, list[float]] = {}
    for c in clips:
        s = spans.setdefault(shot_of(c), [c["t_in"], c["t_out"]])
        s[0], s[1] = min(s[0], c["t_in"]), max(s[1], c["t_out"])
    vals = [b - a for a, b in spans.values()] or [3.0]
    return float(np.clip(np.percentile(vals, 80) - 0.15, 1.6, 8.0))


def _renumber(segs: list[dict]) -> list[dict]:
    for i, s in enumerate(segs):
        s["i"] = i
        s["t0"], s["t1"] = round(s["t0"], 4), round(s["t1"], 4)
        s["dur"] = round(s["t1"] - s["t0"], 4)
    return segs


def split_long(song: dict, segs: list[dict], max_len: float) -> list[dict]:
    """Story segments longer than the library's long shots are split on beats (no black, no 0.4× crawl)."""
    period = float(song["period"])
    beats = np.asarray(song["beats"], np.float64)
    out = []
    for s in segs:
        if s.get("mode") != "story" or s["dur"] <= max_len * 1.15 or s["dur"] < 2 * period:
            out.append(s)
            continue
        n = int(math.ceil(s["dur"] / max_len))
        edges = [s["t0"]]
        for k in range(1, n):
            t = s["t0"] + k * s["dur"] / n
            if len(beats):
                t = float(beats[int(np.argmin(np.abs(beats - t)))])
            if t - edges[-1] >= period and s["t1"] - t >= period:
                edges.append(t)
        edges.append(s["t1"])
        for j, (a, b) in enumerate(zip(edges[:-1], edges[1:])):
            out.append(dict(s, t0=a, t1=b, ta=a if j else s["ta"], accent=s["accent"] if not j else 0.0,
                            drop=s["drop"] and not j))
    return _renumber(out)


def set_speeds(song: dict, segs: list[dict], secs: list[dict], hero_speed: float = 0.5,
               supply: float = 8.0) -> list[dict]:
    """Speed, dissolves and impact anchoring per segment, by section mode. A calm (story) shot longer than the
    library's long shots (`supply` s: trailers cut fast) plays slower, down to 0.45×, instead of being chopped."""
    drops = song.get("drops", [])
    first_of: dict[int, int] = {}
    for s in segs:
        sec = section_at(secs, (s["t0"] + s["t1"]) / 2)
        s["mode"], s["section"] = sec["mode"], sec["k"]
        if sec.get("colour") is not None:
            s["colour"] = sec["colour"]
        first = sec["k"] not in first_of
        first_of.setdefault(sec["k"], s["i"])
        s["section_start"] = first
        s["speed"], s["min_rate"] = 1.0, 0.85
        if s["drop"]:
            s["burst"], s["use_impacts"] = 2.2, True
        elif s["accent"] >= 0.4 and s["level"] >= 2:
            s["use_impacts"] = True
        into_drop = any(abs(d - s["t1"]) < 0.08 for d in drops) and s["dur"] >= 1.0 and not s["drop"]
        hero = (s["mode"] == "story" and first and s["dur"] >= 2.0) or into_drop
        if hero:
            s["speed"], s["min_rate"], s["hero"] = hero_speed, 0.38, True
        elif s["mode"] == "story" and s["dur"] > supply * 1.05:
            s["speed"], s["min_rate"] = round(float(np.clip(supply / s["dur"], 0.45, 1.0)), 3), 0.38
    # dissolves inside story parts: every other cut, 0.2–1.0 s, the outgoing shot keeps playing under it
    k_in = 0
    for a, b in zip(segs, segs[1:]):
        if b["mode"] == "story" and not b["section_start"] and not b["drop"]:
            k_in += 1
            if k_in % 2 == 1:
                d = round(float(np.clip(0.25 * min(a["dur"], b["dur"]), 0.2, 1.0)), 3)
                b["xfade"], a["tail"] = d, d
        else:
            k_in = 0
    return segs


# ----------------------------------------------------------------------------- clip choice
class Hooks:
    """plan.assign_clips hooks: pools by mode, colour runs, motif matches, impacts, one-film climax."""

    def __init__(self, windows: list[dict], merged: list[dict], seed: int = 7, colour_weight: float = 1.3):
        self.windows, self.merged = windows, merged
        self.rng = random.Random(seed + 11)
        self.cw = colour_weight
        self.crun, self.crun_target, self.last_lab = 0, self.rng.randint(3, 8), None
        self.window_shots: set[str] = set()
        self.merged_shots: set[str] = set()

    def pool(self, seg: dict, clips: list[dict]) -> list[dict]:
        # a whole shot and its windows never both: the frames would repeat
        wins = [c for c in self.windows if shot_of(c) not in self.merged_shots]
        if seg.get("mode") == "story":
            return [c for c in self.merged if shot_of(c) not in self.window_shots] + wins
        return wins

    def relax(self, seg: dict) -> dict | None:
        if float(seg.get("min_rate") or 0.38) <= 0.5:
            return None
        return dict(seg, speed=min(float(seg.get("speed") or 1.0), 0.6), min_rate=0.38, relaxed=True)

    def score(self, seg: dict, c: dict, prev: dict | None) -> float:
        sc = 0.0
        lab = c.get("lab")
        if lab is not None and self.last_lab is not None and not seg.get("section_start"):
            d = lab_dist(self.last_lab, lab)
            if self.crun < self.crun_target:
                sc -= self.cw * min(1.0, d / 30.0)
            elif d > 15.0:
                sc += 0.1                          # the run is long enough: a colour step is welcome now
        tgt = colour_target(seg.get("colour"))
        if tgt is not None and lab is not None:
            sc -= 0.35 * min(1.0, target_dist(lab, tgt) / 40.0)
        if prev is not None and c.get("motif") and c.get("motif") == prev.get("motif") and shot_of(c) != shot_of(prev):
            sc += 0.6
        mode = seg.get("mode")
        if mode == "story":
            sc += 0.2 * plan.character(c) - 0.25 * max(0.0, c.get("energy", 0.5) - 0.8) + 0.1 * bool(c.get("merged"))
        elif mode == "climax" and prev is not None and c.get("film") == prev.get("film"):
            sc += 0.9
        if seg.get("use_impacts"):
            lo, hi = c["t_in"] + 0.06, c["t_out"] - 0.06
            if any(lo <= t <= hi for t in c.get("impacts") or []):
                sc += 0.5 if seg.get("drop") else 0.35
        return sc

    def picked(self, seg: dict, c: dict, prev: dict | None) -> None:
        lab = c.get("lab")
        if lab is not None:
            if self.last_lab is not None and lab_dist(self.last_lab, lab) < SAME_COLOUR:
                self.crun += 1
            else:
                self.crun, self.crun_target = 1, self.rng.randint(3, 8)
            self.last_lab = lab
        (self.merged_shots if c.get("merged") else self.window_shots).add(shot_of(c))


def annotate(segs: list[dict], by_id: dict) -> list[dict]:
    """Colour, motif and «the shot's own impact is on screen» per segment (for fx and stats)."""
    for s in segs:
        c = by_id.get(s.get("clip"))
        if c is None:
            continue
        s["lab"] = c.get("lab")
        if c.get("motif"):
            s["motif"] = c["motif"]
        span = s.get("src_span") or 0.0
        s["impact_shown"] = any(s["src_in"] <= t <= s["src_in"] + span for t in c.get("impacts") or [])
        s["shot"] = shot_of(c)
    return segs


def strobe_burst(segs: list[dict], clips: list[dict], fps: float = 30.0, n: int = 5, frames: float = 2.5) -> list[dict]:
    """The final drop opens with n inserts of 2–3 frames (the reference: up to 10 changes in 0.5 s), then its own
    shot continues where it would have been. Inserts: unused clips with the strongest impact and a character."""
    step = frames / fps
    drops = [s for s in segs if s["drop"] and s.get("clip") and s["dur"] >= 3 * step + 0.25]
    if not drops:
        return segs
    d = drops[-1]
    n = min(n, int((d["dur"] - 0.25) / step))
    used = {s.get("clip") for s in segs}
    used_shots = {s.get("shot") for s in segs}
    acts = d.get("acts")
    cand = [c for c in clips if c["id"] not in used and not c.get("merged") and c["t_out"] - c["t_in"] > step + 0.2
            and (not acts or c.get("act") in acts) and plan.character(c) >= 0.25]
    cand.sort(key=lambda c: (shot_of(c) in used_shots, -(c.get("impact", 0.0) + 0.5 * bool(c.get("impacts"))
                                                        + 0.3 * plan.character(c))))
    picks, shots = [], set()
    for c in cand:
        if shot_of(c) in shots:
            continue
        picks.append(c)
        shots.add(shot_of(c))
        if len(picks) >= n:
            break
    if len(picks) < 3:
        return segs
    inserts = []
    for k, c in enumerate(picks):
        a = d["t0"] + k * step
        anchor = plan.anchor_time({"use_impacts": True}, c, c["t_in"], c["t_out"])
        src_in = round(float(min(max(anchor - 0.02, c["t_in"]), c["t_out"] - step - 0.05)), 4)
        inserts.append({**{key: d[key] for key in ("level", "mode", "section") if key in d},
                        "t0": a, "t1": a + step, "drop": k == 0, "ta": a, "accent": 0.0, "clip": c["id"],
                        "src": c["src"], "film": c.get("film"), "act": c.get("act"), "src_in": src_in,
                        "ramp": {"base": 1.0, "peak": 0.0, "ta": a, "w": 0.1}, "src_span": round(step, 4),
                        "cx": c.get("cx", 0.5), "cx_916": c.get("cx_916", c.get("cx", 0.5)),
                        "cx_169": c.get("cx_169", 0.5), "dir_in": c.get("dir_in", (0, 0)),
                        "dir_out": c.get("dir_out", (0, 0)), "t_peak": c.get("t_peak"),
                        "abstract": plan.is_abstract(c), "character": round(plan.character(c), 2), "lab": c.get("lab"),
                        "impact_shown": bool(c.get("impacts")), "shot": shot_of(c), "strobe": True})
    rest = dict(d)
    new_t0 = d["t0"] + len(inserts) * step
    rest["src_in"] = round(d["src_in"] + fx.rate_integral(d["t0"], new_t0, d["ramp"]), 4)
    rest["src_span"] = round(max(0.0, d["src_span"] - (rest["src_in"] - d["src_in"])), 4)
    rest.update(t0=new_t0, drop=False, ta=max(d["ta"], new_t0))
    k = segs.index(d)
    return _renumber(segs[:k] + inserts + [rest] + segs[k + 1:])


# ----------------------------------------------------------------------------- effects
def shake_frac(shakes: list[dict], t0: float, t1: float, fps: float = 30.0, visible: float = 0.003) -> float:
    """Share of frames with a visible shake (offset envelope above `visible` of the frame width)."""
    if t1 <= t0:
        return 0.0
    ts = np.arange(t0, t1, 1.0 / fps)
    on = np.zeros(len(ts), bool)
    for h in shakes:
        if h["amp"] <= visible:
            continue
        life = h["tau"] * math.log(h["amp"] / visible)
        on |= (ts >= h["t"]) & (ts < h["t"] + life)
    return float(on.mean()) if len(on) else 0.0


def restrained_fx(song: dict, segs: list[dict], t0: float, t1: float, seed: int = 3, flash_budget: float = 4.0,
                  fps: float = 30.0, shake_max: float = 0.10) -> dict:
    """Effects of the reference: a punch on every drop, a white flash only on a drop whose shot shows no impact of
    its own (≤ flash_budget per minute), shake bursts on the kicks of the two bars after a drop (≤ shake_max of the
    frames), no whips."""
    rng = random.Random(seed)
    period = float(song["period"])
    flashes, punches, shakes = [], [], []
    budget = max(1, int(round(flash_budget * (t1 - t0) / 60.0)))
    kicks = sorted(song.get("kicks", []))
    for s in segs:
        if not s["drop"]:
            continue
        punches.append({"t": s["t0"], "amp": 0.07, "tau": 0.18})
        if not s.get("impact_shown") and len(flashes) < budget:
            flashes.append({"t": s["t0"], "frames": 2, "color": (255, 255, 255), "amp": 0.8})
        last = -9.0
        for t, st in kicks:
            if s["t0"] <= t < min(t1 - 0.1, s["t0"] + 8 * period) and st >= 0.3 and t - last >= 0.25:
                shakes.append({"t": round(float(t), 4), "amp": round(0.009 + 0.008 * st, 4), "tau": 0.11,
                               "seed": rng.uniform(0, 6.28)})
                last = t
    shakes.sort(key=lambda h: h["t"])
    while shakes and shake_frac(shakes, t0, t1, fps) > shake_max:
        shakes.pop()                       # trim the tails of the bursts first
    return {"flashes": flashes, "punches": punches, "shakes": shakes, "whips": []}


def black_breaths(song: dict, t0: float, t1: float, fps: float = 30.0, dip_db: float = 8.0, min_len: float = 0.4,
                  look_beats: int = 4, max_len: float = 2.9) -> list[dict]:
    """Black holds: a quiet dip (RMS ≥ dip_db under the median for ≥ min_len) within look_beats beats before a drop
    fades to black over 5 frames and the picture returns on the hit; the final drop also gets a 1-frame blink.
    [{"t0", "t1", "fade", "kind": breath|blink}] in song seconds."""
    rms = np.asarray(song.get("rms_db") or [], np.float64)
    step = 0.05                                   # audio.analyze_song keeps every 10th 5 ms frame
    period = float(song["period"])
    drops = [float(d) for d in song.get("drops", []) if t0 + 1.0 < d <= t1 - 0.2]
    out: list[dict] = []
    if len(rms):
        med = float(np.median(rms))
        for d in drops:
            i0, i1 = max(0, int((d - look_beats * period) / step)), min(len(rms), int(d / step))
            quiet = rms[i0:i1] < med - dip_db
            runs, start = [], None
            for k, q in enumerate(list(quiet) + [False]):
                if q and start is None:
                    start = k
                elif not q and start is not None:
                    runs.append((start, k))
                    start = None
            runs = [(a, b) for a, b in runs if (b - a) * step >= min_len]
            if runs:
                a, _ = runs[-1]
                b0 = max(t0, (i0 + a) * step, d - max_len)
                out.append({"t0": round(b0, 3), "t1": round(d, 3), "fade": round(5.0 / fps, 3), "kind": "breath"})
    if drops and not any(abs(b["t1"] - drops[-1]) < 1e-3 for b in out):
        out.append({"t0": round(drops[-1] - 1.0 / fps, 4), "t1": round(drops[-1], 4), "fade": 0.0, "kind": "blink"})
    return sorted(out, key=lambda b: b["t0"])


# ----------------------------------------------------------------------------- plan
def build_plan(song: dict, clips: list[dict], t0: float, t1: float, seed: int = 7, forced: list[float] | None = None,
               story: list[dict] | None = None, max_uses: int = 1, strobe: str = "last", breaths: bool = True,
               fade_out: bool = True, flash_budget: float = 4.0, colour_weight: float = 1.3,
               hero_speed: float = 0.5, fps: float = 30.0) -> dict:
    song = musical(song)
    period = float(song["period"])
    secs = sections(song, t0, t1, story)
    cuts = section_cuts(song, secs, forced)
    segs = plan.make_segments(song, t0, t1, cuts)
    windows = [c for c in clips if not c.get("merged")]
    merged = merged_shots(windows)
    for s in segs:
        s["mode"] = section_at(secs, (s["t0"] + s["t1"]) / 2)["mode"]
    supply = _supply(windows)
    segs = split_long(song, segs, supply / 0.55)
    segs = set_speeds(song, segs, secs, hero_speed, supply)
    hooks = Hooks(windows, merged, seed, colour_weight)
    story_map = [{"t0": s["t0"], "t1": s["t1"], "acts": s["acts"]} for s in secs if s.get("acts")] or None
    segs = plan.assign_clips(segs, windows + merged, period, seed=seed, story=story_map, max_uses=max_uses,
                             hooks=hooks)
    annotate(segs, {c["id"]: c for c in windows + merged})
    if strobe == "last":
        segs = strobe_burst(segs, windows, fps=fps)
    ev = restrained_fx(song, segs, t0, t1, seed=seed + 1, flash_budget=flash_budget, fps=fps)
    blacks = black_breaths(song, t0, t1, fps=fps) if breaths else []
    vfade = [0.0, 0.0]
    if fade_out and t1 >= float(song.get("duration") or t1) - 1.0:
        vfade = [0.0, round(float(np.clip(0.25 * (t1 - t0), 1.0, 4.5)), 2)]
    return {"t0": t0, "t1": t1, "bpm": song["bpm"], "period": song["period"], "style": "reference",
            "half_time": bool(song.get("half_time")), "long_shot_supply": round(supply, 2),
            "segments": segs, **ev, "blacks": blacks, "vfade": vfade,
            "sections": [{k: p.get(k) for k in ("t0", "t1", "mode", "colour", "acts", "note")} for p in secs]}


# ----------------------------------------------------------------------------- stats
def _windows_cpm(cut_t: list[float], t0: float, t1: float, win: float = 10.0, hop: float = 5.0) -> list[float]:
    if t1 - t0 < win:
        return [len(cut_t) / max(1e-6, t1 - t0) * 60.0]
    out = []
    w0 = t0
    while w0 + win <= t1 + 1e-6:
        out.append(sum(1 for t in cut_t if w0 <= t < w0 + win) * 60.0 / win)
        w0 += hop
    return out


def colour_numbers(segs: list[dict], seed: int = 0) -> dict:
    """Colour jump across cuts vs a shuffled order, and the share of runtime in colour runs (≥ 3 shots, ≥ 4 s)."""
    shown = [s for s in segs if s.get("clip") and s.get("lab") is not None and not s.get("strobe")]
    if len(shown) < 3:
        return {"colour_jump": None, "colour_runs": None}
    labs = [s["lab"] for s in shown]
    cut = [lab_dist(a, b) for a, b in zip(labs, labs[1:])]
    rng = random.Random(seed)
    sh = []
    for _ in range(20):
        p = labs[:]
        rng.shuffle(p)
        sh += [lab_dist(a, b) for a, b in zip(p, p[1:])]
    jump = float(np.mean(cut)) / max(1e-6, float(np.mean(sh)))
    total = sum(s["dur"] for s in segs) or 1.0
    in_runs, run = 0.0, [shown[0]]
    for a, b in zip(shown, shown[1:]):
        if lab_dist(a["lab"], b["lab"]) < SAME_COLOUR:
            run.append(b)
            continue
        dur = sum(x["dur"] for x in run)
        if len(run) >= 3 and dur >= 4.0:
            in_runs += dur
        run = [b]
    dur = sum(x["dur"] for x in run)
    if len(run) >= 3 and dur >= 4.0:
        in_runs += dur
    return {"colour_jump": round(jump, 3), "colour_runs": round(in_runs / total, 3)}


def stats(p: dict, song: dict | None = None, fps: float = 30.0) -> dict:
    """A plan in the reference's terms, with checks against TARGETS (ok: None when not measurable, e.g. no colour)."""
    segs = p["segments"]
    t0, t1 = float(p["t0"]), float(p["t1"])
    dur = max(1e-6, t1 - t0)
    lens = np.array([s["dur"] for s in segs if not s.get("strobe")] or [0.0])
    cut_t = [s["t0"] for s in segs[1:]]
    wins = _windows_cpm(cut_t, t0, t1)
    def rate(s):
        r = s.get("ramp") or {}
        return float(r.get("base", 1.0))
    native = sum(s["dur"] for s in segs if s.get("clip") and abs(rate(s) - 1.0) <= 0.05) / dur
    slow = sum(s["dur"] for s in segs if s.get("clip") and rate(s) <= 0.65) / dur
    col = colour_numbers(segs)
    shake = shake_frac(p.get("shakes", []), t0, t1, fps)
    flashes = len(p.get("flashes", [])) / dur * 60.0
    out = {
        "segments": len(segs), "missing_clips": sum(1 for s in segs if not s.get("clip")),
        "cuts_per_min": round(len(cut_t) / dur * 60.0, 1),
        "window_cpm_p10": round(float(np.percentile(wins, 10)), 1),
        "window_cpm_median": round(float(np.median(wins)), 1),
        "window_cpm_p90": round(float(np.percentile(wins, 90)), 1),
        "shot_len_median": round(float(np.median(lens)), 3), "shot_len_p90": round(float(np.percentile(lens, 90)), 3),
        "long_shot_share": round(sum(s["dur"] for s in segs if s["dur"] >= 3.0) / dur, 3),
        "native_share": round(native, 3), "slowmo_share": round(slow, 3), **col,
        "flashes_per_min": round(flashes, 1), "shake_frac": round(shake, 3),
        "dissolves": sum(1 for s in segs if s.get("xfade")), "blacks": len(p.get("blacks", [])),
        "strobe_inserts": sum(1 for s in segs if s.get("strobe")),
        "impacts_on_accents": sum(1 for s in segs if s.get("use_impacts") and s.get("impact_shown")),
        "films_used": sorted({s.get("film") for s in segs if s.get("film")}),
        "sections": [f"{x['mode']} {x['t0']:.1f}–{x['t1']:.1f}" for x in p.get("sections", [])],
    }
    lo, hi = TARGETS["window_p90"]
    checks = [
        ("colour_jump", col["colour_jump"], f"≤ {TARGETS['colour_jump']}",
         None if col["colour_jump"] is None else col["colour_jump"] <= TARGETS["colour_jump"]),
        ("colour_runs", col["colour_runs"], f"≥ {TARGETS['colour_runs']}",
         None if col["colour_runs"] is None or dur < 30 else col["colour_runs"] >= TARGETS["colour_runs"]),
        ("window_cpm_p10", out["window_cpm_p10"], f"≤ {TARGETS['window_p10']:g} (calm parts breathe)",
         None if dur < 40 else out["window_cpm_p10"] <= TARGETS["window_p10"]),
        ("window_cpm_p90", out["window_cpm_p90"], f"{lo:g}–{hi + 15:g}",
         None if dur < 20 else lo * 0.8 <= out["window_cpm_p90"] <= hi + 15),
        ("flashes_per_min", out["flashes_per_min"], f"≤ {TARGETS['flashes_per_min']:g}",
         out["flashes_per_min"] <= TARGETS["flashes_per_min"]),
        ("shake_frac", out["shake_frac"], f"≤ {TARGETS['shake_frac']}", out["shake_frac"] <= TARGETS["shake_frac"]),
        ("native_share", out["native_share"], f"≥ {TARGETS['native_share']}",
         out["native_share"] >= TARGETS["native_share"]),
        ("missing_clips", out["missing_clips"], "0", out["missing_clips"] == 0),
    ]
    out["checks"] = [{"name": n, "value": v, "target": t, "ok": ok} for n, v, t, ok in checks]
    return out
