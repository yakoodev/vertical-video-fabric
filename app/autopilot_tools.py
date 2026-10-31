"""MCP-инструменты автопилота канала (app/autopilot.py): одобрение, расписание, предложение правил.

Как и app/project_tools.py: ``project_id`` — проект-серия (list_channel_projects), можно
``source_id``. ``submit_for_approval`` — клиповый (``clip_id`` — клип из list_clips).
Решать карточки агент не может — это делает владелец (UI «Одобрение» или Telegram).
Регистрируются вместе с проектными (``agent_tools._project_tools``) — только для MCP и чата проекта.
"""

from __future__ import annotations

from app import autopilot as A
from app.agent_tools import Tool, ToolContext, _obj
from app.project_tools import _PROJECT_ARGS, _pid

_KEYS = ("id", "kind", "status", "title", "question", "content_type", "trust", "platforms", "tags",
         "proposed_local", "scheduled_local", "publish_status", "publish_error", "decision_comment", "decided_by",
         "decided_at", "clip_plan_id", "clip_id", "rule_id", "hypothesis_id", "created_at")


def _brief(approval: dict) -> dict:
    return {k: approval.get(k) for k in _KEYS if approval.get(k) not in (None, "", [])}


def h_submit_for_approval(ctx: ToolContext, args: dict) -> dict:
    approval = A.submit_clip(ctx.store, ctx.plan_id, title=str(args.get("title") or ""),
                             description=str(args.get("description") or ""), tags=args.get("tags"),
                             platforms=args.get("platforms"), when=args.get("when"),
                             content_type=args.get("content_type") or "", submitted_by="agent")
    out = {"ok": True, "approval": _brief(approval)}
    if approval["status"] == "approved":
        out["note"] = ("Доверие проекта auto: без одобрения, сразу в расписание (запись в ленте). "
                       f"{approval['scheduled_local'] or approval['publish_error']}")
    else:
        out["note"] = ("Ждёт владельца (UI «Одобрение» / Telegram). НЕ публикуй сам. Проверяй list_approvals: "
                       "changes_requested — переделай по decision_comment и отправь заново.")
    return out


def h_list_approvals(ctx: ToolContext, args: dict) -> dict:
    project_id = None
    if any(args.get(k) not in (None, "") for k in ("project_id", "source_id")):
        project_id = _pid(ctx, args)
    items = A.list_approvals(ctx.store, project_id, status=args.get("status") or None, kind=args.get("kind") or None,
                             limit=int(args.get("limit") or 50))
    return {"project_id": project_id, "approvals": [_brief(a) for a in items], "counts": A.approval_counts(ctx.store)}


def h_get_schedule(ctx: ToolContext, args: dict) -> dict:
    return A.get_schedule(ctx.store, _pid(ctx, args))


def h_propose_rule(ctx: ToolContext, args: dict) -> dict:
    rule_id = args.get("rule_id")
    card = A.propose_rule(ctx.store, _pid(ctx, args), str(args.get("text") or ""), kind=str(args.get("kind") or "do"),
                          quote=str(args.get("quote") or ""), action=str(args.get("action") or "add"),
                          rule_id=int(rule_id) if rule_id not in (None, "") else None, origin="agent",
                          said_at=args.get("said_at"))
    return {"ok": True, "approval": _brief(card),
            "note": "Карточка владельцу; правило запишется только после его «да» (с его цитатой)."}


def autopilot_tools() -> list[Tool]:
    return [
        Tool(
            "submit_for_approval", "Отправляю на одобрение",
            "Клип (готовый рендер) — на одобрение владельцу: title, description, tags [без #], platforms "
            "[youtube|tiktok|instagram] (пусто — площадки проекта), when — «ГГГГ-ММ-ДД ЧЧ:ММ» пояса проекта (пусто — "
            "ближайший свободный слот), content_type (meme, ad… — для доверия; пусто — по тегам клипа). Без "
            "approved НИЧЕГО не публикуется; доверие проекта auto для типа — сразу в расписание. Прежняя "
            "pending-карточка клипа заменяется. / Submit a rendered clip for owner approval.",
            _obj({"title": {"type": "string"}, "description": {"type": "string"},
                  "tags": {"type": "array", "items": {"type": "string"}},
                  "platforms": {"type": "array", "items": {"type": "string", "enum": list(A.PLATFORMS)}},
                  "when": {"type": "string"}, "content_type": {"type": "string"}}, ["title"]),
            h_submit_for_approval, mutating=True, scope="clip",
        ),
        Tool(
            "list_approvals", "Очередь одобрения",
            "Очередь одобрения: status pending|approved|rejected|changes_requested|expired (через запятую; пусто — "
            "все), kind clip_publish|rule_proposal|hypothesis_review|other. decision_comment — что сказал "
            "владелец; changes_requested — переделай и отправь заново. Без project_id/source_id — все проекты. "
            "/ Approval queue.",
            _obj({**_PROJECT_ARGS, "status": {"type": "string"}, "kind": {"type": "string", "enum": list(A.KINDS)},
                  "limit": {"type": "integer"}}),
            h_list_approvals, scope="project",
        ),
        Tool(
            "get_schedule", "Расписание публикаций",
            "Расписание проекта: пояс, слоты (дни 0=пн…6=вс, часы), лимит в день на площадку, ближайшие "
            "публикации (scheduled_local — время проекта) и свободные слоты. / Project publishing schedule.",
            _obj(dict(_PROJECT_ARGS)), h_get_schedule, scope="project",
        ),
        Tool(
            "propose_rule", "Предлагаю правило",
            "Спросить владельца карточкой: записать правило (action add: text, kind do|dont|preference, quote — "
            "его ДОСЛОВНЫЕ слова, если были) или отменить (action revoke, rule_id). Запишется только после "
            "его «да». Свои догадки — так, а не add_rule. / Ask the owner to confirm a rule.",
            _obj({**_PROJECT_ARGS, "text": {"type": "string"}, "kind": {"type": "string", "enum": ["do", "dont",
                                                                                                   "preference"]},
                  "quote": {"type": "string"}, "action": {"type": "string", "enum": ["add", "revoke"]},
                  "rule_id": {"type": "integer"}, "said_at": {"type": "string"}}),
            h_propose_rule, mutating=True, scope="project",
        ),
    ]


_TOOLS: list[Tool] | None = None


def tools() -> list[Tool]:
    global _TOOLS
    if _TOOLS is None:
        _TOOLS = autopilot_tools()
    return _TOOLS
