import json
from pathlib import Path

import pytest

from app.ai.gemini import (
    GeminiClient,
    GeminiVideoAnalyzer,
    build_gemini_analysis_payload,
    gemini_analysis_schema,
)
from app.ai import gemini as gemini_module
from app.settings import settings


def test_gemini_payload_uses_file_data_and_response_format():
    payload = build_gemini_analysis_payload(
        {
            "source_type": "upload",
            "original_filename": "source.mp4",
            "duration_sec": 60,
            "width": 1920,
            "height": 1080,
            "fps": 30,
        },
        "Find clips",
        {"uri": "https://generativelanguage.googleapis.com/v1beta/files/abc", "mimeType": "video/mp4"},
        "video/mp4",
    )

    parts = payload["contents"][0]["parts"]
    assert parts[0]["file_data"]["mime_type"] == "video/mp4"
    assert parts[0]["file_data"]["file_uri"].endswith("/files/abc")
    assert "A clip is the final video plan" in parts[1]["text"]
    assert "multiple segments" in parts[1]["text"]
    assert "Every start_sec and end_sec must be between 0 and 60.000" in parts[1]["text"]
    assert "Each individual fiction segment must be 12 to 75 seconds" in parts[1]["text"]
    assert "clips[0] must be an Episode Story Recap" in parts[1]["text"]
    assert "Do not tile the episode into consecutive timeline slices" in parts[1]["text"]
    assert "in Russian" in parts[1]["text"]
    assert payload["generationConfig"]["responseMimeType"] == "application/json"
    assert payload["generationConfig"]["responseJsonSchema"]["required"] == ["clips"]


def test_gemini_schema_has_property_ordering_for_2_0_models():
    schema = gemini_analysis_schema()

    assert schema["propertyOrdering"] == ["clips"]
    assert schema["properties"]["clips"]["items"]["propertyOrdering"][0] == "title"
    segment_schema = schema["properties"]["clips"]["items"]["properties"]["segments"]["items"]
    assert segment_schema["propertyOrdering"][0] == "start_sec"


def test_gemini_video_analyzer_uploads_waits_and_parses_response(tmp_path):
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"video")

    class FakeClient:
        def upload_file(self, path: Path, mime_type: str):
            assert path == source_path
            assert mime_type == "video/mp4"
            return {"name": "files/abc", "uri": "https://file-uri", "mimeType": "video/mp4", "state": "PROCESSING"}

        def wait_file_active(self, file_info):
            assert file_info["name"] == "files/abc"
            return {**file_info, "state": "ACTIVE"}

        def generate_content(self, model, payload):
            assert model == "gemini-2.5-pro"
            assert payload["contents"][0]["parts"][0]["file_data"]["file_uri"] == "https://file-uri"
            return {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": json.dumps(
                                        {
                                            "segments": [
                                                {
                                                    "start_sec": 0,
                                                    "end_sec": 10,
                                                    "title": "Gemini hook",
                                                    "description": "Opening",
                                                    "score": 0.92,
                                                    "category": "insight",
                                                    "color": "#2563EB",
                                                    "reason": "Standalone moment",
                                                }
                                            ]
                                        }
                                    )
                                }
                            ]
                        }
                    }
                ],
                "usageMetadata": {"totalTokenCount": 123},
            }

    result = GeminiVideoAnalyzer(FakeClient()).analyze(
        {"local_path": str(source_path), "duration_sec": 60},
        "Find clips",
        "gemini-2.5-pro",
    )

    assert result.segments[0].title == "Gemini hook"
    assert result.usage == {"totalTokenCount": 123}
    assert result.response["gemini_file"]["name"] == "files/abc"


def test_gemini_client_requires_api_key(tmp_path):
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"video")
    client = GeminiClient(api_key="", base_url="https://generativelanguage.googleapis.com/v1beta")

    with pytest.raises(RuntimeError, match="Gemini API key is not configured"):
        client.upload_file(source_path, "video/mp4")


def test_gemini_client_uploads_files_in_chunks(tmp_path, monkeypatch):
    source_path = tmp_path / "source.mp4"
    chunk_size = 256 * 1024
    source_path.write_bytes((b"a" * chunk_size) + (b"b" * chunk_size) + b"c")
    monkeypatch.setattr(settings, "gemini_upload_chunk_bytes", chunk_size)
    chunk_calls = []

    class FakeResponse:
        def __init__(self, status_code=200, headers=None, payload=None):
            self.status_code = status_code
            self.headers = headers or {}
            self._payload = payload or {}

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def fake_request(method, url, **kwargs):
        assert method == "POST"
        assert kwargs["headers"]["X-Goog-Upload-Command"] == "start"
        return FakeResponse(headers={"x-goog-upload-url": "https://upload-url"})

    def fake_post(url, headers=None, content=None, timeout=None):
        chunk_calls.append((int(headers["X-Goog-Upload-Offset"]), headers["X-Goog-Upload-Command"], len(content)))
        if "finalize" in headers["X-Goog-Upload-Command"]:
            return FakeResponse(payload={"file": {"name": "files/abc", "uri": "https://file-uri"}})
        return FakeResponse()

    monkeypatch.setattr(gemini_module.httpx, "request", fake_request)
    monkeypatch.setattr(gemini_module.httpx, "post", fake_post)

    file_info = GeminiClient(api_key="key", base_url="https://generativelanguage.googleapis.com/v1beta").upload_file(
        source_path,
        "video/mp4",
    )

    assert file_info["uri"] == "https://file-uri"
    assert chunk_calls == [
        (0, "upload", chunk_size),
        (chunk_size, "upload", chunk_size),
        (chunk_size * 2, "upload, finalize", 1),
    ]


def test_default_gemini_video_model_uses_3_5_flash():
    from app.ai.service import _default_model

    assert settings.gemini_video_model == "gemini-3.5-flash"
    assert _default_model("gemini") == "gemini-3.5-flash"
