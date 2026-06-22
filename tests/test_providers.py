from app.providers.base import safe_provider_error
from app.providers.tiktok import _classify_tiktok_error
from app.providers.youtube import _classify_youtube_error


def test_auth_error_classification():
    assert _classify_youtube_error("401 Unauthorized") == "needs_reauth"
    assert _classify_youtube_error("network down") == "failed"
    assert _classify_tiktok_error("captcha verify required") == "needs_reauth"
    assert _classify_tiktok_error("rate limited") == "failed"


def test_provider_error_redacts_proxy_credentials():
    message = "failed via http://user:pass@proxy.example:8080 with Cookie: SID=secret"

    safe = safe_provider_error(message, "fallback")

    assert "user:pass" not in safe
    assert "SID=secret" not in safe
    assert "http://***:***@proxy.example:8080" in safe
    assert "Cookie: ***" in safe
