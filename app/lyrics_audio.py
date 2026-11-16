"""Текст песни по звуку: участок трека → вокал → слова Whisper → выравнивание текста (app/lyrics_align.py).

Фоном и в кэше, как события геймплея (app/game_events_job.py): одно задание на (файл, текст, язык, участок, модель),
повторный вызов к нему подключается; вызов ждёт не дольше ``wait`` секунд и иначе отвечает
``{status: "running", stage, retry_after_sec}``. Итог — файл ``<runtime>/lyrics/<key>.json`` (``key`` — отпечаток
файла, текста и настроек): по ``lyrics_id`` его берёт ``lyric_layers``. Тяжёлое — в очереди тяжёлых задач.

Вокал. Есть ``DEMUCS_CMD`` (команда demucs, аргументы через «|», например
``D:/…/demucs_venv/Scripts/python.exe|-m|demucs``) — настоящий стем htdemucs (``--two-stems vocals``), как в «клипе на
песню»: Whisper слушает стем, начала слов в тишине вокала переносятся туда, где голос зазвучал, и привязываются к
атакам слогов. Нет или упал — ``method: band``: Whisper слушает участок как есть, время слов — его (полоса голоса
150 Гц–5 кГц не чистый вокал: привязка к её атакам портила время, замер 16.11), пометка в ответе.

Модель — ``LYRICS_WHISPER_MODEL`` (по умолчанию large-v3-turbo: small на пении путает слова), качается один раз в
``whisper-models`` данных; модуль «Распознавание речи» выключен — отказ, как у всего Whisper.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import threading
import time
import wave
from pathlib import Path
from typing import Any

from app import lyrics_align as LA

log = logging.getLogger(__name__)

VERSION = 1
WAIT_SEC = 45.0
DEFAULT_MODEL = "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
DEMUCS_TIMEOUT_SEC = 1800
ANALYSIS_SR = 22050
HINT_CHARS = 220  # начало текста подсказкой Whisper (initial_prompt): названия и имена распознаются точнее
_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()


def lyrics_model() -> str:
    return os.getenv("LYRICS_WHISPER_MODEL", "").strip() or DEFAULT_MODEL


def demucs_cmd() -> list[str]:
    raw = os.getenv("DEMUCS_CMD", "").strip()
    return [p for p in raw.split("|") if p] if raw else []


def cache_dir() -> Path:
    from app.settings import settings

    folder = settings.runtime_dir / "lyrics"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _stamp(path: Path) -> dict:
    try:
        st = path.stat()
        return {"path": str(path), "size": st.st_size, "mtime": int(st.st_mtime)}
    except OSError:
        return {"path": str(path)}


def job_key(path: Path, lyrics: str, lang: str, start: float, end: float | None, model: str, hint: bool) -> str:
    raw = json.dumps({"v": VERSION, "file": _stamp(path), "lyrics": " ".join(str(lyrics or "").split()),
                      "lang": lang, "start": round(start, 2), "end": None if end is None else round(end, 2),
                      "model": model, "hint": hint, "demucs": bool(demucs_cmd())}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def load_result(key: str) -> dict | None:
    """Готовое выравнивание по ``lyrics_id`` (ключу задания) или None."""
    key = "".join(ch for ch in str(key or "") if ch.isalnum())[:32]
    file = cache_dir() / f"{key}.json"
    if not key or not file.exists():
        return None
    try:
        return json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Звук
# --------------------------------------------------------------------------- #


def _ffmpeg(args: list[str], timeout: int = 600) -> None:
    proc = subprocess.run(["ffmpeg", "-nostdin", "-y", "-hide_banner", "-loglevel", "error", *args],
                          capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg: {(proc.stderr or '').strip()[-300:]}")


def cut_section(src: Path, work: Path, start: float, end: float | None) -> Path:
    """Участок трека [start, end] → mix.wav (44.1 кГц стерео, 16 бит) — для demucs и Whisper."""
    out = work / "mix.wav"
    args = ["-ss", f"{max(0.0, start):.3f}"]
    if end is not None:
        args += ["-t", f"{max(0.1, end - start):.3f}"]
    _ffmpeg([*args, "-i", str(src), "-vn", "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(out)])
    return out


def isolate_vocals(mix: Path, work: Path) -> tuple[Path, str, str]:
    """Вокал участка → (vocals.wav 22 кГц моно 16 бит, method demucs|band, пояснение)."""
    out = work / "vocals.wav"
    cmd = demucs_cmd()
    note = ""
    if cmd:
        try:
            from app.heavy_queue import heavy_slot

            with heavy_slot("demucs", mix.name):
                proc = subprocess.run([*cmd, "-n", "htdemucs", "--two-stems", "vocals", "-j", "1", "-o",
                                       str(work / "sep"), str(mix)], capture_output=True, text=True,
                                      timeout=DEMUCS_TIMEOUT_SEC)
            stem = work / "sep" / "htdemucs" / mix.stem / "vocals.wav"
            if proc.returncode == 0 and stem.exists():
                _ffmpeg(["-i", str(stem), "-ac", "1", "-ar", str(ANALYSIS_SR), "-c:a", "pcm_s16le", str(out)])
                return out, "demucs", "вокал — стем demucs htdemucs"
            note = f"demucs не отработал ({(proc.stderr or '').strip()[-160:]}) — "
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            note = f"demucs не запустился ({str(exc)[:160]}) — "
    _ffmpeg(["-i", str(mix), "-af", "pan=mono|c0=0.5*c0+0.5*c1,highpass=f=150,lowpass=f=5000",
             "-ar", str(ANALYSIS_SR), "-c:a", "pcm_s16le", str(out)])
    return out, "band", note + ("вокала отдельно нет (demucs не настроен: DEMUCS_CMD) — время слов по Whisper без "
                                "привязки к слогам, ошибка ~0.1 с, у первых слов фраз бывает до 1–2 с: проверь на слух")


def read_mono(path: Path) -> tuple[Any, int]:
    import numpy as np

    with wave.open(str(path)) as w:
        sr, n, ch, sw = w.getframerate(), w.getnframes(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(n)
    if sw != 2:
        raise ValueError(f"{path.name}: ожидался 16-битный wav")
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return x.reshape(-1, ch).mean(1), sr


def transcribe(path: Path, lang: str, model: str, prompt: str = "") -> tuple[list[dict], str]:
    """Слова Whisper с временем (секунды файла) и язык. VAD выключен: на пении он режет строки."""
    from app.heavy_queue import heavy_slot
    from app.subtitles.whisper_local import WhisperSubtitleProvider

    provider = WhisperSubtitleProvider()
    with heavy_slot("whisper", f"текст песни {path.name}"):
        try:
            whisper = provider._get_model(model)  # noqa: SLF001 - модель общая с субтитрами: одна в памяти
            segments, info = whisper.transcribe(
                str(path), language=lang or None, word_timestamps=True, vad_filter=False, beam_size=5,
                temperature=0.0, condition_on_previous_text=False, initial_prompt=prompt or None,
                no_speech_threshold=0.6, compression_ratio_threshold=2.4)
            words = []
            for segment in segments:
                for word in getattr(segment, "words", None) or []:
                    token = str(getattr(word, "word", "") or "").strip()
                    if token and getattr(word, "start", None) is not None:
                        words.append({"w": token, "s": round(float(word.start), 3),
                                      "e": round(float(word.end or word.start), 3),
                                      "p": round(float(getattr(word, "probability", 1.0) or 0.0), 3)})
        finally:
            provider._schedule_unload()  # noqa: SLF001
    return words, str(getattr(info, "language", "") or lang or "")


# --------------------------------------------------------------------------- #
# Весь проход
# --------------------------------------------------------------------------- #


def align_audio(path: Path, lyrics: str = "", lang: str = "", start: float = 0.0, end: float | None = None,
                model: str | None = None, hint: bool = True, progress: Any = None, keep_dir: Path | None = None) -> dict:
    """Звук + текст → выравнивание (секунды трека). Без текста — строки из того, что распознал Whisper.

    ``keep_dir`` — рабочая папка, которая остаётся после прохода (участок ``mix.wav``, вокал ``vocals.wav``, стем demucs
    в ``sep/``): «Клип на песню» (app/music_video_job.py) берёт их дальше. Без неё — временная папка, удаляется."""
    from app.settings import settings

    model = model or lyrics_model()
    stage = progress or (lambda _s: None)
    lines_in = LA.parse_lyrics(lyrics)
    work = Path(keep_dir) if keep_dir else Path(settings.tmp_dir) / f"lyrics-{os.getpid()}-{time.monotonic_ns()}"
    work.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    try:
        stage("вырезаю участок трека")
        mix = cut_section(path, work, start, end)
        stage("отделяю вокал")
        vocals, method, method_note = isolate_vocals(mix, work)
        stage("распознаю слова (Whisper)")
        heard_src = vocals if method == "demucs" else mix
        prompt = " ".join(line["text"] for line in lines_in)[:HINT_CHARS] if hint and lines_in else ""
        heard, language = transcribe(heard_src, lang, model, prompt)
        heard = [{**w, "s": round(w["s"] + start, 3), "e": round(w["e"] + start, 3)} for w in heard]
        stage("выравниваю текст по слогам")
        x, sr = read_mono(vocals)
        env = LA.vocal_envelope(x, sr, t0=start)
        onsets = env["onsets"]
        result = LA.align_text(lines_in, heard) if lines_in else LA.lines_from_heard(heard)
        if method == "demucs":
            fixed = LA.fix_silent_starts(result["lines"], env)
            snap = LA.snap_to_onsets(result["lines"], onsets)
        else:
            # Полоса голоса — не чистый вокал: атаки там — больше бочка и гитара, чем слоги. Замер 16.11 (Stay
            # Together, 40 с): привязка к ним портила время Whisper (слово в тишине вокала 8 % → 15 %, попадание в
            # атаку слога 44 % → 40 %), поэтому без demucs время слов — Whisper как есть.
            fixed = {"moved": 0}
            snap = {"snapped": 0, "words": sum(len(line["words"]) for line in result["lines"]), "mean_shift": 0.0}
    finally:
        if not keep_dir:
            shutil.rmtree(work, ignore_errors=True)
    notes = [method_note] if method_note else []
    stats = {**result["stats"], **{f"snap_{k}": v for k, v in snap.items()}, "onsets": len(onsets),
             "silent_starts_fixed": fixed["moved"]}
    if not lines_in:
        notes.append("текста не дали — строки из распознанного Whisper (по паузам): сверь слова на слух, "
                     "сомнительные (unsure) не показывай; лучше пришли текст владельца")
    elif stats["heard_share"] < 0.5:
        notes.append(f"услышано только {round(stats['heard_share'] * 100)}% слов текста — тот ли это трек и участок? "
                     "Время остальных — по соседям (interp); проверь на слух")
    if stats.get("unsure_lines"):
        notes.append(f"сомнительных строк: {stats['unsure_lines']} (unsure) — плейбук велит их не показывать")
    return {"version": VERSION, "time": "track", "start_sec": round(start, 3),
            "end_sec": None if end is None else round(end, 3), "lang": language, "model": model, "method": method,
            "with_text": bool(lines_in), "lines": result["lines"], "heard": heard, "stats": stats, "notes": notes,
            "elapsed_sec": round(time.monotonic() - started, 1)}


def _run(path: Path, key: str, params: dict, job: dict) -> None:
    try:
        result = align_audio(path, progress=lambda s: job.__setitem__("stage", s), **params)
        result["lyrics_id"] = key
        result["file"] = str(path)
        (cache_dir() / f"{key}.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        job["result"] = result
    except Exception as exc:  # noqa: BLE001 - ошибка уходит вызывающему
        log.warning("align_lyrics %s: %s", key, exc)
        job["error"] = exc
    finally:
        job["finished"].set()


def align_job(path: Path, *, lyrics: str = "", lang: str = "", start: float = 0.0, end: float | None = None,
              model: str | None = None, hint: bool = True, refresh: bool = False, wait: float | None = None) -> dict:
    """Итог (``status: done``) из кэша или прохода; дольше ``wait`` — ``status: running``."""
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"нет файла звука {path.name}")
    model = model or lyrics_model()
    key = job_key(path, lyrics, lang, start, end, model, hint)
    wait = WAIT_SEC if wait is None else max(0.0, float(wait))
    with _LOCK:
        job = _JOBS.get(key)
        if job is not None and job["finished"].is_set():
            _JOBS.pop(key, None)
            job = None
        if job is None:
            if not refresh:
                hit = load_result(key)
                if hit is not None:
                    return {**hit, "status": "done", "cached": True}
            job = {"finished": threading.Event(), "started": time.monotonic(), "stage": "в очереди"}
            _JOBS[key] = job
            params = {"lyrics": lyrics, "lang": lang, "start": start, "end": end, "model": model, "hint": hint}
            threading.Thread(target=_run, args=(path, key, params, job), name=f"lyrics-{key}", daemon=True).start()
    if job["finished"].wait(wait):
        with _LOCK:
            if _JOBS.get(key) is job:
                _JOBS.pop(key)
        if "error" in job:
            err = job["error"]
            raise err if isinstance(err, (KeyError, ValueError)) else ValueError(str(err)[:300])
        return {**job["result"], "status": "done", "cached": False}
    elapsed = time.monotonic() - job["started"]
    return {"status": "running", "lyrics_id": key, "stage": job["stage"], "elapsed_sec": round(elapsed),
            "retry_after_sec": 30,
            "note": f"текст песни выравнивается фоном ({job['stage']}, {round(elapsed)} с): повтори тот же вызов через "
                    "~30 с — второй проход не запустится"}


__all__ = ["align_audio", "align_job", "cache_dir", "demucs_cmd", "load_result", "lyrics_model"]
