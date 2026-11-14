"""План проигрывания смонтированного клипа: что звучит и что видно в каждую секунду ГОТОВОГО ролика.

Его читают предпросмотр «Смотреть монтаж» (frontend/src/pages/workspace/preview) и пакет для
монтажки (app/timeline_package.py). Времена считает тот же код, что и рендер
(``ClipRenderService``: вырезы, настоящие overlap склеек, стоп-кадры, перенос слоёв, камеры,
слов, вставок и звука) — у предпросмотра нет своей математики, которая могла бы разойтись.

Шкалы: ``parts[*].start/end`` — секунды основы (куски после вырезов и наплывов, без стоп-кадров);
``layers``, ``camera``, ``words`` — время слоёв (как ``comp`` компоновщика, ``freezes`` читаются
``motionTime``); звук и вставки — секунды готового ролика со стоп-кадрами.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

SOURCE_FADE_NOTE = "громкость ролика (−14 LUFS) рендер выравнивает в конце; в предпросмотре — как в записи"


def revision(store: Any, clip_plan_id: int) -> str:
    """Отпечаток монтажа клипа: сменился — кто-то (агент, другая вкладка) правил клип."""
    plan = store.get_clip_plan(clip_plan_id)
    subs = store.get_clip_plan_subtitles(clip_plan_id)
    parts = [
        [s.get("id"), s.get("start_sec"), s.get("end_sec"), s.get("focus"), s.get("speed"), s.get("reverse"),
         s.get("color_fix"), s.get("fit")]
        for s in plan.get("segments") or []
    ]
    blob = json.dumps([parts, plan.get("render_settings"), subs], sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def _asset_url(ref: str) -> str:
    """URL файла слоя: как у редактора (frontend/src/pages/workspace/motion/useLayerAudio.ts)."""
    if ref.startswith("sfx:"):
        return f"/api/motion/packs/{quote(ref[4:], safe='/')}"
    return f"/media/montage-assets/{quote(ref, safe='')}"


def _latest_render(store: Any, clip_plan_id: int) -> dict | None:
    for clip in store.list_clips_for_plan(clip_plan_id):
        if clip.get("status") != "succeeded":
            continue
        path = str(clip.get("output_path") or "")
        if path and Path(path).exists():
            return clip
    return None


def media_for(store: Any, source: dict, clip_plan_id: int, fresh_fingerprint: str | None = None,
              ranges: list[tuple[float, float]] | None = None) -> dict:
    """Чем играть основу: прокси, куски, запись или (ничего нет) готовый рендер с честной припиской.

    ``render`` — готовый рендер, если он есть: плеер предложит его, когда живой монтаж не успевает."""
    from app import preview_proxy

    source_id = int(source["id"])
    proxy = preview_proxy.status(source)
    has_file = bool(source.get("has_file"))
    latest = _latest_render(store, clip_plan_id)
    stale = bool(latest and fresh_fingerprint and latest.get("plan_fingerprint") and latest["plan_fingerprint"] != fresh_fingerprint)
    render = {"url": f"/media/clips/{latest['id']}", "poster": f"/media/clips/{latest['id']}/thumb",
              "clip_id": latest["id"], "stale": stale} if latest else None
    out: dict[str, Any] = {"proxy": proxy, "has_file": has_file, "render": render}
    if preview_proxy.is_canvas(source):
        return {**out, "kind": "canvas", "url": f"/media/sources/{source_id}"}
    if proxy.get("status") == "ready":
        note = None if has_file else "Запись удалена: монтаж показан в 480p. Новый рендер — только с записью."
        return {**out, "kind": "proxy", "url": proxy["url"], "note": note}
    if ranges:
        if has_file and preview_proxy.wanted(source):
            # Большая запись без прокси: сама запись не грузится вовсе (один её заголовок — 31 МБ).
            return {**out, "kind": "pieces", "url": None}
        if not has_file and preview_proxy.pieces_status(source, ranges)["ready"] == len(ranges):
            return {**out, "kind": "pieces", "url": None,
                    "note": "Запись удалена: монтаж показан в 480p. Новый рендер — только с записью."}
    if has_file:
        return {**out, "kind": "original", "url": f"/media/sources/{source_id}"}
    if render:
        message = (
            "Запись удалена: живой монтаж показать не из чего. Показан готовый рендер"
            + (" — он собран до последних правок, их в нём нет." if stale else ".")
        )
        return {**out, "kind": "render", "url": render["url"], "poster": render["poster"],
                "clip_id": render["clip_id"], "stale": stale, "message": message}
    return {**out, "kind": "none", "url": None,
            "message": "Запись удалена, готового рендера нет: показать нечего. Скачайте запись заново."}


def part_ranges(store: Any, clip_plan_id: int) -> tuple[dict, list[tuple[float, float]]]:
    """Источник клипа и отрезки записи его частей — те же, что в ``playback_plan`` (без остального плана)."""
    from app.clip_settings import normalize_render_settings
    from app.render import ClipRenderService, effective_preset

    plan = store.get_clip_plan(clip_plan_id)
    rs = normalize_render_settings(plan.get("render_settings") or {})
    plan = {**plan, "render_settings": {**(plan.get("render_settings") or {}), **rs}}
    source = store.get_source(int(plan["source_id"]))
    segments = plan.get("segments") or []
    preset = effective_preset(store, rs.get("preset_id")) or {}
    cut_plan = ClipRenderService(store)._cut_plan(plan, rs.get("preset_id"), float(preset.get("fps") or 30)) if segments else None  # noqa: SLF001
    parts = cut_plan.parts if cut_plan else segments
    from app.piece_speed import is_reversed
    from app.preview_proxy import chunk_ranges

    ranges: list[tuple[float, float]] = []
    for p in parts:
        ranges.extend(chunk_ranges(float(p["start_sec"]), float(p["end_sec"]), is_reversed(p)))
    return source, ranges


def _subtitles(store: Any, plan: dict, cut_plan: Any, freezes: list[dict], rs: dict) -> dict:
    """Субтитры клипа в секундах готового ролика — те же, что прожжёт рендер."""
    from app.cuts import plan_joins_setting, remap_subtitles, subtitles_to_final
    from app.freeze import FreezeMap

    payload = store.get_clip_plan_subtitles(int(plan["id"]))
    if not payload or not (payload.get("lines") or payload.get("words")):
        return {"lines": [], "words": []}
    final = subtitles_to_final(payload, plan.get("segments") or [], rs.get("transition"), cut_plan,
                               plan_joins_setting(plan))
    if freezes:
        final = remap_subtitles(final, FreezeMap(freezes))
    return {"lines": final.get("lines") or [], "words": final.get("words") or []}


def playback_plan(store: Any, clip_plan_id: int) -> dict:
    """Весь монтаж клипа одним словарём (ничего не записывает и не запускает ffmpeg)."""
    from app.clip_settings import normalize_render_settings, settings_to_render_kwargs
    from app.cuts import remap_inserts, remap_motion, remap_points
    from app.formats import frame_size, normalize_format, settings_layers_format, settings_render_format
    from app.freeze import FreezeMap, holds_layers, is_insert
    from app.montage_assets import DUCK_VOLUME, normalize_inserts
    from app.motion import plan_motion, visual_motion
    from app.motion_render import attach_caption_words, has_chat, plan_chat_messages
    from app.piece_speed import is_reversed, seg_length, seg_speed
    from app.render import ClipRenderService, effective_preset
    from app.preview_proxy import chunk_ranges
    from app.transitions import JOIN_SFX_FILES, TRANSITIONS, normalize_transition, part_overlaps

    svc = ClipRenderService(store)
    plan = store.get_clip_plan(clip_plan_id)
    rs = normalize_render_settings(plan.get("render_settings") or {})
    plan = {**plan, "render_settings": {**(plan.get("render_settings") or {}), **rs}}
    source = store.get_source(int(plan["source_id"]))
    segments = plan.get("segments") or []
    preset = effective_preset(store, rs.get("preset_id")) or {}
    fps = float(preset.get("fps") or 30)
    fmt = normalize_format(settings_render_format(rs))
    frame_w, frame_h = frame_size(fmt)
    kwargs = settings_to_render_kwargs(rs)
    transition = normalize_transition(kwargs.get("transition"))
    warnings: list[str] = [SOURCE_FADE_NOTE]

    # ---- основа: части после вырезов и наплывы между ними (как render_montage)
    cut_plan = svc._cut_plan(plan, rs.get("preset_id"), fps) if segments else None  # noqa: SLF001
    parts_src = cut_plan.parts if cut_plan else segments
    jumps = cut_plan.jump_joins if cut_plan else None
    overlaps = part_overlaps(parts_src, transition, jumps) if parts_src else []
    parts: list[dict] = []
    elapsed = 0.0
    for index, part in enumerate(parts_src):
        length = seg_length(part)
        parts.append({
            "index": index,
            "piece": part.get("piece", index) if cut_plan else index,
            "segment_id": part.get("id"),
            "start": round(elapsed, 4),
            "end": round(elapsed + length, 4),
            "src_start": float(part["start_sec"]),
            "src_end": float(part["end_sec"]),
            "speed": seg_speed(part),
            "reverse": is_reversed(part),
            "focus": part.get("focus") or [],
            "fit": part.get("fit"),
            "color_fix": part.get("color_fix"),
            "speed_audio": part.get("speed_audio"),
            "chunks": chunk_ranges(float(part["start_sec"]), float(part["end_sec"]), is_reversed(part)),
            "overlap": round(overlaps[index], 4) if index < len(overlaps) else 0.0,
            "jump": bool(jumps[index]) if jumps and index < len(jumps) else False,
        })
        elapsed += length - (overlaps[index] if index < len(overlaps) else 0.0)
    base_duration = round(elapsed, 4)

    # ---- время: стоп-кадры и финальный стоп-кадр
    freezes = svc._freezes(plan)  # noqa: SLF001
    final_with_freezes = base_duration + sum(f["duration"] for f in freezes if is_insert(f))
    hold = svc._end_hold_sec(plan)  # noqa: SLF001
    duration = round(final_with_freezes + hold, 4)
    mapper = svc._final_mapper(plan, cut_plan)  # noqa: SLF001

    # ---- слои, камера, слова — как compositor (тем же кодом, что рендер)
    motion = plan_motion(rs)
    visual = visual_motion(motion)
    if visual:
        visual = attach_caption_words(store, plan, visual)
        if has_chat(visual.get("layers")):
            # Только сообщения: картинки эмоутов браузер берёт сам, сеть из плана не трогаем.
            messages = plan_chat_messages(store, plan, visual.get("layers") or [])
            if messages:
                visual = {**visual, "chat": messages}
    if cut_plan is not None:
        visual = svc._cut_motion(plan, cut_plan, visual)  # noqa: SLF001
        motion = remap_motion(motion, svc._finished_cut_mapper(plan, cut_plan))  # noqa: SLF001
    if freezes:
        def running(f: dict) -> bool:
            return not holds_layers(f)

        if visual:
            visual = remap_motion(visual, FreezeMap(freezes, only=running))
        motion = remap_motion(motion, FreezeMap(freezes))
    visual = visual or {}
    from app.end_screen import comp_end_screen

    end_screen = comp_end_screen(visual.get("end_screen"), visual.get("layers") or [], final_with_freezes)

    # ---- звук
    from app.audio_edit import music_automation, normalize_audio, part_shifts
    from app.audio_mix import normalize_mix
    from app.audio_mute import final_pass_mutes, normalize_gain, span_floor

    audio_edit = normalize_audio(rs.get("audio"))
    mix = normalize_mix(rs.get("mix"))

    def spans(raw: list[tuple[float, float, Any]]) -> list[dict]:
        out = []
        for a, b, value in raw:
            rng = mapper.range(a, b) if mapper is not None else (a, b)
            if rng:
                out.append({"start": round(rng[0], 3), "end": round(rng[1], 3), "value": value})
        return out

    mute_items = final_pass_mutes(rs.get("mute"), False)
    mutes = spans([(m["start"], m["end"], span_floor(m)) for m in mute_items])
    gains_all = normalize_gain(rs.get("gain"))
    gains = spans([(g["start"], g["end"], g["gain_db"]) for g in gains_all if g.get("track") is None])
    if any(g.get("track") is not None for g in gains_all):
        warnings.append("громкость одной дорожки записи: в предпросмотре дорожки уже смешаны")
    source_keys = audio_edit.get("source_keys") or []
    if mapper is not None and source_keys:
        source_keys = remap_points(source_keys, mapper)
    fx_spans = []
    for item in audio_edit.get("fx") or []:
        rng = mapper.range(item["start"], item["end"]) if mapper is not None else (item["start"], item["end"])
        if rng:
            fx_spans.append({**item, "start": round(rng[0], 3), "end": round(rng[1], 3)})

    # Склейки считаются в секундах основы; вставленные стоп-кадры сдвигают их в готовом ролике.
    freeze_map = FreezeMap(freezes) if freezes else None

    def on_final(t: float) -> float:
        return float(freeze_map.clamp(t)) if freeze_map is not None else float(t)

    shifts = part_shifts(parts_src, segments, audio_edit.get("joins")) if parts_src else []
    jl = []
    for index, shift in enumerate(shifts):
        if shift:
            at = on_final(parts[index + 1]["start"] + overlaps[index] / 2)
            jl.append({"at": round(at, 3), "shift": shift, "prev_end": parts[index]["src_end"],
                       "next_start": parts[index + 1]["src_start"]})

    music = None
    if rs.get("music_on") and rs.get("track_id"):
        try:
            track = store.get_audio_track(int(rs["track_id"]))
        except (KeyError, TypeError, ValueError):
            track = None
        if track:
            volume = track.get("volume")
            music = {
                "url": f"/media/audio-tracks/{track['id']}",
                "track_id": track["id"],
                "label": track.get("label") or "",
                "volume": 0.5 if volume is None else float(volume),
                "fixed_db": mix.get("music_db"),
                "start_sec": max(0.0, float(rs.get("music_start_sec") or 0)),
                "loop": bool(preset.get("music_loop", True)),
                "fade_in": float(preset.get("music_fade_in_sec") or 0),
                "fade_out": max(float(preset.get("music_fade_out_sec") or 0), hold),
                "duck": bool(preset.get("music_duck", True)) and mix.get("sidechain") is not False,
                "duck_amount": 0.6 if preset.get("music_duck_amount") is None else float(preset["music_duck_amount"]),
                "automation": music_automation(rs, mapper),
            }
            if mix.get("music_fx"):
                warnings.append("цепочка эффектов музыки (music_fx) слышна только в рендере")
    if mix.get("voice_fx"):
        warnings.append("цепочка эффектов голоса (voice_fx) слышна только в рендере")

    sfx: list[dict] = []
    for layer in (motion or {}).get("layers") or []:
        if layer.get("type") != "audio" or layer.get("hidden"):
            continue
        props = layer.get("props") or {}
        ref = str(props.get("asset_id") or "")
        if ref:
            sfx.append({"id": layer.get("id"), "ref": ref, "url": _asset_url(ref), "start": layer["start"], "end": layer["end"],
                        "offset": float(props.get("offset") or 0), "volume": float(props.get("volume", 1) or 0),
                        "gain_db": float(props.get("gain_db") or 0), "track": props.get("mode") == "track"})

    join_sfx: list[dict] = []
    if transition.get("sfx") != "none" and float(transition.get("sfx_volume") or 0) > 0:
        name, gain, lead = JOIN_SFX_FILES[transition["sfx"]]
        for index, part in enumerate(parts[:-1]):
            if part["jump"]:
                continue
            at = on_final(parts[index + 1]["start"] + part["overlap"] / 2)
            join_sfx.append({"url": f"/api/motion/packs/freesound/{name}", "at": round(max(0.0, at - lead), 3),
                             "volume": round(gain * float(transition["sfx_volume"]), 3)})

    inserts = normalize_inserts(rs.get("inserts"))
    if mapper is not None and inserts:
        inserts = remap_inserts(inserts, mapper)
    insert_rows: list[dict] = []
    for ins in inserts:
        try:
            asset = store.get_montage_asset(ins["asset_id"])
        except KeyError:
            continue
        kind = asset.get("kind")
        insert_rows.append({
            **ins,
            "url": f"/media/montage-assets/{asset['id']}",
            "kind": kind,
            "label": asset.get("label") or "",
            "mode": "sound" if kind == "audio" else ins.get("mode"),
            "has_audio": bool(asset.get("has_audio")) or kind == "audio",
            # Как в рендере (app/montage_assets.build_inserts_args): своя глубина у видеомема (0 — звук клипа выключен).
            "duck_volume": (ins.get("duck_volume", DUCK_VOLUME)
                            if ins.get("duck") and (ins.get("mode") == "full" or "duck_volume" in ins) else None),
        })

    # ---- субтитры, баннер, лук
    captions_on = any(l.get("type") == "captions" and not l.get("hidden") for l in visual.get("layers") or [])
    subs_on = bool(kwargs.get("subtitle_profile_id") or rs.get("subs_on")) and not captions_on
    profile = None
    profile_id = rs.get("sub_id") or preset.get("subtitle_profile_id")
    if subs_on and profile_id:
        try:
            profile = store.get_subtitle_profile(int(profile_id))
        except (KeyError, TypeError, ValueError):
            profile = None
    if profile is None and subs_on:
        rows = store.list_subtitle_profiles() if hasattr(store, "list_subtitle_profiles") else []
        profile = rows[0] if rows else None
    subs = _subtitles(store, plan, cut_plan, freezes, rs) if segments else {"lines": [], "words": []}
    banner = None
    if rs.get("banner_on") and rs.get("banner_id"):
        banner = {"url": f"/media/banners/{int(rs['banner_id'])}", "height_pct": rs["banner_height_pct"],
                  "pos_pct": rs["banner_pos_pct"]}

    from app.freeze import comp_freezes
    from app.preview_proxy import is_canvas as source_is_canvas
    from app.quality_lint import plan_fingerprint

    try:
        fingerprint = plan_fingerprint(store, clip_plan_id)
    except Exception:  # noqa: BLE001 - только подпись «рендер устарел»
        fingerprint = None
    if jl:
        warnings.append("J/L-склейки при просмотре с кусков не слышны: звук соседнего куска лежит в другом файле")
    if any(item.get("fx") in ("denoise", "speed", "reverse") for item in fx_spans):
        warnings.append("эффекты звука на отрезке «шумодав», «скорость», «реверс» слышны только в рендере")

    return {
        "clip_plan_id": clip_plan_id,
        "title": plan.get("title") or "",
        "revision": revision(store, clip_plan_id),
        "source": {
            "id": source["id"],
            "width": source.get("width") or 0,
            "height": source.get("height") or 0,
            "duration": source.get("duration_sec") or 0,
            "content_crop": source.get("content_crop"),
            "chroma_key": source.get("chroma_key"),
            "focus_preset": source.get("focus_preset"),
            "canvas": source_is_canvas(source),
        },
        "media": media_for(store, source, clip_plan_id, fingerprint, [tuple(c) for p in parts for c in p["chunks"]]),
        "format": fmt,
        "frame": {"w": frame_w, "h": frame_h},
        "fps": fps,
        "duration": duration,
        "base_duration": base_duration,
        "hold": hold,
        "parts": parts,
        "transition": {**transition, "xfade": (TRANSITIONS.get(transition["type"]) or (None, None))[1]},
        "freezes": comp_freezes(freezes),
        "freezes_raw": freezes,
        "layers": visual.get("layers") or [],
        "camera": visual.get("camera") or [],
        "layers_format": settings_layers_format(rs),
        "words": visual.get("words") or [],
        "chat": visual.get("chat") or [],
        "motion_blur": visual.get("motion_blur"),
        "end_screen": end_screen,
        "layout": rs.get("layout"),
        "backdrop": rs.get("backdrop"),
        "mirror": bool(rs.get("mirror")),
        "look": {k: preset.get(k) for k in ("color_style", "color_strength", "vignette", "grain", "label", "id")},
        "subtitles": {"on": subs_on, "pos_pct": rs["sub_pos_pct"], "profile": profile, **subs},
        "banner": banner,
        "inserts": insert_rows,
        "audio": {
            "mutes": mutes,
            "gains": gains,
            "source_keys": source_keys,
            "fx": fx_spans,
            "jl": jl,
            "transition_audio": transition.get("audio"),
            "music": music,
            "sfx": sfx,
            "join_sfx": join_sfx,
        },
        "warnings": warnings,
    }
