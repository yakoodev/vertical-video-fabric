"""Джамп-каты: вырезать паузы и слова-паразиты внутри клипа.

Самая частая ручная работа в шортсах — выкинуть «ээ», «ну», «короче» и
полсекунды тишины между фразами (auto-editor, Submagic «remove silences»).
Здесь это делается без новой транскрипции — по сохранённым пословным
таймингам субтитров клипа.

Настройка рендера ``render.cuts`` — список ``{start, end}`` во ВРЕМЕНИ КЛИПА:
секунды склейки кусков подряд, как в редакторе и в сохранённых субтитрах
(то есть «до вырезания»). Отрезки по порядку, не пересекаются, каждый
≥ ``MIN_CUT_SEC``, не больше ``MAX_CUTS``.

Как это рендерится (``plan_cuts``): каждый кусок клипа делится на под-куски,
которые обходят вырезанное, — и клип идёт по обычному пути монтажа из
нескольких частей. Между под-кусками одного куска склейка всегда встык
(``jump_joins``) с микрофейдом звука против щелчка; переходы-эффекты —
только между исходными кусками, как раньше.

Всё, что живёт на времени клипа (слова субтитров, моушн-слои, ключи камеры,
вставки, обложка, чат), сдвигается одной функцией времени —
``apply_cuts_to_timeline(cuts)``: время после выреза сдвигается влево на
вырезанное, время внутри выреза — пропало (None).

Пустой ``cuts`` — рендер ровно прежний (те же команды ffmpeg).
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field
from typing import Any, Iterable
from app.piece_speed import drop_reversed, seg_length, seg_speed, to_source

LONG_PAUSE_SEC = 2.5
MIN_CUT_SEC = 0.08
MAX_CUTS = 200
# Под-кусок короче этого не рендерим отдельно (2–3 кадра мелькания) — его
# поглощает соседний вырез.
MIN_PART_SEC = 0.1
# Вырезы ближе этого друг к другу сливаются: между ними остался бы обрывок.
MERGE_GAP_SEC = 0.001
# Остров — то, что зритель видит одним куском между настоящими разрывами (kept_islands).
# Горизонталка Муры 31.10: 72 куска, 15 короче 3 с — «моменты из одного слова».
MIN_ISLAND_SEC = 2.5
MIN_ISLAND_WORDS = 3
# Правило слов — только для коротких: длинный тихий кусок игры обрывком не считается.
ISLAND_WORDS_MAX_SEC = 6.0
# Вырез паузы или «ээ» не длиннее этого — речь идёт дальше той же сценой, разрыва нет
# (длиннее и suggest_cuts помечает risky); столько слов и больше — вырезали сам разговор.
SOFT_CUT_SEC = LONG_PAUSE_SEC
HARD_CUT_WORDS = 3
# Соседние куски идут подряд по исходнику — стык не разрыв.
PIECE_GAP_SEC = 0.05
ISLAND_WARNINGS_MAX = 8


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


def _remap_keys(keys: dict, start: float, new_start: float, mapper: CutMap) -> dict:
    """Ключи слоя (время от начала слоя) у слоя, через который прошёл вырез."""
    out: dict = {}
    for prop, points in (keys or {}).items():
        moved: dict[float, dict] = {}
        for point in points or []:
            t = round(max(0.0, mapper.clamp(start + float(point["t"])) - new_start), 3)
            moved[t] = {**point, "t": t}
        if moved:
            out[prop] = [moved[t] for t in sorted(moved)]
    return out


TRACK_GRID_SEC = 0.05


def _densify_tracking_keys(layer: dict, mapper: Any) -> dict:
    """Ключи трека (app/tracking.py) — опоры Дугласа–Пекера, между ними бывает несколько секунд.
    Перевод времени с разрывом (вырез, стоп-кадр, правка куска) между двумя опорами исказил бы
    траекторию: опоры сошлись бы через склейку. Поэтому у краёв каждого разрыва ставятся опоры
    с интерполированным значением — после перевода слой стоит на той же точке кадра."""
    from app.promo_lint import key_value

    keys = layer.get("keys") or {}
    props = [p for p in (layer.get("tracking") or {}).get("props") or [] if keys.get(p)]
    start, end = float(layer["start"]), float(layer["end"])
    if not props or end <= start:
        return keys
    count = int((end - start) / TRACK_GRID_SEC)
    grid = [start + i * TRACK_GRID_SEC for i in range(count + 1)]
    if end - grid[-1] > 1e-6:
        grid.append(end)
    mapped = [mapper.clamp(t) for t in grid]
    bad = [abs((mapped[i + 1] - mapped[i]) - (grid[i + 1] - grid[i])) > 1e-4 for i in range(len(grid) - 1)]
    marks: set[float] = set()
    i = 0
    while i < len(bad):
        if not bad[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(bad) and bad[j + 1]:
            j += 1
        # Опоры до разрыва (одна строго до него) и после (одна строго после).
        for k in (i - 1, i, j + 1, j + 2):
            if 0 <= k < len(grid):
                marks.add(round(grid[k] - start, 3))
        i = j + 1
    if not marks:
        return keys
    out = dict(keys)
    for prop in props:
        points = sorted(keys[prop], key=lambda p: float(p["t"]))
        have = {round(float(p["t"]), 3) for p in points}
        extra = []
        for t in sorted(marks - have):
            before = [p for p in points if float(p["t"]) <= t]
            point = {"t": t, "v": round(key_value(points, t, 0.0), 4)}
            if before and before[-1].get("ease"):
                point["ease"] = before[-1]["ease"]
            extra.append(point)
        out[prop] = sorted(points + extra, key=lambda p: float(p["t"]))
    return out


PINNED_START_SEC = 0.05


def remap_layers(layers: list[dict], mapper: CutMap, min_len: float = 0.1) -> list[dict]:
    """Моушн-слои: start/end сдвигаются; слой целиком в вырезе — пропадает.

    Ключи слоя считаются от его начала — их трогаем, только если вырез прошёл
    через сам слой (или срезал его начало).
    """
    if not mapper:
        return layers
    out: list[dict] = []
    for layer in layers or []:
        start, end = float(layer["start"]), float(layer["end"])
        got = mapper.range(start, end, min_len)
        if start <= PINNED_START_SEC and (got is None or got[1] - got[0] < (end - start) / 2):
            # Титр с первой секунды (хук) — часть начала клипа, а не речи под ним:
            # вырезали начало — хук остаётся в начале той же длины.
            out.append({**layer, "start": 0.0, "end": round(end - start, 3)})
            continue
        if got is None:
            continue
        new_start, new_end = round(got[0], 3), round(got[1], 3)
        fresh = {**layer, "start": new_start, "end": new_end}
        straddles = (end - start) - (new_end - new_start) > 1e-6
        # Слой с трекингом (app/tracking.py): ключи привязаны к кадру, а не к началу слоя — едут
        # вместе с ним через любой перевод времени (стоп-кадр-вставка растягивает слой, а не режет).
        if layer.get("tracking") and layer.get("keys"):
            fresh["keys"] = _remap_keys(_densify_tracking_keys(layer, mapper), start, new_start, mapper)
        elif straddles and layer.get("keys"):
            fresh["keys"] = _remap_keys(layer["keys"], start, new_start, mapper)
        half = (new_end - new_start) / 2
        for side in ("in", "out"):
            anim = layer.get(side)
            if isinstance(anim, dict) and anim.get("type") != "none" and float(anim.get("duration") or 0) > half:
                fresh[side] = {**anim, "duration": round(max(0.05, half), 3)}
        out.append(fresh)
    return out


def remap_camera(camera: list[dict], mapper: CutMap) -> list[dict]:
    """Ключи камеры: время сдвигается; ключ в вырезе прижимается к склейке (последний побеждает)."""
    if not mapper:
        return camera
    by_t: dict[float, dict] = {}
    for key in camera or []:
        t = round(mapper.clamp(float(key["t"])), 3)
        by_t[t] = {**key, "t": t}
    return [by_t[t] for t in sorted(by_t)]


def remap_inserts(inserts: list[dict], mapper: CutMap) -> list[dict]:
    """Вставки: ``at`` сдвигается; вставка, начавшаяся в вырезе, — с точки склейки.

    Длительность тоже: вырез внутри окна вставки укорачивает её, иначе мем
    вылезает дальше задуманного и налезает на следующий титр."""
    if not mapper:
        return inserts
    out = []
    for ins in inserts or []:
        start = float(ins["at"])
        end = start + float(ins.get("duration") or 0)
        a, b = mapper.clamp(start), mapper.clamp(end)
        fresh = {**ins, "at": round(a, 2)}
        if ins.get("duration") is not None:
            fresh["duration"] = round(max(0.3, b - a), 2)
        out.append(fresh)
    return sorted(out, key=lambda ins: ins["at"])


def remap_points(items: list[dict], mapper: CutMap, key: str = "t") -> list[dict]:
    """Точечные события (сообщения чата): в вырезе — пропадают, после — сдвигаются."""
    out = []
    for item in items or []:
        t = _finite(item.get(key))
        if t is None:
            continue
        mapped = mapper(t)
        if mapped is not None:
            out.append({**item, key: round(mapped, 3)})
    return out


def remap_caption_words(words: list[dict], mapper: CutMap) -> list[dict]:
    """``comp.words`` ({t0, t1, w}) → после вырезов; вырезанные слова выпадают."""
    out = []
    for word in words or []:
        t0, t1 = float(word["t0"]), float(word["t1"])
        if mapper((t0 + t1) / 2) is None:
            continue  # середина слова вырезана — в ролике его не слышно
        a, b = mapper.clamp(t0), mapper.clamp(t1)
        out.append({**word, "t0": round(a, 3), "t1": round(max(b, a + 0.05), 3)})
    return out


def remap_motion(motion: dict | None, mapper: CutMap) -> dict | None:
    """Моушн клипа (слои, камера, а если есть — слова и чат) → время после вырезов."""
    if not motion or not mapper:
        return motion
    fresh = {
        **motion,
        "layers": remap_layers(motion.get("layers") or [], mapper),
        "camera": remap_camera(motion.get("camera") or [], mapper),
    }
    if motion.get("words"):
        fresh["words"] = remap_caption_words(motion["words"], mapper)
    if motion.get("chat"):
        fresh["chat"] = remap_points(motion["chat"], mapper)
    return fresh


# ---------------------------------------------------------------- план рендера


def _focus_x_at(points: list[tuple[float, float, bool]], local: float) -> float | None:
    """Центр кадра в момент ``local`` куска: линейно между точками, ступенькой на cut."""
    if not points:
        return None
    if local <= points[0][0]:
        return points[0][1]
    for (t0, x0, _c0), (t1, x1, cut1) in zip(points, points[1:]):
        if t0 <= local < t1:
            if cut1 or t1 - t0 <= 1e-6:
                return x0
            return x0 + (x1 - x0) * (local - t0) / (t1 - t0)
    return points[-1][1]


def slice_focus(focus: Any, a: float, b: float) -> list[dict]:
    """Дорожка кадра куска (время от начала куска) → дорожка под-куска [a, b].

    В начале под-куска — точка с тем кадром, что был в момент ``a``, внутри —
    исходные точки со сдвигом, в конце — кадр в момент ``b``: кадрирование
    под-кусков продолжает кадрирование куска, а не начинается заново.
    """
    points: list[tuple[float, float, bool]] = []
    for point in focus or []:
        try:
            points.append((float(point["t"]), float(point["x"]), bool(point.get("cut"))))
        except (KeyError, TypeError, ValueError):
            continue
    if not points:
        return []
    points.sort(key=lambda p: p[0])
    out = [{"t": 0.0, "x": round(float(_focus_x_at(points, a)), 4)}]
    for t, x, cut in points:
        if a < t < b:
            item: dict = {"t": round(t - a, 3), "x": x}
            if cut:
                item["cut"] = True
            out.append(item)
    end_x = _focus_x_at(points, b)
    if b - a > 1e-3 and end_x is not None and abs(end_x - out[-1]["x"]) > 1e-6:
        out.append({"t": round(b - a, 3), "x": round(end_x, 4)})
    return out


@dataclass
class CutPlan:
    """Как клип с вырезами рендерится: под-куски, склейки встык, функция времени."""

    parts: list[dict]
    jump_joins: list[bool]
    cuts: list[dict]
    mapper: CutMap
    piece_durations: list[float]  # длина каждого исходного куска после вырезов (0 — вырезан целиком)
    total_before: float
    total_after: float
    piece_offsets: list[float] = field(default_factory=list)

    def piece_spans(self) -> list[tuple[float, float]]:
        """Уцелевшие куски как (0, длина) — для сдвига субтитров на переходы."""
        return [(0.0, d) for d in self.piece_durations if d > 0]

    def part_durations(self) -> list[float]:
        return [seg_length(p) for p in self.parts]

    def finished_mapper(self, segments: list[dict], transition: Any) -> "FinishedCutMap":
        """Время готового клипа без вырезов → с вырезами (с настоящими переходами)."""
        return FinishedCutMap(segments, transition, self)

    def remap_cover(self, cover: dict | None) -> dict | None:
        """Обложка «кусок + секунда в нём» → под-кусок + секунда в нём."""
        if not isinstance(cover, dict) or cover.get("mode") != "frame":
            return cover
        try:
            piece = max(0, int(cover.get("piece", 0)))
            offset = max(0.0, float(cover.get("offset", 0.0)))
        except (TypeError, ValueError):
            return cover
        if not self.piece_offsets:
            return cover
        piece = min(piece, len(self.piece_offsets) - 1)
        t = self.mapper.clamp(self.piece_offsets[piece] + offset)
        elapsed = 0.0
        durations = self.part_durations()
        for index, duration in enumerate(durations):
            if t < elapsed + duration - 1e-6 or index == len(durations) - 1:
                return {**cover, "piece": index, "offset": round(max(0.0, t - elapsed), 3)}
            elapsed += duration
        return cover


def plan_cuts(segments: list[dict], cuts: Any, fps: float = 30.0) -> CutPlan | None:
    """Разложить куски клипа на под-куски в обход вырезов.

    ``segments`` — куски клипа по порядку (``start_sec``/``end_sec`` исходника,
    ``focus`` в секундах от начала куска). Время клипа — куски подряд.

    Вырезы чуть подправляются под рендер (их и видит функция времени):
    * обрывок короче ``MIN_PART_SEC`` между вырезами или у края куска
      уходит в вырез;
    * длина под-куска, который кончается вырезом, — целое число кадров
      (конец выреза не двигается, начало — меньше чем на полкадра): иначе
      картинка и звук каждой склейки расходились бы на долю кадра, а за
      десятки склеек — заметно.

    Нет вырезов внутри клипа — None (рендер ровно прежний).
    """
    raw = normalize_cuts(cuts)
    if not raw or not segments:
        return None
    frame = 1.0 / fps if fps and fps > 0 else 0.0
    spans = [(c["start"], c["end"]) for c in raw]

    parts: list[dict] = []
    effective: list[tuple[float, float]] = []
    piece_durations: list[float] = []
    offsets: list[float] = []
    offset = 0.0
    touched = False
    for index, seg in enumerate(segments):
        src_start, src_end = float(seg["start_sec"]), float(seg["end_sec"])
        length = seg_length(seg)  # шкала клипа: у куска со скоростью — не секунды исходника
        offsets.append(offset)
        # Вырезы внутри куска — в секундах от его начала.
        local = [
            (max(0.0, a - offset), min(length, b - offset))
            for a, b in spans
            if b > offset + 1e-9 and a < offset + length - 1e-9
        ]
        local = [(a, b) for a, b in local if b - a > 1e-9]
        if local:
            touched = True
        # Что остаётся: дополнение вырезов внутри [0, length].
        kept: list[list[float]] = []
        cursor = 0.0
        for a, b in local:
            if a > cursor + 1e-9:
                kept.append([cursor, a])
            cursor = max(cursor, b)
        if cursor < length - 1e-9:
            kept.append([cursor, length])
        # Обрывки — в вырез.
        kept = [k for k in kept if k[1] - k[0] >= MIN_PART_SEC - 1e-9]
        # Под-кусок, кончающийся вырезом, — целое число кадров.
        if frame:
            for k in kept:
                if k[1] < length - 1e-9:
                    frames = max(1, round((k[1] - k[0]) / frame))
                    k[1] = min(length, k[0] + frames * frame)
            kept = [k for k in kept if k[1] - k[0] >= MIN_PART_SEC - 1e-9]
        # Вырезанное в этом куске (в клиповом времени) — дополнение к оставшемуся.
        cursor = 0.0
        for a, b in kept:
            if a > cursor + 1e-9:
                effective.append((offset + cursor, offset + a))
            cursor = b
        if cursor < length - 1e-9:
            effective.append((offset + cursor, offset + length))
        for a, b in kept:
            x, y = to_source(seg, a), to_source(seg, b)  # скорость/реверс куска (app/piece_speed.py)
            part = {
                "id": seg.get("id"),
                "source_id": seg.get("source_id"),
                "title": seg.get("title"),
                "piece": index,
                "local_start": round(a, 3),
                "local_end": round(b, 3),
                "start_sec": round(min(x, y), 3),
                "end_sec": round(max(x, y), 3),
            }
            for key in ("speed", "reverse", "speed_audio", "color_fix"):
                if key in seg:
                    part[key] = seg[key]
            if seg.get("focus"):
                part["focus"] = slice_focus(seg.get("focus"), a, b)
            if seg.get("fit"):
                part["fit"] = seg["fit"]  # «шире окна» — на все части куска
            parts.append(part)
        piece_durations.append(round(sum(b - a for a, b in kept), 3))
        offset += length

    if not touched:
        return None
    if not parts:
        raise ValueError("вырезы закрывают весь клип — нечего рендерить")
    merged = _merge(effective)
    jump_joins = [left["piece"] == right["piece"] for left, right in zip(parts, parts[1:])]
    total_after = round(sum(seg_length(p) for p in parts), 3)
    return CutPlan(
        parts=parts,
        jump_joins=jump_joins,
        cuts=[{"start": round(a, 3), "end": round(b, 3)} for a, b in merged],
        mapper=apply_cuts_to_timeline(merged),
        piece_durations=piece_durations,
        total_before=round(offset, 3),
        total_after=total_after,
        piece_offsets=offsets,
    )


def plan_cuts_for_plan(plan: dict, fps: float = 30.0) -> CutPlan | None:
    """``plan_cuts`` по клипу из хранилища (куски + ``render_settings.cuts``)."""
    cuts = (plan.get("render_settings") or {}).get("cuts")
    if not cuts:
        return None
    return plan_cuts(plan.get("segments") or [], cuts, fps)


def cut_caption_words(store: Any, plan: dict, cut_plan: CutPlan) -> list[dict]:
    """Слова для слоя «субтитры» после вырезов — как ``plan_caption_words``, но
    вырезы применяются ДО сдвига на переходы (слова лежат в склейке кусков)."""
    from app.motion import caption_words

    try:
        payload = store.get_clip_plan_subtitles(int(plan["id"]))
    except (KeyError, TypeError, ValueError):
        return []
    if not payload or not payload.get("words"):
        return []
    transition = (plan.get("render_settings") or {}).get("transition")
    from app.translation import orig_comp_words

    joins = plan_joins_setting(plan)
    return (caption_words(subtitles_to_final(payload, plan.get("segments") or [], transition, cut_plan, joins))
            + orig_comp_words(payload, plan.get("segments") or [], transition, cut_plan, joins=joins))


# ---------------------------------------------------------------- подсказки


# Слова-паразиты целиком (после нормализации: строчные, без пунктуации, ё→е).
FILLER_WORDS = {"короче", "типа"}
FILLER_PHRASES = (("как", "бы"), ("в", "общем"), ("это", "самое"))
# Междометия-мычание: э, ээ, эээ…, эм, эмм, ммм.
HESITATION_RE = re.compile(r"^(э+|э+м+|м{2,}|а+м+)$")
# «Ну» — паразит, только когда стоит отдельно, между паузами.
STANDALONE_ONLY = {"ну"}
STANDALONE_GAP_SEC = 0.2
# Повтор слова («я я я») — заикание; эти повторы обычно нарочно.
EMPHATIC_REPEATS = {"да", "нет", "давай", "стоп", "тихо", "очень", "ха", "хаха", "го", "все", "ну"}
STUTTER_MAX_GAP_SEC = 1.0
# Фраза из двух слов («это самое», «в общем») — паразит, только если рядом
# пауза: «это самое главное», «в общем смысле» подряд — настоящая речь.
PHRASE_PAUSE_SEC = 0.25
_WORD_CLEAN_RE = re.compile(r"[^\w-]+", re.UNICODE)


def _norm_word(text: Any) -> str:
    word = str(text or "").lower().replace("ё", "е")
    return _WORD_CLEAN_RE.sub("", word).strip("-_")


def _clean_words(words: Any) -> list[dict]:
    out = []
    for item in words or []:
        if not isinstance(item, dict):
            continue
        s, e = _finite(item.get("start")), _finite(item.get("end"))
        if s is None or e is None or e < s:
            continue
        out.append({"start": s, "end": e, "word": str(item.get("word") or ""), "norm": _norm_word(item.get("word"))})
    out.sort(key=lambda w: w["start"])
    return out


def _fmt_sec(value: float) -> str:
    return f"{value:.1f}".replace(".", ",") if value < 10 else f"{value:.0f}"


def _pause_items(words: list[dict], duration: float, min_pause: float, keep_pad: float) -> list[dict]:
    items = []
    if not words:
        return items
    bounds = [(0.0, words[0]["start"])]
    bounds += [(a["end"], b["start"]) for a, b in zip(words, words[1:])]
    if duration > words[-1]["end"]:
        bounds.append((words[-1]["end"], duration))
    for index, (left, right) in enumerate(bounds):
        gap = right - left
        if gap <= min_pause:
            continue
        lead = 0.0 if index == 0 else keep_pad  # перед первым словом тишину не бережём
        tail = 0.0 if (index == len(bounds) - 1 and right >= duration - 1e-6 and index > 0) else keep_pad
        start, end = left + lead, right - tail
        if end - start >= MIN_CUT_SEC:
            item = {"start": start, "end": end, "reason": f"пауза {_fmt_sec(gap)} с"}
            # Длинная тишина в реакционном контенте часто и есть момент (стример молча
            # смотрит на скример, муху под Bad Apple) — предлагаем, но не отмечаем.
            # Внутренняя пауза — между двумя словами (bounds[1..len(words)-1]);
            # хвост после последнего слова может и отсутствовать в bounds.
            if gap >= LONG_PAUSE_SEC and 0 < index < len(words):
                item["reason"] += " — проверь: может, это и есть момент"
                item["risky"] = True
            items.append(item)
    return items


def _filler_span(words: list[dict], first: int, last: int, duration: float, keep_pad: float) -> tuple[float, float]:
    """Вырез под слово(а) words[first..last] — вместе с тишиной вокруг, но с запасом у соседей."""
    prev_end = words[first - 1]["end"] if first > 0 else 0.0
    next_start = words[last + 1]["start"] if last + 1 < len(words) else max(duration, words[last]["end"])
    start = min(words[first]["start"], prev_end + keep_pad)
    end = max(words[last]["end"], next_start - keep_pad)
    # Длинную паузу внутри клипа паразит не забирает: она «рискованная» и
    # предлагается отдельно — может, это и есть момент (см. _pause_items).
    if first > 0 and words[first]["start"] - prev_end >= LONG_PAUSE_SEC:
        start = words[first]["start"]
    if last + 1 < len(words) and next_start - words[last]["end"] >= LONG_PAUSE_SEC:
        end = words[last]["end"]
    return max(prev_end, start), min(next_start, end)


def _filler_items(words: list[dict], duration: float, keep_pad: float) -> list[dict]:
    items = []
    i = 0
    n = len(words)
    while i < n:
        w = words[i]
        token = w["norm"]
        # Фразы из двух слов: «как бы», «в общем», «это самое».
        if i + 1 < n:
            pair = (token, words[i + 1]["norm"])
            gap_before = w["start"] - (words[i - 1]["end"] if i > 0 else 0.0)
            gap_after = (words[i + 2]["start"] if i + 2 < n else max(duration, words[i + 1]["end"])) - words[i + 1]["end"]
            if pair in FILLER_PHRASES and max(gap_before, gap_after) >= PHRASE_PAUSE_SEC - 1e-9:
                start, end = _filler_span(words, i, i + 1, duration, keep_pad)
                items.append({"start": start, "end": end, "reason": f"слова «{w['word'].strip()} {words[i + 1]['word'].strip()}»"})
                i += 2
                continue
        hit = False
        if token in FILLER_WORDS or (token and HESITATION_RE.match(token)):
            hit = True
        elif token in STANDALONE_ONLY:
            gap_before = (w["start"] - words[i - 1]["end"]) if i > 0 else float("inf")
            gap_after = (words[i + 1]["start"] - w["end"]) if i + 1 < n else float("inf")
            hit = gap_before >= STANDALONE_GAP_SEC and gap_after >= STANDALONE_GAP_SEC
        if hit:
            start, end = _filler_span(words, i, i, duration, keep_pad)
            items.append({"start": start, "end": end, "reason": f"слово «{w['word'].strip() or token}»"})
            i += 1
            continue
        # Заикание: то же слово подряд («я я я пошёл») — оставляем последнее.
        j = i
        while (
            j + 1 < n
            and token
            and words[j + 1]["norm"] == token
            and token not in EMPHATIC_REPEATS
            and words[j + 1]["start"] - words[j]["end"] <= STUTTER_MAX_GAP_SEC
        ):
            j += 1
        if j > i:
            prev_end = words[i - 1]["end"] if i > 0 else 0.0
            start = max(prev_end, min(words[i]["start"], prev_end + keep_pad))
            end = min(words[j]["start"], max(words[j - 1]["end"], words[j]["start"] - keep_pad))
            if end - start >= MIN_CUT_SEC:
                repeats = " ".join(x["word"].strip() for x in words[i:j])
                items.append({"start": start, "end": end, "reason": f"повтор «{repeats}»"})
            i = j
            continue
        i += 1
    return [it for it in items if it["end"] - it["start"] >= MIN_CUT_SEC]


# Оборванная фраза в конце клипа (QA 6, клип 898: после панчлайна «Я обосралась, п*****.» — «Седьмой стрель.»,
# конец куска на полуслове). Речь упирается в конец клипа (последнее слово кончается ближе TAIL_EDGE_SEC), а
# после законченного предложения (точка/«!»/«?» и пауза) начинается короткий обрывок — его вырезаем до конца клипа.
TAIL_EDGE_SEC = 0.15
TAIL_SPLIT_GAP_SEC = 0.15
TAIL_FRAGMENT_MAX_SEC = 2.0
TAIL_FRAGMENT_MAX_WORDS = 4
_SENTENCE_END_RE = re.compile(r"[.!?…]+[»\"')\]]*$")


def tail_fragment(words: Any, duration: float, keep_pad: float = 0.12) -> dict | None:
    """Обрывок новой фразы в самом конце клипа → вырез ``{start, end, reason, words}`` до конца клипа; нет — None.

    Обрывок — слова после последнего конца предложения (слово с «.», «!», «?», «…» и пауза ≥ TAIL_SPLIT_GAP_SEC), не длиннее
    TAIL_FRAGMENT_MAX_SEC и TAIL_FRAGMENT_MAX_WORDS, и речь доходит до самого конца клипа (фраза не договорена).
    Тишина после предыдущей фразы (реакция) остаётся, сколько можно."""
    clean = _clean_words(words)
    if len(clean) < 2:
        return None
    total = max(float(duration or 0.0), clean[-1]["end"])
    if total - clean[-1]["end"] > TAIL_EDGE_SEC:
        return None  # клип кончается тишиной — фраза договорена
    k = len(clean) - 1
    while k > 0:
        prev = clean[k - 1]
        if clean[k]["start"] - prev["end"] >= TAIL_SPLIT_GAP_SEC and _SENTENCE_END_RE.search(prev["word"].strip()):
            break
        k -= 1
    if k <= 0:
        return None  # конца предложения перед концом клипа нет — обрывка не видно
    fragment = clean[k:]
    start_at = fragment[0]["start"]
    if total - start_at > TAIL_FRAGMENT_MAX_SEC or len(fragment) > TAIL_FRAGMENT_MAX_WORDS:
        return None
    prev_end = clean[k - 1]["end"]
    start = min(max(prev_end + max(0.0, keep_pad), start_at - 0.08), start_at - 0.02)
    start = max(prev_end, start)
    if total - start < MIN_CUT_SEC:
        return None
    text = " ".join(w["word"].strip() for w in fragment)
    return {"start": round(start, 3), "end": round(total, 3), "words": text,
            "reason": f"оборванная фраза в конце «{text}» — клип кончается на полуслове"}


def _piece_breaks(segments: Any) -> list[float]:
    """Стыки кусков (шкала «куски подряд»), где монтаж прыгает в другое место исходника."""
    out: list[float] = []
    at = 0.0
    prev: dict | None = None
    for seg in segments or []:
        if prev is not None:
            joined = abs(float(seg["start_sec"]) - float(prev["end_sec"])) <= PIECE_GAP_SEC
            if not joined or seg.get("reverse") or prev.get("reverse"):
                out.append(round(at, 3))
        at += seg_length(seg)
        prev = seg
    return out


def kept_islands(cuts: Any, words: Any, segments: Any = None, total: float | None = None) -> list[dict]:
    """Что останется после вырезов — острова по порядку, всё на шкале «куски подряд».

    Разрыв — вырез длиннее ``SOFT_CUT_SEC`` или со словами (от ``HARD_CUT_WORDS``), вырез через стык
    кусков и сам стык, если следующий кусок не продолжает предыдущий по исходнику. Вырез паузы разрывом
    не считается: фраза идёт дальше той же сценой, и панчлайн сразу за ним — не обрывок, а её конец.
    Обрывки короче ``MIN_PART_SEC`` рендер всё равно отдаёт вырезу (``plan_cuts``) — здесь их нет.

    Ответ: ``[{start, end, duration, words, text, parts, by_cut}]``; ``duration`` — без вырезов
    внутри, ``by_cut`` — острова коснулся вырез (иначе это кусок целиком, как его создали).
    """
    length = sum(seg_length(s) for s in segments or [])
    total = float(total) if total is not None else length
    spans = [(c["start"], c["end"]) for c in normalize_cuts(cuts)]
    breaks = [b for b in _piece_breaks(segments) if 0.0 < b < total]

    parts: list[tuple[float, float]] = []
    cursor = 0.0
    for a, b in spans + [(total, total)]:
        stops = [cursor] + [x for x in breaks if cursor < x < a] + [a]
        parts += [(p, q) for p, q in zip(stops, stops[1:]) if q - p >= MIN_PART_SEC]
        cursor = max(cursor, b)

    clean = _clean_words(words)
    groups: list[list[tuple[float, float]]] = []
    for part in parts:
        if groups:
            gap_from = groups[-1][-1][1]
            gap = part[0] - gap_from
            lost = sum(gap_from <= (w["start"] + w["end"]) / 2 <= part[0] for w in clean)
            hard = (gap <= 1e-9 or gap > SOFT_CUT_SEC or lost >= HARD_CUT_WORDS
                    or any(gap_from <= x <= part[0] for x in breaks))
            if not hard:
                groups[-1].append(part)
                continue
        groups.append([part])

    islands = []
    for group in groups:
        heard = [w["word"].strip() for w in clean
                 if any(a <= (w["start"] + w["end"]) / 2 <= b for a, b in group)]
        start, end = group[0][0], group[-1][1]
        by_cut = len(group) > 1 or any(abs(start - b) < 1e-6 or abs(end - a) < 1e-6 for a, b in spans)
        islands.append({"start": round(start, 3), "end": round(end, 3),
                        "duration": round(sum(b - a for a, b in group), 3), "words": len(heard),
                        "text": " ".join(heard), "parts": len(group), "by_cut": by_cut})
    return islands


def short_islands(islands: list[dict], has_words: bool = True, min_sec: float = MIN_ISLAND_SEC,
                  min_words: int = MIN_ISLAND_WORDS, by_cut_only: bool = True) -> list[dict]:
    """Обрывки среди ``kept_islands``: короче ``min_sec`` или (если субтитры есть) меньше
    ``min_words`` слов на коротком острове. Единственный остров — это весь клип, не обрывок;
    ``min_sec`` ≤ 0 — проверка выключена. ``by_cut_only`` — без кусков, созданных короткими нарочно."""
    if len(islands) < 2 or min_sec <= 0:
        return []
    words_until = max(ISLAND_WORDS_MAX_SEC, min_sec)

    def short(island: dict) -> bool:
        if island["duration"] < min_sec:
            return True
        return has_words and island["words"] < min_words and island["duration"] < words_until

    return [i for i in islands if short(i) and (i["by_cut"] or not by_cut_only)]


def merge_short_islands(cuts: Any, words: Any, segments: Any = None, total: float | None = None,
                        min_sec: float = MIN_ISLAND_SEC) -> tuple[list[dict], list[dict]]:
    """Вырезать обрывки вместе с соседними вырезами: (новые вырезы, вырезанные острова).
    Весь клип не вырезаем: если обрывки всё, что есть, самый длинный остаётся."""
    islands = kept_islands(cuts, words, segments, total)
    doomed = short_islands(islands, bool(_clean_words(words)), min_sec)
    if len(doomed) == len(islands):
        keep = max(doomed, key=lambda i: i["duration"])
        doomed = [i for i in doomed if i is not keep]
    if not doomed:
        return normalize_cuts(cuts), []
    extra = [{"start": i["start"], "end": i["end"]} for i in doomed]
    return normalize_cuts(normalize_cuts(cuts) + extra), doomed


def island_warnings(islands: list[dict], to_clip: Any = None, limit: int = ISLAND_WARNINGS_MAX) -> list[str]:
    """Обрывки — по-человечески; ``to_clip(start, end)`` — шкала, которую видит агент."""
    out = []
    for island in islands[:limit]:
        a, b = to_clip(island["start"], island["end"]) if to_clip else (island["start"], island["end"])
        heard = f"«{island['text']}»" if island["text"] else "кусок без речи"
        out.append(f"обрывок {heard} на {a:.2f}–{b:.2f} ({island['duration']:.1f} с, слов: {island['words']}) — "
                   "зритель увидит вспышку без начала и конца: сдвинь край соседнего выреза, чтобы фраза "
                   "прозвучала целиком, или вырежи её (apply_cuts с merge_short_islands: true)")
    if len(islands) > limit:
        out.append(f"и ещё обрывков: {len(islands) - limit} — смотри lint_clip, проверка fragments")
    return out


def _spare_islands(items: list[dict], words: list[dict], segments: Any, total: float) -> list[dict]:
    """Рекомендованный вырез, после которого остался бы обрывок, становится ``risky``: пауза лучше вспышки.

    Снимаем меньший из внутренних вырезов у обрывка. Вырезы по краям клипа (тишина в начале, оборванный
    хвост) соседей не соединяют — их не трогаем."""
    has_words = bool(words)
    for _round in range(len(items) + 1):
        safe = [it for it in items if not it.get("risky")]
        spare = None
        for island in short_islands(kept_islands(safe, words, segments, total), has_words):
            near = [it for it in safe if it["start"] > 0 and it["end"] < total - 1e-6
                    and (abs(it["end"] - island["start"]) < 1e-3 or abs(it["start"] - island["end"]) < 1e-3)]
            if near:
                spare = (min(near, key=lambda it: it["end"] - it["start"]), island)
                break
        if spare is None:
            return items
        item, island = spare
        item["risky"] = True
        heard = f"«{island['text']}»" if island["text"] else "кусок без речи"
        item["reason"] = f"{item['reason']} — но оставит обрывок {heard}, смотри сам"
    return items


def suggest_cuts(
    words: Any,
    duration: float,
    mode: str = "both",
    min_pause: float = 0.45,
    keep_pad: float = 0.12,
    segments: Any = None,
) -> dict:
    """Что вырезать по пословным таймингам клипа. Ничего не сохраняет.

    ``mode``: pauses — тишина между словами длиннее ``min_pause`` (середина,
    ``keep_pad`` тишины у каждого слова остаётся); fillers — слова-паразиты и
    повторы; both — всё вместе (пересекающиеся вырезы сливаются, причины —
    через «+»). Вырез, который оставил бы обрывок (``kept_islands``), в рекомендованные не идёт:
    ``segments`` — куски клипа, чтобы видеть обрывки и на их стыках.
    """
    clean = _clean_words(words)
    duration = max(float(duration or 0.0), clean[-1]["end"] if clean else 0.0)
    keep_pad = max(0.0, float(keep_pad))
    min_pause = max(0.1, float(min_pause))
    raw: list[dict] = []
    if mode in ("pauses", "both"):
        raw += _pause_items(clean, duration, min_pause, keep_pad)
        tail = tail_fragment(clean, duration, keep_pad)
        if tail:
            raw.append({k: tail[k] for k in ("start", "end", "reason")})
    if mode in ("fillers", "both"):
        raw += _filler_items(clean, duration, keep_pad)
    raw.sort(key=lambda it: (it["start"], it["end"]))

    def merged(group: list[dict]) -> list[dict]:
        out: list[dict] = []
        for item in group:
            start, end = max(0.0, item["start"]), min(duration, item["end"])
            if end - start < MIN_CUT_SEC:
                continue
            if out and start <= out[-1]["end"] + MERGE_GAP_SEC:
                last = out[-1]
                last["end"] = max(last["end"], end)
                if item["reason"] not in last["reason"]:
                    last["reason"] = f"{last['reason']} + {item['reason']}"
                continue
            out.append({"start": start, "end": end, "reason": item["reason"], **({"risky": True} if item.get("risky") else {})})
        return out

    # Рискованное (длинная пауза) с обычным не сливаем: иначе пауза «съест»
    # соседний паразит и он выпадет из рекомендованного набора. Обычные вырезы
    # остаются целиком, рискованные — только то, что вне них.
    safe = merged([it for it in raw if not it.get("risky")])
    risky: list[dict] = []
    for item in merged([it for it in raw if it.get("risky")]):
        pieces = [(item["start"], item["end"])]
        for cut in safe:
            pieces = [
                part
                for a, b in pieces
                for part in ((a, min(b, cut["start"])), (max(a, cut["end"]), b))
                if part[1] - part[0] > 1e-9
            ]
        risky += [{**item, "start": a, "end": b} for a, b in pieces if b - a >= MIN_CUT_SEC]
    items = sorted(safe + risky, key=lambda it: (it["start"], it["end"]))
    items = [{**it, "start": round(it["start"], 3), "end": round(it["end"], 3)} for it in items[:MAX_CUTS]]
    items = _spare_islands(items, clean, segments, duration)
    # Рекомендованный набор — без «рискованных» длинных пауз: их владелец отмечает сам.
    cuts = normalize_cuts([it for it in items if not it.get("risky")])
    saved = cuts_total(cuts)
    return {
        "cuts": cuts,
        "items": items,
        "saved_sec": saved,
        "before_sec": round(duration, 3),
        "after_sec": round(max(0.0, duration - saved), 3),
    }


def plan_duration(plan: dict) -> float:
    return round(sum(seg_length(s) for s in plan.get("segments") or []), 3)


def check_cuts_for_plan(plan: dict, raw: Any, fps: float = 30.0) -> tuple[list[dict], list[str]]:
    """Вырезы, которые можно сохранить в этот клип: (канонические вырезы, проблемы).

    Кроме формы (``validate_cuts``) — проверка по самому клипу, как его
    разложит рендер (``plan_cuts``): вырез за концом клипа и вырезы на весь
    клип — ошибка сразу, а не упавший рендер потом. Хвост выреза за концом
    клипа обрезается по концу.
    """
    problems = validate_cuts(raw)
    if problems:
        return [], problems
    total = plan_duration(plan)
    cuts = normalize_cuts(raw)
    for cut in cuts:
        if total > 0 and cut["start"] >= total - 1e-6:
            problems.append(
                f"cuts: вырез {cut['start']}–{cut['end']} за концом клипа ({total:.2f} с)"
            )
    if problems:
        return [], problems
    if total > 0:
        cuts = normalize_cuts([{"start": c["start"], "end": min(c["end"], total)} for c in cuts])
    try:
        plan_cuts(plan.get("segments") or [], cuts, fps)
    except ValueError as exc:
        return [], [f"cuts: {exc}"]
    return cuts, []


def suggest_for_plan(store: Any, clip_plan_id: int, mode: str, min_pause: float, keep_pad: float) -> dict:
    """Подсказки вырезов по сохранённым словам субтитров клипа (без новой транскрипции)."""
    plan = store.get_clip_plan(clip_plan_id)
    payload = store.get_clip_plan_subtitles(clip_plan_id) or {}
    words = payload.get("words") or []
    if not words:
        raise ValueError("у клипа нет пословных субтитров — сначала сделайте субтитры в редакторе")
    return suggest_cuts(words, plan_duration(plan), mode=mode, min_pause=min_pause, keep_pad=keep_pad,
                        segments=plan.get("segments") or [])


def cut_edge_warnings(cuts: list[dict], words: list[dict], total: float) -> list[str]:
    """Огрызки на стыках вырезов: слово, разрезанное пополам, и обрывок речи
    короче 0.6 с между вырезами (агент так оставил лишнее «я» перед финалом).

    ``cuts`` и ``words`` — во времени клипа до вырезов."""
    out: list[str] = []
    spans = sorted((float(c["start"]), float(c["end"])) for c in cuts)
    for word in words or []:
        a, b = float(word["start"]), float(word["end"])
        for s, e in spans:
            inside = max(0.0, min(b, e) - max(a, s))
            if 0.04 < inside < (b - a) - 0.04:
                out.append(f"вырез {s:.2f}–{e:.2f} режет слово «{word['word']}» ({a:.2f}–{b:.2f}) — сдвинь край на границу слова")
                break
    edges = [0.0] + [x for span in spans for x in span] + [total]
    for k in range(0, len(edges) - 1, 2):
        lo, hi = edges[k], edges[k + 1]
        if 0 < hi - lo < 0.6 and lo > 0 and hi < total:
            heard = [w["word"] for w in words or [] if lo <= (float(w["start"]) + float(w["end"])) / 2 <= hi]
            if heard:
                out.append(f"между вырезами {lo:.2f}–{hi:.2f} остался обрывок «{' '.join(heard)}» — вырежи его или расширь")
    tail = tail_fragment(words, total)
    if tail and not any(s <= tail["start"] + 0.05 and e >= tail["end"] - 0.05 for s, e in spans):
        # QA 6 (клип 898): «Седьмой стрель.» после панчлайна — конец на полуслове, а вырезы его не трогали.
        out.append(f"клип кончается оборванной фразой «{tail['words']}» ({tail['start']:.2f}–{tail['end']:.2f}) — "
                   f"добавь вырез {{start: {tail['start']:.2f}, end: {tail['end']:.2f}}} (suggest_cuts его предлагает)")
    return out[:6]


def cut_warnings(cuts: list[dict], words: list[dict], segments: Any, total: float, to_clip: Any = None,
                 min_sec: float = MIN_ISLAND_SEC) -> list[str]:
    """Всё, что вырезы испортили: огрызки на стыках и обрывки. Про один и тот же кусок — одно сообщение."""
    short = short_islands(kept_islands(cuts, words, segments, total), bool(_clean_words(words)), min_sec)
    edges = [w for w in cut_edge_warnings(cuts, words, total)
             if not any(f"{i['start']:.2f}–{i['end']:.2f}" in w for i in short)]
    return edges + island_warnings(short, to_clip)
