"""Линт качества клипа перед сдачей (``lint_clip``): то, что глазами проверять долго и ненадёжно.

Закрывает строки 7, 8, 16, 17 docs/guides/missing.md. Пять проверок (``CHECKS``):

* ``ui_zones`` — текст, субтитры (слой ``captions`` и прожигаемые ASS), плашка канала, эмодзи и
  стикеры против интерфейса площадок 9:16: колонка кнопок справа, подпись снизу (у Reels выше,
  чем у TikTok и Shorts), шапка сверху. Раскладки — ``app/safe_zones.PLATFORM_UI`` (приближение,
  источник там же). Находка: какой слой, на какой площадке, на сколько px заходит, когда, и как
  поправить (сдвинуть ``x``/``y``, сузить ``maxWidth``, поднять субтитры ``position_pct``).
* ``reading`` — темп чтения титров и страниц субтитров: время на экране против
  ``READ_SEC_PER_WORD`` · слов + ``READ_BASE_SEC`` и знаки в секунду. Время — на шкале clip
  (как слои и таймлайн редактора), длительность — в готовом ролике (вырезы внутри титра его
  съедают). Скорость кусков (app/piece_speed.py) уже в шкале clip: слова ускоренного куска
  стоят плотнее — находка называет кусок и его скорость. Субтитры — те же слова, что увидит
  зритель: после J/L-склеек (слова идут за звуком, ``audio_edit.heard_subtitles``), без кусков
  с реверсом.
* ``flashes`` — вспышки готового рендера (ffmpeg → кадры 40×40 по 30 в секунду): общие (перепад
  относительной яркости ≥ 10 %, тёмная сторона < 0.8) и насыщенно-красные, по всему кадру и по
  четвертям (площадь ≥ ¼ кадра, как у WCAG 2.3.1 / критерия Хардинга — упрощённо). Больше 3
  вспышек в секунду — диапазоны времени.
* ``loop`` — насколько конец стыкуется с началом: разница последнего и первого кадра, уровень
  звука последних ~0.3 с против первых 0.3 с → оценка 0..1 и подсказки (``set_end_hold(0)``,
  подрезать хвост, убрать концовку-баннер). Без рендера — только подсказки по настройкам клипа.
* `audio_mix` — сведение звука по стемам (app/audio_mix.py): музыка под речью тише голоса на 12–18 дБ,
  звуки не закрывают слова, итог −14 LUFS и true peak ≤ −1 dBTP (по свежему рендеру или прогнозом).
* ``montage`` — ремесло монтажа без медиа: ч/б (fx grayscale) до конца клипа или дольше 2.5 с — цвет не
  вернётся; одна и та же вставка (asset) дважды (QA 02.11, клип 892).
* ``fragments`` — обрывки после вырезов (app/cuts.py ``kept_islands``): кусок короче 2.5 с или без трёх
  слов между разрывами — «момент из одного слова» (горизонталка Муры 31.10). Без медиа, по текущему плану.

* ``sub_sync`` — субтитры против звука, без рендера. Страницы против распознанных слов (app/speech_heard.py, локальный
  whisper, один раз на набор кусков): страница раньше своих слов больше 0.6 с, позже больше 0.7 с, уплывший конец,
  слова, которых не слышно. VAD (app/speech_activity.py): строка в тишине, речь без субтитров перед строкой.

Только предупреждения: ничего не меняет и не блокирует. ``plan``-проверки (зоны, чтение,
подсказки лупа) — без медиа, за доли секунды; ``flashes`` и оценка лупа — по последнему
готовому рендеру (``media``: auto — если он есть). Время находок: ``t`` — шкала clip (перемотка
в редакторе, ``view_frame``), ``final_t`` — секунды готового ролика.

Где что лежит. Здесь — общий прогон ``lint_clip``, свежесть рендера, сводка. Проверки по файлам: общее ``app/quality_lint_base.py``,
зоны интерфейса ``app/quality_lint_zones.py``, темп чтения ``app/quality_lint_reading.py``, вспышки и луп ``app/quality_lint_media.py``,
монтаж ``app/quality_lint_montage.py``. Те модули этот не импортируют, а он их — поэтому все имена по-прежнему доступны отсюда.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from app.quality_lint_base import (  # noqa: F401
    CHECKS, CHECK_ALIASES, _num, _short, normalize_checks, _finding,
)
from app.quality_lint_zones import (  # noqa: F401
    W, H, MIN_PEN_PX, WARN_PEN_PX, WARN_AREA_SHARE, ZONE_GAP_PX, SAMPLE_STEP, SAMPLE_MAX, MIN_OPACITY,
    STICKER_MAX_SHARE, ZONE_LABELS, CAPTION_PAUSE_SPLIT, CAPTION_HOLD, CAPTION_BRIDGE, CAPTION_BASE,
    CAPTION_STYLE_LOOK, ORIG_SPEAKER, _EDGE_PUNCT, _SENTENCE_END, caption_look, _char_factor, _caption_word, _pack,
    caption_layer_pages, ass_pages, _nameplate_box, _counter_text, is_sticker, ZONE_TYPES, layer_box, _samples,
    _on_frame, zone_penetration, _fix_for, _what, _zone_findings_for, ui_zone_findings,
)
from app.quality_lint_reading import (  # noqa: F401
    READ_SEC_PER_WORD, READ_BASE_SEC, FAST_CPS, FLASH_PAGE_SHARE, MAX_PAGE_FINDINGS, FAST_PAGE_MIN_SEC,
    SHORT_PAGES_WARN_SHARE, read_need, _final_span, _piece_speed_note, _title_text, _anim_len,
    title_reading_findings, page_reading_findings, _fast_page_findings,
)
from app.quality_lint_media import (  # noqa: F401
    FLASH_FPS, FLASH_SIZE, FLASH_LUM_DELTA, FLASH_DARK_MAX, RED_DELTA, RED_RATIO, MAX_FLASHES_PER_SEC,
    FLASH_TIMEOUT_SEC, _REGIONS, LOOP_AUDIO_SEC, LOOP_FRAME, LOOP_MAD_SCALE, LOOP_SILENT_DB, LOOP_DB_SCALE,
    LOOP_VISUAL_WEIGHT, _relative_luminance, _red_value, frame_series, transitions, flash_windows, analyze_series,
    _peak_rate, read_frames, analyze_flashes, flash_findings, _to_clip, _run_bytes, _gray, _level_db, loop_metrics,
    loop_score, loop_findings, loop_plan_hints,
)
from app.quality_lint_montage import (  # noqa: F401
    MAX_FRAGMENT_FINDINGS, MAX_SYNC_FINDINGS, _media_ref, montage_findings, people_findings,
    IMAGE_MIN_VISIBLE_SHARE, IMAGE_FULL_SHARE, _VISIBLE_CACHE, visible_width_share, _hook_layers, FACE_COVER_SHARE,
    FULL_INSERT_MAX_SEC, CARD_MAX_SHARE, CARD_MARGIN, _rect_overlap, _captions_band, CAPTION_TEXT_SHARE,
    CAPTION_COVER_SHARE, _layer_order, _covered_captions, card_spot, _natural_size, image_card_findings,
    avatar_face_findings, avatar_presence_findings, tail_fragment_findings, fragment_findings, sub_sync_findings,
)

MEDIA_CHECKS = ("flashes",)  # без рендера не проверить; loop без рендера — только подсказки


# Субтитры по 1–2 слова (maxWords ≤ 2) — нарочно быстрый стиль «слово за голосом»: норму чтения
# (0.3 с/слово + 0.5 с) к ним не применяем, предупреждаем только о странице, что мелькнула короче
# этого (её глаз не поймает) — QA 01.11: 10 warn по темпу на сабах в 1–2 слова, которые так и заказаны.
FAST_CAPTIONS_MAX_WORDS = 2


# =============================================================== всё вместе


def _ass_block(store: Any, rs: dict, words: list[dict]) -> dict | None:
    """Прожигаемые субтитры клипа: стиль (как frame_subtitles_ass) → страницы и место."""
    if not rs.get("subs_on") or not words:
        return None
    from app.clip_settings import settings_to_render_kwargs
    from app.clip_subtitles import _default_profile

    try:
        default_id = int(store.get_app_setting_value("default_subtitle_profile_id") or 0) or None
    except (TypeError, ValueError, AttributeError):
        default_id = None
    kwargs = settings_to_render_kwargs(rs, default_id)
    try:
        profile = (store.get_subtitle_profile(int(kwargs["subtitle_profile_id"])) if kwargs.get("subtitle_profile_id")
                   else _default_profile(store))
    except (KeyError, TypeError, ValueError):
        return None
    pages = ass_pages(words, profile or {})
    return {"pages": pages, "bottom_pct": _num(rs.get("sub_pos_pct"), 12.0),
            "align": int(_num((profile or {}).get("alignment"), 2) or 2)}


def _latest_render_file(store: Any, clip_plan_id: int) -> tuple[dict | None, Path | None]:
    for clip in store.list_clips_for_plan(int(clip_plan_id)):
        if clip.get("status") == "succeeded":
            path = Path(clip.get("output_path") or "")
            return clip, (path if clip.get("output_path") and path.is_file() else None)
    return None, None


def plan_fingerprint(store: Any, clip_plan_id: int) -> str | None:
    """Отпечаток того, что попадает в рендер: клип-файл (куски, монтаж, слои) и субтитры — без
    названия, описания и заметок (их правит автоописание после рендера). Тот же клип — тот же
    отпечаток; правка слоя, выреза или слова — другой."""
    import hashlib
    import json

    try:
        from app.clip_history import capture

        state = capture(store, int(clip_plan_id))
    except Exception:  # noqa: BLE001 - не сняли — проверка по снимкам истории
        return None
    spec = {k: v for k, v in (state.get("spec") or {}).items() if k not in ("title", "description", "notes", "favorite")}
    raw = json.dumps({"spec": spec, "subtitles": state.get("subtitles")}, ensure_ascii=False, sort_keys=True,
                     default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def render_is_stale(store: Any, clip_plan_id: int, clip: dict) -> bool:
    """Клип правили после этого рендера? По отпечатку рендера (``clips.plan_fingerprint``), а у старых
    рендеров без него — по снимку истории правок новее конца рендера. Статус клипа («rendered»,
    который рендер ставит в конце) правкой не считается — раньше из-за него свежий рендер
    сразу был «устаревшим»."""
    stamp = clip.get("plan_fingerprint")
    if stamp:
        current = plan_fingerprint(store, clip_plan_id)
        return bool(current) and current != stamp
    try:
        edited = store.last_clip_plan_history_at(int(clip_plan_id))
    except Exception:  # noqa: BLE001
        return False
    done = str(clip.get("finished_at") or clip.get("created_at") or "")
    return bool(edited and done and edited > done)


def _project_platforms(store: Any, clip_plan_id: int) -> list[str]:
    try:
        from app import autopilot as A
        from app import projects as P

        project = P.project_of_plan(store, int(clip_plan_id))
        return list(A.get_config(store, project)["platforms"]) if project is not None else []
    except Exception:  # noqa: BLE001 - без проекта — все площадки
        return []


def _media_flag(media: Any) -> str:
    if media in (None, "", "auto"):
        return "auto"
    if isinstance(media, str):
        return "yes" if media.strip().lower() in ("1", "true", "yes", "on", "да") else "no"
    return "yes" if media else "no"


def lint_clip(store: Any, clip_plan_id: int, checks: Any = None, platforms: Any = None, media: Any = "auto") -> dict:
    """Все проверки клипа. ``checks`` — подмножество ``CHECKS``; ``platforms`` — tiktok/reels/shorts
    (youtube/instagram; пусто — площадки проекта, нет — все три); ``media`` — auto (по готовому
    рендеру, если он есть) | true | false. Ничего не меняет."""
    from app.agent_tools import plan_clock
    from app.clip_settings import normalize_render_settings
    from app.formats import settings_layers_format, settings_render_format

    wanted = normalize_checks(checks)
    plan = store.get_clip_plan(int(clip_plan_id))
    rs = normalize_render_settings(plan.get("render_settings") or {})
    clock = plan_clock(store, plan)
    duration = clock.clip_duration
    layers = [layer for layer in rs.get("layers") or [] if not layer.get("hidden") and layer.get("type") != "audio"]
    camera = rs.get("camera")
    fmt, layers_fmt = settings_render_format(rs), settings_layers_format(rs)
    used_platforms = platforms if platforms not in (None, "", []) else _project_platforms(store, clip_plan_id)
    from app.safe_zones import platform_ui

    uis = platform_ui(used_platforms)
    findings: list[dict] = []
    skipped: dict[str, str] = {}
    out: dict[str, Any] = {"ok": True, "clip_id": int(clip_plan_id), "format": fmt, "duration": duration,
                           "final_duration": clock.final_duration, "platforms": list(uis)}

    words: list[dict] = []
    if "ui_zones" in wanted or "reading" in wanted:
        try:
            from app.motion_render import plan_caption_words

            words = plan_caption_words(store, plan)
        except Exception:  # noqa: BLE001 - без субтитров — только титры
            words = []
    caption_layers = [layer for layer in layers if layer.get("type") == "captions"]
    pages_by_layer = {str(layer.get("id")): caption_layer_pages(words, layer.get("props"), _num(layer.get("start")),
                                                                min(_num(layer.get("end"), duration), duration or math.inf))
                      for layer in caption_layers}
    try:
        ass = _ass_block(store, rs, words) if words else None
    except Exception:  # noqa: BLE001 - стиль не прочитать — без прожигаемых
        ass = None

    if "ui_zones" in wanted:
        if fmt != "9:16" or layers_fmt != "9:16":
            skipped["ui_zones"] = (f"формат {fmt}: колонка кнопок и подпись TikTok/Reels/Shorts — только у вертикали 9:16 "
                                   "(края 1:1/4:5/16:9 проверяет add_layers)")
        else:
            findings += ui_zone_findings(layers, list(uis), camera, pages_by_layer, ass)

    if "reading" in wanted:
        findings += title_reading_findings(layers, clock, duration)
        for layer in caption_layers:
            fast = caption_look(layer.get("props"))["maxWords"] <= FAST_CAPTIONS_MAX_WORDS
            findings += page_reading_findings(pages_by_layer.get(str(layer.get("id"))) or [], "субтитры", clock,
                                              duration, layer=str(layer.get("id")), fast=fast)
        if ass:
            findings += page_reading_findings(ass["pages"], "прожигаемые субтитры", clock, duration)

    mode = _media_flag(media)
    clip, path = (None, None)
    if mode != "no" and ("flashes" in wanted or "loop" in wanted):
        clip, path = _latest_render_file(store, clip_plan_id)
        if clip is not None:
            out["render_id"] = int(clip["id"])
            if render_is_stale(store, int(clip_plan_id), clip):
                out["render_stale"] = ("клип правили после этого рендера — вспышки и луп проверены по старому файлу; "
                                       "перерендерь и проверь снова")
    no_media = ("проверка медиа выключена (media: false)" if mode == "no" else
                "нет готового рендера — render, потом lint_clip" if clip is None else "файла рендера нет на диске")

    if "flashes" in wanted:
        if path is None:
            skipped["flashes"] = no_media
        else:
            try:
                result = analyze_flashes(path)
                findings += flash_findings(result, clock)
                out["flashes"] = {"max_per_sec": result["max_per_sec"], "seconds": result["seconds"],
                                  "limit_per_sec": MAX_FLASHES_PER_SEC}
            except Exception as exc:  # noqa: BLE001 - не прочитали файл — скажем, не упадём
                skipped["flashes"] = f"не удалось разобрать рендер: {str(exc)[:200]}"

    if "loop" in wanted:
        metrics = None
        if path is not None:
            try:
                metrics = loop_metrics(path)
            except Exception as exc:  # noqa: BLE001
                skipped["loop"] = f"оценка лупа не удалась: {str(exc)[:200]} — только подсказки по настройкам"
        elif mode != "no":
            skipped["loop"] = f"{no_media}: оценки лупа нет, только подсказки по настройкам"
        loop_items, loop_info = loop_findings(metrics, loop_plan_hints(rs, clock), out.get("render_id"))
        findings += loop_items
        if loop_info:
            out["loop"] = loop_info

    if "montage" in wanted:
        findings += montage_findings(rs, duration)
        findings += people_findings(store, rs)
        if layers_fmt == "9:16":
            try:
                from app.agent_tools import avatar_face_zones

                faces = avatar_face_zones(store, int(clip_plan_id), rs)
            except Exception:  # noqa: BLE001 - без лица аватара — прежние проверки
                faces = []
            findings += image_card_findings(store, layers, duration, faces=faces)
        findings += avatar_face_findings(store, int(clip_plan_id), rs)
        findings += avatar_presence_findings(store, int(clip_plan_id), rs)
        findings += tail_fragment_findings(store, plan, rs)

    if "fragments" in wanted:
        items, info = fragment_findings(store, plan, rs, clock)
        findings += items
        out["fragments"] = info

    if "sub_sync" in wanted:
        if mode == "no":
            skipped["sub_sync"] = "проверка медиа выключена (media: false)"
        else:
            try:
                items, info = sub_sync_findings(store, plan, clock)
                findings += items
                if info:
                    out["sub_sync"] = info
            except Exception as exc:  # noqa: BLE001 - нет исходника/звука — скажем, не упадём
                skipped["sub_sync"] = f"не удалось разобрать звук: {str(exc)[:200]}"

    if "audio_mix" in wanted:
        # Сведение по стемам (app/audio_mix.py): ffmpeg по файлам клипа, рендер — если свежий.
        if mode == "no":
            skipped["audio_mix"] = "проверка медиа выключена (media: false)"
        else:
            try:
                from app.audio_mix import lint_findings, mix_report

                report = mix_report(store, int(clip_plan_id))
                findings += lint_findings(report)
                out["audio_mix"] = {"summary": report.get("summary"),
                                    "voice_vs_music_db": (report.get("voice_vs_music") or {}).get("diff_db"),
                                    "lufs": ((report.get("final") or report.get("predicted") or {}).get("integrated")),
                                    "true_peak": ((report.get("final") or report.get("predicted") or {})
                                                  .get("true_peak"))}
            except Exception as exc:  # noqa: BLE001 - нет файлов/ffmpeg — скажем, не упадём
                skipped["audio_mix"] = f"сведение не измерено: {str(exc)[:200]}"

    for item in findings:
        if "t" in item and "final_t" not in item:
            try:
                item["final_t"] = round(clock.clip_to_final(item["t"]), 2)
            except Exception:  # noqa: BLE001
                pass
    order = {"warn": 0, "info": 1}
    findings.sort(key=lambda f: (order.get(f["severity"], 2), CHECKS.index(f["check"]), f.get("t", 0.0)))
    counts = {"warn": sum(f["severity"] == "warn" for f in findings), "info": sum(f["severity"] == "info" for f in findings)}
    by_check = {c: sum(f["check"] == c for f in findings) for c in wanted}
    out.update(checks=wanted, findings=findings, counts=counts, by_check=by_check, skipped=skipped,
               summary=summary_text(counts, by_check, skipped))
    return out


def summary_text(counts: dict, by_check: dict, skipped: dict) -> str:
    labels = {"ui_zones": "зоны площадок", "reading": "темп чтения", "flashes": "вспышки", "loop": "луп",
              "audio_mix": "сведение звука", "montage": "монтаж", "fragments": "обрывки", "sub_sync": "субтитры и голос"}
    if not counts.get("warn") and not counts.get("info"):
        head = "замечаний нет"
    else:
        head = f"предупреждений {counts.get('warn', 0)}, подсказок {counts.get('info', 0)}"
        parts = [f"{labels[c]} {n}" for c, n in by_check.items() if n]
        if parts:
            head += " (" + ", ".join(parts) + ")"
    if skipped:
        head += "; не проверено: " + ", ".join(labels.get(c, c) for c in skipped)
    return head


def brief(result: dict, limit: int = 8) -> dict:
    """Коротко для карточки одобрения и ответа submit_for_approval: счётчики и главные предупреждения."""
    warns = [f for f in result.get("findings") or [] if f.get("severity") == "warn"]
    return {"counts": result.get("counts") or {}, "summary": result.get("summary") or "",
            "warnings": [f["message"] + (f" → {f['fix']}" if f.get("fix") else "") for f in warns[:limit]]}
