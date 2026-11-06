"""3D-серия: камера плана и проверка кадра (из ``app/series3d.py``)."""

from __future__ import annotations

import contextvars
import math

from app.series3d_scene import _sub, _add, _dot, _norm, project, _yaw_vec, mesh_hero

CAMERA_MARGIN = 0.16  # камера не ближе к стене (м)
FOV = 52.0


HEAD_R = 0.16  # от центра головы до макушки (голова игрушки крупнее настоящей)
# Голова героя в позе клипа: (высота центра, вынос вперёд), м — замер движком (персонаж 1.72 м, игрушка).
CLIP_HEAD = {"Idle_Loop": (1.6, 0.02), "Crouch_Idle_Loop": (0.92, 0.21), "Push_Loop": (1.22, 0.18),
             "Sitting_Idle_Loop": (1.26, -0.15), "Punch_Jab": (1.54, 0.03)}


# --------------------------------------------------------------------------- #
# Камера: ракурсы кадра
# --------------------------------------------------------------------------- #


def _inside(p: list[float], bounds: list[float], margin: float = CAMERA_MARGIN) -> bool:
    x0, x1, z0, z1, h = bounds
    return x0 + margin <= p[0] <= x1 - margin and z0 + margin <= p[2] <= z1 - margin and 0.25 <= p[1] <= h - margin


def camera_ok(p: list[float], loc: dict, free: bool = False, margin: float = CAMERA_MARGIN) -> bool:
    """Камера там, где ей можно стоять: внутри декорации или за срезанной стеной (``camera_bounds``), ниже
    потолка; общий план сверху (``free``) — над декорацией, но в пределах шахты/комнаты по x/z."""
    bounds = loc.get("camera_bounds") or loc["bounds"]
    if free:
        x0, x1, z0, z1, _h = bounds
        return x0 <= p[0] <= x1 and z0 <= p[2] <= z1
    return _inside(p, bounds, margin)


# Сгенерированный герой (HERO_MESH): ниже центр головы, сама голова с причёской крупнее — замер движком (inspect).
CLIP_HEAD_MESH = {"Idle_Loop": (1.455, 0.07), "Crouch_Idle_Loop": (0.89, 0.45), "Push_Loop": (1.1, 0.43),
                  "Sitting_Idle_Loop": (1.25, 0.02), "Punch_Jab": (1.4, 0.05)}
HEAD_R_MESH = 0.26
# Какой герой в кадре сейчас считается (таблица голов и радиус) — ставит direct()/check_shots() по карточке.
_HEAD: contextvars.ContextVar = contextvars.ContextVar("series3d_head", default=(CLIP_HEAD, HEAD_R))


def _head_for(look: dict | None) -> tuple[dict, float]:
    return (CLIP_HEAD_MESH, HEAD_R_MESH) if look and mesh_hero(look) else (CLIP_HEAD, HEAD_R)


def head_of(clip: str | None) -> tuple[float, float]:
    """(высота центра головы, вынос вперёд) героя в позе клипа, м — замер движком (``CLIP_HEAD``)."""
    table = _HEAD.get()[0]
    return table.get(str(clip or ""), table["Idle_Loop"])


def head_r() -> float:
    """От центра головы до макушки (м) у текущего героя."""
    return _HEAD.get()[1]


def hero_points(spot: list[float], clip: str | None = None) -> dict:
    """Опорные точки героя для проверок кадра (по позе клипа): макушка, центр головы, грудь, ступни."""
    hy, ahead = head_of(clip)
    f = _yaw_vec(spot[2] if len(spot) > 2 else 0.0)
    x, z = spot[0] + f[0] * ahead, spot[1] + f[2] * ahead
    return {"top": [x, hy + head_r(), z], "head": [x, hy, z], "chest": [x, max(0.3, hy - 0.45), z],
            "feet": [spot[0], 0.02, spot[1]]}


def frame_report(cam: list[float], target: list[float], fov: float, spot: list[float], clip: str | None = None) -> dict:
    """Где герой в кадре: NDC головы/макушки/груди, высота «грудь → макушка» (доля высоты кадра)."""
    pts = hero_points(spot, clip)
    proj = {k: project(cam, target, fov, p) for k, p in pts.items()}
    size = None
    if proj["top"] and proj["chest"]:
        size = abs(proj["top"][1] - proj["chest"][1]) / 2
    return {"proj": proj, "size": size}


def _seg_dist2d(p: list[float], a: list[float], b: list[float]) -> tuple[float, float]:
    """Расстояние по горизонтали от точки p до отрезка a→b и доля отрезка в ближайшей точке."""
    ax, az, bx, bz = a[0], a[2], b[0], b[2]
    dx, dz = bx - ax, bz - az
    ll = dx * dx + dz * dz
    k = 0.0 if ll < 1e-9 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[2] - az) * dz) / ll))
    return math.hypot(p[0] - (ax + dx * k), p[2] - (az + dz * k)), k


def _blocks(spot: list[float], cam: list[float], focus: list[float], clip: str | None) -> bool:
    """Герой стоит между камерой и вещью (закрывает деталь)."""
    hero = [spot[0], 0.0, spot[1]]
    d, k = _seg_dist2d(hero, cam, focus)
    y = cam[1] + (focus[1] - cam[1]) * k
    return d < 0.32 and 0.0 < k < 0.97 and y < head_of(clip)[0] + head_r() + 0.05


def _camera_for(kind: str, spot: list[float], focus: list[float] | None, loc: dict, side: int, clip: str | None,
                via: list[float] | None = None) -> dict | None:
    """Ракурс кадра → {position, target, fov, end (позиция в конце кадра), free}. None — не встаёт.
    Кандидаты перебираются, пока ракурс не пройдёт ``_check`` (камера не в стене, герой виден и не мелкий,
    деталь не закрыта героем)."""
    hero = [spot[0], 0.0, spot[1]]
    yaw = spot[2]
    fwd = _yaw_vec(yaw)
    head_y, ahead = head_of(clip)
    crouch = head_y < 1.1
    hero = _add(hero, fwd, ahead * 0.6)
    # Крупный герой (сгенерированный: голова с причёской и рюкзак больше) — камера дальше и шире в сторону.
    big = min(1.5, max(1.0, head_r() / HEAD_R))
    cands: list[dict] = []
    if kind == "overhead":
        # Общий план — только сверху: над кабиной (потолок срезан), чуть со стороны лица героя.
        h = loc["bounds"][4]
        x0, x1, z0, z1, _h = loc["bounds"]
        for lift, off in ((2.4, 0.7), (2.1, 0.45), (2.6, 0.2)):
            pos = _add([hero[0], h + lift, hero[2]], fwd, off)
            pos = [min(x1 - 0.12, max(x0 + 0.12, pos[0])), pos[1], min(z1 - 0.12, max(z0 + 0.12, pos[2]))]
            # Цель — чуть за спиной героя: сам герой ниже середины кадра, верх — под название серии.
            tgt = [hero[0] - fwd[0] * 0.65, 0.55, hero[2] - fwd[2] * 0.65]
            end = _add(pos, _norm(_sub(tgt, pos)), 0.5)
            cands.append({"position": pos, "target": tgt, "fov": 50.0, "end": end, "end_target": tgt, "free": True})
    elif kind == "ots":
        # За спиной на уровне плеч: смотрим на точку интереса, герой сбоку кадра (≈ треть ширины).
        look = focus or _add([hero[0], head_y * 0.85, hero[2]], fwd, 1.6)
        d = _sub([look[0], 0, look[2]], [hero[0], 0, hero[2]])
        dirv = _norm(d) if _dot(d, d) > 0.05 else fwd
        right = [-dirv[2], 0.0, dirv[0]]
        for dist in (1.85, 1.65, 1.45, 1.25):
            for s in (side, -side):
                pos = _add(_add([hero[0], head_y + (0.16 if crouch else 0.05), hero[2]], dirv, -dist * (0.5 + big / 2)),
                           right, s * 0.36 * big)
                tgt = [look[0], max(look[1], pos[1] - 0.6), look[2]]
                end = _add(pos, _norm(_sub(tgt, pos)), 0.14)
                cands.append({"position": pos, "target": tgt, "fov": round(FOV * (1 + (big - 1) * 0.4), 1), "end": end, "end_target": tgt, "free": False})
    elif kind == "face":
        # Реакция: лицо героя крупно, спереди или в три четверти (что встаёт внутри декорации).
        head = [hero[0], head_y, hero[2]]
        # Сидит/присел — дальше и чуть сверху (в кадре всё тело), стоит — крупно на уровне глаз.
        dists, lift, drop = ((1.6, 1.45, 1.3), 0.12, 0.12) if crouch else ((1.3, 1.15, 1.0), -0.05, 0.08)
        if big > 1.2:
            # У сгенерированного героя чёлка на глаза и тяжёлая голова — снимаем ниже, лицо из-под волос.
            # Лицо у него — нижняя половина «головы с причёской»: цель ниже центра.
            lift, drop = (-0.22, 0.06) if crouch else (-0.16, 0.1)
        for ang in (22 * side, -22 * side, 45 * side, -45 * side, 70 * side, -70 * side):
            v = _yaw_vec(yaw + ang)
            for dist in dists:
                pos = _add([head[0], head_y + lift, head[2]], v, dist * big)
                tgt = [head[0], head_y - drop, head[2]]
                end = _add(pos, _norm(_sub(tgt, pos)), 0.12)
                cands.append({"position": pos, "target": tgt, "fov": 40.0, "end": end, "end_target": tgt, "free": False})
    elif kind == "detail" and focus is not None:
        name = next((k for k, (p, _n) in loc["targets"].items() if p == focus), None)
        n = _norm(loc["targets"][name][1] if name else [0, 0, -1])
        for ang in (0, 25 * side, -25 * side, 45 * side, -45 * side):
            a = math.radians(ang)
            v = [n[0] * math.cos(a) + n[2] * math.sin(a), n[1], -n[0] * math.sin(a) + n[2] * math.cos(a)]
            for dist in (1.0, 0.85, 0.7):
                pos = _add(focus, v, dist)
                pos = [pos[0], min(max(0.7, pos[1]), loc["bounds"][4] - 0.2), pos[2]]
                # Вещь — в верхней трети кадра: середина и низ — под субтитры и титр части.
                tgt = [focus[0], focus[1] - 0.22 * dist, focus[2]]
                end = _add(pos, _norm(_sub(focus, pos)), 0.18)
                cands.append({"position": pos, "target": tgt, "fov": 42.0, "end": end, "end_target": tgt,
                              "free": False, "focus": focus})
    elif kind == "low":
        # Снизу, из угла перед героем — тревога, герой над камерой.
        for ang in (35 * side, -35 * side, 60 * side, -60 * side, 120 * side, -120 * side):
            v = _yaw_vec(yaw + ang)
            for dist in (1.35, 1.15):
                pos = _add([hero[0], 0.45, hero[2]], v, dist)
                tgt = [hero[0], head_y * 0.92, hero[2]]
                end = _add(pos, [0, 1, 0], 0.1)
                cands.append({"position": pos, "target": tgt, "fov": 56.0, "end": end, "end_target": tgt, "free": False})
    for c in cands:
        c.setdefault("thing", focus)
        if not _check(kind, c, spot, loc, clip, via):
            return c
    return None


def _behind_its_wall(cam: list[float], thing: list[float], loc: dict) -> bool:
    """Камера за той стеной, на которой висит вещь (стена срезается по камере — вещь пропала бы с ней)."""
    x0, x1, z0, z1, _h = loc["bounds"]
    near = 0.2
    return ((cam[2] > z1 and thing[2] > z1 - near) or (cam[2] < z0 and thing[2] < z0 + near)
            or (cam[0] > x1 and thing[0] > x1 - near) or (cam[0] < x0 and thing[0] < x0 + near))


def _check(kind: str, cam: dict, spot: list[float], loc: dict, clip: str | None = None,
           via: list[float] | None = None) -> list[str]:
    """Проблемы ракурса: камера в стене/в герое (и на его пути с прошлого места ``via``), голова вне кадра, герой
    мелкий, деталь закрыта героем."""
    problems = []
    for p in (cam["position"], cam.get("end") or cam["position"]):
        if not camera_ok(p, loc, cam["free"]):
            problems.append("камера в стене")
            break
    # Крупный (сгенерированный) герой шире: и запас больше.
    body = 0.5 * min(1.5, max(1.0, head_r() / HEAD_R))
    near = math.hypot(cam["position"][0] - spot[0], cam["position"][2] - spot[1])
    # Ближе 0.5 м — всегда в герое; до запаса крупного героя — только если камера не выше его макушки.
    if not cam["free"] and (near < 0.5 or (near < body and cam["position"][1] < head_of(clip)[0] + head_r() + 0.1)):
        problems.append("камера в герое")
    elif not cam["free"] and via is not None:
        for p in (cam["position"], cam.get("end") or cam["position"]):
            d, _k = _seg_dist2d(p, [via[0], 0.0, via[1]], [spot[0], 0.0, spot[1]])
            if d < body * 0.8 and p[1] < head_of(clip)[0] + head_r() + 0.1:
                problems.append("камера на пути героя")
                break
    if cam.get("focus") is not None and _blocks(spot, cam["position"], cam["focus"], clip):
        problems.append("герой закрывает деталь")
    if not cam["free"] and cam.get("thing") is not None and _behind_its_wall(cam["position"], cam["thing"], loc):
        problems.append("камера за стеной с нужной вещью — срез спрятал бы её")
    if kind in ("ots", "face", "low", "overhead"):
        rep = frame_report(cam["position"], cam["target"], cam["fov"], spot, clip)
        head = rep["proj"]["head"]
        if head is None or abs(head[0]) > 0.8 or abs(head[1]) > 0.86:
            problems.append("голова героя вне кадра")
        top = rep["proj"]["top"]
        if kind in ("ots", "face", "low") and (top is None or top[1] > 0.92):
            problems.append("макушка срезана")
        if kind in ("ots", "low") and (rep["size"] or 0) < 0.12:
            problems.append("герой мелкий")
        if kind == "face" and (rep["size"] or 0) < 0.22:
            problems.append("лицо мелкое")
    return problems
