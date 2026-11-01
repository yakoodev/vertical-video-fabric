from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class SubtitleWord:
    word: str
    start: float
    end: float
    # Эмфаза слова (app/word_emphasis.py): 0 — нет, 1 — pop, 2 — pop со встряской.
    emph: int = 0

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"word": self.word, "start": self.start, "end": self.end}
        if self.emph:
            out["emph"] = self.emph
        return out


@dataclass(frozen=True)
class SubtitleSegment:
    start: float
    end: float
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {"start": self.start, "end": self.end, "text": self.text}


@dataclass(frozen=True)
class SubtitleResult:
    text: str
    language: str
    duration: float
    segments: list[SubtitleSegment] = field(default_factory=list)
    words: list[SubtitleWord] = field(default_factory=list)
    response: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)

    def transcript_json(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "language": self.language,
            "duration": self.duration,
            "segments": [segment.to_dict() for segment in self.segments],
            "words": [word.to_dict() for word in self.words],
        }


class SubtitleProvider(Protocol):
    provider: str

    def transcribe(self, audio_path: Path, profile: dict, model: str) -> SubtitleResult:
        ...
