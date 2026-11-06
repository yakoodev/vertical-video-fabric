"""Блокинг: камера — постановка, проверка кадра, выбор плана (из ``app/aivideo_blocking.py``)."""

from __future__ import annotations

import math
import re
from typing import Any

from app.aivideo_blocking_spec import _norm, _rot_y
from app.aivideo_blocking_parts import _r

# Крупность → сколько видно по высоте кадра (в ростах героя) и что от каждого персонажа обязано влезть.
FRAME_HEIGHT = {"extreme_wide": 3.6, "wide": 2.3, "full": 1.3, "medium": 0.8, "medium_close": 0.55,
                "close": 0.36, "extreme_close": 0.2}
MUST_SEE = {"extreme_close": 0.16, "close": 0.24, "medium_close": 0.4, "medium": 0.6}  # верхняя доля роста
# Где низ кадра режет главного персонажа (доля роста от пола): medium — бёдра/пояс, medium_close — грудь,
# close — плечи и голова, extreme_close — лицо. Над макушкой — запас не меньше HEAD_ROOM высоты кадра.
CUT = {"medium": 0.45, "medium_close": 0.6, "close": 0.74, "extreme_close": 0.84}
HEAD_ROOM = 0.05
# Кадр блокинга не теснее этого (доля роста главного по высоте кадра): иначе манекен на весь кадр,
# и модель не понимает ни места, ни действия.
MIN_FRAME = {"medium": 1.0, "medium_close": 0.9, "close": 0.85, "extreme_close": 0.8}
CROWD_MAX = 0.6  # персонажи заняли больше — комнаты не видно
SIZES = ("extreme_close", "close", "medium_close", "medium", "full", "wide", "extreme_wide")
MIN_SHARE = {"extreme_wide": 0.003, "wide": 0.008, "full": 0.02, "medium": 0.035, "medium_close": 0.05,
             "close": 0.07, "extreme_close": 0.07}
OCCLUDE_MAX = 0.10  # вещь перед персонажами (край стола у камеры) закрывает больше — кадр не годится
HIDDEN_MAX = 0.35  # персонаж закрыт больше чем на столько — не годится (живой план 13: стол закрывал 40%)
FOCUS_SEEN_MIN = 0.4  # вещь действия (тумбочка, на которую ставят кружку) видна хотя бы на столько


GRID = (27, 48)  # лучей по ширине и высоте кадра в самоконтроле


# --------------------------------------------------------------------------- #
# Камера по плану и самоконтроль кадра
# --------------------------------------------------------------------------- #


def lens_fov(lens: Any) -> float:
    """Вертикальное поле кадра 9:16 по объективу (длинная сторона кадра 36 мм — вертикаль)."""
    try:
        mm = float(lens)
    except (TypeError, ValueError):
        mm = 35.0
    mm = max(14.0, min(135.0, mm or 35.0))
    return round(math.degrees(2 * math.atan(18.0 / mm)), 2)


def _basis(pos: list[float], target: list[float]) -> tuple[list[float], list[float], list[float]]:
    f = [target[i] - pos[i] for i in range(3)]
    n = math.sqrt(sum(v * v for v in f)) or 1.0
    f = [v / n for v in f]
    up = [0.0, 1.0, 0.0]
    r = [f[1] * up[2] - f[2] * up[1], f[2] * up[0] - f[0] * up[2], f[0] * up[1] - f[1] * up[0]]
    rn = math.sqrt(sum(v * v for v in r)) or 1.0
    r = [v / rn for v in r]
    u = [r[1] * f[2] - r[2] * f[1], r[2] * f[0] - r[0] * f[2], r[0] * f[1] - r[1] * f[0]]
    return f, r, u


def _box_corners(box: dict) -> list[list[float]]:
    cx, cy, cz = box["center"]
    hx, hy, hz = box["half"]
    out = []
    for sx in (-1, 1):
        for sy in (-1, 1):
            for sz in (-1, 1):
                dx, dz = _rot_y(sx * hx, sz * hz, box.get("rot") or 0.0)
                out.append([cx + dx, cy + sy * hy, cz + dz])
    return out


def _must_see(box: dict, size: str) -> dict:
    """Что от персонажа обязано влезть в кадр при этой крупности (у человека — верх тела/лицо)."""
    if box.get("kind") != "human" or box.get("pose") == "lying":
        return box
    share = MUST_SEE.get(size)
    if not share:
        return box
    top = box["center"][1] + box["half"][1]
    h = box["height"] * share
    return {**box, "center": [box["center"][0], top - h / 2, box["center"][2]],
            "half": [box["half"][0], h / 2, box["half"][2]]}


def _place_camera(actors: list[dict], size: str, shot_cam: dict, room: dict, yaw: float = 0.0,
                  lift: float = 0.0, focus: Any = ()) -> dict:
    """Камера по крупности/ракурсу/объективу: смотрит на центр масс персонажей, все влезают."""
    fov = lens_fov(shot_cam.get("lens"))
    humans = [a for a in actors if a.get("kind") == "human"]
    subj_h = max([a["height"] for a in humans], default=0.0) or max([max(45.0, a["height"] * 1.8) for a in actors],
                                                                     default=150.0)
    frame_h = FRAME_HEIGHT.get(size, FRAME_HEIGHT["medium"]) * subj_h
    seen = [_must_see(a, size) for a in actors]
    main = next((a for a in actors if a.get("main") and a.get("head")), None)
    if main is not None and size in CUT:
        # Блокинг должен показать МЕСТО и ДЕЙСТВИЕ: в кадре все участники плана и вещи действия плюс
        # кусок комнаты; крупность плана — ориентир (сколько роста главного видно, но не меньше
        # MIN_FRAME). Людям можно резать ноги (низ кадра — не выше бёдер), голову — никогда: над
        # макушкой (или поднятыми руками) запас HEAD_ROOM. Маленькие персонажи и вещи — целиком.
        def human_part(a: dict, frame: float) -> dict:
            top = max(a["head"][1] + a["head_r"], a["center"][1] + a["half"][1])
            base = a["center"][1] - a["half"][1]
            cut = base + CUT["medium"] * max(1.0, top - base)
            top += (HEAD_ROOM + 0.02) * frame
            return {**a, "center": [a["center"][0], (top + cut) / 2, a["center"][2]],
                    "half": [min(a["half"][0], 24.0), (top - cut) / 2, min(a["half"][2], 18.0)], "fit_v": 1.0}

        h_main = max(1.0, main["head"][1] + main["head_r"] - (main["center"][1] - main["half"][1]))
        frame_h = max(FRAME_HEIGHT.get(size, 0.8) * h_main, MIN_FRAME[size] * h_main)
        seen = [human_part(a, frame_h) if a.get("kind") == "human" and a.get("head") else a for a in actors]
    if focus:
        seen = seen + [dict(f) for f in focus]
    if main is not None and size in CUT:
        tx = sum(a["center"][0] for a in seen) / len(seen)
        tz = sum(a["center"][2] for a in seen) / len(seen)
        lo = min(a["center"][1] - a["half"][1] for a in seen)
        hi = max(a["center"][1] + a["half"][1] for a in seen)
        ty = (lo + hi) / 2
    elif seen:
        tx = sum(a["center"][0] for a in seen) / len(seen)
        tz = sum(a["center"][2] for a in seen) / len(seen)
        lo = min(a["center"][1] - a["half"][1] for a in seen)
        hi = max(a["center"][1] + a["half"][1] for a in seen)
        ty = (lo + hi) / 2
    else:
        tx, ty, tz = 0.0, 110.0, 0.0
    angle = _norm(shot_cam.get("angle") or "eye")
    if re.search(r"overhead|top|bird|сверху|над", angle):
        pitch = 60.0
    elif re.search(r"high|высок", angle):
        pitch = 28.0
    elif re.search(r"low|низк|снизу", angle):
        pitch = -18.0
    else:
        pitch = None  # на уровне глаз — камера на высоте глаз героя
    eye = (0.93 * max(a["height"] for a in humans)) if humans else (max([a["height"] for a in actors], default=30)
                                                                       + 12.0)
    target = [tx, ty, tz]
    w, d, rh = float(room["width"]), float(room["depth"]), float(room["height"])
    def fit(fov_: float, back_: list[float]) -> float:
        """Минимальная дистанция: кадр нужной крупности и всё обязательное от персонажей влезает."""
        tv_ = math.tan(math.radians(fov_ / 2))
        th_ = tv_ * 9 / 16
        f = [-v for v in back_]
        _f, r, u = _basis([target[i] + back_[i] for i in range(3)], target)
        need = (frame_h / 2) / tv_
        for a in seen:
            for c in _box_corners(a):
                q = [c[i] - target[i] for i in range(3)]
                qa = sum(q[i] * r[i] for i in range(3))
                qb = sum(q[i] * u[i] for i in range(3))
                qf = sum(q[i] * f[i] for i in range(3))
                need = max(need, abs(qa) / (th_ * 0.9) - qf, abs(qb) / (tv_ * a.get("fit_v", 0.9)) - qf)
        return max(25.0, need)

    pos = [tx, ty, tz + 300.0]
    dist = 300.0
    back = [0.0, 0.0, 1.0]
    for _ in range(3):
        p = pitch if pitch is not None else math.degrees(math.atan2(eye + lift - ty, max(30.0, dist)))
        if pitch is not None and lift:
            p += math.degrees(math.atan2(lift, max(30.0, dist)))
        cp, sp = math.cos(math.radians(p)), math.sin(math.radians(p))
        bx, bz = _rot_y(0.0, 1.0, yaw)
        back = [bx * cp, sp, bz * cp]  # от цели к камере
        dist = fit(fov, back)
        pos = [target[i] + back[i] * dist for i in range(3)]
    # В комнате: не за боковыми стенами и не выше потолка; спереди стены нет (открытая сторона
    # декорации) — сначала пробуем поместиться внутрь чуть шире объективом, как в настоящей комнате;
    # не выходит — камера за открытой стороной (там ничего нет, кадр не режет персонажей).
    front = d / 2 - 15.0
    if pos[2] > front and back[2] > 0.2:
        for wider in range(int(fov) + 4, int(min(66.0, fov + 14.0)) + 1, 4):
            d2 = fit(float(wider), back)
            if target[2] + back[2] * d2 <= front:
                fov, dist = float(wider), d2
                pos = [target[i] + back[i] * dist for i in range(3)]
                break
    pos[0] = max(-w / 2 + 15.0, min(w / 2 - 15.0, pos[0]))
    pos[1] = max(12.0, min(rh - 10.0, pos[1]))
    pos[2] = max(-d / 2 + 15.0, pos[2])
    return {"pos": [_r(v) for v in pos], "target": [_r(v) for v in target], "fov": _r(fov)}


def _inside(box: dict, p: list[float], pad: float = 6.0) -> bool:
    q = [p[i] - box["center"][i] for i in range(3)]
    lx, lz = _rot_y(q[0], q[2], -(box.get("rot") or 0.0))
    hx, hy, hz = box["half"]
    return abs(lx) < hx + pad and abs(q[1]) < hy + pad and abs(lz) < hz + pad


def frame_rays(camera: dict, grid: tuple[int, int] = GRID) -> tuple[list[float], Any]:
    import numpy as np

    pos, target, fov = camera["pos"], camera["target"], float(camera.get("fov") or 40)
    f, r, u = _basis(pos, target)
    tv = math.tan(math.radians(fov / 2))
    th = tv * 9 / 16
    gx, gy = grid
    xs = (2 * (np.arange(gx) + 0.5) / gx - 1) * th
    ys = (1 - 2 * (np.arange(gy) + 0.5) / gy) * tv
    X, Y = np.meshgrid(xs, ys)
    dirs = np.stack([f[i] + X * r[i] + Y * u[i] for i in range(3)], axis=-1).reshape(-1, 3)
    dirs /= np.linalg.norm(dirs, axis=1, keepdims=True)
    return list(pos), dirs


def _hit_box(origin: Any, dirs: Any, box: dict) -> Any:
    import numpy as np

    ang = -math.radians(box.get("rot") or 0.0)
    c, s = math.cos(ang), math.sin(ang)

    def local(v: Any) -> Any:
        x, y, z = v[..., 0], v[..., 1], v[..., 2]
        return np.stack([x * c + z * s, y, -x * s + z * c], axis=-1)

    o = local(np.asarray([origin[i] - box["center"][i] for i in range(3)], dtype=float))
    d = local(dirs)
    half = np.asarray(box["half"], dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        t1 = (-half - o) / d
        t2 = (half - o) / d
    t1 = np.nan_to_num(t1, nan=-np.inf)
    t2 = np.nan_to_num(t2, nan=np.inf)
    tmin = np.max(np.minimum(t1, t2), axis=1)
    tmax = np.min(np.maximum(t1, t2), axis=1)
    hit = (tmax >= np.maximum(tmin, 0.0))
    t = np.where(tmin > 0, tmin, 0.0)
    return np.where(hit, t, np.inf)


def _hit_walls(origin: list[float], dirs: Any, room: dict) -> Any:
    import numpy as np

    w, d, h = float(room["width"]), float(room["depth"]), float(room["height"])
    best = np.full(dirs.shape[0], np.inf)
    with np.errstate(divide="ignore", invalid="ignore"):
        for axis, value, lo, hi in ((2, -d / 2, (0, 1), None), (0, -w / 2, (2, 1), None), (0, w / 2, (2, 1), None),
                                    (1, 0.0, (0, 2), None)):
            t = (value - origin[axis]) / dirs[:, axis]
            p = origin + dirs * t[:, None]
            ok = t > 0
            if axis == 2:
                ok &= (np.abs(p[:, 0]) <= w / 2) & (p[:, 1] >= 0) & (p[:, 1] <= h)
            elif axis == 0:
                ok &= (np.abs(p[:, 2]) <= d / 2) & (p[:, 1] >= 0) & (p[:, 1] <= h)
            best = np.where(ok & (t < best), t, best)
    return best


def head_room(camera: dict, actor_box: dict) -> float:
    """Запас кадра над макушкой персонажа (доля высоты кадра); голова вне кадра/срезана — < 0."""
    pos = list(camera["pos"])
    f, r, u = _basis(pos, list(camera["target"]))
    tv = math.tan(math.radians(float(camera.get("fov") or 40) / 2))
    th = tv * 9 / 16
    hx, hy, hz = actor_box["head"]
    rad = float(actor_box.get("head_r") or 11.0)
    out = []
    for p in ([hx, hy + rad, hz], [hx, hy, hz], [hx, hy - rad, hz]):
        q = [p[k] - pos[k] for k in range(3)]
        z = sum(q[k] * f[k] for k in range(3))
        if z <= 1.0:
            return -1.0
        out.append((sum(q[k] * r[k] for k in range(3)) / z / th, sum(q[k] * u[k] for k in range(3)) / z / tv))
    top, centre, chin = out
    if abs(centre[0]) > 0.97 or chin[1] < -1.0:
        return -1.0
    return (1 - top[1]) / 2


def check_frame(camera: dict, boxes: list[dict], room: dict, size: str = "medium",
                grid: tuple[int, int] = GRID) -> dict:
    """Самоконтроль кадра блокинга лучами по сетке кадра: камера не внутри вещи/стены; каждый
    персонаж в кадре и не закрыт; персонажи занимают разумную долю; ничто перед ними не закрывает
    больше ``OCCLUDE_MAX`` кадра."""
    import numpy as np

    problems: list[str] = []
    pos = list(camera["pos"])
    w, d = float(room["width"]), float(room["depth"])
    if abs(pos[0]) > w / 2 - 2 or pos[2] < -d / 2 + 2 or pos[1] < 2:
        problems.append("камера в стене или за ней")
    for b in boxes:
        if _inside(b, pos):
            problems.append(f"камера внутри «{b['label']}»")
    origin, dirs = frame_rays(camera, grid)
    origin = np.asarray(origin, dtype=float)
    n = dirs.shape[0]
    hits = np.stack([_hit_box(origin, dirs, b) for b in boxes] + [_hit_walls(origin, dirs, room)], axis=1) \
        if boxes else _hit_walls(origin, dirs, room)[:, None]
    first = np.argmin(hits, axis=1)
    first_t = hits[np.arange(n), first]
    actor_idx = [i for i, b in enumerate(boxes) if b["role"] == "actor"]
    tgt = camera["target"]
    target_dist = math.sqrt(sum((tgt[i] - pos[i]) ** 2 for i in range(3)))
    if actor_idx:
        near_actor = min(math.sqrt(sum((boxes[i]["center"][k] - pos[k]) ** 2 for k in range(3))) -
                         max(boxes[i]["half"]) for i in actor_idx)
        near_actor = max(10.0, min(target_dist, near_actor))
    else:
        near_actor = target_dist
    shares: dict[str, float] = {}
    total = 0.0
    for i in actor_idx:  # каждый участник плана — в кадре и не закрыт (Бип за кадром — ошибка)
        b = boxes[i]
        any_hit = int(np.isfinite(hits[:, i]).sum())
        own = int(((first == i) & np.isfinite(first_t)).sum())
        shares[b["id"]] = round(own / n, 4)
        total += own / n
        if any_hit == 0:
            problems.append(f"«{b['label']}» не в кадре")
        elif own / any_hit < 1 - HIDDEN_MAX:
            problems.append(f"«{b['label']}» закрыт(а) — видно {round(100 * own / any_hit)}%")
    for i, b in enumerate(boxes):  # вещи действия («ставит кружку на тумбочку») — тоже в кадре и видны
        if not b.get("focus"):
            continue
        seen_rays = int(((first == i) & np.isfinite(first_t)).sum())
        any_rays = int(np.isfinite(hits[:, i]).sum())
        if not seen_rays:
            problems.append(f"«{b['label']}» (вещь действия) не в кадре")
        elif seen_rays / max(1, any_rays) < FOCUS_SEEN_MIN:
            problems.append(f"«{b['label']}» (вещь действия) закрыта — видно {round(100 * seen_rays / any_rays)}%")
    if total > CROWD_MAX:
        problems.append(f"персонажи на весь кадр ({round(100 * total)}%) — не видно ни места, ни действия")
    # Обязательное от персонажа (у человека при этой крупности — верх тела/лицо) влезает в кадр целиком.
    f, r, u = _basis(pos, list(camera["target"]))
    tv = math.tan(math.radians(float(camera.get("fov") or 40) / 2))
    th = tv * 9 / 16

    def ndc(p: list[float]) -> tuple[float, float] | None:
        q = [p[k] - pos[k] for k in range(3)]
        z = sum(q[k] * f[k] for k in range(3))
        if z <= 1.0:
            return None
        return sum(q[k] * r[k] for k in range(3)) / z / th, sum(q[k] * u[k] for k in range(3)) / z / tv

    for i in actor_idx:
        b = boxes[i]
        if b.get("main") and b.get("head"):
            # Голова главного персонажа целиком в кадре, над макушкой — запас ≥ HEAD_ROOM высоты кадра.
            room_top = head_room(camera, b)
            if room_top < HEAD_ROOM - 0.005:
                problems.append(f"голова «{b['label']}» срезана или у верхнего края кадра (запас "
                                f"{max(0, round(100 * room_top, 1))}% < {round(100 * HEAD_ROOM)}%)")
                continue
        if b.get("kind") == "human" and size in CUT:
            continue  # людям на крупном плане можно резать ноги; мерило — голова (выше)
        cut = False
        for c in _box_corners(_must_see(b, size)):
            p = ndc(c)
            if p is None or abs(p[0]) > 1.06 or abs(p[1]) > 1.06:
                cut = True
                break
        if cut and not any(b["label"] in p for p in problems):
            problems.append(f"«{b['label']}» не влезает в кадр")
    if actor_idx and total < MIN_SHARE.get(size, 0.02):
        problems.append(f"персонажи слишком мелкие — {round(100 * total, 1)}% кадра")
    occluders: dict[str, float] = {}
    for i, b in enumerate(boxes):
        if b["role"] == "actor":
            continue
        front = int(((first == i) & (first_t < near_actor)).sum()) / n
        if front > 0.02:
            occluders[b["label"]] = round(front, 3)
        if front > OCCLUDE_MAX:
            problems.append(f"«{b['label']}» перекрывает {round(100 * front)}% кадра")
    for i in actor_idx:  # персонаж почти в камере тоже загораживает остальных
        b = boxes[i]
        front = int(((first == i) & (first_t < 25.0)).sum()) / n
        if front > OCCLUDE_MAX:
            problems.append(f"«{b['label']}» вплотную к камере — {round(100 * front)}% кадра")
    return {"ok": not problems, "problems": problems, "shares": shares, "occluders": occluders,
            "characters_share": round(total, 4)}


def _camera_notes(steps: int, sz: str, size: str, yaw: float, lift: float) -> list[str]:
    notes = []
    if steps:
        notes.append(f"крупность шире плана ({sz} вместо {size}) — иначе персонажи не влезали")
    if yaw or lift:
        notes.append("камера сдвинута/поднята — иначе кадр закрывала мебель")
    return notes


def _wider(size: str, steps: int) -> str:
    k = SIZES.index(size) if size in SIZES else SIZES.index("medium")
    return SIZES[min(len(SIZES) - 1, k + steps)]


def choose_camera(shot: dict, actor_boxes: list[dict], all_boxes: list[dict], room: dict,
                  focus: Any = ()) -> tuple[dict, dict]:
    """Камера плана + самоконтроль. В кадре все участники и вещи действия (``focus``); не прошёл кадр
    по плану — пробуем сместить/поднять камеру и взять шире; ничего не прошло — кадр по плану с
    ``ok: False`` (такой блокинг в кадр не идёт)."""
    cam_cfg = shot.get("camera") or {}
    size = str(cam_cfg.get("shot") or "medium")
    if size not in SIZES:
        size = "medium"
    best: tuple[int, int, dict, dict, str] | None = None
    for steps in (0, 1, 2, 3):
        sz = _wider(size, steps)
        for yaw in (0.0, 18.0, -18.0, 35.0, -35.0, 55.0, -55.0):
            for lift in (0.0, 45.0):
                base = _place_camera(actor_boxes, sz, cam_cfg, room, yaw, lift, focus)
                # Мебель между камерой и действием (стол посреди комнаты) — камера подъезжает ближе
                # по той же линии, за неё, а объектив шире: кадр той же крупности без загородки.
                for dolly in (1.0, 0.7, 0.5, 0.35):
                    cam = _dolly(base, dolly)
                    if cam is None:
                        continue
                    # Камера внутри вещи — отодвигаем вдоль луча к цели, пока не выйдет.
                    for _ in range(30):
                        hit = next((b for b in all_boxes if _inside(b, cam["pos"])), None)
                        if hit is None:
                            break
                        t = cam["target"]
                        vec = [t[i] - cam["pos"][i] for i in range(3)]
                        n = math.sqrt(sum(v * v for v in vec)) or 1.0
                        if n < 40:
                            break
                        cam["pos"] = [_r(cam["pos"][i] + vec[i] / n * 10.0) for i in range(3)]
                    check = check_frame(cam, all_boxes, room, sz)
                    if check["ok"]:
                        notes = _camera_notes(steps, sz, size, yaw, lift)
                        if dolly < 1.0:
                            notes.append("камера ближе и объектив шире — иначе кадр закрывала мебель")
                        return cam, {**check, "size": sz, "notes": notes}
                    # Лучший из провалившихся: меньше проблем, раньше в порядке перебора (ближе к плану).
                    rank = (len(check["problems"]), steps)
                    if best is None or rank < best[:2]:
                        best = (rank[0], rank[1], cam, check, sz)
    _n, _s, cam, check, sz = best  # type: ignore[misc]
    return cam, {**check, "size": sz, "notes": ["ни один вариант камеры не прошёл самоконтроль — взят с наименьшими "
                                                "проблемами"]}


def _dolly(cam: dict, k: float) -> dict | None:
    """Камера на доле k прежней дистанции до цели, угол поля шире настолько, чтобы кадр у цели остался тем же."""
    if k >= 1.0:
        return dict(cam)
    pos, tgt = cam["pos"], cam["target"]
    vec = [pos[i] - tgt[i] for i in range(3)]
    dist = math.sqrt(sum(v * v for v in vec))
    if dist * k < 60.0:
        return None
    fov = math.degrees(2 * math.atan(math.tan(math.radians(float(cam.get("fov") or 40) / 2)) / k))
    if fov > 80.0:
        return None
    return {"pos": [_r(tgt[i] + vec[i] * k) for i in range(3)], "target": list(tgt), "fov": _r(fov)}
