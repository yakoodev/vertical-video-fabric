"""Инструменты монтажа: цвет кусков и резка по биту (из ``app/cut_tools.py``)."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from app.tool_core import (
    _camera_of, _current_render, _layers_of, _num, _piece_index, _save_render, ToolContext, ToolError
)

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
