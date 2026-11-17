"""Сторож памяти перед тяжёлой задачей: не начинать рендер, когда памяти машины нет.

17–18.11 Docker Desktop дважды падал ночью: рендер сервиса совпал с чужой задачей на том же ПК
(модель голоса, 6–9 ГБ из 31 ГБ). Очередь тяжёлых задач (app/heavy_queue.py) держала наши задачи по
одной, но про чужую память она не знала. Теперь задача, дойдя до начала очереди, сначала смотрит,
сколько памяти свободно, и ждёт, пока её хватит на неё саму плюс запас:

* ``/proc/meminfo`` — ``MemAvailable`` машины (в Docker Desktop — виртуалки WSL, общей для всех
  контейнеров): после старта задачи должно остаться не меньше ``HEAVY_MEM_RESERVE_MB`` (3 ГБ);
* cgroup контейнера (v2 ``memory.max``/``memory.current``, v1 ``memory.limit_in_bytes``/``usage``):
  до потолка контейнера (``mem_limit`` в docker-compose) — задача целиком плюс
  ``HEAVY_MEM_CGROUP_RESERVE_MB``; кэш файлов (``inactive_file``) ядро отдаст само, его не считаем.

Сколько задаче нужно — грубо, по роду (``KIND_NEED_MB``), у рендера — по размеру кадра и длине
(``render_need_mb``). Ждёт не дольше ``HEAVY_MEM_WAIT_MAX_SEC`` (30 мин) — дальше ошибка
``MemoryWaitTimeout`` с цифрами (лучше не сделанный рендер, чем упавший Docker). ``HEAVY_MEM_GUARD=0`` —
не смотреть. Не Linux (тесты на Windows) или файлов нет — памяти «достаточно», как раньше.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

MEMINFO = Path("/proc/meminfo")
CGROUP_V2 = (Path("/sys/fs/cgroup/memory.max"), Path("/sys/fs/cgroup/memory.current"), Path("/sys/fs/cgroup/memory.stat"))
CGROUP_V1 = (Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"), Path("/sys/fs/cgroup/memory/memory.usage_in_bytes"),
             Path("/sys/fs/cgroup/memory/memory.stat"))

# Сколько памяти берёт задача рода (МБ, грубо, по замерам RSS в логе очереди и docker stats): с запасом.
KIND_NEED_MB: dict[str, float] = {
    "render": 2000,  # ffmpeg-кодировщик основы + Node-компоновщик 1080×1920 + кадры видео-слоёв
    "whisper": 1800,  # large-v3-turbo + буферы PCM
    "demucs": 2500,
    "music_video": 2500,
    "amv": 2000,
    "game_events": 1200,
    "references": 1200,
    "tracking": 900,
    "audio_map": 800,
    "publish_master": 900,
    "feed_preview": 900,
    "cover_frames": 700,
    "cover_hud": 700,
    "color_match": 700,
    "action": 900,
}
DEFAULT_NEED_MB = 800.0


class MemoryWaitTimeout(RuntimeError):
    """Памяти так и не стало достаточно за отведённое время — задачу не начали."""


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="ascii", errors="replace")
    except OSError:
        return None


def system_available_mb() -> float | None:
    """``MemAvailable`` из /proc/meminfo, МБ; нет файла — None."""
    text = _read(MEMINFO)
    if not text:
        return None
    for line in text.splitlines():
        if line.startswith("MemAvailable:"):
            try:
                return int(line.split()[1]) / 1024
            except (ValueError, IndexError):
                return None
    return None


def _stat_value(text: str | None, key: str) -> int:
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == key:
            try:
                return int(parts[1])
            except ValueError:
                return 0
    return 0


def cgroup_headroom_mb() -> float | None:
    """Сколько ещё можно занять до потолка памяти контейнера, МБ (кэш файлов не в счёт); потолка нет — None."""
    for limit_f, used_f, stat_f in (CGROUP_V2, CGROUP_V1):
        raw = _read(limit_f)
        used = _read(used_f)
        if raw is None or used is None:
            continue
        raw = raw.strip()
        try:
            if raw == "max" or int(raw) >= 1 << 60:
                return None
            limit, current = int(raw), int(used.strip())
        except ValueError:
            continue
        stat = _read(stat_f)
        cache = _stat_value(stat, "inactive_file") or _stat_value(stat, "total_inactive_file")
        return (limit - max(0, current - cache)) / 2 ** 20
    return None


def free_mb() -> float | None:
    """Меньшее из свободного в системе и до потолка контейнера (для оценок вроде числа браузеров)."""
    vals = [v for v in (system_available_mb(), cgroup_headroom_mb()) if v is not None]
    return min(vals) if vals else None


def need_mb(kind: str, need: float | None = None) -> float:
    """Сколько памяти ждём под задачу: явная оценка или по роду."""
    if need is not None:
        try:
            value = float(need)
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass
    return float(KIND_NEED_MB.get(str(kind), DEFAULT_NEED_MB))


def render_need_mb(width: int, height: int, duration_sec: float) -> float:
    """Рендер клипа: кодировщик и компоновщик растут с площадью кадра, видео-слои и вставки — с длиной."""
    megapixels = max(0.1, float(width) * float(height) / 1e6)
    return round(700 + 450 * megapixels + min(800.0, 6.0 * max(0.0, float(duration_sec))))


def _settings() -> Any:
    from app.settings import settings

    return settings


def enabled() -> bool:
    return bool(getattr(_settings(), "heavy_mem_guard", True))


def check(need: float) -> tuple[bool, dict]:
    """Хватит ли памяти на задачу в ``need`` МБ: ``(ok, {system_mb, cgroup_mb, need_mb, reserve_mb, text})``."""
    s = _settings()
    reserve = float(getattr(s, "heavy_mem_reserve_mb", 3072))
    cg_reserve = float(getattr(s, "heavy_mem_cgroup_reserve_mb", 300))
    system, cgroup = system_available_mb(), cgroup_headroom_mb()
    ok = True
    short: list[str] = []
    if system is not None and system - need < reserve:
        ok = False
        short.append(f"свободно {system / 1024:.1f} ГБ, нужно ~{need / 1024:.1f} ГБ и запас {reserve / 1024:.1f} ГБ")
    if cgroup is not None and cgroup - need < cg_reserve:
        ok = False
        short.append(f"до потолка контейнера {cgroup / 1024:.1f} ГБ, нужно ~{need / 1024:.1f} ГБ")
    info = {"system_mb": None if system is None else round(system), "cgroup_mb": None if cgroup is None else round(cgroup),
            "need_mb": round(need), "reserve_mb": round(reserve), "ok": ok}
    info["text"] = ("жду память: " + "; ".join(short)) if short else ""
    return ok, info


def wait_for_memory(kind: str, label: str = "", need: float | None = None, *,
                    on_wait: Callable[[dict], None] | None = None,
                    sleep: Callable[[float], None] | None = None,
                    clock: Callable[[], float] = time.monotonic) -> dict | None:
    """Ждать, пока памяти хватит на задачу. ``on_wait(info)`` — при каждой проверке, пока ждём
    (``info["text"]`` — «жду память: свободно X ГБ, нужно ~Y»). Вернуть последнее ``info`` (ждали) или None
    (памяти хватило сразу / сторож выключен). Не дождались за ``HEAVY_MEM_WAIT_MAX_SEC`` — ``MemoryWaitTimeout``."""
    if not enabled():
        return None
    want = need_mb(kind, need)
    ok, info = check(want)
    if ok:
        return None
    s = _settings()
    poll = max(0.05, float(getattr(s, "heavy_mem_poll_sec", 5.0)))
    limit = max(0.0, float(getattr(s, "heavy_mem_wait_max_sec", 1800.0)))
    sleep = sleep or time.sleep
    started = clock()
    log.warning("heavy queue: %s %s — %s", kind, label, info["text"])
    while not ok:
        info["waited_sec"] = round(clock() - started, 1)
        if on_wait:
            try:
                on_wait(info)
            except Exception:  # noqa: BLE001 - статус — удобство
                log.debug("memory guard: on_wait упал", exc_info=True)
        if clock() - started >= limit:
            raise MemoryWaitTimeout(
                f"не дождался памяти за {limit / 60:.0f} мин ({info['text'].removeprefix('жду память: ')}) — "
                "задачу не начинал; освободите память (другие тяжёлые программы) и повторите")
        sleep(poll)
        ok, info = check(want)
    info["waited_sec"] = round(clock() - started, 1)
    log.info("heavy queue: %s %s — память есть, ждал %.0f с", kind, label, info["waited_sec"])
    return info
