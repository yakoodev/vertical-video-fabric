"""REST раздела «Как работает агент» и проверка владельца для всего, что касается его решений.

Менять настройки, одобрять и публиковать напрямую может только владелец: Bearer без куки входа получает 403,
как у ответов на вопросы (app/stream_questions_api.py). Читать настройки могут все.

``AgentStopGate`` держит настройку «Останавливаться на списке клипов» на всех адресах клипа сразу: агент с токеном
получает 409 на любой правке черновика, который владелец не выбрал, а любая правка самого владельца этот черновик
берёт в работу. Просмотр (кадры, уровни, проверка без записи) сюда не попадает.
"""

# Без from __future__ import annotations: FastAPI читает аннотации _auth: auth_dep сразу.
import re
from typing import Any, Callable

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse

from app import agent_settings as S
from app.auth import is_agent_request, token_is_valid
from app.settings import settings


def owner_only(request: Request, detail: str = "") -> None:
    if is_agent_request(request):
        raise HTTPException(status_code=403, detail=detail or f"Это решает владелец, агент не может: {S.WHERE}")


def refuse_agent_drafts(request: Request, store: Any, clip_plan_ids: Any) -> None:
    """Пачка клипов от агента: черновик, которого владелец не выбрал, останавливает всю пачку.
    Тот же адрес у владельца берёт все эти клипы в работу."""
    if not is_agent_request(request):
        S.set_picked_many(store, clip_plan_ids)
        return
    refusal = S.drafts_refusal(store, clip_plan_ids)
    if refusal:
        raise HTTPException(status_code=409, detail=refusal["error"])


def build_agent_settings_router(auth_dep: Any, get_store: Callable[[], Any]) -> APIRouter:
    router = APIRouter(tags=["Autopilot"])

    @router.get("/api/projects/{project_id}/agent-settings", summary="Как работает агент: читать могут все")
    def api_settings(project_id: int, _auth: auth_dep) -> dict:
        try:
            return S.view(get_store(), project_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Проекта нет") from exc

    @router.put("/api/projects/{project_id}/agent-settings", summary="Как работает агент: менять может только владелец")
    def api_save(project_id: int, request: Request, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        owner_only(request, f"Настройки агента меняет только владелец: {S.WHERE}")
        try:
            return S.save_settings(get_store(), project_id, payload, actor="ui")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Проекта нет") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router


_CLIP_PATH = re.compile(r"^/api/clip-plans/(\d+)(/.*)?$")
_SEGMENT_PATH = re.compile(r"^/api/segments/(\d+)(/.*)?$")
# Что не меняет клип: просмотр и проверка без записи. Звёздочкой (flags) занимается сам адрес.
_NOT_AN_EDIT = {"/flags", "/chat", "/contact-sheet", "/preview-pieces", "/stream", "/levels", "/lint", "/motion/frame",
                "/spec/validate", "/layers/validate", "/cuts/suggest", "/waveform", "/voices/identify", "/agent/cancel",
                "/stream-questions", "/tags"}
_WRITE = {"POST", "PUT", "PATCH", "DELETE"}
# Прямая публикация и рендер старых форм: агент ходит только через submit_for_approval и адреса клипа.
_AGENT_NEVER = re.compile(r"^(/api/clips/\d+/posts|/api/clips/posts-batch|/api/posts|/ui/posts|/ui/clips/\d+/posts|"
                          r"/ui/sources/\d+/render-plans|/ui/segments/\d+/render)$")


def _bearer(request: Request) -> str:
    return request.headers.get("authorization", "").split(" ", 1)[-1].strip()


class AgentStopGate:
    """Чистый ASGI, как ``ModuleGate``: потоки SSE чата и загрузки не задерживает."""

    def __init__(self, app: Any, get_store: Callable[[], Any]) -> None:
        self.app = app
        self.get_store = get_store

    def _refusal(self, scope: dict) -> tuple[int, str] | None:
        method = scope.get("method") or "GET"
        if method not in _WRITE:
            return None
        if _AGENT_NEVER.match(scope.get("path") or ""):
            request = Request(scope)
            if is_agent_request(request) and token_is_valid(_bearer(request)):
                return 403, (f"Напрямую публикует только владелец. Агент отправляет клип через submit_for_approval, а "
                             f"сам публикует только при настройке «{S.NAME_PUBLISH}»: {S.WHERE}.")
            return None
        segment = _SEGMENT_PATH.match(scope.get("path") or "")
        if segment:
            return self._segment_refusal(scope, int(segment.group(1)))
        match = _CLIP_PATH.match(scope.get("path") or "")
        if not match:
            return None
        tail = (match.group(2) or "").rstrip("/")
        # Удаление самого клипа правкой не считаем: у него своя кнопка и своя проверка.
        if tail in _NOT_AN_EDIT or (method == "DELETE" and not tail):
            return None
        request = Request(scope)
        store, clip_plan_id = self.get_store(), int(match.group(1))
        if is_agent_request(request):
            if not token_is_valid(_bearer(request)):
                return None  # ответит проверка входа
            refusal = S.clip_list_refusal(store, clip_plan_id)
            return (409, refusal["error"]) if refusal else None
        if token_is_valid(request.cookies.get(settings.auth_cookie_name)):
            S.set_picked(store, clip_plan_id)
        return None

    def _segment_refusal(self, scope: dict, segment_id: int) -> tuple[int, str] | None:
        """Сегмент принадлежит клипам: правка куска — правка этих клипов."""
        request = Request(scope)
        store = self.get_store()
        plans = [int(r["clip_plan_id"]) for r in store.db.query_all(
            "SELECT clip_plan_id FROM clip_plan_segments WHERE segment_id = ?", (segment_id,))]
        if is_agent_request(request):
            if not token_is_valid(_bearer(request)):
                return None
            refusal = S.drafts_refusal(store, plans)
            return (409, refusal["error"]) if refusal else None
        if token_is_valid(request.cookies.get(settings.auth_cookie_name)):
            S.set_picked_many(store, plans)
        return None

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") == "http":
            try:
                refused = self._refusal(scope)
            except Exception:  # noqa: BLE001 - сбой проверки не закрывает сервис
                refused = None
            if refused:
                await JSONResponse({"detail": refused[1]}, status_code=refused[0])(scope, receive, send)
                return
        await self.app(scope, receive, send)
