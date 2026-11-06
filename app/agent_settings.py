"""«Как работает агент»: настройки проекта (решение 15, docs/redesign/concept-2026-11.md).

Владелец: «настраиваем под каждый проект. Где то ждёт ответов на вопросы, где то тратить пользу без
вопросов, где то публикует сам». Настройка у проекта одна, и её читают все: чат, MCP, REST по токену,
наблюдатель VOD и канал объяснялок.

* ``wait_answers`` (вкл.): открытый обязательный вопрос держит монтаж видео (app/stream_questions.py).
* ``stop_at_clip_list`` (вкл.): агент предлагает черновики, а монтирует только выбранные владельцем.
  Черновик, ждущий выбора, — клип, который нашёл анализ, без готового файла, ещё не взятый владельцем в
  работу (``clip_plans.owner_picked``: ★ в браузере, любая его правка, кнопка в чате). Клип, который агент
  создал по прямому заданию (``create_clip``, анализ ``manual``), не черновик из списка: он работает как раньше.
* ``spend`` (выкл., 0 ₽): этап в пределах суммы за раз и за месяц одобряется сам, той же карточкой и теми же
  потолками денег (app/aivideo_gen.py). Сверх суммы или при выключенной настройке карточка ждёт владельца.
* ``publish`` (выкл.): клип уходит в публикацию сам, только на выбранные здесь аккаунты и с выбранной здесь
  приватностью. Без явно выбранного аккаунта не публикует никогда. Задание помечено ``jobs.made_by = 'agent'``.

Менять может только владелец (Bearer без куки входа получает 403, app/agent_settings_api.py). Читать могут все:
агенту настройки приходят в срез проекта, в ``get_project_brief`` и в ``get_agent_settings``.

Без строки в базе и при испорченной записи действуют значения по умолчанию: они самые строгие. Видео без проекта
живёт как раньше: пауза на вопросах работает, списка нет, тратить и публиковать без подтверждения нельзя.
"""

from __future__ import annotations

import json
from typing import Any

SETTINGS_SQL = """
    CREATE TABLE IF NOT EXISTS project_agent_settings (
        project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
        settings_json TEXT NOT NULL DEFAULT '{}',
        updated_by TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
"""

# Кто решил карточку: в ``approvals.decided_by``, ленте и журнале трат это видно всегда.
SPEND_ACTOR = "setting:spend"
PUBLISH_ACTOR = "setting:publish"
MADE_BY_AGENT = "agent"
ACTOR_LABELS = {SPEND_ACTOR: "настройка проекта «Тратить без подтверждения»",
                PUBLISH_ACTOR: "настройка проекта «Публиковать самому»"}

NAME_WAIT = "Ждать ответов на вопросы перед монтажом"
NAME_STOP = "Останавливаться на списке клипов"
NAME_SPEND = "Тратить без подтверждения"
NAME_PUBLISH = "Публиковать самому"
WHERE = "страница проекта, вкладка «Автопилот», раздел «Как работает агент»"
PRIVACY = ("public", "unlisted", "private")
MAX_PER_DAY = 50
MAX_RUB = 100000.0
RECENT = 5
# Запуски, чьи деньги ещё не в журнале: идут, держатся или ответ не дошёл (списание не подтверждено).
_RESERVING = ("approved", "submitting", "polling", "recovering", "held", "uncertain")


def defaults() -> dict:
    return {
        "wait_answers": True,
        "stop_at_clip_list": True,
        "spend": {"enabled": False, "per_run_rub": 0.0, "per_month_rub": 0.0},
        "publish": {"enabled": False, "account_ids": [], "privacy": "private", "per_day": 1},
    }


def ensure_agent_settings_schema(conn: Any) -> None:
    conn.execute(SETTINGS_SQL)


def where(project_id: int | None = None) -> str:
    return WHERE + (f" (/p/{int(project_id)}?tab=autopilot)" if project_id else "")


# --------------------------------------------------------------------------- #
# Чтение и запись
# --------------------------------------------------------------------------- #


def _rub(value: Any) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("сумма: число рублей") from exc
    if number != number or number < 0 or number > MAX_RUB:
        raise ValueError(f"сумма: от 0 до {MAX_RUB:.0f} ₽")
    return round(number, 2)


def _clean(raw: Any) -> dict:
    """Значения по умолчанию + то, что записано. Плохое значение — ValueError (для записи)."""
    raw = raw if isinstance(raw, dict) else {}
    out = defaults()
    for key in ("wait_answers", "stop_at_clip_list"):
        if key in raw:
            out[key] = bool(raw[key])
    spend = raw.get("spend") if isinstance(raw.get("spend"), dict) else {}
    out["spend"] = {"enabled": bool(spend.get("enabled", False)), "per_run_rub": _rub(spend.get("per_run_rub")),
                    "per_month_rub": _rub(spend.get("per_month_rub"))}
    publish = raw.get("publish") if isinstance(raw.get("publish"), dict) else {}
    privacy = str(publish.get("privacy") or "private").strip().lower()
    if privacy not in PRIVACY:
        raise ValueError(f"приватность: {', '.join(PRIVACY)}")
    ids = publish.get("account_ids") if isinstance(publish.get("account_ids"), list) else []
    try:
        per_day = int(publish.get("per_day", 1))
        account_ids = sorted({int(i) for i in ids})
    except (TypeError, ValueError) as exc:
        raise ValueError("публикация: аккаунты и лимит в день — числа") from exc
    if not 1 <= per_day <= MAX_PER_DAY:
        raise ValueError(f"публикаций в день: от 1 до {MAX_PER_DAY}")
    out["publish"] = {"enabled": bool(publish.get("enabled", False)), "account_ids": account_ids, "privacy": privacy,
                      "per_day": per_day}
    return out


def get_settings(store: Any, project_id: int | None) -> dict:
    if project_id is None:
        return defaults()
    row = store.db.query_one("SELECT settings_json FROM project_agent_settings WHERE project_id = ?", (int(project_id),))
    if not row:
        return defaults()
    try:
        return _clean(json.loads(row["settings_json"] or "{}"))
    except (ValueError, TypeError):
        return defaults()


def _known_accounts(store: Any, ids: list[int]) -> list[dict]:
    out = []
    for account_id in ids:
        try:
            account = store.get_account(int(account_id), include_secret=False)
        except KeyError:
            continue
        out.append({"id": int(account["id"]), "platform": account["platform"], "label": account.get("label") or ""})
    return out


def _change_note(old: dict, new: dict) -> str:
    def word(on: bool) -> str:
        return "вкл." if on else "выкл."

    parts = []
    if old["wait_answers"] != new["wait_answers"]:
        parts.append(f"«{NAME_WAIT}»: {word(new['wait_answers'])}")
    if old["stop_at_clip_list"] != new["stop_at_clip_list"]:
        parts.append(f"«{NAME_STOP}»: {word(new['stop_at_clip_list'])}")
    if old["spend"] != new["spend"]:
        spend = new["spend"]
        parts.append(f"«{NAME_SPEND}»: {word(spend['enabled'])}, за раз {spend['per_run_rub']:g} ₽, "
                     f"за месяц {spend['per_month_rub']:g} ₽")
    if old["publish"] != new["publish"]:
        publish = new["publish"]
        parts.append(f"«{NAME_PUBLISH}»: {word(publish['enabled'])}, аккаунты {publish['account_ids'] or 'не выбраны'}, "
                     f"{publish['privacy']}, в день {publish['per_day']}")
    return "; ".join(parts)


def _cancel_queued_publications(store: Any, project_id: int, actor: str) -> int:
    """Владелец снял разрешение: то, что агент сам поставил в очередь и что ещё не ушло, не публикуется."""
    from app import autopilot as A

    A.sync_publish_status(store, project_id)
    rows = store.db.query_all(
        "SELECT a.id FROM approvals a JOIN jobs j ON j.id = a.job_id WHERE a.project_id = ? AND a.decided_by = ? "
        "AND a.publish_status = 'scheduled' AND j.status = 'queued'", (int(project_id), PUBLISH_ACTOR))
    for row in rows:
        A.reschedule(store, int(row["id"]), "cancel", actor=actor)
    return len(rows)


def save_settings(store: Any, project_id: int, patch: Any, actor: str = "ui") -> dict:
    """Частичное обновление: ключи верхнего уровня, ``spend`` и ``publish`` дополняются. Решает только владелец."""
    from app import autopilot as A
    from app import projects as P

    if not isinstance(patch, dict):
        raise ValueError("настройки агента — объект")
    P.get_project(store, int(project_id), include_sources=False)
    current = get_settings(store, project_id)
    merged = dict(current)
    for key in ("wait_answers", "stop_at_clip_list"):
        if key in patch:
            merged[key] = patch[key]
    for key in ("spend", "publish"):
        if isinstance(patch.get(key), dict):
            merged[key] = {**current[key], **patch[key]}
    new = _clean(merged)
    spend = new["spend"]
    if spend["enabled"] and (spend["per_run_rub"] <= 0 or spend["per_month_rub"] <= 0):
        raise ValueError("Укажи сумму за раз и за месяц: с нулём тратить нечего")
    if spend["enabled"] and spend["per_run_rub"] > spend["per_month_rub"]:
        raise ValueError("Сумма за раз больше суммы за месяц")
    publish = new["publish"]
    chosen = _known_accounts(store, publish["account_ids"])
    missing = set(publish["account_ids"]) - {a["id"] for a in chosen}
    if missing:
        raise ValueError(f"Этих аккаунтов нет: {', '.join(map(str, sorted(missing)))}")
    if publish["enabled"] and not chosen:
        raise ValueError("Выбери хотя бы один аккаунт: без него агент сам не публикует")
    note = _change_note(current, new)
    store.db.execute(
        "INSERT INTO project_agent_settings (project_id, settings_json, updated_by) VALUES (?, ?, ?) "
        "ON CONFLICT(project_id) DO UPDATE SET settings_json = excluded.settings_json, "
        "updated_by = excluded.updated_by, updated_at = CURRENT_TIMESTAMP",
        (int(project_id), json.dumps(new, ensure_ascii=False), str(actor or "")[:80]))
    cancelled = 0
    narrowed = current["publish"]["enabled"] and (
        not publish["enabled"] or not set(current["publish"]["account_ids"]) <= set(publish["account_ids"]))
    if narrowed:
        cancelled = _cancel_queued_publications(store, int(project_id), actor)
    if note:
        A.log_event(store, None, int(project_id), "agent_settings", actor,
                    note + (f"; снято из очереди публикаций: {cancelled}" if cancelled else ""))
    return {**view(store, int(project_id)), "cancelled_publications": cancelled}


# --------------------------------------------------------------------------- #
# Ждать ответов и список клипов
# --------------------------------------------------------------------------- #


def _project_of_source(store: Any, source_id: int) -> int | None:
    row = store.db.query_one("SELECT project_id FROM sources WHERE id = ?", (int(source_id),))
    return int(row["project_id"]) if row and row["project_id"] is not None else None


def waits_for_answers(store: Any, source_id: int) -> bool:
    return bool(get_settings(store, _project_of_source(store, source_id))["wait_answers"])


def _family_ids(store: Any, clip_plan_id: int) -> list[int]:
    row = store.db.query_one("SELECT clip_group_id FROM clip_plans WHERE id = ?", (int(clip_plan_id),))
    if not row or row["clip_group_id"] is None:
        return [int(clip_plan_id)]
    rows = store.db.query_all("SELECT id FROM clip_plans WHERE clip_group_id = ?", (int(row["clip_group_id"]),))
    return sorted({int(r["id"]) for r in rows} | {int(clip_plan_id)})


def awaits_pick(store: Any, clip_plan_id: int) -> bool:
    """Черновик из списка, который владелец ещё не взял: нашёл анализ, готового файла нет, выбора не было."""
    plan = store.db.query_one(
        "SELECT p.source_id, p.owner_picked, a.provider FROM clip_plans p "
        "LEFT JOIN ai_analyses a ON a.id = p.analysis_id WHERE p.id = ?", (int(clip_plan_id),))
    if not plan or plan["provider"] in (None, "manual"):
        return False
    family = _family_ids(store, clip_plan_id)
    marks = ",".join("?" for _ in family)
    picked = store.db.query_one(f"SELECT 1 FROM clip_plans WHERE id IN ({marks}) AND owner_picked = 1 LIMIT 1", family)
    if picked:
        return False
    rendered = store.db.query_one(
        f"SELECT 1 FROM clips WHERE clip_plan_id IN ({marks}) AND status = 'succeeded' LIMIT 1", family)
    return not rendered


def set_picked(store: Any, clip_plan_id: int, picked: bool = True) -> None:
    """Выбор владельца: ★, его правка, кнопка в чате. Агент этого сделать не может: звёздочка по токену не считается."""
    store.db.execute("UPDATE clip_plans SET owner_picked = ? WHERE id = ? AND owner_picked != ?",
                     (1 if picked else 0, int(clip_plan_id), 1 if picked else 0))


def set_picked_many(store: Any, clip_plan_ids: Any) -> None:
    for clip_plan_id in clip_plan_ids or []:
        set_picked(store, int(clip_plan_id))


def clip_list_refusal(store: Any, clip_plan_id: int) -> dict | None:
    """Отказ агенту: этот черновик владелец ещё не выбрал. ``None`` — можно работать."""
    if not awaits_pick(store, clip_plan_id):
        return None
    source = store.db.query_one("SELECT source_id FROM clip_plans WHERE id = ?", (int(clip_plan_id),))
    source_id = int(source["source_id"])
    project_id = _project_of_source(store, source_id)
    if project_id is None or not get_settings(store, project_id)["stop_at_clip_list"]:
        return None
    return {
        "ok": False,
        "paused": "clip_list",
        "setting": "stop_at_clip_list",
        "source_id": source_id,
        "clip_id": int(clip_plan_id),
        "error": (f"Клип #{clip_plan_id} — черновик из списка, владелец его ещё не выбрал. В этом проекте включена "
                  f"настройка «{NAME_STOP}»: агент предлагает, монтирует только выбранное. Покажи владельцу список "
                  f"и дождись выбора: ★ на вкладке «Черновики» (/v/{source_id}/candidates). Выключить настройку "
                  f"может только он: {where(project_id)}."),
    }


def drafts_refusal(store: Any, clip_plan_ids: Any) -> dict | None:
    for clip_plan_id in clip_plan_ids or []:
        refusal = clip_list_refusal(store, int(clip_plan_id))
        if refusal:
            return refusal
    return None


def waiting_drafts(store: Any, source_ids: list[int]) -> int:
    """Сколько черновиков ждут выбора: для подсказки на вкладке «Черновики» и в разделе настроек."""
    if not source_ids:
        return 0
    count = 0
    for chunk_start in range(0, len(source_ids), 400):
        chunk = source_ids[chunk_start:chunk_start + 400]
        marks = ",".join("?" for _ in chunk)
        rows = store.db.query_all(
            f"SELECT p.id FROM clip_plans p JOIN ai_analyses a ON a.id = p.analysis_id WHERE p.source_id IN ({marks}) "
            "AND p.hidden = 0 AND p.owner_picked = 0 AND a.provider != 'manual' "
            "AND NOT EXISTS (SELECT 1 FROM clips c WHERE c.clip_plan_id = p.id AND c.status = 'succeeded')", chunk)
        count += sum(1 for r in rows if awaits_pick(store, int(r["id"])))
    return count


def list_stop_message(project_id: int, count: int) -> str:
    return (f"Черновиков: {count}. Дальше не монтирую: включена настройка «{NAME_STOP}». Выбери нужные ★ на вкладке "
            f"«Черновики» видео. Изменить: {where(project_id)}.")


# --------------------------------------------------------------------------- #
# Траты
# --------------------------------------------------------------------------- #


def _sum(store: Any, sql: str, params: tuple) -> float:
    row = store.db.query_one(sql, params)
    return round(float((row[0] if row else 0) or 0), 2)


def spent_by_setting(store: Any, project_id: int) -> float:
    """Что уже списано в этом месяце по картам, одобренным настройкой."""
    return _sum(store,
                "SELECT COALESCE(SUM(l.amount_rub), 0) FROM aiv_ledger l JOIN approvals a ON a.id = l.approval_id "
                "WHERE a.project_id = ? AND a.decided_by = ? AND l.created_at >= date('now', 'start of month')",
                (int(project_id), SPEND_ACTOR))


def reserved_by_setting(store: Any, project_id: int, exclude_job: int | None = None) -> float:
    marks = ",".join("?" for _ in _RESERVING)
    sql = (f"SELECT COALESCE(SUM(j.estimate_rub), 0) FROM aiv_gen_jobs j JOIN approvals a ON a.id = j.approval_id "
           f"WHERE a.project_id = ? AND a.decided_by = ? AND j.provider != 'none' AND j.status IN ({marks}) "
           "AND j.cost_source = ''")
    params: tuple = (int(project_id), SPEND_ACTOR, *_RESERVING)
    if exclude_job is not None:
        sql += " AND j.id != ?"
        params = (*params, int(exclude_job))
    return _sum(store, sql, params)


def spend_decision(store: Any, project_id: int, amount: float) -> dict:
    """Можно ли этот этап оплатить без карточки. Потолки денег проверяет сам конвейер до этого вызова и при запуске."""
    amount = round(float(amount), 2)
    spend = get_settings(store, project_id)["spend"]
    if not spend["enabled"]:
        return {"allowed": False, "reason": f"настройка «{NAME_SPEND}» выключена", "asked": False}
    if amount > spend["per_run_rub"] + 1e-6:
        return {"allowed": False, "asked": True,
                "reason": f"{amount:.2f} ₽ больше суммы за раз в настройке «{NAME_SPEND}» ({spend['per_run_rub']:g} ₽)"}
    used = round(spent_by_setting(store, project_id) + reserved_by_setting(store, project_id), 2)
    if used + amount > spend["per_month_rub"] + 1e-6:
        return {"allowed": False, "asked": True,
                "reason": (f"за месяц по настройке «{NAME_SPEND}» уже {used:.2f} ₽, вместе с этой суммой "
                           f"{used + amount:.2f} ₽, а можно {spend['per_month_rub']:g} ₽")}
    return {"allowed": True, "asked": False, "reason": "",
            "note": (f"Без подтверждения: {ACTOR_LABELS[SPEND_ACTOR]}, за раз до {spend['per_run_rub']:g} ₽, "
                     f"за месяц до {spend['per_month_rub']:g} ₽ (уже {used:.2f} ₽).")}


def spend_problems_at_run(store: Any, project_id: int, card_amount: float, job: dict) -> list[str]:
    """Перед самой оплатой запуска по карточке, одобренной настройкой: владелец мог успеть её выключить или снизить."""
    spend = get_settings(store, project_id)["spend"]
    if not spend["enabled"]:
        return [f"настройка «{NAME_SPEND}» выключена после разрешения этой карточки: нужна новая карточка"]
    if float(card_amount) > spend["per_run_rub"] + 1e-6:
        return [f"сумма карточки {float(card_amount):.2f} ₽ теперь больше суммы за раз в настройке «{NAME_SPEND}» "
                f"({spend['per_run_rub']:g} ₽): нужна новая карточка"]
    used = spent_by_setting(store, project_id) + _sum(
        store,
        "SELECT COALESCE(SUM(j.estimate_rub), 0) FROM aiv_gen_jobs j JOIN approvals a ON a.id = j.approval_id "
        "WHERE a.project_id = ? AND a.decided_by = ? AND j.status = 'uncertain' AND j.cost_source = '' AND j.id != ?",
        (int(project_id), SPEND_ACTOR, int(job["id"])))
    if used + float(job["estimate_rub"]) > spend["per_month_rub"] + 1e-6:
        return [f"за месяц по настройке «{NAME_SPEND}» {used:.2f} ₽ + {float(job['estimate_rub']):.2f} ₽ больше "
                f"{spend['per_month_rub']:g} ₽: нужна новая карточка"]
    return []


def ledger_note(store: Any, approval_id: Any) -> str:
    """Отметка в журнале трат: списание разрешила настройка, а не кнопка владельца."""
    if not approval_id:
        return ""
    row = store.db.query_one("SELECT decided_by FROM approvals WHERE id = ?", (int(approval_id),))
    return "без подтверждения: разрешила настройка проекта" if row and row["decided_by"] == SPEND_ACTOR else ""


# --------------------------------------------------------------------------- #
# Публикация
# --------------------------------------------------------------------------- #


def publish_decision(store: Any, project_id: int, content_type: str, platforms: list[str], trust: dict,
                     submitted_by: str) -> dict:
    """Опубликовать самому или спросить. Разрешено только когда владелец включил настройку и явно выбрал аккаунт на
    каждую площадку; своё «спросить меня» у типа контента (доверие в автопилоте) сильнее."""
    publish = get_settings(store, project_id)["publish"]
    refused = {"allowed": False, "platforms": platforms, "account_ids": [], "privacy": publish["privacy"]}
    if not publish["enabled"]:
        return {**refused, "reason": f"настройка «{NAME_PUBLISH}» выключена"}
    if submitted_by == "ui":
        return {**refused, "reason": "карточку отправил сам владелец"}
    if content_type and (trust or {}).get(content_type) == "approve":
        return {**refused, "reason": f"тип «{content_type}» в автопилоте всегда спрашивает владельца"}
    chosen = _known_accounts(store, publish["account_ids"])
    if not chosen:
        return {**refused, "reason": f"в настройке «{NAME_PUBLISH}» нет выбранного аккаунта"}
    available = list(dict.fromkeys(a["platform"] for a in chosen))
    wanted = list(platforms) or available
    extra = [p for p in wanted if p not in available]
    if extra:
        return {**refused, "reason": f"на площадку {', '.join(extra)} аккаунт в настройке «{NAME_PUBLISH}» не выбран"}
    return {"allowed": True, "reason": "", "platforms": wanted, "privacy": publish["privacy"],
            "per_day": publish["per_day"], "account_ids": [a["id"] for a in chosen if a["platform"] in wanted]}


# --------------------------------------------------------------------------- #
# Что видит владелец и что читает агент
# --------------------------------------------------------------------------- #


def _month_cap(store: Any, project_id: int) -> float:
    from app.settings import settings

    row = store.db.query_one("SELECT month_cap_rub FROM aiv_projects WHERE project_id = ?", (int(project_id),))
    own = row["month_cap_rub"] if row else None
    global_cap = float(settings.aivideo_month_cap_rub)
    return round(min(float(own), global_cap) if own is not None else global_cap, 2)


def _recent_spends(store: Any, project_id: int) -> list[dict]:
    rows = store.db.query_all(
        "SELECT id, title, decided_at, payload_json FROM approvals WHERE project_id = ? AND decided_by = ? "
        "ORDER BY id DESC LIMIT ?", (int(project_id), SPEND_ACTOR, RECENT))
    out = []
    for row in rows:
        try:
            amount = float(json.loads(row["payload_json"] or "{}").get("amount_rub") or 0)
        except (TypeError, ValueError):
            amount = 0.0
        out.append({"approval_id": int(row["id"]), "title": row["title"], "amount_rub": round(amount, 2),
                    "decided_at": row["decided_at"] or ""})
    return out


def _recent_publications(store: Any, project_id: int) -> list[dict]:
    rows = store.db.query_all(
        "SELECT id, title, scheduled_at, publish_status, job_id FROM approvals WHERE project_id = ? AND decided_by = ? "
        "ORDER BY id DESC LIMIT ?", (int(project_id), PUBLISH_ACTOR, RECENT))
    return [{"approval_id": int(r["id"]), "title": r["title"], "scheduled_at": r["scheduled_at"] or "",
             "publish_status": r["publish_status"] or "", "job_id": r["job_id"]} for r in rows]


def summary_lines(settings_data: dict, accounts: list[dict], project_id: int | None = None) -> list[str]:
    """Что проект разрешает, простыми словами: это читает агент."""
    spend, publish = settings_data["spend"], settings_data["publish"]
    lines = [
        ("Открытый обязательный вопрос держит монтаж видео: дождись ответов." if settings_data["wait_answers"]
         else "Монтаж не ждёт ответов: вопросы задавай, но работу они не держат."),
        ("Черновики из анализа только предлагай: монтировать можно то, что выбрал владелец."
         if settings_data["stop_at_clip_list"] else "Черновики можно брать в монтаж самому, выбора владельца не нужно."),
        (f"Тратить без подтверждения можно: за раз до {spend['per_run_rub']:g} ₽, за месяц до {spend['per_month_rub']:g} ₽; "
         "сверх этого карточка ждёт владельца." if spend["enabled"]
         else "Каждая трата ждёт «Разрешить» владельца на карточке."),
        ("Публиковать самому можно: " + ", ".join(f"{a['platform']} «{a['label']}»" for a in accounts)
         + f", приватность {publish['privacy']}, не больше {publish['per_day']} в день. Другие площадки и аккаунты: карточка."
         if publish["enabled"] and accounts else "Сам не публикуешь: клип уходит владельцу карточкой одобрения."),
    ]
    lines.append(f"Менять настройки может только владелец: {where(project_id)}. Сам их не меняй и не обходи.")
    return lines


def view(store: Any, project_id: int) -> dict:
    """Раздел «Как работает агент»: настройки, что по ним уже сделано и как это прочитает агент."""
    from app import projects as P
    from app.settings import settings

    P.get_project(store, int(project_id), include_sources=False)
    data = get_settings(store, project_id)
    chosen = _known_accounts(store, data["publish"]["account_ids"])
    spent = spent_by_setting(store, project_id)
    reserved = reserved_by_setting(store, project_id)
    sources = [int(r["id"]) for r in store.db.query_all("SELECT id FROM sources WHERE project_id = ?", (int(project_id),))]
    row = store.db.query_one("SELECT updated_at, updated_by FROM project_agent_settings WHERE project_id = ?",
                             (int(project_id),))
    return {
        "project_id": int(project_id),
        "settings": data,
        "defaults": defaults(),
        "accounts": chosen,
        "spend_state": {"spent_month_rub": spent, "reserved_rub": reserved, "project_month_cap_rub": _month_cap(store, project_id),
                        "paid_enabled": bool(settings.aivideo_enabled), "recent": _recent_spends(store, project_id)},
        "publish_state": {"recent": _recent_publications(store, project_id)},
        "drafts_waiting": waiting_drafts(store, sources) if data["stop_at_clip_list"] else 0,
        "summary": summary_lines(data, chosen, project_id),
        "where": where(project_id),
        "owner_only": True,
        "updated_at": row["updated_at"] if row else "",
    }


def agent_brief(store: Any, project_id: int) -> dict:
    """Короткий блок для агента: срез чата, ``get_project_brief``, ``get_agent_settings``."""
    data = get_settings(store, project_id)
    chosen = _known_accounts(store, data["publish"]["account_ids"])
    return {"allows": summary_lines(data, chosen, project_id),
            "wait_answers": data["wait_answers"], "stop_at_clip_list": data["stop_at_clip_list"],
            "spend": data["spend"], "publish": {**data["publish"], "accounts": chosen}}
