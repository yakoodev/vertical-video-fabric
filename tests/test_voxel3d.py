"""Воксельный стиль 3D-серий (app/voxel3d.py): герой из примитивов в двух формах, комната стримера, карточки поверх
кадра; тестовая сцена tools/voxel_demo.py и проверка описания движка (app/scene3d.validate_spec)."""

from __future__ import annotations

import copy

import pytest

from app import voxel3d as V
from app.scene3d import SceneSpecError, validate_spec
from tools import voxel_demo as demo


def test_hero_shape_is_a_parameter():
    a = V.voxel_hero(shape="block", seat=0.3)
    b = V.voxel_hero(shape="soft", seat=0.3)
    assert a["style"] == b["style"] == "voxel"
    assert a["voxel"]["shape"] == "block" and b["voxel"]["shape"] == "soft"
    # Одежда и цвета — общие, отличается только форма (и рост по умолчанию).
    assert {k: v for k, v in a["voxel"].items() if k != "shape"} == {k: v for k, v in b["voxel"].items() if k != "shape"}
    assert a["height"] == V.HERO_HEIGHT["block"] and b["height"] == V.HERO_HEIGHT["soft"]
    assert a["seat"] == 0.3
    assert V.voxel_hero(look={"top": "#ff0000"})["voxel"]["top"] == "#ff0000"


def test_hero_rejects_unknown_shape_clip_face():
    with pytest.raises(ValueError):
        V.voxel_hero(shape="sphere")
    with pytest.raises(ValueError):
        V.voxel_hero(actions=[{"clip": "Dance_Loop", "start": 0}])
    with pytest.raises(ValueError):
        V.voxel_hero(faces=[{"t": 0, "face": "disgust"}])
    faces = V.voxel_hero(faces=[{"t": 2, "face": "wide"}, {"t": 0, "face": "sleep"}])["faces"]
    assert [f["t"] for f in faces] == [0, 2]


def test_streamer_room_has_desk_screens_and_purple_light():
    loc = V.streamer_room()
    objs = {o["id"]: o for o in loc["objects"]}
    for oid in ("desk_top", "monitor", "monitor_screen", "keyboard", "mic", "stool_seat", "phone", "phone_screen",
                "poster_l", "on_air", "wall_led", "lamp_bulb"):
        assert oid in objs, oid
    assert objs["monitor_screen"]["primitive"] == "ui_screen" and objs["monitor_screen"]["params"]["kind"] == "stream"
    assert objs["monitor_screen"]["params"]["viewers"][0]["label"] == "3 зрителя"
    assert objs["phone_screen"]["params"]["kind"] == "phone" and objs["phone_screen"]["parent"] == "phone"
    # Поверхности — пиксельный шум (воксельный вид), стол и стены.
    assert objs["desk_top"]["params"]["surface"]["kind"] == "pixel"
    assert loc["room"]["walls"]["surface"]["kind"] == "pixel"
    assert loc["room"]["floor"]["surface"]["kind"] == "pixel_planks"
    lights = {x["id"]: x for x in loc["lights"]}
    assert lights["lamp"]["type"] == "spot" and lights["lamp"]["color"] == V.WARM
    assert lights["rgb_l"]["color"] == V.PURPLE
    # Тот же формат шаблона, что у локаций серий.
    for key in ("room", "environment", "lights", "objects", "spots", "targets", "bounds", "main_lights", "glow"):
        assert key in loc
    assert set(loc["glow"]) <= set(objs)
    # Содержимое экранов подставляется.
    custom = V.streamer_room(alerts=[{"t": 1, "amount": "$5"}], phone={"screen": "payment"})
    co = {o["id"]: o for o in custom["objects"]}
    assert co["monitor_screen"]["params"]["alerts"][0]["amount"] == "$5"
    assert co["phone_screen"]["params"]["screen"] == "payment"


def test_hud_overlay_chips_follow_changes():
    hud = V.hud_overlay([(8.0, 180), (0.0, 175), (9.0, 105)])
    assert hud["kind"] == "hud" and [v["value"] for v in hud["values"]] == [175, 180, 105]
    assert [(d["text"], d["kind"]) for d in hud["deltas"]] == [("+5", "up"), ("−75", "down")]
    with pytest.raises(ValueError):
        V.hud_overlay([])
    assert V.tag_overlay("ЧАСТЬ 1", end=3)["end"] == 3.0
    toast = V.toast_overlay(1, 3, "До аренды 7 дней", chip="−75")
    assert toast["chip"] == {"text": "−75", "kind": "down"}
    phone = V.phone_overlay(2, 5, screen="payment", payment={"amount": "$250"})
    assert phone["kind"] == "phone" and phone["payment"]["amount"] == "$250"
    with pytest.raises(ValueError):
        V.phone_overlay(0, 1, screen="camera")


@pytest.mark.parametrize("shape", V.SHAPES)
def test_demo_scene_is_valid_and_complete(shape):
    spec = demo.build_spec(shape)
    info = validate_spec(spec, check_assets=False)
    assert info["frames"] == 300 and (info["width"], info["height"]) == (1080, 1920)
    assert info["models"] == []  # ни одной модели из паков
    hero = spec["characters"][0]
    assert hero["style"] == "voxel" and hero["voxel"]["shape"] == shape
    assert [f["face"] for f in hero["faces"]] == ["sleep", "wide", "happy"]
    assert {a["clip"] for a in hero["actions"]} == {"Sleep_Desk_Loop", "Startle", "Sit_Loop"}
    keys = spec["camera"]["keys"]
    assert sum(1 for k in keys if k.get("cut")) == 3  # 4 кадра
    assert any(k.get("roll") for k in keys) and all(k.get("dof") for k in keys)
    kinds = [o["kind"] for o in spec["overlays"]]
    assert kinds == ["tag", "hud"]
    hud = spec["overlays"][1]
    assert hud["values"][0]["value"] == 175 and hud["values"][-1]["value"] == 180 and hud["deltas"][0]["text"] == "+5"
    objs = {o["id"]: o for o in spec["objects"]}
    alerts = objs["monitor_screen"]["params"]["alerts"]
    assert alerts[0]["title"] == "НОВЫЙ ДОНАТ" and alerts[0]["amount"] == "$5"
    assert objs["phone_screen"]["params"]["counter"][-1]["to"] == 99
    assert any(n["title"] == "+40 подписчиков" for n in objs["phone_screen"]["params"]["notifications"])


def test_demo_captions_and_sfx_are_pack_recordings():
    layers, words = demo.caption_layers()
    assert layers[0]["type"] == "captions" and layers[0]["props"]["style"] == "mrbeast"
    colored = {words[h["i"]]["w"] for h in layers[0]["props"]["highlightWords"]}
    assert {"УСНУЛ", "99", "ДОНАТ"} <= colored
    assert all(ref.startswith("sfx:freesound/") for ref, _, _ in demo.SFX)


def test_validate_rejects_bad_voxel_and_overlay():
    spec = demo.build_spec("block")
    bad = copy.deepcopy(spec)
    bad["characters"][0]["voxel"]["shape"] = "blob"
    with pytest.raises(SceneSpecError):
        validate_spec(bad, check_assets=False)
    bad = copy.deepcopy(spec)
    bad["overlays"].append({"kind": "banner"})
    with pytest.raises(SceneSpecError):
        validate_spec(bad, check_assets=False)
    bad = copy.deepcopy(spec)
    bad["overlays"][1]["values"] = []
    with pytest.raises(SceneSpecError):
        validate_spec(bad, check_assets=False)
