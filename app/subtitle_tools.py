"""Инструменты субтитров: распознать, поправить слова и строки, эмфаза, голоса и их банк, перевод и озвучка,
плюс поиск слова клипа по тексту (``word_at``), которым пользуются слои и вставки.

Входят в общий реестр (``app/agent_tools.py``)."""

from __future__ import annotations

import re
from typing import Any

from app.subtitles.phantoms import is_phantom_text
from app.tool_core import (
    _clock, _current_render, _num, _obj, _piece_len, _save_render, _to_clip_time, clip_timeline, Tool, ToolContext,
    ToolError,
)


# --------------------------------------------------------------------------- #
# Обработчики
# --------------------------------------------------------------------------- #


def _subtitles_view(store: Any, clip_plan_id: int, pieces: list[dict]) -> dict:
    """Субтитры во времени готового клипа: под слова подгоняют титры и наезды."""
    from app.clip_subtitles import words_by_line

    payload = store.get_clip_plan_subtitles(clip_plan_id) or {}
    shift = (lambda t: _to_clip_time(pieces, t)) if pieces else (lambda t: round(t, 3))
    words = payload.get("words") or []
    lines = []
    raw_lines = payload.get("lines") or []
    # Каждое слово — в одной строке (раньше слово на стыке попадало в обе).
    for index, (line, own) in enumerate(zip(raw_lines, words_by_line(raw_lines, words))):
        start, end = float(line["start"]), float(line["end"])
        inside = [
            [w["word"], round(shift(float(w["start"])), 2), round(shift(float(w["end"])), 2)]
            + ([w["speaker"]] if w.get("speaker") else [])
            for w in own
        ]
        entry = {"i": index, "start": round(shift(start), 2), "end": round(shift(end), 2),
                 "text": line["text"], "words": inside}
        emph = {j: int(w["emph"]) for j, w in enumerate(own) if w.get("emph")}
        if emph:
            entry["emph"] = emph  # эмфаза слов строки: {номер слова в строке: 1 pop | 2 shake}
        lines.append(entry)
    return {"has_subtitles": bool(lines), "edited": bool(payload.get("edited")), "lines": lines}


# Фраза расшифровки, которая не считается речью для «речь есть, а субтитров нет»: короче
# 0.6 с на куске или меньше 2 слов — смех, «а?», выдох (клипы 583–585: ложная тревога на смехе).
GAP_MIN_PHRASE_SEC = 0.6
GAP_MIN_PHRASE_WORDS = 2


# Вставка, под которой звук клипа «выключен»: duck_volume не громче этого (game_audio: cut — 0).
GAP_MUTED_DUCK = 0.05


def _muted_spans(plan: dict) -> list[tuple[float, float]]:
    """Окна клипа (шкала clip), где звука клипа нет: вставки-картинки (full/pip) с приглушением
    до нуля — видеомем с ``game_audio: cut``. Речь под ними не слышна, субтитров там не ждём."""
    from app.montage_assets import normalize_inserts

    spans = []
    for ins in normalize_inserts((plan.get("render_settings") or {}).get("inserts")):
        # Как build_inserts_args: приглушение — у full всегда при duck, у pip — только со своим duck_volume.
        if ins.get("mode") not in ("full", "pip") or not ins.get("duck") or "duck_volume" not in ins:
            continue
        if float(ins["duck_volume"]) <= GAP_MUTED_DUCK:
            spans.append((float(ins["at"]), float(ins["at"]) + float(ins["duration"])))
    return sorted(spans)


def _uncovered(lo: float, hi: float, spans: list[tuple[float, float]]) -> float:
    """Сколько секунд отрезка ``[lo, hi]`` не закрыто окнами ``spans``."""
    left, cursor = 0.0, lo
    for a, b in spans:
        if b <= cursor or a >= hi:
            continue
        left += max(0.0, a - cursor)
        cursor = max(cursor, b)
        if cursor >= hi:
            break
    return left + max(0.0, hi - cursor)


def subtitle_gaps(store: Any, plan: dict, pieces: list[dict]) -> list[int]:
    """Куски клипа, где в исходнике есть речь, а в субтитрах клипа — ни слова.

    Так бывает, когда куски поменяли после распознавания: субтитры остались от
    старого набора, и у части клипа текста нет (владелец: «у второго клипа
    субтитров почему то нет»). Короткие фразы (< 0.6 с на куске или < 2 слов —
    смех, междометие) речью не считаются. Не считается и речь под вставкой, которая
    выключает звук клипа (полнокадровый видеомем с ``game_audio: cut``): кусок, закрытый
    такой вставкой, — не дыра (ручной мем-эдит: ложное «на кусках N речь есть»).
    """
    payload = store.get_clip_plan_subtitles(plan["id"]) or {}
    words = payload.get("words") or []
    try:
        cues = store.get_source_transcript(int(plan["source_id"])) or []
    except Exception:  # noqa: BLE001 - нет транскрипта — судим только по субтитрам
        cues = []
    muted = _muted_spans(plan)
    gaps, offset = [], 0.0
    for index, piece in enumerate(pieces):
        length = _piece_len(piece)
        lo, hi = offset, offset + length
        offset = hi
        if length < 2.0 or any(lo <= float(w["start"]) < hi for w in words):
            continue
        # Где кусок на шкале clip (там живут вставки); без clip_start — куски встык.
        clip_lo = float(piece["clip_start"]) if piece.get("clip_start") is not None else lo
        src_lo, src_hi = float(piece["source_start"]), float(piece["source_end"])
        rate = length / (src_hi - src_lo) if src_hi > src_lo else 1.0  # секунды клипа на секунду исходника

        def to_clip(t: float) -> float:
            local = (t - src_lo) * rate
            return clip_lo + (length - local if piece.get("reverse") else local)

        if muted and _uncovered(clip_lo, clip_lo + length, muted) < 2.0:
            continue  # кусок почти целиком под вставкой без звука клипа
        phrases: list[tuple[float, float, int]] = []
        for c in cues:
            a = max(float(c.get("start", 0)), src_lo)
            b = min(float(c.get("end", 0)), src_hi)
            count = len(str(c.get("text") or "").split())
            if is_phantom_text(c.get("text")):
                continue  # «Субтитры сделал DimaTorzok» на тишине — не речь
            if b - a < GAP_MIN_PHRASE_SEC or count < GAP_MIN_PHRASE_WORDS:
                continue
            if muted:
                ca, cb = sorted((to_clip(a), to_clip(b)))
                if _uncovered(ca, cb, muted) < GAP_MIN_PHRASE_SEC:
                    continue  # фраза звучит под вставкой, которая выключила звук клипа
            phrases.append((a, b, count))
        if sum(n for _a, _b, n in phrases) >= 3:
            # Фраза расшифровки на тишине дорожки речи — галлюцинация распознавания, не речь.
            phrases = [p for p, heard in zip(phrases, _phrases_heard(store, plan, piece, phrases)) if heard]
        if sum(n for _a, _b, n in phrases) >= 3 or (not cues and words):
            gaps.append(index)
    return gaps


def _phrases_heard(store: Any, plan: dict, piece: dict, phrases: list[tuple[float, float, int]]) -> list[bool]:
    """Звучит ли на дорожке речи источника каждая фраза ``(start, end, слов)`` (секунды
    источника внутри куска). Порог — авто-порог речи (как у голоса PNGtuber). Звук не
    прочитать — считаем, что звучит (предупреждение как раньше)."""
    try:
        from app.source_audio import speech_mix
        from app.source_loudness import read_window_pcm
        from app.subtitles.phantoms import frame_levels, is_silent, speech_threshold

        source = store.get_source(int(plan["source_id"]))
        start = float(piece["source_start"])
        rate = 16000
        pcm = read_window_pcm(source, start, float(piece["source_end"]) - start, rate=rate,
                              tracks=speech_mix(source))
        levels = frame_levels(pcm, rate)
        if not levels:
            return [True] * len(phrases)
        threshold = speech_threshold(levels)
        return [not is_silent(levels, threshold, a - start, b - start) for a, b, _n in phrases]
    except Exception:  # noqa: BLE001 - нет файла / ffmpeg — судим по расшифровке, как раньше
        return [True] * len(phrases)


def h_separate_voices(ctx: ToolContext, args: dict) -> dict:
    from app.ai.speakers import separate_voices

    ctx.before_change()
    try:
        out = separate_voices(ctx.store, ctx.plan_id, all_voices=args.get("all_voices") is True)
    except (RuntimeError, ValueError) as exc:
        raise ToolError(str(exc)) from exc
    voices = len(out["counts"])
    note = ("голос один — субтитры обычные" if voices <= 1 else
            f"голосов {voices}: слова каждого помечены. Главный слой captions — props.speakers ['A'], "
            "другие голоса — второй слой captions с speakers ['B','C','D'], мельче (size ~60) и рядом "
            "(auto_edit ставит оба сам)")
    if out.get("dropped"):
        note += ("; из субтитров убраны голоса не живых людей (игра, ролик, озвучка доната): "
                 + ", ".join(f"{k} — {v['about'] or v['why']}" for k, v in out["dropped"].items())
                 + ". Субтитры только для речи стримера и живых людей, не для голосов игры; вернуть — all_voices: true, "
                 "только если владелец попросил")
    return {"ok": True, **out, "note": note}


def h_register_voice(ctx: ToolContext, args: dict) -> dict:
    from app.voice_bank import register_voice

    source_id = args.get("source_id")
    try:
        return {"ok": True, **register_voice(
            ctx.store, str(args.get("name") or ""), clip_plan_id=None if source_id else ctx.plan_id,
            source_id=int(source_id) if source_id else None, start=_num(args.get("start")), end=_num(args.get("end")),
            speaker=args.get("speaker"), kind=str(args.get("kind") or "person"),
        )}
    except (RuntimeError, ValueError, KeyError) as exc:
        raise ToolError(str(exc)) from exc


def h_identify_voices(ctx: ToolContext, args: dict) -> dict:
    from app.voice_bank import identify_voices

    ctx.before_change()
    try:
        out = identify_voices(ctx.store, ctx.plan_id, labels=args.get("labels", True) is not False,
                              reassign=args.get("reassign") is True)
    except (RuntimeError, ValueError) as exc:
        raise ToolError(str(exc)) from exc
    out.pop("subtitles", None)
    out["note"] = ("names — кто уверенно опознан, unsure — кого нет (имя не ставлю, не угадываю); conflicts — реплики, "
                   "звучащие как другой человек: послушай (listen) и поправь assign_voice или reassign: true")
    return {"ok": True, **out}


def h_list_voices(ctx: ToolContext, _args: dict) -> dict:
    from app.dubbing import GEMINI_VOICE_NOTES
    from app.voice_bank import list_voices, plan_project

    project = plan_project(ctx.store, ctx.plan_id)
    out = {"voices": list_voices(ctx.store, project),
           # Голоса синтеза (озвучка add_voiceover / tts_preview) — не банк узнавания, а чем говорить.
           "tts_voices": [{"voice": name, "about": about} for name, about in GEMINI_VOICE_NOTES.items()]}
    from app.asset_scope import clip_project

    owner = clip_project(ctx.store, ctx.plan_id)  # у холста проект — приписка клипа, а не источника
    if owner is not None:
        from app.projects_brief import narrator_voice

        out["narrator_voice"] = narrator_voice(ctx.store, int(owner)) or None
    return out


def h_delete_voice(ctx: ToolContext, args: dict) -> dict:
    from app.voice_bank import delete_voice

    try:
        delete_voice(ctx.store, int(args.get("voice_id") or 0))
    except KeyError as exc:
        raise ToolError(str(exc)) from exc
    return {"ok": True}


def h_snap_subtitles_to_speech(ctx: ToolContext, _args: dict) -> dict:
    from app.speech_activity import snap_lines_to_speech

    ctx.before_change()
    try:
        return {"ok": True, **snap_lines_to_speech(ctx.store, ctx.plan_id)}
    except (RuntimeError, ValueError) as exc:
        raise ToolError(str(exc)) from exc


def h_translate_subtitles(ctx: ToolContext, args: dict) -> dict:
    from app.translation import translate_subtitles

    ctx.before_change()
    try:
        return translate_subtitles(
            ctx.store, ctx.plan_id, mode=str(args.get("mode") or "ru"), translations=args.get("translations"),
            force=bool(args.get("force")), retranslate=bool(args.get("retranslate")),
        )
    except (RuntimeError, ValueError) as exc:
        raise ToolError(str(exc)) from exc


def h_dub_clip(ctx: ToolContext, args: dict) -> dict:
    from app.dubbing import dub_clip, provider_paid

    confirm, remove = bool(args.get("confirm")), bool(args.get("remove"))
    if confirm or remove or not provider_paid(args.get("provider")):  # бесплатный синтезирует сразу
        ctx.before_change()
    try:
        return dub_clip(
            ctx.store, ctx.plan_id, provider=args.get("provider"), voice=args.get("voice"), model=args.get("model"),
            confirm=confirm, budget_rub=args.get("budget_rub"), stretch=bool(args.get("stretch")), remove=remove,
            style=args.get("style"),
        )
    except (RuntimeError, ValueError) as exc:
        raise ToolError(str(exc)) from exc


def h_tts_preview(ctx: ToolContext, args: dict) -> dict:
    from app.dubbing import tts_preview

    try:
        return tts_preview(ctx.store, provider=args.get("provider") or "gemini", voice=args.get("voice"),
                           model=args.get("model"), style=args.get("style"), text=args.get("text"),
                           confirm=bool(args.get("confirm")))
    except (RuntimeError, ValueError) as exc:
        raise ToolError(str(exc)) from exc


def h_generate_subtitles(ctx: ToolContext, _args: dict) -> dict:
    from app.clip_subtitles import generate_for_plan

    from app.subtitles.registry import QUALITY_WHISPER_MODEL

    ctx.before_change()
    old_words = (ctx.store.get_clip_plan_subtitles(ctx.plan_id) or {}).get("words") or []
    # Точная модель: small на стримах выдумывает фразы под чужими голосами и музыкой.
    payload = generate_for_plan(ctx.store, ctx.plan_id, provider="whisper", model=QUALITY_WHISPER_MODEL)
    from app.caption_marks import rebind_plan_marks

    moved = rebind_plan_marks(ctx.store, ctx.plan_id, old_words, payload.get("words") or [])
    out = {"ok": True, "lines": len(payload.get("lines") or []), "words": len(payload.get("words") or [])}
    if moved:
        out["highlights"] = moved
    return out


SUBTITLE_BAND = 0.12  # высота полосы прожигаемых субтитров в долях кадра 1920


def _subtitle_zone(render: dict) -> dict | None:
    """Где на кадре стоят субтитры — чтобы не класть титры поверх них. Доли кадра формата клипа
    (``render.format``): в 1:1/4:5/16:9 полоса выше (те же px на кадре ниже), а низ не опускается
    ниже безопасной зоны площадки (рендер поднимет субтитры, app/subtitles/ass.py)."""
    from app.formats import frame_size, settings_render_format
    from app.safe_zones import safe_zone

    if not render.get("subs_on"):
        return None
    bottom = 1.0 - float(render.get("sub_pos_pct") or 12.0) / 100
    fmt = settings_render_format(render)
    if fmt == "9:16":
        return {"y_from": round(max(0.0, bottom - SUBTITLE_BAND), 3), "y_to": round(bottom, 3)}
    bottom = min(bottom, safe_zone(fmt)["captions_bottom"])
    band = SUBTITLE_BAND * 1920 / frame_size(fmt)[1]
    return {"y_from": round(max(0.0, bottom - band), 3), "y_to": round(bottom, 3), "format": fmt}


def h_get_transcript(ctx: ToolContext, args: dict) -> dict:
    """Реплики исходника вокруг кусков — для подрезки (время ИСХОДНИКА)."""
    timeline = clip_timeline(ctx.store, ctx.plan_id)
    cues = ctx.store.get_source_transcript(int(timeline["plan"]["source_id"]))
    lo = _num(args.get("from_sec"))
    hi = _num(args.get("to_sec"))
    if lo is None or hi is None:
        starts = [p["source_start"] for p in timeline["pieces"]] or [0.0]
        ends = [p["source_end"] for p in timeline["pieces"]] or [0.0]
        lo, hi = min(starts) - 10, max(ends) + 10
    if hi - lo > 900:
        hi = lo + 900
    out = [
        {"start": round(float(c["start"]), 2), "end": round(float(c["end"]), 2), "text": c.get("text", "")}
        for c in cues
        if float(c["end"]) > lo and float(c["start"]) < hi
    ]
    return {"time": "source", "from_sec": round(lo, 2), "to_sec": round(hi, 2), "cues": out[:400],
            "has_transcript": bool(cues)}


def _norm_word(text: str) -> str:
    return re.sub(r"[^\wё]+", "", str(text or "").lower().replace("ё", "е"))


def _word_match(word: str, want: str) -> int:
    """Насколько слово субтитров ``word`` — это искомое ``want`` (оба через ``_norm_word``): 4 — то же слово,
    3 — то же под звёздочками цензуры («о*********» ~ «обосралась»: первая буква и длина), 2 — та же основа
    (словоформа: «таракана» ~ «таракан»), 1 — содержит искомое (≥ 3 букв), 0 — нет."""
    raw = str(word or "").lower().replace("ё", "е")
    core = re.sub(r"[^\w*]+", "", raw)
    plain = core.replace("*", "")
    if not want or not core:
        return 0
    if plain == want and "*" not in core:
        return 4
    if "*" in core:
        return 3 if core[0] == want[0] and len(core) == len(want) else 0
    common = 0
    for a, b in zip(plain, want):
        if a != b:
            break
        common += 1
    if min(len(plain), len(want)) >= 4 and common >= max(4, min(len(plain), len(want)) - 2):
        return 2
    return 1 if len(want) >= 3 and want in plain else 0


def word_at(ctx: ToolContext, ref: Any) -> dict:
    """Слово субтитров по ссылке at_word → ``{t, word, line, word_i, note?}`` (t — начало, секунды клипа).

    ``ref`` — {"line": i, "word": j} (номера из get_clip.subtitles.lines) или {"text": "Артас", "nth": 1} /
    просто строка: n-е вхождение слова. По тексту ищется сначала то же слово (регистр и ё/е не важны), потом
    слово под звёздочками цензуры, потом словоформа, потом вхождение. QA 4 (клип 896): звук на «обосралась»
    встал на «это» строкой выше — номер строки съехал, а слово под звёздочками по тексту не находилось.
    Даны и номер, и текст, а они расходятся — верит тексту (note)."""
    timeline = clip_timeline(ctx.store, ctx.plan_id)
    lines = _subtitles_view(ctx.store, ctx.plan_id, timeline["pieces"])["lines"]
    if not lines:
        raise ToolError("at_word: у клипа нет субтитров — сначала generate_subtitles")
    if isinstance(ref, str):
        ref = {"text": ref}
    if not isinstance(ref, dict):
        raise ToolError("at_word: {line, word} или {text, nth}")
    by_index = None
    if ref.get("line") is not None:
        try:
            li, wj = int(ref.get("line")), int(ref.get("word", 0) or 0)
            word = lines[li]["words"][wj]
            by_index = {"t": float(word[1]), "word": str(word[0]), "line": li, "word_i": wj}
        except (TypeError, ValueError, IndexError) as exc:
            if ref.get("text") is None:
                raise ToolError("at_word: нет такой строки/слова (см. get_clip.subtitles.lines[i].words[j])") from exc
    if ref.get("text") is None:
        return by_index  # type: ignore[return-value]
    want, nth = _norm_word(ref["text"]), max(1, int(ref.get("nth") or 1))
    scored = [(_word_match(word[0], want), li, wj, word) for li, line in enumerate(lines)
              for wj, word in enumerate(line["words"])]
    best = max((sc for sc, *_rest in scored), default=0)
    hits = [x for x in scored if x[0] == best and best > 0]
    if by_index is not None and hits and not ref.get("nth"):
        # Номер и текст вместе: из подходящих по тексту — ближайшее к названной строке.
        hits.sort(key=lambda x: (abs(x[1] - by_index["line"]), x[1], x[2]))
        hit = hits[0]
    elif len(hits) >= nth:
        hit = hits[nth - 1]
    else:
        raise ToolError(f"at_word: слова «{ref['text']}» (вхождение {nth}) нет в субтитрах")
    _sc, li, wj, word = hit
    out = {"t": float(word[1]), "word": str(word[0]), "line": li, "word_i": wj}
    if by_index is not None and (by_index["line"], by_index["word_i"]) != (li, wj):
        out["note"] = (f"lines[{by_index['line']}].words[{by_index['word_i']}] — это «{by_index['word']}», а не "
                       f"«{ref['text']}»: поставил на «{word[0]}» (lines[{li}].words[{wj}])")
    elif best == 3:
        out["note"] = f"«{ref['text']}» в субтитрах под звёздочками цензуры: «{word[0]}»"
    return out


def word_time(ctx: ToolContext, ref: Any) -> float:
    """Начало слова субтитров в секундах клипа (как в get_clip) — см. ``word_at``."""
    return word_at(ctx, ref)["t"]


def h_list_subtitle_styles(ctx: ToolContext, _args: dict) -> dict:
    styles = []
    for profile in ctx.store.list_subtitle_profiles():
        styles.append({k: profile.get(k) for k in ("id", "name", "font_name", "font_size", "primary_color",
                                                     "highlight_color", "uppercase") if k in profile})
    return {"styles": styles}


def h_set_subtitles(ctx: ToolContext, args: dict) -> dict:
    render = _current_render(ctx)
    update = dict(render)
    if "on" in args and args["on"] is not None:
        update["subs_on"] = bool(args["on"])
    if args.get("style_id") is not None:
        style_id = int(args["style_id"])
        try:
            ctx.store.get_subtitle_profile(style_id)
        except KeyError as exc:
            raise ToolError(f"стиля субтитров {style_id} нет (см. list_subtitle_styles)") from exc
        update["sub_id"] = style_id
    if args.get("position_pct") is not None:
        update["sub_pos_pct"] = float(args["position_pct"])
    fresh = _save_render(ctx, update)
    note = ""
    if fresh["subs_on"] and not (ctx.store.get_clip_plan_subtitles(ctx.plan_id) or {}).get("lines"):
        note = "текста субтитров ещё нет — рендер распознает речь сам (или владелец нажмёт «Сделать субтитры»)"
    return {"ok": True, "subs_on": fresh["subs_on"], "sub_id": fresh["sub_id"], "sub_pos_pct": fresh["sub_pos_pct"],
            "note": note}


def accent_words(ctx: ToolContext) -> list[dict]:
    """Звуки-акценты клипа и слово субтитров под каждым: ``[{layer, asset, t, word, line, j}]`` (шкала clip).

    Слово — то, что начинается в пределах ``ACCENT_WORD_SEC`` от начала звука (at_word ставит звук на начало
    слова), иначе ближайшее звучащее в этот момент; нет слов рядом — word пусто. QA 4 (клип 896): агент
    поставил drama-boom «на обосралась», а звук стоял на «это» за 2.8 с до панчлайна, и никто этого не видел."""
    render = _current_render(ctx)
    sounds = [l for l in render.get("layers") or [] if isinstance(l, dict) and l.get("type") == "audio"
              and not l.get("hidden") and str((l.get("props") or {}).get("asset_id") or "").startswith("sfx:")]
    if not sounds:
        return []
    timeline = clip_timeline(ctx.store, ctx.plan_id)
    lines = _subtitles_view(ctx.store, ctx.plan_id, timeline["pieces"])["lines"]
    flat = [(li, j, w) for li, line in enumerate(lines) for j, w in enumerate(line["words"])]
    out = []
    for layer in sounds:
        t = float(layer.get("start") or 0)
        near = min(flat, key=lambda x: abs(float(x[2][1]) - t), default=None)
        hit = near if near and abs(float(near[2][1]) - t) <= ACCENT_WORD_SEC else next(
            (x for x in flat if float(x[2][1]) <= t < float(x[2][2]) + 0.05), None)
        out.append({"layer": layer.get("id"), "asset": str((layer.get("props") or {}).get("asset_id")),
                    "t": round(t, 2), "word": str(hit[2][0]) if hit else "",
                    **({"line": hit[0], "j": hit[1]} if hit else {})})
    return out


ACCENT_WORD_SEC = 0.12


def censor_keep_words(ctx: ToolContext) -> list[str]:
    """Слова, которые цензура без явного force_words не глушит: из кавычек запроса владельца (панчлайн,
    ключевое слово) и под звуками-акцентами (at_word)."""
    from app.censor import quoted_words

    keep = quoted_words(ctx.request) if ctx.request else []
    try:
        for item in accent_words(ctx):
            core = re.sub(r"[^\w*]+", "", item["word"].lower().replace("ё", "е"))
            # Слово под звуком уже со звёздочками — каким оно было, не знаем (мог быть и мат): не трогаем.
            if len(core) >= 3 and "*" not in core and core not in keep:
                keep.append(core)
    except Exception:  # noqa: BLE001 - без слоёв — только слова владельца
        pass
    return keep


def h_set_subtitle_words(ctx: ToolContext, args: dict) -> dict:
    """Заменить слова субтитров целиком: текст и тайминг каждого слова (секунды клипа до вырезов).

    Агент-монтажёр правил распознанные слова («2D модели» → «дорого») и сдвигал опоздавшее
    «Художник» через REST — инструмента не было. Строки пересобираются из слов."""
    from app.ai.speakers import lines_from_words
    from app.clip_subtitles import save_lines, tidy_words

    raw = args.get("words")
    if not isinstance(raw, list):
        raise ToolError("нужен список words [{word, start, end}] ([] — убрать субтитры клипа)")
    if not raw:
        # Пустой список — очистить: клип без речи (игра), распознались фантомные слова.
        ctx.before_change()
        save_lines(ctx.store, ctx.plan_id, [], [])
        return {"ok": True, "lines": 0, "words": 0, "note": "субтитры клипа убраны"}
    if len(raw) > 2000:
        raise ToolError("слишком много слов (больше 2000) — клип для шортса короче")
    words, problems = [], []
    for i, w in enumerate(raw):
        try:
            word = str(w["word"]).strip()
            start, end = float(w["start"]), float(w["end"])
        except (KeyError, TypeError, ValueError):
            problems.append(f"words[{i}]: нужны word, start, end")
            continue
        if not word or end <= start:
            problems.append(f"words[{i}]: пустое слово или end ≤ start")
            continue
        item = {"word": word[:80], "start": round(start, 3), "end": round(end, 3)}
        speaker = str(w.get("s") or w.get("speaker") or "").strip().upper()[:1]
        if speaker:
            if speaker not in "ABCD":
                problems.append(f"words[{i}]: s — голос A, B, C или D")
                continue
            item["speaker"] = speaker
        if w.get("emph") not in (None, 0, False, ""):
            from app.word_emphasis import level_of

            level = level_of(w.get("emph"))
            if not level:
                problems.append(f"words[{i}]: emph — 1 (pop) или 2 (shake)")
                continue
            item["emph"] = level
        words.append(item)
    if problems:
        raise ToolError("слова не приняты", problems)
    old_payload = ctx.store.get_clip_plan_subtitles(ctx.plan_id) or {}
    # Время агента — шкала clip (как в get_clip: с наплывами переходов); хранятся слова
    # «кусками подряд». Раньше слово из get_clip, отданное обратно как есть, уезжало
    # на длину наплыва.
    words = words_clip_to_plain(_clock(ctx), words, old_payload.get("words") or [])
    # Текст, которого нет среди распознанных слов, встаёт на настоящую речь: равномерная раскладка
    # вслепую давала субтитры раньше голоса (клипы 905–918).
    align_notes: list[str] = []
    if args.get("align", True) is not False:
        from app.speech_activity import fit_unknown_words, plan_speech_spans

        words, align_notes = fit_unknown_words(words, old_payload.get("words") or [],
                                               lambda: plan_speech_spans(ctx.store, ctx.plan_id))
    # Дефисные хвосты Whisper («какой» «-то») склеиваются, дубли на стыке строк убираются.
    words = tidy_words(words)
    ctx.before_change()
    lines = lines_from_words(words)
    payload = save_lines(ctx.store, ctx.plan_id, lines, words)
    from app.caption_marks import rebind_plan_marks

    moved = rebind_plan_marks(ctx.store, ctx.plan_id, old_payload.get("words") or [], payload.get("words") or [])
    out = {"ok": True, "lines": len(payload.get("lines") or []), "words": len(payload.get("words") or []),
           "speakers": sorted({w["speaker"] for w in payload.get("words") or [] if w.get("speaker")}),
           "time": "clip",
           "note": "строки собраны из слов: новая строка на конце предложения (. ! ?), паузе > 0.6 с, смене голоса s "
                   "и после 7 слов"}
    if align_notes:
        out["warnings"] = align_notes
    if moved:
        out["highlights"] = moved
    stored = (ctx.store.get_clip_plan_subtitles(ctx.plan_id) or {}).get("words") or []
    shifted = [b for a, b in zip(words, stored) if len(words) == len(stored) and "*" in str(b.get("word") or "")
               and abs(float(a["start"]) - float(b["start"])) > 0.1]
    if shifted:
        # QA 5 (клип 897): «п*****» на 30.07, а тишина мата с 30.6 — звёздочки раньше звука. Слово встаёт под тишину.
        out.setdefault("warnings", []).append(
            "слова-мат сдвинуты под свои отрезки глушения (звёздочки — когда глушится звук): "
            + ", ".join(f"«{b['word']}» {float(b['start']):.2f}–{float(b['end']):.2f} с (куски подряд)" for b in shifted[:4])
            + ". Глушение стоит по звуку (проверено рендером) — двигать слово мимо него не нужно")
    return out


def words_clip_to_plain(clock: Any, words: list[dict], old_words: list[dict]) -> list[dict]:
    """Слова агента (шкала clip) → «куски подряд», как хранятся субтитры.

    В зоне наплыва момент clip принадлежит двум кускам сразу. Слово, которое уже было в
    субтитрах (тот же текст и то же время в get_clip), остаётся в своём куске; новое —
    в том, где оно не нарушает порядок слов (хвост куска A раньше головы куска B)."""
    if not any(clock.pre.eaten):
        return words  # без наплывов шкалы совпадают
    known: list[tuple[str, float, int]] = []
    for w in old_words:
        try:
            plain = float(w["start"])
        except (KeyError, TypeError, ValueError):
            continue
        known.append((_norm_word(w.get("word")), clock.plain_to_clip(plain), clock.plain_piece(plain)))
    out, last = [], -1e9
    for w in words:
        start, end = float(w["start"]), float(w["end"])
        text = _norm_word(w.get("word"))
        piece = next((k for t, c, k in known if t == text and abs(c - start) <= 0.015), None)
        if piece is None:
            options = [(clock.clip_to_plain(start, k), k) for k in clock.pieces_at_clip(start)]
            fit = [o for o in options if o[0] >= last - 0.05]
            piece = (min(fit) if fit else max(options))[1] if options else None
        a, b = clock.clip_range_to_plain(start, end, piece)
        last = a
        out.append({**w, "start": round(a, 3), "end": round(max(b, a + 0.02), 3)})
    return out


def h_emphasize_words(ctx: ToolContext, args: dict) -> dict:
    """Эмфаза слов субтитров (app/word_emphasis.py): крупнее + pop, у shake — ещё встряска."""
    from app.word_emphasis import apply_levels, emphasized, gemini_pick, level_of, line_words, pick_heuristic

    payload = ctx.store.get_clip_plan_subtitles(ctx.plan_id) or {}
    words = payload.get("words") or []
    if not words:
        raise ToolError("у клипа нет слов субтитров — сначала generate_subtitles")
    style = args.get("style", args.get("level", "pop"))
    level = level_of(style)
    if level == 0 and str(style).strip().lower() not in ("off", "none", "0", "false"):
        raise ToolError("style — pop (крупнее и прыжок), shake (ещё крупнее, прыжок и встряска) или off (снять)")
    lines = line_words(payload)
    levels: dict[int, int] = {}
    if args.get("clear"):
        levels.update({i: 0 for i, w in enumerate(words) if w.get("emph")})
    problems: list[str] = []
    picked_by = None
    banned = _muted_word_indices(ctx, words)
    keys = _key_word_indices(ctx, words, banned)
    if args.get("auto"):
        taken = {i for i, w in enumerate(words) if w.get("emph") and not args.get("clear")}
        # Заглушённый мат — не кандидат (QA 5: подсвечен «х****»); слово владельца/под звуком — первое.
        todo = [[w for w in line if w["i"] not in banned] for line in lines
                if not any(w["i"] in taken for w in line)]
        picked: list[int] = [next(w["i"] for w in line if w["i"] in keys) for line in todo
                             if any(w["i"] in keys for w in line)]
        rest = [line for line in todo if not any(w["i"] in keys for w in line)]
        more: list[int] = []
        if str(args.get("pick") or "").lower() == "gemini":
            try:
                more, picked_by = gemini_pick(rest), "gemini"
            except Exception as exc:  # noqa: BLE001 - Gemini часто недоступен: эвристика
                problems.append(f"Gemini не выбрал ({exc}) — взял эвристику")
        if not more:
            more, picked_by = pick_heuristic(rest), "heuristic"
        levels.update({i: level or 1 for i in picked + [i for i in more if i not in banned]})
    refs = [("indices", r) for r in args.get("indices") or []] + [("words", r) for r in args.get("words") or []]         + [("at", r) for r in args.get("at") or []]
    for key, ref in refs:
        if key == "indices" and isinstance(ref, str) and ref.strip().isdigit():
            ref = int(ref)
        index = _emph_index(words, lines, ref)
        if index is None:
            problems.append(f"{key}: слова {ref!r} нет в субтитрах (номер из get_clip, текст или {{line, word}})")
            continue
        levels[index] = level
    if level:
        levels, notes = _guard_emphasis(words, lines, levels, banned, keys)
        problems += notes
    if not levels:
        raise ToolError("нечего менять: передай indices, words или at, auto: true или clear: true",
                        problems or None)
    ctx.before_change()
    fresh = apply_levels(words, levels)
    ctx.store.set_clip_plan_subtitles(ctx.plan_id, {**payload, "words": fresh})
    marked = emphasized({"words": fresh})
    out: dict = {"ok": True, "emphasized": marked[:200], "count": len(marked),
                 "note": "эмфаза у слов субтитров (emph: 1 pop — ×1.25 и прыжок; 2 shake — ×1.45, прыжок и "
                         "встряска): работает в слое captions (emphScale/emphColor слоя) и в прожигаемых субтитрах "
                         "(там без встряски). Едет за словом при вырезах и правке строк. Больше 1 слова на строку "
                         "и чаще раза в 2–3 с — уже шум. Проверь view_frame на моменте слова."}
    if picked_by:
        out["picked_by"] = picked_by
    if problems:
        out["warnings"] = problems
    return out


def _muted_word_indices(ctx: ToolContext, words: list[dict]) -> set[int]:
    """Слова субтитров под цензурой: со звёздочками или под отрезком тишины ``by: censor``."""
    out = {i for i, w in enumerate(words) if "*" in str(w.get("word") or "")}
    try:
        from app.censor import censored_word_indices

        out |= set(censored_word_indices(ctx.store, ctx.store.get_clip_plan(ctx.plan_id), words))
    except Exception:  # noqa: BLE001 - без цензуры — только звёздочки
        pass
    return out


def _key_word_indices(ctx: ToolContext, words: list[dict], banned: set[int]) -> set[int]:
    """Ключевые слова клипа: из кавычек запроса владельца и под звуками-акцентами (at_word) — их выделять в
    первую очередь (QA 5: в панчлайне «Я обосралась» выделено «Я» при ударе на «обосралась»)."""
    from app.censor import keep_match

    try:
        keep = censor_keep_words(ctx)
    except Exception:  # noqa: BLE001
        keep = []
    if not keep:
        return set()
    return {i for i, w in enumerate(words) if i not in banned and keep_match(w.get("word"), keep)}


def _guard_emphasis(words: list[dict], lines: list[list[dict]], levels: dict[int, int], banned: set[int],
                    keys: set[int]) -> tuple[dict[int, int], list[str]]:
    """Эмфаза никогда не на заглушённом мате; местоимение/служебное слово («Я») — заменяется ключевым словом той же
    строки (слово владельца/под звуком, иначе самое сильное), нет такого — не ставится. → (уровни, замечания)."""
    from app.word_emphasis import key_word_score, weak_word

    line_of = {w["i"]: line for line in lines for w in line}
    out: dict[int, int] = {}
    notes: list[str] = []
    for index, value in levels.items():
        if not value:
            out[index] = value
            continue
        word = str(words[index].get("word") or "") if 0 <= index < len(words) else ""
        if index in banned:
            notes.append(f"«{word}» — заглушённый мат: эмфазу на нём не ставлю (заглушённое не подсвечивают)")
            continue
        if not weak_word(word):
            out[index] = value
            continue
        line = [w for w in line_of.get(index, []) if w["i"] not in banned and w["i"] != index]
        better = next((w for w in line if w["i"] in keys), None)
        if better is None:
            scored = [(key_word_score(w.get("word")), w) for w in line]
            scored = [x for x in scored if x[0] > 0]
            better = max(scored, key=lambda x: x[0])[1] if scored else None
        if better is None:
            notes.append(f"«{word}» — служебное слово/местоимение, выделять нечего: эмфазу не ставлю")
            continue
        out[int(better["i"])] = value
        notes.append(f"«{word}» — местоимение/служебное, не удар: выделил «{better.get('word')}» "
                     + ("(слово владельца/под звуком-акцентом)" if better["i"] in keys else "(ключевое слово строки)"))
    return out, notes


def _emph_index(words: list[dict], lines: list[list[dict]], ref: Any) -> int | None:
    """Номер слова в субтитрах по ссылке агента: число, текст, {text, nth}, {line, word}, {i}."""
    if isinstance(ref, bool):
        return None
    if isinstance(ref, (int, float)):
        index = int(ref)
        return index if 0 <= index < len(words) else None
    if isinstance(ref, str):
        ref = {"text": ref}
    if not isinstance(ref, dict):
        return None
    if ref.get("i") is not None:
        return _emph_index(words, lines, ref.get("i"))
    if ref.get("line") is not None:
        try:
            return int(lines[int(ref["line"])][int(ref.get("word", 0))]["i"])
        except (TypeError, ValueError, IndexError, KeyError):
            return None
    want, nth, seen = _norm_word(ref.get("text") or ref.get("word")), int(ref.get("nth") or 1), 0
    for index, w in enumerate(words):
        if want and _norm_word(w.get("word")) == want:
            seen += 1
            if seen == nth:
                return index
    return None


def h_edit_subtitle_lines(ctx: ToolContext, args: dict) -> dict:
    from app.clip_subtitles import save_lines

    edits = args.get("edits")
    if not isinstance(edits, list) or not edits:
        raise ToolError("нужен список edits [{i, text}]")
    payload = ctx.store.get_clip_plan_subtitles(ctx.plan_id) or {}
    lines = [dict(line) for line in payload.get("lines") or []]
    if not lines:
        raise ToolError("у клипа нет субтитров")
    problems = []
    for edit in edits:
        try:
            index = int(edit.get("i"))
        except (TypeError, ValueError, AttributeError):
            problems.append(f"{edit!r}: нужен номер строки i")
            continue
        if not 0 <= index < len(lines):
            problems.append(f"строки {index} нет (0..{len(lines) - 1})")
            continue
        text = " ".join(str(edit.get("text") or "").split())
        if not text:
            problems.append(f"строка {index}: пустой текст (чтобы убрать строку, оставь её как есть)")
            continue
        lines[index]["text"] = text
    if problems:
        raise ToolError("правка субтитров не принята", problems)
    ctx.before_change()
    saved = save_lines(ctx.store, ctx.plan_id, lines)
    from app.caption_marks import rebind_plan_marks

    moved = rebind_plan_marks(ctx.store, ctx.plan_id, payload.get("words") or [], saved.get("words") or [])
    return {"ok": True, "lines": len(saved.get("lines") or []), **({"highlights": moved} if moved else {})}


def tools() -> list[Tool]:
    return [
        Tool(
            "get_transcript", "Читаю расшифровку видео",
            "Реплики ИСХОДНИКА (время исходника, не клипа) между from_sec и to_sec — чтобы подрезать "
            "кусок по фразе. Без параметров — вокруг кусков клипа. / Source transcript window (source time).",
            _obj({"from_sec": {"type": "number"}, "to_sec": {"type": "number"}}), h_get_transcript,
            label=lambda a: "Читаю расшифровку видео",
        ),
        Tool(
            "list_subtitle_styles", "Смотрю стили субтитров",
            "Стили субтитров (id, шрифт, цвета). / Subtitle styles.",
            _obj({}), h_list_subtitle_styles, scope="global",
        ),
        Tool(
            "set_subtitles", "Настраиваю субтитры",
            "Субтитры: on (вкл/выкл), style_id (см. list_subtitle_styles), position_pct (отступ снизу, 2..85 % "
            "высоты; 12–22 — над интерфейсом площадки; 60–80 — над видео в раскладке meme). / Subtitle settings.",
            _obj({"on": {"type": "boolean"}, "style_id": {"type": "integer"}, "position_pct": {"type": "number"}}),
            h_set_subtitles, mutating=True,
            label=lambda a: "Субтитры " + ("выкл" if a.get("on") is False else "вкл")
            + (f", стиль {a['style_id']}" if a.get("style_id") else "")
            + (f", {a['position_pct']}% снизу" if a.get("position_pct") is not None else ""),
        ),
        Tool(
            "set_subtitle_words", "Задаю слова субтитров",
            "Заменить слова субтитров целиком: words [{word, start, end, s?}] — текст и время каждого слова "
            "(шкала clip: до вырезов, с наплывами — ровно как в get_clip, слова оттуда можно вернуть как есть), "
            "s (или speaker) — голос (A главный, B/C/D — второй голос, TTS-донат, ролик): так помечают голоса "
            "без Gemini; emph — эмфаза слова (1 pop, 2 shake, см. emphasize_words). Подсветка слов (highlightWords) остаётся на тех же словах. Для исправления "
            "ошибок распознавания и опоздавших слов. Строки соберутся из слов сами: новая строка на конце "
            "предложения (. ! ? …), паузе > 0.6 с, смене голоса и после 7 слов; «какой» «-то» склеится в "
            "«какой-то». Новый текст, которого нет среди распознанных слов, встаёт на настоящую речь (VAD), "
            "в warnings — где голоса нет; align: false — оставить заданное время как есть. "
            "Пустой список [] — убрать субтитры клипа (клип без речи). "
            "/ Replace subtitle words with exact timings; [] clears them.",
            _obj({"words": {"type": "array", "items": _obj({"word": {"type": "string"}, "start": {"type": "number"},
                                                              "end": {"type": "number"},
                                                              "s": {"type": "string", "description": "голос: A, B, C, D"},
                                                              "emph": {"type": "integer", "minimum": 0, "maximum": 2,
                                                                       "description": "эмфаза: 1 pop, 2 shake"},
                                                              "speaker": {"type": "string",
                                                                          "description": "то же, что s"}},
                                                             ["word", "start", "end"])},
                  "align": {"type": "boolean", "description": "по умолчанию true: новый текст — по настоящей речи, известные слова, "
                                                              "уехавшие от распознанного больше чем на 0.5 с, — обратно; false — время как задано"}},
                 ["words"]),
            h_set_subtitle_words, mutating=True, label=lambda a: "Задаю слова субтитров",
        ),
        Tool(
            "emphasize_words", "Выделяю ключевые слова",
            "Эмфаза слов субтитров — ключевое слово крупнее и «выпрыгивает», как у топовых каналов (не только "
            "цветом highlightWords): indices — номера слов (порядок слов get_clip), words — тексты («миллион», первое "
            "вхождение), at — [{line, word}] (номера из get_clip.subtitles.lines) или [{text, nth}]; style: pop — ×1.25 и "
            "прыжок (по умолчанию), shake — ×1.45, прыжок и встряска (самый сильный удар), off — снять. auto: true — "
            "одно ключевое слово на строку само (число, слово с «!», иначе самое длинное не служебное; pick: "
            "\"gemini\" — выбирает Gemini, недоступен — та же эвристика), строки, где эмфаза уже есть, не трогает; "
            "clear: true — сначала снять все. Заглушённый мат (звёздочки) не выделяется; местоимение/служебное («Я») "
            "заменяется ключевым словом строки (слово владельца в кавычках или под звуком-акцентом). Работает в слое captions (размер emphScale и цвет emphColor слоя) и в "
            "прожигаемых субтитрах (там без встряски); метка едет со словом при вырезах и правке строк. "
            "/ Emphasize key subtitle words: bigger + pop (shake adds a jolt).",
            _obj({"words": {"type": "array", "items": {"type": "string"}, "description": "тексты слов"},
                  "indices": {"type": "array", "items": {"type": "integer"},
                              "description": "номера слов в субтитрах (порядок слов get_clip)"},
                  "at": {"type": "array", "items": _obj({"line": {"type": "integer"}, "word": {"type": "integer"},
                                                          "text": {"type": "string"}, "nth": {"type": "integer"}})},
                  "style": {"type": "string", "enum": ["pop", "shake", "off"]},
                  "auto": {"type": "boolean"}, "pick": {"type": "string", "enum": ["heuristic", "gemini"]},
                  "clear": {"type": "boolean"}}),
            h_emphasize_words, mutating=True,
            label=lambda a: "Выделяю ключевые слова" + (" (авто)" if a.get("auto") else ""),
        ),
        Tool(
            "edit_subtitle_lines", "Правлю субтитры",
            "Исправить текст строк субтитров: edits [{i, text}] (i — номер из get_clip). Время слов "
            "сохранится, где текст не менялся. / Fix subtitle line text.",
            _obj({"edits": {"type": "array", "items": _obj({"i": {"type": "integer"}, "text": {"type": "string"}},
                                                           ["i", "text"])}}, ["edits"]),
            h_edit_subtitle_lines, mutating=True, label=lambda a: f"Правлю субтитры ({len(a.get('edits') or [])} стр.)",
        ),
        Tool(
            "separate_voices", "Разбираю голоса",
            "Послушать звук клипа и расшифровать каждый голос отдельно (стример A, гость/донат/ролик — B, C): "
            "возвращает потерянные под чужим голосом фразы, метит слова, строки не смешивают людей. "
            "Субтитры только для речи стримера и живых людей, не для голосов игры: голоса игры (персонажи, агенты, "
            "диктор), ролика и озвучки доната сервис из субтитров убирает сам (dropped); all_voices: true — оставить "
            "все, только по просьбе владельца. auto_edit делает это сам; вызывай, если перераспознал речь после "
            "auto_edit. / Separate voices.",
            _obj({"all_voices": {"type": "boolean"}}, []),
            h_separate_voices, mutating=True, label=lambda a: "Разбираю голоса",
        ),
        Tool(
            "register_voice", "Запоминаю голос",
            "Банк голосов канала: запомнить, чей это голос. name — имя («Мура», «Глеб», «TTS чата»), kind — person или bot "
            "(озвучка чата — обычная запись). Откуда: speaker (все слова буквы A/B/C/D этого клипа), или start/end клипа "
            "(шкала куски подряд), или source_id со start/end секунд источника. Нужно от 6 с чистой речи, надёжно от 15 с; "
            "то же имя — новый образец к прежнему. Бери отрезки, где человек говорит один. / Remember a voice.",
            _obj({"name": {"type": "string"}, "speaker": {"type": "string"}, "start": {"type": "number"}, "end": {"type": "number"},
                  "source_id": {"type": "integer"}, "kind": {"type": "string", "enum": ["person", "bot"]}}, ["name"]),
            h_register_voice, label=lambda a: f"Запоминаю голос: {a.get('name') or ''}",
        ),
        Tool(
            "identify_voices", "Узнаю голоса",
            "Кто есть кто в клипе по банку голосов (после separate_voices): буквам A/B/C/D ставит имена, только если "
            "совпадение уверенное; остальное честно в unsure. labels (по умолчанию true) — в готовых субтитрах реплика "
            "не главного голоса идёт как «Глеб: …», имя в текст руками не пиши. conflicts — реплики, которые звучат как "
            "другой человек; reassign: true отдаёт их его букве. Реплики короче 2.5 с не сравниваются: "
            "по ним опознание ненадёжно. / Identify clip voices by the voice bank.",
            _obj({"labels": {"type": "boolean"}, "reassign": {"type": "boolean"}}, []),
            h_identify_voices, mutating=True, label=lambda a: "Узнаю голоса",
        ),
        Tool(
            "list_voices", "Смотрю банк голосов",
            "Голоса в банке канала этого клипа и общие (узнавание, кто говорит): id, name, kind, seconds, samples. "
            "tts_voices — голоса синтеза Gemini для add_voiceover/tts_preview с полом и тембром; narrator_voice — "
            "рассказчик проекта клипа. / List the voice bank and TTS voices.",
            _obj({}, []), h_list_voices, label=lambda a: "Смотрю банк голосов",
        ),
        Tool(
            "delete_voice", "Удаляю голос из банка",
            "Убрать голос из банка (voice_id из list_voices). / Delete a bank voice.",
            _obj({"voice_id": {"type": "integer"}}, ["voice_id"]), h_delete_voice, label=lambda a: "Удаляю голос из банка",
        ),
        Tool(
            "snap_subtitles_to_speech", "Ставлю субтитры на голос",
            "Поправить находки lint_clip sub_sync: слова страницы, разошедшейся со сказанным, встают на время "
            "распознанного голоса; строка, начатая в тишине, — на настоящее начало речи (VAD). Текст не меняется. "
            "Потерянные фразы, строки без голоса и нерасслышанные — в ответе (rest), их решает человек. "
            "/ Snap subtitle words to recognised speech.",
            _obj({}, []), h_snap_subtitles_to_speech, mutating=True, label=lambda a: "Ставлю субтитры на голос",
        ),
        Tool(
            "translate_subtitles", "Перевожу субтитры на русский",
            "Иноязычный клип (whisper: язык не ru) → русские субтитры: перевод по фразам (Gemini, бесплатно), "
            "время фраз оригинала, русские слова разложены внутри фразы. Только НА русский. mode: ru — только "
            "русский; both — русский + оригинал вторым слоем (слой captions speakers ['orig'] мельче над русским, "
            "прожигаемые субтитры — строкой над караоке); off — вернуть оригинал. Уже переведено — смена режима "
            "без модели; retranslate — перевести заново. Gemini недоступен — переведи сам: translations — "
            "строки по порядку фраз (строк субтитров из get_clip). force — если whisper ошибся с языком. "
            "Озвучка — dub_clip. / Translate foreign subtitles into Russian.",
            _obj({"mode": {"type": "string", "enum": ["ru", "both", "off"]},
                  "translations": {"type": "array", "items": {"type": "string"}},
                  "retranslate": {"type": "boolean"}, "force": {"type": "boolean"}}),
            h_translate_subtitles, mutating=True,
            label=lambda a: "Снимаю перевод" if a.get("mode") == "off" else "Перевожу субтитры на русский",
        ),
        Tool(
            "dub_clip", "Озвучка RU",
            "Озвучить русский перевод клипа (после translate_subtitles): фраза → речь → подгонка под окно "
            "реплики (×0.8–1.25, не влезло — обрезка, в ответе overflow) → слои audio dub_ru_* поверх "
            "оригинала, оригинал под фразами приглушён. Только для ПЕРЕВОДА; свой текст на клип или холст — "
            "add_voiceover. provider: gemini — БЕСПЛАТНО (Gemini TTS по ключу сервиса, синтез сразу, confirm не "
            "нужен; voice — один из 30 голосов Gemini: Kore, Puck, Charon, Zephyr, Fenrir, Aoede… — пол и тембр в "
            "описании tts_preview); polza (по умолчанию) — ПЛАТНО: без confirm ТОЛЬКО оценка ₽, покажи её "
            "владельцу; confirm=true — ТОЛЬКО по явному «да» владельца на эту сумму, работает лишь при "
            "DUBBING_ENABLED=1 и не дороже budget_rub (по умолчанию 200 ₽); none — сухой прогон тоном (бесплатно, "
            "проверить окна). remove — снять слои озвучки. / Russian dub: gemini free, polza paid (estimate, "
            "confirm with owner).",
            _obj({"provider": {"type": "string", "enum": ["gemini", "polza", "none"]}, "voice": {"type": "string"},
                  "model": {"type": "string"},
                  "style": {"type": "string", "description": "не используется: Gemini зачитывает указание вслух"},
                  "confirm": {"type": "boolean"},
                  "budget_rub": {"type": "number", "minimum": 0}, "stretch": {"type": "boolean"},
                  "remove": {"type": "boolean"}}),
            h_dub_clip, mutating=True,
            label=lambda a: ("Снимаю озвучку" if a.get("remove") else
                             "Озвучиваю по-русски" if a.get("confirm") or str(a.get("provider") or "").lower()
                             in ("gemini", "none") else "Считаю стоимость озвучки"),
        ),
        Tool(
            "generate_subtitles", "Распознаю речь заново",
            "Заново распознать речь всего клипа и заменить субтитры (ручные правки текста пропадут). "
            "Нужно, когда get_clip предупреждает, что на кусках с речью субтитров нет. После — auto_edit. "
            "Субтитры только для речи стримера и живых людей, не для голосов игры: у записи с отдельной дорожкой "
            "микрофона распознаётся только она; голоса игры и ролика убирает separate_voices. "
            "/ Re-transcribe the clip.",
            _obj({}, []),
            h_generate_subtitles, mutating=True, label=lambda a: "Распознаю речь клипа заново",
        ),
    ]
