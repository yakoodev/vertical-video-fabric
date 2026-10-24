"""Автофокус video-слоя: дорожка «где герой» → окно props.crop и ключи cropX/cropY.

Кусок чужого ролика 4:3/16:9 во весь вертикальный кадр (мем, клип 582) надо вести
окном 9:16 по планам: на склейке окно прыгает, внутри плана — плавно идёт за героем.
Раньше 23 ключа cropX ставились руками по листам кадров.

Детекция — та же, что у auto_focus кусков исходника (app/autofocus.compute_segment_focus:
лица/объект/движение, склейки по гистограмме, Gemini по кадру на план — если включено).
Здесь — только чистая часть без cv2: окно по пропорциям, ключи из дорожки (упрощение
Рамера — Дугласа — Пекера внутри плана, step на склейке, предел точек на свойство).
"""

from __future__ import annotations

import math
from typing import Any

from app.motion import MAX_KEYS_PER_PROP

ASPECTS = {"9:16": 9 / 16, "1:1": 1.0, "4:5": 4 / 5, "3:4": 3 / 4, "16:9": 16 / 9, "4:3": 4 / 3}
DEFAULT_ASPECT = "9:16"
# Внутри плана окно не дёргается на мелочь: разброс меньше этого (доля исходника) — одна рамка.
DEADBAND = 0.03
# Допуск упрощения дорожки (доля исходника): отклонение окна от точной дорожки, которое не видно.
TOLERANCE = 0.015
# Сколько окно должно быть уже исходника, чтобы вести его ключами (иначе — вписать как есть).
MIN_SLACK = 0.01


def parse_aspect(value: Any) -> float:
    """«9:16», «1:1», «4:5», «16:9»… или число w/h → пропорция окна (ширина / высота)."""
    if value is None or value == "":
        return ASPECTS[DEFAULT_ASPECT]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
    else:
        text = str(value).strip()
        if text in ASPECTS:
            return ASPECTS[text]
        if ":" in text:
            a, _, b = text.partition(":")
            try:
                number = float(a) / float(b)
            except (ValueError, ZeroDivisionError) as exc:
                raise ValueError(f"aspect {value!r}: нужен вид «9:16» или число ширина/высота") from exc
        else:
            try:
                number = float(text)
            except ValueError as exc:
                raise ValueError(f"aspect {value!r}: нужен вид «9:16» или число ширина/высота") from exc
    if not math.isfinite(number) or not 0.1 <= number <= 10:
        raise ValueError(f"aspect {value!r}: пропорция вне 0.1..10")
    return number


def crop_window(src_w: float, src_h: float, aspect: float) -> tuple[str | None, float, float]:
    """Окно пропорции ``aspect`` во всю высоту (исходник шире) или во всю ширину (выше).

    → (ось ключей "x"/"y" или None — исходник уже такой пропорции, w, h в долях исходника)."""
    if src_w <= 0 or src_h <= 0:
        raise ValueError("неизвестен размер кадра видео")
    src = src_w / src_h
    if src > aspect * (1 + MIN_SLACK):
        return "x", round(aspect / src, 4), 1.0
    if src < aspect / (1 + MIN_SLACK):
        return "y", 1.0, round(src / aspect, 4)
    return None, 1.0, 1.0


def _rdp(points: list[tuple[float, float]], tolerance: float) -> list[int]:
    """Индексы вершин упрощённой ломаной (Рамер — Дуглас — Пекер, отклонение по значению)."""
    if len(points) <= 2:
        return list(range(len(points)))
    keep = {0, len(points) - 1}
    stack = [(0, len(points) - 1)]
    while stack:
        lo, hi = stack.pop()
        (t0, v0), (t1, v1) = points[lo], points[hi]
        worst, worst_i = -1.0, -1
        for i in range(lo + 1, hi):
            t, v = points[i]
            line = v0 + (v1 - v0) * ((t - t0) / (t1 - t0) if t1 > t0 else 0.0)
            err = abs(v - line)
            if err > worst:
                worst, worst_i = err, i
        if worst > tolerance and worst_i > 0:
            keep.add(worst_i)
            stack += [(lo, worst_i), (worst_i, hi)]
    return sorted(keep)


def _smooth(values: list[float]) -> list[float]:
    """Лёгкое сглаживание (окно 3) — дорожка уже без выбросов (медиана autofocus)."""
    if len(values) < 3:
        return list(values)
    out = [values[0]]
    for i in range(1, len(values) - 1):
        out.append((values[i - 1] + 2 * values[i] + values[i + 1]) / 4)
    out.append(values[-1])
    return out


def _snap_cut(t_prev: float, t_cut: float, scene_times: list[float]) -> float:
    """Где на самом деле склейка между соседними пробами: ffmpeg-склейка в (t_prev, t_cut],
    иначе середина промежутка (проба t_prev — ещё старый план, t_cut — уже новый)."""
    inside = [s for s in scene_times if t_prev < s <= t_cut + 1e-6]
    if inside:
        return max(inside)
    return (t_prev + t_cut) / 2


def track_shots(points: list[dict], axis: str, window: float, speed: float, layer_len: float,
                scene_times: list[float] | None = None) -> list[dict]:
    """Дорожка autofocus (t — секунды файла от from, x/y — центр героя) → планы во времени слоя:
    ``[{t0, samples: [(t, v)]}]``, v — левый/верхний край окна (0..1 − window)."""
    speed = max(1e-3, float(speed or 1.0))
    scene = sorted(float(s) for s in scene_times or [])
    hi = max(0.0, 1.0 - window)
    shots: list[dict] = []
    prev_t: float | None = None
    for point in sorted(points or [], key=lambda p: float(p.get("t", 0))):
        try:
            t_file = float(point["t"])
            centre = float(point.get(axis, 0.5))
        except (KeyError, TypeError, ValueError):
            continue
        t = t_file / speed
        if t > layer_len + 1e-6:
            break
        v = min(hi, max(0.0, centre - window / 2))
        if not shots or (point.get("cut") and prev_t is not None):
            t0 = 0.0 if not shots else _snap_cut(prev_t, t_file, scene) / speed
            shots.append({"t0": round(t0, 3), "samples": []})
        shots[-1]["samples"].append((max(t, shots[-1]["t0"]), v))
        prev_t = t_file
    return shots


def _shot_keys(shot: dict, tolerance: float, deadband: float) -> list[tuple[float, float]]:
    samples = shot["samples"]
    times = [t for t, _v in samples]
    values = _smooth([v for _t, v in samples])
    if max(values) - min(values) < deadband:
        # Герой стоит — одна рамка на план (медиана), без дрожи.
        return [(shot["t0"], sorted(values)[len(values) // 2])]
    pts = list(zip(times, values))
    pts[0] = (shot["t0"], pts[0][1])  # план начинается ровно со склейки
    return [pts[i] for i in _rdp(pts, tolerance)]


def crop_keys_from_track(points: list[dict], *, axis: str, window: float, speed: float = 1.0,
                         layer_len: float, scene_times: list[float] | None = None,
                         max_keys: int = MAX_KEYS_PER_PROP, tolerance: float = TOLERANCE,
                         deadband: float = DEADBAND, loop_len: float | None = None) -> dict:
    """Ключи окна слоя (cropX при axis "x", cropY при "y") по дорожке героя.

    На склейке — ключ ровно на момент склейки, а у последнего ключа плана ``ease: step``
    (держать и прыгнуть); внутри плана — вершины упрощённой дорожки с ``linear``.
    Точек больше ``max_keys`` — допуск растёт, пока не влезет (в крайнем случае ключ на план).
    ``loop_len`` — слой с loop длиннее файла: ключи повторяются каждые ``loop_len`` секунд слоя.

    → {"keys": [{t, v, ease}], "base": v в начале, "shots": планов, "tolerance": итоговый допуск}.
    """
    shots = track_shots(points, axis, window, speed, layer_len, scene_times)
    if not shots:
        return {"keys": [], "base": round(max(0.0, (1.0 - window) / 2), 4), "shots": 0, "tolerance": tolerance}
    tol = tolerance
    while True:
        keys: list[dict] = []
        for index, shot in enumerate(shots):
            vertices = _shot_keys(shot, tol, deadband)
            last = index == len(shots) - 1
            for k, (t, v) in enumerate(vertices):
                ease = "step" if (k == len(vertices) - 1 and not last) else "linear"
                if keys and t <= keys[-1]["t"] + 1e-3:
                    keys[-1] = {"t": keys[-1]["t"], "v": round(v, 4), "ease": ease}
                    continue
                keys.append({"t": round(t, 3), "v": round(v, 4), "ease": ease})
        if loop_len and loop_len > 0.1 and keys:
            period = list(keys)
            period[-1] = {**period[-1], "ease": "step"}  # стык петли — как склейка
            keys = list(period)
            shift = loop_len
            while shift < layer_len - 1e-3 and len(keys) <= max_keys * 4:
                for key in period:
                    t = round(key["t"] + shift, 3)
                    if t >= layer_len:
                        break
                    keys.append({**key, "t": t})
                shift += loop_len
        if len(keys) <= max_keys or tol > 1.0:
            break
        tol *= 1.6
    if len(keys) > max_keys:
        keys = keys[:max_keys]
        keys[-1] = {**keys[-1], "ease": "linear"}
    return {"keys": keys, "base": keys[0]["v"] if keys else 0.0, "shots": len(shots), "tolerance": round(tol, 4)}
