from __future__ import annotations

from app.subtitles.contracts import SubtitleResult, SubtitleSegment, SubtitleWord


MIN_WORD_DURATION_SEC = 0.05
# If words only spill slightly past the audio we trim/clamp them instead of
# warping the whole timeline. We only fall back to a uniform linear compression
# when the model clearly used the wrong timeline (e.g. full-episode timestamps
# for a short clip), where every value is grossly out of range.
TIMELINE_CLAMP_TOLERANCE_SEC = 0.25
GROSS_OVERFLOW_RATIO = 1.5
GROSS_OVERFLOW_MARGIN_SEC = 1.0


def normalize_subtitle_timeline(
    result: SubtitleResult,
    target_duration: float,
) -> SubtitleResult:
    """Keep word timings honest relative to the clip audio.

    The previous implementation re-mapped time non-linearly by shrinking inter
    word gaps, which made the karaoke highlight drift away from the spoken audio.
    Word timestamps coming back from the transcription provider are already in
    clip time, so the only corrections we need are:

    * make the sequence strictly monotonic,
    * clamp/trim anything that runs a hair past the real audio length,
    * and, only for grossly out-of-range timestamps, apply a single uniform
      linear scale so the relative spacing between words is preserved.
    """

    target = max(0.0, float(target_duration or 0))
    words = _monotonic_words(result.words)
    if not words or target <= 0:
        return result

    max_word_end = max(word.end for word in words)
    scale = 1.0
    if max_word_end > target * GROSS_OVERFLOW_RATIO + GROSS_OVERFLOW_MARGIN_SEC:
        scale = target / max_word_end

    scaled_words = [
        SubtitleWord(
            word=word.word,
            start=word.start * scale,
            end=max(word.start * scale + MIN_WORD_DURATION_SEC, word.end * scale),
        )
        for word in words
    ]
    scaled_words = _monotonic_words(scaled_words)

    clamped_words = [
        SubtitleWord(
            word=word.word,
            start=round(word.start, 3),
            end=round(min(word.end, target), 3),
        )
        for word in scaled_words
        if word.start < target
    ]
    if not clamped_words:
        return result

    segments = _normalize_segments(result.segments, clamped_words, target, scale)
    return SubtitleResult(
        text=result.text,
        language=result.language,
        duration=target,
        segments=segments,
        words=clamped_words,
        response=result.response,
        usage={**result.usage, "subtitleTimelineNormalizedToSec": target},
    )


def shift_subtitle_timeline(
    result: SubtitleResult,
    offset_sec: float,
    target_duration: float,
) -> SubtitleResult:
    offset = float(offset_sec or 0)
    target = max(0.0, float(target_duration or result.duration or 0))
    if abs(offset) < 0.001 or target <= 0:
        return result

    words: list[SubtitleWord] = []
    for word in result.words:
        shifted = _shift_interval(float(word.start), float(word.end), offset, target, min_duration=MIN_WORD_DURATION_SEC)
        if shifted is None:
            continue
        start, end = shifted
        words.append(SubtitleWord(word.word, start, end))
    words = _monotonic_words(words)

    segments: list[SubtitleSegment] = []
    for segment in result.segments:
        shifted = _shift_interval(float(segment.start), float(segment.end), offset, target)
        if shifted is None:
            continue
        start, end = shifted
        segments.append(SubtitleSegment(start=start, end=end, text=segment.text))

    if not segments and words:
        segments = [SubtitleSegment(start=words[0].start, end=words[-1].end, text=" ".join(word.word for word in words))]

    return SubtitleResult(
        text=result.text,
        language=result.language,
        duration=target,
        segments=segments,
        words=words,
        response=result.response,
        usage={
            **result.usage,
            "subtitleTimingOffsetSec": round(offset, 3),
            "subtitleTimingTargetSec": round(target, 3),
        },
    )


def _shift_interval(
    start: float,
    end: float,
    offset: float,
    target: float,
    *,
    min_duration: float = 0.0,
) -> tuple[float, float] | None:
    shifted_start = start + offset
    shifted_end = end + offset
    if shifted_end <= 0 or shifted_start >= target:
        return None
    clamped_start = max(0.0, shifted_start)
    clamped_end = min(target, max(clamped_start + min_duration, shifted_end))
    if clamped_end <= clamped_start:
        return None
    return round(clamped_start, 3), round(clamped_end, 3)


def _monotonic_words(words: list[SubtitleWord]) -> list[SubtitleWord]:
    normalized: list[SubtitleWord] = []
    previous_end = 0.0
    for word in sorted(words, key=lambda item: (float(item.start), float(item.end))):
        text = str(word.word).strip()
        if not text:
            continue
        start = max(0.0, float(word.start))
        end = max(start + MIN_WORD_DURATION_SEC, float(word.end))
        if start < previous_end:
            start = previous_end
            end = max(end, start + MIN_WORD_DURATION_SEC)
        normalized.append(SubtitleWord(text, start, end))
        previous_end = end
    return normalized


def _normalize_segments(
    segments: list[SubtitleSegment],
    words: list[SubtitleWord],
    target: float,
    scale: float,
) -> list[SubtitleSegment]:
    if not words:
        return []
    normalized: list[SubtitleSegment] = []
    for segment in segments:
        start = max(0.0, min(target, float(segment.start) * scale))
        end = max(0.0, min(target, float(segment.end) * scale))
        if end < start:
            start, end = end, start
        if end <= start:
            continue
        normalized.append(SubtitleSegment(start=round(start, 3), end=round(end, 3), text=segment.text))
    if normalized:
        return normalized
    return [SubtitleSegment(start=words[0].start, end=words[-1].end, text=" ".join(word.word for word in words))]
