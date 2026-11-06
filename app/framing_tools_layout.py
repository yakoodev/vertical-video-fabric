"""Инструменты кадра: раскладка клипа (из ``app/framing_tools.py``)."""

from __future__ import annotations

from typing import Any

from app.tool_core import (
    _current_render, _num, _piece_index, _piece_len, _save_render, clip_timeline, ToolContext, ToolError
)
from app.framing_tools_avatar import (
    fit_avatar_faces, avatar_absent_spans, _absent_text, check_manual_cams, avatar_places, _places_text
)

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
