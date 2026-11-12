"""Чат проекта: подтверждения и вид нити для интерфейса (из ``app/project_chat.py``)."""

from __future__ import annotations

from typing import Any

from app import autopilot as A
from app import chat_tools as CT
from app import projects as P
from app.settings import settings
from app.project_chat_threads import (
    _clip_title, _loads, get_thread, _video_title, default_thread, add_thread_message, update_message, get_message,
    thread_messages, ai_status, active_run, legacy_clip_messages
)

# --------------------------------------------------------------------------- #
# Подтверждения и откат
# --------------------------------------------------------------------------- #


def _confirmation(row: Any) -> dict:
    data = dict(row)
    data["args"] = _loads(data.pop("args_json", "{}"), {})
    data["result"] = _loads(data.pop("result_json", "{}"), {})
    return data


def get_confirmation(store: Any, confirmation_id: int) -> dict:
    row = store.db.query_one("SELECT * FROM chat_confirmations WHERE id = ?", (int(confirmation_id),))
    if not row:
        raise KeyError(f"подтверждение #{confirmation_id} не найдено")
    return _confirmation(row)


def _patch_step(store: Any, message_id: int, key: str, value: int, **fields: Any) -> None:
    try:
        message = get_message(store, message_id)
    except KeyError:
        return
    steps = message["steps"]
    for step in steps:
        if step.get(key) == value:
            step.update(fields)
    update_message(store, message_id, steps=steps)


def revert_checkpoint(store: Any, checkpoint_id: int) -> dict:
    """«Откатить»: клип — к состоянию до правок агента в этом ответе. Сам откат тоже в истории
    клипа (Ctrl+Z в редакторе вернёт правки агента)."""
    from app.clip_history import restore, snapshot

    row = store.db.query_one("SELECT * FROM chat_checkpoints WHERE id = ?", (int(checkpoint_id),))
    if not row:
        raise KeyError(f"точка отката #{checkpoint_id} не найдена")
    if row["reverted_at"]:
        raise ValueError("уже откачено")
    clip_id = int(row["clip_plan_id"])
    store.get_clip_plan(clip_id, include_segments=False)
    snapshot(store, clip_id, "Чат: перед откатом")
    _fresh, changed = restore(store, clip_id, _loads(row["state_json"], {}))
    store.db.execute("UPDATE chat_checkpoints SET reverted_at = CURRENT_TIMESTAMP WHERE id = ?", (int(checkpoint_id),))
    add_thread_message(store, int(row["thread_id"]), "assistant",
                       f"Откатил правки клипа #{clip_id} из этого ответа.", meta={"service": True})
    return {"ok": True, "clip_plan_id": clip_id, "changes": changed}


# --------------------------------------------------------------------------- #
# Просмотр
# --------------------------------------------------------------------------- #


def _decorate(store: Any, messages: list[dict]) -> list[dict]:
    ids = [m["id"] for m in messages if m["role"] == "assistant"]
    if not ids:
        return messages
    marks = ",".join("?" * len(ids))
    checkpoints: dict[int, list[dict]] = {}
    for row in store.db.query_all(f"SELECT id, message_id, clip_plan_id, reverted_at FROM chat_checkpoints "
                                  f"WHERE message_id IN ({marks}) ORDER BY id", ids):
        brief = CT.clip_brief(store, int(row["clip_plan_id"])) or {"clip_plan_id": int(row["clip_plan_id"])}
        checkpoints.setdefault(int(row["message_id"]), []).append(
            {"checkpoint_id": int(row["id"]), "reverted": bool(row["reverted_at"]), **brief})
    confirmations: dict[int, list[dict]] = {}
    for row in store.db.query_all(f"SELECT * FROM chat_confirmations WHERE message_id IN ({marks}) ORDER BY id", ids):
        conf = _confirmation(row)
        confirmations.setdefault(int(conf["message_id"]), []).append(
            {k: conf[k] for k in ("id", "tool", "args", "label", "status", "result")})
    for m in messages:
        m["changes"] = checkpoints.get(m["id"], [])
        m["confirmations"] = confirmations.get(m["id"], [])
    return messages


def thread_view(store: Any, thread_id: int, after: int = 0) -> dict:
    thread = get_thread(store, thread_id)
    run = active_run(thread_id)
    messages = thread_messages(store, thread_id, after=after)
    for m in messages:
        if m["status"] == "running" and (run is None or run.reply_id != m["id"]):  # сервер перезапускали
            update_message(store, m["id"], status="interrupted", text=m["text"] or "Прервано перезапуском сервера.")
            m["status"], m["text"] = "interrupted", m["text"] or "Прервано перезапуском сервера."
    project = None
    if thread.get("project_id"):
        try:
            p = P.get_project(store, thread["project_id"], include_sources=False)
            project = {"id": p["id"], "name": p["name"], "kind": p.get("kind"),
                       "auto_rules": A.get_config(store, p["id"])["auto_rules"]}
        except KeyError:
            project = None
    video = None
    if thread.get("source_id"):
        video = {"id": int(thread["source_id"]), "title": _video_title(store, int(thread["source_id"]))}
    clip = None
    if thread.get("clip_plan_id"):
        clip_id = int(thread["clip_plan_id"])
        plan = store.get_clip_plan(clip_id, include_segments=False)
        clip = {"id": clip_id, "title": _clip_title(store, clip_id), "source_id": plan.get("source_id")}
    if clip and not after:  # старая переписка клипа — перед новыми репликами, только для чтения
        messages = [*legacy_clip_messages(store, clip["id"]), *messages]
    # Траты Gemini этой переписки: «потрачено N ₽» в шапке и под каждым ответом (app/gemini_usage.py).
    from app.gemini_usage import session_summary

    usage = session_summary(store, f"chat:{int(thread_id)}")
    by_message = usage.pop("by_message", {})
    for m in messages:
        if m.get("role") == "assistant" and str(m.get("id")) in by_message:
            m["usage"] = by_message[str(m["id"])]
    return {"usage": usage, "thread": {k: v for k, v in thread.items()}, "project": project, "video": video, "clip": clip,
            "messages": _decorate(store, messages), "running": run is not None,
            "live": run.snapshot() if run else None, "summary": thread.get("summary") or "",
            "model": thread.get("model") or settings.gemini_agent_model, "ai": ai_status(),
            "tool_groups": thread.get("tool_groups") or []}


def chat_view(store: Any, project_id: int, after: int = 0) -> dict:
    """Основная переписка проекта — для вкладки «Чат» серии (прежний ответ + thread_id)."""
    P.get_project(store, project_id, include_sources=False)
    thread = default_thread(store, project_id)
    view = thread_view(store, thread["id"], after=after)
    state = A._state_row(store, project_id)
    from app import notify_tg

    return {"messages": view["messages"], "running": view["running"], "summary": view["summary"],
            "thread_id": thread["id"], "ai_available": view["ai"]["available"],
            "telegram": {"enabled": notify_tg.is_enabled(store), "thread_id": state.get("tg_thread_id"),
                         "root_message_id": state.get("tg_root_message_id")},
            "auto_rules": A.get_config(store, project_id)["auto_rules"]}
