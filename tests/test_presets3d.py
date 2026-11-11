"""3D-пресеты: снимок реестра свежий, план проверяется с понятными ошибками, сцена с presets собирается
(composition через тот же код движка), инструменты агента list_3d_presets / check_3d_plan."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from app import presets3d
from app.presets3d_tools import h_check_3d_plan, h_list_3d_presets
from app.scene3d import SceneSpecError, validate_spec
from app.tool_core import ToolError

NODE = shutil.which("node")


@pytest.mark.skipif(not NODE, reason="нет node")
def test_catalog_snapshot_is_fresh():
    proc = subprocess.run([NODE, str(presets3d.DUMP_SCRIPT), "--check"], capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stderr


def test_registry_has_all_categories_with_russian_descriptions():
    cat = presets3d.catalog()
    assert set(cat) == set(presets3d.CATEGORIES)
    for name, c in cat.items():
        assert 6 <= len(c["presets"]) <= 16, name
        for p in c["presets"]:
            assert p["title"] and len(p["ru"]) > 20, (name, p["name"])
    # Слот под шейдер мем-пилота виден как слот.
    assert presets3d.get("look", "toon_ink")["status"] == "slot"
    assert {"anime_soft", "psx", "clay", "watercolor", "noir", "vhs_found", "neon_cyber"} <= set(presets3d.names("look"))
    assert {"cozy_desk", "train_station", "void_stage", "studio_cyclorama"} <= set(presets3d.names("room"))


def test_search_and_full_listing():
    found = presets3d.list_presets(q="дожд")
    assert {"rain", "night_rain"} <= {p["name"] for p in found}
    full = presets3d.list_presets("camera", full=True)
    push = next(p for p in full if p["name"] == "slow_push")
    assert any(par["name"] == "distance" for par in push["params"])
    room = next(p for p in presets3d.list_presets("room", full=True) if p["name"] == "cozy_desk")
    assert "hero" in room["anchors"] and room["suggest"]["light"]


GOOD = {"look": "anime_soft", "light": {"name": "golden_hour", "intensity": 0.8}, "room": "cozy_desk", "mood": "cozy_evening",
        "fx": ["film_grain", {"name": "rain", "amount": 0.6}],
        "camera": [{"move": "slow_push", "t0": 0, "t1": 3, "target": "hero"}, {"move": "crash_zoom", "t0": 3, "t1": 5}],
        "transitions": [{"kind": "whip", "t": 3}], "performance": [{"who": "hero", "preset": "shocked", "t": 3.1}], "subject": "hero"}


def test_validate_accepts_good_plan():
    assert presets3d.validate_presets(GOOD, duration=5, characters=["hero"]) is GOOD


@pytest.mark.parametrize("block, needle", [
    ({"look": "anime_sotf"}, "anime_soft"),
    ({"light": "golden"}, "golden_hour"),
    ({"fx": ["rainn"]}, "rain"),
    ({"camera": [{"move": "slow_push", "t0": 2, "t1": 1}]}, "больше t0"),
    ({"camera": [{"move": "orbit", "t0": 0, "t1": 9}]}, "дальше конца"),
    ({"camera": [{"move": "over_shoulder", "t0": 0, "t1": 2}]}, "who"),
    ({"camera": [{"move": "slow_push", "strength": 7}]}, "вне"),
    ({"transitions": [{"kind": "wipe", "t": 1}]}, "нет перехода"),
    ({"transitions": [{"kind": "iris"}]}, "нужен t"),
    ({"performance": [{"who": "bob", "preset": "happy", "t": 1}]}, "нет персонажа"),
    ({"performance": [{"who": "hero", "preset": "hapy", "t": 1}]}, "happy"),
    ({"looks": "noir"}, "неизвестное поле"),
    ({"room": {"name": "cozy_desk", "seed": "x"}}, "число"),
])
def test_validate_reports_problems_with_hints(block, needle):
    with pytest.raises(presets3d.PresetError) as err:
        presets3d.validate_presets(block, duration=5, characters=["hero"])
    assert any(needle in p for p in err.value.problems), err.value.problems


def test_compose_merges_fields_and_validates():
    spec = presets3d.compose({"duration": 5, "characters": [{"id": "hero", "style": "voxel"}], "presets": {"look": "noir"}},
                             light="moonlight", camera=[{"move": "orbit", "t0": 0, "t1": 5, "target": "hero"}])
    assert spec["presets"]["look"] == "noir" and spec["presets"]["light"] == "moonlight"
    with pytest.raises(presets3d.PresetError):
        presets3d.compose({"duration": 2}, look="nope")


def test_scene_spec_validation_checks_presets():
    base = {"duration": 3, "characters": [{"id": "hero", "style": "voxel", "voxel": {"shape": "soft"}}]}
    validate_spec({**base, "presets": {"look": "clay", "room": "forest_clearing", "camera": "orbit"}}, check_assets=False)
    with pytest.raises(SceneSpecError, match="presets"):
        validate_spec({**base, "presets": {"look": "clayy", "camera": "static"}}, check_assets=False)


@pytest.mark.skipif(not NODE, reason="нет node")
def test_expand_composes_scene_with_the_engine_code():
    spec = presets3d.compose({"duration": 5, "fps": 30, "characters": [{"id": "hero", "style": "voxel", "voxel": {"shape": "soft"}, "at": "hero"}]},
                             **GOOD)
    out = presets3d.expand(spec)
    assert "presets" not in out
    assert out["room"] and len(out["objects"]) > 20
    assert any(l.get("id") == "sun" for l in out["lights"])
    assert out["environment"]["matstyle"]["kind"] == "cel"
    assert any(a["kind"] == "rain" for a in out["atmosphere"])
    assert len(out["camera"]["keys"]) == 91 + 61
    assert out["environment"]["transitions"][0]["kind"] == "whip"
    hero = out["characters"][0]
    assert hero["seat"] > 0 and hero["faces"] and hero["actions"]
    assert out["presetsApplied"]["look"] == "anime_soft" and "warnings" not in out["presetsApplied"]


def test_agent_tools_list_and_check():
    out = h_list_3d_presets(None, {})
    assert out["categories"]["look"]["count"] >= 10 and "presets" in out["plan"]
    looks = h_list_3d_presets(None, {"category": "look"})
    assert all(p["category"] == "look" and "params" in p or p["category"] == "look" for p in looks["presets"])
    with pytest.raises(ToolError):
        h_list_3d_presets(None, {"category": "nope"})
    ok = h_check_3d_plan(None, {"presets": GOOD, "duration": 5, "characters": ["hero"]})
    assert ok["ok"] and ok["summary"]["look"] == "anime_soft"
    with pytest.raises(ToolError) as err:
        h_check_3d_plan(None, {"presets": {"look": "nior"}})
    assert any("noir" in p for p in err.value.problems)
