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

Где что лежит. Здесь реестр и запуск (``run_tool``) и обзор клипа (``get_clip``, ``view_frame``). Обработчики и
описания инструментов — в своём домене: субтитры ``app/subtitle_tools.py``, файлы ``app/asset_tools.py``, кадр
``app/framing_tools.py``, слои и камера ``app/layer_tools.py``, звук ``app/sound_tools.py``, куски и вырезы
``app/cut_tools.py``, источник ``app/source_tools.py``, оформление ``app/brand_tools.py``; общее —
``app/tool_core.py`` и ``app/layer_lint.py``. Домены не импортируют этот модуль, а он их — чтобы все имена
оставались доступны отсюда.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from app.canvas import is_canvas

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
from app.asset_tools import (  # noqa: F401
    _animation_fields, _font_manifest, _IMAGE_EXTS, _layer_ref, _video_asset_result, _VIDEO_EXTS,
    h_add_image_from_url, h_add_video_from_source, h_add_video_from_url, h_generate_image, h_list_assets,
    h_list_fonts, h_list_packs, h_search_emoji, h_search_pack, h_search_web_images, h_svg_to_webm, h_upload_font,
    h_upload_image, h_upload_video, MAX_SOURCE_CLIP_SEC, MAX_UPLOAD_FONT_B64, MAX_UPLOAD_IMAGE_BYTES,
    MAX_UPLOAD_VIDEO_BYTES, NO_REAL_PEOPLE_PLAYBOOKS, NO_REAL_PEOPLE_RE, people_gate, real_people_rule,
    similar_pack_refs, source_clip_args,
)
from app.framing_tools import (  # noqa: F401
    _absent_text, _avatar_bits, _avatar_layout_args, _cam_bits, _clip_time_cams, _clip_to_source_t, _face_for_cam,
    _focus_all, _focus_avatar_warnings, _format_view, _framing_view, _known_avatar_places, _piece_words, _place_view,
    _places_text, _rect_hits, _same_cam, _SIDE_RU, avatar_absent_spans, avatar_face_report, avatar_face_zones,
    AVATAR_HIT_SHARE, avatar_places, BG_KINDS_HINT, CAM_BIG_H, CAM_BIG_W, CAM_CROP_WARN, check_manual_cams,
    facecam_warnings, fit_avatar_faces, h_auto_focus, h_detect_avatar, h_set_aspect, h_set_backdrop, h_set_chroma_key,
    h_set_focus, h_set_format, h_set_layout, known_avatar, MAX_CAM_CHECKS, source_span_to_clip,
)
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
from app.sound_tools import (  # noqa: F401
    _audio_view, _is_promo, _music_track_hint, _music_warning, _source_audio_view, _track_warnings,
    h_add_music_from_url, h_add_sound_from_url, h_censor_profanity, h_find_noises, h_list_music, h_listen,
    h_mute_audio, h_set_music, h_set_source_audio, h_set_source_gain, h_source_loudness,
)
from app.cut_tools import (  # noqa: F401
    _beat_grid, _color_entries, _cuts_view, _fit_pip_insert, _plain_span_to_clip, _source_has_key, CAPTION_HALF,
    clip_cuts_to_plain, describe_inserts, ending_warning, h_add_insert, h_apply_cuts, h_auto_edit, h_freeze_frame,
    h_make_compilation, h_match_piece_colors, h_remove_insert, h_set_end_hold, h_set_piece_color, h_set_piece_fit,
    h_set_segment_speed, h_set_transition, h_snap_to_beats, h_suggest_cuts, h_trim_piece, HOOK_BOTTOM, insert_facts,
    INSERT_KIND_WORDS, MAX_FIT_PIECE_SEC, MIN_FINISHED_SEC, plain_cuts_to_clip, refit_inserts, short_clip_warning,
    TIME_SCALES_NOTE,
)
from app.source_tools import (  # noqa: F401
    _audio_brief, _chat_ready, _page_args, _page_info, CHAT_IMPORT_NOTE, DISCOVERY_TOOLS, h_chat_activity,
    h_create_canvas_clip, h_create_clip, h_get_chat, h_get_performance, h_list_clips, h_list_modules, h_list_projects,
    h_source_contact_sheet, h_source_shots, h_source_transcript, LIST_PAGE_DEFAULT, LIST_PAGE_MAX, MAX_CHAT_MESSAGES,
    MAX_CHAT_WINDOW_SEC,
)
from app.brand_tools import (  # noqa: F401
    _END_HOLD_ASK_RE, _scaffold_assets, apply_playbook_defaults, END_SCREEN_FIELDS, h_apply_brand, h_build_app_promo,
    h_compose_cover, h_get_playbook, h_list_brands, h_set_end_screen, h_set_nameplate, nameplate_after_hook,
    NAMEPLATE_FIELDS, SCAFFOLD_POSES,
)
from app.asset_tools import tools as asset_tools
from app.brand_tools import tools as brand_tools
from app.cut_tools import tools as cut_tools
from app.framing_tools import tools as framing_tools
from app.layer_tools import tools as layer_tools
from app.motion_template_tools import tools as motion_template_tools
from app.presets3d_tools import tools as presets3d_tools
from app.sound_tools import tools as sound_tools
from app.source_tools import tools as source_tools
from app.subtitle_tools import tools as subtitle_tools


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
        "owner": _owner_marks(ctx),
    }


def _owner_marks(ctx: ToolContext) -> dict:
    """Заметка владельца и где клип уже выложен (app/clip_marks.py) — только читать: это пишет владелец."""
    from app.clip_marks import card_marks, marks_for_plans
    from app.clip_groups_cards import group_members

    members = [int(m["id"]) for m in group_members(ctx.store, ctx.plan_id)]
    marks = card_marks(members, marks_for_plans(ctx.store, members))
    return {
        "note": marks["note"],
        "published": marks["published"],
        "publications": [{k: p[k] for k in ("kind", "platform", "account", "date", "url", "note")}
                         for p in marks["publications"]],
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
    "list_motion_templates", "add_motion_template", "list_3d_presets", "check_3d_plan",
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


def _clip_view_tools() -> list[Tool]:
    """Обзор клипа: то, что собирает сведения из всех доменов."""
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
            "owner — заметка владельца и где клип уже выложен (только читать). Зови первым. / Full clip state; times are in the 'clip' scale unless named otherwise.",
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
    ]


def _build_registry() -> list[Tool]:
    rank = {name: index for index, name in enumerate(TOOL_ORDER)}
    entries = [*_clip_view_tools(), *subtitle_tools(), *asset_tools(), *framing_tools(), *layer_tools(),
               *motion_template_tools(), *presets3d_tools(), *sound_tools(),
               *cut_tools(), *source_tools(), *brand_tools()]
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
        # Поиск в интернете и страницы сайтов как материал (app/webpage_tools.py).
        from app.webpage_tools import tools as webpage_tools

        _REGISTRY = {tool.name: tool for tool in [*_build_registry(), *audio_tools(), *cover_tools(), *lint_tools(),
                                                  *webpage_tools()]}
    return _REGISTRY


def _project_tools() -> list[Tool]:
    """Бриф, правила, статистика и гипотезы проектов-серий (app/project_tools.py) и автопилот
    (одобрение, расписание — app/autopilot_tools.py) — только MCP и чат проекта."""
    from app.aivideo_tools import all_tools as aivideo_tools
    from app.autopilot_tools import tools as autopilot_tools
    from app.explainer_tools import all_tools as explainer_tools
    from app.project_tools import tools
    from app.s3d_tools import all_tools as series3d_tools
    from app.stories_tools import all_tools as stories_tools
    from app.stream_question_tools import tools as stream_question_tools

    # ИИ-видео (app/aivideo_tools.py): проект, где ролик рисуется с нуля — тоже проектный;
    # объяснялки (app/explainer_tools.py) — маскот объясняет тему, на той же машинерии денег;
    # истории (app/stories_tools.py) — рассказчик и сцена на каждую фразу.
    # вопросы по стриму и выбор мемов (app/stream_question_tools.py) — монтажёр спрашивает владельца.
    # 3D-серия (app/s3d_tools.py) — план серии, версии, рендер заданием и проверки.
    return (tools() + autopilot_tools() + aivideo_tools() + explainer_tools() + stories_tools() + stream_question_tools()
            + series3d_tools())


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
