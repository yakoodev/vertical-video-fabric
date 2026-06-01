import os

import pytest

from app.ai.artemox import ArtemoxClient


@pytest.mark.skipif(
    not os.getenv("VVF_RUN_REAL_AI_TESTS") or not os.getenv("ARTEMOX_API_KEY"),
    reason="set VVF_RUN_REAL_AI_TESTS=1 and ARTEMOX_API_KEY to run Artemox live smoke test",
)
def test_artemox_live_chat_completion_smoke():
    response = ArtemoxClient().chat_completion(
        {
            "model": os.getenv("ARTEMOX_VIDEO_MODEL", "gemini-2.0-flash-lite"),
            "messages": [{"role": "user", "content": "Say test."}],
        }
    )
    assert response["choices"]
