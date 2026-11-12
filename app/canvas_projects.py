"""Миграция: старые холсты — в свои проекты (``plan_projects``).

До 12.11 ``create_canvas_clip`` (MCP и чат) не принимал проект: холст ложился в общий скрытый источник «Холсты» без
приписки. В клипах проекта его не было, а агенты разных проектов видели чужие черновики среди своих (эксперимент
12.11: прогон Gemini выдал холст Claude за свою работу). Сборки сервиса (объяснялки, истории, ИИ-видео, 3D-серии)
приписывали свои холсты и раньше — их это не касается.

Здесь проект ничейного холста выводится из того, что о нём известно, по убыванию надёжности:

1. тег клипа ``project_9`` (агенты ставили его сами);
2. ролик объяснялки, эпизод истории, 3D-серия или сценарий ИИ-видео с этим клипом — их проект;
3. чат клипа (``chat_threads.clip_plan_id``) в проекте;
4. переписка проекта, в которой агент создал этот холст (шаг ``create_canvas_clip`` с этим ``clip_id``);
5. другая версия того же клипа (``clip_group_id``), уже приписанная к проекту.

Кого вывести не удалось, остаётся ничьим: в ответах инструментов у такого клипа ``project_unassigned: true``. Итог
пишется в ``app_settings`` (ключ ``MIGRATION_KEY``): что куда приписано и почему, кто остался ничьим. Миграция только
добавляет строки ``plan_projects`` (``INSERT OR IGNORE``), ничего не удаляет и не трогает уже приписанные клипы;
повторный запуск ничего не меняет (ключ уже записан).
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

MIGRATION_KEY = "canvas_projects_migration_v1"
_TAG_RE = re.compile(r"^project[_-]?(\d+)$")
_CLIP_ID_RE = re.compile(r'"clip_id"\s*:\s*(\d+)')
# Таблица → (колонка клипа, колонка проекта): сборки сервиса, у которых есть проект.
_OWNER_TABLES = (("expl_videos", "clip_id"), ("story_episodes", "clip_id"), ("s3d_episodes", "clip_id"),
                 ("aiv_scripts", "clip_plan_id"))


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})")}


def unassigned_canvases(conn: sqlite3.Connection) -> list[int]:
    """Холсты (клипы скрытого источника ``canvas``) без проекта: ни у источника, ни приписки."""
    rows = conn.execute(
        "SELECT cp.id FROM clip_plans cp JOIN sources s ON s.id = cp.source_id "
        "WHERE s.source_type = 'canvas' AND s.project_id IS NULL "
        "AND cp.id NOT IN (SELECT clip_plan_id FROM plan_projects) ORDER BY cp.id"
    ).fetchall()
    return [int(r[0]) for r in rows]


def _chat_created(conn: sqlite3.Connection, tables: set[str]) -> dict[int, int]:
    """Холсты, созданные агентом в переписке проекта: {clip_id: project_id}."""
    if not {"project_chat_messages", "chat_threads"} <= tables:
        return {}
    out: dict[int, int] = {}
    rows = conn.execute(
        "SELECT COALESCE(t.project_id, m.project_id) AS project_id, m.steps_json FROM project_chat_messages m "
        "LEFT JOIN chat_threads t ON t.id = m.thread_id "
        "WHERE m.steps_json LIKE '%create_canvas_clip%' AND COALESCE(t.project_id, m.project_id) IS NOT NULL "
        "ORDER BY m.id"
    ).fetchall()
    for project_id, steps_json in rows:
        try:
            steps = json.loads(steps_json or "[]")
        except (TypeError, ValueError):
            continue
        for step in steps if isinstance(steps, list) else []:
            if not isinstance(step, dict) or step.get("tool") != "create_canvas_clip" or step.get("ok") is not True:
                continue
            match = _CLIP_ID_RE.search(str(step.get("result") or ""))
            if match:
                out.setdefault(int(match.group(1)), int(project_id))
    return out


def infer_projects(conn: sqlite3.Connection, plan_ids: list[int]) -> dict[int, tuple[int, str]]:
    """{clip_id: (project_id, откуда известно)} для тех, чей проект выводится; проект должен существовать."""
    tables = _tables(conn)
    projects = {int(r[0]) for r in conn.execute("SELECT id FROM projects")}
    found: dict[int, tuple[int, str]] = {}

    def take(plan_id: int, project_id: Any, how: str) -> None:
        try:
            project = int(project_id)
        except (TypeError, ValueError):
            return
        if plan_id not in found and project in projects:
            found[plan_id] = (project, how)

    wanted = set(plan_ids)
    if "clip_tags" in tables:
        for plan_id, tag in conn.execute("SELECT clip_plan_id, tag FROM clip_tags ORDER BY clip_plan_id, tag"):
            match = _TAG_RE.match(str(tag or ""))
            if int(plan_id) in wanted and match:
                take(int(plan_id), match.group(1), f"тег {tag}")
    for table, column in _OWNER_TABLES:
        if table in tables and {column, "project_id"} <= _columns(conn, table):
            for plan_id, project_id in conn.execute(
                    f"SELECT {column}, project_id FROM {table} WHERE {column} IS NOT NULL ORDER BY id"):
                if int(plan_id) in wanted:
                    take(int(plan_id), project_id, table)
    if "chat_threads" in tables and "clip_plan_id" in _columns(conn, "chat_threads"):
        for plan_id, project_id in conn.execute(
                "SELECT clip_plan_id, project_id FROM chat_threads WHERE clip_plan_id IS NOT NULL "
                "AND project_id IS NOT NULL ORDER BY id"):
            if int(plan_id) in wanted:
                take(int(plan_id), project_id, "чат клипа")
    for plan_id, project_id in _chat_created(conn, tables).items():
        if plan_id in wanted:
            take(plan_id, project_id, "создан в переписке проекта")
    if "clip_group_id" in _columns(conn, "clip_plans"):
        for plan_id in sorted(wanted - set(found)):
            rows = conn.execute(
                "SELECT DISTINCT pp.project_id FROM clip_plans me JOIN clip_plans other "
                "ON other.clip_group_id = me.clip_group_id AND other.id != me.id "
                "JOIN plan_projects pp ON pp.clip_plan_id = other.id WHERE me.id = ? AND me.clip_group_id IS NOT NULL",
                (plan_id,)).fetchall()
            if len(rows) == 1:
                take(plan_id, rows[0][0], "версия клипа проекта")
    return found


def migrate_canvas_projects(conn: sqlite3.Connection) -> dict | None:
    """Один раз на базу: приписать ничейные холсты к выведенным проектам. Итог — в app_settings; уже было — None."""
    tables = _tables(conn)
    if not {"plan_projects", "projects", "app_settings", "clip_plans", "sources"} <= tables:
        return None
    if conn.execute("SELECT 1 FROM app_settings WHERE key = ?", (MIGRATION_KEY,)).fetchone():
        return None
    plan_ids = unassigned_canvases(conn)
    found = infer_projects(conn, plan_ids)
    for plan_id, (project_id, _how) in sorted(found.items()):
        conn.execute("INSERT OR IGNORE INTO plan_projects (clip_plan_id, project_id) VALUES (?, ?)",
                     (int(plan_id), int(project_id)))
    report = {
        "assigned": {str(k): {"project_id": v[0], "by": v[1]} for k, v in sorted(found.items())},
        "unassigned": [i for i in plan_ids if i not in found],
        "note": "ничьи холсты: проекта не вывести — в инструментах project_unassigned: true",
    }
    conn.execute("INSERT INTO app_settings (key, value_json, encrypted) VALUES (?, ?, 0) ON CONFLICT(key) DO NOTHING",
                 (MIGRATION_KEY, json.dumps(report, ensure_ascii=False)))
    return report
