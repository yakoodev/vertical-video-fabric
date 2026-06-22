import os
from pathlib import Path

import pytest

from app.ai.gemini import GeminiClient
from app.settings import settings


@pytest.mark.skipif(
    not os.getenv("VVF_RUN_REAL_GEMINI_LARGE_UPLOAD_TESTS") or not os.getenv("GEMINI_API_KEY"),
    reason="set VVF_RUN_REAL_GEMINI_LARGE_UPLOAD_TESTS=1 and GEMINI_API_KEY to upload a large video to Gemini Files API",
)
def test_gemini_large_file_upload_smoke(monkeypatch):
    source_path = Path(
        os.getenv(
            "VVF_GEMINI_LARGE_SOURCE",
            Path(__file__).resolve().parents[1] / "tests data" / "Apex tests video 1.mp4",
        )
    )
    if not source_path.exists():
        pytest.skip("large Gemini source fixture is not available")
    monkeypatch.setattr(settings, "gemini_file_poll_seconds", 5)
    monkeypatch.setattr(settings, "gemini_file_timeout_seconds", 900)

    file_info = GeminiClient(timeout=900).upload_file(source_path, "video/mp4")
    file_info = GeminiClient(timeout=900).wait_file_active(file_info)

    assert file_info["uri"]
    assert file_info.get("sizeBytes") or source_path.stat().st_size > 100 * 1024 * 1024
