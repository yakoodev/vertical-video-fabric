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


def transcribe_source_cues(local_path: str | None, model: str | None = None,
                           tracks: list[int] | None = None, strict: bool = False) -> list[dict]:
    """Transcribe a source file into ``[{start, end, text}]`` cues on the source
    timeline. Returns ``[]`` on any failure so analysis never breaks because of it.

    ``tracks`` — звуковые дорожки источника (app/source_audio.py); None — первая.
    ``strict`` — сбой распознавания поднимается, а не превращается в «пусто»: пустой
    список тогда честно значит «речи нет».
    """

    if not local_path:
        return []
    path = Path(local_path)
    if not path.exists():
        return []

    # Импорты внутри _transcribe_piece: render импортирует AI-хелперы (цикл).
    duration = _duration_sec(path)
    cues: list[dict] = []
    try:
        # Кусками: whisper держит весь звук в памяти, а 4.7 часа стрима — это
        # гигабайты; целиком они выбивали сервис за лимит памяти контейнера.
        start = 0.0
        while True:
            length = CHUNK_SEC if duration > CHUNK_SEC else None
            cues.extend(_transcribe_piece(path, start, length, model, tracks))
            start += CHUNK_SEC
            if not length or start >= duration:
                break
        return cues
    except Exception:  # noqa: BLE001 - transcript is an optional enhancement
        if strict:
            raise
        log.warning("analysis transcript failed for %s", path.name, exc_info=True)
        return cues


CHUNK_SEC = 20 * 60


def _transcribe_piece(path: Path, start: float, length: float | None, model: str | None,
                      tracks: list[int] | None = None) -> list[dict]:
    """Кусок звука [start, start+length) → реплики во времени исходника."""
    from app.render import _run_ffmpeg, build_ffmpeg_extract_audio_args
    from app.subtitles.whisper_local import WhisperSubtitleProvider

    audio_path = settings.tmp_dir / f"analysis-{uuid4().hex}.wav"
    try:
        _run_ffmpeg(
            build_ffmpeg_extract_audio_args(path, audio_path, start_sec=start if length else None, duration_sec=length,
                                            tracks=tracks),
            timeout=60 * 30,
        )
        try:
            result = WhisperSubtitleProvider().transcribe(audio_path, {"language": ""}, (model or settings.whisper_model_size))
        except RuntimeError as exc:
            # Кусок без речи (игра, музыка) — это ответ «речи нет», а не сбой: иначе
            # расшифровка 4-часовой записи обрывалась на первых 20 минутах без слов.
            if "no word-level timestamps" in str(exc):
                return []
            raise
        out: list[dict] = []
        for seg in result.segments:
            text = str(getattr(seg, "text", "") or "").strip()
            if text:
                out.append({"start": round(start + float(seg.start), 2), "end": round(start + float(seg.end), 2),
                            "text": text})
        return out
    finally:
        audio_path.unlink(missing_ok=True)


def _duration_sec(path: Path) -> float:
    import subprocess

    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=False,
    )
    try:
        return float((proc.stdout or "0").strip() or 0)
    except ValueError:
        return 0.0


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
