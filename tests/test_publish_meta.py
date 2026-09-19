import json

import pytest

from app.ai.publish_meta import clip_context, generate_publish_metadata, parse_metadata


class FakeStore:
    def __init__(self):
        self.clip = {"id": 7, "source_id": 3, "clip_plan_id": 11, "segment_id": None, "title": "Клип", "description": "", "duration_sec": 21.3}
        self.plan = {
            "title": "Вторая запретка за год",
            "description": "Стримерша паникует",
            "category": "реакция",
            "segments": [{"start_sec": 10.0, "end_sec": 20.0, "title": "Паника"}],
        }
        self.cues = [
            {"start": 1.0, "end": 5.0, "text": "до клипа"},
            {"start": 9.0, "end": 11.0, "text": "было слово?"},
            {"start": 12.0, "end": 15.0, "text": "я удалю"},
            {"start": 25.0, "end": 26.0, "text": "после клипа"},
        ]

    def get_clip(self, clip_id):
        return self.clip

    def get_clip_plan(self, plan_id, include_segments=True):
        return self.plan

    def get_source_transcript(self, source_id):
        return self.cues

    def get_default_prompt_preset(self, task):
        assert task == "publishing"
        return {"label": "Мой стиль", "prompt": "Без эмодзи."}


class FakeClient:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def generate_content(self, model, payload):
        self.calls.append((model, payload))
        return {"candidates": [{"content": {"parts": [{"text": json.dumps(self.reply, ensure_ascii=False)}]}}]}


def test_context_keeps_only_transcript_inside_segments():
    ctx = clip_context(FakeStore(), FakeStore().clip)
    assert ctx["transcript"] == "было слово? я удалю"
    assert ctx["moment_title"] == "Вторая запретка за год"
    assert ctx["segment_notes"] == ["Паника"]


def test_parse_cleans_hashtags_and_caps_title():
    meta = parse_metadata(
        json.dumps({"title": "  " + "а" * 200, "description": "Описание", "hashtags": ["Shorts", "#стрим", "#стрим", "мем ы", ""]})
    )
    assert len(meta["title"]) <= 95
    assert meta["hashtags"] == ["#shorts", "#стрим", "#мемы"]


def test_parse_rejects_non_json():
    with pytest.raises(RuntimeError):
        parse_metadata("not json")


def test_generate_uses_preset_and_context():
    client = FakeClient({"title": "Стримерша спалилась", "description": "Смотри до конца.", "hashtags": ["#стрим", "shorts"]})
    meta = generate_publish_metadata(FakeStore(), 7, client=client, model="m")
    assert meta["title"] == "Стримерша спалилась"
    assert meta["hashtags"] == ["#стрим", "#shorts"]
    assert meta["has_transcript"] is True
    model, payload = client.calls[0]
    text = payload["contents"][0]["parts"][0]["text"]
    assert model == "m"
    assert "Без эмодзи." in text and "я удалю" in text
    assert payload["generationConfig"]["responseMimeType"] == "application/json"
