"""Вырезы: план рендера с вырезами (из ``app/cuts.py``)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.piece_speed import seg_length, to_source
from app.cuts_map import (
    _merge, normalize_cuts, CutMap, apply_cuts_to_timeline, FinishedCutMap, subtitles_to_final, plan_joins_setting
)

# Под-кусок короче этого не рендерим отдельно (2–3 кадра мелькания) — его
# поглощает соседний вырез.
MIN_PART_SEC = 0.1


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
