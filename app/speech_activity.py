"""Где в звуке клипа на самом деле говорят — и совпадают ли с этим субтитры.

Владелец (клипы 905–918): «субтитры иногда рисуются раньше, чем начали говорить». Причины две:
ручной текст раскидывался по строке равномерно, а прилипание к звуку (app/speech_onsets.py) меряет
громкость и на стриме слышит игру как речь. Здесь голос ищет Silero VAD (``audio_map.vad_segments``,
он же у faster-whisper): игра, музыка и шум ему почти не мешают. Шкала — «куски подряд», как у слов.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from app.settings import settings

RATE = 16000
EARLY_SEC = 0.25  # строка раньше голоса — глаз видит текст до звука
LATE_SEC = 0.5  # после голоса — позже этого заметно
LOST_SPEECH_SEC = 2.5  # речи без субтитров перед строкой больше — не опоздание, а потерянная фраза
KNOWN_MOVE_SEC = 0.5  # слово с тем же текстом уехало от прежнего времени дальше — это раскладка наугад, а не поправка
_CACHE_MAX = 8
_cache: dict[tuple, list[tuple[float, float]]] = {}


def plan_pcm(store: Any, clip_plan_id: int) -> Any:
    """Звук клипа (дорожка речи, куски подряд) — float32 16 кГц. Нет исходника — ValueError."""
    import numpy as np

    from app.clip_subtitles import build_clip_audio_args
    from app.render import _run_ffmpeg
    from app.source_audio import speech_mix
    from app.speech_onsets import wav_pcm

    plan = store.get_clip_plan(clip_plan_id)
    source = store.get_source(int(plan["source_id"]))
    path = Path(str(source.get("local_path") or ""))
    if not path.is_file():
        raise ValueError("исходник недоступен")
    pieces = [(float(s["start_sec"]), float(s["end_sec"])) for s in plan.get("segments") or []]
    if not pieces:
        raise ValueError("у клипа нет кусков")
    out = settings.tmp_dir / f"speech-{uuid4().hex}.wav"
    try:
        from app.canvas_audio import clip_audio_input

        # Холст: речь — в слоях (озвучка), не в основе (app/canvas_audio.py).
        with clip_audio_input(store, plan, source, "speech") as (speech_path, speech_tracks, _canvas):
            _run_ffmpeg(build_clip_audio_args(speech_path, pieces, out, speech_tracks), timeout=600)
        return np.frombuffer(wav_pcm(out), dtype="<i2").astype(np.float32) / 32768.0
    finally:
        out.unlink(missing_ok=True)


def plan_speech_spans(store: Any, clip_plan_id: int) -> list[tuple[float, float]]:
    """Отрезки речи клипа. Кэш по кускам и дорожкам: правки текста звук не меняют."""
    from app.audio_map import vad_segments
    from app.source_audio import speech_mix

    plan = store.get_clip_plan(clip_plan_id)
    source = store.get_source(int(plan["source_id"]))
    key = (int(plan["source_id"]), str(source.get("local_path")), str(speech_mix(source)),
           tuple((float(s["start_sec"]), float(s["end_sec"])) for s in plan.get("segments") or []))
    from app.canvas import is_canvas

    if is_canvas(source):
        from app.canvas_audio import signature

        key = (*key, "canvas", int(plan["id"]), signature(store, plan))
    if key in _cache:
        return _cache[key]
    spans = vad_segments(plan_pcm(store, clip_plan_id), RATE)[0]
    if len(_cache) >= _CACHE_MAX:
        _cache.pop(next(iter(_cache)))
    _cache[key] = spans
    return spans


def spans_within(spans: list[tuple[float, float]], a: float, b: float) -> list[tuple[float, float]]:
    return [(max(s, a), min(e, b)) for s, e in spans if e > a and s < b]


def speech_seconds(spans: list[tuple[float, float]], a: float, b: float) -> float:
    return sum(e - s for s, e in spans_within(spans, a, b))


def spread_over_speech(tokens: list[dict], parts: list[tuple[float, float]]) -> list[dict]:
    """Слова по отрезкам речи пропорционально длине: то, что нельзя привязать к распознанному слову."""
    total = sum(e - s for s, e in parts)
    weights = [len(str(t["word"])) + 1 for t in tokens]
    scale = total / (sum(weights) or 1)

    def at(pos: float) -> float:
        for s, e in parts:
            if pos <= e - s + 1e-9:
                return s + pos
            pos -= e - s
        return parts[-1][1]

    out, pos = [], 0.0
    for tok, weight in zip(tokens, weights):
        share = weight * scale
        start, end = at(pos), at(pos + share * 0.95)
        out.append({**tok, "start": round(start, 3), "end": round(max(end, start + 0.04), 3)})
        pos += share
    return out


def _covered(start: float, end: float, spans: list[tuple[float, float]]) -> float:
    return speech_seconds(spans, start, end) / max(0.01, end - start)


def fit_unknown_words(words: list[dict], old_words: list[dict], spans_fn: Any) -> tuple[list[dict], list[str]]:
    """Текст, которого нет в распознанном, — к настоящей речи. → (слова, предупреждения).

    Слово, совпавшее с распознанным рядом по времени, остаётся как задано, если сдвиг мал: время могли поправить
    нарочно. Сдвиг больше ``KNOWN_MOVE_SEC`` — слово возвращается на прежнее время (клип 930: «не особо» агент разложил
    равномерно на 0.6 с раньше голоса, и правка прошла как «нарочно»); нужно именно так — ``align: false``.
    Остальные идут цепочками: столько же распознанных слов на этом месте — берём их время
    (модель ослышалась); иначе цепочка уже стоит на голосе — не трогаем, а начало в тишине
    или половина мимо речи — раскладываем по речи между соседями. Нет звука или речи — честный warning.
    """
    import difflib

    from app.ai.speakers import _norm

    ordered = sorted((dict(w) for w in words), key=lambda w: float(w["start"]))
    old = sorted((w for w in old_words if str(w.get("word") or "").strip()), key=lambda w: float(w["start"]))
    known = [False] * len(ordered)
    used: set[int] = set()
    sm = difflib.SequenceMatcher(a=[_norm(str(w["word"])) for w in ordered], b=[_norm(str(w["word"])) for w in old], autojunk=False)
    returned = 0
    for block in sm.get_matching_blocks():
        for k in range(block.size):
            mine, theirs = ordered[block.a + k], old[block.b + k]
            gap = abs(float(mine["start"]) - float(theirs["start"]))
            if gap <= 1.5:
                known[block.a + k] = True
                used.add(block.b + k)
                if gap > KNOWN_MOVE_SEC:
                    ordered[block.a + k] = {**mine, "start": float(theirs["start"]), "end": float(theirs["end"])}
                    returned += 1
    notes: list[str] = []
    if returned:
        notes.append(f"слов возвращено на прежнее время: {returned} (сдвиг больше {KNOWN_MOVE_SEC} с); "
                     "если сдвиг нужен, задай align: false")
    if all(known):
        ordered.sort(key=lambda w: float(w["start"]))
        return ordered, notes
    spans: list[tuple[float, float]] | None = None
    failed = False
    i = 0
    while i < len(ordered):
        if known[i]:
            i += 1
            continue
        j = i
        while j < len(ordered) and not known[j]:
            j += 1
        run = ordered[i:j]
        text = " ".join(str(w["word"]) for w in run)
        left, right = float(run[0]["start"]), float(run[-1]["end"])
        low = float(ordered[i - 1]["end"]) if i else max(0.0, left - 1.0)
        high = float(ordered[j]["start"]) if j < len(ordered) else right + 1.0
        low, high = max(low, left - 1.0), min(high, right + 1.0)
        olds = [k for k, w in enumerate(old) if k not in used and left - 0.3 <= (float(w["start"]) + float(w["end"])) / 2 <= right + 0.3]
        if len(olds) == len(run):
            ordered[i:j] = [{**w, "start": round(float(old[k]["start"]), 3), "end": round(float(old[k]["end"]), 3)}
                            for w, k in zip(run, olds)]
            used.update(olds)
            i = j
            continue
        if spans is None and not failed:
            try:
                spans = spans_fn()
            except Exception:  # noqa: BLE001 - нет исходника/звука — время остаётся заданным, с warning
                failed = True
        if failed or spans is None:
            notes.append(f"«{text[:60]}» ({left:.2f} с): не проверено по звуку — нет исходника")
            i = j
            continue
        starts_on_voice = bool(spans_within(spans, left - 0.15, left + 0.15))
        if starts_on_voice and _covered(left, right, spans) >= 0.6:
            i = j
            continue
        parts = spans_within(spans, low, high)
        if sum(e - s for s, e in parts) < 0.08 * len(run):
            notes.append(f"«{text[:60]}» ({left:.2f} с): голоса на этом месте нет — проверь текст и время")
            i = j
            continue
        ordered[i:j] = spread_over_speech(run, parts)
        i = j
    ordered.sort(key=lambda w: float(w["start"]))
    return ordered, notes


def line_sync(lines: list[dict], words: list[dict], spans: list[tuple[float, float]]) -> list[dict]:
    """Расхождения строк с голосом: ``{kind: early|late|lost|silent, start, end, text, by, onset}``."""
    ends = sorted(float(w["end"]) for w in words)
    out: list[dict] = []
    for line in lines:
        a, b = float(line["start"]), float(line["end"])
        text = str(line.get("text") or "")
        here = next((s for s in spans if s[0] - 0.05 <= a < s[1]), None)
        item = {"start": a, "end": b, "text": text}
        if here is None:
            inside = spans_within(spans, a, max(b, a + 0.2) + 0.6)
            if not inside:
                out.append({**item, "kind": "silent", "by": 0.0, "onset": None})
            elif inside[0][0] - a > EARLY_SEC:
                out.append({**item, "kind": "early", "by": round(inside[0][0] - a, 2), "onset": round(inside[0][0], 3)})
            continue
        before = [e for e in ends if e <= a + 1e-6]
        onset = max(here[0], before[-1] if before else 0.0)
        late = a - onset
        if late > LOST_SPEECH_SEC:
            out.append({**item, "kind": "lost", "by": round(late, 2), "onset": round(onset, 3)})
        elif late > LATE_SEC:
            out.append({**item, "kind": "late", "by": round(late, 2), "onset": round(onset, 3)})
    return out


def snap_starts(lines: list[dict], words: list[dict], spans: list[tuple[float, float]]) -> tuple[int, list[dict]]:
    """Первое слово строки, опередившей голос или опоздавшей, — на начало речи (строки и слова правятся на месте).
    → (сколько строк сдвинуто, что осталось решать человеку: потерянные фразы и строки без голоса)."""
    from app.clip_subtitles import words_by_line

    issues = line_sync(lines, words, spans)
    by_start = {i["start"]: i for i in issues if i["kind"] in ("early", "late")}
    moved = 0
    for line, members in zip(lines, words_by_line(lines, words)):
        issue = by_start.get(float(line["start"]))
        if not issue or not members:
            continue
        first = min(members, key=lambda w: float(w["start"]))
        onset = float(issue["onset"])
        if onset >= float(first["end"]) - 0.04:
            first["end"] = round(onset + 0.12, 3)
        first["start"] = line["start"] = round(onset, 3)
        line["end"] = max(float(line["end"]), float(first["end"]))
        moved += 1
    return moved, [i for i in issues if i["kind"] in ("lost", "silent")]


def snap_lines_to_speech(store: Any, clip_plan_id: int) -> dict:
    """Субтитры — на голос. Сначала страницы, разошедшиеся со сказанным (app/speech_heard.py): слова встают на
    распознанное время. Потом начала строк в тишине — по VAD. Текст не меняется; потерянные фразы, строки без голоса и
    нерасслышанные — только в ответе. Распознать нечем — только VAD, и ``by_text: false``."""
    from app.clip_subtitles import words_by_line
    from app.speech_heard import snap_to_heard

    payload = store.get_clip_plan_subtitles(clip_plan_id) or {}
    lines, words = [dict(l) for l in payload.get("lines") or []], [dict(w) for w in payload.get("words") or []]
    if not lines or not words:
        raise ValueError("у клипа нет субтитров")
    spans = plan_speech_spans(store, clip_plan_id)
    members = words_by_line(lines, words)
    by_text, unheard = True, []
    try:
        moved, unheard = snap_to_heard(store, store.get_clip_plan(clip_plan_id), lines, words)
    except Exception:  # noqa: BLE001 - нет модели или выключено распознавание: остаётся VAD
        moved, by_text = 0, False
    if moved:
        for line, own in zip(lines, members):
            if own:  # те же словари, что в words: границы строки идут за её словами
                line["start"] = round(min(float(w["start"]) for w in own), 3)
                line["end"] = round(max(float(w["end"]) for w in own), 3)
    voiced, rest = snap_starts(lines, words, spans)
    moved += voiced
    if moved:
        store.set_clip_plan_subtitles(clip_plan_id, {**payload, "lines": lines, "words": words, "edited": True})
    return {"moved": moved, "by_text": by_text, "lost": sum(i["kind"] == "lost" for i in rest),
            "silent": sum(i["kind"] == "silent" for i in rest), "unheard": len(unheard),
            "rest": [{"kind": i["kind"], "start": i["start"], "text": i["text"][:60]} for i in [*rest, *unheard][:12]]}
