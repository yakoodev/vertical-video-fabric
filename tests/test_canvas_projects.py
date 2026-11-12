"""Холсты в проектах (эксперимент 12.11): create_canvas_clip кладёт холст в проект-серию, list_clips(project_id) его
находит, ответы называют настоящий проект, чужие холсты в общем скрытом источнике не видны своему проекту, миграция
приписывает старые холсты по тегу, переписке и сборкам."""

from __future__ import annotations

import json
import sqlite3

import pytest

from app import chat_tools as CT
from app import projects as P
from app.agent_tools import ToolContext, run_tool
from test_api_pipeline import _require_ffmpeg
from test_data_foundation import _store


@pytest.fixture
def store(tmp_path, monkeypatch):
    _require_ffmpeg()
    import app.canvas as canvas

    monkeypatch.setattr(canvas, "CANVAS_WIDTH", 108)
    monkeypatch.setattr(canvas, "CANVAS_HEIGHT", 192)
    return _store(tmp_path, monkeypatch)


def _canvas(store, project=None, hint=None, title="Промо"):
    args = {"duration": 8, "title": title, **({"project_id": project} if project is not None else {})}
    out, ok = run_tool(ToolContext(store=store, project_hint=hint), "create_canvas_clip", args, validate=True)
    assert ok, out
    return out


def test_canvas_lands_in_project_and_reports_real_project(store):
    nine = P.create_project(store, "vtubika.store")
    out = _canvas(store, nine["id"])
    assert out["project_id"] == nine["id"] and out["project_name"] == "vtubika.store"
    assert out["source_id"] != nine["id"] or out["source_id"] == store.get_clip_plan(out["clip_id"])["source_id"]
    assert P.project_of_plan(store, out["clip_id"]) == nine["id"]

    got, ok = run_tool(ToolContext(store=store, clip_plan_id=out["clip_id"]), "get_clip", {})
    assert ok and got["project_id"] == nine["id"] and got["project_name"] == "vtubika.store"
    assert "project_unassigned" not in got

    listed, ok = run_tool(ToolContext(store=store), "list_clips", {"project_id": nine["id"]}, validate=True)
    assert ok, listed
    assert [c["clip_id"] for c in listed["clips"]] == [out["clip_id"]]
    assert listed["clips"][0]["project_id"] == nine["id"] and listed["project_id"] == nine["id"]
    # channel_project_id — то же имя, что в list_projects.
    again, ok = run_tool(ToolContext(store=store), "list_clips", {"channel_project_id": nine["id"]}, validate=True)
    assert ok and again["clips"] == listed["clips"]


def test_project_hint_is_the_default_project(store):
    nine = P.create_project(store, "Девять")
    ten = P.create_project(store, "Десять")
    mine = _canvas(store, hint=nine["id"], title="Мой")
    theirs = _canvas(store, ten["id"], title="Чужой")
    loose = _canvas(store, title="Ничей")
    assert mine["project_id"] == nine["id"]
    assert loose["project_id"] is None and loose["project_unassigned"] and "warning" in loose

    # Без аргументов — проект работы (чат проекта, /mcp?project_id=N).
    listed, ok = run_tool(ToolContext(store=store, project_hint=nine["id"]), "list_clips", {}, validate=True)
    assert ok and [c["clip_id"] for c in listed["clips"]] == [mine["clip_id"]]

    # Скрытый источник общий: свой проект видит только свои холсты, all_projects — все.
    hidden = mine["source_id"]
    assert theirs["source_id"] == hidden
    scoped, ok = run_tool(ToolContext(store=store, project_hint=nine["id"]), "list_clips", {"source_id": hidden})
    assert ok and [c["clip_id"] for c in scoped["clips"]] == [mine["clip_id"]]
    everything, ok = run_tool(ToolContext(store=store, project_hint=nine["id"]), "list_clips",
                              {"source_id": hidden, "all_projects": True})
    rows = {c["clip_id"]: c for c in everything["clips"]}
    assert set(rows) == {mine["clip_id"], theirs["clip_id"], loose["clip_id"]}
    assert rows[theirs["clip_id"]]["project_id"] == ten["id"]
    assert rows[loose["clip_id"]]["project_unassigned"] is True and rows[loose["clip_id"]]["project_id"] is None

    # Чужой холст в проекте 9: get_clip предупреждает, что он не свой.
    got, ok = run_tool(ToolContext(store=store, clip_plan_id=theirs["clip_id"], project_hint=nine["id"]), "get_clip", {})
    assert ok and got["project_id"] == ten["id"] and "не из этого проекта" in got["project_warning"]
    got, ok = run_tool(ToolContext(store=store, clip_plan_id=loose["clip_id"], project_hint=nine["id"]), "get_clip", {})
    assert ok and got["project_unassigned"] is True and "ничей" in got["project_warning"]


def test_unknown_project_refused_and_nothing_created(store):
    before = store.db.query_one("SELECT COUNT(*) AS n FROM clip_plans")["n"]
    out, ok = run_tool(ToolContext(store=store), "create_canvas_clip", {"duration": 8, "project_id": 999})
    assert not ok and "999" in out["error"]
    assert store.db.query_one("SELECT COUNT(*) AS n FROM clip_plans")["n"] == before


def test_list_clips_project_id_old_meaning_still_reads_a_source(store):
    """Раньше project_id у list_clips значил источник: id, которого нет среди проектов, — источник, с заметкой."""
    source = store.create_source("upload", "s.mp4", original_filename="s.mp4", duration_sec=60, status="ready")
    store.db.execute("UPDATE sqlite_sequence SET seq = 500 WHERE name = 'projects'")
    analysis = store.create_ai_analysis(source["id"], "manual", status="succeeded")
    segment = store.create_ai_segment(analysis["id"], {"start_sec": 0, "end_sec": 6, "title": "м"})
    plan = store.create_clip_plan(source["id"], analysis["id"], "Клип", segment_ids=[segment["id"]])
    out, ok = run_tool(ToolContext(store=store), "list_clips", {"project_id": source["id"]}, validate=True)
    assert ok, out
    assert [c["clip_id"] for c in out["clips"]] == [plan["id"]] and out["source_id"] == source["id"]
    assert any("source_id" in n for n in out["notes"])


def test_chat_project_scope(store):
    nine = P.create_project(store, "Девять")
    ten = P.create_project(store, "Десять")
    theirs = _canvas(store, ten["id"], title="Чужой")
    # Холст из чата проекта — в этот проект; чужой project_id — отказ.
    out, ok, _clip = CT.run(store, "create_canvas_clip", {"duration": 8}, project_id=nine["id"])
    assert ok and out["project_id"] == nine["id"]
    out, ok, _clip = CT.run(store, "create_canvas_clip", {"duration": 8, "project_id": ten["id"]}, project_id=nine["id"])
    assert not ok and "чат проекта" in out["error"]
    # Правка холста другого проекта из чата проекта — отказ; чтение — с пометкой.
    out, ok, _clip = CT.run(store, "set_backdrop", {"clip_id": theirs["clip_id"], "background": "grid"},
                            project_id=nine["id"])
    assert not ok and f"проекта #{ten['id']}" in out["error"]
    listed, ok, _clip = CT.run(store, "list_clips", {}, project_id=nine["id"])
    assert ok and theirs["clip_id"] not in [c["clip_id"] for c in listed["clips"]]


def test_mcp_project_from_url(store, monkeypatch):
    from starlette.requests import Request

    from app import agent_api

    nine = P.create_project(store, "Девять")
    scope = {"type": "http", "query_string": f"project_id={nine['id']}".encode(), "headers": []}
    assert agent_api.mcp_project(Request(scope)) == nine["id"]
    scope = {"type": "http", "query_string": b"", "headers": [(b"x-vvf-project", str(nine["id"]).encode())]}
    assert agent_api.mcp_project(Request(scope)) == nine["id"]
    assert agent_api.mcp_project(Request({"type": "http", "query_string": b"project_id=abc", "headers": []})) is None

    reply = agent_api.mcp_handle(store, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "create_canvas_clip", "arguments": {"duration": 8}}}, nine["id"])
    body = reply["result"]["structuredContent"]
    assert body["project_id"] == nine["id"]


# --------------------------------------------------------------------------- #
# Миграция
# --------------------------------------------------------------------------- #


def _loose_canvas(store, title):
    from app.canvas import create_canvas_clip

    return create_canvas_clip(store, 8, None, title)["id"]


def test_migration_infers_projects_and_marks_the_rest(store, tmp_path):
    from app.canvas_projects import MIGRATION_KEY, migrate_canvas_projects

    nine = P.create_project(store, "Девять")
    seven = P.create_project(store, "Семь")
    tagged = _loose_canvas(store, "по тегу")
    P.set_clip_tags(store, tagged, ["project_" + str(nine["id"]), "ad"])
    chatted = _loose_canvas(store, "из чата")
    thread = store.db.execute("INSERT INTO chat_threads (project_id, title) VALUES (?, 'EXP')", (nine["id"],)).lastrowid
    steps = [{"tool": "create_canvas_clip", "ok": True, "kind": "tool",
              "result": json.dumps({"ok": True, "clip_id": chatted, "title": "Холст"})}]
    store.db.execute("INSERT INTO project_chat_messages (thread_id, project_id, role, text, steps_json) "
                     "VALUES (?, ?, 'assistant', '', ?)", (thread, nine["id"], json.dumps(steps)))
    explained = _loose_canvas(store, "объяснялка")
    store.db.execute("INSERT INTO expl_mascots (project_id, slug, name) VALUES (?, 'uh', 'Ух')", (seven["id"],))
    mascot = store.db.query_one("SELECT id FROM expl_mascots")["id"]
    store.db.execute("INSERT INTO expl_videos (project_id, mascot_id, clip_id) VALUES (?, ?, ?)",
                     (seven["id"], mascot, explained))
    already = _loose_canvas(store, "уже в проекте")
    P.assign_plan_project(store, already, seven["id"])
    orphan = _loose_canvas(store, "ничей")

    path = store.db.path if hasattr(store.db, "path") else None
    store.db.execute("DELETE FROM app_settings WHERE key = ?", (MIGRATION_KEY,))  # база создана до холстов
    conn = sqlite3.connect(str(path or tmp_path / "data" / "app.sqlite"))
    try:
        report = migrate_canvas_projects(conn)
        conn.commit()
        assert migrate_canvas_projects(conn) is None  # второй раз — ничего
    finally:
        conn.close()
    assert report["assigned"][str(tagged)]["project_id"] == nine["id"]
    assert report["assigned"][str(chatted)]["project_id"] == nine["id"]
    assert report["assigned"][str(explained)]["project_id"] == seven["id"]
    assert str(already) not in report["assigned"] and report["unassigned"] == [orphan]
    assert P.project_of_plan(store, tagged) == nine["id"] and P.project_of_plan(store, already) == seven["id"]
    assert P.project_of_plan(store, orphan) is None
    saved = json.loads(store.db.query_one("SELECT value_json FROM app_settings WHERE key = ?", (MIGRATION_KEY,))[0])
    assert saved["unassigned"] == [orphan]
    listed, ok = run_tool(ToolContext(store=store), "list_clips", {"source_id": store.get_clip_plan(orphan)["source_id"]})
    row = next(c for c in listed["clips"] if c["clip_id"] == orphan)
    assert row["project_unassigned"] is True
