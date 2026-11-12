"""Перетекающие переходы между сценами (слой transition, app/motion_flow.py, MCP flow_transition).

Движок — frontend/src/motion/flow.ts (node/motion-flow.test.mjs): здесь схема, нормализация, группы по времени,
подгонка времени слоёв и инструмент."""

from __future__ import annotations

import pathlib

from app import motion
from app.agent_tools import run_tool
from app.motion_flow import FLOW_AUTO_SLACK, FLOW_DIRECTIONS, FLOW_KINDS, FLOW_SHAPES, NEVER_GROUPED, fit_timing, flow_groups
from test_agent_tools import _ctx
from test_api_pipeline import _require_ffmpeg
from test_clip_spec import _setup

FLOW_TS = (pathlib.Path(__file__).resolve().parents[1] / "frontend/src/motion/flow.ts").read_text(encoding="utf-8")


def test_schema_mirrors_engine():
    """Виды, формы, направления, допуск автогрупп и «неприкасаемые» типы — те же, что у движка."""
    assert "transition" in motion.LAYER_TYPES and "transition" not in motion.VISUAL_TYPES
    for names, const in ((FLOW_KINDS, "FLOW_KINDS"), (FLOW_SHAPES, "FLOW_SHAPES"), (FLOW_DIRECTIONS, "FLOW_DIRECTIONS")):
        line = next(l for l in FLOW_TS.splitlines() if l.startswith(f"export const {const} ="))
        assert all(f'"{n}"' in line for n in names), const
    assert f"FLOW_AUTO_SLACK = {FLOW_AUTO_SLACK}" in FLOW_TS
    line = next(l for l in FLOW_TS.splitlines() if l.startswith("const NEVER_GROUPED"))
    assert all(f'"{t}"' in line for t in NEVER_GROUPED)
    described = motion.schema_summary()["props"]["transition"]
    assert set(described) >= {"kind", "from", "to", "target", "match", "ease", "direction", "dip"}


def test_normalize_transition_layer():
    clean = motion.normalize_layers([{
        "type": "transition", "start": 1, "end": 1.7, "in": {"type": "fade", "duration": 0.2},
        "loop": {"type": "pulse"}, "shadow": {"color": "#000000", "blur": 10}, "followCamera": True,
        "props": {"kind": "reveal_from", "from": ["a", "a", "b"], "to": ["b", "c"], "ease": "outCubic",
                  "edge": "#12EEDA", "feather": 40},
    }])[0]
    assert clean["type"] == "transition" and clean["in"]["type"] == "none" and clean["out"]["type"] == "none"
    assert clean["loop"]["type"] == "none" and "shadow" not in clean and clean["followCamera"] is False
    p = clean["props"]
    # Повторы — прочь, слой из обоих списков — в B (как flowGroups движка).
    assert p["from"] == ["a"] and p["to"] == ["b", "c"]
    assert p["ease"] == "outCubic" and p["edge"] == "#12eeda" and p["feather"] == 40
    bez = motion.normalize_layers([{"type": "transition", "start": 0, "end": 1,
                                    "props": {"kind": "push_slide", "ease": "bezier(0.3,1.4,0.6,1)"}}])[0]
    assert bez["props"]["ease"].startswith("bezier(")
    bad = motion.validate_layers([{"type": "transition", "start": 0, "end": 1, "props": {"kind": "wipe", "ease": "zzz",
                                                                                        "direction": "north"}}])
    assert any("kind" in p for p in bad) and any("ease" in p for p in bad) and any("direction" in p for p in bad)


def test_auto_groups_and_fit_timing():
    layers = [
        {"id": "bg", "type": "tile_wall", "start": 0, "end": 10},
        {"id": "a1", "type": "text", "start": 0, "end": 5.0},
        {"id": "a2", "type": "shape", "start": 1, "end": 5.6},
        {"id": "b1", "type": "text", "start": 5.0, "end": 9, "in": {"type": "pop", "duration": 0.3},
         "keys": {"opacity": [{"t": 0, "v": 0}, {"t": 0.5, "v": 1}]}},
        {"id": "b2", "type": "image", "start": 5.3, "end": 9},
        {"id": "cap", "type": "captions", "start": 0, "end": 10},
        {"id": "snd", "type": "audio", "start": 5, "end": 6},
        {"id": "late", "type": "text", "start": 7, "end": 9},
    ]
    a, b = flow_groups(layers, 5.0, 5.6)
    assert a == ["a1", "a2"] and b == ["b1", "b2"]
    fitted, notes = fit_timing(layers, a, b, 5.0, 5.6)
    by = {l["id"]: l for l in fitted}
    # A — ровно до конца перехода, без своего выхода; B — с начала, без входа; ключи B на прежнем абсолютном времени.
    assert by["a1"]["end"] == 5.6 and by["a1"]["out"] is None and by["a2"]["end"] == 5.6
    assert by["b1"]["start"] == 5.0 and by["b1"]["in"] is None and by["b1"]["keys"]["opacity"][1]["t"] == 0.5
    assert by["b2"]["start"] == 5.0
    assert by["bg"] == layers[0] and by["late"] == layers[-1]
    kept, _ = fit_timing(layers, a, b, 5.0, 5.6, keep_in=True)
    assert next(l for l in kept if l["id"] == "b1")["in"]["type"] == "pop"
    assert notes == []


def test_flow_transition_tool(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    ctx = _ctx(store, plan["id"])
    out, ok = run_tool(ctx, "add_layers", {"layers": [
        {"id": "a_title", "type": "text", "start": 0, "end": 2.0, "props": {"text": "СЦЕНА A"}},
        {"id": "a_dot", "type": "shape", "start": 0, "end": 2.0, "x": 0.5, "y": 0.6,
         "props": {"kind": "ellipse", "w": 60, "h": 60, "fill": "#12eeda"}},
        {"id": "b_title", "type": "text", "start": 2.2, "end": 4.0, "props": {"text": "СЦЕНА B"},
         "in": {"type": "pop", "duration": 0.3}},
    ]})
    assert ok, out
    # Группы по времени: A кончается к концу перехода, B начинается на нём.
    out, ok = run_tool(ctx, "flow_transition", {"kind": "reveal_from", "at": 1.7, "duration": 0.6, "target": "a_dot",
                                                "edge": "#12eeda", "feather": 30})
    assert ok, out
    assert out["kind"] == "reveal_from" and out["range"] == [1.7, 2.3] and out["auto_groups"]
    assert set(out["from"]) == {"a_title", "a_dot"} and out["to"] == ["b_title"]
    layers = {l["id"]: l for l in store.get_clip_plan(plan["id"])["render_settings"]["layers"]}
    tr = layers[out["id"]]
    assert tr["type"] == "transition" and tr["props"]["target"] == "a_dot" and tr["props"]["edge"] == "#12eeda"
    assert layers["a_title"]["end"] == 2.3 and layers["b_title"]["start"] == 1.7 and layers["b_title"]["in"]["type"] == "none"
    # Явные группы, псевдоним вида, ошибки.
    out, ok = run_tool(ctx, "flow_transition", {"kind": "push", "at": 3.0, "duration": 0.5, "from": ["b_title"],
                                                "to": [], "direction": "up", "fit_timing": False})
    assert ok and out["kind"] == "push_slide" and out["from"] == ["b_title"], out
    out, ok = run_tool(ctx, "flow_transition", {"kind": "shared_element", "at": 1.0, "target": "a_dot"})
    assert not ok and "match" in out["error"]
    out, ok = run_tool(ctx, "flow_transition", {"kind": "zoom_through", "at": 1.0})
    assert not ok and "target" in out["error"]
    out, ok = run_tool(ctx, "flow_transition", {"kind": "reveal_from", "at": 1.0, "from": ["nope"]})
    assert not ok and "nope" in out["error"]
    out, ok = run_tool(ctx, "flow_transition", {"kind": "wipe", "at": 1.0})
    assert not ok
