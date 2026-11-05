"""Лёгкие копии для предпросмотра монтажа: куски по требованию и общий прокси 480p.

Запись стрима весит 10+ ГБ, а один только её заголовок — 31 МБ: 88 с на 3 Мбит/с до первого кадра.
Общий прокси это лечит, но собирается линейно: до середины 4-часового стрима ему идти полчаса.
Поэтому первым делом собираются **куски**: короткий файл 480p на каждую часть клипа, с быстрым
поиском по записи (10 с — меньше секунды работы). Кэш по источнику, началу, концу и смеси дорожек:
правка границ пересобирает только то, что изменилось, а кусок, целиком накрывающий нужный отрезок,
годится и так. Общий прокси — фоновая работа ради будущего: стоит на паузе, пока есть куски.

* Больше двух ffmpeg не бывает никогда: два рабочих потока, у каждого один процесс. Общий прокси
  занимает только первый поток (второй всегда свободен для кусков), куски идут вперёд него.
* Оригинал не меняется и не удаляется. Файлы лежат в ``runtime/proxies`` (в проде — том на D:).
* Запись нескольких дорожек (игра/микрофон) смешивается как у рендера (``source_audio.mix_chain``):
  браузер сам играет только первую дорожку. Сменили смесь — копии пересоберутся.
* Файл записи удалили, а копии остались — монтаж можно смотреть и править (рендера с них нет).
  Удаляются они только вместе с самим источником (``store.delete_source``: клипов тогда уже нет).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from app.settings import settings

PROXY_VERSION = 2
PIECE_VERSION = 1
SHORT_SIDE = 480
MAX_FPS = 30
CRF = "28"
AUDIO_BITRATE = "96k"
# Запись меньше этого легко играет и так: копии сами не собираем (можно с force).
MIN_AUTO_BYTES = int(os.getenv("PREVIEW_PROXY_MIN_BYTES", str(64 * 1024 * 1024)) or 0)
# Владелец: не больше двух ffmpeg всего. Поэтому это не настройка: два потока — два процесса.
MAX_FFMPEG = 2
THREADS = max(1, int(os.getenv("PREVIEW_PROXY_THREADS", "2") or 2))
TIMEOUT_SEC = int(os.getenv("PREVIEW_PROXY_TIMEOUT_SECONDS", str(6 * 60 * 60)) or 0)
PIECE_TIMEOUT_SEC = 180
PIECE_THREADS = max(1, int(os.getenv("PREVIEW_PIECE_THREADS", "4") or 4))
# Длинная часть режется по сетке записи: 2,5-минутная часть с записи 1080p120 иначе собиралась бы минуту
# до первого кадра (замер, клип 824). Сетка общая: подрезка края не трогает остальные куски.
CHUNK_SEC = 8.0
LONG_PART_SEC = 16.0
# Запас хвоста куска: плеер переключается на следующий кусок чуть позже конца отрезка.
PIECE_TAIL_SEC = 0.15
# Предел кэша кусков на все источники: сверх него уходят самые старые чужие.
PIECES_MAX_BYTES = int(os.getenv("PREVIEW_PIECES_MAX_BYTES", str(4 * 1024 ** 3)) or 0)

_lock = threading.Lock()
_wake = threading.Condition(_lock)
_jobs: dict[int, dict] = {}  # общий прокси: source_id → состояние
_proxy_queue: list[tuple[dict, dict]] = []
_pieces: dict[str, dict] = {}  # кусок: «источник/имя файла» → задача
_workers: list[threading.Thread] = []
_proxy_proc: subprocess.Popen | None = None
_proxy_paused = False
_stream_active = False  # идёт сборка трансляции (app/stream_preview.py): её ffmpeg вместо этих
_NAME = re.compile(r"^(\d{10})-(\d{10})-([0-9a-f]{8})v(\d+)\.mp4$")


def proxy_dir() -> Path:
    raw = os.getenv("PREVIEW_PROXY_DIR", "").strip()
    return Path(raw).resolve() if raw else settings.runtime_dir / "proxies"


def proxy_path(source_id: int) -> Path:
    return proxy_dir() / f"source-{int(source_id)}.mp4"


def _meta_path(source_id: int) -> Path:
    return proxy_dir() / f"source-{int(source_id)}.json"


def _part_path(source_id: int) -> Path:
    return proxy_dir() / f"source-{int(source_id)}.part.mp4"


def pieces_dir(source_id: int) -> Path:
    return proxy_dir() / "pieces" / f"source-{int(source_id)}"


def proxy_url(source_id: int) -> str:
    return f"/media/sources/{int(source_id)}/proxy"


def _read_meta(source_id: int) -> dict:
    try:
        data = json.loads(_meta_path(source_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _audio_key(source: dict) -> str:
    """Смесь дорожек записи, с которой собрана копия: сменилась — копия устарела."""
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
    """Нужны ли лёгкие копии этой записи сами по себе (без force)."""
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
    with _wake:
        job = _jobs.get(source_id)
        if not (job and job.get("state") in ("queued", "running")):
            _jobs[source_id] = {"state": "queued", "progress": 0.0, "queued_at": time.time()}
            _proxy_queue.append((dict(source), key))
            _start_workers()
            _wake.notify_all()
    return status(source)


# ---------------------------------------------------------------- рабочие: не больше двух ffmpeg


def _start_workers() -> None:
    """Под замком: два рабочих потока на всё. Первый может взять прокси, второй — только куски."""
    while len(_workers) < MAX_FFMPEG:
        index = len(_workers)
        thread = threading.Thread(target=_worker, args=(index,), daemon=True, name=f"preview-ffmpeg-{index}")
        _workers.append(thread)
        thread.start()


def _next_piece() -> dict | None:
    if _stream_active:
        return None
    queued = [p for p in _pieces.values() if p["state"] == "queued"]
    return min(queued, key=lambda p: p["priority"]) if queued else None


def _worker(index: int) -> None:
    while True:
        piece = proxy_job = None
        with _wake:
            while True:
                piece = _next_piece()
                if piece is not None:
                    piece["state"] = "running"
                    _sync_proxy_pause()
                    break
                if index == 0 and _proxy_queue and not _stream_active:
                    proxy_job = _proxy_queue.pop(0)
                    break
                _wake.wait()
        try:
            if piece is not None:
                _build_piece(piece)
            elif proxy_job is not None:
                _build(*proxy_job)
        except Exception:  # noqa: BLE001 - рабочий поток не должен умереть
            pass


def _pieces_active() -> bool:
    return any(p["state"] in ("queued", "running") for p in _pieces.values())


def _suspend(proc: subprocess.Popen, on: bool) -> None:
    """Заморозить или продолжить процесс: процессор и диск уходят кускам, прогресс прокси не теряется."""
    try:
        if os.name == "posix":
            os.kill(proc.pid, signal.SIGSTOP if on else signal.SIGCONT)
        else:
            import ctypes

            ntdll = ctypes.windll.ntdll  # type: ignore[attr-defined]
            (ntdll.NtSuspendProcess if on else ntdll.NtResumeProcess)(int(proc._handle))  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - не смогли — значит прокси просто идёт дальше рядом с кусками
        pass


def _sync_proxy_pause() -> None:
    """Под замком: общий прокси стоит, пока собираются куски."""
    global _proxy_paused
    if _proxy_proc is None or _proxy_proc.poll() is not None:
        _proxy_paused = False
        return
    want = _pieces_active() or _stream_active
    if want != _proxy_paused:
        _suspend(_proxy_proc, want)
        _proxy_paused = want


def stream_busy(on: bool) -> None:
    """Трансляция занимает ffmpeg: общий прокси заморожен, новые куски ждут — всего не больше двух ffmpeg."""
    global _stream_active
    with _wake:
        if _stream_active == on:
            return
        _stream_active = on
        _sync_proxy_pause()
        if not on:
            _wake.notify_all()


def build_args(source: dict, output: Path) -> list[str]:
    """Команда ffmpeg: короткая сторона 480, ключ раз в секунду, те же секунды, что у записи."""
    return _encode_args(source, ["-i", str(source["local_path"])], output, fragmented=True)


def chunk_ranges(start: float, end: float, reverse: bool = False) -> list[tuple[float, float]]:
    """Куски части записи: короткая — один, длинная — по сетке ``CHUNK_SEC``, без обрезков короче половины шага."""
    start, end = float(start), float(end)
    if reverse or end - start <= LONG_PART_SEC:
        return [(start, end)]
    edges = [start]
    k = int(start // CHUNK_SEC) + 1
    while k * CHUNK_SEC < end:
        edge = k * CHUNK_SEC
        if edge - edges[-1] >= CHUNK_SEC / 2 and end - edge >= CHUNK_SEC / 2:
            edges.append(edge)
        k += 1
    edges.append(end)
    return list(zip(edges, edges[1:]))


def _encode_args(source: dict, input_args: list[str], output: Path, *, fragmented: bool, progress: bool = True, threads: int = THREADS) -> list[str]:
    from app.source_audio import mix_chain, mix_storage, source_mix

    scale = (
        f"scale='if(gt(a,1),-2,{SHORT_SIDE})':'if(gt(a,1),{SHORT_SIDE},-2)':flags=fast_bilinear,"
        f"fps={MAX_FPS},format=yuv420p"
    )
    args = ["ffmpeg", "-y", "-hide_banner", "-nostats", "-nostdin", "-threads", str(threads), *input_args]
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
        "-threads", str(threads),
        # Ключевой кадр раз в секунду: перемотка не декодирует длинный GOP.
        "-g", str(MAX_FPS), "-keyint_min", str(MAX_FPS), "-sc_threshold", "0",
        "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ac", "2", "-ar", "48000",
        # Общий прокси: фрагменты по ключевым кадрам и общий индекс в начале файла. Обычный moov на
        # 4-часовую запись весит 15 МБ (замер: 40 с на 3 Мбит/с до первого кадра), этот заголовок — 0.3 МБ.
        "-movflags", "+frag_keyframe+empty_moov+default_base_moof+global_sidx" if fragmented else "+faststart",
        "-f", "mp4",
        *(["-progress", "pipe:1"] if progress else []),
        str(output),
    ]
    return args


_OUT_TIME = re.compile(r"out_time_(?:ms|us)=(\d+)")


def _nice(args: list[str]) -> list[str]:
    if os.name == "posix" and Path("/usr/bin/nice").exists():
        # Рендер и остальной сервис важнее: копии идут на остатках процессора.
        return ["/usr/bin/nice", "-n", "15", *args]
    return args


def _build(source: dict, key: dict) -> None:
    global _proxy_proc, _proxy_paused
    source_id = int(source["id"])
    part = _part_path(source_id)
    duration = float(source.get("duration_sec") or 0)
    try:
        proxy_dir().mkdir(parents=True, exist_ok=True)
        part.unlink(missing_ok=True)
        with _lock:
            _jobs[source_id] = {"state": "running", "progress": 0.0, "started_at": time.time()}
        started = time.monotonic()
        proc = subprocess.Popen(_nice(build_args(source, part)), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        with _lock:
            _proxy_proc = proc
            _proxy_paused = False
            _sync_proxy_pause()
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
    except Exception as exc:  # noqa: BLE001 - прокси необязателен: предпросмотр идёт с кусков
        part.unlink(missing_ok=True)
        with _lock:
            _jobs[source_id] = {"state": "failed", "error": str(exc)[:300]}
    finally:
        with _lock:
            _proxy_proc = None
            _proxy_paused = False


# ---------------------------------------------------------------- куски по требованию


def _audio_tag(source: dict) -> str:
    return hashlib.sha1(_audio_key(source).encode("utf-8")).hexdigest()[:8]


def piece_name(source: dict, start: float, end: float) -> str:
    return f"{round(float(start) * 1000):010d}-{round(float(end) * 1000):010d}-{_audio_tag(source)}v{PIECE_VERSION}.mp4"


def piece_url(source_id: int, name: str) -> str:
    return f"/media/sources/{int(source_id)}/pieces/{name}"


def piece_file(source_id: int, name: str) -> Path | None:
    """Файл куска по имени из URL (имя проверяется: наружу из папки не выйти)."""
    if not _NAME.match(name or ""):
        return None
    path = pieces_dir(source_id) / name
    return path if path.is_file() else None


def _find_piece(source: dict, start: float, end: float) -> tuple[str, float] | None:
    """Готовый кусок, накрывающий отрезок: тот же или шире (подрезали край — пересборка не нужна)."""
    folder = pieces_dir(int(source["id"]))
    tag = _audio_tag(source)
    a, b = round(float(start) * 1000), round(float(end) * 1000)
    best: tuple[int, str, float] | None = None
    try:
        names = os.listdir(folder)
    except OSError:
        return None
    for name in names:
        match = _NAME.match(name)
        if not match or match.group(3) != tag or int(match.group(4)) != PIECE_VERSION:
            continue
        lo, hi = int(match.group(1)), int(match.group(2))
        if lo <= a and hi >= b and (best is None or hi - lo < best[0]):
            best = (hi - lo, name, lo / 1000)
    return (best[1], best[2]) if best else None


def pieces_status(source: dict, ranges: list[tuple[float, float]]) -> dict:
    """Что с кусками клипа: ``{total, ready, failed, parts: [{index, start, end, status, url?, offset?, error?}]}``."""
    source_id = int(source["id"])
    parts: list[dict] = []
    ready = failed = 0
    for index, (start, end) in enumerate(ranges):
        row: dict[str, Any] = {"index": index, "start": start, "end": end}
        found = _find_piece(source, start, end)
        if found:
            row.update(status="ready", url=piece_url(source_id, found[0]), offset=found[1])
            ready += 1
        else:
            name = piece_name(source, start, end)
            with _lock:
                job = _pieces.get(f"{source_id}/{name}")
            state = job["state"] if job else "none"
            row["status"] = state
            if state == "failed":
                row["error"] = job.get("error") or "кусок не собрался"
                failed += 1
        parts.append(row)
    return {"total": len(ranges), "ready": ready, "failed": failed, "parts": parts}


def ensure_pieces(source: dict, ranges: list[tuple[float, float]], focus: int = 0, retry: bool = False) -> dict:
    """Поставить недостающие куски: от ``focus`` вперёд, потом те, что раньше него; идемпотентно."""
    key = _source_key(source)
    if key is None:
        return pieces_status(source, ranges)  # записи нет — собирать не из чего, готовые куски годятся
    n = max(1, len(ranges))
    focus = max(0, min(int(focus or 0), n - 1))
    with _wake:
        changed = False
        for index, (start, end) in enumerate(ranges):
            if _find_piece(source, start, end):
                continue
            name = piece_name(source, start, end)
            order = (index - focus) % n
            job_key = f"{int(source['id'])}/{name}"
            job = _pieces.get(job_key)
            if job is None or (retry and job["state"] == "failed"):
                _pieces[job_key] = {"key": job_key, "name": name, "source": dict(source), "start": float(start), "end": float(end),
                                    "state": "queued", "priority": order}
                changed = True
            elif job["state"] == "queued":
                job["priority"] = order
        if changed:
            _sync_proxy_pause()
            _start_workers()
            _wake.notify_all()
    return pieces_status(source, ranges)


def piece_args(source: dict, start: float, end: float, output: Path) -> list[str]:
    """Кусок: ``-ss`` до ``-i`` — быстрый поиск по записи, файл начинается ровно на ``start``."""
    length = max(0.05, float(end) - float(start)) + PIECE_TAIL_SEC
    args = _encode_args(source, ["-ss", f"{float(start):.3f}", "-t", f"{length:.3f}", "-i", str(source["local_path"])],
                        output, fragmented=False, progress=False, threads=PIECE_THREADS)
    return args


def _build_piece(job: dict) -> None:
    source = job["source"]
    folder = pieces_dir(int(source["id"]))
    part = folder / f"{job['name']}.{os.getpid()}.part.mp4"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        _prune_pieces(int(source["id"]))
        subprocess.run(_nice(piece_args(source, job["start"], job["end"], part)), check=True, capture_output=True, timeout=PIECE_TIMEOUT_SEC)
        os.replace(part, folder / job["name"])
        with _lock:
            _pieces.pop(job["key"], None)
            _sync_proxy_pause()
    except Exception as exc:  # noqa: BLE001 - показываем в плеере: «кусок не собрался», кнопка повтора
        part.unlink(missing_ok=True)
        detail = exc.stderr.decode("utf-8", "replace")[-200:] if isinstance(exc, subprocess.CalledProcessError) and exc.stderr else str(exc)
        with _lock:
            job["state"] = "failed"
            job["error"] = detail[:300]
            _sync_proxy_pause()


def _prune_pieces(keep_source: int) -> None:
    """Кэш кусков не растёт без предела: сверх него уходят самые старые куски других источников."""
    root = proxy_dir() / "pieces"
    if not PIECES_MAX_BYTES or not root.is_dir():
        return
    files = []
    total = 0
    for folder in root.iterdir():
        if not folder.is_dir():
            continue
        for path in folder.iterdir():
            if not _NAME.match(path.name):
                continue
            stat = path.stat()
            total += stat.st_size
            if folder.name != f"source-{keep_source}":
                files.append((stat.st_mtime, stat.st_size, path))
    for _mtime, size, path in sorted(files, key=lambda row: row[0]):
        if total <= PIECES_MAX_BYTES:
            break
        path.unlink(missing_ok=True)
        total -= size


def remove_for_deleted_source(source_id: int) -> None:
    """Источник удалён вместе с клипами и монтажом — смотреть больше нечего, файлы не держим.

    Удаление одного файла записи сюда не ведёт: копии остаются, монтаж можно смотреть и править."""
    for path in (proxy_path(source_id), _meta_path(source_id), _part_path(source_id)):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    shutil.rmtree(pieces_dir(source_id), ignore_errors=True)


def active_jobs() -> int:
    """Сколько сборок ждёт или идёт (для проверок)."""
    with _lock:
        return sum(1 for job in _jobs.values() if job.get("state") in ("queued", "running")) + sum(
            1 for p in _pieces.values() if p["state"] in ("queued", "running"))
