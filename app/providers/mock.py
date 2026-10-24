from __future__ import annotations

from pathlib import Path

from app.cookies import CookieRecord
from app.providers.base import Provider, ProviderResult


class MockProvider(Provider):
    def __init__(self, platform: str) -> None:
        self.platform = platform

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
        if not cookies:
            return ProviderResult(status="needs_reauth", error="mock: missing cookies")
        remote_id = f"mock-{self.platform}-{file_path.stem[:12]}"
        response = {
            "mode": "mock",
            "privacy": privacy,
            "account": account_label,
            "proxy_configured": bool(proxy_url),
        }
        if video_format != "9:16":
            # Как ушло бы на настоящую площадку (app/publish_format.py) — видно в задании.
            from app.publish_format import publish_plan, youtube_texts

            plan = publish_plan(self.platform, video_format)
            response.update({"format": video_format, "publish_as": plan["publish_as"]})
            if plan["warning"]:
                response["format_warning"] = plan["warning"]
            if self.platform == "youtube":
                response["title"], response["description"] = youtube_texts(video_format, title, description)
                response["thumbnail"] = bool(thumbnail_path)
        return ProviderResult(
            status="succeeded",
            remote_id=remote_id,
            remote_url=f"https://example.test/{self.platform}/{remote_id}",
            response=response,
        )
