"""Тревога «0 просмотров»: пост через N часов после публикации без просмотров (или ниже порога).

Почему: ролики, выложенные сервисом, бывает, вообще не попадают в рекомендации (разбор —
docs/design/publishing-review-2026-11.md: «не подходит для рекомендаций» у TikTok, ограничение
аккаунта, дубли и переиспользованный контент, автоматизация загрузки). Узнать это через сутки —
поздно: следующие ролики уходят в тот же «тёмный» аккаунт.

Что делает ``check`` (фон — проход ``StatsCollector``, раз в 10 мин):

* по каждой успешной публикации со ссылкой (``job_targets``), которой от ``hours`` до
  ``max_age_hours`` часов: нужен снимок чисел, снятый не раньше чем через ``hours`` после
  публикации. Нет — сходить за ним сейчас (по одной ссылке, с паузой, тем же yt-dlp/instagrapi, что
  ``app/platform_stats.py``); не вышло — ждём следующего прохода, тревогу не поднимаем;
* просмотров ``<= threshold`` (по умолчанию 0) — одна тревога на публикацию (``post_alerts``):
  карточка вида ``alert`` («внимание») в «Одобрении» и в Telegram (если бот настроен) — с вероятными
  причинами (что нашлось в наших данных: дубль, тот же клип уже выкладывали, у аккаунта ещё посты
  без просмотров, приватный пост, клип из чужого видео) и чек-листом площадки;
* просмотры пошли — тревога «прошла», ждущая карточка закрывается сама.

Настройки — «Публикации» → «Тревога 0 просмотров» (``app_settings`` ``zero_views_alert``), по
умолчанию из env: ``ZERO_VIEWS_ALERT`` (1/0), ``ZERO_VIEWS_HOURS`` (2), ``ZERO_VIEWS_THRESHOLD`` (0),
``ZERO_VIEWS_MAX_AGE_HOURS`` (72 — старые посты при включении не будят).
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta
from typing import Any, Callable

from app import autopilot as A

_log = logging.getLogger(__name__)

SETTINGS_KEY = "zero_views_alert"
CARD_ACTION = "zero_views"
TIME_FMT = "%Y-%m-%d %H:%M:%S"
PLATFORM_LABELS = {"youtube": "YouTube", "tiktok": "TikTok", "instagram": "Instagram"}

SCHEMA_SQL = [
    # Одна тревога на публикацию. status: open (ждёт) / checked (владелец проверил) / dismissed (ложная
    # тревога) / resolved (просмотры пошли сами).
    """
    CREATE TABLE IF NOT EXISTS post_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        target_id INTEGER NOT NULL UNIQUE REFERENCES job_targets(id) ON DELETE CASCADE,
        kind TEXT NOT NULL DEFAULT 'zero_views',
        platform TEXT NOT NULL DEFAULT '',
        url TEXT NOT NULL DEFAULT '',
        views INTEGER,
        threshold INTEGER NOT NULL DEFAULT 0,
        hours REAL NOT NULL DEFAULT 2,
        approval_id INTEGER,
        status TEXT NOT NULL DEFAULT 'open',
        findings_json TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        resolved_at TEXT
    )
    """,
]

# Чек-листы площадок (docs/design/publishing-review-2026-11.md, §3–5).
CHECKLISTS = {
    "tiktok": [
        "TikTok Studio → пост: нет ли статуса «Не подходит для рекомендаций» / «на проверке» (там же — причина)",
        "Настройки → Аккаунт → Account Check (статус аккаунта): нет ли ограничений на аккаунт и последние 30 постов",
        "Неоригинальный или переиспользованный материал «без нового» в «Для вас» не идёт — есть ли свой вклад",
        "Дубль: тот же файл на нескольких аккаунтах или повтор старого ролика — так не делать",
        "Водяные знаки других площадок в кадре",
        "Честный A/B: тот же новый ролик руками из TikTok Studio / с телефона — сравнить за 24 ч",
    ],
    "youtube": [
        "YouTube Studio → Контент: статус ролика «Ограничения» / «Проверка», аудитория «для детей»",
        "Шортс: вертикальный или квадратный, до 3 минут",
        "Переиспользованный контент (агрегатор чужих клипов) с 01.10.2026 теряет охват Shorts — свой вклад есть?",
        "Тот же ролик на нескольких каналах — дубль",
    ],
    "instagram": [
        "Профессиональная панель → Статус аккаунта: может ли аккаунт рекомендоваться",
        "Репост чужого и водяные знаки других площадок понижают Reels",
        "Тот же ролик на нескольких аккаунтах — дубль",
    ],
}


def ensure_schema(conn) -> None:
    for sql in SCHEMA_SQL:
        conn.execute(sql)


# --------------------------------------------------------------------------- #
# Настройки
# --------------------------------------------------------------------------- #


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def defaults() -> dict:
    return {"enabled": os.getenv("ZERO_VIEWS_ALERT", "1").strip().lower() not in ("0", "false", "no", "off"),
            "hours": _env_float("ZERO_VIEWS_HOURS", 2.0), "threshold": int(_env_float("ZERO_VIEWS_THRESHOLD", 0)),
            "max_age_hours": _env_float("ZERO_VIEWS_MAX_AGE_HOURS", 72.0)}


def normalize(raw: Any, base: dict | None = None) -> dict:
    cfg = dict(base or defaults())
    raw = raw if isinstance(raw, dict) else {}
    if raw.get("enabled") is not None:
        cfg["enabled"] = bool(raw["enabled"])
    for key, lo, hi, cast in (("hours", 0.25, 168, float), ("threshold", 0, 100000, int),
                              ("max_age_hours", 1, 24 * 30, float)):
        if raw.get(key) is None or raw.get(key) == "":
            continue
        try:
            value = cast(raw[key])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key}: число") from exc
        if not lo <= value <= hi:
            raise ValueError(f"{key}: от {lo:g} до {hi:g}")
        cfg[key] = value
    if cfg["max_age_hours"] < cfg["hours"]:
        cfg["max_age_hours"] = cfg["hours"]
    return cfg


def get_settings(store: Any) -> dict:
    return normalize(store.get_app_setting_value(SETTINGS_KEY, {}) or {})


def save_settings(store: Any, patch: dict) -> dict:
    cfg = normalize(patch, get_settings(store))
    store.set_app_setting(SETTINGS_KEY, cfg)
    return cfg


# --------------------------------------------------------------------------- #
# Проверка
# --------------------------------------------------------------------------- #

# Часы (тесты подменяют): наивное UTC, как CURRENT_TIMESTAMP в SQLite.
now: Callable[[], datetime] = lambda: datetime.utcnow()  # noqa: E731


def _parse(text: Any) -> datetime | None:
    try:
        return datetime.strptime(str(text or "")[:19], TIME_FMT)
    except ValueError:
        return None


def _snapshot_after(store: Any, target_id: int, after: datetime) -> dict | None:
    row = store.db.query_one(
        "SELECT * FROM publication_stats WHERE target_id = ? AND error = '' AND captured_at >= ? "
        "ORDER BY id DESC LIMIT 1", (int(target_id), after.strftime(TIME_FMT)))
    return dict(row) if row else None


def _attempted_after(store: Any, target_id: int, after: datetime) -> bool:
    row = store.db.query_one("SELECT 1 FROM publication_stats WHERE target_id = ? AND captured_at >= ? LIMIT 1",
                             (int(target_id), after.strftime(TIME_FMT)))
    return bool(row)


def default_fetch(store: Any, target: dict, ig_clients: dict) -> dict:
    from app.platform_stats import _fetch_one

    return _fetch_one(store, target, ig_clients)


def check(store: Any, fetch: bool = True, fetcher: Callable[[Any, dict, dict], dict] | None = None,
          sleep: Callable[[float], Any] = time.sleep, pause: float | None = None) -> dict:
    """Один проход: свежие снимки для «созревших» постов, тревоги, снятие прошедших тревог."""
    from app.platform_stats import StatsSkipped, platform_of_url
    from app.settings import settings

    cfg = get_settings(store)
    summary: dict = {"enabled": cfg["enabled"], "checked": 0, "fetched": 0, "alerts": [], "resolved": []}
    summary["resolved"] = resolve(store, cfg)
    if not cfg["enabled"]:
        return summary
    pause = settings.stats_pause_seconds if pause is None else pause
    fetcher = fetcher or default_fetch
    known = {int(r["target_id"]) for r in store.db.query_all("SELECT target_id FROM post_alerts")}
    current = now()
    ig_clients: dict = {}
    asked = False
    for target in store.list_published_targets():
        published = _parse(target.get("finished_at"))
        if published is None or int(target["id"]) in known:
            continue
        age = (current - published).total_seconds() / 3600
        if age < cfg["hours"] or age > cfg["max_age_hours"]:
            continue
        summary["checked"] += 1
        ripe = published + timedelta(hours=cfg["hours"])
        snap = _snapshot_after(store, int(target["id"]), ripe)
        if snap is None:
            if not fetch or _attempted_after(store, int(target["id"]), ripe):
                continue  # уже ходили после «созревания» и не вышло — подождём обычного сбора
            platform = platform_of_url(target.get("remote_url") or "") or target.get("platform") or ""
            if asked and pause > 0:
                sleep(pause)
            asked = True
            try:
                stats = fetcher(store, target, ig_clients)
            except StatsSkipped as exc:
                store.add_publication_stats(target["id"], platform, None, error=f"пропуск: {exc}")
                continue
            except Exception as exc:  # noqa: BLE001 - одна ссылка не валит проход
                store.add_publication_stats(target["id"], platform, None, error=str(exc)[:300] or "ошибка")
                continue
            store.add_publication_stats(target["id"], platform, stats)
            summary["fetched"] += 1
            snap = _snapshot_after(store, int(target["id"]), ripe)
        views = (snap or {}).get("views")
        if views is None or int(views) > int(cfg["threshold"]):
            continue
        summary["alerts"].append(raise_alert(store, target, int(views), cfg))
    return summary


def resolve(store: Any, cfg: dict | None = None) -> list[int]:
    """Открытые тревоги, у которых просмотры пошли, — «прошла»; ждущая карточка закрывается."""
    cfg = cfg or get_settings(store)
    latest = store.latest_publication_stats()
    done = []
    for row in store.db.query_all("SELECT * FROM post_alerts WHERE status = 'open'"):
        views = (latest.get(int(row["target_id"])) or {}).get("views")
        if views is None or int(views) <= int(row["threshold"]):
            continue
        store.db.execute("UPDATE post_alerts SET status = 'resolved', resolved_at = ? WHERE id = ?",
                         (now().strftime(TIME_FMT), int(row["id"])))
        if row["approval_id"]:
            store.db.execute(
                "UPDATE approvals SET status = 'expired', decided_by = 'system', decided_at = ?, decision_comment = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'pending'",
                (A._now_text(), f"просмотры пошли: {views}", int(row["approval_id"])))
            A.log_event(store, int(row["approval_id"]), None, "alert_resolved", "system", f"просмотров {views}")
        done.append(int(row["id"]))
    return done


# --------------------------------------------------------------------------- #
# Причины и тревога
# --------------------------------------------------------------------------- #


def likely_reasons(store: Any, target: dict, cfg: dict) -> list[str]:
    """Что видно в наших данных — от самого вероятного."""
    from app.platform_stats import platform_of_url

    platform = platform_of_url(target.get("remote_url") or "") or target.get("platform") or ""
    label = PLATFORM_LABELS.get(platform, platform)
    out: list[str] = []
    job = store.db.query_one("SELECT privacy FROM jobs WHERE id = ?", (int(target["job_id"]),))
    if job and job["privacy"] == "private":
        out.append("пост приватный — просмотров и не будет")
    # Ограничение аккаунта: у того же аккаунта ещё посты без просмотров.
    latest = store.latest_publication_stats()
    siblings = store.db.query_all(
        "SELECT id, finished_at FROM job_targets WHERE account_id = ? AND status = 'succeeded' AND remote_url != '' "
        "AND id != ? ORDER BY id DESC LIMIT 10", (int(target["account_id"]), int(target["id"])))
    dark = [s for s in siblings if (latest.get(int(s["id"])) or {}).get("views") is not None
            and int(latest[int(s["id"])]["views"]) <= int(cfg["threshold"])]
    if len(dark) >= 2:
        out.append(f"у аккаунта ещё {len(dark)} поста(ов) без просмотров — похоже на ограничение всего аккаунта "
                   "(Account Check)")
    # Дубли: тот же файл на нескольких аккаунтах площадки одним заданием; тот же клип раньше.
    same_job = store.db.query_one(
        "SELECT COUNT(*) AS n FROM job_targets WHERE job_id = ? AND platform = ? AND status = 'succeeded'",
        (int(target["job_id"]), target.get("platform") or platform))
    if same_job and int(same_job["n"]) > 1:
        out.append(f"тот же файл ушёл на {same_job['n']} аккаунта {label} одним заданием — площадка видит дубли")
    if target.get("clip_id"):
        earlier = store.db.query_one(
            "SELECT j.id FROM job_targets jt JOIN jobs j ON j.id = jt.job_id WHERE j.clip_id = ? AND jt.platform = ? "
            "AND jt.status = 'succeeded' AND jt.id < ? AND j.id != ? ORDER BY jt.id LIMIT 1",
            (int(target["clip_id"]), target.get("platform") or platform, int(target["id"]), int(target["job_id"])))
        if earlier:
            out.append(f"этот клип уже выкладывался на {label} (задание #{earlier['id']}) — повтор считается дублем")
        try:
            clip = store.get_clip(int(target["clip_id"]))
            source = store.get_source(int(clip["source_id"]))
            if str(source.get("original_url") or "").strip():
                out.append("клип нарезан из чужого видео — без своего вклада (комментарий, история, монтаж) такой "
                           "материал в рекомендации не пускают")
        except (KeyError, TypeError, ValueError):
            pass
    if platform == "tiktok":
        out.append("загрузка сервисом — автоматизация веб-эндпоинтов TikTok: сигнал риска; проверь статус поста")
    return out


def _plan_and_project(store: Any, target: dict) -> tuple[int | None, int | None]:
    from app import projects as P

    if not target.get("clip_id"):
        return None, None
    try:
        plan_id = store.get_clip(int(target["clip_id"])).get("clip_plan_id")
    except KeyError:
        return None, None
    if not plan_id:
        return None, None
    try:
        return int(plan_id), P.project_of_plan(store, int(plan_id))
    except KeyError:
        return int(plan_id), None


def raise_alert(store: Any, target: dict, views: int, cfg: dict) -> dict:
    """Тревога: строка ``post_alerts`` + карточка «внимание» (UI и Telegram)."""
    from app import projects as P
    from app.platform_stats import platform_of_url

    platform = platform_of_url(target.get("remote_url") or "") or target.get("platform") or ""
    label = PLATFORM_LABELS.get(platform, platform or "площадка")
    findings = likely_reasons(store, target, cfg)
    checklist = CHECKLISTS.get(platform, [])
    plan_id, project_id = _plan_and_project(store, target)
    hours = f"{cfg['hours']:g}"
    title = P._text(target.get("title") or f"публикация #{target['job_id']}", 120)
    head = (f"⚠️ «{title}» на {label}: {views} просмотров через {hours} ч после публикации"
            + (f" (порог {cfg['threshold']})" if cfg["threshold"] else ""))
    parts = [head, target.get("remote_url") or ""]
    if findings:
        parts.append("Вероятные причины:\n" + "\n".join(f"• {f}" for f in findings))
    if checklist:
        parts.append("Проверь:\n" + "\n".join(f"☐ {c}" for c in checklist))
    question = "\n\n".join(p for p in parts if p)
    payload = {"action": CARD_ACTION, "target_id": int(target["id"]), "job_id": int(target["job_id"]),
               "platform": platform, "url": target.get("remote_url") or "", "views": views, "hours": cfg["hours"],
               "findings": findings, "checklist": checklist}
    approval = A.submit_card(store, project_id, "alert", question, payload, title=f"0 просмотров: {title}"[:200],
                             dedupe_key=f"{CARD_ACTION}:{int(target['id'])}", submitted_by="stats")
    if plan_id:
        store.db.execute("UPDATE approvals SET clip_plan_id = ? WHERE id = ?", (plan_id, int(approval["id"])))
    store.db.execute(
        "INSERT OR IGNORE INTO post_alerts (target_id, platform, url, views, threshold, hours, approval_id, "
        "findings_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (int(target["id"]), platform, target.get("remote_url") or "", views, int(cfg["threshold"]), cfg["hours"],
         int(approval["id"]), A._json(findings)))
    sent = False
    try:  # сразу в Telegram, не дожидаясь прохода автопилота
        from app import notify_tg

        if notify_tg.is_enabled(store) and not approval.get("deduplicated"):
            sent = bool(notify_tg.send_card(store, int(approval["id"])).get("ok"))
    except Exception as exc:  # noqa: BLE001 - Telegram лёг — карточка всё равно в UI
        _log.info("zero views alert to telegram failed: %s", exc)
    return {"target_id": int(target["id"]), "approval_id": int(approval["id"]), "views": views,
            "platform": platform, "findings": findings, "telegram": sent}


def _on_decide(store: Any, approval: dict, action: str, comment: str, actor: str) -> dict:
    status = "checked" if action in ("approve", "both") else "dismissed"
    target_id = (approval.get("payload") or {}).get("target_id")
    if target_id:
        store.db.execute("UPDATE post_alerts SET status = ?, resolved_at = ? WHERE target_id = ? AND status = 'open'",
                         (status, now().strftime(TIME_FMT), int(target_id)))
    return {"alert": status}


A.register_card_action(CARD_ACTION, [("approve", "✅ Проверил"), ("reject", "🔕 Ложная тревога")], _on_decide)


# --------------------------------------------------------------------------- #
# Значки «Статистика» для UI
# --------------------------------------------------------------------------- #


def alerts_by_target(store: Any) -> dict[int, dict]:
    rows = store.db.query_all("SELECT target_id, views, hours, threshold, status, approval_id, created_at "
                              "FROM post_alerts")
    return {int(r["target_id"]): dict(r) for r in rows}


def clip_badges(store: Any) -> dict[int, dict]:
    """clip_id → {views, likes, captured_at, publications, alert (открытая тревога: просмотры, часы)}."""
    latest = store.latest_publication_stats()
    alerts = alerts_by_target(store)
    out: dict[int, dict] = {}
    for target in store.list_published_targets():
        clip_id = target.get("clip_id")
        if not clip_id:
            continue
        badge = out.setdefault(int(clip_id), {"views": None, "likes": None, "captured_at": None, "publications": 0,
                                              "alert": None})
        badge["publications"] += 1
        stats = latest.get(int(target["id"])) or {}
        for key in ("views", "likes"):
            if stats.get(key) is not None:
                badge[key] = (badge[key] or 0) + int(stats[key])
        if stats.get("captured_at") and (badge["captured_at"] or "") < stats["captured_at"]:
            badge["captured_at"] = stats["captured_at"]
        alert = alerts.get(int(target["id"]))
        if alert and alert["status"] == "open":
            badge["alert"] = {"views": alert["views"], "hours": alert["hours"], "approval_id": alert["approval_id"],
                              "platform": target.get("platform") or ""}
    return out
