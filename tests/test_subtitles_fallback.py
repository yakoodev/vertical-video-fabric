"""Облачное распознавание упало (кончились кредиты Gemini) — субтитры делает локальный whisper."""

from app.subtitles.contracts import SubtitleResult, SubtitleSegment, SubtitleWord
from test_api_pipeline import _require_ffmpeg
from test_clip_spec import _setup


def test_cloud_transcription_failure_falls_back_to_whisper(tmp_path, monkeypatch):
    _require_ffmpeg()
    from app import clip_subtitles

    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    if not store.list_subtitle_profiles():
        store.create_subtitle_profile("Тест", provider="gemini", model="gemini-2.5-flash", language="ru")
    used = []

    class Cloud:
        def transcribe(self, audio, profile, model):
            used.append("gemini")
            raise RuntimeError("Закончились кредиты Gemini")

    class Local:
        def transcribe(self, audio, profile, model):
            used.append("whisper")
            return SubtitleResult(text="привет", language="ru", duration=0.6,
                                  segments=[SubtitleSegment(start=0.2, end=0.6, text="привет")],
                                  words=[SubtitleWord(word="привет", start=0.2, end=0.6)])

    monkeypatch.setattr(clip_subtitles, "get_subtitle_provider",
                        lambda name=None: Local() if (name or "").lower() == "whisper" else Cloud())
    payload = clip_subtitles.generate_for_plan(store, plan["id"], provider="gemini")
    assert used == ["gemini", "whisper"]
    assert payload["lines"] and payload["lines"][0]["text"] == "привет"
