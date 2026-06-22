from __future__ import annotations

from app.settings import settings
from app.subtitles.contracts import SubtitleProvider
from app.subtitles.gemini import GeminiSubtitleProvider
from app.subtitles.mock import MockSubtitleProvider
from app.subtitles.whisper_local import WhisperSubtitleProvider


WHISPER_MODEL_SIZES = {
    "tiny", "tiny.en", "base", "base.en", "small", "small.en",
    "medium", "medium.en", "large-v1", "large-v2", "large-v3", "large",
    "distil-small.en", "distil-medium.en", "distil-large-v2", "distil-large-v3",
}


def get_subtitle_provider(provider: str | None = None) -> SubtitleProvider:
    name = (provider or settings.subtitle_provider or "mock").strip().lower()
    if name == "mock":
        return MockSubtitleProvider()
    if name == "whisper":
        return WhisperSubtitleProvider()
    if name == "gemini":
        return GeminiSubtitleProvider()
    if name == "polza":
        raise RuntimeError("Polza subtitle provider is not implemented yet")
    if name == "artemox":
        raise RuntimeError("Artemox subtitle provider cannot upload large local audio files directly")
    raise ValueError(f"unsupported subtitle provider: {name}")


def subtitle_model_for_profile(profile: dict) -> str:
    provider = str(profile.get("provider") or settings.subtitle_provider or "mock").strip().lower()
    model = str(profile.get("model") or "").strip()
    if provider == "whisper":
        # A profile may still carry a leftover LLM model name (e.g. a Gemini id);
        # only accept a real Whisper size, otherwise fall back to the default.
        return model if model in WHISPER_MODEL_SIZES else settings.whisper_model_size
    if provider == "gemini" and (not model or model.startswith("openai/")):
        return settings.gemini_transcribe_model
    if provider == "polza" and not model:
        return settings.polza_transcribe_model
    if provider == "artemox" and not model:
        return settings.artemox_transcribe_model
    return model or "mock-subtitle"
