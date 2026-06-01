import json

import httpx
import pytest

from app.ai.artemox import (
    ArtemoxClient,
    ArtemoxVideoAnalyzer,
    build_artemox_analysis_payload,
)
from app.settings import settings


def test_artemox_payload_uses_openai_compatible_schema():
    payload = build_artemox_analysis_payload(
        {
            "source_type": "youtube_url",
            "original_url": "https://youtu.be/example",
            "duration_sec": 60,
            "width": 1920,
            "height": 1080,
            "fps": 30,
        },
        "Find clips",
        "gemini-2.0-flash-lite",
    )

    assert payload["model"] == "gemini-2.0-flash-lite"
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["response_format"]["json_schema"]["schema"]["required"] == ["segments"]
    assert "https://youtu.be/example" in payload["messages"][1]["content"]


def test_artemox_video_analyzer_parses_chat_completion_response():
    class FakeClient:
        def chat_completion(self, payload):
            assert payload["model"] == "gemini-2.0-flash-lite"
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "segments": [
                                        {
                                            "start_sec": 1,
                                            "end_sec": 12,
                                            "title": "Hook",
                                            "description": "Strong opening",
                                            "score": 0.9,
                                            "category": "insight",
                                            "color": "#2563EB",
                                            "reason": "Works standalone",
                                        }
                                    ]
                                }
                            )
                        }
                    }
                ],
                "usage": {"total_tokens": 100},
            }

    result = ArtemoxVideoAnalyzer(FakeClient()).analyze(
        {
            "source_type": "youtube_url",
            "original_url": "https://youtu.be/example",
            "duration_sec": 60,
        },
        "Find clips",
        "gemini-2.0-flash-lite",
    )

    assert result.segments[0].title == "Hook"
    assert result.usage == {"total_tokens": 100}


def test_artemox_video_analyzer_requires_url_source():
    with pytest.raises(RuntimeError, match="requires a URL source"):
        ArtemoxVideoAnalyzer(client=None).analyze(
            {"source_type": "upload", "original_url": "", "duration_sec": 60},
            "Find clips",
            "gemini-2.0-flash-lite",
        )


def test_artemox_client_auth_error(monkeypatch):
    class FakeResponse:
        status_code = 401

        def raise_for_status(self):
            raise AssertionError("auth error should be handled before raise_for_status")

        def json(self):
            return {}

    monkeypatch.setattr("app.ai.artemox.httpx.post", lambda *args, **kwargs: FakeResponse())
    client = ArtemoxClient(api_key="unit-test", base_url="https://api.example/v1")

    with pytest.raises(RuntimeError, match="Artemox auth failed"):
        client.chat_completion({"model": "test", "messages": []})


def test_artemox_client_retries_transient_network_error(monkeypatch):
    monkeypatch.setattr(settings, "external_http_retries", 2)
    monkeypatch.setattr(settings, "external_http_retry_seconds", 0)
    calls = {"count": 0}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "{\"segments\": []}"}}]}

    def fake_post(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise httpx.ConnectError("temporary network failure")
        return FakeResponse()

    monkeypatch.setattr("app.ai.artemox.httpx.post", fake_post)
    client = ArtemoxClient(api_key="unit-test", base_url="https://api.example/v1")

    response = client.chat_completion({"model": "test", "messages": []})

    assert response["choices"]
    assert calls["count"] == 2
