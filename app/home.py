"""«Главная»: короткая сводка по всему сервису одним ответом (``GET /api/home``, инструмент ``home_summary``).

Сама ничего не хранит и не считает заново: собирает то, что уже есть в других местах — счётчики «Ленты»
(``app/feed.py``), карточки одобрения автопилота, открытые вопросы, ждущие подтверждения в чате, упавшие публикации
и рендеры, очередь публикаций по ``jobs.scheduled_at``, числа публикаций (``publication_stats`` и ручные), очередь
тяжёлых задач (``app/heavy_queue.py``), траты Gemini (``app/gemini_usage.py``) и Polza (``aiv_ledger``), проекты.

Каждый блок считается отдельно: упал один источник — в ответе у блока ``error``, остальные на месте
(``errors`` — список упавших блоков). Время — UTC, как в базе; на экране его переводят в МСК.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from app import error_text

log =logging.getLogger(__name__)

RECENT_WORKS = 6
UPCOMING_LIMIT = 8
TOP_LIMIT = 5
TOP_DAYS = 7
FAILED_DAYS = 3
FAILED_LIMIT = 6

# «Примеры»: что сервис умеет, с последней принятой работой каждого вида. ``type`` — карточка «Нового видео»
# (frontend/src/pages/release/NewVideoPage.tsx: TYPES), ``module`` — без модуля карточки нет.
# Работа ищется в «Ленте» (принятая версия) по виду или словам в названии и проекте, потом среди готовых выпусков
# (``release_kinds``, app/releases.py), для 3D-шаблона — в каталоге шаблонов (app/s3d_templates.py).
EXAMPLES: tuple[dict[str, Any], ...] = (
    {"key": "frag", "type": "frag", "module": "frag_movie", "title": "Мувик под песню",
     "text": "Бои из записей игры под трек, текст песни по слогам.", "kinds": ("movie",),
     "words": ("мувик", "frag", "фраг")},
    {"key": "amv", "type": "amv", "module": "amv", "title": "AMV",
     "text": "Кадры одного аниме или фильма под песню: сюжет по актам, ритм.", "kinds": ("amv",),
     "words": ("amv", "амв")},
    {"key": "mv", "type": "mv", "module": "music_video", "title": "Клип на песню",
     "text": "Своя песня, рваная бумага и 2.5D, слова в такт слогам.", "kinds": (),
     "words": ("клип на песню", "клип под песню", "music video")},
    {"key": "expl", "type": "expl", "module": "explainer", "title": "Объяснялка",
     "text": "Маскот объясняет тему: сценарий, позы, озвучка.", "kinds": (),
     "words": ("объясн", "маскот"), "release_kinds": ("expl",)},
    {"key": "s3d", "type": "3d", "module": "stories", "title": "3D-серия из шаблона",
     "text": "Готовая история как старт: поменять реплики и героев, рендер здесь же.", "kinds": ("series",),
     "words": ("3d", "3д"), "release_kinds": ("3d",), "template": True},
    {"key": "vtubika", "type": "3d", "module": "stories", "title": "Витубика-гайд",
     "text": "Ролики вселенной Витубики: рассказчик, комната стримера, свои герои.", "kinds": (),
     "words": ("витубик", "vtubika")},
)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _utc(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def _module_on(store: Any, module_id: str) -> bool:
    from app import modules

    return modules.is_enabled(module_id, store)


def _count(store: Any, sql: str, params: tuple = ()) -> int:
    row = store.db.query_one(sql, params)
    return int((row[0] if row else 0) or 0)


class _Ctx:
    """Общие для нескольких блоков чтения (лента целиком) — один раз на ответ."""

    def __init__(self, store: Any) -> None:
        self.store = store
        self._feed: dict | None = None

    def feed(self) -> dict:
        if self._feed is None:
            from app import feed as FEED

            self._feed = FEED.list_feed(self.store, limit=200, with_versions=True)
        return self._feed


def _guard(name: str, fn: Callable[[], Any]) -> tuple[Any, str]:
    try:
        return fn(), ""
    except Exception as exc:  # noqa: BLE001 - сводка показывает остальное, упавшее — с причиной
        log.warning("home: %s failed", name, exc_info=True)
        return None, (str(exc) or exc.__class__.__name__)[:300]


# --------------------------------------------------------------------------- #
# Блоки
# --------------------------------------------------------------------------- #


def _waiting(ctx: _Ctx) -> dict:
    """«Ждёт тебя»: что требует решения владельца. Внутри — свои предохранители: счётчик не валит соседей."""
    store = ctx.store
    out: dict[str, Any] = {}
    parts_failed: dict[str, str] = {}

    def part(key: str, fn: Callable[[], Any]) -> None:
        value, err = _guard(f"waiting.{key}", fn)
        out[key] = value
        if err:
            parts_failed[key] = err

    def feed_counts() -> dict:
        counts = ctx.feed()["counts"]
        return {"ready_unposted": int(counts.get("ready_unposted", 0)), "review": int(counts.get("review", 0))}

    def approvals() -> dict | None:
        if not _module_on(store, "autopilot"):
            return None
        rows = store.db.query_all(
            "SELECT a.id, a.title, a.project_id, COALESCE(p.name, '') AS project_name FROM approvals a "
            "LEFT JOIN projects p ON p.id = a.project_id WHERE a.status = 'pending' ORDER BY a.id LIMIT 3")
        total = _count(store, "SELECT COUNT(*) FROM approvals WHERE status = 'pending'")
        return {"count": total, "first": [dict(r) for r in rows]}

    def questions() -> dict | None:
        if not _module_on(store, "stream_questions"):
            return None
        return {"questions": _count(store, "SELECT COUNT(*) FROM stream_questions WHERE status = 'open'"),
                "memes": _count(store, "SELECT COUNT(*) FROM meme_moments WHERE status = 'open'")}

    def chat() -> dict | None:
        if not _module_on(store, "agent_chat"):
            return None
        rows = store.db.query_all(
            "SELECT c.thread_id, c.label, t.project_id, t.title AS thread_title, COALESCE(p.name, '') AS project_name "
            "FROM chat_confirmations c JOIN chat_threads t ON t.id = c.thread_id "
            "LEFT JOIN projects p ON p.id = t.project_id WHERE c.status = 'pending' ORDER BY c.id LIMIT 3")
        total = _count(store, "SELECT COUNT(*) FROM chat_confirmations WHERE status = 'pending'")
        return {"count": total, "first": [dict(r) for r in rows]}

    def failed() -> dict:
        since = _utc(_now() - timedelta(days=FAILED_DAYS))
        items: list[dict] = []
        # Публикация упала и с тех пор её не повторили удачно (повтор — новое задание того же клипа).
        for row in store.db.query_all(
            "SELECT j.id, j.status, j.title, j.error, j.updated_at, j.clip_id FROM jobs j "
            "WHERE j.status IN ('failed', 'needs_reauth') AND j.updated_at >= ? AND NOT EXISTS ("
            "  SELECT 1 FROM jobs k WHERE k.clip_id = j.clip_id AND j.clip_id IS NOT NULL AND k.id > j.id "
            "  AND k.status IN ('succeeded', 'queued', 'running')) ORDER BY j.updated_at DESC LIMIT ?",
            (since, FAILED_LIMIT),
        ):
            # Простыми словами — по площадке и аккаунту, на которых упало (app/error_text.py).
            view = error_text.explain_job(dict(row), error_text.job_targets_context(store, row["id"]))
            items.append({"kind": "job", "id": int(row["id"]), "status": row["status"], "label": row["title"],
                          "error": (row["error"] or "")[:200], "error_view": view, "at": row["updated_at"],
                          "to": f"/pubs/{row['id']}"})
        # Рендер упал, и у его плана нет более нового удачного или идущего файла.
        for row in store.db.query_all(
            "SELECT c.id, c.title, c.error, c.updated_at, c.clip_plan_id, c.source_id FROM clips c "
            "WHERE c.status = 'failed' AND c.updated_at >= ? AND NOT EXISTS ("
            "  SELECT 1 FROM clips d WHERE d.clip_plan_id = c.clip_plan_id AND c.clip_plan_id IS NOT NULL "
            "  AND d.id > c.id AND d.status IN ('succeeded', 'queued', 'rendering')) "
            "ORDER BY c.updated_at DESC LIMIT ?",
            (since, FAILED_LIMIT),
        ):
            to = (f"/clips/{row['clip_plan_id']}" if row["clip_plan_id"] else
                  f"/v/{row['source_id']}" if row["source_id"] else "/inbox?tab=tasks")
            items.append({"kind": "clip", "id": int(row["id"]), "status": "failed", "label": row["title"],
                          "error": (row["error"] or "")[:200], "error_view": error_text.explain(row["error"]),
                          "at": row["updated_at"], "to": to})
        items.sort(key=lambda i: str(i["at"] or ""), reverse=True)
        return {"count": len(items), "items": items[:FAILED_LIMIT], "days": FAILED_DAYS}

    part("feed", feed_counts)
    part("approvals", approvals)
    part("questions", questions)
    part("chat", chat)
    part("failed", failed)
    total = 0
    for key in ("approvals", "chat", "failed"):
        total += int((out.get(key) or {}).get("count") or 0)
    total += int((out.get("feed") or {}).get("ready_unposted") or 0) + int((out.get("feed") or {}).get("review") or 0)
    total += sum(int(v or 0) for v in (out.get("questions") or {}).values())
    out["total"] = total
    if parts_failed:
        out["errors"] = parts_failed
    return out


def _work_card(work: dict) -> dict:
    latest = work.get("latest") or None
    return {
        "id": work["id"], "title": work["title"], "project_id": work["project_id"],
        "project_name": work["project_name"], "kind": work["kind"], "kind_label": work["kind_label"],
        "cover_url": work["cover_url"], "activity_at": work["activity_at"],
        "latest": ({"name": latest["name"], "status": latest["status"], "status_label": latest["status_label"]}
                   if latest else None),
        "ready_unposted": work["ready_unposted"], "posted_platforms": work["posted_platforms"],
    }


def _recent_works(ctx: _Ctx) -> dict:
    feed = ctx.feed()
    return {"items": [_work_card(w) for w in feed["items"][:RECENT_WORKS]], "total": int(feed["total"])}


def _job_projects(store: Any, clip_ids: list[int]) -> dict[int, dict]:
    """Клип → проект (через видео или привязку плана к проекту) и план для ссылки."""
    if not clip_ids:
        return {}
    marks = ",".join("?" * len(clip_ids))
    out: dict[int, dict] = {}
    for row in store.db.query_all(
        f"SELECT c.id, c.clip_plan_id, COALESCE(s.project_id, pp.project_id) AS project_id, "
        f"COALESCE(p.name, '') AS project_name FROM clips c LEFT JOIN sources s ON s.id = c.source_id "
        f"LEFT JOIN plan_projects pp ON pp.clip_plan_id = c.clip_plan_id "
        f"LEFT JOIN projects p ON p.id = COALESCE(s.project_id, pp.project_id) WHERE c.id IN ({marks})",
        clip_ids,
    ):
        out[int(row["id"])] = dict(row)
    return out


def _scheduled_rows(store: Any, limit: int | None) -> list[dict]:
    sql = ("SELECT id, title, scheduled_at, clip_id FROM jobs WHERE status = 'queued' AND scheduled_at IS NOT NULL "
           "AND scheduled_at != '' AND scheduled_at > ? ORDER BY scheduled_at, id")
    params: list[Any] = [_utc(_now())]
    if limit:
        sql += " LIMIT ?"
        params.append(int(limit))
    return [dict(r) for r in store.db.query_all(sql, params)]


def _upcoming(ctx: _Ctx) -> dict:
    """«Скоро выйдет»: задания очереди публикаций со временем в будущем, площадки и аккаунты."""
    store = ctx.store
    jobs = _scheduled_rows(store, UPCOMING_LIMIT)
    total = _count(store, "SELECT COUNT(*) FROM jobs WHERE status = 'queued' AND scheduled_at IS NOT NULL "
                          "AND scheduled_at != '' AND scheduled_at > ?", (_utc(_now()),))
    targets: dict[int, list[dict]] = {}
    if jobs:
        marks = ",".join("?" * len(jobs))
        for row in store.db.query_all(
            f"SELECT jt.job_id, jt.platform, COALESCE(a.label, '') AS account FROM job_targets jt "
            f"LEFT JOIN accounts a ON a.id = jt.account_id WHERE jt.job_id IN ({marks}) ORDER BY jt.id",
            [j["id"] for j in jobs],
        ):
            targets.setdefault(int(row["job_id"]), []).append({"platform": row["platform"], "account": row["account"]})
    projects = _job_projects(store, [int(j["clip_id"]) for j in jobs if j.get("clip_id")])
    items = []
    for job in jobs:
        info = projects.get(int(job["clip_id"] or 0), {})
        items.append({"job_id": int(job["id"]), "title": job["title"], "scheduled_at": job["scheduled_at"],
                      "targets": targets.get(int(job["id"]), []), "clip_plan_id": info.get("clip_plan_id"),
                      "project_id": info.get("project_id"), "project_name": info.get("project_name") or "",
                      "to": f"/pubs/{job['id']}"})
    return {"items": items, "total": total}


def _top_week(ctx: _Ctx) -> dict:
    """«Что зашло»: публикации последних ``TOP_DAYS`` дней по просмотрам — сервиса и отмеченные вручную."""
    from app.projects_stats import latest_manual_stats

    store = ctx.store
    since = _utc(_now() - timedelta(days=TOP_DAYS))
    latest = store.latest_publication_stats()
    items: list[dict] = []
    for row in store.db.query_all(
        "SELECT jt.id, jt.platform, jt.remote_url, jt.finished_at, j.title, j.clip_id, c.clip_plan_id, "
        "COALESCE(cp.title, '') AS plan_title, COALESCE(a.label, '') AS account FROM job_targets jt "
        "JOIN jobs j ON j.id = jt.job_id LEFT JOIN clips c ON c.id = j.clip_id "
        "LEFT JOIN clip_plans cp ON cp.id = c.clip_plan_id LEFT JOIN accounts a ON a.id = jt.account_id "
        "WHERE jt.status = 'succeeded' AND jt.remote_url != '' AND jt.finished_at >= ?",
        (since,),
    ):
        stats = latest.get(int(row["id"]), {})
        items.append({"kind": "service", "id": int(row["id"]), "title": row["plan_title"] or row["title"] or "",
                      "platform": row["platform"] or "", "account": row["account"], "url": row["remote_url"],
                      "published_at": row["finished_at"], "views": stats.get("views"), "likes": stats.get("likes"),
                      "clip_plan_id": row["clip_plan_id"],
                      "to": f"/clips/{row['clip_plan_id']}" if row["clip_plan_id"] else f"/pubs/{row['id']}"})
    manual = [dict(r) for r in store.db.query_all(
        "SELECT mp.id, mp.platform, mp.url, mp.account, mp.title, mp.clip_plan_id, mp.feed_variant_id, "
        "COALESCE(NULLIF(mp.published_at, ''), mp.created_at) AS published_at, COALESCE(cp.title, '') AS plan_title, "
        "COALESCE(fw.title, '') AS work_title, fw.id AS work_id FROM project_publications mp "
        "LEFT JOIN clip_plans cp ON cp.id = mp.clip_plan_id "
        "LEFT JOIN feed_variants fv ON fv.id = mp.feed_variant_id LEFT JOIN feed_versions fver ON fver.id = fv.version_id "
        "LEFT JOIN feed_works fw ON fw.id = fver.work_id "
        "WHERE COALESCE(NULLIF(mp.published_at, ''), mp.created_at) >= ?",
        (since[:10],),
    )]
    stats_by_pub = latest_manual_stats(store, [int(m["id"]) for m in manual]) if manual else {}
    for pub in manual:
        stats = stats_by_pub.get(int(pub["id"]), {})
        to = (f"/feed?work={pub['work_id']}" if pub.get("work_id") else
              f"/clips/{pub['clip_plan_id']}" if pub.get("clip_plan_id") else (pub["url"] or ""))
        items.append({"kind": "manual", "id": int(pub["id"]),
                      "title": pub["work_title"] or pub["plan_title"] or pub["title"] or pub["url"] or "",
                      "platform": pub["platform"] or "", "account": pub["account"] or "", "url": pub["url"] or "",
                      "published_at": pub["published_at"], "views": stats.get("views"), "likes": stats.get("likes"),
                      "clip_plan_id": pub.get("clip_plan_id"), "to": to})
    with_views = [i for i in items if i["views"] is not None]
    with_views.sort(key=lambda i: -(i["views"] or 0))
    return {"items": with_views[:TOP_LIMIT], "published": len(items), "days": TOP_DAYS,
            "total_views": sum(int(i["views"] or 0) for i in with_views)}


def _working(ctx: _Ctx) -> dict:
    """«Работает сейчас»: очередь тяжёлых задач процесса и задания из базы. Скачивания и торренты живут в памяти
    процесса — их экран берёт из ``/api/tasks/active``."""
    from app.heavy_queue import HEAVY

    heavy = HEAVY.status()
    active = [t for t in ctx.store.list_active_tasks() if t.get("status") in ("queued", "running", "rendering")]
    return {"heavy": {"limit": heavy.get("limit"), "waiting": heavy.get("waiting", 0),
                      "running": [{"kind": r.get("kind"), "label": r.get("label") or ""} for r in heavy.get("running", [])]},
            "tasks": [{"kind": t["kind"], "id": t["id"], "status": t["status"], "label": t.get("label") or ""}
                      for t in active[:10]],
            "active": len(active)}


def _spend(ctx: _Ctx) -> dict:
    """Траты месяца (UTC, с первого числа): Gemini по журналу запросов, платные генерации Polza по журналу списаний
    и потолок месяца на все проекты (AIVIDEO_MONTH_CAP_RUB)."""
    from app import gemini_usage
    from app.settings import settings

    store = ctx.store
    month_start = _now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    out: dict[str, Any] = {"since": _utc(month_start)}
    gem, err = _guard("spend.gemini", lambda: gemini_usage.totals(store, since=_utc(month_start))["total"])
    out["gemini"] = ({"calls": gem["calls"], "errors": gem["errors"], "cost_rub": gem["cost_rub"],
                      "cost_usd": gem["cost_usd"], "unpriced": gem["unpriced"]} if gem else {"error": err})

    def polza() -> dict:
        from app.aivideo_budget import _reserved, spent_month

        return {"spent_rub": spent_month(store), "reserved_rub": _reserved(store, "1 = 1", ()),
                "cap_rub": float(settings.aivideo_month_cap_rub), "enabled": bool(settings.aivideo_enabled)}

    value, err = _guard("spend.polza", polza)
    out["polza"] = value if value is not None else {"error": err}
    return out


def _projects(ctx: _Ctx) -> dict:
    """Проекты: последнее событие (видео, клип, работа ленты), ближайшая публикация, ждущие карточки."""
    from app import nav

    store = ctx.store
    rows = nav.nav_counts(store)["projects"]
    last: dict[int, str] = {}

    def bump(sql: str) -> None:
        for row in store.db.query_all(sql):
            pid, at = row["project_id"], row["at"]
            if pid is not None and at and str(at) > last.get(int(pid), ""):
                last[int(pid)] = str(at)

    bump("SELECT project_id, MAX(created_at) AS at FROM sources WHERE project_id IS NOT NULL GROUP BY project_id")
    bump("SELECT s.project_id, MAX(cp.updated_at) AS at FROM clip_plans cp JOIN sources s ON s.id = cp.source_id "
         "WHERE s.project_id IS NOT NULL GROUP BY s.project_id")
    bump("SELECT pp.project_id, MAX(cp.updated_at) AS at FROM plan_projects pp "
         "JOIN clip_plans cp ON cp.id = pp.clip_plan_id GROUP BY pp.project_id")
    bump("SELECT project_id, MAX(activity_at) AS at FROM feed_works WHERE project_id IS NOT NULL GROUP BY project_id")
    nxt: dict[int, dict] = {}
    jobs = _scheduled_rows(store, None)
    projects = _job_projects(store, [int(j["clip_id"]) for j in jobs if j.get("clip_id")])
    for job in jobs:
        pid = projects.get(int(job["clip_id"] or 0), {}).get("project_id")
        if pid is not None and int(pid) not in nxt:
            nxt[int(pid)] = {"job_id": int(job["id"]), "title": job["title"], "scheduled_at": job["scheduled_at"]}
    pending: dict[int, int] = {}
    if _module_on(store, "autopilot"):
        for row in store.db.query_all(
                "SELECT project_id, COUNT(*) AS n FROM approvals WHERE status = 'pending' GROUP BY project_id"):
            if row["project_id"] is not None:
                pending[int(row["project_id"])] = int(row["n"])
    items = [{"id": p["id"], "name": p["name"], "kind": p["kind"], "videos": p["videos"],
              "last_activity_at": last.get(int(p["id"])), "next": nxt.get(int(p["id"])),
              "approvals": pending.get(int(p["id"]), 0), "open_questions": p.get("open_questions", 0)}
             for p in rows]
    # Сначала у кого скоро выход (ближайший первым), потом свежие по последнему событию.
    soon = sorted((p for p in items if p["next"]), key=lambda p: p["next"]["scheduled_at"])
    rest = sorted((p for p in items if not p["next"]), key=lambda p: p["last_activity_at"] or "", reverse=True)
    return {"items": soon + rest}


def _matches(spec: dict, work: dict) -> bool:
    if work["kind"] in spec.get("kinds", ()):
        return True
    text = f"{work['title']} {work['project_name']}".lower()
    return any(word in text for word in spec.get("words", ()))


def _accepted(work: dict) -> dict | None:
    return next((v for v in work.get("versions") or [] if v["status"] == "accepted"), None)


def _release_example(store: Any, kinds: tuple[str, ...], cache: dict) -> dict | None:
    if "releases" not in cache:
        from app import releases

        cache["releases"] = releases.collect(store)
    ready = [r for r in cache["releases"] if r["kind"] in kinds and r.get("status") == "ready" and r.get("head")]
    if not ready:
        return None
    item = max(ready, key=lambda r: str(r.get("updated_at") or r.get("date") or ""))
    row = store.db.query_one("SELECT id FROM clips WHERE clip_plan_id = ? AND status = 'succeeded' "
                             "ORDER BY id DESC LIMIT 1", (int(item["head"]),))
    return {"title": item.get("name") or "", "poster_url": f"/media/clips/{row['id']}/thumb" if row else None,
            "to": item["to"], "source": "release"}


def _template_example() -> dict | None:
    from app import s3d_templates

    for tpl in s3d_templates.catalog():
        if tpl.get("thumb") and tpl.get("kind") == "vtubika":
            return {"title": tpl["name"], "poster_url": tpl["thumb"], "to": None, "source": "template",
                    "template": tpl["slug"]}
    return None


def _examples(ctx: _Ctx) -> dict:
    """«Примеры»: на каждый вид — последняя принятая работа ленты (обложка и ссылка), а нет — готовый выпуск или
    шаблон. Одна работа — один пример: мувик не попадёт ещё и в «клип на песню»."""
    store = ctx.store
    works = [w for w in ctx.feed()["items"] if _accepted(w)]
    works.sort(key=lambda w: str((_accepted(w) or {}).get("status_at") or w["activity_at"] or ""), reverse=True)
    used: set[int] = set()
    cache: dict = {}
    items = []
    for spec in EXAMPLES:
        if not _module_on(store, spec["module"]):
            continue
        example: dict | None = None
        for work in works:
            if work["id"] in used or not _matches(spec, work):
                continue
            used.add(work["id"])
            accepted = _accepted(work) or {}
            example = {"title": work["title"], "poster_url": work["cover_url"], "to": f"/feed?work={work['id']}",
                       "source": "feed", "work_id": work["id"], "version": accepted.get("name") or "",
                       "project_name": work["project_name"]}
            break
        if example is None and spec.get("template"):
            example, _err = _guard("examples.template", _template_example)
        if example is None and spec.get("release_kinds"):
            example, _err = _guard("examples.release", lambda: _release_example(store, spec["release_kinds"], cache))
        items.append({"key": spec["key"], "type": spec["type"], "title": spec["title"], "text": spec["text"],
                      "example": example})
    return {"items": items}


BLOCKS: tuple[tuple[str, Callable[[_Ctx], Any]], ...] = (
    ("waiting", _waiting),
    ("recent_works", _recent_works),
    ("upcoming", _upcoming),
    ("top_week", _top_week),
    ("working", _working),
    ("spend", _spend),
    ("projects", _projects),
    ("examples", _examples),
)


def summary(store: Any, only: list[str] | tuple[str, ...] | None = None) -> dict:
    """Сводка «Главной». ``only`` — только эти блоки (для агента). Упавший блок — ``{"error": "…"}``."""
    ctx = _Ctx(store)
    out: dict[str, Any] = {"generated_at": _utc(_now()), "errors": []}
    wanted = set(only or ())
    for name, fn in BLOCKS:
        if wanted and name not in wanted:
            continue
        value, err = _guard(name, lambda fn=fn: fn(ctx))
        if err:
            out[name] = {"error": err}
            out["errors"].append(name)
        else:
            out[name] = value
    return out
