"""Инструменты кадра: раскладки и вебка, поиск аватара и его места в кадре, кадрирование кусков 9:16, формат ролика,
фон и хромакей.

Входят в общий реестр (``app/agent_tools.py``)."""

from __future__ import annotations

from typing import Any

from app.backgrounds import BACKGROUND_KINDS as BG_KINDS
from app.clip_settings import normalize_render_settings
from app.tool_core import (
    _apply_piece, _camera_of, _current_render, _layers_of, _num, _obj, _piece_index, _piece_len, _save_render,
    _shared_warning, _source_keyed, clip_timeline, Tool, ToolContext, ToolError,
)


# Стили фона для подсказок инструментов (app/backgrounds.py).
BG_KINDS_HINT = ("aurora (переливы, по умолчанию) | grid (неоновая перспективная сетка) | bokeh (блики) | "
                 "waves (светящиеся волны) | gradient | solid (статичные)")


def _framing_view(ctx: ToolContext, source: dict, render: dict) -> dict:
    """Как кадрируется формат клипа (9:16 и др.): какую долю ширины исходника видно и как это менять."""
    from app.formats import frame_size, settings_render_format, source_strategy
    from app.render import effective_preset
    from app.video_crop import window_share

    preset = effective_preset(ctx.store, render.get("preset_id")) or {}
    fmt = settings_render_format(render)
    if fmt == "9:16":
        share = window_share(source, int(preset.get("output_width") or 1080), int(preset.get("output_height") or 1920))
    else:
        share = window_share(source, *frame_size(fmt))
    out: dict = {
        "window_w": share,
        "note": (
            f"Окно {fmt} видит долю window_w ширины исходника; центр окна — focus куска (pieces[].focus, "
            "t — секунды от начала куска, x 0..1 — доля ширины исходника; пусто — центр 0.5). Менять — "
            "set_focus(piece, points) с cut:true на склейках планов (get_shots). Двое дальше друг от друга, "
            "чем window_w, в окно не влезут: фокус на говорящего со сменой на склейке/реплике или "
            "set_piece_fit (кусок шире окна, сверху и снизу фон). Камера (set_camera) только приближает, zoom ≥ 1."
        ),
    }
    if preset and not preset.get("smart_reframe"):
        out["warning"] = "у пресета выключен smart_reframe — фокус не действует, кадр по центру"
    avatar = known_avatar(ctx, source)
    if avatar:
        out["avatar"] = {**avatar, "note": (
            "аватар/вебка стримера в долях ВСЕГО кадра исходника. «Только аватар крупно» — set_layout "
            "mode avatar_full (аватар на весь кадр, вокруг размытый фон или set_backdrop), а не set_focus окна "
            "по исходнику: окно 9:16 ловит игру/ролик на экране")}
    elif (render.get("layout") or {}).get("mode") != "avatar_full":
        out["avatar_hint"] = ("стрим с аватаром/вебкой? detect_avatar найдёт его (Gemini по кадрам кусков) — потом "
                              "set_layout avatar_full")
    if fmt != "9:16" and source_strategy(fmt, source) == "fit":
        out["fit"] = (f"исходник уже кадра {fmt}: он целиком по высоте на размытой подложке, фокус не нужен "
                      "(см. «Форматы вывода»)")
    return out


def known_avatar(ctx: ToolContext, source: dict | None = None) -> dict | None:
    """Аватар клипа без вызова модели: раскладка avatar_full, найденный detect_avatar (кэш) или
    facecam источника. ``{cam, from}`` или None."""
    render = _current_render(ctx)
    layout = render.get("layout") or {}
    if layout.get("mode") == "avatar_full" and isinstance(layout.get("cam"), dict):
        return {"cam": layout["cam"], "from": "layout avatar_full"}
    try:
        from app.ai.avatar_box import cached_avatar

        found = cached_avatar(ctx.store, ctx.plan_id)
    except Exception:  # noqa: BLE001
        found = None
    if found and found.get("cam"):
        return {"cam": found["cam"], "from": "detect_avatar", **({"moves": True} if found.get("moves") else {})}
    if source is None:
        plan = ctx.store.get_clip_plan(ctx.plan_id, include_segments=False)
        source = ctx.store.get_source(int(plan["source_id"]))
    if source.get("facecam"):
        return {"cam": source["facecam"], "from": "facecam источника (detect-facecam)"}
    return None


def source_span_to_clip(pieces: list[dict], a: float, b: float) -> list[tuple[float, float]]:
    """Отрезок секунд ИСХОДНИКА → отрезки шкалы clip (по кускам clip_timeline, со скоростью куска)."""
    out = []
    for piece in pieces:
        s0, s1 = float(piece["start_sec"]), float(piece["end_sec"])
        lo, hi = max(a, s0), min(b, s1)
        if hi - lo <= 1e-3:
            continue
        speed = float(piece.get("speed") or 1.0) or 1.0
        c0 = float(piece["clip_start"]) + (lo - s0) / speed
        c1 = float(piece["clip_start"]) + (hi - s0) / speed
        out.append((round(c0, 2), round(c1, 2)))
    return out


def _place_view(pieces: list[dict], place: dict) -> dict | None:
    """Место аватара (секунды исходника) → то же на шкале clip: ``{from_t, to_t, view_t}``; вне клипа — None."""
    spans = source_span_to_clip(pieces, float(place["src_from"]), float(place["src_to"]))
    if not spans:
        return None
    longest = max(spans, key=lambda s: s[1] - s[0])
    return {"from_t": spans[0][0], "to_t": spans[-1][1], "view_t": round((longest[0] + longest[1]) / 2, 2),
            "clip_spans": [list(s) for s in spans]}


def _cam_bits(layout: dict, s0: float, s1: float) -> list[tuple[float, float, dict]]:
    """Рамки аватара по части исходника ``s0..s1``: ``[(a, b, {cam, video_scale})]``, секунды от начала части."""
    from app.stream_layout import avatar_cam_spans

    main, spans = avatar_cam_spans(layout, s0, s1)
    bits = [(0.0, s1 - s0, main)]
    for span in spans:  # перекрасить отрезки другой рамки
        look = {"cam": span["cam"], "video_scale": span.get("video_scale") or main.get("video_scale")}
        cut = []
        for a, b, old in bits:
            lo, hi = max(a, span["t0"]), min(b, span["t1"])
            if hi <= lo:
                cut.append((a, b, old))
                continue
            cut += [x for x in ((a, lo, old), (lo, hi, look), (hi, b, old)) if x[1] - x[0] > 1e-3]
        bits = cut
    return bits


def _same_cam(a: dict, b: dict) -> bool:
    return all(abs(float(a[k]) - float(b[k])) < 0.02 for k in ("x", "y", "w", "h"))


def avatar_face_zones(store: Any, clip_plan_id: int, render: dict | None = None) -> list[dict]:
    """Где лицо аватара в кадре avatar_full (опорный 1080×1920): ``[{t0, t1, rect: (x0, y0, x1, y1) px}]``
    по шкале clip. Рамка модели и лицо — из detect_avatar (место с той же рамкой cam), иначе — середина
    верхней части рамки (``avatar_box.face_of``). Не avatar_full или не 9:16 — пусто.

    QA 3 (клип 895): картинки-карточки 500 px на y 0.40 закрыли лицо Муры, add_layers и lint молчали."""
    from app.ai.avatar_box import cached_avatar, face_of
    from app.formats import settings_layers_format
    from app.stream_layout import avatar_geometry, normalize_layout

    if render is None:
        plan = store.get_clip_plan(int(clip_plan_id))
        render = normalize_render_settings(plan.get("render_settings") or {})
    layout = render.get("layout") or {}
    if layout.get("mode") != "avatar_full" or not isinstance(layout.get("cam"), dict):
        return []
    if settings_layers_format(render) != "9:16":
        return []
    lay = normalize_layout(layout)
    plan = store.get_clip_plan(int(clip_plan_id), include_segments=False)
    source = store.get_source(int(plan["source_id"]))
    src_w, src_h = source.get("width"), source.get("height")
    try:
        found = cached_avatar(store, int(clip_plan_id)) or {}
    except Exception:  # noqa: BLE001
        found = {}
    known = [p for p in [found, *(found.get("places") or [])] if isinstance(p, dict) and p.get("cam") and p.get("box")]
    W, H = 1080, 1920

    with_face = [p for p in known if isinstance(p.get("face"), dict)]

    def face_rect(look: dict, span: tuple[float, float]) -> tuple | None:
        cam = look["cam"]
        match = next((p for p in known if _same_cam(p["cam"], cam)), None)
        # Рамка, поправленная под лицо (set_layout: шире/по центру лица), — лицо того места по времени.
        timed = None if match else _face_for_cam(with_face, cam, span)
        if match:
            face = face_of(match["box"], match.get("face"))
        elif timed:
            face = face_of({}, timed)
        else:  # рамка руками: модель — сама рамка
            face = face_of({"x0": cam["x"], "y0": cam["y"], "x1": cam["x"] + cam["w"], "y1": cam["y"] + cam["h"]})
        geo = avatar_geometry({**lay, "cam": cam, "video_scale": look.get("video_scale") or lay.get("video_scale")},
                              W, H, src_w, src_h)
        px = lambda sx: geo["x"] + (sx - cam["x"]) / max(1e-6, cam["w"]) * geo["w"]  # noqa: E731
        py = lambda sy: geo["y"] + (sy - cam["y"]) / max(1e-6, cam["h"]) * geo["h"]  # noqa: E731
        rect = (max(0.0, px(face["x0"])), max(0.0, py(face["y0"])), min(W, px(face["x1"])), min(H, py(face["y1"])))
        return rect if rect[2] - rect[0] > 20 and rect[3] - rect[1] > 20 else None

    zones: list[dict] = []
    for piece in clip_timeline(store, int(clip_plan_id))["pieces"]:
        s0, s1 = float(piece["start_sec"]), float(piece["end_sec"])
        for a, b, look in _cam_bits(lay, s0, s1):
            rect = face_rect(look, (s0 + a, s0 + b))
            if not rect:
                continue
            rect = tuple(round(v, 1) for v in rect)
            for t0, t1 in source_span_to_clip([piece], s0 + a, s0 + b):
                if zones and zones[-1]["rect"] == rect and abs(zones[-1]["t1"] - t0) < 0.05:
                    zones[-1]["t1"] = t1
                else:
                    zones.append({"t0": t0, "t1": t1, "rect": rect})
    return zones


def _known_avatar_places(store: Any, clip_plan_id: int) -> list[dict]:
    """Места аватара из кэша detect_avatar с лицом: ``[{cam, box, face, src_from?, src_to?}]`` (без вызова модели)."""
    try:
        from app.ai.avatar_box import cached_avatar

        found = cached_avatar(store, int(clip_plan_id)) or {}
    except Exception:  # noqa: BLE001
        return []
    return [p for p in [*(found.get("places") or []), found] if isinstance(p, dict) and p.get("cam")
            and isinstance(p.get("face"), dict)]


def _face_for_cam(known: list[dict], cam: dict, span: tuple[float, float] | None) -> dict | None:
    """Лицо аватара (доли исходника) для рамки ``cam`` на отрезке исходника ``span``: место detect_avatar с той
    же рамкой, иначе место, что дольше всех лежит на отрезке, — если его лицо хоть краем в рамке (рамка руками
    около аватара). Не нашлось — None (лицо не знаем, рамку не трогаем)."""
    same = next((p for p in known if _same_cam(p["cam"], cam)), None)
    if same:
        return same["face"]
    if span is None:
        return None
    a, b = span
    timed = [(min(b, float(p["src_to"])) - max(a, float(p["src_from"])), p) for p in known
             if p.get("src_from") is not None and p.get("src_to") is not None]
    timed = [(o, p) for o, p in timed if o > 0]
    if not timed:
        return None
    face = max(timed, key=lambda item: item[0])[1]["face"]
    rect = (float(cam["x"]), float(cam["y"]), float(cam["x"]) + float(cam["w"]), float(cam["y"]) + float(cam["h"]))
    ix = min(rect[2], float(face["x1"])) - max(rect[0], float(face["x0"]))
    iy = min(rect[3], float(face["y1"])) - max(rect[1], float(face["y0"]))
    return face if ix > 0 and iy > 0 else None


_SIDE_RU = {"left": "левый край", "right": "правый край", "top": "верх", "bottom": "низ"}


def fit_avatar_faces(store: Any, clip_plan_id: int, layout: dict, render: dict, source: dict,
                     auto_scale: bool = True) -> tuple[dict, list[str]]:
    """avatar_full: каждая рамка (``cam`` и ``cams``) — с лицом аватара целиком и с запасом в КАДРЕ
    (``stream_layout.fit_avatar_face``): рамка, срезающая лицо, расширяется до него, крупность, при которой
    бока рамки с лицом уходят за кадр, — меньше. → (раскладка, предупреждения агенту).

    QA 5 (клип 897): рамку второго места сузили ради чата, крупность 1.5 срезала её бока — правый глаз за кадром
    20 с ролика. Лицо — из detect_avatar (кэш); не знаем лица — рамка как есть."""
    from app.ai.avatar_box import face_zone
    from app.formats import frame_size, settings_render_format
    from app.stream_layout import avatar_auto_scale, fit_avatar_face, normalize_layout

    known = _known_avatar_places(store, clip_plan_id)
    if not known:
        return layout, []
    width, height = frame_size(settings_render_format(render))
    src_w, src_h = source.get("width"), source.get("height")
    ay = float(layout.get("avatar_y", 0.5))
    base_scale = float(layout.get("video_scale") or 1.0)
    notes: list[str] = []

    def fit(cam: dict, scale: float, span: tuple[float, float] | None, name: str) -> tuple[dict, float]:
        face = _face_for_cam(known, cam, span)
        if not face:
            return cam, scale
        auto = auto_scale and abs(scale - avatar_auto_scale(cam, width, height, src_w, src_h)) < 0.01
        new_cam, new_scale, how = fit_avatar_face(cam, face_zone(face), scale, width, height, src_w, src_h, ay, auto)
        if how:
            from app.stream_layout import avatar_visible_rect, face_cut_sides

            cut = face_cut_sides(avatar_visible_rect({"cam": cam, "video_scale": scale, "avatar_y": ay}, width, height,
                                                     src_w, src_h), face_zone(face))
            what = ", ".join(_SIDE_RU[s] for s in cut) or "край"
            fix = []
            if "widened" in how:
                fix.append("рамка расширена до лица")
            if "centered" in how:
                fix.append("рамка по центру лица")
            if new_scale < scale - 1e-3:
                fix.append(f"video_scale {scale:g} → {new_scale:g}")
            notes.append(f"{name}: лицо аватара не помещалось в кадр целиком ({what} лица с запасом за кадром) — "
                         f"{', '.join(fix) or 'поправлено'}. Лицо режется — никогда: чат/интерфейс в рамке лучше "
                         "среза лица (прикрой его плашкой, а не рамкой)")
        return new_cam, new_scale

    out = dict(layout)
    cams = [dict(e) for e in layout.get("cams") or []]
    for index, entry in enumerate(cams):
        scale = float(entry.get("video_scale") or base_scale)
        cam, new_scale = fit(entry["cam"], scale, (float(entry["from"]), float(entry["to"])), f"cams[{index}]")
        if cam != entry["cam"] or abs(new_scale - scale) > 1e-4:
            entry["cam"] = cam
            entry["video_scale"] = new_scale
        cams[index] = entry
    if cams:
        out["cams"] = cams
    if not cams:
        plan = store.get_clip_plan(int(clip_plan_id))
        spans = [(float(s["start_sec"]), float(s["end_sec"])) for s in plan.get("segments") or []]
        whole = (min(a for a, _b in spans), max(b for _a, b in spans)) if spans else None
        cam, new_scale = fit(layout["cam"], base_scale, whole, "cam")
    else:
        # Основа вне отрезков: та же рамка, что у места detect_avatar, — без отдельного предупреждения.
        before = len(notes)
        cam, new_scale = fit(layout["cam"], base_scale, None, "cam")
        del notes[before:]
    out["cam"], out["video_scale"] = cam, new_scale
    return normalize_layout(out), notes


def avatar_face_report(store: Any, clip_plan_id: int, render: dict | None = None) -> list[dict]:
    """Факт для сверки отчёта: лицо аватара (бокс от detect_avatar) целиком в кадре на каждом месте avatar_full —
    ``[{from_t, to_t, face_whole: bool, cut: [стороны]}]`` по шкале clip; лица не знаем — место без факта.
    QA 5 (клип 897): «проверил глазами — лицо в кадре», а правый глаз был за кадром 20 с."""
    from app.formats import frame_size, settings_render_format
    from app.stream_layout import avatar_visible_rect, face_cut_sides, normalize_layout

    if render is None:
        plan = store.get_clip_plan(int(clip_plan_id))
        render = normalize_render_settings(plan.get("render_settings") or {})
    layout = render.get("layout") or {}
    if layout.get("mode") != "avatar_full" or not isinstance(layout.get("cam"), dict):
        return []
    known = _known_avatar_places(store, clip_plan_id)
    if not known:
        return []
    lay = normalize_layout(layout)
    plan = store.get_clip_plan(int(clip_plan_id), include_segments=False)
    source = store.get_source(int(plan["source_id"]))
    width, height = frame_size(settings_render_format(render))
    rows: list[dict] = []
    for piece in clip_timeline(store, int(clip_plan_id))["pieces"]:
        s0, s1 = float(piece["start_sec"]), float(piece["end_sec"])
        for a, b, look in _cam_bits(lay, s0, s1):
            face = _face_for_cam(known, look["cam"], (s0 + a, s0 + b))
            if not face:
                continue
            visible = avatar_visible_rect({**lay, "cam": look["cam"], "video_scale": look.get("video_scale")
                                           or lay.get("video_scale")}, width, height, source.get("width"),
                                          source.get("height"))
            cut = face_cut_sides(visible, face)
            for t0, t1 in source_span_to_clip([piece], s0 + a, s0 + b):
                if rows and rows[-1]["cut"] == cut and abs(rows[-1]["to_t"] - t0) < 0.05:
                    rows[-1]["to_t"] = round(t1, 2)
                else:
                    rows.append({"from_t": round(t0, 2), "to_t": round(t1, 2), "face_whole": not cut, "cut": cut})
    return rows


MAX_CAM_CHECKS = 4        # ручные рамки: не больше стольких кадров Gemini за один set_layout
AVATAR_HIT_SHARE = 0.05   # рамка «видит» место аватара, если накрывает хотя бы эту долю его рамки


def _avatar_bits(store: Any, clip_plan_id: int, render: dict) -> list[dict]:
    """avatar_full по кускам клипа: ``[{cam, sa, sb, piece}]`` — рамка на отрезке исходника ``sa..sb``."""
    from app.stream_layout import normalize_layout

    layout = render.get("layout") or {}
    if layout.get("mode") != "avatar_full" or not isinstance(layout.get("cam"), dict):
        return []
    lay = normalize_layout(layout)
    out = []
    for piece in clip_timeline(store, int(clip_plan_id))["pieces"]:
        s0, s1 = float(piece["start_sec"]), float(piece["end_sec"])
        for a, b, look in _cam_bits(lay, s0, s1):
            out.append({"cam": look["cam"], "sa": s0 + a, "sb": s0 + b, "piece": piece})
    return out


def _rect_hits(cam: dict, box: dict) -> bool:
    rect = (float(cam["x"]), float(cam["y"]), float(cam["x"]) + float(cam["w"]), float(cam["y"]) + float(cam["h"]))
    ix = min(rect[2], float(box["x1"])) - max(rect[0], float(box["x0"]))
    iy = min(rect[3], float(box["y1"])) - max(rect[1], float(box["y0"]))
    area = max(1e-6, (float(box["x1"]) - float(box["x0"])) * (float(box["y1"]) - float(box["y0"])))
    return ix > 0 and iy > 0 and ix * iy >= AVATAR_HIT_SHARE * area


def avatar_absent_spans(store: Any, clip_plan_id: int, render: dict | None = None) -> list[dict]:
    """Где в кадре avatar_full аватара нет: ``[{from_t, to_t, cam, why, what?}]`` по шкале clip.

    ``why`` — ``places``: рамка не накрывает ни одно место аватара, найденное detect_avatar (кэш, по времени);
    ``vision``: проверка Gemini-зрением кропа рамки (app/ai/avatar_check.py, кэш) сказала «аватара нет» — там же
    ``what`` (что видно вместо него). QA 6 (клип 898): рамка на плеер YouTube 23 с, lint молчал."""
    from app.ai import avatar_check
    from app.ai.avatar_box import cached_avatar

    if render is None:
        plan = store.get_clip_plan(int(clip_plan_id))
        render = normalize_render_settings(plan.get("render_settings") or {})
    bits = _avatar_bits(store, clip_plan_id, render)
    if not bits:
        return []
    plan = store.get_clip_plan(int(clip_plan_id), include_segments=False)
    source = store.get_source(int(plan["source_id"]))
    try:
        found = cached_avatar(store, int(clip_plan_id)) or {}
    except Exception:  # noqa: BLE001
        found = {}
    places = [p for p in found.get("places") or [] if isinstance(p, dict) and isinstance(p.get("box"), dict)
              and p["box"].get("x1") is not None]
    if not places and isinstance(found.get("box"), dict) and found["box"].get("x1") is not None:
        places = [found]
    rows: list[dict] = []
    for bit in bits:
        cam, sa, sb = bit["cam"], bit["sa"], bit["sb"]
        why, what = None, ""
        checks = avatar_check.lookup(source, cam, sa, sb)
        verdicts = [r for r in checks if isinstance(r.get("avatar"), bool)]
        if verdicts and not any(r["avatar"] for r in verdicts):
            why, what = "vision", str(verdicts[-1].get("what") or "")
        elif places and not any(r["avatar"] for r in verdicts):
            timed = [p for p in places if p.get("src_from") is not None and p.get("src_to") is not None
                     and min(sb, float(p["src_to"])) - max(sa, float(p["src_from"])) > 1e-3]
            if not any(_rect_hits(cam, p["box"]) for p in (timed or places)):
                why = "places"
        if not why:
            continue
        for t0, t1 in source_span_to_clip([bit["piece"]], sa, sb):
            last = rows[-1] if rows else None
            if last and last["why"] == why and _same_cam(last["cam"], cam) and abs(last["to_t"] - t0) < 0.05:
                last["to_t"] = round(t1, 2)
            else:
                rows.append({"from_t": round(t0, 2), "to_t": round(t1, 2), "cam": cam, "why": why,
                             **({"what": what} if what else {})})
    return rows


def _absent_text(row: dict) -> str:
    cam = row["cam"]
    head = (f"{row['from_t']:.1f}–{row['to_t']:.1f} с (cam x{float(cam['x']):.2f} y{float(cam['y']):.2f} "
            f"w{float(cam['w']):.2f} h{float(cam['h']):.2f})")
    if row["why"] == "vision":
        return head + ": в рамке НЕТ аватара — Gemini видит " + (f"«{row['what']}»" if row.get("what") else "не аватар")
    return head + ": рамка не накрывает ни одно место аватара, найденное detect_avatar"


def check_manual_cams(ctx: ToolContext, render: dict, source: dict) -> list[dict]:
    """Рамки руками avatar_full: по кадру Gemini на каждое место (до MAX_CAM_CHECKS, кэш по рамке и секунде) —
    ``[{cam, t, avatar, what?, skipped?}]``."""
    from app.ai.avatar_check import check_cam, same_cam

    spots: list[tuple[dict, float, float]] = []
    for bit in _avatar_bits(ctx.store, ctx.plan_id, render):
        for i, (cam, a, b) in enumerate(spots):
            if same_cam(cam, bit["cam"]):
                if bit["sb"] - bit["sa"] > b - a:
                    spots[i] = (cam, bit["sa"], bit["sb"])
                break
        else:
            spots.append((bit["cam"], bit["sa"], bit["sb"]))
    out = []
    for cam, a, b in spots[:MAX_CAM_CHECKS]:
        out.append(check_cam(ctx.store, source, cam, (a + b) / 2))
    return out


def avatar_places(ctx: ToolContext) -> list[dict]:
    """Где аватар по ходу клипа, если мест больше одного: ``[{place, cam, from_t, to_t, view_t, src_from,
    src_to}]`` (шкала clip). Из раскладки avatar_full с ``cams``, иначе из найденного detect_avatar.
    Одно место — пустой список."""
    render = _current_render(ctx)
    layout = render.get("layout") or {}
    pieces = clip_timeline(ctx.store, ctx.plan_id)["pieces"]
    rows: list[dict] = []
    if layout.get("mode") == "avatar_full" and layout.get("cams"):
        for piece in pieces:
            s0, s1 = float(piece["start_sec"]), float(piece["end_sec"])
            for a, b, look in _cam_bits(layout, s0, s1):
                cam = look["cam"]
                if rows and rows[-1]["cam"] == cam and abs(rows[-1]["src_to"] - (s0 + a)) < 0.05:
                    rows[-1]["src_to"] = round(s0 + b, 3)
                else:
                    rows.append({"cam": cam, "src_from": round(s0 + a, 3), "src_to": round(s0 + b, 3)})
        cams = []
        for row in rows:
            if row["cam"] not in cams:
                cams.append(row["cam"])
            row["place"] = cams.index(row["cam"])
        if len(cams) < 2:
            return []
    else:
        try:
            from app.ai.avatar_box import cached_avatar

            found = cached_avatar(ctx.store, ctx.plan_id) or {}
        except Exception:  # noqa: BLE001
            found = {}
        rows = [dict(p) for p in found.get("places") or [] if isinstance(p, dict) and p.get("src_from") is not None]
        if not found.get("moves") or len(rows) < 2:
            return []
    out = []
    for row in rows:
        view = _place_view(pieces, row)
        if view:
            out.append({"place": row.get("place"), "cam": row["cam"], **view,
                        "src_from": row["src_from"], "src_to": row["src_to"]})
    return out if len({repr(p["cam"]) for p in out}) > 1 else []


def _places_text(places: list[dict]) -> str:
    return "; ".join(f"{p['from_t']:.1f}–{p['to_t']:.1f} с (cam x{p['cam']['x']:.2f} y{p['cam']['y']:.2f} "
                     f"w{p['cam']['w']:.2f} h{p['cam']['h']:.2f})" for p in places)


def h_detect_avatar(ctx: ToolContext, args: dict) -> dict:
    """Аватар стримера (VTuber/PNGtuber/вебка) на кусках клипа — Gemini по целым кадрам исходника."""
    from app.ai.avatar_box import AvatarAnswerError, detect_avatar

    try:
        found = detect_avatar(ctx.store, ctx.plan_id, refresh=bool(args.get("refresh")))
    except AvatarAnswerError as exc:
        # QA 6 (клип 898): ответ Gemini не разобрался, а агенту сказали «аватара нет — задай cam руками» — он 12 раз
        # угадывал рамку и поставил её на плеер YouTube. Сбой сервиса — это не «аватара нет».
        return {"ok": False, "status": "service_failed", "cam": None,
                "error": f"detect_avatar: сервис не смог получить рамки от Gemini ({exc}). Это СБОЙ сервиса, а не "
                         "«аватара нет»",
                "problems": ["повтори detect_avatar(refresh=true) один раз; снова сбой — НЕ угадывай рамку руками: "
                             "доделай остальное и в отчёте спроси владельца, где аватар (секунда и угол кадра)"]}
    except ValueError as exc:
        raise ToolError(f"detect_avatar: {exc}") from exc
    if not found.get("cam"):
        return {"ok": True, "status": "no_avatar", "cam": None, "frames": found.get("frames"),
                "note": "Gemini ответил: аватара/вебки на кадрах клипа нет. Посмотри source_contact_sheet; аватар там "
                        "есть — set_layout cam руками (сервис проверит рамку), нет или не уверен — спроси владельца"}
    out = {"ok": True, "cam": found["cam"], "box": found.get("box"), "frames": found.get("frames"),
           "frames_with_avatar": found.get("frames_with_avatar"),
           "next": "set_layout mode avatar_full (cam возьмётся сам) → view_frame"}
    if found.get("moves"):
        pieces = clip_timeline(ctx.store, ctx.plan_id)["pieces"]
        places = []
        for place in found.get("places") or []:
            view = _place_view(pieces, place) if place.get("src_from") is not None else None
            if view:
                places.append({"place": place.get("place"), "cam": place["cam"], **view,
                               "src_from": place["src_from"], "src_to": place["src_to"]})
        out["places"] = places
        looks = " ".join(f"t={p['view_t']:g}" for p in places)
        out["warnings"] = [
            f"аватар в клипе в {found.get('place_count')} местах (шкала clip): {_places_text(places)}. "
            "set_layout mode avatar_full без cam сам поставит рамку по отрезкам (layout.cams) — не задавай один "
            f"cam на весь клип. Потом view_frame в КАЖДОМ отрезке ({looks}): без этого отчёт не примут"]
        out["next"] = f"set_layout mode avatar_full (рамки по отрезкам возьмутся сами) → view_frame {looks}"
    chat = [w for p in [found, *(found.get("places") or [])] for w in p.get("ui_left") or []]
    if chat:
        where = "; ".join(f"x{w['x0']:.2f}–{w['x1']:.2f} y{w['y0']:.2f}–{w['y1']:.2f}" for w in chat[:3])
        out.setdefault("warnings", []).append(
            f"в рамке аватара остаётся окно чата/интерфейса поверх модели ({where}, доли исходника): срезать его — "
            "значит резать лицо. Посмотри view_frame; можно задать cam руками или прикрыть угол своей плашкой")
    if args.get("save"):
        plan = ctx.store.get_clip_plan(ctx.plan_id, include_segments=False)
        ctx.before_change()
        ctx.store.update_source(int(plan["source_id"]), facecam=found["cam"])
        out["saved"] = "facecam источника"
    return out


CAM_CROP_WARN = 0.15  # срезается больше этой доли вебки — предупреждаем
CAM_BIG_W = 0.30
CAM_BIG_H = 0.45


def facecam_warnings(layout: dict, source: dict, fmt: Any = None) -> list[str]:
    """Вебка в раскладке: не режется ли под пропорции полосы и не захвачен ли лишний оверлей.

    Агент стримов: сохранённая facecam (0.63/0.4/0.37/0.6) захватила вшитый чат, а полоса
    split шире вебки — компоновщик заполняет полосу и срезает верх и низ: аватар до лба.
    ``fmt`` — формат вывода: полоса во всю ширину кадра формата; в 16:9 полосы нет (вебка
    окошком в углу своей пропорции — срезать нечего)."""
    from app.formats import frame_size, is_landscape, settings_format
    from app.stream_layout import normalize_layout

    layout = normalize_layout(layout)
    cam = layout["cam"]
    out = []
    if cam["w"] > CAM_BIG_W or cam["h"] > CAM_BIG_H:
        out.append(f"вебка большая ({cam['w']:.2f}×{cam['h']:.2f} кадра исходника) — вебка стримера обычно меньше: "
                   "проверь source_contact_sheet / view_frame, не захвачен ли вшитый чат или оверлей; поправь cam")
    fmt = settings_format(fmt)
    if layout["mode"] != "split" or is_landscape(fmt):
        return out
    frame_w, frame_h = frame_size(fmt)
    src_w, src_h = float(source.get("width") or 16), float(source.get("height") or 9)
    cam_aspect = (cam["w"] * src_w) / max(1e-6, cam["h"] * src_h)
    strip_aspect = frame_w / max(1.0, layout["ratio"] * frame_h)
    fit_ratio = round(min(0.6, max(0.2, frame_w / cam_aspect / frame_h)), 3)
    if cam_aspect < strip_aspect:
        loss = 1 - cam_aspect / strip_aspect
        if loss > CAM_CROP_WARN:
            out.append(f"полоса вебки шире самой вебки: срежется {loss:.0%} её высоты (сверху и снизу — лицо «до лба»). "
                       f"Под пропорции вебки — ratio {fit_ratio}, или шире cam (w) в исходнике")
    else:
        loss = 1 - strip_aspect / cam_aspect
        if loss > CAM_CROP_WARN:
            out.append(f"полоса вебки уже самой вебки: срежется {loss:.0%} её ширины по бокам. Под пропорции вебки — "
                       f"ratio {fit_ratio}, или выше cam (h) в исходнике")
    return out


def _focus_all(ctx: ToolContext, x: float, piece: int | None = None) -> list[str]:
    """Неподвижный фокус окна игры x на всех кусках (или на одном) — как manual_x в файле клипа."""
    from app.clip_spec import SpecError, apply_spec, export_spec, validate_spec

    spec = export_spec(ctx.store, ctx.plan_id)
    pieces = []
    for i, item in enumerate(spec["pieces"]):
        item = dict(item)
        item.pop("fit", None)
        if piece is None or i == piece:
            item["focus"] = [{"t": 0.0, "x": round(float(x), 4)}]
        else:
            item.pop("focus", None)
        pieces.append(item)
    try:
        validate_spec(ctx.store, ctx.plan_id, {**spec, "pieces": pieces})
    except SpecError as exc:
        raise ToolError("фокус окна игры не принят", exc.problems) from exc
    ctx.before_change()
    _fresh, changes = apply_spec(ctx.store, ctx.plan_id, {**spec, "pieces": pieces})
    return changes


def h_set_layout(ctx: ToolContext, args: dict) -> dict:
    """Раскладка кадра: single, split (вебка+игра), pip_cam, meme (видео сверху, фон снизу)."""
    from app.stream_layout import LAYOUT_MODES, normalize_layout

    if args.get("mode") is not None and args["mode"] not in LAYOUT_MODES:
        raise ToolError(f"mode: одно из {', '.join(LAYOUT_MODES)}")
    game_x = _num(args.get("game_x"), _num(args.get("manual_x")))
    if game_x is not None and not 0.0 <= game_x <= 1.0:
        raise ToolError("game_x — центр окна игры в долях ширины исходника, 0..1")
    piece = args.get("piece")
    if piece is not None:
        piece = _piece_index({"piece": piece}, len(clip_timeline(ctx.store, ctx.plan_id)["pieces"]))
    layout_args = {k: v for k, v in args.items() if v is not None and k not in ("game_x", "manual_x", "piece")}
    render = _current_render(ctx)
    source = ctx.store.get_source(int(ctx.store.get_clip_plan(ctx.plan_id, include_segments=False)["source_id"]))
    took_facecam = False
    if (layout_args.get("mode") in ("split", "pip_cam") and "cam" not in layout_args
            and (render.get("layout") or {}).get("mode") not in ("split", "pip_cam") and source.get("facecam")):
        # Вебка, найденная/заданная у источника, — а не угол по умолчанию.
        layout_args["cam"] = source["facecam"]
        took_facecam = True
    avatar_from = None
    if layout_args.get("mode") == "avatar_full":
        avatar_from = _avatar_layout_args(ctx, render, source, layout_args)
    merged = {**(render.get("layout") or {}), **layout_args}
    face_notes: list[str] = []
    new_layout = normalize_layout(merged)
    if new_layout["mode"] == "avatar_full":
        new_layout, face_notes = fit_avatar_faces(ctx.store, ctx.plan_id, new_layout, render, source,
                                                  auto_scale="video_scale" not in args)
    fresh = _save_render(ctx, {**render, "layout": new_layout})
    out: dict = {"ok": True, "layout": fresh["layout"]}
    if face_notes:
        out.setdefault("warnings", []).extend(face_notes)
    if took_facecam:
        out["cam_from"] = "facecam источника (detect-facecam) — проверь view_frame"
    if game_x is not None:
        out["focus_changes"] = _focus_all(ctx, game_x, piece)
        out["game_x"] = round(game_x, 4)
    mode = fresh["layout"]["mode"]
    if mode == "avatar_full":
        out["cam_from"] = avatar_from or "cam из прежней раскладки"
        out["note"] = ("в кадре только аватар: cam (доли всего исходника) шириной video_scale ширин кадра, центр по "
                       "высоте avatar_y; вокруг — размытый тот же аватар (bg_blur) или фон bg. ОБЯЗАТЕЛЬНО view_frame: "
                       "модель целиком и крупно, игры/браузера нет; мелко — video_scale больше, режет — меньше")
        places = avatar_places(ctx)
        if places:
            looks = " ".join(f"t={p['view_t']:g}" for p in places)
            out["places"] = places
            if fresh["layout"].get("cams"):
                out["note"] += (f". Аватар переезжает — рамка по отрезкам (layout.cams): {_places_text(places)}. "
                                f"view_frame в КАЖДОМ отрезке ({looks}) — без этого отчёт не примут")
            else:
                out.setdefault("warnings", []).append(
                    f"detect_avatar видел аватар в {len({repr(p['cam']) for p in places})} местах: {_places_text(places)}, "
                    "а cam один на весь клип — в части отрезков аватара в кадре не будет. Рамка по отрезкам: "
                    f"set_layout mode avatar_full cams \"auto\" (или cams [{{from_t, to_t, cam}}]). view_frame: {looks}")
        manual = "cam" in args or isinstance(args.get("cams"), list)
        if manual:
            # QA 6 (клип 898): рамка руками на плеер YouTube — сервис принял её молча. Кроп рамки — Gemini-зрению.
            try:
                checks = check_manual_cams(ctx, fresh, source)
            except Exception as exc:  # noqa: BLE001 - проверка не роняет раскладку
                checks = [{"avatar": None, "skipped": f"проверка «аватар в рамке» не удалась: {str(exc)[:120]}"}]
            if checks:
                out["avatar_check"] = [{k: c[k] for k in ("cam", "t", "avatar", "what", "skipped") if k in c}
                                       for c in checks]
        try:
            # По кэшу (без новых вызовов модели) — и для рамки из прежней раскладки: та же рамка мимо аватара.
            absent = avatar_absent_spans(ctx.store, ctx.plan_id, fresh)
        except Exception:  # noqa: BLE001
            absent = []
        if absent:
            out["avatar_miss"] = True
            out.setdefault("warnings", []).append(
                "аватара в кадре НЕТ: " + "; ".join(_absent_text(r) for r in absent[:4])
                + ". Рамка смотрит не туда — source_contact_sheet покажет, где аватар на этих секундах; не перебирай "
                  "рамки наугад: 2–3 неудачи — остановись и спроси владельца")
    elif mode == "meme":
        out["note"] = ("видео сверху во всю ширину; под ним свободный фон — клади туда картинки (image) и титры, "
                       "проверь view_frame")
    elif mode in ("split", "pip_cam"):
        out["note"] = ("камера (set_camera) двигает только игру — полоса/окошко вебки стоят на месте; что из игры в "
                       "кадре — focus кусков (game_x здесь — один x на все куски, set_focus — трек по куску). "
                       "Проверь view_frame: лицо в полосе целиком, хук и субтитры не на лице (positions в get_clip)")
        warnings = facecam_warnings(fresh["layout"], source, fresh.get("format"))
        if warnings:
            out["warnings"] = warnings
    return out


def _clip_to_source_t(pieces: list[dict], t: float) -> float:
    """Секунда шкалы clip → секунда исходника (кусок, где она лежит; за краями — край)."""
    for piece in pieces:
        length = _piece_len(piece)
        if t <= float(piece["clip_start"]) + length + 1e-6 or piece is pieces[-1]:
            local = min(max(0.0, t - float(piece["clip_start"])), length)
            return round(float(piece["start_sec"]) + local * (float(piece.get("speed") or 1.0) or 1.0), 3)
    return round(t, 3)


def _clip_time_cams(ctx: ToolContext, raw: list) -> list[dict]:
    """``cams`` из set_layout: отрезок — ``from``/``to`` (секунды исходника) или ``from_t``/``to_t``
    (шкала clip, переводим в исходник); рамка — ``cam`` или x/y/w/h в самой записи."""
    pieces = clip_timeline(ctx.store, ctx.plan_id)["pieces"]
    out = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ToolError(f"cams[{index}]: нужен объект {{from_t, to_t, cam: {{x, y, w, h}}}}")
        entry = dict(item)
        for key, src_key in (("from_t", "from"), ("to_t", "to")):
            if entry.get(src_key) is None and _num(entry.get(key)) is not None:
                entry[src_key] = _clip_to_source_t(pieces, float(_num(entry[key])))
            entry.pop(key, None)
        if _num(entry.get("from")) is None or _num(entry.get("to")) is None:
            raise ToolError(f"cams[{index}]: нужен отрезок from_t/to_t (шкала clip) или from/to (секунды исходника)")
        out.append(entry)
    return out


def _avatar_layout_args(ctx: ToolContext, render: dict, source: dict, layout_args: dict) -> str:
    """avatar_full: откуда cam (аргумент → прежняя раскладка → detect_avatar → facecam источника), крупность
    и фон по умолчанию. Фон: bg/bg_blur из аргументов; иначе фон set_backdrop клипа, если он задан; иначе
    размытый тот же аватар."""
    from app.formats import frame_size, settings_render_format
    from app.stream_layout import avatar_auto_scale, normalize_cam

    old = render.get("layout") or {}
    where = "cam из аргументов"
    cams_auto = layout_args.get("cams") == "auto"
    if cams_auto:
        layout_args.pop("cams")
    elif isinstance(layout_args.get("cams"), list):
        layout_args["cams"] = _clip_time_cams(ctx, layout_args["cams"])
        try:
            from app.ai.avatar_box import cached_avatar, snap_holds

            # Граница руками у наплыва, найденного detect_avatar, — на его конец со стоп-кадром (без чужой сцены).
            layout_args["cams"] = snap_holds(layout_args["cams"], (cached_avatar(ctx.store, ctx.plan_id) or {})
                                             .get("places") or [])
        except Exception:  # noqa: BLE001 - без кэша — границы как заданы
            pass
        where = "рамки по отрезкам из аргументов (cams)"
    elif "cam" in layout_args and old.get("cams"):
        layout_args["cams"] = []  # один cam из аргументов — на весь клип (прежние рамки по отрезкам сняты)
    if "cam" not in layout_args and "cams" not in layout_args:
        if old.get("mode") == "avatar_full" and isinstance(old.get("cam"), dict) and not cams_auto:
            layout_args["cam"], where = old["cam"], "cam из прежней раскладки avatar_full"
        else:
            found = None
            failed = None
            try:
                from app.ai.avatar_box import AvatarAnswerError, detect_avatar

                found = detect_avatar(ctx.store, ctx.plan_id)
            except AvatarAnswerError as exc:
                failed = str(exc)[:160]
                where = f"detect_avatar не смог (сбой ответа Gemini: {failed})"
            except Exception as exc:  # noqa: BLE001 - нет Gemini/файла — пробуем facecam
                where = f"detect_avatar не смог: {str(exc)[:120]}"
            if found and found.get("cam"):
                layout_args["cam"], where = found["cam"], "аватар найден detect_avatar (Gemini по кадрам кусков)"
                places = [p for p in found.get("places") or [] if p.get("src_from") is not None]
                if found.get("moves") and len(places) > 1:
                    # Аватар переезжает (QA 02.11, клип 892): рамка по отрезкам исходника, а не одна на клип.
                    from app.ai.avatar_box import cams_from_places

                    # Наплыв между местами — стоп-кадр старой сцены до конца наплыва (QA 5, клип 897).
                    layout_args["cams"] = cams_from_places(places)
                    where = (f"аватар найден detect_avatar в {found.get('place_count')} местах — рамка по отрезкам "
                             "(layout.cams)")
            elif source.get("facecam"):
                layout_args["cam"], where = source["facecam"], "facecam источника (detect-facecam)"
            elif failed:
                raise ToolError(f"avatar_full: detect_avatar не смог получить рамки от Gemini ({failed}) — это сбой "
                                "сервиса, а не «аватара нет»",
                                ["повтори detect_avatar(refresh=true) один раз; снова сбой — не угадывай рамку, в "
                                 "отчёте спроси владельца, где аватар"])
            else:
                raise ToolError("avatar_full: не знаю, где аватар — detect_avatar не нашёл, facecam у источника нет",
                                ["передай cam {x, y, w, h} — прямоугольник аватара в долях всего кадра исходника "
                                 "(source_contact_sheet покажет кадры)"])
    if "cam" not in layout_args and layout_args.get("cams"):
        first = layout_args["cams"][0]
        layout_args["cam"] = first["cam"] if isinstance(first.get("cam"), dict) else \
            {k: first[k] for k in ("x", "y", "w", "h") if k in first} or old.get("cam")
    width, height = frame_size(settings_render_format(render))
    if "video_scale" not in layout_args and old.get("mode") != "avatar_full":
        layout_args["video_scale"] = avatar_auto_scale(normalize_cam(layout_args.get("cam") or old.get("cam")), width,
                                                       height, source.get("width"), source.get("height"))
        for entry in layout_args.get("cams") or []:
            # Крупность по умолчанию — своя у каждой рамки (угол-вебка и крупный аватар разной пропорции).
            if isinstance(entry, dict) and isinstance(entry.get("cam"), dict) and entry.get("video_scale") is None:
                entry["video_scale"] = avatar_auto_scale(normalize_cam(entry["cam"]), width, height,
                                                         source.get("width"), source.get("height"))
    elif "video_scale" in layout_args:
        # Крупность задана явно — одна на все рамки.
        cams = layout_args.get("cams") if "cams" in layout_args else old.get("cams")
        if cams:
            layout_args["cams"] = [{k: v for k, v in e.items() if k != "video_scale"} for e in cams if isinstance(e, dict)]
    if "bg" not in layout_args and "bg_blur" not in layout_args and old.get("mode") != "avatar_full":
        from app.chroma_key import normalize_backdrop

        # Фон, заданный set_backdrop (не фон по умолчанию), — под аватар; иначе размытый аватар.
        if render.get("backdrop") and normalize_backdrop(render["backdrop"]) != normalize_backdrop(None):
            layout_args["bg"], layout_args["bg_blur"] = render["backdrop"], False
    elif "bg" in layout_args and "bg_blur" not in layout_args:
        layout_args["bg_blur"] = False
    return where


def _format_view(render: dict) -> dict:
    """Формат клипа для get_clip: ``format``, кадр ``frame`` [w, h], опорный кадр слоёв
    ``ref`` и формат их разметки ``layers_format``; ``aspect`` — старое имя ``format``."""
    from app.formats import frame_size, ref_size, settings_layers_format, settings_render_format

    fmt = settings_render_format(render)
    layers_fmt = settings_layers_format(render)
    return {"format": fmt, "aspect": fmt, "frame": list(frame_size(fmt)),
            "layers_format": layers_fmt, "layers_ref": list(ref_size(layers_fmt))}


def h_set_format(ctx: ToolContext, args: dict) -> dict:
    """Формат вывода клипа (app/formats.py): 9:16 / 1:1 / 4:5 / 16:9 — у любого клипа.
    ``layers_format`` — в каком формате размечены слои (по умолчанию не меняется)."""
    from app.aspect import reframe_plan
    from app.formats import FORMAT_IDS, frame_size, normalize_format, ref_size, settings_layers_format

    try:
        fmt = normalize_format(args.get("format") or args.get("aspect"))
        current = _current_render(ctx)
        layers_fmt = (normalize_format(args["layers_format"]) if args.get("layers_format")
                      else settings_layers_format(current))
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    fresh = _save_render(ctx, {**current, "format": fmt, "layers_format": layers_fmt})
    width, height = frame_size(fmt)
    out: dict = {"ok": True, "format": fresh["format"], "aspect": fresh["format"], "size": f"{width}×{height}",
                 "frame": [width, height], "layers_format": fresh["layers_format"], "formats": list(FORMAT_IDS)}
    if fmt != layers_fmt:
        rw, rh = ref_size(fmt)
        lw, lh = ref_size(layers_fmt)
        layout = reframe_plan([layer for layer in _layers_of(fresh) if not layer.get("hidden")], rw, rh,
                              camera=_camera_of(fresh), ref=(lw, lh))
        out["reframe"] = {"scale": layout["scale"], "scenes": [
            {"start": p["start"], "end": p["end"], "box": p["box"]} for p in layout["scenes"]]}
        out["note"] = (f"слои остаются в опорных {lw}×{lh} ({layers_fmt}) — при рендере и в view_frame они "
                       f"переносятся в {rw}×{rh} одним масштабом ×{layout['scale']} по рамке содержимого; проверь "
                       f"view_frame на титрах и концовке. Вернуть — set_format(\"{layers_fmt}\")")
    if fmt == "16:9":
        out["note_16x9"] = ("16:9: горизонтальный исходник — без кропа, вертикальный — по высоте на размытой "
                            "подложке; вебка сверху/снизу — окошком в углу, meme — обычный кадр. Субтитры, "
                            "баннер и обложка пока размечены под вертикаль — проверь view_frame")
    return out


def h_set_aspect(ctx: ToolContext, args: dict) -> dict:
    """Старое имя set_format (формат кадра — app/formats.py)."""
    return h_set_format(ctx, {"format": args.get("aspect") or args.get("format")})


def h_set_backdrop(ctx: ToolContext, args: dict) -> dict:
    """Фон за вырезанным персонажем (app/backgrounds.py): анимированный kind в цветах
    colors; статичная заливка — только kind solid/gradient (или старые color/color2)."""
    from app.backgrounds import normalize_background
    from app.chroma_key import normalize_backdrop

    render = _current_render(ctx)
    current = normalize_background(render.get("backdrop"))
    fresh_args = {k: v for k, v in args.items() if v is not None}
    if any(k in fresh_args for k in ("kind", "colors", "speed", "intensity")):
        # Передано только то, что меняется: остальное — от текущего фона.
        merged = {**current, **{k: fresh_args[k] for k in ("kind", "colors", "speed", "intensity") if k in fresh_args}}
    elif fresh_args.get("color"):
        # Старый вызов color/color2 — явная заливка (градиент), её просили.
        merged = {"color": fresh_args["color"], "color2": fresh_args.get("color2") or ""}
        if str(merged["color"]).lower() == "#0b0d14" and not merged["color2"]:
            merged = {"kind": "solid", "colors": [merged["color"]]}
    else:
        merged = current
    fresh = _save_render(ctx, {**render, "backdrop": normalize_backdrop(merged)})
    out: dict = {"ok": True, "backdrop": fresh["backdrop"]}
    if not _source_keyed(ctx):
        out["note"] = "у источника нет хромакея — фон не виден (set_chroma_key on)"
    return out


def h_set_chroma_key(ctx: ToolContext, args: dict) -> dict:
    """Хромакей источника клипа: on — найти зелёнку (или взять color), off — убрать."""
    from app.chroma_key import detect_key, normalize_key

    plan = ctx.store.get_clip_plan(ctx.plan_id, include_segments=False)
    source = ctx.store.get_source(int(plan["source_id"]))
    if args.get("on") is False:
        ctx.store.update_source(source["id"], chroma_key=None)
        return {"ok": True, "chroma_key": None}
    current = source.get("chroma_key") or {}
    tweaks = {k: v for k, v in args.items() if k in ("color", "similarity", "blend", "despill") and v is not None}
    if args.get("color") or current:
        key = normalize_key({**current, **tweaks})
    else:
        try:
            key = detect_key(source)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        key = normalize_key({**key, **tweaks}) if key else None
    if not key:
        raise ToolError("зелёного/синего фона в источнике не нашлось — передай color (#rrggbb) сам, если он есть")
    saved = ctx.store.update_source(source["id"], chroma_key=key)
    return {"ok": True, "chroma_key": saved.get("chroma_key"),
            "note": "проверь view_frame: края персонажа без зелени; ореол — similarity выше (0.3), рваные края — ниже; "
                    "жёлтое стало рыжим / зелёное серым — despill \"none\" или мягко 0.3"}


def _piece_words(words: list[dict], plain_start: float, length: float) -> list[dict]:
    """Слова субтитров куска во времени куска (``piece``). Субтитры лежат «кусками подряд»
    (plain, без наплывов) — от начала куска на этой шкале и считаем."""
    out = []
    for w in words or []:
        try:
            start = float(w.get("start", 0)) - plain_start
            end = float(w.get("end", w.get("start", 0))) - plain_start
        except (TypeError, ValueError):
            continue
        if -0.2 <= start <= length + 0.2 and str(w.get("word") or "").strip():
            item = {"word": str(w["word"]), "start": round(start, 3), "end": round(max(start, end), 3)}
            if w.get("speaker"):
                item["speaker"] = str(w["speaker"])
            out.append(item)
    return out


def h_auto_focus(ctx: ToolContext, args: dict) -> dict:
    """Кадрирование окном формата клипа сам: лица/движение по планам (app/autofocus), для
    мультфильмов — ещё и Gemini по кадру на план. Проверочный прогон: встроенный монтажёр 8 шагов
    подбирал x «на глаз» по листам кадров и всё равно промахнулся на стене. 16:9 из
    горизонтального исходника — кадр не режется, фокус не нужен (app/formats.focus_needed)."""
    from app.formats import focus_needed, settings_render_format
    from app.settings import settings

    timeline = clip_timeline(ctx.store, ctx.plan_id)
    pieces = timeline["pieces"]
    indexes = [_piece_index(args, len(pieces))] if args.get("piece") is not None else list(range(len(pieces)))
    plan = ctx.store.get_clip_plan(ctx.plan_id, include_segments=False)
    source = ctx.store.get_source(int(plan["source_id"]))
    fmt = settings_render_format(plan.get("render_settings"))
    if not focus_needed(fmt, source):
        # 16:9 из горизонтального исходника — кадр не режется (вертикальный — целиком на подложке).
        return {"ok": True, "focus_needed": False, "format": fmt, "pieces": [],
                "note": f"формат {fmt}: исходник {source.get('width')}×{source.get('height')} ложится в кадр без "
                        "кропа — фокус не нужен, кадрировать нечего"}
    from app.autofocus import compute_segment_focus
    from app.first_person import first_person_hint, source_texts

    # Игра от первого лица — окно на прицеле (app/first_person): явно (first_person), по
    # названию игры у записи/клипа, иначе — если прицел виден в центре кадров куска ("auto").
    wanted = args.get("first_person")
    if wanted is None or wanted == "auto":
        game = first_person_hint(*source_texts(source, plan))
        first_person: Any = True if game else "auto"
        fp_reason = f"игра от первого лица по названию ({game})" if game else ""
    else:
        first_person = bool(wanted)
        game = None
        fp_reason = "first_person: true" if first_person else ""
    resolver = None
    # По умолчанию с Gemini по кадру на план: на мультфильмах лица не находятся, а монтажёр
    # use_vlm сам не ставил (проверочный прогон: кадр на руке Рика, пока говорит другой).
    if args.get("use_vlm", True) is not False and settings.gemini_api_key:
        from app.ai.vlm_focus import shots_focus_x

        resolver = shots_focus_x
        if fmt != "9:16":  # окно кропа другой пропорции — модель должна это знать
            def resolver(images, hints=None, _fmt=fmt):  # noqa: E306 - замыкание на формат
                return shots_focus_x(images, hints=hints, output_format=_fmt)
    words = (ctx.store.get_clip_plan_subtitles(ctx.plan_id) or {}).get("words") or []
    done = []
    for index in indexes:
        piece = pieces[index]
        length = _piece_len(piece)
        plain_start = sum(_piece_len(p) for p in pieces[:index])
        # Слова куска: Gemini видит реплику плана, а длинный план с диалогом — по кадру на реплику.
        piece_words = _piece_words(words, plain_start, length) if resolver and words else []
        kwargs = {"speech_words": piece_words} if piece_words else {}
        if first_person is not False:
            kwargs["first_person"] = first_person
        points = compute_segment_focus(source, float(piece["source_start"]), float(piece["source_end"]),
                                       vlm_resolver=resolver, **kwargs)
        aim = bool(points) and all(p.get("aim") for p in points)
        local = []
        for point in points:  # время точек — уже от начала куска
            item = {"t": round(float(point["t"]), 3), "x": round(float(point["x"]), 4)}
            if point.get("cut"):
                item["cut"] = True
            local.append(item)
        _apply_piece(ctx, index, focus=local)
        turns = sum(1 for a, b in zip(local, local[1:]) if not b.get("cut") and abs(b["x"] - a["x"]) >= 0.06)
        item = {"piece": index, "points": len(local), "cuts": sum(1 for q in local if q.get("cut"))}
        if turns and resolver:
            item["turns"] = turns  # повороты к ответившему внутри плана (по репликам)
        if aim:
            item["first_person"] = {"x": local[0]["x"] if local else 0.5,
                                    "why": fp_reason or "прицел виден в центре кадров куска"}
        done.append(item)
    out = {"ok": True, "pieces": done,
           "note": "проверь contact_sheet: говорящий в кадре? Промах на плане — поправь set_focus этого куска"}
    if any(p.get("first_person") for p in done):
        out["note"] = ("игра от первого лица: окно стоит на прицеле (x неподвижен, без погони за движением). "
                       "Герой не от первого лица — auto_focus(first_person: false); проверь contact_sheet")
    return out


def h_set_focus(ctx: ToolContext, args: dict) -> dict:
    """Кадрирование 9:16 куска: центр окна x(t), cut — жёсткая смена на склейке плана."""
    timeline = clip_timeline(ctx.store, ctx.plan_id)
    index = _piece_index(args, len(timeline["pieces"]))
    piece = timeline["pieces"][index]
    length = float(piece["source_end"]) - float(piece["source_start"])
    raw = args.get("points")
    if not isinstance(raw, list):
        raise ToolError("points — список {t, x, cut?} (t — секунды от начала куска, x 0..1); [] — по центру")
    points, problems = [], []
    for i, p in enumerate(raw):
        if not isinstance(p, dict):
            problems.append(f"points[{i}]: объект {{t, x, cut?}}")
            continue
        t = _num(p.get("t"))
        if t is None and _num(p.get("source_t")) is not None:
            t = float(_num(p.get("source_t"))) - float(piece["source_start"])  # прямо из get_shots
        x = _num(p.get("x"))
        if t is None or x is None:
            problems.append(f"points[{i}]: нужны числа t (или source_t) и x")
            continue
        if not -0.05 <= t <= length + 0.05:
            problems.append(f"points[{i}]: t={t:.2f} вне куска (0..{length:.2f} с от его начала)")
            continue
        if not 0.0 <= x <= 1.0:
            problems.append(f"points[{i}]: x={x} — доля ширины исходника 0..1")
            continue
        point = {"t": round(min(max(t, 0.0), length), 3), "x": round(x, 4)}
        if p.get("cut"):
            point["cut"] = True
        points.append(point)
    if problems:
        raise ToolError("кадрирование не принято", problems)
    points.sort(key=lambda p: p["t"])
    if points and points[0]["t"] > 0.05:
        # До первой точки окно стоит на её x — явная точка в 0 убирает догадки.
        points.insert(0, {"t": 0.0, "x": points[0]["x"]})
    fresh, changes = _apply_piece(ctx, index, focus=points)
    warnings = _shared_warning(ctx, fresh)
    render = _current_render(ctx)
    mode = (render.get("layout") or {}).get("mode") or "single"
    if mode == "meme" and not fresh.get("fit"):
        warnings.append("раскладка meme показывает видео целиком — фокус в ней не виден")
    from app.render import effective_preset

    preset = effective_preset(ctx.store, render.get("preset_id")) or {}
    if preset and not preset.get("smart_reframe"):
        warnings.append("у пресета выключен smart_reframe — фокус не действует (кадр по центру)")
    if mode == "avatar_full":
        warnings.append("раскладка avatar_full показывает только аватар (cam) — фокус окна в ней не виден")
    elif points:
        warnings.extend(_focus_avatar_warnings(ctx, points, preset))
    out = {"ok": True, "piece": index, "focus": fresh.get("focus") or [], "changes": changes,
           "note": "проверь source_contact_sheet(x=…) или view_frame на точках и сразу после cut"}
    if warnings:
        out["warnings"] = warnings
    return out


def _focus_avatar_warnings(ctx: ToolContext, points: list[dict], preset: dict) -> list[str]:
    """Окно 9:16 по точкам фокуса не захватывает известный аватар стримера (QA 01.11: x взят из центра
    subject_box — человека из ролика на экране, — аватара в кадре не было)."""
    from app.ai.avatar_box import avatar_center_in_window
    from app.video_crop import window_share

    plan = ctx.store.get_clip_plan(ctx.plan_id, include_segments=False)
    source = ctx.store.get_source(int(plan["source_id"]))
    avatar = known_avatar(ctx, source)
    if not avatar:
        return []
    share = window_share(source, int(preset.get("output_width") or 1080), int(preset.get("output_height") or 1920))
    missed = [p for p in points if not avatar_center_in_window(avatar["cam"], float(p["x"]), float(share))]
    if not missed:
        return []
    cam = avatar["cam"]
    cx = round(float(cam["x"]) + float(cam["w"]) / 2, 3)
    return [f"окно ({share:.2f} ширины исходника) на x={missed[0]['x']} не захватывает аватар стримера (центр x={cx}, "
            f"{avatar['from']}) — в кадре будет игра/ролик с экрана. «Только аватар» — set_layout mode avatar_full; "
            f"окно на аватар — x={cx}"]


def tools() -> list[Tool]:
    return [
        Tool(
            "set_layout", "Меняю раскладку кадра",
            "Раскладка кадра 9:16: mode single (обычный кадр) | split (вебка сверху/снизу + игра; cam {x,y,w,h} "
            "в долях исходника, ratio, cam_pos) | pip_cam (окошко вебки pip {x,y,w}) | meme (горизонтальное видео "
            "целиком сверху во всю ширину, ниже фон bg под картинки и титры — анимированный объект {kind, colors, "
            "speed, intensity} как у set_backdrop или имя стиля строкой (\"grid\"); не задан — аврора в цветах "
            "ролика; #rrggbb (заливка) — только если владелец просил; video_y — верх видео в долях "
            "высоты; video_scale 1..4 — ширина видео в ширинах кадра, края срезаются: для персонажа на зелёнке "
            "2–3, чтобы он был крупным). Передавай только то, что меняешь. В split/pip_cam камера (set_camera) "
            "двигает только игру; что из игры в окне — game_x (центр окна игры 0..1 на всех кусках, или на куске "
            "piece; трек по куску — set_focus). cam не задан — берётся вебка источника (detect-facecam); ответ "
            "предупредит, если полоса режет вебку (не те пропорции — ratio) или cam великоват (захвачен чат). "
            "avatar_full — ТОЛЬКО аватар/вебка стримера на весь кадр (истории витубера, плейбук vtuber-story): cam "
            "не задан — найдёт detect_avatar (Gemini), иначе facecam источника; video_scale — ширина аватара в "
            "ширинах кадра (по умолчанию — крупно, почти на всю высоту), avatar_y — центр по высоте; фон — размытый "
            "тот же аватар (bg_blur, по умолчанию) или bg / фон set_backdrop клипа. Игры, браузера и роликов с "
            "экрана в кадре нет. Аватар в разных местах по ходу клипа (detect_avatar: places) — рамка по отрезкам cams "
            "ставится сама; свои — cams [{from_t, to_t, cam}] (шкала clip), \"auto\" — заново по detect_avatar; один "
            "cam — на весь клип. После — view_frame (при нескольких местах — в каждом отрезке). "
            "/ Frame layout.",
            _obj({"mode": {"type": "string", "enum": ["single", "split", "pip_cam", "meme", "avatar_full"]},
                  "cam": {"type": "object"}, "ratio": {"type": "number"}, "cam_pos": {"type": "string"},
                  "pip": {"type": "object"},
                  "bg": {"type": ["object", "string"], "description": "meme/avatar_full: фон {kind, colors, speed, intensity} или имя стиля"},
                  "bg_blur": {"type": "boolean", "description": "avatar_full: фон — размытый тот же аватар (по умолчанию)"},
                  "avatar_y": {"type": "number", "description": "avatar_full: центр аватара по высоте кадра 0.2..0.8"},
                  "cams": {"type": ["array", "string"], "items": {"type": "object"},
                           "description": "avatar_full: рамки аватара по отрезкам [{from_t, to_t, cam {x,y,w,h}}] "
                                          "(шкала clip) или \"auto\" — места из detect_avatar"},
                  "video_y": {"type": "number"},
                  "video_scale": {"type": "number"},
                  "game_x": {"type": "number", "description": "центр окна игры, доля ширины исходника 0..1"},
                  "piece": {"type": "integer", "description": "game_x только для этого куска"}}),
            h_set_layout, mutating=True, label=lambda a: f"Раскладка: {a.get('mode') or 'правка'}",
        ),
        Tool(
            "detect_avatar", "Ищу аватар стримера",
            "Где на кадре стрима аватар стримера — VTuber/PNGtuber-модель или вебка (Gemini по целым кадрам "
            "исходника с кусков клипа; люди внутри роликов, игры и браузера на экране — не аватар). Ответ: cam "
            "{x, y, w, h} в долях всего кадра исходника (с запасом), frames_with_avatar; аватар переезжает — "
            "places [{from_t, to_t, cam, view_t}] по шкале clip и warnings. Дальше set_layout mode avatar_full (рамки "
            "возьмутся сами, по отрезкам). save: true — запомнить как "
            "facecam источника (для split/pip_cam других клипов). refresh — искать заново. / Find the streamer's "
            "avatar/webcam rectangle in the source frames.",
            _obj({"save": {"type": "boolean"}, "refresh": {"type": "boolean"}}),
            h_detect_avatar, label=lambda a: "Ищу аватар стримера",
        ),
        Tool(
            "set_chroma_key", "Вырезаю зелёный фон",
            "Хромакей источника (персонаж на зелёнке): on true — найти фон сам (или color #rrggbb), similarity "
            "0.01..0.6 (порог, по умолчанию 0.25), blend 0..0.5 (мягкость края), despill — подавление зелёного "
            "ореола: \"auto\" (полное, по умолчанию; жёлтое в кадре рыжеет, зелёное сереет), \"none\" (выкл — "
            "промо с жёлтыми/зелёными плашками, экраны), число 0..1 (мягкая сила, 0.3 — ореол слабее, цвета почти "
            "целы); on false — выключить. Фон за персонажем — set_backdrop, слои за ним — behind:true. / Chroma "
            "key of the clip's source.",
            _obj({"on": {"type": "boolean"}, "color": {"type": "string"}, "similarity": {"type": "number"},
                  "blend": {"type": "number"},
                  "despill": {"type": ["string", "number"],
                              "description": "\"auto\" | \"none\" | сила 0..1"}}),
            h_set_chroma_key, mutating=True,
            label=lambda a: "Хромакей " + ("выкл" if a.get("on") is False else "вкл"),
        ),
        Tool(
            "set_format", "Меняю формат вывода",
            "Формат вывода клипа: \"9:16\" (вертикаль, как всегда), \"1:1\" (1080×1080), \"4:5\" (1080×1350), "
            "\"16:9\" (горизонталь 1920×1080) — у любого клипа, из того же монтажа. Формат выбирает владелец: "
            "не меняй без его просьбы. Видео кадрируется само: окно "
            "нужной пропорции с тем же фокусом; горизонтальный исходник в 16:9 — без кропа, вертикальный — по "
            "высоте на размытой подложке. Слои НЕ переделывай: они остаются в формате разметки (layers_format, "
            "обычно 9:16) и при рендере переносятся сами (один масштаб по рамке содержимого, фон — на весь кадр). "
            "layers_format — только если слои уже размечены в другом формате (опорный кадр 16:9 — 1920×1080) "
            "или слоёв ещё нет и размечаешь сразу под формат клипа (set_format(\"16:9\", layers_format=\"16:9\")). "
            "Два формата — два рендера: set_format(\"16:9\"), рендер, set_format(\"9:16\") (или REST render с "
            "{\"format\": \"16:9\"} без смены настроек). Субтитры/баннер/обложка пока под вертикаль. "
            "/ Output format: 9:16, 1:1, 4:5 or 16:9.",
            _obj({"format": {"type": "string", "enum": ["9:16", "1:1", "4:5", "16:9"]},
                  "layers_format": {"type": "string", "enum": ["9:16", "1:1", "4:5", "16:9"]}}, ["format"]),
            h_set_format, mutating=True, label=lambda a: f"Формат {a.get('format') or ''}".strip(),
        ),
        Tool(
            "set_aspect", "Меняю формат кадра",
            "Старое имя set_format: формат кадра \"9:16\" / \"1:1\" / \"4:5\" / \"16:9\" у любого клипа. "
            "/ Legacy alias of set_format.",
            _obj({"aspect": {"type": "string", "enum": ["9:16", "1:1", "4:5", "16:9"]}}, ["aspect"]),
            h_set_aspect, mutating=True, label=lambda a: f"Формат {a.get('aspect') or ''}".strip(),
        ),
        Tool(
            "set_backdrop", "Ставлю фон за персонажем",
            "Фон за вырезанным персонажем (нужен хромакей) — анимированный зацикленный, рисуется кодом: kind "
            f"{BG_KINDS_HINT}; colors — до 3 #rrggbb [тёмная основа, акцент, второй акцент], [] — в цветах ролика "
            "(тёмный + акцент субтитров); speed 0.1..4 (1 — цикл 10 с); intensity 0..1 (яркость свечения, 0.7). "
            "Передавай только то, что меняешь. Статичная заливка (kind solid/gradient или color/color2) — только "
            "если владелец попросил. Картинки во весь фон — слоем image с behind:true. / Animated backdrop behind "
            "a keyed subject.",
            _obj({"kind": {"type": "string", "enum": list(BG_KINDS)}, "colors": {"type": "array", "items": {"type": "string"}},
                  "speed": {"type": "number"}, "intensity": {"type": "number"},
                  "color": {"type": "string", "description": "устар.: заливка #rrggbb"},
                  "color2": {"type": "string", "description": "устар.: низ градиента"}}),
            h_set_backdrop, mutating=True, label=lambda a: "Фон за персонажем",
        ),
        Tool(
            "auto_focus", "Ищу говорящего в кадре",
            "Кадрирование автоматически (окно пропорции формата клипа: 9:16, 1:1, 4:5; у 16:9 из горизонтального "
            "исходника кадр не режется — ответ focus_needed:false, делать нечего): где лица/движение на каждом плане куска (piece — один кусок, "
            "без него — все), с жёсткой сменой на склейках. По умолчанию Gemini смотрит кадр каждого плана "
            "(use_vlm:false — только лица/движение, быстрее). Длинный план с диалогом делит по репликам субтитров "
            "(пауза или смена голоса): кадр на реплику, плавный поворот окна к ответившему (turns в ответе) — "
            "поэтому сначала субтитры. Быстрее, чем подбирать x вручную; потом проверь contact_sheet и "
            "поправь промахи set_focus. Игра от первого лица (шутер: название игры у записи/клипа или прицел "
            "виден в центре кадров) — окно неподвижно на прицеле (x ≈ 0.5), без погони за движением; "
            "first_person: true/false — сказать явно. / Automatic 9:16 framing, per reply inside long dialogue shots.",
            _obj({"piece": {"type": "integer"}, "use_vlm": {"type": "boolean"},
                  "first_person": {"type": "boolean",
                                   "description": "игра от первого лица: окно на прицеле (по умолчанию — само)"}}),
            h_auto_focus, mutating=True, label=lambda a: "Ищу говорящего в кадре",
        ),
        Tool(
            "set_focus", "Кадрирую 9:16",
            "Кадрирование куска piece в формат клипа (окно 9:16, 1:1, 4:5; у 16:9 — только если исходник шире 16:9): "
            "points [{t, x, cut?}] — центр окна x(t) внутри куска (t — секунды "
            "от начала куска, или source_t — секунды исходника прямо из get_shots; x 0..1 — доля ширины "
            "исходника; cut:true — жёсткий переход в этой точке, ставь ровно на склейку плана). Между точками "
            "без cut окно плавно ведётся; одна точка — неподвижный кадр; [] — по центру. Заменяет трек куска "
            "целиком. Текущий трек — get_clip.pieces[].focus, доля ширины окна — get_clip.framing.window_w. "
            "Говорящий в кадре: get_shots → source_contact_sheet(x=…) по планам → set_focus с cut на склейках. "
            "/ 9:16 reframe track of a piece.",
            _obj({"piece": {"type": "integer", "description": "номер куска с 0"},
                  "points": {"type": "array", "items": _obj({
                      "t": {"type": "number", "description": "секунды от начала куска"},
                      "source_t": {"type": "number", "description": "вместо t: секунды исходника"},
                      "x": {"type": "number", "description": "центр окна, доля ширины исходника 0..1"},
                      "cut": {"type": "boolean", "description": "жёсткая смена кадра в этой точке (склейка)"}},
                      ["x"])}}, ["piece", "points"]),
            h_set_focus, mutating=True,
            label=lambda a: f"Кадрирую кусок {a.get('piece')}: {len(a.get('points') or [])} точ.",
        ),
    ]
