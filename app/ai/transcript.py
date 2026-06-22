"""Whisper transcript for the analysis stage.

A timestamped, verbatim speech transcript handed to the video LLM alongside the
clip. It "hears" the audio natively already, but its internal ASR is lossy
(fast/quiet/overlapping speech, names, slang) — a Whisper transcript gives it
exact words and exact timecodes, which sharpens cut boundaries on complete
spoken lines, captures quotable lines/punchlines word-for-word, and curbs
invented timestamps. Most useful on speech-heavy sources; little gain on
visual-only action.
"""

from __future__ import annotations

import logging
from pathlib import Path
from uuid import uuid4

from app.settings import settings

log = logging.getLogger(__name__)


def transcribe_source_cues(local_path: str | None, model: str | None = None) -> list[dict]:
    """Transcribe a source file into ``[{start, end, text}]`` cues on the source
    timeline. Returns ``[]`` on any failure so analysis never breaks because of it.
    """

    if not local_path:
        return []
    path = Path(local_path)
    if not path.exists():
        return []

    # Lazy imports keep this off the hot import path and avoid an import cycle
    # (render imports subtitle/AI helpers).
    from app.render import _run_ffmpeg, build_ffmpeg_extract_audio_args
    from app.subtitles.whisper_local import WhisperSubtitleProvider

    audio_path = settings.tmp_dir / f"analysis-{uuid4().hex}.wav"
    try:
        _run_ffmpeg(build_ffmpeg_extract_audio_args(path, audio_path), timeout=60 * 30)
        result = WhisperSubtitleProvider().transcribe(
            audio_path,
            {"language": ""},
            (model or settings.whisper_model_size),
        )
        cues: list[dict] = []
        for seg in result.segments:
            text = str(getattr(seg, "text", "") or "").strip()
            if not text:
                continue
            cues.append({"start": round(float(seg.start), 2), "end": round(float(seg.end), 2), "text": text})
        return cues
    except Exception:  # noqa: BLE001 - transcript is an optional enhancement
        log.warning("analysis transcript failed for %s", path.name, exc_info=True)
        return []
    finally:
        audio_path.unlink(missing_ok=True)


def format_transcript_block(cues: list[dict], start_sec: float | None, end_sec: float | None) -> str:
    """Render the cues overlapping ``[start_sec, end_sec]`` as a compact
    timestamped block for the prompt, or ``""`` when there is nothing to show."""

    if not cues:
        return ""
    lo = float(start_sec) if start_sec is not None else 0.0
    hi = float(end_sec) if end_sec is not None else float("inf")
    lines: list[str] = []
    for cue in cues:
        if cue["end"] < lo or cue["start"] > hi:
            continue
        lines.append(f"[{cue['start']:.1f}-{cue['end']:.1f}] {cue['text']}")
        if len(lines) >= 1500:  # safety cap for very long sources
            break
    if not lines:
        return ""
    return (
        "Verbatim speech transcript (Whisper), timestamps in seconds on the source timeline:\n"
        + "\n".join(lines)
    )
