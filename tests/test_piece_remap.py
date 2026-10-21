"""Монтаж едет за кусками: подрезали кусок — титры и камера остаются на своих фразах."""

from app.piece_remap import remap_render

OLD = [{"id": 1, "start_sec": 100, "end_sec": 110}, {"id": 2, "start_sec": 200, "end_sec": 210}]


def _render():
    return {
        "layers": [
            {"id": "hook", "type": "text", "start": 0.0, "end": 2.0},
            {"id": "joke", "type": "text", "start": 5.0, "end": 7.0},
            {"id": "late", "type": "text", "start": 14.0, "end": 16.0},
            {"id": "caps", "type": "captions", "start": 0.0, "end": 20.0},
        ],
        "camera": [{"t": 5.0, "zoom": 1.2}, {"t": 15.0, "zoom": 1.0}],
        "inserts": [{"at": 15.0, "duration": 2.0, "asset_id": 1}],
        "cuts": [{"start": 12.0, "end": 13.0}],
    }


def _by_id(render):
    return {l["id"]: (l["start"], l["end"]) for l in render["layers"]}


def test_trim_first_piece_start_moves_everything_after():
    new = [{"id": 1, "start_sec": 103, "end_sec": 110}, {"id": 2, "start_sec": 200, "end_sec": 210}]
    out, note = remap_render(_render(), OLD, new)
    layers = _by_id(out)
    assert layers["hook"] == (0.0, 2.0)  # хук остаётся в начале
    assert layers["joke"] == (2.0, 4.0)  # на своей фразе: −3 с
    assert layers["late"] == (11.0, 13.0)
    assert layers["caps"] == (0.0, 17.0)
    assert [k["t"] for k in out["camera"]] == [2.0, 12.0]
    assert out["inserts"][0]["at"] == 12.0
    assert out["cuts"] == [{"start": 9.0, "end": 10.0}]
    assert note


def test_extend_last_piece_keeps_times():
    new = [OLD[0], {"id": 2, "start_sec": 200, "end_sec": 211}]
    out, _ = remap_render(_render(), OLD, new)
    assert _by_id(out)["late"] == (14.0, 16.0)
    assert _by_id(out)["caps"] == (0.0, 21.0)


def test_unchanged_and_reordered():
    render = _render()
    assert remap_render(render, OLD, OLD) == (render, None)
    out, note = remap_render(render, OLD, list(reversed(OLD)))
    assert out is render and "переставлены" in note


def test_dragging_piece_edge_moves_layers(tmp_path, monkeypatch):
    """Край куска тянут в редакторе (PATCH timecodes) — титр едет за своей фразой."""
    from test_api_pipeline import _require_ffmpeg
    from test_clip_spec import _setup

    _require_ffmpeg()
    client, store, _source, plan, a, _b = _setup(tmp_path, monkeypatch)  # куски [0,5] и [1,6]
    render = dict(store.get_clip_plan(plan["id"])["render_settings"] or {})
    layer = {"id": "t1", "type": "text", "start": 7.0, "end": 8.0, "x": 0.5, "y": 0.3, "props": {"text": "ДА"}}
    store.set_clip_plan_render_settings(plan["id"], {**render, "layers": [layer]})
    client.patch(f"/api/segments/{a['id']}/timecodes", json={"start_sec": 1.0, "end_sec": 6.0}).raise_for_status()
    moved = store.get_clip_plan(plan["id"])["render_settings"]["layers"][0]
    assert (moved["start"], moved["end"]) == (7.0, 8.0)  # длина первого куска та же (5 с) — на месте
    client.patch(f"/api/segments/{a['id']}/timecodes", json={"start_sec": 1.0, "end_sec": 7.0}).raise_for_status()
    moved = store.get_clip_plan(plan["id"])["render_settings"]["layers"][0]
    assert (moved["start"], moved["end"]) == (8.0, 9.0)  # первый кусок длиннее на 1 с


def test_splitting_a_piece_keeps_layers_in_place():
    """Агент разрезал кусок надвое (время клипа то же) — сервер убирал слои второй половины."""
    old = [{"id": 1, "start_sec": 782.0, "end_sec": 803.6}]
    new = [{"id": 1, "start_sec": 782.0, "end_sec": 797.8}, {"id": None, "start_sec": 797.8, "end_sec": 803.6}]
    render = {"layers": [{"id": "late", "type": "text", "start": 17.0, "end": 19.0}],
              "camera": [{"t": 18.0, "zoom": 1.3}], "inserts": [], "cuts": []}
    out, _note = remap_render(render, old, new)
    assert [(l["id"], l["start"], l["end"]) for l in out["layers"]] == [("late", 17.0, 19.0)]
    assert out["camera"][0]["t"] == 18.0
