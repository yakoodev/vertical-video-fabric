"""Пресеты 3D-движения камеры (docs/motion/spec.md «3D: глубина, перспектива, камера»).

Агент (инструмент ``camera_preset``) и редактор (``POST /api/motion/camera-preset``) ставят
движение одной командой: пресет пишет ключи ``render.camera`` (dolly ``z``, наклон ``rotX``,
облёт ``rotY``, поле зрения ``fov``) на отрезке ``start..start+duration`` — ключи камеры внутри
отрезка заменяются, снаружи остаются; плоский наезд (zoom/x/y) на краях отрезка сохраняется.
``card_flip`` — переворот слоя: ключи ``rotY`` самого слоя. ``screen_tilt`` — экран (группа слоёв: блоки
страницы, макет интерфейса) наклонён в 3D и выпрямляется, пока камера наезжает в точку: всем слоям группы
одна точка ``pivot`` и одни ключи rotX/rotY/scale (как шаблон «Экран с наклоном и наездом», templates/ads2.ts).

Кривые — как у моушн-дизайнеров: мягкие in/out (cubic-bezier), перелёт — только там, где он
читается (раскрытие наклоном, переворот карточки). Движение заканчивается в покое, кроме
наезда и «Хичкока» — они держатся до следующего ключа камеры (как панч-ин).
"""

from __future__ import annotations

import math
from typing import Any

from app.motion import (
    CAMERA_Z_LIMIT,
    DEFAULT_FOV,
    MAX_CAMERA_KEYS,
    MAX_KEYS_PER_PROP,
    normalize_camera,
    normalize_layers,
)

PRESETS = ("parallax_push", "orbit", "dolly_zoom", "tilt_reveal", "float", "card_flip", "screen_tilt")
PRESET_ALIASES = {
    "push": "parallax_push", "parallax": "parallax_push", "push_in": "parallax_push",
    "orbit_light": "orbit", "arc": "orbit", "vertigo": "dolly_zoom", "hitchcock": "dolly_zoom",
    "dolly": "dolly_zoom", "tilt": "tilt_reveal", "reveal": "tilt_reveal", "breathe": "float",
    "drift": "float", "flip": "card_flip", "tilt_zoom": "screen_tilt", "screen": "screen_tilt",
    "ui_tilt": "screen_tilt",
}
# Слои, которые пресет двигает сам (не камера).
LAYER_PRESETS = ("card_flip", "screen_tilt")
# Длина по умолчанию, с, и пределы.
DEFAULT_DURATION = {"parallax_push": 2.0, "orbit": 3.0, "dolly_zoom": 2.0, "tilt_reveal": 1.6, "float": 4.0,
                    "card_flip": 0.9, "screen_tilt": 3.0}
MIN_DURATION = 0.3
MAX_DURATION = 12.0
INTENSITY_RANGE = (0.2, 2.0)
# Кривые: мягкий in/out (синус), мягкий выход (expo-out), перелёт с доводкой.
SOFT = "bezier(0.45,0,0.55,1)"
PUSH = "bezier(0.45,0,0.15,1)"
OUT = "bezier(0.16,1,0.3,1)"
OVERSHOOT = "bezier(0.34,1.45,0.64,1)"
# Фокусное покоя в опорных px (кадр 1920 по вертикали, 40°) — для «Хичкока».
REF_H = 1920.0
FOCAL0 = REF_H / 2 / math.tan(math.radians(DEFAULT_FOV) / 2)

DESCRIPTIONS = {
    "parallax_push": "наезд dolly с параллаксом: ближние слои растут быстрее дальних и видео; держится",
    "orbit": "лёгкий облёт ±10° (×intensity) с подходом и возвратом в покой",
    "dolly_zoom": "эффект Хичкока: камера отъезжает, фокусное растёт — план (z 0) стоит, фон «наезжает»; держится",
    "tilt_reveal": "камера из наклона 16° выпрямляется с лёгким перелётом — раскрытие сцены",
    "float": "медленное «дыхание» камеры в 3D (наклон, облёт, dolly) и возврат в покой",
    "card_flip": "переворот слоя layer_id: из ребра (rotY 90°) к лицу с перелётом",
    "screen_tilt": "группа слоёв (layer_ids или layer_prefix — блоки страницы) наклонена в 3D на 24°·intensity и "
                   "выпрямляется, пока наезжает в точку x/y (zoom, 1.3) — как один экран",
}
SCREEN_TILT_DEG = 24.0
SCREEN_TILT_ZOOM = 1.3


class PresetError(ValueError):
    """Пресет не применён: причина и подсказки."""

    def __init__(self, message: str, hints: list[str] | None = None) -> None:
        super().__init__(message)
        self.hints = hints or []


def preset_name(raw: Any) -> str:
    name = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    name = PRESET_ALIASES.get(name, name)
    if name not in PRESETS:
        raise PresetError(f"пресета {raw!r} нет", [f"есть: {', '.join(PRESETS)}"])
    return name


def _lin(keys: list[dict], t: float, name: str, default: float) -> float:
    """Поле камеры в момент ``t`` (линейно между ключами) — плоский наезд на краях отрезка."""
    pts = sorted((float(k["t"]), float(k.get(name, default))) for k in keys if k.get(name, default) is not None)
    if not pts:
        return default
    if t <= pts[0][0]:
        return pts[0][1]
    if t >= pts[-1][0]:
        return pts[-1][1]
    for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
        if t0 <= t <= t1:
            return v0 + (v1 - v0) * ((t - t0) / (t1 - t0) if t1 > t0 else 1.0)
    return pts[-1][1]


def camera_shape(preset: str, intensity: float) -> list[tuple[float, dict, str | None]]:
    """Ключи пресета: (доля отрезка, 3D-поля, ease к следующему)."""
    i = intensity
    if preset == "parallax_push":
        return [(0.0, {"z": 0, "rotX": 0}, PUSH), (1.0, {"z": round(320 * i, 1), "rotX": round(-2 * i, 2)}, None)]
    if preset == "orbit":
        a = round(10 * i, 2)
        return [
            (0.0, {"rotY": 0, "rotX": 0, "z": 0}, SOFT),
            (0.35, {"rotY": -a, "rotX": round(2 * i, 2), "z": round(90 * i, 1)}, SOFT),
            (0.8, {"rotY": a, "rotX": round(-1.5 * i, 2), "z": round(90 * i, 1)}, SOFT),
            (1.0, {"rotY": 0, "rotX": 0, "z": 0}, None),
        ]
    if preset == "dolly_zoom":
        back = min(CAMERA_Z_LIMIT, 1600 * i)
        fov = math.degrees(2 * math.atan(REF_H / 2 / (FOCAL0 + back)))
        return [(0.0, {"z": 0, "fov": DEFAULT_FOV}, "bezier(0.4,0,0.2,1)"), (1.0, {"z": -round(back, 1), "fov": round(fov, 2)}, None)]
    if preset == "tilt_reveal":
        return [
            (0.0, {"rotX": round(16 * i, 2), "z": round(-120 * i, 1)}, OUT),
            (0.75, {"rotX": round(-1.2 * i, 2), "z": round(10 * i, 1)}, SOFT),
            (1.0, {"rotX": 0, "z": 0}, None),
        ]
    if preset == "float":
        return [
            (0.0, {"rotX": 0, "rotY": 0, "z": 0}, SOFT),
            (0.25, {"rotX": round(1.5 * i, 2), "rotY": round(-2.5 * i, 2), "z": round(40 * i, 1)}, SOFT),
            (0.5, {"rotX": round(-1.0 * i, 2), "rotY": round(2.0 * i, 2), "z": round(70 * i, 1)}, SOFT),
            (0.75, {"rotX": round(1.0 * i, 2), "rotY": round(1.5 * i, 2), "z": round(30 * i, 1)}, SOFT),
            (1.0, {"rotX": 0, "rotY": 0, "z": 0}, None),
        ]
    raise PresetError(f"{preset} — не пресет камеры")


def _soft(p: float) -> float:
    """Кривая SOFT (cubic-bezier 0.45,0,0.55,1) в точке p — как ease движка."""
    p = min(1.0, max(0.0, p))
    x1, y1, x2, y2 = 0.45, 0.0, 0.55, 1.0

    def bez(a: float, b: float, u: float) -> float:
        return 3 * a * u * (1 - u) ** 2 + 3 * b * u * u * (1 - u) + u ** 3

    u = p
    for _ in range(12):
        x = bez(x1, x2, u) - p
        dx = 3 * x1 * (1 - u) ** 2 + 6 * (x2 - x1) * u * (1 - u) + 3 * (1 - x2) * u * u
        if abs(x) < 1e-6 or abs(dx) < 1e-9:
            break
        u = min(1.0, max(0.0, u - x / dx))
    return bez(y1, y2, u)


def screen_tilt_curve(d: float, tilt: float, zoom: float) -> dict[str, list[tuple[float, float]]]:
    """Точки (секунда отрезка, значение) rotX/rotY/scale — как screenTiltKeys в templates/ads2.ts."""
    t1 = min(d * 0.35, 1.4)
    t2 = min(d * 0.8, t1 + 1.6)
    return {
        "rotX": [(0.0, tilt), (t1, tilt * 0.45), (t2, tilt * 0.12)],
        "rotY": [(0.0, -tilt * 0.6), (t1, -tilt * 0.28), (t2, tilt * 0.1)],
        "scale": [(0.0, 0.9), (t1, 1.0), (t2, zoom)],
    }


def _curve_at(points: list[tuple[float, float]], t: float) -> float:
    if t <= points[0][0]:
        return points[0][1]
    for (a, va), (b, vb) in zip(points, points[1:]):
        if t <= b:
            return va + (vb - va) * _soft((t - a) / (b - a) if b > a else 1.0)
    return points[-1][1]


def _screen_tilt(lays: list[dict], targets: list[dict], t0: float, d: float, intensity: float, point: Any,
                 zoom: Any) -> tuple[list[dict], str]:
    """Ключи наклона и наезда группе: одна точка pivot, одни и те же кривые (секунды клипа)."""
    tilt = round(SCREEN_TILT_DEG * intensity, 2)
    z = SCREEN_TILT_ZOOM if zoom in (None, "") else min(3.0, max(1.0, float(zoom)))
    curve = screen_tilt_curve(d, tilt, z)
    if isinstance(point, dict) and point.get("x") is not None and point.get("y") is not None:
        pivot = {"x": round(float(point["x"]), 4), "y": round(float(point["y"]), 4)}
    else:
        pivot = {"x": round(sum(float(l.get("x", 0.5)) for l in targets) / len(targets), 4),
                 "y": round(sum(float(l.get("y", 0.5)) for l in targets) / len(targets), 4)}
    added: list[dict] = []
    for lay in targets:
        start, end = float(lay["start"]), float(lay["end"])
        a, b = max(t0, start), min(t0 + d, end)
        if b - a < 0.05:
            continue
        keys = dict(lay.get("keys") or {})
        base = float(lay.get("scale") or 1.0)
        for prop, pts in curve.items():
            # Точки отрезка в секундах слоя; слой начался позже — первая точка — значение кривой в его начале.
            local = [(round(t0 + t - start, 3), v) for t, v in pts if start <= t0 + t <= end]
            if t0 < start:
                local.insert(0, (0.0, _curve_at(pts, start - t0)))
            k = base if prop == "scale" else 1.0
            fresh = [{"t": t, "v": round(v * k, 4), **({"ease": SOFT} if i < len(local) - 1 else {})}
                     for i, (t, v) in enumerate(local)]
            lo, hi = a - start - 1e-3, b - start + 1e-3
            kept = [p for p in keys.get(prop, []) if not (lo <= p["t"] <= hi)]
            keys[prop] = sorted(kept + fresh, key=lambda p: p["t"])[:MAX_KEYS_PER_PROP]
        lay["keys"] = keys
        lay["pivot"] = pivot
        added.append({"layer": lay["id"], "pivot": pivot})
    if not added:
        raise PresetError(f"ни один слой группы не виден на {t0:g}–{t0 + d:g} с")
    return added, (f"группа из {len(added)} слоёв наклонена на {tilt:g}° и наезжает ×{z:g} в точку "
                   f"x {pivot['x']}, y {pivot['y']}; держится до конца слоёв")


def apply_preset(
    preset: Any,
    start: Any,
    duration: Any = None,
    intensity: Any = None,
    *,
    camera: list | None = None,
    layers: list | None = None,
    layer_id: Any = None,
    clip_duration: float | None = None,
    motion_blur: Any = None,
    layer_ids: Any = None,
    layer_prefix: Any = None,
    point: Any = None,
    zoom: Any = None,
) -> dict:
    """Пресет → {preset, camera, layers, keys (что поставлено), note}. Ничего не сохраняет.
    ``motion_blur`` (затвор 0..1) — у ключей движения: быстрые отрезки смазаны (motionblur.ts);
    None — как у клипа (render.motion_blur)."""
    name = preset_name(preset)
    try:
        t0 = max(0.0, float(start))
    except (TypeError, ValueError):
        raise PresetError("start — число секунд клипа") from None
    d = DEFAULT_DURATION[name] if duration in (None, "") else float(duration)
    if not (MIN_DURATION <= d <= MAX_DURATION):
        raise PresetError(f"duration {d:g} с — нужно {MIN_DURATION:g}..{MAX_DURATION:g}")
    i = 1.0 if intensity in (None, "") else float(intensity)
    i = min(INTENSITY_RANGE[1], max(INTENSITY_RANGE[0], i))
    if clip_duration and t0 >= clip_duration:
        raise PresetError(f"start {t0:g} с — после конца клипа ({clip_duration:.2f} с)")
    if clip_duration:
        d = min(d, max(MIN_DURATION, clip_duration - t0))
    t1 = round(t0 + d, 3)
    cam = normalize_camera(camera or [])
    lays = normalize_layers(layers or [])
    note = ""
    if name == "screen_tilt":
        ids = {str(v) for v in (layer_ids if isinstance(layer_ids, list) else [])}
        if layer_id:
            ids.add(str(layer_id))
        prefix = str(layer_prefix or "")
        targets = [lay for lay in lays if lay["type"] != "audio" and (lay["id"] in ids or (prefix and lay["id"].startswith(prefix)))]
        if not targets:
            raise PresetError("screen_tilt наклоняет группу слоёв — нужны layer_ids или layer_prefix",
                              ["блоки страницы page_to_layers — layer_prefix «wp<id страницы>_»", "id слоёв — get_clip"])
        added, note = _screen_tilt(lays, targets, t0, t1 - t0, i, point, zoom)
        return {"preset": name, "camera": cam, "layers": normalize_layers(lays), "keys": added,
                "range": [t0, t1], "note": note}
    if name == "card_flip":
        target = next((lay for lay in lays if lay["id"] == str(layer_id or "")), None)
        if target is None:
            raise PresetError("card_flip переворачивает слой — нужен layer_id",
                              ["id слоя из get_clip (карточка, плашка, картинка)"])
        local0 = round(max(0.0, t0 - target["start"]), 3)
        local1 = round(min(target["end"] - target["start"], local0 + d), 3)
        if local1 - local0 < MIN_DURATION / 2:
            raise PresetError(f"слой {target['id']} не виден на {t0:g}–{t1:g} с")
        keys = dict(target.get("keys") or {})
        kept = [k for k in keys.get("rotY", []) if not (local0 - 1e-3 <= k["t"] <= local1 + 1e-3)]
        flip = [{"t": local0, "v": 90.0, "ease": OVERSHOOT}, {"t": local1, "v": 0.0}]
        keys["rotY"] = sorted(kept + flip, key=lambda k: k["t"])[:MAX_KEYS_PER_PROP]
        target["keys"] = keys
        return {"preset": name, "camera": cam, "layers": normalize_layers(lays), "keys": flip,
                "range": [t0, t1], "note": "слой повёрнут ребром до начала и встаёт лицом с перелётом"}
    shape = camera_shape(name, i)
    inside = [k for k in cam if t0 - 1e-3 <= k["t"] <= t1 + 1e-3]
    outside = [k for k in cam if k not in inside]
    added = []
    first = shape[0][1]
    if t0 > 0.02 and any(v not in (0, DEFAULT_FOV) for v in first.values()):
        # Пресет начинается не из покоя (раскрытие наклоном): до start камера стоит, на start —
        # прыжок в начальное положение (ключ «step»), а не медленный заход с прошлого ключа.
        th = round(t0 - 0.01, 3)
        added.append({"t": th, "zoom": round(_lin(cam, th, "zoom", 1.0), 4), "x": round(_lin(cam, th, "x", 0.5), 4),
                      "y": round(_lin(cam, th, "y", 0.5), 4), "ease": "step"})
        inside = [k for k in cam if th - 1e-3 <= k["t"] <= t1 + 1e-3]
        outside = [k for k in cam if k not in inside]
    for frac, fields, ease in shape:
        t = round(t0 + d * frac, 3)
        key = {"t": t, "zoom": round(_lin(cam, t, "zoom", 1.0), 4), "x": round(_lin(cam, t, "x", 0.5), 4),
               "y": round(_lin(cam, t, "y", 0.5), 4), **fields}
        if ease:
            key["ease"] = ease
        if motion_blur not in (None, ""):
            key["motionBlur"] = round(min(1.0, max(0.0, float(motion_blur))), 3)
        added.append(key)
    merged = sorted(outside + added, key=lambda k: k["t"])
    if len(merged) > MAX_CAMERA_KEYS:
        raise PresetError(f"ключей камеры стало бы {len(merged)}, предел {MAX_CAMERA_KEYS}")
    if name in ("parallax_push", "orbit", "float") and not any(
            lay.get("z") is not None or lay.get("followCamera") for lay in lays
            if lay["type"] != "audio" and lay["start"] < t1 and lay["end"] > t0):
        note = ("параллакс виден между видео (глубина baseZ 1000) и слоями в сцене — дай слоям z "
                "(−300…600) или followCamera, иначе титры стоят на экране")
    if name in ("parallax_push", "dolly_zoom"):
        note = (note + "; " if note else "") + "движение держится до следующего ключа камеры — верни камеру ключом"
    return {"preset": name, "camera": normalize_camera(merged), "layers": lays, "keys": added,
            "range": [t0, t1], "note": note}


def describe() -> list[dict]:
    """Пресеты для UI и агента: id, что делает, длина по умолчанию."""
    return [{"id": p, "description": DESCRIPTIONS[p], "duration": DEFAULT_DURATION[p], "layer": p in LAYER_PRESETS}
            for p in PRESETS]


__all__ = ["PRESETS", "PresetError", "apply_preset", "camera_shape", "describe", "preset_name"]
