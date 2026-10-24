"""HTTP-вход ИИ-монтажёра: чат в редакторе и MCP-сервер для внешних агентов.

Чат (``/api/clip-plans/{id}/agent``) — фоновый ход агента + опрос, см.
``app/ai/editor_agent.py``.

MCP (``/mcp``) — те же инструменты (``app/agent_tools.py``) для Claude Code,
Codex и любого MCP-клиента по транспорту Streamable HTTP. Почему свой небольшой
JSON-RPC, а не официальный SDK ``mcp``: SDK тянет десяток пакетов и требует
встроить свой менеджер сессий в lifespan приложения (main.py), а авторизация
у нас — тот же Bearer-токен, что и у всего API. Нам нужен ровно stateless-срез
протокола: ``initialize``, ``ping``, ``tools/list``, ``tools/call`` и уведомления.
Спецификация разрешает серверу отвечать на POST обычным ``application/json`` и не
открывать SSE-поток (GET → 405), так что любой клиент Streamable HTTP с этим работает.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from app import agent_tools
from app.ai import editor_agent
from app.auth import require_auth, token_is_valid
from app.settings import settings

_log = logging.getLogger(__name__)

router = APIRouter()
_auth = [Depends(require_auth)]

MCP_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
MCP_SERVER_INFO = {"name": "vertical-video-fabric", "title": "Vertical Video Fabric", "version": "1.0.0"}
MCP_INSTRUCTIONS = """\
Инструменты монтажа вертикальных клипов Vertical Video Fabric.
Порядок: list_projects → list_clips(project_id) → get_clip(clip_id) → правки → view_frame(clip_id, t),
чтобы посмотреть результат. Время — секунды готового клипа, кадр 1080×1920, x/y — доли кадра.
Слои: add_layers / update_layer / remove_layers, наезды: set_camera (заменяет все ключи).
Справка по слоям: get_motion_reference; полный контракт — docs/motion/spec.md.
Каждая правка делает снимок истории: владелец откатит её Ctrl+Z в редакторе."""


def _store():
    """Хранилище берём из main в момент запроса: тесты подменяют ``app.main.store``."""
    from app import main

    return main.store


# --------------------------------------------------------------------------- #
# Чат в редакторе
# --------------------------------------------------------------------------- #


class AgentMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000, description="что сделать с клипом")
    model: str | None = Field(default=None, max_length=120, description="модель Gemini (по умолчанию — GEMINI_AGENT_MODEL)")
    max_steps: int | None = Field(default=None, ge=1, le=60)


@router.post(
    "/api/clip-plans/{clip_plan_id}/agent",
    tags=["AI agent"],
    summary="💬 Написать ИИ-монтажёру: он сам правит клип (ход идёт в фоне)",
    dependencies=_auth,
)
def api_agent_send(clip_plan_id: int, payload: AgentMessageRequest) -> dict:
    client = editor_agent.client_factory() if editor_agent.client_factory else None
    if client is None and not settings.gemini_api_key:
        raise HTTPException(status_code=400, detail="Не настроен GEMINI_API_KEY — ИИ-монтажёру нужна Gemini")
    try:
        return editor_agent.start_run(
            _store(), clip_plan_id, payload.message, client=client, model=payload.model, max_steps=payload.max_steps
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except editor_agent.AgentBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/api/clip-plans/{clip_plan_id}/agent",
    tags=["AI agent"],
    summary="Переписка с ИИ-монтажёром и живые шаги текущего хода",
    dependencies=_auth,
)
def api_agent_chat(clip_plan_id: int) -> dict:
    try:
        return editor_agent.chat_view(_store(), clip_plan_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/api/clip-plans/{clip_plan_id}/agent/cancel",
    tags=["AI agent"],
    summary="Остановить ход ИИ-монтажёра",
    dependencies=_auth,
)
def api_agent_cancel(clip_plan_id: int) -> dict:
    return {"cancelling": editor_agent.cancel_run(clip_plan_id)}


@router.delete(
    "/api/clip-plans/{clip_plan_id}/agent",
    tags=["AI agent"],
    summary="Очистить переписку с ИИ-монтажёром (правки клипа остаются)",
    dependencies=_auth,
)
def api_agent_clear(clip_plan_id: int) -> dict:
    try:
        return {"deleted": editor_agent.clear_chat(_store(), clip_plan_id)}
    except editor_agent.AgentBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/api/agent/tools", tags=["AI agent"], summary="Инструменты ИИ-монтажёра (общие с MCP)", dependencies=_auth)
def api_agent_tools() -> dict:
    return {
        "model": settings.gemini_agent_model,
        "max_steps": settings.agent_max_steps,
        "tools": [tool.public() for tool in agent_tools.list_tools(include_discovery=True)],
        "mcp": {"url": "/mcp", "transport": "streamable-http", "auth": "Authorization: Bearer <token>"},
    }


# --------------------------------------------------------------------------- #
# MCP (Streamable HTTP, stateless JSON-RPC)
# --------------------------------------------------------------------------- #


def mcp_tool_list() -> list[dict]:
    out = []
    for tool in agent_tools.list_tools(include_discovery=True):
        schema = json.loads(json.dumps(tool.parameters))
        if tool.scope == "clip":
            schema.setdefault("properties", {})
            schema["properties"] = {
                "clip_id": {"type": "integer", "description": "id клипа (см. list_clips)"},
                **schema["properties"],
            }
            schema["required"] = ["clip_id", *[r for r in schema.get("required", []) if r != "clip_id"]]
        out.append(
            {
                "name": tool.name,
                "title": tool.title,
                "description": tool.description,
                "inputSchema": schema,
                "annotations": {
                    "title": tool.title,
                    "readOnlyHint": not tool.mutating,
                    "destructiveHint": tool.name in ("remove_layers", "clear_layers", "trim_piece", "remove_insert"),
                    "idempotentHint": False,
                    "openWorldHint": tool.name in ("search_web_images", "add_image_from_url", "generate_image",
                                                   "add_video_from_url"),
                },
            }
        )
    return out


def mcp_call_tool(store: Any, name: str, arguments: dict) -> dict:
    """Вызов инструмента → MCP CallToolResult (text + image)."""
    from app.clip_history import snapshot

    try:
        tool = agent_tools.get_tool(name)
    except KeyError:
        return {"content": [{"type": "text", "text": f"нет инструмента {name}"}], "isError": True}
    args = dict(arguments or {})
    clip_id = None
    if tool.scope == "clip":
        try:
            clip_id = int(args.pop("clip_id"))
        except (KeyError, TypeError, ValueError):
            return {"content": [{"type": "text", "text": "нужен clip_id (см. list_clips)"}], "isError": True}
    else:
        args.pop("clip_id", None)
    ctx = agent_tools.ToolContext(
        store=store,
        clip_plan_id=clip_id,
        on_first_change=(lambda: snapshot(store, clip_id, f"агент: {tool.title.lower()}")) if clip_id else None,
    )
    result, ok = agent_tools.run_tool(ctx, name, args)
    if isinstance(result, agent_tools.ImageResult):
        data, mime = editor_agent._shrink_frame(result)
        import base64

        return {
            "content": [
                {"type": "image", "data": base64.b64encode(data).decode("ascii"), "mimeType": mime},
                {"type": "text", "text": agent_tools.dumps(result)},
            ],
            "isError": False,
        }
    payload = agent_tools.result_for_json(result)
    out: dict = {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, default=str)}],
        "isError": not ok,
    }
    if isinstance(payload, dict):
        out["structuredContent"] = json.loads(json.dumps(payload, ensure_ascii=False, default=str))
    return out


def _rpc_error(request_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def mcp_handle(store: Any, message: Any) -> dict | None:
    """Один JSON-RPC объект → ответ (None для уведомлений и ответов клиента)."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _rpc_error(None, -32600, "Invalid Request")
    method = message.get("method")
    request_id = message.get("id")
    is_notification = "id" not in message
    if method is None:  # ответ клиента на наш запрос — нам не нужен
        return None
    if is_notification:
        return None
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    try:
        if method == "initialize":
            asked = str(params.get("protocolVersion") or "")
            version = asked if asked in MCP_PROTOCOL_VERSIONS else MCP_PROTOCOL_VERSIONS[0]
            result: dict = {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": MCP_SERVER_INFO,
                "instructions": MCP_INSTRUCTIONS,
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": mcp_tool_list()}
        elif method == "tools/call":
            name = params.get("name")
            if not isinstance(name, str) or not name:
                return _rpc_error(request_id, -32602, "params.name is required")
            arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
            result = mcp_call_tool(store, name, arguments)
        elif method in ("resources/list", "prompts/list"):
            result = {method.split("/")[0]: []}
        else:
            return _rpc_error(request_id, -32601, f"Method not found: {method}")
    except Exception as exc:  # noqa: BLE001 - ошибка одного вызова не роняет сервер
        _log.warning("MCP %s failed: %s", method, exc)
        return _rpc_error(request_id, -32603, f"Internal error: {str(exc)[:300]}")
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _mcp_authorized(request: Request) -> bool:
    if not settings.auth_enabled:
        return True
    header = request.headers.get("authorization") or ""
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    return token_is_valid(token) or token_is_valid(request.cookies.get(settings.auth_cookie_name))


def _unauthorized() -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": "Authentication required: Bearer <token>"}},
        status_code=401,
        headers={"WWW-Authenticate": "Bearer"},
    )


@router.post("/mcp", include_in_schema=False)
async def mcp_post(request: Request) -> Response:
    if not _mcp_authorized(request):
        return _unauthorized()
    try:
        message = json.loads(await request.body() or b"null")
    except ValueError:
        return JSONResponse(_rpc_error(None, -32700, "Parse error"), status_code=400)
    store = _store()
    if isinstance(message, list):  # пакет (старые версии протокола)
        replies = [r for r in [await run_in_threadpool(mcp_handle, store, m) for m in message] if r is not None]
        return JSONResponse(replies) if replies else Response(status_code=202)
    reply = await run_in_threadpool(mcp_handle, store, message)
    if reply is None:
        return Response(status_code=202)
    return JSONResponse(reply)


@router.get("/mcp", include_in_schema=False)
def mcp_get(request: Request) -> Response:
    """SSE-поток сервер→клиент не нужен: все ответы приходят на POST."""
    if not _mcp_authorized(request):
        return _unauthorized()
    return Response(status_code=405, headers={"Allow": "POST"})


@router.delete("/mcp", include_in_schema=False)
def mcp_delete(request: Request) -> Response:
    if not _mcp_authorized(request):
        return _unauthorized()
    return Response(status_code=405, headers={"Allow": "POST"})
