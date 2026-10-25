"""Наезд камеры «на месте» (``anchor: "in_place"``): точка наезда стоит, кадр масштабируется
вокруг неё. Движок — frontend/src/motion/camera.ts (node/motion.test.mjs), здесь — схема и
зеркала Python (app/promo_lint.camera_view, app/aspect._camera_view)."""

from __future__ import annotations

import pytest

from app import motion
from app.aspect import _camera_view
from app.promo_lint import camera_view


def _screen(view: tuple[float, float, float], p: float, axis: int) -> float:
    """Где окажется точка ``p`` (доля кадра) после камеры (zoom, cx, cy): 0.5 + (p − c)·zoom."""
    zoom = view[0]
    return 0.5 + (p - view[axis]) * zoom


def test_normalize_keeps_anchor_and_reads_aliases():
    keys = motion.normalize_camera([
        {"t": 0, "zoom": 1.5, "x": 0.7, "anchor": "in_place"},
        {"t": 1, "zoom": 1.2, "anchor": "pivot"},
        {"t": 2, "zoom": 1.2, "anchor": "center"},
        {"t": 3, "zoom": 1},
    ])
    assert [k.get("anchor") for k in keys] == ["in_place", "in_place", None, None]
    problems = motion.validate_camera([{"t": 0, "zoom": 1.2, "anchor": "corner"}])
    assert problems and "anchor" in problems[0]
    assert "anchor" not in motion.normalize_camera([{"t": 0, "zoom": 1.2, "anchor": "corner"}])[0]


@pytest.mark.parametrize("t", [0.0, 0.3, 0.5, 1.0, 2.0, 3.4, 3.9, 4.0, 5.0])
def test_in_place_point_stays_through_zoom_in_and_out(t):
    keys = motion.normalize_camera([
        {"t": 0, "zoom": 1, "ease": "linear"},
        {"t": 1, "zoom": 1.6, "x": 0.75, "y": 0.45, "anchor": "in_place"},
        {"t": 3, "zoom": 1.6, "x": 0.75, "y": 0.45, "anchor": "in_place"},
        {"t": 4, "zoom": 1},
    ])
    view = camera_view(keys, t)
    assert _screen(view, 0.75, 1) == pytest.approx(0.75, abs=1e-9)
    assert _screen(view, 0.45, 2) == pytest.approx(0.45, abs=1e-9)
    half = 0.5 / view[0]
    assert half - 1e-9 <= view[1] <= 1 - half + 1e-9 and half - 1e-9 <= view[2] <= 1 - half + 1e-9


def test_default_anchor_unchanged_and_mirrors_agree():
    centered = [{"t": 0, "zoom": 1.6, "x": 0.65, "y": 0.45}]
    view = camera_view(centered, 0)
    assert view == pytest.approx((1.6, 0.65, 0.45))  # прежнее: точка — в центр кадра
    assert _screen(view, 0.65, 1) == pytest.approx(0.5)
    placed = {"t": 0, "zoom": 1.6, "x": 0.65, "y": 0.45, "anchor": "in_place"}
    assert camera_view([placed], 0) == pytest.approx(_camera_view(placed))
    assert _camera_view(placed)[1] == pytest.approx(motion.in_place_center(0.65, 1.6))
    # Край: точка у правого края — правый край кадра стоит.
    edge = _camera_view({"zoom": 2, "x": 1, "y": 0, "anchor": "in_place"})
    assert edge == pytest.approx((2, 0.75, 0.25))
    # Обратный пересчёт центра в точку.
    assert motion.camera_pivot(motion.in_place_center(0.8, 1.5), 1.5, 0.5) == pytest.approx(0.8)
    assert motion.camera_pivot(0.6, 1.0, 0.3) == 0.3  # zoom 1 — точка соседа


def test_reframe_keeps_in_place_anchor():
    from app.aspect import reframe_between

    layers = motion.normalize_layers([
        {"id": "title", "type": "text", "start": 0, "end": 4, "x": 0.28, "y": 0.4, "props": {"text": "Титр", "size": 64}},
        {"id": "media", "type": "image", "start": 0, "end": 4, "x": 0.72, "y": 0.5, "props": {"asset_id": 9, "w": 400}},
    ])
    camera = motion.normalize_camera([{"t": 0, "zoom": 1.3, "x": 0.72, "y": 0.5, "anchor": "in_place"}])
    _layers, moved = reframe_between(layers, camera, "16:9", "1:1", {"9": (1600, 1000)})
    assert moved[0]["anchor"] == "in_place" and 0 <= moved[0]["x"] <= 1
