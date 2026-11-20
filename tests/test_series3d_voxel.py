"""3D-серия в кубическом мире (место ``blockhouse``, ``world: voxel``): режиссёр лифта его не видит, камера автора,
сидит у факела, идёт по домику шагами, появления, своя спека three.js, свой звук события, тень от луны первой.
Серии лифта — без новых полей (ключи кэша прежние)."""
from __future__ import annotations

import sys
from pathlib import Path

from app import series3d_blender as B
from app import series3d_pose as P

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import series3d_voxel_story as story  # noqa: E402

from test_series3d_blender import _job  # noqa: E402


def _voxel_job() -> dict:
    return story.build_job(story.STORY, "fast")


def test_voxel_place_hidden_from_lift_director():
    assert "blockhouse" in B.LOCS and "blockhouse" not in B.LOC_NAMES
    enum = B.GEMINI_SCHEMA["properties"]["shots"]["items"]["properties"]["location"]["enum"]
    assert "blockhouse" not in enum
    assert "кубический" not in B.writer_places()
    assert B.rule_location("он подошёл к окну домика", "cabin") == "cabin"


def test_authored_camera_and_blocking():
    job = _voxel_job()
    perf = B.perform(job)
    ox, oy = B.LOCS["blockhouse"]["origin"]
    cams = {c["n"]: c for c in perf["cameras"]}
    first = story.STORY["shots"][0]["cam"]
    assert cams[0]["pos_a"] == (first["pos"][0] + ox, first["pos"][1] + oy, first["pos"][2])
    assert cams[0]["pos_b"] == (first["to"][0] + ox, first["to"][1] + oy, first["to"][2])
    pr = first["probe"]
    assert cams[0]["probe"] == (pr[0] + ox, pr[1] + oy, pr[2]) and cams[0]["face_w"] == 0.0
    # сидит в «углу» домика (у факела), а не в углу кабины
    (cx, cy, _h) = B.LOCS["blockhouse"]["spots"]["corner"]
    assert perf["shots"][0]["spot"] == [round(cx + ox, 4), round(cy + oy, 4)]
    assert perf["shots"][2]["posture_end"] == "sit"
    # встаёт на камеру и идёт к окну шагами (в домике — до 6 шагов, не склейкой)
    clips = [sg["clip"] for sg in perf["track"]]
    assert "Sit_Floor_StandUp" in clips
    walk = next(sg for sg in perf["track"] if sg["clip"] == B.WALK_CLIP)
    assert walk["end"] - walk["start"] > 2.5
    assert len(story.step_times(perf)) >= 4
    # взгляд автора: на дверь в плане стука (точка места)
    s2 = next(s for s in perf["shots"] if s["n"] == 2)
    f = int((s2["a0"] + 0.4) * B.FPS)
    door = B.loc_target("blockhouse", "door")
    assert tuple(perf["gaze"][f]) == tuple(round(c, 4) for c in door)


def test_appear_track_and_events_sound():
    job = _voxel_job()
    perf = B.perform(job)
    lt = perf["lights"]
    assert lt[0]["appear"] == {"eyes": 0, "guest": 0}
    assert lt[int(20.0 * B.FPS)]["appear"] == {"eyes": 0, "guest": 1}
    assert lt[int(21.5 * B.FPS)]["appear"] == {"eyes": 1, "guest": 1}
    sfx = B.sfx_for(job, perf)
    knocks = [s for s in sfx if s["kind"] == "knock"]
    assert len(knocks) == 3 and all("impactWood" in s["ref"] for s in knocks)
    assert any(s["ref"].endswith("sting.ogg") for s in sfx)
    assert not any(s.get("role") == "ambience" for s in sfx), "фон лифта (гул кабины) в домике не нужен"


def test_lift_series_unchanged_fields():
    job = _job(render_backend="threejs")
    perf = B.perform(job)
    assert "appear" not in perf["lights"][0]
    assert B.three_spec(job) is B.THREE_SPEC
    keys = B.chunk_keys(job, perf)
    job2 = {**job, "three_spec": {"haze": 0.5}}
    keys2 = B.chunk_keys(job2, perf)
    assert [k["key"] for k in keys] != [k["key"] for k in keys2], "своя спека — свои кадры кэша"
    assert [k["key"] for k in keys] == [k["key"] for k in B.chunk_keys(dict(job), perf)]


def test_three_spec_merge():
    sp = B.three_spec({"three_spec": {"haze": 0.8, "post": {"bloom": {"strength": 0.4}}}})
    assert sp["haze"] == 0.8 and sp["post"]["bloom"]["strength"] == 0.4
    assert sp["post"]["bloom"]["threshold"] == B.THREE_SPEC["post"]["bloom"]["threshold"]
    assert sp["post"]["ao"] == B.THREE_SPEC["post"]["ao"]
    assert B.THREE_SPEC["haze"] == 0.6, "общая спека не меняется"


def test_sun_shadow_first():
    lights = [{"shadow": True, "type": "POINT"}, {"shadow": True, "type": "SUN"}, {"shadow": True, "type": "SPOT"}]
    energy = [[50.0, 1.0, 30.0]] * 3
    dist = [[0.3, 20.0, 0.5]] * 3
    assert P.shadow_pick(lights, energy, dist)[0] == 1
    assert P.shadow_pick(lights, [[50.0, 0.0, 30.0]] * 3, dist) == [0, 2], "луна не светит — тени нет"


def test_walk_plan_limit():
    assert B.walk_plan(1.0)[0] == B.MAX_WALK_STEPS
    assert B.walk_plan(1.0, B.loc_max_steps("blockhouse"))[0] == 5
    assert B.loc_max_steps("cabin") == B.MAX_WALK_STEPS
