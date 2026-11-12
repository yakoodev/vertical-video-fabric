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
* Публиковать без одобрения можно только настройкой проекта «Публиковать самому» (app/agent_settings.py):
  на явно выбранные там аккаунты, с записью в ленту (``approval_events``) и пометкой в «Публикациях».
  Доверие по типам контента (``trust``) теперь только добавляет исключения: тип с ``approve`` спрашивает
  владельца всегда, ``auto`` само по себе ничего не публикует.
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

Где что лежит. Здесь — то, что зависит от часов ``now`` (их подменяют тесты): подача, решения, расписание, дайджест, работник. Вынесено:
настройка и карточки ``app/autopilot_config.py``, сигналы обучения ``app/autopilot_signals.py``. Те модули этот не импортируют, а он
их — поэтому все имена по-прежнему доступны отсюда.
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from app import projects as P
from app.db import atomic

from app.autopilot_config import (  # noqa: F401
    AUTOPILOT_SCHEMA_SQL, ensure_autopilot_schema, KINDS, KIND_LABELS, STATUSES, TRUST_LEVELS, PLATFORMS,
    DEFAULT_TZ, WEEKDAYS, TIME_FMT, _FIXED_ZONES, fmt_utc, parse_utc, tz_of, to_local, parse_when, _loads, _json,
    DEFAULT_CONFIG, _time_of_day, normalize_config, _state_row, set_state, get_config, update_config, trust_for,
    content_type_of, log_event, list_events, _approval_from_row, buttons_for, CARD_ACTIONS, register_card_action,
    get_approval, list_approvals, approval_counts, _update, _tg_on, _insert, _clean_tags, _platforms,
)
from app.autopilot_signals import (  # noqa: F401
    _GENERAL_RE, _COMMAND_RE, _DEICTIC_RE, _PROJECT_SCOPE_RE, rule_signal, _PRAISE_FILLER_RE, _bare, _sentences,
    _rule_text_of, describe_learning,
)

_log = logging.getLogger(__name__)


ACTIONS = ("approve", "reject", "changes", "both")


PLATFORM_LABELS = {"youtube": "YouTube", "tiktok": "TikTok", "instagram": "Instagram"}


SLOT_SEARCH_DAYS = 28


# Часы сервиса: тесты подменяют ``autopilot.now``.
now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)  # noqa: E731


def _now_text() -> str:
    return now().astimezone(timezone.utc).strftime(TIME_FMT)


def submit_clip(store: Any, clip_plan_id: int, title: str = "", description: str = "", tags: Any = None,
                platforms: Any = None, when: Any = None, content_type: Any = "", submitted_by: str = "agent",
                cover_sec: Any = None) -> dict:
    """Клип на одобрение. Нужен готовый рендер и проект у источника. Настройка «Публиковать самому» с выбранными
    аккаунтами — сразу одобрено (и в ленту), иначе pending + карточка владельцу. Прежняя pending-карточка
    того же клипа уходит в expired: на руках у владельца — одна, свежая. ``cover_sec`` — секунда кадра-обложки
    TikTok (пусто — выбранная обложка клипа или кадр TikTok по умолчанию), едет в задание публикации."""
    from app import agent_settings as S
    from app.publish_format import normalize_cover_sec

    cover_sec = normalize_cover_sec(cover_sec)

    plan = store.get_clip_plan(int(clip_plan_id), include_segments=False)
    project_id = P.project_of_plan(store, int(clip_plan_id))
    if project_id is None:
        raise ValueError(f"источник клипа #{clip_plan_id} не в проекте-серии — без проекта нет доверия и расписания")
    clips = [c for c in store.list_clips_for_plan(int(clip_plan_id)) if c.get("status") == "succeeded"]
    if not clips:
        raise ValueError(f"у клипа #{clip_plan_id} нет готового рендера — сначала render_clip (MCP; дождись render_status done)")
    clip = clips[0]
    cfg = get_config(store, project_id)
    platforms_list = _platforms(platforms) or list(cfg["platforms"])
    proposed = parse_when(when, cfg["timezone"])
    ctype = content_type_of(store, cfg, int(clip_plan_id), content_type)
    decision = S.publish_decision(store, project_id, ctype, _platforms(platforms), cfg["trust"], submitted_by)
    if decision["allowed"]:
        platforms_list = decision["platforms"]
    trust = "auto" if decision["allowed"] else "approve"
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
        payload_json=_json({"cover_sec": cover_sec} if cover_sec is not None else {}),
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
    if decision["allowed"]:
        note = f"Опубликует агент: {S.ACTOR_LABELS[S.PUBLISH_ACTOR]}"
        _update(store, approval_id, status="approved", decided_by=S.PUBLISH_ACTOR, decided_at=_now_text(),
                decision_comment=note)
        log_event(store, approval_id, project_id, "auto_approved", S.PUBLISH_ACTOR, note,
                  {"accounts": decision["account_ids"], "privacy": decision["privacy"]})
        _place(store, approval_id, actor=S.PUBLISH_ACTOR)
    out = get_approval(store, approval_id)
    out["publish_decision"] = decision["reason"]
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


def submit_card(store: Any, project_id: int | None, kind: str, question: str, payload: dict | None = None,
                title: str = "", rule_id: int | None = None, hypothesis_id: int | None = None,
                dedupe_key: str = "", submitted_by: str = "system", notify: bool = True) -> dict:
    """Карточка-вопрос владельцу (правило, статистика, что угодно). С dedupe_key — не
    повторяем, пока владелец не ответил или уже ответил (кроме expired). Без проекта
    (``project_id`` None) — только «внимание» (alert): публикация клипа вне серий."""
    kind = P._choice(kind, KINDS, "kind")
    if project_id is None and kind != "alert":
        raise ValueError("карточка без проекта — только alert")
    if project_id is not None:
        P.get_project(store, int(project_id), include_sources=False)
    if dedupe_key:
        row = store.db.query_one(
            "SELECT id FROM approvals WHERE dedupe_key = ? AND status != 'expired' ORDER BY id DESC LIMIT 1",
            (dedupe_key,))
        if row:
            return get_approval(store, row["id"]) | {"deduplicated": True}
    approval_id = _insert(
        store, project_id=int(project_id) if project_id is not None else None, kind=kind,
        title=P._text(title or question, 200),
        question=P._long_text(question, 2000), payload_json=_json(payload or {}), rule_id=rule_id,
        hypothesis_id=hypothesis_id, dedupe_key=dedupe_key, submitted_by=str(submitted_by or "")[:40],
        **({} if notify else {"tg_state": ""}),
    )
    log_event(store, approval_id, int(project_id) if project_id is not None else None, "submitted", submitted_by,
              question[:300])
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
    # Сравнить-и-записать: два одновременных решения (двойной клик, кнопка в Telegram и в UI) —
    # выполняется одно, второе видит «уже решена» (иначе обработчик карточки сработал бы дважды).
    hook = (CARD_ACTIONS.get(str(approval["payload"].get("action") or ""))
            if approval["kind"] in ("other", "alert") else None)
    result: dict = {"ok": True, "status": status}
    # Решение и его следствия целиком: упало посреди — карточка снова ждёт, а не «одобрена» без запусков.
    with store.db.transaction():
        cur = store.db.execute(
            "UPDATE approvals SET status = ?, decision_comment = ?, decided_by = ?, decided_at = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'pending'",
            (status, comment, str(actor or "")[:80], _now_text(), int(approval_id)))
        if not cur.rowcount:
            raise ValueError(f"карточка #{approval_id} уже решена: {get_approval(store, approval_id)['status']}")
        log_event(store, approval_id, approval["project_id"], status, actor, comment)
        if status == "approved":
            result.update(_on_approved(store, approval, action, comment, actor))
        elif comment and approval["kind"] == "clip_publish":
            result["learned"] = learn_from_text(store, approval["project_id"], comment, origin=_origin(actor),
                                                origin_ref=f"approval#{approval_id}", approval_id=approval_id,
                                                context={"title": approval["title"]})
        elif status == "rejected":
            result.update(_on_rejected(store, approval, comment, actor))
        if hook and hook.get("atomic"):
            result.update(hook["on_decide"](store, approval, action, comment, actor) or {})
    if hook and not hook.get("atomic"):
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
_JUST_ANSWER_RE = re.compile(r"(ничего не|пока не)\s+(созда|мен|дела|трога|запуска)\w*|ответь коротко|просто ответь", re.IGNORECASE)


_NOT_LANDS_RE = re.compile(r"не\s+(заход|залета)\w*", re.IGNORECASE)


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
    # «Ничего не меняй, ответь коротко» — просьба на эту реплику, а не правило: карточка спрашивала бы зря,
    # а записанное правило запретило бы агенту работать в проекте.
    if _JUST_ANSWER_RE.search(raw):
        return {"intent": "none", "confidence": 0.0}
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
              exclude: int | None = None, per_day: int | None = None) -> datetime:
    """Ближайший свободный момент: слот, не занятый другим клипом проекта, с лимитом в день
    на каждую площадку. Без слотов — сразу (если лимит дня позволяет), иначе завтра в 12:00.
    ``per_day`` — лимит настройки «Публиковать самому»: действует, если он строже лимита автопилота."""
    cfg = get_config(store, project_id)
    if per_day:
        tight = {key: min(value or per_day, per_day) for key, value in cfg["per_day"].items()}
        cfg = {**cfg, "per_day": {**tight, "default": min(tight.get("default") or per_day, per_day)}}
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


@atomic
def _place(store: Any, approval_id: int, actor: str = "", at: datetime | None = None,
           use_proposed: bool = True) -> dict:
    """Одобренный клип → задание публикации на ближайший свободный слот (или на ``at``).
    Не вышло (нет аккаунта, нет слотов) — publish_status=failed с причиной, без исключения."""
    from app import agent_settings as S

    approval = get_approval(store, approval_id)
    if approval["status"] != "approved" or approval["kind"] != "clip_publish":
        raise ValueError("в расписание — только одобренная публикация клипа")
    project_id = int(approval["project_id"])
    cfg = get_config(store, project_id)
    platforms = approval["platforms"] or cfg["platforms"]
    made_by = ""
    privacy = cfg["privacy"]
    try:
        if not platforms and approval.get("decided_by") != S.PUBLISH_ACTOR:
            raise ValueError("не выбраны площадки — укажи platforms или площадки по умолчанию в автопилоте")
        if not approval.get("clip_id"):
            raise ValueError("рендер клипа удалён — отрендери заново и отправь на одобрение")
        proposed = parse_utc(approval.get("proposed_at")) if use_proposed else None
        proposed = proposed if proposed and proposed > now() else None
        if approval.get("decided_by") == S.PUBLISH_ACTOR:
            # Только то, что владелец выбрал сейчас: настройку могли изменить после карточки.
            decision = S.publish_decision(store, project_id, approval.get("content_type") or "", platforms, cfg["trust"],
                                          "agent")
            if not decision["allowed"]:
                raise ValueError(f"не публикую сам: {decision['reason']}. Изменить: {S.where(project_id)}")
            platforms, account_ids, privacy = decision["platforms"], decision["account_ids"], decision["privacy"]
            made_by = S.MADE_BY_AGENT
            moment = at or next_slot(store, project_id, platforms, after=proposed, exclude=approval_id,
                                     per_day=decision["per_day"])
        else:
            account_ids, problems = _accounts_for(store, cfg, platforms)
            if problems:
                raise ValueError("; ".join(problems))
            moment = at or proposed or next_slot(store, project_id, platforms, exclude=approval_id)
        # Прежнее задание (перенос «в ближайший слот») снимаем раньше нового: работник успел его взять — второго не будет.
        old_job = approval.get("job_id")
        if old_job and not store.cancel_job(int(old_job)) and store.get_job(int(old_job))["status"] != "cancelled":
            raise ValueError(f"публикация №{old_job} уже идёт — переносить поздно")
        job = store.create_clip_post_job(
            int(approval["clip_id"]), approval["title"], _description_with_tags(approval["description"],
                                                                                 approval["tags"]),
            account_ids, privacy, True, scheduled_at=fmt_utc(moment), made_by=made_by,
            cover_sec=(approval.get("payload") or {}).get("cover_sec"),
        )
    except (KeyError, ValueError) as exc:
        _update(store, approval_id, publish_status="failed", publish_error=str(exc)[:500])
        log_event(store, approval_id, project_id, "schedule_failed", actor, str(exc)[:500])
        return {"ok": False, "error": str(exc)}
    _update(store, approval_id, publish_status="scheduled", publish_error="", scheduled_at=fmt_utc(moment),
            job_id=int(job["id"]), platforms_json=_json(platforms))
    local = to_local(moment, cfg["timezone"])
    log_event(store, approval_id, project_id, "scheduled", actor, f"{local} ({cfg['timezone']})",
              {"job_id": job["id"], "scheduled_at": fmt_utc(moment)})
    return {"ok": True, "job_id": job["id"], "scheduled_at": fmt_utc(moment), "scheduled_local": local}


@atomic
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
            store.cancel_job(int(job["id"]))
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
    try:  # каналы объяснялок (app/explainer_channel.py): темы, ролики к слотам, ролики на одобрение
        from app import explainer_channel, modules

        if modules.is_enabled("explainer", store):
            channels = explainer_channel.tick(store)
            if channels["channels"]:
                out["explainer_channels"] = channels
    except Exception as exc:  # noqa: BLE001 - канал не валит автопилот
        _log.warning("explainer channels tick failed: %s", exc)
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
