"""Аргументы вызова сверяются со схемой на входе MCP и чата (app/tool_args.py): лишний — отказ с «может, …?»,
нет обязательного — отказ с его именем; старые имена, которые обработчик понимает, проходят (эксперимент 12.11)."""

from __future__ import annotations

from app import agent_api
from app import chat_tools as CT
from app.agent_tools import ToolContext, get_tool, run_tool
from app.tool_args import check_args, handler_keys, suggest
from test_data_foundation import _store


def _clip(store):
    source = store.create_source("upload", "s.mp4", original_filename="s.mp4", duration_sec=60, status="ready")
    analysis = store.create_ai_analysis(source["id"], "manual", status="succeeded")
    segment = store.create_ai_segment(analysis["id"], {"start_sec": 0, "end_sec": 20, "title": "м"})
    return store.create_clip_plan(source["id"], analysis["id"], "Клип", segment_ids=[segment["id"]])


def test_search_pack_query_instead_of_q_is_refused_with_hint(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    out, ok = run_tool(ToolContext(store=store), "search_pack", {"query": "boom"}, validate=True)
    assert not ok
    assert out["unknown"] == ["query"] and out["did_you_mean"] == {"query": ["q"]}
    assert "может, q?" in out["error"] and "Ожидаются: q (string)" in out["error"]


def test_apply_cuts_keep_does_not_touch_the_clip(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    plan = _clip(store)
    store.set_clip_plan_render_settings(plan["id"], {"cuts": [{"start": 2.0, "end": 3.0}]})
    out, ok = run_tool(ToolContext(store=store, clip_plan_id=plan["id"]), "apply_cuts", {"keep": [[0, 5]]},
                       validate=True)
    assert not ok and out["did_you_mean"] == {"keep": ["cuts"]} and out["missing"] == ["cuts"]
    assert "нет обязательного аргумента cuts" in out["error"]
    # Раньше keep молча снимал все вырезы (cuts: 0).
    assert store.get_clip_plan(plan["id"])["render_settings"]["cuts"] == [{"start": 2.0, "end": 3.0}]


def test_remove_layers_names_the_expected_field(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    plan = _clip(store)
    out, ok = run_tool(ToolContext(store=store, clip_plan_id=plan["id"]), "remove_layers", {"layer_ids": ["t"]},
                       validate=True)
    assert not ok and out["did_you_mean"] == {"layer_ids": ["ids"]} and "ids* (array of string)" in out["error"]
    # Без проверки (вызов сервиса) — понятная ошибка самого обработчика.
    out, ok = run_tool(ToolContext(store=store, clip_plan_id=plan["id"]), "remove_layers", {})
    assert not ok and "нужен ids" in out["error"] and "layers[].id" in out["error"]


def test_old_names_the_handler_reads_still_pass():
    # set_format читает и старое aspect, trim_piece — source_start/source_end, auto_mix — compare.
    assert {"aspect"} <= handler_keys(get_tool("set_format").handler)
    assert {"source_start", "source_end"} <= handler_keys(get_tool("trim_piece").handler)
    assert check_args(get_tool("set_format"), {"aspect": "1:1"}) is None
    assert check_args(get_tool("trim_piece"), {"index": 0, "source_start": 1.0}) is None


def test_clip_id_on_a_global_tool_is_dropped_quietly():
    args = {"q": "boom", "clip_id": 5}
    assert check_args(get_tool("search_pack"), args) is None and "clip_id" not in args


def test_suggest():
    assert suggest("query", ["q", "pack"]) == ["q"]
    assert suggest("layer_ids", ["ids"]) == ["ids"]
    assert suggest("durration", ["duration", "title"]) == ["duration"]
    assert suggest("zzz", ["q"]) == []


def test_every_tool_is_scanned_and_injected_project_id_is_not_an_error():
    tools = CT.all_tools()
    for name, tool in tools.items():
        keys = handler_keys(tool.handler)  # разбор не падает
        assert isinstance(keys, frozenset), name
        props = set((tool.parameters or {}).get("properties") or {})
        args = {k: 1 for k in props}
        bad = check_args(tool, args, {"project_id"})
        assert bad is None or not bad.get("unknown"), (name, bad)
    # Чат подставляет project_id своего проекта инструментам проекта — даже тем, у кого его нет в схеме.
    bad = check_args(tools["series3d_status"], {"project_id": 1, "episode_id": 1}, {"project_id"})
    assert bad is None or not bad.get("unknown")


def test_chat_and_mcp_refuse_unknown_arguments(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    out, ok, _clip_id = CT.run(store, "search_pack", {"query": "boom"}, project_id=None)
    assert not ok and out["did_you_mean"] == {"query": ["q"]}
    out, ok, _clip_id = CT.run(store, "load_tools", {"group": ["montage"]}, project_id=None)  # инструмент чата
    assert not ok and "groups" in out["error"]
    reply = agent_api.mcp_call_tool(store, "search_pack", {"query": "boom"})
    assert reply["isError"] and reply["structuredContent"]["did_you_mean"] == {"query": ["q"]}
    reply = agent_api.mcp_call_tool(store, "search_pak", {"q": "boom"})
    assert reply["isError"]
    out, ok = run_tool(ToolContext(store=store), "search_pak", {"q": "boom"}, validate=True)
    assert not ok and "search_pack" in out["error"]
