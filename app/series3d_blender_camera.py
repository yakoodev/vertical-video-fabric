"""3D-серия: камера плана и что видно (из ``app/series3d_blender.py``)."""

from __future__ import annotations

import math
import random

from app.series3d_blender_stage import (
    S, D, H, FRONT, LEFT, DOOR_FACE_Y, DISPLAY, DOORWAY, button_pos, facing, to_world, loc_of,
)
from app.series3d_blender_direction import CAMERA_PREFS

FACE_Z = {"stand": 1.62 * S, "sit": 1.28 * S}
SIT_DROP = 0.17                    # насколько ниже грудь сидя (клип Sit_Floor_Idle)
PHONE_SHOW = (-0.06, 0.27, 0.38)   # телефон экраном к камере (часы/заряд): ниже подбородка и вперёд — не прячется за голову


# --------------------------------------------------------------------------- #
# Камера: план кадра → положение и цель (сцена Blender доводит цель по настоящей голове героя)
# --------------------------------------------------------------------------- #

CAM_BOUNDS = (LEFT + 0.06, -LEFT - 0.06, FRONT + 0.06, D / 2 - 0.06, 0.12, H - 0.1)


_CAM = {"bounds": CAM_BOUNDS}     # границы камеры текущего места (camera_plan ставит на каждый план)


def cam_bounds() -> tuple:
    return _CAM["bounds"]


def cam_inside(p: tuple[float, float, float]) -> bool:
    x0, x1, y0, y1, z0, z1 = cam_bounds()
    return x0 <= p[0] <= x1 and y0 <= p[1] <= y1 and z0 <= p[2] <= z1


def _rot(v: tuple[float, float], ang: float) -> tuple[float, float]:
    c, s_ = math.cos(ang), math.sin(ang)
    return (v[0] * c - v[1] * s_, v[0] * s_ + v[1] * c)


def face_point(root: tuple[float, float], heading: float, posture: str) -> tuple[float, float, float]:
    """Середина лица (оценка без Blender): над корнем, чуть впереди, по позе."""
    fx, fy = facing(heading)
    fwd = 0.07 if posture == "stand" else 0.02
    return (root[0] + fx * fwd, root[1] + fy * fwd, FACE_Z["sit" if posture == "sit" else "stand"])


def shot_camera(kind: str, shot: dict, root: tuple[float, float], heading: float, posture: str,
                aim_point: tuple[float, float, float] | None, rnd: random.Random,
                avoid: list[tuple] | None = None) -> dict | None:
    """Камера кадра: ``{kind, pos_a, pos_b, point, face_w, offset, lens, fstop, focus}``. ``point`` — во что целиться
    помимо лица, ``face_w`` — доля лица в цели (сцена ведёт её за настоящей головой). Ракурс не встаёт (камера
    в стене) — ``None``; ``avoid`` — положения прошлых кадров того же плана (повтор того же ракурса не берём)."""
    avoid = avoid or []

    def fresh(p) -> bool:
        return cam_inside(p) and all(math.dist(p, q) > 0.22 for q in avoid)
    fx, fy = facing(heading)
    fwd = (fx, fy)
    face = face_point(root, heading, posture)
    subject = shot.get("subject") or "face"
    side = 1.0 if rnd.random() < 0.5 else -1.0

    def out(kind_, pos, point, face_w, lens, fstop=2.4, focus="face", push=0.93, offset=(0.0, 0.0, 0.0)):
        pos = tuple(round(c, 4) for c in pos)
        if focus == "face":
            pivot = face
        else:
            pivot = point
        pos_b = tuple(round(pivot[i] + (pos[i] - pivot[i]) * push, 4) for i in range(3))
        return {"kind": kind_, "pos_a": pos, "pos_b": pos_b, "point": tuple(round(c, 4) for c in point),
                "face_w": face_w, "lens": lens, "fstop": fstop, "focus": focus, "offset": offset}

    def around(dist: float, angles: tuple[float, ...], dz: float, center=face) -> tuple | None:
        for a in angles:
            d = _rot(fwd, math.radians(a * side))
            for k in (1.0, 0.85, 0.72):
                p = (center[0] + d[0] * dist * k, center[1] + d[1] * dist * k, center[2] + dz)
                if fresh(p):
                    return p
        return None

    if kind == "detail" and subject == "press":
        pos = (LEFT + 0.17, FRONT + 0.12, 0.84)
        pt = tuple((button_pos("1")[i] * 0.55 + face[i] * 0.45) for i in range(3))
        return out("detail", pos, pt, 0.35, 19.0, fstop=3.2, focus="point", push=0.95)
    if kind == "detail" and subject == "phone" and shot.get("screen"):
        # экран со значением (часы, заряд): телефон в руке экраном к камере и лицо над ним — близко, длинный фокус
        phone_c = to_world(root, heading, (PHONE_SHOW[0], PHONE_SHOW[1], PHONE_SHOW[2] + 0.07 - (SIT_DROP if posture == "sit" else 0.0)))
        # телефон — в середине кадра (выше субтитров), лицо — сверху
        center = tuple(phone_c[i] * 0.8 + face[i] * 0.2 for i in range(3))
        p = around(0.62, (-20, 20, -32, 32, 0, -45, 45), 0.0, center)
        if p:
            # кадр ≈ 0.62 м по высоте (телефон и лицо целиком) при любом отступе камеры от стен
            lens = max(20.0, min(40.0, 58.0 * math.dist(p, center)))
            return out("detail", p, center, 0.0, round(lens, 1), fstop=4.0, focus="point", push=0.95)
    if kind == "detail" and subject == "phone":
        hand_l = to_world(root, heading, (-0.04, 0.22, 0.52 - (SIT_DROP if posture == "sit" else 0.0)))
        center = tuple((hand_l[i] + face[i]) / 2 for i in range(3))
        p = around(0.72, (-38, 38, -55, 55, 0), -0.08, center)
        if p:
            return out("detail", p, hand_l, 0.5, 26.0, fstop=2.4, focus="point")
    ox, oy = shot.get("origin") or (0.0, 0.0)
    key = tuple(shot.get("key_target") or DISPLAY)
    if kind == "detail" and subject == "display":
        # главная вещь места (табло, надпись, номер этажа) из-за плеча героя; не встаёт — сама вещь крупно
        p = (face[0] - fx * 0.70 + fy * 0.22 * side, face[1] - fy * 0.70 - fx * 0.22 * side, face[2] + 0.08)
        if cam_inside(p):
            return out("detail", p, key, 0.0, 62.0 if key == DISPLAY else 40.0, fstop=4.0, focus="point", push=0.95)
        if key == DISPLAY:
            return out("detail", (0.30, FRONT + 0.45, 1.20), DISPLAY, 0.0, 30.0, fstop=4.0, focus="point")
        return None
    if kind == "detail" and subject == "doors":
        # сбоку у дверей, почти анфас: руки на створках и лицо (герой стоит к дверям вплотную)
        # сбоку сверху у дверей: руки на створках, щель и лицо в профиль (вплотную к дверям анфаса нет)
        sx = 1.0   # с +X: туда герой отворачивает лицо, когда тужится; у стука правая рука — к −X
        pos = (ox + sx * 0.58, oy + FRONT + 0.13, 0.98)
        pt = (ox, oy + DOOR_FACE_Y + 0.10, 0.62)
        if not cam_inside(pos):
            return None
        return out("detail", pos, pt, 0.75, 17.0, fstop=3.2, focus="face", push=0.96)
    if kind == "detail":
        return None
    if kind == "ots":
        # за плечом: сзади-сбоку на уровне головы — в кадре плечо и профиль героя И то, на что он смотрит; герой
        # не в кадре (пустой коридор/стена/капюшон на полкадра) — ракурс не годится, план выберут другой
        tgt = aim_point or (root[0] + fx * 1.2, root[1] + fy * 1.2, 0.85)
        for sh, back, dz in ((0.36 * side, 1.00, 0.22), (-0.36 * side, 1.00, 0.22), (0.30 * side, 0.86, 0.16),
                             (-0.30 * side, 0.86, 0.16), (0.24 * side, 1.15, 0.30), (-0.24 * side, 1.15, 0.30),
                             (0.42 * side, 0.80, 0.12), (-0.42 * side, 0.80, 0.12)):
            # вправо от взгляда: (fy, −fx); sh>0 — над правым плечом
            p = (face[0] - fx * back + fy * sh, face[1] - fy * back - fx * sh, face[2] + dz)
            if not fresh(p):
                continue
            c_ = out("ots", p, tgt, 0.30, 20.0, fstop=5.0, focus="face", push=0.94)
            if ots_ok(c_, face, tgt):
                return c_
        return None
    if kind == "overhead":
        x0, x1, y0, y1, _z0, z1 = cam_bounds()
        best, best_s = None, -1e9
        for cx in (x0 + 0.02, x1 - 0.02):
            for cy in (y0 + 0.02, y1 - 0.02):
                p = (cx, cy, min(1.86, face[2] + 0.95))
                if not fresh(p):
                    continue
                dx, dy = p[0] - face[0], p[1] - face[1]
                dist = math.hypot(dx, dy)
                score = (dx * fx + dy * fy) / max(dist, 1e-6) + dist * 0.6
                if score > best_s:
                    best, best_s = p, score
        if best is None:
            return None
        # из угла, но не дальше ~1.2 м по полу: в темноте мелкий герой не читается
        dx, dy = best[0] - face[0], best[1] - face[1]
        k = min(1.0, 0.95 / max(math.hypot(dx, dy), 1e-6))
        best = (face[0] + dx * k, face[1] + dy * k, best[2])
        pt = (face[0], face[1], face[2] - 0.22)
        return out("overhead", best, pt, 0.5, 17.0, fstop=4.0, focus="face", push=0.94)
    # крупно лицо: спереди под небольшим углом, чуть ниже глаз
    if shot.get("n") == 0:
        # первый план под титром названия (верх кадра): лицо мельче и ниже, под титром, над субтитрами
        p = around(1.25, (22, -22, 40, -40, 0), 0.10)
        if p is not None:
            return out("face", p, face, 1.0, 28.0, fstop=2.4, focus="face", push=0.93, offset=(0.0, 0.0, 0.0))
    p = around(0.95, (22, -22, 40, -40, 0, 60, -60), -0.03)
    if p is None:
        return None
    return out("face", p, face, 1.0, 30.0, fstop=2.2, focus="face", push=0.9, offset=(0.0, 0.0, -0.06))


# Ракурсы на проём: герой стоит у левого края, камера с другой стороны, луч уходит от неё в шахту (в объектив не бьёт).
DOORWAY_CAMS = {
    # за плечом: герой снизу слева, в проёме плита, дверь этажа и стена
    "ots": {"pos": (0.30, FRONT + 0.92, 1.38), "point": (-0.15, FRONT - 0.10, 0.96), "face_w": 0.0, "lens": 21.0,
            "fstop": 5.6, "focus": "point", "push": 0.95},
    # сверху: через голову героя вниз по стене, видно, что между кабиной и стеной стоять негде
    "overhead": {"pos": (0.40, FRONT + 1.16, 1.92), "point": (0.11, FRONT - 0.26, 0.56), "face_w": 0.0, "lens": 19.0,
                 "fstop": 5.6, "focus": "point", "push": 0.96},
    # лицо из шахты, с того места, где человеку негде встать: герой смотрит в объектив, луч уходит мимо него к стене
    "face": {"pos": (0.16, FRONT - 0.24, 0.82), "point": None, "face_w": 1.0, "lens": 20.0, "fstop": 2.8, "focus": "face",
             "push": 0.97},
}
DOORWAY_CAMS["detail"] = DOORWAY_CAMS["ots"]
# Стук: чуть сверху от самых дверей, с той стороны, куда он повёрнут: лицо вполоборота и кулак на створке.
KNOCK_CAM = {"pos": (0.62, FRONT + 0.09, 1.05), "point": (-0.03, FRONT + 0.14, 0.70), "face_w": 0.0, "lens": 18.0, "fstop": 5.6,
             "focus": "point", "push": 0.96}


def doorway_camera(kind: str, face: tuple, spec: dict | None = None) -> dict:
    """Камера плана с проёмом в кадре (``frame: doorway``). Точка цели своя: лицо героя тут не главное."""
    c = spec or DOORWAY_CAMS.get(kind) or DOORWAY_CAMS["ots"]
    pos = c["pos"]
    point = c["point"] or face
    pivot = face if c["focus"] == "face" else point
    pos_b = tuple(round(pivot[i] + (pos[i] - pivot[i]) * c["push"], 4) for i in range(3))
    return {"kind": "ots" if kind == "detail" else kind, "pos_a": pos, "pos_b": pos_b, "point": tuple(round(x, 4) for x in point),
            "face_w": c["face_w"], "lens": c["lens"], "fstop": c["fstop"], "focus": c["focus"], "offset": (0.0, 0.0, 0.0)}


def cam_sees(cam: dict, face: tuple, point: tuple, margin: float = 0.9) -> bool:
    """Попадает ли точка в вертикальный кадр 9:16 камеры плана (без Blender: как сцена целит — между вещью и лицом)."""
    pos = cam["pos_a"]
    face_aim = tuple(face[i] + cam["offset"][i] for i in range(3))
    look = tuple(cam["point"][i] + (face_aim[i] - cam["point"][i]) * cam["face_w"] for i in range(3))
    fwd = [look[i] - pos[i] for i in range(3)]
    n_ = math.sqrt(sum(c * c for c in fwd)) or 1.0
    fwd = [c / n_ for c in fwd]
    right = [fwd[1], -fwd[0], 0.0]
    rn = math.hypot(right[0], right[1]) or 1.0
    right = [right[0] / rn, right[1] / rn, 0.0]
    up = [right[1] * fwd[2] - right[2] * fwd[1], right[2] * fwd[0] - right[0] * fwd[2],
          right[0] * fwd[1] - right[1] * fwd[0]]
    v = [point[i] - pos[i] for i in range(3)]
    z = sum(v[i] * fwd[i] for i in range(3))
    if z <= 0.05:
        return False
    tan_v = 18.0 / cam["lens"]                 # датчик 36 по длинной (вертикальной) стороне
    tan_h = tan_v * 1080 / 1920
    x = sum(v[i] * right[i] for i in range(3)) / z
    y = sum(v[i] * up[i] for i in range(3)) / z
    return abs(x) <= tan_h * margin and abs(y) <= tan_v * margin


def ots_ok(cam: dict, face: tuple, target: tuple) -> bool:
    """«Через плечо» годится, только если в кадре и голова героя (плечо+профиль), и то, на что он смотрит, а голова
    не во весь кадр (камера не ближе 0.45 м)."""
    if math.dist(cam["pos_a"], face) < 0.7:
        return False
    return cam_sees(cam, face, face, 0.6) and cam_sees(cam, face, tuple(target), 0.9)


def camera_plan(perf: dict, seed: int = 7) -> list[dict]:
    """Камеры всех кадров (без повтора одинакового ракурса подряд — другой бок)."""
    rnd = random.Random(seed + 11)
    fps = perf["fps"]
    out = []
    for s in perf["shots"]:
        lo = loc_of(s.get("location"))
        b_ = lo["bounds"]
        ox_, oy_ = lo["origin"]
        _CAM["bounds"] = (b_[0] + ox_, b_[1] + ox_, b_[2] + oy_, b_[3] + oy_, b_[4], b_[5])
        tm = min(s["t1"] - 0.05, max(s.get("a0", s["t0"]) + 0.3, (s["t0"] + s["t1"]) / 2))
        f = min(perf["frames"] - 1, int(tm * fps))
        posture = s.get("posture_end", "stand")
        g = perf["gaze"][f]
        aim = g if isinstance(g, (list, tuple)) else None
        prev = out[-1]["kind"] if out else ""
        root, head = tuple(perf["root"][f]), perf["heading"][f]
        face = face_point(root, head, posture)
        authored = s.get("by") == "author" and s.get("planned")
        if authored and (s.get("at_door") or (s["action"] == "knock" and s["camera"] == "overhead")):
            cam = doorway_camera(s["camera"], face, None if s.get("at_door") else KNOCK_CAM)
            s["camera"] = cam["kind"]
            cam["face_y"] = face_target(s["n"], len(perf["shots"]), cam["kind"])
            out.append({"n": s["n"], "t0": s["t0"], "t1": s["t1"], "location": "cabin", **cam})
            continue
        # что фраза называет (лампа, надпись, двери…) — план, где это в кадре; за плечом смотрим на эту вещь
        items = [] if authored else [tuple(x["point"]) for x in s.get("show") or [] if x.get("point")]
        cam, best = None, -1
        rnd_n = random.Random(seed * 1000 + s["n"])   # свой случай у плана: пробы ракурсов не сдвигают другие планы
        kinds = list(dict.fromkeys((s["camera"], *CAMERA_PREFS.get(s["action"], ()), "face", "overhead", "ots", "detail")))
        # тот же план подряд — только последней попыткой и только если иначе названное не видно (с другого места)
        order = [(k, False) for k in kinds if k != prev or authored] + ([(prev, True)] if items and prev else [])
        if s.get("screen") or s.get("hook") == 1:
            # экран телефона — деталью, крючок — лицом: этот план первым, даже если прошлый такой же
            must = "detail" if s.get("screen") else "face"
            order = [(must, False)] + [o for o in order if o[0] != must]
        for kind, again in order:
            if again and cam is not None and best == len(items):
                break
            # у автора план один и тот же ракурс лучший (лицо к камере): другой угол ради разнообразия даёт макушку
            avoid = [] if authored else [c["pos_a"] for c in out if c["kind"] == kind][-2:]
            if again:
                avoid = avoid + [out[-1]["pos_a"]]
            for aim_ in ((items[0], aim) if items and kind == "ots" else (aim,)):
                c_ = shot_camera(kind, s, root, head, posture, aim_, random.Random(rnd_n.random()), avoid)
                if c_ is None and not again:
                    c_ = shot_camera(kind, s, root, head, posture, aim_, random.Random(rnd_n.random()))
                if c_ is None:
                    continue
                seen = sum(cam_sees(c_, face, p) for p in items)
                if kind == "ots" and seen < len(items):
                    continue      # за плечом без названной вещи — пустой план
                if seen > best:
                    cam, best = c_, seen
                if seen == len(items):
                    break
            if cam is not None and best == len(items):
                break
        if cam is None:   # совсем тесно — общий сверху без проверки повтора
            cam = shot_camera("overhead", s, root, head, posture, aim, rnd)
        s["camera"] = cam["kind"]
        cam["face_y"] = face_target(s["n"], len(perf["shots"]), cam["kind"])
        out.append({"n": s["n"], "t0": s["t0"], "t1": s["t1"], "location": s.get("location") or "cabin", **cam})
    _CAM["bounds"] = CAM_BOUNDS
    return out


# Где лицо в кадре (доля высоты сверху): субтитры — 0.74, HUD — 0.145/0.585; первый план — под титром названия
# (TITLE_Y), последний — над титром «ЧАСТЬ N+1» (PART_Y): титры и плашки не ложатся на лицо.
TITLE_Y, PART_Y = 0.27, 0.62


def face_target(n: int, count: int, kind: str) -> float:
    if n == 0:
        return 0.52
    if n == count - 1:
        return 0.30
    return 0.40 if kind in ("face", "overhead") else 0.42


def face_bands(perf: dict) -> list[dict]:
    """Лицо в кадре по планам: ``{n, t0, t1, y, h}`` (центр и высота, доли кадра; ``y`` None — лица почти нет: за
    плечом, табло). Высота — лицо с капюшоном ≈ 0.42 м на расстоянии камеры и фокусном (вертикальный кадр, датчик 36)."""
    out = []
    shots = {s["n"]: s for s in perf["shots"]}
    for c in perf["cameras"]:
        s = shots[c["n"]]
        fi = min(perf["frames"] - 1, int((c["t0"] + c["t1"]) / 2 * perf["fps"]))
        face = face_point(tuple(perf["root"][fi]), perf["heading"][fi], s.get("posture_end", "stand"))
        dist = max(0.2, math.dist(c["pos_a"], face))
        h = max(0.05, min(0.75, 0.42 * c["lens"] / (36.0 * dist)))   # лоб–подбородок с капюшоном ≈ 0.42 м
        y = c.get("face_y") if c.get("face_w", 0) > 0.3 else None
        out.append({"n": c["n"], "t0": c["t0"], "t1": c["t1"], "y": y, "h": round(h, 3)})
    return out
