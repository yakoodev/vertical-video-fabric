"""«Пометка от руки» одним вызовом (``add_annotation``): круг, подчёркивание, стрелка или штриховка маркером
поверх кадра — для объяснялок совы и разборов.

Одобрено владельцем 17.11 (идея — рисованные пометки каталога HyperFrames, Apache-2.0; код свой). Это обычные
слои: фигура ``shape`` от руки (``rough`` — дрожание, круг заходит сам на себя, ``pen: hand`` — нажим маркера:
тонкий заход, хвост сходит на нет; frontend/src/motion/layers/sketch.ts) рисуется входом ``draw`` за 0.3–0.5 с,
подпись — текст шрифтом Caveat с входом «шторкой» (как пишется). Место — доли кадра: ``target`` {x, y, w?, h?}
(центр и размер того, что помечаем) или у стрелки ``from``/``to``. Время — ``at`` (секунды клипа) или
``at_word`` (как в add_layers). Слои проходят тот же путь, что add_layers: проверки, id, предупреждения.
"""

from __future__ import annotations

import math
from typing import Any

from app.tool_core import _current_render, _num, _obj, Tool, ToolContext, ToolError

KINDS = ("circle", "underline", "arrow", "scribble")
KIND_ALIASES = {"ellipse": "circle", "ring": "circle", "обвести": "circle", "круг": "circle", "line": "underline",
                "underscore": "underline", "подчеркнуть": "underline", "стрелка": "arrow", "pointer": "arrow",
                "highlight": "scribble", "marker": "scribble", "hatch": "scribble", "штриховка": "scribble"}
DEFAULT_COLOR = "#ff4d3d"   # красный маркер: видно и на светлом, и на тёмном
DRAW_MIN, DRAW_MAX, DRAW_DEFAULT = 0.3, 0.5, 0.4
HOLD_DEFAULT = 2.2          # сколько пометка держится (с, вместе с рисованием)
FONT = "Caveat"             # рукописный, с кириллицей (vendor/fonts)


def _frac(value: Any, default: float, lo: float = -0.2, hi: float = 1.2) -> float:
    v = _num(value)
    return min(hi, max(lo, float(v))) if v is not None else default


def _point(raw: Any, where: str, problems: list[str]) -> tuple[float, float] | None:
    if raw is None:
        return None
    if isinstance(raw, dict) and _num(raw.get("x")) is not None and _num(raw.get("y")) is not None:
        return _frac(raw["x"], 0.5), _frac(raw["y"], 0.5)
    if isinstance(raw, (list, tuple)) and len(raw) == 2 and all(_num(v) is not None for v in raw):
        return _frac(raw[0], 0.5), _frac(raw[1], 0.5)
    problems.append(f"{where}: {{x, y}} — доли кадра (0.5, 0.5 — центр)")
    return None


def annotation_layers(args: dict, frame: tuple[float, float] = (1080.0, 1920.0)) -> tuple[list[dict], list[str]]:
    """Слои пометки (чистая функция): ``([фигура, подпись?], проблемы)``. ``frame`` — опорный кадр, px."""
    problems: list[str] = []
    W, H = float(frame[0]), float(frame[1])
    kind = str(args.get("kind") or "circle").strip().lower()
    kind = KIND_ALIASES.get(kind, kind)
    if kind not in KINDS:
        problems.append(f"kind: {', '.join(KINDS)}")
        return [], problems
    target = args.get("target") if isinstance(args.get("target"), dict) else None
    if args.get("target") is not None and target is None:
        problems.append("target: {x, y, w?, h?} — центр и размер помечаемого в долях кадра")
    tx, ty = (_frac(target.get("x"), 0.5), _frac(target.get("y"), 0.5)) if target else (0.5, 0.5)
    tw = _frac(target.get("w"), 0.28, 0.02, 1.0) if target else 0.28
    th = _frac(target.get("h"), 0.08, 0.01, 1.0) if target else 0.08
    color = str(args.get("color") or DEFAULT_COLOR)
    width = min(40.0, max(4.0, _num(args.get("width")) or 14.0))
    draw = min(DRAW_MAX, max(DRAW_MIN, _num(args.get("draw")) or DRAW_DEFAULT))
    start = max(0.0, _num(args.get("at")) or 0.0)
    hold = max(draw + 0.4, _num(args.get("duration")) or HOLD_DEFAULT)
    seed = int(abs(_num(args.get("seed")) or (round(tx * 997 + ty * 131 + KINDS.index(kind) * 7)))) % 100000
    rough = min(1.0, max(0.15, _num(args.get("rough")) or 0.55))
    props: dict[str, Any] = {"fill": None, "stroke": {"color": color, "width": width}, "rough": rough, "seed": seed,
                             "pen": "hand"}
    layer: dict[str, Any] = {"type": "shape", "name": f"пометка: {kind}", "start": start, "end": start + hold,
                             "in": {"type": "draw", "duration": draw}, "out": {"type": "fade", "duration": 0.25}}
    label_at: tuple[float, float] = (tx, ty - th / 2 - 0.035)
    if kind == "circle":
        props.update(kind="ellipse", w=round(tw * W * 1.18 + 36, 1), h=round(th * H * 1.3 + 36, 1))
        layer.update(x=tx, y=ty)
        label_at = (tx, ty - (th * 1.3 * H + 36) / 2 / H - 0.035)
    elif kind == "underline":
        y = ty + th / 2 + 0.012
        bend = (1 if seed % 2 else -1) * min(14.0, tw * W * 0.02)
        props.update(kind="line", w=round(tw * W * 1.05, 1), bend=round(bend, 1))
        layer.update(x=tx, y=y)
        label_at = (tx, y + 0.045)
    elif kind == "scribble":
        props.update(kind="scribble", w=round(tw * W, 1), h=round(th * H, 1))
        props["stroke"] = {"color": color, "width": round(max(14.0, min(40.0, th * H / 3)), 1)}
        props["rough"] = min(rough, 0.4)
        # Маркер поверх слова: «осветление» — белые буквы остаются белыми, тёмный фон под ними желтеет.
        layer.update(x=tx, y=ty, opacity=0.8, blend="screen")
        label_at = (tx, ty - th / 2 - 0.035)
    else:  # arrow
        to = _point(args.get("to"), "to", problems) or (tx, ty)
        frm = _point(args.get("from"), "from", problems)
        if frm is None:
            # Нет начала — стрелка приходит к цели от центра кадра, длиной ~ 0.2 ширины кадра.
            vx, vy = (0.5 - to[0]) * W, (0.5 - to[1]) * H
            norm = math.hypot(vx, vy) or 1.0
            if norm < 1:
                vx, vy, norm = -1.0, 1.0, math.sqrt(2)
            length = 0.22 * W
            frm = (to[0] + vx / norm * length / W, to[1] + vy / norm * length / H)
        ax, ay, bx, by = frm[0] * W, frm[1] * H, to[0] * W, to[1] * H
        length = math.hypot(bx - ax, by - ay)
        if length < 40:
            problems.append("стрелка короче 40 px — разведи from и to")
            return [], problems
        props.update(kind="arrow", w=round(length, 1), bend=round((1 if seed % 2 else -1) * 0.1 * length, 1))
        layer.update(x=(frm[0] + to[0]) / 2, y=(frm[1] + to[1]) / 2,
                     rotation=round(math.degrees(math.atan2(by - ay, bx - ax)), 2))
        # Подпись — у хвоста, с той стороны, откуда стрелка пришла.
        ux, uy = (ax - bx) / length, (ay - by) / length
        label_at = (frm[0] + ux * 0.06, frm[1] + uy * 0.035)
    layer["props"] = props
    if args.get("at_word") is not None:
        layer["at_word"] = args["at_word"]
        layer.pop("start", None)
        layer["duration"] = hold
    out = [layer]
    text = " ".join(str(args.get("text") or "").split())
    if text:
        size = min(140.0, max(36.0, _num(args.get("text_size")) or 84.0))
        label = {"type": "text", "name": "пометка: подпись", "x": min(0.92, max(0.08, label_at[0])),
                 "y": min(0.95, max(0.04, label_at[1])), "start": start + draw * 0.6, "end": start + hold,
                 "rotation": -3 if seed % 2 else 2,
                 "props": {"text": text[:80], "font": FONT, "weight": 700, "size": size, "color": color,
                           "maxWidth": 700},
                 "in": {"type": "wipe", "duration": 0.45}, "out": {"type": "fade", "duration": 0.25}}
        if args.get("at_word") is not None:
            label["at_word"] = args["at_word"]
            label["word_offset"] = round(draw * 0.6, 3)  # подпись — когда обводка почти дорисована
            label.pop("start", None)
            label["duration"] = round(hold - draw * 0.6, 3)
        out.append(label)
    return out, problems


def h_add_annotation(ctx: ToolContext, args: dict) -> dict:
    from app.formats import ref_size, settings_layers_format
    from app.layer_tools import h_add_layers

    render = _current_render(ctx)
    layers, problems = annotation_layers(args, ref_size(settings_layers_format(render)))
    if problems:
        raise ToolError("пометка не принята", problems)
    out = h_add_layers(ctx, {"layers": layers})
    out["note"] = ("пометка от руки: обводка рисуется входом draw, перо с нажимом (shape pen hand, rough); "
                   "посмотри view_frame посреди рисования и после")
    return out


def tools() -> list[Tool]:
    return [
        Tool(
            "add_annotation", "Рисую пометку от руки",
            "«Пометка от руки» поверх кадра одним вызовом — для объяснялок совы и разборов: kind circle (обвести), "
            "underline (подчеркнуть), arrow (стрелка к цели), scribble (заштриховать маркером). Линия дрожит как "
            "у руки (rough), перо с нажимом (тонкий заход, хвост на нет), рисуется за draw 0.3–0.5 с (0.4) и "
            "держится duration (2.2 с). Место — доли кадра: target {x, y, w, h} — центр и размер того, что "
            "помечаешь (слово, цифра, лицо); у стрелки to {x, y} — куда указывает (нет — центр target), from — "
            "откуда (нет — от центра кадра). Время — at (секунды клипа) или at_word {text: \"…\"} — как в "
            "add_layers. text — подпись рукописным шрифтом Caveat тем же цветом (у круга — над ним, у стрелки — "
            "у хвоста), пишется шторкой. color (#ff4d3d), width (толщина линии, 14), seed (другой вариант "
            "дрожания). Не больше 1–2 пометок на кадр. Шаблоны hand-arrow/hand-circle/hand-underline "
            "(add_motion_template) — то же с подписью-печатью; здесь — точно по рамке цели и слову речи. "
            "/ Hand-drawn annotation (circle/underline/arrow/scribble).",
            _obj({"kind": {"type": "string", "enum": list(KINDS)},
                  "target": {"type": "object", "description": "{x, y, w, h} — доли кадра (центр и размер)"},
                  "to": {"type": "object", "description": "стрелка: куда указывает {x, y}"},
                  "from": {"type": "object", "description": "стрелка: откуда {x, y}"},
                  "at": {"type": "number", "description": "секунды клипа"},
                  "at_word": {"type": "object", "description": "{text} или {line, word} — как в add_layers"},
                  "duration": {"type": "number"},
                  "draw": {"type": "number", "minimum": DRAW_MIN, "maximum": DRAW_MAX},
                  "text": {"type": "string"}, "text_size": {"type": "number"},
                  "color": {"type": "string"}, "width": {"type": "number"}, "rough": {"type": "number"},
                  "seed": {"type": "integer"}}, ["kind"]),
            h_add_annotation, mutating=True,
            label=lambda a: f"Рисую пометку: {a.get('kind') or 'circle'}" + (f" «{a.get('text')}»" if a.get("text") else ""),
        ),
    ]
