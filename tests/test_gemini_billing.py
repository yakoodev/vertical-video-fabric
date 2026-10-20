"""Кончились кредиты Gemini — сразу понятная ошибка, без повторов с паузами."""

import httpx
import pytest

from app.ai import gemini


def _resp(status, message):
    return httpx.Response(status, json={"error": {"message": message}}, request=httpx.Request("POST", "https://x"))


def test_depleted_credits_raise_clear_error_without_retries(monkeypatch):
    calls = []
    monkeypatch.setattr(gemini, "_request_once", lambda m, u, k: calls.append(1) or _resp(
        429, "Your prepayment credits are depleted. Please go to AI Studio"))
    monkeypatch.setattr(gemini.time, "sleep", lambda s: None)
    response = gemini._request_with_retries("POST", "https://x")
    assert len(calls) == 1
    with pytest.raises(gemini.GeminiBillingError, match="кредиты Gemini"):
        gemini._raise_gemini_status(response, "Gemini generateContent failed")


def test_overload_429_is_still_retried(monkeypatch):
    calls = []
    monkeypatch.setattr(gemini, "_request_once", lambda m, u, k: calls.append(1) or _resp(429, "high demand"))
    monkeypatch.setattr(gemini.time, "sleep", lambda s: None)
    monkeypatch.setattr(gemini.settings, "gemini_http_retries", 3)
    gemini._request_with_retries("POST", "https://x")
    assert len(calls) == 3
