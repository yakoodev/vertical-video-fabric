"""Инструменты кадра: раскладки и вебка, поиск аватара и его места в кадре, кадрирование кусков 9:16, формат ролика,
фон и хромакей.

Входят в общий реестр (``app/agent_tools.py``).

Где что лежит. Здесь — формат, фокус и реестр ``tools``. Вынесено: аватар и лицо ``app/framing_tools_avatar.py``, раскладка кадра ``app/framing_tools_layout.py``.
Те модули этот не импортируют, а он их — поэтому все имена по-прежнему доступны отсюда.
"""

from __future__ import annotations

from typing import Any

from app.backgrounds import BACKGROUND_KINDS as BG_KINDS
from app.tool_core import (
    _apply_piece, _camera_of, _current_render, _layers_of, _num, _obj, _piece_index, _piece_len, _save_render,
    _shared_warning, _source_keyed, clip_timeline, Tool, ToolContext, ToolError,
)

from app.framing_tools_avatar import (  # noqa: F401
    _framing_view, known_avatar, source_span_to_clip, _place_view, _cam_bits, _same_cam, avatar_face_zones,
    _known_avatar_places, _face_for_cam, _SIDE_RU, fit_avatar_faces, avatar_face_report, MAX_CAM_CHECKS,
    AVATAR_HIT_SHARE, _avatar_bits, _rect_hits, avatar_absent_spans, _absent_text, check_manual_cams, avatar_places,
    _places_text, h_detect_avatar,
)
from app.framing_tools_layout import (  # noqa: F401
    CAM_CROP_WARN, CAM_BIG_W, CAM_BIG_H, facecam_warnings, _focus_all, h_set_layout, _clip_to_source_t,
    _clip_time_cams, _avatar_layout_args,
)

# Стили фона для подсказок инструментов (app/backgrounds.py).
BG_KINDS_HINT = ("aurora (переливы, по умолчанию) | grid (неоновая перспективная сетка) | bokeh (блики) | "
                 "waves (светящиеся волны) | gradient | solid (статичные)")


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
