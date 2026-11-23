"""Новая версия заменяет невыложенную прошлую — у объяснялок (сова) и гайдов (решение 23.11).

Сова пересобирается по каждой правке битов, гайд загружается файлом по каждой правке — у клипа копились v1, v2, v3…
черновиков, и владелец выбирал среди них. Теперь у этих конвейеров новая версия ЗАМЕНЯЕТ прошлые невыложенные:

* объяснялка — ``explainer_gen.assemble`` после ``finish_assembly`` зовёт ``request``: замена случится, когда у новой
  версии появится первый готовый файл (``after_render`` из ``Store.finish_clip_render``). Упавшая пересборка ничего
  не прячет — у владельца остаётся прошлый файл;
* загрузка файлом с ``pipeline`` из ``REPLACE_PIPELINES`` — сразу (файл уже есть), app/clips_api.py.

Заменить — не удалить. Прошлый план скрывается (``hidden = 1``) и выходит из группы клипа (``group_locked = 1``: скрипт
группировки его не вернёт): в версиях и в сетке его нет, а файл, рендеры и прямая ссылка ``/clips/<id>`` остаются.
Запись — ``clip_superseded`` (кто кого заменил, из какой группы); ``restore`` возвращает план в клип. Файлы удаляет только
владелец. Не заменяются (остаются версиями клипа): выложенные (сервисом, руками, отметкой ленты), принятые владельцем в
«Ленте», стоящие в очереди публикации или одобренные к ней, избранные и отобранные владельцем, ещё рендерящиеся.
«Лента»: вариант на файле заменённого плана в невыложенной версии без вердикта меняется на новый файл в той же версии
(app/feed_replace.py) — v1 не превращается в v1, v2, v3.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

REPLACE_PIPELINES = frozenset({"explainer", "owl", "guide", "vtubika_guide"})

SCHEMA_SQL = [
    """
    CREATE TABLE IF NOT EXISTS clip_replace_requests (
        plan_id INTEGER PRIMARY KEY REFERENCES clip_plans(id) ON DELETE CASCADE,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        done_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS clip_superseded (
        plan_id INTEGER PRIMARY KEY REFERENCES clip_plans(id) ON DELETE CASCADE,
        by_plan_id INTEGER NOT NULL,
        group_id INTEGER,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_clip_superseded_by ON clip_superseded(by_plan_id)",
]


def ensure_schema(conn) -> None:
    for sql in SCHEMA_SQL:
        conn.execute(sql)


def replaces(pipeline: Any) -> bool:
    from app.upload_versions import clean_pipeline

    return clean_pipeline(pipeline) in REPLACE_PIPELINES


def request(store: Any, plan_id: int) -> None:
    """План заменит прошлые невыложенные версии своего клипа, когда у него будет готовый файл."""
    store.db.execute("INSERT OR IGNORE INTO clip_replace_requests (plan_id) VALUES (?)", (int(plan_id),))


def protected_reason(store: Any, plan_id: int) -> str:
    """Почему план нельзя заменить ('' — можно)."""
    from app.clip_marks import published_count

    pid = int(plan_id)
    row = store.db.query_one("SELECT favorite, owner_picked FROM clip_plans WHERE id = ?", (pid,))
    if not row:
        return "плана нет"
    if published_count(store, [pid]):
        return "выложен"
    if row["favorite"] or row["owner_picked"]:
        return "владелец отметил его"
    if store.db.query_one("SELECT 1 AS x FROM clips WHERE clip_plan_id = ? AND status IN ('queued', 'rendering') LIMIT 1",
                          (pid,)):
        return "ещё рендерится"
    if store.db.query_one(
        """SELECT 1 AS x FROM feed_variants v JOIN feed_versions ver ON ver.id = v.version_id JOIN clips c ON c.id = v.clip_id
           WHERE c.clip_plan_id = ? AND ver.status = 'accepted' LIMIT 1""", (pid,)):
        return "принят владельцем в ленте"
    if store.db.query_one(
        "SELECT 1 AS x FROM approvals WHERE clip_plan_id = ? AND (status = 'approved' OR publish_status != '') LIMIT 1",
        (pid,)):
        return "одобрен к публикации"
    return ""


def supersede_older(store: Any, plan_id: int) -> dict:
    """Прошлые версии клипа плана ``plan_id`` (старше него) — заменены им, кроме защищённых. Ответ: кто заменён, кто
    оставлен и почему."""
    from app.clip_groups_cards import group_members

    me = int(plan_id)
    members = group_members(store, me)
    group = next((m.get("clip_group_id") for m in members if int(m["id"]) == me), None)
    replaced: list[int] = []
    kept: dict[int, str] = {}
    with store.db.transaction():
        for member in members:
            pid = int(member["id"])
            if pid >= me:
                continue
            why = protected_reason(store, pid)
            if why:
                kept[pid] = why
                continue
            store.db.execute("UPDATE clip_plans SET hidden = 1, clip_group_id = NULL, group_locked = 1 WHERE id = ?", (pid,))
            store.db.execute(
                "INSERT OR REPLACE INTO clip_superseded (plan_id, by_plan_id, group_id) VALUES (?, ?, ?)", (pid, me, group))
            replaced.append(pid)
        store.db.execute("UPDATE clip_replace_requests SET done_at = CURRENT_TIMESTAMP WHERE plan_id = ?", (me,))
    return {"replaced": replaced, "kept": kept}


def after_render(store: Any, clip_id: int) -> dict | None:
    """Готовый файл появился: если его план ждёт замены (``request``) — заменить прошлые версии. Иначе ничего."""
    row = store.db.query_one(
        """SELECT r.plan_id FROM clips c JOIN clip_replace_requests r ON r.plan_id = c.clip_plan_id
           WHERE c.id = ? AND c.status = 'succeeded' AND r.done_at IS NULL""", (int(clip_id),))
    if not row:
        return None
    return supersede_older(store, int(row["plan_id"]))


def current_plan(store: Any, plan_id: int) -> int:
    """Чем в итоге заменён план (цепочка v1 → v2 → v3); не заменён — он сам."""
    pid, seen = int(plan_id), set()
    while pid not in seen:
        seen.add(pid)
        row = store.db.query_one("SELECT by_plan_id FROM clip_superseded WHERE plan_id = ?", (pid,))
        if not row:
            return pid
        pid = int(row["by_plan_id"])
    return pid


def replaced_in_group(store: Any, group_id: Any) -> list[dict]:
    """Заменённые планы клипа: для экрана клипа и агента (файлы остались, ``restore`` вернёт)."""
    if group_id is None:
        return []
    return [dict(r) for r in store.db.query_all(
        "SELECT plan_id, by_plan_id, created_at FROM clip_superseded WHERE group_id = ? ORDER BY plan_id", (int(group_id),))]


def restore(store: Any, plan_id: int) -> dict:
    """Вернуть заменённый план в его клип версией (видимым)."""
    row = store.db.query_one("SELECT group_id FROM clip_superseded WHERE plan_id = ?", (int(plan_id),))
    if not row:
        raise KeyError(f"план {plan_id} не заменялся")
    with store.db.transaction():
        store.db.execute("UPDATE clip_plans SET hidden = 0, clip_group_id = ?, group_locked = 0 WHERE id = ?",
                         (row["group_id"], int(plan_id)))
        store.db.execute("DELETE FROM clip_superseded WHERE plan_id = ?", (int(plan_id),))
    return {"plan_id": int(plan_id), "group_id": row["group_id"]}


def safe_after_render(store: Any, clip_id: int) -> None:
    """Для ``Store.finish_clip_render``: замена — не повод уронить рендер."""
    try:
        after_render(store, clip_id)
    except Exception:  # noqa: BLE001
        log.warning("clip replace after render %s failed", clip_id, exc_info=True)
