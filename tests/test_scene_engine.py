"""Слой scene (движок сцен): схема в app.motion, проверка движком, инструменты агента."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app import agent_tools, chat_tools, motion, scene_engine
from app.agent_tools import ImageResult, ToolContext, run_tool

ROOT = Path(__file__).resolve().parents[1]
RENDER_TS = ROOT / "frontend" / "src" / "motion" / "scene" / "nodeTypes.ts"

GOOD_SCENE = {
    "camera": {"orbit": {"azimuth": {"keys": [[0, -20], [2, 20]]}, "elevation": 15}},
    "style": {"shading": "toon", "outline": {"width": 5}},
    "nodes": [
        {"type": "box", "size": 300, "color": "#FF2D55", "rotation": [0, "=t*60", 0]},
        {"type": "text3d", "text": "ПРИВЕТ", "size": 120, "depth": 30, "position": [0, 400, 0], "color": "#ffffff"},
    ],
}


def _compositor() -> None:
    from app.motion_render import CompositorMissing, compositor_command

    try:
        compositor_command()
    except CompositorMissing:
        pytest.skip("компоновщик не собран (npm run build:motion)")


def _ts_list(name: str) -> list[str]:
    src = RENDER_TS.read_text(encoding="utf-8")
    block = re.search(rf"export const {name} = \[(.*?)\]", src, re.S).group(1)
    return re.findall(r'"([^"]+)"', block)


def test_python_lists_match_engine():
    assert list(scene_engine.NODE_TYPES) == _ts_list("NODE_TYPES")
    assert list(scene_engine.SHADINGS) == _ts_list("SHADINGS")
    src = RENDER_TS.read_text(encoding="utf-8")
    block = re.search(r"export const TYPE_ALIASES[^=]*= \{(.*?)\};", src, re.S).group(1)
    assert dict(re.findall(r"(\w+): \"(\w+)\"", block)) == scene_engine.TYPE_ALIASES


def test_scene_layer_normalizes_to_json_and_reports_problems():
    raw = {"type": "scene", "start": 0, "end": 3, "props": {"h": 900, "scene": {
        "camera": {"fov": float("nan")},
        "nodes": [{"type": "cube"}, {"type": "boxx"}, {"type": "group", "children": [{"type": "sphereish"}]}],
    }}}
    layer = motion.normalize_layers([raw])[0]
    assert layer["type"] == "scene" and layer["props"]["h"] == 900 and "w" not in layer["props"]
    assert layer["props"]["scene"]["camera"]["fov"] == 0
    problems = motion.validate_layers([raw])
    text = "\n".join(problems)
    assert "nodes[1].type" in text and "boxx" in text
    assert "children[0].type" in text and "sphereish" in text
    assert "cube" not in text, "синоним cube → box понятен движку"
    assert any("не конечное" in p for p in problems)
    # Пустая сцена и не-объект — тоже вслух.
    assert any("пустая сцена" in p for p in motion.validate_layers([{"type": "scene", "start": 0, "end": 1, "props": {"scene": {"nodes": []}}}]))
    assert motion.normalize_layers([{"type": "scene", "start": 0, "end": 1, "props": {"scene": "box"}}])[0]["props"]["scene"] == {"nodes": []}
    huge = {"nodes": [{"type": "box", "name": "x" * 3000} for _ in range(100)]}
    assert motion.normalize_layers([{"type": "scene", "start": 0, "end": 1, "props": {"scene": huge}}])[0]["props"]["scene"] == {"nodes": []}


def test_scene_images_are_layer_assets_and_scene_covers_only_with_background():
    layer = {"type": "scene", "props": {"scene": {"nodes": [
        {"type": "image", "src": "12"}, {"type": "group", "children": [{"type": "image", "src": "emoji:1f525"}]},
        {"type": "repeat", "child": {"type": "image", "asset_id": 12}},
    ]}}}
    assert motion.layer_asset_refs(layer) == [12, "emoji:1f525"]
    clear = {"type": "scene", "props": {"scene": {"nodes": [{"type": "box"}]}}}
    assert agent_tools.cover_share(clear) == 0
    opaque = {"type": "scene", "props": {"h": 960, "scene": {"background": "#000000", "nodes": [{"type": "box"}]}}}
    assert agent_tools.cover_share(opaque) == pytest.approx(0.5)
    assert agent_tools.covering_problems([{**opaque, "id": "s"}])


def test_tools_registered_and_in_chat_group():
    names = {tool.name for tool in agent_tools.list_tools(include_discovery=True)}
    assert {"get_scene_reference", "preview_scene_frame"} <= names
    assert "get_scene_reference" in chat_tools.group_tools("montage")
    assert "preview_scene_frame" in chat_tools.group_tools("montage")
    assert "scene" in agent_tools.motion_enums()["types"]
    ctx = ToolContext(store=None)
    out, ok = run_tool(ctx, "get_scene_reference", {})
    assert ok, out
    assert "orthographic" in out["reference"] and "order: -1" in out["reference"]
    assert set(out["examples"]) >= {"toon_logo", "ortho_2d_motion"}
    assert "scene-card-flip" in out["gallery_templates"]
    one, ok = run_tool(ctx, "get_scene_reference", {"example": "toon_logo"})
    assert ok and one["props"]["scene"]["nodes"]
    _bad, ok = run_tool(ctx, "get_scene_reference", {"example": "нет такого"})
    assert not ok


def test_engine_check_finds_errors_and_examples_are_clean():
    _compositor()
    good = scene_engine.check_scene(GOOD_SCENE, 1080, 1920, 1.0, 3.0)
    assert good["ok"], good
    assert good["stats"]["frame_faces"] >= 6 and good["stats"]["frame_ms"] >= 0
    bad = scene_engine.check_scene({"nodes": [{"type": "box", "position": [0, "=sin(", 0]},
                                              {"type": "sphere", "colour": "#ff0000"}]})
    assert not bad["ok"]
    errors, warnings = scene_engine.format_problems(bad)
    assert any("position[1]" in e and "выражение" in e for e in errors)
    assert any("colour" in w and "color" in w for w in warnings)
    for name, example in scene_engine.scene_examples().items():
        props = example["props"]
        check = scene_engine.check_scene(props["scene"], props.get("w", 1080), props.get("h", 1920), 1.0, 3.0)
        assert check["ok"] and not check["warnings"], (name, check)


def test_preview_scene_frame_renders_png_and_sheet():
    _compositor()
    ctx = ToolContext(store=None)
    out, ok = run_tool(ctx, "preview_scene_frame", {"scene": GOOD_SCENE, "t": 0.5})
    assert ok, out
    result = agent_tools.registry()["preview_scene_frame"].handler(ctx, {"scene": GOOD_SCENE, "t": 0.5, "width": 270})
    assert isinstance(result, ImageResult) and result.mime_type == "image/png"
    assert result.data[:8] == b"\x89PNG\r\n\x1a\n"
    assert result.info["width"] == 270 and result.info["height"] == 480
    assert result.info["errors"] == [] and result.info["stats"]["frame_faces"] > 0
    sheet = agent_tools.registry()["preview_scene_frame"].handler(ctx, {"scene": GOOD_SCENE, "t": [0, 1, 2], "width": 200})
    assert sheet.mime_type == "image/jpeg" and sheet.info["times"] == [0.0, 1.0, 2.0]
    bad = agent_tools.registry()["preview_scene_frame"].handler(ctx, {"scene": {"nodes": [{"type": "box", "rotation": [0, "=foo", 0]}]}})
    assert any("foo" in e for e in bad.info["errors"])
    _none, ok = run_tool(ctx, "preview_scene_frame", {"t": 1})
    assert not ok


def test_add_layers_rejects_broken_scene_and_accepts_good(tmp_path, monkeypatch):
    _compositor()
    from test_agent_tools import _ctx
    from test_api_pipeline import _require_ffmpeg
    from test_clip_spec import _setup

    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    ctx = _ctx(store, plan["id"])
    broken = {"type": "scene", "start": 0, "end": 2, "props": {"h": 900, "scene": {
        "nodes": [{"type": "box", "rotation": [0, {"keys": [[0, 0, "outBak"], [1, 90]]}, 0]}]}}}
    out, ok = run_tool(ctx, "add_layers", {"layers": [broken]})
    assert not ok and any("outBak" in p for p in out["problems"]), out
    good = {"type": "scene", "start": 0, "end": 2, "props": {"h": 900, "scene": {**GOOD_SCENE, "nodes": [
        *GOOD_SCENE["nodes"], {"type": "sphere", "radius": 50, "colour": "#ffffff"}]}}}
    out, ok = run_tool(ctx, "add_layers", {"layers": [good]})
    assert ok, out
    assert "3D-сцена" in out["added"][0]["summary"]
    assert any("colour" in w for w in out["warnings"]), out["warnings"]
    assert "preview_scene_frame" in out["check"]
    layer_id = out["added"][0]["id"]
    frame = agent_tools.registry()["preview_scene_frame"].handler(ctx, {"layer_id": layer_id, "t": 1, "width": 200})
    assert frame.info["viewport"] == [1080.0, 900.0]
    saved = store.get_clip_plan(plan["id"])["render_settings"]["layers"]
    assert json.dumps(saved[0]["props"]["scene"], ensure_ascii=False).count("ПРИВЕТ") == 1
