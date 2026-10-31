"""Псевдо-3D: нормализация глубины и наклонов слоёв, 3D-поля камеры и пресеты движения
(docs/motion/spec.md «3D: глубина, перспектива, камера»; app/camera3d.py)."""

from __future__ import annotations

import pytest

from app import motion
from app.camera3d import PRESETS, PresetError, apply_preset, describe, preset_name
from app.motion import normalize_camera, normalize_layers, validate_camera, validate_layers


def _text(**over):
    layer = {"id": "t", "type": "text", "start": 0, "end": 4, "props": {"text": "Привет"}}
    layer.update(over)
    return layer


def test_layer_depth_and_tilts_normalized_only_when_set():
    raw = [_text(z=-5000, rotX=1000, rotY="x", keys={"z": [{"t": 0, "v": 30000}], "rotY": [{"t": 0, "v": 90}]}),
           _text(id="b")]
    problems = validate_layers(raw)
    a, b = normalize_layers(raw)
    assert a["z"] == -motion.Z_NEAR_LIMIT and a["rotX"] == motion.ROT3D_LIMIT and a["rotY"] == 0
    assert a["keys"]["z"][0]["v"] == motion.Z_FAR_LIMIT and a["keys"]["rotY"][0]["v"] == 90
    assert len(problems) == 4
    # Старый слой без 3D-полей — без новых полей (рендер ровно прежний).
    assert "z" not in b and "rotX" not in b and "rotY" not in b
    assert validate_layers(normalize_layers(raw)) == []


def test_camera_3d_fields_and_limits():
    old = normalize_camera([{"t": 1, "zoom": 1.3, "x": 0.4}])
    assert old == [{"t": 1, "zoom": 1.3, "x": 0.4, "y": 0.5}]  # старые ключи — как были
    raw = [{"t": 0, "z": 9000, "rotX": -80, "rotY": 12.5, "fov": 2, "baseZ": 800}]
    problems = validate_camera(raw)
    key = normalize_camera(raw)[0]
    assert key["z"] == motion.CAMERA_Z_LIMIT and key["rotX"] == -motion.CAMERA_ROT_LIMIT
    assert key["rotY"] == 12.5 and key["fov"] == motion.FOV_RANGE[0] and key["baseZ"] == 800
    assert len(problems) == 3
    schema = motion.schema_summary()
    assert {"z", "rotX", "rotY"} <= set(schema["animatable"]) and "fov" in schema["camera"]


@pytest.mark.parametrize("preset", [p for p in PRESETS if p != "card_flip"])
def test_camera_presets_write_keys_in_range_and_end_at_rest(preset):
    before = [{"t": 0, "zoom": 1}, {"t": 1.5, "zoom": 1.2}, {"t": 9, "zoom": 1}]
    res = apply_preset(preset, 2, None, 1, camera=before, layers=[_text(z=200, end=10)], clip_duration=10)
    t0, t1 = res["range"]
    assert t0 == 2 and t1 > t0
    keys = res["camera"]
    assert keys == normalize_camera(keys)  # всё в пределах схемы
    inside = [k for k in keys if t0 <= k["t"] <= t1]
    assert len(inside) >= 2 and any(k.get("rotX") or k.get("rotY") or k.get("z") for k in inside)
    assert [k["t"] for k in keys if k["t"] < t0 - 0.02] == [0, 1.5] and keys[-1]["t"] == 9  # снаружи — как было
    # Плоский наезд на краях сохраняется (zoom 1.2 → 1 к 9 с).
    assert 1.0 < inside[0]["zoom"] <= 1.2
    last = inside[-1]
    if preset in ("orbit", "tilt_reveal", "float"):
        assert not last.get("rotX") and not last.get("rotY") and not last.get("z")  # кончается в покое
    if preset == "dolly_zoom":
        assert last["z"] < 0 and last["fov"] < motion.DEFAULT_FOV  # отъезд и «телевик» — план стоит
    if preset == "tilt_reveal":
        hold = next(k for k in keys if k["t"] < t0 and k["t"] > 1.5)
        assert hold["ease"] == "step"  # до начала камера стоит, на start — в наклон


def test_card_flip_aliases_and_errors():
    layers = [{"id": "card", "type": "shape", "start": 1, "end": 5, "props": {"kind": "rect"},
               "keys": {"rotY": [{"t": 3.5, "v": 5}]}}]
    res = apply_preset("flip", 1.5, None, 1, layers=layers, layer_id="card")
    rot = res["layers"][0]["keys"]["rotY"]
    assert [k["v"] for k in rot] == [90, 0, 5] and rot[0]["t"] == 0.5
    assert preset_name("Hitchcock") == "dolly_zoom" and preset_name("push") == "parallax_push"
    with pytest.raises(PresetError):
        apply_preset("card_flip", 1, layers=layers)
    with pytest.raises(PresetError):
        apply_preset("barrel_roll", 1)
    with pytest.raises(PresetError):
        apply_preset("orbit", 20, clip_duration=10)
    assert {p["id"] for p in describe()} == set(PRESETS)
    note = apply_preset("orbit", 0, camera=[], layers=[_text()])["note"]
    assert "параллакс" in note  # слоёв в сцене нет — подсказка дать им z


def test_mcp_camera_preset_and_api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import app.main as main_module
    from app.agent_tools import ToolContext, run_tool
    from test_motion import _render_fixture

    store, _service, plan, *_ = _render_fixture(tmp_path, monkeypatch)
    store.set_clip_plan_render_settings(plan["id"], {"layers": [_text(z=-200)], "camera": []})
    ctx = ToolContext(store=store, clip_plan_id=plan["id"])
    out, ok = run_tool(ctx, "camera_preset", {"preset": "orbit", "start": 0.5, "duration": 2, "intensity": 0.8})
    assert ok, out
    cam = store.get_clip_plan(plan["id"])["render_settings"]["camera"]
    assert any(abs(k.get("rotY", 0)) == pytest.approx(8.0) for k in cam)
    bad, ok = run_tool(ctx, "camera_preset", {"preset": "card_flip", "start": 1})
    assert not ok and "layer_id" in bad["error"]

    monkeypatch.setattr(main_module, "store", store)
    monkeypatch.setattr("app.settings.settings.auth_enabled", False)
    client = TestClient(main_module.app)
    res = client.post("/api/motion/camera-preset", json={"preset": "float", "start": 1, "camera": [], "clip_duration": 4})
    assert res.status_code == 200, res.text
    assert res.json()["range"][0] == 1 and len(res.json()["keys"]) == 5
    assert client.post("/api/motion/camera-preset", json={"preset": "nope", "start": 0}).status_code == 400
    assert len(client.get("/api/motion/camera-presets").json()["presets"]) == len(PRESETS)
