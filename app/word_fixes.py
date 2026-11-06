"""Правки субтитров руками при пересборке выпуска (решение 14, docs/redesign/concept-2026-11.md).

Сборка заново раскладывает слова сценария по времени речи, поэтому без этого модуля правка владельца
(«снись» → «снизь») молча пропала бы вместе с прошлой версией. Здесь чистые функции над списками слов:

* ``find_fixes`` — чем субтитры клипа отличаются от слов, с которыми он был собран;
* ``carry_fixes`` — перенести эти отличия на новые слова там, где исправленный кусок ещё есть;
* ``summary`` — что сказать владельцу и агенту.

Сравниваем текст. Время слова новая сборка берёт из речи: сдвиг времени не переносится, но считается
и называется, он остаётся в прошлой версии клипа.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

_EDGE = re.compile(r"^[\W_]+|[\W_]+$", re.U)
TIMING_TOLERANCE_SEC = 0.05


def _key(word: Any) -> str:
    return _EDGE.sub("", str(word or "").strip()).casefold().replace("ё", "е")


def _text(item: dict) -> str:
    return str(item.get("word") or "").strip()


def find_fixes(base: list[dict], current: list[dict]) -> dict:
    """Что поправили в субтитрах после сборки. ``fixes`` — куски {at, was, now}: ``at`` — номер слова в ``base``,
    ``was`` — слова сборки, ``now`` — что вместо них (пусто — слово убрали). ``timing`` — слов со сдвинутым временем."""
    base = [w for w in base or [] if _text(w)]
    current = [w for w in current or [] if _text(w)]
    if not base or not current:
        return {"fixes": [], "timing": 0}
    base_keys = [_key(_text(w)) for w in base]
    current_keys = [_key(_text(w)) for w in current]
    fixes: list[dict] = []
    timing = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, base_keys, current_keys, autojunk=False).get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                a, b = base[i1 + k], current[j1 + k]
                if _text(a) != _text(b):  # только регистр или знак: тоже правка владельца
                    fixes.append({"at": i1 + k, "was": [_text(a)], "now": [dict(b)]})
                elif abs(float(a.get("start") or 0) - float(b.get("start") or 0)) > TIMING_TOLERANCE_SEC:
                    timing += 1
            continue
        fixes.append({"at": i1, "was": [_text(w) for w in base[i1:i2]], "now": [dict(w) for w in current[j1:j2]]})
    return {"fixes": fixes, "timing": timing}


def label(fix: dict) -> str:
    """«снись → снизь», «лишнее → убрано», «добавлено: слово»."""
    was = " ".join(fix.get("was") or [])
    now = " ".join(_text(w) for w in fix.get("now") or [])
    if not was:
        return f"добавлено: {now}"
    if not now:
        return f"{was} → убрано"
    return f"{was} → {now}"


def _spread(now: list[dict], start: float, end: float) -> list[dict]:
    """Новые слова куска: на время тех слов сборки, которые они заменили."""
    span = max(end - start, 0.08 * len(now))
    weights = [len(_text(w)) + 1.0 for w in now]
    total = sum(weights) or 1.0
    out, cursor = [], start
    for word, weight in zip(now, weights):
        length = span * weight / total
        item = {k: v for k, v in word.items() if k not in ("start", "end")}
        out.append({**item, "start": round(cursor, 3), "end": round(cursor + length, 3)})
        cursor += length
    return out


def carry_fixes(base: list[dict], fixes: list[dict], new_words: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """Перенести правки на новые слова: возвращает (слова, перенесённые, потерянные). Правка теряется, только если
    исправленных слов в новом тексте больше нет: сценарий переписан именно в этом месте."""
    if not fixes:
        return list(new_words), [], []
    base = [w for w in base or [] if _text(w)]
    new_words = list(new_words)
    base_keys = [_key(_text(w)) for w in base]
    new_keys = [_key(_text(w)) for w in new_words]
    position: dict[int, int] = {}
    for block in difflib.SequenceMatcher(None, base_keys, new_keys, autojunk=False).get_matching_blocks():
        for k in range(block.size):
            position[block.a + k] = block.b + k
    carried, lost = [], []
    replace: dict[int, tuple[int, list[dict]]] = {}  # начало в новых словах → (сколько заменить, чем)
    for fix in fixes:
        at, width = int(fix["at"]), len(fix.get("was") or [])
        if width:
            spots = [position.get(at + k) for k in range(width)]
            if any(s is None for s in spots) or spots != list(range(spots[0], spots[0] + width)):
                lost.append(fix)
                continue
            first, last = spots[0], spots[-1]
            words = _spread(fix.get("now") or [], float(new_words[first]["start"]), float(new_words[last]["end"]))
            replace[first] = (width, words)
        else:  # добавленное слово: перед тем словом сборки, что шло за ним
            anchor = position.get(at)
            if anchor is None:
                lost.append(fix)
                continue
            start = float(new_words[anchor]["start"])
            previous = float(new_words[anchor - 1]["end"]) if anchor else max(0.0, start - 0.3)
            replace.setdefault(anchor, (0, []))
            replace[anchor] = (replace[anchor][0], replace[anchor][1] + _spread(fix.get("now") or [], previous, start))
        carried.append(fix)
    out: list[dict] = []
    n = 0
    while n < len(new_words):
        if n in replace:
            width, words = replace[n]
            out += words
            if width:
                n += width
                continue
        out.append(new_words[n])
        n += 1
    return out, carried, lost


def summary(found: dict, carried: list[dict] | None = None, lost: list[dict] | None = None) -> dict:
    """Итог для ролика и экрана: ``note`` — готовая фраза, пусто, если правок не было."""
    fixes = found.get("fixes") or []
    timing = int(found.get("timing") or 0)
    if not fixes and not timing:
        return {"count": 0, "timing": 0, "examples": [], "lost": [], "note": ""}
    examples = [label(f) for f in fixes][:4]
    lost_labels = [label(f) for f in lost or []]
    parts: list[str] = []
    if carried is None:
        if fixes:
            parts.append(f"Правки субтитров руками: {len(fixes)} ({'; '.join(examples)}). Новая сборка их перенесёт, "
                         "если эти слова останутся в сценарии, про остальные скажет.")
    else:
        if carried:
            parts.append(f"Правки субтитров перенесены в новую версию: {len(carried)} "
                         f"({'; '.join(label(f) for f in carried[:4])}).")
        if lost_labels:
            parts.append(f"Не перенесены, этих слов больше нет в тексте: {'; '.join(lost_labels[:4])}. "
                         "Они остались в прошлой версии клипа.")
    if timing:
        parts.append(f"Сдвиги времени у {timing} сл. не переносятся, они остались в прошлой версии клипа.")
    return {"count": len(fixes), "timing": timing, "examples": examples, "lost": lost_labels, "note": " ".join(parts)}


def from_clip(store: Any, clip_plan_id: int | None, base_words: list[dict]) -> dict:
    """Правки в субтитрах главного плана клипа относительно слов сборки."""
    if not clip_plan_id:
        return {"fixes": [], "timing": 0}
    from app.clip_groups import head_plan_id

    try:
        head = head_plan_id(store, int(clip_plan_id))
    except KeyError:
        return {"fixes": [], "timing": 0}
    subs = store.get_clip_plan_subtitles(head) or {}
    return find_fixes(base_words or [], subs.get("words") or [])


def apply_previous(store: Any, previous_clip: int | None, base_words: list[dict], new_words: list[dict],
                   notes: list[str] | None = None) -> tuple[list[dict], dict]:
    """Шаг сборки: новые слова с правками прошлой версии и итог для ``timeline``. Прежние заметки о правках заменяются."""
    found = from_clip(store, previous_clip, base_words)
    words, carried, lost = carry_fixes(base_words, found["fixes"], new_words)
    result = summary(found, carried, lost)
    if notes is not None:
        notes[:] = [n for n in notes if not str(n).startswith(("Правки субтитров", "Не перенесены", "Сдвиги времени"))]
        if result["note"]:
            notes.append(result["note"])
    return words, result
