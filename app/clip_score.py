"""Heuristic clip-quality score for ranking candidates (0..1).

Blends what we already have: the model's own score, a duration sweet-spot (shorts
live ~15-45s), and speech density from the cached transcript (a clip that is mostly
talking holds attention better than dead air). Pure + unit-testable; the transcript
word count is passed in, not read here.
"""

from __future__ import annotations


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def duration_fit(total_sec: float) -> float:
    if total_sec <= 0:
        return 0.0
    if total_sec < 15:
        return total_sec / 15.0            # too short → weak
    if total_sec <= 45:
        return 1.0                          # sweet spot
    return max(0.3, 1.0 - (total_sec - 45) / 120.0)  # long → decays, floor 0.3


def speech_density(word_count: int, total_sec: float) -> float:
    if total_sec <= 0:
        return 0.0
    wps = word_count / total_sec
    return _clamp(wps / 3.0)                # ~3 words/sec of speech → full marks


def clip_quality(model_score: float, total_sec: float, word_count: int) -> float:
    """Composite 0..1: 50% model, 25% duration fit, 25% speech density."""
    llm = _clamp(float(model_score or 0.0))
    q = 0.5 * llm + 0.25 * duration_fit(total_sec) + 0.25 * speech_density(word_count, total_sec)
    return round(q, 4)


def words_in_ranges(cues: list[dict], ranges: list[tuple[float, float]]) -> int:
    """Count transcript words whose phrase overlaps any of the clip's segments."""
    if not cues or not ranges:
        return 0
    total = 0
    for cue in cues:
        cs, ce = float(cue.get("start", 0)), float(cue.get("end", 0))
        if any(cs < r_end and ce > r_start for (r_start, r_end) in ranges):
            total += len(str(cue.get("text", "")).split())
    return total
