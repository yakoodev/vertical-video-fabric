"""🧩 Компиляция: моменты подряд, номера обратным отсчётом, субтитры со сдвигом."""

from __future__ import annotations

from app.compilation import build_compilation
from test_api_pipeline import _require_ffmpeg
from test_clip_spec import _setup


def test_compilation_orders_countdown_and_shifts_subtitles(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, source, plan, a, b = _setup(tmp_path, monkeypatch)
    first = store.create_clip_plan(source["id"], plan["analysis_id"], "Первый", segment_ids=[a["id"]])
    second = store.create_clip_plan(source["id"], plan["analysis_id"], "Второй", segment_ids=[b["id"]])
    store.set_clip_plan_subtitles(first["id"], {"lines": [{"start": 0.5, "end": 1.0, "text": "раз"}],
                                                "words": [{"word": "раз", "start": 0.5, "end": 1.0}], "duration": 5})
    store.set_clip_plan_subtitles(second["id"], {"lines": [{"start": 0.2, "end": 0.6, "text": "два"}],
                                                 "words": [{"word": "два", "start": 0.2, "end": 0.6}], "duration": 5})
    res = build_compilation(store, source["id"], [first["id"], second["id"]], countdown=True)
    comp = store.get_clip_plan(res["clip_plan_id"])
    # обратный отсчёт: второй клип первым, его карточка — #2
    assert [s["id"] for s in comp["segments"]] == [b["id"], a["id"]]
    layers = comp["render_settings"]["layers"]
    assert next(l for l in layers if l["id"] == "comp_n0")["props"]["text"] == "#2"
    dur_b = b["end_sec"] - b["start_sec"]
    subs = store.get_clip_plan_subtitles(comp["id"])
    assert subs["words"][0]["word"] == "два" and subs["words"][0]["start"] == 0.2
    assert abs(subs["words"][1]["start"] - (dur_b + 0.5)) < 1e-6
    assert any(l["type"] == "captions" for l in layers)


def test_compilation_needs_two_clips(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, source, plan, a, b = _setup(tmp_path, monkeypatch)
    import pytest

    with pytest.raises(ValueError):
        build_compilation(store, source["id"], [plan["id"]])
