"""Субтитры клипа ДО рендера: сгенерировать, прочитать, поправить, сохранить.

Until now subtitles existed only as a side effect of rendering: the render cut
the clip, transcribed its audio and burned the result in one go, so the owner
first saw the text (and its typos) in the finished video. Now a clip plan can
carry its own subtitles:

    clip_plans.subtitles_json = {
      "lines":    [{"start", "end", "text"}],   # what the editor shows and edits
      "words":    [{"word", "start", "end"}],   # karaoke timing
      "language", "provider", "model", "edited"
    }

Times are in CLIP time (0 = first frame of the clip), exactly like the audio the
render burns onto. The render reuses these words instead of transcribing again,
so what you fixed in the editor is what ends up in the video.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.subtitles.contracts import SubtitleResult, SubtitleSegment, SubtitleWord
from app.subtitles.registry import get_subtitle_provider, subtitle_model_for_profile
from app.subtitles.timing import normalize_subtitle_timeline
from app.settings import settings
from app.source_audio import is_default_mix, mix_chain, source_mix, speech_mix
from app.word_emphasis import level_of

MAX_LINES = 400


def build_clip_audio_args(
    source_path: Path, pieces: list[tuple[float, float]], out_path: Path, tracks: Any = None,
    ops: list[dict] | None = None,
) -> list[str]:
    """One wav with the clip's pieces played back to back — the audio the render burns onto.

    Каждый кусок — свой вход с быстрым поиском (``-ss``/``-t`` до ``-i``): ffmpeg читает
    только нужные секунды. Раньше был один вход и ``atrim``: ffmpeg декодировал весь
    4-часовой исходник с начала до конца — 11 ГБ через кэш WSL на каждый клип, и
    Docker Desktop падал от нехватки памяти.

    ``tracks`` — звуковые дорожки источника (``source_mix`` — как в рендере, ``speech_mix`` —
    для распознавания): номера или ``[{track, gain_db}]``. None — первая дорожка (граф прежний).
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    inputs: list[str] = []
    for start, end in pieces:
        inputs += ["-ss", f"{start:.3f}", "-t", f"{max(0.01, end - start):.3f}", "-i", str(source_path)]
    from app.audio_edit import ops_in_window, source_graph

    windows = [ops_in_window(ops or [], a, b) for a, b in pieces]
    if any(windows):
        # Тишина/громкость/эффекты одной дорожки и эффекты на отрезке (app/audio_edit.py) — как в рендере.
        chains = [source_graph(i, list(tracks or []), {"ops": windows[i], "src0": pieces[i][0]}, f"p{i}r")
                  + f";[p{i}r]aresample=48000,aformat=sample_fmts=s16:channel_layouts=stereo[p{i}]"
                  for i in range(len(pieces))]
        joined = "".join(f"[p{i}]" for i in range(len(pieces)))
        graph = ";".join(chains) + f";{joined}concat=n={len(pieces)}:v=0:a=1[out]"
    elif tracks and not is_default_mix(tracks):
        chains = [mix_chain(i, list(tracks), f"p{i}") for i in range(len(pieces))]
        if len(pieces) > 1:
            joined = "".join(f"[p{i}]" for i in range(len(pieces)))
            graph = ";".join(chains) + f";{joined}concat=n={len(pieces)}:v=0:a=1[out]"
        else:
            graph = chains[0].replace("[p0]", "[out]")
    elif len(pieces) > 1:
        graph = "".join(f"[{i}:a]" for i in range(len(pieces))) + f"concat=n={len(pieces)}:v=0:a=1[out]"
    else:
        graph = "[0:a]anull[out]"
    return [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        *inputs,
        "-filter_complex", graph,
        "-map", "[out]", "-ac", "1", "-ar", "16000", str(out_path),
    ]


def _words_of_line(words: list[dict], start: float, end: float) -> list[dict]:
    return [w for w in words if float(w["start"]) < end - 0.001 and float(w["end"]) > start + 0.001]


def words_by_line(lines: list[dict], words: list[dict]) -> list[list[dict]]:
    """Слова по строкам: каждое слово — ровно в ОДНОЙ строке (где оно больше всего лежит).

    Раньше слово брала каждая строка, которую оно задевает: слово на стыке строк
    («Слушай,» в строках 12 и 13) показывалось дважды, а после правки строки
    (rebuild_words) дублировалось в словах насовсем."""
    out: list[list[dict]] = [[] for _ in lines]
    spans = [(float(l["start"]), float(l["end"])) for l in lines]
    for w in words:
        a, b = float(w["start"]), float(w["end"])
        best, best_overlap = None, 0.001
        for index, (ls, le) in enumerate(spans):
            overlap = min(b, le) - max(a, ls)
            if overlap > best_overlap:
                best, best_overlap = index, overlap
        if best is not None:
            out[best].append(w)
    return out


# Whisper отрывает дефисную часть слова в отдельное «слово»: «какой» «-то», «кунг» «-фу», «ну» «-ка».
_HYPHEN_TAIL = re.compile(r"^[-‐‑]\w")
_WORD_END = re.compile(r"\w$")


def glue_hyphen_words(words: list[dict]) -> list[dict]:
    """«какой» + «-то» → «какой-то» одним словом (время — от начала первого до конца второго).

    Агент-монтажёр: «Whisper отрывает дефисную часть» — в субтитрах выходило «какой -то»
    и «-то» могло уехать в следующую строку. Тире с пробелами («да - нет») не трогаем."""
    out: list[dict] = []
    for w in words:
        token = str(w.get("word") or "").strip()
        prev = out[-1] if out else None
        if prev is not None and prev.get("speaker") == w.get("speaker"):
            head = str(prev["word"]).strip()
            if _HYPHEN_TAIL.match(token) and _WORD_END.search(head):
                out[-1] = {**prev, "word": head + "-" + token[1:], "end": max(float(prev["end"]), float(w["end"]))}
                continue
            if head.endswith("-") and len(head) > 1 and token[:1].isalnum():
                out[-1] = {**prev, "word": head + token, "end": max(float(prev["end"]), float(w["end"]))}
                continue
        out.append({**w, "word": token} if token != w.get("word") else dict(w))
    return out


def tidy_words(words: list[dict]) -> list[dict]:
    """Слова по времени: дефисные хвосты склеены, точные дубли (то же слово на том же
    месте — след прежнего бага стыка строк) убраны, конец слова не заходит на начало
    следующего того же голоса (иначе слово задевает чужую строку)."""
    ordered = sorted((dict(w) for w in words if str(w.get("word") or "").strip()), key=lambda w: float(w["start"]))
    clean: list[dict] = []
    for w in glue_hyphen_words(ordered):
        last = clean[-1] if clean else None
        if last is not None and last["word"] == w["word"] and abs(float(last["start"]) - float(w["start"])) < 0.05:
            continue
        clean.append(w)
    for a, b in zip(clean, clean[1:]):
        if a.get("speaker") == b.get("speaker") and float(b["start"]) > float(a["start"]) and float(a["end"]) > float(b["start"]):
            a["end"] = round(float(b["start"]), 3)
    return clean


def _split_words(text: str, start: float, end: float) -> list[dict]:
    """Spread a line's words over its span (used for lines the owner retyped)."""
    tokens = [t for t in re.split(r"\s+", text.strip()) if t]
    if not tokens:
        return []
    total = sum(len(t) for t in tokens) or 1
    span = max(0.12 * len(tokens), end - start)
    out, cursor = [], start
    for token in tokens:
        share = span * (len(token) / total)
        out.append({"word": token, "start": round(cursor, 3), "end": round(cursor + share, 3)})
        cursor += share
    return out


def _same_text(a: str, b: str) -> bool:
    norm = lambda s: re.sub(r"\s+", " ", s or "").strip().lower()  # noqa: E731
    return norm(a) == norm(b)


def _align_line(text: str, inside: list[dict], start: float, end: float) -> list[dict]:
    """Переписанная строка: совпавшие слова держат время распознанных, поправленные — время слова,
    которое заменили; остальные встают между соседями. Голос строки не теряем."""
    import difflib

    from app.ai.speakers import _fill_timing, _norm

    tokens = [t for t in re.split(r"\s+", text.strip()) if t]
    if not tokens:
        return []
    speaker = next((w["speaker"] for w in inside if w.get("speaker")), None)
    timing: list = [None] * len(tokens)
    if len(tokens) == len(inside):
        timing = [(max(start, float(w["start"])), min(end, float(w["end"]))) for w in inside]
    else:
        sm = difflib.SequenceMatcher(a=[_norm(t) for t in tokens], b=[_norm(str(w["word"])) for w in inside], autojunk=False)
        for block in sm.get_matching_blocks():
            for k in range(block.size):
                w = inside[block.b + k]
                timing[block.a + k] = (max(start, float(w["start"])), min(end, float(w["end"])))
    if not any(timing):
        # Ни одного общего слова: речь строки от первого до последнего распознанного слова.
        span = {"word": "", "start": float(inside[0]["start"]), "end": float(inside[-1]["end"])}
        return [{**w, **({"speaker": speaker} if speaker else {})}
                for w in _split_words(text, max(start, span["start"]), min(end, span["end"]))]
    filled = _fill_timing(tokens, timing, {"s": speaker or "", "start": start, "end": end})
    return [{k: v for k, v in w.items() if k != "speaker" or v} for w in filled]


def rebuild_words(lines: list[dict], old_words: list[dict]) -> list[dict]:
    """Keep the original per-word timing where the text is untouched; re-spread it
    where the owner rewrote the line (so karaoke never breaks on an edit)."""
    return _rebuild_words(lines, old_words)[0]


def _rebuild_words(lines: list[dict], old_words: list[dict]) -> tuple[list[dict], list[int]]:
    """→ (слова, номера слов без опоры на распознанное — их время раскладка вслепую, ``fit_blind_words``)."""
    words: list[dict] = []
    blind: list[int] = []
    for line, inside in zip(lines, words_by_line(lines, tidy_words(old_words))):
        start, end = float(line["start"]), float(line["end"])
        if inside and _same_text(" ".join(w["word"] for w in inside), line["text"]):
            words.extend(
                {"word": w["word"], "start": max(start, float(w["start"])), "end": min(end, float(w["end"])),
                 **({"speaker": w["speaker"]} if w.get("speaker") else {}),
                 **({"emph": w["emph"]} if w.get("emph") else {})}
                for w in inside
            )
        elif inside:
            words.extend(_align_line(line["text"], inside, start, end))
        else:
            spread = _split_words(line["text"], start, end)
            blind.extend(range(len(words), len(words) + len(spread)))
            words.extend(spread)
    return words, blind


def fit_blind_words(store: Any, clip_plan_id: int, words: list[dict], blind: list[int]) -> tuple[list[dict], list[str]]:
    """Строки без единого распознанного слова внутри — к настоящей речи (VAD) в их границах.
    Голоса нет или звука нет — время остаётся, и об этом warning: равномерно вслепую молча не бывает."""
    if not blind:
        return words, []
    from app.speech_activity import plan_speech_spans, spans_within, spread_over_speech

    try:
        spans = plan_speech_spans(store, clip_plan_id)
    except Exception:  # noqa: BLE001 - нет исходника: время как есть, но честно
        return words, ["время новых строк не проверено по звуку — исходник недоступен"]
    out = list(words)
    notes: list[str] = []
    runs: list[list[int]] = []
    for index in blind:
        if runs and index == runs[-1][-1] + 1 and float(out[index]["start"]) <= float(out[index - 1]["end"]) + 0.02:
            runs[-1].append(index)
        else:
            runs.append([index])
    for run in runs:
        items = [out[i] for i in run]
        a, b = float(items[0]["start"]), float(items[-1]["end"])
        parts = spans_within(spans, a, b)
        if sum(e - s for s, e in parts) < 0.08 * len(run):
            notes.append(f"«{' '.join(str(w['word']) for w in items)[:60]}» ({a:.2f} с): голоса на этом месте нет — проверь время")
            continue
        for i, w in zip(run, spread_over_speech(items, parts)):
            out[i] = w
    return out, notes


_TOKEN_EDGE = re.compile(r"^[^\w*]+|[^\w*]+$", re.UNICODE)


def _tokens(text: str) -> list[str]:
    """Слова текста для сравнения: строчными, без пунктуации по краям (звёздочки маски — часть слова)."""
    return [t for t in (_TOKEN_EDGE.sub("", tok).lower() for tok in str(text or "").split()) if t]


def _line_words(lines: list[dict], words: list[dict]) -> list[list[dict]]:
    """Слова по строкам (как ``words_by_line``), но у строк с голосом (``speaker``) — только слова того же
    голоса: реплика второго голоса поверх первой не уезжает в чужую строку."""
    if not any(line.get("speaker") for line in lines):
        return words_by_line(lines, words)
    out: list[list[dict]] = [[] for _ in lines]
    for w in words:
        a, b = float(w["start"]), float(w["end"])
        voice = w.get("speaker") or "A"
        best, best_overlap = None, 0.001
        for index, line in enumerate(lines):
            if line.get("speaker") and line["speaker"] != voice:
                continue
            overlap = min(b, float(line["end"])) - max(a, float(line["start"]))
            if overlap > best_overlap:
                best, best_overlap = index, overlap
        if best is not None:
            out[best].append(w)
    return out


def sync_lines_with_words(payload: Any) -> Any:
    """Текст строк — по словам, если они разошлись (QA 02.11, клип 892).

    После censor и перераспределения слов по строкам ``lines[].text`` отставал от ``words``: в тексте
    строки остался немаскированный мат («хуйня» при «х****» в словах), а строки, чьи слова ушли в
    соседнюю, остались дублями с пустыми словами. Текст берут SRT-экспорт и описания — он обязан
    совпадать с тем, что в кадре. Правило: у строки со словами текст, расходящийся со словами не только
    регистром и пунктуацией, пересобирается из слов; строка без слов, все слова которой уже есть в
    соседних строках, — дубль, её нет. Строки без слов с другим текстом и совпадающие — как были."""
    if not isinstance(payload, dict) or not payload.get("lines") or not payload.get("words"):
        return payload
    lines = [dict(line) for line in payload["lines"] if isinstance(line, dict)]
    words = [w for w in payload["words"] if isinstance(w, dict) and str(w.get("word") or "").strip()]
    try:
        inside = _line_words(lines, words)
    except (KeyError, TypeError, ValueError):
        return payload
    out: list[dict] = []
    changed = False
    for index, (line, own) in enumerate(zip(lines, inside)):
        if not own:
            near = [w for k in (index - 1, index + 1) if 0 <= k < len(inside) for w in inside[k]]
            near_tokens = set(_tokens(" ".join(str(w["word"]) for w in near)))
            tokens = _tokens(line.get("text") or "")
            if tokens and near_tokens and all(t in near_tokens for t in tokens):
                changed = True  # слова этой строки уже показаны соседней — дубль
                continue
            out.append(line)
            continue
        joined = " ".join(str(w["word"]).strip() for w in own)
        if _tokens(joined) != _tokens(line.get("text") or ""):
            line["text"] = joined[:300]
            changed = True
        out.append(line)
    return {**payload, "lines": out} if changed else payload


def normalize_lines(raw: Any, duration: float | None = None) -> list[dict]:
    out: list[dict] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            start = max(0.0, float(item.get("start", 0)))
            end = float(item.get("end", 0))
        except (TypeError, ValueError):
            continue
        text = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()
        if not text or end <= start:
            continue
        if duration:
            end = min(end, duration + 0.2)
        out.append({"start": round(start, 3), "end": round(end, 3), "text": text[:300]})
        if len(out) >= MAX_LINES:
            break
    return sorted(out, key=lambda x: x["start"])


def payload_from_result(result: SubtitleResult, provider: str, model: str) -> dict:
    """Строки — из слов (``lines_from_words``): рвутся на конце предложения, паузе и по
    длине, каждое слово ровно в одной строке. Сегменты движка — только если слов нет."""
    from app.ai.speakers import lines_from_words

    words = tidy_words([{"word": w.word, "start": round(w.start, 3), "end": round(w.end, 3)} for w in result.words])
    if words:
        lines = normalize_lines(lines_from_words(words), result.duration)
    else:
        lines = [{"start": round(s.start, 3), "end": round(s.end, 3), "text": s.text.strip()} for s in result.segments]
        lines = normalize_lines(lines, result.duration)
    if not lines and words:  # a provider that only returns words
        lines = normalize_lines(
            [{"start": words[0]["start"], "end": words[-1]["end"], "text": " ".join(w["word"] for w in words)}]
        )
    return {
        "lines": lines,
        "words": words or rebuild_words(lines, []),
        "language": result.language,
        "provider": provider,
        "model": model,
        "edited": False,
        "duration": round(result.duration, 3),
    }


def result_from_payload(payload: dict) -> SubtitleResult:
    """Back to what write_ass_subtitles() expects."""
    lines = payload.get("lines") or []
    words = payload.get("words") or []
    return SubtitleResult(
        text=" ".join(line["text"] for line in lines),
        language=str(payload.get("language") or ""),
        duration=float(payload.get("duration") or (lines[-1]["end"] if lines else 0)),
        segments=[SubtitleSegment(start=float(l["start"]), end=float(l["end"]), text=l["text"]) for l in lines],
        words=[SubtitleWord(word=w["word"], start=float(w["start"]), end=float(w["end"]), emph=level_of(w.get("emph")))
               for w in words],
    )


def shift_for_transition(
    payload: dict, pieces: list[tuple[float, ...]], overlap: float | list[float]
) -> dict:
    """A real transition overlaps neighbours, so every piece after the first
    starts earlier in the rendered clip than in the plain concatenation the
    subtitles were timed against.

    ``overlap`` — одно число на все склейки или список overlap по склейкам
    (``app.transitions.part_overlaps``): рендер режет переход до 40% соседней
    части, а «умная» склейка соседних кусков исходника идёт встык (0).
    """
    joins = max(0, len(pieces) - 1)
    if isinstance(overlap, (int, float)):
        per_join = [float(overlap)] * joins
    else:
        per_join = [float(o) for o in list(overlap)[:joins]]
        per_join += [0.0] * (joins - len(per_join))
    if joins == 0 or not any(o > 0 for o in per_join):
        return payload
    bounds, t = [], 0.0
    for piece in pieces:
        t += _span_length(piece)
        bounds.append(t)
    eaten = [0.0]
    for o in per_join:
        eaten.append(eaten[-1] + o)

    def shift(value: float) -> float:
        k = sum(1 for b in bounds[:-1] if value >= b)
        return max(0.0, value - eaten[k])

    return {
        **payload,
        "lines": [{**l, "start": shift(float(l["start"])), "end": shift(float(l["end"]))} for l in payload.get("lines") or []],
        "words": [{**w, "start": shift(float(w["start"])), "end": shift(float(w["end"]))} for w in payload.get("words") or []],
    }


def _span_length(piece: Any) -> float:
    """Длина куска на шкале клипа: ``(start, end)`` или ``(start, end, speed)``."""
    start, end = float(piece[0]), float(piece[1])
    speed = float(piece[2]) if len(piece) > 2 and float(piece[2]) > 0 else 1.0
    return max(0.0, end - start) / speed


def _layout(pieces: list) -> list[tuple[float, float, float, float]]:
    """[(src_start, src_end, clip_offset, speed)] — где каждый кусок лежит в склейке
    (у куска со скоростью секунда клипа — ``speed`` секунд исходника)."""
    out, offset = [], 0.0
    for piece in pieces:
        start, end = float(piece[0]), float(piece[1])
        speed = float(piece[2]) if len(piece) > 2 and float(piece[2]) > 0 else 1.0
        if end > start:
            out.append((start, end, offset, speed))
            offset += (end - start) / speed
    return out


def _old_index(layout: list, t: float) -> int:
    for index, (start, end, offset, speed) in enumerate(layout):
        if t < offset + (end - start) / speed:
            return index
    return len(layout) - 1


def _to_source(layout: list, t: float, index: int | None = None) -> float:
    start, _end, offset, speed = layout[_old_index(layout, t) if index is None else index]
    return start + max(0.0, t - offset) * speed


def _piece_at(layout: list, x: float, prefer: int) -> tuple[float, float, float, float] | None:
    """Новый кусок, где лежит момент исходника ``x``; сначала тот же по номеру
    (куски могут перекрываться — слово остаётся в своём)."""
    inside = lambda p: p[0] - 0.001 <= x < p[1] + 0.001  # noqa: E731
    if prefer < len(layout) and inside(layout[prefer]):
        return layout[prefer]
    return next((p for p in layout if inside(p)), None)


def _to_clip(piece: tuple[float, float, float, float], x: float) -> float:
    start, end, offset, speed = piece
    return round(offset + (min(max(x, start), end) - start) / speed, 3)


def remap_to_pieces(payload: dict, old_pieces: list, new_pieces: list) -> dict:
    """Перенести субтитры на новый набор кусков: слово привязано ко времени
    исходника и уезжает вместе со своим куском; слова из вырезанного пропадают.

    Агент-монтажёр: «после правки кусков субтитры не переезжают» — подрезал
    кусок, а текст остался на старых секундах и разъехался с речью."""
    old, new = _layout(old_pieces), _layout(new_pieces)
    if not old or not new:
        return payload

    def move(item: dict) -> dict | None:
        a, b = float(item["start"]), float(item["end"])
        index = _old_index(old, (a + b) / 2)
        piece = _piece_at(new, _to_source(old, (a + b) / 2, index), index)
        if piece is None:
            return None
        src_a, src_b = _to_source(old, a, index), _to_source(old, b, index)
        start, end = _to_clip(piece, src_a), _to_clip(piece, src_b)
        return {**item, "start": start, "end": max(end, round(start + 0.05, 3))}

    old_words = payload.get("words") or []
    words = [m for m in (move(w) for w in old_words) if m]
    lines = []
    old_lines = payload.get("lines") or []
    for line, inside in zip(old_lines, words_by_line(old_lines, old_words)):
        if not inside:
            moved = move(line)
            if moved:
                lines.append(moved)
            continue
        kept = [m for m in (move(w) for w in inside) if m]
        if not kept:
            continue
        text = line["text"] if len(kept) == len(inside) else " ".join(w["word"] for w in kept)
        lines.append({**line, "text": text, "start": min(w["start"] for w in kept), "end": max(w["end"] for w in kept)})
    total = round(sum((e - s) / v for s, e, _o, v in new), 3)
    out = {
        **payload,
        "words": sorted(words, key=lambda w: w["start"]),
        "lines": normalize_lines(lines, total),
        "duration": total,
        "pieces": [[s, e] if v == 1.0 else [s, e, v] for s, e, _o, v in new],
    }
    tr = payload.get("translation")
    if isinstance(tr, dict) and isinstance(tr.get("original"), dict):
        # Оригинал переведённых субтитров (app/translation.py) едет за кусками так же.
        moved = remap_to_pieces({**tr["original"], "pieces": None}, old_pieces, new_pieces)
        out["translation"] = {**tr, "original": {k: moved.get(k) for k in ("lines", "words", "language")}}
    return out


# Дорожка похожа на голос, если между тишиной и громкими местами столько дБ (микрофон:
# пауза −70, речь −25; игра гудит ровно — разброс 10–15 дБ).
VOICE_SPREAD_DB = 25.0


def voice_spread(pcm: bytes, rate: int = 16000) -> float:
    """Разброс громкости дорожки (90-й минус 15-й перцентиль кадров по 20 мс, дБ)."""
    from app.subtitles.phantoms import frame_levels

    levels = sorted(frame_levels(pcm, rate))
    if len(levels) < 10:
        return 0.0
    return levels[int(len(levels) * 0.9)] - levels[int(len(levels) * 0.15)]


def onset_pcm(source_path: Path, pieces: list[tuple[float, float]], source: dict, speech_pcm: bytes) -> bytes:
    """Звук, по которому слова прилипают к началам звука (app/speech_onsets.py): дорожка речи.

    ``speech_tracks`` заданы — тот же звук, что распознавался. Не заданы, а смесь рендера из
    нескольких дорожек (запись NVIDIA: 0 — игра, 1 — микрофон) — распознавание идёт по смеси,
    и в ней звучит всё время (игра): начало слова, поставленное распознаванием на 0.3 с раньше,
    попадало в «звук» и не сдвигалось. Тогда берём дорожку, похожую на голос (паузы и речь —
    разброс громкости ≥ VOICE_SPREAD_DB); такой нет — смесь, как раньше."""
    from app.render import _run_ffmpeg  # local import: render imports this module
    from app.source_audio import separate_speech, source_mix
    from app.speech_onsets import wav_pcm

    mix = source_mix(source) or []
    if separate_speech(source) or len(mix) < 2:
        return speech_pcm
    best, best_spread = speech_pcm, VOICE_SPREAD_DB
    for item in mix:
        path = settings.tmp_dir / f"{uuid4().hex}.wav"
        try:
            _run_ffmpeg(build_clip_audio_args(source_path, pieces, path, [{"track": item["track"], "gain_db": 0.0}]),
                        timeout=60 * 5)
            pcm = wav_pcm(path)
        except Exception:  # noqa: BLE001 - нет дорожки — остаёмся на смеси
            continue
        finally:
            path.unlink(missing_ok=True)
        spread = voice_spread(pcm)
        if spread >= best_spread:
            best, best_spread = pcm, spread
    return best


def generate_for_plan(store: Any, clip_plan_id: int, provider: str | None = None, model: str | None = None) -> dict:
    """Cut the clip's audio, transcribe it, store editable lines on the plan."""
    from app.render import _run_ffmpeg  # local import: render imports this module

    plan = store.get_clip_plan(clip_plan_id)
    segments = plan.get("segments") or []
    if not segments:
        raise ValueError("у клипа нет кусков")
    source = store.get_source(int(plan["source_id"]))
    source_path = Path(str(source.get("local_path") or ""))
    if not source_path.exists():
        raise ValueError("исходник недоступен")

    saved = store.get_clip_plan_subtitles(clip_plan_id) or {}
    settings_profile = (plan.get("render_settings") or {}).get("sub_id")
    profile = store.get_subtitle_profile(settings_profile) if settings_profile else _default_profile(store)
    if provider:
        profile = {**profile, "provider": provider}
    used_model = model or subtitle_model_for_profile(profile)
    engine = get_subtitle_provider(profile.get("provider"))

    plain_pieces = [(float(s["start_sec"]), float(s["end_sec"])) for s in segments]
    # J/L: распознаём кусок вместе с его «ручкой» (звук исходника до начала / после конца,
    # который слышен у стыка) — субтитры покажут и эти слова (app/audio_edit.heard_subtitles).
    render = plan.get("render_settings") or {}
    from app.audio_edit import handle_extents, normalize_audio, split_handle_words

    joins = normalize_audio(render.get("audio")).get("joins")
    extents = handle_extents(segments, joins, render.get("transition")) if joins else []
    limit = float(source.get("duration_sec") or 0)
    pieces = [(max(0.0, a - head), min(limit, b + tail) if limit > b else b)
              for (a, b), (head, tail) in zip(plain_pieces, extents)] if any(any(e) for e in extents) else plain_pieces
    audio_path = settings.tmp_dir / f"{uuid4().hex}.wav"
    try:
        # Распознаём по дорожке речи (микрофон без игры — меньше выдумок), не по смеси рендера.
        _run_ffmpeg(build_clip_audio_args(source_path, pieces, audio_path, speech_mix(source)), timeout=60 * 10)
        try:
            result = engine.transcribe(audio_path, profile, used_model)
        except RuntimeError:
            if str(profile.get("provider") or "").lower() == "whisper":
                raise
            # Облачный движок недоступен (кончились кредиты Gemini, нет ключа) —
            # распознаём локальным whisper, он есть в контейнере всегда.
            profile = {**profile, "provider": "whisper"}
            used_model = subtitle_model_for_profile(profile)
            result = get_subtitle_provider("whisper").transcribe(audio_path, profile, used_model)
        from app.speech_onsets import snap_payload, wav_pcm
        from app.subtitles.phantoms import drop_phantoms

        pcm = wav_pcm(audio_path)
        # «Субтитры сделал DimaTorzok», «Продолжение следует…» на тишине дорожки речи — прочь.
        result, phantoms = drop_phantoms(result, pcm)
        total = sum(e - s for s, e in pieces)
        result = normalize_subtitle_timeline(result, total)
        payload = payload_from_result(result, str(profile.get("provider") or ""), used_model)
        payload["duration"] = round(total, 3)
        if phantoms:
            payload["dropped_phantoms"] = phantoms
        # Начала слов — по настоящему звуку дорожки речи, а не по распознаванию (оно спешит
        # или опаздывает). Онсеты — по микрофону, даже если распознавание шло по смеси.
        payload["snapped_words"] = snap_payload(payload, onset_pcm(source_path, pieces, source, pcm))
        if pieces != plain_pieces:
            payload = split_handle_words(payload, pieces, segments)
        # Распознано без скорости кусков: штамп — куски как в исходнике; чтение субтитров
        # растянет слова кусков со скоростью (штамп текущих кусков — со speed).
        payload["pieces"] = [[round(a, 3), round(b, 3)] for a, b in plain_pieces]
        if saved.get("lines"):
            payload["previous_lines"] = len(saved["lines"])
        store.set_clip_plan_subtitles(clip_plan_id, payload)
        return payload
    finally:
        audio_path.unlink(missing_ok=True)


def save_lines(store: Any, clip_plan_id: int, lines: Any, words: Any = None) -> dict:
    """Store the owner's edited lines (word timing follows the text).

    ``words`` — пословные тайминги, распознанные снаружи (агент или распознавание на
    хосте): тогда караоке и анимированные субтитры идут по ним, а не по словам,
    равномерно разложенным по строке.
    """
    current = store.get_clip_plan_subtitles(clip_plan_id) or {}
    if isinstance(words, list) and words:
        clean_words = []
        for w in words:
            try:
                word = {"word": str(w["word"])[:80], "start": round(float(w["start"]), 3), "end": round(float(w["end"]), 3)}
            except (KeyError, TypeError, ValueError):
                continue
            if w.get("speaker"):
                word["speaker"] = str(w["speaker"])[:1]  # голос (A/B/C/D) — не терять при правке
            if level_of(w.get("emph")):
                word["emph"] = level_of(w.get("emph"))  # эмфаза слова (app/word_emphasis.py)
            clean_words.append(word)
        current = {**current, "words": clean_words}
        if not current.get("duration"):
            plan = store.get_clip_plan(clip_plan_id)
            from app.piece_speed import seg_length

            current["duration"] = round(sum(seg_length(seg) for seg in plan.get("segments") or []), 3)
    clean = normalize_lines(lines, current.get("duration"))
    if not clean:
        store.set_clip_plan_subtitles(clip_plan_id, None)
        return {"lines": [], "words": [], "edited": True}
    rebuilt, blind = _rebuild_words(clean, current.get("words") or [])
    rebuilt, notes = fit_blind_words(store, clip_plan_id, rebuilt, blind)
    payload = {
        **current,
        "lines": clean,
        "words": rebuilt,
        "edited": True,
    }
    store.set_clip_plan_subtitles(clip_plan_id, payload)
    return {**payload, "align_warnings": notes} if notes else payload


def _default_profile(store: Any) -> dict:
    saved = store.get_app_setting_value("default_subtitle_profile_id")
    try:
        if saved:
            return store.get_subtitle_profile(int(saved))
    except (KeyError, TypeError, ValueError):
        pass
    profiles = store.list_subtitle_profiles()
    if not profiles:
        raise ValueError("нет ни одного стиля субтитров")
    return profiles[0]


MAX_LISTEN_SEC = 30.0


def listen_range(store: Any, clip_plan_id: int, from_sec: float | None, to_sec: float | None,
                 before_cuts: bool = False) -> dict:
    """Что реально звучит в готовом клипе между from_sec и to_sec (с учётом кусков,
    вырезов и наплывов переходов): распознаём именно этот звук, а не верим субтитрам.

    Агент-монтажёр проверял так финалы вручную (рендер → распознать хвост mp4):
    договорена ли фраза, сколько тишины после последнего слова, не торчит ли
    слово из выреза. Здесь — без рендера, по исходнику.

    Время — шкала ``final`` (готовый ролик, app/timebase.py); ``before_cuts`` — шкала
    ``clip`` (до вырезов, как слои и субтитры). Раньше части клеились встык, без наплывов,
    и слово выходило позже, чем в ролике (15.62 против 15.23). Распознаётся дорожка речи
    (``speech_mix``), громкость ``level`` — по смеси рендера."""
    from app.render import _run_ffmpeg  # local import: render imports this module
    from app.source_audio import separate_speech
    from app.subtitles.registry import QUALITY_WHISPER_MODEL
    from app.timebase import ClipClock, scale_note

    plan = store.get_clip_plan(clip_plan_id)
    source = store.get_source(int(plan["source_id"]))
    source_path = Path(str(source.get("local_path") or ""))
    if not source_path.exists():
        raise ValueError("исходник недоступен")
    clock = ClipClock.for_plan(plan)
    scale = "clip" if before_cuts else "final"
    total = clock.clip_duration if before_cuts else clock.final_duration
    # Без границ — последние 6 с (проверка финала).
    hi = min(total, float(to_sec)) if to_sec is not None else total
    lo = max(0.0, float(from_sec)) if from_sec is not None else max(0.0, hi - 6.0)
    if to_sec is None and from_sec is not None:
        hi = min(total, lo + 6.0)
    if hi - lo < 0.3:
        raise ValueError(f"пустой отрезок: клип длится {total:.2f} с ({scale_note(scale)})")
    if hi - lo > MAX_LISTEN_SEC:
        hi = lo + MAX_LISTEN_SEC
    lo_f, hi_f = (clock.clip_to_final(lo), clock.clip_to_final(hi)) if before_cuts else (lo, hi)
    # Части подряд (после вырезов, без наплывов) — так режется звук.
    p_lo, p_hi = clock.final_to_parts(lo_f), clock.final_to_parts(hi_f)
    pieces, offset = [], 0.0
    for part in clock.parts():
        start, end = float(part["start_sec"]), float(part["end_sec"])
        a, b = max(p_lo, offset), min(p_hi, offset + end - start)
        if b > a:
            pieces.append((start + a - offset, start + b - offset))
        offset += end - start
    if not pieces:
        raise ValueError(f"пустой отрезок: клип длится {total:.2f} с ({scale_note(scale)})")
    try:
        profile = {**_default_profile(store), "provider": "whisper"}
    except ValueError:  # стилей нет — распознаванию стиль и не нужен
        profile = {"provider": "whisper"}
    audio_path = settings.tmp_dir / f"{uuid4().hex}.wav"
    mix_path = settings.tmp_dir / f"{uuid4().hex}.wav"
    # Тишина рендера (render.mute: шум, мат censor_profanity) молчит и здесь: раньше listen
    # слышал заглушённое, и проверить им глушение мата было нельзя.
    muted = mute_in_parts(clock, (plan.get("render_settings") or {}).get("mute"), p_lo, p_hi)
    # Тишина/эффекты одной дорожки (track) и эффекты на отрезке — в самом звуке, как в рендере.
    from app.audio_edit import plan_source_ops

    ops = plan_source_ops(plan, source)
    try:
        _run_ffmpeg(build_clip_audio_args(source_path, pieces, audio_path, speech_mix(source), ops), timeout=60 * 5)
        silence_wav(audio_path, muted)
        from app.speech_onsets import snap_words, voiced_frames, wav_pcm

        pcm = wav_pcm(audio_path)
        level_pcm = pcm
        if separate_speech(source):
            # Громкость — того, что услышит зритель (смесь рендера), а не одного микрофона.
            _run_ffmpeg(build_clip_audio_args(source_path, pieces, mix_path, source_mix(source), ops), timeout=60 * 5)
            silence_wav(mix_path, muted)
            level_pcm = wav_pcm(mix_path)
        try:
            from app.subtitles.phantoms import drop_phantoms

            result = get_subtitle_provider("whisper").transcribe(audio_path, profile, QUALITY_WHISPER_MODEL)
            result, _phantoms = drop_phantoms(result, pcm)  # «Продолжение следует…» на тишине — не речь
            raw_words = [w.to_dict() for w in result.words]
        except RuntimeError as exc:
            # Игра, музыка, тишина — Whisper не нашёл ни слова: это ответ «речи нет», а не сбой.
            if "no word-level timestamps" not in str(exc):
                raise
            raw_words = []
        # Те же начала слов по звуку, что у generate_subtitles — иначе времена расходятся.
        heard, _moved = (snap_words(raw_words, voiced_frames(onset_pcm(source_path, pieces, source, pcm)))
                         if raw_words else ([], 0))
    finally:
        audio_path.unlink(missing_ok=True)
        mix_path.unlink(missing_ok=True)

    def back(t: float) -> float:
        final = clock.parts_to_final(p_lo + t)
        return round(clock.final_to_clip(final) if before_cuts else final, 2)

    words = [[w["word"], back(float(w["start"])), back(float(w["end"]))] for w in heard]
    note = scale_note(scale) + ("; без before_cuts — шкала final (готовый ролик)" if before_cuts else
                                 "; before_cuts=true — шкала clip (как слои и субтитры get_clip)")
    if not words:
        note = "речи нет — громкость отрезка в level (RMS дБ); " + note
    if muted:
        note += f"; заглушённое mute ({len(muted)} отр.) молчит, как в рендере"
    return {
        "time": scale,
        "from": round(lo, 2),
        "to": round(hi, 2),
        "clip_duration": round(total, 2),
        "finished_duration": clock.final_duration,
        "text": " ".join(w[0] for w in words).strip(),
        "words": words,
        "silence_after_last_word": round(hi - words[-1][2], 2) if words else round(hi - lo, 2),
        "level": _pcm_level(level_pcm),
        "note": note,
    }


def mute_in_parts(clock: Any, mute: Any, p_lo: float, p_hi: float) -> list[tuple]:
    """Отрезки ``render.mute`` (шкала clip) → секунды звука listen (части подряд от ``p_lo``).

    listen режет звук из исходника частями подряд (после вырезов, без наплывов); тишина
    рендера — на шкале clip. Перевод: clip → куски подряд → части (вырезы) → от начала отрезка.
    Мат со стилем ``duck`` — ``(a, b, громкость)``; со стилем-эффектом — уже в звуке (ops)."""
    from app.audio_mute import final_pass_mutes, span_floor

    out = []
    for m in final_pass_mutes(mute):
        a, b = clock.clip_range_to_plain(m["start"], m["end"])
        if clock.cut_plan is not None:
            a, b = clock.cut_plan.mapper.clamp(a), clock.cut_plan.mapper.clamp(b)
        a, b = max(a, p_lo), min(b, p_hi)
        if b - a > 0.005:
            floor = span_floor(m)
            out.append((round(a - p_lo, 3), round(b - p_lo, 3), floor) if floor > 0 else
                       (round(a - p_lo, 3), round(b - p_lo, 3)))
    return out


def silence_wav(path: Path, spans: list[tuple]) -> None:
    """Обнулить звук wav (16-бит PCM) на отрезках ``spans`` (секунды от начала файла);
    ``(a, b, k)`` — не обнулить, а умножить на ``k`` (мат со стилем duck)."""
    import wave

    if not spans:
        return
    with wave.open(str(path), "rb") as src:
        params = src.getparams()
        frames = bytearray(src.readframes(src.getnframes()))
    step = params.sampwidth * params.nchannels
    total = len(frames) // step
    for span in spans:
        a, b = span[0], span[1]
        i0 = max(0, min(total, int(round(a * params.framerate))))
        i1 = max(0, min(total, int(round(b * params.framerate))))
        if len(span) > 2 and float(span[2]) > 0 and params.sampwidth == 2:
            import array

            chunk = array.array("h", bytes(frames[i0 * step:i1 * step]))
            k = float(span[2])
            chunk = array.array("h", (int(v * k) for v in chunk))
            frames[i0 * step:i1 * step] = chunk.tobytes()
        else:
            frames[i0 * step:i1 * step] = bytes((i1 - i0) * step)
    with wave.open(str(path), "wb") as dst:
        dst.setparams(params)
        dst.writeframes(bytes(frames))


def _pcm_level(pcm: bytes) -> dict:
    """Громкость отрезка: средняя (RMS) и самая громкая десятая секунды, дБ полной шкалы."""
    from app.source_loudness import SILENCE_DB, pcm_levels

    overall = pcm_levels(pcm, 16000, max(0.1, len(pcm) / 2 / 16000)) if pcm else []
    windows = pcm_levels(pcm, 16000, 0.1) if pcm else []
    return {"rms_db": overall[0] if overall else SILENCE_DB, "max_db": max(windows) if windows else SILENCE_DB}


def find_noises(store: Any, clip_plan_id: int, threshold_db: float | None = None, min_len: float = 0.2) -> dict:
    """Громкие места звука клипа, где никто не говорит: вшитые эффекты, музыка, шум, выстрелы.

    Агент-монтажёр искал такие «пердежи» старого монтажа вручную спектрограммами —
    распознавание их не видит. Время — шкала ``clip`` (до вырезов, с наплывами, как у
    слоёв: отрезок сразу годится в mute_audio). Окна по 0.1 с сливаются в отрезки
    (разрыв до 0.3 с — тот же звук): раньше ответ был сотнями кусков по 0.1 с. Порог по
    умолчанию — медиана клипа + 6 дБ (громче фона ЭТОГО клипа без речи: у игры фон
    громкий, у разговорного — тишина пауз). Речь — по словам субтитров (±0.08 с);
    субтитров нет, а у источника своя дорожка речи — по голосу на ней; иначе ищем без
    вычитания речи (игра без голоса)."""
    from app.render import _run_ffmpeg  # local import: render imports this module
    from app.source_audio import separate_speech
    from app.source_loudness import LOUD_OVER_MEDIAN_DB, loud_spans, median, pcm_levels
    from app.timebase import ClipClock, scale_note

    plan = store.get_clip_plan(clip_plan_id)
    source = store.get_source(int(plan["source_id"]))
    source_path = Path(str(source.get("local_path") or ""))
    if not source_path.exists():
        raise ValueError("исходник недоступен")
    pieces = [(float(s["start_sec"]), float(s["end_sec"])) for s in plan.get("segments") or []]
    if not pieces:
        raise ValueError("у клипа нет кусков")
    words = (store.get_clip_plan_subtitles(clip_plan_id) or {}).get("words") or []
    audio_path = settings.tmp_dir / f"{uuid4().hex}.wav"
    voice_path = settings.tmp_dir / f"{uuid4().hex}.wav"
    voiced: list[bool] = []
    from app.audio_edit import plan_source_ops

    ops = plan_source_ops(plan, source)
    try:
        _run_ffmpeg(build_clip_audio_args(source_path, pieces, audio_path, source_mix(source), ops), timeout=60 * 5)
        from app.speech_onsets import FRAME_SEC, voiced_frames, wav_pcm

        raw = wav_pcm(audio_path)
        if not words and separate_speech(source):
            _run_ffmpeg(build_clip_audio_args(source_path, pieces, voice_path, speech_mix(source)), timeout=60 * 5)
            voiced = voiced_frames(wav_pcm(voice_path))
    finally:
        audio_path.unlink(missing_ok=True)
        voice_path.unlink(missing_ok=True)
    step = 0.1
    levels = pcm_levels(raw, 16000, step)

    def talking(a: float, b: float) -> bool:
        mid = (a + b) / 2
        if words:
            return any(float(w["start"]) - 0.08 <= mid <= float(w["end"]) + 0.08 for w in words)
        k = int(mid / FRAME_SEC)
        return any(voiced[j] for j in range(max(0, k - 4), min(len(voiced), k + 5)))

    by_speech = bool(words) or any(voiced)
    # Фон — медиана окон без речи: у разговорного клипа это тишина пауз, у игры — её звук.
    background = [db for i, db in enumerate(levels) if not (by_speech and talking(i * step, (i + 1) * step))]
    med = median(background or levels)
    threshold = float(threshold_db) if threshold_db is not None else med + LOUD_OVER_MEDIAN_DB

    found = loud_spans(levels, step, threshold, gap=0.3, min_len=min_len, skip=talking if by_speech else None)
    # Шкала clip: отрезок через стык кусков с наплывом — двумя частями (piece — чей).
    clock = ClipClock(plan.get("segments") or [], (plan.get("render_settings") or {}).get("transition"))
    noises = []
    for item in found:
        for part in clock.plain_range_to_clip(item["start"], item["end"]):
            noises.append({**item, "start": round(part["start"], 2), "end": round(part["end"], 2),
                           **({"piece": part["piece"]} if len(pieces) > 1 else {})})
    out = {
        "time": "clip",
        "noises": noises[:80],
        "threshold_db": round(threshold, 1),
        "median_db": round(med, 1),
        "speech_excluded": by_speech,
        "note": (f"громкий звук без речи ({scale_note('clip')}) — отрезки годятся прямо в mute_audio и "
                 "set_source_gain. Если это вшитый эффект/шум в паузе — вырежи apply_cuts или заглуши "
                 "mute_audio; если он поверх слова — не трогай, проверь listen"),
    }
    if not by_speech:
        out["note"] += ("; субтитров нет — речь не вычтена: это просто громкие места клипа "
                        "(в игре — выстрелы, взрывы, реакции)")
    elif not words:
        out["note"] += "; речь вычтена по голосу на дорожке речи (speech_tracks)"
    if len(noises) > 80:
        out["note"] += f"; показаны первые 80 из {len(noises)} — подними threshold_db"
    return out
