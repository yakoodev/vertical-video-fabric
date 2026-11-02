"""ИИ-видео: подробная раскладка локации для 3D-блокинга (одна на локацию, общая для всех планов).

Зачем: блокинг (``app/aivideo_blocking.py``) — макет кадра для картинковой модели. Пустая коробка с
манекенами-столбами ей не помогает (она перерисовывает их в «двери» и «ящики»), а комната, которая в
каждом плане своя, «плывёт». Поэтому у локации ОДНА раскладка: все вещи из её описания (кровать с
подушкой и пледом, тумбочка, лампа, окно со шторами, стол, стул, полки, ковёр, носки на полу…) простыми
фигурами в узнаваемых пропорциях и цветах, на своих местах — и все планы локации снимают её.

* **Источник списка вещей** — описание локации (словарь ``CATALOG``: слова → тип вещи, размер, цвет,
  место у стены). Если есть плита локации и ключ Gemini — один раз на локацию (бесплатная модель)
  Gemini-зрение разбирает плиту в список предметов с положением и цветом (``vision_layout``), и
  раскладка повторяет плиту; вещи из описания, которых зрение не назвало, добавляются по словарю.
* **Кэш** — ``aiv_locations.layout_json`` с ключом (описание + версия плиты + ``LAYOUT_VERSION``):
  правка описания или новая плита — раскладка пересчитывается, иначе одна и та же.
* **Единицы** — сантиметры; x вправо, y вверх, z к камере; пол y=0, комната по центру, задняя стена
  z = −depth/2. ``rot`` — градусы вокруг вертикали, у вещи «лицо» смотрит в +z при rot 0 (у левой
  стены rot 90, у правой −90).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

LAYOUT_VERSION = 1
ROOM_DEFAULT = {"width": 440, "depth": 420, "height": 260, "floor": "#b58a62", "wall": "#e9dfd0"}

# Цветовые слова описания → цвет (родная палитра плиты ближе, чем серые коробки).
COLOR_WORDS: list[tuple[str, str]] = [
    ("лавандов", "#b8a9f0"), ("lavender", "#b8a9f0"), ("сиренев", "#c3a6e0"), ("фиолет", "#8a6fc0"),
    ("purple", "#8a6fc0"), ("розов", "#f4b6c2"), ("pink", "#f4b6c2"), ("мят", "#8fd3c1"), ("mint", "#8fd3c1"),
    ("бирюз", "#5fc3c0"), ("голуб", "#9cc8e8"), ("light blue", "#9cc8e8"), ("син", "#3b5ba8"), ("blue", "#3b5ba8"),
    ("зелён", "#7fb27a"), ("зелен", "#7fb27a"), ("green", "#7fb27a"), ("жёлт", "#f2d16b"), ("желт", "#f2d16b"),
    ("yellow", "#f2d16b"), ("оранж", "#f0a050"), ("orange", "#f0a050"), ("красн", "#d9534f"), ("red", "#d9534f"),
    ("коричн", "#8b5a3c"), ("brown", "#8b5a3c"), ("деревян", "#b07c52"), ("wooden", "#b07c52"),
    ("бежев", "#e3d3b5"), ("beige", "#e3d3b5"), ("кремов", "#f3e6c8"), ("cream", "#f3e6c8"),
    ("бел", "#eeeae2"), ("white", "#eeeae2"), ("сер", "#9a9a9a"), ("grey", "#9a9a9a"), ("gray", "#9a9a9a"),
    ("чёрн", "#2b2b2b"), ("черн", "#2b2b2b"), ("black", "#2b2b2b"),
]

# Тип вещи: слова (regex по описанию, без ё), размер [ширина x, высота y, глубина z] см, цвет, место.
# place: back | left | right | floor | on:<тип>|<тип>… | corner ; wall — висит на стене на высоте y.
CATALOG: dict[str, dict[str, Any]] = {
    "bed": {"words": r"кроват|\bbed\b|постел", "size": [120, 50, 200], "color": "#b0835a", "place": "back",
            "label": "кровать"},
    "nightstand": {"words": r"тумбоч|прикроватн|nightstand|bedside", "size": [45, 55, 40], "color": "#a87b55",
                   "place": "back", "label": "тумбочка"},
    "lamp": {"words": r"ламп|\blamp|светильник|ночник", "size": [28, 48, 28], "color": "#f6e3b0",
             "place": "on:nightstand|desk|dresser|table", "label": "лампа"},
    "floor_lamp": {"words": r"торшер|floor lamp", "size": [40, 160, 40], "color": "#f6e3b0", "place": "corner",
                   "label": "торшер"},
    "window": {"words": r"\bокн|\bокош|\bwindow", "size": [110, 130, 8], "color": "#f2efe8", "place": "back",
               "wall": 95, "label": "окно"},
    "garland": {"words": r"гирлянд|fairy ?lights|string lights", "size": [200, 20, 4], "color": "#ffe7a8",
                "place": "back", "wall": 185, "label": "гирлянда"},
    "door": {"words": r"\bдвер|\bdoor", "size": [90, 205, 6], "color": "#c9a27a", "place": "left", "label": "дверь"},
    "desk": {"words": r"письменн\w* стол|рабоч\w* стол|\bdesk", "size": [120, 75, 60], "color": "#b5895f",
             "place": "right", "label": "письменный стол"},
    "table": {"words": r"обеденн\w* стол|кухонн\w* стол|журнальн\w* стол|\bстол\b|\bстола\b|\bстоле\b|столик"
                       r"|\btable\b", "size": [110, 75, 75], "color": "#b5895f", "place": "floor", "label": "стол"},
    "chair": {"words": r"\bстул|\bстуль|кресл|табурет|\bchair|\bstool", "size": [45, 90, 45], "color": "#9c7452",
              "place": "at:desk|table", "label": "стул"},
    "sofa": {"words": r"диван|\bsofa|\bcouch", "size": [190, 85, 85], "color": "#8a9bb0", "place": "back",
             "label": "диван"},
    "shelf": {"words": r"\bполк|\bполоч|\bshel", "size": [100, 55, 24], "color": "#b5895f", "place": "right",
              "wall": 135, "label": "полки"},
    "bookcase": {"words": r"стеллаж|книжн\w* шкаф|bookcase", "size": [80, 180, 32], "color": "#a87b55",
                 "place": "left", "label": "стеллаж"},
    "wardrobe": {"words": r"(?<!книжный )шкаф|гардероб|wardrobe|closet", "size": [100, 200, 60], "color": "#c49a6c",
                 "place": "right", "label": "шкаф"},
    "dresser": {"words": r"комод|dresser", "size": [100, 85, 48], "color": "#c49a6c", "place": "left",
                "label": "комод"},
    "rug": {"words": r"ков[её]р|коврик|\brug|carpet", "size": [160, 1, 110], "color": "#dcc0b8", "place": "floor",
            "label": "ковёр"},
    "socks": {"words": r"носк|носок|\bsock", "size": [40, 3, 30], "color": "#f4b6c2", "place": "floor",
              "label": "носки"},
    "clothes": {"words": r"одежд|разбросан\w* вещ|\bвещи\b|clothes", "size": [45, 8, 35], "color": "#9cb0c8",
                "place": "floor", "label": "одежда"},
    "dock": {"words": r"станци\w* зарядк|зарядн\w* станц|док-станц|\bdock|charging", "size": [32, 11, 14],
             "color": "#3a3d44", "place": "back", "label": "зарядная станция"},
    "plush": {"words": r"плюшев|мягк\w* игрушк|игрушк|plush|teddy", "size": [40, 20, 25], "color": "#e8c9a8",
              "place": "on:bed|sofa", "label": "мягкие игрушки"},
    "plant": {"words": r"растен|в горшк|горшок|фикус|\bplant", "size": [35, 80, 35], "color": "#5f9b5a",
              "place": "corner", "label": "растение"},
    "poster": {"words": r"постер|плакат|картин|poster", "size": [50, 70, 2], "color": "#c8b0d8", "place": "left",
               "wall": 150, "label": "постер"},
    "mirror": {"words": r"зеркал|mirror", "size": [50, 110, 3], "color": "#cfe3ee", "place": "left", "wall": 70,
               "label": "зеркало"},
    "tv": {"words": r"телевизор|\btv\b", "size": [110, 65, 8], "color": "#222226", "place": "right", "wall": 100,
           "label": "телевизор"},
    "computer": {"words": r"ноутбук|компьютер|монитор|laptop|computer", "size": [55, 40, 25], "color": "#2f3238",
                 "place": "on:desk|table", "label": "компьютер"},
    "fridge": {"words": r"холодильник|fridge|refrigerator", "size": [70, 180, 65], "color": "#e9ecef",
               "place": "back", "label": "холодильник"},
    "counter": {"words": r"столешниц|кухонн\w* шкаф|гарнитур|раковин|\bsink|counter", "size": [180, 90, 60],
                "color": "#d8cbb8", "place": "back", "label": "кухонный гарнитур"},
    "stove": {"words": r"плит[аыуе]\b|\bstove|\boven|духовк", "size": [60, 90, 60], "color": "#cfd2d6",
              "place": "back", "label": "плита"},
    "books": {"words": r"книг|\bbooks?\b", "size": [25, 12, 18], "color": "#7a6ff0", "place": "on:desk|nightstand|table",
              "label": "книги"},
}
WALL_TYPES = {"window", "garland", "poster", "mirror", "shelf", "tv"}  # висят на стене
FLAT_TYPES = {"rug", "socks", "clothes"}  # плоские — не мешают ставить мебель и не загораживают кадр
SUPPORT_TOP = {"bed": 50, "nightstand": 55, "desk": 75, "table": 75, "dresser": 85, "sofa": 45, "counter": 90,
               "stove": 90, "chair": 46}  # высота поверхности (см) у мебели, на которую что-то ставят
_ORDER = ["bed", "nightstand", "dock", "window", "garland", "desk", "chair", "computer", "shelf", "bookcase",
          "wardrobe", "dresser", "sofa", "table", "fridge", "counter", "stove", "tv", "door", "poster", "mirror",
          "lamp", "floor_lamp", "plant", "plush", "books", "rug", "socks", "clothes"]


def _norm(text: Any) -> str:
    return str(text or "").casefold().replace("ё", "е")


def _hex(value: Any, default: str) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"#[0-9a-fA-F]{6}", text):
        return text.lower()
    return default


def _num(value: Any, default: float, lo: float, hi: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return max(lo, min(hi, number))


def color_in(text: str) -> str | None:
    t = _norm(text)
    for word, color in COLOR_WORDS:
        if _norm(word) in t:
            return color
    return None


def _clauses(description: str) -> list[str]:
    return [c.strip() for c in re.split(r"[,;.:\n]+", _norm(description)) if c.strip()]


def described_types(description: str) -> dict[str, str]:
    """Какие вещи есть в описании: {тип: фраза описания, где она названа}."""
    found: dict[str, str] = {}
    for clause in _clauses(description):
        for kind in _ORDER:
            if kind in found:
                continue
            if re.search(CATALOG[kind]["words"], clause):
                if kind == "table" and "desk" in found and re.search(CATALOG["desk"]["words"], clause):
                    continue
                if kind == "lamp" and re.search(CATALOG["floor_lamp"]["words"], clause):
                    continue
                if kind == "wardrobe" and re.search(CATALOG["bookcase"]["words"], clause):
                    continue
                found[kind] = clause
    return found


def room_of(description: str) -> dict:
    t = _norm(description)
    room = dict(ROOM_DEFAULT)
    if re.search(r"маленьк|крошечн|тесн|small|tiny", t):
        room.update(width=380, depth=380)
    elif re.search(r"больш|просторн|large|spacious|зал\b", t):
        room.update(width=600, depth=500, height=290)
    if re.search(r"кухн|kitchen|ванн|bath", t):
        room["floor"] = "#d9d3c7"
    for clause in _clauses(description):
        if re.search(r"стен|wall", clause):
            room["wall"] = color_in(clause) or room["wall"]
        if re.search(r"\bпол\b|паркет|floor", clause):
            room["floor"] = color_in(clause) or room["floor"]
    return room


def _obj(kind: str, clause: str = "", **extra: Any) -> dict:
    cat = CATALOG[kind]
    obj = {"id": kind, "type": kind, "name": cat["label"], "size": list(cat["size"]), "pos": [0.0, 0.0], "y": 0.0,
           "rot": 0.0, "color": cat["color"]}
    c = color_in(clause) if clause else None
    if c:
        if kind in ("bed", "sofa"):
            obj["accent"] = c  # плед/подушки — цвет из описания, каркас — дерево
        elif kind == "window":
            obj["accent"] = c  # небо за окном
        else:
            obj["color"] = c
    if kind == "window":
        if re.search(r"вечер|ноч|night|evening|dusk", clause) and obj.get("accent") in (None, "#3b5ba8", "#9cc8e8"):
            obj["accent"] = "#2c3e7a"  # вечернее/ночное небо — тёмно-синее
        if re.search(r"штор|занавес|curtain", clause):
            obj["curtains"] = True
    if kind == "socks":
        obj["count"] = 3 if re.search(r"разбросан|много|scattered", clause) else 2
    if kind == "bed" and re.search(r"двуспальн|double|queen|king", clause):
        obj["size"][0] = 160
    obj.update(extra)
    return obj


def _footprint(o: dict) -> tuple[float, float, float, float]:
    """Прямоугольник вещи на полу (x0, z0, x1, z1) с учётом поворота на 90°."""
    w, _h, d = o["size"]
    if abs(abs(float(o.get("rot") or 0)) - 90) < 1:
        w, d = d, w
    x, z = o["pos"]
    return x - w / 2, z - d / 2, x + w / 2, z + d / 2


def _overlap(a: tuple, b: tuple, pad: float = 4.0) -> bool:
    return a[0] < b[2] + pad and b[0] < a[2] + pad and a[1] < b[3] + pad and b[1] < a[3] + pad


def _on_floor(o: dict) -> bool:
    return o["type"] not in WALL_TYPES and o["type"] not in FLAT_TYPES and not o.get("on") and o["type"] != "door"


def _place_wall(o: dict, wall: str, room: dict, along: float) -> None:
    w, d = float(room["width"]), float(room["depth"])
    sw, _sh, sd = o["size"]
    if wall == "left":
        o["rot"] = 90.0
        o["pos"] = [-w / 2 + sd / 2 + 1, along]
    elif wall == "right":
        o["rot"] = -90.0
        o["pos"] = [w / 2 - sd / 2 - 1, along]
    else:
        o["rot"] = 0.0
        o["pos"] = [along, -d / 2 + sd / 2 + 1]


def _settle(o: dict, placed: list[dict], room: dict) -> None:
    """Сдвинуть вещь вдоль её стены, пока не перестанет налезать на уже поставленные (по полу)."""
    if not _on_floor(o):
        return
    w, d = float(room["width"]), float(room["depth"])
    rot = float(o.get("rot") or 0)
    step = 15.0
    for k in range(40):
        fp = _footprint(o)
        hits = [p for p in placed if _on_floor(p) and _overlap(fp, _footprint(p))]
        if not hits:
            return
        sign = 1 if k % 2 == 0 else -1
        dist = step * (k // 2 + 1) * sign
        x, z = o["pos"]
        if abs(abs(rot) - 90) < 1:
            o["pos"] = [x, max(-d / 2 + 20, min(d / 2 - 20, z + dist))]
        else:
            o["pos"] = [max(-w / 2 + 20, min(w / 2 - 20, x + dist)), z]


def top_of(o: dict) -> float:
    """Высота верхней поверхности вещи (на неё ставят лампу, кружку, игрушки)."""
    base = float(o.get("y") or 0.0)
    return base + float(SUPPORT_TOP.get(o["type"], o["size"][1]))


def _put_on(o: dict, support: dict, slot: float = 0.0) -> None:
    sx, sz = support["pos"]
    w = support["size"][0]
    o["pos"] = [sx + slot * max(0.0, w / 2 - o["size"][0] / 2 - 4), sz]
    o["y"] = top_of(support)
    o["rot"] = float(support.get("rot") or 0)
    o["on"] = support["id"]


EXTRA_SKIP = {"socks", "clothes", "books", "plush", "computer", "garland", "poster"}  # мелочь планов — реквизит


def auto_layout(description: str, extra: Any = ()) -> dict:
    """Раскладка по описанию локации: каждая названная вещь — на своё место у стены (без Gemini).
    ``extra`` — типы мебели, которые называют планы этой локации («ставит кружку на тумбочку»)."""
    room = room_of(description)
    w, d = float(room["width"]), float(room["depth"])
    found = described_types(description)
    for kind in extra or ():
        if kind in CATALOG and kind not in EXTRA_SKIP:
            found.setdefault(kind, "")
    # Настольной лампе нужна опора: у кровати — тумбочка, иначе стол.
    if "lamp" in found and not {"nightstand", "desk", "dresser", "table"} & set(found):
        found["nightstand" if "bed" in found else "desk"] = ""
    by: dict[str, dict] = {}
    placed: list[dict] = []

    def add(o: dict) -> dict:
        _settle(o, placed, room)
        placed.append(o)
        by[o["type"]] = o
        return o

    for kind in _ORDER:
        if kind not in found:
            continue
        clause = found[kind]
        o = _obj(kind, clause)
        sw, sh, sd = o["size"]
        if kind == "bed":
            o["pos"] = [-w / 2 + sw / 2 + 25, -d / 2 + sd / 2 + 1]
        elif kind == "nightstand":
            bed = by.get("bed")
            x = bed["pos"][0] + bed["size"][0] / 2 + sw / 2 + 6 if bed else -w / 2 + sw / 2 + 10
            o["pos"] = [x, -d / 2 + sd / 2 + 1]
        elif kind == "dock":
            near = by.get("nightstand") or by.get("bed")
            x = (near["pos"][0] + (near["size"][0] / 2 if near else 0) + sw / 2 + 12) if near else 0.0
            o["pos"] = [x, -d / 2 + sd / 2 + 1]
        elif kind == "window":
            o["y"] = float(CATALOG[kind]["wall"])
            o["pos"] = [w * 0.14 if "bed" in by else 0.0, -d / 2 + sd / 2 + 0.5]
        elif kind == "garland":
            o["y"] = float(CATALOG[kind]["wall"])
            bed = by.get("bed")
            o["size"][0] = min(220.0, w * 0.55)
            o["pos"] = [bed["pos"][0] + 20 if bed else -w * 0.15, -d / 2 + 2]
        elif kind in ("desk", "wardrobe", "tv", "bookcase", "dresser", "door", "poster", "mirror", "shelf"):
            wall = CATALOG[kind]["place"]
            along = {"desk": -d / 2 + sw / 2 + 25, "wardrobe": d * 0.12, "tv": 0.0, "bookcase": -d * 0.05,
                     "dresser": -d * 0.1, "door": d / 2 - sw / 2 - 25, "poster": -d * 0.2, "mirror": d * 0.05,
                     "shelf": -d / 2 + sw / 2 + 35}[kind]
            if kind == "shelf" and "desk" in by:
                along = by["desk"]["pos"][1]
            if kind == "wardrobe" and "desk" in by:
                along = by["desk"]["pos"][1] + by["desk"]["size"][0] / 2 + sw / 2 + 15
            _place_wall(o, wall, room, along)
            if CATALOG[kind].get("wall"):
                o["y"] = float(CATALOG[kind]["wall"])
        elif kind == "chair":
            at = by.get("desk") or by.get("table")
            if at is not None and at["type"] == "desk":
                ax, az = at["pos"]
                o["pos"] = [ax - at["size"][2] / 2 - sd / 2 + 8, az]
                o["rot"] = 90.0 if float(at.get("rot") or 0) < 0 else -90.0
            elif at is not None:
                o["pos"] = [at["pos"][0], at["pos"][1] + at["size"][2] / 2 + sd / 2 - 8]
                o["rot"] = 180.0
            else:
                o["pos"] = [w * 0.25, 0.0]
        elif kind == "sofa":
            o["pos"] = [0.0, -d / 2 + sd / 2 + 1]
        elif kind == "table":
            o["pos"] = [0.0, d * 0.05]
            if re.search(r"журнальн|coffee", clause):
                o["size"] = [90, 42, 55]
                if "sofa" in by:
                    o["pos"] = [0.0, by["sofa"]["pos"][1] + by["sofa"]["size"][2] / 2 + 55]
        elif kind == "fridge":
            o["pos"] = [-w / 2 + sw / 2 + 5, -d / 2 + sd / 2 + 1]
        elif kind == "counter":
            fr = by.get("fridge")
            x = fr["pos"][0] + fr["size"][0] / 2 + sw / 2 + 2 if fr else -w / 2 + sw / 2 + 5
            o["pos"] = [x, -d / 2 + sd / 2 + 1]
        elif kind == "stove":
            ct = by.get("counter")
            x = ct["pos"][0] + ct["size"][0] / 2 + sw / 2 + 2 if ct else w * 0.2
            o["pos"] = [x, -d / 2 + sd / 2 + 1]
        elif kind in ("floor_lamp", "plant"):
            corners = [[w / 2 - sw / 2 - 8, -d / 2 + sd / 2 + 8], [-w / 2 + sw / 2 + 8, d / 2 - sd / 2 - 60],
                       [w / 2 - sw / 2 - 8, d / 2 - sd / 2 - 60]]
            o["pos"] = corners[0]
            for c in corners:
                o["pos"] = c
                if not any(_on_floor(p) and _overlap(_footprint(o), _footprint(p)) for p in placed):
                    break
        elif kind in ("lamp", "plush", "books", "computer"):
            targets = CATALOG[kind]["place"].split(":", 1)[1].split("|")
            support = next((by[t] for t in targets if t in by), None)
            if support is not None:
                slot = {"lamp": -0.9, "plush": -0.5, "books": 0.6, "computer": 0.0}[kind]
                if kind == "plush" and support["type"] == "bed":
                    o["pos"] = [support["pos"][0] - support["size"][0] * 0.15,
                                support["pos"][1] - support["size"][2] / 2 + 45]
                    o["y"] = top_of(support) + 6
                    o["on"] = support["id"]
                else:
                    _put_on(o, support, slot)
                if kind == "lamp" and support["type"] == "desk":
                    o["pos"][1] = support["pos"][1] - support["size"][0] / 2 + 20
            else:
                if kind == "lamp":
                    o = _obj("floor_lamp", clause)
                    o["pos"] = [w / 2 - 28, -d / 2 + 28]
                else:
                    o["pos"] = [w * 0.2, d * 0.1]
        elif kind == "rug":
            bed = by.get("bed")
            o["pos"] = [(bed["pos"][0] + bed["size"][0] / 2 + sw / 2 - 30) if bed else 0.0, d * 0.02]
            o["pos"][0] = max(-w / 2 + sw / 2 + 5, min(w / 2 - sw / 2 - 5, o["pos"][0]))
        elif kind in ("socks", "clothes"):
            rug = by.get("rug")
            o["pos"] = [rug["pos"][0] + 25, rug["pos"][1] + 30] if rug else [w * 0.05, d * 0.15]
            if kind == "clothes":
                o["pos"] = [o["pos"][0] - 70, o["pos"][1] - 20]
        add(o)
    _unique_ids(placed)
    return {"room": room, "objects": placed, "source": "auto"}


def _unique_ids(objects: list[dict]) -> None:
    seen: dict[str, int] = {}
    for o in objects:
        base = o["id"]
        seen[base] = seen.get(base, 0) + 1
        if seen[base] > 1:
            o["id"] = f"{base}{seen[base]}"


# --------------------------------------------------------------------------- #
# Gemini-зрение: плита локации → список вещей с положением
# --------------------------------------------------------------------------- #

VISION_PROMPT = """\
You see the empty background plate of an animated series location (a room). List every piece of
furniture and every visible object (bed, pillows and blanket count as the bed; nightstand, lamp,
window with curtains, door, desk, chair, shelves, rug, socks on the floor, plants, posters, garland …).
For each: type — one of {types} (or "other"); name (short, in Russian); x — horizontal centre in the
image from 0 (left edge) to 1 (right edge); depth — 0 near the camera … 1 at the back wall; wall —
back | left | right | floor (stands free on the floor) | on (stands on another object, give its type in
"on"); color — main colour "#rrggbb" as seen; size — small | medium | large. Return ONLY JSON."""


def _vision_schema() -> dict:
    item = {"type": "object", "properties": {
        "type": {"type": "string"}, "name": {"type": "string"}, "x": {"type": "number"},
        "depth": {"type": "number"}, "wall": {"type": "string"}, "on": {"type": "string"},
        "color": {"type": "string"}, "size": {"type": "string"}}, "required": ["type", "x", "depth"]}
    return {"type": "object", "properties": {"objects": {"type": "array", "items": item}}, "required": ["objects"]}


def from_vision(items: list, description: str, extra: Any = ()) -> dict:
    """Ответ зрения → раскладка: места по плите, размеры — по словарю, цвета — с плиты. Вещи из
    описания, которых зрение не назвало, ставятся по словарю."""
    base = auto_layout(description, extra)
    room = base["room"]
    w, d = float(room["width"]), float(room["depth"])
    placed: list[dict] = []
    by: dict[str, dict] = {}
    pending_on: list[tuple[dict, str]] = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        kind = _norm(it.get("type")).replace(" ", "_")
        if kind not in CATALOG:
            continue
        o = _obj(kind)
        if it.get("name"):
            o["name"] = str(it["name"])[:40]
        o["color"] = _hex(it.get("color"), o["color"]) if kind not in ("bed", "sofa", "window") else o["color"]
        if kind in ("bed", "sofa") and _hex(it.get("color"), ""):
            o["accent"] = _hex(it.get("color"), "")
        if kind == "window":
            o["curtains"] = True
            o["accent"] = "#2c3e7a"
        scale = {"small": 0.8, "large": 1.2}.get(_norm(it.get("size")), 1.0)
        if kind not in ("bed", "door", "window"):
            o["size"] = [round(v * scale, 1) for v in o["size"]]
        x = _num(it.get("x"), 0.5, 0.0, 1.0)
        depth = _num(it.get("depth"), 0.6, 0.0, 1.0)
        wall = _norm(it.get("wall"))
        sw, _sh, sd = o["size"]
        if CATALOG[kind].get("wall"):
            o["y"] = float(CATALOG[kind]["wall"])
        if wall == "left":
            _place_wall(o, "left", room, d / 2 - depth * d)
        elif wall == "right":
            _place_wall(o, "right", room, d / 2 - depth * d)
        elif wall == "back":
            _place_wall(o, "back", room, (x - 0.5) * w * 0.95)
        elif wall == "on" and it.get("on"):
            pending_on.append((o, _norm(it.get("on")).replace(" ", "_")))
            o["pos"] = [(x - 0.5) * w * 0.9, d / 2 - depth * d]
        else:
            spread = 1.15 - 0.35 * depth  # ближе к камере кадр уже — та же доля кадра = меньше см
            o["pos"] = [(x - 0.5) * w * spread, d / 2 - depth * d]
        o["pos"] = [max(-w / 2 + sw / 2, min(w / 2 - sw / 2, o["pos"][0])),
                    max(-d / 2 + sd / 2, min(d / 2 - sd / 2, o["pos"][1]))]
        _settle(o, placed, room)
        placed.append(o)
        by.setdefault(kind, o)
    for o, support_kind in pending_on:
        support = by.get(support_kind)
        if support is not None:
            _put_on(o, support)
    if not placed:
        return base
    have = {o["type"] for o in placed}
    for o in base["objects"]:
        if o["type"] in have:
            continue
        support = by.get(str(o.get("on") or ""))
        if o.get("on") and support is not None:
            _put_on(o, support)
        else:
            _settle(o, placed, room)
        placed.append(o)
        by.setdefault(o["type"], o)
    _unique_ids(placed)
    return {"room": room, "objects": placed, "source": "vision"}


def vision_layout(plate: Any, description: str, client: Any = None, extra: Any = ()) -> dict:
    """Плита локации → раскладка (Gemini-зрение, бесплатная модель, один раз на локацию)."""
    import base64
    from pathlib import Path

    from app.ai.gemini import GeminiClient, _extract_response_text
    from app.settings import settings

    path = Path(plate)
    mime = "image/png" if path.read_bytes()[:4] == b"\x89PNG" else "image/jpeg"
    payload = {"contents": [{"role": "user", "parts": [
        {"text": VISION_PROMPT.format(types=", ".join(CATALOG))},
        {"text": f"Location description: {description}"},
        {"inline_data": {"mime_type": mime, "data": base64.b64encode(path.read_bytes()).decode("ascii")}}]}],
        "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json",
                             "responseSchema": _vision_schema()}}
    response = (client or GeminiClient(timeout=90)).generate_content(settings.gemini_text_model, payload)
    data = json.loads(_extract_response_text(response))
    items = data.get("objects") if isinstance(data, dict) else data
    return from_vision(items or [], description, extra)


# --------------------------------------------------------------------------- #
# Кэш раскладки локации
# --------------------------------------------------------------------------- #


def layout_key(location: dict, plate_ref: dict | None, extra: Any = ()) -> str:
    raw = json.dumps([LAYOUT_VERSION, location.get("description") or "", (plate_ref or {}).get("id"),
                      sorted(extra or ())], ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _plate_ref(store: Any, project_id: int, location: dict) -> dict | None:
    from app import aivideo as V

    ref = V.selected_ref(store, project_id, "location", int(location["id"]), "location")
    return ref if ref and ref.get("asset_id") else None


def shot_types(store: Any, project_id: int, location: dict) -> list[str]:
    """Мебель, которую называют планы этой локации (действие, состояния, промпт кадра)."""
    try:
        rows = store.db.query_all(
            "SELECT s.action, s.state_json, s.end_state_json, s.image_prompt FROM aiv_shots s JOIN aiv_scripts c "
            "ON c.id = s.script_id WHERE c.project_id = ? AND s.location = ?",
            (int(project_id), str(location.get("slug") or "")))
    except Exception:  # noqa: BLE001 - старая база: только описание
        return []
    text = " . ".join(" . ".join(str(r[k] or "") for k in ("action", "state_json", "end_state_json", "image_prompt"))
                      for r in rows)
    return sorted(k for k in described_types(text) if k not in EXTRA_SKIP)


def cached(location: dict) -> dict:
    try:
        data = json.loads(location.get("layout_json") or "{}")
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def location_layout(store: Any, project_id: int, location: dict | None, vision: bool = False,
                    client: Any = None) -> dict:
    """Раскладка локации — одна на все её планы. ``vision`` — можно спросить Gemini-зрение (на этапе
    блокинга; оценка этапа и просмотр — только кэш или словарь, без сети)."""
    if not location:
        return {"room": dict(ROOM_DEFAULT), "objects": [], "source": "none"}
    from app.settings import settings

    plate = _plate_ref(store, project_id, location)
    extra = shot_types(store, project_id, location)
    key = layout_key(location, plate, extra)
    data = cached(location)
    can_see = bool(vision and plate is not None and (client is not None or settings.gemini_api_key))
    fresh = data.get("key") == key and isinstance(data.get("objects"), list)
    # Свежий кэш — он; по словарю, а плиту можно разобрать зрением (и раньше не падало) — пересчёт.
    if fresh and (not can_see or data.get("source") in ("vision", "agent") or data.get("vision_failed")):
        return data
    out = None
    note = ""
    if can_see:
        from app.aivideo_gen import _asset_path

        path = _asset_path(store, plate)
        if path is not None:
            try:
                out = vision_layout(path, location.get("description") or "", client, extra)
            except Exception as exc:  # noqa: BLE001 - зрение недоступно: раскладка по описанию
                note = f"Gemini-зрение не ответило ({type(exc).__name__}) — раскладка по описанию"
    if out is None:
        out = auto_layout(location.get("description") or "", extra)
        if note:
            out["vision_failed"] = True
            out["note"] = note
    out["key"] = key
    try:
        store.db.execute("UPDATE aiv_locations SET layout_json = ? WHERE id = ?",
                         (json.dumps(out, ensure_ascii=False), int(location["id"])))
    except Exception:  # noqa: BLE001 - старая база без колонки: раскладка живёт без кэша
        pass
    location["layout_json"] = json.dumps(out, ensure_ascii=False)
    return out


def find(layout: dict, text: Any) -> dict | None:
    """Вещь раскладки, названная в тексте («на прикроватной тумбочке», «у окна», «on the bed»).
    Совпадений несколько — самое длинное («прикроватн» — тумбочка, а не «кроват» — кровать)."""
    t = _norm(text)
    if not t:
        return None
    best: tuple[int, dict] | None = None
    for o in layout.get("objects") or []:
        words = CATALOG.get(o.get("type") or "", {}).get("words")
        score = 0
        for m in re.finditer(words, t) if words else ():
            score = max(score, len(m.group(0)))
        if o.get("name") and _norm(o["name"]) in t:
            score = max(score, len(o["name"]))
        if _norm(o["id"]) == t:
            score = 100
        if not score:
            continue
        if o.get("type") in ("lamp", "books", "plush", "computer"):  # мелочь — только если больше нечего
            score = 1
        if best is None or score > best[0]:
            best = (score, o)
    return best[1] if best else None


# --------------------------------------------------------------------------- #
# Вещи → узлы движка сцен (простые фигуры в узнаваемых пропорциях)
# --------------------------------------------------------------------------- #


def _box(size: list[float], pos: list[float], color: str, **extra: Any) -> dict:
    return {"type": "box", "size": [round(v, 2) for v in size], "position": [round(v, 2) for v in pos],
            "color": color, **extra}


def _cyl(r: float, h: float, pos: list[float], color: str, rt: float | None = None, seg: int = 20,
         **extra: Any) -> dict:
    node = {"type": "cylinder", "height": round(h, 2), "position": [round(v, 2) for v in pos], "color": color,
            "segments": seg, **extra}
    if rt is None:
        node["radius"] = round(r, 2)
    else:
        node["radiusTop"], node["radiusBottom"] = round(rt, 2), round(r, 2)
    return node


def _shade(color: str, k: float) -> str:
    c = _hex(color, "#888888")
    rgb = [int(c[i:i + 2], 16) for i in (1, 3, 5)]
    rgb = [max(0, min(255, int(v * k))) for v in rgb]
    return "#" + "".join(f"{v:02x}" for v in rgb)


def furniture_parts(o: dict) -> list[dict]:
    """Части вещи в её осях: начало — центр опоры на полу (y=0), лицо — в +z."""
    kind = o.get("type") or ""
    w, h, d = (float(v) for v in o["size"])
    col = o.get("color") or "#8a7f72"
    acc = o.get("accent")
    dark = _shade(col, 0.72)
    if kind == "bed":
        blanket = acc or "#c9b8e8"
        return [_box([w, 24, d], [0, 12, 0], col), _box([w, 100, 7], [0, 50, -d / 2 + 3.5], dark),
                _box([w - 6, 18, d - 10], [0, 33, 2], "#f1ece4"),
                _box([w * 0.62, 12, 34], [0, 48, -d / 2 + 30], "#f6f1ea"),
                _box([w + 2, 7, d * 0.62], [0, 45.5, d * 0.18], blanket),
                _box([w + 2, 30, 3], [0, 32, d * 0.18 + d * 0.31 + 1], blanket)]
    if kind == "nightstand":
        return [_box([w, h, d], [0, h / 2, 0], col), _box([w * 0.82, h * 0.32, 1.5], [0, h * 0.68, d / 2 + 0.6], dark),
                {"type": "sphere", "radius": 1.8, "position": [0, h * 0.68, d / 2 + 2], "color": "#e8d9a8",
                 "segments": 10}]
    if kind in ("lamp", "floor_lamp"):
        stem = h - 18
        return [_cyl(w * 0.28, 3, [0, 1.5, 0], dark), _cyl(1.2, stem, [0, 3 + stem / 2, 0], "#6b5a48"),
                _cyl(w * 0.5, 18, [0, stem + 9, 0], col, rt=w * 0.32, seg=24,
                     material={"emissive": "#ffd98a"})]
    if kind == "window":
        sky = acc or "#a9cfee"
        parts = [_box([w, h, 1.5], [0, h / 2, 0], sky), _box([w + 10, 6, 5], [0, h + 3, 2], col),
                 _box([w + 16, 4, 14], [0, -2, 6], col), _box([6, h, 5], [-w / 2 - 2, h / 2, 2], col),
                 _box([6, h, 5], [w / 2 + 2, h / 2, 2], col), _box([4, h, 4], [0, h / 2, 2], col),
                 _box([w, 4, 4], [0, h * 0.55, 2], col)]
        if o.get("curtains", True):
            cur = o.get("curtain_color") or "#efe2cf"
            parts += [_box([w * 0.3, h + 40, 4], [-w / 2 - w * 0.12, (h + 40) / 2 - 15, 7], cur),
                      _box([w * 0.3, h + 40, 4], [w / 2 + w * 0.12, (h + 40) / 2 - 15, 7], cur),
                      _cyl(1.2, w * 1.5, [0, h + 26, 7], "#8a7a68", rotation=[0, 0, 90], seg=8)]
        return parts
    if kind == "garland":
        n = max(6, int(w // 22))
        parts = []
        for i in range(n):
            u = i / (n - 1)
            x = -w / 2 + u * w
            y = -10 * math.sin(math.pi * u)  # провисает
            parts.append({"type": "sphere", "radius": 2.2, "position": [round(x, 1), round(y, 1), 2], "color": col,
                          "segments": 8, "material": {"emissive": col}})
        parts.append({"type": "line", "points": [[round(-w / 2 + u * w, 1), round(-10 * math.sin(math.pi * u), 1), 1]
                                                 for u in (i / 10 for i in range(11))], "width": 2, "color": "#5a4a3a"})
        return parts
    if kind == "door":
        return [_box([w, h, d], [0, h / 2, 0], col), _box([w + 12, 6, d + 2], [0, h + 3, 0], _shade(col, 0.85)),
                _box([6, h, d + 2], [-w / 2 - 3, h / 2, 0], _shade(col, 0.85)),
                _box([6, h, d + 2], [w / 2 + 3, h / 2, 0], _shade(col, 0.85)),
                {"type": "sphere", "radius": 3, "position": [w / 2 - 12, h * 0.48, d / 2 + 3], "color": "#d8c070",
                 "segments": 10}]
    if kind in ("desk", "table"):
        parts = [_box([w, 4, d], [0, h - 2, 0], col)]
        parts += [_box([5, h - 4, 5], [sx * (w / 2 - 5), (h - 4) / 2, sz * (d / 2 - 5)], dark)
                  for sx in (-1, 1) for sz in (-1, 1)]
        if kind == "desk":
            parts.append(_box([w * 0.35, 18, d - 6], [w * 0.28, h - 13, 0], dark))
        return parts
    if kind == "chair":
        seat = 46.0
        parts = [_box([w, 5, d], [0, seat - 2.5, 0], col), _box([w, h - seat, 4], [0, seat + (h - seat) / 2, -d / 2 + 2],
                                                                 col)]
        parts += [_box([4, seat - 5, 4], [sx * (w / 2 - 4), (seat - 5) / 2, sz * (d / 2 - 4)], dark)
                  for sx in (-1, 1) for sz in (-1, 1)]
        return parts
    if kind == "sofa":
        cushion = acc or _shade(col, 1.12)
        return [_box([w, 42, d], [0, 21, 0], col), _box([w, 45, 20], [0, 64, -d / 2 + 10], col),
                _box([18, 62, d], [-w / 2 + 9, 31, 0], dark), _box([18, 62, d], [w / 2 - 9, 31, 0], dark),
                _box([w / 2 - 22, 10, d - 26], [-(w / 4 - 2), 47, 8], cushion),
                _box([w / 2 - 22, 10, d - 26], [w / 4 - 2, 47, 8], cushion)]
    if kind in ("shelf", "bookcase"):
        boards = 3 if kind == "shelf" else 5
        parts = []
        for i in range(boards):
            y = (h / (boards - 1)) * i if boards > 1 else 0
            parts.append(_box([w, 2.5, d], [0, y + 1.25, 0], col))
            if i < boards - 1:
                books = ["#7a6ff0", "#f4b6c2", "#8fd3c1", "#f2d16b", "#d9534f", "#3b5ba8"]
                x = -w / 2 + 6
                for k in range(int(w // 9) - 2):
                    bh = 14 + (k * 7) % 9
                    parts.append(_box([6, bh, d * 0.7], [x + 3, y + 2.5 + bh / 2, 0], books[(k + i) % len(books)]))
                    x += 8
        if kind == "bookcase":
            parts += [_box([2.5, h, d], [-w / 2, h / 2, 0], dark), _box([2.5, h, d], [w / 2, h / 2, 0], dark)]
        return parts
    if kind in ("wardrobe", "dresser"):
        parts = [_box([w, h, d], [0, h / 2, 0], col)]
        if kind == "wardrobe":
            parts += [_box([1.5, h - 10, 1], [0, h / 2, d / 2 + 0.5], dark),
                      _box([2, 20, 2], [-6, h * 0.5, d / 2 + 1.5], "#d8c070"),
                      _box([2, 20, 2], [6, h * 0.5, d / 2 + 1.5], "#d8c070")]
        else:
            for i in range(3):
                y = h * (0.2 + 0.28 * i)
                parts += [_box([w - 8, 1, 1], [0, y + h * 0.13, d / 2 + 0.5], dark),
                          _box([10, 2, 2], [0, y, d / 2 + 1.2], "#d8c070")]
        return parts
    if kind == "rug":
        return [_box([w, max(1.0, h), d], [0, max(1.0, h) / 2, 0], col)]
    if kind == "socks":
        cols = [col, "#f2d16b", "#9cc8e8"]
        spots = [(-12, -6, 25), (10, 8, -40), (-4, 14, 70)]
        return [_box([7, 3, 18], [sx, 1.5, sz], cols[i % 3], rotation=[0, rot, 0])
                for i, (sx, sz, rot) in enumerate(spots[:int(o.get("count") or 2)])]
    if kind == "clothes":
        return [_box([w, 4, d], [0, 2, 0], col, rotation=[0, 15, 0]),
                _box([w * 0.7, 4, d * 0.6], [6, 6, -4], "#e8e4dc", rotation=[0, -20, 0])]
    if kind == "dock":
        return [_box([w, 2, d + 10], [0, 1, 5], dark), _box([w, h, d * 0.5], [0, h / 2, -d * 0.25], col),
                {"type": "sphere", "radius": 1.2, "position": [0, h - 3, d * 0.02], "color": "#22d3ee", "segments": 8,
                 "material": {"emissive": "#22d3ee"}}]
    if kind == "plush":
        return [{"type": "sphere", "radius": 9, "position": [-10, 9, 0], "color": col, "segments": 14},
                {"type": "sphere", "radius": 6.5, "position": [-10, 22, 0], "color": col, "segments": 14},
                {"type": "sphere", "radius": 8, "position": [12, 8, 2], "color": "#c8b0d8", "segments": 14},
                {"type": "sphere", "radius": 5.5, "position": [12, 18, 2], "color": "#c8b0d8", "segments": 14}]
    if kind == "plant":
        return [_cyl(w * 0.32, h * 0.3, [0, h * 0.15, 0], "#b5653b", rt=w * 0.4),
                {"type": "sphere", "radius": w * 0.55, "position": [0, h * 0.62, 0], "color": col, "segments": 12},
                {"type": "sphere", "radius": w * 0.4, "position": [w * 0.2, h * 0.88, 0], "color": _shade(col, 1.15),
                 "segments": 12}]
    if kind in ("poster", "mirror"):
        return [_box([w, h, d], [0, h / 2, 0], col), _box([w + 4, h + 4, d * 0.5], [0, h / 2, -0.5], _shade(col, 0.6))]
    if kind == "tv":
        return [_box([w, h, d], [0, h / 2, 0], col), _box([w - 6, h - 6, 1], [0, h / 2, d / 2 + 0.3], "#3a4250")]
    if kind == "computer":
        return [_box([w, 2, d], [0, 1, 4], "#55585e"), _box([w, h * 0.85, 2], [0, h * 0.5, -d / 2 + 4], col),
                _box([w - 4, h * 0.75, 0.5], [0, h * 0.5, -d / 2 + 5.2], "#4a6a8a")]
    if kind == "fridge":
        return [_box([w, h, d], [0, h / 2, 0], col), _box([w - 2, 1, 1], [0, h * 0.62, d / 2 + 0.5], "#b8bec4"),
                _box([3, 30, 3], [w / 2 - 8, h * 0.78, d / 2 + 2], "#9aa2aa"),
                _box([3, 30, 3], [w / 2 - 8, h * 0.4, d / 2 + 2], "#9aa2aa")]
    if kind == "counter":
        return [_box([w, h - 4, d], [0, (h - 4) / 2, 0], col), _box([w + 2, 4, d + 2], [0, h - 2, 0], "#ece6dc")]
    if kind == "stove":
        parts = [_box([w, h, d], [0, h / 2, 0], col), _box([w - 10, h * 0.4, 1], [0, h * 0.35, d / 2 + 0.5], "#2a2d33")]
        parts += [_cyl(8, 1.5, [sx * 14, h + 0.75, sz * 14], "#2a2d33") for sx in (-1, 1) for sz in (-1, 1)]
        return parts
    if kind == "books":
        return [_box([w, 4, d], [0, 2, 0], col), _box([w * 0.9, 4, d * 0.9], [1, 6, 0], "#f4b6c2"),
                _box([w * 0.85, 4, d * 0.85], [-1, 10, 0], "#8fd3c1")]
    # Своя/неизвестная вещь — коробка нужного размера и цвета.
    shape = o.get("kind") or "box"
    if shape in ("cylinder", "disc"):
        return [_cyl(w / 2, h, [0, h / 2, 0], col, seg=24)]
    if shape == "sphere":
        return [{"type": "sphere", "radius": w / 2, "position": [0, w / 2, 0], "color": col, "segments": 16}]
    return [_box([w, h, d], [0, h / 2, 0], col)]


def furniture_node(o: dict) -> dict:
    return {"type": "group", "position": [round(float(o["pos"][0]), 2), round(float(o.get("y") or 0.0), 2),
                                          round(float(o["pos"][1]), 2)],
            "rotation": [0, float(o.get("rot") or 0.0), 0], "children": furniture_parts(o), "sort": "object"}


def object_box(o: dict) -> dict | None:
    """Коробка вещи для проверки кадра (центр, полуразмеры, поворот). Плоское и настенное — нет."""
    if o.get("type") in FLAT_TYPES or o.get("type") in ("garland", "window", "poster", "mirror"):
        return None
    w, h, d = (float(v) for v in o["size"])
    if o.get("type") == "bed":
        h = 50.0  # изголовье выше, но кадр загораживает матрас с пледом
    y0 = float(o.get("y") or 0.0)
    return {"id": o["id"], "role": "object", "center": [float(o["pos"][0]), y0 + h / 2, float(o["pos"][1])],
            "half": [w / 2, h / 2, d / 2], "rot": float(o.get("rot") or 0.0), "label": o.get("name") or o["id"]}


def clean_object(raw: Any, k: int) -> dict | None:
    """Вещь от агента/Gemini: известный тип (кровать, тумбочка…) или простая фигура kind."""
    if not isinstance(raw, dict):
        return None
    kind = _norm(raw.get("type")).replace(" ", "_")
    base = _obj(kind) if kind in CATALOG else {
        "id": f"obj{k}", "type": "", "name": "", "size": [60, 60, 60], "pos": [0.0, 0.0], "y": 0.0, "rot": 0.0,
        "color": "#8a7f72"}
    out = dict(base)
    from app.aivideo import slugify

    out["id"] = slugify(raw.get("id") or raw.get("name") or base["id"], base["id"])[:40]
    if raw.get("name"):
        out["name"] = str(raw["name"])[:60]
    if not out["type"]:
        out["kind"] = raw.get("kind") if raw.get("kind") in ("box", "cylinder", "disc", "sphere") else "box"
    size = raw.get("size")
    if isinstance(size, list):
        out["size"] = [_num(v, base["size"][i] if i < 3 else 60, 1, 800) for i, v in
                       enumerate((list(size) + list(base["size"]))[:3])]
    elif size is not None:
        out["size"] = [_num(size, 60, 1, 800)] * 3
    pos = raw.get("pos") if isinstance(raw.get("pos"), list) else [0, 0]
    out["pos"] = [_num(v, 0.0, -2000, 2000) for v in (list(pos) + [0, 0])[:2]]
    out["y"] = _num(raw.get("y"), float(base.get("y") or 0.0), 0, 400)
    out["rot"] = _num(raw.get("rot"), float(base.get("rot") or 0.0), -360, 360)
    out["color"] = _hex(raw.get("color"), base["color"])
    if raw.get("accent"):
        out["accent"] = _hex(raw.get("accent"), base.get("accent") or base["color"])
    for key in ("curtains", "count", "on"):
        if raw.get(key) not in (None, ""):
            out[key] = raw[key]
    return out
