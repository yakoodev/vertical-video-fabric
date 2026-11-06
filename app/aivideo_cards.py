"""ИИ-видео: оценка этапа и карточки одобрения — заявка, список, решение владельца (из ``app/aivideo_gen.py``)."""

from __future__ import annotations

import json
from typing import Any

from app import agent_settings as S
from app import aivideo as V
from app import autopilot as A
from app import projects as P
from app.aivideo_providers import PRICE_CHECKED, VIDEO_MODE_LABELS, video_capabilities, max_refs
from app.settings import settings
from app.aivideo_budget import (
    STAGE_LABELS, EPISODE_CAP_RUB, BudgetError, EXTENSIONS, _ext_of_target, spent_approval, budget_view,
    cap_problems
)
from app.aivideo_prompts import _paid, series_video_model, tail_mode, video_model_for, BUSY, build_request
from app.aivideo_checks import video_qa_flags, continuity_flags
from app.aivideo_plan import plan_stage

CARD_ACTION = "aiv_budget"


def estimate(store: Any, project_id: int, stage: str, script_id: int | None = None, items: list | None = None,
             regenerate: bool = False) -> dict:
    """Оценка этапа (бесплатно, ничего не запускает)."""
    V.require_project(store, project_id)
    raw = plan_stage(store, project_id, stage, script_id, items, regenerate)
    planned = [i for i in raw if not i.get("blocked") and not i.get("skipped")]
    blocked = [i["blocked"] for i in raw if i.get("blocked")]
    notes = [i["skipped"] for i in raw if i.get("skipped")]
    # Ждущая карточка этого же этапа, которую новая заменит (request_stage): её запуски не «в работе»
    # и не резерв — иначе повторный запрос того же этапа упирается в потолок сам о себя.
    replaces = (_replaceable_cards(store, project_id, stage, script_id, planned[0].get("group"))
                if planned and not planned[0].get("regenerate") else [])
    busy_jobs: list[dict] = []
    if not regenerate:
        # Повтор запроса (двойной клик, повтор после ошибки клиента): цель, у которой уже есть
        # ждущий/идущий запуск, второй раз не заказываем — иначе кадр рисуется и оплачивается дважды.
        # Явная перегенерация (aiv_regenerate, regenerate=true) — как раньше: новая версия.
        busy = _busy_jobs(store, project_id, exclude_cards=replaces)
        kept = []
        for item in planned:
            hit = busy.get(_target_key(item))
            if hit is None:
                kept.append(item)
                continue
            busy_jobs.append(hit)
            notes.append(f"{item['label']}: уже в работе (запуск #{hit['id']}, {hit['status']}"
                         + (f", карточка #{hit['approval_id']}" if hit.get("approval_id") else "")
                         + ") — второй раз не заказываю"
                         + ("; держится — «Продолжить» у карточки (aiv_run_approved) или отмени её запуски"
                            if hit["status"] == "held" else ""))
        planned = kept
    total = round(sum(i["estimate_rub"] for i in planned), 2)
    paid = any(_paid(i["provider"], i["kind"]) for i in planned)
    problems = cap_problems(store, project_id, script_id, total, stage, exclude_approval=replaces) if paid else []
    problems += blocked
    if paid and not settings.aivideo_enabled:
        problems.append("платная генерация выключена (AIVIDEO_ENABLED=0) — включает владелец; карточку можно "
                        "отправить заранее, запуск — после включения")
    style = V.get_style(store, project_id)
    extra: dict[str, Any] = {}
    if stage == "video":
        provider = style["video_provider"]
        quality = V.get_script(store, script_id, with_shots=False)["quality"] if script_id is not None else None
        model = video_model_for(style, quality)
        caps = video_capabilities(model, provider)
        modes = sorted({i.get("mode") or "" for i in planned} - {""})
        extra = {"video_caps": caps, "modes": modes}
        if planned:
            notes.append(f"{model}: " + ", ".join(VIDEO_MODE_LABELS.get(m, m) for m in modes)
                         + ("" if not caps.get("exclusive") else " (кадры и листы персонажей вместе не шлются — "
                                                                 "у модели это разные режимы)"))
    else:
        # Этап расширения платит тому провайдеру, что записан в его запусках (объяснялка: провайдер проекта none —
        # не заглушки и не подмена голоса, а карточка на настоящую картинку и озвучку).
        charged = [i for i in planned if _paid(i["provider"], i["kind"])] if stage in EXTENSIONS else []
        provider, model = charged[0]["provider"] if charged else style["provider"], style["image_model"]
    if stage == "voice" or (planned and all(i["kind"] == "tts" for i in planned)):
        model = "TTS"
    elif stage in EXTENSIONS and any(i["kind"] == "tts" for i in planned):
        model = f"{model} + TTS"
    if stage == "blocking":
        provider, model = "none", "scene"
    if stage == "keyframes" and script_id is not None:
        pid = int(project_id)
        no_block = [s["idx"] for s in V.get_script(store, script_id)["shots"]
                    if not V.selected_ref(store, pid, "shot", int(s["id"]), "blocking_start")]
        if no_block:
            notes.append("нет блокинга у планов " + ", ".join(map(str, no_block)) + " — кадры нарисуются без "
                         "3D-макета композиции; сначала этап blocking (0 ₽)")
        # Лимит референсов модели (nano-banana 10): блокинг + кадр-основа + листы + плита + стиль.
        over = []
        for item in planned:
            try:
                info = build_request(store, {**item, "project_id": pid})[3]
            except Exception:  # noqa: BLE001 - оценка не падает из-за подсказки
                continue
            cut = [m.split(": ", 1)[1] for m in info["missing"] if m.startswith("не влезло")]
            if cut:
                over.append(f"{item['label']}: не влезут {', '.join(cut)}")
        if over:
            notes.append(f"лимит референсов {max_refs(model, provider)} у {model}: " + "; ".join(over[:4]))
        pairs = sum(1 for i in planned if i["ref_kind"] == "endframe")
        if pairs:
            notes.append(f"пары кадров: {pairs} конечных; конец плана = старт сцепленного следующего (один кадр)")
        if tail_mode(store, pid, script_id):
            vmodel = series_video_model(store, pid, script_id)[0]
            notes.append(f"конечные кадры не заказываю: {vmodel} берёт только стартовый кадр — видео к нарисованному "
                         "концу не приходит; старт продолжения — последний кадр готового видео прошлого плана")
    note = (f"{STAGE_LABELS[stage]}: {len(planned)} шт. ≈ {total:.2f} ₽"
            + (f" ({model}, цены от {PRICE_CHECKED}: "
               + ", ".join(sorted({i['price_note'] for i in planned if _paid(i['provider'], i['kind'])})) + ")"
               if paid else (" — сухой прогон, заглушки и тон, 0 ₽" if planned and all(i["provider"] == "none"
                                                                                       for i in planned)
                             else " — бесплатно, 0 ₽")))
    if stage == "video" and paid and any("потолка серии" in p for p in problems):
        problems.append(f"подсказка: серия-черновик ≤ {EPISODE_CAP_RUB['draft']:.0f} ₽ — для видео переключи серию "
                        "в quality (aiv_update_script quality) или возьми дешёвую модель (video_model "
                        "grok-imagine-video-1-5); решает владелец")
    out = {"stage": stage, "stage_label": STAGE_LABELS[stage], "project_id": int(project_id), "script_id": script_id,
           "items": planned, "count": len(planned), "total_rub": total, "paid": paid, "blocked": blocked,
           "notes": notes, "continuity": continuity_flags(store, script_id) if script_id is not None else [],
           "provider": provider, "model": model, "problems": problems, "busy": busy_jobs, "replaces": replaces,
           "budget": budget_view(store, project_id, script_id), "note": note, **extra}
    if stage == "video" and script_id is not None:
        out["video_qa"] = video_qa_flags(store, script_id)
    return out


def _target_key(item: dict) -> tuple[str, int, str, str]:
    return (str(item["target_type"]), int(item["target_id"]), str(item["ref_kind"]), str(item.get("slot") or ""))


def _busy_jobs(store: Any, project_id: int, exclude_cards: Any = ()) -> dict[tuple, dict]:
    """Цель → её занятый запуск (BUSY), кроме запусков карточек ``exclude_cards`` (их заменит новая)."""
    marks = ", ".join("?" for _ in BUSY)
    rows = store.db.query_all(
        f"SELECT id, approval_id, status, target_type, target_id, ref_kind, slot FROM aiv_gen_jobs "
        f"WHERE project_id = ? AND status IN ({marks}) ORDER BY id", (int(project_id), *BUSY))
    skip = {int(x) for x in exclude_cards}
    out: dict[tuple, dict] = {}
    for row in rows:
        if row["approval_id"] is not None and int(row["approval_id"]) in skip:
            continue
        out.setdefault(_target_key(dict(row)), dict(row))
    return out


def _replaceable_cards(store: Any, project_id: int, stage: str, script_id: int | None, group: Any) -> list[int]:
    """Ждущие карточки этого этапа (та же серия и группа) — новая карточка этапа заменяет их."""
    return [int(c["id"]) for c in stage_approvals(store, project_id, script_id, status="pending")
            if c["stage"] == stage and c.get("script_id") == script_id and c.get("group") == group]


def _card_keys(store: Any, approval_id: int) -> list[tuple]:
    return sorted(_target_key(dict(r)) for r in store.db.query_all(
        "SELECT target_type, target_id, ref_kind, slot FROM aiv_gen_jobs WHERE approval_id = ? AND status = 'awaiting'",
        (int(approval_id),)))


def _insert_job(store: Any, project_id: int, item: dict, status: str, approval_id: int | None,
                requested_by: str) -> int:
    params = {"resolution": item.get("resolution"), "aspect": item.get("aspect"), "voice": item.get("voice"),
              "text": item.get("text"), "style": item.get("style"), "regenerate": item.get("regenerate", False),
              "seconds": item.get("seconds"),
              "start": item.get("start"), "mode": item.get("mode"), "group": item.get("group"),
              **({"dialog": item["dialog"], "hero_voice": item.get("hero_voice")} if item.get("dialog") else {})}
    cur = store.db.execute(
        "INSERT INTO aiv_gen_jobs (project_id, script_id, approval_id, stage, kind, target_type, target_id, ref_kind, "
        "slot, label, provider, model, params_json, estimate_rub, status, requested_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (int(project_id), item.get("script_id"), approval_id, item["stage"], item["kind"], item["target_type"],
         int(item["target_id"]), item["ref_kind"], item.get("slot") or "", item["label"][:200], item["provider"],
         item["model"], json.dumps({k: v for k, v in params.items() if v is not None}, ensure_ascii=False),
         float(item["estimate_rub"]), status, str(requested_by or "")[:40]))
    return int(cur.lastrowid)


def _card_question(store: Any, project: dict, est: dict, script: dict | None, spend_reason: str = "") -> str:
    bud = est["budget"]
    ext = EXTENSIONS.get(est["stage"])
    head = ext.card_line(store, project["id"], est) if ext is not None and hasattr(ext, "card_line") else ""
    lines = [head or (f"ИИ-видео «{project['name']}»"
                      + (f", серия {script['episode_no']} «{script['title']}»" if script else "")
                      + f": {est['stage_label']} — {est['count']} шт. ≈ {est['total_rub']:.2f} ₽ "
                      f"({est['model'] if est['stage'] != 'voice' else 'TTS'}).")]
    month = bud["month"]
    lines.append(f"Месяц проекта: потрачено {month['spent_rub']:.0f} из {month['cap_rub']:.0f} ₽"
                 + (f"; серия: {bud['episode']['spent_rub']:.0f} из {bud['episode']['cap_rub']:.0f} ₽"
                    if bud.get("episode") else "") + ".")
    names = [i["label"] for i in est["items"][:8]]
    lines.append("Что: " + "; ".join(names) + (f" и ещё {est['count'] - 8}" if est["count"] > 8 else ""))
    bad = [f for f in est.get("continuity") or [] if not f["ok"] and not f["outdated"]]
    if bad:
        lines.append("⚠ Склейки (Gemini): " + "; ".join(
            f"план {f['idx']} — " + (", ".join(m["detail"] for m in f["mismatches"][:2]) or f"оценка {f['score']}")
            for f in bad[:4]) + ". Можно сначала перерисовать эти кадры (дёшево).")
    if est["stage"] == "video" and est.get("modes"):
        lines.append("Режим: " + ", ".join(VIDEO_MODE_LABELS.get(m, m) for m in est["modes"]) + ".")
    targets = {int(i["target_id"]) for i in est["items"]}
    qa = [f for f in est.get("video_qa") or [] if not f["ok"] and (est["stage"] != "video" or f["shot_id"] in targets)]
    if qa:
        lines.append("⚠ Проверка видео (Gemini): " + "; ".join(
            f"план {f['idx']} — " + ("; ".join(i["detail"] for i in f["issues"][:2]) or f"оценка {f['score']}")
            for f in qa[:4]) + ".")
    if not settings.aivideo_enabled:
        lines.append("⚠ Платная генерация сейчас выключена в настройках сервиса: запустится после её включения.")
    if spend_reason:
        lines.append(f"Спрашиваю, потому что {spend_reason}.")
    lines.append("Разрешить эту сумму?")
    return "\n".join(lines)


def _request_locked(store: Any, project: dict, stage: str, script_id: int | None, items: list | None,
                    regenerate: bool, requested_by: str, note: str, continuity: dict | None) -> dict:
    project_id = int(project["id"])
    est = estimate(store, project_id, stage, script_id, items, regenerate)
    if continuity is not None:
        est["continuity_check"] = continuity
    if est["blocked"]:
        raise V.AivError("этап не запрошен: " + "; ".join(est["blocked"]))
    if not est["items"]:
        if est["busy"]:
            cards = sorted({int(j["approval_id"]) for j in est["busy"] if j.get("approval_id")})
            out = {"ok": True, "status": "in_progress", "estimate": est,
                   "job_ids": [int(j["id"]) for j in est["busy"]],
                   "note": "уже заказано и ждёт решения или идёт — второй раз не заказываю"
                           + (" (карточка #" + ", #".join(map(str, cards)) + ")" if cards else "")}
            if len(cards) == 1:
                out["approval_id"] = cards[0]
            return out
        return {"ok": True, "status": "nothing", "estimate": est,
                "note": "всё уже есть и не устарело — для новой версии укажи items (перегенерация)"}
    if not est["paid"]:
        job_ids = [_insert_job(store, project_id, item, "approved", None, requested_by) for item in est["items"]]
        return {"status": "run", "estimate": est, "job_ids": job_ids}
    hard = [p for p in est["problems"] if "AIVIDEO_ENABLED" not in p]
    if hard:
        raise BudgetError("этап не запрошен: " + "; ".join(hard))
    script = V.get_script(store, script_id, with_shots=False) if script_id is not None else None
    # Группа карточки (расширения: один ролик-объяснялка) — заменяется только карточка той же группы.
    group = est["items"][0].get("group")
    # Тот же запрос второй раз (двойной клик, повтор после ошибки клиента), а ждущая карточка с теми же
    # запусками и суммой уже есть — она и есть ответ: вторую карточку владельцу не шлём.
    want = sorted(_target_key(i) for i in est["items"])
    for old_id in est["replaces"]:
        old = A.get_approval(store, old_id)
        if (_card_keys(store, old_id) == want
                and abs(float((old.get("payload") or {}).get("amount_rub") or 0) - est["total_rub"]) < 0.01):
            return {"ok": True, "status": "awaiting_approval", "approval_id": old_id, "estimate": est,
                    "duplicate": True,
                    "note": f"карточка #{old_id} на {est['total_rub']:.2f} ₽ уже ждёт владельца — вторую не шлю"}
    # Прежняя ждущая карточка того же этапа — устарела: новая заменяет её.
    for old_id in est["replaces"]:
        A._update(store, old_id, status="expired", decision_comment="заменена новой карточкой этапа")
        store.db.execute("UPDATE aiv_gen_jobs SET status = 'cancelled', error = 'карточка заменена' "
                         "WHERE approval_id = ? AND status = 'awaiting'", (old_id,))
    payload = {"action": CARD_ACTION, "stage": stage, "project_id": int(project_id), "script_id": script_id,
               "amount_rub": est["total_rub"], "count": est["count"], "model": est["model"],
               "provider": est["provider"], "note": P._text(note, 300)}
    if group:
        payload["group"] = group
    prefix = getattr(EXTENSIONS[stage], "card_prefix", "Объяснялка") if stage in EXTENSIONS else "ИИ-видео"
    # Настройка проекта: в пределах суммы карточку решает она. Платная генерация выключена — карточка ждёт, как всегда.
    spend = S.spend_decision(store, project_id, est["total_rub"]) if settings.aivideo_enabled else {
        "allowed": False, "asked": False, "reason": "платная генерация выключена"}
    card = A.submit_card(store, project_id, "other",
                         _card_question(store, project, est, script, spend["reason"] if spend["asked"] else ""), payload,
                         title=f"{prefix}: {est['stage_label']} ≈ {est['total_rub']:.0f} ₽",
                         submitted_by=requested_by, notify=not spend["allowed"])
    job_ids = [_insert_job(store, project_id, item, "awaiting", card["id"], requested_by) for item in est["items"]]
    store.db.execute("UPDATE approvals SET payload_json = ? WHERE id = ?",
                     (json.dumps({**payload, "job_ids": job_ids}, ensure_ascii=False), card["id"]))
    if spend["allowed"]:
        A.decide(store, card["id"], "approve", comment=spend["note"], actor=S.SPEND_ACTOR)
        return {"ok": True, "status": "approved", "approval_id": card["id"], "estimate": est,
                "approved_by_setting": True,
                "note": (f"{est['total_rub']:.2f} ₽ без подтверждения: {S.ACTOR_LABELS[S.SPEND_ACTOR]} (карточка "
                         f"#{card['id']}, запись в журнале трат). Запуски идут сами.")}
    why = spend["reason"] or f"настройка «{S.NAME_SPEND}» выключена"
    return {"ok": True, "status": "awaiting_approval", "approval_id": card["id"], "estimate": est,
            "note": (f"карточка #{card['id']} на {est['total_rub']:.2f} ₽ ушла владельцу (UI «Одобрение», страница "
                     f"проекта, Telegram): {why}. Запуск — только после его «Разрешить»; сам не одобряй и не обходи. "
                     f"Настройка меняется здесь: {S.where(project_id)}.")}


def _project_of_target(store: Any, target_type: str, target_id: int) -> int:
    ext = _ext_of_target(target_type)
    if ext is not None:
        return int(ext.project_of(store, target_type, target_id))
    if target_type == "character":
        return int(V.get_character(store, target_id)["project_id"])
    if target_type == "look":
        return int(V.get_character(store, V.get_look(store, target_id)["character_id"])["project_id"])
    if target_type == "location":
        return int(V.get_location(store, target_id)["project_id"])
    if target_type in ("shot", "line"):
        return int(V.get_script(store, V.get_shot(store, target_id)["script_id"], with_shots=False)["project_id"])
    raise V.AivError("для стиля нужен ref_id или project_id")


def _stage_of(store: Any, target_type: str, target_id: int, kind: str) -> tuple[str, int | None]:
    ext = _ext_of_target(target_type)
    if ext is not None:
        return ext.stage, None
    if target_type == "shot":
        stage = "video" if kind == "shot_video" else "blocking" if kind.startswith("blocking") else "keyframes"
        return stage, int(V.get_shot(store, target_id)["script_id"])
    if target_type == "line":
        return "voice", int(V.get_shot(store, target_id)["script_id"])
    return "refs", None


def stage_approvals(store: Any, project_id: int, script_id: int | None = None, status: str | None = None) -> list[dict]:
    """Карточки этапов проекта (последние первыми): сумма, этап, статус, сколько уже потрачено."""
    sql = "SELECT id FROM approvals WHERE project_id = ? AND kind = 'other' AND payload_json LIKE ?"
    params: list[Any] = [int(project_id), f'%"action": "{CARD_ACTION}"%']
    if status:
        sql += " AND status = ?"
        params.append(status)
    rows = store.db.query_all(sql + " ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END, id DESC LIMIT 50", params)
    out = []
    for r in rows:
        card = A.get_approval(store, int(r["id"]))
        payload = card.get("payload") or {}
        if script_id is not None and payload.get("script_id") not in (None, script_id):
            continue
        counts = {row["status"]: int(row["n"]) for row in store.db.query_all(
            "SELECT status, COUNT(*) AS n FROM aiv_gen_jobs WHERE approval_id = ? GROUP BY status", (card["id"],))}
        # Сеть: точно не оплачено — «Повторить» без новой карточки (retry_card).
        retryable = sum(1 for row in store.db.query_all(
            "SELECT params_json FROM aiv_gen_jobs WHERE approval_id = ? AND status IN ('failed', 'uncertain') "
            "AND remote_id = ''", (card["id"],)) if V._loads(row["params_json"], {}).get("retry_free"))
        out.append({"id": card["id"], "status": card["status"], "stage": payload.get("stage"),
                    "group": payload.get("group"),
                    "script_id": payload.get("script_id"), "amount_rub": payload.get("amount_rub"),
                    "count": payload.get("count"), "model": payload.get("model"), "title": card["title"],
                    "question": card["question"], "decided_by": card.get("decided_by"),
                    "decision_comment": card.get("decision_comment"), "created_at": card.get("created_at"),
                    "spent_rub": spent_approval(store, card["id"]), "jobs": counts, "retryable": retryable})
    return out


def decide_card(store: Any, approval_id: int, action: str, comment: str = "", actor: str = "ui") -> dict:
    """Решение владельца по карточке этапа — идемпотентно: повтор того же решения (повтор запроса
    после ошибки клиента, двойной клик) ничего не запускает второй раз и не падает, а отдаёт
    текущее состояние. Противоположное решение по уже решённой карточке — ошибка, как раньше."""
    card = A.get_approval(store, approval_id)
    if (card.get("payload") or {}).get("action") != CARD_ACTION:
        raise V.AivError(f"карточка #{approval_id} — не этап ИИ-видео")
    want = A.DECISION_STATUS.get(action)
    if card["status"] == "pending":
        try:
            return A.decide(store, approval_id, action, comment=comment, actor=actor)
        except ValueError:
            card = A.get_approval(store, approval_id)  # решили параллельно — ниже: то же ли решение
            if card["status"] == "pending":
                raise
    if want and card["status"] == want:
        counts = {row["status"]: int(row["n"]) for row in store.db.query_all(
            "SELECT status, COUNT(*) AS n FROM aiv_gen_jobs WHERE approval_id = ? GROUP BY status", (card["id"],))}
        return {"ok": True, "status": card["status"], "already": True, "approval": card,
                "aiv": {"jobs": counts, "note": f"карточка #{approval_id} уже {card['status']} — повторно ничего не "
                                                "запускаю"}}
    raise ValueError(f"карточка #{approval_id} уже решена: {card['status']}")
