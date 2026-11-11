"""Инструменты камеры: ключи, пресеты, предупреждения (из ``app/layer_tools.py``)."""

from __future__ import annotations

from app import motion
from app.tool_core import (
    _camera_of, _clip_duration, _current_render, _layers_of, _num, _save_render, ToolContext, ToolError
)

PUNCH_LEAD = 0.2      # наезд-удар: от 1.0 до пика за столько секунд до слова
PUNCH_HOLD = 0.8      # держать пик
PUNCH_RELEASE = 0.35  # и вернуть к 1.0 за столько
PUNCH_ZOOM = 1.15
SLOW_ZOOM_MIN = 1.1   # переход к такому zoom дольше SLOW_ZOOM_SEC — медленный наезд, не удар
SLOW_ZOOM_SEC = 2.0


def punch_keys(times: list[float], zoom: float = PUNCH_ZOOM, hold: float = PUNCH_HOLD) -> list[dict]:
    """Ключи наезда-удара на каждый момент ``t``: 1.0 за PUNCH_LEAD до t → ``zoom`` на t (резко, outCubic) →
    держать ``hold`` → вернуть к 1.0 за PUNCH_RELEASE (QA 02.11: «наезд на панчлайн» вышел плавным 0→28 с)."""
    out: list[dict] = []
    for t in sorted(times):
        lead = max(0.0, t - PUNCH_LEAD)
        out += [{"t": round(lead, 3), "zoom": 1.0, "ease": "outCubic"},
                {"t": round(max(t, lead + 0.05), 3), "zoom": round(zoom, 3), "ease": "linear"},
                {"t": round(t + hold, 3), "zoom": round(zoom, 3), "ease": "inOutCubic"},
                {"t": round(t + hold + PUNCH_RELEASE, 3), "zoom": 1.0}]
    return out


def slow_zoom_warnings(camera: list[dict]) -> list[str]:
    """Переход к zoom ≥ SLOW_ZOOM_MIN дольше SLOW_ZOOM_SEC — это медленный наезд через полролика, а не удар."""
    out = []
    for left, right in zip(camera, camera[1:]):
        z0, z1 = _num(left.get("zoom"), 1.0) or 1.0, _num(right.get("zoom"), 1.0) or 1.0
        t0, t1 = float(left.get("t") or 0.0), float(right.get("t") or 0.0)
        if z1 >= SLOW_ZOOM_MIN and z1 - z0 >= 0.05 and t1 - t0 > SLOW_ZOOM_SEC:
            out.append(f"наезд к {z1:g}× тянется {t1 - t0:.1f} с ({t0:g}→{t1:g} с) — это медленный наезд, удара на "
                       f"{t1:g} с не будет. Панч: ключ zoom {z0:g} за {PUNCH_LEAD:g} с до слова, zoom {z1:g} на слове, "
                       f"держать {PUNCH_HOLD:g} с, вернуть — или set_camera punch_at: {t1:g}")
    return out


def h_set_camera(ctx: ToolContext, args: dict) -> dict:
    keys = args.get("keys")
    punch = args.get("punch_at")
    if punch is not None:
        # Помощник: наезд-удар на секунды punch_at (число или список), поверх keys (их ключи в окне удара — прочь).
        times = punch if isinstance(punch, list) else [punch]
        if not times or any(_num(t) is None for t in times):
            raise ToolError("punch_at — секунда шкалы clip (или список секунд), где ударить наездом")
        zoom = _num(args.get("punch_zoom"), PUNCH_ZOOM) or PUNCH_ZOOM
        hold = _num(args.get("punch_hold"), PUNCH_HOLD) or PUNCH_HOLD
        if not 1.0 < zoom <= 1.6:
            raise ToolError("punch_zoom — пик наезда 1.05..1.6 (1.15 — заметно, не мылит)")
        times = [float(_num(t)) for t in times]
        windows = [(t - PUNCH_LEAD - 0.01, t + hold + PUNCH_RELEASE + 0.01) for t in times]
        base = keys if isinstance(keys, list) else _camera_of(_current_render(ctx))
        keys = [k for k in base if not (isinstance(k, dict) and _num(k.get("t")) is not None
                                        and any(a <= float(_num(k.get("t"))) <= b for a, b in windows))]
        keys += punch_keys(times, zoom, max(0.2, hold))
    if not isinstance(keys, list):
        raise ToolError("нужен список keys (пустой — убрать все наезды) или punch_at")
    blur = args.get("motionBlur", args.get("motion_blur"))
    if blur is not None:
        # Затвор всем ключам без своего (motion blur наезда на отрезке от ключа).
        keys = [{**k, "motionBlur": blur} if isinstance(k, dict) and k.get("motionBlur") is None
                and k.get("motion_blur") is None else k for k in keys]
    duration = _clip_duration(ctx)
    below = [k for k in keys if isinstance(k, dict) and (_num(k.get("zoom"), 1.0) or 1.0) < 1.0]
    if below:
        # Камера только приближает окно 9:16 — «отъехать» и показать больше исходника она не может.
        raise ToolError("zoom < 1 нельзя: камера только приближает кадр 9:16 (1..4)", [
            "двое по краям широкого плана не влезают в окно — set_piece_fit(piece, w 0.5–1): кусок шире окна, "
            "сверху и снизу фон; или фокус на говорящего со сменой на склейке/реплике (set_focus с cut:true)"])
    problems = motion.validate_camera(keys, duration)
    if problems:
        raise ToolError("камера не принята", problems)
    keys = _anchor_zoom_keys(ctx, keys)
    clean = motion.normalize_camera(keys)
    render = _current_render(ctx)
    fresh = _save_render(ctx, {**render, "camera": clean})
    camera = _camera_of(fresh)
    warnings = []
    for key in camera:
        if (_num(key.get("zoom"), 1.0) or 1.0) > 1.6:
            warnings.append(f"наезд {key.get('zoom')}× на {key.get('t')} с — сильнее 1.6× мылит картинку")
    warnings += slow_zoom_warnings(camera)
    out = {"ok": True, "camera": camera, "dropped": len(keys) - len(camera), "warnings": warnings}
    if punch is not None:
        out["punch"] = {"at": times, "zoom": zoom, "check": f"view_frame на {times[0]:g} с — пик наезда"}
    mode = (fresh.get("layout") or {}).get("mode")
    if camera and mode in ("pip_cam", "split"):
        # Компоновщик зумит только окно игры (app/stream_layout.camera_regions).
        out["note"] = ("раскладка " + mode + ": камера двигает только игру — окошко pip / полоса вебки стоят "
                       "на месте; x/y камеры — доли окна игры. Проверь view_frame на пике наезда")
    return out


def h_camera_preset(ctx: ToolContext, args: dict) -> dict:
    """3D-движение камеры одной командой (app/camera3d.py): ключи на отрезке start..start+duration."""
    from app.camera3d import PresetError, apply_preset

    render = _current_render(ctx)
    try:
        res = apply_preset(args.get("preset"), args.get("start"), args.get("duration"), args.get("intensity"),
                           camera=render.get("camera"), layers=render.get("layers"), layer_id=args.get("layer_id"),
                           clip_duration=_clip_duration(ctx),
                           motion_blur=args.get("motionBlur", args.get("motion_blur")),
                           layer_ids=args.get("layer_ids"), layer_prefix=args.get("layer_prefix"),
                           point={"x": args.get("x"), "y": args.get("y")}, zoom=args.get("zoom"))
    except (PresetError, ValueError) as exc:
        raise ToolError(f"пресет не поставлен: {exc}", getattr(exc, "hints", [])) from exc
    fresh = _save_render(ctx, {**render, "camera": res["camera"], "layers": res["layers"]})
    out = {"ok": True, "preset": res["preset"], "range": res["range"], "keys": res["keys"],
           "camera_keys": len(_camera_of(fresh)),
           "check": f"view_frame на {res['range'][0] + (res['range'][1] - res['range'][0]) / 2:.2f} с и на конце"}
    if res["note"]:
        out["note"] = res["note"]
    return out


def _anchor_zoom_keys(ctx: ToolContext, keys: list) -> list:
    """Наезд без своих x/y — в героя: край героя со стороны текста стоит на месте,
    и при zoom он не заезжает под субтитры (по умолчанию камера зумит в центр кадра)."""
    if not any(isinstance(k, dict) and "x" not in k and "y" not in k for k in keys):
        return keys
    try:
        from app.ai.subject_box import camera_center, subject_box, text_positions, zoom_anchor
        from app.auto_edit import caption_y_for

        box = subject_box(ctx.store, ctx.plan_id)
        render = _current_render(ctx)
        caption_y = next((float(l.get("y", 0.7)) for l in _layers_of(render) if l.get("type") == "captions"), None)
        if caption_y is None:
            caption_y = text_positions(box, caption_y_for(render))["captions_y"]
        anchor = zoom_anchor(box, caption_y)
    except Exception:  # noqa: BLE001 - без зрения — как просили
        return keys
    if not anchor:
        return keys
    out = []
    for key in keys:
        if isinstance(key, dict) and "x" not in key and "y" not in key:
            zoom = _num(key.get("zoom"), 1.0) or 1.0
            if str(key.get("anchor") or "") == "in_place":
                key = {**key, "x": round(anchor["x"], 4), "y": round(anchor["y"], 4)}  # точка наезда — сам якорь
            else:
                key = {**key, **camera_center(anchor, zoom)}
        out.append(key)
    return out
