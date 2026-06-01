import json
from pathlib import Path

import pytest

from app.settings import settings
from app.subtitles.ass import ass_color, ass_timestamp, write_ass_subtitles
from app.subtitles.contracts import SubtitleResult, SubtitleWord
from app.subtitles.gemini import GeminiSubtitleProvider, build_gemini_subtitle_payload
from app.subtitles.mock import MockSubtitleProvider
from app.subtitles.registry import subtitle_model_for_profile


def test_ass_renderer_writes_karaoke_dialogues(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "subtitle_dir", tmp_path)
    result = SubtitleResult(
        text="one two three four five six",
        language="en",
        duration=3,
        words=[
            SubtitleWord("one", 0.0, 0.4),
            SubtitleWord("two", 0.4, 0.8),
            SubtitleWord("three", 0.8, 1.2),
            SubtitleWord("four", 1.2, 1.6),
            SubtitleWord("five", 1.6, 2.0),
            SubtitleWord("six", 2.0, 2.4),
        ],
    )

    path = write_ass_subtitles(
        result,
        {
            "font_family": "Arial",
            "font_size": 48,
            "primary_color": "#FFFFFF",
            "active_word_color": "#FACC15",
            "outline_color": "#111827",
            "back_color": "#000000",
            "alignment": 2,
            "margin_v": 120,
            "max_words_per_line": 3,
            "uppercase": True,
        },
        width=180,
        height=320,
    )

    text = path.read_text(encoding="utf-8")
    assert "[Events]" in text
    assert "Dialogue: 0,0:00:00.00,0:00:00.40,Karaoke" in text
    assert r"\N" in text
    assert r"{\c&H0015CCFA&}ONE" in text
    assert "PlayResX: 180" in text


def test_ass_helpers_convert_color_and_timestamp():
    assert ass_color("#112233") == "&H00332211&"
    assert ass_timestamp(61.239) == "0:01:01.24"


def test_mock_subtitle_provider_returns_word_level_timestamps(tmp_path):
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"not a real wav")

    result = MockSubtitleProvider().transcribe(audio_path, {"language": "ru"}, "mock")

    assert result.language == "ru"
    assert result.words
    assert result.transcript_json()["words"][0]["word"] == "mock"


def test_gemini_subtitle_payload_uses_files_api_and_json_schema():
    payload = build_gemini_subtitle_payload(
        {"uri": "https://file-uri", "mimeType": "audio/wav"},
        {"language": "ru"},
    )

    parts = payload["contents"][0]["parts"]
    assert parts[0]["file_data"]["file_uri"] == "https://file-uri"
    assert parts[0]["file_data"]["mime_type"] == "audio/wav"
    assert payload["generationConfig"]["responseMimeType"] == "application/json"
    assert payload["generationConfig"]["responseJsonSchema"]["required"][-1] == "words"


def test_gemini_subtitle_provider_uploads_waits_and_parses_response(tmp_path):
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"wav")

    class FakeClient:
        def upload_file(self, path: Path, mime_type: str):
            assert path == audio_path
            assert mime_type == "audio/wav"
            return {"name": "files/audio", "uri": "https://file-uri", "mimeType": "audio/wav"}

        def wait_file_active(self, file_info):
            assert file_info["name"] == "files/audio"
            return {**file_info, "state": "ACTIVE"}

        def generate_content(self, model, payload):
            assert model == "gemini-2.5-flash"
            assert payload["contents"][0]["parts"][0]["file_data"]["file_uri"] == "https://file-uri"
            return {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": json.dumps(
                                        {
                                            "text": "hello world",
                                            "language": "en",
                                            "duration": 1.0,
                                            "segments": [{"start": 0, "end": 1, "text": "hello world"}],
                                            "words": [
                                                {"word": "hello", "start": 0, "end": 0.5},
                                                {"word": "world", "start": 0.5, "end": 1.0},
                                            ],
                                        }
                                    )
                                }
                            ]
                        }
                    }
                ],
                "usageMetadata": {"totalTokenCount": 42},
            }

    result = GeminiSubtitleProvider(FakeClient()).transcribe(
        audio_path,
        {"language": "en"},
        "gemini-2.5-flash",
    )

    assert result.words[1].word == "world"
    assert result.usage == {"totalTokenCount": 42}


def test_gemini_subtitle_provider_fails_without_words(tmp_path):
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"wav")

    class FakeClient:
        def upload_file(self, path: Path, mime_type: str):
            return {"name": "files/audio", "uri": "https://file-uri", "mimeType": "audio/wav"}

        def wait_file_active(self, file_info):
            return file_info

        def generate_content(self, model, payload):
            return {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": json.dumps(
                                        {
                                            "text": "hello",
                                            "language": "en",
                                            "duration": 1.0,
                                            "segments": [{"start": 0, "end": 1, "text": "hello"}],
                                            "words": [],
                                        }
                                    )
                                }
                            ]
                        }
                    }
                ]
            }

    with pytest.raises(RuntimeError, match="no word-level timestamps"):
        GeminiSubtitleProvider(FakeClient()).transcribe(audio_path, {"language": "en"}, "gemini-2.5-flash")


def test_gemini_profile_uses_configured_transcribe_model(monkeypatch):
    monkeypatch.setattr(settings, "gemini_transcribe_model", "gemini-2.5-flash")

    assert (
        subtitle_model_for_profile({"provider": "gemini", "model": "openai/gpt-4o-transcribe"})
        == "gemini-2.5-flash"
    )


def test_default_gemini_transcribe_model_uses_3_1_flash_lite():
    assert settings.gemini_transcribe_model == "gemini-3.1-flash-lite"
    assert (
        subtitle_model_for_profile({"provider": "gemini", "model": "openai/gpt-4o-transcribe"})
        == "gemini-3.1-flash-lite"
    )
