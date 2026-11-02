"""Режиссура движения 3D-героя без случайных клипов: таймлайн действий с целями.

Чистая математика без bpy/three — её считает и Blender-рендер (``tools/blender/hero_shot.py``), и тесты.

* ``ClipTrack`` — клипы тела по времени с плавными переходами (blend 0.2–0.4 с): в каждый момент —
  список ``(клип, локальное время, вес)``, веса в сумме 1, без рывков.
* ``Curve`` — ключи скаляра/вектора со сглаженной интерполяцией (влияние IK, мимика, свет).
* ``two_bone_ik`` / ``stand_point`` — аналитический IK руки и точка, где встать, чтобы кнопка была
  в зоне досягаемости (рука не тянется в струну и не упирается локтем).
* ``look_angles`` / ``split_gaze`` / ``spring_follow`` — взгляд: глаза прыгают к цели быстро (саккада),
  голова догоняет пружиной с пределами поворота, остаток угла добирают глаза.
* ``blink_times`` / ``blink_amount`` — моргание с живыми интервалами + принудительное на испуге.
* ``stride_speed`` — скорость корня под клип шага: опорная стопа не скользит.
* ``ShotList`` — планы камеры: мягкий наезд внутри плана, жёсткая склейка между планами.
"""
from __future__ import annotations

import math
import random
from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Iterable, Sequence

Vec = tuple[float, float, float]


# ---------------------------------------------------------------- базовое

def clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def smoothstep(x: float) -> float:
    x = clamp(x, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def smootherstep(x: float) -> float:
    x = clamp(x, 0.0, 1.0)
    return x * x * x * (x * (x * 6.0 - 15.0) + 10.0)


def lerp(a, b, k: float):
    if isinstance(a, (tuple, list)):
        return tuple(x + (y - x) * k for x, y in zip(a, b))
    return a + (b - a) * k


def v_sub(a: Sequence[float], b: Sequence[float]) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def v_add(a: Sequence[float], b: Sequence[float]) -> Vec:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def v_scale(a: Sequence[float], k: float) -> Vec:
    return (a[0] * k, a[1] * k, a[2] * k)


def v_dot(a: Sequence[float], b: Sequence[float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def v_cross(a: Sequence[float], b: Sequence[float]) -> Vec:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def v_len(a: Sequence[float]) -> float:
    return math.sqrt(v_dot(a, a))


def v_norm(a: Sequence[float]) -> Vec:
    n = v_len(a)
    return (0.0, 0.0, 0.0) if n < 1e-12 else v_scale(a, 1.0 / n)


# ---------------------------------------------------------------- клипы тела

@dataclass
class ClipSegment:
    """Клип тела с момента ``start``: ``blend`` — длительность наплыва из предыдущего клипа,
    ``speed`` — множитель скорости, ``loop`` — зациклен, иначе держит последний кадр,
    ``offset`` — с какой секунды клипа начинать."""
    clip: str
    start: float
    duration: float          # длительность самого клипа, с
    blend: float = 0.3
    speed: float = 1.0
    loop: bool = True
    offset: float = 0.0
    weight: float = 1.0      # доля клипа (например, испуг вполсилы поверх покоя — см. ClipTrack.layers)

    def local_time(self, t: float) -> float:
        lt = self.offset + max(0.0, t - self.start) * self.speed
        if self.duration <= 0:
            return 0.0
        if self.loop:
            return lt % self.duration
        return min(lt, self.duration)


@dataclass
class ClipTrack:
    """Последовательность клипов с кроссфейдом. Переход в сегмент i длится ``blend`` с его
    ``start``; прошлый клип всё это время доигрывает (не замирает), веса — smoothstep, сумма 1."""
    segments: list[ClipSegment] = field(default_factory=list)

    def add(self, seg: ClipSegment) -> "ClipTrack":
        if self.segments and seg.start < self.segments[-1].start:
            raise ValueError("сегменты клипов должны идти по времени")
        self.segments.append(seg)
        return self

    def weights_at(self, t: float) -> list[tuple[str, float, float]]:
        """``[(клип, локальное время, вес)]`` — только с весом > 0, сумма весов 1."""
        if not self.segments:
            return []
        raw: list[tuple[ClipSegment, float]] = []
        for i, seg in enumerate(self.segments):
            if t < seg.start and i > 0:
                break
            w = 1.0 if i == 0 else smoothstep((t - seg.start) / max(seg.blend, 1e-6))
            nxt = self.segments[i + 1] if i + 1 < len(self.segments) else None
            if nxt is not None and t >= nxt.start:
                w *= 1.0 - smoothstep((t - nxt.start) / max(nxt.blend, 1e-6))
            if w > 1e-6:
                raw.append((seg, w))
        total = sum(w for _, w in raw) or 1.0
        return [(seg.clip, seg.local_time(t), w / total) for seg, w in raw]


# ---------------------------------------------------------------- кривые параметров

@dataclass
class Curve:
    """Ключи ``(время, значение)``; между ключами — smoothstep (без углов по скорости),
    до первого и после последнего — держит значение. Значение — число или кортеж."""
    keys: list[tuple[float, object]] = field(default_factory=list)
    ease: str = "smooth"     # smooth | linear | step

    def __post_init__(self) -> None:
        self.keys = sorted(self.keys, key=lambda k: k[0])

    def key(self, t: float, value) -> "Curve":
        self.keys.append((t, value))
        self.keys.sort(key=lambda k: k[0])
        return self

    def at(self, t: float):
        if not self.keys:
            raise ValueError("пустая кривая")
        times = [k[0] for k in self.keys]
        i = bisect_right(times, t)
        if i == 0:
            return self.keys[0][1]
        if i >= len(self.keys):
            return self.keys[-1][1]
        (t0, a), (t1, b) = self.keys[i - 1], self.keys[i]
        if self.ease == "step" or t1 <= t0:
            return a
        x = (t - t0) / (t1 - t0)
        k = x if self.ease == "linear" else smoothstep(x)
        return lerp(a, b, k)


# ---------------------------------------------------------------- IK руки

def two_bone_ik(root: Sequence[float], target: Sequence[float], l1: float, l2: float,
                pole: Sequence[float]) -> tuple[Vec, Vec, bool]:
    """Аналитический IK двух костей (плечо→локоть→кисть).

    Возвращает ``(локоть, кисть, достал)``: если цель дальше ``l1+l2`` — рука вытянута в её сторону
    (почти, без «струны»: 0.999), если ближе ``|l1-l2|`` — тоже на луче. Локоть — в плоскости
    (корень, цель, полюс), со стороны полюса."""
    d_vec = v_sub(target, root)
    d = v_len(d_vec)
    axis = v_norm(d_vec) if d > 1e-9 else (0.0, 0.0, -1.0)
    reach_max = (l1 + l2) * 0.999
    reach_min = abs(l1 - l2) * 1.001 + 1e-6
    reached = reach_min <= d <= reach_max + 1e-9
    dc = clamp(d, reach_min, reach_max)
    # угол у плеча по теореме косинусов
    cos_a = clamp((l1 * l1 + dc * dc - l2 * l2) / (2 * l1 * dc), -1.0, 1.0)
    a = math.acos(cos_a)
    # направление «к полюсу», ортогональное оси
    p = v_sub(pole, root)
    p_perp = v_sub(p, v_scale(axis, v_dot(p, axis)))
    if v_len(p_perp) < 1e-9:  # полюс на оси — берём любую перпендикулярную
        p_perp = v_cross(axis, (0.0, 0.0, 1.0)) if abs(axis[2]) < 0.9 else v_cross(axis, (1.0, 0.0, 0.0))
    up = v_norm(p_perp)
    elbow = v_add(root, v_add(v_scale(axis, l1 * math.cos(a)), v_scale(up, l1 * math.sin(a))))
    hand = v_add(root, v_scale(axis, dc))
    return elbow, hand, reached


def stand_point(button: Sequence[float], wall_normal: Sequence[float], shoulder: Sequence[float],
                reach: float, comfort: float = 0.8, side: float = 0.0,
                facing: Sequence[float] | None = None, ahead: float = 0.0) -> Vec:
    """Где встать корнем (на полу, z=0), чтобы плечо оказалось на расстоянии ``comfort·reach`` от кнопки
    прямо напротив неё (по нормали стены).

    ``shoulder`` — плечо относительно корня героя (x — вправо от взгляда, y — вперёд, z — вверх);
    ``wall_normal`` — нормаль стены (из стены в комнату); ``facing`` — куда смотрит герой (по умолчанию
    в стену; вдоль стены — для бокового нажатия, когда голова больше вылета руки); ``side`` — сдвиг вбок;
    ``ahead`` — насколько кнопка впереди плеча по взгляду (при боковом нажатии: чтобы кнопка была
    перед глазами, а не за огромной головой).
    Возвращает точку корня в мировых координатах."""
    n = v_norm((wall_normal[0], wall_normal[1], 0.0))
    fwd = v_norm((facing[0], facing[1], 0.0)) if facing is not None else v_scale(n, -1.0)
    right = (fwd[1], -fwd[0], 0.0)               # вправо от взгляда (z вверх)
    dz = button[2] - shoulder[2]
    want = comfort * reach
    horiz = math.sqrt(max(want * want - dz * dz, 0.0))   # сколько остаётся по горизонтали
    along = clamp(ahead, -horiz, horiz) if abs(v_dot(fwd, n)) < 0.99 else 0.0
    out = math.sqrt(max(horiz * horiz - along * along, 0.0))
    px = button[0] + n[0] * out - fwd[0] * along  # плечо — напротив кнопки, кнопка чуть впереди
    py = button[1] + n[1] * out - fwd[1] * along
    sx, sy = shoulder[0], shoulder[1]
    rx = px - (sx * right[0] + sy * fwd[0]) + side * right[0]
    ry = py - (sx * right[1] + sy * fwd[1]) + side * right[1]
    return (rx, ry, 0.0)


def heading_of(facing: Sequence[float]) -> float:
    """Угол корпуса (против ч.с. от −Y) для направления взгляда ``facing``."""
    return math.atan2(facing[0], -facing[1])

@dataclass
class Reach:
    """Нажатие: рука подходит к точке «зависания» перед кнопкой, вдавливает на ``depth`` и отпускает.
    ``influence`` — вес IK (0 — играет клип), ``target_at`` — куда тянется кисть."""
    button: Vec
    normal: Vec              # из стены наружу
    t_start: float           # начало замаха
    t_contact: float         # касание
    t_release: float         # конец нажатия
    t_end: float             # рука вернулась к клипу
    hover: float = 0.06      # м перед кнопкой
    depth: float = 0.008     # насколько вдавить
    rest: Vec | None = None  # откуда рука стартует (мир), по умолчанию — от точки зависания

    def influence(self, t: float) -> float:
        if t <= self.t_start or t >= self.t_end:
            return 0.0
        if t < self.t_contact:
            return smootherstep((t - self.t_start) / max(self.t_contact - self.t_start - 0.08, 1e-6))
        if t <= self.t_release:
            return 1.0
        return 1.0 - smootherstep((t - self.t_release) / max(self.t_end - self.t_release, 1e-6))

    def target_at(self, t: float) -> Vec:
        hover_pt = v_add(self.button, v_scale(v_norm(self.normal), self.hover))
        press_pt = v_add(self.button, v_scale(v_norm(self.normal), -self.depth))
        if t < self.t_contact:
            # подлёт к зависанию за первые 70% замаха, потом — короткий тычок
            approach = self.t_start + (self.t_contact - self.t_start) * 0.7
            if t < approach:
                start = self.rest or hover_pt
                return lerp(start, hover_pt, smootherstep((t - self.t_start) / max(approach - self.t_start, 1e-6)))
            return lerp(hover_pt, press_pt, smootherstep((t - approach) / max(self.t_contact - approach, 1e-6)))
        if t <= self.t_release:
            return press_pt
        back = self.t_release + (self.t_end - self.t_release) * 0.35
        return lerp(press_pt, hover_pt, smootherstep((t - self.t_release) / max(back - self.t_release, 1e-6)))

    def pressed(self, t: float) -> bool:
        return self.t_contact <= t <= self.t_release


# ---------------------------------------------------------------- взгляд

def look_angles(eye: Sequence[float], forward_yaw: float, target: Sequence[float]) -> tuple[float, float]:
    """Рыскание/тангаж на цель относительно направления корпуса (радианы).
    ``forward_yaw`` — куда смотрит корпус: 0 = −Y (к дверям), положительный — поворот против часовой
    стрелки вокруг +Z (как rotation_euler.z в Blender). Возвращённый yaw — тот же знак."""
    d = v_sub(target, eye)
    yaw_world = math.atan2(d[0], -d[1])      # 0 при взгляде в −Y, +90° — в +X
    yaw = (yaw_world - forward_yaw + math.pi) % (2 * math.pi) - math.pi
    pitch = math.atan2(d[2], math.hypot(d[0], d[1]))
    return yaw, pitch


@dataclass
class GazeLimits:
    head_yaw: float = math.radians(55)
    head_pitch_up: float = math.radians(40)
    head_pitch_down: float = math.radians(30)
    head_share: float = 0.7          # какую долю угла берёт голова (остальное — глаза)
    eye_yaw: float = math.radians(28)
    eye_pitch: float = math.radians(20)


def split_gaze(yaw: float, pitch: float, lim: GazeLimits = GazeLimits()) -> tuple[tuple[float, float], tuple[float, float]]:
    """Делит угол взгляда между головой и глазами: голова — ``head_share`` в своих пределах,
    глаза добирают остаток в своих. Возвращает ``((head_yaw, head_pitch), (eye_yaw, eye_pitch))``."""
    hy = clamp(yaw * lim.head_share, -lim.head_yaw, lim.head_yaw)
    hp = clamp(pitch * lim.head_share, -lim.head_pitch_down, lim.head_pitch_up)
    ey = clamp(yaw - hy, -lim.eye_yaw, lim.eye_yaw)
    ep = clamp(pitch - hp, -lim.eye_pitch, lim.eye_pitch)
    return (hy, hp), (ey, ep)


def spring_follow(samples: Sequence[Sequence[float]], dt: float, halflife: float) -> list[Vec]:
    """Критически задемпфированная пружина за целью (покадрово): без перелёта и без рывков
    при скачке цели. ``halflife`` — за сколько секунд отставание уменьшается вдвое."""
    if not samples:
        return []
    if halflife <= 0:
        return [tuple(s) for s in samples]
    y = 4.0 * 0.69314718056 / (halflife + 1e-9) / 2.0
    pos = list(samples[0])
    vel = [0.0] * len(pos)
    out = [tuple(pos)]
    for tgt in samples[1:]:
        for i in range(len(pos)):
            j0 = pos[i] - tgt[i]
            j1 = vel[i] + j0 * y
            e = math.exp(-y * dt)
            pos[i] = e * (j0 + j1 * dt) + tgt[i]
            vel[i] = e * (vel[i] - j1 * y * dt)
        out.append(tuple(pos))
    return out


def saccade_track(times: Sequence[float], target_curve: "Curve", dur: float = 0.06) -> list:
    """Глаза: цель меняется скачком, а не плывёт — переход за ``dur`` с (саккада)."""
    out = []
    keys = target_curve.keys
    for t in times:
        times_k = [k[0] for k in keys]
        i = bisect_right(times_k, t)
        if i == 0:
            out.append(keys[0][1])
            continue
        if i >= len(keys):
            out.append(keys[-1][1])
            continue
        t0, a = keys[i - 1]
        # ключ — момент, когда взгляд начинает прыгать к следующему значению
        t1, b = keys[i]
        if t1 - t < dur:
            out.append(lerp(a, b, smoothstep(1.0 - (t1 - t) / dur)))
        else:
            out.append(a)
    return out


# ---------------------------------------------------------------- моргание

def blink_times(duration: float, seed: int = 7, min_gap: float = 1.8, max_gap: float = 4.2,
                forced: Iterable[float] = ()) -> list[float]:
    """Моменты морганий: случайные интервалы ``min_gap..max_gap`` (детерминированно по seed)
    + принудительные (испуг, смена взгляда). Рядом с принудительным (<0.6 с) случайное убираем."""
    rnd = random.Random(seed)
    forced = sorted(forced)
    out: list[float] = list(forced)
    t = rnd.uniform(0.6, min_gap)
    while t < duration:
        if all(abs(t - f) > 0.6 for f in forced):
            out.append(t)
        t += rnd.uniform(min_gap, max_gap)
    return sorted(out)


def blink_amount(t: float, times: Sequence[float], close: float = 0.07, hold: float = 0.04,
                 open_: float = 0.13) -> float:
    """Насколько закрыт глаз (0 — открыт, 1 — закрыт): быстро закрыть, чуть подержать, медленнее открыть."""
    best = 0.0
    for b in times:
        x = t - b
        if x < 0 or x > close + hold + open_:
            continue
        if x < close:
            v = smoothstep(x / close)
        elif x < close + hold:
            v = 1.0
        else:
            v = 1.0 - smoothstep((x - close - hold) / open_)
        best = max(best, v)
    return best


# ---------------------------------------------------------------- шаги

def stride_speed(foot_l: Sequence[Sequence[float]], foot_r: Sequence[Sequence[float]], fps: float,
                 forward: Sequence[float] = (0.0, -1.0, 0.0)) -> float:
    """Скорость корня, при которой опорная стопа стоит на месте.

    ``foot_l``/``foot_r`` — позиции стоп по кадрам клипа *на месте* (in-place) в системе персонажа.
    Опорная — та, что ниже; в клипе на месте она едет назад со скоростью ходьбы. Берём медиану
    скорости опорной стопы против ``forward``."""
    n = min(len(foot_l), len(foot_r))
    if n < 3:
        return 0.0
    f = v_norm(forward)
    speeds = []
    for i in range(1, n):
        a, b = (foot_l, foot_r) if foot_l[i][2] <= foot_r[i][2] else (foot_r, foot_l)
        if a is foot_l and foot_l[i - 1][2] > foot_r[i - 1][2]:
            continue  # смена опоры на этом кадре — пропуск
        if a is foot_r and foot_r[i - 1][2] > foot_l[i - 1][2]:
            continue
        d = v_sub(a[i], a[i - 1])
        speeds.append(-v_dot(d, f) * fps)
    if not speeds:
        return 0.0
    speeds.sort()
    return max(0.0, speeds[len(speeds) // 2])


def travel_profile(weights: Sequence[float], dt: float, distance: float) -> list[float]:
    """Сколько пройдено к каждому кадру, если скорость корня пропорциональна весу клипа ходьбы:
    разгон и торможение совпадают с наплывом клипа, весь путь = ``distance``. Возвращает
    накопленную долю пути 0..1 по кадрам (монотонно)."""
    total = sum(weights) * dt
    out, acc = [], 0.0
    for w in weights:
        acc += w * dt
        out.append(acc / total if total > 0 else 0.0)
    if out and total > 0:
        out[-1] = 1.0
    _ = distance
    return out


def walk_duration(distance: float, speed: float, cycle: float, half_cycles: bool = True) -> float:
    """Длительность ходьбы на ``distance`` — округлённая до целых шагов (полуциклов), чтобы
    в конце стопы стояли рядом, а не в разножке; скорость потом подстраиваем под эту длительность."""
    if speed <= 0:
        return 0.0
    raw = distance / speed
    if not half_cycles or cycle <= 0:
        return raw
    step = cycle / 2.0
    return max(step, round(raw / step) * step)


# ---------------------------------------------------------------- камера

@dataclass
class Shot:
    """План: камера за время плана плавно едет из ``a`` в ``b`` (положение, цель, фокусное).
    ``focus`` — на что фокус (точка), по умолчанию — цель."""
    start: float
    end: float
    pos_a: Vec
    pos_b: Vec
    look_a: Vec
    look_b: Vec
    lens_a: float = 35.0
    lens_b: float | None = None
    focus: Vec | None = None
    fstop: float = 2.8
    name: str = ""

    def at(self, t: float) -> dict:
        x = clamp((t - self.start) / max(self.end - self.start, 1e-6), 0.0, 1.0)
        k = smoothstep(x) * 0.6 + x * 0.4      # мягкий старт/стоп, но без «зависания» в середине
        pos = lerp(self.pos_a, self.pos_b, k)
        look = lerp(self.look_a, self.look_b, k)
        lens = lerp(self.lens_a, self.lens_b if self.lens_b is not None else self.lens_a, k)
        focus = self.focus or look
        return {"pos": pos, "look": look, "lens": lens, "focus_dist": v_len(v_sub(focus, pos)),
                "fstop": self.fstop, "shot": self.name}


@dataclass
class ShotList:
    shots: list[Shot]

    def __post_init__(self) -> None:
        self.shots.sort(key=lambda s: s.start)
        for a, b in zip(self.shots, self.shots[1:]):
            if b.start < a.end - 1e-6:
                raise ValueError(f"планы {a.name} и {b.name} перекрываются")

    def at(self, t: float) -> dict:
        for s in self.shots:
            if s.start <= t < s.end:
                return s.at(t)
        return self.shots[-1].at(t) if t >= self.shots[-1].end else self.shots[0].at(t)

    def cuts(self) -> list[float]:
        return [s.start for s in self.shots[1:]]


# ---------------------------------------------------------------- свет

def flicker(t: float, t_start: float, t_off: float, seed: int = 3) -> float:
    """Яркость основного света 0..1: ровно до ``t_start``, затем рваное мигание с нарастающими
    провалами, на ``t_off`` — гаснет совсем (и больше не загорается)."""
    if t < t_start:
        return 1.0
    if t >= t_off:
        return 0.0
    rnd = random.Random(seed)
    # провалы: интервалы [s, s+len) с глубиной; ближе к t_off — чаще и глубже
    span = t_off - t_start
    events = []
    s = t_start + rnd.uniform(0.02, 0.1)
    while s < t_off:
        prog = (s - t_start) / span
        ln = rnd.uniform(0.03, 0.08 + 0.1 * prog)
        depth = rnd.uniform(0.5, 0.85) + 0.15 * prog
        events.append((s, ln, min(depth, 1.0)))
        s += ln + rnd.uniform(0.05, 0.28 * (1.0 - prog) + 0.04)
    v = 1.0
    for s, ln, depth in events:
        if s <= t < s + ln:
            v = 1.0 - depth
    return v
