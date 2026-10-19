"""HTTP-ручки раскладок стрима (app/stream_layout.py).

* ``POST  /api/sources/{id}/detect-facecam`` — найти вебку стримера по ~20 кадрам
  VOD; найденная запоминается на источнике (новые клипы берут её по умолчанию).
* ``GET   /api/sources/{id}/facecam`` — сохранённая вебка источника.
* ``PATCH /api/sources/{id}/facecam`` — задать/сбросить вебку руками.

Роутер собирается функцией, как motion_api: ``main.py`` передаёт зависимость
авторизации и доступ к хранилищу лямбдой (тесты подменяют ``app.main.store``).
"""

# Без from __future__ import annotations: FastAPI читает аннотации _auth: auth_dep
# в момент объявления, а auth_dep — локальная переменная.
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


class FacecamRect(BaseModel):
    x: float
    y: float
    w: float
    h: float


class FacecamPayload(BaseModel):
    cam: FacecamRect | None = None


def build_layout_router(auth_dep: Any, get_store: Callable[[], Any]) -> APIRouter:
    router = APIRouter(tags=["Sources"])

    def _source(source_id: int) -> dict:
        try:
            return get_store().get_source(source_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/api/sources/{source_id}/detect-facecam", summary="Найти вебку стримера автоматически")
    def api_detect_facecam(source_id: int, _auth: auth_dep) -> dict:  # type: ignore[valid-type]
        from app.stream_layout import detect_facecam

        source = _source(source_id)
        try:
            found = detect_facecam(source)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        saved = False
        if found.get("cam"):
            get_store().update_source(source_id, facecam=found["cam"])
            saved = True
        return {**found, "saved": saved}

    @router.get("/api/sources/{source_id}/facecam", summary="Сохранённая вебка источника")
    def api_get_facecam(source_id: int, _auth: auth_dep) -> dict:  # type: ignore[valid-type]
        return {"cam": _source(source_id).get("facecam")}

    @router.patch("/api/sources/{source_id}/facecam", summary="Задать или сбросить вебку источника")
    def api_set_facecam(source_id: int, payload: FacecamPayload, _auth: auth_dep) -> dict:  # type: ignore[valid-type]
        _source(source_id)
        cam = payload.cam.model_dump() if payload.cam else None
        source = get_store().update_source(source_id, facecam=cam)
        return {"cam": source.get("facecam")}

    return router
