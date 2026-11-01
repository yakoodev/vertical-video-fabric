"""REST модулей сервиса (app/modules.py): «Настройки → Модули».

``GET /api/modules`` — модули по группам, их состояние, зависимости и живые ресурсы.
``PUT /api/modules`` — переключить (только владелец: сессия браузера, не Bearer-токен
агентов). Каскад зависимостей без ``confirm`` → 409 со списком того, что ещё
выключится/включится; с ``confirm: true`` — применяется.

Плюс ``ModuleGate`` — ASGI-прослойка: адрес выключенного модуля отвечает 423.
"""

# Без from __future__ import annotations: FastAPI читает аннотации _auth: auth_dep
# в момент объявления, а auth_dep — локальная переменная (как в app/layout_api.py).
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app import modules
from app.auth import token_is_valid
from app.settings import settings


class ModulesUpdate(BaseModel):
    modules: dict[str, bool] = Field(description="{id модуля: включён}")
    confirm: bool = Field(default=False, description="согласен на каскад зависимостей")
    dry_run: bool = Field(default=False, description="только показать, что изменится")


def _owner_session(request: Request) -> bool:
    """Переключать модули может только владелец из интерфейса (cookie-сессия). Агенты ходят с
    Bearer-токеном — им модули только читать (list_modules). Без авторизации (dev) — можно."""
    if not settings.auth_enabled:
        return True
    return token_is_valid(request.cookies.get(settings.auth_cookie_name))


def _names(ids: list[str]) -> str:
    return ", ".join(f"«{n}»" for n in modules.names(ids))


def build_modules_router(auth_dep: Any, get_store: Callable[[], Any]) -> APIRouter:
    router = APIRouter(tags=["Modules"])

    @router.get("/api/modules", summary="Модули сервиса: что включено, зависимости, ресурсы")
    def api_modules(_auth: auth_dep) -> dict:
        store = get_store()
        return {**modules.describe(store), "resources": modules.resources(store)}

    @router.put("/api/modules", summary="Включить/выключить модули (только владелец, каскад зависимостей)")
    def api_put_modules(payload: ModulesUpdate, request: Request, _auth: auth_dep) -> Any:
        store = get_store()
        if not payload.modules:
            raise HTTPException(status_code=400, detail="укажи modules: {id: true|false}")
        try:
            plan = modules.plan(payload.modules, store)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        brief = {"changed": plan["changed"], "also_off": plan["also_off"], "also_on": plan["also_on"],
                 "locked": plan["locked"]}
        if plan["locked"]:
            raise HTTPException(status_code=409, detail=str(modules.ModuleLocked(plan["locked"])))
        if payload.dry_run:
            return {"dry_run": True, **brief}
        if not _owner_session(request):
            raise HTTPException(
                status_code=403,
                detail="переключать модули может только владелец — в интерфейсе (Настройки → Модули) "
                       f"или переменными {modules.ENV_DISABLED}/{modules.ENV_ENABLED}; агентам — только list_modules",
            )
        if (plan["also_off"] or plan["also_on"]) and not payload.confirm:
            parts = []
            if plan["also_off"]:
                parts.append("выключится и " + _names(plan["also_off"]))
            if plan["also_on"]:
                parts.append("включится и " + _names(plan["also_on"]))
            return JSONResponse(status_code=409, content={
                "detail": "зависимости: " + "; ".join(parts) + " — повтори с confirm: true",
                "needs_confirm": True, **brief})
        modules.set_modules(store, payload.modules)
        return {**brief, **modules.describe(store), "resources": modules.resources(store)}

    return router


class ModuleGate:
    """ASGI-прослойка: запрос к адресу выключенного модуля → 423 с понятным текстом
    (чистый ASGI, чтобы не мешать потокам SSE чата)."""

    def __init__(self, app: Any, get_store: Callable[[], Any]) -> None:
        self.app = app
        self.get_store = get_store

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") == "http":
            try:
                blocked = modules.blocked_route(scope.get("method", "GET"), scope.get("path", ""), self.get_store())
            except Exception:  # noqa: BLE001 - сбой проверки не закрывает сервис
                blocked = None
            if blocked:
                response = JSONResponse(modules.disabled_payload(blocked), status_code=modules.HTTP_STATUS)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


async def module_disabled_handler(_request: Request, exc: modules.ModuleDisabled) -> JSONResponse:
    """Вызов в выключенный модуль изнутри обработчика (сервисная проверка) → тот же 423."""
    return JSONResponse(modules.disabled_payload(exc.module_id), status_code=modules.HTTP_STATUS)
