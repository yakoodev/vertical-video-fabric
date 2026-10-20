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

    def clamp(self, t: float) -> float:
        t = float(t)
        if self.old and t >= self.old[-1].offset + self.old[-1].length - 1e-3:
            return round(self.total, 3)  # «до конца клипа» (субтитры, плашка) — до нового конца
        index = self._old_index(t)
        piece = self.old[index]
        source = piece.start + max(0.0, t - piece.offset)
        target = self.match[index]
        if target is None:  # кусок удалили — к месту, где он был
            following = next((m for m in self.match[index + 1:] if m is not None), None)
            return round(following.offset if following else self.total, 3)
        inside = min(max(source, target.start), target.end)
        return round(target.offset + inside - target.start, 3)

    def __call__(self, t: float) -> float | None:
        t = float(t)
        index = self._old_index(t)
        piece, target = self.old[index], self.match[index]
        source = piece.start + max(0.0, t - piece.offset)
        if target is None or not target.start - 1e-3 <= source <= target.end + 1e-3:
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
