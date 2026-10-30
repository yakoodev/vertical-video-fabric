"""Память процесса: сколько держит (VmRSS) и как отдать освобождённое системе.

Ночью 25–26.10 ядро трижды убивало uvicorn по лимиту контейнера (6 ГБ, anon-rss ~5.35 ГБ),
хотя тяжёлые задачи уже шли по одной (app/heavy_queue.py). Снимок /proc/1/smaps живого
процесса через сутки работы (30.10, VmRSS 3.7 ГБ, 66 потоков): 3.4 ГБ анонимной памяти —
сотни кусков до 64 МБ, это кучи (арены) glibc malloc, по одной на поток; больших блоков
нет. Модель Whisper после простоя уже выгружалась (gc), но освобождённое glibc системе не
возвращает: веса Whisper, буферы numpy и PCM многих задач оседают в своих аренах, и
следующая задача берёт новое — RSS растёт ступеньками до OOM.

Отсюда: ``release()`` после каждой тяжёлой задачи и после выгрузки Whisper (gc +
``malloc_trim(0)`` — glibc отдаёт свободные страницы всех арен), ``limit_arenas()`` при
старте (меньше арен — меньше «чужой» свободной памяти, ``MALLOC_ARENA_MAX``) и
``rss_mb()`` для лога до/после задачи. Не Linux (тесты на Windows) — тихо ничего.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import gc
import logging
import os
import sys

log = logging.getLogger(__name__)

M_ARENA_MAX = -8  # mallopt(3), glibc
DEFAULT_ARENA_MAX = 2

_libc: object | None = None


def _glibc():
    """libc с malloc_trim/mallopt (glibc на Linux) или None."""
    global _libc
    if _libc is None:
        lib = False
        if sys.platform.startswith("linux"):
            try:
                candidate = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6")
                if hasattr(candidate, "malloc_trim") and hasattr(candidate, "mallopt"):
                    lib = candidate
            except OSError:
                lib = False
        _libc = lib
    return _libc or None


def rss_mb() -> float | None:
    """Сколько процесс держит в памяти сейчас (VmRSS из /proc/self/status), МБ; None — не Linux."""
    try:
        with open("/proc/self/status", encoding="ascii", errors="replace") as status:
            for line in status:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 1)
    except (OSError, ValueError, IndexError):
        return None
    return None


def release() -> bool:
    """Собрать мусор и вернуть системе свободные страницы куч malloc. True — trim что-то отдал."""
    gc.collect()
    libc = _glibc()
    if libc is None:
        return False
    try:
        return bool(libc.malloc_trim(0))
    except Exception:  # noqa: BLE001 - очистка — удобство, не повод падать
        return False


def limit_arenas() -> None:
    """Не больше ``MALLOC_ARENA_MAX`` арен malloc (по умолчанию 2), пока потоков мало.

    Без ограничения glibc заводит до 8×ядер арен — у каждого потока пула FastAPI своя, и
    освобождённое в одной другим потокам не достаётся. Задан ``MALLOC_ARENA_MAX`` в
    окружении — glibc уже применил его сам при старте, не трогаем.
    """
    if os.environ.get("MALLOC_ARENA_MAX"):
        return
    libc = _glibc()
    if libc is None:
        return
    try:
        libc.mallopt(M_ARENA_MAX, DEFAULT_ARENA_MAX)
    except Exception:  # noqa: BLE001
        pass


def log_to_stderr() -> None:
    """Логи сервиса (``app.*``) от INFO — в вывод контейнера (``docker logs``).

    Без своего обработчика logging печатал только WARNING и выше: строки RSS тяжёлых задач
    и «ждал в очереди» терялись. Обработчик уже есть (тесты, свой конфиг) — не трогаем."""
    app_log = logging.getLogger("app")
    if app_log.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s:     %(name)s: %(message)s"))
    app_log.addHandler(handler)
    app_log.setLevel(logging.INFO)


def fmt(value: float | None) -> str:
    return "?" if value is None else f"{value:.0f}"
