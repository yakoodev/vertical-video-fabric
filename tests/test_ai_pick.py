"""✨ ИИ выбирает лучшие: picks are starred with a reason; made-up ids are ignored."""

from __future__ import annotations

import json

from app.ai.pick import pick_best
from test_api_pipeline import _require_ffmpeg
from test_clip_spec import _setup


class Client:
    def __init__(self, picks):
        self.picks = picks
        self.prompt = ""

    def generate_content(self, model, payload):
        self.prompt = payload["contents"][0]["parts"][0]["text"]
        return {"candidates": [{"content": {"parts": [{"text": json.dumps({"picks": self.picks}, ensure_ascii=False)}]}}]}


def test_pick_stars_best_ignores_invented_and_keeps_owner_stars(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, source, plan, a, b = _setup(tmp_path, monkeypatch)
    second = store.create_clip_plan(source["id"], plan["analysis_id"], "Второй", segment_ids=[b["id"]])
    owner = store.create_clip_plan(source["id"], plan["analysis_id"], "Мой ★", segment_ids=[a["id"]])
    store.set_clip_plan_flags(owner["id"], favorite=True)
    store.set_source_transcript(source["id"], [{"start": 0.5, "end": 3.0, "text": "смешная фраза"}])

    client = Client([
        {"id": second["id"], "score": 9, "reason": "сильная реакция"},
        {"id": 999999, "score": 10, "reason": "выдумал"},
        {"id": second["id"], "score": 8, "reason": "повтор"},
    ])
    out = pick_best(store, source["id"], count=5, goal="мемы", client=client, model="m")

    assert [p["id"] for p in out["picks"]] == [second["id"]]
    assert "смешная фраза" in client.prompt and "мемы" in client.prompt
    fresh = store.get_clip_plan(second["id"])
    assert fresh["favorite"] and fresh["ai_pick"] == {"score": 9.0, "reason": "сильная реакция"}
    assert store.get_clip_plan(owner["id"])["favorite"]  # never un-starred
