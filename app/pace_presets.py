"""Темп по типу ролика (findings.md п. 43, 45, 53): какие паузы речи резать и проверка результата.

* ``reaction_full`` — горизонтальная «полная реакция»: только паузы > 3 с, вырезано не больше 10 % (43: 7 пауз
  > 3 с за 40 минут);
* ``horizontal_game`` — горизонтальная игровая нарезка: паузы > 6 с (45: 49 пауз > 3 с за 12 минут оставлены —
  геймплей без слов не режут), не больше 10 %;
* ``short`` — шортс: паузы > 0,7 с;
* ``explainer`` — объяснялка: паузы голоса > 0,3 с.

``pace_cuts`` — вырезы (секунды «кусками подряд», как ``cuts_suggest``) с запасом тишины у слов; если доля
вырезанного больше предела — берутся самые длинные паузы, пока доля не уложится. ``check_pace`` — замер после
вырезов: нет паузы длиннее порога (кроме оставленных ради предела) и доля вырезанного в пределе.
"""

from __future__ import annotations

PRESETS: dict[str, dict] = {
    "reaction_full": {"min_pause": 3.0, "max_share": 0.10, "keep_pad": 0.3},
    "horizontal_game": {"min_pause": 6.0, "max_share": 0.10, "keep_pad": 0.5},
    "short": {"min_pause": 0.7, "max_share": 1.0, "keep_pad": 0.12},
    "explainer": {"min_pause": 0.3, "max_share": 1.0, "keep_pad": 0.08},
}


def _words(words: list[dict] | list[tuple[float, float]]) -> list[tuple[float, float]]:
    out = []
    for w in words or []:
        a, b = (w["start"], w["end"]) if isinstance(w, dict) else w
        out.append((float(a), float(b)))
    return sorted(out)


def pauses(words: list, duration: float) -> list[tuple[float, float]]:
    """Паузы речи между словами (без тишины до первого и после последнего слова)."""
    ws = _words(words)
    return [(a1, b0) for (_a0, a1), (b0, _b1) in zip(ws, ws[1:]) if b0 > a1]


def pace_cuts(words: list, duration: float, preset: str) -> list[dict]:
    cfg = PRESETS[preset]
    pad = float(cfg["keep_pad"])
    # Пауза длиннее порога сокращается до «запаса» у слов: остаётся 2·pad, но не больше порога.
    keep = min(2 * pad, float(cfg["min_pause"]))
    long = [(a, b) for a, b in pauses(words, duration) if b - a > cfg["min_pause"]]
    cuts = [{"start": round(a + keep / 2, 3), "end": round(b - keep / 2, 3)} for a, b in long]
    limit = float(cfg["max_share"]) * float(duration)
    if sum(c["end"] - c["start"] for c in cuts) > limit:
        chosen, total, skipped = [], 0.0, []
        for c in sorted(cuts, key=lambda c: -(c["end"] - c["start"])):
            if total + (c["end"] - c["start"]) <= limit:
                chosen.append(c)
                total += c["end"] - c["start"]
            else:
                skipped.append(c)
        budget = limit - total
        if skipped and budget >= 0.3:  # остаток предела — на самую длинную паузу: укоротить её, а не оставить целиком
            c = skipped[0]
            mid = (c["start"] + c["end"]) / 2
            chosen.append({"start": round(mid - budget / 2, 3), "end": round(mid + budget / 2, 3)})
        cuts = sorted(chosen, key=lambda c: c["start"])
    return cuts


def apply_cuts_to_words(words: list, cuts: list[dict]) -> list[tuple[float, float]]:
    """Слова в шкале после вырезов (для замера)."""
    out = []
    for a, b in _words(words):
        shift = sum(c["end"] - c["start"] for c in cuts if c["end"] <= a + 1e-9)
        out.append((a - shift, b - shift))
    return out


def check_pace(words: list, duration: float, cuts: list[dict], preset: str) -> dict:
    cfg = PRESETS[preset]
    cut_total = sum(float(c["end"]) - float(c["start"]) for c in cuts)
    share = cut_total / duration if duration > 0 else 0.0
    after = apply_cuts_to_words(words, cuts)
    worst = max((b0 - a1 for (_a0, a1), (b0, _b1) in zip(after, after[1:])), default=0.0)
    problems = []
    if share > cfg["max_share"] + 1e-6:
        problems.append(f"вырезано {share:.0%} > {cfg['max_share']:.0%}")
    capped = cfg["max_share"] < 1.0 and share >= cfg["max_share"] - 0.02
    if worst > cfg["min_pause"] + 1e-6 and not capped:
        problems.append(f"осталась пауза {worst:.2f} с > {cfg['min_pause']:g} с")
    return {"ok": not problems, "problems": problems, "cut_share": round(share, 3), "max_pause": round(worst, 2),
            "cuts": len(cuts)}
