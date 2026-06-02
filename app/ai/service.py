from __future__ import annotations

from app.analysis_preprocess import normalize_analysis_preprocessing, prepare_source_for_analysis
from app.ai.registry import get_video_analyzer
from app.settings import settings
from app.store import AppStore


PROMPT_VERSION = "vertical-short-v1"
BASE_ANALYSIS_PROMPT = """
Find strong vertical short-form moments in the source video.
The default content context is Apex Legends gameplay: expect squad voice chat,
legend names, weapons, damage numbers, knocks, revives, rotations, third parties,
ranked callouts, and fast fight pacing.
Return clips that work standalone, have a clear hook, and contain conflict,
emotion, humor, insight, tension, or spectacle. Prefer moments between 5 and
180 seconds and include a practical title and description for publishing.
When the source has enough material, return 3 to 5 distinct moments so they can
be stitched into a montage.
""".strip()


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
        selected_prompt = (
            prompt
            or _prompt_from_preset(self.store.get_default_prompt_preset("analysis"))
            or self.store.get_app_setting_value("analysis_prompt", BASE_ANALYSIS_PROMPT)
            or BASE_ANALYSIS_PROMPT
        ).strip()
        source = self.store.get_source(source_id)
        if source["status"] == "failed":
            raise ValueError("failed source cannot be analyzed")
        normalized_preprocessing = normalize_analysis_preprocessing(preprocessing)
        analysis = self.store.create_ai_analysis(
            source_id,
            selected_provider,
            model=selected_model,
            prompt_version=PROMPT_VERSION,
            request={"prompt": selected_prompt, "preprocessing": normalized_preprocessing},
        )
        self.store.update_source(source_id, status="analyzing", error="")
        self.store.mark_ai_analysis_running(analysis["id"])
        try:
            analyzer_source, preprocessing_meta = prepare_source_for_analysis(source, normalized_preprocessing)
            result = get_video_analyzer(selected_provider).analyze(analyzer_source, selected_prompt, selected_model)
            created_plans = _persist_clip_plans(
                self.store,
                source_id=source_id,
                analysis_id=analysis["id"],
                result=result,
                source_duration=float(source.get("duration_sec") or 0),
            )
            if not created_plans:
                raise RuntimeError("AI analyzer returned no valid segments")
            usage = dict(result.usage)
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
            analysis = self.store.finish_ai_analysis(
                analysis["id"],
                "failed",
                response={"error": str(exc)},
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


def _segments_for_store(segments, source_duration: float) -> list[dict]:
    normalized: list[dict] = []
    for segment in segments:
        data = segment.to_store_dict()
        start = max(0.0, float(data.get("start_sec") or 0))
        end = float(data.get("end_sec") or 0)
        if source_duration > 0:
            if start >= source_duration:
                continue
            end = min(end, source_duration)
        if end <= start:
            continue
        if end - start < 5:
            if source_duration > 0 and source_duration - start >= 5:
                end = min(source_duration, start + 5)
            else:
                start = max(0.0, end - 5)
        if end - start > 180:
            end = start + 180
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
) -> list[dict]:
    created_plans: list[dict] = []
    if getattr(result, "clips", None):
        for index, clip in enumerate(result.clips):
            segments = _segments_for_store(clip.segments, source_duration)
            if not segments:
                continue
            created_segments = store.create_ai_segments(analysis_id, segments)
            created_plans.append(
                store.create_clip_plan(
                    source_id,
                    analysis_id,
                    clip.title,
                    description=clip.description,
                    segment_ids=[segment["id"] for segment in created_segments],
                    score=clip.score,
                    category=clip.category,
                    color=clip.color,
                    sort_order=index,
                )
            )
        return created_plans

    segments = _segments_for_store(result.segments, source_duration)
    if not segments:
        return []
    created_segments = store.create_ai_segments(analysis_id, segments)
    for index, segment in enumerate(created_segments):
        created_plans.append(
            store.create_clip_plan(
                source_id,
                analysis_id,
                segment["title"],
                description=segment["description"],
                segment_ids=[segment["id"]],
                score=segment["score"],
                category=segment["category"],
                color=segment["color"],
                sort_order=index,
            )
        )
    return created_plans
