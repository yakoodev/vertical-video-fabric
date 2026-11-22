"""3D-серия: бег героя по пути и его двойник (ч.5 «Лифт»: выбегает из кабины, за ним идёт такой же).

Чистый Python без ``bpy``: те же дорожки считают сцена Blender (``tools/blender/series_scene.py``), задание и тесты.

**Что пишет автор** (поля фразы сценария, как ``cam``; ``apply_authored`` переносит их в планы задания):

* ``run`` — герой бежит по пути: ``{"path": [[x, y], …], "at": 0.2}`` (м, система места; ``at`` — через сколько секунд
  от начала плана). Бег, начатый в прошлом плане, не прерывается: на склейке герой ставится в начало нового пути, шаг
  идёт дальше той же фазой. Путь кончился раньше плана — останавливается;
* ``double`` — двойник (копия героя в той же одежде, ``perform_double``): ``{"do": "lie" | "twitch" | "rise" |
  "stand" | "walk" | "run", "pos": [x, y], "heading": h, "path": […], "at": с}``. Что задано в плане, держится до
  следующего указания; ``run`` идёт в ритм героя (та же фаза шага с отставанием ``SYNC_LAG``);
* ``screen`` — экран телефона плана сильнее правил (``chat_value``: переписка списком, с фото); ``location``, ``fill``
  (заполняющий свет плана, Вт — в полной темноте, иначе лежащего за спиной не видно), ``look``.

**Корень на бегу** — как у шага (``series3d_blender_perform.walk_root``): опорная стопа клипа стоит на полу, корень
едет на её сдвиг назад; в полёте (обе стопы над полом) — со средней скоростью опоры. Курс — по касательной пути.
"""

from __future__ import annotations

import json
import math
from typing import Any

from app import motion3d as m
from app.series3d_blender_stage import S, facing

FPS = 30
RUN_CLIP = "Running_A"
# Стопы клипа бега на месте (замер в Blender, система героя без масштаба): (y левой, z левой, y правой, z правой) на
# кадр клипа (30 к/с), вперёд — −y. Замер прямой кинематикой по GLB KayKit (совпадает с замером Walking_C в Blender —
# series3d_blender_stage.WALK_FEET): 24 кадра, 0.8 с; опора — z < CONTACT_Z.
RUN_FEET: list[tuple[float, float, float, float]] = [
    (-0.3375, 0.2224, 0.389, 0.4152), (-0.2868, 0.1928, 0.3589, 0.4085), (-0.2045, 0.1602, 0.281, 0.3912),
    (-0.1218, 0.1395, 0.1751, 0.3657), (-0.0125, 0.1406, 0.0601, 0.3326), (0.1008, 0.1409, -0.0498, 0.2921),
    (0.2085, 0.1543, -0.1458, 0.2498), (0.2823, 0.2187, -0.2185, 0.2362), (0.3438, 0.2974, -0.2645, 0.2534),
    (0.3838, 0.3604, -0.2855, 0.2654), (0.3893, 0.3979, -0.3024, 0.2456), (0.3886, 0.4123, -0.3296, 0.2217),
    (0.389, 0.4152, -0.3375, 0.2224), (0.3584, 0.4084, -0.2862, 0.1953), (0.2804, 0.3911, -0.2045, 0.1604),
    (0.1747, 0.3657, -0.1218, 0.1395), (0.0599, 0.3326, -0.0126, 0.1409), (-0.0499, 0.2921, 0.1008, 0.141),
    (-0.1458, 0.2498, 0.2085, 0.1543), (-0.2186, 0.2364, 0.2808, 0.2169), (-0.2645, 0.2535, 0.3416, 0.2936),
    (-0.2855, 0.2654, 0.3844, 0.3619), (-0.3022, 0.2474, 0.4075, 0.3986), (-0.3282, 0.2252, 0.3949, 0.4136),
    (-0.3375, 0.2224, 0.389, 0.4152)]
RUN_LEN = 0.8
RUN_SPEED = 2.6               # ед/с клипа без масштаба, если стоп нет (×S — м/с)
CONTACT_Z = 0.20              # стопа на полу: ниже этой высоты (ед. без масштаба)
SYNC_LAG = 0.08               # двойник бежит в ритм героя, отстаёт на ~2 кадра: шаги слышны второй парой
LIE_CLIP, RISE_CLIP, TWITCH_CLIP = "Lie_Idle", "Lie_StandUp", "Lie_StandUp"
DOUBLE_CLIPS = ("Lie_Idle", "Lie_StandUp", "Idle_A", "Walking_C", RUN_CLIP)
AUTHORED = ("cam", "run", "double", "screen", "location", "fill", "look", "look_at", "flash")
# Звуки двойника (настоящие записи паков, громкость в коде): шевельнулся на полу — шорох ткани по металлу пола;
# встаёт — ткань и упор ногой
SFX_DOUBLE = {"twitch": {"ref": "sfx:kenney-impact/impactSoft_medium_002.ogg", "volume": 0.5, "label": "шевелится на полу",
                         "kind": "double_twitch"},
              "rise": {"ref": "sfx:kenney-impact/impactSoft_heavy_001.ogg", "volume": 0.45, "label": "встаёт",
                       "kind": "double_rise"},
              "rise_step": {"ref": "sfx:kenney-impact/footstep_concrete_003.ogg", "volume": 0.4, "label": "встал",
                            "kind": "double_rise"}}


# --------------------------------------------------------------------------- цикл шага клипа

class Cycle:
    """Сдвиг корня по клипу бега/шага: ``units(lt)`` — сколько ед. (без масштаба) корень прошёл от начала клипа к его
    секунде ``lt`` (с переходом через цикл). Опора — стопа ниже другой и у пола; в полёте — средняя скорость опоры."""

    def __init__(self, feet: list[tuple[float, float, float, float]] | None, length: float, speed: float = RUN_SPEED):
        self.length = float(length)
        self.feet = list(feet or [])
        if len(self.feet) < 3:
            n = max(2, int(round(self.length * FPS)))
            self.step = [speed / FPS] * n
        else:
            raw: list[float | None] = []
            for i in range(len(self.feet) - 1):
                ly, lz, ry, rz = self.feet[i]
                ly2, _lz2, ry2, _rz2 = self.feet[i + 1]
                low = min(lz, rz)
                if low > CONTACT_Z:
                    raw.append(None)                   # полёт: обе стопы в воздухе
                else:
                    d = (ly2 - ly) if lz <= rz else (ry2 - ry)
                    raw.append(d if d > 0 else None)  # опора уехала вперёд — не опора (смена ноги)
            ok = [d for d in raw if d is not None]
            mean = sum(ok) / len(ok) if ok else speed / FPS
            self.step = [mean if d is None else d for d in raw]
        self.cum = [0.0]
        for d in self.step:
            self.cum.append(self.cum[-1] + d)
        self.total = self.cum[-1]

    @property
    def speed(self) -> float:
        """Средняя скорость корня, м/с (с масштабом героя)."""
        return self.total * S / max(self.length, 1e-6)

    def units(self, lt: float) -> float:
        n = len(self.step)
        phi = lt * FPS
        cycles, rest = divmod(phi, n)
        i = int(rest)
        frac = rest - i
        return cycles * self.total + self.cum[min(i, n)] + self.step[min(i, n - 1)] * frac

    def strikes(self) -> list[tuple[float, str]]:
        """Касания пола по циклу: ``[(секунда клипа, сторона)]`` — стопа опустилась ниже ``CONTACT_Z``."""
        if len(self.feet) < 3:
            half = self.length / 2
            return [(0.0, "l"), (half, "r")]
        out = []
        n = len(self.feet)
        for i in range(n):
            a, b = self.feet[i - 1], self.feet[i]
            for side, za, zb in (("l", a[1], b[1]), ("r", a[3], b[3])):
                if za > CONTACT_Z >= zb:
                    out.append((i / FPS, side))
        return sorted(out)


RUN = Cycle(RUN_FEET, RUN_LEN)


# --------------------------------------------------------------------------- путь

def path_len(path: list) -> float:
    return sum(math.dist(path[i], path[i + 1]) for i in range(len(path) - 1))


def path_at(path: list, s: float) -> tuple[float, float, float]:
    """Точка пути через ``s`` м от начала и курс по касательной: ``(x, y, курс)``. Дальше конца — конец."""
    pts = [(float(p[0]), float(p[1])) for p in path]
    if len(pts) == 1:
        return pts[0][0], pts[0][1], 0.0
    s = max(0.0, s)
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        seg = math.dist(a, b)
        if s <= seg or i == len(pts) - 2:
            k = min(1.0, s / seg) if seg > 1e-9 else 1.0
            h = m.heading_of((b[0] - a[0], b[1] - a[1])) if seg > 1e-9 else 0.0
            return a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, h
        s -= seg
    return pts[-1][0], pts[-1][1], 0.0


def path_heading(path: list, s: float, look: float = 0.35) -> float:
    """Курс на пути со сглаживанием поворота: направление на точку ``look`` м впереди (угол не рвётся на вершине)."""
    x, y, h = path_at(path, s)
    L = path_len(path)
    if s + look <= L:
        x2, y2, _ = path_at(path, s + look)
        if math.hypot(x2 - x, y2 - y) > 1e-6:
            return m.heading_of((x2 - x, y2 - y))
    return h


def ahead_point(path: list, extra: float = 2.5, z: float = 0.6) -> tuple[float, float, float]:
    """Куда смотреть (и светить) на бегу: за конец пути по последнему отрезку — вперёд, не назад."""
    x, y, h = path_at(path, path_len(path))
    fx, fy = facing(h)
    return (round(x + fx * extra, 4), round(y + fy * extra, 4), z)


def unwrap(prev: float, h: float) -> float:
    """Курс ``h`` ближайшим к ``prev`` оборотом (без скачка на ±π)."""
    while h - prev > math.pi:
        h -= 2 * math.pi
    while h - prev < -math.pi:
        h += 2 * math.pi
    return h


# --------------------------------------------------------------------------- двери на этаж, свет, вибрация

DOORS_FULL = 0.425            # щель створок кабины целиком (полстворки: DOOR_W / 2 без зазора)
DOORS_OPEN_SEC = 1.3          # створки расходятся за столько секунд
SPILL_MAIN = 0.34             # свет кабины после «двери разъехались» — тусклый, вполсилы (main 0..1)
VIB_SEC, VIB_PULSES = 0.42, 2   # «телефон вибрирует»: два коротких жужжания


def spill_at(t: float, t_open: float) -> float:
    """Свет кабины вернулся к открытию дверей: 0 → 1 за 0.25 с (чуть раньше створок — питание дали)."""
    return m.smoothstep((t - (t_open - 0.15)) / 0.25) if t >= t_open - 0.15 else 0.0


def spill_flicker(t: float, t_open: float) -> float:
    """Лампа кабины после темноты: сначала мигает, потом тускло дрожит."""
    x = t - t_open
    if x < 0.9:
        return 0.35 + 0.65 * (0.5 + 0.5 * math.sin(x * 37.0) * math.sin(x * 11.0 + 0.7))
    return 0.86 + 0.14 * math.sin(t * 7.3) * math.sin(t * 2.1 + 1.0)


class Override:
    """Кривая с участками, где значение задал автор: ``[(с, по, значение)]`` сильнее кривой (на склейке — сразу)."""

    def __init__(self, curve: m.Curve, spans: list[tuple[float, float, float]]):
        self.curve, self.spans = curve, list(spans)

    def at(self, t: float):
        for a, b, v in self.spans:
            if a <= t < b:
                return v
        return self.curve.at(t)


def vibration(t: float, t_vib: float) -> float:
    """Сила дрожи телефона 0..1: ``VIB_PULSES`` жужжаний по ``VIB_SEC`` с паузой 0.18 с."""
    for k in range(VIB_PULSES):
        a = t_vib + k * (VIB_SEC + 0.18)
        if a <= t < a + VIB_SEC:
            return 1.0
    return 0.0


# --------------------------------------------------------------------------- авторские поля фраз

def apply_authored(shots: list[dict], sentences: list[dict]) -> list[dict]:
    """Поля автора из фраз сценария (``AUTHORED``) → в планы этих фраз. ``cam``/``run``/``double`` — по номеру куска
    фразы, если заданы списком (``cams``/``runs``/``doubles``); ``screen`` где-либо в сценарии — экраны ведёт автор
    (у остальных планов экрана нет: правила по словам не путают «сообщения от него» с входящим)."""
    authored_screens = any("screen" in s_ for s_ in sentences)
    for shot in shots:
        idx = [i for i in shot.get("sentences") or [] if 0 <= i < len(sentences)]
        if authored_screens and not any("screen" in sentences[i] for i in idx):
            if shot.get("screen"):
                shot.pop("screen", None)
                if shot.get("action") == "phone_out" and shot.get("by") != "author":
                    shot["action"] = "wait"
        for i in idx:
            sent = sentences[i]
            part = int(shot.get("part") or 0)
            for key in AUTHORED:
                many = sent.get(key + "s")
                if isinstance(many, list) and many:
                    val = many[min(part, len(many) - 1)]
                elif key in sent:
                    val = sent[key]
                else:
                    continue
                if key == "screen":
                    val = screen_value(val)
                    if val:
                        shot["screen"] = val
                        shot["camera"] = "detail"
                        if shot.get("action") not in ("phone_out", "phone_light"):
                            shot["action"] = "phone_out"
                    else:
                        shot.pop("screen", None)
                    continue
                if key == "cam" and not (shot.get("by") == "author" and shot.get("planned")):
                    continue
                shot[key] = val
    return shots


def screen_value(val: Any) -> str:
    """Экран телефона от автора: строка как есть («8%», «03:33»), словарь — переписка ``chat|{json}``."""
    if isinstance(val, dict):
        return "chat|" + json.dumps(val, ensure_ascii=False, sort_keys=True)
    return str(val or "")


def chat_spec(value: str) -> dict | None:
    """``chat|{json}`` → словарь переписки: ``{to, status, items: [{in|out|photo, note}], scroll}``."""
    if not str(value or "").startswith("chat|"):
        return None
    try:
        spec = json.loads(str(value)[5:])
    except ValueError:
        return None
    return spec if isinstance(spec, dict) else None


def is_chat(value: Any) -> bool:
    return str(value or "").startswith(("chat|", "msg|"))


# --------------------------------------------------------------------------- бег героя

def run_window(track: m.ClipTrack, add_clip, t_cut: float, t1: float, spec: dict, running: dict | None,
               clip_len: dict) -> dict:
    """Окно бега плана: клип бега (новый — с ``at``; бег идёт с прошлого плана — тот же сегмент, фаза дальше) и
    остановка, если путь короче плана. → ``{t0, t1, path, start, stop}``; ``running`` — окно прошлого плана."""
    path = [(float(p[0]), float(p[1])) for p in spec.get("path") or []]
    if len(path) < 2:
        raise ValueError("путь бега — хотя бы две точки")
    at = float(spec.get("at", 0.15))
    cont = running is not None and running.get("stop") is None
    t0 = t_cut if cont else t_cut + at
    if not cont:
        add_clip(RUN_CLIP, t0, blend=0.18)
    dur = path_len(path) / max(RUN.speed, 1e-3)
    end = t0 + dur + (0.0 if cont else 0.12)
    stop = None
    if spec.get("stop") or end < t1 - 0.1:
        stop = min(end, t1 - 0.05)
        add_clip("Idle_A", stop - 0.1, blend=0.35)
    _ = (track, clip_len)
    return {"t0": round(t0, 4), "t1": round(t1, 4), "path": path, "stop": None if stop is None else round(stop, 4),
            "cont": cont}


def run_root(track: m.ClipTrack, windows: list[dict], times: list[float], clip: str = RUN_CLIP,
             cycle: Cycle | None = None) -> dict[int, tuple[float, float, float]]:
    """Корень и курс на кадрах бега: ``{кадр: (x, y, курс)}``. В каждом окне корень идёт по своему пути: на кадр —
    на сдвиг опоры клипа × вес клипа (наплыв — медленнее), не дальше конца пути."""
    cyc = cycle or RUN
    out: dict[int, tuple[float, float, float]] = {}
    for k, w_ in enumerate(windows):
        nxt = windows[k + 1]["t0"] if k + 1 < len(windows) else 1e9
        t_end = min(w_["t1"], nxt)
        path = w_["path"]
        L = path_len(path)
        s = 0.0
        prev_lt = None
        for f, t in enumerate(times):
            if t < w_["t0"] - 1e-9 or t >= t_end:
                continue
            lt, w = None, 0.0
            for c_, l_, ww in track.weights_at(t):
                if c_ == clip:
                    lt, w = l_, ww
            if lt is not None and prev_lt is not None:
                d = cyc.units(lt) - cyc.units(prev_lt)
                if d < -cyc.total / 2:
                    d += cyc.total
                s = min(L, s + max(0.0, d) * S * w)
            prev_lt = lt
            x, y, _h = path_at(path, s)
            out[f] = (round(x, 5), round(y, 5), round(path_heading(path, s), 5))
    return out


def strikes_in(track: m.ClipTrack, times: list[float], clip: str = RUN_CLIP, cycle: Cycle | None = None,
               min_w: float = 0.5) -> list[tuple[float, str]]:
    """Моменты, когда стопа касается пола на бегу (для звука шагов): ``[(t, сторона)]``."""
    cyc = cycle or RUN
    marks = cyc.strikes()
    out: list[tuple[float, str]] = []
    prev = None
    for t in times:
        lt, w = None, 0.0
        for c_, l_, ww in track.weights_at(t):
            if c_ == clip:
                lt, w = l_, ww
        if lt is None or w < min_w:
            prev = None
            continue
        if prev is not None:
            for mt, side in marks:
                a, b = prev, lt
                hit = (a < mt <= b) if b >= a else (mt > a or mt <= b)
                if hit:
                    out.append((round(t, 3), side))
        prev = lt
    return out


# --------------------------------------------------------------------------- двойник

def perform_double(shots: list[dict], times: list[float], hero_track: m.ClipTrack, clip_len: dict) -> dict | None:
    """Дорожки двойника по указаниям планов (``double``): корень, курс, клипы, взгляд (нет — прямо), шаги.
    Нет ни одного указания — ``None`` (у серии двойника нет)."""
    plans = [(s_, s_["double"]) for s_ in shots if isinstance(s_.get("double"), dict)]
    if not plans:
        return None
    track = m.ClipTrack()
    seg = {"clip": None}

    def add(name: str, t: float, blend: float = 0.3, loop: bool = True, offset: float = 0.0, speed: float = 1.0):
        if track.segments and t < track.segments[-1].start + 0.05:
            t = track.segments[-1].start + 0.05
        track.add(m.ClipSegment(name, t, clip_len[name], blend=blend, loop=loop, offset=offset, speed=speed))
        seg["clip"] = name

    sfx: list[dict] = []
    first = plans[0][1]
    pos = tuple(first.get("pos") or (0.0, 0.0))
    heading = float(first.get("heading", 0.0))
    pos_keys: list[tuple[float, tuple, float]] = [(0.0, pos, heading)]
    add(LIE_CLIP if first.get("do", "lie") in ("lie", "twitch") else "Idle_A", 0.0, blend=0.01)
    windows: list[dict] = []
    walks: list[dict] = []
    running = None
    for s_, d in plans:
        t0, t1 = float(s_["t0"]), float(s_["t1"])
        t_cut = round(t0 * FPS) / FPS
        at = float(d.get("at", 0.0))
        do = d.get("do", "stand")
        if d.get("pos") is not None and do not in ("run", "walk"):
            pos = (float(d["pos"][0]), float(d["pos"][1]))
            heading = float(d.get("heading", heading))
            pos_keys.append((t_cut, pos, heading))
        if do != "run":
            running = None
        if do == "lie" and seg["clip"] != LIE_CLIP:
            add(LIE_CLIP, t0 + at, blend=0.3)
        elif do == "twitch":
            # шевелится на полу: начало подъёма и обратно (рука, плечо дёрнулись)
            add(TWITCH_CLIP, t0 + at, blend=0.35, loop=False)
            add(LIE_CLIP, t0 + at + 0.55, blend=0.6)
            sfx.append({"t": round(t0 + at + 0.1, 3), **SFX_DOUBLE["twitch"]})
        elif do == "rise":
            add(RISE_CLIP, t0 + at, blend=0.25, loop=False, speed=float(d.get("speed", 1.0)))
            up = t0 + at + clip_len[RISE_CLIP] / float(d.get("speed", 1.0))
            add("Idle_A", up - 0.2, blend=0.4)
            sfx.append({"t": round(t0 + at + 0.15, 3), **SFX_DOUBLE["rise"]})
            sfx.append({"t": round(up - 0.25, 3), **SFX_DOUBLE["rise_step"]})
        elif do == "stand" and seg["clip"] not in ("Idle_A",):
            add("Idle_A", t0 + at, blend=0.4)
        elif do == "walk":
            path = [(float(p[0]), float(p[1])) for p in d.get("path") or []]
            pos_keys.append((t_cut, path[0], path_at(path, 0.0)[2]))
            ws = t0 + at
            add("Walking_C", ws, blend=0.3)
            we = ws + path_len(path) / max(WALK.speed, 1e-3)
            walks.append({"t0": ws, "t1": min(t1, we), "path": path, "stop": we if we < t1 else None})
            if we < t1:
                add("Idle_A", we - 0.1, blend=0.35)
            pos = path[-1]
            heading = path_at(path, path_len(path))[2]
        elif do == "run":
            path = [(float(p[0]), float(p[1])) for p in d.get("path") or []]
            pos_keys.append((t_cut, path[0], path_at(path, 0.0)[2]))
            cont = running is not None
            ws = t_cut if cont else t0 + at
            if not cont:
                # та же фаза шага, что у героя в этот момент (в ритм), с отставанием SYNC_LAG
                off = 0.0
                for c_, l_, _w in hero_track.weights_at(ws):
                    if c_ == RUN_CLIP:
                        off = (l_ - SYNC_LAG) % RUN.length
                add(RUN_CLIP, ws, blend=0.18, offset=off)
            we = ws + path_len(path) / max(RUN.speed, 1e-3)
            win = {"t0": ws, "t1": t1, "path": path, "stop": None}
            if d.get("stop") or we < t1 - 0.1:
                win["stop"] = min(we, t1 - 0.05)
                add("Idle_A", win["stop"] - 0.1, blend=0.35)
            windows.append(win)
            running = win if win["stop"] is None else None
            pos = path[-1]
            heading = path_at(path, path_len(path))[2]
    n = len(times)
    over: dict[int, tuple[float, float, float]] = {}
    for wins, clip, cyc in ((windows, RUN_CLIP, RUN), (walks, "Walking_C", WALK)):
        for k, win in enumerate(wins):
            got = run_root(track, [win], times, clip=clip, cycle=cyc)
            nxt = wins[k + 1]["t0"] if k + 1 < len(wins) else 1e9
            got = {f: v for f, v in got.items() if times[f] < nxt}
            if got:
                # после окна — стоит там, где остановился (а не на старте пути), до следующего указания; указание
                # следующего плана на той же склейке сильнее (ключ окна не должен лечь на кадр после него)
                last = max(got)
                end_t = times[last] + 0.5 / FPS
                if not any(abs(k_[0] - end_t) < 0.1 for k_ in pos_keys):
                    pos_keys.append((end_t, got[last][:2], got[last][2]))
            over.update(got)
    keys = sorted(pos_keys, key=lambda k: k[0])
    root, heads = [], []
    ki, cur = 0, keys[0]
    for t in times:
        while ki < len(keys) and keys[ki][0] <= t + 1e-9:
            cur = keys[ki]
            ki += 1
        root.append((round(cur[1][0], 5), round(cur[1][1], 5)))
        heads.append(round(cur[2], 5))
    for f, (x, y, h) in over.items():
        root[f] = (x, y)
        heads[f] = h
    for f in range(1, n):
        heads[f] = round(unwrap(heads[f - 1], heads[f]), 5)
    steps = [(t, "double") for t, _side in strikes_in(track, times)]
    steps += [(t, "double") for t, _side in strikes_in(track, times, clip="Walking_C", cycle=WALK)]
    return {"track": [{"clip": sg.clip, "start": round(sg.start, 4), "duration": sg.duration, "blend": sg.blend,
                       "speed": round(sg.speed, 4), "loop": sg.loop, "offset": round(sg.offset, 4), "ease_in": sg.ease_in,
                       "ease_out": sg.ease_out, "end": None if sg.end is None else round(sg.end, 4)} for sg in track.segments],
            "root": root, "heading": heads, "gaze": [None] * n,
            "body": [{"startle": 0.0, "lean": 0.0, "roll": 0.0}] * n, "steps": sorted(steps), "sfx": sfx}


# Шаг двойника — осторожная ходьба героя (Walking_C, замер — series3d_blender_stage.WALK_FEET)
def _walk_cycle() -> Cycle:
    from app.series3d_blender_stage import CLIP_LEN, WALK_FEET

    return Cycle(WALK_FEET, CLIP_LEN["Walking_C"])


WALK = _walk_cycle()
