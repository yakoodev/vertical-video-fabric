"""HTTP-вход ИИ-монтажёра: чат в редакторе и MCP-сервер для внешних агентов.

Чат (``/api/clip-plans/{id}/agent``) — прежний адрес чата клипа: фоновый ход + опрос поверх
обычной переписки, см. ``app/clip_chat.py``. Им пользуются ``tools/vvf.py agent`` и скрипты, браузер ходит в ``/api/chat/*``.

MCP (``/mcp``) — те же инструменты (``app/agent_tools.py``) для Claude Code,
Codex и любого MCP-клиента по транспорту Streamable HTTP. Почему свой небольшой
JSON-RPC, а не официальный SDK ``mcp``: SDK тянет десяток пакетов и требует
встроить свой менеджер сессий в lifespan приложения (main.py), а авторизация
у нас — тот же Bearer-токен, что и у всего API. Нам нужен ровно stateless-срез
протокола: ``initialize``, ``ping``, ``tools/list``, ``tools/call``, ``prompts/list``,
``prompts/get`` (плейбуки docs/playbooks, app/playbooks.py) и уведомления.
Спецификация разрешает серверу отвечать на POST обычным ``application/json`` и не
открывать SSE-поток (GET → 405), так что любой клиент Streamable HTTP с этим работает.
"""

from __future__ import annotations

import json
import re
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
Инструменты монтажа клипов Vertical Video Fabric: вертикаль 9:16, а также 1:1, 4:5 и 16:9.
Порядок: list_channel_projects / list_projects → list_clips(project_id или source_id) → get_clip(clip_id) → правки →
view_frame(clip_id, t), чтобы посмотреть результат; готовый mp4 — render_clip → render_status.
Проект работы: адрес /mcp?project_id=N (или заголовок X-VVF-Project) — list_clips и create_canvas_clip без project_id
берут его; холсты чужих проектов в списках не видны. Клип с project_warning / project_unassigned — не твой: не выдавай
его за свою работу. Время — секунды готового клипа, x/y — доли кадра. Кадр — формата клипа:
get_clip → render.format и render.frame (9:16 — 1080×1920, 1:1 — 1080×1080, 4:5 — 1080×1350,
16:9 — 1920×1080); px слоёв — опорные кадра разметки render.layers_format (render.layers_ref).
Формат выбирает владелец (set_format — только по его просьбе).
Слои: add_layers / update_layer / remove_layers, наезды: set_camera (заменяет все ключи).
Справка по слоям: get_motion_reference(format?); полный контракт — docs/motion/spec.md.
3D/2D-анимация кодом — слой scene: get_scene_reference → preview_scene_frame(scene, t) → add_layers
(docs/motion/scene-engine.md).
Типовые заказы (нарезка стрима, промо, мемы) — плейбуки: get_playbook() — список, get_playbook(name) — шаги
и чек-лист (то же — MCP prompts).
Реклама сайта или товара («реклама <X>»): сам web_search → официальный сайт → page_snapshot главной, товара и цен →
page_edit (подсветка, убрать баннер, crop_to цены — правка через HTML копии) → слой page (поле layer ответа) через
add_layers; ключевой экран — page_to_layers: фон и блоки отдельными слоями (карточку приблизить, кнопку нажать,
цену выпрыгнуть — update_layer). Ссылки у владельца не спрашивай, пока поиск что-то находит.
Каждая правка делает снимок истории: владелец откатит её Ctrl+Z в редакторе.
Длинные описания инструментов здесь укорочены («… Полностью — describe_tool»): правила и примеры — describe_tool(name).
Аргументы сверяются со схемой: лишний или пропущенный — ошибка с ожидаемыми именами и «может, …?».
«Готово»/«отрендерено» — только после render_status со status done.
Что можно без владельца, решает он в настройках проекта: get_agent_settings (ждать ли ответов, стоп на списке клипов,
траты, публикация). Отказ называет настройку и где её менять: перескажи ему, сам не меняй и не обходи."""


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
        # «Запомни» работает и без модели: факт записан, а ответить агент не может.
        note = editor_agent.remember_owner_fact(_store(), clip_plan_id, payload.message)
        raise HTTPException(status_code=400, detail=(f"{note} " if note else "")
                            + "Не настроен GEMINI_API_KEY — ИИ-монтажёру нужна Gemini")
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
        "tools": [tool.public() for tool in agent_tools.list_tools(include_discovery=True, store=_store())],
        "mcp": {"url": "/mcp", "transport": "streamable-http", "auth": "Authorization: Bearer <token>"},
    }


# --------------------------------------------------------------------------- #
# MCP (Streamable HTTP, stateless JSON-RPC)
# --------------------------------------------------------------------------- #


# Длинные описания в tools/list укорочены (эксперимент 12.11: ответ ~70 КБ+, бо́льшая часть — правила и примеры
# десятка инструментов): первые фразы до предела + отсылка к describe_tool. Схемы аргументов — целиком.
MCP_DESCRIPTION_LIMIT = 500


def short_description(name: str, text: str, limit: int = MCP_DESCRIPTION_LIMIT) -> str:
    """Описание для tools/list: целиком, если короче ``limit``; иначе — начало до конца фразы, «полностью —
    describe_tool» и английская сводка (хвост после « / »), если она короткая."""
    text = str(text or "")
    if len(text) <= limit:
        return text
    head, sep, english = text.rpartition(" / ")
    if not sep or len(english) > 240:
        head, english = text, ""
    cut = head[:limit]
    stop = max(cut.rfind(". "), cut.rfind("; "), cut.rfind(".\n"))
    if stop > limit // 2:
        cut = cut[:stop + 1]
    # Запреты и деньги не теряются: такие фразы из отрезанного остаются (коротко).
    rest = head[len(cut):]
    keep = [s.strip() for s in _SENTENCE_RE.split(rest) if s.strip() and _CRITICAL_RE.search(s)]
    kept = " ".join(s if len(s) <= 220 else s[:219] + "…" for s in keep[:3])
    tail = f" … Полностью — describe_tool(name=\"{name}\")."
    short = cut.rstrip() + (f" … {kept}" if kept else "") + tail + (f" / {english}" if english else "")
    return short if len(short) < len(text) - 100 else text  # чуть длиннее предела — не режем: выигрыша нет


_SENTENCE_RE = re.compile(r"(?<=[.!?;])\s+")
# Фразы, которые нельзя отрезать: платное, запреты, «только владелец/с разрешения».
_CRITICAL_RE = re.compile(r"ПЛАТН|платн|confirm|₽|\bНЕ\b|НЕЛЬЗЯ|нельзя|запрещ|только владел|только с разреш|"
                          r"только по «да»|не публикуй|не обходи|сам не", re.UNICODE)


def mcp_tool_list(store: Any = None) -> list[dict]:
    """Инструменты включённых модулей (app/modules.py); выключенные в tools/list не попадают. Все до одного, со
    схемами целиком; длинные описания укорочены (``short_description``) — полностью их даёт describe_tool."""
    out = []
    for tool in agent_tools.list_tools(include_discovery=True, store=store):
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
                "description": short_description(tool.name, tool.description),
                "inputSchema": schema,
                "annotations": {
                    "title": tool.title,
                    "readOnlyHint": not tool.mutating,
                    "destructiveHint": tool.name in ("remove_layers", "clear_layers", "trim_piece", "remove_insert"),
                    "idempotentHint": False,
                    "openWorldHint": tool.name in ("search_web_images", "add_image_from_url", "generate_image",
                                                   "add_video_from_url"),
                },
                **({"_meta": {"vvf/cost": list(tool.cost)}} if tool.cost else {}),
            }
        )
    return out


def mcp_call_tool(store: Any, name: str, arguments: dict, project_hint: int | None = None) -> dict:
    """Вызов инструмента → MCP CallToolResult (text + image). ``project_hint`` — проект работы клиента
    (``/mcp?project_id=N``): списки и новые холсты — в нём."""
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
    elif "clip_id" not in (tool.parameters.get("properties") or {}):
        args.pop("clip_id", None)
    ctx = agent_tools.ToolContext(
        store=store,
        clip_plan_id=clip_id,
        on_first_change=(lambda: snapshot(store, clip_id, f"агент: {tool.title.lower()}")) if clip_id else None,
        project_hint=project_hint,
    )
    result, ok = agent_tools.run_tool(ctx, name, args, validate=True)
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


def mcp_prompt_list() -> list[dict]:
    """Плейбуки (docs/playbooks/*.md) как MCP prompts: тот же текст, что у get_playbook."""
    from app import playbooks

    return [{"name": p["name"], "title": p["title"], "description": p["description"]}
            for p in playbooks.list_playbooks()]


def mcp_get_prompt(name: str) -> dict | None:
    from app import playbooks

    try:
        book = playbooks.get_playbook(name)
    except KeyError:
        return None
    return {
        "description": book["description"] or book["title"],
        "messages": [{"role": "user", "content": {"type": "text", "text": book["text"]}}],
    }


def _rpc_error(request_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def mcp_handle(store: Any, message: Any, project_hint: int | None = None) -> dict | None:
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
                "capabilities": {"tools": {"listChanged": False}, "prompts": {"listChanged": False}},
                "serverInfo": MCP_SERVER_INFO,
                "instructions": MCP_INSTRUCTIONS,
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": mcp_tool_list(store)}
        elif method == "tools/call":
            name = params.get("name")
            if not isinstance(name, str) or not name:
                return _rpc_error(request_id, -32602, "params.name is required")
            arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
            result = mcp_call_tool(store, name, arguments, project_hint)
        elif method == "prompts/list":
            result = {"prompts": mcp_prompt_list()}
        elif method == "prompts/get":
            name = params.get("name")
            if not isinstance(name, str) or not name:
                return _rpc_error(request_id, -32602, "params.name is required")
            prompt = mcp_get_prompt(name)
            if prompt is None:
                return _rpc_error(request_id, -32602, f"Unknown prompt: {name}")
            result = prompt
        elif method == "resources/list":
            result = {"resources": []}
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


def mcp_project(request: Request) -> int | None:
    """Проект работы MCP-клиента: ``/mcp?project_id=N`` или заголовок ``X-VVF-Project``; нет или не число — None."""
    raw = request.query_params.get("project_id") or request.headers.get("x-vvf-project") or ""
    try:
        value = int(str(raw).strip())
    except ValueError:
        return None
    return value if value > 0 else None


@router.post("/mcp", include_in_schema=False)
async def mcp_post(request: Request) -> Response:
    if not _mcp_authorized(request):
        return _unauthorized()
    try:
        message = json.loads(await request.body() or b"null")
    except ValueError:
        return JSONResponse(_rpc_error(None, -32700, "Parse error"), status_code=400)
    store = _store()
    project = mcp_project(request)
    if isinstance(message, list):  # пакет (старые версии протокола)
        replies = [r for r in [await run_in_threadpool(mcp_handle, store, m, project) for m in message] if r is not None]
        return JSONResponse(replies) if replies else Response(status_code=202)
    reply = await run_in_threadpool(mcp_handle, store, message, project)
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
