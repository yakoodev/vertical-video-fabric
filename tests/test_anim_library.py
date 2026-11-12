"""Библиотека движений VRM: сопоставление костей разных скелетов, перенос (поправка позы покоя), каталог и сборка
(tools/anim_retarget.py, tools/anim_library.py, app/animlib.py), проверка клипа в 3D-плане."""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from app import animlib, presets3d
from tools import anim_library as L
from tools import anim_retarget as R


def chain(names_parents: list[tuple[str, int]]) -> tuple[list[str], list[int]]:
    return [n for n, _ in names_parents], [p for _, p in names_parents]


def humanoid(style: str) -> tuple[list[str], list[int]]:
    """Скелет гуманоида в стиле Mixamo / UE / KayKit / Rigify-метариг (с опечатками и «приклеенной» стороной)."""
    if style == "mixamo":
        n = ["mixamorig:Hips", "mixamorig:Spine", "mixamorig:Spine1", "mixamorig:Spine2", "mixamorig:Neck", "mixamorig:Head"]
        out = [(x, i - 1) for i, x in enumerate(n)]
        for s, S in (("left", "Left"), ("right", "Right")):
            b = len(out)
            out += [(f"mixamorig:{S}Shoulder", 3), (f"mixamorig:{S}Arm", b), (f"mixamorig:{S}ForeArm", b + 1), (f"mixamorig:{S}Hand", b + 2),
                    (f"mixamorig:{S}HandIndex1", b + 3), (f"mixamorig:{S}HandIndex2", b + 4)]
            b = len(out)
            out += [(f"mixamorig:{S}UpLeg", 0), (f"mixamorig:{S}Leg", b), (f"mixamorig:{S}Foot", b + 1), (f"mixamorig:{S}ToeBase", b + 2)]
        return chain(out)
    if style == "ue":
        out = [("root", -1), ("pelvis", 0), ("spine_01", 1), ("spine_02", 2), ("spine_03", 3), ("neck_01", 4), ("Head", 5)]
        for s in ("l", "r"):
            b = len(out)
            out += [(f"clavicle_{s}", 4), (f"upperarm_{s}", b), (f"lowerarm_{s}", b + 1), (f"hand_{s}", b + 2), (f"thumb_01_{s}", b + 3)]
            b = len(out)
            out += [(f"thigh_{s}", 1), (f"calf_{s}", b), (f"foot_{s}", b + 1), (f"ball_{s}", b + 2), (f"ball_leaf_{s}", b + 3)]
        return chain(out)
    if style == "kaykit":
        out = [("Rig_Medium", -1), ("root", 0), ("hips", 1), ("spine", 2), ("chest", 3), ("head", 4)]
        for s in ("l", "r"):
            b = len(out)
            out += [(f"upperarm.{s}", 4), (f"lowerarm.{s}", b), (f"wrist.{s}", b + 1), (f"hand.{s}", b + 2), (f"handslot.{s}", b + 3)]
            b = len(out)
            out += [(f"upperleg.{s}", 2), (f"lowerleg.{s}", b), (f"foot.{s}", b + 1), (f"toes.{s}", b + 2)]
        return chain(out)
    # Метариг Blender с опечатками: «pevis», «LoweArmL», сторона приклеена заглавной.
    out = [("Armature", -1), ("pevis", 0), ("spine", 1), ("Chest", 2), ("neck", 3), ("head", 4)]
    for S in ("L", "R"):
        b = len(out)
        out += [(f"shoulder{S}", 3), (f"upper_arm{S}", b), (f"LoweArm{S}", b + 1), (f"hand{S}", b + 2)]
        b = len(out)
        out += [(f"thigh{S}", 1), (f"shin{S}", b), (f"foot{S}", b + 1), (f"foot{S}_end", b + 2)]
    return chain(out)


@pytest.mark.parametrize("style", ["mixamo", "ue", "kaykit", "metarig"])
def test_bone_mapping_across_rigs(style: str) -> None:
    names, parents = humanoid(style)
    m = R.map_bones(names, parents)
    got = {k: names[v] for k, v in m.items()}
    expect = {
        "mixamo": {"hips": "mixamorig:Hips", "spine": "mixamorig:Spine", "chest": "mixamorig:Spine1", "upperChest": "mixamorig:Spine2",
                   "neck": "mixamorig:Neck", "leftUpperArm": "mixamorig:LeftArm", "leftLowerArm": "mixamorig:LeftForeArm",
                   "leftUpperLeg": "mixamorig:LeftUpLeg", "leftLowerLeg": "mixamorig:LeftLeg", "rightToes": "mixamorig:RightToeBase",
                   "leftShoulder": "mixamorig:LeftShoulder", "leftIndexProximal": "mixamorig:LeftHandIndex1"},
        "ue": {"hips": "pelvis", "upperChest": "spine_03", "neck": "neck_01", "leftShoulder": "clavicle_l", "leftLowerLeg": "calf_l",
               "rightToes": "ball_r", "leftThumbMetacarpal": "thumb_01_l"},
        "kaykit": {"hips": "hips", "spine": "spine", "chest": "chest", "leftHand": "wrist.l", "rightUpperLeg": "upperleg.r"},
        "metarig": {"hips": "pevis", "leftLowerArm": "LoweArmL", "rightHand": "handR", "rightLowerLeg": "shinR", "leftShoulder": "shoulderL",
                    "neck": "neck", "chest": "Chest"},
    }[style]
    for k, v in expect.items():
        assert got.get(k) == v, (style, k, got.get(k))
    assert got["head"].lower().endswith("head")
    # Левые — левые, правые — правые.
    assert all(("l" in names[v].lower().replace("leaf", "")) for k, v in m.items() if k.startswith("left"))


def test_namespace_prefix_and_duplicate_pivots() -> None:
    """Экспорт FBX из Maya: префикс пространства имён и цепочки одноимённых узлов-опор."""
    base, parents = humanoid("ue")
    names = ["SapphiArt_19" + n for n in base]
    names2 = names + ["SapphiArt_19Head"]  # «Head» группы мешей — не под тазом
    parents2 = parents + [0]
    m = R.map_bones(names2, parents2)
    assert names2[m["head"]] == "SapphiArt_19Head" and m["head"] != len(names2) - 1
    assert names2[m["hips"]] == "SapphiArt_19pelvis"


def tpose_skeleton() -> dict:
    """Нейтральный скелет в Т-позе (Y вверх, лицом +Z, левая рука +X) и клип «правая рука опущена на 70°»."""
    nodes = []

    def add(name: str, parent: int, t: list[float]) -> int:
        nodes.append({"name": name, "parent": parent, "t": t, "r": [0, 0, 0, 1]})
        return len(nodes) - 1

    hips = add("hips", -1, [0, 1.0, 0])
    sp = add("spine", hips, [0, 0.1, 0])
    ch = add("chest", sp, [0, 0.15, 0])
    nk = add("neck", ch, [0, 0.2, 0])
    add("head", nk, [0, 0.1, 0])
    for s, x in (("l", 1), ("r", -1)):
        ua = add(f"upperarm_{s}", ch, [0.15 * x, 0.15, 0])
        la = add(f"lowerarm_{s}", ua, [0.28 * x, 0, 0])
        add(f"hand_{s}", la, [0.25 * x, 0, 0])
        th = add(f"thigh_{s}", hips, [0.1 * x, -0.05, 0])
        cf = add(f"calf_{s}", th, [0, -0.45, 0])
        ft = add(f"foot_{s}", cf, [0, -0.42, 0])
        add(f"ball_{s}", ft, [0, -0.05, 0.12])
    ua_r = [i for i, n in enumerate(nodes) if n["name"] == "upperarm_r"][0]
    a = math.radians(70) / 2
    down = [[0, 0, math.sin(a), math.cos(a)]] * 3  # вокруг +Z: рука -X → вниз
    return {"nodes": nodes, "clips": {"T-Pose": {"fps": 30, "frames": 1, "tracks": {}},
                                      "Down": {"fps": 30, "frames": 3, "tracks": {ua_r: {"r": down}}}}, "source": "test"}


def test_retarget_tpose_is_rest_and_rotation_carries_over() -> None:
    sk = tpose_skeleton()
    rest = R.retarget(sk, "T-Pose")
    for b, qs in rest["bones"].items():
        assert abs(abs(qs[0][3]) - 1) < 1e-4, (b, qs[0])
    out = R.retarget(sk, "Down")
    q = np.array(out["bones"]["rightUpperArm"][1])
    # Мировое направление правой руки: -X, повернуть на 70° вокруг +Z → вниз-в сторону.
    d = R.qrot(q, np.array([-1.0, 0, 0]))
    assert d[1] < -0.9 and d[0] < -0.3
    assert out["legLength"] == pytest.approx(0.87, abs=1e-3)
    assert out["spine"] == "hips" and out["root"][0] == [0, 0, 0]


def test_retarget_handles_axis_conversion() -> None:
    """Скелет в Z-up (как UE в glTF без поворота корня): тот же результат после поворота сцены."""
    sk = tpose_skeleton()
    a = math.radians(-90) / 2
    rot = [math.sin(a), 0, 0, math.cos(a)]
    sk2 = json.loads(json.dumps(sk))
    sk2["nodes"].insert(0, {"name": "root", "parent": -1, "t": [0, 0, 0], "r": rot})
    for n in sk2["nodes"][1:]:
        n["parent"] += 1
    for c in sk2["clips"].values():
        c["tracks"] = {int(k) + 1: v for k, v in c["tracks"].items()}
    q1 = np.array(R.retarget(sk, "Down")["bones"]["rightUpperArm"][1])
    q2 = np.array(R.retarget(sk2, "Down")["bones"]["rightUpperArm"][1])
    assert abs(abs(float(np.dot(q1, q2))) - 1) < 1e-4


def test_catalog_names_unique_and_complete() -> None:
    cat = L.catalog()
    names = [c["name"] for c in cat["clips"]]
    assert len(names) == len(set(names)) and len(names) >= 60
    for want in ("idle_breathe", "sit_idle", "walk", "run", "wave", "cheer", "sad_idle", "angry", "talk_gesture_a", "dance_a",
                 "sit_down", "stand_up", "jump", "fall", "look_around", "typing"):
        assert want in names, want
    for c in cat["clips"]:
        assert c.get("ru") and c.get("layer") in ("full", "upper"), c["name"]
        if c.get("from"):
            assert c["from"] in names
        elif c.get("pack") != "cmu":
            assert c["pack"] in cat["packs"] and cat["packs"][c["pack"]]["license"] == "CC0-1.0"


def test_derive_is_deterministic_and_loops_cleanly() -> None:
    base = R.retarget(tpose_skeleton(), "Down")
    base = {**base, "frames": 31, "bones": {k: v[:1] * 31 for k, v in base["bones"].items()}, "root": [[0, 0, 0]] * 31}
    e = {"name": "x", "from": "y", "loop": True, "speed": 0.5, "add": {"head": [10, 0, 0]}, "sway": {"hz": 0.7, "bones": {"neck": [0, 8, 0]}}}
    a, b = L.derive(base, e), L.derive(base, e)
    assert a == b and a["frames"] == 61
    neck = np.array(a["bones"]["neck"])
    assert abs(float(np.dot(neck[0], neck[-1]))) > 0.9999  # целое число колебаний: петля без шва
    head = np.array(a["bones"]["head"][0])
    assert 2 * math.degrees(math.acos(min(1, abs(head[3])))) == pytest.approx(10, abs=0.1)


def test_library_index_and_licenses(tmp_path) -> None:
    entries = [{"name": "wave", "ru": "Машет", "frames": 10, "duration": 0.3, "layer": "upper", "tags": [], "loop": True,
                "file": "anim/wave.json", "source": "KayKit", "license": "CC0-1.0"}]
    L.write_index(tmp_path, entries)
    L.write_index(tmp_path, [{**entries[0], "name": "in_mine", "license": "свой"}])
    idx = json.loads((tmp_path / "index.json").read_text("utf-8"))["clips"]
    assert [c["name"] for c in idx] == ["wave", "in_mine"]
    lic = L.write_licenses(tmp_path).read_text("utf-8")
    assert "CC0-1.0" in lic and "Quaternius" in lic and "Kay Lousberg" in lic and "Mixamo" in lic


def test_animlib_search_and_plan_validation(tmp_path) -> None:
    (tmp_path / "anim").mkdir()
    (tmp_path / "anim" / "index.json").write_text(json.dumps({"clips": [
        {"name": "dance_a", "duration": 1.0, "source": "UAL", "license": "CC0-1.0"},
        {"name": "in_my_dance", "ru": "мой танец", "tags": ["свой"], "layer": "full", "duration": 2.0, "license": "свой"}]}), "utf-8")
    found = animlib.search("танец", vrm_dir=tmp_path)
    assert {c["name"] for c in found} >= {"dance_a", "in_my_dance"}
    assert next(c for c in found if c["name"] == "dance_a")["built"] is True
    assert all(c["layer"] == "upper" for c in animlib.search(layer="upper", vrm_dir=tmp_path))
    ok = presets3d.validate_presets({"performance": [{"who": "hero", "preset": "anim", "clip": "dance_a", "t": 1},
                                                     {"who": "hero", "preset": "stand_up", "t": 3}, {"who": "hero", "preset": "cheer", "t": 5}]})
    assert ok["performance"][0]["clip"] == "dance_a"
    with pytest.raises(presets3d.PresetError) as err:
        presets3d.validate_presets({"performance": [{"who": "hero", "preset": "anim", "clip": "danse_a", "t": 1}]})
    assert "dance_a" in str(err.value)
