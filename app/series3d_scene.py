"""3D-серия: геометрия, герой, локации (из ``app/series3d.py``)."""

from __future__ import annotations

import math
import os
import re
from typing import Any

WIDTH, HEIGHT = 1080, 1920
ASPECT = WIDTH / HEIGHT
HERO_ID = "hero"
HERO_HEIGHT = 1.72
CHAR_MODEL = "quaternius-characters/Superhero_Male_FullBody.gltf"
CHAR_MODEL_FEMALE = "quaternius-characters/Superhero_Female_FullBody.gltf"
ANIMATIONS = ["quaternius-animations/UAL1_Standard.glb", "quaternius-animations/UAL2_Standard.glb"]


STYLE = "lowpoly"


# Постобработка 3D-серий (node/scene3d/src/post.ts): мягкое затенение щелей, свечение ламп, «киношная» цветокоррекция —
# бирюзовые тени, тёплые света, приглушённая насыщенность, виньетка и зерно; глубина резкости — ключи камеры dof.
POST = {"ao": 1.0, "aoRadius": 0.3, "aoScale": 0.5, "bloom": {"strength": 0.45, "radius": 0.5, "threshold": 1.4},
        "grade": {"lift": [0.012, 0.02, 0.025], "gamma": [0.98, 1.0, 1.02], "gain": [1.02, 1.0, 0.97],
                  "shadows": "#3d5a82", "highlights": "#d0ab7c", "split": 0.2, "contrast": 1.1, "saturation": 1.0},
        "vignette": 0.5, "grain": 0.045, "ca": 1.4, "dof": True}  # заливка от камеры: при свете / в темноте (лицо читается, тьма остаётся)

# Одна яркая вещь героя — цвета по словам карточки (англ./рус.).
COLORS = {
    "yellow": "#f2c21b", "жёлт": "#f2c21b", "желт": "#f2c21b", "red": "#d3392b", "красн": "#d3392b",
    "orange": "#f07c1f", "оранж": "#f07c1f", "green": "#3f8f4a", "зелён": "#3f8f4a", "зелен": "#3f8f4a",
    "olive": "#5d6b3a", "олив": "#5d6b3a", "khaki": "#7d7a52", "blue": "#2d6fd1", "син": "#2d6fd1",
    "navy": "#23305e", "black": "#1c1d22", "чёрн": "#1c1d22", "черн": "#1c1d22", "white": "#e8e6e1",
    "бел": "#e8e6e1", "grey": "#8a8f98", "gray": "#8a8f98", "сер": "#8a8f98", "brown": "#6b4a32",
    "коричн": "#6b4a32", "pink": "#e86fa0", "розов": "#e86fa0", "purple": "#7a4fb5", "фиолет": "#7a4fb5",
    "beige": "#cdb38f", "бежев": "#cdb38f", "denim": "#3d5a80", "teal": "#2a8c8c",
}
HAIR_COLORS = {"red": "#a8452a", "рыж": "#a8452a", "ginger": "#a8452a", "blond": "#d9b26a", "светл": "#d9b26a",
               "brown": "#4a2f22", "brunette": "#4a2f22", "black": "#1f1b1b", "чёрн": "#1f1b1b", "черн": "#1f1b1b",
               "grey": "#9a9a9a", "gray": "#9a9a9a", "сед": "#9a9a9a", "white": "#dcdcdc", "pink": "#e58fb5"}
HERO_DEFAULT = {"skin": "#e3a985", "hair": "#4a2f22", "hairStyle": "short", "top": "#f2c21b", "coat": True,
                "hood": True, "pants": "#2f3542", "shoes": "#1c1d22", "backpack": "#5d6b3a"}

# Сгенерированный герой (Gemini-картинка → Hunyuan3D-2mini форма → проекция картинки как текстура; node/scene3d/assets):
# статичный меш в A-позе, Y вверх, лицом в +z, единицы меша. Опорные точки суставов сняты по силуэту — по ним движок
# ставит скелет Quaternius в позу меша и считает веса (node/scene3d/src/meshrig.ts). Глянец — жёлтый ПВХ дождевика.
HERO_MESH = {
    "model": "builtin/hero_raincoat.glb",
    "joints": {
        "pelvis": [-0.08, -0.40, 0.0], "spine_01": [-0.08, -0.28, 0.0], "spine_02": [-0.08, -0.08, 0.0],
        "spine_03": [-0.08, 0.10, 0.0], "neck_01": [-0.08, 0.32, 0.0], "Head": [-0.08, 0.43, 0.0],
        "top": [-0.08, 0.96, 0.0],
        "upperarm_l": [0.19, 0.17, 0.0], "lowerarm_l": [0.45, -0.05, -0.01], "hand_l": [0.5, -0.2, -0.01],
        "upperarm_r": [-0.29, 0.17, 0.0], "lowerarm_r": [-0.5, -0.05, -0.01], "hand_r": [-0.57, -0.2, -0.01],
        "thigh_l": [0.09, -0.43, 0.0], "calf_l": [0.10, -0.70, 0.0], "foot_l": [0.11, -0.91, -0.02],
        "ball_l": [0.11, -0.97, 0.12],
        "thigh_r": [-0.25, -0.43, 0.0], "calf_r": [-0.26, -0.70, 0.0], "foot_r": [-0.27, -0.91, -0.02],
        "ball_r": [-0.27, -0.97, 0.12],
        # Граница корпуса для весов рук: подмышка → край подола (что латеральнее — рука).
        "armpit_l": [0.355, 0.1, 0.0], "hem_l": [0.36, -0.45, 0.0],
        "armpit_r": [-0.31, 0.1, 0.0], "hem_r": [-0.40, -0.45, 0.0],
    },
    # Дождевик перекрашивается в чистый ярко-жёлтый (#F5C518): тон ровно жёлтый, насыщенность не ниже 0.9, тёмные
    # «запечённые» тени картинки высветлены — под холодным светом не оливка, в красном — оранжевый, не коричневый.
    "gloss": {"hue": [0.105, 0.155], "roughness": 0.3, "matte": 0.82, "hueTo": 0.125, "hueMix": 1.0, "sat": 1.4,
              "satMin": 0.88, "light": 1.15, "lightMin": 0.45},
    "smooth": 6,
}


class DirectionError(ValueError):
    """Сцену не собрать (нет слов, пустой сценарий)."""


# --------------------------------------------------------------------------- #
# Геометрия: камера и проекция (та же модель, что у three.js PerspectiveCamera)
# --------------------------------------------------------------------------- #


def _sub(a, b):
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def _add(a, b, k=1.0):
    return [a[0] + b[0] * k, a[1] + b[1] * k, a[2] + b[2] * k]


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _norm(a):
    n = math.sqrt(_dot(a, a)) or 1.0
    return [a[0] / n, a[1] / n, a[2] / n]


def _r(v, nd=3):
    return [round(float(x), nd) for x in v]


def project(cam: list[float], target: list[float], fov: float, point: list[float], aspect: float = ASPECT):
    """Точка мира → (x, y) в NDC [-1; 1] кадра камеры (None — за камерой)."""
    f = _norm(_sub(target, cam))
    right = _cross(f, [0.0, 1.0, 0.0])
    if _dot(right, right) < 1e-9:
        right = [1.0, 0.0, 0.0]
    right = _norm(right)
    up = _cross(right, f)
    v = _sub(point, cam)
    z = _dot(v, f)
    if z <= 0.05:
        return None
    th = math.tan(math.radians(fov) / 2)
    return _dot(v, right) / (z * th * aspect), _dot(v, up) / (z * th)


def _yaw_vec(yaw: float) -> list[float]:
    """Направление «вперёд» персонажа с поворотом yaw (0 — вдоль +z, 90 — вдоль +x), как в движке."""
    a = math.radians(yaw)
    return [math.sin(a), 0.0, math.cos(a)]


def _yaw_to(src: list[float], dst: list[float]) -> float:
    return math.degrees(math.atan2(dst[0] - src[0], dst[2] - src[2]))


# --------------------------------------------------------------------------- #
# Герой
# --------------------------------------------------------------------------- #


def _color(text: str, table: dict[str, str]) -> str | None:
    for key, value in table.items():
        if key in text:
            return value
    return None


def hero_look(card: str | None) -> dict:
    """Карточка героя («24, red hair, bright yellow hooded raincoat, olive backpack, dark trousers») →
    внешность игрушки: яркий верх, капюшон/плащ, шапка, рюкзак, штаны, волосы. Чего нет — по умолчанию
    (жёлтый дождевик с капюшоном — по нему героя узнают даже мелким планом)."""
    look = dict(HERO_DEFAULT)
    text = " ".join(str(card or "").lower().replace("ё", "е").split())
    if not text:
        return look
    parts = re.split(r"[,;.]", text)

    def around(words: str) -> tuple[str, str] | None:
        for part in parts:
            m = re.search(words, part)
            if m:
                return part, m.group(0)
        return None

    top = around(r"raincoat|coat|jacket|hoodie|parka|windbreaker|sweater|дождевик|куртк|плащ|худи|пуховик|свитер")
    if top:
        look["top"] = _color(top[0], COLORS) or look["top"]
        look["coat"] = bool(re.search(r"raincoat|coat|parka|дождевик|плащ|пуховик", top[1]))
        look["hood"] = bool(re.search(r"hood|капюш|hoodie|худи|raincoat|дождевик", top[0]))
    hat = around(r"\bhat\b|beanie|\bcap\b|шапк|кепк")
    if hat:
        look["hat"] = _color(hat[0], COLORS) or "#d3392b"
    bag = around(r"backpack|рюкзак")
    look["backpack"] = (_color(bag[0], COLORS) or "#5d6b3a") if bag else False
    pants = around(r"trousers|pants|jeans|штаны|брюки|джинс")
    if pants:
        look["pants"] = _color(pants[0], COLORS) or ("#2f3542" if "dark" in pants[0] or "тёмн" in pants[0] else "#3d5a80")
    hair = around(r"hair|волос|blond|ginger|рыж")
    if hair:
        look["hair"] = _color(hair[0], HAIR_COLORS) or look["hair"]
        if re.search(r"long|длинн|ponytail|хвост", hair[0]):
            look["hairStyle"] = "long"
        elif re.search(r"\bbun\b|пучок", hair[0]):
            look["hairStyle"] = "bun"
        elif re.search(r"bald|лыс", hair[0]):
            look["hairStyle"] = "none"
    if re.search(r"\b(woman|girl|she|female)\b|девушк|женщин|девочк", text):
        look["female"] = True
        look.setdefault("hairStyle", "long")
        if look["hairStyle"] == "short":
            look["hairStyle"] = "long"
    if re.search(r"bald|лыс", text):
        look["hairStyle"] = "none"
    return look


def mesh_hero(look: dict) -> dict | None:
    """Сгенерированный герой (жёлтый дождевик, парень) — меш HERO_MESH на скелете Quaternius. ВЫКЛЮЧЕН по умолчанию:
    владелец забраковал лицо и пластику (2026-11-02) — включается явно (``look["mesh"]`` или ``SERIES3D_HERO_MESH=1``)
    для опытов; по умолчанию — игрушка из деталей (toy)."""
    if not (look.get("mesh") or os.environ.get("SERIES3D_HERO_MESH") == "1"):
        return None
    if look.get("female") or not look.get("coat") or look.get("top") != COLORS["yellow"] or look.get("hat"):
        return None
    return HERO_MESH


def _character(look: dict, path: list[dict], facing: list[dict], actions: list[dict], poses: list[dict]) -> dict:
    toy = {k: v for k, v in look.items() if k != "female"}
    out = {"id": HERO_ID, "model": CHAR_MODEL_FEMALE if look.get("female") else CHAR_MODEL, "height": HERO_HEIGHT,
           "style": "toy", "toy": toy, "animations": list(ANIMATIONS), "path": path, "facing": facing,
           "actions": actions, "poses": poses}
    mesh = mesh_hero(look)
    if mesh:
        out.update({"style": "mesh", "mesh": mesh})
        out.pop("toy")
    return out


# --------------------------------------------------------------------------- #
# Локации (лоу-поли шаблоны из примитивов)
# --------------------------------------------------------------------------- #


def _box(oid: str, pos, size, color, *, rot=None, rounded=0.0, emissive=None, glow=None, metal=0.0, rough=0.8,
         no_shadow=False, hide_above=None, surface=None) -> dict:
    params: dict[str, Any] = {"size": _r(size), "color": color, "roughness": rough, "metalness": metal, "flat": True}
    if surface:
        params["surface"] = surface
    if rounded:
        params["radius"] = rounded
    if emissive:
        params["emissive"] = emissive
        params["emissiveIntensity"] = glow if glow is not None else 1.0
    if no_shadow:
        params["noShadow"] = True
    out = {"id": oid, "primitive": "rounded_box" if rounded else "box", "params": params, "position": _r(pos)}
    if rot:
        out["rotation"] = _r(rot, 2)
    if hide_above is not None:
        out["hideAbove"] = hide_above
    return out


def _cyl(oid: str, pos, diameter, length, color, *, rot=None, metal=0.0, rough=0.6, segments=10) -> dict:
    out = {"id": oid, "primitive": "cylinder", "params": {"size": _r([diameter, length]), "color": color,
                                                         "metalness": metal, "roughness": rough,
                                                         "segments": segments, "flat": True},
           "position": _r(pos)}
    if rot:
        out["rotation"] = _r(rot, 2)
    return out


def _on(wall: str, *objs: dict) -> list[dict]:
    """Вещи на стене: срезана стена по камере — не рисуются и они."""
    return [{**o, "wall": wall} for o in objs]


def elevator_location() -> dict:
    """Кабина лифта 2.0 × 2.2 × 2.5 м в бетонной шахте: стены — листы шлифованной нержавейки со швами, пол —
    резина «монетка», потолок — крашеный металл с панелью холодного света; двери спереди (+z), кнопочная панель
    на правой стене (+x), табло над дверями, поручень, аварийный красный фонарь в углу, люк. Свет и материалы —
    по сгенерированным референсам: холодный бирюзовый ключ сверху, глубокие тени, дымка; в темноте —
    красный аварийный и луч фонарика. Срез стен/потолка по камере — для общего плана сверху."""
    W, D, H = 2.0, 2.2, 2.5
    dark = "#24272c"
    steel = {"kind": "brushed_steel", "color": "#a3a9ae"}
    objects = [
        # Двери: две створки шлифованной стали с щелью, рама вокруг; табло этажа над дверями (смотрит в кабину, −z).
        *_on("front",
             _box("door_l", [-0.27, 0, D / 2 - 0.035], [0.53, 2.12, 0.04], "#a9b0b6", metal=0.9, rough=0.45,
                  surface={**steel, "seed": 5}),
             _box("door_r", [0.27, 0, D / 2 - 0.035], [0.53, 2.12, 0.04], "#a9b0b6", metal=0.9, rough=0.45,
                  surface={**steel, "seed": 6}),
             _box("door_frame_top", [0, 2.12, D / 2 - 0.03], [1.26, 0.1, 0.05], dark, metal=0.6, rough=0.5),
             _box("door_frame_l", [-0.6, 0, D / 2 - 0.03], [0.07, 2.16, 0.05], dark, metal=0.6, rough=0.5),
             _box("door_frame_r", [0.6, 0, D / 2 - 0.03], [0.07, 2.16, 0.05], dark, metal=0.6, rough=0.5),
             _box("door_sill", [0, 0, D / 2 - 0.09], [1.2, 0.012, 0.1], "#8d9297", metal=0.9, rough=0.5),
             {"id": "display", "primitive": "display", "params": {"size": [0.3, 0.15, 0.03], "text": "",
                                                                  "color": "#ff3324", "glow": 2.6},
              "position": [0, 2.2, D / 2 - 0.02], "rotation": [0, 180, 0]}),
        # Кнопочная панель на правой стене (+x), лицом в кабину (−x).
        *_on("right",
             {"id": "panel", "primitive": "button_panel", "params": {"rows": 6, "cols": 2, "size": [0.2, 0.56, 0.025]},
              "position": [W / 2 - 0.02, 0.98, 0.62], "rotation": [0, -90, 0]}),
        # Поручень по задней стене, плинтус.
        *_on("back",
             _cyl("rail", [0, 0.92, -D / 2 + 0.07], 0.035, 1.7, "#c9ced3", rot=[0, 0, 90], metal=1.0, rough=0.22,
                  segments=24),
             _cyl("rail_post_l", [-0.8, 0.86, -D / 2 + 0.03], 0.025, 0.08, "#c9ced3", rot=[90, 0, 0], metal=1.0,
                  rough=0.25, segments=16),
             _cyl("rail_post_r", [0.8, 0.86, -D / 2 + 0.03], 0.025, 0.08, "#c9ced3", rot=[90, 0, 0], metal=1.0,
                  rough=0.25, segments=16),
             _box("kick", [0, 0, -D / 2 + 0.01], [W, 0.12, 0.02], dark, metal=0.7, rough=0.55)),
        *_on("left", _box("kick_l", [-W / 2 + 0.01, 0, 0], [0.02, 0.12, D], dark, metal=0.7, rough=0.55)),
        # Аварийный фонарь в заднем левом углу под потолком: корпус и красное стекло (светится, пока горит свет).
        _box("emergency_lamp", [-W / 2 + 0.16, H - 0.2, -D / 2 + 0.05], [0.16, 0.08, 0.06], "#5a0d08",
             emissive="#ff2a18", glow=3.0, no_shadow=True, rough=0.3),
        _box("emergency_case", [-W / 2 + 0.16, H - 0.215, -D / 2 + 0.025], [0.19, 0.11, 0.03], dark, metal=0.5,
             rough=0.5),
        # Панель холодного света на потолке — гаснет при блэкауте; сверху (общий план) её не видно.
        _box("ceiling_light", [0, H - 0.035, 0], [1.1, 0.03, 0.62], "#f2fbff", rounded=0.012, emissive="#e6f6ff",
             glow=2.4, no_shadow=True, hide_above=H + 0.05),
        _box("ceiling_frame", [0, H - 0.02, 0], [1.18, 0.02, 0.7], dark, metal=0.6, rough=0.5, hide_above=H + 0.05,
             no_shadow=True),
        _box("hatch", [0, H - 0.012, -0.62], [0.6, 0.012, 0.42], "#3a3f46", metal=0.4, rough=0.6,
             hide_above=H + 0.05, no_shadow=True, surface={"kind": "painted_metal", "color": "#4a4f55", "seed": 9}),
        # Шахта: бетонные стены вокруг кабины, тросы и направляющие над крышей.
        _box("shaft_back", [0, -3, -D / 2 - 1.05], [W + 2.2, 10, 0.2], "#4b4a47",
             surface={"kind": "concrete", "color": "#4b4a47", "seed": 3}),
        _box("shaft_left", [-W / 2 - 1.0, -3, 0.3], [0.2, 10, D + 2.4], "#4b4a47",
             surface={"kind": "concrete", "color": "#4b4a47", "seed": 4}),
        _box("shaft_right", [W / 2 + 1.0, -3, 0.3], [0.2, 10, D + 2.4], "#4b4a47",
             surface={"kind": "concrete", "color": "#4b4a47", "seed": 5}),
        # Крыша тени не даёт: верхний свет стоит над потолком (ровнее заливает кабину, без «прожектора» в макушку).
        _box("roof", [0, H + 0.002, 0], [W + 0.3, 0.06, D + 0.3], "#30343a", hide_above=H + 0.05, no_shadow=True,
             surface={"kind": "painted_metal", "color": "#3a3e44", "seed": 2}),
        *[_cyl(f"cable{i}", [x, H + 3.0, -0.1], 0.025, 6.0, "#15171a", segments=8, metal=0.8, rough=0.5)
          for i, x in enumerate((-0.12, 0.0, 0.12))],
    ]
    lights = [
        {"type": "hemisphere", "id": "ambient", "color": "#4a6a78", "ground": "#14110f", "intensity": 0.32},
        {"type": "spot", "id": "main", "color": "#f2f7ff", "intensity": 27, "position": [0, H + 0.55, 0.05],
         "target": [0, 0, 0.1], "angle": 62, "penumbra": 0.85, "decay": 2, "castShadow": True,
         "shadow": {"size": 1024, "bias": -0.0006, "normalBias": 0.02, "radius": 5},
         "beam": {"length": 3.0, "radius": 1.0, "opacity": 0.03}},
        {"type": "point", "id": "fill", "color": "#bfe4e6", "intensity": 1.4, "position": [0, 1.6, -0.6], "decay": 2},
        {"type": "point", "id": "emergency", "color": "#ff2a14", "intensity": 0, "position": [-W / 2 + 0.2, H - 0.3, -D / 2 + 0.15],
         "decay": 1.7, "distance": 6, "level": 2.6, "castShadow": True,
         "shadow": {"size": 512, "bias": -0.003, "normalBias": 0.03, "radius": 6},
         "halo": {"size": 0.55, "opacity": 0.75, "color": "#ff3a22"}},
        # В темноте — холодная заливка (экран телефона, отсвет фонарика от стали): герой читается по цвету и силуэту,
        # красный аварийный — контровой акцент, а не заливка всего кадра.
        {"type": "point", "id": "emergency_fill", "color": "#c4d3ea", "intensity": 0, "position": [0.3, 1.45, 0.85],
         "decay": 1.6, "distance": 4, "level": 1.2},
    ]
    return {
        "name": "elevator", "label": "лифт",
        # Камера может стоять «за стеной» (стена срезается), но внутри шахты.
        "camera_bounds": [-W / 2 - 0.85, W / 2 + 0.85, -D / 2 - 0.9, D / 2 + 1.3, H],
        "room": {"width": W, "depth": D, "height": H, "thickness": 0.08, "style": "lowpoly", "cutaway": True,
                 "floor": {"color": "#26282b", "surface": {"kind": "coin_rubber", "color": "#25272a"}},
                 "walls": {"color": "#8f969d", "surface": {"kind": "steel_panel", "color": "#9097a0", "seed": 11}},
                 "ceiling": {"color": "#c9cdd2", "surface": {"kind": "painted_metal", "color": "#b9bdbf", "seed": 8}},
                 "baseboard": False, "hallway": False},
        "environment": {"background": "#040507", "fog": {"color": "#07080a", "near": 2.6, "far": 10},
                        "exposure": 1.1, "envIntensity": 0.12, "style": "cinematic",
                        "reflections": {"position": [0.0, 1.3, 0.0], "size": 128, "intensity": 0.6},
                        "post": POST},
        "lights": lights, "objects": objects,
        "bounds": [-W / 2, W / 2, -D / 2, D / 2, H],
        # Места героя: x, z, yaw (0 — лицом к дверям, +z).
        "spots": {"center": [0.0, 0.3, 0.0], "doors": [0.0, 0.62, 0.0], "panel": [0.45, 0.55, 90.0],
                  "corner": [-0.52, -0.6, 25.0], "back": [-0.1, -0.3, 0.0]},
        # Точки интереса: позиция и «нормаль» (куда смотрит поверхность — откуда снимать деталь).
        "targets": {"doors": ([0, 1.15, D / 2 - 0.06], [0, 0, -1]), "panel": ([W / 2 - 0.05, 1.25, 0.62], [-1, 0, 0]),
                    "display": ([0, 2.28, D / 2 - 0.05], [0, -0.35, -1]), "hatch": ([0, H - 0.02, -0.62], [0, -1, 0]),
                    "floor": ([0, 0.05, 0.3], [0, 1, 0])},
        "main_lights": ["main", "fill"], "ambient": "ambient", "emergency": "emergency",
        "glow": ["ceiling_light", "display", "panel"], "display": "display", "panel": "panel",
        "doors": ["door_l", "door_r"], "focus": "doors", "start_floor": "", "emergency_lamp": "emergency_lamp",
        # Заливка от камеры слабее: глянец дождевика и сталь не дают «прожектор» в центре кадра.
        "fill_levels": (0.25, 0.75), "fill_color": "#d4e6ff", "phone_light": 1.8,
    }


def apartment_location() -> dict:
    """Квартира: комната 4.2 × 4.6 м с окном (ночной город), входной дверью, диваном, столом и лампой
    (мебель Kenney, лоу-поли)."""
    W, D, H = 4.2, 4.6, 2.7
    objects = [
        {"id": "sofa", "model": "kenney-furniture/loungeSofa.glb", "position": [-1.2, 0, -1.6], "fit": [1.9, 0.85, 0.9],
         "materials": {"*": "#59627a"}},
        {"id": "table", "model": "kenney-furniture/tableCoffee.glb", "position": [-1.2, 0, -0.6], "fit": [1.0, 0.42, 0.55]},
        {"id": "lamp", "model": "kenney-furniture/lampRoundFloor.glb", "position": [-1.85, 0, -1.95],
         "fit": [0.35, 1.5, 0.35]},
        {"id": "shelf", "model": "kenney-furniture/bookcaseOpen.glb", "position": [1.75, 0, -1.4], "rotation": [0, -90, 0],
         "fit": [0.9, 1.8, 0.35]},
        {"id": "shoes", "model": "kenney-furniture/rugDoormat.glb", "position": [1.2, 0, 1.75], "fit": [0.8, 0.02, 0.5]},
        {"id": "display", "primitive": "display", "params": {"size": [0.22, 0.1, 0.03], "text": "", "color": "#58ff7a"},
         "position": [-1.2, 0.43, -0.6]},
        {"id": "panel", "primitive": "button_panel", "params": {"rows": 2, "cols": 1, "size": [0.12, 0.2, 0.02],
                                                                 "alarm": False},
         "position": [1.9, 1.2, 1.2], "rotation": [0, -90, 0]},
    ]
    lights = [
        {"type": "hemisphere", "id": "ambient", "color": "#4d5b80", "ground": "#1d1611", "intensity": 0.5},
        {"type": "point", "id": "main", "color": "#ffd9a6", "intensity": 9, "position": [-1.85, 1.45, -1.9],
         "decay": 2, "castShadow": True},
        {"type": "point", "id": "fill", "color": "#ffe2b8", "intensity": 3.5, "position": [0.6, 2.3, 0.4], "decay": 2},
        {"type": "directional", "id": "moon", "color": "#8fa6e8", "intensity": 0.5, "position": [0.5, 3.4, -5.5],
         "target": [0, 0, 0], "castShadow": True, "shadow": {"size": 2048, "extent": 3.4}},
        {"type": "point", "id": "emergency", "color": "#ff3a24", "intensity": 0, "position": [1.6, 2.2, 1.6], "decay": 2},
    ]
    return {
        "name": "apartment", "label": "квартира",
        "room": {"width": W, "depth": D, "height": H, "style": "lowpoly", "cutaway": True,
                 "floor": {"color": "#6b5442"}, "walls": {"color": "#8d8a86"}, "ceiling": {"color": "#bdb8b0"},
                 "openings": [{"wall": "back", "type": "window", "x": 0.6, "y": 0.9, "width": 1.3, "height": 1.35,
                               "curtains": {"color": "#555d78", "open": 0.7}},
                              {"wall": "front", "type": "door", "x": -1.2, "width": 0.9, "height": 2.08,
                               "door": {"color": "#5e5148", "angle": 0, "hinge": "left"}}],
                 "hallway": {"color": "#3a3530", "light": "#ff9a5a"}},
        "environment": {"background": "#07080c", "fog": {"color": "#0a0b10", "near": 4, "far": 14},
                        "exposure": 1.0, "envIntensity": 0.12, "style": STYLE},
        "lights": lights, "objects": objects,
        "bounds": [-W / 2, W / 2, -D / 2, D / 2, H],
        "spots": {"center": [0.2, 0.3, 180.0], "doors": [1.2, 1.35, 0.0], "panel": [1.45, 1.2, 90.0],
                  "corner": [-1.2, -1.15, 180.0], "back": [0.6, -1.5, 180.0], "window": [0.6, -1.55, 180.0]},
        "targets": {"doors": ([1.2, 1.1, D / 2 - 0.05], [0, 0, -1]), "panel": ([1.95, 1.3, 1.2], [-1, 0, 0]),
                    "display": ([-1.2, 0.5, -0.6], [0, 0.6, 1]), "hatch": ([0, H, 0], [0, -1, 0]),
                    "window": ([0.6, 1.5, -D / 2 + 0.05], [0, 0, 1]), "floor": ([0, 0.05, 0], [0, 1, 0])},
        "main_lights": ["main", "fill"], "ambient": "ambient", "emergency": "emergency",
        "glow": ["display", "panel"], "display": "display", "panel": "panel", "doors": [], "focus": "window",
        "start_floor": "",
    }


_LOCATION_WORDS = (("elevator", r"лифт|кабин|шахт|elevator|lift"),
                   ("apartment", r"квартир|комнат|прихож|дома\b|дом\b|кухн|apartment|room|flat"))


def pick_location(text: str) -> str:
    low = str(text or "").lower()
    for name, pattern in _LOCATION_WORDS:
        if re.search(pattern, low):
            return name
    return "apartment"
