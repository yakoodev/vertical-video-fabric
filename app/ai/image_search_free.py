"""Поиск мемов и картинок без Gemini — когда у Gemini кончились кредиты или его нет.

Только официальные публичные API:

* Giphy — гифки (нужен бесплатный ключ разработчика ``GIPHY_API_KEY``; без него пропускаем);
* Imgflip — популярные мем-шаблоны (Drake, Roll Safe, Distracted Boyfriend…), без ключа;
* Wikimedia Commons — фото, рисунки и GIF со свободными лицензиями, без ключа.

Возвращает кандидатов в той же форме, что и поиск через Gemini:
``{url, title, source, license, mime, size_bytes}``.
"""

from __future__ import annotations

import os
import re
from typing import Any

import httpx

UA = "VerticalVideoFabric/1.0 (meme search; local tool)"
TIMEOUT = 10.0


def search_free(query: str, count: int = 6) -> list[dict]:
    wish = " ".join(str(query or "").split())[:200]
    if not wish:
        return []
    found: list[dict] = []
    for provider in (_giphy, _imgflip, _commons):
        try:
            found.extend(provider(wish, count))
        except Exception:  # noqa: BLE001 - один источник упал — берём другие
            continue
        if len(found) >= count:
            break
    seen, out = set(), []
    for item in found:
        if item["url"] not in seen:
            seen.add(item["url"])
            out.append(item)
    return out[:count]


def _giphy(query: str, count: int) -> list[dict]:
    key = os.getenv("GIPHY_API_KEY", "").strip()
    if not key:
        return []
    data = httpx.get(
        "https://api.giphy.com/v1/gifs/search",
        params={"api_key": key, "q": query, "limit": count, "rating": "r", "lang": "en"},
        timeout=TIMEOUT, headers={"User-Agent": UA},
    ).json()
    out = []
    for gif in data.get("data") or []:
        img = ((gif.get("images") or {}).get("downsized_medium") or (gif.get("images") or {}).get("original") or {})
        if img.get("url"):
            out.append({"url": img["url"], "title": gif.get("title") or query, "source": "giphy.com",
                        "license": "Giphy", "mime": "image/gif", "size_bytes": int(img.get("size") or 0)})
    return out


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-zа-яё0-9]+", text.lower()) if len(w) > 2}


def _imgflip(query: str, count: int) -> list[dict]:
    """Мем-шаблон, чьё название совпадает со словами запроса (запросы лучше по-английски)."""
    data = httpx.get("https://api.imgflip.com/get_memes", timeout=TIMEOUT, headers={"User-Agent": UA}).json()
    want = _words(query)
    scored = []
    for meme in (data.get("data") or {}).get("memes") or []:
        overlap = len(want & _words(meme.get("name") or ""))
        if overlap:
            scored.append((overlap, meme))
    scored.sort(key=lambda pair: -pair[0])
    return [
        {"url": meme["url"], "title": meme.get("name") or query, "source": "imgflip.com",
         "license": "meme template", "mime": "image/jpeg" if meme["url"].endswith(".jpg") else "image/png",
         "size_bytes": 0, "width": meme.get("width"), "height": meme.get("height")}
        for _overlap, meme in scored[:count]
    ]


def _commons(query: str, count: int) -> list[dict]:
    """Wikimedia Commons: поиск по файлам (фото, GIF), только свободные лицензии."""
    data = httpx.get(
        "https://commons.wikimedia.org/w/api.php",
        params={
            "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
            "gsrsearch": f"{query} filetype:bitmap", "gsrlimit": count,
            "prop": "imageinfo", "iiprop": "url|mime|size|extmetadata", "iiurlwidth": 720,
        },
        timeout=TIMEOUT, headers={"User-Agent": UA},
    ).json()
    out = []
    for page in ((data.get("query") or {}).get("pages") or {}).values():
        info = (page.get("imageinfo") or [{}])[0]
        url = info.get("thumburl") or info.get("url")
        mime = info.get("mime") or ""
        if not url or mime not in {"image/jpeg", "image/png", "image/gif", "image/webp"}:
            continue  # tiff/svg в ролик не вставить
        meta: dict[str, Any] = info.get("extmetadata") or {}
        license_name = ((meta.get("LicenseShortName") or {}).get("value") or "Wikimedia Commons")
        out.append({"url": url if mime != "image/gif" else info.get("url"), "title": page.get("title", "")[5:],
                    "source": "commons.wikimedia.org", "license": license_name, "mime": mime,
                    "size_bytes": int(info.get("size") or 0)})
    return out
