"""HTTP-ручки событий геймплея (app/game_events_job.py) — то же, что MCP ``game_events``.

* ``POST /api/sources/{id}/game-events?refresh=&wait=`` — запустить скан (или взять кэш); ещё идёт — 202
  и ``{status: running, done_min, total_min, retry_after_sec}``, повтори тот же запрос;
* ``GET  /api/sources/{id}/game-events?from_sec=&to_sec=&kinds=knock,kill&min_score=`` — готовый итог из
  кэша (нет — 404 с подсказкой запустить POST; идёт — 202).
"""

# Без from __future__ import annotations: FastAPI читает аннотации вида _auth: auth_dep при объявлении.
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Response


def build_game_events_router(auth_dep: Any, get_store: Callable[[], Any]) -> APIRouter:
    router = APIRouter(tags=["Sources"])

    def _kinds(kinds: str | None) -> list[str] | None:
        return [k.strip() for k in kinds.split(",") if k.strip()] if kinds else None

    @router.post("/api/sources/{source_id}/game-events",
                 summary="🎮 События геймплея (нокауты, убийства, смерть, победа, выстрелы, реакции) — фоном, в кэш")
    def api_game_events_scan(source_id: int, _auth: auth_dep, response: Response,  # type: ignore[valid-type]
                             refresh: bool = False, wait: float | None = None, game: str = "apex") -> dict:
        from app.game_events_job import game_events_job, window

        limit = None if wait is None else min(55.0, max(0.0, wait))
        try:
            out = game_events_job(get_store(), source_id, game=game, refresh=refresh, wait=limit)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)[:400]) from exc
        if out.get("status") == "running":
            response.status_code = 202
            response.headers["Retry-After"] = str(out["retry_after_sec"])
            return out
        return window(out)

    @router.get("/api/sources/{source_id}/game-events", summary="🎮 События геймплея из кэша")
    def api_game_events(source_id: int, _auth: auth_dep, response: Response,  # type: ignore[valid-type]
                        from_sec: float | None = None, to_sec: float | None = None, kinds: str | None = None,
                        min_score: float = 0.0, game: str = "apex") -> dict:
        from app.game_events_job import _JOBS, _status, cached, window

        store = get_store()
        try:
            store.get_source(source_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        hit = cached(store, source_id, game)
        if hit is None:
            job = _JOBS.get(int(source_id))
            if job is not None and not job["finished"].is_set():
                response.status_code = 202
                return _status(int(source_id), job)
            raise HTTPException(status_code=404, detail="событий ещё нет: запусти POST /api/sources/{id}/game-events")
        return window({**hit, "status": "done"}, from_sec, to_sec, _kinds(kinds), min_score)

    return router
