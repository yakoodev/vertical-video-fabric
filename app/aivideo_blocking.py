"""ИИ-видео v3: блокинг планов в нашем 3D-движке сцен (слой ``scene``, docs/motion/scene-engine.md).

Зачем: картинковая модель рисует ключевые кадры «по описанию» — позы, места и ракурс плывут, пылесос
встаёт на дыбы, печенье двоится. Блокинг даёт ей КОМПОЗИЦИЮ: подробный макет сцены — комната локации
со всеми её вещами, манекены персонажей в позе, реквизит в руках и на местах, снятые камерой плана, на
старте и на конце плана. Скриншот блокинга идёт первым референсом кадра (только позиции, позы и
ракурс — цвета и формы макета модель не рисует), листы персонажей и плита локации — следом.

* **Комната** — одна раскладка на локацию (``app/aivideo_layout.py``): все вещи из описания (и с плиты —
  Gemini-зрением, один раз, кэш), узнаваемые пропорции и цвета, одна и та же во всех планах.
* **spec** плана (сантиметры; x вправо, y вверх, z на камеру; пол y=0, комната по центру):

  .. code-block:: json

     {"start": {"actors": [{"id": "mira", "pos": [-40, 20], "facing": "camera", "pose": "standing",
                            "arms": "holding", "on": ""}],
                "props": [{"id": "mug", "name": "кружка", "holder": "mira"},
                          {"id": "sock", "name": "носок", "on": "rug"}]},
      "end": {"actors": [...], "props": [...]},
      "objects": [], "room": {}, "camera": {"pos": [...], "target": [...], "fov": 50}}

  ``objects``/``room`` пусто — комната локации (так и надо); своя мебель — только если агент её задал.
  ``camera`` можно не писать — она ставится по плану (``camera.shot``/``angle``/``lens``). ``facing`` —
  градусы (0 — в камеру, 90 — вправо кадра, −90 — влево, 180 — спиной) или camera|left|right|away.
  ``pose`` — standing | walking | sitting | crouching | kneeling | lying; ``arms`` — down | up | forward |
  holding | wave; ``on`` — id вещи комнаты, на которой персонаж сидит/лежит (кровать, стул…).
  Реквизит: ``holder`` — в руке персонажа, ``on`` — на вещи комнаты, иначе ``pos`` [x, y, z].
* **Манекены** в реальном росте: человек — голова, туловище, руки и ноги в позе (рост из карты
  личности, по умолчанию 165 см), робот-диск — низкий диск ~33 см с экраном-глазами и антенной,
  животное — тело, голова, лапы, хвост; у каждого персонажа свой нейтральный цвет — промпт кадра
  говорит, какой цвет кто.
* **Камера** по плану: крупность → сколько видно по высоте, объектив → угол поля, ракурс → высота;
  смотрит на центр масс персонажей, все персонажи влезают; никогда не внутри вещи или стены.
* **Самоконтроль кадра** (``frame_check``): лучи из камеры по сетке кадра — каждый персонаж в кадре и
  не закрыт, персонажи занимают разумную долю кадра, никакая вещь перед ними не закрывает > 40 % кадра.
  Не прошёл ни один вариант камеры — блокинг в кадр НЕ идёт (промпт без него, пометка в params).
* **Кто пишет** места персонажей: Gemini — раскладку всей серии одним запросом (бесплатно; комната
  дана, места из state/end_state планов); нет ключа — ``auto_spec`` по state (left/center/right,
  foreground/background, поза, «у кровати», «на тумбочке»). Агент и владелец правят spec
  (``set_blocking``) или кладут свою JSON-сцену движка поверх (``scenes.start/end``).
* **Рендер** — 3D-движком сцен (``app/aivideo_blocking3d.py``: комната из моделей Kenney, персонажи
  со скелетом в позах из клипов UAL, цвета из карточек, нейтральный матовый стиль), 9:16, 0 ₽ —
  ``prompt`` = ``blocking3d:<хэш>``. Нет браузера/паков, своя сцена агента или
  ``AIV_BLOCKING_ENGINE=2d`` — компоновщиком (2D-холст, ``blocking:<хэш сцены>``). Версии
  ``blocking_start`` / ``blocking_end`` плана.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

import logging

from app import aivideo as V
from app import aivideo_layout as L
from app import projects as P

_log = logging.getLogger(__name__)
KINDS = ("box", "cylinder", "disc", "sphere")
POSES = ("standing", "walking", "sitting", "crouching", "kneeling", "lying")
ARMS = ("down", "up", "forward", "holding", "wave")
# Нейтральные, но различимые цвета манекенов: яркий оранжевый модель перерисовывала в «дверь».
ACTOR_COLORS = ("#8e9bb5", "#c2a98a", "#8fae95", "#b08fa8")
COLOR_NAMES = {"#8e9bb5": "slate-blue", "#c2a98a": "sand", "#8fae95": "sage-green", "#b08fa8": "mauve"}
ROOM_DEFAULT = L.ROOM_DEFAULT
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
BLOCKING_W, BLOCKING_H = 1080, 1920
RENDER_WIDTH = 540
LIMIT = 1000.0  # см: дальше комнаты ничего не ставим
GRID = (27, 48)  # лучей по ширине и высоте кадра в самоконтроле


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


def legend(store: Any, project_id: int, shot: dict) -> str:
    """Для промпта кадра: какой манекен — кто («slate-blue mannequin = Мира; sand flat disc = Бип»).
    Кадр блокинга нарисован 3D-движком — персонажи там в своих цветах («mint hair, lavender top = Мира»)."""
    ref = V.selected_ref(store, project_id, "shot", int(shot["id"]), "blocking_start")
    if ref and str(ref.get("prompt") or "").startswith("blocking3d:"):
        from app import aivideo_blocking3d as B3

        return B3.legend(cast_info(store, project_id, shot), _characters(store, project_id, shot))
    what = {"human": "mannequin", "disc": "flat disc", "quadruped": "animal stand-in", "small": "ball stand-in"}
    return "; ".join(f"the {c['color_name']} {what.get(c['stand_in'], 'stand-in')} = {c['name']}"
                     for c in cast_info(store, project_id, shot))


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


def auto_spec(store: Any, project_id: int, shot: dict, nxt: dict | None, layout: dict | None = None,
              prev: list[dict] | None = None) -> dict:
    """Раскладка без Gemini: места/позы/взгляд/руки из state (старт) и конца плана, у вещей комнаты.

    Связность: план, продолжающий предыдущий (``prev`` — планы серии до этого), начинает с тех же мест,
    где тот закончил; кто в конце «там же» (то же место в state) — стоит там же; кто ставит предмет
    на вещь (в начале в руке, в конце на тумбочке) — стоит у этой вещи."""
    cast = shot.get("cast") or []
    if layout is None:
        layout = shot_layout(store, project_id, shot)
    infos = {c["id"]: c for c in cast_info(store, project_id, shot)}
    action = f"{shot.get('action') or ''} {shot.get('motion_prompt') or ''}"
    start = _moment(shot.get("state") or {}, cast, infos, layout, action)
    end = _moment(V.end_state_of(shot, nxt), cast, infos, layout, action, end=True)
    objects = {o["id"]: o for o in layout.get("objects") or []}
    # Предмет из руки на вещь — персонаж у этой вещи и в начале, и в конце.
    for p in end["props"]:
        support = objects.get(str(p.get("on") or ""))
        held = next((q for q in start["props"] if q.get("holder") and prop_kind(q.get("name"))[0] ==
                     prop_kind(p.get("name"))[0]), None)
        if support is None or held is None:
            continue
        for moment in (start, end):
            for a in moment["actors"]:
                if a["id"] == held["holder"] and not a.get("on"):
                    a["pos"] = _in_front(support, 35.0)
                    a["_anchored"] = True
    # Продолжение прошлого плана: начало — там, где он закончился.
    if prev and shot.get("continues_from_prev") and prev[-1].get("location") == shot.get("location"):
        before = auto_spec(store, project_id, prev[-1], shot, layout, prev[:-1])
        last = {a["id"]: a for a in before["end"]["actors"]}
        for a in start["actors"]:
            if a["id"] in last and not a.get("_anchored"):
                a["pos"] = list(last[a["id"]]["pos"])
                if last[a["id"]].get("on"):
                    a["on"] = last[a["id"]]["on"]
    # Кто в конце на том же месте по state — стоит там же, где в начале.
    st_pos = {c["id"]: str(c.get("position") or "") for c in (shot.get("state") or {}).get("characters") or []}
    en_pos = {c["id"]: str(c.get("position") or "") for c in V.end_state_of(shot, nxt).get("characters") or []}
    first = {a["id"]: a for a in start["actors"]}
    for a in end["actors"]:
        if not a.get("_anchored") and a["id"] in first and st_pos.get(a["id"]) == en_pos.get(a["id"]):
            a["pos"] = list(first[a["id"]]["pos"])
            if first[a["id"]].get("on"):
                a["on"] = first[a["id"]]["on"]
    for moment in (start, end):
        for a in moment["actors"]:
            a.pop("_anchored", None)
    return {"room": {}, "objects": [], "start": start, "end": end}


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


def shot_layout(store: Any, project_id: int, shot: dict, vision: bool = False, client: Any = None) -> dict:
    """Комната плана — раскладка его локации (одна на все планы локации)."""
    loc = V.location_by_slug(store, project_id, shot.get("location") or "") if shot.get("location") else None
    return L.location_layout(store, project_id, loc, vision=vision, client=client)


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
    return {"spec": auto_spec(store, _project_of(store, shot), shot, nxt, prev=shots[:n]), "source": "auto",
            "state_key": key,
            "scenes": {}, "outdated": bool(data.get("spec"))}


def built_of(store: Any, shot: dict) -> dict[str, dict]:
    """Кадры блокинга плана {start, end?}: {scene, check, override}."""
    data = current(store, shot)
    project_id = _project_of(store, shot)
    infos = cast_info(store, project_id, shot)
    layout = shot_layout(store, project_id, shot)
    spec = clean_spec(data["spec"], [c["id"] for c in shot.get("cast") or []])
    out = {}
    for which in ("start", "end") if shot.get("needs_motion") else ("start",):
        override = (data.get("scenes") or {}).get(which)
        if isinstance(override, dict) and override.get("nodes"):
            out[which] = {"scene": override, "override": True,
                          "check": {"ok": True, "problems": [], "notes": ["своя сцена движка — не проверяется"]}}
        else:
            out[which] = {**build(spec, which, shot, infos, layout), "override": False}
    return out


def scenes_of(store: Any, shot: dict) -> dict[str, dict]:
    """Готовые JSON-сцены движка {start, end} (end — только у плана с видео)."""
    return {which: b["scene"] for which, b in built_of(store, shot).items()}


def frame_check(store: Any, shot: dict, which: str) -> dict:
    """Самоконтроль кадра блокинга ``which`` плана: ``{ok, problems, …}`` (кадра нет — ok False)."""
    try:
        built = built_of(store, shot).get(which)
    except Exception as exc:  # noqa: BLE001 - проверка не валит кадр: блокинг просто не используется
        return {"ok": False, "problems": [f"блокинг не собран: {type(exc).__name__}: {str(exc)[:160]}"]}
    if built is None:
        return {"ok": False, "problems": [f"блокинга «{which}» нет"]}
    return built["check"]


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
        if not isinstance(spec, dict):
            raise V.AivError("spec — объект {start?, end?, objects?, room?, camera?}")
        # Не названное — как было (правка одного старта не стирает конец и мебель).
        merged = {**(data.get("spec") or {}), **spec}
        data["spec"] = clean_spec(merged, [c["id"] for c in shot.get("cast") or []])
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
    layout = shot_layout(store, project_id, shot)
    try:
        checks = {w: b["check"] for w, b in built_of(store, shot).items()}
    except Exception as exc:  # noqa: BLE001 - просмотр не падает из-за сборки
        checks = {"error": {"ok": False, "problems": [str(exc)[:200]]}}
    return {"shot_id": int(shot["id"]), "idx": shot["idx"], "source": data.get("source"),
            "outdated": bool(data.get("outdated")), "spec": data["spec"], "scenes_override": data.get("scenes") or {},
            "legend": legend(store, project_id, shot), "cast": cast_info(store, project_id, shot),
            "layout": {"source": layout.get("source"), "room": layout.get("room"),
                       "objects": [{k: o.get(k) for k in ("id", "type", "name", "pos", "y", "rot", "size", "on")}
                                   for o in layout.get("objects") or []]},
            "checks": checks,
            "frames": {w: V.selected_ref(store, project_id, "shot", int(shot["id"]), f"blocking_{w}")
                       for w in ("start", "end")}}


BLOCKING_PROMPT = """\
Ты постановщик мультсериала и расставляешь персонажей и реквизит планов серии в подробный 3D-макет
(блокинг) для картинковой модели: она перерисует скриншот макета в кадр, сохранив композицию, позы и
места. Единицы — сантиметры. Оси: x вправо (от камеры), y вверх, z к камере; пол y=0; комната по
центру (0,0), задняя стена z = -depth/2. Комнаты локаций УЖЕ расставлены (layouts: вещи с id, типом,
pos [x, z], size [ширина, высота, глубина], y — высота низа, rot) — мебель не двигай и не придумывай,
ставь персонажей относительно неё. Для каждого плана — start (момент начала) и end (момент конца):
actors — каждый персонаж плана: pos [x, z], facing (градусы: 0 — лицом в камеру, 90 — вправо кадра,
-90 — влево, 180 — спиной), pose standing|walking|sitting|crouching|kneeling|lying, arms
down|up|forward|holding|wave (holding — держит предмет правой рукой), on — id вещи комнаты, на которой
сидит/лежит (кровать, стул, диван); props — реквизит плана из state и действия: name, count, и где он:
holder — id персонажа, у которого в руке, или on — id вещи комнаты, на которой стоит/лежит, или
pos [x, y, z]. Состояния бери из state (начало) и end_state (конец) плана; конец плана, который
продолжает следующий, = старт следующего (те же места и позы). Никто не стоит внутри мебели.
Робот-пылесос и другие «предметные» персонажи — всегда на полу. Камеру не пиши — её ставит сервис
по крупности плана. Верни ТОЛЬКО JSON по схеме."""


def _schema() -> dict:
    num = {"type": "number"}
    vec = {"type": "array", "items": num}
    actor = {"type": "object", "properties": {"id": {"type": "string"}, "pos": vec, "facing": num,
                                              "pose": {"type": "string"}, "arms": {"type": "string"},
                                              "on": {"type": "string"}},
             "required": ["id", "pos", "facing", "pose"]}
    prop = {"type": "object", "properties": {"id": {"type": "string"}, "name": {"type": "string"},
                                             "count": {"type": "integer"}, "pos": vec, "holder": {"type": "string"},
                                             "on": {"type": "string"}}, "required": ["id"]}
    moment = {"type": "object", "properties": {"actors": {"type": "array", "items": actor},
                                               "props": {"type": "array", "items": prop}}, "required": ["actors"]}
    return {"type": "object", "properties": {
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
    by_idx = {int(s.get("idx") or 0): s for s in data.get("shots") or [] if isinstance(s, dict)}
    written = []
    for shot in todo:
        n = next(i for i, s in enumerate(shots) if s["id"] == shot["id"])
        nxt = shots[n + 1] if n + 1 < len(shots) else None
        auto = auto_spec(store, project_id, shot, nxt, prev=shots[:n])
        got = by_idx.get(int(shot["idx"]))
        if got and source == "gemini":
            raw = {"room": {}, "objects": [], "start": got.get("start") or auto["start"],
                   "end": got.get("end") or auto["end"]}
            spec, src = clean_spec(raw, [c["id"] for c in shot.get("cast") or []]), "gemini"
            for which in ("start", "end"):  # Gemini забыл персонажа или реквизит — ставим по state
                have = {a["id"] for a in spec[which]["actors"]}
                spec[which]["actors"] += [a for a in auto[which]["actors"] if a["id"] not in have]
                if not spec[which]["props"]:
                    spec[which]["props"] = auto[which]["props"]
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
    layouts = []
    for loc in V.list_locations(store, project_id):
        lay = L.location_layout(store, project_id, loc)
        layouts.append({"id": loc["slug"], "name": loc["name"], "room": lay.get("room"),
                        "objects": [{k: o.get(k) for k in ("id", "type", "name", "pos", "y", "rot", "size")}
                                    for o in lay.get("objects") or []]})
    context = {
        "characters": [{"id": c["slug"], "name": c["name"], "stand_in": stand_in(c),
                        "size_cm": body_size(c, stand_in(c)),
                        "identity_card": c["identity_card"], **({"constraints": c["constraints"]}
                                                                  if c.get("constraints") else {})}
                       for c in V.list_characters(store, project_id)],
        "layouts": layouts,
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


def _characters(store: Any, project_id: int, shot: dict) -> dict[str, dict]:
    out = {}
    for member in shot.get("cast") or []:
        c = V.character_by_slug(store, project_id, member["id"])
        if c:
            out[member["id"]] = c
    return out


def frame_plans(store: Any, shot: dict) -> dict[str, dict]:
    """Чем рисовать кадры блокинга плана: {which: {engine: 3d|2d, marker, scene}}. 3D-движок
    (``app/aivideo_blocking3d``) — по умолчанию, если доступен; своя сцена агента и нет браузера/паков
    (или AIV_BLOCKING_ENGINE=2d) — старый 2D-холст."""
    from app import aivideo_blocking3d as B3

    built = built_of(store, shot)
    use3d = B3.available()
    project_id = _project_of(store, shot)
    infos = chars = None
    out = {}
    for which, b in built.items():
        if use3d and not b.get("override"):
            if infos is None:
                infos, chars = cast_info(store, project_id, shot), _characters(store, project_id, shot)
            spec3 = B3.scene_spec(b, shot, infos, chars, which)
            out[which] = {"engine": "3d", "marker": f"blocking3d:{B3.spec_key(spec3)}", "scene": spec3, "built": b}
        else:
            out[which] = {"engine": "2d", "marker": f"blocking:{scene_key(b['scene'])}", "scene": b["scene"], "built": b}
    return out


def render(store: Any, shot_id: int, which: str, job: dict | None = None) -> dict:
    """Скриншот блокинга (9:16, 0 ₽) → версия ``blocking_<which>`` плана. Тот же кадр уже есть — он."""
    from uuid import uuid4

    from app import scene_engine as se
    from app.montage_assets import assets_dir, probe_asset

    shot = V.get_shot(store, shot_id)
    project_id = _project_of(store, shot)
    plans = frame_plans(store, shot)
    if which not in plans:
        raise V.AivError(f"план {shot['idx']}: блокинга «{which}» нет (конец — только у плана с видео)")
    plan = plans[which]
    marker = plan["marker"]
    kind = f"blocking_{which}"
    have = V.selected_ref(store, project_id, "shot", int(shot_id), kind)
    if have and have.get("prompt") == marker and have.get("asset_id"):
        if have["stale"]:
            store.db.execute("UPDATE aiv_refs SET stale = 0 WHERE id = ?", (int(have["id"]),))
        return V.get_ref(store, int(have["id"]))
    png = None
    if plan["engine"] == "3d":
        from app import aivideo_blocking3d as B3
        from app.scene3d import Scene3dError, SceneSpecError

        try:
            png = B3.render_png(plan["scene"])
        except (Scene3dError, SceneSpecError) as exc:  # 3D не отрисовался — кадр всё равно будет (2D)
            _log.warning("aiv: 3D-блокинг плана %s (%s) не отрисован: %s — 2D-холст", shot["idx"], which, exc)
            marker = f"blocking:{scene_key(plan['built']['scene'])}"
            plan = {**plan, "engine": "2d", "scene": plan["built"]["scene"]}
    if png is None:
        frames, _info = se.render_scene_png(store, plan["scene"], [0.0], w=BLOCKING_W, h=BLOCKING_H, background=None,
                                            width=RENDER_WIDTH)
        png = frames[0]
    dest = assets_dir() / f"{uuid4().hex}-aiv-{kind}.png"
    dest.write_bytes(png)
    meta = probe_asset(dest)
    asset = store.create_montage_asset(
        kind="image", label=f"ИИ-видео: план {shot['idx']} — блокинг {which}"[:120],
        description=f"ИИ-видео: блокинг плана {shot['idx']} ({which}) — 3D-макет композиции"[:500],
        tags="ии-видео", file_path=str(dest), original_filename=dest.name, mime_type="image/png",
        size_bytes=dest.stat().st_size, project_id=int(project_id), **meta)
    return V.add_ref(store, project_id, "shot", int(shot_id), kind, int(asset["id"]), prompt=marker,
                     job_id=(job or {}).get("id"))


def needs_render(store: Any, project_id: int, shot: dict) -> list[str]:
    """Какие кадры блокинга плана надо (пере)рисовать: нет, устарел или сцена другая."""
    out = []
    for which, plan in frame_plans(store, shot).items():
        ref = V.selected_ref(store, project_id, "shot", int(shot["id"]), f"blocking_{which}")
        if not ref or ref["stale"] or not ref.get("asset_id") or ref.get("prompt") != plan["marker"]:
            out.append(which)
    return out
