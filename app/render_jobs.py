"""Рендер клипа заданием для агентов (MCP ``render_clip`` / ``render_status``, app/render_tools.py).

Тот же рендер, что ``POST /api/clip-plans/{id}/render`` и ``vvf.py render``: клип со своими настройками
(``app/censor.render_plan``), очередь тяжёлых задач (одно тяжёлое за раз) — внутри. Разница одна: REST ждёт файл
в том же запросе (минуты), а вызов MCP столько не живёт — поэтому рендер идёт в фоне, агент получает ``render_id``
и спрашивает статус.

Задания живут в памяти процесса: после перезапуска сервиса ``render_status`` их не знает — тогда готовый файл виден
по клипу (``get_clip``, рендеры клипа). На один клип — одно активное задание: повторный ``render_clip``, пока
идёт прошлый, возвращает его же.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable
from uuid import uuid4

_log = logging.getLogger(__name__)

ACTIVE = ("queued", "rendering")
KEEP_DONE = 200  # сколько завершённых заданий помнить

_jobs: dict[str, dict] = {}
_lock = threading.Lock()
_changed = threading.Condition(_lock)


def _default_render(store: Any, clip_id: int) -> dict:
    from app.censor import render_plan

    return render_plan(store, clip_id)


# Тесты подменяют (настоящий рендер — ffmpeg и Node).
render_fn: Callable[[Any, int], dict] = _default_render


def _now() -> float:
    return round(time.time(), 3)


def _brief_clip(clip: dict) -> dict:
    qc = clip.get("qc") if isinstance(clip.get("qc"), dict) else None
    out = {"render_clip_id": clip.get("id"), "render_status": clip.get("status"),
           "duration_sec": clip.get("duration_sec"), "width": clip.get("width"), "height": clip.get("height"),
           "size_bytes": clip.get("size_bytes"), "error": clip.get("error") or ""}
    if clip.get("id"):
        out["video_url"] = f"/media/clips/{clip['id']}"
    if qc is not None:
        out["qc"] = qc
    return out


def _public(job: dict) -> dict:
    return {k: v for k, v in job.items() if not k.startswith("_")}


def _trim() -> None:
    done = [j for j in _jobs.values() if j["status"] not in ACTIVE]
    for job in sorted(done, key=lambda j: j["created_at"])[:-KEEP_DONE or None]:
        _jobs.pop(job["render_id"], None)


def active_for(clip_id: int) -> dict | None:
    with _lock:
        job = next((j for j in _jobs.values() if j["clip_id"] == int(clip_id) and j["status"] in ACTIVE), None)
        return _public(job) if job else None


def start(store: Any, clip_id: int, requested_by: str = "agent") -> dict:
    """Поставить рендер клипа в фон. Уже идёт — вернуть идущее (``reused``)."""
    clip_id = int(clip_id)
    store.get_clip_plan(clip_id, include_segments=False)  # нет клипа — KeyError сразу, а не в потоке
    with _lock:
        cur = next((j for j in _jobs.values() if j["clip_id"] == clip_id and j["status"] in ACTIVE), None)
        if cur is not None:
            return {**_public(cur), "reused": True}
        job = {"render_id": "r" + uuid4().hex[:10], "clip_id": clip_id, "status": "queued", "requested_by": requested_by,
               "created_at": _now(), "started_at": None, "finished_at": None, "error": "", "result": None}
        _jobs[job["render_id"]] = job
        _trim()
    threading.Thread(target=_run, args=(store, job["render_id"]), name=f"render-job-{clip_id}", daemon=True).start()
    return {**_public(job), "reused": False}


def _set(render_id: str, **fields: Any) -> None:
    with _changed:
        job = _jobs.get(render_id)
        if job is not None:
            job.update(fields)
        _changed.notify_all()


def _run(store: Any, render_id: str) -> None:
    job = _jobs[render_id]
    # «rendering» — поток взял задание; место в очереди тяжёлых он ждёт уже внутри рендера (heavy_slot).
    _set(render_id, status="rendering", started_at=_now())
    try:
        clip = render_fn(store, job["clip_id"])
    except Exception as exc:  # noqa: BLE001 - любой сбой — статус задания, а не упавший поток
        _log.warning("render job %s (клип %s) failed: %s", render_id, job["clip_id"], exc)
        # KeyError/ValueError — ошибки запроса (как 400 у REST): текст как есть; прочее — с именем исключения.
        text = str(exc) if isinstance(exc, (KeyError, ValueError)) else f"{type(exc).__name__}: {exc}"
        _set(render_id, status="failed", finished_at=_now(), error=text[:500])
        return
    result = _brief_clip(clip) if isinstance(clip, dict) else {}
    ok = isinstance(clip, dict) and clip.get("status") == "succeeded"
    _set(render_id, status="done" if ok else "failed", finished_at=_now(), result=result,
         error="" if ok else (result.get("error") or f"рендер вернул статус {result.get('render_status')}"))


def get(render_id: str) -> dict | None:
    with _lock:
        job = _jobs.get(str(render_id))
        return _public(job) if job else None


def wait(render_id: str, timeout: float) -> dict | None:
    """Статус задания; пока оно активно — ждать до ``timeout`` секунд (длинный опрос)."""
    deadline = time.monotonic() + max(0.0, float(timeout))
    with _changed:
        while True:
            job = _jobs.get(str(render_id))
            if job is None or job["status"] not in ACTIVE:
                return _public(job) if job else None
            left = deadline - time.monotonic()
            if left <= 0:
                return _public(job)
            _changed.wait(left)


def reset() -> None:
    """Для тестов."""
    with _lock:
        _jobs.clear()
