"""Инструменты кадра: аватар и лицо стримера (из ``app/framing_tools.py``)."""

from __future__ import annotations

from typing import Any

from app.clip_settings import normalize_render_settings
from app.tool_core import _current_render, clip_timeline, ToolContext, ToolError

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
