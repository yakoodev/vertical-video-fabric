"""✂ Джамп-каты: функция времени, нормализация, подсказки, рендер с вырезами."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.clip_settings import normalize_render_settings
from app.cuts import (
    MAX_CUTS,
    apply_cuts_to_timeline,
    normalize_cuts,
    plan_cuts,
    remap_camera,
    remap_inserts,
    remap_layers,
    remap_motion,
    remap_subtitles,
    slice_focus,
    suggest_cuts,
    validate_cuts,
)
from app.ingest import probe_media
from app.render import ClipRenderService
from app.settings import settings
from app.transitions import JUMP_DECLICK_SEC, build_join_args, normalize_transition, plan_joins
from test_api_pipeline import _client, _require_ffmpeg
from test_render import _store


# ---------------------------------------------------------------- функция времени


def test_mapper_shifts_after_cuts_and_drops_inside():
    m = apply_cuts_to_timeline([{"start": 1, "end": 2}, {"start": 4, "end": 4.5}])
    assert m(0.5) == 0.5
    assert m(1.0) == 1.0 and m(2.0) == 1.0  # обе границы выреза — одна точка склейки
    assert m(1.5) is None and m(4.2) is None
    assert m(3.0) == pytest.approx(2.0)
    assert m(5.0) == pytest.approx(3.5)
    assert m.clamp(1.5) == 1.0 and m.clamp(4.2) == pytest.approx(3.0)
    assert m.range(0.5, 1.5) == (0.5, 1.0)
    assert m.range(1.2, 1.8) is None
    assert m.range(0.5, 5.0) == pytest.approx((0.5, 3.5))
    assert m.duration(6.0) == pytest.approx(4.5)
    assert m.removed == pytest.approx(1.5)
    # Пустые вырезы — тождество.
    empty = apply_cuts_to_timeline([])
    assert not empty and empty(3.3) == 3.3


def test_normalize_sorts_merges_drops_and_caps():
    raw = [
        {"start": 5, "end": 6},
        {"start": 1, "end": 2},
        {"start": 1.5, "end": 2.5},  # пересекается — сливается
        {"start": 3, "end": 3.05},  # короче 0.08 — выброшен
        {"start": "x", "end": 1},
        "junk",
        {"start": 7, "end": 6},
    ]
    assert normalize_cuts(raw) == [{"start": 1.0, "end": 2.5}, {"start": 5.0, "end": 6.0}]
    assert normalize_cuts(None) == [] and normalize_cuts("x") == []
    many = [{"start": i, "end": i + 0.5} for i in range(300)]
    assert len(normalize_cuts(many)) == MAX_CUTS
    assert normalize_render_settings({})["cuts"] == []
    assert normalize_render_settings({"cuts": [{"start": 2, "end": 3}]})["cuts"] == [{"start": 2.0, "end": 3.0}]


def test_validate_explains_each_problem():
    problems = validate_cuts([{"start": 2, "end": 1}, {"start": 0, "end": 0.01}, {"start": 1, "end": 3}, {"start": 2, "end": 4}])
    text = " | ".join(problems)
    assert "cuts[0]" in text and "start < end" in text
    assert "cuts[1]" in text and "короче" in text
    assert "пересекаются" in text
    assert validate_cuts([{"start": 1, "end": 2}]) == []
    assert any("не больше" in p for p in validate_cuts([{"start": i, "end": i + 0.5} for i in range(MAX_CUTS + 1)]))
    assert validate_cuts("nope") == ["cuts: нужен список отрезков [{start, end}]"]


def test_remap_layers_camera_inserts_subtitles():
    m = apply_cuts_to_timeline([{"start": 2, "end": 3}])
    layers = [
        {"id": "a", "type": "text", "start": 0.5, "end": 1.5, "keys": {"opacity": [{"t": 0.5, "v": 1}]}},
        {"id": "b", "type": "text", "start": 2.2, "end": 2.8, "keys": {}},  # целиком в вырезе
        {"id": "c", "type": "text", "start": 1.0, "end": 4.0,
         "keys": {"scale": [{"t": 0.5, "v": 1}, {"t": 1.5, "v": 2}, {"t": 2.5, "v": 3}]},
         "in": {"type": "fade", "duration": 0.3}, "out": {"type": "none", "duration": 0.3}},
        {"id": "d", "type": "text", "start": 4.0, "end": 5.0, "keys": {"x": [{"t": 0.2, "v": 0.1}]}},
    ]
    out = {layer["id"]: layer for layer in remap_layers(layers, m)}
    assert set(out) == {"a", "c", "d"}
    assert out["a"]["start"] == 0.5 and out["a"]["keys"] == layers[0]["keys"]
    # c прошёл через вырез: 1..4 → 1..3; ключ t=1.5 (абс. 2.5, в вырезе) прижат к склейке,
    # t=2.5 (абс. 3.5) → 1.5.
    assert (out["c"]["start"], out["c"]["end"]) == (1.0, 3.0)
    assert out["c"]["keys"]["scale"] == [{"t": 0.5, "v": 1}, {"t": 1.0, "v": 2}, {"t": 1.5, "v": 3}]
    # d после выреза: сдвинут, ключи (от начала слоя) не тронуты.
    assert (out["d"]["start"], out["d"]["end"]) == (3.0, 4.0) and out["d"]["keys"] == layers[3]["keys"]

    camera = remap_camera([{"t": 1, "zoom": 1}, {"t": 2.5, "zoom": 2}, {"t": 4, "zoom": 1.5}], m)
    assert [k["t"] for k in camera] == [1.0, 2.0, 3.0]
    inserts = remap_inserts([{"asset_id": 1, "at": 2.5}, {"asset_id": 2, "at": 5}], m)
    assert [i["at"] for i in inserts] == [2.0, 4.0]

    payload = {
        "lines": [{"start": 0.0, "end": 2.5, "text": "раз два"}, {"start": 2.1, "end": 2.9, "text": "ээ"},
                  {"start": 3.0, "end": 4.0, "text": "три"}],
        "words": [{"word": "раз", "start": 0.0, "end": 1.0}, {"word": "ээ", "start": 2.2, "end": 2.6},
                  {"word": "три", "start": 3.2, "end": 3.8}],
        "duration": 5.0,
    }
    fresh = remap_subtitles(payload, m)
    assert [l["text"] for l in fresh["lines"]] == ["раз два", "три"]
    assert fresh["lines"][0]["end"] == 2.0 and fresh["lines"][1]["start"] == 2.0
    assert [w["word"] for w in fresh["words"]] == ["раз", "три"]
    assert fresh["words"][1]["start"] == pytest.approx(2.2) and fresh["duration"] == 4.0

    motion = remap_motion({"layers": layers, "camera": [], "chat": [{"t": 2.5}, {"t": 3.5}],
                           "words": [{"t0": 2.1, "t1": 2.4, "w": "ээ"}, {"t0": 3.1, "t1": 3.5, "w": "да"}]}, m)
    assert [c["t"] for c in motion["chat"]] == [2.5]  # 2.5 — в вырезе, 3.5 → 2.5
    assert [w["w"] for w in motion["words"]] == ["да"]


# ---------------------------------------------------------------- подсказки


def _w(word, start, end):
    return {"word": word, "start": start, "end": end}


def test_suggest_pauses_keeps_pad_around_words():
    words = [_w("Привет", 0.0, 0.5), _w("мир", 0.6, 1.0), _w("как", 2.0, 2.3), _w("дела", 2.35, 2.8)]
    out = suggest_cuts(words, 3.0, mode="pauses", min_pause=0.45, keep_pad=0.12)
    assert out["cuts"] == [{"start": 1.12, "end": 1.88}]
    assert out["items"][0]["reason"] == "пауза 1,0 с"
    assert out["saved_sec"] == pytest.approx(0.76)
    assert out["before_sec"] == 3.0 and out["after_sec"] == pytest.approx(2.24)
    # Короткие паузы не трогаем.
    assert suggest_cuts(words, 2.8, mode="pauses", min_pause=1.5)["cuts"] == []


def test_suggest_pauses_trims_leading_and_trailing_silence():
    out = suggest_cuts([_w("слово", 1.0, 1.5)], 3.0, mode="pauses", min_pause=0.45, keep_pad=0.1)
    assert out["cuts"] == [{"start": 0.0, "end": 0.9}, {"start": 1.6, "end": 3.0}]


def test_suggest_fillers_hesitations_phrases_and_standalone_nu():
    words = [
        _w("Я", 0.0, 0.2), _w("ээ", 0.5, 0.8), _w("пошёл", 1.0, 1.4),
        _w("короче,", 1.5, 1.9), _w("домой", 2.0, 2.4),
        _w("как", 2.5, 2.6), _w("бы", 2.6, 2.7), _w("Ну", 3.2, 3.4), _w("вот", 3.8, 4.0),
        _w("ну", 4.05, 4.15), _w("давай", 4.2, 4.6), _w("блин", 4.7, 5.0),
    ]
    out = suggest_cuts(words, 5.0, mode="fillers", keep_pad=0.12)
    reasons = [it["reason"] for it in out["items"]]
    assert "слово «ээ»" in reasons[0]
    assert any("короче" in r for r in reasons)
    assert any("как бы" in r for r in reasons)
    assert any("слово «Ну»" in r for r in reasons)  # стоит особняком между паузами
    assert not any("блин" in r for r in reasons)  # «блин» — не паразит
    # «ну» вплотную к «давай» — часть фразы, не трогаем.
    assert not any(it["start"] <= 4.1 <= it["end"] for it in out["items"])
    # «ээ»: вырез вместе с тишиной вокруг, но с запасом keep_pad у соседей.
    first = out["items"][0]
    assert first["start"] == pytest.approx(0.32) and first["end"] == pytest.approx(0.88)


def test_suggest_stutters_keep_last_repeat_and_both_merges():
    words = [_w("я", 0.0, 0.1), _w("я", 0.3, 0.4), _w("я", 0.6, 0.7), _w("пошёл", 0.75, 1.1),
             _w("да", 1.3, 1.4), _w("да", 1.5, 1.6)]
    out = suggest_cuts(words, 1.6, mode="fillers", keep_pad=0.05)
    assert len(out["items"]) == 1  # «да да» — нарочно, не заикание
    item = out["items"][0]
    assert item["reason"] == "повтор «я я»"
    assert item["start"] == pytest.approx(0.0) and item["end"] == pytest.approx(0.55)

    words = [_w("раз", 0.0, 0.4), _w("эм", 1.2, 1.4), _w("два", 2.4, 2.8)]
    both = suggest_cuts(words, 2.8, mode="both", min_pause=0.45, keep_pad=0.1)
    # Пауза до «эм», само «эм» и пауза после — один вырез, причины через «+».
    assert len(both["cuts"]) == 1 and both["cuts"][0] == {"start": 0.5, "end": 2.3}
    assert "пауза" in both["items"][0]["reason"] and "эм" in both["items"][0]["reason"]


# ---------------------------------------------------------------- план рендера


def _segs():
    return [
        {"id": 1, "source_id": 9, "start_sec": 10.0, "end_sec": 14.0,
         "focus": [{"t": 0, "x": 0.2}, {"t": 2, "x": 0.6}, {"t": 3, "x": 0.9, "cut": True}]},
        {"id": 2, "source_id": 9, "start_sec": 50.0, "end_sec": 53.0, "focus": []},
    ]


def test_plan_cuts_splits_pieces_and_marks_jump_joins():
    plan = plan_cuts(_segs(), [{"start": 1.0, "end": 1.5}, {"start": 3.9, "end": 4.5}], fps=25)
    assert plan is not None
    spans = [(p["piece"], p["start_sec"], p["end_sec"]) for p in plan.parts]
    # Кусок 1: 10–11 | 11.5–13.9; кусок 2 (с 4.0 с клипа): вырезано начало 0.5 с.
    assert spans == [(0, 10.0, 11.0), (0, 11.5, 13.9), (1, 50.5, 53.0)]
    assert plan.jump_joins == [True, False]
    assert plan.piece_durations == [pytest.approx(3.4), pytest.approx(2.5)]
    assert plan.total_after == pytest.approx(5.9)
    # Вырез через стык кусков — один отрезок в функции времени.
    assert plan.cuts == [{"start": 1.0, "end": 1.5}, {"start": 3.9, "end": 4.5}]
    assert plan.mapper(5.0) == pytest.approx(3.9)
    # Дорожка кадра части 2 (с 1.5 с куска): кадр в момент 1.5 = 0.5, точки после — со сдвигом.
    assert plan.parts[1]["focus"][0] == {"t": 0.0, "x": 0.5}
    assert {"t": 0.5, "x": 0.6} in plan.parts[1]["focus"]
    assert {"t": 1.5, "x": 0.9, "cut": True} in plan.parts[1]["focus"]
    assert "focus" not in plan.parts[2]
    # Обложка «кусок 1, 0.2 с» → часть 2 (кусок 2 начинается с 0.5 с — вырезано), 0 с.
    cover = plan.remap_cover({"mode": "frame", "piece": 1, "offset": 0.2})
    assert cover["piece"] == 2 and cover["offset"] == 0.0
    assert plan.remap_cover({"mode": "frame", "piece": 0, "offset": 2.0}) == {"mode": "frame", "piece": 1, "offset": 0.5}


def test_plan_cuts_snaps_to_frames_absorbs_slivers_and_rejects_everything():
    segs = [{"id": 1, "start_sec": 0.0, "end_sec": 5.0}]
    plan = plan_cuts(segs, [{"start": 1.013, "end": 2.0}, {"start": 2.05, "end": 3.0}], fps=30)
    # Обрывок 2.0–2.05 ушёл в вырез; часть до выреза — целое число кадров.
    assert [(p["start_sec"], p["end_sec"]) for p in plan.parts] == [(0.0, 1.0), (3.0, 5.0)]
    assert plan.cuts == [{"start": 1.0, "end": 3.0}]
    # Вырезы мимо клипа — рендер прежний.
    assert plan_cuts(segs, [{"start": 6, "end": 7}]) is None
    assert plan_cuts(segs, []) is None
    with pytest.raises(ValueError):
        plan_cuts(segs, [{"start": 0, "end": 5}])


def test_slice_focus_continues_the_track():
    focus = [{"t": 0, "x": 0.0}, {"t": 4, "x": 1.0}]
    assert slice_focus(focus, 1.0, 3.0) == [{"t": 0.0, "x": 0.25}, {"t": 2.0, "x": 0.75}]
    assert slice_focus([], 1, 2) == []


def test_jump_joins_are_hard_with_short_declick_and_no_sfx(tmp_path):
    parts = [tmp_path / f"{i}.mp4" for i in range(3)]
    durations = [2.0, 1.5, 3.0]
    join = normalize_transition({"type": "fade", "duration": 0.3, "smart": False, "sfx": "whoosh"})
    # Без вырезов (None или все False) — ровно прежние аргументы.
    base = build_join_args(parts, durations, tmp_path / "o.mp4", join, has_audio=True)
    assert build_join_args(parts, durations, tmp_path / "o.mp4", join, has_audio=True, jump_joins=[False, False]) == base
    assert plan_joins(join, durations) == [0.3, 0.3]
    assert plan_joins(join, durations, jump_joins=[True, False]) == [0.0, 0.3]
    args = build_join_args(parts, durations, tmp_path / "o.mp4", join, has_audio=True, jump_joins=[True, False])
    graph = args[args.index("-filter_complex") + 1]
    assert f"d={JUMP_DECLICK_SEC}" in graph
    assert "xfade" in graph and graph.count("xfade") == 1
    assert graph.count("adelay") == 1  # звук стыка только на смене сцены
    # Встык + резкий звук: джамп-кат всё равно с микрофейдом.
    hard = normalize_transition({"type": "cut", "audio": "hard"})
    args = build_join_args(parts, durations, tmp_path / "o.mp4", hard, has_audio=True, jump_joins=[True, False])
    graph = args[args.index("-filter_complex") + 1]
    assert graph.count("afade") == 2 and f"d={JUMP_DECLICK_SEC}" in graph
    base_hard = build_join_args(parts, durations, tmp_path / "o.mp4", hard, has_audio=True)
    assert "afade" not in base_hard[base_hard.index("-filter_complex") + 1]


# ---------------------------------------------------------------- рендер (ffmpeg подменён)


class _Meta:
    duration_sec = 2.0
    width = 180
    height = 320
    fps = 25.0
    raw = {"streams": [{"codec_type": "video"}, {"codec_type": "audio"}]}


def _mock_render(tmp_path, monkeypatch, pieces):
    store = _store(tmp_path, monkeypatch)
    source_path = settings.source_dir / "s.mp4"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"src")
    source = store.create_source("upload", source_path, duration_sec=60, width=1280, height=720, status="ready")
    analysis = store.create_ai_analysis(source["id"], "mock", status="succeeded")
    segs = [store.create_ai_segment(analysis["id"], {"start_sec": a, "end_sec": b, "title": f"P{i}"})
            for i, (a, b) in enumerate(pieces)]
    plan = store.create_clip_plan(source["id"], analysis["id"], "План", segment_ids=[s["id"] for s in segs])
    preset = store.create_ffmpeg_preset("P", output_width=180, output_height=320, fps=25)
    calls: list[list[str]] = []

    def fake_ffmpeg(args, timeout, cwd=None):
        calls.append(list(args))
        Path(args[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(args[-1]).write_bytes(b"video")

    class _Uuid:
        hex = "0" * 32

    monkeypatch.setattr("app.render._run_ffmpeg", fake_ffmpeg)
    monkeypatch.setattr("app.render.uuid4", lambda: _Uuid())
    monkeypatch.setattr("app.render.probe_media", lambda path: _Meta())
    monkeypatch.setattr("app.render.run_qc", lambda *a, **k: None)
    monkeypatch.setattr("app.render._has_audio_stream", lambda path: True)
    return store, ClipRenderService(store), plan, preset, calls


@pytest.mark.parametrize("pieces", [[(0, 5)], [(0, 5), (10, 16)]])
def test_empty_cuts_change_nothing_in_ffmpeg_args(tmp_path, monkeypatch, pieces):
    store, service, plan, preset, calls = _mock_render(tmp_path, monkeypatch, pieces)
    service.render_clip_plan(plan["id"], ffmpeg_preset_id=preset["id"])
    before = [list(c) for c in calls]
    calls.clear()
    store.set_clip_plan_render_settings(plan["id"], {"cuts": []})
    service.render_clip_plan(plan["id"], ffmpeg_preset_id=preset["id"])
    assert calls == before
    calls.clear()
    # Вырез мимо кусков — тоже ничего.
    store.set_clip_plan_render_settings(plan["id"], {"cuts": [{"start": 50, "end": 51}]})
    service.render_clip_plan(plan["id"], ffmpeg_preset_id=preset["id"])
    assert calls == before


def test_cuts_render_sub_parts_and_join_with_declick(tmp_path, monkeypatch):
    store, service, plan, preset, calls = _mock_render(tmp_path, monkeypatch, [(0, 5)])
    store.set_clip_plan_render_settings(
        plan["id"],
        {"cuts": [{"start": 1.0, "end": 2.0}, {"start": 3.0, "end": 3.5}],
         "inserts": [{"asset_id": 1, "at": 4.0}]},
    )
    clip = service.render_clip_plan(plan["id"], ffmpeg_preset_id=preset["id"])
    assert clip["status"] == "succeeded", clip.get("error")
    renders = [c for c in calls if "-ss" in c and "-filter_complex" in c and "-t" in c]
    windows = [(c[c.index("-ss") + 1], c[c.index("-t") + 1]) for c in renders]
    assert windows == [("0.000", "1.000"), ("2.000", "1.000"), ("3.500", "1.500")]
    joins = [c for c in calls if c.count("-i") == 3]
    assert len(joins) == 1
    graph = joins[0][joins[0].index("-filter_complex") + 1]
    assert f"d={JUMP_DECLICK_SEC}" in graph and "concat=n=3" in graph


def test_plan_subtitles_are_cut_before_burning(tmp_path, monkeypatch):
    store, service, plan, preset, _calls = _mock_render(tmp_path, monkeypatch, [(0, 5)])
    store.set_clip_plan_subtitles(plan["id"], {
        "lines": [{"start": 0.2, "end": 0.9, "text": "раз"}, {"start": 1.2, "end": 1.6, "text": "ээ"},
                  {"start": 2.2, "end": 2.9, "text": "два"}],
        "words": [{"word": "раз", "start": 0.2, "end": 0.9}, {"word": "ээ", "start": 1.2, "end": 1.6},
                  {"word": "два", "start": 2.2, "end": 2.9}],
    })
    store.set_clip_plan_render_settings(plan["id"], {"cuts": [{"start": 1.0, "end": 2.0}]})
    clip = store.create_clip(plan["source_id"], clip_plan_id=plan["id"], ffmpeg_preset_id=preset["id"])
    result = service._plan_subtitles_for_clip(clip["id"], 25.0)
    assert [s.text for s in result.segments] == ["раз", "два"]
    assert [w.word for w in result.words] == ["раз", "два"]
    assert result.words[1].start == pytest.approx(1.2)


# ---------------------------------------------------------------- API


def _api_plan(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    source_path = settings.source_dir / "s.mp4"
    source_path.write_bytes(b"src")
    source = store.create_source("upload", source_path, duration_sec=60, width=1280, height=720, status="ready")
    analysis = store.create_ai_analysis(source["id"], "mock", status="succeeded")
    seg = store.create_ai_segment(analysis["id"], {"start_sec": 10, "end_sec": 16, "title": "A"})
    plan = store.create_clip_plan(source["id"], analysis["id"], "План", segment_ids=[seg["id"]])
    return client, store, plan


def test_api_suggest_apply_and_patch_keeps_cuts(tmp_path, monkeypatch):
    client, store, plan = _api_plan(tmp_path, monkeypatch)
    pid = plan["id"]
    assert client.post(f"/api/clip-plans/{pid}/cuts/suggest", json={"mode": "pauses"}).status_code == 400
    store.set_clip_plan_subtitles(pid, {"lines": [], "words": [
        {"word": "раз", "start": 0.0, "end": 0.5}, {"word": "ээ", "start": 1.5, "end": 1.8},
        {"word": "два", "start": 3.0, "end": 3.5}]})
    out = client.post(f"/api/clip-plans/{pid}/cuts/suggest", json={"mode": "both"}).raise_for_status().json()
    assert out["before_sec"] == 6.0 and out["saved_sec"] > 1.5 and out["items"]
    assert not (store.get_clip_plan(pid).get("render_settings") or {}).get("cuts")  # подсказка ничего не сохранила

    bad = client.post(f"/api/clip-plans/{pid}/cuts/apply", json={"cuts": [{"start": 2, "end": 1}]})
    assert bad.status_code == 422 and "problems" in bad.json()["detail"]
    applied = client.post(f"/api/clip-plans/{pid}/cuts/apply", json={"cuts": out["cuts"]}).raise_for_status().json()
    assert applied["cuts"] == out["cuts"] and applied["after_sec"] == pytest.approx(6.0 - out["saved_sec"])
    assert store.get_clip_plan(pid)["render_settings"]["cuts"] == out["cuts"]

    # Автосохранение редактора без ключа cuts — вырезы на месте; [] — убраны.
    client.patch(f"/api/clip-plans/{pid}/render-settings", json={"settings": {"mirror": True}}).raise_for_status()
    assert store.get_clip_plan(pid)["render_settings"]["cuts"] == out["cuts"]
    client.patch(f"/api/clip-plans/{pid}/render-settings", json={"settings": {"cuts": []}}).raise_for_status()
    assert store.get_clip_plan(pid)["render_settings"]["cuts"] == []

    # Отмена возвращает вырезы (снимок перед правкой).
    client.post(f"/api/clip-plans/{pid}/undo").raise_for_status()
    assert store.get_clip_plan(pid)["render_settings"]["cuts"] == out["cuts"]


def test_spec_round_trip_and_validation(tmp_path, monkeypatch):
    client, store, plan = _api_plan(tmp_path, monkeypatch)
    pid = plan["id"]
    spec = client.get(f"/api/clip-plans/{pid}/spec").raise_for_status().json()
    assert spec["render"]["cuts"] == []
    spec["render"]["cuts"] = [{"start": 2.5, "end": 3}, {"start": 0.5, "end": 1}]
    fresh = client.put(f"/api/clip-plans/{pid}/spec", json=spec).raise_for_status().json()["spec"]
    assert fresh["render"]["cuts"] == [{"start": 0.5, "end": 1.0}, {"start": 2.5, "end": 3.0}]
    spec["render"]["cuts"] = [{"start": 1, "end": 1.02}]
    bad = client.put(f"/api/clip-plans/{pid}/spec", json=spec)
    assert bad.status_code == 422
    assert any(p.startswith("render.cuts[0]") for p in bad.json()["detail"]["problems"])


# ---------------------------------------------------------------- настоящий ffmpeg


def test_real_ffmpeg_two_cuts_shorten_the_clip(tmp_path, monkeypatch):
    _require_ffmpeg()
    store = _store(tmp_path, monkeypatch)
    source_path = settings.source_dir / "cuts-src.mp4"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=size=320x180:rate=25",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
         "-t", "8", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
         str(source_path)],
        check=True,
    )
    meta = probe_media(source_path)
    source = store.create_source("upload", source_path, duration_sec=meta.duration_sec, width=meta.width,
                                 height=meta.height, fps=meta.fps, status="ready")
    analysis = store.create_ai_analysis(source["id"], "mock", status="succeeded")
    seg = store.create_ai_segment(analysis["id"], {"start_sec": 0, "end_sec": 8, "title": "Всё"})
    plan = store.create_clip_plan(source["id"], analysis["id"], "Вырезы", segment_ids=[seg["id"]])
    preset = store.create_ffmpeg_preset("Tiny", output_width=180, output_height=320, fps=25, scale_mode="cover",
                                        extra={"crf": 35})
    store.set_clip_plan_render_settings(plan["id"], {"cuts": [{"start": 2.0, "end": 3.0}, {"start": 5.0, "end": 6.5}]})

    clip = ClipRenderService(store).render_clip_plan(plan["id"], ffmpeg_preset_id=preset["id"])

    assert clip["status"] == "succeeded", clip.get("error")
    out = probe_media(Path(clip["output_path"]))
    assert out.duration_sec == pytest.approx(5.5, abs=0.12)
    # Картинка и звук не разъехались на склейках (части — целое число кадров).
    streams = {
        st.get("codec_type"): float(st.get("duration") or 0)
        for st in (out.raw or {}).get("streams") or []
    }
    assert abs(streams["video"] - streams["audio"]) < 0.1


# ---------------------------------------------------------------- регрессии: вырезы + переходы


FADE = {"type": "fade", "duration": 0.5, "smart": True}


def _two_pieces(tmp_path, monkeypatch, settings_extra=None, pieces=((0, 10), (30, 40))):
    store, service, plan, preset, _calls = _mock_render(tmp_path, monkeypatch, list(pieces))
    store.set_clip_plan_render_settings(plan["id"], {"transition": FADE, **(settings_extra or {})})
    store.set_clip_plan_subtitles(plan["id"], {
        "lines": [{"start": 12.0, "end": 12.5, "text": "два"}],
        "words": [{"word": "раз", "start": 5.0, "end": 5.5}, {"word": "два", "start": 12.0, "end": 12.5}],
    })
    return store, service, store.get_clip_plan(plan["id"]), preset


def test_subtitles_shift_by_real_overlap_after_cuts(tmp_path, monkeypatch):
    # Два куска по 10 с, наплыв 0.5, вырез 9.0–9.7: от первого куска остаётся
    # хвост 0.3 с, и рендер урезает наплыв до 40% от него (0.12), а не 0.5.
    from app.cuts import cut_caption_words

    store, service, plan, preset = _two_pieces(tmp_path, monkeypatch, {"cuts": [{"start": 9.0, "end": 9.7}]})
    clip = store.create_clip(plan["source_id"], clip_plan_id=plan["id"], ffmpeg_preset_id=preset["id"])
    result = service._plan_subtitles_for_clip(clip["id"], 30.0)
    assert result.words[0].start == pytest.approx(5.0)
    assert result.words[1].start == pytest.approx(12.0 - 0.7 - 0.12)  # не на 0.38 с раньше
    assert result.segments[0].start == pytest.approx(12.0 - 0.7 - 0.12)
    words = cut_caption_words(store, plan, plan_cuts(plan["segments"], plan["render_settings"]["cuts"], 30.0))
    assert words[1]["t0"] == pytest.approx(11.18)


def test_smart_join_between_adjacent_source_pieces_does_not_shift(tmp_path, monkeypatch):
    from app.motion_render import plan_caption_words

    store, service, plan, preset = _two_pieces(tmp_path, monkeypatch, pieces=((0, 10), (10, 20)))
    assert plan_caption_words(store, plan)[1]["t0"] == pytest.approx(12.0)  # «умная» склейка встык
    clip = store.create_clip(plan["source_id"], clip_plan_id=plan["id"], ffmpeg_preset_id=preset["id"])
    assert service._plan_subtitles_for_clip(clip["id"], 30.0).words[1].start == pytest.approx(12.0)


def test_far_pieces_still_shift_by_the_full_transition(tmp_path, monkeypatch):
    from app.motion_render import plan_caption_words

    store, _service, plan, _preset = _two_pieces(tmp_path, monkeypatch)
    assert plan_caption_words(store, plan)[1]["t0"] == pytest.approx(11.5)


def test_layers_camera_chat_inserts_follow_cuts_in_finished_time():
    segs = [{"id": 1, "start_sec": 0.0, "end_sec": 10.0}, {"id": 2, "start_sec": 30.0, "end_sec": 40.0}]
    cut_plan = plan_cuts(segs, [{"start": 9.0, "end": 9.7}], 30.0)
    m = cut_plan.finished_mapper(segs, FADE)
    # Слой во времени готового клипа без вырезов: кусок 2 начинается в 10 − 0.5 = 9.5,
    # 11.5 — это 2 с от его начала. После выреза кусок 2 начинается в 9.3 − 0.12 = 9.18,
    # значит слой — в 11.18 (по «кускам подряд» он уезжал на 10.8).
    layers = remap_layers([{"id": "a", "start": 11.5, "end": 12.5}, {"id": "b", "start": 2.0, "end": 3.0}], m)
    assert [l["start"] for l in layers] == pytest.approx([11.18, 2.0])
    assert [k["t"] for k in remap_camera([{"t": 11.5, "zoom": 1.2}], m)] == pytest.approx([11.18])
    chat = remap_motion({"layers": [], "camera": [], "chat": [{"t": 11.5}, {"t": 9.2}]}, m)["chat"]
    assert [c["t"] for c in chat] == pytest.approx([11.18])  # 9.2 готового = 9.2 подряд — в вырезе
    assert remap_inserts([{"asset_id": 1, "at": 11.5}], m)[0]["at"] == pytest.approx(11.18, abs=0.01)


def test_render_remaps_layers_through_finished_time(tmp_path, monkeypatch):
    store, service, plan, preset = _two_pieces(tmp_path, monkeypatch, {
        "cuts": [{"start": 9.0, "end": 9.7}],
        "layers": [{"id": "t1", "type": "text", "start": 11.5, "end": 12.5, "props": {"text": "Хук"}}],
    })
    seen: dict = {}

    def fake_motion(render_id, base, preset, banner, motion):
        seen.update(motion)
        return []

    monkeypatch.setattr(service, "_apply_motion", fake_motion)
    clip = service.render_clip_plan(plan["id"], ffmpeg_preset_id=preset["id"], transition=FADE)
    assert clip["status"] == "succeeded", clip.get("error")
    assert seen["layers"][0]["start"] == pytest.approx(11.18)


def test_subtitle_sync_qc_uses_the_rendered_word_timing(tmp_path, monkeypatch):
    store, service, plan, preset = _two_pieces(tmp_path, monkeypatch, {"cuts": [{"start": 9.0, "end": 9.7}]})
    clip = store.create_clip(plan["source_id"], clip_plan_id=plan["id"], ffmpeg_preset_id=preset["id"])
    got: dict = {}

    def fake_offset(path, words):
        got["words"] = words
        return None

    monkeypatch.setattr("app.render_qc.estimate_subtitle_offset", fake_offset)
    service._qc_subtitle_sync(store.get_clip(clip["id"]), Path("x.mp4"), {"ok": True, "issues": []})
    rendered = service._plan_subtitles_for_clip(clip["id"], float(preset.get("fps") or 30))
    assert [w["start"] for w in got["words"]] == pytest.approx([w.start for w in rendered.words])
    assert got["words"][1]["start"] == pytest.approx(11.18, abs=0.02)


# ---------------------------------------------------------------- регрессии: подсказки


def test_two_word_phrase_needs_a_pause_to_be_a_filler():
    tight = [_w("это", 0.0, 0.2), _w("самое", 0.2, 0.5), _w("главное", 0.52, 0.9), _w("в", 1.0, 1.05),
             _w("общем", 1.05, 1.4), _w("смысле", 1.42, 1.8)]
    assert suggest_cuts(tight, 1.8, mode="fillers")["items"] == []
    paused = [_w("я", 0.0, 0.2), _w("это", 0.25, 0.4), _w("самое", 0.4, 0.7), _w("пришёл", 1.1, 1.5)]
    items = suggest_cuts(paused, 1.5, mode="fillers")["items"]
    assert len(items) == 1 and "это самое" in items[0]["reason"]


def test_risky_long_pause_does_not_swallow_a_neighbouring_filler():
    words = [_w("раз", 0.0, 0.4), _w("ээ", 0.5, 0.8), _w("два", 4.0, 4.4)]
    out = suggest_cuts(words, 4.4, mode="both", min_pause=0.45, keep_pad=0.12)
    assert out["cuts"] == [{"start": 0.5, "end": 0.8}]  # паразит — рекомендован отдельно
    risky = [it for it in out["items"] if it.get("risky")]
    assert len(risky) == 1 and risky[0]["start"] >= 0.8 and risky[0]["end"] == pytest.approx(3.88)
    safe = [it for it in out["items"] if not it.get("risky")]
    assert len(safe) == 1 and "ээ" in safe[0]["reason"]


# ---------------------------------------------------------------- регрессии: сохранение вырезов


def test_check_cuts_for_plan_rejects_past_end_and_whole_clip():
    from app.cuts import check_cuts_for_plan

    plan = {"segments": [{"start_sec": 10.0, "end_sec": 16.0}]}
    cuts, problems = check_cuts_for_plan(plan, [{"start": 7, "end": 8}])
    assert cuts == [] and "за концом клипа" in problems[0]
    cuts, problems = check_cuts_for_plan(plan, [{"start": 0, "end": 6}])
    assert cuts == [] and "весь клип" in problems[0]
    cuts, problems = check_cuts_for_plan(plan, [{"start": 5, "end": 9}])
    assert problems == [] and cuts == [{"start": 5.0, "end": 6.0}]  # хвост за концом обрезан


def test_api_and_agent_apply_cuts_validate_against_the_clip(tmp_path, monkeypatch):
    from app.agent_tools import ToolContext, ToolError, h_apply_cuts, h_suggest_cuts

    client, store, plan = _api_plan(tmp_path, monkeypatch)
    pid = plan["id"]
    bad = client.post(f"/api/clip-plans/{pid}/cuts/apply", json={"cuts": [{"start": 0, "end": 6}]})
    assert bad.status_code == 400 and "весь клип" in bad.json()["detail"]
    bad = client.post(f"/api/clip-plans/{pid}/cuts/apply", json={"cuts": [{"start": 20, "end": 21}]})
    assert bad.status_code == 400 and "за концом" in bad.json()["detail"]
    assert not (store.get_clip_plan(pid).get("render_settings") or {}).get("cuts")

    ctx = ToolContext(store=store, clip_plan_id=pid)
    with pytest.raises(ToolError):
        h_apply_cuts(ctx, {"cuts": [{"start": 0, "end": 6}]})
    assert h_apply_cuts(ctx, {"cuts": [{"start": 1, "end": 2}]})["cuts"] == 1

    seen: dict = {}

    def fake_suggest(store, plan_id, mode, min_pause, keep_pad):
        seen.update(min_pause=min_pause, keep_pad=keep_pad)
        return {}

    monkeypatch.setattr("app.cuts.suggest_for_plan", fake_suggest)
    h_suggest_cuts(ctx, {"keep_pad": 0})
    assert seen == {"min_pause": 0.45, "keep_pad": 0.0}  # keep_pad=0 — законное значение, не 0.12


def test_source_time_inverts_cut_map():
    from app.cuts import CutMap

    m = CutMap([{"start": 0.0, "end": 7.77}, {"start": 16.91, "end": 21.78}])
    for pre in (8.0, 10.0, 16.0, 22.5, 30.0):
        assert abs(m.source_time(m(pre)) - pre) < 1e-9
    assert m.source_time(0.0) == 7.77  # начало готового клипа — сразу после первого выреза


def test_hook_survives_cut_of_clip_start():
    from app.cuts import CutMap, remap_layers

    hook = {"id": "auto_hook", "type": "text", "start": 0.0, "end": 2.5, "props": {}}
    punch = {"id": "p", "type": "text", "start": 3.0, "end": 4.0, "props": {}}
    late = {"id": "l", "type": "text", "start": 10.0, "end": 11.0, "props": {}}
    out = {l["id"]: l for l in remap_layers([hook, punch, late], CutMap([{"start": 0.0, "end": 7.77}]))}
    assert (out["auto_hook"]["start"], out["auto_hook"]["end"]) == (0.0, 2.5)
    assert "p" not in out  # обычный слой целиком в вырезе — пропадает, как раньше
    assert abs(out["l"]["start"] - 2.23) < 1e-6


def test_insert_duration_shrinks_when_cut_runs_through_it():
    from app.cuts import CutMap, remap_inserts

    out = remap_inserts([{"asset_id": 1, "at": 10.0, "duration": 4.0}], CutMap([{"start": 11.0, "end": 13.0}]))
    assert out[0]["at"] == 10.0 and out[0]["duration"] == 2.0


def test_cut_drops_words_mostly_inside_and_rebuilds_line():
    """Агент-монтажёр: «слова из вырезанного всё равно попадали в субтитры»."""
    from app.cuts import CutMap, remap_subtitles

    m = CutMap([(2.0, 5.0)])
    payload = {
        "lines": [{"start": 0.0, "end": 6.0, "text": "Я везу не картошку колы я везу"}],
        "words": [{"word": "Я", "start": 0.0, "end": 0.4}, {"word": "картошку", "start": 1.5, "end": 2.2},
                  {"word": "колы", "start": 2.1, "end": 3.0}, {"word": "я", "start": 4.6, "end": 5.2},
                  {"word": "везу", "start": 5.2, "end": 5.8}],
        "duration": 8.0,
    }
    fresh = remap_subtitles(payload, m)
    # «колы» и «я» серединой в вырезе — пропали, хоть и торчат краем; «картошку» серединой до выреза — осталась
    assert [w["word"] for w in fresh["words"]] == ["Я", "картошку", "везу"]
    assert [l["text"] for l in fresh["lines"]] == ["Я картошку везу"]


def test_cut_edge_warnings_split_word_and_scrap():
    from app.cuts import cut_edge_warnings

    words = [{"word": "Кока-Колы", "start": 1.0, "end": 1.6}, {"word": "я", "start": 2.1, "end": 2.3},
             {"word": "орешки", "start": 4.0, "end": 4.5}]
    out = cut_edge_warnings([{"start": 1.3, "end": 2.0}, {"start": 2.4, "end": 3.9}], words, 6.0)
    assert any("режет слово «Кока-Колы»" in w for w in out)
    assert any("обрывок «я»" in w for w in out)
    assert cut_edge_warnings([{"start": 1.7, "end": 3.9}], words, 6.0) == []
