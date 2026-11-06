"""Анализ видео: куски и бюджет пересказа (из ``app/ai/service.py``)."""

from __future__ import annotations

from itertools import combinations

from app.store import MAX_SEGMENT_DURATION_SEC
from app.ai.service_options import ANALYSIS_MODE_NARRATIVE

COALESCE_SINGLE_SEGMENT_CLIP_GAP_SEC = 45.0
COALESCE_SINGLE_SEGMENT_CLIP_MAX_DURATION_SEC = 150.0


NARRATIVE_MAX_CLIP_DURATION_SEC = 135.0
NARRATIVE_TARGET_RECAP_CLIP_DURATION_SEC = 165.0


NARRATIVE_RECAP_MIN_SEGMENTS = 4
NARRATIVE_RECAP_MIN_SPAN_RATIO = 0.45
NARRATIVE_RECAP_MIN_SPAN_SEC = 480.0


def _recap_segment_subsets_for_budget(
    segments: list[dict],
    source_duration: float,
    max_total_duration: float,
) -> list[list[dict]]:
    if len(segments) <= NARRATIVE_RECAP_MIN_SEGMENTS:
        return []
    candidates: list[list[dict]] = []
    indices = range(len(segments))
    for size in range(len(segments) - 1, NARRATIVE_RECAP_MIN_SEGMENTS - 1, -1):
        for selected_indices in combinations(indices, size):
            if 0 not in selected_indices or len(segments) - 1 not in selected_indices:
                continue
            subset = [dict(segments[index]) for index in selected_indices]
            if _segments_total_duration(subset) > max_total_duration:
                continue
            if not _segments_look_like_recap(subset, source_duration):
                continue
            candidates.append(subset)
    return candidates


def _shrink_segments_to_total_duration(
    segments: list[dict],
    max_total_duration: float,
    *,
    min_segment_duration: float,
) -> list[dict]:
    total = _segments_total_duration(segments)
    if total <= max_total_duration:
        return segments
    shrunk = [dict(segment) for segment in segments]
    excess = total - max_total_duration
    while excess > 0.001:
        adjustable = [
            segment
            for segment in shrunk
            if _segment_duration(segment) > min_segment_duration + 0.001
        ]
        if not adjustable:
            break
        cut_each = excess / len(adjustable)
        removed = 0.0
        for segment in adjustable:
            duration = _segment_duration(segment)
            cut = min(cut_each, duration - min_segment_duration)
            if cut <= 0:
                continue
            segment["start_sec"] = round(float(segment["start_sec"]) + cut / 2, 3)
            segment["end_sec"] = round(float(segment["end_sec"]) - cut / 2, 3)
            removed += cut
        if removed <= 0.001:
            break
        excess -= removed
    return shrunk


def _expand_range_to_duration(
    start: float,
    end: float,
    min_duration: float,
    source_duration: float,
) -> tuple[float, float]:
    duration = end - start
    missing = max(0.0, min_duration - duration)
    start -= missing / 2
    end += missing / 2
    if start < 0:
        end -= start
        start = 0.0
    if source_duration > 0 and end > source_duration:
        overflow = end - source_duration
        start = max(0.0, start - overflow)
        end = source_duration
    return start, end


def _is_episode_recap_spec(spec: dict, source_duration: float) -> bool:
    return _is_episode_recap_shape(spec, source_duration)


def _is_episode_recap_shape(spec: dict, source_duration: float) -> bool:
    return _segments_look_like_recap(spec.get("segments") or [], source_duration)


def _segments_look_like_recap(segments: list[dict], source_duration: float) -> bool:
    return not _recap_shape_problem(segments, source_duration)


def _recap_shape_problem(segments: list[dict], source_duration: float) -> str:
    if len(segments) < NARRATIVE_RECAP_MIN_SEGMENTS:
        return (
            f"recap has only {len(segments)} segment(s); it needs "
            f"{NARRATIVE_RECAP_MIN_SEGMENTS} to 6 ordered main-plot segments"
        )
    start = min(float(segment["start_sec"]) for segment in segments)
    end = max(float(segment["end_sec"]) for segment in segments)
    if source_duration >= 900:
        latest_allowed_setup = source_duration * 0.35
        earliest_allowed_resolution = source_duration * 0.65
        if start > latest_allowed_setup:
            return (
                f"recap starts too late at {start:.1f}s; the first recap segment must "
                f"start before {latest_allowed_setup:.1f}s and establish the early setup/inciting problem"
            )
        if end < earliest_allowed_resolution:
            return (
                f"recap ends too early at {end:.1f}s; the final recap segment must "
                f"end after {earliest_allowed_resolution:.1f}s and show the consequence or new direction"
            )
    span = end - start
    min_span = min(NARRATIVE_RECAP_MIN_SPAN_SEC, max(0.0, source_duration * NARRATIVE_RECAP_MIN_SPAN_RATIO))
    if span < min_span:
        return (
            f"recap covers only {span:.1f}s of source timeline; it must span at least "
            f"{min_span:.1f}s across setup, reveal, crisis, and consequence"
        )
    return ""


def _max_clip_duration_for_spec(
    spec: dict,
    source_duration: float,
    mode: str = ANALYSIS_MODE_NARRATIVE,
) -> float:
    if mode == ANALYSIS_MODE_NARRATIVE and _is_episode_recap_shape(spec, source_duration):
        return NARRATIVE_TARGET_RECAP_CLIP_DURATION_SEC
    return NARRATIVE_MAX_CLIP_DURATION_SEC


def _clip_total_duration(spec: dict) -> float:
    return _segments_total_duration(spec.get("segments") or [])


def _segments_total_duration(segments: list[dict]) -> float:
    return sum(_segment_duration(segment) for segment in segments)


def _merge_close_segment_ranges(
    segments: list[dict],
    merge_gap_sec: float,
    *,
    max_duration_sec: float = float(MAX_SEGMENT_DURATION_SEC),
) -> list[dict]:
    if not segments:
        return []
    merged: list[dict] = []
    max_duration = max(5.0, float(max_duration_sec or MAX_SEGMENT_DURATION_SEC))
    for segment in segments:
        if not merged:
            merged.append(dict(segment))
            continue
        previous = merged[-1]
        gap = float(segment["start_sec"]) - float(previous["end_sec"])
        merged_end = max(float(previous["end_sec"]), float(segment["end_sec"]))
        overlap = gap < 0
        allowed_duration = max_duration
        if gap <= merge_gap_sec and merged_end - float(previous["start_sec"]) <= allowed_duration:
            previous["end_sec"] = merged_end
            previous["title"] = _join_short_text(previous.get("title"), segment.get("title"), " + ", 100)
            previous["description"] = _join_short_text(previous.get("description"), segment.get("description"), " ", 500)
            previous["reason"] = _join_short_text(previous.get("reason"), segment.get("reason"), " ", 500)
            previous["score"] = max(float(previous.get("score") or 0), float(segment.get("score") or 0))
            if not previous.get("category"):
                previous["category"] = segment.get("category") or ""
            if not previous.get("color"):
                previous["color"] = segment.get("color") or "#64748B"
            continue
        if overlap:
            trimmed = dict(segment)
            trimmed["start_sec"] = round(float(previous["end_sec"]), 3)
            if float(trimmed["end_sec"]) - float(trimmed["start_sec"]) < 5:
                continue
            merged.append(trimmed)
            continue
        merged.append(dict(segment))
    return merged


def _join_short_text(first, second, separator: str, limit: int) -> str:
    parts = [str(value or "").strip() for value in (first, second) if str(value or "").strip()]
    return separator.join(parts)[:limit]


def _coalesce_single_segment_clip_specs(specs: list[dict]) -> list[dict]:
    coalesced: list[dict] = []
    pending: list[dict] = []

    def flush_pending() -> None:
        if not pending:
            return
        if len(pending) == 1:
            coalesced.append(pending[0])
        else:
            coalesced.append(_merge_single_segment_clip_specs(pending))
        pending.clear()

    for spec in specs:
        if len(spec.get("segments") or []) != 1:
            flush_pending()
            coalesced.append(spec)
            continue
        if not pending or _can_join_single_segment_spec(pending, spec):
            pending.append(spec)
            continue
        flush_pending()
        pending.append(spec)
    flush_pending()
    return coalesced


def _can_join_single_segment_spec(pending: list[dict], next_spec: dict) -> bool:
    previous = pending[-1]["segments"][-1]
    next_segment = next_spec["segments"][0]
    gap = float(next_segment["start_sec"]) - float(previous["end_sec"])
    if gap < -0.5 or gap > COALESCE_SINGLE_SEGMENT_CLIP_GAP_SEC:
        return False
    total_duration = sum(_segment_duration(segment) for spec in [*pending, next_spec] for segment in spec["segments"])
    return total_duration <= COALESCE_SINGLE_SEGMENT_CLIP_MAX_DURATION_SEC


def _merge_single_segment_clip_specs(specs: list[dict]) -> dict:
    segments = [spec["segments"][0] for spec in specs]
    first = specs[0]
    return {
        "title": _combined_clip_title(specs),
        "description": _combined_clip_description(specs),
        "score": max(float(spec.get("score") or 0) for spec in specs),
        "category": first.get("category") or "montage",
        "color": first.get("color") or "#64748B",
        "segments": segments,
    }


def _combined_clip_title(specs: list[dict]) -> str:
    titles = [str(spec.get("title") or "").strip() for spec in specs if str(spec.get("title") or "").strip()]
    if not titles:
        return "Сборный клип"
    if len(titles) == 2:
        return f"{titles[0]} + {titles[1]}"[:100]
    return f"{titles[0]} + еще {len(titles) - 1} момента"[:100]


def _combined_clip_description(specs: list[dict]) -> str:
    descriptions = [
        str(spec.get("description") or "").strip()
        for spec in specs
        if str(spec.get("description") or "").strip()
    ]
    if not descriptions:
        return "Связанные моменты объединены в один клип из нескольких сегментов."
    return " ".join(descriptions)[:500]


def _segment_duration(segment: dict) -> float:
    return max(0.0, float(segment.get("end_sec") or 0) - float(segment.get("start_sec") or 0))
