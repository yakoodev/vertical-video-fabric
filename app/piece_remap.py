"""Правка кусков клипа → время слоёв, камеры, вставок и вырезов едет следом.

Слои и прочее монтажа задаются во времени склейки кусков (до вырезов). Подрезали
начало первого куска на 2 с — всё, что после, должно приехать на 2 с раньше, иначе
титр «НО ЕСТЬ НЮАНС» встанет не на ту фразу. Каждый момент склейки привязан к
секунде исходника своего куска и едет вместе с ним; момент из отрезанного
прижимается к краю куска (как время внутри выреза — к склейке).

``PieceMap`` повторяет интерфейс :class:`app.cuts.CutMap` (``__call__``,
``clamp``, ``range``, ``duration``), поэтому годятся те же ``remap_layers`` /
``remap_camera`` / ``remap_inserts``. Перестановку кусков не переносим —
порядок поменялся, сдвиг «на N секунд» теряет смысл (возвращаем None).
"""

from __future__ import annotations

from typing import Any


class Piece:
    __slots__ = ("key", "start", "end", "offset")

    def __init__(self, key: Any, start: float, end: float, offset: float):
        self.key, self.start, self.end, self.offset = key, start, end, offset

    @property
    def length(self) -> float:
        return self.end - self.start


def layout(pieces: list[dict]) -> list[Piece]:
    """[{id, start_sec, end_sec}] → куски с местом в склейке."""
    out, offset = [], 0.0
    for p in pieces:
        start, end = float(p["start_sec"]), float(p["end_sec"])
        if end > start:
            out.append(Piece(p.get("id"), start, end, offset))
            offset += end - start
    return out


class PieceMap:
    def __init__(self, old: list[Piece], new: list[Piece]):
        self.old, self.new = old, new
        self.total = sum(p.length for p in new)
        by_key = {p.key: p for p in new if p.key is not None}
        same_count = len(old) == len(new)
        self.match: list[Piece | None] = []
        for index, piece in enumerate(old):
            hit = by_key.get(piece.key) if piece.key is not None else None
            if hit is None and same_count:
                hit = new[index]  # кусок пересоздан копией (общий с другим клипом)
            self.match.append(hit)

    def monotone(self) -> bool:
        offsets = [m.offset for m in self.match if m is not None]
        return offsets == sorted(offsets)

    def changed(self) -> bool:
        if len(self.old) != len(self.new):
            return True
        return any(
            abs(a.start - b.start) > 1e-3 or abs(a.end - b.end) > 1e-3 or a.key != b.key
            for a, b in zip(self.old, self.new)
        )

    def __bool__(self) -> bool:
        return self.changed()

    def _old_index(self, t: float) -> int:
        for index, piece in enumerate(self.old):
            if t < piece.offset + piece.length:
                return index
        return len(self.old) - 1

    def _home(self, index: int, source: float) -> Piece | None:
        """Новый кусок, где теперь лежит момент исходника: сначала свой (тот же id),
        иначе ближайший по месту кусок, который его содержит — кусок разрезали
        надвое, и вторая половина стала новым куском (агент: «разрезал — слои убраны»)."""
        target = self.match[index]
        if target is not None and target.start - 1e-3 <= source <= target.end + 1e-3:
            return target
        holders = [p for p in self.new if p.start - 1e-3 <= source <= p.end + 1e-3]
        if not holders:
            return None
        anchor = target.offset if target is not None else self.old[index].offset
        return min(holders, key=lambda p: abs(p.offset - anchor))

    def clamp(self, t: float) -> float:
        t = float(t)
        if self.old and t >= self.old[-1].offset + self.old[-1].length - 1e-3:
            return round(self.total, 3)  # «до конца клипа» (субтитры, плашка) — до нового конца
        index = self._old_index(t)
        piece = self.old[index]
        source = piece.start + max(0.0, t - piece.offset)
        home = self._home(index, source)
        if home is not None:
            return round(home.offset + source - home.start, 3)
        target = self.match[index]
        if target is None:  # кусок удалили — к месту, где он был
            following = next((m for m in self.match[index + 1:] if m is not None), None)
            return round(following.offset if following else self.total, 3)
        inside = min(max(source, target.start), target.end)
        return round(target.offset + inside - target.start, 3)

    def __call__(self, t: float) -> float | None:
        t = float(t)
        index = self._old_index(t)
        piece = self.old[index]
        if self._home(index, piece.start + max(0.0, t - piece.offset)) is None:
            return None
        return self.clamp(t)

    def range(self, start: float, end: float, min_len: float = 1e-6) -> tuple[float, float] | None:
        a, b = self.clamp(start), self.clamp(end)
        if b - a < min_len:
            return None
        return a, b

    def duration(self, _total: float) -> float:
        return round(self.total, 3)


def remap_render(render: dict, old_pieces: list[dict], new_pieces: list[dict]) -> tuple[dict, str | None]:
    """Перенести времена монтажа на новые куски. (render, пояснение или None)."""
    from app.cuts import remap_camera, remap_inserts, remap_layers

    mapper = PieceMap(layout(old_pieces), layout(new_pieces))
    if not mapper.changed():
        return render, None
    if not mapper.monotone():
        return render, "куски переставлены — слои, камеру и вставки проверь вручную"
    fresh = dict(render)
    fresh["layers"] = remap_layers(render.get("layers") or [], mapper)
    fresh["camera"] = remap_camera(render.get("camera") or [], mapper)
    fresh["inserts"] = remap_inserts(render.get("inserts") or [], mapper)
    cuts = []
    for cut in render.get("cuts") or []:
        got = mapper.range(float(cut["start"]), float(cut["end"]), 0.05)
        if got is not None:
            cuts.append({**cut, "start": got[0], "end": got[1]})
    fresh["cuts"] = cuts
    lost = len(render.get("layers") or []) - len(fresh["layers"])
    note = "слои, камера, вставки и вырезы сдвинуты вслед за кусками"
    if lost > 0:
        note += f"; {lost} слоёв были целиком в отрезанном — убраны"
    return fresh, note


def retime_segment(store: Any, segment_id: int, start_sec: float, end_sec: float) -> dict:
    """Поменять таймкоды куска и сдвинуть монтаж всех клипов, где он стоит.

    Перетаскивание края куска в редакторе идёт сюда, мимо клип-файла
    (app/clip_spec.apply_spec) — без этого титры оставались на старых секундах."""
    from app.clip_settings import normalize_render_settings

    rows = store.db.query_all(
        "SELECT DISTINCT clip_plan_id FROM clip_plan_segments WHERE segment_id = ?", (segment_id,)
    )
    before = {}
    for row in rows:
        plan = store.get_clip_plan(int(row["clip_plan_id"]))
        before[plan["id"]] = plan
    segment = store.update_ai_segment_timecodes(segment_id, start_sec, end_sec)
    for plan_id, plan in before.items():
        stored = normalize_render_settings(plan.get("render_settings") or {})
        after = store.get_clip_plan(plan_id).get("segments") or []
        moved, _note = remap_render(stored, plan.get("segments") or [], after)
        if moved is not stored and moved != stored:
            store.set_clip_plan_render_settings(plan_id, moved)
    return segment


class TransitionMap:
    """Готовое время при старом переходе → при новом (куски те же).

    Слои, камера и вставки живут во времени ГОТОВОГО клипа: наплыв съедает
    секунды на каждой склейке. Поменяли «встык» на «наплыв» — субтитры
    сдвигались сами (они во времени кусков подряд), а титры оставались на
    старых секундах и уезжали от речи (отзыв агента-монтажёра)."""

    def __init__(self, segments: list[dict], old_transition: Any, new_transition: Any):
        from app.cuts import _Joins

        self.old = _Joins(list(segments or []), old_transition)
        self.new = _Joins(list(segments or []), new_transition)

    def __bool__(self) -> bool:
        return self.old.eaten != self.new.eaten

    def clamp(self, t: float) -> float:
        return round(self.new.forward(self.old.inverse(float(t))), 3)

    def __call__(self, t: float) -> float:
        return self.clamp(t)

    def range(self, start: float, end: float, min_len: float = 1e-6) -> tuple[float, float] | None:
        a, b = self.clamp(start), self.clamp(end)
        return None if b - a < min_len else (a, b)

    def duration(self, total: float) -> float:
        return self.clamp(total)


def remap_for_transition(render: dict, segments: list[dict], old_transition: Any) -> dict:
    """Перенести титры, камеру и вставки на новый переход (render уже с новым)."""
    from app.cuts import remap_camera, remap_inserts, remap_layers

    mapper = TransitionMap(segments, old_transition, render.get("transition"))
    if not mapper or len(segments or []) < 2:
        return render
    return {
        **render,
        "layers": remap_layers(render.get("layers") or [], mapper),
        "camera": remap_camera(render.get("camera") or [], mapper),
        "inserts": remap_inserts(render.get("inserts") or [], mapper),
    }
