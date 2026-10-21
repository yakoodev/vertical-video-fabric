"""Смена перехода сдвигает титры, камеру и вставки вслед за наплывом, как субтитры."""

from test_api_pipeline import _require_ffmpeg
from test_clip_spec import _setup

TITLE = {"id": "t1", "type": "text", "start": 7.0, "end": 8.0, "x": 0.5, "y": 0.3, "props": {"text": "ДА"}}


def test_crossfade_moves_layers_on_second_piece(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)  # куски по 5 с
    render = dict(store.get_clip_plan(plan["id"])["render_settings"] or {})
    store.set_clip_plan_render_settings(plan["id"], {**render, "layers": [TITLE], "camera": [{"t": 7.0, "zoom": 1.2}]})
    saved = store.get_clip_plan(plan["id"])["render_settings"]
    store.set_clip_plan_render_settings(plan["id"], {**saved, "transition": {"type": "fade", "duration": 0.5}})
    fresh = store.get_clip_plan(plan["id"])["render_settings"]
    layer = fresh["layers"][0]
    overlap = 7.0 - layer["start"]
    assert 0.2 < overlap <= 0.5  # второй кусок начался раньше на наплыв — титр с ним
    assert abs(fresh["camera"][0]["t"] - layer["start"]) < 1e-6


def test_layers_edited_together_with_transition_are_left_alone(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    render = dict(store.get_clip_plan(plan["id"])["render_settings"] or {})
    store.set_clip_plan_render_settings(plan["id"], {**render, "layers": [TITLE]})
    saved = store.get_clip_plan(plan["id"])["render_settings"]
    moved = {**TITLE, "start": 6.0, "end": 7.0}
    store.set_clip_plan_render_settings(
        plan["id"], {**saved, "layers": [moved], "transition": {"type": "fade", "duration": 0.5}}
    )
    assert store.get_clip_plan(plan["id"])["render_settings"]["layers"][0]["start"] == 6.0
