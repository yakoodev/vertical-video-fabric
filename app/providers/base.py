from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from app.cookies import CookieRecord

# До последней @ перед хостом: пароль бывает с @, схема любая (прокси — и socks5).
_URL_USERINFO_RE = re.compile(r"\b([a-z][a-z0-9+.-]*://)[^\s/]+@", re.IGNORECASE)
# В тексте ошибки requests и браузера подписи — адрес с параметрами запроса.
_QUERY_SECRET_RE = re.compile(r"\b(mstoken|sessionid|sid_tt|session_token|access_key_id)=[^&\s'\"]+", re.IGNORECASE)
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
    # Запрос поста ушёл, а ответа нет: ролик мог выйти. Повторять только осознанно (app/store_jobs.py).
    maybe_posted: bool = False


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
        video_format: str = "9:16",
        thumbnail_path: str = "",
    ) -> ProviderResult:
        """``video_format`` — формат клипа (app/publish_format.py): 16:9 на YouTube — обычное
        видео, не Shorts; ``thumbnail_path`` — миниатюра обычного видео. Воркер передаёт их
        только не для 9:16 — у вертикали вызов прежний."""
        raise NotImplementedError


def safe_provider_error(message: str, fallback: str, limit: int = 2000) -> str:
    text = (message or fallback).strip()
    text = _URL_USERINFO_RE.sub(r"\1***:***@", text)
    text = _QUERY_SECRET_RE.sub(lambda match: f"{match.group(1)}=***", text)
    text = _HEADER_SECRET_RE.sub(lambda match: f"{match.group(1)}: ***", text)
    if len(text) > limit:
        text = text[:limit] + "..."
    return text
