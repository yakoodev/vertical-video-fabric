from __future__ import annotations

from pathlib import Path

from app.ingest import probe_media
from app.subtitles.contracts import SubtitleResult, SubtitleSegment, SubtitleWord


class MockSubtitleProvider:
    provider = "mock"

    def transcribe(self, audio_path: Path, profile: dict, model: str) -> SubtitleResult:
        try:
            duration = probe_media(audio_path).duration_sec
        except ValueError:
            duration = 4
        duration = max(2, duration or 4)
        words_text = ["mock", "karaoke", "subtitle", "test"]
        step = min(0.8, duration / max(len(words_text), 1))
        words = [
            SubtitleWord(word=word, start=round(index * step, 3), end=round((index + 1) * step, 3))
            for index, word in enumerate(words_text)
        ]
        text = " ".join(words_text)
        return SubtitleResult(
            text=text,
            language=profile.get("language") or "und",
            duration=duration,
            segments=[SubtitleSegment(start=0, end=min(duration, words[-1].end), text=text)],
            words=words,
            response={"mock": True, "model": model},
            usage={"mock": True},
        )
