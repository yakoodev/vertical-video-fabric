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
"""

from __future__ import annotations

from typing import Any

from app.clip_spec import apply_spec, export_spec

HISTORY_DEPTH = 20
# Что именно сохраняем: клип-файл целиком плюс субтитры (их в файле нет).
_EDITABLE = ("schema", "title", "description", "notes", "favorite", "pieces", "render")


def snapshot(store: Any, clip_plan_id: int, label: str) -> None:
    """Запомнить состояние клипа ПЕРЕД правкой. Ошибка снимка не ломает правку."""
    try:
        spec = export_spec(store, clip_plan_id)
        state = {
            "spec": {k: spec[k] for k in _EDITABLE if k in spec},
            "subtitles": store.get_clip_plan_subtitles(clip_plan_id),
        }
        store.push_clip_plan_history(clip_plan_id, state, label, depth=HISTORY_DEPTH)
    except Exception:  # noqa: BLE001 - история это удобство, а не причина падать
        return


def undo(store: Any, clip_plan_id: int) -> tuple[dict, list[str], str]:
    """Вернуть клип к последнему снимку. Возвращает (клип-файл, что изменилось, метка)."""
    entry = store.pop_clip_plan_history(clip_plan_id)
    if entry is None:
        raise KeyError("отменять нечего")
    state = entry.get("state") or {}
    spec = dict(state.get("spec") or {})
    spec["pieces"] = _relink_pieces(store, clip_plan_id, spec.get("pieces") or [])
    fresh, changes = apply_spec(store, clip_plan_id, spec)
    if "subtitles" in state:
        store.set_clip_plan_subtitles(clip_plan_id, state["subtitles"])
    return fresh, changes, str(entry.get("label") or "правка")


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
