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
    ) -> ProviderResult:
        if not cookies:
            return ProviderResult(status="needs_reauth", error="mock: missing cookies")
        remote_id = f"mock-{self.platform}-{file_path.stem[:12]}"
        return ProviderResult(
            status="succeeded",
            remote_id=remote_id,
            remote_url=f"https://example.test/{self.platform}/{remote_id}",
            response={
                "mode": "mock",
                "privacy": privacy,
                "account": account_label,
                "proxy_configured": bool(proxy_url),
            },
        )
