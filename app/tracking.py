"""Трекинг: слой (эмодзи, стрелка, плашка, текст, стикер) следует за лицом, объектом или точкой.

Как это устроено (docs/motion/spec.md, «Трекинг слоя»):

1. **Кадры основы клипа.** Цель ищется не в исходнике, а в том же кадре, что видит зритель: основа
   клипа собирается тем же графом ffmpeg, что у рендера и ``view_frame`` (рефрейм x(t), кроп полей,
   раскладка стрима split/pip/meme, «шире окна», зеркало, хромакей, формат вывода), только
   5–10 кадров в секунду и в половину размера. Поэтому координаты цели сразу в долях кадра клипа —
   переводить из исходника нечего, и любая будущая раскладка тоже «бесплатна».
2. **Цель.** ``face`` — лица (YuNet, тот же детектор, что у автофокуса); ``object`` — объект класса
   COCO (YOLOX: человек, машина, собака…); ``point`` — точка/область кадра, указанная вручную:
   шаблон, который ищется в окне вокруг прогноза нормированной корреляцией (cv2.matchTemplate,
   без OpenCV — то же через numpy FFT). Детекция по кадрам связывается в один трек по близости к
   прогнозу (скорость), пропуски детекции держатся и экстраполируются до ``MAX_GAP_SEC``; дольше —
   цель потеряна (стоит на последнем месте, интервал попадает в отчёт). Цель видна меньше чем в
   ``FAIL_FOUND_SHARE`` кадров — отказ с понятным сообщением.
3. **Сглаживание** — Калман с постоянной скоростью + проход назад (RTS): без запаздывания и без
   «притяжения» краёв; сэмплы-прогнозы и потери тянут слабо. Отдельно на каждом куске.
4. **Ключи слоя** ``x``/``y`` (и ``scale`` по размеру цели) — во времени слоя, с ``ease: linear``,
   прорежены Дугласом–Пекером до ≤ 200 точек; на стыке кусков — ступенька (``step``). Слой получает
   пометку ``tracking`` — повторный запуск заменяет ключи, ``untrack`` их снимает; вырезы, стоп-кадры
   и правка кусков двигают ключи такого слоя вместе с содержимым (app/cuts.remap_layers).
5. **Камера.** Ключи — координаты на основе ДО наезда; слой получает ``followCamera`` (едет и
   зумится с видео), если цель не в окошке раскладки, которое камера не двигает (pip / полоса вебки).

Тяжёлое (кадры + детекция) — в очереди тяжёлых задач (app/heavy_queue.py), результат кэшируется по
источнику, кадрированию, цели и диапазону: смена отступа или сглаживания не гоняет детекцию заново.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterator

import numpy as np

log = logging.getLogger(__name__)

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
DEFAULT_FPS = {"face": 8.0, "object": 8.0, "point": 10.0}
FPS_RANGE = (4.0, 15.0)
MAX_RANGE_SEC = 120.0
MAX_GAP_SEC = 0.8  # пропуск детекции дольше — цель потеряна
FAIL_FOUND_SHARE = 0.4
DETECT_MAX_SIDE = 960  # кадр детекции: 540×960 у 9:16
POINT_BOX = 0.12  # сторона шаблона точки по умолчанию — доля ширины кадра
TRACKED_PROPS = ("x", "y", "scale")
TOLERANCE = {"x": 0.0012, "y": 0.0007, "scale": 0.006}  # ≈ 1.3 px кадра 1080×1920
MAX_KEYS = 200  # = app.motion.MAX_KEYS_PER_PROP
CACHE_VERSION = 2
COVER_SHARE = 0.25  # слой закрывает больше этой доли лица — предупреждение

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


# ---------------------------------------------------------------- сглаживание и прореживание


# Доверие к сэмплу трека в сглаживании: найден — как есть, прогноз в пропуске — слабо, потерян — почти нет.
NOISE_BY_STATUS = {"d": 1.0, "w": 8.0, "p": 25.0, "l": 400.0}
FOUND_STATUSES = ("d", "w")


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


# ---------------------------------------------------------------- шаблон (ручная точка)


def _integral(a: np.ndarray) -> np.ndarray:
    out = np.zeros((a.shape[0] + 1, a.shape[1] + 1), np.float64)
    out[1:, 1:] = a.cumsum(0).cumsum(1)
    return out


def _box_sums(ii: np.ndarray, h: int, w: int) -> np.ndarray:
    return ii[h:, w:] - ii[:-h, w:] - ii[h:, :-w] + ii[:-h, :-w]


def ncc_map(img: np.ndarray, tpl: np.ndarray) -> np.ndarray | None:
    """Нормированная корреляция шаблона по всем положениям (как cv2.TM_CCOEFF_NORMED):
    карта (H−h+1)×(W−w+1) в −1..1. С OpenCV — matchTemplate, без — FFT + интегральные суммы."""
    H, W = img.shape[:2]
    h, w = tpl.shape[:2]
    if h > H or w > W or h < 2 or w < 2:
        return None
    try:
        import cv2

        return cv2.matchTemplate(img.astype(np.float32), tpl.astype(np.float32), cv2.TM_CCOEFF_NORMED)
    except ImportError:
        pass
    img = img.astype(np.float64)
    t = tpl.astype(np.float64)
    t = t - t.mean()
    tn = math.sqrt(float((t * t).sum()))
    if tn < 1e-6:
        return np.zeros((H - h + 1, W - w + 1))
    corr = np.fft.irfft2(np.fft.rfft2(img) * np.conj(np.fft.rfft2(t, s=(H, W))), s=(H, W))[:H - h + 1, :W - w + 1]
    s1 = _box_sums(_integral(img), h, w)
    s2 = _box_sums(_integral(img * img), h, w)
    den = np.sqrt(np.maximum(s2 - s1 * s1 / (h * w), 0.0)) * tn
    return np.where(den > 1e-6, corr / np.maximum(den, 1e-12), 0.0)


def _peak(score: np.ndarray) -> tuple[float, float, float]:
    """Лучшее положение с поднастройкой параболой: (x, y, оценка)."""
    iy, ix = np.unravel_index(int(np.argmax(score)), score.shape)
    best = float(score[iy, ix])

    def sub(c: float, l: float, r: float) -> float:
        den = l - 2 * c + r
        return 0.0 if abs(den) < 1e-9 else max(-0.5, min(0.5, 0.5 * (l - r) / den))

    dx = sub(best, float(score[iy, ix - 1]), float(score[iy, ix + 1])) if 0 < ix < score.shape[1] - 1 else 0.0
    dy = sub(best, float(score[iy - 1, ix]), float(score[iy + 1, ix])) if 0 < iy < score.shape[0] - 1 else 0.0
    return ix + dx, iy + dy, best


class TemplateTracker:
    """Ручная цель: шаблон области вокруг точки ищется в окне вокруг прогноза.

    Шаблон медленно обновляется на уверенных кадрах (модель VTuber моргает и качается), а исходный
    шаблон страхует от «сползания» на фон: обновление — только пока исходный ещё узнаётся."""

    ACCEPT = 0.55
    STRONG = 0.72  # совпадение, которому верим даже далеко от прогноза
    UPDATE = 0.75

    def __init__(self, gray: np.ndarray, cx: float, cy: float, tw: int, th: int):
        self.tw, self.th = max(8, int(tw)) | 1, max(8, int(th)) | 1
        patch = self._patch(gray, cx, cy)
        if patch is None:
            raise TrackError("точка слишком близко к краю кадра — укажи её внутри картинки")
        if float(patch.std()) < 4.0:
            raise TrackError("в указанной области нет деталей (ровный цвет) — шаблону не за что зацепиться",
                             ["укажи точку на заметной детали: глаза, край головы, логотип"])
        self.tpl0 = patch.astype(np.float32)
        self.tpl = self.tpl0.copy()
        self.cx, self.cy = float(cx), float(cy)
        self.vx = self.vy = 0.0
        self.last_strong = True  # скорость — по паре уверенных совпадений подряд

    def _patch(self, gray: np.ndarray, cx: float, cy: float) -> np.ndarray | None:
        x0, y0 = int(round(cx - self.tw / 2)), int(round(cy - self.th / 2))
        if x0 < 0 or y0 < 0 or x0 + self.tw > gray.shape[1] or y0 + self.th > gray.shape[0]:
            return None
        return gray[y0:y0 + self.th, x0:x0 + self.tw]

    def _search(self, gray: np.ndarray, px: float, py: float, radius: float, tpl: np.ndarray):
        H, W = gray.shape
        x0 = int(max(0, math.floor(px - self.tw / 2 - radius)))
        y0 = int(max(0, math.floor(py - self.th / 2 - radius)))
        x1 = int(min(W, math.ceil(px + self.tw / 2 + radius)))
        y1 = int(min(H, math.ceil(py + self.th / 2 + radius)))
        score = ncc_map(gray[y0:y1, x0:x1], tpl)
        if score is None or not score.size:
            return None
        sx, sy, best = _peak(score)
        return x0 + sx + self.tw / 2, y0 + sy + self.th / 2, best

    def step(self, gray: np.ndarray, dt: float, wide: bool = False) -> tuple[float, float, float] | None:
        """Новое положение (cx, cy, оценка) или None — не найдено (прогноз сдвигается сам)."""
        px, py = self.cx + self.vx * dt, self.cy + self.vy * dt
        size = max(self.tw, self.th)
        radius = size * (2.0 if wide else 0.9) + math.hypot(self.vx, self.vy) * dt * 0.5
        found = self._search(gray, px, py, radius, self.tpl)
        if found is None or found[2] < self.ACCEPT:
            alt = self._search(gray, px, py, radius * 1.4, self.tpl0)
            if alt is not None and (found is None or alt[2] > found[2]):
                found = alt
        # Цель закрыта (зрители кидают предметы в модель, рука у лица): корреляция падает до 0.5–0.65 и
        # пик прыгает на соседнее. Слабое совпадение далеко от прогноза — не цель, а пропуск.
        # На стыке кусков и после потери (wide) цель вправе быть далеко — там ворот нет.
        if found is not None and not wide and found[2] < self.STRONG and math.hypot(found[0] - px, found[1] - py) > 0.35 * size:
            found = None
        if found is None or found[2] < self.ACCEPT:
            self.cx, self.cy = px, py
            self.vx *= 0.7
            self.vy *= 0.7
            self.last_strong = False
            return None
        nx, ny, score = found
        strong_pair = score >= self.STRONG and self.last_strong
        self.last_strong = score >= self.STRONG
        if dt > 0 and strong_pair:
            # Скорость — только по уверенным совпадениям и не быстрее 1.5 размера цели в секунду:
            # иначе один ложный пик под помехой разгоняет прогноз, и пропуск «уезжает» на сотни px.
            cap = 1.5 * size
            self.vx = max(-cap, min(cap, 0.5 * self.vx + 0.5 * (nx - self.cx) / dt))
            self.vy = max(-cap, min(cap, 0.5 * self.vy + 0.5 * (ny - self.cy) / dt))
        else:
            self.vx *= 0.5
            self.vy *= 0.5
        self.cx, self.cy = nx, ny
        if score >= self.UPDATE:
            patch = self._patch(gray, nx, ny)
            if patch is not None:
                check = ncc_map(patch, self.tpl0)
                if check is not None and float(check.max()) > 0.4:
                    self.tpl = 0.85 * self.tpl + 0.15 * patch.astype(np.float32)
        return nx, ny, score


# ---------------------------------------------------------------- детекторы


def _sharpness(gray: np.ndarray, box: dict) -> float:
    h, w = gray.shape[:2]
    x0, y0 = max(0, int(box["x"] * w)), max(0, int(box["y"] * h))
    x1, y1 = min(w, int((box["x"] + box["w"]) * w) + 1), min(h, int((box["y"] + box["h"]) * h) + 1)
    patch = gray[y0:y1, x0:x1].astype(np.float32)
    if patch.shape[0] < 3 or patch.shape[1] < 3:
        return 0.0
    lap = (patch[1:-1, :-2] + patch[1:-1, 2:] + patch[:-2, 1:-1] + patch[2:, 1:-1] - 4 * patch[1:-1, 1:-1])
    return float(lap.var())


def _drop_blurred(dets: list[dict], gray: np.ndarray) -> list[dict]:
    """Размытая подложка (blur_background, «шире окна») повторяет кадр крупно и мутно — лицо там
    тоже находится. Рамки заметно мутнее самой резкой — прочь."""
    if len(dets) < 2:
        return dets
    for det in dets:
        det["sharp"] = _sharpness(gray, det)
    top = max(det["sharp"] for det in dets)
    return [det for det in dets if det["sharp"] >= 0.15 * top]


def face_detector(score: float = 0.6) -> Callable[[np.ndarray], list[dict]]:
    """Лица кадра BGR → ``[{x, y, w, h, score}]`` (доли кадра). YuNet, без модели — каскады Haar."""
    try:
        import cv2

        from app import autofocus
    except ImportError as exc:
        raise TrackError(f"детектор лиц недоступен: {exc}",
                         ["точку на лице можно отследить вручную: target {kind: point, x, y, t}"]) from exc

    def detect(img: np.ndarray) -> list[dict]:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        h, w = img.shape[:2]
        dets = [{"x": fx / w, "y": fy / h, "w": fw / w, "h": fh / h, "score": 1.0}
                for fx, fy, fw, fh in autofocus._detect_faces(img, gray, score) if fw >= 6 and fh >= 6]
        return _drop_blurred(dets, gray)

    return detect


def object_detector(class_name: str) -> Callable[[np.ndarray], list[dict]]:
    try:
        import cv2

        from app.object_detect import _load, detect_objects
    except ImportError as exc:
        raise TrackError(f"детектор объектов недоступен: {exc}") from exc
    if not _load():
        raise TrackError("нет модели YOLOX (YOLOX_MODEL) — объекты не ищутся",
                         ["отследи точку вручную: target {kind: point, x, y, t}"])

    def detect(img: np.ndarray) -> list[dict]:
        dets = [d for d in detect_objects(img, 0.3) if d["name"] == class_name]
        return _drop_blurred(dets, cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))

    return detect


# ---------------------------------------------------------------- связывание детекций в трек


def _center(box: dict) -> tuple[float, float]:
    return box["x"] + box["w"] / 2, box["y"] + box["h"] / 2


def pick_seed(dets: list[dict], target: dict, aspect: float) -> dict | None:
    """Какую из рамок кадра брать целью: самую крупную, ближайшую к точке или i-ю слева."""
    if not dets:
        return None
    mode = target.get("mode", "largest")
    if mode == "near":
        px, py = float(target["x"]), float(target["y"])

        def dist(d: dict) -> float:
            cx, cy = _center(d)
            return math.hypot((cx - px) * aspect, cy - py)

        return min(dets, key=dist)
    if mode == "index":
        ordered = sorted(dets, key=lambda d: _center(d)[0])
        index = int(target.get("index", 0))
        return ordered[index] if index < len(ordered) else None
    return max(dets, key=lambda d: d["w"] * d["h"])


def associate(frames: list[dict], seed_index: int, seed_box: dict, aspect: float,
              max_gap: float = MAX_GAP_SEC) -> list[dict]:
    """Детекции по кадрам (``{t, piece, dets}``) → трек одной цели: от кадра-семени вперёд и назад.

    Следующая рамка — ближайшая к прогнозу (центр + скорость·dt), в пределах ворот ~ размера цели;
    на стыке кусков и после потери ворота шире. Нет рамки — прогноз держится до ``max_gap`` с
    (``s: "p"``), дальше цель стоит на месте (``s: "l"``) и ищется заново в широких воротах."""
    out: list[dict | None] = [None] * len(frames)
    seed = {"t": frames[seed_index]["t"], "piece": frames[seed_index]["piece"], **_box_xywh(seed_box), "s": "d"}
    out[seed_index] = seed
    for direction in (1, -1):
        cx, cy = _center(seed_box)
        bw, bh = seed_box["w"], seed_box["h"]
        vx = vy = 0.0
        last_t = frames[seed_index]["t"]
        piece = frames[seed_index]["piece"]
        missed = 0.0
        i = seed_index + direction
        while 0 <= i < len(frames):
            frame = frames[i]
            dt = abs(frame["t"] - last_t)
            boundary = frame["piece"] != piece
            if boundary:
                vx = vy = 0.0
            px, py = cx + vx * dt, cy + vy * dt
            size = max(bw * aspect, bh)
            gate = 2.5 if (boundary or missed > max_gap) else 0.9
            best, best_cost = None, None
            for det in frame["dets"]:
                dcx, dcy = _center(det)
                dist = math.hypot((dcx - px) * aspect, dcy - py) / max(1e-6, size)
                ratio = math.sqrt(max(1e-9, det["w"] * det["h"]) / max(1e-9, bw * bh))
                if dist > gate or not 0.45 <= ratio <= 2.2:
                    continue
                cost = dist + 0.6 * abs(math.log(ratio))
                if best_cost is None or cost < best_cost:
                    best, best_cost = det, cost
            if best is not None:
                ncx, ncy = _center(best)
                if dt > 0 and not boundary and missed <= max_gap:
                    vx = 0.5 * vx + 0.5 * (ncx - cx) / dt
                    vy = 0.5 * vy + 0.5 * (ncy - cy) / dt
                cx, cy, bw, bh = ncx, ncy, best["w"], best["h"]
                missed = 0.0
                status = "d"
            else:
                missed += dt
                if missed <= max_gap:
                    cx, cy = px, py
                    vx *= 0.7
                    vy *= 0.7
                    status = "p"
                else:
                    vx = vy = 0.0
                    status = "l"
            out[i] = {"t": frame["t"], "piece": frame["piece"], "x": cx - bw / 2, "y": cy - bh / 2,
                      "w": bw, "h": bh, "s": status}
            last_t = frame["t"]
            piece = frame["piece"]
            i += direction
    return [item for item in out if item is not None]


def _box_xywh(box: dict) -> dict:
    return {"x": float(box["x"]), "y": float(box["y"]), "w": float(box["w"]), "h": float(box["h"])}


# ---------------------------------------------------------------- кадры основы клипа


def _frame_setup(store: Any, plan: dict) -> dict:
    """Пресет и размеры кадра клипа — как у ``view_frame`` (motion_render.frame_preset)."""
    from app.clip_settings import normalize_render_settings
    from app.motion_render import frame_preset

    rs = normalize_render_settings(plan.get("render_settings") or {})
    preset, out_format, layers_format = frame_preset(store, plan, rs)
    if out_format != layers_format:
        raise TrackError(
            f"слои клипа размечены в {layers_format}, а выводится {out_format} — трек лёг бы не в те координаты",
            ["сначала «Разметить слои в этом формате» (или set_format с перекомпоновкой слоёв), потом трек"])
    width = int(preset.get("output_width") or 1080)
    height = int(preset.get("output_height") or 1920)
    scale = min(1.0, DETECT_MAX_SIDE / max(width, height))
    dw = max(2, int(round(width * scale / 2)) * 2)
    dh = max(2, int(round(height * scale / 2)) * 2)
    return {"rs": rs, "preset": preset, "format": out_format, "width": width, "height": height, "dw": dw, "dh": dh}


def piece_spans(plan: dict, rs: dict) -> list[tuple[int, float, float]]:
    """(кусок, начало, конец) на шкале clip: зона наплыва — следующему куску (как ClipClock)."""
    from app.motion import clip_timeline

    segments = plan.get("segments") or []
    spans = [(float(s["start_sec"]), float(s["end_sec"])) for s in segments]
    starts, total = clip_timeline(spans, rs.get("transition"))
    out = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else total
        out.append((i, float(start), float(end)))
    return out


def _piece_graph(store: Any, plan: dict, setup: dict, segment: dict, fps: float) -> tuple[str, Path]:
    from app import render as _render

    source = store.get_source(int(segment.get("source_id") or plan["source_id"]))
    path = Path(source.get("local_path") or "")
    if not path.is_file():
        raise TrackError("нет файла исходника — кадры для трекинга брать неоткуда",
                         ["трекинг работает по исходнику (как view_frame); файл записи удалён?"])
    preset, keyed = _render._keyed_render(setup["preset"], {"key": source.get("chroma_key")})
    keyed = keyed or {}
    piece = _render._preset_for_segment(preset, segment)
    reframe = _render._segment_reframe_x(segment, piece, source)
    graph = _render._render_filter(setup["width"], setup["height"], fps, piece, None,
                                   _render._base_crop(source, keyed), reframe)
    return graph, path


def base_frames_args(graph: str, path: Path, seek: float, offset: float, count: int, fps: float,
                     dw: int, dh: int) -> list[str]:
    """ffmpeg: ``count`` кадров основы с секунды куска ``offset`` (исходник с ``seek``) → raw BGR.

    Прореживание до ``fps`` — сразу на входе (граф основы не масштабирует лишние 60 кадров в
    секунду), а время сдвигается на ``offset``: выражение рефрейма x(t) считает от начала куска."""
    uses = graph.count("[0:v]")
    labels = [f"tin{k}" for k in range(max(1, uses))]
    for label in labels:
        graph = graph.replace("[0:v]", f"[{label}]", 1)
    head = f"[0:v]fps={fps:g},setpts=PTS+{offset:.6f}/TB"
    head += (f",split={len(labels)}" if len(labels) > 1 else "") + "".join(f"[{label}]" for label in labels)
    # На выходе время — снова с нуля: иначе выход cfr «дорисует» дубли первого кадра от 0 до offset.
    full = f"{head};{graph};[vout]setpts=PTS-STARTPTS,scale={dw}:{dh}:flags=area,format=bgr24[trk]"
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-ss", f"{max(0.0, seek):.3f}", "-t", f"{count / fps + 0.5:.3f}", "-i", str(path),
        "-filter_complex", full, "-map", "[trk]", "-frames:v", str(count), "-f", "rawvideo", "-",
    ]


def iter_base_frames(store: Any, plan: dict, setup: dict, start: float, end: float, fps: float,
                     on_frame: Callable[[float], None] | None = None) -> Iterator[dict]:
    """Кадры основы клипа на шкале clip от ``start`` до ``end`` с шагом 1/``fps``:
    ``{t, piece, img}`` (BGR ``dh×dw``)."""
    segments = plan.get("segments") or []
    dw, dh = setup["dw"], setup["dh"]
    size = dw * dh * 3
    for index, a_clip, b_clip in piece_spans(plan, setup["rs"]):
        lo, hi = max(start, a_clip), min(end, b_clip)
        if hi - lo < 1e-6:
            continue
        segment = segments[index]
        # Кадры на сетке 1/fps от начала куска: fps в графе основы их не двигает.
        offset = math.ceil((lo - a_clip) * fps - 1e-6) / fps
        count = max(1, int(math.floor((hi - a_clip - offset) * fps - 1e-6)) + 1)
        graph, path = _piece_graph(store, plan, setup, segment, fps)
        args = base_frames_args(graph, path, float(segment["start_sec"]) + offset, offset, count, fps, dw, dh)
        got = 0
        with tempfile.TemporaryFile() as errlog:
            proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=errlog)
            try:
                assert proc.stdout is not None
                while got < count:
                    buf = proc.stdout.read(size)
                    if not buf or len(buf) < size:
                        break
                    t = a_clip + offset + got / fps
                    if on_frame:
                        on_frame(t)
                    yield {"t": round(t, 4), "piece": index, "img": np.frombuffer(buf, np.uint8).reshape(dh, dw, 3)}
                    got += 1
            finally:
                if proc.poll() is None and got < count:
                    proc.kill()  # потребитель бросил кадры (ошибка) — ffmpeg дальше не нужен
                if proc.stdout:
                    proc.stdout.close()
                try:
                    proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    proc.kill()
            if got == 0:
                errlog.seek(0)
                err = errlog.read().decode(errors="replace")[-300:]
                raise TrackError(f"ffmpeg не отдал кадры куска {index}: {err}")


def _gray(img: np.ndarray) -> np.ndarray:
    return (img[..., 0] * 0.114 + img[..., 1] * 0.587 + img[..., 2] * 0.299).astype(np.float32)


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


# ---------------------------------------------------------------- расчёт трека


def _cache_key(store: Any, plan: dict, setup: dict, target: dict, start: float, end: float, fps: float) -> str:
    rs = setup["rs"]
    sources = {}
    for segment in plan.get("segments") or []:
        sid = int(segment.get("source_id") or plan["source_id"])
        if sid not in sources:
            source = store.get_source(sid)
            path = Path(source.get("local_path") or "")
            try:
                stat = path.stat()
                stamp = [stat.st_size, int(stat.st_mtime)]
            except OSError:
                stamp = None
            sources[sid] = {"path": str(path), "stamp": stamp, "crop": source.get("content_crop"),
                            "key": source.get("chroma_key"), "w": source.get("width"), "h": source.get("height"),
                            "focus_preset": source.get("focus_preset")}
    raw = {
        "v": CACHE_VERSION,
        "pieces": [(s.get("source_id"), s["start_sec"], s["end_sec"], s.get("focus") or [], s.get("fit"))
                   for s in plan.get("segments") or []],
        "sources": {str(k): v for k, v in sources.items()},
        "render": {k: rs.get(k) for k in ("preset_id", "layout", "mirror", "format", "transition")},
        "frame": [setup["width"], setup["height"], setup["dw"], setup["dh"]],
        "target": target, "range": [round(start, 3), round(end, 3)], "fps": fps,
    }
    return hashlib.sha256(json.dumps(raw, sort_keys=True, default=str).encode()).hexdigest()[:24]


def _cache_dir() -> Path:
    from app.settings import settings

    path = settings.runtime_dir / "tracking"
    path.mkdir(parents=True, exist_ok=True)
    return path


def compute_track(store: Any, clip_plan_id: int, start: float, end: float, target: Any, fps: float | None = None,
                  progress: Callable[[float, str], None] | None = None,
                  detector: Callable[[np.ndarray], list[dict]] | None = None) -> dict:
    """Трек цели на шкале clip ``start..end``: ``{samples: [{t, piece, x, y, w, h, s}], fps, frame,
    found, lost, regions, notes}`` — рамка цели в долях кадра клипа (основа до камеры), ``s`` —
    d (найдена) / w (слабо: цель частично закрыта) / p (прогноз в пропуске) / l (потеряна, стоит). Кэш — по источнику, кадрированию,
    цели и диапазону. ``detector`` — подмена детектора (тесты)."""
    target = normalize_target(target)
    plan = store.get_clip_plan(clip_plan_id)
    if not plan.get("segments"):
        raise TrackError("в клипе нет кусков")
    setup = _frame_setup(store, plan)
    spans = piece_spans(plan, setup["rs"])
    total = spans[-1][2] if spans else 0.0
    start = max(0.0, float(start))
    end = min(total, float(end))
    if end - start < 0.1:
        raise TrackError(f"диапазон трека {start:.2f}–{end:.2f} с вне клипа (длина {total:.2f} с)")
    if end - start > MAX_RANGE_SEC:
        raise TrackError(f"трек длиннее {MAX_RANGE_SEC:g} с — раздели слой или укажи start/end")
    fps = float(fps or DEFAULT_FPS[target["kind"]])
    fps = min(FPS_RANGE[1], max(FPS_RANGE[0], fps))
    key = _cache_key(store, plan, setup, target, start, end, fps)
    cache = _cache_dir() / f"{clip_plan_id}-{key}.json"
    if detector is None and cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            data["cached"] = True
            return data
        except (OSError, ValueError):
            pass
    if progress:
        progress(0.0, "в очереди тяжёлых задач")
    from app.heavy_queue import heavy_slot

    with heavy_slot("tracking", f"клип {clip_plan_id}"):
        started = time.monotonic()
        data = _compute(store, plan, setup, target, start, end, fps, progress, detector)
        data["seconds"] = round(time.monotonic() - started, 2)
    if detector is None:
        try:
            cache.write_text(json.dumps(data), encoding="utf-8")
        except OSError:
            pass
    data["cached"] = False
    return data


def _compute(store: Any, plan: dict, setup: dict, target: dict, start: float, end: float, fps: float,
             progress: Callable[[float, str], None] | None,
             detector: Callable[[np.ndarray], list[dict]] | None) -> dict:
    rs = setup["rs"]
    width, height, dw, dh = setup["width"], setup["height"], setup["dw"], setup["dh"]
    aspect = width / height
    from app import render as _render

    source = store.get_source(int(plan["source_id"]))
    regions = _render.layout_camera_regions(setup["preset"], source, width, height)
    camera = rs.get("camera") or []
    span = max(1e-6, end - start)
    notes: list[str] = []

    def tick(t: float, stage: str) -> None:
        if progress:
            progress(min(0.99, max(0.0, (t - start) / span)), stage)

    t_seed = min(end, max(start, float(target.get("t", start))))
    if target["kind"] == "point":
        samples = _track_point(store, plan, setup, target, start, end, fps, t_seed, camera, regions, tick)
    else:
        if detector is None:
            detector = face_detector() if target["kind"] == "face" else object_detector(target["class"])
        frames = []
        for frame in iter_base_frames(store, plan, setup, start, end, fps,
                                      on_frame=lambda t: tick(t, "ищу цель по кадрам")):
            frames.append({"t": frame["t"], "piece": frame["piece"], "dets": detector(frame["img"])})
        if not frames:
            raise TrackError("кадров для трекинга нет")
        seed_target = dict(target)
        if seed_target.get("mode") == "near":
            seed_target["x"], seed_target["y"] = screen_to_base(target["x"], target["y"], t_seed, camera, regions,
                                                                width, height)
        order = sorted(range(len(frames)), key=lambda i: abs(frames[i]["t"] - t_seed))
        seed_index, seed_box = None, None
        for i in order:
            box = pick_seed(frames[i]["dets"], seed_target, aspect)
            if box is not None:
                seed_index, seed_box = i, box
                break
        what = "лиц" if target["kind"] == "face" else f"объектов «{target['class']}»"
        if seed_index is None:
            raise TrackError(f"в кадрах {start:.1f}–{end:.1f} с {what} не найдено",
                             ["аниме-модель VTuber детектор лиц не видит — отследи точку: target {kind: point, x, y, t}",
                              "проверь view_frame: цель в кадре?"])
        if abs(frames[seed_index]["t"] - t_seed) > 1.0:
            notes.append(f"в {t_seed:.2f} с цели нет — трек начат с {frames[seed_index]['t']:.2f} с")
        samples = associate(frames, seed_index, seed_box, aspect)
    found = sum(1 for s in samples if s["s"] in FOUND_STATUSES) / max(1, len(samples))
    lost = _intervals([s for s in samples], "l", 1.0 / fps)
    if found < FAIL_FOUND_SHARE:
        where = ", ".join(f"{a:.1f}–{b:.1f} с" for a, b in lost[:4]) or "почти везде"
        raise TrackError(f"цель видна только в {found:.0%} кадров — потеряна: {where}",
                         ["сузь диапазон слоя до места, где цель видна", "укажи цель точнее (mode near / point)",
                          "закрытое лицо, разворот, уход из кадра — трек тут не поможет"])
    if lost:
        notes.append("цель потеряна на " + ", ".join(f"{a:.1f}–{b:.1f} с" for a, b in lost[:4])
                     + " — там слой стоит на последнем месте")
    return {"samples": [{k: (round(v, 5) if isinstance(v, float) else v) for k, v in s.items()} for s in samples],
            "fps": fps, "frame": {"w": width, "h": height, "dw": dw, "dh": dh}, "found": round(found, 3),
            "lost": [[round(a, 2), round(b, 2)] for a, b in lost], "regions": regions, "target": target,
            "range": [round(start, 3), round(end, 3)], "notes": notes}


def _track_point(store: Any, plan: dict, setup: dict, target: dict, start: float, end: float, fps: float,
                 t_seed: float, camera: list[dict], regions: dict | None,
                 tick: Callable[[float, str], None]) -> list[dict]:
    """Ручная точка: шаблон с кадра ``t_seed`` вперёд до ``end`` (кадры идут потоком), затем
    назад до ``start`` (в памяти только эта часть)."""
    width, height, dw, dh = setup["width"], setup["height"], setup["dw"], setup["dh"]
    bx, by = screen_to_base(target["x"], target["y"], t_seed, camera, regions, width, height)
    tw = int(round(float(target.get("w") or POINT_BOX) * dw))
    th = int(round(float(target["h"]) * dh)) if target.get("h") else tw
    box_w, box_h = tw / dw, th / dh

    seed: dict = {}

    def run(frames: Iterator[dict], tracker: TemplateTracker | None, last: dict | None) -> list[dict]:
        out: list[dict] = []
        missed = 0.0
        for frame in frames:
            gray = _gray(frame["img"])
            if tracker is None:  # кадр-семя: шаблон берётся с него
                tracker = TemplateTracker(gray, bx * dw, by * dh, tw, th)
                seed.update(gray=gray, t=frame["t"], piece=frame["piece"])
                out.append(_point_sample(frame, tracker, box_w, box_h, "d", dw, dh))
                last = frame
                continue
            dt = abs(frame["t"] - last["t"]) if last else 0.0
            boundary = bool(last) and frame["piece"] != last["piece"]
            if boundary:
                tracker.vx = tracker.vy = 0.0
            got = tracker.step(gray, dt, wide=boundary or missed > MAX_GAP_SEC)
            if got is None:
                missed += dt
                status = "p" if missed <= MAX_GAP_SEC else "l"
                if status == "l":
                    tracker.vx = tracker.vy = 0.0
            else:
                missed = 0.0
                # Слабое совпадение (цель частично закрыта) — в треке, но сглаживание ему верит меньше.
                status = "d" if got[2] >= tracker.STRONG else "w"
            out.append(_point_sample(frame, tracker, box_w, box_h, status, dw, dh))
            last = frame
        return out

    # Вперёд от кадра-семени — потоком; назад — только кусок до него (он в памяти).
    forward = run(iter_base_frames(store, plan, setup, t_seed, end, fps,
                                   on_frame=lambda t: tick(t, "веду точку по кадрам")), None, None)
    if not forward:
        raise TrackError("кадров для трекинга нет")
    first_t = forward[0]["t"]
    if first_t - start < 0.5 / fps:
        return forward
    before = [f for f in iter_base_frames(store, plan, setup, start, first_t - 0.5 / fps, fps)]
    if not before:
        return forward
    back_tracker = TemplateTracker(seed["gray"], bx * dw, by * dh, tw, th)
    back = run(iter(reversed(before)), back_tracker, {"t": seed["t"], "piece": seed["piece"]})
    return list(reversed(back)) + forward


def _point_sample(frame: dict, tracker: TemplateTracker, box_w: float, box_h: float, status: str,
                  dw: int, dh: int) -> dict:
    cx, cy = tracker.cx / dw, tracker.cy / dh
    return {"t": frame["t"], "piece": frame["piece"], "x": cx - box_w / 2, "y": cy - box_h / 2,
            "w": box_w, "h": box_h, "s": status}


def _intervals(samples: list[dict], status: str, step: float) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for s in samples:
        if s["s"] != status:
            continue
        if out and s["t"] - out[-1][1] <= step * 1.5:
            out[-1] = (out[-1][0], s["t"])
        else:
            out.append((s["t"], s["t"]))
    return out


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


def track_layer(store: Any, clip_plan_id: int, layer: dict, target: Any, offset: Any = None, follow: Any = None,
                smooth: Any = None, fps: float | None = None, preset: str | None = None,
                progress: Callable[[float, str], None] | None = None,
                detector: Callable[[np.ndarray], list[dict]] | None = None) -> tuple[dict, dict]:
    """Посчитать трек под окно слоя и вернуть ``(новый слой, отчёт)``. Ничего не сохраняет."""
    from app.clip_settings import normalize_render_settings

    plan = store.get_clip_plan(clip_plan_id)
    rs = normalize_render_settings(plan.get("render_settings") or {})
    fmt = rs.get("layers_format")
    if layer.get("type") in ("audio", "fx", "captions"):
        raise TrackError(f"слой «{layer.get('type')}» не двигается по кадру — трекать нечего")
    target_n = normalize_target(target)
    if "t" not in target_n:
        target_n["t"] = float(layer["start"])
    p_offset, p_follow, patch = preset_offset(preset, layer, fmt)
    base_layer = {**layer, **patch}
    offset_n = normalize_offset({**p_offset, **(offset if isinstance(offset, dict) else {})})
    follow_n = normalize_follow({**p_follow, **(follow if isinstance(follow, dict) else {})})
    if target_n["kind"] == "point" and follow_n["scale"]:
        follow_n["scale"] = False  # у ручной точки размер не меряется
    track = compute_track(store, clip_plan_id, float(layer["start"]), float(layer["end"]), target_n, fps,
                          progress, detector)
    fresh = apply_track(base_layer, track, offset_n, follow_n, smooth, fmt, preset)
    notes = list(track.get("notes") or [])
    if rs.get("camera"):
        notes.append("наезды камеры: слой " + ("едет с камерой (followCamera)" if fresh.get("followCamera")
                                              else "в окошке раскладки — стоит, как окошко"))
        if any(k.get(f) for k in rs["camera"] for f in ("z", "rotX", "rotY")):
            notes.append("3D-камера: параллакс основы трек не повторяет — проверь кадры")
    report = {
        "target": track.get("target"), "range": track.get("range"), "fps": track.get("fps"),
        "found": track.get("found"), "lost": track.get("lost"), "samples": len(track.get("samples") or []),
        "keys": {p: len((fresh.get("keys") or {}).get(p) or []) for p in fresh["tracking"]["props"]},
        "follow_camera": fresh.get("followCamera"), "cached": track.get("cached"),
        "seconds": track.get("seconds"), "notes": notes, "warnings": cover_warnings(fresh, track, fmt),
        "check_times": check_times(fresh, track),
    }
    return fresh, report


# ---------------------------------------------------------------- фоновые задачи (прогресс для UI)


_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()
_MAX_JOBS = 40


def start_job(run: Callable[[Callable[[float, str], None]], dict]) -> str:
    """Запустить ``run(progress)`` в потоке; ``job_status(id)`` — ход и результат."""
    job_id = uuid.uuid4().hex[:12]
    job = {"id": job_id, "state": "queued", "progress": 0.0, "stage": "в очереди", "result": None, "error": None,
           "hints": [], "started": time.time()}
    with _JOBS_LOCK:
        _JOBS[job_id] = job
        for old in sorted(_JOBS, key=lambda k: _JOBS[k]["started"])[:-_MAX_JOBS]:
            _JOBS.pop(old, None)

    def progress(value: float, stage: str) -> None:
        job.update(state="running", progress=round(float(value), 3), stage=stage)

    def worker() -> None:
        try:
            job["result"] = run(progress)
            job.update(state="done", progress=1.0, stage="готово")
        except TrackError as exc:
            job.update(state="error", error=str(exc), hints=exc.hints)
        except Exception as exc:  # noqa: BLE001 - любая ошибка — в ответ задачи, а не в лог потока
            log.exception("трекинг упал")
            job.update(state="error", error=f"трекинг не удался: {exc}")

    threading.Thread(target=worker, name=f"track-{job_id}", daemon=True).start()
    return job_id


def job_status(job_id: str) -> dict | None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job else None
