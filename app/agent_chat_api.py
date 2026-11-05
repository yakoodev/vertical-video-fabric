"""HTTP-ручки страницы «Чат» (переписки с агентом, ``app/project_chat.py``).

* ``GET  /api/chat/threads`` — переписки: по проектам и общие
* ``POST /api/chat/threads`` {project_id?, title?, model?} — новая переписка
* ``POST /api/chat/video-thread`` {source_id} — чат видео (один на видео)
* ``GET/PATCH/DELETE /api/chat/threads/{id}`` — переписка (реплики, шаги, живой ход) / имя, модель / удалить
* ``POST /api/chat/threads/{id}/messages`` {text, attachments?} — написать (ответ — в фоне)
* ``GET  /api/chat/threads/{id}/stream`` — SSE живого хода: text, steps, phase, done
* ``POST /api/chat/threads/{id}/cancel`` — «Стоп»; ``DELETE /api/chat/threads/{id}/messages`` — очистить
* ``POST /api/chat/confirmations/{id}`` {approve} — карточка опасного действия
* ``POST /api/chat/checkpoints/{id}/revert`` — «Откатить» правки клипа из ответа
* ``GET  /api/chat/models`` — провайдер и модели для шапки; ``GET /api/chat/media/{name}`` — кадры превью
"""

# Без from __future__ import annotations: FastAPI читает аннотации _auth: auth_dep сразу.
import asyncio
import json
import re
from typing import Any, Callable

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from app import chat_tools
from app import project_chat as C

_MEDIA_RE = re.compile(r"^[0-9a-f]{32}\.(jpg|png)$")
POLL_SEC = 0.08
KEEPALIVE_SEC = 15.0


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def build_agent_chat_router(auth_dep: Any, get_store: Callable[[], Any],
                            services: dict[str, Callable[[], Any]] | None = None) -> APIRouter:
    router = APIRouter(tags=["Chat"])
    chat_tools.SERVICES.update(services or {})

    def call(fn: Callable, *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(get_store(), *args, **kwargs)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc).strip("'\"")) from exc
        except C.ChatBusy as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/chat/threads", summary="Переписки с агентом: по проектам и общие")
    def api_threads(_auth: auth_dep) -> dict:
        return call(C.list_threads)

    @router.post("/api/chat/threads", summary="Новая переписка (project_id — чат проекта, без него — общий)")
    def api_thread_create(_auth: auth_dep, payload: dict = Body(default={})) -> dict:
        project_id = payload.get("project_id")
        return call(C.create_thread, int(project_id) if project_id else None, title=str(payload.get("title") or ""),
                    model=str(payload.get("model") or ""))

    @router.post("/api/chat/video-thread", summary="Чат видео: один на видео, заводится при первом открытии")
    def api_video_thread(_auth: auth_dep, payload: dict = Body(...)) -> dict:
        return call(C.video_thread, int(payload.get("source_id") or 0))

    @router.get("/api/chat/threads/{thread_id}", summary="Переписка: реплики, шаги, живой ход")
    def api_thread(thread_id: int, _auth: auth_dep) -> dict:
        return call(C.thread_view, thread_id)

    @router.patch("/api/chat/threads/{thread_id}", summary="Имя переписки, модель")
    def api_thread_update(thread_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        return call(C.update_thread, thread_id, title=payload.get("title"), model=payload.get("model"))

    @router.delete("/api/chat/threads/{thread_id}", summary="Удалить переписку")
    def api_thread_delete(thread_id: int, _auth: auth_dep) -> dict:
        return call(C.delete_thread, thread_id)

    @router.post("/api/chat/threads/{thread_id}/messages", summary="Написать агенту (ответ — в фоне, поток — /stream)")
    def api_thread_send(thread_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        attachments = payload.get("attachments") if isinstance(payload.get("attachments"), list) else []
        return call(C.send_to_thread, thread_id, str(payload.get("text") or ""), origin="ui", actor="chat:ui",
                    attachments=attachments)

    @router.delete("/api/chat/threads/{thread_id}/messages", summary="Очистить переписку (правила остаются)")
    def api_thread_clear(thread_id: int, _auth: auth_dep) -> dict:
        return {"deleted": call(C.clear_thread, thread_id)}

    @router.post("/api/chat/threads/{thread_id}/cancel", summary="Остановить ответ агента")
    def api_thread_cancel(thread_id: int, _auth: auth_dep) -> dict:
        return {"cancelled": C.cancel(thread_id)}

    @router.get("/api/chat/threads/{thread_id}/stream", summary="SSE живого ответа: text, steps, phase, done")
    async def api_thread_stream(thread_id: int, request: Request, _auth: auth_dep) -> StreamingResponse:
        call(C.get_thread, thread_id)

        async def events():
            run = C.active_run(thread_id)
            if run is None:
                yield _sse("done", {"reply_id": None})
                return
            text_seq = steps_seq = -1
            idle = 0.0
            while True:
                snap = run.snapshot()
                sent = False
                if snap["steps_seq"] != steps_seq:
                    steps_seq = snap["steps_seq"]
                    yield _sse("steps", {"steps": snap["steps"], "phase": snap["phase"], "reply_id": snap["reply_id"]})
                    sent = True
                if snap["text_seq"] != text_seq:
                    text_seq = snap["text_seq"]
                    yield _sse("text", {"text": snap["text"], "reply_id": snap["reply_id"]})
                    sent = True
                if snap["done"]:
                    yield _sse("done", {"reply_id": snap["reply_id"]})
                    return
                if await request.is_disconnected():
                    return
                idle = 0.0 if sent else idle + POLL_SEC
                if idle >= KEEPALIVE_SEC:
                    idle = 0.0
                    yield ": keepalive\n\n"
                await asyncio.sleep(POLL_SEC)

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @router.post("/api/chat/confirmations/{confirmation_id}", summary="Подтвердить / отклонить опасное действие агента")
    def api_confirm(confirmation_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        return call(C.decide_confirmation, confirmation_id, bool(payload.get("approve")), actor="chat:ui")

    @router.post("/api/chat/checkpoints/{checkpoint_id}/revert", summary="Откатить правки клипа из ответа агента")
    def api_revert(checkpoint_id: int, _auth: auth_dep) -> dict:
        return call(C.revert_checkpoint, checkpoint_id)

    @router.get("/api/chat/clips/{clip_plan_id}", summary="Клип для плеера в чате: источник, рендер, обложка")
    def api_clip(clip_plan_id: int, _auth: auth_dep) -> dict:
        brief = chat_tools.clip_brief(get_store(), clip_plan_id)
        if brief is None:
            raise HTTPException(status_code=404, detail=f"клип #{clip_plan_id} не найден")
        return brief

    @router.get("/api/chat/models", summary="Провайдер и модели чата")
    def api_models(_auth: auth_dep) -> dict:
        return C.models()

    @router.get("/api/chat/media/{name}", include_in_schema=False)
    def api_media(name: str, _auth: auth_dep) -> FileResponse:
        if not _MEDIA_RE.match(name):
            raise HTTPException(status_code=404, detail="нет такого кадра")
        path = chat_tools.media_dir() / name
        if not path.exists():
            raise HTTPException(status_code=404, detail="нет такого кадра")
        return FileResponse(path, headers={"Cache-Control": "private, max-age=604800"})

    return router
