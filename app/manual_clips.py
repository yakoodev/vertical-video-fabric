"""Клипы без Gemini: расшифровка исходника и клип по таймкодам.

Владелец спросил, надо ли пополнять Google, — не обязательно. Моменты может
выбрать агент-монтажёр сам: читает расшифровку (локальный Whisper), смотрит
кадры и создаёт клип по таймкодам. Клипы складываются в анализ-«папку»
``manual`` исходника.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.settings import settings

MAX_PIECES = 12


def source_transcript(store: Any, source_id: int, *, refresh: bool = False) -> dict:
    """Расшифровка исходника (кэш на источнике; первый раз — Whisper, минуты)."""
    from app.ai import transcript
    from app.source_audio import audio_streams, source_tracks

    source = store.get_source(source_id)
    cues = [] if refresh else store.get_source_transcript(source_id)
    no_speech = not refresh and not cues and store.get_source_transcript_model(source_id).endswith(NO_SPEECH)
    cached = bool(cues) or no_speech
    if not cues and not no_speech:
        try:
            cues = transcript.transcribe_source_cues(source.get("local_path"), tracks=source_tracks(source), strict=True)
        except Exception as exc:  # noqa: BLE001 - сбой распознавания — понятная ошибка, а не «речи нет»
            raise ValueError(f"расшифровка не получилась: {str(exc)[:200]}") from exc
        if cues:
            store.set_source_transcript(source_id, cues, settings.whisper_model_size)
        elif Path(str(source.get("local_path") or "")).is_file() and audio_streams(source):
            # Игра, музыка без слов: звук есть, речи нет — честный пустой ответ
            # (и в кэш, чтобы не гонять Whisper по 4-часовой записи снова).
            no_speech = True
            store.set_source_transcript(source_id, [], settings.whisper_model_size + NO_SPEECH)
        else:
            raise ValueError("расшифровка не получилась (нет звука или файла исходника)")
    out = {"source_id": source_id, "cached": cached, "duration_sec": source.get("duration_sec"), "cues": cues}
    if no_speech:
        out["no_speech"] = True
        out["note"] = ("речи не найдено (звук есть): игра или музыка без слов. Моменты ищи по громкости "
                       "(source_loudness) и кадрам (source_contact_sheet); если голос на другой звуковой "
                       "дорожке — set_source_audio")
    return out


NO_SPEECH = "|no_speech"


def _manual_analysis(store: Any, source_id: int) -> dict:
    for analysis in store.list_ai_analyses(source_id):
        if analysis.get("provider") == "manual":
            return analysis
    return store.create_ai_analysis(source_id, "manual", model="agent", status="succeeded")


def create_manual_clip(store: Any, source_id: int, title: str, pieces: list[dict], description: str = "") -> dict:
    """Клип из кусков исходника [{start_sec, end_sec, title?}] — дальше как обычный клип."""
    title = " ".join(str(title or "").split())[:100]
    if not title:
        raise ValueError("нужно название клипа")
    if not isinstance(pieces, list) or not pieces:
        raise ValueError("нужен хотя бы один кусок {start_sec, end_sec}")
    if len(pieces) > MAX_PIECES:
        raise ValueError(f"не больше {MAX_PIECES} кусков")
    segments = []
    for index, piece in enumerate(pieces, start=1):
        try:
            start, end = float(piece["start_sec"]), float(piece["end_sec"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"кусок {index}: нужны числа start_sec и end_sec") from exc
        segments.append({
            "start_sec": start, "end_sec": end,
            "title": str(piece.get("title") or f"{title} — {index}")[:100],
            "reason": "выбрал агент по расшифровке",
        })
    analysis = _manual_analysis(store, source_id)
    created = store.create_ai_segments(analysis["id"], segments)
    return store.create_clip_plan(
        source_id, analysis["id"], title, description=str(description or "")[:2000],
        segment_ids=[seg["id"] for seg in created],
    )
