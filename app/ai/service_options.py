"""Анализ видео: настройки анализа и окна (из ``app/ai/service.py``)."""

from __future__ import annotations

import math

from app.formats import settings_format
from app.settings import settings
from app.store import AppStore, MAX_SEGMENT_DURATION_SEC

# Episodic post-processing (padding, merging, boundary snapping, oversize guards)
# runs in two modes. "narrative" forces an Episode Story Recap as the first clip
# and is opted into by prompts that ask for a recap (e.g. the Series preset).
# "highlights" keeps the same phrase-safe segment hygiene but returns several
# strong standalone clips instead, which is what the Anime preset now uses.
ANALYSIS_MODE_NARRATIVE = "narrative"
ANALYSIS_MODE_HIGHLIGHTS = "highlights"
HIGHLIGHTS_MAX_CLIPS = 6


NARRATIVE_SEGMENT_PADDING_SEC = 4.0
NARRATIVE_MIN_SEGMENT_DURATION_SEC = 20.0
NARRATIVE_MERGE_ADJACENT_SEGMENT_GAP_SEC = 3.0
NARRATIVE_MAX_RAW_SEGMENT_DURATION_SEC = 75.0
NARRATIVE_MAX_SEGMENT_DURATION_SEC = 85.0


def _default_model(provider: str) -> str:
    if provider == "action":
        return "motion+audio"
    if provider == "polza":
        return settings.polza_video_model
    if provider == "gemini":
        return settings.gemini_video_model
    if provider == "artemox":
        return settings.artemox_video_model
    return "mock-video-analyzer"


def _safe_error(exc: Exception) -> str:
    return str(exc).strip()[:500] or "analysis failed"


def _analysis_output_format(store: AppStore, requested: str | None) -> str:
    """Формат клипов анализа: из запроса, иначе настройка ``analysis_format``, иначе 9:16.
    Явно заданный мусор — ValueError (понятная ошибка запроса), сохранённый — 9:16."""
    from app.formats import normalize_format

    if requested not in (None, ""):
        return normalize_format(requested)
    return settings_format(_app_setting(store, "analysis_format"))


def _app_setting(store: AppStore, key: str) -> str:
    getter = getattr(store, "get_app_setting_value", None)
    return str((getter(key, "") if getter else "") or "")


def _landscape_max_clip_sec(store: AppStore) -> float:
    """Потолок длины клипа 16:9: настройка ``landscape_max_clip_sec`` или LANDSCAPE_MAX_CLIP_SECONDS."""
    try:
        value = float(_app_setting(store, "landscape_max_clip_sec") or 0)
    except (TypeError, ValueError):
        value = 0.0
    return value if value > 0 else float(settings.landscape_max_clip_seconds)


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


def _highlights_clip_cap(source_duration: float) -> int:
    """How many highlight clips to keep. A short source keeps the base cap; a long
    windowed episode keeps more so coverage isn't truncated to the first window."""

    window = max(120.0, float(settings.analysis_window_seconds))
    by_length = math.ceil(max(0.0, source_duration) / window) * 4
    return max(HIGHLIGHTS_MAX_CLIPS, by_length)


def _analysis_windows(duration_sec: float, mode: str, provider: str) -> list[tuple[float, float]] | None:
    """Split a long source into overlapping time windows for gemini.

    A long video sent in one request blows Gemini's per-request token budget
    (~1M tokens ≈ a few tens of minutes of video), so any source longer than the
    single-pass threshold MUST be windowed — regardless of analysis mode. Each
    window is analyzed with a video_metadata start/end offset (and a transcript
    slice), keeping every request small; absolute timestamps merge cleanly.
    Short sources return None and are analyzed in one pass.
    """

    if provider != "gemini":
        return None
    _ = mode  # windowing is now mode-independent; kept for signature compatibility
    window = max(120.0, float(settings.analysis_window_seconds))
    overlap = max(0.0, float(settings.analysis_window_overlap_seconds))
    if duration_sec <= window * 1.4:
        return None
    count = max(2, math.ceil(duration_sec / window))
    step = duration_sec / count
    windows: list[tuple[float, float]] = []
    for index in range(count):
        start = max(0.0, index * step - (overlap if index else 0.0))
        end = duration_sec if index == count - 1 else (index + 1) * step
        windows.append((round(start, 3), round(end, 3)))
    return windows


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
