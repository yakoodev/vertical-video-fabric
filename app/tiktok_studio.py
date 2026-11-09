"""Аналитика роликов из TikTok Studio: удержание, досмотры, среднее время просмотра, источники, подписки.

Публичные числа (просмотры, лайки) сервис берёт через yt-dlp (``app/platform_stats.py``). Как долго смотрят
и откуда пришли зрители, видно только владельцу аккаунта — в TikTok Studio. Сбор (``app/tiktok_studio_collect.py``)
открывает страницу аналитики ролика в Chromium с cookie и прокси аккаунта (``node/tiktok-studio.cjs``) и
забирает ответ ``/aweme/v2/data/insight/``. Здесь — разбор этого ответа, хранение снимков и короткие сводки
для экрана клипа, вкладки «Статистика» проекта и агентов («половина уходит за 2 секунды»).

Снимок лежит у публикации: ``target_id`` — публикация сервиса (``job_targets``), ``publication_id`` — выложенное
руками (``project_publications``). Один ролик TikTok читается только cookie аккаунта, которому он принадлежит.
"""

from __future__ import annotations

import json
import re
import statistics
from typing import Any
from urllib.parse import urlsplit

# Удержание: первые секунды — по одной (хук, тут уходит больше всего), дальше реже.
RETENTION_FINE_SEC = 15
RETENTION_COARSE_STEP = 5
HOOK_POINTS = (1, 2, 3, 5)

SOURCE_LABELS = {
    "For You": "рекомендации",
    "Personal Profile": "профиль",
    "Follow": "подписки",
    "Following": "подписки",
    "Search": "поиск",
    "Sound": "звук",
    "Direct Message": "личные сообщения",
    "Others": "другое",
}

# Как читать поля studio — агентам рядом с числами (бриф проекта, get_project_stats).
STUDIO_LEGEND = ("studio — TikTok Studio: hook — доля зрителей, что ещё смотрят на 1/2/3/5-й секунде; "
                 "half_gone_sec — к какой секунде ушла половина; watch_share — средний просмотр от длины ролика; "
                 "finish_rate — досмотрели до конца; main_source — откуда пришло больше всего зрителей.")

SCHEMA_SQL = (
    # Снимок аналитики TikTok Studio публикации: ровно одно из target_id / publication_id.
    # retention_json — [[секунда, доля 0..1], …] (по секунде до 15 с, дальше через 5 с);
    # traffic_json — [[источник как у TikTok, доля], …] по убыванию; error — снимок не удался, числа NULL.
    """
    CREATE TABLE IF NOT EXISTS tiktok_studio_stats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        target_id INTEGER REFERENCES job_targets(id) ON DELETE CASCADE,
        publication_id INTEGER REFERENCES project_publications(id) ON DELETE CASCADE,
        account_id INTEGER,
        video_id TEXT NOT NULL DEFAULT '',
        captured_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        views INTEGER,
        avg_watch_sec REAL,
        duration_sec REAL,
        finish_rate REAL,
        total_play_sec REAL,
        new_followers INTEGER,
        traffic_json TEXT NOT NULL DEFAULT '[]',
        retention_json TEXT NOT NULL DEFAULT '[]',
        error TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tiktok_studio_target ON tiktok_studio_stats(target_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_tiktok_studio_pub ON tiktok_studio_stats(publication_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_tiktok_studio_video ON tiktok_studio_stats(video_id, id)",
)


def ensure_schema(conn: Any) -> None:
    for sql in SCHEMA_SQL:
        conn.execute(sql)


# --------------------------------------------------------------------------- #
# Ссылки
# --------------------------------------------------------------------------- #

_VIDEO_RE = re.compile(r"/(?:video|photo)/(\d{6,30})")
_HANDLE_RE = re.compile(r"^/@([^/?#]+)")


def video_id_of(url: str) -> str:
    """Числовой id ролика из ссылки TikTok (``/@ник/video/<id>``; короткие vm.tiktok.com — нет)."""
    if not (urlsplit(url or "").hostname or "").lower().endswith("tiktok.com"):
        return ""
    found = _VIDEO_RE.search(urlsplit(url).path)
    return found.group(1) if found else ""


def normalize_handle(handle: Any) -> str:
    return str(handle or "").strip().lstrip("@").strip().lower()


def handle_of(url: str) -> str:
    """Ник из ссылки ``https://www.tiktok.com/@ник/video/…``; у ссылок сервиса (``/@/video/…``) — пусто."""
    found = _HANDLE_RE.match(urlsplit(url or "").path)
    return normalize_handle(found.group(1)) if found else ""


# --------------------------------------------------------------------------- #
# Разбор ответа insight
# --------------------------------------------------------------------------- #


def _unwrap(node: Any) -> Any:
    """Числа TikTok завёрнуты: ``{"value": {"status": 0, "value": X}}`` или ``{"status": 0, "value": X}``.
    status ≠ 0 — данных нет (None)."""
    for _ in range(4):
        if not isinstance(node, dict):
            return node
        if "status" in node and node.get("status") not in (0, None):
            return None
        if "value" not in node:
            return node
        node = node["value"]
    return node


def _number(node: Any) -> float | None:
    value = _unwrap(node)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _share(value: Any) -> float | None:
    try:
        share = float(value)
    except (TypeError, ValueError):
        return None
    return round(max(0.0, min(1.0, share)), 4)


def downsample_retention(points: list[tuple[float, float]], duration_sec: float | None = None) -> list[list[float]]:
    """[[секунда, доля], …]: до RETENTION_FINE_SEC — каждая секунда, дальше — каждые RETENTION_COARSE_STEP
    и последняя точка ролика."""
    points = sorted((float(sec), float(share)) for sec, share in points)
    if not points:
        return []
    out: list[list[float]] = []
    last_sec = points[-1][0]
    for sec, share in points:
        whole = abs(sec - round(sec)) < 0.05
        fine = sec <= RETENTION_FINE_SEC and whole
        coarse = whole and int(round(sec)) % RETENTION_COARSE_STEP == 0
        if fine or coarse or sec == last_sec:
            out.append([round(sec, 1), round(share, 4)])
    if duration_sec and out and out[-1][0] > duration_sec + 1:
        out = [p for p in out if p[0] <= duration_sec + 1] or out[:1]
    return out


def _retention(node: Any) -> list[tuple[float, float]]:
    value = node.get("value") if isinstance(node, dict) else None
    items = value.get("list") if isinstance(value, dict) else None
    out = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        try:
            sec = float(item.get("timestamp")) / 1000.0
        except (TypeError, ValueError):
            continue
        share = _share(item.get("value"))
        if share is not None and sec >= 0:
            out.append((sec, share))
    return out


def _traffic(node: Any) -> list[list]:
    items = _unwrap(node)
    out = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not item.get("key"):
            continue
        share = _share(item.get("value"))
        if share:
            out.append([str(item["key"])[:60], share])
    out.sort(key=lambda pair: -pair[1])
    return out


def parse_insight(raw: dict) -> dict:
    """Ответ insight (поля, что оставил node/tiktok-studio.cjs) → числа снимка. Нет ни одного числа — ValueError."""
    raw = raw if isinstance(raw, dict) else {}
    info = raw.get("video_info") if isinstance(raw.get("video_info"), dict) else {}
    duration_ms = _number(info.get("duration_ms"))
    duration = round(duration_ms / 1000.0, 2) if duration_ms else None
    views = _number(raw.get("realtime_total_video_views"))
    followers = _number(raw.get("video_new_followers"))
    if followers is None:
        followers = _number(raw.get("realtime_new_followers"))
    finish = _number(raw.get("video_finish_rate_realtime"))
    avg_watch = _number(raw.get("video_per_duration_realtime"))
    total_play = _number(raw.get("video_total_duration_realtime"))
    parsed = {
        "video_id": str(info.get("aweme_id") or ""),
        "author": normalize_handle(info.get("author")),
        "views": int(views) if views is not None else None,
        "avg_watch_sec": round(avg_watch, 2) if avg_watch is not None else None,
        "duration_sec": duration,
        "finish_rate": _share(finish) if finish is not None else None,
        "total_play_sec": round(total_play, 1) if total_play is not None else None,
        "new_followers": int(followers) if followers is not None else None,
        "traffic": _traffic(raw.get("video_traffic_source_percent_realtime")),
        "retention": downsample_retention(_retention(raw.get("video_retention_rate_realtime")), duration),
    }
    numbers = ("views", "avg_watch_sec", "finish_rate", "total_play_sec", "new_followers")
    if all(parsed[key] is None for key in numbers) and not parsed["retention"]:
        raise ValueError("TikTok Studio не отдал чисел")
    return parsed


# --------------------------------------------------------------------------- #
# Хранение
# --------------------------------------------------------------------------- #


def add_snapshot(store: Any, *, target_id: int | None = None, publication_id: int | None = None,
                 account_id: int | None = None, video_id: str = "", parsed: dict | None = None,
                 error: str = "") -> dict:
    """Снимок у публикации. Та же ошибка, что в прошлый раз, — новой строки нет, сдвигается время попытки."""
    if (target_id is None) == (publication_id is None):
        raise ValueError("нужна ровно одна публикация: target_id или publication_id")
    column, ref = ("target_id", int(target_id)) if target_id is not None else ("publication_id", int(publication_id))
    error = str(error or "")[:300]
    if error:
        last = store.db.query_one(f"SELECT id, error FROM tiktok_studio_stats WHERE {column} = ? ORDER BY id DESC LIMIT 1",
                                  (ref,))
        if last and last["error"] == error:
            store.db.execute("UPDATE tiktok_studio_stats SET captured_at = CURRENT_TIMESTAMP WHERE id = ?", (last["id"],))
            return dict(store.db.query_one("SELECT * FROM tiktok_studio_stats WHERE id = ?", (last["id"],)))
    parsed = parsed or {}
    cur = store.db.execute(
        f"""INSERT INTO tiktok_studio_stats ({column}, account_id, video_id, views, avg_watch_sec, duration_sec,
                                             finish_rate, total_play_sec, new_followers, traffic_json, retention_json,
                                             error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (ref, account_id, str(video_id or ""), parsed.get("views"), parsed.get("avg_watch_sec"),
         parsed.get("duration_sec"), parsed.get("finish_rate"), parsed.get("total_play_sec"),
         parsed.get("new_followers"), json.dumps(parsed.get("traffic") or [], ensure_ascii=False),
         json.dumps(parsed.get("retention") or []), error),
    )
    return dict(store.db.query_one("SELECT * FROM tiktok_studio_stats WHERE id = ?", (cur.lastrowid,)))


def _loads(text: Any) -> list:
    try:
        value = json.loads(text or "[]")
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def latest_snapshots(store: Any) -> dict[tuple[str, int], dict]:
    """("service", target_id) / ("manual", publication_id) → последний удачный снимок + время и ошибка
    последней попытки (``last_attempt_at``, ``error``)."""
    out: dict[tuple[str, int], dict] = {}
    for column, kind in (("target_id", "service"), ("publication_id", "manual")):
        for row in store.db.query_all(
            f"""SELECT s.* FROM tiktok_studio_stats s JOIN (
                  SELECT {column} AS ref, MAX(id) AS mid FROM tiktok_studio_stats
                  WHERE {column} IS NOT NULL AND error = '' GROUP BY {column}) m ON s.id = m.mid"""
        ):
            data = dict(row)
            data["traffic"] = _loads(data.pop("traffic_json", "[]"))
            data["retention"] = _loads(data.pop("retention_json", "[]"))
            out[(kind, int(data[column]))] = data
        for row in store.db.query_all(
            f"""SELECT s.{column} AS ref, s.captured_at, s.error FROM tiktok_studio_stats s JOIN (
                  SELECT {column} AS ref, MAX(id) AS mid FROM tiktok_studio_stats
                  WHERE {column} IS NOT NULL GROUP BY {column}) m ON s.id = m.mid"""
        ):
            entry = out.setdefault((kind, int(row["ref"])), {"captured_at": None})
            entry["last_attempt_at"] = row["captured_at"]
            entry["error"] = row["error"] or ""
    return out


def last_attempts(store: Any) -> dict[tuple[str, int], str]:
    """Когда по публикации последний раз ходили в Studio (удачно или нет) — для частоты сбора."""
    out: dict[tuple[str, int], str] = {}
    for column, kind in (("target_id", "service"), ("publication_id", "manual")):
        for row in store.db.query_all(
            f"SELECT {column} AS ref, MAX(captured_at) AS at FROM tiktok_studio_stats "
            f"WHERE {column} IS NOT NULL GROUP BY {column}"
        ):
            out[(kind, int(row["ref"]))] = row["at"]
    return out


# --------------------------------------------------------------------------- #
# Сводки: экран клипа, проект, агенты
# --------------------------------------------------------------------------- #


def source_label(key: str) -> str:
    return SOURCE_LABELS.get(str(key), str(key).lower())


def kept_at(retention: list, sec: float) -> float | None:
    """Доля зрителей, что ещё смотрят на секунде ``sec`` (линейно между точками)."""
    points = [(float(p[0]), float(p[1])) for p in retention or [] if isinstance(p, (list, tuple)) and len(p) == 2]
    if not points or sec > points[-1][0]:
        return None
    prev = points[0]
    for point in points:
        if point[0] >= sec:
            if point[0] == prev[0]:
                return round(point[1], 4)
            part = (sec - prev[0]) / (point[0] - prev[0])
            return round(prev[1] + (point[1] - prev[1]) * part, 4)
        prev = point
    return None


def half_gone_sec(retention: list) -> float | None:
    """Секунда, к которой осталось не больше половины зрителей (None — половина досидела до конца)."""
    points = [(float(p[0]), float(p[1])) for p in retention or [] if isinstance(p, (list, tuple)) and len(p) == 2]
    prev = None
    for sec, share in points:
        if share <= 0.5:
            if prev is None or prev[1] == share:
                return round(sec, 1)
            part = (prev[1] - 0.5) / (prev[1] - share)
            return round(prev[0] + (sec - prev[0]) * part, 1)
        prev = (sec, share)
    return None


def _num(value: float | None, digits: int = 1) -> str:
    return f"{value:.{digits}f}".rstrip("0").rstrip(".").replace(".", ",") if value is not None else "?"


def _pct(share: float | None) -> str:
    return f"{round(share * 100)}%" if share is not None else "?"


def summary_text(brief: dict) -> str:
    """Одна строка простыми словами: «Половина уходит к 2 с; к 3-й секунде остаётся 46%; …»."""
    parts = []
    half = brief.get("half_gone_sec")
    if half is not None:
        parts.append(f"половина уходит к {_num(half)} с")
    elif brief.get("retention"):
        parts.append("больше половины досматривает до конца")
    kept3 = (brief.get("hook") or {}).get("3")
    if kept3 is not None:
        parts.append(f"к 3-й секунде остаётся {_pct(kept3)}")
    if brief.get("avg_watch_sec") is not None:
        of = f" из {_num(brief.get('duration_sec'))}" if brief.get("duration_sec") else ""
        parts.append(f"смотрят в среднем {_num(brief['avg_watch_sec'])} с{of}")
    if brief.get("finish_rate") is not None:
        parts.append(f"досматривают {_pct(brief['finish_rate'])}")
    main = brief.get("main_source")
    if main:
        parts.append(f"{_pct(main['share'])} — {main['label']}")
    if brief.get("new_followers"):
        parts.append(f"+{brief['new_followers']} подписч.")
    text = "; ".join(parts)
    return text[:1].upper() + text[1:] if text else ""


def brief_of(snapshot: dict | None) -> dict | None:
    """Короткая сводка снимка для экранов и агентов. Нет удачного снимка — только ошибка (или None)."""
    if not snapshot:
        return None
    if snapshot.get("id") is None:
        error = snapshot.get("error") or ""
        return {"captured_at": None, "error": error, "last_attempt_at": snapshot.get("last_attempt_at")} if error \
            else None
    retention = snapshot.get("retention") or []
    traffic = snapshot.get("traffic") or []
    duration = snapshot.get("duration_sec")
    avg = snapshot.get("avg_watch_sec")
    sources = [{"key": key, "label": source_label(key), "share": share} for key, share in traffic[:3]]
    brief = {
        "captured_at": snapshot.get("captured_at"),
        "views": snapshot.get("views"),
        "avg_watch_sec": avg,
        "duration_sec": duration,
        "watch_share": round(min(1.0, avg / duration), 3) if avg is not None and duration else None,
        "finish_rate": snapshot.get("finish_rate"),
        "new_followers": snapshot.get("new_followers"),
        "main_source": sources[0] if sources else None,
        "sources": sources,
        "hook": {str(sec): kept_at(retention, sec) for sec in HOOK_POINTS},
        "half_gone_sec": half_gone_sec(retention),
        "retention": [p for p in retention if float(p[0]) <= RETENTION_FINE_SEC],
        "error": snapshot.get("error") or "",
        "last_attempt_at": snapshot.get("last_attempt_at"),
    }
    brief["text"] = summary_text(brief)
    return brief


def briefs_by_ref(store: Any) -> dict[tuple[str, int], dict]:
    """("service"|"manual", id) → сводка (``brief_of``) — один проход по таблице для экрана или проекта."""
    out = {}
    for key, snap in latest_snapshots(store).items():
        brief = brief_of(snap)
        if brief:
            out[key] = brief
    return out


def _median(values: list) -> float | None:
    known = [float(v) for v in values if v is not None]
    return round(float(statistics.median(known)), 4) if known else None


def project_summary(briefs: list[dict]) -> dict | None:
    """Медианы по роликам проекта с числами Studio + строка для агента."""
    rows = [b for b in briefs if b and b.get("captured_at")]
    if not rows:
        return None
    sources: dict[str, list[float]] = {}
    for row in rows:
        for src in row.get("sources") or []:
            sources.setdefault(src["label"], []).append(float(src["share"]))
    main = max(sources.items(), key=lambda kv: sum(kv[1]))[0] if sources else ""
    summary = {
        "videos": len(rows),
        "median_half_gone_sec": _median([r.get("half_gone_sec") for r in rows]),
        "median_kept_3s": _median([(r.get("hook") or {}).get("3") for r in rows]),
        "median_avg_watch_sec": _median([r.get("avg_watch_sec") for r in rows]),
        "median_watch_share": _median([r.get("watch_share") for r in rows]),
        "median_finish_rate": _median([r.get("finish_rate") for r in rows]),
        "new_followers": sum(int(r.get("new_followers") or 0) for r in rows),
        "main_source": main,
    }
    parts = [f"TikTok Studio, роликов: {len(rows)} (медианы)"]
    if summary["median_half_gone_sec"] is not None:
        parts.append(f"половина зрителей уходит к {_num(summary['median_half_gone_sec'])} с")
    if summary["median_kept_3s"] is not None:
        parts.append(f"к 3-й секунде остаётся {_pct(summary['median_kept_3s'])}")
    if summary["median_watch_share"] is not None:
        parts.append(f"смотрят {_pct(summary['median_watch_share'])} длины ролика")
    if summary["median_finish_rate"] is not None:
        parts.append(f"досматривают {_pct(summary['median_finish_rate'])}")
    if main:
        parts.append(f"главный источник — {main}")
    if summary["new_followers"]:
        parts.append(f"новых подписчиков {summary['new_followers']}")
    summary["text"] = "; ".join(parts)
    summary["legend"] = STUDIO_LEGEND
    return summary
