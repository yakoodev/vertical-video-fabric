"""Части длинного ролика для TikTok (app/tiktok_parts.py, инструмент plan_tiktok_parts): ≤ 58 с песни в части,
стыки на долях (лучше — начало фразы), никогда посреди боя, «часть N/M» и «Часть N+1 → в профиле».

Сетки долей и бои — синтетика: 120 BPM (доля 0.5 с), первая доля 0.2 с, сильная — каждая четвёртая, фраза —
четыре такта (8 с)."""

from __future__ import annotations

import pytest

from app.tiktok_parts import boundary_candidates, grid_lists, phrase_marks, plan_parts

GRID = {"bpm": 120, "period": 0.5, "first_beat": 0.2, "first_downbeat": 0.2, "downbeat_every": 4}
PHRASE = 8.0  # 4 такта × 4 доли × 0.5 с


def _grid(duration):
    return grid_lists(GRID, duration)


def _inside_any(t, events):
    return any(a + 1e-6 < t < b - 1e-6 for a, b in events)


def _check_parts(plan, duration, events, max_song=58.0, song=None):
    parts = plan["parts"]
    assert parts[0]["start"] == 0.0 and parts[-1]["end"] == pytest.approx(duration)
    for prev, nxt in zip(parts, parts[1:]):
        assert prev["end"] == nxt["start"]
        assert not _inside_any(prev["end"], events), (prev["end"], events)
    for p in parts:
        lo, hi = song or (0.0, duration)
        sec = max(0.0, min(p["end"], hi) - max(p["start"], lo))
        assert sec <= max_song + 1e-6 and (p["song"] or {}).get("sec", 0.0) == pytest.approx(sec, abs=1e-3)


def test_phrase_marks_and_candidates_skip_fights():
    beats, downs = _grid(40)
    kinds = phrase_marks(beats, downs)
    assert kinds[0.2] == "phrase" and kinds[2.2] == "downbeat" and kinds[0.7] == "beat" and kinds[8.2] == "phrase"
    assert phrase_marks([0.0, 0.5, 1.0, 1.5, 2.0], None)[2.0] == "downbeat"  # нет сильных — каждая 4-я доля
    cands = boundary_candidates(40, beats=beats, downbeats=downs, events=[(10.0, 20.0)])
    times = [c["t"] for c in cands]
    assert not any(10.0 < t < 20.0 for t in times) and 0.2 in times
    hook = next(c for c in cands if c["t"] == 8.7)  # доля за 1.3 с до боя — бонус «часть начнётся с боя»
    assert hook["cost"] < 1.5 and next(c for c in cands if c["t"] == 30.7)["cost"] == 1.5


def test_three_parts_on_phrases_with_labels_and_end_text():
    duration = 170.0
    beats, downs = _grid(duration)
    events = [(10.0, 25.0), (30.0, 40.0), (80.0, 95.0), (140.0, 160.0)]
    plan = plan_parts(duration, beats=beats, downbeats=downs, events=events, song=(0.0, duration), song_offset=12.0)
    assert plan["count"] == 3  # 170 / 58 → 3; стыки — в окнах 54–58 и 112–116 с, там боёв нет
    # Бой на единственном окне первого стыка (54–58 с) — на три части не делится, частей станет четыре.
    assert plan_parts(duration, beats=beats, downbeats=downs, events=[(50.0, 62.0)], song=(0.0, duration))["count"] == 4
    _check_parts(plan, duration, events)
    cuts = [p["cut"] for p in plan["parts"][:-1]]
    assert all(c["kind"] in ("phrase", "downbeat") for c in cuts), cuts
    assert all(c["at"] in set(beats) for c in cuts)
    p1, p2, p3 = plan["parts"]
    assert (p1["label"], p3["label"]) == ("часть 1/3", "часть 3/3") and p2["caption_suffix"] == " · 2/3"
    assert p1["end_text"] == "Часть 2 → в профиле" and p3["end_text"] is None
    assert p1["song"]["track_start"] == 12.0 and p2["song"]["track_start"] == pytest.approx(12.0 + p2["start"])
    texts = [layer["props"]["text"] for layer in p1["layers"]]
    assert texts == ["часть 1/3", "Часть 2 → в профиле"]
    end = p1["layers"][1]
    assert end["end"] == p1["duration"] and end["start"] == pytest.approx(p1["duration"] - 2.0)  # секунды части
    assert len(p3["layers"]) == 1


def test_fight_across_the_natural_cut_moves_the_cut():
    """Середина ролика 100 с — 50 с, но там бой 46–54: стык уходит за край боя, песни в части ≤ 58 с."""
    duration = 100.0
    beats, downs = _grid(duration)
    events = [(46.0, 54.0)]
    free = plan_parts(duration, beats=beats, downbeats=downs, song=(0.0, duration))
    assert free["count"] == 2 and free["parts"][0]["end"] == 48.2  # без боя — фраза у середины
    plan = plan_parts(duration, beats=beats, downbeats=downs, events=events, song=(0.0, duration))
    assert plan["count"] == 2
    _check_parts(plan, duration, events)
    cut = plan["parts"][0]["end"]
    assert cut <= 46.0 or cut >= 54.0
    assert 42.0 <= cut <= 58.0 and plan["parts"][0]["cut"]["kind"] in ("phrase", "downbeat", "beat")
    plan3 = plan_parts(duration, beats=beats, downbeats=downs, events=events, song=(0.0, duration), parts=3)
    assert plan3["count"] == 3
    _check_parts(plan3, duration, events)


def test_intro_without_song_restart_breaks_and_track_seconds():
    duration = 130.0
    beats, downs = _grid(duration)
    # Песня с 10 с ролика (интро без музыки), трек начинается заново на 70 с.
    plan = plan_parts(duration, beats=beats, downbeats=downs, song=(10.0, duration), song_offset=30.0,
                      song_breaks=[70.0])
    _check_parts(plan, duration, [], song=(10.0, duration))
    assert any(p["end"] == 70.0 for p in plan["parts"])  # повтор трека — обязательный стык
    first, after = plan["parts"][0], next(p for p in plan["parts"] if p["start"] == 70.0)
    assert first["song"]["from"] == 10.0 and first["song"]["track_start"] == 30.0
    assert after["song"]["track_start"] == 0.0  # после повтора — трек с начала


def test_errors_and_fallbacks():
    beats, downs = _grid(120)
    with pytest.raises(ValueError, match="бой"):
        plan_parts(120.0, beats=beats, downbeats=downs, events=[(5.0, 70.0)], song=(0.0, 120.0))
    with pytest.raises(ValueError, match="посреди боя"):
        plan_parts(120.0, beats=beats, events=[(60.0, 75.0)], song=(0.0, 120.0), song_breaks=[66.0])
    with pytest.raises(ValueError, match="max_song_sec"):
        plan_parts(120.0, beats=beats, song=(0.0, 120.0), max_song_sec=90)
    # Долей нет вовсе: стыки — в просветах между боями, с пометкой.
    events = [(0.0, 40.0), (44.0, 90.0)]
    plan = plan_parts(90.0, beats=[], events=events, song=(0.0, 90.0))
    assert plan["count"] == 2 and plan["parts"][0]["end"] == 42.0 and plan["parts"][0]["cut"]["kind"] == "gap"
    assert plan["notes"] and "не на доле" in plan["notes"][0]
    # Ролик короче лимита — одна часть без «далее».
    short = plan_parts(40.0, beats=beats, song=(0.0, 40.0))
    assert short["count"] == 1 and short["parts"][0]["end_text"] is None and short["parts"][0]["label"] == "часть 1/1"


# ---------------------------------------------------------------- инструмент


def test_plan_tiktok_parts_tool(tmp_path, monkeypatch):
    from app.agent_tools import ToolContext, get_tool, run_tool
    from app.modules import module_of_tool
    from test_cuts import _two_pieces

    store, _service, plan, _preset = _two_pieces(tmp_path, monkeypatch)  # 10 + 10 с, наплыв 0.5 → 19.5 с
    ctx = ToolContext(store=store, clip_plan_id=plan["id"])
    args = {"bpm": 120, "first_beat": 0.2, "max_song_sec": 10, "min_part_sec": 3, "events": [{"start": 3.0, "end": 6.0}]}
    out, ok = run_tool(ctx, "plan_tiktok_parts", args)
    assert ok, out
    assert out["count"] == 2 and out["time"] == "final" and out["grid"]["source"] == "manual"
    assert "запечена" in out["song_note"] and "из запроса" in out["events_note"]
    cut = out["parts"][0]["end"]
    assert 9.5 <= cut <= 10.0 and out["parts"][-1]["end"] == pytest.approx(19.5)
    assert all(p["song"]["sec"] <= 10 for p in out["parts"])
    assert store.count_clip_plan_history(plan["id"]) == 0  # только план

    bad, ok = run_tool(ctx, "plan_tiktok_parts", {**args, "events": [{"start": 1.0, "end": 15.0}]})
    assert not ok and "бой" in bad["error"]
    assert get_tool("plan_tiktok_parts").mutating is False and module_of_tool("plan_tiktok_parts") is None
