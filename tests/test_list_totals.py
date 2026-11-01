"""Списки для агента и чата: «сколько у меня источников» — по total, а не по длине страницы.

Раньше store.list_sources молча обрезал до 200, и чат отвечал «200», когда источников было 244."""

from __future__ import annotations

from app import project_chat
from app.agent_tools import ToolContext, get_tool, run_tool
from app.settings import settings
from test_api_pipeline import _client


def _sources(store, n, status="ready"):
    return [store.create_source("upload", settings.source_dir / f"s{i}.mp4", original_filename=f"stream-{i}.mp4",
                                duration_sec=600, status=status) for i in range(n)]


def test_list_projects_reports_total_beyond_200(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    _sources(store, 240)
    _sources(store, 4, status="failed")
    # Служебный источник загруженного клипа — не проект и не считается.
    store.create_source("clip_upload", settings.source_dir / "upl.mp4", status="ready")

    assert store.count_sources() == {"total": 244, "by_status": {"ready": 240, "failed": 4}}
    assert len(store.list_sources()) == 244  # без обрезки
    assert len(store.list_sources(limit=10, offset=240)) == 4
    assert len(client.get("/api/sources").json()) == 244

    ctx = ToolContext(store=store)
    out, ok = run_tool(ctx, "list_projects", {})
    assert ok, out
    assert out["total"] == 244 and out["all_sources_total"] == 244
    assert out["returned"] == len(out["projects"]) == 100
    assert out["truncated"] and out["next_offset"] == 100 and "244" in out["note"]

    ids = [p["id"] for p in out["projects"]]
    seen = set(ids)
    offset = out["next_offset"]
    while offset is not None:
        page, ok = run_tool(ctx, "list_projects", {"offset": offset, "limit": 100})
        assert ok and page["total"] == 244
        seen |= {p["id"] for p in page["projects"]}
        offset = page.get("next_offset")
    assert len(seen) == 244

    out, ok = run_tool(ctx, "list_projects", {"status": "failed"})
    assert ok and out["total"] == 4 and not out["truncated"] and out["all_sources_total"] == 244
    out, ok = run_tool(ctx, "list_projects", {"query": "stream-23"})
    assert ok and out["total"] == len(out["projects"]) == 11  # 23, 230..239

    out, ok = run_tool(ctx, "list_projects", {"limit": 9999})
    assert ok and out["limit"] == 500 and out["returned"] == 244 and not out["truncated"]

    # Описание инструмента говорит модели, где число.
    assert "total" in get_tool("list_projects").description

    # Контекст общего чата: последние 40 строк, но число всех источников — отдельно.
    text = project_chat.general_context(store)
    assert '"sources_total": 244' in text


def test_list_clips_is_paginated_with_total(tmp_path, monkeypatch):
    _c, store = _client(tmp_path, monkeypatch)
    source = _sources(store, 1)[0]
    analysis = store.create_ai_analysis(source["id"], "manual", status="succeeded")
    for i in range(7):
        seg = store.create_ai_segment(analysis["id"], {"start_sec": i * 10, "end_sec": i * 10 + 6, "title": f"m{i}"})
        store.create_clip_plan(source["id"], analysis["id"], f"Клип {i}", segment_ids=[seg["id"]])
    ctx = ToolContext(store=store)
    out, ok = run_tool(ctx, "list_clips", {"project_id": source["id"], "limit": 3})
    assert ok and out["total"] == 7 and out["returned"] == 3 and out["next_offset"] == 3
    out, ok = run_tool(ctx, "list_clips", {"project_id": source["id"], "offset": 6, "limit": 3})
    assert ok and out["returned"] == 1 and not out["truncated"]
    assert "total" in get_tool("list_clips").description
