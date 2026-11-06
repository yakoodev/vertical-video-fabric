"""Блокинг: сборка сцены из спецификации (из ``app/aivideo_blocking.py``)."""

from __future__ import annotations

import math
import re

from app import aivideo_layout as L
from app.aivideo_blocking_spec import ROOM_DEFAULT, _norm, prop_kind, _rot_y
from app.aivideo_blocking_parts import _r, _hand_local, _actor_parts, _prop_parts
from app.aivideo_blocking_camera import check_frame, choose_camera

# --------------------------------------------------------------------------- #
# spec → JSON-сцена движка
# --------------------------------------------------------------------------- #


def _settle_actor(actor: dict, radius: float, objects: list[dict], room: dict) -> None:
    """Персонаж не стоит внутри мебели: выталкиваем к камере (или вбок), в пределах комнаты."""
    if actor.get("on"):
        return
    w, d = float(room["width"]), float(room["depth"])
    solid = [o for o in objects if L._on_floor(o) or o.get("type") == "door"]
    x, z = actor["pos"]
    x = max(-w / 2 + radius, min(w / 2 - radius, x))
    z = max(-d / 2 + radius, min(d / 2 - radius, z))
    for k in range(60):
        fp = (x - radius, z - radius, x + radius, z + radius)
        if not any(L._overlap(fp, L._footprint(o), pad=2.0) for o in solid):
            break
        step = 10.0 * (k // 4 + 1)
        dx, dz = ((0, step), (step, 0), (-step, 0), (0, step))[k % 4]
        x = max(-w / 2 + radius, min(w / 2 - radius, actor["pos"][0] + dx))
        z = max(-d / 2 + radius, min(d / 2 - radius, actor["pos"][1] + dz))
    actor["pos"] = [_r(x), _r(z)]


def _step_aside(actor: dict, radius: float, focus: list[dict], objects: list[dict], room: dict) -> None:
    """Человек вплотную перед вещью действия (стоит у тумбочки, заслоняя её от любой камеры спереди) —
    шаг в сторону: встаёт сбоку от неё, лицом туда же; вещь и то, что на ней, видно (живой план 12–14)."""
    for o in focus:
        if o.get("type") in L.FLAT_TYPES or L.WALL_TYPES & {o.get("type")}:
            continue
        rot = float(o.get("rot") or 0.0)
        ox, oz = (float(v) for v in o["pos"])
        # Положение человека в осях вещи: u — вдоль её фронта, v — от фронта наружу.
        dx, dz = actor["pos"][0] - ox, actor["pos"][1] - oz
        u, v = _rot_y(dx, dz, -rot)
        hw, hd = float(o["size"][0]) / 2, float(o["size"][2]) / 2
        if abs(u) < hw + radius * 0.6 and hd - 5 < v < hd + radius + 45:
            w, d = float(room["width"]), float(room["depth"])
            solid = [x for x in objects if L._on_floor(x) and x is not o]
            options = []
            for side in ((1 if u >= 0 else -1), (-1 if u >= 0 else 1)):
                su = side * (hw + radius + 8)
                px, pz = _rot_y(su, hd + radius * 0.5, rot)
                cand = [ox + px, oz + pz]
                fp = (cand[0] - radius, cand[1] - radius, cand[0] + radius, cand[1] + radius)
                inside = abs(cand[0]) <= w / 2 - radius and abs(cand[1]) <= d / 2 - radius
                free = inside and not any(L._overlap(fp, L._footprint(x), pad=2.0) for x in solid)
                options.append((not free, cand))
            options.sort(key=lambda t: t[0])
            actor["pos"] = [_r(options[0][1][0]), _r(options[0][1][1])]
            return


def build(spec: dict, which: str, shot: dict, infos: list[dict], layout: dict | None = None) -> dict:
    """spec плана → {scene — JSON-сцена движка (9:16, toon, без текста), check — самоконтроль кадра,
    camera}. Комната — своя мебель spec, иначе раскладка локации (одна на все планы)."""
    layout = layout or {"room": dict(ROOM_DEFAULT), "objects": []}
    own = bool(spec.get("objects"))
    room = {**ROOM_DEFAULT, **(layout.get("room") or {}), **((spec.get("room") or {}) if own else {})}
    objects = [dict(o) for o in (spec["objects"] if own else layout.get("objects") or [])]
    obj_by = {o["id"]: o for o in objects}
    by_id = {i["id"]: i for i in infos}
    moment = spec.get(which) or spec.get("start") or {"actors": [], "props": []}
    actors = [dict(a) for a in moment.get("actors") or [] if a["id"] in by_id]
    w, d, h = float(room["width"]), float(room["depth"]), float(room["height"])
    nodes: list[dict] = [
        # Пол — от задней стены до камеры (иначе виден его передний край).
        {"type": "plane", "size": [w, d * 4], "rotation": [-90, 0, 0], "position": [0, 0, d * 1.5],
         "color": room["floor"], "order": -2, "material": {"shading": "lambert"}},
        {"type": "plane", "size": [w, h], "position": [0, h / 2, -d / 2], "color": room["wall"], "order": -3,
         "material": {"shading": "lambert"}},
        {"type": "plane", "size": [d, h], "position": [-w / 2, h / 2, 0], "rotation": [0, 90, 0],
         "color": L._shade(room["wall"], 0.94), "order": -3, "material": {"shading": "lambert", "doubleSided": True}},
        {"type": "plane", "size": [d, h], "position": [w / 2, h / 2, 0], "rotation": [0, -90, 0],
         "color": L._shade(room["wall"], 0.94), "order": -3, "material": {"shading": "lambert", "doubleSided": True}},
        {"type": "box", "size": [w, 8, 2], "position": [0, 4, -d / 2 + 1], "color": L._shade(room["wall"], 0.8)},
    ]
    boxes: list[dict] = []
    for o in objects:
        if o.get("type"):
            nodes.append(L.furniture_node(o))
        else:  # простая фигура агента (box/cylinder/disc/sphere)
            nodes.append(L.furniture_node({**o, "type": ""}))
        b = L.object_box(o)
        if b is not None:
            boxes.append(b)
    actor_boxes = []
    placed: dict[str, dict] = {}
    spots: dict[str, dict] = {}
    focus_pre = {str(p.get("on")) for m in ("start", "end") for p in (spec.get(m) or {}).get("props") or []
                 if p.get("on")}
    named_pre = L.find({"objects": objects}, shot.get("action") or "")
    if named_pre is not None and named_pre.get("type") not in L.FLAT_TYPES:
        focus_pre.add(named_pre["id"])
    focus_objs = [obj_by[i] for i in focus_pre if i in obj_by]
    for a in actors:
        info = by_id[a["id"]]
        kind = info.get("stand_in", "human")
        support = obj_by.get(str(a.get("on") or ""))
        if support is not None:
            base = L.top_of(support)
            if kind == "human" and a.get("pose") == "sitting":
                base -= 46 * float(info.get("size") or 165) / 165  # таз на поверхности, а не стопы
                sx, sz = support["pos"]
                if support["type"] in ("bed", "sofa"):  # сидит на краю, ноги на полу
                    dx, dz = _rot_y(0.0, support["size"][2] / 2 - 18, float(support.get("rot") or 0))
                    if support["type"] == "bed":
                        dx, dz = _rot_y(0.0, support["size"][2] * 0.15, float(support.get("rot") or 0))
                        sx += support["size"][0] / 2 - 12
                    a["pos"] = [sx + dx, sz + dz]
                    base = max(0.0, base)
            a["y"] = max(0.0, base)
        elif a.get("y") and kind == "human" and a.get("pose") == "sitting":
            a["y"] = max(0.0, float(a["y"]) - 46.0)  # y — высота сиденья: таз манекена на ней
        if support is None:
            radius = {"human": 22.0, "disc": float(info.get("size") or 33) / 2 + 3,
                      "quadruped": float(info.get("size") or 46) / 2, "small": 14.0}.get(kind, 22.0)
            _settle_actor(a, radius, objects, room)
            if kind == "human":
                _step_aside(a, radius, focus_objs, objects, room)
            # Двое в одной точке (оба «center foreground») — рядом, а не друг в друге.
            for k in range(8):
                other = next((p for p in placed.values() if not p.get("on") and
                              math.dist(p["pos"], a["pos"]) < p["radius"] + radius + 6), None)
                if other is None:
                    break
                gap = other["radius"] + radius + 10
                side = -1 if (k % 2 == 0) == (a["pos"][0] <= other["pos"][0]) else 1
                a["pos"] = [other["pos"][0] + side * gap, other["pos"][1]]
                _settle_actor(a, radius, objects, room)
        else:
            radius = 0.0
        spots[a["id"]] = {**a, "radius": radius}
        placed[a["id"]] = spots[a["id"]]
    _face_partners(actors, by_id, shot, which)
    placed = {}
    for a in actors:
        info = by_id[a["id"]]
        kind = info.get("stand_in", "human")
        support = obj_by.get(str(a.get("on") or ""))
        radius = spots[a["id"]]["radius"]
        parts, box = _actor_parts(a, info)
        if kind == "human" and a.get("pose") == "sitting" and support is None and not a.get("y"):
            parts.append({"type": "box", "size": [40, 44, 40], "position": [0, 22, -6], "color": "#8a7f72"})  # табурет
        x, z = a["pos"]
        y = float(a.get("y") or 0.0)
        facing = float(a.get("facing") or 0.0)
        nodes.append({"type": "group", "position": [_r(x), _r(y), _r(z)], "rotation": [0, facing, 0],
                      "children": parts, "sort": "object"})
        ab = {"id": a["id"], "role": "actor", "label": info.get("name") or a["id"], "kind": kind,
              "pose": a.get("pose"), "center": [x, y + box["cy"], z], "half": box["half"], "rot": facing,
              "height": box["height"]}
        if box.get("head"):
            hx, hz = _rot_y(box["head"][0], box["head"][2], facing)
            ab["head"] = [x + hx, y + box["head"][1], z + hz]
            ab["head_r"] = box["head_r"]
            # Главный персонаж — первый человек плана не лёжа: по нему крупность и запас над головой.
            if a.get("pose") != "lying" and not any(b.get("main") for b in actor_boxes):
                ab["main"] = True
        boxes.append(ab)
        actor_boxes.append(ab)
        placed[a["id"]] = {**a, "rig": box.get("rig"), "kind": kind, "size": float(info.get("size") or 0), "y": y,
                           "box": box, "radius": radius}
    props_placed = []
    # Предмет «висит» сам по себе (pos без holder/on), а у персонажа руки «держит» — он в руке (живой
    # план 12: кружка с кофе была точкой внутри Миры).
    holders = [a["id"] for a in actors if a.get("arms") == "holding" and by_id.get(a["id"], {}).get("stand_in",
                                                                                                     "human") == "human"]
    taken = {str(p.get("holder")) for p in moment.get("props") or [] if p.get("holder")}
    fixed = []
    for p in moment.get("props") or []:
        free = [h for h in holders if h not in taken]
        if not p.get("holder") and not p.get("on") and free:
            p = {**p, "holder": free[0]}
            p.pop("pos", None)
            taken.add(free[0])
        fixed.append(p)
    for k, p in enumerate(fixed):
        nodes += _prop_nodes(p, placed, obj_by, k)
        props_placed.append(prop_place(p, placed, obj_by, k))
    # Вещи действия: на что ставят/кладут реквизит (в начале или в конце плана), на чём сидят/лежат,
    # что названо в действии («врезается в тумбочку») — они обязаны быть в кадре.
    focus_ids = {str(p.get("on")) for m in ("start", "end") for p in (spec.get(m) or {}).get("props") or []
                 if p.get("on")}
    focus_ids |= {str(a.get("on")) for a in actors if a.get("on")}
    named = L.find({"objects": objects}, shot.get("action") or "")
    if named is not None and named.get("type") not in L.FLAT_TYPES:
        focus_ids.add(named["id"])
    focus = []
    for b in boxes:
        if b["role"] == "object" and b["id"] in focus_ids:
            b["focus"] = True
            focus.append(b)
    if spec.get("camera"):
        cam = spec["camera"]
        check = check_frame(cam, boxes, room, str((shot.get("camera") or {}).get("shot") or "medium"))
        check = {**check, "size": (shot.get("camera") or {}).get("shot") or "medium", "notes": ["своя камера spec"]}
    else:
        cam, check = choose_camera(shot, actor_boxes, boxes, room, focus)
    scene = {
        "camera": {"type": "perspective", "position": list(cam["pos"]), "target": list(cam["target"]),
                   "fov": float(cam.get("fov") or 40), "near": 3, "far": 6000},
        "lights": [{"type": "hemisphere", "sky": "#ffffff", "ground": "#8a7f72", "intensity": 0.6},
                   {"type": "directional", "direction": [-0.6, 1, 0.8], "intensity": 0.7}],
        "background": {"gradient": ["#cfc7bb", "#efe9df"], "angle": 90},
        "style": {"shading": "toon", "bands": 3, "outline": {"color": "#2a2a30", "width": 2}},
        "nodes": nodes,
    }
    return {"scene": scene, "check": check, "camera": cam, "boxes": boxes, "room": room, "objects": objects,
            "actors": list(placed.values()), "props": props_placed}


def _face_partners(actors: list[dict], infos: dict[str, dict], shot: dict, which: str) -> None:
    """Конец плана: человек, чьё действие — над/к другому персонажу («наклоняет кружку над роботом»,
    «протягивает Бипу»), повёрнут к нему (места уже расставлены — по ним)."""
    if which != "end":
        return
    for a in actors:
        if infos.get(a["id"], {}).get("stand_in", "human") != "human":
            continue
        member = next((c for c in shot.get("cast") or [] if c["id"] == a["id"]), {})
        text = _norm(member.get("action") or "")
        for b in actors:
            if b is a:
                continue
            kind = infos.get(b["id"], {}).get("stand_in", "human")
            name = _norm(infos.get(b["id"], {}).get("name") or b["id"])
            if (name and name[:max(3, len(name) - 1)] in text) or (
                    kind == "disc" and re.search(r"робот|пылесос|robot|vacuum", text)):
                dx, dz = b["pos"][0] - a["pos"][0], b["pos"][1] - a["pos"][1]
                if dx * dx + dz * dz > 1:
                    a["facing"] = round(math.degrees(math.atan2(dx, dz)), 1)
                break


def compile_scene(spec: dict, which: str, shot: dict, infos: list[dict], layout: dict | None = None) -> dict:
    """spec плана → JSON-сцена движка (кадр start или end)."""
    return build(spec, which, shot, infos, layout)["scene"]


def prop_place(prop: dict, actors: dict[str, dict], objects: dict[str, dict], k: int = 0) -> dict:
    """Где реквизит в кадре: {kind, size, color, base [x, y, z] см, holder, on, count} (2D- и 3D-блокинг)."""
    kind, size, color = prop_kind(prop.get("name") or prop.get("id"))
    if prop.get("size") and kind == "item":
        size = [float(prop["size"])] * 3
    color = prop.get("color") or color
    holder = actors.get(str(prop.get("holder") or ""))
    support = objects.get(str(prop.get("on") or ""))
    if holder is not None:
        x, z = holder["pos"]
        facing = float(holder.get("facing") or 0.0)
        if holder.get("kind") == "human" and holder.get("rig"):
            hx, hy, hz = _hand_local(holder["rig"], 1)
            dx, dz = _rot_y(hx, hz + 3.0, facing)
            base = [x + dx, holder["y"] + hy - size[1] * 0.45, z + dz]
        else:  # робот/зверь «держит» — на спине
            base = [x, holder["y"] + float(holder["box"]["height"]), z]
    elif support is not None:
        sx, sz = support["pos"]
        off = (0.22 + 0.12 * k) * float(support["size"][0])
        dx, dz = _rot_y(off if support["type"] != "bed" else 0.0, 0.0, float(support.get("rot") or 0.0))
        base = [sx + dx - 0.0, L.top_of(support), sz + dz]
        if support["type"] in ("nightstand", "desk", "table"):
            dx2, dz2 = _rot_y(0.0, float(support["size"][2]) * 0.15, float(support.get("rot") or 0.0))
            base = [base[0] + dx2, base[1], base[2] + dz2]
    else:
        base = list(prop.get("pos") or [0, 0, 40])
    return {"id": prop.get("id"), "name": prop.get("name") or prop.get("id"), "kind": kind, "size": size,
            "color": color, "base": [float(v) for v in base], "holder": holder["id"] if holder is not None else "",
            "on": support["id"] if support is not None else "", "count": int(prop.get("count") or 1)}


def _prop_nodes(prop: dict, actors: dict[str, dict], objects: dict[str, dict], k: int = 0) -> list[dict]:
    pl = prop_place(prop, actors, objects, k)
    kind, size, color, base = pl["kind"], pl["size"], pl["color"], pl["base"]
    out = []
    for i in range(pl["count"]):
        out.append({"type": "group", "position": [_r(base[0] + i * size[0] * 1.5), _r(base[1]), _r(base[2])],
                    "rotation": [0, 0, 0], "children": _prop_parts(kind, size, color)})
    return out
