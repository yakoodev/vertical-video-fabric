"""Способ «AI-аниме 2D» (tools/anime2d*.py): чистые функции, пресет, смета и потолок трат — без сети и картинок."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from tools import anime2d as A
from tools import anime2d_polza as G
from tools import anime2d_preset as P

PRESET = Path(__file__).resolve().parent.parent / "tools" / "anime2d_presets" / "tema_yawn.json"


@pytest.fixture()
def preset() -> dict:
    return P.load(PRESET)


# ---------------------------------------------------------------- огибающие


def test_keys_and_env() -> None:
    pts = [(0.0, 0.0), (1.0, 10.0)]
    assert A.keys(-1, pts) == 0.0 and A.keys(2, pts) == 10.0
    assert A.keys(0.5, pts) == pytest.approx(5.0)
    assert A.env(0.0, 0, 1, 2, 3) == 0.0 and A.env(1.5, 0, 1, 2, 3) == 1.0 and A.env(3.5, 0, 1, 2, 3) == 0.0
    assert P.step(1.0, 1.0, 2.0) == 1.0 and P.step(2.0, 1.0, 2.0) == 0.0
    assert P.value(3.0, 7) == 7 and P.value(0.5, [[0, 0], [1, 2]]) == pytest.approx(1.0)


def test_blink_weight_closes_and_opens() -> None:
    assert A.blink_weight(0.0, [1.0]) == 0.0
    assert A.blink_weight(1.0 + 0.16 * 0.35, [1.0]) == pytest.approx(1.0)
    assert A.blink_weight(1.2, [1.0]) == 0.0


# ---------------------------------------------------------------- картинки


def test_key_green_cuts_green_keeps_cyan() -> None:
    rgb = np.zeros((8, 16, 3), np.float32)
    rgb[:, :8] = (10 / 255, 1.0, 2 / 255)  # зелёнка
    rgb[:, 8:] = (0.3, 0.8, 0.85)  # циан персонажа в свете монитора
    out = A.key_green(rgb, erode=0)
    assert out[4, 2, 3] < 0.05
    assert out[4, 13, 3] > 0.95


def test_ellipse_mask_center_and_outside() -> None:
    m = A.ellipse_mask((100, 100), (50, 50), (30, 20), 6)
    assert m[50, 50] == 1.0 and m[0, 0] == 0.0 and m[50, 95] == 0.0


def test_face_rig_only_changes_inside_box() -> None:
    base = np.zeros((40, 40, 4), np.float32)
    var = np.ones((40, 40, 4), np.float32)
    rig = A.FaceRig(base, box=(10, 10, 30, 30))
    rig.add("eyes", var, A.ellipse_mask((40, 40), (20, 20), (8, 8), 2))
    assert rig.compose({"eyes": 0.0}) is base
    out = rig.compose({"eyes": 1.0})
    assert out[20, 20, 0] == 1.0  # центр маски — из варианта
    assert out[2, 2, 0] == 0.0 and out[35, 35, 0] == 0.0  # вне коробки — база


def test_stretch_below_keeps_top_and_far_bottom() -> None:
    img = np.tile(np.arange(400, dtype=np.float32)[:, None, None], (1, 4, 4))
    assert A.stretch_below(img, 100, 1.0) is img
    out = A.stretch_below(img, 100, 1.2, span=100, fade=50)
    assert np.allclose(out[:100], img[:100])  # выше линии — без изменений
    assert np.allclose(out[300:], img[300:])  # ниже полосы и затухания — тоже
    assert out[200, 0, 0] < 200  # в полосе пиксели «тянутся» сверху


def test_camera_layer_scale_maps_center() -> None:
    cam = A.Camera(1080, 1920, 1080 / 1536, (768, 1376))
    m = cam.matrix(768, 1376, 1.0, 0.45, layer_scale=0.5)
    x, y = m @ np.float32([384, 688, 1])  # центр мира в половинном слое
    assert (x, y) == pytest.approx((540, 960))


# ---------------------------------------------------------------- пресет


def test_tema_preset_is_valid_and_documents_rejected_yawn(preset: dict) -> None:
    ims = P.images(preset)
    assert len(ims) == 9
    rej = ims["tema_yawn"]["rejected"][0]
    assert "крик" in rej["why"] and "референс" in rej["why"]
    assert ims["tema_yawn"]["refs"] == ["tema", "sheet"]
    assert P.spent_before(preset) == pytest.approx(39.0)  # столько и ушло на тест


def test_validate_rejects_bad_refs_and_gaps(preset: dict) -> None:
    bad = json.loads(json.dumps(preset))
    bad["generation"]["images"][1]["refs"] = ["nope"]
    with pytest.raises(P.PresetError, match="референс"):
        P.validate(bad)
    gap = json.loads(json.dumps(preset))
    gap["shots"][1]["t0"] = 3.2
    with pytest.raises(P.PresetError, match="подряд"):
        P.validate(gap)


def test_plan_counts_only_missing(preset: dict, tmp_path: Path) -> None:
    full = P.plan(preset, tmp_path)
    assert sum(i["rub"] for i in full) == pytest.approx(34.5)  # 5×2K по 4.5 + 4×1K по 3
    for im in preset["generation"]["images"]:
        (tmp_path / im["file"]).write_bytes(b"x")
    assert P.plan(preset, tmp_path) == []
    assert len(P.plan(preset, tmp_path, only=["sheet"], force=True)) == 1


def test_generate_dry_run_and_cap_never_call_docker(preset: dict, tmp_path: Path, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(G, "_docker", lambda *a, **k: calls.append(a))
    assert G.generate(preset, tmp_path, max_rub=0, dry_run=True) == pytest.approx(34.5)
    with pytest.raises(G.SpendError, match="потолк"):
        G.generate(preset, tmp_path, max_rub=10, dry_run=False)
    assert calls == []


def test_acting_yawn_and_smile_are_drawn_switches(preset: dict) -> None:
    act = P.Acting(preset)
    assert act.state(4.4)["w"]["yawn"] == 1.0
    assert act.state(3.4)["w"]["yawn"] == 0.0
    assert act.state(3.55)["w"]["blink"] == 1.0  # глаза закрываются раньше рта
    assert act.state(9.0)["w"]["smile"] == 1.0
    for t in np.arange(0, 9.6, 1 / 30):  # ни одного полупрозрачного «двойного» лица
        assert set(act.state(float(t))["w"].values()) <= {0.0, 1.0}


def test_shots_and_camera(preset: dict) -> None:
    assert P.shot_at(preset, 7.0)["kind"] == "screen"
    assert P.shot_at(preset, 9.59)["kind"] == "character"
    cx, cy, z = P.camera(preset, preset["shots"][0], 0.0)
    assert z == pytest.approx(1.0) and abs(cx - 768) < 7
