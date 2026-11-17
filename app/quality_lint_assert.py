"""Проверки-утверждения клипа (``assertions``): что должно быть правдой в плане — сверяется при каждом lint_clip.

Файл клипа ``<data>/clip_checks/<id>.json`` — ``{"assertions": [...]}`` (пишет lint_clip с
``save_assertions``, владелец или агент — руками). Утверждение — объект или строка по-русски:

* ``{"appear": "X", "by": 2.0}`` / «X появился к 2.0» — элемент X полностью вошёл к секунде t;
* ``{"before": ["A", "B"]}`` / «A раньше B» — A появляется раньше B;
* ``{"inside": "X"}`` / «X не выходит за кадр» — рамка X всё время в кадре (``margin`` px — с полем);
* ``{"moving": true, "from": 0, "to": 10, "max_still": 3}`` / «сцена не стоит» (или «сцена не стоит 0–10»)
  — в отрезке нет окна без монтажных событий дольше ``max_still`` с.

Элемент — id слоя, его name или кусок текста (без регистра). Время — шкала clip (как слои);
``"final": true`` — секунды готового ролика. Нарушение — P1, непонятное утверждение — P2.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.quality_lint_base import _finding, _num, _short
from app.quality_lint_delivery import (
    DEAD_SEC, event_spans, frame_size, in_span, layer_label, layer_rect, plan_gaps, visual_layers,
)

MAX_ASSERTIONS = 40
NUM = r"(-?\d+(?:[.,]\d+)?)"
_RE_APPEAR = re.compile(rf"^(.+?)\s+(?:появил(?:ся|ась|ось|ись)|виден|видна|видно|на экране)\s+(?:к|до)\s+{NUM}\s*(?:с|сек)?\.?$", re.I)
_RE_BEFORE = re.compile(r"^(.+?)\s+раньше\s+(.+?)\.?$", re.I)
_RE_INSIDE = re.compile(r"^(.+?)\s+не\s+выходит\s+за\s+(?:кадр|край|рамку)\.?$", re.I)
_RE_MOVING = re.compile(rf"^сцена\s+не\s+стоит(?:\s+{NUM}\s*[-–—]\s*{NUM})?\s*(?:с)?\.?$", re.I)


def checks_path(clip_plan_id: int) -> Path:
    from app.settings import settings

    return Path(settings.data_dir) / "clip_checks" / f"{int(clip_plan_id)}.json"


def load_assertions(clip_plan_id: int) -> list:
    path = checks_path(clip_plan_id)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = data.get("assertions") if isinstance(data, dict) else data
    return list(items)[:MAX_ASSERTIONS] if isinstance(items, list) else []


def save_assertions(clip_plan_id: int, items: list) -> Path:
    path = checks_path(clip_plan_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"assertions": list(items)[:MAX_ASSERTIONS]}, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    return path


def parse_assertion(raw: Any) -> dict | None:
    """Строка или объект → {kind, ...}; None — не понял."""
    if isinstance(raw, dict):
        final = bool(raw.get("final"))
        if raw.get("appear") is not None and raw.get("by") is not None:
            return {"kind": "appear", "x": str(raw["appear"]), "by": _num(raw.get("by")), "final": final}
        pair = raw.get("before")
        if isinstance(pair, list) and len(pair) == 2:
            return {"kind": "before", "a": str(pair[0]), "b": str(pair[1])}
        if raw.get("first") is not None and raw.get("then") is not None:
            return {"kind": "before", "a": str(raw["first"]), "b": str(raw["then"])}
        if raw.get("inside") is not None:
            return {"kind": "inside", "x": str(raw["inside"]), "margin": _num(raw.get("margin"), 0.0)}
        if raw.get("moving") or raw.get("not_static"):
            return {"kind": "moving", "from": raw.get("from"), "to": raw.get("to"),
                    "max_still": _num(raw.get("max_still"), DEAD_SEC), "final": final}
        return None
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = " ".join(raw.split())
    if m := _RE_MOVING.match(text):
        return {"kind": "moving", "from": _num(m.group(1).replace(",", ".")) if m.group(1) else None,
                "to": _num(m.group(2).replace(",", ".")) if m.group(2) else None, "max_still": DEAD_SEC, "final": False}
    if m := _RE_APPEAR.match(text):
        return {"kind": "appear", "x": m.group(1).strip(" «»\"'"), "by": _num(m.group(2).replace(",", ".")),
                "final": False}
    if m := _RE_INSIDE.match(text):
        return {"kind": "inside", "x": m.group(1).strip(" «»\"'"), "margin": 0.0}
    if m := _RE_BEFORE.match(text):
        return {"kind": "before", "a": m.group(1).strip(" «»\"'"), "b": m.group(2).strip(" «»\"'")}
    return None


def find_layer(layers: list[dict], ref: str) -> dict | None:
    """Слой по id, name или куску текста (без регистра); несколько — самый ранний."""
    key = ref.strip().lower()
    if not key:
        return None
    for test in (lambda l: str(l.get("id") or "").lower() == key,
                 lambda l: str(l.get("name") or "").lower() == key,
                 lambda l: key in str(((l.get("props") or {}).get("text") or "")).lower()
                 or key in str(l.get("name") or "").lower()):
        hits = [l for l in layers if test(l)]
        if hits:
            return min(hits, key=lambda l: _num(l.get("start")))
    return None


def _appear_at(layer: dict) -> float:
    return _num(layer.get("start")) + in_span(layer)


def assertion_findings(items: list, rs: dict, clock: Any) -> tuple[list[dict], dict]:
    layers = visual_layers(rs)
    out: list[dict] = []
    passed = 0
    for raw in items[:MAX_ASSERTIONS]:
        spec = parse_assertion(raw)
        label = _short(raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False), 60)
        if spec is None:
            out.append(_finding("assertions", "info", f"не понял проверку «{label}»",
                                "формат: «X появился к 2.0», «A раньше B», «X не выходит за кадр», «сцена не стоит 0–10» "
                                "или {appear, by} / {before: [A, B]} / {inside} / {moving, from, to}",
                                priority="P2", where="проверки клипа"))
            continue
        kind = spec["kind"]
        if kind == "appear":
            layer = find_layer(layers, spec["x"])
            by = clock.final_to_clip(spec["by"]) if spec["final"] else spec["by"]
            if layer is None:
                out.append(_finding("assertions", "warn", f"«{label}»: элемента «{spec['x']}» нет в клипе",
                                    "добавь элемент или поправь проверку", t=by, priority="P1", where="проверки клипа"))
                continue
            at = _appear_at(layer)
            if at > by + 1e-3:
                out.append(_finding("assertions", "warn", f"«{label}»: {layer_label(layer)} полностью виден только на "
                                                          f"{at:.2f} с — позже {by:g} с на {at - by:.2f} с",
                                    f"start раньше или вход короче: start ≤ {max(0.0, by - in_span(layer)):.2f}",
                                    t=by, layer=str(layer.get("id") or ""), priority="P1", where=layer_label(layer)))
            else:
                passed += 1
        elif kind == "before":
            a, b = find_layer(layers, spec["a"]), find_layer(layers, spec["b"])
            missing = [r for r, l in ((spec["a"], a), (spec["b"], b)) if l is None]
            if missing:
                out.append(_finding("assertions", "warn", f"«{label}»: нет элемента {', '.join(missing)}",
                                    "добавь элемент или поправь проверку", priority="P1", where="проверки клипа"))
                continue
            if _num(a.get("start")) >= _num(b.get("start")) - 1e-3:
                out.append(_finding("assertions", "warn", f"«{label}»: {layer_label(a)} появляется на "
                                                          f"{_num(a.get('start')):.2f} с — не раньше {layer_label(b)} "
                                                          f"({_num(b.get('start')):.2f} с)",
                                    f"поменяй порядок: start {a.get('id')} < {_num(b.get('start')):.2f}",
                                    t=_num(b.get("start")), layer=str(a.get("id") or ""), priority="P1",
                                    where=layer_label(a)))
            else:
                passed += 1
        elif kind == "inside":
            layer = find_layer(layers, spec["x"])
            if layer is None:
                out.append(_finding("assertions", "warn", f"«{label}»: элемента «{spec['x']}» нет в клипе",
                                    "добавь элемент или поправь проверку", priority="P1", where="проверки клипа"))
                continue
            worst = _overflow(layer, rs.get("camera"), spec["margin"])
            if worst:
                over, side, t = worst
                out.append(_finding("assertions", "warn", f"«{label}»: {layer_label(layer)} выходит за кадр {side} "
                                                          f"на {over:.0f} px",
                                    "сдвинь к центру, уменьши scale/size или maxWidth", t=t,
                                    layer=str(layer.get("id") or ""), priority="P1", where=layer_label(layer)))
            else:
                passed += 1
        else:
            lo = spec["from"]
            hi = spec["to"]
            final_lo = 0.0 if lo is None else (_num(lo) if spec["final"] else clock.clip_to_final(_num(lo)))
            final_hi = clock.final_duration if hi is None else (_num(hi) if spec["final"] else clock.clip_to_final(_num(hi)))
            gaps = plan_gaps(event_spans(rs, clock, clock.clip_duration), clock, clock.final_duration,
                             min_sec=max(0.5, spec["max_still"]))
            bad = [(max(a, final_lo), min(b, final_hi)) for a, b in gaps
                   if min(b, final_hi) - max(a, final_lo) >= max(0.5, spec["max_still"])]
            if bad:
                a, b = bad[0]
                more = f" (и ещё {len(bad) - 1})" if len(bad) > 1 else ""
                out.append(_finding("assertions", "warn", f"«{label}»: сцена стоит {b - a:.1f} с без событий{more}",
                                    "склейка, наезд камеры, вставка или новый элемент каждые 2–3 с",
                                    t=clock.final_to_clip(a), until=clock.final_to_clip(b), priority="P1",
                                    where="сцена"))
            else:
                passed += 1
    return out, {"total": min(len(items), MAX_ASSERTIONS), "passed": passed}


def _overflow(layer: dict, camera: Any, margin: float) -> tuple[float, str, float] | None:
    start, end = _num(layer.get("start")), _num(layer.get("end"))
    steps = max(1, min(120, int((end - start) / 0.1)))
    worst: tuple[float, str, float] | None = None
    W, H = frame_size()
    for k in range(steps + 1):
        t = min(end - 1e-3, start + (end - start) * k / steps)
        rect = layer_rect(layer, t, camera)
        if not rect:
            return None
        x0, y0, x1, y1 = rect
        for over, side in ((margin - x0, "слева"), (x1 - (W - margin), "справа"), (margin - y0, "сверху"),
                           (y1 - (H - margin), "снизу")):
            if over > 2 and (worst is None or over > worst[0]):
                worst = (over, side, round(t, 2))
    return worst
