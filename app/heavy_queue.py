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

Память (app/memory.py): после каждой тяжёлой задачи — gc и ``malloc_trim`` (освобождённое
задачей возвращается системе, а не оседает в кучах glibc) и строка в лог с RSS процесса до
задачи, после неё и после очистки. Перед задачей другого рода выгружается то, что держит
прошлый род (``release_before_other``: модель Whisper не лежит рядом с рендером).

Сторож памяти (app/memory_guard.py, только у ``HEAVY``): задача, получив место, сначала ждёт, пока у
машины и контейнера хватит памяти на неё и запас (ПК общий — рядом чужая модель голоса на 6–9 ГБ).
Пока ждёт — ``status()["memory_wait"]`` и колбэк потока ``on_wait`` («жду память: свободно X ГБ,
нужно ~Y»): задание рендера показывает это в ``render_status``.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

log = logging.getLogger(__name__)

# Род задачи → «отпусти память» (модель Whisper): зовётся перед задачей ДРУГОГО рода.
_release_before_other: dict[str, Callable[[], None]] = {}


def release_before_other(kind: str, release: Callable[[], None]) -> None:
    """``release`` вызовется перед каждой тяжёлой задачей не рода ``kind`` (выгрузить модель)."""
    _release_before_other[kind] = release


class HeavyQueue:
    """FIFO-очередь с ``limit()`` мест; ``slot(kind, label)`` — контекстный менеджер."""

    def __init__(self, limit: Any = None, *, track_memory: bool = False, mem_guard: bool = False) -> None:
        self._limit = limit
        self._track_memory = track_memory
        self._mem_guard = mem_guard
        self._mem_wait: dict[int, dict] = {}
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

    def slot(self, kind: str, label: str = "", need_mb: float | None = None) -> "_Slot":
        return _Slot(self, kind, label, need_mb)

    def _enter(self, kind: str, label: str, need_mb: float | None = None) -> int | None:
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
                told = False
                while self._waiting[0] != ticket or (limit and len(self._active) >= limit):
                    if not told:
                        _notify(_queue_note(self._active.values(), self._waiting.index(ticket)))
                        told = True
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
        if self._track_memory:
            self._before_task(ticket, kind)
        if self._mem_guard:
            try:
                self._wait_memory(ticket, kind, label, need_mb)
            except BaseException:
                self._exit(ticket)  # не дождались памяти — место отдаём, задачу не начинаем
                raise
        _notify(None)
        return ticket

    def _wait_memory(self, ticket: int, kind: str, label: str, need_mb: float | None) -> None:
        from app import memory_guard

        def waiting(info: dict) -> None:
            with self._cond:
                self._mem_wait[ticket] = {"kind": kind, "label": label, **info}
            _notify(info.get("text") or "жду память")

        try:
            memory_guard.wait_for_memory(kind, label, need_mb, on_wait=waiting)
        finally:
            with self._cond:
                self._mem_wait.pop(ticket, None)

    def _exit(self, ticket: int | None) -> None:
        self._local.depth = max(0, getattr(self._local, "depth", 1) - 1)
        if ticket is None:
            return
        if self._track_memory:
            # Очистка — пока место занято: следующая задача стартует с отданной памятью.
            self._after_task(ticket)
        with self._cond:
            self._active.pop(ticket, None)
            self._cond.notify_all()

    def _before_task(self, ticket: int, kind: str) -> None:
        from app import memory

        try:
            for owner, release in list(_release_before_other.items()):
                if owner != kind:
                    release()
            rss = memory.rss_mb()
        except Exception:  # noqa: BLE001 - память — забота, не повод сорвать задачу
            log.warning("heavy queue: подготовка памяти перед %s не удалась", kind, exc_info=True)
            return
        with self._cond:
            if ticket in self._active:
                self._active[ticket]["rss_mb"] = rss

    def _after_task(self, ticket: int) -> None:
        from app import memory

        with self._cond:
            info = dict(self._active.get(ticket) or {})
        try:
            after = memory.rss_mb()
            memory.release()
            trimmed = memory.rss_mb()
        except Exception:  # noqa: BLE001
            log.warning("heavy queue: очистка памяти после %s не удалась", info.get("kind"), exc_info=True)
            return
        log.info(
            "heavy queue: %s %s — RSS до %s МБ, после %s МБ, после очистки %s МБ (%.0f с)",
            info.get("kind"), info.get("label") or "", memory.fmt(info.get("rss_mb")), memory.fmt(after),
            memory.fmt(trimmed), time.time() - float(info.get("since") or time.time()),
        )

    def status(self) -> dict:
        """Что идёт и сколько ждёт (для отладки и ответа агенту)."""
        with self._cond:
            return {
                "limit": self.limit(),
                "running": [{k: v for k, v in item.items() if k != "rss_mb"} for item in self._active.values()],
                "waiting": len(self._waiting),
                **({"memory_wait": [dict(v) for v in self._mem_wait.values()]} if self._mem_guard else {}),
            }


_notes = threading.local()


def _notify(text: str | None) -> None:
    """Сказать ждущему потоку, что с его задачей (``on_wait``): текст — ждёт, None — пошла."""
    callback = getattr(_notes, "callback", None)
    if callback is not None:
        try:
            callback(text)
        except Exception:  # noqa: BLE001 - статус — удобство, не повод сорвать задачу
            log.debug("heavy queue: on_wait упал", exc_info=True)


def _queue_note(active: Any, ahead: int) -> str:
    running = ", ".join(f"{a.get('kind')} {a.get('label') or ''}".strip() for a in active) or "—"
    return f"в очереди тяжёлых задач: сейчас идёт {running}" + (f", впереди ещё {ahead}" if ahead else "")


class on_wait:  # noqa: N801 - контекстный менеджер
    """``with on_wait(cb):`` — тяжёлые задачи этого потока сообщают ``cb(text)``, чего ждут (очередь, память),
    и ``cb(None)``, когда начались (статус задания рендера)."""

    def __init__(self, callback: Callable[[str | None], None]) -> None:
        self.callback = callback
        self.prev: Any = None

    def __enter__(self) -> "on_wait":
        self.prev = getattr(_notes, "callback", None)
        _notes.callback = self.callback
        return self

    def __exit__(self, *_exc: Any) -> None:
        _notes.callback = self.prev


class _Slot:
    def __init__(self, queue: HeavyQueue, kind: str, label: str, need_mb: float | None = None) -> None:
        self.queue, self.kind, self.label, self.need_mb = queue, kind, label, need_mb
        self.ticket: int | None = None

    def __enter__(self) -> "_Slot":
        self.ticket = self.queue._enter(self.kind, self.label, self.need_mb)
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.queue._exit(self.ticket)


HEAVY = HeavyQueue(track_memory=True, mem_guard=True)


def _frame_limit() -> int:
    from app.settings import settings

    return settings.frame_jobs_concurrency


# Кадры для агента (view_frame, contact_sheet, обложка): своя очередь пошире — каждый кадр
# поднимает Node-компоновщик (~0.3 ГБ), четыре агента сразу — больше гигабайта.
FRAMES = HeavyQueue(_frame_limit)


def heavy_slot(kind: str, label: str = "", need_mb: float | None = None) -> _Slot:
    """Место в очереди тяжёлых задач процесса (``with heavy_slot("render", "клип 5"): ...``). ``need_mb`` —
    сколько памяти задаче нужно (сторож памяти ждёт её); нет — по роду (app/memory_guard.KIND_NEED_MB)."""
    return HEAVY.slot(kind, label, need_mb)


def frame_slot(label: str = "") -> _Slot:
    """Место в очереди кадров (отдельной от рендера: кадр не ждёт минутный рендер)."""
    return FRAMES.slot("frame", label)


class heavy_queue_nested:  # noqa: N801 - контекстный менеджер
    """Этот поток считается уже в очереди: звуковой проход предпросмотра (app/stream_preview.py) лёгкий,
    минутный рендер он не ждёт и места не занимает."""

    def __enter__(self) -> "heavy_queue_nested":
        HEAVY._local.depth = getattr(HEAVY._local, "depth", 0) + 1  # noqa: SLF001
        return self

    def __exit__(self, *_exc: Any) -> None:
        HEAVY._local.depth = max(0, getattr(HEAVY._local, "depth", 1) - 1)  # noqa: SLF001


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
