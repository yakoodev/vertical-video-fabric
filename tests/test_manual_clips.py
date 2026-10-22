"""Клипы без Gemini: агент создаёт клип по таймкодам, расшифровка кэшируется."""

from test_api_pipeline import _require_ffmpeg
from test_clip_spec import _setup


def test_manual_clip_from_timecodes(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, _store, source, _plan, _a, _b = _setup(tmp_path, monkeypatch)
    resp = client.post(f"/api/sources/{source['id']}/manual-clips", json={
        "title": "Рик превратился в огурец",
        "pieces": [{"start_sec": 0, "end_sec": 5.5}, {"start_sec": 1, "end_sec": 6.5}],
    })
    assert resp.status_code == 200, resp.text
    plan = resp.json()
    assert [(s["start_sec"], s["end_sec"]) for s in plan["segments"]] == [(0, 5.5), (1, 6.5)]
    second = client.post(f"/api/sources/{source['id']}/manual-clips", json={
        "title": "Второй", "pieces": [{"start_sec": 0, "end_sec": 6}]}).json()
    assert second["analysis_id"] == plan["analysis_id"]  # одна «папка» manual на исходник
    bad = client.post(f"/api/sources/{source['id']}/manual-clips", json={"title": "x", "pieces": []})
    assert bad.status_code == 400


def test_source_transcript_cached_and_filtered(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, _store, source, _plan, _a, _b = _setup(tmp_path, monkeypatch)
    calls = []

    def fake(path, model=None):
        calls.append(path)
        return [{"start": 0.5, "end": 2.0, "text": "раз"}, {"start": 4.0, "end": 6.0, "text": "два"}]

    monkeypatch.setattr("app.ai.transcript.transcribe_source_cues", fake)
    out = client.get(f"/api/sources/{source['id']}/transcript").json()
    assert not out["cached"] and len(out["cues"]) == 2
    out = client.get(f"/api/sources/{source['id']}/transcript", params={"from_sec": 3}).json()
    assert out["cached"] and [c["text"] for c in out["cues"]] == ["два"] and len(calls) == 1


def test_get_shots_cached_and_filtered(tmp_path, monkeypatch):
    _require_ffmpeg()
    from app.agent_tools import ToolContext, run_tool

    _client, store, source, _plan, _a, _b = _setup(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr("app.scene_cuts.detect_scene_cuts", lambda path: calls.append(path) or [1.5, 3.25, 6.0])
    ctx = ToolContext(store=store, clip_plan_id=None)
    out, ok = run_tool(ctx, "get_shots", {"project_id": source["id"], "from_sec": 2})
    assert ok, out
    assert out["shot_changes"] == [3.25, 6.0]
    out, ok = run_tool(ctx, "get_shots", {"project_id": source["id"]})
    assert out["shot_changes"] == [1.5, 3.25, 6.0] and len(calls) == 1


def test_delete_clip_plan_keeps_rendered_clips(tmp_path, monkeypatch):
    client, store, source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    clip = store.create_clip(source["id"], clip_plan_id=plan["id"])
    store.set_clip_plan_render_settings(plan["id"], {"mirror": True})
    assert client.delete(f"/api/clip-plans/{plan['id']}").json() == {"deleted": True}
    assert client.delete(f"/api/clip-plans/{plan['id']}").status_code == 404
    assert store.get_clip(clip["id"])["clip_plan_id"] is None
