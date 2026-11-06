"""Вырезы: настройка, карта времени, субтитры (из ``app/cuts.py``)."""

from __future__ import annotations

import bisect
from typing import Any, Iterable

from app.piece_speed import drop_reversed, seg_length, seg_speed

MIN_CUT_SEC = 0.08
MAX_CUTS = 200


# Вырезы ближе этого друг к другу сливаются: между ними остался бы обрывок.
MERGE_GAP_SEC = 0.001


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


# ---------------------------------------------------------------- настройка


def _parse_cuts(raw: Any) -> tuple[list[tuple[float, float]], list[str]]:
    """Разобрать список вырезов: (годные отрезки, сообщения о негодных)."""
    problems: list[str] = []
    out: list[tuple[float, float]] = []
    if raw is None:
        return out, problems
    if not isinstance(raw, list):
        return out, ["cuts: нужен список отрезков [{start, end}]"]
    for index, item in enumerate(raw):
        where = f"cuts[{index}]"
        if not isinstance(item, dict):
            problems.append(f"{where}: нужен объект {{start, end}}")
            continue
        start, end = _finite(item.get("start")), _finite(item.get("end"))
        if start is None or end is None:
            problems.append(f"{where}: start и end должны быть числами (секунды клипа)")
            continue
        if start < 0:
            problems.append(f"{where}: start {start} < 0")
            continue
        if end <= start:
            problems.append(f"{where}: нужно start < end (пришло {start}–{end})")
            continue
        if end - start < MIN_CUT_SEC - 1e-9:
            problems.append(f"{where}: вырез {end - start:.3f} с короче {MIN_CUT_SEC} с")
            continue
        out.append((round(start, 3), round(end, 3)))
    return out, problems


def _merge(spans: Iterable[tuple[float, float]], gap: float = MERGE_GAP_SEC) -> list[tuple[float, float]]:
    merged: list[list[float]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1] + gap:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(a, b) for a, b in merged]


def normalize_cuts(raw: Any) -> list[dict]:
    """Вырезы в каноническом виде: по порядку, слитые, каждый ≥ MIN_CUT_SEC, ≤ MAX_CUTS."""
    spans, _problems = _parse_cuts(raw)
    merged = [(a, b) for a, b in _merge(spans) if b - a >= MIN_CUT_SEC - 1e-9]
    return [{"start": round(a, 3), "end": round(b, 3)} for a, b in merged[:MAX_CUTS]]


def validate_cuts(raw: Any) -> list[str]:
    """Что не так с вырезами — по-человечески (для PUT клип-файла и агентов)."""
    spans, problems = _parse_cuts(raw)
    if isinstance(raw, list) and len(raw) > MAX_CUTS:
        problems.append(f"cuts: не больше {MAX_CUTS} вырезов (пришло {len(raw)})")
    for (a0, a1), (b0, _b1) in zip(sorted(spans), sorted(spans)[1:]):
        if b0 < a1 - 1e-9:
            problems.append(f"cuts: вырезы {a0}–{a1} и {b0}–… пересекаются")
    return problems


def cuts_total(cuts: Iterable[dict]) -> float:
    return round(sum(max(0.0, float(c["end"]) - float(c["start"])) for c in cuts), 3)


# ---------------------------------------------------------------- время


class CutMap:
    """Время клипа до вырезов → время после.

    ``m(t)`` — новое время или None, если ``t`` строго внутри выреза; границы
    выреза (start и end) обе попадают в одну и ту же точку склейки.
    ``m.clamp(t)`` — то же, но время внутри выреза прижимается к склейке.
    ``m.range(a, b)`` — отрезок после вырезов или None, если от него ничего не осталось.
    """

    def __init__(self, cuts: Iterable[dict] | Iterable[tuple[float, float]]):
        spans: list[tuple[float, float]] = []
        for cut in cuts:
            if isinstance(cut, dict):
                spans.append((float(cut["start"]), float(cut["end"])))
            else:
                spans.append((float(cut[0]), float(cut[1])))
        self.spans = [(a, b) for a, b in _merge(spans, 0.0) if b > a]
        self.starts = [a for a, _b in self.spans]
        # removed_before[i] — сколько вырезано до начала выреза i.
        self.removed_before: list[float] = []
        total = 0.0
        for a, b in self.spans:
            self.removed_before.append(total)
            total += b - a
        self.removed = total

    def __bool__(self) -> bool:
        return bool(self.spans)

    def _locate(self, t: float) -> int:
        """Номер последнего выреза, начавшегося не позже t (-1 — таких нет)."""
        return bisect.bisect_right(self.starts, t) - 1

    def __call__(self, t: float) -> float | None:
        t = float(t)
        i = self._locate(t)
        if i < 0:
            return t
        a, b = self.spans[i]
        if a < t < b:
            return None
        if t <= a:  # ровно на начале выреза
            return a - self.removed_before[i]
        return t - self.removed_before[i] - (b - a)

    def clamp(self, t: float) -> float:
        t = float(t)
        mapped = self(t)
        if mapped is not None:
            return mapped
        i = self._locate(t)
        return self.spans[i][0] - self.removed_before[i]

    def inside(self, t: float) -> bool:
        return self(t) is None

    def range(self, start: float, end: float, min_len: float = 1e-6) -> tuple[float, float] | None:
        a, b = self.clamp(start), self.clamp(end)
        if b - a < min_len:
            return None
        return a, b

    def duration(self, total: float) -> float:
        """Длина клипа длиной ``total`` после вырезов."""
        return max(0.0, self.clamp(total))

    def source_time(self, t: float) -> float:
        """Обратно: время готового клипа → время до вырезов (точка сразу после склейки)."""
        t = float(t)
        for a, b in self.spans:
            if t >= a:
                t += b - a
            else:
                break
        return t


def apply_cuts_to_timeline(cuts: Iterable[dict] | Iterable[tuple[float, float]]) -> CutMap:
    """Одна функция времени на всё: клиповое время до вырезов → после (или None)."""
    return CutMap(cuts)


class _Joins:
    """Склейка частей подряд → готовый клип: каждая склейка съедает свой overlap.

    ``forward`` — время «части подряд» → время готового клипа (как
    ``shift_for_transition``); ``inverse`` — обратно (момент внутри наплыва
    относится к следующей части).
    """

    def __init__(self, parts: list[dict], transition: Any, jump_joins: list[bool] | None = None):
        from app.transitions import part_overlaps

        overlaps = part_overlaps(parts, transition, jump_joins) if parts else []
        self.plain_starts: list[float] = []
        self.eaten: list[float] = []
        plain = eaten = 0.0
        for index, part in enumerate(parts):
            self.plain_starts.append(plain)
            self.eaten.append(eaten)
            plain += seg_length(part)
            eaten += overlaps[index] if index < len(overlaps) else 0.0
        self.final_starts = [p - e for p, e in zip(self.plain_starts, self.eaten)]

    def forward(self, t: float) -> float:
        if not self.plain_starts:
            return t
        k = max(0, bisect.bisect_right(self.plain_starts, t) - 1)
        return max(0.0, t - self.eaten[k])

    def inverse(self, t: float) -> float:
        if not self.final_starts:
            return t
        k = max(0, bisect.bisect_right(self.final_starts, t) - 1)
        return t + self.eaten[k]


class FinishedCutMap:
    """Время ГОТОВОГО клипа без вырезов → время готового клипа с вырезами.

    Слои, камера, чат и вставки живут во времени готового клипа (после
    переходов), а вырезы — во времени кусков подряд. Поэтому: готовое → подряд
    (переходы до вырезов), вырезы, подряд → готовое (настоящие overlap после
    вырезов: короткий под-кусок урезает наплыв, джамп-каты встык). Интерфейс —
    как у ``CutMap`` (``m(t)``, ``clamp``, ``range``, ``duration``), так что все
    ``remap_*`` работают с ним без изменений.
    """

    def __init__(self, segments: list[dict], transition: Any, cut_plan: "CutPlan"):
        self.pre = _Joins(list(segments or []), transition)
        self.post = _Joins(cut_plan.parts, transition, cut_plan.jump_joins)
        self.cut = cut_plan.mapper

    def __bool__(self) -> bool:
        return True

    def __call__(self, t: float) -> float | None:
        mapped = self.cut(self.pre.inverse(float(t)))
        return None if mapped is None else self.post.forward(mapped)

    def clamp(self, t: float) -> float:
        return self.post.forward(self.cut.clamp(self.pre.inverse(float(t))))

    def inside(self, t: float) -> bool:
        return self(t) is None

    def range(self, start: float, end: float, min_len: float = 1e-6) -> tuple[float, float] | None:
        a, b = self.clamp(start), self.clamp(end)
        if b - a < min_len:
            return None
        return a, b

    def duration(self, total: float) -> float:
        return max(0.0, self.clamp(total))


def subtitles_to_final(payload: dict, segments: list[dict], transition: Any, cut_plan: "CutPlan | None" = None,
                       joins: Any = None) -> dict:
    """Субтитры клипа (куски подряд, до вырезов) → время готового клипа.

    Сначала вырезы, потом J/L (``joins`` — ``render_settings.audio.joins``: субтитры идут за
    звуком, ``app.audio_edit.heard_subtitles``), потом сдвиг на НАСТОЯЩИЕ overlap склеек — те же,
    что спланирует рендер (``part_overlaps`` по рендерящимся частям и дыркам в исходнике): наплыв
    урезан до 40% соседней части, «умная» склейка соседних кусков исходника и джамп-каты — встык.
    """
    from app.clip_subtitles import shift_for_transition
    from app.transitions import part_overlaps
    from app.voice_bank import with_speaker_labels

    payload = with_speaker_labels(payload)
    if cut_plan is not None:
        payload = remap_subtitles(payload, cut_plan.mapper)
        parts, jumps = cut_plan.parts, cut_plan.jump_joins
    else:
        parts, jumps = list(segments or []), None
    overlaps = part_overlaps(parts, transition, jumps)
    if joins:
        from app.audio_edit import heard_subtitles

        payload = heard_subtitles(payload, parts, list(segments or []), joins, overlaps, jumps)
    # Развёрнутые куски (реверс) — без субтитров: речь задом не читается.
    payload = drop_reversed(payload, parts)
    spans = [(float(p["start_sec"]), float(p["end_sec"]), seg_speed(p)) for p in parts]
    return shift_for_transition(payload, spans, overlaps)


def plan_joins_setting(plan: dict | None) -> list[dict]:
    """J/L клипа (``render_settings.audio.joins``) — для ``subtitles_to_final``."""
    from app.audio_edit import normalize_audio

    render = (plan or {}).get("render_settings") or {}
    return normalize_audio(render.get("audio") if isinstance(render, dict) else None).get("joins") or []


# ---------------------------------------------------------------- что сдвигается


def remap_subtitles(payload: dict, mapper: CutMap) -> dict:
    """Строки и слова субтитров клипа → время после вырезов (вырезанное выпадает)."""
    if not mapper or not isinstance(payload, dict):
        return payload

    def spans(items: Any, min_len: float) -> list[dict]:
        out = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            s, e = _finite(item.get("start")), _finite(item.get("end"))
            if s is None or e is None:
                continue
            got = mapper.range(s, e, min_len)
            if got is None:
                continue
            out.append({**item, "start": round(got[0], 3), "end": round(got[1], 3)})
        return out

    def words_kept(items: Any) -> list[tuple[dict, dict]]:
        """(исходное слово, слово после вырезов). Слово живёт, если не вырезана его
        середина: тайминги Whisper неточные, и слово, лишь краем торчащее из выреза,
        раньше оставалось на экране, хотя в ролике его уже не слышно."""
        out = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            s, e = _finite(item.get("start")), _finite(item.get("end"))
            if s is None or e is None or mapper((s + e) / 2) is None:
                continue
            a, b = mapper.clamp(s), mapper.clamp(e)
            out.append((item, {**item, "start": round(a, 3), "end": round(max(b, a + 0.05), 3)}))
        return out

    kept = words_kept(payload.get("words"))
    source_lines = [l for l in payload.get("lines") or [] if isinstance(l, dict)
                    and _finite(l.get("start")) is not None and _finite(l.get("end")) is not None]

    def owner(word: Any) -> dict | None:
        """Строка слова: та, где его середина (из перекрывающихся — начавшаяся позже)."""
        if not isinstance(word, dict) or _finite(word.get("start")) is None or _finite(word.get("end")) is None:
            return None
        mid = (float(word["start"]) + float(word["end"])) / 2
        hits = [l for l in source_lines if float(l["start"]) - 0.001 <= mid <= float(l["end"]) + 0.001]
        return max(hits, key=lambda l: float(l["start"])) if hits else None

    lines = []
    for line in spans(payload.get("lines"), 0.05):
        original = next(
            (l for l in payload.get("lines") or [] if isinstance(l, dict) and l.get("text") == line.get("text")
             and mapper.range(float(l["start"]), float(l["end"]), 0.05) == (line["start"], line["end"])),
            None,
        )
        if original is not None and (line["end"] - line["start"]) < float(original["end"]) - float(original["start"]) - 1e-3:
            # Строку задел вырез — текст только из прозвучавших слов.
            all_words = [w for w in payload.get("words") or [] if owner(w) is original]
            inside = [new for old, new in kept if owner(old) is original]
            if len(inside) == len(all_words):
                lines.append(line)  # ни одно слово строки не вырезано — текст (и правки) как был
                continue
            if not inside:
                continue
            line = {**line, "text": " ".join(str(w.get("word") or "").strip() for w in inside).strip(),
                    "start": min(w["start"] for w in inside), "end": max(w["end"] for w in inside)}
        lines.append(line)
    fresh = {**payload, "lines": lines, "words": [new for _old, new in kept]}
    if _finite(payload.get("duration")) is not None:
        fresh["duration"] = round(mapper.duration(float(payload["duration"])), 3)
    return fresh
