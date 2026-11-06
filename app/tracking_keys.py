"""Слежение: камера и раскладка, ключи слоя, проверка «не закрывает» (из ``app/tracking.py``)."""

from __future__ import annotations

import math
from typing import Any

from app.tracking_normalize import (
    PRESETS, TrackError, normalize_offset, normalize_follow, normalize_smooth, smooth_series, simplify
)

TOLERANCE = {"x": 0.0012, "y": 0.0007, "scale": 0.006}  # ≈ 1.3 px кадра 1080×1920
MAX_KEYS = 200  # = app.motion.MAX_KEYS_PER_PROP


COVER_SHARE = 0.25  # слой закрывает больше этой доли лица — предупреждение


# ---------------------------------------------------------------- сглаживание и прореживание


# Доверие к сэмплу трека в сглаживании: найден — как есть, прогноз в пропуске — слабо, потерян — почти нет.
NOISE_BY_STATUS = {"d": 1.0, "w": 8.0, "p": 25.0, "l": 400.0}


# ---------------------------------------------------------------- камера и раскладка


def screen_to_base(x: float, y: float, t: float, camera: list[dict] | None, regions: dict | None,
                   width: int, height: int) -> tuple[float, float]:
    """Точка, как её видно на превью / view_frame в момент ``t`` (с наездом камеры), → точка
    основы ДО камеры. В окошке раскладки (pinned) и вне окна игры камера не действует."""
    if not camera:
        return x, y
    from app.promo_lint import camera_view

    zoom, cx, cy = camera_view(camera, t)
    if zoom <= 1.001:
        return x, y
    px, py = x * width, y * height
    box = {"x": 0, "y": 0, "w": width, "h": height}
    if regions:
        if any(_inside(px, py, r) for r in regions.get("pinned") or []):
            return x, y
        box = regions.get("box") or box
        if not _inside(px, py, box):
            return x, y
    half = 0.5 / zoom
    cx, cy = min(1 - half, max(half, cx)), min(1 - half, max(half, cy))
    sx, sy = (px - box["x"]) / box["w"], (py - box["y"]) / box["h"]
    bx, by = cx + (sx - 0.5) / zoom, cy + (sy - 0.5) / zoom
    return (box["x"] + bx * box["w"]) / width, (box["y"] + by * box["h"]) / height


def _inside(px: float, py: float, rect: dict) -> bool:
    return rect["x"] <= px <= rect["x"] + rect["w"] and rect["y"] <= py <= rect["y"] + rect["h"]


def follows_camera(samples: list[dict], regions: dict | None, width: int, height: int) -> bool:
    """Ехать ли слою с камерой: да, если цель в той части кадра, что двигает наезд."""
    if not samples:
        return True
    xs = sorted(s["x"] + s["w"] / 2 for s in samples)
    ys = sorted(s["y"] + s["h"] / 2 for s in samples)
    px, py = xs[len(xs) // 2] * width, ys[len(ys) // 2] * height
    if not regions:
        return True
    if any(_inside(px, py, r) for r in regions.get("pinned") or []):
        return False
    box = regions.get("box")
    return _inside(px, py, box) if box else True


# ---------------------------------------------------------------- ключи слоя


def _ref_size(fmt: str | None) -> tuple[float, float]:
    from app.formats import ref_size, settings_format

    w, h = ref_size(settings_format(fmt))
    return float(w), float(h)


def track_values(track: dict, layer: dict, offset: dict, follow: dict, smooth: float,
                 fmt: str | None = None) -> dict:
    """Сглаженные значения свойств слоя по сэмплам трека в окне слоя: ``{t: [...], x, y, scale,
    breaks: {индексы последних сэмплов куска}}``."""
    start, end = float(layer["start"]), float(layer["end"])
    samples = [s for s in track.get("samples") or [] if start - 1e-6 <= s["t"] <= end + 1e-6]
    if not samples:
        raise TrackError(f"трек не покрывает слой {start:.2f}–{end:.2f} с")
    ref_w, ref_h = _ref_size(fmt)
    # Сглаживание — по кускам отдельно: на стыке кусков картинка меняется скачком.
    chunks: list[list[int]] = []
    for i, s in enumerate(samples):
        if i and s["piece"] == samples[i - 1]["piece"]:
            chunks[-1].append(i)
        else:
            chunks.append([i])
    ts = [s["t"] for s in samples]
    cx = [s["x"] + s["w"] / 2 for s in samples]
    cy = [s["y"] + s["h"] / 2 for s in samples]
    ws = [s["w"] for s in samples]
    hs = [s["h"] for s in samples]
    out = {"t": ts, "x": [0.0] * len(samples), "y": [0.0] * len(samples), "w": [0.0] * len(samples),
           "h": [0.0] * len(samples), "breaks": {chunk[-1] for chunk in chunks[:-1]}}
    for chunk in chunks:
        sub_t = [ts[i] for i in chunk]
        noise = [NOISE_BY_STATUS.get(samples[i]["s"], 1.0) for i in chunk]
        for name, values, slow in (("x", cx, 1.0), ("y", cy, 1.0), ("w", ws, 2.0), ("h", hs, 2.0)):
            smoothed = smooth_series(sub_t, [values[i] for i in chunk], smooth, slow, noise)
            for i, v in zip(chunk, smoothed):
                out[name][i] = v
    units_w = out["w"] if offset.get("units") == "target" else [1.0] * len(samples)
    units_h = out["h"] if offset.get("units") == "target" else [1.0] * len(samples)
    dx = float(offset.get("dx") or 0.0) / ref_w
    dy = float(offset.get("dy") or 0.0) / ref_h
    out["x"] = [min(3.0, max(-2.0, v + float(offset["x"]) * u + dx)) for v, u in zip(out["x"], units_w)]
    out["y"] = [min(3.0, max(-2.0, v + float(offset["y"]) * u + dy)) for v, u in zip(out["y"], units_h)]
    if follow.get("scale"):
        sizes = [math.sqrt(max(1e-9, w * h)) for w, h in zip(out["w"], out["h"])]
        good = sorted(sz for sz, s in zip(sizes, samples) if s["s"] == "d") or sorted(sizes)
        ref = good[len(good) // 2]
        base = float(layer.get("scale", 1.0) or 1.0)
        out["scale"] = [min(10.0, max(0.05, base * sz / ref)) for sz in sizes]
    return out


def keys_for(values: dict, prop: str, layer_start: float) -> list[dict]:
    """Ключи свойства: опорные точки (Дуглас–Пекер, ≤ 200), ``linear``; последний ключ куска перед
    стыком — ``step`` (слой не «проезжает» через склейку)."""
    ts, vs = values["t"], values[prop]
    breaks = values["breaks"]
    keep = set(breaks) | {b + 1 for b in breaks if b + 1 < len(ts)}
    tol = TOLERANCE[prop]
    idxs = simplify(ts, vs, tol, keep)
    while len(idxs) > MAX_KEYS and tol < 1.0:
        tol *= 1.6
        idxs = simplify(ts, vs, tol, keep)
    if len(idxs) > MAX_KEYS:  # обязательных точек больше предела — равномерно
        step = len(idxs) / MAX_KEYS
        idxs = [idxs[int(k * step)] for k in range(MAX_KEYS)]
    keys: list[dict] = []
    seen: set[float] = set()
    for i in idxs:
        t = round(max(0.0, ts[i] - layer_start), 3)
        if t in seen:
            continue
        seen.add(t)
        keys.append({"t": t, "v": round(vs[i], 4), "ease": "step" if i in breaks else "linear"})
    return keys


def apply_track(layer: dict, track: dict, offset: Any = None, follow: Any = None, smooth: Any = None,
                fmt: str | None = None, preset: str | None = None) -> dict:
    """Слой с ключами трека (замена прежних ключей трекинга) и пометкой ``tracking``.

    Вырезы и стоп-кадры, даже поставленные позже, ключи не ломают: app/cuts.remap_layers у слоя
    с пометкой ставит опоры у краёв разрыва и переводит их вместе с содержимым кадра."""
    offset_n = normalize_offset(offset)
    follow_n = normalize_follow(follow)
    smooth_n = normalize_smooth(smooth)
    fresh = untrack_layer(layer)
    values = track_values(track, fresh, offset_n, follow_n, smooth_n, fmt)
    keys = dict(fresh.get("keys") or {})
    props = [p for p in ("x", "y") if follow_n[p]] + (["scale"] if follow_n["scale"] and "scale" in values else [])
    for prop in props:
        keys[prop] = keys_for(values, prop, float(fresh["start"]))
    fresh["keys"] = keys
    for prop in props:
        if keys[prop]:
            fresh[prop] = keys[prop][0]["v"]  # трек сняли — слой остаётся там, где начинался
    marker = {"target": track.get("target"), "offset": offset_n, "follow": follow_n, "smooth": smooth_n,
              "props": props, "range": [fresh["start"], fresh["end"]], "fps": track.get("fps")}
    if preset in PRESETS:
        marker["preset"] = preset
    fresh["tracking"] = marker
    width, height = (track.get("frame") or {}).get("w") or 1080, (track.get("frame") or {}).get("h") or 1920
    fresh["followCamera"] = follows_camera(track.get("samples") or [], track.get("regions"), width, height)
    return fresh


def untrack_layer(layer: dict) -> dict:
    """Снять трек: ключи, что записал трекинг, и пометку. Положение — с первого ключа трека."""
    fresh = dict(layer)
    marker = fresh.pop("tracking", None)
    if not isinstance(marker, dict):
        return fresh
    keys = dict(fresh.get("keys") or {})
    for prop in marker.get("props") or []:
        points = keys.pop(prop, None)
        if points:
            fresh[prop] = points[0].get("v", fresh.get(prop))
    fresh["keys"] = keys
    return fresh


def preset_offset(name: str | None, layer: dict, fmt: str | None = None) -> tuple[dict, dict, dict]:
    """Отступ, слежение и правка слоя по подсказке ``PRESETS``: у стрелки — поворот и сдвиг на
    полдлины, чтобы кончик смотрел в угол цели, а не центр стрелки лёг на неё."""
    preset = PRESETS.get(str(name or ""))
    if not preset:
        return {}, {}, {}
    offset = dict(preset["offset"])
    follow = dict(preset.get("follow") or {})
    patch: dict = {}
    if preset.get("arrow"):
        props = layer.get("props") or {}
        length = float(props.get("w") or 300) * float(layer.get("scale") or 1.0)
        angle = -45.0
        patch["rotation"] = angle
        reach = length / 2 + 24
        offset["dx"] = round(-reach * math.cos(math.radians(angle)), 1)
        offset["dy"] = round(-reach * math.sin(math.radians(angle)), 1)
    return offset, follow, patch


# ---------------------------------------------------------------- проверка «не закрывает лицо»


def cover_warnings(layer: dict, track: dict, fmt: str | None = None) -> list[str]:
    """Слой над лицом закрывает его — предупреждение (по рамке слоя и рамке цели в сэмплах)."""
    if (track.get("target") or {}).get("kind") != "face":
        return []
    try:
        from app.aspect import layer_size

        size = layer_size(layer)
    except Exception:  # noqa: BLE001 - размер не посчитался — молчим
        size = None
    if not size:
        return []
    ref_w, ref_h = _ref_size(fmt)
    keys = layer.get("keys") or {}
    from app.promo_lint import key_value as interpolate_keys

    start = float(layer["start"])
    covered = 0
    total = 0
    for s in track.get("samples") or []:
        if not start <= s["t"] <= float(layer["end"]):
            continue
        local = s["t"] - start
        x = interpolate_keys(keys.get("x"), local, float(layer.get("x", 0.5)))
        y = interpolate_keys(keys.get("y"), local, float(layer.get("y", 0.5)))
        scale = interpolate_keys(keys.get("scale"), local, float(layer.get("scale", 1.0)))
        lw, lh = size[0] * scale / ref_w, size[1] * scale / ref_h
        ix = max(0.0, min(x + lw / 2, s["x"] + s["w"]) - max(x - lw / 2, s["x"]))
        iy = max(0.0, min(y + lh / 2, s["y"] + s["h"]) - max(y - lh / 2, s["y"]))
        total += 1
        if ix * iy > COVER_SHARE * s["w"] * s["h"]:
            covered += 1
    if total and covered / total > 0.2:
        return [f"слой закрывает лицо в {covered / total:.0%} кадров — сдвинь offset (preset above_head / "
                "under_face) или уменьши слой: лицо должно оставаться видно"]
    return []


def check_times(layer: dict, track: dict) -> list[float]:
    """2–3 момента для проверки кадром: начало, середина и конец слоя, где цель найдена."""
    start, end = float(layer["start"]), float(layer["end"])
    good = [s["t"] for s in track.get("samples") or [] if s["s"] == "d" and start <= s["t"] <= end]
    if not good:
        return [round(start + (end - start) / 2, 2)]
    wanted = [start + (end - start) * f for f in ((0.12, 0.5, 0.88) if end - start >= 1.5 else (0.5,))]
    out = []
    for want in wanted:
        t = min(good, key=lambda g: abs(g - want))
        if all(abs(t - o) > 0.2 for o in out):
            out.append(round(t, 2))
    return out
