"""Инструмент агента «Главной» (app/home.py): та же сводка, что видит владелец на первом экране, — с чего начать день."""

from __future__ import annotations

from app import home
from app.tool_core import _obj, Tool, ToolContext, ToolError


def h_home_summary(ctx: ToolContext, args: dict) -> dict:
    blocks = args.get("blocks") or []
    if not isinstance(blocks, list):
        raise ToolError("blocks — список имён блоков")
    names = [name for name, _fn in home.BLOCKS]
    unknown = [b for b in blocks if b not in names]
    if unknown:
        raise ToolError(f"нет блоков: {', '.join(map(str, unknown))} (есть: {', '.join(names)})")
    out = home.summary(ctx.store, blocks)
    out["page"] = "/"
    return out


def tools() -> list[Tool]:
    names = [name for name, _fn in home.BLOCKS]
    return [
        Tool(
            "home_summary", "Смотрю сводку сервиса",
            "Сводка «Главной» — что видит владелец на первом экране: waiting (ждёт его: «готово, не выложено» и «на "
            "проверке» в ленте, карточки одобрения, открытые вопросы, подтверждения в чате, упавшие публикации и "
            "рендеры за 3 дня), recent_works (свежие работы ленты), upcoming (ближайшие публикации по расписанию, "
            "UTC), top_week (что зашло за 7 дней по просмотрам), working (тяжёлая очередь и задания), spend (траты "
            "месяца: Gemini и Polza против потолка), projects (последнее событие и ближайший выход), examples (что "
            "сервис умеет, с примерами). blocks — только эти блоки. Упавший блок приходит с error, остальные на месте. "
            "/ Service home summary.",
            _obj({"blocks": {"type": "array", "items": {"type": "string", "enum": names}}}),
            h_home_summary, scope="global",
            label=lambda a: "Смотрю сводку сервиса",
        ),
    ]
