"""Инструмент агента ``plan_tiktok_parts`` (app/tiktok_parts.py): план частей длинного клипа для TikTok — ≤ 58 с
песни в части, стыки на долях и не посреди боя, «часть N/M» и «Часть N+1 → в профиле». Только план, ничего не
меняет и не рендерит. Входит в общий реестр (``app/agent_tools.py``) — у встроенного монтажёра и в MCP."""

from __future__ import annotations

from typing import Any

from app.tool_core import _num, _obj, Tool, ToolContext, ToolError

EVENT_JOIN_GAP = 0.1  # бой через стык кусков — один бой


def _song_span(store: Any, render: dict, duration: float, args: dict) -> tuple[tuple[float, float], float, list[float], str]:
    """Где в готовом ролике звучит песня: (начало, конец), секунда трека в начале, повторы трека, пояснение."""
    if args.get("song_start") is not None or args.get("song_end") is not None:
        start = _num(args.get("song_start"), 0.0) or 0.0
        end = _num(args.get("song_end"), duration) or duration
        offset = _num(args.get("song_offset"), 0.0) or 0.0
        return (start, min(end, duration)), offset, [], "песня — song_start..song_end из запроса"
    if render.get("music_on") and render.get("track_id"):
        offset = float(render.get("music_start_sec") or 0.0)
        try:
            track = store.get_audio_track(int(render["track_id"]))
        except (KeyError, TypeError, ValueError):
            track = {}
        length = float(track.get("duration_sec") or 0.0)
        title = str(track.get("label") or track.get("original_filename") or f"трек {render['track_id']}")
        if length <= offset:
            return (0.0, duration), offset, [], f"песня — музыка клипа «{title}» на весь ролик (длина трека неизвестна)"
        first = length - offset
        if first >= duration:
            return (0.0, duration), offset, [], f"песня — музыка клипа «{title}» с {offset:g} с трека"
        try:
            from app.render import effective_preset

            loop = bool((effective_preset(store, render.get("preset_id")) or {}).get("music_loop", True))
        except Exception:  # noqa: BLE001 - без пресета — как рендер по умолчанию (повтор)
            loop = True
        if not loop:
            return (0.0, first), offset, [], f"песня — музыка клипа «{title}», кончается на {first:.1f} с ролика"
        breaks, t = [], first
        while t < duration:
            breaks.append(round(t, 3))
            t += length
        return ((0.0, duration), offset, breaks,
                f"песня — музыка клипа «{title}», трек повторяется с начала на {', '.join(f'{b:g}' for b in breaks)} с "
                "(там обязательный стык: в части один непрерывный кусок песни)")
    return (0.0, duration), 0.0, [], ("музыки set_music у клипа нет — считаю, что песня запечена во весь ролик; иначе "
                                      "передай song_start/song_end (секунды готового ролика)")


def _events(ctx: ToolContext, plan: dict, clock: Any, args: dict) -> tuple[list[dict], str]:
    """Бои в секундах готового ролика: из запроса или из кэша game_events записи клипа (по кускам)."""
    raw = args.get("events")
    if raw is not None:
        if not isinstance(raw, list):
            raise ToolError("events — список {start, end} в секундах готового ролика")
        try:
            return [{"start": float(e["start"]), "end": float(e["end"])} for e in raw], "бои — events из запроса"
        except (KeyError, TypeError, ValueError) as exc:
            raise ToolError("events — список {start, end} в секундах готового ролика") from exc
    try:
        from app.game_events_job import cached

        data = cached(ctx.store, int(plan["source_id"]))
    except Exception:  # noqa: BLE001 - нет кэша/файла — без боёв
        data = None
    if not data:
        return [], ("событий боя нет (game_events по записи не запускался) — стыки только по долям; бои можно "
                    "передать events или сначала game_events(source_id)")
    from app.tool_core import clip_timeline

    spans: list[list[float]] = []
    for piece in clip_timeline(ctx.store, ctx.plan_id)["pieces"]:
        if piece.get("reverse"):
            continue
        speed = float(piece.get("speed") or 1.0)
        for fight in data.get("fights") or []:
            a, b = max(float(fight["start"]), piece["source_start"]), min(float(fight["end"]), piece["source_end"])
            if b <= a:
                continue
            ca = piece["clip_start"] + (a - piece["source_start"]) / speed
            cb = piece["clip_start"] + (b - piece["source_start"]) / speed
            fa, fb = clock.clip_to_final(ca), clock.clip_to_final(cb)
            if fb > fa:
                spans.append([fa, fb])
    spans.sort()
    merged: list[list[float]] = []
    for a, b in spans:
        if merged and a <= merged[-1][1] + EVENT_JOIN_GAP:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return ([{"start": round(a, 3), "end": round(b, 3)} for a, b in merged],
            f"бои — game_events записи ({len(merged)} в ролике)")


def h_plan_tiktok_parts(ctx: ToolContext, args: dict) -> dict:
    from app.clip_settings import normalize_render_settings
    from app.cut_tools_beats import _beat_grid
    from app.tiktok_parts import MAX_SONG_SEC, MIN_PART_SEC, plan_parts
    from app.tool_core import plan_clock

    plan = ctx.store.get_clip_plan(ctx.plan_id)
    clock = plan_clock(ctx.store, plan)
    duration = float(clock.final_duration)
    render = normalize_render_settings(plan.get("render_settings") or {})
    grid_args = {k: args[k] for k in ("beats", "bpm", "first_beat", "first_downbeat", "source") if args.get(k) is not None}
    beats, scale, grid = _beat_grid(ctx, {**grid_args, "grid": "beat"}, plan, clock)
    downbeats = None
    if args.get("beats") is None:
        downbeats, _scale, _grid = _beat_grid(ctx, {**grid_args, "grid": "downbeat"}, plan, clock)
    if scale == "clip":
        beats = [clock.clip_to_final(t) for t in beats]
        downbeats = [clock.clip_to_final(t) for t in downbeats or []] or None
    song, offset, breaks, song_note = _song_span(ctx.store, render, duration, args)
    events, events_note = _events(ctx, plan, clock, args)
    parts = args.get("parts")
    try:
        result = plan_parts(duration, beats=beats, downbeats=downbeats, events=events, song=song, song_offset=offset,
                            song_breaks=breaks, max_song_sec=_num(args.get("max_song_sec"), MAX_SONG_SEC) or MAX_SONG_SEC,
                            min_part_sec=_num(args.get("min_part_sec"), MIN_PART_SEC) or MIN_PART_SEC,
                            parts=int(parts) if parts not in (None, "") else None)
    except (TypeError, ValueError) as exc:
        raise ToolError(f"части не спланированы: {exc}") from exc
    result.update(ok=True, grid=grid, song_note=song_note, events_note=events_note,
                  note=("Только план — клип не изменён. Время — секунды ГОТОВОГО ролика (final); layers и end_text — "
                        "секунды части от её начала. Часть = отрезок готового ролика start..end: отдельный клип из "
                        "тех же кусков (create_clip) с подписью «… · N/M» (caption_suffix). Заставку yakoodev на части "
                        "не ставь — только на длинную версию. Крючок в первые 1–2 с части проверь глазами."))
    return result


def tools() -> list[Tool]:
    return [
        Tool(
            "plan_tiktok_parts", "Делю ролик на части для TikTok",
            "План частей длинного клипа (мувик) для TikTok — ТОЛЬКО ПЛАН, ничего не меняет. TikTok глушит ролик "
            "целиком, если в нём больше 60 с одной песни со стримингов (журнал владельца 15.11): каждая часть — один "
            "непрерывный кусок песни ≤ max_song_sec (58), стыки — на долях музыки (лучше начало фразы, потом сильная "
            "доля; прямо перед боем — плюс: часть начнётся с боя) и НИКОГДА посреди боя; число частей — наименьшее "
            "возможное (parts — больше), длины близкие. Сетка долей — как у snap_to_beats: музыка set_music, без неё — "
            "доли звука клипа, или bpm + first_beat (+ first_downbeat), или beats (секунды готового ролика). Песня — "
            "музыка клипа (повтор трека — обязательный стык) или song_start/song_end (+ song_offset — секунда трека). "
            "Бои — events [{start, end}] (секунды готового ролика) или из game_events записи клипа. Ответ: parts "
            "[{index, label «часть N/M», start, end, duration, song {track_start, track_end, sec}, cut {at, kind}, "
            "end_text «Часть N+1 → в профиле», layers — титры части (add_layers в клипе части), caption_suffix "
            "« · N/M»}], notes. / Plan TikTok parts (≤58 s of one song each, cuts on beats, never inside a fight).",
            _obj({"max_song_sec": {"type": "number", "minimum": 10, "maximum": 60,
                                   "description": "песни в части не больше, с (58)"},
                  "min_part_sec": {"type": "number", "minimum": 1, "description": "часть не короче, с (15)"},
                  "parts": {"type": "integer", "minimum": 1, "description": "частей не меньше этого"},
                  "events": {"type": "array", "items": {"type": "object", "properties": {
                      "start": {"type": "number"}, "end": {"type": "number"}}},
                             "description": "бои/события — внутри них стыка нет (секунды готового ролика)"},
                  "beats": {"type": "array", "items": {"type": "number"}},
                  "bpm": {"type": "number"}, "first_beat": {"type": "number"}, "first_downbeat": {"type": "number"},
                  "source": {"type": "string", "enum": ["auto", "music", "audio"]},
                  "song_start": {"type": "number"}, "song_end": {"type": "number"}, "song_offset": {"type": "number"}}),
            h_plan_tiktok_parts,
            label=lambda a: "Делю ролик на части для TikTok" + (f" (≥ {a.get('parts')})" if a.get("parts") else ""),
        ),
    ]
