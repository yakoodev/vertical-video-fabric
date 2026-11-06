"""Инструменты ИИ-монтажёра: один реестр для встроенного чата и для MCP.

Зачем один реестр. Клип правят два вида агентов: встроенный ассистент в
редакторе (Gemini с function calling, ``app/ai/editor_agent.py``) и внешние
агенты — Claude Code, Codex, любой MCP-клиент (``app/agent_api.py`` → ``/mcp``).
Если у каждого свой набор ручек, они разъезжаются: один умеет ставить титры, другой
нет, проверки разные. Поэтому инструмент описан ровно один раз — имя, описание
(рус + англ), JSON Schema параметров и обработчик ``handler(ctx, args)`` — а
встроенный агент и MCP только переводят его в свой протокол.

Правила, общие для всех инструментов:

* Время — секунды готового клипа (как субтитры на дорожке редактора), кадр —
  опорные 1080×1920, ``x``/``y`` — доли кадра. Контракт слоёв — ``docs/motion/spec.md``.
* Всё, что меняет клип, проходит через те же нормализацию и проверку, что и правка
  человека (``app.motion`` для слоёв, ``app.clip_spec`` для кусков). Ошибка не
  роняет агента: инструмент возвращает ``ok: false`` и список ``problems``, чтобы
  модель сама исправилась.
* Перед первой правкой вызывается ``ctx.before_change()`` — это снимок истории
  (Ctrl+Z). Встроенный агент делает ОДИН снимок на весь свой ход («ИИ: …»),
  MCP — по снимку на каждый вызов, меняющий клип.
"""

from __future__ import annotations

import base64
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from app import motion
from app.clip_settings import normalize_render_settings
from app.backgrounds import BACKGROUND_KINDS as BG_KINDS
from app.canvas import is_canvas
from app.promo_lint import has_device_layers, promo_warnings
from app.subtitles.phantoms import is_phantom_text

# Инструменты разложены по доменам (app/*_tools.py); имена остаются доступны и отсюда.
from app.tool_core import (  # noqa: F401
    _apply_piece, _assets_project, _camera_of, _clip_duration, _clip_time_arg, _clock, _current_render,
    _foreign_asset_problem, _layers_of, _motion_assets, _num, _obj, _piece_index, _piece_len, _save_render,
    _shared_warning, _short, _source_arg, _source_canvas, _source_id, _source_keyed, _source_or_clip_source,
    _to_clip_time, ASSET_OWNER_PARAMS, clip_timeline, ImageResult, plan_clock, SOURCE_ARGS, SOURCE_ID_TOOLS, Tool,
    ToolContext, ToolError,
)
from app.layer_lint import (  # noqa: F401
    _cover_problems, _layer_box, _lint_scene, _ref, _rgb, _secondary_captions, _side_warnings, _zone, cam_strip,
    CAPTION_ACTIVE_COLORS, caption_highlight_warnings, caption_overlap_warnings, caption_zone_warnings,
    CAPTIONS_MIN_GAP, CAPTIONS_ZONE_ROOM, colors_close, cover_share, covering_problems, FULL_INSERT_MAX_SEC,
    HIGHLIGHT_MIN_DISTANCE, is_decor_layer, is_full_insert, lint_layers, MAX_COVER_SHARE, MAX_SIMULTANEOUS,
    SAFE_BOTTOM, safe_text_y, SAFE_TOP, separate_voice_captions, text_band, text_half_height, text_half_width,
    text_lines, TEXTUAL_TYPES, video_height, voice_caption_y, VOICE_CAPTIONS_BOTTOM, VOICE_CAPTIONS_TOP,
    VOICE_GAP_ABOVE, VOICE_GAP_BELOW,
)
from app.subtitle_tools import (  # noqa: F401
    _emph_index, _guard_emphasis, _key_word_indices, _muted_word_indices, _norm_word, _phrases_heard, _subtitle_zone,
    _subtitles_view, _word_match, ACCENT_WORD_SEC, accent_words, censor_keep_words, GAP_MIN_PHRASE_SEC,
    GAP_MIN_PHRASE_WORDS, h_delete_voice, h_dub_clip, h_edit_subtitle_lines, h_emphasize_words, h_generate_subtitles,
    h_get_transcript, h_identify_voices, h_list_subtitle_styles, h_list_voices, h_register_voice, h_separate_voices,
    h_set_subtitle_words, h_set_subtitles, h_snap_subtitles_to_speech, h_translate_subtitles, h_tts_preview,
    SUBTITLE_BAND, subtitle_gaps, word_at, word_time, words_clip_to_plain,
)
from app.subtitle_tools import tools as subtitle_tools_entries
from app.asset_tools import (  # noqa: F401
    _animation_fields, _font_manifest, _IMAGE_EXTS, _layer_ref, _video_asset_result, _VIDEO_EXTS,
    h_add_image_from_url, h_add_video_from_source, h_add_video_from_url, h_generate_image, h_list_assets,
    h_list_fonts, h_list_packs, h_search_emoji, h_search_pack, h_search_web_images, h_svg_to_webm, h_upload_font,
    h_upload_image, h_upload_video, MAX_SOURCE_CLIP_SEC, MAX_UPLOAD_FONT_B64, MAX_UPLOAD_IMAGE_BYTES,
    MAX_UPLOAD_VIDEO_BYTES, NO_REAL_PEOPLE_PLAYBOOKS, NO_REAL_PEOPLE_RE, people_gate, real_people_rule,
    similar_pack_refs, source_clip_args,
)
from app.asset_tools import tools as asset_tools_entries
from app.framing_tools import (  # noqa: F401
    _absent_text, _avatar_bits, _avatar_layout_args, _cam_bits, _clip_time_cams, _clip_to_source_t, _face_for_cam,
    _focus_all, _focus_avatar_warnings, _format_view, _framing_view, _known_avatar_places, _piece_words, _place_view,
    _places_text, _rect_hits, _same_cam, _SIDE_RU, avatar_absent_spans, avatar_face_report, avatar_face_zones,
    AVATAR_HIT_SHARE, avatar_places, BG_KINDS_HINT, CAM_BIG_H, CAM_BIG_W, CAM_CROP_WARN, check_manual_cams,
    facecam_warnings, fit_avatar_faces, h_auto_focus, h_detect_avatar, h_set_aspect, h_set_backdrop, h_set_chroma_key,
    h_set_focus, h_set_format, h_set_layout, known_avatar, MAX_CAM_CHECKS, source_span_to_clip,
)
from app.framing_tools import tools as framing_tools_entries
from app.layer_tools import (  # noqa: F401
    _anchor_zoom_keys, _AT_WORD_SCHEMA, _crop_aspect, _deep_merge, _drop_emoji, _EMOJI_RE, _image_card_warnings,
    _label_add_layers, _label_camera, _layer_index, _layer_problems, _layer_schema, _layer_summary,
    _layers_room_warning, _motion_reference_text, _on_word_line, _PACK_REF_MISS_RE, _place_on_word, _pngtuber_asset,
    _pngtuber_target, _pngtuber_track, _problem_hint, _ref_checker, _replace_layer, _resolve_caption_marks,
    _scene_engine_problems, _scene_times, _scene_warnings, _strip_emoji, _tracking_presets, _validate_new_layers,
    _video_kind_problems, _voice_check, h_add_glitch, h_add_layers, h_add_pngtuber, h_auto_crop_layer,
    h_camera_preset, h_clear_layers, h_get_motion_reference, h_get_scene_reference, h_preview_scene_frame,
    h_remove_layers, h_set_camera, h_set_motion_blur, h_track_layer, h_untrack_layer, h_update_layer,
    MAX_LAYERS_PER_CALL, motion_enums, motion_reference, PUNCH_HOLD, punch_keys, PUNCH_LEAD, PUNCH_RELEASE,
    PUNCH_ZOOM, scene_check, SCENE_TEMPLATES, SLOW_ZOOM_MIN, SLOW_ZOOM_SEC, slow_zoom_warnings,
)
from app.layer_tools import tools as layer_tools_entries
from app.sound_tools import (  # noqa: F401
    _audio_view, _is_promo, _music_track_hint, _music_warning, _source_audio_view, _track_warnings,
    h_add_music_from_url, h_add_sound_from_url, h_censor_profanity, h_find_noises, h_list_music, h_listen,
    h_mute_audio, h_set_music, h_set_source_audio, h_set_source_gain, h_source_loudness,
)
from app.sound_tools import tools as sound_tools_entries
from app.cut_tools import (  # noqa: F401
    _beat_grid, _color_entries, _cuts_view, _fit_pip_insert, _plain_span_to_clip, _source_has_key, CAPTION_HALF,
    clip_cuts_to_plain, describe_inserts, ending_warning, h_add_insert, h_apply_cuts, h_auto_edit, h_freeze_frame,
    h_make_compilation, h_match_piece_colors, h_remove_insert, h_set_end_hold, h_set_piece_color, h_set_piece_fit,
    h_set_segment_speed, h_set_transition, h_snap_to_beats, h_suggest_cuts, h_trim_piece, HOOK_BOTTOM, insert_facts,
    INSERT_KIND_WORDS, MAX_FIT_PIECE_SEC, MIN_FINISHED_SEC, plain_cuts_to_clip, refit_inserts, short_clip_warning,
    TIME_SCALES_NOTE,
)
from app.cut_tools import tools as cut_tools_entries
from app.source_tools import (  # noqa: F401
    _audio_brief, _chat_ready, _page_args, _page_info, CHAT_IMPORT_NOTE, DISCOVERY_TOOLS, h_chat_activity,
    h_create_canvas_clip, h_create_clip, h_get_chat, h_get_performance, h_list_clips, h_list_modules, h_list_projects,
    h_source_contact_sheet, h_source_shots, h_source_transcript, LIST_PAGE_DEFAULT, LIST_PAGE_MAX, MAX_CHAT_MESSAGES,
    MAX_CHAT_WINDOW_SEC,
)
from app.source_tools import tools as source_tools_entries
# --- реэкспорт конец ---


def h_get_clip(ctx: ToolContext, _args: dict) -> dict:
    timeline = clip_timeline(ctx.store, ctx.plan_id)
    plan, render = timeline["plan"], timeline["render"]
    source = ctx.store.get_source(int(plan["source_id"]))
    library = {a["id"]: a for a in ctx.store.list_montage_assets()}
    inserts = []
    for index, ins in enumerate(render.get("inserts") or []):
        asset = library.get(ins["asset_id"]) or {}
        inserts.append({"index": index, **ins, "asset_label": asset.get("label") or "", "asset_kind": asset.get("kind") or ""})
    layers = _layers_of(render)
    boxes = _subject_boxes(ctx)
    pieces = [{**piece, "subject_box": box} for piece, box in zip(timeline["pieces"], boxes["pieces"])] \
        if boxes["pieces"] and len(boxes["pieces"]) == len(timeline["pieces"]) else timeline["pieces"]
    return {
        "clip_id": plan["id"],
        "title": plan["title"],
        "description": plan.get("description") or "",
        "source": {"id": source["id"], "name": source.get("original_filename") or "",
                   "width": source.get("width"), "height": source.get("height"),
                   "audio": _source_audio_view(source)},
        "duration": timeline["duration"],
        **_cuts_view(plan_clock(ctx.store, plan), render, timeline["duration"]),
        "frame": _frame_view(render),
        "framing": _framing_view(ctx, source, render),
        "pieces": pieces,
        "render": {
            "preset_id": render.get("preset_id"),
            "effective_preset": _effective_preset_view(ctx.store, render.get("preset_id")),
            "subs_on": render.get("subs_on"),
            "sub_id": render.get("sub_id"),
            "sub_pos_pct": render.get("sub_pos_pct"),
            "subtitle_zone": _subtitle_zone(render),
            "banner_on": render.get("banner_on"),
            "music_on": render.get("music_on"),
            "track_id": render.get("track_id") if render.get("music_on") else None,
            "music_start_sec": render.get("music_start_sec") or 0.0,
            "mirror": render.get("mirror"),
            "transition": render.get("transition"),
            "cover": (render.get("cover") or {}).get("mode"),
            "mute": render.get("mute") or [],
            "gain": render.get("gain") or [],
            # Звуковой монтаж: J/L (piece — номер куска), ключи, ducking, эффекты (app/audio_tools.py).
            "audio": _audio_view(render, plan.get("segments") or []),
            # Стоп-кадры посреди ролика (freeze_frame): at — шкала final без стоп-кадров.
            "freezes": render.get("freezes") or [],
            # Формат вывода (app/formats.py): format, кадр и опорный кадр слоёв; aspect — старое имя.
            **_format_view(render),
            **_key_view(source, render),
        },
        # Готовые места на кадре — как монтирует владелец: заголовок сверху по центру,
        # субтитры строго по центру под лицом (с учётом раскладки «вебка снизу»).
        "positions": _recommended_positions(ctx, render, boxes),
        "layers": layers,
        "camera": _camera_of(render),
        "inserts": inserts,
        "subtitles": _subtitles_view(ctx.store, ctx.plan_id, timeline["pieces"]),
        "warnings": _clip_warnings(ctx, plan, timeline, layers),
    }


def _frame_view(render: dict) -> dict:
    """Кадр слоёв для get_clip: опорный кадр формата их разметки (``layers_format``) и безопасная
    зона площадок (app/safe_zones.py). У 9:16 — 1080×1920, зона SAFE_TOP/SAFE_BOTTOM, как всегда;
    рендер в другом формате — ещё зона кадра готового ролика (``render_safe_zone``)."""
    from app.formats import ref_size, settings_layers_format, settings_render_format
    from app.safe_zones import describe

    layers_fmt = settings_layers_format(render)
    out_fmt = settings_render_format(render)
    w, h = ref_size(layers_fmt)
    if layers_fmt == "9:16" and out_fmt == "9:16":
        return {"width": 1080, "height": 1920, "safe_zone": {"top": SAFE_TOP, "bottom": SAFE_BOTTOM}}
    out = {"width": w, "height": h, "format": layers_fmt, "safe_zone": describe(layers_fmt)}
    if out_fmt != layers_fmt:
        out["render_format"] = out_fmt
        out["render_safe_zone"] = describe(out_fmt)
        out["note"] = (f"слои размечены в {layers_fmt} и переносятся в кадр {out_fmt} при рендере: ставь их по зоне "
                       f"{layers_fmt}; хочешь раскладку под {out_fmt} — set_format(\"{out_fmt}\", "
                       f"layers_format=\"{out_fmt}\") и размечай в его опорном кадре")
    return out


def _effective_preset_view(store: Any, preset_id: Any) -> dict | None:
    """Каким пресетом отрендерится клип (при preset_id null — пресет по умолчанию)."""
    from app.render import effective_preset

    preset = effective_preset(store, preset_id)
    if not preset:
        return None
    return {"id": preset["id"], "name": preset.get("label") or "", "default": preset_id is None,
            "size": f"{preset.get('output_width')}×{preset.get('output_height')}"}


def _key_view(source: dict, render: dict) -> dict:
    """Хромакей источника и фон за персонажем — агент должен знать, что фон свой."""
    key = source.get("chroma_key")
    if not key:
        return {}
    if is_canvas(source):
        return {"canvas": True, "backdrop": render.get("backdrop"),
                "key_note": ("Пустой холст: видео исходника нет, весь кадр — фон backdrop (set_backdrop). Картинки "
                             "и плашки во весь кадр можно — закрывать нечего; behind не нужен.")}
    return {
        "chroma_key": key,
        "backdrop": render.get("backdrop"),
        "key_note": ("Источник на зелёнке: персонаж вырезан, фон — backdrop (set_backdrop). Слои с behind:true "
                     "лежат ЗА персонажем — скрины, картинки, фон-мемы во весь кадр можно (видео не закрывают). "
                     "Без behind слой поверх персонажа: на нём ничего крупного. Крупность и место персонажа — "
                     "set_layout mode meme + video_scale (2–3) + video_y."),
    }


def _clip_warnings(ctx: ToolContext, plan: dict, timeline: dict, layers: list) -> list[str]:
    scene = _lint_scene(ctx, timeline.get("render") or {})
    warnings = lint_layers(layers, timeline["duration"], **scene) if layers else []
    from app.cuts import CutMap, normalize_cuts

    cut_list = normalize_cuts((timeline.get("render") or {}).get("cuts"))
    finished = CutMap(cut_list).duration(float(timeline["duration"])) if cut_list else float(timeline["duration"])
    short = short_clip_warning(finished)
    if short:
        warnings.insert(0, short)
    gaps = subtitle_gaps(ctx.store, plan, timeline["pieces"])
    if gaps:
        warnings.insert(0, (
            f"На кусках {', '.join(map(str, gaps))} речь есть, а субтитров нет — субтитры устарели. "
            "Сначала generate_subtitles (распознать заново), потом auto_edit."
        ))
    cuts = (timeline.get("render") or {}).get("cuts") or []
    if cuts:
        from app.cuts import cut_warnings

        words = (ctx.store.get_clip_plan_subtitles(plan["id"]) or {}).get("words") or []
        warnings.extend(f"Вырезы: {w}" for w in cut_warnings(cuts, words, plan.get("segments") or [],
                                                             float(timeline["duration"])))
    if timeline["pieces"]:
        ending = ending_warning(ctx.store, int(plan["source_id"]), float(timeline["pieces"][-1]["source_end"]))
        if ending:
            warnings.append(f"Концовка: {ending} (trim_piece последнего куска).")
    transition = (timeline.get("render") or {}).get("transition")
    if len(timeline["pieces"]) > 1 and transition and transition.get("type") != "cut":
        from app.transitions import speech_join_warnings, speech_under_joins

        words = (ctx.store.get_clip_plan_subtitles(plan["id"]) or {}).get("words") or []
        report = speech_under_joins(plan.get("segments") or [], transition, words)
        warnings.extend(f"Переход: {w}" for w in speech_join_warnings(report, transition))
    layout = (timeline.get("render") or {}).get("layout") or {}
    if layout.get("mode") in ("split", "pip_cam"):
        source = ctx.store.get_source(int(plan["source_id"]))
        fmt = (timeline.get("render") or {}).get("format")
        warnings.extend(f"Раскладка: {w}" for w in facecam_warnings(layout, source, fmt))
    music = _music_warning(ctx, timeline.get("render") or {})
    if music:
        warnings.append(music)
    return warnings


def _subject_boxes(ctx: ToolContext) -> dict:
    """Рамки героя (Gemini-зрение, кэш): ``{union, pieces}``; без зрения — пусто."""
    try:
        from app.ai.subject_box import subject_boxes

        return subject_boxes(ctx.store, ctx.plan_id)
    except Exception:  # noqa: BLE001 - без зрения — привычные места
        return {"union": None, "pieces": []}


def _recommended_positions(ctx: ToolContext, render: dict, boxes: dict | None = None) -> dict:
    """Места для текста с учётом того, где на кадре герой (рамку даёт Gemini-зрение)."""
    from app.auto_edit import HOOK_Y, caption_y_for

    layout = (render.get("layout") or {}).get("mode") or "single"
    places = {"captions_y": caption_y_for(render), "punchline_y": 0.3, "subject": None}
    boxes = boxes if boxes is not None else _subject_boxes(ctx)
    try:
        from app.ai.subject_box import text_positions

        if boxes.get("union"):
            places = text_positions(boxes["union"], caption_y_for(render))
    except Exception:  # noqa: BLE001 - без зрения — привычные места
        pass
    from app.formats import settings_layers_format

    hook_props = {"size": 96, "weight": 900, "uppercase": True}
    layers_fmt = settings_layers_format(render)
    ref_h = _ref(layers_fmt)[1]
    # Верх зоны текста — с учётом полосы вебки (split сверху): хук под полосой, а не на лице.
    top, _bottom = text_band(render)
    by_lines = {str(n): safe_text_y({**hook_props, "text": "\n".join(["X"] * n)}, HOOK_Y, top=top, ref_h=ref_h)
                for n in (1, 2, 3)}
    strip = cam_strip(render.get("layout"), layers_fmt)
    under = f" В раскладке split вебка сверху до {strip[1]:.2f} кадра — хук под ней." if strip and strip[0] <= 0 else ""
    out = {
        "hook": {"x": 0.5, "y": by_lines["1"], "y_by_lines": by_lines,
                 "note": (f"заголовок-хук: строго по центру сверху; y={by_lines['1']} — для ОДНОЙ строки 96 px. Верх "
                          f"текста не выше {top}: две строки — y≈{by_lines['2']}, три — {by_lines['3']} (add_layers "
                          "предупредит, если верх выше зоны или текст на полосе вебки)." + under)},
        "captions": {"x": 0.5, "y": places["captions_y"], "note": "субтитры: строго x=0.5 и этот y — свободное от героя место"},
        "punchline_text": {"x": 0.5, "y": places["punchline_y"], "note": "крупное слово-панчлайн: по центру, не на герое"},
        "subject_box": places["subject"],
        "layout": layout,
        "text_band": {"top": top, "bottom": _bottom},
    }
    if strip:
        out["cam_strip"] = {"y_from": strip[0], "y_to": strip[1],
                            "note": "полоса вебки split: текст и картинки сюда не ставь — там лицо стримера"}
    if places["subject"]:
        out["subject_note"] = ("subject_box — объединение рамок героя по всем кускам (кадр 9:16); у каждого куска своя — "
                               "pieces[].subject_box. Внутри куска со сменой планов (get_shots) рамка тоже объединение "
                               "и ненадёжна — смотри source_contact_sheet с x по планам. subject_box в долях КАДРА клипа, "
                               "а x у set_focus — доля ширины ИСХОДНИКА: центр рамки в set_focus не подставляй")
    if (boxes or {}).get("screen"):
        out["screen_box"] = boxes["screen"]
        out["screen_note"] = ("screen_box — человек/персонаж ВНУТРИ экранного контента (ролик, игра, браузер на экране "
                              "стрима), это не герой: не кадрируй на него и не считай его стримером. Аватар/вебка "
                              "стримера — detect_avatar → set_layout avatar_full")
    return out


def h_export_timeline_package(ctx: ToolContext, args: dict) -> dict:
    from app.timeline_package import build_package

    try:
        result = build_package(ctx.store, ctx.plan_id, with_pieces=args.get("pieces") is True)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    return {
        "url": result["url"],
        "filename": result["filename"],
        "size_bytes": result["size_bytes"],
        "files": result["files"],
        "tracks": result["tracks"],
        "summary": result["summary"],
        "notes": result["notes"],
    }


def h_view_frame(ctx: ToolContext, args: dict) -> ImageResult:
    from app.freeze import normalize_freezes, post_to_pre

    with_layers = args.get("with_layers", True) is not False
    # Субтитры клипа (если включены) — по умолчанию в кадре: наезд на HUD игры видно до рендера.
    subtitles = with_layers and args.get("subtitles", True) is not False
    freezes = normalize_freezes(_current_render(ctx).get("freezes"))
    if args.get("final") and freezes:
        # Готовый ролик со стоп-кадрами: что в момент t видно (кадр), где слои и субтитры.
        u = _num(args.get("t"), 0.0) or 0.0
        where = post_to_pre(u, freezes)
        clock = _clock(ctx)
        t = clock.final_to_clip(where["video"])
        data, mime, info = render_clip_frame(
            ctx.store, ctx.plan_id, t, with_layers=with_layers, subtitles=subtitles,
            layer_t=clock.final_to_clip(where["layers"]), sub_t=clock.final_to_clip(where["audio"]))
        scale = {"time": "clip", "final_t": round(u, 3),
                 "final_note": "final — готовый ролик СО стоп-кадрами (freeze_frame)"}
        if where["freeze"] is not None:
            scale["freeze"] = freezes[where["freeze"]]
        return ImageResult(data=data, mime_type=mime,
                           info={"t": round(t, 3), **scale, "with_layers": with_layers, **info})
    t, scale = _clip_time_arg(ctx, args)
    data, mime, info = render_clip_frame(ctx.store, ctx.plan_id, t, with_layers=with_layers, subtitles=subtitles)
    return ImageResult(data=data, mime_type=mime, info={"t": round(t, 3), **scale, "with_layers": with_layers, **info})


def h_contact_sheet(ctx: ToolContext, args: dict) -> ImageResult:
    from app.motion_render import contact_sheet_jpeg

    n = int(_num(args.get("n"), 8) or 8)
    times = args.get("times")
    if times is not None and (not isinstance(times, list) or not all(_num(t) is not None for t in times)):
        raise ToolError("times: список секунд готового клипа, до 16")
    times = [float(_num(t)) for t in times] if times else None
    width = 220 if not times or len(times) > 4 else 300
    before = bool(args.get("before_cuts"))
    subtitles = args.get("subtitles", True) is not False and bool(_current_render(ctx).get("subs_on"))
    data = contact_sheet_jpeg(ctx.store, ctx.plan_id, n=n, width=width, times=times, before_cuts=before,
                              subtitles=subtitles)
    scale = "clip" if before and times else "final"
    return ImageResult(data=data, mime_type="image/jpeg",
                       info={"n": len(times) if times else n, "times": times, "time": scale, "subtitles": subtitles,
                             "note": "подписи на листе — шкала " + scale + (" (до вырезов, как слои)" if scale == "clip"
                                                                            else " (готовый ролик)")})


def h_compose_cover(ctx: ToolContext, args: dict) -> dict:
    from app.cover_compose import compose_cover

    t, scale = _clip_time_arg(ctx, args)
    ctx.before_change()
    out = compose_cover(ctx.store, ctx.plan_id, t, str(args.get("title") or ""),
                        emoji=str(args.get("emoji") or ""), style=str(args.get("style") or "yellow"),
                        y=float(_num(args.get("y"), 0.2) or 0.2))
    return {**out, "t": round(t, 3), **scale}


def render_clip_frame(store: Any, clip_plan_id: int, t: float, with_layers: bool = True,
                      subtitles: bool = False, layer_t: float | None = None,
                      sub_t: float | None = None) -> tuple[bytes, str, dict]:
    """Кадр готового вида на клиповом времени ``t``.

    Сначала — моушн-кадр (камера + слои тем же кодом, что и рендер,
    ``app.motion_render.render_frame_png``). Нет компоновщика или он упал — кадр основы:
    окно 9:16 исходника на этом месте (без слоёв), с пометкой в ``info``.
    """
    timeline = clip_timeline(store, clip_plan_id)
    t = min(max(0.0, t), max(0.0, timeline["duration"] - 0.04))
    try:
        from app.motion_render import render_frame_png

        layers = None if with_layers else []
        camera = None if with_layers else []
        notes: list[str] = []
        extra = {k: v for k, v in (("layer_t", layer_t), ("sub_t", sub_t)) if v is not None}
        data = render_frame_png(store, clip_plan_id, t, layers=layers, camera=camera, inserts=with_layers,
                                subtitles=subtitles, notes=notes, **extra)
        info: dict = {"kind": "motion"}
        if notes:
            info["subtitles"] = "; ".join(notes)
        return data, "image/png", info
    except ImportError:
        problem = "моушн-кадр недоступен"
    except KeyError:
        raise
    except Exception as exc:  # noqa: BLE001 - нет компоновщика/упал — агент всё равно видит основу
        problem = f"моушн-кадр не вышел: {str(exc)[:160]}"
    piece = next((p for p in timeline["pieces"] if p["clip_start"] <= t < p["clip_end"]), None)
    piece = piece or (timeline["pieces"][-1] if timeline["pieces"] else None)
    if piece is None:
        raise ToolError("у клипа нет кусков")
    from app.storyboard import plan_thumb

    local = t - piece["clip_start"]
    x = _focus_x_at(piece.get("focus") or [], local)
    source = store.get_source(int(timeline["plan"]["source_id"]))
    path = plan_thumb(source, piece["source_start"] + local, x)
    return path.read_bytes(), "image/jpeg", {"kind": "base", "note": problem + " — показан кадр основы без слоёв"}


def _focus_x_at(track: list, t: float) -> float:
    points = sorted((p for p in track if isinstance(p, dict) and "x" in p), key=lambda p: float(p.get("t", 0)))
    if not points:
        return 0.5
    x = float(points[0]["x"])
    for left, right in zip(points, points[1:]):
        if float(left.get("t", 0)) <= t < float(right.get("t", 0)):
            if right.get("cut"):
                return float(left["x"])
            span = float(right["t"]) - float(left["t"]) or 1.0
            k = (t - float(left["t"])) / span
            return float(left["x"]) + (float(right["x"]) - float(left["x"])) * k
    if t >= float(points[-1].get("t", 0)):
        x = float(points[-1]["x"])
    return x


def clip_warnings(ctx: ToolContext) -> list[str]:
    """Предупреждения get_clip по текущему клипу (без рамок героя — без вызова зрения)."""
    timeline = clip_timeline(ctx.store, ctx.plan_id)
    return _clip_warnings(ctx, timeline["plan"], timeline, _layers_of(timeline["render"]))


_END_HOLD_ASK_RE = re.compile(r"стоп[- ]?кадр|замира|замри|end[_ ]?hold|freeze", re.IGNORECASE)


def nameplate_after_hook(ctx: ToolContext) -> bool:
    """Плашка канала в углу выходит, когда уходит хук. ``set_nameplate`` так и ставит, но если хук добавили
    ПОСЛЕ плашки, она остаётся с 0 с и выглядывает из-под хука (QA 8, клип 901). → поменяли ли что-то."""
    from app.brand_kit import NAMEPLATE_ID, find_hook

    render = _current_render(ctx)
    layers = [dict(l) for l in render.get("layers") or []]
    plate = next((l for l in layers if l.get("id") == NAMEPLATE_ID and not l.get("hidden")), None)
    hook = find_hook([l for l in layers if l.get("id") != NAMEPLATE_ID])
    if plate is None or hook is None or hook.get("hidden"):
        return False
    position = str((plate.get("props") or {}).get("position") or plate.get("position") or "")
    corner = position.startswith("top-") or float(plate.get("y", 0.5)) < 0.2
    start, hook_start, hook_end = (float(plate.get("start") or 0.0), float(hook.get("start") or 0.0),
                                   float(hook.get("end") or 0.0))
    end = float(plate.get("end") or 0.0)
    if not corner or not (hook_start <= start < hook_end - 0.05) or hook_end >= end - 0.5:
        return False
    plate["start"] = round(hook_end, 3)
    _save_render(ctx, {**render, "layers": [plate if l.get("id") == NAMEPLATE_ID else l for l in layers]})
    return True


def apply_playbook_defaults(ctx: ToolContext) -> bool:
    """Умолчания активного плейбука, которых нет у клипа: стоп-кадр в конце (``end_hold_sec``) у vtuber-story — 0.

    QA 5 (клип 897): в конце ролика стоп-кадр 0.5 с (общее умолчание RENDER_END_HOLD_SEC) — на повторе ролик
    «замирает»; в прошлый раз агент сам ставил set_end_hold 0, в этот — нет. Стоп-кадр владелец просил в запросе
    или агент задал явно — не трогаем. → поменяли ли что-то."""
    from app.auto_edit import playbook_rules

    rules = playbook_rules(ctx.playbook) or {}
    if "end_hold_sec" not in rules:
        return False
    if ctx.request and _END_HOLD_ASK_RE.search(ctx.request):
        return False
    render = _current_render(ctx)
    if render.get("end_hold_sec") is not None:
        return False
    _save_render(ctx, {**render, "end_hold_sec": float(rules["end_hold_sec"])})
    return True


def h_list_brands(ctx: ToolContext, _args: dict) -> dict:
    """Бренды владельца целиком (шрифт, цвета, логотип, призыв, музыка) — их задаёт он сам."""
    return {"brands": [{k: v for k, v in b.items() if k not in ("created_at", "updated_at")}
                       for b in ctx.store.list_brands()],
            "note": "бренд задаёт владелец (Настройки → Бренды); ты только применяешь: apply_brand"}


def h_apply_brand(ctx: ToolContext, args: dict) -> dict:
    """Оформить клип в стиле бренда (app/brand_kit.py): повтор не плодит дублей."""
    from app.brand_kit import apply_brand

    brand_id = args.get("brand_id")
    if not brand_id:
        raise ToolError("нужен brand_id — см. list_brands")
    try:
        ctx.store.get_brand(int(brand_id))
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolError(f"бренда {brand_id} нет — см. list_brands") from exc
    plate = False if args.get("remove_nameplate") is True else args.get("nameplate")
    if plate is not None and not isinstance(plate, (dict, bool)):
        raise ToolError("nameplate — объект полей плашки канала или false (снять)")
    try:
        return apply_brand(ctx.store, ctx.plan_id, int(brand_id), before_change=ctx.before_change,
                           nameplate=None if plate is True else plate)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


NAMEPLATE_FIELDS = ("platform", "handle", "style", "accent", "position", "size", "fade", "anim", "live", "outro")


def h_set_nameplate(ctx: ToolContext, args: dict) -> dict:
    """Плашка канала (значок площадки + ник) на весь ролик без остального бренда (app/brand_kit.py)."""
    from app.brand_kit import set_nameplate

    brand_id = args.get("brand_id")
    if brand_id:
        try:
            ctx.store.get_brand(int(brand_id))
        except (KeyError, TypeError, ValueError) as exc:
            raise ToolError(f"бренда {brand_id} нет — см. list_brands") from exc
    fields = {k: args[k] for k in NAMEPLATE_FIELDS if k in args}
    try:
        return set_nameplate(ctx.store, ctx.plan_id, fields, brand_id=int(brand_id) if brand_id else None,
                             remove=args.get("remove") is True, before_change=ctx.before_change,
                             corner=args.get("corner") or None, start=_num(args.get("start")),
                             end=_num(args.get("end")))
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


END_SCREEN_FIELDS = ("template", "svg_asset_id", "text", "handle", "platform", "accent", "days", "time", "font",
                     "duration", "blur", "dim", "ramp", "y", "w")


def h_set_end_screen(ctx: ToolContext, args: dict) -> dict:
    """Концовка-баннер: размытие основы на хвосте + анимированный баннер из SVG (app/end_screen.py)."""
    from app.end_screen import set_end_screen
    from app.svg_webm import SvgWebmError

    brand_id = args.get("brand_id")
    if brand_id:
        try:
            ctx.store.get_brand(int(brand_id))
        except (KeyError, TypeError, ValueError) as exc:
            raise ToolError(f"бренда {brand_id} нет — см. list_brands") from exc
    fields = {k: args[k] for k in END_SCREEN_FIELDS if k in args}
    if fields.get("svg_asset_id"):
        foreign = _foreign_asset_problem(ctx, fields["svg_asset_id"])
        if foreign:
            raise ToolError(f"svg_asset_id: {foreign}")
    try:
        return set_end_screen(ctx.store, ctx.plan_id, fields, brand_id=int(brand_id) if brand_id else None,
                              remove=args.get("remove") is True, before_change=ctx.before_change)
    except (ValueError, SvgWebmError) as exc:
        raise ToolError(str(exc)) from exc


def h_get_playbook(_ctx: ToolContext, args: dict) -> dict:
    """Плейбук — пошаговый рецепт заказа (docs/playbooks/<name>.md); без name — список."""
    from app import playbooks

    name = str(args.get("name") or "").strip()
    items = playbooks.list_playbooks()
    if not name:
        return {"playbooks": items, "note": "get_playbook(name) — текст рецепта: шаги, инструменты, чек-лист"}
    try:
        return playbooks.get_playbook(name)
    except KeyError as exc:
        raise ToolError(f"плейбука «{name}» нет; есть: {', '.join(p['name'] for p in items) or '—'}") from exc


def _scaffold_assets(ctx: ToolContext, spec: dict) -> tuple[dict, list[str]]:
    """Размеры и длины записей/логотипа заготовки из библиотеки; чужой вид файла — проблема."""
    assets: dict = {}
    problems: list[str] = []
    wanted = [(f"scenes[{i}].asset_id", s["asset_id"], ("video", "gif")) for i, s in enumerate(spec["scenes"])]
    if spec["end"]["logo_asset_id"] is not None:
        wanted.append(("end.logo_asset_id", spec["end"]["logo_asset_id"], ("image",)))
    for where, ref, kinds in wanted:
        try:
            asset = ctx.store.get_montage_asset(int(ref))
        except (KeyError, TypeError, ValueError):
            problems.append(f"{where}: файла {ref} нет в библиотеке — id из list_assets")
            continue
        foreign = _foreign_asset_problem(ctx, asset["id"])
        if foreign:
            problems.append(f"{where}: {foreign}")
            continue
        if asset.get("kind") not in kinds:
            problems.append(f"{where}: {ref} — {asset.get('kind')}, нужен {' или '.join(kinds)}"
                            + (" (запись экрана: upload_video / add_video_from_source)" if "video" in kinds else ""))
            continue
        assets[str(asset["id"])] = {"kind": asset.get("kind"), "width": asset.get("width"),
                                    "height": asset.get("height"), "duration": asset.get("duration_sec")}
    return assets, problems


SCAFFOLD_POSES = {"center": "по центру крупно", "left": "слева с наклоном", "right": "справа с наклоном"}


def h_build_app_promo(ctx: ToolContext, args: dict) -> dict:
    """Заготовка промо приложения на холсте (app/promo_scaffold.py): spec → слои, камера, музыка."""
    from app import promo_scaffold
    from app.music_energy import ensure_energy

    raw = args.get("spec") if isinstance(args.get("spec"), dict) else {
        k: v for k, v in args.items() if k not in ("spec", "dry_run")}
    dry = bool(args.get("dry_run"))
    if not _source_canvas(ctx):
        raise ToolError("build_app_promo — только на холсте: create_canvas_clip, потом build_app_promo с его clip_id")
    from app.formats import settings_layers_format, settings_render_format

    # Формат клипа: 9:16 / 1:1 / 4:5 — заготовка в опорном кадре 9:16 (в 1:1 и 4:5 переносится
    # при рендере); 16:9 — понятный отказ (app/promo_scaffold.py, «Форматы»).
    out_fmt = settings_render_format(_current_render(ctx))
    try:
        promo_scaffold.check_format(out_fmt)
    except promo_scaffold.ScaffoldError as exc:
        raise ToolError(str(exc), exc.problems) from exc
    try:
        spec = promo_scaffold.normalize_spec(raw)
    except promo_scaffold.ScaffoldError as exc:
        raise ToolError(str(exc), exc.problems) from exc
    assets, problems = _scaffold_assets(ctx, spec)
    raw = dict(raw)
    if spec["music"]:
        try:
            [track] = ensure_energy(ctx.store, [ctx.store.get_audio_track(int(spec["music"]["track_id"]))])
        except (KeyError, TypeError, ValueError):
            problems.append(f"music.track_id: трека {spec['music']['track_id']} нет — list_music")
        else:
            raw["music"] = {**spec["music"], "bpm": track.get("bpm"), "energy_start_sec": track.get("energy_start_sec"),
                            "drop_sec": track.get("drop_sec"), "duration": track.get("duration_sec")}
    if problems:
        raise ToolError("spec заготовки не принят", problems)
    try:
        built = promo_scaffold.build_app_promo(raw, assets)
    except promo_scaffold.ScaffoldError as exc:
        raise ToolError(str(exc), exc.problems) from exc
    duration = float(built["duration"])
    render = _current_render(ctx)
    kept = [l for l in _layers_of(render) if not promo_scaffold.is_scaffold_layer(l)]
    replaced = len(_layers_of(render)) - len(kept)
    layers_fmt = settings_layers_format(render)
    if layers_fmt != promo_scaffold.SCAFFOLD_FORMAT and kept:
        raise ToolError(f"свои слои клипа размечены в {layers_fmt}, а заготовка — в {promo_scaffold.SCAFFOLD_FORMAT}",
                        [f"убери свои слои (remove_layers) или собери промо на новом холсте; формат вывода "
                         f"{out_fmt} останется — слои 9:16 перенесутся в него при рендере"])
    if len(kept) + len(built["layers"]) > motion.MAX_LAYERS:
        raise ToolError(f"заготовка не влезает: своих слоёв {len(kept)} + заготовка {len(built['layers'])} > "
                        f"{motion.MAX_LAYERS}", ["убери лишние свои слои (remove_layers) и повтори"])
    taken = {str(l.get("id")) for l in kept}
    new_layers = []
    for layer in built["layers"]:
        layer = dict(layer)
        if str(layer["id"]) in taken:
            layer["id"] = motion.new_layer_id(taken)
        taken.add(str(layer["id"]))
        new_layers.append(layer)
    # Звук, которого нет в паке этой установки, не валит заготовку — слой пропускается с пометкой.
    problems, skipped_sfx = list(motion.validate_camera(built["camera"], duration)), []
    checked = []
    for layer in new_layers:
        bad = _validate_new_layers(ctx, [layer], duration)
        if bad and layer.get("type") == "audio":
            skipped_sfx.append(str((layer.get("props") or {}).get("asset_id")))
            continue
        problems += [re.sub(r"^layers\[0\]", f"{layer['id']} ({layer.get('name')})", p) for p in bad]
        checked.append(layer)
    if problems:
        raise ToolError("заготовка не прошла проверку слоёв", problems)
    new_layers = checked

    plan = built["plan"]
    scenes = []
    for s in plan["scenes"]:
        extra = []
        if s.get("zoom"):
            extra.append(f"наезд {s['zoom']['zoom']:g}× на {s['zoom']['t']:.1f} с")
        if s.get("window"):
            extra.append(f"окно записи с {s['window']['t']:.1f} с")
        scenes.append(f"{s['scene']}. {s['start']:.1f}–{s['end']:.1f} с «{s['title']}» — телефон "
                      f"{SCAFFOLD_POSES.get(s['pose'], s['pose'])}" + (", " + ", ".join(extra) if extra else ""))
    climax = plan["climax"]
    out: dict = {
        "ok": True,
        "layers": len(new_layers),
        "layers_total": len(kept) + len(new_layers),
        "camera_keys": len(built["camera"]),
        "duration": round(duration, 2),
        "hook": f"0–{plan['hook'][1]:.1f} с",
        "scenes": scenes,
        "climax": f"{climax['t']:.1f} с (сцена {climax['scene']}, первая доля такта): вспышка, кольцо, частицы, бас-дроп",
        "final": f"{plan['final'][0]:.1f}–{plan['final'][1]:.1f} с: логотип, строки, плашка CTA",
        "music": built["music"],
        "notes": built["notes"],
    }
    if skipped_sfx:
        out["sounds_skipped"] = (f"нет в паке звуков: {', '.join(sorted(set(skipped_sfx)))} — без них; свои — "
                                 "search_pack freesound")
    if out_fmt != promo_scaffold.SCAFFOLD_FORMAT:
        out["format"] = (f"клип {out_fmt}: заготовка размечена в {promo_scaffold.SCAFFOLD_FORMAT} (layers_format) и "
                         f"переносится в кадр {out_fmt} одним масштабом — титры мельче, чем в вертикали; проверь "
                         "view_frame (он уже в формате клипа)")
    if dry:
        scene = {**_lint_scene(ctx, {**render, "layers": kept + new_layers, "camera": built["camera"],
                                     "layers_format": promo_scaffold.SCAFFOLD_FORMAT})}
        out.update(dry_run=True, warnings=lint_layers(kept + new_layers, duration, **scene),
                   note="ничего не применено; dry_run:false — применить")
        return out

    current = _clip_duration(ctx)
    if abs(current - duration) > 0.01:
        timeline = clip_timeline(ctx.store, ctx.plan_id)
        start = float(timeline["pieces"][0]["start_sec"])
        _apply_piece(ctx, 0, end_sec=round(start + duration, 3))
        render = _current_render(ctx)
        kept = [l for l in _layers_of(render) if not promo_scaffold.is_scaffold_layer(l)]
        out["resized"] = f"длина холста {current:.2f} → {duration:.2f} с (по сетке долей)"
    update = {**render, "layers": kept + new_layers, "camera": built["camera"]}
    if layers_fmt != promo_scaffold.SCAFFOLD_FORMAT:
        update["layers_format"] = promo_scaffold.SCAFFOLD_FORMAT
    if built["music"]:
        update.update(music_on=True, track_id=int(built["music"]["track_id"]),
                      music_start_sec=float(built["music"]["start_sec"] or 0.0))
    if spec["background"] is not None:
        from app.backgrounds import normalize_background
        from app.chroma_key import normalize_backdrop

        update["backdrop"] = normalize_backdrop(normalize_background(built["backdrop"]))
    fresh = _save_render(ctx, update)
    if replaced:
        out["replaced"] = f"прежняя заготовка убрана: {replaced} слоёв «promo:»; свои слои ({len(kept)}) на месте"
    if _camera_of(render):
        out["camera_note"] = "ключи камеры заменены наездами заготовки (свои наезды — set_camera вместе с ними)"
    out["warnings"] = lint_layers(_layers_of(fresh), duration, **_lint_scene(ctx, fresh))
    music = _music_warning(ctx, fresh)
    if music:
        out["warnings"].append(music)
    times = [round(float(plan["hook"][1]) - 0.4, 1)]
    times += [round(s["zoom"]["t"] + 0.4, 1) for s in plan["scenes"] if s.get("zoom")][:2]
    times += [round(float(climax["t"]) + 0.4, 1), round(float(plan["final"][1]) - 0.6, 1)]
    out["next"] = (f"дальше подстрой: view_frame на {', '.join(f'{t:g}' for t in times)} с — кольцо касания на кнопке "
                   "(иначе поправь focus сцены и вызови build_app_promo снова), окно кульминации показывает главное "
                   "(иначе window), титры по смыслу; точечно — update_layer по id (слои «promo: …»); повторный вызов "
                   "заменит только слои «promo:»")
    return out


# --------------------------------------------------------------------------- #
# Реестр
# --------------------------------------------------------------------------- #


# Порядок инструментов реестра: в этом порядке их видит модель. Описаны они в своих доменах (app/*_tools.py);
# новый инструмент, которого здесь нет, встаёт в конец.
TOOL_ORDER = (
    "get_clip", "get_transcript", "export_timeline_package", "view_frame", "find_noises", "listen", "contact_sheet",
    "source_contact_sheet", "source_loudness", "chat_activity", "get_chat", "set_source_audio",
    "get_motion_reference", "get_scene_reference", "preview_scene_frame", "add_layers", "add_pngtuber",
    "update_layer", "track_layer", "untrack_layer", "remove_layers", "clear_layers", "set_camera", "camera_preset",
    "list_fonts", "upload_font", "search_emoji", "list_packs", "search_pack", "list_assets", "search_web_images",
    "add_image_from_url", "upload_image", "upload_video", "add_video_from_source", "add_video_from_url",
    "generate_image", "list_subtitle_styles", "set_subtitles", "set_layout", "detect_avatar", "set_chroma_key",
    "build_app_promo", "list_music", "add_sound_from_url", "get_performance", "mute_audio", "censor_profanity",
    "set_source_gain", "add_music_from_url", "set_music", "set_format", "set_aspect", "set_end_hold", "add_glitch",
    "set_motion_blur", "freeze_frame", "set_backdrop", "list_brands", "apply_brand", "set_nameplate",
    "set_end_screen", "svg_to_webm", "get_playbook", "set_subtitle_words", "emphasize_words", "edit_subtitle_lines",
    "separate_voices", "register_voice", "identify_voices", "list_voices", "delete_voice",
    "snap_subtitles_to_speech", "translate_subtitles", "dub_clip", "generate_subtitles", "suggest_cuts",
    "apply_cuts", "auto_edit", "compose_cover", "make_compilation", "set_transition", "trim_piece", "auto_focus",
    "auto_crop_layer", "set_focus", "set_segment_speed", "set_piece_color", "match_piece_colors", "snap_to_beats",
    "set_piece_fit", "add_insert", "remove_insert",
)


def _remaining_tools() -> list[Tool]:
    return [
        Tool(
            "get_clip", "Смотрю клип",
            "Весь клип: куски (start_sec/end_sec — время исходника, как в trim_piece и create_clip; "
            "clip_start/clip_end — шкала clip; у каждого куска его кадрирование focus, fit и рамка героя "
            "subject_box), framing (доля ширины исходника в окне 9:16), настройки (и каким пресетом "
            "отрендерится — render.effective_preset), слои, камера, вставки, вырезы, тишина и громкость "
            "исходника, субтитры с временем слов, места для текста (positions: хук с поправкой на число строк "
            "и полосу вебки split), звуковые дорожки исходника (source.audio) и предупреждения. Шкалы времени "
            "— time_scales: clip (до вырезов, с наплывами; почти всё), final (готовый ролик), source, piece. "
            "Зови первым. / Full clip state; times are in the 'clip' scale unless named otherwise.",
            _obj({}), h_get_clip,
        ),
        Tool(
            "export_timeline_package", "Собираю пакет для монтажки",
            "Пакет монтажа для Premiere и DaVinci Resolve без рендера: zip с FCP7 .xml и .otio (дорожки на готовом "
            "таймлайне после вырезов), .srt субтитров и текста слоёв, файлы мемов, картинок, звуков и музыки на нужных "
            "таймкодах, кроп и наезды ключами, README на русском — что доехало, что нет, как открыть. Запись стрима в "
            "архив не кладётся; pieces=true — короткие куски 720p по кускам клипа (дольше). Ответ: url для скачивания "
            "(с токеном или cookie), размер, состав и замечания. / Build a Premiere/Resolve package (no render).",
            _obj({"pieces": {"type": "boolean", "description": "положить в архив куски видео 720p (по умолчанию нет)"}}),
            h_export_timeline_package,
            label=lambda a: "Собираю пакет для монтажки",
        ),
        Tool(
            "view_frame", "Смотрю кадр",
            "Картинка клипа в момент t — как будет в готовом видео, со слоями и камерой. t — шкала clip "
            "(до вырезов, как слои); final=true — t в секундах ГОТОВОГО ролика (как contact_sheet и listen). "
            "Проверяй так размещение, читаемость, что титр не закрыл лицо. Субтитры клипа (если включены) "
            "прожжены как в рендере — видно, не легли ли они на HUD игры; subtitles=false — без них. "
            "/ Render the clip frame at t.",
            _obj({"t": {"type": "number"}, "with_layers": {"type": "boolean"},
                  "subtitles": {"type": "boolean", "description": "прожечь субтитры клипа (по умолчанию да)"},
                  "final": {"type": "boolean", "description": "t — секунды готового ролика (после вырезов; "
                                                                     "со стоп-кадрами freeze_frame, если они есть)"}},
                 ["t"]), h_view_frame,
            label=lambda a: f"Смотрю кадр на {(_num(a.get('t'), 0.0) or 0.0):.1f} с",
        ),
        Tool(
            "contact_sheet", "Смотрю весь клип",
            "Сетка из n кадров равномерно по клипу (с временем) — весь монтаж одним взглядом: "
            "проверить ритм графики, не перегружен ли кадр, где пусто. times — свои моменты, до 16: по "
            "умолчанию секунды ГОТОВОГО ролика (после вырезов, как подписи на листе); before_cuts=true — "
            "секунды ДО вырезов, как у слоёв и камеры. Субтитры клипа (если включены) прожжены как в рендере; "
            "subtitles=false — без них. Кадры исходника вне клипа — source_contact_sheet. / "
            "Contact sheet of n frames or of chosen times.",
            _obj({"n": {"type": "integer", "minimum": 2, "maximum": 16},
                  "subtitles": {"type": "boolean", "description": "прожечь субтитры клипа (по умолчанию да)"},
                  "times": {"type": "array", "items": {"type": "number"}},
                  "before_cuts": {"type": "boolean",
                                  "description": "times в секундах ДО вырезов — как у слоёв, камеры и view_frame"}}),
            h_contact_sheet,
            label=lambda a: "Смотрю весь клип сеткой кадров",
        ),
        Tool(
            "build_app_promo", "Собираю заготовку промо",
            "ЗАГОТОВКА ПРОМО ПРИЛОЖЕНИЯ на холсте (create_canvas_clip) одним вызовом — сильная основа по приёмам "
            "эталонных промо: сетка долей трека, хук крупно по словам над телефоном, сцены с записями экрана в "
            "телефоне (позы чередуются: по центру крупно / слева / справа с наклоном, свайпы), наезд камеры на "
            "focus на сильной доле и касание, окно записи (window) на ключевой момент, кульминация на первой доле "
            "такта (вспышка, кольцо, частицы, бас-дроп), титры над телефоном с затемнением, финал ≥ 3 с (логотип, "
            "строки, плашка CTA), тихие звуки, музыка со start_sec (дроп — на кульминацию). spec: {duration? 20–40 "
            "(нет — по сценам; холст подгоняется), brand {accent #rrggbb, text?, dark?, font?, weight?}, hook [1–3 "
            "удара по 1–3 слова], scenes [1–6: {asset_id записи, from, to (секунды записи), title 2–6 слов (« — » "
            "делит на строку и акцент), focus? {x, y доли экрана записи — кнопка; t? — секунда нажатия}, window? "
            "{x, y, w, h доли записи}, climax?}], end {logo_asset_id, lines [дата/оффер], cta (ссылка), sub?}, "
            "music {track_id, start_sec?} | null, background? (как set_backdrop), variant? (другая раскладка)}. "
            "Повторный вызов заменяет только прежние слои «promo: …» (свои слои остаются), камеру — целиком. "
            "dry_run:true — план без применения. / One-call app promo scaffold on a canvas clip.",
            _obj({
                "spec": _obj({
                    "duration": {"type": "number", "description": "20–40 с; не задано — по сценам"},
                    "brand": _obj({"accent": {"type": "string"}, "text": {"type": "string"}, "dark": {"type": "string"},
                                   "font": {"type": "string"}, "weight": {"type": "integer"}}, ["accent"]),
                    "hook": {"type": "array", "items": {"type": "string"}},
                    "scenes": {"type": "array", "items": _obj({
                        "asset_id": {"type": "integer"}, "from": {"type": "number"}, "to": {"type": "number"},
                        "title": {"type": "string"},
                        "focus": _obj({"x": {"type": "number"}, "y": {"type": "number"}, "t": {"type": "number"}}),
                        "window": _obj({"x": {"type": "number"}, "y": {"type": "number"}, "w": {"type": "number"},
                                        "h": {"type": "number"}}),
                        "climax": {"type": "boolean"}}, ["asset_id", "from", "to", "title"])},
                    "end": _obj({"logo_asset_id": {"type": "integer"}, "lines": {"type": "array", "items": {"type": "string"}},
                                 "cta": {"type": "string"}, "sub": {"type": "string"}}, ["cta"]),
                    "music": _obj({"track_id": {"type": "integer"}, "start_sec": {"type": "number"}}, ["track_id"]),
                    "background": {"type": "object"},
                    "variant": {"type": "integer"},
                }, ["brand", "hook", "scenes", "end"]),
                "dry_run": {"type": "boolean", "description": "true — только план, клип не меняется"},
            }, ["spec"]),
            h_build_app_promo, mutating=True,
            label=lambda a: (("План заготовки промо" if a.get("dry_run") else "Собираю заготовку промо")
                             + f" ({len(((a.get('spec') or {}).get('scenes') or []))} сцен)"),
        ),
        Tool(
            "list_brands", "Смотрю бренды",
            "Бренды владельца (повторяющиеся заказчики, например магазин): шрифт, цвета, логотип, призыв в "
            "конце (cta), плашка канала (nameplate), музыка, фон, политика эффектов. Создаёт и правит их только "
            "владелец. / Brand kits.",
            _obj({}), h_list_brands, scope="global",
        ),
        Tool(
            "apply_brand", "Оформляю в стиле бренда",
            "Ролик для бренда (реклама повторяющегося заказчика) — в его стиле одной командой: перекрашивает "
            "субтитры-слои и хук, ставит маленький логотип в углу, плашку-призыв на последние секунды, плашку "
            "канала (если она есть в бренде), музыку и фон бренда, снимает лишние эффекты. Зови ПОСЛЕ монтажа "
            "(auto_edit, хук, вырезы): повтор ничего не дублирует (слои brand_*). nameplate — плашка канала "
            "на этот ролик: поля как у set_nameplate поверх плашки бренда; remove_nameplate:true — снять. "
            "Потом view_frame на концовке — призыв не на лице. / Apply a brand kit to the clip.",
            _obj({"brand_id": {"type": "integer"},
                  "nameplate": {"type": "object", "description": "поля плашки канала, как у set_nameplate"},
                  "remove_nameplate": {"type": "boolean"}},
                 ["brand_id"]),
            h_apply_brand, mutating=True, label=lambda a: f"Бренд #{a.get('brand_id')}",
        ),
        Tool(
            "set_nameplate", "Ставлю плашку канала",
            "Плашка канала на весь ролик: значок площадки (свой векторный глиф) + ник в пилюле — как на "
            "нарезках стримов. platform twitch|youtube|tiktok|telegram|kick|none, handle (ник как есть), style "
            "glass (тёмное стекло + обводка цветом площадки, по умолчанию) | solid (заливка), accent #rrggbb "
            "(нет — цвет площадки), size (высота пилюли, px кадра 1080×1920, 74). anim — своя анимация: subtle (по "
            "умолчанию: вход от значка ~0.7 с — значок выпрыгивает, пилюля вытягивается, ник по буквам; дальше раз в "
            "7 с блик по пилюле и тик значка), lively (ярче, тик каждые 3.5 с), none (стоит; fade — мягкое "
            "появление). live:true — красная пульсирующая точка LIVE. outro: auto (по умолчанию — свернуться в "
            "значок, когда выходит призыв бренда brand_cta) | collapse (свернуться в конце ролика) | none. position: auto (по умолчанию: в раскладке «игра + вебка» — на 72 px ниже "
            "разделителя и ниже субтитров, слева; иначе — безопасный верхний угол, напротив логотипа бренда) | "
            "under-split-left | under-split-center | top-left | top-right. Место считается от зон площадок "
            "(вкладки сверху, подпись снизу, кнопки TikTok справа), субтитров, хука и логотипа. brand_id — "
            "взять плашку бренда за основу. corner: top-right|top-left — пресет «маленькая в верхнем углу» (кегль "
            "58, угол безопасной зоны; есть хук — выходит, когда он уходит). start/end — секунды клипа, когда плашка "
            "на экране (хук, ушедший до start, ей не мешает). remove:true — снять. Повтор заменяет прежнюю (слой brand_nameplate). "
            "Потом view_frame. / Channel nameplate (platform icon + handle) for the whole clip.",
            _obj({"platform": {"type": "string", "enum": ["twitch", "youtube", "tiktok", "telegram", "kick", "none"]},
                  "handle": {"type": "string"},
                  "style": {"type": "string", "enum": ["glass", "solid"]},
                  "accent": {"type": "string"},
                  "position": {"type": "string", "enum": ["auto", "top-left", "top-right", "under-split-left",
                                                          "under-split-center"]},
                  "corner": {"type": "string", "enum": ["top-right", "top-left"]},
                  "start": {"type": "number", "minimum": 0},
                  "end": {"type": "number", "minimum": 0},
                  "size": {"type": "number", "minimum": 40, "maximum": 160},
                  "fade": {"type": "boolean"},
                  "anim": {"type": "string", "enum": ["none", "subtle", "lively"]},
                  "live": {"type": "boolean"},
                  "outro": {"type": "string", "enum": ["auto", "collapse", "none"]},
                  "brand_id": {"type": "integer"},
                  "remove": {"type": "boolean"}}),
            h_set_nameplate, mutating=True,
            label=lambda a: "Снимаю плашку канала" if a.get("remove") else f"Плашка канала {a.get('handle') or ''}".strip(),
        ),
        Tool(
            "set_end_screen", "Ставлю концовку-баннер",
            "Концовка шортса отдельным решением: на последних duration с (1.5–8, по умолчанию 3; не больше половины "
            "ролика) видео плавно уходит в размытие (blur 0–60, 18) и темнеет (dim 0–0.8, 0.35) за ramp с (0.6), "
            "поверх — анимированный баннер (SVG → webm с альфой, слой end_banner). template: live (стеклянная "
            "карточка, пульсирующая точка LIVE, фраза крупно — после « — » акцентом, ник со значком площадки) | "
            "schedule (дни недели чипами, время крупно) | neon (ник светящейся обводкой, адрес канала); или "
            "svg_asset_id — свой SVG из библиотеки. text («Стримлю с 8 утра — заходи»), handle, platform "
            "twitch|youtube|tiktok|telegram|kick|none, accent #rrggbb (нет — цвет площадки), days («пн–пт», "
            "«будни»), time («8:00 МСК»), font; y — центр баннера (0.44), w — ширина (960 px кадра 1080). "
            "brand_id — тексты, шрифт и акцент из бренда. Когда: стример — анонс расписания; реклама — призыв "
            "бренда; НЕ на каждый мем и не поверх развязки. remove:true — снять. Повтор заменяет. Потом "
            "view_frame на концовке. / Blurred end screen with an animated banner.",
            _obj({"template": {"type": "string", "enum": ["live", "schedule", "neon"]},
                  "svg_asset_id": {"type": "integer"},
                  "text": {"type": "string"}, "handle": {"type": "string"},
                  "platform": {"type": "string", "enum": ["twitch", "youtube", "tiktok", "telegram", "kick", "none"]},
                  "accent": {"type": "string"}, "days": {"type": "string"}, "time": {"type": "string"},
                  "font": {"type": "string"},
                  "duration": {"type": "number", "minimum": 1.5, "maximum": 8},
                  "blur": {"type": "number", "minimum": 0, "maximum": 60},
                  "dim": {"type": "number", "minimum": 0, "maximum": 0.8},
                  "ramp": {"type": "number", "minimum": 0.1, "maximum": 2},
                  "y": {"type": "number"}, "w": {"type": "number"},
                  "brand_id": {"type": "integer"}, "remove": {"type": "boolean"}}),
            h_set_end_screen, mutating=True,
            label=lambda a: "Снимаю концовку" if a.get("remove") else f"Концовка {a.get('template') or ''}".strip(),
        ),
        Tool(
            "get_playbook", "Читаю плейбук",
            "Плейбук — пошаговый рецепт типового заказа по опыту реальных работ: шаги, какие инструменты, "
            "чек-лист перед сдачей. Без name — список (stream-shorts — нарезка стрима в вертикальные шортсы, "
            "stream-horizontal — горизонтальная нарезка стрима 10–15 мин, meme-insert — мем/вставка поверх "
            "видео, app-promo — промо приложения, autopilot — автопилот канала: от VOD до одобрения, расписания "
            "и сводки, vtuber-story — история-«говорящая голова» витубера: только аватар крупно, заголовок, "
            "сабы по 1–2 слова, вставки под слова). Прочитай подходящий ДО монтажа. / Step-by-step playbook "
            "for a typical order; no name — list.",
            _obj({"name": {"type": "string"}}), h_get_playbook, scope="global",
            label=lambda a: f"Плейбук {a.get('name') or ''}".strip(),
        ),
        Tool(
            "compose_cover", "Рисую обложку",
            "Обложка: кадр клипа в момент t + крупный заголовок (2–4 слова), style yellow|fire|white|plate; "
            "включается сразу и вшивается в первые кадры. t — шкала clip (как view_frame); final=true — секунды "
            "готового ролика. / Compose a cover with a title.",
            _obj({"t": {"type": "number"}, "title": {"type": "string"}, "emoji": {"type": "string"},
                  "style": {"type": "string"}, "y": {"type": "number"},
                  "final": {"type": "boolean", "description": "t — секунды готового ролика"}}, ["t", "title"]),
            h_compose_cover, mutating=True, label=lambda a: f"Обложка «{(a.get('title') or '')[:30]}»",
        ),
    ]


def _build_registry() -> list[Tool]:
    rank = {name: index for index, name in enumerate(TOOL_ORDER)}
    entries = [*_remaining_tools(), *source_tools_entries(), *cut_tools_entries(), *sound_tools_entries(), *layer_tools_entries(), *framing_tools_entries(), *asset_tools_entries(), *subtitle_tools_entries(), ]
    return sorted(entries, key=lambda tool: rank.get(tool.name, len(rank)))

_REGISTRY: dict[str, Tool] | None = None


def registry() -> dict[str, Tool]:
    global _REGISTRY
    if _REGISTRY is None:
        # Звуковой монтаж (audio_map, J/L, ключи, ducking, эффекты) — свой модуль app/audio_tools.py.
        from app.audio_tools import tools as audio_tools
        # Варианты обложки готового рендера — app/cover_tools.py.
        from app.cover_tools import tools as cover_tools
        # Линт качества перед сдачей (зоны площадок, темп чтения, вспышки, луп) — app/lint_tools.py.
        from app.lint_tools import tools as lint_tools

        _REGISTRY = {tool.name: tool for tool in [*_build_registry(), *audio_tools(), *cover_tools(), *lint_tools()]}
    return _REGISTRY


def _project_tools() -> list[Tool]:
    """Бриф, правила, статистика и гипотезы проектов-серий (app/project_tools.py) и автопилот
    (одобрение, расписание — app/autopilot_tools.py) — только MCP и чат проекта."""
    from app.aivideo_tools import all_tools as aivideo_tools
    from app.autopilot_tools import tools as autopilot_tools
    from app.explainer_tools import all_tools as explainer_tools
    from app.project_tools import tools
    from app.stories_tools import all_tools as stories_tools
    from app.stream_question_tools import tools as stream_question_tools

    # ИИ-видео (app/aivideo_tools.py): проект, где ролик рисуется с нуля — тоже проектный;
    # объяснялки (app/explainer_tools.py) — маскот объясняет тему, на той же машинерии денег;
    # истории (app/stories_tools.py) — рассказчик и сцена на каждую фразу.
    # вопросы по стриму и выбор мемов (app/stream_question_tools.py) — монтажёр спрашивает владельца.
    return tools() + autopilot_tools() + aivideo_tools() + explainer_tools() + stories_tools() + stream_question_tools()


def list_tools(include_discovery: bool = False, store: Any = None) -> list[Tool]:
    """Инструменты включённых модулей (app/modules.py): выключенный модуль прячет свои
    из MCP tools/list, чата и ИИ-монтажёра."""
    from app import modules

    tools = list(registry().values())
    tools = (DISCOVERY_TOOLS + _project_tools() + tools) if include_discovery else tools
    state = modules.enabled_map(store)
    return [t for t in tools if state.get(modules.module_of_tool(t.name) or "", True)]


def get_tool(name: str) -> Tool:
    tool = (registry().get(name) or next((t for t in DISCOVERY_TOOLS if t.name == name), None)
            or next((t for t in _project_tools() if t.name == name), None))
    if tool is None:
        raise KeyError(name)
    return tool


def run_tool(ctx: ToolContext, name: str, args: dict | None) -> tuple[Any, bool]:
    """Выполнить инструмент. Возвращает (результат, ok). Никогда не бросает:
    ошибка — это результат ``{ok: false, error, problems}``, который увидит модель."""
    try:
        tool = get_tool(name)
    except KeyError:
        return {"ok": False, "error": f"нет инструмента {name}"}, False
    from app import modules

    if not modules.tool_enabled(name, getattr(ctx, "store", None)):
        return modules.disabled_tool_error(name), False
    args = dict(args or {})
    if (_takes_source(tool) and getattr(ctx, "clip_plan_id", None)
            and args.get("source_id") in (None, "") and args.get("project_id") in (None, "")):
        # Монтажёр работает внутри клипа: источник — источник этого клипа (Gemini спотыкался
        # о «нужен project_id» на get_shots).
        try:
            args["source_id"] = int(ctx.store.get_clip_plan(ctx.clip_plan_id, include_segments=False)["source_id"])
        except (KeyError, TypeError, ValueError):
            pass
    paused = _montage_pause(ctx, name, args) or _clip_list_stop(ctx, tool, args)
    if paused:
        return paused, False
    framing = _framing_call(name, args)
    if framing and getattr(ctx, "framing_stop", ""):
        return {"ok": False, "error": "кадрирование остановлено: " + ctx.framing_stop, "stop_framing": True}, False
    try:
        result = tool.handler(ctx, args)
    except ToolError as exc:
        result = {"ok": False, "error": str(exc), "problems": exc.problems}
    except KeyError as exc:
        result = {"ok": False, "error": f"не найдено: {exc}"}
    except (ValueError, TypeError) as exc:
        result = {"ok": False, "error": str(exc)[:300]}
    except Exception as exc:  # noqa: BLE001 - инструмент не роняет агента
        result = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
    ok = not (isinstance(result, dict) and result.get("ok") is False)
    if framing:
        _track_framing(ctx, framing, result, ok)
    return result, ok


def _montage_pause(ctx: ToolContext, name: str, args: dict) -> dict | None:
    """Пауза монтажа (app/stream_questions.py): отказ, если инструмент начинает монтаж видео, чьих ответов ждём.
    Сбой проверки не должен останавливать работу: тогда отвечает сам инструмент."""
    from app import stream_questions as Q

    if name not in Q.MONTAGE_TOOLS:
        return None
    store = getattr(ctx, "store", None)
    try:
        targets: list[tuple[int, int | None]] = []
        if name == "create_clip":
            version_of = args.get("version_of")
            targets.append((_source_id(args), int(version_of) if version_of else None))
        elif name == "add_video_from_source":
            targets.append((int(args.get("source_id")), None))
        elif name == "make_compilation":
            for clip_id in args.get("clip_ids") or []:
                targets.append((int(store.get_clip_plan(int(clip_id), include_segments=False)["source_id"]), int(clip_id)))
        elif getattr(ctx, "clip_plan_id", None):
            plan = store.get_clip_plan(int(ctx.clip_plan_id), include_segments=False)
            targets.append((int(plan["source_id"]), int(ctx.clip_plan_id)))
        for source_id, clip_id in targets:
            refusal = Q.montage_refusal(store, source_id, clip_id)
            if refusal:
                return refusal
    except (KeyError, TypeError, ValueError, ToolError):
        return None
    return None


def _clip_list_stop(ctx: ToolContext, tool: Tool, args: dict) -> dict | None:
    """Настройка «Останавливаться на списке клипов» (app/agent_settings.py): правка черновика, которого владелец не
    выбрал, отказывает и называет настройку. Смотреть можно, новый клип по заданию создать тоже."""
    from app import agent_settings as S

    store = getattr(ctx, "store", None)
    try:
        if tool.name == "make_compilation":
            ids = args.get("clip_ids") or []
        elif tool.name == "create_clip":
            ids = [args["version_of"]] if args.get("version_of") else []
        elif tool.mutating and tool.scope == "clip" and getattr(ctx, "clip_plan_id", None):
            ids = [ctx.clip_plan_id]
        else:
            return None
        return S.drafts_refusal(store, ids)
    except (KeyError, TypeError, ValueError):
        return None  # сбой проверки не останавливает работу: отвечает сам инструмент


# Сколько неудачных попыток кадрирования аватара подряд (detect_avatar без рамки/со сбоем, set_layout avatar_full с
# ошибкой или «аватара в кадре нет») и сколько рамок руками за ход — до указания остановиться и спросить владельца.
FRAMING_FAIL_LIMIT = 3
FRAMING_MANUAL_LIMIT = 6


def _framing_call(name: str, args: dict) -> str:
    """``detect`` — поиск аватара, ``manual`` — set_layout avatar_full с рамкой руками, ``auto`` — avatar_full по
    detect_avatar; иначе пусто (не кадрирование аватара)."""
    if name == "detect_avatar":
        return "detect"
    if name == "set_layout" and args.get("mode") == "avatar_full":
        return "manual" if ("cam" in args or isinstance(args.get("cams"), list)) else "auto"
    return ""


def _track_framing(ctx: ToolContext, kind: str, result: Any, ok: bool) -> None:
    """Счёт попыток кадрирования (QA 6, клип 898: 12 set_layout и 20+ взглядов без прогресса). На пределе в ответ
    инструмента — ``stop_framing``: перестать перебирать рамки и спросить владельца."""
    if not hasattr(ctx, "framing_fails"):
        return
    body = result if isinstance(result, dict) else {}
    checks = [c for c in body.get("avatar_check") or [] if isinstance(c, dict)]
    verified = bool(checks) and all(c.get("avatar") is True for c in checks)
    if kind == "manual" and ok and not verified:
        ctx.framing_manual += 1  # рамка руками, которую Gemini-зрение не подтвердило
    failed = (not ok) or (kind == "detect" and not body.get("cam")) or bool(body.get("avatar_miss"))
    ctx.framing_fails = ctx.framing_fails + 1 if failed else 0
    if ctx.framing_stop:
        return
    if ctx.framing_fails >= FRAMING_FAIL_LIMIT:
        reason = f"{ctx.framing_fails} неудачных попыток кадрирования аватара подряд"
    elif ctx.framing_manual >= FRAMING_MANUAL_LIMIT and kind == "manual" and not verified:
        reason = f"{ctx.framing_manual} рамок аватара руками за ход"
    else:
        return
    ctx.framing_stop = (f"{reason}. Не перебирай рамки дальше (set_layout avatar_full с cam/cams и detect_avatar больше "
                        "не принимаются в этом ходе). Доделай остальное по задаче, а в отчёте задай владельцу прямой "
                        "вопрос: где аватар (секунда и угол кадра) — или пусть поставит рамку сам в редакторе")
    if isinstance(result, dict):
        result["stop_framing"] = ctx.framing_stop


def _takes_source(tool: Tool) -> bool:
    # У остальных project_id — проект-серия (app/project_tools.py, файлы для монтажа), источник клипа ему не подставить.
    return tool.name in SOURCE_ID_TOOLS


def result_for_json(result: Any) -> Any:
    """Результат без байтов картинки — для журнала шагов и текстовых протоколов."""
    if isinstance(result, ImageResult):
        return {"ok": True, "image": result.mime_type, "bytes": len(result.data), **result.info}
    return result


def image_b64(result: ImageResult) -> str:
    return base64.b64encode(result.data).decode("ascii")


def dumps(value: Any) -> str:
    return json.dumps(result_for_json(value), ensure_ascii=False, default=str)
