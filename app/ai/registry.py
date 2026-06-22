from __future__ import annotations

from app.ai.action_detect import ActionVideoAnalyzer
from app.ai.artemox import ArtemoxVideoAnalyzer
from app.ai.contracts import VideoAnalyzer
from app.ai.gemini import GeminiVideoAnalyzer
from app.ai.mock import MockVideoAnalyzer
from app.ai.polza import PolzaVideoAnalyzer


class NotConfiguredVideoAnalyzer:
    def __init__(self, provider: str) -> None:
        self.provider = provider

    def analyze(self, source: dict, prompt: str, model: str):
        raise RuntimeError(f"{self.provider} video analyzer is not implemented yet")


def get_video_analyzer(provider: str) -> VideoAnalyzer:
    normalized = provider.strip().lower()
    if normalized == "mock":
        return MockVideoAnalyzer()
    if normalized == "artemox":
        return ArtemoxVideoAnalyzer()
    if normalized == "gemini":
        return GeminiVideoAnalyzer()
    if normalized == "action":
        return ActionVideoAnalyzer()
    if normalized == "polza":
        return PolzaVideoAnalyzer()
    raise ValueError(f"unsupported video analyzer: {provider}")
