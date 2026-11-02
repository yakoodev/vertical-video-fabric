"""ИИ-видео: блокинг в 3D-движке сцен (``node/scene3d``) вместо 2D-холста — стоп-кадры плана.

Те же данные, что у 2D-блокинга (``app/aivideo_blocking.build``): комната локации
(``app/aivideo_layout``), места/позы/взгляд персонажей из state и end_state, реквизит в руке и на
вещах, камера по правилам кадра (``choose_camera``/``check_frame`` — все участники и вещи действия в
кадре, голова не режется). Здесь это переводится в сцену движка:

* **Вещи** → модели Kenney Furniture Kit по типу раскладки, вписанные в её габарит (``fit``) и
  перекрашенные в её цвета; окно и дверь — настоящие проёмы стены (окно со шторами и видом),
  гирлянда — лампочки по стене; нет модели — аккуратная фигура нужного размера и цвета.
* **Персонажи** → человек: тело со скелетом Quaternius, причёска и цвета волос/одежды из карточки
  (свободная футболка и шорты — оболочка, без облегания), очки, если в карточке; матовые ровные цвета
  без текстуры кожи. Робот-диск — по карточке (белый корпус, полоса по ободу, экраны-глаза, антенна).
* **Позы** — кадры готовых анимаций UAL (таблица ``POSE_CLIPS``: поза+руки → клип и время), не
  крутка костей; предмет в руке — на сокете кисти. Без подходящего клипа (руки вверх, машет) —
  стоячая поза и поворот рук.
* **Стиль** — нейтральный чистый макет: матовые материалы, мягкий свет, без тумана и реализма.

``scene_spec`` — сцена движка (JSON); ``render_png`` — PNG 9:16 одним кадром; ``available`` —
можно ли рисовать 3D (Node, браузер, бандл, паки), иначе блокинг рисует старый 2D-холст.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from app import aivideo_layout as L

ENGINE_VERSION = "3d-1"  # меняется — кадры блокинга пересчитываются
CM = 0.01
UAL = ["quaternius-animations/UAL1_Standard.glb", "quaternius-animations/UAL2_Standard.glb"]
FEMALE = "quaternius-characters/Superhero_Female_FullBody.gltf"
MALE = "quaternius-characters/Superhero_Male_FullBody.gltf"

# Поза (+ руки) → кадр готовой анимации: (клип, секунда). Руки «holding» — сокет кисти держит предмет.
POSE_CLIPS: dict[tuple[str, str], tuple[str, float]] = {
    ("standing", "down"): ("Idle_Loop", 0.0),
    ("standing", "holding"): ("Idle_Talking_Loop", 0.5),  # предмет у груди обеими руками
    ("standing", "forward"): ("Interact", 0.8),
    ("standing", "up"): ("Idle_Loop", 0.0),
    ("standing", "wave"): ("Idle_Loop", 0.0),
    ("walking", "down"): ("Walk_Loop", 0.35),
    ("walking", "holding"): ("Walk_Carry_Loop", 0.5),
    ("sitting", "down"): ("Sitting_Idle_Loop", 0.5),
    ("crouching", "down"): ("Crouch_Idle_Loop", 0.5),
    ("kneeling", "down"): ("Fixing_Kneeling", 0.5),
    ("lying", "down"): ("LayToIdle", 0.05),
}
# Нет клипа — поворот рук поверх стоячей позы (направление кости: вправо, вверх, вперёд).
ARM_POSES: dict[str, dict[str, list[float]]] = {
    "up": {"upperarm_l": [-0.28, 1, 0.05], "lowerarm_l": [0.3, 1, 0.0], "upperarm_r": [0.28, 1, 0.05],
           "lowerarm_r": [-0.3, 1, 0.0]},
    "wave": {"upperarm_r": [0.85, 0.55, 0.1], "lowerarm_r": [0.15, 1, 0.1]},
}

# Тип вещи раскладки → модель Kenney и какой материал красить её цветом (accent — второй цвет).
FURNITURE: dict[str, dict[str, Any]] = {
    "bed": {"model": "bedDouble.glb", "color": "wood", "accent": "carpet", "extra": {"carpetWhite": "#f6f1ea"}},
    "nightstand": {"model": "cabinetBedDrawerTable.glb", "color": "wood"},
    "lamp": {"model": "lampRoundTable.glb", "color": "lamp", "light": 1.6},
    "floor_lamp": {"model": "lampRoundFloor.glb", "color": "lamp", "light": 2.2},
    "desk": {"model": "desk.glb", "color": "wood"},
    "table": {"model": "table.glb", "color": "wood"},
    "chair": {"model": "chairDesk.glb", "color": "carpet"},
    "sofa": {"model": "loungeSofa.glb", "color": "carpet"},
    "shelf": {"model": "bookcaseOpenLow.glb", "color": "wood"},
    "bookcase": {"model": "bookcaseOpen.glb", "color": "wood"},
    "wardrobe": {"model": "bookcaseClosedDoors.glb", "color": "wood"},
    "dresser": {"model": "sideTableDrawers.glb", "color": "wood"},
    "rug": {"model": "rugRectangle.glb", "color": "carpet", "accent": "carpetDarker"},
    "plant": {"model": "pottedPlant.glb", "color": "plant"},
    "plush": {"model": "bear.glb", "color": "fur"},
    "mirror": {"model": "bathroomMirror.glb"},
    "tv": {"model": "televisionModern.glb"},
    "computer": {"model": "laptop.glb"},
    "fridge": {"model": "kitchenFridge.glb"},
    "counter": {"model": "kitchenCabinet.glb"},
    "stove": {"model": "kitchenStove.glb"},
    "books": {"model": "books.glb"},
}
ROUND_RUG = re.compile(r"кругл|овальн|round|oval")


def _hex(value: Any, default: str) -> str:
    return L._hex(value, default)


def _m(v: float) -> float:
    return round(float(v) * CM, 4)


# --------------------------------------------------------------------------- #
# Карточка персонажа → облик манекена
# --------------------------------------------------------------------------- #

_HEX = re.compile(r"#[0-9a-fA-F]{6}")
_PARTS = {
    "hair": r"волос|каре|причёс|причес|чёлк|челк|прядь|косичк|hair|\bbob\b|bangs",
    "shirt": r"футболк|майк|кофт|рубашк|свитер|худи|топ\b|блузк|платье|t-?shirt|shirt|hoodie|sweater|top\b|dress",
    "bottom": r"шорт|брюк|штан|джинс|юбк|леггинс|pants|shorts|jeans|skirt|trousers",
}


def _clause_color(clause: str) -> str | None:
    m = _HEX.search(clause)
    if m:
        return m.group(0).lower()
    return L.color_in(clause)


def appearance(character: dict | None) -> dict:
    """Облик человека для блокинга из карточки: пол, причёска и цвета волос/верха/низа, очки."""
    text = f"{(character or {}).get('identity_card') or ''}; {(character or {}).get('constraints') or ''}"
    t = L._norm(text)
    male = bool(re.search(r"\bпарен|мужчин|мальчик|дедушк|\bman\b|\bboy\b|\bhe\b|\bguy\b", t)) and \
        not re.search(r"девушк|женщин|девочк|бабушк|woman|girl|\bshe\b", t)
    out = {"model": MALE if male else FEMALE, "hair_color": "#3a2a20", "shirt": "#9cb0c8", "bottom": "#6f7480",
           "hair": "quaternius-characters/Hair_SimpleParted.gltf" if male else "quaternius-characters/Hair_Long.gltf",
           "glasses": bool(re.search(r"очк[иоа]|glasses|spectacles", t)), "skin": "#f1c9a5"}
    if re.search(r"смугл|тёмн\w* кож|темн\w* кож|dark skin|tan skin", t):
        out["skin"] = "#b07a55"
    # Части карточки: «;» и «,» и « и » — каждая про одну вещь.
    for clause in re.split(r";|,|\s+и\s+|\s+and\s+|\s+—\s+", text):
        c = L._norm(clause)
        for part, words in _PARTS.items():
            if re.search(words, c):
                color = _clause_color(clause)
                if color:
                    out[{"hair": "hair_color", "shirt": "shirt", "bottom": "bottom"}[part]] = color
    if re.search(r"пуч(ок|ки|ками)|\bbuns?\b", t):
        out["hair"] = "quaternius-characters/Hair_Buns.gltf"
    elif re.search(r"лыс|bald", t):
        out["hair"] = ""
    elif male and re.search(r"длинн\w* волос|long hair", t):
        out["hair"] = "quaternius-characters/Hair_Long.gltf"
    return out


def robot_look(character: dict | None, size_cm: float) -> dict:
    """Робот-диск по карточке: корпус, полоса по ободу, экраны-глаза спереди или визор, антенна."""
    text = L._norm(f"{(character or {}).get('identity_card') or ''} {(character or {}).get('constraints') or ''}")
    params: dict[str, Any] = {"radius": round(size_cm / 2 * CM, 4), "color": "#f5f5f3"}
    body = re.search(r"(бел|черн|чёрн|сер|white|black|grey|gray)\w*\s+(глянц\w*\s+)?корпус", text)
    if body:
        params["color"] = L.color_in(body.group(0)) or params["color"]
    for clause in re.split(r";|,", text):
        if re.search(r"полос|ободок|обод|stripe|rim", clause):
            col = _clause_color(clause)
            if col:
                params["stripe"] = col
        if re.search(r"антенн|antenna", clause):
            params["antenna"] = "#ff3b30" if re.search(r"красн|red", clause) else (_clause_color(clause) or "#ff3b30")
        if re.search(r"глаз|экран|eye|screen", clause):
            params["faceColor"] = _clause_color(clause) or "#3aa0ff"
            if re.search(r"кромк|перед|спереди|front|edge|бок", clause):
                params["screen"] = "rim"
    params.setdefault("faceColor", "#3aa0ff")
    params.setdefault("screen", "visor")
    params["height"] = round(max(0.06, params["radius"] * 0.5), 4)
    return params


_FACES = [
    (r"невинн|огромн\w* глаз|big eyes|innocent|puppy", "innocent"),
    (r"радост|счастл|довол|мига|happy|joy|smil|улыб", "happy"),
    (r"удивл|испуг|врез|стука|surpris|shock", "surprised"),
    (r"груст|печал|sad", "sad"),
    (r"сонн|спит|sleep", "sleepy"),
    (r"головокруж|dizzy|оглуш", "dizzy"),
]


def robot_face(text: str) -> str:
    t = L._norm(text)
    for words, face in _FACES:
        if re.search(words, t):
            return face
    return "neutral"


# --------------------------------------------------------------------------- #
# Сцена движка
# --------------------------------------------------------------------------- #


def _wall_of(o: dict, room: dict) -> tuple[str, float]:
    """На какой стене вещь (окно/дверь/постер) и где вдоль неё (см, мировая ось вдоль стены)."""
    w, d = float(room["width"]), float(room["depth"])
    x, z = (float(v) for v in o["pos"])
    gaps = {"back": abs(z + d / 2), "front": abs(z - d / 2), "left": abs(x + w / 2), "right": abs(x - w / 2)}
    rot = float(o.get("rot") or 0.0)
    if abs(rot - 90) < 1:
        gaps["left"] -= 1000
    elif abs(rot + 90) < 1:
        gaps["right"] -= 1000
    wall = min(gaps, key=gaps.get)
    return wall, (x if wall in ("back", "front") else z)


def _room(room: dict, objects: list[dict], evening: bool) -> dict:
    out: dict[str, Any] = {
        "width": _m(room["width"]), "depth": _m(room["depth"]), "height": _m(room["height"]),
        "floor": {"color": _hex(room.get("floor"), "#b58a62"), "plank": 0.14},
        "walls": {"color": _hex(room.get("wall"), "#e9dfd0")},
        "baseboard": {"color": "#f4f0e8", "height": 0.08},
        "hallway": False, "openings": [],
        # Открытая декорация, как у 2D-макета: передней стены и потолка нет, пол и боковые стены
        # тянутся к камере (камера плана часто дальше передней стены).
        "open": ["front"], "ceiling": False, "extend": round(_m(room["depth"]) * 3, 3),
    }
    for o in objects:
        if o.get("type") not in ("window", "door"):
            continue
        wall, along = _wall_of(o, room)
        w, h = float(o["size"][0]), float(o["size"][1])
        if o["type"] == "window":
            sky = _hex(o.get("accent"), "#2c3e7a" if evening else "#9cc8e8")
            op = {"wall": wall, "type": "window", "x": _m(along), "y": _m(o.get("y") or 95), "width": _m(w),
                  "height": _m(h), "view": {"sky": [L._shade(sky, 0.55), sky, L._shade(sky, 1.35)]}}
            op["curtains"] = {"color": _hex(o.get("curtain_color"), "#efe2cf"), "open": 0.7} if o.get("curtains") else False
        else:
            op = {"wall": wall, "type": "door", "x": _m(along), "width": _m(w), "height": _m(h),
                  "door": {"color": _hex(o.get("color"), "#c9a27a"), "angle": 0}}
        out["openings"].append(op)
    return out


def _furniture(o: dict, room: dict) -> list[dict]:
    kind = o.get("type") or ""
    w, h, d = (float(v) for v in o["size"])
    color = _hex(o.get("color"), "#8a7f72")
    pos = [_m(o["pos"][0]), _m(o.get("y") or 0.0), _m(o["pos"][1])]
    rot = [0, float(o.get("rot") or 0.0), 0]
    oid = f"obj_{o['id']}"
    if kind in ("window", "door"):
        return []
    if kind == "garland":
        x, z = float(o["pos"][0]), float(o["pos"][1])
        y = float(o.get("y") or 185)
        wall, _along = _wall_of(o, room)
        if wall in ("back", "front"):
            zz = z + (3 if wall == "back" else -3)
            pts = [[_m(x - w / 2), _m(y), _m(zz)], [_m(x), _m(y - 6), _m(zz)], [_m(x + w / 2), _m(y), _m(zz)]]
        else:
            xx = x + (3 if wall == "left" else -3)
            pts = [[_m(xx), _m(y), _m(z - w / 2)], [_m(xx), _m(y - 6), _m(z)], [_m(xx), _m(y), _m(z + w / 2)]]
        return [{"id": oid, "primitive": "string_lights", "params": {"points": pts, "count": max(10, int(w // 10)),
                                                                    "sag": 0.08, "color": color}}]
    if kind == "poster":
        return [{"id": oid, "primitive": "poster", "params": {"width": _m(w), "height": _m(h), "seed": len(o["id"])},
                 "position": [pos[0], _m(o.get("y") or 150) - _m(h) / 2, pos[2]], "rotation": rot}]
    if kind in ("socks", "clothes"):
        out = []
        n = int(o.get("count") or (2 if kind == "socks" else 2))
        cols = [color, "#f2d16b", "#9cc8e8"] if kind == "socks" else [color, "#e8e4dc"]
        spots = [(-12, -6, 25), (10, 8, -40), (-4, 14, 70)]
        for i in range(min(n, 3)):
            sx, sz, r = spots[i]
            size = [0.07, 0.015, 0.18] if kind == "socks" else [_m(w) * (1 - 0.3 * i), 0.012, _m(d) * (1 - 0.3 * i)]
            out.append({"id": f"{oid}_{i}", "primitive": "box", "params": {"size": size, "color": cols[i % len(cols)],
                                                                          "roughness": 1},
                        "position": [pos[0] + _m(sx), 0.0, pos[2] + _m(sz)], "rotation": [0, r, 0]})
        return out
    if kind == "dock":
        return [{"id": oid, "primitive": "box", "params": {"size": [_m(w), _m(h), _m(d)], "color": "#3a3d44"},
                 "position": pos, "rotation": rot},
                {"id": f"{oid}_led", "primitive": "sphere", "params": {"size": [0.012], "color": "#22d3ee",
                                                                       "emissive": "#22d3ee"},
                 "position": [pos[0], _m(h) - 0.02, pos[2] + _m(d) / 2], "rotation": rot}]
    spec = FURNITURE.get(kind)
    if spec is None:  # своя/неизвестная вещь — фигура нужного размера и цвета
        shape = o.get("kind") or "box"
        prim = {"cylinder": "cylinder", "disc": "cylinder", "sphere": "sphere"}.get(shape, "box")
        return [{"id": oid, "primitive": prim, "params": {"size": [_m(w), _m(h), _m(d)], "color": color, "roughness": 0.9},
                 "position": pos, "rotation": rot}]
    model = spec["model"]
    if kind == "rug" and ROUND_RUG.search(L._norm(o.get("name") or "")):
        model = "rugRound.glb"
    if kind == "table" and re.search(r"журнальн|coffee", L._norm(o.get("name") or "")):
        model = "tableCoffee.glb"
    if kind == "bed" and w < 120:
        model = "bedSingle.glb"
    hh = max(h, 1.0)
    if kind == "rug":
        hh = 1.0
    mats: dict[str, Any] = dict(spec.get("extra") or {})
    if spec.get("color"):
        mats[spec["color"]] = color if kind != "lamp" and kind != "floor_lamp" else {
            "color": color, "emissive": "#ffc477", "emissiveIntensity": 0.9}
    if spec.get("accent"):
        mats[spec["accent"]] = _hex(o.get("accent"), L._shade(color, 0.82) if kind == "rug" else "#c9b8e8")
    obj: dict[str, Any] = {"id": oid, "model": f"kenney-furniture/{model}", "fit": [_m(w), _m(hh), _m(d)],
                           "position": pos, "rotation": rot, "materials": mats}
    if kind == "shelf":
        obj["position"][1] = _m(o.get("y") or 135) - _m(h) / 2
    if spec.get("light"):
        obj["light"] = {"type": "point", "color": "#ffc27a", "intensity": spec["light"], "position": [0, _m(h) * 0.75, 0],
                        "castShadow": False}
    return [obj]


def _actor(a: dict, info: dict, character: dict | None, look_text: str) -> tuple[list[dict], list[dict]]:
    """Персонаж → (персонажи движка, объекты движка)."""
    kind = a.get("kind") or info.get("stand_in") or "human"
    x, z = (float(v) for v in a["pos"])
    y = float(a.get("y") or 0.0)
    facing = float(a.get("facing") or 0.0)
    size = float(info.get("size") or a.get("size") or 165)
    if kind == "disc":
        params = robot_look(character, size)
        params["face"] = robot_face(look_text)
        return [], [{"id": f"actor_{a['id']}", "primitive": "robot_disc", "params": params,
                     "position": [_m(x), _m(y), _m(z)], "rotation": [0, facing, 0]}]
    if kind != "human":
        col = _hex(info.get("color"), "#c2a98a")
        s = _m(size)
        return [], [{"id": f"actor_{a['id']}", "primitive": "sphere" if kind == "small" else "box",
                     "params": {"size": [s * 0.5, s * 0.45, s] if kind == "quadruped" else [s], "color": col},
                     "position": [_m(x), _m(y), _m(z)], "rotation": [0, facing, 0]}]
    look = appearance(character)
    pose = a.get("pose") or "standing"
    arms = a.get("arms") or "down"
    clip, at = POSE_CLIPS.get((pose, arms)) or POSE_CLIPS.get((pose, "down")) or POSE_CLIPS[("standing", "down")]
    ch: dict[str, Any] = {
        "id": a["id"], "model": look["model"], "height": round(size * CM, 3), "style": "flat",
        "skinColor": look["skin"], "animations": UAL, "position": [_m(x), 0.0, _m(z)], "yaw": facing,
        "outfit": {"shirt": look["shirt"], "shorts": look["bottom"], "fit": "loose", "sleeve": 0.55, "shortsLength": 0.5},
        "actions": [{"clip": clip, "start": 0, "speed": 0, "offset": at}],
    }
    if look["hair"]:
        ch["hair"] = {"model": look["hair"], "color": look["hair_color"]}
    if look["glasses"]:
        ch["glasses"] = {"color": "#3a3236"}
    if arms in ARM_POSES and pose in ("standing", "walking"):
        ch["poses"] = [{"start": -1, "end": 99, "fade": 0.001, "aim": ARM_POSES[arms]}]
    extra: list[dict] = []
    if pose == "sitting":
        ch["seat"] = round((y + 46.0 * size / 165) * CM, 3) if y else round(0.46 * size / 165, 3)
        if not a.get("on") and not y:  # сидит «ни на чём» — табурет, как у 2D-макета
            dx, dz = math.sin(math.radians(facing)) * -0.06, math.cos(math.radians(facing)) * -0.06
            extra.append({"id": f"stool_{a['id']}", "primitive": "box",
                          "params": {"size": [0.4, round(ch["seat"] - 0.02, 3), 0.4], "color": "#8a7f72", "roughness": 1},
                          "position": [_m(x) + dx, 0.0, _m(z) + dz], "rotation": [0, facing, 0]})
    elif pose == "lying" and y:
        ch["lie"] = round(y * CM, 3)
    elif y:
        ch["position"][1] = _m(y)
    return [ch], extra


def _prop(p: dict, k: int) -> list[dict]:
    kind, size, color = p["kind"], [float(v) for v in p["size"]], _hex(p.get("color"), "#c98b3b")
    base = p["base"]
    out = []
    for i in range(int(p.get("count") or 1)):
        oid = f"prop_{p.get('id') or k}_{i}"
        pos = [_m(base[0] + i * size[0] * 1.5), _m(base[1]), _m(base[2])]
        if kind == "mug":
            obj: dict[str, Any] = {"id": oid, "primitive": "mug",
                                   "params": {"color": color, "radius": _m(size[0]) / 2, "height": _m(size[1])},
                                   "position": pos, "rotation": [0, -30, 0]}
        else:
            prim = "cylinder" if kind in ("bottle", "glass", "flower") else "sphere" if kind == "ball" else "box"
            obj = {"id": oid, "primitive": prim,
                   "params": {"size": [_m(size[0]), _m(size[1]), _m(size[2])], "color": color, "roughness": 0.85},
                   "position": pos, "rotation": [0, 0, 0]}
        if p.get("holder"):
            obj["holder"] = p["holder"]
        out.append(obj)
    return out


def scene_spec(built: dict, shot: dict, infos: list[dict], characters: dict[str, dict], which: str,
               width: int = 1080, height: int = 1920) -> dict:
    """Собранный 2D-блокинг (``aivideo_blocking.build``) → сцена 3D-движка (те же места и камера)."""
    room = built["room"]
    objects = built["objects"]
    text = L._norm(" ".join(str(shot.get(k) or "") for k in ("action", "location")))
    evening = bool(re.search(r"вечер|ноч|night|evening|dusk", text + " " + L._norm(json.dumps(room))))
    by_info = {i["id"]: i for i in infos}
    chars, objs = [], []
    for o in objects:
        objs += _furniture(o, room)
    for a in built["actors"]:
        info = by_info.get(a["id"], {})
        member = next((c for c in shot.get("cast") or [] if c["id"] == a["id"]), {})
        look_text = f"{shot.get('action') or ''} {member.get('action') or ''}" if which == "end" else \
            f"{member.get('action') or ''}"
        if which == "end":
            look_text = f"{member.get('action') or ''} {shot.get('action') or ''}"
        c, o = _actor(a, info, characters.get(a["id"]), look_text)
        chars += c
        objs += o
    for k, p in enumerate(built["props"]):
        for obj in _prop(p, k):
            holder = obj.pop("holder", "")
            if holder and any(c["id"] == holder for c in chars):
                # Сокет кисти: предмет в правой руке, стоит ровно.
                obj["attach"] = [{"to": f"{holder}:hand_r", "start": -1, "end": 99, "fadeIn": 0, "fadeOut": 0,
                                  "offset": [0.0, -0.035, 0.045], "settle": False}]
            objs.append(obj)
    cam = built["camera"]
    lights: list[dict] = [
        {"type": "hemisphere", "color": "#ffffff", "ground": "#8a7f72", "intensity": 1.15},
        {"type": "directional", "color": "#fff4e6", "intensity": 1.5, "position": [-1.6, 3.4, 3.2], "target": [0, 0.6, -0.4],
         "castShadow": True, "shadow": {"size": 2048, "extent": max(_m(room["width"]), _m(room["depth"])) * 0.75,
                                         "bias": -0.0004, "normalBias": 0.03, "radius": 4}},
    ]
    return {
        "version": 1, "width": width, "height": height, "fps": 30, "duration": 1,
        "environment": {"background": _hex(room.get("wall"), "#e9dfd0"), "fog": False, "exposure": 1.0,
                        "envIntensity": 0.35, "toneMapping": "neutral", "style": "flat"},
        "room": _room(room, objects, evening),
        "lights": lights,
        "objects": objs,
        "characters": chars,
        # Головы людей — в кадре при любой позе клипа (их габарит у 2D-манекена другой).
        "camera": {"clamp": False, "far": 80, "keepInFrame": {"ids": [c["id"] for c in chars], "margin": 0.06}, "keys": [{"t": 0, "position": [_m(v) for v in cam["pos"]],
                                             "target": [_m(v) for v in cam["target"]],
                                             "fov": float(cam.get("fov") or 40)}]},
    }


def spec_key(spec: dict) -> str:
    body = json.dumps({"v": ENGINE_VERSION, "spec": spec}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(body.encode("utf-8")).hexdigest()[:16]


def legend(infos: list[dict], characters: dict[str, dict]) -> str:
    """Для промпта кадра: кто есть кто в 3D-макете («the girl with mint hair and lavender T-shirt = Мира»)."""
    parts = []
    for i in infos:
        c = characters.get(i["id"])
        if i.get("stand_in") == "disc":
            parts.append(f"the white disc robot = {i['name']}")
        elif i.get("stand_in") == "human":
            look = appearance(c)
            parts.append(f"the figure with {look['hair_color']} hair and {look['shirt']} top = {i['name']}")
        else:
            parts.append(f"the {i.get('color_name') or 'plain'} stand-in = {i['name']}")
    return "; ".join(parts)


def available() -> bool:
    """3D-блокинг возможен: не выключен (AIV_BLOCKING_ENGINE=2d), есть Node, браузер, бандл и 3D-паки."""
    if os.environ.get("AIV_BLOCKING_ENGINE", "auto").strip().lower() in ("2d", "canvas", "off"):
        return False
    from app import scene3d

    try:
        scene3d.node_bin()
        scene3d.ensure_bundle()
    except scene3d.Scene3dError:
        return False
    packs = scene3d.packs_dir()
    return bool(scene3d.chromium_path()) and (packs / "kenney-furniture").is_dir() and \
        (packs / "quaternius-characters" / "Superhero_Female_FullBody.gltf").is_file()


def render_png(spec: dict) -> bytes:
    """Один кадр сцены → PNG (headless Chromium, 0 ₽)."""
    from app import scene3d

    with tempfile.TemporaryDirectory(prefix="aiv-block3d-") as tmp:
        frames = scene3d.preview_frames(spec, [0.0], tmp)
        if not frames:
            raise scene3d.Scene3dError("3D-блокинг: кадр не отрисован")
        return Path(frames[0]["file"]).read_bytes()
