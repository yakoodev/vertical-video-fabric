from __future__ import annotations

import array
import math
import re
import subprocess
from itertools import combinations
from pathlib import Path

from app.analysis_preprocess import normalize_analysis_preprocessing, prepare_source_for_analysis
from app.ai.contracts import AnalysisClip
from app.ai.registry import get_video_analyzer
from app.default_prompts import BASE_ANALYSIS_PROMPT
from app.settings import settings
from app.store import AppStore, MAX_SEGMENT_DURATION_SEC


PROMPT_VERSION = "vertical-short-v1"
# Episodic post-processing (padding, merging, boundary snapping, oversize guards)
# runs in two modes. "narrative" forces an Episode Story Recap as the first clip
# and is opted into by prompts that ask for a recap (e.g. the Series preset).
# "highlights" keeps the same phrase-safe segment hygiene but returns several
# strong standalone clips instead, which is what the Anime preset now uses.
ANALYSIS_MODE_NARRATIVE = "narrative"
ANALYSIS_MODE_HIGHLIGHTS = "highlights"
HIGHLIGHTS_MAX_CLIPS = 6
COALESCE_SINGLE_SEGMENT_CLIP_GAP_SEC = 45.0
COALESCE_SINGLE_SEGMENT_CLIP_MAX_DURATION_SEC = 150.0
NARRATIVE_SEGMENT_PADDING_SEC = 4.0
NARRATIVE_MIN_SEGMENT_DURATION_SEC = 20.0
NARRATIVE_MERGE_ADJACENT_SEGMENT_GAP_SEC = 3.0
NARRATIVE_MAX_RAW_SEGMENT_DURATION_SEC = 75.0
NARRATIVE_MAX_SEGMENT_DURATION_SEC = 85.0
NARRATIVE_MAX_CLIP_DURATION_SEC = 135.0
NARRATIVE_TARGET_RECAP_CLIP_DURATION_SEC = 165.0
NARRATIVE_MAX_RECAP_CLIP_DURATION_SEC = 210.0
NARRATIVE_MAX_TOTAL_SELECTED_DURATION_SEC = 300.0
NARRATIVE_MAX_TOTAL_SELECTED_RATIO = 0.28
NARRATIVE_MIN_FINAL_RECAP_SEGMENT_SEC = 10.0
NARRATIVE_RECAP_MIN_SEGMENTS = 4
NARRATIVE_RECAP_MIN_SPAN_RATIO = 0.45
NARRATIVE_RECAP_MIN_SPAN_SEC = 480.0
NARRATIVE_AUDIO_BOUNDARY_SEARCH_SEC = 8.0
NARRATIVE_AUDIO_BOUNDARY_MAX_EXTRA_SEC = 8.0
NARRATIVE_SILENCE_THRESHOLD_DB = -35
NARRATIVE_SILENCE_MIN_DURATION_SEC = 0.25
NARRATIVE_ENERGY_SAMPLE_RATE = 8000
NARRATIVE_ENERGY_WINDOW_SEC = 0.2
NARRATIVE_ENERGY_LOCAL_RADIUS = 2
NARRATIVE_ENERGY_PERCENTILE = 0.35
SILENCE_START_RE = re.compile(r"silence_start:\s*(?P<time>\d+(?:\.\d+)?)")
SILENCE_END_RE = re.compile(r"silence_end:\s*(?P<time>\d+(?:\.\d+)?)")


class VideoAnalysisService:
    def __init__(self, store: AppStore) -> None:
        self.store = store

    def run_analysis(
        self,
        source_id: int,
        provider: str | None = None,
        model: str | None = None,
        prompt: str | None = None,
        preprocessing: dict | None = None,
    ) -> dict:
        selected_provider = (
            provider
            or self.store.get_app_setting_value("default_ai_provider", settings.ai_video_provider)
            or settings.ai_video_provider
            or "mock"
        ).strip().lower()
        selected_model = (
            model
            or self.store.get_app_setting_value("default_ai_model", "")
            or _default_model(selected_provider)
        ).strip()
        source = self.store.get_source(source_id)
        saved_prompt = str(self.store.get_app_setting_value("analysis_prompt", "") or "").strip()
        selected_prompt = (
            prompt
            or _source_specific_analysis_prompt(self.store, source)
            or saved_prompt
            or _prompt_from_preset(self.store.get_default_prompt_preset("analysis"))
            or BASE_ANALYSIS_PROMPT
        ).strip()
        if source["status"] == "failed":
            raise ValueError("failed source cannot be analyzed")
        normalized_preprocessing = normalize_analysis_preprocessing(preprocessing)
        segment_options = _segment_postprocess_options(source, selected_prompt, selected_provider)
        analysis = self.store.create_ai_analysis(
            source_id,
            selected_provider,
            model=selected_model,
            prompt_version=PROMPT_VERSION,
            request={"prompt": selected_prompt, "preprocessing": normalized_preprocessing},
        )
        self.store.update_source(source_id, status="analyzing", error="")
        self.store.mark_ai_analysis_running(analysis["id"])
        result = None
        retry_meta = None
        preprocessing_meta: dict = {}
        try:
            analyzer_source, preprocessing_meta = prepare_source_for_analysis(source, normalized_preprocessing)
            analysis_segment_options = _segment_options_with_audio_boundaries(
                segment_options,
                analyzer_source,
                float(source.get("duration_sec") or 0),
            )
            analyzer = get_video_analyzer(selected_provider)
            result = analyzer.analyze(analyzer_source, selected_prompt, selected_model)
            retry_reason = _narrative_retry_reason(
                result,
                float(source.get("duration_sec") or 0),
                analysis_segment_options,
            )
            if retry_reason:
                retry_prompt = _narrative_retry_prompt(
                    selected_prompt,
                    float(source.get("duration_sec") or 0),
                    retry_reason,
                    mode=_analysis_mode(analysis_segment_options),
                )
                result = analyzer.analyze(analyzer_source, retry_prompt, selected_model)
                retry_meta = {"reason": retry_reason}
            quality_error = _narrative_quality_error(
                result,
                float(source.get("duration_sec") or 0),
                analysis_segment_options,
            )
            if quality_error:
                raise RuntimeError(f"AI analyzer returned invalid narrative analysis: {quality_error}")
            created_plans = _persist_clip_plans(
                self.store,
                source_id=source_id,
                analysis_id=analysis["id"],
                result=result,
                source_duration=float(source.get("duration_sec") or 0),
                coalesce_single_segment_clips=selected_provider != "mock" and not segment_options,
                segment_options=analysis_segment_options,
            )
            if not created_plans:
                raise RuntimeError("AI analyzer returned no valid segments")
            self.store.delete_generated_clip_plans_for_source(source_id, exclude_analysis_id=analysis["id"])
            usage = dict(result.usage)
            if retry_meta:
                usage["analysis_retry"] = retry_meta
            if preprocessing_meta.get("enabled"):
                usage["analysis_preprocessing"] = preprocessing_meta
            analysis = self.store.finish_ai_analysis(
                analysis["id"],
                "succeeded",
                response=result.response,
                usage=usage,
            )
            self.store.update_source(source_id, status="analyzed", error="")
            return analysis
        except Exception as exc:  # noqa: BLE001 - failures are part of analysis lifecycle
            response = {"error": str(exc)}
            if result is not None and getattr(result, "response", None):
                response = dict(result.response)
                response["error"] = str(exc)
            try:
                self.store.delete_generated_outputs_for_analysis(analysis["id"])
            except Exception as cleanup_exc:  # noqa: BLE001 - preserve the original analysis failure
                response["cleanup_error"] = _safe_error(cleanup_exc)
            failed_usage = dict(getattr(result, "usage", {}) or {})
            if retry_meta:
                failed_usage["analysis_retry"] = retry_meta
            if preprocessing_meta.get("enabled"):
                failed_usage["analysis_preprocessing"] = preprocessing_meta
            analysis = self.store.finish_ai_analysis(
                analysis["id"],
                "failed",
                response=response,
                usage=failed_usage,
                error=_safe_error(exc),
            )
            self.store.update_source(source_id, status="ready", error="")
            return analysis


def _default_model(provider: str) -> str:
    if provider == "polza":
        return settings.polza_video_model
    if provider == "gemini":
        return settings.gemini_video_model
    if provider == "artemox":
        return settings.artemox_video_model
    return "mock-video-analyzer"


def _safe_error(exc: Exception) -> str:
    return str(exc).strip()[:500] or "analysis failed"


def _prompt_from_preset(preset: dict | None) -> str:
    return str((preset or {}).get("prompt") or "").strip()


def _source_specific_analysis_prompt(store: AppStore, source: dict) -> str:
    if not _source_looks_like_anime(source):
        return ""
    for preset in store.list_prompt_presets("analysis"):
        if str(preset.get("label") or "").strip().lower() == "anime analysis":
            return _prompt_from_preset(preset)
    return ""


def _source_looks_like_anime(source: dict) -> bool:
    source_type = str(source.get("source_type") or "").lower()
    original_url = str(source.get("original_url") or "").lower()
    original_filename = str(source.get("original_filename") or "").lower()
    haystack = f"{source_type} {original_url} {original_filename}"
    return any(marker in haystack for marker in ("smotvibe", "yummyani", "anime"))


def _segment_postprocess_options(source: dict, prompt: str, provider: str) -> dict:
    if provider == "mock":
        return {}
    prompt_text = prompt.lower()
    episodic = (
        _source_looks_like_anime(source)
        or "anime" in prompt_text
        or "tv series" in prompt_text
        or "episodic" in prompt_text
    )
    if not episodic:
        return {}
    # Narrative (recap-first) mode is opted into only by prompts that explicitly
    # ask for the structured recap clip, so a casual mention of the word "recap"
    # in a highlights prompt does not flip the mode.
    wants_recap = "episode story recap" in prompt_text or "main plot recap" in prompt_text
    return {
        "mode": ANALYSIS_MODE_NARRATIVE if wants_recap else ANALYSIS_MODE_HIGHLIGHTS,
        "padding_sec": NARRATIVE_SEGMENT_PADDING_SEC,
        "min_duration_sec": NARRATIVE_MIN_SEGMENT_DURATION_SEC,
        "merge_adjacent_gap_sec": NARRATIVE_MERGE_ADJACENT_SEGMENT_GAP_SEC,
        "max_duration_sec": NARRATIVE_MAX_SEGMENT_DURATION_SEC,
        "max_raw_duration_sec": NARRATIVE_MAX_RAW_SEGMENT_DURATION_SEC,
    }


def _analysis_mode(segment_options: dict | None) -> str:
    return str((segment_options or {}).get("mode") or ANALYSIS_MODE_NARRATIVE)


def _segment_options_with_audio_boundaries(
    segment_options: dict | None,
    source: dict,
    source_duration: float,
) -> dict | None:
    if not segment_options:
        return segment_options
    options = dict(segment_options)
    boundaries = _detect_audio_boundaries(Path(str(source.get("local_path") or "")), source_duration)
    if boundaries:
        options["audio_boundaries"] = boundaries
        options["boundary_search_sec"] = NARRATIVE_AUDIO_BOUNDARY_SEARCH_SEC
        options["boundary_max_extra_sec"] = NARRATIVE_AUDIO_BOUNDARY_MAX_EXTRA_SEC
    return options


def _segments_for_store(
    segments,
    source_duration: float,
    *,
    padding_sec: float = 0.0,
    min_duration_sec: float = 5.0,
    max_duration_sec: float = 180.0,
    audio_boundaries: list[float] | None = None,
    boundary_search_sec: float = 0.0,
    boundary_max_extra_sec: float = 0.0,
) -> list[dict]:
    normalized: list[dict] = []
    for segment in segments:
        data = segment.to_store_dict()
        start = max(0.0, float(data.get("start_sec") or 0) - max(0.0, padding_sec))
        end = float(data.get("end_sec") or 0) + max(0.0, padding_sec)
        if source_duration > 0:
            if start >= source_duration:
                continue
            end = min(end, source_duration)
        if end <= start:
            continue
        min_duration = max(5.0, float(min_duration_sec or 5.0))
        if end - start < min_duration:
            start, end = _expand_range_to_duration(start, end, min_duration, source_duration)
        max_duration = max(min_duration, float(max_duration_sec or 180.0))
        if audio_boundaries:
            start, end = _expand_range_to_audio_boundaries(
                start,
                end,
                audio_boundaries,
                source_duration,
                search_sec=float(boundary_search_sec or 0),
                max_extra_sec=float(boundary_max_extra_sec or 0),
                max_duration_sec=max_duration,
            )
        if end - start > max_duration:
            end = start + max_duration
            if source_duration > 0:
                end = min(end, source_duration)
        if end <= start or end - start < 5:
            continue
        data["start_sec"] = round(start, 3)
        data["end_sec"] = round(end, 3)
        normalized.append(data)
    return normalized


def _persist_clip_plans(
    store: AppStore,
    source_id: int,
    analysis_id: int,
    result,
    source_duration: float,
    coalesce_single_segment_clips: bool = False,
    segment_options: dict | None = None,
) -> list[dict]:
    created_plans: list[dict] = []
    if getattr(result, "clips", None):
        clip_plans = _clip_plan_specs(result.clips, source_duration, segment_options=segment_options)
        clip_plans = _trim_narrative_clip_specs_to_budget(clip_plans, source_duration, segment_options)
        if coalesce_single_segment_clips:
            clip_plans = _coalesce_single_segment_clip_specs(clip_plans)
        for index, clip in enumerate(clip_plans):
            created_segments = store.create_ai_segments(analysis_id, clip["segments"])
            created_plans.append(
                store.create_clip_plan(
                    source_id,
                    analysis_id,
                    clip["title"],
                    description=clip["description"],
                    segment_ids=[segment["id"] for segment in created_segments],
                    score=clip["score"],
                    category=clip["category"],
                    color=clip["color"],
                    sort_order=index,
                )
            )
        return created_plans

    segments = _segments_for_store(result.segments, source_duration, **_segment_store_options(segment_options))
    if not segments:
        return []
    clip_plans = [
        {
            "title": segment["title"],
            "description": segment["description"],
            "score": segment["score"],
            "category": segment["category"],
            "color": segment["color"],
            "segments": [segment],
        }
        for segment in segments
    ]
    clip_plans = _trim_narrative_clip_specs_to_budget(clip_plans, source_duration, segment_options)
    if coalesce_single_segment_clips:
        clip_plans = _coalesce_single_segment_clip_specs(clip_plans)
    for index, clip in enumerate(clip_plans):
        created_segments = store.create_ai_segments(analysis_id, clip["segments"])
        created_plans.append(
            store.create_clip_plan(
                source_id,
                analysis_id,
                clip["title"],
                description=clip["description"],
                segment_ids=[segment["id"] for segment in created_segments],
                score=clip["score"],
                category=clip["category"],
                color=clip["color"],
                sort_order=index,
            )
        )
    return created_plans


def _clip_plan_specs(
    clips: list[AnalysisClip],
    source_duration: float,
    *,
    segment_options: dict | None = None,
) -> list[dict]:
    specs = []
    for index, clip in enumerate(clips):
        segments = _segments_for_store(clip.segments, source_duration, **_segment_store_options(segment_options))
        merge_gap_sec = float((segment_options or {}).get("merge_adjacent_gap_sec") or 0)
        if merge_gap_sec > 0:
            segments = _merge_close_segment_ranges(
                segments,
                merge_gap_sec,
                max_duration_sec=float((segment_options or {}).get("max_duration_sec") or MAX_SEGMENT_DURATION_SEC),
            )
        if (
            index == 0
            and segment_options
            and _analysis_mode(segment_options) == ANALYSIS_MODE_NARRATIVE
            and source_duration >= 900
            and _segments_look_like_recap(segments, source_duration)
            and sum(_segment_duration(segment) for segment in segments) > NARRATIVE_TARGET_RECAP_CLIP_DURATION_SEC
        ):
            segments = _fit_recap_segments_to_render_budget(
                clip.segments,
                segments,
                source_duration,
                segment_options,
                merge_gap_sec,
            )
        if not segments:
            continue
        specs.append(
            {
                "title": clip.title,
                "description": clip.description,
                "score": clip.score,
                "category": clip.category,
                "color": clip.color,
                "segments": segments,
            }
        )
    return specs


def _segment_store_options(segment_options: dict | None) -> dict:
    segment_options = segment_options or {}
    return {
        "padding_sec": float(segment_options.get("padding_sec") or 0),
        "min_duration_sec": float(segment_options.get("min_duration_sec") or 5),
        "max_duration_sec": float(segment_options.get("max_duration_sec") or MAX_SEGMENT_DURATION_SEC),
        "audio_boundaries": list(segment_options.get("audio_boundaries") or []),
        "boundary_search_sec": float(segment_options.get("boundary_search_sec") or 0),
        "boundary_max_extra_sec": float(segment_options.get("boundary_max_extra_sec") or 0),
    }


def _compact_recap_segment_options(segment_options: dict) -> dict:
    compact = dict(segment_options)
    compact["padding_sec"] = min(float(compact.get("padding_sec") or 0), 1.0)
    compact["boundary_search_sec"] = min(float(compact.get("boundary_search_sec") or 0), 1.5)
    compact["boundary_max_extra_sec"] = min(float(compact.get("boundary_max_extra_sec") or 0), 1.5)
    return compact


def _raw_recap_segment_options(segment_options: dict) -> dict:
    raw = dict(segment_options)
    raw["padding_sec"] = 0.0
    raw["boundary_search_sec"] = 0.0
    raw["boundary_max_extra_sec"] = 0.0
    return raw


def _fit_recap_segments_to_render_budget(
    raw_segments,
    current_segments: list[dict],
    source_duration: float,
    segment_options: dict,
    merge_gap_sec: float,
) -> list[dict]:
    candidates = [current_segments]
    for options in (_compact_recap_segment_options(segment_options), _raw_recap_segment_options(segment_options)):
        candidate = _segments_for_store(
            raw_segments,
            source_duration,
            **_segment_store_options(options),
        )
        if merge_gap_sec > 0:
            candidate = _merge_close_segment_ranges(
                candidate,
                merge_gap_sec,
                max_duration_sec=float((segment_options or {}).get("max_duration_sec") or MAX_SEGMENT_DURATION_SEC),
            )
        candidates.append(candidate)

    valid_candidates = [candidate for candidate in candidates if _segments_look_like_recap(candidate, source_duration)]
    for candidate in valid_candidates:
        if _segments_total_duration(candidate) <= NARRATIVE_TARGET_RECAP_CLIP_DURATION_SEC:
            return candidate
    if not valid_candidates:
        return current_segments
    subset_candidates = [
        subset
        for candidate in valid_candidates
        for subset in _recap_segment_subsets_for_budget(candidate, source_duration, NARRATIVE_TARGET_RECAP_CLIP_DURATION_SEC)
    ]
    if subset_candidates:
        return max(subset_candidates, key=_segments_total_duration)
    for candidate in valid_candidates:
        if _segments_total_duration(candidate) <= NARRATIVE_MAX_RECAP_CLIP_DURATION_SEC:
            return candidate
    best = min(valid_candidates, key=_segments_total_duration)
    return _shrink_segments_to_total_duration(
        best,
        NARRATIVE_MAX_RECAP_CLIP_DURATION_SEC,
        min_segment_duration=NARRATIVE_MIN_FINAL_RECAP_SEGMENT_SEC,
    )


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


def _detect_audio_boundaries(path: Path, source_duration: float) -> list[float]:
    if not path or not path.exists() or source_duration <= 0:
        return []
    try:
        proc = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-i",
                str(path),
                "-vn",
                "-af",
                f"silencedetect=n={NARRATIVE_SILENCE_THRESHOLD_DB}dB:d={NARRATIVE_SILENCE_MIN_DURATION_SEC:g}",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=60 * 5,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    output = f"{proc.stderr or ''}\n{proc.stdout or ''}"
    boundaries = [0.0, float(source_duration)]
    for line in output.splitlines():
        start_match = SILENCE_START_RE.search(line)
        if start_match:
            boundaries.append(float(start_match.group("time")))
        end_match = SILENCE_END_RE.search(line)
        if end_match:
            boundaries.append(float(end_match.group("time")))
    boundaries.extend(_detect_audio_energy_boundaries(path, source_duration))
    return _unique_sorted_boundaries(boundaries, source_duration)


def _detect_audio_energy_boundaries(path: Path, source_duration: float) -> list[float]:
    if not path or not path.exists() or source_duration <= 0:
        return []
    try:
        proc = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-i",
                str(path),
                "-vn",
                "-ac",
                "1",
                "-ar",
                str(NARRATIVE_ENERGY_SAMPLE_RATE),
                "-f",
                "s16le",
                "-",
            ],
            capture_output=True,
            check=False,
            timeout=60 * 5,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0 or not proc.stdout:
        return []
    return _energy_boundaries_from_pcm(
        proc.stdout,
        sample_rate=NARRATIVE_ENERGY_SAMPLE_RATE,
        window_sec=NARRATIVE_ENERGY_WINDOW_SEC,
        source_duration=source_duration,
    )


def _energy_boundaries_from_pcm(
    pcm: bytes,
    *,
    sample_rate: int,
    window_sec: float,
    source_duration: float,
) -> list[float]:
    samples = array.array("h")
    samples.frombytes(pcm)
    if not samples:
        return []
    window_size = max(1, int(sample_rate * window_sec))
    energies: list[float] = []
    for offset in range(0, len(samples), window_size):
        chunk = samples[offset : offset + window_size]
        if not chunk:
            continue
        square_sum = sum(float(value) * float(value) for value in chunk)
        energies.append(math.sqrt(square_sum / len(chunk)))
    if len(energies) < NARRATIVE_ENERGY_LOCAL_RADIUS * 2 + 1:
        return []
    sorted_energies = sorted(energies)
    threshold_index = min(len(sorted_energies) - 1, max(0, int(len(sorted_energies) * NARRATIVE_ENERGY_PERCENTILE)))
    threshold = sorted_energies[threshold_index]
    boundaries: list[float] = []
    radius = NARRATIVE_ENERGY_LOCAL_RADIUS
    for index in range(radius, len(energies) - radius):
        value = energies[index]
        if value > threshold:
            continue
        neighborhood = energies[index - radius : index + radius + 1]
        if value > min(neighborhood):
            continue
        timestamp = (index + 0.5) * window_sec
        if 0 < timestamp < source_duration:
            boundaries.append(round(timestamp, 3))
    return boundaries


def _unique_sorted_boundaries(boundaries: list[float], source_duration: float) -> list[float]:
    unique: list[float] = []
    for value in sorted(boundaries):
        if value < 0 or (source_duration > 0 and value > source_duration):
            continue
        if unique and abs(unique[-1] - value) < 0.05:
            continue
        unique.append(round(value, 3))
    return unique


def _expand_range_to_audio_boundaries(
    start: float,
    end: float,
    boundaries: list[float],
    source_duration: float,
    *,
    search_sec: float,
    max_extra_sec: float,
    max_duration_sec: float,
) -> tuple[float, float]:
    search = max(0.0, float(search_sec or 0))
    max_extra = max(0.0, float(max_extra_sec or 0))
    if search <= 0 or max_extra <= 0:
        return start, end
    previous = _nearest_previous_boundary(boundaries, start, min(search, max_extra))
    next_ = _nearest_next_boundary(boundaries, end, min(search, max_extra))
    candidates = [(start, end)]
    if previous is not None:
        candidates.append((previous, end))
    if next_ is not None:
        candidates.append((start, next_))
    if previous is not None and next_ is not None:
        candidates.append((previous, next_))
    valid = []
    for candidate_start, candidate_end in candidates:
        candidate_start = max(0.0, min(start, candidate_start))
        if source_duration > 0:
            candidate_end = min(source_duration, max(end, candidate_end))
        else:
            candidate_end = max(end, candidate_end)
        if candidate_end <= candidate_start:
            continue
        if candidate_end - candidate_start > max_duration_sec:
            continue
        valid.append((candidate_start, candidate_end))
    adjusted = [item for item in valid if item[0] < start or item[1] > end]
    if not adjusted:
        return start, end
    return min(
        adjusted,
        key=lambda item: (
            -int(item[0] < start) - int(item[1] > end),
            (start - item[0]) + (item[1] - end),
            item[0],
            item[1],
        ),
    )


def _nearest_previous_boundary(boundaries: list[float], value: float, max_distance: float) -> float | None:
    previous = [boundary for boundary in boundaries if boundary <= value and value - boundary <= max_distance]
    return max(previous) if previous else None


def _nearest_next_boundary(boundaries: list[float], value: float, max_distance: float) -> float | None:
    next_boundaries = [boundary for boundary in boundaries if boundary >= value and boundary - value <= max_distance]
    return min(next_boundaries) if next_boundaries else None


def _narrative_retry_reason(result, source_duration: float, segment_options: dict | None) -> str:
    if not segment_options or source_duration <= 0:
        return ""
    invalid_count = _invalid_timestamp_count(result, source_duration)
    if invalid_count:
        return (
            f"{invalid_count} returned segment timestamp(s) were outside the uploaded "
            f"source duration of {source_duration:.3f} seconds"
        )
    oversized_count = _oversized_segment_count(
        result,
        float(segment_options.get("max_raw_duration_sec") or NARRATIVE_MAX_RAW_SEGMENT_DURATION_SEC),
    )
    if oversized_count:
        return (
            f"{oversized_count} returned segment(s) were too long; each narrative segment "
            f"must be at most {NARRATIVE_MAX_RAW_SEGMENT_DURATION_SEC:.0f} seconds and long "
            "dialogue scenes must be split into several ordered segments"
        )
    valid_clip_count = len(_clip_plan_specs(result.clips, source_duration, segment_options=segment_options))
    if not result.clips:
        segments = _segments_for_store(result.segments, source_duration, **_segment_store_options(segment_options))
        valid_clip_count = len(segments)
    recap_problem = _narrative_recap_problem(result, source_duration, segment_options)
    if recap_problem:
        return recap_problem
    if source_duration >= 900 and valid_clip_count < 1:
        return (
            f"only {valid_clip_count} valid story clip(s) remained after timestamp validation; "
            "a long episodic source needs at least one usable story clip"
        )
    duration_problem = _narrative_duration_problem(result, source_duration, segment_options)
    if duration_problem:
        return duration_problem
    return ""


def _narrative_quality_error(result, source_duration: float, segment_options: dict | None) -> str:
    if not segment_options or source_duration <= 0:
        return ""
    invalid_count = _invalid_timestamp_count(result, source_duration)
    if invalid_count:
        return (
            f"{invalid_count} segment timestamp(s) are outside the uploaded source "
            f"duration of {source_duration:.3f} seconds"
        )
    oversized_count = _oversized_segment_count(
        result,
        float(segment_options.get("max_raw_duration_sec") or NARRATIVE_MAX_RAW_SEGMENT_DURATION_SEC),
    )
    if oversized_count:
        return (
            f"{oversized_count} segment(s) are longer than "
            f"{NARRATIVE_MAX_RAW_SEGMENT_DURATION_SEC:.0f} seconds"
        )
    valid_clip_count = len(_clip_plan_specs(result.clips, source_duration, segment_options=segment_options))
    if source_duration >= 900 and valid_clip_count == 0:
        return "no valid self-contained story clips remained after timestamp validation"
    recap_problem = _narrative_recap_problem(result, source_duration, segment_options)
    if recap_problem:
        return recap_problem
    duration_problem = _narrative_duration_problem(result, source_duration, segment_options)
    if duration_problem and not _can_trim_narrative_specs_to_budget(result, source_duration, segment_options):
        return duration_problem
    return ""


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


def _narrative_duration_problem(result, source_duration: float, segment_options: dict | None) -> str:
    specs = _clip_plan_specs(getattr(result, "clips", []) or [], source_duration, segment_options=segment_options)
    if not specs:
        return ""
    mode = _analysis_mode(segment_options)
    long_clip_count = sum(
        1
        for spec in specs
        if _clip_total_duration(spec) > _max_clip_duration_for_spec(spec, source_duration, mode)
    )
    if long_clip_count:
        return (
            f"{long_clip_count} clip(s) are too long for vertical story editing; regular narrative clips "
            f"must be at most {NARRATIVE_MAX_CLIP_DURATION_SEC:.0f} seconds total and the episode recap "
            f"clip should stay under {NARRATIVE_TARGET_RECAP_CLIP_DURATION_SEC:.0f} seconds total"
        )
    if mode != ANALYSIS_MODE_NARRATIVE:
        return ""
    total_selected = sum(_clip_total_duration(spec) for spec in specs)
    max_total = min(
        NARRATIVE_MAX_TOTAL_SELECTED_DURATION_SEC,
        max(NARRATIVE_MAX_CLIP_DURATION_SEC, source_duration * NARRATIVE_MAX_TOTAL_SELECTED_RATIO),
    )
    if source_duration >= 900 and total_selected > max_total:
        return (
            f"selected timeline is too broad ({total_selected:.0f}s); choose only the strongest "
            f"self-contained main story clips and keep total selected duration under {max_total:.0f}s"
        )
    return ""


def _can_trim_narrative_specs_to_budget(result, source_duration: float, segment_options: dict | None) -> bool:
    specs = _clip_plan_specs(getattr(result, "clips", []) or [], source_duration, segment_options=segment_options)
    trimmed = _trim_narrative_clip_specs_to_budget(specs, source_duration, segment_options)
    if _analysis_mode(segment_options) != ANALYSIS_MODE_NARRATIVE:
        return bool(trimmed)
    return bool(trimmed and _is_episode_recap_spec(trimmed[0], source_duration))


def _trim_narrative_clip_specs_to_budget(
    specs: list[dict],
    source_duration: float,
    segment_options: dict | None,
) -> list[dict]:
    if not specs or not segment_options or source_duration < 900:
        return specs
    if _analysis_mode(segment_options) != ANALYSIS_MODE_NARRATIVE:
        kept = [
            spec
            for spec in specs
            if _clip_total_duration(spec) <= NARRATIVE_MAX_CLIP_DURATION_SEC
        ][:HIGHLIGHTS_MAX_CLIPS]
        return kept or specs[:HIGHLIGHTS_MAX_CLIPS]
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


def _narrative_recap_problem(result, source_duration: float, segment_options: dict | None) -> str:
    if _analysis_mode(segment_options) != ANALYSIS_MODE_NARRATIVE:
        return ""
    if source_duration < 900:
        return ""
    specs = _clip_plan_specs(getattr(result, "clips", []) or [], source_duration, segment_options=segment_options)
    if not specs:
        return (
            "missing required first clip: Episode Story Recap with 4 to 6 ordered segments "
            "covering the main plot essentials across the uploaded episode"
        )
    shape_problem = _recap_shape_problem(specs[0].get("segments") or [], source_duration)
    if shape_problem:
        return f"invalid Episode Story Recap: {shape_problem}"
    return ""


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
            "hook, just enough context, and a clear payoff or punchline. Do not build an episode "
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
