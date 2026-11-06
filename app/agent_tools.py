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


def _audio_view(render: dict, segments: list[dict]) -> dict:
    from app.audio_tools import audio_view

    return audio_view(render, segments)


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


def _source_audio_view(source: dict) -> dict:
    """Звуковые дорожки источника: сколько и какие выбраны (игра + микрофон — set_source_audio)."""
    from app.source_audio import audio_view

    try:
        return audio_view(source)
    except Exception:  # noqa: BLE001 - справка, не роняет get_clip
        return {}


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


TIME_SCALES_NOTE = {
    "clip": ("секунды клипа ДО вырезов, с наплывами переходов: duration, pieces[].clip_start, слои, камера, "
             "вставки, субтитры, cuts, mute, gain, view_frame, at_word, find_noises, apply_cuts, set_subtitle_words"),
    "final": ("секунды ГОТОВОГО ролика (после вырезов и наплывов): finished_duration, contact_sheet и listen по "
              "умолчанию; before_cuts=true у них — шкала clip, final=true у view_frame/compose_cover — шкала final"),
    "source": "секунды исходника: pieces[].start_sec/end_sec, trim_piece, get_transcript, get_shots, чат",
    "piece": "секунды от начала куска: pieces[].focus, set_focus t",
}


def plain_cuts_to_clip(clock: Any, cuts: list[dict]) -> list[dict]:
    """Вырезы (хранятся «кусками подряд») → шкала clip; через стык с наплывом — две части с piece."""
    out = []
    multi = len(clock.lengths) > 1
    for cut in cuts:
        for part in clock.plain_range_to_clip(float(cut["start"]), float(cut["end"])):
            out.append({**cut, "start": part["start"], "end": part["end"],
                        **({"piece": part["piece"]} if multi else {})})
    return out


def _cuts_view(clock: Any, render: dict, duration: float) -> dict:
    """Вырезы и длина ГОТОВОГО ролика: агент не должен гадать, сколько останется."""
    from app.cuts import normalize_cuts

    cuts = normalize_cuts(render.get("cuts"))
    if not cuts:
        return {"time": "clip", "time_scales": TIME_SCALES_NOTE,
                "time_note": "вырезов нет: шкала clip совпадает с готовым роликом (final)"}
    final = clock.final_duration
    return {
        "time": "clip",
        "time_scales": TIME_SCALES_NOTE,
        "cuts": plain_cuts_to_clip(clock, cuts),
        "finished_duration": final,
        "time_note": (
            f"Есть вырезы: готовый ролик {final} с (duration {duration} — до вырезов). Слои, камера, "
            "вставки, субтитры и сами cuts — шкала clip (ДО вырезов, как duration) и при рендере сдвигаются "
            "сами; то, что целиком внутри выреза, пропадает. Хук с 0 с остаётся в начале. Готовый ролик — "
            "шкала final (contact_sheet, listen)."
        ),
    }


MIN_FINISHED_SEC = 12.0


def short_clip_warning(finished_sec: float) -> str | None:
    """Проверочный прогон Gemini: апекс подрезан до 6 с — голая реакция без завязки."""
    if finished_sec >= MIN_FINISHED_SEC:
        return None
    return (f"Готовый ролик {finished_sec:.1f} с — слишком коротко: нет завязки, зритель не поймёт, что "
            f"случилось. Шортс 15–60 с: верни 5–15 с до момента (trim_piece, cuts), не режь до голой реакции.")


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


def _music_warning(ctx: ToolContext, render: dict) -> str | None:
    """Промо под тихое вступление трека: «Бодрый фон» с 0 с — барабаны только с 10 с."""
    if not render.get("music_on") or not render.get("track_id") or not _is_promo(ctx, render):
        return None
    from app.music_energy import ensure_energy, quiet_intro_sec

    try:
        [track] = ensure_energy(ctx.store, [ctx.store.get_audio_track(int(render["track_id"]))])
    except (KeyError, ValueError, TypeError):
        return None
    start = float(render.get("music_start_sec") or 0.0)
    quiet = quiet_intro_sec(track, start)
    if not quiet:
        return None
    energy = float(track["energy_start_sec"])
    return (f"Музыка: первые {quiet:g} с музыки тихие (вступление трека, энергичная часть с {energy:g} с) — "
            f"set_music(start_sec={energy:g})")


def h_listen(ctx: ToolContext, args: dict) -> dict:
    from app.clip_subtitles import listen_range

    try:
        return listen_range(ctx.store, ctx.plan_id, _num(args.get("from_sec")), _num(args.get("to_sec")),
                            before_cuts=bool(args.get("before_cuts")))
    except ValueError as exc:
        raise ToolError(f"listen: {exc}") from exc


def h_find_noises(ctx: ToolContext, args: dict) -> dict:
    from app.clip_subtitles import find_noises

    try:
        # Порог не задан — от медианы звука клипа (игра громкая, разговор — тихий).
        return find_noises(ctx.store, ctx.plan_id, _num(args.get("threshold_db")))
    except ValueError as exc:
        raise ToolError(f"find_noises: {exc}") from exc


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


def h_freeze_frame(ctx: ToolContext, args: dict) -> dict:
    """Стоп-кадр посреди ролика (app/freeze.py): кадр в at (шкала final) замирает на duration с."""
    from app.freeze import finished_with_freezes, freeze_problem, normalize_freezes

    render = _current_render(ctx)
    current = normalize_freezes(render.get("freezes"))
    clock = _clock(ctx)
    if args.get("reset"):
        fresh = _save_render(ctx, {**render, "freezes": []})
        return {"ok": True, "freezes": fresh.get("freezes") or [], "finished_duration": clock.final_duration}
    at = _num(args.get("at"))
    if args.get("remove"):
        if at is None:
            raise ToolError("remove: укажи at стоп-кадра (или reset: true — снять все)")
        kept = [f for f in current if abs(f["at"] - at) > 0.05]
        if len(kept) == len(current):
            raise ToolError(f"стоп-кадра в {at:g} с нет; есть: {[f['at'] for f in current]}")
        fresh = _save_render(ctx, {**render, "freezes": kept})
        return {"ok": True, "freezes": fresh.get("freezes") or []}
    raw = {"at": at, "duration": _num(args.get("duration"), 1.0), "audio": args.get("audio") or "silence",
           "layers": args.get("layers") or "freeze"}
    problem = freeze_problem(raw, clock.final_duration)
    if problem:
        raise ToolError(f"freeze_frame: {problem}")
    others = [f for f in current if abs(f["at"] - float(at)) > 0.05]
    wanted = normalize_freezes([*others, raw])
    if not any(abs(f["at"] - float(at)) <= 0.001 for f in wanted):
        raise ToolError("freeze_frame: на это место уже приходится другой стоп-кадр — "
                        f"есть {[{'at': f['at'], 'duration': f['duration']} for f in others]}")
    fresh = _save_render(ctx, {**render, "freezes": wanted})
    freezes = fresh.get("freezes") or []
    total = finished_with_freezes(clock.final_duration, freezes)
    insert = raw["audio"] == "silence"
    return {
        "ok": True, "freezes": freezes, "time": "final",
        "finished_duration": clock.final_duration, "finished_with_freezes": total,
        "note": (("кадр стоит, звук исходника молчит, музыка играет; ролик длиннее на "
                  f"{float(raw['duration']):g} с, всё после {float(at):g} с сдвинется" if insert else
                  "кадр стоит, звук идёт дальше; длина ролика та же")
                 + ("; слои и камера стоят с кадром" if raw["layers"] == "freeze" else "; слои анимируются дальше")
                 + f". Проверь view_frame(t, final: true) — t в готовом ролике со стоп-кадрами "
                   f"({total:g} с)"),
    }


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



# ---------------------------------------------------------------- монтаж «кнопками»


def h_suggest_cuts(ctx: ToolContext, args: dict) -> dict:
    from app.cuts import suggest_for_plan

    mode = args.get("mode") if args.get("mode") in ("pauses", "fillers", "both") else "both"
    # Явная проверка на None: keep_pad=0 («тишину у слов не оставлять») — законное значение.
    min_pause = _num(args.get("min_pause"))
    keep_pad = _num(args.get("keep_pad"))
    out = suggest_for_plan(ctx.store, ctx.plan_id, mode, 0.45 if min_pause is None else float(min_pause),
                           0.12 if keep_pad is None else float(keep_pad))
    # Подсказки считаются по словам «кусками подряд» — агенту отдаём шкалу clip (как apply_cuts
    # и get_clip): переданные как есть в apply_cuts, они встанут ровно туда же.
    clock = _clock(ctx)
    return {**out, "cuts": plain_cuts_to_clip(clock, out.get("cuts") or []),
            "items": plain_cuts_to_clip(clock, out.get("items") or []), "time": "clip",
            "before_sec": clock.clip_duration,
            "after_sec": round(max(0.0, clock.clip_duration - float(out.get("saved_sec") or 0)), 3),
            "note": "шкала clip (до вырезов, как слои и субтитры get_clip); передай cuts в apply_cuts как есть"}


def clip_cuts_to_plain(clock: Any, raw: Any) -> Any:
    """Вырезы агента (шкала clip) → «куски подряд», как их хранит рендер. ``piece`` у выреза —
    чей он в зоне наплыва (get_clip и suggest_cuts его дают)."""
    if not isinstance(raw, list) or not any(clock.pre.eaten):
        return raw  # без наплывов шкалы совпадают
    out = []
    for cut in raw:
        if not isinstance(cut, dict):
            out.append(cut)
            continue
        a, b = _num(cut.get("start")), _num(cut.get("end"))
        if a is None or b is None:
            out.append(cut)
            continue
        piece = cut.get("piece")
        piece = int(piece) if isinstance(piece, (int, float)) and not isinstance(piece, bool) else None
        start, end = clock.clip_range_to_plain(a, b, piece)
        out.append({"start": start, "end": end})
    return out


def _plain_span_to_clip(clock: Any, start: float, end: float) -> tuple[float, float]:
    parts = clock.plain_range_to_clip(start, end)
    return (parts[0]["start"], parts[-1]["end"]) if parts else (start, end)


def h_apply_cuts(ctx: ToolContext, args: dict) -> dict:
    from app.cuts import MIN_ISLAND_SEC, check_cuts_for_plan, cut_warnings, cuts_total, merge_short_islands, plan_duration

    plan = ctx.store.get_clip_plan(ctx.plan_id)
    clock = plan_clock(ctx.store, plan)
    raw = clip_cuts_to_plain(clock, args.get("cuts") or [])
    cuts, problems = check_cuts_for_plan(plan, raw)
    if problems:
        raise ToolError("вырезы не приняты", problems)
    min_island = _num(args.get("min_island_sec"))
    min_island = MIN_ISLAND_SEC if min_island is None else float(min_island)
    if min_island < 0:
        raise ToolError("min_island_sec — секунды, ≥ 0 (0 — не проверять обрывки)")
    words = (ctx.store.get_clip_plan_subtitles(ctx.plan_id) or {}).get("words") or []
    segments, total = plan.get("segments") or [], plan_duration(plan)
    merged: list[dict] = []
    if args.get("merge_short_islands") is True:
        wider, merged = merge_short_islands(cuts, words, segments, total, min_island)
        cuts, problems = check_cuts_for_plan(plan, wider)
        if problems:
            raise ToolError("вырезы не приняты", problems)
    ctx.before_change()
    settings = dict(_current_render(ctx))
    settings["cuts"] = cuts
    ctx.store.set_clip_plan_render_settings(ctx.plan_id, settings)
    out: dict = {"cuts": len(cuts), "saved_sec": round(cuts_total(cuts), 2), "time": "clip",
                 "finished_duration": _clock(ctx).final_duration}
    if merged:
        out["merged_islands"] = [{"start": a, "end": b, "text": i["text"]}
                                 for i in merged for a, b in [_plain_span_to_clip(clock, i["start"], i["end"])]]
    warnings = cut_warnings(cuts, words, segments, total, lambda a, b: _plain_span_to_clip(clock, a, b), min_island)
    if cuts and _source_keyed(ctx):
        # Владелец: «на 4-5 секунде персонаж моргает на склейке» — втубер стоит
        # неподвижно, и вырезанная пауза видна как рывок головы.
        warnings.append("Говорящая голова на зелёнке: вырез посреди фразы виден как рывок персонажа. "
                        "Режь только длинные паузы между фразами и прячь стык (смена плана за персонажем, "
                        "наезд камеры); короткие паузы лучше оставить.")
    if warnings:
        out["warnings"] = warnings
    return out


def h_auto_edit(ctx: ToolContext, args: dict) -> dict:
    from app.auto_edit import apply_auto_edit

    ctx.before_change()
    return apply_auto_edit(ctx.store, ctx.plan_id, style=str(args.get("style") or "hormozi"),
                           keep_layers=args.get("keep_layers", True) is not False, playbook=ctx.playbook)


def h_compose_cover(ctx: ToolContext, args: dict) -> dict:
    from app.cover_compose import compose_cover

    t, scale = _clip_time_arg(ctx, args)
    ctx.before_change()
    out = compose_cover(ctx.store, ctx.plan_id, t, str(args.get("title") or ""),
                        emoji=str(args.get("emoji") or ""), style=str(args.get("style") or "yellow"),
                        y=float(_num(args.get("y"), 0.2) or 0.2))
    return {**out, "t": round(t, 3), **scale}


def h_make_compilation(ctx: ToolContext, args: dict) -> dict:
    from app.compilation import build_compilation

    from app.compilation import _unique_ids

    try:
        ids = _unique_ids(args.get("clip_ids") or [])
    except ValueError as exc:
        raise ToolError(str(exc)) from None
    if len(ids) < 2:
        raise ToolError("нужно хотя бы два разных clip_ids")
    try:
        first = ctx.store.get_clip_plan(ids[0])
    except KeyError:
        raise ToolError(f"клип {ids[0]} не найден") from None
    return build_compilation(ctx.store, first["source_id"], ids, title=str(args.get("title") or ""),
                             countdown=args.get("countdown", True) is not False)


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


INSERT_KIND_WORDS = {"image": "картинка", "gif": "гифка", "video": "видео", "audio": "звук"}


def insert_facts(ctx: ToolContext) -> list[dict]:
    """Вставки клипа по факту — для честного отчёта (QA 01.11: агент назвал гифкой статичную картинку):
    слои image/video и вставки add_insert с типом файла библиотеки. ``[{what, kind, at, end, id}]``,
    время — шкала clip."""
    render = _current_render(ctx)

    def asset_of(asset_id: Any) -> dict:
        try:
            return ctx.store.get_montage_asset(int(asset_id))
        except (KeyError, TypeError, ValueError):
            return {}

    def asset_kind(asset_id: Any) -> str | None:
        return asset_of(asset_id).get("kind")

    def people(asset_id: Any) -> dict:
        """Реальный человек на файле по кэшу проверки (app/ai/people_check.py) — поле факта для отчёта."""
        check = asset_of(asset_id).get("people_check") if asset_id is not None else None
        if isinstance(check, dict) and check.get("real_person") is True:
            return {"real_person": str(check.get("what") or "реальный человек")}
        return {}

    out = []
    for layer in _layers_of(render):
        if layer.get("hidden") or layer.get("type") not in ("image", "video"):
            continue
        props = layer.get("props") if isinstance(layer.get("props"), dict) else {}
        src = str(props.get("src") or "")
        if src.startswith("emoji:"):
            continue
        kind = "video" if layer.get("type") == "video" else (asset_kind(props.get("asset_id")) or (
            "gif" if src.lower().split("?")[0].endswith(".gif") else "image"))
        out.append({"what": f"слой {layer.get('type')} {layer.get('id')}", "kind": kind,
                    "at": round(_num(layer.get("start"), 0.0) or 0.0, 2), "end": round(_num(layer.get("end"), 0.0) or 0.0, 2),
                    "id": str(layer.get("id") or ""), **people(props.get("asset_id"))})
    for index, ins in enumerate(render.get("inserts") or []):
        kind = asset_kind(ins.get("asset_id")) or "image"
        at = _num(ins.get("at"), 0.0) or 0.0
        out.append({"what": f"вставка {ins.get('mode') or 'pip'} #{index}", "kind": kind, "at": round(at, 2),
                    "end": round(at + (_num(ins.get("duration"), 1.5) or 1.5), 2), "id": f"insert#{index}",
                    **people(ins.get("asset_id"))})
    out.sort(key=lambda f: f["at"])
    return out


def describe_inserts(facts: list[dict]) -> str:
    return "; ".join(f"{INSERT_KIND_WORDS.get(f['kind'], f['kind'])} {f['at']:g}–{f['end']:g} с ({f['what']})"
                     for f in facts)


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


def h_set_end_hold(ctx: ToolContext, args: dict) -> dict:
    """Стоп-кадр в конце ролика на клип: 0 — мем обрывается на последнем кадре, null — как у сервиса."""
    from app.clip_settings import END_HOLD_MAX_SEC, normalize_end_hold
    from app.settings import settings

    raw = None if args.get("reset") else args.get("sec", args.get("end_hold_sec"))
    value = normalize_end_hold(raw)
    if raw not in (None, "") and value is None:
        raise ToolError(f"sec — число 0..{END_HOLD_MAX_SEC:g} или null (как у сервиса)")
    fresh = _save_render(ctx, {**_current_render(ctx), "end_hold_sec": value})
    default = float(settings.render_end_hold_sec or 0)
    effective = fresh["end_hold_sec"] if fresh["end_hold_sec"] is not None else default
    return {"ok": True, "end_hold_sec": fresh["end_hold_sec"], "effective_sec": effective,
            "note": ("готовый ролик кончится ровно на последнем кадре" if effective == 0 else
                     f"последний кадр стоит {effective:g} с, пока затухает звук; музыка доигрывает на нём")}


def h_list_music(ctx: ToolContext, _args: dict) -> dict:
    """Фоновая музыка сервиса (пак CC0 + загруженная владельцем) с отметками энергии."""
    from app.music_energy import energy_view, ensure_energy

    tracks = [
        {"id": t["id"], "label": t.get("label") or "", "duration": round(float(t.get("duration_sec") or 0), 1),
         **energy_view(t)}
        for t in ensure_energy(ctx.store)  # старые треки размечаются лениво, по одному
    ]
    return {"tracks": tracks,
            "note": ("громкость фона выставляется сама — заметно тише голоса клипа и приглушается под речь. "
                     "energy_start_sec — с какой секунды трек звучит в полную силу (до неё тихое вступление), "
                     "drop_sec — самый сильный подъём, bpm — темп. Промо/холст: set_music(start_sec=energy_start_sec)")}


def _music_track_hint(track: dict, start: float, promo: bool) -> dict:
    """Отметки трека и подсказка «пропусти тихое вступление» (start_sec не меняем сами)."""
    from app.music_energy import energy_view, quiet_intro_sec

    out: dict = {}
    marks = energy_view(track)
    if marks:
        out["track_energy"] = marks
    quiet = quiet_intro_sec(track, start)
    if quiet and promo:
        energy = marks.get("energy_start_sec")
        out["hint"] = (f"первые {quiet:g} с трека тихие — энергичная часть с {energy:g} с: "
                       f"set_music(start_sec={energy:g})")
    return out


def h_mute_audio(ctx: ToolContext, args: dict) -> dict:
    """Тишина вместо звука исходника на отрезках (секунды клипа до вырезов), картинка остаётся."""
    from app.audio_mute import normalize_mute

    ranges = args.get("ranges")
    if not isinstance(ranges, list):
        raise ToolError("ranges — список {start, end} (секунды клипа до вырезов; [] — снять всё)")
    duration = _clip_duration(ctx)
    clean = [m for m in normalize_mute(ranges) if m["start"] < duration]
    render = _current_render(ctx)
    note = "звук исходника там заглушён; звуки слоёв и музыка играют. Проверь listen (он слышит mute)."
    # Отрезки цензуры мата (by: censor, app/censor.py) — свои: список без помеченных их не стирает.
    censored = [m for m in render.get("mute") or [] if m.get("by") == "censor"]
    if censored and not any(m.get("by") for m in clean):
        clean = normalize_mute(clean + censored)
        note += (f" Отрезки мата ({len(censored)}, by: censor) оставлены — снять их: "
                 "censor_profanity(clear=true).")
    fresh = _save_render(ctx, {**render, "mute": clean})
    out = {"ok": True, "mute": fresh["mute"], "note": note}
    warnings = _track_warnings(ctx, fresh["mute"])
    if warnings:
        out["warnings"] = warnings
    return out


def _track_warnings(ctx: ToolContext, spans: list[dict]) -> list[str]:
    """Отрезки дорожки, которой нет в звуке ролика (set_source_audio), ничего не изменят."""
    from app.source_audio import source_tracks

    tracks = {s["track"] for s in spans if s.get("track") is not None}
    if not tracks:
        return []
    plan = ctx.store.get_clip_plan(ctx.plan_id, include_segments=False)
    mix = source_tracks(ctx.store.get_source(int(plan["source_id"]))) or [0]
    missing = sorted(t for t in tracks if t not in mix)
    return ([f"дорожек {missing} нет в звуке ролика (смесь {mix}) — их отрезки ничего не изменят; "
             "set_source_audio tracks"] if missing else [])


def h_censor_profanity(ctx: ToolContext, args: dict) -> dict:
    """Мат одной командой (app/censor.py): тишина серединой слова, звёздочки в субтитрах и,
    если verify, круги «рендер → Whisper по готовому файлу → шире, где узнаётся»."""
    from app import censor

    notes: list[str] = []
    soft = bool(args.get("soft"))
    groups = args.get("groups") or None
    if ctx.request and not censor.owner_asks_soft(ctx.request):
        # QA 4 (клип 896): soft включил агент сам — заглушил панчлайн «обосралась». Мягкая брань (блин,
        # жопа, срать) — не мат, площадки за неё не режут; глушим её, только если владелец просил.
        if soft or (isinstance(groups, (list, tuple)) and "soft" in groups):
            notes.append("мягкая брань (soft: блин, жопа, срать…) — не мат; владелец её глушить не просил — не глушу")
        soft = False
        if isinstance(groups, (list, tuple)):
            groups = [g for g in groups if g != "soft"] or None
    options = {"stars": args.get("stars", True) is not False, "soft": soft,
               "groups": groups, "skip": args.get("skip") or ()}
    keep = censor_keep_words(ctx)
    if keep:
        options["keep"] = keep
    if args.get("force_words"):
        options["force_words"] = [int(i) for i in args.get("force_words") or () if str(i).lstrip("-").isdigit()]
    if args.get("track") not in (None, ""):
        options["track"] = args.get("track")  # только эта дорожка (число или "voice"), app/censor.py
    if args.get("style") not in (None, ""):
        try:
            options["style"] = censor.clamp_style(args.get("style"))  # чем заполнить середину слова
        except ValueError as exc:
            raise ToolError(f"censor_profanity: {exc}") from exc
    levels = args.get("levels")
    if args.get("level") is not None and levels is None:
        levels = args.get("level")
    if censor.job_running(ctx.plan_id):
        if args.get("verify", True) is False or args.get("clear"):
            raise ToolError("идёт проверка цензуры этого клипа (рендер + Whisper) — дождись её: "
                            "повтори censor_profanity без verify=false")
    # QA 4 (клип 896): 5 вызовов подряд — явные levels/style/groups обходили «уже применено». Теперь
    # повторный вызов при стоящей цензуре — «уже применено», что бы ни передали; переделать — redo: true.
    redo = any(args.get(k) not in (None, "", [], False) for k in ("redo", "overwrite", "clear", "verify_again",
                                                                    "force_words"))
    if not redo and args.get("stars", True) is not False:
        try:
            done = censor.already_censored(ctx.store, ctx.plan_id, keep=keep)
        except Exception:  # noqa: BLE001 - не смогли сверить — делаем как обычно
            done = None
        if done:
            explicit = [k for k in ("levels", "level", "style", "groups", "soft", "skip", "track")
                        if args.get(k) not in (None, "", [], False)]
            if explicit:
                done["note"] += (f". Переданные {', '.join(explicit)} НЕ применены: цензура уже стоит — переделать "
                                 "её можно только с redo: true (и только если владелец просил)")
            return {"ok": True, "status": "already", "already_applied": True, **done,
                    **({"notes": notes} if notes else {})}
    try:
        if args.get("clear"):
            ctx.before_change()
            return {"ok": True, **censor.censor_plan(ctx.store, ctx.plan_id, clear=True)}
        if args.get("verify", True) is False:
            ctx.before_change()
            out = censor.censor_plan(ctx.store, ctx.plan_id, levels, **options)
            out["note"] += ". Без проверки: рендер и Whisper не запускались (verify=true — проверит сам)"
            return {"ok": True, "verified": False, **out, **({"notes": notes} if notes else {})}
        if not censor.job_running(ctx.plan_id):
            ctx.before_change()
        out = censor.censor_job(ctx.store, ctx.plan_id, levels=levels,
                                max_rounds=int(_num(args.get("max_rounds"), censor.DEFAULT_MAX_ROUNDS) or 3),
                                **options)
    except ValueError as exc:
        raise ToolError(f"censor_profanity: {exc}") from exc
    return {"ok": True, **out, **({"notes": notes} if notes else {})}


def h_add_music_from_url(ctx: ToolContext, args: dict) -> dict:
    """Музыка по ссылке (YouTube и т.п.) → новый трек фона."""
    from app.music_import import import_music_from_url

    try:
        track = import_music_from_url(ctx.store, str(args.get("url") or ""), str(args.get("label") or ""))
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    return {"ok": True, "track_id": track["id"], "label": track["label"],
            "duration": round(float(track.get("duration_sec") or 0), 1)}


def h_add_sound_from_url(ctx: ToolContext, args: dict) -> dict:
    """Звуковой эффект по ссылке владельца (страница freesound или видео) → asset_id для слоя audio."""
    from app.sound_import import MAX_SOUND_SEC, import_sound_from_url

    try:
        asset = import_sound_from_url(ctx.store, str(args.get("url") or ""), str(args.get("label") or ""),
                                      start=_num(args.get("start"), 0.0) or 0.0, project_id=_assets_project(ctx, args))
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - сеть/удалённый сайт
        raise ToolError(f"не удалось скачать звук: {str(exc)[:200]}") from exc
    return {"ok": True, "asset_id": asset["id"], "kind": asset["kind"], "label": asset["label"],
            "duration": round(float(asset.get("duration_sec") or 0), 2),
            "note": (f"обрезан до {MAX_SOUND_SEC:g} с; ставь слоем audio (props.asset_id) на слово (at_word), "
                     "громкость 0.3–0.6 — сервис сам держит эффект под голосом")}


def h_get_performance(ctx: ToolContext, args: dict) -> dict:
    """Что заходит: топ и антитоп опубликованных клипов по просмотрам с признаками монтажа."""
    from app.platform_stats import performance_report

    limit = int(_num(args.get("limit"), 5) or 5)
    return performance_report(ctx.store, limit)


def h_set_music(ctx: ToolContext, args: dict) -> dict:
    """Фоновая музыка клипа: on + track_id (list_music), start_sec — с какой секунды трека."""
    render = _current_render(ctx)
    on = args.get("on") is not False
    track_id = args.get("track_id")
    start = render.get("music_start_sec") or 0.0
    if on:
        changed_track = bool(track_id) and int(track_id) != render.get("track_id")
        track_id = track_id or render.get("track_id")
        if not track_id:
            raise ToolError("нужен track_id — см. list_music")
        try:
            track = ctx.store.get_audio_track(int(track_id))
        except (KeyError, ValueError, TypeError) as exc:
            raise ToolError(f"нет трека {track_id} — см. list_music") from exc
        if args.get("start_sec") is not None:
            start = _num(args.get("start_sec"), 0.0) or 0.0
        elif changed_track:
            start = 0.0  # новый трек — с начала, если не сказано иное
        length = float(track.get("duration_sec") or 0)
        if length and start >= length:
            raise ToolError(f"start_sec {start:g} — за концом трека ({length:g} с)")
    fresh = _save_render(ctx, {**render, "music_on": on, "track_id": int(track_id) if on else render.get("track_id"),
                               "music_start_sec": start})
    out = {"ok": True, "music_on": fresh["music_on"], "track_id": fresh["track_id"],
           "music_start_sec": fresh["music_start_sec"]}
    if on:
        from app.music_energy import ensure_energy

        [track] = ensure_energy(ctx.store, [track])
        out.update(_music_track_hint(track, float(fresh["music_start_sec"] or 0.0), _is_promo(ctx, fresh)))
    return out


def _is_promo(ctx: ToolContext, render: dict) -> bool:
    """Промо: клип-холст или в слоях есть телефон (device) — там музыка с тихим вступлением мешает."""
    return _source_canvas(ctx) or has_device_layers(_layers_of(render))


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


def _color_entries(ctx: ToolContext) -> tuple[dict, list[dict]]:
    from app.piece_color import normalize_color_fix

    render = _current_render(ctx)
    return render, normalize_color_fix(render.get("color_fix"))


def h_set_piece_color(ctx: ToolContext, args: dict) -> dict:
    """Цветокоррекция куска основы (app/piece_color.py): экспозиция, контраст, насыщенность, баланс."""
    from app.piece_color import FIELDS, describe, normalize_fix

    plan = ctx.store.get_clip_plan(ctx.plan_id)
    segments = plan.get("segments") or []
    index = _piece_index(args, len(segments))
    seg = segments[index]
    render, entries = _color_entries(ctx)
    current = normalize_fix(seg.get("color_fix"))
    problems = []
    for name, (_neutral, lo, hi) in FIELDS.items():
        value = args.get(name)
        if value is None:
            continue
        number = _num(value)
        if number is None or not lo - 1e-9 <= number <= hi + 1e-9:
            problems.append(f"{name} — {lo:g}..{hi:g}")
    if problems:
        raise ToolError("цвет куска не принят", problems)
    if args.get("reset"):
        fix: dict = {}
    else:
        fix = {**current, **{k: args[k] for k in FIELDS if args.get(k) is not None}}
    fix = normalize_fix(fix)
    entries = [e for e in entries if e["piece"] != seg["id"]] + ([{"piece": seg["id"], **fix}] if fix else [])
    saved = _save_render(ctx, {**render, "color_fix": entries})
    out: dict = {"ok": True, "piece": index, "color_fix": fix, "summary": describe(fix) or "как в исходнике",
                 "pieces_with_color": len(saved.get("color_fix") or []),
                 "note": "поправка куска — до лука пресета; exposure в ступенях (EV), temperature + теплее / − "
                         "холоднее, tint + пурпурнее / − зеленее. Подогнать куски друг к другу по замеру — "
                         "match_piece_colors. Проверь view_frame на этом куске."}
    warnings = []
    if _source_has_key(ctx, plan):
        warnings.append("у источника хромакей — цвет куска к основе не применяется (сорвал бы ключ)")
    if abs(fix.get("exposure", 0.0)) > 1.2:
        warnings.append("экспозиция больше ±1.2 EV — светлое выгорит / тёмное зашумит, проверь кадр")
    if warnings:
        out["warnings"] = warnings
    return out


def _source_has_key(ctx: ToolContext, plan: dict) -> bool:
    try:
        return bool(ctx.store.get_source(int(plan["source_id"])).get("chroma_key"))
    except (KeyError, TypeError, ValueError):
        return False


def h_match_piece_colors(ctx: ToolContext, args: dict) -> dict:
    """Подогнать цвет кусков к образцу по замеру signalstats (app/piece_color.py)."""
    from app.heavy_queue import heavy_slot
    from app.piece_color import describe, match_fix, measure, normalize_fix, predict
    from app.piece_speed import seg_length

    plan = ctx.store.get_clip_plan(ctx.plan_id)
    segments = plan.get("segments") or []
    if len(segments) < 2:
        raise ToolError("в клипе один кусок — подгонять не к чему (цвет куска — set_piece_color)")
    if args.get("reference") is None:
        reference = max(range(len(segments)), key=lambda i: seg_length(segments[i]))
    else:
        reference = _piece_index({"piece": args.get("reference")}, len(segments))
    targets = args.get("pieces")
    if targets is None:
        targets = [i for i in range(len(segments)) if i != reference]
    try:
        targets = sorted({int(i) for i in targets} - {reference})
    except (TypeError, ValueError) as exc:
        raise ToolError("pieces — номера кусков с 0") from exc
    if any(not 0 <= i < len(segments) for i in targets):
        raise ToolError(f"pieces: куски 0..{len(segments) - 1}")
    strength = _num(args.get("strength"))
    strength = 1.0 if strength is None else min(1.0, max(0.0, strength))
    source = ctx.store.get_source(int(plan["source_id"]))
    path = Path(str(source.get("local_path") or ""))
    if not path.is_file():
        raise ToolError("исходник недоступен — замерить кадры нечем")
    if source.get("chroma_key"):
        raise ToolError("у источника хромакей — цвет основы не трогаем (сорвал бы ключ)")
    crop = source.get("content_crop") or None
    stats: dict[int, dict] = {}
    try:
        with heavy_slot("color_match", f"клип {ctx.plan_id}"):
            for i in [reference, *targets]:
                seg = segments[i]
                stats[i] = measure(path, float(seg["start_sec"]), float(seg["end_sec"]), crop)
    except (ValueError, OSError) as exc:
        raise ToolError(f"замер не удался: {exc}") from exc
    target = predict(stats[reference], segments[reference].get("color_fix"))
    render, entries = _color_entries(ctx)
    by_id = {e["piece"]: e for e in entries}
    report = []
    for i in targets:
        seg = segments[i]
        fix = match_fix(stats[i], target, seg.get("color_fix"), strength)
        if fix:
            by_id[seg["id"]] = {"piece": seg["id"], **fix}
        else:
            by_id.pop(seg["id"], None)
        after = predict(stats[i], fix)
        report.append({"piece": i, "before": stats[i], "after": after, "color_fix": fix,
                       "summary": describe(fix) or "без поправки",
                       "luma_gap_before": round(abs(stats[i]["y"] - target["y"]), 1),
                       "luma_gap_after": round(abs(after["y"] - target["y"]), 1)})
    order = {seg["id"]: k for k, seg in enumerate(segments)}
    fresh = sorted(by_id.values(), key=lambda e: order.get(e["piece"], 10**6))
    _save_render(ctx, {**render, "color_fix": fresh})
    out: dict = {"ok": True, "reference": reference, "reference_stats": stats[reference], "target": target,
                 "strength": strength, "pieces": report,
                 "note": "средние яркость (Y), U/V и насыщенность кусков сведены к образцу по 5 кадрам каждого. "
                         "Это выравнивание, а не лук: лук пресета ложится поверх. Ночь и день так не сравнять — "
                         "проверь view_frame/contact_sheet; поправить руками — set_piece_color, снять — reset."}
    clipped = [r["piece"] for r in report if r["luma_gap_after"] > 8]
    if clipped:
        out["warnings"] = [f"куски {clipped}: разница слишком большая (поправка упёрлась в предел ±1.5 EV / "
                           "насыщенность 0.4..1.8) — сцены разные, подгоняй мягче (strength) или оставь"]
    return out


def _beat_grid(ctx: ToolContext, args: dict, plan: dict, clock: Any) -> tuple[list[float], str, dict]:
    """Сетка долей для snap_to_beats: (моменты, шкала "final"/"clip", описание)."""
    from app.beat_snap import describe_grid, grid_times

    kind = str(args.get("grid") or "beat")
    if args.get("beats") is not None:
        try:
            beats = sorted({round(float(t), 4) for t in args["beats"]})
        except (TypeError, ValueError) as exc:
            raise ToolError("beats — список секунд готового ролика") from exc
        return beats, "final", {"source": "beats", "count": len(beats), "time": "final"}
    if args.get("bpm") is not None:
        bpm = _num(args.get("bpm"))
        if bpm is None or not 40 <= bpm <= 240:
            raise ToolError("bpm — 40..240")
        first = _num(args.get("first_beat")) or 0.0
        down = _num(args.get("first_downbeat"))
        grid = {"bpm": bpm, "period": 60.0 / bpm, "first_beat": first,
                "first_downbeat": first if down is None else down, "downbeat_every": 4, "time": "final"}
        return grid_times(grid, clock.final_duration, kind), "final", describe_grid(grid, kind, "manual")
    source = str(args.get("source") or "auto")
    if source in ("auto", "music"):
        from app.audio_map import _music_beats

        grid = _music_beats(ctx.store, plan.get("render_settings") or {}, clock)
        if grid:
            return grid_times(grid, clock.final_duration, kind), "final", describe_grid(grid, kind, "music")
        if source == "music":
            raise ToolError("у клипа нет музыки set_music или в ней не слышно пульса — передай bpm и first_beat "
                            "(или beats) сам, либо source: \"audio\" — доли звука клипа")
    from app.audio_map import clip_audio_map

    try:
        view, _png = clip_audio_map(ctx.store, ctx.plan_id)
    except ValueError as exc:
        raise ToolError(f"доли звука клипа: {exc}") from exc
    grid = view.get("beats")
    if not grid:
        raise ToolError("в звуке клипа нет пульса (и музыки set_music нет) — передай bpm и first_beat сам")
    return grid_times(grid, clock.clip_duration, kind), "clip", describe_grid(grid, kind, "audio")


def h_snap_to_beats(ctx: ToolContext, args: dict) -> dict:
    """Начала слоёв, ключи, камера и склейки — на ближайшую долю (app/beat_snap.py)."""
    from app.beat_snap import (
        DEFAULT_TOLERANCE, GRIDS, MAX_TOLERANCE, MIN_PIECE_SEC, TARGETS, Scale, nearest, snap_camera, snap_layers,
    )
    from app.clip_spec import SpecError, apply_spec, export_spec, validate_spec
    from app.piece_speed import is_reversed, seg_speed
    from app.timebase import ClipClock

    raw_targets = args.get("targets") or ["layers"]
    if isinstance(raw_targets, str):
        raw_targets = [raw_targets]
    targets = set(TARGETS) if "all" in raw_targets else {str(t) for t in raw_targets}
    if not targets or targets - set(TARGETS):
        raise ToolError(f"targets — из {', '.join(TARGETS)} (или all)")
    grid_kind = str(args.get("grid") or "beat")
    if grid_kind not in GRIDS:
        raise ToolError(f"grid — {', '.join(GRIDS)}")
    tolerance = _num(args.get("tolerance"))
    tolerance = DEFAULT_TOLERANCE if tolerance is None else tolerance
    if not 0.01 <= tolerance <= MAX_TOLERANCE:
        raise ToolError(f"tolerance — 0.01..{MAX_TOLERANCE:g} с (дальше — это уже другой момент, не «чуть мимо»)")
    dry = bool(args.get("dry_run"))
    only = {str(i) for i in args.get("layer_ids") or []} or None

    plan = ctx.store.get_clip_plan(ctx.plan_id)
    clock = ClipClock.for_plan(plan)
    beats, scale_name, grid_info = _beat_grid(ctx, args, plan, clock)
    if not beats:
        raise ToolError("в сетке нет ни одной доли на длине ролика")
    changes: list[dict] = []
    problems: list[str] = []

    if "joins" in targets and len(plan.get("segments") or []) > 1:
        segments = plan["segments"]
        to_grid = clock.clip_to_final if scale_name == "final" else (lambda t: float(t))
        spec = export_spec(ctx.store, ctx.plan_id)
        pieces = [dict(p) for p in spec["pieces"]]
        duration = float(spec["source"].get("duration_sec") or 0) or math.inf
        shift = 0.0
        moved = False
        for k in range(len(segments) - 1):
            join = (clock.clip_starts[k + 1] + clock.clip_end(k)) / 2
            g = to_grid(join) + shift
            b = nearest(g, beats, tolerance)
            if b is None or abs(b - g) < 0.004:
                continue
            delta = round(b - g, 3)
            seg, piece = segments[k], pieces[k]
            src_delta = delta * seg_speed(seg)
            start, end = float(piece["start_sec"]), float(piece["end_sec"])
            if is_reversed(seg):
                start = round(start - src_delta, 3)  # конец развёрнутого куска на клипе — начало в исходнике
            else:
                end = round(end + src_delta, 3)
            if start < 0 or end > duration or end - start < MIN_PIECE_SEC:
                problems.append(f"стык {k}→{k + 1}: до доли {delta:+.2f} с — кусок вышел бы за исходник или "
                                f"короче {MIN_PIECE_SEC:g} с, не трогаю")
                continue
            piece.update(start_sec=start, end_sec=end)
            piece.pop("focus", None)
            shift += delta
            moved = True
            changes.append({"target": "join", "index": k, "from": round(g - shift + delta, 3), "to": b, "delta": delta})
        if moved and not dry:
            try:
                validate_spec(ctx.store, ctx.plan_id, {**spec, "pieces": pieces})
            except SpecError as exc:
                raise ToolError("склейки к долям не приняты", exc.problems) from exc
            ctx.before_change()
            apply_spec(ctx.store, ctx.plan_id, {**spec, "pieces": pieces})
            plan = ctx.store.get_clip_plan(ctx.plan_id)
            clock = ClipClock.for_plan(plan)

    scale = Scale(clock.clip_to_final, clock.final_to_clip) if scale_name == "final" else Scale()
    render = _current_render(ctx)
    fresh = dict(render)
    if targets & {"layers", "keys"}:
        layers, moved_layers = snap_layers(_layers_of(render), beats, tolerance, scale, keys="keys" in targets,
                                           starts="layers" in targets, only=only)
        if moved_layers:
            fresh["layers"] = layers
            changes += moved_layers
    if "camera" in targets and render.get("camera"):
        camera, moved_cam = snap_camera(_camera_of(render), beats, tolerance, scale)
        if moved_cam:
            fresh["camera"] = camera
            changes += moved_cam
    if fresh != render and not dry:
        _save_render(ctx, fresh)
    out: dict = {"ok": True, "dry_run": dry, "moved": len(changes), "changes": changes[:80], "grid": grid_info,
                 "tolerance": tolerance, "time": scale_name,
                 "note": ("from/to — секунды шкалы сетки (final — готовый ролик, clip — до вырезов). Сдвинуто только то, "
                          f"что было ближе {tolerance:g} с к доле; слои сдвинуты целиком (длина та же), склейки — "
                          "подрезкой конца куска (всё после стыка едет за ним). Субтитры, чат и запертые слои не "
                          "трогаются. Проверь contact_sheet/listen.")}
    if dry and "joins" in targets:
        out["note"] += " dry_run: слои посчитаны без сдвига склеек."
    if not changes:
        out["note"] = f"ничего не сдвинуто: ни один момент не ближе {tolerance:g} с к доле ({grid_kind})"
    if problems:
        out["warnings"] = problems
    return out


def h_set_transition(ctx: ToolContext, args: dict) -> dict:
    from app.transitions import AUDIO_MODES, SFX_KINDS, TRANSITIONS

    render = _current_render(ctx)
    transition = dict(render.get("transition") or {})
    problems = []
    if args.get("type") is not None:
        if args["type"] not in TRANSITIONS:
            problems.append(f"type: одно из {', '.join(TRANSITIONS)}")
        transition["type"] = args["type"]
    if args.get("sfx") is not None:
        if args["sfx"] not in SFX_KINDS:
            problems.append(f"sfx: одно из {', '.join(SFX_KINDS)}")
        transition["sfx"] = args["sfx"]
    if args.get("audio") is not None:
        if args["audio"] not in AUDIO_MODES:
            problems.append(f"audio: одно из {', '.join(AUDIO_MODES)}")
        transition["audio"] = args["audio"]
    for key in ("duration", "sfx_volume", "motion_blur"):
        if args.get(key) is not None:
            transition[key] = args[key]
    if args.get("smart") is not None:
        transition["smart"] = bool(args["smart"])
    if problems:
        raise ToolError("переход не принят", problems)
    from app.transitions import MIN_DURATION, SNAP_DURATIONS, normalize_transition, speech_join_warnings, \
        speech_under_joins

    plan = ctx.store.get_clip_plan(ctx.plan_id)
    segments = plan.get("segments") or []
    words = (ctx.store.get_clip_plan_subtitles(ctx.plan_id) or {}).get("words") or []
    notes = []
    if args.get("fit_speech"):
        # Наплыв не должен уводить слова под переход: длину — по самому тесному стыку.
        report = speech_under_joins(segments, transition, words)
        kind = normalize_transition(transition)["type"]
        if report["hit"] and kind not in SNAP_DURATIONS:
            if report["safe_duration"] >= MIN_DURATION:
                transition["duration"] = report["safe_duration"]
                notes.append(f"длина перехода {report['safe_duration']:.2f} с — наплыв не заходит на речь")
            else:
                notes.append("на каком-то стыке речь вплотную к склейке — переход любой длины заденет слова; "
                             "подрежь куски (см. warnings) или поставь type cut")
    fresh = _save_render(ctx, {**render, "transition": transition})
    out: dict = {"ok": True, "transition": fresh["transition"]}
    warnings = speech_join_warnings(speech_under_joins(segments, fresh["transition"], words), fresh["transition"])
    if warnings:
        out["warnings"] = warnings
    if notes:
        out["note"] = "; ".join(notes)
    return out


def ending_warning(store: Any, source_id: int, end_sec: float) -> str | None:
    """Совет, если клип кончается на полуслове или вплотную к последнему слову.

    Владелец: «только концовки обрываются». Конец последнего куска — время
    исходника; сверяем с расшифровкой: внутри фразы → договорить; ровно на
    её конце (меньше 0.3 с) → оставить хвост, Whisper ставит конец рано."""
    try:
        cues = store.get_source_transcript(source_id)
    except KeyError:
        return None
    for cue in cues:
        s, e = float(cue.get("start") or 0), float(cue.get("end") or 0)
        if s + 0.1 < end_sec < e - 0.05:
            text = " ".join(str(cue.get("text") or "").split())[-60:]
            return (f"клип обрывается на полуслове: фраза «…{text}» идёт до {e:.2f} с исходника — "
                    f"продли последний кусок до ≈{e + 0.5:.2f}")
        if e - 0.05 <= end_sec < e + 0.3:
            return f"конец вплотную к последнему слову ({e:.2f}) — оставь хвост 0.4–0.8 с (до ≈{e + 0.6:.2f})"
    return None


def h_trim_piece(ctx: ToolContext, args: dict) -> dict:
    from app.clip_spec import SpecError, apply_spec, export_spec

    try:
        index = int(args.get("index"))
    except (TypeError, ValueError) as exc:
        raise ToolError("нужен index куска (с 0)") from exc
    spec = export_spec(ctx.store, ctx.plan_id)
    pieces = spec["pieces"]
    if not 0 <= index < len(pieces):
        raise ToolError(f"куска {index} нет (0..{len(pieces) - 1})")
    piece = dict(pieces[index])
    # source_start/source_end — прежние имена из get_clip (алиасы).
    start = _num(args.get("start_sec", args.get("source_start")), piece["start_sec"])
    end = _num(args.get("end_sec", args.get("source_end")), piece["end_sec"])
    piece.update(start_sec=start, end_sec=end)
    piece.pop("focus", None)  # трек кадра остаётся прежним
    pieces = [*pieces[:index], piece, *pieces[index + 1:]]
    try:
        from app.clip_spec import validate_spec

        validate_spec(ctx.store, ctx.plan_id, {**spec, "pieces": pieces})
    except SpecError as exc:
        raise ToolError("подрезка не принята", exc.problems) from exc
    ctx.before_change()
    _fresh, changes = apply_spec(ctx.store, ctx.plan_id, {**spec, "pieces": pieces})
    timeline = clip_timeline(ctx.store, ctx.plan_id)
    out = {"ok": True, "changes": changes, "duration": timeline["duration"],
           "note": "слои, камера, вставки и субтитры сдвинуты вслед за куском; если кусок расширили на новую речь — generate_subtitles"}
    if index == len(pieces) - 1:
        warn = ending_warning(ctx.store, int(spec["source"]["id"]), float(end))
        if warn:
            out["warnings"] = [warn]
    return out


MAX_FIT_PIECE_SEC = 12.0


def h_set_segment_speed(ctx: ToolContext, args: dict) -> dict:
    """Скорость и реверс куска основы (app/piece_speed.py): slow-mo, ускорение, «перемотка»."""
    from app.audio_edit import normalize_audio
    from app.piece_remap import remap_render
    from app.piece_speed import (
        AUDIO_MODES, MAX_REVERSE_SEC, MAX_SPEED, MIN_SPEED, attach, default_audio, describe, normalize_speed,
        seg_length,
    )

    plan = ctx.store.get_clip_plan(ctx.plan_id)
    segments = plan.get("segments") or []
    index = _piece_index(args, len(segments))
    seg = segments[index]
    speed = 1.0 if args.get("speed") in (None, "") else _num(args.get("speed"))
    if speed is None or not MIN_SPEED <= speed <= MAX_SPEED:
        raise ToolError(f"speed — {MIN_SPEED:g}..{MAX_SPEED:g} (0.5 — вдвое медленнее, 2 — вдвое быстрее; 1 — как есть)")
    reverse = args.get("reverse") is True
    audio = str(args.get("audio") or "").strip()
    if audio and audio not in AUDIO_MODES:
        raise ToolError(f"audio — из {', '.join(AUDIO_MODES)}")
    if audio == "reverse" and not reverse:
        raise ToolError("audio \"reverse\" — только с reverse: true")
    source_len = float(seg["end_sec"]) - float(seg["start_sec"])
    if reverse and source_len > MAX_REVERSE_SEC + 1e-6:
        raise ToolError(f"реверс — кусок до {MAX_REVERSE_SEC:g} с исходника (этот {source_len:.1f} с): ffmpeg держит "
                        "все кадры в памяти. Разрежь кусок (trim_piece / create_clip) и разверни короткий")
    render = _current_render(ctx)
    entries = [e for e in normalize_speed(render.get("speed")) if e["piece"] != seg["id"]]
    entry = normalize_speed([{"piece": seg["id"], "speed": speed, "reverse": reverse, "audio": audio}])
    fresh = {**render, "speed": entries + entry}
    new_segments = attach([{k: v for k, v in s.items() if k not in ("speed", "reverse", "speed_audio")}
                           for s in segments], fresh)
    # Слои, камера, вставки, вырезы — за новой длиной куска (секунда исходника — та же).
    moved, note = remap_render(fresh, segments, new_segments)
    saved = _save_render(ctx, moved)
    piece = clip_timeline(ctx.store, ctx.plan_id)["pieces"][index]
    new_seg = new_segments[index]
    out: dict = {"ok": True, "piece": index, "speed": speed, "reverse": reverse,
                 "audio": (entry[0].get("audio") or default_audio(entry[0])) if entry else None,
                 "length_before": round(seg_length(seg), 3), "length": round(seg_length(new_seg), 3),
                 "clip_start": piece["clip_start"], "clip_end": piece["clip_end"],
                 "duration": clip_timeline(ctx.store, ctx.plan_id)["duration"], "time": "clip",
                 "speeds": saved.get("speed") or [],
                 "note": ("кусок на шкале clip теперь длиной (конец − начало исходника)/speed; focus куска — по-прежнему "
                          "секунды ИСХОДНИКА от его начала. Звук: tempo — та же речь без смены тона, pitch — как плёнка, "
                          "mute — тишина, reverse — задом наперёд. Субтитры куска растянуты в 1/speed, у реверса — "
                          "скрыты. Проверь view_frame/contact_sheet и рендер.")}
    if note:
        out["changes"] = [note]
    warnings = []
    if speed < 0.5:
        warnings.append("медленнее 0.5 при 30 к/с — кадры повторяются, движение «ступеньками»; для слоу-мо лучше "
                        "исходник 60 к/с")
    if reverse and normalize_audio(render.get("audio")).get("joins"):
        warnings.append("J/L на стыках с развёрнутым куском не работают — звук за краем к реверсу не относится")
    if describe(new_seg) and seg_length(new_seg) > 20:
        warnings.append(f"кусок на клипе {seg_length(new_seg):.1f} с — длинное слоу-мо утомляет; обычно 1–4 с")
    if warnings:
        out["warnings"] = warnings
    return out


def h_set_piece_fit(ctx: ToolContext, args: dict) -> dict:
    """Кусок шире окна 9:16: в кадре доля w ширины исходника, сверху и снизу фон."""
    from app.video_crop import FIT_MIN_W, normalize_fit, window_share

    timeline = clip_timeline(ctx.store, ctx.plan_id)
    index = _piece_index(args, len(timeline["pieces"]))
    w = args.get("w")
    if w is None or w is False or _num(w) == 0:
        fit = None
    else:
        value = _num(w)
        if value is None or not FIT_MIN_W <= value <= 1.0:
            raise ToolError(f"w — доля ширины исходника в кадре, {FIT_MIN_W}..1 (1 — весь кадр); null — обычный кадр")
        fit = normalize_fit({"w": value, "bg": args.get("bg") or "blur"})
        piece = timeline["pieces"][index]
        length = float(piece["source_end"]) - float(piece["source_start"])
        if length > MAX_FIT_PIECE_SEC:
            # Проверочный прогон Gemini: fit на весь 40-секундный клип — ролик превратился в
            # мелкую полоску на размытом фоне, как у дешёвых перезаливов.
            raise ToolError(
                f"кусок {length:.0f} с — «шире окна» на весь кусок делает ролик мелким видео на размытом фоне. "
                f"Сначала auto_focus (говорящий в кадре по планам). fit — только короткий кусок ≤ {MAX_FIT_PIECE_SEC:.0f} с, "
                "где двое по краям: разрежь кусок по склейке плана (trim_piece / create_clip)")
    fresh, changes = _apply_piece(ctx, index, fit=fit)
    out: dict = {"ok": True, "piece": index, "fit": fresh.get("fit"), "changes": changes}
    warnings = _shared_warning(ctx, fresh)
    source = ctx.store.get_source(int(timeline["plan"]["source_id"]))
    from app.formats import frame_size, settings_render_format

    share = window_share(source, *frame_size(settings_render_format(_current_render(ctx))))
    if fit and share and fit["w"] <= share + 0.02:
        warnings.append(f"w={fit['w']} не шире обычного окна ({share}) — смысла нет, бери больше")
    mode = (_current_render(ctx).get("layout") or {}).get("mode") or "single"
    if fit and mode in ("split", "pip_cam", "meme"):
        warnings.append(f"на этом куске раскладка {mode} не действует (вебки/окошка нет) — только он шире окна")
    if fit:
        bg = fit["bg"]
        bg_name = bg.get("kind") if isinstance(bg, dict) else bg
        out["note"] = ("кусок рендерится шире окна: центр — тот же focus куска; фон сверху и снизу — "
                       f"{bg_name}. Проверь view_frame на куске: субтитры и титры лягут на фон — это хорошо")
    if warnings:
        out["warnings"] = warnings
    return out


def h_add_insert(ctx: ToolContext, args: dict) -> dict:
    from app.montage_assets import MAX_INSERTS, MODES, normalize_inserts

    try:
        asset = ctx.store.get_montage_asset(int(args.get("asset_id")))
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolError("нет такого файла в библиотеке (см. list_assets)") from exc
    foreign = _foreign_asset_problem(ctx, asset["id"])
    if foreign:
        raise ToolError(foreign)
    mode = args.get("mode") or ("sound" if asset["kind"] == "audio" else "pip")
    if mode not in MODES:
        raise ToolError(f"mode: одно из {', '.join(MODES)}")
    if mode == "full" and (_num(args.get("duration"), 1.5) or 1.5) > FULL_INSERT_MAX_SEC + 1e-6:
        raise ToolError(f"вставка на весь кадр (mode full) — не дольше {FULL_INSERT_MAX_SEC:g} с: голос идёт под ней, "
                        "а зритель ждёт героя; дольше — mode pip (окошко)")
    render = _current_render(ctx)
    inserts = list(render.get("inserts") or [])
    if len(inserts) >= MAX_INSERTS:
        raise ToolError(f"вставок уже {MAX_INSERTS} — больше нельзя; для картинок используй слой image")
    on_word = None
    if args.get("at_word") is not None:
        on_word = word_at(ctx, args["at_word"])
        args = {**args, "at": round(on_word["t"] + float(args.get("word_offset") or 0), 3)}
    if args.get("at") is None:
        raise ToolError("нужно at (секунды клипа) или at_word")
    people = people_gate(ctx, asset)  # реальный человек при запрете — отказ до записи
    entry = {k: args[k] for k in ("at", "duration", "x", "y", "scale", "volume", "duck", "reason", "fill")
             if args.get(k) is not None}
    entry.update(asset_id=asset["id"], mode=mode)
    note = _fit_pip_insert(render, entry, asset, _clock(ctx)) if mode == "pip" else None
    fresh = _save_render(ctx, {**render, "inserts": normalize_inserts([*inserts, entry])})
    out: dict = {"ok": True, "inserts": fresh["inserts"]}
    if people.get("people_check"):
        out["people_check"] = people["people_check"]
    if on_word:
        out["on_word"] = (f"на слове «{on_word['word']}» (lines[{on_word['line']}].words[{on_word['word_i']}])"
                          + (f"; {on_word['note']}" if on_word.get("note") else ""))
    warnings = [note] if note else []
    if people.get("people_warning"):
        warnings.append(people["people_warning"])
    twice = [i for i in inserts if int(i.get("asset_id") or -1) == int(asset["id"]) and i.get("mode") != "sound"]
    if twice and mode != "sound":
        # QA 02.11 (клип 892): одна и та же гифка на 6.7 и 13.5 с.
        seconds = ", ".join(f"{float(i['at']):g}" for i in twice)
        warnings.append(f"этот файл уже вставлен на {seconds} с — повтор "
                        "выглядит как нехватка материала: на второе место — другая картинка/гифка под смысл слов")
    if warnings:
        out["warnings"] = warnings
    return out


def refit_inserts(store: Any, clip_plan_id: int) -> list[str]:
    """Подогнать все окошки-вставки клипа под текущие субтитры и хук (как add_insert).

    Вставки ставит и первый шаг ИИ-монтажа (склейка), когда субтитров ещё нет, —
    после моушн-прохода их надо развести с текстом. Возвращает пояснения."""
    from app.montage_assets import normalize_inserts

    plan = store.get_clip_plan(clip_plan_id, include_segments=False)
    render = dict(plan.get("render_settings") or {})
    notes, out = [], []
    for ins in render.get("inserts") or []:
        entry = dict(ins)
        if entry.get("mode") == "pip":
            try:
                note = _fit_pip_insert(render, entry, store.get_montage_asset(int(entry["asset_id"])),
                                       plan_clock(store, store.get_clip_plan(clip_plan_id)))
            except (KeyError, TypeError, ValueError):
                note = None
            if note:
                notes.append(note)
        out.append(entry)
    if notes:
        store.set_clip_plan_render_settings(clip_plan_id, {**render, "inserts": normalize_inserts(out)})
    return notes


CAPTION_HALF = 0.075  # половина высоты двух строк субтитров в долях кадра
HOOK_BOTTOM = 0.2


def _fit_pip_insert(render: dict, entry: dict, asset: dict, clock: Any = None) -> str | None:
    """Окошко мема не должно лечь на субтитры и хук: ставим его над субтитрами
    и, если не влезает, уменьшаем. Вставки ложатся поверх субтитров при рендере,
    а агент их в view_frame не видит — поэтому подгоняем здесь.

    Вставка ложится на кадр готового ролика (формат клипа, ``render.format``): ``scale`` — доля
    его ширины; кегль титров — px опорного кадра слоёв (``layers_format``). У 9:16 оба 1080×1920."""
    from app.formats import frame_size, ref_size, settings_layers_format, settings_render_format
    from app.montage_assets import MIN_INSERT_SCALE, PIP_WIDTH

    from app.cuts import PINNED_START_SEC, CutMap, normalize_cuts

    frame_w, frame_h = frame_size(settings_render_format(render))
    ref_h = float(ref_size(settings_layers_format(render))[1])
    cap_half = CAPTION_HALF if ref_h == 1920.0 else CAPTION_HALF * 1920 / ref_h

    # Сравниваем во времени ГОТОВОГО клипа: вырезанное начало сдвигает мем к хуку.
    # clock — шкалы клипа (app/timebase.py) с наплывами; без него — только вырезы.
    mapper = CutMap(normalize_cuts(render.get("cuts")))
    if clock is not None and clock.has_cuts:
        to_final, from_final = clock.clip_to_final, clock.final_to_clip
    elif mapper:
        to_final, from_final = mapper.clamp, mapper.source_time
    else:
        to_final = from_final = (lambda t: t)
    at = to_final(float(entry.get("at") or 0.0))
    end = at + float(entry.get("duration") or 1.5)
    layers = [l for l in render.get("layers") or [] if not l.get("hidden")]

    def window(l: dict) -> tuple[float, float]:
        a, b = float(l.get("start") or 0), float(l.get("end") or 0)
        if a <= PINNED_START_SEC:  # хук остаётся в начале и после вырезов
            return 0.0, b - a
        return to_final(a), to_final(b)

    def active(l: dict) -> bool:
        a, b = window(l)
        return a < end and b > at

    caps = [l for l in layers if l.get("type") == "captions" and active(l)]
    if not caps:
        return None
    cap_top = float(caps[0].get("y", 0.68)) - cap_half
    # Крупные титры (панчлайны) выше субтитров — тоже потолок для окошка мема.
    for l in layers:
        if l.get("type") != "text" or l.get("id") == "auto_hook" or not active(l):
            continue
        y = float(l.get("y", 0.5))
        if y < cap_top:
            size = float((l.get("props") or {}).get("size") or 96)
            cap_top = min(cap_top, y - size * 0.65 / ref_h)
    moved = ""
    hook = next((l for l in layers if l.get("id") == "auto_hook" and active(l)), None)
    if hook and cap_top - 0.015 - HOOK_BOTTOM < 0.12:
        # Между хуком и субтитрами мему не уместиться — пусть выходит сразу после хука.
        at = round(window(hook)[1], 2)
        entry["at"] = round(from_final(at), 2)
        hook = None
        moved = f"сдвинул на {at} с (после хука), "
    top = HOOK_BOTTOM if hook else 0.07
    room = cap_top - 0.015 - top
    if room < 0.08:
        return None  # субтитры у самого верха — не трогаем, пусть решает агент
    aw, ah = float(asset.get("width") or 0), float(asset.get("height") or 0)
    ratio = ah / aw if aw > 0 and ah > 0 else 1.0  # высота / ширина
    scale = float(entry.get("scale") or PIP_WIDTH)
    height = scale * frame_w * ratio / frame_h
    y = float(entry.get("y") if entry.get("y") is not None else 0.5)
    if y - height / 2 >= top and y + height / 2 <= cap_top - 0.015:
        return f"вставку {moved.rstrip(', ')}" if moved else None
    if height > room:
        scale = max(MIN_INSERT_SCALE, int(room * frame_h / (frame_w * ratio) * 1000) / 1000)
        height = scale * frame_w * ratio / frame_h
    entry["scale"] = scale
    entry["y"] = round(cap_top - 0.015 - height / 2, 3)
    entry["x"] = 0.5
    return (f"вставку {moved}подвинул/уменьшил, чтобы не закрыть субтитры и хук: y={entry['y']}, "
            f"scale={entry['scale']}")


def h_remove_insert(ctx: ToolContext, args: dict) -> dict:
    render = _current_render(ctx)
    inserts = list(render.get("inserts") or [])
    try:
        index = int(args.get("index"))
    except (TypeError, ValueError) as exc:
        raise ToolError("нужен index вставки") from exc
    if not 0 <= index < len(inserts):
        raise ToolError(f"вставки {index} нет (0..{len(inserts) - 1})")
    inserts.pop(index)
    fresh = _save_render(ctx, {**render, "inserts": inserts})
    return {"ok": True, "inserts": fresh["inserts"]}


# Страница списков для агента: по умолчанию столько строк, сколько не раздувает контекст;
# всего — отдельным числом (total), чтобы на «сколько у меня…» не отвечать длиной страницы.
LIST_PAGE_DEFAULT = 100
LIST_PAGE_MAX = 500


def _page_args(args: dict, default: int = LIST_PAGE_DEFAULT) -> tuple[int, int]:
    def num(key: str, fallback: int) -> int:
        try:
            return int(args.get(key) if args.get(key) not in (None, "") else fallback)
        except (TypeError, ValueError) as exc:
            raise ToolError(f"{key} — целое число") from exc

    return max(0, num("offset", 0)), max(1, min(num("limit", default), LIST_PAGE_MAX))


def _page_info(total: int, offset: int, limit: int, returned: int) -> dict:
    """total — сколько всего (с учётом фильтров), returned — сколько в этом ответе."""
    end = offset + returned
    info = {"total": total, "offset": offset, "limit": limit, "returned": returned, "truncated": end < total}
    if end < total:
        info["next_offset"] = end
        info["note"] = (f"показаны {offset + 1 if returned else offset}–{end} из {total}; "
                        f"остальные — тот же вызов с offset={end}. Сколько всего — total, не длина списка")
    return info


def h_list_projects(ctx: ToolContext, args: dict) -> dict:
    offset, limit = _page_args(args)
    query = str(args.get("query") or "").strip().casefold()
    status = str(args.get("status") or "").strip()
    counts = ctx.store.count_sources()
    if query or status:
        # Фильтр — по всем источникам, total — число подошедших.
        rows = [s for s in ctx.store.list_sources()
                if (not query or query in str(s.get("original_filename") or "").casefold()
                    or query in str(s.get("original_url") or "").casefold())
                and (not status or s.get("status") == status)]
        total, rows = len(rows), rows[offset:offset + limit]
    else:
        total, rows = counts["total"], ctx.store.list_sources(limit=limit, offset=offset)
    return {
        **_page_info(total, offset, limit, len(rows)),
        "all_sources_total": counts["total"],
        "by_status": counts["by_status"],
        "projects": [
            {"id": s["id"], "name": s.get("original_filename") or "", "status": s.get("status"),
             "duration_sec": s.get("duration_sec"), "clips": s.get("clip_plans_count"),
             "audio_tracks": _audio_brief(s), "channel_project_id": s.get("project_id")}
            for s in rows
        ],
    }


def _audio_brief(source: dict) -> dict:
    """Сколько звуковых дорожек у записи и какие выбраны (по метаданным загрузки, без ffprobe)."""
    from app.source_audio import audio_streams, normalize_tracks

    return {"total": len(audio_streams(source, probe=False)),
            "selected": normalize_tracks(source.get("audio_tracks")) or [0]}


def h_source_transcript(ctx: ToolContext, args: dict) -> dict:
    from app.manual_clips import transcript_job

    try:
        # Whisper фоном: вызов не держит соединение дольше ~45 с (раньше HTTP рвался на 2.5 мин).
        out = transcript_job(ctx.store, _source_id(args))
    except (KeyError, ValueError) as exc:
        raise ToolError(str(exc)) from exc
    if out.get("status") == "running":
        return {"project_id": out["source_id"], **out}
    lo, hi = _num(args.get("from_sec"), 0.0) or 0.0, _num(args.get("to_sec"))
    cues = [c for c in out["cues"] if float(c["end"]) > lo and (hi is None or float(c["start"]) < hi)]
    result = {"source_id": out["source_id"], "project_id": out["source_id"], "status": "done", "time": "source",
              "duration_sec": out["duration_sec"],
              "lines": [[round(float(c["start"]), 2), round(float(c["end"]), 2), c["text"]] for c in cues]}
    if out.get("no_speech"):
        result.update(no_speech=True, note=out["note"])
    return result


def h_set_source_audio(ctx: ToolContext, args: dict) -> dict:
    """Звуковые дорожки исходника: [0] — первая, [1] — вторая, [0, 1] — смешать (игра + микрофон)."""
    from app.source_audio import set_source_audio

    from app.source_audio import _KEEP

    project = _source_or_clip_source(ctx, args)
    if "tracks" not in args and "speech_tracks" not in args:
        raise ToolError("нужны tracks (дорожки для рендера) и/или speech_tracks (дорожки для распознавания речи)")
    tracks = args.get("tracks", _KEEP)
    if tracks is not _KEEP and tracks is not None and not isinstance(tracks, list):
        raise ToolError("tracks — список: номера дорожек с 0 ([0], [1], [0, 1]) или [{track, gain_db}] "
                        "(gain_db — дБ или \"auto\"); [] — по умолчанию (первая)")
    speech = args.get("speech_tracks", _KEEP)
    if speech is not _KEEP and speech is not None and not isinstance(speech, list):
        raise ToolError("speech_tracks — список номеров дорожек для распознавания речи, например [1]; [] — как tracks")
    try:
        return set_source_audio(ctx.store, project, tracks, speech)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


def h_set_source_gain(ctx: ToolContext, args: dict) -> dict:
    """Громкость звука исходника на отрезках клипа (шкала clip), картинка не меняется."""
    from app.audio_mute import GAIN_MAX_DB, GAIN_MIN_DB, normalize_gain

    ranges = args.get("ranges")
    if not isinstance(ranges, list):
        raise ToolError("ranges — список {start, end, gain_db} (секунды клипа до вырезов; [] — снять всё)")
    problems = []
    for i, item in enumerate(ranges):
        if not isinstance(item, dict) or _num(item.get("gain_db")) is None:
            problems.append(f"ranges[{i}]: нужны start, end и gain_db (дБ, {GAIN_MIN_DB:g}..+{GAIN_MAX_DB:g})")
    if problems:
        raise ToolError("громкость не принята", problems)
    duration = _clip_duration(ctx)
    clean = [g for g in normalize_gain(ranges) if g["start"] < duration]
    render = _current_render(ctx)
    fresh = _save_render(ctx, {**render, "gain": clean})
    out: dict = {"ok": True, "gain": fresh["gain"], "time": "clip",
                 "note": ("громкость звука исходника на этих отрезках изменена (рампы 0.05 с по краям); звуки слоёв и "
                          "музыка не меняются, ролик потом выравнивается целиком до −14 LUFS. Проверь listen.")}
    if any(g["gain_db"] > 12 for g in clean):
        out["warnings"] = ["подъём больше 12 дБ поднимает и шум — если голос тонет в игре по всему ролику, лучше "
                           "громкость дорожки микрофона: set_source_audio tracks [{track, gain_db}]"]
    out["warnings"] = out.get("warnings", []) + _track_warnings(ctx, fresh["gain"])
    if not out["warnings"]:
        out.pop("warnings")
    return out


def h_source_loudness(ctx: ToolContext, args: dict) -> dict:
    """Громкость исходника по времени: пики и громкие места — перестрелки и реакции без Gemini."""
    from app.source_loudness import source_loudness

    project = _source_or_clip_source(ctx, args)
    try:
        track = _num(args.get("track"))
        return source_loudness(ctx.store, project, _num(args.get("from_sec")), _num(args.get("to_sec")),
                               _num(args.get("step")), int(_num(args.get("top"), 10) or 10),
                               track=int(track) if track is not None else None)
    except ValueError as exc:
        raise ToolError(f"source_loudness: {exc}") from exc


CHAT_IMPORT_NOTE = ("чат этого проекта не импортирован. Импорт — запрос к Twitch (POST /api/sources/{id}/chat/import): "
                    "сам не импортируй — спроси владельца, он включит импорт, если разрешает")
MAX_CHAT_WINDOW_SEC = 900.0
MAX_CHAT_MESSAGES = 300


def _chat_ready(ctx: ToolContext, project: int) -> dict | None:
    """Статус чата проекта; None — сообщения есть, можно отвечать."""
    from app.twitch_chat import get_chat_status, has_chat

    if has_chat(ctx.store, project):
        return None
    status = get_chat_status(ctx.store, project)
    out = {"source_id": project, "project_id": project, "has_chat": False, "status": status.get("status"), "time": "source"}
    if status.get("status") in ("queued", "running"):
        out["note"] = f"чат импортируется ({round(float(status.get('progress') or 0) * 100)}%) — повтори позже"
    else:
        out["note"] = CHAT_IMPORT_NOTE
    return out


def h_chat_activity(ctx: ToolContext, args: dict) -> dict:
    """Всплески чата Twitch по времени исходника: где зрители взорвались."""
    from app.twitch_chat import chat_activity, hype_windows_from_activity

    project = _source_or_clip_source(ctx, args)
    missing = _chat_ready(ctx, project)
    if missing:
        return missing
    lo = max(0.0, _num(args.get("from_sec"), 0.0) or 0.0)
    hi = _num(args.get("to_sec"))
    span = (hi - lo) if hi is not None else None
    bucket = _num(args.get("bucket"))
    if bucket is None:
        bucket = 10.0 if span is not None and span <= 900 else 60.0
    bucket = min(600.0, max(5.0, float(bucket)))
    activity = chat_activity(ctx.store, project, bucket)
    inside = [b for b in activity.get("buckets") or [] if b["t"] + bucket > lo and (hi is None or b["t"] < hi)]
    counts = sorted(b["count"] for b in inside if b["count"])
    windows = [w for w in hype_windows_from_activity(activity, top=int(_num(args.get("top"), 10) or 10))
               if w["end"] > lo and (hi is None or w["start"] < hi)]
    shown = inside[:240]
    return {
        "source_id": project, "project_id": project, "has_chat": True, "time": "source", "bucket": bucket,
        "from_sec": round(lo, 2), "to_sec": round(hi, 2) if hi is not None else None,
        "messages": sum(b["count"] for b in inside),
        "median_per_bucket": counts[len(counts) // 2] if counts else 0,
        "windows": windows,
        "buckets": [[b["t"], b["count"], b["hype"], b["top"]] for b in shown],
        "note": ("секунды исходника. windows — всплески (hype 0..1, top — самая частая реакция); buckets — "
                 "[t, сообщений, hype, реакция] по bucket с. Зрители пишут с задержкой 5–20 с: событие обычно "
                 "чуть РАНЬШЕ всплеска — смотри get_source_transcript/source_contact_sheet перед ним. "
                 "Что писали — get_chat")
                + (f"; показаны первые {len(shown)} из {len(inside)} окон — сузь from_sec/to_sec" if len(inside) > len(shown) else ""),
    }


def h_get_chat(ctx: ToolContext, args: dict) -> dict:
    """Сообщения чата Twitch между from_sec и to_sec (время исходника)."""
    from app.twitch_chat import list_messages

    project = _source_or_clip_source(ctx, args)
    missing = _chat_ready(ctx, project)
    if missing:
        return missing
    lo, hi = _num(args.get("from_sec")), _num(args.get("to_sec"))
    if lo is None or hi is None or hi <= lo:
        raise ToolError("нужны from_sec < to_sec (секунды исходника); всплески — chat_activity")
    notes = []
    if hi - lo > MAX_CHAT_WINDOW_SEC:
        hi = lo + MAX_CHAT_WINDOW_SEC
        notes.append(f"окно урезано до {MAX_CHAT_WINDOW_SEC / 60:g} мин")
    limit = max(1, min(MAX_CHAT_MESSAGES, int(_num(args.get("limit"), MAX_CHAT_MESSAGES) or MAX_CHAT_MESSAGES)))
    messages = list_messages(ctx.store, project, lo, hi, limit + 1)
    if len(messages) > limit:
        notes.append(f"показаны первые {limit} сообщений — сузь окно")
    return {
        "source_id": project, "project_id": project, "time": "source", "from_sec": round(lo, 2), "to_sec": round(hi, 2),
        "messages": [[round(m["t"], 1), m["user"], m["text"]] for m in messages[:limit]],
        "note": "[секунда исходника, кто, текст]" + ("; " + "; ".join(notes) if notes else ""),
    }


def h_source_contact_sheet(ctx: ToolContext, args: dict) -> ImageResult:
    """Лист кадров исходника с временем (до 24): что на экране в громких местах."""
    from app.motion_render import source_contact_sheet_jpeg

    project = _source_or_clip_source(ctx, args)
    lo, hi = _num(args.get("from_sec")), _num(args.get("to_sec"))
    if lo is None or hi is None:
        raise ToolError("нужны from_sec и to_sec (секунды исходника)")
    x = _num(args.get("x"))
    if x is not None and not 0.0 <= x <= 1.0:
        raise ToolError("x — центр окна 9:16 в долях ширины, 0..1")
    try:
        data, times = source_contact_sheet_jpeg(ctx.store, project, lo, hi, _num(args.get("step")), x)
    except ValueError as exc:
        raise ToolError(f"source_contact_sheet: {exc}") from exc
    return ImageResult(data=data, mime_type="image/jpeg",
                       info={"source_id": project, "project_id": project, "time": "source", "times": times, "x": x,
                             "note": "подписи — время исходника; окна рабочего стола/уведомления на кадрах — "
                                     "такие места в клип не брать"})


def h_source_shots(ctx: ToolContext, args: dict) -> dict:
    """Склейки планов исходника (секунды исходника) — куда ставить смену кадра (cut)."""
    from app.store import _json_text, _parse_transcript

    project = _source_id(args)
    try:
        row = ctx.store.db.query_one("SELECT local_path, scene_cuts_json FROM sources WHERE id = ?", (project,))
    except Exception as exc:  # noqa: BLE001
        raise ToolError(str(exc)) from exc
    if not row:
        raise ToolError("нет такого проекта (см. list_projects)")
    cuts = _parse_transcript(row["scene_cuts_json"])
    if not cuts:
        from app.scene_cuts import detect_scene_cuts

        cuts = detect_scene_cuts(row["local_path"])
        ctx.store.db.execute("UPDATE sources SET scene_cuts_json = ? WHERE id = ?", (_json_text(cuts), project))
    lo, hi = _num(args.get("from_sec"), 0.0) or 0.0, _num(args.get("to_sec"))
    shown = [round(float(t), 3) for t in cuts if float(t) >= lo and (hi is None or float(t) <= hi)]
    return {"source_id": project, "project_id": project, "time": "source", "shot_changes": shown,
            "note": "секунды исходника; в кадрировании куска ставь точку {t: склейка − начало куска, x, cut: true}"}


def h_create_clip(ctx: ToolContext, args: dict) -> dict:
    from app.manual_clips import create_manual_clip

    try:
        version_of = args.get("version_of")
        plan = create_manual_clip(ctx.store, _source_id(args), str(args.get("title") or ""),
                                  args.get("pieces") or [], str(args.get("description") or ""),
                                  version_of=int(version_of) if version_of else None)
    except (KeyError, ValueError) as exc:
        raise ToolError(f"клип не создан: {exc}") from exc
    segs = plan.get("segments") or []
    return {"ok": True, "clip_id": plan["id"], "title": plan["title"],
            "duration": round(sum(float(s["end_sec"]) - float(s["start_sec"]) for s in segs), 2)}


def h_create_canvas_clip(ctx: ToolContext, args: dict) -> dict:
    """Пустой холст (app/canvas.py): клип без исходника — анимированный фон нужной длины."""
    from app.canvas import create_canvas_clip

    try:
        plan = create_canvas_clip(ctx.store, args.get("duration"), args.get("background"),
                                  str(args.get("title") or ""), str(args.get("description") or ""))
    except (KeyError, ValueError) as exc:
        raise ToolError(f"холст не создан: {exc}") from exc
    segs = plan.get("segments") or []
    return {"ok": True, "clip_id": plan["id"], "title": plan["title"], "source_id": plan["source_id"], "project_id": plan["source_id"],
            "duration": round(sum(float(s["end_sec"]) - float(s["start_sec"]) for s in segs), 2),
            "backdrop": (plan.get("render_settings") or {}).get("backdrop"),
            "note": ("холст: видео исходника нет, весь кадр — фон (set_backdrop меняет его). Дальше обычные "
                     "инструменты клипа: add_layers (картинки — image, звуки — audio), set_music, set_camera; "
                     "квадрат, 4:5 или 16:9 из того же монтажа — set_format. Холсты лежат в скрытом видео-источнике source_id — list_clips(source_id)")}


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


def h_list_modules(ctx: ToolContext, _args: dict) -> dict:
    """Модули сервиса (app/modules.py) — только чтение."""
    from app import modules

    view = modules.describe(ctx.store)
    keep = ("id", "name", "group", "description", "enabled", "depends", "blocked_by", "cost", "cost_note", "tools")
    mods = [{k: m[k] for k in keep} for m in view["modules"]]
    off = [m["id"] for m in mods if not m["enabled"]]
    return {"ok": True, "modules": mods, "disabled": off,
            "note": "переключает только владелец: Настройки → Модули" + (
                f"; выключены: {', '.join(off)}" if off else "; включены все")}


def h_list_clips(ctx: ToolContext, args: dict) -> dict:
    from app import agent_settings as S
    from app.piece_speed import seg_length

    project = _source_id(args)
    favorites = bool(args.get("favorites_only"))
    offset, limit = _page_args(args)
    out = []
    for plan in ctx.store.list_clip_plans(source_id=project, include_segments=True):
        if favorites and not plan.get("favorite"):
            continue
        if plan.get("hidden") and not favorites:
            continue
        segs = plan.get("segments") or []
        settings = plan.get("render_settings") or {}
        out.append(
            {
                "clip_id": plan["id"],
                "title": plan.get("title"),
                "favorite": bool(plan.get("favorite")),
                "status": plan.get("status"),
                # Черновик: файла ещё нет. waits_owner: по настройке проекта его монтирует только выбор владельца.
                "draft": plan.get("status") != "rendered",
                "waits_owner": S.clip_list_refusal(ctx.store, int(plan["id"])) is not None,
                "pieces": len(segs),
                "duration": round(sum(seg_length(s) for s in segs), 2),
                "layers": len(settings.get("layers") or []) if isinstance(settings, dict) else 0,
            }
        )
    total = len(out)
    page = out[offset:offset + limit]
    return {"source_id": project, "project_id": project, **_page_info(total, offset, limit, len(page)), "clips": page}


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
            "find_noises", "Ищу посторонние звуки",
            "Громкие места звука клипа без речи — вшитые эффекты старого монтажа, музыка, шум, в игре — "
            "выстрелы и взрывы (распознавание их не видит). Отрезки уже слиты, шкала clip (до вырезов, как "
            "слои и mute_audio — годятся туда как есть): в паузе — вырежи apply_cuts или mute_audio, тише — "
            "set_source_gain. threshold_db — порог громкости, по умолчанию медиана звука клипа без речи + 6 дБ. "
            "Речь вычитается по субтитрам, без них — по дорожке речи (speech_tracks), если она своя. "
            "/ Loud non-speech spans in the clip audio.",
            _obj({"threshold_db": {"type": "number"}}), h_find_noises,
            label=lambda a: "Ищу посторонние звуки",
        ),
        Tool(
            "listen", "Слушаю отрезок",
            "Распознать, что РЕАЛЬНО звучит в готовом клипе между from_sec и to_sec (с вырезами и наплывами "
            "переходов, до 30 с): время — шкала final (готовый ролик, как contact_sheet); before_cuts=true — "
            "шкала clip (до вырезов, как слои и субтитры get_clip). Распознаёт дорожку речи (speech_tracks). "
            "Слова с временем и тишина после последнего слова, громкость отрезка (level, RMS дБ). Без "
            "параметров — последние 6 с: проверь, договорена ли развязка и есть ли хвост реакции. Речи нет "
            "(игра, музыка) — words [] и пометка. Заглушённое (mute, в т.ч. мат censor_profanity) молчит, как в "
            "рендере. Для концовок, вырезов и «не торчит ли слово» — НЕ для "
            "исправления распознавания: это тот же Whisper и он повторит те же ошибки (неверное слово правь "
            "по смыслу/на слух владельца через set_subtitle_words). / Transcribe the finished clip audio in a range.",
            _obj({"from_sec": {"type": "number"}, "to_sec": {"type": "number"},
                  "before_cuts": {"type": "boolean", "description": "from/to и слова — шкала clip (до вырезов)"}}),
            h_listen,
            label=lambda a: "Слушаю финал клипа" if a.get("from_sec") is None else
            f"Слушаю {(_num(a.get('from_sec'), 0.0) or 0.0):.1f}–{(_num(a.get('to_sec'), 0.0) or 0.0):.1f} с",
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
            "source_contact_sheet", "Смотрю кадры исходника",
            "Сетка кадров ИСХОДНИКА между from_sec и to_sec (секунды исходника, подписи — время исходника): "
            "step — шаг в секундах (от 0.04 — покадрово на коротком окне; до 24 кадров), без него 12 "
            "равномерно; x 0..1 — показать окно 9:16 с "
            "центром x, как оно встанет в вертикальный кадр. Выбирать моменты без Gemini: громкие места "
            "source_loudness → что там на экране. Окна рабочего стола, Alt-Tab, уведомления — такие места не "
            "брать. source_id — видео-источник (у встроенного монтажёра — источник клипа). / Contact sheet of the source.",
            _obj({**SOURCE_ARGS, "from_sec": {"type": "number"}, "to_sec": {"type": "number"},
                  "step": {"type": "number"}, "x": {"type": "number"}}, ["from_sec", "to_sec"]),
            h_source_contact_sheet, scope="global",
            label=lambda a: f"Смотрю кадры видео {(_num(a.get('from_sec'), 0.0) or 0.0):.0f}–"
                            f"{(_num(a.get('to_sec'), 0.0) or 0.0):.0f} с",
        ),
        Tool(
            "source_loudness", "Слушаю громкость исходника",
            "Профиль громкости ИСХОДНИКА: RMS дБ по шагу step (≥ 0.5 с, по умолчанию 1) в окне from_sec..to_sec "
            "(не больше 10 мин за вызов, по умолчанию 10 мин от from_sec), пики (top, по умолчанию 10) и "
            "громкие отрезки, сводка: медиана и порог «громко» (медиана + 6 дБ). Для игр и стримов без речи — "
            "так ищут перестрелки, взрывы, реакции; потом source_contact_sheet на пиках. Звук — смесь рендера "
            "(set_source_audio); track — одна дорожка записи (сравнить громкость игры и микрофона перед "
            "gain_db). source_id — видео-источник (у встроенного монтажёра — источник клипа). / Loudness profile of the source over time.",
            _obj({**SOURCE_ARGS, "from_sec": {"type": "number"}, "to_sec": {"type": "number"},
                  "step": {"type": "number"}, "top": {"type": "integer"},
                  "track": {"type": "integer", "description": "одна звуковая дорожка вместо смеси"}}),
            h_source_loudness, scope="global",
            label=lambda a: f"Слушаю громкость видео с {(_num(a.get('from_sec'), 0.0) or 0.0):.0f} с",
        ),
        Tool(
            "chat_activity", "Активность чата",
            "Всплески чата Twitch (время исходника) между from_sec и to_sec: windows — самые горячие окна "
            "(hype 0..1, top — частая реакция), buckets — [t, сообщений, hype, реакция] по bucket секунд "
            "(по умолчанию 60, на окне до 15 мин — 10). Зрители пишут с задержкой 5–20 с — событие чуть раньше "
            "всплеска. Чата нет — has_chat false: импорт — запрос к Twitch, только с разрешения владельца, сам не "
            "импортируй. source_id — видео-источник. / Twitch chat activity bursts.",
            _obj({**SOURCE_ARGS, "from_sec": {"type": "number"}, "to_sec": {"type": "number"},
                  "bucket": {"type": "number"}, "top": {"type": "integer"}}),
            h_chat_activity, scope="global",
            label=lambda a: "Смотрю активность чата",
        ),
        Tool(
            "get_chat", "Читаю чат",
            "Сообщения чата Twitch между from_sec и to_sec (время исходника, окно до 15 мин, до 300 сообщений): "
            "[секунда, кто, текст] — что зрители писали на моменте (реакции, мемы, цитаты для хука). Чата нет — "
            "has_chat false (импорт — только с разрешения владельца). source_id — видео-источник. / Twitch chat messages in a source window.",
            _obj({**SOURCE_ARGS, "from_sec": {"type": "number"}, "to_sec": {"type": "number"},
                  "limit": {"type": "integer"}}, ["from_sec", "to_sec"]),
            h_get_chat, scope="global",
            label=lambda a: f"Читаю чат {(_num(a.get('from_sec'), 0.0) or 0.0):.0f}–{(_num(a.get('to_sec'), 0.0) or 0.0):.0f} с",
        ),
        Tool(
            "set_source_audio", "Выбираю звуковую дорожку",
            "Звуковые дорожки исходника (запись NVIDIA/OBS: часто 0 — игра, 1 — микрофон). tracks — смесь для "
            "рендера: [0], [1], [0, 1] или со своей громкостью [{track, gain_db}] (gain_db — дБ −30..+30; "
            "\"auto\" — замерить и поставить голос на ~9 дБ над игрой в моменты речи; не хватает запаса пика голоса — "
            "остальные дорожки получают отрицательный gain_db; ответ: auto_gain — громкость всех дорожек, auto — "
            "over_db, peak_limited): тихий микрофон в смеси поровну тонет. "
            "speech_tracks — дорожки для РАСПОЗНАВАНИЯ речи (расшифровка исходника, субтитры, listen, сверка "
            "субтитров в QC), например [1] — один микрофон: на смеси с игрой Whisper выдумывает; [] — как tracks. "
            "Сменился звук для распознавания — расшифровка исходника сбрасывается. Сколько дорожек — "
            "get_clip.source.audio / list_projects; громкость каждой — source_loudness(track). source_id — "
            "видео-источник (у встроенного монтажёра — источник клипа). / Choose, mix and level the source audio tracks.",
            _obj({**SOURCE_ARGS,
                  "tracks": {"type": "array", "items": {
                      "anyOf": [{"type": "integer"},
                                _obj({"track": {"type": "integer"},
                                      "gain_db": {"type": ["number", "string"],
                                                  "description": "дБ −30..+30 или \"auto\""}}, ["track"])]}},
                  "speech_tracks": {"type": "array", "items": {"type": "integer"},
                                    "description": "дорожки для распознавания речи; [] — как tracks"}}),
            h_set_source_audio, mutating=True, scope="global",
            label=lambda a: f"Звук видео: дорожки {a.get('tracks')}"
            + (f", речь {a.get('speech_tracks')}" if a.get("speech_tracks") is not None else ""),
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
            "list_music", "Смотрю фоновую музыку",
            "Треки фоновой музыки (id, название, длина; energy_start_sec — где кончается тихое вступление, "
            "drop_sec — самый сильный подъём, bpm — темп). / Background music tracks with energy marks.",
            _obj({}), h_list_music, scope="global",
        ),
        Tool(
            "add_sound_from_url", "Скачиваю звук",
            "Звуковой эффект по ссылке ВЛАДЕЛЬЦА: страница звука freesound.org или любое видео yt-dlp (только "
            "звук; start — с какой секунды взять). Режет тишину в начале, обрезает до 6 с → asset_id для слоя "
            "audio. Сам звуки в интернете не ищи — только ссылка владельца; иначе пак freesound (search_pack). / "
            "Sound effect from the owner's link.",
            _obj({"url": {"type": "string"}, "label": {"type": "string"}, "start": {"type": "number"},
                  **ASSET_OWNER_PARAMS}, ["url"]),
            h_add_sound_from_url, scope="global",
            label=lambda a: f"Скачиваю звук {(a.get('label') or '')[:40]}".strip(),
        ),
        Tool(
            "get_performance", "Смотрю, что заходит",
            "Статистика опубликованных клипов: топ и антитоп по просмотрам (limit, по умолчанию 5) с признаками "
            "монтажа — длина, хук-титр, субтитры, музыка, слои, звуки, вырезы, раскладка, хромакей — и сравнение "
            "«с признаком / без». Зови перед монтажом нового клипа, чтобы повторять то, что заходит. Пусто "
            "(hint) — публикаций с числами ещё нет: это нормально, монтируй по правилам. / Top and "
            "bottom published clips by views with their montage features.",
            _obj({"limit": {"type": "integer"}}), h_get_performance, scope="global",
        ),
        Tool(
            "mute_audio", "Глушу звук исходника",
            "Тишина вместо звука исходника на отрезках ranges [{start, end}] (секунды клипа до вырезов), "
            "картинка остаётся — для вшитых эффектов старого монтажа, шума, мата (find_noises покажет где). "
            "Заменяет прежний список ручных; [] — снять их. Отрезки мата (by: censor, censor_profanity) остаются, "
            "если в ranges нет помеченных. track — только эта дорожка записи (0 — игра, 1 — микрофон у NVIDIA/OBS): "
            "остальные звучат. / Mute source audio in ranges (optionally one track).",
            _obj({"ranges": {"type": "array", "items": _obj({"start": {"type": "number"}, "end": {"type": "number"},
                                                            "track": {"type": "integer"}},
                                                           ["start", "end"])}}, ["ranges"]),
            h_mute_audio, mutating=True, label=lambda a: f"Глушу звук ({len(a.get('ranges') or [])} отр.)",
        ),
        Tool(
            "censor_profanity", "Глушу мат",
            "Мат одной командой по словам субтитров (нужны generate_subtitles): каждое матерное слово глушится "
            "СЕРЕДИНОЙ (mute с меткой by: censor, уровень 1 — 30–85% слова, 2 — шире, 3 — слышна только первая "
            "согласная) — человек понимает, автосубтитры TikTok/YouTube мат не распознают; в субтитрах — первая "
            "буква и звёздочки («б****»). Ручные mute не трогает, свои прежние отрезки заменяет; повторный "
            "запуск идемпотентен (звёздочки находит сам). По умолчанию мат + «сука»; soft=true — ещё блин, "
            "хрен, жопа, говно, срать — ТОЛЬКО если владелец сам просил глушить мягкую брань (иначе soft не "
            "применяется: это не мат). Слова владельца в кавычках (панчлайн, ключевое слово) и слово под звуком-"
            "акцентом не глушатся (kept в ответе), кроме настоящего мата; заглушить такое всё же — force_words [i], "
            "только по просьбе владельца. verify=true (по умолчанию) — рендер → Whisper по звуку готового файла → там, где "
            "мат ещё узнаётся (±0.4 с), уровень +1 и новый рендер, не больше max_rounds (3) кругов; это минуты: "
            "ответ status running — повтори тот же вызов через retry_after_sec. Итог: words [{i, word, start, "
            "end, level, heard, note}], render_id последнего рендера, unmuted_heard — мат, который Whisper "
            "услышал мимо субтитров. heard на уровне 3 — «узнаётся по контексту»: тишиной не лечится. "
            "verify=false — быстро, без рендера. levels — число всем или {i: уровень}; skip [i] — не трогать "
            "слово; clear=true — снять свои отрезки. track: \"voice\" — глушить только дорожку голоса (запись "
            "игра+микрофон со своей speech_tracks; неизвестна — весь звук), число — эту дорожку; слово, что "
            "узнаётся и так, verify заглушит целиком. style — чем заполнить середину: silence (тишина), beep "
            "(тихий писк), noise (розовый шум), reverse (задом наперёд), lowpass («под водой»), stretch (растяжка, "
            "тон ниже), duck (−35 дБ, не в ноль); не задан — прежний стиль клипа (хранится у отрезков), у нового "
            "— reverse (выбор владельца на слух). Слово, что узнаётся и на уровне 3, verify сам переключает на "
            "silence, затем noise (только его; style_switched в ответе). Вызывай ОДИН раз: цензура уже стоит на весь "
            "мат — повтор с любыми levels/style/groups отвечает status already («уже применено», без рендера) и их "
            "не применяет; переделать — redo: true (только если владелец просил), verify_again: true — проверить "
            "рендером заново. Слова под цензурой в субтитрах всегда со звёздочками и не подсвечиваются "
            "(highlightWords/эмфаза на них не действуют). "
            "/ Mute profanity mid-word, star it in subtitles, verify by Whisper on the render.",
            _obj({"verify": {"type": "boolean"}, "verify_again": {"type": "boolean"}, "max_rounds": {"type": "integer"},
                  "levels": {"type": ["integer", "object"]}, "soft": {"type": "boolean"},
                  "stars": {"type": "boolean"}, "skip": {"type": "array", "items": {"type": "integer"}},
                  "clear": {"type": "boolean"},
                  "redo": {"type": "boolean", "description": "переделать уже стоящую цензуру (иначе повтор — already)"},
                  "force_words": {"type": "array", "items": {"type": "integer"},
                                  "description": "номера слов владельца/под акцентом, которые всё же глушить"},
                  "track": {"type": ["integer", "string"], "description": "\"voice\" или номер дорожки"},
                  "style": {"type": "string", "enum": ["silence", "beep", "noise", "reverse", "lowpass", "stretch",
                                                       "duck"]}}),
            h_censor_profanity, mutating=True,
            label=lambda a: "Глушу мат" + ("" if a.get("verify") is False else " и проверяю рендером"),
        ),
        Tool(
            "set_source_gain", "Громкость исходника на отрезках",
            "Громкость звука исходника на отрезках ranges [{start, end, gain_db}] (шкала clip — секунды клипа до "
            "вырезов, как mute_audio и find_noises): поднять тихий кусок (+6..+12 дБ) или приглушить громкий "
            "(−6..−20 дБ), картинка та же; звуки слоёв и музыка не меняются, ролик потом выравнивается целиком. "
            "Заменяет прежний список; [] — снять. Тихий микрофон по всему ролику — громкость дорожки "
            "(set_source_audio tracks [{track, gain_db}]), а не отрезки. track — громкость только этой дорожки "
            "(приглушить игру под фразой, микрофон не трогая). / Source audio gain in ranges (optionally one track).",
            _obj({"ranges": {"type": "array", "items": _obj({"start": {"type": "number"}, "end": {"type": "number"},
                                                            "gain_db": {"type": "number"},
                                                            "track": {"type": "integer"}},
                                                           ["start", "end", "gain_db"])}}, ["ranges"]),
            h_set_source_gain, mutating=True,
            label=lambda a: f"Громкость видео ({len(a.get('ranges') or [])} отр.)",
        ),
        Tool(
            "add_music_from_url", "Скачиваю музыку",
            "Трек фоновой музыки по ссылке (YouTube и всё, что умеет yt-dlp; до 15 мин) → track_id для "
            "set_music. Только когда владелец дал ссылку. / Background music from a link.",
            _obj({"url": {"type": "string"}, "label": {"type": "string"}}, ["url"]),
            h_add_music_from_url, scope="global", label=lambda a: "Скачиваю музыку по ссылке",
        ),
        Tool(
            "set_music", "Подкладываю музыку",
            "Фоновая музыка клипа: on, track_id (list_music), start_sec — с какой секунды трека играть на 0 с "
            "клипа (пропустить тихое вступление и начать сразу с барабанов/дропа — energy_start_sec трека из "
            "list_music; 0 — с начала, новый трек без start_sec — с начала). Громкость сама: заметно тише "
            "голоса и приглушается под речь; эффекты при музыке — реже. / Background music for the clip.",
            _obj({"on": {"type": "boolean"}, "track_id": {"type": "integer"},
                  "start_sec": {"type": "number", "description": "секунда трека на 0 с клипа"}}),
            h_set_music, mutating=True,
            label=lambda a: "Музыка " + ("выкл" if a.get("on") is False else f"трек {a.get('track_id') or ''}".strip()),
        ),
        Tool(
            "set_end_hold", "Ставлю стоп-кадр в конце",
            "Стоп-кадр в конце готового ролика на этот клип: sec 0..1.5 (последний кадр стоит, пока затухает звук; "
            "музыка доигрывает на нём). Мему — 0: ролик обрывается на последнем кадре, без «замирания». "
            "reset:true (или sec не задан) — как у сервиса (RENDER_END_HOLD_SEC, обычно 0.5). "
            "/ End freeze-frame per clip (0 for memes).",
            _obj({"sec": {"type": "number", "minimum": 0, "maximum": 1.5}, "reset": {"type": "boolean"}}),
            h_set_end_hold, mutating=True,
            label=lambda a: "Стоп-кадр в конце: " + ("как у сервиса" if a.get("sec") is None or a.get("reset")
                                                     else f"{a.get('sec')} с"),
        ),
        Tool(
            "freeze_frame", "Ставлю стоп-кадр",
            "Стоп-кадр посреди ролика: кадр в момент at (секунды ГОТОВОГО ролика — шкала final, как "
            "contact_sheet и listen; без других стоп-кадров) замирает на duration 0.2–2.0 с (по умолчанию 1). "
            "audio: \"silence\" (по умолчанию) — стоп-кадр вставляется: звук исходника молчит, музыка играет, "
            "ролик длиннее на duration, субтитры, слои, звуки и вставки после at сдвигаются сами; "
            "\"continue\" — звук идёт дальше (стример договаривает), картинка стоит и продолжается с at+duration, "
            "длина та же. layers: \"freeze\" (по умолчанию) — слои, камера и анимированные субтитры стоят с "
            "кадром; \"continue\" — анимируются дальше на замершем кадре. Тот же at — заменить; remove:true + at — "
            "снять один; reset:true — снять все. Проверка — view_frame(t, final: true): t в готовом ролике СО "
            "стоп-кадрами. / Freeze-frame mid-clip.",
            _obj({"at": {"type": "number", "minimum": 0},
                  "duration": {"type": "number", "minimum": 0.2, "maximum": 2.0},
                  "audio": {"type": "string", "enum": ["silence", "continue"]},
                  "layers": {"type": "string", "enum": ["freeze", "continue"]},
                  "remove": {"type": "boolean"}, "reset": {"type": "boolean"}}),
            h_freeze_frame, mutating=True,
            label=lambda a: ("Снимаю стоп-кадры" if a.get("reset") or a.get("remove") else
                             f"Стоп-кадр на {(_num(a.get('at'), 0.0) or 0.0):.1f} с"),
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
            "suggest_cuts", "Ищу паузы и паразиты",
            "Найти паузы и слова-паразиты по словам клипа (ничего не меняет). mode: pauses|fillers|both. "
            "Пока владелец не ответил на вопросы по видео, откажет: сначала ответы (list_stream_answers), потом резка. "
            "Длинные паузы и вырезы, после которых остался бы обрывок, помечены risky и в cuts не входят — в реакциях "
            "это часто сам момент, не вырезай их вслепую. Шкала clip: "
            "cuts можно передать в apply_cuts как есть. / Suggest jump cuts.",
            _obj({"mode": {"type": "string"}, "min_pause": {"type": "number"}, "keep_pad": {"type": "number"}}),
            h_suggest_cuts, label=lambda a: "Ищу паузы и слова-паразиты",
        ),
        Tool(
            "apply_cuts", "Вырезаю паузы",
            "Пока владелец не ответил на вопросы по видео, откажет: сначала ответы (list_stream_answers), потом резка. "
            "Сохранить вырезы [{start,end}] — шкала clip (секунды клипа ДО вырезов, с наплывами, как слои, "
            "субтитры и cuts в get_clip); [] — вернуть всё. piece — номер куска, если вырез в зоне наплыва "
            "перехода (там время принадлежит двум кускам; get_clip и suggest_cuts его дают). В warnings — обрывки: "
            "кусок между вырезами короче min_island_sec или без трёх слов; merge_short_islands: true вырежет их "
            "вместе с соседними вырезами (merged_islands в ответе). / Save jump cuts.",
            _obj({"cuts": {"type": "array", "items": {"type": "object", "properties": {
                "start": {"type": "number"}, "end": {"type": "number"},
                "piece": {"type": "integer", "description": "чей вырез в зоне наплыва"}}}},
                  "min_island_sec": {"type": "number", "description": "короче — обрывок, по умолчанию 2.5; 0 — не проверять"},
                  "merge_short_islands": {"type": "boolean", "description": "вырезать обрывки вместе с соседними вырезами"}},
                 ["cuts"]),
            h_apply_cuts, mutating=True, label=lambda a: f"Вырезаю {len(a.get('cuts') or [])} кусочков",
        ),
        Tool(
            "auto_edit", "Автомонтаж",
            "Без ИИ за секунду: хук-титр, анимированные субтитры с эмодзи, наезды и удары по речи. "
            "Хорошая основа — дальше правь слои. style — стиль субтитров. / Rule-based auto edit.",
            _obj({"style": {"type": "string"}, "keep_layers": {"type": "boolean"}}),
            h_auto_edit, mutating=True, label=lambda a: "Делаю автомонтаж",
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
        Tool(
            "make_compilation", "Собираю компиляцию",
            "Новый клип «ТОП-N» из клипов одного проекта: номера обратным отсчётом, названия, субтитры, whoosh. "
            "clip_ids — от худшего к лучшему не важно, countdown ставит лучший (первый в списке) последним. / Compilation.",
            _obj({"clip_ids": {"type": "array", "items": {"type": "integer"}}, "title": {"type": "string"},
                  "countdown": {"type": "boolean"}}, ["clip_ids"]),
            h_make_compilation, mutating=True, scope="global", label=lambda a: "Собираю компиляцию",
        ),
        Tool(
            "set_transition", "Ставлю переход",
            "Переход между кусками: type (cut, whip, zoompunch, glitch, flash, fade, fadeblack, …), duration, "
            "sfx (none/whoosh/click/pop/boom), audio (smooth/hard), smart. Наплыв занимает последние duration "
            "секунд куска и первые следующего: если там речь — warnings скажут, какие слова уйдут под переход "
            "(fadeblack — речь на чёрном) и как поправить; fit_speech=true — сам укоротит переход, чтобы он не "
            "заходил на речь. Наплыв сдвигает шкалу clip — слои, камера, вставки переезжают сами. "
            "motion_blur 0..1 — смаз сдвигового перехода (whip/slide/smooth) по горизонтали на окне перехода; "
            "нет — как у клипа (set_motion_blur). / Transition between pieces.",
            _obj({"type": {"type": "string"}, "duration": {"type": "number"}, "sfx": {"type": "string"},
                  "sfx_volume": {"type": "number"}, "audio": {"type": "string"}, "smart": {"type": "boolean"},
                  "motion_blur": {"type": "number", "description": "смаз whip/slide 0..1"},
                  "fit_speech": {"type": "boolean", "description": "укоротить переход, чтобы не задевал слова"}}),
            h_set_transition, mutating=True, label=lambda a: f"Переход {a.get('type') or ''} {a.get('sfx') or ''}".strip(),
        ),
        Tool(
            "trim_piece", "Подрезаю кусок",
            "Подрезать кусок index: новые start_sec/end_sec во времени ИСХОДНИКА (5..180 с; те же имена, что "
            "get_clip.pieces[].start_sec/end_sec). Слои, камера, вставки, вырезы, тишина и субтитры едут за "
            "куском сами. / Trim a piece (source seconds).",
            _obj({"index": {"type": "integer"}, "start_sec": {"type": "number"}, "end_sec": {"type": "number"}},
                 ["index"]),
            h_trim_piece, mutating=True, label=lambda a: f"Подрезаю кусок {a.get('index')}",
        ),
        Tool(
            "set_segment_speed", "Скорость куска",
            "Скорость и реверс куска основы piece (номер с 0): speed 0.25..4 (0.5 — слоу-мо вдвое, 0.75, 1.5, 2 — "
            "ускорение; 1 — как есть), reverse true — задом наперёд («перемотка», кусок ≤ 10 с исходника). Кусок на "
            "таймлайне становится длиной (конец − начало)/speed: слои, камера, вставки и вырезы после него сдвигаются "
            "сами, субтитры куска растягиваются в 1/speed (у реверса скрыты). audio: tempo (по умолчанию у скорости — "
            "речь без смены тона), pitch (как плёнка: на слоу-мо ниже), mute (по умолчанию у реверса), reverse (звук "
            "задом, только с reverse). Повтор килла в слоу-мо — отдельный кусок того же момента (create_clip/"
            "spec) со speed 0.5. speed 1 и reverse false — снять. / Piece playback speed and reverse.",
            _obj({"piece": {"type": "integer"}, "speed": {"type": "number", "description": "0.25..4, 1 — как есть"},
                  "reverse": {"type": "boolean"},
                  "audio": {"type": "string", "enum": ["tempo", "pitch", "mute", "reverse"]}}, ["piece"]),
            h_set_segment_speed, mutating=True,
            label=lambda a: (f"Кусок {a.get('piece')}: " + ("реверс " if a.get("reverse") else "")
                             + (f"×{a.get('speed')}" if a.get("speed") not in (None, 1, 1.0) else "")).strip(),
        ),
        Tool(
            "set_piece_color", "Цвет куска",
            "Цветокоррекция куска основы piece (номер с 0) — компиляции и сериалы из разных сцен не прыгают по "
            "цвету: exposure −2..2 (ступени EV: +1 — вдвое светлее), contrast 0.5..1.5, saturation 0..2 (0 — ч/б), "
            "temperature −1..1 (+ теплее, − холоднее), tint −1..1 (+ пурпурнее, − зеленее). Не переданное поле "
            "остаётся прежним; reset true — снять поправку. Поправка едет с куском (по его id), рендерится до лука "
            "пресета. Подогнать куски друг к другу по замеру — match_piece_colors. / Per-piece colour correction.",
            _obj({"piece": {"type": "integer"}, "exposure": {"type": "number"}, "contrast": {"type": "number"},
                  "saturation": {"type": "number"}, "temperature": {"type": "number"}, "tint": {"type": "number"},
                  "reset": {"type": "boolean"}}, ["piece"]),
            h_set_piece_color, mutating=True,
            label=lambda a: f"Цвет куска {a.get('piece')}" + (": сброс" if a.get("reset") else ""),
        ),
        Tool(
            "match_piece_colors", "Подгоняю цвет кусков",
            "Авто-подгонка цвета: по 5 кадрам каждого куска ffmpeg меряет среднюю яркость, U/V (баланс) и "
            "насыщенность и ставит кускам pieces (нет — всем, кроме образца) поправку (exposure, saturation, "
            "temperature, tint), чтобы они совпали с куском-образцом reference (нет — самый длинный). strength "
            "0..1 — доля пути (1 — полностью, 0.5 — наполовину). Ответ: замеры до/после и разница яркости. "
            "Ночь и день так не сравнять — смотри contact_sheet. / Auto-match piece colours to a reference piece.",
            _obj({"reference": {"type": "integer"}, "pieces": {"type": "array", "items": {"type": "integer"}},
                  "strength": {"type": "number", "minimum": 0, "maximum": 1}}),
            h_match_piece_colors, mutating=True,
            label=lambda a: "Подгоняю цвет кусков" + (f" к куску {a.get('reference')}" if a.get("reference") is not None else ""),
        ),
        Tool(
            "snap_to_beats", "Ставлю на доли",
            "Привязка к долям музыки одной командой: targets — layers (начала слоёв, длина та же), keys (ключевые "
            "кадры слоёв), camera (ключи камеры), joins (склейки кусков — подрезкой конца куска, всё после стыка "
            "едет за ним) или all; по умолчанию layers. grid: beat (каждая доля), downbeat (сильная, раз в такт), "
            "half (восьмые). tolerance (0.15 с; до 0.5) — дальше от доли не трогаем. Сетка: музыка set_music "
            "(audio_map.music_beats), без неё — доли звука клипа (source: audio), или своя: bpm + first_beat "
            "(+ first_downbeat) в секундах готового ролика, или beats — список моментов. Субтитры, чат, PNGtuber "
            "и запертые слои не двигаются; layer_ids — только эти слои. dry_run — только показать сдвиги. "
            "/ Snap layer starts, keyframes and cut joins to the nearest music beat.",
            _obj({"targets": {"type": "array", "items": {"type": "string",
                                                         "enum": ["layers", "keys", "camera", "joins", "all"]}},
                  "tolerance": {"type": "number", "minimum": 0.01, "maximum": 0.5},
                  "grid": {"type": "string", "enum": ["beat", "downbeat", "half"]},
                  "source": {"type": "string", "enum": ["auto", "music", "audio"]},
                  "bpm": {"type": "number"}, "first_beat": {"type": "number"}, "first_downbeat": {"type": "number"},
                  "beats": {"type": "array", "items": {"type": "number"}},
                  "layer_ids": {"type": "array", "items": {"type": "string"}},
                  "dry_run": {"type": "boolean"}}),
            h_snap_to_beats, mutating=True,
            label=lambda a: "Ставлю на доли: " + ", ".join(a.get("targets") or ["layers"]),
        ),
        Tool(
            "set_piece_fit", "Кадр шире окна",
            "Кусок piece шире окна 9:16 — когда двое по краям широкого плана не влезают в окно (камера только "
            "приближает, zoom ≥ 1): w — доля ширины исходника в кадре (0.35..1; 1 — весь кадр, ≈0.6 — двое "
            "крупнее), bg — фон сверху и снизу: blur (размытый тот же кадр, по умолчанию) или анимированный фон "
            "(имя стиля aurora|grid|bokeh|waves или объект {kind, colors, speed, intensity} как у set_backdrop); "
            "black/#rrggbb (заливка) — только если владелец просил. "
            "Центр — тот же focus куска. w null — обычный кадр. Раскладка клипа (split/pip/meme) на этот кусок "
            "не действует. Часто лучше фокус на говорящего с cut на склейках (set_focus) — fit для общего плана. "
            "/ Show a piece wider than the 9:16 window.",
            _obj({"piece": {"type": "integer"}, "w": {"type": ["number", "null"]},
                  "bg": {"type": ["string", "object"],
                         "description": "blur | aurora | grid | bokeh | waves | {kind, colors, speed, intensity} | black | #rrggbb"}},
                 ["piece"]),
            h_set_piece_fit, mutating=True,
            label=lambda a: f"Кусок {a.get('piece')} шире окна" if a.get("w") else f"Кусок {a.get('piece')}: обычный кадр",
        ),
        Tool(
            "add_insert", "Вставляю мем",
            "Вставка из библиотеки поверх клипа (видео-мем/картинка/звук): asset_id, at (с клипа), duration, "
            "mode pip|full|sound, x, y, scale, volume. Для статичных картинок лучше слой image. Вставки "
            "видны в view_frame и contact_sheet (первые кадры гифки). Окошко само встаёт между хуком и "
            "субтитрами. mode full другой пропорции (горизонтальная гифка) — вокруг размытая копия вставки "
            "(fill blur, по умолчанию), cover — обрезать во весь кадр, black — чёрные поля. Один файл дважды — "
            "предупреждение. / Insert.",
            _obj({"asset_id": {"type": "integer"}, "at": {"type": "number"}, "duration": {"type": "number"},
                  "mode": {"type": "string", "enum": ["pip", "full", "sound"]}, "x": {"type": "number"},
                  "y": {"type": "number"}, "scale": {"type": "number"}, "volume": {"type": "number"},
                  "reason": {"type": "string"},
                  "fill": {"type": "string", "enum": ["blur", "cover", "black", "clip"],
                           "description": ("mode full: чем заполнить кадр вокруг вставки другой пропорции; clip — "
                                           "размытый кадр клипа (по умолчанию у прозрачных гифок/PNG)")},
                  "at_word": _AT_WORD_SCHEMA,
                  "word_offset": {"type": "number"}}, ["asset_id"]),
            h_add_insert, mutating=True,
            label=lambda a: (f"Вставляю мем #{a.get('asset_id')} на слово" if a.get("at_word") is not None
                             else f"Вставляю мем #{a.get('asset_id')} на {(_num(a.get('at'), 0.0) or 0.0):.1f} с"),
        ),
        Tool(
            "remove_insert", "Убираю вставку",
            "Убрать вставку по index (из get_clip). / Remove insert.",
            _obj({"index": {"type": "integer"}}, ["index"]), h_remove_insert, mutating=True,
            label=lambda a: f"Убираю вставку {a.get('index')}",
        ),
    ]


def _build_registry() -> list[Tool]:
    rank = {name: index for index, name in enumerate(TOOL_ORDER)}
    entries = [*_remaining_tools(), *layer_tools_entries(), *framing_tools_entries(), *asset_tools_entries(), *subtitle_tools_entries(), ]
    return sorted(entries, key=lambda tool: rank.get(tool.name, len(rank)))

_REGISTRY: dict[str, Tool] | None = None

# Только для внешних агентов (MCP): найти проект и клип.
DISCOVERY_TOOLS = [
    Tool(
        "list_projects", "Проекты",
        "Видео-источники с числом клипов, новые первыми: id здесь — это source_id для инструментов источника. channel_project_id — проект-серия/канал "
        "источника: прочитай get_project_brief(source_id=id) перед нарезкой. Список ПОСТРАНИЧНЫЙ (limit по "
        "умолчанию 100, до 500; offset): СКОЛЬКО ВСЕГО — поле total (all_sources_total — без фильтров, by_status — "
        "по статусам), не длина projects; truncated/next_offset — есть ещё. query — часть имени/ссылки, status — "
        "фильтр. / Projects (source videos), paginated: use total for counts.",
        _obj({"offset": {"type": "integer"}, "limit": {"type": "integer"}, "query": {"type": "string"},
              "status": {"type": "string"}}), h_list_projects, scope="global",
    ),
    Tool(
        "list_clips", "Клипы проекта",
        "Клипы видео-источника (source_id обязателен, id из list_projects): clip_id, название, ★, длина, число слоёв. Постранично (limit по умолчанию 100, до 500; "
        "offset): сколько всего — total, не длина clips; truncated/next_offset — есть ещё. "
        "/ Clips of a project, paginated: use total for counts.",
        _obj({**SOURCE_ARGS, "favorites_only": {"type": "boolean"},
              "offset": {"type": "integer"}, "limit": {"type": "integer"}}),
        h_list_clips, scope="global",
    ),
    Tool(
        "get_source_transcript", "Расшифровка проекта",
        "Реплики ВСЕГО исходника (source_id обязателен, id из list_projects) [{start, end, text}] (время исходника) — выбрать моменты "
        "для клипов без анализа Gemini. Первый раз распознаёт локальный Whisper ФОНОМ (минуты): вызов "
        "ждёт не дольше ~45 с и, если не готово, отвечает status \"running\" — «распознаю: готово N из M "
        "мин, повтори через ~K с» (retry_after_sec); зови тот же вызов ещё раз, второе распознавание не "
        "запустится. Готово — status \"done\" и реплики из кэша. "
        "Звук без речи (игра) — пустой список и no_speech: ищи моменты source_loudness. "
        "/ Whole-source transcript.",
        _obj({**SOURCE_ARGS, "from_sec": {"type": "number"}, "to_sec": {"type": "number"}}),
        h_source_transcript, scope="global",
    ),
    Tool(
        "get_shots", "Склейки планов",
        "Моменты смены плана в исходнике source_id (id из list_projects, обязателен; секунды исходника) между from_sec и to_sec — чтобы ставить "
        "жёсткую смену кадрирования ровно на склейку. Первый вызов — ffmpeg по всему видео (минута-две), "
        "потом кэш. / Shot boundaries of the source.",
        _obj({**SOURCE_ARGS, "from_sec": {"type": "number"}, "to_sec": {"type": "number"}}),
        h_source_shots, scope="global",
    ),
    Tool(
        "create_clip", "Создаю клип",
        "Порядок: посмотри стрим, задай вопросы (add_stream_questions), дождись ответов (list_stream_answers), потом режь: "
        "пока ответов нет, откажет и назовёт открытые вопросы. "
        "Новый клип из кусков видео-источника: source_id (id из list_projects), title, pieces [{start_sec, end_sec, title?}] "
        "(каждый 1.5–180 с, до 12; короткий кусок 1.5–3 с — кадр под широкий баннер/титр; source_start/source_end — тоже можно). Клип сразу с пресетом рендера по "
        "умолчанию (preset_id числом). Вернёт clip_id — дальше обычные инструменты клипа. / Create a clip from timecodes.",
        _obj({**SOURCE_ARGS, "title": {"type": "string"}, "description": {"type": "string"},
              "pieces": {"type": "array", "items": _obj({"start_sec": {"type": "number"}, "end_sec": {"type": "number"},
                                                          "title": {"type": "string"}}, ["start_sec", "end_sec"])},
              "version_of": {"type": "integer", "description": "новая версия этого клипа (его clip_id): карточка не задвоится"}},
             ["title", "pieces"]),
        h_create_clip, scope="global",
    ),
    Tool(
        "create_canvas_clip", "Создаю холст",
        "Пустой холст: клип БЕЗ исходного видео длиной duration (5–180 с, по умолчанию 15) — весь кадр "
        "анимированный фон background (имя стиля aurora/grid/bokeh/waves/gradient/solid или объект "
        "{kind, colors, speed, intensity} как у set_backdrop; не задан — аврора). Для промо из картинок: "
        "дальше add_layers, set_music, звуки (слой audio), рендер — как у обычного клипа. Холсты лежат в "
        "скрытом видео-источнике (в list_projects его нет; source_id — в ответе). / Empty canvas clip, no source video.",
        _obj({"duration": {"type": "number"}, "title": {"type": "string"}, "description": {"type": "string"},
              "background": {"type": ["string", "object"]}}, ["duration"]),
        h_create_canvas_clip, scope="global",
        label=lambda a: f"Создаю холст на {(_num(a.get('duration'), 15.0) or 15.0):g} с",
    ),
    Tool(
        "list_modules", "Модули сервиса",
        "Какие модули сервиса включены (Настройки → Модули): id, название, включён ли, от чего зависит, чем "
        "дорог (cpu/ram/network/paid) и какие инструменты даёт. Выключенный модуль прячет свои инструменты, а "
        "вызов отвечает «модуль … выключен в настройках» — не обходи его другими инструментами, скажи владельцу. "
        "Переключает модули ТОЛЬКО владелец в интерфейсе — у агента такого инструмента нет. "
        "/ Service modules and whether they are enabled (read-only).",
        _obj({}), h_list_modules, scope="global",
    ),
    Tool(
        "tts_preview", "Проба голоса",
        "Проба голоса TTS: короткая фраза (text; по умолчанию приветствие) → mp3 в библиотеке, в ответе url и "
        "asset_id — дай владельцу послушать, прежде чем выбрать голос для dub_clip или персонажа ИИ-видео. "
        "provider gemini (по умолчанию) — БЕСПЛАТНО, voice — голос Gemini (Zephyr, Puck, Charon, Kore, Fenrir, "
        "Leda, Orus, Aoede, Callirrhoe, Autonoe, Enceladus, Iapetus, Umbriel, Algieba, Despina, Erinome, "
        "Algenib, Rasalgethi, Laomedeia, Achernar, Alnilam, Schedar, Gacrux, Pulcherrima, Achird, "
        "Zubenelgenubi, Vindemiatrix, Sadachbia, Sadaltager, Sulafat), style — как говорить («говори весело, "
        "быстро»). provider polza — ПЛАТНО: без confirm только оценка, confirm — только по «да» владельца. "
        "Та же фраза тем же голосом — тот же файл. / Audition a TTS voice (gemini free).",
        _obj({"provider": {"type": "string", "enum": ["gemini", "polza", "none"]}, "voice": {"type": "string"},
              "model": {"type": "string"}, "style": {"type": "string"}, "text": {"type": "string"},
              "confirm": {"type": "boolean"}}),
        h_tts_preview, scope="global", mutating=True,
        label=lambda a: f"Пробую голос {a.get('voice') or ''}".strip(),
    ),
]


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
