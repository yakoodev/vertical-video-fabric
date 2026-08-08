from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from hashlib import sha1
from urllib.parse import urlencode, urljoin, urlsplit

import httpx

from app.settings import settings


SMOTVIBE_HOST = "smotvibe.sbs"
# Smotvibe rotates its top-level domain (smotvibe.sbs, smotvibe.pics, …), so match
# the brand at the registrable (second-level) label instead of a fixed host.
SMOTVIBE_BRAND = "smotvibe"
# Sites that ship the very same Kinobox player template as Smotvibe (same 404-with-
# player-markup pages, same /series/<kinopoisk-id> routes). Extra ones can be added
# through PLAYER_PAGE_HOSTS without touching the code.
PLAYER_PAGE_BRANDS = (SMOTVIBE_BRAND, "gromfaer")
PLAYER_EXTENSIONS = (".m3u8", ".mp4")
STATIC_EXTENSIONS = (
    ".css",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".js",
    ".png",
    ".svg",
    ".webp",
)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36 Edg/149.0.0.0"
)
BASE_HEADERS = {
    "accept-language": "ru,en;q=0.9,en-GB;q=0.8,en-US;q=0.7",
    "cache-control": "no-cache",
    "pragma": "no-cache",
    "user-agent": USER_AGENT,
}

_QUOTED_URL_RE = re.compile(
    r"""["'](?P<url>(?:https?:)?//[^"']+?|/[^"']+?)["']""",
    re.IGNORECASE,
)
_IFRAME_SRC_RE = re.compile(
    r"""<iframe\b[^>]*\bsrc\s*=\s*["'](?P<src>[^"']+)["']""",
    re.IGNORECASE,
)
_KINOBOX_ATTR_RE = re.compile(
    r"""data-(?P<key>kinopoisk|imdb|title|query)\s*=\s*["'](?P<value>[^"']+)["']""",
    re.IGNORECASE,
)
_KINOBOX_BASE_RE = re.compile(
    r"""kinobox\s*\([^)]*baseUrl\s*:\s*["'](?P<base>https?://[^"']+)["']""",
    re.IGNORECASE | re.DOTALL,
)
_ROUTE_KINOPOISK_RE = re.compile(r"""^/(?:series|film)/(?P<id>[^/?#]+)""", re.IGNORECASE)


@dataclass(frozen=True)
class SmotvibeMedia:
    media_url: str
    referer: str


@dataclass(frozen=True)
class KinoboxFrame:
    frame_url: str
    provider: str = ""
    translation: str = ""
    quality: str = ""


@dataclass(frozen=True)
class SmotvibeDownloadOption:
    provider: str
    season: str
    episode: str
    translation: str
    media_url: str
    referer: str
    audio_format_id: str = ""
    quality: str = ""
    title: str = ""
    duration_sec: float = 0

    @property
    def option_id(self) -> str:
        raw = "|".join(
            [
                self.provider,
                self.season,
                self.episode,
                self.translation,
                self.audio_format_id,
                self.media_url,
            ]
        )
        return sha1(raw.encode("utf-8")).hexdigest()[:16]

    @property
    def filename_label(self) -> str:
        parts = []
        if self.season:
            parts.append(f"s{self.season}")
        if self.episode:
            parts.append(f"e{self.episode}")
        if self.translation:
            parts.append(self.translation)
        return "-".join(parts)

    def to_dict(self) -> dict:
        return {
            "id": self.option_id,
            "provider": self.provider,
            "season": self.season,
            "episode": self.episode,
            "translation": self.translation,
            "quality": self.quality,
            "title": self.title,
            "duration_sec": self.duration_sec,
            "media_url": self.media_url,
            "referer": self.referer,
            "audio_format_id": self.audio_format_id,
            "filename_label": self.filename_label,
        }


def is_player_page_url(url: str) -> bool:
    host = (urlsplit(url.strip()).hostname or "").lower()
    labels = [label for label in host.split(".") if label]
    if len(labels) < 2:
        return False
    brands = set(PLAYER_PAGE_BRANDS)
    for entry in settings.player_page_hosts:
        entry = entry.strip().lower().lstrip(".")
        if not entry:
            continue
        # A full host from the config matches exactly (or as a parent domain);
        # a bare brand matches whatever TLD the site currently squats on.
        if "." in entry:
            if host == entry or host.endswith(f".{entry}"):
                return True
        else:
            brands.add(entry)
    # Match <brand>.<tld> and any subdomain like www.<brand>.<tld>, regardless of
    # which TLD the brand is currently using.
    return labels[-2] in brands


# Kept for callers and tests that predate the multi-site support.
is_smotvibe_url = is_player_page_url


def resolve_smotvibe_media(url: str, *, max_pages: int = 24) -> SmotvibeMedia:
    _ensure_page_url(url)
    headers = {**BASE_HEADERS, "referer": url}
    with httpx.Client(follow_redirects=True, timeout=30, headers=headers) as client:
        return resolve_smotvibe_media_with_client(client, url, max_pages=max_pages)


def resolve_smotvibe_media_with_client(
    client: httpx.Client,
    url: str,
    *,
    max_pages: int = 24,
) -> SmotvibeMedia:
    queue: list[tuple[str, str]] = [(url, url)]
    seen: set[str] = set()
    pages_read = 0
    while queue and pages_read < max_pages:
        page_url, referer = queue.pop(0)
        if page_url in seen:
            continue
        seen.add(page_url)
        try:
            response = client.get(page_url, headers={"referer": referer})
            _raise_for_unusable_status(response)
        except httpx.HTTPError:
            continue
        pages_read += 1
        html_text = _normalize_html(response.text)
        media_urls = extract_smotvibe_media_urls(html_text, page_url)
        if media_urls:
            return SmotvibeMedia(_preferred_media_url(media_urls), page_url)
        for frame_url in extract_smotvibe_frame_urls(html_text, page_url):
            if frame_url not in seen:
                queue.append((frame_url, page_url))
        for frame_url in _prioritize_frame_urls(extract_kinobox_frame_urls(client, html_text, page_url)):
            if frame_url not in seen:
                queue.append((frame_url, page_url))
    raise ValueError("Smotvibe media URL was not found in page player")


def discover_smotvibe_download_targets(url: str, *, max_pages: int = 24) -> list[SmotvibeMedia]:
    _ensure_page_url(url)
    headers = {**BASE_HEADERS, "referer": url}
    targets = [SmotvibeMedia(url, url)]
    with httpx.Client(follow_redirects=True, timeout=30, headers=headers) as client:
        queue: list[tuple[str, str]] = [(url, url)]
        seen: set[str] = set()
        pages_read = 0
        while queue and pages_read < max_pages:
            page_url, referer = queue.pop(0)
            if page_url in seen:
                continue
            seen.add(page_url)
            try:
                response = client.get(page_url, headers={"referer": referer})
                _raise_for_unusable_status(response)
            except httpx.HTTPError:
                continue
            pages_read += 1
            html_text = _normalize_html(response.text)
            for media_url in extract_smotvibe_media_urls(html_text, page_url):
                targets.append(SmotvibeMedia(media_url, page_url))
            for frame_url in extract_smotvibe_frame_urls(html_text, page_url):
                targets.append(SmotvibeMedia(frame_url, page_url))
                if frame_url not in seen:
                    queue.append((frame_url, page_url))
            for frame in _prioritize_kinobox_frames(extract_kinobox_frames(client, html_text, page_url)):
                targets.append(SmotvibeMedia(frame.frame_url, page_url))
                if frame.frame_url not in seen:
                    queue.append((frame.frame_url, page_url))
    return _unique_media(targets)


def discover_smotvibe_download_options(url: str, *, max_pages: int = 24) -> list[SmotvibeDownloadOption]:
    _ensure_page_url(url)
    headers = {**BASE_HEADERS, "referer": url}
    options: list[SmotvibeDownloadOption] = []
    with httpx.Client(follow_redirects=True, timeout=30, headers=headers) as client:
        queue: list[tuple[str, str, str]] = [(url, url, "")]
        seen: set[str] = set()
        pages_read = 0
        while queue and pages_read < max_pages:
            page_url, referer, provider = queue.pop(0)
            if page_url in seen:
                continue
            seen.add(page_url)
            try:
                response = client.get(page_url, headers={"referer": referer})
                _raise_for_unusable_status(response)
            except httpx.HTTPError:
                continue
            pages_read += 1
            html_text = _normalize_html(response.text)
            options.extend(
                extract_collaps_playlist_options(
                    html_text,
                    page_url,
                    provider=provider or _provider_from_url(page_url),
                )
            )
            for frame_url in extract_smotvibe_frame_urls(html_text, page_url):
                if frame_url not in seen:
                    queue.append((frame_url, page_url, _provider_from_url(frame_url)))
            for frame in _prioritize_kinobox_frames(extract_kinobox_frames(client, html_text, page_url)):
                if frame.frame_url not in seen:
                    queue.append((frame.frame_url, page_url, frame.provider))
    return _unique_options(options)


def extract_smotvibe_media_urls(html_text: str, base_url: str) -> list[str]:
    urls = []
    for raw_url in _extract_quoted_urls(_normalize_html(html_text), base_url):
        path = urlsplit(raw_url).path.lower()
        if any(path.endswith(ext) for ext in PLAYER_EXTENSIONS):
            urls.append(raw_url)
    return _unique(urls)


def extract_smotvibe_frame_urls(html_text: str, base_url: str) -> list[str]:
    normalized = _normalize_html(html_text)
    urls = [urljoin(base_url, match.group("src").strip()) for match in _IFRAME_SRC_RE.finditer(normalized)]
    # Some players store iframe URLs in JS strings instead of iframe markup.
    urls.extend(
        raw_url
        for raw_url in _extract_quoted_urls(normalized, base_url)
        if _looks_like_player_page(raw_url)
    )
    return _unique(urls)


def extract_kinobox_frame_urls(client: httpx.Client, html_text: str, page_url: str) -> list[str]:
    return _unique([frame.frame_url for frame in extract_kinobox_frames(client, html_text, page_url)])


def extract_kinobox_frames(client: httpx.Client, html_text: str, page_url: str) -> list[KinoboxFrame]:
    search = _extract_kinobox_search(html_text, page_url)
    if not search:
        return []
    base_urls = _extract_kinobox_base_urls(html_text)
    frames: list[KinoboxFrame] = []
    for base_url in base_urls:
        api_url = urljoin(base_url, f"/api/players?{urlencode(search)}")
        try:
            response = client.get(
                api_url,
                headers={
                    "accept": "*/*",
                    "origin": _origin(page_url),
                    "referer": f"{_origin(page_url)}/",
                    "sec-fetch-dest": "empty",
                    "sec-fetch-mode": "cors",
                    "sec-fetch-site": "cross-site",
                },
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, json.JSONDecodeError, ValueError):
            continue
        for item in payload.get("data") or []:
            if not isinstance(item, dict):
                continue
            provider = str(item.get("type") or "")
            iframe_url = item.get("iframeUrl") if isinstance(item, dict) else None
            if iframe_url:
                frames.append(KinoboxFrame(urljoin(api_url, str(iframe_url)), provider=provider))
            translations = item.get("translations") if isinstance(item, dict) else None
            if isinstance(translations, list):
                for translation in translations:
                    translation_url = translation.get("iframeUrl") if isinstance(translation, dict) else None
                    if translation_url:
                        frames.append(
                            KinoboxFrame(
                                urljoin(api_url, str(translation_url)),
                                provider=provider,
                                translation=str(translation.get("name") or ""),
                                quality=str(translation.get("quality") or ""),
                            )
                        )
    return _unique_frames(frames)


def extract_collaps_playlist_options(
    html_text: str,
    page_url: str,
    *,
    provider: str = "Collaps",
) -> list[SmotvibeDownloadOption]:
    normalized = _normalize_html(html_text)
    seasons_json = _extract_js_array(normalized, "seasons")
    if not seasons_json:
        return []
    try:
        seasons = json.loads(seasons_json)
    except json.JSONDecodeError:
        return []
    if not isinstance(seasons, list):
        return []
    provider = provider or _provider_from_url(page_url) or "Collaps"
    options: list[SmotvibeDownloadOption] = []
    for season_item in seasons:
        if not isinstance(season_item, dict):
            continue
        season = str(season_item.get("season") or "")
        episodes = season_item.get("episodes") or []
        if not isinstance(episodes, list):
            continue
        for episode_item in episodes:
            if not isinstance(episode_item, dict):
                continue
            media_url = str(episode_item.get("hls") or episode_item.get("file") or episode_item.get("download") or "")
            if not media_url:
                continue
            episode = str(episode_item.get("episode") or "")
            title = str(episode_item.get("title") or "")
            duration_sec = _float_or_zero(episode_item.get("duration"))
            audio = episode_item.get("audio") if isinstance(episode_item.get("audio"), dict) else {}
            audio_names = audio.get("names") if isinstance(audio, dict) else None
            audio_order = audio.get("order") if isinstance(audio, dict) else None
            options.extend(
                _collaps_audio_options(
                    provider=provider,
                    season=season,
                    episode=episode,
                    title=title,
                    duration_sec=duration_sec,
                    media_url=media_url,
                    referer=page_url,
                    audio_names=audio_names if isinstance(audio_names, list) else [],
                    audio_order=audio_order if isinstance(audio_order, list) else [],
                )
            )
    return _unique_options(options)


def _collaps_audio_options(
    *,
    provider: str,
    season: str,
    episode: str,
    title: str,
    duration_sec: float,
    media_url: str,
    referer: str,
    audio_names: list,
    audio_order: list,
) -> list[SmotvibeDownloadOption]:
    if not audio_names:
        return [
            SmotvibeDownloadOption(
                provider=provider,
                season=season,
                episode=episode,
                translation="",
                media_url=media_url,
                referer=referer,
                title=title,
                duration_sec=duration_sec,
            )
        ]
    options = []
    for index, raw_name in enumerate(audio_names):
        translation = str(raw_name or "").strip()
        if not translation:
            continue
        order_value = audio_order[index] if index < len(audio_order) else index
        audio_format_id = _collaps_audio_format_id(translation, order_value)
        options.append(
            SmotvibeDownloadOption(
                provider=provider,
                season=season,
                episode=episode,
                translation=translation,
                media_url=media_url,
                referer=referer,
                audio_format_id=audio_format_id,
                title=title,
                duration_sec=duration_sec,
            )
        )
    return options


def _extract_quoted_urls(html_text: str, base_url: str) -> list[str]:
    urls = []
    for match in _QUOTED_URL_RE.finditer(html_text):
        raw_url = match.group("url").strip()
        if not raw_url:
            continue
        if raw_url.startswith("//"):
            raw_url = f"{urlsplit(base_url).scheme}:{raw_url}"
        urls.append(urljoin(base_url, raw_url))
    return urls


def _looks_like_player_page(url: str) -> bool:
    path = urlsplit(url).path.lower()
    if path.endswith(PLAYER_EXTENSIONS) or path.endswith(STATIC_EXTENSIONS):
        return False
    host = (urlsplit(url).hostname or "").lower()
    if any(
        static_host in host
        for static_host in (
            "cdn.jsdelivr.net",
            "data.jsdelivr.com",
            "jsdelivr.com",
            "npmjs.com",
            "registry.npmjs.org",
        )
    ):
        return False
    return any(marker in path for marker in ("/player", "/embed", "/iframe", "/video"))


def _prioritize_frame_urls(urls: list[str]) -> list[str]:
    def priority(url: str) -> int:
        host = (urlsplit(url).hostname or "").lower()
        if "ortified" in host:
            return 0
        if "obrut" in host:
            return 1
        if "theatre" in host or "stravers" in host:
            return 2
        return 3

    return sorted(urls, key=priority)


def _prioritize_kinobox_frames(frames: list[KinoboxFrame]) -> list[KinoboxFrame]:
    prioritized_urls = _prioritize_frame_urls([frame.frame_url for frame in frames])
    by_url = {frame.frame_url: frame for frame in frames}
    return [by_url[url] for url in prioritized_urls if url in by_url]


def _extract_kinobox_search(html_text: str, page_url: str) -> dict[str, str]:
    normalized = _normalize_html(html_text)
    if "kinobox" not in normalized.lower():
        return {}
    search: dict[str, str] = {}
    for match in _KINOBOX_ATTR_RE.finditer(normalized):
        value = html.unescape(match.group("value")).strip()
        if value:
            search[match.group("key").lower()] = value
    route_match = _ROUTE_KINOPOISK_RE.match(urlsplit(page_url).path)
    if route_match:
        search["kinopoisk"] = route_match.group("id")
    return search


def _extract_kinobox_base_urls(html_text: str) -> list[str]:
    urls = [match.group("base").strip() for match in _KINOBOX_BASE_RE.finditer(_normalize_html(html_text))]
    urls.append("https://api.kinobox.tv/")
    return _unique(urls)


def _ensure_page_url(url: str) -> None:
    """Discovery works on any Kinobox-style player page, not just known brands."""
    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("player page url must be http:// or https://")


def _raise_for_unusable_status(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    body = _normalize_html(response.text)
    if response.status_code == 404 and _has_player_markup(body):
        return
    response.raise_for_status()


def _has_player_markup(html_text: str) -> bool:
    lowered = html_text.lower()
    return any(marker in lowered for marker in ("kinobox", "<iframe", ".m3u8", ".mp4", "player"))


def _preferred_media_url(urls: list[str]) -> str:
    hls = [url for url in urls if urlsplit(url).path.lower().endswith(".m3u8")]
    return (hls or urls)[0]


def _normalize_html(html_text: str) -> str:
    return html.unescape(html_text).replace("\\/", "/")


def _extract_js_array(text: str, key: str) -> str:
    match = re.search(rf"\b{re.escape(key)}\s*:", text, re.IGNORECASE)
    if not match:
        return ""
    index = match.end()
    while index < len(text) and text[index].isspace():
        index += 1
    if index >= len(text) or text[index] != "[":
        return ""
    end = _balanced_json_end(text, index, "[", "]")
    return text[index:end] if end > index else ""


def _balanced_json_end(text: str, start: int, opening: str, closing: str) -> int:
    depth = 0
    in_string = False
    escape = False
    quote = ""
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote:
                in_string = False
            continue
        if char in {"'", '"'}:
            in_string = True
            quote = char
            continue
        if char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return index + 1
    return -1


def _collaps_audio_format_id(translation: str, order_value) -> str:
    try:
        order_index = int(order_value)
    except (TypeError, ValueError):
        order_index = 0
    lowered = translation.lower()
    prefix = "jpn" if any(marker in lowered for marker in ("japan", "japanese", "original")) else "rus"
    return f"audio0-{prefix}{order_index}"


def _provider_from_url(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    if "ortified" in host or "collaps" in host:
        return "Collaps"
    if "theatre" in host or "stravers" in host:
        return "Alloha"
    if "obrut" in host:
        return "Videocdn"
    return ""


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique_values = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique_values.append(value)
    return unique_values


def _unique_frames(values: list[KinoboxFrame]) -> list[KinoboxFrame]:
    seen: set[tuple[str, str, str, str]] = set()
    unique_values = []
    for value in values:
        key = (value.frame_url, value.provider, value.translation, value.quality)
        if key in seen:
            continue
        seen.add(key)
        unique_values.append(value)
    return unique_values


def _unique_media(values: list[SmotvibeMedia]) -> list[SmotvibeMedia]:
    seen: set[tuple[str, str]] = set()
    unique_values = []
    for value in values:
        key = (value.media_url, value.referer)
        if key in seen:
            continue
        seen.add(key)
        unique_values.append(value)
    return unique_values


def _unique_options(values: list[SmotvibeDownloadOption]) -> list[SmotvibeDownloadOption]:
    seen: set[str] = set()
    unique_values = []
    for value in values:
        key = value.option_id
        if key in seen:
            continue
        seen.add(key)
        unique_values.append(value)
    return unique_values


def _float_or_zero(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"
