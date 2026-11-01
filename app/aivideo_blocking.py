"""ИИ-видео v3: блокинг планов в нашем 3D-движке сцен (слой ``scene``, docs/motion/scene-engine.md).

Зачем: картинковая модель рисует ключевые кадры «по описанию» — позы, места и ракурс плывут, пылесос
встаёт на дыбы, печенье двоится. Блокинг даёт ей КОМПОЗИЦИЮ: простые манекены в комнате, снятые
камерой плана, на старте и на конце плана. Скриншот блокинга идёт первым референсом кадра (img2img
по композиции), листы персонажей и плита локации — следом.

* **spec** — простая раскладка плана (сантиметры; x вправо, y вверх, z на камеру; пол y=0, комната
  по центру):

  .. code-block:: json

     {"room": {"width": 500, "depth": 400, "height": 260, "floor": "#b9a68a", "wall": "#e9e2d4"},
      "objects": [{"id": "bed", "kind": "box", "size": [200, 50, 90], "pos": [-120, -100], "rot": 0}],
      "camera": {"pos": [0, 130, 320], "target": [0, 80, 0], "fov": 40},
      "start": {"actors": [{"id": "mira", "pos": [-40, 20], "facing": "camera", "pose": "standing"}],
                "props": [{"id": "cookie", "kind": "disc", "size": 8, "pos": [30, 0, 40], "count": 1}]},
      "end": {"actors": [...], "props": [...]}}

  ``camera`` можно не писать — она ставится по плану (``camera.shot``/``angle``). ``facing`` — градусы
  (0 — в камеру, 90 — вправо кадра, −90 — влево, 180 — спиной) или camera|left|right|away. ``pose`` —
  standing | walking | sitting | kneeling | lying. ``kind`` мебели/реквизита — box | cylinder | disc |
  sphere. Реквизит ``holder`` — в руке персонажа.
* **Манекены** (``stand_in`` персонажа или по карте личности): human — капсула с головой-шаром и
  «носом» (куда смотрит), disc — плоский диск на полу (робот-пылесос), quadruped — коробка с головой,
  small — шар. У каждого персонажа свой цвет — промпт кадра говорит, какой цвет кто.
* **Кто пишет**: Gemini — раскладку всей серии одним запросом (бесплатно; одна комната на локацию,
  места из state/end_state планов); нет ключа — ``auto_spec`` по state (left/center/right,
  foreground/background, поза, взгляд). Агент и владелец правят spec (``set_blocking``) или
  кладут свою JSON-сцену движка поверх (``scenes.start/end``).
* **Рендер** — компоновщиком (тот же, что рендер клипа), 9:16, 0 ₽: версии ``blocking_start`` /
  ``blocking_end`` плана (``prompt`` = ``blocking:<хэш сцены>``).
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from app import aivideo as V
from app import projects as P

KINDS = ("box", "cylinder", "disc", "sphere")
POSES = ("standing", "walking", "sitting", "kneeling", "lying")
ACTOR_COLORS = ("#e8743b", "#3b82e8", "#22a35a", "#c44fd1")
COLOR_NAMES = {"#e8743b": "orange", "#3b82e8": "blue", "#22a35a": "green", "#c44fd1": "purple"}
ROOM_DEFAULT = {"width": 500, "depth": 400, "height": 260, "floor": "#b9a68a", "wall": "#e9e2d4"}
# Дистанция камеры (см) по крупности плана — вертикальное поле 40° у кадра 9:16.
SHOT_DISTANCE = {"extreme_wide": 1100, "wide": 750, "full": 430, "medium": 270, "medium_close": 190,
                 "close": 120, "extreme_close": 70}
BLOCKING_W, BLOCKING_H = 1080, 1920
RENDER_WIDTH = 540
LIMIT = 1000.0  # см: дальше комнаты ничего не ставим


def _num(value: Any, default: float = 0.0, lo: float = -LIMIT * 2, hi: float = LIMIT * 2) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return max(lo, min(hi, number))


def _color(value: Any, default: str) -> str:
    text = str(value or "").strip()
    if len(text) == 7 and text.startswith("#"):
        try:
            int(text[1:], 16)
            return text.lower()
        except ValueError:
            pass
    return default


# --------------------------------------------------------------------------- #
# Манекены персонажей
# --------------------------------------------------------------------------- #

_DISC_WORDS = ("пылесос", "vacuum", "roomba", "диск", "disc", "шайб")
_QUAD_WORDS = ("кот", "кошк", "cat", "собак", "пёс", "пес", "dog", "щен", "puppy", "лис", "fox", "хомяк")
_SMALL_WORDS = ("мыш", "mouse", "птиц", "bird", "игрушк", "toy", "мяч", "ball")


def stand_in(character: dict | None) -> str:
    """Манекен персонажа: свой ``stand_in`` или по карте личности и ограничениям."""
    if not character:
        return "human"
    if character.get("stand_in"):
        return str(character["stand_in"])
    text = f"{character.get('identity_card') or ''} {character.get('constraints') or ''} {character.get('name') or ''}"
    text = text.casefold()
    if any(w in text for w in _DISC_WORDS):
        return "disc"
    if any(w in text for w in _QUAD_WORDS):
        return "quadruped"
    if any(w in text for w in _SMALL_WORDS):
        return "small"
    return "human"


def cast_info(store: Any, project_id: int, shot: dict) -> list[dict]:
    """Персонажи плана: id, имя, манекен, цвет манекена (по порядку в кадре)."""
    out = []
    for n, member in enumerate(shot.get("cast") or []):
        c = V.character_by_slug(store, project_id, member["id"])
        color = ACTOR_COLORS[n % len(ACTOR_COLORS)]
        out.append({"id": member["id"], "name": (c or {}).get("name") or member["id"], "stand_in": stand_in(c),
                    "color": color, "color_name": COLOR_NAMES[color]})
    return out


def legend(store: Any, project_id: int, shot: dict) -> str:
    """Для промпта кадра: какой манекен — кто («orange mannequin = Мира; grey-blue flat disc = Бип»)."""
    what = {"human": "mannequin", "disc": "flat disc", "quadruped": "box-shaped animal stand-in",
            "small": "ball stand-in"}
    return "; ".join(f"the {c['color_name']} {what.get(c['stand_in'], 'stand-in')} = {c['name']}"
                     for c in cast_info(store, project_id, shot))


# --------------------------------------------------------------------------- #
# spec: чистка и раскладка по state (без Gemini)
# --------------------------------------------------------------------------- #

_FACING_WORDS = (
    (("away", "back", "спин", "от камер"), 180.0),
    (("left", "влев", "налев", "слева"), -90.0),
    (("right", "вправ", "направ", "справа"), 90.0),
    (("camera", "камер", "зрител", "front", "вперёд", "вперед"), 0.0),
)


def facing_deg(value: Any) -> float:
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value) % 360 if abs(float(value)) > 360 else float(value)
    text = str(value or "").casefold()
    for words, deg in _FACING_WORDS:
        if any(w in text for w in words):
            return deg
    try:
        return float(text)
    except ValueError:
        return 0.0


def pose_of(value: Any) -> str:
    text = str(value or "").casefold()
    if text in POSES:
        return text
    for words, pose in ((("lie", "lying", "лёж", "леж", "лёг", "лег"), "lying"),
                        (("kneel", "колен", "присел", "присев", "squat", "корточ"), "kneeling"),
                        (("sit", "сид", "сел"), "sitting"), (("walk", "ид", "шаг"), "walking")):
        if any(w in text for w in words):
            return pose
    return "standing"


def _position(text: Any, n: int, total: int) -> list[float]:
    t = str(text or "").casefold()
    x = (n - (total - 1) / 2) * 90.0
    if any(w in t for w in ("left", "слева", "лев")):
        x = -120.0
    elif any(w in t for w in ("right", "справа", "прав")):
        x = 120.0
    elif any(w in t for w in ("center", "centre", "центр", "middle", "посередин")):
        x = 0.0
    z = 0.0
    if any(w in t for w in ("foreground", "передн", "front", "близко", "close")):
        z = 70.0
    elif any(w in t for w in ("background", "задн", "far", "далеко", "back")):
        z = -90.0
    return [x, z]


def _moment(state: dict, cast: list[dict]) -> dict:
    """state плана (или конца) → актёры и реквизит раскладки."""
    by_id = {c["id"]: c for c in (state or {}).get("characters") or []}
    actors = []
    for n, member in enumerate(cast):
        st = by_id.get(member["id"], {})
        pos = _position(st.get("position"), n, len(cast))
        actors.append({"id": member["id"], "pos": pos, "facing": facing_deg(st.get("facing") or "camera"),
                       "pose": pose_of(st.get("pose"))})
    taken = [a["pos"] for a in actors]
    for i, a in enumerate(actors):  # двое в одной точке — развести
        for prev in taken[:i]:
            if abs(prev[0] - a["pos"][0]) < 40 and abs(prev[1] - a["pos"][1]) < 40:
                a["pos"] = [a["pos"][0] + 80.0, a["pos"][1]]
    props = []
    for k, p in enumerate((state or {}).get("props") or []):
        holder = next((c["id"] for c in (state or {}).get("characters") or []
                       if p["name"].casefold() in str(c.get("holding") or "").casefold()), "")
        item = {"id": V.slugify(p["name"], f"prop{k}"), "name": p["name"], "kind": "disc", "size": 10,
                "count": int(p.get("count") or 1), "color": "#c98b3b"}
        if holder:
            item["holder"] = holder
        else:
            item["pos"] = [-30.0 + 30.0 * k, 0.0, 45.0]
        props.append(item)
    return {"actors": actors, "props": props}


def auto_spec(store: Any, project_id: int, shot: dict, nxt: dict | None) -> dict:
    """Раскладка без Gemini: места/позы/взгляд из state (старт) и конца плана."""
    cast = shot.get("cast") or []
    return {"room": dict(ROOM_DEFAULT), "objects": [],
            "start": _moment(shot.get("state") or {}, cast),
            "end": _moment(V.end_state_of(shot, nxt), cast)}


def _clean_props(items: Any, actor_ids: set[str]) -> list[dict]:
    out = []
    for k, p in enumerate(items if isinstance(items, list) else []):
        if not isinstance(p, dict):
            continue
        item = {"id": V.slugify(p.get("id") or p.get("name") or "", f"prop{k}"),
                "name": P._text(p.get("name") or p.get("id"), 60),
                "kind": p.get("kind") if p.get("kind") in KINDS else "disc",
                "size": _num(p.get("size"), 10, 1, 300), "count": int(_num(p.get("count"), 1, 1, 9)),
                "color": _color(p.get("color"), "#c98b3b")}
        holder = V.slugify(p.get("holder") or "", "")
        if holder and holder in actor_ids:
            item["holder"] = holder
        else:
            pos = p.get("pos") if isinstance(p.get("pos"), list) else [0, 0, 40]
            item["pos"] = [_num(v) for v in (list(pos) + [0, 0, 0])[:3]]
        out.append(item)
    return out[:12]


def clean_spec(raw: Any, cast_ids: list[str]) -> dict:
    """Чистка spec от Gemini/агента: числа в пределах, известные виды и позы, только персонажи плана."""
    raw = raw if isinstance(raw, dict) else {}
    room_raw = raw.get("room") if isinstance(raw.get("room"), dict) else {}
    room = {"width": _num(room_raw.get("width"), ROOM_DEFAULT["width"], 150, 2000),
            "depth": _num(room_raw.get("depth"), ROOM_DEFAULT["depth"], 150, 2000),
            "height": _num(room_raw.get("height"), ROOM_DEFAULT["height"], 150, 600),
            "floor": _color(room_raw.get("floor"), ROOM_DEFAULT["floor"]),
            "wall": _color(room_raw.get("wall"), ROOM_DEFAULT["wall"])}
    objects = []
    for k, o in enumerate(raw.get("objects") if isinstance(raw.get("objects"), list) else []):
        if not isinstance(o, dict):
            continue
        size = o.get("size")
        size = [_num(v, 60, 1, 800) for v in (list(size) + [60, 60, 60])[:3]] if isinstance(size, list) else \
            [_num(size, 60, 1, 800)] * 3
        pos = o.get("pos") if isinstance(o.get("pos"), list) else [0, 0]
        objects.append({"id": V.slugify(o.get("id") or o.get("name") or "", f"obj{k}"),
                        "kind": o.get("kind") if o.get("kind") in KINDS else "box", "size": size,
                        "pos": [_num(v) for v in (list(pos) + [0, 0])[:2]], "y": _num(o.get("y"), 0, 0, 400),
                        "rot": _num(o.get("rot"), 0, -360, 360), "color": _color(o.get("color"), "#8a7f72")})
    out: dict[str, Any] = {"room": room, "objects": objects[:24]}
    cam = raw.get("camera") if isinstance(raw.get("camera"), dict) else None
    if cam and isinstance(cam.get("pos"), list) and isinstance(cam.get("target"), list):
        out["camera"] = {"pos": [_num(v) for v in (list(cam["pos"]) + [0, 0, 0])[:3]],
                         "target": [_num(v) for v in (list(cam["target"]) + [0, 0, 0])[:3]],
                         "fov": _num(cam.get("fov"), 40, 15, 90)}
    ids = set(cast_ids)
    for which in ("start", "end"):
        moment = raw.get(which) if isinstance(raw.get(which), dict) else {}
        actors = []
        for a in moment.get("actors") if isinstance(moment.get("actors"), list) else []:
            if not isinstance(a, dict):
                continue
            aid = V.slugify(a.get("id") or "", "")
            if aid not in ids or aid in {x["id"] for x in actors}:
                continue
            pos = a.get("pos") if isinstance(a.get("pos"), list) else [0, 0]
            actors.append({"id": aid, "pos": [_num(v) for v in (list(pos) + [0, 0])[:2]],
                           "facing": facing_deg(a.get("facing")), "pose": pose_of(a.get("pose")),
                           **({"y": _num(a.get("y"), 0, 0, 300)} if a.get("y") not in (None, "") else {})})
        out[which] = {"actors": actors, "props": _clean_props(moment.get("props"), ids)}
    return out


# --------------------------------------------------------------------------- #
# spec → JSON-сцена движка
# --------------------------------------------------------------------------- #


def _rot_y(x: float, z: float, deg: float) -> tuple[float, float]:
    """Поворот вокруг y (как в движке: +z при повороте θ уходит в (sin θ, cos θ))."""
    r = math.radians(deg)
    return x * math.cos(r) + z * math.sin(r), -x * math.sin(r) + z * math.cos(r)


def _human(pose: str, color: str, seat: bool = True) -> list[dict]:
    skin, dark = "#f1d3b5", "#2b2b33"
    body: list[dict]
    if pose == "lying":
        body = [{"type": "cylinder", "radius": 17, "height": 140, "position": [0, 17, 0], "rotation": [0, 0, 90],
                 "color": color},
                {"type": "sphere", "radius": 13, "position": [82, 18, 0], "color": skin, "segments": 16},
                {"type": "box", "size": [6, 6, 10], "position": [84, 18, 13], "color": dark}]
        return body
    if pose == "sitting":
        hip = 45.0
        body = [{"type": "cylinder", "radiusTop": 17, "radiusBottom": 15, "height": 58, "position": [0, hip + 29, 0],
                 "color": color},
                {"type": "box", "size": [30, 14, 42], "position": [0, hip, 18], "color": color},
                {"type": "cylinder", "radius": 7, "height": hip, "position": [0, hip / 2, 38], "color": color},
                *([{"type": "box", "size": [38, hip - 7, 38], "position": [0, (hip - 7) / 2, 6], "color": "#8a7f72"}]
                  if seat else [])]  # табурет, если сидит не на мебели
        head_y = hip + 58 + 14
    elif pose == "kneeling":
        body = [{"type": "box", "size": [28, 12, 40], "position": [0, 6, -14], "color": color},
                {"type": "cylinder", "radius": 13, "height": 40, "position": [0, 26, 0], "color": color},
                {"type": "cylinder", "radiusTop": 17, "radiusBottom": 15, "height": 56, "position": [0, 74, 0],
                 "color": color}]
        head_y = 74 + 28 + 14
    else:  # standing / walking
        body = [{"type": "cylinder", "radiusTop": 18, "radiusBottom": 13, "height": 125, "position": [0, 62.5, 0],
                 "color": color}]
        if pose == "walking":
            body.append({"type": "box", "size": [10, 50, 12], "position": [-8, 25, 14], "rotation": [-25, 0, 0],
                         "color": color})
        head_y = 125 + 14
    body += [{"type": "sphere", "radius": 13, "position": [0, head_y, 0], "color": skin, "segments": 16},
             {"type": "box", "size": [6, 6, 10], "position": [0, head_y, 13], "color": dark}]  # «нос» — куда смотрит
    return body


def _actor_nodes(actor: dict, info: dict) -> dict:
    kind, color = info.get("stand_in", "human"), info.get("color", ACTOR_COLORS[0])
    if kind == "disc":
        children = [{"type": "cylinder", "radius": 17, "height": 8, "position": [0, 4, 0], "color": "#9aa6b2",
                     "segments": 32},
                    {"type": "cylinder", "radius": 15, "height": 1, "position": [0, 8.5, 0], "color": color,
                     "segments": 32},
                    {"type": "box", "size": [12, 3, 2], "position": [0, 6, 16.5], "color": "#22d3ee"}]
    elif kind == "quadruped":
        children = [{"type": "box", "size": [22, 18, 46], "position": [0, 24, 0], "color": color},
                    {"type": "sphere", "radius": 11, "position": [0, 36, 28], "color": color, "segments": 16},
                    {"type": "box", "size": [4, 4, 6], "position": [0, 36, 39], "color": "#2b2b33"}]
        children += [{"type": "cylinder", "radius": 3.5, "height": 16, "position": [sx * 8, 8, sz * 17],
                      "color": color, "segments": 8} for sx in (-1, 1) for sz in (-1, 1)]
    elif kind == "small":
        children = [{"type": "sphere", "radius": 14, "position": [0, 14, 0], "color": color, "segments": 16},
                    {"type": "box", "size": [4, 4, 6], "position": [0, 16, 14], "color": "#2b2b33"}]
    else:
        children = _human(actor.get("pose") or "standing", color, seat=not actor.get("y"))
    x, z = actor["pos"]
    y = float(actor.get("y") or 0.0)
    if y and kind == "human" and actor.get("pose") == "sitting":
        y = max(0.0, y - 45.0)  # y — высота сиденья: таз манекена на ней
    return {"type": "group", "position": [x, y, z],
            "rotation": [0, float(actor.get("facing") or 0.0), 0], "children": children}


def _prop_nodes(prop: dict, actors: dict[str, dict]) -> list[dict]:
    size = float(prop.get("size") or 10)
    if prop.get("holder") and prop["holder"] in actors:
        a = actors[prop["holder"]]
        dx, dz = _rot_y(0.0, 26.0, float(a.get("facing") or 0.0))
        base = [a["pos"][0] + dx, float(a.get("y") or 0.0) + (60.0 if a.get("pose") == "sitting" else 95.0),
                a["pos"][1] + dz]
    else:
        base = list(prop.get("pos") or [0, 0, 40])
    out = []
    for i in range(int(prop.get("count") or 1)):
        pos = [base[0] + i * (size * 1.4), base[1], base[2]]
        kind = prop.get("kind") or "disc"
        if kind == "sphere":
            out.append({"type": "sphere", "radius": size / 2, "position": [pos[0], pos[1] + size / 2, pos[2]],
                        "color": prop.get("color"), "segments": 12})
        elif kind == "box":
            out.append({"type": "box", "size": size, "position": [pos[0], pos[1] + size / 2, pos[2]],
                        "color": prop.get("color")})
        elif kind == "cylinder":
            out.append({"type": "cylinder", "radius": size / 3, "height": size, "position":
                        [pos[0], pos[1] + size / 2, pos[2]], "color": prop.get("color"), "segments": 12})
        else:
            out.append({"type": "cylinder", "radius": size / 2, "height": max(1.0, size / 6), "position":
                        [pos[0], pos[1] + max(1.0, size / 6) / 2, pos[2]], "color": prop.get("color"),
                        "segments": 16})
    return out


def auto_camera(shot: dict, actors: list[dict], infos: dict[str, dict]) -> dict:
    """Камера плана из крупности и ракурса: смотрит на центр персонажей, стоит перед ними."""
    cam = shot.get("camera") or {}
    size = str(cam.get("shot") or "medium")
    humans = [a for a in actors if infos.get(a["id"], {}).get("stand_in", "human") == "human"]
    dist = SHOT_DISTANCE.get(size, SHOT_DISTANCE["medium"]) * (1.0 if humans or not actors else 0.45)
    if actors:
        tx = sum(a["pos"][0] for a in actors) / len(actors)
        tz = sum(a["pos"][1] for a in actors) / len(actors)
    else:
        tx, tz = 0.0, 0.0
    ty = 95.0 if humans else 20.0
    if size in ("close", "extreme_close", "medium_close") and humans:
        ty = 125.0
    angle = str(cam.get("angle") or "eye").casefold()
    if "high" in angle or "top" in angle or "сверху" in angle or "bird" in angle:
        cy = ty + dist * 0.65
    elif "low" in angle or "снизу" in angle:
        cy = max(15.0, ty * 0.35)
    else:
        cy = ty + (20.0 if humans else 25.0)
    return {"pos": [tx, round(cy, 1), round(tz + dist, 1)], "target": [tx, ty, tz], "fov": 40.0}


def compile_scene(spec: dict, which: str, shot: dict, infos: list[dict]) -> dict:
    """spec плана → JSON-сцена движка (кадр start или end), 9:16, toon-заливка, без текста."""
    room = spec.get("room") or ROOM_DEFAULT
    by_id = {i["id"]: i for i in infos}
    moment = spec.get(which) or spec.get("start") or {"actors": [], "props": []}
    actors = [a for a in moment.get("actors") or [] if a["id"] in by_id]
    cam = spec.get("camera") or auto_camera(shot, actors, by_id)
    w, d, h = float(room["width"]), float(room["depth"]), float(room["height"])
    nodes: list[dict] = [
        {"type": "plane", "size": [w, d], "rotation": [-90, 0, 0], "position": [0, 0, 0], "color": room["floor"],
         "order": -2, "material": {"shading": "lambert"}},
        {"type": "plane", "size": [w, h], "position": [0, h / 2, -d / 2], "color": room["wall"], "order": -3,
         "material": {"shading": "lambert"}},
        {"type": "plane", "size": [d, h], "position": [-w / 2, h / 2, 0], "rotation": [0, 90, 0],
         "color": room["wall"], "order": -3, "material": {"shading": "lambert", "doubleSided": True}},
        {"type": "plane", "size": [d, h], "position": [w / 2, h / 2, 0], "rotation": [0, -90, 0],
         "color": room["wall"], "order": -3, "material": {"shading": "lambert", "doubleSided": True}},
    ]
    for o in spec.get("objects") or []:
        sx, sy, sz = o["size"]
        y0 = float(o.get("y") or 0.0)
        if o["kind"] in ("cylinder", "disc"):
            node = {"type": "cylinder", "radius": sx / 2, "height": sy, "segments": 24}
        elif o["kind"] == "sphere":
            node = {"type": "sphere", "radius": sx / 2, "segments": 16}
        else:
            node = {"type": "box", "size": [sx, sy, sz]}
        node.update({"position": [o["pos"][0], y0 + sy / 2, o["pos"][1]], "rotation": [0, o.get("rot") or 0, 0],
                     "color": o.get("color") or "#8a7f72", "sort": "object"})
        nodes.append(node)
    for a in actors:
        nodes.append({**_actor_nodes(a, by_id[a["id"]]), "sort": "object"})
    placed = {a["id"]: a for a in actors}
    for p in moment.get("props") or []:
        nodes += _prop_nodes(p, placed)
    return {
        "camera": {"type": "perspective", "position": list(cam["pos"]), "target": list(cam["target"]),
                   "fov": float(cam.get("fov") or 40), "near": 5, "far": 6000},
        "lights": [{"type": "hemisphere", "sky": "#ffffff", "ground": "#8a7f72", "intensity": 0.55},
                   {"type": "directional", "direction": [-0.6, 1, 0.8], "intensity": 0.75}],
        "background": {"gradient": ["#cfc7bb", "#efe9df"], "angle": 90},
        "style": {"shading": "toon", "bands": 3, "outline": {"color": "#2a2a30", "width": 3}},
        "nodes": nodes,
    }


def scene_key(scene: dict) -> str:
    return hashlib.sha1(json.dumps(scene, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def state_key(shot: dict, nxt: dict | None) -> str:
    """По чему писалась раскладка: правка этого меняет авто-/Gemini-раскладку (своя — остаётся)."""
    data = [shot.get("cast"), shot.get("camera"), shot.get("location"), shot.get("action"), shot.get("state"),
            V.end_state_of(shot, nxt)]
    return hashlib.sha1(json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# Хранение, Gemini, рендер
# --------------------------------------------------------------------------- #


def _neighbors(store: Any, shot: dict) -> tuple[list[dict], int]:
    shots = V.list_shots(store, int(shot["script_id"]))
    ids = [int(s["id"]) for s in shots]
    return shots, ids.index(int(shot["id"]))


def _project_of(store: Any, shot: dict) -> int:
    return int(V.get_script(store, int(shot["script_id"]), with_shots=False)["project_id"])


def _save(store: Any, shot_id: int, data: dict) -> None:
    store.db.execute("UPDATE aiv_shots SET blocking_json = ? WHERE id = ?",
                     (json.dumps(data, ensure_ascii=False), int(shot_id)))


def current(store: Any, shot: dict) -> dict:
    """Действующая раскладка плана: сохранённая (если не устарела по state) или авто по state."""
    shots, n = _neighbors(store, shot)
    nxt = shots[n + 1] if n + 1 < len(shots) else None
    data = shot.get("blocking") or {}
    key = state_key(shot, nxt)
    if data.get("spec") and (data.get("source") in ("agent", "owner") or data.get("state_key") == key):
        return data
    return {"spec": auto_spec(store, _project_of(store, shot), shot, nxt), "source": "auto", "state_key": key,
            "scenes": {}, "outdated": bool(data.get("spec"))}


def scenes_of(store: Any, shot: dict) -> dict[str, dict]:
    """Готовые JSON-сцены движка {start, end} (end — только у плана с видео)."""
    data = current(store, shot)
    infos = cast_info(store, _project_of(store, shot), shot)
    spec = clean_spec(data["spec"], [c["id"] for c in shot.get("cast") or []])
    out = {}
    for which in ("start", "end") if shot.get("needs_motion") else ("start",):
        override = (data.get("scenes") or {}).get(which)
        out[which] = override if isinstance(override, dict) and override.get("nodes") else \
            compile_scene(spec, which, shot, infos)
    return out


def set_blocking(store: Any, shot_id: int, spec: Any = None, scenes: Any = None, source: str = "agent") -> dict:
    """Своя раскладка плана (агент/владелец): spec и/или готовые сцены движка {start?, end?}."""
    from app import motion
    from app import scene_engine as se

    shot = V.get_shot(store, shot_id)
    data = dict(shot.get("blocking") or current(store, shot))
    if spec is not None:
        if isinstance(spec, str):
            try:
                spec = json.loads(spec)
            except ValueError as exc:
                raise V.AivError(f"spec — не JSON: {exc}") from exc
        data["spec"] = clean_spec(spec, [c["id"] for c in shot.get("cast") or []])
    if scenes is not None:
        if not isinstance(scenes, dict):
            raise V.AivError("scenes — {start?: сцена, end?: сцена} (JSON-сцены движка, get_scene_reference)")
        clean = {}
        for which in ("start", "end"):
            if isinstance(scenes.get(which), dict):
                problems: list[str] = []
                clean[which] = se.SceneSpec().norm(scenes[which], motion._Report(problems), f"scenes.{which}")
                if problems:
                    raise V.AivError("; ".join(problems[:5]))
        data["scenes"] = clean
    data["source"] = source
    _save(store, shot_id, data)
    store.db.execute("UPDATE aiv_refs SET stale = 1 WHERE target_type = 'shot' AND target_id = ? "
                     "AND kind IN ('blocking_start', 'blocking_end')", (int(shot_id),))
    return view(store, V.get_shot(store, shot_id))


def view(store: Any, shot: dict) -> dict:
    data = current(store, shot)
    project_id = _project_of(store, shot)
    return {"shot_id": int(shot["id"]), "idx": shot["idx"], "source": data.get("source"),
            "outdated": bool(data.get("outdated")), "spec": data["spec"], "scenes_override": data.get("scenes") or {},
            "legend": legend(store, project_id, shot), "cast": cast_info(store, project_id, shot),
            "frames": {w: V.selected_ref(store, project_id, "shot", int(shot["id"]), f"blocking_{w}")
                       for w in ("start", "end")}}


BLOCKING_PROMPT = """\
Ты постановщик мультсериала и раскладываешь планы серии в простой 3D-макет (блокинг) для
картинковой модели: она перерисует скриншот макета в кадр, сохранив композицию, позы и места.
Единицы — сантиметры. Оси: x вправо (от камеры), y вверх, z к камере; пол y=0; комната по центру
(0,0), задняя стена z = -depth/2. Для каждой локации — комната (width, depth, height) и мебель
простыми фигурами (kind box|cylinder|disc|sphere, size [ширина, высота, глубина], pos [x, z], rot —
градусы вокруг вертикали): кровать, стол, диван, холодильник… Одна и та же мебель на тех же местах
во всех планах локации. Для каждого плана — start (момент начала) и end (момент конца плана):
actors — каждый персонаж плана: pos [x, z], facing (градусы: 0 — лицом в камеру, 90 — вправо кадра,
-90 — влево, 180 — спиной), pose standing|walking|sitting|kneeling|lying, y — высота, если сидит/лежит
на мебели; props — ключевой реквизит (kind, size, count — сколько штук, pos [x, y, z] или holder — у
кого в руке). Состояния бери из state (начало) и end_state (конец) плана; конец плана, который
продолжает следующий, = старт следующего (те же места и позы). Робот-пылесос и другие «предметные»
персонажи — всегда на полу. Камеру не пиши — её ставит сервис по крупности плана.
Верни ТОЛЬКО JSON по схеме."""


def _schema() -> dict:
    num = {"type": "number"}
    vec = {"type": "array", "items": num}
    actor = {"type": "object", "properties": {"id": {"type": "string"}, "pos": vec, "facing": num,
                                              "pose": {"type": "string"}, "y": num},
             "required": ["id", "pos", "facing", "pose"]}
    prop = {"type": "object", "properties": {"id": {"type": "string"}, "kind": {"type": "string"}, "size": num,
                                             "count": {"type": "integer"}, "pos": vec, "holder": {"type": "string"},
                                             "color": {"type": "string"}}, "required": ["id"]}
    moment = {"type": "object", "properties": {"actors": {"type": "array", "items": actor},
                                               "props": {"type": "array", "items": prop}}, "required": ["actors"]}
    obj = {"type": "object", "properties": {"id": {"type": "string"}, "kind": {"type": "string"}, "size": vec,
                                            "pos": vec, "rot": num, "color": {"type": "string"}},
           "required": ["id", "kind", "size", "pos"]}
    room = {"type": "object", "properties": {"width": num, "depth": num, "height": num,
                                             "floor": {"type": "string"}, "wall": {"type": "string"}}}
    return {"type": "object", "properties": {
        "locations": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "room": room, "objects": {"type": "array", "items": obj}},
            "required": ["id", "objects"]}},
        "shots": {"type": "array", "items": {"type": "object", "properties": {
            "idx": {"type": "integer"}, "start": moment, "end": moment}, "required": ["idx", "start", "end"]}}},
        "required": ["shots"]}


def write_blocking(store: Any, script_id: int, client: Any = None, only: set[int] | None = None) -> dict:
    """Раскладка серии: Gemini одним запросом (бесплатно); нет ключа или ошибка — авто по state.
    ``only`` — id планов, которым писать (остальные не трогаются); своя раскладка (agent) не трогается."""
    from app.settings import settings

    script = V.get_script(store, script_id)
    project_id = int(script["project_id"])
    shots = script["shots"]
    todo = [s for s in shots if (only is None or int(s["id"]) in only)
            and (s.get("blocking") or {}).get("source") not in ("agent", "owner")]
    if not todo:
        return {"ok": True, "source": "kept", "shots": []}
    data: dict = {}
    source, note = "auto", ""
    if client is not None or settings.gemini_api_key:
        try:
            data = _ask_gemini(store, script, client)
            source = "gemini"
        except Exception as exc:  # noqa: BLE001 - Gemini недоступен: раскладка по state
            note = f"Gemini не ответил ({type(exc).__name__}: {str(exc)[:160]}) — раскладка по state"
    rooms = {str(l.get("id") or ""): l for l in data.get("locations") or [] if isinstance(l, dict)}
    by_idx = {int(s.get("idx") or 0): s for s in data.get("shots") or [] if isinstance(s, dict)}
    written = []
    for shot in todo:
        n = next(i for i, s in enumerate(shots) if s["id"] == shot["id"])
        nxt = shots[n + 1] if n + 1 < len(shots) else None
        auto = auto_spec(store, project_id, shot, nxt)
        got = by_idx.get(int(shot["idx"]))
        if got and source == "gemini":
            loc = rooms.get(shot.get("location") or "", {})
            raw = {"room": loc.get("room") or auto["room"], "objects": loc.get("objects") or [],
                   "start": got.get("start") or auto["start"], "end": got.get("end") or auto["end"]}
            spec, src = clean_spec(raw, [c["id"] for c in shot.get("cast") or []]), "gemini"
            for which in ("start", "end"):  # Gemini забыл персонажа — ставим по state
                have = {a["id"] for a in spec[which]["actors"]}
                spec[which]["actors"] += [a for a in auto[which]["actors"] if a["id"] not in have]
        else:
            spec, src = auto, "auto"
        _save(store, int(shot["id"]), {"spec": spec, "source": src, "state_key": state_key(shot, nxt), "scenes": {}})
        written.append({"idx": shot["idx"], "source": src})
    return {"ok": True, "source": source, "shots": written, **({"note": note} if note else {})}


def _ask_gemini(store: Any, script: dict, client: Any = None) -> dict:
    from app.ai.gemini import GeminiClient, _extract_response_text
    from app.settings import settings

    project_id = int(script["project_id"])
    shots = script["shots"]
    context = {
        "characters": [{"id": c["slug"], "name": c["name"], "stand_in": stand_in(c),
                        "identity_card": c["identity_card"], **({"constraints": c["constraints"]}
                                                                  if c.get("constraints") else {})}
                       for c in V.list_characters(store, project_id)],
        "locations": [{"id": l["slug"], "name": l["name"], "description": l["description"]}
                      for l in V.list_locations(store, project_id)],
        "shots": [{"idx": s["idx"], "location": s["location"], "camera": s["camera"], "action": s["action"],
                   "cast": [c["id"] for c in s["cast"]], "continues_from_prev": s["continues_from_prev"],
                   "state": s.get("state") or {}, "end_state": V.end_state_of(s, shots[n + 1] if n + 1 < len(shots)
                                                                             else None)}
                  for n, s in enumerate(shots)],
    }
    payload = {"contents": [{"role": "user", "parts": [{"text": BLOCKING_PROMPT},
                                                       {"text": json.dumps(context, ensure_ascii=False)}]}],
               "generationConfig": {"temperature": 0.3, "responseMimeType": "application/json",
                                    "responseSchema": _schema()}}
    response = (client or GeminiClient(timeout=90)).generate_content(settings.gemini_text_model, payload)
    data = json.loads(_extract_response_text(response))
    if not isinstance(data, dict):
        raise V.AivError("Gemini вернул не объект")
    return data


def render(store: Any, shot_id: int, which: str, job: dict | None = None) -> dict:
    """Скриншот блокинга (9:16, 0 ₽) → версия ``blocking_<which>`` плана. Тот же кадр уже есть — он."""
    from uuid import uuid4

    from app import scene_engine as se
    from app.montage_assets import assets_dir, probe_asset

    shot = V.get_shot(store, shot_id)
    project_id = _project_of(store, shot)
    scenes = scenes_of(store, shot)
    if which not in scenes:
        raise V.AivError(f"план {shot['idx']}: блокинга «{which}» нет (конец — только у плана с видео)")
    scene = scenes[which]
    marker = f"blocking:{scene_key(scene)}"
    kind = f"blocking_{which}"
    have = V.selected_ref(store, project_id, "shot", int(shot_id), kind)
    if have and have.get("prompt") == marker and have.get("asset_id"):
        if have["stale"]:
            store.db.execute("UPDATE aiv_refs SET stale = 0 WHERE id = ?", (int(have["id"]),))
        return V.get_ref(store, int(have["id"]))
    frames, _info = se.render_scene_png(store, scene, [0.0], w=BLOCKING_W, h=BLOCKING_H, background=None,
                                        width=RENDER_WIDTH)
    dest = assets_dir() / f"{uuid4().hex}-aiv-{kind}.png"
    dest.write_bytes(frames[0])
    meta = probe_asset(dest)
    asset = store.create_montage_asset(
        kind="image", label=f"ИИ-видео: план {shot['idx']} — блокинг {which}"[:120],
        description=f"ИИ-видео: блокинг плана {shot['idx']} ({which}) — 3D-макет композиции"[:500],
        tags="ии-видео", file_path=str(dest), original_filename=dest.name, mime_type="image/png",
        size_bytes=dest.stat().st_size, **meta)
    return V.add_ref(store, project_id, "shot", int(shot_id), kind, int(asset["id"]), prompt=marker,
                     job_id=(job or {}).get("id"))


def needs_render(store: Any, project_id: int, shot: dict) -> list[str]:
    """Какие кадры блокинга плана надо (пере)рисовать: нет, устарел или сцена другая."""
    out = []
    for which, scene in scenes_of(store, shot).items():
        ref = V.selected_ref(store, project_id, "shot", int(shot["id"]), f"blocking_{which}")
        if not ref or ref["stale"] or not ref.get("asset_id") or ref.get("prompt") != f"blocking:{scene_key(scene)}":
            out.append(which)
    return out
