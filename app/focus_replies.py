"""Реплики внутри плана для кадрирования окном формата клипа — 9:16, 1:1, 4:5 (у 16:9 из
горизонтального исходника кадр не режется, см. app/formats.focus_needed); без OpenCV — чистая
логика, тестируется на хосте.

Почему. auto_focus давал один центр на план (план — между склейками). Проверочный прогон на
«Рике и Морти»: в длинном плане двое по краям (x≈0.4 и x≈0.72) говорят по очереди — окно
вставало между ними, в кадре рука Рика. Монтажёр в вертикали делит такой план по репликам и
«поворачивает камеру» к ответившему. Здесь:

* ``split_replies`` — план длиннее ~3 с с несколькими фразами режется на реплики по словам
  субтитров (пауза > 0.5 с или смена голоса ``speaker``);
* ``plan_units`` — что спросить у Gemini: кадр на план, а на длинном плане с репликами — кадр
  на реплику (с её текстом: «кто говорит эту фразу»), в пределах лимита кадров на запрос;
* ``apply_answers`` — ответы в точки фокуса: на склейке ``cut``, между репликами внутри плана —
  плавный переход ~0.35 с.

Время везде — секунды от начала куска (шкала ``piece``), слова — ``{word, start, end, speaker?}``.
"""

from __future__ import annotations

MIN_SHOT_SEC = 3.0  # короче — одна реплика на план, делить нечего
REPLY_PAUSE_SEC = 0.5  # пауза между словами больше — новая реплика
MIN_REPLY_SEC = 0.8  # «Ага.» на 0.3 с не дёргает камеру — приклеивается к соседней реплике
TURN_SEC = 0.35  # переход окна к ответившему внутри плана (не cut)
SAME_X = 0.06  # ответы ближе — тот же герой, перехода нет


def _f(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _words_in(words: list[dict], start: float, end: float, pad: float = 0.0) -> list[dict]:
    """Слова, начавшиеся внутри [start, end] (с запасом pad), по времени."""
    out = [w for w in words or [] if str(w.get("word") or "").strip()
           and start - pad <= _f(w.get("start")) <= end + pad]
    return sorted(out, key=lambda w: _f(w.get("start")))


def _text(words: list[dict]) -> str:
    return " ".join(str(w.get("word") or "").strip() for w in words).strip()


def split_replies(
    words: list[dict],
    start: float,
    end: float,
    *,
    min_shot: float = MIN_SHOT_SEC,
    pause: float = REPLY_PAUSE_SEC,
    min_reply: float = MIN_REPLY_SEC,
) -> list[dict]:
    """Реплики плана ``[start, end)``: ``[{start, end, text, speaker}]`` или ``[]``, если план
    короткий, без слов или фраза в нём одна (тогда кадр — как раньше, один на план).

    Граница реплики — пауза больше ``pause`` или смена ``speaker`` у слов. Слишком короткая
    реплика приклеивается к соседней (через меньшую паузу). Если все слова помечены одним
    голосом — говорящий один, план не делим.
    """
    if end - start < min_shot:
        return []
    inside = _words_in(words, start, end)
    if len(inside) < 2:
        return []
    speakers = {str(w.get("speaker") or "").strip().upper() for w in inside}
    if len(speakers) == 1 and "" not in speakers:
        return []  # метки голосов есть, и голос один

    groups: list[list[dict]] = [[inside[0]]]
    for prev, word in zip(inside, inside[1:]):
        gap = _f(word.get("start")) - _f(prev.get("end"), _f(prev.get("start")))
        changed = (word.get("speaker") or None) != (prev.get("speaker") or None)
        if gap > pause or changed:
            groups.append([word])
        else:
            groups[-1].append(word)

    def span(group: list[dict]) -> tuple[float, float]:
        a = _f(group[0].get("start"))
        return a, max(a, _f(group[-1].get("end"), a))

    # Короткие реплики — к соседу через меньшую паузу (камера не мечется на «Ага»).
    merged = True
    while merged and len(groups) > 1:
        merged = False
        for i, group in enumerate(groups):
            a, b = span(group)
            if b - a >= min_reply:
                continue
            left = a - span(groups[i - 1])[1] if i > 0 else None
            right = span(groups[i + 1])[0] - b if i + 1 < len(groups) else None
            if right is None or (left is not None and left <= right):
                groups[i - 1].extend(group)
            else:
                groups[i + 1][:0] = group
            del groups[i]
            merged = True
            break
    if len(groups) < 2:
        return []

    replies = []
    for group in groups:
        a, b = span(group)
        voices = [str(w.get("speaker") or "") for w in group if w.get("speaker")]
        replies.append({"start": round(max(start, a), 3), "end": round(min(end, b), 3), "text": _text(group),
                        "speaker": max(set(voices), key=voices.count) if voices else ""})
    return replies


def _hint(text: str, speaker: str = "") -> str:
    return (f"voice {speaker}: " if speaker else "") + text if text else ""


def plan_units(shots: list[tuple[float, float]], words: list[dict] | None, limit: int) -> list[dict]:
    """Что отправить Gemini одним запросом: ``[{shot, start, end, hint, reply}]``.

    Каждый план (до ``limit``) — минимум один кадр. Оставшиеся кадры лимита — длинным планам
    с репликами по порядку: план целиком на реплики (или целиком одним кадром, если реплики
    не влезли — половина реплик хуже одного центра).
    """
    shots = list(shots)[:max(0, limit)]
    per_shot: list[list[dict]] = []
    spare = max(0, limit) - len(shots)
    for index, (a, b) in enumerate(shots):
        replies = split_replies(words or [], a, b) if words else []
        if replies and len(replies) - 1 <= spare:
            spare -= len(replies) - 1
            per_shot.append([{"shot": index, "start": r["start"], "end": r["end"],
                              "hint": _hint(r["text"], r["speaker"]), "reply": True} for r in replies])
        else:
            hint = _text(_words_in(words or [], a, b, pad=0.2))
            per_shot.append([{"shot": index, "start": a, "end": b, "hint": hint, "reply": False}])
    return [unit for units in per_shot for unit in units]


def apply_answers(
    points: list[dict],
    shot_ranges: list[tuple[int, int]],
    units: list[dict],
    answers: list[float | None],
    *,
    turn: float = TURN_SEC,
) -> list[dict]:
    """Ответы Gemini (x на кадр) — в трек фокуса куска.

    План одним кадром — весь план на этот x (как раньше). План по репликам — точка на начало
    плана (``cut`` склейки сохраняется) и на каждую смену говорящего пара точек: старый x за
    ``turn`` секунд до реплики, новый — на её начале (плавный поворот, не cut). Реплика без
    ответа держит x плана из OpenCV; соседние реплики с одним x сливаются.
    """
    by_shot: dict[int, list[tuple[dict, float | None]]] = {}
    for unit, x in zip(units, list(answers) + [None] * max(0, len(units) - len(answers))):
        by_shot.setdefault(int(unit["shot"]), []).append((unit, x))

    out: list[dict] = []
    covered = 0
    for index, (lo, hi) in enumerate(shot_ranges):
        shot = [dict(p) for p in points[lo:hi]]
        if not shot:
            continue
        got = by_shot.get(index) or []
        cv_x = float(shot[0]["x"])
        if not got or not any(x is not None for _u, x in got):
            out.extend(shot)
            covered = hi
            continue
        if len(got) == 1 or not got[0][0].get("reply"):
            fixed = round(min(1.0, max(0.0, float(got[0][1]))), 4)
            for p in shot:
                p["x"] = fixed
            out.extend(shot)
            covered = hi
            continue
        # По репликам: x каждой (без ответа — x плана), одинаковые подряд — одна.
        turns: list[tuple[float, float]] = []
        for unit, x in got:
            value = round(min(1.0, max(0.0, float(x))), 4) if x is not None else cv_x
            if turns and abs(turns[-1][1] - value) < SAME_X:
                continue
            turns.append((float(unit["start"]), value))
        head = dict(shot[0])
        head["x"] = turns[0][1]
        track = [head]
        shot_start = float(head["t"])
        shot_end = float(points[hi]["t"]) if hi < len(points) else float(shot[-1]["t"])
        for (at, x_new), (_prev_at, x_old) in zip(turns[1:], turns):
            at = min(max(at, shot_start + 0.05), max(shot_start + 0.05, shot_end - 0.05))
            begin = max(float(track[-1]["t"]) + 0.01, at - turn)
            if begin >= at:
                continue
            y = head.get("y", 0.5)
            track.append({"t": round(begin, 3), "x": x_old, "y": y})
            track.append({"t": round(at, 3), "x": x_new, "y": y})
        out.extend(track)
        covered = hi
    out.extend(dict(p) for p in points[covered:])
    return out
