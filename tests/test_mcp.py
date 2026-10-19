"""MCP (Streamable HTTP) на /mcp: те же инструменты для Claude Code / Codex, тот же токен."""

from __future__ import annotations

from app.settings import settings
from test_api_pipeline import _require_ffmpeg
from test_clip_spec import _setup

HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _rpc(client, method, params=None, request_id=1, headers=None):
    body = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body, headers={**HEADERS, **(headers or {})})


def test_mcp_requires_the_api_token(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, _store, _source, _plan, _a, _b = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "api_token", "secret-token")

    denied = _rpc(client, "initialize", {"protocolVersion": "2025-06-18"})
    assert denied.status_code == 401
    assert denied.headers["www-authenticate"] == "Bearer"
    assert _rpc(client, "ping", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/mcp").status_code == 401

    ok = _rpc(client, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                     "clientInfo": {"name": "test", "version": "1"}},
              headers={"Authorization": "Bearer secret-token"})
    assert ok.status_code == 200
    result = ok.json()["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert result["capabilities"]["tools"] == {"listChanged": False}
    assert result["serverInfo"]["name"] == "vertical-video-fabric"
    # Чат редактора защищён тем же токеном.
    assert client.get("/api/agent/tools").status_code == 401


def test_mcp_protocol_basics(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, _store, _source, _plan, _a, _b = _setup(tmp_path, monkeypatch)
    unknown = _rpc(client, "initialize", {"protocolVersion": "1999-01-01"}).json()["result"]
    assert unknown["protocolVersion"] == "2025-11-25"
    note = client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=HEADERS)
    assert note.status_code == 202
    assert _rpc(client, "ping", request_id="p").json() == {"jsonrpc": "2.0", "id": "p", "result": {}}
    assert _rpc(client, "nope").json()["error"]["code"] == -32601
    assert client.post("/mcp", content=b"{oops", headers=HEADERS).json()["error"]["code"] == -32700
    assert client.get("/mcp", headers=HEADERS).status_code == 405

    tools = {t["name"]: t for t in _rpc(client, "tools/list").json()["result"]["tools"]}
    assert {"list_projects", "list_clips", "get_clip", "add_layers", "view_frame", "set_camera"} <= set(tools)
    assert tools["add_layers"]["inputSchema"]["required"][0] == "clip_id"
    assert tools["get_clip"]["annotations"]["readOnlyHint"] is True
    assert "clip_id" not in tools["list_projects"]["inputSchema"].get("properties", {})


def test_mcp_tools_call_edits_the_clip_and_shows_a_frame(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, store, source, plan, _a, _b = _setup(tmp_path, monkeypatch)

    projects = _rpc(client, "tools/call", {"name": "list_projects", "arguments": {}}).json()["result"]
    assert projects["isError"] is False
    assert source["id"] in [p["id"] for p in projects["structuredContent"]["projects"]]

    clip = _rpc(client, "tools/call", {"name": "get_clip", "arguments": {"clip_id": plan["id"]}}).json()["result"]
    assert clip["structuredContent"]["clip_id"] == plan["id"]

    layer = {"type": "text", "start": 0.5, "end": 2.5, "y": 0.3, "props": {"text": "ПРИВЕТ", "size": 90}}
    added = _rpc(client, "tools/call", {"name": "add_layers",
                                        "arguments": {"clip_id": plan["id"], "layers": [layer]}}).json()["result"]
    assert added["isError"] is False, added
    assert store.get_clip_plan(plan["id"])["render_settings"]["layers"][0]["props"]["text"] == "ПРИВЕТ"
    assert store.count_clip_plan_history(plan["id"]) == 1

    bad = _rpc(client, "tools/call", {"name": "add_layers", "arguments": {
        "clip_id": plan["id"], "layers": [{"type": "text", "start": 2, "end": 1}]}}).json()["result"]
    assert bad["isError"] is True and bad["structuredContent"]["problems"]

    missing = _rpc(client, "tools/call", {"name": "get_clip", "arguments": {}}).json()["result"]
    assert missing["isError"] is True

    frame = _rpc(client, "tools/call", {"name": "view_frame",
                                        "arguments": {"clip_id": plan["id"], "t": 1.0}}).json()["result"]
    assert frame["isError"] is False
    assert frame["content"][0]["type"] == "image"
    assert frame["content"][0]["mimeType"].startswith("image/") and len(frame["content"][0]["data"]) > 500
