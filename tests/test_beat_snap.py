"""Привязка к долям (app/beat_snap.py, snap_to_beats): сетка, допуск, слои/ключи/камера, склейки
кусков, шкала final при вырезах, сетка музыки клипа, dry_run и REST."""

from __future__ import annotations

import pytest

from app.agent_tools import ToolContext, run_tool
from app.beat_snap import Scale, grid_times, nearest, snap_camera, snap_layers
from app.clip_history import snapshot
from test_cuts import _mock_render


def _ctx(store, plan_id):
    return ToolContext(store=store, clip_plan_id=plan_id, on_first_change=lambda: snapshot(store, plan_id, "тест"))


def _text(lid, start, end=None, **extra):
    return {"id": lid, "type": "text", "start": start, "end": end if end is not None else start + 1.0, "x": 0.5,
            "y": 0.3, "props": {"text": lid, "size": 80}, **extra}


def test_grid_and_nearest():
    grid = {"bpm": 120, "period": 0.5, "first_beat": 0.25, "first_downbeat": 0.75, "downbeat_every": 4}
    assert grid_times(grid, 2.0)[:5] == [0.25, 0.75, 1.25, 1.75, 2.25]
    assert grid_times(grid, 5.0, "downbeat")[:3] == [0.75, 2.75, 4.75]
    assert grid_times(grid, 1.0, "half")[:4] == [0.0, 0.25, 0.5, 0.75]
    assert grid_times({"bpm": 100, "first_beat": 3.1}, 2.0)[0] == pytest.approx(0.1)  # и до первой доли
    beats = [0.25, 0.75, 1.25]
    assert nearest(0.8, beats, 0.1) == 0.75 and nearest(1.0, beats, 0.15) is None
    with pytest.raises(ValueError):
        grid_times({}, 2.0)


def test_snap_layers_keys_camera_pure():
    beats = [k * 0.5 for k in range(20)]
    layers = [_text("a", 1.07), _text("b", 3.25), {**_text("cap", 1.05), "type": "captions"},
              _text("lock", 2.04, locked=True), _text("zero", 0.0),
              _text("k", 2.0, 4.0, keys={"scale": [{"t": 0.0, "v": 0.5}, {"t": 0.46, "v": 1.0}, {"t": 0.52, "v": 1.1}]})]
    out, changes = snap_layers(layers, beats, 0.15, Scale(), keys=True)
    by = {l["id"]: l for l in out}
    assert (by["a"]["start"], by["a"]["end"]) == (1.0, 2.0)
    assert by["b"]["start"] == 3.25 and by["cap"]["start"] == 1.05 and by["lock"]["start"] == 2.04
    assert by["zero"]["start"] == 0.0
    # Ключ 0.46 → 0.5, но 0.52 → тоже 0.5 нельзя (сосед) — остаётся.
    assert [p["t"] for p in by["k"]["keys"]["scale"]] == [0.0, 0.5, 0.52]
    assert {c["target"] for c in changes} == {"layer", "key"}
    cam, cam_changes = snap_camera([{"t": 4.04, "zoom": 1}, {"t": 4.3, "zoom": 1.3}], beats, 0.15, Scale())
    assert [k["t"] for k in cam] == [4.0, 4.3] and len(cam_changes) == 1


def test_tool_snaps_layers_camera_and_joins(tmp_path, monkeypatch):
    store, _service, plan, _preset, _calls = _mock_render(tmp_path, monkeypatch, [(0, 6), (10, 16)])
    ctx = _ctx(store, plan["id"])
    store.set_clip_plan_render_settings(plan["id"], {
        "layers": [_text("hook", 1.07), _text("late", 7.0), _text("far", 3.35)],
        "camera": [{"t": 3.04, "zoom": 1.0}, {"t": 3.3, "zoom": 1.4}],
    })
    # dry_run: ничего не сохраняет.
    out, ok = run_tool(ctx, "snap_to_beats", {"targets": ["layers"], "bpm": 120, "first_beat": 0.1, "dry_run": True})
    assert ok and out["moved"] == 2 and out["changes"][0]["to"] == pytest.approx(1.1)  # hook и late
    assert store.get_clip_plan(plan["id"])["render_settings"]["layers"][0]["start"] == 1.07

    out, ok = run_tool(ctx, "snap_to_beats", {"targets": ["all"], "bpm": 120, "first_beat": 0.1})
    assert ok, out
    fresh = store.get_clip_plan(plan["id"])
    # Стык 6.0 → доля 6.1: первый кусок удлинён, слой во втором куске уехал вместе с ним.
    assert fresh["segments"][0]["end_sec"] == pytest.approx(6.1)
    layers = {l["id"]: l for l in fresh["render_settings"]["layers"]}
    assert layers["hook"]["start"] == pytest.approx(1.1) and layers["hook"]["end"] == pytest.approx(2.1)
    assert layers["late"]["start"] == pytest.approx(7.1)  # 7.0 + 0.1 за стыком, уже на доле
    assert layers["far"]["start"] == 3.35  # 0.25 от доли — дальше допуска
    assert [k["t"] for k in fresh["render_settings"]["camera"]] == [pytest.approx(3.1), 3.3]
    assert {c["target"] for c in out["changes"]} == {"join", "layer", "camera"}
    assert out["grid"]["source"] == "manual"

    # Ошибки — понятным текстом.
    assert not run_tool(ctx, "snap_to_beats", {"targets": ["всё"], "bpm": 120})[1]
    assert not run_tool(ctx, "snap_to_beats", {"bpm": 120, "tolerance": 2})[1]
    assert not run_tool(ctx, "snap_to_beats", {"bpm": 120, "grid": "такт"})[1]


def test_final_scale_with_cuts_and_music_grid(tmp_path, monkeypatch):
    store, _service, plan, _preset, _calls = _mock_render(tmp_path, monkeypatch, [(0, 10)])
    # Вырез 2–3 с (clip): слой на 4.07 clip = 3.07 готового ролика → доля 3.0 → 4.0 clip.
    store.set_clip_plan_render_settings(plan["id"], {"cuts": [{"start": 2.0, "end": 3.0}],
                                                     "layers": [_text("x", 4.07)]})
    grid = {"bpm": 120, "period": 0.5, "first_beat": 0.0, "first_downbeat": 1.0, "downbeat_every": 4, "time": "final"}
    monkeypatch.setattr("app.audio_map._music_beats", lambda store, render, clock: grid)
    out, ok = run_tool(_ctx(store, plan["id"]), "snap_to_beats", {})
    assert ok, out
    assert out["grid"]["source"] == "music" and out["time"] == "final"
    assert store.get_clip_plan(plan["id"])["render_settings"]["layers"][0]["start"] == pytest.approx(4.0)
    # Сильные доли (раз в 2 с с 1.0): 3.0 final — сильная, остаётся на месте.
    out, ok = run_tool(_ctx(store, plan["id"]), "snap_to_beats", {"grid": "downbeat"})
    assert ok and out["moved"] == 0


def test_rest_snap_beats(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import app.main as app_main
    from app.settings import settings

    store, _service, plan, _preset, _calls = _mock_render(tmp_path, monkeypatch, [(0, 10)])
    store.set_clip_plan_render_settings(plan["id"], {"layers": [_text("x", 2.06)]})
    monkeypatch.setattr(settings, "auth_enabled", False)
    monkeypatch.setattr(app_main, "store", store)
    client = TestClient(app_main.app)
    resp = client.post(f"/api/clip-plans/{plan['id']}/snap-beats", json={"targets": ["layers"], "bpm": 120})
    assert resp.status_code == 200, resp.text
    assert resp.json()["moved"] == 1
    assert store.get_clip_plan(plan["id"])["render_settings"]["layers"][0]["start"] == pytest.approx(2.0)
    assert client.post(f"/api/clip-plans/{plan['id']}/snap-beats", json={"tolerance": 3}).status_code == 422
