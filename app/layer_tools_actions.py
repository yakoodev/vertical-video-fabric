"""Инструменты слоёв: слежение, очистка, кадрирование, глитч, размытие, детали PNGtuber (из ``app/layer_tools.py``)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app import motion
from app.layer_lint import _lint_scene, lint_layers
from app.tool_core import (
    _camera_of, _clip_duration, _current_render, _foreign_asset_problem, _layers_of, _num, _save_render,
    ImageResult, ToolContext, ToolError
)
from app.layer_tools_checks import _layer_problems, _layer_index

def _tracking_presets() -> tuple[str, ...]:
    from app.tracking import PRESETS

    return tuple(PRESETS)


def h_track_layer(ctx: ToolContext, args: dict) -> Any:
    """Слой следует за лицом / объектом / точкой (app/tracking.py): ключи x/y(/scale) с пометкой
    tracking, повторный вызов их заменяет. Ответ — лист из 2–3 кадров с этим слоем: попал ли."""
    from app import tracking

    layer_id = str(args.get("layer_id") or args.get("id") or "")
    if not layer_id:
        raise ToolError("нужен layer_id")
    render = _current_render(ctx)
    layers = _layers_of(render)
    index = _layer_index(layers, layer_id)
    layer = dict(layers[index])
    if args.get("start") is not None or args.get("end") is not None:
        layer["start"] = _num(args.get("start"), layer["start"])
        layer["end"] = _num(args.get("end"), layer["end"])
    try:
        fresh, report = tracking.track_layer(
            ctx.store, ctx.plan_id, layer, args.get("target"), offset=args.get("offset"), follow=args.get("follow"),
            smooth=args.get("smooth"), fps=_num(args.get("fps")), preset=args.get("preset"))
    except tracking.TrackError as exc:
        raise ToolError(f"трек не построен: {exc}", exc.hints) from exc
    normalized = motion.normalize_layers([fresh])[0]
    layers[index] = normalized
    fresh_render = _save_render(ctx, {**render, "layers": layers})
    duration = _clip_duration(ctx)
    report["warnings"] = list(report.get("warnings") or []) + lint_layers(
        _layers_of(fresh_render), duration, only_ids={layer_id}, **_lint_scene(ctx, fresh_render))
    report = {"ok": True, "layer_id": layer_id, "time": "clip", **report,
              "note": "кадры ниже — шкала clip (как слои): слой на цели? Промах — поправь offset или цель "
                      "(target near/index/point) и вызови снова; снять — untrack_layer"}
    if args.get("check") is False:
        return report
    from app.motion_render import contact_sheet_jpeg

    try:
        data = contact_sheet_jpeg(ctx.store, ctx.plan_id, times=report["check_times"], width=300,
                                  before_cuts=True)
    except Exception as exc:  # noqa: BLE001 - нет кадра — отчёт всё равно нужен
        report["check_error"] = f"кадры проверки не собраны: {str(exc)[:200]}"
        return report
    return ImageResult(data=data, mime_type="image/jpeg", info=report)


def h_untrack_layer(ctx: ToolContext, args: dict) -> dict:
    from app import tracking

    layer_id = str(args.get("layer_id") or args.get("id") or "")
    render = _current_render(ctx)
    layers = _layers_of(render)
    index = _layer_index(layers, layer_id)
    if not layers[index].get("tracking"):
        return {"ok": True, "layer_id": layer_id, "note": "у слоя нет трека — снимать нечего"}
    layers[index] = motion.normalize_layers([tracking.untrack_layer(layers[index])])[0]
    _save_render(ctx, {**render, "layers": layers})
    return {"ok": True, "layer_id": layer_id, "layer": layers[index],
            "note": "ключи трека сняты, слой стоит там, где трек начинался"}


def h_clear_layers(ctx: ToolContext, args: dict) -> dict:
    render = _current_render(ctx)
    layers = _layers_of(render)
    camera = _camera_of(render)
    with_camera = bool(args.get("camera", False))
    if not layers and not (with_camera and camera):
        return {"ok": True, "removed": 0}
    update = {**render, "layers": []}
    if with_camera:
        update["camera"] = []
    _save_render(ctx, update)
    return {"ok": True, "removed": len(layers), "camera_cleared": with_camera}


# ---- PNGtuber: аватар владельца, который «говорит» голосом дорожки -------------


def _pngtuber_asset(ctx: ToolContext, value: Any, what: str, required: bool = True) -> str | None:
    """Файл состояния аватара: картинка или анимация (GIF/APNG/webm с альфой) из библиотеки."""
    if value in (None, ""):
        if required:
            raise ToolError(f"нужен {what} — id картинки/GIF из библиотеки (upload_image, list_assets)")
        return None
    try:
        asset = ctx.store.get_montage_asset(int(value))
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolError(f"{what}: файла для монтажа {value!r} нет — id из list_assets или upload_image") from exc
    foreign = _foreign_asset_problem(ctx, asset["id"])
    if foreign:
        raise ToolError(f"{what}: {foreign}")
    if asset.get("kind") not in ("image", "gif", "video"):
        raise ToolError(f"{what}: файл {asset['id']} — {asset.get('kind')}, нужна картинка или GIF")
    return str(asset["id"])


def _pngtuber_track(ctx: ToolContext, args: dict) -> tuple[Any, str]:
    """Дорожка голоса: задана — она; иначе своя дорожка речи источника (speech_tracks), иначе у
    записи с несколькими дорожками — 1 (NVIDIA/OBS: 0 — игра, 1 — микрофон), иначе смесь."""
    from app.source_audio import audio_view, normalize_tracks

    plan = ctx.store.get_clip_plan(ctx.plan_id, include_segments=False)
    try:
        source = ctx.store.get_source(int(plan["source_id"]))
    except (KeyError, TypeError, ValueError):
        source = {}
    total = len((audio_view(source) if source else {}).get("streams") or [])
    if args.get("voice_asset") not in (None, ""):
        return None, "голос — из файла библиотеки (секунда файла = время от начала слоя)"
    track = args.get("track")
    if track not in (None, ""):
        if track == motion.VOICE_SPEECH:
            return track, "голос — дорожки распознавания речи источника (speech_tracks)"
        try:
            index = int(track)
        except (TypeError, ValueError) as exc:
            raise ToolError("track — номер звуковой дорожки (0, 1…) или \"speech\"") from exc
        if total and not 0 <= index < total:
            raise ToolError(f"у записи {total} звуковых дорожек — track {index} нет (0..{total - 1})")
        return index, f"голос — дорожка {index} записи"
    if normalize_tracks(source.get("speech_tracks")):
        return motion.VOICE_SPEECH, f"голос — дорожка речи источника {normalize_tracks(source.get('speech_tracks'))}"
    if total >= 2:
        return 1, "голос — дорожка 1 (у записи несколько дорожек: 0 — игра, 1 — микрофон); другая — track"
    return motion.VOICE_SPEECH, "голос — звук клипа (у записи одна дорожка: игра и голос вместе — рот откроется и на громкую игру)"


def _pngtuber_target(layers: list[dict], args: dict) -> int | None:
    """Какой слой заменить: тот же id; без id — первый PNGtuber клипа (если не allow_multiple)."""
    wanted = str(args.get("id") or "")
    if wanted:
        index = next((i for i, l in enumerate(layers) if str(l.get("id")) == wanted), None)
        if index is not None and layers[index].get("type") != "pngtuber":
            raise ToolError(f"слой {wanted} — {layers[index].get('type')}, не PNGtuber: другой id или update_layer")
        return index
    if args.get("allow_multiple"):
        return None
    return next((i for i, l in enumerate(layers) if l.get("type") == "pngtuber"), None)


def _crop_aspect(args: dict, props: dict, src_w: float, src_h: float) -> float:
    """Пропорция окна: aspect из аргументов; нет — как у нынешнего окна crop; нет и его — 9:16."""
    from app.layer_crop import parse_aspect

    if args.get("aspect") not in (None, ""):
        return parse_aspect(args.get("aspect"))
    crop = props.get("crop") if isinstance(props.get("crop"), dict) else None
    if crop and (crop.get("w", 1) < 0.999 or crop.get("h", 1) < 0.999):
        return (float(crop["w"]) * src_w) / max(1e-6, float(crop["h"]) * src_h)
    return parse_aspect(None)


def h_auto_crop_layer(ctx: ToolContext, args: dict) -> dict:
    """Окно video-слоя по планам: склейки — step, внутри плана — плавно за героем (app/layer_crop).

    Детекция — та же, что у auto_focus кусков исходника (app/autofocus: объект/лица/движение,
    склейки по гистограмме; Gemini по кадру на план — как у auto_focus, если ключ есть и use_vlm
    не false). Склейки уточняются ffmpeg-детектором сцен (app/scene_cuts) по файлу слоя."""
    from app.layer_crop import crop_keys_from_track, crop_window
    from app.montage_assets import probe_asset
    from app.motion import VIDEO_MAX_SPEED, VIDEO_MIN_SPEED, VIDEO_TAIL
    from app.settings import settings

    layer_id = str(args.get("layer_id") or args.get("id") or "")
    render = _current_render(ctx)
    layers = _layers_of(render)
    index = next((i for i, l in enumerate(layers) if str(l.get("id")) == layer_id), None)
    if index is None:
        raise ToolError(f"слоя {layer_id or '?'} нет", [f"есть: {', '.join(str(l.get('id')) for l in layers) or 'ничего'}"])
    layer = layers[index]
    if layer.get("type") != "video":
        raise ToolError(f"слой {layer_id} — {layer.get('type')}, автофокус окна — только у video")
    props = dict(layer.get("props") or {})
    try:
        asset = ctx.store.get_montage_asset(int(props.get("asset_id")))
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolError(f"у слоя {layer_id} нет файла видео (asset_id)") from exc
    path = Path(str(asset.get("file_path") or ""))
    if not path.is_file():
        raise ToolError(f"файла видео {asset['id']} нет на диске")
    src_w, src_h = float(asset.get("width") or 0), float(asset.get("height") or 0)
    file_duration = float(asset.get("duration_sec") or 0)
    if not (src_w and src_h and file_duration):
        meta = probe_asset(path)
        src_w, src_h = float(meta.get("width") or 0), float(meta.get("height") or 0)
        file_duration = float(meta.get("duration_sec") or 0)
    try:
        aspect = _crop_aspect(args, props, src_w, src_h)
        axis, win_w, win_h = crop_window(src_w, src_h, aspect)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    if axis is None:
        raise ToolError(f"видео {src_w:.0f}×{src_h:.0f} уже пропорции окна — вести нечего (aspect другой?)")
    window = win_w if axis == "x" else win_h

    begin = max(0.0, float(props.get("from") or 0.0))
    speed = 1.0 if props.get("hold") else min(VIDEO_MAX_SPEED, max(VIDEO_MIN_SPEED, float(props.get("speed") or 1.0)))
    layer_len = max(0.1, float(layer["end"]) - float(layer["start"]))
    available = max(0.0, file_duration - VIDEO_TAIL - begin) if file_duration else layer_len * speed
    if available <= 0.1:
        raise ToolError(f"from {begin:g} — за концом файла ({file_duration:g} с)")
    span = min(0.6 if props.get("hold") else layer_len * speed, available)
    loop_len = available / speed if props.get("loop") and available < layer_len * speed else None

    resolver = None
    if args.get("use_vlm", True) is not False and settings.gemini_api_key:
        from app.ai.vlm_focus import shots_focus_x

        resolver = shots_focus_x
    from app.autofocus import compute_segment_focus

    mode = "shot" if str(args.get("mode") or "").lower() == "shot" else "follow"
    pseudo = {"local_path": str(path), "focus_preset": args.get("preset") or "balanced", "focus_strategy": mode}
    try:
        points = compute_segment_focus(pseudo, begin, begin + span, strategy=mode, vlm_resolver=resolver)
    except ValueError as exc:
        raise ToolError(f"автофокус не прошёл: {exc}") from exc
    scene: list[float] = []
    if not props.get("hold"):
        from app.scene_cuts import detect_scene_cuts

        scene = [t - begin for t in detect_scene_cuts(str(path)) if begin < t < begin + span]
    if props.get("hold"):
        points = points[:1]
    result = crop_keys_from_track(points, axis=axis, window=window, speed=speed, layer_len=layer_len,
                                  scene_times=scene, loop_len=loop_len)
    keys = dict(layer.get("keys") or {})
    prop = "cropX" if axis == "x" else "cropY"
    other = "cropY" if axis == "x" else "cropX"
    dropped = [name for name in ("cropW", "cropH", other) if keys.pop(name, None)]
    base_x = result["base"] if axis == "x" else 0.0
    base_y = result["base"] if axis == "y" else 0.0
    props["crop"] = {"x": round(base_x, 4), "y": round(base_y, 4), "w": round(win_w, 4), "h": round(win_h, 4)}
    if len(result["keys"]) > 1:
        keys[prop] = result["keys"]
    else:
        keys.pop(prop, None)  # одна рамка на весь слой — хватает props.crop
    layers[index] = motion.normalize_layers([{**layer, "props": props, "keys": keys}])[0]
    fresh = _save_render(ctx, {**render, "layers": layers})
    saved = next((l for l in _layers_of(fresh) if str(l.get("id")) == layer_id), layers[index])
    cuts = sum(1 for k in result["keys"][:-1] if k.get("ease") == "step")
    out: dict = {"ok": True, "layer_id": layer_id, "axis": prop, "window": {"w": win_w, "h": win_h},
                 "keys": len(result["keys"]), "shots": result["shots"], "cuts": cuts,
                 "vlm": resolver is not None, "crop": saved["props"].get("crop"),
                 "note": "проверь contact_sheet по планам; промах — update_layer keys." + prop}
    if dropped:
        out["dropped_keys"] = dropped
    if loop_len:
        out["loop"] = f"ключи повторяются каждые {loop_len:.2f} с (loop)"
    return out


def h_add_glitch(ctx: ToolContext, args: dict) -> dict:
    """Глитч на отрезке основы (app/glitch.py): слой fx стиля со «сбоящей» силой (+ звук из пака)."""
    from app.glitch import GlitchError, build_glitch
    from app.motion_render import resolve_pack_ref

    duration = _clip_duration(ctx)
    try:
        res = build_glitch(args.get("at"), args.get("duration"), args.get("style"), args.get("intensity"),
                           sfx=args.get("sfx", False), sfx_volume=args.get("sfx_volume"), speed=args.get("speed"),
                           seed=args.get("seed"), clip_duration=duration)
    except GlitchError as exc:
        raise ToolError(f"глитч не поставлен: {exc}") from exc
    render = _current_render(ctx)
    current = _layers_of(render)
    taken = {str(l.get("id")) for l in current}
    fx = res["fx"]
    fx["id"] = motion.new_layer_id(taken)
    taken.add(fx["id"])
    over = bool(args.get("over_layers"))
    if over:
        # Над всеми слоями: титры и плашки тоже сбоят.
        fx["track"] = min(motion.MAX_TRACK, max([int(_num(l.get("track"), 0) or 0) for l in current] + [0]) + 1)
    notes: list[str] = []
    audio = res["audio"]
    if audio is not None:
        if resolve_pack_ref(audio["props"]["asset_id"]) is None:
            notes.append(f"звука {audio['props']['asset_id']} нет в паке — глитч без звука")
            audio = None
        else:
            audio["id"] = motion.new_layer_id(taken)
    new = [fx] + ([audio] if audio else [])
    problems = [p for i, layer in enumerate(new) for p in _layer_problems(ctx, layer, i, duration)]
    if problems:
        raise ToolError("глитч не поставлен", problems)
    if len(current) + len(new) > motion.MAX_LAYERS:
        raise ToolError(f"в клипе уже {len(current)} слоёв, предел {motion.MAX_LAYERS}")
    added = motion.normalize_layers(new)
    # Под слоями — первым в списке на дорожке 0: эффект берёт только видео (и фон), титры поверх чистые.
    layers = (current + added[:1] if over else added[:1] + current) + added[1:]
    fresh = _save_render(ctx, {**render, "layers": layers})
    t0, t1 = res["range"]
    out = {"ok": True, "id": fx["id"], "style": res["style"], "range": res["range"],
           "sfx": audio["props"]["asset_id"] if audio else None, "sfx_id": audio["id"] if audio else None,
           "over_layers": over,
           "check": f"view_frame на {t0 + min(0.05, (t1 - t0) * 0.1) + 0.01:.2f} с (пик) и на {(t0 + t1) / 2:.2f} с",
           "warnings": lint_layers(_layers_of(fresh), duration, only_ids={fx["id"]})}
    if notes:
        out["notes"] = notes
    return out


def h_set_motion_blur(ctx: ToolContext, args: dict) -> dict:
    """Motion blur клипа по умолчанию (render.motion_blur): слоям и ключам камеры без своего motionBlur."""
    raw = 0 if args.get("reset") else args.get("value", args.get("shutter", args.get("motion_blur")))
    if raw is None:
        raise ToolError("нужен value — затвор 0..1 (0.5 — киношные 180°; 0 — выключить)")
    value = motion.normalize_motion_blur(raw)
    if raw not in (0, 0.0, "0") and value is None:
        raise ToolError("value — число 0..1 (0 — выключить)")
    render = _current_render(ctx)
    fresh = _save_render(ctx, {**render, "motion_blur": value})
    layers = [l for l in _layers_of(fresh) if l.get("type") not in ("audio", "fx")]
    own = [str(l.get("id")) for l in layers if l.get("motionBlur") is not None]
    out = {"ok": True, "motion_blur": fresh.get("motion_blur"),
           "note": ("motion blur выключен: слои и камера резкие (кроме своих motionBlur)" if not value else
                    f"затвор {value:g}: смазываются только быстрые движения (въезды, whip, вращение, наезды "
                    "камеры); стоящий кадр не меняется. Проверь view_frame посреди быстрого движения")}
    if own:
        out["own"] = f"свой motionBlur у слоёв: {', '.join(own[:12])} — у них остаётся свой"
    return out
