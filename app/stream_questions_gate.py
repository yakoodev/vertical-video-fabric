"""Вопросы стрима: стоп монтажа до ответа (из ``app/stream_questions.py``)."""

from __future__ import annotations

from typing import Any

from app.projects import project_of_source
from app.stream_questions_core import LEVEL_OF_SCOPE, _clip_family

LEVEL_WORD = {"project": "проект", "video": "видео", "clip": "клип"}


GATE_SHOWN = 5


# --------------------------------------------------------------------------- #
# Пауза монтажа
# --------------------------------------------------------------------------- #


def _release_row(store: Any, source_id: int) -> Any:
    return store.db.query_one("SELECT up_to_question_id, released_at FROM stream_montage_release WHERE source_id = ?",
                              (int(source_id),))


def montage_gate(store: Any, source_id: int, clip_plan_id: int | None = None) -> dict:
    """Ждёт ли монтаж ответов. Считаются открытые обязательные вопросы ЭТОГО видео: про проект и про видео
    держат всё видео; про клип только сам этот клип (``clip_plan_id``)."""
    family = _clip_family(store, int(clip_plan_id)) if clip_plan_id else set()
    rows = [dict(r) for r in store.db.query_all(
        "SELECT id, question, scope, clip_plan_id FROM stream_questions "
        "WHERE source_id = ? AND status = 'open' AND optional = 0 ORDER BY t_start, id", (int(source_id),))]
    relevant = [r for r in rows if r["scope"] != "clip" or r["clip_plan_id"] in family]
    release = _release_row(store, source_id)
    covered = int(release["up_to_question_id"]) if release else 0
    waiting = [r for r in relevant if int(r["id"]) > covered]
    from app.agent_settings import waits_for_answers

    waits = waits_for_answers(store, int(source_id))
    return {
        "paused": bool(waiting) and waits,
        "waits": waits,
        "open": len(waiting),
        "project_open": sum(1 for r in waiting if r["scope"] == "channel"),
        "clip_open": sum(1 for r in waiting if r["scope"] == "clip"),
        "items": [{"id": int(r["id"]), "question": r["question"], "level": LEVEL_OF_SCOPE.get(r["scope"], "video")}
                  for r in waiting[:GATE_SHOWN]],
        "released": release is not None,
        "released_open": len(relevant) - len(waiting),
    }


def release_montage(store: Any, source_id: int) -> dict:
    """«Монтировать без ответов»: решение владельца про это видео, инструментов агента к нему нет."""
    store.get_source(int(source_id))
    with store.db._lock:
        top = store.db.query_one("SELECT COALESCE(MAX(id), 0) AS n FROM stream_questions WHERE source_id = ?",
                                 (int(source_id),))
        store.db.execute(
            "INSERT INTO stream_montage_release (source_id, up_to_question_id) VALUES (?, ?) "
            "ON CONFLICT(source_id) DO UPDATE SET up_to_question_id = excluded.up_to_question_id, "
            "released_at = CURRENT_TIMESTAMP", (int(source_id), int(top["n"])))
    return montage_gate(store, source_id)


def restore_montage_pause(store: Any, source_id: int) -> dict:
    """Владелец передумал: снова ждём ответов."""
    store.get_source(int(source_id))
    store.db.execute("DELETE FROM stream_montage_release WHERE source_id = ?", (int(source_id),))
    return montage_gate(store, source_id)


def _ru_questions(count: int) -> str:
    tail = count % 100
    if 11 <= tail <= 14 or count % 10 in (0, 5, 6, 7, 8, 9):
        return f"{count} вопросов"
    return f"{count} вопрос" if count % 10 == 1 else f"{count} вопроса"


def montage_refusal(store: Any, source_id: int, clip_plan_id: int | None = None) -> dict | None:
    """Отказ агенту, пока видео ждёт ответов владельца; ``None`` — можно работать."""
    from app import modules

    if not modules.tool_enabled("add_stream_questions", store):  # модуль выключен: ответить негде, ждать нечего
        return None
    gate = montage_gate(store, int(source_id), clip_plan_id)
    if not gate["paused"]:
        return None
    problems = [f"#{q['id']} ({LEVEL_WORD[q['level']]}): {q['question'][:140]}" for q in gate["items"]]
    if gate["open"] > len(gate["items"]):
        problems.append(f"и ещё {gate['open'] - len(gate['items'])}")
    from app import agent_settings as S

    project_id = project_of_source(store, int(source_id))
    return {
        "ok": False,
        "paused": "open_questions",
        "setting": "wait_answers",
        "source_id": int(source_id),
        "open_questions": [q["id"] for q in gate["items"]],
        "error": (f"Монтаж видео #{source_id} на паузе: владелец ещё не ответил на {_ru_questions(gate['open'])}. "
                  "Сначала ответы, потом нарезка. Дождись их и прочитай list_stream_answers. Пока можно смотреть "
                  "стрим, задавать вопросы и править субтитры, слои и звук готовых клипов. Начать без ответов может "
                  f"только сам владелец: кнопка «Монтировать без ответов» на странице видео /v/{source_id}"
                  + (f" или настройка «{S.NAME_WAIT}»: {S.where(project_id)}." if project_id is not None else ".")),
        "problems": problems,
    }
