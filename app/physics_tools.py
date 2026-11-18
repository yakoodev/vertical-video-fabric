"""Физика одним вызовом (``add_physics``): буквы падают и ложатся, стикеры сыплются или разлетаются из точки,
тарелку сбивает со стола — слой ``physics`` (frontend/src/motion/physics.ts, matter.js). Одобрено владельцем 18.11.

Мир считается движком заранее фиксированным шагом и кешируется треком: превью и рендер совпадают кадр в кадр.
Здесь — только сборка слоя (чистая функция ``physics_layer``): тела, появление, стенки, толчок, опоры. Стенки
``band`` — рамка полосы видео в раскладке «полоса» (meme); ``frame`` — кадр, пол по умолчанию на 80% высоты (ниже —
подпись и кнопки площадок). Слой проходит тот же путь, что add_layers: проверки, id, предупреждения.
"""

from __future__ import annotations

import re
from typing import Any

from app.tool_core import _current_render, _num, _obj, Tool, ToolContext, ToolError

SPAWNS = ("drop", "rain", "burst", "stack")
WALLS = ("frame", "band", "floor", "none")
SHAPES = ("box", "circle", "triangle", "plate")
SPLITS = ("letters", "words", "whole")
SPAWN_ALIASES = {"падение": "drop", "fall": "drop", "дождь": "rain", "сыплются": "rain", "взрыв": "burst",
                 "разлёт": "burst", "разлет": "burst", "explode": "burst", "explosion": "burst", "куча": "stack",
                 "pile": "stack", "tower": "stack", "стопка": "stack"}
DEFAULT_SEC = 3.5
MAX_SEC = 20.0
MAX_BODIES = 150
FLOOR = 0.8             # пол мира в кадре без полосы — над подписью и кнопками площадок
COUNTS = {"rain": 18, "burst": 16, "drop": 1, "stack": 6}
_EMOJI_CHAR = re.compile("[\U0001F000-\U0001FAFF☀-➿⭐⭕]")


def _frac(value: Any, default: float, lo: float = -0.2, hi: float = 1.2) -> float:
    v = _num(value)
    return round(min(hi, max(lo, float(v))), 4) if v is not None else default


def _image_ref(raw: Any) -> str | None:
    """«🔥» → emoji:1f525; emoji:…/sticker:… — как есть; число — id файла для монтажа."""
    from app.motion import emoji_ref

    text = str(raw if raw is not None else "").strip()
    if not text:
        return None
    if text.isdigit():
        return text
    if text.startswith(("emoji:", "sticker:")):
        return text
    if _EMOJI_CHAR.search(text):
        return emoji_ref(text)
    return None


def _point(raw: Any, where: str, problems: list[str]) -> dict | None:
    if raw is None:
        return None
    if isinstance(raw, dict) and _num(raw.get("x")) is not None and _num(raw.get("y")) is not None:
        return {"x": _frac(raw["x"], 0.5), "y": _frac(raw["y"], 0.5)}
    if isinstance(raw, (list, tuple)) and len(raw) == 2 and all(_num(v) is not None for v in raw):
        return {"x": _frac(raw[0], 0.5), "y": _frac(raw[1], 0.5)}
    problems.append(f"{where}: {{x, y}} — доли кадра (0.5, 0.5 — центр)")
    return None


def _bodies(args: dict, spawn: str | None, problems: list[str]) -> tuple[list[dict], int]:
    """Описания тел слоя и сколько тел выйдет (буквы + count)."""
    colors = [str(c) for c in args.get("colors") or [] if isinstance(c, str)][:6] or None
    size = _num(args.get("size"))
    out: list[dict] = []
    total = 0
    text = " ".join(str(args.get("text") or "").split())[:200]
    if text:
        split = str(args.get("split") or "letters")
        if split not in SPLITS:
            problems.append(f"split: {', '.join(SPLITS)}")
            split = "letters"
        body: dict[str, Any] = {"kind": "text", "text": text, "split": split, "size": size or 200, "weight": 900,
                                "stroke": args.get("stroke", "#000000"), "colors": colors or ["#ffffff"]}
        if args.get("font"):
            body["font"] = str(args["font"])[:80]
        out.append(body)
        total += len(text.split()) if split == "words" else 1 if split == "whole" else len(text.replace(" ", ""))
    images = args.get("images")
    if images is not None and not isinstance(images, list):
        images = [images]
    count = _num(args.get("count"))
    for raw in images or []:
        ref = _image_ref(raw)
        if not ref:
            problems.append(f"images: {raw!r} — эмодзи «🔥», emoji:…, sticker:<пак>/<файл> или id файла для монтажа")
            continue
        n = int(min(MAX_BODIES, max(1, count if count is not None else COUNTS.get(spawn or "rain", 12))))
        item: dict[str, Any] = {"kind": "image", "count": n, "size": size or 110}
        item["asset_id" if ref.isdigit() else "src"] = ref
        if args.get("shape") in SHAPES and args.get("shape") != "plate":
            item["shape"] = args["shape"]
        out.append(item)
        total += n
    shape = args.get("shape")
    if shape is not None and not images:
        if shape not in SHAPES:
            problems.append(f"shape: {', '.join(SHAPES)}")
        else:
            n = int(min(MAX_BODIES, max(1, count if count is not None else 1 if shape == "plate" else
                                        COUNTS.get(spawn or "rain", 12))))
            out.append({"kind": "shape", "shape": shape, "count": n, "size": size or (300 if shape == "plate" else 100),
                        "colors": colors or (["#f4f1ea"] if shape == "plate" else ["#ffd400", "#ff3b6b", "#22d3ee"])})
            total += n
    for raw in args.get("bodies") or []:
        if isinstance(raw, dict) and raw.get("kind") in ("text", "image", "shape"):
            out.append(dict(raw))
            total += 1 if raw.get("kind") == "text" and raw.get("split") == "whole" else \
                len(str(raw.get("text") or "").replace(" ", "")) if raw.get("kind") == "text" else \
                int(_num(raw.get("count")) or 1)
        else:
            problems.append("bodies[]: {kind: text|image|shape, …} — как в get_motion_reference (physics)")
    if not out and not problems:
        problems.append("что падает: text (буквы/слова), images (стикеры, эмодзи, файлы) или shape (plate — тарелка)")
    return out, total


def band_rect(ctx: ToolContext, render: dict) -> dict | None:
    """Полоса видео раскладки «полоса» (meme) в долях кадра; нет полосы — None."""
    from app.backgrounds import meme_hole
    from app.stream_layout import normalize_layout

    layout = render.get("layout")
    if not isinstance(layout, dict) or layout.get("mode") != "meme":
        return None
    plan = ctx.store.get_clip_plan(ctx.plan_id, include_segments=False)
    source = ctx.store.get_source(int(plan["source_id"])) if plan.get("source_id") else None
    width, height = 1080, 1920
    hole = meme_hole(normalize_layout(layout), source, (source or {}).get("content_crop"), width, height)
    if not hole or hole["w"] <= 0 or hole["h"] <= 0:
        return None
    return {"x": round(hole["x"] / width, 4), "y": round(hole["y"] / height, 4), "w": round(hole["w"] / width, 4),
            "h": round(hole["h"] / height, 4)}


def physics_layer(args: dict, band: dict | None = None) -> tuple[dict | None, list[str], list[str]]:
    """Слой физики (чистая функция): ``(слой, проблемы, заметки)``. ``band`` — полоса видео (доли кадра)."""
    problems: list[str] = []
    notes: list[str] = []
    spawn = args.get("spawn")
    if spawn is not None:
        spawn = SPAWN_ALIASES.get(str(spawn).strip().lower(), str(spawn).strip().lower())
        if spawn not in SPAWNS:
            problems.append(f"spawn: {', '.join(SPAWNS)} (drop — падают строкой, rain — дождь, burst — разлёт, "
                            "stack — стоит кучей до толчка)")
            spawn = None
    if spawn is None:
        spawn = "stack" if args.get("shape") == "plate" else "drop" if args.get("text") else "rain" \
            if args.get("images") or args.get("shape") else "drop"
    bodies, total = _bodies(args, spawn, problems)
    if total > MAX_BODIES:
        notes.append(f"тел {total} — движок возьмёт первые {MAX_BODIES}")
    walls = str(args.get("walls") or ("band" if band else "frame"))
    if walls not in WALLS:
        problems.append(f"walls: {', '.join(WALLS)}")
        walls = "frame"
    if walls == "band" and not band:
        notes.append("раскладка не «полоса» — стенки по кадру")
        walls = "frame"
    floor = _frac(args.get("floor"), FLOOR, 0.2, 1.0)
    bounds = dict(band) if walls == "band" and band else {"x": 0.0, "y": 0.0, "w": 1.0, "h": floor}
    origin = _point(args.get("origin"), "origin", problems)
    props: dict[str, Any] = {"bodies": bodies, "spawn": spawn, "walls": "frame" if walls == "band" else walls,
                             "bounds": bounds}
    for key, lo, hi in (("gravity", -3, 3), ("restitution", 0, 1), ("friction", 0, 1), ("power", 0, 8000),
                        ("spread", 0, 20), ("spin", 0, 10)):
        value = _num(args.get(key))
        if value is not None:
            props[key] = round(min(hi, max(lo, value)), 3)
    plate = args.get("shape") == "plate"
    if plate and spawn == "stack":
        # Тарелка на столе: стол — невидимая опора под ней (если своих опор нет), толчок сбоку через 0.6 с.
        origin = origin or {"x": 0.5, "y": round(min(bounds["y"] + bounds["h"], 0.6), 4)}
        props.setdefault("friction", 0.08)
        knock = str(args.get("knock") or "right").lower()
        side = -1.0 if knock in ("left", "влево") else 1.0
        if not args.get("platforms"):
            props["platforms"] = [{"x": round(origin["x"] - side * 0.08, 4), "y": round(origin["y"] + 0.01, 4),
                                   "w": 0.5, "h": 0.02}]
        if args.get("impulse") is None:
            props["impulse"] = {"at": 0.6, "x": round(origin["x"] - side * 0.3, 4), "y": round(origin["y"] - 0.002, 4),
                                "force": 2600, "radius": 0.5, "lift": 0}
    if origin:
        props["origin"] = origin
    impulse = args.get("impulse")
    if isinstance(impulse, dict):
        point = _point(impulse, "impulse", problems)
        if point:
            imp: dict[str, Any] = {"at": round(max(0.0, _num(impulse.get("at")) or 0.5), 3), **point}
            for key in ("force", "radius", "lift"):
                if _num(impulse.get(key)) is not None:
                    imp[key] = float(_num(impulse[key]))
            props["impulse"] = imp
    elif impulse is not None:
        problems.append("impulse: {at (с от начала слоя), x, y, force?, radius?, lift?}")
    if isinstance(args.get("platforms"), list):
        props["platforms"] = [p for p in args["platforms"] if isinstance(p, dict)][:8]
    if _num(args.get("seed")) is not None:
        props["seed"] = int(abs(_num(args["seed"]) or 0)) % 1000000
    if problems:
        return None, problems, notes
    duration = min(MAX_SEC, max(0.5, _num(args.get("duration")) or DEFAULT_SEC))
    start = max(0.0, _num(args.get("at")) or 0.0)
    layer: dict[str, Any] = {"type": "physics", "name": f"физика: {spawn}", "x": 0.5, "y": 0.5, "start": start,
                             "end": start + duration, "props": props, "out": {"type": "fade", "duration": 0.3}}
    if args.get("at_word") is not None:
        layer["at_word"] = args["at_word"]
        layer.pop("start", None)
        layer.pop("end", None)
        layer["duration"] = duration
    return layer, problems, notes


def h_add_physics(ctx: ToolContext, args: dict) -> dict:
    from app.layer_tools import h_add_layers

    render = _current_render(ctx)
    layer, problems, notes = physics_layer(args, band_rect(ctx, render))
    if problems or layer is None:
        raise ToolError("физика не принята", problems)
    out = h_add_layers(ctx, {"layers": [layer]})
    out["note"] = "; ".join(notes + ["мир считается заранее (превью = рендер); посмотри view_frame в момент удара и "
                                     "в конце — где легли тела, не закрыли ли лицо и субтитры"])
    return out


def tools() -> list[Tool]:
    return [
        Tool(
            "add_physics", "Бросаю тела с физикой",
            "Слой физики одним вызовом (matter.js; мир считается заранее — превью и рендер совпадают кадр в кадр): "
            "буквы слова падают и ложатся кучей, стикеры сыплются дождём или разлетаются из точки, тарелку "
            "сбивает со стола. Что падает: text (+ split letters|words|whole, stroke, font, colors), images "
            "[«sticker:<пак>/<файл>», id файла, эмодзи — только если владелец просил: эмодзи в монтаже он не любит] "
            "с count, или shape box|circle|triangle|plate (plate — тарелка); bodies — свои описания тел (как в "
            "get_motion_reference physics). spawn: drop (строкой сверху, по умолчанию для текста), rain (дождь, "
            "spread — за сколько секунд), burst (разлёт из origin, power px/с), stack (стоит кучей до толчка; "
            "shape plate + stack — тарелка на невидимом столе, через 0.6 с её толкает knock right|left). walls: "
            "band (в раскладке «полоса» — стенки и пол по полосе видео, по умолчанию там), frame (кадр, пол на "
            "floor = 0.8 высоты — над подписью площадки), floor (только пол), none (улетают за кадр). gravity 1, "
            "restitution 0.3 (упругость), friction 0.3; impulse {at (с от начала слоя), x, y, force, radius, lift} "
            "— толчок из точки; platforms [{x, y, w, h, angle}] — невидимые опоры (стол, полка в кадре); точки — "
            "доли кадра. Время — at (секунды клипа) или at_word {text}; duration 3.5 с (≤20). Тел ≤150. Физика — "
            "акцент удара/кульминации, не фон всего ролика. / Rigid-body physics layer (letters fall, stickers "
            "rain/burst, plate knocked off).",
            _obj({"text": {"type": "string"}, "split": {"type": "string", "enum": list(SPLITS)},
                  "images": {"type": "array", "items": {"type": "string"},
                             "description": "sticker:<пак>/<файл>, id файла или эмодзи"},
                  "shape": {"type": "string", "enum": list(SHAPES)},
                  "count": {"type": "integer"}, "size": {"type": "number"},
                  "bodies": {"type": "array", "items": {"type": "object"}},
                  "spawn": {"type": "string", "enum": list(SPAWNS)},
                  "walls": {"type": "string", "enum": list(WALLS)},
                  "floor": {"type": "number", "description": "пол мира без полосы, доля высоты (0.8)"},
                  "gravity": {"type": "number"}, "restitution": {"type": "number"}, "friction": {"type": "number"},
                  "power": {"type": "number"}, "spread": {"type": "number"}, "spin": {"type": "number"},
                  "origin": {"type": "object", "description": "{x, y} — точка разлёта / центр строки / основание кучи"},
                  "impulse": {"type": "object", "description": "{at, x, y, force?, radius?, lift?}"},
                  "knock": {"type": "string", "enum": ["right", "left"], "description": "тарелка: куда сбить"},
                  "platforms": {"type": "array", "items": {"type": "object"}},
                  "at": {"type": "number", "description": "секунды клипа"},
                  "at_word": {"type": "object", "description": "{text} или {line, word} — как в add_layers"},
                  "duration": {"type": "number"}, "colors": {"type": "array", "items": {"type": "string"}},
                  "stroke": {"type": ["string", "null"]}, "font": {"type": "string"}, "seed": {"type": "integer"}}),
            h_add_physics, mutating=True,
            label=lambda a: "Бросаю тела с физикой" + (f": «{a.get('text')}»" if a.get("text") else ""),
        ),
    ]
