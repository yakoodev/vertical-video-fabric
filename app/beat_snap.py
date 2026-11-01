"""Привязка к долям: начала слоёв, склейки кусков и ключи — на ближайшую долю (или сильную долю)
музыки клипа одной командой.

Сетка долей — ``audio_map.music_beats`` (трек ``set_music``, секунды ГОТОВОГО ролика: доля —
``first_beat + k·period``, сильная — ``first_downbeat + 4k·period``), без музыки — доли звука
самого клипа (``audio_map.beats``, шкала clip), либо своя сетка агента (``bpm`` + ``first_beat``)
или список моментов ``beats``.

Что двигается (``targets``):

* ``layers`` — начало слоя (длина та же: конец едет вместе с ним). Не трогаются субтитры, чат,
  PNGtuber (идут за речью и чатом), запертые слои и слои с самого начала ролика;
* ``keys`` — ключевые кадры слоёв (время слоя → секунды ролика → доля → обратно), порядок ключей
  сохраняется, два ключа на одну долю не съезжаются;
* ``camera`` — ключи камеры;
* ``joins`` — склейки кусков: конец куска удлиняется/укорачивается так, чтобы стык (середина
  наплыва) лёг на долю; всё после стыка едет вместе с ним (слои, субтитры — ``apply_spec``).

Дальше ``tolerance`` от доли — не трогаем (это не «чуть мимо», а другой момент). Сетка
``grid``: ``beat`` — каждая доля, ``downbeat`` — сильные (раз в такт), ``half`` — восьмые.
Сдвиг считается в шкале сетки (final или clip) — вырезы и наплывы учтены часами клипа
(``app/timebase.ClipClock``).
"""

from __future__ import annotations

import bisect
import math
from typing import Callable

GRIDS = ("beat", "downbeat", "half")
TARGETS = ("layers", "keys", "camera", "joins")
DEFAULT_TOLERANCE = 0.15
MAX_TOLERANCE = 0.5
# Слои, что идут за речью/чатом или живут весь ролик, к долям не двигаем.
SKIP_TYPES = ("captions", "chat", "pngtuber")
MIN_PIECE_SEC = 0.5
EPS = 1e-6


def grid_times(grid: dict, duration: float, kind: str = "beat") -> list[float]:
    """Моменты сетки ``kind`` на отрезке [0, duration] (секунды шкалы сетки)."""
    period = float(grid.get("period") or 0) or (60.0 / float(grid["bpm"]) if grid.get("bpm") else 0.0)
    if period <= 0.05:
        raise ValueError("у сетки нет периода (bpm)")
    first = float(grid.get("first_beat") or 0.0)
    if kind == "downbeat":
        every = int(grid.get("downbeat_every") or 4)
        first = float(grid.get("first_downbeat", first))
        period *= every
    elif kind == "half":
        period /= 2
    k0 = math.floor((0.0 - first) / period)
    out = []
    k = k0
    while True:
        t = first + k * period
        if t > duration + period:
            break
        if t >= -EPS:
            out.append(round(t, 4))
        k += 1
    return out


def nearest(t: float, beats: list[float], tolerance: float) -> float | None:
    """Ближайшая доля к ``t`` не дальше ``tolerance`` (None — далеко или сетки нет)."""
    if not beats:
        return None
    i = bisect.bisect_left(beats, t)
    best = None
    for j in (i - 1, i):
        if 0 <= j < len(beats) and (best is None or abs(beats[j] - t) < abs(best - t)):
            best = beats[j]
    if best is None or abs(best - t) > tolerance + EPS:
        return None
    return best


class Scale:
    """Перевод «шкала clip ↔ шкала сетки». Сетка в final — через часы клипа, в clip — как есть."""

    def __init__(self, to_grid: Callable[[float], float] | None = None, from_grid: Callable[[float], float] | None = None):
        self.to_grid = to_grid or (lambda t: float(t))
        self.from_grid = from_grid or (lambda t: float(t))

    def snap(self, t_clip: float, beats: list[float], tolerance: float) -> float | None:
        g = self.to_grid(t_clip)
        b = nearest(g, beats, tolerance)
        if b is None or abs(b - g) < 0.004:
            return None
        return round(self.from_grid(b), 3)


def snap_layers(layers: list[dict], beats: list[float], tolerance: float, scale: Scale, *,
                keys: bool = False, starts: bool = True, only: set[str] | None = None,
                min_start: float = 0.05) -> tuple[list[dict], list[dict]]:
    """Слои: начало (длина та же) и/или ключи на доли. → (слои, изменения)."""
    out, changes = [], []
    for layer in layers:
        layer = dict(layer)
        skip = (layer.get("type") in SKIP_TYPES or layer.get("locked")
                or (only is not None and str(layer.get("id")) not in only))
        if skip:
            out.append(layer)
            continue
        start, end = float(layer.get("start") or 0), float(layer.get("end") or 0)
        if starts and start >= min_start:
            new = scale.snap(start, beats, tolerance)
            if new is not None and new >= 0:
                delta = round(new - start, 3)
                layer["start"], layer["end"] = round(start + delta, 3), round(end + delta, 3)
                changes.append({"target": "layer", "id": layer.get("id"), "type": layer.get("type"),
                                "from": round(start, 3), "to": layer["start"], "delta": delta})
                start, end = layer["start"], layer["end"]
        if keys and isinstance(layer.get("keys"), dict):
            fresh_keys = {}
            for prop, points in layer["keys"].items():
                if not isinstance(points, list):
                    fresh_keys[prop] = points
                    continue
                pts = sorted((dict(p) for p in points if isinstance(p, dict)), key=lambda p: float(p.get("t", 0)))
                for n, p in enumerate(pts):
                    t_abs = start + float(p.get("t", 0))
                    new = scale.snap(t_abs, beats, tolerance)
                    if new is None:
                        continue
                    local = round(new - start, 3)
                    lo = float(pts[n - 1]["t"]) + 0.01 if n > 0 else 0.0
                    hi = float(pts[n + 1]["t"]) - 0.01 if n + 1 < len(pts) else end - start
                    if not lo - EPS <= local <= hi + EPS:
                        continue  # ключ ушёл бы за соседа или за слой — оставляем
                    changes.append({"target": "key", "id": layer.get("id"), "prop": prop,
                                    "from": round(t_abs, 3), "to": new, "delta": round(new - t_abs, 3)})
                    p["t"] = local
                fresh_keys[prop] = pts
            layer["keys"] = fresh_keys
        out.append(layer)
    return out, changes


def snap_camera(camera: list[dict], beats: list[float], tolerance: float, scale: Scale) -> tuple[list[dict], list[dict]]:
    keys = sorted((dict(k) for k in camera if isinstance(k, dict)), key=lambda k: float(k.get("t", 0)))
    changes = []
    for n, key in enumerate(keys):
        t = float(key.get("t", 0))
        new = scale.snap(t, beats, tolerance)
        if new is None:
            continue
        lo = float(keys[n - 1]["t"]) + 0.01 if n > 0 else 0.0
        hi = float(keys[n + 1]["t"]) - 0.01 if n + 1 < len(keys) else math.inf
        if not lo - EPS <= new <= hi + EPS:
            continue
        changes.append({"target": "camera", "index": n, "from": round(t, 3), "to": new, "delta": round(new - t, 3)})
        key["t"] = new
    return keys, changes


def describe_grid(grid: dict, kind: str, source: str) -> dict:
    period = float(grid.get("period") or (60.0 / float(grid["bpm"]) if grid.get("bpm") else 0))
    return {"source": source, "grid": kind, "bpm": grid.get("bpm"), "period": round(period, 4),
            "first_beat": grid.get("first_beat"), "first_downbeat": grid.get("first_downbeat"),
            "time": grid.get("time") or ("final" if source == "music" else "clip")}
