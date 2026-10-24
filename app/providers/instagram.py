from __future__ import annotations

from pathlib import Path

from app.cookies import CookieRecord, tiktok_caption
from app.providers.base import Provider, ProviderResult, safe_provider_error
from app.publish_format import publish_plan


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
        video_format: str = "9:16",
        thumbnail_path: str = "",
    ) -> ProviderResult:
        # 9:16 — Reels, как всегда; 1:1/4:5 — пост в ленту (её формат); 16:9 — тоже в ленту,
        # с предупреждением: Reels только вертикальные (app/publish_format.py).
        plan = publish_plan(self.platform, video_format)
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
            if plan["publish_as"] == "feed":
                media = client.video_upload(Path(file_path), caption)
            else:
                media = client.clip_upload(Path(file_path), caption)
        except Exception as exc:  # noqa: BLE001 - provider must report failures to the queue
            message = str(exc)
            return ProviderResult(status=_classify_instagram_error(message), error=_safe_error(message))

        code = getattr(media, "code", "") or ""
        remote_id = str(getattr(media, "pk", "") or code)
        response = {"code": code, "pk": remote_id}
        if plan["publish_as"] == "feed":
            response.update({"format": video_format, "publish_as": "feed"})
            if plan["warning"]:
                response["format_warning"] = plan["warning"]
        kind = "p" if plan["publish_as"] == "feed" else "reel"
        return ProviderResult(
            status="succeeded",
            remote_id=remote_id,
            remote_url=f"https://www.instagram.com/{kind}/{code}/" if code else "",
            response=response,
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


# --- статистика опубликованного рилса (app/platform_stats.py) --------------- #


class InstagramUnavailable(Exception):
    """Числа Instagram взять нечем (нет сессии / instagrapi) — пропуск, не ошибка площадки."""


def session_client(cookies: list[CookieRecord], proxy_url: str = ""):
    """Клиент instagrapi по cookie ``sessionid`` аккаунта — тем же входом, что и загрузка.
    Без логина Instagram чисел не отдаёт, поэтому без сессии — пропуск."""
    sessionid = _cookie_value(cookies, "sessionid")
    if not sessionid:
        raise InstagramUnavailable("у аккаунта Instagram нет cookie sessionid — статистику взять нечем")
    try:
        from instagrapi import Client  # noqa: PLC0415 - heavy optional dependency
    except ImportError as exc:
        raise InstagramUnavailable("instagrapi не установлен — статистика Instagram недоступна") from exc
    client = Client()
    if proxy_url:
        client.set_proxy(proxy_url)
    client.login_by_sessionid(sessionid)
    return client


def media_stats(client, remote_id: str, remote_url: str = "") -> dict:
    """Просмотры/лайки/комментарии рилса через media_info."""
    pk = str(remote_id or "").strip()
    if not pk.isdigit():
        code = pk or remote_url.rstrip("/").rsplit("/", 1)[-1]
        pk = str(client.media_pk_from_code(code))
    media = client.media_info(pk)
    views = getattr(media, "play_count", None) or getattr(media, "view_count", None)
    return {
        "views": views,
        "likes": getattr(media, "like_count", None),
        "comments": getattr(media, "comment_count", None),
    }
