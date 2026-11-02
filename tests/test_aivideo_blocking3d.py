"""3D-блокинг ИИ-видео (app/aivideo_blocking3d.py): облик из карточек, позы из клипов UAL, сцена движка
из собранного блокинга (те же места и камера), настоящий кадр — если есть Node, браузер, бандл и паки."""

from __future__ import annotations

import json

import pytest

from app import aivideo_blocking as B
from app import aivideo_blocking3d as B3
from app import aivideo_layout as L
from app import scene3d

MIRA = {"slug": "mira", "name": "Мира", "identity_card": (
    "девушка 20 лет, аниме-стиль; короткое каре цвета мяты (#8FD3C1) с чёлкой, большие янтарные глаза, круглые очки "
    "в тонкой оправе; рост средний, стройная; домашняя одежда — свободная лавандовая футболка (#B8A9F0) и серые "
    "шорты; отличительная черта — родинка под левым глазом")}
BEEP = {"slug": "beep", "name": "Бип", "identity_card": (
    "круглый робот-пылесос размером с тарелку; белый глянцевый корпус с мятной полосой (#8FD3C1) по ободу, два "
    "больших круглых синих экрана-глаза на передней кромке, маленькая антенна с красной лампочкой; без рук и ног")}
ROOM = ("уютная маленькая спальня, вечер, тёплый свет настольной лампы и гирлянды на стене, кровать с лавандовым "
        "пледом, пушистый ковёр, на полу разбросаны носки, у кровати станция зарядки робота-пылесоса, окно с синим "
        "вечерним небом")
SHOT = {"id": 12, "idx": 1, "location": "bedroom", "camera": {"shot": "medium", "angle": "eye_level", "lens": 35},
        "action": "Сонная Мира ставит кружку кофе на прикроватную тумбочку и поворачивается к окну потянуться.",
        "cast": [{"id": "mira", "action": "ставит кружку и отворачивается"}, {"id": "beep", "action": "стоит на полу"}],
        "state": {"characters": [{"id": "mira", "pose": "standing", "facing": "left", "position": "center foreground",
                                  "holding": "кружка с кофе"},
                                 {"id": "beep", "pose": "lying", "facing": "left", "position": "left background"}],
                  "props": [{"name": "кружка с кофе", "state": "в руках Миры", "count": 1}]},
        "end_state": {"characters": [{"id": "mira", "pose": "standing", "facing": "away", "position": "center foreground",
                                      "holding": "ничего"},
                                     {"id": "beep", "pose": "lying", "facing": "left", "position": "left background"}],
                      "props": [{"name": "кружка с кофе", "state": "стоит на прикроватной тумбочке", "count": 1}]},
        "needs_motion": True, "continues_from_prev": False}
INFOS = [{"id": "mira", "name": "Мира", "stand_in": "human", "size": 165.0, "color": "#8e9bb5", "color_name": "slate-blue"},
         {"id": "beep", "name": "Бип", "stand_in": "disc", "size": 28.0, "color": "#c2a98a", "color_name": "sand"}]


def test_appearance_from_identity_card():
    look = B3.appearance(MIRA)
    assert look["model"] == B3.FEMALE and look["glasses"] is True
    assert look["hair_color"] == "#8fd3c1" and look["shirt"] == "#b8a9f0"
    assert look["bottom"] == L.color_in("серые")
    man = B3.appearance({"identity_card": "парень 25 лет, синяя рубашка, чёрные джинсы, короткие волосы"})
    assert man["model"] == B3.MALE and man["shirt"] == L.color_in("синяя") and man["bottom"] == L.color_in("чёрные")
    assert man["glasses"] is False


def test_robot_look_and_face_from_card_and_action():
    look = B3.robot_look(BEEP, 28.0)
    assert look["radius"] == pytest.approx(0.14) and look["screen"] == "rim"
    assert look["stripe"] == "#8fd3c1" and look["antenna"] == "#ff3b30" and look["color"] == L.color_in("белый")
    assert B3.robot_face("Бип делает огромные невинные глаза") == "innocent"
    assert B3.robot_face("радостно мигает") == "happy"
    assert B3.robot_face("просто стоит") == "neutral"


def test_pose_table_uses_clips_from_the_animation_pack():
    clips = {c for c, _t in B3.POSE_CLIPS.values()}
    index = json.loads((scene3d.packs_dir() / "packs.json").read_text("utf-8")) if (scene3d.packs_dir() / "packs.json").is_file() else {}
    have = {c for p in index.get("packs") or [] if p["id"] == "quaternius-animations" for i in p["items"] for c in i.get("clips", [])}
    if not have:
        pytest.skip("3D-паки не скачаны (tools/fetch_packs.py)")
    assert clips <= have, clips - have
    for pose in B.POSES:
        assert (pose, "down") in B3.POSE_CLIPS, pose
    assert set(B3.ARM_POSES) == {"up", "wave"}  # нет клипа — только поворот рук


def _built(which: str) -> dict:
    layout = L.auto_layout(ROOM, ["nightstand"])
    import app.aivideo_blocking as mod

    orig = mod.cast_info
    mod.cast_info = lambda store, pid, shot: INFOS
    try:
        spec = B.auto_spec(None, 1, SHOT, None, layout)
    finally:
        mod.cast_info = orig
    return B.build(spec, which, SHOT, INFOS, layout)


@pytest.mark.parametrize("which", ["start", "end"])
def test_scene_spec_from_built_blocking(which):
    built = _built(which)
    assert built["check"]["ok"], built["check"]
    spec = B3.scene_spec(built, SHOT, INFOS, {"mira": MIRA, "beep": BEEP}, which)
    assert scene3d.validate_spec(spec, check_assets=False)["frames"] == 30
    # Комната: окно и гирлянда, мебель — модели Kenney в габарите раскладки и её цвете.
    room = spec["room"]
    assert room["open"] == ["front"] and room["ceiling"] is False
    assert any(o["type"] == "window" and o["wall"] == "back" for o in room["openings"])
    models = {o["id"]: o for o in spec["objects"] if o.get("model")}
    bed = models["obj_bed"]
    assert bed["model"].endswith("bedDouble.glb") and bed["fit"][0] == pytest.approx(L._obj("bed")["size"][0] / 100, 0.3)
    assert any(o.get("primitive") == "string_lights" for o in spec["objects"])
    lamp = models["obj_lamp"]
    assert lamp["light"]["type"] == "point"
    # Персонажи: Мира — тело со скелетом в позе клипа, цвета карточки, очки; Бип — робот-диск по карточке.
    mira = spec["characters"][0]
    assert mira["hair"]["color"] == "#8fd3c1" and mira["outfit"]["shirt"] == "#b8a9f0" and mira["glasses"]
    assert mira["style"] == "flat" and mira["outfit"]["fit"] == "loose"
    clip = mira["actions"][0]["clip"]
    robot = next(o for o in spec["objects"] if o["id"] == "actor_beep")
    assert robot["primitive"] == "robot_disc" and robot["params"]["screen"] == "rim"
    mug = next(o for o in spec["objects"] if o["id"].startswith("prop_"))
    if which == "start":
        assert clip == B3.POSE_CLIPS[("standing", "holding")][0]
        assert mug["attach"][0]["to"] == "mira:hand_r"  # кружка в руке — сокет кисти
    else:
        assert "attach" not in mug and mug["position"][1] > 0.4  # на тумбочке
        assert mira["poses"][0]["aim"] == B3.ARM_POSES["up"]  # «потянуться»
    # Камера — та же, что выбрал самоконтроль кадра (сантиметры → метры), головы держит в кадре.
    cam = spec["camera"]
    assert cam["keys"][0]["position"] == [round(v / 100, 4) for v in built["camera"]["pos"]]
    assert cam["keepInFrame"]["ids"] == ["mira"] and cam["clamp"] is False
    assert B3.spec_key(spec) == B3.spec_key(json.loads(json.dumps(spec)))


def test_available_respects_switch(monkeypatch):
    monkeypatch.setenv("AIV_BLOCKING_ENGINE", "2d")
    assert B3.available() is False


@pytest.mark.skipif(not B3.available(), reason="нет Node/браузера/бандла/3D-паков")
def test_real_blocking_frame_png():
    spec = B3.scene_spec(_built("end"), SHOT, INFOS, {"mira": MIRA, "beep": BEEP}, "end", width=216, height=384)
    png = B3.render_png(spec)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 20_000
