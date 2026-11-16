"""Поза плоскости слова: ограниченная модель, отбраковка кадров, сглаживание и проверка «не плывёт, не крутится,
не переворачивается» (для app/surface_track.py, слой ``tracked_text``).

Владелец 16.11 на первую пробу: «текст переворачивается» — слово на колонне легло боком и крутилось вслед за
дрейфом трека. Отсюда правила:

* **Модель.** Подобие (сдвиг, поворот, масштаб) по точкам поверхности — основа; перспектива и перекос сверх
  подобия — только поправкой к углам не больше ``PERSP_CAP`` размера четырёхугольника (``constrain``).
* **Отбраковка кадра** (``reject_reason``): ориентация перевернулась (обход углов, знак определителя линейной
  части), четырёхугольник невыпуклый, ребро схлопнулось, скачок масштаба или поворота от прошлого кадра, поворот
  от кадра-семени больше ``MAX_ROT_DEG``. Отбракованный кадр — прогноз (последняя поза + скорость), несколько
  подряд — слово гаснет (статус lost, движок плавно гасит видимость).
* **Сглаживание** (``smooth_poses``) — без запаздывания (Калман вперёд + RTS назад, app/tracking_normalize):
  центр — слегка, масштаб — сильнее, поворот и остаток перспективы — сильно: слово «приклеено», а не плавает.
* **Проверка** (``check_surface``): по показанным кадрам — перевороты (обход/выпуклость), угол текста на экране
  (с поворотом слоя; боком > ``TEXT_ANGLE_MAX`` — провал), разброс и шаг поворота, дрожь углов (px готового кадра).
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Sequence

import numpy as np

PERSP_CAP = 0.12  # перспектива сверх подобия — не больше этой доли размера четырёхугольника на угол
# Поворот подобия от кадра-семени. Камера игры не кренится, но стена, уходящая вглубь, при движении мимо неё
# заметно меняет наклон краёв (перспектива) — 40° ещё возможны, больше — дрейф трека.
MAX_ROT_DEG = 40.0
ROT_STEP_DEG = 1.5  # допуск поворота за кадр (+ скорость ниже)
ROT_RATE_DEG = 45.0  # градусов в секунду — предел скорости поворота
SCALE_STEP = 1.12  # масштаб за кадр
MIN_EDGE_SHARE = 0.35  # ребро не короче этой доли своего в кадре-семени (с поправкой на масштаб)

# Проверка (пороги провала).
# Текст вдоль стены в глубину может идти наклонно (до ~50°, «LIKE A» на скале у референса); боком (≈90°) и вверх
# ногами — провал.
TEXT_ANGLE_MAX = 60.0
ROT_STEP_FAIL = 2.5  # градусов за кадр (сглаженный трек)
JITTER_FAIL_PX = 2.0  # дрожь углов, px кадра 1080 по ширине


def area(q: np.ndarray) -> float:
    x, y = q[:, 0], q[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def convex_same_winding(q: np.ndarray, sign: float) -> bool:
    """Выпуклый и с тем же обходом, что у семени (``sign`` — знак его площади)."""
    if not np.all(np.isfinite(q)):
        return False
    for i in range(4):
        a, b, c = q[i], q[(i + 1) % 4], q[(i + 2) % 4]
        cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
        if cross * sign <= 0:
            return False
    return area(q) * sign > 0


def order_quad(q: np.ndarray) -> np.ndarray:
    """Углы по часовой (y вниз) с левого верхнего: как бы их ни задали, обход у всех семян один."""
    q = np.asarray(q, np.float64).reshape(4, 2)
    c = q.mean(axis=0)
    ang = np.arctan2(q[:, 1] - c[1], q[:, 0] - c[0])
    q = q[np.argsort(ang)]  # по возрастанию угла при y вниз — по часовой на экране
    # «Левый верхний» — угол, ребро из которого (по часовой) ближе всего к направлению +x: это база строки текста.
    # (min(x+y) ошибался у стены, уходящей вглубь: верхнее ребро под 40° — и текст ложился боком.)
    edges = np.roll(q, -1, axis=0) - q
    cos = edges[:, 0] / np.maximum(1e-9, np.linalg.norm(edges, axis=1))
    start = int(np.argmax(cos))
    return np.roll(q, -start, axis=0)


def pose(q0: np.ndarray, q: np.ndarray) -> tuple[np.ndarray, float, float, np.ndarray]:
    """Подобие семя → кадр по углам (Прокруст): (центр, масштаб, угол рад, остаток в координатах семени)."""
    c0 = q0.mean(axis=0)
    p = q0 - c0
    c = q.mean(axis=0)
    r = q - c
    num = float(np.sum(p[:, 0] * r[:, 1] - p[:, 1] * r[:, 0]))
    den = float(np.sum(p[:, 0] * r[:, 0] + p[:, 1] * r[:, 1]))
    ang = math.atan2(num, den)
    s = math.hypot(num, den) / max(1e-9, float(np.sum(p * p)))
    rot = np.array([[math.cos(ang), -math.sin(ang)], [math.sin(ang), math.cos(ang)]])
    res = (r @ rot) / max(1e-9, s) - p  # обратно в осях семени: R⁻¹·r/s − p
    return c, s, ang, res


def compose(q0: np.ndarray, c: np.ndarray, s: float, ang: float, res: np.ndarray) -> np.ndarray:
    p = q0 - q0.mean(axis=0)
    rot = np.array([[math.cos(ang), -math.sin(ang)], [math.sin(ang), math.cos(ang)]])
    return c + s * ((p + res) @ rot.T)


def constrain(q0: np.ndarray, q_full: np.ndarray, sim: np.ndarray | None, cap_share: float | None = PERSP_CAP) -> np.ndarray:
    """Углы: подобие ``sim`` (2×3 по точкам поверхности; нет — подобие по углам) + перспектива ``q_full`` сверх
    него, урезанная до ``PERSP_CAP`` размера четырёхугольника на угол."""
    if sim is not None:
        base = q0 @ sim[:, :2].T + sim[:, 2]
    else:
        c, s, ang, _ = pose(q0, q_full)
        base = compose(q0, c, s, ang, np.zeros_like(q0))
    if cap_share is None:
        return np.asarray(q_full, np.float64)
    cap = cap_share * math.sqrt(max(1e-9, abs(area(base))))
    d = q_full - base
    n = np.linalg.norm(d, axis=1, keepdims=True)
    d = np.where(n > cap, d * (cap / np.maximum(n, 1e-9)), d)
    return base + d


def reject_reason(q0: np.ndarray, prev_q: np.ndarray, q: np.ndarray, dt: float, gap: int = 1) -> str | None:
    """Почему кадр нельзя принять (None — можно). ``gap`` — кадров с последнего принятого."""
    sign = 1.0 if area(q0) > 0 else -1.0
    if not convex_same_winding(q, sign):
        return "flip"
    _, s, ang, _ = pose(q0, q)
    _, s_prev, ang_prev, _ = pose(q0, prev_q)
    if abs(math.degrees(ang)) > MAX_ROT_DEG:
        return "rotation_limit"
    step = abs(math.degrees((ang - ang_prev + math.pi) % (2 * math.pi) - math.pi))
    if step > ROT_STEP_DEG * gap + ROT_RATE_DEG * dt * gap:
        return "rotation_jump"
    if not (SCALE_STEP ** -gap <= s / max(1e-9, s_prev) <= SCALE_STEP ** gap):
        return "scale_jump"
    e0 = np.linalg.norm(np.roll(q0, -1, axis=0) - q0, axis=1)
    e = np.linalg.norm(np.roll(q, -1, axis=0) - q, axis=1)
    if np.any(e < MIN_EDGE_SHARE * s * e0):
        return "collapse"
    return None


def smooth_poses(q0: np.ndarray, ts: Sequence[float], quads: Sequence[np.ndarray], smooth: float,
                 noise: Sequence[float] | None = None, extra: tuple[float, float, float] = (0.25, 0.45, 0.5)) -> list[np.ndarray]:
    """Сглаживание одного непрерывного куска без запаздывания: центр ``smooth``, масштаб/поворот/остаток сильнее."""
    from app.tracking_normalize import smooth_series

    n = len(quads)
    if n < 3 or smooth <= 0:
        return [np.asarray(q, np.float64) for q in quads]
    poses = [pose(q0, np.asarray(q, np.float64)) for q in quads]
    ts = list(ts)
    nz = list(noise) if noise is not None else None
    cx = smooth_series(ts, [p[0][0] for p in poses], smooth, noise=nz)
    cy = smooth_series(ts, [p[0][1] for p in poses], smooth, noise=nz)
    ls = smooth_series(ts, [math.log(max(1e-9, p[1])) for p in poses], min(1.0, smooth + extra[0]), noise=nz)
    angs = np.unwrap([p[2] for p in poses])
    an = smooth_series(ts, list(angs), min(1.0, smooth + extra[1]), noise=nz)
    res = np.array([p[3].reshape(-1) for p in poses])
    rs = np.array([smooth_series(ts, list(res[:, k]), min(1.0, smooth + extra[2]), noise=nz) for k in range(8)]).T
    return [compose(q0, np.array([cx[i], cy[i]]), math.exp(ls[i]), an[i], rs[i].reshape(4, 2)) for i in range(n)]


def check_surface(samples: Sequence[dict], *, frame: tuple[float, float], rotation: float = 0.0,
                  show: Iterable[float] | None = None, out_width: float = 1080.0) -> dict:
    """Проверка трека под слово (углы ``q`` — доли кадра ``frame`` = (w, h) px, по которым считали).

    По кадрам, где слово видно (ok/weak/pred в окне ``show``): перевороты (обход/выпуклость), угол текста на экране
    (поворот плоскости + ``rotation`` слоя), разброс и наибольший шаг поворота, дрожь углов (RMS второй разности,
    px кадра шириной ``out_width``). ``ok`` False — проба не годится (``fail`` — причины)."""
    w, h = frame
    a, b = (list(show) + [None, None])[:2] if show is not None else (None, None)
    vis = [s for s in samples if s.get("s") in ("ok", "weak", "pred")
           and (a is None or float(s["t"]) >= a - 1e-6) and (b is None or float(s["t"]) <= b + 1e-6)]
    out: dict[str, Any] = {"frames": len(vis), "flips": 0, "text_angle_max": 0.0, "rot_std": 0.0, "rot_step_max": 0.0,
                           "jitter_px": 0.0, "fail": []}
    if len(vis) < 2:
        return {**out, "ok": True}
    qs = [np.asarray(s["q"], np.float64).reshape(4, 2) * [w, h] for s in vis]
    ref = qs[0]
    sign = 1.0 if area(ref) > 0 else -1.0
    flips = sum(1 for q in qs if not convex_same_winding(q, sign))
    # Угол текста на экране: направление верхнего ребра (база строки) + поворот слоя.
    base_angles = [math.degrees(math.atan2((q[1] - q[0])[1] + (q[2] - q[3])[1], (q[1] - q[0])[0] + (q[2] - q[3])[0]))
                   for q in qs]
    text = [((ang + rotation + 180) % 360) - 180 for ang in base_angles]
    rot = np.unwrap(np.radians(base_angles))
    steps = np.degrees(np.abs(np.diff(rot)))
    k = out_width / w
    jit = []
    for i in range(1, len(qs) - 1):
        if abs((float(vis[i + 1]["t"]) - float(vis[i - 1]["t"]))) > 0.12:
            continue  # через пропуск вторая разность — не дрожь
        jit.append(np.linalg.norm(qs[i] - (qs[i - 1] + qs[i + 1]) / 2, axis=1).mean() * k)
    out.update(flips=flips, text_angle_max=round(max(abs(x) for x in text), 2),
               rot_std=round(float(np.degrees(np.std(rot))), 2), rot_step_max=round(float(steps.max()), 2),
               jitter_px=round(float(np.sqrt(np.mean(np.square(jit)))) if jit else 0.0, 2))
    if flips:
        out["fail"].append(f"переворот/излом плоскости в {flips} кадрах")
    if out["text_angle_max"] > TEXT_ANGLE_MAX:
        out["fail"].append(f"текст на экране под {out['text_angle_max']:.0f}° — боком или вверх ногами")
    if out["rot_step_max"] > ROT_STEP_FAIL:
        out["fail"].append(f"поворот прыгает на {out['rot_step_max']:.1f}° за кадр")
    if out["jitter_px"] > JITTER_FAIL_PX:
        out["fail"].append(f"дрожь углов {out['jitter_px']:.1f} px")
    out["ok"] = not out["fail"]
    return out


__all__ = ["constrain", "reject_reason", "smooth_poses", "check_surface", "order_quad", "pose", "compose", "area"]
