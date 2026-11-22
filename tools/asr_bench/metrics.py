"""Метрики стенда: выдумки на записях без речи, лишние слова в хвосте тишины, доля ошибок по словам (WER)."""

from __future__ import annotations

import re
from typing import Any

TAIL_GRACE_SEC = 0.5  # слово, начатое позже конца речи на столько, — дописано в тишину


_UNITS = "ноль один два три четыре пять шесть семь восемь девять десять одиннадцать двенадцать тринадцать " \
         "четырнадцать пятнадцать шестнадцать семнадцать восемнадцать девятнадцать".split()
_TENS = "_ _ двадцать тридцать сорок пятьдесят шестьдесят семьдесят восемьдесят девяносто".split()
_HUNDREDS = "_ сто двести триста четыреста пятьсот шестьсот семьсот восемьсот девятьсот".split()


def _below_1000(n: int, female: bool = False) -> list[str]:
    out = []
    if n >= 100:
        out.append(_HUNDREDS[n // 100])
        n %= 100
    if n >= 20:
        out.append(_TENS[n // 10])
        n %= 10
    if n or not out:
        word = _UNITS[n]
        if female and n in (1, 2):
            word = "одна" if n == 1 else "две"
        out.append(word)
    return out


def number_words(n: int) -> list[str]:
    """Число цифрами → слова (именительный падеж, до миллиона): Whisper пишет «200 баксов», в эталоне «двести»."""
    if n >= 1_000_000:
        return [str(n)]
    if n < 1000:
        return _below_1000(n)
    th, rest = divmod(n, 1000)
    last, last2 = th % 10, th % 100
    form = "тысяч" if 11 <= last2 <= 14 or last == 0 or last >= 5 else ("тысяча" if last == 1 else "тысячи")
    return _below_1000(th, female=True) + [form] + (_below_1000(rest) if rest else [])


def norm_words(text: Any) -> list[str]:
    """Слова для WER: нижний регистр, ё → е, без ударений, знаков и тегов голоса ([laughs]), числа — словами."""
    low = str(text or "").lower().replace("ё", "е").replace("́", "")
    low = re.sub(r"\[[^\]]*\]", " ", low)
    out: list[str] = []
    for tok in re.findall(r"[a-zа-я0-9]+", low):
        out += number_words(int(tok)) if tok.isdigit() and len(tok) < 7 else [tok]
    return out


def edit_distance(a: list[str], b: list[str]) -> int:
    d = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        prev, d[0] = d[0], i
        for j, y in enumerate(b, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (x != y))
    return d[len(b)]


def wer(ref: str, hyp: str) -> tuple[int, int]:
    """(ошибок, слов в эталоне) — суммируются по набору, доля = Σошибок / Σслов."""
    r, h = norm_words(ref), norm_words(hyp)
    return edit_distance(r, h), len(r)


def tail_words(words: list[dict], speech_end: float, grace: float = TAIL_GRACE_SEC) -> list[dict]:
    """Слова, начатые в хвосте тишины после конца речи."""
    return [w for w in words if float(w["start"]) >= speech_end + grace]


def summarize(items: list[dict], results: dict[str, dict]) -> dict[str, Any]:
    """Сводка профиля по наборам. ``results[set/id] = {text, words:[{word,start,end}], sec}``."""
    out: dict[str, Any] = {}
    by_set: dict[str, list[tuple[dict, dict]]] = {}
    for it in items:
        res = results.get(f"{it['set']}/{it['id']}")
        if res is not None:
            by_set.setdefault(it["set"], []).append((it, res))
    if "nospeech" in by_set:
        rows = by_set["nospeech"]
        hits = [(it["id"], r["text"]) for it, r in rows if norm_words(r["text"])]
        out["nospeech"] = {"clips": len(rows), "hallucinated": len(hits), "examples": hits}
    for name in ("clean", "tail", "music", "dub"):
        if name not in by_set:
            continue
        rows = by_set[name]
        errs = sum(wer(it["ref"], r["text"])[0] for it, r in rows)
        total = sum(wer(it["ref"], r["text"])[1] for it, r in rows)
        info: dict[str, Any] = {"clips": len(rows), "wer": round(errs / max(1, total), 4), "ref_words": total}
        if name == "tail":
            extra = [(it["id"], " ".join(w["word"] for w in tail_words(r["words"], it["speech_end"])))
                     for it, r in rows]
            extra = [(i, t) for i, t in extra if t.strip()]
            info["clips_with_tail_text"] = len(extra)
            info["tail_words"] = sum(len(norm_words(t)) for _, t in extra)
            info["examples"] = extra[:12]
        controls = [(it, r) for it, r in rows if it.get("control")]
        if controls:
            c_err = sum(wer(it["ref"], r["text"])[0] for it, r in controls)
            c_tot = sum(wer(it["ref"], r["text"])[1] for it, r in controls)
            info["controls_wer"] = round(c_err / max(1, c_tot), 4)
            info["controls_lost"] = [it["id"] for it, r in controls
                                     if len(norm_words(r["text"])) < 0.5 * len(norm_words(it["ref"]))]
        out[name] = info
    out["sec"] = round(sum(r.get("sec", 0.0) for r in results.values()), 1)
    return out
