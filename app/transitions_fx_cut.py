"""Переходы склеек кадрами: хлыст, заливка цветом, смена через размытие, затемнение, зум-размытие.

Одобрено владельцем 17.11 (идеи — переходы каталога HyperFrames, Apache-2.0, и Remotion; код свой, ничего не
скопировано). Окно перехода — то же, что у мувика (``app/transitions_fx.py``): A — последние ``n`` кадров уходящей
части, B — первые ``n`` входящей, середина окна — точка склейки; первый кадр окна почти A, последний — ровно B.
Кадры — float32 RGB (h, w, 3) 0..255, всё детерминированно: тот же стык — те же кадры.

* ``whippan`` — «хлыст»: A уезжает по ``direction``, B въезжает вплотную за ним (как быстрая панорама),
  обе картинки смазаны вдоль направления; смаз максимален на склейке. ``direction`` — куда едет КАРТИНКА
  (left — влево: камера «хлестнула» вправо).
* ``colorfill`` — «заливка цветом»: плашка ``color`` (и светлая полоса перед ней) въезжает по направлению,
  закрывает кадр на склейке и уезжает дальше, открывая B.
* ``blurswap`` — A уходит в размытие, на пике размытия подменяется B, B проясняется.
* ``dipblack`` — «затемнение»: первая половина окна (≈3 кадра при 0.2 с и 30 к/с) — A гаснет в чёрный, к
  склейке кадр чёрный; со склейки — B сразу, без проявления.
* ``zoomblur`` — радиальное зум-размытие: A влетает в центр со смазом от центра, B вылетает из центра и
  успокаивается («шейдерный» переход на OpenCV, без GLSL).
"""

from __future__ import annotations

import math
from typing import Sequence

import cv2
import numpy as np

from app.transitions_fx import ease

# Направление картинки → единичный вектор экрана (y вниз).
DIRECTION_VECTORS: dict[str, tuple[float, float]] = {"left": (-1.0, 0.0), "right": (1.0, 0.0),
                                                     "up": (0.0, -1.0), "down": (0.0, 1.0)}
WHIP_BLUR = 0.22        # длина смаза хлыста на склейке — доля стороны кадра по направлению
FILL_TINT = 0.45        # светлая полоса перед плашкой: цвет + столько белого
FILL_LEAD = 0.08        # насколько светлая полоса опережает плашку (доля окна)
FILL_RUN = 0.42         # за какую долю окна кромка проходит кадр
BLUR_MAX = 0.03         # сигма размытия на склейке — доля длинной стороны кадра
ZOOM_MAX = 0.55         # насколько A «влетает» (масштаб 1 → 1 + ZOOM_MAX), B — обратно
ZOOM_TAPS = 8           # копий кадра на радиальный смаз


def cut_progress(j: int, n: int) -> float:
    """Кадр ``j`` из ``n`` → 0..1 с концами: 0 — ровно A, 1 — ровно B; середина окна (склейка) — 0.5."""
    return j / (n - 1) if n > 1 else 1.0


def axis_of(direction: str) -> tuple[float, float]:
    return DIRECTION_VECTORS.get(direction, DIRECTION_VECTORS["left"])


def nearest_direction(v: Sequence[float]) -> str:
    """Вектор движения картинки → ближайшее из left/right/up/down."""
    x, y = float(v[0]), float(v[1])
    if abs(x) >= abs(y):
        return "right" if x >= 0 else "left"
    return "down" if y >= 0 else "up"


def ease_cubic(p: float) -> float:
    """Вход-выход кубом: медленно у краёв окна, рывок посередине (хлыст)."""
    p = min(1.0, max(0.0, p))
    return 4 * p ** 3 if p < 0.5 else 1 - (-2 * p + 2) ** 3 / 2


def axis_blur(img: np.ndarray, horizontal: bool, length: float) -> np.ndarray:
    """Смаз коробкой вдоль оси на ``length`` px (на половинном размере — быстро и мягко)."""
    size = int(length)
    if size < 3:
        return img
    h, w = img.shape[:2]
    small = cv2.resize(img, (max(1, w // 2), max(1, h // 2)), interpolation=cv2.INTER_AREA)
    k = max(2, size // 2)
    small = cv2.blur(small, (k, 1) if horizontal else (1, k), borderType=cv2.BORDER_REPLICATE)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


def _strip(img: np.ndarray, d: tuple[float, float], offset: float) -> tuple[np.ndarray, np.ndarray]:
    """Координата пикселя вдоль направления ``d`` (0 — край, откуда едут) и длина стороны."""
    h, w = img.shape[:2]
    if d[0]:
        coord = np.arange(w, dtype=np.float32)[None, :].repeat(h, 0)
        side = w
        if d[0] < 0:
            coord = side - 1 - coord
    else:
        coord = np.arange(h, dtype=np.float32)[:, None].repeat(w, 1)
        side = h
        if d[1] < 0:
            coord = side - 1 - coord
    return coord + offset, np.float32(side)


def whippan(a_img: np.ndarray, b_img: np.ndarray, p: float, direction: str) -> np.ndarray:
    """A уезжает по направлению, B въезжает вплотную за ним; смаз вдоль хода, пик — на склейке."""
    h, w = a_img.shape[:2]
    d = axis_of(direction)
    horizontal = bool(d[0])
    side = w if horizontal else h
    q = ease_cubic(p)
    travel = q * side
    dx, dy = d[0] * travel, d[1] * travel
    m = np.float32([[1, 0, dx], [0, 1, dy]])
    a_s = cv2.warpAffine(a_img, m, (w, h), borderMode=cv2.BORDER_REPLICATE)
    m2 = np.float32([[1, 0, dx - d[0] * side], [0, 1, dy - d[1] * side]])
    b_s = cv2.warpAffine(b_img, m2, (w, h), borderMode=cv2.BORDER_REPLICATE)
    coord, _ = _strip(a_img, d, 0.0)
    mask = np.clip(travel - coord, 0, 1)[:, :, None]  # где уже B (у края, откуда едут)
    out = a_s * (1 - mask) + b_s * mask
    blur = WHIP_BLUR * side * math.sin(math.pi * min(1.0, max(0.0, p))) ** 0.8
    return axis_blur(out, horizontal, blur)


def _tint(color: Sequence[float], amount: float) -> np.ndarray:
    c = np.array(color, np.float32)
    return c + (255.0 - c) * amount


def colorfill(a_img: np.ndarray, b_img: np.ndarray, p: float, direction: str,
              color: Sequence[float]) -> np.ndarray:
    """Светлая полоса и плашка цвета въезжают по направлению, на склейке кадр залит, дальше открывается B."""
    d = axis_of(direction)
    coord, side = _strip(a_img, d, 0.0)
    u = coord / side  # 0 — край, откуда въезжает плашка
    base = a_img if p < 0.5 else b_img

    def cover(enter: float, leave: float) -> np.ndarray:
        # Ведущая кромка идёт 0 → 1 с ``enter``, задняя — с ``leave`` (плашка уезжает дальше по ходу).
        lead = ease(min(1.0, max(0.0, (p - enter) / FILL_RUN)))
        tail = ease(min(1.0, max(0.0, (p - leave) / FILL_RUN)))
        soft = 2.0 / float(side)
        reach = 1 + 2 * soft  # кромка уходит за край кадра — у края не остаётся полупрозрачной полосы
        inside = np.clip((lead * reach - u) / soft, 0, 1) * np.clip((u - tail * reach) / soft + 1, 0, 1)
        return inside[:, :, None]

    # Светлая полоса въезжает первой и уходит последней; плашка — поверх неё, на склейке кадр залит.
    light = cover(0.0, 1.0 - FILL_RUN)
    main = cover(FILL_LEAD, 0.5)
    out = base * (1 - light) + _tint(color, FILL_TINT) * light
    return out * (1 - main) + np.array(color, np.float32) * main


def _soft_blur(img: np.ndarray, sigma: float) -> np.ndarray:
    """Гаусс на четверти размера (сильное размытие дёшево и без ступенек)."""
    if sigma < 0.6:
        return img
    h, w = img.shape[:2]
    k = 4 if sigma > 8 else 2
    small = cv2.resize(img, (max(1, w // k), max(1, h // k)), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), sigma / k, borderType=cv2.BORDER_REFLECT)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


def blurswap(a_img: np.ndarray, b_img: np.ndarray, p: float) -> np.ndarray:
    """Размытие растёт к склейке, на пике A подменяется B (короткий наплыв размытых), B проясняется."""
    h, w = a_img.shape[:2]
    sigma = BLUR_MAX * max(h, w) * math.sin(math.pi * min(1.0, max(0.0, p))) ** 1.2
    mix = min(1.0, max(0.0, (p - 0.4) / 0.2))
    if mix <= 0:
        return _soft_blur(a_img, sigma)
    if mix >= 1:
        return _soft_blur(b_img, sigma)
    return _soft_blur(a_img * (1 - mix) + b_img * mix, sigma)


def dipblack(a_img: np.ndarray, b_img: np.ndarray, j: int, n: int) -> np.ndarray:
    """Первая половина окна — A гаснет в чёрный (последний кадр до склейки — чёрный), дальше — B."""
    half = n // 2
    if j >= n - half:
        return b_img
    if n % 2 and j == half:
        return np.zeros_like(a_img)
    k = (j + 1) / max(1, half)
    return a_img * float((1 - k) ** 1.3)


def _radial(img: np.ndarray, scale: float, smear: float) -> np.ndarray:
    """Кадр в масштабе ``scale`` от центра со смазом от центра: среднее копий от scale до scale·(1+smear)."""
    h, w = img.shape[:2]
    small = cv2.resize(img, (max(1, w // 2), max(1, h // 2)), interpolation=cv2.INTER_AREA)
    sh, sw = small.shape[:2]
    taps = ZOOM_TAPS if smear > 0.004 else 1
    acc = np.zeros_like(small)
    for i in range(taps):
        s = scale * (1 + smear * (i / max(1, taps - 1)))
        m = cv2.getRotationMatrix2D((sw / 2, sh / 2), 0, s)
        acc += cv2.warpAffine(small, m, (sw, sh), borderMode=cv2.BORDER_REFLECT)
    acc /= taps
    out = cv2.resize(acc, (w, h), interpolation=cv2.INTER_LINEAR)
    if smear <= 0.004:
        # Без смаза — кадр в полном разрешении (последний кадр окна ровно B).
        if abs(scale - 1.0) < 1e-4:
            return img
        m = cv2.getRotationMatrix2D((w / 2, h / 2), 0, scale)
        return cv2.warpAffine(img, m, (w, h), borderMode=cv2.BORDER_REFLECT)
    return out


def zoomblur(a_img: np.ndarray, b_img: np.ndarray, p: float) -> np.ndarray:
    """A влетает в центр (масштаб и смаз растут), с середины B вылетает из центра и успокаивается."""
    p = min(1.0, max(0.0, p))
    peak = math.sin(math.pi * p)
    smear = 0.22 * peak ** 1.5
    if p < 0.5:
        img = _radial(a_img, 1 + ZOOM_MAX * ease_cubic(p), smear)
    else:
        img = _radial(b_img, 1 + ZOOM_MAX * (1 - ease_cubic(p)), smear)
    # Вспышки нет — только чуть светлее на пике (свет «тянется» к центру).
    return img * (1 + 0.08 * peak)


def compose_cut(kind: str, a_img: np.ndarray, b_img: np.ndarray, j: int, n: int, *, direction: str = "left",
                color: Sequence[float] = (108.0, 92.0, 255.0)) -> np.ndarray:
    """Кадр ``j`` окна перехода склейки ``kind`` (whippan / colorfill / blurswap / dipblack / zoomblur)."""
    # Первый кадр окна — ровно A, последний — ровно B: стыки с частями без скачка.
    p = cut_progress(j, n)
    if kind == "whippan":
        return whippan(a_img, b_img, p, direction)
    if kind == "colorfill":
        return colorfill(a_img, b_img, p, direction, color)
    if kind == "blurswap":
        return blurswap(a_img, b_img, p)
    if kind == "dipblack":
        return dipblack(a_img, b_img, j, n)
    if kind == "zoomblur":
        return zoomblur(a_img, b_img, p)
    raise ValueError(f"нет перехода склейки «{kind}»")
