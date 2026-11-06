"""Чат Twitch: разбор, хранение, время клипа (из ``app/twitch_chat.py``)."""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

TWITCH_EMOTE_URL = "https://static-cdn.jtvnw.net/emoticons/v2/{id}/default/dark/2.0"

# Цвета ника по умолчанию — как у Twitch для тех, кто цвет не выбирал.
DEFAULT_NAME_COLORS = (
    "#FF0000", "#0000FF", "#008000", "#B22222", "#FF7F50", "#9ACD32", "#FF4500",
    "#2E8B57", "#DAA520", "#D2691E", "#5F9EA0", "#1E90FF", "#FF69B4", "#8A2BE2", "#00FF7F",
)


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
        with db.transaction() as conn:
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
    with db.transaction() as conn:
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


# ------------------------------------------------------------------------ импорт


_ACTIVE: set[int] = set()
