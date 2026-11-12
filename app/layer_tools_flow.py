"""Инструмент ``flow_transition``: перетекающий переход между сценами моушн-клипа (слой ``transition``).

Схема и группы — ``app/motion_flow.py``, движок — ``frontend/src/motion/flow.ts``. Входит в реестр через
``app/layer_tools.py``.
"""

from __future__ import annotations

from app import motion
from app.layer_lint import lint_layers
from app.layer_tools_reference import motion_enums
from app.motion_flow import (
    FLOW_DEFAULT_SEC, FLOW_DIRECTIONS, FLOW_KINDS, FLOW_SHAPES, KIND_ABOUT, fit_timing, flow_groups, groupable,
    transition_problems,
)
from app.tool_core import _clip_duration, _current_render, _layers_of, _num, _obj, _save_render, Tool, ToolContext, ToolError

_KIND_ALIASES = {
    "reveal": "reveal_from", "circle": "reveal_from", "circle_reveal": "reveal_from", "iris": "reveal_from",
    "zoom": "zoom_through", "zoom_in": "zoom_through", "through": "zoom_through",
    "shared": "shared_element", "morph": "shared_element", "match_cut": "shared_element",
    "push": "push_slide", "slide": "push_slide",
}


def _ids(ctx_layers: list[dict], ids, prefix, what: str) -> list[str]:
    by_id = {str(l.get("id")): l for l in ctx_layers}
    out: list[str] = []
    if ids is not None:
        if isinstance(ids, str):
            ids = [ids]
        if not isinstance(ids, list):
            raise ToolError(f"{what} — список id слоёв")
        missing = [str(i) for i in ids if str(i) not in by_id]
        if missing:
            raise ToolError(f"{what}: нет слоёв {', '.join(missing[:10])}", ["get_clip — id слоёв"])
        out += [str(i) for i in ids if groupable(by_id[str(i)])]
    if prefix:
        out += [lid for lid, l in by_id.items() if lid.startswith(str(prefix)) and groupable(l) and lid not in out]
    return out


def h_flow_transition(ctx: ToolContext, args: dict) -> dict:
    kind = str(args.get("kind") or args.get("type") or "").strip()
    kind = _KIND_ALIASES.get(kind, kind)
    if kind not in FLOW_KINDS:
        raise ToolError(f"kind — один из {', '.join(FLOW_KINDS)}")
    at = _num(args.get("at"))
    if at is None or at < 0:
        raise ToolError("at — секунда клипа, где начинается переход (обычно за 0.3–0.4 с до конца сцены A)")
    duration = _num(args.get("duration"), FLOW_DEFAULT_SEC[kind]) or FLOW_DEFAULT_SEC[kind]
    if not 0.2 <= duration <= 3:
        raise ToolError("duration — 0.2..3 с (0.5–0.9 — заметно и не тянется)")
    clip = _clip_duration(ctx)
    end = round(at + duration, 3)
    if clip and end > clip + 0.05:
        raise ToolError(f"переход кончается на {end:g} с — после конца клипа ({clip:.2f} с)")
    render = _current_render(ctx)
    layers = _layers_of(render)
    by_id = {str(l.get("id")): l for l in layers}
    a_ids = _ids(layers, args.get("from"), args.get("from_prefix"), "from")
    b_ids = _ids(layers, args.get("to"), args.get("to_prefix"), "to")
    auto = not a_ids and not b_ids and args.get("from") is None and args.get("to") is None
    if auto:
        a_ids, b_ids = flow_groups(layers, at, end)
    target = args.get("target") or args.get("target_id")
    match = args.get("match") or args.get("match_id")
    for name, lid in (("target", target), ("match", match)):
        if lid and str(lid) not in by_id:
            raise ToolError(f"{name}: нет слоя {lid!r}")
    if kind == "shared_element" and not (target and match):
        raise ToolError("shared_element: нужны target (элемент сцены A) и match (тот же элемент в сцене B)")
    if kind == "zoom_through" and not target and not (_num(args.get("w")) and _num(args.get("h"))):
        raise ToolError("zoom_through: target — элемент A, сквозь который летит камера (кнопка, карточка), "
                        "или область x/y + w/h (опорные px)")
    if target and str(target) not in a_ids and str(target) not in b_ids:
        a_ids.append(str(target))
    if match and str(match) not in b_ids:
        b_ids.append(str(match))
    a_ids = [i for i in a_ids if i not in b_ids]
    if not a_ids and not b_ids:
        raise ToolError("переходить нечему: на этом стыке нет слоёв, которые кончаются (A) или начинаются (B)",
                        ["from/to — id слоёв сцен, или from_prefix/to_prefix", "at — секунда стыка сцен"])
    props: dict = {"kind": kind, "from": a_ids, "to": b_ids}
    for name, key in (("target", target), ("match", match)):
        if key:
            props[name] = str(key)
    simple = {"ease": "ease", "shape": "shape", "w": "w", "h": "h", "radius": "radius", "feather": "feather",
              "depth": "depth", "edge": "edge", "edge_width": "edgeWidth", "edgeWidth": "edgeWidth", "fill": "fill",
              "direction": "direction", "dip": "dip"}
    for arg, prop in simple.items():
        if args.get(arg) is not None:
            props[prop] = args[arg]
    taken = set(by_id)
    n = 1
    while f"tr_{n}" in taken:
        n += 1
    raw = {"id": f"tr_{n}", "type": "transition", "name": f"Переход: {KIND_ABOUT[kind]}", "start": round(at, 3),
           "end": end, "track": 0, "x": _num(args.get("x"), 0.5), "y": _num(args.get("y"), 0.5), "props": props}
    problems = motion.validate_layers([raw])
    if problems:
        raise ToolError("переход не поставлен", problems)
    new = motion.normalize_layers([raw])[0]
    notes: list[str] = []
    if args.get("fit_timing", True) is not False:
        layers, notes = fit_timing(layers, a_ids, b_ids, at, end, keep_in=bool(args.get("keep_in")))
    overlap = [l["id"] for l in layers if l.get("type") == "transition"
               and float(l.get("start", 0)) < end and float(l.get("end", 0)) > at]
    if overlap:
        notes.append(f"на этом отрезке уже есть переход {', '.join(overlap)} — слой, попавший в оба, ведёт первый")
    if len(layers) + 1 > motion.MAX_LAYERS:
        raise ToolError(f"в клипе уже {len(layers)} слоёв, предел {motion.MAX_LAYERS}")
    fresh = _save_render(ctx, {**render, "layers": layers + [new]})
    saved = _layers_of(fresh)
    out = {"ok": True, "id": new["id"], "kind": kind, "range": [new["start"], new["end"]],
           "from": a_ids, "to": b_ids, "auto_groups": auto,
           "check": f"view_frame на {at + duration * 0.35:.2f}, {at + duration * 0.7:.2f} с — сцены перетекают",
           "warnings": transition_problems(new, saved) + lint_layers(saved, clip, only_ids={new["id"]})}
    if notes:
        out["notes"] = notes
    return out


def flow_tools() -> list[Tool]:
    return [
        Tool(
            "flow_transition", "Ставлю перетекающий переход",
            "Перетекающий переход между сценами моушн-клипа/холста — вместо склейки сцена A перетекает в B (слой "
            "transition; сам не рисуется, ведёт группы слоёв). kind: reveal_from — из элемента target (точка, кружок "
            "в цифре, значок) растёт круг (shape rect — скруглённый прямоугольник) и раскрывает B, A вокруг "
            "уходит; depth 0..0.5 — «пролёт камеры» (0.12), feather — растушёвка края px, edge — цвет кольца по "
            "краю, fill — заливка под B (гаснет к концу). zoom_through — камера пролетает сквозь элемент target "
            "(кнопка, карточка) или область x/y + w/h: он растёт на весь кадр, внутри него из миниатюры вырастает "
            "B (radius — скругление элемента px). shared_element — элемент есть в обеих сценах: target (в A) "
            "едет и меняет размер, скругление, цвет в match (в B), остальное A гаснет, B приходит вокруг. "
            "push_slide — A и B одной лентой встык: direction left|right|up|down (куда уезжает A), dip 0..0.4 — "
            "отъезд камеры посередине (0.08). at — секунда начала, duration 0.2..3 (по умолчанию 0.55–0.8), "
            "ease — кривая (inOutCubic). Группы: from/to — id слоёв сцен A и B (или from_prefix/to_prefix); "
            "без них — по времени: A — слои, что кончаются к концу перехода, B — что начинаются на нём; фон, "
            "субтитры и слои через весь стык не трогаются. fit_timing (по умолчанию да): A живут ровно до конца "
            "перехода без своей анимации выхода, B начинаются с его начала без анимации входа (keep_in — "
            "оставить вход), ключи B сдвигаются, чтобы их время не изменилось. Между КАЖДЫМИ сценами промо — "
            "свой переход, склейка — только как осознанный удар. Проверка — view_frame на трети и двух третях "
            "перехода. Убрать — remove_layers id. / Flowing scene transition (reveal, zoom-through, shared element, push).",
            _obj({"kind": {"type": "string", "enum": list(FLOW_KINDS)},
                  "at": {"type": "number", "description": "секунда клипа: начало перехода"},
                  "duration": {"type": "number", "description": "длина, с (0.2..3)"},
                  "from": {"type": "array", "items": {"type": "string"}, "description": "id слоёв сцены A"},
                  "to": {"type": "array", "items": {"type": "string"}, "description": "id слоёв сцены B"},
                  "from_prefix": {"type": "string"}, "to_prefix": {"type": "string"},
                  "target": {"type": "string", "description": "элемент A: откуда раскрытие / сквозь что пролёт / общий элемент"},
                  "match": {"type": "string", "description": "shared_element: тот же элемент в сцене B"},
                  "x": {"type": "number", "description": "точка/центр области без target, доля кадра"},
                  "y": {"type": "number", "description": "точка/центр области без target, доля кадра"},
                  "w": {"type": "number", "description": "размер области без target, опорные px"},
                  "h": {"type": "number", "description": "размер области без target, опорные px"},
                  "shape": {"type": "string", "enum": list(FLOW_SHAPES)},
                  "radius": {"type": "number"}, "feather": {"type": "number"}, "depth": {"type": "number"},
                  "edge": {"type": "string", "description": "цвет кольца по краю маски #rrggbb"},
                  "edge_width": {"type": "number"}, "fill": {"type": "string", "description": "заливка под B #rrggbb"},
                  "direction": {"type": "string", "enum": list(FLOW_DIRECTIONS)}, "dip": {"type": "number"},
                  "ease": {"type": "string", "enum": motion_enums()["easings"]},
                  "fit_timing": {"type": "boolean"}, "keep_in": {"type": "boolean"}},
                 ["kind", "at"]),
            h_flow_transition, mutating=True,
            label=lambda a: f"Ставлю переход {a.get('kind') or ''} на {a.get('at')} с",
        ),
    ]
