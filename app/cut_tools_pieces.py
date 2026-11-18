"""Инструменты монтажа: переходы, обрезка, скорость, посадка, вставки (из ``app/cut_tools.py``)."""

from __future__ import annotations

from typing import Any

from app.asset_tools import people_gate
from app.layer_lint import FULL_INSERT_MAX_SEC
from app.subtitle_tools import word_at
from app.tool_core import (
    _apply_piece, _clock, _current_render, _foreign_asset_problem, _num, _piece_index, _save_render,
    _shared_warning, clip_timeline, plan_clock, ToolContext, ToolError
)

def h_set_transition(ctx: ToolContext, args: dict) -> dict:
    from app.transitions import AUDIO_MODES, SFX_KINDS, TRANSITIONS

    from app.transitions import resolve_kind

    render = _current_render(ctx)
    if args.get("type") not in (None, "inherit"):
        # Шейдерные переходы — и по-русски («шторка», «стекло», «волна»): в настройки — ключ gl_….
        args = {**args, "type": resolve_kind(args["type"])}
    if args.get("join") is not None:
        return _set_join_transition(ctx, render, args)
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
    if args.get("scope") is not None:
        # Раскладка «полоса» (meme): переход только в полосе видео или во весь кадр (app/transitions.py).
        if str(args["scope"]) not in ("band", "frame"):
            problems.append("scope: band (только в полосе видео) или frame (во весь кадр)")
        transition["scope"] = args["scope"]
    from app.transitions import FX_DURATIONS

    if args.get("type") in FX_DURATIONS and args["type"] != (render.get("transition") or {}).get("type") \
            and args.get("duration") is None:
        transition.pop("duration", None)  # у вида кадрами — его длина по умолчанию (срез 0.2 с, панели 0.4 с)
    problems += _fx_args(args, transition)
    if problems:
        raise ToolError("переход не принят", problems)
    return _save_transition(ctx, render, transition, args)


def _save_transition(ctx: ToolContext, render: dict, transition: dict, args: dict) -> dict:
    """Проверки речи и мувика, fit_speech, сохранение — общее у общего перехода и перехода стыка."""
    from app.transitions import FRAG_REJECTED, MIN_DURATION, SNAP_DURATIONS, normalize_transition, \
        speech_join_warnings, speech_under_joins

    plan = ctx.store.get_clip_plan(ctx.plan_id)
    segments = plan.get("segments") or []
    words = (ctx.store.get_clip_plan_subtitles(ctx.plan_id) or {}).get("words") or []
    notes = []
    normal = normalize_transition(transition)
    kind = normal["type"]
    kinds = {kind, *(j["type"] for j in normal.get("joins") or [])}
    rule = []
    if any(k in FRAG_REJECTED for k in kinds):
        game = gaming_hint(ctx)
        if game:
            bad = ", ".join(sorted(k for k in kinds if k in FRAG_REJECTED))
            rule.append(f"{bad} в записи игры ({game}): белые вспышки и зум-панчи владелец забраковал для мувиков "
                        "(журнал монтажа 15.11) — type frag (срез по движению камеры, панели, искры) или slash")
    if args.get("fit_speech") and args.get("join") is not None:
        notes.append("fit_speech — у общего перехода; у стыка задай duration сам (см. warnings)")
    elif args.get("fit_speech"):
        # Наплыв не должен уводить слова под переход: длину — по самому тесному стыку.
        report = speech_under_joins(segments, transition, words)
        if report["hit"] and kind == "frag":
            notes.append("у frag длина у каждого вида своя — fit_speech её не меняет: подрежь куски (см. warnings) "
                         "или поставь slash с duration")
        elif report["hit"] and kind not in SNAP_DURATIONS:
            if report["safe_duration"] >= MIN_DURATION:
                transition["duration"] = report["safe_duration"]
                notes.append(f"длина перехода {report['safe_duration']:.2f} с — наплыв не заходит на речь")
            else:
                notes.append("на каком-то стыке речь вплотную к склейке — переход любой длины заденет слова; "
                             "подрежь куски (см. warnings) или поставь type cut")
    fresh = _save_render(ctx, {**render, "transition": transition})
    out: dict = {"ok": True, "transition": fresh["transition"]}
    warnings = rule + speech_join_warnings(speech_under_joins(segments, fresh["transition"], words),
                                           fresh["transition"])
    if warnings:
        out["warnings"] = warnings
    if notes:
        out["note"] = "; ".join(notes)
    return out


def _set_join_transition(ctx: ToolContext, render: dict, args: dict) -> dict:
    """Переход одного стыка (или нескольких): ``join`` — номер стыка кусков, свой вид и параметры в
    ``transition.joins``; ``type: inherit`` — снять, стык снова как общий переход."""
    from app.transitions import TRANSITIONS, normalize_direction

    plan = ctx.store.get_clip_plan(ctx.plan_id)
    count = max(0, len(plan.get("segments") or []) - 1)
    raw = args["join"] if isinstance(args["join"], list) else [args["join"]]
    problems: list[str] = []
    targets: list[int] = []
    for item in raw:
        try:
            index = int(item)
        except (TypeError, ValueError):
            problems.append(f"join: номер стыка 0..{max(0, count - 1)} (0 — между кусками 0 и 1)")
            continue
        if not 0 <= index < count:
            problems.append(f"join {index}: у клипа {count} стык(ов) — 0..{max(0, count - 1)}"
                            if count else "join: у клипа один кусок — стыков нет")
        targets.append(index)
    for key in ("sfx", "sfx_volume", "audio", "smart", "scope", "motion_blur", "kinds", "fit_speech"):
        if args.get(key) is not None and key != "fit_speech":
            problems.append(f"{key} — у общего перехода (вызови set_transition без join)")
    transition = dict(render.get("transition") or {})
    joins = [dict(j) for j in transition.get("joins") or [] if isinstance(j, dict)]
    kind = args.get("type")
    if kind not in (None, "inherit") and (kind not in TRANSITIONS or kind == "frag"):
        problems.append(f"type стыка: inherit (как общий) или одно из {', '.join(k for k in TRANSITIONS if k != 'frag')}")
    if args.get("direction") is not None and args["direction"] not in ("", "auto") \
            and not normalize_direction(args["direction"]):
        problems.append("direction: left, right, up, down (куда едет картинка) или auto")
    if problems:
        raise ToolError("переход стыка не принят", problems)
    for index in targets:
        old = next((j for j in joins if j.get("join") == index), None)
        joins = [j for j in joins if j.get("join") != index]
        if kind == "inherit":
            continue
        entry = dict(old or {"join": index, "type": kind or str(transition.get("type") or "cut")})
        if kind is not None and kind != entry.get("type"):
            entry = {"join": index, "type": kind, **({"reason": old["reason"]} if old and old.get("reason") else {})}
        if entry["type"] == "frag":
            entry["type"] = "slash"
        if args.get("duration") is not None:
            entry["duration"] = args["duration"]
        problems += _fx_args({k: v for k, v in args.items() if k != "kinds"}, entry)
        if args.get("direction") is not None:
            if args["direction"] in ("", "auto"):
                entry.pop("direction", None)
            else:
                entry["direction"] = normalize_direction(args["direction"])
        if args.get("reason") is not None:
            text = " ".join(str(args["reason"]).split())[:160]
            if text:
                entry["reason"] = text
            else:
                entry.pop("reason", None)
        joins.append(entry)
    if problems:
        raise ToolError("переход стыка не принят", problems)
    transition["joins"] = sorted(joins, key=lambda j: int(j["join"]))
    if not transition["joins"]:
        transition.pop("joins")
    out = _save_transition(ctx, render, transition, args)
    out["joins"] = out["transition"].get("joins") or []
    return out


def _fx_args(args: dict, transition: dict) -> list[str]:
    """Поля переходов мувика (angle, color, kinds, panels) → в ``transition``; ответ — что не так."""
    from app.transitions import FX_DURATIONS

    problems = []
    if args.get("angle") is not None:
        if args["angle"] in ("", "auto"):
            transition.pop("angle", None)
        elif _num(args["angle"]) is None:
            problems.append("angle: градусы (0 — вправо, 90 — вниз) или \"auto\" — по движению камеры")
        else:
            transition["angle"] = _num(args["angle"])
    if args.get("color") is not None:
        text = str(args["color"]).strip()
        if text in ("", "auto", "accent"):
            transition.pop("color", None)  # заливка — цветом бренда (accent) или фирменным
        else:
            if len(text.lstrip("#")) != 6 or any(ch not in "0123456789abcdefABCDEF" for ch in text.lstrip("#")):
                problems.append("color: #rrggbb (или accent — цвет бренда)")
            transition["color"] = text
    if args.get("direction") is not None and args.get("join") is None:
        from app.transitions import normalize_direction

        if args["direction"] in ("", "auto"):
            transition.pop("direction", None)
        elif not normalize_direction(args["direction"]):
            problems.append("direction: left, right, up, down (куда едет картинка) или auto — по движению камеры")
        else:
            transition["direction"] = normalize_direction(args["direction"])
    if args.get("kinds") is not None:
        from app.transitions import FRAG_KINDS

        kinds = args["kinds"] if isinstance(args["kinds"], list) else []
        bad = [str(k) for k in kinds if str(k) not in FRAG_KINDS]
        if bad or not kinds:
            problems.append(f"kinds: список из {', '.join(FRAG_KINDS)}")
        transition["kinds"] = kinds
    if args.get("panels") is not None:
        if args["panels"] not in (2, 3):
            problems.append("panels: 2 или 3")
        transition["panels"] = args["panels"]
    return problems


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

# Правило владельца по скорости (журнал монтажа 15.11, плейбук frag-movie): мувик игры — 0.87–1.15×, рамп к
# ключевому моменту; почти-стоп (слоу-мо до стоп-кадра под текст) он забраковал: «клип просто остановился».
FRAG_SPEED_MIN, FRAG_SPEED_MAX = 0.87, 1.15
NEAR_FREEZE_SPEED = 0.3
FRAG_SPEED_RULE = ("правило владельца для мувиков игры (журнал монтажа 15.11, плейбук frag-movie): скорость "
                   "0.87–1.15× — умный рамп к ключевому моменту (подход чуть быстрее, нок чуть медленнее); стрельбу "
                   "резко не ускорять, стоп-кадров и почти-стопа нет, под текст не замедлять — пусть идёт как есть")
NEAR_FREEZE_RULE = ("медленнее 0.3× — почти стоп-кадр: владелец забраковал «клип просто остановился» (журнал "
                    "монтажа 15.11, мувик BURN v3). Замедляй умеренно (подход к моменту), а нужна пауза — "
                    "осознанный freeze_frame; проверь рендер lint_clip(checks: [\"freezes\"])")
# Не «фраг» само по себе: «фрагмент» в названии — не игра.
FRAG_WORDS = ("мувик", "frag movie", "frag-movie", "fragmovie", "геймплей", "gameplay", "мем-эдит", "meme edit")


def gaming_hint(ctx: ToolContext) -> str | None:
    """Клип — запись игры или мувик? Что нашлось (для текста предупреждения) или None: игра от первого лица в
    имени файла/тегах/названии (``first_person_hint``), слово «мувик»/«фраг» в названии клипа, события игры
    в кэше (``game_events`` уже разбирал запись — только Apex)."""
    return gaming_hint_for(ctx.store, ctx.plan_id)


def gaming_hint_for(store: Any, plan_id: int) -> str | None:
    """``gaming_hint`` по хранилищу и номеру клипа (для ИИ-монтажа вне инструментов)."""
    try:
        plan = store.get_clip_plan(plan_id, include_segments=False)
        source = store.get_source(int(plan["source_id"]))
    except (KeyError, TypeError, ValueError):
        return None
    from app.first_person import first_person_hint, source_texts

    hint = first_person_hint(*source_texts(source, plan))
    if hint:
        return hint
    title = f"{plan.get('title') or ''} {plan.get('description') or ''}".lower()
    word = next((w for w in FRAG_WORDS if w in title), None)
    if word:
        return word
    try:
        from app.game_events_job import cached

        if cached(store, int(source["id"])):
            return "события игры"
    except Exception:  # noqa: BLE001 - нет кэша/файла — не игра по этому признаку
        pass
    return None


def speed_warnings(speed: float, game: str | None) -> list[str]:
    """Предупреждения о скорости по правилам владельца (не запрет): почти-стоп всегда, вне 0.87–1.15× — у игры."""
    out = []
    if speed < NEAR_FREEZE_SPEED - 1e-9:
        out.append(f"×{speed:g}: {NEAR_FREEZE_RULE}")
    elif speed < 0.5:
        out.append("медленнее 0.5 при 30 к/с — кадры повторяются, движение «ступеньками»; для слоу-мо лучше "
                   "исходник 60 к/с")
    if game and abs(speed - 1.0) > 1e-9 and not FRAG_SPEED_MIN - 1e-9 <= speed <= FRAG_SPEED_MAX + 1e-9:
        out.append(f"×{speed:g} вне 0.87–1.15× для игры ({game}): {FRAG_SPEED_RULE}. Повтор килла в слоу-мо — "
                   "только если владелец просил")
    return out


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
    warnings = speed_warnings(speed, gaming_hint(ctx))
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
    from app.video_memes import meme_for_asset, MEME_FULL_MAX_SEC

    meme = meme_for_asset(ctx.store, asset["id"])
    if meme and not meme["approved"]:
        # Неодобренный видеомем ИИ не ставит никогда (app/video_memes.py): одобряет владелец в «Видеомемах».
        raise ToolError(f"видеомем #{meme['id']} «{meme['title']}» владелец ещё не одобрил — ставить нельзя. "
                        "Возьми одобренный (search_video_memes) или попроси владельца одобрить этот")
    mode = args.get("mode") or ("sound" if asset["kind"] == "audio" else "pip")
    if mode not in MODES:
        raise ToolError(f"mode: одно из {', '.join(MODES)}")
    if meme and mode == "full" and (_num(args.get("duration"), 1.5) or 1.5) > MEME_FULL_MAX_SEC + 1e-6:
        raise ToolError(f"врезка видеомема во весь кадр — не дольше {MEME_FULL_MAX_SEC:g} с; дольше — mode pip")
    if (not meme and mode == "full"
            and (_num(args.get("duration"), 1.5) or 1.5) > FULL_INSERT_MAX_SEC + 1e-6):
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
    entry = {k: args[k] for k in ("at", "duration", "x", "y", "scale", "volume", "duck", "reason", "fill",
                                  "duck_volume", "level", "crop") if args.get(k) is not None}
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
