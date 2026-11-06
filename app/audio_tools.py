"""MCP-инструменты звукового монтажа: audio_map, J/L, ключи громкости, ducking музыки, эффекты.

Здесь — только обработчики и описания для агента; вся логика — app/audio_edit.py (хранение,
рендер) и app/audio_map.py (анализ). Реестр agent_tools берёт отсюда ``tools()``: общий файл
инструментов не разрастается.
"""

from __future__ import annotations

from typing import Any


def _at():
    from app import agent_tools

    return agent_tools


def _audio(ctx: Any) -> tuple[dict, dict]:
    from app.audio_edit import normalize_audio

    render = _at()._current_render(ctx)
    return render, normalize_audio(render.get("audio"))


def _save_audio(ctx: Any, render: dict, audio: dict) -> dict:
    from app.audio_edit import normalize_audio

    fresh = _at()._save_render(ctx, {**render, "audio": normalize_audio(audio)})
    return fresh.get("audio") or {}


def _segments(ctx: Any) -> list[dict]:
    return ctx.store.get_clip_plan(ctx.plan_id).get("segments") or []


def audio_view(render: dict, segments: list[dict]) -> dict:
    """``render.audio`` для get_clip: J/L — с номером куска (как в set_audio_joins), а не id."""
    from app.audio_edit import normalize_audio

    audio = normalize_audio(render.get("audio"))
    if audio.get("joins"):
        ids = [s.get("id") for s in segments]
        audio["joins"] = [{"piece": ids.index(j["piece"]), **{k: v for k, v in j.items() if k != "piece"}}
                          for j in audio["joins"] if j["piece"] in ids]
    return audio


# --------------------------------------------------------------------------- #
# audio_map
# --------------------------------------------------------------------------- #


def h_audio_map(ctx: Any, args: dict) -> Any:
    from app.audio_map import clip_audio_map, source_audio_map

    at = _at()
    image = bool(args.get("image"))
    lo, hi = at._num(args.get("from_sec")), at._num(args.get("to_sec"))
    try:
        if args.get("source") or ctx.clip_plan_id is None:
            project = at._source_or_clip_source(ctx, args)
            track = at._num(args.get("track"))
            out, png = source_audio_map(ctx.store, project, lo, hi, None if track is None else int(track), image)
        else:
            out, png = clip_audio_map(ctx.store, ctx.plan_id, from_sec=lo, to_sec=hi, want_image=image)
    except ValueError as exc:
        raise at.ToolError(f"audio_map: {exc}") from exc
    out = _with_chat_peaks(ctx, out)
    if png:
        out = {**out, "image_note": "картинка: громкость столбиками (−60…0 дБ), зелёная полоса — речь, красные "
                                    "риски — удары, жёлтые линии — стыки кусков; засечки снизу — каждая секунда "
                                    "шкалы ответа, длинные — кратные 5 с"}
        return at.ImageResult(data=png, mime_type="image/png", info=out)
    return {"ok": True, **out}


def _with_chat_peaks(ctx: Any, out: dict) -> dict:
    """Пики чата Twitch (если импортирован) — в карту моментов, шкалой ответа (app/chat_signal.py)."""
    from app import chat_signal as cs

    if out.get("time") == "source":
        peaks = cs.audio_map_peaks(ctx.store, int(out["project_id"]), out.get("from_sec"), out.get("to_sec"))
    else:
        plan = ctx.store.get_clip_plan(ctx.plan_id)
        segs = [(float(s["start_sec"]), float(s["end_sec"])) for s in plan.get("segments") or []]
        if not segs:
            return out
        peaks = cs.audio_map_peaks(ctx.store, int(plan["source_id"]), min(a for a, _ in segs),
                                   max(b for _, b in segs), to_out=cs.clip_time_mapper(plan))
    return {**out, "chat_peaks": peaks, "chat_note": cs.PEAKS_NOTE} if peaks else out


# --------------------------------------------------------------------------- #
# J/L
# --------------------------------------------------------------------------- #


def h_set_audio_joins(ctx: Any, args: dict) -> dict:
    from app.audio_edit import MAX_SHIFT_SEC, jl_speech_report, normalize_joins, part_shifts, plan_jl
    from app.transitions import part_overlaps

    at = _at()
    raw = args.get("joins")
    if not isinstance(raw, list):
        raise at.ToolError("joins — список {piece, lead?, lag?} (piece — номер куска с 0; [] — снять все J/L)")
    segments = _segments(ctx)
    problems, warnings = [], []
    items: dict[int, dict] = {}  # кусок → {piece, lead, lag}: несколько записей одного куска сливаются
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            problems.append(f"joins[{i}]: нужен объект {{piece, lead?, lag?}}")
            continue
        try:
            index = at._piece_index(item, len(segments))
        except at.ToolError as exc:
            problems.append(f"joins[{i}]: {exc}")
            continue
        entry = items.setdefault(index, {"piece": int(segments[index]["id"])})
        for key in ("lead", "lag"):
            value = at._num(item.get(key))
            if value is None:
                continue
            if value < 0 or value > MAX_SHIFT_SEC:
                problems.append(f"joins[{i}].{key}: 0..{MAX_SHIFT_SEC:g} с")
                continue
            entry[key] = value
        if entry.get("lead") and index == 0:
            warnings.append("lead у первого куска не на что накладывать — пропущен")
            entry.pop("lead")
        if entry.get("lag") and index == len(segments) - 1:
            warnings.append("lag у последнего куска не на что накладывать — пропущен")
            entry.pop("lag")
    if problems:
        raise at.ToolError("J/L не приняты", problems)
    render, audio = _audio(ctx)
    audio["joins"] = normalize_joins(list(items.values()))
    saved = _save_audio(ctx, render, audio)
    ids = [s["id"] for s in segments]
    view = [{"piece": ids.index(j["piece"]), **{k: v for k, v in j.items() if k != "piece"}}
            for j in saved.get("joins") or [] if j["piece"] in ids]
    shifts = part_shifts(segments, segments, saved.get("joins"))
    from app.piece_speed import seg_length

    lengths = [seg_length(s) for s in segments]
    plan = plan_jl(lengths, part_overlaps(segments, render.get("transition")), shifts)
    for k, (want, got) in enumerate(zip(shifts, plan["shift"])):
        if abs(want - got) > 0.01:
            warnings.append(f"стык {k}→{k + 1}: сдвиг {want:+.2f} с урезан до {got:+.2f} (J/L не больше 45% "
                            "соседнего куска)")
    subs = ctx.store.get_clip_plan_subtitles(ctx.plan_id) or {}
    words = subs.get("words") or []
    report = jl_speech_report(segments, saved.get("joins"), words, render.get("transition"))
    for row in report:
        if row["lost_words"]:
            warnings.append(f"стык {row['join']}→{row['join'] + 1} ({row['kind']} {abs(row['shift']):.2f} с): не будет "
                            f"слышно {row['hidden']} — слова «{' '.join(row['lost_words'])}» (субтитры их тоже не "
                            "покажут); ставь J/L на паузу (audio_map: pauses, jl) или подрежь кусок")
    missing = _handles_not_transcribed(segments, saved.get("joins"), render.get("transition"), subs)
    if missing:
        warnings.append(f"звук ручек J/L ({', '.join(missing)}) ещё не распознан — субтитры там пустые; "
                        "generate_subtitles распознает куски вместе с ручками (правки строк — заново)")
    out = {"ok": True, "joins": view, "joins_report": report,
           "note": ("J/L: картинка режется как раньше, звук соседнего куска наезжает на стык с кроссфейдом "
                    "0.08 с (под переходом — его длиной). lead — звук куска раньше его картинки (J), lag — звук "
                    "тянется под следующую картинку (L); на одном стыке lag A и lead B вычитаются. Звук каждого "
                    "куска остаётся синхронным со своей картинкой. Субтитры идут за звуком: слова заменённого звука "
                    "убраны, слова ручки — там, где их слышно (тем же сдвигом, что у их куска). Превью редактора "
                    "играет J/L вторым плеером. Проверь listen на стыке.")}
    if warnings:
        out["warnings"] = warnings
    return out


def _handles_not_transcribed(segments: list[dict], joins: Any, transition: Any, subs: dict) -> list[str]:
    """Ручки J/L, звук которых субтитры не знают (распознавали без них): «кусок 2: J» …"""
    from app.audio_edit import handle_extents, normalize_handle_words

    if not subs.get("words"):
        return []
    have: dict[Any, list[float]] = {}
    for w in normalize_handle_words(subs.get("handle_words")):
        have.setdefault(w["piece"], []).append((w["start"] + w["end"]) / 2)
    # Распознавание с ручками отмечает, какие окна исходника уже слушали (даже без слов).
    heard = {(int(h.get("piece", -1)), str(h.get("side"))): (float(h.get("start", 0)), float(h.get("end", 0)))
             for h in subs.get("handle_spans") or [] if isinstance(h, dict)}
    out = []
    for k, (head, tail) in enumerate(handle_extents(segments, joins, transition, margin=0.0)):
        seg = segments[k]
        for side, length in (("head", head), ("tail", tail)):
            if length <= 0:
                continue
            lo = float(seg["start_sec"]) - length if side == "head" else float(seg["end_sec"])
            hi = float(seg["start_sec"]) if side == "head" else float(seg["end_sec"]) + length
            span = heard.get((int(seg.get("id") or -1), side))
            if span and span[0] <= lo + 0.05 and span[1] >= hi - 0.05:
                continue
            if any(lo <= m <= hi for m in have.get(seg.get("id"), [])):
                continue
            out.append(f"кусок {k}: {'J' if side == 'head' else 'L'}")
    return out


# --------------------------------------------------------------------------- #
# Громкость во времени
# --------------------------------------------------------------------------- #


def h_set_audio_keys(ctx: Any, args: dict) -> dict:
    from app.audio_edit import EASES, KEY_MAX_DB, KEY_MIN_DB, normalize_keys

    at = _at()
    target = str(args.get("target") or "")
    if target not in ("source", "music"):
        raise at.ToolError('target — "source" (звук исходника) или "music" (фоновая музыка)')
    keys = args.get("keys")
    if not isinstance(keys, list):
        raise at.ToolError("keys — список {t, gain_db, ease?} (шкала clip; [] — снять)")
    problems = []
    for i, k in enumerate(keys):
        if not isinstance(k, dict) or at._num(k.get("t")) is None or at._num(k.get("gain_db")) is None:
            problems.append(f"keys[{i}]: нужны t (с, шкала clip) и gain_db ({KEY_MIN_DB:g}..+{KEY_MAX_DB:g})")
        elif k.get("ease") is not None and k.get("ease") not in EASES:
            problems.append(f"keys[{i}].ease — из {', '.join(EASES)}")
    if problems:
        raise at.ToolError("ключи не приняты", problems)
    render, audio = _audio(ctx)
    audio[f"{target}_keys"] = normalize_keys(keys)
    saved = _save_audio(ctx, render, audio)
    out = {"ok": True, "target": target, "keys": saved.get(f"{target}_keys") or [], "time": "clip",
           "note": ("ключи громкости: 0 дБ — как без ключей, между ключами — по ease ключа-цели (linear, smooth, in, "
                    "out, step — держать прежнее до самого ключа); до первого и после последнего — их значение. "
                    + ("Музыка: поверх автоматического ducking под речью." if target == "music" else
                       "Звук исходника (все дорожки), до звуков слоёв и музыки.") + " Ролик потом выравнивается до −14 LUFS.")}
    if target == "music" and not render.get("music_on"):
        out["warnings"] = ["музыка клипа выключена — ключи сработают после set_music"]
    return out


def h_set_music_duck(ctx: Any, args: dict) -> dict:
    from app.audio_edit import DIP_DEFAULT_SEC, normalize_dips, normalize_duck

    at = _at()
    if args.get("duck") is None and args.get("dips") is None:
        raise at.ToolError("нужен duck [{start, end, gain_db}] и/или dips [{at | at_word, before}] ([] — снять)")
    render, audio = _audio(ctx)
    if args.get("duck") is not None:
        if not isinstance(args["duck"], list):
            raise at.ToolError("duck — список {start, end, gain_db} (шкала clip)")
        audio["music_duck"] = normalize_duck(args["duck"])
    if args.get("dips") is not None:
        if not isinstance(args["dips"], list):
            raise at.ToolError("dips — список {at | at_word, before}")
        dips = []
        for item in args["dips"]:
            item = dict(item) if isinstance(item, dict) else {"at": item}
            if item.get("at_word") is not None:
                item["at"] = round(at.word_time(ctx, item.pop("at_word")) + float(item.pop("word_offset", 0) or 0), 3)
            dips.append(item)
        audio["music_dips"] = normalize_dips(dips)
    saved = _save_audio(ctx, render, audio)
    out = {"ok": True, "duck": saved.get("music_duck") or [], "dips": saved.get("music_dips") or [], "time": "clip",
           "note": (f"duck — музыка тише на отрезке (рампы 0.12 с) поверх автоматического ducking под речью; dips — "
                    f"музыка уходит в тишину за before (по умолчанию {DIP_DEFAULT_SEC:g} с, лучше 0.3–0.8) до at и "
                    "возвращается ровно на at — удар/панчлайн звучит в тишине. at_word — на начало слова.")}
    if not render.get("music_on"):
        out["warnings"] = ["музыка клипа выключена — сработает после set_music"]
    return out


# --------------------------------------------------------------------------- #
# Эффекты на отрезке
# --------------------------------------------------------------------------- #


def h_set_audio_fx(ctx: Any, args: dict) -> dict:
    from app.audio_edit import FX_KINDS, normalize_fx

    at = _at()
    raw = args.get("fx")
    if not isinstance(raw, list):
        raise at.ToolError("fx — список {start, end, fx, track?, amount?, rate?, keep_pitch?} (шкала clip; [] — снять)")
    problems = []
    items = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict) or item.get("fx") not in FX_KINDS:
            problems.append(f"fx[{i}].fx — из {', '.join(FX_KINDS)}")
            continue
        item = dict(item)
        if item.get("at_word") is not None:
            item = at._place_on_word(ctx, item)
        if at._num(item.get("start")) is None or at._num(item.get("end")) is None:
            problems.append(f"fx[{i}]: нужны start и end (шкала clip) или at_word")
            continue
        items.append(item)
    if problems:
        raise at.ToolError("эффекты не приняты", problems)
    render, audio = _audio(ctx)
    audio["fx"] = normalize_fx(items)
    saved = _save_audio(ctx, render, audio)
    out = {"ok": True, "fx": saved.get("fx") or [], "time": "clip",
           "note": ("эффект на звуке исходника (без track — вся смесь, с track — одна дорожка), до звуков слоёв и "
                    "музыки; едет со своим звуком через вырезы и J/L. speed: rate 0.5–2 (меньше 1 — медленнее и ниже, "
                    "keep_pitch — без смены тона), длина отрезка та же; reverse — задом наперёд. Превью редактора "
                    "эффекты не играет — проверь listen или рендером.")}
    warnings = _at()._track_warnings(ctx, saved.get("fx") or [])
    if warnings:
        out["warnings"] = warnings
    return out


# --------------------------------------------------------------------------- #
# Сведение: замер и автосведение (app/audio_mix.py)
# --------------------------------------------------------------------------- #

MIX_NOTE = ("Цифры — по стемам клипа ровно так, как их сведёт рендер (голос-слои, звук исходника, музыка, звуки, "
            "звук видео-слоёв); итог ролика — по свежему рендеру, иначе прогноз финального прохода. Главное — "
            "voice_vs_music.diff_db: насколько речь громче музыки ТАМ, ГДЕ ГОВОРЯТ (норма ≥ 12, цель 15–18 дБ). "
            "verdicts — что не так и как поправить (suggest — готовый вызов). Время — шкала clip.")


def h_audio_mix_report(ctx: Any, args: dict) -> dict:
    from app.audio_mix import mix_report

    at = _at()
    try:
        out = mix_report(ctx.store, ctx.plan_id, render_id=args.get("render_id"),
                         use_render=args.get("use_render", True) is not False)
    except (ValueError, KeyError) as exc:
        raise at.ToolError(f"audio_mix_report: {exc}") from exc
    if not args.get("timeline"):
        line = (out.get("timeline") or {}).get("line")
        out["timeline"] = {"line": line, "step_sec": (out.get("timeline") or {}).get("step_sec"),
                           "line_scale": (out.get("timeline") or {}).get("line_scale"),
                           "note": "timeline=true — числа short-term по шагу"}
    out["note"] = MIX_NOTE
    return out


def h_auto_mix(ctx: Any, args: dict) -> dict:
    from app.audio_mix import AUTO_DUCK_DB, AUTO_SFX_UNDER_VOICE_DB, MUSIC_UNDER_TARGET_DB, auto_mix

    at = _at()

    def num(name: str, default: float | None, lo: float, hi: float) -> float | None:
        value = at._num(args.get(name))
        if value is None:
            return default
        if not lo <= value <= hi:
            raise at.ToolError(f"{name}: {lo:g}..{hi:g}")
        return value

    apply = not (args.get("dry_run") is True or args.get("apply") is False)
    try:
        return auto_mix(
            ctx.store, ctx.plan_id, target_lufs=num("target_lufs", None, -24, -9),
            music_under_voice_db=num("music_under_voice_db", -MUSIC_UNDER_TARGET_DB, -30, -6),
            duck_db=num("duck_db", AUTO_DUCK_DB, -24, 0),
            sfx_under_voice_db=num("sfx_under_voice_db", AUTO_SFX_UNDER_VOICE_DB, -20, 3),
            apply=apply, save=lambda render: at._save_render(ctx, render), compare=args.get("compare") is not False)
    except (ValueError, KeyError) as exc:
        raise at.ToolError(f"auto_mix: {exc}") from exc


# --------------------------------------------------------------------------- #
# Эффекты: цепочки слоёв/голоса/музыки, справочник, авто-звуки на склейках, кроссфейд
# --------------------------------------------------------------------------- #


def h_audio_fx_reference(_ctx: Any, _args: dict) -> dict:
    from app.audio_edit import FX_KINDS
    from app.audio_fx import reference

    out = reference()
    out["span_fx"] = {"kinds": FX_KINDS,
                      "about": "set_audio_fx — эффект на отрезке звука исходника; fx \"chain\" + chain/preset — любая "
                               "цепочка отсюда (кроме speed/tempo/reverse — у отрезка свои)"}
    out["auto_sfx"] = {"sounds": AUTO_SFX_SOUNDS, "about": "auto_sfx — вжухи на склейках, «поп» на плашках, "
                                                            "райзер/удар в заданные моменты; только записи пака"}
    return out


def h_set_audio_chain(ctx: Any, args: dict) -> dict:
    from app.audio_fx import describe_chain, normalize_chain
    from app.audio_mix import normalize_mix

    at = _at()
    target = str(args.get("target") or ("layer" if args.get("layer_id") else ""))
    if target not in ("layer", "voice", "music"):
        raise at.ToolError('target — "layer" (с layer_id), "voice" (голос клипа) или "music" (фоновый трек)')
    raw = args.get("chain") if args.get("chain") is not None else args.get("preset")
    problems: list[str] = []
    chain = normalize_chain(raw, problems)
    if raw not in (None, "", []) and not chain:
        raise at.ToolError("цепочка не принята", problems or ["пусто"])
    render = at._current_render(ctx)
    if target == "layer":
        lid = str(args.get("layer_id") or "")
        layers = at._layers_of(render)
        layer = next((layer for layer in layers if str(layer.get("id")) == lid), None)
        if layer is None:
            raise at.ToolError(f"слоя {lid!r} нет — get_clip (layers)")
        if layer.get("type") not in ("audio", "video"):
            raise at.ToolError(f"слой {lid} — {layer.get('type')}: эффекты звука — у audio и video с audio:true")
        props = dict(layer.get("props") or {})
        if args.get("append"):
            chain = (list(props.get("fx") or []) + chain)
        if chain:
            props["fx"] = chain
        else:
            props.pop("fx", None)
        layer["props"] = props
        fresh = at._save_render(ctx, {**render, "layers": layers})
        saved = next((x for x in fresh.get("layers") or [] if str(x.get("id")) == lid), {}).get("props", {}).get("fx") or []
    else:
        key = "voice_fx" if target == "voice" else "music_fx"
        mix = dict(render.get("mix") or {})
        if args.get("append"):
            chain = list(mix.get(key) or []) + chain
        mix[key] = chain
        fresh = at._save_render(ctx, {**render, "mix": normalize_mix(mix)})
        saved = (fresh.get("mix") or {}).get(key) or []
    out = {"ok": True, "target": target, "chain": saved, "chain_text": describe_chain(saved) or "без эффектов",
           "note": ("цепочка по порядку; превью редактора эффекты не играет — слушай рендер; громкость после эффектов "
                    "проверит audio_mix_report (финал всё равно −14 LUFS)")}
    if target == "voice":
        out["note"] += "; голос: у холста — слои-голос (озвучка, реплики), у клипа с исходником — весь его звук"
    if problems:
        out["warnings"] = problems
    return out


AUTO_SFX_SOUNDS = {
    "cut": ("sfx:freesound/whoosh-1.ogg", "sfx:freesound/whoosh-soft.ogg"),
    "badge": ("sfx:freesound/pop-soft.ogg",),
    "riser": ("sfx:freesound/riser.ogg",),
    "impact": ("sfx:freesound/impact-cinematic.ogg",),
}
AUTO_SFX_PREFIX = "asfx_"
AUTO_SFX_MIN_GAP = 0.5
AUTO_SFX_MAX = 40
AUTO_SFX_VOLUME = {"cut": 0.6, "badge": 0.5, "riser": 0.7, "impact": 0.8}
BADGE_LAYER_TYPES = ("text", "callout", "nameplate", "counter")


def _sound_ok(ref: str) -> tuple[bool, float]:
    from app.motion_assets import pack_item_info

    try:
        info = pack_item_info(ref)
    except Exception:  # noqa: BLE001
        info = None
    if not info:
        return False, 0.0
    duration = info.get("duration") or info.get("duration_sec")
    if not duration:
        from pathlib import Path

        from app.motion_render import _probe_sound

        try:
            duration = _probe_sound(Path(info["path"]))[0]
        except Exception:  # noqa: BLE001
            duration = 1.0
    return True, float(duration or 1.0)


def h_auto_sfx(ctx: Any, args: dict) -> dict:
    from app.agent_tools import plan_clock
    from app.canvas import is_canvas

    at = _at()
    kinds = args.get("on") or ["cuts", "badges"]
    if isinstance(kinds, str):
        kinds = [kinds]
    allowed = {"cuts", "badges"}
    bad = [k for k in kinds if k not in allowed]
    if bad:
        raise at.ToolError(f"on — из {', '.join(sorted(allowed))} (райзеры и удары — riser_at / impact_at)")
    plan = ctx.store.get_clip_plan(ctx.plan_id)
    render = at._current_render(ctx)
    clock = plan_clock(ctx.store, plan)
    duration = float(clock.clip_duration)
    layers = at._layers_of(render)
    kept = [layer for layer in layers if not str(layer.get("id") or "").startswith(AUTO_SFX_PREFIX)]
    existing = sorted(float(layer.get("start") or 0) for layer in kept if layer.get("type") == "audio"
                      and (layer.get("end") or 0) - (layer.get("start") or 0) < 3.0)
    notes: list[str] = []
    wanted: list[tuple[float, str]] = []
    if "cuts" in kinds:
        points = [round(float(t), 3) for t in clock.clip_starts[1:]]
        try:
            canvas = is_canvas(ctx.store.get_source(int(plan["source_id"])))
        except (KeyError, TypeError, ValueError):
            canvas = False
        if canvas or len(points) == 0:
            points += [round(float(layer["start"]), 3) for layer in kept if layer.get("type") in ("image", "video")
                       and not layer.get("hidden") and float(layer.get("start") or 0) > 0.15]
        wanted += [(t, "cut") for t in points]
    if "badges" in kinds:
        wanted += [(round(float(layer["start"]), 3), "badge") for layer in kept
                   if layer.get("type") in BADGE_LAYER_TYPES and not layer.get("hidden")]
    for key, kind in (("riser_at", "riser"), ("impact_at", "impact")):
        for value in args.get(key) or []:
            t = at._num(value)
            if t is not None and 0 <= t <= duration:
                wanted.append((round(t, 3), kind))
    sounds = {kind: [(ref, *_sound_ok(ref)) for ref in refs] for kind, refs in AUTO_SFX_SOUNDS.items()}
    placed: list[dict] = []
    taken: list[float] = list(existing)
    count = {"cut": 0, "badge": 0, "riser": 0, "impact": 0}
    missing: set[str] = set()
    for t, kind in sorted(wanted):
        ok_refs = [(ref, dur) for ref, ok, dur in sounds[kind] if ok]
        if not ok_refs:
            missing.add(kind)
            continue
        if any(abs(t - x) < (0.25 if x in existing else AUTO_SFX_MIN_GAP) for x in taken) and kind in ("cut", "badge"):
            continue
        ref, dur = ok_refs[count[kind] % len(ok_refs)]
        if kind == "riser":  # райзер кончается на моменте
            start, end = max(0.0, t - dur), t
        elif kind == "cut":  # вжух — чуть раньше склейки, пик на ней
            start, end = max(0.0, t - 0.05), min(duration, t + min(dur, 0.8))
        else:
            start, end = t, min(duration, t + min(dur, 1.5))
        if end - start < 0.1:
            continue
        count[kind] += 1
        taken.append(t)
        placed.append({"id": f"{AUTO_SFX_PREFIX}{kind}{count[kind]:02d}", "type": "audio", "name": f"Авто-звук: {kind}",
                       "start": round(start, 3), "end": round(end, 3),
                       "props": {"asset_id": ref, "volume": AUTO_SFX_VOLUME[kind], "role": "sfx"}})
        if len(placed) >= AUTO_SFX_MAX:
            notes.append(f"не больше {AUTO_SFX_MAX} авто-звуков — остальное пропущено")
            break
    if missing:
        notes.append(f"нет записей пака для: {', '.join(sorted(missing))} (tools/fetch_sfx.py) — синтетику не подставляю")
    out = {"ok": True, "placed": [{k: x[k] for k in ("id", "start", "end")} | {"sound": x["props"]["asset_id"]}
                                  for x in placed], "count": len(placed), "applied": not args.get("dry_run"),
           "note": ("звуки — эффекты (громкость под голос ставит сервис), роль sfx, id asfx_* (повторный вызов "
                    "заменит их). Потом auto_mix выровняет каждый под голос; убрать — remove_layers.")}
    if notes:
        out["warnings"] = notes
    if not args.get("dry_run"):
        at._save_render(ctx, {**render, "layers": kept + placed})
    return out


def h_audio_crossfade(ctx: Any, args: dict) -> dict:
    at = _at()
    a_id, b_id = str(args.get("from_layer") or ""), str(args.get("to_layer") or "")
    d = at._num(args.get("duration"))
    d = 0.5 if d is None else d
    if not 0.02 <= d <= 5:
        raise at.ToolError("duration — 0.02..5 с")
    render = at._current_render(ctx)
    layers = at._layers_of(render)
    a = next((x for x in layers if str(x.get("id")) == a_id), None)
    b = next((x for x in layers if str(x.get("id")) == b_id), None)
    if a is None or b is None:
        raise at.ToolError("from_layer и to_layer — id звуковых слоёв клипа (get_clip layers)")
    if a.get("type") != "audio" or b.get("type") != "audio":
        raise at.ToolError("кроссфейд — между audio-слоями")
    changes = []
    overlap = float(a["end"]) - float(b["start"])
    if overlap < d - 1e-3:
        # Слой A дотягивается под начало B (читает дальше своего файла), B остаётся на месте.
        new_end = round(float(b["start"]) + d, 3)
        changes.append(f"{a_id}: конец {float(a['end']):.2f} → {new_end:.2f} с (дотянут под начало {b_id})")
        a["end"] = new_end
    a["props"] = {**(a.get("props") or {}), "fadeOut": round(d, 3)}
    b["props"] = {**(b.get("props") or {}), "fadeIn": round(d, 3)}
    changes.append(f"наплыв {d:g} с: {a_id} затухает, {b_id} нарастает")
    at._save_render(ctx, {**render, "layers": layers})
    return {"ok": True, "changes": changes,
            "note": "если у файла A звука на дотяжку не хватит — там тишина; проверь listen/рендер"}


def h_set_mix(ctx: Any, args: dict) -> dict:
    from app.audio_mix import normalize_mix

    at = _at()
    render = at._current_render(ctx)
    mix = {} if args.get("reset") else dict(render.get("mix") or {})
    for key in ("target_lufs", "music_db", "duck_attack", "duck_release"):
        if key in args:
            if args[key] is None:
                mix.pop(key, None)
            else:
                mix[key] = args[key]
    if "sidechain" in args:
        if args["sidechain"] is False:
            mix["sidechain"] = False
        else:
            mix.pop("sidechain", None)
    for key in ("groups", "layer_gains"):
        if isinstance(args.get(key), dict):
            merged = dict(mix.get(key) or {})
            for name, value in args[key].items():
                if value is None:
                    merged.pop(str(name), None)
                else:
                    merged[str(name)] = value
            mix[key] = merged
    mix.pop("auto", None)  # руками правили — это уже не чистый auto_mix
    fresh = at._save_render(ctx, {**render, "mix": normalize_mix(mix)})
    out = {"ok": True, "mix": fresh.get("mix") or {},
           "note": ("music_db — громкость трека в дБ к файлу (без подгонки под голос); sidechain false — без "
                    "автоматического ducking (тогда set_music_duck); groups {voice, sfx, music, video} и layer_gains "
                    "{id слоя: дБ} — поправки поверх громкости слоёв; target_lufs — громкость ролика. Проверь "
                    "audio_mix_report.")}
    unknown = [lid for lid in (args.get("layer_gains") or {}) if lid not in
               {str(layer.get("id")) for layer in render.get("layers") or []}]
    if unknown:
        out["warnings"] = [f"слоёв {', '.join(map(str, unknown[:8]))} нет в клипе — их поправки ничего не делают"]
    return out


# --------------------------------------------------------------------------- #
# Реестр
# --------------------------------------------------------------------------- #


def tools() -> list:
    at = _at()
    Tool, obj = at.Tool, at._obj
    key = obj({"t": {"type": "number"}, "gain_db": {"type": "number"},
               "ease": {"type": "string", "enum": ["linear", "smooth", "in", "out", "step"]}}, ["t", "gain_db"])
    span = obj({"start": {"type": "number"}, "end": {"type": "number"}, "gain_db": {"type": "number"}},
               ["start", "end"])
    return [
        Tool(
            "audio_map", "Смотрю карту звука",
            "Карта звука клипа (шкала clip; source=true или без клипа — окно исходника from_sec..to_sec, шкала "
            "source, ≤ 300 с): громкость LUFS по шагу 0.1–0.5 с (числа и строка ▁…█), отрезки «что звучит» "
            "(речь, тишина, громко, крик, смех?, музыка?), удары (hits), паузы речи (before_punch — перед ударом/"
            "громкой фразой), точки склейки (конец фразы +3 кадра), советы J/L на стыках кусков (jl), сетка долей "
            "(beats — музыка в звуке, music_beats — трек set_music в секундах готового ролика), по дорожкам "
            "записи отдельно (tracks). Зови перед монтажом звука и склейками; image=true — ещё и картинка "
            "(громкость, речь, удары, стыки). Кешируется. / Compact audio map: loudness, speech, pauses, hits, "
            "cut points, J/L hints, beats, per track.",
            obj({"from_sec": {"type": "number"}, "to_sec": {"type": "number"},
                 "image": {"type": "boolean", "description": "вернуть картинку карты"},
                 "source": {"type": "boolean", "description": "окно исходника, а не клип"},
                 **_at().SOURCE_ARGS,
                 "track": {"type": "integer", "description": "source: одна дорожка записи"}}),
            h_audio_map, scope="global",
            label=lambda a: "Смотрю карту звука" + (" видео" if a.get("source") else ""),
        ),
        Tool(
            "set_audio_joins", "J/L-склейки",
            "J/L на стыках кусков: joins [{piece, lead?, lag?}] (piece — номер куска с 0, секунды ≤ 1.5). lead "
            "(J) — звук куска начинается раньше его картинки, под концом прошлого куска; lag (L) — звук куска "
            "тянется под картинку следующего. Картинка режется как раньше, звук соседнего куска — из исходника "
            "за краем, с кроссфейдом 0.08 с. Типично 0.2–0.6 с: L — договорить фразу под реакцией, J — ответ "
            "слышен раньше смены плана, удар раньше картинки. Заменяет прежний список; [] — снять. Предупредит, "
            "какие слова не будет слышно. Субтитры идут за звуком: заменённые слова не показываются, слова ручки "
            "(звук из-за края куска) — показываются, когда их слышно, если распознаны: после новых J/L — "
            "generate_subtitles (распознаёт куски вместе с ручками). Советы — audio_map.jl. / J/L cuts at piece "
            "joins; captions follow the heard audio.",
            obj({"joins": {"type": "array", "items": obj({"piece": {"type": "integer"}, "lead": {"type": "number"},
                                                           "lag": {"type": "number"}}, ["piece"])}}, ["joins"]),
            h_set_audio_joins, mutating=True, label=lambda a: f"J/L на стыках ({len(a.get('joins') or [])})",
        ),
        Tool(
            "set_audio_keys", "Ключи громкости",
            "Громкость во времени ключами: target \"music\" (фоновая музыка, поверх ducking) или \"source\" (звук "
            "исходника), keys [{t, gain_db, ease}] — шкала clip, 0 дБ — как без ключей, −60 — тишина; ease "
            "linear/smooth/in/out/step. Пример: музыка тише к концу [{t:20,gain_db:0},{t:24,gain_db:-12,"
            "ease:smooth}]. Заменяет ключи этой цели; [] — снять. Выключить музыку перед ударом проще "
            "set_music_duck dips. / Volume automation keys.",
            obj({"target": {"type": "string", "enum": ["music", "source"]},
                 "keys": {"type": "array", "items": key}}, ["target", "keys"]),
            h_set_audio_keys, mutating=True,
            label=lambda a: f"Ключи громкости {a.get('target') or ''} ({len(a.get('keys') or [])})",
        ),
        Tool(
            "set_music_duck", "Приглушаю музыку",
            "Музыка поверх автоматики: duck [{start, end, gain_db}] — ручной ducking (тише на отрезке, −6…−20 дБ; "
            "автоматический под речью остаётся); dips [{at | at_word, before}] — «тишина перед ударом»: музыка "
            "уходит в ноль за before (0.3–0.8 с) до at и возвращается ровно на at (панчлайн, удар, дроп). Шкала "
            "clip. Каждый переданный список заменяет прежний; [] — снять. Паузы перед панчлайном — audio_map "
            "pauses before_punch. / Manual music ducking and pre-hit dropouts.",
            obj({"duck": {"type": "array", "items": span},
                 "dips": {"type": "array", "items": obj({"at": {"type": "number"}, "before": {"type": "number"},
                                                          "at_word": at._AT_WORD_SCHEMA})}}),
            h_set_music_duck, mutating=True,
            label=lambda a: "Приглушаю музыку" + (f" ({len(a.get('dips') or [])} провалов)" if a.get("dips") else ""),
        ),
        Tool(
            "set_audio_fx", "Звуковые эффекты",
            "Эффекты звука исходника на отрезках fx [{start, end, fx, track?, amount?, rate?, keep_pitch?}] (шкала "
            "clip; или at_word + duration): muffle — «за стеной», underwater — «под водой», echo — эхо на слове, "
            "reverb — зал, phone — телефон, radio — рация, denoise — шумодав микрофона (track — дорожка "
            "микрофона), speed — замедлить/ускорить звук (rate 0.5–2, длина отрезка та же), reverse — задом "
            "наперёд, beep — писк вместо звука, noise — розовый шум вместо звука, chain — любая цепочка "
            "эффектов (chain [{fx, …}] или preset: эквалайзер, компрессор, ревёрб, свип фильтра… — "
            "audio_fx_reference). amount 0..1 — сила (0.5). 0–2 эффекта на ролик, на смысловом моменте. Заменяет список; [] — "
            "снять. Проверь listen. / Audio effects on ranges (lowpass, echo, reverb, phone, radio, denoise, "
            "speed, reverse).",
            obj({"fx": {"type": "array", "items": obj({
                "start": {"type": "number"}, "end": {"type": "number"},
                "fx": {"type": "string", "enum": ["muffle", "underwater", "echo", "reverb", "phone", "radio",
                                                  "denoise", "speed", "reverse", "beep", "noise", "chain"]},
                "chain": {"type": "array", "items": {"type": "object"},
                          "description": "fx chain: цепочка [{fx, …}] (audio_fx_reference)"},
                "preset": {"type": "string", "description": "fx chain: имя пресета (audio_fx_reference)"},
                "track": {"type": "integer"}, "amount": {"type": "number"}, "rate": {"type": "number"},
                "keep_pitch": {"type": "boolean"}, "at_word": at._AT_WORD_SCHEMA,
                "duration": {"type": "number"}}, ["fx"])}}, ["fx"]),
            h_set_audio_fx, mutating=True, label=lambda a: f"Звуковые эффекты ({len(a.get('fx') or [])})",
        ),
        Tool(
            "audio_mix_report", "Слушаю сведение",
            "ЗВУК ГЛАЗАМИ: громко ли музыка под голосом, не тонут ли слова под звуками, готов ли ролик по громкости. "
            "Раскладывает звук клипа на стемы так, как их сведёт рендер (голос-слои / звук исходника / музыка / звуки "
            "/ звук видео-слоёв) и меряет ffmpeg ebur128: integrated LUFS и true peak каждого стема, short-term по "
            "времени, и ГЛАВНОЕ — голос против музыки только там, где говорят (voice_vs_music.diff_db: норма ≥ 12 дБ, "
            "цель 15–18), звуки против речи (voice_vs_sfx.masking_at, layers[].excess_db), итог ролика (−14 LUFS, "
            "true peak ≤ −1 dBTP) по свежему рендеру или прогнозом, клиппинг, дыры тишины. verdicts — по-русски, "
            "с готовой поправкой (suggest: auto_mix или set_mix с числами). Зови после сборки звука и перед "
            "сдачей; исправляет auto_mix. render_id — какой рендер мерить (по умолчанию свежий последний); "
            "use_render=false — только модель; timeline=true — числа short-term. / Loudness & mix report by stems: "
            "voice-vs-music under speech, sfx masking, LUFS, true peak.",
            obj({"render_id": {"type": "integer"}, "use_render": {"type": "boolean"},
                 "timeline": {"type": "boolean", "description": "числа short-term LUFS по времени"}}),
            h_audio_mix_report, label=lambda a: "Слушаю сведение",
        ),
        Tool(
            "auto_mix", "Свожу звук",
            "АВТОСВЕДЕНИЕ по замеру стемов (как audio_mix_report): громкость музыки так, чтобы под речью она была на "
            "music_under_voice_db тише голоса (−18 по умолчанию; −12 — смелее, −22 — тише), ducking музыки на каждую "
            "фразу (duck_db −10, атака 0.12 с, отпускание 0.3 с; sidechain выключается), каждый звук слоя (вжухи, "
            "удары) — под голос (пик на sfx_under_voice_db −6 ниже речи там, где говорят; в паузе чуть громче), "
            "звук видео-слоёв — фоном, цель ролика target_lufs (−14 TikTok/Reels/Shorts) с true peak ≤ −1 dBTP в "
            "финальном проходе. Считает заново от исходной громкости (повтор не накапливает). dry_run=true — только "
            "прогноз. Ответ: changes, before/after (голос над музыкой, LUFS, пик, предупреждения). Потом — рендер и "
            "audio_mix_report. / Auto-mix: music level + per-phrase ducking, sfx under voice, loudness target.",
            obj({"target_lufs": {"type": "number"}, "music_under_voice_db": {"type": "number"},
                 "duck_db": {"type": "number"}, "sfx_under_voice_db": {"type": "number"},
                 "dry_run": {"type": "boolean"}}),
            h_auto_mix, mutating=True,
            label=lambda a: "Прикидываю сведение" if a.get("dry_run") else "Свожу звук",
        ),
        Tool(
            "set_mix", "Ручки сведения",
            "Ручное сведение (то же, что выставляет auto_mix): music_db — громкость фонового трека в дБ к файлу "
            "(−40..+12, null — снова подгонка под голос), sidechain false/true — автоматический ducking музыки под "
            "речью, duck_attack/duck_release — рампы ручного ducking (с), groups {voice, sfx, music, video: дБ} и "
            "layer_gains {id слоя: дБ} — поправки поверх громкости слоёв (null — снять), target_lufs — громкость "
            "ролика (−24..−9), reset=true — сначала всё снять. Переданные поля меняются, остальные остаются. "
            "Проверь audio_mix_report. / Manual mix knobs.",
            obj({"music_db": {"type": ["number", "null"]}, "target_lufs": {"type": ["number", "null"]},
                 "sidechain": {"type": "boolean"}, "duck_attack": {"type": ["number", "null"]},
                 "duck_release": {"type": ["number", "null"]},
                 "groups": {"type": "object", "additionalProperties": {"type": ["number", "null"]}},
                 "layer_gains": {"type": "object", "additionalProperties": {"type": ["number", "null"]}},
                 "reset": {"type": "boolean"}}),
            h_set_mix, mutating=True, label=lambda a: "Кручу ручки сведения",
        ),
        Tool(
            "audio_fx_reference", "Смотрю звуковые эффекты",
            "Справочник звуковых эффектов: виды (eq — срез низа/верха, полки, presence, полосы; highpass/lowpass со "
            "свипом to; compressor; deesser; gate; denoise; reverb room/hall/plate/cave; delay; pitch (полутона, "
            "длина та же); speed (с тоном) / tempo (без тона); stereo width; distortion; bitcrush (лоу-фай); "
            "tremolo; reverse; gain; legacy — эффекты set_audio_fx), параметры с пределами и умолчаниями, готовые "
            "пресеты (telephone, radio, underwater, muffle, megaphone, lofi, voice_clean, voice_broadcast, "
            "voice_warm, small_room, hall, cave, dream, echo, slapback, monster, chipmunk, robot, sweep_in, "
            "sweep_out, thin_out, slowdown), где применять (слой / голос / музыка / отрезок) и звуки auto_sfx. "
            "/ Audio effects reference.",
            obj({}), h_audio_fx_reference, scope="global", label=lambda a: "Смотрю звуковые эффекты",
        ),
        Tool(
            "set_audio_chain", "Эффекты звука",
            "Цепочка эффектов: target \"layer\" + layer_id — звуковой слой (audio или video с audio:true), "
            "\"voice\" — голос клипа (холст: слои-голос — озвучка, реплики; клип с исходником — весь его звук, "
            "после распознавания субтитров), \"music\" — фоновый трек. chain [{fx, …}] (по порядку: eq → "
            "compressor → deesser → reverb/delay) или preset (имя из audio_fx_reference); append=true — дописать "
            "к текущей; [] или null — снять. Свип фильтра на переход: {fx:\"lowpass\", f:300, to:18000, "
            "sweep_end:0.6} или preset sweep_in/sweep_out. У голоса и музыки нет speed/tempo/reverse (только у "
            "слоя). Потом audio_mix_report. / Effects chain for a layer, the voice or the music.",
            obj({"target": {"type": "string", "enum": ["layer", "voice", "music"]}, "layer_id": {"type": "string"},
                 "chain": {"type": "array", "items": {"type": "object"}}, "preset": {"type": "string"},
                 "append": {"type": "boolean"}}, ["target"]),
            h_set_audio_chain, mutating=True,
            label=lambda a: f"Эффекты звука: {a.get('preset') or a.get('target') or ''}".strip(),
        ),
        Tool(
            "auto_sfx", "Расставляю звуки",
            "Звуки на монтаж — ТОЛЬКО настоящие записи пака freesound (синтетику не подставляет): on [\"cuts\", "
            "\"badges\"] — вжух на каждой склейке (стыки кусков; у холста — смены картинок/видео), «поп» на "
            "появлении плашек/титров; riser_at [t] — райзер, кончающийся ровно на t (перед дропом/панчлайном); "
            "impact_at [t] — удар на t. Пропускает места, где звук уже стоит, держит паузу ≥ 0.5 с. Слои asfx_* "
            "(эффекты: громкость под голос держит сервис), повтор заменяет прежние. dry_run=true — только список. "
            "Потом auto_mix. / Auto-place real whooshes/pops/risers/impacts.",
            obj({"on": {"type": "array", "items": {"type": "string", "enum": ["cuts", "badges"]}},
                 "riser_at": {"type": "array", "items": {"type": "number"}},
                 "impact_at": {"type": "array", "items": {"type": "number"}},
                 "dry_run": {"type": "boolean"}}),
            h_auto_sfx, mutating=True, label=lambda a: "Расставляю звуки",
        ),
        Tool(
            "audio_crossfade", "Кроссфейд звуков",
            "Наплыв между двумя audio-слоями: from_layer затухает, to_layer нарастает за duration (0.5 с); если "
            "слои не перекрываются — from_layer дотягивается под начало to_layer (to_layer стоит на месте). "
            "/ Crossfade two audio layers.",
            obj({"from_layer": {"type": "string"}, "to_layer": {"type": "string"}, "duration": {"type": "number"}},
                ["from_layer", "to_layer"]),
            h_audio_crossfade, mutating=True, label=lambda a: "Кроссфейд звуков",
        ),
    ]
