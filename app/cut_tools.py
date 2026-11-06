"""Инструменты кусков и вырезов: джамп-каты, автомонтаж, подрезка, скорость и цвет куска, доли, переходы, стоп-кадр,
вставки поверх кадра, компиляция.

Входят в общий реестр (``app/agent_tools.py``)."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from app.asset_tools import people_gate
from app.layer_lint import FULL_INSERT_MAX_SEC
from app.layer_tools import _AT_WORD_SCHEMA
from app.subtitle_tools import word_at
from app.tool_core import (
    _apply_piece, _camera_of, _clock, _current_render, _foreign_asset_problem, _layers_of, _num, _obj, _piece_index,
    _save_render, _shared_warning, _source_keyed, clip_timeline, plan_clock, Tool, ToolContext, ToolError,
)


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


INSERT_KIND_WORDS = {"image": "картинка", "gif": "гифка", "video": "видео", "audio": "звук"}


def insert_facts(ctx: ToolContext) -> list[dict]:
    """Вставки клипа по факту — для честного отчёта (QA 01.11: агент назвал гифкой статичную картинку):
    слои image/video и вставки add_insert с типом файла библиотеки. ``[{what, kind, at, end, id}]``,
    время — шкала clip."""
    render = _current_render(ctx)

    def asset_of(asset_id: Any) -> dict:
        try:
            return ctx.store.get_montage_asset(int(asset_id))
        except (KeyError, TypeError, ValueError):
            return {}

    def asset_kind(asset_id: Any) -> str | None:
        return asset_of(asset_id).get("kind")

    def people(asset_id: Any) -> dict:
        """Реальный человек на файле по кэшу проверки (app/ai/people_check.py) — поле факта для отчёта."""
        check = asset_of(asset_id).get("people_check") if asset_id is not None else None
        if isinstance(check, dict) and check.get("real_person") is True:
            return {"real_person": str(check.get("what") or "реальный человек")}
        return {}

    out = []
    for layer in _layers_of(render):
        if layer.get("hidden") or layer.get("type") not in ("image", "video"):
            continue
        props = layer.get("props") if isinstance(layer.get("props"), dict) else {}
        src = str(props.get("src") or "")
        if src.startswith("emoji:"):
            continue
        kind = "video" if layer.get("type") == "video" else (asset_kind(props.get("asset_id")) or (
            "gif" if src.lower().split("?")[0].endswith(".gif") else "image"))
        out.append({"what": f"слой {layer.get('type')} {layer.get('id')}", "kind": kind,
                    "at": round(_num(layer.get("start"), 0.0) or 0.0, 2), "end": round(_num(layer.get("end"), 0.0) or 0.0, 2),
                    "id": str(layer.get("id") or ""), **people(props.get("asset_id"))})
    for index, ins in enumerate(render.get("inserts") or []):
        kind = asset_kind(ins.get("asset_id")) or "image"
        at = _num(ins.get("at"), 0.0) or 0.0
        out.append({"what": f"вставка {ins.get('mode') or 'pip'} #{index}", "kind": kind, "at": round(at, 2),
                    "end": round(at + (_num(ins.get("duration"), 1.5) or 1.5), 2), "id": f"insert#{index}",
                    **people(ins.get("asset_id"))})
    out.sort(key=lambda f: f["at"])
    return out


def describe_inserts(facts: list[dict]) -> str:
    return "; ".join(f"{INSERT_KIND_WORDS.get(f['kind'], f['kind'])} {f['at']:g}–{f['end']:g} с ({f['what']})"
                     for f in facts)


def h_set_end_hold(ctx: ToolContext, args: dict) -> dict:
    """Стоп-кадр в конце ролика на клип: 0 — мем обрывается на последнем кадре, null — как у сервиса."""
    from app.clip_settings import END_HOLD_MAX_SEC, normalize_end_hold
    from app.settings import settings

    raw = None if args.get("reset") else args.get("sec", args.get("end_hold_sec"))
    value = normalize_end_hold(raw)
    if raw not in (None, "") and value is None:
        raise ToolError(f"sec — число 0..{END_HOLD_MAX_SEC:g} или null (как у сервиса)")
    fresh = _save_render(ctx, {**_current_render(ctx), "end_hold_sec": value})
    default = float(settings.render_end_hold_sec or 0)
    effective = fresh["end_hold_sec"] if fresh["end_hold_sec"] is not None else default
    return {"ok": True, "end_hold_sec": fresh["end_hold_sec"], "effective_sec": effective,
            "note": ("готовый ролик кончится ровно на последнем кадре" if effective == 0 else
                     f"последний кадр стоит {effective:g} с, пока затухает звук; музыка доигрывает на нём")}


def _color_entries(ctx: ToolContext) -> tuple[dict, list[dict]]:
    from app.piece_color import normalize_color_fix

    render = _current_render(ctx)
    return render, normalize_color_fix(render.get("color_fix"))


def h_set_piece_color(ctx: ToolContext, args: dict) -> dict:
    """Цветокоррекция куска основы (app/piece_color.py): экспозиция, контраст, насыщенность, баланс."""
    from app.piece_color import FIELDS, describe, normalize_fix

    plan = ctx.store.get_clip_plan(ctx.plan_id)
    segments = plan.get("segments") or []
    index = _piece_index(args, len(segments))
    seg = segments[index]
    render, entries = _color_entries(ctx)
    current = normalize_fix(seg.get("color_fix"))
    problems = []
    for name, (_neutral, lo, hi) in FIELDS.items():
        value = args.get(name)
        if value is None:
            continue
        number = _num(value)
        if number is None or not lo - 1e-9 <= number <= hi + 1e-9:
            problems.append(f"{name} — {lo:g}..{hi:g}")
    if problems:
        raise ToolError("цвет куска не принят", problems)
    if args.get("reset"):
        fix: dict = {}
    else:
        fix = {**current, **{k: args[k] for k in FIELDS if args.get(k) is not None}}
    fix = normalize_fix(fix)
    entries = [e for e in entries if e["piece"] != seg["id"]] + ([{"piece": seg["id"], **fix}] if fix else [])
    saved = _save_render(ctx, {**render, "color_fix": entries})
    out: dict = {"ok": True, "piece": index, "color_fix": fix, "summary": describe(fix) or "как в исходнике",
                 "pieces_with_color": len(saved.get("color_fix") or []),
                 "note": "поправка куска — до лука пресета; exposure в ступенях (EV), temperature + теплее / − "
                         "холоднее, tint + пурпурнее / − зеленее. Подогнать куски друг к другу по замеру — "
                         "match_piece_colors. Проверь view_frame на этом куске."}
    warnings = []
    if _source_has_key(ctx, plan):
        warnings.append("у источника хромакей — цвет куска к основе не применяется (сорвал бы ключ)")
    if abs(fix.get("exposure", 0.0)) > 1.2:
        warnings.append("экспозиция больше ±1.2 EV — светлое выгорит / тёмное зашумит, проверь кадр")
    if warnings:
        out["warnings"] = warnings
    return out


def _source_has_key(ctx: ToolContext, plan: dict) -> bool:
    try:
        return bool(ctx.store.get_source(int(plan["source_id"])).get("chroma_key"))
    except (KeyError, TypeError, ValueError):
        return False


def h_match_piece_colors(ctx: ToolContext, args: dict) -> dict:
    """Подогнать цвет кусков к образцу по замеру signalstats (app/piece_color.py)."""
    from app.heavy_queue import heavy_slot
    from app.piece_color import describe, match_fix, measure, normalize_fix, predict
    from app.piece_speed import seg_length

    plan = ctx.store.get_clip_plan(ctx.plan_id)
    segments = plan.get("segments") or []
    if len(segments) < 2:
        raise ToolError("в клипе один кусок — подгонять не к чему (цвет куска — set_piece_color)")
    if args.get("reference") is None:
        reference = max(range(len(segments)), key=lambda i: seg_length(segments[i]))
    else:
        reference = _piece_index({"piece": args.get("reference")}, len(segments))
    targets = args.get("pieces")
    if targets is None:
        targets = [i for i in range(len(segments)) if i != reference]
    try:
        targets = sorted({int(i) for i in targets} - {reference})
    except (TypeError, ValueError) as exc:
        raise ToolError("pieces — номера кусков с 0") from exc
    if any(not 0 <= i < len(segments) for i in targets):
        raise ToolError(f"pieces: куски 0..{len(segments) - 1}")
    strength = _num(args.get("strength"))
    strength = 1.0 if strength is None else min(1.0, max(0.0, strength))
    source = ctx.store.get_source(int(plan["source_id"]))
    path = Path(str(source.get("local_path") or ""))
    if not path.is_file():
        raise ToolError("исходник недоступен — замерить кадры нечем")
    if source.get("chroma_key"):
        raise ToolError("у источника хромакей — цвет основы не трогаем (сорвал бы ключ)")
    crop = source.get("content_crop") or None
    stats: dict[int, dict] = {}
    try:
        with heavy_slot("color_match", f"клип {ctx.plan_id}"):
            for i in [reference, *targets]:
                seg = segments[i]
                stats[i] = measure(path, float(seg["start_sec"]), float(seg["end_sec"]), crop)
    except (ValueError, OSError) as exc:
        raise ToolError(f"замер не удался: {exc}") from exc
    target = predict(stats[reference], segments[reference].get("color_fix"))
    render, entries = _color_entries(ctx)
    by_id = {e["piece"]: e for e in entries}
    report = []
    for i in targets:
        seg = segments[i]
        fix = match_fix(stats[i], target, seg.get("color_fix"), strength)
        if fix:
            by_id[seg["id"]] = {"piece": seg["id"], **fix}
        else:
            by_id.pop(seg["id"], None)
        after = predict(stats[i], fix)
        report.append({"piece": i, "before": stats[i], "after": after, "color_fix": fix,
                       "summary": describe(fix) or "без поправки",
                       "luma_gap_before": round(abs(stats[i]["y"] - target["y"]), 1),
                       "luma_gap_after": round(abs(after["y"] - target["y"]), 1)})
    order = {seg["id"]: k for k, seg in enumerate(segments)}
    fresh = sorted(by_id.values(), key=lambda e: order.get(e["piece"], 10**6))
    _save_render(ctx, {**render, "color_fix": fresh})
    out: dict = {"ok": True, "reference": reference, "reference_stats": stats[reference], "target": target,
                 "strength": strength, "pieces": report,
                 "note": "средние яркость (Y), U/V и насыщенность кусков сведены к образцу по 5 кадрам каждого. "
                         "Это выравнивание, а не лук: лук пресета ложится поверх. Ночь и день так не сравнять — "
                         "проверь view_frame/contact_sheet; поправить руками — set_piece_color, снять — reset."}
    clipped = [r["piece"] for r in report if r["luma_gap_after"] > 8]
    if clipped:
        out["warnings"] = [f"куски {clipped}: разница слишком большая (поправка упёрлась в предел ±1.5 EV / "
                           "насыщенность 0.4..1.8) — сцены разные, подгоняй мягче (strength) или оставь"]
    return out


def _beat_grid(ctx: ToolContext, args: dict, plan: dict, clock: Any) -> tuple[list[float], str, dict]:
    """Сетка долей для snap_to_beats: (моменты, шкала "final"/"clip", описание)."""
    from app.beat_snap import describe_grid, grid_times

    kind = str(args.get("grid") or "beat")
    if args.get("beats") is not None:
        try:
            beats = sorted({round(float(t), 4) for t in args["beats"]})
        except (TypeError, ValueError) as exc:
            raise ToolError("beats — список секунд готового ролика") from exc
        return beats, "final", {"source": "beats", "count": len(beats), "time": "final"}
    if args.get("bpm") is not None:
        bpm = _num(args.get("bpm"))
        if bpm is None or not 40 <= bpm <= 240:
            raise ToolError("bpm — 40..240")
        first = _num(args.get("first_beat")) or 0.0
        down = _num(args.get("first_downbeat"))
        grid = {"bpm": bpm, "period": 60.0 / bpm, "first_beat": first,
                "first_downbeat": first if down is None else down, "downbeat_every": 4, "time": "final"}
        return grid_times(grid, clock.final_duration, kind), "final", describe_grid(grid, kind, "manual")
    source = str(args.get("source") or "auto")
    if source in ("auto", "music"):
        from app.audio_map import _music_beats

        grid = _music_beats(ctx.store, plan.get("render_settings") or {}, clock)
        if grid:
            return grid_times(grid, clock.final_duration, kind), "final", describe_grid(grid, kind, "music")
        if source == "music":
            raise ToolError("у клипа нет музыки set_music или в ней не слышно пульса — передай bpm и first_beat "
                            "(или beats) сам, либо source: \"audio\" — доли звука клипа")
    from app.audio_map import clip_audio_map

    try:
        view, _png = clip_audio_map(ctx.store, ctx.plan_id)
    except ValueError as exc:
        raise ToolError(f"доли звука клипа: {exc}") from exc
    grid = view.get("beats")
    if not grid:
        raise ToolError("в звуке клипа нет пульса (и музыки set_music нет) — передай bpm и first_beat сам")
    return grid_times(grid, clock.clip_duration, kind), "clip", describe_grid(grid, kind, "audio")


def h_snap_to_beats(ctx: ToolContext, args: dict) -> dict:
    """Начала слоёв, ключи, камера и склейки — на ближайшую долю (app/beat_snap.py)."""
    from app.beat_snap import (
        DEFAULT_TOLERANCE, GRIDS, MAX_TOLERANCE, MIN_PIECE_SEC, TARGETS, Scale, nearest, snap_camera, snap_layers,
    )
    from app.clip_spec import SpecError, apply_spec, export_spec, validate_spec
    from app.piece_speed import is_reversed, seg_speed
    from app.timebase import ClipClock

    raw_targets = args.get("targets") or ["layers"]
    if isinstance(raw_targets, str):
        raw_targets = [raw_targets]
    targets = set(TARGETS) if "all" in raw_targets else {str(t) for t in raw_targets}
    if not targets or targets - set(TARGETS):
        raise ToolError(f"targets — из {', '.join(TARGETS)} (или all)")
    grid_kind = str(args.get("grid") or "beat")
    if grid_kind not in GRIDS:
        raise ToolError(f"grid — {', '.join(GRIDS)}")
    tolerance = _num(args.get("tolerance"))
    tolerance = DEFAULT_TOLERANCE if tolerance is None else tolerance
    if not 0.01 <= tolerance <= MAX_TOLERANCE:
        raise ToolError(f"tolerance — 0.01..{MAX_TOLERANCE:g} с (дальше — это уже другой момент, не «чуть мимо»)")
    dry = bool(args.get("dry_run"))
    only = {str(i) for i in args.get("layer_ids") or []} or None

    plan = ctx.store.get_clip_plan(ctx.plan_id)
    clock = ClipClock.for_plan(plan)
    beats, scale_name, grid_info = _beat_grid(ctx, args, plan, clock)
    if not beats:
        raise ToolError("в сетке нет ни одной доли на длине ролика")
    changes: list[dict] = []
    problems: list[str] = []

    if "joins" in targets and len(plan.get("segments") or []) > 1:
        segments = plan["segments"]
        to_grid = clock.clip_to_final if scale_name == "final" else (lambda t: float(t))
        spec = export_spec(ctx.store, ctx.plan_id)
        pieces = [dict(p) for p in spec["pieces"]]
        duration = float(spec["source"].get("duration_sec") or 0) or math.inf
        shift = 0.0
        moved = False
        for k in range(len(segments) - 1):
            join = (clock.clip_starts[k + 1] + clock.clip_end(k)) / 2
            g = to_grid(join) + shift
            b = nearest(g, beats, tolerance)
            if b is None or abs(b - g) < 0.004:
                continue
            delta = round(b - g, 3)
            seg, piece = segments[k], pieces[k]
            src_delta = delta * seg_speed(seg)
            start, end = float(piece["start_sec"]), float(piece["end_sec"])
            if is_reversed(seg):
                start = round(start - src_delta, 3)  # конец развёрнутого куска на клипе — начало в исходнике
            else:
                end = round(end + src_delta, 3)
            if start < 0 or end > duration or end - start < MIN_PIECE_SEC:
                problems.append(f"стык {k}→{k + 1}: до доли {delta:+.2f} с — кусок вышел бы за исходник или "
                                f"короче {MIN_PIECE_SEC:g} с, не трогаю")
                continue
            piece.update(start_sec=start, end_sec=end)
            piece.pop("focus", None)
            shift += delta
            moved = True
            changes.append({"target": "join", "index": k, "from": round(g - shift + delta, 3), "to": b, "delta": delta})
        if moved and not dry:
            try:
                validate_spec(ctx.store, ctx.plan_id, {**spec, "pieces": pieces})
            except SpecError as exc:
                raise ToolError("склейки к долям не приняты", exc.problems) from exc
            ctx.before_change()
            apply_spec(ctx.store, ctx.plan_id, {**spec, "pieces": pieces})
            plan = ctx.store.get_clip_plan(ctx.plan_id)
            clock = ClipClock.for_plan(plan)

    scale = Scale(clock.clip_to_final, clock.final_to_clip) if scale_name == "final" else Scale()
    render = _current_render(ctx)
    fresh = dict(render)
    if targets & {"layers", "keys"}:
        layers, moved_layers = snap_layers(_layers_of(render), beats, tolerance, scale, keys="keys" in targets,
                                           starts="layers" in targets, only=only)
        if moved_layers:
            fresh["layers"] = layers
            changes += moved_layers
    if "camera" in targets and render.get("camera"):
        camera, moved_cam = snap_camera(_camera_of(render), beats, tolerance, scale)
        if moved_cam:
            fresh["camera"] = camera
            changes += moved_cam
    if fresh != render and not dry:
        _save_render(ctx, fresh)
    out: dict = {"ok": True, "dry_run": dry, "moved": len(changes), "changes": changes[:80], "grid": grid_info,
                 "tolerance": tolerance, "time": scale_name,
                 "note": ("from/to — секунды шкалы сетки (final — готовый ролик, clip — до вырезов). Сдвинуто только то, "
                          f"что было ближе {tolerance:g} с к доле; слои сдвинуты целиком (длина та же), склейки — "
                          "подрезкой конца куска (всё после стыка едет за ним). Субтитры, чат и запертые слои не "
                          "трогаются. Проверь contact_sheet/listen.")}
    if dry and "joins" in targets:
        out["note"] += " dry_run: слои посчитаны без сдвига склеек."
    if not changes:
        out["note"] = f"ничего не сдвинуто: ни один момент не ближе {tolerance:g} с к доле ({grid_kind})"
    if problems:
        out["warnings"] = problems
    return out


def h_set_transition(ctx: ToolContext, args: dict) -> dict:
    from app.transitions import AUDIO_MODES, SFX_KINDS, TRANSITIONS

    render = _current_render(ctx)
    transition = dict(render.get("transition") or {})
    problems = []
    if args.get("type") is not None:
        if args["type"] not in TRANSITIONS:
            problems.append(f"type: одно из {', '.join(TRANSITIONS)}")
        transition["type"] = args["type"]
    if args.get("sfx") is not None:
        if args["sfx"] not in SFX_KINDS:
            problems.append(f"sfx: одно из {', '.join(SFX_KINDS)}")
        transition["sfx"] = args["sfx"]
    if args.get("audio") is not None:
        if args["audio"] not in AUDIO_MODES:
            problems.append(f"audio: одно из {', '.join(AUDIO_MODES)}")
        transition["audio"] = args["audio"]
    for key in ("duration", "sfx_volume", "motion_blur"):
        if args.get(key) is not None:
            transition[key] = args[key]
    if args.get("smart") is not None:
        transition["smart"] = bool(args["smart"])
    if problems:
        raise ToolError("переход не принят", problems)
    from app.transitions import MIN_DURATION, SNAP_DURATIONS, normalize_transition, speech_join_warnings, \
        speech_under_joins

    plan = ctx.store.get_clip_plan(ctx.plan_id)
    segments = plan.get("segments") or []
    words = (ctx.store.get_clip_plan_subtitles(ctx.plan_id) or {}).get("words") or []
    notes = []
    if args.get("fit_speech"):
        # Наплыв не должен уводить слова под переход: длину — по самому тесному стыку.
        report = speech_under_joins(segments, transition, words)
        kind = normalize_transition(transition)["type"]
        if report["hit"] and kind not in SNAP_DURATIONS:
            if report["safe_duration"] >= MIN_DURATION:
                transition["duration"] = report["safe_duration"]
                notes.append(f"длина перехода {report['safe_duration']:.2f} с — наплыв не заходит на речь")
            else:
                notes.append("на каком-то стыке речь вплотную к склейке — переход любой длины заденет слова; "
                             "подрежь куски (см. warnings) или поставь type cut")
    fresh = _save_render(ctx, {**render, "transition": transition})
    out: dict = {"ok": True, "transition": fresh["transition"]}
    warnings = speech_join_warnings(speech_under_joins(segments, fresh["transition"], words), fresh["transition"])
    if warnings:
        out["warnings"] = warnings
    if notes:
        out["note"] = "; ".join(notes)
    return out


def ending_warning(store: Any, source_id: int, end_sec: float) -> str | None:
    """Совет, если клип кончается на полуслове или вплотную к последнему слову.

    Владелец: «только концовки обрываются». Конец последнего куска — время
    исходника; сверяем с расшифровкой: внутри фразы → договорить; ровно на
    её конце (меньше 0.3 с) → оставить хвост, Whisper ставит конец рано."""
    try:
        cues = store.get_source_transcript(source_id)
    except KeyError:
        return None
    for cue in cues:
        s, e = float(cue.get("start") or 0), float(cue.get("end") or 0)
        if s + 0.1 < end_sec < e - 0.05:
            text = " ".join(str(cue.get("text") or "").split())[-60:]
            return (f"клип обрывается на полуслове: фраза «…{text}» идёт до {e:.2f} с исходника — "
                    f"продли последний кусок до ≈{e + 0.5:.2f}")
        if e - 0.05 <= end_sec < e + 0.3:
            return f"конец вплотную к последнему слову ({e:.2f}) — оставь хвост 0.4–0.8 с (до ≈{e + 0.6:.2f})"
    return None


def h_trim_piece(ctx: ToolContext, args: dict) -> dict:
    from app.clip_spec import SpecError, apply_spec, export_spec

    try:
        index = int(args.get("index"))
    except (TypeError, ValueError) as exc:
        raise ToolError("нужен index куска (с 0)") from exc
    spec = export_spec(ctx.store, ctx.plan_id)
    pieces = spec["pieces"]
    if not 0 <= index < len(pieces):
        raise ToolError(f"куска {index} нет (0..{len(pieces) - 1})")
    piece = dict(pieces[index])
    # source_start/source_end — прежние имена из get_clip (алиасы).
    start = _num(args.get("start_sec", args.get("source_start")), piece["start_sec"])
    end = _num(args.get("end_sec", args.get("source_end")), piece["end_sec"])
    piece.update(start_sec=start, end_sec=end)
    piece.pop("focus", None)  # трек кадра остаётся прежним
    pieces = [*pieces[:index], piece, *pieces[index + 1:]]
    try:
        from app.clip_spec import validate_spec

        validate_spec(ctx.store, ctx.plan_id, {**spec, "pieces": pieces})
    except SpecError as exc:
        raise ToolError("подрезка не принята", exc.problems) from exc
    ctx.before_change()
    _fresh, changes = apply_spec(ctx.store, ctx.plan_id, {**spec, "pieces": pieces})
    timeline = clip_timeline(ctx.store, ctx.plan_id)
    out = {"ok": True, "changes": changes, "duration": timeline["duration"],
           "note": "слои, камера, вставки и субтитры сдвинуты вслед за куском; если кусок расширили на новую речь — generate_subtitles"}
    if index == len(pieces) - 1:
        warn = ending_warning(ctx.store, int(spec["source"]["id"]), float(end))
        if warn:
            out["warnings"] = [warn]
    return out


MAX_FIT_PIECE_SEC = 12.0


def h_set_segment_speed(ctx: ToolContext, args: dict) -> dict:
    """Скорость и реверс куска основы (app/piece_speed.py): slow-mo, ускорение, «перемотка»."""
    from app.audio_edit import normalize_audio
    from app.piece_remap import remap_render
    from app.piece_speed import (
        AUDIO_MODES, MAX_REVERSE_SEC, MAX_SPEED, MIN_SPEED, attach, default_audio, describe, normalize_speed,
        seg_length,
    )

    plan = ctx.store.get_clip_plan(ctx.plan_id)
    segments = plan.get("segments") or []
    index = _piece_index(args, len(segments))
    seg = segments[index]
    speed = 1.0 if args.get("speed") in (None, "") else _num(args.get("speed"))
    if speed is None or not MIN_SPEED <= speed <= MAX_SPEED:
        raise ToolError(f"speed — {MIN_SPEED:g}..{MAX_SPEED:g} (0.5 — вдвое медленнее, 2 — вдвое быстрее; 1 — как есть)")
    reverse = args.get("reverse") is True
    audio = str(args.get("audio") or "").strip()
    if audio and audio not in AUDIO_MODES:
        raise ToolError(f"audio — из {', '.join(AUDIO_MODES)}")
    if audio == "reverse" and not reverse:
        raise ToolError("audio \"reverse\" — только с reverse: true")
    source_len = float(seg["end_sec"]) - float(seg["start_sec"])
    if reverse and source_len > MAX_REVERSE_SEC + 1e-6:
        raise ToolError(f"реверс — кусок до {MAX_REVERSE_SEC:g} с исходника (этот {source_len:.1f} с): ffmpeg держит "
                        "все кадры в памяти. Разрежь кусок (trim_piece / create_clip) и разверни короткий")
    render = _current_render(ctx)
    entries = [e for e in normalize_speed(render.get("speed")) if e["piece"] != seg["id"]]
    entry = normalize_speed([{"piece": seg["id"], "speed": speed, "reverse": reverse, "audio": audio}])
    fresh = {**render, "speed": entries + entry}
    new_segments = attach([{k: v for k, v in s.items() if k not in ("speed", "reverse", "speed_audio")}
                           for s in segments], fresh)
    # Слои, камера, вставки, вырезы — за новой длиной куска (секунда исходника — та же).
    moved, note = remap_render(fresh, segments, new_segments)
    saved = _save_render(ctx, moved)
    piece = clip_timeline(ctx.store, ctx.plan_id)["pieces"][index]
    new_seg = new_segments[index]
    out: dict = {"ok": True, "piece": index, "speed": speed, "reverse": reverse,
                 "audio": (entry[0].get("audio") or default_audio(entry[0])) if entry else None,
                 "length_before": round(seg_length(seg), 3), "length": round(seg_length(new_seg), 3),
                 "clip_start": piece["clip_start"], "clip_end": piece["clip_end"],
                 "duration": clip_timeline(ctx.store, ctx.plan_id)["duration"], "time": "clip",
                 "speeds": saved.get("speed") or [],
                 "note": ("кусок на шкале clip теперь длиной (конец − начало исходника)/speed; focus куска — по-прежнему "
                          "секунды ИСХОДНИКА от его начала. Звук: tempo — та же речь без смены тона, pitch — как плёнка, "
                          "mute — тишина, reverse — задом наперёд. Субтитры куска растянуты в 1/speed, у реверса — "
                          "скрыты. Проверь view_frame/contact_sheet и рендер.")}
    if note:
        out["changes"] = [note]
    warnings = []
    if speed < 0.5:
        warnings.append("медленнее 0.5 при 30 к/с — кадры повторяются, движение «ступеньками»; для слоу-мо лучше "
                        "исходник 60 к/с")
    if reverse and normalize_audio(render.get("audio")).get("joins"):
        warnings.append("J/L на стыках с развёрнутым куском не работают — звук за краем к реверсу не относится")
    if describe(new_seg) and seg_length(new_seg) > 20:
        warnings.append(f"кусок на клипе {seg_length(new_seg):.1f} с — длинное слоу-мо утомляет; обычно 1–4 с")
    if warnings:
        out["warnings"] = warnings
    return out


def h_set_piece_fit(ctx: ToolContext, args: dict) -> dict:
    """Кусок шире окна 9:16: в кадре доля w ширины исходника, сверху и снизу фон."""
    from app.video_crop import FIT_MIN_W, normalize_fit, window_share

    timeline = clip_timeline(ctx.store, ctx.plan_id)
    index = _piece_index(args, len(timeline["pieces"]))
    w = args.get("w")
    if w is None or w is False or _num(w) == 0:
        fit = None
    else:
        value = _num(w)
        if value is None or not FIT_MIN_W <= value <= 1.0:
            raise ToolError(f"w — доля ширины исходника в кадре, {FIT_MIN_W}..1 (1 — весь кадр); null — обычный кадр")
        fit = normalize_fit({"w": value, "bg": args.get("bg") or "blur"})
        piece = timeline["pieces"][index]
        length = float(piece["source_end"]) - float(piece["source_start"])
        if length > MAX_FIT_PIECE_SEC:
            # Проверочный прогон Gemini: fit на весь 40-секундный клип — ролик превратился в
            # мелкую полоску на размытом фоне, как у дешёвых перезаливов.
            raise ToolError(
                f"кусок {length:.0f} с — «шире окна» на весь кусок делает ролик мелким видео на размытом фоне. "
                f"Сначала auto_focus (говорящий в кадре по планам). fit — только короткий кусок ≤ {MAX_FIT_PIECE_SEC:.0f} с, "
                "где двое по краям: разрежь кусок по склейке плана (trim_piece / create_clip)")
    fresh, changes = _apply_piece(ctx, index, fit=fit)
    out: dict = {"ok": True, "piece": index, "fit": fresh.get("fit"), "changes": changes}
    warnings = _shared_warning(ctx, fresh)
    source = ctx.store.get_source(int(timeline["plan"]["source_id"]))
    from app.formats import frame_size, settings_render_format

    share = window_share(source, *frame_size(settings_render_format(_current_render(ctx))))
    if fit and share and fit["w"] <= share + 0.02:
        warnings.append(f"w={fit['w']} не шире обычного окна ({share}) — смысла нет, бери больше")
    mode = (_current_render(ctx).get("layout") or {}).get("mode") or "single"
    if fit and mode in ("split", "pip_cam", "meme"):
        warnings.append(f"на этом куске раскладка {mode} не действует (вебки/окошка нет) — только он шире окна")
    if fit:
        bg = fit["bg"]
        bg_name = bg.get("kind") if isinstance(bg, dict) else bg
        out["note"] = ("кусок рендерится шире окна: центр — тот же focus куска; фон сверху и снизу — "
                       f"{bg_name}. Проверь view_frame на куске: субтитры и титры лягут на фон — это хорошо")
    if warnings:
        out["warnings"] = warnings
    return out


def h_add_insert(ctx: ToolContext, args: dict) -> dict:
    from app.montage_assets import MAX_INSERTS, MODES, normalize_inserts

    try:
        asset = ctx.store.get_montage_asset(int(args.get("asset_id")))
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolError("нет такого файла в библиотеке (см. list_assets)") from exc
    foreign = _foreign_asset_problem(ctx, asset["id"])
    if foreign:
        raise ToolError(foreign)
    mode = args.get("mode") or ("sound" if asset["kind"] == "audio" else "pip")
    if mode not in MODES:
        raise ToolError(f"mode: одно из {', '.join(MODES)}")
    if mode == "full" and (_num(args.get("duration"), 1.5) or 1.5) > FULL_INSERT_MAX_SEC + 1e-6:
        raise ToolError(f"вставка на весь кадр (mode full) — не дольше {FULL_INSERT_MAX_SEC:g} с: голос идёт под ней, "
                        "а зритель ждёт героя; дольше — mode pip (окошко)")
    render = _current_render(ctx)
    inserts = list(render.get("inserts") or [])
    if len(inserts) >= MAX_INSERTS:
        raise ToolError(f"вставок уже {MAX_INSERTS} — больше нельзя; для картинок используй слой image")
    on_word = None
    if args.get("at_word") is not None:
        on_word = word_at(ctx, args["at_word"])
        args = {**args, "at": round(on_word["t"] + float(args.get("word_offset") or 0), 3)}
    if args.get("at") is None:
        raise ToolError("нужно at (секунды клипа) или at_word")
    people = people_gate(ctx, asset)  # реальный человек при запрете — отказ до записи
    entry = {k: args[k] for k in ("at", "duration", "x", "y", "scale", "volume", "duck", "reason", "fill")
             if args.get(k) is not None}
    entry.update(asset_id=asset["id"], mode=mode)
    note = _fit_pip_insert(render, entry, asset, _clock(ctx)) if mode == "pip" else None
    fresh = _save_render(ctx, {**render, "inserts": normalize_inserts([*inserts, entry])})
    out: dict = {"ok": True, "inserts": fresh["inserts"]}
    if people.get("people_check"):
        out["people_check"] = people["people_check"]
    if on_word:
        out["on_word"] = (f"на слове «{on_word['word']}» (lines[{on_word['line']}].words[{on_word['word_i']}])"
                          + (f"; {on_word['note']}" if on_word.get("note") else ""))
    warnings = [note] if note else []
    if people.get("people_warning"):
        warnings.append(people["people_warning"])
    twice = [i for i in inserts if int(i.get("asset_id") or -1) == int(asset["id"]) and i.get("mode") != "sound"]
    if twice and mode != "sound":
        # QA 02.11 (клип 892): одна и та же гифка на 6.7 и 13.5 с.
        seconds = ", ".join(f"{float(i['at']):g}" for i in twice)
        warnings.append(f"этот файл уже вставлен на {seconds} с — повтор "
                        "выглядит как нехватка материала: на второе место — другая картинка/гифка под смысл слов")
    if warnings:
        out["warnings"] = warnings
    return out


def refit_inserts(store: Any, clip_plan_id: int) -> list[str]:
    """Подогнать все окошки-вставки клипа под текущие субтитры и хук (как add_insert).

    Вставки ставит и первый шаг ИИ-монтажа (склейка), когда субтитров ещё нет, —
    после моушн-прохода их надо развести с текстом. Возвращает пояснения."""
    from app.montage_assets import normalize_inserts

    plan = store.get_clip_plan(clip_plan_id, include_segments=False)
    render = dict(plan.get("render_settings") or {})
    notes, out = [], []
    for ins in render.get("inserts") or []:
        entry = dict(ins)
        if entry.get("mode") == "pip":
            try:
                note = _fit_pip_insert(render, entry, store.get_montage_asset(int(entry["asset_id"])),
                                       plan_clock(store, store.get_clip_plan(clip_plan_id)))
            except (KeyError, TypeError, ValueError):
                note = None
            if note:
                notes.append(note)
        out.append(entry)
    if notes:
        store.set_clip_plan_render_settings(clip_plan_id, {**render, "inserts": normalize_inserts(out)})
    return notes


CAPTION_HALF = 0.075  # половина высоты двух строк субтитров в долях кадра
HOOK_BOTTOM = 0.2


def _fit_pip_insert(render: dict, entry: dict, asset: dict, clock: Any = None) -> str | None:
    """Окошко мема не должно лечь на субтитры и хук: ставим его над субтитрами
    и, если не влезает, уменьшаем. Вставки ложатся поверх субтитров при рендере,
    а агент их в view_frame не видит — поэтому подгоняем здесь.

    Вставка ложится на кадр готового ролика (формат клипа, ``render.format``): ``scale`` — доля
    его ширины; кегль титров — px опорного кадра слоёв (``layers_format``). У 9:16 оба 1080×1920."""
    from app.formats import frame_size, ref_size, settings_layers_format, settings_render_format
    from app.montage_assets import MIN_INSERT_SCALE, PIP_WIDTH

    from app.cuts import PINNED_START_SEC, CutMap, normalize_cuts

    frame_w, frame_h = frame_size(settings_render_format(render))
    ref_h = float(ref_size(settings_layers_format(render))[1])
    cap_half = CAPTION_HALF if ref_h == 1920.0 else CAPTION_HALF * 1920 / ref_h

    # Сравниваем во времени ГОТОВОГО клипа: вырезанное начало сдвигает мем к хуку.
    # clock — шкалы клипа (app/timebase.py) с наплывами; без него — только вырезы.
    mapper = CutMap(normalize_cuts(render.get("cuts")))
    if clock is not None and clock.has_cuts:
        to_final, from_final = clock.clip_to_final, clock.final_to_clip
    elif mapper:
        to_final, from_final = mapper.clamp, mapper.source_time
    else:
        to_final = from_final = (lambda t: t)
    at = to_final(float(entry.get("at") or 0.0))
    end = at + float(entry.get("duration") or 1.5)
    layers = [l for l in render.get("layers") or [] if not l.get("hidden")]

    def window(l: dict) -> tuple[float, float]:
        a, b = float(l.get("start") or 0), float(l.get("end") or 0)
        if a <= PINNED_START_SEC:  # хук остаётся в начале и после вырезов
            return 0.0, b - a
        return to_final(a), to_final(b)

    def active(l: dict) -> bool:
        a, b = window(l)
        return a < end and b > at

    caps = [l for l in layers if l.get("type") == "captions" and active(l)]
    if not caps:
        return None
    cap_top = float(caps[0].get("y", 0.68)) - cap_half
    # Крупные титры (панчлайны) выше субтитров — тоже потолок для окошка мема.
    for l in layers:
        if l.get("type") != "text" or l.get("id") == "auto_hook" or not active(l):
            continue
        y = float(l.get("y", 0.5))
        if y < cap_top:
            size = float((l.get("props") or {}).get("size") or 96)
            cap_top = min(cap_top, y - size * 0.65 / ref_h)
    moved = ""
    hook = next((l for l in layers if l.get("id") == "auto_hook" and active(l)), None)
    if hook and cap_top - 0.015 - HOOK_BOTTOM < 0.12:
        # Между хуком и субтитрами мему не уместиться — пусть выходит сразу после хука.
        at = round(window(hook)[1], 2)
        entry["at"] = round(from_final(at), 2)
        hook = None
        moved = f"сдвинул на {at} с (после хука), "
    top = HOOK_BOTTOM if hook else 0.07
    room = cap_top - 0.015 - top
    if room < 0.08:
        return None  # субтитры у самого верха — не трогаем, пусть решает агент
    aw, ah = float(asset.get("width") or 0), float(asset.get("height") or 0)
    ratio = ah / aw if aw > 0 and ah > 0 else 1.0  # высота / ширина
    scale = float(entry.get("scale") or PIP_WIDTH)
    height = scale * frame_w * ratio / frame_h
    y = float(entry.get("y") if entry.get("y") is not None else 0.5)
    if y - height / 2 >= top and y + height / 2 <= cap_top - 0.015:
        return f"вставку {moved.rstrip(', ')}" if moved else None
    if height > room:
        scale = max(MIN_INSERT_SCALE, int(room * frame_h / (frame_w * ratio) * 1000) / 1000)
        height = scale * frame_w * ratio / frame_h
    entry["scale"] = scale
    entry["y"] = round(cap_top - 0.015 - height / 2, 3)
    entry["x"] = 0.5
    return (f"вставку {moved}подвинул/уменьшил, чтобы не закрыть субтитры и хук: y={entry['y']}, "
            f"scale={entry['scale']}")


def h_remove_insert(ctx: ToolContext, args: dict) -> dict:
    render = _current_render(ctx)
    inserts = list(render.get("inserts") or [])
    try:
        index = int(args.get("index"))
    except (TypeError, ValueError) as exc:
        raise ToolError("нужен index вставки") from exc
    if not 0 <= index < len(inserts):
        raise ToolError(f"вставки {index} нет (0..{len(inserts) - 1})")
    inserts.pop(index)
    fresh = _save_render(ctx, {**render, "inserts": inserts})
    return {"ok": True, "inserts": fresh["inserts"]}


def tools() -> list[Tool]:
    return [
        Tool(
            "set_end_hold", "Ставлю стоп-кадр в конце",
            "Стоп-кадр в конце готового ролика на этот клип: sec 0..1.5 (последний кадр стоит, пока затухает звук; "
            "музыка доигрывает на нём). Мему — 0: ролик обрывается на последнем кадре, без «замирания». "
            "reset:true (или sec не задан) — как у сервиса (RENDER_END_HOLD_SEC, обычно 0.5). "
            "/ End freeze-frame per clip (0 for memes).",
            _obj({"sec": {"type": "number", "minimum": 0, "maximum": 1.5}, "reset": {"type": "boolean"}}),
            h_set_end_hold, mutating=True,
            label=lambda a: "Стоп-кадр в конце: " + ("как у сервиса" if a.get("sec") is None or a.get("reset")
                                                     else f"{a.get('sec')} с"),
        ),
        Tool(
            "freeze_frame", "Ставлю стоп-кадр",
            "Стоп-кадр посреди ролика: кадр в момент at (секунды ГОТОВОГО ролика — шкала final, как "
            "contact_sheet и listen; без других стоп-кадров) замирает на duration 0.2–2.0 с (по умолчанию 1). "
            "audio: \"silence\" (по умолчанию) — стоп-кадр вставляется: звук исходника молчит, музыка играет, "
            "ролик длиннее на duration, субтитры, слои, звуки и вставки после at сдвигаются сами; "
            "\"continue\" — звук идёт дальше (стример договаривает), картинка стоит и продолжается с at+duration, "
            "длина та же. layers: \"freeze\" (по умолчанию) — слои, камера и анимированные субтитры стоят с "
            "кадром; \"continue\" — анимируются дальше на замершем кадре. Тот же at — заменить; remove:true + at — "
            "снять один; reset:true — снять все. Проверка — view_frame(t, final: true): t в готовом ролике СО "
            "стоп-кадрами. / Freeze-frame mid-clip.",
            _obj({"at": {"type": "number", "minimum": 0},
                  "duration": {"type": "number", "minimum": 0.2, "maximum": 2.0},
                  "audio": {"type": "string", "enum": ["silence", "continue"]},
                  "layers": {"type": "string", "enum": ["freeze", "continue"]},
                  "remove": {"type": "boolean"}, "reset": {"type": "boolean"}}),
            h_freeze_frame, mutating=True,
            label=lambda a: ("Снимаю стоп-кадры" if a.get("reset") or a.get("remove") else
                             f"Стоп-кадр на {(_num(a.get('at'), 0.0) or 0.0):.1f} с"),
        ),
        Tool(
            "suggest_cuts", "Ищу паузы и паразиты",
            "Найти паузы и слова-паразиты по словам клипа (ничего не меняет). mode: pauses|fillers|both. "
            "Пока владелец не ответил на вопросы по видео, откажет: сначала ответы (list_stream_answers), потом резка. "
            "Длинные паузы и вырезы, после которых остался бы обрывок, помечены risky и в cuts не входят — в реакциях "
            "это часто сам момент, не вырезай их вслепую. Шкала clip: "
            "cuts можно передать в apply_cuts как есть. / Suggest jump cuts.",
            _obj({"mode": {"type": "string"}, "min_pause": {"type": "number"}, "keep_pad": {"type": "number"}}),
            h_suggest_cuts, label=lambda a: "Ищу паузы и слова-паразиты",
        ),
        Tool(
            "apply_cuts", "Вырезаю паузы",
            "Пока владелец не ответил на вопросы по видео, откажет: сначала ответы (list_stream_answers), потом резка. "
            "Сохранить вырезы [{start,end}] — шкала clip (секунды клипа ДО вырезов, с наплывами, как слои, "
            "субтитры и cuts в get_clip); [] — вернуть всё. piece — номер куска, если вырез в зоне наплыва "
            "перехода (там время принадлежит двум кускам; get_clip и suggest_cuts его дают). В warnings — обрывки: "
            "кусок между вырезами короче min_island_sec или без трёх слов; merge_short_islands: true вырежет их "
            "вместе с соседними вырезами (merged_islands в ответе). / Save jump cuts.",
            _obj({"cuts": {"type": "array", "items": {"type": "object", "properties": {
                "start": {"type": "number"}, "end": {"type": "number"},
                "piece": {"type": "integer", "description": "чей вырез в зоне наплыва"}}}},
                  "min_island_sec": {"type": "number", "description": "короче — обрывок, по умолчанию 2.5; 0 — не проверять"},
                  "merge_short_islands": {"type": "boolean", "description": "вырезать обрывки вместе с соседними вырезами"}},
                 ["cuts"]),
            h_apply_cuts, mutating=True, label=lambda a: f"Вырезаю {len(a.get('cuts') or [])} кусочков",
        ),
        Tool(
            "auto_edit", "Автомонтаж",
            "Без ИИ за секунду: хук-титр, анимированные субтитры с эмодзи, наезды и удары по речи. "
            "Хорошая основа — дальше правь слои. style — стиль субтитров. / Rule-based auto edit.",
            _obj({"style": {"type": "string"}, "keep_layers": {"type": "boolean"}}),
            h_auto_edit, mutating=True, label=lambda a: "Делаю автомонтаж",
        ),
        Tool(
            "make_compilation", "Собираю компиляцию",
            "Новый клип «ТОП-N» из клипов одного проекта: номера обратным отсчётом, названия, субтитры, whoosh. "
            "clip_ids — от худшего к лучшему не важно, countdown ставит лучший (первый в списке) последним. / Compilation.",
            _obj({"clip_ids": {"type": "array", "items": {"type": "integer"}}, "title": {"type": "string"},
                  "countdown": {"type": "boolean"}}, ["clip_ids"]),
            h_make_compilation, mutating=True, scope="global", label=lambda a: "Собираю компиляцию",
        ),
        Tool(
            "set_transition", "Ставлю переход",
            "Переход между кусками: type (cut, whip, zoompunch, glitch, flash, fade, fadeblack, …), duration, "
            "sfx (none/whoosh/click/pop/boom), audio (smooth/hard), smart. Наплыв занимает последние duration "
            "секунд куска и первые следующего: если там речь — warnings скажут, какие слова уйдут под переход "
            "(fadeblack — речь на чёрном) и как поправить; fit_speech=true — сам укоротит переход, чтобы он не "
            "заходил на речь. Наплыв сдвигает шкалу clip — слои, камера, вставки переезжают сами. "
            "motion_blur 0..1 — смаз сдвигового перехода (whip/slide/smooth) по горизонтали на окне перехода; "
            "нет — как у клипа (set_motion_blur). / Transition between pieces.",
            _obj({"type": {"type": "string"}, "duration": {"type": "number"}, "sfx": {"type": "string"},
                  "sfx_volume": {"type": "number"}, "audio": {"type": "string"}, "smart": {"type": "boolean"},
                  "motion_blur": {"type": "number", "description": "смаз whip/slide 0..1"},
                  "fit_speech": {"type": "boolean", "description": "укоротить переход, чтобы не задевал слова"}}),
            h_set_transition, mutating=True, label=lambda a: f"Переход {a.get('type') or ''} {a.get('sfx') or ''}".strip(),
        ),
        Tool(
            "trim_piece", "Подрезаю кусок",
            "Подрезать кусок index: новые start_sec/end_sec во времени ИСХОДНИКА (5..180 с; те же имена, что "
            "get_clip.pieces[].start_sec/end_sec). Слои, камера, вставки, вырезы, тишина и субтитры едут за "
            "куском сами. / Trim a piece (source seconds).",
            _obj({"index": {"type": "integer"}, "start_sec": {"type": "number"}, "end_sec": {"type": "number"}},
                 ["index"]),
            h_trim_piece, mutating=True, label=lambda a: f"Подрезаю кусок {a.get('index')}",
        ),
        Tool(
            "set_segment_speed", "Скорость куска",
            "Скорость и реверс куска основы piece (номер с 0): speed 0.25..4 (0.5 — слоу-мо вдвое, 0.75, 1.5, 2 — "
            "ускорение; 1 — как есть), reverse true — задом наперёд («перемотка», кусок ≤ 10 с исходника). Кусок на "
            "таймлайне становится длиной (конец − начало)/speed: слои, камера, вставки и вырезы после него сдвигаются "
            "сами, субтитры куска растягиваются в 1/speed (у реверса скрыты). audio: tempo (по умолчанию у скорости — "
            "речь без смены тона), pitch (как плёнка: на слоу-мо ниже), mute (по умолчанию у реверса), reverse (звук "
            "задом, только с reverse). Повтор килла в слоу-мо — отдельный кусок того же момента (create_clip/"
            "spec) со speed 0.5. speed 1 и reverse false — снять. / Piece playback speed and reverse.",
            _obj({"piece": {"type": "integer"}, "speed": {"type": "number", "description": "0.25..4, 1 — как есть"},
                  "reverse": {"type": "boolean"},
                  "audio": {"type": "string", "enum": ["tempo", "pitch", "mute", "reverse"]}}, ["piece"]),
            h_set_segment_speed, mutating=True,
            label=lambda a: (f"Кусок {a.get('piece')}: " + ("реверс " if a.get("reverse") else "")
                             + (f"×{a.get('speed')}" if a.get("speed") not in (None, 1, 1.0) else "")).strip(),
        ),
        Tool(
            "set_piece_color", "Цвет куска",
            "Цветокоррекция куска основы piece (номер с 0) — компиляции и сериалы из разных сцен не прыгают по "
            "цвету: exposure −2..2 (ступени EV: +1 — вдвое светлее), contrast 0.5..1.5, saturation 0..2 (0 — ч/б), "
            "temperature −1..1 (+ теплее, − холоднее), tint −1..1 (+ пурпурнее, − зеленее). Не переданное поле "
            "остаётся прежним; reset true — снять поправку. Поправка едет с куском (по его id), рендерится до лука "
            "пресета. Подогнать куски друг к другу по замеру — match_piece_colors. / Per-piece colour correction.",
            _obj({"piece": {"type": "integer"}, "exposure": {"type": "number"}, "contrast": {"type": "number"},
                  "saturation": {"type": "number"}, "temperature": {"type": "number"}, "tint": {"type": "number"},
                  "reset": {"type": "boolean"}}, ["piece"]),
            h_set_piece_color, mutating=True,
            label=lambda a: f"Цвет куска {a.get('piece')}" + (": сброс" if a.get("reset") else ""),
        ),
        Tool(
            "match_piece_colors", "Подгоняю цвет кусков",
            "Авто-подгонка цвета: по 5 кадрам каждого куска ffmpeg меряет среднюю яркость, U/V (баланс) и "
            "насыщенность и ставит кускам pieces (нет — всем, кроме образца) поправку (exposure, saturation, "
            "temperature, tint), чтобы они совпали с куском-образцом reference (нет — самый длинный). strength "
            "0..1 — доля пути (1 — полностью, 0.5 — наполовину). Ответ: замеры до/после и разница яркости. "
            "Ночь и день так не сравнять — смотри contact_sheet. / Auto-match piece colours to a reference piece.",
            _obj({"reference": {"type": "integer"}, "pieces": {"type": "array", "items": {"type": "integer"}},
                  "strength": {"type": "number", "minimum": 0, "maximum": 1}}),
            h_match_piece_colors, mutating=True,
            label=lambda a: "Подгоняю цвет кусков" + (f" к куску {a.get('reference')}" if a.get("reference") is not None else ""),
        ),
        Tool(
            "snap_to_beats", "Ставлю на доли",
            "Привязка к долям музыки одной командой: targets — layers (начала слоёв, длина та же), keys (ключевые "
            "кадры слоёв), camera (ключи камеры), joins (склейки кусков — подрезкой конца куска, всё после стыка "
            "едет за ним) или all; по умолчанию layers. grid: beat (каждая доля), downbeat (сильная, раз в такт), "
            "half (восьмые). tolerance (0.15 с; до 0.5) — дальше от доли не трогаем. Сетка: музыка set_music "
            "(audio_map.music_beats), без неё — доли звука клипа (source: audio), или своя: bpm + first_beat "
            "(+ first_downbeat) в секундах готового ролика, или beats — список моментов. Субтитры, чат, PNGtuber "
            "и запертые слои не двигаются; layer_ids — только эти слои. dry_run — только показать сдвиги. "
            "/ Snap layer starts, keyframes and cut joins to the nearest music beat.",
            _obj({"targets": {"type": "array", "items": {"type": "string",
                                                         "enum": ["layers", "keys", "camera", "joins", "all"]}},
                  "tolerance": {"type": "number", "minimum": 0.01, "maximum": 0.5},
                  "grid": {"type": "string", "enum": ["beat", "downbeat", "half"]},
                  "source": {"type": "string", "enum": ["auto", "music", "audio"]},
                  "bpm": {"type": "number"}, "first_beat": {"type": "number"}, "first_downbeat": {"type": "number"},
                  "beats": {"type": "array", "items": {"type": "number"}},
                  "layer_ids": {"type": "array", "items": {"type": "string"}},
                  "dry_run": {"type": "boolean"}}),
            h_snap_to_beats, mutating=True,
            label=lambda a: "Ставлю на доли: " + ", ".join(a.get("targets") or ["layers"]),
        ),
        Tool(
            "set_piece_fit", "Кадр шире окна",
            "Кусок piece шире окна 9:16 — когда двое по краям широкого плана не влезают в окно (камера только "
            "приближает, zoom ≥ 1): w — доля ширины исходника в кадре (0.35..1; 1 — весь кадр, ≈0.6 — двое "
            "крупнее), bg — фон сверху и снизу: blur (размытый тот же кадр, по умолчанию) или анимированный фон "
            "(имя стиля aurora|grid|bokeh|waves или объект {kind, colors, speed, intensity} как у set_backdrop); "
            "black/#rrggbb (заливка) — только если владелец просил. "
            "Центр — тот же focus куска. w null — обычный кадр. Раскладка клипа (split/pip/meme) на этот кусок "
            "не действует. Часто лучше фокус на говорящего с cut на склейках (set_focus) — fit для общего плана. "
            "/ Show a piece wider than the 9:16 window.",
            _obj({"piece": {"type": "integer"}, "w": {"type": ["number", "null"]},
                  "bg": {"type": ["string", "object"],
                         "description": "blur | aurora | grid | bokeh | waves | {kind, colors, speed, intensity} | black | #rrggbb"}},
                 ["piece"]),
            h_set_piece_fit, mutating=True,
            label=lambda a: f"Кусок {a.get('piece')} шире окна" if a.get("w") else f"Кусок {a.get('piece')}: обычный кадр",
        ),
        Tool(
            "add_insert", "Вставляю мем",
            "Вставка из библиотеки поверх клипа (видео-мем/картинка/звук): asset_id, at (с клипа), duration, "
            "mode pip|full|sound, x, y, scale, volume. Для статичных картинок лучше слой image. Вставки "
            "видны в view_frame и contact_sheet (первые кадры гифки). Окошко само встаёт между хуком и "
            "субтитрами. mode full другой пропорции (горизонтальная гифка) — вокруг размытая копия вставки "
            "(fill blur, по умолчанию), cover — обрезать во весь кадр, black — чёрные поля. Один файл дважды — "
            "предупреждение. / Insert.",
            _obj({"asset_id": {"type": "integer"}, "at": {"type": "number"}, "duration": {"type": "number"},
                  "mode": {"type": "string", "enum": ["pip", "full", "sound"]}, "x": {"type": "number"},
                  "y": {"type": "number"}, "scale": {"type": "number"}, "volume": {"type": "number"},
                  "reason": {"type": "string"},
                  "fill": {"type": "string", "enum": ["blur", "cover", "black", "clip"],
                           "description": ("mode full: чем заполнить кадр вокруг вставки другой пропорции; clip — "
                                           "размытый кадр клипа (по умолчанию у прозрачных гифок/PNG)")},
                  "at_word": _AT_WORD_SCHEMA,
                  "word_offset": {"type": "number"}}, ["asset_id"]),
            h_add_insert, mutating=True,
            label=lambda a: (f"Вставляю мем #{a.get('asset_id')} на слово" if a.get("at_word") is not None
                             else f"Вставляю мем #{a.get('asset_id')} на {(_num(a.get('at'), 0.0) or 0.0):.1f} с"),
        ),
        Tool(
            "remove_insert", "Убираю вставку",
            "Убрать вставку по index (из get_clip). / Remove insert.",
            _obj({"index": {"type": "integer"}}, ["index"]), h_remove_insert, mutating=True,
            label=lambda a: f"Убираю вставку {a.get('index')}",
        ),
    ]
