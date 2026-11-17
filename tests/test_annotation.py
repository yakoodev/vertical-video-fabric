"""«Пометка от руки» (app/annotation_tools.py): круг, подчёркивание, стрелка, штриховка — фигура от руки с пером
``pen: hand`` и входом draw 0.3–0.5 с, подпись шрифтом Caveat; инструмент ``add_annotation`` и схема ``pen``."""

from __future__ import annotations

import math

import pytest

from app.annotation_tools import annotation_layers
from app.motion import normalize_layers, validate_layers


def test_circle_around_target_with_caveat_label():
    layers, problems = annotation_layers({"kind": "circle", "target": {"x": 0.4, "y": 0.3, "w": 0.2, "h": 0.05},
                                          "at": 2.0, "text": "вот тут", "draw": 0.9})
    assert not problems and len(layers) == 2
    shape, label = layers
    p = shape["props"]
    assert shape["type"] == "shape" and p["kind"] == "ellipse" and p["pen"] == "hand" and p["rough"] > 0
    assert p["w"] > 0.2 * 1080 and p["h"] > 0.05 * 1920 and (shape["x"], shape["y"]) == (0.4, 0.3)
    assert shape["in"] == {"type": "draw", "duration": 0.5}               # 0.3–0.5 с
    assert shape["start"] == 2.0 and shape["end"] > 2.5
    assert label["props"]["font"] == "Caveat" and label["props"]["color"] == p["stroke"]["color"]
    assert label["y"] < shape["y"] and label["start"] > shape["start"]   # над кругом, когда он почти дорисован
    clean = normalize_layers(layers)
    assert clean[0]["props"]["pen"] == "hand" and clean[0]["in"]["type"] == "draw"
    assert not [x for x in validate_layers(layers) if "pen" in x or "draw" in x]


def test_underline_sits_under_target():
    (line,), _ = annotation_layers({"kind": "подчеркнуть", "target": {"x": 0.5, "y": 0.6, "w": 0.5, "h": 0.04}})
    assert line["props"]["kind"] == "line" and line["y"] > 0.62 and abs(line["props"]["w"] - 0.5 * 1080 * 1.05) < 1
    assert line["in"]["duration"] == 0.4


def test_arrow_points_from_tail_to_target():
    (arrow, label), problems = annotation_layers({"kind": "arrow", "from": {"x": 0.2, "y": 0.2},
                                                  "to": {"x": 0.6, "y": 0.5}, "text": "смотри"})
    assert not problems
    ax, ay, bx, by = 0.2 * 1080, 0.2 * 1920, 0.6 * 1080, 0.5 * 1920
    assert arrow["props"]["kind"] == "arrow" and math.isclose(arrow["props"]["w"], math.hypot(bx - ax, by - ay), abs_tol=0.2)
    assert math.isclose(arrow["rotation"], math.degrees(math.atan2(by - ay, bx - ax)), abs_tol=0.01)
    assert (arrow["x"], arrow["y"]) == pytest.approx((0.4, 0.35))
    assert label["x"] < 0.2 and label["y"] < 0.2                          # подпись у хвоста
    # без from — стрелка приходит от центра кадра
    (auto,), _ = annotation_layers({"kind": "arrow", "to": {"x": 0.8, "y": 0.8}})
    assert auto["x"] < 0.8 and auto["y"] < 0.8


def test_problems_and_other_format():
    assert annotation_layers({"kind": "звезда"})[1]
    assert annotation_layers({"kind": "arrow", "from": {"x": 0.5, "y": 0.5}, "to": {"x": 0.51, "y": 0.5}})[1]
    assert annotation_layers({"kind": "arrow", "to": "туда"})[1]
    (wide,), _ = annotation_layers({"kind": "circle", "target": {"x": 0.5, "y": 0.5, "w": 0.1, "h": 0.1}}, (1920, 1080))
    assert wide["props"]["w"] == round(0.1 * 1920 * 1.18 + 36, 1)
    (word,), _ = annotation_layers({"kind": "scribble", "at_word": {"text": "цена"}})
    assert word["at_word"] == {"text": "цена"} and "start" not in word and word["duration"] > 0.4


def test_pen_schema_optional():
    plain = normalize_layers([{"id": "s", "type": "shape", "start": 0, "end": 1, "props": {"kind": "line"}}])[0]
    assert "pen" not in plain["props"]
    bad = normalize_layers([{"id": "s", "type": "shape", "start": 0, "end": 1, "props": {"kind": "line", "pen": "x"}}])
    assert bad[0]["props"]["pen"] == "even"


def test_add_annotation_tool(tmp_path, monkeypatch):
    from app.agent_tools import ToolContext, run_tool
    from app.clip_history import snapshot
    from test_cuts import _two_pieces

    store, _service, plan, _preset = _two_pieces(tmp_path, monkeypatch)
    ctx = ToolContext(store=store, clip_plan_id=plan["id"], on_first_change=lambda: snapshot(store, plan["id"], "тест"))
    out, ok = run_tool(ctx, "add_annotation", {"kind": "circle", "target": {"x": 0.5, "y": 0.4, "w": 0.3, "h": 0.06},
                                               "at": 1.0, "text": "главное"})
    assert ok, out
    saved = (store.get_clip_plan(plan["id"]).get("render_settings") or {}).get("layers") or []
    kinds = sorted(layer["type"] for layer in saved)
    assert kinds == ["shape", "text"] and any(l.get("props", {}).get("pen") == "hand" for l in saved)
    out, ok = run_tool(ctx, "add_annotation", {"kind": "nope"})
    assert not ok and out["problems"]
