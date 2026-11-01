"""Субтитры за звуком J/L: слова заменённого звука убираются, слова «ручки» встают туда,
где их слышно, — тем же сдвигом, что у их куска (тайминги не растягиваются)."""

from __future__ import annotations

import pytest

from app.agent_tools import ToolContext, run_tool
from app.audio_edit import handle_extents, heard_subtitles, split_handle_words
from app.clip_history import snapshot
from app.cuts import subtitles_to_final
from app.transitions import part_overlaps
from test_cuts import _two_pieces

# Три куска по 6 с: A (src 10–16), B (30–36), C (50–56). Стыки на 6 и 12 («куски подряд»).
SEGS = [{"id": 1, "start_sec": 10.0, "end_sec": 16.0}, {"id": 2, "start_sec": 30.0, "end_sec": 36.0},
        {"id": 3, "start_sec": 50.0, "end_sec": 56.0}]
# У куска B J 0.5 (звук B раньше картинки — под хвостом A) и L 0.4 (звук B тянется под голову C).
JOINS = [{"piece": 2, "lead": 0.5, "lag": 0.4}]
WORDS = [
    {"word": "a-ранние", "start": 5.0, "end": 5.3},   # A, до J — слышно
    {"word": "a-хвост", "start": 5.6, "end": 5.9},    # A, под J — звук заменён звуком B
    {"word": "b-первое", "start": 6.1, "end": 6.4},   # B — как было
    {"word": "c-голова", "start": 12.1, "end": 12.3},  # C, под L — звук заменён хвостом B
    {"word": "c-после", "start": 12.5, "end": 12.8},  # C, после L — слышно
]
HANDLE = [
    {"piece": 2, "word": "j-ручка", "start": 29.6, "end": 29.9},  # src до начала B
    {"piece": 2, "word": "l-ручка", "start": 36.1, "end": 36.3},  # src после конца B
    {"piece": 2, "word": "мимо", "start": 20.0, "end": 20.3},     # не в ручке — не показывается
]
PAYLOAD = {"words": WORDS, "lines": [{"start": 5.0, "end": 5.9, "text": "a-ранние a-хвост"},
                                     {"start": 6.1, "end": 6.4, "text": "b-первое"},
                                     {"start": 12.1, "end": 12.8, "text": "c-голова c-после"}],
           "handle_words": HANDLE}


def _times(payload):
    return {w["word"]: (w["start"], w["end"]) for w in payload["words"]}


def test_captions_follow_j_and_l_cut_audio_without_transition():
    final = subtitles_to_final(PAYLOAD, SEGS, None, joins=JOINS)
    times = _times(final)
    assert "a-хвост" not in times and "c-голова" not in times and "мимо" not in times
    assert times["a-ранние"] == (5.0, 5.3) and times["b-первое"] == (6.1, 6.4) and times["c-после"] == (12.5, 12.8)
    # J: слово за 0.4 с до начала B слышно за 0.4 с до стыка (часы куска B).
    assert times["j-ручка"] == pytest.approx((5.6, 5.9))
    # L: слово через 0.1 с после конца B слышно через 0.1 с после стыка.
    assert times["l-ручка"] == pytest.approx((12.1, 12.3))
    # Длина слова та же — без растяжения.
    assert all(round(b - a, 3) == round(w["end"] - w["start"], 3)
               for w in HANDLE[:2] for name, (a, b) in times.items() if name == w["word"])
    texts = [line["text"] for line in final["lines"]]
    assert texts == ["a-ранние", "j-ручка", "b-первое", "l-ручка", "c-после"]


def test_captions_follow_jl_under_a_real_transition():
    fade = {"type": "fade", "duration": 0.3}
    overlaps = part_overlaps(SEGS, fade)
    assert overlaps[0] > 0 and overlaps[1] > 0
    o0, o1 = overlaps
    times = _times(subtitles_to_final(PAYLOAD, SEGS, fade, joins=JOINS))
    b_start = 6.0 - o0             # начало картинки B в готовом ролике
    c_start = 12.0 - o0 - o1        # начало картинки C
    # Звук B синхронен с картинкой B: слово ручки — на (src − начало B) от начала B.
    assert times["j-ручка"][0] == pytest.approx(b_start - 0.4)
    assert times["b-первое"][0] == pytest.approx(6.1 - o0)
    assert times["l-ручка"][0] == pytest.approx(c_start + o1 + 0.1)  # от конца картинки B
    assert "a-хвост" not in times and "c-голова" not in times


def test_without_joins_or_handles_nothing_moves():
    plain = subtitles_to_final(PAYLOAD, SEGS, None)
    assert _times(plain) == {w["word"]: (w["start"], w["end"]) for w in WORDS}
    # J/L есть, ручки не распознаны — заменённые слова убраны, чужих не появилось.
    got = heard_subtitles({"words": WORDS}, SEGS, SEGS, JOINS)
    assert [w["word"] for w in got["words"]] == ["a-ранние", "b-первое", "c-после"]


def test_handle_extents_and_split_after_transcription():
    extents = handle_extents(SEGS, JOINS, margin=0.3)
    assert extents == [(0.0, 0.0), (0.8, 0.7), (0.0, 0.0)]
    ext = [(10.0, 16.0), (29.2, 36.7), (50.0, 56.0)]
    # Распознано по кускам с ручками подряд: A 0–6, B' 6–13.5 (src 29.2…), C' 13.5–19.5.
    raw = {"words": [{"word": "a", "start": 1.0, "end": 1.2},
                     {"word": "j", "start": 6.4, "end": 6.7},    # src 29.6–29.9 — ручка J
                     {"word": "b", "start": 7.0, "end": 7.3},    # src 30.2 — в куске B
                     {"word": "l", "start": 13.0, "end": 13.2},  # src 36.2 — ручка L
                     {"word": "c", "start": 14.0, "end": 14.3}],
           "lines": [{"start": 6.4, "end": 7.3, "text": "j b"}], "duration": 19.5}
    out = split_handle_words(raw, ext, SEGS)
    assert [(w["word"], w["start"]) for w in out["words"]] == [("a", 1.0), ("b", 6.2), ("c", 12.5)]
    assert [(w["word"], w["piece"], w["start"]) for w in out["handle_words"]] == [("j", 2, 29.6), ("l", 2, 36.2)]
    assert out["lines"] == [{"start": 6.2, "end": 6.5, "text": "b"}] and out["duration"] == 18.0
    assert {(h["piece"], h["side"]) for h in out["handle_spans"]} == {(2, "head"), (2, "tail")}


def test_set_audio_joins_warns_until_handles_are_transcribed(tmp_path, monkeypatch):
    store, _service, plan, _preset = _two_pieces(tmp_path, monkeypatch)
    ctx = ToolContext(store=store, clip_plan_id=plan["id"], on_first_change=lambda: snapshot(store, plan["id"], "т"))
    out, ok = run_tool(ctx, "set_audio_joins", {"joins": [{"piece": 1, "lead": 0.4}]})
    assert ok, out
    assert any("не распознан" in w for w in out.get("warnings") or [])
    assert "Субтитры идут за звуком" in out["note"]
    subs = store.get_clip_plan_subtitles(plan["id"])
    seg_b = plan["segments"][1]
    store.set_clip_plan_subtitles(plan["id"], {**subs, "handle_spans": [
        {"piece": seg_b["id"], "side": "head", "start": float(seg_b["start_sec"]) - 0.7,
         "end": float(seg_b["start_sec"])}]})
    out, ok = run_tool(ctx, "set_audio_joins", {"joins": [{"piece": 1, "lead": 0.4}]})
    assert ok and not any("не распознан" in w for w in out.get("warnings") or [])
