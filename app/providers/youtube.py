from __future__ import annotations

import json
import subprocess
from pathlib import Path

from app.cookies import CookieRecord, to_cookie_header_for_host
from app.providers.base import Provider, ProviderResult
from app.settings import settings


class YouTubeProvider(Provider):
    platform = "youtube"

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
        payload = {
            "cookie": to_cookie_header_for_host(cookies, "studio.youtube.com"),
            "filePath": str(file_path),
            "title": title,
            "description": description,
            "privacy": privacy.upper(),
            "proxyUrl": proxy_url,
        }
        try:
            proc = subprocess.run(
                [settings.node_bin, str(settings.youtube_helper)],
                input=json.dumps(payload, ensure_ascii=False),
                text=True,
                capture_output=True,
                timeout=60 * 60,
                check=False,
            )
        except FileNotFoundError:
            return ProviderResult(status="failed", error="node executable not found")
        except subprocess.TimeoutExpired:
            return ProviderResult(status="failed", error="youtube upload timed out")

        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()
        if proc.returncode != 0:
            error = stderr
            response = {}
            if stdout:
                try:
                    response = json.loads(stdout)
                    error = response.get("error") or error
                except json.JSONDecodeError:
                    error = stdout or error
            return ProviderResult(
                status=_classify_youtube_error(error),
                error=_safe_error(error),
                response=response,
            )
        try:
            result = json.loads(stdout)
        except json.JSONDecodeError:
            return ProviderResult(status="failed", error="youtube helper returned invalid JSON")
        if not result.get("ok"):
            error = _safe_error(result.get("error") or "")
            return ProviderResult(status=_classify_youtube_error(error), error=error, response=result)
        remote_id = result.get("videoId") or ""
        return ProviderResult(
            status="succeeded",
            remote_id=remote_id,
            remote_url=f"https://youtu.be/{remote_id}" if remote_id else "",
            response=result,
        )


def _classify_youtube_error(message: str) -> str:
    lowered = message.lower()
    auth_markers = ("sign in", "signed in", "cookie", "401", "403", "unauthorized", "forbidden")
    if any(marker in lowered for marker in auth_markers):
        return "needs_reauth"
    return "failed"


def _safe_error(message: str) -> str:
    text = (message or "youtube upload failed").strip()
    if len(text) > 2000:
        text = text[:2000] + "..."
    return text
