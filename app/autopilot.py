"""Автопилот канала: очередь одобрения, доверие, расписание, обучение на ответах владельца.

Замысел владельца: «было бы круто если бы gemini сам мог вести целые каналы»;
«система одобрения и как бы его заставлять самого становится умнее в зависимости от
того что я ему пишу. если я один раз в конкретном проекте написал что что то не нужно,
то он это запомнит. и наоборот. но и если я написал что я пересмотрел контент и старое
правило больше не работает он его забыл».

Что здесь (поверх проектов-серий, app/projects.py):

* ``approvals`` — очередь одобрения. Элемент: проект, тип (clip_publish / rule_proposal /
  hypothesis_review / other), клип или правило, подготовленные название, описание, теги,
  площадки, предлагаемое время. Статус pending → approved / rejected / changes_requested /
  expired; кто и когда решил, комментарий. **Без approved ничего не публикуется**: задание
  публикации (``jobs``) создаётся только из одобренного элемента.
* Доверие проекта по типам контента (``trust``: ``{"meme": "auto", "default": "approve"}``):
  auto пропускает одобрение, но всё равно пишет в ленту (``approval_events``).
* Расписание: слоты проекта (дни недели, часы, часовой пояс), не больше N в день на
  площадку. Одобренный клип встаёт в ближайший свободный слот; публикует обычный JobWorker
  по ``jobs.scheduled_at`` (UTC, как CURRENT_TIMESTAMP в SQLite).
* Обучение: комментарий к «Переделать»/«В мусор» и свободный ответ владельца → правило
  проекта через ``projects.add_rule`` (цитата, дата, ``approval#id``). Классификация — простые
  эвристики без платного ИИ; для сложных — хук ``register_reply_classifier``; не уверены —
  карточка rule_proposal «Записать правило: …?». «Пересмотрел / больше не работает /
  забудь правило» → поиск похожего активного правила → предложение отменить
  (``revoke_rule`` с причиной-цитатой). Противоречие — вопрос владельцу (механизм projects).
* Петля статистики: после фонового сбора чисел ``after_stats`` пересчитывает гипотезы
  проектов; подтверждённые и правила «на пересмотр» → карточки hypothesis_review.
  Раз в неделю — сводка проекта.

Доставка карточек владельцу — ``app/notify_tg.py`` (Telegram Bot API, отключаемый);
без него карточки живут только в UI («Одобрение»). Фон — ``AutopilotWorker``.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any, Callable

from app import projects as P

_log = logging.getLogger(__name__)

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
    from app.project_chat import ensure_chat_schema
    from app.vod_watch import ensure_vod_watch_schema

    ensure_chat_schema(conn)
    ensure_vod_watch_schema(conn)


# --------------------------------------------------------------------------- #
# Общее
# --------------------------------------------------------------------------- #

KINDS = ("clip_publish", "rule_proposal", "hypothesis_review", "other")
KIND_LABELS = {"clip_publish": "публикация клипа", "rule_proposal": "правило", "hypothesis_review": "статистика",
               "other": "вопрос"}
STATUSES = ("pending", "approved", "rejected", "changes_requested", "expired")
ACTIONS = ("approve", "reject", "changes", "both")
TRUST_LEVELS = ("approve", "auto")
PLATFORMS = ("youtube", "tiktok", "instagram")
PLATFORM_LABELS = {"youtube": "YouTube", "tiktok": "TikTok", "instagram": "Instagram"}
DEFAULT_TZ = "Europe/Moscow"
WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
SLOT_SEARCH_DAYS = 28
TIME_FMT = "%Y-%m-%d %H:%M:%S"

# Часы без tzdata (Windows без пакета tzdata): популярные пояса без летнего времени.
_FIXED_ZONES = {"europe/moscow": 3, "europe/kaliningrad": 2, "europe/samara": 4, "asia/yekaterinburg": 5,
                "asia/omsk": 6, "asia/novosibirsk": 7, "asia/krasnoyarsk": 7, "asia/irkutsk": 8,
                "asia/yakutsk": 9, "asia/vladivostok": 10, "asia/magadan": 11, "asia/kamchatka": 12,
                "europe/minsk": 3, "europe/istanbul": 3, "asia/almaty": 5, "asia/tashkent": 5, "asia/tbilisi": 4,
                "asia/yerevan": 4, "asia/baku": 4, "asia/dubai": 4, "asia/tokyo": 9, "asia/shanghai": 8}

# Часы сервиса: тесты подменяют ``autopilot.now``.
now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)  # noqa: E731


def _now_text() -> str:
    return now().astimezone(timezone.utc).strftime(TIME_FMT)


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
    hook = CARD_ACTIONS.get(str(payload.get("action") or "")) if kind == "other" else None
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


def submit_clip(store: Any, clip_plan_id: int, title: str = "", description: str = "", tags: Any = None,
                platforms: Any = None, when: Any = None, content_type: Any = "", submitted_by: str = "agent") -> dict:
    """Клип на одобрение. Нужен готовый рендер и проект у источника. Доверие auto —
    сразу одобрено (и в ленту), иначе pending + карточка владельцу. Прежняя pending-карточка
    того же клипа уходит в expired: на руках у владельца — одна, свежая."""
    plan = store.get_clip_plan(int(clip_plan_id), include_segments=False)
    project_id = P.project_of_plan(store, int(clip_plan_id))
    if project_id is None:
        raise ValueError(f"источник клипа #{clip_plan_id} не в проекте-серии — без проекта нет доверия и расписания")
    clips = [c for c in store.list_clips_for_plan(int(clip_plan_id)) if c.get("status") == "succeeded"]
    if not clips:
        raise ValueError(f"у клипа #{clip_plan_id} нет готового рендера — сначала render")
    clip = clips[0]
    cfg = get_config(store, project_id)
    platforms_list = _platforms(platforms) or list(cfg["platforms"])
    proposed = parse_when(when, cfg["timezone"])
    ctype = content_type_of(store, cfg, int(clip_plan_id), content_type)
    trust = trust_for(cfg, ctype)
    # Заголовок, описание и хэштеги, что сервис написал после рендера (app/ai/publish_meta.py), и
    # выбранная обложка (app/cover_variants.py) — в карточку, если агент своих не дал.
    meta = clip.get("publish_meta") or {}
    title = P._text(title or clip.get("title") or plan.get("title") or f"Клип #{clip_plan_id}", 200)
    description = P._long_text(description or clip.get("description") or "", 4000)
    if not tags and meta.get("hashtags"):
        tags = meta["hashtags"]
    cover_path = ""
    getter = getattr(store, "get_clip_cover_path", None)
    if getter:
        try:
            cover_path = str(getter(int(clip["id"])) or "")
        except KeyError:
            cover_path = ""
    approval_id = _insert(
        store, project_id=project_id, kind="clip_publish", clip_plan_id=int(clip_plan_id), clip_id=int(clip["id"]),
        content_type=ctype, trust=trust, title=title, description=description, tags_json=_json(_clean_tags(tags)),
        cover_path=cover_path, platforms_json=_json(platforms_list),
        proposed_at=fmt_utc(proposed) if proposed else "", submitted_by=str(submitted_by or "")[:40],
        tg_state="" if trust == "auto" else ("queued" if _tg_on(store) else ""),
    )
    for old in store.db.query_all(
        "SELECT id FROM approvals WHERE clip_plan_id = ? AND kind = 'clip_publish' AND status = 'pending' AND id != ?",
        (int(clip_plan_id), approval_id),
    ):
        _update(store, old["id"], status="expired", decided_at=_now_text(), decided_by="system",
                decision_comment=f"заменено новой версией #{approval_id}")
        log_event(store, old["id"], project_id, "expired", "system", f"заменено #{approval_id}")
    lint = pre_submit_lint(store, int(clip_plan_id), platforms_list)
    log_event(store, approval_id, project_id, "submitted", submitted_by, title,
              {"content_type": ctype, "trust": trust, "platforms": platforms_list,
               **({"lint": lint["counts"]} if lint else {})})
    if trust == "auto":
        _update(store, approval_id, status="approved", decided_by="auto", decided_at=_now_text(),
                decision_comment=f"доверие проекта: {ctype or 'default'} = auto")
        log_event(store, approval_id, project_id, "auto_approved", "auto",
                  f"без одобрения: доверие «{ctype or 'по умолчанию'}» = auto")
        _place(store, approval_id, actor="auto")
    out = get_approval(store, approval_id)
    if lint:
        out["lint"] = lint
    return out


def pre_submit_lint(store: Any, clip_plan_id: int, platforms: list[str]) -> dict | None:
    """Линт качества перед сдачей (app/quality_lint.py: зоны площадок, темп чтения, вспышки и луп по
    готовому рендеру) — только предупреждения: отправку не блокирует, ошибка линта не роняет её.
    Коротко: ``{counts, summary, warnings}``."""
    from app import quality_lint

    try:
        return quality_lint.brief(quality_lint.lint_clip(store, clip_plan_id, platforms=platforms or None))
    except Exception as exc:  # noqa: BLE001 - линт — подсказка, не условие отправки
        _log.warning("pre-submit lint for clip plan %s failed: %s", clip_plan_id, exc)
        return None


def submit_card(store: Any, project_id: int, kind: str, question: str, payload: dict | None = None,
                title: str = "", rule_id: int | None = None, hypothesis_id: int | None = None,
                dedupe_key: str = "", submitted_by: str = "system") -> dict:
    """Карточка-вопрос владельцу (правило, статистика, что угодно). С dedupe_key — не
    повторяем, пока владелец не ответил или уже ответил (кроме expired)."""
    kind = P._choice(kind, KINDS, "kind")
    P.get_project(store, int(project_id), include_sources=False)
    if dedupe_key:
        row = store.db.query_one(
            "SELECT id FROM approvals WHERE dedupe_key = ? AND status != 'expired' ORDER BY id DESC LIMIT 1",
            (dedupe_key,))
        if row:
            return get_approval(store, row["id"]) | {"deduplicated": True}
    approval_id = _insert(
        store, project_id=int(project_id), kind=kind, title=P._text(title or question, 200),
        question=P._long_text(question, 2000), payload_json=_json(payload or {}), rule_id=rule_id,
        hypothesis_id=hypothesis_id, dedupe_key=dedupe_key, submitted_by=str(submitted_by or "")[:40],
    )
    log_event(store, approval_id, int(project_id), "submitted", submitted_by, question[:300])
    return get_approval(store, approval_id)


def propose_rule(store: Any, project_id: int, text: str, kind: str = "do", quote: str = "", action: str = "add",
                 rule_id: int | None = None, origin: str = "agent", origin_ref: str = "", said_at: Any = None,
                 replaces: list | None = None, conflicts: list | None = None, question: str = "") -> dict:
    """Карточка rule_proposal: «Записать правило: …?» / «Отменить правило #…?»."""
    action = P._choice(action, ("add", "revoke"), "action")
    if action == "add":
        text = P._text(text, 500)
        if not text:
            raise ValueError("нужен текст правила")
        kind = P._choice(kind, P.RULE_KINDS, "kind", "do")
        word = {"do": "делать", "dont": "не делать", "preference": "предпочтение"}[kind]
        question = question or f"Записать правило ({word}): «{text}»?"
    else:
        rule = P.get_rule(store, int(rule_id))
        question = question or f"Отменить правило #{rule['id']} «{rule['text']}»?"
    payload = {"action": action, "text": text, "kind": kind, "quote": P._long_text(quote, 2000), "rule_id": rule_id,
               "origin": origin, "origin_ref": origin_ref, "said_at": str(said_at or "")[:19],
               "replaces": [int(r) for r in replaces or []], "conflicts": conflicts or []}
    return submit_card(store, project_id, "rule_proposal", question, payload, title=question,
                       rule_id=int(rule_id) if rule_id else None, submitted_by=origin)


# --------------------------------------------------------------------------- #
# Решение владельца
# --------------------------------------------------------------------------- #

DECISION_STATUS = {"approve": "approved", "both": "approved", "reject": "rejected", "changes": "changes_requested"}


def decide(store: Any, approval_id: int, action: str, comment: str = "", actor: str = "ui") -> dict:
    """Публиковать / Переделать / В мусор (и «Оставить оба» у противоречия правил).
    Решать можно только pending. Комментарий к «Переделать»/«В мусор» — урок проекту."""
    action = P._choice(action, ACTIONS, "action")
    approval = get_approval(store, approval_id)
    if approval["status"] != "pending":
        raise ValueError(f"карточка #{approval_id} уже решена: {approval['status']}")
    comment = P._long_text(comment, 2000)
    status = DECISION_STATUS[action]
    _update(store, approval_id, status=status, decision_comment=comment, decided_by=str(actor or "")[:80],
            decided_at=_now_text())
    log_event(store, approval_id, approval["project_id"], status, actor, comment)
    result: dict = {"ok": True, "status": status}
    if status == "approved":
        result.update(_on_approved(store, approval, action, comment, actor))
    elif comment and approval["kind"] == "clip_publish":
        result["learned"] = learn_from_text(store, approval["project_id"], comment, origin=_origin(actor),
                                            origin_ref=f"approval#{approval_id}", approval_id=approval_id,
                                            context={"title": approval["title"]})
    elif status == "rejected":
        result.update(_on_rejected(store, approval, comment, actor))
    hook = CARD_ACTIONS.get(str(approval["payload"].get("action") or "")) if approval["kind"] == "other" else None
    if hook:
        result.update(hook["on_decide"](store, approval, action, comment, actor) or {})
    result["approval"] = get_approval(store, approval_id)
    return result


def add_comment(store: Any, approval_id: int, text: str, actor: str = "tg") -> dict:
    """Текст к уже решённой карточке («Переделать» кнопкой, а что переделать — ответом)."""
    approval = get_approval(store, approval_id)
    text = P._long_text(text, 2000)
    joined = (approval["decision_comment"] + "\n" + text).strip() if approval["decision_comment"] else text
    _update(store, approval_id, decision_comment=joined[:4000])
    log_event(store, approval_id, approval["project_id"], "comment", actor, text)
    learned = None
    if approval["kind"] == "clip_publish" and approval["project_id"]:
        learned = learn_from_text(store, approval["project_id"], text, origin=_origin(actor),
                                  origin_ref=f"approval#{approval_id}", approval_id=approval_id,
                                  context={"title": approval["title"]})
    return {"ok": True, "approval": get_approval(store, approval_id), "learned": learned}


def _origin(actor: str) -> str:
    return "tg" if str(actor).startswith("tg") else ("chat" if str(actor).startswith("chat") else "ui")


def _owner_quote(approval: dict, comment: str) -> str:
    return comment or f"«{approval['question'] or approval['title']}» — да (approval#{approval['id']})"


def _on_approved(store: Any, approval: dict, action: str, comment: str, actor: str) -> dict:
    kind, payload = approval["kind"], approval["payload"]
    project_id = approval["project_id"]
    if kind == "clip_publish":
        return {"placement": _place(store, approval["id"], actor=actor)}
    if kind == "rule_proposal":
        if payload.get("action") == "revoke":
            quote = payload.get("quote") or comment
            res = P.revoke_rule(store, int(payload["rule_id"]), reason=quote or "отменено владельцем", quote=quote)
            if res.get("revoked"):
                log_event(store, approval["id"], project_id, "rule_revoked", actor,
                          f"#{payload['rule_id']} {res['rule']['text']}", {"rule_id": payload["rule_id"]})
            return {"rule": res}
        res = P.add_rule(
            store, payload.get("text") or "", kind=payload.get("kind") or "do", project_id=project_id,
            quote=payload.get("quote") or _owner_quote(approval, comment),
            origin=payload.get("origin") or _origin(actor), origin_ref=f"approval#{approval['id']}",
            said_at=payload.get("said_at") or None,
            replaces=payload.get("replaces") if action == "approve" else None, force=action == "both",
        )
        return {"rule": _rule_outcome(store, approval, res, actor)}
    if kind == "hypothesis_review":
        if payload.get("action") == "promote":
            res = P.promote_hypothesis(store, int(payload["hypothesis_id"]), text=payload.get("text") or "",
                                       quote=_owner_quote(approval, comment), force=action == "both")
            return {"rule": _rule_outcome(store, approval, res, actor)}
        if payload.get("action") == "review_rule":  # «оставить правило» — снимаем пометку
            store.db.execute("UPDATE project_rules SET needs_review = 0, review_note = '', "
                             "updated_at = CURRENT_TIMESTAMP WHERE id = ?", (int(payload["rule_id"]),))
            log_event(store, approval["id"], project_id, "rule_kept", actor, f"#{payload['rule_id']}")
            return {"rule_kept": int(payload["rule_id"])}
    return {}


def _on_rejected(store: Any, approval: dict, comment: str, actor: str) -> dict:
    payload = approval["payload"]
    if approval["kind"] == "hypothesis_review" and payload.get("action") == "review_rule":
        quote = comment or f"«{approval['question']}» — отменить (approval#{approval['id']})"
        res = P.revoke_rule(store, int(payload["rule_id"]), reason="статистика перестала подтверждать", quote=quote)
        if res.get("revoked"):
            log_event(store, approval["id"], approval["project_id"], "rule_revoked", actor, f"#{payload['rule_id']}")
        return {"rule": res}
    return {}


def _rule_outcome(store: Any, approval: dict, res: dict, actor: str) -> dict:
    """Итог add_rule: записано / дубль / противоречие → новая карточка-вопрос владельцу."""
    project_id = approval["project_id"]
    if res.get("created"):
        rule = res["rule"]
        log_event(store, approval["id"], project_id, "rule_added", actor, f"#{rule['id']} {rule['text']}",
                  {"rule_id": rule["id"], "superseded": res.get("superseded") or []})
    elif res.get("needs_confirmation"):
        payload = approval["payload"]
        conflicts = res.get("conflicts") or []
        # Вопрос владельцу человеческими словами (у add_rule он для агента: replaces/force).
        old = "; ".join(f"#{c['id']} «{c['text']}»" + (f" от {str(c.get('said_at'))[:10]}" if c.get("said_at") else "")
                        for c in conflicts[:3])
        res = {**res, "question": f"Новое правило «{payload.get('text') or approval['title']}» противоречит "
                                  f"действующему: {old}. Заменить старое или оставить оба?"}
        card = propose_rule(
            store, project_id, payload.get("text") or approval["title"], kind=payload.get("kind") or "do",
            quote=payload.get("quote") or approval["decision_comment"], origin=payload.get("origin") or "ui",
            said_at=payload.get("said_at") or None, replaces=[c["id"] for c in conflicts], conflicts=conflicts,
            question=res.get("question") or "",
        )
        res = {**res, "proposal_id": card["id"]}
    return res


# --------------------------------------------------------------------------- #
# Обучение на ответах владельца
# --------------------------------------------------------------------------- #

_REVOKE_RE = re.compile(
    r"(пересмотр|больше не работа|уже не работа|не актуальн|неактуальн|забудь|забыть правило|отмени(ть)? правило|"
    r"отменяю правило|сними правило|правило устарел|можно снова|снова можно|теперь можно|уже можно|"
    r"больше не нужно правило|forget the rule|no longer)", re.IGNORECASE)
_DONT_RE = re.compile(
    r"(^|[^\w])(не|без|убери|убрать|уберите|удали|удалить|выкинь|хватит|нельзя|перестань|избегай|никогда|"
    r"слишком|не надо|не нужно|не бери|не ставь|no|don't|dont|never|avoid|stop)(?=[^\w]|$)", re.IGNORECASE)
_DO_RE = re.compile(
    r"(круто|так и делай|так держать|так и надо|больше такого|ещё такого|еще такого|отлично|супер|класс|нравится|"
    r"огонь|(?<![\w])делай так(?![\w])|продолжай|в том же духе|всегда|обязательно|почаще|побольше|давай больше|заходит|заходят|"
    r"залетает|залетают|keep doing|love)",
    re.IGNORECASE)
# Это про конкретный клип, а не правило на будущее.
_ONE_OFF_RE = re.compile(r"(в этом клипе|в этом ролике|тут|здесь|на \d+[:.]\d\d|\b\d+:\d\d\b|на \d+ ?сек|в начале|"
                         r"в конце|этот кадр|эту сцену)", re.IGNORECASE)
# Признак правила на будущее: обобщение («всегда», «больше не», «такие моменты»), оценка типа
# контента («такое заходит»), «не бери / не ставь», явное «запомни».
_GENERAL_RE = re.compile(
    r"(всегда|никогда|больше не|впредь|в будущем|на будущее|каждый раз|в каждом|во всех|по умолчанию|"
    r"в следующий раз|в этом проекте|на этом канале|у нас на канале|вообще|"
    r"(^|[^\w])(такие|такое|таких|таким)(?=[^\w]|$)(?!\s+же)|"
    r"(не )?заход(ит|ят)|залетает|не залетает|"
    r"не бери|не берите|не брать|не ставь|не ставьте|не ставить|не делай так|не надо так|"
    r"так и делай|(^|[^\w])делай так(?=[^\w]|$)|так держать|почаще|пореже|побольше|поменьше|"
    r"запомни|запиши правило|возьми за правило|always|never|from now on|remember)", re.IGNORECASE)
# Разовая команда: повелительный глагол про конкретный объект («убери …», «удали этот …», «сделай …»).
_COMMAND_RE = re.compile(
    r"(^|[.!?,;:—\-]\s*|\s(и|а|потом|ещё|еще|теперь)\s+)(убери|уберите|удали|удалите|сделай|сделайте|поставь|"
    r"поставьте|переделай|переделайте|перемонтируй|добавь|добавьте|вставь|обрежь|обрежьте|подрежь|вырежи|вырежьте|"
    r"сократи|замени|поменяй|сдвинь|подвинь|исправь|поправь|заглуши|запикай|приглуши|увеличь|уменьши|"
    r"смонтируй|сгенерируй|покажи|найди|открой|опубликуй|отправь|запусти|нарежь|скачай|склей|верни|откати|"
    r"перенеси|выкинь|выкиньте|почисти|наложи|подставь|переименуй|назови)(?=[^\w]|$)", re.IGNORECASE)
# Указание на конкретное: этот/здесь/тут, номер клипа, секунда, таймкод.
_DEICTIC_RE = re.compile(
    r"((^|[^\w])(этот|эта|эту|этого|этой|этом|эти|этих|здесь|тут|вот тут|вот здесь)(?=[^\w]|$)|#\s?\d+|"
    r"клип[а-я]*\s+\d+|\d+:\d\d|\d+(?:[.,]\d+)?\s?(с|сек|секунд[а-я]*)(?=[^\w]|$))", re.IGNORECASE)
_NOT_LANDS_RE = re.compile(r"не\s+(заход|залета)\w*", re.IGNORECASE)
_PROJECT_SCOPE_RE = re.compile(r"(в этом проекте|на этом канале)", re.IGNORECASE)


def rule_signal(text: str) -> str:
    """general — есть признак правила на будущее; command — разовая команда к конкретному
    (повелительный глагол или «этот/здесь/тут», номер клипа, секунда) без признака обобщения;
    neutral — ни того ни другого (спорно: «без мата», «слишком длинно»)."""
    raw = str(text or "")
    if _GENERAL_RE.search(raw):
        return "general"
    deictic = _DEICTIC_RE.search(_PROJECT_SCOPE_RE.sub(" ", raw))
    if _COMMAND_RE.search(raw) or deictic:
        return "command"
    return "neutral"


_PRAISE_FILLER_RE = re.compile(
    r"(круто|так и делай|так держать|так и надо|отлично|супер|класс|огонь|продолжай|в том же духе|"
    r"нравится|мне|пожалуйста|плиз|плз|пж|и переделай|переделай|перемонтируй|давай)", re.IGNORECASE)
APPROVE_WORDS = {"да", "ок", "окей", "ok", "го", "публикуй", "норм", "нормально", "годно", "одобряю", "+", "👍",
                 "yes", "давай", "ага", "пиши", "запиши", "записывай", "согласен", "верно", "да, запиши"}
REJECT_WORDS = {"нет", "мусор", "в мусор", "no", "-", "👎", "фу", "не надо", "не", "неа", "отмена", "не нужно"}
CONFIDENT = 0.7

_reply_classifier: Callable[[str, dict], dict | None] | None = None


def register_reply_classifier(fn: Callable[[str, dict], dict | None] | None) -> None:
    """Хук ИИ для сложных ответов: fn(text, context) → {kind, text, confidence} или None.
    По умолчанию не подключён: Gemini часто недоступен, платный Polza запрещён."""
    global _reply_classifier
    _reply_classifier = fn


def _bare(text: str) -> str:
    return re.sub(r"[\s!.…,)]+$", "", str(text or "").strip().lower())


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?…])\s+|\n+", str(text or "")) if s.strip()]


def _rule_text_of(sentence: str, kind: str) -> str:
    text = sentence.strip().strip("«»\"'")
    if kind == "do":
        text = _PRAISE_FILLER_RE.sub(" ", text)
        text = re.sub(r"^\W*(что|чтобы|когда)\s+", "", text.strip(" ,.—-!"), flags=re.IGNORECASE)
    else:
        text = re.sub(r"\b(пожалуйста|плиз|плз|пж|и переделай|переделай)\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" ,.—-!")
    return (text[:1].upper() + text[1:])[:300] if text else ""


def classify_reply(text: str) -> dict:
    """Эвристика без ИИ: что владелец сказал. intent: approve / reject (просто да/нет) /
    revoke («пересмотрел, правило больше не работает») / rule (do|dont) / none.
    confidence < CONFIDENT — переспросить карточкой."""
    raw = str(text or "").strip()
    bare = _bare(raw)
    if not bare:
        return {"intent": "none", "confidence": 0.0}
    if bare in APPROVE_WORDS:
        return {"intent": "approve", "confidence": 0.9}
    if bare in REJECT_WORDS:
        return {"intent": "reject", "confidence": 0.9}
    if _REVOKE_RE.search(raw):
        subject = _REVOKE_RE.sub(" ", raw)
        subject = re.sub(r"\b(правило|правила|про|что|я|контент|старое|все|всё|и)\b", " ", subject, flags=re.I)
        return {"intent": "revoke", "subject": re.sub(r"\s+", " ", subject).strip(" ,.—-!:«»"), "confidence": 0.8}
    # «не заходит» — оценка «не делать», а не смесь похвалы и запрета.
    marked = [(s, bool(_DONT_RE.search(s)), bool(_DO_RE.search(_NOT_LANDS_RE.sub(" ", s))))
              for s in _sentences(raw)]
    marked = [m for m in marked if m[1] or m[2]]
    if not marked:
        return {"intent": "none", "confidence": 0.0}
    sentence, dont, do = marked[0]
    kind = "dont" if dont else "do"
    signal = rule_signal(raw)
    if signal == "command":
        # «убери замирание в конце», «удали этот клип» — разовая правка, а не правило.
        return {"intent": "command", "kind": kind, "confidence": 0.0, "signal": signal}
    rule_text = _rule_text_of(sentence, kind)
    core = P.rule_tokens(rule_text)[0]
    confidence = 0.8 if kind == "dont" else 0.75
    if kind == "dont" and not core:
        confidence = 0.3
    if kind == "do" and len(core) < 2:
        # «круто, так и делай» без того, ЧТО именно, — это похвала, а не правило.
        return {"intent": "approve", "praise": True, "confidence": 0.6}
    if dont and do:
        confidence = min(confidence, 0.5)
    if len(marked) > 1:
        confidence = min(confidence, 0.5)
    if "?" in raw or len(raw) > 200:
        confidence = min(confidence, 0.5)
    one_off = bool(_ONE_OFF_RE.search(raw))
    if one_off:
        confidence = min(confidence, 0.5)
    return {"intent": "rule", "kind": kind, "text": rule_text or raw[:300], "confidence": round(confidence, 2),
            "one_off": one_off, "signal": signal}


def learn_from_text(store: Any, project_id: int, text: str, *, origin: str = "ui", origin_ref: str = "",
                    approval_id: int | None = None, said_at: Any = None, context: dict | None = None,
                    auto_rules: str = "auto", judge: Callable[[str], dict | None] | None = None) -> dict:
    """Слова владельца → правило проекта (уверены и auto_rules=auto), карточка «Записать
    правило?» (не уверены или auto_rules=confirm), предложение отменить старое правило.
    Правило всегда с дословной цитатой владельца. Разовые команды («убери …», «удали этот
    клип») правилом не считаются (``rule_signal``). ``judge`` — ИИ для спорных реплик
    (без признака обобщения и без команды): text → {rule: bool, kind, text} или None (эвристика)."""
    text = P._long_text(text, 2000)
    cls = classify_reply(text)
    said = said_at or _now_text()[:16]
    out: dict = {"classified": cls, "action": "none"}
    if cls["intent"] == "revoke":
        subject = cls.get("subject") or text
        pool = P.list_rules(store, project_id=project_id, status="active", include_global=False)
        scored = sorted(((max(P.rule_similarity(subject, r["text"]), P.rule_similarity(text, r["text"])), r)
                         for r in pool), key=lambda x: -x[0])
        scored = [(s, r) for s, r in scored if s >= 0.34]
        if not scored:
            out.update(action="revoke_not_found", note="Не нашёл похожего правила — назови его номер (#id).")
            return out
        rule = scored[0][1]
        card = propose_rule(store, project_id, "", action="revoke", rule_id=rule["id"], quote=text, origin=origin,
                            origin_ref=origin_ref, said_at=said)
        out.update(action="revoke_proposed", proposal_id=card["id"], rule_id=rule["id"],
                   candidates=[{"id": r["id"], "text": r["text"], "score": s} for s, r in scored[:3]])
        return out
    if cls["intent"] != "rule":
        return out
    kind, rule_text, confidence = cls["kind"], cls["text"], cls["confidence"]
    if judge is not None and cls.get("signal") == "neutral":
        try:
            verdict = judge(text)
        except Exception as exc:  # noqa: BLE001 - ИИ недоступен — решает эвристика
            _log.info("rule judge failed: %s", exc)
            verdict = None
        if isinstance(verdict, dict) and "rule" in verdict:
            out["ai"] = True
            if not verdict.get("rule"):
                out["classified"] = {**cls, "intent": "command", "confidence": 0.0}
                return out
            if verdict.get("kind") in P.RULE_KINDS:
                kind = verdict["kind"]
            if verdict.get("text"):
                rule_text = P._text(verdict["text"], 300)
    if confidence < CONFIDENT and _reply_classifier is not None:
        try:
            ai = _reply_classifier(text, context or {})
        except Exception as exc:  # noqa: BLE001 - ИИ недоступен — остаёмся на эвристике
            _log.info("reply classifier failed: %s", exc)
            ai = None
        if isinstance(ai, dict) and ai.get("text") and ai.get("kind") in P.RULE_KINDS:
            kind, rule_text = ai["kind"], P._text(ai["text"], 300)
            confidence = float(ai.get("confidence") or 0)
            out["ai"] = True
    if confidence >= CONFIDENT and auto_rules == "auto":
        res = P.add_rule(store, rule_text, kind=kind, project_id=project_id, quote=text, origin=origin,
                         origin_ref=origin_ref, said_at=said)
        fake = {"id": approval_id, "project_id": project_id, "payload": {"text": rule_text, "kind": kind,
                "quote": text, "origin": origin, "said_at": said}, "title": rule_text, "decision_comment": text}
        if res.get("created"):
            log_event(store, approval_id, project_id, "rule_added", origin, f"#{res['rule']['id']} {rule_text}",
                      {"rule_id": res["rule"]["id"], "quote": text})
            out.update(action="rule_added", rule=res["rule"])
        elif res.get("needs_confirmation"):
            card = _rule_outcome(store, fake, res, origin)
            out.update(action="conflict_proposed", proposal_id=card.get("proposal_id"), question=res.get("question"))
        else:
            out.update(action="duplicate", rule=res.get("duplicate_of"))
        return out
    card = propose_rule(store, project_id, rule_text, kind=kind, quote=text, origin=origin, origin_ref=origin_ref,
                        said_at=said, question=(f"Записать на будущее как правило ({'не делать' if kind == 'dont' else 'делать'}): "
                                                f"«{rule_text}»?" if cls.get("one_off") else ""))
    out.update(action="rule_proposed", proposal_id=card["id"], text=rule_text, kind=kind)
    return out


def describe_learning(result: dict | None) -> str:
    """Человеческая строка для ответа владельцу в Telegram/UI."""
    if not result:
        return ""
    action = result.get("action")
    if action == "rule_added":
        return f"Запомнил правило #{result['rule']['id']}: «{result['rule']['text']}»."
    if action == "duplicate" and result.get("rule"):
        return f"Такое правило уже есть: #{result['rule']['id']} «{result['rule']['text']}»."
    if action == "rule_proposed":
        return f"Записать правило «{result['text']}»? Карточка #{result['proposal_id']}."
    if action == "conflict_proposed":
        return f"{result.get('question') or 'Противоречит старому правилу.'} Карточка #{result.get('proposal_id')}."
    if action == "revoke_proposed":
        return f"Отменить правило #{result['rule_id']}? Карточка #{result['proposal_id']}."
    if action == "revoke_not_found":
        return result.get("note") or ""
    return ""


# --------------------------------------------------------------------------- #
# Расписание
# --------------------------------------------------------------------------- #

BOOKED_STATUSES = ("scheduled", "publishing", "published")


def _booked(store: Any, project_id: int, exclude: int | None = None) -> list[tuple[datetime, list[str]]]:
    rows = store.db.query_all(
        f"SELECT id, scheduled_at, platforms_json FROM approvals WHERE project_id = ? AND scheduled_at != '' "
        f"AND publish_status IN ({', '.join('?' for _ in BOOKED_STATUSES)})",
        (int(project_id), *BOOKED_STATUSES),
    )
    out = []
    for row in rows:
        if exclude and int(row["id"]) == int(exclude):
            continue
        moment = parse_utc(row["scheduled_at"])
        if moment:
            out.append((moment, _loads(row["platforms_json"], [])))
    return out


def slot_candidates(cfg: dict, after: datetime, days: int = SLOT_SEARCH_DAYS) -> list[datetime]:
    """Моменты слотов проекта после ``after`` (UTC) на ``days`` дней вперёд."""
    tz = tz_of(cfg["timezone"])
    local_start = after.astimezone(tz)
    out = set()
    for offset in range(days + 1):
        day = (local_start + timedelta(days=offset)).date()
        for slot in cfg["slots"]:
            if day.weekday() not in slot["days"]:
                continue
            for hhmm in slot["times"]:
                hh, mm = _time_of_day(hhmm)
                moment = datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz).astimezone(timezone.utc)
                if moment > after:
                    out.add(moment)
    return sorted(out)


def _fits(cfg: dict, moment: datetime, platforms: list[str], booked: list[tuple[datetime, list[str]]]) -> bool:
    tz = tz_of(cfg["timezone"])
    day = moment.astimezone(tz).date()
    for platform in platforms or ["*"]:
        limit = cfg["per_day"].get(platform, cfg["per_day"].get("default", 0))
        if not limit:
            continue
        same_day = sum(1 for when, plats in booked
                       if when.astimezone(tz).date() == day and (platform == "*" or platform in plats or not plats))
        if same_day >= limit:
            return False
    return True


def next_slot(store: Any, project_id: int, platforms: list[str], after: datetime | None = None,
              exclude: int | None = None) -> datetime:
    """Ближайший свободный момент: слот, не занятый другим клипом проекта, с лимитом в день
    на каждую площадку. Без слотов — сразу (если лимит дня позволяет), иначе завтра в 12:00."""
    cfg = get_config(store, project_id)
    start = after or now()
    booked = _booked(store, project_id, exclude)
    taken = {when.replace(second=0) for when, _ in booked}
    if cfg["slots"]:
        candidates = slot_candidates(cfg, start)
    else:
        tz = tz_of(cfg["timezone"])
        local = start.astimezone(tz)
        candidates = [start] + [
            datetime(d.year, d.month, d.day, 12, 0, tzinfo=tz).astimezone(timezone.utc)
            for d in ((local + timedelta(days=k)).date() for k in range(1, SLOT_SEARCH_DAYS + 1))
        ]
    for moment in candidates:
        if cfg["slots"] and moment.replace(second=0) in taken:
            continue
        if _fits(cfg, moment, platforms, booked):
            return moment
    raise ValueError(f"нет свободных слотов на {SLOT_SEARCH_DAYS} дней — добавь слоты или подними лимит в день")


def _accounts_for(store: Any, cfg: dict, platforms: list[str]) -> tuple[list[int], list[str]]:
    """Аккаунты под площадки: из настроек проекта, иначе единственный аккаунт площадки."""
    accounts = store.list_accounts()
    ids, problems = [], []
    for platform in platforms:
        chosen = [int(i) for i in cfg["accounts"].get(platform) or []]
        if not chosen:
            own = [a for a in accounts if a["platform"] == platform]
            if len(own) == 1:
                chosen = [int(own[0]["id"])]
            elif not own:
                problems.append(f"нет аккаунта {PLATFORM_LABELS.get(platform, platform)}")
            else:
                problems.append(f"у {PLATFORM_LABELS.get(platform, platform)} несколько аккаунтов — выбери в "
                                "настройках автопилота проекта")
        ids.extend(chosen)
    return list(dict.fromkeys(ids)), problems


def _description_with_tags(description: str, tags: list[str]) -> str:
    missing = [f"#{t}" for t in tags if f"#{t}".lower() not in description.lower()]
    return "\n\n".join(x for x in (description.strip(), " ".join(missing)) if x)


def _place(store: Any, approval_id: int, actor: str = "", at: datetime | None = None,
           use_proposed: bool = True) -> dict:
    """Одобренный клип → задание публикации на ближайший свободный слот (или на ``at``).
    Не вышло (нет аккаунта, нет слотов) — publish_status=failed с причиной, без исключения."""
    approval = get_approval(store, approval_id)
    if approval["status"] != "approved" or approval["kind"] != "clip_publish":
        raise ValueError("в расписание — только одобренная публикация клипа")
    project_id = int(approval["project_id"])
    cfg = get_config(store, project_id)
    platforms = approval["platforms"] or cfg["platforms"]
    try:
        if not platforms:
            raise ValueError("не выбраны площадки — укажи platforms или площадки по умолчанию в автопилоте")
        if not approval.get("clip_id"):
            raise ValueError("рендер клипа удалён — отрендери заново и отправь на одобрение")
        account_ids, problems = _accounts_for(store, cfg, platforms)
        if problems:
            raise ValueError("; ".join(problems))
        proposed = parse_utc(approval.get("proposed_at")) if use_proposed else None
        moment = at or (proposed if proposed and proposed > now() else None) or \
            next_slot(store, project_id, platforms, exclude=approval_id)
        job = store.create_clip_post_job(
            int(approval["clip_id"]), approval["title"], _description_with_tags(approval["description"],
                                                                                 approval["tags"]),
            account_ids, cfg["privacy"], True, scheduled_at=fmt_utc(moment),
        )
    except (KeyError, ValueError) as exc:
        _update(store, approval_id, publish_status="failed", publish_error=str(exc)[:500])
        log_event(store, approval_id, project_id, "schedule_failed", actor, str(exc)[:500])
        return {"ok": False, "error": str(exc)}
    if approval.get("job_id"):  # старое задание (перенос «в ближайший слот») больше не нужно
        _cancel_job(store, int(approval["job_id"]))
    _update(store, approval_id, publish_status="scheduled", publish_error="", scheduled_at=fmt_utc(moment),
            job_id=int(job["id"]), platforms_json=_json(platforms))
    local = to_local(moment, cfg["timezone"])
    log_event(store, approval_id, project_id, "scheduled", actor, f"{local} ({cfg['timezone']})",
              {"job_id": job["id"], "scheduled_at": fmt_utc(moment)})
    return {"ok": True, "job_id": job["id"], "scheduled_at": fmt_utc(moment), "scheduled_local": local}


def _cancel_job(store: Any, job_id: int) -> bool:
    cur = store.db.execute("UPDATE jobs SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP "
                           "WHERE id = ? AND status = 'queued'", (int(job_id),))
    return bool(cur.rowcount)


def reschedule(store: Any, approval_id: int, action: str = "move", at: Any = None, actor: str = "ui") -> dict:
    """Перенос (move: на ``at`` — время проекта), в ближайший слот (next_slot) или отмена (cancel)."""
    approval = get_approval(store, approval_id)
    project_id = int(approval["project_id"])
    cfg = get_config(store, project_id)
    action = P._choice(action, ("move", "next_slot", "cancel"), "action")
    job = store.get_job(int(approval["job_id"])) if approval.get("job_id") else None
    if job and job["status"] not in ("queued", "cancelled"):
        raise ValueError(f"публикация уже {job['status']} — переносить поздно")
    if action == "cancel":
        if job:
            _cancel_job(store, int(job["id"]))
        _update(store, approval_id, publish_status="cancelled")
        log_event(store, approval_id, project_id, "cancelled", actor, "публикация отменена")
        return {"ok": True, "approval": get_approval(store, approval_id)}
    if action == "next_slot":
        res = _place(store, approval_id, actor=actor, use_proposed=False)
        if not res.get("ok"):
            raise ValueError(res.get("error") or "не поставилось в слот")
        return {"ok": True, **res, "approval": get_approval(store, approval_id)}
    moment = parse_when(at, cfg["timezone"])
    if moment is None:
        raise ValueError("нужно время: ГГГГ-ММ-ДД ЧЧ:ММ")
    if moment <= now():
        raise ValueError("время уже прошло")
    if job and job["status"] == "queued":
        store.db.execute("UPDATE jobs SET scheduled_at = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (fmt_utc(moment), int(job["id"])))
        _update(store, approval_id, scheduled_at=fmt_utc(moment), publish_status="scheduled")
    else:
        res = _place(store, approval_id, actor=actor, at=moment)
        if not res.get("ok"):
            raise ValueError(res.get("error") or "не поставилось")
    log_event(store, approval_id, project_id, "rescheduled", actor, to_local(moment, cfg["timezone"]))
    return {"ok": True, "approval": get_approval(store, approval_id)}


def get_schedule(store: Any, project_id: int, free: int = 6) -> dict:
    """Ближайшие публикации проекта (местным временем) и свободные слоты."""
    cfg = get_config(store, project_id)
    sync_publish_status(store, project_id)
    rows = store.db.query_all(
        "SELECT a.*, p.name AS project_name FROM approvals a LEFT JOIN projects p ON p.id = a.project_id "
        "WHERE a.project_id = ? AND a.kind = 'clip_publish' AND a.status = 'approved' "
        "AND (a.publish_status IN ('scheduled', 'publishing', 'failed') "
        "OR (a.publish_status = 'published' AND a.scheduled_at >= ?)) ORDER BY a.scheduled_at, a.id",
        (int(project_id), fmt_utc(now() - timedelta(days=2))),
    )
    keys = ("id", "title", "platforms", "scheduled_at", "scheduled_local", "publish_status", "publish_error",
            "job_id", "clip_id", "clip_plan_id", "content_type", "decided_by", "media_url", "thumb_url")
    upcoming = [{k: a.get(k) for k in keys} for a in (_approval_from_row(store, r) for r in rows)]
    free_slots = []
    if cfg["slots"]:
        booked = _booked(store, project_id)
        taken = {when.replace(second=0) for when, _ in booked}
        for moment in slot_candidates(cfg, now(), days=14):
            if moment.replace(second=0) not in taken and _fits(cfg, moment, cfg["platforms"], booked):
                free_slots.append({"at": fmt_utc(moment), "local": to_local(moment, cfg["timezone"])})
            if len(free_slots) >= free:
                break
    return {"project_id": int(project_id), "timezone": cfg["timezone"], "slots": cfg["slots"],
            "per_day": cfg["per_day"], "upcoming": upcoming, "free_slots": free_slots,
            "note": ("Время в UTC — scheduled_at, местное (пояс проекта) — scheduled_local. "
                     "Публикует обычная очередь заданий по jobs.scheduled_at.")}


def sync_publish_status(store: Any, project_id: int | None = None) -> list[dict]:
    """Статус задания публикации → карточка (published / failed). Возвращает изменения."""
    sql = ("SELECT a.id, a.project_id, a.publish_status, a.title, j.status AS job_status, j.error AS job_error, "
           "j.result_json FROM approvals a JOIN jobs j ON j.id = a.job_id "
           "WHERE a.publish_status IN ('scheduled', 'publishing')")
    params: list = []
    if project_id is not None:
        sql += " AND a.project_id = ?"
        params.append(int(project_id))
    changed = []
    for row in store.db.query_all(sql, params):
        new = {"queued": "scheduled", "running": "publishing", "succeeded": "published",
               "cancelled": "cancelled"}.get(row["job_status"], "failed")
        if new == row["publish_status"]:
            continue
        error = "" if new != "failed" else (row["job_error"] or row["job_status"])
        _update(store, row["id"], publish_status=new, publish_error=str(error)[:500])
        urls = []
        if new == "published":
            urls = [t.get("remote_url") for t in _loads(row["result_json"], {}).get("targets", []) if t.get("remote_url")]
        if new in ("published", "failed"):
            log_event(store, row["id"], row["project_id"], new, "worker", error or " ".join(urls), {"urls": urls})
        changed.append({"id": row["id"], "project_id": row["project_id"], "status": new, "title": row["title"],
                        "error": error, "urls": urls})
    return changed


def expire_old(store: Any) -> int:
    """pending дольше expire_hours проекта → expired."""
    count = 0
    for row in store.db.query_all("SELECT id, project_id, created_at FROM approvals WHERE status = 'pending'"):
        if not row["project_id"]:
            continue
        hours = get_config(store, int(row["project_id"]))["expire_hours"]
        created = parse_utc(row["created_at"])
        if hours and created and now() - created >= timedelta(hours=hours):
            _update(store, row["id"], status="expired", decided_by="system", decided_at=_now_text(),
                    decision_comment=f"без ответа {hours} ч")
            log_event(store, row["id"], row["project_id"], "expired", "system", f"без ответа {hours} ч")
            count += 1
    return count


# --------------------------------------------------------------------------- #
# Петля статистики и недельная сводка
# --------------------------------------------------------------------------- #


def after_stats(store: Any) -> dict:
    """После сбора чисел: гипотезы всех проектов заново; подтверждённая без правила и
    правило, которое цифры перестали подтверждать, → карточки hypothesis_review."""
    summary = {"projects": 0, "cards": []}
    for project in P.list_projects(store):
        if not project.get("hypotheses_count"):
            continue
        summary["projects"] += 1
        try:
            res = P.evaluate_hypotheses(store, int(project["id"]))
        except Exception as exc:  # noqa: BLE001 - один проект не валит петлю
            _log.warning("evaluate_hypotheses(%s) failed: %s", project["id"], exc)
            continue
        for hyp in res["hypotheses"]:
            if hyp.get("suggest_rule"):
                full = P.get_hypothesis(store, hyp["id"])
                text = full.get("rule_text") or full["statement"]
                card = submit_card(
                    store, int(project["id"]), "hypothesis_review",
                    f"Гипотеза подтвердилась: «{full['statement']}» — {hyp['conclusion']}. Сделать правилом «{text}»?",
                    {"action": "promote", "hypothesis_id": hyp["id"], "text": text}, hypothesis_id=hyp["id"],
                    dedupe_key=f"hyp:{hyp['id']}:promote")
                if not card.get("deduplicated"):
                    summary["cards"].append(card["id"])
            for rule_id in hyp.get("rules_flagged") or []:
                rule = P.get_rule(store, rule_id)
                card = submit_card(
                    store, int(project["id"]), "hypothesis_review",
                    f"Правило #{rule_id} «{rule['text']}» больше не подтверждается цифрами: {rule['review_note']}. "
                    "Оставить правило?",
                    {"action": "review_rule", "rule_id": rule_id, "hypothesis_id": hyp["id"]}, rule_id=rule_id,
                    hypothesis_id=hyp["id"], dedupe_key=f"rule:{rule_id}:review")
                if not card.get("deduplicated"):
                    summary["cards"].append(card["id"])
    return summary


def weekly_digest(store: Any, project_id: int, days: int = 7) -> dict:
    """Сводка проекта за неделю: что вышло, что выстрелило, топ и антитоп, гипотезы, правила."""
    project = P.get_project(store, project_id, include_sources=False)
    since = now() - timedelta(days=days)
    since_naive = since.replace(tzinfo=None)
    items = P.project_items(store, project_id)

    def published_at(item: dict) -> datetime | None:
        times = [P._parse_time(p.get("published_at")) for p in item["publications"]]
        times = [t for t in times if t]
        return min(times) if times else None

    fresh = [i for i in items if (published_at(i) or datetime.min) >= since_naive]
    rated = [i for i in items if i.get("views_per_day") is not None]
    median = P._median([float(i["views_per_day"]) for i in rated])
    hits = [i for i in fresh if median and (i.get("views_per_day") or 0) >= 2 * median]
    recent = [i for i in rated if (published_at(i) or datetime.min) >= since_naive - timedelta(days=21)] or rated
    recent.sort(key=lambda i: -(i["views_per_day"] or 0))
    top = recent[:3]
    anti = [i for i in recent[::-1][:3] if i not in top]
    confirmed = [h for h in P.list_hypotheses(store, project_id) if h["status"] == "confirmed"]
    since_text = fmt_utc(since)
    rules_added = store.db.query_all("SELECT id, text FROM project_rules WHERE project_id = ? AND created_at >= ?",
                                     (int(project_id), since_text))
    rules_revoked = store.db.query_all("SELECT id, text FROM project_rules WHERE project_id = ? AND revoked_at >= ?",
                                       (int(project_id), since_text))
    pending = len(list_approvals(store, project_id, status="pending"))
    upcoming = [u for u in get_schedule(store, project_id)["upcoming"] if u["publish_status"] == "scheduled"]

    def line(item: dict) -> str:
        views = item.get("views")
        return (f"«{item['title'][:60]}» — {views if views is not None else '—'} просм., "
                f"{item.get('views_per_day') or 0:g}/день")

    parts = [f"📊 Неделя проекта «{project['name']}»"]
    parts.append(f"Вышло: {len(fresh)}" + (f" · выстрелило: {len(hits)}" if hits else ""))
    for item in hits[:3]:
        parts.append(f"🔥 {line(item)}")
    if top:
        parts.append("Топ:")
        parts += [f"  {n}. {line(i)}" for n, i in enumerate(top, 1)]
    if anti:
        parts.append("Антитоп:")
        parts += [f"  • {line(i)}" for i in anti]
    if len(rated) < 4:
        parts.append("Чисел мало — выводов не делаю.")
    if confirmed:
        parts.append("Подтвердились гипотезы:")
        parts += [f"  ✓ {h['statement']} — {h['conclusion']}" for h in confirmed[:5]]
    if rules_added or rules_revoked:
        parts.append(f"Правила: +{len(rules_added)} новых, −{len(rules_revoked)} отменено")
    parts.append(f"Ждут одобрения: {pending} · в расписании: {len(upcoming)}")
    return {"project_id": int(project_id), "text": "\n".join(parts), "published": len(fresh),
            "hits": [i["key"] for i in hits], "top": [i["key"] for i in top], "anti": [i["key"] for i in anti],
            "confirmed": [h["id"] for h in confirmed], "pending": pending, "upcoming": len(upcoming)}


def digest_due(store: Any, project_id: int) -> bool:
    cfg = get_config(store, project_id)
    if not cfg["digest"]["enabled"]:
        return False
    local = now().astimezone(tz_of(cfg["timezone"]))
    if local.weekday() != cfg["digest"]["weekday"] or local.hour < cfg["digest"]["hour"]:
        return False
    last = parse_utc(_state_row(store, project_id).get("digest_sent_at"))
    return last is None or now() - last >= timedelta(days=6)


def send_digest(store: Any, project_id: int, actor: str = "worker") -> dict:
    from app import notify_tg

    digest = weekly_digest(store, project_id)
    sent = notify_tg.send_text(store, digest["text"], project_id=project_id, purpose="digest")
    set_state(store, project_id, digest_sent_at=_now_text())
    log_event(store, None, project_id, "digest", actor, "сводка недели" + (" → Telegram" if sent else ""))
    return {**digest, "sent": bool(sent)}


# --------------------------------------------------------------------------- #
# Фон
# --------------------------------------------------------------------------- #


def tick(store: Any) -> dict:
    """Один проход фона: протухшие карточки, статусы публикаций, исходящие карточки, сводки."""
    from app import notify_tg

    out: dict = {"expired": expire_old(store), "changed": sync_publish_status(store)}
    if notify_tg.is_enabled(store):
        for change in out["changed"]:
            if change["status"] in ("published", "failed"):
                icon = "✅ Опубликовано" if change["status"] == "published" else "⚠️ Не опубликовано"
                tail = " ".join(change["urls"]) if change["urls"] else change["error"]
                notify_tg.send_text(store, f"{icon}: «{change['title']}» {tail}".strip(),
                                    project_id=change["project_id"], purpose="note")
        out["cards"] = notify_tg.send_queued_cards(store)
        for project in P.list_projects(store):
            if digest_due(store, int(project["id"])):
                try:
                    send_digest(store, int(project["id"]))
                except Exception as exc:  # noqa: BLE001
                    _log.warning("digest %s failed: %s", project["id"], exc)
    return out


class AutopilotWorker:
    """Фон автопилота, как JobWorker: проход ``tick`` и long-polling Telegram (если включён).
    ``AUTOPILOT_WORKER=0`` — не запускать."""

    IDLE_SECONDS = 20

    def __init__(self, store: Any) -> None:
        self.store = store
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        import os

        if os.getenv("AUTOPILOT_WORKER", "1").strip().lower() in ("0", "false", "no", "off"):
            return
        if self._thread and self._thread.is_alive() and not self._stop.is_set():
            return
        # Перезапуск после выключения модуля (app/modules.py): новый флаг остановки.
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(self._stop,), name="autopilot", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self, stop: threading.Event | None = None) -> None:
        from app import notify_tg

        stop = stop or self._stop
        if stop.wait(5):
            return
        while not stop.is_set():
            try:
                tick(self.store)
            except Exception as exc:  # noqa: BLE001 - фон не должен умирать
                _log.warning("autopilot tick failed: %s", exc)
            try:
                if notify_tg.is_enabled(self.store):
                    notify_tg.poll_once(self.store, timeout=notify_tg.POLL_TIMEOUT)
                    continue
            except Exception as exc:  # noqa: BLE001 - сеть/Telegram лёг — подождём
                _log.info("telegram poll failed: %s", exc)
            stop.wait(self.IDLE_SECONDS)
