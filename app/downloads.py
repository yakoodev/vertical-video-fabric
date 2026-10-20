from __future__ import annotations

import threading
from datetime import datetime, timezone
from itertools import count
from urllib.parse import urlsplit

from app.ingest import CancelToken, DownloadCancelled, cancellable, classify_source_url


ACTIVE_STATUSES = {"queued", "downloading", "cancelling"}
KEEP_TERMINAL = 20


def _now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _safe_error(exc: object) -> str:
    text = str(exc or "").strip()
    return text[:500] or "download failed"


def _label_for(url: str, selection: dict | None) -> str:
    """A short human label: the page path plus the picked episode, if any."""
    parts = [part for part in urlsplit(url.strip()).path.split("/") if part]
    tail = parts[-1] if parts else (urlsplit(url.strip()).hostname or url.strip())
    host = (urlsplit(url.strip()).hostname or "").lower()
    label = f"{host}/{tail}" if host else tail
    picked = (selection or {}).get("filename_label") or ""
    return f"{label} · {picked}" if picked else label


class DownloadService:
    """Runs source downloads on background threads so the UI stays responsive.

    Each task is cancellable: the thread holds a CancelToken that reaches the live
    yt-dlp process (or the streaming HTTP read), so «Отменить» actually stops the
    transfer and cleans up the partial file. Tasks live in memory only — a download
    cannot survive a restart anyway, since the process doing it dies with the app.
    """

    def __init__(self, ingestor) -> None:
        self.ingestor = ingestor
        self._tasks: list[dict] = []
        self._tokens: dict[int, CancelToken] = {}
        self._lock = threading.Lock()
        self._ids = count(1)

    def list_tasks(self) -> list[dict]:
        with self._lock:
            return [dict(task) for task in self._tasks]

    def get(self, task_id: int) -> dict:
        with self._lock:
            for task in self._tasks:
                if task["id"] == task_id:
                    return dict(task)
        raise KeyError(f"download {task_id} not found")

    def start(self, url: str, *, quality: str = "", selection: dict | None = None) -> dict:
        url = url.strip()
        # Fail fast on a malformed URL so the user sees it in the response instead
        # of having to open the activity panel for it.
        classify_source_url(url)
        selection = {k: str(v or "").strip() for k, v in (selection or {}).items()}
        task = self._new_task(url=url, quality=quality, label=_label_for(url, selection))
        thread = threading.Thread(
            target=self._run,
            args=(task["id"], url, quality, selection),
            name=f"download-{task['id']}",
            daemon=True,
        )
        thread.start()
        return self.get(task["id"])

    def record_live(self, channel: str) -> dict:
        """● Запись эфира Twitch-канала (app/live_record.py) — когда VOD не скачать.

        Отмена у записи — «остановить и сохранить»: записанное становится источником."""
        from app.live_record import parse_channel

        channel = parse_channel(channel)
        with self._lock:
            busy = any(t.get("live") == channel and t["status"] in ACTIVE_STATUSES for t in self._tasks)
        if busy:
            raise ValueError(f"эфир {channel} уже пишется")
        task = self._new_task(url=f"https://www.twitch.tv/{channel}", quality="", label=f"● эфир {channel}",
                              live=channel)
        threading.Thread(target=self._run_live, args=(task["id"], channel), name=f"live-{task['id']}",
                         daemon=True).start()
        return self.get(task["id"])

    def _run_live(self, task_id: int, channel: str) -> None:
        from app.live_record import record_to_source

        token = CancelToken()
        with self._lock:
            self._tokens[task_id] = token
        self._update(task_id, status="downloading", message="Подключаюсь к эфиру…")
        try:
            source = record_to_source(
                self.ingestor, channel, token,
                on_progress=lambda message: self._update(task_id, message=message),
                on_recorded=self.on_live_recorded,
            )
        except Exception as exc:  # noqa: BLE001 - любая беда — на задачу
            self._update(task_id, status="failed", message="", error=_safe_error(exc))
            return
        finally:
            with self._lock:
                self._tokens.pop(task_id, None)
        self._update(task_id, status="succeeded", progress=100.0, source_id=source.get("id"),
                     message=source.get("original_filename") or "Запись сохранена")

    # Колбэк «эфир записан» (source, vod_id, vod_start_sec): main.py вешает сюда
    # импорт чата архива со сдвигом.
    on_live_recorded = None

    def cancel(self, task_id: int) -> dict:
        with self._lock:
            task = next((t for t in self._tasks if t["id"] == task_id), None)
            if task is None:
                raise KeyError(f"download {task_id} not found")
            if task["status"] not in ACTIVE_STATUSES:
                return dict(task)
            token = self._tokens.get(task_id)
            message = "Останавливаю и сохраняю…" if task.get("live") else "Отменяю…"
            task.update(status="cancelling", message=message, updated_at=_now_text())
            result = dict(task)
        if token:
            token.cancel()
        return result

    def _new_task(self, **fields) -> dict:
        with self._lock:
            task = {
                "id": next(self._ids),
                "status": "queued",
                "progress": 0.0,
                "message": "В очереди",
                "error": "",
                "source_id": None,
                "created_at": _now_text(),
                "updated_at": _now_text(),
            }
            task.update(fields)
            self._tasks.insert(0, task)
            self._prune()
            return dict(task)

    def _update(self, task_id: int, **fields) -> None:
        with self._lock:
            for task in self._tasks:
                if task["id"] == task_id:
                    task.update(fields)
                    task["updated_at"] = _now_text()
                    return

    def _prune(self) -> None:
        """Keep every active task plus the most recent finished ones."""
        terminal = [t for t in self._tasks if t["status"] not in ACTIVE_STATUSES]
        for stale in terminal[KEEP_TERMINAL:]:
            self._tasks.remove(stale)
            self._tokens.pop(stale["id"], None)

    def _run(self, task_id: int, url: str, quality: str, selection: dict) -> None:
        # Percentages come from yt-dlp's own progress lines; keep the last one so a
        # cancel mid-download still shows where it stopped.
        def on_progress(percent: float, message: str) -> None:
            self._update(task_id, progress=round(percent, 1), message=message or f"Скачивание {percent:.0f}%")

        token = CancelToken(on_progress)
        with self._lock:
            self._tokens[task_id] = token
        self._update(task_id, status="downloading", message="Скачивание…")
        try:
            with cancellable(token):
                source = self._ingest(url, quality, selection)
        except DownloadCancelled:
            self._update(task_id, status="cancelled", message="Отменено", progress=0.0)
            return
        except Exception as exc:  # noqa: BLE001 - any failure belongs on the task
            self._update(task_id, status="failed", message="", error=_safe_error(exc))
            return
        finally:
            with self._lock:
                self._tokens.pop(task_id, None)
        if source.get("status") == "failed":
            self._update(
                task_id,
                status="failed",
                message="",
                source_id=source.get("id"),
                error=source.get("error") or "не удалось скачать источник",
            )
            return
        self._update(
            task_id,
            status="succeeded",
            progress=100.0,
            message=source.get("original_filename") or "Готово",
            source_id=source.get("id"),
        )

    def _ingest(self, url: str, quality: str, selection: dict) -> dict:
        if selection.get("media_url"):
            return self.ingestor.ingest_smotvibe_selection(
                url,
                media_url=selection.get("media_url", ""),
                referer=selection.get("referer", ""),
                audio_format_id=selection.get("audio_format_id", ""),
                filename_label=selection.get("filename_label", ""),
                quality=quality,
            )
        return self.ingestor.ingest_url(url, quality=quality)

    def as_tasks(self) -> list[dict]:
        """Shape download tasks like the rows behind /api/tasks."""
        return [
            {
                "kind": "download",
                "id": task["id"],
                "status": task["status"],
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
