"""MCP / ИИ-монтажёр: ``game_events`` — события геймплея источника (app/game_events.py, кэш и фон —
app/game_events_job.py)."""

from __future__ import annotations

from typing import Any


def h_game_events(ctx: Any, args: dict) -> dict:
    from app.game_events import KINDS
    from app.game_events_job import game_events_job, window
    from app.tool_core import ToolError, _num, _source_or_clip_source

    source = _source_or_clip_source(ctx, args)
    kinds = args.get("kinds") or None
    if isinstance(kinds, str):
        kinds = [k.strip() for k in kinds.split(",") if k.strip()]
    bad = [k for k in kinds or [] if k not in KINDS]
    if bad:
        raise ToolError(f"неизвестные виды {bad}; есть: {', '.join(KINDS)}")
    try:
        out = game_events_job(ctx.store, source, refresh=bool(args.get("refresh")))
    except (KeyError, ValueError) as exc:
        raise ToolError(str(exc)) from exc
    if out.get("status") == "running":
        return {"project_id": source, **out}
    res = window(out, _num(args.get("from_sec")), _num(args.get("to_sec")), kinds, _num(args.get("min_score"), 0.0) or 0.0)
    res["note"] = ("t — секунды исходника. knock — плашка «НОКАУТ», kill — игрок добил (лента убийств), death — игрок "
                   "сбит/убит, squad_wipe — отряд уничтожен, win — «ВЫ ЧЕМПИОН», shot_burst — очередь выстрелов по звуку, "
                   "reaction — громкий голос в микрофон, ult — засвет кадра. fights — перестрелки целиком "
                   "(start…end): резать бой оттуда, реакцию-мем ставить на knock/kill/death/reaction.")
    return res


def tools() -> list:
    from app.tool_core import SOURCE_ARGS, Tool, _obj

    return [
        Tool(
            "game_events", "События геймплея",
            "События в записи игры (пока Apex Legends) source_id: нокауты (плашка «НОКАУТ … +150»), убийства игрока "
            "(лента убийств), смерть игрока (сбит / режим зрителя), уничтожение отряда, победа «ВЫ ЧЕМПИОН», новый и "
            "макс. уровень, очереди выстрелов (звук игры), реакции в микрофон (крик, смех?), засветы кадра — "
            "[{t, kind, score 0..1, detail}] в секундах исходника — и перестрелки fights [{start, end, score}]. "
            "Куда резать мем-монтаж: бой целиком — fights, мем-реакцию — на knock/kill/death/reaction. Первый вызов "
            "сканирует запись ФОНОМ (~6 с на минуту видео): не готово — status \"running\" и retry_after_sec, зови "
            "тот же вызов ещё раз. Потом кэш. kinds — только эти виды; from_sec/to_sec — окно; min_score — порог. "
            "/ Gameplay events (knocks, kills, deaths, wins, gunfire, mic reactions) + fight spans.",
            _obj({**SOURCE_ARGS, "from_sec": {"type": "number"}, "to_sec": {"type": "number"},
                  "kinds": {"type": "array", "items": {"type": "string"},
                            "description": "knock, kill, death, squad_wipe, win, level_up, max_level, shot_burst, "
                                           "reaction, ult"},
                  "min_score": {"type": "number"},
                  "refresh": {"type": "boolean", "description": "сканировать заново, мимо кэша"}}),
            h_game_events, scope="global", cost=("cpu",),
            label=lambda a: "Ищу события геймплея",
        ),
    ]
