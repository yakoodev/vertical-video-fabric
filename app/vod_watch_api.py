"""HTTP-ручки наблюдателя VOD (app/vod_watch.py): ``GET/PUT /api/projects/{id}/vod-watch``,
``POST …/vod-watch/check`` (проверить сейчас), ``POST …/vod-watch/items/{item}/queue`` (VOD — в очередь)."""

# Без from __future__ import annotations: FastAPI читает аннотации _auth: auth_dep сразу.
from typing import Any, Callable

from app.vod_watch import advance, check_project, get_item, get_watch, queue_item, update_config


def build_vod_watch_router(auth_dep: Any, get_store: Callable[[], Any], get_services: Callable[[], tuple]) -> Any:
    """``GET/PUT /api/projects/{id}/vod-watch``, ``POST …/vod-watch/check``, ``POST …/vod-watch/items/{item}/queue``."""
    from fastapi import APIRouter, Body, HTTPException

    router = APIRouter(tags=["Autopilot"])

    def call(fn: Callable, *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(get_store(), *args, **kwargs)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc).strip("'\"")) from exc
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/projects/{project_id}/vod-watch", summary="Наблюдатель VOD проекта: настройки и VOD")
    def api_get(project_id: int, _auth: auth_dep) -> dict:
        return call(get_watch, project_id)

    @router.put("/api/projects/{project_id}/vod-watch", summary="Настройки наблюдателя VOD (частично)")
    def api_put(project_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        call(update_config, project_id, payload)
        return call(get_watch, project_id)

    @router.post("/api/projects/{project_id}/vod-watch/check", summary="Проверить канал сейчас")
    def api_check(project_id: int, _auth: auth_dep) -> dict:
        result = call(check_project, project_id, force=True)
        call(advance, *get_services())
        return {**result, **call(get_watch, project_id)}

    @router.post("/api/projects/{project_id}/vod-watch/items/{item_id}/queue", summary="VOD — в очередь нарезки")
    def api_queue(project_id: int, item_id: int, _auth: auth_dep, payload: dict = Body(default={})) -> dict:
        if call(get_item, item_id)["project_id"] != project_id:
            raise HTTPException(status_code=404, detail="VOD не из этого проекта")
        call(queue_item, item_id, str((payload or {}).get("cutter") or "pipeline"))
        call(advance, *get_services())
        return call(get_watch, project_id)

    return router
