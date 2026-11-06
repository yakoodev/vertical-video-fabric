"""Блокинг: детали сцены — тело, диск, плоскость, предметы (из ``app/aivideo_blocking.py``)."""

from __future__ import annotations

import math

from app import aivideo_layout as L
from app.aivideo_blocking_spec import ACTOR_COLORS

def _mat(rx: float, ry: float, rz: float) -> list[list[float]]:
    """Матрица поворота Эйлера XYZ (как three.js: R = Rx·Ry·Rz), градусы."""
    a, b, c = (math.radians(v) for v in (rx, ry, rz))
    ca, sa, cb, sb, cc, sc = math.cos(a), math.sin(a), math.cos(b), math.sin(b), math.cos(c), math.sin(c)
    mx = [[1, 0, 0], [0, ca, -sa], [0, sa, ca]]
    my = [[cb, 0, sb], [0, 1, 0], [-sb, 0, cb]]
    mz = [[cc, -sc, 0], [sc, cc, 0], [0, 0, 1]]
    return _mm(_mm(mx, my), mz)


def _mm(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _mv(m: list[list[float]], v: list[float]) -> list[float]:
    return [sum(m[i][k] * v[k] for k in range(3)) for i in range(3)]


def _r(v: float) -> float:
    return round(float(v), 2)


# Позы: углы суставов (Эйлер XYZ, градусы). Ноги: бедро и колено; руки: плечо и локоть.
_LEGS = {
    "standing": ((0, 0, 0), (0, 0, 0), (0, 0, 0), (0, 0, 0)),
    "walking": ((22, 0, 0), (12, 0, 0), (-22, 0, 0), (6, 0, 0)),
    "sitting": ((-90, 0, 0), (90, 0, 0), (-90, 0, 0), (90, 0, 0)),
    "crouching": ((-80, 0, 0), (125, 0, 0), (-70, 0, 0), (115, 0, 0)),
    "kneeling": ((0, 0, 0), (90, 0, 0), (0, 0, 0), (90, 0, 0)),
    "lying": ((0, 0, 0), (0, 0, 0), (0, 0, 0), (0, 0, 0)),
}
_ARMS = {  # (левое плечо, левый локоть, правое плечо, правый локоть)
    "down": ((0, 0, -7), (-8, 0, 0), (0, 0, 7), (-8, 0, 0)),
    "up": ((180, 0, -14), (0, 0, 0), (180, 0, 14), (0, 0, 0)),
    "forward": ((-78, 0, -4), (-15, 0, 0), (-78, 0, 4), (-15, 0, 0)),
    "holding": ((0, 0, -7), (-8, 0, 0), (-28, 0, 6), (-58, 0, 0)),
    "wave": ((0, 0, -7), (-8, 0, 0), (165, 0, 22), (-35, 0, 0)),
}


def _human_rig(height: float, pose: str, arms: str) -> dict:
    """Скелет человека: размеры частей и углы суставов в позе (для узлов и для руки с предметом)."""
    s = height / 165.0
    hip_y = 87 * s
    legs = _LEGS.get(pose, _LEGS["standing"])
    arm = _ARMS.get(arms, _ARMS["down"])
    if pose == "walking" and arms == "down":
        arm = ((-16, 0, -6), (-10, 0, 0), (16, 0, 6), (-6, 0, 0))
    if pose == "sitting":
        root = [0.0, 0.0, 0.0]
        hip_h = 46 * s + 7 * s  # таз на сиденье, бедро — над ним
        root[1] = hip_h - hip_y
        root[2] = -12 * s  # таз сзади, колени вперёд — центр по стопам
    elif pose == "crouching":
        root = [0.0, 50 * s - hip_y, -8 * s]
    elif pose == "kneeling":
        root = [0.0, 45 * s - hip_y, 0.0]
    elif pose == "lying":
        root = [0.0, 14 * s, 82 * s]
    else:
        root = [0.0, 0.0, 0.0]
    return {"s": s, "hip_y": hip_y, "shoulder_y": 136 * s, "shoulder_x": 19 * s, "hip_x": 9 * s,
            "upper": 28 * s, "fore": 25 * s, "thigh": 39 * s, "shin": 36 * s, "root": root,
            "body": [-90, 0, 0] if pose == "lying" else [0, 0, 0], "legs": legs, "arms": arm}


def _human_nodes(rig: dict, color: str) -> list[dict]:
    s = rig["s"]
    head = L._shade(color, 1.22)
    dark = "#2b2b33"
    hip_y, sh_y = rig["hip_y"], rig["shoulder_y"]
    torso_lo, torso_hi = hip_y + 5 * s, sh_y + 4 * s
    parts: list[dict] = [
        {"type": "box", "size": [_r(30 * s), _r(16 * s), _r(18 * s)], "position": [0, _r(hip_y + 2 * s), 0],
         "color": color},
        {"type": "cylinder", "radiusTop": _r(17 * s), "radiusBottom": _r(14 * s), "height": _r(torso_hi - torso_lo),
         "position": [0, _r((torso_lo + torso_hi) / 2), 0], "color": color, "segments": 16},
        {"type": "cylinder", "radius": _r(5 * s), "height": _r(10 * s), "position": [0, _r(sh_y + 7 * s), 0],
         "color": head, "segments": 10},
        {"type": "group", "position": [0, _r(154 * s), 0], "children": [
            {"type": "sphere", "radius": _r(11 * s), "position": [0, 0, 0], "color": head, "segments": 18},
            {"type": "box", "size": [_r(3 * s), _r(3.5 * s), _r(5 * s)], "position": [0, _r(-1 * s), _r(11 * s)],
             "color": dark},
            {"type": "box", "size": [_r(8 * s), _r(1 * s), _r(1.2 * s)], "position": [0, _r(3 * s), _r(10.4 * s)],
             "color": dark}]},  # линия глаз — видно, куда смотрит лицо
    ]
    (l_sh, l_el, r_sh, r_el) = rig["arms"]
    for side, sh, el in ((-1, l_sh, l_el), (1, r_sh, r_el)):
        parts.append({"type": "group", "position": [_r(side * rig["shoulder_x"]), _r(sh_y), 0], "rotation": list(sh),
                      "children": [
                          {"type": "sphere", "radius": _r(5 * s), "position": [0, 0, 0], "color": color,
                           "segments": 10},
                          {"type": "cylinder", "radius": _r(4.3 * s), "height": _r(rig["upper"]),
                           "position": [0, _r(-rig["upper"] / 2), 0], "color": color, "segments": 10},
                          {"type": "group", "position": [0, _r(-rig["upper"]), 0], "rotation": list(el), "children": [
                              {"type": "cylinder", "radius": _r(3.8 * s), "height": _r(rig["fore"]),
                               "position": [0, _r(-rig["fore"] / 2), 0], "color": color, "segments": 10},
                              {"type": "sphere", "radius": _r(4.6 * s), "position": [0, _r(-rig["fore"] - 2 * s), 0],
                               "color": head, "segments": 10}]}]})
    (l_hip, l_knee, r_hip, r_knee) = rig["legs"]
    for side, hp, kn in ((-1, l_hip, l_knee), (1, r_hip, r_knee)):
        parts.append({"type": "group", "position": [_r(side * rig["hip_x"]), _r(hip_y), 0], "rotation": list(hp),
                      "children": [
                          {"type": "cylinder", "radiusTop": _r(7.5 * s), "radiusBottom": _r(6 * s),
                           "height": _r(rig["thigh"]), "position": [0, _r(-rig["thigh"] / 2), 0], "color": color,
                           "segments": 12},
                          {"type": "group", "position": [0, _r(-rig["thigh"]), 0], "rotation": list(kn), "children": [
                              {"type": "cylinder", "radiusTop": _r(5.8 * s), "radiusBottom": _r(4.5 * s),
                               "height": _r(rig["shin"]), "position": [0, _r(-rig["shin"] / 2), 0], "color": color,
                               "segments": 12},
                              {"type": "box", "size": [_r(9 * s), _r(6 * s), _r(23 * s)],
                               "position": [0, _r(-rig["shin"] - 3 * s), _r(6 * s)], "color": L._shade(color, 0.7)}]}]})
    return [{"type": "group", "position": [_r(v) for v in rig["root"]], "rotation": list(rig["body"]),
             "children": parts}]


def _hand_local(rig: dict, side: int = 1) -> list[float]:
    """Где рука (правая side=1) в осях манекена — туда ставится предмет в руке."""
    sh, el = (rig["arms"][2], rig["arms"][3]) if side > 0 else (rig["arms"][0], rig["arms"][1])
    body = _mat(*rig["body"])
    m_sh = _mm(body, _mat(*sh))
    m_el = _mm(m_sh, _mat(*el))
    p = _mv(body, [side * rig["shoulder_x"], rig["shoulder_y"], 0.0])
    p = [p[i] + v for i, v in enumerate(_mv(m_sh, [0.0, -rig["upper"], 0.0]))]
    p = [p[i] + v for i, v in enumerate(_mv(m_el, [0.0, -rig["fore"] - 2 * rig["s"], 0.0]))]
    return [p[i] + rig["root"][i] for i in range(3)]


def _disc_nodes(d: float, color: str) -> tuple[list[dict], float]:
    h = max(8.0, d * 0.27)
    r = d / 2
    return [
        {"type": "cylinder", "radius": _r(r), "height": _r(h * 0.42), "position": [0, _r(h * 0.23), 0],
         "color": L._shade(color, 0.62), "segments": 32},
        {"type": "cylinder", "radius": _r(r - 0.6), "height": _r(h * 0.78), "position": [0, _r(h * 0.55), 0],
         "color": color, "segments": 32},
        {"type": "cylinder", "radius": _r(r * 0.8), "height": 0.8, "position": [0, _r(h * 0.95), 0],
         "color": L._shade(color, 1.15), "segments": 32},
        {"type": "box", "size": [_r(d * 0.48), _r(h * 0.46), 1.4], "position": [0, _r(h * 0.55), _r(r - 0.4)],
         "color": "#1d2733"},
        {"type": "sphere", "radius": _r(d * 0.05), "position": [_r(-d * 0.1), _r(h * 0.56), _r(r + 0.4)],
         "color": "#38bdf8", "segments": 10},
        {"type": "sphere", "radius": _r(d * 0.05), "position": [_r(d * 0.1), _r(h * 0.56), _r(r + 0.4)],
         "color": "#38bdf8", "segments": 10},
        {"type": "cylinder", "radius": 0.5, "height": 7, "position": [0, _r(h + 3.5), _r(-r * 0.35)],
         "color": "#55585e", "segments": 6},
        {"type": "sphere", "radius": 1.2, "position": [0, _r(h + 7.5), _r(-r * 0.35)], "color": "#e5484d",
         "segments": 8},
    ], h


def _quad_nodes(length: float, color: str, pose: str) -> tuple[list[dict], float]:
    s = length / 46.0
    leg = 14 * s
    body_y = leg + 8 * s
    dark = L._shade(color, 0.75)
    if pose == "lying":
        leg, body_y = 0.0, 8 * s
    parts = [
        {"type": "box", "size": [_r(18 * s), _r(16 * s), _r(36 * s)], "position": [0, _r(body_y), 0], "color": color},
        {"type": "sphere", "radius": _r(8.5 * s), "position": [0, _r(body_y + 10 * s), _r(20 * s)], "color": color,
         "segments": 14},
        {"type": "cone", "radius": _r(2.8 * s), "height": _r(6 * s), "position": [_r(-4.5 * s), _r(body_y + 19 * s),
                                                                                  _r(20 * s)], "color": dark,
         "segments": 4},
        {"type": "cone", "radius": _r(2.8 * s), "height": _r(6 * s), "position": [_r(4.5 * s), _r(body_y + 19 * s),
                                                                                 _r(20 * s)], "color": dark,
         "segments": 4},
        {"type": "box", "size": [_r(3 * s), _r(3 * s), _r(4 * s)], "position": [0, _r(body_y + 9 * s), _r(28.5 * s)],
         "color": "#2b2b33"},
        {"type": "cylinder", "radius": _r(1.8 * s), "height": _r(22 * s), "position": [0, _r(body_y + 8 * s),
                                                                                        _r(-24 * s)],
         "rotation": [-50, 0, 0], "color": dark, "segments": 8},
    ]
    if leg:
        parts += [{"type": "cylinder", "radius": _r(2.6 * s), "height": _r(leg + 2 * s),
                   "position": [_r(sx * 6 * s), _r((leg + 2 * s) / 2), _r(sz * 13 * s)], "color": dark, "segments": 8}
                  for sx in (-1, 1) for sz in (-1, 1)]
    return parts, body_y + 19 * s


def _support_top(objects: dict[str, dict], oid: str) -> float:
    o = objects.get(oid)
    return L.top_of(o) if o else 0.0


def _actor_parts(actor: dict, info: dict) -> tuple[list[dict], dict]:
    """Узлы манекена (в его осях) и его коробка для самоконтроля (в осях манекена)."""
    kind, color = info.get("stand_in", "human"), info.get("color", ACTOR_COLORS[0])
    size = float(info.get("size") or 165.0)
    pose = actor.get("pose") or "standing"
    if kind == "disc":
        nodes, h = _disc_nodes(size, color)
        return nodes, {"half": [size / 2, h / 2 + 2, size / 2], "cy": h / 2 + 2, "height": h + 8}
    if kind == "quadruped":
        nodes, h = _quad_nodes(size, color, pose)
        return nodes, {"half": [10.0 * size / 46, h / 2, size / 2 + 4], "cy": h / 2, "height": h}
    if kind == "small":
        r = size / 2
        return [{"type": "sphere", "radius": _r(r), "position": [0, _r(r), 0], "color": color, "segments": 16},
                {"type": "box", "size": [3, 3, 4], "position": [0, _r(r * 1.15), _r(r * 0.95)], "color": "#2b2b33"}], \
            {"half": [r, r, r], "cy": r, "height": 2 * r}
    rig = _human_rig(size, pose, actor.get("arms") or "down")
    nodes = _human_nodes(rig, color)
    if pose == "lying":
        box = {"half": [24 * rig["s"], 14 * rig["s"], size / 2], "cy": 14 * rig["s"], "height": 28 * rig["s"]}
    elif pose in ("sitting", "crouching", "kneeling"):
        top = rig["root"][1] + 165 * rig["s"]
        box = {"half": [22 * rig["s"], top / 2, 26 * rig["s"]], "cy": top / 2, "height": top}
    else:
        reach = 30 * rig["s"] if actor.get("arms") in ("up", "wave") else 0.0
        front = 45 * rig["s"] if actor.get("arms") in ("forward", "holding") else 15 * rig["s"]  # рука с предметом
        box = {"half": [22 * rig["s"], (size + reach) / 2, front], "cy": (size + reach) / 2,
               "height": size}
    box["rig"] = rig
    head = _mv(_mat(*rig["body"]), [0.0, 154 * rig["s"], 0.0])
    box["head"] = [head[i] + rig["root"][i] for i in range(3)]  # центр головы в осях манекена
    box["head_r"] = 11 * rig["s"]
    return nodes, box


def _prop_parts(kind: str, size: list[float], color: str) -> list[dict]:
    """Предмет реквизита в его осях: низ на y=0, по центру."""
    w, h, d = size
    if kind == "mug":
        r = w / 2
        return [{"type": "cylinder", "radius": _r(r), "height": _r(h), "position": [0, _r(h / 2), 0], "color": color,
                 "segments": 18},
                {"type": "cylinder", "radius": _r(r * 0.85), "height": 0.4, "position": [0, _r(h - 0.6), 0],
                 "color": "#5a3a24", "segments": 18},
                {"type": "torus", "radius": _r(h * 0.28), "tube": 0.7, "position": [_r(r + 1.2), _r(h * 0.55), 0],
                 "color": color, "segments": 16, "tubeSegments": 8}]
    if kind in ("plate", "cookie"):
        return [{"type": "cylinder", "radius": _r(w / 2), "height": _r(h), "position": [0, _r(h / 2), 0],
                 "color": color, "segments": 20}]
    if kind in ("bottle", "glass"):
        parts = [{"type": "cylinder", "radius": _r(w / 2), "height": _r(h * 0.75 if kind == "bottle" else h),
                  "position": [0, _r((h * 0.75 if kind == "bottle" else h) / 2), 0], "color": color, "segments": 14}]
        if kind == "bottle":
            parts.append({"type": "cylinder", "radius": _r(w / 5), "height": _r(h * 0.25),
                          "position": [0, _r(h * 0.875), 0], "color": color, "segments": 10})
        return parts
    if kind in ("ball", "toy"):
        return [{"type": "sphere", "radius": _r(w / 2), "position": [0, _r(w / 2), 0], "color": color, "segments": 14}]
    if kind == "flower":
        return [{"type": "cylinder", "radius": 0.6, "height": _r(h), "position": [0, _r(h / 2), 0], "color": "#4f8a4a",
                 "segments": 6},
                {"type": "sphere", "radius": _r(w / 2), "position": [0, _r(h), 0], "color": color, "segments": 10}]
    return [{"type": "box", "size": [_r(w), _r(h), _r(d)], "position": [0, _r(h / 2), 0], "color": color}]
