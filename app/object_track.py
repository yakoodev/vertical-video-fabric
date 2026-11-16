"""Трекинг мелкого летящего объекта (граната, снаряд, мяч) и движения камеры — для слоя ``trail``.

Чистые функции над кадрами ``[{t, img}]`` (как app/surface_track.py).

1. **Движение камеры.** Между соседними кадрами — аффинное преобразование фона (LK по углам всего кадра,
   кроме окна объекта, RANSAC). Накопленное ``g`` переводит точку кадра в координаты кадра-семени
   («мир»): след гранаты рисуется в мире, и когда камера поворачивается, уже нарисованная дуга едет
   вместе с фоном, а не за экраном.
2. **Объект.** Шаблон с кадра-семени (``w×h`` вокруг точки), в каждом кадре — поиск в окне вокруг
   прогноза: позиция и скорость в мире (с ускорением, если точек хватает — дуга броска), переведённые в
   кадр. Счёт = нормированная корреляция (cv2.matchTemplate, 3 масштаба: граната улетает и мельчает) +
   «движется против фона» (разность с кадром, сдвинутым по движению камеры) − штраф за даль от прогноза.
   Шаблон слегка обновляется на уверенных кадрах.
3. **Подсказки** ``hints`` — несколько точек ``{t, x, y}``, увиденных глазами (агент на view_frame):
   прогноз идёт по ним (в мире), поиск — в малом окне. Так держится и граната в 15 px на пёстром фоне.
4. Нет совпадения — прогноз до ``MAX_GAP_SEC``, дальше объект потерян (граната упала/взорвалась): трек
   кончается там. Сэмплы: ``{t, x, y, c, s, g}`` — центр в долях кадра, уверенность, статус d/w/p/l и
   аффинная ``g`` (6 чисел, доли кадра) кадр → мир.
"""

from __future__ import annotations

import math
from typing import Any, Sequence

import numpy as np

from app.surface_track import _lk_fb, to_gray

MAX_GAP_SEC = 0.2
STRONG = 0.62
WEAK = 0.4
SCALES = (0.8, 1.0, 1.2)


def _cv2():
    import cv2  # noqa: PLC0415

    return cv2


def _aff3(m: np.ndarray) -> np.ndarray:
    out = np.eye(3)
    out[:2, :] = m
    return out


def global_motion(prev: np.ndarray, cur: np.ndarray, exclude: tuple[float, float, float] | None = None) -> tuple[np.ndarray, float]:
    """Аффинное преобразование фона prev → cur (3×3, px) и доля инлаеров. ``exclude`` — (x, y, r) окно объекта."""
    cv2 = _cv2()
    h, w = prev.shape[:2]
    mask = np.full((h, w), 255, np.uint8)
    # HUD игры стоит на экране — по нему камера «не двигается»: края кадра в счёт не берём.
    mask[: int(h * 0.1)] = 0
    mask[int(h * 0.84):] = 0
    if exclude is not None:
        cv2.circle(mask, (int(exclude[0]), int(exclude[1])), int(exclude[2]), 0, -1)
    pts = cv2.goodFeaturesToTrack(prev, maxCorners=400, qualityLevel=0.01, minDistance=10, mask=mask, blockSize=5)
    if pts is None or len(pts) < 8:
        return np.eye(3), 0.0
    pts = pts.reshape(-1, 2)
    moved, good = _lk_fb(prev, cur, pts)
    if good.sum() < 8:
        return np.eye(3), 0.0
    m, inl = cv2.estimateAffinePartial2D(pts[good], moved[good], method=cv2.RANSAC, ransacReprojThreshold=2.0)
    if m is None:
        return np.eye(3), 0.0
    return _aff3(m), float(inl.sum()) / float(good.sum())


def _apply(M: np.ndarray, x: float, y: float) -> tuple[float, float]:
    v = M @ np.array([x, y, 1.0])
    return float(v[0] / v[2]), float(v[1] / v[2])


def _hint_at(hints: list[tuple[float, float, float]], t: float) -> tuple[float, float] | None:
    """Точка подсказок (мир, px) в момент t: линейно между соседними, за краями — по крайнему отрезку."""
    if not hints:
        return None
    if len(hints) == 1:
        return hints[0][1], hints[0][2]
    for (t0, x0, y0), (t1, x1, y1) in zip(hints, hints[1:]):
        if t <= t1 or (t1, x1, y1) == hints[-1]:
            k = (t - t0) / max(1e-6, t1 - t0)
            return x0 + (x1 - x0) * k, y0 + (y1 - y0) * k
    return hints[-1][1], hints[-1][2]


REST_MIN_PEAK = 0.35  # ширин кадра в секунду: объект действительно летел
REST_SHARE = 0.15
REST_SEC = 0.08


def _at_rest(hist: list[tuple[float, float, float]], width: int, rest: dict) -> bool:
    """Летел и остановился (в мире): скорость упала ниже ``REST_SHARE`` пика на ``REST_SEC``."""
    if len(hist) < 3:
        return False
    (ta, xa, ya), (tb, xb, yb) = hist[-3], hist[-1]
    speed = math.hypot(xb - xa, yb - ya) / max(1e-3, tb - ta) / max(1, width)
    rest["peak"] = max(rest["peak"], speed)
    if rest["peak"] < REST_MIN_PEAK or speed > REST_SHARE * rest["peak"]:
        rest["slow"] = 0.0
        return False
    rest["slow"] += (tb - hist[-2][0])
    return rest["slow"] >= REST_SEC


def _lead_in(out: dict, G: dict, grays: list, ts: list[float], seed: int, lead_in: float, W: int, H: int,
             sample: Any) -> None:
    """Начало броска до кадра-семени (граната вылетела из-за края, из руки): дуга полёта по первым точкам трека
    продолжается назад в мире — до края кадра или ``lead_in`` секунд. Сэмплы со статусом «e» (продолжение)."""
    pts = [(ts[i], *_apply(G[i], out[i]["x"] * W, out[i]["y"] * H)) for i in sorted(out)
           if i >= seed and out[i]["s"] in ("d", "w")][:8]
    if len(pts) < 3:
        return
    tt = np.array([p[0] for p in pts])
    span = tt[-1] - tt[0]
    deg = 2 if len(pts) >= 5 and span >= 0.08 else 1
    cx = np.polyfit(tt - tt[0], [p[1] for p in pts], deg)
    cy = np.polyfit(tt - tt[0], [p[2] for p in pts], deg)
    i = seed - 1
    while i >= 0 and i in G and ts[seed] - ts[i] <= lead_in + 1e-6:
        wx = float(np.polyval(cx, ts[i] - tt[0]))
        wy = float(np.polyval(cy, ts[i] - tt[0]))
        px, py = _apply(np.linalg.inv(G[i]), wx, wy)
        out[i] = {**sample(i, px, py, 0.0, "e")}
        if not (0 <= px < W and 0 <= py < H):
            break  # одна точка за краем — линия доходит до края кадра
        i -= 1


TURN_DEG = 75.0


def _turned(hist: list[tuple[float, float, float]], new: tuple[float, float], width: int) -> bool:
    """Новая точка ломает дугу: направление шага расходится с прежним полётом больше ``TURN_DEG``."""
    if len(hist) < 4:
        return False
    (t0, x0, y0), (t1, x1, y1) = hist[-4], hist[-1]
    vx, vy = x1 - x0, y1 - y0
    if math.hypot(vx, vy) / max(1e-3, t1 - t0) / max(1, width) < REST_MIN_PEAK:
        return False  # почти стоит — направление шумное
    dx, dy = new[0] - x1, new[1] - y1
    if math.hypot(dx, dy) < 1.5:
        return False
    cos = (vx * dx + vy * dy) / (math.hypot(vx, vy) * math.hypot(dx, dy))
    return cos < math.cos(math.radians(TURN_DEG))


class _Object:
    def __init__(self, gray: np.ndarray, x: float, y: float, bw: int, bh: int):
        self.h, self.w = gray.shape[:2]
        self.bw, self.bh = max(6, bw), max(6, bh)
        self.tpl = self._patch(gray, x, y, self.bw, self.bh).astype(np.float32)
        self.hist: list[tuple[float, float, float]] = []  # (t, мир x, мир y) уверенных

    def _patch(self, gray: np.ndarray, x: float, y: float, bw: int, bh: int) -> np.ndarray:
        cv2 = _cv2()
        return cv2.getRectSubPix(gray, (bw, bh), (float(x), float(y)))

    def predict_world(self, t: float) -> tuple[float, float] | None:
        pts = self.hist[-6:]
        if not pts:
            return None
        if len(pts) == 1:
            return pts[0][1], pts[0][2]
        ts = np.array([p[0] for p in pts])
        deg = 2 if len(pts) >= 4 else 1
        out = []
        for k in (1, 2):
            v = np.array([p[k] for p in pts])
            coef = np.polyfit(ts - ts[-1], v, deg)
            out.append(float(np.polyval(coef, t - ts[-1])))
        return out[0], out[1]

    def search(self, gray: np.ndarray, motion: np.ndarray | None, px: float, py: float, radius: float) -> tuple[float, float, float] | None:
        """Лучшее совпадение в окне ``radius`` вокруг (px, py): (x, y, счёт) или None."""
        cv2 = _cv2()
        best = None
        for s in SCALES:
            tw, th = max(5, int(round(self.bw * s))), max(5, int(round(self.bh * s)))
            tpl = cv2.resize(self.tpl, (tw, th), interpolation=cv2.INTER_AREA) if s != 1.0 else self.tpl
            r = int(radius) + max(tw, th)
            x0, y0 = int(px) - r, int(py) - r
            x1, y1 = int(px) + r, int(py) + r
            if x1 <= 0 or y1 <= 0 or x0 >= self.w or y0 >= self.h:
                continue
            cx0, cy0 = max(0, x0), max(0, y0)
            win = gray[cy0:min(self.h, y1), cx0:min(self.w, x1)].astype(np.float32)
            if win.shape[0] <= th or win.shape[1] <= tw:
                continue
            score = cv2.matchTemplate(win, tpl, cv2.TM_CCOEFF_NORMED)
            gy, gx = np.mgrid[0:score.shape[0], 0:score.shape[1]]
            cxs = cx0 + gx + tw / 2.0
            cys = cy0 + gy + th / 2.0
            dist = np.hypot(cxs - px, cys - py) / max(4.0, radius)
            total = score - 0.25 * dist ** 2
            if motion is not None:
                m = motion[cy0:min(self.h, y1), cx0:min(self.w, x1)]
                m = cv2.boxFilter(m, -1, (tw, th), normalize=True)[th // 2: th // 2 + score.shape[0],
                                                                    tw // 2: tw // 2 + score.shape[1]]
                if m.shape == total.shape:
                    total = total + 0.5 * np.clip(m / 40.0, 0, 1)
            iy, ix = np.unravel_index(int(np.argmax(total)), total.shape)
            cand = (float(cxs[iy, ix]), float(cys[iy, ix]), float(score[iy, ix]), float(total[iy, ix]), s)
            if best is None or cand[3] > best[3]:
                best = cand
        if best is None:
            return None
        if best[4] != 1.0 and best[2] >= STRONG:  # объект мельчает/растёт — шаблон за ним
            self.bw = max(5, int(round(self.bw * best[4])))
            self.bh = max(5, int(round(self.bh * best[4])))
            self.tpl = cv2.resize(self.tpl, (self.bw, self.bh), interpolation=cv2.INTER_AREA)
        return best[0], best[1], best[2]

    def update(self, gray: np.ndarray, x: float, y: float) -> None:
        patch = self._patch(gray, x, y, self.bw, self.bh).astype(np.float32)
        self.tpl = 0.8 * self.tpl + 0.2 * patch


def track_point(frames: Sequence[dict], seed_index: int, x: float, y: float, *, w: float = 0.04, h: float | None = None,
                hints: Sequence[dict] | None = None, until_lost: bool = True, back: bool = False,
                stop_at_rest: bool = True, lead_in: float = 0.0) -> dict:
    """Объект по кадрам от ``seed_index`` вперёд (``back`` — и назад). ``x, y, w, h`` — доли кадра.
    Бросок ведётся от вылета вперёд: до вылета граната в руке за краем — назад искать нечего.

    Ответ: ``{samples: [{t, x, y, c, s, g}], found, lost_at, …}``; после потери (граната упала) сэмплов нет
    (``until_lost``) — след кончается там же."""
    cv2 = _cv2()
    if not frames:
        raise ValueError("кадров нет")
    grays = [to_gray(f["img"]) for f in frames]
    H, W = grays[0].shape[:2]
    seed_index = max(0, min(len(frames) - 1, int(seed_index)))
    bw = int(round(w * W))
    bh = int(round((h if h else w * W / H) * H))
    # Движение камеры: g[i] — кадр i → мир (кадр-семя), px.
    G: dict[int, np.ndarray] = {seed_index: np.eye(3)}
    motion_maps: dict[int, np.ndarray] = {}
    for direction in ((1, -1) if back or lead_in > 0 else (1,)):
        i = seed_index + direction
        while 0 <= i < len(frames):
            prev = grays[i - direction]
            A, _share = global_motion(prev, grays[i])  # prev → cur
            G[i] = G[i - direction] @ np.linalg.inv(A)
            if direction > 0:
                warped = cv2.warpAffine(prev, A[:2], (W, H), borderMode=cv2.BORDER_REPLICATE)
                motion_maps[i] = cv2.absdiff(grays[i], warped).astype(np.float32)
            i += direction
    hint_pts: list[tuple[float, float, float]] = []
    t_by_index = [float(f["t"]) for f in frames]
    for hnt in hints or []:
        ht = float(hnt["t"])
        k = int(np.argmin([abs(t - ht) for t in t_by_index]))
        wx, wy = _apply(G[k], float(hnt["x"]) * W, float(hnt["y"]) * H)
        hint_pts.append((ht, wx, wy))
    hint_pts.sort()
    out: dict[int, dict] = {}

    def sample(i: int, px: float, py: float, c: float, s: str) -> dict:
        g = G[i]
        # g в долях кадра: S⁻¹·G·S
        S = np.diag([W, H, 1.0])
        gn = np.linalg.inv(S) @ g @ S
        return {"t": round(t_by_index[i], 4), "x": round(px / W, 5), "y": round(py / H, 5), "c": round(c, 3), "s": s,
                "g": [round(float(v), 6) for v in gn[:2].reshape(-1)]}

    sx, sy = x * W, y * H
    for direction in ((1, -1) if back else (1,)):
        obj = _Object(grays[seed_index], sx, sy, bw, bh)
        wx0, wy0 = _apply(G[seed_index], sx, sy)
        obj.hist.append((t_by_index[seed_index], wx0, wy0))
        out[seed_index] = sample(seed_index, sx, sy, 1.0, "d")
        missed = 0.0
        last = (sx, sy)
        gone = False
        rest: dict = {"peak": 0.0, "slow": 0.0}
        i = seed_index + direction
        while 0 <= i < len(frames):
            t = t_by_index[i]
            if gone:  # объект потерян — дальше сэмплы несут только движение камеры (дуга едет с фоном)
                out[i] = sample(i, last[0], last[1], 0.0, "l")
                i += direction
                continue
            dt = abs(t - t_by_index[i - direction])
            hint = _hint_at(hint_pts, t)
            pred_w = hint if hint is not None else obj.predict_world(t)
            Ginv = np.linalg.inv(G[i])
            if pred_w is None:
                px, py = last
            else:
                px, py = _apply(Ginv, *pred_w)
            speed = 0.0
            if len(obj.hist) >= 2:
                (ta, xa, ya), (tb, xb, yb) = obj.hist[-2], obj.hist[-1]
                speed = math.hypot(xb - xa, yb - ya) / max(1e-3, tb - ta)
            if hint is not None:
                radius = max(bw, bh) * 0.8 + 0.02 * W
            elif len(obj.hist) < 2:  # скорость ещё не знаем — первый шаг броска ищем шире
                radius = max(bw, bh) * 3.0 + 0.05 * W
            else:
                radius = max(bw, bh) * 1.5 + speed * dt * 0.6 + missed * speed
            got = obj.search(grays[i], motion_maps.get(i) if direction > 0 else None, px, py, radius)
            if got is not None and got[2] >= WEAK and stop_at_rest and _turned(obj.hist, _apply(G[i], got[0], got[1]), W):
                # Дуга резко сломалась (ударилась, покатилась, шаблон перепрыгнул на искры) — бросок кончился.
                gone = True
                out[i] = sample(i, last[0], last[1], 0.0, "l")
                i += direction
                continue
            if got is not None and got[2] >= WEAK:
                gx, gy, score = got
                status = "d" if score >= STRONG else "w"
                wx, wy = _apply(G[i], gx, gy)
                obj.hist.append((t, wx, wy))
                if score >= STRONG:
                    obj.update(grays[i], gx, gy)
                out[i] = sample(i, gx, gy, score, status)
                last = (gx, gy)
                missed = 0.0
                if stop_at_rest and _at_rest(obj.hist, W, rest):
                    gone = True  # граната упала и лежит: дальше след не тянем (иначе ползёт по искрам)
            else:
                missed += dt
                if missed > MAX_GAP_SEC or not (0 <= px < W and 0 <= py < H):
                    if until_lost:
                        gone = True
                    out[i] = sample(i, last[0], last[1], 0.0, "l")
                else:
                    out[i] = sample(i, px, py, 0.0, "p")
                    last = (px, py)
            i += direction
    if lead_in > 0 and not back:
        _lead_in(out, G, grays, t_by_index, seed_index, lead_in, W, H, sample)
    order = sorted(out)
    samples = [out[i] for i in order]
    # Прогнозы перед потерей (объект уже пропал) — не след: в «l» (камера остаётся).
    k = len(samples) - 1
    while k >= 0 and samples[k]["s"] in ("p", "l"):
        if samples[k]["s"] == "p":
            samples[k]["s"] = "l"
        k -= 1
    while samples and samples[0]["s"] == "p":
        samples.pop(0)
    seen = [s for s in samples if s["s"] in ("d", "w")]
    found = len(seen) / max(1, len([s for s in samples if s["s"] != "l"]))
    return {"samples": samples, "found": round(found, 3), "frame": {"dw": W, "dh": H},
            "range": [seen[0]["t"], seen[-1]["t"]] if seen else None,
            "lost_at": samples[k + 1]["t"] if 0 <= k + 1 < len(samples) else None,
            "seed_t": round(t_by_index[seed_index], 4)}


__all__ = ["track_point", "global_motion"]
