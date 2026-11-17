"""view_frame на куске со скоростью (set_segment_speed): кадр — та же секунда записи, что в рендере.

Пилот 17.11: слоу-мо 0.5 в кадре агента шло как 1× — кадр брался с ``start + t``, а рендер берёт
``start + t·speed`` (app/piece_speed.to_source). Здесь — то же время, что у рендера.
"""

from __future__ import annotations

import pytest

from test_motion import _render_fixture


def _seek(calls) -> float:
    """Секунда исходника кадра основы (первый ffmpeg с -ss)."""
    args = next(a for kind, a in calls if kind == "ffmpeg" and "-ss" in a)
    return float(args[args.index("-ss") + 1])


def _frame(tmp_path, monkeypatch, t, **speed):
    from app.motion_render import render_frame_png

    store, _svc, plan, _banner, calls, _comps, kwargs = _render_fixture(tmp_path, monkeypatch)

    def fake_compositor(args, timeout):
        from pathlib import Path

        calls.append(("compositor", list(args)))
        Path(args[args.index("--png") + 1]).write_bytes(b"png")

    monkeypatch.setattr("app.render._run_compositor", fake_compositor)
    seg = store.get_clip_plan(plan["id"])["segments"][0]
    rs = {"preset_id": kwargs["ffmpeg_preset_id"]}
    if speed:
        rs["speed"] = [{"piece": seg["id"], **speed}]
    store.set_clip_plan_render_settings(plan["id"], rs)
    render_frame_png(store, plan["id"], t)
    return calls


@pytest.mark.parametrize("speed, t, src", [
    (None, 3.0, 3.0),  # без скорости — как было
    (0.5, 4.0, 2.0),  # слоу-мо вдвое: 4 с ролика = 2 с записи
    (2.0, 1.5, 3.0),  # ускорение
])
def test_frame_time_follows_piece_speed(tmp_path, monkeypatch, speed, t, src):
    calls = _frame(tmp_path, monkeypatch, t, **({"speed": speed} if speed else {}))
    assert _seek(calls) == pytest.approx(src, abs=1e-3)


def test_reversed_piece_frame_counts_from_the_end(tmp_path, monkeypatch):
    calls = _frame(tmp_path, monkeypatch, 1.0, reverse=True)
    assert _seek(calls) == pytest.approx(4.0, abs=1e-3)  # кусок 0–5 задом: 1 с ролика = 4 с записи


def test_offset_helper_matches_render_mapping():
    from app.motion_render import frame_source_offset
    from app.piece_speed import seg_length, to_source

    seg = {"start_sec": 10.0, "end_sec": 14.0, "speed": 0.5}
    for u in (0.0, 1.0, 3.3, 7.9):
        assert frame_source_offset(seg, u) == pytest.approx(min(to_source(seg, u) - 10.0, 4.0 - 0.02))
    assert seg_length(seg) == 8.0
    assert frame_source_offset({**seg, "reverse": True}, 0.0) == pytest.approx(4.0 - 0.02)
