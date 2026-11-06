"""Объяснялки: слова по времени — выравнивание, цифры, акценты (из ``app/explainer_gen.py``)."""

from __future__ import annotations

import difflib
import re
from typing import Any

from app import explainer as E

REAL_SPLIT_SEC = 3.6  # после озвучки бит длиннее — делится тем же кадром с наездом


def proportional_words(text: str, duration: float) -> list[dict]:
    """Слова текста равномерно по длине (сухой прогон: вместо речи тон)."""
    words = text.split()
    if not words:
        return []
    weights = [len(re.sub(r"\W", "", w)) + 1.0 for w in words]
    start, end = 0.1, max(0.5, duration - 0.1)
    scale = (end - start) / sum(weights)
    out, t = [], start
    for w, wt in zip(words, weights):
        d = wt * scale
        out.append({"word": w, "start": round(t, 3), "end": round(t + d * 0.92, 3)})
        t += d
    return out


def _norm(word: str) -> str:
    return re.sub(r"[^a-zа-я0-9]", "", str(word).lower().replace("ё", "е"))


def align(beats: list[dict], words: list[dict]) -> list[int]:
    """Индекс первого слова озвучки каждого бита (difflib по словам текста и распознанным)."""
    script_tokens: list[str] = []
    owner: list[int] = []
    for n, beat in enumerate(beats):
        for w in beat["text"].split():
            script_tokens.append(_norm(w))
            owner.append(n)
    spoken = [_norm(w["word"]) for w in words]
    mapping = [0] * len(script_tokens)
    sm = difflib.SequenceMatcher(None, script_tokens, spoken, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        for k in range(i1, i2):
            if tag == "equal":
                mapping[k] = j1 + (k - i1)
            elif tag == "replace":
                span = max(1, i2 - i1)
                mapping[k] = min(j2 - 1, j1 + int((k - i1) * (j2 - j1) / span)) if j2 > j1 else j1
            else:  # delete: слова нет в распознанном — к следующему
                mapping[k] = j1
    firsts = []
    for n in range(len(beats)):
        ks = [k for k, o in enumerate(owner) if o == n]
        firsts.append(min(len(words) - 1, mapping[ks[0]]) if ks and words else 0)
    for n in range(1, len(firsts)):  # монотонно
        firsts[n] = max(firsts[n], firsts[n - 1])
    return firsts


def place(beats: list[dict], words: list[dict], total: float) -> list[dict]:
    """Биты по времени: старт — начало первого слова; бит длиннее ``REAL_SPLIT_SEC`` делится тем же
    кадром с наездом; слипшиеся (< 0.35 с) — в соседний."""
    if not words:
        raise E.ExplError("нет слов озвучки")
    firsts = align(beats, words)
    placed: list[dict] = []
    for n, beat in enumerate(beats):
        start = 0.0 if n == 0 else round(float(words[firsts[n]]["start"]), 3)
        w0 = 0 if n == 0 else firsts[n]
        if placed and start - placed[-1]["start"] < 0.35:
            # Слово не нашлось или бит слишком короткий — сливаем с прошлым (поза прошлого).
            placed[-1]["text"] += " " + beat["text"]
            if beat.get("badge") and not placed[-1].get("badge"):
                placed[-1]["badge"] = beat["badge"]
            if beat.get("chip") and not placed[-1].get("chip"):
                placed[-1]["chip"] = beat["chip"]
            placed[-1]["emphasis"] = (placed[-1].get("emphasis") or []) + (beat.get("emphasis") or [])
            continue
        placed.append({**beat, "start": start, "w0": w0})
    for n, item in enumerate(placed):
        item["end"] = placed[n + 1]["start"] if n + 1 < len(placed) else round(total, 3)
        item["w1"] = (placed[n + 1]["w0"] - 1) if n + 1 < len(placed) else len(words) - 1
    out: list[dict] = []
    for item in placed:
        length = item["end"] - item["start"]
        inner = [k for k in range(item["w0"] + 1, item["w1"] + 1)
                 if item["start"] + 1.0 < words[k]["start"] < item["end"] - 1.0]
        if length > REAL_SPLIT_SEC and inner and item is not placed[-1]:
            mid = item["start"] + length / 2
            k = min(inner, key=lambda j: abs(words[j]["start"] - mid))
            cut = round(float(words[k]["start"]), 3)
            first = {**item, "end": cut, "w1": k - 1}
            second = {**item, "start": cut, "w0": k, "variant": "punch", "badge": None, "split_of": item["i"]}
            if item.get("chip") and _word_in(words, item["chip"].get("word"), item["w0"], k - 1) is None:
                first["chip"] = None
            else:
                second["chip"] = None
            out += [first, second]
        else:
            out.append(item)
    return out


def heard_share(text: str, words: list[dict]) -> float:
    """Какая доля слов сценария есть в распознанной речи, в том же порядке (0–1)."""
    script = [t for t in (_norm(w) for w in str(text or "").split()) if t]
    spoken = [_norm(w["word"]) for w in words]
    if not script:
        return 0.0
    sm = difflib.SequenceMatcher(None, script, spoken, autojunk=False)
    return round(sum(b.size for b in sm.get_matching_blocks()) / len(script), 3)


_DIGITS_RE = re.compile(r"^\d[\d.,:]*%?$")
# Основы русских числительных: только для слова, вместо которого Whisper написал цифры.
_NUMERAL_RE = re.compile(r"^(ноль|нол|один|одн|дв|тр[иеё]|четыр|пят|шест|сем|восем|девят|десят|сорок|сто|ста|сот|"
                         r"тысяч|полтор)")


def _digits_for(script_word: str, heard_word: str) -> str | None:
    """Цифры, которые Whisper услышал вместо числа словами («шесть» → «6»): в субтитрах читаются лучше. Только если
    это то же число; иначе (ослышка, слово не про числа) — None, остаётся слово сценария."""
    digits = re.sub(r"[^\d%]+$", "", str(heard_word).strip())
    if not _DIGITS_RE.match(digits):
        return None
    key = _norm(script_word)
    known = _NUMBER_WORD.get(key)
    if known is not None and known != digits:
        return None
    return digits if known is not None or _NUMERAL_RE.match(key) else None


def _word_tail(word: str) -> str:
    """Пунктуация в конце слова сценария."""
    return re.sub(r"^.*?([^\w%]*)$", r"\1", word)


def script_words(text: str, heard: list[dict]) -> list[dict]:
    """Слова сценария на времени распознанной речи: голос читает наш текст, значит показывать надо его, а Whisper
    даёт только время (его «снись» вместо «снизь» в субтитры не идёт). Лишнее распознанное отбрасывается; слову,
    которого Whisper не услышал, отводится промежуток между соседями. Числа, услышанные цифрами, — цифрами."""
    tokens = [w for w in str(text or "").split() if _norm(w)]  # «—» отдельным словом не показываем
    keys = [_norm(w) for w in tokens]
    spoken = [_norm(w["word"]) for w in heard]
    spans: list[list[float] | None] = [None] * len(tokens)
    shown: list[str | None] = list(tokens)
    sm = difflib.SequenceMatcher(None, keys, spoken, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal" or (tag == "replace" and i2 - i1 == j2 - j1):
            for k in range(i2 - i1):
                spans[i1 + k] = [float(heard[j1 + k]["start"]), float(heard[j1 + k]["end"])]
                digits = _digits_for(tokens[i1 + k], heard[j1 + k]["word"]) if tag == "replace" else None
                if digits:
                    shown[i1 + k] = digits + _word_tail(tokens[i1 + k])
        elif tag == "replace":
            start, end = float(heard[j1]["start"]), float(heard[j2 - 1]["end"])
            digits = re.sub(r"[^\d%]+$", "", str(heard[j1]["word"]).strip())
            if j2 - j1 == 1 and _DIGITS_RE.match(digits) and all(_NUMERAL_RE.match(keys[k]) for k in range(i1, i2)):
                # «двадцать пять» → «25»: одно слово на весь кусок.
                shown[i1] = digits + _word_tail(tokens[i2 - 1])
                spans[i1] = [start, end]
                for k in range(i1 + 1, i2):
                    shown[k] = None
                    spans[k] = [end, end]
                continue
            weights = [len(keys[k]) + 1.0 for k in range(i1, i2)]
            scale = (end - start) / sum(weights)
            t = start
            for k, weight in zip(range(i1, i2), weights):
                spans[k] = [t, t + weight * scale]
                t += weight * scale
    n = 0
    while n < len(spans):  # слова без распознанной пары — в промежуток между соседями
        if spans[n] is not None:
            n += 1
            continue
        stop = n
        while stop < len(spans) and spans[stop] is None:
            stop += 1
        count = stop - n
        left = spans[n - 1][1] if n else 0.0
        right = spans[stop][0] if stop < len(spans) else left + 0.3 * count
        if right - left < 0.12 * count and n:  # промежутка нет — половина прошлого слова
            prev = spans[n - 1]
            prev[1] = left = round((prev[0] + prev[1]) / 2, 3)
            right = max(right, left + 0.12 * count)
        for k in range(count):
            spans[n + k] = [left + (right - left) * k / count, left + (right - left) * (k + 1) / count]
        n = stop
    out = []
    for word, span in zip(shown, spans):
        if word is None:
            continue
        start = round(span[0], 3)
        out.append({"word": word, "start": start, "end": round(max(span[1], start + 0.04), 3)})
    return out


def _word_in(words: list[dict], target: Any, w0: int, w1: int) -> int | None:
    """Индекс слова озвучки в [w0, w1]: то же слово, та же основа (4 буквы) или число («семь» → «7»)."""
    key = _norm(target)
    if not key:
        return None
    keys = [key] + ([_NUMBER_WORD[key]] if key in _NUMBER_WORD else [])
    for want in keys:
        for k in range(max(0, w0), min(len(words) - 1, w1) + 1):
            word = _norm(words[k]["word"])
            if word == want or (len(want) >= 4 and word.startswith(want[:4])):
                return k
    return None


_NUMBER_WORD = {"ноль": "0", "один": "1", "одну": "1", "два": "2", "две": "2", "три": "3", "четыре": "4",
                "пять": "5", "шесть": "6", "семь": "7", "восемь": "8", "девять": "9", "десять": "10",
                "двенадцать": "12", "двух": "2", "трех": "3"}


def emphasis_marks(placed: list[dict], words: list[dict]) -> dict[int, int]:
    """Индексы слов субтитров с эмфазой: ключевые слова битов + числа чипов."""
    marks: dict[int, int] = {}
    for item in placed:
        targets = list(item.get("emphasis") or [])
        if item.get("chip") and item["chip"].get("word"):
            targets.append(item["chip"]["word"])
        for target in targets:
            k = _word_in(words, target, item["w0"], item["w1"])
            if k is not None:
                marks[k] = 1
    return marks
