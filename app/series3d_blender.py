"""3D-серия в Blender: сценарий → режиссура по фразам → задание рендера → кадры (кусками, с кэшем) → mp4.

Чистый Python без ``bpy``: эту же режиссуру считают сцена Blender (``tools/blender/series_scene.py``),
сервис (задание в очередь хост-воркера, ``app/render_queue.py``) и тесты.

**Режиссура.** Фразы озвучки → кадры (``app.series3d.split_shots``), события на словах
(``app.series3d.detect_events``: погас свет, дёрнулся, стук, табло…). На каждый кадр — поставленное действие из
фиксированного словаря ``ACTIONS`` (подойти, нажать кнопку N, стучать, раздвигать двери, сесть в угол/встать,
телефон, взгляд на табло/потолок/камеру, вздрогнуть, кричать, слушать, ждать) + эмоция лица ``EMOTIONS`` + план
камеры ``CAMERAS`` (за плечом / крупно лицо / деталь / общий сверху). Выбирает Gemini (бесплатно) по JSON-схеме
со строгими enum'ами; без ключа или при ошибке — правила по словам фразы. Потом общая правка
(``fix_sequence``): действие должно быть выполнимо (сидя — сначала встать), один план камеры подряд не
повторяется.

**Исполнение** (``perform``): покадровые дорожки — клипы KayKit с наплывами (``motion3d.ClipTrack``), путь
корня (шаги или склейка-«телепорт», если идти дольше половины кадра), поворот на месте шагами, цели рук для IK
(нажатия, стук, двери, телефон), взгляд, мимика (эмоция + испуг на событиях + моргание), рот — лип-синк по
таймингам слов (визем-группы гласных) с огибающей озвучки, свет (мигание, блэкаут, аварийный, табло), двери,
тряска камеры. Камеру по плану кадра ставит сцена Blender по настоящим костям героя.

**Рендер** (``render_local``): одна сцена лифта на всю серию, кадры — кусками по планам в кэш
``<cache>/<хэш плана>/``; хэш — от покадровых дорожек плана, поэтому правка одной фразы перерисует только её
кадры; упал — повторный запуск дорисует с того кадра, где остановился. Склейка → mp4 без звука (звук, субтитры,
HUD, титры кладёт холст сервиса — ``app/stories_3d.py``).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from app import motion3d as m

FPS = 30
SCENE_VERSION = 3          # меняй, когда меняется сцена Blender: кэш кусков станет недействительным
S = 0.5                    # масштаб героя KayKit (2.17 → 1.08 м)

# --------------------------------------------------------------------------- #
# Словари режиссуры (строгие enum'ы схемы Gemini)
# --------------------------------------------------------------------------- #

ACTIONS = {
    "approach": "подойти к цели (target) и встать лицом к ней",
    "press_button": "нажать кнопку на панели (button: номер 1–12, bell — вызов диспетчера, all — все подряд)",
    "knock": "стучать кулаком в двери",
    "pry_doors": "раздвигать двери руками",
    "sit_corner": "сесть на пол в углу",
    "stand_up": "встать с пола",
    "phone_out": "достать телефон и смотреть в экран",
    "phone_light": "светить фонариком телефона",
    "look_display": "посмотреть на табло этажа над дверью",
    "look_ceiling": "посмотреть на потолок/люк",
    "look_camera": "посмотреть в камеру — обращается к зрителю",
    "flinch": "вздрогнуть от испуга",
    "shout": "кричать/звать на помощь",
    "listen": "прислушиваться",
    "wait": "ждать",
}
TARGETS = ("panel", "doors", "corner", "center", "display")
BUTTONS = tuple(str(n) for n in range(1, 13)) + ("bell", "all")
EMOTIONS = {
    # брови вверх (−1..1), «домиком» (−1 — сведены, сердито .. 1), глаза шире (−1 прищур .. 1)
    "calm": (0.0, 0.0, 0.0),
    "worried": (0.25, 0.65, 0.12),
    "scared": (0.65, 0.9, 0.55),
    "startled": (1.0, 0.5, 1.0),
    "determined": (-0.45, -0.55, -0.1),
    "exhausted": (-0.15, 0.45, -0.4),
    "relieved": (0.35, 0.15, 0.0),
}
CAMERAS = {
    "ots": "за плечом героя на то, что он видит/делает",
    "face": "крупно лицо",
    "detail": "деталь: рука и кнопка с лицом в профиль, телефон, табло, руки на дверях",
    "overhead": "общий сверху из угла кабины",
}
# Предпочтения плана по действию (первый — лучший; повтор прошлого плана пропускается).
CAMERA_PREFS = {
    "approach": ("overhead", "face"),
    "press_button": ("detail", "overhead", "ots"),
    "knock": ("ots", "face", "overhead"),
    "pry_doors": ("ots", "detail", "overhead"),
    "sit_corner": ("overhead", "face"),
    "stand_up": ("overhead", "face"),
    "phone_out": ("detail", "face", "ots"),
    "phone_light": ("ots", "face", "overhead"),
    "look_display": ("detail", "ots", "face"),
    "look_ceiling": ("face", "overhead"),
    "look_camera": ("face", "overhead"),
    "flinch": ("face", "overhead"),
    "shout": ("face", "ots"),
    "listen": ("face", "ots", "overhead"),
    "wait": ("overhead", "face"),
}
HAND_ACTIONS = ("press_button", "knock", "pry_doors")
SIT_OK = ("sit_corner", "wait", "listen", "look_display", "look_ceiling", "look_camera", "phone_out", "phone_light",
          "flinch", "shout", "stand_up")
LIPSYNC_MODES = ("speaking", "all", "off")

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
DOOR_W = 0.86
DISPLAY = (0.0, FRONT + 0.03, 1.93)
CEIL_LIGHT = (0.0, 0.05, H - 0.02)
HATCH = (0.25, 0.30, H)            # люк в потолке
EMERG = (-0.42, FRONT + 0.05, H - 0.07)
REACH = (0.242 + 0.259 + 0.074 + 0.10) * S
SHOULDER_R = (0.212 * S, 0.0, 1.107 * S)   # правое плечо от корня (x — вправо от взгляда, y — вперёд)
SHOULDER_L = (-0.212 * S, 0.0, 1.107 * S)
MAX_PRESS_Z = 0.83                 # выше чиби-рука не достаёт — берём кнопку ниже в том же столбце
FACE_Z = {"stand": 1.62 * S, "sit": 1.28 * S}
SIT_DROP = 0.17                    # насколько ниже грудь сидя (клип Sit_Floor_Idle)
PHONE_SHOW = (-0.06, 0.27, 0.38)   # телефон экраном к камере (часы/заряд): ниже подбородка и вперёд — не прячется за голову

# Длины клипов KayKit (с) и шаг — замер в Blender (сцена сверяет и пишет в отчёт расхождение).
CLIP_LEN = {"Idle_A": 1.067, "Idle_B": 2.133, "Walking_A": 1.067, "Walking_B": 1.067, "Walking_C": 1.6,
            "Sit_Floor_Down": 1.0, "Sit_Floor_Idle": 4.0, "Sit_Floor_StandUp": 1.133, "Hit_A": 0.667, "Hit_B": 0.867}

# Шаг: KayKit Walking_C (осторожная ходьба, 2 шага за 1.6 с, шаг ≈ 18 см у героя) — ровнее Walking_A/B.
# Стопы клипа на месте (замер в Blender, система героя без масштаба): (y левой, z левой, y правой, z правой) на кадр
# клипа (30 к/с); вперёд — −y. Корень едет ровно на столько, на сколько опорная стопа (ниже другой) уезжает назад:
# стопа в контакте стоит на полу (root motion из клипа), а не скользит, как при постоянной скорости корня.
WALK_CLIP = "Walking_C"
WALK_FEET = [(-0.1433, 0.1463, 0.1896, 0.2276), (-0.1336, 0.1461, 0.1865, 0.2279), (-0.11, 0.1458, 0.1778, 0.2288), (-0.0802, 0.1454, 0.1641, 0.2302), (-0.0493, 0.1443, 0.1464, 0.2324), (-0.0184, 0.1422, 0.1254, 0.2352), (0.0142, 0.1408, 0.1019, 0.2386), (0.0445, 0.1405, 0.0768, 0.2427), (0.0685, 0.1405, 0.0508, 0.2475), (0.0875, 0.1404, 0.0247, 0.2528), (0.103, 0.1404, -0.0006, 0.2586), (0.1164, 0.1403, -0.0245, 0.2647), (0.1289, 0.1401, -0.0462, 0.2709), (0.1419, 0.1393, -0.0628, 0.2752), (0.1559, 0.1375, -0.0731, 0.275), (0.1713, 0.1346, -0.079, 0.2696), (0.1884, 0.1306, -0.083, 0.2587), (0.2059, 0.1289, -0.0873, 0.243), (0.2211, 0.1343, -0.0934, 0.2239), (0.2377, 0.1408, -0.102, 0.2036), (0.2515, 0.15, -0.1127, 0.1843), (0.2412, 0.1814, -0.1239, 0.1678), (0.2205, 0.2074, -0.1339, 0.1555), (0.1996, 0.2226, -0.1409, 0.1481), (0.1896, 0.2276, -0.1433, 0.1463), (0.1857, 0.2282, -0.1336, 0.1469), (0.1763, 0.2294, -0.11, 0.1466), (0.1624, 0.231, -0.0802, 0.1459), (0.1446, 0.2332, -0.0494, 0.1444), (0.1237, 0.2359, -0.0184, 0.1422), (0.1004, 0.2393, 0.0142, 0.1408), (0.0756, 0.2433, 0.0445, 0.1405), (0.0499, 0.2479, 0.0685, 0.1405), (0.0242, 0.253, 0.0875, 0.1404), (-0.0009, 0.2587, 0.103, 0.1404), (-0.0246, 0.2647, 0.1164, 0.1403), (-0.0462, 0.2709, 0.1289, 0.1401), (-0.0627, 0.2751, 0.1419, 0.1393), (-0.0728, 0.2748, 0.1559, 0.1375), (-0.0787, 0.2693, 0.1713, 0.1346), (-0.0827, 0.2584, 0.1884, 0.1306), (-0.0871, 0.2428, 0.2077, 0.1253), (-0.0935, 0.2241, 0.2241, 0.1286), (-0.1022, 0.2043, 0.2412, 0.1345), (-0.1128, 0.1855, 0.2514, 0.1501), (-0.1239, 0.1694, 0.2409, 0.1817), (-0.1339, 0.157, 0.2199, 0.2078), (-0.1408, 0.1491, 0.1989, 0.2229), (-0.1433, 0.1463, 0.1896, 0.2276)]
WALK_FOOT_X = 0.1709
WALK_PASS = (8 / 30, 32 / 30)   # кадры клипа, где стопы ближе всего: начало и конец шага — ноги рядом
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

STRIDE = 0.6708            # ед/с клипа Walking_A без масштаба (×S = м/с)
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
              "keywords": r"шахт|трос|противовес|крыш\w* (лифт|кабин)|через люк|на крышу",
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
                  "keywords": r"машинн|технич\w* этаж|лебёдк|лебедк|щит|мотор|котельн",
                  "spots": {"entry": (0.0, -0.6, 3.1416), "center": (0.0, 0.0, 3.1416), "motor": (0.55, 0.3, 2.3)},
                  "targets": {"key": (0.9, 1.0, 0.8), "decal": (-1.88, 0.4, 1.3), "ceiling": (0.0, 0.0, 2.6),
                              "sound": (0.9, 1.0, 0.8), "lamp": (0.0, 0.3, 2.3), "cabinet": (-1.0, 1.85, 1.0)}},
    "corridor": {"label": "коридор технического этажа", "origin": (48.0, 0.0), "bounds": (-0.82, 0.82, FRONT + 0.06, 6.9, 0.15, 2.3),
                 "props": ("бетонные стены", "лампочка", "календарь/надпись на стене", "двери", "двери лифта в торце",
                           "трубы"),
                 "keywords": r"коридор|проход|бетонн\w* (стен|коридор)|в глубин",
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


DOOR_OPEN = {"corridor": 0.2}     # двери лифта в торце коридора — открыты (герой из них вышел), пока не схлопнутся
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
              ("двер|створ", "doors"), ("трос", "cables"), ("противовес", "counterweight"), ("номер", "decal"),
              ("люк", "hatch"), ("стен\\w* шахт", "wall"), ("\\bщит|электрощит", "cabinet"), ("мотор|лебёдк|лебедк", "key"),
              ("коридор|проход|в глубин", "deep"), ("табло", "display"), ("кнопк", "buttons"), ("квартир", "apartment"))


def show_items(loc: str, shot: dict) -> list[dict]:
    """Вещи и место, которые фраза называет: ``[{item, point}]`` — сцена обязана их показать (проверка в отчёте)."""
    text = _low(" ".join([shot.get("text") or "", " ".join(shot.get("props") or [])]))
    out = [{"item": f"место: {loc_of(loc)['label']}", "point": None}]
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


# --------------------------------------------------------------------------- #
# Кадры и выбор действий
# --------------------------------------------------------------------------- #

_RX = {
    "bell": r"вызов|диспетчер|колокол",
    "all_buttons": r"вс[еяю]\s+кнопк|кнопки подряд|подряд",
    "press": r"кнопк|нажа|нажм|нажим|жмет|жмите|жал\b|тыка",
    "pry": r"раздвин|раздвиг|разжа|разжим|двер\w*\s+рук|откры\w*\s+двер|тяну\w*\s+створ",
    "knock_self": r"(стучит|стучал|колотит|колотил|барабанит|барабанил|стукнул)\s+(в|по)",
    "shout": r"крич|закрич|зовет|звал|позвал|помогите|эй\b|ау\b",
    "sit": r"\bсел\b|сел[аи]?\b|сидит|сидел|присел|на пол\b|в угол",
    "stand": r"встал|встает|поднялся|поднимается",
    "light": r"фонар|подсвет|посвет",
    "phone": r"телефон|заряд|связи|сети нет|смартфон|звонит|позвон",
    "display": r"табло|цифр|этаж",
    "ceiling": r"потол|люк|сверху|наверх|над голов|трос|крыш",
    "listen": r"слыш|слуша|голос|шаг|тишин|шорох|постуча|стук",
    "flinch": r"дерн|тряс|рывок|тряхн|качну|опусти|провали|упал|рухну|грохн",
    "wait": r"ждал|ждет|ждать|минут|час|сберечь|сил",
    "viewer": r"\bвы\b|\bвас\b|\bвам\b|\bвами\b|\bне\s+\w+те\b|\bжмите\b|\bпытайтесь\b",
}


def _low(text: str) -> str:
    return str(text or "").lower().replace("ё", "е")


def button_from_text(text: str) -> str:
    low = _low(text)
    if re.search(_RX["bell"], low):
        return "bell"
    if re.search(_RX["all_buttons"], low):
        return "all"
    hit = re.search(r"кнопк\w*\s+(\d{1,2})|(\d{1,2})\s*-?\s*(?:й|ю|го)?\s*кнопк", low)
    if hit:
        return str(max(1, min(12, int(hit.group(1) or hit.group(2)))))
    return "1"


def rule_choice(shot: dict, events: list[dict], dark: bool) -> dict:
    """Действие/эмоция/план по словам фразы — запасной путь без Gemini."""
    low = _low(shot.get("spoken") or shot.get("text"))
    role = shot.get("role") or ""
    kinds = {e["kind"] for e in events}
    action, target, button = "wait", "center", "1"
    if re.search(_RX["pry"], low):
        action, target = "pry_doors", "doors"
    elif re.search(_RX["press"], low) or re.search(_RX["bell"], low):
        action, target, button = "press_button", "panel", button_from_text(low)
    elif re.search(_RX["knock_self"], low):
        action, target = "knock", "doors"
    elif re.search(_RX["shout"], low):
        action, target = "shout", "doors"
    elif re.search(_RX["sit"], low):
        action, target = "sit_corner", "corner"
    elif re.search(_RX["stand"], low):
        action = "stand_up"
    elif re.search(_RX["light"], low):
        action = "phone_light"
    elif re.search(_RX["phone"], low):
        action = "phone_out"
    elif "blackout" in kinds or "jolt" in kinds:
        action = "flinch"
    elif re.search(_RX["listen"], low) or "knock" in kinds:
        action, target = "listen", "doors"
    elif re.search(_RX["ceiling"], low) or "scrape" in kinds:
        action = "look_ceiling"
    elif re.search(_RX["display"], low):
        action, target = "look_display", "display"
    elif re.search(_RX["flinch"], low):
        action = "flinch"
    elif role == "hook" and re.search(_RX["viewer"], low):
        action = "look_camera"
    elif re.search(_RX["wait"], low):
        action = "wait"
    elif role == "hook":
        action = "look_camera"
    emotion = {"hook": "worried", "problem": "scared", "solution": "determined", "mistake": "determined",
               "finale": "scared"}.get(role, "worried")
    if "blackout" in kinds or "jolt" in kinds or action == "flinch":
        emotion = "startled"
    elif action in ("sit_corner", "wait") and dark:
        emotion = "exhausted"
    return {"action": action, "target": target, "button": button, "emotion": emotion,
            "camera": CAMERA_PREFS[action][0], "speaker": "narrator", "by": "rules"}


SPEAKERS = ("narrator", "hero", "other")
SPEECH_VERB = (r"сказал|сказала|крикнул|крикнула|закричал|закричала|позвал|позвала|прошептал|прошептала|ответил|"
               r"ответила|спросил|спросила|произн[её]с|заорал|завопил|шепнул|пробормотал|выдохнул|взмолился|повторил")
OTHER_SPEAKER = (r"голос|кто-то|кто то|спасател|диспетчер|сосед|мужчин|женщин|охранник|динамик|громкоговор|"
                 r"оператор|снаружи|за дверью|незнаком|ш[её]пот|телефон|раздал|послышал|донес|силуэт|фигур")
HERO_SUBJECT = r"\bон\b|\bпарень\b|\bя\b|\bмы\b|\bгерой\b"
_OPEN_Q, _CLOSE_Q = "«“„\"", "»”\""


def speech_labels(text: str, hero_names: tuple[str, ...] = ()) -> list[str]:
    """Кто говорит каждое слово фразы: ``narrator`` (закадровый рассказчик), ``hero`` (прямая речь героя) или
    ``other`` (реплика другого: голос за дверью, диспетчер). Реплика — в кавычках или после «сказал/крикнул…:»;
    кто сказал — по словам вне реплики (голос/спасатель/диспетчер… — другой, иначе — герой). Без реплик — всё
    рассказчик: рот героя под закадровый голос не двигается."""
    tokens = str(text or "").split()
    labels = ["narrator"] * len(tokens)
    spans: list[tuple[int, int]] = []
    start = None
    for i, t in enumerate(tokens):
        core = t.strip(".,!?…;:—–-")
        if not core:
            continue
        if start is None and core[:1] in _OPEN_Q:
            start = i
        if start is not None and (core[-1:] in _CLOSE_Q) and (i > start or len(core) > 1):
            spans.append((start, i))
            start = None
    if start is not None:
        spans.append((start, len(tokens) - 1))
    low = [_low(t) for t in tokens]
    dashes = [i for i, t in enumerate(tokens) if t in ("—", "–", "-")]
    if not spans and dashes and dashes[0] == 0:
        # диалог через тире: «— Эй! — позвал он.»
        nxt = next((i for i in dashes[1:] if re.search(SPEECH_VERB, " ".join(low[i:]))), None)
        if nxt is not None and nxt > 1:
            spans.append((1, nxt - 1))
    if not spans:
        for k, t in enumerate(tokens):
            if t.endswith(":") and k + 1 < len(tokens) and re.search(SPEECH_VERB, " ".join(low[: k + 1])):
                spans.append((k + 1, len(tokens) - 1))
                break
    if not spans:
        return labels
    inside = {i for a, b in spans for i in range(a, b + 1)}
    outside = " ".join(w for i, w in enumerate(low) if i not in inside)
    if not re.search(SPEECH_VERB, outside) and not any(t.endswith(":") for t in tokens):
        return labels          # цитата без «сказал» (надпись, табличка) — читает рассказчик
    names = "|".join(re.escape(_low(n_)[:max(3, len(n_) - 1)]) for n_ in hero_names if n_)
    hero_said = re.search(HERO_SUBJECT + (f"|{names}" if names else ""), outside)
    # герой — только если сказал он сам (он/парень/имя героя) и это не голос/шёпот/телефон/силуэт
    who = "hero" if hero_said and not re.search(OTHER_SPEAKER, outside) else "other"
    for i in inside:
        labels[i] = who
    return labels


def hero_names(script: dict | None) -> tuple[str, ...]:
    """Имена героя из сценария (new_characters: name/key) — по ним реплика приписывается герою."""
    out = []
    for c in (script or {}).get("new_characters") or []:
        for k in ("name", "key"):
            if c.get(k):
                out.append(str(c[k]))
    return tuple(out)


def dialog_lines(sentences: list[dict], names: tuple[str, ...] = ()) -> list[dict]:
    """Озвучка по ролям: ``[{speaker: narrator|hero, text}]`` подряд (реплики «других» читает рассказчик)."""
    out: list[dict] = []
    for sent in sentences:
        tokens = str(sent.get("text") or "").split()
        for tok, lab in zip(tokens, speech_labels(sent.get("text") or "", names)):
            who = "hero" if lab == "hero" else "narrator"
            if out and out[-1]["speaker"] == who:
                out[-1]["text"] += " " + tok
            else:
                out.append({"speaker": who, "text": tok})
    return out


def has_hero_lines(sentences: list[dict], names: tuple[str, ...] = ()) -> bool:
    return any(lab == "hero" for s_ in sentences for lab in speech_labels(s_.get("text") or "", names))


GEMINI_SCHEMA = {
    "type": "object",
    "properties": {"shots": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "n": {"type": "integer"},
            "action": {"type": "string", "enum": list(ACTIONS)},
            "target": {"type": "string", "enum": list(TARGETS)},
            "button": {"type": "string", "enum": list(BUTTONS)},
            "emotion": {"type": "string", "enum": list(EMOTIONS)},
            "camera": {"type": "string", "enum": list(CAMERAS)},
            "speaker": {"type": "string", "enum": list(SPEAKERS)},
            "location": {"type": "string", "enum": list(LOCS)},
            "props": {"type": "array", "items": {"type": "string"}},
            "decal": {"type": "string"},
        },
        "required": ["n", "action", "emotion", "camera", "speaker", "location"]}}},
    "required": ["shots"],
}


def gemini_prompt(title: str, shots: list[dict], events_by_shot: dict[int, list[dict]]) -> str:
    lines = [
        "Ты режиссёр короткой вертикальной 3D-серии. Герой один — игрушечный мальчик в жёлтом дождевике. Голос за кадром "
        "читает текст; на каждый кадр выбери МЕСТО (location — где герой по тексту, только из списка), вещи, которые текст "
        "называет и которые надо показать (props — из списка вещей этого места), надпись на стене, если текст её называет "
        "(decal: «1998», «23», до 14 знаков, иначе пусто), поставленное действие героя, эмоцию лица и план камеры.",
        "Места (location) и вещи в них:\n" + "\n".join(f"{k} — {v['label']}: {', '.join(v['props'])}" for k, v in LOCS.items()),
        "Место меняется только когда текст говорит, что герой туда перешёл (раздвинул двери и вышел в коридор, вылез через "
        "люк в шахту); иначе — там же, где был в прошлом кадре. Кнопки, табло, поручни — только в кабине.",
        f"Серия: «{title}».",
        "Действия (action): " + "; ".join(f"{k} — {v}" for k, v in ACTIONS.items()) + ".",
        "Цель (target) — для approach: " + ", ".join(TARGETS) + ". Кнопка (button) — для press_button.",
        "Эмоции (emotion): " + ", ".join(EMOTIONS) + ".",
        "Планы (camera): " + "; ".join(f"{k} — {v}" for k, v in CAMERAS.items()) + ".",
        "Правила: действие показывает то, о чём фраза (кнопки — press_button, «раздвинул двери» — pry_doors, стучат снаружи — "
        "listen, сам стучит — knock, сел — sit_corner); хук на «вы» — герой смотрит в камеру (look_camera) или показывает "
        "ошибку действием; один и тот же план камеры два кадра подряд нельзя; деталь (detail) — когда важна рука/вещь; "
        "общий сверху (overhead) — для пространства и одиночества; крупно лицо — на реакции. speaker — кто говорит "
        "реплику в кадре: narrator — закадровый рассказчик (почти всегда: рассказ в 3-м лице, обращение к зрителю), "
        "hero — только прямая речь самого героя (в кавычках или «— крикнул Даня»), other — реплика другого (голос за "
        "дверью, диспетчер). Рот героя двигается только на hero.",
        "Кадры (n, роль, время, события на словах, текст):",
    ]
    for s in shots:
        ev = ", ".join(sorted({e["kind"] for e in events_by_shot.get(s["n"], [])})) or "—"
        lines.append(f'{s["n"]}. [{s.get("role") or "—"}] {s["t1"] - s["t0"]:.1f} с; события: {ev}; «{s.get("spoken") or s["text"]}»')
    lines.append("Ответ — JSON по схеме: shots[] на каждый n.")
    return "\n".join(lines)


def gemini_choices(title: str, shots: list[dict], events_by_shot: dict[int, list[dict]], client: Any = None,
                   model: str | None = None) -> dict[int, dict]:
    """Выбор Gemini (бесплатный текст): ``{n: {action, target, button, emotion, camera, speaks}}``; ошибка — ``{}``."""
    from app.ai.gemini import GeminiClient, _extract_response_text
    from app.settings import settings

    if client is None and not settings.gemini_api_key:
        return {}
    payload = {"contents": [{"role": "user", "parts": [{"text": gemini_prompt(title, shots, events_by_shot)}]}],
               "generationConfig": {"temperature": 0.4, "responseMimeType": "application/json",
                                    "responseSchema": GEMINI_SCHEMA}}
    response = (client or GeminiClient(timeout=90)).generate_content(model or settings.gemini_text_model, payload)
    data = json.loads(_extract_response_text(response) or "{}")
    out: dict[int, dict] = {}
    for item in data.get("shots") or []:
        try:
            n = int(item["n"])
        except (KeyError, TypeError, ValueError):
            continue
        if item.get("action") not in ACTIONS or item.get("emotion") not in EMOTIONS or item.get("camera") not in CAMERAS:
            continue
        out[n] = {"action": item["action"], "target": item.get("target") if item.get("target") in TARGETS else "center",
                  "button": item.get("button") if item.get("button") in BUTTONS else "1",
                  "emotion": item["emotion"], "camera": item["camera"],
                  "speaker": item.get("speaker") if item.get("speaker") in SPEAKERS else "narrator", "by": "gemini",
                  "location": item.get("location") if item.get("location") in LOCS else "",
                  "props": [str(x)[:40] for x in (item.get("props") or [])][:4],
                  "decal": str(item.get("decal") or "").strip().upper()[:14]}
    return out


# Часы/телефон в тексте («на часах было 03:33», «телефон показывал 14%») — герой держит телефон, на экране значение.
SCREEN_CUE = (r"на час(ах|ы)\b|час\w* показ|телефон\w* показ|на экран|экран\w* (телефон|показ)|на телефон\w*\s+было|"
              r"посмотрел\w* на (часы|телефон)|датчик\w* .{0,20}час")
HOOK_FACE_SEC = 1.75     # крючок: сначала лицо героя с реакцией (под названием серии), потом названная вещь


def screen_value(text: str) -> str:
    """Что показать на экране телефона: время «03:33», температура «−12 °C» или процент «14%» — первое найденное."""
    raw = str(text or "")
    hit = re.search(r"(?<![\d:])(\d{1,2})[:.](\d{2})(?![\d:])", raw)
    if hit and int(hit.group(1)) < 24 and int(hit.group(2)) < 60:
        return f"{int(hit.group(1)):02d}:{hit.group(2)}"
    hit = re.search(r"([−-]\s?)?(\d{1,3})\s*°\s*[CcСс]?", raw)
    if hit:
        return f"{'−' if hit.group(1) else ''}{hit.group(2)} °C"
    hit = re.search(r"(\d{1,3})\s*%", raw)
    if hit:
        return f"{hit.group(1)}%"
    return ""


def screen_shots(shots: list[dict]) -> dict[int, str]:
    """Планы, где фраза называет часы/экран телефона: ``{индекс: значение на экране}``. Значения нет в самом плане —
    берём из его фразы (соседние планы той же фразы: «Датчик на часах упал | до −12 °C»)."""
    out: dict[int, str] = {}
    for k, s in enumerate(shots):
        if not re.search(SCREEN_CUE, _low(s.get("text"))):
            continue
        value = screen_value(s.get("text"))
        if not value:
            sent = set(s.get("sentences") or [])
            same = [x for x in shots if sent & set(x.get("sentences") or [])]
            value = next((v for v in (screen_value(x.get("text")) for x in same) if v), "")
        if value:
            out[k] = value
    return out


def hook_items(s: dict) -> list[dict]:
    """Вещи, которые названы в первой фразе (кроме места)."""
    loc = s.get("location") if s.get("location") in LOCS else "cabin"
    return [x for x in show_items(loc, s) if not str(x["item"]).startswith("место")]


def split_hook(shots: list[dict]) -> list[dict]:
    """Первый план — крупно лицо героя с реакцией (не табло). Если фраза называет вещь (табло, надпись), а план
    длинный — второй кусок той же фразы показывает её. Повторный вызов ничего не меняет (``hook``)."""
    if not shots or shots[0].get("hook"):
        return shots
    first = dict(shots[0])
    span = float(first.get("t1") or 0.0) - float(first.get("t0") or 0.0)
    items = hook_items(first)
    if first["action"] in ("look_display", "wait", "approach", "phone_light", "look_ceiling", *HAND_ACTIONS):
        first["action"] = "look_camera"
        first["target"] = "center"
    if first.get("emotion") in ("calm", "relieved", "determined", None):
        first["emotion"] = "worried"
    first["camera"] = "face"
    first["hook"] = 1
    if not items or span < HOOK_FACE_SEC + 1.0:
        return [{**first, "show_none": bool(items)}, *shots[1:]]
    cut = round(float(first["t0"]) + max(HOOK_FACE_SEC, min(span - 1.0, span * 0.55)), 3)
    second = {**dict(shots[0]), "t0": cut, "action": "look_display", "target": "display", "camera": "detail", "hook": 2}
    first = {**first, "t1": cut, "show_none": True}
    out = [first, second, *shots[1:]]
    for n_, s_ in enumerate(out):
        out[n_] = {**s_, "n": n_}
    return out


def fix_sequence(shots: list[dict]) -> list[dict]:
    """Выполнимость и монтаж: сидя — сначала встать (``stand_first``), стоя «встать» — ждать; кнопка — достижимая;
    план камеры не повторяет прошлый. Рот говорит только на прямой речи героя (``plan_shots``: ``speech``).
    Крючок — крупно лицо (``split_hook``); часы/экран в тексте — телефон в руке с этим значением (``screen``)."""
    shots = split_hook([dict(s) for s in shots])
    screens = screen_shots(shots)
    posture = "stand"
    prev_cam = ""
    out = []
    for k, s in enumerate(shots):
        s = dict(s)
        if k in screens and s["action"] not in HAND_ACTIONS and not s.get("hook"):
            s["screen"] = screens[k]
            s["action"] = "phone_out"
            s["camera"] = "detail"
        a = s["action"]
        s["stand_first"] = False
        if a == "stand_up" and posture == "stand":
            s["action"] = a = "wait"
        if a == "sit_corner" and posture == "sit":
            s["action"] = a = "wait"
        if posture == "sit" and a not in SIT_OK:
            s["stand_first"] = True
        loc = s.get("location") if s.get("location") in LOCS else "cabin"
        if out and out[-1].get("location") != loc:
            posture = "stand"     # другое место — склейкой, герой стоит
            s["stand_first"] = False
        s["location"] = loc
        if loc != "cabin":
            # кнопки и табло — только в кабине; двери лифта есть в коридоре и на площадке
            has_doors = "doors" in LOCS[loc]["spots"]
            if a == "press_button" or (a in ("pry_doors", "knock") and not has_doors):
                s["action"] = a = "approach" if has_doors else "look_display"
                s["target"] = "doors" if has_doors else "center"
            elif a == "sit_corner":
                s["action"] = a = "wait"
        if a == "press_button":
            s["target"] = "panel"
            s["button"] = s.get("button") if s.get("button") in BUTTONS else "1"
        elif a in ("knock", "pry_doors", "shout", "listen"):
            s["target"] = "doors"
        elif a == "sit_corner":
            s["target"] = "corner"
        if a == "sit_corner" or (posture == "sit" and a in SIT_OK and a != "stand_up"):
            posture = "sit"
        else:
            posture = "stand"
        cam = s.get("camera") if s.get("camera") in CAMERAS else CAMERA_PREFS[a][0]
        if cam not in CAMERA_PREFS[a] and a in ("look_camera", "sit_corner", "stand_up", "flinch", "look_ceiling"):
            cam = CAMERA_PREFS[a][0]   # спиной к объективу/деталь без детали — не тот план
        if cam == prev_cam:
            cam = next((c for c in CAMERA_PREFS[a] if c != prev_cam), None) or next(c for c in CAMERAS if c != prev_cam)
        if cam == "ots" and a == "approach":
            cam = "overhead" if prev_cam != "overhead" else "face"   # идёт от камеры — со спины лишь капюшон
        if cam == "face" and a == "press_button":
            # лицом к панели вбок — спереди виден только капюшон: деталь (рука, кнопка, профиль) или сверху
            cam = "detail" if prev_cam != "detail" else "overhead"
        if cam == "ots" and a in HAND_ACTIONS:
            # со спины руки не видно — рука, кнопка/двери и лицо сбоку (или сверху, если деталь только что была)
            cam = "detail" if prev_cam != "detail" else "overhead"
        if cam == "detail" and a not in ("press_button", "phone_out", "phone_light", "look_display", "knock", "pry_doors"):
            cam = next(c for c in (*CAMERA_PREFS[a], "face", "overhead") if c not in ("detail", prev_cam))
        if s.get("screen"):
            cam = "detail"        # экран телефона со значением — только деталью (даже после детали)
        if s.get("hook") == 1:
            cam = "face"          # крючок — лицо героя
        s["camera"] = cam
        s["posture"] = posture
        prev_cam = cam
        out.append(s)
    return out


def plan_shots(script: dict, words: list[dict], total: float, *, gemini: bool = True, client: Any = None) -> dict:
    """Сценарий + слова озвучки → ``{shots, events, words, director}``: кадры с действием, эмоцией, планом."""
    from app import series3d as S3

    sentences = [s for s in (script.get("sentences") or []) if str(s.get("text") or "").strip()]
    if not sentences:
        raise S3.DirectionError("в сценарии нет фраз")
    if not words:
        raise S3.DirectionError("нет слов озвучки")
    words = S3.align_words(" ".join(s["text"] for s in sentences), words)
    spans = S3.sentence_spans(sentences, words, total)
    shots = S3.split_shots(spans, words)
    for k, s in enumerate(shots):
        s["n"] = k
        s["t1"] = shots[k + 1]["t0"] if k + 1 < len(shots) else float(total)
    events = S3.detect_events(spans, words)
    by_shot: dict[int, list[dict]] = {}
    for e in events:
        k = next((s["n"] for s in shots if s["t0"] <= e["t"] < s["t1"]), len(shots) - 1)
        by_shot.setdefault(k, []).append(e)
    for sp in spans:
        low_ = _low(sp["text"])
        if re.search(r"двер|створ", low_) and re.search(r"схлопнул|захлопнул|закрыл|сомкнул|сдвинул", low_):
            k_ = S3.find_word(words, sp["w0"], sp["w1"], r"^(схлопнул|захлопнул|закрыл|сомкнул|сдвинул)")
            events.append({"kind": "doors_close", "t": float(words[k_]["start"]) if k_ is not None else sp["t0"],
                           "sentence": sp["i"], "role": sp["role"]})
    blackout = next((e["t"] for e in events if e["kind"] == "blackout"), None)
    choices: dict[int, dict] = {}
    director, error = "rules", ""
    if gemini:
        try:
            choices = gemini_choices(str(script.get("title") or ""), shots, by_shot, client=client)
            director = "gemini" if choices else "rules"
        except Exception as exc:  # noqa: BLE001 - сеть/квота/ответ не по схеме — правила
            error = f"{type(exc).__name__}: {str(exc)[:160]}"
    names = hero_names(script)
    labels = [lab for sent in sentences for lab in speech_labels(sent["text"], names)]
    labels = (labels + ["narrator"] * len(words))[: len(words)]
    planned = []
    where = "cabin"
    decal_at: dict[str, str] = {}
    for s in shots:
        dark = blackout is not None and s["t0"] + 0.3 >= blackout
        pick = choices.get(s["n"]) or rule_choice(s, by_shot.get(s["n"], []), dark)
        text_ = s.get("spoken") or s["text"]
        by_rules = rule_location(text_, where)
        loc = pick.get("location") if pick.get("location") in LOCS else by_rules
        if by_rules != where and loc == where:
            loc = by_rules       # текст явно назвал другое место — правило сильнее
        where = loc
        decal = pick.get("decal") or decal_from_text(text_)
        if re.fullmatch(r"\d{1,2}[:.]\d{2}", decal or ""):
            decal = ""            # время («03:33») — часы/телефон, не надпись на стене
        if decal and decal_at.setdefault(loc, decal) != decal:
            decal = ""            # на месте одна надпись — первая названная; другой в кадре не будет
        pick = {**pick, "location": loc, "decal": decal, "props": list(pick.get("props") or [])}
        idx = [k for k in range(len(words)) if s["t0"] <= float(words[k]["start"]) < s["t1"]]
        said = {labels[k] for k in idx} - {"narrator"}
        if said and pick.get("speaker") in ("hero", "other") and pick.get("by") == "gemini":
            for k in idx:   # Gemini уточняет, чья найденная реплика (кавычки/«сказал:» нашли правила)
                if labels[k] != "narrator":
                    labels[k] = pick["speaker"]
            said = {pick["speaker"]}
        speaker = "hero" if "hero" in said else ("other" if said else "narrator")
        planned.append({"n": s["n"], "t0": round(float(s["t0"]), 3), "t1": round(float(s["t1"]), 3),
                        "text": s.get("spoken") or s["text"], "role": s.get("role") or "",
                        "sentences": s.get("sentences") or [s.get("i", 0)], **pick,
                        "speaker": speaker, "speaks": speaker == "hero"})
    windows: list[list[float]] = []
    for k, lab in enumerate(labels):
        if lab != "hero":
            continue
        a_, b_ = float(words[k]["start"]), float(words[k]["end"])
        if windows and a_ - windows[-1][1] < 0.35:
            windows[-1][1] = b_
        else:
            windows.append([a_, b_])
    return {"shots": fix_sequence(planned), "events": events, "words": words, "director": director,
            "director_error": error, "speech": {"labels": labels, "hero_windows": [[round(a_, 3), round(b_, 3)]
                                                                                   for a_, b_ in windows]}}


# --------------------------------------------------------------------------- #
# Лип-синк
# --------------------------------------------------------------------------- #

# Визем-группы: (открытие, округление, растяжка в ширину)
VISEMES = {
    "а": (1.0, 0.0, 0.35), "я": (0.95, 0.0, 0.35),
    "о": (0.75, 1.0, 0.0), "ё": (0.75, 1.0, 0.0),
    "у": (0.38, 1.0, 0.0), "ю": (0.4, 0.9, 0.0),
    "э": (0.62, 0.0, 0.75), "е": (0.55, 0.0, 0.75),
    "и": (0.32, 0.0, 1.0), "ы": (0.4, 0.0, 0.85),
    "м": (0.0, 0.0, 0.0), "б": (0.0, 0.0, 0.0), "п": (0.0, 0.0, 0.0),
    "ф": (0.1, 0.0, 0.3), "в": (0.1, 0.0, 0.3),
}
VOWELS = set("аяоёуюэеиы")
_DIGITS = {"0": "ноль", "1": "один", "2": "два", "3": "три", "4": "четыре", "5": "пять", "6": "шесть", "7": "семь",
           "8": "восемь", "9": "девять"}


def _letters(word: str) -> str:
    w = str(word).lower()
    w = "".join(_DIGITS.get(c, c) for c in w)
    return re.sub(r"[^а-яё]", "", w) or "а"


def viseme_keys(words: list[dict]) -> list[tuple[float, float, tuple[float, float, float]]]:
    """``[(t0, t1, (open, round, wide))]`` — буквы слова поровну по его длительности, гласные длиннее (×1.7);
    согласные — полуоткрыто с формой соседней гласной (коартикуляция), губные — закрыто."""
    keys = []
    for w in words:
        t0, t1 = float(w["start"]), float(w["end"])
        if t1 - t0 < 0.03:
            continue
        letters = _letters(w["word"])
        weights = [1.7 if c in VOWELS else 1.0 for c in letters]
        total = sum(weights)
        t = t0
        for i, c in enumerate(letters):
            d = (t1 - t0) * weights[i] / total
            if c in VISEMES:
                v = VISEMES[c]
            else:
                near = next((letters[j] for j in (i + 1, i - 1, i + 2) if 0 <= j < len(letters) and letters[j] in VOWELS), "")
                o, r, wd = VISEMES.get(near, (0.3, 0.0, 0.3))
                v = (0.22, r * 0.6, wd * 0.6)
            keys.append((t, t + d, v))
            t += d
    return keys


def lipsync(words: list[dict], n_frames: int, fps: int = FPS, envelope: list[float] | None = None,
            windows: list[tuple[float, float]] | None = None, gain: float = 1.0) -> list[tuple[float, float, float]]:
    """Покадрово ``(open, round, wide)`` 0..1: визем-цели по таймингам слов, между словами рот закрыт;
    атака быстрее отпускания; огибающая озвучки (если есть) — громче → шире. ``windows`` — где рот говорит
    (вне окон — закрыт, на краях — 0.12 с перехода)."""
    keys = viseme_keys(words)
    target = [(0.0, 0.0, 0.0)] * n_frames
    ki = 0
    for f in range(n_frames):
        t = f / fps
        while ki < len(keys) and keys[ki][1] <= t:
            ki += 1
        if ki < len(keys) and keys[ki][0] <= t < keys[ki][1]:
            target[f] = keys[ki][2]
    env = None
    if envelope:
        peak = sorted(envelope)[int(len(envelope) * 0.95)] or 1.0
        env = [min(1.2, max(0.0, e / peak)) for e in envelope]
    out = []
    o = r = w = 0.0
    dt = 1.0 / fps
    for f in range(n_frames):
        to, tr, tw = target[f]
        if env is not None and f < len(env):
            to *= 0.35 + 0.75 * env[f]
        to = min(1.0, to * gain)
        k_open = 1.0 - math.exp(-dt / (0.028 if to > o else 0.05))
        k_shape = 1.0 - math.exp(-dt / 0.06)
        o += (to - o) * k_open
        r += (tr - r) * k_shape
        w += (tw - w) * k_shape
        out.append((o, r, w))
    if windows is not None:
        def gate(t: float) -> float:
            best = 0.0
            for a, b in windows:
                if a - 0.12 <= t <= b + 0.12:
                    best = max(best, min(1.0, (t - (a - 0.12)) / 0.12, ((b + 0.12) - t) / 0.12))
            return best
        out = [(o * gate(f / fps), r, w) for f, (o, r, w) in enumerate(out)]
    return [(round(o, 4), round(r, 4), round(w, 4)) for o, r, w in out]


# --------------------------------------------------------------------------- #
# Исполнение: покадровые дорожки
# --------------------------------------------------------------------------- #

def _pulse(x: float, rise: float = 0.09, decay: float = 0.45) -> float:
    if x < 0:
        return 0.0
    if x < rise:
        return m.smoothstep(x / rise)
    return math.exp(-(x - rise) / decay)


def _ang(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


class _Track:
    """Ключи ``(t, значение)`` со сглаживанием, значение — число/кортеж/строка (строка — ступенькой)."""

    def __init__(self, default: Any):
        self.keys: list[tuple[float, Any]] = [(0.0, default)]

    def key(self, t: float, v: Any) -> None:
        self.keys.append((float(t), v))

    def curve(self, ease: str = "smooth") -> m.Curve:
        return m.Curve(sorted(self.keys, key=lambda k: k[0]), ease=ease)


def perform(job: dict, clip_len: dict | None = None, stride: float | None = None) -> dict:
    """Задание серии → покадровые дорожки исполнения (детерминированно; то же в Blender и в хэшах кусков)."""
    clip_len = {**CLIP_LEN, **(clip_len or {})}
    stride = float(stride or STRIDE)
    fps = int(job.get("fps") or FPS)
    dur = float(job["duration"])
    nf = int(round(dur * fps))
    times = [f / fps for f in range(nf)]
    shots = job["shots"]
    events = job.get("events") or []
    rnd = random.Random(int(job.get("seed") or 7))

    track = m.ClipTrack()
    walks: list[dict] = []           # окна шага: корень по опорной стопе клипа (walk_root)
    pos_keys: list[tuple[float, tuple[float, float]]] = []   # телепорт на склейке
    head_keys = _Track(0.0)
    gaze = _Track((0.0, FRONT, 1.0))
    reaches: list[dict] = []          # правая рука: нажатия/стук ({side, target(t), infl(t)} как Reach)
    holds: list[dict] = []            # руки в системе героя (телефон, двери)
    emo = _Track(EMOTIONS["calm"])
    lean = _Track(0.0)                # наклон груди: + вперёд, − назад
    roll = _Track(0.0)                # голова набок (слушает)
    startles: list[tuple[float, float]] = []
    phone_iv: list[list] = []         # [начало, конец|None] — телефон в левой руке
    phone_mode = _Track(0.0)          # 0 — экран, 1 — фонарик
    show_track = _Track(0.0)          # 1 — телефон вытянут экраном к камере (значение на экране)
    flash_track = _Track(0.0)         # фонарик включён
    contacts: list[dict] = []         # касания (для подгонки IK по мешу и звуков)
    shout_bursts: list[tuple[float, float]] = []
    speak_windows: list[tuple[float, float]] = []
    seg_clip = None

    def add_clip(name: str, t: float, blend: float = 0.3, speed: float = 1.0, loop: bool = True, offset: float = 0.0):
        nonlocal seg_clip
        if track.segments and t < track.segments[-1].start + 0.05:
            t = track.segments[-1].start + 0.05
        track.add(m.ClipSegment(name, t, clip_len[name], blend=blend, speed=speed, loop=loop, offset=offset))
        seg_clip = name

    blackout = next((e["t"] for e in events if e["kind"] == "blackout"), None)
    light_back = next((e["t"] for e in events if e["kind"] == "light_back" and blackout is not None and e["t"] > blackout), None)

    def dark(t: float) -> bool:
        return blackout is not None and t >= blackout and (light_back is None or t < light_back)

    first_spot, first_head = spot_for(shots[0].get("target") if shots[0]["action"] == "approach" else "center")
    if shots and shots[0].get("location") in LOCS and shots[0]["location"] != "cabin":
        first_spot, first_head = loc_spot(shots[0]["location"], "entry")
    pos, heading, posture = first_spot, first_head, "stand"
    phone_out = False
    phone_hold: dict | None = None
    cur_loc = shots[0].get("location") if shots and shots[0].get("location") in LOCS else "cabin"
    door_tracks: dict[str, _Track] = {}
    pos_keys.append((0.0, pos))
    head_keys.keys = [(0.0, heading)]
    add_clip("Idle_A", 0.0, blend=0.01)
    shot_out = []
    for k, s in enumerate(shots):
        t0, t1 = float(s["t0"]), float(s["t1"])
        a = s["action"]
        span = t1 - t0
        loc = s.get("location") if s.get("location") in LOCS else "cabin"
        if loc != cur_loc:
            # другое место — склейкой: герой уже там, стоит у входа (телефон в руке остаётся)
            spot0, h0 = loc_spot(loc, "entry" if loc != "cabin" else "center")
            pos_keys.append((t0, spot0))
            head_keys.key(t0 - 1e-3, heading)
            head_keys.key(t0, h0)
            pos, heading, posture = spot0, h0, "stand"
            if seg_clip != "Idle_A":
                add_clip("Idle_A", t0, blend=0.01)
            cur_loc = loc
        origin = loc_of(loc)["origin"]

        def W(p_: tuple, o_=origin) -> tuple:
            return (p_[0] + o_[0], p_[1] + o_[1], *p_[2:])
        dtrack = door_tracks.setdefault(loc, _Track(DOOR_OPEN.get(loc, 0.0)))
        # куда и как стоять
        if a == "press_button":
            want = ["1", "3", "2", "4"] if s.get("button") == "all" else [reachable(s.get("button") or "1")]
            want = [reachable(b) for b in want]
            spot, hd = press_stand(want)
        elif a == "knock":
            spot, hd = spot_for("knock")
            spot = W(spot)
        elif a == "pry_doors":
            spot, hd = spot_for("pry")
            spot = W(spot)
        elif a == "sit_corner":
            spot, hd = spot_for("corner")
        elif a == "approach":
            spot, hd = loc_spot(loc, s.get("target") or "center")
        elif loc == "cabin" and a in ("shout", "listen") and posture == "stand" and abs(_ang(heading)) > math.radians(35):
            spot, hd = pos, 0.0
        elif loc == "cabin" and posture == "stand" and pos[1] < FRONT + 0.45 and a not in ("shout", "listen", "flinch"):
            # у самых дверей лицу некуда снимать — отходит в середину кабины
            spot, hd = spot_for("center")
        else:
            spot, hd = pos, heading
            # фраза называет вещь за спиной (лампа, календарь, глубина коридора) — герой к ней повёрнут: за плечом
            # видно и его, и её (иначе «через плечо» — пустой коридор, а план без героя — брак)
            named = [x["point"] for x in show_items(loc, s) if x.get("point") and not str(x["item"]).startswith("место")]
            if named and loc != "cabin" and a not in HAND_ACTIONS and a not in ("look_camera", "phone_out") \
                    and posture == "stand" and not s.get("hook"):
                p_ = named[0]
                want_h = m.heading_of((p_[0] - pos[0], p_[1] - pos[1]))
                if math.hypot(p_[0] - pos[0], p_[1] - pos[1]) > 0.35 and abs(_ang(want_h - heading)) > math.radians(55):
                    # разворот склейкой: на новом плане герой уже смотрит туда
                    head_keys.key(t0 - 1e-3, heading)
                    head_keys.key(t0, want_h)
                    heading = hd = want_h
                fx_, fy_ = facing(heading)
                if not inside_loc(loc, (pos[0] - fx_ * 1.1, pos[1] - fy_ * 1.1, 1.0), 0.05):
                    # спиной в стену/двери — камере за плечом негде встать: склейкой на шаг-другой вперёд
                    for k_ in (0.5, 0.75, 1.0, 1.3):
                        p2 = (round(pos[0] + fx_ * k_, 4), round(pos[1] + fy_ * k_, 4))
                        if inside_loc(loc, (p2[0] - fx_ * 1.1, p2[1] - fy_ * 1.1, 1.0), 0.05) and \
                                inside_loc(loc, (p2[0], p2[1], 1.0), 0.25):
                            pos_keys.append((t0, p2))
                            pos = spot = p2
                            break
            if s.get("screen") and posture == "stand":
                # экран телефона снимают спереди: перед героем нужно ~0.7 м места (не носом в двери/стену)
                for dh_ in (0.0, math.pi, math.pi / 2, -math.pi / 2):
                    fx_, fy_ = facing(heading + dh_)
                    if inside_loc(loc, (pos[0] + fx_ * 0.75, pos[1] + fy_ * 0.75, 1.0), 0.05):
                        if dh_:
                            head_keys.key(t0 - 1e-3, heading)
                            head_keys.key(t0, heading + dh_)
                            heading = hd = heading + dh_
                        break
        moved = math.dist(spot, pos) > 0.04
        tt = t0
        # встать с пола (на камеру) или переставить на склейке
        if posture == "sit" and (s.get("stand_first") or a == "stand_up" or moved):
            if a == "stand_up" or (s.get("stand_first") and not moved):
                add_clip("Sit_Floor_StandUp", tt + 0.05, blend=0.2, loop=False)
                tt += clip_len["Sit_Floor_StandUp"] + 0.05
                add_clip("Idle_A", tt - 0.15, blend=0.25)
            else:
                add_clip("Idle_A", t0, blend=0.01)
            posture = "stand"
        walk_dist = math.dist(spot, pos)
        steps, clip_sec = walk_plan(walk_dist)
        wsec = walk_seconds(clip_sec)
        dirv = (spot[0] - pos[0], spot[1] - pos[1]) if moved else (0.0, 0.0)
        walk_head = m.heading_of(dirv) if moved else heading
        t_turn0 = 0.25 + 0.45 * abs(_ang(walk_head - heading)) / math.pi if moved else 0.0
        far = walk_dist > MAX_WALK_STEPS * STEP_LEN * 1.25
        if moved:   # шаги разворота в начале/конце (см. ниже) — тоже время шага
            _h = clip_len[WALK_CLIP] / 2
            _d0, _d1 = abs(_ang(walk_head - heading)), abs(_ang(hd - walk_head))
            wsec = walk_seconds(clip_sec + (_h if _d0 > math.radians(100) else 0.0)
                                + (0.0 if _d1 <= math.radians(40) else _h * (2 if _d1 > math.radians(100) else 1)))
        if moved and (k == 0 or far or t_turn0 + wsec > (0.9 if a == "approach" else 0.6) * span or a == "sit_corner" and walk_dist > 0.45):
            # склейка: герой уже на месте (переход между кадрами не показываем)
            pos_keys.append((t0, spot))
            head_keys.key(t0 - 1e-3, heading)
            head_keys.key(t0, hd)
            if posture == "stand" and seg_clip not in ("Idle_A",):
                add_clip("Idle_A", t0, blend=0.01)
            pos, heading = spot, hd
        elif moved:
            # сначала взгляд к цели; корпус: малый поворот — на месте до шага, большой — на первом шаге (вокруг
            # опорной стопы); 1–3 шага с разгоном и торможением; к цели лицом — на последнем шаге (большой — лишний шаг)
            gaze.key(tt, (spot[0], spot[1], 0.5))
            dh0 = _ang(walk_head - heading)
            h_walk = heading + dh0
            dh_end = _ang(hd - walk_head)
            half = clip_len[WALK_CLIP] / 2
            # большой разворот — на шаге (корпус вокруг опорной стопы); больше 100° — за два шага, не рывком
            turn0 = half * (2 if abs(dh0) > math.radians(100) else 1)
            turn1 = half * (2 if abs(dh_end) > math.radians(100) else 1)
            if abs(dh0) > math.radians(100):
                clip_sec += half
            if abs(dh_end) > math.radians(40):
                clip_sec += turn1
            pre = t_turn0 if abs(dh0) <= math.radians(40) else 0.15
            head_keys.key(tt, heading)
            ws = tt + pre - 0.05
            we = ws + walk_seconds(clip_sec)
            if abs(dh0) <= math.radians(40):
                head_keys.key(tt + pre, h_walk)
            else:
                head_keys.key(ws + 0.25, heading)
                head_keys.key(ws + 0.25 + turn0, h_walk)
            track.add(m.ClipSegment(WALK_CLIP, max(ws, track.segments[-1].start + 0.05), clip_len[WALK_CLIP], blend=0.3,
                                    loop=True, offset=WALK_PASS[0], ease_in=0.35, ease_out=0.35, end=we))
            walks.append({"t0": ws, "t1": we + 0.4, "target": spot, "kind": "walk"})
            h_end = h_walk + dh_end
            if abs(dh_end) > math.radians(40):
                head_keys.key(we - turn1 - 0.1, h_walk)
                head_keys.key(we - 0.15, h_end)
            else:
                head_keys.key(we - 0.45, h_walk)
                head_keys.key(we + 0.25, h_end)
            add_clip("Idle_A", we - 0.05, blend=0.4)
            heading, pos = h_end, spot
            tt = we + 0.2
        elif abs(_ang(hd - heading)) > math.radians(18):
            dh = _ang(hd - heading)
            mid = facing(heading + dh / 2)
            p_end = (pos[0] + mid[0] * STEP_LEN, pos[1] + mid[1] * STEP_LEN)
            room = inside_loc(loc, (p_end[0], p_end[1], 1.0), 0.3)
            if abs(dh) > math.radians(50) and room and posture == "stand" and span > 2.2:
                # разворот шагом: один шаг (корень по опорной стопе), корпус поворачивается на шаге
                ws = tt + 0.05
                we = ws + walk_seconds(CLIP_LEN[WALK_CLIP] / 2, 0.3)
                track.add(m.ClipSegment(WALK_CLIP, max(ws, track.segments[-1].start + 0.05), clip_len[WALK_CLIP],
                                        blend=0.25, loop=True, offset=WALK_PASS[0], ease_in=0.3, ease_out=0.3, end=we))
                walks.append({"t0": ws, "t1": we + 0.3, "target": None, "kind": "turn"})
                head_keys.key(ws, heading)
                head_keys.key(we - 0.1, heading + dh)
                add_clip("Idle_A", we - 0.05, blend=0.35)
                pos = p_end
                heading += dh
                tt = we + 0.1
            else:
                # небольшой поворот — на месте, плавно (ноги почти не уходят)
                t_turn = 0.35 + 0.6 * abs(dh) / math.pi
                head_keys.key(tt + 0.05, heading)
                head_keys.key(tt + 0.05 + t_turn, heading + dh)
                heading += dh
                tt += t_turn
        a0 = min(tt + 0.05, t1 - 0.3)
        # эмоция кадра (наплыв 0.35 с)
        emo.key(t0 + 0.02, emo.keys[-1][1] if emo.keys else EMOTIONS["calm"])
        emo.key(t0 + 0.37, EMOTIONS.get(s.get("emotion") or "calm", EMOTIONS["calm"]))
        lean.key(t0, lean.keys[-1][1])
        roll.key(t0, roll.keys[-1][1])
        lean_v, roll_v = 0.0, 0.0
        shot_end = t1

        def gkey(t: float, v: Any) -> None:
            if t < shot_end - 0.05:
                gaze.key(max(t, t0), v)
        cam_subject = "face"
        # действие
        if a == "press_button":
            seq = ["1", "3", "2", "4"] if s.get("button") == "all" else [reachable(s.get("button") or "1")]
            seq = [reachable(b) for b in seq]
            per = 0.55 if len(seq) > 1 else 1.4
            if s.get("button") == "bell":
                seq = ["bell", "bell"]
                per = 0.9
            t = a0 + 0.1
            rest = None
            for b in seq:
                if t + per > t1 + 0.2:
                    break
                bp = button_pos(b)
                if len(seq) > 1:
                    r = {"button": bp, "normal": BTN_NORMAL, "t_start": t, "t_contact": t + per * 0.55,
                         "t_release": t + per * 0.72, "t_end": t + per * 1.02, "hover": 0.04, "depth": 0.006,
                         "id": b, "side": "r", "rest": rest}
                else:
                    r = {"button": bp, "normal": BTN_NORMAL, "t_start": t, "t_contact": t + 0.6, "t_release": t + 0.85,
                         "t_end": t + 1.4, "hover": 0.05, "depth": 0.006, "id": b, "side": "r", "rest": rest}
                reaches.append(r)
                contacts.append({"kind": "press", "t": r["t_contact"], "button": b, "point": bp, "normal": BTN_NORMAL})
                rest = m.v_add(bp, m.v_scale(BTN_NORMAL, 0.05))
                gkey(t - 0.15, bp)
                t += per * (0.75 if len(seq) > 1 else 1.0)
            gkey(t + 0.2, DISPLAY if s.get("button") != "bell" else (BELL[0], BELL[1], BELL[2] + 0.25))
            add_clip("Idle_A", a0, blend=0.3) if seg_clip != "Idle_A" else None
            cam_subject = "press"
        elif a == "knock":
            hits = [a0 + 0.35 + i * 0.38 for i in range(3)]
            hits = [h for h in hits if h < t1 - 0.15] or [a0 + 0.2]
            kp = W(KNOCK_POINT)
            reaches.append({"button": kp, "normal": DOOR_NORMAL, "t_start": a0, "t_contact": hits[0],
                            "t_release": hits[-1] + 0.05, "t_end": hits[-1] + 0.6, "hover": 0.06, "depth": 0.004,
                            "id": "door", "side": "r", "hits": hits})
            for h in hits:
                contacts.append({"kind": "knock", "t": h, "point": kp, "normal": DOOR_NORMAL})
            gkey(a0 - 0.1, (kp[0], kp[1], 0.85))
            lean_v = 0.12
            cam_subject = "doors"
        elif a == "pry_doors":
            full = bool(re.search(r"раздвинул|открыл|разжал|распахнул", _low(s.get("text"))))
            t_grip = a0 + 0.45
            open_t = t_grip + 0.5
            holds.append({"side": "both", "t0": a0, "t1": t1 + 0.4, "grip": t_grip, "mode": "doors", "origin": origin,
                          "loc": loc})
            base_gap = dtrack.keys[-1][1]
            dtrack.key(open_t, base_gap)
            if full:
                dtrack.key(open_t + 1.1, 0.24)
            else:
                dtrack.key(open_t + 0.4, base_gap + 0.025)
                dtrack.key(open_t + 0.8, base_gap + 0.012)
                dtrack.key(min(t1, open_t + 1.5), base_gap + 0.03)
                dtrack.key(min(t1 + 0.3, open_t + 2.0), base_gap)
            contacts.append({"kind": "doors", "t": open_t, "full": full})
            gkey(a0 - 0.1, W((0.0, DOOR_FACE_Y, 0.75)))
            # тужится — отворачивает лицо вбок (к камере детали, она с +X): лицо видно, а не капюшон
            gkey(t_grip + 0.35, W((0.70, FRONT + 0.12, 0.85)))
            if full:
                gkey(open_t + 1.3, W((0.0, FRONT - 0.4, 0.45)))     # в шахту: внизу край этажа
            lean_v = -0.16
            if phone_out:
                phone_iv[-1][1] = a0 + 0.3        # обе руки на дверях — телефон в карман
                if phone_hold is not None:
                    phone_hold["t1"] = a0 + 0.3
                phone_out = False
            cam_subject = "doors"
        elif a == "sit_corner":
            if posture != "sit":
                add_clip("Sit_Floor_Down", a0, blend=0.25, loop=False)
                add_clip("Sit_Floor_Idle", a0 + clip_len["Sit_Floor_Down"] - 0.1, blend=0.2)
            posture = "sit"
            gkey(a0 + 0.2, (0.0, FRONT, 0.55))
            cam_subject = "face"
        elif a == "stand_up":
            gkey(a0, loc_target(loc, "key"))
        elif a in ("phone_out", "phone_light"):
            if not phone_out:
                phone_iv.append([a0, None])
                phone_hold = {"side": "l", "t0": a0, "t1": None, "mode": "phone", "from": "pocket"}
                holds.append(phone_hold)
                phone_out = True
            aim = a == "phone_light"
            phone_mode.key(a0 + 0.55, phone_mode.keys[-1][1])
            phone_mode.key(a0 + 0.8, 1.0 if aim else 0.0)
            if aim or dark(a0 + 0.5):
                flash_track.key(a0 + 0.85, flash_track.keys[-1][1])
                flash_track.key(a0 + 0.9, 1.0)
            gkey(a0 + 0.1, "phone")
            if aim:
                # светит туда, куда смотрит: на двери, потом ведёт лучом по кабине
                if loc == "cabin":
                    tgt = (0.0, DOOR_FACE_Y, 0.9) if rnd.random() < 0.5 else (DISPLAY[0], DISPLAY[1], DISPLAY[2] - 0.1)
                else:
                    tgt = loc_target(loc, "key")
                gkey(a0 + 0.9, tgt)
                gkey(a0 + 0.9 + max(0.6, (t1 - a0) * 0.45), (tgt[0] + 0.35, tgt[1] + 0.25, tgt[2] + 0.25))
            cam_subject = "phone"
        elif a == "look_display":
            gkey(a0, loc_target(loc, "key"))
            cam_subject = "display"
        elif a == "look_ceiling":
            if loc == "cabin":
                gkey(a0, HATCH)
                gkey(a0 + max(0.6, span * 0.55), (CEIL_LIGHT[0] - 0.15, CEIL_LIGHT[1] - 0.2, H))
            else:
                gkey(a0, loc_target(loc, "ceiling"))
            cam_subject = "face"
        elif a == "look_camera":
            gkey(a0, "camera")
        elif a == "flinch":
            ev = [e["t"] for e in events if t0 - 0.2 <= e["t"] < t1 and e["kind"] in ("blackout", "jolt", "knock", "scrape", "flash")]
            tf = ev[0] if ev else a0 + 0.15
            startles.append((tf + 0.05, 1.0))
            add_clip("Hit_A", max(tt, tf), blend=0.12, loop=False) if posture == "stand" else None
            add_clip("Idle_A", max(tt, tf) + clip_len["Hit_A"] - 0.05, blend=0.3) if posture == "stand" else None
            gkey(tf + 0.1, (CEIL_LIGHT[0], CEIL_LIGHT[1], H) if loc == "cabin" else loc_target(loc, "ceiling"))
        elif a == "shout":
            gkey(a0, (0.0, DOOR_FACE_Y, 1.05) if loc == "cabin" else loc_target(loc, "sound"))
            lean_v = 0.18
            # кричит без своей реплики в сценарии — рот закрыт (только испуг/напряжение): рот — лишь под речь героя
        elif a == "listen":
            gkey(a0, (0.05, DOOR_FACE_Y, 0.8) if loc == "cabin" else loc_target(loc, "sound"))
            roll_v = 0.22
            if seg_clip == "Idle_A" and posture == "stand":
                add_clip("Idle_B", a0, blend=0.4, speed=0.6)
        elif a == "wait":
            if posture == "stand" and seg_clip != "Idle_B":
                add_clip("Idle_B", a0, blend=0.4, speed=0.7)
            if loc == "cabin":
                gkey(a0, (0.1, FRONT + 0.3, 0.0) if rnd.random() < 0.5 else DISPLAY)
                gkey(a0 + span * 0.5, DISPLAY if dark(a0) else (0.3, FRONT, 0.6))
            else:
                gkey(a0, loc_target(loc, "key"))
                gkey(a0 + span * 0.5, loc_target(loc, "sound"))
            lean_v = -0.05
        for e in events:
            if e["kind"] == "doors_close" and t0 <= e["t"] < t1:
                dtrack.key(e["t"], dtrack.keys[-1][1])
                dtrack.key(e["t"] + 0.22, 0.0)
                startles.append((e["t"] + 0.1, 0.8))
                gkey(e["t"] + 0.12, W((0.0, DOOR_FACE_Y, 0.8)))
        if phone_out and a not in ("phone_out", "phone_light"):
            # телефон уже в руке — светит туда, куда смотрит (экраном к себе — только когда смотрит в телефон)
            phone_mode.key(a0 + 0.1, phone_mode.keys[-1][1])
            phone_mode.key(a0 + 0.5, 1.0)

        show_track.key(t0, show_track.keys[-1][1])
        show_track.key(a0 + 0.5 if s.get("screen") else t0 + 0.35, 1.0 if s.get("screen") else 0.0)
        lean.key(t0 + 0.4, lean_v)
        roll.key(t0 + 0.4, roll_v)
        shot_out.append({**s, "a0": round(a0, 3), "spot": [round(pos[0], 4), round(pos[1], 4)], "heading": round(heading, 4),
                         "posture_end": posture, "subject": cam_subject, "phone": phone_out, "location": loc,
                         "origin": list(origin), "key_target": list(loc_target(loc, "key")),
                         "show": show_items(loc, s)})

    # события → испуг, взгляд на источник
    for e in events:
        if e["kind"] in ("blackout", "jolt", "knock", "scrape", "flash"):
            amp = {"blackout": 1.0, "jolt": 0.9, "knock": 0.55, "scrape": 0.5, "flash": 0.7}[e["kind"]]
            if not any(abs(t - e["t"]) < 0.5 for t, _ in startles):
                startles.append((e["t"] + 0.08, amp))
    startles.sort()

    # покадрово: корень
    tl = track
    head_c = head_keys.curve()
    heads = [round(head_c.at(t), 5) for t in times]
    root = walk_root(tl, sorted(pos_keys, key=lambda k: k[0]), walks, heads, times)

    # руки: правая (нажатия/стук), левая и обе (телефон, двери)
    hand = {"r": [None] * nf, "l": [None] * nf}
    for r in reaches:
        rc = m.Reach(button=tuple(r["button"]), normal=tuple(r["normal"]), t_start=r["t_start"], t_contact=r["t_contact"],
                     t_release=r["t_release"], t_end=r["t_end"], hover=r["hover"], depth=r["depth"],
                     rest=tuple(r["rest"]) if r.get("rest") else None)
        hits = r.get("hits")
        for f, t in enumerate(times):
            w = rc.influence(t)
            if w <= 0:
                continue
            p = rc.target_at(t)
            if hits:
                # стук: кулак отводится и бьёт на каждом ударе
                near = min(hits, key=lambda h: abs(h - t))
                x = (t - near) / 0.19
                back = 0.07 * (1.0 - math.exp(-(x * x) * 3.0)) if r["t_contact"] - 0.05 <= t <= r["t_release"] else 0.0
                p = m.v_add(p, m.v_scale(r["normal"], back))
            prev = hand["r"][f]
            if prev is None or prev[1] < w:
                hand["r"][f] = (tuple(round(c, 5) for c in p), round(w, 4), r["id"])
    pm_c = phone_mode.curve()
    show_c = show_track.curve()
    door_cs = {k: v.curve() for k, v in door_tracks.items()}
    door_c0 = door_cs.get("cabin") or _Track(0.0).curve()
    for h in holds:
        for f, t in enumerate(times):
            if t < h["t0"] or (h["t1"] is not None and t > h["t1"]):
                continue
            rp, hd = root[f], heads[f]
            sit = 1.0 if _posture_at(shot_out, t) == "sit" else 0.0
            if h["mode"] == "phone":
                x = m.smootherstep((t - h["t0"]) / 0.65)
                look = (-0.035, 0.20, 0.50 - SIT_DROP * sit)
                light = (-0.07, 0.25, 0.57 - SIT_DROP * sit)
                pocket = (-0.13, 0.04, 0.32 - SIT_DROP * sit)
                mode = pm_c.at(t)
                held = m.lerp(look, light, mode)
                show = (PHONE_SHOW[0], PHONE_SHOW[1], PHONE_SHOW[2] - SIT_DROP * sit)
                held = m.lerp(held, show, show_c.at(t))
                loc = m.lerp(pocket, held, x)
                w = m.smoothstep((t - h["t0"]) / 0.25)
                hand["l"][f] = (to_world(rp, hd, loc), round(w, 4), "phone")
            elif h["mode"] == "doors":
                op = (door_cs.get(h.get("loc") or "cabin") or door_c0).at(t)
                ox, oy = h.get("origin") or (0.0, 0.0)
                w = m.smoothstep((t - h["t0"]) / max(h["grip"] - h["t0"], 1e-3))
                if h["t1"] is not None:
                    w *= 1.0 - m.smoothstep((t - (h["t1"] - 0.4)) / 0.4)
                for side, sx in (("r", -1.0), ("l", 1.0)):
                    # створки разъезжаются — кисти на кромках (x: −1 — вправо от героя к −X мира)
                    p = (ox + sx * (0.045 + op), oy + DOOR_FACE_Y + 0.025, 0.62)
                    if w > 0 and (hand[side][f] is None or hand[side][f][1] < w):
                        hand[side][f] = (p, round(w, 4), "doors")
    # телефон виден в своих интервалах (в кармане — нет), левая рука держит его только тогда
    vis = [1.0 if any(a <= t < (b if b is not None else 1e9) for a, b in phone_iv) else 0.0 for t in times]
    for f in range(nf):
        if not vis[f] and hand["l"][f] and hand["l"][f][2] == "phone":
            hand["l"][f] = None

    # взгляд и мимика
    gaze_c = gaze.curve(ease="step")
    gz = []
    for t in times:
        g = gaze_c.at(t)
        here = next((x_["location"] for x_ in shot_out if x_["t0"] <= t < x_["t1"]), shot_out[-1]["location"])
        for e in events:
            if e["kind"] in ("blackout", "scrape") and e["t"] + 0.15 <= t < e["t"] + 1.1:
                g = (CEIL_LIGHT[0], CEIL_LIGHT[1], H) if here == "cabin" else loc_target(here, "ceiling")
            elif e["kind"] == "knock" and e["t"] + 0.1 <= t < e["t"] + 1.2:
                g = (0.0, DOOR_FACE_Y, 0.85) if here == "cabin" else loc_target(here, "sound")
        gz.append(g if isinstance(g, str) else tuple(round(c, 4) for c in g))
    emo_c, lean_c, roll_c = emo.curve(), lean.curve(), roll.curve()
    blinks = m.blink_times(dur, seed=int(job.get("seed") or 7) + 4, min_gap=1.9, max_gap=4.0,
                           forced=[t for t, _ in startles])
    speak_windows = [tuple(w) for w in ((job.get("speech") or {}).get("hero_windows") or [])]
    lip_mode = job.get("lipsync") or "speaking"
    windows = None if lip_mode == "all" else ([] if lip_mode == "off" else speak_windows)
    mouth = lipsync(job.get("words") or [], nf, fps, job.get("envelope"), windows)
    face, body = [], []
    for f, t in enumerate(times):
        b, wy, wd = emo_c.at(t)
        st = sum(amp * _pulse(t - ts) for ts, amp in startles if t >= ts - 0.01)
        st = min(1.0, st)
        b = max(b, st)
        wd = max(wd, st)
        o, r, w = mouth[f]
        # испуг — рот «о», крик — широко
        o = max(o, 0.7 * st)
        r = max(r, 0.8 * st if st > 0.3 else r)
        for a_, b_ in shout_bursts:
            if a_ <= t <= b_:
                x = (t - a_) / (b_ - a_)
                o = max(o, 0.95 * math.sin(math.pi * x))
                r = min(r, 0.3)
        face.append({"brow": round(b, 4), "worry": round(wy, 4), "wide": round(wd, 4),
                     "blink": round(m.blink_amount(t, blinks), 4), "mouth": (round(o, 4), round(r, 4), round(w, 4))})
        body.append({"startle": round(st, 4), "lean": round(lean_c.at(t), 4), "roll": round(roll_c.at(t), 4)})

    # свет, табло, двери, тряска
    lights = []
    floors = [e for e in events if e["kind"] == "floor"]
    start_floor = floors[0]["value"] if floors else "22"
    jolts = [e["t"] for e in events if e["kind"] in ("jolt", "scrape")]
    flick = [e["t"] for e in events if e["kind"] == "flicker"]
    flashes = [e["t"] for e in events if e["kind"] == "flash"]
    lit_at: dict[str, float] = {}
    for c in contacts:
        if c["kind"] == "press" and c["button"] != "bell":
            lit_at.setdefault(c["button"], c["t"])
    door_c = door_c0
    named_display = [(float(x_["t0"]), float(x_["t1"])) for x_ in shot_out if x_["location"] == "cabin"
                     and any(it["item"] == "display" or str(it["item"]).startswith("надпись") for it in x_["show"])]
    for t in times:
        main = 1.0
        if blackout is not None:
            if t >= blackout - 0.9 and (light_back is None or t < light_back):
                main = m.flicker(t, blackout - 0.9, blackout, seed=5)
            if light_back is not None and t >= light_back:
                main = m.smoothstep((t - light_back) / 0.15)
        for tf in flick:
            if tf <= t < tf + 0.9:
                main *= m.flicker(t, tf, tf + 10.0, seed=int(tf * 10)) * 0.8 + 0.2
        for tj in jolts:
            if tj <= t < tj + 0.35:
                main *= 0.35 + 0.65 * ((t - tj) / 0.35)
        red = 0.0
        if blackout is not None and t >= blackout + 0.25 and (light_back is None or t < light_back):
            x = t - blackout - 0.25
            red = m.smoothstep(x / 0.12) * (0.55 + 0.45 * m.smoothstep(x / 0.6)) * (0.9 + 0.1 * math.sin(2 * math.pi * x / 1.6))
            for tj in jolts:
                if tj <= t < tj + 0.3:
                    red *= 0.4 + 0.6 * ((t - tj) / 0.3)
        flash = max([math.exp(-(t - tf) / 0.12) for tf in flashes if t >= tf] or [0.0])
        disp_on = 1.0
        value = start_floor
        for e in floors[1:]:
            if t >= e["t"]:
                value = e["value"]
        if blackout is not None and t >= blackout + 0.04 and (light_back is None or t < light_back):
            disp_on = 0.0
            for e in floors[1:]:
                on_t = max(blackout + 1.0, e["t"] - 1.8)
                if e["t"] > blackout and t >= on_t:
                    disp_on = m.smoothstep((t - on_t) / 0.25)
            for a_, b_ in named_display:
                if a_ <= t < b_:     # фраза называет табло — оно горит (аварийное питание), иначе в темноте не видно
                    disp_on = max(disp_on, 0.85 * m.smoothstep((t - a_ + 0.15) / 0.25))
        shake = 0.0
        for e in events:
            amp = {"blackout": 0.006, "jolt": 0.03, "scrape": 0.01, "knock": 0.004}.get(e["kind"], 0.0)
            ln = {"blackout": 0.6, "jolt": 0.9, "scrape": 1.2, "knock": 0.4}.get(e["kind"], 0.5)
            if amp and e["t"] <= t < e["t"] + ln:
                shake = max(shake, amp * (1.0 - (t - e["t"]) / ln))
        drop = 0.0
        for tj in [e["t"] for e in events if e["kind"] == "jolt"]:
            if t >= tj:
                drop += -0.05 * m.smoothstep((t - tj) / 0.12) * math.exp(-max(0.0, t - tj - 0.12) / 0.35)
        lit = sorted(b for b, tb in lit_at.items() if t >= tb)
        lights.append({"main": round(main, 4), "red": round(red, 4), "flash": round(flash, 4), "display": round(disp_on, 4),
                       "floor": value, "doors": round(door_c.at(t), 4), "shake": round(shake, 5), "drop": round(drop, 5),
                       "ldoors": {k: round(c_.at(t), 4) for k, c_ in door_cs.items() if k != "cabin"},
                       "lit": lit, "dark": dark(t)})

    phone = []
    pm = phone_mode.curve()
    fl = flash_track.curve(ease="linear")
    beam_raw = []
    for f, t in enumerate(times):
        # луч — туда, куда смотрит герой (в объектив/на телефон — вперёд в пол: не слепить камеру)
        g = gz[f]
        if isinstance(g, tuple):
            beam_raw.append(g)
        else:
            fx, fy = facing(heads[f])
            beam_raw.append((root[f][0] + fx * 0.9, root[f][1] + fy * 0.9, 0.05))
    perf = {"fps": fps, "frames": nf, "duration": dur, "track": [
        {"clip": sg.clip, "start": round(sg.start, 4), "duration": sg.duration, "blend": sg.blend, "speed": round(sg.speed, 4),
         "loop": sg.loop, "offset": sg.offset, "ease_in": sg.ease_in, "ease_out": sg.ease_out,
         "end": None if sg.end is None else round(sg.end, 4)} for sg in track.segments],
        "root": root, "heading": heads, "hands": hand, "gaze": gz, "face": face, "body": body, "lights": lights,
        "phone": phone, "contacts": contacts, "shots": shot_out, "startles": startles,
        "blackout": blackout, "light_back": light_back, "speak_windows": speak_windows}
    perf["cameras"] = camera_plan(perf, int(job.get("seed") or 7))
    # луч фонарика не в объектив: камера в конусе — увести луч вбок-вниз (иначе кадр — белая вспышка)
    beam_raw = _beam_off_camera(perf, beam_raw)
    beam_s = m.spring_follow(beam_raw, 1.0 / fps, 0.18)
    for f, t in enumerate(times):
        # экран со значением (часы, заряд) — в плане с ``screen``, когда телефон уже у лица (через 0.5 с)
        sh_ = next((x_ for x_ in shot_out if x_["t0"] <= t < x_["t1"]), None)
        scr = str(sh_.get("screen") or "") if sh_ and vis[f] and t >= float(sh_.get("a0", sh_["t0"])) + 0.5 else ""
        phone.append({"on": vis[f], "aim": round(pm.at(t) if vis[f] else 0.0, 4),
                      "light": round(fl.at(t) if vis[f] else 0.0, 4), "beam": tuple(round(c, 4) for c in beam_s[f]),
                      **({"screen": scr} if scr else {})})
    perf["phone"] = phone
    return perf


def _beam_off_camera(perf: dict, beam: list[tuple]) -> list[tuple]:
    fps = perf["fps"]
    starts = [(int(round(c["t0"] * fps)), c) for c in perf["cameras"]]
    out = []
    for f, b in enumerate(beam):
        cam = next((c for f0, c in reversed(starts) if f >= f0), starts[0][1])
        posture = _posture_at(perf["shots"], f / fps)
        ph = to_world(perf["root"][f], perf["heading"][f], (-0.07, 0.25, 0.57 - (SIT_DROP if posture == "sit" else 0.0)))
        to_b = m.v_norm(m.v_sub(b, ph))
        to_c = m.v_norm(m.v_sub(cam["pos_a"], ph))
        if m.v_dot(to_b, to_c) > math.cos(math.radians(40)):
            # повернуть луч от камеры на ~70° по горизонтали и опустить к полу
            side = 1.0 if (to_b[0] * to_c[1] - to_b[1] * to_c[0]) >= 0 else -1.0
            d = _rot((to_c[0], to_c[1]), math.radians(70 * side))
            b = (ph[0] + d[0] * 0.9, ph[1] + d[1] * 0.9, 0.1)
        out.append(b)
    return out


# --------------------------------------------------------------------------- #
# Камера: план кадра → положение и цель (сцена Blender доводит цель по настоящей голове героя)
# --------------------------------------------------------------------------- #

CAM_BOUNDS = (LEFT + 0.06, -LEFT - 0.06, FRONT + 0.06, D / 2 - 0.06, 0.12, H - 0.1)


_CAM = {"bounds": CAM_BOUNDS}     # границы камеры текущего места (camera_plan ставит на каждый план)


def cam_bounds() -> tuple:
    return _CAM["bounds"]


def cam_inside(p: tuple[float, float, float]) -> bool:
    x0, x1, y0, y1, z0, z1 = cam_bounds()
    return x0 <= p[0] <= x1 and y0 <= p[1] <= y1 and z0 <= p[2] <= z1


def _rot(v: tuple[float, float], ang: float) -> tuple[float, float]:
    c, s_ = math.cos(ang), math.sin(ang)
    return (v[0] * c - v[1] * s_, v[0] * s_ + v[1] * c)


def face_point(root: tuple[float, float], heading: float, posture: str) -> tuple[float, float, float]:
    """Середина лица (оценка без Blender): над корнем, чуть впереди, по позе."""
    fx, fy = facing(heading)
    fwd = 0.07 if posture == "stand" else 0.02
    return (root[0] + fx * fwd, root[1] + fy * fwd, FACE_Z["sit" if posture == "sit" else "stand"])


def shot_camera(kind: str, shot: dict, root: tuple[float, float], heading: float, posture: str,
                aim_point: tuple[float, float, float] | None, rnd: random.Random,
                avoid: list[tuple] | None = None) -> dict | None:
    """Камера кадра: ``{kind, pos_a, pos_b, point, face_w, offset, lens, fstop, focus}``. ``point`` — во что целиться
    помимо лица, ``face_w`` — доля лица в цели (сцена ведёт её за настоящей головой). Ракурс не встаёт (камера
    в стене) — ``None``; ``avoid`` — положения прошлых кадров того же плана (повтор того же ракурса не берём)."""
    avoid = avoid or []

    def fresh(p) -> bool:
        return cam_inside(p) and all(math.dist(p, q) > 0.22 for q in avoid)
    fx, fy = facing(heading)
    fwd = (fx, fy)
    face = face_point(root, heading, posture)
    subject = shot.get("subject") or "face"
    side = 1.0 if rnd.random() < 0.5 else -1.0

    def out(kind_, pos, point, face_w, lens, fstop=2.4, focus="face", push=0.93, offset=(0.0, 0.0, 0.0)):
        pos = tuple(round(c, 4) for c in pos)
        if focus == "face":
            pivot = face
        else:
            pivot = point
        pos_b = tuple(round(pivot[i] + (pos[i] - pivot[i]) * push, 4) for i in range(3))
        return {"kind": kind_, "pos_a": pos, "pos_b": pos_b, "point": tuple(round(c, 4) for c in point),
                "face_w": face_w, "lens": lens, "fstop": fstop, "focus": focus, "offset": offset}

    def around(dist: float, angles: tuple[float, ...], dz: float, center=face) -> tuple | None:
        for a in angles:
            d = _rot(fwd, math.radians(a * side))
            for k in (1.0, 0.85, 0.72):
                p = (center[0] + d[0] * dist * k, center[1] + d[1] * dist * k, center[2] + dz)
                if fresh(p):
                    return p
        return None

    if kind == "detail" and subject == "press":
        pos = (LEFT + 0.17, FRONT + 0.12, 0.84)
        pt = tuple((button_pos("1")[i] * 0.55 + face[i] * 0.45) for i in range(3))
        return out("detail", pos, pt, 0.35, 19.0, fstop=3.2, focus="point", push=0.95)
    if kind == "detail" and subject == "phone" and shot.get("screen"):
        # экран со значением (часы, заряд): телефон в руке экраном к камере и лицо над ним — близко, длинный фокус
        phone_c = to_world(root, heading, (PHONE_SHOW[0], PHONE_SHOW[1], PHONE_SHOW[2] + 0.07 - (SIT_DROP if posture == "sit" else 0.0)))
        center = tuple(phone_c[i] * 0.55 + face[i] * 0.45 for i in range(3))
        p = around(0.62, (-20, 20, -32, 32, 0, -45, 45), 0.0, center)
        if p:
            # кадр ≈ 0.62 м по высоте (телефон и лицо целиком) при любом отступе камеры от стен
            lens = max(20.0, min(40.0, 58.0 * math.dist(p, center)))
            return out("detail", p, center, 0.0, round(lens, 1), fstop=4.0, focus="point", push=0.95)
    if kind == "detail" and subject == "phone":
        hand_l = to_world(root, heading, (-0.04, 0.22, 0.52 - (SIT_DROP if posture == "sit" else 0.0)))
        center = tuple((hand_l[i] + face[i]) / 2 for i in range(3))
        p = around(0.72, (-38, 38, -55, 55, 0), -0.08, center)
        if p:
            return out("detail", p, hand_l, 0.5, 26.0, fstop=2.4, focus="point")
    ox, oy = shot.get("origin") or (0.0, 0.0)
    key = tuple(shot.get("key_target") or DISPLAY)
    if kind == "detail" and subject == "display":
        # главная вещь места (табло, надпись, номер этажа) из-за плеча героя; не встаёт — сама вещь крупно
        p = (face[0] - fx * 0.70 + fy * 0.22 * side, face[1] - fy * 0.70 - fx * 0.22 * side, face[2] + 0.08)
        if cam_inside(p):
            return out("detail", p, key, 0.0, 62.0 if key == DISPLAY else 40.0, fstop=4.0, focus="point", push=0.95)
        if key == DISPLAY:
            return out("detail", (0.30, FRONT + 0.45, 1.20), DISPLAY, 0.0, 30.0, fstop=4.0, focus="point")
        return None
    if kind == "detail" and subject == "doors":
        # сбоку у дверей, почти анфас: руки на створках и лицо (герой стоит к дверям вплотную)
        # сбоку сверху у дверей: руки на створках, щель и лицо в профиль (вплотную к дверям анфаса нет)
        sx = 1.0   # с +X: туда герой отворачивает лицо, когда тужится; у стука правая рука — к −X
        pos = (ox + sx * 0.58, oy + FRONT + 0.13, 0.98)
        pt = (ox, oy + DOOR_FACE_Y + 0.10, 0.62)
        if not cam_inside(pos):
            return None
        return out("detail", pos, pt, 0.75, 17.0, fstop=3.2, focus="face", push=0.96)
    if kind == "detail":
        return None
    if kind == "ots":
        # за плечом: сзади-сбоку на уровне головы — в кадре плечо и профиль героя И то, на что он смотрит; герой
        # не в кадре (пустой коридор/стена/капюшон на полкадра) — ракурс не годится, план выберут другой
        tgt = aim_point or (root[0] + fx * 1.2, root[1] + fy * 1.2, 0.85)
        for sh, back, dz in ((0.36 * side, 1.00, 0.22), (-0.36 * side, 1.00, 0.22), (0.30 * side, 0.86, 0.16),
                             (-0.30 * side, 0.86, 0.16), (0.24 * side, 1.15, 0.30), (-0.24 * side, 1.15, 0.30),
                             (0.42 * side, 0.80, 0.12), (-0.42 * side, 0.80, 0.12)):
            # вправо от взгляда: (fy, −fx); sh>0 — над правым плечом
            p = (face[0] - fx * back + fy * sh, face[1] - fy * back - fx * sh, face[2] + dz)
            if not fresh(p):
                continue
            c_ = out("ots", p, tgt, 0.30, 20.0, fstop=5.0, focus="face", push=0.94)
            if ots_ok(c_, face, tgt):
                return c_
        return None
    if kind == "overhead":
        x0, x1, y0, y1, _z0, z1 = cam_bounds()
        best, best_s = None, -1e9
        for cx in (x0 + 0.02, x1 - 0.02):
            for cy in (y0 + 0.02, y1 - 0.02):
                p = (cx, cy, min(1.86, face[2] + 0.95))
                if not fresh(p):
                    continue
                dx, dy = p[0] - face[0], p[1] - face[1]
                dist = math.hypot(dx, dy)
                score = (dx * fx + dy * fy) / max(dist, 1e-6) + dist * 0.6
                if score > best_s:
                    best, best_s = p, score
        if best is None:
            return None
        # из угла, но не дальше ~1.2 м по полу: в темноте мелкий герой не читается
        dx, dy = best[0] - face[0], best[1] - face[1]
        k = min(1.0, 0.95 / max(math.hypot(dx, dy), 1e-6))
        best = (face[0] + dx * k, face[1] + dy * k, best[2])
        pt = (face[0], face[1], face[2] - 0.22)
        return out("overhead", best, pt, 0.5, 17.0, fstop=4.0, focus="face", push=0.94)
    # крупно лицо: спереди под небольшим углом, чуть ниже глаз
    if shot.get("n") == 0:
        # первый план под титром названия (верх кадра): лицо мельче и ниже, под титром, над субтитрами
        p = around(1.25, (22, -22, 40, -40, 0), 0.10)
        if p is not None:
            return out("face", p, face, 1.0, 28.0, fstop=2.4, focus="face", push=0.93, offset=(0.0, 0.0, 0.0))
    p = around(0.95, (22, -22, 40, -40, 0, 60, -60), -0.03)
    if p is None:
        return None
    return out("face", p, face, 1.0, 30.0, fstop=2.2, focus="face", push=0.9, offset=(0.0, 0.0, -0.06))


def cam_sees(cam: dict, face: tuple, point: tuple, margin: float = 0.9) -> bool:
    """Попадает ли точка в вертикальный кадр 9:16 камеры плана (без Blender: как сцена целит — между вещью и лицом)."""
    pos = cam["pos_a"]
    face_aim = tuple(face[i] + cam["offset"][i] for i in range(3))
    look = tuple(cam["point"][i] + (face_aim[i] - cam["point"][i]) * cam["face_w"] for i in range(3))
    fwd = [look[i] - pos[i] for i in range(3)]
    n_ = math.sqrt(sum(c * c for c in fwd)) or 1.0
    fwd = [c / n_ for c in fwd]
    right = [fwd[1], -fwd[0], 0.0]
    rn = math.hypot(right[0], right[1]) or 1.0
    right = [right[0] / rn, right[1] / rn, 0.0]
    up = [right[1] * fwd[2] - right[2] * fwd[1], right[2] * fwd[0] - right[0] * fwd[2],
          right[0] * fwd[1] - right[1] * fwd[0]]
    v = [point[i] - pos[i] for i in range(3)]
    z = sum(v[i] * fwd[i] for i in range(3))
    if z <= 0.05:
        return False
    tan_v = 18.0 / cam["lens"]                 # датчик 36 по длинной (вертикальной) стороне
    tan_h = tan_v * 1080 / 1920
    x = sum(v[i] * right[i] for i in range(3)) / z
    y = sum(v[i] * up[i] for i in range(3)) / z
    return abs(x) <= tan_h * margin and abs(y) <= tan_v * margin


def ots_ok(cam: dict, face: tuple, target: tuple) -> bool:
    """«Через плечо» годится, только если в кадре и голова героя (плечо+профиль), и то, на что он смотрит, а голова
    не во весь кадр (камера не ближе 0.45 м)."""
    if math.dist(cam["pos_a"], face) < 0.7:
        return False
    return cam_sees(cam, face, face, 0.72) and cam_sees(cam, face, tuple(target), 0.9)


def camera_plan(perf: dict, seed: int = 7) -> list[dict]:
    """Камеры всех кадров (без повтора одинакового ракурса подряд — другой бок)."""
    rnd = random.Random(seed + 11)
    fps = perf["fps"]
    out = []
    for s in perf["shots"]:
        lo = loc_of(s.get("location"))
        b_ = lo["bounds"]
        ox_, oy_ = lo["origin"]
        _CAM["bounds"] = (b_[0] + ox_, b_[1] + ox_, b_[2] + oy_, b_[3] + oy_, b_[4], b_[5])
        tm = min(s["t1"] - 0.05, max(s.get("a0", s["t0"]) + 0.3, (s["t0"] + s["t1"]) / 2))
        f = min(perf["frames"] - 1, int(tm * fps))
        posture = s.get("posture_end", "stand")
        g = perf["gaze"][f]
        aim = g if isinstance(g, (list, tuple)) else None
        prev = out[-1]["kind"] if out else ""
        root, head = tuple(perf["root"][f]), perf["heading"][f]
        face = face_point(root, head, posture)
        # что фраза называет (лампа, надпись, двери…) — план, где это в кадре; за плечом смотрим на эту вещь
        items = [tuple(x["point"]) for x in s.get("show") or [] if x.get("point")]
        cam, best = None, -1
        rnd_n = random.Random(seed * 1000 + s["n"])   # свой случай у плана: пробы ракурсов не сдвигают другие планы
        kinds = list(dict.fromkeys((s["camera"], *CAMERA_PREFS.get(s["action"], ()), "face", "overhead", "ots", "detail")))
        # тот же план подряд — только последней попыткой и только если иначе названное не видно (с другого места)
        order = [(k, False) for k in kinds if k != prev] + ([(prev, True)] if items and prev else [])
        if s.get("screen") or s.get("hook") == 1:
            # экран телефона — деталью, крючок — лицом: этот план первым, даже если прошлый такой же
            must = "detail" if s.get("screen") else "face"
            order = [(must, False)] + [o for o in order if o[0] != must]
        for kind, again in order:
            if again and cam is not None and best == len(items):
                break
            avoid = [c["pos_a"] for c in out if c["kind"] == kind][-2:]
            if again:
                avoid = avoid + [out[-1]["pos_a"]]
            for aim_ in ((items[0], aim) if items and kind == "ots" else (aim,)):
                c_ = shot_camera(kind, s, root, head, posture, aim_, random.Random(rnd_n.random()), avoid)
                if c_ is None and not again:
                    c_ = shot_camera(kind, s, root, head, posture, aim_, random.Random(rnd_n.random()))
                if c_ is None:
                    continue
                seen = sum(cam_sees(c_, face, p) for p in items)
                if kind == "ots" and seen < len(items):
                    continue      # за плечом без названной вещи — пустой план
                if seen > best:
                    cam, best = c_, seen
                if seen == len(items):
                    break
            if cam is not None and best == len(items):
                break
        if cam is None:   # совсем тесно — общий сверху без проверки повтора
            cam = shot_camera("overhead", s, root, head, posture, aim, rnd)
        s["camera"] = cam["kind"]
        cam["face_y"] = face_target(s["n"], len(perf["shots"]), cam["kind"])
        out.append({"n": s["n"], "t0": s["t0"], "t1": s["t1"], "location": s.get("location") or "cabin", **cam})
    _CAM["bounds"] = CAM_BOUNDS
    return out


# Где лицо в кадре (доля высоты сверху): субтитры — 0.74, HUD — 0.145/0.585; первый план — под титром названия
# (TITLE_Y), последний — над титром «ЧАСТЬ N+1» (PART_Y): титры и плашки не ложатся на лицо.
TITLE_Y, PART_Y = 0.27, 0.62


def face_target(n: int, count: int, kind: str) -> float:
    if n == 0:
        return 0.52
    if n == count - 1:
        return 0.30
    return 0.40 if kind in ("face", "overhead") else 0.42


def face_bands(perf: dict) -> list[dict]:
    """Лицо в кадре по планам: ``{n, t0, t1, y, h}`` (центр и высота, доли кадра; ``y`` None — лица почти нет: за
    плечом, табло). Высота — лицо с капюшоном ≈ 0.42 м на расстоянии камеры и фокусном (вертикальный кадр, датчик 36)."""
    out = []
    shots = {s["n"]: s for s in perf["shots"]}
    for c in perf["cameras"]:
        s = shots[c["n"]]
        fi = min(perf["frames"] - 1, int((c["t0"] + c["t1"]) / 2 * perf["fps"]))
        face = face_point(tuple(perf["root"][fi]), perf["heading"][fi], s.get("posture_end", "stand"))
        dist = max(0.2, math.dist(c["pos_a"], face))
        h = max(0.05, min(0.75, 0.42 * c["lens"] / (36.0 * dist)))   # лоб–подбородок с капюшоном ≈ 0.42 м
        y = c.get("face_y") if c.get("face_w", 0) > 0.3 else None
        out.append({"n": c["n"], "t0": c["t0"], "t1": c["t1"], "y": y, "h": round(h, 3)})
    return out


def planted_local(lt: float) -> tuple[str, float, float]:
    """Опорная стопа клипа шага в момент ``lt``: (сторона, x, y) в системе клипа (x — влево, y — назад), ед."""
    n = len(WALK_FEET) - 1
    phi = (lt * 30.0) % n
    i = int(phi)
    a_, b_ = WALK_FEET[i], WALK_FEET[min(i + 1, n)]
    k = phi - i
    ly, lz, ry, rz = (a_[j] + (b_[j] - a_[j]) * k for j in range(4))
    return ("l", WALK_FOOT_X, ly) if lz <= rz else ("r", -WALK_FOOT_X, ry)


def clip_to_world(root: tuple[float, float], heading: float, x: float, y: float) -> tuple[float, float]:
    """Точка клипа (x — влево от взгляда, y — назад; ед. без масштаба) → мир."""
    fx, fy = facing(heading)
    rx, ry = fy, -fx
    return (root[0] + (-x * rx - y * fx) * S, root[1] + (-x * ry - y * fy) * S)


def walk_root(track: m.ClipTrack, pos_keys: list[tuple[float, tuple]], walks: list[dict], heads: list[float],
              times: list[float]) -> list[tuple[float, float]]:
    """Корень по кадрам. Склейки — сразу на месте. На шаге в полный вес опорная стопа клипа стоит на полу: корень
    ставится так, чтобы она не сдвинулась (и поворот корпуса идёт вокруг неё) — root motion из клипа. В наплывах
    шага — корень едет на сдвиг опорной стопы × вес. Шаг к цели доводится до точки поправкой по кадрам окна
    (1–3 шага: миллиметры за кадр)."""
    n = len(times)
    walk = []
    for t in times:
        lt, w = None, 0.0
        for clip, l_, w_ in track.weights_at(t):
            if clip == WALK_CLIP:
                lt, w = l_, w_
        walk.append((lt, w))

    def step(cur: tuple[float, float], f: int) -> tuple[float, float]:
        lt, w = walk[f]
        plt, pw = walk[f - 1] if f else (None, 0.0)
        if lt is None or plt is None or w <= 0:
            return cur
        if w >= 0.999 and pw >= 0.999:
            side0, x0, y0 = planted_local(plt)
            side1, x1, y1 = planted_local(lt)
            if side0 == side1:
                px, py = clip_to_world(cur, heads[f - 1], x0, y0)
                nx, ny = clip_to_world((0.0, 0.0), heads[f], x1, y1)
                return (px - nx, py - ny)
        d = stance_units(lt) - stance_units(plt)
        if d < -WALK_CYCLE_UNITS / 2:          # переход через цикл
            d += WALK_CYCLE_UNITS
        fx, fy = facing(heads[f])
        return (cur[0] + fx * d * S * w, cur[1] + fy * d * S * w)

    keys = list(pos_keys)
    cur = keys[0][1] if keys else (0.0, 0.0)
    out = []
    ki = 1
    win_i = 0
    walks = sorted(walks, key=lambda w_: w_["t0"])
    corr: dict[int, tuple[float, float]] = {}
    for f, t in enumerate(times):
        while ki < len(keys) and keys[ki][0] <= t + 1e-9:
            cur = keys[ki][1]
            ki += 1
        while win_i < len(walks) and walks[win_i]["t0"] <= t:
            w_ = walks[win_i]
            win_i += 1
            if w_["target"] is None:
                continue
            fr = [i for i in range(f, n) if times[i] <= w_["t1"]]
            sim, moves = cur, []
            for i in fr:
                nxt = step(sim, i)
                moves.append(math.dist(sim, nxt))
                sim = nxt
            ex, ey = w_["target"][0] - sim[0], w_["target"][1] - sim[1]
            tot = sum(moves) or 1.0
            corr = {i: (ex * mv / tot, ey * mv / tot) for i, mv in zip(fr, moves)}
        nxt = step(cur, f)
        if f in corr:
            nxt = (nxt[0] + corr[f][0], nxt[1] + corr[f][1])
        cur = nxt
        out.append((round(cur[0], 5), round(cur[1], 5)))
    return out


def foot_slide(perf: dict) -> list[float]:
    """Скольжение опорной стопы (мм за кадр) на кадрах полного веса шага: стопа клипа в мире = корень + поворот ×
    стопа × масштаб; опорная — ниже другой в обоих кадрах."""
    track = track_of(perf)
    fps = perf["fps"]
    out = []
    prev = None
    for f in range(perf["frames"]):
        t = f / fps
        ws = [(lt, w) for c, lt, w in track.weights_at(t) if c == WALK_CLIP]
        if not ws or ws[0][1] < 0.999:
            prev = None
            continue
        side, x, y = planted_local(ws[0][0])
        wx, wy = clip_to_world(tuple(perf["root"][f]), perf["heading"][f], x, y)
        if prev is not None and prev[0] == side:
            out.append(math.hypot(wx - prev[1], wy - prev[2]) * 1000)
        prev = (side, wx, wy)
    return out


def _posture_at(shots: list[dict], t: float) -> str:
    for s in shots:
        if s["t0"] <= t < s["t1"]:
            return s.get("posture_end", "stand") if s.get("action") != "sit_corner" or t >= s.get("a0", s["t0"]) + 0.6 else "stand"
    return shots[-1].get("posture_end", "stand") if shots else "stand"


def track_of(perf: dict) -> m.ClipTrack:
    tr = m.ClipTrack()
    for sg in perf["track"]:
        tr.add(m.ClipSegment(sg["clip"], sg["start"], sg["duration"], blend=sg["blend"], speed=sg["speed"],
                             loop=sg["loop"], offset=sg["offset"], ease_in=sg.get("ease_in", 0.0),
                             ease_out=sg.get("ease_out", 0.0), end=sg.get("end")))
    return tr


# --------------------------------------------------------------------------- #
# Звуки событий и действий (настоящие записи паков)
# --------------------------------------------------------------------------- #

SFX = {
    "press": ("sfx:kenney-interface/click_002.ogg", 0.55),
    "bell": ("sfx:kenney-interface/click_004.ogg", 0.5),
    "knock_hit": ("sfx:kenney-impact/impactMetal_medium_002.ogg", 0.55),
    "doors_strain": ("sfx:tension/metal-scrape.ogg", 0.55),
    "doors_open": ("sfx:tension/clank.ogg", 0.8),
}


def sfx_for(job: dict, perf: dict) -> list[dict]:
    """Звуки холста: события режиссуры (как ``series3d``) + щелчки нажатий, удары стука, скрежет створок,
    фон-петли гула кабины/шахты."""
    from app import series3d as S3

    total = float(job["duration"])
    out: list[dict] = []

    def add(ref_vol: tuple[str, float], t: float, label: str, kind: str) -> None:
        if t < total - 0.2:
            out.append({"t": round(max(0.0, t), 3), "ref": ref_vol[0], "volume": ref_vol[1], "label": label, "kind": kind})

    for e in job.get("events") or []:
        k, t = e["kind"], float(e["t"])
        if k == "blackout":
            add(S3.SFX["blackout"], t - 0.05, "блэкаут", "blackout")
            add(S3.SFX["power_down"], t, "гул глохнет", "power_down")
        elif k == "jolt":
            n = len([x for x in out if x["kind"] in ("jolt", "jolt2")])
            add(S3.SFX["jolt" if n % 2 == 0 else "jolt2"], t, "рывок", "jolt")
        elif k == "scrape":
            add(S3.SFX["scrape"], t, "скрежет", "scrape")
        elif k == "flash":
            add(S3.SFX["flash"], t, "вспышка", "flash")
        elif k == "knock":
            add(S3.SFX["knock"], t, "стук", "knock")
        elif k == "sting":
            add(S3.SFX["sting"], t, "акцент", "sting")
        elif k == "doors_close":
            add(SFX["doors_open"], t, "двери схлопнулись", "doors_close")
    for c in perf["contacts"]:
        if c["kind"] == "press":
            add(SFX["bell" if c.get("button") == "bell" else "press"], c["t"] - 0.01, "кнопка", "press")
        elif c["kind"] == "knock":
            add(SFX["knock_hit"], c["t"] - 0.01, "удар в дверь", "knock_hit")
        elif c["kind"] == "doors":
            add(SFX["doors_strain"], c["t"], "створки", "doors")
            if c.get("full"):
                add(SFX["doors_open"], c["t"] + 1.0, "двери разъехались", "doors_open")
    story = next((s for s in job["shots"] if s.get("role") not in ("hook", "")), None)
    if story:
        add(S3.SFX["tension"], story["t0"], "напряжение", "tension")
    b, back = perf.get("blackout"), perf.get("light_back")

    def bed(kind: str, t0: float, t1: float, label: str, fi: float, fo: float) -> None:
        if t1 - t0 >= 0.5:
            ref, under = S3.AMBIENCE[kind]
            out.append({"t": round(max(0.0, t0), 3), "ref": ref, "volume": 1.0, "label": label, "kind": kind,
                        "end": round(min(total, t1), 3), "role": "ambience", "under_db": under, "fade_in": fi, "fade_out": fo})

    if b is None:
        bed("hum", 0.0, total, "гул кабины", 0.4, 1.5)
    else:
        bed("hum", 0.0, b + 0.12, "гул кабины", 0.4, 0.18)
        bed("dark", b + 0.6, back or total, "гул шахты", 2.5, 1.0 if back else 2.0)
        if back:
            bed("hum", back, total, "гул кабины", 0.3, 1.5)
    return sorted(out, key=lambda x: x["t"])


# --------------------------------------------------------------------------- #
# Задание рендера, куски и кэш
# --------------------------------------------------------------------------- #

# Пресеты рендера. Узкое место Eevee здесь — не GPU, а CPU между кадрами: композитор (свечение + размытая виньетка
# на CPU), пересчёт сцены на каждый замер размытия движения, сжатие PNG. Поэтому быстрые пресеты: композитор на GPU,
# кадры JPEG 95, объём/размытие/глубина резкости — только в планах, где нужны (``shot_fx``), меньше сэмплов, два
# процесса Blender на разные планы.
#   samples — сэмплы Eevee; scale — % от 1080×1920 (fast720 — 720×1280, апскейл до 1080×1920 lanczos при склейке);
#   fx — all: объём/размытие/ГРИП везде, auto — по планам, none — без них; frames — формат кадров кэша;
#   comp_gpu — композитор на GPU; workers — процессов Blender; blur_steps — шагов размытия движения.
QUALITY = {
    "final": {"samples": 48, "scale": 100, "fx": "all", "frames": "PNG", "comp_gpu": False, "workers": 1, "blur_steps": 1,
              "label": "кино: как пробный кадр, всё везде"},
    "fast": {"samples": 24, "scale": 100, "fx": "auto", "frames": "JPEG", "comp_gpu": True, "workers": 2, "blur_steps": 1,
             "label": "финал быстро: 1080p, эффекты по планам, 2 процесса"},
    "fast720": {"samples": 24, "scale": 66.667, "fx": "auto", "frames": "JPEG", "comp_gpu": True, "workers": 2,
                "blur_steps": 1, "upscale": True, "label": "финал быстрее: 720p + апскейл lanczos"},
    "preview": {"samples": 16, "scale": 50, "fx": "auto", "frames": "JPEG", "comp_gpu": True, "workers": 1, "blur_steps": 1,
                "label": "проверка: 540p"},
    "draft": {"samples": 4, "scale": 50, "fx": "none", "frames": "JPEG", "comp_gpu": True, "workers": 2, "blur_steps": 1,
              "label": "черновик действий: 540p, 4 сэмпла, без объёма/размытия/ГРИП"},
}
DEFAULT_QUALITY = "fast"


def preset(job: dict) -> dict:
    """Пресет задания (неизвестный — ``DEFAULT_QUALITY``)."""
    name = job.get("quality") if job.get("quality") in QUALITY else DEFAULT_QUALITY
    return {"name": name, **QUALITY[name]}


def shot_fx(perf: dict, mode: str = "auto") -> dict[int, dict]:
    """Какие дорогие эффекты нужны плану: объём — когда в кадре луч фонарика или мигает/гаснет свет; размытие
    движения — шаги, рывок/испуг, стук, двери, тряска камеры; глубина резкости — крупные планы и детали."""
    fps = perf["fps"]
    out: dict[int, dict] = {}
    cams = {c["n"]: c for c in perf.get("cameras") or []}
    walks = [(sg["start"], sg["start"] + 1.2) for sg in perf["track"] if sg["clip"] in ("Walking_A", "Walking_B", "Walking_C", "Hit_A",
                                                                                          "Sit_Floor_Down", "Sit_Floor_StandUp")]
    for s in perf["shots"]:
        if mode in ("all", "none"):
            on = mode == "all"
            out[s["n"]] = {"volume": on, "blur": on, "dof": on}
            continue
        f0, f1 = int(s["t0"] * fps), min(perf["frames"], int(s["t1"] * fps) + 1)
        lt = perf["lights"][f0:f1]
        ph = perf["phone"][f0:f1]
        beam = any(p["on"] and p["light"] > 0.05 for p in ph)
        flick = any(0.02 < x["main"] < 0.98 for x in lt)
        move = any(a < s["t1"] and b > s["t0"] for a, b in walks)
        shake = any(x["shake"] > 0.003 or x["drop"] < -0.005 for x in lt)
        hits = any(c["kind"] in ("knock", "doors") and s["t0"] <= c["t"] < s["t1"] for c in perf["contacts"])
        st = any(s["t0"] - 0.1 <= t < s["t1"] for t, _ in perf.get("startles") or [])
        kind = (cams.get(s["n"]) or {}).get("kind") or s.get("camera")
        out[s["n"]] = {"volume": beam or flick or (s.get("location") or "cabin") != "cabin",
                       "blur": move or shake or hits or st, "dof": kind in ("face", "detail")}
    return out


def build_job(script: dict, words: list[dict], total: float, *, gemini: bool = True, client: Any = None,
              envelope: list[float] | None = None, lipsync_mode: str = "speaking", quality: str = DEFAULT_QUALITY,
              look: dict | None = None, seed: int = 7) -> dict:
    """Всё, что нужно сцене Blender (JSON): кадры с режиссурой, события, слова, огибающая голоса, качество."""
    plan = plan_shots(script, words, total, gemini=gemini, client=client)
    return {"version": SCENE_VERSION, "fps": FPS, "duration": round(float(total), 3), "title": script.get("title") or "",
            "shots": plan["shots"], "events": plan["events"], "words": plan["words"], "director": plan["director"],
            "speech": plan["speech"],
            "director_error": plan["director_error"], "envelope": [round(x, 4) for x in (envelope or [])],
            "lipsync": lipsync_mode if lipsync_mode in LIPSYNC_MODES else "speaking",
            "quality": quality if quality in QUALITY else DEFAULT_QUALITY, "look": look or {}, "seed": int(seed)}


def chunks(perf: dict) -> list[dict]:
    """Куски рендера = планы: ``[{n, f0, f1}]`` (кадры с 1, включительно)."""
    fps, nf = perf["fps"], perf["frames"]
    out = []
    for s in perf["shots"]:
        f0 = int(round(s["t0"] * fps)) + 1
        f1 = min(nf, int(round(s["t1"] * fps)))
        if f1 >= f0:
            out.append({"n": s["n"], "f0": f0, "f1": f1})
    return out


def _code_hash() -> str:
    root = Path(__file__).resolve().parent.parent
    h = hashlib.sha1()
    for p in (root / "tools" / "blender" / "series_scene.py", root / "tools" / "blender" / "hero_shot.py",
              root / "tools" / "blender" / "series_places.py", root / "app" / "motion3d.py"):
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()[:12]


def chunk_keys(job: dict, perf: dict) -> list[dict]:
    """Хэш куска — от покадровых дорожек его кадров (+ соседей на наплыв), плана камеры, качества и кода сцены."""
    code = _code_hash()
    pr = preset(job)
    fx = shot_fx(perf, pr["fx"])
    rset = {k: pr[k] for k in ("samples", "scale", "frames", "comp_gpu", "blur_steps")}
    out = []
    shots = {s["n"]: s for s in perf["shots"]}
    for c in chunks(perf):
        a, b = max(0, c["f0"] - 1 - 3), min(perf["frames"], c["f1"] + 3)
        s = shots[c["n"]]
        c_n = c["n"]
        blob = {"v": SCENE_VERSION, "code": code, "q": rset, "fx": fx.get(c["n"]), "f": [c["f0"], c["f1"]],
                "shot": {k: s.get(k) for k in ("action", "camera", "subject", "spot", "heading", "target", "button", "emotion",
                                              "posture_end", "phone", "t0", "t1", "a0", "screen", "hook")},
                "root": perf["root"][a:b], "heading": perf["heading"][a:b], "gaze": perf["gaze"][a:b],
                "face": perf["face"][a:b], "body": perf["body"][a:b], "lights": perf["lights"][a:b],
                "phone": perf["phone"][a:b], "hands": [perf["hands"]["r"][a:b], perf["hands"]["l"][a:b]],
                "track": [sg for sg in perf["track"] if sg["start"] < b / perf["fps"] + 1 and
                          sg["start"] + 60 > a / perf["fps"] - 2],
                "camera": next((c for c in perf["cameras"] if c["n"] == c_n), None),
                "look": job.get("look") or {}}
        key = hashlib.sha1(json.dumps(blob, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]
        out.append({**c, "key": key, "fx": fx.get(c["n"]), "ext": "jpg" if pr["frames"] == "JPEG" else "png"})
    return out


def frame_ok(path: Path) -> bool:
    """Кадр дописан до конца (PNG — IEND, JPEG — FFD9) — оборванный рендером кадр рисуем заново."""
    try:
        size = path.stat().st_size
        if size < 64:
            return False
        with path.open("rb") as fh:
            fh.seek(-12, os.SEEK_END)
            tail = fh.read(12)
        if path.suffix.lower() in (".jpg", ".jpeg"):
            return tail.rstrip(b"\x00")[-2:] == b"\xff\xd9"
        return tail[4:8] == b"IEND"
    except OSError:
        return False


def frame_name(f: int, ext: str = "png") -> str:
    """Имя кадра в папке куска — по номеру кадра серии (так пишет рендер анимации Blender: ``f_#####``)."""
    return f"f_{int(f):05d}.{ext}"


def missing_frames(cache: Path, keys: list[dict]) -> dict[str, list[int]]:
    """Каких кадров нет в кэше по кускам: ``{key: [номер кадра серии, …]}``."""
    out = {}
    for c in keys:
        d = cache / c["key"]
        need = [f for f in range(c["f0"], c["f1"] + 1) if not frame_ok(d / frame_name(f, c.get("ext", "png")))]
        if need:
            out[c["key"]] = need
    return out


def audio_envelope(path: Path | str, fps: int = FPS) -> list[float]:
    """Огибающая озвучки (RMS на кадр) — для лип-синка; без ffmpeg/файла — пусто."""
    try:
        proc = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                              capture_output=True, check=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return []
    import array

    pcm = array.array("h")
    pcm.frombytes(proc.stdout[: len(proc.stdout) // 2 * 2])
    step = 8000 // fps
    out = []
    for i in range(0, len(pcm), step):
        chunk = pcm[i:i + step]
        if not chunk:
            break
        out.append(math.sqrt(sum(x * x for x in chunk) / len(chunk)) / 32768.0)
    return out


DEFAULT_BLENDER = ["D:/vvf-offload/tools/blender/blender-4.5.15-windows-x64/blender.exe", "/usr/bin/blender"]


def find_blender(explicit: str | None = None) -> str | None:
    for c in [explicit or "", os.environ.get("BLENDER_BIN", ""), *DEFAULT_BLENDER]:
        if c and Path(c).exists():
            return c
    return shutil.which("blender")


def packs_root() -> Path:
    from app.settings import settings

    return Path(settings.motion_packs_dir)


def scene_config(job: dict, work: Path, cache: Path, packs: Path | None = None) -> dict:
    packs = packs or packs_root()

    def need(rel: str) -> str:
        p = packs / rel
        if not p.is_file():
            raise FileNotFoundError(f"нет {rel} — python tools/fetch_packs.py --only {rel.split('/')[0]}")
        return str(p)

    return {"job": str(work / "job.json"), "cache": str(cache), "out_dir": str(work),
            "character": need("kaykit-adventurers/Rogue_Hooded.glb"),
            "animations": [need(f"kaykit-animations/{n}.glb") for n in ("Rig_Medium_General", "Rig_Medium_MovementBasic",
                                                                          "Rig_Medium_Simulation")],
            "samples": preset(job)["samples"],
            "quality": preset(job)["name"], "preset": preset(job),
            "font_display": ["C:/Windows/Fonts/bahnschrift.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf"],
            "font_ui": ["C:/Windows/Fonts/bahnschrift.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"],
            "font_symbols": ["C:/Windows/Fonts/seguisym.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]}


class RenderError(RuntimeError):
    pass


def frame_spread(path: Path | str, samples: int = 8) -> list[float]:
    """Разброс яркости (СКО, 0..255) кадров видео в ``samples`` точках — однотонный кадр ≈ 0."""
    from app.dubbing import probe_duration

    try:
        dur = float(probe_duration(Path(path)) or 0.0)
    except Exception:  # noqa: BLE001 - нечитаемый файл — пустой список, вызывающий считает браком
        return []
    out = []
    for i in range(samples):
        t = dur * (i + 0.5) / samples
        proc = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
                               "-vf", "scale=72:128,format=gray", "-f", "rawvideo", "-"], capture_output=True, check=False)
        px = proc.stdout
        if not px:
            continue
        mean = sum(px) / len(px)
        out.append(math.sqrt(sum((x - mean) ** 2 for x in px) / len(px)))
    return out


def video_flat(path: Path | str, samples: int = 8, threshold: float = 3.0) -> bool:
    """Видео однотонное (зелёная основа холста без слоёв, чёрный/пустой рендер): все пробы почти без разброса."""
    spread = frame_spread(path, samples)
    return not spread or max(spread) < threshold


def split_chunks(keys: list[dict], todo: dict[str, list[int]], workers: int) -> list[list[dict]]:
    """Куски с недостающими кадрами — по процессам поровну по числу кадров (жадно, крупные первыми)."""
    groups: list[list[dict]] = [[] for _ in range(max(1, workers))]
    load = [0] * len(groups)
    for c in sorted((c for c in keys if c["key"] in todo), key=lambda c: -len(todo[c["key"]])):
        i = load.index(min(load))
        groups[i].append(c)
        load[i] += len(todo[c["key"]])
    return [g for g in groups if g]


def render_local(job: dict, out_mp4: Path | str, *, work: Path | str, cache: Path | str | None = None,
                 blender: str | None = None, progress: Callable[[float, str], Any] | None = None,
                 only: list[int] | None = None, stills: list[int] | None = None, timeout: float = 4 * 3600,
                 should_stop: Callable[[], bool] | None = None, workers: int | None = None) -> dict:
    """Рендер серии Blender'ом здесь: сцена один раз на процесс → недостающие кадры кусков в кэш (возобновляемо)
    → склейка в mp4 без звука. ``progress(доля 0..1, заметка)``; ``only`` — номера планов; ``stills`` — только эти
    кадры (номера с 1, PNG в ``work``) для проверки; ``should_stop()`` — True: остановить Blender (отмена задания);
    ``workers`` — процессов Blender (по умолчанию из пресета: куски делятся между ними)."""
    import threading

    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    cache = Path(cache or work / "cache")
    cache.mkdir(parents=True, exist_ok=True)
    exe = find_blender(blender)
    if not exe:
        raise RenderError("нет Blender: задай BLENDER_BIN (portable 4.5 LTS) — рендер 3D-серии идёт на компьютере с GPU")
    pr = preset(job)
    perf = perform(job)
    keys = chunk_keys(job, perf)
    if only is not None:
        keys = [c for c in keys if c["n"] in set(only)]
    todo = missing_frames(cache, keys)
    total_missing = sum(len(v) for v in todo.values())
    (work / "job.json").write_text(json.dumps(job, ensure_ascii=False), "utf-8")
    base = scene_config(job, work, cache)
    scene = Path(__file__).resolve().parent.parent / "tools" / "blender" / "series_scene.py"
    t0 = time.time()
    groups = [keys] if stills else split_chunks(keys, todo, int(workers or pr["workers"]))
    done = [0] * len(groups)
    lock = threading.Lock()
    stop = {"err": None}
    procs: list[subprocess.Popen] = []

    def run(i: int, group: list[dict]) -> None:
        wdir = work if len(groups) == 1 else work / f"w{i}"
        wdir.mkdir(parents=True, exist_ok=True)
        cfg = {**base, "out_dir": str(wdir), "chunks": group, "todo": {c["key"]: todo[c["key"]] for c in group
                                                                      if c["key"] in todo}}
        if stills:
            cfg["stills"] = [int(x) for x in stills]
        (wdir / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=1), "utf-8")
        with (wdir / "blender.log").open("w", encoding="utf-8", errors="replace") as log:
            proc = subprocess.Popen([exe, "-b", "--factory-startup", "-P", str(scene), "--", "--config",
                                     str(wdir / "config.json")], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding="utf-8", errors="replace")
            procs.append(proc)
            assert proc.stdout is not None
            for line in proc.stdout:
                log.write(line)
                if not line.startswith("PROGRESS "):
                    continue
                log.flush()
                try:
                    n = int(line.split()[1])
                except (IndexError, ValueError):
                    continue
                with lock:
                    done[i] = n
                    total = sum(done)
                    if progress:
                        progress(min(0.99, total / max(1, total_missing)), f"кадр {total}/{total_missing}")
                    if should_stop is not None and should_stop():
                        stop["err"] = "рендер остановлен (задание отменено) — готовые кадры в кэше"
                    if time.time() - t0 > timeout:
                        stop["err"] = "рендер дольше лимита — остановлен (кадры в кэше, повторный запуск продолжит)"
                    if stop["err"]:
                        for p_ in procs:
                            p_.kill()
                        break
            code = proc.wait()
        if code != 0 and not stop["err"]:
            tail = (wdir / "blender.log").read_text("utf-8", errors="replace")[-600:]
            stop["err"] = f"Blender упал (код {code}); кадры в кэше — повторный запуск продолжит. {tail}"

    if total_missing or stills:
        if progress:
            progress(0.0, f"сцена Blender: {total_missing} кадров к рендеру из {perf['frames']}, процессов {len(groups)}")
        threads = [threading.Thread(target=run, args=(i, g), daemon=True) for i, g in enumerate(groups)]
        for i, th in enumerate(threads):
            th.start()
            if i + 1 < len(threads):
                time.sleep(2.0)   # второй процесс стартует со сдвигом — не толкаются на загрузке сцены
        for th in threads:
            th.join()
        if stop["err"]:
            raise RenderError(stop["err"])
    reports = [json.loads(p_.read_text("utf-8")) for p_ in sorted(work.glob("report.json")) + sorted(work.glob("w*/report.json"))]
    report = reports[0] if reports else {}
    if len(reports) > 1:
        report = {**report, "workers": [{k: r.get(k) for k in ("setup_sec", "render_sec", "rendered")} for r in reports]}
    render_sec = round(time.time() - t0, 1)
    if stills:
        return {"ok": True, "stills": [str(work / f"still_{int(f):04d}.png") for f in stills], "report": report,
                "seconds": render_sec}
    left = missing_frames(cache, keys)
    if left:
        raise RenderError(f"не дорисованы куски: {', '.join(list(left)[:5])} — повторный запуск продолжит")
    out = Path(out_mp4)
    out.parent.mkdir(parents=True, exist_ok=True)
    seq = work / "seq"
    if seq.exists():
        shutil.rmtree(seq)
    seq.mkdir()
    n = 0
    exts = set()
    for c in keys:
        ext = c.get("ext", "png")
        exts.add(ext)
        for f in range(c["f0"], c["f1"] + 1):
            n += 1
            src = cache / c["key"] / frame_name(f, ext)
            dst = seq / f"f_{n:05d}.{ext}"
            try:
                os.link(src, dst)
            except OSError:
                shutil.copyfile(src, dst)
    ext = exts.pop() if len(exts) == 1 else "png"
    scale = "scale=1080:1920:flags=lanczos" if pr.get("upscale") else "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    t_enc = time.time()
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-framerate", str(perf["fps"]), "-i", str(seq / f"f_%05d.{ext}"),
                    "-vf", f"{scale},noise=alls=3:allf=t,format=yuv420p", "-c:v", "libx264", "-preset", "slow",
                    "-crf", "17", "-profile:v", "high", "-movflags", "+faststart", str(out)], check=True)
    shutil.rmtree(seq, ignore_errors=True)
    if video_flat(out):
        raise RenderError(f"склейка однотонная (пустые кадры?) — {out.name} не годится, кадры в кэше проверь")
    if progress:
        progress(1.0, "склеено")
    return {"ok": True, "out": str(out), "frames": n, "rendered": total_missing, "cached": n - total_missing,
            "seconds": round(time.time() - t0, 1), "encode_sec": round(time.time() - t_enc, 1), "report": report,
            "chunks": len(keys), "preset": pr["name"], "processes": len(groups)}


# --------------------------------------------------------------------------- #
# Для сборки холста (app/stories_3d.py)
# --------------------------------------------------------------------------- #

def result_for(job: dict, perf: dict | None = None) -> dict:
    """Режиссура в виде ``series3d.direct``: кадры (для обложек), события, звуки, локация."""
    perf = perf or perform(job)
    cam_map = {"overhead": "overhead", "face": "face", "ots": "ots", "detail": "detail"}
    shots = [{"n": s["n"], "t0": s["t0"], "t1": s["t1"], "text": s["text"], "role": s.get("role") or "",
              "action": s["action"], "camera": cam_map[s["camera"]], "spot": s.get("target") or "",
              "emotion": s.get("emotion"), "speaks": bool(s.get("speaks")), "speaker": s.get("speaker") or "narrator",
              "by": s.get("by")} for s in job["shots"]]
    return {"shots": shots, "events": job.get("events") or [], "sfx": sfx_for(job, perf), "location": "elevator",
            "faces": face_bands(perf),
            "checks": [], "notes": [], "look": job.get("look") or {}, "director": job.get("director")}
