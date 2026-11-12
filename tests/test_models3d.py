"""Библиотека 3D-моделей вещей: справка агенту и сверка ссылок сцены (app/models3d.py, docs/3d/models.md)."""

from __future__ import annotations

import json

from app import models3d, scene3d


def _lib(tmp_path):
    man = {
        "version": 1,
        "categories": {"keyboard": ["keyboard/mech_tkl_dark", "keyboard/rgb_black"]},
        "models": {
            "keyboard/mech_tkl_dark": {"id": "keyboard/mech_tkl_dark", "category": "keyboard", "slug": "mech_tkl_dark",
                                       "file": "keyboard/mech_tkl_dark.glb", "title": "Механическая", "size": [0.36, 0.025, 0.14],
                                       "tags": ["mechanical", "dark"], "license": "CC0-1.0"},
            "keyboard/rgb_black": {"id": "keyboard/rgb_black", "category": "keyboard", "slug": "rgb_black",
                                   "file": "keyboard/rgb_black.glb", "title": "Игровая", "size": [0.45, 0.017, 0.17],
                                   "tags": ["gaming"], "license": "CC-BY-3.0", "anchors": {}},
        },
    }
    (tmp_path / "manifest.json").write_text(json.dumps(man), "utf-8")
    return tmp_path


def test_list_and_resolve(tmp_path, monkeypatch):
    monkeypatch.setenv("SCENE3D_MODELS_DIR", str(_lib(tmp_path)))
    assert models3d.is_library_ref("keyboard") and models3d.is_library_ref("keyboard/rgb_black")
    assert not models3d.is_library_ref("kenney-furniture/desk.glb")
    # CC BY без атрибуции в описаниях не берём (решение владельца 12.11): ни по категории, ни по точному id.
    assert models3d.resolve("keyboard") == ["keyboard/mech_tkl_dark"]
    assert models3d.resolve("keyboard/rgb_black") == []
    assert [m["id"] for m in models3d.list_models()["categories"]["keyboard"]] == ["keyboard/mech_tkl_dark"]
    assert models3d.resolve("sofa") == []
    out = models3d.list_models(q="механ")
    assert out["available"] and [m["id"] for m in out["categories"]["keyboard"]] == ["keyboard/mech_tkl_dark"]


def test_no_library(tmp_path, monkeypatch):
    monkeypatch.setenv("SCENE3D_MODELS_DIR", str(tmp_path / "none"))
    assert models3d.manifest() is None
    assert models3d.list_models()["available"] is False


def test_scene_accepts_library_refs(tmp_path, monkeypatch):
    monkeypatch.setenv("SCENE3D_MODELS_DIR", str(_lib(tmp_path)))
    spec = {"duration": 1, "camera": {"keys": [{"t": 0, "position": [0, 1, 2], "target": [0, 0.8, 0]}]}, "objects": [
        {"id": "kb", "model": "keyboard", "primitive": "keyboard", "position": [0, 0.75, 0]},
        {"id": "sofa", "model": "sofa", "fallback": [{"id": "sofa.box", "primitive": "box"}]},
    ]}
    res = scene3d.validate_spec(spec, packs=tmp_path, check_assets=True)
    assert "keyboard" in res["models"]
    assert any("sofa" in w and "процедурная замена" in w for w in res["warnings"])
