"""Перетекающие переходы между сценами моушн-клипа: слой ``transition``.

Сцена A не сменяется сценой B склейкой, а перетекает в неё (как в рекламных роликах, где «одна сцена
перетекает в другую»):

* ``reveal_from`` — из элемента/точки A растёт круг (или скруглённый прямоугольник) и раскрывает B;
* ``zoom_through`` — камера пролетает сквозь элемент A (кнопку, карточку), он заполняет кадр, а B
  вырастает внутри него из миниатюры до полного кадра;
* ``shared_element`` — элемент есть в обеих сценах: он едет и меняет размер, скругление и цвет из
  состояния A в состояние B, а остальное A уходит, B приходит вокруг;
* ``push_slide`` — A и B одной лентой: A уезжает, B въезжает встык с лёгким отъездом камеры.

Слой ничего не рисует сам: на своём отрезке ``start..end`` он собирает слои ``props.from`` (A) и
``props.to`` (B) в группы со своей матрицей, маской и прозрачностью (движок —
``frontend/src/motion/flow.ts`` + ``render.ts``, одинаково в превью и рендере). Пустые ``from`` и ``to`` —
группы по времени (``flow_groups``). Здесь — схема props, группы и подгонка времени слоёв
(инструмент ``flow_transition``, ``app/layer_tools_flow.py``).
"""

from __future__ import annotations

from typing import Any

from app.motion_fields import Choice, Color, ListOf, Num, Opt, Text

FLOW_KINDS = ("reveal_from", "zoom_through", "shared_element", "push_slide")
FLOW_SHAPES = ("circle", "rect")
FLOW_DIRECTIONS = ("left", "right", "up", "down")
FLOW_GROUP_MAX = 120
# Автогруппа (как FLOW_AUTO_SLACK в flow.ts): «кончается на стыке» / «начинается на стыке», с.
FLOW_AUTO_SLACK = 0.12
# Длина по умолчанию: раскрытие и пролёт — заметное движение, лента — быстрее.
FLOW_DEFAULT_SEC = {"reveal_from": 0.7, "zoom_through": 0.8, "shared_element": 0.7, "push_slide": 0.55}
# Слои, которые переход не трогает: рисуются как всегда (субтитры, звук, эффекты кадра, чат).
NEVER_GROUPED = ("captions", "audio", "fx", "transition", "chat")

LAYER_REF = Text("", 32)

TRANSITION: dict = {
    "kind": Choice("reveal_from", FLOW_KINDS),
    "from": ListOf(LAYER_REF, FLOW_GROUP_MAX),
    "to": ListOf(LAYER_REF, FLOW_GROUP_MAX),
    "target": Opt(LAYER_REF),
    "match": Opt(LAYER_REF),
    "ease": Opt(Text("inOutCubic", 64)),
    "shape": Opt(Choice("circle", FLOW_SHAPES)),
    "w": Opt(Num(0, 0, 4000, 1)),
    "h": Opt(Num(0, 0, 4000, 1)),
    "radius": Opt(Num(0, 0, 2000, 1)),
    "feather": Opt(Num(0, 0, 600, 1)),
    "depth": Opt(Num(0.12, 0, 0.5, 3)),
    "edge": Opt(Color(None, nullable=True)),
    "edgeWidth": Opt(Num(8, 0.5, 80, 1)),
    "fill": Opt(Color(None, nullable=True)),
    "direction": Opt(Choice("left", FLOW_DIRECTIONS)),
    "dip": Opt(Num(0.08, 0, 0.4, 3)),
    "distance": Opt(Num(1, 0.2, 1, 3)),
}

KIND_ABOUT = {
    "reveal_from": "круг из элемента A раскрывает B",
    "zoom_through": "пролёт камеры сквозь элемент A, B внутри него",
    "shared_element": "общий элемент перетекает из A в B",
    "push_slide": "A и B одной лентой, встык",
}


def groupable(layer: dict) -> bool:
    return layer.get("type") not in NEVER_GROUPED and not layer.get("behind")


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def flow_groups(layers: list[dict], start: float, end: float, exclude: str | None = None) -> tuple[list[str], list[str]]:
    """Группы по времени (как flowGroups в flow.ts): A — начались до перехода и кончаются к его концу,
    B — начинаются на его отрезке и живут дальше. Сквозные слои (фон, логотип, субтитры) — ни там, ни там."""
    a: list[str] = []
    b: list[str] = []
    for layer in layers:
        if not groupable(layer) or layer.get("id") == exclude or layer.get("hidden"):
            continue
        s, e = _f(layer.get("start")), _f(layer.get("end"))
        if s < start - 1e-6 and start - FLOW_AUTO_SLACK <= e <= end + FLOW_AUTO_SLACK:
            a.append(str(layer["id"]))
        elif start - FLOW_AUTO_SLACK <= s < end and e > end:
            b.append(str(layer["id"]))
    return a, b


def _shift_keys(keys: Any, dt: float) -> Any:
    """Ключи слоя — в его локальном времени: начало раньше на dt — ключи позже на dt (абсолютное время то же)."""
    if not isinstance(keys, dict) or abs(dt) < 1e-9:
        return keys
    out = {}
    for name, points in keys.items():
        if isinstance(points, list):
            out[name] = [{**p, "t": round(max(0.0, _f(p.get("t")) + dt), 3)} if isinstance(p, dict) else p for p in points]
        else:
            out[name] = points
    return out


def fit_timing(layers: list[dict], a_ids: list[str], b_ids: list[str], start: float, end: float,
               keep_in: bool = False) -> tuple[list[dict], list[str]]:
    """Подогнать слои к переходу: A живут ровно до конца перехода (без своей анимации выхода — уходят
    переходом), B начинаются с его начала (без анимации входа — приходят переходом; keep_in — оставить).
    Ключи B сдвигаются так, чтобы их абсолютное время не изменилось. Возвращает слои и заметки."""
    a, b = set(a_ids), set(b_ids)
    notes: list[str] = []
    out = []
    for layer in layers:
        lid = str(layer.get("id"))
        if lid in a:
            layer = {**layer, "end": round(end, 3), "out": None}
        elif lid in b:
            s = _f(layer.get("start"))
            if s > start + 1e-6:
                dt = s - start
                layer = {**layer, "start": round(start, 3), "keys": _shift_keys(layer.get("keys"), dt)}
                if layer.get("type") in ("bubbles", "counter", "carousel", "flow_diagram", "page"):
                    notes.append(f"{lid}: начало раньше на {dt:.2f} с — свои времена props (focusAt, stagger…) "
                                 "считаются от нового начала, проверь кадр")
            elif s < start - 1e-6:
                notes.append(f"{lid}: слой B виден ещё до перехода ({s:g} с < {start:g} с) — до перехода он стоит "
                             "как есть, на переходе входит с группой B")
            if not keep_in:
                layer = {**layer, "in": None}
        out.append(layer)
    return out, notes


def transition_problems(layer: dict, layers: list[dict]) -> list[str]:
    """Чего не хватает переходу, чтобы он что-то сделал (слой не отбрасываем — говорим)."""
    p = layer.get("props") or {}
    kind = p.get("kind")
    ids = {str(l.get("id")) for l in layers}
    out = []
    if kind == "shared_element" and not (p.get("target") and p.get("match")):
        out.append("shared_element: нужны target (элемент в A) и match (его пара в B)")
    if kind == "zoom_through" and not p.get("target") and not (_f(p.get("w")) > 0 and _f(p.get("h")) > 0):
        out.append("zoom_through: нужен target (элемент A, сквозь который летит камера) или область x/y + w/h")
    for name in ("target", "match"):
        if p.get(name) and p[name] not in ids:
            out.append(f"{name} {p[name]!r}: такого слоя нет")
    missing = [i for i in list(p.get("from") or []) + list(p.get("to") or []) if i not in ids]
    if missing:
        out.append(f"нет слоёв {', '.join(missing[:8])} — в группе их не будет")
    return out
