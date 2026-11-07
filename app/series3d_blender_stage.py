"""3D-серия: лифт и локации — стойка, кнопки, точки, координаты (из ``app/series3d_blender.py``)."""

from __future__ import annotations

import math
import re

from app import motion3d as m

S = 0.5                    # масштаб героя KayKit (2.17 → 1.08 м)


# --------------------------------------------------------------------------- #
# Лифт (общая геометрия для сцены Blender и планировщика; z — вверх, герой при курсе 0 смотрит в −Y, к дверям)
# --------------------------------------------------------------------------- #

W, D, H = 1.70, 1.80, 2.15
FRONT = -D / 2                     # стена с дверями
LEFT = -W / 2                      # стена с кнопочной панелью
PANEL_Y = -0.22
BTN_X = LEFT + 0.026               # лицевая поверхность кнопок
BTN_Z0, BTN_DZ, BTN_DY = 0.64, 0.056, 0.064
BELL = (BTN_X, PANEL_Y, 0.575)     # вызов диспетчера — внизу панели: чиби-рука выше ~0.85 м не достаёт
BTN_NORMAL = (1.0, 0.0, 0.0)
DOOR_FACE_Y = FRONT - 0.0325       # лицевая поверхность створок (утоплены за обрамлением)
DOOR_NORMAL = (0.0, 1.0, 0.0)


DISPLAY = (0.0, FRONT + 0.03, 1.93)
CEIL_LIGHT = (0.0, 0.05, H - 0.02)
HATCH = (0.25, 0.30, H)            # люк в потолке


REACH = (0.242 + 0.259 + 0.074 + 0.10) * S
SHOULDER_R = (0.212 * S, 0.0, 1.107 * S)   # правое плечо от корня (x — вправо от взгляда, y — вперёд)


MAX_PRESS_Z = 0.83                 # выше чиби-рука не достаёт — берём кнопку ниже в том же столбце


# Длины клипов KayKit (с) и шаг — замер в Blender (сцена сверяет и пишет в отчёт расхождение).
CLIP_LEN = {"Idle_A": 1.067, "Idle_B": 2.133, "Walking_A": 1.067, "Walking_B": 1.067, "Walking_C": 1.6,
            "Sit_Floor_Down": 1.0, "Sit_Floor_Idle": 4.0, "Sit_Floor_StandUp": 1.133, "Hit_A": 0.667, "Hit_B": 0.867}

# Шаг: KayKit Walking_C (осторожная ходьба, 2 шага за 1.6 с, шаг ≈ 18 см у героя) — ровнее Walking_A/B.
# Стопы клипа на месте (замер в Blender, система героя без масштаба): (y левой, z левой, y правой, z правой) на кадр
# клипа (30 к/с); вперёд — −y. Корень едет ровно на столько, на сколько опорная стопа (ниже другой) уезжает назад:
# стопа в контакте стоит на полу (root motion из клипа), а не скользит, как при постоянной скорости корня.
WALK_CLIP = "Walking_C"
WALK_FEET = [(-0.1433, 0.1463, 0.1896, 0.2276), (-0.1336, 0.1461, 0.1865, 0.2279), (-0.11, 0.1458, 0.1778, 0.2288), (-0.0802, 0.1454, 0.1641, 0.2302), (-0.0493, 0.1443, 0.1464, 0.2324), (-0.0184, 0.1422, 0.1254, 0.2352), (0.0142, 0.1408, 0.1019, 0.2386), (0.0445, 0.1405, 0.0768, 0.2427), (0.0685, 0.1405, 0.0508, 0.2475), (0.0875, 0.1404, 0.0247, 0.2528), (0.103, 0.1404, -0.0006, 0.2586), (0.1164, 0.1403, -0.0245, 0.2647), (0.1289, 0.1401, -0.0462, 0.2709), (0.1419, 0.1393, -0.0628, 0.2752), (0.1559, 0.1375, -0.0731, 0.275), (0.1713, 0.1346, -0.079, 0.2696), (0.1884, 0.1306, -0.083, 0.2587), (0.2059, 0.1289, -0.0873, 0.243), (0.2211, 0.1343, -0.0934, 0.2239), (0.2377, 0.1408, -0.102, 0.2036), (0.2515, 0.15, -0.1127, 0.1843), (0.2412, 0.1814, -0.1239, 0.1678), (0.2205, 0.2074, -0.1339, 0.1555), (0.1996, 0.2226, -0.1409, 0.1481), (0.1896, 0.2276, -0.1433, 0.1463), (0.1857, 0.2282, -0.1336, 0.1469), (0.1763, 0.2294, -0.11, 0.1466), (0.1624, 0.231, -0.0802, 0.1459), (0.1446, 0.2332, -0.0494, 0.1444), (0.1237, 0.2359, -0.0184, 0.1422), (0.1004, 0.2393, 0.0142, 0.1408), (0.0756, 0.2433, 0.0445, 0.1405), (0.0499, 0.2479, 0.0685, 0.1405), (0.0242, 0.253, 0.0875, 0.1404), (-0.0009, 0.2587, 0.103, 0.1404), (-0.0246, 0.2647, 0.1164, 0.1403), (-0.0462, 0.2709, 0.1289, 0.1401), (-0.0627, 0.2751, 0.1419, 0.1393), (-0.0728, 0.2748, 0.1559, 0.1375), (-0.0787, 0.2693, 0.1713, 0.1346), (-0.0827, 0.2584, 0.1884, 0.1306), (-0.0871, 0.2428, 0.2077, 0.1253), (-0.0935, 0.2241, 0.2241, 0.1286), (-0.1022, 0.2043, 0.2412, 0.1345), (-0.1128, 0.1855, 0.2514, 0.1501), (-0.1239, 0.1694, 0.2409, 0.1817), (-0.1339, 0.157, 0.2199, 0.2078), (-0.1408, 0.1491, 0.1989, 0.2229), (-0.1433, 0.1463, 0.1896, 0.2276)]


MAX_WALK_STEPS = 3              # в лифте — 1–3 шага, дальше — склейка


def _walk_stance() -> list[float]:
    """Сдвиг опорной стопы назад за кадр клипа (ед. без масштаба) — на столько корень едет вперёд."""
    out = []
    for i in range(len(WALK_FEET) - 1):
        ly, lz, ry, rz = WALK_FEET[i]
        ly2, lz2, ry2, rz2 = WALK_FEET[i + 1]
        out.append((ly2 - ly) if lz <= rz else (ry2 - ry))
    return out


WALK_STANCE = _walk_stance()
WALK_CYCLE_UNITS = sum(WALK_STANCE)
STEP_LEN = WALK_CYCLE_UNITS * S / 2          # м за шаг (полцикла)
_STANCE_CUM = [0.0]
for _v in WALK_STANCE:
    _STANCE_CUM.append(_STANCE_CUM[-1] + _v)


def stance_units(lt: float) -> float:
    """Пройдено корнем (ед. без масштаба) от начала клипа шага к его секунде ``lt`` (с переходом через цикл)."""
    n = len(WALK_STANCE)
    phi = lt * 30.0
    cycles, rest = divmod(phi, n)
    i = int(rest)
    frac = rest - i
    return cycles * WALK_CYCLE_UNITS + _STANCE_CUM[i] + WALK_STANCE[min(i, n - 1)] * frac


def walk_plan(dist: float) -> tuple[int, float]:
    """(шагов, сколько секунд клипа сыграть): шагов 1–3 по длине пути, начало и конец — ноги рядом."""
    steps = max(1, min(MAX_WALK_STEPS, int(round(dist / STEP_LEN))))
    return steps, steps * CLIP_LEN[WALK_CLIP] / 2


def walk_seconds(clip_sec: float, ease: float = 0.35) -> float:
    """Сколько реальных секунд идёт ``clip_sec`` секунд клипа с разгоном и торможением ``ease``."""
    return clip_sec + (1 - m.ClipSegment.EASE_LOW) * ease


CLIPS_NEEDED = tuple(CLIP_LEN)


def button_pos(button: str | int) -> tuple[float, float, float]:
    """Центр лицевой поверхности кнопки: 1–12 в два столбца снизу вверх, ``bell`` — вызов диспетчера."""
    if str(button) == "bell":
        return BELL
    n = max(1, min(12, int(button)))
    col, row = (n - 1) % 2, (n - 1) // 2
    return (BTN_X, PANEL_Y + 0.032 - col * BTN_DY, BTN_Z0 + row * BTN_DZ)


def reachable(button: str | int) -> str:
    """Кнопка, до которой герой дотянется: выше ``MAX_PRESS_Z`` — та же колонка ниже."""
    if str(button) == "bell":
        return "bell"
    n = max(1, min(12, int(button)))
    while n > 2 and button_pos(n)[2] > MAX_PRESS_Z:
        n -= 2
    return str(n)


def facing(heading: float) -> tuple[float, float]:
    return (math.sin(heading), -math.cos(heading))


def to_world(root: tuple[float, float], heading: float, local: tuple[float, float, float]) -> tuple[float, float, float]:
    """Точка в системе героя (x — вправо от взгляда, y — вперёд, z — вверх) → мир."""
    fx, fy = facing(heading)
    rx, ry = fy, -fx   # вправо от взгляда: при курсе 0 (взгляд −Y) правая рука — к −X
    return (root[0] + local[0] * rx + local[1] * fx, root[1] + local[0] * ry + local[1] * fy, local[2])


PRESS_TURN = math.radians(-22)     # к панели вполоборота: в профиль к камере у дверей
KNOCK_POINT = (-0.10, DOOR_FACE_Y, 0.60)


def press_stand(buttons: list[str]) -> tuple[tuple[float, float], float]:
    """Где встать и куда смотреть, чтобы нажать кнопки (середина набора)."""
    pts = [button_pos(b) for b in buttons]
    mid = tuple(sum(p[i] for p in pts) / len(pts) for i in range(3))
    fx, fy = facing(PRESS_TURN)
    st = m.stand_point(mid, BTN_NORMAL, SHOULDER_R, REACH, comfort=0.95, facing=(fx, fy, 0.0), ahead=0.08)
    return (round(st[0], 4), round(st[1], 4)), PRESS_TURN


def spot_for(name: str) -> tuple[tuple[float, float], float]:
    """Место героя (корень) и курс для цели."""
    if name == "panel":
        return press_stand(["1"])
    if name == "doors":
        return (0.0, FRONT + 0.36), 0.0
    if name == "knock":
        h = math.radians(14)
        fx, fy = facing(h)
        st = m.stand_point(KNOCK_POINT, DOOR_NORMAL, SHOULDER_R, REACH, comfort=0.96, facing=(fx, fy, 0.0), ahead=0.02)
        return (round(st[0], 4), round(st[1], 4)), h
    if name == "pry":
        return (0.0, FRONT + 0.31), 0.0
    if name == "corner":
        return (0.47, 0.50), m.heading_of((-0.55, -1.0))
    if name == "display":
        return (0.05, -0.25), 0.0
    return (0.0, 0.05), 0.0   # center


# --------------------------------------------------------------------------- #
# Локации серии: кабина + шахта, площадка этажа, технический этаж, коридор (одна раскладка на локацию)
# --------------------------------------------------------------------------- #
# Все в одной сцене Blender, каждая — со своим началом координат по x (свет одной не попадает в другую); внутри —
# свои места героя (spots: (x, y, курс)), точки интереса (targets), границы камеры и реквизит (props: что сцена
# показывает). Двери лифта у коридора и площадки — на той же стене (y = FRONT), что в кабине: те же «раздвигать/
# стучать». Надпись на стене (decal) — текстом в сцене (например «1998»).
LOCS: dict[str, dict] = {
    "cabin": {"label": "кабина лифта", "origin": (0.0, 0.0),
              "bounds": (LEFT + 0.06, -LEFT - 0.06, FRONT + 0.06, D / 2 - 0.06, 0.12, H - 0.1),
              "props": ("кнопки", "табло", "двери лифта", "люк", "поручни", "аварийный свет"),
              "keywords": r"кабин|лифт\w*(?! шахт)|кнопк|табло|поручн",
              "spots": {}, "targets": {}},
    "shaft": {"label": "шахта лифта (на крыше кабины)", "origin": (12.0, 0.0), "bounds": (-1.05, 1.05, -1.15, 1.15, 0.2, 3.2),
              "props": ("тросы", "противовес", "стены шахты", "крыша кабины", "люк", "направляющие"),
              "keywords": r"шахт|\bтрос|противовес|крыш\w* (лифт|кабин)|через люк|на крышу",
              "spots": {"entry": (0.15, 0.25, 0.0), "center": (0.0, 0.0, 0.0), "edge": (0.0, -0.55, 0.0)},
              "targets": {"key": (0.0, 0.0, 3.5), "ceiling": (0.0, 0.0, 6.0), "sound": (0.0, 0.0, 4.5),
                          "cables": (0.05, 0.05, 2.0), "counterweight": (0.8, 0.75, 1.4), "wall": (0.0, -1.2, 1.2),
                          "hatch": (0.3, 0.45, 0.02), "decal": (0.0, -1.18, 1.3)}},
    "landing": {"label": "площадка этажа (лестничная клетка)", "origin": (24.0, 0.0), "bounds": (-1.35, 1.35, FRONT + 0.06, 1.4, 0.15, 2.45),
                "props": ("двери квартир", "номер этажа на стене", "лампа", "лестница", "двери лифта", "надпись на стене"),
                "keywords": r"площадк|лестни|ступен|квартир|этаж\w* (вышел|вышла)|подъезд|номер этажа",
                "spots": {"entry": (0.0, -0.45, 3.1416), "center": (0.0, 0.1, 3.1416), "doors": (0.0, FRONT + 0.36, 0.0),
                          "decal": (0.0, 0.75, 3.1416), "apartment": (-0.75, 0.2, -1.5708)},
                "targets": {"key": (0.0, 1.47, 1.35), "decal": (0.0, 1.47, 1.35), "ceiling": (0.0, 0.0, 2.55),
                            "sound": (1.2, 1.2, 1.4), "lamp": (0.0, 0.0, 2.45), "apartment": (-1.47, 0.3, 0.9),
                            "doors": (0.0, DOOR_FACE_Y, 0.8)}},
    "techfloor": {"label": "технический этаж (машинное отделение)", "origin": (36.0, 0.0), "bounds": (-1.9, 1.9, -1.9, 1.9, 0.15, 2.5),
                  "props": ("мотор лебёдки", "электрощит", "трубы", "лампа", "надпись на стене"),
                  "keywords": r"машинн|технич\w* этаж|лебёдк|лебедк|\bщит|электрощит|\bмотор|котельн",
                  "spots": {"entry": (0.0, -0.6, 3.1416), "center": (0.0, 0.0, 3.1416), "motor": (0.55, 0.3, 2.3)},
                  "targets": {"key": (0.9, 1.0, 0.8), "decal": (-1.88, 0.4, 1.3), "ceiling": (0.0, 0.0, 2.6),
                              "sound": (0.9, 1.0, 0.8), "lamp": (0.0, 0.3, 2.3), "cabinet": (-1.0, 1.85, 1.0)}},
    "corridor": {"label": "коридор технического этажа", "origin": (48.0, 0.0), "bounds": (-0.82, 0.82, FRONT + 0.06, 6.9, 0.15, 2.3),
                 "props": ("бетонные стены", "лампочка", "календарь/надпись на стене", "двери", "двери лифта в торце",
                           "трубы"),
                 "keywords": r"коридор|\bпроход[аеу]?\b|бетонн\w* (стен|коридор)|в глубин",
                 "spots": {"entry": (0.0, FRONT + 0.5, 3.1416), "center": (0.0, 0.9, 3.1416),
                           "doors": (0.0, FRONT + 0.36, 0.0), "lamp": (0.0, 1.9, 3.1416), "decal": (-0.3, 1.6, -1.5708),
                           "deep": (0.0, 3.6, 3.1416)},
                 "targets": {"key": (-0.79, 1.6, 1.05), "decal": (-0.79, 1.6, 1.05), "ceiling": (0.0, 1.2, 2.4),
                             "sound": (0.0, 7.0, 0.9), "lamp": (0.0, 2.6, 2.05), "doors": (0.0, DOOR_FACE_Y, 0.8),
                             "deep": (0.0, 6.5, 0.8)}},
}
LOC_NAMES = tuple(LOCS)
LOC_TARGETS = ("doors", "lamp", "decal", "deep", "apartment", "motor", "cabinet", "edge", "center")


def loc_of(name: str | None) -> dict:
    return LOCS.get(str(name or "cabin")) or LOCS["cabin"]


def loc_world(loc: str, p: tuple) -> tuple:
    """Точка локации → мир (сдвиг начала координат)."""
    ox, oy = loc_of(loc)["origin"]
    return (p[0] + ox, p[1] + oy, *p[2:])


def loc_spot(loc: str, name: str) -> tuple[tuple[float, float], float]:
    """Место героя (мир) и курс для цели в локации; кабина — свои расчёты (кнопки, стук, угол)."""
    if loc == "cabin":
        return spot_for(name)
    spots = loc_of(loc)["spots"]
    x, y, h = spots.get(name) or spots.get("center") or spots["entry"]
    return loc_world(loc, (x, y)), h


def loc_target(loc: str, name: str, default: tuple | None = None) -> tuple:
    """Точка интереса локации (мир): key — главная вещь (надпись, номер этажа, тросы), ceiling, sound, …"""
    if loc == "cabin":
        cab = {"key": DISPLAY, "display": DISPLAY, "ceiling": HATCH, "sound": (0.05, DOOR_FACE_Y, 0.8),
               "doors": (0.0, DOOR_FACE_Y, 0.8)}
        return cab.get(name) or default or DISPLAY
    t = loc_of(loc)["targets"]
    p = t.get(name) or t.get("key")
    return loc_world(loc, p) if p else (default or loc_world(loc, (0.0, 1.0, 1.0)))


# Свет вне кабины (сцена Blender): заполняющий у камеры («от фонарика/лампы», Вт при 0.8 м до героя), контровой
# сзади-сверху (Вт) и экспозиция места — герой и место читаются в каждом кадре (проверка — лист «фраза → что в кадре»).
FILL_W = {"corridor": 5.0, "shaft": 4.0, "landing": 3.0, "techfloor": 3.5}
RIM_W = {"corridor": 25.0, "shaft": 22.0, "landing": 15.0, "techfloor": 18.0}
EXPOSURE = {"cabin": 0.0, "corridor": 0.35, "shaft": 0.3, "landing": 0.15, "techfloor": 0.2}


def inside_loc(loc: str, p: tuple, margin: float = 0.0) -> bool:
    x0, x1, y0, y1, z0, z1 = loc_of(loc)["bounds"]
    ox, oy = loc_of(loc)["origin"]
    x, y = p[0] - ox, p[1] - oy
    return x0 + margin <= x <= x1 - margin and y0 + margin <= y <= y1 - margin


# Что фраза называет → что сцена показывает (точка для проверки «видно в кадре»): по словам и реквизиту Gemini.
SHOW_WORDS = (("лампочк|лампа|светильник", "lamp"), ("календар|надпис|на стене|1998|цифр\\w* на стен", "decal"),
              ("двер|\\bствор", "doors"), ("\\bтрос", "cables"), ("противовес", "counterweight"), ("номер", "decal"),
              ("\\bлюк", "hatch"), ("стен\\w* шахт", "wall"), ("\\bщит|электрощит", "cabinet"), ("\\bмотор|лебёдк|лебедк", "key"),
              ("коридор|\\bпроход[аеу]?\\b|в глубин", "deep"), ("табло", "display"), ("кнопк", "buttons"), ("квартир", "apartment"))


# Проём кабины: куда встать, чтобы за героем было видно шахту, и что там видно. Кабина застряла между этажами: вверху
# край плиты перекрытия и закрытая дверь этажа, ниже плиты голая стена шахты вплотную, стоять там негде.
DOORWAY = {"spot": (-0.21, FRONT + 0.30), "heading": math.radians(24), "gap": 0.31, "pry_y": FRONT + 0.27,
           "slab": (0.09, FRONT - 0.06, 1.13), "floor_door": (0.09, FRONT - 0.10, 1.50), "wall": (0.11, FRONT - 0.30, 0.52)}


def show_items(loc: str, shot: dict) -> list[dict]:
    """Вещи и место, которые фраза называет: ``[{item, point}]`` — сцена обязана их показать (проверка в отчёте)."""
    text = _low(" ".join([shot.get("text") or "", " ".join(shot.get("props") or [])]))
    out = [{"item": f"место: {loc_of(loc)['label']}", "point": None}]
    if shot.get("frame") == "doorway" and loc == "cabin":
        out += [{"item": name, "point": [round(c, 3) for c in DOORWAY[key]]}
                for name, key in (("край плиты", "slab"), ("дверь этажа", "floor_door"), ("стена шахты", "wall"))]
    if shot.get("screen"):
        out.append({"item": f"экран «{shot['screen']}»", "point": None, "screen": True})
    if shot.get("show_none"):
        return out            # крючок: лицо, названное — следующим куском фразы
    seen = set()
    for rx, name in SHOW_WORDS:
        if not re.search(rx, text) or name in seen:
            continue
        seen.add(name)
        if loc == "cabin":
            pt = {"display": DISPLAY, "buttons": button_pos("1"), "doors": (0.0, DOOR_FACE_Y, 0.8), "hatch": HATCH,
                  "lamp": CEIL_LIGHT}.get(name)
        else:
            t = loc_of(loc)["targets"]
            pt = loc_world(loc, t[name]) if name in t else None
        if pt is not None:
            out.append({"item": name, "point": [round(c, 3) for c in pt]})
        else:
            out.append({"item": name, "point": None, "missing": True})
    if shot.get("decal") and loc == "cabin":
        # в кабине цифры — на табло над дверями (номер этажа), отдельной надписи нет
        out.append({"item": f"надпись «{shot['decal']}»", "point": [round(c, 3) for c in DISPLAY]})
    elif shot.get("decal"):
        t = loc_of(loc)["targets"].get("decal")
        out.append({"item": f"надпись «{shot['decal']}»",
                    "point": [round(c, 3) for c in loc_world(loc, t)] if t else None, **({} if t else {"missing": True})})
    return out


# Длина серии для сценариста: 55–65 с с титром части (2 с) при ≈ 2.3 слова/с рассказчика — 120–140 слов.
SCRIPT_SEC = (55, 65)
SCRIPT_WORDS = (120, 140)


def writer_places() -> str:
    """Что сценарист может упоминать: места и вещи, которые сцена покажет."""
    return "\n".join(f"- {v['label']}: {', '.join(v['props'])}" for v in LOCS.values())


# Места/вещи, которых сцена не покажет: сценарист их не упоминает (иначе фраза переписывается).
OFFLIST = (r"улиц|подвал|кухн|ванн(ой|ая|у)\b|балкон|\bокн[оаеу]|окошк|во двор|двор[ае]?\b|парк\b|\bлес\b|магазин|"
           r"автомобил|машин[аеуы]\b|квартир\w* (внутри|изнутри)|в квартир|комнат|спальн|чердак|крыш\w* дома|"
           r"перрон|метро|больниц|кладбищ")


def offlist_mentions(sentences: list[dict]) -> list[tuple[int, str]]:
    """Фразы, где упомянуто место/вещь, которых нет в наборе локаций: ``[(номер, что)]``."""
    out = []
    for i, sent in enumerate(sentences):
        hit = re.search(OFFLIST, _low(sent.get("text")))
        if hit:
            out.append((i, hit.group(0)))
    return out


def rule_location(text: str, current: str) -> str:
    """Где кадр по словам фразы; не названо — там же, где был."""
    low = _low(text)
    for name in ("shaft", "corridor", "techfloor", "landing", "cabin"):
        if re.search(LOCS[name]["keywords"], low):
            if name == "cabin" and current != "cabin" and not re.search(
                    r"(вернул\w*|залез\w*|забежал\w*|вош[её]л|зашёл|зашел|шагнул|запрыгнул)\s.{0,25}(лифт|кабин)|в кабине", low):
                continue   # «двери лифта», «путь в кабину» в коридоре — ещё не возврат в кабину
            return name
    return current


DECAL_RX = r"(\d{4})\s*год|надпис\w*\s*[«\"]([^»\"]{1,14})|календар\w*\s+за\s+(\d{4})"


def decal_from_text(text: str) -> str:
    m_ = re.search(DECAL_RX, _low(text))
    if not m_:
        return ""
    return next((g for g in m_.groups() if g), "").upper()[:14]


def _low(text: str) -> str:
    return str(text or "").lower().replace("ё", "е")
