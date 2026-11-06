"""3D-серия: исполнение — покадровые дорожки, лип-синк, походка, руки, свет (из ``app/series3d_blender.py``)."""

from __future__ import annotations

import math
import random
import re
from typing import Any

from app import motion3d as m
from app.series3d_blender_stage import (
    S, H, FRONT, BELL, BTN_NORMAL, DOOR_FACE_Y, DOOR_NORMAL, DISPLAY, CEIL_LIGHT, HATCH, CLIP_LEN, WALK_CLIP,
    WALK_FEET, MAX_WALK_STEPS, WALK_CYCLE_UNITS, STEP_LEN, stance_units, walk_plan, walk_seconds, button_pos,
    reachable, facing, to_world, KNOCK_POINT, press_stand, spot_for, LOCS, loc_of, loc_spot, loc_target, inside_loc,
    show_items, _low
)
from app.series3d_blender_direction import EMOTIONS, HAND_ACTIONS
from app.series3d_blender_camera import SIT_DROP, PHONE_SHOW, _rot, camera_plan

FPS = 30


WALK_FOOT_X = 0.1709
WALK_PASS = (8 / 30, 32 / 30)   # кадры клипа, где стопы ближе всего: начало и конец шага — ноги рядом


STRIDE = 0.6708            # ед/с клипа Walking_A без масштаба (×S = м/с)


DOOR_OPEN = {"corridor": 0.2}     # двери лифта в торце коридора — открыты (герой из них вышел), пока не схлопнутся


# --------------------------------------------------------------------------- #
# Лип-синк
# --------------------------------------------------------------------------- #

# Визем-группы: (открытие, округление, растяжка в ширину)
VISEMES = {
    "а": (1.0, 0.0, 0.35), "я": (0.95, 0.0, 0.35),
    "о": (0.75, 1.0, 0.0), "ё": (0.75, 1.0, 0.0),
    "у": (0.38, 1.0, 0.0), "ю": (0.4, 0.9, 0.0),
    "э": (0.62, 0.0, 0.75), "е": (0.55, 0.0, 0.75),
    "и": (0.32, 0.0, 1.0), "ы": (0.4, 0.0, 0.85),
    "м": (0.0, 0.0, 0.0), "б": (0.0, 0.0, 0.0), "п": (0.0, 0.0, 0.0),
    "ф": (0.1, 0.0, 0.3), "в": (0.1, 0.0, 0.3),
}
VOWELS = set("аяоёуюэеиы")
_DIGITS = {"0": "ноль", "1": "один", "2": "два", "3": "три", "4": "четыре", "5": "пять", "6": "шесть", "7": "семь",
           "8": "восемь", "9": "девять"}


def _letters(word: str) -> str:
    w = str(word).lower()
    w = "".join(_DIGITS.get(c, c) for c in w)
    return re.sub(r"[^а-яё]", "", w) or "а"


def viseme_keys(words: list[dict]) -> list[tuple[float, float, tuple[float, float, float]]]:
    """``[(t0, t1, (open, round, wide))]`` — буквы слова поровну по его длительности, гласные длиннее (×1.7);
    согласные — полуоткрыто с формой соседней гласной (коартикуляция), губные — закрыто."""
    keys = []
    for w in words:
        t0, t1 = float(w["start"]), float(w["end"])
        if t1 - t0 < 0.03:
            continue
        letters = _letters(w["word"])
        weights = [1.7 if c in VOWELS else 1.0 for c in letters]
        total = sum(weights)
        t = t0
        for i, c in enumerate(letters):
            d = (t1 - t0) * weights[i] / total
            if c in VISEMES:
                v = VISEMES[c]
            else:
                near = next((letters[j] for j in (i + 1, i - 1, i + 2) if 0 <= j < len(letters) and letters[j] in VOWELS), "")
                o, r, wd = VISEMES.get(near, (0.3, 0.0, 0.3))
                v = (0.22, r * 0.6, wd * 0.6)
            keys.append((t, t + d, v))
            t += d
    return keys


def lipsync(words: list[dict], n_frames: int, fps: int = FPS, envelope: list[float] | None = None,
            windows: list[tuple[float, float]] | None = None, gain: float = 1.0) -> list[tuple[float, float, float]]:
    """Покадрово ``(open, round, wide)`` 0..1: визем-цели по таймингам слов, между словами рот закрыт;
    атака быстрее отпускания; огибающая озвучки (если есть) — громче → шире. ``windows`` — где рот говорит
    (вне окон — закрыт, на краях — 0.12 с перехода)."""
    keys = viseme_keys(words)
    target = [(0.0, 0.0, 0.0)] * n_frames
    ki = 0
    for f in range(n_frames):
        t = f / fps
        while ki < len(keys) and keys[ki][1] <= t:
            ki += 1
        if ki < len(keys) and keys[ki][0] <= t < keys[ki][1]:
            target[f] = keys[ki][2]
    env = None
    if envelope:
        peak = sorted(envelope)[int(len(envelope) * 0.95)] or 1.0
        env = [min(1.2, max(0.0, e / peak)) for e in envelope]
    out = []
    o = r = w = 0.0
    dt = 1.0 / fps
    for f in range(n_frames):
        to, tr, tw = target[f]
        if env is not None and f < len(env):
            to *= 0.35 + 0.75 * env[f]
        to = min(1.0, to * gain)
        k_open = 1.0 - math.exp(-dt / (0.028 if to > o else 0.05))
        k_shape = 1.0 - math.exp(-dt / 0.06)
        o += (to - o) * k_open
        r += (tr - r) * k_shape
        w += (tw - w) * k_shape
        out.append((o, r, w))
    if windows is not None:
        def gate(t: float) -> float:
            best = 0.0
            for a, b in windows:
                if a - 0.12 <= t <= b + 0.12:
                    best = max(best, min(1.0, (t - (a - 0.12)) / 0.12, ((b + 0.12) - t) / 0.12))
            return best
        out = [(o * gate(f / fps), r, w) for f, (o, r, w) in enumerate(out)]
    return [(round(o, 4), round(r, 4), round(w, 4)) for o, r, w in out]


# --------------------------------------------------------------------------- #
# Исполнение: покадровые дорожки
# --------------------------------------------------------------------------- #

def _pulse(x: float, rise: float = 0.09, decay: float = 0.45) -> float:
    if x < 0:
        return 0.0
    if x < rise:
        return m.smoothstep(x / rise)
    return math.exp(-(x - rise) / decay)


def _ang(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


class _Track:
    """Ключи ``(t, значение)`` со сглаживанием, значение — число/кортеж/строка (строка — ступенькой)."""

    def __init__(self, default: Any):
        self.keys: list[tuple[float, Any]] = [(0.0, default)]

    def key(self, t: float, v: Any) -> None:
        self.keys.append((float(t), v))

    def curve(self, ease: str = "smooth") -> m.Curve:
        return m.Curve(sorted(self.keys, key=lambda k: k[0]), ease=ease)


def perform(job: dict, clip_len: dict | None = None, stride: float | None = None) -> dict:
    """Задание серии → покадровые дорожки исполнения (детерминированно; то же в Blender и в хэшах кусков)."""
    clip_len = {**CLIP_LEN, **(clip_len or {})}
    stride = float(stride or STRIDE)
    fps = int(job.get("fps") or FPS)
    dur = float(job["duration"])
    nf = int(round(dur * fps))
    times = [f / fps for f in range(nf)]
    shots = job["shots"]
    events = job.get("events") or []
    rnd = random.Random(int(job.get("seed") or 7))

    track = m.ClipTrack()
    walks: list[dict] = []           # окна шага: корень по опорной стопе клипа (walk_root)
    pos_keys: list[tuple[float, tuple[float, float]]] = []   # телепорт на склейке
    head_keys = _Track(0.0)
    gaze = _Track((0.0, FRONT, 1.0))
    reaches: list[dict] = []          # правая рука: нажатия/стук ({side, target(t), infl(t)} как Reach)
    holds: list[dict] = []            # руки в системе героя (телефон, двери)
    emo = _Track(EMOTIONS["calm"])
    lean = _Track(0.0)                # наклон груди: + вперёд, − назад
    roll = _Track(0.0)                # голова набок (слушает)
    startles: list[tuple[float, float]] = []
    phone_iv: list[list] = []         # [начало, конец|None] — телефон в левой руке
    phone_mode = _Track(0.0)          # 0 — экран, 1 — фонарик
    show_track = _Track(0.0)          # 1 — телефон вытянут экраном к камере (значение на экране)
    flash_track = _Track(0.0)         # фонарик включён
    contacts: list[dict] = []         # касания (для подгонки IK по мешу и звуков)
    shout_bursts: list[tuple[float, float]] = []
    speak_windows: list[tuple[float, float]] = []
    seg_clip = None

    def add_clip(name: str, t: float, blend: float = 0.3, speed: float = 1.0, loop: bool = True, offset: float = 0.0):
        nonlocal seg_clip
        if track.segments and t < track.segments[-1].start + 0.05:
            t = track.segments[-1].start + 0.05
        track.add(m.ClipSegment(name, t, clip_len[name], blend=blend, speed=speed, loop=loop, offset=offset))
        seg_clip = name

    blackout = next((e["t"] for e in events if e["kind"] == "blackout"), None)
    light_back = next((e["t"] for e in events if e["kind"] == "light_back" and blackout is not None and e["t"] > blackout), None)

    def dark(t: float) -> bool:
        return blackout is not None and t >= blackout and (light_back is None or t < light_back)

    first_spot, first_head = spot_for(shots[0].get("target") if shots[0]["action"] == "approach" else "center")
    if shots and shots[0].get("location") in LOCS and shots[0]["location"] != "cabin":
        first_spot, first_head = loc_spot(shots[0]["location"], "entry")
    pos, heading, posture = first_spot, first_head, "stand"
    phone_out = False
    phone_hold: dict | None = None
    cur_loc = shots[0].get("location") if shots and shots[0].get("location") in LOCS else "cabin"
    door_tracks: dict[str, _Track] = {}
    pos_keys.append((0.0, pos))
    head_keys.keys = [(0.0, heading)]
    add_clip("Idle_A", 0.0, blend=0.01)
    shot_out = []
    for k, s in enumerate(shots):
        t0, t1 = float(s["t0"]), float(s["t1"])
        a = s["action"]
        span = t1 - t0
        loc = s.get("location") if s.get("location") in LOCS else "cabin"
        if loc != cur_loc:
            # другое место — склейкой: герой уже там, стоит у входа (телефон в руке остаётся)
            spot0, h0 = loc_spot(loc, "entry" if loc != "cabin" else "center")
            pos_keys.append((t0, spot0))
            head_keys.key(t0 - 1e-3, heading)
            head_keys.key(t0, h0)
            pos, heading, posture = spot0, h0, "stand"
            if seg_clip != "Idle_A":
                add_clip("Idle_A", t0, blend=0.01)
            cur_loc = loc
        origin = loc_of(loc)["origin"]

        def W(p_: tuple, o_=origin) -> tuple:
            return (p_[0] + o_[0], p_[1] + o_[1], *p_[2:])
        dtrack = door_tracks.setdefault(loc, _Track(DOOR_OPEN.get(loc, 0.0)))
        # куда и как стоять
        if a == "press_button":
            want = ["1", "3", "2", "4"] if s.get("button") == "all" else [reachable(s.get("button") or "1")]
            want = [reachable(b) for b in want]
            spot, hd = press_stand(want)
        elif a == "knock":
            spot, hd = spot_for("knock")
            spot = W(spot)
        elif a == "pry_doors":
            spot, hd = spot_for("pry")
            spot = W(spot)
        elif a == "sit_corner":
            spot, hd = spot_for("corner")
        elif a == "approach":
            spot, hd = loc_spot(loc, s.get("target") or "center")
        elif loc == "cabin" and a in ("shout", "listen") and posture == "stand" and abs(_ang(heading)) > math.radians(35):
            spot, hd = pos, 0.0
        elif loc == "cabin" and posture == "stand" and pos[1] < FRONT + 0.45 and a not in ("shout", "listen", "flinch"):
            # у самых дверей лицу некуда снимать — отходит в середину кабины
            spot, hd = spot_for("center")
        else:
            spot, hd = pos, heading
            # фраза называет вещь за спиной (лампа, календарь, глубина коридора) — герой к ней повёрнут: за плечом
            # видно и его, и её (иначе «через плечо» — пустой коридор, а план без героя — брак)
            named = [x["point"] for x in show_items(loc, s) if x.get("point") and not str(x["item"]).startswith("место")]
            if named and loc != "cabin" and a not in HAND_ACTIONS and a not in ("look_camera", "phone_out") \
                    and posture == "stand" and not s.get("hook"):
                p_ = named[0]
                want_h = m.heading_of((p_[0] - pos[0], p_[1] - pos[1]))
                if math.hypot(p_[0] - pos[0], p_[1] - pos[1]) > 0.35 and abs(_ang(want_h - heading)) > math.radians(55):
                    # разворот склейкой: на новом плане герой уже смотрит туда
                    head_keys.key(t0 - 1e-3, heading)
                    head_keys.key(t0, want_h)
                    heading = hd = want_h
                fx_, fy_ = facing(heading)
                if not inside_loc(loc, (pos[0] - fx_ * 1.1, pos[1] - fy_ * 1.1, 1.0), 0.05):
                    # спиной в стену/двери — камере за плечом негде встать: склейкой на шаг-другой вперёд
                    for k_ in (0.5, 0.75, 1.0, 1.3):
                        p2 = (round(pos[0] + fx_ * k_, 4), round(pos[1] + fy_ * k_, 4))
                        if inside_loc(loc, (p2[0] - fx_ * 1.1, p2[1] - fy_ * 1.1, 1.0), 0.05) and \
                                inside_loc(loc, (p2[0], p2[1], 1.0), 0.25):
                            pos_keys.append((t0, p2))
                            pos = spot = p2
                            break
            if s.get("screen") and posture == "stand":
                # экран телефона снимают спереди: перед героем нужно ~0.7 м места (не носом в двери/стену)
                for dh_ in (0.0, math.pi, math.pi / 2, -math.pi / 2):
                    fx_, fy_ = facing(heading + dh_)
                    if inside_loc(loc, (pos[0] + fx_ * 0.75, pos[1] + fy_ * 0.75, 1.0), 0.05):
                        if dh_:
                            head_keys.key(t0 - 1e-3, heading)
                            head_keys.key(t0, heading + dh_)
                            heading = hd = heading + dh_
                        break
        moved = math.dist(spot, pos) > 0.04
        tt = t0
        # встать с пола (на камеру) или переставить на склейке
        if posture == "sit" and (s.get("stand_first") or a == "stand_up" or moved):
            if a == "stand_up" or (s.get("stand_first") and not moved):
                add_clip("Sit_Floor_StandUp", tt + 0.05, blend=0.2, loop=False)
                tt += clip_len["Sit_Floor_StandUp"] + 0.05
                add_clip("Idle_A", tt - 0.15, blend=0.25)
            else:
                add_clip("Idle_A", t0, blend=0.01)
            posture = "stand"
        walk_dist = math.dist(spot, pos)
        steps, clip_sec = walk_plan(walk_dist)
        wsec = walk_seconds(clip_sec)
        dirv = (spot[0] - pos[0], spot[1] - pos[1]) if moved else (0.0, 0.0)
        walk_head = m.heading_of(dirv) if moved else heading
        t_turn0 = 0.25 + 0.45 * abs(_ang(walk_head - heading)) / math.pi if moved else 0.0
        far = walk_dist > MAX_WALK_STEPS * STEP_LEN * 1.25
        if moved:   # шаги разворота в начале/конце (см. ниже) — тоже время шага
            _h = clip_len[WALK_CLIP] / 2
            _d0, _d1 = abs(_ang(walk_head - heading)), abs(_ang(hd - walk_head))
            wsec = walk_seconds(clip_sec + (_h if _d0 > math.radians(100) else 0.0)
                                + (0.0 if _d1 <= math.radians(40) else _h * (2 if _d1 > math.radians(100) else 1)))
        if moved and (k == 0 or far or t_turn0 + wsec > (0.9 if a == "approach" else 0.6) * span or a == "sit_corner" and walk_dist > 0.45):
            # склейка: герой уже на месте (переход между кадрами не показываем)
            pos_keys.append((t0, spot))
            head_keys.key(t0 - 1e-3, heading)
            head_keys.key(t0, hd)
            if posture == "stand" and seg_clip not in ("Idle_A",):
                add_clip("Idle_A", t0, blend=0.01)
            pos, heading = spot, hd
        elif moved:
            # сначала взгляд к цели; корпус: малый поворот — на месте до шага, большой — на первом шаге (вокруг
            # опорной стопы); 1–3 шага с разгоном и торможением; к цели лицом — на последнем шаге (большой — лишний шаг)
            gaze.key(tt, (spot[0], spot[1], 0.5))
            dh0 = _ang(walk_head - heading)
            h_walk = heading + dh0
            dh_end = _ang(hd - walk_head)
            half = clip_len[WALK_CLIP] / 2
            # большой разворот — на шаге (корпус вокруг опорной стопы); больше 100° — за два шага, не рывком
            turn0 = half * (2 if abs(dh0) > math.radians(100) else 1)
            turn1 = half * (2 if abs(dh_end) > math.radians(100) else 1)
            if abs(dh0) > math.radians(100):
                clip_sec += half
            if abs(dh_end) > math.radians(40):
                clip_sec += turn1
            pre = t_turn0 if abs(dh0) <= math.radians(40) else 0.15
            head_keys.key(tt, heading)
            ws = tt + pre - 0.05
            we = ws + walk_seconds(clip_sec)
            if abs(dh0) <= math.radians(40):
                head_keys.key(tt + pre, h_walk)
            else:
                head_keys.key(ws + 0.25, heading)
                head_keys.key(ws + 0.25 + turn0, h_walk)
            track.add(m.ClipSegment(WALK_CLIP, max(ws, track.segments[-1].start + 0.05), clip_len[WALK_CLIP], blend=0.3,
                                    loop=True, offset=WALK_PASS[0], ease_in=0.35, ease_out=0.35, end=we))
            walks.append({"t0": ws, "t1": we + 0.4, "target": spot, "kind": "walk"})
            h_end = h_walk + dh_end
            if abs(dh_end) > math.radians(40):
                head_keys.key(we - turn1 - 0.1, h_walk)
                head_keys.key(we - 0.15, h_end)
            else:
                head_keys.key(we - 0.45, h_walk)
                head_keys.key(we + 0.25, h_end)
            add_clip("Idle_A", we - 0.05, blend=0.4)
            heading, pos = h_end, spot
            tt = we + 0.2
        elif abs(_ang(hd - heading)) > math.radians(18):
            dh = _ang(hd - heading)
            mid = facing(heading + dh / 2)
            p_end = (pos[0] + mid[0] * STEP_LEN, pos[1] + mid[1] * STEP_LEN)
            room = inside_loc(loc, (p_end[0], p_end[1], 1.0), 0.3)
            if abs(dh) > math.radians(50) and room and posture == "stand" and span > 2.2:
                # разворот шагом: один шаг (корень по опорной стопе), корпус поворачивается на шаге
                ws = tt + 0.05
                we = ws + walk_seconds(CLIP_LEN[WALK_CLIP] / 2, 0.3)
                track.add(m.ClipSegment(WALK_CLIP, max(ws, track.segments[-1].start + 0.05), clip_len[WALK_CLIP],
                                        blend=0.25, loop=True, offset=WALK_PASS[0], ease_in=0.3, ease_out=0.3, end=we))
                walks.append({"t0": ws, "t1": we + 0.3, "target": None, "kind": "turn"})
                head_keys.key(ws, heading)
                head_keys.key(we - 0.1, heading + dh)
                add_clip("Idle_A", we - 0.05, blend=0.35)
                pos = p_end
                heading += dh
                tt = we + 0.1
            else:
                # небольшой поворот — на месте, плавно (ноги почти не уходят)
                t_turn = 0.35 + 0.6 * abs(dh) / math.pi
                head_keys.key(tt + 0.05, heading)
                head_keys.key(tt + 0.05 + t_turn, heading + dh)
                heading += dh
                tt += t_turn
        a0 = min(tt + 0.05, t1 - 0.3)
        # эмоция кадра (наплыв 0.35 с)
        emo.key(t0 + 0.02, emo.keys[-1][1] if emo.keys else EMOTIONS["calm"])
        emo.key(t0 + 0.37, EMOTIONS.get(s.get("emotion") or "calm", EMOTIONS["calm"]))
        lean.key(t0, lean.keys[-1][1])
        roll.key(t0, roll.keys[-1][1])
        lean_v, roll_v = 0.0, 0.0
        shot_end = t1

        def gkey(t: float, v: Any) -> None:
            if t < shot_end - 0.05:
                gaze.key(max(t, t0), v)
        cam_subject = "face"
        # действие
        if a == "press_button":
            seq = ["1", "3", "2", "4"] if s.get("button") == "all" else [reachable(s.get("button") or "1")]
            seq = [reachable(b) for b in seq]
            per = 0.55 if len(seq) > 1 else 1.4
            if s.get("button") == "bell":
                seq = ["bell", "bell"]
                per = 0.9
            t = a0 + 0.1
            rest = None
            for b in seq:
                if t + per > t1 + 0.2:
                    break
                bp = button_pos(b)
                if len(seq) > 1:
                    r = {"button": bp, "normal": BTN_NORMAL, "t_start": t, "t_contact": t + per * 0.55,
                         "t_release": t + per * 0.72, "t_end": t + per * 1.02, "hover": 0.04, "depth": 0.006,
                         "id": b, "side": "r", "rest": rest}
                else:
                    r = {"button": bp, "normal": BTN_NORMAL, "t_start": t, "t_contact": t + 0.6, "t_release": t + 0.85,
                         "t_end": t + 1.4, "hover": 0.05, "depth": 0.006, "id": b, "side": "r", "rest": rest}
                reaches.append(r)
                contacts.append({"kind": "press", "t": r["t_contact"], "button": b, "point": bp, "normal": BTN_NORMAL})
                rest = m.v_add(bp, m.v_scale(BTN_NORMAL, 0.05))
                gkey(t - 0.15, bp)
                t += per * (0.75 if len(seq) > 1 else 1.0)
            gkey(t + 0.2, DISPLAY if s.get("button") != "bell" else (BELL[0], BELL[1], BELL[2] + 0.25))
            add_clip("Idle_A", a0, blend=0.3) if seg_clip != "Idle_A" else None
            cam_subject = "press"
        elif a == "knock":
            hits = [a0 + 0.35 + i * 0.38 for i in range(3)]
            hits = [h for h in hits if h < t1 - 0.15] or [a0 + 0.2]
            kp = W(KNOCK_POINT)
            reaches.append({"button": kp, "normal": DOOR_NORMAL, "t_start": a0, "t_contact": hits[0],
                            "t_release": hits[-1] + 0.05, "t_end": hits[-1] + 0.6, "hover": 0.06, "depth": 0.004,
                            "id": "door", "side": "r", "hits": hits})
            for h in hits:
                contacts.append({"kind": "knock", "t": h, "point": kp, "normal": DOOR_NORMAL})
            gkey(a0 - 0.1, (kp[0], kp[1], 0.85))
            lean_v = 0.12
            cam_subject = "doors"
        elif a == "pry_doors":
            full = bool(re.search(r"раздвинул|открыл|разжал|распахнул", _low(s.get("text"))))
            t_grip = a0 + 0.45
            open_t = t_grip + 0.5
            holds.append({"side": "both", "t0": a0, "t1": t1 + 0.4, "grip": t_grip, "mode": "doors", "origin": origin,
                          "loc": loc})
            base_gap = dtrack.keys[-1][1]
            dtrack.key(open_t, base_gap)
            if full:
                dtrack.key(open_t + 1.1, 0.24)
            else:
                dtrack.key(open_t + 0.4, base_gap + 0.025)
                dtrack.key(open_t + 0.8, base_gap + 0.012)
                dtrack.key(min(t1, open_t + 1.5), base_gap + 0.03)
                dtrack.key(min(t1 + 0.3, open_t + 2.0), base_gap)
            contacts.append({"kind": "doors", "t": open_t, "full": full})
            gkey(a0 - 0.1, W((0.0, DOOR_FACE_Y, 0.75)))
            # тужится — отворачивает лицо вбок (к камере детали, она с +X): лицо видно, а не капюшон
            gkey(t_grip + 0.35, W((0.70, FRONT + 0.12, 0.85)))
            if full:
                gkey(open_t + 1.3, W((0.0, FRONT - 0.4, 0.45)))     # в шахту: внизу край этажа
            lean_v = -0.16
            if phone_out:
                phone_iv[-1][1] = a0 + 0.3        # обе руки на дверях — телефон в карман
                if phone_hold is not None:
                    phone_hold["t1"] = a0 + 0.3
                phone_out = False
            cam_subject = "doors"
        elif a == "sit_corner":
            if posture != "sit":
                add_clip("Sit_Floor_Down", a0, blend=0.25, loop=False)
                add_clip("Sit_Floor_Idle", a0 + clip_len["Sit_Floor_Down"] - 0.1, blend=0.2)
            posture = "sit"
            gkey(a0 + 0.2, (0.0, FRONT, 0.55))
            cam_subject = "face"
        elif a == "stand_up":
            gkey(a0, loc_target(loc, "key"))
        elif a in ("phone_out", "phone_light"):
            if not phone_out:
                phone_iv.append([a0, None])
                phone_hold = {"side": "l", "t0": a0, "t1": None, "mode": "phone", "from": "pocket"}
                holds.append(phone_hold)
                phone_out = True
            aim = a == "phone_light"
            phone_mode.key(a0 + 0.55, phone_mode.keys[-1][1])
            phone_mode.key(a0 + 0.8, 1.0 if aim else 0.0)
            if aim or dark(a0 + 0.5):
                flash_track.key(a0 + 0.85, flash_track.keys[-1][1])
                flash_track.key(a0 + 0.9, 1.0)
            gkey(a0 + 0.1, "phone")
            if aim:
                # светит туда, куда смотрит: на двери, потом ведёт лучом по кабине
                if loc == "cabin":
                    tgt = (0.0, DOOR_FACE_Y, 0.9) if rnd.random() < 0.5 else (DISPLAY[0], DISPLAY[1], DISPLAY[2] - 0.1)
                else:
                    tgt = loc_target(loc, "key")
                gkey(a0 + 0.9, tgt)
                gkey(a0 + 0.9 + max(0.6, (t1 - a0) * 0.45), (tgt[0] + 0.35, tgt[1] + 0.25, tgt[2] + 0.25))
            cam_subject = "phone"
        elif a == "look_display":
            gkey(a0, loc_target(loc, "key"))
            cam_subject = "display"
        elif a == "look_ceiling":
            if loc == "cabin":
                gkey(a0, HATCH)
                gkey(a0 + max(0.6, span * 0.55), (CEIL_LIGHT[0] - 0.15, CEIL_LIGHT[1] - 0.2, H))
            else:
                gkey(a0, loc_target(loc, "ceiling"))
            cam_subject = "face"
        elif a == "look_camera":
            gkey(a0, "camera")
        elif a == "flinch":
            ev = [e["t"] for e in events if t0 - 0.2 <= e["t"] < t1 and e["kind"] in ("blackout", "jolt", "knock", "scrape", "flash")]
            tf = ev[0] if ev else a0 + 0.15
            startles.append((tf + 0.05, 1.0))
            add_clip("Hit_A", max(tt, tf), blend=0.12, loop=False) if posture == "stand" else None
            add_clip("Idle_A", max(tt, tf) + clip_len["Hit_A"] - 0.05, blend=0.3) if posture == "stand" else None
            gkey(tf + 0.1, (CEIL_LIGHT[0], CEIL_LIGHT[1], H) if loc == "cabin" else loc_target(loc, "ceiling"))
        elif a == "shout":
            gkey(a0, (0.0, DOOR_FACE_Y, 1.05) if loc == "cabin" else loc_target(loc, "sound"))
            lean_v = 0.18
            # кричит без своей реплики в сценарии — рот закрыт (только испуг/напряжение): рот — лишь под речь героя
        elif a == "listen":
            gkey(a0, (0.05, DOOR_FACE_Y, 0.8) if loc == "cabin" else loc_target(loc, "sound"))
            roll_v = 0.22
            if seg_clip == "Idle_A" and posture == "stand":
                add_clip("Idle_B", a0, blend=0.4, speed=0.6)
        elif a == "wait":
            if posture == "stand" and seg_clip != "Idle_B":
                add_clip("Idle_B", a0, blend=0.4, speed=0.7)
            if loc == "cabin":
                gkey(a0, (0.1, FRONT + 0.3, 0.0) if rnd.random() < 0.5 else DISPLAY)
                gkey(a0 + span * 0.5, DISPLAY if dark(a0) else (0.3, FRONT, 0.6))
            else:
                gkey(a0, loc_target(loc, "key"))
                gkey(a0 + span * 0.5, loc_target(loc, "sound"))
            lean_v = -0.05
        for e in events:
            if e["kind"] == "doors_close" and t0 <= e["t"] < t1:
                dtrack.key(e["t"], dtrack.keys[-1][1])
                dtrack.key(e["t"] + 0.22, 0.0)
                startles.append((e["t"] + 0.1, 0.8))
                gkey(e["t"] + 0.12, W((0.0, DOOR_FACE_Y, 0.8)))
        if phone_out and a not in ("phone_out", "phone_light"):
            # телефон уже в руке — светит туда, куда смотрит (экраном к себе — только когда смотрит в телефон)
            phone_mode.key(a0 + 0.1, phone_mode.keys[-1][1])
            phone_mode.key(a0 + 0.5, 1.0)

        show_track.key(t0, show_track.keys[-1][1])
        show_track.key(a0 + 0.5 if s.get("screen") else t0 + 0.35, 1.0 if s.get("screen") else 0.0)
        lean.key(t0 + 0.4, lean_v)
        roll.key(t0 + 0.4, roll_v)
        shot_out.append({**s, "a0": round(a0, 3), "spot": [round(pos[0], 4), round(pos[1], 4)], "heading": round(heading, 4),
                         "posture_end": posture, "subject": cam_subject, "phone": phone_out, "location": loc,
                         "origin": list(origin), "key_target": list(loc_target(loc, "key")),
                         "show": show_items(loc, s)})

    # события → испуг, взгляд на источник
    for e in events:
        if e["kind"] in ("blackout", "jolt", "knock", "scrape", "flash"):
            amp = {"blackout": 1.0, "jolt": 0.9, "knock": 0.55, "scrape": 0.5, "flash": 0.7}[e["kind"]]
            if not any(abs(t - e["t"]) < 0.5 for t, _ in startles):
                startles.append((e["t"] + 0.08, amp))
    startles.sort()

    # покадрово: корень
    tl = track
    head_c = head_keys.curve()
    heads = [round(head_c.at(t), 5) for t in times]
    root = walk_root(tl, sorted(pos_keys, key=lambda k: k[0]), walks, heads, times)

    # руки: правая (нажатия/стук), левая и обе (телефон, двери)
    hand = {"r": [None] * nf, "l": [None] * nf}
    for r in reaches:
        rc = m.Reach(button=tuple(r["button"]), normal=tuple(r["normal"]), t_start=r["t_start"], t_contact=r["t_contact"],
                     t_release=r["t_release"], t_end=r["t_end"], hover=r["hover"], depth=r["depth"],
                     rest=tuple(r["rest"]) if r.get("rest") else None)
        hits = r.get("hits")
        for f, t in enumerate(times):
            w = rc.influence(t)
            if w <= 0:
                continue
            p = rc.target_at(t)
            if hits:
                # стук: кулак отводится и бьёт на каждом ударе
                near = min(hits, key=lambda h: abs(h - t))
                x = (t - near) / 0.19
                back = 0.07 * (1.0 - math.exp(-(x * x) * 3.0)) if r["t_contact"] - 0.05 <= t <= r["t_release"] else 0.0
                p = m.v_add(p, m.v_scale(r["normal"], back))
            prev = hand["r"][f]
            if prev is None or prev[1] < w:
                hand["r"][f] = (tuple(round(c, 5) for c in p), round(w, 4), r["id"])
    pm_c = phone_mode.curve()
    show_c = show_track.curve()
    door_cs = {k: v.curve() for k, v in door_tracks.items()}
    door_c0 = door_cs.get("cabin") or _Track(0.0).curve()
    for h in holds:
        for f, t in enumerate(times):
            if t < h["t0"] or (h["t1"] is not None and t > h["t1"]):
                continue
            rp, hd = root[f], heads[f]
            sit = 1.0 if _posture_at(shot_out, t) == "sit" else 0.0
            if h["mode"] == "phone":
                x = m.smootherstep((t - h["t0"]) / 0.65)
                look = (-0.035, 0.20, 0.50 - SIT_DROP * sit)
                light = (-0.07, 0.25, 0.57 - SIT_DROP * sit)
                pocket = (-0.13, 0.04, 0.32 - SIT_DROP * sit)
                mode = pm_c.at(t)
                held = m.lerp(look, light, mode)
                show = (PHONE_SHOW[0], PHONE_SHOW[1], PHONE_SHOW[2] - SIT_DROP * sit)
                held = m.lerp(held, show, show_c.at(t))
                loc = m.lerp(pocket, held, x)
                w = m.smoothstep((t - h["t0"]) / 0.25)
                hand["l"][f] = (to_world(rp, hd, loc), round(w, 4), "phone")
            elif h["mode"] == "doors":
                op = (door_cs.get(h.get("loc") or "cabin") or door_c0).at(t)
                ox, oy = h.get("origin") or (0.0, 0.0)
                w = m.smoothstep((t - h["t0"]) / max(h["grip"] - h["t0"], 1e-3))
                if h["t1"] is not None:
                    w *= 1.0 - m.smoothstep((t - (h["t1"] - 0.4)) / 0.4)
                for side, sx in (("r", -1.0), ("l", 1.0)):
                    # створки разъезжаются — кисти на кромках (x: −1 — вправо от героя к −X мира)
                    p = (ox + sx * (0.045 + op), oy + DOOR_FACE_Y + 0.025, 0.62)
                    if w > 0 and (hand[side][f] is None or hand[side][f][1] < w):
                        hand[side][f] = (p, round(w, 4), "doors")
    # телефон виден в своих интервалах (в кармане — нет), левая рука держит его только тогда
    vis = [1.0 if any(a <= t < (b if b is not None else 1e9) for a, b in phone_iv) else 0.0 for t in times]
    for f in range(nf):
        if not vis[f] and hand["l"][f] and hand["l"][f][2] == "phone":
            hand["l"][f] = None

    # взгляд и мимика
    gaze_c = gaze.curve(ease="step")
    gz = []
    for t in times:
        g = gaze_c.at(t)
        here = next((x_["location"] for x_ in shot_out if x_["t0"] <= t < x_["t1"]), shot_out[-1]["location"])
        for e in events:
            if e["kind"] in ("blackout", "scrape") and e["t"] + 0.15 <= t < e["t"] + 1.1:
                g = (CEIL_LIGHT[0], CEIL_LIGHT[1], H) if here == "cabin" else loc_target(here, "ceiling")
            elif e["kind"] == "knock" and e["t"] + 0.1 <= t < e["t"] + 1.2:
                g = (0.0, DOOR_FACE_Y, 0.85) if here == "cabin" else loc_target(here, "sound")
        gz.append(g if isinstance(g, str) else tuple(round(c, 4) for c in g))
    emo_c, lean_c, roll_c = emo.curve(), lean.curve(), roll.curve()
    blinks = m.blink_times(dur, seed=int(job.get("seed") or 7) + 4, min_gap=1.9, max_gap=4.0,
                           forced=[t for t, _ in startles])
    speak_windows = [tuple(w) for w in ((job.get("speech") or {}).get("hero_windows") or [])]
    lip_mode = job.get("lipsync") or "speaking"
    windows = None if lip_mode == "all" else ([] if lip_mode == "off" else speak_windows)
    mouth = lipsync(job.get("words") or [], nf, fps, job.get("envelope"), windows)
    face, body = [], []
    for f, t in enumerate(times):
        b, wy, wd = emo_c.at(t)
        st = sum(amp * _pulse(t - ts) for ts, amp in startles if t >= ts - 0.01)
        st = min(1.0, st)
        b = max(b, st)
        wd = max(wd, st)
        o, r, w = mouth[f]
        # испуг — рот «о», крик — широко
        o = max(o, 0.7 * st)
        r = max(r, 0.8 * st if st > 0.3 else r)
        for a_, b_ in shout_bursts:
            if a_ <= t <= b_:
                x = (t - a_) / (b_ - a_)
                o = max(o, 0.95 * math.sin(math.pi * x))
                r = min(r, 0.3)
        face.append({"brow": round(b, 4), "worry": round(wy, 4), "wide": round(wd, 4),
                     "blink": round(m.blink_amount(t, blinks), 4), "mouth": (round(o, 4), round(r, 4), round(w, 4))})
        body.append({"startle": round(st, 4), "lean": round(lean_c.at(t), 4), "roll": round(roll_c.at(t), 4)})

    # свет, табло, двери, тряска
    lights = []
    floors = [e for e in events if e["kind"] == "floor"]
    start_floor = floors[0]["value"] if floors else "22"
    jolts = [e["t"] for e in events if e["kind"] in ("jolt", "scrape")]
    flick = [e["t"] for e in events if e["kind"] == "flicker"]
    flashes = [e["t"] for e in events if e["kind"] == "flash"]
    lit_at: dict[str, float] = {}
    for c in contacts:
        if c["kind"] == "press" and c["button"] != "bell":
            lit_at.setdefault(c["button"], c["t"])
    door_c = door_c0
    named_display = [(float(x_["t0"]), float(x_["t1"])) for x_ in shot_out if x_["location"] == "cabin"
                     and any(it["item"] == "display" or str(it["item"]).startswith("надпись") for it in x_["show"])]
    for t in times:
        main = 1.0
        if blackout is not None:
            if t >= blackout - 0.9 and (light_back is None or t < light_back):
                main = m.flicker(t, blackout - 0.9, blackout, seed=5)
            if light_back is not None and t >= light_back:
                main = m.smoothstep((t - light_back) / 0.15)
        for tf in flick:
            if tf <= t < tf + 0.9:
                main *= m.flicker(t, tf, tf + 10.0, seed=int(tf * 10)) * 0.8 + 0.2
        for tj in jolts:
            if tj <= t < tj + 0.35:
                main *= 0.35 + 0.65 * ((t - tj) / 0.35)
        red = 0.0
        if blackout is not None and t >= blackout + 0.25 and (light_back is None or t < light_back):
            x = t - blackout - 0.25
            red = m.smoothstep(x / 0.12) * (0.55 + 0.45 * m.smoothstep(x / 0.6)) * (0.9 + 0.1 * math.sin(2 * math.pi * x / 1.6))
            for tj in jolts:
                if tj <= t < tj + 0.3:
                    red *= 0.4 + 0.6 * ((t - tj) / 0.3)
        flash = max([math.exp(-(t - tf) / 0.12) for tf in flashes if t >= tf] or [0.0])
        disp_on = 1.0
        value = start_floor
        for e in floors[1:]:
            if t >= e["t"]:
                value = e["value"]
        if blackout is not None and t >= blackout + 0.04 and (light_back is None or t < light_back):
            disp_on = 0.0
            for e in floors[1:]:
                on_t = max(blackout + 1.0, e["t"] - 1.8)
                if e["t"] > blackout and t >= on_t:
                    disp_on = m.smoothstep((t - on_t) / 0.25)
            for a_, b_ in named_display:
                if a_ <= t < b_:     # фраза называет табло — оно горит (аварийное питание), иначе в темноте не видно
                    disp_on = max(disp_on, 0.85 * m.smoothstep((t - a_ + 0.15) / 0.25))
        shake = 0.0
        for e in events:
            amp = {"blackout": 0.006, "jolt": 0.03, "scrape": 0.01, "knock": 0.004}.get(e["kind"], 0.0)
            ln = {"blackout": 0.6, "jolt": 0.9, "scrape": 1.2, "knock": 0.4}.get(e["kind"], 0.5)
            if amp and e["t"] <= t < e["t"] + ln:
                shake = max(shake, amp * (1.0 - (t - e["t"]) / ln))
        drop = 0.0
        for tj in [e["t"] for e in events if e["kind"] == "jolt"]:
            if t >= tj:
                drop += -0.05 * m.smoothstep((t - tj) / 0.12) * math.exp(-max(0.0, t - tj - 0.12) / 0.35)
        lit = sorted(b for b, tb in lit_at.items() if t >= tb)
        lights.append({"main": round(main, 4), "red": round(red, 4), "flash": round(flash, 4), "display": round(disp_on, 4),
                       "floor": value, "doors": round(door_c.at(t), 4), "shake": round(shake, 5), "drop": round(drop, 5),
                       "ldoors": {k: round(c_.at(t), 4) for k, c_ in door_cs.items() if k != "cabin"},
                       "lit": lit, "dark": dark(t)})

    phone = []
    pm = phone_mode.curve()
    fl = flash_track.curve(ease="linear")
    beam_raw = []
    for f, t in enumerate(times):
        # луч — туда, куда смотрит герой (в объектив/на телефон — вперёд в пол: не слепить камеру)
        g = gz[f]
        if isinstance(g, tuple):
            beam_raw.append(g)
        else:
            fx, fy = facing(heads[f])
            beam_raw.append((root[f][0] + fx * 0.9, root[f][1] + fy * 0.9, 0.05))
    perf = {"fps": fps, "frames": nf, "duration": dur, "track": [
        {"clip": sg.clip, "start": round(sg.start, 4), "duration": sg.duration, "blend": sg.blend, "speed": round(sg.speed, 4),
         "loop": sg.loop, "offset": sg.offset, "ease_in": sg.ease_in, "ease_out": sg.ease_out,
         "end": None if sg.end is None else round(sg.end, 4)} for sg in track.segments],
        "root": root, "heading": heads, "hands": hand, "gaze": gz, "face": face, "body": body, "lights": lights,
        "phone": phone, "contacts": contacts, "shots": shot_out, "startles": startles,
        "blackout": blackout, "light_back": light_back, "speak_windows": speak_windows}
    perf["cameras"] = camera_plan(perf, int(job.get("seed") or 7))
    # луч фонарика не в объектив: камера в конусе — увести луч вбок-вниз (иначе кадр — белая вспышка)
    beam_raw = _beam_off_camera(perf, beam_raw)
    beam_s = m.spring_follow(beam_raw, 1.0 / fps, 0.18)
    for f, t in enumerate(times):
        # экран со значением (часы, заряд) — в плане с ``screen``, когда телефон уже у лица (через 0.5 с)
        sh_ = next((x_ for x_ in shot_out if x_["t0"] <= t < x_["t1"]), None)
        scr = str(sh_.get("screen") or "") if sh_ and vis[f] and t >= float(sh_.get("a0", sh_["t0"])) + 0.5 else ""
        phone.append({"on": vis[f], "aim": round(pm.at(t) if vis[f] else 0.0, 4),
                      "light": round(fl.at(t) if vis[f] else 0.0, 4), "beam": tuple(round(c, 4) for c in beam_s[f]),
                      # на экране: «−» в шрифте сцены нет — дефис; «-12°C» короче — крупнее
                      **({"screen": scr.replace("−", "-").replace(" °", "°")} if scr else {})})
    perf["phone"] = phone
    return perf


def _beam_off_camera(perf: dict, beam: list[tuple]) -> list[tuple]:
    fps = perf["fps"]
    starts = [(int(round(c["t0"] * fps)), c) for c in perf["cameras"]]
    out = []
    for f, b in enumerate(beam):
        cam = next((c for f0, c in reversed(starts) if f >= f0), starts[0][1])
        posture = _posture_at(perf["shots"], f / fps)
        ph = to_world(perf["root"][f], perf["heading"][f], (-0.07, 0.25, 0.57 - (SIT_DROP if posture == "sit" else 0.0)))
        to_b = m.v_norm(m.v_sub(b, ph))
        to_c = m.v_norm(m.v_sub(cam["pos_a"], ph))
        if m.v_dot(to_b, to_c) > math.cos(math.radians(40)):
            # повернуть луч от камеры на ~70° по горизонтали и опустить к полу
            side = 1.0 if (to_b[0] * to_c[1] - to_b[1] * to_c[0]) >= 0 else -1.0
            d = _rot((to_c[0], to_c[1]), math.radians(70 * side))
            b = (ph[0] + d[0] * 0.9, ph[1] + d[1] * 0.9, 0.1)
        out.append(b)
    return out


def planted_local(lt: float) -> tuple[str, float, float]:
    """Опорная стопа клипа шага в момент ``lt``: (сторона, x, y) в системе клипа (x — влево, y — назад), ед."""
    n = len(WALK_FEET) - 1
    phi = (lt * 30.0) % n
    i = int(phi)
    a_, b_ = WALK_FEET[i], WALK_FEET[min(i + 1, n)]
    k = phi - i
    ly, lz, ry, rz = (a_[j] + (b_[j] - a_[j]) * k for j in range(4))
    return ("l", WALK_FOOT_X, ly) if lz <= rz else ("r", -WALK_FOOT_X, ry)


def clip_to_world(root: tuple[float, float], heading: float, x: float, y: float) -> tuple[float, float]:
    """Точка клипа (x — влево от взгляда, y — назад; ед. без масштаба) → мир."""
    fx, fy = facing(heading)
    rx, ry = fy, -fx
    return (root[0] + (-x * rx - y * fx) * S, root[1] + (-x * ry - y * fy) * S)


def walk_root(track: m.ClipTrack, pos_keys: list[tuple[float, tuple]], walks: list[dict], heads: list[float],
              times: list[float]) -> list[tuple[float, float]]:
    """Корень по кадрам. Склейки — сразу на месте. На шаге в полный вес опорная стопа клипа стоит на полу: корень
    ставится так, чтобы она не сдвинулась (и поворот корпуса идёт вокруг неё) — root motion из клипа. В наплывах
    шага — корень едет на сдвиг опорной стопы × вес. Шаг к цели доводится до точки поправкой по кадрам окна
    (1–3 шага: миллиметры за кадр)."""
    n = len(times)
    walk = []
    for t in times:
        lt, w = None, 0.0
        for clip, l_, w_ in track.weights_at(t):
            if clip == WALK_CLIP:
                lt, w = l_, w_
        walk.append((lt, w))

    def step(cur: tuple[float, float], f: int) -> tuple[float, float]:
        lt, w = walk[f]
        plt, pw = walk[f - 1] if f else (None, 0.0)
        if lt is None or plt is None or w <= 0:
            return cur
        if w >= 0.999 and pw >= 0.999:
            side0, x0, y0 = planted_local(plt)
            side1, x1, y1 = planted_local(lt)
            if side0 == side1:
                px, py = clip_to_world(cur, heads[f - 1], x0, y0)
                nx, ny = clip_to_world((0.0, 0.0), heads[f], x1, y1)
                return (px - nx, py - ny)
        d = stance_units(lt) - stance_units(plt)
        if d < -WALK_CYCLE_UNITS / 2:          # переход через цикл
            d += WALK_CYCLE_UNITS
        fx, fy = facing(heads[f])
        return (cur[0] + fx * d * S * w, cur[1] + fy * d * S * w)

    keys = list(pos_keys)
    cur = keys[0][1] if keys else (0.0, 0.0)
    out = []
    ki = 1
    win_i = 0
    walks = sorted(walks, key=lambda w_: w_["t0"])
    corr: dict[int, tuple[float, float]] = {}
    for f, t in enumerate(times):
        while ki < len(keys) and keys[ki][0] <= t + 1e-9:
            cur = keys[ki][1]
            ki += 1
        while win_i < len(walks) and walks[win_i]["t0"] <= t:
            w_ = walks[win_i]
            win_i += 1
            if w_["target"] is None:
                continue
            fr = [i for i in range(f, n) if times[i] <= w_["t1"]]
            sim, moves = cur, []
            for i in fr:
                nxt = step(sim, i)
                moves.append(math.dist(sim, nxt))
                sim = nxt
            ex, ey = w_["target"][0] - sim[0], w_["target"][1] - sim[1]
            tot = sum(moves) or 1.0
            corr = {i: (ex * mv / tot, ey * mv / tot) for i, mv in zip(fr, moves)}
        nxt = step(cur, f)
        if f in corr:
            nxt = (nxt[0] + corr[f][0], nxt[1] + corr[f][1])
        cur = nxt
        out.append((round(cur[0], 5), round(cur[1], 5)))
    return out


def foot_slide(perf: dict) -> list[float]:
    """Скольжение опорной стопы (мм за кадр) на кадрах полного веса шага: стопа клипа в мире = корень + поворот ×
    стопа × масштаб; опорная — ниже другой в обоих кадрах."""
    track = track_of(perf)
    fps = perf["fps"]
    out = []
    prev = None
    for f in range(perf["frames"]):
        t = f / fps
        ws = [(lt, w) for c, lt, w in track.weights_at(t) if c == WALK_CLIP]
        if not ws or ws[0][1] < 0.999:
            prev = None
            continue
        side, x, y = planted_local(ws[0][0])
        wx, wy = clip_to_world(tuple(perf["root"][f]), perf["heading"][f], x, y)
        if prev is not None and prev[0] == side:
            out.append(math.hypot(wx - prev[1], wy - prev[2]) * 1000)
        prev = (side, wx, wy)
    return out


def _posture_at(shots: list[dict], t: float) -> str:
    for s in shots:
        if s["t0"] <= t < s["t1"]:
            return s.get("posture_end", "stand") if s.get("action") != "sit_corner" or t >= s.get("a0", s["t0"]) + 0.6 else "stand"
    return shots[-1].get("posture_end", "stand") if shots else "stand"


def track_of(perf: dict) -> m.ClipTrack:
    tr = m.ClipTrack()
    for sg in perf["track"]:
        tr.add(m.ClipSegment(sg["clip"], sg["start"], sg["duration"], blend=sg["blend"], speed=sg["speed"],
                             loop=sg["loop"], offset=sg["offset"], ease_in=sg.get("ease_in", 0.0),
                             ease_out=sg.get("ease_out", 0.0), end=sg.get("end")))
    return tr
