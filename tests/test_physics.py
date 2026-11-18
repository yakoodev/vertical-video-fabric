"""Слой физики (``physics``) и инструмент ``add_physics`` (app/physics_tools.py): сборка слоя (тела, появление,
стенки по кадру и полосе, тарелка на столе с толчком), схема свойств, ссылки на картинки, вызов инструмента.
Сам мир (детерминизм, пределы, производительность) — node/motion-physics.test.mjs.
"""

from __future__ import annotations

from app import motion
from app.physics_tools import FLOOR, physics_layer


def test_text_drops_by_letters_on_safe_floor():
    layer, problems, notes = physics_layer({"text": "БАМ ПАДАЕТ", "at": 2.0})
    assert not problems and not notes
    p = layer["props"]
    assert layer["type"] == "physics" and layer["start"] == 2.0 and layer["end"] == 2.0 + 3.5
    assert p["spawn"] == "drop" and p["walls"] == "frame" and p["bounds"] == {"x": 0.0, "y": 0.0, "w": 1.0, "h": FLOOR}
    assert p["bodies"][0]["kind"] == "text" and p["bodies"][0]["split"] == "letters"
    assert p["bodies"][0]["stroke"] == "#000000"


def test_images_rain_emoji_and_stickers_and_limits():
    layer, problems, _ = physics_layer({"images": ["🔥", "sticker:kenney-particles/star.png", 7], "count": 10})
    assert not problems
    bodies = layer["props"]["bodies"]
    assert layer["props"]["spawn"] == "rain"
    assert [b.get("src") or b.get("asset_id") for b in bodies] == ["emoji:1f525", "sticker:kenney-particles/star.png", "7"]
    assert all(b["count"] == 10 for b in bodies)
    _, problems, _ = physics_layer({"images": ["кот"]})
    assert problems and "images" in problems[0]
    _, _, notes = physics_layer({"images": ["🔥"], "count": 150, "text": "много букв"})
    assert any("150" in n for n in notes)
    _, problems, _ = physics_layer({})
    assert problems and "что падает" in problems[0]
    _, problems, _ = physics_layer({"text": "x", "spawn": "летят"})
    assert problems and problems[0].startswith("spawn")
    assert physics_layer({"images": ["🔥"], "spawn": "взрыв"})[0]["props"]["spawn"] == "burst"


def test_plate_on_table_knocked_off():
    layer, problems, _ = physics_layer({"shape": "plate", "origin": {"x": 0.5, "y": 0.6}, "knock": "right"})
    assert not problems
    p = layer["props"]
    assert p["spawn"] == "stack" and p["bodies"][0]["shape"] == "plate" and p["bodies"][0]["count"] == 1
    table = p["platforms"][0]
    assert table["y"] > 0.6 and table["x"] < 0.5          # стол под тарелкой, край — справа от неё
    assert p["impulse"]["x"] < 0.5 and p["impulse"]["lift"] == 0 and p["friction"] == 0.08
    left = physics_layer({"shape": "plate", "origin": {"x": 0.5, "y": 0.6}, "knock": "left"})[0]["props"]
    assert left["impulse"]["x"] > 0.5 and left["platforms"][0]["x"] > 0.5


def test_band_walls_and_fallback():
    band = {"x": 0.0, "y": 0.3, "w": 1.0, "h": 0.32}
    layer, _, notes = physics_layer({"text": "ой"}, band)
    assert layer["props"]["bounds"] == band and layer["props"]["walls"] == "frame" and not notes
    layer, _, notes = physics_layer({"text": "ой", "walls": "band"})
    assert layer["props"]["bounds"]["h"] == FLOOR and notes == ["раскладка не «полоса» — стенки по кадру"]
    layer, _, _ = physics_layer({"text": "ой", "walls": "none", "floor": 0.95}, band)
    assert layer["props"]["walls"] == "none" and layer["props"]["bounds"]["h"] == 0.95


def test_schema_normalizes_and_refs():
    layer, _, _ = physics_layer({"images": ["🔥"], "impulse": {"at": 1, "x": 0.5, "y": 0.8, "force": 99999},
                                 "gravity": 9, "platforms": [{"x": 0.5, "y": 0.7, "w": 0.4, "h": 0.02}]})
    layer["id"] = "ph1"
    norm = motion.normalize_layers([layer])[0]
    p = norm["props"]
    assert p["gravity"] == 3 and p["impulse"]["force"] == 10000 and p["platforms"][0]["w"] == 0.4
    assert p["bodies"][0]["src"] == "emoji:1f525"
    assert motion.layer_asset_refs(norm) == ["emoji:1f525"]
    assert "physics" in motion.LAYER_TYPES and "physics" in motion.VISUAL_TYPES
    raw = [{"id": "b", "type": "physics", "start": 0, "end": 2, "props": {"bodies": [{"kind": "rock"}], "spawn": "nope"}}]
    bad = motion.normalize_layers(raw)[0]["props"]
    assert bad["bodies"][0]["kind"] == "text" and bad["spawn"] == "drop"
    problems = motion.validate_layers(raw, 10)
    assert any("kind" in p for p in problems) and any("spawn" in p for p in problems), problems


def test_add_physics_tool(tmp_path, monkeypatch):
    from app.agent_tools import ToolContext, run_tool
    from app.clip_history import snapshot
    from test_cuts import _two_pieces

    store, _service, plan, _preset = _two_pieces(tmp_path, monkeypatch)
    ctx = ToolContext(store=store, clip_plan_id=plan["id"], on_first_change=lambda: snapshot(store, plan["id"], "тест"))
    out, ok = run_tool(ctx, "add_physics", {"text": "БУМ", "at": 0.5, "duration": 2})
    assert ok, out
    saved = (store.get_clip_plan(plan["id"]).get("render_settings") or {}).get("layers") or []
    phys = [l for l in saved if l.get("type") == "physics"]
    assert len(phys) == 1 and phys[0]["props"]["bodies"][0]["text"] == "БУМ"
    assert "физика" in out["added"][0]["summary"]
    out, ok = run_tool(ctx, "add_physics", {"spawn": "летят"})
    assert not ok and out["problems"]
    # Раскладка «полоса» (meme): стенки и пол — по полосе видео.
    from app.tool_core import _current_render

    render = _current_render(ctx)
    store.set_clip_plan_render_settings(plan["id"], {**render, "layout": {"mode": "meme", "video_y": 0.3}})
    out, ok = run_tool(ctx, "add_physics", {"shape": "circle", "count": 8, "spawn": "rain"})
    assert ok, out
    saved = (store.get_clip_plan(plan["id"]).get("render_settings") or {}).get("layers") or []
    bounds = [l for l in saved if l.get("type") == "physics"][-1]["props"]["bounds"]
    assert abs(bounds["y"] - 0.3) < 0.01 and bounds["h"] < 0.6, (bounds, out)
