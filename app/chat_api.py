"""HTTP-ручки чата Twitch-VOD (docs/twitch-chat.md).

* ``POST   /api/sources/{id}/chat/import``   — импорт в фоне (тело: ``vod``, ``vod_start_sec`` — необязательно)
* ``GET    /api/sources/{id}/chat/status``   — статус импорта
* ``GET    /api/sources/{id}/chat?from=&to=&limit=`` — сообщения (время исходника)
* ``GET    /api/sources/{id}/chat/activity?bucket=10&top=20`` — сообщения/бакет, хайп 0..1, горячие окна
* ``DELETE /api/sources/{id}/chat``          — забыть чат
* ``GET    /api/clip-plans/{id}/chat``       — сообщения в кусках клипа, время КЛИПА

Роутер собирается функцией, как ``app/motion_api.py``: ``main.py`` передаёт свою
зависимость авторизации, доступ к хранилищу (лямбдой — тесты подменяют
``app.main.store``) и ``SourceIngestor`` — на него вешается автоимпорт чата
для только что скачанных Twitch-источников.
"""

# Без from __future__ import annotations: FastAPI читает аннотации вида
# _auth: auth_dep в момент объявления, а auth_dep — локальная переменная.
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.twitch_chat import (
    ChatImportService,
    chat_activity,
    clip_messages,
    delete_chat,
    get_chat_status,
    hype_windows_from_activity,
    list_messages,
)


class ChatImportPayload(BaseModel):
    vod: str = Field("", description="ссылка на Twitch VOD/клип или id VOD; пусто — из original_url источника")
    vod_start_sec: float | None = Field(
        None, ge=0, description="секунда VOD, которой соответствует 0:00 источника (для обрезанных файлов)"
    )


def build_chat_router(
    auth_dep: Any,
    get_store: Callable[[], Any],
    ingestor: Any = None,
    client_factory: Callable[[], Any] | None = None,
) -> APIRouter:
    router = APIRouter(tags=["Chat"])
    service = ChatImportService(get_store, client_factory=client_factory)
    router.chat_service = service  # type: ignore[attr-defined] - для тестов
    if ingestor is not None:
        ingestor.on_source_created = service.auto_import

    def source_or_404(source_id: int) -> dict:
        try:
            return get_store().get_source(source_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/api/sources/{source_id}/chat/import", summary="Импортировать чат Twitch-VOD (в фоне)")
    def api_chat_import(
        source_id: int,
        _auth: auth_dep,  # type: ignore[valid-type]
        payload: ChatImportPayload | None = None,
    ) -> dict:
        source_or_404(source_id)
        payload = payload or ChatImportPayload()
        try:
            return service.start(source_id, vod=payload.vod, vod_start_sec=payload.vod_start_sec)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/sources/{source_id}/chat/status", summary="Статус импорта чата")
    def api_chat_status(source_id: int, _auth: auth_dep) -> dict:  # type: ignore[valid-type]
        source_or_404(source_id)
        return get_chat_status(get_store(), source_id)

    @router.get("/api/sources/{source_id}/chat", summary="Сообщения чата (время исходника)")
    def api_chat_messages(
        source_id: int,
        _auth: auth_dep,  # type: ignore[valid-type]
        start: float | None = Query(None, alias="from", ge=0, description="с секунды исходника"),
        end: float | None = Query(None, alias="to", ge=0, description="до секунды исходника (не включая)"),
        limit: int = Query(500, ge=1, le=20000),
    ) -> dict:
        source_or_404(source_id)
        store = get_store()
        messages = list_messages(store, source_id, start, end, limit)
        return {"chat": get_chat_status(store, source_id), "messages": messages}

    @router.get("/api/sources/{source_id}/chat/activity", summary="Активность чата и «хайп» по бакетам")
    def api_chat_activity(
        source_id: int,
        _auth: auth_dep,  # type: ignore[valid-type]
        bucket: float = Query(10.0, ge=1, le=600, description="ширина бакета, с"),
        top: int = Query(20, ge=0, le=200, description="сколько горячих окон вернуть"),
    ) -> dict:
        source_or_404(source_id)
        activity = dict(chat_activity(get_store(), source_id, bucket))
        activity["windows"] = hype_windows_from_activity(activity, top=top) if top else []
        return activity

    @router.delete("/api/sources/{source_id}/chat", summary="Удалить импортированный чат")
    def api_chat_delete(source_id: int, _auth: auth_dep) -> dict:  # type: ignore[valid-type]
        source_or_404(source_id)
        delete_chat(get_store(), source_id)
        return {"deleted": True}

    @router.get(
        "/api/clip-plans/{clip_plan_id}/chat",
        summary="Чат в кусках клипа: [{t (сек клипа), user, color, text, fragments, badges}]",
    )
    def api_clip_chat(
        clip_plan_id: int,
        _auth: auth_dep,  # type: ignore[valid-type]
        limit: int = Query(5000, ge=1, le=20000),
    ) -> list[dict]:
        store = get_store()
        try:
            plan = store.get_clip_plan(clip_plan_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return clip_messages(store, plan, limit=limit)

    return router
