"""Слежение: шаблон, детекторы, связывание детекций (из ``app/tracking.py``)."""

from __future__ import annotations

import math
from typing import Callable

import numpy as np

from app.tracking_normalize import TrackError

MAX_GAP_SEC = 0.8  # пропуск детекции дольше — цель потеряна


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
