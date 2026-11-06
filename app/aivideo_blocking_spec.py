"""Блокинг: описание сцены — рост и поза, расстановка из реплики, чистка спецификации (из ``app/aivideo_blocking.py``)."""

from __future__ import annotations

import math
import re
from typing import Any

from app import aivideo as V
from app import aivideo_layout as L
from app import projects as P

KINDS = ("box", "cylinder", "disc", "sphere")
POSES = ("standing", "walking", "sitting", "crouching", "kneeling", "lying")
ARMS = ("down", "up", "forward", "holding", "wave")
# Нейтральные, но различимые цвета манекенов: яркий оранжевый модель перерисовывала в «дверь».
ACTOR_COLORS = ("#8e9bb5", "#c2a98a", "#8fae95", "#b08fa8")
COLOR_NAMES = {"#8e9bb5": "slate-blue", "#c2a98a": "sand", "#8fae95": "sage-green", "#b08fa8": "mauve"}
ROOM_DEFAULT = L.ROOM_DEFAULT


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
    return L._hex(value, default)


def _norm(text: Any) -> str:
    return str(text or "").casefold().replace("ё", "е")


# --------------------------------------------------------------------------- #
# Персонажи: манекен, рост, цвет
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


def body_size(character: dict | None, kind: str) -> float:
    """Реальный размер манекена, см: человек — рост, диск — диаметр, животное — длина, шар — диаметр."""
    text = _norm(f"{(character or {}).get('identity_card') or ''} {(character or {}).get('constraints') or ''}")
    if kind == "human":
        m = re.search(r"рост\D{0,4}(1\d\d|2[01]\d)", text) or re.search(r"(1\d\d|2[01]\d)\s*см", text)
        if m:
            return float(m.group(1))
        if re.search(r"ребен|ребён|девочк|мальчик|child|\bkid", text):
            return 125.0
        if re.search(r"высок|tall", text):
            return 178.0
        if re.search(r"низк|миниатюрн|short|petite", text):
            return 155.0
        return 165.0
    if kind == "disc":
        m = re.search(r"диаметр\D{0,4}(\d{2})", text)
        if m:
            return float(m.group(1))
        if re.search(r"тарелк|plate", text):
            return 28.0
        return 33.0
    if kind == "quadruped":
        if re.search(r"собак|пес\b|пёс|dog", text):
            return 75.0
        if re.search(r"хомяк|мыш|hamster", text):
            return 14.0
        return 46.0
    return 24.0


def cast_info(store: Any, project_id: int, shot: dict) -> list[dict]:
    """Персонажи плана: id, имя, манекен, реальный размер, цвет манекена (по порядку в кадре)."""
    out = []
    for n, member in enumerate(shot.get("cast") or []):
        c = V.character_by_slug(store, project_id, member["id"])
        color = ACTOR_COLORS[n % len(ACTOR_COLORS)]
        kind = stand_in(c)
        out.append({"id": member["id"], "name": (c or {}).get("name") or member["id"], "stand_in": kind,
                    "size": body_size(c, kind), "color": color, "color_name": COLOR_NAMES[color]})
    return out


# --------------------------------------------------------------------------- #
# spec: чистка и раскладка по state (без Gemini)
# --------------------------------------------------------------------------- #

_FACING_WORDS = (
    (("away", "back", "спин", "от камер", "к окну"), 180.0),
    (("left", "влев", "налев", "слева"), -90.0),
    (("right", "вправ", "направ", "справа"), 90.0),
    (("camera", "камер", "зрител", "front", "вперёд", "вперед"), 0.0),
)
_NOTHING = re.compile(r"^\s*(ничего|nothing|none|нет|пусто|-|—|empty)?\s*$")


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
                        (("присел", "присев", "присела", "squat", "корточ", "crouch"), "crouching"),
                        (("kneel", "колен"), "kneeling"),
                        (("sit", "сид", "сел"), "sitting"), (("walk", "ид", "шаг"), "walking")):
        if any(w in text for w in words):
            return pose
    return "standing"


def arms_of(value: Any, default: str = "down") -> str:
    text = _norm(value)
    if text in ARMS:
        return text
    if re.search(r"рук\w* (вверх|над голов)|поднима\w* рук|подня\w* рук|потяг|потянул|stretch|arms? up|hands up",
                 text):
        return "up"
    if re.search(r"маш\w* рук|wave|машет", text):
        return "wave"
    if re.search(r"рук\w* вперед|протягива|reach|arms? forward", text):
        return "forward"
    return default


def holding_text(value: Any) -> str:
    text = str(value or "").strip()
    return "" if _NOTHING.match(_norm(text)) else text


def _position(text: Any, n: int, total: int, room: dict) -> list[float]:
    t = str(text or "").casefold()
    side = min(95.0, float(room["width"]) * 0.22)
    x = (n - (total - 1) / 2) * 70.0
    if any(w in t for w in ("left", "слева", "лев")):
        x = -side
    elif any(w in t for w in ("right", "справа", "прав")):
        x = side
    elif any(w in t for w in ("center", "centre", "центр", "middle", "посередин")):
        x = 0.0
    z = 0.0
    if any(w in t for w in ("foreground", "передн", "front", "близко", "close")):
        z = float(room["depth"]) * 0.2
    elif any(w in t for w in ("background", "задн", "far", "далеко", "back")):
        z = -float(room["depth"]) * 0.2
    return [x, z]


_ON_WORDS = re.compile(r"\bна\b|\bon\b|\bв кроват|\bin bed|\bлежит в|\bсидит в")
_HOLD_WORDS = re.compile(r"в рук|держ|несет|несёт|hold|carr|in (her|his|their) hand")
_STRETCH = re.compile(r"потяг|потянут|stretch|рук\w* (вверх|над голов)|поднима\w* рук")

# Реквизит по названию: вид (форма), размер см [ш, в, г] и цвет — в реальных пропорциях.
PROP_KINDS: list[tuple[str, str, list[float], str]] = [
    (r"кружк|чашк|\bmug|\bcup|кофе|\bчай|coffee|\btea\b", "mug", [8.5, 9.5, 8.5], "#f3efe8"),
    (r"носк|носок|sock", "sock", [7, 3, 18], "#f4b6c2"),
    (r"книг|\bbook|тетрад|журнал", "book", [15, 3, 22], "#7a6ff0"),
    (r"телефон|смартфон|phone", "phone", [7, 1, 15], "#26272b"),
    (r"тарелк|\bplate", "plate", [22, 2, 22], "#f4f1ea"),
    (r"печень|cookie|пирож|конфет", "cookie", [7, 1.2, 7], "#c99a5b"),
    (r"бутыл|bottle", "bottle", [7, 24, 7], "#5f9b5a"),
    (r"стакан|\bglass", "glass", [7, 11, 7], "#bfe0f0"),
    (r"подушк|pillow", "pillow", [45, 12, 32], "#f6f1ea"),
    (r"плед|одеял|blanket", "blanket", [100, 3, 80], "#c9b8e8"),
    (r"коробк|посылк|\bbox", "box", [30, 20, 25], "#c9a26b"),
    (r"мяч|\bball", "ball", [20, 20, 20], "#d9534f"),
    (r"игрушк|\btoy|плюш", "toy", [18, 18, 18], "#e8c9a8"),
    (r"сумк|рюкзак|\bbag", "bag", [30, 30, 14], "#8b5a3c"),
    (r"письм|бумаг|\bлист|записк|letter|paper|\bnote", "paper", [21, 0.4, 29.7], "#fbfaf6"),
    (r"ключ|\bkey", "key", [6, 0.6, 2.5], "#d8c070"),
    (r"пульт|remote", "remote", [5, 2, 18], "#2a2b30"),
    (r"ноутбук|laptop", "laptop", [32, 2, 22], "#8a8f96"),
    (r"цвет(ок|ы)|букет|flower", "flower", [8, 30, 8], "#e06c8c"),
]


def prop_kind(name: Any) -> tuple[str, list[float], str]:
    text = _norm(name)
    for words, kind, size, color in PROP_KINDS:
        if re.search(words, text):
            return kind, list(size), color
    return "item", [10, 10, 10], "#c98b3b"


def _who(text: str, cast: list[dict], infos: dict[str, dict]) -> str:
    """Чей реквизит по тексту («в руках Миры», «Mira holds…»): id персонажа или ""."""
    t = _norm(text)
    for c in cast:
        name = _norm(infos.get(c["id"], {}).get("name") or c["id"])
        stem = name[:max(3, len(name) - 1)]
        if stem and (stem in t or _norm(c["id"]) in t):
            return c["id"]
    return ""


def _moment(state: dict, cast: list[dict], infos: dict[str, dict], layout: dict, action: str = "",
            end: bool = False) -> dict:
    """state плана (или конца) → актёры и реквизит раскладки, привязанные к вещам комнаты."""
    room = layout.get("room") or ROOM_DEFAULT
    by_id = {c["id"]: c for c in (state or {}).get("characters") or []}
    actors = []
    stretch = bool(end and _STRETCH.search(_norm(action)))
    for n, member in enumerate(cast):
        st = by_id.get(member["id"], {})
        kind = infos.get(member["id"], {}).get("stand_in", "human")
        pose = pose_of(st.get("pose"))
        holding = holding_text(st.get("holding"))
        arms = "holding" if holding else arms_of(f"{st.get('pose') or ''} {member.get('action') or ''}"
                                                 if not end else f"{st.get('pose') or ''}")
        if stretch and not holding and kind == "human" and n == 0:
            arms = "up"
        text = f"{st.get('position') or ''} {st.get('pose') or ''}"
        pos = _position(st.get("position"), n, len(cast), room)
        actor = {"id": member["id"], "pos": pos, "facing": facing_deg(st.get("facing") or "camera"), "pose": pose,
                 "arms": arms if kind == "human" else "down"}
        anchor = L.find(layout, text)
        if anchor is None and end:
            # Конец плана: вещь из действия персонажа («стукается бампером о тумбочку») — он у неё.
            anchor = L.find(layout, member.get("action") or "")
            if anchor is not None and anchor.get("type") in L.FLAT_TYPES:
                anchor = None
        if anchor is not None and anchor.get("type") not in ("lamp", "books", "computer", "plush", "garland"):
            actor["_anchored"] = True
            on = (bool(_ON_WORDS.search(_norm(text))) or pose in ("lying", "sitting")) and \
                anchor["type"] in ("bed", "sofa", "chair", "rug", "table", "desk")
            if on and anchor["type"] != "rug":
                actor["on"] = anchor["id"]
                actor["pos"] = list(anchor["pos"])
            else:
                actor["pos"] = _in_front(anchor, 35.0 if kind == "human" else 25.0)
        actors.append(actor)
    props = []
    seen_kinds = set()
    for k, p in enumerate((state or {}).get("props") or []):
        name = p.get("name") or f"prop{k}"
        pk, _size, _col = prop_kind(name)
        seen_kinds.add(pk)
        item = {"id": V.slugify(name, f"prop{k}"), "name": name, "count": int(p.get("count") or 1)}
        where = f"{p.get('state') or ''}"
        holder = next((c["id"] for c in (state or {}).get("characters") or []
                       if _norm(name)[:5] and _norm(name)[:5] in _norm(holding_text(c.get("holding")))), "")
        if not holder and _HOLD_WORDS.search(_norm(where)):
            holder = _who(where, cast, infos) or next((c["id"] for c in cast if infos.get(c["id"], {}).get(
                "stand_in", "human") == "human"), "")
        support = None if holder else L.find(layout, where)
        if holder:
            item["holder"] = holder
        elif support is not None:
            item["on"] = support["id"]
        else:
            first = actors[0]["pos"] if actors else [0.0, 0.0]
            item["pos"] = [first[0] + 30.0 + 20.0 * k, 0.0, first[1] + 25.0]
        props.append(item)
    # Реквизит из действия плана, которого нет в state (кружка в «ставит кружку на тумбочку»).
    for words, pk, _size, _col in PROP_KINDS:
        if pk in seen_kinds or not re.search(words, _norm(action)):
            continue
        seen_kinds.add(pk)
        item = {"id": pk, "name": pk, "count": 1}
        support = L.find(layout, action) if end else None
        holder = next((a["id"] for a in actors if a.get("arms") == "holding"), "")
        if support is not None and support.get("type") not in ("rug",):
            item["on"] = support["id"]
        elif holder or not end:
            human = holder or next((c["id"] for c in cast if infos.get(c["id"], {}).get("stand_in", "human")
                                    == "human"), "")
            if not human:
                continue
            item["holder"] = human
            for a in actors:
                if a["id"] == human and a["arms"] in ("down", "forward"):
                    a["arms"] = "holding"
        else:
            continue
        props.append(item)
    return {"actors": actors, "props": props}


def _in_front(o: dict, gap: float) -> list[float]:
    """Точка перед вещью (со стороны её лица) на расстоянии ``gap`` от края."""
    dx, dz = _rot_y(0.0, float(o["size"][2]) / 2 + gap, float(o.get("rot") or 0.0))
    return [float(o["pos"][0]) + dx, float(o["pos"][1]) + dz]


def _clean_props(items: Any, actor_ids: set[str]) -> list[dict]:
    out = []
    for k, p in enumerate(items if isinstance(items, list) else []):
        if not isinstance(p, dict):
            continue
        item = {"id": V.slugify(p.get("id") or p.get("name") or "", f"prop{k}"),
                "name": P._text(p.get("name") or p.get("id"), 60),
                "count": int(_num(p.get("count"), 1, 1, 9))}
        if p.get("kind") in KINDS:
            item["kind"] = p["kind"]
        if p.get("size") not in (None, ""):
            item["size"] = _num(p.get("size") if not isinstance(p.get("size"), list) else p["size"][0], 10, 1, 300)
        if p.get("color"):
            item["color"] = _color(p.get("color"), "#c98b3b")
        holder = V.slugify(p.get("holder") or "", "")
        if holder and holder in actor_ids:
            item["holder"] = holder
        elif p.get("on"):
            item["on"] = V.slugify(str(p["on"]), "")[:40]
        else:
            pos = p.get("pos") if isinstance(p.get("pos"), list) else [0, 0, 40]
            item["pos"] = [_num(v) for v in (list(pos) + [0, 0, 0])[:3]]
        out.append(item)
    return out[:12]


def clean_spec(raw: Any, cast_ids: list[str]) -> dict:
    """Чистка spec от Gemini/агента: числа в пределах, известные позы, только персонажи плана.
    Пустые ``objects`` — комната локации (одна на все планы)."""
    raw = raw if isinstance(raw, dict) else {}
    room_raw = raw.get("room") if isinstance(raw.get("room"), dict) else {}
    room = {}
    if room_raw:
        room = {"width": _num(room_raw.get("width"), ROOM_DEFAULT["width"], 150, 2000),
                "depth": _num(room_raw.get("depth"), ROOM_DEFAULT["depth"], 150, 2000),
                "height": _num(room_raw.get("height"), ROOM_DEFAULT["height"], 150, 600),
                "floor": _color(room_raw.get("floor"), ROOM_DEFAULT["floor"]),
                "wall": _color(room_raw.get("wall"), ROOM_DEFAULT["wall"])}
    objects = [o for o in (L.clean_object(o, k) for k, o in
                           enumerate(raw.get("objects") if isinstance(raw.get("objects"), list) else [])) if o]
    out: dict[str, Any] = {"room": room, "objects": objects[:40]}
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
            item = {"id": aid, "pos": [_num(v) for v in (list(pos) + [0, 0])[:2]],
                    "facing": facing_deg(a.get("facing")), "pose": pose_of(a.get("pose")),
                    "arms": arms_of(a.get("arms"))}
            if a.get("y") not in (None, ""):
                item["y"] = _num(a.get("y"), 0, 0, 300)
            if a.get("on"):
                item["on"] = V.slugify(str(a["on"]), "")[:40]
            actors.append(item)
        out[which] = {"actors": actors, "props": _clean_props(moment.get("props"), ids)}
    return out


# --------------------------------------------------------------------------- #
# Геометрия: повороты, манекены, реквизит
# --------------------------------------------------------------------------- #


def _rot_y(x: float, z: float, deg: float) -> tuple[float, float]:
    """Поворот вокруг y (как в движке: +z при повороте θ уходит в (sin θ, cos θ))."""
    r = math.radians(deg)
    return x * math.cos(r) + z * math.sin(r), -x * math.sin(r) + z * math.cos(r)
