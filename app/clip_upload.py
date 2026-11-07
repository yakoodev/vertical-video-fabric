"""Загруженный готовый ролик как настоящий клип: план с одним куском на весь файл, рендер — версия этого плана.

Файл не перекодируется и не меняется: монтажа у такого клипа нет, план нужен, чтобы у него были экран клипа, версии, описание
и место в проекте. Скрытый источник ``clip_upload`` остаётся якорем и хранит нетронутую копию. Проект — ``plan_projects``,
как у холстов: источник не должен считаться видео проекта.
"""

from __future__ import annotations

from typing import Any

UPLOAD_SOURCE_TYPE = "clip_upload"
NO_MONTAGE = "Это готовый загруженный файл: монтажа у него нет, поэтому пересобрать его нельзя. Новую версию загрузите файлом."


def _analysis(store: Any, source_id: int) -> dict:
    from app.manual_clips import _manual_analysis

    return _manual_analysis(store, int(source_id))


def create_plan(store: Any, source: dict, title: str, description: str = "", project_id: int | None = None) -> dict:
    """План на весь файл. Кусок пишется напрямую: готовый ролик бывает длиннее предела куска монтажа (180 с) и короче его
    минимума, а монтировать этот кусок всё равно никто не будет."""
    from app.projects import assign_plan_project

    source_id = int(source["id"])
    name = " ".join(str(title or "").split())[:100] or str(source.get("original_filename") or "").rsplit(".", 1)[0][:100] or "Клип"
    end = max(float(source.get("duration_sec") or 0), 0.1)
    analysis_id = int(_analysis(store, source_id)["id"])
    cur = store.db.execute(
        """
        INSERT INTO ai_segments (source_id, analysis_id, start_sec, end_sec, title, reason, status, raw_start_sec, raw_end_sec)
        VALUES (?, ?, 0, ?, ?, 'загружен готовым файлом', 'candidate', 0, ?)
        """,
        (source_id, analysis_id, end, name, end),
    )
    plan = store.create_clip_plan(source_id, analysis_id, name, description=str(description or "")[:500],
                                  segment_ids=[int(cur.lastrowid)])
    store.update_clip_plan_status(int(plan["id"]), "rendered")
    if project_id is not None:
        assign_plan_project(store, int(plan["id"]), int(project_id))
    return store.get_clip_plan(int(plan["id"]))


def is_upload_plan(store: Any, clip_plan_id: int) -> bool:
    row = store.db.query_one(
        "SELECT s.source_type FROM clip_plans cp JOIN sources s ON s.id = cp.source_id WHERE cp.id = ?", (int(clip_plan_id),)
    )
    return bool(row and row["source_type"] == UPLOAD_SOURCE_TYPE)


def planless_uploads(store: Any) -> list[dict]:
    """Загрузки старого вида: источник и готовые файлы есть, плана нет. Только чтение."""
    rows = store.db.query_all(
        """
        SELECT c.id, c.source_id, c.title, c.description, c.status, s.project_id
        FROM clips c JOIN sources s ON s.id = c.source_id
        WHERE s.source_type = ? AND c.clip_plan_id IS NULL ORDER BY c.source_id, c.id
        """,
        (UPLOAD_SOURCE_TYPE,),
    )
    return [dict(r) for r in rows]


def link_planless(store: Any, source_id: int) -> dict:
    """Дать загрузке старого вида план: все её файлы становятся версиями одного клипа. Файлы не трогает.
    Повторный вызов ничего не меняет: файлы с планом сюда не попадают."""
    clips = [c for c in planless_uploads(store) if int(c["source_id"]) == int(source_id)]
    if not clips:
        return {"source_id": int(source_id), "clip_plan_id": None, "clips": []}
    with store.db.transaction():
        plan = create_plan(store, store.get_source(int(source_id)), clips[0]["title"], clips[0]["description"])
        for clip in clips:
            store.db.execute("UPDATE clips SET clip_plan_id = ? WHERE id = ?", (int(plan["id"]), int(clip["id"])))
    return {"source_id": int(source_id), "clip_plan_id": int(plan["id"]), "clips": [int(c["id"]) for c in clips]}
