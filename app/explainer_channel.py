"""Канал объяснялок: автопилот серии с маскотом (docs/playbooks/explainer-channel.md).

Замысел: владелец заводит канал («сова Ух объясняет …»), а дальше сервис сам держит очередь тем,
делает ролики заранее, к слоту расписания, и кладёт каждый готовый ролик владельцу карточкой
одобрения. Публикуется только одобренное — в свой слот (общая машинерия ``app/autopilot.py``).

Что здесь (поверх проекта-серии ``projects``, маскота ``app/explainer.py`` и конвейера
``app/explainer_gen.py``):

* ``expl_channels`` — режим канала у проекта: маскот (его голос, позы, цвета), ниша, включён ли
  автопилот, настройки (на сколько дней вперёд делать, сколько роликов параллельно, длина, тон…).
  Расписание (слоты, лимит в день, площадки, аккаунты, пояс) — это настройки автопилота проекта
  (``autopilot.update_config``): один источник правды для «Одобрения» и «Автопилота».
* ``expl_topics`` — очередь тем: proposed (предложил Gemini/агент — ждёт владельца) → approved (в
  очереди) → producing (ролик в конвейере) → review (готовый ролик в карточке одобрения) →
  scheduled → published; rejected (тему отклонили), failed (ролик не вышел — можно повторить),
  dropped (ролик отклонили или кончились попытки).
* Темы предлагает Gemini по нише и брифу канала, правилам владельца, цифрам (что зашло, что нет,
  подтверждённые гипотезы) и отклонённым темам — без дублей с уже сделанным (``topic_similarity``).
  Пачка новых тем — одна карточка «Взять все / Отклонить все»; по одной — в UI.
* Производство: свободные слоты на ``lead_days`` дней вперёд минус ролики в работе и на одобрении —
  столько роликов и запускаем (не больше ``max_parallel`` сразу). Ролик — обычный
  ``explainer_create`` с голосом маскота, ``render`` (рендер, обложки, заголовок) и привязкой
  клипа к проекту канала. Деньги — как у любой объяснялки: платны только новые позы (и озвучка
  Polza), одной карточкой этапа с потолками ``EXPLAINER_VIDEO_CAP_RUB`` и месячным.
* Обучение: «Переделать»/«В мусор» с комментарием у карточки ролика и отказ от темы с причиной →
  правила проекта (``autopilot.learn_from_text``) → они в промпте тем и сценария. «Переделать»
  ещё и пересобирает ролик по той же теме с замечанием владельца (до ``max_attempts`` попыток).

Фон — ``tick`` из прохода ``AutopilotWorker`` (каждые ~20 с; Gemini за темами — не чаще
``propose_every_hours``).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import timedelta
from difflib import SequenceMatcher
from typing import Any

from app import autopilot as A
from app import explainer as E
from app import projects as P

_log = logging.getLogger(__name__)

CHANNEL_SCHEMA_SQL = [
    """
    CREATE TABLE IF NOT EXISTS expl_channels (
        project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
        mascot_id INTEGER REFERENCES expl_mascots(id) ON DELETE SET NULL,
        enabled INTEGER NOT NULL DEFAULT 0,
        niche TEXT NOT NULL DEFAULT '',
        config_json TEXT NOT NULL DEFAULT '{}',
        last_proposed_at TEXT,
        last_tick_at TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS expl_topics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        topic TEXT NOT NULL,
        angle TEXT NOT NULL DEFAULT '',
        why TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'proposed',
        origin TEXT NOT NULL DEFAULT 'owner',
        priority INTEGER NOT NULL DEFAULT 0,
        batch TEXT NOT NULL DEFAULT '',
        card_id INTEGER,
        video_id INTEGER,
        approval_id INTEGER,
        attempts INTEGER NOT NULL DEFAULT 0,
        feedback TEXT NOT NULL DEFAULT '',
        reason TEXT NOT NULL DEFAULT '',
        decided_by TEXT NOT NULL DEFAULT '',
        decided_at TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_expl_topics ON expl_topics(project_id, status, id)",
]

TOPIC_STATUSES = ("proposed", "approved", "producing", "review", "scheduled", "published", "rejected", "failed",
                  "dropped")
TOPIC_STATUS_LABELS = {"proposed": "ждёт решения", "approved": "в очереди", "producing": "делается",
                       "review": "на одобрении", "scheduled": "в расписании", "published": "опубликовано",
                       "rejected": "отклонена", "failed": "не вышло", "dropped": "снята"}
OPEN_STATUSES = ("proposed", "approved", "producing", "review", "scheduled", "published", "failed")
DUP_STEMS = 0.6  # Жаккар основ слов — та же тема
DUP_SEQ = 0.82  # похожесть строк — та же тема другими словами
CARD_TOPICS = "expl_topics"
CARD_RETRY = "expl_retry"
CARD_NOTE = "expl_note"
REQUESTED_BY = "explainer_channel"
CONTENT_TYPE = "explainer"

DEFAULT_CONFIG: dict = {
    "lead_days": 2,  # готовые ролики — на столько дней слотов вперёд
    "max_parallel": 2,  # роликов в конвейере одновременно
    "backlog_min": 5,  # тем в очереди меньше — Gemini предлагает новые
    "propose_count": 6,  # сколько тем за раз
    "propose_every_hours": 12,  # не чаще
    "auto_approve_topics": False,  # темы Gemini — сразу в очередь (ролик всё равно на одобрение)
    "duration": 45,
    "tone": "дружелюбно",
    "max_attempts": 2,  # «Переделать» у ролика — пересборка по той же теме, не больше N раз
}

def _now():
    """Часы автопилота (тесты подменяют ``autopilot.now``)."""
    return A.now()


def ensure_channel_schema(conn) -> None:
    """Таблицы канала объяснялок (из ``Database.init`` после объяснялок)."""
    for sql in CHANNEL_SCHEMA_SQL:
        conn.execute(sql)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _now_text() -> str:
    return A.fmt_utc(_now())


# --------------------------------------------------------------------------- #
# Канал
# --------------------------------------------------------------------------- #


def normalize_config(raw: Any) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    cfg = dict(DEFAULT_CONFIG)

    def num(key: str, lo: float, hi: float, cast: Any = int) -> None:
        if raw.get(key) is None or raw.get(key) == "":
            return
        try:
            value = cast(raw[key])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key}: число") from exc
        if not lo <= value <= hi:
            raise ValueError(f"{key}: от {lo:g} до {hi:g}")
        cfg[key] = value

    num("lead_days", 1, 14)
    num("max_parallel", 1, 5)
    num("backlog_min", 0, 50)
    num("propose_count", 1, 15)
    num("propose_every_hours", 1, 24 * 7, float)
    num("duration", E.DURATION_MIN, E.DURATION_MAX, float)
    num("max_attempts", 1, 5)
    if raw.get("auto_approve_topics") is not None:
        cfg["auto_approve_topics"] = bool(raw["auto_approve_topics"])
    if raw.get("tone") is not None:
        cfg["tone"] = P._text(raw["tone"], 60) or DEFAULT_CONFIG["tone"]
    return cfg


def _row(store: Any, project_id: int) -> dict | None:
    row = store.db.query_one("SELECT * FROM expl_channels WHERE project_id = ?", (int(project_id),))
    return dict(row) if row else None


def get_channel(store: Any, project_id: int) -> dict:
    row = _row(store, project_id)
    if row is None:
        raise KeyError(f"проект #{project_id} — не канал объяснялок (explainer_channel_setup)")
    row["config"] = normalize_config(P._loads(row.pop("config_json", "{}"), {}))
    row["enabled"] = bool(row["enabled"])
    return row


def list_channels(store: Any, enabled_only: bool = False) -> list[dict]:
    sql = "SELECT project_id FROM expl_channels" + (" WHERE enabled = 1" if enabled_only else "") + " ORDER BY project_id"
    return [get_channel(store, int(r["project_id"])) for r in store.db.query_all(sql)]


def _schedule_patch(schedule: dict) -> dict:
    """Расписание канала → настройки автопилота: «N в день в ЧЧ:ММ по дням», площадки, аккаунты."""
    if not isinstance(schedule, dict):
        raise ValueError("schedule — {times: [\"19:00\"], days: [0..6], per_day, timezone, platforms, accounts}")
    patch: dict = {}
    if schedule.get("times") is not None or schedule.get("days") is not None:
        times = schedule.get("times") or []
        if isinstance(times, str):
            times = [t for t in re.split(r"[\s,;]+", times) if t]
        days = schedule.get("days")
        days = list(range(7)) if days is None else [int(d) for d in days]
        patch["slots"] = [{"days": days, "times": times}] if times and days else []
    if schedule.get("per_day") is not None:
        patch["per_day"] = {"default": max(0, int(schedule["per_day"] or 0))}
    for key in ("timezone", "platforms", "accounts"):
        if schedule.get(key) is not None:
            patch[key] = schedule[key]
    return patch


def setup_channel(store: Any, project_id: int | None = None, mascot_id: int | None = None, name: str = "",
                  niche: Any = None, enabled: Any = None, config: dict | None = None,
                  schedule: dict | None = None, description: Any = None) -> dict:
    """Включить режим канала у проекта (или поправить). Нет ``project_id`` — канал на проекте маскота
    (или новый проект «канал объяснялок» с именем ``name``). Расписание — в автопилот проекта."""
    current = _row(store, project_id) if project_id else None
    if current is None:
        if not mascot_id:
            raise ValueError("нужен mascot_id — маскот канала (explainer_overview)")
        mascot = E.get_mascot(store, int(mascot_id))
        if project_id is None:
            if P._text(name):
                project_id = int(P.create_project(store, name, kind="explainer",
                                                  description=description or "")["id"])
            else:
                project_id = int(mascot["project_id"])
        project = P.get_project(store, int(project_id), include_sources=False)
        if project["kind"] == "other":
            P.update_project(store, int(project_id), kind="explainer")
        elif project["kind"] not in ("explainer", "ai_video"):
            raise ValueError(f"проект «{project['name']}» — {project['kind_label']}; канал объяснялок — новый проект "
                             "(name) или проект вида «ИИ-видео»/«канал объяснялок»")
        store.db.execute("INSERT INTO expl_channels (project_id, mascot_id) VALUES (?, ?)",
                         (int(project_id), int(mascot["id"])))
        A.log_event(store, None, int(project_id), "config", "ui", f"канал объяснялок: маскот {mascot['name']}")
    pid = int(project_id)
    sets: dict[str, Any] = {}
    if mascot_id and current is not None:
        sets["mascot_id"] = int(E.get_mascot(store, int(mascot_id))["id"])
    if niche is not None:
        sets["niche"] = P._long_text(niche, 600)
    if enabled is not None:
        sets["enabled"] = 1 if enabled else 0
    if config is not None:
        merged = {**get_channel(store, pid)["config"], **(config if isinstance(config, dict) else {})}
        sets["config_json"] = _json(normalize_config(merged))
    if sets:
        cols = ", ".join(f"{k} = ?" for k in sets)
        store.db.execute(f"UPDATE expl_channels SET {cols}, updated_at = CURRENT_TIMESTAMP WHERE project_id = ?",
                         (*sets.values(), pid))
    if description is not None and current is not None:
        P.update_project(store, pid, description=description)
    if schedule is not None:
        A.update_config(store, pid, _schedule_patch(schedule))
    return channel_view(store, pid)


def mascot_choices(store: Any) -> list[dict]:
    """Маскоты всех проектов — кого можно поставить ведущим канала."""
    names = {p["id"]: p["name"] for p in P.list_projects(store)}
    out = []
    for m in E.list_mascots(store):
        out.append({"id": m["id"], "name": m["name"], "project_id": m["project_id"],
                    "project_name": names.get(m["project_id"], ""), "has_sheet": bool(m.get("sheet")),
                    "sheet_url": (m.get("sheet") or {}).get("url")})
    return out


def channel_mascot(store: Any, channel: dict) -> dict:
    if not channel.get("mascot_id"):
        raise E.ExplError("у канала нет маскота — explainer_channel_setup(mascot_id)")
    return E.get_mascot(store, int(channel["mascot_id"]))


# --------------------------------------------------------------------------- #
# Темы
# --------------------------------------------------------------------------- #


def _norm(text: Any) -> str:
    return " ".join(re.findall(r"[a-zа-я0-9]+", str(text or "").lower().replace("ё", "е")))


def topic_similarity(a: Any, b: Any) -> tuple[float, float]:
    """(Жаккар основ слов, похожесть строк) двух тем."""
    sa, sb = E.stems(a), E.stems(b)
    jac = len(sa & sb) / len(sa | sb) if sa and sb else 0.0
    seq = SequenceMatcher(None, _norm(a), _norm(b)).ratio() if _norm(a) and _norm(b) else 0.0
    return round(jac, 3), round(seq, 3)


def is_duplicate(a: Any, b: Any) -> bool:
    """Та же тема: общие основы слов (≥ DUP_STEMS) или почти та же строка (≥ DUP_SEQ)."""
    jac, seq = topic_similarity(a, b)
    return jac >= DUP_STEMS or seq >= DUP_SEQ


def _topic_view(row: Any) -> dict:
    data = dict(row)
    data["status_label"] = TOPIC_STATUS_LABELS.get(data["status"], data["status"])
    return data


def get_topic(store: Any, topic_id: int) -> dict:
    row = store.db.query_one("SELECT * FROM expl_topics WHERE id = ?", (int(topic_id),))
    if not row:
        raise KeyError(f"нет темы {topic_id}")
    return _topic_view(row)


def list_topics(store: Any, project_id: int, status: str | None = None) -> list[dict]:
    sql, params = "SELECT * FROM expl_topics WHERE project_id = ?", [int(project_id)]
    if status and status != "all":
        statuses = [s.strip() for s in str(status).split(",") if s.strip()]
        for s in statuses:
            P._choice(s, TOPIC_STATUSES, "status")
        sql += f" AND status IN ({', '.join('?' for _ in statuses)})"
        params += statuses
    sql += (" ORDER BY CASE status WHEN 'proposed' THEN 0 WHEN 'approved' THEN 1 WHEN 'producing' THEN 2 "
            "WHEN 'review' THEN 3 WHEN 'failed' THEN 4 WHEN 'scheduled' THEN 5 ELSE 6 END, priority DESC, id")
    return [_topic_view(r) for r in store.db.query_all(sql, params)]


def done_topics(store: Any, project_id: int, channel: dict | None = None) -> list[str]:
    """Что уже было: темы канала (кроме отклонённых) и темы роликов маскота."""
    out = [r["topic"] for r in store.db.query_all(
        "SELECT topic FROM expl_topics WHERE project_id = ? AND status != 'rejected' ORDER BY id", (int(project_id),))]
    channel = channel or _row(store, project_id)
    if channel and channel.get("mascot_id"):
        out += [r["topic"] for r in store.db.query_all(
            "SELECT topic FROM expl_videos WHERE mascot_id = ? AND status != 'cancelled' ORDER BY id",
            (int(channel["mascot_id"]),))]
    seen: list[str] = []
    for topic in out:
        if topic and topic not in seen:
            seen.append(topic)
    return seen


def find_duplicate(store: Any, project_id: int, text: str, extra: list[str] | None = None) -> str | None:
    """Похожая тема среди сделанных/очереди канала (отклонённые — тоже: их не предлагаем снова)."""
    pool = [r["topic"] for r in store.db.query_all("SELECT topic FROM expl_topics WHERE project_id = ?",
                                                    (int(project_id),))]
    pool += done_topics(store, project_id) + list(extra or [])
    return next((t for t in pool if is_duplicate(text, t)), None)


def add_topics(store: Any, project_id: int, topics: Any, origin: str = "owner", status: str | None = None,
               batch: str = "", actor: str = "ui") -> dict:
    """Темы в очередь: от владельца — сразу approved, от агента/Gemini — proposed (ждут владельца).
    Дубли сделанного и очереди не добавляются (``duplicates``)."""
    get_channel(store, project_id)
    if isinstance(topics, str):
        topics = [t for t in topics.splitlines() if t.strip()]
    status = status or ("approved" if origin == "owner" else "proposed")
    P._choice(status, TOPIC_STATUSES, "status")
    added, duplicates, batch_seen = [], [], []
    for raw in topics or []:
        item = raw if isinstance(raw, dict) else {"topic": raw}
        text = P._text(item.get("topic"), 300)
        if len(text) < 3:
            continue
        dup = find_duplicate(store, project_id, text, batch_seen)
        if dup:
            duplicates.append({"topic": text, "like": dup})
            continue
        batch_seen.append(text)
        cur = store.db.execute(
            "INSERT INTO expl_topics (project_id, topic, angle, why, status, origin, priority, batch, decided_by, "
            "decided_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (int(project_id), text, P._text(item.get("angle"), 300), P._text(item.get("why"), 300), status,
             P._text(origin, 20), int(item.get("priority") or 0), batch,
             actor if status == "approved" else "", _now_text() if status == "approved" else None))
        added.append(get_topic(store, int(cur.lastrowid)))
    if added:
        A.log_event(store, None, int(project_id), "topics_added", actor,
                    f"тем: {len(added)} ({origin}, {TOPIC_STATUS_LABELS[status]})")
    return {"added": added, "duplicates": duplicates}


def _set_topic(store: Any, topic_id: int, **fields: Any) -> None:
    cols = ", ".join(f"{k} = ?" for k in fields)
    store.db.execute(f"UPDATE expl_topics SET {cols}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (*fields.values(), int(topic_id)))


def decide_topic(store: Any, topic_id: int, action: str, comment: str = "", actor: str = "ui") -> dict:
    """approve — в очередь; reject — отказ (причина — урок проекту); retry — ролик не вышел, ещё раз."""
    action = P._choice(action, ("approve", "reject", "retry"), "action")
    topic = get_topic(store, topic_id)
    comment = P._long_text(comment, 1000)
    allowed = {"approve": ("proposed", "rejected"), "reject": ("proposed", "approved", "failed"),
               "retry": ("failed", "dropped")}[action]
    if topic["status"] not in allowed:
        raise ValueError(f"тема #{topic_id} — {topic['status_label']}: «{action}» нельзя")
    if action == "reject":
        _set_topic(store, topic_id, status="rejected", reason=comment, decided_by=P._text(actor, 80),
                   decided_at=_now_text())
    else:
        _set_topic(store, topic_id, status="approved", reason="" if action == "approve" else topic["reason"],
                   decided_by=P._text(actor, 80), decided_at=_now_text(),
                   **({"attempts": 0} if action == "retry" else {}))
    A.log_event(store, None, int(topic["project_id"]), f"topic_{action}", actor, f"«{topic['topic']}» {comment}".strip())
    out: dict = {"ok": True, "topic": get_topic(store, topic_id)}
    if comment and action == "reject":
        out["learned"] = A.learn_from_text(store, int(topic["project_id"]), comment, origin=A._origin(actor),
                                           origin_ref=f"topic#{topic_id}", context={"title": topic["topic"]})
    _close_batch_card(store, topic)
    return out


def update_topic(store: Any, topic_id: int, topic: Any = None, angle: Any = None, priority: Any = None) -> dict:
    current = get_topic(store, topic_id)
    sets: dict[str, Any] = {}
    if topic is not None:
        text = P._text(topic, 300)
        if len(text) < 3:
            raise ValueError("тема — хотя бы пара слов")
        sets["topic"] = text
    if angle is not None:
        sets["angle"] = P._text(angle, 300)
    if priority is not None:
        sets["priority"] = int(priority or 0)
    if sets:
        if current["status"] in ("producing", "review", "scheduled", "published") and "topic" in sets:
            raise ValueError("ролик по теме уже делается — тему не меняем")
        _set_topic(store, topic_id, **sets)
    return get_topic(store, topic_id)


def delete_topic(store: Any, topic_id: int) -> dict:
    topic = get_topic(store, topic_id)
    if topic["status"] in ("producing", "review", "scheduled"):
        raise ValueError("ролик по теме в работе — отмени ролик или отклони карточку")
    store.db.execute("DELETE FROM expl_topics WHERE id = ?", (int(topic_id),))
    _close_batch_card(store, topic)
    return {"ok": True, "deleted": int(topic_id)}


def _close_batch_card(store: Any, topic: dict) -> None:
    """Все темы пачки разобраны по одной — карточка «Взять все?» больше не нужна."""
    if not topic.get("card_id"):
        return
    left = store.db.query_one("SELECT COUNT(*) AS n FROM expl_topics WHERE card_id = ? AND status = 'proposed'",
                              (int(topic["card_id"]),))
    if left and int(left["n"]) == 0:
        store.db.execute("UPDATE approvals SET status = 'expired', decided_by = 'system', decided_at = ?, "
                         "decision_comment = 'темы разобраны по одной', updated_at = CURRENT_TIMESTAMP "
                         "WHERE id = ? AND status = 'pending'", (_now_text(), int(topic["card_id"])))


# --- Gemini предлагает темы -------------------------------------------------- #

TOPICS_RULES = """\
Ты продюсер канала коротких роликов-объяснялок (Shorts/TikTok/Reels, 30–60 с): маскот из context
простыми словами объясняет одну тему. Предложи count НОВЫХ тем для канала ПО-РУССКИ.
- Держись ниши канала (niche, channel_about) и характера маскота.
- Тема — одна конкретная мысль, которую можно объяснить за 45 секунд: «почему мы зеваем», «3 ошибки
  сна», «зачем коту усы». Не общие («здоровье»), не спорные, без медицинских и денежных обещаний.
- НЕ повторяй уже сделанные и отклонённые темы (done_topics, rejected) — ни дословно, ни другими словами.
- Соблюдай правила владельца (owner_rules: dont — нельзя, do — нужно) и табу (taboo).
- Опирайся на цифры: что зашло (top) — похожие форматы и крючки; что не зашло (bottom) — избегай;
  подтверждённые гипотезы (hypotheses) — следуй им.
angle — подход/крючок (одна фраза), why — почему зайдёт (одна фраза). Верни ТОЛЬКО JSON по схеме.
"""


def topics_schema() -> dict:
    return {"type": "object", "properties": {"topics": {"type": "array", "items": {"type": "object", "properties": {
        "topic": {"type": "string"}, "angle": {"type": "string"}, "why": {"type": "string"}},
        "required": ["topic"]}}}, "required": ["topics"]}


def topics_context(store: Any, project_id: int, count: int) -> dict:
    channel = get_channel(store, project_id)
    project = P.get_project(store, project_id, include_sources=False)
    mascot = channel_mascot(store, channel)
    rules = [{"kind": r["kind"], "text": r["text"]} for r in P.list_rules(store, project_id=project_id)]
    if int(mascot["project_id"]) != int(project_id):
        rules += [{"kind": r["kind"], "text": r["text"]}
                  for r in P.list_rules(store, project_id=int(mascot["project_id"]), include_global=False)]
    brief = P.get_brief(store, project_id)["brief"]
    taboo = [item.get("text") or item.get("name") or "" for item in brief.get("taboo") or []]
    stats = P.project_stats(store, project_id, top=5)
    perf = lambda items: [{"title": i["title"], "views_per_day": i.get("views_per_day"),  # noqa: E731
                           "views": i.get("views")} for i in items]
    hypotheses = [h["statement"] for h in P.list_hypotheses(store, project_id) if h["status"] == "confirmed"]
    rejected = [{"topic": r["topic"], "reason": r["reason"]} for r in store.db.query_all(
        "SELECT topic, reason FROM expl_topics WHERE project_id = ? AND status IN ('rejected', 'dropped') "
        "ORDER BY id DESC LIMIT 40", (int(project_id),))]
    return {"channel": project["name"], "channel_about": project.get("description") or "",
            "niche": channel["niche"], "brief_summary": brief.get("summary") or "",
            "mascot": {"name": mascot["name"], "persona": mascot["persona"]},
            "count": int(count), "owner_rules": rules, "taboo": [t for t in taboo if t],
            "done_topics": done_topics(store, project_id, channel)[-120:], "rejected": rejected,
            "top": perf(stats["best"]), "bottom": perf(stats["worst"]), "hypotheses": hypotheses}


def propose_topics(store: Any, project_id: int, count: int | None = None, client: Any = None,
                   actor: str = "system") -> dict:
    """Gemini предлагает темы → дедуп → очередь (proposed) + одна карточка владельцу на пачку
    (или сразу approved, если ``auto_approve_topics``)."""
    from app import explainer_gen as X

    channel = get_channel(store, project_id)
    cfg = channel["config"]
    count = max(1, min(int(count or cfg["propose_count"]), 15))
    client = client or X.gemini_client()
    if client is None:
        raise E.ExplError("Gemini не настроен (GEMINI_API_KEY) — темы добавь сам (explainer_channel_add_topics)")
    data = X._ask(client, TOPICS_RULES, topics_context(store, project_id, count), topics_schema(), 0.95)
    raw = [t for t in data.get("topics") or [] if isinstance(t, dict)]
    batch = f"g{_now().strftime('%Y%m%d%H%M%S')}"
    # Темы — такой же список: «Останавливаться на списке клипов» сильнее «брать темы сразу».
    from app.agent_settings import get_settings

    stop = get_settings(store, int(project_id))["stop_at_clip_list"]
    status = "approved" if cfg["auto_approve_topics"] and not stop else "proposed"
    res = add_topics(store, project_id, raw[: count * 2], origin="gemini", status=status, batch=batch, actor=actor)
    added = res["added"][:count]
    for extra in res["added"][count:]:  # Gemini дал больше, чем просили — лишнее не держим
        store.db.execute("DELETE FROM expl_topics WHERE id = ?", (int(extra["id"]),))
    store.db.execute("UPDATE expl_channels SET last_proposed_at = ? WHERE project_id = ?",
                     (_now_text(), int(project_id)))
    out = {"ok": True, "proposed": added, "duplicates": res["duplicates"], "status": status}
    if added and status == "proposed":
        lines = "\n".join(f"{n}. {t['topic']}" + (f" — {t['angle']}" if t["angle"] else "")
                          for n, t in enumerate(added, 1))
        card = A.submit_card(store, int(project_id), "other",
                             f"Новые темы для канала ({len(added)}):\n{lines}\n\nВзять все в очередь? По одной — в "
                             "сервисе (Серии → канал → Темы). Ответь текстом, чего не брать — запомню.",
                             {"action": CARD_TOPICS, "topic_ids": [t["id"] for t in added], "batch": batch},
                             title=f"Новые темы: {len(added)}", dedupe_key=f"{CARD_TOPICS}:{project_id}:{batch}",
                             submitted_by=REQUESTED_BY)
        store.db.execute(f"UPDATE expl_topics SET card_id = ? WHERE id IN ({', '.join('?' for _ in added)})",
                         (int(card["id"]), *[t["id"] for t in added]))
        out["card_id"] = card["id"]
    return out


def _on_topics_card(store: Any, approval: dict, action: str, comment: str, actor: str) -> dict:
    ids = [int(i) for i in (approval.get("payload") or {}).get("topic_ids") or []]
    changed = 0
    for tid in ids:
        try:
            topic = get_topic(store, tid)
        except KeyError:
            continue
        if topic["status"] != "proposed":
            continue
        if action in ("approve", "both"):
            _set_topic(store, tid, status="approved", decided_by=P._text(actor, 80), decided_at=_now_text())
        else:
            _set_topic(store, tid, status="rejected", reason=comment, decided_by=P._text(actor, 80),
                       decided_at=_now_text())
        changed += 1
    out: dict = {"topics": changed}
    if comment and approval.get("project_id"):
        out["learned"] = A.learn_from_text(store, int(approval["project_id"]), comment, origin=A._origin(actor),
                                           origin_ref=f"approval#{approval['id']}",
                                           context={"title": approval.get("title") or ""})
    return out


def _on_retry_card(store: Any, approval: dict, action: str, comment: str, actor: str) -> dict:
    tid = (approval.get("payload") or {}).get("topic_id")
    if not tid:
        return {}
    try:
        topic = get_topic(store, int(tid))
    except KeyError:
        return {}
    if topic["status"] not in ("failed", "dropped"):
        return {"topic": topic["status"]}
    if action in ("approve", "both"):
        _set_topic(store, int(tid), status="approved", attempts=0, feedback=comment or topic["feedback"],
                   decided_by=P._text(actor, 80), decided_at=_now_text())
    else:
        _set_topic(store, int(tid), status="dropped", reason=comment or topic["reason"])
    return {"topic": get_topic(store, int(tid))["status"]}


A.register_card_action(CARD_TOPICS, [("approve", "✅ Взять все"), ("reject", "🗑 Отклонить все")], _on_topics_card)
A.register_card_action(CARD_RETRY, [("approve", "🔁 Сделать заново"), ("reject", "🗑 Снять тему")], _on_retry_card)
A.register_card_action(CARD_NOTE, [("approve", "👌 Понял")], lambda *a, **k: {})


# --------------------------------------------------------------------------- #
# Производство
# --------------------------------------------------------------------------- #


def capacity(store: Any, project_id: int, channel: dict | None = None) -> dict:
    """Сколько роликов запускать: свободные слоты на lead_days вперёд минус ролики в работе и на
    одобрении. Без слотов — ничего (канал без расписания не производит)."""
    channel = channel or get_channel(store, project_id)
    cfg = A.get_config(store, project_id)
    start = _now()
    horizon = start + timedelta(days=int(channel["config"]["lead_days"]))
    booked = A._booked(store, project_id)
    taken = {when.replace(second=0) for when, _ in booked}
    platforms = cfg["platforms"]
    free = 0
    if cfg["slots"]:
        sim = list(booked)
        for moment in A.slot_candidates(cfg, start, days=int(channel["config"]["lead_days"]) + 1):
            if moment > horizon:
                break
            if moment.replace(second=0) in taken or not A._fits(cfg, moment, platforms, sim):
                continue
            free += 1
            sim.append((moment, platforms))
    counts = {r["status"]: int(r["n"]) for r in store.db.query_all(
        "SELECT status, COUNT(*) AS n FROM expl_topics WHERE project_id = ? GROUP BY status", (int(project_id),))}
    in_flight = counts.get("producing", 0)
    in_review = counts.get("review", 0)
    need = max(0, free - in_flight - in_review)
    start_now = max(0, min(need, int(channel["config"]["max_parallel"]) - in_flight))
    return {"free_slots": free, "in_flight": in_flight, "in_review": in_review, "need": need, "start": start_now,
            "horizon_days": int(channel["config"]["lead_days"]), "has_slots": bool(cfg["slots"])}


def start_video(store: Any, topic_id: int, client: Any = None, run: bool = False, actor: str = REQUESTED_BY) -> dict:
    """Ролик по теме: explainer_create голосом маскота канала, с рендером и привязкой к каналу."""
    from app import explainer_gen as X

    topic = get_topic(store, topic_id)
    if topic["status"] != "approved":
        raise ValueError(f"тема #{topic_id} — {topic['status_label']}: в работу берём только одобренные")
    channel = get_channel(store, int(topic["project_id"]))
    mascot = channel_mascot(store, channel)
    cfg = channel["config"]
    options = {"render": True, "cover": True, "channel_project_id": int(topic["project_id"]),
               "channel_topic_id": int(topic_id)}
    for key, value in (("niche", channel["niche"]), ("angle", topic["angle"]), ("owner_feedback", topic["feedback"])):
        if value:
            options[key] = value
    # Статус — до запуска: конвейер с run=True идёт синхронно, второй проход не должен взять тему ещё раз.
    _set_topic(store, topic_id, status="producing", attempts=int(topic["attempts"]) + 1, reason="")
    try:
        video = X.create(store, int(mascot["id"]), topic["topic"], cfg["duration"], cfg["tone"], options=options,
                         requested_by=actor, run=run, client=client)
    except Exception as exc:  # noqa: BLE001 - нет листа маскота и т.п.: тема ждёт правки
        _set_topic(store, topic_id, status="failed", reason=str(exc)[:500])
        A.log_event(store, None, int(topic["project_id"]), "video_failed", actor, f"«{topic['topic']}»: {exc}"[:500])
        raise
    _set_topic(store, topic_id, video_id=int(video["id"]), approval_id=None)
    A.log_event(store, None, int(topic["project_id"]), "video_started", actor,
                f"«{topic['topic']}» → ролик #{video['id']}", {"topic_id": int(topic_id), "video_id": video["id"]})
    return video


def _fail(store: Any, topic: dict, reason: str, ask: bool = True) -> dict:
    _set_topic(store, topic["id"], status="failed", reason=reason[:500])
    A.log_event(store, None, int(topic["project_id"]), "video_failed", REQUESTED_BY,
                f"«{topic['topic']}»: {reason}"[:500], {"topic_id": topic["id"], "video_id": topic.get("video_id")})
    if ask:
        A.submit_card(store, int(topic["project_id"]), "other",
                      f"Ролик по теме «{topic['topic']}» не вышел: {reason[:300]}. Сделать заново?",
                      {"action": CARD_RETRY, "topic_id": topic["id"]}, title=f"Не вышло: {topic['topic'][:80]}",
                      dedupe_key=f"{CARD_RETRY}:{topic['id']}:{topic['attempts']}", submitted_by=REQUESTED_BY)
    return {"topic_id": topic["id"], "status": "failed", "reason": reason}


def _submit(store: Any, topic: dict, video: dict) -> dict:
    plan_id = int(video["clip_id"])
    if P.project_of_plan(store, plan_id) is None:
        P.assign_plan_project(store, plan_id, int(topic["project_id"]))
    try:
        card = A.submit_clip(store, plan_id, content_type=CONTENT_TYPE, submitted_by=REQUESTED_BY)
    except ValueError as exc:
        notes = "; ".join(str(n) for n in (video.get("notes") or [])[-2:])
        return _fail(store, topic, f"{exc}" + (f" ({notes})" if notes else ""))
    status = "review" if card["status"] == "pending" else "scheduled"
    _set_topic(store, topic["id"], status=status, approval_id=int(card["id"]))
    A.log_event(store, card["id"], int(topic["project_id"]), "video_ready", REQUESTED_BY,
                f"«{topic['topic']}» → карточка #{card['id']}", {"topic_id": topic["id"], "video_id": video["id"]})
    return {"topic_id": topic["id"], "status": status, "approval_id": card["id"]}


def sync(store: Any, project_id: int) -> list[dict]:
    """Темы в работе → дальше: готовый ролик — на одобрение; решение владельца — в тему."""
    channel = get_channel(store, project_id)
    changes = []
    for topic in list_topics(store, project_id, "producing,review,scheduled"):
        if topic["status"] == "producing":
            if not topic.get("video_id"):
                continue
            try:
                video = E.get_video(store, int(topic["video_id"]))
            except KeyError:
                changes.append(_fail(store, topic, "ролик удалён"))
                continue
            if video["status"] == "done":
                changes.append(_submit(store, topic, video))
            elif video["status"] == "failed":
                changes.append(_fail(store, topic, video.get("error") or "ошибка конвейера"))
            elif video["status"] == "cancelled":
                changes.append(_fail(store, topic, video.get("error") or "ролик отменён", ask=False))
            continue
        if not topic.get("approval_id"):
            continue
        try:
            card = A.get_approval(store, int(topic["approval_id"]))
        except KeyError:
            continue
        if topic["status"] == "review":
            if card["status"] == "approved":
                _set_topic(store, topic["id"], status="scheduled")
                changes.append({"topic_id": topic["id"], "status": "scheduled"})
            elif card["status"] == "rejected":
                _set_topic(store, topic["id"], status="dropped", reason=card["decision_comment"] or "ролик в мусор")
                changes.append({"topic_id": topic["id"], "status": "dropped"})
            elif card["status"] == "changes_requested":
                comment = card["decision_comment"] or ""
                if int(topic["attempts"]) >= int(channel["config"]["max_attempts"]):
                    _set_topic(store, topic["id"], status="dropped", feedback=comment,
                               reason=f"попыток {topic['attempts']} — снято, замечание: {comment}"[:500])
                    changes.append({"topic_id": topic["id"], "status": "dropped"})
                else:  # ещё раз по той же теме — с замечанием владельца в промпте сценария
                    _set_topic(store, topic["id"], status="approved", feedback=comment, priority=100,
                               approval_id=None)
                    changes.append({"topic_id": topic["id"], "status": "approved", "redo": True})
            elif card["status"] == "expired":
                _set_topic(store, topic["id"], status="dropped", reason="карточка ролика просрочена")
                changes.append({"topic_id": topic["id"], "status": "dropped"})
        elif topic["status"] == "scheduled" and card.get("publish_status") == "published":
            _set_topic(store, topic["id"], status="published")
            changes.append({"topic_id": topic["id"], "status": "published"})
    return changes


def warnings(store: Any, project_id: int, channel: dict | None = None) -> list[str]:
    from app import modules
    from app.settings import settings

    channel = channel or get_channel(store, project_id)
    out = []
    cfg = A.get_config(store, project_id)
    if not cfg["slots"]:
        out.append("нет расписания — канал не знает, к какому времени делать ролики (schedule.times)")
    if not cfg["platforms"]:
        out.append("не выбраны площадки — одобренный ролик некуда поставить")
    else:
        _ids, problems = A._accounts_for(store, cfg, cfg["platforms"])
        out += problems
    try:
        mascot = channel_mascot(store, channel)
        if E.sheet_path(store, mascot) is None:
            out.append(f"у маскота «{mascot['name']}» нет листа — ролики не начнутся")
    except (E.ExplError, KeyError) as exc:
        out.append(str(exc))
    if not settings.gemini_api_key:
        out.append("Gemini не настроен: темы и сценарии — только руками (свои темы и explainer_set_script)")
    for module_id, label in (("autopilot", "Автопилот канала"), ("explainer", "Объяснялки")):
        if not modules.is_enabled(module_id, store):
            out.append(f"модуль «{label}» выключен — канал стоит")
    return out


def tick_channel(store: Any, project_id: int, client: Any = None, run: bool = False) -> dict:
    """Один проход канала: синхронизация, темы (если мало), запуск роликов под свободные слоты."""
    channel = get_channel(store, project_id)
    out: dict = {"project_id": int(project_id), "changes": sync(store, project_id), "started": []}
    cfg = channel["config"]
    backlog = len(list_topics(store, project_id, "approved"))
    pending_batch = store.db.query_one(
        "SELECT COUNT(*) AS n FROM expl_topics WHERE project_id = ? AND status = 'proposed'", (int(project_id),))
    last = A.parse_utc(channel.get("last_proposed_at"))
    due = last is None or _now() - last >= timedelta(hours=float(cfg["propose_every_hours"]))
    if backlog < int(cfg["backlog_min"]) and not int(pending_batch["n"]) and due:
        try:
            res = propose_topics(store, project_id, client=client)
            out["proposed"] = len(res["proposed"])
        except Exception as exc:  # noqa: BLE001 - нет Gemini / сбой: скажем владельцу один раз в день
            store.db.execute("UPDATE expl_channels SET last_proposed_at = ? WHERE project_id = ?",
                             (_now_text(), int(project_id)))
            out["propose_error"] = str(exc)[:300]
            if backlog == 0:
                A.submit_card(store, int(project_id), "other",
                              f"Темы канала кончились, а новые не придумались: {str(exc)[:200]}. Добавь темы "
                              "(Серии → канал → Темы) — ролики пойдут сами.",
                              {"action": CARD_NOTE}, title="Нет тем для канала",
                              dedupe_key=f"{CARD_NOTE}:empty:{project_id}:{_now().strftime('%Y-%m-%d')}",
                              submitted_by=REQUESTED_BY)
    cap = capacity(store, project_id, channel)
    out["capacity"] = cap
    for topic in list_topics(store, project_id, "approved")[: cap["start"]]:
        try:
            video = start_video(store, int(topic["id"]), client=client, run=run)
            out["started"].append({"topic_id": topic["id"], "video_id": video["id"]})
        except Exception as exc:  # noqa: BLE001 - тема уже помечена failed
            out.setdefault("errors", []).append(f"#{topic['id']}: {str(exc)[:200]}")
    if run and out["started"]:
        out["changes"] += sync(store, project_id)
    store.db.execute("UPDATE expl_channels SET last_tick_at = ? WHERE project_id = ?", (_now_text(), int(project_id)))
    return out


def tick(store: Any, client: Any = None, run: bool = False) -> dict:
    """Проход всех включённых каналов (из ``autopilot.tick``)."""
    out = {"channels": []}
    for channel in list_channels(store, enabled_only=True):
        try:
            out["channels"].append(tick_channel(store, int(channel["project_id"]), client=client, run=run))
        except Exception as exc:  # noqa: BLE001 - один канал не валит остальные
            _log.warning("explainer channel %s tick failed: %s", channel["project_id"], exc)
    return out


# --------------------------------------------------------------------------- #
# Вид для UI и MCP
# --------------------------------------------------------------------------- #


def channel_view(store: Any, project_id: int) -> dict:
    channel = get_channel(store, project_id)
    project = P.get_project(store, project_id, include_sources=False)
    mascot = None
    if channel.get("mascot_id"):
        try:
            m = E.get_mascot(store, int(channel["mascot_id"]))
            poses = E.list_poses(store, int(m["id"]))
            mascot = {"id": m["id"], "name": m["name"], "project_id": m["project_id"], "persona": m["persona"],
                      "voice": E.mascot_voice(store, m), "sheet_url": (m.get("sheet") or {}).get("url"),
                      "bg_color": m["bg_color"], "poses": len(poses), "poses_ready": sum(1 for p in poses if p["ready"])}
        except KeyError:
            mascot = None
    topics = list_topics(store, project_id)
    videos: dict[int, dict] = {}
    for topic in topics:
        if topic.get("video_id"):
            try:
                v = E.get_video(store, int(topic["video_id"]), full=False)
                videos[int(topic["video_id"])] = v
                topic["video"] = {k: v.get(k) for k in ("id", "status", "status_label", "title", "clip_id", "error",
                                                         "estimate_rub", "approval_id")}
            except KeyError:
                topic["video"] = None
        if topic.get("approval_id"):
            try:
                card = A.get_approval(store, int(topic["approval_id"]))
                topic["approval"] = {k: card.get(k) for k in ("id", "status", "publish_status", "scheduled_local",
                                                                "publish_error", "title")}
            except KeyError:
                topic["approval"] = None
    counts = {s: 0 for s in TOPIC_STATUSES}
    for topic in topics:
        counts[topic["status"]] += 1
    cfg = A.get_config(store, project_id)
    schedule = A.get_schedule(store, project_id, free=8)
    return {"channel": channel, "project": {k: project.get(k) for k in ("id", "name", "kind", "kind_label",
                                                                          "description")},
            "mascot": mascot, "topics": topics, "counts": counts,
            "schedule": {"timezone": cfg["timezone"], "slots": cfg["slots"], "per_day": cfg["per_day"],
                         "platforms": cfg["platforms"], "accounts": cfg["accounts"],
                         "free_slots": schedule["free_slots"], "upcoming": schedule["upcoming"]},
            "capacity": capacity(store, project_id, channel), "warnings": warnings(store, project_id, channel),
            "status_labels": TOPIC_STATUS_LABELS}
