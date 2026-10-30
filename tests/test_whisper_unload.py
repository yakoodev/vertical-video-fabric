"""Модель whisper выгружается после простоя — память машины небольшая."""

import time

from app.settings import settings
from app.subtitles.whisper_local import WhisperSubtitleProvider


def test_model_is_unloaded_after_idle(monkeypatch):
    monkeypatch.setattr(settings, "whisper_idle_unload_sec", 0.2)
    WhisperSubtitleProvider._models.clear()
    loads = []
    provider = WhisperSubtitleProvider(model_loader=lambda size: loads.append(size) or object())
    provider._get_model("small")
    provider._get_model("small")
    assert loads == ["small"] and WhisperSubtitleProvider._models
    # Простой считается от конца распознавания (transcribe ставит таймер в finally).
    provider._schedule_unload()
    time.sleep(0.5)
    assert not WhisperSubtitleProvider._models
    provider._get_model("small")
    assert loads == ["small", "small"]
    WhisperSubtitleProvider._models.clear()
