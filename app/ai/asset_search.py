"""🔎 ИИ ищет картинку в интернете (Gemini + Google Search) и показывает кандидатов.

The owner asked for this: when the library has nothing fitting, the agent should
be able to find a picture online. Two rules make it safe to live with:

* the search is steered at freely usable sources (Wikimedia Commons, openclipart,
  public domain / CC0) and away from stock watermarks, brand logos, photos of
  real people and famous characters;
* nothing is downloaded silently — the search only RETURNS candidates with their
  source link, and a file lands in the library when the owner picks it.

`fetch_preview` proxies a candidate image so the browser can show it (many sites
block hotlinking), with an SSRF guard: public http(s) only, no local networks.
"""

from __future__ import annotations

import ipaddress
import json
import re
import socket
from typing import Any
from urllib.parse import urlparse

import httpx

from app.ai.gemini import GeminiClient
from app.settings import settings

MAX_RESULTS = 8
PREVIEW_LIMIT = 12 * 1024 * 1024
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif")

RULES = """\
Ты ищешь картинку для вставки в вертикальный клип (мем-реакция, стикер, иконка, подпись).

Запрос владельца: {query}

Найди в интернете {n} прямых ссылок на файлы изображений (URL должен заканчиваться на .png, .jpg,
.jpeg, .webp или .gif и открываться напрямую).
Бери в первую очередь свободные источники: Wikimedia Commons, openclipart.org, public domain, CC0,
государственные и открытые библиотеки изображений.
НЕ бери: фотостоки с водяными знаками, логотипы и бренды, фотографии реальных людей, кадры из
фильмов/мультфильмов и известных персонажей, изображения с запретом на использование.

Верни ТОЛЬКО JSON без пояснений:
{{"results": [{{"url": "...", "title": "что на картинке", "source": "сайт", "license": "если известна"}}]}}
"""


def _is_public_url(url: str) -> bool:
    """http(s) на публичный адрес — никаких localhost и внутренних сетей."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return False
        infos = socket.getaddrinfo(parsed.hostname, None)
    except (ValueError, socket.gaierror, UnicodeError):
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


def _parse_results(text: str) -> list[dict]:
    body = text.strip()
    if body.startswith("```"):
        body = body.strip("`")
        body = body[body.find("{") :]
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        return []
    try:
        data = json.loads(body[start : end + 1])
    except json.JSONDecodeError:
        return []
    out = []
    for item in data.get("results") or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url.lower().split("?")[0].endswith(IMAGE_EXT) or not _is_public_url(url):
            continue
        out.append(
            {
                "url": url,
                "title": str(item.get("title") or "")[:200],
                "source": str(item.get("source") or urlparse(url).hostname or "")[:120],
                "license": str(item.get("license") or "")[:120],
            }
        )
    return out


def search_images(query: str, count: int = 6, client: GeminiClient | None = None, model: str | None = None) -> dict:
    """Ask the model to find image links; check that each one really is an image."""
    wish = " ".join(str(query or "").split())[:200]
    if not wish:
        raise ValueError("нужен запрос")
    n = max(1, min(int(count), MAX_RESULTS))
    used_model = model or settings.gemini_text_model
    payload = {
        "contents": [{"role": "user", "parts": [{"text": RULES.format(query=wish, n=n)}]}],
        "tools": [{"google_search": {}}],
    }
    response = (client or GeminiClient()).generate_content(used_model, payload)
    candidate = (response.get("candidates") or [{}])[0]
    text = "".join(
        part.get("text", "") for part in (candidate.get("content") or {}).get("parts") or [] if isinstance(part, dict)
    )
    results = _parse_results(text)[:n]
    # Проверяем, что ссылка живая и это действительно картинка.
    checked = []
    for item in results:
        try:
            head = httpx.head(item["url"], timeout=10, follow_redirects=True)
            mime = (head.headers.get("content-type") or "").split(";")[0].strip().lower()
            size = int(head.headers.get("content-length") or 0)
            if head.status_code >= 400 or (mime and not mime.startswith("image/")):
                continue
            checked.append({**item, "mime": mime or "image/*", "size_bytes": size})
        except httpx.HTTPError:
            continue
    sources = [
        chunk.get("web", {}).get("uri")
        for chunk in (candidate.get("groundingMetadata") or {}).get("groundingChunks") or []
        if isinstance(chunk, dict)
    ]
    return {"query": wish, "results": checked, "model": used_model, "searched": [s for s in sources if s][:5]}


def fetch_preview(url: str) -> tuple[bytes, str]:
    """Bytes of a candidate image for the preview (never saved to the library)."""
    if not _is_public_url(url):
        raise ValueError("ссылка недоступна для загрузки")
    with httpx.stream("GET", url, timeout=20, follow_redirects=True) as response:
        response.raise_for_status()
        mime = (response.headers.get("content-type") or "image/jpeg").split(";")[0].strip()
        if not mime.startswith("image/"):
            raise ValueError("по ссылке не картинка")
        blob = b""
        for chunk in response.iter_bytes(256 * 1024):
            blob += chunk
            if len(blob) > PREVIEW_LIMIT:
                raise ValueError("картинка слишком большая")
    return blob, mime


SAFE_NAME = re.compile(r"[^\w.\-]+", re.UNICODE)
