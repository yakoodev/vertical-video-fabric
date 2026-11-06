"""Автопилот: время, настройка, журнал, чтение карточек (из ``app/autopilot.py``)."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any, Callable

from app import projects as P

# --------------------------------------------------------------------------- #
# Схема
# --------------------------------------------------------------------------- #

AUTOPILOT_SCHEMA_SQL = [
    # Настройки автопилота проекта: доверие, слоты, лимиты, аккаунты, сводка; состояние
    # Telegram-темы проекта и сжатое резюме длинного чата (app/project_chat.py).
    """
    CREATE TABLE IF NOT EXISTS project_autopilot (
        project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
        config_json TEXT NOT NULL DEFAULT '{}',
        digest_sent_at TEXT,
        tg_thread_id INTEGER,
        tg_root_message_id INTEGER,
        chat_summary TEXT NOT NULL DEFAULT '',
        chat_summary_upto INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    # Элемент очереди одобрения. Время — UTC «ГГГГ-ММ-ДД ЧЧ:ММ:СС».
    # publish_status: '' (не дошло) / scheduled / publishing / published / failed / cancelled.
    # tg_state: '' (не слать) / queued / sent / failed — исходящие карточки отправляет фон.
    # dedupe_key — одна карточка на событие петли статистики («hyp:5:promote»).
    """
    CREATE TABLE IF NOT EXISTS approvals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
        kind TEXT NOT NULL DEFAULT 'clip_publish',
        status TEXT NOT NULL DEFAULT 'pending',
        clip_plan_id INTEGER REFERENCES clip_plans(id) ON DELETE SET NULL,
        clip_id INTEGER REFERENCES clips(id) ON DELETE SET NULL,
        rule_id INTEGER REFERENCES project_rules(id) ON DELETE SET NULL,
        hypothesis_id INTEGER,
        content_type TEXT NOT NULL DEFAULT '',
        trust TEXT NOT NULL DEFAULT 'approve',
        title TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT '',
        tags_json TEXT NOT NULL DEFAULT '[]',
        cover_path TEXT NOT NULL DEFAULT '',
        platforms_json TEXT NOT NULL DEFAULT '[]',
        proposed_at TEXT NOT NULL DEFAULT '',
        question TEXT NOT NULL DEFAULT '',
        payload_json TEXT NOT NULL DEFAULT '{}',
        submitted_by TEXT NOT NULL DEFAULT '',
        decision_comment TEXT NOT NULL DEFAULT '',
        decided_by TEXT NOT NULL DEFAULT '',
        decided_at TEXT,
        scheduled_at TEXT NOT NULL DEFAULT '',
        job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
        publish_status TEXT NOT NULL DEFAULT '',
        publish_error TEXT NOT NULL DEFAULT '',
        dedupe_key TEXT NOT NULL DEFAULT '',
        tg_state TEXT NOT NULL DEFAULT '',
        tg_attempts INTEGER NOT NULL DEFAULT 0,
        tg_error TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_approvals_project ON approvals(project_id, status, id)",
    "CREATE INDEX IF NOT EXISTS idx_approvals_dedupe ON approvals(dedupe_key)",
    # Лента автопилота: всё, что он сделал сам (auto), и все решения владельца.
    """
    CREATE TABLE IF NOT EXISTS approval_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        approval_id INTEGER REFERENCES approvals(id) ON DELETE CASCADE,
        project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
        event TEXT NOT NULL,
        actor TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '',
        data_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_approval_events ON approval_events(project_id, id)",
    # Какое сообщение Telegram к чему относится: ответ (reply) на него — к этой карточке
    # или к этому проекту (сводка, чат). purpose: card / comment / digest / chat / note.
    """
    CREATE TABLE IF NOT EXISTS tg_messages (
        chat_id TEXT NOT NULL,
        message_id INTEGER NOT NULL,
        approval_id INTEGER REFERENCES approvals(id) ON DELETE CASCADE,
        project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
        purpose TEXT NOT NULL DEFAULT 'card',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (chat_id, message_id)
    )
    """,
]


def ensure_autopilot_schema(conn) -> None:
    """Таблицы автопилота (вызывается из ``Database.init`` после проектов)."""
    for sql in AUTOPILOT_SCHEMA_SQL:
        conn.execute(sql)
    from app.agent_settings import ensure_agent_settings_schema
    from app.project_chat import ensure_chat_schema
    from app.vod_watch import ensure_vod_watch_schema

    ensure_agent_settings_schema(conn)
    ensure_chat_schema(conn)
    ensure_vod_watch_schema(conn)


# --------------------------------------------------------------------------- #
# Общее
# --------------------------------------------------------------------------- #

# alert — «внимание»: тревога статистики (0 просмотров через N часов, app/zero_views.py).
KINDS = ("clip_publish", "rule_proposal", "hypothesis_review", "other", "alert")
KIND_LABELS = {"clip_publish": "публикация клипа", "rule_proposal": "правило", "hypothesis_review": "статистика",
               "other": "вопрос", "alert": "внимание"}
STATUSES = ("pending", "approved", "rejected", "changes_requested", "expired")


TRUST_LEVELS = ("approve", "auto")
PLATFORMS = ("youtube", "tiktok", "instagram")


DEFAULT_TZ = "Europe/Moscow"
WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")


TIME_FMT = "%Y-%m-%d %H:%M:%S"

# Часы без tzdata (Windows без пакета tzdata): популярные пояса без летнего времени.
_FIXED_ZONES = {"europe/moscow": 3, "europe/kaliningrad": 2, "europe/samara": 4, "asia/yekaterinburg": 5,
                "asia/omsk": 6, "asia/novosibirsk": 7, "asia/krasnoyarsk": 7, "asia/irkutsk": 8,
                "asia/yakutsk": 9, "asia/vladivostok": 10, "asia/magadan": 11, "asia/kamchatka": 12,
                "europe/minsk": 3, "europe/istanbul": 3, "asia/almaty": 5, "asia/tashkent": 5, "asia/tbilisi": 4,
                "asia/yerevan": 4, "asia/baku": 4, "asia/dubai": 4, "asia/tokyo": 9, "asia/shanghai": 8}


def fmt_utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime(TIME_FMT)


def parse_utc(text: Any) -> datetime | None:
    raw = str(text or "").strip().replace("T", " ")
    if not raw:
        return None
    for fmt, size in ((TIME_FMT, 19), ("%Y-%m-%d %H:%M", 16)):
        try:
            return datetime.strptime(raw[:size], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def tz_of(name: Any) -> tzinfo:
    """Часовой пояс: IANA-имя («Europe/Moscow»), «UTC», «UTC+3», «+03:00»."""
    text = str(name or "").strip() or DEFAULT_TZ
    if text.upper() in ("UTC", "GMT", "Z"):
        return timezone.utc
    match = re.match(r"^(?:UTC|GMT)?\s*([+-])(\d{1,2})(?::?(\d{2}))?$", text, re.IGNORECASE)
    if match:
        sign = -1 if match.group(1) == "-" else 1
        delta = timedelta(hours=int(match.group(2)), minutes=int(match.group(3) or 0))
        if delta > timedelta(hours=14):
            raise ValueError("часовой пояс: сдвиг не больше ±14 ч")
        return timezone(sign * delta, text)
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(text)
    except Exception:  # noqa: BLE001 - нет tzdata или неизвестное имя
        hours = _FIXED_ZONES.get(text.lower())
        if hours is not None:
            return timezone(timedelta(hours=hours), text)
    raise ValueError(f"часовой пояс «{text}» не знаю — дай IANA-имя (Europe/Moscow) или сдвиг UTC+3")


def to_local(moment: datetime | None, tz_name: Any) -> str:
    if moment is None:
        return ""
    return moment.astimezone(tz_of(tz_name)).strftime("%Y-%m-%d %H:%M")


def parse_when(value: Any, tz_name: Any) -> datetime | None:
    """«ГГГГ-ММ-ДД ЧЧ:ММ» — время проекта (его поясом), ISO со сдвигом/Z — как есть. Пусто — None."""
    text = str(value or "").strip()
    if not text or text.lower() in ("slot", "next", "auto", "ближайший"):
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00").replace(" ", "T", 1))
    except ValueError as exc:
        raise ValueError("время — ГГГГ-ММ-ДД ЧЧ:ММ (пояс проекта) или ISO со сдвигом") from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=tz_of(tz_name))
    return moment.astimezone(timezone.utc)


def _loads(text: Any, default: Any) -> Any:
    return P._loads(text, default)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# Настройки автопилота проекта
# --------------------------------------------------------------------------- #

DEFAULT_CONFIG: dict = {
    "trust": {"default": "approve"},
    "timezone": DEFAULT_TZ,
    "slots": [],  # [{"days": [0..6], "times": ["12:00", "19:00"]}], 0 — понедельник
    "per_day": {"default": 0},  # не больше N в день на площадку; 0 — без лимита
    "platforms": [],  # площадки по умолчанию, если агент не указал
    "accounts": {},  # {"youtube": [account_id, …]}; пусто — единственный аккаунт площадки
    "privacy": "public",
    "expire_hours": 168,  # pending без ответа столько часов → expired; 0 — никогда
    "digest": {"enabled": True, "weekday": 0, "hour": 10},
    "auto_rules": "confirm",  # чат проекта: правило из разговора — confirm (спросить) / auto (сразу)
}


def _time_of_day(text: Any) -> tuple[int, int]:
    match = re.match(r"^(\d{1,2})(?::(\d{2}))?$", str(text or "").strip())
    if not match or int(match.group(1)) > 23 or int(match.group(2) or 0) > 59:
        raise ValueError(f"время слота «{text}» — ЧЧ:ММ")
    return int(match.group(1)), int(match.group(2) or 0)


def normalize_config(raw: Any) -> dict:
    """Проверить и дополнить настройки (недостающее — по умолчанию). Ошибка — ValueError."""
    raw = raw if isinstance(raw, dict) else {}
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    trust_in = raw.get("trust") if isinstance(raw.get("trust"), dict) else {}
    trust = {"default": "approve"}
    for key, level in trust_in.items():
        name = P.normalize_tag(key) or "default"
        level = str(level or "").strip().lower()
        if level not in TRUST_LEVELS:
            raise ValueError(f"доверие «{key}»: approve или auto")
        trust[name] = level
    cfg["trust"] = trust
    if raw.get("timezone"):
        tz_of(raw["timezone"])
        cfg["timezone"] = str(raw["timezone"]).strip()
    slots = []
    for slot in raw.get("slots") or []:
        if not isinstance(slot, dict):
            raise ValueError("слот — {days: [0..6], times: [\"ЧЧ:ММ\"]}")
        days = sorted({int(d) for d in (slot.get("days") if slot.get("days") is not None else range(7))})
        if any(d < 0 or d > 6 for d in days):
            raise ValueError("дни слота — 0 (пн) … 6 (вс)")
        times = sorted({"%02d:%02d" % _time_of_day(t) for t in slot.get("times") or []})
        if days and times:
            slots.append({"days": days, "times": times})
    cfg["slots"] = slots[:20]
    per_day = {"default": 0}
    for key, value in (raw.get("per_day") or {}).items() if isinstance(raw.get("per_day"), dict) else []:
        per_day[str(key).strip().lower() or "default"] = max(0, int(value or 0))
    cfg["per_day"] = per_day
    cfg["platforms"] = [p for p in (str(x).strip().lower() for x in raw.get("platforms") or []) if p in PLATFORMS]
    accounts = {}
    for platform, ids in (raw.get("accounts") or {}).items() if isinstance(raw.get("accounts"), dict) else []:
        platform = str(platform).strip().lower()
        if platform in PLATFORMS:
            accounts[platform] = [int(i) for i in (ids if isinstance(ids, list) else [ids]) if str(i).strip()]
    cfg["accounts"] = accounts
    privacy = str(raw.get("privacy") or "public").strip().lower()
    if privacy not in ("public", "unlisted", "private"):
        raise ValueError("privacy: public / unlisted / private")
    cfg["privacy"] = privacy
    if raw.get("expire_hours") is not None:
        cfg["expire_hours"] = max(0, int(raw["expire_hours"]))
    digest = raw.get("digest") if isinstance(raw.get("digest"), dict) else {}
    cfg["digest"] = {
        "enabled": bool(digest.get("enabled", True)),
        "weekday": min(6, max(0, int(digest.get("weekday", 0)))),
        "hour": min(23, max(0, int(digest.get("hour", 10)))),
    }
    auto_rules = str(raw.get("auto_rules") or "confirm").strip().lower()
    if auto_rules not in ("confirm", "auto"):
        raise ValueError("auto_rules: confirm или auto")
    cfg["auto_rules"] = auto_rules
    return cfg


def _state_row(store: Any, project_id: int) -> dict:
    row = store.db.query_one("SELECT * FROM project_autopilot WHERE project_id = ?", (int(project_id),))
    if row:
        return dict(row)
    P.get_project(store, project_id, include_sources=False)
    store.db.execute("INSERT OR IGNORE INTO project_autopilot (project_id) VALUES (?)", (int(project_id),))
    return dict(store.db.query_one("SELECT * FROM project_autopilot WHERE project_id = ?", (int(project_id),)))


def set_state(store: Any, project_id: int, **fields: Any) -> None:
    _state_row(store, project_id)
    sets = ", ".join(f"{k} = ?" for k in fields)
    store.db.execute(f"UPDATE project_autopilot SET {sets}, updated_at = CURRENT_TIMESTAMP WHERE project_id = ?",
                     (*fields.values(), int(project_id)))


def get_config(store: Any, project_id: int) -> dict:
    stored = _loads(_state_row(store, project_id).get("config_json"), {})
    try:
        return normalize_config(stored)
    except (ValueError, TypeError):
        return normalize_config({})


def update_config(store: Any, project_id: int, patch: dict) -> dict:
    """Частичное обновление: ключи верхнего уровня заменяются целиком."""
    if not isinstance(patch, dict):
        raise ValueError("настройки автопилота — объект")
    current = get_config(store, project_id)
    current.update({k: v for k, v in patch.items() if k in DEFAULT_CONFIG})
    cfg = normalize_config(current)
    for platform, ids in cfg["accounts"].items():
        for account_id in ids:
            account = store.get_account(int(account_id), include_secret=False)
            if account["platform"] != platform:
                raise ValueError(f"аккаунт #{account_id} — {account['platform']}, а не {platform}")
    set_state(store, project_id, config_json=_json(cfg))
    log_event(store, None, project_id, "config", "ui", "настройки автопилота изменены")
    return cfg


def trust_for(cfg: dict, content_type: str) -> str:
    trust = cfg.get("trust") or {}
    return trust.get(P.normalize_tag(content_type), trust.get("default", "approve")) if content_type else \
        trust.get("default", "approve")


def content_type_of(store: Any, cfg: dict, clip_plan_id: int | None, explicit: Any = "") -> str:
    """Тип контента: явный, иначе тег клипа, совпавший с ключом доверия (meme, type:meme, тип:мем)."""
    if str(explicit or "").strip():
        return P.normalize_tag(explicit)
    if not clip_plan_id:
        return ""
    keys = {k for k in (cfg.get("trust") or {}) if k != "default"}
    for tag in P.get_clip_tags(store, int(clip_plan_id)):
        name = re.sub(r"^(type|content|тип|контент):", "", tag)
        if name in keys:
            return name
    return ""


# --------------------------------------------------------------------------- #
# Лента
# --------------------------------------------------------------------------- #


def log_event(store: Any, approval_id: int | None, project_id: int | None, event: str, actor: str = "",
              note: str = "", data: dict | None = None) -> None:
    store.db.execute(
        "INSERT INTO approval_events (approval_id, project_id, event, actor, note, data_json) VALUES (?, ?, ?, ?, ?, ?)",
        (approval_id, project_id, event, str(actor or "")[:80], str(note or "")[:1000], _json(data or {})),
    )


def list_events(store: Any, project_id: int | None = None, limit: int = 100) -> list[dict]:
    sql = ("SELECT e.*, p.name AS project_name, a.title AS approval_title, a.kind AS approval_kind "
           "FROM approval_events e LEFT JOIN projects p ON p.id = e.project_id "
           "LEFT JOIN approvals a ON a.id = e.approval_id")
    params: list = []
    if project_id is not None:
        sql += " WHERE e.project_id = ?"
        params.append(int(project_id))
    sql += " ORDER BY e.id DESC LIMIT ?"
    params.append(max(1, min(int(limit), 500)))
    out = []
    for row in store.db.query_all(sql, params):
        data = dict(row)
        data["data"] = _loads(data.pop("data_json", "{}"), {})
        out.append(data)
    return out


# --------------------------------------------------------------------------- #
# Очередь одобрения
# --------------------------------------------------------------------------- #


def _approval_from_row(store: Any, row: Any) -> dict:
    data = dict(row)
    data["tags"] = _loads(data.pop("tags_json", "[]"), [])
    data["platforms"] = _loads(data.pop("platforms_json", "[]"), [])
    data["payload"] = _loads(data.pop("payload_json", "{}"), {})
    data["kind_label"] = KIND_LABELS.get(data["kind"], data["kind"])
    tz_name = DEFAULT_TZ
    if data.get("project_id"):
        try:
            tz_name = get_config(store, int(data["project_id"]))["timezone"]
        except KeyError:
            pass
    data["timezone"] = tz_name
    data["scheduled_local"] = to_local(parse_utc(data.get("scheduled_at")), tz_name)
    data["proposed_local"] = to_local(parse_utc(data.get("proposed_at")), tz_name)
    clip_id = data.get("clip_id")
    data["media_url"] = f"/media/clips/{clip_id}" if clip_id else None
    has_cover = bool(data.get("cover_path"))
    if clip_id and not has_cover:
        try:  # обложку могли выбрать уже после отправки (app/cover_variants.choose_cover)
            has_cover = bool(store.get_clip_cover_path(int(clip_id)))
        except (KeyError, AttributeError):
            has_cover = False
    data["cover_url"] = f"/media/clips/{clip_id}/cover" if clip_id and has_cover else None
    data["thumb_url"] = f"/media/clip-plans/{data['clip_plan_id']}/thumb" if data.get("clip_plan_id") else None
    data.pop("cover_path", None)
    data["buttons"] = [{"action": a, "label": label} for a, label in buttons_for(data)]
    return data


def buttons_for(approval: dict) -> list[tuple[str, str]]:
    """Кнопки карточки (одни и те же в UI и Telegram): (действие, подпись)."""
    kind, payload = approval["kind"], approval.get("payload") or {}
    if kind == "clip_publish":
        return [("approve", "✅ Публиковать"), ("changes", "✏️ Переделать"), ("reject", "🗑 В мусор")]
    if kind == "rule_proposal":
        if payload.get("action") == "revoke":
            return [("approve", "✅ Отменить правило"), ("reject", "Оставить как есть")]
        if payload.get("conflicts"):
            return [("approve", "🔁 Заменить старое"), ("both", "➕ Оставить оба"), ("reject", "🗑 Не записывать")]
        return [("approve", "✅ Записать"), ("reject", "🗑 Не надо")]
    if kind == "hypothesis_review":
        if payload.get("action") == "review_rule":
            return [("approve", "✅ Оставить правило"), ("reject", "🗑 Отменить правило")]
        return [("approve", "✅ Сделать правилом"), ("reject", "🗑 Не надо")]
    hook = CARD_ACTIONS.get(str(payload.get("action") or "")) if kind in ("other", "alert") else None
    return list(hook["buttons"]) if hook else [("approve", "✅ Да"), ("reject", "🗑 Нет")]


# Карточки-вопросы других модулей (kind other, payload.action) — свои кнопки и обработчик решения
# (app/vod_watch.py: «новый VOD — нарезать?», «мало места — повторить?»).
CARD_ACTIONS: dict[str, dict] = {}


def register_card_action(action: str, buttons: list[tuple[str, str]], on_decide: Callable[..., dict]) -> None:
    CARD_ACTIONS[action] = {"buttons": buttons, "on_decide": on_decide}


def get_approval(store: Any, approval_id: int) -> dict:
    row = store.db.query_one(
        "SELECT a.*, p.name AS project_name FROM approvals a LEFT JOIN projects p ON p.id = a.project_id "
        "WHERE a.id = ?", (int(approval_id),))
    if not row:
        raise KeyError(f"approval not found: {approval_id}")
    return _approval_from_row(store, row)


def list_approvals(store: Any, project_id: int | None = None, status: str | None = None,
                   kind: str | None = None, limit: int = 200) -> list[dict]:
    where, params = [], []
    if project_id is not None:
        where.append("a.project_id = ?")
        params.append(int(project_id))
    if status and status != "all":
        statuses = [s.strip() for s in str(status).split(",") if s.strip()]
        for s in statuses:
            P._choice(s, STATUSES, "status")
        where.append(f"a.status IN ({', '.join('?' for _ in statuses)})")
        params.extend(statuses)
    if kind:
        where.append("a.kind = ?")
        params.append(P._choice(kind, KINDS, "kind"))
    sql = "SELECT a.*, p.name AS project_name FROM approvals a LEFT JOIN projects p ON p.id = a.project_id"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY CASE a.status WHEN 'pending' THEN 0 ELSE 1 END, a.id DESC LIMIT ?"
    params.append(max(1, min(int(limit), 1000)))
    return [_approval_from_row(store, row) for row in store.db.query_all(sql, params)]


def approval_counts(store: Any) -> dict:
    rows = store.db.query_all("SELECT status, COUNT(*) AS n FROM approvals GROUP BY status")
    out = {s: 0 for s in STATUSES}
    out.update({r["status"]: int(r["n"]) for r in rows})
    return out


def _update(store: Any, approval_id: int, **fields: Any) -> None:
    sets = ", ".join(f"{k} = ?" for k in fields)
    store.db.execute(f"UPDATE approvals SET {sets}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (*fields.values(), int(approval_id)))


def _tg_on(store: Any) -> bool:
    from app import notify_tg

    return notify_tg.is_enabled(store)


def _insert(store: Any, **fields: Any) -> int:
    fields.setdefault("tg_state", "queued" if _tg_on(store) else "")
    cols = ", ".join(fields)
    cur = store.db.execute(f"INSERT INTO approvals ({cols}) VALUES ({', '.join('?' for _ in fields)})",
                           tuple(fields.values()))
    return int(cur.lastrowid)


def _clean_tags(tags: Any) -> list[str]:
    if isinstance(tags, str):
        tags = re.split(r"[\s,]+", tags)
    out = []
    for tag in tags or []:
        text = str(tag or "").strip().lstrip("#")
        text = re.sub(r"\s+", "_", text)[:60]
        if text and text not in out:
            out.append(text)
    return out[:30]


def _platforms(value: Any) -> list[str]:
    if isinstance(value, str):
        value = re.split(r"[\s,]+", value)
    out = []
    for item in value or []:
        name = str(item or "").strip().lower()
        if not name:
            continue
        if name not in PLATFORMS:
            raise ValueError(f"площадка «{item}»: {', '.join(PLATFORMS)}")
        if name not in out:
            out.append(name)
    return out
