"""Видеомем в библиотеку: загрузка файла или отрезок по ссылке → подрезка → mp4 (h264 + AAC) → расшифровка в фоне.

* Подрезка руками — ``trim_start``/``trim_end`` (секунды исходного файла или отрезка по ссылке).
* Автоподрезка (``auto_trim``, по умолчанию да) — тишина и чёрный кадр в начале и в конце (ffmpeg silencedetect
  и blackdetect): реакция должна начинаться сразу, а не через полсекунды тишины. Режется не больше половины файла,
  с маленьким запасом до звука; файл без звука режется только по чёрному.
* Ссылка — тот же yt-dlp, маршруты и прокси (``DOWNLOAD_PROXY_URL`` / настройки «Прокси»), что у источников:
  ``app/video_import.fetched_section``. Без ``end`` берутся первые ``URL_DEFAULT_SEC`` секунд от ``start``.
* Расшифровка — локальный Whisper сервиса (app/subtitles/whisper_local.py, large-v3-turbo, язык сам, без VAD —
  короткое «вау» VAD съедает), в фоне: ответ на загрузку не ждёт. Итог — ``transcript_status``
  (pending → running → done | empty | failed). Тесты подменяют ``TRANSCRIBE`` и ``_spawn``.
"""

from __future__ import annotations

import logging
import re
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from app import video_memes as VM

_log = logging.getLogger(__name__)

URL_DEFAULT_SEC = 8.0
MIN_SEC = 0.3
SILENCE_DB = -42
SILENCE_MIN_SEC = 0.12
BLACK_MIN_SEC = 0.08
LEAD_PAD = 0.06  # до первого звука оставить
TAIL_PAD = 0.2  # после последнего звука оставить
MAX_TRIM_SHARE = 0.5

# Подмена в тестах: path -> {"text", "language", "words": [{word, start, end}]}. None — Whisper сервиса.
TRANSCRIBE: Callable[[Path], dict] | None = None


def _spawn(target: Callable[[], Any], name: str) -> None:
    threading.Thread(target=target, name=name, daemon=True).start()


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


# --------------------------------------------------------------------------- #
# Подрезка и перекодирование
# --------------------------------------------------------------------------- #


def parse_edges(log: str, length: float, has_audio: bool) -> tuple[float, float]:
    """Из вывода ffmpeg (silencedetect + blackdetect по отрезку длиной ``length``) — сколько срезать в начале
    и где закончить: ``(lead, end)`` в секундах отрезка. Ничего лишнего — ``(0, length)``."""
    lead, end = 0.0, length
    for m in re.finditer(r"black_start:\s*([\d.]+)\s+black_end:\s*([\d.]+)", log):
        a, b = float(m.group(1)), float(m.group(2))
        if a <= 0.05:
            lead = max(lead, b)
        if b >= length - 0.05:
            end = min(end, a)
    if has_audio:
        starts = [float(x) for x in re.findall(r"silence_start:\s*(-?[\d.]+)", log)]
        ends = [float(x) for x in re.findall(r"silence_end:\s*([\d.]+)", log)]
        if starts and starts[0] <= 0.05 and ends:
            lead = max(lead, ends[0] - LEAD_PAD)
        if starts and (len(ends) < len(starts) or (ends and ends[-1] >= length - 0.05)):
            end = min(end, starts[-1] + TAIL_PAD)
    lead, end = max(0.0, lead), min(length, end)
    if end - lead < max(MIN_SEC, length * (1 - MAX_TRIM_SHARE)):
        return 0.0, length  # «тишина» почти весь файл — это не край, а сам мем (или детектор ошибся)
    return round(lead, 3), round(end, 3)


def detect_edges(src: Path, start: float, length: float, has_audio: bool) -> tuple[float, float]:
    """Тишина и чёрное по краям отрезка ``start..start+length`` файла ``src`` (ffmpeg; ошибка — без подрезки)."""
    filters = ["-vf", f"blackdetect=d={BLACK_MIN_SEC}:pix_th=0.10"]
    if has_audio:
        filters += ["-af", f"silencedetect=n={SILENCE_DB}dB:d={SILENCE_MIN_SEC}"]
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", str(src),
             *filters, "-f", "null", "-"],
            capture_output=True, text=True, timeout=120, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return 0.0, length
    return parse_edges(proc.stderr or "", length, has_audio)


def encode_args(src: Path, dest: Path, start: float, length: float) -> list[str]:
    """ffmpeg: отрезок файла → h264 + AAC 48 кГц (≤ 1920 по стороне), ровно ``length`` секунд."""
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.3f}", "-i", str(src),
            "-t", f"{length:.3f}", "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn",
            "-vf", "scale='min(1920,iw)':'min(1920,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(dest)]


def prepare_file(src: Path, dest: Path, trim_start: Any = None, trim_end: Any = None,
                 auto_trim: bool = True) -> dict:
    """Подрезать и перекодировать ``src`` в ``dest``. Ответ — probe итогового файла + ``trimmed``
    (что срезано и почему). ValueError — нет кадров, отрезок пуст или мем длиннее ``VM.MAX_MEME_SEC``."""
    from app.montage_assets import probe_asset
    from app.render import _run_ffmpeg

    meta = probe_asset(src)
    total = float(meta.get("duration_sec") or 0)
    if not meta.get("width") or total <= 0:
        raise ValueError("в файле нет видео (нужен ролик со звуком: mp4, mov, webm…)")
    a = max(0.0, _num(trim_start) or 0.0)
    b = _num(trim_end)
    b = total if b is None or b <= 0 else min(total, b)
    if b - a < MIN_SEC:
        raise ValueError(f"после подрезки короче {MIN_SEC:g} с — проверь trim_start/trim_end")
    lead, end = detect_edges(src, a, b - a, bool(meta.get("has_audio"))) if auto_trim else (0.0, b - a)
    start, length = a + lead, end - lead
    if length > VM.MAX_MEME_SEC + 1e-6:
        raise ValueError(f"мем {length:.1f} с — длиннее {VM.MAX_MEME_SEC:g} с не берём: задай trim_start/trim_end "
                         "(реакция — 0.5–4 с)")
    try:
        _run_ffmpeg(encode_args(src, dest, start, length), timeout=300)
    except Exception as exc:  # noqa: BLE001
        dest.unlink(missing_ok=True)
        raise ValueError(f"видео не перекодировалось: {str(exc)[:200]}") from exc
    out = probe_asset(dest) if dest.exists() else {}
    if not out.get("width") or not out.get("duration_sec"):
        dest.unlink(missing_ok=True)
        raise ValueError("после перекодирования в файле нет кадров")
    trimmed = {"from": round(start, 3), "to": round(start + length, 3), "source_sec": round(total, 3)}
    if lead > 0 or end < b - a - 1e-3:
        trimmed["auto"] = {"lead": round(lead, 3), "tail": round(b - a - end, 3)}
    return {**out, "trimmed": trimmed}


# --------------------------------------------------------------------------- #
# Заведение мема
# --------------------------------------------------------------------------- #


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-.")[:50] or "meme"


def create_from_file(store: Any, src: Path, *, original_name: str = "", title: str = "", trim_start: Any = None,
                     trim_end: Any = None, auto_trim: bool = True, transcribe: bool = True, **fields: Any) -> dict:
    """Файл ``src`` (не трогается) → mp4 в библиотеке → «Файл для монтажа» + мем; расшифровка — в фоне."""
    from app.montage_assets import assets_dir

    name = f"{_slug(Path(original_name or src.name).stem)}.mp4"
    dest = assets_dir() / f"{uuid4().hex}-{name}"
    prepared = prepare_file(src, dest, trim_start, trim_end, auto_trim)
    trimmed = prepared.pop("trimmed")
    asset = store.create_montage_asset(
        kind="video",
        label=(title or Path(original_name or src.name).stem or "Видеомем").strip()[:120],
        tags="видеомем",
        file_path=str(dest),
        original_filename=original_name or src.name,
        mime_type="video/mp4",
        size_bytes=dest.stat().st_size,
        project_id=None,  # мемы — общие файлы
        **prepared,
    )
    status = "pending" if transcribe and asset.get("has_audio") else ("empty" if transcribe else "off")
    meme = VM.create_meme(store, int(asset["id"]), trimmed=trimmed, transcript_status=status, **fields)
    if status == "pending":
        start_transcript(store, int(meme["id"]))
        meme = VM.get_meme(store, int(meme["id"]))
    return meme


def create_from_url(store: Any, url: str, *, start: Any = None, end: Any = None, title: str = "",
                    auto_trim: bool = True, transcribe: bool = True, **fields: Any) -> dict:
    """Отрезок по ссылке (yt-dlp сервиса) → мем. Ссылка становится ``source_url``, если она не задана."""
    from app.video_import import fetched_section

    a = max(0.0, _num(start) or 0.0)
    b = _num(end)
    b = a + URL_DEFAULT_SEC if b is None or b <= a else b
    with fetched_section(url, a, b) as (file, video_title, _a, _b):
        return create_from_file(
            store, file, original_name=f"{_slug(title or video_title or 'meme')}.mp4",
            title=title or (f"{video_title} {a:.0f}–{b:.0f} с" if video_title else "Видеомем по ссылке"),
            auto_trim=auto_trim, transcribe=transcribe,
            **{**fields, "source_url": fields.get("source_url") or url.strip()},
        )


# --------------------------------------------------------------------------- #
# Расшифровка
# --------------------------------------------------------------------------- #


def whisper_transcribe(path: Path) -> dict:
    """Whisper сервиса: язык сам, без VAD (короткие выкрики), слова с временем."""
    from app.subtitles.registry import QUALITY_WHISPER_MODEL
    from app.subtitles.whisper_local import WhisperSubtitleProvider

    result = WhisperSubtitleProvider().transcribe(Path(path), {"language": "", "vad_filter": False},
                                                   QUALITY_WHISPER_MODEL)
    words = [{"word": w.word.strip(), "start": round(float(w.start), 3), "end": round(float(w.end), 3)}
             for w in result.words if str(w.word or "").strip()]
    return {"text": (result.text or "").strip(), "language": result.language or "", "words": words}


def transcribe_meme(store: Any, meme_id: int) -> dict:
    """Расшифровать мем сейчас (в фоне зовёт ``start_transcript``). Ошибка не бросается — пишется в мем."""
    meme = VM.get_meme(store, meme_id)
    path = Path(meme.get("file_path") or "")
    if not meme.get("has_audio") or not path.is_file():
        return VM.set_transcript(store, meme_id, status="empty",
                                 error="" if path.is_file() else "файл мема не найден")
    store.db.execute("UPDATE video_memes SET transcript_status = 'running' WHERE id = ?", (int(meme_id),))
    try:
        result = (TRANSCRIBE or whisper_transcribe)(path)
    except Exception as exc:  # noqa: BLE001 - расшифровка — удобство, мем остаётся
        _log.warning("видеомем %s: расшифровка не удалась: %s", meme_id, exc)
        return VM.set_transcript(store, meme_id, status="failed", error=f"{type(exc).__name__}: {str(exc)[:200]}")
    text = str(result.get("text") or "").strip()
    return VM.set_transcript(store, meme_id, status="done" if text else "empty", text=text,
                             language=str(result.get("language") or ""), words=list(result.get("words") or []))


def start_transcript(store: Any, meme_id: int) -> None:
    VM.set_transcript(store, meme_id, status="pending")
    _spawn(lambda: transcribe_meme(store, meme_id), f"video-meme-transcript-{meme_id}")
