import os
import subprocess
from pathlib import Path

import pytest

from app.settings import settings
from app.subtitles.gemini import GeminiSubtitleProvider


@pytest.mark.skipif(
    not os.getenv("VVF_RUN_REAL_GEMINI_SUBTITLE_TESTS") or not os.getenv("GEMINI_API_KEY"),
    reason="set VVF_RUN_REAL_GEMINI_SUBTITLE_TESTS=1 and GEMINI_API_KEY to run Gemini subtitle smoke test",
)
def test_gemini_subtitle_provider_live_smoke(tmp_path, monkeypatch):
    source_path = Path(
        os.getenv(
            "VVF_GEMINI_SUBTITLE_LIVE_SOURCE",
            Path(__file__).resolve().parents[1] / "tests data" / "Apex tests video 1.mp4",
        )
    )
    if not source_path.exists():
        pytest.skip("Gemini subtitle live source fixture is not available")
    audio_path = tmp_path / "speech.wav"
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                os.getenv("VVF_GEMINI_SUBTITLE_LIVE_OFFSET", "0"),
                "-t",
                os.getenv("VVF_GEMINI_SUBTITLE_LIVE_DURATION", "12"),
                "-i",
                str(source_path),
                "-map",
                "0:a:0",
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(audio_path),
            ],
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        pytest.skip("live fixture has no extractable audio or ffmpeg is unavailable")
    monkeypatch.setattr(settings, "gemini_file_poll_seconds", 2)
    monkeypatch.setattr(settings, "gemini_file_timeout_seconds", 180)

    result = GeminiSubtitleProvider().transcribe(
        audio_path,
        {"language": os.getenv("VVF_GEMINI_SUBTITLE_LIVE_LANGUAGE", "")},
        os.getenv("GEMINI_TRANSCRIBE_MODEL", "gemini-3.1-flash-lite"),
    )

    assert result.words
    assert result.response["gemini_file"]["uri"]
