"""Анализ видео: проверка качества повествования и повтор (из ``app/ai/service.py``)."""

from __future__ import annotations

from app.ai.service_options import (
    ANALYSIS_MODE_NARRATIVE, NARRATIVE_MAX_RAW_SEGMENT_DURATION_SEC, _analysis_mode, _highlights_clip_cap
)
from app.ai.service_segments import (
    NARRATIVE_MAX_CLIP_DURATION_SEC, NARRATIVE_TARGET_RECAP_CLIP_DURATION_SEC, _is_episode_recap_spec,
    _max_clip_duration_for_spec, _clip_total_duration
)

NARRATIVE_MAX_TOTAL_SELECTED_DURATION_SEC = 300.0
NARRATIVE_MAX_TOTAL_SELECTED_RATIO = 0.28


def _invalid_timestamp_count(result, source_duration: float) -> int:
    raw_segments = _raw_result_segments(result)
    invalid_count = 0
    for segment in raw_segments:
        start = float(segment.start_sec)
        end = float(segment.end_sec)
        if start < 0 or end <= start or start >= source_duration or end > source_duration + 0.5:
            invalid_count += 1
    return invalid_count


def _oversized_segment_count(result, max_duration_sec: float) -> int:
    raw_segments = _raw_result_segments(result)
    max_duration = max(5.0, float(max_duration_sec or NARRATIVE_MAX_RAW_SEGMENT_DURATION_SEC))
    return sum(1 for segment in raw_segments if float(segment.end_sec) - float(segment.start_sec) > max_duration)


def _raw_result_segments(result) -> list:
    raw_segments = [segment for clip in getattr(result, "clips", []) for segment in clip.segments]
    if not raw_segments:
        raw_segments = list(getattr(result, "segments", []) or [])
    return raw_segments


def _trim_narrative_clip_specs_to_budget(
    specs: list[dict],
    source_duration: float,
    segment_options: dict | None,
) -> list[dict]:
    if not specs or not segment_options or source_duration < 900:
        return specs
    if _analysis_mode(segment_options) != ANALYSIS_MODE_NARRATIVE:
        cap = _highlights_clip_cap(source_duration)
        kept = [
            spec
            for spec in specs
            if _clip_total_duration(spec) <= NARRATIVE_MAX_CLIP_DURATION_SEC
        ][:cap]
        return kept or specs[:cap]
    max_total = min(
        NARRATIVE_MAX_TOTAL_SELECTED_DURATION_SEC,
        max(NARRATIVE_MAX_CLIP_DURATION_SEC, source_duration * NARRATIVE_MAX_TOTAL_SELECTED_RATIO),
    )
    first = specs[0]
    if not _is_episode_recap_spec(first, source_duration):
        return specs
    if _clip_total_duration(first) > _max_clip_duration_for_spec(first, source_duration):
        return []
    kept = [first]
    total = _clip_total_duration(first)
    if total > max_total:
        return []
    for spec in specs[1:]:
        duration = _clip_total_duration(spec)
        if duration > _max_clip_duration_for_spec(spec, source_duration):
            continue
        if total + duration > max_total:
            continue
        kept.append(spec)
        total += duration
    return kept


def _narrative_retry_prompt(
    prompt: str,
    source_duration: float,
    reason: str,
    mode: str = ANALYSIS_MODE_NARRATIVE,
) -> str:
    shared = (
        f"{prompt}\n\n"
        "Correction pass: the previous answer is rejected because "
        f"{reason}.\n"
        f"The uploaded source duration is exactly {source_duration:.3f} seconds. "
        f"All start_sec and end_sec values must be between 0 and {source_duration:.3f}. "
        "Every individual segment must be 12 to 75 seconds, with 12 to 35 seconds as "
        "the preferred range. If a scene is longer, split it into multiple ordered "
        "segments inside the same clip instead of returning one continuous range. "
        "Do not trim through dialogue; start before a complete line begins and end "
        "after the line, reaction, or action resolves. "
        "Return a fresh clips[] answer using only the uploaded source timeline.\n"
    )
    if mode != ANALYSIS_MODE_NARRATIVE:
        return (
            f"{shared}"
            "Return 3 to 5 strong standalone clips. Each clip must work on its own with a fast "
            "hook, just enough context, and a clear payoff or punchline. The clip must END on that "
            "payoff: the last segment ends about one second after the punchline/reaction lands, never "
            "on a neutral line, a trailing ramble, or the first words of the next topic. Do not build an episode "
            "recap and do not cover the timeline in order. Group several closely related shots into "
            "one clip with multiple ordered segments[] only when they belong to the same moment; "
            "otherwise keep moments as separate clips. Keep each finished clip under "
            f"{NARRATIVE_MAX_CLIP_DURATION_SEC:.0f} seconds total. "
            "Write every clip title, clip description, segment title, segment description, and segment "
            "reason in Russian. Do not reuse timestamps from memory, another cut, a full episode, or "
            "any range outside the uploaded file."
        )
    recap_bounds = ""
    if source_duration >= 900:
        recap_bounds = (
            f"For the required Episode Story Recap, the first segment must start before "
            f"{source_duration * 0.35:.1f}s, and the final segment must end after "
            f"{source_duration * 0.65:.1f}s. "
        )
    return (
        f"{shared}"
        "For a long anime/series episode, return 2 to 3 self-contained main story clips. "
        "The first clip must be titled like Episode Story Recap / Main Plot Recap and contain "
        "4 to 6 ordered segments: premise, inciting incident, key secret/reveal, protagonist "
        "choice or mistake, crisis/consequence, and new direction. The recap clip may pull "
        "segments from distant parts of the source, but keep the whole recap around 90 to "
        f"{NARRATIVE_TARGET_RECAP_CLIP_DURATION_SEC:.0f} seconds after segments are combined. "
        f"{recap_bounds}"
        "Other clips must work as their own shorts: hook, local context, conflict/reveal, and payoff. "
        "Write every clip title, clip description, segment title, segment description, and segment reason in Russian. "
        "Do not tile the episode into consecutive chapters. It is correct to skip connective "
        "scenes, weak setup, and scenes that only matter in a full-episode recap. Do not reuse "
        "timestamps from memory, another cut, a full episode, or any range outside the uploaded file."
    )
