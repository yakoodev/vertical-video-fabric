"""Чат Twitch: номер VOD и клиент (из ``app/twitch_chat.py``)."""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Callable, Protocol

TWITCH_GQL_URL = "https://gql.twitch.tv/gql"
# Публичный Client-ID веб-сайта twitch.tv (его же используют плеер и TwitchDownloader).
TWITCH_WEB_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
COMMENTS_PERSISTED_HASH = "b70a3591ff0f4e0313d126c6a1502d79a1c02baebb288227c582044aa76adf6a"


_VOD_RE = re.compile(r"(?:twitch\.tv/(?:[^/]+/)?(?:videos?|v)/|^v?)(\d{6,})(?:[/?#]|$)", re.I)
_CLIP_RE = re.compile(r"(?:clips\.twitch\.tv/|twitch\.tv/[^/]+/clip/)([A-Za-z0-9_-]+)", re.I)


# --------------------------------------------------------------------------- VOD id


def parse_twitch_vod_ref(value: str) -> dict:
    """``{"vod_id"}`` или ``{"clip_slug"}`` из ссылки/числа; пусто — не Twitch."""
    text = str(value or "").strip()
    if not text:
        return {}
    clip = _CLIP_RE.search(text)
    if clip:
        return {"clip_slug": clip.group(1)}
    vod = _VOD_RE.search(text)
    if vod:
        return {"vod_id": vod.group(1)}
    return {}


# ------------------------------------------------------------------------- клиент


class ChatClient(Protocol):
    def video_info(self, vod_id: str) -> dict | None: ...
    def clip_info(self, slug: str) -> dict | None: ...
    def comments_page(self, vod_id: str, offset: int) -> tuple[list[dict], bool]: ...
    def badges(self, channel_id: str) -> dict[str, str]: ...
    def third_party_emotes(self, channel_id: str) -> dict[str, str]: ...


class TwitchChatClient:
    """Сеть: GQL Twitch + API 7TV/BTTV/FFZ. Потокобезопасен (общий httpx.Client)."""

    def __init__(self, client_id: str | None = None, timeout: float = 20.0, retries: int = 4) -> None:
        import httpx

        self.client_id = (client_id or os.getenv("TWITCH_GQL_CLIENT_ID", "") or TWITCH_WEB_CLIENT_ID).strip()
        self.retries = max(1, retries)
        self._http = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (vvf chat import)"},
        )
        self._use_persisted = False

    def close(self) -> None:
        self._http.close()

    # -- низкоуровневое

    def _request(self, method: str, url: str, **kwargs) -> Any:
        last: Exception | None = None
        for attempt in range(self.retries):
            try:
                resp = self._http.request(method, url, **kwargs)
                if resp.status_code == 404:
                    return None
                if resp.status_code in {429} or resp.status_code >= 500:
                    raise RuntimeError(f"HTTP {resp.status_code} от {url}")
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:  # noqa: BLE001 - сеть: повторяем с паузой
                last = exc
                time.sleep(min(8.0, 0.5 * (2**attempt)))
        raise RuntimeError(f"запрос не удался: {last}") from last

    def _gql(self, payload: Any) -> Any:
        return self._request(
            "POST",
            TWITCH_GQL_URL,
            headers={"Client-ID": self.client_id, "Content-Type": "text/plain;charset=UTF-8"},
            content=json.dumps(payload),
        )

    # -- Twitch

    def video_info(self, vod_id: str) -> dict | None:
        data = self._gql(
            {"query": 'query{video(id:"%s"){id,title,lengthSeconds,owner{id,login,displayName}}}' % _gql_id(vod_id)}
        )
        video = ((data or {}).get("data") or {}).get("video")
        if not video:
            return None
        owner = video.get("owner") or {}
        return {
            "vod_id": str(video.get("id") or vod_id),
            "title": video.get("title") or "",
            "length_sec": float(video.get("lengthSeconds") or 0),
            "channel_id": str(owner.get("id") or ""),
            "channel_login": owner.get("login") or "",
        }

    def clip_info(self, slug: str) -> dict | None:
        data = self._gql(
            {
                "query": 'query{clip(slug:"%s"){slug,durationSeconds,videoOffsetSeconds,video{id}}}'
                % re.sub(r"[^A-Za-z0-9_-]", "", slug)
            }
        )
        clip = ((data or {}).get("data") or {}).get("clip")
        if not clip or not (clip.get("video") or {}).get("id"):
            return None
        return {
            "vod_id": str(clip["video"]["id"]),
            "start_sec": float(clip.get("videoOffsetSeconds") or 0),
            "duration_sec": float(clip.get("durationSeconds") or 0),
        }

    def comments_page(self, vod_id: str, offset: int) -> tuple[list[dict], bool]:
        """Страница сообщений вокруг секунды ``offset``: (узлы, есть ли дальше)."""
        if not self._use_persisted:
            query = (
                'query{video(id:"%s"){comments(contentOffsetSeconds:%d){edges{node{id,contentOffsetSeconds,'
                "commenter{id,login,displayName},message{fragments{text,emote{emoteID}},"
                "userBadges{setID,version},userColor}}},pageInfo{hasNextPage}}}}" % (_gql_id(vod_id), max(0, int(offset)))
            )
            data = self._gql({"query": query}) or {}
            if not data.get("errors"):
                return _comments_from_video(((data.get("data") or {}).get("video")))
            # Сырой запрос когда-нибудь могут закрыть — тогда как веб-плеер.
            self._use_persisted = True
        data = self._gql(
            [
                {
                    "operationName": "VideoCommentsByOffsetOrCursor",
                    "variables": {"videoID": str(vod_id), "contentOffsetSeconds": max(0, int(offset))},
                    "extensions": {"persistedQuery": {"version": 1, "sha256Hash": COMMENTS_PERSISTED_HASH}},
                }
            ]
        )
        first = (data or [{}])[0] if isinstance(data, list) else (data or {})
        if first.get("errors"):
            raise RuntimeError(f"Twitch GQL: {first['errors'][0].get('message', 'ошибка')}")
        return _comments_from_video(((first.get("data") or {}).get("video")))

    def badges(self, channel_id: str) -> dict[str, str]:
        fields = "setID,version,imageURL(size:DOUBLE)"
        query = "query{badges{%s}%s}" % (
            fields,
            ',user(id:"%s"){broadcastBadges{%s}}' % (_gql_id(channel_id), fields) if channel_id else "",
        )
        data = ((self._gql({"query": query}) or {}).get("data")) or {}
        out: dict[str, str] = {}
        rows = list(data.get("badges") or []) + list(((data.get("user") or {}).get("broadcastBadges")) or [])
        for row in rows:  # канальные идут последними и перекрывают глобальные
            if row and row.get("setID") and row.get("imageURL"):
                out[f"{row['setID']}/{row.get('version') or '1'}"] = row["imageURL"]
        return out

    def third_party_emotes(self, channel_id: str) -> dict[str, str]:
        """Карта ``имя → url`` 7TV/BTTV/FFZ. Источник упал — просто без него."""
        layers: list[dict[str, str]] = []  # от слабого к сильному: глобальные, потом канал

        def safe(fn: Callable[[], dict[str, str]]) -> dict[str, str]:
            try:
                return fn() or {}
            except Exception:  # noqa: BLE001 - смайлы необязательны
                return {}

        layers.append(safe(lambda: _ffz_sets(self._request("GET", "https://api.frankerfacez.com/v1/set/global"))))
        layers.append(safe(lambda: _bttv_list(self._request("GET", "https://api.betterttv.net/3/cached/emotes/global"))))
        layers.append(safe(lambda: _seventv_set(self._request("GET", "https://7tv.io/v3/emote-sets/global"))))
        if channel_id:
            cid = re.sub(r"\D", "", channel_id)
            layers.append(safe(lambda: _ffz_sets(self._request("GET", f"https://api.frankerfacez.com/v1/room/id/{cid}"))))
            layers.append(
                safe(lambda: _bttv_user(self._request("GET", f"https://api.betterttv.net/3/cached/users/twitch/{cid}")))
            )
            layers.append(
                safe(lambda: _seventv_set((self._request("GET", f"https://7tv.io/v3/users/twitch/{cid}") or {}).get("emote_set")))
            )
        merged: dict[str, str] = {}
        for layer in layers:
            merged.update(layer)
        return merged


def _gql_id(value: str) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _comments_from_video(video: dict | None) -> tuple[list[dict], bool]:
    if not video:
        raise LookupError("VOD недоступен: удалён, скрыт или только для подписчиков")
    comments = video.get("comments") or {}
    nodes = [edge.get("node") for edge in comments.get("edges") or [] if edge and edge.get("node")]
    has_next = bool((comments.get("pageInfo") or {}).get("hasNextPage"))
    return nodes, has_next


def _seventv_set(emote_set: dict | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for emote in (emote_set or {}).get("emotes") or []:
        host = ((emote.get("data") or {}).get("host")) or {}
        base = host.get("url") or ""
        if emote.get("name") and base:
            out[emote["name"]] = ("https:" + base if base.startswith("//") else base) + "/2x.webp"
    return out


def _bttv_list(items: list | None) -> dict[str, str]:
    return {
        e["code"]: f"https://cdn.betterttv.net/emote/{e['id']}/2x.webp"
        for e in items or []
        if isinstance(e, dict) and e.get("code") and e.get("id")
    }


def _bttv_user(data: dict | None) -> dict[str, str]:
    data = data or {}
    return _bttv_list(list(data.get("sharedEmotes") or []) + list(data.get("channelEmotes") or []))


def _ffz_sets(data: dict | None) -> dict[str, str]:
    out: dict[str, str] = {}
    sets = (data or {}).get("sets") or {}
    for emote_set in sets.values():
        for emote in (emote_set or {}).get("emoticons") or []:
            urls = emote.get("urls") or {}
            url = urls.get("2") or urls.get("1") or ""
            if emote.get("name") and url:
                out[emote["name"]] = "https:" + url if url.startswith("//") else url
    return out
