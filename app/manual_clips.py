"""Клипы без Gemini: расшифровка исходника и клип по таймкодам.

Владелец спросил, надо ли пополнять Google, — не обязательно. Моменты может
выбрать агент-монтажёр сам: читает расшифровку (локальный Whisper), смотрит
кадры и создаёт клип по таймкодам. Клипы складываются в анализ-«папку»
``manual`` исходника.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable

from app.settings import settings

MAX_PIECES = 12
# Сколько ждать готовую расшифровку в одном запросе. Агент-монтажёр: первый вызов
# get_source_transcript держал HTTP ~2.5 мин, и соединение рвалось («Remote end closed
# connection»), хотя распознавание шло дальше. Дольше этого — ответ «распознаю,
# повтори через …», а Whisper работает фоном.
TRANSCRIPT_WAIT_SEC = 45.0
# Куски фонового распознавания: короче обычных 20 мин, чтобы прогресс шёл по минутам.
JOB_CHUNK_SEC = 5 * 60


def source_transcript(store: Any, source_id: int, *, refresh: bool = False,
                      progress: Callable[[float, float], None] | None = None) -> dict:
    """Расшифровка исходника (кэш на источнике; первый раз — Whisper, минуты).

    Синхронно: держит вызов до конца распознавания. Для HTTP/MCP — ``transcript_job``."""
    from app.ai import transcript
    from app.source_audio import audio_streams, speech_mix

    source = store.get_source(source_id)
    cues = [] if refresh else store.get_source_transcript(source_id)
    no_speech = not refresh and not cues and store.get_source_transcript_model(source_id).endswith(NO_SPEECH)
    cached = bool(cues) or no_speech
    if not cues and not no_speech:
        try:
            extra = {"progress": progress, "chunk_sec": JOB_CHUNK_SEC} if progress is not None else {}
            cues = transcript.transcribe_source_cues(source.get("local_path"), tracks=speech_mix(source), strict=True,
                                                     **extra)
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


# --------------------------------------------------------------------------- #
# Фоновое распознавание: один поток на источник, запрос ждёт не дольше WAIT
# --------------------------------------------------------------------------- #

_JOBS: dict[int, dict] = {}
_JOBS_LOCK = threading.Lock()


def _has_cache(store: Any, source_id: int) -> bool:
    return bool(store.get_source_transcript(source_id)) or         store.get_source_transcript_model(source_id).endswith(NO_SPEECH)


def _run_job(store: Any, source_id: int, job: dict, refresh: bool) -> None:
    def progress(done: float, total: float) -> None:
        job["done_sec"], job["total_sec"] = float(done), float(total)
        job["progress_at"] = time.monotonic()

    try:
        job["result"] = source_transcript(store, source_id, refresh=refresh, progress=progress)
    except Exception as exc:  # noqa: BLE001 - ошибку отдаст следующий опрос
        job["error"] = exc
    finally:
        job["finished"].set()


def transcript_job(store: Any, source_id: int, *, refresh: bool = False, wait: float | None = None) -> dict:
    """Расшифровка без долгого HTTP: из кэша сразу; иначе Whisper в фоне (один поток
    на источник — повторный вызов к нему же подключается, второго не плодит) и ждём
    его не дольше ``wait`` с. Не успел — ``{status: "running", done_min, total_min,
    retry_after_sec, note}``: позови ещё раз через ``retry_after_sec``. Готово —
    обычный ответ ``source_transcript`` (``status: "done"``)."""
    wait = TRANSCRIPT_WAIT_SEC if wait is None else wait
    store.get_source(source_id)  # нет источника — KeyError сразу, а не из потока
    with _JOBS_LOCK:
        job = _JOBS.get(source_id)
        if job is not None and job["finished"].is_set() and (refresh or "error" not in job and "result" not in job):
            job = None
        if job is None:
            if not refresh and _has_cache(store, source_id):
                return {**source_transcript(store, source_id), "status": "done"}
            job = {"finished": threading.Event(), "started": time.monotonic(), "done_sec": 0.0, "total_sec": 0.0}
            _JOBS[source_id] = job
            threading.Thread(target=_run_job, args=(store, source_id, job, refresh),
                             name=f"transcript-{source_id}", daemon=True).start()
    if job["finished"].wait(max(0.0, float(wait))):
        with _JOBS_LOCK:
            if _JOBS.get(source_id) is job:
                _JOBS.pop(source_id)
        if "error" in job:
            error = job["error"]
            raise error if isinstance(error, (KeyError, ValueError)) else ValueError(str(error)[:200])
        return {**job["result"], "status": "done"}
    return _job_status(source_id, job)


def _job_status(source_id: int, job: dict) -> dict:
    done, total = job["done_sec"], job["total_sec"]
    elapsed = max(0.0, time.monotonic() - job["started"])
    if done > 0 and total > done:
        # Скорость по уже готовым кускам; повторить — к следующему куску, но не реже раза в 2 мин.
        left = elapsed / done * (total - done)
        retry = min(120.0, max(15.0, min(left, elapsed / done * JOB_CHUNK_SEC)))
    else:
        retry = 30.0
    done_min, total_min = round(done / 60, 1), round(total / 60, 1)
    progress = f"готово {done_min:g} из {total_min:g} мин" if total else "готовлю звук"
    return {
        "source_id": source_id, "status": "running", "done_min": done_min, "total_min": total_min,
        "elapsed_sec": round(elapsed), "retry_after_sec": int(round(retry)),
        "note": (f"распознаю: {progress}, повтори через ~{int(round(retry))} с (тот же вызов; второе "
                 "распознавание не запустится). Пока можно смотреть кадры — source_contact_sheet."),
    }


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
            # source_start/source_end — имена из get_clip.pieces (алиасы start_sec/end_sec).
            start = float(piece["start_sec"] if "start_sec" in piece else piece["source_start"])
            end = float(piece["end_sec"] if "end_sec" in piece else piece["source_end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"кусок {index}: нужны числа start_sec и end_sec") from exc
        segments.append({
            "start_sec": start, "end_sec": end,
            "title": str(piece.get("title") or f"{title} — {index}")[:100],
            "reason": "выбрал агент по расшифровке",
        })
    analysis = _manual_analysis(store, source_id)
    created = store.create_ai_segments(analysis["id"], segments)
    plan = store.create_clip_plan(
        source_id, analysis["id"], title, description=str(description or "")[:2000],
        segment_ids=[seg["id"] for seg in created],
    )
    return with_default_preset(store, plan)


def with_default_preset(store: Any, plan: dict) -> dict:
    """Новый клип агента — сразу с пресетом рендера по умолчанию (а не preset_id null).

    Агент стримов: у новых клипов preset_id null — непонятно, чем отрендерится. Ставим тот же
    пресет, что взял бы рендер (``effective_preset``), числом; остальные настройки — по
    умолчанию (как у клипа, сохранённого из редактора)."""
    from app.clip_settings import normalize_render_settings
    from app.render import effective_preset

    if plan.get("render_settings"):
        return plan
    preset = effective_preset(store, None)
    if not preset:
        return plan
    store.set_clip_plan_render_settings(plan["id"], normalize_render_settings({"preset_id": preset["id"]}))
    return store.get_clip_plan(plan["id"])
