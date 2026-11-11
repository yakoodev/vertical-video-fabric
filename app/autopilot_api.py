"""HTTP-ручки автопилота канала (app/autopilot.py), Telegram-адаптера (app/notify_tg.py) и чата проекта.

* ``GET /api/approvals`` (project_id, status, kind), ``GET /api/approvals/counts``, ``GET /api/approvals/feed``
* ``POST /api/approvals`` — клип на одобрение (как MCP submit_for_approval) или карточка-вопрос
* ``GET /api/approvals/{id}``, ``POST /api/approvals/{id}/decide`` {action approve|changes|reject|both, comment}
* ``POST /api/approvals/{id}/comment``, ``POST /api/approvals/{id}/schedule`` {action move|next_slot|cancel, at},
  ``POST /api/approvals/{id}/telegram`` — отправить карточку в Telegram ещё раз
* ``GET/PUT /api/projects/{id}/autopilot`` — доверие, слоты, лимиты, аккаунты, сводка, auto_rules
* ``GET /api/projects/{id}/schedule``, ``GET/POST /api/projects/{id}/autopilot/digest`` (просмотр / отправить)
* ``POST /api/projects/{id}/autopilot/reply`` — слова владельца → правило / вопрос / отмена правила
* ``GET/POST/DELETE /api/projects/{id}/chat``, ``POST /api/projects/{id}/chat/telegram`` — чат проекта
* ``GET/PUT/DELETE /api/settings/telegram``, ``POST /api/settings/telegram/test``
"""

# Без from __future__ import annotations: FastAPI читает аннотации _auth: auth_dep сразу.
from typing import Any, Callable

from fastapi import APIRouter, Body, HTTPException, Query, Request

from app import autopilot as A
from app.agent_settings_api import owner_only
from app.auth import is_agent_request
from app import notify_tg
from app import project_chat


def build_autopilot_router(auth_dep: Any, get_store: Callable[[], Any]) -> APIRouter:
    router = APIRouter(tags=["Autopilot"])

    def call(fn: Callable, *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(get_store(), *args, **kwargs)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc).strip("'\"")) from exc
        except project_chat.ChatBusy as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # --- очередь одобрения ---------------------------------------------------- #

    @router.get("/api/approvals", summary="Очередь одобрения")
    def api_approvals(_auth: auth_dep, project_id: int | None = Query(None), status: str | None = Query(None),
                      kind: str | None = Query(None), limit: int = Query(200)) -> list[dict]:
        return call(A.list_approvals, project_id, status=status, kind=kind, limit=limit)

    @router.get("/api/approvals/counts", summary="Сколько карточек в каждом статусе")
    def api_approval_counts(_auth: auth_dep) -> dict:
        return call(A.approval_counts)

    @router.get("/api/approvals/feed", summary="Лента автопилота: решения, авто-публикации, правила")
    def api_feed(_auth: auth_dep, project_id: int | None = Query(None), limit: int = Query(100)) -> list[dict]:
        return call(A.list_events, project_id, limit=limit)

    @router.post("/api/approvals", summary="Клип на одобрение (clip_plan_id) или карточка-вопрос (project_id, kind)")
    def api_submit(request: Request, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        if payload.get("clip_plan_id"):
            return call(A.submit_clip, int(payload["clip_plan_id"]), title=str(payload.get("title") or ""),
                        description=str(payload.get("description") or ""), tags=payload.get("tags"),
                        platforms=payload.get("platforms"), when=payload.get("when"),
                        content_type=payload.get("content_type") or "",
                        submitted_by="agent" if is_agent_request(request) else "ui",
                        cover_sec=payload.get("cover_sec"))
        if not payload.get("project_id"):
            raise HTTPException(status_code=400, detail="нужен clip_plan_id или project_id")
        return call(A.submit_card, int(payload["project_id"]), str(payload.get("kind") or "other"),
                    str(payload.get("question") or ""), payload.get("payload") or {}, submitted_by="ui")

    @router.get("/api/approvals/{approval_id}", summary="Карточка одобрения")
    def api_approval(approval_id: int, _auth: auth_dep) -> dict:
        return call(A.get_approval, approval_id)

    @router.post("/api/approvals/{approval_id}/decide", summary="Публиковать / Переделать / В мусор")
    def api_decide(approval_id: int, request: Request, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        owner_only(request, "Карточку решает владелец: агент может только спросить")
        return call(A.decide, approval_id, str(payload.get("action") or ""), comment=str(payload.get("comment") or ""),
                    actor="ui")

    @router.post("/api/approvals/{approval_id}/comment", summary="Комментарий к решённой карточке (урок проекту)")
    def api_comment(approval_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        return call(A.add_comment, approval_id, str(payload.get("text") or ""), actor="ui")

    @router.post("/api/approvals/{approval_id}/schedule", summary="Перенести / в ближайший слот / отменить публикацию")
    def api_reschedule(approval_id: int, request: Request, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        owner_only(request, "Расписание публикации меняет владелец")
        return call(A.reschedule, approval_id, str(payload.get("action") or "move"), at=payload.get("at"), actor="ui")

    @router.post("/api/approvals/{approval_id}/telegram", summary="Отправить карточку в Telegram ещё раз")
    def api_resend(approval_id: int, _auth: auth_dep) -> dict:
        return call(notify_tg.queue_card, approval_id)

    # --- проект: настройки, расписание, сводка -------------------------------- #

    @router.get("/api/projects/{project_id}/autopilot", summary="Настройки автопилота проекта")
    def api_autopilot(project_id: int, _auth: auth_dep) -> dict:
        return call(A.get_config, project_id)

    @router.put("/api/projects/{project_id}/autopilot", summary="Изменить настройки автопилота (ключи целиком)")
    def api_autopilot_update(project_id: int, request: Request, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        owner_only(request, "Настройки автопилота меняет только владелец")
        return call(A.update_config, project_id, payload)

    @router.get("/api/projects/{project_id}/schedule", summary="Ближайшие публикации и свободные слоты")
    def api_schedule(project_id: int, _auth: auth_dep) -> dict:
        return call(A.get_schedule, project_id)

    @router.get("/api/projects/{project_id}/autopilot/digest", summary="Сводка недели (просмотр)")
    def api_digest(project_id: int, _auth: auth_dep) -> dict:
        return call(A.weekly_digest, project_id)

    @router.post("/api/projects/{project_id}/autopilot/digest", summary="Отправить сводку недели в Telegram")
    def api_digest_send(project_id: int, _auth: auth_dep) -> dict:
        return call(A.send_digest, project_id, actor="ui")

    @router.post("/api/projects/{project_id}/autopilot/reply", summary="Слова владельца → правило / вопрос / отмена")
    def api_reply(project_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        res = call(A.learn_from_text, project_id, str(payload.get("text") or ""), origin="ui",
                   origin_ref=str(payload.get("origin_ref") or ""))
        return {**res, "message": A.describe_learning(res)}

    # --- чат проекта ---------------------------------------------------------- #

    @router.get("/api/projects/{project_id}/chat", summary="Чат проекта: переписка и «агент отвечает»")
    def api_chat(project_id: int, _auth: auth_dep, after: int = Query(0)) -> dict:
        return call(project_chat.chat_view, project_id, after=after)

    @router.post("/api/projects/{project_id}/chat", summary="Написать агенту проекта (ответ — в фоне)")
    def api_chat_send(project_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        return call(project_chat.send, project_id, str(payload.get("text") or ""), origin="ui", actor="chat:ui")

    @router.delete("/api/projects/{project_id}/chat", summary="Очистить чат проекта (правила остаются)")
    def api_chat_clear(project_id: int, _auth: auth_dep) -> dict:
        return {"deleted": call(project_chat.clear_chat, project_id)}

    @router.post("/api/projects/{project_id}/chat/telegram", summary="Завести тему проекта в Telegram")
    def api_chat_topic(project_id: int, _auth: auth_dep) -> dict:
        return call(notify_tg.create_project_topic, project_id)

    # --- настройки Telegram --------------------------------------------------- #

    @router.get("/api/settings/telegram", tags=["Settings"], summary="Telegram-бот сервиса (без секрета)")
    def api_tg(_auth: auth_dep) -> dict:
        return call(notify_tg.status)

    @router.put("/api/settings/telegram", tags=["Settings"], summary="Задать бота: токен, chat_id, тема, прокси")
    def api_tg_save(request: Request, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        # Бот решает карточки публикации: кто может нажимать — решает владелец.
        owner_only(request, "Telegram-бота настраивает только владелец")
        allowed = {k: payload[k] for k in ("token", "chat_id", "thread_id", "enabled", "use_proxy", "public_url",
                                           "allowed_user_ids") if k in payload}
        return call(notify_tg.save, **allowed)

    @router.delete("/api/settings/telegram", tags=["Settings"], summary="Убрать бота (карточки — только в UI)")
    def api_tg_clear(request: Request, _auth: auth_dep) -> dict:
        owner_only(request, "Telegram-бота настраивает только владелец")
        return call(notify_tg.clear)

    @router.post("/api/settings/telegram/test", tags=["Settings"], summary="Проверить бота: тестовое сообщение")
    def api_tg_test(_auth: auth_dep) -> dict:
        return call(notify_tg.test)

    return router
