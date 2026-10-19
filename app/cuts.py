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

MIN_CUT_SEC = 0.08
MAX_CUTS = 200
# Под-кусок короче этого не рендерим отдельно (2–3 кадра мелькания) — его
# поглощает соседний вырез.
MIN_PART_SEC = 0.1
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


def apply_cuts_to_timeline(cuts: Iterable[dict] | Iterable[tuple[float, float]]) -> CutMap:
    """Одна функция времени на всё: клиповое время до вырезов → после (или None)."""
    return CutMap(cuts)


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

    fresh = {**payload, "lines": spans(payload.get("lines"), 0.05), "words": spans(payload.get("words"), 0.02)}
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
        if got is None:
            continue
        new_start, new_end = round(got[0], 3), round(got[1], 3)
        fresh = {**layer, "start": new_start, "end": new_end}
        straddles = (end - start) - (new_end - new_start) > 1e-6
        if straddles and layer.get("keys"):
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
    """Вставки: ``at`` сдвигается; вставка, начавшаяся в вырезе, — с точки склейки."""
    if not mapper:
        return inserts
    return sorted(
        ({**ins, "at": round(mapper.clamp(float(ins["at"])), 2)} for ins in inserts or []),
        key=lambda ins: ins["at"],
    )


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
        got = mapper.range(float(word["t0"]), float(word["t1"]), 0.02)
        if got is not None:
            out.append({**word, "t0": round(got[0], 3), "t1": round(got[1], 3)})
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
        return [float(p["end_sec"]) - float(p["start_sec"]) for p in self.parts]

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
        length = max(0.0, src_end - src_start)
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
            part = {
                "id": seg.get("id"),
                "source_id": seg.get("source_id"),
                "title": seg.get("title"),
                "piece": index,
                "local_start": round(a, 3),
                "local_end": round(b, 3),
                "start_sec": round(src_start + a, 3),
                "end_sec": round(src_start + b, 3),
            }
            if seg.get("focus"):
                part["focus"] = slice_focus(seg.get("focus"), a, b)
            parts.append(part)
        piece_durations.append(round(sum(b - a for a, b in kept), 3))
        offset += length

    if not touched:
        return None
    if not parts:
        raise ValueError("вырезы закрывают весь клип — нечего рендерить")
    merged = _merge(effective)
    jump_joins = [left["piece"] == right["piece"] for left, right in zip(parts, parts[1:])]
    total_after = round(sum(float(p["end_sec"]) - float(p["start_sec"]) for p in parts), 3)
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
    from app.clip_subtitles import shift_for_transition
    from app.motion import caption_words

    try:
        payload = store.get_clip_plan_subtitles(int(plan["id"]))
    except (KeyError, TypeError, ValueError):
        return []
    if not payload or not payload.get("words"):
        return []
    transition = (plan.get("render_settings") or {}).get("transition") or {}
    overlap = float(transition.get("duration") or 0) if transition.get("type") not in (None, "cut") else 0.0
    payload = remap_subtitles(payload, cut_plan.mapper)
    return caption_words(shift_for_transition(payload, cut_plan.piece_spans(), overlap))


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
            items.append({"start": start, "end": end, "reason": f"пауза {_fmt_sec(gap)} с"})
    return items


def _filler_span(words: list[dict], first: int, last: int, duration: float, keep_pad: float) -> tuple[float, float]:
    """Вырез под слово(а) words[first..last] — вместе с тишиной вокруг, но с запасом у соседей."""
    prev_end = words[first - 1]["end"] if first > 0 else 0.0
    next_start = words[last + 1]["start"] if last + 1 < len(words) else max(duration, words[last]["end"])
    start = min(words[first]["start"], prev_end + keep_pad)
    end = max(words[last]["end"], next_start - keep_pad)
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
            if pair in FILLER_PHRASES:
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


def suggest_cuts(
    words: Any,
    duration: float,
    mode: str = "both",
    min_pause: float = 0.45,
    keep_pad: float = 0.12,
) -> dict:
    """Что вырезать по пословным таймингам клипа. Ничего не сохраняет.

    ``mode``: pauses — тишина между словами длиннее ``min_pause`` (середина,
    ``keep_pad`` тишины у каждого слова остаётся); fillers — слова-паразиты и
    повторы; both — всё вместе (пересекающиеся вырезы сливаются, причины —
    через «+»).
    """
    clean = _clean_words(words)
    duration = max(float(duration or 0.0), clean[-1]["end"] if clean else 0.0)
    keep_pad = max(0.0, float(keep_pad))
    min_pause = max(0.1, float(min_pause))
    raw: list[dict] = []
    if mode in ("pauses", "both"):
        raw += _pause_items(clean, duration, min_pause, keep_pad)
    if mode in ("fillers", "both"):
        raw += _filler_items(clean, duration, keep_pad)
    raw.sort(key=lambda it: (it["start"], it["end"]))
    items: list[dict] = []
    for item in raw:
        start, end = max(0.0, item["start"]), min(duration, item["end"])
        if end - start < MIN_CUT_SEC:
            continue
        if items and start <= items[-1]["end"] + MERGE_GAP_SEC:
            last = items[-1]
            last["end"] = max(last["end"], end)
            if item["reason"] not in last["reason"]:
                last["reason"] = f"{last['reason']} + {item['reason']}"
            continue
        items.append({"start": start, "end": end, "reason": item["reason"]})
    items = [{**it, "start": round(it["start"], 3), "end": round(it["end"], 3)} for it in items[:MAX_CUTS]]
    cuts = normalize_cuts(items)
    saved = cuts_total(cuts)
    return {
        "cuts": cuts,
        "items": items,
        "saved_sec": saved,
        "before_sec": round(duration, 3),
        "after_sec": round(max(0.0, duration - saved), 3),
    }


def plan_duration(plan: dict) -> float:
    return round(sum(max(0.0, float(s["end_sec"]) - float(s["start_sec"])) for s in plan.get("segments") or []), 3)


def suggest_for_plan(store: Any, clip_plan_id: int, mode: str, min_pause: float, keep_pad: float) -> dict:
    """Подсказки вырезов по сохранённым словам субтитров клипа (без новой транскрипции)."""
    plan = store.get_clip_plan(clip_plan_id)
    payload = store.get_clip_plan_subtitles(clip_plan_id) or {}
    words = payload.get("words") or []
    if not words:
        raise ValueError("у клипа нет пословных субтитров — сначала сделайте субтитры в редакторе")
    return suggest_cuts(words, plan_duration(plan), mode=mode, min_pause=min_pause, keep_pad=keep_pad)
