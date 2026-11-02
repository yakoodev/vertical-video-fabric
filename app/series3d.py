"""3D-серии «сериал-катастрофа» на своём движке (node/scene3d): сценарий + тайминги слов озвучки → одна
JSON-сцена движка (функция времени) → mp4. Без видео-нейронок и без платного: всё считается правилами.

Режиссура (``direct``):

* **Локация** — шаблон из примитивов в лоу-поли стиле (``LOCATIONS``): лифт (кабина в шахте) или квартира;
  выбирается по тексту (``pick_location``). У шаблона — места героя (``spots``), точки интереса (кнопки, двери,
  табло, люк, окно), основной и аварийный свет, светящиеся вещи (гаснут при блэкауте).
* **Герой** — игрушка из скруглённых деталей на скелете Quaternius (style ``toy``) с анимациями UAL; одна
  яркая вещь из карточки героя (``hero_look``: «yellow hooded raincoat» → жёлтый дождевик с капюшоном).
* **Кадры** — по фразам озвучки (длинная фраза делится на два кадра по слову); у кадра действие героя по словам
  фразы (кнопки → панель и Interact, фонарик → телефон в руке и луч, двери → толкает створки, сел → присел в
  углу, потолок/люк → смотрит вверх…), место и камера: по умолчанию **близко за спиной на уровне плеч**,
  крупный план лица — на реакции, деталь (кнопки, табло) — когда о ней говорят, общий план — **только сверху**
  (срез потолка). Соседние кадры — разные ракурсы. Камера всегда внутри декорации (проверка
  ``check_shots``: камера не в стене, голова героя в кадре, герой не мелкий).
* **События на словах** — на начале нужного слова: «погас/блэкаут» — основной свет мигнул и погас, табло и
  кнопки потухли, через миг — красный аварийный (щелчок рубильника, гул кабины глохнет); «трясёт/рывок» —
  тряска камеры и лязг; «заскрежетало/скрип» — скрежет металла и лёгкая тряска; «мигает» — мерцание;
  «вспышка/искры» — вспышка; «стук» — стук в металлическую дверь (серия из трёх); «N этаж» и «табло … N» —
  цифры на табло; «Была лишь одна проблема» — акцент. Звуки — только настоящие записи паков (``SFX``); фон-петли
  (``AMBIENCE``): гул кабины, пока горит свет, в темноте — далёкий гул шахты. Тревожную музыку под голос
  кладёт сборка (app/stories_3d.py).

``render_episode`` — сцена → mp4 через ``app.scene3d.render_scene``. Сборка серии (субтитры по 1–3 слова,
HUD-плашки, название, титр «ЧАСТЬ N+1», звуки, голос) — холст-клип сервиса (``app.stories_gen``).
"""

from __future__ import annotations

import contextvars
import math
import re
from typing import Any

FPS = 30
WIDTH, HEIGHT = 1080, 1920
ASPECT = WIDTH / HEIGHT
HERO_ID = "hero"
HERO_HEIGHT = 1.72
CHAR_MODEL = "quaternius-characters/Superhero_Male_FullBody.gltf"
CHAR_MODEL_FEMALE = "quaternius-characters/Superhero_Female_FullBody.gltf"
ANIMATIONS = ["quaternius-animations/UAL1_Standard.glb", "quaternius-animations/UAL2_Standard.glb"]
SHOT_MIN_SEC = 1.4  # фраза короче — в один кадр со следующей
SHOT_SPLIT_SEC = 4.6  # фраза длиннее — два кадра
DETAIL_WORDS = r"^(табло|кнопк|люк|экран)"  # вещь крупно — вставка-деталь со склейкой на этом слове
WALK_SPEED = 1.1  # м/с
CAMERA_MARGIN = 0.16  # камера не ближе к стене (м)
FOV = 52.0
STYLE = "lowpoly"
FILL, FILL_DARK = 0.9, 0.35
DOF = {"face": 1.0, "detail": 1.3, "low": 0.5}  # сила размытия по ракурсу (ключ камеры dof)
# Постобработка 3D-серий (node/scene3d/src/post.ts): мягкое затенение щелей, свечение ламп, «киношная» цветокоррекция —
# бирюзовые тени, тёплые света, приглушённая насыщенность, виньетка и зерно; глубина резкости — ключи камеры dof.
POST = {"ao": 1.0, "aoRadius": 0.3, "aoScale": 0.5, "bloom": {"strength": 0.45, "radius": 0.5, "threshold": 1.4},
        "grade": {"lift": [0.012, 0.02, 0.025], "gamma": [0.98, 1.0, 1.02], "gain": [1.02, 1.0, 0.97],
                  "shadows": "#3f6f78", "highlights": "#c9a27a", "split": 0.25, "contrast": 1.1, "saturation": 1.0},
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
    "gloss": {"hue": [0.105, 0.2], "roughness": 0.3, "matte": 0.82, "hueTo": 0.135, "sat": 1.25},
    "smooth": 6,
}

# Звуки событий — настоящие записи паков (tension — лифт и тревожный фон CC0, freesound — монтажные CC0,
# kenney-impact — удары/шаги CC0).
SFX = {
    "blackout": ("sfx:tension/breaker.ogg", 1.0),  # щелчок рубильника — свет отключился
    "power_down": ("sfx:tension/power-down.ogg", 0.8),  # гул кабины проседает и глохнет
    "jolt": ("sfx:tension/clank.ogg", 1.0),  # рывок кабины — лязг
    "jolt2": ("sfx:kenney-impact/impactMetal_heavy_003.ogg", 0.8),
    "scrape": ("sfx:tension/metal-scrape.ogg", 0.9),  # «заскрежетало» — трос/металл
    "flash": ("sfx:freesound/impact-cinematic.ogg", 0.7),
    "knock": ("sfx:tension/knock-metal.ogg", 1.0),  # стук в металлическую дверь (на записи — три удара)
    "press": ("sfx:freesound/camera-2.ogg", 0.5),
    "sting": ("sfx:freesound/sting.ogg", 0.75),
    "tension": ("sfx:freesound/tension.ogg", 0.45),
    "step": ("sfx:kenney-impact/footstep_concrete_001.ogg", 0.5),
    "door": ("sfx:kenney-impact/impactMetal_medium_002.ogg", 0.7),
}
# Фон-петли локации (пак tension): ``(ref, на сколько дБ тише голоса)``. Гул кабины — пока горит свет, после
# блэкаута — далёкий гул шахты (до «свет загорелся» или до конца).
AMBIENCE = {
    "hum": ("sfx:tension/cabin-hum.ogg", 26.0),
    "dark": ("sfx:tension/shaft-rumble.ogg", 24.0),
}
KNOCKS = 1  # записей стука подряд: на записи уже серия из трёх ударов

_NUM_WORDS = {"двадцать два": 22, "двадцать три": 23, "двадцать один": 21, "тринадцать": 13}


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
    """Готовый сгенерированный герой под карточку: жёлтый дождевик с капюшоном (парень) — меш HERO_MESH на скелете
    Quaternius; для остальных карточек — игрушка из деталей (toy)."""
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
         "decay": 1.2, "distance": 7, "level": 5.0, "castShadow": True,
         "shadow": {"size": 512, "bias": -0.003, "normalBias": 0.03, "radius": 6},
         "halo": {"size": 0.55, "opacity": 0.75, "color": "#ff3a22"}},
        # Отсвет аварийного от дверей/пола: красная заливка лица спереди, пока горит аварийный (как на референсе).
        {"type": "point", "id": "emergency_fill", "color": "#ff3020", "intensity": 0, "position": [0.3, 1.3, 0.85],
         "decay": 1.6, "distance": 4, "level": 1.1},
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
        "fill_levels": (0.25, 0.3), "phone_light": 1.8,
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


LOCATIONS = {"elevator": elevator_location, "apartment": apartment_location}
_LOCATION_WORDS = (("elevator", r"лифт|кабин|шахт|elevator|lift"),
                   ("apartment", r"квартир|комнат|прихож|дома\b|дом\b|кухн|apartment|room|flat"))


def pick_location(text: str) -> str:
    low = str(text or "").lower()
    for name, pattern in _LOCATION_WORDS:
        if re.search(pattern, low):
            return name
    return "apartment"


# --------------------------------------------------------------------------- #
# Слова: фразы по времени, кадры, поиск слова
# --------------------------------------------------------------------------- #


def _wnorm(word: str) -> str:
    return re.sub(r"[^a-zа-я0-9]", "", str(word).lower().replace("ё", "е"))


def align_words(text: str, spoken: list[dict]) -> list[dict]:
    """Слова СЦЕНАРИЯ со временем распознанных (Whisper ошибается: «блокаут», «от облона» вместо «а табло»):
    совпавшие — время слова, заменённые — время куска поровну по длине, пропущенные в речи — короткий миг у
    следующего слова. Субтитры и режиссура идут по тексту сценария, а не по распознанному."""
    import difflib

    tokens = str(text or "").split()
    if not tokens or not spoken:
        return [dict(w) for w in spoken]
    a = [_wnorm(t) for t in tokens]
    b = [_wnorm(w["word"]) for w in spoken]
    out: list[dict | None] = [None] * len(tokens)
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                w = spoken[j1 + k]
                out[i1 + k] = {"word": tokens[i1 + k], "start": float(w["start"]), "end": float(w["end"])}
        elif tag == "replace":
            t0, t1 = float(spoken[j1]["start"]), float(spoken[j2 - 1]["end"])
            weights = [len(x) + 1 for x in a[i1:i2]]
            acc = t0
            for k, wt in enumerate(weights):
                d = (t1 - t0) * wt / sum(weights)
                out[i1 + k] = {"word": tokens[i1 + k], "start": round(acc, 3), "end": round(acc + d, 3)}
                acc += d
    # Пропущенные в речи (delete) — вплотную к соседям.
    for k in range(len(tokens)):
        if out[k] is None:
            prev_end = out[k - 1]["end"] if k and out[k - 1] else float(spoken[0]["start"])
            nxt = next((out[j]["start"] for j in range(k + 1, len(tokens)) if out[j]), prev_end + 0.12)
            out[k] = {"word": tokens[k], "start": round(prev_end, 3), "end": round(max(prev_end, min(nxt, prev_end + 0.12)), 3)}
    res: list[dict] = []
    last = 0.0
    for w in out:
        s = max(last, float(w["start"]))
        e = max(s + 0.02, float(w["end"]))
        res.append({"word": w["word"], "start": round(s, 3), "end": round(e, 3)})
        last = s
    return res


def sentence_spans(sentences: list[dict], words: list[dict], total: float) -> list[dict]:
    """Фраза → [t0, t1) по словам озвучки и диапазон слов [w0, w1]."""
    from app.explainer_gen import align

    if not words:
        raise DirectionError("нет слов озвучки")
    firsts = align([{"text": s["text"]} for s in sentences], words)
    out = []
    for n, s in enumerate(sentences):
        w0 = firsts[n]
        w1 = (firsts[n + 1] - 1) if n + 1 < len(sentences) else len(words) - 1
        w1 = max(w0, w1)
        t0 = 0.0 if n == 0 else float(words[w0]["start"])
        t1 = float(words[firsts[n + 1]]["start"]) if n + 1 < len(sentences) else float(total)
        out.append({"i": n, "text": s["text"], "role": s.get("role") or "", "hud": s.get("hud") or "", "t0": t0,
                    "t1": max(t0 + 0.2, t1), "w0": w0, "w1": w1})
    return out


def split_shots(spans: list[dict], words: list[dict]) -> list[dict]:
    """Кадры: фраза = кадр; короткая (< 1.4 с) — вместе со следующей; длинная (> 4.6 с) — два кадра, разрез
    на слове у середины (лучше после запятой)."""
    merged: list[dict] = []
    for sp in spans:
        if merged and merged[-1]["t1"] - merged[-1]["t0"] < SHOT_MIN_SEC:
            prev = merged[-1]
            prev.update({"t1": sp["t1"], "w1": sp["w1"], "text": f"{prev['text']} {sp['text']}",
                         "sentences": prev["sentences"] + [sp["i"]], "role": prev["role"] or sp["role"]})
            continue
        merged.append({**sp, "sentences": [sp["i"]]})
    shots: list[dict] = []
    for sp in merged:
        length = sp["t1"] - sp["t0"]
        inner = [k for k in range(sp["w0"] + 1, sp["w1"] + 1) if sp["t0"] + 1.6 < words[k]["start"] < sp["t1"] - 1.6]
        # Вещь крупно (табло, кнопки, люк) упомянута во второй половине — склейка на ней: вставка-деталь.
        detail = [k for k in range(sp["w0"] + 1, sp["w1"] + 1)
                  if sp["t0"] + 1.2 < words[k]["start"] < sp["t1"] - 1.0 and re.search(DETAIL_WORDS, _wnorm(words[k]["word"]))]
        if length > 2.8 and detail:
            k0 = detail[0]
            # Склейка — на начале фразы/словосочетания с вещью («А табло…»), не посреди него.
            while k0 - 1 > sp["w0"] and words[k0 - 1]["start"] > sp["t0"] + 1.2 and not \
                    str(words[k0 - 1]["word"]).rstrip().endswith((".", ",", "!", "?", "…", ":")) and \
                    len(_wnorm(words[k0 - 1]["word"])) <= 3:
                k0 -= 1
            inner = [k0]
        if (length > SHOT_SPLIT_SEC or (length > 2.8 and detail)) and inner:
            mid = sp["t0"] + length / 2

            def cost(k: int) -> float:
                comma = str(words[k - 1]["word"]).rstrip().endswith((",", "—", ":", ";", "."))
                return abs(words[k]["start"] - mid) - (0.6 if comma else 0.0)

            k = min(inner, key=cost)
            cut = float(words[k]["start"])
            first_text = " ".join(str(w["word"]) for w in words[sp["w0"]:k])
            second_text = " ".join(str(w["word"]) for w in words[k:sp["w1"] + 1])
            shots.append({**sp, "t1": cut, "w1": k - 1, "part": 0, "spoken": first_text})
            shots.append({**sp, "t0": cut, "w0": k, "part": 1, "spoken": second_text})
        else:
            shots.append({**sp, "part": 0, "spoken": " ".join(str(w["word"]) for w in words[sp["w0"]:sp["w1"] + 1])})
    for n, s in enumerate(shots):
        s["n"] = n
    return shots


def find_word(words: list[dict], w0: int, w1: int, pattern: str) -> int | None:
    """Индекс первого слова в [w0, w1], чья нормализованная форма подходит под ``pattern`` (основа)."""
    rx = re.compile(pattern)
    for k in range(max(0, w0), min(len(words) - 1, w1) + 1):
        if rx.search(_wnorm(words[k]["word"])):
            return k
    return None


# --------------------------------------------------------------------------- #
# Действия героя и события
# --------------------------------------------------------------------------- #

# (имя, основы слов, клип, место, точка интереса, разовый клип → потом покой)
ACTIONS = [
    ("phone_light", r"фонар|подсвет|экран\w* телефон|свет\w* телефон", "Idle_Lantern_Loop", "center", "doors", False),
    ("call", r"позвон|звонит|набра|номер", "Idle_TalkingPhone_Loop", "back", "doors", False),
    ("press", r"кнопк|нажа|нажим|жмет|жал|вызов|диспетчер", "Interact", "panel", "panel", True),
    # Стучит сам герой («стучит в двери»); «в двери постучали» снаружи — он слушает (listen).
    ("knock", r"(стучит|стучал|колотит|колотил|барабанит)\s+(в|по)", "Punch_Jab", "doors", "doors", True),
    ("look_up", r"потол|люк|сверху|наверх|над голов|крыш|трос", "Idle_Loop", "center", "hatch", False),
    ("listen", r"слыш|слуша|тишин|шорох|скреж|голос|шаг|постуч|стук", "Idle_Loop", "center", "doors", False),
    ("sit", r"\bсел\b|сел[аи]?\b|сидит|сидел|присел|на пол", "Crouch_Idle_Loop", "corner", "doors", False),
    ("jolt", r"тряс|дерн|рывок|тряхн|качну|пада|рухну|провали", "Hit_Chest", "center", "doors", True),
    ("display", r"табло|цифр|экран|показыва", "Idle_Loop", "center", "display", False),
    ("doors", r"двер|створ|раздвин|щел", "Push_Loop", "doors", "doors", False),
    ("wait", r"ждал|ждет|ждать|прошл\w* \d|минут|час", "Idle_FoldArms_Loop", "back", "doors", False),
]
ONE_SHOT_SEC = {"Interact": 1.6, "Punch_Jab": 0.8, "Hit_Chest": 1.0}
PHONE_CLIP = "Idle_Lantern_Loop"
PHONE_LIGHT = 6.0  # луч фонарика телефона (кд)  # рука с телефоном вперёд на уровне груди — луч фонарика туда, куда смотрит герой
HEAD_R = 0.16  # от центра головы до макушки (голова игрушки крупнее настоящей)
# Голова героя в позе клипа: (высота центра, вынос вперёд), м — замер движком (персонаж 1.72 м, игрушка).
CLIP_HEAD = {"Idle_Loop": (1.6, 0.02), "Crouch_Idle_Loop": (0.92, 0.21), "Push_Loop": (1.22, 0.18),
             "Sitting_Idle_Loop": (1.26, -0.15), "Punch_Jab": (1.54, 0.03)}

# (событие, основы слов)
EVENTS = [
    ("blackout", r"^(погас|отключ|обесточ|темнот|блэкаут|блекаут|вырубил|отруб)"),
    ("flicker", r"^(мига|мерца|моргн|замига)"),
    ("scrape", r"^(за|про)?(скреж|скрип|скреб)"),
    ("jolt", r"^(за|по)?(тряс|дерн|рывок|тряхн|качнул|лязг|грохот|удар|пада|рухну|провали)"),
    ("flash", r"^(вспыш|искр|вспыхн)"),
    ("knock", r"^(стук|стуч|постуча)"),
    ("alarm", r"^(тревог|сирен|аварийн)"),
]


def _action_for(text: str, role: str) -> tuple:
    low = text.lower().replace("ё", "е")
    for act in ACTIONS:
        if re.search(act[1], low):
            return act
    return ("idle", "", "Idle_Loop", "center", None, False)


def detect_events(spans: list[dict], words: list[dict]) -> list[dict]:
    """События на словах: ``{kind, t, word, sentence}``; блэкаут из хука («Если блэкаут застал вас…») — только
    если дальше по сюжету свет не гаснет; цифры этажа на табло; акцент «Была лишь одна проблема»."""
    events: list[dict] = []
    for sp in spans:
        for k in range(sp["w0"], sp["w1"] + 1):
            w = _wnorm(words[k]["word"])
            for kind, pattern in EVENTS:
                if re.search(pattern, w):
                    events.append({"kind": kind, "t": float(words[k]["start"]), "word": words[k]["word"],
                                   "sentence": sp["i"], "role": sp["role"]})
                    break
        low = sp["text"].lower().replace("ё", "е")
        # Цифры на табло: «на 22 этаже», «табло показывало 23».
        for m in re.finditer(r"(\d{1,3})", low):
            tail, head = low[m.end():m.end() + 14], low[max(0, m.start() - 30):m.start()]
            if re.search(r"^\s*(-?[а-я]{0,3})?\s*этаж", tail) or re.search(r"табло|экран|показыва|цифр", head):
                k = find_word(words, sp["w0"], sp["w1"], rf"^{m.group(1)}")
                t = float(words[k]["start"]) if k is not None else sp["t0"]
                events.append({"kind": "floor", "t": t, "value": m.group(1), "sentence": sp["i"], "role": sp["role"]})
        # Свет вернулся — только «свет загорелся/включили/дали» (а не «включил фонарик»).
        m = re.search(r"свет\w*\s+(снова\s+|опять\s+)?(загорел|включил|вернул|дали|появил)", low)
        if m:
            k = find_word(words, sp["w0"], sp["w1"], r"^(загорел|включил|вернул|дали|появил)")
            events.append({"kind": "light_back", "t": float(words[k]["start"]) if k is not None else sp["t0"],
                           "sentence": sp["i"], "role": sp["role"]})
        if re.search(r"одна проблем|лишь одн", low):
            k = find_word(words, sp["w0"], sp["w1"], r"^(был|одн|лишь)")
            events.append({"kind": "sting", "t": float(words[k]["start"]) if k is not None else sp["t0"],
                           "sentence": sp["i"], "role": sp["role"]})
    blackouts = [e for e in events if e["kind"] == "blackout"]
    story = [e for e in blackouts if e["role"] != "hook"]
    keep = (story or blackouts)[:1]
    events = [e for e in events if e["kind"] != "blackout" or e in keep]
    # Повторы одного события ближе 1.2 с — один.
    out: list[dict] = []
    for e in sorted(events, key=lambda x: x["t"]):
        if any(o["kind"] == e["kind"] and abs(o["t"] - e["t"]) < 1.2 and e["kind"] != "floor" for o in out):
            continue
        out.append(e)
    return out


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


def _camera_for(kind: str, spot: list[float], focus: list[float] | None, loc: dict, side: int, clip: str | None) -> dict | None:
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
        if crouch and big > 1.2:
            # Сгенерированный герой сидя опускает голову — снимаем ниже, чтобы видеть лицо, а не макушку.
            lift, drop = -0.22, -0.02
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
        if not _check(kind, c, spot, loc, clip):
            return c
    return None


def _behind_its_wall(cam: list[float], thing: list[float], loc: dict) -> bool:
    """Камера за той стеной, на которой висит вещь (стена срезается по камере — вещь пропала бы с ней)."""
    x0, x1, z0, z1, _h = loc["bounds"]
    near = 0.2
    return ((cam[2] > z1 and thing[2] > z1 - near) or (cam[2] < z0 and thing[2] < z0 + near)
            or (cam[0] > x1 and thing[0] > x1 - near) or (cam[0] < x0 and thing[0] < x0 + near))


def _check(kind: str, cam: dict, spot: list[float], loc: dict, clip: str | None = None) -> list[str]:
    """Проблемы ракурса: камера в стене/в герое, голова вне кадра, герой мелкий, деталь закрыта героем."""
    problems = []
    for p in (cam["position"], cam.get("end") or cam["position"]):
        if not camera_ok(p, loc, cam["free"]):
            problems.append("камера в стене")
            break
    if not cam["free"] and math.hypot(cam["position"][0] - spot[0], cam["position"][2] - spot[1]) < 0.5:
        problems.append("камера в герое")
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


# --------------------------------------------------------------------------- #
# Режиссура
# --------------------------------------------------------------------------- #


def _kinds_for(shots: list[dict], acts: list[tuple]) -> list[str]:
    """Ракурсы по ролям и действиям: первый кадр — общий сверху; деталь, когда говорят о вещи; реакция —
    лицо; финал — лицо, потом деталь; по умолчанию — за спиной. Соседние — разные."""
    out = []
    for n, (shot, act) in enumerate(zip(shots, acts)):
        name = act[0]
        role = shot.get("role") or ""
        if n == 0:
            prefs = ["overhead"]
        elif name in ("doors", "knock"):
            # Только из кабины: камера за срезанной стеной спрятала бы и сами двери — герой толкал бы воздух.
            prefs = ["ots"]
        elif name == "display":
            prefs = ["detail", "ots"]
        elif name == "press":
            prefs = ["detail", "ots"] if shot.get("part") == 1 else ["ots", "detail", "face"]
        elif name == "look_up":
            prefs = ["low", "face", "ots"]
        elif name in ("jolt", "listen") or role == "mistake":
            prefs = ["face", "ots", "low"]
        elif name == "sit":
            prefs = ["face", "overhead", "low"]
        elif role == "finale":
            prefs = ["face", "ots"]
        else:
            prefs = ["ots", "face"]
        out.append(prefs)
    return out


def _order(prefs: list[str], history: list[str]) -> list[str]:
    """Порядок ракурсов кадра: свои предпочтения, но не как у прошлого кадра (за спиной — не больше двух
    подряд), потом запасные."""
    prev = history[-1] if history else None
    streak = len(history) >= 2 and history[-1] == history[-2] == "ots"
    first = [k for k in prefs if k != prev or (k == "ots" and not streak)]
    rest = [k for k in ("ots", "face", "low", "overhead") if k not in first]
    return first + [k for k in prefs if k not in first] + rest


def _light_keys(base: float, off_at: float | None, back_at: float | None = None) -> list[dict] | None:
    if off_at is None:
        return None
    keys = [{"t": 0, "intensity": base}, {"t": round(off_at, 3), "intensity": base},
            {"t": round(off_at + 0.04, 3), "intensity": 0.0, "ease": "linear"}]
    if back_at is not None and back_at > off_at:
        keys += [{"t": round(back_at, 3), "intensity": 0.0}, {"t": round(back_at + 0.25, 3), "intensity": base}]
    return keys


def direct(script: dict, words: list[dict], *, total: float | None = None, look: dict | None = None,
           location: str | None = None, fps: int = FPS) -> dict:
    """Сценарий + слова озвучки → ``{spec, shots, events, sfx, location, checks}``. ``spec`` — сцена
    движка на всю серию (``total`` секунд), ``sfx`` — звуки событий ``[{t, ref, volume, label}]``."""
    look = look or hero_look("")
    token = _HEAD.set(_head_for(look))
    try:
        return _direct(script, words, total=total, look=look, location=location, fps=fps)
    finally:
        _HEAD.reset(token)


def _direct(script: dict, words: list[dict], *, total: float | None, look: dict, location: str | None,
            fps: int) -> dict:
    sentences = [s for s in (script.get("sentences") or []) if str(s.get("text") or "").strip()]
    if not sentences:
        raise DirectionError("в сценарии нет фраз")
    if not words:
        raise DirectionError("нет слов озвучки")
    total = float(total or (float(words[-1]["end"]) + 2.0))
    text = " ".join([str(script.get("title") or ""), *[s["text"] for s in sentences]])
    loc_name = location if location in LOCATIONS else pick_location(text)
    loc = LOCATIONS[loc_name]()
    look = look or hero_look("")
    words = align_words(" ".join(s["text"] for s in sentences), words)
    spans = sentence_spans(sentences, words, total)
    shots = split_shots(spans, words)
    events = detect_events(spans, words)
    acts = [_action_for(s.get("spoken") or s["text"], s.get("role") or "") for s in shots]
    # Финал после вставки «табло/вещь» без своего действия — камера остаётся на вещи (жуткая деталь держится).
    n = 1
    while n < len(shots):
        if shots[n].get("role") == "finale" and acts[n][0] == "idle" and acts[n - 1][0] == "display":
            shots[n - 1] = {**shots[n - 1], "t1": shots[n]["t1"], "w1": shots[n]["w1"],
                            "spoken": f"{shots[n - 1].get('spoken') or ''} {shots[n].get('spoken') or ''}".strip()}
            del shots[n], acts[n]
            continue
        n += 1
    for k, s in enumerate(shots):
        s["n"] = k
    kinds = _kinds_for(shots, acts)

    blackout = next((e["t"] for e in events if e["kind"] == "blackout"), None)
    light_back = next((e["t"] for e in events if e["kind"] == "light_back" and blackout and e["t"] > blackout), None)
    dark = (lambda t: blackout is not None and t >= blackout and (light_back is None or t < light_back))

    # Герой: путь по местам кадров, действия и позы.
    path: list[dict] = []
    facing: list[dict] = []
    actions: list[dict] = []
    poses: list[dict] = []
    phone_windows: list[list[float]] = []
    prev_spot = None
    shot_out: list[dict] = []
    checks: list[str] = []
    notes: list[str] = []
    side = 1
    for n, (shot, act, kind) in enumerate(zip(shots, acts, kinds)):
        name, _pat, clip, spot_name, focus_name, one_shot = act
        t0, t1 = shot["t0"], shot["t1"]
        if n + 1 < len(shots):
            t1 = shots[n + 1]["t0"]
        else:
            t1 = total
        # Темно и действие «фонарик/звонок» не названо — герой всё равно светит телефоном (иначе ничего не видно);
        # смотрит вверх/слушает — с телефоном в руке.
        phone = name in ("phone_light", "call")
        if dark(t0 + 0.3) and name in ("idle", "listen", "look_up", "display", "wait"):
            phone, clip = True, PHONE_CLIP
            if name in ("idle", "wait"):
                name = "phone_light"
        spot = list(loc["spots"].get(spot_name) or loc["spots"]["center"])
        if focus_name in loc["targets"] and spot_name in ("center", "back") and name not in ("sit",):
            # Лицом к точке интереса.
            fp = loc["targets"][focus_name][0]
            spot[2] = round(_yaw_to([spot[0], 0, spot[1]], fp), 1)
        walk = 0.0
        if prev_spot is None:
            path.append({"t": 0, "p": [spot[0], spot[1]]})
            facing.append({"t": 0, "yaw": spot[2], "dur": 0.01})
        elif abs(prev_spot[0] - spot[0]) + abs(prev_spot[1] - spot[1]) > 0.05:
            dist = math.hypot(spot[0] - prev_spot[0], spot[1] - prev_spot[1])
            walk = max(0.6, min(1.6, dist / WALK_SPEED))
            path.append({"t": round(t0, 3), "p": [prev_spot[0], prev_spot[1]]})
            path.append({"t": round(t0 + walk, 3), "p": [spot[0], spot[1]]})
            facing.append({"t": round(t0, 3), "auto": True, "dur": 0.25})
            facing.append({"t": round(t0 + walk - 0.15, 3), "yaw": spot[2], "dur": 0.4})
            actions.append({"clip": "Walk_Loop", "start": round(t0, 3), "end": round(t0 + walk + 0.1, 3), "fade": 0.2})
        elif abs(((prev_spot[2] - spot[2]) + 180) % 360 - 180) > 3:
            facing.append({"t": round(t0, 3), "yaw": spot[2], "dur": 0.45})
        a0 = t0 + walk
        if one_shot:
            dur = ONE_SHOT_SEC.get(clip, 1.2)
            reps = 3 if clip == "Punch_Jab" else 1
            for r in range(reps):
                actions.append({"clip": clip, "start": round(a0 + r * dur * 0.8, 3), "end": round(a0 + (r + 1) * dur * 0.8 + 0.1, 3),
                                "fade": 0.18})
            actions.append({"clip": "Idle_Loop", "start": round(a0 + reps * dur * 0.8, 3), "end": round(t1 + 0.4, 3),
                            "fade": 0.35})
        else:
            actions.append({"clip": clip, "start": round(a0, 3), "end": round(t1 + 0.4, 3), "fade": 0.3,
                            "offset": round((n * 0.37) % 1.0, 2)})
        if name == "look_up":
            poses.append({"start": round(a0, 3), "end": round(t1, 3), "fade": 0.4, "rotate": {"neck_01": [-28, 0, 0],
                                                                                            "Head": [-18, 0, 0]}})
        elif name == "listen":
            poses.append({"start": round(a0, 3), "end": round(t1, 3), "fade": 0.4, "rotate": {"Head": [6, 0, 14]}})
        elif name == "sit" and mesh_hero(look):
            # Тяжёлая голова сгенерированного героя в клипе «присел» уходит в грудь — приподнять: лицо в кадре.
            poses.append({"start": round(a0, 3), "end": round(t1, 3), "fade": 0.4,
                          "rotate": {"spine_03": [-18, 0, 0], "neck_01": [-24, 0, 0], "Head": [-12, 0, 0]}})
        if phone:
            phone_windows.append([a0, t1])
        prev_spot = spot

        focus = loc["targets"][focus_name][0] if focus_name in loc["targets"] else None
        cam = None
        order = _order(kind, [s["camera"] for s in shot_out])
        wanted = order[0]
        for k in order:
            cam = _camera_for(k, spot, focus, loc, side, clip)
            if cam is not None:
                kind = k
                break
        if cam is None:
            # Не встал ни один ракурс с проверками — общий сверху без проверки (над декорацией стена не мешает).
            h = loc["bounds"][4]
            pos = [spot[0], h + 2.4, spot[1] + 0.6]
            tgt = [spot[0], 0.5, spot[1]]
            cam = {"position": pos, "target": tgt, "fov": 50.0, "end": pos, "end_target": tgt, "free": True}
            kind = "overhead"
            checks.append(f"кадр {n}: ни один ракурс не прошёл проверку — общий сверху")
        elif kind != wanted:
            notes.append(f"кадр {n}: ракурс {wanted} не встал — взят {kind}")
        side = -side
        shot_out.append({"n": n, "t0": round(t0, 3), "t1": round(t1, 3), "text": shot.get("spoken") or shot["text"],
                         "sentences": shot["sentences"], "role": shot.get("role") or "", "action": name, "clip": clip,
                         "spot": spot_name, "hero": _r(spot, 2), "focus": focus_name, "camera": kind, "position": _r(cam["position"]),
                         "target": _r(cam["target"]), "fov": cam["fov"], "free": cam["free"]})
        shot_out[-1]["_cam"] = cam

    # Камера: ключи кадров со склейками.
    cam_keys: list[dict] = []
    for s in shot_out:
        cam = s.pop("_cam")
        k0 = {"t": s["t0"], "position": _r(cam["position"]), "target": _r(cam["target"]), "fov": cam["fov"]}
        if cam_keys:
            k0["cut"] = True
        if cam["free"]:
            k0["free"] = True
        k1 = {"t": s["t1"], "position": _r(cam["end"]), "target": _r(cam["end_target"]), "fov": cam["fov"],
              "ease": "inOutSine"}
        if cam["free"]:
            k1["free"] = True
        # Глубина резкости (post.dof): на крупных — фон мягко размыт, фокус — на цели кадра.
        if DOF.get(s["camera"]):
            k0["dof"] = k1["dof"] = DOF[s["camera"]]
        cam_keys += [k0, k1]

    # Свет, табло, кнопки по событиям.
    lights = [dict(x) for x in loc["lights"]]
    by_id = {x.get("id"): x for x in lights}
    shake: list[dict] = []
    sfx: list[dict] = []

    def add_sfx(kind: str, t: float, label: str) -> None:
        if kind in SFX and t < total - 0.2:
            ref, vol = SFX[kind]
            sfx.append({"t": round(max(0.0, t), 3), "ref": ref, "volume": vol, "label": label, "kind": kind})

    for e in events:
        t = e["t"]
        if e["kind"] == "blackout":
            for lid in loc["main_lights"]:
                light = by_id[lid]
                light["flicker"] = [{"t": round(max(0, t - 0.45), 3), "amount": 0.85, "duration": 0.45}]
                light["keys"] = _light_keys(float(light["intensity"]), t, light_back)
            amb = by_id[loc["ambient"]]
            amb["keys"] = [{"t": 0, "intensity": amb["intensity"]}, {"t": round(t, 3), "intensity": amb["intensity"]},
                           {"t": round(t + 0.05, 3), "intensity": 0.16, "ease": "linear"}]
            emer = by_id[loc["emergency"]]
            emer["keys"] = [{"t": 0, "intensity": 0}, {"t": round(t + 0.7, 3), "intensity": 0},
                            {"t": round(t + 1.0, 3), "intensity": float(emer.get("level") or 0.8), "ease": "linear"}]
            emer["flicker"] = [{"t": round(t + 0.7, 3), "amount": 0.7, "duration": 0.6}]
            if by_id.get("emergency_fill"):
                ef = by_id["emergency_fill"]
                # Сразу после «погас» — тусклый отсвет (кадр не уходит в чёрное: проверка чёрных кадров), потом —
                # в полную силу вместе с аварийным.
                lv = float(ef.get("level") or 1.0)
                ef["keys"] = [{"t": 0, "intensity": 0}, {"t": round(t, 3), "intensity": 0},
                              {"t": round(t + 0.04, 3), "intensity": round(lv * 0.5, 3), "ease": "linear"},
                              {"t": round(t + 0.7, 3), "intensity": round(lv * 0.5, 3)},
                              {"t": round(t + 1.0, 3), "intensity": lv, "ease": "linear"}]
                if light_back:
                    ef["keys"] += [{"t": round(light_back, 3), "intensity": float(ef.get("level") or 1.0)},
                                   {"t": round(light_back + 0.2, 3), "intensity": 0}]
            shake.append({"t": round(t, 3), "amp": 0.012, "duration": 0.6})
            add_sfx("blackout", t - 0.05, "блэкаут")
            add_sfx("power_down", t, "гул глохнет")
        elif e["kind"] == "flicker":
            for lid in loc["main_lights"]:
                by_id[lid].setdefault("flicker", []).append({"t": round(t, 3), "amount": 0.75, "duration": 0.9})
        elif e["kind"] == "jolt":
            shake.append({"t": round(t, 3), "amp": 0.035, "duration": 0.9})
            for lid in loc["main_lights"] + [loc["emergency"]]:
                by_id[lid].setdefault("flicker", []).append({"t": round(t, 3), "amount": 0.6, "duration": 0.5})
            n_jolts = len([x for x in sfx if x["kind"] in ("jolt", "jolt2")])
            add_sfx("jolt" if n_jolts % 2 == 0 else "jolt2", t, "рывок")
        elif e["kind"] == "scrape":
            shake.append({"t": round(t, 3), "amp": 0.01, "duration": 1.2})
            add_sfx("scrape", t, "скрежет")
        elif e["kind"] == "flash":
            for lid in loc["main_lights"][:1]:
                light = by_id[lid]
                base = 0.0 if dark(t) else float(light["intensity"])
                light.setdefault("pulses", []).append(t)
                light["keys"] = (light.get("keys") or [{"t": 0, "intensity": float(light["intensity"])}]) + [
                    {"t": round(t, 3), "intensity": base}, {"t": round(t + 0.05, 3), "intensity": 90.0, "ease": "linear"},
                    {"t": round(t + 0.35, 3), "intensity": base, "ease": "outQuad"}]
            add_sfx("flash", t, "вспышка")
        elif e["kind"] == "knock":
            shake.append({"t": round(t, 3), "amp": 0.008, "duration": 0.4})
            for r in range(KNOCKS):
                add_sfx("knock", t + r * 1.2, "стук")
        elif e["kind"] == "sting":
            add_sfx("sting", t, "акцент")
    for light in lights:
        light.pop("pulses", None)
        light.pop("level", None)
        if light.get("keys"):
            light["keys"] = sorted(light["keys"], key=lambda k: k["t"])

    objects = [dict(o) for o in loc["objects"]]
    obj_by_id = {o["id"]: o for o in objects}
    # Табло: стартовый этаж — первая цифра этажа в серии (обычно из хука), дальше — по словам.
    floors = [e for e in events if e["kind"] == "floor"]
    disp = obj_by_id.get(loc["display"])
    if disp is not None:
        state = [{"t": 0, "text": floors[0]["value"] if floors else loc.get("start_floor") or "", "glow": 1}]
        for e in floors[1:]:
            state.append({"t": round(e["t"], 3), "text": e["value"]})
        if blackout is not None:
            state.append({"t": round(blackout + 0.04, 3), "glow": 0.0})
            # Табло в темноте «оживает» само, если в серии меняется цифра (жуткая деталь).
            for e in floors[1:]:
                if e["t"] > blackout:
                    state.append({"t": round(e["t"] - 0.02, 3), "glow": 1.0})
        disp["state"] = sorted(state, key=lambda k: k["t"])
    for oid in loc["glow"]:
        o = obj_by_id.get(oid)
        if o is None or oid == loc["display"]:
            continue
        st = [{"t": 0, "glow": 1}]
        if blackout is not None:
            st.append({"t": round(blackout + 0.04, 3), "glow": 0.0})
            if light_back:
                st.append({"t": round(light_back + 0.2, 3), "glow": 1.0})
        o["state"] = st
    # Стекло аварийного фонаря светится вместе с его светом (зажигается после блэкаута, гаснет со светом).
    lamp = obj_by_id.get(loc.get("emergency_lamp") or "")
    if lamp is not None:
        st = [{"t": 0, "glow": 0.0}]
        if blackout is not None:
            st += [{"t": round(blackout + 0.85, 3), "glow": 1.0}]
            if light_back:
                st.append({"t": round(light_back + 0.2, 3), "glow": 0.0})
        lamp["state"] = st
    panel = obj_by_id.get(loc["panel"])
    if panel is not None:
        presses = [s for s in shot_out if s["action"] == "press"]
        st = list(panel.get("state") or [{"t": 0, "glow": 1}])
        for s in presses:
            st.append({"t": round(s["t0"] + 0.7, 3), "alarm": True, "lit": [9]})
            add_sfx("press", s["t0"] + 0.7, "кнопка")
        if any(e["kind"] == "alarm" for e in events):
            t = next(e["t"] for e in events if e["kind"] == "alarm")
            st.append({"t": round(t, 3), "alarm": True})
        panel["state"] = sorted(st, key=lambda k: k["t"])
    # Двери: «раздвинуть» — створки на 4 см в стороны, пока герой толкает.
    for s in shot_out:
        if s["action"] == "doors" and loc["doors"]:
            for oid, dx in zip(loc["doors"], (-1, 1)):
                o = obj_by_id[oid]
                base = o["position"]
                keys = list(o.get("keys") or [{"t": 0, "position": base}])
                keys += [{"t": round(s["t0"] + 0.9, 3), "position": base},
                         {"t": round(s["t0"] + 1.8, 3), "position": _r([base[0] + dx * 0.045, base[1], base[2]]),
                          "ease": "outQuad"},
                         {"t": round(s["t1"] + 0.3, 3), "position": _r([base[0] + dx * 0.045, base[1], base[2]])}]
                o["keys"] = keys
            add_sfx("door", s["t0"] + 1.0, "створки")
    # Телефон в руке и луч фонарика — пока герой светит.
    if phone_windows:
        merged: list[list[float]] = []
        for a, b in sorted(phone_windows):
            if merged and a - merged[-1][1] < 0.6:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        objects.append({"id": "phone", "primitive": "box", "params": {"size": [0.075, 0.15, 0.012], "color": "#121418",
                                                                       "emissive": "#9fc6ff", "emissiveIntensity": 0.06,
                                                                       "roughness": 0.25, "metalness": 0.4},
                        "position": [loc["spots"]["corner"][0], -0.5, loc["spots"]["corner"][1]],
                        "attach": [{"to": f"{HERO_ID}:hand_r", "start": round(a, 3), "end": round(b, 3), "fadeIn": 0.05,
                                    "fadeOut": 0.05, "offset": [0, 0.04, 0.05], "settle": False} for a, b in merged],
                        "visible": [{"t": 0, "on": False}] + [x for a, b in merged for x in
                                                              ({"t": round(a, 3), "on": True}, {"t": round(b, 3), "on": False})],
                        "light": {"type": "spot", "color": "#e9f1ff", "intensity": 0, "position": [0, 0.08, 0.02],
                                  "target": [0, 0.2, 3.0], "angle": 38, "penumbra": 0.85, "decay": 2,
                                  # Луч фонарика виден в дымке кабины, у линзы — яркая точка.
                                  "beam": {"length": 2.4, "radius": 0.8, "opacity": 0.12},
                                  "halo": {"size": 0.06, "opacity": 0.5},
                                  "keys": [{"t": 0, "intensity": 0}] + [
                                      x for a, b in merged for x in ({"t": round(a, 3), "intensity": 0},
                                                                     {"t": round(a + 0.15, 3), "intensity": loc.get("phone_light", PHONE_LIGHT)},
                                                                     {"t": round(b - 0.1, 3), "intensity": loc.get("phone_light", PHONE_LIGHT)},
                                                                     {"t": round(b, 3), "intensity": 0})]}})
    # Напряжение в начале истории (после хука) — тихо.
    first_story = next((s for s in shot_out if s["role"] not in ("hook", "")), None)
    if first_story:
        add_sfx("tension", first_story["t0"], "напряжение")

    def add_bed(kind: str, t0: float, t1: float, label: str, fade_in: float, fade_out: float) -> None:
        if kind in AMBIENCE and t1 - t0 >= 0.5:
            ref, under = AMBIENCE[kind]
            sfx.append({"t": round(max(0.0, t0), 3), "ref": ref, "volume": 1.0, "label": label, "kind": kind,
                        "end": round(min(total, t1), 3), "role": "ambience", "under_db": under,
                        "fade_in": fade_in, "fade_out": fade_out})

    # Фон-петли: гул кабины, пока горит свет (глохнет на блэкауте), в темноте — далёкий гул шахты.
    if loc_name == "elevator":
        if blackout is None:
            add_bed("hum", 0.0, total, "гул кабины", 0.4, 1.5)
        else:
            add_bed("hum", 0.0, blackout + 0.12, "гул кабины", 0.4, 0.18)
            add_bed("dark", blackout + 0.6, light_back or total, "гул шахты", 2.5, 1.0 if light_back else 2.0)
            if light_back:
                add_bed("hum", light_back, total, "гул кабины", 0.3, 1.5)

    character = _character(look, path, facing, actions, poses)
    fill_lit, fill_dark = loc.get("fill_levels") or (FILL, FILL_DARK)
    env = dict(loc["environment"])
    spec = {"version": 1, "width": WIDTH, "height": HEIGHT, "fps": int(fps), "duration": round(total, 3),
            "environment": env, "room": loc["room"], "lights": lights, "objects": objects, "characters": [character],
            # Камера сама не прижимается к стенам: ракурсы уже проверены (внутри декорации или за срезанной стеной).
            "camera": {"fov": FOV, "keys": cam_keys, "shake": shake, "margin": 0.1, "clamp": False,
                       "fill": {"color": "#ffe6cc", "intensity": fill_lit, "distance": 4,
                                "keys": ([{"t": 0, "intensity": fill_lit}, {"t": round(blackout, 3), "intensity": fill_lit},
                                          {"t": round(blackout + 0.05, 3), "intensity": fill_dark}]
                                         if blackout is not None else None)}},
            "quality": {"shadows": True, "shadowSize": 1024, "antialias": True}}
    return {"spec": spec, "shots": shot_out, "events": events, "sfx": sorted(sfx, key=lambda x: x["t"]),
            "location": loc_name, "checks": checks, "notes": notes, "look": look}


def check_shots(result: dict) -> list[str]:
    """Проверка готовой режиссуры: камера внутри декорации (кроме общих сверху), кадры встык, без дыр."""
    loc = LOCATIONS[result["location"]]()
    out = list(result.get("checks") or [])
    shots = result["shots"]
    for a, b in zip(shots, shots[1:]):
        if abs(a["t1"] - b["t0"]) > 1e-3:
            out.append(f"кадры {a['n']}–{b['n']}: дыра/нахлёст {a['t1']}–{b['t0']}")
    for s in shots:
        if not camera_ok(s["position"], loc, s["free"], 0.08):
            out.append(f"кадр {s['n']}: камера в стене")
    return out


def render_episode(result: dict, out_path: Any, *, workers: int | None = None, gl: str | None = None) -> dict:
    """Сцена серии → mp4 (``app.scene3d.render_scene``)."""
    from app.scene3d import render_scene

    return render_scene(result["spec"], out_path, workers=workers, gl=gl)
