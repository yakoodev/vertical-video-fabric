"""🤖 ИИ-монтаж: focus carry-over, proposal → clip file, the validation loop."""

from __future__ import annotations

import json

from app.ai.montage import carry_focus, propose_montage, to_spec
from test_api_pipeline import _require_ffmpeg
from test_clip_spec import _setup


def _spec():
    return {
        "schema": "vvf.clip/1",
        "title": "T",
        "description": "D",
        "pieces": [
            {"segment_id": 1, "title": "A", "start_sec": 100.0, "end_sec": 120.0,
             "focus": [{"t": 0, "x": 0.2}, {"t": 10, "x": 0.8, "cut": True}]},
            {"segment_id": 2, "title": "B", "start_sec": 200.0, "end_sec": 215.0, "focus": [{"t": 0, "x": 0.5}]},
        ],
        "render": {"subs_on": False, "transition": {"type": "cut", "sfx": "none"}, "cover": {"mode": "none"}},
    }


def test_carry_focus_rebases_to_new_piece_start():
    # New piece 105–118 inside A: starts on x=0.2, keeps the cut at abs 110 → t=5.
    track = carry_focus(_spec(), 105.0, 118.0)
    assert track == [{"t": 0.0, "x": 0.2}, {"t": 5.0, "x": 0.8, "cut": True}]
    # Starting after the cut inherits the post-cut framing.
    assert carry_focus(_spec(), 112.0, 119.0) == [{"t": 0.0, "x": 0.8}]
    assert carry_focus(_spec(), 300.0, 310.0) is None


def test_to_spec_splits_piece_reuses_segment_once_and_sets_render():
    proposal = {
        "pieces": [
            {"source_piece": 1, "start_sec": 205, "end_sec": 213, "title": "Хук"},
            {"source_piece": 0, "start_sec": 100, "end_sec": 107, "title": "Начало"},
            {"source_piece": 0, "start_sec": 111, "end_sec": 119, "title": "Конец"},
        ],
        "transition": {"type": "flash", "sfx": "whoosh"},
        "subtitles": True,
        "cover": {"piece": 0, "offset": 99},
        "rationale": ["хук вперёд", "вырезал паузу 107–111"],
    }
    out = to_spec(_spec(), proposal)
    assert [p.get("segment_id") for p in out["pieces"]] == [2, 1, None]  # A reused once, then a new piece
    assert out["pieces"][2]["focus"] == [{"t": 0.0, "x": 0.8}]
    assert out["render"]["transition"]["type"] == "flash" and out["render"]["transition"]["sfx"] == "whoosh"
    assert out["render"]["subs_on"] is True
    assert out["render"]["cover"]["mode"] == "frame" and out["render"]["cover"]["offset"] <= 8
    assert "вырезал паузу" in out["notes"]


class LoopClient:
    """First answer strays outside the shown material; after feedback it fixes it."""

    def __init__(self, first, second):
        self.answers = [first, second]
        self.requests = []

    def generate_content(self, model, payload):
        self.requests.append(payload)
        text = json.dumps(self.answers[len(self.requests) - 1], ensure_ascii=False)
        return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def test_agent_loop_fixes_invalid_answer_and_writes_nothing(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, source, plan, a, b = _setup(tmp_path, monkeypatch)
    store.set_source_transcript(source["id"], [{"start": 0.2, "end": 4.8, "text": "привет"}])
    before = store.get_clip_plan(plan["id"])
    base = {"transition": {"type": "cut", "sfx": "none"}, "subtitles": True, "cover": {"piece": 0, "offset": 1}}
    bad = {**base, "pieces": [{"source_piece": 0, "start_sec": 500, "end_sec": 506, "title": "x"}], "rationale": ["?"]}
    good = {**base, "pieces": [{"source_piece": 1, "start_sec": 1, "end_sec": 6, "title": "Хук"}], "rationale": ["ок"]}
    client = LoopClient(bad, good)

    out = propose_montage(store, plan["id"], goal="короче", client=client, model="m")

    assert out["attempts"] == 2
    feedback = client.requests[1]["contents"][-1]["parts"][0]["text"]
    assert "вне переданных окон" in feedback
    assert "короче" in client.requests[0]["contents"][0]["parts"][0]["text"]
    assert out["spec"]["pieces"][0]["segment_id"] == b["id"]
    assert out["diff"]["total_after"] == 5.0
    # A proposal only — the clip is untouched until the owner applies it.
    after = store.get_clip_plan(plan["id"])
    assert [s["id"] for s in after["segments"]] == [s["id"] for s in before["segments"]]


def test_apply_keeps_pre_ai_backup_and_undo_restores_dropped_piece(tmp_path, monkeypatch):
    _require_ffmpeg()
    from app.ai.montage import apply_montage, undo_montage
    from app.clip_spec import export_spec

    _client, store, _source, plan, a, b = _setup(tmp_path, monkeypatch)
    original = export_spec(store, plan["id"])
    ai = {**original, "pieces": [{**original["pieces"][1], "start_sec": 1.0, "end_sec": 6.0}]}  # A dropped
    apply_montage(store, plan["id"], ai)
    assert store.get_clip_plan(plan["id"])["has_montage_backup"] is True
    # A second AI apply must not overwrite the owner's original.
    apply_montage(store, plan["id"], {**ai, "title": "ещё вариант"})
    assert [p["segment_id"] for p in store.get_clip_plan_montage_backup(plan["id"])["pieces"]] == [a["id"], b["id"]]

    restored, _changes = undo_montage(store, plan["id"])
    assert [(p["start_sec"], p["end_sec"]) for p in restored["pieces"]] == [(0.0, 5.0), (1.0, 6.0)]
    assert restored["title"] == original["title"]
    assert store.get_clip_plan(plan["id"])["has_montage_backup"] is False


def test_montage_many_reports_each_clip_and_survives_failures(tmp_path, monkeypatch):
    _require_ffmpeg()
    from app.ai.montage import montage_many
    from app.clip_spec import export_spec

    _client, store, source, plan, a, b = _setup(tmp_path, monkeypatch)
    other = store.create_clip_plan(source["id"], plan["analysis_id"], "Второй", segment_ids=[a["id"]])

    def fake_propose(st, plan_id, goal=""):
        if plan_id == other["id"]:
            raise RuntimeError("модель легла")
        spec = export_spec(st, plan_id)
        return {"spec": spec, "diff": {"total_before": 10, "total_after": 10, "after": spec["pieces"]}, "rationale": ["ок"]}

    rendered = []
    items = montage_many(store, [plan["id"], other["id"]], render=lambda pid: rendered.append(pid) or {"id": 1},
                         propose=fake_propose)
    by_id = {i["plan_id"]: i for i in items}
    assert by_id[plan["id"]]["status"] == "done" and rendered == [plan["id"]]
    assert by_id[other["id"]]["status"] == "failed" and "модель легла" in by_id[other["id"]]["error"]
