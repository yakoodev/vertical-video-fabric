from __future__ import annotations

from pathlib import Path

from app.cookies import CookieRecord, tiktok_caption
from app.providers.base import Provider, ProviderResult, safe_provider_error


class InstagramProvider(Provider):
    """Reels upload via instagrapi (unofficial private API), authenticated by the
    account's ``sessionid`` cookie. instagrapi is imported lazily so the app and
    tests load without the (heavy) dependency installed."""

    platform = "instagram"

    def upload(
        self,
        *,
        cookies: list[CookieRecord],
        file_path: Path,
        title: str,
        description: str,
        privacy: str,
        allow_comments: bool,
        account_label: str,
        proxy_url: str,
    ) -> ProviderResult:
        sessionid = _cookie_value(cookies, "sessionid")
        if not sessionid:
            return ProviderResult(status="needs_reauth", error="Instagram sessionid cookie is missing")
        try:
            from instagrapi import Client  # noqa: PLC0415 - heavy optional dependency
        except ImportError:
            return ProviderResult(status="failed", error="instagrapi is not installed")

        try:
            client = Client()
            if proxy_url:
                client.set_proxy(proxy_url)
            client.login_by_sessionid(sessionid)
            caption = tiktok_caption(title, description)
            media = client.clip_upload(Path(file_path), caption)
        except Exception as exc:  # noqa: BLE001 - provider must report failures to the queue
            message = str(exc)
            return ProviderResult(status=_classify_instagram_error(message), error=_safe_error(message))

        code = getattr(media, "code", "") or ""
        remote_id = str(getattr(media, "pk", "") or code)
        return ProviderResult(
            status="succeeded",
            remote_id=remote_id,
            remote_url=f"https://www.instagram.com/reel/{code}/" if code else "",
            response={"code": code, "pk": remote_id},
        )


def _cookie_value(cookies: list[CookieRecord], name: str) -> str:
    return next((c.value for c in cookies if c.name == name and c.value), "")


def _classify_instagram_error(message: str) -> str:
    lowered = message.lower()
    auth_markers = (
        "login_required",
        "challenge_required",
        "checkpoint",
        "feedback_required",
        "sessionid",
        "session",
        "401",
        "403",
        "two-factor",
        "bad password",
    )
    if any(marker in lowered for marker in auth_markers):
        return "needs_reauth"
    return "failed"


def _safe_error(message: str) -> str:
    return safe_provider_error(message, "instagram upload failed")
