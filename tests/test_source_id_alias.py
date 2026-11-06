"""Инструменты источника: source_id — правильное имя, project_id — старое; оба и разные — отказ."""

from __future__ import annotations

import pytest

from app import agent_tools
from app import projects as P
from app.agent_tools import SOURCE_ID_TOOLS, ToolContext, get_tool, list_tools, run_tool
from test_data_foundation import _store


def _setup(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    source = store.create_source("upload", "s.mp4", original_filename="s.mp4", duration_sec=60, status="ready")
    other = store.create_source("upload", "o.mp4", original_filename="o.mp4", duration_sec=60, status="ready")
    analysis = store.create_ai_analysis(source["id"], "manual", status="succeeded")
    segment = store.create_ai_segment(analysis["id"], {"start_sec": 0, "end_sec": 6, "title": "м"})
    plan = store.create_clip_plan(source["id"], analysis["id"], "Клип", segment_ids=[segment["id"]])
    return store, source, other, plan


def test_source_id_and_old_name_resolve_the_same(tmp_path, monkeypatch):
    store, source, _other, plan = _setup(tmp_path, monkeypatch)
    ctx = ToolContext(store=store)
    for args in ({"source_id": source["id"]}, {"project_id": source["id"]},
                 {"source_id": source["id"], "project_id": source["id"]}, {"source_id": str(source["id"])}):
        out, ok = run_tool(ctx, "list_clips", args)
        assert ok, out
        assert [c["clip_id"] for c in out["clips"]] == [plan["id"]]
        # Прежние вызовы читают project_id в ответе, новые — source_id.
        assert out["source_id"] == out["project_id"] == source["id"]


@pytest.mark.parametrize("name", sorted(SOURCE_ID_TOOLS))
def test_disagreement_is_refused_by_every_tool(tmp_path, monkeypatch, name):
    store, source, other, _plan = _setup(tmp_path, monkeypatch)
    args = {"source_id": source["id"], "project_id": other["id"], "from_sec": 0, "to_sec": 5, "title": "т",
            "pieces": [{"start_sec": 0, "end_sec": 5}], "tracks": [0], "source": True}
    plans = store.db.query_one("SELECT COUNT(*) AS n FROM clip_plans")["n"]
    out, ok = run_tool(ToolContext(store=store), name, args)
    assert not ok and "расходятся" in out["error"]
    assert store.db.query_one("SELECT COUNT(*) AS n FROM clip_plans")["n"] == plans  # ничего не создано


@pytest.mark.parametrize("name", ["list_clips", "get_shots", "get_source_transcript", "create_clip"])
def test_missing_or_bad_id_names_source_id(tmp_path, monkeypatch, name):
    store, *_ = _setup(tmp_path, monkeypatch)
    ctx = ToolContext(store=store)
    out, ok = run_tool(ctx, name, {})
    assert not ok and "нужен source_id" in out["error"]
    out, ok = run_tool(ctx, name, {"project_id": "пять"})
    assert not ok and "project_id — число" in out["error"]


def test_inside_a_clip_source_comes_from_the_clip_unless_given(tmp_path, monkeypatch):
    store, source, other, plan = _setup(tmp_path, monkeypatch)
    ctx = ToolContext(store=store, clip_plan_id=plan["id"])
    seen = []
    monkeypatch.setattr(get_tool("get_shots"), "handler", lambda _c, a: seen.append(agent_tools._source_id(a)) or {"ok": True})

    for args, expected in (({}, source["id"]), ({"source_id": other["id"]}, other["id"]), ({"project_id": other["id"]}, other["id"])):
        _out, ok = run_tool(ctx, "get_shots", args)  # подстановка не спорит с явным значением
        assert ok and seen[-1] == expected

    out, ok = run_tool(ctx, "get_shots", {"source_id": source["id"], "project_id": other["id"]})
    assert not ok and "расходятся" in out["error"]


def test_series_project_id_is_not_filled_with_a_source(tmp_path, monkeypatch):
    """У «Файлов для монтажа» project_id — проект-серия: источник клипа ему не подставляется."""
    store, _source, _other, plan = _setup(tmp_path, monkeypatch)
    seen = {}
    monkeypatch.setattr(get_tool("list_assets"), "handler", lambda _c, a: seen.update(a) or {"ok": True})
    _out, ok = run_tool(ToolContext(store=store, clip_plan_id=plan["id"]), "list_assets", {})
    assert ok and "project_id" not in seen and "source_id" not in seen


def test_series_tools_still_mean_the_series(tmp_path, monkeypatch):
    store, source, _other, _plan = _setup(tmp_path, monkeypatch)
    project = P.create_project(store, "Серия")
    out, ok = run_tool(ToolContext(store=store), "get_project_brief", {"project_id": project["id"]})
    assert ok and out["project"]["id"] == project["id"]
    # Здесь оба аргумента законны и значат разное, отказа «расходятся» нет.
    P.set_source_project(store, source["id"], project["id"])
    out, ok = run_tool(ToolContext(store=store), "get_project_brief", {"project_id": project["id"], "source_id": source["id"]})
    assert ok and out["project"]["id"] == project["id"]


def test_schemas_describe_the_meaning(tmp_path, monkeypatch):
    store, *_ = _setup(tmp_path, monkeypatch)
    tools = {t.name: t for t in list_tools(include_discovery=True, store=store)}
    assert SOURCE_ID_TOOLS <= set(tools)
    for name in SOURCE_ID_TOOLS:
        schema = tools[name].parameters
        assert "source_id" in schema["properties"], name
        assert "старое имя source_id" in schema["properties"]["project_id"]["description"], name
        assert not {"source_id", "project_id"} & set(schema.get("required") or []), name
    # Обнаруженные чтением: project_id есть ещё только там, где он значит проект-серию.
    series_meaning = {n for n, t in tools.items()
                      if "project_id" in (t.parameters.get("properties") or {}) and n not in SOURCE_ID_TOOLS}
    assert all(tools[n].scope == "project" or "проект-серия" in tools[n].parameters["properties"]["project_id"]["description"]
               for n in series_meaning), series_meaning
