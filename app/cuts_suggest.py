"""Вырезы: подсказки — паузы, слова-паразиты, островки (из ``app/cuts.py``)."""

from __future__ import annotations

import re
from typing import Any

from app.piece_speed import seg_length
from app.cuts_map import MIN_CUT_SEC, MAX_CUTS, MERGE_GAP_SEC, _finite, normalize_cuts, cuts_total
from app.cuts_plan import MIN_PART_SEC

LONG_PAUSE_SEC = 2.5


# Остров — то, что зритель видит одним куском между настоящими разрывами (kept_islands).
# Горизонталка Муры 31.10: 72 куска, 15 короче 3 с — «моменты из одного слова».
MIN_ISLAND_SEC = 2.5
MIN_ISLAND_WORDS = 3
# Правило слов — только для коротких: длинный тихий кусок игры обрывком не считается.
ISLAND_WORDS_MAX_SEC = 6.0
# Вырез паузы или «ээ» не длиннее этого — речь идёт дальше той же сценой, разрыва нет
# (длиннее и suggest_cuts помечает risky); столько слов и больше — вырезали сам разговор.
SOFT_CUT_SEC = LONG_PAUSE_SEC
HARD_CUT_WORDS = 3
# Соседние куски идут подряд по исходнику — стык не разрыв.
PIECE_GAP_SEC = 0.05
ISLAND_WARNINGS_MAX = 8


# ---------------------------------------------------------------- подсказки


# Слова-паразиты целиком (после нормализации: строчные, без пунктуации, ё→е).
FILLER_WORDS = {"короче", "типа"}
FILLER_PHRASES = (("как", "бы"), ("в", "общем"), ("это", "самое"))
# Междометия-мычание: э, ээ, эээ…, эм, эмм, ммм.
HESITATION_RE = re.compile(r"^(э+|э+м+|м{2,}|а+м+)$")
# «Ну» — паразит, только когда стоит отдельно, между паузами.
STANDALONE_ONLY = {"ну"}
STANDALONE_GAP_SEC = 0.2
# Повтор слова («я я я») — заикание; эти повторы обычно нарочно.
EMPHATIC_REPEATS = {"да", "нет", "давай", "стоп", "тихо", "очень", "ха", "хаха", "го", "все", "ну"}
STUTTER_MAX_GAP_SEC = 1.0
# Фраза из двух слов («это самое», «в общем») — паразит, только если рядом
# пауза: «это самое главное», «в общем смысле» подряд — настоящая речь.
PHRASE_PAUSE_SEC = 0.25
_WORD_CLEAN_RE = re.compile(r"[^\w-]+", re.UNICODE)


def _norm_word(text: Any) -> str:
    word = str(text or "").lower().replace("ё", "е")
    return _WORD_CLEAN_RE.sub("", word).strip("-_")


def _clean_words(words: Any) -> list[dict]:
    out = []
    for item in words or []:
        if not isinstance(item, dict):
            continue
        s, e = _finite(item.get("start")), _finite(item.get("end"))
        if s is None or e is None or e < s:
            continue
        out.append({"start": s, "end": e, "word": str(item.get("word") or ""), "norm": _norm_word(item.get("word"))})
    out.sort(key=lambda w: w["start"])
    return out


def _fmt_sec(value: float) -> str:
    return f"{value:.1f}".replace(".", ",") if value < 10 else f"{value:.0f}"


def _pause_items(words: list[dict], duration: float, min_pause: float, keep_pad: float) -> list[dict]:
    items = []
    if not words:
        return items
    bounds = [(0.0, words[0]["start"])]
    bounds += [(a["end"], b["start"]) for a, b in zip(words, words[1:])]
    if duration > words[-1]["end"]:
        bounds.append((words[-1]["end"], duration))
    for index, (left, right) in enumerate(bounds):
        gap = right - left
        if gap <= min_pause:
            continue
        lead = 0.0 if index == 0 else keep_pad  # перед первым словом тишину не бережём
        tail = 0.0 if (index == len(bounds) - 1 and right >= duration - 1e-6 and index > 0) else keep_pad
        start, end = left + lead, right - tail
        if end - start >= MIN_CUT_SEC:
            item = {"start": start, "end": end, "reason": f"пауза {_fmt_sec(gap)} с"}
            # Длинная тишина в реакционном контенте часто и есть момент (стример молча
            # смотрит на скример, муху под Bad Apple) — предлагаем, но не отмечаем.
            # Внутренняя пауза — между двумя словами (bounds[1..len(words)-1]);
            # хвост после последнего слова может и отсутствовать в bounds.
            if gap >= LONG_PAUSE_SEC and 0 < index < len(words):
                item["reason"] += " — проверь: может, это и есть момент"
                item["risky"] = True
            items.append(item)
    return items


def _filler_span(words: list[dict], first: int, last: int, duration: float, keep_pad: float) -> tuple[float, float]:
    """Вырез под слово(а) words[first..last] — вместе с тишиной вокруг, но с запасом у соседей."""
    prev_end = words[first - 1]["end"] if first > 0 else 0.0
    next_start = words[last + 1]["start"] if last + 1 < len(words) else max(duration, words[last]["end"])
    start = min(words[first]["start"], prev_end + keep_pad)
    end = max(words[last]["end"], next_start - keep_pad)
    # Длинную паузу внутри клипа паразит не забирает: она «рискованная» и
    # предлагается отдельно — может, это и есть момент (см. _pause_items).
    if first > 0 and words[first]["start"] - prev_end >= LONG_PAUSE_SEC:
        start = words[first]["start"]
    if last + 1 < len(words) and next_start - words[last]["end"] >= LONG_PAUSE_SEC:
        end = words[last]["end"]
    return max(prev_end, start), min(next_start, end)


def _filler_items(words: list[dict], duration: float, keep_pad: float) -> list[dict]:
    items = []
    i = 0
    n = len(words)
    while i < n:
        w = words[i]
        token = w["norm"]
        # Фразы из двух слов: «как бы», «в общем», «это самое».
        if i + 1 < n:
            pair = (token, words[i + 1]["norm"])
            gap_before = w["start"] - (words[i - 1]["end"] if i > 0 else 0.0)
            gap_after = (words[i + 2]["start"] if i + 2 < n else max(duration, words[i + 1]["end"])) - words[i + 1]["end"]
            if pair in FILLER_PHRASES and max(gap_before, gap_after) >= PHRASE_PAUSE_SEC - 1e-9:
                start, end = _filler_span(words, i, i + 1, duration, keep_pad)
                items.append({"start": start, "end": end, "reason": f"слова «{w['word'].strip()} {words[i + 1]['word'].strip()}»"})
                i += 2
                continue
        hit = False
        if token in FILLER_WORDS or (token and HESITATION_RE.match(token)):
            hit = True
        elif token in STANDALONE_ONLY:
            gap_before = (w["start"] - words[i - 1]["end"]) if i > 0 else float("inf")
            gap_after = (words[i + 1]["start"] - w["end"]) if i + 1 < n else float("inf")
            hit = gap_before >= STANDALONE_GAP_SEC and gap_after >= STANDALONE_GAP_SEC
        if hit:
            start, end = _filler_span(words, i, i, duration, keep_pad)
            items.append({"start": start, "end": end, "reason": f"слово «{w['word'].strip() or token}»"})
            i += 1
            continue
        # Заикание: то же слово подряд («я я я пошёл») — оставляем последнее.
        j = i
        while (
            j + 1 < n
            and token
            and words[j + 1]["norm"] == token
            and token not in EMPHATIC_REPEATS
            and words[j + 1]["start"] - words[j]["end"] <= STUTTER_MAX_GAP_SEC
        ):
            j += 1
        if j > i:
            prev_end = words[i - 1]["end"] if i > 0 else 0.0
            start = max(prev_end, min(words[i]["start"], prev_end + keep_pad))
            end = min(words[j]["start"], max(words[j - 1]["end"], words[j]["start"] - keep_pad))
            if end - start >= MIN_CUT_SEC:
                repeats = " ".join(x["word"].strip() for x in words[i:j])
                items.append({"start": start, "end": end, "reason": f"повтор «{repeats}»"})
            i = j
            continue
        i += 1
    return [it for it in items if it["end"] - it["start"] >= MIN_CUT_SEC]


# Оборванная фраза в конце клипа (QA 6, клип 898: после панчлайна «Я обосралась, п*****.» — «Седьмой стрель.»,
# конец куска на полуслове). Речь упирается в конец клипа (последнее слово кончается ближе TAIL_EDGE_SEC), а
# после законченного предложения (точка/«!»/«?» и пауза) начинается короткий обрывок — его вырезаем до конца клипа.
TAIL_EDGE_SEC = 0.15
TAIL_SPLIT_GAP_SEC = 0.15
TAIL_FRAGMENT_MAX_SEC = 2.0
TAIL_FRAGMENT_MAX_WORDS = 4
_SENTENCE_END_RE = re.compile(r"[.!?…]+[»\"')\]]*$")


def tail_fragment(words: Any, duration: float, keep_pad: float = 0.12) -> dict | None:
    """Обрывок новой фразы в самом конце клипа → вырез ``{start, end, reason, words}`` до конца клипа; нет — None.

    Обрывок — слова после последнего конца предложения (слово с «.», «!», «?», «…» и пауза ≥ TAIL_SPLIT_GAP_SEC), не длиннее
    TAIL_FRAGMENT_MAX_SEC и TAIL_FRAGMENT_MAX_WORDS, и речь доходит до самого конца клипа (фраза не договорена).
    Тишина после предыдущей фразы (реакция) остаётся, сколько можно."""
    clean = _clean_words(words)
    if len(clean) < 2:
        return None
    total = max(float(duration or 0.0), clean[-1]["end"])
    if total - clean[-1]["end"] > TAIL_EDGE_SEC:
        return None  # клип кончается тишиной — фраза договорена
    k = len(clean) - 1
    while k > 0:
        prev = clean[k - 1]
        if clean[k]["start"] - prev["end"] >= TAIL_SPLIT_GAP_SEC and _SENTENCE_END_RE.search(prev["word"].strip()):
            break
        k -= 1
    if k <= 0:
        return None  # конца предложения перед концом клипа нет — обрывка не видно
    fragment = clean[k:]
    start_at = fragment[0]["start"]
    if total - start_at > TAIL_FRAGMENT_MAX_SEC or len(fragment) > TAIL_FRAGMENT_MAX_WORDS:
        return None
    prev_end = clean[k - 1]["end"]
    start = min(max(prev_end + max(0.0, keep_pad), start_at - 0.08), start_at - 0.02)
    start = max(prev_end, start)
    if total - start < MIN_CUT_SEC:
        return None
    text = " ".join(w["word"].strip() for w in fragment)
    return {"start": round(start, 3), "end": round(total, 3), "words": text,
            "reason": f"оборванная фраза в конце «{text}» — клип кончается на полуслове"}


def _piece_breaks(segments: Any) -> list[float]:
    """Стыки кусков (шкала «куски подряд»), где монтаж прыгает в другое место исходника."""
    out: list[float] = []
    at = 0.0
    prev: dict | None = None
    for seg in segments or []:
        if prev is not None:
            joined = abs(float(seg["start_sec"]) - float(prev["end_sec"])) <= PIECE_GAP_SEC
            if not joined or seg.get("reverse") or prev.get("reverse"):
                out.append(round(at, 3))
        at += seg_length(seg)
        prev = seg
    return out


def kept_islands(cuts: Any, words: Any, segments: Any = None, total: float | None = None) -> list[dict]:
    """Что останется после вырезов — острова по порядку, всё на шкале «куски подряд».

    Разрыв — вырез длиннее ``SOFT_CUT_SEC`` или со словами (от ``HARD_CUT_WORDS``), вырез через стык
    кусков и сам стык, если следующий кусок не продолжает предыдущий по исходнику. Вырез паузы разрывом
    не считается: фраза идёт дальше той же сценой, и панчлайн сразу за ним — не обрывок, а её конец.
    Обрывки короче ``MIN_PART_SEC`` рендер всё равно отдаёт вырезу (``plan_cuts``) — здесь их нет.

    Ответ: ``[{start, end, duration, words, text, parts, by_cut}]``; ``duration`` — без вырезов
    внутри, ``by_cut`` — острова коснулся вырез (иначе это кусок целиком, как его создали).
    """
    length = sum(seg_length(s) for s in segments or [])
    total = float(total) if total is not None else length
    spans = [(c["start"], c["end"]) for c in normalize_cuts(cuts)]
    breaks = [b for b in _piece_breaks(segments) if 0.0 < b < total]

    parts: list[tuple[float, float]] = []
    cursor = 0.0
    for a, b in spans + [(total, total)]:
        stops = [cursor] + [x for x in breaks if cursor < x < a] + [a]
        parts += [(p, q) for p, q in zip(stops, stops[1:]) if q - p >= MIN_PART_SEC]
        cursor = max(cursor, b)

    clean = _clean_words(words)
    groups: list[list[tuple[float, float]]] = []
    for part in parts:
        if groups:
            gap_from = groups[-1][-1][1]
            gap = part[0] - gap_from
            lost = sum(gap_from <= (w["start"] + w["end"]) / 2 <= part[0] for w in clean)
            hard = (gap <= 1e-9 or gap > SOFT_CUT_SEC or lost >= HARD_CUT_WORDS
                    or any(gap_from <= x <= part[0] for x in breaks))
            if not hard:
                groups[-1].append(part)
                continue
        groups.append([part])

    islands = []
    for group in groups:
        heard = [w["word"].strip() for w in clean
                 if any(a <= (w["start"] + w["end"]) / 2 <= b for a, b in group)]
        start, end = group[0][0], group[-1][1]
        by_cut = len(group) > 1 or any(abs(start - b) < 1e-6 or abs(end - a) < 1e-6 for a, b in spans)
        islands.append({"start": round(start, 3), "end": round(end, 3),
                        "duration": round(sum(b - a for a, b in group), 3), "words": len(heard),
                        "text": " ".join(heard), "parts": len(group), "by_cut": by_cut})
    return islands


def short_islands(islands: list[dict], has_words: bool = True, min_sec: float = MIN_ISLAND_SEC,
                  min_words: int = MIN_ISLAND_WORDS, by_cut_only: bool = True) -> list[dict]:
    """Обрывки среди ``kept_islands``: короче ``min_sec`` или (если субтитры есть) меньше
    ``min_words`` слов на коротком острове. Единственный остров — это весь клип, не обрывок;
    ``min_sec`` ≤ 0 — проверка выключена. ``by_cut_only`` — без кусков, созданных короткими нарочно."""
    if len(islands) < 2 or min_sec <= 0:
        return []
    words_until = max(ISLAND_WORDS_MAX_SEC, min_sec)

    def short(island: dict) -> bool:
        if island["duration"] < min_sec:
            return True
        return has_words and island["words"] < min_words and island["duration"] < words_until

    return [i for i in islands if short(i) and (i["by_cut"] or not by_cut_only)]


def merge_short_islands(cuts: Any, words: Any, segments: Any = None, total: float | None = None,
                        min_sec: float = MIN_ISLAND_SEC) -> tuple[list[dict], list[dict]]:
    """Вырезать обрывки вместе с соседними вырезами: (новые вырезы, вырезанные острова).
    Весь клип не вырезаем: если обрывки всё, что есть, самый длинный остаётся."""
    islands = kept_islands(cuts, words, segments, total)
    doomed = short_islands(islands, bool(_clean_words(words)), min_sec)
    if len(doomed) == len(islands):
        keep = max(doomed, key=lambda i: i["duration"])
        doomed = [i for i in doomed if i is not keep]
    if not doomed:
        return normalize_cuts(cuts), []
    extra = [{"start": i["start"], "end": i["end"]} for i in doomed]
    return normalize_cuts(normalize_cuts(cuts) + extra), doomed


def island_warnings(islands: list[dict], to_clip: Any = None, limit: int = ISLAND_WARNINGS_MAX) -> list[str]:
    """Обрывки — по-человечески; ``to_clip(start, end)`` — шкала, которую видит агент."""
    out = []
    for island in islands[:limit]:
        a, b = to_clip(island["start"], island["end"]) if to_clip else (island["start"], island["end"])
        heard = f"«{island['text']}»" if island["text"] else "кусок без речи"
        out.append(f"обрывок {heard} на {a:.2f}–{b:.2f} ({island['duration']:.1f} с, слов: {island['words']}) — "
                   "зритель увидит вспышку без начала и конца: сдвинь край соседнего выреза, чтобы фраза "
                   "прозвучала целиком, или вырежи её (apply_cuts с merge_short_islands: true)")
    if len(islands) > limit:
        out.append(f"и ещё обрывков: {len(islands) - limit} — смотри lint_clip, проверка fragments")
    return out


def _spare_islands(items: list[dict], words: list[dict], segments: Any, total: float) -> list[dict]:
    """Рекомендованный вырез, после которого остался бы обрывок, становится ``risky``: пауза лучше вспышки.

    Снимаем меньший из внутренних вырезов у обрывка. Вырезы по краям клипа (тишина в начале, оборванный
    хвост) соседей не соединяют — их не трогаем."""
    has_words = bool(words)
    for _round in range(len(items) + 1):
        safe = [it for it in items if not it.get("risky")]
        spare = None
        for island in short_islands(kept_islands(safe, words, segments, total), has_words):
            near = [it for it in safe if it["start"] > 0 and it["end"] < total - 1e-6
                    and (abs(it["end"] - island["start"]) < 1e-3 or abs(it["start"] - island["end"]) < 1e-3)]
            if near:
                spare = (min(near, key=lambda it: it["end"] - it["start"]), island)
                break
        if spare is None:
            return items
        item, island = spare
        item["risky"] = True
        heard = f"«{island['text']}»" if island["text"] else "кусок без речи"
        item["reason"] = f"{item['reason']} — но оставит обрывок {heard}, смотри сам"
    return items


def suggest_cuts(
    words: Any,
    duration: float,
    mode: str = "both",
    min_pause: float = 0.45,
    keep_pad: float = 0.12,
    segments: Any = None,
) -> dict:
    """Что вырезать по пословным таймингам клипа. Ничего не сохраняет.

    ``mode``: pauses — тишина между словами длиннее ``min_pause`` (середина,
    ``keep_pad`` тишины у каждого слова остаётся); fillers — слова-паразиты и
    повторы; both — всё вместе (пересекающиеся вырезы сливаются, причины —
    через «+»). Вырез, который оставил бы обрывок (``kept_islands``), в рекомендованные не идёт:
    ``segments`` — куски клипа, чтобы видеть обрывки и на их стыках.
    """
    clean = _clean_words(words)
    duration = max(float(duration or 0.0), clean[-1]["end"] if clean else 0.0)
    keep_pad = max(0.0, float(keep_pad))
    min_pause = max(0.1, float(min_pause))
    raw: list[dict] = []
    if mode in ("pauses", "both"):
        raw += _pause_items(clean, duration, min_pause, keep_pad)
        tail = tail_fragment(clean, duration, keep_pad)
        if tail:
            raw.append({k: tail[k] for k in ("start", "end", "reason")})
    if mode in ("fillers", "both"):
        raw += _filler_items(clean, duration, keep_pad)
    raw.sort(key=lambda it: (it["start"], it["end"]))

    def merged(group: list[dict]) -> list[dict]:
        out: list[dict] = []
        for item in group:
            start, end = max(0.0, item["start"]), min(duration, item["end"])
            if end - start < MIN_CUT_SEC:
                continue
            if out and start <= out[-1]["end"] + MERGE_GAP_SEC:
                last = out[-1]
                last["end"] = max(last["end"], end)
                if item["reason"] not in last["reason"]:
                    last["reason"] = f"{last['reason']} + {item['reason']}"
                continue
            out.append({"start": start, "end": end, "reason": item["reason"], **({"risky": True} if item.get("risky") else {})})
        return out

    # Рискованное (длинная пауза) с обычным не сливаем: иначе пауза «съест»
    # соседний паразит и он выпадет из рекомендованного набора. Обычные вырезы
    # остаются целиком, рискованные — только то, что вне них.
    safe = merged([it for it in raw if not it.get("risky")])
    risky: list[dict] = []
    for item in merged([it for it in raw if it.get("risky")]):
        pieces = [(item["start"], item["end"])]
        for cut in safe:
            pieces = [
                part
                for a, b in pieces
                for part in ((a, min(b, cut["start"])), (max(a, cut["end"]), b))
                if part[1] - part[0] > 1e-9
            ]
        risky += [{**item, "start": a, "end": b} for a, b in pieces if b - a >= MIN_CUT_SEC]
    items = sorted(safe + risky, key=lambda it: (it["start"], it["end"]))
    items = [{**it, "start": round(it["start"], 3), "end": round(it["end"], 3)} for it in items[:MAX_CUTS]]
    items = _spare_islands(items, clean, segments, duration)
    # Рекомендованный набор — без «рискованных» длинных пауз: их владелец отмечает сам.
    cuts = normalize_cuts([it for it in items if not it.get("risky")])
    saved = cuts_total(cuts)
    return {
        "cuts": cuts,
        "items": items,
        "saved_sec": saved,
        "before_sec": round(duration, 3),
        "after_sec": round(max(0.0, duration - saved), 3),
    }
