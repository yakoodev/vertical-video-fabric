"""Инструменты монтажа: вырезы, заморозка, автомонтаж (из ``app/cut_tools.py``)."""

from __future__ import annotations

from typing import Any

from app.tool_core import _clock, _current_render, _num, _save_render, _source_keyed, plan_clock, ToolContext, ToolError

TIME_SCALES_NOTE = {
    "clip": ("секунды клипа ДО вырезов, с наплывами переходов: duration, pieces[].clip_start, слои, камера, "
             "вставки, субтитры, cuts, mute, gain, view_frame, at_word, find_noises, apply_cuts, set_subtitle_words"),
    "final": ("секунды ГОТОВОГО ролика (после вырезов и наплывов): finished_duration, contact_sheet и listen по "
              "умолчанию; before_cuts=true у них — шкала clip, final=true у view_frame/compose_cover — шкала final"),
    "source": "секунды исходника: pieces[].start_sec/end_sec, trim_piece, get_transcript, get_shots, чат",
    "piece": "секунды от начала куска: pieces[].focus, set_focus t",
}


def plain_cuts_to_clip(clock: Any, cuts: list[dict]) -> list[dict]:
    """Вырезы (хранятся «кусками подряд») → шкала clip; через стык с наплывом — две части с piece."""
    out = []
    multi = len(clock.lengths) > 1
    for cut in cuts:
        for part in clock.plain_range_to_clip(float(cut["start"]), float(cut["end"])):
            out.append({**cut, "start": part["start"], "end": part["end"],
                        **({"piece": part["piece"]} if multi else {})})
    return out


def _cuts_view(clock: Any, render: dict, duration: float) -> dict:
    """Вырезы и длина ГОТОВОГО ролика: агент не должен гадать, сколько останется."""
    from app.cuts import normalize_cuts

    cuts = normalize_cuts(render.get("cuts"))
    if not cuts:
        return {"time": "clip", "time_scales": TIME_SCALES_NOTE,
                "time_note": "вырезов нет: шкала clip совпадает с готовым роликом (final)"}
    final = clock.final_duration
    return {
        "time": "clip",
        "time_scales": TIME_SCALES_NOTE,
        "cuts": plain_cuts_to_clip(clock, cuts),
        "finished_duration": final,
        "time_note": (
            f"Есть вырезы: готовый ролик {final} с (duration {duration} — до вырезов). Слои, камера, "
            "вставки, субтитры и сами cuts — шкала clip (ДО вырезов, как duration) и при рендере сдвигаются "
            "сами; то, что целиком внутри выреза, пропадает. Хук с 0 с остаётся в начале. Готовый ролик — "
            "шкала final (contact_sheet, listen)."
        ),
    }


MIN_FINISHED_SEC = 12.0


def short_clip_warning(finished_sec: float) -> str | None:
    """Проверочный прогон Gemini: апекс подрезан до 6 с — голая реакция без завязки."""
    if finished_sec >= MIN_FINISHED_SEC:
        return None
    return (f"Готовый ролик {finished_sec:.1f} с — слишком коротко: нет завязки, зритель не поймёт, что "
            f"случилось. Шортс 15–60 с: верни 5–15 с до момента (trim_piece, cuts), не режь до голой реакции.")


def h_freeze_frame(ctx: ToolContext, args: dict) -> dict:
    """Стоп-кадр посреди ролика (app/freeze.py): кадр в at (шкала final) замирает на duration с."""
    from app.freeze import finished_with_freezes, freeze_problem, normalize_freezes

    render = _current_render(ctx)
    current = normalize_freezes(render.get("freezes"))
    clock = _clock(ctx)
    if args.get("reset"):
        fresh = _save_render(ctx, {**render, "freezes": []})
        return {"ok": True, "freezes": fresh.get("freezes") or [], "finished_duration": clock.final_duration}
    at = _num(args.get("at"))
    if args.get("remove"):
        if at is None:
            raise ToolError("remove: укажи at стоп-кадра (или reset: true — снять все)")
        kept = [f for f in current if abs(f["at"] - at) > 0.05]
        if len(kept) == len(current):
            raise ToolError(f"стоп-кадра в {at:g} с нет; есть: {[f['at'] for f in current]}")
        fresh = _save_render(ctx, {**render, "freezes": kept})
        return {"ok": True, "freezes": fresh.get("freezes") or []}
    raw = {"at": at, "duration": _num(args.get("duration"), 1.0), "audio": args.get("audio") or "silence",
           "layers": args.get("layers") or "freeze"}
    problem = freeze_problem(raw, clock.final_duration)
    if problem:
        raise ToolError(f"freeze_frame: {problem}")
    others = [f for f in current if abs(f["at"] - float(at)) > 0.05]
    wanted = normalize_freezes([*others, raw])
    if not any(abs(f["at"] - float(at)) <= 0.001 for f in wanted):
        raise ToolError("freeze_frame: на это место уже приходится другой стоп-кадр — "
                        f"есть {[{'at': f['at'], 'duration': f['duration']} for f in others]}")
    fresh = _save_render(ctx, {**render, "freezes": wanted})
    freezes = fresh.get("freezes") or []
    total = finished_with_freezes(clock.final_duration, freezes)
    insert = raw["audio"] == "silence"
    return {
        "ok": True, "freezes": freezes, "time": "final",
        "finished_duration": clock.final_duration, "finished_with_freezes": total,
        "note": (("кадр стоит, звук исходника молчит, музыка играет; ролик длиннее на "
                  f"{float(raw['duration']):g} с, всё после {float(at):g} с сдвинется" if insert else
                  "кадр стоит, звук идёт дальше; длина ролика та же")
                 + ("; слои и камера стоят с кадром" if raw["layers"] == "freeze" else "; слои анимируются дальше")
                 + f". Проверь view_frame(t, final: true) — t в готовом ролике со стоп-кадрами "
                   f"({total:g} с)"),
    }


# ---------------------------------------------------------------- монтаж «кнопками»


def h_suggest_cuts(ctx: ToolContext, args: dict) -> dict:
    from app.cuts import suggest_for_plan

    mode = args.get("mode") if args.get("mode") in ("pauses", "fillers", "both") else "both"
    # Явная проверка на None: keep_pad=0 («тишину у слов не оставлять») — законное значение.
    min_pause = _num(args.get("min_pause"))
    keep_pad = _num(args.get("keep_pad"))
    out = suggest_for_plan(ctx.store, ctx.plan_id, mode, 0.45 if min_pause is None else float(min_pause),
                           0.12 if keep_pad is None else float(keep_pad))
    # Подсказки считаются по словам «кусками подряд» — агенту отдаём шкалу clip (как apply_cuts
    # и get_clip): переданные как есть в apply_cuts, они встанут ровно туда же.
    clock = _clock(ctx)
    return {**out, "cuts": plain_cuts_to_clip(clock, out.get("cuts") or []),
            "items": plain_cuts_to_clip(clock, out.get("items") or []), "time": "clip",
            "before_sec": clock.clip_duration,
            "after_sec": round(max(0.0, clock.clip_duration - float(out.get("saved_sec") or 0)), 3),
            "note": "шкала clip (до вырезов, как слои и субтитры get_clip); передай cuts в apply_cuts как есть"}


def clip_cuts_to_plain(clock: Any, raw: Any) -> Any:
    """Вырезы агента (шкала clip) → «куски подряд», как их хранит рендер. ``piece`` у выреза —
    чей он в зоне наплыва (get_clip и suggest_cuts его дают)."""
    if not isinstance(raw, list) or not any(clock.pre.eaten):
        return raw  # без наплывов шкалы совпадают
    out = []
    for cut in raw:
        if not isinstance(cut, dict):
            out.append(cut)
            continue
        a, b = _num(cut.get("start")), _num(cut.get("end"))
        if a is None or b is None:
            out.append(cut)
            continue
        piece = cut.get("piece")
        piece = int(piece) if isinstance(piece, (int, float)) and not isinstance(piece, bool) else None
        start, end = clock.clip_range_to_plain(a, b, piece)
        out.append({"start": start, "end": end})
    return out


def _plain_span_to_clip(clock: Any, start: float, end: float) -> tuple[float, float]:
    parts = clock.plain_range_to_clip(start, end)
    return (parts[0]["start"], parts[-1]["end"]) if parts else (start, end)


def h_apply_cuts(ctx: ToolContext, args: dict) -> dict:
    from app.cuts import MIN_ISLAND_SEC, check_cuts_for_plan, cut_warnings, cuts_total, merge_short_islands, plan_duration

    plan = ctx.store.get_clip_plan(ctx.plan_id)
    clock = plan_clock(ctx.store, plan)
    raw = clip_cuts_to_plain(clock, args.get("cuts") or [])
    cuts, problems = check_cuts_for_plan(plan, raw)
    if problems:
        raise ToolError("вырезы не приняты", problems)
    min_island = _num(args.get("min_island_sec"))
    min_island = MIN_ISLAND_SEC if min_island is None else float(min_island)
    if min_island < 0:
        raise ToolError("min_island_sec — секунды, ≥ 0 (0 — не проверять обрывки)")
    words = (ctx.store.get_clip_plan_subtitles(ctx.plan_id) or {}).get("words") or []
    segments, total = plan.get("segments") or [], plan_duration(plan)
    merged: list[dict] = []
    if args.get("merge_short_islands") is True:
        wider, merged = merge_short_islands(cuts, words, segments, total, min_island)
        cuts, problems = check_cuts_for_plan(plan, wider)
        if problems:
            raise ToolError("вырезы не приняты", problems)
    ctx.before_change()
    settings = dict(_current_render(ctx))
    settings["cuts"] = cuts
    ctx.store.set_clip_plan_render_settings(ctx.plan_id, settings)
    out: dict = {"cuts": len(cuts), "saved_sec": round(cuts_total(cuts), 2), "time": "clip",
                 "finished_duration": _clock(ctx).final_duration}
    if merged:
        out["merged_islands"] = [{"start": a, "end": b, "text": i["text"]}
                                 for i in merged for a, b in [_plain_span_to_clip(clock, i["start"], i["end"])]]
    warnings = cut_warnings(cuts, words, segments, total, lambda a, b: _plain_span_to_clip(clock, a, b), min_island)
    if cuts and _source_keyed(ctx):
        # Владелец: «на 4-5 секунде персонаж моргает на склейке» — втубер стоит
        # неподвижно, и вырезанная пауза видна как рывок головы.
        warnings.append("Говорящая голова на зелёнке: вырез посреди фразы виден как рывок персонажа. "
                        "Режь только длинные паузы между фразами и прячь стык (смена плана за персонажем, "
                        "наезд камеры); короткие паузы лучше оставить.")
    if warnings:
        out["warnings"] = warnings
    return out


def h_auto_edit(ctx: ToolContext, args: dict) -> dict:
    from app.auto_edit import apply_auto_edit

    ctx.before_change()
    return apply_auto_edit(ctx.store, ctx.plan_id, style=str(args.get("style") or "hormozi"),
                           keep_layers=args.get("keep_layers", True) is not False, playbook=ctx.playbook)


def h_make_compilation(ctx: ToolContext, args: dict) -> dict:
    from app.compilation import build_compilation

    from app.compilation import _unique_ids

    try:
        ids = _unique_ids(args.get("clip_ids") or [])
    except ValueError as exc:
        raise ToolError(str(exc)) from None
    if len(ids) < 2:
        raise ToolError("нужно хотя бы два разных clip_ids")
    try:
        first = ctx.store.get_clip_plan(ids[0])
    except KeyError:
        raise ToolError(f"клип {ids[0]} не найден") from None
    return build_compilation(ctx.store, first["source_id"], ids, title=str(args.get("title") or ""),
                             countdown=args.get("countdown", True) is not False)
