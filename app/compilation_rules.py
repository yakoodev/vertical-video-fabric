"""Правила компиляции по разбору подборок (findings.md п. 49, 50, 52) и их автопроверки.

* 20–30 с на кусок, около двух кусков в минуту (49, 52) — ``PIECE_RANGE`` для горизонтали; у шортса-подборки
  свой диапазон ``SHORT_PIECE_RANGE``;
* самый сильный кусок первым, второй по силе — последним: финал на 49 пересматривают (1,00) — ``order_by_strength``;
* открывать и закрывать одной темой (50) — ``bookend_theme``;
* режим «один гэг × много повторов» (50: ~25 повторов «Кай возникает из ниоткуда») — ``find_gag_repeats``;
* в конце без аутро: после последнего слова не больше 1 с — ``outro_cut``.

``check_compilation`` — замер всего этого по готовому порядку кусков.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterable

PIECE_RANGE = (20.0, 30.0)
SHORT_PIECE_RANGE = (5.0, 15.0)
TAIL_AFTER_WORD = 1.0
GAG_MIN_REPEATS = 3
_STOP = frozenset("""
это как что там тут вот так уже еще ещё был была были было есть если когда тоже даже только просто очень
меня тебя него неё нее они она оно мы вы ты он я the and that this with have what just like your you they
from there here then them were been into about would could should really
""".split())


def _tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-zа-яё0-9]+", (text or "").lower().replace("ё", "е")) if len(w) >= 4 and w not in _STOP]


def theme_key(text: str) -> set[str]:
    """Тема куска — основы содержательных слов (первые 5 букв: «игуана»/«игуаны» — одна тема)."""
    return {w[:5] for w in _tokens(text)}


def same_theme(a: str, b: str) -> bool:
    return bool(theme_key(a) & theme_key(b))


def order_by_strength(items: list[Any], strength: Callable[[Any], float]) -> list[Any]:
    """Сильнейший первым, второй по силе последним, середина — в исходном порядке (хронология)."""
    if len(items) < 3:
        return sorted(items, key=lambda x: -strength(x))
    ranked = sorted(range(len(items)), key=lambda i: (-strength(items[i]), i))
    first, last = ranked[0], ranked[1]
    middle = [items[i] for i in range(len(items)) if i not in (first, last)]
    return [items[first], *middle, items[last]]


def bookend_theme(items: list[Any], strength: Callable[[Any], float], text: Callable[[Any], str]) -> list[Any]:
    """Как ``order_by_strength``, но последним — сильнейший кусок той же темы, что первый (если такой есть)."""
    ordered = order_by_strength(items, strength)
    if len(ordered) < 3:
        return ordered
    first = ordered[0]
    same = [x for x in ordered[1:] if same_theme(text(first), text(x))]
    if not same:
        return ordered
    closer = max(same, key=strength)
    rest = [x for x in ordered[1:] if x is not closer]
    return [first, *rest, closer]


def find_gag_repeats(cues: Iterable[dict], phrase: str | None = None, n: int = 2,
                     min_repeats: int = GAG_MIN_REPEATS, min_gap: float = 30.0) -> list[dict]:
    """Повторы одной фразы в транскрипте источника: [{phrase, times, count}] по убыванию числа повторов.

    ``phrase`` задана — её вхождения (по основам слов); нет — повторяющиеся n-граммы содержательных слов.
    Повтор ближе ``min_gap`` с к прошлому — тот же эпизод, не новый."""
    rows = [(float(c.get("start", 0)), [w[:5] for w in _tokens(str(c.get("text") or ""))]) for c in cues or []]
    found: dict[tuple, list[float]] = {}
    if phrase:
        want = tuple(w[:5] for w in _tokens(phrase))
        if not want:
            return []
        for t, toks in rows:
            if all(w in toks for w in want):
                found.setdefault(want, []).append(t)
    else:
        for t, toks in rows:
            for gram in {tuple(toks[i:i + n]) for i in range(len(toks) - n + 1)}:
                found.setdefault(gram, []).append(t)
    out = []
    for gram, times in found.items():
        episodes: list[float] = []
        for t in sorted(times):
            if not episodes or t - episodes[-1] >= min_gap:
                episodes.append(t)
        if len(episodes) >= min_repeats:
            out.append({"phrase": " ".join(gram), "times": [round(t, 2) for t in episodes], "count": len(episodes)})
    return sorted(out, key=lambda r: (-r["count"], r["phrase"]))


def outro_cut(total: float, last_word_end: float | None, tail: float = TAIL_AFTER_WORD) -> dict | None:
    """Вырез хвоста после последнего слова длиннее ``tail`` (шкала «куски подряд»); не нужен — None."""
    if last_word_end is None or total - last_word_end <= tail + 0.05:
        return None
    return {"start": round(last_word_end + tail, 3), "end": round(total, 3)}


def check_compilation(durations: list[float], strengths: list[float], texts: list[str] | None = None,
                      total: float | None = None, last_word_end: float | None = None,
                      piece_range: tuple[float, float] = PIECE_RANGE, bookend: bool = False) -> dict:
    """Замер порядка кусков компиляции. ``durations``/``strengths``/``texts`` — по порядку в ролике."""
    problems: list[str] = []
    lo, hi = piece_range
    off = [i + 1 for i, d in enumerate(durations) if not lo - 1e-6 <= d <= hi + 1e-6]
    if off:
        problems.append(f"куски {off} вне {lo:g}–{hi:g} с")
    if len(strengths) >= 2:
        ranked = sorted(range(len(strengths)), key=lambda i: -strengths[i])
        if strengths[0] < strengths[ranked[0]] - 1e-9:
            problems.append("первым не самый сильный кусок")
        if len(strengths) >= 3 and strengths[-1] < strengths[ranked[1]] - 1e-9 and not bookend:
            problems.append("последним не второй по силе")
    if bookend and texts and len(texts) >= 2 and not same_theme(texts[0], texts[-1]):
        problems.append("первый и последний куски разных тем")
    tail = None
    if total is not None and last_word_end is not None:
        tail = round(total - last_word_end, 2)
        if tail > TAIL_AFTER_WORD + 0.05:
            problems.append(f"после последнего слова {tail:.2f} с > {TAIL_AFTER_WORD:g} с (аутро)")
    return {"ok": not problems, "problems": problems, "pieces": len(durations),
            "per_min": round(len(durations) * 60 / sum(durations), 2) if durations and sum(durations) else 0.0,
            "tail_after_word": tail}
