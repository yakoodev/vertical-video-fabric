"""Проекты: теги клипов, ручные публикации, сводка и статистика (из ``app/projects.py``)."""

from __future__ import annotations

import re
import statistics
from datetime import datetime
from typing import Any, Callable

from app.projects_core import _text, _long_text, _date, _json, _loads, get_project, project_of_plan

# --------------------------------------------------------------------------- #
# Теги клипов
# --------------------------------------------------------------------------- #


def normalize_tag(tag: Any) -> str:
    text = str(tag or "").strip().lower().replace("ё", "е")
    text = re.sub(r"[\s\-]+", "_", text)
    text = re.sub(r"[^\w:]", "", text)
    return text[:60]


def _tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = re.split(r"[,\s]+", value)
    out = []
    for tag in value:
        norm = normalize_tag(tag)
        if norm and norm not in out:
            out.append(norm)
    return out[:30]


def clip_tags_map(store: Any, plan_ids: list[int]) -> dict[int, list[str]]:
    if not plan_ids:
        return {}
    marks = ",".join("?" * len(plan_ids))
    out: dict[int, list[str]] = {}
    for row in store.db.query_all(
        f"SELECT clip_plan_id, tag FROM clip_tags WHERE clip_plan_id IN ({marks}) ORDER BY tag", list(plan_ids)
    ):
        out.setdefault(int(row["clip_plan_id"]), []).append(row["tag"])
    return out


def get_clip_tags(store: Any, clip_plan_id: int) -> list[str]:
    return clip_tags_map(store, [int(clip_plan_id)]).get(int(clip_plan_id), [])


def set_clip_tags(store: Any, clip_plan_id: int, add: Any = None, remove: Any = None,
                  replace: Any = None) -> list[str]:
    store.get_clip_plan(int(clip_plan_id), include_segments=False)
    if replace is not None:
        store.db.execute("DELETE FROM clip_tags WHERE clip_plan_id = ?", (int(clip_plan_id),))
        add = replace
    for tag in _tags(remove):
        store.db.execute("DELETE FROM clip_tags WHERE clip_plan_id = ? AND tag = ?", (int(clip_plan_id), tag))
    for tag in _tags(add):
        store.db.execute("INSERT OR IGNORE INTO clip_tags (clip_plan_id, tag) VALUES (?, ?)",
                         (int(clip_plan_id), tag))
    return get_clip_tags(store, clip_plan_id)


# --------------------------------------------------------------------------- #
# Ручные публикации (выложено не через сервис)
# --------------------------------------------------------------------------- #


def _pub_from_row(row: Any) -> dict:
    data = dict(row)
    data["tags"] = _loads(data.pop("tags_json", "[]"), [])
    return data


def get_manual_publication(store: Any, publication_id: int) -> dict:
    row = store.db.query_one("SELECT * FROM project_publications WHERE id = ?", (int(publication_id),))
    if not row:
        raise KeyError(f"publication not found: {publication_id}")
    data = _pub_from_row(row)
    data["stats"] = latest_manual_stats(store, [int(publication_id)]).get(int(publication_id))
    return data


def add_manual_publication(store: Any, url: str, project_id: int | None = None, clip_plan_id: int | None = None,
                           title: str = "", published_at: Any = None, tags: Any = None, notes: str = "",
                           stats: dict | None = None) -> dict:
    """Видео, выложенное руками, по ссылке — к клипу или к проекту. ``stats`` — числа,
    введённые вручную (Instagram без сессии и т.п.); YouTube и TikTok сервис подтянет сам."""
    from app.platform_stats import platform_of_url

    url = _text(url, 500)
    if not re.match(r"^https?://", url):
        raise ValueError("нужна ссылка http(s)://")
    if clip_plan_id is not None:
        plan_project = project_of_plan(store, int(clip_plan_id))
        if project_id is None:
            project_id = plan_project
    if project_id is not None:
        get_project(store, int(project_id), include_sources=False)
    if project_id is None and clip_plan_id is None:
        raise ValueError("укажите проект или клип")
    if store.db.query_one("SELECT id FROM project_publications WHERE url = ?", (url,)):
        raise ValueError("эта ссылка уже добавлена")
    cur = store.db.execute(
        """INSERT INTO project_publications (project_id, clip_plan_id, url, platform, title, published_at, tags_json, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (project_id, clip_plan_id, url, platform_of_url(url), _text(title, 200), _date(published_at),
         _json(_tags(tags)), _long_text(notes, 1000)),
    )
    pub_id = int(cur.lastrowid)
    if stats:
        add_manual_stats(store, pub_id, stats)
    return get_manual_publication(store, pub_id)


def update_manual_publication(store: Any, publication_id: int, **fields: Any) -> dict:
    get_manual_publication(store, publication_id)
    updates: dict[str, Any] = {}
    if "title" in fields:
        updates["title"] = _text(fields["title"], 200)
    if "published_at" in fields:
        updates["published_at"] = _date(fields["published_at"])
    if "tags" in fields:
        updates["tags_json"] = _json(_tags(fields["tags"]))
    if "notes" in fields:
        updates["notes"] = _long_text(fields["notes"], 1000)
    if "clip_plan_id" in fields:
        plan_id = fields["clip_plan_id"]
        if plan_id is not None:
            project_of_plan(store, int(plan_id))
        updates["clip_plan_id"] = None if plan_id is None else int(plan_id)
    if updates:
        sets = ", ".join(f"{key} = ?" for key in updates)
        store.db.execute(f"UPDATE project_publications SET {sets}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (*updates.values(), int(publication_id)))
    return get_manual_publication(store, publication_id)


def delete_manual_publication(store: Any, publication_id: int) -> dict:
    get_manual_publication(store, publication_id)
    store.db.execute("DELETE FROM project_publications WHERE id = ?", (int(publication_id),))
    return {"deleted": True}


def _count_value(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"не число: {value}") from exc


def add_manual_stats(store: Any, publication_id: int, stats: dict | None, origin: str = "manual",
                     error: str = "") -> dict:
    """Снимок чисел ручной публикации. avg_retention — доля 0..1 (или проценты 0..100)."""
    stats = stats or {}
    retention = stats.get("avg_retention")
    if retention not in (None, ""):
        retention = float(retention)
        retention = retention / 100 if retention > 1 else retention
        retention = max(0.0, min(1.0, retention))
    else:
        retention = None
    values = {key: _count_value(stats.get(key)) for key in ("views", "likes", "comments", "shares")}
    if not error and all(v is None for v in values.values()) and retention is None:
        raise ValueError("нет ни одного числа")
    cur = store.db.execute(
        """INSERT INTO project_publication_stats
           (publication_id, views, likes, comments, shares, avg_retention, origin, error)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (int(publication_id), values["views"], values["likes"], values["comments"], values["shares"], retention,
         origin, _text(error, 300)),
    )
    return dict(store.db.query_one("SELECT * FROM project_publication_stats WHERE id = ?", (cur.lastrowid,)))


def latest_manual_stats(store: Any, publication_ids: list[int] | None = None) -> dict[int, dict]:
    """publication_id → последние удачные числа (+ последняя попытка и её ошибка)."""
    where = ""
    params: list = []
    if publication_ids is not None:
        if not publication_ids:
            return {}
        where = f" AND publication_id IN ({','.join('?' * len(publication_ids))})"
        params = list(publication_ids)
    out: dict[int, dict] = {}
    for row in store.db.query_all(
        f"""SELECT ps.* FROM project_publication_stats ps JOIN (
              SELECT publication_id, MAX(id) AS mid FROM project_publication_stats
              WHERE error = ''{where} GROUP BY publication_id) m ON ps.id = m.mid""",
        params,
    ):
        out[int(row["publication_id"])] = {key: row[key] for key in
                                           ("views", "likes", "comments", "shares", "avg_retention", "captured_at",
                                            "origin")}
    for row in store.db.query_all(
        f"""SELECT ps.publication_id, ps.captured_at, ps.error FROM project_publication_stats ps JOIN (
              SELECT publication_id, MAX(id) AS mid FROM project_publication_stats
              WHERE 1 = 1{where} GROUP BY publication_id) m ON ps.id = m.mid""",
        params,
    ):
        entry = out.setdefault(int(row["publication_id"]), {"views": None, "likes": None, "comments": None,
                                                            "shares": None, "avg_retention": None,
                                                            "captured_at": None, "origin": ""})
        entry["last_attempt_at"] = row["captured_at"]
        entry["error"] = row["error"] or ""
    return out


def refresh_manual_publication(store: Any, publication_id: int) -> dict:
    """Числа ручной публикации тем же путём, что публикации сервиса (yt-dlp для
    YouTube/TikTok). Instagram без входа чисел не отдаёт — тогда только вручную."""
    from app import platform_stats

    pub = get_manual_publication(store, publication_id)
    platform = platform_stats.platform_of_url(pub["url"])
    if platform not in ("youtube", "tiktok"):
        reason = ("Instagram без входа чисел не отдаёт — введите вручную" if platform == "instagram"
                  else "площадка не поддерживается — введите числа вручную")
        add_manual_stats(store, publication_id, None, origin="auto", error=f"пропуск: {reason}")
        return {"ok": False, "skipped": True, "error": reason}
    try:
        stats = platform_stats.fetch_ytdlp_stats(pub["url"])
    except RuntimeError as exc:
        add_manual_stats(store, publication_id, None, origin="auto", error=str(exc)[:300] or "ошибка")
        return {"ok": False, "error": str(exc)[:300]}
    add_manual_stats(store, publication_id, stats, origin="auto")
    return {"ok": True, "stats": stats}


def due_manual_publications(store: Any, *, force: bool) -> list[dict]:
    """Ручные публикации YouTube/TikTok, по которым пора за числами (те же интервалы, что у сервиса)."""
    from app import platform_stats

    rows = store.db.query_all(
        """SELECT mp.id, mp.url, mp.platform,
                  (SELECT MAX(captured_at) FROM project_publication_stats ps
                    WHERE ps.publication_id = mp.id AND ps.origin = 'auto') AS last_attempt_at
           FROM project_publications mp WHERE mp.platform IN ('youtube', 'tiktok') ORDER BY mp.id"""
    )
    return platform_stats.due_targets([dict(r) for r in rows], force=force)


def collect_manual_stats(store: Any, *, force: bool = False, sleep: Callable[[float], None] | None = None,
                         pause: float = 0.0) -> dict:
    """Проход сбора по ручным публикациям (зовётся из platform_stats.collect_stats)."""
    summary = {"checked": 0, "ok": 0, "failed": 0}
    for index, pub in enumerate(due_manual_publications(store, force=force)):
        if index and pause > 0 and sleep:
            sleep(pause)
        summary["checked"] += 1
        result = refresh_manual_publication(store, int(pub["id"]))
        summary["ok" if result.get("ok") else "failed"] += 1
    return summary


def list_manual_publications(store: Any, project_id: int) -> list[dict]:
    rows = store.db.query_all(
        """SELECT mp.*, cp.title AS clip_title FROM project_publications mp
           LEFT JOIN clip_plans cp ON cp.id = mp.clip_plan_id
           LEFT JOIN sources s ON s.id = cp.source_id
           WHERE (mp.clip_plan_id IS NOT NULL AND s.project_id = ?)
              OR (mp.clip_plan_id IS NULL AND mp.project_id = ?)
              OR (mp.clip_plan_id IS NOT NULL AND s.project_id IS NULL AND mp.project_id = ?)
           ORDER BY mp.id DESC""",
        (int(project_id), int(project_id), int(project_id)),
    )
    pubs = [_pub_from_row(r) for r in rows]
    latest = latest_manual_stats(store, [p["id"] for p in pubs])
    for pub in pubs:
        pub["stats"] = latest.get(int(pub["id"]))
    return pubs


# --------------------------------------------------------------------------- #
# Статистика проекта
# --------------------------------------------------------------------------- #


def _parse_time(text: Any) -> datetime | None:
    text = str(text or "").strip().replace("T", " ")
    for fmt, size in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d %H:%M", 16), ("%Y-%m-%d", 10)):
        try:
            return datetime.strptime(text[:size], fmt)
        except ValueError:
            continue
    return None


def _sum(values: list) -> int | None:
    known = [int(v) for v in values if v is not None]
    return sum(known) if known else None


def project_items(store: Any, project_id: int, with_features: bool = False) -> list[dict]:
    """Единицы статистики проекта: клип (все его публикации — сервиса и ручные — в сумме)
    или ручная публикация без клипа. У каждой — теги, числа и, по запросу, признаки монтажа."""
    get_project(store, project_id, include_sources=False)
    latest = store.latest_publication_stats()
    items: dict[str, dict] = {}
    now = datetime.utcnow()

    def item_for(key: str, **base: Any) -> dict:
        return items.setdefault(key, {"key": key, "clip_plan_id": None, "title": "", "source_id": None,
                                      "tags": [], "publications": [], **base})

    for row in store.db.query_all(
        """
        SELECT jt.id AS target_id, jt.platform, jt.remote_url, jt.finished_at, j.title, j.clip_id,
               c.clip_plan_id, c.source_id, cp.title AS plan_title
        FROM job_targets jt
        JOIN jobs j ON j.id = jt.job_id
        JOIN clips c ON c.id = j.clip_id
        JOIN sources s ON s.id = c.source_id
        LEFT JOIN clip_plans cp ON cp.id = c.clip_plan_id
        WHERE jt.status = 'succeeded' AND jt.remote_url != ''
          AND (s.project_id = ? OR (s.project_id IS NULL AND c.clip_plan_id IN
               (SELECT pp.clip_plan_id FROM plan_projects pp WHERE pp.project_id = ?)))
        ORDER BY jt.id
        """,
        (int(project_id), int(project_id)),
    ):
        plan_id = row["clip_plan_id"]
        key = f"plan:{plan_id}" if plan_id else f"clip:{row['clip_id']}"
        item = item_for(key, clip_plan_id=plan_id, title=row["plan_title"] or row["title"] or "",
                        source_id=row["source_id"])
        stats = latest.get(int(row["target_id"]), {})
        item["publications"].append({
            "kind": "service", "id": row["target_id"], "platform": row["platform"] or "", "url": row["remote_url"],
            "published_at": row["finished_at"], **{k: stats.get(k) for k in
                                                   ("views", "likes", "comments", "shares", "avg_retention",
                                                    "captured_at")},
            "error": stats.get("error") or "",
        })
    manual = list_manual_publications(store, project_id)
    for pub in manual:
        plan_id = pub.get("clip_plan_id")
        if plan_id:
            item = item_for(f"plan:{plan_id}", clip_plan_id=plan_id, title=pub.get("clip_title") or pub["title"])
        else:
            item = item_for(f"pub:{pub['id']}", title=pub["title"] or pub["url"], manual_id=pub["id"])
            item["tags"] = list(pub["tags"])
        stats = pub.get("stats") or {}
        item["publications"].append({
            "kind": "manual", "id": pub["id"], "platform": pub["platform"], "url": pub["url"],
            "published_at": pub["published_at"] or pub["created_at"],
            **{k: stats.get(k) for k in ("views", "likes", "comments", "shares", "avg_retention", "captured_at")},
            "error": stats.get("error") or "",
        })
        if plan_id:  # теги ручной публикации клипа тоже признаки клипа
            item["tags"] = sorted(set(item["tags"]) | set(pub["tags"]))
    plan_ids = [int(i["clip_plan_id"]) for i in items.values() if i["clip_plan_id"]]
    tags = clip_tags_map(store, plan_ids)
    for item in items.values():
        pubs = item["publications"]
        if item["clip_plan_id"]:
            item["tags"] = sorted(set(item["tags"]) | set(tags.get(int(item["clip_plan_id"]), [])))
        views = _sum([p["views"] for p in pubs])
        likes = _sum([p["likes"] for p in pubs])
        published = [t for t in (_parse_time(p["published_at"]) for p in pubs) if t]
        age = round((now - min(published)).total_seconds() / 86400, 1) if published else None
        retention = [float(p["avg_retention"]) for p in pubs if p.get("avg_retention") is not None]
        item.update(
            views=views, likes=likes, comments=_sum([p["comments"] for p in pubs]),
            shares=_sum([p["shares"] for p in pubs]),
            like_rate=round(likes / views, 4) if views and likes is not None else None,
            views_per_day=round(views / max(1.0, age), 1) if views is not None and age is not None else None,
            avg_retention=round(sum(retention) / len(retention), 3) if retention else None,
            age_days=age, platforms=sorted({p["platform"] for p in pubs if p["platform"]}),
        )
        item["features"] = None
        if with_features and item["clip_plan_id"]:
            from app.platform_stats import plan_features

            try:
                item["features"] = plan_features(store, int(item["clip_plan_id"]))
            except Exception:  # noqa: BLE001 - клип сломан/удалён: числа всё равно считаем
                item["features"] = None
    out = list(items.values())
    out.sort(key=lambda i: (i["views"] is None, -(i["views"] or 0)))
    return out


def _median(values: list[float]) -> float | None:
    return float(statistics.median(values)) if values else None


def project_stats(store: Any, project_id: int, top: int = 5) -> dict:
    """Агрегаты проекта: суммы, медиана просмотров, удержание (если есть), площадки,
    лучшие и худшие клипы."""
    items = project_items(store, project_id)
    with_views = [i for i in items if i["views"] is not None]
    retention = [i["avg_retention"] for i in items if i["avg_retention"] is not None]
    platforms: dict[str, dict] = {}
    for item in items:
        for pub in item["publications"]:
            entry = platforms.setdefault(pub["platform"] or "другое", {"publications": 0, "views": None})
            entry["publications"] += 1
            if pub["views"] is not None:
                entry["views"] = (entry["views"] or 0) + int(pub["views"])

    def brief(item: dict) -> dict:
        return {key: item.get(key) for key in ("key", "clip_plan_id", "title", "views", "likes", "like_rate",
                                               "views_per_day", "avg_retention", "age_days", "platforms", "tags")}

    top = max(1, int(top))
    best = with_views[:top]
    worst = [i for i in with_views[::-1][:top] if i not in best]
    note = ("Просмотры зависят от возраста публикации и площадки — сравнивай views_per_day.")
    if len(with_views) < 4:
        note = "Мало данных (меньше 4 клипов с числами) — выводов не делай. " + note
    return {
        "totals": {
            "items": len(items), "items_with_stats": len(with_views),
            "publications": sum(len(i["publications"]) for i in items),
            "views": _sum([i["views"] for i in items]), "likes": _sum([i["likes"] for i in items]),
            "comments": _sum([i["comments"] for i in items]),
            "median_views": _median([float(i["views"]) for i in with_views]),
            "avg_retention": round(sum(retention) / len(retention), 3) if retention else None,
        },
        "platforms": platforms,
        "best": [brief(i) for i in best],
        "worst": [brief(i) for i in worst],
        "items": [brief(i) | {"publications": i["publications"]} for i in items],
        "note": note,
    }
