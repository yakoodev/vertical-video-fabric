import json
from pathlib import Path

import pytest

from app.settings import settings
from app.subtitles.ass import ass_color, ass_timestamp, write_ass_subtitles
from app.subtitles.contracts import SubtitleResult, SubtitleWord
from app.subtitles.gemini import GeminiSubtitleProvider, build_gemini_subtitle_payload
from app.subtitles.mock import MockSubtitleProvider
from app.subtitles.registry import subtitle_model_for_profile
from app.subtitles.timing import normalize_subtitle_timeline, shift_subtitle_timeline


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
    first_dialogue = next(line for line in text.splitlines() if line.startswith("Dialogue:"))
    # The active word is shown in the active colour; later words are kept in the
    # text (fixed layout) but fully transparent until they are spoken.
    assert r"{\c&H0015CCFA&\alpha&H00&}ONE" in first_dialogue
    assert r"{\c&H00FFFFFF&\alpha&HFF&}TWO" in first_dialogue
    assert "PlayResX: 180" in text
    assert "WrapStyle: 2" in text


def test_ass_renderer_keeps_layout_fixed_while_highlight_moves(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "subtitle_dir", tmp_path)
    result = SubtitleResult(
        text="alpha beta gamma",
        language="en",
        duration=3,
        words=[
            SubtitleWord("alpha", 0.0, 0.4),
            SubtitleWord("beta", 0.4, 0.8),
            SubtitleWord("gamma", 0.8, 1.2),
        ],
    )

    path = write_ass_subtitles(
        result,
        {"primary_color": "#FFFFFF", "active_word_color": "#FACC15", "max_words_per_line": 5},
        width=180,
        height=320,
    )

    text = path.read_text(encoding="utf-8")
    dialogues = [line for line in text.splitlines() if line.startswith("Dialogue:")]
    assert len(dialogues) == 3
    # Every event keeps all three word slots (fixed layout); only the active word
    # and which words are visible vs transparent changes.
    for dialogue in dialogues:
        body = dialogue.split(",,", 1)[1]
        words = [chunk.split("}")[-1].strip() for chunk in body.split(r"{\c")[1:]]
        assert words == ["alpha", "beta", "gamma"]
    assert r"{\c&H0015CCFA&\alpha&H00&}alpha" in dialogues[0]
    assert r"{\c&H0015CCFA&\alpha&H00&}beta" in dialogues[1]
    assert r"{\c&H0015CCFA&\alpha&H00&}gamma" in dialogues[2]
    # In the first event the later words are present but transparent.
    assert r"\alpha&HFF&}beta" in dialogues[0]
    assert r"\alpha&HFF&}gamma" in dialogues[0]


def test_ass_helpers_convert_color_and_timestamp():
    assert ass_color("#112233") == "&H00332211&"
    assert ass_timestamp(61.239) == "0:01:01.24"


def test_ass_renderer_splits_phrases_on_long_pause(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "subtitle_dir", tmp_path)
    result = SubtitleResult(
        text="phrase pause",
        language="en",
        duration=3,
        words=[
            SubtitleWord("phrase", 0.0, 0.4),
            SubtitleWord("pause", 2.0, 2.4),
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
        },
        width=180,
        height=320,
    )

    text = path.read_text(encoding="utf-8")
    assert "Dialogue: 0,0:00:00.00,0:00:00.60,Karaoke" in text
    assert "Dialogue: 0,0:00:00.00,0:00:02.00,Karaoke" not in text
    assert r"{\alpha&HFF&}" not in text
    assert "pause" not in text.split("Dialogue: 0,0:00:00.00,0:00:00.60,Karaoke", 1)[1].splitlines()[0]


def test_ass_renderer_does_not_hold_last_page_word_over_next_page(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "subtitle_dir", tmp_path)
    result = SubtitleResult(
        text="one two three four",
        language="en",
        duration=2,
        words=[
            SubtitleWord("one", 0.0, 0.2),
            SubtitleWord("two", 0.2, 0.4),
            SubtitleWord("three", 0.4, 0.6),
            SubtitleWord("four", 0.6, 0.8),
        ],
    )

    path = write_ass_subtitles(result, {"max_words_per_line": 3}, width=180, height=320)

    text = path.read_text(encoding="utf-8")
    assert "Dialogue: 0,0:00:00.40,0:00:00.60,Karaoke" in text
    assert "Dialogue: 0,0:00:00.40,0:00:00.80,Karaoke" not in text
    assert "Dialogue: 0,0:00:00.60,0:00:01.00,Karaoke" in text


def test_subtitle_timeline_normalization_compresses_out_of_range_gaps():
    result = SubtitleResult(
        text="one two three",
        language="en",
        duration=10,
        words=[
            SubtitleWord("one", 0.0, 0.5),
            SubtitleWord("two", 20.0, 20.5),
            SubtitleWord("three", 30.0, 31.0),
        ],
    )

    normalized = normalize_subtitle_timeline(result, 10)

    assert normalized.duration == 10
    assert normalized.words[-1].end <= 10
    assert normalized.words[1].start < 20
    assert normalized.usage["subtitleTimelineNormalizedToSec"] == 10


def test_subtitle_timeline_shift_delays_words_and_segments():
    result = SubtitleResult(
        text="one two",
        language="en",
        duration=2,
        segments=[],
        words=[
            SubtitleWord("one", 0.0, 0.4),
            SubtitleWord("two", 1.0, 1.3),
        ],
    )

    shifted = shift_subtitle_timeline(result, 0.35, 2)

    assert shifted.duration == 2
    assert shifted.words[0].start == 0.35
    assert shifted.words[0].end == 0.75
    assert shifted.words[1].start == 1.35
    assert shifted.segments[0].start == 0.35
    assert shifted.segments[0].end == 1.65
    assert shifted.usage["subtitleTimingOffsetSec"] == 0.35


def test_ass_renderer_caps_dialogue_end_to_result_duration(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "subtitle_dir", tmp_path)
    result = SubtitleResult(
        text="last",
        language="en",
        duration=1.0,
        words=[SubtitleWord("last", 0.8, 1.0)],
    )

    path = write_ass_subtitles(result, {"max_words_per_line": 3}, width=180, height=320)

    text = path.read_text(encoding="utf-8")
    assert "Dialogue: 0,0:00:00.80,0:00:01.00,Karaoke" in text
    assert "0:00:01.35" not in text


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
    # A generous output cap keeps long word-level transcripts from truncating.
    assert payload["generationConfig"]["maxOutputTokens"] >= 32768
    assert "do not anticipate speech" in parts[1]["text"].lower()


def test_gemini_subtitle_recovers_from_truncated_json(tmp_path):
    # Gemini occasionally truncates the words[] array (hitting the output token
    # limit), producing invalid JSON. The provider must salvage the complete
    # words instead of failing the whole render.
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"wav")
    truncated = (
        '{"text":"привет мир","language":"ru","duration":3,'
        '"segments":[{"start":0,"end":3,"text":"привет мир"}],'
        '"words":[{"word":"привет","start":0.0,"end":0.5},'
        '{"word":"мир","start":0.6,"end":1.1},{"word":"тест'
    )

    class FakeClient:
        def upload_file(self, path: Path, mime_type: str):
            return {"name": "files/audio", "uri": "https://file-uri", "mimeType": "audio/wav"}

        def wait_file_active(self, file_info):
            return file_info

        def generate_content(self, model, payload):
            return {"candidates": [{"content": {"parts": [{"text": truncated}]}}]}

    result = GeminiSubtitleProvider(FakeClient()).transcribe(audio_path, {"language": "ru"}, "gemini-2.5-flash")

    assert [word.word for word in result.words] == ["привет", "мир"]
    assert result.words[0].start == 0.0
    assert result.words[1].end == 1.1


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
    assert result.usage == {
        "totalTokenCount": 42,
        "requestedModel": "gemini-2.5-flash",
        "model": "gemini-2.5-flash",
    }


def test_gemini_subtitle_provider_falls_back_on_high_demand(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "gemini_transcribe_fallback_models", ["gemini-2.5-flash"])
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"wav")
    calls = []

    class FakeClient:
        def upload_file(self, path: Path, mime_type: str):
            return {"name": "files/audio", "uri": "https://file-uri", "mimeType": "audio/wav"}

        def wait_file_active(self, file_info):
            return file_info

        def generate_content(self, model, payload):
            calls.append(model)
            if model == "gemini-3.1-flash-lite":
                raise RuntimeError(
                    "Gemini generateContent failed: This model is currently experiencing high demand. "
                    "Please try again later."
                )
            return {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": json.dumps(
                                        {
                                            "text": "fallback works",
                                            "language": "en",
                                            "duration": 1.0,
                                            "segments": [{"start": 0, "end": 1, "text": "fallback works"}],
                                            "words": [
                                                {"word": "fallback", "start": 0, "end": 0.5},
                                                {"word": "works", "start": 0.5, "end": 1.0},
                                            ],
                                        }
                                    )
                                }
                            ]
                        }
                    }
                ],
                "usageMetadata": {"totalTokenCount": 7},
            }

    result = GeminiSubtitleProvider(FakeClient()).transcribe(
        audio_path,
        {"language": "en"},
        "gemini-3.1-flash-lite",
    )

    assert calls == ["gemini-3.1-flash-lite", "gemini-2.5-flash"]
    assert result.usage["requestedModel"] == "gemini-3.1-flash-lite"
    assert result.usage["model"] == "gemini-2.5-flash"
    assert "high demand" in result.usage["fallbackErrors"][0]


def test_gemini_subtitle_provider_falls_back_from_unavailable_model(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "gemini_transcribe_fallback_models", ["gemini-2.5-flash-lite"])
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"wav")
    calls = []

    class FakeClient:
        def upload_file(self, path: Path, mime_type: str):
            return {"name": "files/audio", "uri": "https://file-uri", "mimeType": "audio/wav"}

        def wait_file_active(self, file_info):
            return file_info

        def generate_content(self, model, payload):
            calls.append(model)
            if model == "gemini-2.0-flash-lite":
                raise RuntimeError(
                    "Gemini generateContent failed: This model models/gemini-2.0-flash-lite is no longer available."
                )
            return {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": json.dumps(
                                        {
                                            "text": "fallback model",
                                            "language": "en",
                                            "duration": 1.0,
                                            "segments": [{"start": 0, "end": 1, "text": "fallback model"}],
                                            "words": [
                                                {"word": "fallback", "start": 0, "end": 0.5},
                                                {"word": "model", "start": 0.5, "end": 1.0},
                                            ],
                                        }
                                    )
                                }
                            ]
                        }
                    }
                ],
            }

    result = GeminiSubtitleProvider(FakeClient()).transcribe(
        audio_path,
        {"language": "en"},
        "gemini-2.0-flash-lite",
    )

    assert calls == ["gemini-2.0-flash-lite", "gemini-2.5-flash-lite"]
    assert result.usage["model"] == "gemini-2.5-flash-lite"
    assert "no longer available" in result.usage["fallbackErrors"][0]


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


def test_whisper_provider_builds_word_timestamps(tmp_path):
    from app.subtitles.whisper_local import WhisperSubtitleProvider

    class FakeWord:
        def __init__(self, word, start, end):
            self.word, self.start, self.end = word, start, end

    class FakeSegment:
        def __init__(self, start, end, text, words):
            self.start, self.end, self.text, self.words = start, end, text, words

    class FakeInfo:
        language = "ru"
        language_probability = 0.98

    class FakeModel:
        def transcribe(self, audio, **kwargs):
            assert kwargs["word_timestamps"] is True
            assert kwargs["vad_filter"] is True
            return (
                iter(
                    [
                        FakeSegment(1.2, 2.0, "Привет мир", [FakeWord(" Привет", 1.2, 1.7), FakeWord(" мир", 1.75, 2.0)]),
                    ]
                ),
                FakeInfo(),
            )

    audio = tmp_path / "a.wav"
    audio.write_bytes(b"wav")
    provider = WhisperSubtitleProvider(model_loader=lambda size: FakeModel())
    result = provider.transcribe(audio, {"language": "ru"}, "fake-size")

    assert [word.word for word in result.words] == ["Привет", "мир"]
    # Whisper anchors the first word at its real onset (1.2s), not at 0.
    assert result.words[0].start == 1.2
    assert result.words[-1].end == 2.0
    assert result.language == "ru"


def test_whisper_profile_ignores_leftover_llm_model_name():
    from app.subtitles.registry import get_subtitle_provider, subtitle_model_for_profile
    from app.subtitles.whisper_local import WhisperSubtitleProvider

    assert isinstance(get_subtitle_provider("whisper"), WhisperSubtitleProvider)
    # A profile carrying a Gemini model id must not be passed to WhisperModel.
    assert subtitle_model_for_profile({"provider": "whisper", "model": "gemini-3.5-flash"}) == settings.whisper_model_size
    assert subtitle_model_for_profile({"provider": "whisper", "model": "medium"}) == "medium"


def test_gemini_profile_uses_configured_transcribe_model(monkeypatch):
    monkeypatch.setattr(settings, "gemini_transcribe_model", "gemini-2.5-flash")

    assert (
        subtitle_model_for_profile({"provider": "gemini", "model": "openai/gpt-4o-transcribe"})
        == "gemini-2.5-flash"
    )


def test_default_gemini_transcribe_model_uses_3_1_flash_lite():
    assert settings.gemini_transcribe_model == "gemini-3.1-flash-lite"
    assert "gemini-2.5-flash" in settings.gemini_transcribe_fallback_models
    assert "gemini-2.5-flash-lite" in settings.gemini_transcribe_fallback_models
    assert "gemini-2.0-flash-lite" not in settings.gemini_transcribe_fallback_models
    assert (
        subtitle_model_for_profile({"provider": "gemini", "model": "openai/gpt-4o-transcribe"})
        == "gemini-3.1-flash-lite"
    )
