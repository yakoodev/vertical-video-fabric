"""3D-серия, бэкенд threejs («Blender ставит позу, three.js рисует»): формат потока поз, выбор бэкенда по заданию,
ключи кэша, порядок процессов (сначала один Blender для поз, потом один Chromium), склейка."""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from app import series3d_blender as B
from app import series3d_pose as P
from test_series3d_blender import JPG, _job


# --------------------------------------------------------------------------- #
# Формат
# --------------------------------------------------------------------------- #

def _header(n_obj=2, n_bones=3, n_mats=1, n_lights=2, n_fogs=1, frames=(5, 6, 7)) -> dict:
    objects = [{"name": f"o{i}", "dyn": True, "skin": False, "hero": False} for i in range(n_obj)] + \
              [{"name": "static", "dyn": False, "skin": False, "hero": False}]
    mats = [{"id": i, "name": f"m{i}", "anim": i < n_mats} for i in range(n_mats + 2)]
    h = {"format": P.FORMAT, "frames": list(frames), "objects": objects, "bones": [f"b{i}" for i in range(n_bones)],
         "materials": mats, "lights": [{"name": f"l{i}", "shadow": True} for i in range(n_lights)],
         "fogs": [{"name": f"f{i}"} for i in range(n_fogs)]}
    h["layout"] = P.header_layout(h)
    return h


def _m(v: float) -> list[float]:
    return [v + k for k in range(12)]


def test_layout_roundtrip_and_stream_append(tmp_path):
    h = _header()
    lay = h["layout"]
    # кадр: 1 + камера 21 + 2·12 объектов + 3·12 костей + 1 материал + 2·13 источников + 1 взвесь
    assert lay["stride"] == 1 + 21 + 24 + 36 + 1 + 26 + 1
    assert lay["objects"] < lay["bones"] < lay["materials"] < lay["lights"] < lay["fogs"] < lay["stride"]
    (tmp_path / "scene.json").write_text(json.dumps(h), "utf-8")
    with (tmp_path / "frames.bin").open("wb") as fh:
        for k, f in enumerate(h["frames"]):
            cam = _m(100 + k) + [30.0, 36.0, 1.5, 2.2, 0.25, 1.0] + [0.1, 0.9, 0.2]
            fh.write(P.pack_record(lay, f, cam, [_m(10 * k), _m(20 * k)], [_m(1), _m(2), _m(3)], [7.5 + k],
                                   [[60.0] + _m(0), [9.0] + _m(5)], [0.35]))
            fh.flush()
            assert P.record_count(tmp_path, h) == k + 1, "запись видна сразу после кадра (поток)"
    hh = P.read_header(tmp_path)
    r = P.read_record(tmp_path, hh, P.frame_index(hh)[6])
    assert r["frame"] == 6 and r["camera"]["lens"] == 30.0 and r["camera"]["exposure"] == pytest.approx(0.25)
    assert r["camera"]["face"] == pytest.approx([0.1, 0.9, 0.2]) and r["camera"]["matrix"][0] == 101
    assert r["objects"][1][0] == 20 and r["bones"][2][0] == 3 and r["materials"] == [8.5]
    assert r["lights"][0]["energy"] == 60 and r["lights"][1]["matrix"][0] == 5 and r["fogs"] == pytest.approx([0.35])
    with pytest.raises(IndexError):
        P.read_record(tmp_path, hh, 3)


def test_pack_record_rejects_wrong_lengths():
    lay = _header()["layout"]
    cam = [0.0] * P.CAMERA_FIELDS
    ok = dict(objects=[_m(0)] * 2, bones=[_m(0)] * 3, materials=[1.0], lights=[[1.0] + _m(0)] * 2, fogs=[0.1])
    P.pack_record(lay, 1, cam, **ok)
    for key, bad in (("objects", [_m(0)]), ("bones", [_m(0)[:11]] * 3), ("materials", []), ("lights", [[1.0] + _m(0)])):
        with pytest.raises(ValueError):
            P.pack_record(lay, 1, cam, **{**ok, key: bad})
    with pytest.raises(ValueError):
        P.pack_record(lay, 1, cam[:-1], **ok)


def test_wrong_format_refused(tmp_path):
    (tmp_path / "scene.json").write_text(json.dumps({"format": "other/9"}), "utf-8")
    with pytest.raises(ValueError):
        P.read_header(tmp_path)


def test_export_frames_take_previous_frame_for_motion_blur():
    keys = [{"key": "a", "f0": 1, "f1": 30}, {"key": "b", "f0": 31, "f1": 60}, {"key": "c", "f0": 61, "f1": 90}]
    todo = {"a": [10, 11, 12], "b": [31, 32]}
    fr = P.export_frames(keys, todo)
    assert fr == [9, 10, 11, 12, 31, 32], "прошлый кадр (f−1) внутри плана — для подкадров затвора; за склейку не лезем"


def test_shadow_pick_three_most_important_near_face():
    lights = [{"shadow": True}, {"shadow": False}, {"shadow": True}, {"shadow": True}, {"shadow": True}]
    energy = [[70.0, 500.0, 5.0, 30.0, 0.0], [70.0, 500.0, 5.0, 30.0, 0.0]]
    dist = [[0.5, 0.1, 0.3, 2.0, 0.2], [0.5, 0.1, 0.3, 2.0, 0.2]]
    pick = P.shadow_pick(lights, energy, dist)
    assert pick == [0, 2, 3], "без тени и выключенные не берутся; важность — мощность / расстояние²"
    assert len(P.shadow_pick(lights, energy, dist, limit=2)) == 2


# --------------------------------------------------------------------------- #
# Выбор бэкенда и кэш
# --------------------------------------------------------------------------- #

def test_backend_selection_default_blender():
    assert B.backend({}) == "blender" == B.DEFAULT_BACKEND
    assert B.backend({"render_backend": "ThreeJS"}) == "threejs"
    assert B.backend({"render_backend": "vulkan"}) == "blender"
    assert _job()["render_backend"] == "blender"
    assert _job(render_backend="threejs")["render_backend"] == "threejs"


def test_cache_keys_blender_unchanged_threejs_separate():
    job = _job()
    perf = B.perform(job)
    plain = {k: v for k, v in job.items() if k != "render_backend"}
    k_b = B.chunk_keys(job, perf)
    assert [c["key"] for c in k_b] == [c["key"] for c in B.chunk_keys(plain, perf)], "у blender ключи прежние"
    k_t = B.chunk_keys({**job, "render_backend": "threejs"}, perf)
    assert not {c["key"] for c in k_b} & {c["key"] for c in k_t}, "кадры three.js — свой кэш"
    assert {c["ext"] for c in k_t} == {"jpg"}
    final = {**job, "quality": "final", "render_backend": "threejs"}
    assert {c["ext"] for c in B.chunk_keys(final, B.perform(final))} == {"jpg"}


def test_three_plan_only_missing_chunks(tmp_path):
    job = {**_job(quality="preview"), "render_backend": "threejs"}
    keys = B.chunk_keys(job, B.perform(job))
    todo = {keys[1]["key"]: [keys[1]["f0"], keys[1]["f0"] + 1]}
    plan = B.three_plan(job, keys, todo, tmp_path / "pose", tmp_path / "cache", tmp_path, "bundle.js")
    assert [c["key"] for c in plan["chunks"]] == [keys[1]["key"]] and plan["chunks"][0]["todo"] == todo[keys[1]["key"]]
    assert (plan["width"], plan["height"]) == (540, 960), "размер кадра — по пресету"
    assert plan["spec"]["blurSteps"] >= 2 and plan["spec"]["lut"] is True


class FakeProc:
    """Blender в режиме поз (пишет заголовок и записи) или node series_render.mjs (пишет JPEG кадров по плану)."""

    calls: list[tuple[str, dict]] = []

    def __init__(self, args, stdout=None, stderr=None, text=None, encoding=None, errors=None, cwd=None):
        self.code = 0
        if any(str(a).endswith("series_render.mjs") for a in args):
            plan = json.loads(Path(args[args.index("--plan") + 1]).read_text("utf-8"))
            FakeProc.calls.append(("node", plan))
            lines, n = [], 0
            total = sum(len(c["todo"]) for c in plan["chunks"])
            for c in plan["chunks"]:
                d = Path(plan["cache"]) / c["key"]
                d.mkdir(parents=True, exist_ok=True)
                for f in c["todo"]:
                    (d / B.frame_name(f, c["ext"])).write_bytes(JPG)
                    n += 1
                    lines.append(f"PROGRESS {n} {total} shot={c['n']} frame={f}\n")
            Path(plan["timing"]).write_text(json.dumps({"ms_per_frame": 120, "black": []}), "utf-8")
        else:
            cfg = json.loads(Path(args[args.index("--config") + 1]).read_text("utf-8"))
            FakeProc.calls.append(("blender", cfg))
            assert cfg["backend"] == "threejs" and not cfg["todo"], "Blender в режиме поз ничего не рендерит"
            pose = Path(cfg["export_dir"])
            pose.mkdir(parents=True, exist_ok=True)
            (pose / "scene.json").write_text(json.dumps({"format": P.FORMAT}), "utf-8")
            (pose / "frames.bin").write_bytes(b"\x00" * 16)
            Path(cfg["out_dir"], "report.json").write_text(json.dumps({"shots": [{"n": 0, "face_in": 5}]}), "utf-8")
            lines = [f"PROGRESS {len(cfg['pose_frames'])} {len(cfg['pose_frames'])} pose\n"]
        self.stdout = io.StringIO("".join(lines))

    def wait(self):
        return self.code

    def kill(self):
        pass


def test_render_local_threejs_pose_then_draw_then_stitch(tmp_path, monkeypatch):
    job = {**_job(quality="draft"), "render_backend": "threejs"}
    job["duration"] = 4.0
    job["shots"] = [s for s in job["shots"] if s["t0"] < 4.0]
    job["shots"][-1]["t1"] = 4.0
    monkeypatch.setattr(B, "find_blender", lambda explicit=None: "blender")
    monkeypatch.setattr(B, "node_bin", lambda: "node")
    monkeypatch.setattr(B, "seriesview_bundle", lambda: tmp_path / "seriesview.js")
    monkeypatch.setattr(B, "scene_config", lambda job, work, cache, packs=None: {"cache": str(cache), "out_dir": str(work)})
    monkeypatch.setattr(B.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(B, "video_flat", lambda path, *a, **k: False)
    encoded = []
    monkeypatch.setattr(B.subprocess, "run", lambda args, **kw: encoded.append(args) or Path(args[-1]).write_bytes(b"mp4"))
    FakeProc.calls = []
    progress = []
    res = B.render_local(job, tmp_path / "a.mp4", work=tmp_path / "w", cache=tmp_path / "cache",
                         progress=lambda p, n: progress.append(p))
    assert [c[0] for c in FakeProc.calls] == ["blender", "node"], "по очереди: один Blender (позы), потом один Chromium"
    pose_cfg, plan = FakeProc.calls[0][1], FakeProc.calls[1][1]
    assert pose_cfg["pose_frames"][0] == 1 and len(pose_cfg["pose_frames"]) == res["frames"] == 120
    assert sum(len(c["todo"]) for c in plan["chunks"]) == 120 and res["backend"] == "threejs" and res["processes"] == 1
    assert res["report"]["shots"][0]["face_in"] == 5, "проверки кадра (лицо в кадре, вещи видны) — из Blender, как раньше"
    assert res["report"]["threejs"]["ms_per_frame"] == 120 and progress[-1] == 1.0
    assert any("f_%05d.jpg" in str(a) for a in encoded[-1])
    # повтор — всё в кэше: ни Blender, ни браузера
    res2 = B.render_local(job, tmp_path / "b.mp4", work=tmp_path / "w2", cache=tmp_path / "cache")
    assert len(FakeProc.calls) == 2 and res2["rendered"] == 0


def test_render_local_blender_backend_does_not_start_node(tmp_path, monkeypatch):
    from test_series3d_blender import FakeBlender

    job = _job(quality="draft")
    job["duration"] = 2.0
    job["shots"] = [s for s in job["shots"] if s["t0"] < 2.0]
    job["shots"][-1]["t1"] = 2.0
    monkeypatch.setattr(B, "find_blender", lambda explicit=None: "blender")
    monkeypatch.setattr(B, "scene_config", lambda job, work, cache, packs=None: {"cache": str(cache), "out_dir": str(work)})
    monkeypatch.setattr(B.subprocess, "Popen", FakeBlender)
    monkeypatch.setattr(B, "video_flat", lambda path, *a, **k: False)
    monkeypatch.setattr(B, "render_threejs", lambda *a, **k: pytest.fail("threejs не должен запускаться"))
    monkeypatch.setattr(B.subprocess, "run", lambda args, **kw: Path(args[-1]).write_bytes(b"mp4"))
    FakeBlender.calls, FakeBlender.fail_after = [], None
    res = B.render_local(job, tmp_path / "a.mp4", work=tmp_path / "w", cache=tmp_path / "cache", workers=1)
    assert res["backend"] == "blender" and "backend" not in FakeBlender.calls[0]


def test_episode_option_selects_backend_and_keeps_blender_key():
    from app import stories_3d as S3

    ep = {"id": 3, "options": {}, "script": {"sentences": [{"text": "Свет погас."}]}, "beats": None}
    ref = {"asset_id": 9}
    assert S3.render_backend(ep) == "blender"
    k_blender = S3.blender_key(ep, ref)
    ep3 = {**ep, "options": {"render_backend": "threejs"}}
    assert S3.render_backend(ep3) == "threejs" and S3.blender_key(ep3, ref) != k_blender
    assert S3.blender_key({**ep, "options": {"render_backend": "blender"}}, ref) == k_blender


# --------------------------------------------------------------------------- #
# Часть 3: люк в потолке кабины (защёлка, крышка), вид на потолок снизу
# --------------------------------------------------------------------------- #

def test_rules_open_hatch_from_words():
    shot = {"text": "Даня повернул защёлку и открыл люк в потолке.", "role": "solution"}
    ch = B.rule_choice(shot, [], True)
    assert ch["action"] == "open_hatch" and ch["target"] == "hatch"
    assert B.rule_choice({"text": "Наверху был люк.", "role": ""}, [], True)["action"] == "look_ceiling"
    assert "open_hatch" in B.ACTIONS and "open_hatch" in B.HAND_ACTIONS and "hatch" in B.TARGETS


def test_open_hatch_tracks_hand_camera_and_sounds():
    job = _job()
    plain = B.perform(job)
    assert "hatch" not in plain["lights"][0], "у серий без люка дорожки (и ключи кэша) прежние"
    s = next(x for x in job["shots"] if x["t0"] > 8.0 and (x.get("location") or "cabin") == "cabin")
    s.update(action="open_hatch", target="hatch", camera="detail", by="rules")
    perf = B.perform(job)
    f0, f1 = int(s["t0"] * 30), int(s["t1"] * 30)
    lt = perf["lights"]
    assert lt[f0]["hatch"] == 0.0 and lt[f0]["latch"] == 0.0
    assert lt[-1]["hatch"] == 1.0 and lt[-1]["latch"] == 1.0, "люк остаётся открытым"
    turned = next(i for i in range(f0, len(lt)) if lt[i]["latch"] >= 0.99)
    opened = next(i for i in range(f0, len(lt)) if lt[i]["hatch"] >= 0.99)
    assert turned < opened, "сначала защёлка, потом крышка"
    up = [h for h in perf["hands"]["r"][f0:f1] if h and h[2] == "hatch"]
    assert up and max(p[0][2] for p in up) > 0.8, "рука тянется вверх к защёлке"
    assert any(c["kind"] == "hatch" for c in perf["contacts"])
    cam = next(c for c in perf["cameras"] if c["n"] == s["n"])
    assert cam["kind"] == "detail" and cam["pos_a"][2] < 0.6 and cam["point"][2] > 1.2, "камера снизу смотрит на потолок"
    kinds = {x["kind"] for x in B.sfx_for(job, perf)}
    assert {"latch", "hatch", "hatch_open"} <= kinds
    shown = B.show_items("cabin", {"text": "Защёлка люка поддалась, наверху тросы."})
    assert {"latch", "hatch", "cables"} <= {x["item"] for x in shown} or {"latch", "cables"} <= {x["item"] for x in shown}
    assert all(x.get("point") for x in shown if x["item"] in ("latch", "cables"))
