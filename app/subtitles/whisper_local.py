from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

from app.settings import settings
from app.subtitles.contracts import SubtitleResult, SubtitleSegment, SubtitleWord

# Whisper invents speech on instrumental music / room tone. These guards drop it:
# the decoder's own "this is not speech" probability, and a floor on confidence.
_NO_SPEECH_THRESHOLD = 0.6
_MIN_AVG_LOGPROB = -1.0
_MUSIC_LOGPROB = -0.5  # «песня» под музыкой обычно декодируется неуверенно
# Известные «субтитры из ниоткуда» Whisper на тишине и музыке (из обучающих титров).
_KNOWN_PHANTOMS = (
    "субтитры сделал", "субтитры создавал", "редактор субтитров", "корректор",
    "продолжение следует", "спасибо за просмотр", "подписывайтесь на канал",
    "amara.org", "dimatorzok",
)


def _is_hallucination(segment: Any) -> bool:
    """True when a decoded segment looks like non-speech (music, silence) rather
    than real dialogue — the usual source of phantom subtitles."""
    text = str(getattr(segment, "text", "") or "").lower()
    if any(phrase in text for phrase in _KNOWN_PHANTOMS):
        return True
    no_speech = _num(getattr(segment, "no_speech_prob", None))
    avg_logprob = _num(getattr(segment, "avg_logprob", None))
    if avg_logprob is not None and avg_logprob < _MIN_AVG_LOGPROB:
        return True
    if no_speech is not None and no_speech > _NO_SPEECH_THRESHOLD:
        # Как у самого Whisper: «не речь» — когда высокая вероятность тишины И декодер
        # не уверен. Под дубляжом с музыкой no_speech ≈ 0.7–0.93 даже на чистых
        # фразах (уверенность −0.16): по одному no_speech выпадало 85% реплик серии
        # и целые сцены по минуте.
        # Порог «почти наверняка тишина» (0.9) не годится: реальные сцены под
        # музыкой шли с 0.92 и уверенностью −0.33 — и выпадали по 2 минуты.
        if avg_logprob is None or avg_logprob < _MUSIC_LOGPROB:
            return True
    return False


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
            # Voice-activity filter with a bit of padding: instrumental music and
            # room tone never reach the decoder, which is where hallucinated
            # "lyrics" come from.
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500, "speech_pad_ms": 200},
            beam_size=5,
            # Single temperature (no fallback sampling) keeps word timestamps
            # deterministic — otherwise hard segments get retried with random
            # sampling and the same clip can time a word a second off between runs.
            temperature=0.0,
            condition_on_previous_text=False,
            # Anti-hallucination guards: drop windows the model itself thinks are
            # non-speech, low-confidence output, and repetitive gibberish
            # ("ла-ла-ла", subtitle-credits spam) that music tends to produce.
            no_speech_threshold=_NO_SPEECH_THRESHOLD,
            log_prob_threshold=-1.0,
            compression_ratio_threshold=2.4,
            hallucination_silence_threshold=2.0,
        )

        words: list[SubtitleWord] = []
        segments: list[SubtitleSegment] = []
        texts: list[str] = []
        decoded_any = False
        dropped = 0
        for segment in segments_iter:
            decoded_any = True
            # Belt-and-braces on top of the decoder thresholds: never emit words
            # for a window that scores as non-speech / low confidence.
            if _is_hallucination(segment):
                dropped += 1
                continue
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

        language_name = str(getattr(info, "language", None) or profile.get("language") or "und")

        if not words:
            # Everything the decoder produced scored as non-speech (instrumental
            # music, room tone) — that is a valid "no speech here" answer, so return
            # an empty track instead of failing the render with phantom lyrics.
            if decoded_any and dropped:
                return SubtitleResult(
                    text="",
                    language=language_name,
                    duration=0.0,
                    segments=[],
                    words=[],
                    response={"provider": "whisper", "model": size, "droppedNonSpeech": dropped},
                    usage={"provider": "whisper", "model": size, "requestedModel": model, "droppedNonSpeech": dropped},
                )
            # Nothing came back at all → genuine failure worth surfacing.
            raise RuntimeError("Whisper returned no word-level timestamps")
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

    # Модель (large — полтора гигабайта) выгружается после простоя: память машины
    # небольшая, а распознают клипы редко и пачками.
    IDLE_UNLOAD_SEC = 600.0
    _unload_timer: threading.Timer | None = None

    @classmethod
    def _schedule_unload(cls) -> None:
        with cls._lock:
            if cls._unload_timer is not None:
                cls._unload_timer.cancel()
            timer = threading.Timer(cls.IDLE_UNLOAD_SEC, cls._unload)
            timer.daemon = True
            cls._unload_timer = timer
            timer.start()

    @classmethod
    def _unload(cls) -> None:
        with cls._lock:
            cls._models.clear()
            cls._unload_timer = None
        import gc

        gc.collect()

    def _get_model(self, size: str) -> Any:
        key = (size, settings.whisper_device, settings.whisper_compute_type)
        self._schedule_unload()
        with self._lock:
            model = self._models.get(key)
            if model is None:
                # Держим в памяти одну модель: память машины небольшая, а large
                # рядом со small — лишние полтора гигабайта.
                self._models.clear()
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
