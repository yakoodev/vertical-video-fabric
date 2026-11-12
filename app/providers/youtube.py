from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from app.cookies import CookieRecord, to_cookie_header_for_host
from app.providers.base import Provider, ProviderResult, safe_provider_error
from app.publish_format import is_regular_video, youtube_texts
from app.settings import settings

log = logging.getLogger(__name__)


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
        video_format: str = "9:16",
        thumbnail_path: str = "",
        channel: str = "",
    ) -> ProviderResult:
        # 16:9 — обычное видео, не Shorts (app/publish_format.py): YouTube сам решает по кадру,
        # а #shorts в тексте горизонтального ролика только путает — убираем; горизонтальная
        # обложка ставится миниатюрой. У вертикали всё как раньше.
        title, description = youtube_texts(video_format, title, description)
        payload = {
            "cookie": to_cookie_header_for_host(cookies, "studio.youtube.com"),
            "filePath": str(file_path),
            "title": title,
            "description": description,
            "privacy": privacy.upper(),
            "proxyUrl": proxy_url,
            # Повторы до создания ролика (сбой выдачи адреса / передачи файла) — в помощнике.
            "retries": max(0, int(settings.publish_upload_retries)),
            "retrySeconds": max(0.0, float(settings.publish_upload_retry_seconds)),
        }
        # Аудитория («для детей» YouTube требует у каждого ролика) и категория — после загрузки.
        made_for_kids = _made_for_kids(settings.youtube_made_for_kids)
        if made_for_kids is not None:
            payload["madeForKids"] = made_for_kids
        category = _category_id(settings.youtube_category_id)
        if category:
            payload["categoryId"] = category
        if thumbnail_path and is_regular_video(self.platform, video_format):
            payload["thumbnailPath"] = str(thumbnail_path)
        # Канал аккаунта (UC… или @ник, accounts.handle): у одного входа Google их бывает
        # несколько, без выбора ролик уходит на канал по умолчанию. Помощник переключается
        # и до загрузки проверяет, что активен именно он; не тот — отказ без загрузки.
        if channel.strip():
            payload["channel"] = channel.strip()
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
            response = response if isinstance(response, dict) else {}
            # Свежие cookie и при падении помощника: Google уже сменил __Secure-*PSIDTS, без них
            # сохранённый вход протухает (10.11 задание 117 так разлогинило аккаунт). В ответ
            # задания cookie не кладём.
            refreshed = response.pop("refreshedCookies", None)
            return ProviderResult(
                status=_classify_youtube_error(error),
                error=_safe_error(error),
                response=response,
                refreshed_cookies=refreshed if isinstance(refreshed, dict) else {},
            )
        try:
            result = json.loads(stdout)
        except json.JSONDecodeError:
            return ProviderResult(status="failed", error="youtube helper returned invalid JSON")
        refreshed = result.pop("refreshedCookies", None)
        refreshed = refreshed if isinstance(refreshed, dict) else {}
        if not result.get("ok"):
            error = _safe_error(result.get("error") or "")
            if result.get("channelError"):
                log.warning("youtube channel check failed: account=%s channel=%s %s", account_label, channel, error)
            # Отказ из-за канала — не повод перевходить: cookie живые, дело в настройке канала.
            status = "failed" if result.get("channelError") else _classify_youtube_error(error)
            return ProviderResult(status=status, error=error, response=result, refreshed_cookies=refreshed)
        remote_id = result.get("videoId") or ""
        log.info("youtube upload ok: account=%s video_id=%s channel=%s attempts=%s metadata=%s%s", account_label,
                 remote_id or "?", _channel_text(result.get("channel")), result.get("uploadAttempts"),
                 result.get("metadataSet"),
                 (f" metadata_error={result['metadataError']}" if result.get("metadataError") else "")
                 + (f" metadata_check={result['metadataCheck']}" if result.get("metadataCheck") else ""))
        response = dict(result)
        if video_format != "9:16":
            response["publish_as"] = "video" if is_regular_video(self.platform, video_format) else "shorts"
        return ProviderResult(
            status="succeeded",
            remote_id=remote_id,
            remote_url=f"https://youtu.be/{remote_id}" if remote_id else "",
            response=response,
            refreshed_cookies=refreshed,
        )


def _channel_text(channel) -> str:
    """Канал, куда ушёл ролик, для журнала: «имя (UC…)»; помощник не узнал — «?»."""
    if not isinstance(channel, dict):
        return "?"
    name = channel.get("name") or channel.get("handle") or ""
    cid = channel.get("id") or ""
    return f"{name} ({cid})" if name and cid else (name or cid or "?")


def _made_for_kids(value: str) -> bool | None:
    """YOUTUBE_MADE_FOR_KIDS: false/no/0 — «не для детей», true/yes/1 — «для детей», иное — не трогать."""
    value = str(value or "").strip().lower()
    if value in ("false", "no", "0", "off"):
        return False
    if value in ("true", "yes", "1", "on"):
        return True
    return None


def _category_id(value: str) -> int:
    try:
        category = int(str(value or "").strip())
    except ValueError:
        return 0
    return category if category > 0 else 0


def _classify_youtube_error(message: str) -> str:
    lowered = message.lower()
    auth_markers = ("sign in", "signed in", "cookie", "401", "403", "unauthorized", "forbidden")
    if any(marker in lowered for marker in auth_markers):
        return "needs_reauth"
    return "failed"


def _safe_error(message: str) -> str:
    return safe_provider_error(message, "youtube upload failed")
