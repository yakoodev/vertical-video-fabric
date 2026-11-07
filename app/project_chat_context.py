"""Чат проекта: контекст для модели и сводка нити (из ``app/project_chat.py``)."""

from __future__ import annotations

import json
import logging
from typing import Any

from app import agent_settings as S
from app import autopilot as A
from app import project_knowledge as K
from app import projects as P
from app.settings import settings
from app.project_chat_threads import (
    default_thread, get_thread, legacy_clip_messages, thread_messages, update_thread, _video_title,
)

_log = logging.getLogger("app.project_chat")

HISTORY_KEEP = 12  # последних реплик — модели целиком
SUMMARIZE_AFTER = 30  # больше несжатых реплик — старые уходят в резюме
SUMMARY_CHARS = 3000
CONTEXT_CHARS = 24000
CONTEXT_SOURCES = 40  # у Apex 218 видео: их список не должен вытеснять лор из обрезки


def error_hint(exc: Exception) -> str:
    """Честная причина сбоя Gemini и что сделать владельцу."""
    text = str(exc)
    low = text.lower()
    if "кредиты" in low or "billing" in low:
        return text
    if "location is not supported" in low:
        return ("Gemini недоступен из этой страны. Включи VPN на сервере или задай GEMINI_PROXY_URL "
                "в .env сервиса.")
    if "auth failed" in low or "api key" in low:
        return "Gemini не принял ключ: проверь GEMINI_API_KEY в .env сервиса."
    if "network" in low or "timed out" in low or "timeout" in low:
        return "Gemini не ответил по сети. Попробуй ещё раз через минуту; если повторяется — проверь VPN/прокси."
    if "429" in low or "overloaded" in low or "high demand" in low or "503" in low:
        return "Gemini перегружен. Попробуй ещё раз через минуту или выбери другую модель в шапке чата."
    return f"Ошибка Gemini: {text[:300]}"


def video_context(store: Any, source_id: int) -> dict:
    """Какое это видео: для чата видео, в каждом ходе. Ответы владельца — только про него."""
    from app.stream_questions import answers_brief

    source = store.get_source(int(source_id))
    plans = store.db.query_one("SELECT COUNT(*) AS n FROM clip_plans WHERE source_id = ? AND hidden = 0", (int(source_id),))
    return {"source_id": int(source_id), "title": _video_title(store, source_id), "status": source.get("status"),
            "duration_sec": source.get("duration_sec"), "size": [source.get("width"), source.get("height")],
            "original_url": source.get("original_url") or "", "has_transcript": bool(source.get("has_transcript")),
            "moments_and_clips": int(plans["n"]), "owner_answers": answers_brief(store, int(source_id))}


def project_context(store: Any, project_id: int, source_id: int | None = None) -> str:
    """Свежий срез проекта в каждый ход: бриф, правила, статистика, гипотезы, одобрение, расписание.
    Текст обрезается по длине, поэтому сначала идёт то, что агент знает, а длинные списки — в конце."""
    brief = P.project_brief(store, project_id, covered_limit=30)
    project = brief.pop("project")
    sources = project.get("sources") or []
    project = {**project, "sources": sources[:CONTEXT_SOURCES], "sources_total": len(sources)}
    stats = P.project_stats(store, project_id, top=3)
    stats.pop("items", None)
    approvals = [{k: a.get(k) for k in ("id", "kind", "status", "title", "question", "decision_comment",
                                          "scheduled_local", "publish_status")}
                 for a in A.list_approvals(store, project_id, status="pending,changes_requested", limit=20)]
    schedule = A.get_schedule(store, project_id)
    cfg = A.get_config(store, project_id)
    mascots = K.project_mascots(store, int(project_id))
    data = {
        "project_id": int(project_id),
        "agent_settings": S.agent_brief(store, int(project_id)),
        **({"video": video_context(store, int(source_id))} if source_id else {}),
        **({"mascots": mascots} if mascots else {}),
        "brief": brief, "stats": stats, "approvals_open": approvals,
        "schedule": {"timezone": schedule["timezone"], "upcoming": [
            {k: u.get(k) for k in ("id", "title", "scheduled_local", "publish_status", "platforms")}
            for u in schedule["upcoming"][:10]]},
        "autopilot": {"always_ask_types": [k for k, v in cfg["trust"].items() if k != "default" and v == "approve"],
                      "slots": cfg["slots"], "per_day": cfg["per_day"], "auto_rules": cfg["auto_rules"]},
        "project": project,
    }
    text = json.dumps(data, ensure_ascii=False, default=str)
    return text if len(text) <= CONTEXT_CHARS else text[:CONTEXT_CHARS] + "…(обрезано)"


def general_context(store: Any) -> str:
    projects = []
    for p in P.list_projects(store):
        mascots = [{k: m[k] for k in ("mascot_id", "name", "about")} for m in K.project_mascots(store, p["id"])]
        projects.append({"project_id": p["id"], "name": p["name"], "kind": p.get("kind"),
                         "rules": p.get("active_rules_count"), **({"mascots": mascots} if mascots else {})})
    sources = [{"source_id": s["id"], "name": s.get("original_filename") or "", "status": s.get("status"),
                "duration_sec": s.get("duration_sec"), "clips": s.get("clip_plans_count"),
                "channel_project_id": s.get("project_id")} for s in store.list_sources(limit=40)]
    counts = store.count_sources()
    # Число — отдельно от списка: иначе на «сколько у меня источников» модель считает 40 строк.
    text = json.dumps({"channel_projects": projects, "channel_projects_total": len(projects),
                       "sources_total": counts["total"], "sources_by_status": counts["by_status"],
                       "sources_note": f"ниже последние {len(sources)} из {counts['total']} источников; "
                                       "остальные — list_projects(offset=…)",
                       "sources": sources}, ensure_ascii=False, default=str)
    return text if len(text) <= CONTEXT_CHARS else text[:CONTEXT_CHARS] + "…(обрезано)"


def _heuristic_summary(old_summary: str, messages: list[dict]) -> str:
    lines = [old_summary] if old_summary else []
    for m in messages:
        who = "Владелец" if m["role"] == "user" else "Агент"
        text = " ".join(str(m.get("text") or "").split())
        if text:
            lines.append(f"{who}: {text[:160]}")
    out = "\n".join(lines)
    return out[-SUMMARY_CHARS:]


def summarize_thread(store: Any, thread_id: int, client: Any = None) -> bool:
    """Старые реплики → резюме (Gemini, если есть; иначе выжимка строк). True — сжали."""
    thread = get_thread(store, thread_id)
    upto = int(thread.get("summary_upto") or 0)
    fresh = [m for m in thread_messages(store, thread_id, after=upto, limit=1000) if m["status"] != "running"]
    if len(fresh) <= SUMMARIZE_AFTER:
        return False
    old = fresh[:-HISTORY_KEEP]
    # Старая переписка клипа в резюме идёт один раз, вместе с первым сжатием. Её строки остаются на месте.
    earlier = legacy_clip_messages(store, thread["clip_plan_id"]) if thread.get("clip_plan_id") and not upto else []
    summary = ""
    if client is not None:
        prompt = ("Сожми переписку владельца с агентом в 5–12 пунктов: решения, договорённости, "
                  "просьбы владельца, открытые вопросы, id клипов и источников. По-русски, без воды.\n\n"
                  f"Прежнее резюме:\n{thread.get('summary') or '—'}\n\nНовые реплики:\n" + _heuristic_summary("", earlier + old))
        try:
            from app.ai.publish_meta import _extract_response_text

            response = client.generate_content(settings.gemini_text_model,
                                               {"contents": [{"role": "user", "parts": [{"text": prompt}]}]})
            summary = _extract_response_text(response).strip()[:SUMMARY_CHARS]
        except Exception as exc:  # noqa: BLE001 - не сжалось моделью — сожмём строками
            _log.info("chat summary via model failed: %s", exc)
    summary = summary or _heuristic_summary(thread.get("summary") or "", earlier + old)
    update_thread(store, thread_id, summary=summary, summary_upto=int(old[-1]["id"]))
    if thread["is_default"] and thread["project_id"]:
        A.set_state(store, thread["project_id"], chat_summary=summary, chat_summary_upto=int(old[-1]["id"]))
    return True


def model_history(store: Any, thread: dict) -> list[dict]:
    """Что модель получает из прошлой переписки: несжатые реплики; у чата клипа до первого сжатия — и старые из
    clip_agent_messages (монтажёр всегда видел последние реплики клипа, сколько бы их ни было в новой таблице)."""
    upto = int(thread.get("summary_upto") or 0)
    fresh = [m for m in thread_messages(store, thread["id"], after=upto) if m["status"] != "running"]
    if not thread.get("clip_plan_id"):
        return fresh[-HISTORY_KEEP:]
    if upto:
        return fresh
    return [*legacy_clip_messages(store, thread["clip_plan_id"]), *fresh]


def maybe_summarize(store: Any, project_id: int, client: Any = None) -> bool:
    return summarize_thread(store, default_thread(store, project_id)["id"], client)
