"""Подсветка слов субтитров (``highlightWords``, ``emoji`` слоя captions) — по слову, а не по номеру.

Метка слова — номер ``i`` в словах клипа (``comp.words``). Агент стримов: после
``set_subtitle_words`` номера съезжали, и красным горело чужое слово — подсветку
приходилось пересчитывать руками. Теперь у метки есть якорь — текст слова ``w`` и его
начало ``t`` (секунды «кусками подряд», как хранятся субтитры), и при любой перезаписи
слов (set_subtitle_words, generate_subtitles, правка строк) номер ищется заново: то же
слово ближе всего по времени. Не нашлось — метка снимается (а не красит чужое).

Ещё два входа: агент может задать метку словом (``{text, nth?, color}`` вместо ``i``),
а рендер с вырезами — пересчитать номера под слова, оставшиеся после вырезов (иначе
номер указывал бы на слово, сдвинутое вырезом).
"""

from __future__ import annotations

import re
from typing import Any, Callable

MARK_KEYS = ("highlightWords", "emoji")
REBIND_WINDOW_SEC = 1.5


def norm(text: Any) -> str:
    return re.sub(r"[^\wё]+", "", str(text or "").lower().replace("ё", "е"))


def comp_words(words: Any) -> list[dict]:
    """Слова в том порядке, в каком их номерует слой (как ``app.motion.caption_words``)."""
    out = []
    for item in words if isinstance(words, list) else []:
        if not isinstance(item, dict) or not str(item.get("word") or "").strip():
            continue
        try:
            start = float(item["start"])
            float(item["end"])
        except (KeyError, TypeError, ValueError):
            continue
        out.append({"w": str(item["word"]).strip(), "t": start})
    out.sort(key=lambda w: w["t"])
    return out


def _find(words: list[dict], text: str, t: float | None, window: float) -> int | None:
    best, best_gap = None, None
    for index, word in enumerate(words):
        if norm(word["w"]) != text:
            continue
        gap = abs(word["t"] - t) if t is not None else 0.0
        if t is not None and gap > window:
            continue
        if best_gap is None or gap < best_gap:
            best, best_gap = index, gap
    return best


def rebind(marks: Any, old: list[dict], new: list[dict], time_map: Callable[[float], float | None] | None = None,
           window: float = REBIND_WINDOW_SEC) -> tuple[list[dict], int]:
    """Метки под новые слова: (метки, сколько снято). ``old``/``new`` — ``comp_words``;
    ``time_map`` — как время старых слов переходит на новые (вырезы)."""
    out, lost = [], 0
    for mark in marks if isinstance(marks, list) else []:
        if not isinstance(mark, dict):
            continue
        text, t = norm(mark.get("w")), mark.get("t")
        try:
            anchor = old[int(mark.get("i"))]
        except (TypeError, ValueError, IndexError):
            anchor = None
        # Номер в прежних словах надёжнее всего, если там то же слово (или якоря нет).
        if anchor is not None and (not text or norm(anchor["w"]) == text):
            text, t = norm(anchor["w"]), anchor["t"]
        elif not text:
            lost += 1
            continue
        t = float(t) if isinstance(t, (int, float)) else None
        if t is not None and time_map is not None:
            t = time_map(t)
            if t is None:  # слово целиком в вырезе
                lost += 1
                continue
        index = _find(new, text, t, window)
        if index is None:
            lost += 1
            continue
        out.append({**mark, "i": index, "w": new[index]["w"], "t": round(new[index]["t"], 3)})
    return out, lost


def resolve_text_marks(props: dict, words: list[dict]) -> tuple[dict, list[str]]:
    """Метки, заданные словом (``{text, nth?, color}`` или ``{word: …}``), → номер ``i`` и якорь;
    у меток с ``i`` дописывается якорь. Возвращает (props, проблемы)."""
    problems: list[str] = []
    out = dict(props)
    for key in MARK_KEYS:
        marks = props.get(key)
        if not isinstance(marks, list):
            continue
        fixed = []
        for n, mark in enumerate(marks):
            if not isinstance(mark, dict):
                fixed.append(mark)
                continue
            mark = dict(mark)
            text = mark.pop("text", None) or mark.pop("word", None)
            nth = mark.pop("nth", None)
            if text is not None and mark.get("i") is None:
                want, count, seen = norm(text), int(nth or 1), 0
                index = None
                for k, word in enumerate(words):
                    if want and norm(word["w"]) == want:
                        seen += 1
                        if seen == count:
                            index = k
                            break
                if index is None:
                    problems.append(f"{key}[{n}]: слова «{text}» (вхождение {int(nth or 1)}) нет в субтитрах")
                    continue
                mark["i"] = index
            try:
                anchor = words[int(mark.get("i"))]
                mark["w"], mark["t"] = anchor["w"], round(anchor["t"], 3)
            except (TypeError, ValueError, IndexError):
                pass
            fixed.append(mark)
        out[key] = fixed
    return out, problems


def rebind_layers(layers: list[dict], old: list[dict], new: list[dict],
                  time_map: Callable[[float], float | None] | None = None) -> tuple[list[dict], int]:
    """Все слои captions: метки под новые слова. (слои, сколько меток снято)."""
    lost_total = 0
    out = []
    for layer in layers:
        props = layer.get("props") if isinstance(layer.get("props"), dict) else None
        if layer.get("type") != "captions" or not props or not any(props.get(k) for k in MARK_KEYS):
            out.append(layer)
            continue
        props = dict(props)
        for key in MARK_KEYS:
            if props.get(key):
                props[key], lost = rebind(props[key], old, new, time_map)
                lost_total += lost
        out.append({**layer, "props": props})
    return out, lost_total


def rebind_plan_marks(store: Any, clip_plan_id: int, old_words: Any, new_words: Any) -> dict | None:
    """После перезаписи слов субтитров клипа — метки его слоёв captions на те же слова.

    Снимок для Ctrl+Z делает вызывающий. None — меток нет, нечего двигать."""
    plan = store.get_clip_plan(clip_plan_id, include_segments=False)
    render = dict(plan.get("render_settings") or {})
    layers = render.get("layers") or []
    if not any(l.get("type") == "captions" and any((l.get("props") or {}).get(k) for k in MARK_KEYS) for l in layers):
        return None
    old, new = comp_words(old_words), comp_words(new_words)
    fresh, lost = rebind_layers(layers, old, new)
    if fresh == layers:
        return None
    store.set_clip_plan_render_settings(clip_plan_id, {**render, "layers": fresh})
    kept = sum(len((l.get("props") or {}).get("highlightWords") or []) for l in fresh if l.get("type") == "captions")
    return {"kept": kept, "lost": lost,
            "note": "подсветка слов (highlightWords) переставлена на те же слова по тексту и времени"
                    + (f"; {lost} меток сняты — их слов больше нет" if lost else "")}
