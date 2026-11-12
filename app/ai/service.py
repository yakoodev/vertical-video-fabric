from __future__ import annotations

from pathlib import Path

from app.analysis_preprocess import (
    fit_source_under_upload_limit,
    normalize_analysis_preprocessing,
    prepare_source_for_analysis,
)
from app.ai.contracts import AnalysisCancelled, AnalysisClip
from app.ai.gemini import GeminiVideoAnalyzer
from app.ai.registry import get_video_analyzer
from app.ai.transcript import transcribe_source_cues
from app.default_prompts import BASE_ANALYSIS_PROMPT, focus_track_instruction, output_format_instruction
from app.formats import DEFAULT_FORMAT, focus_needed
from app.settings import settings
from app.store import AppStore, MAX_SEGMENT_DURATION_SEC

from app.ai.service_options import (  # noqa: F401
    ANALYSIS_MODE_NARRATIVE, ANALYSIS_MODE_HIGHLIGHTS, HIGHLIGHTS_MAX_CLIPS, NARRATIVE_SEGMENT_PADDING_SEC,
    NARRATIVE_MIN_SEGMENT_DURATION_SEC, NARRATIVE_MERGE_ADJACENT_SEGMENT_GAP_SEC,
    NARRATIVE_MAX_RAW_SEGMENT_DURATION_SEC, NARRATIVE_MAX_SEGMENT_DURATION_SEC, _default_model, _safe_error,
    _analysis_output_format, _app_setting, _landscape_max_clip_sec, _prompt_from_preset,
    _source_specific_analysis_prompt, _source_looks_like_anime, _segment_postprocess_options, _analysis_mode,
    _highlights_clip_cap, _analysis_windows, _segment_store_options, _compact_recap_segment_options,
    _raw_recap_segment_options,
)
from app.ai.service_audio import (  # noqa: F401
    NARRATIVE_SILENCE_THRESHOLD_DB, NARRATIVE_SILENCE_MIN_DURATION_SEC, NARRATIVE_ENERGY_SAMPLE_RATE,
    NARRATIVE_ENERGY_WINDOW_SEC, NARRATIVE_ENERGY_LOCAL_RADIUS, NARRATIVE_ENERGY_PERCENTILE, SILENCE_START_RE,
    SILENCE_END_RE, _detect_audio_boundaries, _detect_audio_energy_boundaries, _energy_boundaries_from_pcm,
    _unique_sorted_boundaries, _expand_range_to_audio_boundaries, _nearest_previous_boundary,
    _nearest_next_boundary,
)
from app.ai.service_segments import (  # noqa: F401
    COALESCE_SINGLE_SEGMENT_CLIP_GAP_SEC, COALESCE_SINGLE_SEGMENT_CLIP_MAX_DURATION_SEC,
    NARRATIVE_MAX_CLIP_DURATION_SEC, NARRATIVE_TARGET_RECAP_CLIP_DURATION_SEC, NARRATIVE_RECAP_MIN_SEGMENTS,
    NARRATIVE_RECAP_MIN_SPAN_RATIO, NARRATIVE_RECAP_MIN_SPAN_SEC, _recap_segment_subsets_for_budget,
    _shrink_segments_to_total_duration, _expand_range_to_duration, _is_episode_recap_spec, _is_episode_recap_shape,
    _segments_look_like_recap, _recap_shape_problem, _max_clip_duration_for_spec, _clip_total_duration,
    _segments_total_duration, _merge_close_segment_ranges, _join_short_text, _coalesce_single_segment_clip_specs,
    _can_join_single_segment_spec, _merge_single_segment_clip_specs, _combined_clip_title,
    _combined_clip_description, _segment_duration,
)
from app.ai.service_narrative import (  # noqa: F401
    NARRATIVE_MAX_TOTAL_SELECTED_DURATION_SEC, NARRATIVE_MAX_TOTAL_SELECTED_RATIO, _invalid_timestamp_count,
    _oversized_segment_count, _raw_result_segments, _trim_narrative_clip_specs_to_budget, _narrative_retry_prompt,
)

PROMPT_VERSION = "vertical-short-v1"


NARRATIVE_MAX_RECAP_CLIP_DURATION_SEC = 210.0


NARRATIVE_MIN_FINAL_RECAP_SEGMENT_SEC = 10.0


NARRATIVE_AUDIO_BOUNDARY_SEARCH_SEC = 8.0
NARRATIVE_AUDIO_BOUNDARY_MAX_EXTRA_SEC = 8.0


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
        use_transcript: bool | None = None,
        output_format: str | None = None,
    ) -> dict:
        """Анализ источника; траты Gemini за него — purpose ``analysis``, сессия ``analysis:<id>``."""
        from app import gemini_usage

        with gemini_usage.scope("analysis", source_id=source_id):
            return self._run_analysis(source_id, provider, model, prompt, preprocessing, use_transcript, output_format)

    def _run_analysis(
        self,
        source_id: int,
        provider: str | None = None,
        model: str | None = None,
        prompt: str | None = None,
        preprocessing: dict | None = None,
        use_transcript: bool | None = None,
        output_format: str | None = None,
    ) -> dict:
        """``output_format`` — формат будущих клипов (app/formats.py): не задан — настройка
        приложения ``analysis_format``, нет и её — 9:16 (прежний запрос модели, слово в слово).
        Другой формат дописывает модели блок OUTPUT FORMAT (не шортс у 16:9, длина до
        LANDSCAPE_MAX_CLIP_SECONDS), а фокус-трек просит под окно своей пропорции или не
        просит вовсе (16:9 из горизонтального исходника — кадр не режется)."""
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
        # Vision providers are additionally asked for a point-of-interest track so
        # wide sources can be dynamically reframed to 9:16. This augmentation goes
        # only to the model — the stored request keeps the user's clean prompt.
        analyzer_prompt = selected_prompt
        clip_format = _analysis_output_format(self.store, output_format)
        format_block = output_format_instruction(clip_format, _landscape_max_clip_sec(self.store))
        if format_block:
            analyzer_prompt = f"{analyzer_prompt}\n\n{format_block}".strip()
        if selected_provider in {"gemini", "polza", "artemox"} and focus_needed(clip_format, source):
            analyzer_prompt = f"{analyzer_prompt}\n\n{focus_track_instruction(clip_format)}".strip()
        # 💬 Импортированный чат Twitch: всплески реакций зрителей — подсказка, где
        # искать моменты. Тоже только модели; выключается настройкой analysis_use_chat.
        from app.twitch_chat import chat_prompt_block

        chat_block = chat_prompt_block(self.store, source_id)
        if chat_block:
            analyzer_prompt = f"{analyzer_prompt}\n\n{chat_block}".strip()
        # YouTube «самые пересматриваемые» места — такая же подсказка модели.
        from app.youtube_heatmap import heatmap_prompt_block

        heat_block = heatmap_prompt_block(self.store, source_id)
        if heat_block:
            analyzer_prompt = f"{analyzer_prompt}\n\n{heat_block}".strip()
        # Что агент знает о проекте этого видео (app/project_knowledge.py). Тоже только модели.
        from app.project_knowledge import block_for_source

        lore_block = block_for_source(self.store, source_id)
        if lore_block:
            analyzer_prompt = f"{analyzer_prompt}\n\n{lore_block}".strip()
        if source["status"] == "failed":
            raise ValueError("failed source cannot be analyzed")
        normalized_preprocessing = normalize_analysis_preprocessing(preprocessing)
        segment_options = _segment_postprocess_options(source, selected_prompt, selected_provider)
        analysis = self.store.create_ai_analysis(
            source_id,
            selected_provider,
            model=selected_model,
            prompt_version=PROMPT_VERSION,
            request={"prompt": selected_prompt, "preprocessing": normalized_preprocessing,
                     **({"format": clip_format} if clip_format != DEFAULT_FORMAT else {})},
        )
        self.store.update_source(source_id, status="analyzing", error="")
        self.store.mark_ai_analysis_running(analysis["id"])
        analysis_id = analysis["id"]
        from app import gemini_usage

        gemini_usage.annotate(session=f"analysis:{analysis_id}")

        def should_cancel() -> bool:
            return self.store.ai_analysis_cancel_requested(analysis_id)

        def heartbeat() -> None:
            self.store.touch_ai_analysis(analysis_id)

        def progress(stage: str, done: int = 0, total: int = 0) -> None:
            self.store.set_ai_analysis_progress(analysis_id, stage, done, total)

        def check_cancel() -> None:
            heartbeat()
            if should_cancel():
                raise AnalysisCancelled()

        result = None
        retry_meta = None
        preprocessing_meta: dict = {}
        try:
            check_cancel()
            progress("prepare")
            analyzer_source, preprocessing_meta = prepare_source_for_analysis(source, normalized_preprocessing)
            # Gemini rejects uploads over 2 GiB. Shrink an oversized source to a
            # downscaled analysis copy that fits, so long VODs analyze instead of
            # failing at the upload step.
            if selected_provider == "gemini":
                check_cancel()
                analyzer_source, sizefit_meta = fit_source_under_upload_limit(
                    analyzer_source, float(source.get("duration_sec") or 0)
                )
                if sizefit_meta.get("fit"):
                    preprocessing_meta = {**(preprocessing_meta or {}), "enabled": True, "gemini_sizefit": sizefit_meta}
            analysis_segment_options = _segment_options_with_audio_boundaries(
                segment_options,
                analyzer_source,
                float(source.get("duration_sec") or 0),
            )
            analyzer = get_video_analyzer(selected_provider, self.store)
            windows = _analysis_windows(
                float(source.get("duration_sec") or 0),
                _analysis_mode(analysis_segment_options),
                selected_provider,
            )
            # Hand the video LLM a verbatim Whisper transcript so cut boundaries
            # land on whole spoken lines and quotes survive. Only Gemini ingests
            # it (polza already does its own ASR); default on for Gemini, but the
            # caller can force it off for visual-only sources to save the pass.
            is_gemini = isinstance(analyzer, GeminiVideoAnalyzer)
            want_transcript = use_transcript if use_transcript is not None else is_gemini
            transcript_cues: list[dict] = []
            if want_transcript and is_gemini:
                # Reuse a cached transcript for this source; only run Whisper (the
                # multi-minute step) the first time.
                transcript_cues = self.store.get_source_transcript(source_id)
                if not transcript_cues:
                    progress("transcript")
                    from app.source_audio import speech_mix

                    # Выбраны дорожки (игра + микрофон или своя дорожка речи) — слушаем
                    # оригинал: в сжатой для анализа копии дорожки уже сведены по-своему.
                    tracks = speech_mix(source)
                    transcript_cues = transcribe_source_cues(
                        (source if tracks else analyzer_source).get("local_path"), tracks=tracks)
                    if transcript_cues:
                        self.store.set_source_transcript(source_id, transcript_cues, settings.whisper_model_size)
            check_cancel()
            gemini_kwargs = (
                {
                    "transcript": transcript_cues,
                    "should_cancel": should_cancel,
                    "heartbeat": heartbeat,
                    "progress": progress,
                }
                if is_gemini
                else {}
            )
            if not is_gemini:
                progress("windows", 0, 1)
            if windows and is_gemini:
                result = analyzer.analyze(analyzer_source, analyzer_prompt, selected_model, windows=windows, **gemini_kwargs)
            else:
                result = analyzer.analyze(analyzer_source, analyzer_prompt, selected_model, **gemini_kwargs)
            check_cancel()
            # The recap-quality retry (and its single-shot re-analysis) only make
            # sense for narrative/recap mode. Running it in highlights mode would
            # throw away the full-episode windowed coverage by re-analyzing the
            # whole file in one pass; oversized highlights segments are trimmed in
            # post-processing instead.
            if _analysis_mode(analysis_segment_options) == ANALYSIS_MODE_NARRATIVE:
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
                        mode=ANALYSIS_MODE_NARRATIVE,
                    )
                    if format_block:
                        retry_prompt = f"{retry_prompt}\n\n{format_block}"
                    if lore_block:
                        retry_prompt = f"{retry_prompt}\n\n{lore_block}"
                    result = analyzer.analyze(analyzer_source, retry_prompt, selected_model, **gemini_kwargs)
                    retry_meta = {"reason": retry_reason}
                quality_error = _narrative_quality_error(
                    result,
                    float(source.get("duration_sec") or 0),
                    analysis_segment_options,
                )
                if quality_error:
                    raise RuntimeError(f"AI analyzer returned invalid narrative analysis: {quality_error}")
            progress("collect")
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
            # Candidates from earlier analyses are kept on purpose: a second run with
            # another preset/model is a source of MORE options, not a replacement.
            # Drop an analysis (which cascades to its plans) to clean up.
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
            # Snap the fresh candidates' boundaries to speech per the source's cut
            # hypothesis (default phrase-safe). Best-effort — never fail analysis on it.
            try:
                self.store.refine_source_cuts(source_id, source.get("cut_strategy") or None)
            except Exception:  # noqa: BLE001
                pass
            self.store.update_source(source_id, status="analyzed", error="")
            return analysis
        except AnalysisCancelled:
            # Cooperative cancel: drop any partial output and mark the run cancelled
            # (not failed) so the UI shows it was stopped on purpose, not an error.
            try:
                self.store.delete_generated_outputs_for_analysis(analysis["id"])
            except Exception:  # noqa: BLE001 - best effort cleanup
                pass
            analysis = self.store.finish_ai_analysis(
                analysis["id"],
                "cancelled",
                response={"cancelled": True},
                usage=dict(getattr(result, "usage", {}) or {}),
                error="Анализ отменён пользователем",
            )
            self.store.update_source(source_id, status="ready", error="")
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
