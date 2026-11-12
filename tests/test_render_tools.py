"""MCP render_clip / render_status (app/render_tools.py, app/render_jobs.py): тот же рендер, что POST
/api/clip-plans/{id}/render, только заданием — внешний агент получает render_id и спрашивает статус."""

from __future__ import annotations

import threading

import pytest

from app import agent_tools, modules, render_jobs
from test_api_pipeline import _require_ffmpeg
from test_clip_spec import _setup

HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _call(client, name, arguments):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
    return client.post("/mcp", json=body, headers=HEADERS).json()["result"]


@pytest.fixture(autouse=True)
def _jobs(monkeypatch):
    render_jobs.reset()
    modules.invalidate()
    yield
    render_jobs.reset()


def test_tools_are_registered_core_and_cost_cpu():
    reg = agent_tools.registry()
    assert reg["render_clip"].scope == "clip" and reg["render_clip"].mutating
    assert reg["render_clip"].cost == ("cpu",)
    assert reg["render_status"].scope == "global" and not reg["render_status"].mutating
    assert {"render_clip", "render_status"} <= modules.CORE_TOOLS
    assert "render_clip" in agent_tools.get_tool("submit_for_approval").description
    assert "render_clip" in agent_tools.get_tool("lint_clip").description


def test_mcp_lists_render_tools_with_cost(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, *_rest = _setup(tmp_path, monkeypatch)
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    tools = {t["name"]: t for t in client.post("/mcp", json=body, headers=HEADERS).json()["result"]["tools"]}
    assert tools["render_clip"]["_meta"] == {"vvf/cost": ["cpu"]}
    assert tools["render_clip"]["inputSchema"]["required"] == ["clip_id"]
    assert "render_id" in tools["render_status"]["inputSchema"]["required"]


def test_render_clip_queues_and_status_reports_done(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, store, _source, plan, *_ = _setup(tmp_path, monkeypatch)
    gate = threading.Event()
    calls = []

    def fake(st, clip_id):
        calls.append(clip_id)
        gate.wait(10)
        return {"id": 77, "status": "succeeded", "duration_sec": 5.0, "width": 1080, "height": 1920,
                "size_bytes": 1234, "error": "", "qc": {"ok": True, "warnings": []}}

    monkeypatch.setattr(render_jobs, "render_fn", fake)
    first = _call(client, "render_clip", {"clip_id": plan["id"]})
    assert first["isError"] is False, first
    job = first["structuredContent"]
    assert job["status"] in ("queued", "rendering") and job["render_id"].startswith("r") and job["reused"] is False
    # Пока идёт — тот же рендер, второго не ставит.
    again = _call(client, "render_clip", {"clip_id": plan["id"]})["structuredContent"]
    assert again["render_id"] == job["render_id"] and again["reused"] is True
    running = _call(client, "render_status", {"render_id": job["render_id"]})["structuredContent"]
    assert running["status"] in ("queued", "rendering") and "render_status" in running["note"]
    gate.set()
    done = _call(client, "render_status", {"render_id": job["render_id"], "wait_sec": 10})["structuredContent"]
    assert done["status"] == "done" and done["ok"] is True
    assert done["render_clip_id"] == 77 and done["video_url"] == "/media/clips/77" and done["qc"]["ok"] is True
    assert "submit_for_approval" in done["next"]
    assert calls == [plan["id"]]


def test_render_failure_is_a_status_not_a_crash(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, _store, _source, plan, *_ = _setup(tmp_path, monkeypatch)

    def boom(st, clip_id):
        raise ValueError("clip plan has no segments")

    monkeypatch.setattr(render_jobs, "render_fn", boom)
    job = _call(client, "render_clip", {"clip_id": plan["id"]})["structuredContent"]
    res = _call(client, "render_status", {"render_id": job["render_id"], "wait_sec": 10})
    assert res["isError"] is True
    data = res["structuredContent"]
    assert data["status"] == "failed" and data["error"] == "clip plan has no segments"
    assert "render_clip" in data["next"]


def test_unknown_render_id_and_unknown_clip(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, *_rest = _setup(tmp_path, monkeypatch)
    res = _call(client, "render_status", {"render_id": "rnope"})
    assert res["isError"] is True and "нет задания рендера rnope" in res["content"][0]["text"]
    missing = _call(client, "render_clip", {"clip_id": 99999})
    assert missing["isError"] is True


def test_real_render_through_mcp_matches_api(tmp_path, monkeypatch):
    """Настоящий рендер (ffmpeg) заданием: готовый файл — успешный рендер клипа, как у POST …/render."""
    _require_ffmpeg()
    client, store, _source, plan, *_ = _setup(tmp_path, monkeypatch)
    job = _call(client, "render_clip", {"clip_id": plan["id"]})["structuredContent"]
    done = None
    for _ in range(12):
        done = _call(client, "render_status", {"render_id": job["render_id"], "wait_sec": 30})["structuredContent"]
        if done["status"] not in ("queued", "rendering"):
            break
    assert done["status"] == "done", done
    clip = store.get_clip(int(done["render_clip_id"]))
    assert clip["status"] == "succeeded" and clip["clip_plan_id"] == plan["id"]
    assert store.get_clip_plan(plan["id"], include_segments=False)["status"] == "rendered"
