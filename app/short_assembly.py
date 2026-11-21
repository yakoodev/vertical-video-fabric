"""Сборка стрим-шортса «заявка → опровержение → длинный хвост реакции» и её автопроверка.

Разбор (findings.md п. 46, 51): шортс 46 сделан из куска лонга 45 — 49 с ужаты до 21 с (×2,3): 0–5 с заявка
(«я бы никогда в жизни не заплатил»), прыжок сразу к опровержению («держать, нажми держать»), дальше ~12 с смеха.
51 устроен так же: заявка 0–5 с, на 0:05 опровержение, хвост реакции — 80 % шортса.

Правило: если в окне момента есть фраза-заявка («никогда», «точно не», «спорим», never, no way, bet…) — она
первым куском, не длиннее 5 с; середина вырезается до первой реакции (громко/крик/смех по ``audio_map`` или немая
реакция); хвост реакции 8–15 с. Проверка — ``check_short``: длина заявки, время от заявки до опровержения в
готовом ролике, длина хвоста, нет паузы речи > 0,7 с вне хвоста.
"""

from __future__ import annotations

import re
from typing import Iterable

CLAIM_MAX = 5.0
TAIL_MIN, TAIL_MAX = 8.0, 15.0
PAYOFF_LEAD = 1.5        # сколько оставить до первой реакции (сама реплика-опровержение)
MAX_PAUSE = 0.7          # пауза речи вне хвоста в шортсе
REACTION_LABELS = ("громко", "крик", "смех?")
MIN_REACTION_SEC = 0.4
_CLAIM_RE = re.compile(
    r"\b(никогда|ни\s?за\s?что|точно\s+не|сто\s+процентов|сто\s+пудов|сто\s?пудово|сто\s?проц\w*|сп[оа]рим|"
    r"сейчас\s+(?:я\s+)?(?:покажу|сделаю)|сто\s+из\s+ста|сто\s+пудов|клянусь|обещаю|"
    r"never|no\s+way|i\s+bet|bet\b|watch\s+this|i\s+promise|trust\s+me|guaranteed?|easy)\b", re.I)


def is_claim(text: str) -> bool:
    return bool(_CLAIM_RE.search(text or ""))


def find_claim(phrases: Iterable[dict], lo: float, hi: float) -> dict | None:
    """Первая фраза-заявка в окне [lo, hi]: ``{start, end, text}`` (фразы — реплики/строки субтитров)."""
    for p in sorted(phrases or [], key=lambda p: float(p.get("start", 0))):
        a, b = float(p.get("start", 0)), float(p.get("end", 0))
        if a >= lo and b <= hi and b > a and is_claim(str(p.get("text") or "")):
            return {"start": a, "end": b, "text": str(p.get("text") or "")}
    return None


def first_reaction(spans: Iterable[tuple[float, float, str]], after: float, hi: float,
                   reactions: Iterable[dict] = ()) -> float | None:
    """Начало первой реакции после ``after``: громко/крик/смех не короче 0,4 с или немая реакция."""
    times = [float(a) for a, b, lab in spans or [] if lab in REACTION_LABELS and float(b) - float(a) >= MIN_REACTION_SEC
             and after < float(a) < hi]
    times += [float(r["start"]) for r in reactions or [] if after < float(r["start"]) < hi]
    return min(times) if times else None


def assemble_short(phrases: list[dict], spans: list[tuple[float, float, str]], lo: float, hi: float,
                   reactions: list[dict] = ()) -> dict | None:
    """Куски шортса из окна [lo, hi] исходника: [заявка ≤5 с] + [опровержение и хвост 8–15 с].

    Нет заявки или реакции после неё — None (правило неприменимо, монтаж как был)."""
    claim = find_claim(phrases, lo, hi)
    if claim is None:
        return None
    c0, c1 = claim["start"], claim["end"]
    if c1 - c0 > CLAIM_MAX:
        c0 = c1 - CLAIM_MAX  # длинная фраза — её хвост, где обычно и сама заявка
    react = first_reaction(spans, c1, hi, reactions)
    if react is None:
        return None
    p0 = max(c1, react - PAYOFF_LEAD)
    tail_end = min(hi, react + TAIL_MAX)
    if tail_end - react < TAIL_MIN:
        return None  # хвоста реакции нет — шортс этим правилом не собрать
    pieces = [(round(c0, 2), round(c1, 2))]
    if p0 - c1 < 0.3:  # опровержение сразу за заявкой — один кусок
        pieces = [(round(c0, 2), round(tail_end, 2))]
    else:
        pieces.append((round(p0, 2), round(tail_end, 2)))
    return {"pieces": pieces, "claim": claim, "reaction_t": round(react, 2), "tail_end": round(tail_end, 2),
            "source_sec": round(hi - lo, 2), "out_sec": round(sum(b - a for a, b in pieces), 2)}


def _to_out(pieces: list[tuple[float, float]], t: float) -> float | None:
    acc = 0.0
    for a, b in pieces:
        if a <= t <= b:
            return acc + t - a
        acc += b - a
    return None


def check_short(pieces: list[tuple[float, float]], claim: dict, reaction_t: float,
                words: list[tuple[float, float]]) -> dict:
    """Автопроверка собранного шортса (секунды исходника → шкала ролика). ``ok`` и ``problems``."""
    problems: list[str] = []
    total = sum(b - a for a, b in pieces)
    claim_len = min(float(claim["end"]), pieces[0][1]) - max(float(claim["start"]), pieces[0][0])
    if claim_len > CLAIM_MAX + 1e-6:
        problems.append(f"заявка {claim_len:.1f} с > {CLAIM_MAX:g} с")
    react_out = _to_out(pieces, reaction_t)
    if react_out is None:
        problems.append("первая реакция вырезана")
        react_out = total
    payoff_gap = react_out - claim_len
    if payoff_gap > CLAIM_MAX:
        problems.append(f"от заявки до опровержения {payoff_gap:.1f} с > {CLAIM_MAX:g} с — середина не вырезана")
    tail = total - react_out
    if not TAIL_MIN - 1e-6 <= tail <= TAIL_MAX + 1e-6:
        problems.append(f"хвост реакции {tail:.1f} с вне {TAIL_MIN:g}–{TAIL_MAX:g} с")
    # Паузы речи вне хвоста: слова в шкале ролика до реакции.
    out_words = sorted(x for x in ((_to_out(pieces, a), _to_out(pieces, b)) for a, b in words)
                       if x[0] is not None and x[1] is not None and x[1] <= react_out)
    worst = max((b0 - a1 for (_a0, a1), (b0, _b1) in zip(out_words, out_words[1:])), default=0.0)
    if worst > MAX_PAUSE + 1e-6:
        problems.append(f"пауза {worst:.2f} с вне хвоста > {MAX_PAUSE:g} с")
    return {"ok": not problems, "problems": problems, "claim_sec": round(claim_len, 2),
            "claim_to_payoff_sec": round(max(0.0, payoff_gap), 2), "tail_sec": round(tail, 2),
            "max_pause_sec": round(worst, 2), "total_sec": round(total, 2)}
