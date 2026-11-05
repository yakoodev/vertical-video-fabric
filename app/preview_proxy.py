"""Лёгкий прокси 480p для предпросмотра монтажа.

Записи стримов весят 10–12 ГБ при 1280×720: первый кадр по слабой сети ждать долго, а прыжки
по склейкам упираются в битрейт оригинала. Прокси — тот же таймлайн с теми же секундами, но
~0.8 Мбит/с, ключевой кадр раз в секунду и лёгкий индекс в начале файла: первый кадр и перемотка
укладываются в пару Range-запросов.

* Оригинал не меняется и не удаляется: файл прокси лежит отдельно в ``runtime/proxies``
  (в проде это том на D:, как и остальной ``runtime``).
* Генерация фоном и кэш на диске: первый запрос ставит задачу, пока её нет — предпросмотр играет
  оригинал. Параллельно — не больше двух ffmpeg (``PREVIEW_PROXY_CONCURRENCY``, 1 или 2).
* Запись нескольких дорожек (игра/микрофон) смешивается как у рендера (``source_audio.mix_chain``):
  браузер сам играет только первую дорожку. Сменили смесь — прокси пересоберётся.
* Файл записи удалили, а прокси остался — монтаж всё равно можно смотреть и править (рендера с него нет).
  Удаляется прокси только вместе с самим источником (``store.delete_source``: клипов тогда уже нет).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from app.settings import settings

PROXY_VERSION = 2
SHORT_SIDE = 480
MAX_FPS = 30
CRF = "28"
AUDIO_BITRATE = "96k"
# Запись меньше этого легко играет и так: автоматически прокси не делаем (можно с force).
MIN_AUTO_BYTES = int(os.getenv("PREVIEW_PROXY_MIN_BYTES", str(64 * 1024 * 1024)) or 0)
# Не больше двух ffmpeg — так сказано владельцем; значение настройки выше двух режется.
CONCURRENCY = max(1, min(2, int(os.getenv("PREVIEW_PROXY_CONCURRENCY", "2") or 2)))
THREADS = max(1, int(os.getenv("PREVIEW_PROXY_THREADS", "2") or 2))
TIMEOUT_SEC = int(os.getenv("PREVIEW_PROXY_TIMEOUT_SECONDS", str(6 * 60 * 60)) or 0)

_lock = threading.Lock()
_jobs: dict[int, dict] = {}
_pool: ThreadPoolExecutor | None = None


def proxy_dir() -> Path:
    raw = os.getenv("PREVIEW_PROXY_DIR", "").strip()
    return Path(raw).resolve() if raw else settings.runtime_dir / "proxies"


def proxy_path(source_id: int) -> Path:
    return proxy_dir() / f"source-{int(source_id)}.mp4"


def _meta_path(source_id: int) -> Path:
    return proxy_dir() / f"source-{int(source_id)}.json"


def _part_path(source_id: int) -> Path:
    return proxy_dir() / f"source-{int(source_id)}.part.mp4"


def proxy_url(source_id: int) -> str:
    return f"/media/sources/{int(source_id)}/proxy"


def _read_meta(source_id: int) -> dict:
    try:
        data = json.loads(_meta_path(source_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _audio_key(source: dict) -> str:
    """Смесь дорожек записи, с которой собран прокси: сменилась — прокси устарел."""
    try:
        from app.source_audio import mix_storage, source_mix

        return json.dumps(mix_storage(source_mix(source)) or [], sort_keys=True)
    except Exception:  # noqa: BLE001 - нет файла/ffprobe: сравнивать не с чем
        return "[]"


def _source_key(source: dict) -> dict | None:
    """Отпечаток файла записи (None — файла нет)."""
    raw = str(source.get("local_path") or "")
    if not raw:
        return None
    try:
        size = Path(raw).stat().st_size
    except OSError:
        return None
    return {"size": size, "audio": _audio_key(source), "version": PROXY_VERSION}


def is_canvas(source: dict) -> bool:
    return str(source.get("source_type") or "") == "canvas"


def is_ready(source: dict) -> bool:
    """Готовый прокси, которым можно играть: файл есть (устаревший — тоже, пока новый не собран)."""
    return proxy_path(int(source["id"])).exists()


def media_path(source: dict) -> Path | None:
    """Файл для кадров и волны: запись, а если её удалили — прокси того же таймлайна."""
    raw = str(source.get("local_path") or "")
    if raw and Path(raw).exists():
        return Path(raw)
    fallback = proxy_path(int(source["id"]))
    return fallback if fallback.exists() else None


def status(source: dict) -> dict:
    """``{status, url?, progress, size_bytes, stale?, error?}``: none / not_needed / queued / running / ready / failed."""
    source_id = int(source["id"])
    path = proxy_path(source_id)
    with _lock:
        job = dict(_jobs.get(source_id) or {})
    out: dict[str, Any] = {"status": "none", "progress": 0.0, "url": None}
    if path.exists():
        out.update(status="ready", progress=1.0, url=proxy_url(source_id), size_bytes=path.stat().st_size)
        key = _source_key(source)
        if key is not None and _read_meta(source_id).get("key") != key:
            out["stale"] = True
    if job.get("state") in ("queued", "running"):
        # Пересборка устаревшего: старый файл продолжает играть, а статус честно показывает работу.
        out["rebuilding" if path.exists() else "status"] = job["state"]
        out["progress"] = float(job.get("progress") or 0)
    elif job.get("state") == "failed" and not path.exists():
        out.update(status="failed", error=str(job.get("error") or "")[:300])
    elif not path.exists() and (is_canvas(source) or not wanted(source)):
        out["status"] = "not_needed"
    return out


def wanted(source: dict) -> bool:
    """Нужен ли прокси этой записи сам по себе (без force)."""
    if is_canvas(source):
        return False
    raw = str(source.get("local_path") or "")
    try:
        return bool(raw) and Path(raw).stat().st_size >= MIN_AUTO_BYTES
    except OSError:
        return False


def ensure(source: dict, *, force: bool = False) -> dict:
    """Поставить сборку прокси, если его нет или он устарел; идемпотентно. Возвращает статус."""
    source_id = int(source["id"])
    key = _source_key(source)
    path = proxy_path(source_id)
    if key is None or is_canvas(source):
        return status(source)  # записи нет — собирать не из чего; прокси (если есть) остаётся
    fresh = path.exists() and _read_meta(source_id).get("key") == key
    if fresh or not (force or wanted(source)):
        return status(source)
    with _lock:
        job = _jobs.get(source_id)
        if job and job.get("state") in ("queued", "running"):
            pass
        else:
            _jobs[source_id] = {"state": "queued", "progress": 0.0, "queued_at": time.time()}
            _executor().submit(_build, dict(source), key)
    return status(source)


def _executor() -> ThreadPoolExecutor:
    global _pool
    if _pool is None:
        _pool = ThreadPoolExecutor(max_workers=CONCURRENCY, thread_name_prefix="preview-proxy")
    return _pool


def build_args(source: dict, output: Path) -> list[str]:
    """Команда ffmpeg: короткая сторона 480, ключ раз в секунду, те же секунды, что у записи."""
    from app.source_audio import mix_chain, mix_storage, source_mix

    scale = (
        f"scale='if(gt(a,1),-2,{SHORT_SIDE})':'if(gt(a,1),{SHORT_SIDE},-2)':flags=fast_bilinear,"
        f"fps={MAX_FPS},format=yuv420p"
    )
    args = ["ffmpeg", "-y", "-hide_banner", "-nostats", "-nostdin", "-threads", str(THREADS),
            "-i", str(source["local_path"])]
    tracks = mix_storage(source_mix(source))
    filters = [f"[0:v:0]{scale}[v]"]
    maps = ["-map", "[v]"]
    if tracks:
        filters.append(mix_chain(0, list(tracks), "a"))
        maps += ["-map", "[a]"]
    else:
        maps += ["-map", "0:a:0?"]
    args += ["-filter_complex", ";".join(filters), *maps]
    args += [
        "-c:v", "libx264", "-preset", "veryfast", "-tune", "fastdecode", "-crf", CRF, "-profile:v", "main",
        "-threads", str(THREADS),
        # Ключевой кадр раз в секунду: перемотка к склейке не декодирует длинный GOP.
        "-g", str(MAX_FPS), "-keyint_min", str(MAX_FPS), "-sc_threshold", "0",
        "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ac", "2", "-ar", "48000",
        # Фрагменты по ключевым кадрам и общий индекс в начале файла. Обычный moov на 4-часовую запись
        # весит 15 МБ (замер: 40 с на 3 Мбит/с до первого кадра), этот заголовок — 0.3 МБ.
        "-movflags", "+frag_keyframe+empty_moov+default_base_moof+global_sidx", "-f", "mp4",
        "-progress", "pipe:1",
        str(output),
    ]
    return args


_OUT_TIME = re.compile(r"out_time_(?:ms|us)=(\d+)")


def _build(source: dict, key: dict) -> None:
    source_id = int(source["id"])
    part = _part_path(source_id)
    duration = float(source.get("duration_sec") or 0)
    try:
        proxy_dir().mkdir(parents=True, exist_ok=True)
        part.unlink(missing_ok=True)
        with _lock:
            _jobs[source_id] = {"state": "running", "progress": 0.0, "started_at": time.time()}
        args = build_args(source, part)
        if os.name == "posix" and Path("/usr/bin/nice").exists():
            # Рендер и остальной сервис важнее прокси: он идёт на остатках процессора.
            args = ["/usr/bin/nice", "-n", "15", *args]
        started = time.monotonic()
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        assert proc.stdout is not None
        for line in proc.stdout:
            match = _OUT_TIME.match(line.strip())
            if match and duration > 0:
                # ffmpeg пишет микросекунды в обоих полях (out_time_ms — историческое имя).
                done = min(0.99, int(match.group(1)) / 1_000_000 / duration)
                with _lock:
                    if source_id in _jobs:
                        _jobs[source_id]["progress"] = round(done, 4)
            if TIMEOUT_SEC and time.monotonic() - started > TIMEOUT_SEC:
                proc.kill()
                raise TimeoutError("прокси собирается слишком долго")
        code = proc.wait()
        if code != 0:
            raise RuntimeError(f"ffmpeg завершился с кодом {code}")
        os.replace(part, proxy_path(source_id))
        _meta_path(source_id).write_text(
            json.dumps({"key": key, "built_at": time.time(), "source_id": source_id}), encoding="utf-8"
        )
        with _lock:
            _jobs.pop(source_id, None)
    except Exception as exc:  # noqa: BLE001 - прокси необязателен: предпросмотр играет оригинал
        part.unlink(missing_ok=True)
        with _lock:
            _jobs[source_id] = {"state": "failed", "error": str(exc)[:300]}


def remove_for_deleted_source(source_id: int) -> None:
    """Источник удалён вместе с клипами и монтажом — смотреть больше нечего, файл не держим.

    Удаление одного файла записи сюда не ведёт: прокси остаётся, монтаж можно смотреть и править."""
    for path in (proxy_path(source_id), _meta_path(source_id), _part_path(source_id)):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def active_jobs() -> int:
    """Сколько сборок ждёт или идёт (для проверок)."""
    with _lock:
        return sum(1 for job in _jobs.values() if job.get("state") in ("queued", "running"))
