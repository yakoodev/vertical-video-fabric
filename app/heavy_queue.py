"""Очередь тяжёлых задач процесса: рендер, распознавание речи (Whisper), автофокус.

Контейнер один раз упал по памяти (лимит 6 ГБ) на очереди рендеров пачки апекс-тиктоков,
и ещё раз перезапустился, когда параллельно шли generate_subtitles и auto_focus. Каждый
рендер держит ffmpeg-кодировщик основы, Node-компоновщик (холст 1080×1920, декодер и
кодировщик внутри) и кадры видео-слоёв; Whisper large — ещё 1–1.5 ГБ модели и буферов.
Запросы API идут потоками FastAPI — несколько агентов сразу запускали всё это параллельно.

Теперь такие задачи идут по очереди: ``with heavy_slot("render", "клип 12"):`` ждёт, пока
освободится место (по умолчанию одно, ``HEAVY_JOBS_CONCURRENCY``; 0 — без очереди), в
порядке прихода. Вложенный вызов в том же потоке (рендер распознаёт субтитры) места не
ждёт — иначе очередь в одно место заперла бы сама себя. Кадр для агента (view_frame) в
эту очередь не встаёт (ждать минутный рендер ради картинки незачем) — у кадров своя,
пошире (``frame_slot``, ``FRAME_JOBS_CONCURRENCY``, по умолчанию 2).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

log = logging.getLogger(__name__)


class HeavyQueue:
    """FIFO-очередь с ``limit()`` мест; ``slot(kind, label)`` — контекстный менеджер."""

    def __init__(self, limit: Any = None) -> None:
        self._limit = limit
        self._cond = threading.Condition()
        self._local = threading.local()
        self._waiting: list[int] = []
        self._active: dict[int, dict] = {}
        self._tickets = 0

    def limit(self) -> int:
        """Сколько задач сразу; 0 — без ограничения."""
        if callable(self._limit):
            value = self._limit()
        elif self._limit is not None:
            value = self._limit
        else:
            from app.settings import settings

            value = settings.heavy_jobs_concurrency
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 1

    def slot(self, kind: str, label: str = "") -> "_Slot":
        return _Slot(self, kind, label)

    def _enter(self, kind: str, label: str) -> int | None:
        depth = getattr(self._local, "depth", 0)
        if depth:
            self._local.depth = depth + 1
            return None
        limit = self.limit()
        with self._cond:
            self._tickets += 1
            ticket = self._tickets
            self._waiting.append(ticket)
            started = time.monotonic()
            try:
                while self._waiting[0] != ticket or (limit and len(self._active) >= limit):
                    self._cond.wait(timeout=5.0)
                    limit = self.limit()
            except BaseException:
                self._waiting.remove(ticket)  # прерванное ожидание не держит очередь
                self._cond.notify_all()
                raise
            self._waiting.pop(0)
            self._active[ticket] = {"kind": kind, "label": label, "since": time.time()}
            # Следующий в очереди может войти, если мест больше одного.
            self._cond.notify_all()
        waited = time.monotonic() - started
        if waited > 1.0:
            log.info("heavy queue: %s %s ждал %.1f с", kind, label, waited)
        self._local.depth = 1
        return ticket

    def _exit(self, ticket: int | None) -> None:
        self._local.depth = max(0, getattr(self._local, "depth", 1) - 1)
        if ticket is None:
            return
        with self._cond:
            self._active.pop(ticket, None)
            self._cond.notify_all()

    def status(self) -> dict:
        """Что идёт и сколько ждёт (для отладки и ответа агенту)."""
        with self._cond:
            return {
                "limit": self.limit(),
                "running": [dict(v) for v in self._active.values()],
                "waiting": len(self._waiting),
            }


class _Slot:
    def __init__(self, queue: HeavyQueue, kind: str, label: str) -> None:
        self.queue, self.kind, self.label = queue, kind, label
        self.ticket: int | None = None

    def __enter__(self) -> "_Slot":
        self.ticket = self.queue._enter(self.kind, self.label)
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.queue._exit(self.ticket)


HEAVY = HeavyQueue()


def _frame_limit() -> int:
    from app.settings import settings

    return settings.frame_jobs_concurrency


# Кадры для агента (view_frame, contact_sheet, обложка): своя очередь пошире — каждый кадр
# поднимает Node-компоновщик (~0.3 ГБ), четыре агента сразу — больше гигабайта.
FRAMES = HeavyQueue(_frame_limit)


def heavy_slot(kind: str, label: str = "") -> _Slot:
    """Место в очереди тяжёлых задач процесса (``with heavy_slot("render", "клип 5"): ...``)."""
    return HEAVY.slot(kind, label)


def frame_slot(label: str = "") -> _Slot:
    """Место в очереди кадров (отдельной от рендера: кадр не ждёт минутный рендер)."""
    return FRAMES.slot("frame", label)


def heavy(kind: str):
    """Декоратор: вся функция — в очереди тяжёлых задач."""
    import functools

    def wrap(fn):
        @functools.wraps(fn)
        def inner(*args, **kwargs):
            with heavy_slot(kind, fn.__name__):
                return fn(*args, **kwargs)

        return inner

    return wrap
