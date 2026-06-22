from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

from app.settings import settings
from app.subtitles.contracts import SubtitleResult, SubtitleSegment, SubtitleWord


class WhisperSubtitleProvider:
    """Local forced-alignment transcription via faster-whisper.

    Unlike an LLM guessing timestamps from audio, Whisper aligns each word to the
    waveform (cross-attention + DTW) and a built-in voice-activity filter skips
    music/non-speech, so karaoke captions stay locked to the spoken audio instead
    of drifting a second or two ahead on some clips.

    Models are heavy to load, so a single instance is cached process-wide per
    (size, device, compute) and reused across renders.
    """

    provider = "whisper"
    _models: dict[tuple[str, str, str], Any] = {}
    _lock = threading.Lock()

    def __init__(self, model_loader: Callable[[str], Any] | None = None) -> None:
        self._loader = model_loader

    def transcribe(self, audio_path: Path, profile: dict, model: str) -> SubtitleResult:
        size = str(model or "").strip() or settings.whisper_model_size
        whisper = self._get_model(size)
        language = str(profile.get("language") or "").strip() or None
        segments_iter, info = whisper.transcribe(
            str(audio_path),
            language=language,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 300},
            beam_size=5,
            # Single temperature (no fallback sampling) keeps word timestamps
            # deterministic — otherwise hard segments get retried with random
            # sampling and the same clip can time a word a second off between runs.
            temperature=0.0,
            condition_on_previous_text=False,
        )

        words: list[SubtitleWord] = []
        segments: list[SubtitleSegment] = []
        texts: list[str] = []
        for segment in segments_iter:
            text = str(getattr(segment, "text", "") or "").strip()
            if text:
                texts.append(text)
            seg_start = _num(getattr(segment, "start", None))
            seg_end = _num(getattr(segment, "end", None))
            if text and seg_start is not None and seg_end is not None and seg_end > seg_start:
                segments.append(SubtitleSegment(start=round(seg_start, 3), end=round(seg_end, 3), text=text))
            for word in getattr(segment, "words", None) or []:
                token = str(getattr(word, "word", "") or "").strip()
                start = _num(getattr(word, "start", None))
                end = _num(getattr(word, "end", None))
                if not token or start is None or end is None:
                    continue
                start = round(max(0.0, start), 3)
                end = round(max(end, start + 0.05), 3)
                words.append(SubtitleWord(word=token, start=start, end=end))

        if not words:
            raise RuntimeError("Whisper returned no word-level timestamps")

        language_name = str(getattr(info, "language", None) or profile.get("language") or "und")
        return SubtitleResult(
            text=" ".join(texts) or " ".join(word.word for word in words),
            language=language_name,
            duration=words[-1].end,
            segments=segments or [SubtitleSegment(words[0].start, words[-1].end, " ".join(w.word for w in words))],
            words=words,
            response={"provider": "whisper", "model": size},
            usage={
                "provider": "whisper",
                "model": size,
                "requestedModel": model,
                "whisperLanguageProbability": round(_num(getattr(info, "language_probability", None)) or 0.0, 3),
            },
        )

    def _get_model(self, size: str) -> Any:
        key = (size, settings.whisper_device, settings.whisper_compute_type)
        with self._lock:
            model = self._models.get(key)
            if model is None:
                model = self._load(size)
                self._models[key] = model
            return model

    def _load(self, size: str) -> Any:
        if self._loader is not None:
            return self._loader(size)
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:  # pragma: no cover - depends on optional install
            raise RuntimeError(
                "faster-whisper is not installed; install it or pick another subtitle provider"
            ) from exc
        settings.whisper_model_dir.mkdir(parents=True, exist_ok=True)
        return WhisperModel(
            size,
            device=settings.whisper_device,
            compute_type=settings.whisper_compute_type,
            download_root=str(settings.whisper_model_dir),
        )


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
