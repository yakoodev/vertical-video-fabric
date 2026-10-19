"""💬 Чат Twitch-VOD: импорт, хранение, «хайп»-активность и привязка к клипу.

Откуда берём чат (проверено вживую, октябрь 2026 — см. docs/twitch-chat.md):

* Публичный GQL веб-плеера ``https://gql.twitch.tv/gql`` с публичным Client-ID
  сайта (тот же, что у TwitchDownloader). Ключ API не нужен.
* Листание ТОЛЬКО по ``contentOffsetSeconds``: курсорные запросы (``cursor``/
  ``after``) Twitch теперь режет «failed integrity check». Страница — ~50–60
  сообщений вокруг запрошенной секунды; следующая страница запрашивается с
  последней секунды страницы (дубли отсекает уникальный индекс по id). Сверка с
  перебором «каждой секунды» дала то же число сообщений.
* Пакет ``chat-downloader`` для Twitch-VOD больше не работает (ходит курсором).

Сторонние смайлы (7TV, BTTV, FFZ: канал + глобальные) сводятся в карту
``имя → url`` на источник; родные смайлы Twitch приходят во фрагментах с id.

Модуль не тянет ``app.main``: всё работает с ``AppStore`` (``store.db``), а
сеть спрятана за ``TwitchChatClient`` — тесты подсовывают фейк.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from collections import Counter
from typing import Any, Callable, Iterable, Protocol

TWITCH_GQL_URL = "https://gql.twitch.tv/gql"
# Публичный Client-ID веб-сайта twitch.tv (его же используют плеер и TwitchDownloader).
TWITCH_WEB_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
COMMENTS_PERSISTED_HASH = "b70a3591ff0f4e0313d126c6a1502d79a1c02baebb288227c582044aa76adf6a"
TWITCH_EMOTE_URL = "https://static-cdn.jtvnw.net/emoticons/v2/{id}/default/dark/2.0"

# Цвета ника по умолчанию — как у Twitch для тех, кто цвет не выбирал.
DEFAULT_NAME_COLORS = (
    "#FF0000", "#0000FF", "#008000", "#B22222", "#FF7F50", "#9ACD32", "#FF4500",
    "#2E8B57", "#DAA520", "#D2691E", "#5F9EA0", "#1E90FF", "#FF69B4", "#8A2BE2", "#00FF7F",
)

_VOD_RE = re.compile(r"(?:twitch\.tv/(?:[^/]+/)?(?:videos?|v)/|^v?)(\d{6,})(?:[/?#]|$)", re.I)
_CLIP_RE = re.compile(r"(?:clips\.twitch\.tv/|twitch\.tv/[^/]+/clip/)([A-Za-z0-9_-]+)", re.I)


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


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


# ------------------------------------------------------------------------ разбор


def parse_comment(node: dict) -> dict | None:
    """Узел GQL → строка для БД (offset ещё в секундах VOD)."""
    if not node or node.get("contentOffsetSeconds") is None:
        return None
    message = node.get("message") or {}
    commenter = node.get("commenter") or {}
    fragments_raw = message.get("fragments") or []
    text = "".join(str(f.get("text") or "") for f in fragments_raw)
    fragments: list[dict] = []
    has_emote = False
    for frag in fragments_raw:
        piece = str(frag.get("text") or "")
        emote_id = ((frag.get("emote") or {}).get("emoteID")) or ""
        if emote_id:
            has_emote = True
            fragments.append({"text": piece, "emote_id": str(emote_id)})
        elif piece:
            fragments.append({"text": piece})
    badges = [
        f"{b['setID']}/{b.get('version') or '1'}"
        for b in message.get("userBadges") or []
        if b and b.get("setID")
    ]
    login = commenter.get("login") or ""
    user = commenter.get("displayName") or login or "аноним"
    return {
        "msg_id": str(node.get("id") or ""),
        "offset_sec": float(node["contentOffsetSeconds"]),
        "user_name": user,
        "user_login": login,
        "color": message.get("userColor") or "",
        "text": text,
        # Фрагменты храним, только если в сообщении есть родные смайлы: обычный
        # текст восстанавливается из ``text``, а сторонние смайлы — по карте.
        "fragments_json": json.dumps(fragments, ensure_ascii=False) if has_emote else "",
        "badges_json": json.dumps(badges) if badges else "",
    }


def default_color(user: str) -> str:
    seed = sum(ord(ch) for ch in (user or "").lower())
    return DEFAULT_NAME_COLORS[seed % len(DEFAULT_NAME_COLORS)]


def render_fragments(text: str, fragments_json: str, emotes: dict[str, str]) -> list[dict]:
    """Фрагменты для клиента: ``{"text"}`` и ``{"emote", "url"}`` по порядку."""
    raw: list[dict] = []
    if fragments_json:
        try:
            raw = json.loads(fragments_json) or []
        except ValueError:
            raw = []
    if not raw:
        raw = [{"text": text or ""}]
    out: list[dict] = []

    def push_text(piece: str) -> None:
        if not piece:
            return
        if out and "text" in out[-1] and "emote" not in out[-1]:
            out[-1]["text"] += piece
        else:
            out.append({"text": piece})

    for frag in raw:
        piece = str(frag.get("text") or "")
        if frag.get("emote_id"):
            out.append({"emote": piece, "url": TWITCH_EMOTE_URL.format(id=frag["emote_id"])})
            continue
        if not emotes:
            push_text(piece)
            continue
        for token in re.split(r"(\s+)", piece):
            if token and token in emotes:
                out.append({"emote": token, "url": emotes[token]})
            else:
                push_text(token)
    return out


# ------------------------------------------------------------------------ хранение


CHAT_SCHEMA_SQL = (
    """
    CREATE TABLE IF NOT EXISTS source_chat (
        source_id INTEGER PRIMARY KEY REFERENCES sources(id) ON DELETE CASCADE,
        platform TEXT NOT NULL DEFAULT 'twitch',
        vod_id TEXT NOT NULL DEFAULT '',
        vod_start_sec REAL NOT NULL DEFAULT 0,
        channel_id TEXT NOT NULL DEFAULT '',
        channel_login TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'idle',
        progress REAL NOT NULL DEFAULT 0,
        message_count INTEGER NOT NULL DEFAULT 0,
        error TEXT NOT NULL DEFAULT '',
        emotes_json TEXT NOT NULL DEFAULT '{}',
        badges_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        finished_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
        msg_id TEXT NOT NULL DEFAULT '',
        offset_sec REAL NOT NULL,
        user_name TEXT NOT NULL DEFAULT '',
        user_login TEXT NOT NULL DEFAULT '',
        color TEXT NOT NULL DEFAULT '',
        text TEXT NOT NULL DEFAULT '',
        fragments_json TEXT NOT NULL DEFAULT '',
        badges_json TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_chat_messages_source ON chat_messages(source_id, offset_sec)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_messages_msg ON chat_messages(source_id, msg_id)",
)


def ensure_chat_schema(conn) -> None:
    """Таблицы чата (вызывается из ``Database.init`` — и на всякий случай здесь)."""
    for sql in CHAT_SCHEMA_SQL:
        conn.execute(sql)


_schema_ready: set[str] = set()


def _db(store):
    db = store.db
    key = str(getattr(db, "path", id(db)))
    if key not in _schema_ready:
        with db._lock, db.connect() as conn:
            ensure_chat_schema(conn)
        _schema_ready.add(key)
    return db


def get_chat_status(store, source_id: int) -> dict:
    row = _db(store).query_one("SELECT * FROM source_chat WHERE source_id = ?", (source_id,))
    if not row:
        return {"source_id": source_id, "status": "none", "message_count": 0, "progress": 0.0, "error": ""}
    data = dict(row)
    data.pop("emotes_json", None)
    data.pop("badges_json", None)
    # «Идёт», но потока нет — процесс перезапускали посреди импорта.
    if data["status"] in {"queued", "running"} and source_id not in _ACTIVE:
        data["status"] = "failed"
        data["error"] = data.get("error") or "импорт прерван (перезапуск приложения) — запустите заново"
    return data


def _upsert_status(store, source_id: int, **fields: Any) -> None:
    db = _db(store)
    if not db.query_one("SELECT 1 FROM source_chat WHERE source_id = ?", (source_id,)):
        db.execute("INSERT INTO source_chat (source_id) VALUES (?)", (source_id,))
    if fields:
        sets = ", ".join(f"{key} = ?" for key in fields)
        db.execute(
            f"UPDATE source_chat SET {sets}, updated_at = CURRENT_TIMESTAMP WHERE source_id = ?",
            (*fields.values(), source_id),
        )


def chat_emotes(store, source_id: int) -> dict[str, str]:
    row = _db(store).query_one("SELECT emotes_json FROM source_chat WHERE source_id = ?", (source_id,))
    return _loads(row["emotes_json"], {}) if row else {}


def chat_badges(store, source_id: int) -> dict[str, str]:
    row = _db(store).query_one("SELECT badges_json FROM source_chat WHERE source_id = ?", (source_id,))
    return _loads(row["badges_json"], {}) if row else {}


def has_chat(store, source_id: int) -> bool:
    try:
        row = _db(store).query_one("SELECT 1 FROM chat_messages WHERE source_id = ? LIMIT 1", (source_id,))
    except Exception:  # noqa: BLE001
        return False
    return bool(row)


def _loads(text: str, default: Any) -> Any:
    try:
        return json.loads(text) if text else default
    except ValueError:
        return default


def insert_messages(store, source_id: int, rows: Iterable[dict], shift_sec: float = 0.0) -> int:
    """Записать сообщения (дубли по ``msg_id`` молча отбрасываются)."""
    db = _db(store)
    params = [
        (
            source_id,
            r["msg_id"],
            round(float(r["offset_sec"]) - shift_sec, 3),
            r.get("user_name") or "",
            r.get("user_login") or "",
            r.get("color") or "",
            r.get("text") or "",
            r.get("fragments_json") or "",
            r.get("badges_json") or "",
        )
        for r in rows
    ]
    if not params:
        return 0
    with db._lock, db.connect() as conn:
        before = conn.total_changes
        conn.executemany(
            """
            INSERT OR IGNORE INTO chat_messages
                (source_id, msg_id, offset_sec, user_name, user_login, color, text, fragments_json, badges_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            params,
        )
        return conn.total_changes - before


def delete_chat(store, source_id: int) -> None:
    db = _db(store)
    db.execute("DELETE FROM chat_messages WHERE source_id = ?", (source_id,))
    db.execute("DELETE FROM source_chat WHERE source_id = ?", (source_id,))


def _message_out(row, emotes: dict[str, str], badges: dict[str, str], t: float) -> dict:
    user = row["user_name"] or row["user_login"] or ""
    badge_ids = _loads(row["badges_json"], []) if row["badges_json"] else []
    return {
        "t": round(t, 3),
        "user": user,
        "color": row["color"] or default_color(row["user_login"] or user),
        "text": row["text"],
        "fragments": render_fragments(row["text"], row["fragments_json"], emotes),
        "badges": [{"id": b, "url": badges[b]} for b in badge_ids if b in badges],
    }


def list_messages(
    store,
    source_id: int,
    start: float | None = None,
    end: float | None = None,
    limit: int = 500,
) -> list[dict]:
    """Сообщения источника по времени исходника (``t`` = секунда исходника)."""
    db = _db(store)
    sql = "SELECT * FROM chat_messages WHERE source_id = ?"
    params: list[Any] = [source_id]
    if start is not None:
        sql += " AND offset_sec >= ?"
        params.append(float(start))
    if end is not None:
        sql += " AND offset_sec < ?"
        params.append(float(end))
    sql += " ORDER BY offset_sec, id LIMIT ?"
    params.append(max(1, min(int(limit), 20000)))
    emotes, badges = chat_emotes(store, source_id), chat_badges(store, source_id)
    return [_message_out(row, emotes, badges, float(row["offset_sec"])) for row in db.query_all(sql, params)]


# ------------------------------------------------------------------- клип → время


def clip_piece_starts(plan: dict) -> tuple[list[tuple[float, float]], list[float]]:
    """Куски клипа и где каждый начинается на таймлайне готового клипа.

    Как у моушн-слоёв (``app.motion.clip_timeline``): переходы съедают свой
    overlap на склейках. Без модуля — просто встык, как ``pieceBounds`` в
    ClipTimeline.tsx.
    """
    pieces = [
        (float(s["start_sec"]), float(s["end_sec"]))
        for s in plan.get("segments") or []
        if float(s.get("end_sec") or 0) > float(s.get("start_sec") or 0)
    ]
    try:
        from app.clip_settings import normalize_render_settings
        from app.motion import clip_timeline

        transition = normalize_render_settings(plan.get("render_settings") or {}).get("transition")
        starts, _duration = clip_timeline(pieces, transition)
        if len(starts) == len(pieces):
            return pieces, starts
    except Exception:  # noqa: BLE001 - запасной путь: встык
        pass
    starts, t = [], 0.0
    for start, end in pieces:
        starts.append(t)
        t += end - start
    return pieces, starts


def clip_messages(store, plan: dict, limit: int = 5000) -> list[dict]:
    """Сообщения, попавшие в куски клипа, со временем КЛИПА (``t``)."""
    db = _db(store)
    source_id = int(plan["source_id"])
    emotes, badges = chat_emotes(store, source_id), chat_badges(store, source_id)
    pieces, starts = clip_piece_starts(plan)
    out: list[dict] = []
    for (start, end), clip_start in zip(pieces, starts):
        rows = db.query_all(
            "SELECT * FROM chat_messages WHERE source_id = ? AND offset_sec >= ? AND offset_sec < ? "
            "ORDER BY offset_sec, id LIMIT ?",
            (source_id, start, end, max(1, int(limit))),
        )
        out.extend(_message_out(row, emotes, badges, clip_start + float(row["offset_sec"]) - start) for row in rows)
    out.sort(key=lambda m: m["t"])
    return out[: max(1, int(limit))]


# ------------------------------------------------------------------- активность


# Реакции, которые означают «тут что-то случилось»: смех, вау, плюсы/W.
HYPE_TOKENS = {
    t.lower()
    for t in (
        "KEKW LUL LULW OMEGALUL KEKL ICANT pepeLaugh LMAO LMFAO LOL xD XD KEK "
        "PogChamp Pog POGGERS PogU POGGIES PagMan PagChomp W WW WWW + ++ +++ "
        "GG EZ Clap monkaS monkaW D: WAYTOODANK Kreygasm OOOO ? ?? ??? !!! "
        "ЛОЛ КЕК ОРУ ЖЕСТЬ ВАУ ИМБА КРАСАВА БАЗА ГГ АХАХ ХАХА"
    ).split()
}
_LAUGH_RE = re.compile(r"^(?:[аa]?[хx][аa]){2,}|^(?:[аa][хx]){2,}|^(?:ha){2,}|^(?:хе){2,}|^\++$|^w+$", re.I)

_activity_cache: dict[tuple, dict] = {}
_activity_lock = threading.Lock()


def _hype_token(token: str) -> bool:
    low = token.lower()
    return low in HYPE_TOKENS or bool(_LAUGH_RE.match(low))


def _is_caps(text: str) -> bool:
    letters = [ch for ch in text if ch.isalpha()]
    if len(letters) < 4:
        return False
    return sum(1 for ch in letters if ch.isupper()) / len(letters) >= 0.7


def score_activity(
    messages: Iterable[tuple[float, str]],
    bucket: float = 10.0,
    duration: float = 0.0,
    emote_names: Iterable[str] = (),
) -> dict:
    """Сообщения/бакет и нормированный «хайп» 0..1.

    hype ∝ (частота / локальная медиана ±5 мин) × (1 + 1.5·доля реакций +
    0.5·доля капса) × count/(count+медиана) — последний множитель гасит
    «всплеск» из двух сообщений в тихом чате.
    """
    bucket = max(1.0, float(bucket))
    emote_set = set(emote_names)
    counts: dict[int, int] = {}
    hype_msgs: dict[int, int] = {}
    caps_msgs: dict[int, int] = {}
    tokens: dict[int, Counter] = {}
    last = 0
    for offset, text in messages:
        idx = int(max(0.0, float(offset)) // bucket)
        last = max(last, idx)
        counts[idx] = counts.get(idx, 0) + 1
        words = (text or "").split()
        reacts = [w for w in words if w in emote_set or _hype_token(w)]
        if reacts:
            hype_msgs[idx] = hype_msgs.get(idx, 0) + 1
            tokens.setdefault(idx, Counter()).update(set(reacts))
        if _is_caps(text or ""):
            caps_msgs[idx] = caps_msgs.get(idx, 0) + 1
    n = max(last + 1, int(math.ceil(float(duration or 0) / bucket)) if duration else 0)
    if not counts:
        return {"bucket": bucket, "total": 0, "buckets": []}
    series = [counts.get(i, 0) for i in range(n)]
    global_median = max(1.0, _median([c for c in series if c > 0]))
    half = max(3, int(round(300 / bucket)))  # ±5 минут
    raw: list[float] = []
    for i, count in enumerate(series):
        if not count:
            raw.append(0.0)
            continue
        window = series[max(0, i - half) : i + half + 1]
        baseline = max(1.0, _median(window), global_median * 0.5)
        ratio = count / baseline
        react = hype_msgs.get(i, 0) / count
        caps = caps_msgs.get(i, 0) / count
        damp = count / (count + global_median)
        raw.append(ratio * (1 + 1.5 * react + 0.5 * caps) * damp)
    peak = max(raw) or 1.0
    buckets = []
    for i, count in enumerate(series):
        top, top_count = "", 0
        if i in tokens:
            top, top_count = tokens[i].most_common(1)[0]
        buckets.append(
            {
                "t": round(i * bucket, 3),
                "count": count,
                "hype": round(raw[i] / peak, 4),
                "reactions": hype_msgs.get(i, 0),
                "caps": caps_msgs.get(i, 0),
                "top": top,
                "top_count": top_count,
            }
        )
    return {"bucket": bucket, "total": sum(series), "buckets": buckets}


def _median(values: list[float]) -> float:
    vals = sorted(values)
    if not vals:
        return 0.0
    mid = len(vals) // 2
    return float(vals[mid]) if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2.0


def chat_activity(store, source_id: int, bucket: float = 10.0) -> dict:
    """Активность чата источника (кэш в памяти до смены числа сообщений)."""
    db = _db(store)
    count_row = db.query_one("SELECT COUNT(*) AS n FROM chat_messages WHERE source_id = ?", (source_id,))
    total = int(count_row["n"]) if count_row else 0
    duration = 0.0
    try:
        duration = float(store.get_source(source_id).get("duration_sec") or 0)
    except Exception:  # noqa: BLE001
        pass
    key = (str(db.path), source_id, float(bucket), total)
    with _activity_lock:
        cached = _activity_cache.get(key)
    if cached is not None:
        return cached
    emote_names = list(chat_emotes(store, source_id).keys())
    # Идём курсором, не fetchall: в 4-часовом VOD сотни тысяч сообщений.
    with db._lock:
        conn = db.connect()
        try:
            cur = conn.execute(
                "SELECT offset_sec, text FROM chat_messages WHERE source_id = ? ORDER BY offset_sec", (source_id,)
            )
            result = score_activity(((r[0], r[1]) for r in cur), bucket, duration, emote_names)
        finally:
            conn.close()
    result["source_id"] = source_id
    with _activity_lock:
        for stale in [k for k in _activity_cache if k[:2] == key[:2]]:
            _activity_cache.pop(stale, None)
        _activity_cache[key] = result
    return result


def hype_windows_from_activity(
    activity: dict, top: int = 20, min_gap: float = 45.0, min_hype: float = 0.2
) -> list[dict]:
    """Самые горячие окна: пики хайпа ≥ ``min_hype`` (не ближе ``min_gap`` друг
    к другу), расширенные на соседние бакеты не ниже половины пика (до ±60 с);
    окна не перекрываются — более горячее забирает бакеты первым."""
    buckets = activity.get("buckets") or []
    size = float(activity.get("bucket") or 10.0)
    order = sorted(range(len(buckets)), key=lambda i: (-buckets[i]["hype"], i))
    picked: list[int] = []
    for i in order:
        if len(picked) >= max(1, int(top)) or buckets[i]["hype"] < min_hype or not buckets[i]["count"]:
            break
        if all(abs(buckets[i]["t"] - buckets[j]["t"]) >= min_gap for j in picked):
            picked.append(i)
    reach = max(1, int(60 // size))
    claimed: set[int] = set(picked)
    windows = []
    for i in picked:
        level = buckets[i]["hype"] * 0.5
        lo = hi = i
        while lo - 1 >= 0 and i - (lo - 1) <= reach and lo - 1 not in claimed and buckets[lo - 1]["hype"] >= level:
            lo -= 1
        while (
            hi + 1 < len(buckets)
            and (hi + 1) - i <= reach
            and hi + 1 not in claimed
            and buckets[hi + 1]["hype"] >= level
        ):
            hi += 1
        claimed.update(range(lo, hi + 1))
        span = buckets[lo : hi + 1]
        tokens: Counter = Counter()
        for b in span:
            if b["top"]:
                tokens[b["top"]] += b["top_count"]
        top_token, top_count = tokens.most_common(1)[0] if tokens else ("", 0)
        start, end = buckets[lo]["t"], buckets[hi]["t"] + size
        label = f"{format_ts(start)}–{format_ts(end)} ({sum(b['count'] for b in span)} сообщ."
        label += f", {top_token} x{top_count})" if top_token else ")"
        windows.append(
            {
                "start": start,
                "end": end,
                "peak_t": buckets[i]["t"],
                "hype": buckets[i]["hype"],
                "count": sum(b["count"] for b in span),
                "top": top_token,
                "top_count": top_count,
                "label": label,
            }
        )
    return windows


def chat_hype_windows(store, source_id: int, top: int = 20, bucket: float = 10.0) -> list[dict]:
    """Самые горячие окна чата источника (по убыванию хайпа). Нет чата — []."""
    if not has_chat(store, source_id):
        return []
    return hype_windows_from_activity(chat_activity(store, source_id, bucket), top=top)


def chat_prompt_block(store, source_id: int, top: int = 15) -> str:
    """Короткий блок для промпта анализа; пусто, если чата нет или он выключен."""
    try:
        if not bool(store.get_app_setting_value("analysis_use_chat", True)):
            return ""
        windows = chat_hype_windows(store, source_id, top=top)
    except Exception:  # noqa: BLE001 - чат — подсказка, анализ без него не падает
        return ""
    if not windows:
        return ""
    lines = [
        "Чат зрителей (Twitch): всплески активности. Зрители реагируют с задержкой "
        "~5–20 с, поэтому само событие обычно чуть РАНЬШЕ всплеска. Это подсказка, "
        "а не приказ: бери момент, только если он и на видео сильный."
    ]
    for w in sorted(windows, key=lambda w: w["start"]):
        lines.append(f"- {w['label']}, хайп {int(round(w['hype'] * 100))}%")
    return "\n".join(lines)


def format_ts(sec: float) -> str:
    sec = int(max(0, sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# ------------------------------------------------------------------------ импорт


_ACTIVE: set[int] = set()
_ACTIVE_LOCK = threading.Lock()


def resolve_vod(client: ChatClient, source: dict, vod: str = "", vod_start_sec: float | None = None) -> dict:
    """Какой VOD и с какой секунды соответствует нулю исходника."""
    ref = parse_twitch_vod_ref(vod) or parse_twitch_vod_ref(source.get("original_url") or "")
    if not ref:
        raise ValueError("у источника нет ссылки на Twitch VOD — передайте vod (ссылку или id)")
    start = 0.0
    if ref.get("clip_slug"):
        clip = client.clip_info(ref["clip_slug"])
        if not clip:
            raise LookupError("клип Twitch не найден или у него нет VOD")
        ref = {"vod_id": clip["vod_id"]}
        start = float(clip["start_sec"])
    if vod_start_sec is not None:
        start = max(0.0, float(vod_start_sec))
    return {"vod_id": ref["vod_id"], "vod_start_sec": start}


def import_chat(
    store,
    source_id: int,
    *,
    client: ChatClient | None = None,
    vod: str = "",
    vod_start_sec: float | None = None,
    workers: int | None = None,
) -> dict:
    """Скачать весь чат VOD в БД (синхронно). Возвращает статус."""
    own_client = client is None
    client = client or TwitchChatClient()
    try:
        source = store.get_source(source_id)
        target = resolve_vod(client, source, vod, vod_start_sec)
        vod_id, shift = target["vod_id"], target["vod_start_sec"]
        info = client.video_info(vod_id)
        if not info:
            raise LookupError(f"VOD {vod_id} недоступен: удалён, скрыт или только для подписчиков")
        start = shift
        end = float(info.get("length_sec") or 0)
        src_duration = float(source.get("duration_sec") or 0)
        if src_duration > 0:
            end = min(end, shift + src_duration + 1) if end > 0 else shift + src_duration + 1
        if end <= start:
            end = start + max(src_duration, 1.0)
        db = _db(store)
        db.execute("DELETE FROM chat_messages WHERE source_id = ?", (source_id,))
        _upsert_status(
            store,
            source_id,
            vod_id=vod_id,
            vod_start_sec=shift,
            channel_id=info.get("channel_id") or "",
            channel_login=info.get("channel_login") or "",
            status="running",
            progress=0.0,
            message_count=0,
            error="",
            finished_at=None,
        )
        channel_id = info.get("channel_id") or ""
        try:
            emotes = client.third_party_emotes(channel_id)
        except Exception:  # noqa: BLE001
            emotes = {}
        try:
            badges = client.badges(channel_id)
        except Exception:  # noqa: BLE001
            badges = {}
        _upsert_status(
            store,
            source_id,
            emotes_json=json.dumps(emotes, ensure_ascii=False),
            badges_json=json.dumps(badges, ensure_ascii=False),
        )
        total = _crawl(store, source_id, client, vod_id, start, end, shift, workers)
        _upsert_status(
            store,
            source_id,
            status="succeeded",
            progress=1.0,
            message_count=total,
            finished_at=time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
        )
    except Exception as exc:  # noqa: BLE001 - ошибка — часть статуса импорта
        try:
            store.get_source(source_id)
        except KeyError:
            raise  # источник удалили посреди импорта — писать некуда
        _upsert_status(store, source_id, status="failed", error=str(exc)[:500])
    finally:
        if own_client:
            try:
                client.close()  # type: ignore[union-attr]
            except Exception:  # noqa: BLE001
                pass
    return get_chat_status(store, source_id)


def _crawl(store, source_id: int, client: ChatClient, vod_id: str, start: float, end: float, shift: float, workers: int | None) -> int:
    """Параллельный обход по времени: N потоков, каждый — свой отрезок VOD."""
    n = max(1, min(int(workers or _env_int("TWITCH_CHAT_WORKERS", 4)), 8))
    span = end - start
    if span < 600:
        n = 1
    edges = [start + span * k / n for k in range(n + 1)]
    covered = [0.0] * n
    errors: list[BaseException] = []
    lock = threading.Lock()
    last_report = [0.0]

    def report() -> None:
        now = time.monotonic()
        with lock:
            if now - last_report[0] < 2.0:
                return
            last_report[0] = now
            done = sum(covered) / span if span > 0 else 0.0
        count_row = _db(store).query_one("SELECT COUNT(*) AS n FROM chat_messages WHERE source_id = ?", (source_id,))
        _upsert_status(store, source_id, progress=round(min(0.99, done), 4), message_count=int(count_row["n"]))

    def run(k: int, a: float, b: float) -> None:
        try:
            offset = int(math.floor(a))
            prev_ids: set[str] = set()
            while offset < b:
                nodes, has_next = client.comments_page(vod_id, offset)
                if not nodes:
                    break
                rows = [r for r in (parse_comment(node) for node in nodes) if r and start <= r["offset_sec"] <= end]
                insert_messages(store, source_id, rows, shift_sec=shift)
                if not has_next:
                    break
                ids = {str(node.get("id") or "") for node in nodes}
                newest = int(max(float(node.get("contentOffsetSeconds") or 0) for node in nodes))
                # Следующая страница — с последней секунды этой (дубли отсечёт
                # уникальный индекс); ничего нового или стоим на месте — шаг +1 с.
                offset = newest if (ids - prev_ids and newest > offset) else offset + 1
                prev_ids = ids
                with lock:
                    covered[k] = min(b, max(a, offset)) - a
                report()
            with lock:
                covered[k] = b - a
        except BaseException as exc:  # noqa: BLE001 - поднимем в основном потоке
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(k, edges[k], edges[k + 1]), daemon=True) for k in range(n)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if errors:
        raise errors[0]
    row = _db(store).query_one("SELECT COUNT(*) AS n FROM chat_messages WHERE source_id = ?", (source_id,))
    return int(row["n"]) if row else 0


class ChatImportService:
    """Фоновые импорты (по потоку на источник). Повторный запуск того же
    источника, пока идёт прежний, просто возвращает текущий статус."""

    def __init__(self, get_store: Callable[[], Any], client_factory: Callable[[], ChatClient] | None = None) -> None:
        self.get_store = get_store
        self.client_factory = client_factory

    def start(self, source_id: int, *, vod: str = "", vod_start_sec: float | None = None, wait: bool = False) -> dict:
        store = self.get_store()
        source = store.get_source(source_id)  # KeyError → 404
        # Проверим ссылку сразу, чтобы ошибка пришла в ответе, а не в статусе.
        if not parse_twitch_vod_ref(vod) and not parse_twitch_vod_ref(source.get("original_url") or ""):
            raise ValueError("у источника нет ссылки на Twitch VOD — передайте vod (ссылку или id)")
        with _ACTIVE_LOCK:
            if source_id in _ACTIVE:
                return get_chat_status(store, source_id)
            _ACTIVE.add(source_id)
        _upsert_status(store, source_id, status="queued", error="", progress=0.0)

        def job() -> None:
            try:
                client = self.client_factory() if self.client_factory else None
                import_chat(store, source_id, client=client, vod=vod, vod_start_sec=vod_start_sec)
            except Exception:  # noqa: BLE001 - статус уже записан (или источника нет)
                pass
            finally:
                with _ACTIVE_LOCK:
                    _ACTIVE.discard(source_id)

        if wait:
            job()
        else:
            threading.Thread(target=job, name=f"chat-import-{source_id}", daemon=True).start()
        return get_chat_status(store, source_id)

    def auto_import(self, source: dict) -> None:
        """Хук ``SourceIngestor.on_source_created``: Twitch-источник → чат в фоне."""
        if not source or source.get("status") == "failed":
            return
        if not _env_flag("TWITCH_CHAT_AUTO_IMPORT", True):
            return
        if not parse_twitch_vod_ref(source.get("original_url") or ""):
            return
        try:
            if not bool(self.get_store().get_app_setting_value("chat_auto_import", True)):
                return
            self.start(int(source["id"]))
        except Exception:  # noqa: BLE001 - чат не должен ломать загрузку источника
            pass
