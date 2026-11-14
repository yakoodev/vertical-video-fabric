"""Шкалы времени клипа — одна таблица на всех инструментов.

Агент-монтажёр запутался: view_frame брал время до вырезов, contact_sheet — готового
ролика, слои и камера жили с наплывами переходов, а вырезы и set_subtitle_words — без
них; listen наплыв не учитывал (15.62 против 15.23), и вырез встал на 0.4 с раньше.
Теперь у агента ровно четыре шкалы, и каждый ответ называет свою:

* ``source`` — секунды исходника (куски ``start_sec``/``end_sec``, расшифровка, склейки
  планов, чат);
* ``piece``  — секунды от начала куска (``focus``);
* ``clip``   — секунды клипа ДО вырезов, С наплывами переходов: слои, камера, вставки,
  тишина и громкость исходника, субтитры, вырезы, view_frame, ``duration``;
* ``final``  — секунды ГОТОВОГО ролика (после вырезов и наплывов): ``finished_duration``,
  contact_sheet и listen по умолчанию.

Внутри хранится ещё «куски подряд» (``plain``, без наплывов): так лежат слова субтитров
и ``render_settings.cuts`` (их так же пишет редактор). Агенту plain не показывается —
инструменты переводят на входе и на выходе этим модулем.

Неоднозначность одна: в зоне наплыва (последние ``overlap`` секунд куска A на шкале clip)
звучат хвост A и голова B сразу. Момент оттуда без подсказки относится к следующему
куску (как ``_Joins.inverse``); подсказка — номер куска ``piece``.
"""

from __future__ import annotations

import bisect
import math
from typing import Any

SCALES = {
    "source": "секунды исходника",
    "piece": "секунды от начала куска",
    "clip": "секунды клипа ДО вырезов, с наплывами переходов (как слои, камера, субтитры get_clip, duration)",
    "final": "секунды ГОТОВОГО ролика: после вырезов и наплывов (как finished_duration)",
}


def scale_note(name: str) -> str:
    """``"clip"`` → «clip: секунды клипа ДО вырезов…» — подпись шкалы в ответе инструмента."""
    return f"{name}: {SCALES[name]}"


def frame_at(t: float, fps: float) -> int:
    """Секунда → номер ближайшего кадра на сетке ``fps`` (половина — вверх, с запасом на
    погрешность float: 15.0333…·30 = 450.99999 — это кадр 451). Одна функция на длины частей
    рендера (``part_frames``) и на края вставок (``app/montage_assets.build_inserts_args``):
    иначе вставка «на стыке» кусков открывала кадр спрятанного под ней геймплея."""
    return int(math.floor(float(t) * float(fps) + 0.5 + 1e-6))


def part_frames(length: float, fps: float) -> int:
    """Сколько кадров в отрендеренной части длиной ``length`` с (``-frames:v``, app/render_args.py)."""
    return max(1, frame_at(length, fps))


class FrameGrid:
    """Время готового ролика по плану → время в отрендеренном файле.

    Рендер режет каждую часть (кусок или под-кусок после вырезов) целым числом кадров
    (``part_frames``), а план считает точные секунды. За 13 кусков разница копится до 1–2
    кадров, и полнокадровая вставка, поставленная ровно на стык по плану, открывала по краю
    кадр-другой спрятанного под ней геймплея. Перевод: момент части ``k`` сдвигается на
    ``(кадры частей до k)/fps − (точные секунды частей до k)``. Момент ближе полукадра до
    начала части относится к ней (вставка «на стыке» с округлённым ``at`` — на стык).
    Интерфейс — как у ``app/cuts.CutMap`` (``m(t)``, ``clamp``, ``range``, ``duration``);
    пустой (``bool`` — False), если ни одна часть не сдвинута."""

    def __init__(self, parts: list[dict], transition: Any = None, jump_joins: list[bool] | None = None,
                 fps: float = 30.0):
        from app.cuts_map import _Joins
        from app.piece_speed import seg_length

        self.fps = float(fps or 30.0)
        joins = _Joins(list(parts or []), transition, jump_joins)
        self.starts = list(joins.final_starts)
        self.delta: list[float] = []
        rendered = 0.0
        for part, plain in zip(parts or [], joins.plain_starts):
            self.delta.append(rendered - plain)
            rendered += part_frames(seg_length(part), self.fps) / self.fps

    def __bool__(self) -> bool:
        return any(abs(d) > 1e-9 for d in self.delta)

    def __call__(self, t: float) -> float:
        if not self.starts:
            return float(t)
        k = max(0, bisect.bisect_right(self.starts, float(t) + 0.5 / self.fps) - 1)
        return max(0.0, float(t) + self.delta[k])

    def clamp(self, t: float) -> float:
        return self(t)

    def inside(self, t: float) -> bool:
        return False

    def range(self, start: float, end: float, min_len: float = 1e-6) -> tuple[float, float] | None:
        a, b = self(start), self(end)
        return None if b - a < min_len else (a, b)

    def duration(self, total: float) -> float:
        return max(0.0, self(total))


class ClipClock:
    """Перевод времени между шкалами одного клипа (куски, переход, вырезы)."""

    def __init__(self, segments: list[dict], transition: Any = None, cuts: Any = None, fps: float = 30.0):
        from app.cuts import _Joins, plan_cuts

        self.segments = list(segments or [])
        from app.piece_speed import seg_length

        # Длина куска на шкале клипа: со скоростью куска (app/piece_speed.py) — не секунды исходника.
        self.lengths = [seg_length(s) for s in self.segments]
        self.plain_total = sum(self.lengths)
        self.pre = _Joins(self.segments, transition)
        try:
            self.cut_plan = plan_cuts(self.segments, cuts, fps) if cuts and self.segments else None
        except ValueError:  # вырезы на весь клип — рендер откажет сам, время считаем без них
            self.cut_plan = None
        if self.cut_plan is not None:
            self.post = _Joins(self.cut_plan.parts, transition, self.cut_plan.jump_joins)
        else:
            self.post = self.pre
        self.clip_starts = list(self.pre.final_starts)

    @classmethod
    def for_plan(cls, plan: dict, fps: float = 30.0) -> "ClipClock":
        render = plan.get("render_settings") or {}
        return cls(plan.get("segments") or [], render.get("transition"), render.get("cuts"), fps)

    # ------------------------------------------------------------------ длины

    @property
    def has_cuts(self) -> bool:
        return self.cut_plan is not None

    @property
    def clip_duration(self) -> float:
        if not self.lengths:
            return 0.0
        return round(self.clip_starts[-1] + self.lengths[-1], 3)

    @property
    def final_duration(self) -> float:
        if self.cut_plan is None:
            return self.clip_duration
        parts = self.cut_plan.part_durations()
        if not parts:
            return 0.0
        return round(self.post.final_starts[-1] + parts[-1], 3)

    def clip_end(self, index: int) -> float:
        return self.clip_starts[index] + self.lengths[index]

    # ------------------------------------------------------------------ clip ↔ plain

    def pieces_at_clip(self, t: float) -> list[int]:
        """Куски, в которые попадает момент ``t`` шкалы clip (в зоне наплыва — два)."""
        out = [i for i in range(len(self.lengths)) if self.clip_starts[i] - 1e-6 <= t < self.clip_end(i) - 1e-9]
        if not out and self.lengths:
            out = [0 if t < 0 else len(self.lengths) - 1]
        return out

    def piece_at_clip(self, t: float, later: bool = True) -> int:
        found = self.pieces_at_clip(float(t))
        return found[-1] if later else found[0]

    def clip_to_plain(self, t: float, piece: int | None = None) -> float:
        """clip → куски подряд. ``piece`` — чей это момент в зоне наплыва (иначе следующего куска)."""
        if not self.lengths:
            return float(t)
        index = self.piece_at_clip(t) if piece is None else max(0, min(int(piece), len(self.lengths) - 1))
        local = min(max(0.0, float(t) - self.clip_starts[index]), self.lengths[index])
        return round(self.pre.plain_starts[index] + local, 3)

    def plain_to_clip(self, t: float) -> float:
        return round(self.pre.forward(float(t)), 3)

    def plain_piece(self, t: float) -> int:
        """Кусок, в котором лежит момент шкалы plain."""
        if not self.lengths:
            return 0
        return max(0, min(len(self.lengths) - 1, bisect.bisect_right(self.pre.plain_starts, float(t)) - 1))

    def clip_range_to_plain(self, start: float, end: float, piece: int | None = None) -> tuple[float, float]:
        """Отрезок clip → plain: начало — в куске ``piece`` (или в следующем при наплыве),
        конец — в том же куске, если он туда помещается, иначе в куске, где он лежит."""
        a = self.clip_to_plain(start, piece)
        index = self.plain_piece(a) if piece is None else int(piece)
        if self.lengths and float(end) <= self.clip_end(index) + 1e-6:
            b = self.clip_to_plain(end, index)
        else:
            b = self.clip_to_plain(end)
        return a, max(a, b)

    def plain_range_to_clip(self, start: float, end: float) -> list[dict]:
        """Отрезок plain → clip; через стык кусков — двумя частями (у каждой ``piece``)."""
        out = []
        cursor = float(start)
        end = float(end)
        while cursor < end - 1e-9:
            index = self.plain_piece(cursor)
            piece_end = self.pre.plain_starts[index] + self.lengths[index] if self.lengths else end
            b = min(end, piece_end) if index < len(self.lengths) - 1 else end
            local_a = cursor - self.pre.plain_starts[index]
            local_b = b - self.pre.plain_starts[index]
            out.append({"start": round(self.clip_starts[index] + local_a, 3),
                        "end": round(self.clip_starts[index] + local_b, 3), "piece": index})
            if b <= cursor + 1e-9:
                break
            cursor = b
        return out

    # ------------------------------------------------------------------ final

    def plain_to_final(self, t: float) -> float:
        if self.cut_plan is None:
            return self.plain_to_clip(t)
        return round(self.post.forward(self.cut_plan.mapper.clamp(float(t))), 3)

    def clip_to_final(self, t: float, piece: int | None = None) -> float:
        return self.plain_to_final(self.clip_to_plain(t, piece))

    def final_to_parts(self, t: float) -> float:
        """Готовый ролик → части подряд (после вырезов, без наплывов): так режется звук для listen."""
        return round(self.post.inverse(float(t)), 3)

    def parts_to_final(self, t: float) -> float:
        return round(self.post.forward(float(t)), 3)

    def final_to_plain(self, t: float) -> float:
        parts = self.post.inverse(float(t))
        if self.cut_plan is None:
            return round(parts, 3)
        return round(self.cut_plan.mapper.source_time(parts), 3)

    def final_to_clip(self, t: float) -> float:
        if self.cut_plan is None:
            return round(float(t), 3)
        return self.plain_to_clip(self.final_to_plain(t))

    def parts(self) -> list[dict]:
        """Что рендерится подряд: под-куски после вырезов или сами куски."""
        return list(self.cut_plan.parts) if self.cut_plan is not None else list(self.segments)
