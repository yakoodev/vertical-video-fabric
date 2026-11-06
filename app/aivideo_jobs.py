"""ИИ-видео: запись запуска — чтение и смена статуса, готовый результат (из ``app/aivideo_gen.py``)."""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

from app import aivideo as V

_log = logging.getLogger("app.aivideo_gen")


def _fresh(ref: dict | None) -> bool:
    return bool(ref and not ref["stale"] and ref.get("asset_id"))


def _is_chain(shot: dict, prev: dict | None) -> bool:
    """План продолжает прошлый план с видео: его старт — конечный кадр прошлого (один общий кадр)."""
    return bool(prev and shot.get("continues_from_prev") and prev.get("needs_motion"))


def _fresh_video(store: Any, project_id: int, shot: dict | None) -> dict | None:
    if not shot:
        return None
    ref = V.selected_ref(store, project_id, "shot", int(shot["id"]), "shot_video")
    return ref if _fresh(ref) else None


def _usable(ref: dict | None) -> dict | None:
    return ref if ref and ref.get("asset_id") else None


# --------------------------------------------------------------------------- #
# Запуск. Статусы: approved → submitting → polling → done | failed | blocked_policy | held |
# uncertain (ответ не дошёл — могло быть оплачено; сам не повторяю, сверка баланса).
# --------------------------------------------------------------------------- #


def get_job(store: Any, job_id: int) -> dict:
    row = store.db.query_one("SELECT * FROM aiv_gen_jobs WHERE id = ?", (int(job_id),))
    if not row:
        raise KeyError(f"нет запуска {job_id}")
    data = dict(row)
    data["params"] = json.loads(data.pop("params_json") or "{}")
    return data


def _set_job(store: Any, job_id: int, **fields: Any) -> None:
    cols = ", ".join(f"{k} = ?" for k in fields)
    store.db.execute(f"UPDATE aiv_gen_jobs SET {cols}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (*fields.values(), int(job_id)))


def _cas(store: Any, job_id: int, expect: tuple[str, ...], where: str = "", where_args: tuple = (),
         **fields: Any) -> bool:
    """Сменить поля запуска, только если его статус — один из ``expect`` (сравнить-и-записать одним
    UPDATE). False — запуск уже у другого исполнителя/в другом состоянии: ничего не записано."""
    cols = ", ".join(f"{k} = ?" for k in fields)
    marks = ", ".join("?" for _ in expect)
    cur = store.db.execute(
        f"UPDATE aiv_gen_jobs SET {cols}, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND status IN ({marks})"
        + (f" AND {where}" if where else ""), (*fields.values(), int(job_id), *expect, *where_args))
    return bool(cur.rowcount)


def _runner_id() -> str:
    return f"{threading.current_thread().name}@{os.getpid()}"


def _save_asset(store: Any, job: dict, path: Path, kind: str, mime: str, label: str) -> dict:
    from app.montage_assets import assets_dir, probe_asset

    dest = assets_dir() / f"{uuid4().hex}-aiv-{job['ref_kind']}{path.suffix or '.jpg'}"
    shutil.move(str(path), dest)
    meta = probe_asset(dest)
    if kind == "audio":
        meta = {**meta, "width": 0, "height": 0}
    return store.create_montage_asset(
        kind=kind, label=label[:120], description=f"ИИ-видео: {job['label']} ({job['provider']}/{job['model']})"[:500],
        tags="ии-видео", file_path=str(dest), original_filename=dest.name, mime_type=mime,
        size_bytes=dest.stat().st_size, project_id=int(job["project_id"]), **meta)


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _cached(store: Any, digest: str) -> Any:
    return store.db.query_one(
        "SELECT j.id, r.asset_id FROM aiv_gen_jobs j JOIN aiv_refs r ON r.id = j.result_ref_id "
        "WHERE j.input_hash = ? AND j.status = 'done' AND r.asset_id IS NOT NULL ORDER BY j.id DESC LIMIT 1", (digest,))


def _variant(store: Any, job: dict) -> int:
    if not job["params"].get("regenerate"):
        return 0
    return len(V.list_refs(store, job["project_id"], job["target_type"], job["target_id"], job["ref_kind"],
                           job.get("slot") or "")) + 1


# --------------------------------------------------------------------------- #
# Видео плана и сцепка
# --------------------------------------------------------------------------- #


def _neighbors(store: Any, shot: dict) -> tuple[dict | None, dict | None]:
    shots = V.list_shots(store, shot["script_id"])
    ids = [int(s["id"]) for s in shots]
    if int(shot["id"]) not in ids:
        return None, None
    i = ids.index(int(shot["id"]))
    return (shots[i - 1] if i else None), (shots[i + 1] if i + 1 < len(shots) else None)


def _run_blocking(store: Any, job: dict) -> dict | None:
    """Блокинг плана: раскладка (Gemini для всей серии / по state) → скриншот движком, 0 ₽."""
    from app import aivideo_blocking as B
    from app.motion_render import CompositorMissing

    shot = V.get_shot(store, int(job["target_id"]))
    try:  # комната локации: с плиты Gemini-зрением (один раз, кэш), иначе по описанию
        B.shot_layout(store, int(job["project_id"]), shot, vision=True)
    except Exception as exc:  # noqa: BLE001 - раскладка по описанию всё равно есть
        _log.warning("aiv: раскладка локации плана %s: %s", shot["idx"], exc)
    data = B.current(store, shot)
    if data.get("source") == "auto" and (not shot.get("blocking") or data.get("outdated")):
        only = None if not shot.get("blocking") else {int(shot["id"])}
        if only is None:  # раскладки ещё нет — пишем сразу всей серии (одна комната на локацию)
            missing = {int(s["id"]) for s in V.list_shots(store, int(shot["script_id"])) if not s.get("blocking")}
            only = missing or {int(shot["id"])}
        B.write_blocking(store, int(shot["script_id"]), only=only)
    which = str(job["ref_kind"]).replace("blocking_", "")
    try:
        ref = B.render(store, int(shot["id"]), which, job)
    except CompositorMissing as exc:
        _cas(store, job["id"], ("submitting",), status="failed", error=f"блокинг: {exc}"[:500])
        return None
    if _cas(store, job["id"], ("submitting",), status="done", cost_rub=0.0, cost_source="free", error="",
            result_ref_id=ref["id"]):
        return ref
    return None


def _result_ref(store: Any, job_id: int) -> dict | None:
    rid = get_job(store, job_id).get("result_ref_id")
    try:
        return V.get_ref(store, int(rid)) if rid else None
    except KeyError:
        return None


def list_jobs(store: Any, project_id: int | None = None, approval_id: int | None = None,
              script_id: int | None = None, limit: int = 200) -> list[dict]:
    where, params = [], []
    for col, value in (("project_id", project_id), ("approval_id", approval_id), ("script_id", script_id)):
        if value is not None:
            where.append(f"{col} = ?")
            params.append(int(value))
    sql = "SELECT * FROM aiv_gen_jobs" + (" WHERE " + " AND ".join(where) if where else "")
    rows = store.db.query_all(sql + " ORDER BY id DESC LIMIT ?", (*params, max(1, min(int(limit), 1000))))
    out = []
    for r in rows:
        data = dict(r)
        data.pop("params_json", None)
        data["prompt"] = (data.get("prompt") or "")[:400]
        out.append(data)
    return out


def job_counts(store: Any, project_id: int) -> dict:
    rows = store.db.query_all("SELECT status, COUNT(*) AS n FROM aiv_gen_jobs WHERE project_id = ? GROUP BY status",
                              (int(project_id),))
    return {r["status"]: int(r["n"]) for r in rows}


def cancel_jobs(store: Any, project_id: int, approval_id: int | None = None) -> dict:
    sql = ("UPDATE aiv_gen_jobs SET status = 'cancelled', error = 'отменено', updated_at = CURRENT_TIMESTAMP "
           "WHERE project_id = ? AND status IN ('awaiting', 'approved', 'held')")
    params: list[Any] = [int(project_id)]
    if approval_id is not None:
        sql += " AND approval_id = ?"
        params.append(int(approval_id))
    cur = store.db.execute(sql, params)
    return {"ok": True, "cancelled": int(cur.rowcount or 0)}
