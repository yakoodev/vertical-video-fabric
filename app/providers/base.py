from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.cookies import CookieRecord


@dataclass
class ProviderResult:
    status: str
    remote_id: str = ""
    remote_url: str = ""
    error: str = ""
    response: dict = field(default_factory=dict)


class Provider:
    platform: str

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
        raise NotImplementedError
