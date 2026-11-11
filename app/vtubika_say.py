"""Как рассказчик «Витубики» читает слова-термины: словарь произношения только для текста TTS.

Субтитры остаются текстом сценария («Live2D»), а в TTS уходит «как читать» («лайв-два-дэ»): иначе Gemini читает
«Live2D» как «лайв ту ди». Словарь — ``app.vtubika.PRONOUNCE`` (общий на вселенную), план может дополнить или
отключить запись полем ``"pronounce": {"Live2D": "…", "PNGtuber": null}``.

* ``apply`` — замена в тексте для TTS: термин целиком, без учёта регистра, не внутри латинского слова (окончание
  кириллицей сохраняется: «PNGtuber-ы», «PNGtuberом»).
* ``heard_as_script`` — обратный ход для выравнивания: Whisper слышит произнесённое («лайв два дэ», «лайв-два-дэ»,
  слитно с соседним словом), подряд идущие распознанные слова, которые вместе дают вариант произношения,
  сливаются в термин сценария на их общем времени. Дальше ``script_words`` сопоставляет «Live2D-модель:» с
  «Live2D модель» как обычно — подпись «Live2D» встаёт на время сказанного «лайв-два-дэ».
"""

from __future__ import annotations

import re
from typing import Any

_LATIN_ADJ = r"A-Za-z0-9"

# Как Whisper записывает сказанное по словарю (кроме самого произношения): «лайв -2D -модель» (замер 08.11 на
# озвучке «лайв-два-дэ»), «лайф…» — глухое «в» на стыке. Нормализованные, без пробелов и знаков.
HEARD_ALSO: dict[str, tuple[str, ...]] = {
    "Live2D": ("лайв2d", "лайв2д", "лайф2d", "лайфдвадэ", "лайвдвадэ"),
    "Free10": ("фри10", "free10", "фритен", "фридесять"),  # промокод (план: "pronounce": {"Free10": "фри десять"})
}


def _norm(word: Any) -> str:
    # Та же нормализация, что у выравнивания слов (app/explainer_gen_words._norm).
    return re.sub(r"[^a-zа-я0-9]", "", str(word).lower().replace("ё", "е"))


def pronounce_map(plan: dict | None = None) -> dict[str, str]:
    """Словарь вселенной + поле ``pronounce`` плана (значение null убирает запись)."""
    from app import vtubika as U

    merged: dict[str, str] = dict(U.PRONOUNCE)
    for key, value in ((plan or {}).get("pronounce") or {}).items():
        if value is None or not str(value).strip():
            merged.pop(key, None)
        else:
            merged[str(key)] = str(value)
    return merged


def _pattern(term: str) -> re.Pattern:
    body = r"\s+".join(re.escape(part) for part in term.split())
    return re.compile(rf"(?<![{_LATIN_ADJ}]){body}(?![{_LATIN_ADJ}])", re.IGNORECASE)


def apply(text: str, mapping: dict[str, str]) -> str:
    """Текст для TTS: термины словаря → как читать. Длинные термины раньше коротких («VTube Studio» до «VTube»)."""
    out = str(text or "")
    for term in sorted(mapping, key=len, reverse=True):
        out = _pattern(term).sub(lambda _m, say=mapping[term]: say, out)
    return out


def heard_as_script(heard: list[dict], mapping: dict[str, str]) -> list[dict]:
    """Слова Whisper → слова в написании сценария: произношение термина (одним словом, через дефис, несколькими
    словами или слитно с окончанием/соседним словом) сливается в термин на общем времени. Многословный термин
    («VTube Studio») делится на слова сценария пропорционально длине."""
    pairs = {(_norm(say), term) for term, say in mapping.items() if _norm(say)}
    pairs |= {(alt, term) for term in mapping for alt in HEARD_ALSO.get(term, ())}
    variants = sorted(pairs, key=lambda v: (len(v[0]), v), reverse=True)
    out: list[dict] = []
    i = 0
    while i < len(heard):
        hit = None
        if not _norm(heard[i]["word"]):  # знак препинания отдельным словом — термин с него не начинается
            out.append(heard[i])
            i += 1
            continue
        for spoken, term in variants:
            acc = ""
            for j in range(i, len(heard)):
                acc += _norm(heard[j]["word"])
                if acc.startswith(spoken):
                    hit = (j, term, acc[len(spoken):])
                    break
                if not spoken.startswith(acc):
                    break
            if hit:
                break
        if not hit:
            out.append(heard[i])
            i += 1
            continue
        j, term, rest = hit
        start, end = float(heard[i]["start"]), float(heard[j]["end"])
        parts = term.split()
        parts[-1] += rest  # «лайвдвадэмодель» → «Live2Dмодель»: окончание/соседнее слово остаётся при термине
        total = sum(len(p) for p in parts) or 1
        t = start
        for p in parts:
            d = (end - start) * len(p) / total
            out.append({**heard[i], "word": p, "start": round(t, 3), "end": round(t + d, 3)})
            t += d
        i = j + 1
    return out
