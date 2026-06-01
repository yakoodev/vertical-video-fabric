from __future__ import annotations

from app.ai.registry import get_video_analyzer
from app.settings import settings
from app.store import AppStore


PROMPT_VERSION = "vertical-short-v1"
BASE_ANALYSIS_PROMPT = """
Find strong vertical short-form moments in the source video.
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
        prompt: str = BASE_ANALYSIS_PROMPT,
    ) -> dict:
        selected_provider = (provider or settings.ai_video_provider or "mock").strip().lower()
        selected_model = (model or _default_model(selected_provider)).strip()
        source = self.store.get_source(source_id)
        if source["status"] == "failed":
            raise ValueError("failed source cannot be analyzed")
        analysis = self.store.create_ai_analysis(
            source_id,
            selected_provider,
            model=selected_model,
            prompt_version=PROMPT_VERSION,
            request={"prompt": prompt},
        )
        self.store.update_source(source_id, status="analyzing", error="")
        self.store.mark_ai_analysis_running(analysis["id"])
        try:
            result = get_video_analyzer(selected_provider).analyze(source, prompt, selected_model)
            segments = _segments_for_store(result.segments, float(source.get("duration_sec") or 0))
            if not segments:
                raise RuntimeError("AI analyzer returned no valid segments")
            self.store.create_ai_segments(analysis["id"], segments)
            analysis = self.store.finish_ai_analysis(
                analysis["id"],
                "succeeded",
                response=result.response,
                usage=result.usage,
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
