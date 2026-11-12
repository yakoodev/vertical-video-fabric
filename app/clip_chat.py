"""Чат клипа — обычная переписка (``chat_threads.clip_plan_id``), а ход в ней делает ИИ-монтажёр.

Что здесь:

* **Ход** (``run_clip_turn``): модель и проверки перед сдачей — ``editor_agent.run_agent``, как и было. От переписки ему
  даются поток текста в браузер, карточка опасного (``ClipHooks.hold``), превью в шагах (``ClipHooks.decorate``) и точка
  отката: снимок истории «ИИ: …» для Ctrl+Z и копия клипа для кнопки «Откатить».
* **Прежний адрес** ``/api/clip-plans/{id}/agent`` (``tools/vvf.py agent``, MCP-скрипты): те же ответы поверх переписки.
* **Старая переписка** в ``clip_agent_messages`` не копируется: строки лежат где лежали, новых туда не пишем, читает их
  ``project_chat_threads.legacy_clip_messages``.

Чем чат клипа отличается от чата проекта: правила из его реплик не записываются («убери этот мем» — про этот клип, а не на
будущее), «запомни: …» записывается в факты проекта клипа. Инструментов лора и правил у него нет, чужой проект недостижим.
"""

from __future__ import annotations

from typing import Any, Callable

from app import agent_tools
from app import chat_tools as CT
from app import project_chat as C
from app import project_knowledge as K
from app.project_chat_threads import (
    active_clip_run, ChatBusy, clip_thread, find_clip_thread, legacy_clip_messages, wait_clip_run,
)
from app.settings import settings

CLIP_NOTE = (
    "\n\nЭТОТ ЧАТ\n"
    "Владелец пишет из чата этого клипа. Правки откатываются кнопкой «Откатить» в чате и Ctrl+Z в редакторе.\n"
    "Опасное (снять все слои) и платное (платная озвучка) сервис сразу не выполняет: владелец подтверждает карточкой в "
    "чате. Ответ pending_confirmation — скажи, что ждёшь подтверждения, и не повторяй вызов.\n"
    "Приложенные файлы: «файл для монтажа #N» — asset_id для add_insert и слоёв; «источник #N» — source_id.\n"
    "Новую серию или работу на весь проект делает чат проекта: скажи владельцу открыть его.\n"
    "Готовый ролик (mp4) — только render_clip → render_status(render_id, wait_sec=30) до status done. Просят "
    "«отрендеренный клип», «готовый ролик», «результат» — запусти render_clip сам и дождись done. Не пиши "
    "«отрендерен», «готов», «соберётся автоматически», пока render_status не ответил done: сам сервис клип не "
    "рендерит, а ответ о готовом ролике без рендера он пометит.\n"
)


class ClipHooks:
    """Что переписка добавляет к ходу монтажёра (``editor_agent.ClipPolicy``)."""

    def __init__(self, store: Any, thread: dict, reply_id: int | None) -> None:
        self.store = store
        self.thread = thread
        self.reply_id = reply_id
        self.clip_id = int(thread["clip_plan_id"])
        self.project_id = thread.get("project_id")

    def prompt_note(self) -> str:
        summary = self.thread.get("summary")
        return CLIP_NOTE + (f"\nРезюме прежней переписки:\n{summary}\n" if summary else "")

    def hold(self, step: dict, name: str, args: dict) -> dict | None:
        """Опасное и платное — в карточку, как в любом чате. Что монтажёру не объявлено, карточкой не становится:
        ни публикации, ни удаления отсюда нет, такой вызов ответит «нет инструмента»."""
        tool = agent_tools.registry().get(name)
        if tool is None:
            return None
        bound = {**args, "clip_id": self.clip_id} if tool.scope == "clip" else args
        if not CT.is_dangerous(self.store, name, bound, self.project_id):
            return None
        return C.hold_for_owner(self.store, self.thread["id"], self.reply_id, step, name, bound, step.get("label") or name)

    def decorate(self, step: dict, name: str, result: Any, ok: bool) -> None:
        step["result"] = CT.result_preview(result)
        tool = agent_tools.registry().get(name)
        if tool is not None and tool.scope == "clip":
            step["clip_id"] = self.clip_id
        preview = CT.preview_for(self.store, name, result, self.clip_id) if ok else None
        # Карточка этого же клипа в его чате ни к чему: зато кадры и новые клипы показываем.
        if preview and not (preview.get("kind") == "clip" and preview.get("clip_plan_id") == self.clip_id):
            step["preview"] = preview


def remember(store: Any, thread: dict, message: str) -> dict | None:
    """«Запомни: …» в чате клипа → факт проекта этого клипа."""
    clip_id = int(thread["clip_plan_id"])
    try:
        source_id = store.get_clip_plan(clip_id, include_segments=False).get("source_id")
        return K.remember_from_chat(store, thread.get("project_id"), message.split("\n[Прикреплён")[0],
                                    origin_ref=f"clip#{clip_id}", clip_plan_id=clip_id,
                                    source_id=int(source_id) if source_id else None)
    except Exception as exc:  # noqa: BLE001 - разговор важнее записи
        C._log.info("remember fact from clip chat %s failed: %s", clip_id, exc)
        return None


def run_clip_turn(store: Any, thread: dict, message: str, *, reply_id: int | None = None,
                  history: list[dict] | None = None, client: Any = None,
                  on_step: Callable[[list], None] | None = None, run: Any = None, model: str | None = None,
                  max_steps: int | None = None) -> dict:
    """Ход в чате клипа: ответ в том же виде, что у ``project_chat.run_turn``."""
    from app.ai import editor_agent
    from app.clip_history import snapshot

    clip_id = int(thread["clip_plan_id"])
    remembered = remember(store, thread, message)
    note = K.remembered_note(remembered)
    gemini = C._gemini(client, timeout=600)
    if gemini is None:
        text = ("ИИ-агент сейчас недоступен: в сервисе не настроен GEMINI_API_KEY. Сообщение сохранено. "
                "Что сделать: добавь ключ Gemini из AI Studio в .env сервиса и перезапусти его.") \
            + (f"\n{note}" if note else "")
        return {"text": text, "steps": [], "remembered": remembered, "ai": False, "changes": []}
    changes: list[dict] = []

    def first_change() -> None:
        label = f"ИИ: {C._short(message)}"
        if reply_id is None:
            snapshot(store, clip_id, label)
            return
        checkpoint = C._checkpoint(store, thread["id"], reply_id, clip_id, label)
        if checkpoint:
            changes.append({"checkpoint_id": checkpoint, "clip_plan_id": clip_id})

    prompt = f"{message}\n\nСервис уже сделал: {note}" if note else message
    used_model = model or thread.get("model") or settings.gemini_agent_model
    from app.ai.render_claims import db_now, with_render_note

    started = db_now(store)
    result = editor_agent.run_agent(store, clip_id, prompt, client=gemini, model=used_model, history=history,
                                    on_step=on_step, run=run, max_steps=max_steps, on_first_change=first_change,
                                    hooks=ClipHooks(store, thread, reply_id))
    text = f"{note}\n\n{result['text']}" if note else result["text"]
    # «Отрендерено»/«готово» без рендера после правок этого хода — пометка сервиса (эксперимент 12.11).
    text = with_render_note(store, text, [{**s, "clip_id": s.get("clip_id", clip_id)} for s in result["steps"]],
                            started)
    return {"text": text, "steps": result["steps"], "remembered": remembered, "ai": True, "model": result["model"],
            "changes": changes}


# --------------------------------------------------------------------------- #
# Прежний адрес /api/clip-plans/{id}/agent
# --------------------------------------------------------------------------- #


def start_run(store: Any, clip_plan_id: int, message: str, *, client: Any = None, model: str | None = None,
              max_steps: int | None = None) -> dict:
    """Запустить ход агента в фоне. Возвращает ``{run_id, message_id, model}``."""
    text = str(message or "").strip()
    if not text:
        raise ValueError("пустое сообщение")
    thread = clip_thread(store, clip_plan_id)  # KeyError → 404
    sent = C.send_to_thread(store, thread["id"], text[:8000], client=client, model=model, max_steps=max_steps)
    return {"run_id": sent["run_id"], "message_id": sent["reply_id"],
            "model": model or thread.get("model") or settings.gemini_agent_model}


def cancel_run(clip_plan_id: int) -> bool:
    run = active_clip_run(clip_plan_id)
    if run is None:
        return False
    run.cancel.set()
    return True


def wait_run(clip_plan_id: int, timeout: float | None = None) -> bool:
    """Дождаться конца хода (для тестов и CLI в процессе). True — закончился."""
    return wait_clip_run(clip_plan_id, timeout)


def chat_view(store: Any, clip_plan_id: int) -> dict:
    """Переписка + флаг «агент работает» — ответ прежнего адреса: старые реплики и новые подряд."""
    store.get_clip_plan(clip_plan_id, include_segments=False)
    thread = find_clip_thread(store, clip_plan_id)
    run = active_clip_run(clip_plan_id)
    messages = C.thread_view(store, thread["id"])["messages"] if thread else legacy_clip_messages(store, clip_plan_id)
    return {
        "messages": [
            {
                "id": m["id"],
                "role": m["role"],
                "text": m.get("text") or "",
                "steps": [{k: v for k, v in s.items() if k != "args"} for s in m.get("steps") or []],
                "status": m.get("status") or "done",
                "model": m.get("model") or "",
                "created_at": m.get("created_at"),
            }
            for m in messages
        ],
        "running": run is not None,
        "run_id": run.id if run else None,
        "model": (thread or {}).get("model") or settings.gemini_agent_model,
    }


def clear_chat(store: Any, clip_plan_id: int) -> int:
    if active_clip_run(clip_plan_id) is not None:
        raise ChatBusy("агент ещё работает — сначала остановите его")
    thread = find_clip_thread(store, clip_plan_id)
    if thread:
        return C.clear_thread(store, thread["id"])
    return store.clear_clip_agent_messages(clip_plan_id)
