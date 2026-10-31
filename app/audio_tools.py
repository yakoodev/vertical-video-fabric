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
            project = at._project_or_clip_source(ctx, args)
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
    lengths = [float(s["end_sec"]) - float(s["start_sec"]) for s in segments]
    plan = plan_jl(lengths, part_overlaps(segments, render.get("transition")), shifts)
    for k, (want, got) in enumerate(zip(shifts, plan["shift"])):
        if abs(want - got) > 0.01:
            warnings.append(f"стык {k}→{k + 1}: сдвиг {want:+.2f} с урезан до {got:+.2f} (J/L не больше 45% "
                            "соседнего куска)")
    words = (ctx.store.get_clip_plan_subtitles(ctx.plan_id) or {}).get("words") or []
    report = jl_speech_report(segments, saved.get("joins"), words, render.get("transition"))
    for row in report:
        if row["lost_words"]:
            warnings.append(f"стык {row['join']}→{row['join'] + 1} ({row['kind']} {abs(row['shift']):.2f} с): не будет "
                            f"слышно {row['hidden']} — слова «{' '.join(row['lost_words'])}» (субтитры их покажут); "
                            "ставь J/L на паузу (audio_map: pauses, jl) или подрежь кусок")
    out = {"ok": True, "joins": view, "joins_report": report,
           "note": ("J/L: картинка режется как раньше, звук соседнего куска наезжает на стык с кроссфейдом "
                    "0.08 с (под переходом — его длиной). lead — звук куска раньше его картинки (J), lag — звук "
                    "тянется под следующую картинку (L); на одном стыке lag A и lead B вычитаются. Звук каждого "
                    "куска остаётся синхронным со своей картинкой: субтитры не сдвигаются. Превью редактора "
                    "играет J/L вторым плеером. Проверь listen на стыке.")}
    if warnings:
        out["warnings"] = warnings
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
                 "project_id": {"type": "integer"},
                 "track": {"type": "integer", "description": "source: одна дорожка записи"}}),
            h_audio_map, scope="global",
            label=lambda a: "Смотрю карту звука" + (" исходника" if a.get("source") else ""),
        ),
        Tool(
            "set_audio_joins", "J/L-склейки",
            "J/L на стыках кусков: joins [{piece, lead?, lag?}] (piece — номер куска с 0, секунды ≤ 1.5). lead "
            "(J) — звук куска начинается раньше его картинки, под концом прошлого куска; lag (L) — звук куска "
            "тянется под картинку следующего. Картинка режется как раньше, звук соседнего куска — из исходника "
            "за краем, с кроссфейдом 0.08 с. Типично 0.2–0.6 с: L — договорить фразу под реакцией, J — ответ "
            "слышен раньше смены плана, удар раньше картинки. Заменяет прежний список; [] — снять. Предупредит, "
            "какие слова не будет слышно. Советы — audio_map.jl. / J/L cuts at piece joins.",
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
            "наперёд, beep — писк вместо звука, noise — розовый шум вместо звука. amount 0..1 — сила (0.5). 0–2 эффекта на ролик, на смысловом моменте. Заменяет список; [] — "
            "снять. Проверь listen. / Audio effects on ranges (lowpass, echo, reverb, phone, radio, denoise, "
            "speed, reverse).",
            obj({"fx": {"type": "array", "items": obj({
                "start": {"type": "number"}, "end": {"type": "number"},
                "fx": {"type": "string", "enum": ["muffle", "underwater", "echo", "reverb", "phone", "radio",
                                                  "denoise", "speed", "reverse", "beep", "noise"]},
                "track": {"type": "integer"}, "amount": {"type": "number"}, "rate": {"type": "number"},
                "keep_pitch": {"type": "boolean"}, "at_word": at._AT_WORD_SCHEMA,
                "duration": {"type": "number"}}, ["fx"])}}, ["fx"]),
            h_set_audio_fx, mutating=True, label=lambda a: f"Звуковые эффекты ({len(a.get('fx') or [])})",
        ),
    ]
