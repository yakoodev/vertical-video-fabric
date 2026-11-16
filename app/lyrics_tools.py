"""Инструменты агента для текста песни мувика: ``align_lyrics`` и ``lyric_layers``.

* ``align_lyrics(track_id | source_id | audio_path, lyrics, lang)`` — строки песни со временем слов по слогам вокала
  (app/lyrics_audio.py, app/lyrics_align.py): фоном, повторный вызов — к тому же заданию, итог в кэше
  (``lyrics_id``). Текста нет — берётся сохранённый у трека карточкой «Мувик под песню», нет и его — строки из
  распознанного.
* ``lyric_layers(clip_id, lyrics_id)`` — слой текста песни в клипе (app/lyrics_layers.py): все строки, только в паузах
  (очереди выстрелов game_events — мимо), уход за 3 кадра до склейки, крупные узкие капсы; прежние слои текста песни
  (id ``lyr_…``) заменяются.

Входят в общий реестр (``app/agent_tools.py``) — у встроенного монтажёра, в чате проекта и в MCP; модуль сервиса —
«Мувик под песню» (``frag_movie``, app/modules.py).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.tool_core import SOURCE_ARGS, Tool, ToolContext, ToolError, _num, _obj

MAX_LINES_OUT = 120


def _audio_path(ctx: ToolContext, args: dict) -> tuple[Path, int | None, str]:
    """Файл песни: трек музыки, звук источника или путь в данных сервиса. (путь, track_id, подпись)."""
    from app.settings import settings

    if args.get("track_id") not in (None, ""):
        try:
            track = ctx.store.get_audio_track(int(args["track_id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ToolError(f"нет трека {args.get('track_id')} — list_music") from exc
        return Path(str(track.get("file_path") or "")), int(track["id"]), str(track.get("label") or "")
    if args.get("source_id") not in (None, ""):
        try:
            source = ctx.store.get_source(int(args["source_id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ToolError(f"нет источника {args.get('source_id')}") from exc
        return Path(str(source.get("local_path") or "")), None, str(source.get("original_filename") or "")
    raw = str(args.get("audio_path") or "").strip()
    if not raw:
        raise ToolError("нужен track_id (list_music), source_id или audio_path")
    path = Path(raw).expanduser()
    roots = [settings.data_dir, settings.audio_dir]
    try:
        resolved = path.resolve()
    except OSError as exc:
        raise ToolError(f"путь {raw} не читается") from exc
    if not any(resolved.is_relative_to(Path(r).resolve()) for r in roots):
        raise ToolError("audio_path — только файл в данных сервиса; песню с компьютера — в музыку (add_music_from_url "
                        "или загрузка на странице «Музыка»), потом track_id")
    return resolved, None, resolved.name


def h_align_lyrics(ctx: ToolContext, args: dict) -> dict:
    from app import frag_movie as F
    from app import lyrics_align as LA
    from app import lyrics_audio as A

    path, track_id, label = _audio_path(ctx, args)
    if not path.is_file():
        raise ToolError("у песни нет файла (трек удалён или источник не скачан)")
    lyrics = str(args.get("lyrics") or args.get("lyrics_text") or "").strip()
    lang = str(args.get("lang") or "").strip().lower()
    note_text = ""
    if not lyrics and track_id:
        saved = F.track_lyrics(ctx.store, track_id)
        if saved:
            lyrics = saved["text"]
            lang = lang or str(saved.get("lang") or "")
            note_text = "текст — сохранённый у трека (карточка «Мувик под песню»)"
    start = max(0.0, _num(args.get("start_sec"), 0.0) or 0.0)
    end = _num(args.get("end_sec"))
    if end is not None and end <= start + 1.0:
        raise ToolError("end_sec должен быть больше start_sec хотя бы на секунду")
    try:
        out = A.align_job(path, lyrics=lyrics, lang=lang, start=start, end=end,
                          model=str(args.get("model") or "") or None, hint=args.get("hint") is not False,
                          refresh=bool(args.get("refresh")), wait=_num(args.get("wait_sec")))
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    if out.get("status") != "done":
        return {"ok": True, **out, "track_id": track_id}
    lines = out.get("lines") or []
    detail = bool(args.get("words"))
    view = {
        "ok": True, "status": "done", "lyrics_id": out["lyrics_id"], "track_id": track_id, "track": label,
        "time": "track", "method": out.get("method"), "lang": out.get("lang"), "with_text": out.get("with_text"),
        "stats": out.get("stats"), "cached": out.get("cached", False),
        "lines": LA.compact_lines(lines[:MAX_LINES_OUT], with_words=detail),
        "notes": [*([note_text] if note_text else []), *(out.get("notes") or [])],
        "next": ("t0/t1 и at (начала слов) — секунды ТРЕКА. В клип — lyric_layers(clip_id, lyrics_id): все строки в "
                 "паузах, мимо стрельбы и склеек; сомнительные (unsure) он пропустит сам."),
    }
    if len(lines) > MAX_LINES_OUT:
        view["truncated"] = len(lines) - MAX_LINES_OUT
    return view


def _final_spans(ctx: ToolContext, plan: dict, clock: Any, source_spans: list[tuple[float, float]]) -> list[list[float]]:
    """Отрезки секунд исходника → отрезки готового ролика (по кускам клипа, со скоростью кусков)."""
    from app.tool_core import clip_timeline

    out: list[list[float]] = []
    for piece in clip_timeline(ctx.store, ctx.plan_id)["pieces"]:
        if piece.get("reverse"):
            continue
        speed = float(piece.get("speed") or 1.0)
        for a0, b0 in source_spans:
            a, b = max(a0, piece["source_start"]), min(b0, piece["source_end"])
            if b <= a:
                continue
            fa = clock.clip_to_final(piece["clip_start"] + (a - piece["source_start"]) / speed)
            fb = clock.clip_to_final(piece["clip_start"] + (b - piece["source_start"]) / speed)
            if fb > fa:
                out.append([round(fa, 3), round(fb, 3)])
    return sorted(out)


def _busy(ctx: ToolContext, plan: dict, clock: Any) -> tuple[list[list[float]], str]:
    """Стрельба в готовом ролике: очереди выстрелов (shot_burst) и ноки/киллы из кэша game_events записи клипа."""
    try:
        from app.game_events_job import cached

        data = cached(ctx.store, int(plan["source_id"]))
    except Exception:  # noqa: BLE001 - нет кэша — без стрельбы
        data = None
    if not data:
        return [], ("событий боя нет (game_events по записи клипа не запускался) — стрельбу не обхожу сам: "
                    "передай busy или сначала game_events(source_id)")
    spans = [(float(e["t"]) - 0.15, float(e.get("end") or e["t"]) + 0.25) for e in data.get("events") or []
             if e.get("kind") in ("shot_burst", "knock", "kill", "squad_wipe")]
    return _final_spans(ctx, plan, clock, spans), f"стрельба — game_events записи ({len(spans)} событий)"


def _plan_fps(ctx: ToolContext, plan: dict) -> float:
    """Кадры рендера клипа (пресет; нет — 30): «уходит за 3 кадра до склейки» — в кадрах готового ролика."""
    try:
        from app.render import effective_preset

        preset = effective_preset(ctx.store, (plan.get("render_settings") or {}).get("preset_id")) or {}
        return float(preset.get("fps") or 30.0)
    except Exception:  # noqa: BLE001 - без пресета — 30 кадров
        return 30.0


def h_lyric_layers(ctx: ToolContext, args: dict) -> dict:
    from app import lyrics_audio as A
    from app.clip_settings import normalize_render_settings
    from app.formats import settings_layers_format
    from app.layer_lint import _ref
    from app.layer_tools import MAX_LAYERS_PER_CALL, h_add_layers, h_remove_layers
    from app.lyrics_layers import LAYER_PREFIX, build_lyric_layers
    from app.tool_core import _current_render, _layers_of, clip_timeline, plan_clock

    if args.get("lines") is not None:
        lines = args["lines"]
        if not isinstance(lines, list) or not all(isinstance(x, dict) and x.get("words") for x in lines):
            raise ToolError("lines — строки align_lyrics с words (align_lyrics(words: true)) или дай lyrics_id")
        lyrics_id = ""
    else:
        lyrics_id = str(args.get("lyrics_id") or "").strip()
        result = A.load_result(lyrics_id) if lyrics_id else None
        if not result:
            raise ToolError("нет выравнивания lyrics_id — сначала align_lyrics (его lyrics_id из ответа)")
        lines = result.get("lines") or []
    plan = ctx.store.get_clip_plan(ctx.plan_id)
    clock = plan_clock(ctx.store, plan)
    render = normalize_render_settings(plan.get("render_settings") or {})
    offset = _num(args.get("song_offset"))
    offset_note = "song_offset из запроса"
    if offset is None:
        offset = float(render.get("music_start_sec") or 0.0) if render.get("music_on") and render.get("track_id") else 0.0
        offset_note = (f"песня — музыка клипа с {offset:g} с трека (set_music)" if render.get("music_on")
                       else "музыки set_music у клипа нет — считаю, что трек звучит с 0 с ролика; иначе song_offset")
    busy = args.get("busy")
    busy_note = "стрельба — busy из запроса"
    if busy is None:
        busy, busy_note = _busy(ctx, plan, clock)
    pieces = clip_timeline(ctx.store, ctx.plan_id)["pieces"]
    cuts = [clock.clip_to_final(p["clip_start"]) for p in pieces[1:]]
    fmt = settings_layers_format(_current_render(ctx))
    built = build_lyric_layers(
        lines, song_offset=offset, duration=float(clock.final_duration), fps=_plan_fps(ctx, plan), busy=busy,
        avoid=args.get("avoid"), cuts=cuts, only=args.get("only"), zone=str(args.get("zone") or ""),
        ref=_ref(fmt), style=args.get("style") if isinstance(args.get("style"), dict) else None,
        show_unsure=bool(args.get("show_unsure")))
    for layer in built["layers"]:  # секунды ролика → шкала слоёв (clip, до вырезов)
        layer["start"] = clock.final_to_clip(layer["start"])
        layer["end"] = clock.final_to_clip(layer["end"])
    out: dict = {"ok": True, "lyrics_id": lyrics_id, "zone": built["zone"], "placed": len(built["placed"]),
                 "skipped": built["skipped"], "lines": len(lines), "offset_note": offset_note, "busy_note": busy_note}
    if args.get("dry_run"):
        out.update(dry_run=True, layers=built["layers"][:40], placed_lines=built["placed"])
        return out
    old = [str(l.get("id")) for l in _layers_of(_current_render(ctx)) if str(l.get("id") or "").startswith(LAYER_PREFIX)]
    if old and args.get("replace", True) is not False:
        h_remove_layers(ctx, {"ids": old})
        out["replaced"] = len(old)
    warnings: list[str] = []
    added = 0
    for k in range(0, len(built["layers"]), MAX_LAYERS_PER_CALL):
        res = h_add_layers(ctx, {"layers": built["layers"][k:k + MAX_LAYERS_PER_CALL]})
        added += len(res.get("added") or [])
        warnings += [str(w) for w in res.get("warnings") or []][:6]
        if res.get("skipped"):
            warnings += [str(s) for s in res["skipped"]][:6]
    out.update(layers_added=added, warnings=warnings[:20],
               next=("посмотри строки глазами: view_frame(final: true) на паре строк, contact_sheet; не на прицеле? "
                     "поправить вид — style (font, color, accent, size) или zone; пропущенные (skipped) — сдвинь "
                     "склейку snap_to_beats или кусок, потом lyric_layers снова (прежние lyr_ заменятся)"))
    return out


def tools() -> list[Tool]:
    return [
        Tool(
            "align_lyrics", "Выравниваю текст песни",
            "Текст песни → строки со временем слов по слогам вокала (мувик под песню, плейбук frag-movie: текст — "
            "«аналог субтитров», все строки и повторы). Песня — track_id (list_music), source_id или audio_path (файл в "
            "данных сервиса). lyrics — текст владельца построчно ([Chorus] и пустые строки — не строки, «(x2)» — "
            "повтор строки); нет — сохранённый у трека карточкой «Мувик под песню», нет и его — строки из "
            "распознанного (сверь на слух). lang — ru|en|… (пусто — сам). Вокал отделяется (demucs, если настроен; "
            "иначе полоса голоса — грубее), слова — Whisper, текст выравнивается по ним, начало слова — к атаке слога. "
            "Идёт ФОНОМ (минута-две на песню): не готово — status \"running\" и retry_after_sec, зови тот же вызов ещё "
            "раз; потом кэш. start_sec/end_sec — участок трека. Ответ: lyrics_id, lines [{i, text, t0, t1, at — начала "
            "слов, matched, unsure}] в секундах ТРЕКА, stats, notes; words: true — слова со слогами. Дальше — "
            "lyric_layers(clip_id, lyrics_id). / Align song lyrics to sung syllables (vocals + Whisper word times).",
            _obj({"track_id": {"type": "integer", "description": "трек музыки (list_music)"},
                  "source_id": SOURCE_ARGS["source_id"],
                  "audio_path": {"type": "string", "description": "файл звука в данных сервиса"},
                  "lyrics": {"type": "string", "description": "текст песни построчно"},
                  "lang": {"type": "string", "description": "язык песни: ru, en, … (пусто — сам)"},
                  "start_sec": {"type": "number", "minimum": 0}, "end_sec": {"type": "number"},
                  "words": {"type": "boolean", "description": "слова со временем и слогами в ответе"},
                  "hint": {"type": "boolean", "description": "начало текста подсказкой Whisper (по умолчанию да)"},
                  "model": {"type": "string", "description": "модель Whisper (по умолчанию large-v3-turbo)"},
                  "refresh": {"type": "boolean", "description": "заново, мимо кэша"},
                  "wait_sec": {"type": "number", "minimum": 0, "maximum": 50}}),
            h_align_lyrics, scope="global", cost=("cpu", "ram"),
            label=lambda a: "Выравниваю текст песни" + (f" трека {a.get('track_id')}" if a.get("track_id") else ""),
        ),
        Tool(
            "lyric_layers", "Ставлю текст песни",
            "Слой текста песни в клип мувика из align_lyrics (lyrics_id): ВСЕ строки по времени вокала, только в паузах "
            "— мимо очередей выстрелов, ноков и киллов (game_events записи клипа; свои — busy [{start, end}] в секундах "
            "готового ролика) и мимо реплик тиммейтов (avoid [{start, end}]); строка уходит за 3 кадра до склейки, "
            "не успевает до склейки — пропуск с причиной (сдвинь склейку). Вид: жирные узкие капсы (Oswald 700), "
            "строка кусками по паузам вокала, короткий кусок крупнее, слова загораются, когда поют; zone — bottom "
            "(по умолчанию у 9:16, мимо прицела) | top | center | left | right (16:9 — left), поля 6%; style — font, "
            "weight, color, accent (цвет крупного куска; null — без), size (предел кегля), stroke, letterSpacing. "
            "Песня — музыка клипа (set_music, с music_start_sec) или song_offset (секунда трека в начале ролика). "
            "only — номера строк; unsure не ставятся (show_unsure — всё же). Прежние слои lyr_… заменяются; dry_run — "
            "только план. / Place song lyrics as styled text layers between kills.",
            _obj({"lyrics_id": {"type": "string", "description": "из align_lyrics"},
                  "lines": {"type": "array", "items": {"type": "object"},
                            "description": "строки align_lyrics(words: true) вместо lyrics_id"},
                  "song_offset": {"type": "number", "description": "секунда трека в начале ролика"},
                  "only": {"type": "array", "items": {"type": "integer"}},
                  "busy": {"type": "array", "items": {"type": "object", "properties": {
                      "start": {"type": "number"}, "end": {"type": "number"}}}},
                  "avoid": {"type": "array", "items": {"type": "object", "properties": {
                      "start": {"type": "number"}, "end": {"type": "number"}}}},
                  "zone": {"type": "string", "enum": ["bottom", "top", "center", "left", "right"]},
                  "style": {"type": "object"},
                  "show_unsure": {"type": "boolean"}, "replace": {"type": "boolean"}, "dry_run": {"type": "boolean"}}),
            h_lyric_layers, mutating=True,
            label=lambda a: "Ставлю текст песни" + (" (план)" if a.get("dry_run") else ""),
        ),
    ]
