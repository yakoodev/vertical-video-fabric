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

MAX_LINES = 400


def build_clip_audio_args(source_path: Path, pieces: list[tuple[float, float]], out_path: Path) -> list[str]:
    """One wav with the clip's pieces played back to back — the audio the render burns onto.

    Каждый кусок — свой вход с быстрым поиском (``-ss``/``-t`` до ``-i``): ffmpeg читает
    только нужные секунды. Раньше был один вход и ``atrim``: ffmpeg декодировал весь
    4-часовой исходник с начала до конца — 11 ГБ через кэш WSL на каждый клип, и
    Docker Desktop падал от нехватки памяти.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    inputs: list[str] = []
    for start, end in pieces:
        inputs += ["-ss", f"{start:.3f}", "-t", f"{max(0.01, end - start):.3f}", "-i", str(source_path)]
    if len(pieces) > 1:
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


def rebuild_words(lines: list[dict], old_words: list[dict]) -> list[dict]:
    """Keep the original per-word timing where the text is untouched; re-spread it
    where the owner rewrote the line (so karaoke never breaks on an edit)."""
    words: list[dict] = []
    for line in lines:
        start, end = float(line["start"]), float(line["end"])
        inside = _words_of_line(old_words, start, end)
        if inside and _same_text(" ".join(w["word"] for w in inside), line["text"]):
            words.extend(
                {"word": w["word"], "start": max(start, float(w["start"])), "end": min(end, float(w["end"])),
                 **({"speaker": w["speaker"]} if w.get("speaker") else {})}
                for w in inside
            )
        else:
            words.extend(_split_words(line["text"], start, end))
    return words


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
    lines = [{"start": round(s.start, 3), "end": round(s.end, 3), "text": s.text.strip()} for s in result.segments]
    lines = normalize_lines(lines, result.duration)
    words = [{"word": w.word, "start": round(w.start, 3), "end": round(w.end, 3)} for w in result.words]
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
        words=[SubtitleWord(word=w["word"], start=float(w["start"]), end=float(w["end"])) for w in words],
    )


def shift_for_transition(
    payload: dict, pieces: list[tuple[float, float]], overlap: float | list[float]
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
    for start, end in pieces:
        t += end - start
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


def _layout(pieces: list) -> list[tuple[float, float, float]]:
    """[(src_start, src_end, clip_offset)] — где каждый кусок лежит в склейке."""
    out, offset = [], 0.0
    for start, end in pieces:
        start, end = float(start), float(end)
        if end > start:
            out.append((start, end, offset))
            offset += end - start
    return out


def _old_index(layout: list, t: float) -> int:
    for index, (start, end, offset) in enumerate(layout):
        if t < offset + (end - start):
            return index
    return len(layout) - 1


def _to_source(layout: list, t: float, index: int | None = None) -> float:
    start, _end, offset = layout[_old_index(layout, t) if index is None else index]
    return start + max(0.0, t - offset)


def _piece_at(layout: list, x: float, prefer: int) -> tuple[float, float, float] | None:
    """Новый кусок, где лежит момент исходника ``x``; сначала тот же по номеру
    (куски могут перекрываться — слово остаётся в своём)."""
    inside = lambda p: p[0] - 0.001 <= x < p[1] + 0.001  # noqa: E731
    if prefer < len(layout) and inside(layout[prefer]):
        return layout[prefer]
    return next((p for p in layout if inside(p)), None)


def _to_clip(piece: tuple[float, float, float], x: float) -> float:
    start, end, offset = piece
    return round(offset + min(max(x, start), end) - start, 3)


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
    for line in payload.get("lines") or []:
        inside = _words_of_line(old_words, float(line["start"]), float(line["end"]))
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
    total = round(sum(e - s for s, e, _o in new), 3)
    return {
        **payload,
        "words": sorted(words, key=lambda w: w["start"]),
        "lines": normalize_lines(lines, total),
        "duration": total,
        "pieces": [[s, e] for s, e, _o in new],
    }


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

    pieces = [(float(s["start_sec"]), float(s["end_sec"])) for s in segments]
    audio_path = settings.tmp_dir / f"{uuid4().hex}.wav"
    try:
        _run_ffmpeg(build_clip_audio_args(source_path, pieces, audio_path), timeout=60 * 10)
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
        total = sum(e - s for s, e in pieces)
        result = normalize_subtitle_timeline(result, total)
        payload = payload_from_result(result, str(profile.get("provider") or ""), used_model)
        payload["duration"] = round(total, 3)
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
                clean_words.append({"word": str(w["word"])[:80], "start": round(float(w["start"]), 3), "end": round(float(w["end"]), 3)})
            except (KeyError, TypeError, ValueError):
                continue
        current = {**current, "words": clean_words}
        if not current.get("duration"):
            plan = store.get_clip_plan(clip_plan_id)
            current["duration"] = round(
                sum(float(seg["end_sec"]) - float(seg["start_sec"]) for seg in plan.get("segments") or []), 3
            )
    clean = normalize_lines(lines, current.get("duration"))
    if not clean:
        store.set_clip_plan_subtitles(clip_plan_id, None)
        return {"lines": [], "words": [], "edited": True}
    payload = {
        **current,
        "lines": clean,
        "words": rebuild_words(clean, current.get("words") or []),
        "edited": True,
    }
    store.set_clip_plan_subtitles(clip_plan_id, payload)
    return payload


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


def listen_range(store: Any, clip_plan_id: int, from_sec: float | None, to_sec: float | None) -> dict:
    """Что реально звучит в готовом клипе между from_sec и to_sec (с учётом кусков
    и вырезов): распознаём именно этот звук, а не верим субтитрам.

    Агент-монтажёр проверял так финалы вручную (рендер → распознать хвост mp4):
    договорена ли фраза, сколько тишины после последнего слова, не торчит ли
    слово из выреза. Здесь — без рендера, по исходнику."""
    from app.cuts import plan_cuts_for_plan
    from app.render import _run_ffmpeg  # local import: render imports this module
    from app.subtitles.registry import QUALITY_WHISPER_MODEL

    plan = store.get_clip_plan(clip_plan_id)
    source = store.get_source(int(plan["source_id"]))
    source_path = Path(str(source.get("local_path") or ""))
    if not source_path.exists():
        raise ValueError("исходник недоступен")
    cut_plan = plan_cuts_for_plan(plan)
    parts = cut_plan.parts if cut_plan else (plan.get("segments") or [])
    total = sum(float(p["end_sec"]) - float(p["start_sec"]) for p in parts)
    # Без границ — последние 6 с (проверка финала).
    hi = min(total, float(to_sec)) if to_sec is not None else total
    lo = max(0.0, float(from_sec)) if from_sec is not None else max(0.0, hi - 6.0)
    if to_sec is None and from_sec is not None:
        hi = min(total, lo + 6.0)
    if hi - lo < 0.3:
        raise ValueError(f"пустой отрезок: клип длится {total:.2f} с")
    if hi - lo > MAX_LISTEN_SEC:
        hi = lo + MAX_LISTEN_SEC
    pieces, offset = [], 0.0
    for part in parts:
        start, end = float(part["start_sec"]), float(part["end_sec"])
        a, b = max(lo, offset), min(hi, offset + end - start)
        if b > a:
            pieces.append((start + a - offset, start + b - offset))
        offset += end - start
    try:
        profile = {**_default_profile(store), "provider": "whisper"}
    except ValueError:  # стилей нет — распознаванию стиль и не нужен
        profile = {"provider": "whisper"}
    audio_path = settings.tmp_dir / f"{uuid4().hex}.wav"
    try:
        _run_ffmpeg(build_clip_audio_args(source_path, pieces, audio_path), timeout=60 * 5)
        result = get_subtitle_provider("whisper").transcribe(audio_path, profile, QUALITY_WHISPER_MODEL)
    finally:
        audio_path.unlink(missing_ok=True)
    words = [[w.word, round(lo + w.start, 2), round(lo + w.end, 2)] for w in result.words]
    return {
        "from": round(lo, 2),
        "to": round(hi, 2),
        "clip_duration": round(total, 2),
        "text": " ".join(w[0] for w in words).strip(),
        "words": words,
        "silence_after_last_word": round(hi - words[-1][2], 2) if words else round(hi - lo, 2),
        "note": "секунды готового клипа (после вырезов); переходы с наплывом сдвигают на доли секунды",
    }


def find_noises(store: Any, clip_plan_id: int, threshold_db: float = -32.0, min_len: float = 0.12) -> dict:
    """Громкие места звука клипа, где никто не говорит: вшитые эффекты, музыка, шум.

    Агент-монтажёр искал такие «пердежи» старого монтажа вручную спектрограммами —
    распознавание их не видит. Время — клип до вырезов (как у субтитров и вырезов),
    кусками по 0.1 с; речь — по словам субтитров клипа (±0.08 с)."""
    import struct

    from app.render import _run_ffmpeg  # local import: render imports this module

    plan = store.get_clip_plan(clip_plan_id)
    source = store.get_source(int(plan["source_id"]))
    source_path = Path(str(source.get("local_path") or ""))
    if not source_path.exists():
        raise ValueError("исходник недоступен")
    pieces = [(float(s["start_sec"]), float(s["end_sec"])) for s in plan.get("segments") or []]
    if not pieces:
        raise ValueError("у клипа нет кусков")
    words = (store.get_clip_plan_subtitles(clip_plan_id) or {}).get("words") or []
    if not words:
        raise ValueError("сначала generate_subtitles — без слов не отличить речь от шума")
    audio_path = settings.tmp_dir / f"{uuid4().hex}.wav"
    try:
        _run_ffmpeg(build_clip_audio_args(source_path, pieces, audio_path), timeout=60 * 5)
        raw = audio_path.read_bytes()[44:]
    finally:
        audio_path.unlink(missing_ok=True)
    count = len(raw) // 2
    samples = struct.unpack(f"<{count}h", raw[: count * 2])
    win = 1600  # 0.1 с при 16 кГц
    spans: list[tuple[float, float, float]] = []
    for i in range(0, count - win, win):
        chunk = samples[i: i + win]
        rms = (sum(v * v for v in chunk) / win) ** 0.5 / 32768
        db = 20 * __import__("math").log10(rms + 1e-9)
        t = i / 16000
        talking = any(float(w["start"]) - 0.08 <= t + 0.05 <= float(w["end"]) + 0.08 for w in words)
        if db > threshold_db and not talking:
            if spans and abs(spans[-1][1] - t) < 1e-6:
                spans[-1] = (spans[-1][0], t + 0.1, max(spans[-1][2], db))
            else:
                spans.append((t, t + 0.1, db))
    found = [{"start": round(a, 2), "end": round(b, 2), "loudest_db": round(d, 1)}
             for a, b, d in spans if b - a >= min_len - 1e-6]
    return {
        "noises": found,
        "note": ("громкий звук без речи (секунды клипа до вырезов). Если это вшитый эффект/шум в паузе — "
                 "вырежи apply_cuts; если он поверх слова — не трогай, проверь listen"),
    }
