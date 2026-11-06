"""История правок клипа: шаг назад по любому изменению (Ctrl+Z).

Редактор правит клип десятком разных путей — настройки рендера, куски,
субтитры, ИИ-монтаж, ручки мема на кадре. Городить отмену в каждом из них
бессмысленно, поэтому снимок делается в одном месте: перед изменением
сохраняем весь клип целиком (клип-файл + субтитры), а отмена возвращает
последний снимок через ту же проверку, что и ручная правка.

Глубина ограничена: держим последние ``HISTORY_DEPTH`` шагов на клип, чтобы
база не пухла от автосохранений настроек. Одинаковые подряд снимки не пишем —
редактор сохраняет настройки по таймеру, и без этого история забилась бы
копиями одного состояния.

Шаг вперёд (Ctrl+Shift+Z): отмена кладёт то, что отменила, в ``clip_plan_redo``, вместе с отпечатком
клипа сразу после отмены. Клип с тех пор поменялся (неважно каким путём) — вперёд идти некуда.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from app.clip_spec import apply_spec, export_spec

HISTORY_DEPTH = 20
# Что именно сохраняем: клип-файл целиком плюс субтитры (их в файле нет).
_EDITABLE = ("schema", "title", "description", "notes", "favorite", "pieces", "render")


def capture(store: Any, clip_plan_id: int) -> dict:
    """Состояние клипа целиком (клип-файл + субтитры) — то, что хранит снимок."""
    spec = export_spec(store, clip_plan_id)
    return {
        "spec": {k: spec[k] for k in _EDITABLE if k in spec},
        "subtitles": store.get_clip_plan_subtitles(clip_plan_id),
    }


def snapshot(store: Any, clip_plan_id: int, label: str) -> dict | None:
    """Запомнить состояние клипа ПЕРЕД правкой. Ошибка снимка не ломает правку.
    Возвращает снятое состояние (чат агента хранит его у себя для «Откатить»)."""
    try:
        state = capture(store, clip_plan_id)
        store.push_clip_plan_history(clip_plan_id, state, label, depth=HISTORY_DEPTH)
        return state
    except Exception:  # noqa: BLE001 - история это удобство, а не причина падать
        return None


def save_with_snapshot(store: Any, clip_plan_id: int, label: str, save: Callable[[], Any]) -> Any:
    """Сделать правку и запомнить прежнее состояние, только если она что-то изменила.
    Редактор сохраняет настройки сразу при открытии: снимок на каждое открытие съедал «Шаг назад»
    (первое нажатие возвращало то же самое, что на экране)."""
    try:
        before = capture(store, clip_plan_id)
    except Exception:  # noqa: BLE001 - история это удобство, а не причина падать
        return save()
    result = save()
    try:
        if capture(store, clip_plan_id) != before:
            store.push_clip_plan_history(clip_plan_id, before, label, depth=HISTORY_DEPTH)
    except Exception:  # noqa: BLE001
        pass
    return result


def restore(store: Any, clip_plan_id: int, state: dict) -> tuple[dict, list[str]]:
    """Вернуть клип к сохранённому состоянию (снимок истории или чата агента)."""
    spec = dict((state or {}).get("spec") or {})
    spec["pieces"] = _relink_pieces(store, clip_plan_id, spec.get("pieces") or [])
    # Снимок — то, что уже было сохранено: возвращаем как есть, без придирок к слоям.
    fresh, changes = apply_spec(store, clip_plan_id, spec, check_motion=False)
    if "subtitles" in (state or {}):
        store.set_clip_plan_subtitles(clip_plan_id, state["subtitles"])
    return fresh, changes


def undo(store: Any, clip_plan_id: int) -> tuple[dict, list[str], str]:
    """Вернуть клип к последнему снимку. Возвращает (клип-файл, что изменилось, метка)."""
    entry = store.pop_clip_plan_history(clip_plan_id)
    if entry is None:
        raise KeyError("отменять нечего")
    label = str(entry.get("label") or "правка")
    undone = capture(store, clip_plan_id)
    # Клип поменяли после прошлой отмены: те отменённые правки вернуть уже нельзя.
    top = _redo_top(store, clip_plan_id)
    if top and top["after_undo"] != _fingerprint(undone):
        _redo_clear(store, clip_plan_id)
    fresh, changes = restore(store, clip_plan_id, entry.get("state") or {})
    store.db.execute(
        "INSERT INTO clip_plan_redo (clip_plan_id, state_json, label, after_undo) VALUES (?, ?, ?, ?)",
        (clip_plan_id, _dumps(undone), label[:120], _fingerprint(capture(store, clip_plan_id))),
    )
    store.db.execute(
        """
        DELETE FROM clip_plan_redo
        WHERE clip_plan_id = ? AND id NOT IN (
            SELECT id FROM clip_plan_redo WHERE clip_plan_id = ? ORDER BY id DESC LIMIT ?
        )
        """,
        (clip_plan_id, clip_plan_id, HISTORY_DEPTH),
    )
    return fresh, changes, label


def redo(store: Any, clip_plan_id: int) -> tuple[dict, list[str], str]:
    """Вернуть то, что отменил шаг назад. Шаг вперёд честный, только пока клип такой, каким его оставила
    отмена: любая новая правка (редактор, агент, чат) — и отменённое вернуть нельзя, иначе она бы пропала молча.
    Возвращает (клип-файл, что изменилось, метка)."""
    top = _redo_top(store, clip_plan_id)
    if not top:
        raise KeyError("повторять нечего")
    current = capture(store, clip_plan_id)
    if top["after_undo"] != _fingerprint(current):
        _redo_clear(store, clip_plan_id)
        raise KeyError("повторять нечего: клип уже поменялся")
    store.db.execute("DELETE FROM clip_plan_redo WHERE id = ?", (top["id"],))
    label = str(top["label"] or "правка")
    store.push_clip_plan_history(clip_plan_id, current, label, depth=HISTORY_DEPTH)
    fresh, changes = restore(store, clip_plan_id, json.loads(top["state_json"]))
    # Куски могли создаться заново (другие segment_id): следующий шаг вперёд сверяем с тем, что вышло сейчас.
    following = _redo_top(store, clip_plan_id)
    if following:
        store.db.execute(
            "UPDATE clip_plan_redo SET after_undo = ? WHERE id = ?",
            (_fingerprint(capture(store, clip_plan_id)), following["id"]),
        )
    return fresh, changes, label


def redo_depth(store: Any, clip_plan_id: int) -> int:
    """Сколько шагов вперёд доступно сейчас: после новой правки — ни одного."""
    top = _redo_top(store, clip_plan_id)
    if not top or top["after_undo"] != _fingerprint(capture(store, clip_plan_id)):
        return 0
    row = store.db.query_one("SELECT COUNT(*) AS n FROM clip_plan_redo WHERE clip_plan_id = ?", (clip_plan_id,))
    return int(row["n"]) if row else 0


def _dumps(state: dict) -> str:
    return json.dumps(state, ensure_ascii=False, sort_keys=True)


def _fingerprint(state: dict) -> str:
    return hashlib.sha256(_dumps(state).encode()).hexdigest()


def _redo_top(store: Any, clip_plan_id: int) -> Any:
    return store.db.query_one(
        "SELECT id, state_json, label, after_undo FROM clip_plan_redo WHERE clip_plan_id = ? ORDER BY id DESC LIMIT 1",
        (clip_plan_id,),
    )


def _redo_clear(store: Any, clip_plan_id: int) -> None:
    store.db.execute("DELETE FROM clip_plan_redo WHERE clip_plan_id = ?", (clip_plan_id,))


def _relink_pieces(store: Any, clip_plan_id: int, pieces: list) -> list:
    """Куски, которых в клипе уже нет, вернуть как новые.

    Правка могла удалить кусок, а снимок ссылается на него по segment_id —
    проверка такую ссылку не пропустит. Таймкоды у нас есть, поэтому кусок
    создаётся заново (теряется только его дорожка фокуса).
    """

    plan = store.get_clip_plan(clip_plan_id)
    alive = {seg["id"] for seg in plan.get("segments") or []}
    out = []
    for piece in pieces:
        item = dict(piece)
        if item.get("segment_id") not in alive:
            item.pop("segment_id", None)
        out.append(item)
    return out
