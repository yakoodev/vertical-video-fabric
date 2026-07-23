"""Cut refinement: snap LLM clip boundaries to real speech (and optionally scene
cuts) so clips never open mid-word or chop the punchline.

The video LLM picks *roughly* where a moment is. The cached Whisper transcript
gives exact phrase boundaries, and ffmpeg scenedetect gives shot boundaries — this
module pulls each segment's start/end onto those, per a chosen hypothesis, so the
user can compare "raw model" vs "phrase-safe" vs "tight" vs "phrase + scene-cut".

Pure functions over ``cues`` (``[{start,end,text}]``, phrase level) and
``cut_times`` (scene-change seconds) — trivially unit-testable, no ffmpeg here.
"""

from __future__ import annotations

DEFAULT_CUT_STRATEGY = "phrase"

# Each hypothesis = a full set of knobs. Compare them on the same analysis.
CUT_STRATEGIES: dict[str, dict] = {
    "off": {
        "label": "Как решила модель",
        "hint": "Без обработки — сырые границы от AI.",
        "enabled": False,
    },
    "phrase": {
        "label": "По фразам (рекомендуется)",
        "hint": "Старт на начало фразы, конец — договорить фразу, обрезать паузы по краям.",
        "enabled": True,
        "start_window": 2.5,   # как далеко искать начало фразы у старта
        "end_extend": 3.0,     # насколько тянуть конец, чтобы договорить фразу
        "trim_edges": True,    # обрезать тишину по краям до речи
        "snap_cuts": False,
        "cut_window": 0.0,
    },
    "tight": {
        "label": "Плотно (без пауз)",
        "hint": "Жёстко на границы фраз, режем все паузы — самый короткий чистый клип.",
        "enabled": True,
        "start_window": 4.0,
        "end_extend": 2.0,
        "trim_edges": True,
        "trim_internal": False,
        "snap_cuts": False,
        "cut_window": 0.0,
    },
    "cuts": {
        "label": "По фразам + склейкам",
        "hint": "Как «по фразам», плюс подтягивает края к ближайшему монтажному стыку.",
        "enabled": True,
        "start_window": 2.5,
        "end_extend": 3.0,
        "trim_edges": True,
        "snap_cuts": True,
        "cut_window": 0.6,     # притянуть к стыку, если он в этом окне
    },
}


def list_cut_strategies() -> list[dict]:
    return [{"key": k, "label": v["label"], "hint": v["hint"]} for k, v in CUT_STRATEGIES.items()]


def get_cut_strategy(name: str | None) -> dict:
    return CUT_STRATEGIES.get(str(name or "").strip().lower()) or CUT_STRATEGIES[DEFAULT_CUT_STRATEGY]


def refine_boundaries(
    start: float,
    end: float,
    cues: list[dict],
    strategy: str | None,
    *,
    cut_times: list[float] | None = None,
    duration: float = 0.0,
    min_duration: float = 5.0,
    max_duration: float = 180.0,
) -> tuple[float, float]:
    """Return refined (start, end) for one segment under the given hypothesis."""
    cfg = get_cut_strategy(strategy)
    if not cfg.get("enabled") or not cues:
        return start, end

    phrases = sorted(
        ({"s": float(c["start"]), "e": float(c["end"])} for c in cues if c.get("end", 0) > c.get("start", 0)),
        key=lambda p: p["s"],
    )
    if not phrases:
        return start, end

    sw = float(cfg.get("start_window", 2.0))
    ext = float(cfg.get("end_extend", 3.0))
    trim = bool(cfg.get("trim_edges"))

    # --- START: open on a phrase, don't clip a word ---
    new_start = start
    containing = next((p for p in phrases if p["s"] - 0.05 <= start <= p["e"]), None)
    if containing is not None:
        # We start mid-phrase: back up to the phrase start (finish opening cleanly).
        new_start = containing["s"]
    else:
        # In a gap: snap to the nearest phrase start within the window.
        cands = [p["s"] for p in phrases if abs(p["s"] - start) <= sw]
        if cands:
            new_start = min(cands, key=lambda s: abs(s - start))
        elif trim:
            # Trim leading dead air: jump to the first phrase after start.
            after = next((p["s"] for p in phrases if p["s"] >= start), None)
            if after is not None and after - start <= sw * 2:
                new_start = after

    # --- END: finish the phrase being spoken, don't chop the punchline ---
    new_end = end
    speaking = next((p for p in phrases if p["s"] <= end <= p["e"] + 0.05), None)
    if speaking is not None and speaking["e"] > end:
        new_end = min(speaking["e"], end + ext)
    elif trim:
        # Trailing dead air: pull back to the last phrase that ended before `end`.
        before = [p["e"] for p in phrases if p["e"] <= end]
        if before and end - before[-1] > 0.4:
            new_end = before[-1]

    # --- optional: nudge onto the nearest scene cut ---
    if cfg.get("snap_cuts") and cut_times:
        cw = float(cfg.get("cut_window", 0.5))
        near_s = [c for c in cut_times if abs(c - new_start) <= cw]
        if near_s:
            new_start = min(near_s, key=lambda c: abs(c - new_start))
        near_e = [c for c in cut_times if abs(c - new_end) <= cw]
        if near_e:
            new_end = min(near_e, key=lambda c: abs(c - new_end))

    # --- guards ---
    if new_end <= new_start:
        return start, end  # refinement produced nothing usable
    if duration > 0:
        new_start = max(0.0, min(new_start, duration))
        new_end = min(new_end, duration)
    # Enforce min duration by extending (end first, then start) rather than
    # discarding the clean boundaries.
    if new_end - new_start < min_duration:
        new_end = new_start + min_duration
        if duration > 0 and new_end > duration:
            new_end = duration
            new_start = max(0.0, new_end - min_duration)
    if new_end - new_start > max_duration:
        new_end = new_start + max_duration
    return round(new_start, 3), round(new_end, 3)
