"""3D-сцены (app/scene3d.py): проверка описания, аргументы рендера, обёртка над node/scene3d/render.mjs
(подменённым и настоящим — если есть Node, браузер, бандл и 3D-паки), 3D-паки в tools/fetch_packs.py."""

from __future__ import annotations

import copy
import json
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from app import scene3d
from app.scene3d import Scene3dError, SceneSpecError, build_args, contact_sheet, render_scene, validate_spec

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "node" / "scene3d" / "examples" / "bedroom.json"


def example() -> dict:
    return json.loads(EXAMPLE.read_text("utf-8"))


def packs(tmp_path: Path) -> Path:
    """Мини-packs.json с 3D-паками: то, на что ссылается пример."""
    spec = example()
    models = {o["model"] for o in spec["objects"] if o.get("model")}
    by_pack: dict[str, list[dict]] = {}
    for ref in models:
        pack, file = ref.split("/")
        by_pack.setdefault(pack, []).append({"file": file})
    by_pack["quaternius-characters"] = [
        {"file": "Superhero_Female_FullBody.gltf", "skinned": True}, {"file": "Hair_Long.gltf", "skinned": True}]
    by_pack["quaternius-animations"] = [
        {"file": "UAL1_Standard.glb", "clips": ["Walk_Loop", "Idle_Loop", "PickUp_Table"]},
        {"file": "UAL2_Standard.glb", "clips": ["Idle_No_Loop", "Yes"]}]
    index = {"packs": [{"id": k, "kind": "model3d", "items": v} for k, v in by_pack.items()]
             + [{"id": "twemoji", "kind": "emoji", "items": [{"file": "1f600.svg"}]}]}
    (tmp_path / "packs.json").write_text(json.dumps(index), "utf-8")
    return tmp_path


def test_example_scene_is_valid(tmp_path):
    res = validate_spec(example(), packs=packs(tmp_path))
    assert res["frames"] == 300 and res["width"] == 1080 and res["height"] == 1920
    assert res["warnings"] == []
    assert "quaternius-characters/Superhero_Female_FullBody.gltf" in res["models"]


def test_validate_without_packs_only_warns(tmp_path):
    res = validate_spec(example(), packs=tmp_path)
    assert any("паки не найдены" in w for w in res["warnings"])
    assert validate_spec(example(), check_assets=False)["warnings"] == []


@pytest.mark.parametrize("mutate, message", [
    (lambda s: s.update(duration=0), "duration"),
    (lambda s: s.update(width=1081), "чётными"),
    (lambda s: s.update(duration=200), "duration"),
    (lambda s: s.update(fps=30, duration=120, width=1080) or s.update(fps=60), "кадров"),
    (lambda s: s["objects"][0].update(model="kenney-furniture/nope.glb"), "нет модели"),
    (lambda s: s["objects"][0].update(model="../../etc/passwd"), "пак/файл"),
    (lambda s: s["objects"].append(dict(s["objects"][0])), "уникальный"),
    (lambda s: s["objects"][1].update(parent="nobody"), "нет родителя"),
    (lambda s: s["characters"][0]["actions"].append({"clip": "Moonwalk_Loop", "start": 1}), "Moonwalk_Loop"),
    (lambda s: s["characters"][0]["path"].reverse(), "не по времени"),
    (lambda s: s["camera"].update(keys=[]), "camera.keys"),
    (lambda s: s["camera"]["keys"][0].update(rel="ghost"), "ghost"),
    (lambda s: s["lights"].append({"type": "laser"}), "lights"),
    (lambda s: s["room"]["openings"][0].update(wall="ceiling"), "wall"),
    (lambda s: next(o for o in s["objects"] if o["id"] == "mug")["attach"][0].update(to="ghost:hand_r"), "ghost"),
])
def test_validate_rejects(tmp_path, mutate, message):
    spec = example()
    mutate(spec)
    with pytest.raises(SceneSpecError, match=message):
        validate_spec(spec, packs=packs(tmp_path))


def test_parent_cycle_rejected():
    spec = example()
    a, b = spec["objects"][0], spec["objects"][1]
    a["parent"], b["parent"] = b["id"], a["id"]
    with pytest.raises(SceneSpecError, match="цикл"):
        validate_spec(spec, check_assets=False)


def test_camera_outside_room_is_a_warning():
    spec = example()
    spec["camera"]["keys"][0]["position"] = [9, 1.5, 0]
    assert any("вне комнаты" in w for w in validate_spec(spec, check_assets=False)["warnings"])


def test_build_args(monkeypatch, tmp_path):
    monkeypatch.setattr(scene3d, "node_bin", lambda: "node")
    monkeypatch.setenv("SCENE3D_GL", "gpu")
    monkeypatch.setenv("SCENE3D_WORKERS", "3")
    args = build_args(tmp_path / "s.json", out=tmp_path / "o.mp4", fps=24, duration=4.5)
    assert args[:2] == ["node", str(scene3d.RENDERER)]
    assert args[args.index("--fps") + 1] == "24" and args[args.index("--duration") + 1] == "4.5"
    assert args[args.index("--workers") + 1] == "3" and args[args.index("--gl") + 1] == "gpu"
    prev = build_args(tmp_path / "s.json", preview=[0, 1.5], preview_dir=tmp_path, workers=1, gl="swiftshader")
    assert prev[prev.index("--preview") + 1] == "0,1.5" and "--out" not in prev


def fake_runner(calls: list, *, code: int = 0, black: list | None = None):
    def run(args, timeout):
        calls.append(args)
        out = Path(args[args.index("--out") + 1])
        spec = json.loads(Path(args[args.index("--spec") + 1]).read_text("utf-8"))
        out.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        info = {"ok": not black, "frames": round(spec["duration"] * spec["fps"]), "fps": spec["fps"],
                "black_frames": black or [], "seconds": 1.0, "warnings": []}
        return subprocess.CompletedProcess(args, code, json.dumps(info) + "\n", "")
    return run


def test_render_scene_with_fake_runner(monkeypatch, tmp_path):
    monkeypatch.setattr(scene3d, "node_bin", lambda: "node")
    calls: list = []
    out = tmp_path / "out" / "scene.mp4"
    res = render_scene(example(), out, fps=24, duration=2, run=fake_runner(calls), check_assets=False)
    assert out.is_file() and res["path"] == str(out.resolve())
    assert res["frames"] == 48 and res["fps"] == 24
    assert not list(out.parent.glob("*.part.mp4")) and not list(out.parent.glob("scene3d-*"))


def test_render_scene_black_frames_and_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(scene3d, "node_bin", lambda: "node")
    with pytest.raises(Scene3dError, match="чёрные кадры"):
        render_scene(example(), tmp_path / "a.mp4", run=fake_runner([], code=3, black=[0, 1]), check_assets=False)

    def broken(args, timeout):
        return subprocess.CompletedProcess(args, 1, "", "scene3d: сцена: TypeError: boom\n")

    with pytest.raises(Scene3dError, match="boom"):
        render_scene(example(), tmp_path / "b.mp4", run=broken, check_assets=False)
    with pytest.raises(SceneSpecError):
        render_scene({"camera": {}}, tmp_path / "c.mp4", run=broken, check_assets=False)


def test_contact_sheet(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("нет ffmpeg")
    video = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=108x192:r=30:d=2", "-pix_fmt", "yuv420p",
                    str(video)], check=True)
    sheet = contact_sheet(video, tmp_path / "sheet.jpg", count=6, cols=3, width=100)
    assert sheet.is_file() and sheet.stat().st_size > 1000


# --------------------------------------------------------------------------- #
# 3D-паки в tools/fetch_packs.py
# --------------------------------------------------------------------------- #


def test_fetch_packs_3d_specs_are_pinned_cc0():
    from tools import fetch_packs as fp

    assert {"kenney-furniture", "quaternius-characters", "quaternius-animations"} <= set(fp.PACK_ORDER)
    assert {"kenney-furniture", "quaternius-characters", "quaternius-animations"} <= fp.SOFT_PACKS
    for spec in [*fp.KENNEY_3D_PACKS, fp.QUATERNIUS_CHARACTERS, *fp.QUATERNIUS_ANIMATIONS["archives"]]:
        assert len(spec["sha256"]) == 64 and int(spec["sha256"], 16) >= 0
    assert fp.QUATERNIUS_LICENSE["license"] == "CC0-1.0"
    assert fp.QUATERNIUS_CHARACTERS["gltf"].match(
        "Universal Base Characters[Standard]/Base Characters/Godot - UE/Superhero_Female_FullBody.gltf")
    assert not fp.QUATERNIUS_CHARACTERS["gltf"].match(
        "Universal Base Characters[Standard]/Base Characters/Unity/Superhero_Female_FullBody.fbx")
    assert fp.KENNEY_3D_PACKS[0]["member"].match("Models/GLTF format/bedDouble.glb")


def test_gltf_json_reads_glb_and_gltf(tmp_path):
    from tools.fetch_packs import _model_extra, describe_item, gltf_json

    doc = {"asset": {"version": "2.0"}, "animations": [{"name": "Walk_Loop"}, {"name": "Idle_Loop"}], "skins": [{}]}
    body = json.dumps(doc).encode()
    body += b" " * (-len(body) % 4)
    glb = b"glTF" + struct.pack("<II", 2, 12 + 8 + len(body)) + struct.pack("<I", len(body)) + b"JSON" + body
    assert gltf_json(glb) == doc
    assert gltf_json(json.dumps(doc).encode()) == doc
    assert _model_extra(doc) == {"clips": ["Walk_Loop", "Idle_Loop"], "skinned": True}
    f = tmp_path / "a.glb"
    f.write_bytes(glb)
    item = describe_item(f, "model3d", "a", ["x"], clips=["Walk_Loop"])
    assert item["clips"] == ["Walk_Loop"] and "width" not in item


def test_kenney_signed_url_flow(monkeypatch):
    from tools import fetch_packs as fp

    seen: list[str] = []

    class Resp:
        def __init__(self, body: bytes):
            self.body = body

        def read(self, *_):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    class Opener:
        addheaders: list = []

        def open(self, req, timeout=None):
            url = req if isinstance(req, str) else req.full_url
            seen.append(url)
            if "/download-link/" in url:
                assert url.endswith("assets/furniture-kit".encode().hex())
                return Resp(b'{"url": "https://kenney.nl/download/x?expires=1"}')
            return Resp(b"<html></html>")

    monkeypatch.setattr(fp, "_session", lambda: Opener())
    assert fp.kenney_signed_url("furniture-kit") == "https://kenney.nl/download/x?expires=1"
    assert seen[0] == "https://kenney.nl/assets/furniture-kit"


# --------------------------------------------------------------------------- #
# Настоящий рендер (если есть всё нужное)
# --------------------------------------------------------------------------- #


def _have_real() -> bool:
    if not (shutil.which("node") and shutil.which("ffmpeg") and scene3d.chromium_path()):
        return False
    p = scene3d.packs_dir()
    return scene3d.BUNDLE.is_file() and (p / "quaternius-characters" / "Superhero_Female_FullBody.gltf").is_file() \
        and (p / "kenney-furniture" / "bedDouble.glb").is_file()


@pytest.mark.skipif(not _have_real(), reason="нет Node/браузера/бандла/3D-паков")
def test_real_render_small(tmp_path):
    spec = copy.deepcopy(example())
    spec.update(width=144, height=256, fps=10, duration=1.2)
    res = render_scene(spec, tmp_path / "s.mp4", workers=2, gl="swiftshader")
    assert res["frames"] == 12 and res["segments"] == 1 and res["black_frames"] == []
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries",
                            "stream=width,height,nb_read_frames,pix_fmt,color_range", "-of", "json", str(tmp_path / "s.mp4")],
                           capture_output=True, text=True, check=True)
    st = json.loads(probe.stdout)["streams"][0]
    assert (st["width"], st["height"], int(st["nb_read_frames"])) == (144, 256, 12)
    assert st["pix_fmt"] == "yuv420p" and st.get("color_range") == "tv"
    frames = scene3d.preview_frames(spec, [0.0, 1.0], tmp_path / "prev")
    assert len(frames) == 2 and all(Path(f["file"]).is_file() for f in frames)


def test_follow_camera_keys_validated():
    spec = example()
    follow = [k for k in spec["camera"]["keys"] if k.get("follow")]
    assert follow and all(k["follow"] == "girl" for k in follow), "пример снимает за плечом героини"
    assert validate_spec(spec, check_assets=False)["warnings"] == []
    bad = example()
    bad["camera"]["keys"][-1]["follow"] = "ghost"
    with pytest.raises(SceneSpecError, match="ghost"):
        validate_spec(bad, check_assets=False)
    bad = example()
    bad["camera"]["keys"][-1]["look"] = "no_such_object"
    with pytest.raises(SceneSpecError, match="no_such_object"):
        validate_spec(bad, check_assets=False)
    high = example()
    high["camera"]["keys"][-1]["height"] = 1.4
    assert any("роста" in w for w in validate_spec(high, check_assets=False)["warnings"])
    assert scene3d.FOLLOW_HEIGHT == (0.95, 1.05)
    engine = (ROOT / "node" / "scene3d" / "src" / "engine.ts").read_text("utf-8")
    assert "FOLLOW_HEIGHT_MIN = 0.95" in engine and "FOLLOW_HEIGHT_MAX = 1.05" in engine
