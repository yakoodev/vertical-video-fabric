"""Инструменты звука клипа: что звучит и где шум, глушение, цензура, музыка и звуки, дорожки и громкость исходника.

Звуковой монтаж (карта звука, J/L, ключи, эффекты) отдельно — ``app/audio_tools.py``. Входят в общий реестр
(``app/agent_tools.py``)."""

from __future__ import annotations

from app.promo_lint import has_device_layers
from app.subtitle_tools import censor_keep_words
from app.tool_core import (
    _assets_project, _clip_duration, _current_render, _layers_of, _num, _obj, _save_render, _source_canvas,
    _source_or_clip_source, ASSET_OWNER_PARAMS, SOURCE_ARGS, Tool, ToolContext, ToolError,
)


def _audio_view(render: dict, segments: list[dict]) -> dict:
    from app.audio_tools import audio_view

    return audio_view(render, segments)


def _source_audio_view(source: dict) -> dict:
    """Звуковые дорожки источника: сколько и какие выбраны (игра + микрофон — set_source_audio)."""
    from app.source_audio import audio_view

    try:
        return audio_view(source)
    except Exception:  # noqa: BLE001 - справка, не роняет get_clip
        return {}


def _music_warning(ctx: ToolContext, render: dict) -> str | None:
    """Промо под тихое вступление трека: «Бодрый фон» с 0 с — барабаны только с 10 с."""
    if not render.get("music_on") or not render.get("track_id") or not _is_promo(ctx, render):
        return None
    from app.music_energy import ensure_energy, quiet_intro_sec

    try:
        [track] = ensure_energy(ctx.store, [ctx.store.get_audio_track(int(render["track_id"]))])
    except (KeyError, ValueError, TypeError):
        return None
    start = float(render.get("music_start_sec") or 0.0)
    quiet = quiet_intro_sec(track, start)
    if not quiet:
        return None
    energy = float(track["energy_start_sec"])
    return (f"Музыка: первые {quiet:g} с музыки тихие (вступление трека, энергичная часть с {energy:g} с) — "
            f"set_music(start_sec={energy:g})")


def h_listen(ctx: ToolContext, args: dict) -> dict:
    from app.clip_subtitles import listen_range

    try:
        return listen_range(ctx.store, ctx.plan_id, _num(args.get("from_sec")), _num(args.get("to_sec")),
                            before_cuts=bool(args.get("before_cuts")))
    except ValueError as exc:
        raise ToolError(f"listen: {exc}") from exc


def h_find_noises(ctx: ToolContext, args: dict) -> dict:
    from app.clip_subtitles import find_noises

    try:
        # Порог не задан — от медианы звука клипа (игра громкая, разговор — тихий).
        return find_noises(ctx.store, ctx.plan_id, _num(args.get("threshold_db")))
    except ValueError as exc:
        raise ToolError(f"find_noises: {exc}") from exc


def h_list_music(ctx: ToolContext, _args: dict) -> dict:
    """Фоновая музыка сервиса (пак CC0 + загруженная владельцем) с отметками энергии."""
    from app.music_energy import energy_view, ensure_energy

    tracks = [
        {"id": t["id"], "label": t.get("label") or "", "duration": round(float(t.get("duration_sec") or 0), 1),
         **energy_view(t)}
        for t in ensure_energy(ctx.store)  # старые треки размечаются лениво, по одному
    ]
    return {"tracks": tracks,
            "note": ("громкость фона выставляется сама — заметно тише голоса клипа и приглушается под речь. "
                     "energy_start_sec — с какой секунды трек звучит в полную силу (до неё тихое вступление), "
                     "drop_sec — самый сильный подъём, bpm — темп. Промо/холст: set_music(start_sec=energy_start_sec)")}


def _music_track_hint(track: dict, start: float, promo: bool) -> dict:
    """Отметки трека и подсказка «пропусти тихое вступление» (start_sec не меняем сами)."""
    from app.music_energy import energy_view, quiet_intro_sec

    out: dict = {}
    marks = energy_view(track)
    if marks:
        out["track_energy"] = marks
    quiet = quiet_intro_sec(track, start)
    if quiet and promo:
        energy = marks.get("energy_start_sec")
        out["hint"] = (f"первые {quiet:g} с трека тихие — энергичная часть с {energy:g} с: "
                       f"set_music(start_sec={energy:g})")
    return out


def h_mute_audio(ctx: ToolContext, args: dict) -> dict:
    """Тишина вместо звука исходника на отрезках (секунды клипа до вырезов), картинка остаётся."""
    from app.audio_mute import normalize_mute

    ranges = args.get("ranges")
    if not isinstance(ranges, list):
        raise ToolError("ranges — список {start, end} (секунды клипа до вырезов; [] — снять всё)")
    duration = _clip_duration(ctx)
    clean = [m for m in normalize_mute(ranges) if m["start"] < duration]
    render = _current_render(ctx)
    note = "звук исходника там заглушён; звуки слоёв и музыка играют. Проверь listen (он слышит mute)."
    # Отрезки цензуры мата (by: censor, app/censor.py) — свои: список без помеченных их не стирает.
    censored = [m for m in render.get("mute") or [] if m.get("by") == "censor"]
    if censored and not any(m.get("by") for m in clean):
        clean = normalize_mute(clean + censored)
        note += (f" Отрезки мата ({len(censored)}, by: censor) оставлены — снять их: "
                 "censor_profanity(clear=true).")
    fresh = _save_render(ctx, {**render, "mute": clean})
    out = {"ok": True, "mute": fresh["mute"], "note": note}
    warnings = _track_warnings(ctx, fresh["mute"])
    if warnings:
        out["warnings"] = warnings
    return out


def _track_warnings(ctx: ToolContext, spans: list[dict]) -> list[str]:
    """Отрезки дорожки, которой нет в звуке ролика (set_source_audio), ничего не изменят."""
    from app.source_audio import source_tracks

    tracks = {s["track"] for s in spans if s.get("track") is not None}
    if not tracks:
        return []
    plan = ctx.store.get_clip_plan(ctx.plan_id, include_segments=False)
    mix = source_tracks(ctx.store.get_source(int(plan["source_id"]))) or [0]
    missing = sorted(t for t in tracks if t not in mix)
    return ([f"дорожек {missing} нет в звуке ролика (смесь {mix}) — их отрезки ничего не изменят; "
             "set_source_audio tracks"] if missing else [])


def h_censor_profanity(ctx: ToolContext, args: dict) -> dict:
    """Мат одной командой (app/censor.py): тишина серединой слова, звёздочки в субтитрах и,
    если verify, круги «рендер → Whisper по готовому файлу → шире, где узнаётся»."""
    from app import censor

    notes: list[str] = []
    soft = bool(args.get("soft"))
    groups = args.get("groups") or None
    if ctx.request and not censor.owner_asks_soft(ctx.request):
        # QA 4 (клип 896): soft включил агент сам — заглушил панчлайн «обосралась». Мягкая брань (блин,
        # жопа, срать) — не мат, площадки за неё не режут; глушим её, только если владелец просил.
        if soft or (isinstance(groups, (list, tuple)) and "soft" in groups):
            notes.append("мягкая брань (soft: блин, жопа, срать…) — не мат; владелец её глушить не просил — не глушу")
        soft = False
        if isinstance(groups, (list, tuple)):
            groups = [g for g in groups if g != "soft"] or None
    options = {"stars": args.get("stars", True) is not False, "soft": soft,
               "groups": groups, "skip": args.get("skip") or ()}
    keep = censor_keep_words(ctx)
    if keep:
        options["keep"] = keep
    if args.get("force_words"):
        options["force_words"] = [int(i) for i in args.get("force_words") or () if str(i).lstrip("-").isdigit()]
    if args.get("track") not in (None, ""):
        options["track"] = args.get("track")  # только эта дорожка (число или "voice"), app/censor.py
    if args.get("style") not in (None, ""):
        try:
            options["style"] = censor.clamp_style(args.get("style"))  # чем заполнить середину слова
        except ValueError as exc:
            raise ToolError(f"censor_profanity: {exc}") from exc
    levels = args.get("levels")
    if args.get("level") is not None and levels is None:
        levels = args.get("level")
    if censor.job_running(ctx.plan_id):
        if args.get("verify", True) is False or args.get("clear"):
            raise ToolError("идёт проверка цензуры этого клипа (рендер + Whisper) — дождись её: "
                            "повтори censor_profanity без verify=false")
    # QA 4 (клип 896): 5 вызовов подряд — явные levels/style/groups обходили «уже применено». Теперь
    # повторный вызов при стоящей цензуре — «уже применено», что бы ни передали; переделать — redo: true.
    redo = any(args.get(k) not in (None, "", [], False) for k in ("redo", "overwrite", "clear", "verify_again",
                                                                    "force_words"))
    if not redo and args.get("stars", True) is not False:
        try:
            done = censor.already_censored(ctx.store, ctx.plan_id, keep=keep)
        except Exception:  # noqa: BLE001 - не смогли сверить — делаем как обычно
            done = None
        if done:
            explicit = [k for k in ("levels", "level", "style", "groups", "soft", "skip", "track")
                        if args.get(k) not in (None, "", [], False)]
            if explicit:
                done["note"] += (f". Переданные {', '.join(explicit)} НЕ применены: цензура уже стоит — переделать "
                                 "её можно только с redo: true (и только если владелец просил)")
            return {"ok": True, "status": "already", "already_applied": True, **done,
                    **({"notes": notes} if notes else {})}
    try:
        if args.get("clear"):
            ctx.before_change()
            return {"ok": True, **censor.censor_plan(ctx.store, ctx.plan_id, clear=True)}
        if args.get("verify", True) is False:
            ctx.before_change()
            out = censor.censor_plan(ctx.store, ctx.plan_id, levels, **options)
            out["note"] += ". Без проверки: рендер и Whisper не запускались (verify=true — проверит сам)"
            return {"ok": True, "verified": False, **out, **({"notes": notes} if notes else {})}
        if not censor.job_running(ctx.plan_id):
            ctx.before_change()
        out = censor.censor_job(ctx.store, ctx.plan_id, levels=levels,
                                max_rounds=int(_num(args.get("max_rounds"), censor.DEFAULT_MAX_ROUNDS) or 3),
                                **options)
    except ValueError as exc:
        raise ToolError(f"censor_profanity: {exc}") from exc
    return {"ok": True, **out, **({"notes": notes} if notes else {})}


def h_add_music_from_url(ctx: ToolContext, args: dict) -> dict:
    """Музыка по ссылке (YouTube и т.п.) → новый трек фона."""
    from app.music_import import import_music_from_url

    try:
        track = import_music_from_url(ctx.store, str(args.get("url") or ""), str(args.get("label") or ""))
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    return {"ok": True, "track_id": track["id"], "label": track["label"],
            "duration": round(float(track.get("duration_sec") or 0), 1)}


def h_add_sound_from_url(ctx: ToolContext, args: dict) -> dict:
    """Звуковой эффект по ссылке владельца (страница freesound или видео) → asset_id для слоя audio."""
    from app.sound_import import MAX_SOUND_SEC, import_sound_from_url

    try:
        asset = import_sound_from_url(ctx.store, str(args.get("url") or ""), str(args.get("label") or ""),
                                      start=_num(args.get("start"), 0.0) or 0.0, project_id=_assets_project(ctx, args))
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - сеть/удалённый сайт
        raise ToolError(f"не удалось скачать звук: {str(exc)[:200]}") from exc
    return {"ok": True, "asset_id": asset["id"], "kind": asset["kind"], "label": asset["label"],
            "duration": round(float(asset.get("duration_sec") or 0), 2),
            "note": (f"обрезан до {MAX_SOUND_SEC:g} с; ставь слоем audio (props.asset_id) на слово (at_word), "
                     "громкость 0.3–0.6 — сервис сам держит эффект под голосом")}


def h_set_music(ctx: ToolContext, args: dict) -> dict:
    """Фоновая музыка клипа: on + track_id (list_music), start_sec — с какой секунды трека."""
    render = _current_render(ctx)
    on = args.get("on") is not False
    track_id = args.get("track_id")
    start = render.get("music_start_sec") or 0.0
    if on:
        changed_track = bool(track_id) and int(track_id) != render.get("track_id")
        track_id = track_id or render.get("track_id")
        if not track_id:
            raise ToolError("нужен track_id — см. list_music")
        try:
            track = ctx.store.get_audio_track(int(track_id))
        except (KeyError, ValueError, TypeError) as exc:
            raise ToolError(f"нет трека {track_id} — см. list_music") from exc
        if args.get("start_sec") is not None:
            start = _num(args.get("start_sec"), 0.0) or 0.0
        elif changed_track:
            start = 0.0  # новый трек — с начала, если не сказано иное
        length = float(track.get("duration_sec") or 0)
        if length and start >= length:
            raise ToolError(f"start_sec {start:g} — за концом трека ({length:g} с)")
    fresh = _save_render(ctx, {**render, "music_on": on, "track_id": int(track_id) if on else render.get("track_id"),
                               "music_start_sec": start})
    out = {"ok": True, "music_on": fresh["music_on"], "track_id": fresh["track_id"],
           "music_start_sec": fresh["music_start_sec"]}
    if on:
        from app.music_energy import ensure_energy

        [track] = ensure_energy(ctx.store, [track])
        out.update(_music_track_hint(track, float(fresh["music_start_sec"] or 0.0), _is_promo(ctx, fresh)))
    return out


def _is_promo(ctx: ToolContext, render: dict) -> bool:
    """Промо: клип-холст или в слоях есть телефон (device) — там музыка с тихим вступлением мешает."""
    return _source_canvas(ctx) or has_device_layers(_layers_of(render))


def h_set_source_audio(ctx: ToolContext, args: dict) -> dict:
    """Звуковые дорожки исходника: [0] — первая, [1] — вторая, [0, 1] — смешать (игра + микрофон)."""
    from app.source_audio import set_source_audio

    from app.source_audio import _KEEP

    project = _source_or_clip_source(ctx, args)
    if "tracks" not in args and "speech_tracks" not in args:
        raise ToolError("нужны tracks (дорожки для рендера) и/или speech_tracks (дорожки для распознавания речи)")
    tracks = args.get("tracks", _KEEP)
    if tracks is not _KEEP and tracks is not None and not isinstance(tracks, list):
        raise ToolError("tracks — список: номера дорожек с 0 ([0], [1], [0, 1]) или [{track, gain_db}] "
                        "(gain_db — дБ или \"auto\"); [] — по умолчанию (первая)")
    speech = args.get("speech_tracks", _KEEP)
    if speech is not _KEEP and speech is not None and not isinstance(speech, list):
        raise ToolError("speech_tracks — список номеров дорожек для распознавания речи, например [1]; [] — как tracks")
    try:
        return set_source_audio(ctx.store, project, tracks, speech)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


def h_set_source_gain(ctx: ToolContext, args: dict) -> dict:
    """Громкость звука исходника на отрезках клипа (шкала clip), картинка не меняется."""
    from app.audio_mute import GAIN_MAX_DB, GAIN_MIN_DB, normalize_gain

    ranges = args.get("ranges")
    if not isinstance(ranges, list):
        raise ToolError("ranges — список {start, end, gain_db} (секунды клипа до вырезов; [] — снять всё)")
    problems = []
    for i, item in enumerate(ranges):
        if not isinstance(item, dict) or _num(item.get("gain_db")) is None:
            problems.append(f"ranges[{i}]: нужны start, end и gain_db (дБ, {GAIN_MIN_DB:g}..+{GAIN_MAX_DB:g})")
    if problems:
        raise ToolError("громкость не принята", problems)
    duration = _clip_duration(ctx)
    clean = [g for g in normalize_gain(ranges) if g["start"] < duration]
    render = _current_render(ctx)
    fresh = _save_render(ctx, {**render, "gain": clean})
    out: dict = {"ok": True, "gain": fresh["gain"], "time": "clip",
                 "note": ("громкость звука исходника на этих отрезках изменена (рампы 0.05 с по краям); звуки слоёв и "
                          "музыка не меняются, ролик потом выравнивается целиком до −14 LUFS. Проверь listen.")}
    if any(g["gain_db"] > 12 for g in clean):
        out["warnings"] = ["подъём больше 12 дБ поднимает и шум — если голос тонет в игре по всему ролику, лучше "
                           "громкость дорожки микрофона: set_source_audio tracks [{track, gain_db}]"]
    out["warnings"] = out.get("warnings", []) + _track_warnings(ctx, fresh["gain"])
    if not out["warnings"]:
        out.pop("warnings")
    return out


def h_source_loudness(ctx: ToolContext, args: dict) -> dict:
    """Громкость исходника по времени: пики и громкие места — перестрелки и реакции без Gemini."""
    from app.source_loudness import source_loudness

    project = _source_or_clip_source(ctx, args)
    try:
        track = _num(args.get("track"))
        return source_loudness(ctx.store, project, _num(args.get("from_sec")), _num(args.get("to_sec")),
                               _num(args.get("step")), int(_num(args.get("top"), 10) or 10),
                               track=int(track) if track is not None else None)
    except ValueError as exc:
        raise ToolError(f"source_loudness: {exc}") from exc


def tools() -> list[Tool]:
    return [
        Tool(
            "find_noises", "Ищу посторонние звуки",
            "Громкие места звука клипа без речи — вшитые эффекты старого монтажа, музыка, шум, в игре — "
            "выстрелы и взрывы (распознавание их не видит). Отрезки уже слиты, шкала clip (до вырезов, как "
            "слои и mute_audio — годятся туда как есть): в паузе — вырежи apply_cuts или mute_audio, тише — "
            "set_source_gain. threshold_db — порог громкости, по умолчанию медиана звука клипа без речи + 6 дБ. "
            "Речь вычитается по субтитрам, без них — по дорожке речи (speech_tracks), если она своя. "
            "/ Loud non-speech spans in the clip audio.",
            _obj({"threshold_db": {"type": "number"}}), h_find_noises,
            label=lambda a: "Ищу посторонние звуки",
        ),
        Tool(
            "listen", "Слушаю отрезок",
            "Распознать, что РЕАЛЬНО звучит в готовом клипе между from_sec и to_sec (с вырезами и наплывами "
            "переходов, до 30 с): время — шкала final (готовый ролик, как contact_sheet); before_cuts=true — "
            "шкала clip (до вырезов, как слои и субтитры get_clip). Распознаёт дорожку речи (speech_tracks). "
            "Слова с временем и тишина после последнего слова, громкость отрезка (level, RMS дБ). Без "
            "параметров — последние 6 с: проверь, договорена ли развязка и есть ли хвост реакции. Речи нет "
            "(игра, музыка) — words [] и пометка. Заглушённое (mute, в т.ч. мат censor_profanity) молчит, как в "
            "рендере. Для концовок, вырезов и «не торчит ли слово» — НЕ для "
            "исправления распознавания: это тот же Whisper и он повторит те же ошибки (неверное слово правь "
            "по смыслу/на слух владельца через set_subtitle_words). / Transcribe the finished clip audio in a range.",
            _obj({"from_sec": {"type": "number"}, "to_sec": {"type": "number"},
                  "before_cuts": {"type": "boolean", "description": "from/to и слова — шкала clip (до вырезов)"}}),
            h_listen,
            label=lambda a: "Слушаю финал клипа" if a.get("from_sec") is None else
            f"Слушаю {(_num(a.get('from_sec'), 0.0) or 0.0):.1f}–{(_num(a.get('to_sec'), 0.0) or 0.0):.1f} с",
        ),
        Tool(
            "source_loudness", "Слушаю громкость исходника",
            "Профиль громкости ИСХОДНИКА: RMS дБ по шагу step (≥ 0.5 с, по умолчанию 1) в окне from_sec..to_sec "
            "(не больше 10 мин за вызов, по умолчанию 10 мин от from_sec), пики (top, по умолчанию 10) и "
            "громкие отрезки, сводка: медиана и порог «громко» (медиана + 6 дБ). Для игр и стримов без речи — "
            "так ищут перестрелки, взрывы, реакции; потом source_contact_sheet на пиках. Звук — смесь рендера "
            "(set_source_audio); track — одна дорожка записи (сравнить громкость игры и микрофона перед "
            "gain_db). source_id — видео-источник (у встроенного монтажёра — источник клипа). / Loudness profile of the source over time.",
            _obj({**SOURCE_ARGS, "from_sec": {"type": "number"}, "to_sec": {"type": "number"},
                  "step": {"type": "number"}, "top": {"type": "integer"},
                  "track": {"type": "integer", "description": "одна звуковая дорожка вместо смеси"}}),
            h_source_loudness, scope="global",
            label=lambda a: f"Слушаю громкость видео с {(_num(a.get('from_sec'), 0.0) or 0.0):.0f} с",
        ),
        Tool(
            "set_source_audio", "Выбираю звуковую дорожку",
            "Звуковые дорожки исходника (запись NVIDIA/OBS: часто 0 — игра, 1 — микрофон). tracks — смесь для "
            "рендера: [0], [1], [0, 1] или со своей громкостью [{track, gain_db}] (gain_db — дБ −30..+30; "
            "\"auto\" — замерить и поставить голос на ~9 дБ над игрой в моменты речи; не хватает запаса пика голоса — "
            "остальные дорожки получают отрицательный gain_db; ответ: auto_gain — громкость всех дорожек, auto — "
            "over_db, peak_limited): тихий микрофон в смеси поровну тонет. "
            "speech_tracks — дорожки для РАСПОЗНАВАНИЯ речи (расшифровка исходника, субтитры, listen, сверка "
            "субтитров в QC), например [1] — один микрофон: на смеси с игрой Whisper выдумывает; [] — как tracks. "
            "Сменился звук для распознавания — расшифровка исходника сбрасывается. Сколько дорожек — "
            "get_clip.source.audio / list_projects; громкость каждой — source_loudness(track). source_id — "
            "видео-источник (у встроенного монтажёра — источник клипа). / Choose, mix and level the source audio tracks.",
            _obj({**SOURCE_ARGS,
                  "tracks": {"type": "array", "items": {
                      "anyOf": [{"type": "integer"},
                                _obj({"track": {"type": "integer"},
                                      "gain_db": {"type": ["number", "string"],
                                                  "description": "дБ −30..+30 или \"auto\""}}, ["track"])]}},
                  "speech_tracks": {"type": "array", "items": {"type": "integer"},
                                    "description": "дорожки для распознавания речи; [] — как tracks"}}),
            h_set_source_audio, mutating=True, scope="global",
            label=lambda a: f"Звук видео: дорожки {a.get('tracks')}"
            + (f", речь {a.get('speech_tracks')}" if a.get("speech_tracks") is not None else ""),
        ),
        Tool(
            "list_music", "Смотрю фоновую музыку",
            "Треки фоновой музыки (id, название, длина; energy_start_sec — где кончается тихое вступление, "
            "drop_sec — самый сильный подъём, bpm — темп). / Background music tracks with energy marks.",
            _obj({}), h_list_music, scope="global",
        ),
        Tool(
            "add_sound_from_url", "Скачиваю звук",
            "Звуковой эффект по ссылке ВЛАДЕЛЬЦА: страница звука freesound.org или любое видео yt-dlp (только "
            "звук; start — с какой секунды взять). Режет тишину в начале, обрезает до 6 с → asset_id для слоя "
            "audio. Сам звуки в интернете не ищи — только ссылка владельца; иначе пак freesound (search_pack). / "
            "Sound effect from the owner's link.",
            _obj({"url": {"type": "string"}, "label": {"type": "string"}, "start": {"type": "number"},
                  **ASSET_OWNER_PARAMS}, ["url"]),
            h_add_sound_from_url, scope="global",
            label=lambda a: f"Скачиваю звук {(a.get('label') or '')[:40]}".strip(),
        ),
        Tool(
            "mute_audio", "Глушу звук исходника",
            "Тишина вместо звука исходника на отрезках ranges [{start, end}] (секунды клипа до вырезов), "
            "картинка остаётся — для вшитых эффектов старого монтажа, шума, мата (find_noises покажет где). "
            "Заменяет прежний список ручных; [] — снять их. Отрезки мата (by: censor, censor_profanity) остаются, "
            "если в ranges нет помеченных. track — только эта дорожка записи (0 — игра, 1 — микрофон у NVIDIA/OBS): "
            "остальные звучат. / Mute source audio in ranges (optionally one track).",
            _obj({"ranges": {"type": "array", "items": _obj({"start": {"type": "number"}, "end": {"type": "number"},
                                                            "track": {"type": "integer"}},
                                                           ["start", "end"])}}, ["ranges"]),
            h_mute_audio, mutating=True, label=lambda a: f"Глушу звук ({len(a.get('ranges') or [])} отр.)",
        ),
        Tool(
            "censor_profanity", "Глушу мат",
            "Мат одной командой по словам субтитров (нужны generate_subtitles): каждое матерное слово глушится "
            "СЕРЕДИНОЙ (mute с меткой by: censor, уровень 1 — 30–85% слова, 2 — шире, 3 — слышна только первая "
            "согласная) — человек понимает, автосубтитры TikTok/YouTube мат не распознают; в субтитрах — первая "
            "буква и звёздочки («б****»). Ручные mute не трогает, свои прежние отрезки заменяет; повторный "
            "запуск идемпотентен (звёздочки находит сам). По умолчанию мат + «сука»; soft=true — ещё блин, "
            "хрен, жопа, говно, срать — ТОЛЬКО если владелец сам просил глушить мягкую брань (иначе soft не "
            "применяется: это не мат). Слова владельца в кавычках (панчлайн, ключевое слово) и слово под звуком-"
            "акцентом не глушатся (kept в ответе), кроме настоящего мата; заглушить такое всё же — force_words [i], "
            "только по просьбе владельца. verify=true (по умолчанию) — рендер → Whisper по звуку готового файла → там, где "
            "мат ещё узнаётся (±0.4 с), уровень +1 и новый рендер, не больше max_rounds (3) кругов; это минуты: "
            "ответ status running — повтори тот же вызов через retry_after_sec. Итог: words [{i, word, start, "
            "end, level, heard, note}], render_id последнего рендера, unmuted_heard — мат, который Whisper "
            "услышал мимо субтитров. heard на уровне 3 — «узнаётся по контексту»: тишиной не лечится. "
            "verify=false — быстро, без рендера. levels — число всем или {i: уровень}; skip [i] — не трогать "
            "слово; clear=true — снять свои отрезки. track: \"voice\" — глушить только дорожку голоса (запись "
            "игра+микрофон со своей speech_tracks; неизвестна — весь звук), число — эту дорожку; слово, что "
            "узнаётся и так, verify заглушит целиком. style — чем заполнить середину: silence (тишина), beep "
            "(тихий писк), noise (розовый шум), reverse (задом наперёд), lowpass («под водой»), stretch (растяжка, "
            "тон ниже), duck (−35 дБ, не в ноль); не задан — прежний стиль клипа (хранится у отрезков), у нового "
            "— reverse (выбор владельца на слух). Слово, что узнаётся и на уровне 3, verify сам переключает на "
            "silence, затем noise (только его; style_switched в ответе). Вызывай ОДИН раз: цензура уже стоит на весь "
            "мат — повтор с любыми levels/style/groups отвечает status already («уже применено», без рендера) и их "
            "не применяет; переделать — redo: true (только если владелец просил), verify_again: true — проверить "
            "рендером заново. Слова под цензурой в субтитрах всегда со звёздочками и не подсвечиваются "
            "(highlightWords/эмфаза на них не действуют). "
            "/ Mute profanity mid-word, star it in subtitles, verify by Whisper on the render.",
            _obj({"verify": {"type": "boolean"}, "verify_again": {"type": "boolean"}, "max_rounds": {"type": "integer"},
                  "levels": {"type": ["integer", "object"]}, "soft": {"type": "boolean"},
                  "stars": {"type": "boolean"}, "skip": {"type": "array", "items": {"type": "integer"}},
                  "clear": {"type": "boolean"},
                  "redo": {"type": "boolean", "description": "переделать уже стоящую цензуру (иначе повтор — already)"},
                  "force_words": {"type": "array", "items": {"type": "integer"},
                                  "description": "номера слов владельца/под акцентом, которые всё же глушить"},
                  "track": {"type": ["integer", "string"], "description": "\"voice\" или номер дорожки"},
                  "style": {"type": "string", "enum": ["silence", "beep", "noise", "reverse", "lowpass", "stretch",
                                                       "duck"]}}),
            h_censor_profanity, mutating=True,
            label=lambda a: "Глушу мат" + ("" if a.get("verify") is False else " и проверяю рендером"),
        ),
        Tool(
            "set_source_gain", "Громкость исходника на отрезках",
            "Громкость звука исходника на отрезках ranges [{start, end, gain_db}] (шкала clip — секунды клипа до "
            "вырезов, как mute_audio и find_noises): поднять тихий кусок (+6..+12 дБ) или приглушить громкий "
            "(−6..−20 дБ), картинка та же; звуки слоёв и музыка не меняются, ролик потом выравнивается целиком. "
            "Заменяет прежний список; [] — снять. Тихий микрофон по всему ролику — громкость дорожки "
            "(set_source_audio tracks [{track, gain_db}]), а не отрезки. track — громкость только этой дорожки "
            "(приглушить игру под фразой, микрофон не трогая). / Source audio gain in ranges (optionally one track).",
            _obj({"ranges": {"type": "array", "items": _obj({"start": {"type": "number"}, "end": {"type": "number"},
                                                            "gain_db": {"type": "number"},
                                                            "track": {"type": "integer"}},
                                                           ["start", "end", "gain_db"])}}, ["ranges"]),
            h_set_source_gain, mutating=True,
            label=lambda a: f"Громкость видео ({len(a.get('ranges') or [])} отр.)",
        ),
        Tool(
            "add_music_from_url", "Скачиваю музыку",
            "Трек фоновой музыки по ссылке (YouTube и всё, что умеет yt-dlp; до 15 мин) → track_id для "
            "set_music. Только когда владелец дал ссылку. / Background music from a link.",
            _obj({"url": {"type": "string"}, "label": {"type": "string"}}, ["url"]),
            h_add_music_from_url, scope="global", label=lambda a: "Скачиваю музыку по ссылке",
        ),
        Tool(
            "set_music", "Подкладываю музыку",
            "Фоновая музыка клипа: on, track_id (list_music), start_sec — с какой секунды трека играть на 0 с "
            "клипа (пропустить тихое вступление и начать сразу с барабанов/дропа — energy_start_sec трека из "
            "list_music; 0 — с начала, новый трек без start_sec — с начала). Громкость сама: заметно тише "
            "голоса и приглушается под речь; эффекты при музыке — реже. / Background music for the clip.",
            _obj({"on": {"type": "boolean"}, "track_id": {"type": "integer"},
                  "start_sec": {"type": "number", "description": "секунда трека на 0 с клипа"}}),
            h_set_music, mutating=True,
            label=lambda a: "Музыка " + ("выкл" if a.get("on") is False else f"трек {a.get('track_id') or ''}".strip()),
        ),
    ]
