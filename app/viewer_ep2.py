"""Серия 2 «Сара» (проект 10 «Мир от зрителей», TikTok @.psx53 «Выжить psx»): сценарий огонька → сцена PS1.
Чистые функции; озвучка, рендер, звук и проверки — tools/viewer_ep2.py. Опирается на серию 1 v2 (app/viewer_ep1v2.py).

Что в серии (план, отправленный владельцу; один комментарий под серией 1 — Neko_Sil Flood, 2 лайка):
1. Помехи → титр-крючок «ЕЁ ЗОВУТ САРА / ВЫ ТАК РЕШИЛИ» на её лице (первый кадр ролика — обложка).
2. Огонёк: один голос, карточка-комментарий как в TikTok (ник, текст, сердце 2); «Ты — Сара». Она шёпотом
   повторяет имя — не помнит.
3. Зритель предложил снять Сборщика ИЛИ спрятаться — она делает и то и другое: прячется за будкой кассы на вокзале
   (station.ts вживую, зал развёрнут к проходу на платформы) и снимает из-за угла: видоискатель (REC, штамп).
4. Честный бросок d20 на скрытность (ЛОВКОСТЬ против 12, строка ROLL_SEED) — окно «БРОСОК» как в серии 1.
   Текст после броска написан под выпавшее (15 → успех: её не видно, снимает всё, он подходит под лампу).
5. На плёнке — то, чего не видит глаз: на пустой маске Сборщика её лицо (только через видоискатель).
6. Камера пищит «БАТАРЕЯ» → «они идут на звук» → Сборщик рывком сворачивает голову к будке → срыв в помехи.
7. Меню «1 — БЕЖАТЬ / 2 — ЗАМЕРЕТЬ / 3 — СВОЁ», титр «ЧТО ДЕЛАЕТ САРА? / — В КОММЕНТЫ», помехи в конце.

Сборщик переделан (владелец: «кринжовый, а не страшный») — collector.ts: тощий, 3 м, колени назад, руки до земли,
голова свёрнута набок, пустая маска; стоп-моушен, на виду замирает, двигается за кадром; рядом с ним рвётся сигнал.
"""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

from app.viewer_dnd import resolve
from app.viewer_ep import _key, _wisp_pos, cue_times, pace, scale_beats, shift_beats, speed_for
from app.viewer_ep1 import HER_VOICE  # noqa: F401 — её голос (Gemini Leda), как в серии 1
from app.viewer_episode import caption_chunks, sentence_times

NAME = "Сара"
COMMENT = {"nick": "Neko_Sil Flood", "likes": 2,
           "text": "1. Сара 2. Может попробовать записать это существо на камеру? Либо же спрятаться"}
ROLL_SEED = "серия 2|Сара|записать и спрятаться"  # открытая строка кубика (зрители могут пересчитать)
ROLL_STAT, ROLL_DIFF = "ЛОВКОСТЬ", "normal"  # спрятаться и снимать — ловкость против обычной сложности (12)
HOOK = ["ЕЁ ЗОВУТ САРА", "ВЫ ТАК РЕШИЛИ"]  # в кадре: «ЕЁ ЗОВУТ / САРА» крупно (24 px), «ВЫ ТАК РЕШИЛИ» — 16 px (ширина)
CHOICE = ["ЧТО ДЕЛАТЬ?", "1 — БЕЖАТЬ", "2 — ЗАМЕРЕТЬ", "3 — СВОЁ"]
FINAL = ["ЧТО ДЕЛАЕТ САРА?", "— В КОММЕНТЫ"]  # в кадре — три строки (ширина 16 px шрифта)
HER_TEXT = "Сара… Не помню."
DATE, STAMP = "09 НОЯ", "03:24"
SKIN = "#8e8b80"  # кожа Сборщика: в полутьме зала серая, бледнеет только под лампой

# Место (мир; героиня в начале координат, смотрит в +Z — на проход к платформам). Будка кассы слева-спереди.
BOOTH = {"pos": [0.83, 1.2], "rot": 0.0}
HALL = {"seed": 29, "clock": [3, 17], "sign": "ВОКЗАЛ", "flip": True, "booth": BOOTH, "lamps": 0.55, "dust": 160}
HALL_PSX = {"fog": {"color": "#070a09", "near": 2.0, "far": 12.0},
            "light": {"dir": [0.15, 1, -0.25], "color": "#a8d0b8", "power": 0.26, "ambient": "#46604f", "ambientPower": 0.3}}
HALL_FX = {"desat": 0.45, "tint": "#d4e8da", "contrast": 1.1, "gain": 1.3, "lift": 6, "grain": 13, "vignette": 0.48, "staticBase": 0}
LENS = [-0.1, 1.42, 0.62]  # видоискатель: объектив камеры в её руках (поза film, hold.ts)
LAMP = [-0.9, 3.8]  # лампа над залом за турникетами: пятно света, где он встаёт (x, z)
# Где он: в коридоре на фоне бледного света, у турникетов, под лампой, ближе к будке.
LISTEN = [1.6, 2.3, 2.2]  # куда он «прислушивается» под лампой (голова поднята, смотрит мимо будки)
# Огонёк в серии светит недалеко (свет не достаёт до Сборщика — тот виден только под лампой), за её плечом, когда он рядом.
WISP_HIDE = {"range": 2.6, "power": 2.4}
WISP_BACK = [0.42, 1.55, -0.3]
SPOTS = {"corridor": [-0.25, 0.0, 10.0], "mouth": [-0.6, 0.0, 7.6], "gate": [-1.3, 0.0, 5.2], "lamp": [LAMP[0], 0.0, LAMP[1]],
         "near": [-0.75, 0.0, 2.6]}


def ep2_roll() -> dict[str, Any]:
    """Бросок на скрытность по правилам (app.viewer_dnd.resolve): Сара — те же статы, что выпали в серии 1."""
    from app.viewer_ep1 import ep1_roll

    hero = deepcopy(ep1_roll()["heroine"])
    hero["name"] = NAME
    hero["episode"] = 2
    res = resolve(hero, ROLL_STAT, ROLL_DIFF, ROLL_SEED)
    return {"seed": ROLL_SEED, "result": res, "heroine": hero, "preset": hero["preset"]}


def pov_key(t: float, cam: list[float], target: list[float], fov: float) -> dict:
    """Ключ облёта, который ставит камеру ровно в точку cam и смотрит на target (orbit.ts: pos = target + (sin, cos)·dist)."""
    dx, dz = cam[0] - target[0], cam[2] - target[2]
    return {"t": round(t, 3), "yaw": round(math.degrees(math.atan2(dx, dz)), 3), "dist": round(math.hypot(dx, dz), 4),
            "height": cam[1], "target": [round(v, 3) for v in target], "fov": fov}


def head_of(spot: list[float], toward: list[float] | None = None, raised: bool = False, height: float = 3.0) -> list[float]:
    """Где голова Сборщика (collector.ts: висит на 2.08 м и 0.77 м впереди; поднята — 2.38 м, 0.66 м) у точки spot,
    если он повёрнут к toward (x, z)."""
    k = height / 3.0
    toward = toward or [0.0, 0.0]
    dx, dz = toward[0] - spot[0], toward[1] - spot[2]
    n = math.hypot(dx, dz) or 1.0
    fwd, y = (0.66, 2.38) if raised else (0.77, 2.08)
    return [round(spot[0] + dx / n * fwd * k, 3), round(y * k, 3), round(spot[2] + dz / n * fwd * k, 3)]


def base_spec(base: dict) -> dict[str, Any]:
    """Основа серии: пресет улицы без улицы → вокзал вживую, свет и туман зала, тон."""
    spec = {k: deepcopy(v) for k, v in base.items() if k not in ("street", "shots", "stills", "look_at", "cuts", "duration")}
    spec["psx"] = {**spec.get("psx", {}), **deepcopy(HALL_PSX)}
    spec["hall"] = deepcopy(HALL)
    spec["fx"] = {**spec.get("fx", {}), **HALL_FX}
    return spec


def gaunt(path: list[dict], show: list[list[float]], tilt=None, look=None, face=None, hunch=None, height: float = 3.0) -> dict:
    """Сборщик v2 для things.ts (shape gaunt): путь, окна, наклон головы, доворот, окна её лица."""
    g: dict[str, Any] = {"path": path, "stepFps": 7, "drop": 0.3, "skin": SKIN}
    if tilt:
        g["tilt"] = tilt
    if look:
        g["look"] = look
    if face:
        g["faceShow"] = face
    if hunch:
        g["hunch"] = hunch
    return {"shape": "gaunt", "pos": path[0]["pos"], "show": show, "height": height, "gaunt": g}


def monster_spec(base: dict) -> tuple[dict[str, Any], list[tuple[float, str]]]:
    """Проба Сборщика v2 до серии: 8 планов по 2 с — видоискатель (коридор, турникеты, наезд на её лицо, голова к
    камере), глазами из-за плеча, общий план с будкой, снизу крупно. Возвращает spec и (секунда, подпись) для листа."""
    spec = base_spec(base)
    S = SPOTS
    marks = [(1.0, "видоискатель: в коридоре"), (3.2, "видоискатель: шаг у турникетов"), (5.4, "наезд: её лицо на маске"),
             (7.0, "глазами: пустая маска"), (9.0, "голова рывком к камере"), (11.0, "ближе, из-за плеча"),
             (13.0, "видоискатель: ближе, замер"), (15.0, "у будки, её глазами")]
    me = [0.0, 0.0]
    path = [{"t": 0, "pos": S["corridor"], "face": me}, {"t": 1.6, "pos": S["corridor"], "face": me}, {"t": 3.6, "pos": S["gate"]},
            {"t": 4.2, "pos": S["lamp"], "face": me}, {"t": 9.8, "pos": S["lamp"], "face": me}, {"t": 10.4, "pos": S["near"], "face": me},
            {"t": 99, "pos": S["near"], "face": me}]
    head = head_of(S["lamp"], me)
    up = head_of(S["lamp"], me, raised=True)
    shoulder = [-0.42, 1.66, -0.5]
    shots = [
        {"name": "pov коридор", "t0": 0, "t1": 2, "pov": True, "free": True, "camKeys": [pov_key(0, LENS, [-0.3, 1.5, 10], 50)]},
        {"name": "pov ворота", "t0": 2, "t1": 4, "pov": True, "free": True, "camKeys": [pov_key(0, LENS, [-1.0, 1.5, 6], 46)]},
        {"name": "pov наезд", "t0": 4, "t1": 6, "pov": True, "free": True, "aimHead": 1, "camKeys": [pov_key(0, LENS, head, 40), pov_key(1.4, LENS, head, 16)]},
        {"name": "глазами", "t0": 6, "t1": 8, "free": True, "camKeys": [pov_key(0, shoulder, [-0.8, 1.6, 3.8], 44)]},
        {"name": "pov голова", "t0": 8, "t1": 10, "pov": True, "free": True, "aimHead": 1, "camKeys": [pov_key(0, LENS, up, 18)]},
        {"name": "из-за плеча", "t0": 10, "t1": 12, "free": True, "camKeys": [pov_key(0, shoulder, [-0.6, 1.5, 2.6], 48)]},
        {"name": "pov лампа", "t0": 12, "t1": 14, "pov": True, "free": True, "camKeys": [pov_key(0, LENS, [S["lamp"][0], 1.55, S["lamp"][2]], 44)]},
        {"name": "у будки", "t0": 14, "t1": 16, "free": True, "camKeys": [pov_key(0, [-0.3, 1.55, -0.35], head_of(S["near"], me), 46)]},
    ]
    spec["shots"] = shots
    spec["duration"] = 16
    spec["holds"] = {"object": "camcorder", "keys": [{"t": -1, "mode": "film", "blend": 0.05}]}
    spec["look_at"] = [{"t": -5, "target": [-0.6, 1.8, 5], "head": 0.5}]
    spec["stalker"] = gaunt(path, [[0, 99]], tilt=[[0, 1.45], [3.9, 0.55], [8.1, 0.75], [10.0, 1.45]],
                            look=[{"t": 3.9, "target": LISTEN}, {"t": 8.1, "target": LENS}, {"t": 9.95, "target": None}],
                            face=[[4, 6], [8, 10]])
    spec["flashlight"] = {"pos": [LAMP[0], 4.3, LAMP[1]], "dir": [0, -1, 0.05], "color": "#d8ffe4", "cone": 30, "range": 6.5, "power": 1.7}
    spec["vhs"] = {"keys": [[0, 1.0], [5.99, 1.0], [6, 0], [7.99, 0], [8, 1.0], [9.99, 1.0], [10, 0]], "shake": 0.01}
    spec["cam"] = [{"t0": 0, "t1": 6, "mode": "rec", "stamp": STAMP, "sec0": 5, "date": DATE},
                   {"t0": 8, "t1": 10, "mode": "rec", "stamp": STAMP, "sec0": 12, "date": DATE, "lowAt": 8.0, "lowText": "БАТАРЕЯ"}]
    spec["wisp"] = {**spec.get("wisp", {}), **WISP_HIDE, "keys": [{"t": 0, "pos": WISP_BACK}], "appear": -5, "dim": [[0, 99, 0.5]]}
    return spec, marks


# --------------------------------------------------------------------------- Сборщик v3 (онрё) — проба стопкадрами

# Владелец про v2: «кринжовый» (коробки, гладкая голова-шар, плоское аниме-лицо). v3 — настоящая VRM-модель (не
# героиня: VRM1_Constraint_Twist_Sample из vendor, pixiv, изменения разрешены), перекрашена в онрё: серо-зелёная
# кожа, чёрные мокрые волосы занавесом на всё лицо, грязное тёмное платье до пят, босая; выше человека, руки
# длиннее, шея длиннее, плечи подняты, голова завалена набок. Лицо видно только в щели — кусок её лица (Сары).
ONRYO_MODEL = "VRM1_Constraint_Twist_Sample.vrm"
ONRYO_SKIN = ["#3a443d", "#aab5a5"]  # кожа: рампа по яркости текстуры (тень, свет) — бледная серо-зелёная
ONRYO_LOOK = {
    "repaint": {"Hair": ["#020303", "#1e2421"], "Body": ONRYO_SKIN, "Face_00": ["#161b18", "#59645a"],
                "Tops": ["#0a0908", "#2e2a25"], "Bottoms": ["#090807", "#26231f"], "EyeIris": ["#000000", "#0b0c0b"],
                "FaceBrow": ["#0b0c0b", "#232825"], "FaceEyeline": ["#090a09", "#1d211e"], "FaceMouth": ["#141614", "#454c46"]},
    "stains": {"Tops": {"colors": ["#1d1a15", "#3a352c", "#120f0c", "#2b2e26"], "n": 70, "seed": 13, "size": 0.035},
               "Body": {"colors": ["#4a5248", "#2c332d", "#6a7266"], "n": 50, "seed": 4, "size": 0.012}},
}
ONRYO_PSX = {"maxTex": 128, "texFor": {"Face": 128, "Tops": 128}, "unlit": ["__нет__"], "tint": {"EyeWhite": "#262a27"},
             "decimate": 0.03, "decimateUv": 0.08, "decimateOnly": "Hair"}
ONRYO_STILLS = [(1.0, "далеко, в коридоре"), (3.0, "REC под лампой"), (5.0, "в рост"), (7.0, "крупно, волосы"),
                (9.0, "REC щель, её лицо"), (11.0, "из-за будки"), (13.0, "снизу"), (15.0, "REC в проёме")]


def onryo(keys: list[dict], show: list[list[float]] | None = None, **kw) -> dict:
    """Онрё для world.ts (spec.onryo): модель, перекраска, ключи позы (где стоит, наклон головы, щель в волосах)."""
    return {"model": ONRYO_MODEL, "keys": keys, "show": show or [[0, 999]], "scale": 1.2, "arms": [1.15, 1.26, 1.1],
            "neck": 1.3, "look": deepcopy(ONRYO_LOOK), "heroine": deepcopy(ONRYO_PSX), "hide": ["EyeHighlight", "Shoes"],
            "hair": {"front": 150, "side": 110, "length": 0.62, "colors": ["#080a09", "#6a7a72"]}, "dress": {"colors": ["#0b0a09", "#29261f"], "hem": 0.07},
            "faceTint": "#d0dacd", **kw}


def onryo_spec(base: dict) -> tuple[dict[str, Any], list[tuple[float, str]]]:
    """Проба Сборщика v3 стопкадрами (без анимации): 8 планов по 2 с в зале серии 2, кадр — в середине плана."""
    spec = base_spec(base)
    S = SPOTS
    me = [0.0, 0.0]
    lamp = S["lamp"]
    door = [-0.35, 0.0, 6.9]  # в проёме на платформы: против бледного света — только силуэт
    keys = [{"t": 0, "pos": S["corridor"], "face": me, "tilt": 24},
            {"t": 2, "pos": lamp, "face": me, "tilt": 30},
            {"t": 4, "pos": lamp, "face": [0.6, 1.4], "tilt": 34},
            {"t": 6, "pos": lamp, "face": me, "tilt": 22, "nod": -14},
            {"t": 8, "pos": lamp, "face": LENS[::2], "tilt": 24, "nod": -8, "part": 1},
            {"t": 10, "pos": lamp, "face": me, "tilt": 30},
            {"t": 12, "pos": lamp, "face": me, "tilt": 40, "nod": -4},
            {"t": 14, "pos": door, "face": LENS[::2], "tilt": 34, "nod": 10, "turn": -10}]
    shoulder = [-0.12, 1.62, -0.9]
    shots = [
        {"name": "далеко", "t0": 0, "t1": 2, "pov": True, "free": True, "camKeys": [pov_key(0, [-0.12, 1.52, 0.25], [-0.3, 1.25, 10], 34)]},
        {"name": "pov лампа", "t0": 2, "t1": 4, "pov": True, "free": True, "camKeys": [pov_key(0, LENS, [lamp[0], 1.2, lamp[2]], 48)]},
        {"name": "в рост", "t0": 4, "t1": 6, "free": True, "pov": True, "camKeys": [pov_key(0, [1.05, 1.0, 1.95], [lamp[0], 0.98, lamp[2]], 52)]},
        {"name": "крупно", "t0": 6, "t1": 8, "free": True, "pov": True, "aimHead": 1, "camKeys": [pov_key(0, [-0.75, 1.55, 1.7], [lamp[0], 1.65, lamp[2]], 44)]},
        {"name": "pov щель", "t0": 8, "t1": 10, "pov": True, "free": True, "aimHead": 1, "camKeys": [pov_key(0, LENS, [lamp[0], 1.8, lamp[2]], 11)]},
        {"name": "из-за будки", "t0": 10, "t1": 12, "free": True, "camKeys": [pov_key(0, shoulder, [lamp[0] + 0.35, 1.35, lamp[2]], 56)]},
        {"name": "снизу", "t0": 12, "t1": 14, "free": True, "pov": True, "camKeys": [pov_key(0, [-0.45, 0.35, 1.75], [lamp[0], 1.5, lamp[2]], 50)]},
        {"name": "pov голова", "t0": 14, "t1": 16, "pov": True, "free": True, "aimHead": 0.35, "camKeys": [pov_key(0, LENS, [door[0], 1.15, door[2]], 22)]},
    ]
    spec["shots"] = shots
    spec["duration"] = 16
    spec["holds"] = {"object": "camcorder", "keys": [{"t": -1, "mode": "film", "blend": 0.05}]}
    spec["look_at"] = [{"t": -5, "target": [lamp[0], 1.6, lamp[2]], "head": 0.5}]
    spec["onryo"] = onryo(keys)
    spec["flashlight"] = {"pos": [LAMP[0], 4.3, LAMP[1]], "dir": [0, -1, 0.05], "color": "#d8ffe4", "cone": 28, "range": 7.0, "power": 1.8}
    # Плёнка: видоискатель — полный VHS и REC, глазами — легче (зерно и виньетка остаются от тона зала).
    spec["vhs"] = {"keys": [[0, 0.35], [1.99, 0.35], [2, 1.0], [3.99, 1.0], [4, 0.35], [7.99, 0.35], [8, 1.0], [9.99, 1.0],
                            [10, 0.35], [13.99, 0.35], [14, 1.0], [16, 1.0]], "shake": 0.006}
    spec["cam"] = [{"t0": 2, "t1": 4, "mode": "rec", "stamp": STAMP, "sec0": 5, "date": DATE},
                   {"t0": 8, "t1": 10, "mode": "rec", "stamp": STAMP, "sec0": 9, "date": DATE},
                   {"t0": 14, "t1": 16, "mode": "rec", "stamp": STAMP, "sec0": 14, "date": DATE, "lowAt": 14.0, "lowText": "БАТАРЕЯ"}]
    spec["fx"] = {**spec["fx"], "grain": 18, "vignette": 0.55}
    spec["wisp"] = {**spec.get("wisp", {}), **WISP_HIDE, "keys": [{"t": 0, "pos": WISP_BACK}], "appear": 99}
    return spec, list(ONRYO_STILLS)


# --------------------------------------------------------------------------- серия

LEAD = 0.45  # до первой фразы: обложка + 10 кадров помех + титр на лице (с)
TAIL = 1.9  # после последней фразы: титр-призыв, потом помехи (с)
FPS = 30
HEAD_STATIC = (1, 11)  # кадры помех в начале [от, до): кадр 0 — обложка, потом ровно 10 кадров помех
END_STATIC = 12  # последние кадры — помехи (ровно столько)
POV1_LEN = 2.4  # видоискатель: он в коридоре, рывком два шага — и замер
SPIN = 1.5  # окно броска: кубик крутится (с)
ZOOM = 1.3  # наезд на голову (с)
NUMS = ["", "Один", "Два", "Три", "Четыре", "Пять", "Шесть", "Семь", "Восемь", "Девять", "Десять", "Одиннадцать", "Двенадцать",
        "Тринадцать", "Четырнадцать", "Пятнадцать", "Шестнадцать", "Семнадцать", "Восемнадцать", "Девятнадцать", "Двадцать"]

# Пауза перед фразой роли (с). advice1 — после её шёпота (her_len), result — видоискатель и бросок, choice1 — срыв в помехи.
PAUSE_BEFORE = {"nick": 0.35, "name": 0.45, "advice2": 0.3, "both": 0.45, "here": 1.1, "film": 0.4, "zoom": 1.5,
                "face": 0.1 + ZOOM + 0.45, "eye": 0.55, "sound": 1.25, "choice1": 1.45, "choice2": 0.45}


def ep2_script(roll: dict[str, Any]) -> list[dict]:
    """Реплики огонька (её шёпот — HER_TEXT, другой голос). Строка про бросок — по тому, что выпало."""
    r = roll["result"]
    said = f"{NUMS[r['die']]}. " + ("Тебя не видно." if r["ok"] else "Он что-то услышал.")
    return [
        {"beat": "intro", "text": "Под прошлой серией — один голос."},
        {"beat": "nick", "text": "Неко Сил Флуд."},
        {"beat": "name", "text": "Ты — Сара.", "cues": ["сара"]},
        {"beat": "advice1", "text": "Ещё совет: записать его на камеру.", "cues": ["записать"]},
        {"beat": "advice2", "text": "Или спрятаться.", "cues": ["спрятаться"]},
        {"beat": "both", "text": "Будем делать и то, и другое."},
        {"beat": "here", "text": "Тсс. Он здесь."},
        {"beat": "result", "text": said},
        {"beat": "film", "text": "Снимай. Не дыши."},
        {"beat": "zoom", "text": "Стой… Приблизь."},
        {"beat": "face", "text": "Сара… у него твоё лицо."},
        {"beat": "eye", "text": "А глазами — пусто."},
        {"beat": "sound", "text": "Нет… Они идут на звук.", "cues": ["звук"]},
        {"beat": "choice1", "text": "Бежать или замереть?"},
        {"beat": "choice2", "text": "Решайте за Сару.", "cues": ["решайте"]},
    ]


def ep2_beats(script: list[dict], words: list[dict], her_len: float, lead: float = LEAD) -> tuple[list[dict], float, list[dict]]:
    """Слова озвучки → фразы → темп с паузами серии (её шёпот после «Ты — Сара», видоискатель и бросок перед итогом)."""
    before = {**PAUSE_BEFORE, "advice1": 0.55 + her_len + 0.6, "result": 0.15 + POV1_LEN + 0.25 + 0.3 + SPIN + 0.85}
    segs, paced = pace(sentence_times(script, words), before=before)
    k = speed_for(segs[-1]["at"] + segs[-1]["src"][1] - segs[-1]["src"][0], lead, max_total=999.0)
    return segs, k, shift_beats(scale_beats(paced, k), lead)


def frame_t(n: int) -> float:
    return round(n / FPS, 4)


def timeline(beats: list[dict], her_len: float, total: float) -> dict[str, float]:
    """Все моменты серии (с от начала ролика) по времени фраз."""
    ph = {b["beat"]: b for b in beats}
    cue = lambda b, i=0, d=0.5: cue_times(ph[b])[i] or ph[b]["t0"] + d  # noqa: E731
    ev: dict[str, float] = {}
    ev["card0"] = ph["nick"]["t0"] - 0.35
    ev["name"] = cue("name")
    ev["her0"] = ph["name"]["t1"] + 0.55
    ev["her1"] = ev["her0"] + her_len
    ev["adv1"] = cue("advice1")
    ev["adv2"] = cue("advice2")
    ev["raise"] = ph["both"]["t0"] + 0.25
    ev["card1"] = ph["both"]["t1"] + 0.25
    ev["pov1"] = ph["here"]["t1"] + 0.15
    ev["lurch"] = ev["pov1"] + 1.0
    ev["roll"] = ev["pov1"] + POV1_LEN
    ev["land"] = ev["roll"] + 0.25 + 0.3 + SPIN
    ev["pov2"] = ph["result"]["t1"] + 0.25  # «…Тебя не видно» — и снова видоискатель: он уже под лампой
    ev["zoom0"] = ph["zoom"]["t1"] + 0.1
    ev["zoom1"] = ev["zoom0"] + ZOOM
    ev["eye"] = ph["face"]["t1"] + 0.3
    ev["pov3"] = ph["eye"]["t1"] + 0.3
    ev["beep"] = ev["pov3"] + 0.25
    ev["snap"] = cue("sound", 0, 1.0) - 0.05
    ev["tear"] = ev["snap"] + 0.5
    ev["final"] = ev["tear"] + 0.45
    ev["choice"] = ph["choice1"]["t0"] - 0.15
    ev["cta"] = cue("choice2", 0, 0.1) - 0.1
    ev["end"] = total
    n = int(round(total * FPS))
    ev["static_end"] = frame_t(n - END_STATIC)
    if ev["final"] > ev["choice"] - 0.3:
        raise ValueError(f"срыв в помехи налезает на меню: {ev['final']:.2f} > {ev['choice']:.2f}")
    if ev["pov3"] > ev["beep"] or ev["snap"] < ev["beep"] + 0.6:
        raise ValueError("писк батареи и рывок головы идут не по порядку")
    return {k: round(v, 3) for k, v in ev.items()}


def _r(x: float) -> float:
    return round(x, 3)


def ep2_scene(beats: list[dict], total: float, roll: dict[str, Any], her_len: float, her_env: list[float]) -> dict[str, Any]:
    """Планы, огонёк, интерфейс, камера в руках, Сборщик, плёнка и помехи серии по времени фраз."""
    ph = {b["beat"]: b for b in beats}
    ev = timeline(beats, her_len, total)
    S = SPOTS
    me = [0.0, 0.0]
    end = total + 1

    def shot(name, t0, t1, keys, **kw):
        return {"name": name, "t0": _r(t0), "t1": _r(t1), "minHeight": 0.0, "camKeys": keys, **kw}

    lamp_head = head_of(S["lamp"], me, raised=True)
    shoulder = [-0.42, 1.66, -0.5]
    d_zoom = ev["zoom0"] - ev["pov2"]
    shots = [
        # Обложка: её лицо крупно, сбоку от будки; медленный наезд. Титр — сверху.
        shot("лицо", 0, ev["card0"], [_key(0, -32, 1.35, 1.47, 1.43, 30), _key(ev["card0"], -26, 1.2, 1.47, 1.43, 30)]),
        # Комментарий: она ниже в кадре (сверху — карточка), шепчет имя.
        shot("комментарий", ev["card0"], ev["raise"] - 0.05, [_key(0, -40, 1.45, 1.6, [0, 1.62, 0], 36),
                                                             _key(ev["raise"] - ev["card0"], -34, 1.35, 1.6, [0, 1.62, 0], 36)]),
        # Прячется за будкой и поднимает камеру: сзади-справа, видно её, будку и тёмный зал.
        shot("за будкой", ev["raise"] - 0.05, ev["pov1"], [pov_key(0, [-1.5, 1.62, -1.25], [0.1, 1.35, 0.25], 50),
                                                         pov_key(ev["pov1"] - ev["raise"], [-1.35, 1.6, -1.05], [0.1, 1.35, 0.3], 48)]),
        # Видоискатель 1: он в проходе на фоне бледного света — стоит, дёргает головой, два рывка вперёд, замер.
        shot("видоискатель 1", ev["pov1"], ev["roll"], [pov_key(0, LENS, [-0.35, 1.55, 10], 46),
                                                       pov_key(POV1_LEN, LENS, [-0.4, 1.5, 9], 43)], pov=True, free=True),
        # Бросок: её лицо в свете экранчика (замерла), окно броска сверху.
        shot("бросок", ev["roll"], ev["pov2"], [_key(0, -58, 1.15, 1.5, [0, 1.47, 0.05], 34),
                                                _key(ev["pov2"] - ev["roll"], -52, 1.05, 1.5, [0, 1.47, 0.05], 34)]),
        # Видоискатель 2: он уже под лампой (перешёл, пока она не смотрела), замер, голова поднята; наезд на лицо.
        shot("видоискатель 2", ev["pov2"], ev["eye"], [pov_key(0, LENS, [S["lamp"][0], 1.55, S["lamp"][2]], 44),
                                                     pov_key(d_zoom, LENS, [S["lamp"][0], 1.55, S["lamp"][2]], 44),
                                                     pov_key(d_zoom + ZOOM, LENS, lamp_head, 16),
                                                     pov_key(ev["eye"] - ev["pov2"], LENS, lamp_head, 15)],
             pov=True, free=True, aimHead=[[0, 0.35], [_r(d_zoom), 0.35], [_r(d_zoom + ZOOM * 0.7), 1.0]]),
        # Её глазами (без камеры): из-за плеча — под лампой та же фигура, на маске пусто.
        shot("глазами", ev["eye"], ev["pov3"], [pov_key(0, shoulder, [-0.85, 1.75, 3.8], 40),
                                               pov_key(ev["pov3"] - ev["eye"], [-0.4, 1.66, -0.45], [-0.85, 1.8, 3.8], 38)], free=True),
        # Видоискатель 3: «БАТАРЕЯ», голова рывком к объективу — срыв.
        shot("видоискатель 3", ev["pov3"], ev["final"], [pov_key(0, LENS, lamp_head, 20)], pov=True, free=True, aimHead=1.0),
        # Финал: из-за плеча — он ближе, голова свёрнута к будке.
        shot("ближе", ev["final"], end, [pov_key(0, [-0.48, 1.62, -0.62], [-0.6, 1.7, 2.5], 44),
                                        pov_key(end - ev["final"], [-0.42, 1.6, -0.5], [-0.6, 1.72, 2.5], 42)], free=True),
    ]
    cuts = [s["t0"] for s in shots[1:]]

    # --- Сборщик: на виду замирает, двигается за кадром (между видоискателями); в коридоре пригибается.
    show = [[ev["pov1"], ev["roll"]], [ev["pov2"], ev["tear"] + 0.45], [ev["final"], end]]
    path = [{"t": -5, "pos": S["corridor"], "face": me}, {"t": _r(ev["lurch"]), "pos": S["corridor"], "face": me},
            {"t": _r(ev["lurch"] + 0.9), "pos": S["mouth"], "face": me}, {"t": _r(ev["roll"] + 0.6), "pos": S["mouth"], "face": me},
            {"t": _r(ev["roll"] + 1.6), "pos": S["gate"]}, {"t": _r(ev["pov2"] - 0.3), "pos": S["lamp"], "face": me},
            {"t": _r(ev["final"] - 0.3), "pos": S["lamp"], "face": me}, {"t": _r(ev["final"] - 0.05), "pos": S["near"], "face": me},
            {"t": 999, "pos": S["near"], "face": me}]
    booth_edge = [0.1, 1.45, 0.5]
    stalker = gaunt(path, show, tilt=[[-5, 1.45], [ev["pov2"] - 0.5, 0.55], [ev["snap"], 0.75], [ev["final"] - 0.1, 1.2]],
                    look=[{"t": -5, "target": None}, {"t": _r(ev["pov2"] - 0.5), "target": LISTEN}, {"t": _r(ev["snap"]), "target": LENS},
                          {"t": _r(ev["final"] - 0.1), "target": booth_edge}],
                    face=[[ev["pov1"], ev["roll"]], [ev["pov2"], ev["eye"]], [ev["pov3"], ev["tear"] + 0.45]],
                    hunch=[[-5, 0.95], [ev["roll"] + 1.0, 0.55]])

    # --- огонёк: у её лица, пока говорит про комментарий; когда он рядом — за её плечом, приглушён.
    keys = [{"t": 0, "pos": [0.04, 1.86, 0.44]}, {"t": 1.2, "pos": [0.05, 1.82, 0.42]}]  # над ней, у угла будки: светит, не закрывая лицо
    t_a, t_b = ev["card0"], ev["raise"]
    n = max(2, int((t_b - t_a) / 0.4) + 1)
    for i in range(n):
        t = t_a + (t_b - t_a) * i / (n - 1)
        a = 0.6 * (t - t_a)
        keys.append({"t": _r(t), "pos": [round(0.04 + 0.04 * math.sin(a * 1.7), 3), round(1.82 + 0.05 * math.sin(a), 3),
                                         round(0.42 + 0.04 * math.cos(a * 1.3), 3)]})
    keys += [{"t": _r(ev["raise"] + 0.8), "pos": WISP_BACK}, {"t": _r(end), "pos": [WISP_BACK[0] - 0.05, WISP_BACK[1] - 0.05, WISP_BACK[2]]}]
    wisp = {**WISP_HIDE, "appear": -5, "fadeIn": 0.5, "keys": keys, "dim": [[_r(ev["raise"] + 0.4), _r(end), 0.45]]}

    # --- взгляд: на огонёк, на будку, в экранчик камеры (снимает), замирает
    cam_pt = [-0.6, 1.47, 1.2]
    look = [{"t": -5, "target": cam_pt, "head": 0.55}, {"t": _r(ev["card0"] + 0.2), "target": _wisp_pos(keys, ev["card0"] + 0.4), "head": 0.6},
            {"t": _r(ev["her0"] - 0.1), "target": [-0.2, 1.2, 1.0], "head": 0.6},  # повторяет имя — смотрит вниз
            {"t": _r(ev["adv1"]), "target": _wisp_pos(keys, ev["adv1"] + 0.2), "head": 0.6},
            {"t": _r(ev["raise"] + 0.2), "target": [-0.3, 1.45, 3.0], "head": 0.7},  # из-за угла будки — на проход
            {"t": _r(ev["roll"]), "target": [0.06, 1.38, 0.4], "head": 0.6}]  # на экранчик в руках

    # --- интерфейс
    r = roll["result"]
    card = {"t0": _r(ev["card0"]), "t1": _r(ev["card1"]), "nick": COMMENT["nick"], "likes": COMMENT["likes"], "text": COMMENT["text"],
            "style": "tiktok", "header": "1 комментарий", "age": "1 д.", "y": 128,
            "hl": [{"w": "Сара", "t": _r(ev["name"])}, {"w": "записать", "t": _r(ev["adv1"])}, {"w": "существо", "t": _r(ev["adv1"] + 0.15)},
                   {"w": "камеру", "t": _r(ev["adv1"] + 0.3)}, {"w": "спрятаться", "t": _r(ev["adv2"])}]}
    rolls = {"t0": _r(ev["roll"] + 0.25), "t1": _r(ph["result"]["t1"] + 0.45), "stat": r["stat"], "action": "СПРЯТАТЬСЯ И СНИМАТЬ",
             "die": r["die"], "bonus": r["bonus"], "dc": r["dc"], "spin": SPIN, "y": 40}
    cards = [{"t0": _r(ev["choice"]), "t1": _r(ev["cta"] - 0.05), "lines": CHOICE, "accent": [0], "y": 196}]
    titles = [{"t0": 0, "t1": _r(ev["card0"]), "lines": ["ЕЁ ЗОВУТ", "САРА"], "colors": ["#f8f8f8", "#f8d848"], "y": 52, "px": 24},
              {"t0": 0, "t1": _r(ev["card0"]), "lines": ["ВЫ ТАК РЕШИЛИ"], "colors": ["#f8f8f8"], "y": 124, "px": 16},
              {"t0": _r(ev["cta"]), "t1": _r(ev["static_end"]), "lines": ["ЧТО ДЕЛАЕТ", "САРА?", "— В КОММЕНТЫ"],
               "colors": ["#f8f8f8", "#f8d848", "#f8f8f8"], "y": 176, "px": 16}]
    cam = [{"t0": _r(ev["pov1"]), "t1": _r(ev["roll"]), "mode": "rec", "stamp": STAMP, "sec0": 7, "date": DATE},
           {"t0": _r(ev["pov2"]), "t1": _r(ev["eye"]), "mode": "rec", "stamp": STAMP, "sec0": 7 + int(ev["pov2"] - ev["pov1"]), "date": DATE},
           {"t0": _r(ev["pov3"]), "t1": _r(ev["final"]), "mode": "rec", "stamp": STAMP, "sec0": 7 + int(ev["pov3"] - ev["pov1"]), "date": DATE,
            "lowAt": _r(ev["beep"]), "lowText": "БАТАРЕЯ"}]

    # --- плёнка: видоискатель — картинка камеры; рядом с ним — срывы трекинга; на наезде автофокус ищет.
    def vwin(a, b, k=1.0):
        return [[_r(a - 0.01), 0], [_r(a), k], [_r(b - 0.01), k], [_r(b), 0]]
    vk = [[0, 0]] + vwin(ev["pov1"], ev["roll"]) + vwin(ev["pov2"], ev["eye"]) + vwin(ev["pov3"], ev["final"], 1.2)
    vhs = {"keys": vk, "hunts": [_r(ev["pov1"] + 0.3), _r(ev["zoom0"] + 0.2), _r(ev["pov3"] + 0.1)],
           "tears": [[_r(ev["lurch"] - 0.05), _r(ev["lurch"] + 0.12)], [_r(ev["lurch"] + 0.55), _r(ev["lurch"] + 0.65)],
                     [_r(ev["zoom0"] + ZOOM * 0.55), _r(ev["zoom0"] + ZOOM * 0.55 + 0.12)], [_r(ev["beep"] + 0.3), _r(ev["beep"] + 0.38)],
                     [_r(ev["snap"]), _r(ev["snap"] + 0.12)], [_r(ev["tear"] - 0.1), _r(ev["final"])]],
           "shake": 0.01}
    spike = lambda t, a: [{"t": _r(t - 0.05), "static": 0.0}, {"t": _r(t), "static": a},  # noqa: E731
                          {"t": _r(t + 0.1), "static": a * 0.5}, {"t": _r(t + 0.2), "static": 0.0}]
    h0, h1 = frame_t(HEAD_STATIC[0]), frame_t(HEAD_STATIC[1])
    se = ev["static_end"]
    fx_keys = [{"t": 0, "black": 0.0, "static": 0.0}, {"t": _r(h0 - 0.012), "static": 0.0}, {"t": _r(h0 - 0.002), "static": 1.0},
               {"t": _r(h1 - 0.02), "static": 1.0}, {"t": _r(h1 - 0.012), "static": 0.0}]
    fx_keys += spike(ev["pov1"], 0.45) + spike(ev["lurch"] + 0.02, 0.35) + spike(ev["pov2"], 0.4) + spike(ev["pov3"], 0.35)
    fx_keys += [{"t": _r(ev["tear"] - 0.05), "static": 0.0}, {"t": _r(ev["tear"] + 0.12), "static": 0.95},
                {"t": _r(ev["final"] - 0.05), "static": 0.9}, {"t": _r(ev["final"] + 0.12), "static": 0.0}]
    fx_keys += [{"t": _r(se - 0.012), "static": 0.0}, {"t": _r(se - 0.002), "static": 1.0}, {"t": _r(end + 1), "static": 1.0}]
    fx_keys.sort(key=lambda k: k["t"])
    holds = {"object": "camcorder", "keys": [{"t": -5, "mode": "chest", "blend": 0.1}, {"t": _r(ev["raise"]), "mode": "film", "blend": 0.7}]}
    lcd_glow = [{"t0": _r(ev["roll"]), "t1": _r(ev["pov2"]), "color": "#9ccaff", "power": 1.6, "range": 2.4}]
    lamp = {"pos": [LAMP[0], 4.3, LAMP[1]], "dir": [0, -1, 0.05], "color": "#d8ffe4", "cone": 30, "range": 6.5,
            "powerKeys": [[0, 1.6], [_r(ev["beep"]), 1.6], [_r(ev["beep"] + 0.08), 0.4], [_r(ev["beep"] + 0.16), 1.6],
                          [_r(ev["snap"]), 1.6], [_r(ev["snap"] + 0.06), 0.3], [_r(ev["snap"] + 0.14), 1.7], [_r(end), 1.7]]}
    glitch = [_r(x) for x in (h0, h1, ev["pov1"], ev["lurch"], ev["pov2"], ev["pov3"], ev["snap"], ev["tear"] + 0.12, ev["final"], se)]
    return {"shots": shots, "cuts": cuts, "look_at": look, "wisp": wisp, "comment": card, "roll": rolls, "cards": cards,
            "titles": titles, "cam": cam, "vhs": vhs, "fx_keys": fx_keys, "stalker": stalker, "holds": holds, "lcdGlow": lcd_glow,
            "flashlight": lamp, "objects": [], "glitch_cuts": glitch, "events": ev}


def build_ep2_spec(base: dict, beats: list[dict], words: list[dict], total: float, voice_env: list[float], roll: dict[str, Any],
                   her_len: float, her_env: list[float], her_words: list[dict]) -> dict[str, Any]:
    """Полный spec серии для world.ts. ``her_words`` — слова её шёпота от начала её аудио (сдвигаются на her0)."""
    spec = base_spec(base)
    scene = ep2_scene(beats, total, roll, her_len, her_env)
    events = scene.pop("events")
    glitch = scene.pop("glitch_cuts")
    fx_keys = scene.pop("fx_keys")
    wisp = {**spec.get("wisp", {}), **scene.pop("wisp"), "voice": voice_env, "voiceFps": spec.get("fps", 30)}
    spec.update(scene)
    spec["wisp"] = wisp
    spec["fx"] = {**spec.get("fx", {}), "keys": fx_keys}
    spec["cuts"] = sorted(set(scene["cuts"]) | set(glitch))
    spec["duration"] = round(total, 3)
    spec["mouth"] = [{"t0": events["her0"], "t1": events["her1"], "voice": her_env, "fps": 30}]
    mine = [{"word": w["word"], "start": round(w["start"] + events["her0"], 3), "end": round(w["end"] + events["her0"], 3)} for w in her_words]
    spec["captions"] = caption_chunks(sorted(words + mine, key=lambda w: w["start"]))
    spec["captionY"] = 316
    spec["_events"] = events
    return spec
