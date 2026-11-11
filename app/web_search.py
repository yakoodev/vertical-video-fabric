"""Поиск в интернете для агента: «нужна реклама витубики» → что это и где официальный сайт.

Сначала Gemini с поиском Google (``tools: google_search``, тот же клиент сервиса ``app/ai/gemini.py``) — он
отвечает списком {title, url, snippet}. Gemini часто заблокирован или упирается в лимит — тогда обычная HTML-выдача
DuckDuckGo (через прокси загрузок, если он задан в настройках). Не вышло ни то, ни другое — понятная ошибка.
Платный шлюз (Polza) здесь не используется никогда.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlsplit

import httpx

MAX_RESULTS = 10
DDG_URL = "https://html.duckduckgo.com/html/"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36")

PROMPT = """Найди в интернете: {query}
Верни JSON {{"results":[{{"title":"…","url":"https://…","snippet":"1–2 предложения, что там"}}]}} — до {n} самых
полезных страниц, сначала официальный сайт (если он есть). Только настоящие адреса страниц, которые ты видел в
поиске, без выдуманных. Если ничего не нашлось — {{"results":[]}}."""


class SearchError(RuntimeError):
    pass


def json_object(text: str) -> dict:
    """Объект JSON из ответа модели: голый, в ```json … ``` или внутри текста. Не вышло — ValueError."""
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = "\n".join(line for line in raw.splitlines() if not line.startswith("```")).strip()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("в ответе нет JSON") from None
        value = json.loads(raw[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("JSON ответа — не объект")
    return value


def _clean(items: Any, n: int) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or item.get("link") or "").strip()
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname or url in seen:
            continue
        seen.add(url)
        out.append({"title": " ".join(str(item.get("title") or parts.hostname).split())[:200], "url": url[:1000],
                    "snippet": " ".join(str(item.get("snippet") or "").split())[:400]})
        if len(out) >= n:
            break
    return out


def search_gemini(query: str, n: int, client: Any = None, model: str | None = None) -> list[dict]:
    from app.ai.gemini import GeminiClient, _extract_response_text
    from app.settings import settings

    if client is None and not settings.gemini_api_key:
        raise SearchError("Gemini не настроен")
    payload = {"contents": [{"role": "user", "parts": [{"text": PROMPT.format(query=query, n=n)}]}],
               "tools": [{"google_search": {}}]}
    response = (client or GeminiClient(timeout=90)).generate_content(model or settings.gemini_text_model, payload)
    results: list[dict] = []
    try:
        parsed = json_object(_extract_response_text(response))
        results = _clean(parsed.get("results") if isinstance(parsed, dict) else None, n)
    except (RuntimeError, ValueError, json.JSONDecodeError):
        results = []
    if not results:
        # Модель не дала JSON — берём источники поиска из groundingMetadata (заголовок там — домен).
        candidate = (response.get("candidates") or [{}])[0] if isinstance(response, dict) else {}
        chunks = (candidate.get("groundingMetadata") or {}).get("groundingChunks") or []
        results = _clean([{"title": (c.get("web") or {}).get("title"), "url": (c.get("web") or {}).get("uri")}
                          for c in chunks if isinstance(c, dict)], n)
    return results


def _ddg_href(raw: str) -> str:
    href = html.unescape(raw)
    if href.startswith("//"):
        href = "https:" + href
    parts = urlsplit(href)
    if parts.hostname and parts.hostname.endswith("duckduckgo.com") and parts.path.startswith("/l/"):
        target = parse_qs(parts.query).get("uddg")
        if target:
            return unquote(target[0])
    return href


def _strip_tags(text: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split())


def parse_ddg(page: str, n: int) -> list[dict]:
    """Выдача html.duckduckgo.com → [{title, url, snippet}] (реклама «ad» пропускается)."""
    items = []
    for block in re.split(r'<div class="result[ "]', page)[1:]:
        if "result--ad" in block[:200]:
            continue
        link = re.search(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not link:
            continue
        snippet = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', block, re.S)
        items.append({"url": _ddg_href(link.group(1)), "title": _strip_tags(link.group(2)),
                      "snippet": _strip_tags(snippet.group(1)) if snippet else ""})
    return _clean(items, n)


def search_ddg(query: str, n: int, fetch: Callable[..., httpx.Response] | None = None) -> list[dict]:
    from app.proxy_config import download_proxy

    kwargs: dict = {"data": {"q": query, "kl": "ru-ru"}, "headers": {"User-Agent": BROWSER_UA}, "timeout": 20,
                    "follow_redirects": True}
    proxy = download_proxy()
    if proxy:
        kwargs["proxy"] = proxy
    try:
        response = (fetch or httpx.post)(DDG_URL, **kwargs)
    except httpx.HTTPError as exc:
        raise SearchError(f"DuckDuckGo недоступен ({type(exc).__name__})") from exc
    if response.status_code != 200:
        raise SearchError(f"DuckDuckGo ответил {response.status_code}")
    return parse_ddg(response.text, n)


def web_search(query: str, count: int = 6, client: Any = None, fetch: Callable[..., httpx.Response] | None = None) -> dict:
    """Найти страницы: {query, engine, results: [{title, url, snippet}], note?}. Не вышло — SearchError."""
    wish = " ".join(str(query or "").split())[:300]
    if len(wish) < 2:
        raise SearchError("нужен запрос")
    n = max(1, min(int(count or 6), MAX_RESULTS))
    failures = []
    try:
        results = search_gemini(wish, n, client=client)
        if results:
            return {"query": wish, "engine": "gemini", "results": results}
        failures.append("Gemini ничего не нашёл")
    except Exception as exc:  # noqa: BLE001 - любой сбой Gemini → запасной поиск
        failures.append(f"Gemini: {str(exc)[:160]}")
    try:
        results = search_ddg(wish, n, fetch=fetch)
    except SearchError as exc:
        failures.append(str(exc))
        raise SearchError("поиск не удался: " + "; ".join(failures)) from exc
    return {"query": wish, "engine": "duckduckgo", "results": results,
            **({"note": "; ".join(failures)} if failures else {})}
