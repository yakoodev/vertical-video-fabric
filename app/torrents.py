"""Источник из торрента: magnet или .torrent → список файлов → выбранные видео → обычные источники.

Как загрузки по ссылке (``app/downloads.py``): задачи в памяти (загрузка всё равно не переживёт перезапуск —
вместе с сервисом гаснет и aria2c), каждая в своём потоке, видна в «Активности» (``as_tasks``) и отменяется.
У каждой задачи — свой процесс aria2c (``app/aria2.py``) и свой рабочий каталог ``sources/.torrents/t<id>-…``.

Шаги задачи (``status``):

* ``metadata`` — aria2 получает список файлов (magnet — у пиров по DHT/трекерам, .torrent — сразу);
* ``selecting`` — список готов, ждём выбора (``start``); по умолчанию отмечен самый большой видеофайл.
  ``auto_start`` — начать сразу с ним (или с ``files``);
* ``downloading`` — качаем выбранное: процент, скорость, осталось, пиры;
* ``registering`` — aria2 остановлен, файлы переезжают в каталог источников и становятся источниками
  (один видеофайл — один источник, ``source_type="torrent"``, в metadata_json ключ ``torrent``: имя, info hash);
* ``succeeded`` / ``failed`` / ``cancelled``.

Раздача после загрузки выключена (``seed-time=0``), отдача ограничена (TORRENT_UPLOAD_LIMIT). Потолок выбранного —
TORRENT_MAX_GB, плюс проверка свободного места (``ensure_free_space``). Отмена, сбой и успех одинаково удаляют
рабочий каталог: недокачанное не остаётся лежать гигабайтами.
"""

from __future__ import annotations

import base64
import logging
import os
import shutil
import threading
import time
from datetime import datetime, timezone
from itertools import count
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from uuid import uuid4

from app.ingest import GB, _safe_filename_stem, ensure_free_space
from app.proxy_config import mask_secrets
from app.settings import settings
from app.torrent_meta import (
    TorrentError, TorrentFile, VIDEO_EXTENSIONS, default_selection, magnet_for, parse_magnet, parse_torrent,
)

_log = logging.getLogger(__name__)

ACTIVE_STATUSES = {"metadata", "selecting", "downloading", "registering", "cancelling"}
KEEP_TERMINAL = 20
WORK_SUBDIR = ".torrents"
STATUS_KEYS = ["gid", "status", "totalLength", "completedLength", "uploadLength", "downloadSpeed", "uploadSpeed",
               "connections", "numSeeders", "errorCode", "errorMessage", "followedBy", "infoHash", "bittorrent"]
# Как задача выглядит в общей ленте «Активности» (frontend useActiveTasks: queued/running/downloading — активные).
FEED_STATUS = {"metadata": "running", "selecting": "queued", "registering": "running"}

_SERVICE: Callable[[], "TorrentService"] | None = None


def register(factory: Callable[[], "TorrentService"]) -> None:
    """main.py: откуда инструментам агента (app/torrent_tools.py) брать сервис."""
    global _SERVICE
    _SERVICE = factory


def service() -> "TorrentService":
    if _SERVICE is None:
        raise RuntimeError("торренты недоступны в этом процессе")
    return _SERVICE()


class TorrentCancelled(RuntimeError):
    """Задачу отменили."""


def _now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _gb(size: int) -> str:
    return f"{size / GB:.1f} ГБ"


def human_speed(value: int) -> str:
    if value >= 1024 * 1024:
        return f"{value / 1024 / 1024:.1f} МБ/с"
    if value >= 1024:
        return f"{value / 1024:.0f} КБ/с"
    return f"{value} Б/с"


def human_eta(seconds: int | None) -> str:
    if seconds is None:
        return "—"
    if seconds >= 3600:
        return f"{seconds // 3600} ч {seconds % 3600 // 60} мин"
    if seconds >= 60:
        return f"{seconds // 60} мин"
    return f"{seconds} с"


def parse_progress(status: dict, selected_total: int) -> dict:
    """Ответ ``aria2.tellStatus`` → поля задачи: процент, байты, скорость, ETA, пиры и строка для человека.

    aria2 отдаёт числа строками. С ``select-file`` его totalLength — сумма выбранных файлов; если он ещё 0
    (первые секунды), знаменатель — сумма выбранного по списку файлов."""
    total = _int(status.get("totalLength")) or selected_total
    done = min(_int(status.get("completedLength")), total) if total else _int(status.get("completedLength"))
    speed = _int(status.get("downloadSpeed"))
    peers = _int(status.get("connections"))
    seeders = _int(status.get("numSeeders"))
    percent = round(done * 100 / total, 1) if total else 0.0
    eta = int((total - done) / speed) if speed > 0 and total > done else (0 if total and done >= total else None)
    message = (f"{percent:.0f}% · {human_speed(speed)} · осталось {human_eta(eta)} · пиров {peers}"
               + (f" (раздают {seeders})" if seeders else ""))
    return {
        "progress": percent, "done_bytes": done, "total_bytes": total, "speed": speed,
        "upload_speed": _int(status.get("uploadSpeed")), "eta_sec": eta, "peers": peers, "seeders": seeders,
        "message": message,
    }


def files_from_aria2(entries: list[dict], download_dir: Path) -> list[TorrentFile]:
    """``aria2.getFiles`` → файлы с путями внутри торрента (через /). Путь вне каталога загрузки — отказ."""
    root = download_dir.resolve()
    out: list[TorrentFile] = []
    for entry in entries or []:
        raw = str(entry.get("path") or "")
        if not raw:
            raise TorrentError("aria2 не назвал файлы торрента")
        path = Path(raw)
        if not path.is_absolute():
            path = root / path
        try:
            rel = path.resolve(strict=False).relative_to(root)
        except ValueError as exc:
            raise TorrentError("файл торрента вне каталога загрузки — такой торрент не качаем") from exc
        out.append(TorrentFile(_int(entry.get("index")), PurePosixPath(*rel.parts).as_posix(), _int(entry.get("length"))))
    return out


class _Job:
    """Что нужно потоку задачи и не нужно в её dict: события отмены и старта, aria2, рабочий каталог."""

    def __init__(self, *, kind: str, payload: str, workdir: Path, on_source: Callable[[dict], None] | None) -> None:
        self.kind = kind  # magnet | torrent
        self.payload = payload  # magnet-ссылка или .torrent в base64
        self.workdir = workdir
        self.on_source = on_source
        self.cancel = threading.Event()
        self.start = threading.Event()


class TorrentService:
    def __init__(
        self,
        ingestor,
        *,
        daemon_factory: Callable[[Path], Any] | None = None,
        poll_seconds: float = 1.0,
    ) -> None:
        from app.aria2 import Aria2Daemon

        self.ingestor = ingestor
        self.daemon_factory = daemon_factory or Aria2Daemon
        self.poll_seconds = poll_seconds
        self._tasks: list[dict] = []
        self._jobs: dict[int, _Job] = {}
        self._lock = threading.Lock()
        self._ids = count(1)
        self._swept = False

    # ------------------------------------------------------------------ чтение

    def list_tasks(self) -> list[dict]:
        with self._lock:
            return [self._public(task) for task in self._tasks]

    def get(self, task_id: int) -> dict:
        with self._lock:
            return self._public(self._find(task_id))

    def _find(self, task_id: int) -> dict:
        for task in self._tasks:
            if task["id"] == task_id:
                return task
        raise KeyError(f"торрент {task_id} не найден")

    @staticmethod
    def _public(task: dict) -> dict:
        out = dict(task)
        out["files"] = [dict(f) for f in task.get("files") or []]
        out["selected"] = list(task.get("selected") or [])
        out["source_ids"] = list(task.get("source_ids") or [])
        return out

    def as_tasks(self) -> list[dict]:
        """Задачи в форме строк /api/tasks — для общей ленты «Активности»."""
        return [
            {
                "kind": "torrent",
                "id": task["id"],
                "status": FEED_STATUS.get(task["status"], task["status"]),
                "label": task["label"],
                "error": task["error"],
                "created_at": task["created_at"],
                "updated_at": task["updated_at"],
                "scheduled_at": "",
                "source_id": task["source_id"],
                "detail": task["message"],
                "progress": task["progress"],
            }
            for task in self.list_tasks()
        ]

    # ------------------------------------------------------------------ команды

    def add(
        self,
        *,
        magnet: str = "",
        torrent_bytes: bytes | None = None,
        project_id: int | None = None,
        files: list[int] | None = None,
        auto_start: bool = False,
        on_source: Callable[[dict], None] | None = None,
    ) -> dict:
        """Новая задача: magnet-ссылка или содержимое .torrent. Ошибки ввода — ValueError сразу, до потока."""
        from app import modules

        modules.require("torrents")
        magnet = (magnet or "").strip()
        if bool(magnet) == bool(torrent_bytes):
            raise TorrentError("нужна magnet-ссылка или файл .torrent — что-то одно")
        if magnet:
            info_hash, name = parse_magnet(magnet)
            kind, payload, original = "magnet", magnet, magnet
        else:
            info = parse_torrent(torrent_bytes or b"")
            info_hash, name = info.info_hash, info.name
            kind, payload = "torrent", base64.b64encode(torrent_bytes or b"").decode("ascii")
            original = magnet_for(info_hash, name)
        with self._lock:
            for running in self._tasks:
                if running["info_hash"] == info_hash and running["status"] in ACTIVE_STATUSES:
                    return self._public(running)  # тот же торрент второй раз не качаем
            active = sum(1 for t in self._tasks if t["status"] in ACTIVE_STATUSES)
            if active >= max(1, settings.torrent_max_active):
                raise TorrentError(f"уже идут {active} торрента (предел TORRENT_MAX_ACTIVE) — дождись или отмени один")
        self._sweep_stale_workdirs()
        task_id = next(self._ids)
        task = {
            "id": task_id, "kind": "torrent", "status": "metadata", "progress": 0.0,
            "message": "Получаю список файлов…" if kind == "magnet" else "Читаю .torrent…",
            "error": "", "label": name or f"торрент {info_hash[:8]}", "name": name, "info_hash": info_hash,
            "magnet": original, "input": kind, "files": [], "selected": [], "total_bytes": 0, "done_bytes": 0,
            "speed": 0, "upload_speed": 0, "eta_sec": None, "peers": 0, "seeders": 0,
            "max_bytes": self.max_bytes(), "source_id": None, "source_ids": [], "project_id": project_id,
            "auto_start": bool(auto_start or files), "requested_files": [int(i) for i in files or []],
            "created_at": _now_text(), "updated_at": _now_text(),
        }
        job = _Job(kind=kind, payload=payload,
                   workdir=settings.source_dir / WORK_SUBDIR / f"t{task_id}-{uuid4().hex[:8]}",
                   on_source=on_source or self._assign_to_project(project_id))
        with self._lock:
            self._tasks.insert(0, task)
            self._jobs[task_id] = job
            self._prune()
        threading.Thread(target=self._run, args=(task_id,), name=f"torrent-{task_id}", daemon=True).start()
        return self.get(task_id)

    def start(self, task_id: int, files: list[int] | None = None) -> dict:
        """Выбор файлов и старт загрузки. Проверки — здесь, чтобы отказ пришёл ответом, а не в задаче."""
        with self._lock:
            task = self._find(task_id)
            if task["status"] != "selecting":
                raise TorrentError(_not_selecting(task))
            selected = self._validate_selection(task, files)
            task.update(selected=selected, status="downloading", message="Начинаю загрузку…", updated_at=_now_text())
            job = self._jobs.get(task_id)
            result = self._public(task)
        if job:
            job.start.set()
        return result

    def cancel(self, task_id: int) -> dict:
        with self._lock:
            task = self._find(task_id)
            if task["status"] not in ACTIVE_STATUSES or task["status"] in {"registering", "cancelling"}:
                # Файлы уже переезжают в источники — доделываем, иначе половина источников без файла.
                return self._public(task)
            task.update(status="cancelling", message="Отменяю…", updated_at=_now_text())
            job = self._jobs.get(task_id)
            result = self._public(task)
        if job:
            job.cancel.set()
        return result

    def max_bytes(self) -> int:
        return int(max(0.0, settings.torrent_max_gb) * GB)

    # ------------------------------------------------------------------ проверки

    def _validate_selection(self, task: dict, files: list[int] | None) -> list[int]:
        by_index = {f["index"]: f for f in task["files"]}
        wanted = sorted({int(i) for i in files}) if files else list(task["selected"])
        if not wanted:
            raise TorrentError("не выбрано ни одного файла")
        unknown = [i for i in wanted if i not in by_index]
        if unknown:
            raise TorrentError(f"в торренте нет файлов с номерами {unknown}")
        not_video = [by_index[i]["path"] for i in wanted if not by_index[i]["is_video"]]
        if not_video:
            raise TorrentError("источником станет только видео: " + ", ".join(not_video[:3]))
        total = sum(by_index[i]["size"] for i in wanted)
        limit = self.max_bytes()
        if total > limit:
            raise TorrentError(f"выбрано {_gb(total)} — больше предела {_gb(limit)} (TORRENT_MAX_GB); "
                               "отметь меньше файлов")
        ensure_free_space((total, total), directory=settings.source_dir)
        return wanted

    # ------------------------------------------------------------------ поток задачи

    def _update(self, task_id: int, **fields) -> None:
        with self._lock:
            for task in self._tasks:
                if task["id"] == task_id:
                    # Отмену, нажатую посреди шага, не затираем статусом этого шага.
                    if task["status"] == "cancelling" and fields.get("status") not in (None, "cancelled", "failed"):
                        fields = {k: v for k, v in fields.items() if k not in ("status", "message")}
                    task.update(fields)
                    task["updated_at"] = _now_text()
                    return

    def _snapshot(self, task_id: int) -> dict:
        with self._lock:
            return self._public(self._find(task_id))

    def _run(self, task_id: int) -> None:
        job = self._jobs[task_id]
        daemon = None
        try:
            job.workdir.mkdir(parents=True, exist_ok=True)
            daemon = self.daemon_factory(job.workdir)
            rpc = daemon.start()
            self._check(job)
            gid = self._fetch_metadata(task_id, job, rpc)
            self._offer_files(task_id, job, rpc, gid, Path(getattr(daemon, "download_dir", job.workdir / "data")))
            self._wait_for_selection(task_id, job)
            self._download(task_id, job, rpc, gid)
            daemon.stop()  # файлы закрыты, aria2 больше ничего не пишет
            daemon = None
            self._register(task_id, job)
        except TorrentCancelled:
            self._update(task_id, status="cancelled", message="Отменено", progress=0.0, speed=0, eta_sec=None)
        except Exception as exc:  # noqa: BLE001 - любая ошибка — на задачу
            _log.warning("torrent %s failed: %s", task_id, mask_secrets(exc))
            self._update(task_id, status="failed", message="", speed=0, eta_sec=None, error=_safe_error(exc))
        finally:
            if daemon is not None:
                try:
                    daemon.stop()
                except Exception:  # noqa: BLE001 - остановка — лучшее, что можем
                    _log.warning("torrent %s: aria2 stop failed", task_id, exc_info=True)
            shutil.rmtree(job.workdir, ignore_errors=True)
            with self._lock:
                self._jobs.pop(task_id, None)

    def _check(self, job: _Job) -> None:
        if job.cancel.is_set():
            raise TorrentCancelled()

    def _sleep(self, job: _Job) -> None:
        if job.cancel.wait(self.poll_seconds):
            raise TorrentCancelled()

    def _fetch_metadata(self, task_id: int, job: _Job, rpc) -> str:
        if job.kind == "torrent":
            return rpc.add_torrent(job.payload, {"pause": "true"})
        meta_gid = rpc.add_uri([job.payload])
        deadline = time.monotonic() + settings.torrent_metadata_timeout_sec
        while True:
            self._check(job)
            status = rpc.tell_status(meta_gid, ["status", "followedBy", "connections", "errorMessage"])
            state = status.get("status")
            followed = status.get("followedBy") or []
            if state == "complete" and followed:
                return str(followed[0])
            if state == "error":
                raise TorrentError(f"не удалось получить список файлов: {status.get('errorMessage') or 'ошибка aria2'}")
            if state == "removed":
                raise TorrentCancelled()
            if time.monotonic() > deadline:
                raise TorrentError(f"за {settings.torrent_metadata_timeout_sec:.0f} с не нашлось пиров со списком файлов "
                                   "— раздача мёртвая или ссылка неверная; попробуй файл .torrent")
            self._update(task_id, message=f"Получаю список файлов… пиров {_int(status.get('connections'))}")
            self._sleep(job)

    def _offer_files(self, task_id: int, job: _Job, rpc, gid: str, download_dir: Path) -> None:
        files = files_from_aria2(rpc.get_files(gid), download_dir)
        if not files:
            raise TorrentError("в торренте нет файлов")
        status = rpc.tell_status(gid, ["infoHash", "bittorrent"])
        name = str(((status.get("bittorrent") or {}).get("info") or {}).get("name") or "").strip()
        rows = [{"index": f.index, "path": f.path, "size": f.size, "is_video": f.is_video} for f in files]
        default = default_selection(files)
        task = self._snapshot(task_id)
        fields: dict[str, Any] = {
            "gid": gid, "files": rows, "selected": default, "total_bytes": sum(f.size for f in files if f.index in default),
            "status": "selecting",
            "message": "Выбери файлы" if default else "В торренте нет видео, которое можно взять источником",
        }
        if name:
            fields.update(name=name, label=name)
        if status.get("infoHash"):
            fields["info_hash"] = str(status["infoHash"]).lower()
        self._update(task_id, **fields)
        if not default and not task["requested_files"]:
            raise TorrentError("в торренте нет видеофайлов (" + ", ".join(sorted(VIDEO_EXTENSIONS)[:6]) + "…)")
        if task["auto_start"]:
            try:
                self.start(task_id, task["requested_files"] or None)
            except TorrentError as exc:
                raise TorrentError(f"не начал загрузку: {exc}") from exc

    def _wait_for_selection(self, task_id: int, job: _Job) -> None:
        deadline = time.monotonic() + settings.torrent_select_timeout_sec
        while not job.start.is_set():
            self._check(job)
            if time.monotonic() > deadline:
                raise TorrentError("файлы так и не выбрали — задача закрыта, добавь торрент заново")
            if job.start.wait(min(self.poll_seconds, 1.0)):
                break
        self._check(job)

    def _download(self, task_id: int, job: _Job, rpc, gid: str) -> None:
        task = self._snapshot(task_id)
        selected = task["selected"]
        selected_total = sum(f["size"] for f in task["files"] if f["index"] in selected)
        rpc.change_option(gid, {"select-file": ",".join(str(i) for i in selected)})
        rpc.unpause(gid)
        self._update(task_id, total_bytes=selected_total, message="Подключаюсь к пирам…")
        last_done, last_change = -1, time.monotonic()
        while True:
            self._sleep(job)
            status = rpc.tell_status(gid, STATUS_KEYS)
            state = status.get("status")
            if state == "error":
                raise TorrentError(f"aria2: {status.get('errorMessage') or 'ошибка загрузки'}")
            if state == "removed":
                raise TorrentCancelled()
            progress = parse_progress(status, selected_total)
            if state == "complete":
                self._update(task_id, **{**progress, "progress": 100.0, "speed": 0, "eta_sec": 0,
                                         "message": "Скачано, переношу в источники…"})
                return
            self._update(task_id, **progress)
            if progress["done_bytes"] > last_done:
                last_done, last_change = progress["done_bytes"], time.monotonic()
            elif time.monotonic() - last_change > settings.torrent_stall_timeout_sec:
                raise TorrentError(f"{settings.torrent_stall_timeout_sec / 60:.0f} мин ни байта — раздающих нет")

    def _enter_registering(self, task_id: int, job: _Job) -> dict:
        """Последняя точка отмены: дальше файлы становятся источниками и отмена — уже не отмена, а потеря."""
        with self._lock:
            task = self._find(task_id)
            if job.cancel.is_set() or task["status"] == "cancelling":
                raise TorrentCancelled()
            task.update(status="registering", message="Переношу файлы в источники…", updated_at=_now_text())
            return self._public(task)

    def _register(self, task_id: int, job: _Job) -> None:
        task = self._enter_registering(task_id, job)
        download_dir = (job.workdir / "data").resolve()
        by_index = {f["index"]: f for f in task["files"]}
        sources: list[dict] = []
        failures: list[str] = []
        chosen = task["selected"]
        for number, index in enumerate(chosen, start=1):
            entry = by_index[index]
            self._update(task_id, message=f"Проверяю файл {number} из {len(chosen)}: {PurePosixPath(entry['path']).name}")
            src = (download_dir / entry["path"]).resolve(strict=False)
            try:
                src.relative_to(download_dir)
            except ValueError:
                failures.append(f"{entry['path']}: вне каталога загрузки")
                continue
            if not src.is_file():
                failures.append(f"{entry['path']}: aria2 не оставил файла")
                continue
            name = PurePosixPath(entry["path"]).name
            stem = _safe_filename_stem(PurePosixPath(name).stem) or "torrent"
            dest = settings.source_dir / f"{uuid4().hex}-{stem}{PurePosixPath(name).suffix.lower()}"
            os.replace(src, dest)  # тот же диск: переезд, не копия
            meta = {"torrent": {"name": task["name"], "info_hash": task["info_hash"], "file_path": entry["path"],
                                "file_index": index, "magnet": task["magnet"], "input": task["input"]}}
            try:
                source = self.ingestor.ingest_local_file(
                    dest, source_type="torrent", original_url=task["magnet"], original_filename=name,
                    extra_metadata=meta, max_bytes=self.max_bytes(),
                )
            except Exception as exc:  # noqa: BLE001 - один битый файл не отменяет остальные
                dest.unlink(missing_ok=True)
                failures.append(f"{entry['path']}: {_safe_error(exc)}")
                continue
            sources.append(source)
            if job.on_source:
                try:
                    job.on_source(source)
                except Exception:  # noqa: BLE001 - источник есть и без проекта: виден во «Всех видео»
                    _log.warning("torrent %s: on_source failed", task_id, exc_info=True)
        ids = [int(s["id"]) for s in sources]
        ready = [s for s in sources if s.get("status") != "failed"]
        if not ids:
            raise TorrentError("ни один файл не стал источником: " + "; ".join(failures)[:400])
        bad = [f"{s.get('original_filename')}: {s.get('error')}" for s in sources if s.get("status") == "failed"]
        problems = failures + bad
        self._update(
            task_id, status="succeeded" if ready else "failed", progress=100.0, speed=0, eta_sec=0,
            source_id=ids[0], source_ids=ids,
            message=f"Готово: источников {len(ids)}" if len(ids) > 1 else (sources[0].get("original_filename") or "Готово"),
            error="; ".join(problems)[:500],
        )

    # ------------------------------------------------------------------ уборка

    def _assign_to_project(self, project_id: int | None):
        if project_id is None:
            return None
        store = getattr(self.ingestor, "store", None)

        def assign(source: dict) -> None:
            from app import projects as P

            P.set_source_project(store, int(source["id"]), int(project_id))

        return assign if store is not None else None

    def _prune(self) -> None:
        terminal = [t for t in self._tasks if t["status"] not in ACTIVE_STATUSES]
        for stale in terminal[KEEP_TERMINAL:]:
            self._tasks.remove(stale)

    def _sweep_stale_workdirs(self) -> None:
        """Каталоги прошлого запуска сервиса: их aria2 погас вместе с ним, докачать нельзя — место освобождаем."""
        if self._swept:
            return
        self._swept = True
        root = settings.source_dir / WORK_SUBDIR
        if not root.is_dir():
            return
        with self._lock:
            mine = {job.workdir.name for job in self._jobs.values()}
        for child in root.iterdir():
            if child.is_dir() and child.name not in mine:
                shutil.rmtree(child, ignore_errors=True)


def _not_selecting(task: dict) -> str:
    status = task["status"]
    if status == "metadata":
        return "список файлов ещё не получен — подожди"
    if status in {"downloading", "registering"}:
        return "загрузка уже идёт"
    return f"задача уже закрыта ({status})"


def _safe_error(exc: object) -> str:
    text = mask_secrets(exc).strip()
    return text[:500] or "торрент не скачался"
