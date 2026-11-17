"""Инструмент ``critique_render`` — независимый критик готового ролика (app/render_critique.py): MCP и чат проекта.

Не в наборе ИИ-монтажёра клипа: смысл критика в том, что он не видит хода монтажа. Монтажёр (агент чата/MCP)
зовёт его на готовый рендер или версию ленты, правит по списку и зовёт снова — второй круг сверяет прошлые пункты.
"""

from __future__ import annotations

from app import render_critique as C
from app.tool_core import _obj, Tool, ToolContext, ToolError


def h_critique_render(ctx: ToolContext, args: dict) -> dict:
    ids = {k: args.get(k) for k in ("clip_id", "render_clip_id", "feed_version_id")}
    if all(v in (None, "") for v in ids.values()) and ctx.clip_plan_id is not None:
        ids["clip_id"] = ctx.clip_plan_id  # чат клипа: его клип
    try:
        target = C.resolve_target(ctx.store, variant_id=args.get("variant_id"), **ids)
        out = C.critique(ctx.store, target, brief=str(args.get("brief") or ""),
                         previous=args.get("previous_report"), fresh=bool(args.get("fresh")))
    except C.CritiqueError as exc:
        raise ToolError(f"critique_render: {exc}") from exc
    except RuntimeError as exc:  # Gemini недоступен, ключ, прокси
        raise ToolError(f"critique_render: критик не ответил — {str(exc)[:300]}") from exc
    report = out["report"]
    hint = ("Правь по P0 и P1 (что оставить — не трогай), перерендерь и позови critique_render снова на новый рендер: "
            "второй круг сверит прошлые пункты. «Сдавать» — не приёмка: решает владелец.")
    if report["verdict"] == "сдавать":
        hint = ("Критик за сдачу. Дальше как обычно: lint_clip, feed_add_version; решение — за владельцем.")
    return {"ok": True, "verdict": report["verdict"], "summary": report["summary"], "issues": report["issues"],
            **({"previous": report["previous"]} if report.get("previous") else {}),
            **({"keep": report["keep"]} if report.get("keep") else {}),
            **({"verdict_note": report["verdict_note"]} if report.get("verdict_note") else {}),
            "text": "\n".join(out["lines"]), "round": out["round"], "critique_id": out["critique_id"],
            "measures": out["measures"], "cost": out["cost"], "saw": out["saw"], "hint": hint}


def tools() -> list[Tool]:
    return [
        Tool(
            "critique_render", "Зову независимого критика",
            "Независимый критик ГОТОВОГО ролика: свежий вызов модели, который видит только сам ролик (лёгкая копия), "
            "задачу (brief — что просил владелец, его словами) и прошлые замечания владельца по проекту (правила, "
            "ответы, вердикты ленты) — НЕ видит плана монтажа и твоих рассуждений, поэтому не пересказывай их в brief. "
            "Ответ: verdict «сдавать» / «ещё проход», issues — «t · где · что не так → что поменять · что оставить» "
            "с P0 (сдавать нельзя) / P1 / P2. Повторный вызов на тот же клип или работу ленты сам берёт прошлый отчёт "
            "и отмечает каждый пункт «исправлено / частично / осталось» (previous_report — свой прошлый отчёт, "
            "fresh=true — без прошлого круга). Цель: clip_id (последний готовый рендер клипа), render_clip_id или "
            "feed_version_id (+ variant_id). Платный вызов Gemini (копия 480p — копейки), траты в общем учёте. "
            "Звать перед сдачей в ленту и после правок по его списку. / Independent critic of a finished render.",
            _obj({"clip_id": {"type": "integer", "description": "клип: берётся его последний готовый рендер"},
                  "render_clip_id": {"type": "integer", "description": "конкретный готовый рендер"},
                  "feed_version_id": {"type": "integer", "description": "версия ленты (feed_list)"},
                  "variant_id": {"type": "integer", "description": "вариант версии (по умолчанию первый с файлом)"},
                  "brief": {"type": "string", "description": "задача ролика словами владельца: что просили"},
                  "previous_report": {"type": "string",
                                      "description": "прошлый отчёт (JSON или строки), если не тот, что сохранён"},
                  "fresh": {"type": "boolean", "description": "без прошлого круга"}}),
            h_critique_render, scope="global", cost=("paid", "network"),
            label=lambda a: "Критик смотрит " + (f"версию ленты {a['feed_version_id']}" if a.get("feed_version_id")
                                                 else f"рендер {a['render_clip_id']}" if a.get("render_clip_id")
                                                 else f"клип {a.get('clip_id') or ''}".strip()),
        ),
    ]
