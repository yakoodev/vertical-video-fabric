from app.providers.tiktok import _classify_tiktok_error
from app.providers.youtube import _classify_youtube_error


def test_auth_error_classification():
    assert _classify_youtube_error("401 Unauthorized") == "needs_reauth"
    assert _classify_youtube_error("network down") == "failed"
    assert _classify_tiktok_error("captcha verify required") == "needs_reauth"
    assert _classify_tiktok_error("rate limited") == "failed"

