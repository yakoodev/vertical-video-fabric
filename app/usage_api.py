"""HTTP-ручки учёта трат Gemini (``app/gemini_usage.py``).

* ``GET  /api/usage/gemini`` ?since&until&project_id&source_id&clip_id&session&purpose&model&group_by — итог и группы
  (group_by: purpose | model | day | session | project | caller | message)
* ``GET  /api/usage/gemini/calls`` — те же фильтры + limit/offset: последние запросы
* ``GET  /api/usage/gemini/prices`` — таблица цен (умолчания, правки, курс); ``PUT`` {overrides?, usd_rub?} — правка
* ``POST /api/usage/gemini/recompute`` — пересчитать цену записанных строк по нынешней таблице

Время — ISO (``2026-11-12``, ``2026-11-12T10:00:00Z``, со сдвигом зоны); в базе UTC.
"""

# Без from __future__ import annotations: FastAPI читает аннотации _auth: auth_dep сразу.
from typing import Any, Callable, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request

from app import gemini_usage as U
from app.agent_settings_api import owner_only


def build_usage_router(auth_dep: Any, get_store: Callable[[], Any]) -> APIRouter:
    router = APIRouter(tags=["Usage"])

    def filters(since: Optional[str], until: Optional[str], project_id: Optional[int], source_id: Optional[int],
                clip_id: Optional[int], session: Optional[str], purpose: Optional[str], model: Optional[str]) -> dict:
        return {"since": since, "until": until, "project_id": project_id, "source_id": source_id, "clip_id": clip_id,
                "session": session, "purpose": purpose, "model": model}

    def call(fn: Callable, *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(get_store(), *args, **kwargs)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/usage/gemini", summary="Траты Gemini: итог и по группам (purpose, model, day, session…)")
    def api_totals(_auth: auth_dep, since: Optional[str] = None, until: Optional[str] = None,
                   project_id: Optional[int] = None, source_id: Optional[int] = None, clip_id: Optional[int] = None,
                   session: Optional[str] = None, purpose: Optional[str] = None, model: Optional[str] = None,
                   group_by: Optional[str] = None, limit: int = Query(200, ge=1, le=1000)) -> dict:
        return call(U.totals, group_by=group_by or None, limit=limit,
                    **filters(since, until, project_id, source_id, clip_id, session, purpose, model))

    @router.get("/api/usage/gemini/calls", summary="Последние запросы к Gemini с токенами и ценой")
    def api_calls(_auth: auth_dep, since: Optional[str] = None, until: Optional[str] = None,
                  project_id: Optional[int] = None, source_id: Optional[int] = None, clip_id: Optional[int] = None,
                  session: Optional[str] = None, purpose: Optional[str] = None, model: Optional[str] = None,
                  limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)) -> dict:
        return call(U.calls, limit=limit, offset=offset,
                    **filters(since, until, project_id, source_id, clip_id, session, purpose, model))

    @router.get("/api/usage/gemini/prices", summary="Цены Gemini (USD за 1M токенов) и курс рубля")
    def api_prices(_auth: auth_dep) -> dict:
        return U.price_config(get_store())

    @router.put("/api/usage/gemini/prices", summary="Правка цен: overrides {модель: {...}} и/или usd_rub")
    def api_set_prices(request: Request, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        owner_only(request, "Цены и курс меняет владелец")
        rate = payload.get("usd_rub")
        return call(U.set_prices, payload.get("overrides") if "overrides" in payload else None,
                    float(rate) if rate not in (None, "") else None)

    @router.post("/api/usage/gemini/recompute", summary="Пересчитать цену записанных запросов по нынешним ценам")
    def api_recompute(request: Request, _auth: auth_dep, payload: dict = Body(default={})) -> dict:
        owner_only(request, "Пересчёт трат запускает владелец")
        return call(U.recompute, since=payload.get("since"), until=payload.get("until"))

    return router
