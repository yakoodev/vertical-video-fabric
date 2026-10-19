"""Инструменты ИИ-монтажёра: слои через app.motion, проблемы вместо падений, снимок истории."""

from __future__ import annotations

from app import agent_tools
from app.agent_tools import ImageResult, ToolContext, run_tool
from app.clip_history import snapshot, undo
from test_api_pipeline import _require_ffmpeg
from test_clip_spec import _setup


def _ctx(store, plan_id, label="ИИ: тест"):
    return ToolContext(store=store, clip_plan_id=plan_id, on_first_change=lambda: snapshot(store, plan_id, label))


TITLE = {
    "type": "text", "name": "Хук", "start": 0.0, "end": 2.0, "x": 0.5, "y": 0.25,
    "props": {"text": "ОН ЭТО СДЕЛАЛ", "size": 96, "color": "#FFFFFF", "stroke": {"color": "#000000", "width": 10}},
    "in": {"type": "pop", "duration": 0.3},
}


def test_registry_is_complete_and_json_serializable():
    names = {tool.name for tool in agent_tools.list_tools(include_discovery=True)}
    for required in ("get_clip", "view_frame", "add_layers", "update_layer", "remove_layers", "clear_layers",
                     "set_camera", "search_emoji", "list_packs", "search_pack", "search_web_images",
                     "add_image_from_url", "generate_image", "list_fonts", "set_subtitles", "set_transition",
                     "trim_piece", "add_insert", "list_projects", "list_clips"):
        assert required in names, required
    import json

    json.dumps([tool.public() for tool in agent_tools.list_tools(include_discovery=True)], ensure_ascii=False)
    assert "counter" in agent_tools.motion_reference()


def test_add_update_remove_layers_with_one_history_step(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    ctx = _ctx(store, plan["id"])

    out, ok = run_tool(ctx, "add_layers", {"layers": [TITLE, {"type": "emoji", "start": 3, "end": 4.5,
                                                              "x": 0.7, "y": 0.35, "props": {"char": "🔥", "size": 200}}]})
    assert ok, out
    ids = [item["id"] for item in out["added"]]
    assert len(ids) == 2 and "титр «ОН ЭТО СДЕЛАЛ»" in out["added"][0]["summary"]

    out, ok = run_tool(ctx, "update_layer", {"id": ids[0], "patch": {"props": {"text": "НОВЫЙ ТЕКСТ"}, "y": 0.3}})
    assert ok, out
    assert out["layer"]["props"]["text"] == "НОВЫЙ ТЕКСТ" and out["layer"]["props"]["size"] == 96
    assert out["layer"]["y"] == 0.3

    out, ok = run_tool(ctx, "remove_layers", {"ids": [ids[1]]})
    assert ok and out["removed"] == 1

    layers = store.get_clip_plan(plan["id"])["render_settings"]["layers"]
    assert [layer["id"] for layer in layers] == [ids[0]]
    # Один ход — один снимок: Ctrl+Z возвращает клип без слоёв целиком.
    assert store.count_clip_plan_history(plan["id"]) == 1
    _fresh, _changes, label = undo(store, plan["id"])
    assert label == "ИИ: тест"
    assert (store.get_clip_plan(plan["id"])["render_settings"] or {}).get("layers") in (None, [])


def test_invalid_layers_return_problems_and_change_nothing(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    ctx = _ctx(store, plan["id"])
    out, ok = run_tool(ctx, "add_layers", {"layers": [
        {"type": "text", "start": 3, "end": 2, "props": {"text": "x"}},
        {"type": "hologram", "start": 1, "end": 2},
        {"type": "image", "start": 1, "end": 2, "props": {"asset_id": 99999}},
    ]})
    assert not ok
    assert len(out["problems"]) >= 3, out
    assert any("99999" in p for p in out["problems"])
    assert store.count_clip_plan_history(plan["id"]) == 0
    assert not (store.get_clip_plan(plan["id"])["render_settings"] or {}).get("layers")

    out, ok = run_tool(ctx, "update_layer", {"id": "nope", "patch": {"x": 0.1}})
    assert not ok and "nope" in out["error"]
    out, ok = run_tool(ctx, "no_such_tool", {})
    assert not ok


def test_craft_warnings_for_unsafe_zone_and_overload(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    ctx = _ctx(store, plan["id"])
    low = {**TITLE, "y": 0.9}
    crowd = [{"type": "emoji", "start": 1, "end": 3, "x": 0.2 + i * 0.15, "y": 0.4, "props": {"char": "😂"}}
             for i in range(3)]
    out, ok = run_tool(ctx, "add_layers", {"layers": [low, *crowd]})
    assert ok, out
    text = " ".join(out["warnings"])
    assert "нижние 20%" in text
    assert "перегруз" in text


def test_camera_validated_and_saved(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    ctx = _ctx(store, plan["id"])
    out, ok = run_tool(ctx, "set_camera", {"keys": [{"t": 2, "zoom": 1}, {"t": 2.2, "zoom": 1.3, "ease": "outCubic"}]})
    assert ok, out
    assert [k["zoom"] for k in store.get_clip_plan(plan["id"])["render_settings"]["camera"]] == [1, 1.3]
    out, ok = run_tool(ctx, "set_camera", {"keys": [{"t": 1, "zoom": 9}, {"zoom": 1}]})
    assert not ok and out["problems"]
    assert len(store.get_clip_plan(plan["id"])["render_settings"]["camera"]) == 2


def test_get_clip_shows_pieces_and_subtitles_in_clip_time(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    store.set_clip_plan_subtitles(plan["id"], {
        "lines": [{"start": 0.5, "end": 1.5, "text": "привет мир"}, {"start": 6.0, "end": 7.0, "text": "второй кусок"}],
        "words": [{"word": "привет", "start": 0.5, "end": 1.0}, {"word": "мир", "start": 1.0, "end": 1.5},
                  {"word": "второй", "start": 6.0, "end": 6.5}, {"word": "кусок", "start": 6.5, "end": 7.0}],
    })
    out, ok = run_tool(_ctx(store, plan["id"]), "get_clip", {})
    assert ok
    assert [p["clip_start"] for p in out["pieces"]] == [0.0, 5.0]
    assert out["duration"] == 10.0
    assert out["subtitles"]["lines"][0]["words"][1] == ["мир", 1.0, 1.5]
    assert out["layers"] == [] and out["camera"] == []


def test_view_frame_returns_an_image(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    result, ok = run_tool(_ctx(store, plan["id"]), "view_frame", {"t": 6.0})
    assert ok, result
    assert isinstance(result, ImageResult)
    assert result.mime_type.startswith("image/") and len(result.data) > 500
    assert result.info["kind"] in ("motion", "base")


def test_clip_settings_tools_trim_transition_subtitles(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, _source, plan, a, _b = _setup(tmp_path, monkeypatch)
    store.set_clip_plan_subtitles(plan["id"], {"lines": [{"start": 0.5, "end": 1.5, "text": "превет"}], "words": []})
    ctx = _ctx(store, plan["id"])

    out, ok = run_tool(ctx, "set_transition", {"type": "whip", "sfx": "whoosh"})
    assert ok and out["transition"]["type"] == "whip"
    out, ok = run_tool(ctx, "set_transition", {"type": "teleport"})
    assert not ok and out["problems"]

    out, ok = run_tool(ctx, "set_subtitles", {"on": True, "position_pct": 18})
    assert ok and out["subs_on"] is True and out["sub_pos_pct"] == 18

    out, ok = run_tool(ctx, "edit_subtitle_lines", {"edits": [{"i": 0, "text": "привет"}]})
    assert ok
    assert store.get_clip_plan_subtitles(plan["id"])["lines"][0]["text"] == "привет"

    out, ok = run_tool(ctx, "trim_piece", {"index": 0, "start_sec": 0.5, "end_sec": 5.5})
    assert ok, out
    seg = store.get_clip_plan(plan["id"])["segments"][0]
    assert (float(seg["start_sec"]), float(seg["end_sec"])) == (0.5, 5.5)
    out, ok = run_tool(ctx, "trim_piece", {"index": 0, "start_sec": 0, "end_sec": 1})
    assert not ok and out["problems"]
    assert store.count_clip_plan_history(plan["id"]) == 1


def test_discovery_tools(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    ctx = ToolContext(store=store)
    out, ok = run_tool(ctx, "list_projects", {})
    assert ok and source["id"] in [p["id"] for p in out["projects"]]
    out, ok = run_tool(ctx, "list_clips", {"project_id": source["id"]})
    assert ok and plan["id"] in [c["clip_id"] for c in out["clips"]]
    out, ok = run_tool(ctx, "get_clip", {})
    assert not ok and "clip_id" in out["error"]


def test_emoji_and_pack_search_give_layer_refs():
    import pytest

    from app import motion_assets

    if not motion_assets.list_packs():
        pytest.skip("паки не скачаны (tools/fetch_packs.py)")
    ctx = ToolContext(store=None)
    out, ok = run_tool(ctx, "search_emoji", {"q": "огонь", "limit": 5})
    assert ok and any(e["char"] == "🔥" and e["ref"] == "emoji:1f525" for e in out["results"])
    out, ok = run_tool(ctx, "list_packs", {"kind": "sfx"})
    assert ok and all(p["kind"] == "sfx" for p in out["packs"])
    out, ok = run_tool(ctx, "search_pack", {"q": "", "kind": "sfx", "limit": 3})
    assert ok and out["results"] and all(r["ref"].startswith("sfx:") for r in out["results"])
    out, ok = run_tool(ctx, "search_pack", {"pack": "нет-такого"})
    assert not ok
