import os
from pathlib import Path

import pytest

from app.ai.gemini import GeminiClient, GeminiVideoAnalyzer
from app.settings import settings


@pytest.mark.skipif(
    not os.getenv("VVF_RUN_REAL_GEMINI_TESTS") or not os.getenv("GEMINI_API_KEY"),
    reason="set VVF_RUN_REAL_GEMINI_TESTS=1 and GEMINI_API_KEY to run Gemini Files API smoke test",
)
def test_gemini_files_api_upload_and_generate_smoke(monkeypatch):
    source_path = Path(
        os.getenv(
            "VVF_GEMINI_LIVE_SOURCE",
            Path(__file__).resolve().parents[1] / "tests data" / "overlay.webm",
        )
    )
    if not source_path.exists():
        pytest.skip("Gemini live source fixture is not available")
    monkeypatch.setattr(settings, "gemini_file_poll_seconds", 2)
    monkeypatch.setattr(settings, "gemini_file_timeout_seconds", 180)
    client = GeminiClient()
    file_info = client.upload_file(source_path, "video/webm")
    file_info = client.wait_file_active(file_info)
    response = client.generate_content(
        os.getenv("GEMINI_VIDEO_MODEL", "gemini-3.1-flash-lite"),
        {
            "contents": [
                {
                    "parts": [
                        {
                            "file_data": {
                                "mime_type": file_info.get("mimeType") or "video/webm",
                                "file_uri": file_info["uri"],
                            }
                        },
                        {"text": "Reply with exactly: test"},
                    ]
                }
            ]
        },
    )
    assert response["candidates"]


@pytest.mark.skipif(
    not os.getenv("VVF_RUN_REAL_GEMINI_ANALYSIS_TESTS") or not os.getenv("GEMINI_API_KEY"),
    reason="set VVF_RUN_REAL_GEMINI_ANALYSIS_TESTS=1 and GEMINI_API_KEY to run Gemini analyzer smoke test",
)
def test_gemini_video_analyzer_live_structured_smoke(monkeypatch):
    source_path = Path(
        os.getenv(
            "VVF_GEMINI_LIVE_SOURCE",
            Path(__file__).resolve().parents[1] / "tests data" / "overlay.webm",
        )
    )
    if not source_path.exists():
        pytest.skip("Gemini live source fixture is not available")
    monkeypatch.setattr(settings, "gemini_file_poll_seconds", 2)
    monkeypatch.setattr(settings, "gemini_file_timeout_seconds", 180)
    result = GeminiVideoAnalyzer().analyze(
        {
            "source_type": "upload",
            "original_filename": source_path.name,
            "local_path": str(source_path),
            "duration_sec": 6,
            "width": 1600,
            "height": 480,
            "fps": 1,
        },
        (
            "Return exactly one candidate segment for this test file. "
            "Use start_sec=0 and end_sec=5 if the visual content is too simple."
        ),
        os.getenv("GEMINI_VIDEO_MODEL", "gemini-3.1-flash-lite"),
    )
    assert result.segments
    assert result.response["gemini_file"]["uri"]
