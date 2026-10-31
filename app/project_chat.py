"""💬 Чат проекта: разговор владельца с агентом серии/канала — в UI (/series/:id) и в Telegram.

Владелец: «было бы круто еще общаться с тем агентом как с тобой. типа на каждый проект
свои чаты. чтобы там дополнительно что то обсуждать и улучшать».

Строится на встроенном ИИ-монтажёре (``app/ai/editor_agent.py``): тот же клиент Gemini
(``editor_agent.client_factory`` — тесты подставляют фейк), та же сборка истории и ответов
инструментов, тот же реестр инструментов (``app/agent_tools.py``). Отличия — масштаб:

* инструменты — проектные (бриф, правила, гипотезы, статистика, одобрение, расписание)
  и несколько клиповых только для чтения (``get_clip`` с ``clip_id``, расшифровка,
  что заходит, плейбуки);
* в контекст каждого хода — свежие бриф, активные правила, статистика, гипотезы,
  очередь одобрения и расписание проекта;
* история хранится по проекту (``project_chat_messages``), модели — последние реплики
  и сжатое резюме старых (``project_autopilot.chat_summary``), а не вся переписка.

Разговор — источник обучения: правилоподобная фраза владельца («такое больше не бери»,
«вот так круто») сразу разбирается эвристикой автопилота (``autopilot.learn_from_text``).
Настройка проекта ``auto_rules``: ``confirm`` (по умолчанию) — карточка «Записать
правило?», ответ «да» в чате её одобряет; ``auto`` — однозначное записывается сразу
(с цитатой). Провайдер — Gemini сервиса; нет ключа или он лёг — так и отвечаем,
правила из сообщения всё равно учтены. Платный Polza не используется.
"""

from __future__ import annotations

import copy
import json
import logging
import threading
from typing import Any, Callable

from app import agent_tools
from app import autopilot as A
from app import projects as P
from app.agent_tools import ImageResult, ToolContext
from app.settings import settings

_log = logging.getLogger(__name__)

HISTORY_KEEP = 12  # последних реплик — модели целиком
SUMMARIZE_AFTER = 30  # больше несжатых реплик — старые уходят в резюме
SUMMARY_CHARS = 3000
CONTEXT_CHARS = 24000
MAX_ROUNDS = 12
# Клиповые инструменты, полезные в разговоре о проекте (только чтение).
CLIP_TOOLS = ("get_clip", "get_transcript", "get_performance", "get_playbook", "list_clips", "list_projects")

CHAT_SCHEMA_SQL = [
    """
    CREATE TABLE IF NOT EXISTS project_chat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        role TEXT NOT NULL,
        text TEXT NOT NULL DEFAULT '',
        steps_json TEXT NOT NULL DEFAULT '[]',
        status TEXT NOT NULL DEFAULT 'done',
        model TEXT NOT NULL DEFAULT '',
        origin TEXT NOT NULL DEFAULT 'ui',
        learned_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_project_chat ON project_chat_messages(project_id, id)",
]


def ensure_chat_schema(conn) -> None:
    for sql in CHAT_SCHEMA_SQL:
        conn.execute(sql)


class ChatBusy(RuntimeError):
    """Агент проекта ещё отвечает на прошлое сообщение."""


# --------------------------------------------------------------------------- #
# Хранение
# --------------------------------------------------------------------------- #


def _msg(row: Any) -> dict:
    data = dict(row)
    data["steps"] = P._loads(data.pop("steps_json", "[]"), [])
    data["learned"] = P._loads(data.pop("learned_json", "{}"), {})
    return data


def add_message(store: Any, project_id: int, role: str, text: str, status: str = "done", origin: str = "ui",
                model: str = "") -> int:
    cur = store.db.execute(
        "INSERT INTO project_chat_messages (project_id, role, text, status, origin, model) VALUES (?, ?, ?, ?, ?, ?)",
        (int(project_id), role, str(text or "")[:8000], status, origin, model))
    return int(cur.lastrowid)


def update_message(store: Any, message_id: int, **fields: Any) -> None:
    if "steps" in fields:
        fields["steps_json"] = json.dumps(fields.pop("steps"), ensure_ascii=False, default=str)
    if "learned" in fields:
        fields["learned_json"] = json.dumps(fields.pop("learned"), ensure_ascii=False, default=str)
    sets = ", ".join(f"{k} = ?" for k in fields)
    store.db.execute(f"UPDATE project_chat_messages SET {sets} WHERE id = ?", (*fields.values(), int(message_id)))


def list_messages(store: Any, project_id: int, after: int = 0, limit: int = 200) -> list[dict]:
    rows = store.db.query_all(
        "SELECT * FROM (SELECT * FROM project_chat_messages WHERE project_id = ? AND id > ? ORDER BY id DESC LIMIT ?) "
        "ORDER BY id", (int(project_id), int(after), int(limit)))
    return [_msg(r) for r in rows]


def clear_chat(store: Any, project_id: int) -> int:
    if _active(project_id):
        raise ChatBusy("агент ещё отвечает — подожди")
    cur = store.db.execute("DELETE FROM project_chat_messages WHERE project_id = ?", (int(project_id),))
    A.set_state(store, project_id, chat_summary="", chat_summary_upto=0)
    return int(cur.rowcount)


# --------------------------------------------------------------------------- #
# Контекст и резюме
# --------------------------------------------------------------------------- #

SYSTEM = """\
Ты — агент проекта-канала в сервисе Vertical Video Fabric: ведёшь серию/канал вместе с владельцем.
Обсуждаешь с ним нарезку, названия, расписание, что заходит, и улучшаешь работу канала.

Как работать:
- Отвечай по-русски, коротко и по делу, как коллега. Не выдумывай: факты — из контекста ниже
  и инструментов (get_project_brief, get_project_stats, list_hypotheses, list_approvals, get_schedule…).
- Публикуется только одобренное владельцем (кроме доверия auto). Клип на публикацию — submit_for_approval,
  сам ничего не публикуй.
- Правила проекта — только словами владельца, с цитатой. Если в сообщении владельца правило или отмена
  правила, сервис уже разобрал его (блок «Сервис уже сделал»): опирайся на это и не дублируй.
  Своё наблюдение — гипотеза (add_hypothesis) или вопрос владельцу (propose_rule), не правило.
- В инструментах проекта project_id — id этого проекта-серии (он ниже); клиповым инструментам нужен clip_id.
- Если чего-то не знаешь или инструмент не сработал — так и скажи.
"""


def project_context(store: Any, project_id: int) -> str:
    """Свежий срез проекта в каждый ход: бриф, правила, статистика, гипотезы, одобрение, расписание."""
    brief = P.project_brief(store, project_id, covered_limit=30)
    stats = P.project_stats(store, project_id, top=3)
    stats.pop("items", None)
    approvals = [{k: a.get(k) for k in ("id", "kind", "status", "title", "question", "decision_comment",
                                          "scheduled_local", "publish_status")}
                 for a in A.list_approvals(store, project_id, status="pending,changes_requested", limit=20)]
    schedule = A.get_schedule(store, project_id)
    cfg = A.get_config(store, project_id)
    data = {
        "project_id": int(project_id), "brief": brief, "stats": stats, "approvals_open": approvals,
        "schedule": {"timezone": schedule["timezone"], "upcoming": [
            {k: u.get(k) for k in ("id", "title", "scheduled_local", "publish_status", "platforms")}
            for u in schedule["upcoming"][:10]]},
        "autopilot": {"trust": cfg["trust"], "slots": cfg["slots"], "per_day": cfg["per_day"],
                      "auto_rules": cfg["auto_rules"]},
    }
    text = json.dumps(data, ensure_ascii=False, default=str)
    return text if len(text) <= CONTEXT_CHARS else text[:CONTEXT_CHARS] + "…(обрезано)"


def _heuristic_summary(old_summary: str, messages: list[dict]) -> str:
    lines = [old_summary] if old_summary else []
    for m in messages:
        who = "Владелец" if m["role"] == "user" else "Агент"
        text = " ".join(str(m.get("text") or "").split())
        if text:
            lines.append(f"{who}: {text[:160]}")
    out = "\n".join(lines)
    return out[-SUMMARY_CHARS:]


def maybe_summarize(store: Any, project_id: int, client: Any = None) -> bool:
    """Старые реплики → резюме (Gemini, если есть; иначе выжимка строк). True — сжали."""
    state = A._state_row(store, project_id)
    upto = int(state.get("chat_summary_upto") or 0)
    fresh = [m for m in list_messages(store, project_id, after=upto, limit=1000) if m["status"] != "running"]
    if len(fresh) <= SUMMARIZE_AFTER:
        return False
    old = fresh[:-HISTORY_KEEP]
    summary = ""
    if client is not None:
        prompt = ("Сожми переписку владельца с агентом проекта в 5–12 пунктов: решения, договорённости, "
                  "просьбы владельца, открытые вопросы. По-русски, без воды.\n\nПрежнее резюме:\n"
                  f"{state.get('chat_summary') or '—'}\n\nНовые реплики:\n" + _heuristic_summary("", old))
        try:
            from app.ai.publish_meta import _extract_response_text

            response = client.generate_content(settings.gemini_text_model,
                                               {"contents": [{"role": "user", "parts": [{"text": prompt}]}]})
            summary = _extract_response_text(response).strip()[:SUMMARY_CHARS]
        except Exception as exc:  # noqa: BLE001 - не сжалось моделью — сожмём строками
            _log.info("chat summary via model failed: %s", exc)
    summary = summary or _heuristic_summary(state.get("chat_summary") or "", old)
    A.set_state(store, project_id, chat_summary=summary, chat_summary_upto=int(old[-1]["id"]))
    return True


# --------------------------------------------------------------------------- #
# Инструменты агента проекта
# --------------------------------------------------------------------------- #


def chat_tools() -> list:
    from app.agent_tools import _project_tools

    tools = list(_project_tools())
    names = {t.name for t in tools}
    for name in CLIP_TOOLS:
        try:
            tool = agent_tools.get_tool(name)
        except KeyError:
            continue
        if tool.name not in names:
            tools.append(tool)
            names.add(tool.name)
    return tools


def _declarations(json_schema: bool = True) -> list[dict]:
    from app.ai.editor_agent import _openapi_schema

    out = []
    for tool in chat_tools():
        schema = json.loads(json.dumps(tool.parameters))
        if tool.scope == "clip":  # как в MCP: клиповому инструменту — clip_id
            schema.setdefault("properties", {})
            schema["properties"] = {"clip_id": {"type": "integer", "description": "id клипа"},
                                    **schema["properties"]}
            schema["required"] = ["clip_id", *[r for r in schema.get("required", []) if r != "clip_id"]]
        decl: dict = {"name": tool.name, "description": tool.description}
        decl["parametersJsonSchema" if json_schema else "parameters"] = schema if json_schema else \
            _openapi_schema(schema)
        out.append(decl)
    return out


def _run_tool(store: Any, project_id: int, name: str, args: dict) -> tuple[Any, bool]:
    allowed = {t.name: t for t in chat_tools()}
    tool = allowed.get(name)
    if tool is None:
        return {"ok": False, "error": f"в чате проекта нет инструмента {name}"}, False
    args = dict(args or {})
    clip_id = None
    if tool.scope == "clip":
        try:
            clip_id = int(args.pop("clip_id"))
        except (KeyError, TypeError, ValueError):
            return {"ok": False, "error": "нужен clip_id"}, False
    elif tool.scope == "project" and args.get("project_id") in (None, "") and args.get("source_id") in (None, ""):
        args["project_id"] = int(project_id)
    return agent_tools.run_tool(ToolContext(store=store, clip_plan_id=clip_id), name, args)


# --------------------------------------------------------------------------- #
# Ход агента
# --------------------------------------------------------------------------- #


def _gemini(client: Any = None) -> Any:
    from app.ai import editor_agent

    if client is not None:
        return client
    if editor_agent.client_factory:
        return editor_agent.client_factory()
    if not settings.gemini_api_key:
        return None
    from app.ai.gemini import GeminiClient

    return GeminiClient(timeout=300)


def _service_note(learned: dict | None, decided: dict | None) -> str:
    parts = []
    if decided:
        parts.append(decided.get("note") or "")
    text = A.describe_learning(learned)
    if text:
        parts.append(text)
    return " ".join(p for p in parts if p).strip()


def _quick_decision(store: Any, project_id: int, text: str, actor: str) -> dict | None:
    """«да»/«нет» в чате — ответ на последнее «Записать правило?» из этого чата."""
    cls = A.classify_reply(text)
    if cls["intent"] not in ("approve", "reject") or cls.get("praise"):
        return None
    row = store.db.query_one(
        "SELECT id FROM approvals WHERE project_id = ? AND kind = 'rule_proposal' AND status = 'pending' "
        "AND submitted_by = 'chat' ORDER BY id DESC LIMIT 1", (int(project_id),))
    if not row:
        return None
    result = A.decide(store, int(row["id"]), cls["intent"], actor=actor)
    from app.notify_tg import decision_note

    return {"approval_id": int(row["id"]), "status": result["status"], "note": decision_note(result)}


def run_turn(store: Any, project_id: int, message: str, *, actor: str = "chat:ui", message_id: int | None = None,
             history: list[dict] | None = None, client: Any = None, on_step: Callable[[list], None] | None = None,
             max_rounds: int = MAX_ROUNDS) -> dict:
    """Один ход: обучение на словах владельца, затем модель ↔ инструменты проекта.
    Возвращает ``{text, steps, learned, decided, ai}``."""
    from app.ai.editor_agent import _clean_reply, _history_contents, _parts_text, _response_object, _step_from_result

    cfg = A.get_config(store, project_id)
    decided = _quick_decision(store, project_id, message, actor)
    learned = None
    if decided is None:
        learned = A.learn_from_text(store, project_id, message, origin="chat",
                                    origin_ref=f"chat#{message_id}" if message_id else "chat",
                                    auto_rules=cfg["auto_rules"])
    note = _service_note(learned, decided)
    gemini = _gemini(client)
    if gemini is None:
        text = ("ИИ-агент проекта сейчас недоступен: в сервисе не настроен GEMINI_API_KEY. "
                "Сообщение сохранено.") + (f"\n{note}" if note else "")
        return {"text": text, "steps": [], "learned": learned, "decided": decided, "ai": False}
    state = A._state_row(store, project_id)
    system = SYSTEM + f"\nПроект: #{project_id}.\n"
    if state.get("chat_summary"):
        system += f"\nРезюме прежней переписки:\n{state['chat_summary']}\n"
    first = f"{message.strip()}\n\n---\nСрез проекта (свежий):\n{project_context(store, project_id)}"
    if note:
        first += f"\n\nСервис уже сделал: {note}"
    contents = _history_contents(history or []) + [{"role": "user", "parts": [{"text": first}]}]
    steps: list[dict] = []
    use_json = True
    used_model = settings.gemini_agent_model
    final = ""
    for rounds in range(max_rounds + 1):
        tools_on = rounds < max_rounds
        payload: dict = {"systemInstruction": {"parts": [{"text": system}]}, "contents": contents,
                         "generationConfig": {"maxOutputTokens": 4096}}
        if tools_on:
            payload["tools"] = [{"functionDeclarations": _declarations(use_json)}]
        try:
            response = gemini.generate_content(used_model, payload)
        except RuntimeError as exc:
            if tools_on and use_json and "parametersJsonSchema" in str(exc):
                use_json = False
                payload["tools"] = [{"functionDeclarations": _declarations(False)}]
                response = gemini.generate_content(used_model, payload)
            else:
                raise
        candidates = (response or {}).get("candidates") if isinstance(response, dict) else None
        if not candidates:
            raise RuntimeError("модель не ответила")
        parts = [p for p in (candidates[0].get("content") or {}).get("parts") or [] if isinstance(p, dict)]
        calls = [p for p in parts if isinstance(p.get("functionCall"), dict)]
        if parts:
            contents.append({"role": "model", "parts": parts})
        if not calls:
            final = _clean_reply(_parts_text(parts))
            break
        responses = []
        for part in calls:
            call = part["functionCall"]
            name = str(call.get("name") or "")
            args = call.get("args") if isinstance(call.get("args"), dict) else {}
            try:
                label = agent_tools.get_tool(name).step_label(args)
            except KeyError:
                label = name
            step = {"tool": name, "label": label, "ok": None}
            steps.append(step)
            result, ok = _run_tool(store, project_id, name, args)
            _step_from_result(step, result, ok)
            if on_step:
                on_step(copy.deepcopy(steps))
            body = _response_object(result) if not isinstance(result, ImageResult) else {"ok": True, "image": True}
            response_part: dict = {"functionResponse": {"name": name, "response": body}}
            if call.get("id"):
                response_part["functionResponse"]["id"] = call["id"]
            responses.append(response_part)
        contents.append({"role": "user", "parts": responses})
    if not final:
        final = note or "Готово."
    return {"text": final, "steps": steps, "learned": learned, "decided": decided, "ai": True, "model": used_model}


# --------------------------------------------------------------------------- #
# Фоновые ходы
# --------------------------------------------------------------------------- #

_RUNS: dict[int, threading.Thread] = {}
_LOCK = threading.Lock()


def _active(project_id: int) -> bool:
    with _LOCK:
        thread = _RUNS.get(int(project_id))
        return bool(thread and thread.is_alive())


def send(store: Any, project_id: int, text: str, *, origin: str = "ui", actor: str = "chat:ui",
         client: Any = None, on_done: Callable[[str], None] | None = None) -> dict:
    """Сообщение владельца → ход агента в фоне. Возвращает {message_id, reply_id}."""
    text = str(text or "").strip()
    if not text:
        raise ValueError("пустое сообщение")
    P.get_project(store, project_id, include_sources=False)
    with _LOCK:
        thread = _RUNS.get(int(project_id))
        if thread and thread.is_alive():
            raise ChatBusy("агент проекта ещё отвечает на прошлое сообщение")
        state = A._state_row(store, project_id)
        history = [m for m in list_messages(store, project_id, after=int(state.get("chat_summary_upto") or 0))
                   if m["status"] != "running"][-HISTORY_KEEP:]
        user_id = add_message(store, project_id, "user", text, origin=origin)
        reply_id = add_message(store, project_id, "assistant", "", status="running", origin=origin)

        def work() -> None:
            def on_step(steps: list) -> None:
                update_message(store, reply_id, steps=steps)

            try:
                result = run_turn(store, project_id, text, actor=actor, message_id=user_id, history=history,
                                  client=client, on_step=on_step)
                update_message(store, reply_id, text=result["text"], steps=result["steps"],
                               learned=result.get("learned") or {}, status="done", model=result.get("model") or "")
                reply = result["text"]
            except Exception as exc:  # noqa: BLE001 - честно: ИИ недоступен
                _log.warning("project chat %s failed: %s", project_id, exc)
                reply = f"ИИ-агент проекта не ответил: {str(exc)[:300]}. Сообщение сохранено, попробуй позже."
                update_message(store, reply_id, text=reply, status="error")
            try:
                maybe_summarize(store, project_id, _gemini(client) if settings.gemini_api_key or client else None)
            except Exception:  # noqa: BLE001
                pass
            if on_done:
                try:
                    on_done(reply)
                except Exception as exc:  # noqa: BLE001
                    _log.info("project chat on_done failed: %s", exc)

        thread = threading.Thread(target=work, name=f"project-chat-{project_id}", daemon=True)
        _RUNS[int(project_id)] = thread
        thread.start()
    return {"message_id": user_id, "reply_id": reply_id}


def wait(project_id: int, timeout: float | None = None) -> bool:
    with _LOCK:
        thread = _RUNS.get(int(project_id))
    if thread is None:
        return True
    thread.join(timeout)
    return not thread.is_alive()


def chat_view(store: Any, project_id: int, after: int = 0) -> dict:
    P.get_project(store, project_id, include_sources=False)
    running = _active(project_id)
    messages = list_messages(store, project_id, after=after)
    for m in messages:
        if m["status"] == "running" and not running:  # сервер перезапускали посреди хода
            update_message(store, m["id"], status="interrupted", text=m["text"] or "Прервано перезапуском сервера.")
            m["status"], m["text"] = "interrupted", m["text"] or "Прервано перезапуском сервера."
    state = A._state_row(store, project_id)
    from app import notify_tg
    from app.ai import editor_agent

    return {"messages": messages, "running": running, "summary": state.get("chat_summary") or "",
            "ai_available": bool(settings.gemini_api_key or editor_agent.client_factory),
            "telegram": {"enabled": notify_tg.is_enabled(store), "thread_id": state.get("tg_thread_id"),
                         "root_message_id": state.get("tg_root_message_id")},
            "auto_rules": A.get_config(store, project_id)["auto_rules"]}


def handle_telegram(store: Any, project_id: int, text: str, actor: str, message_id: int) -> dict:
    """Сообщение в теме проекта (или reply на сообщение бота проекта) → ход агента, ответ — туда же."""
    from app import notify_tg

    def reply(answer: str) -> None:
        notify_tg.send_text(store, answer, project_id=project_id, purpose="chat", reply_to=message_id)

    try:
        return send(store, project_id, text, origin="tg", actor=actor, on_done=reply)
    except ChatBusy as exc:
        reply(f"⏳ {exc}")
        return {"busy": True}
