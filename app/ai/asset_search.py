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
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import urlparse

import httpx

from app.ai.gemini import GeminiClient
from app.settings import settings

MAX_RESULTS = 10
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
PREVIEW_LIMIT = 12 * 1024 * 1024
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif")

BASE_RULES = """\
Ты ищешь картинку для вставки в вертикальный клип (мем-реакция, стикер, иконка, подпись).

Запрос владельца: {query}

Найди в интернете {n} ПРЯМЫХ ссылок на файлы изображений — URL, который открывается как сам файл
(обычно оканчивается на .png/.jpg/.jpeg/.webp/.gif, но бывает и без расширения). Ссылка на
HTML-страницу галереи не подходит: нужен сам файл. Прямые ссылки хорошо отдают, например:
upload.wikimedia.org, openclipart.org, freesvg.org, CDN Pixabay, i.imgur.com, media.tenor.com.
Давай разные варианты из разных источников. Если по-русски находится плохо, ищи и по-английски.
{scope}
Верни ТОЛЬКО JSON без пояснений:
{{"results": [{{"url": "...", "title": "что на картинке", "source": "сайт", "license": "если известна"}}]}}
"""

# По умолчанию ищем только там, откуда картинку можно взять законно.
FREE_SCOPE = """\
Бери ТОЛЬКО свободные источники: Wikimedia Commons, openclipart.org, freesvg.org, public domain,
CC0, Pixabay, Openverse, открытые библиотеки изображений.
НЕ бери: фотостоки с водяными знаками, логотипы и бренды, фотографии реальных людей, кадры из
фильмов/мультфильмов и известных персонажей, изображения с запретом на использование.
"""

# Владелец может снять ограничение: тогда ищем шире, но источник и лицензию
# всё равно показываем — решение, что публиковать, остаётся за ним.
ANY_SCOPE = """\
Ищи где угодно, включая мем-сайты и агрегаторы картинок: владелец сам решит, что использовать.
Всё равно не бери фотостоки с водяными знаками и файлы с явным запретом на скачивание.
Для каждой ссылки обязательно укажи source (сайт) и license, если она известна.
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
        if not _is_public_url(url):
            continue
        path = url.lower().split("?")[0]
        # HTML-страницу галереи отсекаем; расширения может и не быть — тип проверит сеть.
        if path.endswith((".html", ".htm", ".php", "/")) and not path.endswith(IMAGE_EXT):
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


def search_images(
    query: str,
    count: int = 6,
    free_only: bool = True,
    client: GeminiClient | None = None,
    model: str | None = None,
) -> dict:
    """Ask the model to find image links; check that each one really is an image."""
    wish = " ".join(str(query or "").split())[:200]
    if not wish:
        raise ValueError("нужен запрос")
    n = max(1, min(int(count), MAX_RESULTS))
    used_model = model or settings.gemini_text_model
    payload = {
        # просим вдвое больше: часть ссылок окажется страницами или мёртвой
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "text": BASE_RULES.format(
                            query=wish,
                            n=min(MAX_RESULTS, n * 2),
                            scope=FREE_SCOPE if free_only else ANY_SCOPE,
                        )
                    }
                ],
            }
        ],
        "tools": [{"google_search": {}}],
    }
    response = (client or GeminiClient()).generate_content(used_model, payload)
    candidate = (response.get("candidates") or [{}])[0]  # noqa: PLR2004
    text = "".join(
        part.get("text", "") for part in (candidate.get("content") or {}).get("parts") or [] if isinstance(part, dict)
    )
    results = _parse_results(text)[: n * 2]
    # Проверяем ссылки параллельно: последовательная проверка десятка URL с
    # таймаутами превращала поиск в трёхминутное ожидание.
    checked = []
    if results:
        with ThreadPoolExecutor(max_workers=min(8, len(results))) as pool:
            for item, info in zip(results, pool.map(lambda r: _probe_url(r["url"]), results)):
                if info:
                    checked.append({**item, **info})
    sources = [
        chunk.get("web", {}).get("uri")
        for chunk in (candidate.get("groundingMetadata") or {}).get("groundingChunks") or []
        if isinstance(chunk, dict)
    ]
    return {
        "query": wish,
        "results": checked[:n],
        "model": used_model,
        "free_only": free_only,
        "searched": [s for s in sources if s][:5],
        "raw": text[:400] if not checked else "",
    }


def _probe_url(url: str) -> dict | None:
    """Живая ли ссылка и правда ли это картинка. HEAD умеют не все — тогда просим
    первые байты обычным GET (многие CDN отвечают 403/405 только на HEAD)."""
    headers = {"User-Agent": BROWSER_UA, "Accept": "image/*,*/*"}
    try:
        head = httpx.head(url, timeout=6, follow_redirects=True, headers=headers)
        mime = (head.headers.get("content-type") or "").split(";")[0].strip().lower()
        if head.status_code < 400 and mime.startswith("image/"):
            return {"mime": mime, "size_bytes": int(head.headers.get("content-length") or 0)}
    except httpx.HTTPError:
        pass
    try:
        with httpx.stream(
            "GET", url, timeout=8, follow_redirects=True, headers={**headers, "Range": "bytes=0-2047"}
        ) as response:
            mime = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
            if response.status_code >= 400 or not mime.startswith("image/"):
                return None
            size = int(response.headers.get("content-length") or 0)
            return {"mime": mime, "size_bytes": size if size > 2048 else 0}
    except httpx.HTTPError:
        return None


def fetch_preview(url: str) -> tuple[bytes, str]:
    """Bytes of a candidate image for the preview (never saved to the library)."""
    if not _is_public_url(url):
        raise ValueError("ссылка недоступна для загрузки")
    with httpx.stream("GET", url, timeout=20, follow_redirects=True, headers={"User-Agent": BROWSER_UA}) as response:
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
