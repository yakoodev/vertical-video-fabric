"""Слежение: нормализация входа, сглаживание, прореживание (из ``app/tracking.py``)."""

from __future__ import annotations

import math
from typing import Any

import numpy as np

KINDS = ("face", "object", "point")
MODES = ("largest", "near", "index")
KIND_ALIASES = {"лицо": "face", "лица": "face", "голова": "face", "head": "face", "объект": "object",
                "obj": "object", "точка": "point", "область": "point", "manual": "point", "region": "point"}
OBJECT_ALIASES = {
    "человек": "person", "люди": "person", "персонаж": "person", "people": "person", "human": "person",
    "машина": "car", "авто": "car", "автомобиль": "car", "собака": "dog", "пёс": "dog", "пес": "dog",
    "кошка": "cat", "кот": "cat", "мяч": "sports ball", "ball": "sports ball", "телефон": "cell phone",
    "phone": "cell phone", "мотоцикл": "motorcycle", "велосипед": "bicycle", "грузовик": "truck",
    "автобус": "bus", "птица": "bird", "лошадь": "horse", "чашка": "cup", "кружка": "cup",
    "бутылка": "bottle", "ноутбук": "laptop", "самолёт": "airplane", "самолет": "airplane",
    "лодка": "boat", "поезд": "train", "стул": "chair", "диван": "couch", "телевизор": "tv",
    "монитор": "tv", "клавиатура": "keyboard", "мышь": "mouse", "книга": "book", "мишка": "teddy bear",
}


FPS_RANGE = (4.0, 15.0)


TRACKED_PROPS = ("x", "y", "scale")


# Подсказки «куда ставить слой» (MCP track_layer preset, кнопки инспектора): отступ в размерах цели
# (``units: target``) и/или в опорных px (``dx``/``dy``). Слой не должен закрывать лицо.
PRESETS: dict[str, dict] = {
    "on_target": {"title": "На цели", "offset": {"x": 0.0, "y": 0.0, "units": "target"}},
    "above_head": {"title": "Над головой (эмодзи на голову)", "offset": {"x": 0.0, "y": -1.05, "units": "target"},
                   "follow": {"scale": True}},
    "under_face": {"title": "Под лицом (плашка с именем)", "offset": {"x": 0.0, "y": 1.2, "units": "target"}},
    "arrow_to": {"title": "Стрелка на цель (снизу слева)", "offset": {"x": -0.6, "y": 0.6, "units": "target"},
                 "arrow": True},
}


class TrackError(ValueError):
    """Понятная причина отказа (цель не найдена / потеряна / формат) + подсказки."""

    def __init__(self, message: str, hints: list[str] | None = None):
        super().__init__(message)
        self.hints = list(hints or [])


# ---------------------------------------------------------------- нормализация входа


def _num(value: Any, default: float | None = None) -> float | None:
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def object_class(name: Any) -> str:
    """«машина» / «car» / 2 → имя класса COCO; иначе TrackError со списком."""
    from app.object_detect import COCO_CLASSES

    if isinstance(name, (int, float)) and not isinstance(name, bool) and 0 <= int(name) < len(COCO_CLASSES):
        return COCO_CLASSES[int(name)]
    text = str(name or "").strip().lower().replace("_", " ")
    text = OBJECT_ALIASES.get(text, text)
    if text in COCO_CLASSES:
        return text
    raise TrackError(f"класс объекта {name!r} неизвестен детектору",
                     ["классы COCO: " + ", ".join(COCO_CLASSES[:30]) + "…",
                      "по-русски: человек, машина, собака, кошка, мяч, телефон"])


def normalize_target(raw: Any) -> dict:
    """``{kind, mode?, class?, index?, x?, y?, t?, w?, h?}`` — что отслеживать.

    * ``face`` / ``object`` (+ ``class``): ``mode`` largest (по умолчанию) — самое крупное; near —
      ближайшее к точке ``x``, ``y`` (доли кадра, как видно на превью в момент ``t``); index —
      ``index``-е слева (0 — самое левое) в момент ``t``.
    * ``point``: точка ``x``, ``y`` в момент ``t`` (шкала clip); ``w``/``h`` — размер области
      (доли ширины/высоты кадра), по умолчанию квадрат 12 % ширины.
    """
    if isinstance(raw, str):
        raw = {"kind": raw}
    if not isinstance(raw, dict):
        raise TrackError("target: нужен объект {kind: face|object|point, …}")
    kind = str(raw.get("kind") or "").strip().lower()
    kind = KIND_ALIASES.get(kind, kind)
    if kind not in KINDS:
        raise TrackError(f"target.kind {raw.get('kind')!r} — нужно face | object | point")
    out: dict = {"kind": kind}
    x, y, t = _num(raw.get("x")), _num(raw.get("y")), _num(raw.get("t"))
    if t is not None:
        out["t"] = round(max(0.0, t), 3)
    if kind == "point":
        if x is None or y is None:
            raise TrackError("target point: нужны x и y — доли кадра (как на превью / view_frame)")
        out["x"], out["y"] = round(min(1.0, max(0.0, x)), 4), round(min(1.0, max(0.0, y)), 4)
        for key in ("w", "h"):
            size = _num(raw.get(key))
            if size is not None:
                out[key] = round(min(0.6, max(0.02, size)), 4)
        return out
    if kind == "object":
        out["class"] = object_class(raw.get("class") or raw.get("label") or raw.get("name") or "person")
    mode = str(raw.get("mode") or "").strip().lower()
    if not mode:
        mode = "index" if raw.get("index") is not None else ("near" if x is not None and y is not None else "largest")
    if mode in ("biggest", "closest", "крупное"):
        mode = "largest"
    if mode not in MODES:
        raise TrackError(f"target.mode {mode!r} — нужно largest | near | index")
    out["mode"] = mode
    if mode == "near":
        if x is None or y is None:
            raise TrackError("target mode near: нужны x и y — точка, к которой ближе цель")
        out["x"], out["y"] = round(min(1.0, max(0.0, x)), 4), round(min(1.0, max(0.0, y)), 4)
    if mode == "index":
        out["index"] = max(0, int(_num(raw.get("index"), 0) or 0))
    return out


def normalize_offset(raw: Any) -> dict:
    """Отступ слоя от центра цели: ``x``/``y`` в ``units`` (frame — доли кадра, target — размеры
    цели) + ``dx``/``dy`` — опорные px (1080 по ширине)."""
    raw = raw if isinstance(raw, dict) else {}
    units = raw.get("units") if raw.get("units") in ("frame", "target") else "frame"
    out = {"x": round(_num(raw.get("x"), 0.0) or 0.0, 4), "y": round(_num(raw.get("y"), 0.0) or 0.0, 4),
           "units": units}
    for key in ("dx", "dy"):
        value = _num(raw.get(key))
        if value:
            out[key] = round(min(3000.0, max(-3000.0, value)), 1)
    return out


def normalize_follow(raw: Any) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    return {"x": raw.get("x", True) is not False, "y": raw.get("y", True) is not False,
            "scale": bool(raw.get("scale", False))}


DEFAULT_SMOOTH = 0.5


def normalize_smooth(value: Any) -> float:
    value = _num(value, DEFAULT_SMOOTH) if value is not None else DEFAULT_SMOOTH
    return round(min(1.0, max(0.0, value)), 3)


def normalize_tracking(raw: Any) -> dict | None:
    """Пометка ``layer.tracking`` (что записал трекинг) — для нормализации слоёв (app/motion.py)."""
    if not isinstance(raw, dict):
        return None
    try:
        target = normalize_target(raw.get("target"))
    except (TrackError, ImportError):
        return None
    props = [p for p in raw.get("props") or [] if p in TRACKED_PROPS]
    out: dict = {"target": target, "offset": normalize_offset(raw.get("offset")),
                 "follow": normalize_follow(raw.get("follow")), "smooth": normalize_smooth(raw.get("smooth")),
                 "props": props}
    rng = raw.get("range")
    if isinstance(rng, (list, tuple)) and len(rng) == 2 and all(_num(v) is not None for v in rng):
        out["range"] = [round(float(rng[0]), 3), round(float(rng[1]), 3)]
    fps = _num(raw.get("fps"))
    if fps:
        out["fps"] = round(min(FPS_RANGE[1], max(FPS_RANGE[0], fps)), 2)
    if isinstance(raw.get("preset"), str) and raw["preset"] in PRESETS:
        out["preset"] = raw["preset"]
    return out


def smooth_series(ts: list[float], vs: list[float], smooth: float, slow: float = 1.0,
                  noise: list[float] | None = None) -> list[float]:
    """Калман с постоянной скоростью + сглаживание Рауха–Тунга–Штрибеля (проход назад).

    Офлайн-трек: смотрим и в прошлое, и в будущее — запаздывания нет ни в середине, ни у краёв
    (у краёв модель скорости продолжает траекторию, а не тянет её к середине, как среднее двух
    проходов One Euro). ``smooth`` 0 — как есть, 0.5 — полоса ≈ 1 Гц (дрожь детектора 3–5 Гц
    гасится, поворот головы проходит), 1 — ≈ 0.3 Гц. ``slow`` > 1 — ещё мягче (размер цели).
    ``noise`` — множитель шума измерения сэмпла (пропуски и потери тянут слабее)."""
    n = len(vs)
    if smooth <= 0 or n < 3:
        return list(vs)
    ratio = 10 ** (5.5 - 4.4 * smooth) / slow ** 4  # q/r: полоса ~ ratio^(1/4) рад/с
    noise = noise or [1.0] * n
    xf = np.zeros((n, 2))
    pf = np.zeros((n, 2, 2))
    xp = np.zeros((n, 2))
    pp = np.zeros((n, 2, 2))
    x = np.array([vs[0], 0.0])
    p = np.diag([noise[0], 1e6])
    mats: list[np.ndarray] = [np.eye(2)]
    for i in range(n):
        if i:
            dt = max(1e-3, ts[i] - ts[i - 1])
            f = np.array([[1.0, dt], [0.0, 1.0]])
            q = ratio * np.array([[dt ** 3 / 3, dt ** 2 / 2], [dt ** 2 / 2, dt]])
            x = f @ x
            p = f @ p @ f.T + q
            mats.append(f)
        xp[i], pp[i] = x, p
        s = p[0, 0] + noise[i]
        k = p[:, 0] / s
        x = x + k * (vs[i] - x[0])
        p = p - np.outer(k, p[0, :])
        xf[i], pf[i] = x, p
    xs = xf.copy()
    for i in range(n - 2, -1, -1):
        f = mats[i + 1]
        c = pf[i] @ f.T @ np.linalg.inv(pp[i + 1])
        xs[i] = xf[i] + c @ (xs[i + 1] - xp[i + 1])
    return [float(v) for v in xs[:, 0]]


def simplify(ts: list[float], vs: list[float], tol: float, keep: set[int] | None = None) -> list[int]:
    """Дуглас–Пекер по отклонению значения от хорды: индексы опорных точек (``keep`` — обязательные)."""
    n = len(ts)
    if n <= 2:
        return list(range(n))
    anchors = sorted({0, n - 1, *(keep or set())})
    chosen: set[int] = set(anchors)
    for lo, hi in zip(anchors, anchors[1:]):
        stack = [(lo, hi)]
        while stack:
            a, b = stack.pop()
            if b - a < 2:
                continue
            ta, tb, va, vb = ts[a], ts[b], vs[a], vs[b]
            worst, where = -1.0, -1
            for i in range(a + 1, b):
                f = (ts[i] - ta) / (tb - ta) if tb > ta else 0.0
                err = abs(vs[i] - (va + (vb - va) * f))
                if err > worst:
                    worst, where = err, i
            if worst > tol:
                chosen.add(where)
                stack.append((a, where))
                stack.append((where, b))
    return sorted(chosen)
