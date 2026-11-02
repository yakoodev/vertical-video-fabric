"""Очередь 3D-рендера для хост-воркера: в Docker сервиса нет ни GPU, ни Blender — рендерит компьютер владельца.

Сервис ставит задание (``enqueue``: режиссура серии ``app.series3d_blender.build_job`` — JSON), воркер на хосте
(``tools/render_worker.py``) забирает его (``claim``), шлёт пульс с процентом (``heartbeat``), рендерит Blender'ом
и отдаёт mp4 (``complete``) или ошибку (``fail``). Ручки — ``build_render3d_router`` (Bearer-токен, как у MCP).

* Статусы: ``queued`` → ``running`` → ``done`` | ``failed`` | ``cancelled``.
* Пульса нет ``STALE_SEC`` — задание снова в очереди (воркер упал/выключили компьютер); после ``MAX_ATTEMPTS``
  попыток — ``failed``. Рендер возобновляемый: кадры в кэше воркера, повтор дорисует.
* ``key`` — что именно рендерим (сценарий + озвучка + качество): то же задание повторно не ставится, готовое —
  берётся из кэша.
* Воркер «в сети», если заходил за заданием или слал пульс за ``ONLINE_SEC``.
"""
import json
import shutil
import time
from pathlib import Path
from typing import Any, Callable

STATUSES = ("queued", "running", "done", "failed", "cancelled")
ACTIVE = ("queued", "running")
STALE_SEC = 180.0
ONLINE_SEC = 90.0
MAX_ATTEMPTS = 3

SCHEMA_SQL = [
    """
    CREATE TABLE IF NOT EXISTS render3d_jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL DEFAULT 'series3d',
        key TEXT NOT NULL DEFAULT '',
        episode_id INTEGER,
        status TEXT NOT NULL DEFAULT 'queued',
        title TEXT NOT NULL DEFAULT '',
        payload_json TEXT NOT NULL DEFAULT '{}',
        progress REAL NOT NULL DEFAULT 0,
        note TEXT NOT NULL DEFAULT '',
        worker TEXT NOT NULL DEFAULT '',
        attempts INTEGER NOT NULL DEFAULT 0,
        result_path TEXT NOT NULL DEFAULT '',
        result_json TEXT NOT NULL DEFAULT '{}',
        error TEXT NOT NULL DEFAULT '',
        heartbeat_ts REAL NOT NULL DEFAULT 0,
        claimed_ts REAL NOT NULL DEFAULT 0,
        finished_ts REAL NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_render3d_jobs_status ON render3d_jobs(status, id)",
    "CREATE INDEX IF NOT EXISTS idx_render3d_jobs_key ON render3d_jobs(key, id)",
    """
    CREATE TABLE IF NOT EXISTS render3d_workers (
        name TEXT PRIMARY KEY,
        info_json TEXT NOT NULL DEFAULT '{}',
        last_seen REAL NOT NULL DEFAULT 0
    )
    """,
]


class QueueError(ValueError):
    """Не то задание/не тот воркер/не в том статусе."""


def ensure_render_queue_schema(conn) -> None:
    for sql in SCHEMA_SQL:
        conn.execute(sql)


def _now() -> float:
    return time.time()


def _view(row: Any, payload: bool = False) -> dict:
    d = dict(row)
    raw = d.pop("payload_json", "{}")
    if payload:
        d["payload"] = json.loads(raw or "{}")
    d["result"] = json.loads(d.pop("result_json", "{}") or "{}")
    d["progress"] = round(float(d.get("progress") or 0.0), 4)
    return d


def get(store: Any, job_id: int, payload: bool = False) -> dict:
    row = store.db.query_one("SELECT * FROM render3d_jobs WHERE id = ?", (int(job_id),))
    if not row:
        raise KeyError(f"нет задания рендера {job_id}")
    return _view(row, payload)


def find(store: Any, key: str, payload: bool = False) -> dict | None:
    """Последнее не отменённое задание с этим ключом."""
    row = store.db.query_one("SELECT * FROM render3d_jobs WHERE key = ? AND status != 'cancelled' ORDER BY id DESC LIMIT 1",
                             (str(key),))
    return _view(row, payload) if row else None


def list_jobs(store: Any, limit: int = 30, episode_id: int | None = None) -> list[dict]:
    if episode_id is not None:
        rows = store.db.query_all("SELECT * FROM render3d_jobs WHERE episode_id = ? ORDER BY id DESC LIMIT ?",
                                  (int(episode_id), int(limit)))
    else:
        rows = store.db.query_all("SELECT * FROM render3d_jobs ORDER BY id DESC LIMIT ?", (int(limit),))
    return [_view(r) for r in rows]


def enqueue(store: Any, payload: dict, *, key: str, kind: str = "series3d", episode_id: int | None = None,
            title: str = "") -> dict:
    """Поставить задание; то же (по ``key``) в работе или готово — вернуть его, упавшее — поставить заново."""
    have = find(store, key)
    if have and have["status"] in ("queued", "running", "done") and (have["status"] != "done" or result_file(have)):
        return have
    cur = store.db.execute(
        "INSERT INTO render3d_jobs (kind, key, episode_id, title, payload_json) VALUES (?, ?, ?, ?, ?)",
        (kind, str(key), episode_id, str(title)[:200], json.dumps(payload, ensure_ascii=False)))
    return get(store, int(cur.lastrowid))


def touch_worker(store: Any, worker: str, info: dict | None = None) -> None:
    """Воркер заходил; ``info`` (Blender, хост) — только если прислал, иначе прежнее."""
    keep = "render3d_workers.info_json" if not info else "excluded.info_json"
    store.db.execute(
        "INSERT INTO render3d_workers (name, info_json, last_seen) VALUES (?, ?, ?) "
        f"ON CONFLICT(name) DO UPDATE SET info_json = {keep}, last_seen = excluded.last_seen",
        (str(worker)[:80] or "worker", json.dumps(info or {}, ensure_ascii=False), _now()))


def workers(store: Any) -> list[dict]:
    now = _now()
    out = []
    for r in store.db.query_all("SELECT * FROM render3d_workers ORDER BY last_seen DESC"):
        out.append({"name": r["name"], "info": json.loads(r["info_json"] or "{}"), "last_seen": r["last_seen"],
                    "ago_sec": round(now - float(r["last_seen"] or 0), 1),
                    "online": now - float(r["last_seen"] or 0) <= ONLINE_SEC})
    return out


def worker_online(store: Any) -> bool:
    return any(w["online"] for w in workers(store))


def requeue_stale(store: Any) -> int:
    """Пульса давно нет — задание снова в очереди (или ``failed`` после ``MAX_ATTEMPTS``)."""
    cut = _now() - STALE_SEC
    rows = store.db.query_all("SELECT id, attempts FROM render3d_jobs WHERE status = 'running' AND heartbeat_ts < ?", (cut,))
    for r in rows:
        if int(r["attempts"]) >= MAX_ATTEMPTS:
            store.db.execute("UPDATE render3d_jobs SET status = 'failed', error = ?, finished_ts = ?, "
                             "updated_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'running'",
                             (f"воркер пропал {MAX_ATTEMPTS} раза подряд — рендер не закончен", _now(), int(r["id"])))
        else:
            store.db.execute("UPDATE render3d_jobs SET status = 'queued', worker = '', note = 'воркер пропал — снова в очереди', "
                             "updated_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'running'", (int(r["id"]),))
    return len(rows)


def claim(store: Any, worker: str, info: dict | None = None, kinds: tuple[str, ...] = ("series3d",)) -> dict | None:
    """Воркер берёт старейшее задание из очереди (сравнить-и-записать: двое одно не возьмут)."""
    touch_worker(store, worker, info)
    requeue_stale(store)
    marks = ", ".join("?" for _ in kinds)
    for _ in range(5):
        row = store.db.query_one(f"SELECT id FROM render3d_jobs WHERE status = 'queued' AND kind IN ({marks}) "
                                 "ORDER BY id LIMIT 1", kinds)
        if not row:
            return None
        now = _now()
        cur = store.db.execute(
            "UPDATE render3d_jobs SET status = 'running', worker = ?, attempts = attempts + 1, heartbeat_ts = ?, "
            "claimed_ts = ?, note = 'воркер взял задание', error = '', updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND status = 'queued'", (str(worker)[:80], now, now, int(row["id"])))
        if cur.rowcount:
            return get(store, int(row["id"]), payload=True)
    return None


def _own(store: Any, job_id: int, worker: str) -> dict:
    job = get(store, job_id)
    if job["status"] != "running" or job["worker"] != str(worker)[:80]:
        raise QueueError(f"задание {job_id} не у воркера {worker} (статус {job['status']})")
    return job


def heartbeat(store: Any, job_id: int, worker: str, progress: float | None = None, note: str = "") -> dict:
    """Пульс: процент и заметка. Ответ — статус задания (``cancelled`` — воркеру остановиться)."""
    touch_worker(store, worker)
    job = get(store, job_id)
    if job["status"] != "running" or job["worker"] != str(worker)[:80]:
        return {"status": job["status"], "stop": True}
    sets, args = ["heartbeat_ts = ?"], [_now()]
    if progress is not None:
        sets.append("progress = ?")
        args.append(max(0.0, min(1.0, float(progress))))
    if note:
        sets.append("note = ?")
        args.append(str(note)[:300])
    store.db.execute(f"UPDATE render3d_jobs SET {', '.join(sets)}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (*args, int(job_id)))
    return {"status": "running", "stop": False}


def results_dir() -> Path:
    from app.settings import settings

    path = Path(settings.data_dir) / "render3d"
    path.mkdir(parents=True, exist_ok=True)
    return path


def complete(store: Any, job_id: int, worker: str, src: Path | str | Any, result: dict | None = None) -> dict:
    """Готово: mp4 (путь или файловый объект) → ``<data>/render3d/job-<id>.mp4``."""
    _own(store, job_id, worker)
    dest = results_dir() / f"job-{int(job_id)}.mp4"
    tmp = dest.with_suffix(".part")
    if isinstance(src, (str, Path)):
        shutil.copyfile(src, tmp)
    else:
        with tmp.open("wb") as fh:
            shutil.copyfileobj(src, fh)
    if tmp.stat().st_size < 1000:
        tmp.unlink(missing_ok=True)
        raise QueueError("пустой результат рендера")
    tmp.replace(dest)
    store.db.execute("UPDATE render3d_jobs SET status = 'done', progress = 1, result_path = ?, result_json = ?, note = 'готово', "
                     "finished_ts = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (str(dest), json.dumps(result or {}, ensure_ascii=False), _now(), int(job_id)))
    touch_worker(store, worker)
    _after_done()
    return get(store, job_id)


def fail(store: Any, job_id: int, worker: str, error: str, retry: bool = False) -> dict:
    _own(store, job_id, worker)
    if retry and get(store, job_id)["attempts"] < MAX_ATTEMPTS:
        store.db.execute("UPDATE render3d_jobs SET status = 'queued', worker = '', note = ?, updated_at = CURRENT_TIMESTAMP "
                         "WHERE id = ?", (f"повтор: {str(error)[:200]}", int(job_id)))
    else:
        store.db.execute("UPDATE render3d_jobs SET status = 'failed', error = ?, finished_ts = ?, "
                         "updated_at = CURRENT_TIMESTAMP WHERE id = ?", (str(error)[:2000], _now(), int(job_id)))
    _after_done()
    return get(store, job_id)


def cancel(store: Any, job_id: int) -> dict:
    store.db.execute("UPDATE render3d_jobs SET status = 'cancelled', note = 'отменено', updated_at = CURRENT_TIMESTAMP "
                     "WHERE id = ? AND status IN ('queued', 'running')", (int(job_id),))
    return get(store, job_id)


def result_file(job: dict) -> Path | None:
    p = Path(job.get("result_path") or "")
    return p if job.get("status") == "done" and p.is_file() else None


_on_done: list[Callable[[], Any]] = []


def on_done(fn: Callable[[], Any]) -> None:
    """Подписка: задание закончилось (истории будят свой работник, чтобы сразу собрать клип)."""
    _on_done.append(fn)


def _after_done() -> None:
    for fn in list(_on_done):
        try:
            fn()
        except Exception:  # noqa: BLE001 - подписчик не валит воркера
            pass


def wait_label(store: Any, job: dict) -> tuple[str, str]:
    """(статус для UI, заметка) пока эпизод ждёт рендер."""
    online = worker_online(store)
    if job["status"] == "running":
        pct = int(round(job["progress"] * 100))
        return f"рендер на компьютере… {pct}%", job.get("note") or ""
    if job["status"] == "queued":
        if online:
            return "рендер: в очереди компьютера", ""
        return ("рендер: ждёт компьютер с Blender",
                "Компьютер с Blender не в сети — 3D-серия ждёт рендера. Запусти на нём: "
                "python tools/render_worker.py --server <адрес сервиса> (токен — как у MCP)")
    return job["status"], job.get("error") or ""


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

def build_render3d_router(auth_dep: Any, get_store: Callable[[], Any]):
    """``/api/render3d/*``: claim, heartbeat, complete (multipart mp4), fail, cancel, список, статус воркеров."""
    from fastapi import APIRouter, Body, File, Form, HTTPException, UploadFile

    router = APIRouter(tags=["Render3D"])

    def call(fn: Callable, *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(get_store(), *args, **kwargs)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc).strip("'\"")) from exc
        except QueueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/api/render3d/claim", summary="Воркер: взять задание 3D-рендера")
    def api_claim(_auth: auth_dep, payload: dict = Body(default={})) -> dict:
        worker = str(payload.get("worker") or "worker")
        kinds = tuple(payload.get("kinds") or ("series3d",))
        return {"job": call(claim, worker, payload.get("info") or {}, kinds)}

    @router.post("/api/render3d/jobs/{job_id}/heartbeat", summary="Воркер: пульс и процент")
    def api_heartbeat(job_id: int, _auth: auth_dep, payload: dict = Body(default={})) -> dict:
        return call(heartbeat, job_id, str(payload.get("worker") or "worker"), payload.get("progress"),
                    str(payload.get("note") or ""))

    @router.post("/api/render3d/jobs/{job_id}/complete", summary="Воркер: готовый mp4")
    def api_complete(job_id: int, _auth: auth_dep, file: UploadFile = File(...), worker: str = Form("worker"),
                     result: str = Form("{}")) -> dict:
        try:
            res = json.loads(result or "{}")
        except json.JSONDecodeError:
            res = {}
        return call(complete, job_id, worker, file.file, res)

    @router.post("/api/render3d/jobs/{job_id}/fail", summary="Воркер: рендер не удался")
    def api_fail(job_id: int, _auth: auth_dep, payload: dict = Body(default={})) -> dict:
        return call(fail, job_id, str(payload.get("worker") or "worker"), str(payload.get("error") or "ошибка"),
                    bool(payload.get("retry")))

    @router.post("/api/render3d/jobs/{job_id}/cancel", summary="Отменить задание рендера")
    def api_cancel(job_id: int, _auth: auth_dep) -> dict:
        return call(cancel, job_id)

    @router.get("/api/render3d/jobs", summary="Задания 3D-рендера")
    def api_jobs(_auth: auth_dep, limit: int = 30, episode_id: int | None = None) -> dict:
        return {"jobs": call(list_jobs, limit, episode_id)}

    @router.get("/api/render3d/jobs/{job_id}", summary="Задание 3D-рендера")
    def api_job(job_id: int, _auth: auth_dep) -> dict:
        return call(get, job_id)

    @router.get("/api/render3d/status", summary="Воркеры 3D-рендера: кто в сети, что в очереди")
    def api_status(_auth: auth_dep) -> dict:
        store = get_store()
        counts = {r["status"]: int(r["n"]) for r in store.db.query_all(
            "SELECT status, COUNT(*) AS n FROM render3d_jobs GROUP BY status")}
        ws = workers(store)
        return {"online": any(w["online"] for w in ws), "workers": ws, "counts": counts}

    return router
