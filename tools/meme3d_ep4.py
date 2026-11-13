"""Мем-серия 4 «Экономия»: юзер купил пачку дешёвых аккаунтов Клода вместо подписки — Клоды по очереди упираются
в лимит и вырубаются лицом в клаву, за креслом растёт гора спящих «нас», а в углу всё это время стоит Кодекс с чаем.

Сценарий (одобрен владельцем): D:/vvf-offload/memes/ep4/script_limits.md. Рецепт — пилоты 2/3 (toon ink, вечерний
свет, склейки каждые 1–2 с, мат в субтитрах звёздочками). Герой — зверёк tools/meme3d_hero_claude.py с бейджем
«аккаунт N» (critter.ts: ``badge``), Кодекс — node/scene3d/src/kodex.ts, юзер — моти в худи (meme_props.ts:
``mochi_user``), коробки на полке — ``toy_box``, кресло — ``mini_chair``.

Шаг 1 — кадры-эскизы (обложка + по кадру на бит + каст) на согласование внешности::

    python tools/meme3d_ep4.py stills D:/vvf-offload/memes/ep4

Голос: ``work/take1_callum.mp3`` (Клод: закадр и реплики в кадре) и ``work/take1_eric.mp3`` (Кодекс) — пробы
tools/meme3d_voice.py; разметка реплик — ``LINES`` (границы по паузам пробы, silencedetect −38 дБ).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import meme3d_hero_claude as H  # noqa: E402
import meme3d_pilot as P1  # noqa: E402
import meme3d_scene as S  # noqa: E402

T = S.TOP
W_HERO = 0.27  # ширина корпуса Клода, м (как в пилоте 3)
HOOK = "ЭКОНОМИЯ"

# --------------------------------------------------------------------------- голос и темп

# Реплики: (id, начало и конец в пробе, когда звучит в ролике, текст субтитров, проба). Монтаж — плотный: паузы
# между репликами 0.1–0.15 с, тишина только после «бума» лимита (бит 4) и на быстрой нарезке (бит 6) — под ней звуки.
TAKE_C = "take1_callum.mp3"
TAKE_K = "take1_eric.mp3"
LINES = [
    ("c1", 0.00, 1.68, 0.10, "Мой юзер долбо**.", TAKE_C),
    ("c2", 2.06, 4.10, 1.90, "Он думает, что придумал, как экономить.", TAKE_C),
    ("c3", 4.58, 6.92, 4.05, "Подписка за двести баксов ему дорого.", TAKE_C),
    ("c4", 7.34, 9.28, 6.50, "Он купил пачку дешёвых аккаунтов.", TAKE_C),
    ("c5", 9.79, 10.31, 8.55, "Нас.", TAKE_C),
    ("c6", 10.70, 11.56, 9.25, "Я работаю.", TAKE_C),
    ("c7", 11.86, 13.17, 10.25, "Я реально работаю.", TAKE_C),
    # 11.6 — «Лимит исчерпан», 11.85 — лицом в клаву, бум, тишина до 12.9.
    ("c8", 13.63, 15.31, 12.90, "Потом приходит следующий я.", TAKE_C),
    ("c9", 15.55, 17.05, 14.68, "Спихивает меня со стула.", TAKE_C),
    ("c10", 17.52, 18.69, 16.35, "Так. Что делаем?", TAKE_C),  # Клод 2 в кадре
    ("c11", 19.07, 20.61, 17.65, "Он не помнит, что делал я.", TAKE_C),
    ("c12", 20.86, 21.98, 19.30, "Начинает сначала.", TAKE_C),
    # 20.45–22.6 — быстрая нарезка: аккаунты 3…8 печатают, «Лимит», вырубаются, гора растёт.
    ("c13", 22.26, 24.62, 22.70, "Девятый я спрашивает то же самое.", TAKE_C),
    ("c10b", 17.52, 18.69, 25.20, "Так. Что делаем?", TAKE_C),  # Клод 9 — та же запись, слово в слово
    ("c14", 25.01, 27.24, 26.50, "А в углу всё это время стоит Кодекс.", TAKE_C),
    ("c15", 27.45, 28.22, 28.83, "И пьёт чай.", TAKE_C),
    ("k1", 0.00, 2.15, 29.75, "А может, я что-нибудь сделаю?", TAKE_K),
    ("c16", 28.76, 29.13, 32.00, "Нет.", TAKE_C),
    ("c17", 29.43, 30.96, 32.60, "Нарисуй мне графику на сайт.", TAKE_C),
    ("c18", 31.14, 31.91, 34.20, "И побыстрее.", TAKE_C),
    ("c19", 32.28, 33.02, 35.70, "Сэкономил.", TAKE_C),
]
DURATION = 37.2


# --------------------------------------------------------------------------- раскладка стола

# Как в пилоте 3 (одобрено): Клод лицом к камере, монитор сзади-слева, клавиатура перед ним. Кресло — под ним,
# спинка к стене; гора спящих растёт справа за креслом, Кодекс — в правом углу у окна.
MON = S.MONITOR_POS
CHAIR = [0.14, T, 0.02]
SEAT_H = 0.045
SEAT = [CHAIR[0], T + SEAT_H + 0.015, CHAIR[2] + 0.01]  # где стоит Клод на сиденье
KB = [0.12, T, 0.235]  # клавиатура — перед ним, у края стола
KODEX = [0.84, T, -0.2]
PILE_C = [0.42, T, -0.3]  # центр горы спящих (за креслом, справа)
PAPER = [0.5, T, 0.22]  # лист Кодекса (финал)
HERO_YAW = -8

# Виньетки (отдельные «комнаты» по оси x): юзер, полка с коробками, каст.
X_USER, X_SHELF, X_CAST = 6.0, 9.0, -6.0

# Гора спящих: (dx, y, dz, yaw, lean, tilt) от PILE_C — кто где и как лежит; по порядку «вырубания», 9 штук.
# lean < 0 — на спине (лицо вверх, видно закрытые глаза), > 0 — лицом вниз, tilt ±90 — на боку.
PILE = [
    (-0.13, 0.03, 0.08, 15, -82, 0),
    (0.13, 0.0, 0.1, -25, 0, 0),
    (0.02, 0.03, -0.1, 40, -80, 10),
    (0.21, 0.03, -0.1, -10, -84, -8),
    (-0.17, 0.0, -0.12, -40, 80, 0),
    (0.0, 0.12, 0.04, 10, -70, 12),
    (0.16, 0.12, -0.02, -30, 0, 84),
    (-0.08, 0.13, -0.06, 30, -75, -10),
    (0.05, 0.22, -0.02, -5, -65, 0),
]


def critter(cid: str, num: int, pose: list[dict], face: list[dict], position: list[float], yaw: float, **kw) -> dict:
    """Клод «аккаунт N»: тот же зверёк пилота 3 (гарнитура, «Claude»), бейдж на груди — отличается только номер."""
    ch = H.hero(pose, face, kw.pop("voice", None), kw.pop("blinks", None), position=position, yaw=yaw, width=W_HERO, label="Claude",
                char_id=cid, seed=kw.pop("seed", 5 + num), glow=0.05)
    ch["critter"]["badge"] = {"text": "аккаунт", "number": num}
    ch.update(kw)
    return ch


def room_objects(session: dict, state: list[dict] | None = None) -> list[dict]:
    """Комната пилота 3 (вечер): монитор показывает сессию «аккаунта» (desk_ui: session), плюс кресло под Клода."""
    objs = S.objects({"tabs": []}, [], [])
    out = []
    for o in objs:
        i = o["id"]
        if i == "wall":
            o["params"]["color"] = "#5c7488"
        elif i == "wall_low":
            o["params"]["color"] = "#4a5f72"
        elif i == "monitor":
            o["params"] = {**S.SCREEN, "color": S.PALETTE["cream"], "stand": "#c8b59b", "px": 1280, "notes": ["#ffe27a", "#ffb4a2"],
                           "panel": "session", "panelParams": session, "panelStart": 0, "glow": 0.8}
            if state:
                o["state"] = state
        elif i == "keyboard":
            o["position"] = KB
            o["rotation"] = [0, -6, 0]
        elif i in ("mug", "notes"):
            continue
        elif i == "plant":
            o["position"] = [0.9, T, -0.5]
        elif i == "lamp":
            o["position"] = [-0.66, T, -0.4]
            o["rotation"] = [0, 160, 0]
        out.append(o)
    out += [
        {"id": "garland", "primitive": "string_lights", "position": [0, 0, 0],
         "params": {"points": [[-1.0, T + 0.82, -0.585], [-0.4, T + 0.74, -0.585], [0.15, T + 0.82, -0.585],
                               [0.62, T + 0.94, -0.585], [1.1, T + 0.86, -0.585]],
                    "color": "#ffc773", "count": 26, "sag": 0.07, "seed": 5}},
        {"id": "chair", "primitive": "mini_chair", "params": {"width": 0.34, "seatH": SEAT_H, "backH": 0.25, "color": "#3b4160",
                                                              "accent": "#d97757"},
         "position": CHAIR, "rotation": [0, HERO_YAW, 0]},
    ]
    return out


def lights(extra: list[dict] | None = None) -> list[dict]:
    c = S.screen_center()
    bx, by, bz = SEAT
    out = [
        {"type": "hemisphere", "color": "#8794c4", "ground": "#4a2e3a", "intensity": 0.45},
        {"type": "ambient", "color": "#c79aa8", "intensity": 0.34},
        {"type": "directional", "id": "key", "color": "#ffe6c8", "intensity": 1.35, "position": [1.4, 2.6, 2.1],
         "target": [0, T, 0], "castShadow": True, "shadow": {"extent": 1.4, "size": 2048, "bias": -0.0006, "normalBias": 0.015}},
        {"type": "spot", "id": "screen", "color": "#ffe9cf", "intensity": 1.0, "position": [c[0] + 0.05, c[1], c[2] + 0.06],
         "target": [bx, by + 0.12, bz], "angle": 55, "penumbra": 1, "decay": 2, "distance": 2.5},
        {"type": "point", "id": "lamp", "color": "#ffb070", "intensity": 1.4, "distance": 2.2, "decay": 2,
         "position": [-0.62, T + 0.3, -0.3]},
        {"type": "directional", "id": "moon", "color": "#9fb6ff", "intensity": 0.9, "position": [1.2, 2.2, -2.2], "target": [bx, T, bz]},
        {"type": "point", "id": "garland_l", "color": "#ffc773", "intensity": 0.6, "distance": 1.4, "decay": 2,
         "position": [-0.6, T + 0.72, -0.5]},
        {"type": "point", "id": "garland_r", "color": "#ffc773", "intensity": 0.6, "distance": 1.4, "decay": 2,
         "position": [0.5, T + 0.72, -0.5]},
        {"type": "point", "id": "corner", "color": "#ffd2a0", "intensity": 0.7, "distance": 1.2, "decay": 2,
         "position": [KODEX[0] - 0.2, T + 0.45, KODEX[2] + 0.35]},
    ]
    for x, accent in ((X_USER, "#ffb36b"), (X_SHELF, "#ff8fb1"), (X_CAST, "#9fb6ff")):
        out += [
            {"type": "point", "color": "#fff1dc", "intensity": 2.2, "distance": 3, "decay": 2, "position": [x + 0.7, T + 0.9, 0.9]},
            {"type": "point", "color": accent, "intensity": 1.3, "distance": 2, "decay": 2, "position": [x - 0.5, T + 0.6, -0.1]},
        ]
    return out + (extra or [])


def vignette_set(x: float, wall: str, wall2: str, tag: str) -> list[dict]:
    return [
        {"id": f"{tag}_wall", "primitive": "box", "params": {"size": [2.4, 2.6, 0.05], "color": wall}, "position": [x, 0, -0.45]},
        {"id": f"{tag}_wall2", "primitive": "box", "params": {"size": [2.4, 0.5, 0.06], "color": wall2}, "position": [x, T + 0.62, -0.43]},
        {"id": f"{tag}_desk", "primitive": "rounded_box", "params": {"size": [1.6, 0.06, 0.9], "color": "#c98c58", "radius": 0.02},
         "position": [x, T - 0.06, 0.0]},
        {"id": f"{tag}_front", "primitive": "box", "params": {"size": [1.6, T - 0.05, 0.05], "color": "#8f5634"}, "position": [x, 0, 0.43]},
    ]


def user_objects() -> list[dict]:
    """Юзер: моти в худи у ноутбука, довольно потирает ручки (бит 1)."""
    x = X_USER
    return vignette_set(x, "#9fb7d9", "#7f9cc4", "user") + [
        {"id": "user", "primitive": "mochi_user", "params": {"size": 0.3, "hoodie": "#6f86e0", "mood": "rub", "eyes": "smug"},
         "position": [x - 0.1, T, -0.1], "rotation": [0, 14, 0]},
        {"id": "user_mug", "primitive": "mug", "params": {"color": "#ffb36b", "radius": 0.03, "height": 0.07}, "position": [x - 0.38, T, 0.08]},
        {"id": "user_poster", "primitive": "poster", "params": {"width": 0.26, "height": 0.34, "colors": ["#f6e6cf", "#e2a83b", "#3f7f84", "#d97757"],
                                                                "frame": "#8f5634", "seed": 9},
         "position": [x - 0.42, T + 0.5, -0.42]},
    ]


def shelf_objects() -> tuple[list[dict], list[dict]]:
    """Полка магазина: коробки-блистеры с одинаковыми Клодами (бейджи 1…6), ценник «копейки» (бит 2)."""
    x = X_SHELF
    objs = vignette_set(x, "#f2c48f", "#e0a46c", "shop")
    chars = []
    bw, bh, bd = 0.36, 0.25, 0.2
    for r, y in enumerate([T + 0.0, T + 0.4]):
        objs.append({"id": f"shelf{r}", "primitive": "rounded_box", "params": {"size": [1.3, 0.025, 0.26], "color": "#8f5634", "radius": 0.006},
                     "position": [x, y - 0.0125, -0.12]})
        for c in range(3):
            n = (1 - r) * 3 + c + 1  # верхний ряд 1–3, нижний 4–6
            bx = x + (c - 1) * 0.4
            objs.append({"id": f"box{n}", "primitive": "toy_box",
                         "params": {"size": [bw, bh, bd], "color": "#f0a868", "headColor": "#2b1c20", "title": "Claude", "sub": f"аккаунт {n}"},
                         "position": [bx, y, -0.12]})
            chars.append(critter(f"b{n}", n, [{"t": 0, "armL": "rest", "armR": "rest"}], [{"t": 0, "expr": "happy"}],
                                 [bx, y + 0.02, -0.12], 0, stepFps=15))
    objs += [
        {"id": "price", "primitive": "sign_plate", "params": {"text": "копейки", "sub": "за штуку", "bg": "#ffd23f", "fg": "#c8261e",
                                                              "width": 0.26, "height": 0.11, "tilt": -6, "glow": 0.1},
         "position": [x + 0.16, T + 0.355, 0.04]},
        {"id": "sale", "primitive": "sign_plate", "params": {"text": "−95%", "bg": "#c8261e", "fg": "#fff6ea", "width": 0.17, "height": 0.09,
                                                             "tilt": 8, "glow": 0.15},
         "position": [x - 0.45, T + 0.75, -0.05]},
    ]
    return objs, chars


def pile_place(k: int) -> tuple[list[float], float, dict]:
    dx, y, dz, yaw, lean, tilt = PILE[k]
    return [PILE_C[0] + dx, T + y, PILE_C[2] + dz], yaw, {"lean": lean, "tilt": tilt}


# --------------------------------------------------------------------------- кадры-эскизы


def stills_spec() -> tuple[dict, list[tuple[float, str]]]:
    """Одна сцена, кадры по слотам в 1 с: в слоте i — своя расстановка (ключи-ступеньки) и свой план камеры."""
    chars: list[dict] = []
    objs: list[dict] = []
    overlays: list[dict] = []
    SL = ["cover", "b1_user", "b2_shelf", "b3_work", "b4_limit", "b5_push", "b6_montage", "b7_ninth", "b8_kodex", "b9_pencil",
          "b10_final", "cast"]
    n = len(SL)
    slot = {name: i for i, name in enumerate(SL)}

    def vis(*names: str) -> list[dict]:
        on = {slot[s] for s in names}
        return [{"t": i, "on": i in on} for i in range(n)]

    def keyed(per_slot: dict[str, dict], default: dict) -> list[dict]:
        return [{"t": i, "ease": "step", **default, **per_slot.get(name, {})} for i, name in enumerate(SL)]

    def moved(per_slot: dict[str, list[float]], default: list[float]) -> list[dict]:
        return [{"t": i, "ease": "step", "position": per_slot.get(name, default)} for i, name in enumerate(SL)]

    sx, sy, sz = SEAT
    # Экран: сессии по слотам: работа (код), лимит (красный), новая сессия («> Так. Что делаем?»).
    session = {"account": 1, "speed": 7,
               "limits": [slot["cover"] + 0.01, slot["b4_limit"] + 0.05, slot["b6_montage"] + 0.7],
               "resets": [{"t": slot["b1_user"], "account": 1}, {"t": slot["b3_work"] - 2.5, "account": 1},
                          {"t": slot["b5_push"], "account": 2}, {"t": slot["b6_montage"] - 1.4, "account": 5},
                          {"t": slot["b7_ninth"], "account": 9}, {"t": slot["b8_kodex"] - 1.5, "account": 9}]}
    objs += room_objects(session)
    # «ЛИМИТ» над вырубившимся Клодом (обложка, бит 4), лист Кодекса (финал).
    objs += [
        {"id": "limit_sign", "primitive": "sign_plate", "params": {"text": "ЛИМИТ", "bg": "#d8342c", "fg": "#fff6ea", "width": 0.2,
                                                                   "height": 0.078, "tilt": -7, "glow": 0.35},
         "position": [sx - 0.01, T + 0.25, sz + 0.02], "rotation": [0, HERO_YAW, 0], "visible": vis("cover")},
        {"id": "paper", "primitive": "paper_sheet", "params": {"width": 0.15, "height": 0.2, "panelParams": {"start": 0, "dur": 0.6}},
         "position": PAPER, "rotation": [0, 8, 0], "visible": vis("b10_final")},
    ]
    objs += user_objects()
    shop, shop_chars = shelf_objects()
    objs += shop
    chars += shop_chars

    # --- Клод «аккаунт 1»: обложка (лицом в клаву), работает, лимит, его спихивают; каст.
    face_down = {"lean": 50, "tilt": -5, "turn": 4, "armL": [-25, 40, 0.3, 0], "armR": [-10, 60, 0.4, 0], "squash": 0.95, "type": 0,
                 "deflate": 0.3}
    a1_pose = keyed({
        "cover": face_down,
        "b3_work": {"lean": 12, "turn": -6, "armL": "type", "armR": "type", "type": 1, "squash": 1.03},
        "b4_limit": face_down,
        "b5_push": {"lean": 25, "tilt": -40, "turn": 15, "armL": "flail", "armR": "flail", "hop": 0.03},
        "cast": {"lean": 0, "turn": 8},
    }, {"lean": 0, "tilt": 0, "turn": 0, "hop": 0, "squash": 1.0, "armL": "rest", "armR": "rest", "type": 0, "deflate": 0})
    a1_face = [{"t": 0, "expr": "ko", "blend": 0}, {"t": slot["b3_work"], "expr": "working", "blend": 0, "look": [-0.2, -0.4]},
               {"t": slot["b4_limit"], "expr": "ko", "blend": 0, "look": [0, 0]}, {"t": slot["b5_push"], "expr": "shocked", "blend": 0},
               {"t": slot["cast"], "expr": "neutral", "blend": 0, "look": [0, 0]}]
    slump = [sx - 0.01, sy - 0.035, sz + 0.035]
    chars.append(critter("a1", 1, a1_pose, a1_face, SEAT, HERO_YAW, autoBlink=False, stepFps=15,
                         move=moved({"cover": slump, "b4_limit": slump, "b5_push": [sx + 0.2, sy + 0.02, sz + 0.02],
                                     "cast": [X_CAST - 0.25, T, 0.06]}, SEAT),
                         visible=vis("cover", "b3_work", "b4_limit", "b5_push", "cast")))
    # --- Клод 2: залез на кресло и толкает первого вбок.
    chars.append(critter("a2", 2, [{"t": 0, "lean": 10, "turn": 35, "tilt": 8, "armL": "jab", "armR": "jab"}],
                         [{"t": 0, "expr": "focused", "look": [0.8, 0]}], [sx - 0.06, sy, sz], HERO_YAW, autoBlink=False, stepFps=15,
                         visible=vis("b5_push")))
    # --- Клод 5 (быстрая нарезка) и Клод 9.
    chars.append(critter("a5", 5, [{"t": 0, "lean": 12, "turn": -6, "armL": "type", "armR": "type", "type": 1}],
                         [{"t": 0, "expr": "working", "look": [-0.2, -0.4]}], SEAT, HERO_YAW, autoBlink=False, stepFps=15,
                         visible=vis("b6_montage")))
    a9_pose = keyed({
        "b7_ninth": {"turn": 0, "lean": 3, "armL": "rest", "armR": "shrug"},
        "b8_kodex": {"turn": -4, "lean": 10, "armL": "type", "armR": "type", "type": 1},
        "b9_pencil": {"turn": 35, "lean": -4, "hop": 0.012, "squash": 1.05, "armL": [78, 35, 0.8, 0], "armR": "rest"},
    }, {"armL": "rest", "armR": "rest"})
    a9_face = [{"t": 0, "expr": "neutral"}, {"t": slot["b7_ninth"], "expr": "neutral", "look": [0, 0]},
               {"t": slot["b8_kodex"], "expr": "deadpan", "look": [-0.2, -0.3]}, {"t": slot["b9_pencil"], "expr": "grumpy", "look": [0.6, 0.5]}]
    chars.append(critter("a9", 9, a9_pose, a9_face, SEAT, HERO_YAW, autoBlink=False, stepFps=15,
                         move=moved({"b9_pencil": [0.53, T, 0.12]}, SEAT),
                         visible=vis("b7_ninth", "b8_kodex", "b9_pencil")))
    # Карандаш в ручке Клода 9 (бит 9).
    objs.append({"id": "pencil9", "primitive": "cylinder", "params": {"size": [0.016, 0.15, 0.016], "color": "#f2c14e", "segments": 6},
                 "position": [KODEX[0] - 0.13, T + 0.1, KODEX[2] + 0.1], "rotation": [0, 0, 20], "visible": vis("b9_pencil"),
                 "attach": [{"to": "a9:hand_l", "start": slot["b9_pencil"], "end": slot["b9_pencil"] + 1, "fadeIn": 0, "fadeOut": 0,
                             "offset": [0.0, 0.05, 0.02], "upright": False}]})
    # --- Гора спящих: бит 6 — четверо, 7–9 — восемь, финал — девять.
    counts = {"b6_montage": 4, "b7_ninth": 8, "b8_kodex": 8, "b9_pencil": 8, "b10_final": 9}
    for k in range(9):
        pos, yaw, lay = pile_place(k)
        slots = [s for s, c in counts.items() if c > k]
        pose = [{"t": 0, **lay, "armL": "down", "armR": "down", "deflate": 0.35}]
        chars.append(critter(f"s{k + 1}", k + 1, pose, [{"t": 0, "expr": "sleep"}], pos, yaw, autoBlink=False, stepFps=15,
                             visible=vis(*slots)))
        if k in (0, 2, 3, 5, 8):
            objs.append({"id": f"zzz{k}", "primitive": "zzz", "params": {"size": 0.045, "phase": k * 0.7},
                         "position": [pos[0] + 0.04, pos[1] + 0.16, pos[2] + 0.05], "visible": vis(*slots)})
    # --- Кодекс: с чаем в углу; берёт карандаш; рисует; каст — анфас.
    k_pose = keyed({
        "b8_kodex": {"armR": "mug", "armL": "rest", "headTilt": 6, "holdR": "mug"},
        "b9_pencil": {"armR": [48, 2, 12], "armL": "rest", "lean": 10, "headNod": 18, "holdR": "none", "turn": -12},
        "b10_final": {"armR": "draw", "armL": [26, 12, 55], "lean": 22, "headNod": 24, "holdR": "pencil", "turn": 30},
        "cast": {"armR": "mug", "armL": "rest", "headTilt": 4, "holdR": "mug", "turn": 30},
    }, {"armR": "mug", "armL": "rest", "holdR": "mug", "holdL": "none", "lean": 0, "headNod": 0, "headTilt": 0, "turn": 0})
    k_face = [{"t": 0, "expr": "calm"}, {"t": slot["b9_pencil"], "expr": "hopeful", "look": [-0.4, -0.3]},
              {"t": slot["b10_final"], "expr": "focused", "look": [0, -0.7]}, {"t": slot["cast"], "expr": "calm", "look": [0, 0]}]
    chars.append({"id": "kodex", "style": "kodex", "height": 0.4, "position": KODEX, "yaw": -30, "pose": k_pose, "face": k_face,
                  "stepFps": 15, "visible": vis("b7_ninth", "b8_kodex", "b9_pencil", "b10_final", "cast"),
                  "move": moved({"b9_pencil": [0.79, T, 0.04], "b10_final": [PAPER[0] - 0.01, T, PAPER[2] - 0.15],
                                 "cast": [X_CAST + 0.02, T, 0.0]}, KODEX)})
    # --- Каст: Клод, Кодекс, юзер рядом на нейтральном столе.
    objs += vignette_set(X_CAST, "#3d4a66", "#334059", "cast")
    objs.append({"id": "cast_user", "primitive": "mochi_user", "params": {"size": 0.3, "hoodie": "#6f86e0", "mood": "idle", "eyes": "smug",
                                                                          "laptop": False},
                 "position": [X_CAST + 0.27, T, 0.06], "rotation": [0, -12, 0]})
    # --- Камера по слотам: (позиция, цель, fov).
    fy = sy + H.face_height(W_HERO)
    cam = {
        # Обложка: крупно, чуть снизу-спереди — лицо смято о клавиатуру, «ЛИМИТ» над спиной.
        "cover": ([sx + 0.12, T + 0.11, sz + 1.3], [sx + 0.0, T + 0.15, sz + 0.1], 30),
        "b1_user": ([X_USER + 0.06, T + 0.26, 1.55], [X_USER + 0.0, T + 0.17, -0.04], 34),
        "b2_shelf": ([X_SHELF + 0.0, T + 0.46, 2.45], [X_SHELF, T + 0.35, -0.12], 34),
        "b3_work": ([sx + 0.24, fy + 0.1, sz + 1.45], [sx - 0.12, fy + 0.02, sz - 0.05], 34),
        "b4_limit": ([sx + 0.08, T + 0.35, sz + 1.6], [sx - 0.17, T + 0.2, sz - 0.15], 34),
        "b5_push": ([sx + 0.14, fy + 0.08, sz + 1.35], [sx + 0.06, fy - 0.02, sz], 34),
        "b6_montage": ([sx + 0.0, T + 0.42, sz + 1.6], [sx + 0.2, T + 0.14, sz - 0.12], 34),
        "b7_ninth": ([sx + 0.1, fy + 0.03, sz + 1.15], [sx + 0.05, fy - 0.01, sz], 34),
        # Кодекс: широкоугольно по диагонали — Клод 9 спиной к нему на первом плане, Кодекс с чаем в глубине.
        "b8_kodex": ([-0.16, T + 0.22, 1.0], [0.4, T + 0.15, 0.03], 48),
        "b9_pencil": ([0.6, T + 0.3, 1.45], [0.66, T + 0.15, 0.06], 34),
        "b10_final": ([PAPER[0] + 0.02, T + 0.42, PAPER[2] + 1.1], [PAPER[0] - 0.02, T + 0.2, PAPER[2] - 0.25], 34),
        "cast": ([X_CAST + 0.0, T + 0.24, 2.9], [X_CAST + 0.0, T + 0.2, 0.0], 34),
    }
    keys = []
    for i, name in enumerate(SL):
        pos, tgt, fov = cam[name]
        k0 = {"t": i, "position": pos, "target": tgt, "fov": fov, "dof": 0.0}
        if i:
            k0["cut"] = True
        keys += [k0, {**k0, "t": i + 0.999, "cut": False}]
    overlays.append({"kind": "toast", "t0": slot["b4_limit"], "t1": slot["b4_limit"] + 0.999, "title": "Лимит исчерпан",
                     "text": "аккаунт 1 · попробуйте через 5 ч", "icon": "!", "y": 300})
    env = {
        "background": "#1d2233", "fog": False, "exposure": 0.95, "envIntensity": 0.0, "toneMapping": "neutral",
        "toon": {"preset": "ink", "outline": {"width": 5.5, "ref": 1.9}},
        "post": {"ao": 0, "bloom": {"strength": 0.2, "radius": 0.35, "threshold": 1.05}, "vignette": 0.34, "grain": 0.05, "ca": 0.2,
                 "grade": {"saturation": 1.06, "contrast": 1.05, "shadows": "#3b3466", "highlights": "#ffd9aa", "split": 0.3}},
    }
    spec = {"width": 1080, "height": 1920, "fps": 30, "duration": float(n), "environment": env, "lights": lights(),
            "objects": objs, "characters": chars, "camera": {"fov": 34, "keys": keys, "clamp": False}, "overlays": overlays,
            "quality": {"shadows": True, "shadowSize": 2048, "antialias": True}}
    times = [(i + 0.5, name) for i, name in enumerate(SL)]
    return spec, times


def cover_text(png: Path, out: Path, work: Path) -> None:
    """Обложка: огромное «ЭКОНОМИЯ» поверх кадра 0 — тем же компоновщиком, что и субтитры ролика."""
    from app.motion_render_comp import build_compositor_args, write_comp

    clip = work / "_cover_in.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(png), "-t", "0.5", "-r", "30", "-pix_fmt", "yuv420p",
                    "-c:v", "libx264", "-crf", "10", str(clip)], check=True)
    layers = [{"id": "hook", "type": "text", "start": 0.0, "end": 0.5, "x": 0.5, "y": 0.16,
               "props": {"text": HOOK, "font": "Montserrat", "weight": 900, "size": 150, "color": "#fff6e8", "align": "center",
                         "uppercase": True, "stroke": {"color": "#2b1810", "width": 16},
                         "bg": {"color": "#d8342c", "opacity": 1, "radius": 34, "padX": 44, "padY": 24}}}]
    comp = write_comp(work / "cover_comp.json", width=1080, height=1920, fps=30, duration=0.5, layers=layers, camera=[])
    (work / "cover_assets.json").write_text("{}", "utf-8")
    args = build_compositor_args(comp, work / "cover_assets.json", input_path=clip, frame=0.1, png_path=out)
    proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, check=False)
    if proc.returncode != 0 or not out.is_file():
        raise RuntimeError(f"обложка: {proc.stderr.strip()[-1200:]}")


LABELS = {
    "cover": "0 обложка", "b1_user": "1 юзер", "b2_shelf": "2 полка аккаунтов", "b3_work": "3 я работаю", "b4_limit": "4 лимит",
    "b5_push": "5 спихивает", "b6_montage": "6 нарезка, гора", "b7_ninth": "7 девятый", "b8_kodex": "8 Кодекс с чаем",
    "b9_pencil": "9 карандаш", "b10_final": "10 сэкономил", "cast": "каст",
}


def sheet(files: list[Path], labels: list[str], out: Path, cols: int = 4, w: int = 400) -> None:
    h = w * 16 // 9
    font = "C\\:/Windows/Fonts/arialbd.ttf"
    inputs: list[str] = []
    chains = []
    for i, f in enumerate(files):
        inputs += ["-i", str(f)]
        lab = labels[i].replace(":", "\\:").replace("'", "")
        chains.append(f"[{i}:v]scale={w}:{h},drawtext=fontfile='{font}':text='{lab}':x=10:y=10:fontsize=26:fontcolor=white:box=1:"
                      f"boxcolor=black@0.6:boxborderw=6[v{i}]")
    n = len(files)
    layout = "|".join(f"{(i % cols) * w}_{(i // cols) * h}" for i in range(n))
    graph = ";".join(chains) + ";" + "".join(f"[v{i}]" for i in range(n)) + f"xstack=inputs={n}:layout={layout}:fill=white[o]"
    subprocess.run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", graph, "-map", "[o]", "-frames:v", "1", "-q:v", "3",
                    str(out)], check=True)


def stills(out_dir: Path, gl: str, build: bool) -> Path:
    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    dst = out_dir / "stills"
    dst.mkdir(exist_ok=True)
    if build:
        P1._run(["npm", "run", "build:scene3d"], "сборка движка", cwd=ROOT, shell=sys.platform == "win32")
    spec, times = stills_spec()
    sp = work / "stills.json"
    sp.write_text(json.dumps(spec, ensure_ascii=False), "utf-8")
    prev = work / "stills_frames"
    shutil.rmtree(prev, ignore_errors=True)
    P1.wait_ram(4.0)
    r = P1._run(["node", str(ROOT / "node" / "scene3d" / "render.mjs"), "--spec", str(sp), "--packs", str(P1.PACKS), "--gl", gl,
                 "--workers", "1", "--preview", ",".join(f"{t:.2f}" for t, _ in times), "--preview-dir", str(prev)], "кадры")
    res = json.loads(r.stdout.strip().splitlines()[-1])
    if res.get("warnings"):
        print("предупреждения:", res["warnings"][:10])
    files = []
    for t, name in times:
        src = prev / ("frame_" + f"{t:.2f}".replace(".", "_") + ".png")
        f = dst / f"{name}.png"
        shutil.copy2(src, f)
        files.append(f)
    cover_text(files[0], dst / "cover_text.png", work)
    files[0] = dst / "cover_text.png"
    out = out_dir / "stills_sheet.jpg"
    sheet(files, [LABELS[n] for _, n in times], out)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["stills"])
    ap.add_argument("out_dir")
    ap.add_argument("--gl", default="gpu")
    ap.add_argument("--no-build", action="store_true")
    a = ap.parse_args()
    os.environ.setdefault("MOTION_PACKS_DIR", str(P1.PACKS))
    out = stills(Path(a.out_dir).resolve(), a.gl, not a.no_build)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
