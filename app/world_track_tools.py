"""Инструменты агента «в мире игры»: ``suggest_surfaces``, ``track_surface``, ``track_object`` (app/world_track.py).

* ``suggest_surfaces(t, text)`` — где у момента t ровная фактурная поверхность под слово (картинка с рамками 1..n);
* ``track_surface(t, quad|box, start, end)`` — трек плоскости → ``track_id``; ``layer_id`` — сразу привязать к слою
  ``tracked_text``, ``text`` — сразу добавить такой слой (граффити 3D, слова по ``words``);
* ``track_object(t, x, y, …)`` — трек броска (граната) → ``track_id``; ``trail: true`` — сразу слой ``trail``.

Ответ трека — числа (доля кадров, где держится, ошибка в px) и лист кадров проверки со слоем: «прилипло ли».
Входят в общий реестр (``app/agent_tools.py``); модуль сервиса — «Мувик под песню» (``frag_movie``).
"""

from __future__ import annotations

from typing import Any

from app.tool_core import ImageResult, Tool, ToolContext, ToolError, _num, _obj

POINT = {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}


def _range(args: dict, t: float, before: float, after: float) -> tuple[float, float]:
    start = _num(args.get("start"))
    end = _num(args.get("end"))
    return (max(0.0, t - before) if start is None else start), (t + after if end is None else end)


def _sheet(ctx: ToolContext, times: list[float]) -> bytes | None:
    from app.motion_render import contact_sheet_jpeg

    try:
        return contact_sheet_jpeg(ctx.store, ctx.plan_id, times=times[:6], width=300, before_cuts=True)
    except Exception:  # noqa: BLE001 - лист — подспорье, трек важнее
        return None


def _check_times(a: float, b: float, n: int = 4) -> list[float]:
    if b <= a:
        return [round(a, 3)]
    return [round(a + (b - a) * (k + 0.5) / n, 3) for k in range(n)]


def _add_or_attach(ctx: ToolContext, args: dict, layer_type: str, track_id: str, layer: dict | None) -> dict:
    """Привязать трек к слою ``layer_id`` или добавить новый слой ``layer`` (add_layers). → {layer_id, added|updated}."""
    from app.layer_tools import h_add_layers, h_update_layer
    from app.tool_core import _current_render, _layers_of

    layer_id = str(args.get("layer_id") or "")
    if layer_id:
        found = next((l for l in _layers_of(_current_render(ctx)) if str(l.get("id")) == layer_id), None)
        if found is None:
            raise ToolError(f"нет слоя {layer_id}")
        if found.get("type") != layer_type:
            raise ToolError(f"слой {layer_id} — {found.get('type')}, трек подходит слою {layer_type}")
        h_update_layer(ctx, {"id": layer_id, "patch": {"props": {"track": track_id}}})
        return {"layer_id": layer_id, "updated": True}
    if layer is None:
        return {}
    res = h_add_layers(ctx, {"layers": [layer]})
    added = res.get("added") or []
    return {"layer_id": (added[0].get("id") if added and isinstance(added[0], dict) else layer.get("id")), "added": True,
            "warnings": res.get("warnings") or []}


def h_suggest_surfaces(ctx: ToolContext, args: dict) -> Any:
    from app import world_track as WT

    t = _num(args.get("t"))
    if t is None:
        raise ToolError("нужен t — момент (шкала clip), где встанет слово")
    n = int(min(5, max(1, _num(args.get("n"), 3) or 3)))
    width = min(0.9, max(0.15, _num(args.get("width"), 0.5) or 0.5))
    try:
        cands, setup, img = WT.suggest(ctx.store, ctx.plan_id, t, str(args.get("text") or ""), n, width)
    except WT.TrackError as exc:
        raise ToolError(str(exc), exc.hints) from exc
    info = {"ok": True, "t": t, "time": "clip", "candidates": [
        {"n": k + 1, "quad": c["quad"], "box": {kk: c[kk] for kk in ("x", "y", "w", "h")}, "score": c["score"],
         "points": c["points"], "flat": c["flat"]} for k, c in enumerate(cands)],
        "note": ("рамки 1..n на кадре — места под слово (фактура + одна плоскость по соседним кадрам). Это подсказка: "
                 "плоскость поверхности (скала, стена, ящик, колонна) выбери глазами — quad по её углам даёт "
                 "перспективу, лучше дальняя/средняя поверхность, а не пол под ногами (он уезжает вниз и растёт). "
                 "Дальше — track_surface(t, quad, text)")}
    if not cands:
        info["note"] = "ровной фактурной поверхности у центра не нашлось — другой момент или quad руками"
    data = WT.draw_candidates(img, cands)
    return ImageResult(data=data, mime_type="image/jpeg", info=info) if data else info


def h_track_surface(ctx: ToolContext, args: dict) -> Any:
    from app import world_track as WT

    t = _num(args.get("t"))
    if t is None:
        raise ToolError("нужен t — момент (шкала clip), в который задана поверхность")
    start, end = _range(args, t, 0.6, 1.6)
    plan = ctx.store.get_clip_plan(ctx.plan_id)
    from app.clip_settings import normalize_render_settings
    from app.formats import settings_layers_format
    from app.layer_lint import _ref

    fmt = settings_layers_format(normalize_render_settings(plan.get("render_settings") or {}))
    rw, rh = _ref(fmt)
    try:
        quad = WT.normalize_quad(args, rw / rh)
        track = WT.track_surface(ctx.store, ctx.plan_id, t=t, start=start, end=end, quad=quad, fps=args.get("fps"),
                                 smooth=args.get("smooth"))
    except WT.TrackError as exc:
        raise ToolError(f"поверхность не отслежена: {exc}", exc.hints) from exc
    show_arg = args.get("show") if isinstance(args.get("show"), list) and len(args["show"]) == 2 else None
    report = {"ok": True, "time": "clip", **WT.surface_report(track, _num(args.get("rotation"), 0.0) or 0.0, show_arg)}
    if not report["check"]["ok"]:
        report["warning_check"] = ("ПРОВАЛ проверки позы: " + "; ".join(report["check"]["fail"])
                                   + " — другое место (без деревьев/рук перед поверхностью), окно короче, без поворота "
                                   "текста; такое слово владельцу не показывать")
    held = float(track.get("found") or 0)
    if held < WT.SURFACE_OK:
        report["warning"] = (f"держится только {held:.0%} кадров — слово на этой поверхности будет пропадать; другое "
                             "место (suggest_surfaces), короче окно или обычный текст на экране")
    text = str(args.get("text") or "").strip()
    layer = None
    if text:
        words = [{"t": float(w)} for w in args.get("word_times") or [] if _num(w) is not None]
        props: dict = {"text": text, "track": track["id"]}
        if words:
            props["words"] = words
        for key in ("preset", "font", "color", "gradient", "stroke", "extrude", "skew", "uppercase", "fit", "reveal"):
            if args.get(key) is not None:
                props[key] = args[key]
        show = args.get("show") if isinstance(args.get("show"), list) and len(args["show"]) == 2 else None
        layer = {"type": "tracked_text", "start": float(show[0]) if show else start, "end": float(show[1]) if show else end,
                 "track": 6, "out": {"type": "fade", "duration": 0.12}, "props": props}
        if args.get("rotation") is not None:
            layer["rotation"] = _num(args.get("rotation"), 0.0)
        if args.get("id"):
            layer["id"] = str(args["id"])
    report.update(_add_or_attach(ctx, args, "tracked_text", track["id"], layer))
    report["next"] = ("слой: add_layers({type: \"tracked_text\", start, end, props: {text, track: track_id, words: [{t}]}}) "
                      "— x/y/scale слоя — место и размер на плоскости; текст не поворачивать (боком владелец читает как "
                      "«перевёрнутый», проверка check такое проваливает). "
                      "Смотри лист: слово стоит на поверхности, не плавает? Плывёт — другой quad (без деревьев и рук "
                      "перед поверхностью) или окно короче.")
    a, b = (layer["start"], layer["end"]) if layer else (start, end)
    sheet = _sheet(ctx, _check_times(a, b)) if (layer or args.get("layer_id")) and args.get("check") is not False else None
    return ImageResult(data=sheet, mime_type="image/jpeg", info=report) if sheet else report


def h_track_object(ctx: ToolContext, args: dict) -> Any:
    from app import world_track as WT

    t, x, y = _num(args.get("t")), _num(args.get("x")), _num(args.get("y"))
    if t is None or x is None or y is None:
        raise ToolError("нужны t, x, y — где объект в момент t (доли кадра, как на view_frame)")
    start, end = _range(args, t, 0.0, 1.5)
    try:
        track = WT.track_object(ctx.store, ctx.plan_id, t=t, x=x, y=y, start=start, end=end, w=args.get("w"),
                                h=args.get("h"), hints=args.get("hints"), fps=args.get("fps"),
                                lead_in=_num(args.get("lead_in"), 0.0) or 0.0,
                                stop_at_rest=args.get("stop_at_rest") is not False)
    except WT.TrackError as exc:
        raise ToolError(f"объект не отслежен: {exc}", exc.hints) from exc
    report = {"ok": True, "time": "clip", **WT.object_report(track)}
    layer = None
    if args.get("trail"):
        props: dict = {"track": track["id"]}
        for key in ("color", "glowColor", "width", "glow", "style", "tail", "fadeTail", "head", "anchor"):
            if args.get(key) is not None:
                props[key] = args[key]
        rng = track.get("range") or [t, end]
        a = float((track.get("samples") or [{"t": rng[0]}])[0]["t"])
        layer = {"type": "trail", "start": round(a, 3), "end": round(min(end, float(rng[1]) + 1.2), 3), "track": 5,
                 "out": {"type": "fade", "duration": 0.35}, "props": props}
    report.update(_add_or_attach(ctx, args, "trail", track["id"], layer))
    report["next"] = ("след: add_layers({type: \"trail\", start, end, props: {track: track_id, color, width, glow, style: "
                      "solid|dashed|dotted, tail}}). Объект потерян рано — hints [{t, x, y}] по кадрам view_frame; "
                      "граната вылетает из-за края — lead_in 0.1–0.2 (дуга продолжается назад до края).")
    rng = track.get("range") or [t, t]
    sheet = _sheet(ctx, _check_times(float(rng[0]), float(rng[1]) + 0.3)) if layer or args.get("layer_id") else None
    return ImageResult(data=sheet, mime_type="image/jpeg", info=report) if sheet else report


def tools() -> list[Tool]:
    return [
        Tool(
            "suggest_surfaces", "Ищу поверхность под слово",
            "Где в кадре момента t (шкала clip) поставить слово «в мир игры»: окна у центра кадра на ровной фактурной "
            "поверхности (скала, стена, ящик) — точки окна по соседним кадрам ложатся одной плоскостью, не небо и не "
            "HUD. Ответ — кадр с рамками 1..n и их quad/box (доли кадра). Подсказка, не приговор: лучше поверхность "
            "средней дальности перед камерой, без деревьев и рук перед ней. text — слово (пропорции окна), width — "
            "ширина окна (доля кадра, 0.5). Дальше — track_surface. / Suggest flat textured surfaces for a world-stuck word.",
            _obj({"t": {"type": "number"}, "text": {"type": "string"}, "n": {"type": "integer", "minimum": 1, "maximum": 5},
                  "width": {"type": "number"}}, ["t"]),
            h_suggest_surfaces, cost=("cpu",),
            label=lambda a: f"Ищу поверхность под слово на {(_num(a.get('t'), 0.0) or 0.0):.1f} с",
        ),
        Tool(
            "track_surface", "Веду поверхность",
            "Слово «прилипает» к поверхности мира (как граффити на скале у референса владельца): трек плоскости по "
            "кадрам основы клипа (KLT + гомография RANSAC, сверка с кадром-семенем против дрейфа, потеря и поиск "
            "заново). t — момент, где задана поверхность: quad [[x,y]×4] — углы по часовой с левого верхнего (доли "
            "кадра; трапеция по перспективе поверхности) или box {x, y, w, h, angle} — центр и размер. start/end — окно "
            "трека (шкала clip; нет — t−0.6…t+1.6), не через склейку. Ответ: track_id, held (доля кадров, где держится), "
            "err_px, lost. text — сразу слой tracked_text (preset graffiti3d: синий→фиолетовый, белая обводка, объём; "
            "word_times — время появления каждого слова, шкала clip; show [start, end] — когда виден; текст только "
            "горизонтально), layer_id — привязать трек к готовому слою. Модель — подобие + ограниченная перспектива, "
            "перевёрнутые/боком/скачущие кадры отбраковываются, слово там гаснет. check — проверка позы (flips, "
            "text_angle_max, rot_step_max, jitter_px; ok false — не показывать). Ответ со слоем — лист кадров "
            "проверки. / Track a "
            "planar surface for a world-stuck word layer.",
            _obj({"t": {"type": "number"},
                  "quad": {"type": "array", "items": POINT, "minItems": 4, "maxItems": 4},
                  "box": _obj({"x": {"type": "number"}, "y": {"type": "number"}, "w": {"type": "number"},
                               "h": {"type": "number"}, "angle": {"type": "number"}}),
                  "start": {"type": "number"}, "end": {"type": "number"},
                  "fps": {"type": "number", "description": "кадров трека в секунду (30; 60 — для быстрых поворотов)"},
                  "smooth": {"type": "number", "description": "сглаживание углов 0..1 (0.35)"},
                  "text": {"type": "string"}, "word_times": {"type": "array", "items": {"type": "number"}},
                  "show": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
                  "rotation": {"type": "number"}, "preset": {"type": "string", "enum": ["graffiti3d", "plain"]},
                  "font": {"type": "string"}, "color": {"type": "string"},
                  "gradient": {"type": "array", "items": {"type": "string"}}, "stroke": {"type": "object"},
                  "extrude": {"type": "object"}, "skew": {"type": "number"}, "uppercase": {"type": "boolean"},
                  "fit": {"type": "number"}, "reveal": {"type": "string", "enum": ["pop", "fade", "slam", "none"]},
                  "id": {"type": "string"}, "layer_id": {"type": "string"}, "check": {"type": "boolean"}}, ["t"]),
            h_track_surface, mutating=True, cost=("cpu",),
            label=lambda a: "Веду поверхность" + (f" под «{str(a.get('text'))[:20]}»" if a.get("text") else ""),
        ),
        Tool(
            "track_object", "Веду брошенный предмет",
            "Трек мелкого летящего объекта (граната, снаряд, мяч) для следа trail: шаблон с кадра t в точке x, y (доли "
            "кадра, как на view_frame; w — размер, доля ширины, 0.035), поиск по прогнозу дуги в мире (движение камеры "
            "вычитается), 3 масштаба. Кончается, когда объект упал/пропал (stop_at_rest). hints [{t, x, y}] — точки, "
            "увиденные глазами: прогноз идёт по ним (мелкая граната на пёстром фоне). lead_in — секунд дуги назад "
            "до края кадра (граната вылетает из-за края). start/end — окно (шкала clip; нет — t…t+1.5), fps (60). "
            "trail: true — сразу слой trail (color, width, glow, style solid|dashed|dotted, tail), layer_id — "
            "привязать к готовому. / Track a thrown object for an animated trajectory trail.",
            _obj({"t": {"type": "number"}, "x": {"type": "number"}, "y": {"type": "number"},
                  "w": {"type": "number"}, "h": {"type": "number"},
                  "hints": {"type": "array", "items": _obj({"t": {"type": "number"}, "x": {"type": "number"},
                                                            "y": {"type": "number"}}, ["t", "x", "y"])},
                  "lead_in": {"type": "number", "minimum": 0, "maximum": 0.5},
                  "stop_at_rest": {"type": "boolean"},
                  "start": {"type": "number"}, "end": {"type": "number"}, "fps": {"type": "number"},
                  "trail": {"type": "boolean"}, "color": {"type": "string"}, "glowColor": {"type": "string"},
                  "width": {"type": "number"}, "glow": {"type": "number"},
                  "style": {"type": "string", "enum": ["solid", "dashed", "dotted"]}, "tail": {"type": "number"},
                  "fadeTail": {"type": "boolean"}, "head": {"type": "boolean"},
                  "anchor": {"type": "string", "enum": ["world", "screen"]}, "layer_id": {"type": "string"}},
                 ["t", "x", "y"]),
            h_track_object, mutating=True, cost=("cpu",),
            label=lambda a: f"Веду брошенный предмет с {(_num(a.get('t'), 0.0) or 0.0):.1f} с",
        ),
    ]
