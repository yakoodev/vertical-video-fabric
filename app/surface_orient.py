"""Четырёхугольник-семя по настоящей ориентации поверхности: слово ложится ВДОЛЬ стены в глубину, а не плашкой.

Владелец 16.11 на вторую пробу: «перспективы нет» — слово было прямоугольником экрана, а не плоскостью стены.
Здесь по кадру-семени и окну (``box``: центр и размер, доли кадра) находим две главные системы прямых поверхности
(края стены, швы плит, ряды окон, доски):

1. **Отрезки.** Canny + вероятностный Хаф в окне (с полями) — отрезки с длиной (вес).
2. **Два семейства.** Гистограмма углов (взвешенная длиной, по модулю 180°): главный пик и второй не ближе 25°.
   «Горизонтальное» семейство (ближе к 0°) — направление строки текста (вдоль стены в глубину), второе — «вертикаль»
   стены. Второго нет — вертикаль кадра (стены в игре вертикальны, камера почти не кренится).
3. **Точки схода.** Для семейства — точка, ближе всего ко всем его прямым (наименьшие квадраты в однородных
   координатах: SVD; почти параллельные прямые дают точку на бесконечности — это тоже правильный ответ).
4. **Четырёхугольник.** Через центр окна — прямая к первой точке схода и ко второй; на них откладываем половину
   ширины и высоты окна; через полученные точки — прямые к противоположным точкам схода; их пересечения — углы.
   Получается настоящий «прямоугольник на плоскости» в перспективе: стороны идут по линиям стены.

Ответ — углы TL, TR, BR, BL (доли кадра) и что нашлось (``info``): углы семейств, точки схода, число отрезков.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

MIN_SEG_FRAC = 0.04  # отрезок не короче этой доли ширины кадра
FAMILY_GAP_DEG = 25.0
MAX_VERTICAL_TILT = 15.0  # «вертикаль» стены не дальше этого от вертикали кадра (камера игры не кренится)
MIN_FAMILY = 4  # отрезков в семействе «вдоль стены», меньше — случайность


def _cv2():
    import cv2  # noqa: PLC0415

    return cv2


def segments(gray: np.ndarray, region: tuple[int, int, int, int]) -> np.ndarray:
    """Отрезки ``[[x1, y1, x2, y2, len], …]`` (px кадра) в окне ``region`` (x0, y0, x1, y1)."""
    cv2 = _cv2()
    x0, y0, x1, y1 = region
    patch = gray[y0:y1, x0:x1]
    if patch.size == 0:
        return np.zeros((0, 5))
    blur = cv2.GaussianBlur(patch, (3, 3), 0)
    med = float(np.median(blur))
    edges = cv2.Canny(blur, max(10, 0.5 * med), max(30, 1.2 * med))
    w = gray.shape[1]
    min_len = max(12, int(MIN_SEG_FRAC * w))
    lines = cv2.HoughLinesP(edges, 1, np.pi / 360, threshold=max(15, min_len // 2), minLineLength=min_len, maxLineGap=4)
    if lines is None:
        return np.zeros((0, 5))
    out = []
    for xa, ya, xb, yb in lines.reshape(-1, 4):
        ln = math.hypot(xb - xa, yb - ya)
        out.append([xa + x0, ya + y0, xb + x0, yb + y0, ln])
    return np.asarray(out, np.float64)


def _angles(seg: np.ndarray) -> np.ndarray:
    return np.degrees(np.arctan2(seg[:, 3] - seg[:, 1], seg[:, 2] - seg[:, 0])) % 180.0


def _peak(ang: np.ndarray, wts: np.ndarray, exclude: float | None = None) -> float | None:
    hist = np.zeros(180)
    for a, wt in zip(ang, wts):
        hist[int(a) % 180] += wt
    sm = np.zeros(180)
    for k in range(-3, 4):  # круговое сглаживание ±3°
        sm += np.roll(hist, k) * (1 - abs(k) / 4)
    if exclude is not None:
        for d in range(180):
            if min(abs(d - exclude), 180 - abs(d - exclude)) < FAMILY_GAP_DEG:
                sm[d] = 0
    if sm.max() <= 0:
        return None
    return float(np.argmax(sm))


def _member(ang: np.ndarray, peak: float, tol: float = 8.0) -> np.ndarray:
    d = np.abs(ang - peak)
    return np.minimum(d, 180 - d) <= tol


def vanishing_point(seg: np.ndarray) -> np.ndarray:
    """Однородная точка схода семейства (3,), ближайшая ко всем прямым (вес — длина)."""
    rows = []
    for xa, ya, xb, yb, ln in seg:
        ln_h = np.cross([xa, ya, 1.0], [xb, yb, 1.0])
        nrm = math.hypot(ln_h[0], ln_h[1])
        if nrm < 1e-9:
            continue
        rows.append(ln_h / nrm * math.sqrt(ln))
    A = np.asarray(rows)
    # Масштаб координат: прямые в px, третья компонента велика — нормируем для устойчивости SVD.
    _, _, vt = np.linalg.svd(A)
    v = vt[-1]
    return v / (np.linalg.norm(v) or 1.0)


def _dir_at(v: np.ndarray, c: np.ndarray, ref: np.ndarray) -> np.ndarray:
    """Единичное направление из точки ``c`` к точке схода ``v`` (однородной), со знаком как у ``ref``."""
    if abs(v[2]) < 1e-9 * max(1.0, abs(v[0]) + abs(v[1])):
        d = np.array([v[0], v[1]])
    else:
        d = np.array([v[0] / v[2], v[1] / v[2]]) - c
    n = np.linalg.norm(d)
    if n < 1e-9:
        return ref / np.linalg.norm(ref)
    d = d / n
    return d if float(np.dot(d, ref)) >= 0 else -d


def _line_to(p: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Однородная прямая через точку p (px) и точку схода v (однородную)."""
    return np.cross([p[0], p[1], 1.0], v)


def _meet(l1: np.ndarray, l2: np.ndarray) -> np.ndarray | None:
    x = np.cross(l1, l2)
    if abs(x[2]) < 1e-9:
        return None
    return np.array([x[0] / x[2], x[1] / x[2]])


def plane_quad(gray: np.ndarray, box: dict, *, pad: float = 0.6) -> tuple[list[list[float]], dict]:
    """Четырёхугольник по ориентации поверхности в окне ``box`` {x, y, w, h} (центр и размер, доли кадра).

    Возвращает (углы TL, TR, BR, BL в долях кадра, info). Не нашлось систем прямых — прямоугольник окна (info
    ``fallback``)."""
    h_img, w_img = gray.shape[:2]
    cx, cy = float(box["x"]) * w_img, float(box["y"]) * h_img
    bw, bh = float(box["w"]) * w_img, float(box.get("h") or box["w"] * 0.3) * h_img
    rx0 = int(max(0, cx - bw * (0.5 + pad)))
    rx1 = int(min(w_img, cx + bw * (0.5 + pad)))
    ry0 = int(max(0, cy - bh * (0.5 + pad * 2)))
    ry1 = int(min(h_img, cy + bh * (0.5 + pad * 2)))
    seg = segments(gray, (rx0, ry0, rx1, ry1))
    info: dict[str, Any] = {"segments": int(len(seg))}
    rect = [[(cx - bw / 2) / w_img, (cy - bh / 2) / h_img], [(cx + bw / 2) / w_img, (cy - bh / 2) / h_img],
            [(cx + bw / 2) / w_img, (cy + bh / 2) / h_img], [(cx - bw / 2) / w_img, (cy + bh / 2) / h_img]]
    if len(seg) < 3:
        return rect, {**info, "fallback": "мало прямых — прямоугольник окна"}
    ang = _angles(seg)
    wts = seg[:, 4]
    # «Горизонталь» стены — пик среди наклонов не круче 60° от горизонтали; «вертикаль» — около вертикали.
    near_h = np.minimum(ang, 180 - ang) <= 60
    near_v = np.abs(ang - 90) <= MAX_VERTICAL_TILT
    p1 = _peak(ang[near_h], wts[near_h]) if near_h.any() else None
    if p1 is None:
        return rect, {**info, "fallback": "нет линий вдоль стены — прямоугольник окна"}
    p2 = _peak(ang[near_v], wts[near_v], exclude=p1) if near_v.any() else None
    fam1 = seg[_member(ang, p1)]
    c = np.array([cx, cy])
    if len(fam1) < MIN_FAMILY or float(fam1[:, 4].sum()) < 0.8 * bw:
        # Вдоль стены прямых почти нет (гладкая плита лицом к камере) — строка по горизонтали кадра, а не по двум
        # случайным отрезкам (иначе текст ложится наискось ни к чему).
        v1 = np.array([1.0, 0.0, 0.0])
        info["along"] = "горизонталь кадра (мало прямых вдоль стены)"
        p1 = 0.0
        fam1 = seg[:0]
    else:
        v1 = vanishing_point(fam1)
    if p2 is not None and _member(ang, p2).sum() >= 3:
        v2 = vanishing_point(seg[_member(ang, p2)])
    else:
        v2 = np.array([0.0, 1.0, 0.0])  # вертикаль кадра
        p2 = 90.0
    u1 = _dir_at(v1, c, np.array([1.0, 0.0]))
    u2 = _dir_at(v2, c, np.array([0.0, 1.0]))
    # Точки на осях через центр, потом прямые к противоположным точкам схода — углы.
    a, b = c - u1 * bw / 2, c + u1 * bw / 2
    t, d = c - u2 * bh / 2, c + u2 * bh / 2
    top, bot = _line_to(t, v1), _line_to(d, v1)
    left, right = _line_to(a, v2), _line_to(b, v2)
    corners = [_meet(top, left), _meet(top, right), _meet(bot, right), _meet(bot, left)]
    if any(p is None or not np.all(np.isfinite(p)) for p in corners):
        return rect, {**info, "fallback": "вырожденные точки схода"}
    q = np.asarray(corners)
    # Здравый смысл: выпуклый, не вывернут, не улетел (каждый угол в пределах 2 размеров окна от центра).
    from app.surface_pose import area, convex_same_winding

    if not convex_same_winding(q, 1.0) or np.max(np.linalg.norm(q - c, axis=1)) > 2.2 * math.hypot(bw, bh):
        return rect, {**info, "fallback": "четырёхугольник по прямым вывернут — прямоугольник окна"}
    info.update(along_deg=round(p1, 1), up_deg=round(float(p2), 1), families=[int(len(fam1)), int(_member(ang, p2).sum())],
                vp1=[round(float(x), 4) for x in v1], vp2=[round(float(x), 4) for x in v2], area_px=round(area(q), 1))
    return [[round(float(x) / w_img, 5), round(float(y) / h_img, 5)] for x, y in q], info


__all__ = ["plane_quad", "segments", "vanishing_point"]
