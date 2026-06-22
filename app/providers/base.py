from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from app.cookies import CookieRecord

_URL_USERINFO_RE = re.compile(r"\b(https?://)[^/\s:@]+:[^/\s:@]+@", re.IGNORECASE)
_HEADER_SECRET_RE = re.compile(
    r"\b(authorization|proxy-authorization|cookie)\s*:\s*[^\r\n]+",
    re.IGNORECASE,
)


@dataclass
class ProviderResult:
    status: str
    remote_id: str = ""
    remote_url: str = ""
    error: str = ""
    response: dict = field(default_factory=dict)
    # Refreshed auth material (e.g. rotated cookies {name: value}) to persist
    # back to the account after a successful upload, keeping the session alive.
    refreshed_cookies: dict = field(default_factory=dict)


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


def safe_provider_error(message: str, fallback: str, limit: int = 2000) -> str:
    text = (message or fallback).strip()
    text = _URL_USERINFO_RE.sub(r"\1***:***@", text)
    text = _HEADER_SECRET_RE.sub(lambda match: f"{match.group(1)}: ***", text)
    if len(text) > limit:
        text = text[:limit] + "..."
    return text
