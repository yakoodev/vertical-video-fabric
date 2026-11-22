"""«3D-модели» проекта (app/assets3d.py, app/assets3d_preview.py, app/anim3d.py): лёгкое превью (WebP, meshopt) без
поломки VRM, справка о модели, ручки API (загрузка, повтор, группы, файлы, обложка, удаление), анимации и импорт."""

from __future__ import annotations

import gzip
import io
import json
import shutil
import struct
from pathlib import Path

import pytest
from PIL import Image

from app import anim3d as AN
from app import assets3d as A
from app import assets3d_preview as AP
from app import projects as P
from test_api_pipeline import _client


def _png(size: int, color=(200, 40, 60, 255)) -> bytes:
    im = Image.new("RGBA", (size, size), color)
    for x in range(0, size, 16):  # не однотонная — иначе WebP выйдет почти пустым
        im.putpixel((x, x), (0, 0, 0, 128))
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


def make_glb(path: Path, *, vrm: bool = False, tex: int = 2048, tris: int = 200) -> Path:
    """Сетка из tris треугольников, текстура tex×tex; vrm — с расширением VRM 0.x (мета-портрет, выражения)."""
    import random

    rnd = random.Random(1)
    n = tris * 3
    pos = b"".join(struct.pack("<3f", rnd.random(), rnd.random(), rnd.random()) for _ in range(n))
    uv = b"".join(struct.pack("<2f", rnd.random(), rnd.random()) for _ in range(n))
    idx = b"".join(struct.pack("<H", i) for i in range(n))
    idx += b"\0" * (-len(idx) % 4)
    img = _png(tex)
    thumb = _png(64, (10, 200, 10, 255))
    views, bin_ = [], bytearray()
    for data in (pos, uv, idx, img, thumb):
        views.append({"buffer": 0, "byteOffset": len(bin_), "byteLength": len(data)})
        bin_ += data
        bin_ += b"\0" * (-len(bin_) % 4)
    views[0]["target"] = 34962
    views[1]["target"] = 34962
    views[2]["target"] = 34963
    views[2]["byteLength"] = n * 2
    doc = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": len(bin_)}],
        "bufferViews": views,
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": n, "type": "VEC3", "min": [0, 0, 0], "max": [1, 1, 1]},
            {"bufferView": 1, "componentType": 5126, "count": n, "type": "VEC2"},
            {"bufferView": 2, "componentType": 5123, "count": n, "type": "SCALAR"},
        ],
        "images": [{"bufferView": 3, "mimeType": "image/png"}, {"bufferView": 4, "mimeType": "image/png"}],
        "textures": [{"source": 0}, {"source": 1}],
        "materials": [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "TEXCOORD_0": 1}, "indices": 2, "material": 0}]}],
        "nodes": [{"mesh": 0}],
        "scenes": [{"nodes": [0]}],
    }
    if vrm:
        doc["extensionsUsed"] = ["VRM"]
        doc["extensions"] = {"VRM": {
            "meta": {"title": "Тест", "author": "мы", "texture": 1},
            "humanoid": {"humanBones": [{"bone": "hips", "node": 0}]},
            "blendShapeMaster": {"blendShapeGroups": [{"presetName": "joy", "name": "Joy"}, {"presetName": "a", "name": "A"},
                                                       {"presetName": "unknown", "name": "Smirk"}]},
            "secondaryAnimation": {"boneGroups": [{"bones": [0]}]},
            "materialProperties": [{"textureProperties": {"_MainTex": 0}}],
        }}
    path.write_bytes(AP.write_glb(doc, bytes(bin_)))
    return path


@pytest.fixture
def env(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    monkeypatch.setenv("ASSETS3D_DIR", str(tmp_path / "assets3d"))
    project = P.create_project(store, "Невезучий", kind="series")
    return client, store, int(project["id"]), tmp_path


def _upload(client, pid: int, path: Path, **form):
    with path.open("rb") as fh:
        return client.post(f"/api/projects/{pid}/assets3d", files={"file": (path.name, fh, "application/octet-stream")},
                           data={k: str(v) for k, v in form.items()})


# --------------------------------------------------------------------------- превью


def test_preview_shrinks_textures_keeps_vrm_and_compresses_geometry(tmp_path):
    src = make_glb(tmp_path / "hero.vrm", vrm=True, tex=2048, tris=3000)
    dst = tmp_path / "out" / "preview.glb"
    thumb = tmp_path / "out" / "thumb.webp"
    info = AP.build_preview(src, dst, thumb=thumb)
    assert info["triangles"] == 3000 and info["materials"] == 1 and info["textures"] == 2
    assert info["vrm"] == "0.x"
    assert info["expressions"] == ["happy", "aa", "Smirk"]
    assert info["spring_bones"] == 1
    assert info["meta"]["title"] == "Тест"
    assert thumb.is_file() and Image.open(thumb).format == "WEBP"
    doc, bin_ = AP.read_model(dst)
    # VRM-расширение и номера на месте, картинки — WebP через EXT_texture_webp.
    assert doc["extensions"]["VRM"]["meta"]["texture"] == 1
    assert doc["extensions"]["VRM"]["materialProperties"][0]["textureProperties"]["_MainTex"] == 0
    assert "EXT_texture_webp" in doc["extensionsRequired"]
    assert doc["textures"][0] == {"extensions": {"EXT_texture_webp": {"source": 0}}}
    sizes = [(im["width"], im["height"]) for im in info["preview_images"]]
    assert sizes[0] == (1024, 1024)  # цветовая карта: 2048 → 1024
    assert max(sizes[1]) <= 512
    assert dst.stat().st_size < src.stat().st_size
    if shutil.which("node") and (AP.ROOT / "node_modules" / "meshoptimizer").is_dir():
        assert info["geometry_compressed"] is True
        assert "EXT_meshopt_compression" in doc["extensionsRequired"]
        assert doc["buffers"][1]["extensions"]["EXT_meshopt_compression"]["fallback"] is True
        assert any("EXT_meshopt_compression" in bv.get("extensions", {}) for bv in doc["bufferViews"])


def test_preview_small_texture_map_capped_at_512(tmp_path):
    src = make_glb(tmp_path / "prop.glb", tex=1024)
    doc, bin_ = AP.read_model(src)
    doc["materials"][0] = {"normalTexture": {"index": 0}}
    src.write_bytes(AP.write_glb(doc, bin_))
    info = AP.build_preview(src, tmp_path / "p.glb", geometry=False)
    assert max(info["preview_images"][0]["width"], info["preview_images"][0]["height"]) == 512


def test_gltf_with_embedded_data_reads_like_glb(tmp_path):
    glb = make_glb(tmp_path / "a.glb", tex=64)
    doc, bin_ = AP.read_model(glb)
    import base64

    doc["buffers"] = [{"byteLength": len(bin_), "uri": "data:application/octet-stream;base64," + base64.b64encode(bin_).decode()}]
    gltf = tmp_path / "a.gltf"
    gltf.write_text(json.dumps(doc), "utf-8")
    doc2, bin2 = AP.read_model(gltf)
    assert AP.inspect(doc2, bin2)["triangles"] == 200
    doc["buffers"][0]["uri"] = "external.bin"
    gltf.write_text(json.dumps(doc), "utf-8")
    with pytest.raises(AP.ModelError, match="внешние файлы"):
        AP.read_model(gltf)


def test_not_a_model_is_rejected(tmp_path):
    bad = tmp_path / "x.glb"
    bad.write_bytes(b"hello world, not a model")
    with pytest.raises(AP.ModelError):
        AP.read_model(bad)


# --------------------------------------------------------------------------- API


def test_api_upload_list_duplicate_media_patch_delete(env):
    client, store, pid, tmp = env
    vrm = make_glb(tmp / "hero.vrm", vrm=True, tex=1500)
    res = _upload(client, pid, vrm, name="Герой")
    assert res.status_code == 200, res.text
    hero = res.json()
    assert hero["kind"] == "character" and hero["is_vrm"] and hero["status"] == "ready" and not hero["duplicate"]
    assert hero["name"] == "Герой" and hero["size_bytes"] == vrm.stat().st_size
    assert 0 < hero["preview_bytes"] < hero["size_bytes"]
    assert hero["has_thumb"] and hero["urls"]["thumb"]
    prop = _upload(client, pid, make_glb(tmp / "guitar.glb", tex=256)).json()
    assert prop["kind"] == "prop" and prop["name"] == "guitar"

    # Тот же файл ещё раз — та же модель, без копии.
    again = _upload(client, pid, vrm, name="Другое имя").json()
    assert again["id"] == hero["id"] and again["duplicate"] is True

    listing = client.get(f"/api/projects/{pid}/assets3d").json()
    assert [g["kind"] for g in listing["groups"]] == ["character", "prop", "environment"]
    assert [g["label"] for g in listing["groups"]] == ["Персонажи", "Реквизит", "Окружение"]
    assert [a["id"] for a in listing["groups"][0]["assets"]] == [hero["id"]]
    assert listing["count"] == 2

    # Превью: gzip, если браузер просит, и размер без сжатия для полосы загрузки.
    r = client.get(hero["urls"]["preview"], headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    assert int(r.headers["x-raw-length"]) == hero["preview_bytes"]
    assert r.content[:4] == b"glTF"  # httpx распаковал
    raw = client.get(hero["urls"]["preview"], headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in raw.headers and len(raw.content) == hero["preview_bytes"]
    assert client.get(hero["urls"]["original"]).content == vrm.read_bytes()
    assert client.get(hero["urls"]["thumb"]).headers["content-type"] == "image/webp"

    # Правка и обложка из окна просмотра.
    r = client.patch(f"/api/assets3d/{prop['id']}", json={"name": "Гитара", "kind": "environment"})
    assert r.json()["kind"] == "environment" and r.json()["name"] == "Гитара"
    assert client.patch(f"/api/assets3d/{prop['id']}", json={"kind": "car"}).status_code == 400
    shot = io.BytesIO()
    Image.new("RGB", (800, 600), (30, 30, 30)).save(shot, "PNG")
    r = client.post(f"/api/assets3d/{prop['id']}/thumb", files={"file": ("t.png", shot.getvalue(), "image/png")})
    assert r.status_code == 200 and r.json()["has_thumb"]
    assert max(Image.open(io.BytesIO(client.get(r.json()["urls"]["thumb"]).content)).size) <= 384

    # Удаление — со своей папкой.
    folder = A.asset_dir(prop["id"])
    assert folder.is_dir()
    assert client.delete(f"/api/assets3d/{prop['id']}").json()["deleted"]
    assert not folder.exists()
    assert client.get(f"/api/assets3d/{prop['id']}").status_code == 404


def test_api_rejects_wrong_type_bad_content_and_too_big(env, monkeypatch):
    client, _store, pid, tmp = env
    txt = tmp / "notes.txt"
    txt.write_text("hi")
    assert _upload(client, pid, txt).status_code == 400
    fake = tmp / "fake.glb"
    fake.write_bytes(b"not really a glb file")
    r = _upload(client, pid, fake)
    assert r.status_code == 400 and "3D-модель" in r.json()["detail"]
    plain = make_glb(tmp / "plain.vrm", tex=32)  # .vrm без данных VRM
    assert "VRM" in _upload(client, pid, plain).json()["detail"]
    monkeypatch.setattr(A, "MAX_UPLOAD_BYTES", 1000)
    assert _upload(client, pid, make_glb(tmp / "big.glb", tex=64)).status_code == 413
    assert client.get(f"/api/projects/{pid}/assets3d").json()["count"] == 0
    assert client.get("/api/projects/9999/assets3d").status_code == 404


def test_project_delete_removes_model_files(env):
    client, store, pid, tmp = env
    a = _upload(client, pid, make_glb(tmp / "x.glb", tex=32)).json()
    folder = A.asset_dir(a["id"])
    assert folder.is_dir()
    P.delete_project(store, pid)
    assert not folder.exists()
    assert store.db.query_one("SELECT COUNT(*) AS n FROM project_assets3d")["n"] == 0


def test_failed_build_keeps_row_with_plain_error(env, monkeypatch):
    client, store, pid, tmp = env

    def boom(*_a, **_k):
        raise RuntimeError("kaboom")

    real = AP.build_preview
    monkeypatch.setattr(AP, "build_preview", boom)
    a = _upload(client, pid, make_glb(tmp / "x.glb", tex=32)).json()
    assert a["status"] == "failed" and a["error"] == "не получилось подготовить модель к просмотру"
    monkeypatch.setattr(AP, "build_preview", real)
    assert client.post(f"/api/assets3d/{a['id']}/rebuild").json()["status"] == "ready"


# --------------------------------------------------------------------------- анимации


def _clip(frames=3):
    return {"fps": 30, "frames": frames, "spine": "hips", "legLength": 0.8,
            "bones": {"hips": [[0, 0, 0, 1.0]] * frames, "leftUpperArm": [[0.123456, 0, 0, 0.99]] * frames},
            "root": [[0, 0.01 * i, 0] for i in range(frames)]}


def test_anim_library_merges_service_and_own_clips(env, monkeypatch):
    client, _store, _pid, tmp = env
    lib = tmp / "vrm" / "anim"
    own = tmp / "anim3d"
    lib.mkdir(parents=True)
    own.mkdir()
    from app.settings import settings

    monkeypatch.setattr(settings, "series3d_vrm_dir", tmp / "vrm")
    monkeypatch.setenv("ANIM3D_DIR", str(own))
    (lib / "walk.json").write_text(json.dumps(_clip()), "utf-8")
    (lib / "punch.json").write_text(json.dumps(_clip()), "utf-8")
    (lib / "index.json").write_text(json.dumps({"clips": [
        {"name": "walk", "ru": "Идёт", "loop": True, "license": "CC0-1.0", "source": "Quaternius"},
        {"name": "punch", "ru": "Бьёт", "license": "CC0-1.0"},
        {"name": "missing", "ru": "Нет файла"}]}), "utf-8")
    (own / "pilot_stairs.json").write_text(json.dumps(_clip(5)), "utf-8")
    (own / "index.json").write_text(json.dumps({"clips": [{"name": "pilot_stairs", "ru": "Лестница", "group": "Лестница",
                                                           "curated": True, "root": "move", "license": "свои"}]}), "utf-8")
    data = client.get("/api/anim3d").json()
    ids = [c["id"] for c in data["clips"]]
    assert set(ids) == {"lib_walk", "lib_punch", "pilot_stairs"}
    walk = next(c for c in data["clips"] if c["id"] == "lib_walk")
    assert walk["curated"] and walk["group"] == "Ходьба" and walk["license"].startswith("CC0")
    assert not next(c for c in data["clips"] if c["id"] == "lib_punch")["curated"]
    assert "Лестница" in data["groups"]
    clip = client.get("/api/anim3d/lib_walk").json()
    assert clip["bones"]["leftUpperArm"][0][0] == 0.1235 and clip["spine"] == "hips" and clip["legLength"] == 0.8
    assert client.get("/api/anim3d/pilot_stairs").json()["frames"] == 5
    assert client.get("/api/anim3d/..%2Fsecret").status_code == 404
    assert client.get("/api/anim3d/nope").status_code == 404
    assert AN.clip_path("lib_../x") is None


def test_bvh_export_roundtrip(tmp_path):
    """BVH (кадр 0 — Т-поза) → клип VRM: кости по именам CMU, без пальцев, таз — от стоящей Т-позы."""
    from tools import anim3d_export as E

    bvh = tmp_path / "t.bvh"
    bvh.write_text("""HIERARCHY
ROOT Hips
{
 OFFSET 0 0 0
 CHANNELS 6 Xposition Yposition Zposition Zrotation Yrotation Xrotation
 JOINT LeftUpLeg
 { OFFSET 1 0 0
   CHANNELS 3 Zrotation Yrotation Xrotation
   JOINT LeftLeg
   { OFFSET 0 -4 0
     CHANNELS 3 Zrotation Yrotation Xrotation
     JOINT LeftFoot
     { OFFSET 0 -4 0
       CHANNELS 3 Zrotation Yrotation Xrotation
       End Site
       { OFFSET 0 0 1 }
     }
   }
 }
 JOINT RightUpLeg
 { OFFSET -1 0 0
   CHANNELS 3 Zrotation Yrotation Xrotation
   JOINT RightLeg
   { OFFSET 0 -4 0
     CHANNELS 3 Zrotation Yrotation Xrotation
     JOINT RightFoot
     { OFFSET 0 -4 0
       CHANNELS 3 Zrotation Yrotation Xrotation
       End Site
       { OFFSET 0 0 1 }
     }
   }
 }
 JOINT Spine
 { OFFSET 0 2 0
   CHANNELS 3 Zrotation Yrotation Xrotation
   JOINT Neck
   { OFFSET 0 3 0
     CHANNELS 3 Zrotation Yrotation Xrotation
     JOINT Head
     { OFFSET 0 1 0
       CHANNELS 3 Zrotation Yrotation Xrotation
       End Site
       { OFFSET 0 1 0 }
     }
   }
   JOINT LeftArm
   { OFFSET 1 2.5 0
     CHANNELS 3 Zrotation Yrotation Xrotation
     JOINT LeftForeArm
     { OFFSET 2 0 0
     CHANNELS 3 Zrotation Yrotation Xrotation
     JOINT LeftHand
     { OFFSET 3 0 0
       CHANNELS 3 Zrotation Yrotation Xrotation
       JOINT LeftHandIndex1
       { OFFSET 1 0 0
         CHANNELS 3 Zrotation Yrotation Xrotation
         End Site
         { OFFSET 1 0 0 }
       }
     }
     }
   }
   JOINT RightArm
   { OFFSET -1 2.5 0
     CHANNELS 3 Zrotation Yrotation Xrotation
     JOINT RightForeArm
     { OFFSET -2 0 0
     CHANNELS 3 Zrotation Yrotation Xrotation
     JOINT RightHand
     { OFFSET -3 0 0
       CHANNELS 3 Zrotation Yrotation Xrotation
       End Site
       { OFFSET -1 0 0 }
     }
     }
   }
 }
}
MOTION
Frames: 3
Frame Time: 0.0333333
""" + "\n".join(" ".join(["0", "8", "0"] + ["0"] * (3 + 3 * 16)) for _ in range(3)) + "\n", "utf-8")
    clip = E.compact(E.cmu_clip(bvh, "cmu_test"))
    assert clip["source"] == "CMU t" and clip["spine"] == "hips"
    assert {"hips", "leftUpperLeg", "leftLowerLeg", "leftFoot", "head", "leftHand"} <= set(clip["bones"])
    assert not any("Index" in b for b in clip["bones"])
    # Неподвижно в Т-позе: единичные повороты одним кадром, таз на месте.
    assert clip["bones"]["leftLowerLeg"] == [[0.0, 0.0, 0.0, 1.0]]
    assert clip["root"] == [[0.0, 0.0, 0.0]]


# --------------------------------------------------------------------------- импорт


def test_import_tool_is_idempotent(env, tmp_path):
    client, store, pid, tmp = env
    from tools import assets3d_import as I

    items = [(str(make_glb(tmp / "hero.vrm", vrm=True, tex=64)), "Герой", "character"),
             (str(make_glb(tmp / "guitar.glb", tex=64, tris=10)), "Гитара", "prop"),
             (str(tmp / "missing.glb"), "Нет", "prop")]
    first = I.run(client, items)
    assert [a["name"] for a in first] == ["Герой", "Гитара"]
    second = I.run(client, items)
    assert [a["id"] for a in second] == [a["id"] for a in first]
    projects = [p for p in client.get("/api/projects").json() if "невезуч" in p["name"].lower()]
    assert len(projects) == 1 and projects[0]["id"] == pid
    assert client.get(f"/api/projects/{pid}/assets3d").json()["count"] == 2
    # Без проекта — создаётся один раз, сериалом.
    P.delete_project(store, pid)
    I.run(client, items[:1])
    I.run(client, items[:1])
    made = [p for p in client.get("/api/projects").json() if "невезуч" in p["name"].lower()]
    assert len(made) == 1 and made[0]["kind"] == "series"
