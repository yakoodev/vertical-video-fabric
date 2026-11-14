"""События геймплея источника фоном и в кэше: REST ``/api/sources/{id}/game-events`` и MCP ``game_events``.

Скан записи (app/game_events.py) — минуты на час видео, поэтому как у расшифровки: один поток на источник,
повторный вызов к нему подключается; вызов ждёт не дольше ``wait`` секунд и иначе отвечает
``{status: "running", done_min, total_min, retry_after_sec}``. Итог лежит файлом
``<runtime>/game_events/source-<id>.json`` с отпечатком файла исходника (путь, размер, время) и версией
детектора: пересобранный исходник или новый детектор — скан заново. Скан идёт в очереди тяжёлых задач.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

VERSION = 1
WAIT_SEC = 45.0
_JOBS: dict[int, dict] = {}
_LOCK = threading.Lock()


def _cache_path(source_id: int) -> Path:
    from app.settings import settings

    folder = settings.runtime_dir / "game_events"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"source-{int(source_id)}.json"


def _stamp(path: Path, game: str) -> dict:
    try:
        st = path.stat()
        return {"path": str(path), "size": st.st_size, "mtime": int(st.st_mtime), "version": VERSION, "game": game}
    except OSError:
        return {"path": str(path), "version": VERSION, "game": game}


def _source_path(store: Any, source_id: int) -> Path:
    source = store.get_source(int(source_id))  # нет источника — KeyError
    path = Path(source.get("local_path") or "")
    if not str(source.get("local_path") or "") or not path.exists():
        raise ValueError("у источника нет файла видео (не скачан или удалён)")
    return path


def cached(store: Any, source_id: int, game: str = "apex") -> dict | None:
    """Готовый итог из кэша, если он про этот же файл и эту версию детектора."""
    file = _cache_path(source_id)
    if not file.exists():
        return None
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
        path = _source_path(store, source_id)
    except (OSError, ValueError, KeyError):
        return None
    return data if data.get("stamp") == _stamp(path, game) else None


def _run(store: Any, source_id: int, game: str, job: dict) -> None:
    from app.game_events import detect_game_events, fight_spans
    from app.heavy_queue import heavy_slot

    try:
        path = _source_path(store, source_id)
        from app.game_events import probe

        job["total_sec"] = float(probe(path).get("duration") or 0.0)
        started = time.monotonic()
        with heavy_slot("game_events", f"источник {source_id}"):
            events = detect_game_events(path, game=game, progress=lambda t: job.__setitem__("done_sec", t))
        result = {"source_id": int(source_id), "game": game, "time": "source", "duration_sec": job["total_sec"],
                  "scan_sec": round(time.monotonic() - started, 1), "events": events, "fights": fight_spans(events),
                  "stamp": _stamp(path, game)}
        _cache_path(source_id).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        job["result"] = result
    except Exception as exc:  # noqa: BLE001 - ошибка уходит вызывающему
        job["error"] = exc
    finally:
        job["finished"].set()


def game_events_job(store: Any, source_id: int, *, game: str = "apex", refresh: bool = False,
                    wait: float | None = None) -> dict:
    """Итог (``status: "done"``) из кэша или скана; скан дольше ``wait`` — ``status: "running"``."""
    wait = WAIT_SEC if wait is None else max(0.0, float(wait))
    source_id = int(source_id)
    with _LOCK:
        job = _JOBS.get(source_id)
        if job is not None and job["finished"].is_set():
            _JOBS.pop(source_id, None)
            job = None
        if job is None:
            if not refresh:
                hit = cached(store, source_id, game)
                if hit is not None:
                    return {**hit, "status": "done", "cached": True}
            _source_path(store, source_id)  # нет источника/файла — ошибка сразу, а не из потока
            job = {"finished": threading.Event(), "started": time.monotonic(), "done_sec": 0.0, "total_sec": 0.0}
            _JOBS[source_id] = job
            threading.Thread(target=_run, args=(store, source_id, game, job), name=f"game-events-{source_id}",
                             daemon=True).start()
    if job["finished"].wait(wait):
        with _LOCK:
            if _JOBS.get(source_id) is job:
                _JOBS.pop(source_id)
        if "error" in job:
            err = job["error"]
            raise err if isinstance(err, (KeyError, ValueError)) else ValueError(str(err)[:300])
        return {**job["result"], "status": "done", "cached": False}
    return _status(source_id, job)


def _status(source_id: int, job: dict) -> dict:
    done, total = job["done_sec"], job["total_sec"]
    elapsed = max(0.0, time.monotonic() - job["started"])
    retry = 30.0
    if done > 0 and total > done:
        retry = min(120.0, max(10.0, elapsed / done * (total - done)))
    retry_i = int(round(retry))
    return {"source_id": source_id, "status": "running", "done_min": round(done / 60, 1),
            "total_min": round(total / 60, 1), "elapsed_sec": round(elapsed), "retry_after_sec": retry_i,
            "note": f"ищу события в записи: готово {done / 60:.1f} из {total / 60:.1f} мин, повтори через ~{retry_i} с "
                    "(тот же вызов; второй скан не запустится)"}


def window(result: dict, lo: float | None = None, hi: float | None = None, kinds: list[str] | None = None,
           min_score: float = 0.0) -> dict:
    """Итог, урезанный до окна [lo, hi], видов и минимальной оценки (бои — пересекающие окно)."""
    lo_v, hi_v = float(lo or 0.0), float("inf") if hi is None else float(hi)
    wanted = set(kinds or [])
    events = [e for e in result.get("events", []) if lo_v <= e["t"] <= hi_v and e["score"] >= min_score
              and (not wanted or e["kind"] in wanted)]
    fights = [f for f in result.get("fights", []) if f["end"] >= lo_v and f["start"] <= hi_v]
    counts: dict[str, int] = {}
    for e in events:
        counts[e["kind"]] = counts.get(e["kind"], 0) + 1
    out = {k: v for k, v in result.items() if k not in ("events", "fights", "stamp")}
    return {**out, "events": events, "fights": fights, "counts": counts}
