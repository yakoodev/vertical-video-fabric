from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class AnalysisSegment:
    start_sec: float
    end_sec: float
    title: str
    description: str = ""
    score: float = 0
    category: str = "general"
    color: str = "#64748B"
    reason: str = ""
    focus: tuple[dict[str, Any], ...] = ()

    def to_store_dict(self) -> dict[str, Any]:
        return {
            "start_sec": self.start_sec,
            "end_sec": self.end_sec,
            "title": self.title,
            "description": self.description,
            "score": self.score,
            "category": self.category,
            "color": self.color,
            "reason": self.reason,
            "focus": list(self.focus),
        }


@dataclass(frozen=True)
class AnalysisClip:
    title: str
    segments: list[AnalysisSegment]
    description: str = ""
    score: float = 0
    category: str = "general"
    color: str = "#64748B"


@dataclass(frozen=True)
class AnalysisResult:
    segments: list[AnalysisSegment]
    clips: list[AnalysisClip] = field(default_factory=list)
    response: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)


class VideoAnalyzer(Protocol):
    provider: str

    def analyze(self, source: dict, prompt: str, model: str) -> AnalysisResult:
        ...
