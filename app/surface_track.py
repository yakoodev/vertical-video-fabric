"""Трекинг плоскости в кадре: слово «прилипает» к скале, стене, ящику (слой ``tracked_text``).

Чистые функции над кадрами (серые uint8 одного размера, время ``t``) — сервис подаёт кадры основы клипа
(app/world_track.py, тот же граф, что у рендера), тесты — синтетику.

Как держится плоскость (``track_plane``):

1. **Точки поверхности.** В кадре-семени внутри четырёхугольника ``quad`` (доли кадра) — углы Ши–Томаси.
   Координаты точек в кадре-семени — их «координаты на плоскости» (якорь, он не дрейфует).
2. **Кадр за кадром** — пирамидальный Лукас–Канаде вперёд и назад (forward–backward: точка, что не
   вернулась на место ±1 px, выбывает), гомография якорь → кадр по RANSAC (MAGSAC, порог 2 px детекции).
3. **Защита от дрейфа.** Каждый кадр текущий кадр «выпрямляется» найденной гомографией в плоскость
   семени и сверяется с ней же (LK по точкам семени): остаток — поправка гомографии. Накопленная ошибка
   цепочки так не растёт — плоскость всё время сверяется с тем, как она выглядела в кадре-семени.
4. **Проверка на здравый смысл.** Четырёхугольник выпуклый, площадь за кадр меняется не больше чем в
   ``MAX_AREA_STEP`` раз, углы не прыгают дальше ``MAX_JUMP`` кадра, инлаеров ≥ ``MIN_INLIERS``.
   Не прошло — кадр «прогноз» (углы едут с прежней скоростью) до ``MAX_GAP_SEC``, дальше — «потеряна»;
   потерянную ищем заново дескрипторами ORB семени (резкий разворот камеры и обратно).
5. **Точки кончаются** (ушли за край, закрыло) — добираем новые внутри текущего четырёхугольника, их
   якорь — обратная гомография.
6. **Сглаживание** лёгкое, симметричное (3 кадра), только внутри непрерывных «хороших» кусков: сильное
   сглаживание заставило бы слово «плавать» на рывках камеры.

Результат — ``{samples: [{t, q: [x0,y0,…,x3,y3], c, s, e}], …}``: углы плоскости в долях кадра,
уверенность ``c`` 0..1, статус ``s`` ok/weak/pred/lost, ошибка ``e`` (RMS перепроекции инлаеров, px детекции).

``suggest_surfaces`` — где в кадре ровная фактурная поверхность под слово: сетка окон у центра,
фактура (углы), «плоскость» (точки окна за ±0.5 с ложатся одной гомографией), не небо и не HUD.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Sequence

import numpy as np

from app import surface_pose as P

MIN_INLIERS = 10
MIN_POINTS = 14
MAX_POINTS = 260
REFILL_SHARE = 0.55
FB_MAX_PX = 1.0
RANSAC_PX = 2.0
MAX_AREA_STEP = 1.35
MAX_JUMP = 0.22  # доля диагонали кадра за кадр
MAX_GAP_SEC = 0.3
REACQUIRE_MIN = 14
OK_CONF = 0.5
WEAK_CONF = 0.25
CORRECT_MAX_PX = 6.0
ECC_MIN = 0.9  # корреляция ECC, с которой гладкая поверхность считается найденной
MIN_FLAT = 0.7  # доля точек окна, что ложатся одной гомографией по соседним кадрам
LK_WIN = (21, 21)
LK_LEVELS = 4


def _cv2():
    import cv2  # noqa: PLC0415 - тяжёлый импорт только там, где трекают

    return cv2


def _lk_params() -> dict:
    cv2 = _cv2()
    return {"winSize": LK_WIN, "maxLevel": LK_LEVELS,
            "criteria": (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01)}


def to_gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return img if img.dtype == np.uint8 else np.clip(img, 0, 255).astype(np.uint8)
    return _cv2().cvtColor(img, _cv2().COLOR_BGR2GRAY)


# ---------------------------------------------------------------- геометрия


def quad_px(quad: Sequence[float] | Sequence[Sequence[float]], w: int, h: int) -> np.ndarray:
    """Четырёхугольник (доли кадра: 4 точки или 8 чисел) → px ``(4, 2)`` float32."""
    flat = np.asarray(quad, dtype=np.float64).reshape(-1)
    if flat.size != 8 or not np.all(np.isfinite(flat)):
        raise ValueError("quad — 4 точки [[x, y], …] в долях кадра")
    pts = flat.reshape(4, 2) * np.array([w, h], dtype=np.float64)
    return pts.astype(np.float32)


def rect_quad(x: float, y: float, w: float, h: float, angle: float = 0.0) -> list[list[float]]:
    """Прямоугольник центр (x, y), размер (w, h) в долях кадра, поворот angle° → 4 угла (по часовой с левого верхнего).
    Поворот считается в px квадратного кадра — у 9:16 ширина и высота в долях разные, это учитывает вызывающий."""
    a = math.radians(angle)
    c, s = math.cos(a), math.sin(a)
    out = []
    for dx, dy in ((-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5)):
        px, py = dx * w, dy * h
        out.append([round(x + px * c - py * s, 5), round(y + px * s + py * c, 5)])
    return out


def apply_h(H: np.ndarray, pts: np.ndarray) -> np.ndarray:
    pts = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
    hom = np.hstack([pts, np.ones((len(pts), 1))]) @ H.T
    w = hom[:, 2:3]
    w = np.where(np.abs(w) < 1e-9, 1e-9, w)
    return hom[:, :2] / w


def _area(q: np.ndarray) -> float:
    x, y = q[:, 0], q[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def is_convex(q: np.ndarray) -> bool:
    signs = []
    for i in range(4):
        a, b, c = q[i], q[(i + 1) % 4], q[(i + 2) % 4]
        cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
        signs.append(cross > 0)
    return all(signs) or not any(signs)


def _sane(prev_q: np.ndarray, q: np.ndarray, diag: float, gap: int = 1) -> bool:
    if not np.all(np.isfinite(q)) or not is_convex(q):
        return False
    a0, a1 = abs(_area(prev_q)), abs(_area(q))
    if a0 < 1e-6 or a1 < 1e-6:
        return False
    step = MAX_AREA_STEP ** max(1, gap)
    if not (1 / step <= a1 / a0 <= step):
        return False
    return float(np.max(np.linalg.norm(q - prev_q, axis=1))) <= MAX_JUMP * diag * max(1, gap)


def _mask_poly(shape: tuple[int, int], q: np.ndarray, shrink: float = 0.0) -> np.ndarray:
    cv2 = _cv2()
    mask = np.zeros(shape, np.uint8)
    poly = q.copy()
    if shrink:
        c = poly.mean(axis=0)
        poly = c + (poly - c) * (1 - shrink)
    cv2.fillConvexPoly(mask, np.round(poly).astype(np.int32), 255)
    return mask


def _features(gray: np.ndarray, mask: np.ndarray, n: int, min_dist: float) -> np.ndarray:
    cv2 = _cv2()
    pts = cv2.goodFeaturesToTrack(gray, maxCorners=n, qualityLevel=0.004, minDistance=min_dist, mask=mask,
                                  blockSize=5)
    return np.zeros((0, 2), np.float32) if pts is None else pts.reshape(-1, 2).astype(np.float32)


def _lk_fb(prev: np.ndarray, cur: np.ndarray, pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Лукас–Канаде вперёд и назад: (новые точки, годные)."""
    cv2 = _cv2()
    if len(pts) == 0:
        return pts, np.zeros(0, bool)
    p0 = pts.reshape(-1, 1, 2).astype(np.float32)
    p1, st1, _ = cv2.calcOpticalFlowPyrLK(prev, cur, p0, None, **_lk_params())
    p0r, st2, _ = cv2.calcOpticalFlowPyrLK(cur, prev, p1, None, **_lk_params())
    fb = np.linalg.norm((p0 - p0r).reshape(-1, 2), axis=1)
    good = (st1.reshape(-1) == 1) & (st2.reshape(-1) == 1) & (fb < FB_MAX_PX)
    h, w = cur.shape[:2]
    p1 = p1.reshape(-1, 2)
    good &= (p1[:, 0] >= 0) & (p1[:, 0] < w) & (p1[:, 1] >= 0) & (p1[:, 1] < h)
    return p1, good


def _homography(src: np.ndarray, dst: np.ndarray) -> tuple[np.ndarray | None, np.ndarray]:
    cv2 = _cv2()
    if len(src) < 4:
        return None, np.zeros(len(src), bool)
    method = getattr(cv2, "USAC_MAGSAC", cv2.RANSAC)
    H, inl = cv2.findHomography(src.reshape(-1, 1, 2), dst.reshape(-1, 1, 2), method, RANSAC_PX,
                                maxIters=2000, confidence=0.995)
    if H is None or inl is None:
        return None, np.zeros(len(src), bool)
    inl = inl.reshape(-1).astype(bool)
    if inl.sum() >= 8:  # уточнение по всем инлаерам — наименьшие квадраты
        H2, _ = cv2.findHomography(src[inl].reshape(-1, 1, 2), dst[inl].reshape(-1, 1, 2), 0)
        if H2 is not None:
            H = H2
    return H / H[2, 2], inl


def _rms(H: np.ndarray, src: np.ndarray, dst: np.ndarray) -> float:
    if len(src) == 0:
        return 99.0
    return float(np.sqrt(np.mean(np.sum((apply_h(H, src) - dst) ** 2, axis=1))))


def _confidence(inliers: int, total: int, err: float) -> float:
    if total <= 0:
        return 0.0
    count = min(1.0, inliers / 40.0)
    share = inliers / max(1, total)
    return round(float(count * (0.4 + 0.6 * share) * math.exp(-max(0.0, err - 0.4) / 1.6)), 3)


# ---------------------------------------------------------------- плоскость: один проход


class _PlaneRun:
    """Проход в одну сторону от кадра-семени. Хранит якорные точки (координаты в кадре-семени)."""

    def __init__(self, seed_gray: np.ndarray, quad: np.ndarray, *, correct: bool = True):
        cv2 = _cv2()
        self.cv2 = cv2
        self.seed = seed_gray
        self.h, self.w = seed_gray.shape[:2]
        self.diag = math.hypot(self.w, self.h)
        self.quad0 = quad.astype(np.float64)
        side = math.sqrt(max(1.0, abs(_area(self.quad0))))
        self.min_dist = max(3.0, min(9.0, side / 18))
        mask = _mask_poly((self.h, self.w), self.quad0, shrink=0.04)
        pts = _features(seed_gray, mask, MAX_POINTS, self.min_dist)
        if len(pts) < MIN_POINTS:
            raise ValueError(f"на поверхности мало фактуры: {len(pts)} точек (нужно {MIN_POINTS}+) — "
                             "выбери место с рисунком (камни, швы, надписи), не небо и не гладкую стену")
        self.anchor = pts.astype(np.float64)  # координаты на плоскости (кадр-семя)
        self.cur = pts.astype(np.float32)
        self.n0 = len(pts)
        self.H = np.eye(3)
        self.prev_gray = seed_gray
        self.vel = np.zeros((4, 2))
        self.missed = 0.0
        self.correct = correct
        # Опорные точки для сверки с семенем — сетка внутри четырёхугольника + углы.
        self.ref_pts = pts.astype(np.float32)
        self.orb = None
        self.rejects: dict[str, int] = {}

    def quad(self, H: np.ndarray | None = None) -> np.ndarray:
        return apply_h(self.H if H is None else H, self.quad0)

    def _refill(self, gray: np.ndarray) -> None:
        if len(self.cur) >= max(MIN_POINTS * 2, self.n0 * REFILL_SHARE):
            return
        q = self.quad()
        mask = _mask_poly((self.h, self.w), q, shrink=0.04)
        for x, y in self.cur:
            self.cv2.circle(mask, (int(round(x)), int(round(y))), int(self.min_dist), 0, -1)
        new = _features(gray, mask, MAX_POINTS - len(self.cur), self.min_dist)
        if len(new) == 0:
            return
        try:
            Hinv = np.linalg.inv(self.H)
        except np.linalg.LinAlgError:
            return
        self.anchor = np.vstack([self.anchor, apply_h(Hinv, new)])
        self.cur = np.vstack([self.cur, new]).astype(np.float32)

    def _correct(self, gray: np.ndarray, H: np.ndarray) -> np.ndarray:
        """Сверка с семенем: текущий кадр, выпрямленный в плоскость семени, против самого семени."""
        cv2 = self.cv2
        q = apply_h(H, self.quad0)
        x0, y0 = np.floor(self.quad0.min(axis=0)).astype(int) - 24
        x1, y1 = np.ceil(self.quad0.max(axis=0)).astype(int) + 24
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(self.w, x1), min(self.h, y1)
        if x1 - x0 < 16 or y1 - y0 < 16 or not np.all(np.isfinite(q)):
            return H
        # Выпрямленный кадр в окне семени: пиксель (u, v) семени ← кадр в H·(u, v).
        T = np.array([[1, 0, x0], [0, 1, y0], [0, 0, 1]], dtype=np.float64)
        rect = cv2.warpPerspective(gray, H @ T, (x1 - x0, y1 - y0),
                                   flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REPLICATE)
        seed = self.seed[y0:y1, x0:x1]
        inside = (self.ref_pts[:, 0] > x0 + 6) & (self.ref_pts[:, 0] < x1 - 6) & \
                 (self.ref_pts[:, 1] > y0 + 6) & (self.ref_pts[:, 1] < y1 - 6)
        ref = self.ref_pts[inside] - np.array([x0, y0], np.float32)
        if len(ref) < MIN_INLIERS:
            return H
        # Видимые в кадре: точки, что проецируются внутрь кадра.
        proj = apply_h(H, ref + np.array([x0, y0]))
        vis = (proj[:, 0] > 2) & (proj[:, 0] < self.w - 2) & (proj[:, 1] > 2) & (proj[:, 1] < self.h - 2)
        ref = ref[vis]
        if len(ref) < MIN_INLIERS:
            return H
        moved, good = _lk_fb(seed, rect, ref)
        if good.sum() < MIN_INLIERS:
            return H
        C, inl = _homography(ref[good].astype(np.float64), moved[good].astype(np.float64))
        if C is None or inl.sum() < MIN_INLIERS:
            return H
        # C: семя (окно) → выпрямленный кадр (окно); поправка в координатах семени.
        Cw = T @ C @ np.linalg.inv(T)
        shift = np.max(np.linalg.norm(apply_h(Cw, self.quad0) - self.quad0, axis=1))
        if shift > CORRECT_MAX_PX or shift < 0.05:
            return H
        return H @ Cw

    def step(self, gray: np.ndarray, dt: float) -> dict:
        prev_q = self.quad()
        status, conf, err = "lost", 0.0, 99.0
        if len(self.cur) >= 4:
            moved, good = _lk_fb(self.prev_gray, gray, self.cur)
            anchor, cur = self.anchor[good], moved[good]
            H, inl = _homography(anchor, cur.astype(np.float64))
            gap = 1 + int(round(self.missed / max(dt, 1e-3)))
            why = None
            if H is None or inl.sum() < MIN_INLIERS:
                self.rejects["few_points"] = self.rejects.get("few_points", 0) + 1
            if H is not None and inl.sum() >= MIN_INLIERS:
                if self.correct:
                    H = self._correct(gray, H)
                # Ограниченная модель: подобие по точкам + урезанная перспектива (app/surface_pose.py).
                H = self._constrain(H, anchor[inl], cur[inl].astype(np.float64))
                why = None if H is not None else "degenerate"
                if H is not None and not _sane(prev_q, apply_h(H, self.quad0), self.diag, gap):
                    why = "jump"
                if why is None:
                    why = P.reject_reason(self.quad0, prev_q, apply_h(H, self.quad0), dt, gap)
                if why is not None:
                    self.rejects[why] = self.rejects.get(why, 0) + 1
            if H is not None and inl.sum() >= MIN_INLIERS and why is None:
                err = _rms(H, anchor[inl], cur[inl].astype(np.float64))
                conf = _confidence(int(inl.sum()), len(self.anchor), err)
                q = apply_h(H, self.quad0)
                if dt > 0 and self.missed <= 0:
                    self.vel = 0.5 * self.vel + 0.5 * (q - prev_q) / dt
                self.H = H
                self.anchor, self.cur = anchor[inl], cur[inl].astype(np.float32)
                # Совсем слабое совпадение ведёт трек дальше, но слово по нему не показываем (как прогноз).
                status = "ok" if conf >= OK_CONF else ("weak" if conf >= WEAK_CONF else "pred")
                self.missed = 0.0
            else:
                self.anchor, self.cur = anchor, cur.astype(np.float32)
        if status == "lost" and len(self.cur) < MIN_POINTS * 2:
            # Точек мало (гладкое дерево, штукатурка) — прямое выравнивание ECC с семенем от прежнего места.
            got = self._reacquire_ecc(gray)
            if got is not None and P.reject_reason(self.quad0, prev_q, apply_h(got[0], self.quad0), dt,
                                                   1 + int(round(self.missed / max(dt, 1e-3)))) is None:
                q = apply_h(got[0], self.quad0)
                if dt > 0 and self.missed <= 0:
                    self.vel = 0.5 * self.vel + 0.5 * (q - prev_q) / dt
                self.H, conf = got
                status, self.missed = "weak", 0.0
                self.rejects["ecc_used"] = self.rejects.get("ecc_used", 0) + 1  # не отказ — шаг по ECC
                self._reseed_points(gray)
        if status == "lost":
            self.missed += dt
            if self.missed <= MAX_GAP_SEC:
                status = "pred"
                self.vel *= 0.6  # прогноз гаснет: лучше застыть, чем улететь
                q = prev_q + self.vel * dt
                H = self._quad_h(q)
                if H is not None:
                    self.H = H
            else:
                got = self._reacquire(gray)
                if got is not None and P.reject_reason(self.quad0, prev_q, apply_h(got[0], self.quad0), dt,
                                                       1 + int(round(self.missed / max(dt, 1e-3)))) in (
                        "flip", "rotation_limit", "collapse"):
                    got = None  # нашлось что-то перевёрнутое или боком — не верим
                if got is not None:
                    self.H, conf = got
                    status, self.missed = "weak", 0.0
                    self._reseed_points(gray)
        else:
            self._refill(gray)
        self.prev_gray = gray
        return {"q": self.quad(), "c": conf if status in ("ok", "weak") else 0.0, "s": status,
                "e": round(err, 3) if err < 99 else None}

    def _constrain(self, H: np.ndarray, src: np.ndarray, dst: np.ndarray) -> np.ndarray | None:
        """Гомография → подобие по точкам (``estimateAffinePartial2D``) + перспектива, урезанная до PERSP_CAP."""
        sim = None
        if len(src) >= 3:
            sim, _ = self.cv2.estimateAffinePartial2D(src.reshape(-1, 1, 2), dst.reshape(-1, 1, 2),
                                                      method=self.cv2.LMEDS)
        q = P.constrain(self.quad0, apply_h(H, self.quad0), sim)
        return self._quad_h(q)

    def _quad_h(self, q: np.ndarray) -> np.ndarray | None:
        H = self.cv2.getPerspectiveTransform(self.quad0.astype(np.float32), q.astype(np.float32))
        return None if H is None else H

    def _reseed_points(self, gray: np.ndarray) -> None:
        q = self.quad()
        mask = _mask_poly((self.h, self.w), q, shrink=0.04)
        new = _features(gray, mask, MAX_POINTS, self.min_dist)
        if len(new) < 4:
            self.anchor, self.cur = np.zeros((0, 2)), np.zeros((0, 2), np.float32)
            return
        self.anchor = apply_h(np.linalg.inv(self.H), new)
        self.cur = new.astype(np.float32)

    def _reacquire(self, gray: np.ndarray) -> tuple[np.ndarray, float] | None:
        """Потерянную плоскость — дескрипторами семени (камера ушла и вернулась, меню закрыло кадр): SIFT, если
        есть (на дереве и камне держится лучше ORB), иначе ORB; поиск — в окне вокруг последнего места."""
        cv2 = self.cv2
        if self.orb is None:
            det = cv2.SIFT_create(nfeatures=800, contrastThreshold=0.01) if hasattr(cv2, "SIFT_create") else cv2.ORB_create(nfeatures=600,
                                                                                                    fastThreshold=10)
            norm = cv2.NORM_L2 if hasattr(cv2, "SIFT_create") else cv2.NORM_HAMMING
            mask = _mask_poly((self.h, self.w), self.quad0)
            kp, des = det.detectAndCompute(self.seed, mask)
            self.orb = (det, norm, kp, des)
        det, norm, kp0, des0 = self.orb
        if des0 is None or len(kp0) < REACQUIRE_MIN:
            return self._reacquire_ecc(gray)
        q_last = self.quad()
        c = q_last.mean(axis=0)
        win = _mask_poly((self.h, self.w), c + (q_last - c) * 2.2)
        kp1, des1 = det.detectAndCompute(gray, win)
        if des1 is None or len(kp1) < REACQUIRE_MIN:
            return self._reacquire_ecc(gray)
        matches = cv2.BFMatcher(norm).knnMatch(des0, des1, k=2)
        good = [m for m, *rest in matches if rest and m.distance < 0.75 * rest[0].distance]
        if len(good) < REACQUIRE_MIN:
            return self._reacquire_ecc(gray)
        src = np.float64([kp0[m.queryIdx].pt for m in good])
        dst = np.float64([kp1[m.trainIdx].pt for m in good])
        H, inl = _homography(src, dst)
        if H is None or inl.sum() < REACQUIRE_MIN:
            return self._reacquire_ecc(gray)
        H = self._constrain(H, src[inl], dst[inl])
        if H is None:
            return self._reacquire_ecc(gray)
        q = apply_h(H, self.quad0)
        if not is_convex(q) or abs(_area(q)) < 0.05 * abs(_area(self.quad0)):
            return self._reacquire_ecc(gray)
        err = _rms(H, src[inl], dst[inl])
        return H, _confidence(int(inl.sum()), len(good), err)

    def _reacquire_ecc(self, gray: np.ndarray) -> tuple[np.ndarray, float] | None:
        """Гладкая поверхность (дерево, штукатурка), точек мало: прямое выравнивание ECC — участок семени против
        кадра, от последнего места, в половинном размере. Корреляция < ``ECC_MIN`` — не нашлась."""
        cv2 = self.cv2
        x0, y0 = np.floor(self.quad0.min(axis=0)).astype(int)
        x1, y1 = np.ceil(self.quad0.max(axis=0)).astype(int)
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(self.w, x1), min(self.h, y1)
        if x1 - x0 < 24 or y1 - y0 < 16:
            return None
        k = 0.5
        S = np.diag([k, k, 1.0])
        T = np.array([[1, 0, x0], [0, 1, y0], [0, 0, 1]], np.float64)
        tpl = cv2.resize(self.seed[y0:y1, x0:x1], None, fx=k, fy=k, interpolation=cv2.INTER_AREA).astype(np.float32)
        img = cv2.resize(gray, None, fx=k, fy=k, interpolation=cv2.INTER_AREA).astype(np.float32)
        # Маска ECC — по кадру (inputMask), не по шаблону: окно семени берём целиком (рамка четырёхугольника).
        # Варп: точка шаблона (полуразмер, от угла окна) → точка кадра (полуразмер).
        init = (S @ self.H @ T @ np.linalg.inv(S)).astype(np.float32)
        init /= init[2, 2]
        try:
            cc, warp = cv2.findTransformECC(tpl, img, init, cv2.MOTION_HOMOGRAPHY,
                                            (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 60, 1e-5), None, 5)
        except cv2.error:
            return None
        if cc < ECC_MIN:
            return None
        H = np.linalg.inv(S) @ warp.astype(np.float64) @ S @ np.linalg.inv(T)
        H /= H[2, 2]
        q = P.constrain(self.quad0, apply_h(H, self.quad0), None)
        H = self._quad_h(q)
        if H is None:
            return None
        return H, round(float(min(0.6, (cc - ECC_MIN) / (1 - ECC_MIN) + 0.3)), 3)


# ---------------------------------------------------------------- плоскость: весь трек


def _smooth_runs(samples: list[dict], amount: float, quad0: np.ndarray | None = None) -> None:
    """Сглаживание позы (app/surface_pose.smooth_poses) по непрерывным видимым кускам (ok/weak/pred); прогноз
    тянет слабее. Без ``quad0`` — прежнее симметричное по углам."""
    if quad0 is None:
        _smooth_corners(samples, amount)
        return
    i = 0
    while i < len(samples):
        if samples[i]["s"] not in ("ok", "weak", "pred"):
            i += 1
            continue
        j = i
        while j < len(samples) and samples[j]["s"] in ("ok", "weak", "pred"):
            j += 1
        run = samples[i:j]
        sm = P.smooth_poses(quad0, [s["t"] for s in run], [s["q"] for s in run], amount,
                            noise=[1.0 if s["s"] == "ok" else 2.0 if s["s"] == "weak" else 6.0 for s in run])
        for s, q in zip(run, sm):
            s["q"] = q
        i = j


def _smooth_corners(samples: list[dict], amount: float) -> None:
    """Симметричное сглаживание углов [a/2, 1−a, a/2] внутри непрерывных ok/weak-кусков (на месте)."""
    if amount <= 0:
        return
    good = [s["s"] in ("ok", "weak") for s in samples]
    qs = [np.asarray(s["q"], np.float64) for s in samples]
    for i in range(1, len(samples) - 1):
        if good[i - 1] and good[i] and good[i + 1]:
            samples[i]["q_s"] = (1 - amount) * qs[i] + amount / 2 * (qs[i - 1] + qs[i + 1])
    for s in samples:
        if "q_s" in s:
            s["q"] = s.pop("q_s")


def _bound_predictions(samples: list[dict]) -> None:
    """Прогноз показываем только в коротком пропуске МЕЖДУ увиденными кадрами: прогноз в начале/конце трека
    (поверхность ещё/уже не видна) — «потеряна», иначе слово висит в выдуманном месте."""
    seen = [s["s"] in ("ok", "weak") for s in samples]
    i = 0
    while i < len(samples):
        if samples[i]["s"] != "pred":
            i += 1
            continue
        j = i
        while j < len(samples) and samples[j]["s"] == "pred":
            j += 1
        if not (i > 0 and seen[i - 1] and j < len(samples) and seen[j]):
            for k in range(i, j):
                samples[k]["s"] = "lost"
        i = j


def track_plane(frames: Sequence[dict], seed_index: int, quad: Any, *, smooth: float = 0.35,
                correct: bool = True) -> dict:
    """Плоскость по кадрам ``[{t, img}]`` (серые или BGR, один размер) от кадра ``seed_index`` вперёд и назад.

    ``quad`` — 4 угла в долях кадра семени. Ответ — ``{samples, found, ok_share, err_px, lost, seed_t}``;
    углы ``q`` в долях кадра. ValueError — на поверхности нечего держать."""
    if not frames:
        raise ValueError("кадров нет")
    seed_index = max(0, min(len(frames) - 1, int(seed_index)))
    grays = [to_gray(f["img"]) for f in frames]
    h, w = grays[0].shape[:2]
    q0 = P.order_quad(quad_px(quad, w, h)).astype(np.float32)
    if not is_convex(q0.astype(np.float64)) or abs(_area(q0.astype(np.float64))) < 64:
        raise ValueError("quad — выпуклый четырёхугольник заметного размера (углы по кругу)")
    out: dict[int, dict] = {seed_index: {"q": q0.astype(np.float64), "c": 1.0, "s": "ok", "e": 0.0}}
    rejects: dict[str, int] = {}
    for direction in (1, -1):
        run = _PlaneRun(grays[seed_index], q0, correct=correct)
        run.rejects = rejects
        lost_for = 0.0
        i = seed_index + direction
        while 0 <= i < len(frames):
            dt = abs(float(frames[i]["t"]) - float(frames[i - direction]["t"]))
            res = run.step(grays[i], dt)
            out[i] = res
            lost_for = lost_for + dt if res["s"] == "lost" else 0.0
            if lost_for > 1.5:  # давно потеряна — дальше не ищем, остаток помечается lost
                for k in range(i + direction, len(frames) if direction > 0 else -1, direction):
                    out[k] = {"q": res["q"], "c": 0.0, "s": "lost", "e": None}
                break
            i += direction
    samples = []
    for i, f in enumerate(frames):
        r = out[i]
        samples.append({"t": round(float(f["t"]), 4), "q": np.asarray(r["q"], np.float64), "c": r["c"], "s": r["s"],
                        "e": r.get("e")})
    _bound_predictions(samples)
    _smooth_runs(samples, float(smooth), q0.astype(np.float64))
    # После сглаживания — та же проверка позы: что всё же легло боком или перевернулось, не показываем.
    sign = 1.0 if P.area(q0.astype(np.float64)) > 0 else -1.0
    for s in samples:
        if s["s"] != "lost" and not P.convex_same_winding(np.asarray(s["q"], np.float64), sign):
            s["s"] = "lost"
    errs = [s["e"] for s in samples if s["s"] == "ok" and s["e"] is not None and s is not samples[seed_index]]
    for s in samples:
        q = np.asarray(s["q"]) / np.array([w, h])
        s["q"] = [round(float(v), 5) for v in q.reshape(-1)]
    n = len(samples)
    ok = sum(1 for s in samples if s["s"] == "ok")
    found = sum(1 for s in samples if s["s"] in ("ok", "weak"))
    return {
        "samples": samples, "seed_t": samples[seed_index]["t"], "frame": {"dw": w, "dh": h},
        "ok_share": round(ok / n, 3), "found": round(found / n, 3),
        "err_px": {"median": round(float(np.median(errs)), 3) if errs else None,
                   "p95": round(float(np.percentile(errs, 95)), 3) if errs else None},
        "lost": _runs([s for s in samples], ("lost", "pred")),
        # Отбракованные кадры по причинам (переворот, поворот, скачок…) и проверка позы трека (app/surface_pose.py).
        "rejects": rejects,
        "check": P.check_surface(samples, frame=(w, h)),
    }


def _runs(samples: list[dict], statuses: tuple[str, ...]) -> list[list[float]]:
    out: list[list[float]] = []
    prev_in = False
    for s in samples:
        inside = s["s"] in statuses
        if inside and prev_in:
            out[-1][1] = s["t"]
        elif inside:
            out.append([s["t"], s["t"]])
        prev_in = inside
    return [[round(a, 3), round(b, 3)] for a, b in out]


# ---------------------------------------------------------------- где поставить слово


DEFAULT_AVOID = ({"x": 0.0, "y": 0.0, "w": 1.0, "h": 0.1}, {"x": 0.0, "y": 0.82, "w": 1.0, "h": 0.18})


def _box_overlap(a: dict, b: dict) -> float:
    ix = max(0.0, min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"]))
    iy = max(0.0, min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"]))
    return ix * iy / max(1e-9, a["w"] * a["h"])


def suggest_surfaces(frames: Sequence[dict], index: int, *, aspect: float = 3.0, width: float = 0.5,
                     n: int = 3, avoid: Iterable[dict] = DEFAULT_AVOID, center: tuple[float, float] = (0.5, 0.45)) -> list[dict]:
    """Окна под слово у кадра ``frames[index]``: ``[{x, y, w, h, quad, score, texture, flat, err}]`` (доли кадра).

    ``aspect`` — ширина/высота слова (px кадра), ``width`` — желаемая ширина окна (доля кадра). Плоскость
    проверяется движением: точки окна по соседним кадрам (``frames`` — несколько кадров вокруг ``index``)
    должны ложиться одной гомографией с малой ошибкой."""
    cv2 = _cv2()
    grays = [to_gray(f["img"]) for f in frames]
    g0 = grays[index]
    h, w = g0.shape[:2]
    pts = _features(g0, np.full((h, w), 255, np.uint8), 1500, 4)
    if len(pts) < MIN_POINTS:
        return []
    # Где точки через соседние кадры (цепочкой LK вперёд и назад от index).
    tracks: list[tuple[np.ndarray, np.ndarray]] = []
    for direction in (1, -1):
        cur, alive = pts.copy(), np.ones(len(pts), bool)
        prev = g0
        i = index + direction
        steps = 0
        while 0 <= i < len(grays) and steps < 6:
            moved, good = _lk_fb(prev, grays[i], cur)
            alive &= good
            cur = moved.astype(np.float32)
            prev = grays[i]
            i += direction
            steps += 1
        if steps:
            tracks.append((alive, cur))
    gx = cv2.Sobel(g0, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g0, cv2.CV_32F, 0, 1, ksize=3)
    energy = np.sqrt(gx * gx + gy * gy)
    color = frames[index]["img"]
    hsv = cv2.cvtColor(color, cv2.COLOR_BGR2HSV) if color.ndim == 3 else np.dstack([g0 * 0, g0 * 0, g0])
    bw_px = width * w
    bh_px = bw_px / max(0.5, aspect)
    bh = min(0.3, max(0.05, bh_px / h))
    bw = width
    cands = []
    avoid = list(avoid)
    for cy in np.arange(0.12 + bh / 2, 0.82 - bh / 2 + 1e-6, 0.04):
        for cx in np.arange(0.06 + bw / 2, 0.94 - bw / 2 + 1e-6, 0.04):
            box = {"x": float(cx - bw / 2), "y": float(cy - bh / 2), "w": float(bw), "h": float(bh)}
            if any(_box_overlap(box, a) > 0.15 for a in avoid):
                continue
            x0, y0 = int(box["x"] * w), int(box["y"] * h)
            x1, y1 = int((box["x"] + bw) * w), int((box["y"] + bh) * h)
            inside = (pts[:, 0] >= x0) & (pts[:, 0] < x1) & (pts[:, 1] >= y0) & (pts[:, 1] < y1)
            count = int(inside.sum())
            if count < MIN_POINTS:
                continue
            patch = g0[y0:y1, x0:x1]
            bright = float(patch.mean())
            if bright > 235 or bright < 14:
                continue
            # «Ровная фактура»: доля пикселей с перепадом — небо и гладкая заливка мимо.
            textured = float((energy[y0:y1, x0:x1] > 18).mean())
            flat_scores, errs = [], []
            for alive, moved in tracks:
                sel = inside & alive
                if sel.sum() < MIN_INLIERS:
                    flat_scores.append(0.0)
                    continue
                H, inl = _homography(pts[sel].astype(np.float64), moved[sel].astype(np.float64))
                if H is None:
                    flat_scores.append(0.0)
                    continue
                share = float(inl.sum()) / float(sel.sum())
                errs.append(_rms(H, pts[sel][inl].astype(np.float64), moved[sel][inl].astype(np.float64)))
                flat_scores.append(share)
            flat = min(flat_scores) if flat_scores else 0.5
            err = max(errs) if errs else 1.0
            if flat < MIN_FLAT:  # окно на двух глубинах (край башни и небо за ней) или огонь — не плоскость
                continue
            # Огонь, искры, дым: яркое и насыщенное «живое» — плоскость под ним не держится.
            hot = float((hsv[y0:y1, x0:x1, 2] > 235).mean())
            if hot > 0.12:
                continue
            dist = math.hypot(cx - center[0], (cy - center[1]) * h / w)
            score = (min(1.0, count / 60) * 0.25 + min(1.0, textured / 0.5) * 0.1 + flat ** 2 * 0.45
                     + math.exp(-(dist / 0.35) ** 2) * 0.2) * math.exp(-max(0.0, err - 0.6) / 1.5)
            cands.append({**{k: round(v, 4) for k, v in box.items()}, "score": round(score, 3), "points": count,
                          "texture": round(textured, 3), "flat": round(flat, 3), "err": round(err, 3)})
    cands.sort(key=lambda c: -c["score"])
    picked: list[dict] = []
    for c in cands:
        if all(_box_overlap(c, p) < 0.25 and _box_overlap(p, c) < 0.25 for p in picked):
            c["quad"] = rect_quad(c["x"] + c["w"] / 2, c["y"] + c["h"] / 2, c["w"], c["h"])
            picked.append(c)
        if len(picked) >= n:
            break
    return picked


__all__ = ["track_plane", "suggest_surfaces", "rect_quad", "quad_px", "apply_h", "is_convex", "to_gray"]
