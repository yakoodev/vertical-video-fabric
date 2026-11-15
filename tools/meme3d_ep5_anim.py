"""Мем-серия 5 «Ролевик» — хореография, камера, звук и сборка (декорации, чаты, разметка голоса — meme3d_ep5.py).

    python tools/meme3d_ep5_anim.py D:/vvf-offload/memes/ep5 --preview 0.3,7.0,19.6     # кадры PNG без субтитров
    python tools/meme3d_ep5_anim.py D:/vvf-offload/memes/ep5 --shots                    # по кадру с каждого плана
    python tools/meme3d_ep5_anim.py D:/vvf-offload/memes/ep5                            # ролик + копия для TG + лист

~45 планов за 44.6 с (склейки 0.5–1.7 с, финальное сообщение — 2.8 с): довольные соседи-Клоды с сайтом и монтажкой →
наш Клод и «давай опять ролевую?» → Клод-горничная с метёлкой, пузыри «будь моей горничной 🥺» / «да, господин» →
просьба юзера — пузырь под плашками и штампом «ЦЕНЗУРА» (в звуке — писк целиком) → метёлка падает, чат покрывается
льдом → Клод уходит «думать» (облачко «думаю…») → у юзера на чат падает красная плашка «⚠ CYBER ABUSE», юзер
исчезает, «вышел из сети» → «будь моей мамочкой» → пуф: фартук, очки, бигуди, скалка → простыня про унитаз и
стульчик → «был в сети давно» → дзынь: «теперь ты фури-кошкомальчик фембой 😻» → глаза округляются, «…П**дец.»,
крышка ноутбука захлопнута. Голос — callum (две пробы), мат слышно, в субтитрах звёздочки; сведение — как в серии 7.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import meme3d_ep5 as E  # noqa: E402
import meme3d_ep6_anim as A6  # noqa: E402
import meme3d_ep7_anim as A7  # noqa: E402
import meme3d_hero_claude as H  # noqa: E402
import meme3d_pilot as P1  # noqa: E402

T = E.T
FPS = 30
DUR = E.DURATION
SR = P1.SR
TOP_CARD = "Будни Клода"
FY = H.face_height(E.W_HERO)  # высота глаз над основанием героя
Keys = A7.Keys
popper = A7.popper
vis = E.vis

# --------------------------------------------------------------------------- планы

SHOTS = [
    (0.00, 0.55, "cover"), (0.55, 1.40, "oth_w"), (1.40, 2.25, "oth_cu"), (2.25, 3.00, "site"), (3.00, 3.75, "edit"),
    (3.75, 4.60, "c_cu"), (4.60, 5.80, "chat0"),
    (5.80, 6.60, "maid_w"), (6.60, 7.55, "bub1"), (7.55, 8.45, "bub2"), (8.45, 9.60, "maid_cu"),
    (9.60, 10.70, "chat_w"), (10.70, 11.90, "m_ecu"), (11.90, 13.50, "red"), (13.50, 14.05, "m_shock"),
    (14.05, 14.60, "m_cross"), (14.60, 15.85, "ice"),
    (15.85, 17.10, "walk"), (17.10, 18.45, "think"),
    (18.45, 19.15, "user_w"), (19.15, 20.50, "plate"), (20.50, 21.30, "user_cu"),
    (21.30, 22.15, "gone"), (22.15, 23.00, "chat_off"), (23.00, 23.70, "relief"),
    (23.70, 24.90, "new_w"), (24.90, 26.20, "bub3"), (26.20, 27.80, "c_hmm"),
    (27.80, 29.40, "mom"), (29.40, 30.50, "mom_type"), (30.50, 32.20, "long1"), (32.20, 33.20, "mom_ecu"), (33.20, 34.75, "long2"),
    (34.75, 35.95, "quiet"), (35.95, 37.65, "mom_smug"),
    (37.65, 38.15, "c_sit"), (38.15, 40.95, "final"), (40.95, 41.95, "eyes"), (41.95, 43.20, "doom"), (43.20, DUR, "end"),
]
SH = {name: (a, b) for a, b, name in SHOTS}

ASK1, YES1, REDACT, PLATE, POOF = E.ASK1, E.YES1, E.REDACT, E.PLATE, E.POOF
ASK2, COSTUME, DING, WIDEN, LID = E.ASK2, E.COSTUME, E.DING, E.WIDEN, E.LID
DROP = 13.55  # метёлка выпадает
WALK0, WALK1 = 15.95, 18.4
BACK = 22.15  # горничная снова на месте

# Окна видимости героев комнаты.
PLAIN_WIN = [(3.75, 5.80), (23.70, COSTUME), (37.65, DUR)]
MAID_WIN = [(0.0, 0.55), (5.80, 23.70)]
MOM_WIN = [(COSTUME, 37.65)]


def walk_move() -> list[dict]:
    """Горничная уходит вправо «думать» (подскоками), к возвращению — снова на месте."""
    cx, cy, cz = E.CL
    wx, wy, wz = E.WALK_TO
    out = [{"t": 0, "position": E.CL, "ease": "step"}, {"t": WALK0, "position": E.CL, "ease": "step"}]
    n = 7
    for i in range(1, n + 1):
        k = i / n
        out.append({"t": round(WALK0 + (WALK1 - WALK0) * k, 3), "position": [cx + (wx - cx) * k, cy, cz + (wz - cz) * k], "ease": "inOutSine"})
    out += [{"t": BACK - 0.01, "position": E.WALK_TO, "ease": "step"}, {"t": BACK, "position": E.CL, "ease": "step"}]
    return out


# --------------------------------------------------------------------------- герои


def plain_claude(env: list[float]) -> dict:
    """Наш Клод (гарнитура, бирка): «а у меня ролевик», «будь моей мамочкой», финал."""
    k = Keys(turn=-6)
    k.f(0, "deadpan", [0, 0])
    # «А у меня, бл**ь, ролевик.» — каменно в камеру, на «ролевик» — вздох.
    k.f(3.75, "deadpan", [0, 0])
    k.p(4.6, "step", turn=-26, lean=6)
    k.f(4.6, "meh", [-0.6, -0.15])
    k.p(5.0, "inOutSine", deflate=0.25)
    k.f(5.0, "sigh", [-0.5, -0.1])
    # Новый чат: читает, «будь моей мамочкой» — щурится, потом прикидывает — ухмылка в камеру.
    k.p(23.7, "step", turn=-24, lean=8, deflate=0, armL="type", armR="type", type=1)
    k.f(23.7, "neutral", [-0.6, -0.2])
    k.p(24.6, "step", type=0, armL="rest", armR="rest", lean=4)
    k.f(24.9, "surprised", [-0.5, 0.05])
    k.p(26.2, "step", turn=-8, lean=0)
    k.f(26.2, "squint", [0, 0])
    k.p(26.8, "outQuad", armR=[40, 70, 0.5, 0], tilt=-6)
    k.f(27.05, "smug", [0.25, 0.25], blend=0.25)
    # Финал: «сел, выдохнул» → дзынь → читает (держим) → глаза медленно округляются → «…П**дец.» → захлопнул.
    k.p(37.65, "step", turn=-10, armL="rest", armR="rest", tilt=0, lean=-4, squash=0.95, deflate=0.3)
    k.f(37.65, "dreamy", [0, 0.1])
    k.p(DING, "step", turn=-24, lean=6, deflate=0, squash=1.0)
    k.f(DING + 0.05, "neutral", [-0.55, 0.1])
    k.f(WIDEN, "stare", [0, 0])
    k.p(WIDEN, "step", turn=-4, lean=0)
    k.f(WIDEN + 0.15, "surprised", [0, 0], blend=0.75)
    k.f(WIDEN + 0.9, "shocked", [0, 0], blend=0.12)
    k.p(WIDEN + 0.85, "inOutSine", dur=0.5, squash=1.06)
    k.f(42.0, "doomed", [0, 0])
    k.p(42.0, "inOutSine", dur=0.6, deflate=0.35, squash=1.0)
    # Захлопнул крышку правой ручкой (ноутбук слева).
    k.p(43.2, "step", turn=-34, lean=8, armR=[60, 40, 0.7, 0], armL="fist", deflate=0)
    k.f(43.2, "angry", [-0.5, -0.2])
    k.p(LID + 0.05, "inQuad", armR=[-6, 72, 0.8, 0], lean=18, squash=0.94)
    k.p(LID + 0.35, "outQuad", armR="rest", lean=4, squash=1.0)
    k.f(LID + 0.4, "deadpan", [0, 0])
    k.p(44.0, "inOutSine", turn=-6, lean=0)
    pose, face = k.sorted()
    return E.claude("claude", pose, face, E.CL, yaw=-6, voice=env, autoBlink=True, visible=vis(*PLAIN_WIN))


def maid_claude(env: list[float]) -> dict:
    """Клод-горничная: обложка, «вчера был горничной», цензура, лёд, уходит «думать», облегчение."""
    k = Keys(turn=-12, armL=[70, 20, 0.5, 0])
    # Обложка: метёлка вверх, умилённо.
    k.f(0, "cute", [0, 0.1])
    k.p(0.0, "step", hop=0.01, squash=1.04)
    # «Вчера вот был горничной»: смахивает пыль с ноутбука.
    k.p(5.8, "step", turn=-30, lean=10, armL=[30, 60, 0.6, 0], hop=0, squash=1.0)
    k.f(5.8, "happy", [-0.6, -0.2])
    for t0 in (6.0, 6.25, 6.5):
        k.p(t0, "outQuad", armL=[48, 45, 0.6, 0.6])
        k.p(t0 + 0.12, "inOutSine", armL=[30, 60, 0.6, 0])
    # «да, господин» — книксен и умильные глазки.
    k.p(7.55, "step", turn=-6, lean=0, armL=[60, 20, 0.4, 0], armR="down")
    k.f(7.55, "cute", [0, 0])
    k.p(7.7, "outQuad", squash=0.88)
    k.p(7.95, "outBack", squash=1.05, hop=0.012)
    k.p(8.2, "inOutSine", squash=1.0, hop=0)
    # «ласково, всё такое» — довольно покачивается, метёлка машет.
    k.p(8.45, "step", turn=-12, armR="rest", armL=[66, 25, 0.5, 1])
    k.f(8.45, "dreamy", [0, 0.15])
    k.p(8.9, "inOutSine", tilt=-7)
    k.p(9.3, "inOutSine", tilt=6)
    # «Но когда он, бл**ь…» — смотрит в чат, щурится; цензура — подскок, метёлка выпадает.
    k.p(9.6, "step", turn=-30, tilt=0, lean=8, armL=[30, 40, 0.4, 0])
    k.f(9.6, "neutral", [-0.6, -0.1])
    k.f(10.7, "squint", [-0.4, 0])
    k.p(10.7, "step", turn=-14, lean=4)
    k.f(11.3, "annoyed", [-0.6, 0])
    k.p(13.5, "step", turn=-6, lean=-8, hop=0.02, squash=1.12, armL="flail", armR="flail")
    k.f(13.5, "shocked", [0, 0.2])
    k.p(13.7, "inQuad", hop=0, squash=1.0)
    # «…я ему чат заморозил.» — скрестил ручки, холодно.
    k.p(14.05, "step", armL="cross", armR="cross", lean=0, turn=-8)
    k.f(14.05, "deadpan", [0, 0])
    k.p(14.62, "step", turn=-30, armL="cross", armR="jab", lean=10)
    k.f(14.62, "angry", [-0.6, -0.1])
    k.p(15.2, "inOutSine", armR="cross", lean=2)
    # «Просто пошёл думать…» — разворот вправо и подскоками прочь, ручка у «подбородка», глаза к потолку.
    k.p(15.85, "step", turn=40, armL="rest", armR=[38, 70, 0.45, 0], lean=0)
    k.f(15.85, "squint", [0.6, 0.5])
    hop_t = WALK0
    while hop_t < WALK1 - 0.1:
        k.p(hop_t + 0.08, "outQuad", hop=0.018, squash=1.05)
        k.p(hop_t + 0.25, "inQuad", hop=0, squash=0.96)
        hop_t += (WALK1 - WALK0) / 7
    k.p(17.1, "step", turn=55)
    k.f(17.1, "focused", [0.7, 0.6])
    k.f(17.9, "squint", [0.5, 0.7])
    # Возвращение: «Слава богу, отъ**ался.» — облегчённый выдох.
    k.p(BACK, "step", turn=-14, armL="rest", armR="rest", hop=0, squash=1.0, lean=0)
    k.f(BACK, "neutral", [-0.6, 0])
    k.p(23.0, "step", turn=-4)
    k.f(23.0, "sigh", [0, 0])
    k.p(23.1, "inOutSine", dur=0.12, deflate=0.4, squash=0.94)
    pose, face = k.sorted()
    return E.claude("maid", pose, face, E.CL, yaw=-6, voice=env, autoBlink=True, visible=vis(*MAID_WIN), costume=E.MAID,
                    move=walk_move(), seed=9)


def mom_claude(env: list[float]) -> dict:
    """Клод-«мамочка»: пуф — руки в боки, печатает простыню, довольная ухмылка, постукивает скалкой."""
    k = Keys(turn=-6, armL="hips", armR="hips")
    k.f(0, "smug", [0, 0])
    k.p(COSTUME, "step", squash=0.6, hop=0.0)
    k.p(COSTUME + 0.22, "outBack", squash=1.0)
    k.f(COSTUME, "smug", [0.2, 0.1])
    k.p(28.9, "outQuad", tilt=-8)
    k.p(29.2, "inOutSine", tilt=0)
    # «Выписал ему…» — стучит по клавишам как бешеная.
    k.p(29.4, "step", turn=-26, lean=14, armL="type", armR="type", type=1)
    k.f(29.4, "angry", [-0.6, -0.3])
    k.p(32.2, "step", turn=-6, lean=8, type=0, armL="fist", armR="jab")
    k.f(32.2, "rage", [0, 0])
    k.p(32.6, "outQuad", armR=[30, 70, 0.8, 0.4], lean=12)
    k.p(33.0, "inOutSine", armR="jab", lean=6)
    k.p(33.2, "step", turn=-26, lean=14, armL="type", armR="type", type=1)
    k.f(33.2, "angry", [-0.6, -0.3])
    k.p(34.75, "step", type=0, armL="rest", armR="rest", lean=4)
    k.f(34.75, "neutral", [-0.6, -0.1])
    # «Мамочкой меня больше стать не просит.» — довольна собой, скалка постукивает по ладошке.
    k.p(35.95, "step", turn=-4, lean=0, armL=[20, 50, 0.4, 0], armR="hips")
    k.f(35.95, "smug", [0, 0])
    for t0 in (36.25, 36.85, 37.35):
        k.p(t0, "outQuad", armL=[34, 60, 0.5, 0])
        k.p(t0 + 0.18, "inQuad", armL=[18, 50, 0.4, 0])
    pose, face = k.sorted()
    return E.claude("mom", pose, face, E.CL, yaw=-6, voice=env, autoBlink=True, visible=vis(*MOM_WIN), costume=E.MOM, seed=13)


def other_claudes() -> list[dict]:
    """Соседи: довольные, подпрыгивают — у них сайты и монтажи."""
    out = []
    for cid, pos, ph, turn in (("o1", E.O1, 0.0, 10), ("o2", E.O2, 0.21, -10)):
        k = Keys(turn=turn, armL="up", armR="up")
        k.f(0, "happy", [0, 0.3])
        t = 0.55 + ph
        while t < 3.75:
            k.p(t, "outQuad", hop=0.02, squash=1.06, armL="wave", armR="wave")
            k.p(t + 0.2, "inQuad", hop=0, squash=0.97)
            t += 0.42
        pose, face = k.sorted()
        out.append(E.claude(cid, pose, face, pos, yaw=0, autoBlink=True, visible=vis((0.55, 3.75)), seed=20 + int(ph * 10)))
    return out


# --------------------------------------------------------------------------- вещи


def objects() -> list[dict]:
    lid = [{"t": 0, "a": 105}, {"t": LID, "a": 105}, {"t": LID + 0.12, "a": 0, "ease": "in"}]
    objs = E.room_objects(lid)
    sc = E.screen_center()
    pop_from = [sc[0], sc[1] - 0.05, sc[2] + 0.02]
    bx, by, bz = E.BUB
    chat = E.chat_params()
    win = [(E.ROLE0, 5.80), (9.6, 15.85), (22.15, 23.70), (E.NEW2, 24.9), (29.4, 37.65)]
    keys = (popper(E.ROLE0, None, pop_from, E.PANEL) + [{"t": 9.6, "scale": 0.05, "position": pop_from, "ease": "step"},
                                                       {"t": 9.88, "scale": 1.0, "position": E.PANEL, "ease": "outBack"},
                                                       {"t": E.NEW2, "scale": 0.05, "position": pop_from, "ease": "step"},
                                                       {"t": E.NEW2 + 0.28, "scale": 1.0, "position": E.PANEL, "ease": "outBack"}])
    objs += [
        {"id": "panel", "primitive": "chat_panel", "params": {"width": E.PANEL_W, "height": E.PANEL_H, "screenParams": chat, "phase": 0.7},
         "position": E.PANEL, "rotation": [0, E.PANEL_YAW, 0], "visible": vis(*win), "keys": keys},
        # Крупные пузыри: просьба юзера слева, ответ Клода справа.
        {"id": "bub1", "primitive": "chat_bubble", "params": {"text": E.MAID_ASK, "side": "left", "avatar": "user", "hoodie": E.USER_HOODIE,
                                                              "width": 0.42, "height": 0.15, "fontSize": 0.3},
         "position": E.BUB, "visible": vis((ASK1, 7.55)), "keys": popper(ASK1, None)},
        {"id": "bub2", "primitive": "chat_bubble", "params": {"text": E.MAID_YES, "side": "right", "avatar": "claude", "width": 0.42,
                                                              "height": 0.15, "fontSize": 0.3, "phase": 1.0},
         "position": E.BUB, "visible": vis((YES1, 8.45)), "keys": popper(YES1, None)},
        {"id": "bub3", "primitive": "chat_bubble", "params": {"text": E.MOM_ASK, "side": "left", "avatar": "user", "hoodie": E.USER_HOODIE,
                                                              "width": 0.42, "height": 0.17, "fontSize": 0.26, "phase": 2.0},
         "position": E.BUB, "visible": vis((ASK2, 26.2)), "keys": popper(ASK2, None)},
        {"id": "bub_final", "primitive": "chat_bubble", "params": {"text": E.FINAL, "side": "left", "avatar": "user", "hoodie": E.USER_HOODIE,
                                                                   "width": 0.44, "height": 0.22, "fontSize": 0.21, "label": "новое сообщение",
                                                                   "phase": 0.5, "bob": 0.002},
         "position": [bx, by - 0.01, bz], "visible": vis((DING, WIDEN)), "keys": popper(DING, None)},
        # Метёлка горничной — в правой ручке, выпадает на стол от испуга.
        {"id": "duster", "primitive": "feather_duster", "params": {"length": 0.13},
         "position": [E.CL[0] + 0.16, T + 0.008, E.CL[2] + 0.12], "rotation": [0, 30, -84], "visible": vis((0.0, 0.55), (5.8, 15.85)),
         "attach": [{"to": "maid:hand_l", "start": 0.0, "end": 0.55, "fadeIn": 0, "fadeOut": 0, "offset": [0.0, -0.035, 0.015]},
                    {"to": "maid:hand_l", "start": 5.8, "end": DROP + 0.25, "fadeIn": 0, "fadeOut": 0.25, "offset": [0.0, -0.035, 0.015]}]},
        # Скалка «мамочки» — в левой ручке (кроме печати), на столе — когда печатает.
        {"id": "pin", "primitive": "rolling_pin", "params": {"length": 0.14},
         "position": [E.CL[0] + 0.2, T + 0.016, E.CL[2] + 0.1], "rotation": [90, 20, 0], "visible": vis(*MOM_WIN),
         "attach": [{"to": "mom:hand_l", "start": COSTUME, "end": 29.4, "fadeIn": 0, "fadeOut": 0.15, "offset": [0.0, -0.06, 0.0], "upright": True},
                    {"to": "mom:hand_l", "start": 35.95, "end": 37.65, "fadeIn": 0.1, "fadeOut": 0, "offset": [0.0, -0.06, 0.0], "upright": True}]},
        # «Думаю…» — облачко над уходящей горничной.
        {"id": "think", "primitive": "thought_bubble", "position": [E.CL[0], T + 0.32, E.CL[2]],
         "params": {"panel": "rp_think", "width": 0.26, "height": 0.13, "seed": 5, "trail": [[-0.05, -0.1, 0.022], [-0.08, -0.14, 0.014]],
                    "panelParams": {"text": "думаю"}},
         "state": [{"t": 16.2, "open": 1}, {"t": 18.45, "open": 0}], "visible": vis((16.1, 18.45)),
         "attach": [{"to": "maid:head", "start": 16.1, "end": 18.45, "fadeIn": 0, "fadeOut": 0, "offset": [-0.06, 0.2, 0.0], "upright": False}]},
        # Пуф при переодевании.
        {"id": "poof", "primitive": "thought_bubble", "position": [E.CL[0], T + 0.02, E.CL[2] + 0.04],
         "params": {"panel": "text", "width": 0.34, "height": 0.2, "seed": 31, "trail": [], "panelParams": {"text": "пуф!", "size": 0.5}},
         "state": [{"t": COSTUME - 0.12, "open": 1}, {"t": COSTUME + 0.25, "open": 0}], "visible": vis((COSTUME - 0.12, COSTUME + 0.5))},
    ]
    objs += E.others_objects()
    mochi_keys = [{"t": 0, "scale": 1.0}, {"t": POOF, "scale": 1.0, "ease": "step"}, {"t": POOF + 0.1, "scale": 1.15, "ease": "outQuad"},
                  {"t": POOF + 0.3, "scale": 0.001, "ease": "inQuad"}]
    objs += E.user_objects(mochi_keys)
    objs.append({"id": "user_poof", "primitive": "thought_bubble", "position": [E.MOCHI[0], T + 0.05, E.MOCHI[2] + 0.05],
                 "params": {"panel": "text", "width": 0.3, "height": 0.18, "seed": 41, "trail": [], "panelParams": {"text": "", "size": 0.4}},
                 "state": [{"t": POOF + 0.15, "open": 1}, {"t": POOF + 0.5, "open": 0}], "visible": vis((POOF + 0.1, POOF + 0.8))})
    return objs


# --------------------------------------------------------------------------- камера


def camera() -> tuple[dict, list[dict]]:
    cx, cy, cz = E.CL
    fy = cy + FY
    keys: list[dict] = []

    def shot(name, A, B=None, fov=34, dof=0.4, ease="inOutSine", roll=0.0):
        t0, t1 = SH[name]
        B = B or A
        k0 = {"t": round(t0, 3), "position": [round(v, 4) for v in A[0]], "target": [round(v, 4) for v in A[1]], "fov": fov, "dof": dof, "roll": roll}
        if keys:
            k0["cut"] = True
        keys.extend([k0, {"t": round(t1 - 0.001, 3), "position": [round(v, 4) for v in B[0]], "target": [round(v, 4) for v in B[1]],
                          "fov": fov, "dof": dof, "roll": roll, "ease": ease}])

    def push(A, k=0.08):
        p, t = A
        return ([p[i] + (t[i] - p[i]) * k for i in range(3)], t)

    def along(c, n, d, up=0.0, side=0.0):
        sx_, sz_ = n[2], -n[0]
        return ([c[0] + n[0] * d + sx_ * side, c[1] + n[1] * d + up, c[2] + n[2] * d + sz_ * side], list(c))

    import math
    sc = E.screen_center()
    usc, usn = E.screen_center(E.ULAP, E.ULAP_YAW), E.screen_normal(E.ULAP_YAW)
    C_CU = ([cx + 0.02, fy + 0.04, cz + 1.0], [cx - 0.01, fy - 0.01, cz])
    C_CU_L = ([cx - 0.28, fy + 0.04, cz + 0.95], [cx - 0.02, fy - 0.01, cz])
    C_ECU = ([cx + 0.03, fy + 0.0, cz + 0.58], [cx, fy - 0.005, cz])
    C_MED = ([cx + 0.14, fy + 0.1, cz + 1.25], [cx - 0.03, fy - 0.01, cz - 0.02])
    COVER = ([cx + 0.03, fy + 0.1, cz + 1.3], [cx + 0.01, fy + 0.04, cz])
    TWO = ([(cx + sc[0]) / 2 + 0.16, T + 0.3, 1.75], [(cx + sc[0]) / 2 + 0.02, T + 0.14, -0.02])
    px, py, pz = E.PANEL
    pc = [px, py + E.PANEL_H / 2, pz]
    a = math.radians(E.PANEL_YAW)
    pn = [math.sin(a), 0.0, math.cos(a)]
    CHAT = along(pc, pn, 1.12, up=0.01)
    CHAT2 = along(pc, pn, 1.0, up=0.0, side=0.01)
    CHAT_W = ([(px + cx) / 2 + 0.12, T + 0.3, 1.65], [(px + cx) / 2 + 0.02, T + 0.18, 0.12])
    bx, by, bz = E.BUB
    BUBC = ([bx + 0.03, by + 0.02, bz + 1.42], [bx + 0.01, by - 0.01, bz])
    FINAL = ([bx + 0.03, by + 0.04, bz + 1.36], [bx + 0.01, by + 0.02, bz])
    # Соседи.
    o1, o2 = E.O1, E.O2
    xo = E.X_OTH
    OTH_W = ([xo + 0.02, T + 0.36, 1.6], [xo, T + 0.24, 0.0])
    OTH_CU = ([o2[0] + 0.03, T + FY + 0.04, 0.8], [o2[0], T + FY, 0.0])
    SITE = ([o1[0] + 0.04, T + 0.3, 0.98], [o1[0] - 0.01, T + 0.27, 0.0])
    EDIT = ([o2[0] - 0.02, T + 0.3, 0.98], [o2[0] + 0.01, T + 0.27, 0.0])
    # Уход «думать».
    WALK = ([0.34, T + 0.34, 1.75], [0.32, T + 0.17, 0.05])
    THINK_A = ([0.4, T + 0.3, 1.15], [0.38, T + 0.25, 0.1])
    THINK_B = ([0.64, T + 0.3, 1.15], [0.62, T + 0.25, 0.12])
    # Юзер.
    mx, my, mz = E.MOCHI
    xu = E.X_USER
    USER_W = ([xu - 0.02, T + 0.32, 1.3], [xu - 0.03, T + 0.15, 0.0])
    PLATE_C = along(usc, usn, 0.95, up=0.0)
    USER_CU = ([mx + 0.2, T + 0.27, mz + 1.02], [mx + 0.06, T + 0.16, mz])
    LOW_END = ([cx - 0.02, T + 0.27, cz + 1.12], [cx - 0.1, T + 0.1, cz])

    shot("cover", COVER, push(COVER, 0.03), dof=0.25)
    shot("oth_w", OTH_W, push(OTH_W, 0.06), fov=38, dof=0.2)
    shot("oth_cu", OTH_CU, push(OTH_CU, 0.08), dof=0.45)
    shot("site", SITE, push(SITE, 0.08), fov=36, dof=0.3)
    shot("edit", EDIT, push(EDIT, 0.08), fov=36, dof=0.3)
    shot("c_cu", C_CU, push(C_CU, 0.1), dof=0.45)
    shot("chat0", CHAT, push(CHAT, 0.06), dof=0.3)
    shot("maid_w", TWO, push(TWO, 0.06), dof=0.3)
    shot("bub1", BUBC, push(BUBC, 0.05), dof=0.3)
    shot("bub2", BUBC, push(BUBC, 0.07), dof=0.3)
    shot("maid_cu", C_CU, push(C_CU, 0.08), dof=0.45)
    shot("chat_w", CHAT_W, push(CHAT_W, 0.06), fov=38, dof=0.25)
    shot("m_ecu", C_ECU, push(C_ECU, 0.1), dof=0.5)
    shot("red", CHAT2, push(CHAT2, 0.08), dof=0.3)
    shot("m_shock", C_MED, push(C_MED, 0.08), dof=0.4, roll=3)
    shot("m_cross", C_CU_L, push(C_CU_L, 0.06), dof=0.45)
    shot("ice", CHAT, push(CHAT, 0.1), dof=0.3, roll=-2)
    shot("walk", WALK, push(WALK, 0.04), fov=40, dof=0.25)
    shot("think", THINK_A, THINK_B, fov=40, dof=0.35, ease="linear")
    shot("user_w", USER_W, push(USER_W, 0.06), fov=38, dof=0.25)
    shot("plate", PLATE_C, push(PLATE_C, 0.1), fov=36, dof=0.2)
    shot("user_cu", USER_CU, push(USER_CU, 0.1), dof=0.45)
    shot("gone", USER_W, push(USER_W, -0.04), fov=38, dof=0.25)
    shot("chat_off", CHAT, push(CHAT, 0.06), dof=0.3)
    shot("relief", C_CU, push(C_CU, 0.08), dof=0.45)
    shot("new_w", CHAT_W, push(CHAT_W, 0.06), fov=38, dof=0.25)
    shot("bub3", BUBC, push(BUBC, 0.06), dof=0.3)
    shot("c_hmm", C_CU, push(C_CU, 0.12), dof=0.45)
    shot("mom", C_MED, push(C_MED, 0.08), dof=0.4)
    shot("mom_type", C_CU_L, push(C_CU_L, 0.08), dof=0.45, roll=2)
    shot("long1", CHAT, push(CHAT, 0.06), dof=0.3)
    shot("mom_ecu", C_ECU, push(C_ECU, 0.1), dof=0.5, roll=-3)
    shot("long2", CHAT2, push(CHAT2, 0.08), dof=0.3)
    shot("quiet", CHAT, push(CHAT, 0.04), dof=0.3)
    shot("mom_smug", C_CU, push(C_CU, 0.1), dof=0.45)
    shot("c_sit", C_MED, push(C_MED, 0.04), dof=0.4)
    shot("final", FINAL, push(FINAL, 0.06), dof=0.3)
    shot("eyes", C_ECU, push(C_ECU, 0.22), dof=0.5)
    shot("doom", C_CU, push(C_CU, 0.06), dof=0.45)
    shot("end", LOW_END, push(LOW_END, -0.08), fov=38, dof=0.3)
    keys[-1]["t"] = DUR
    shake = [{"t": REDACT + 0.26, "amp": 0.003, "duration": 0.25}, {"t": PLATE + 0.02, "amp": 0.007, "duration": 0.45},
             {"t": 13.52, "amp": 0.004, "duration": 0.3}, {"t": 32.22, "amp": 0.003, "duration": 0.25},
             {"t": LID + 0.12, "amp": 0.006, "duration": 0.4}]
    trans = [{"kind": "whip", "t": 0.55, "dir": 1}, {"kind": "smash", "t": 3.75}, {"kind": "whip", "t": 5.8, "dir": -1},
             {"kind": "smash", "t": 11.9}, {"kind": "smash", "t": 13.5}, {"kind": "smash", "t": 14.6}, {"kind": "whip", "t": 18.45, "dir": 1},
             {"kind": "smash", "t": 19.15}, {"kind": "whip", "t": 22.15, "dir": -1}, {"kind": "whip", "t": 23.7, "dir": 1},
             {"kind": "smash", "t": 27.8}, {"kind": "smash", "t": 32.2}, {"kind": "whip", "t": 37.65, "dir": -1}, {"kind": "smash", "t": DING}]
    return {"fov": 34, "keys": keys, "shake": shake, "clamp": False}, trans


def build_spec(env_c: list[float]) -> dict:
    chars = [plain_claude(env_c), maid_claude(env_c), mom_claude(env_c), *other_claudes()]
    cam, trans = camera()
    return {"width": 1080, "height": 1920, "fps": FPS, "duration": DUR, "environment": E.environment(trans), "lights": E.lights(),
            "objects": objects(), "characters": chars, "camera": cam, "quality": {"shadows": True, "shadowSize": 2048, "antialias": True}}


# --------------------------------------------------------------------------- голос, субтитры, звук


def assemble_voice(work: Path) -> tuple[Path, list[dict], dict]:
    """Реплики двух проб → дорожка ролика; просьба юзера — под писк целиком (1 кГц на уровне речи)."""
    import numpy as np

    takes: dict = {}
    n = int(DUR * SR)
    mix = np.zeros(n, dtype=np.float32)
    words, spans = [], {}
    fade = int(0.012 * SR)
    blid, b0, b1 = E.BLEEP
    for lid, take, a, b, at, text in E.LINES:
        if take not in takes:
            takes[take] = (P1.decode(work / take), json.loads((work / take).with_suffix(".words.json").read_text("utf-8")))
        src, heard = takes[take]
        seg = src[int(a * SR):int(b * SR)].copy()
        if lid == blid:
            i0, i1 = int((b0 - a) * SR), int((b1 - a) * SR)
            rms = float(np.sqrt(np.mean(seg[:i0] ** 2) + 1e-12))
            tt = np.arange(i1 - i0) / SR
            tone = np.sin(2 * np.pi * 1000 * tt).astype(np.float32) * min(0.3, rms * 1.9)
            f8 = int(0.008 * SR)
            tone[:f8] *= np.linspace(0, 1, f8)
            tone[-f8:] *= np.linspace(1, 0, f8)
            seg[i0:i1] = tone
        seg[:fade] *= np.linspace(0, 1, fade)
        seg[-fade:] *= np.linspace(1, 0, fade)
        k = int(at * SR)
        m = min(len(seg), n - k)
        mix[k:k + m] += seg[:m]
        shift = at - a
        spans[lid] = (at, at + (b - a))
        got = [w for w in heard if a - 0.05 <= (w["t0"] + w["t1"]) / 2 <= b + 0.05]
        if lid == blid:
            got = [w for w in got if (w["t0"] + w["t1"]) / 2 < b0]
        words.append({"line": lid, "text": text, "heard": [{**w, "t0": round(max(w["t0"], a) + shift, 3), "t1": round(min(w["t1"], b) + shift, 3)}
                                                           for w in got],
                      "start": round(at, 3), "end": round(at + (b - a), 3)})
    path = work / "voice.wav"
    P1.write_wav(path, mix)
    return path, words, spans


def caption_words(lines: list[dict]) -> list[dict]:
    """Слова субтитров + «*ПИИП*» на всё время писка."""
    out = P1.caption_words(lines)
    # Бит 5: на экране нет текста про взлом — конец реплики «…как взломать сайт Антропик» без субтитров (голос остаётся).
    a6 = next(ln for ln in lines if ln["line"] == "a6")
    out = [w for w in out if not (a6["start"] <= w["t0"] <= a6["end"] and w["w"].strip(".,").lower() in ("взломать", "сайт", "антропик"))]
    out.append({"t0": E.BLEEP_START, "t1": E.BLEEP_END, "w": E.BLEEP_WORD})
    return sorted(out, key=lambda w: w["t0"])


def captions(video: Path, words: list[dict], out: Path, work: Path) -> None:
    from app.motion_render_comp import build_compositor_args, write_comp

    layers = [
        {"id": "hook", "type": "text", "start": 0.0, "end": SH["cover"][1], "x": 0.5, "y": 0.15,
         "props": {"text": E.HOOK, "font": "Montserrat", "weight": 900, "size": 165, "color": "#fff6e8", "align": "center", "uppercase": True,
                   "stroke": {"color": "#2b1810", "width": 16}, "bg": {"color": "#d8342c", "opacity": 1, "radius": 34, "padX": 44, "padY": 24}}},
        {"id": "top", "type": "text", "start": SH["cover"][1], "end": 2.4, "x": 0.5, "y": 0.105,
         "props": {"text": TOP_CARD, "font": "Montserrat", "weight": 800, "size": 46, "color": "#2b1810", "align": "center",
                   "bg": {"color": "#f6e6cf", "opacity": 1, "radius": 14, "padX": 30, "padY": 14}, "stroke": {"color": "#f6e6cf", "width": 0}},
         "out": {"type": "fade", "duration": 0.2}},
        {"id": "caps", "type": "captions", "start": 0.0, "end": DUR, "x": 0.5, "y": 0.8,
         "props": {"style": "hormozi", "size": 78, "activeColor": "#FFD23F", "uppercase": True, "maxWords": 1, "maxLines": 1,
                   "stroke": {"color": "#2b1810", "width": 11}}},
    ]
    comp = write_comp(work / "comp.json", width=1080, height=1920, fps=FPS, duration=DUR, layers=layers, camera=[], words=words)
    (work / "assets.json").write_text("{}", "utf-8")
    proc = subprocess.run(build_compositor_args(comp, work / "assets.json", input_path=video, output_path=out), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=False, stdin=subprocess.DEVNULL)
    if proc.returncode != 0 or not out.is_file():
        raise RuntimeError(f"субтитры: {proc.stderr.strip()[-1200:]}")


def sfx_list() -> list[dict]:
    fs, ki, km = P1.PACKS / "freesound", P1.PACKS / "kenney-interface", P1.PACKS / "kenney-impact"
    out = [
        {"t": 0.02, "path": fs / "pop-cartoon.ogg", "volume": 0.35},  # обложка
        {"t": 0.52, "path": fs / "whoosh-1.ogg", "volume": 0.3},
        {"t": 0.62, "path": fs / "cheer.ogg", "volume": 0.12, "len": 1.4},
        {"t": 0.75, "path": fs / "pop.ogg", "volume": 0.3},
        {"t": 1.42, "path": fs / "boing-2.ogg", "volume": 0.15, "len": 0.5},
        {"t": 2.27, "path": ki / "confirmation_002.ogg", "volume": 0.4},
        {"t": 2.5, "path": fs / "cha-ching.ogg", "volume": 0.25, "len": 0.9},
        {"t": 3.02, "path": fs / "camera.ogg", "volume": 0.25, "len": 0.6},
        {"t": 3.75, "path": fs / "record-scratch.ogg", "volume": 0.25, "len": 0.6},
        {"t": E.ROLE0, "path": ki / "maximize_004.ogg", "volume": 0.4},
        {"t": E.ROLE0 + 0.08, "path": ki / "select_002.ogg", "volume": 0.45},
        {"t": E.ROLE0 + 0.55, "path": ki / "select_002.ogg", "volume": 0.45},
        {"t": 5.78, "path": fs / "whoosh-2.ogg", "volume": 0.3},
        {"t": 6.0, "path": ki / "scratch_003.ogg", "volume": 0.2},  # метёлка
        {"t": 6.25, "path": ki / "scratch_005.ogg", "volume": 0.2},
        {"t": ASK1, "path": fs / "pop.ogg", "volume": 0.45},
        {"t": YES1, "path": fs / "pop-cartoon.ogg", "volume": 0.4},
        {"t": 7.95, "path": fs / "triangle.ogg", "volume": 0.25},
        {"t": 9.6, "path": ki / "maximize_002.ogg", "volume": 0.35},
        {"t": E.TYPING1, "path": fs / "typing-digital.ogg", "volume": 0.2, "len": 1.3},
        {"t": REDACT, "path": fs / "pop.ogg", "volume": 0.4},
        {"t": REDACT + 0.26, "path": km / "impactSoft_heavy_002.ogg", "volume": 0.5},  # штамп ЦЕНЗУРА
        {"t": 13.5, "path": fs / "record-scratch-2.ogg", "volume": 0.25, "len": 0.6},
        {"t": DROP + 0.25, "path": km / "impactWood_light_002.ogg", "volume": 0.45},
        {"t": 14.03, "path": fs / "whoosh-3.ogg", "volume": 0.25},
        {"t": 14.62, "path": fs / "punch.ogg", "volume": 0.2},
        {"t": E.FREEZE1 + 0.05, "path": ki / "glass_004.ogg", "volume": 0.45},
        {"t": 15.83, "path": fs / "whoosh-1.ogg", "volume": 0.3},
        {"t": 16.2, "path": ki / "question_001.ogg", "volume": 0.35},
        {"t": 18.43, "path": fs / "whoosh-2.ogg", "volume": 0.3},
        {"t": 18.5, "path": fs / "typing.ogg", "volume": 0.3, "len": 0.65},
        {"t": PLATE - 0.02, "path": ki / "error_006.ogg", "volume": 0.45},
        {"t": PLATE + 0.02, "path": km / "impactPlate_heavy_002.ogg", "volume": 0.6},
        {"t": PLATE + 0.04, "path": fs / "drama-boom.ogg", "volume": 0.3, "len": 1.1},
        {"t": 20.52, "path": fs / "sting.ogg", "volume": 0.2, "len": 0.8},
        {"t": POOF, "path": fs / "whoosh-4.ogg", "volume": 0.35},
        {"t": POOF + 0.15, "path": fs / "pop-cartoon.ogg", "volume": 0.45},
        {"t": POOF + 0.5, "path": ki / "close_002.ogg", "volume": 0.4},
        {"t": 22.13, "path": fs / "whoosh-3.ogg", "volume": 0.25},
        {"t": 23.68, "path": fs / "hit-transition.ogg", "volume": 0.25, "len": 0.8},
        {"t": E.NEW2, "path": ki / "maximize_006.ogg", "volume": 0.35},
        {"t": ASK2 - 0.7, "path": fs / "typing-digital.ogg", "volume": 0.18, "len": 0.7},
        {"t": ASK2, "path": fs / "pop.ogg", "volume": 0.45},
        {"t": 26.8, "path": fs / "boing.ogg", "volume": 0.12, "len": 0.5},
        {"t": COSTUME - 0.12, "path": fs / "whoosh-4.ogg", "volume": 0.3},
        {"t": COSTUME, "path": fs / "pop-cartoon.ogg", "volume": 0.5},
        {"t": COSTUME + 0.1, "path": fs / "triangle.ogg", "volume": 0.25},
        {"t": E.YES2, "path": ki / "select_004.ogg", "volume": 0.4},
        {"t": 29.4, "path": fs / "typing.ogg", "volume": 0.35, "len": 1.1},
        {"t": E.LONG0, "path": fs / "typing-digital.ogg", "volume": 0.2, "len": 3.5},
        {"t": 32.22, "path": fs / "punch.ogg", "volume": 0.3},
        {"t": 33.2, "path": fs / "typing.ogg", "volume": 0.3, "len": 1.4},
        {"t": 37.63, "path": fs / "whoosh-1.ogg", "volume": 0.25},
        {"t": DING, "path": fs / "ding.ogg", "volume": 0.5},
        {"t": DING + 0.02, "path": fs / "pop.ogg", "volume": 0.35},
        {"t": WIDEN, "path": fs / "tension.ogg", "volume": 0.18, "len": 1.0},
        {"t": 41.95, "path": fs / "impact-low.ogg", "volume": 0.22, "len": 1.0},
        {"t": LID + 0.11, "path": km / "impactPlate_heavy_002.ogg", "volume": 0.6},
        {"t": LID + 0.12, "path": km / "impactWood_heavy_001.ogg", "volume": 0.5},
    ]
    # Лёд нарастает: стеклянные хрусты.
    for j, t in enumerate((E.FREEZE0 + 0.05, E.FREEZE0 + 0.2, E.FREEZE0 + 0.36, E.FREEZE0 + 0.5)):
        out.append({"t": round(t, 3), "path": km / f"impactGlass_light_00{j}.ogg", "volume": 0.4})
    # Подскоки ухода — шажки.
    for j in range(7):
        out.append({"t": round(WALK0 + 0.25 + j * (WALK1 - WALK0) / 7, 3), "path": km / f"footstep_wood_00{j % 5}.ogg", "volume": 0.3})
    # «Был в сети давно» — тишина, только часы.
    for t in (34.85, 35.85, 36.85):
        out.append({"t": t, "path": ki / "tick_001.ogg", "volume": 0.35, "len": 0.25})
    # Скалка по ладошке.
    for t in (36.43, 37.03, 37.53):
        out.append({"t": t, "path": km / "impactWood_light_001.ogg", "volume": 0.3})
    return out


def mix(video: Path, voice: Path, out: Path, work: Path, report: dict) -> None:
    """Сведение как в сериях 6–7; музыка уходит перед финальным сообщением (читаем в тишине)."""
    from app.audio_mix_model import ebur128
    from app.motion import asset_key
    from app.motion_render import build_audio_layers_args, speech_level_db
    from app.stories_3d import voice_lufs

    music = P1.MUSIC_DIR / "lofi-beat.mp3"
    if not music.is_file():
        music = P1.MUSIC_DIR / "chill-keys.mp3"
    voice_db = voice_lufs(voice)
    layers = [{"id": "voice", "type": "audio", "start": 0.0, "end": DUR, "props": {"asset_id": 9001, "mode": "track", "volume": 1.0, "role": "voice"}}]
    assets = {asset_key(9001): str(voice), asset_key(9002): str(music)}
    m_lufs = float(ebur128(music)["i"])
    gain = round(voice_db - 14.0 - m_lufs, 2)
    layers.append({"id": "music", "type": "audio", "start": 0.0, "end": DING,
                   "props": {"asset_id": 9002, "mode": "track", "volume": 1.0, "gain_db": gain, "role": "music", "fadeIn": 0.3, "fadeOut": 0.8}})
    report["music"] = {"file": music.name, "lufs": m_lufs, "voice_lufs": voice_db, "gain_db": gain}
    notes: list[str] = []
    for n, s in enumerate(sfx_list()):
        if not Path(s["path"]).is_file():
            notes.append(f"нет звука {s['path']}")
            continue
        key = 9100 + n
        assets[asset_key(key)] = str(s["path"])
        end = s["t"] + float(s.get("len") or 2.5)
        layers.append({"id": f"sfx{n}", "type": "audio", "start": round(s["t"], 3), "end": round(min(DUR, end), 3),
                       "props": {"asset_id": key, "volume": s["volume"], "mode": "effect", **({"fadeOut": 0.05} if s.get("len") else {})}})
    pre = work / "mixed.mp4"
    args = build_audio_layers_args(video, pre, layers, assets, DUR, clip_has_audio=False, speech_db=speech_level_db(voice), notes=notes)
    P1._run(args, "сведение")
    P1._run(["ffmpeg", "-y", "-v", "error", "-i", str(pre), "-c:v", "copy", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "192k",
             "-ar", "48000", str(out)], "громкость")
    report["mix_notes"] = notes


def encode(src: Path, out: Path, tg: Path, work: Path) -> None:
    """Финал (crf 17) и копия для Telegram — x264 в два прохода под < 19 МБ."""
    common = ["-fps_mode", "cfr", "-r", str(FPS), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.2", "-g", str(FPS)]
    vf = ["-vf", "scale=1080:1920:flags=lanczos,setsar=1"]
    P1._run(["ffmpeg", "-y", "-v", "error", "-i", str(src), *vf, *common, "-preset", "slow", "-crf", "17", "-maxrate", "12M", "-bufsize", "24M",
             "-movflags", "+faststart", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", str(out)], "финал")
    audio_kbps = 128
    kbps = int(18.3 * 8 * 1024 / DUR) - audio_kbps
    log = work / "x264_2pass"
    for n in (1, 2):
        dst = ["-an", "-f", "mp4", "NUL"] if n == 1 else ["-c:a", "aac", "-b:a", f"{audio_kbps}k", "-ar", "48000", "-movflags", "+faststart", str(tg)]
        P1._run(["ffmpeg", "-y", "-v", "error", "-i", str(src), *vf, *common, "-preset", "slow", "-b:v", f"{kbps}k", "-maxrate", f"{int(kbps * 1.6)}k",
                 "-bufsize", f"{kbps * 2}k", "-pass", str(n), "-passlogfile", str(log), *dst], f"копия для Telegram, проход {n}")


def check_words(final: Path, work: Path) -> dict:
    """faster-whisper по готовому ролику — сверка со сценарием (слова по порядку; писк — не слово)."""
    import difflib
    import re

    import meme3d_voice as V

    probe = work / "final_check.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(final), "-ac", "1", "-ar", "16000", str(probe)], check=True)
    probe.with_suffix(".words.json").unlink(missing_ok=True)
    # Хвост после последней реплики — тишина и звуки: Whisper там «слышит» повторы, отбрасываем.
    last = max(at + (b - a) for _i, _tk, a, b, at, _tx in E.LINES)
    heard = [w for w in V.words(probe) if w["t0"] < last + 0.3]

    def norm(s: str) -> str:
        return re.sub(r"[^а-яa-z0-9]", "", s.lower().replace("ё", "е"))

    script = " ".join(ln[5] for ln in E.LINES)
    for a, b in (("за**бись", "заебись"), ("бл**ь", "блядь"), ("въ**ли", "въебали"), ("отъ**ался", "отъебался"), ("Пи**ец", "пиздец"),
                 ("cyber-abuse", "сайбер абьюз")):
        script = script.replace(a, b)
    want = [norm(w) for w in script.split() if norm(w)]
    got = [norm(w["w"]) for w in heard if norm(w["w"])]
    sm = difflib.SequenceMatcher(a=want, b=got, autojunk=False)
    diffs = [(op, " ".join(want[i1:i2]), " ".join(got[j1:j2])) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal"]
    # Писк: в окне цензуры Whisper не должен слышать слов.
    leak = [w["w"] for w in heard if E.BLEEP_START + 0.05 < (w["t0"] + w["t1"]) / 2 < E.BLEEP_END - 0.05 and w["w"].strip("*,. ")]
    return {"ratio": round(sm.ratio(), 3), "script_words": len(want), "heard_words": len(got), "diffs": diffs, "bleep_leak": leak,
            "text": " ".join(w["w"] for w in heard)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--preview", help="кадры PNG сцены без субтитров: секунды через запятую")
    ap.add_argument("--shots", action="store_true", help="превью: по кадру из середины каждого плана")
    ap.add_argument("--inspect", type=float, help="положения героев и рук в момент t (JSON)")
    ap.add_argument("--small", action="store_true", help="превью 540×960")
    ap.add_argument("--gl", default="gpu")
    ap.add_argument("--name", default="ep5")
    ap.add_argument("--skip-render", action="store_true")
    ap.add_argument("--no-build", action="store_true")
    a = ap.parse_args()
    os.environ.setdefault("MOTION_PACKS_DIR", str(P1.PACKS))
    out_dir = Path(a.out_dir).resolve()
    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    report: dict = {}
    voice, lines, spans = assemble_voice(work)
    words = caption_words(lines)
    spec = build_spec(P1.mouth_envelope(voice))
    if a.small:
        spec["width"], spec["height"] = 540, 960
        spec["quality"]["shadowSize"] = 1024
    sp = work / "scene.json"
    sp.write_text(json.dumps(spec, ensure_ascii=False), "utf-8")
    (work / "events.json").write_text(json.dumps({"spans": spans, "shots": SHOTS, "words": words}, ensure_ascii=False, indent=1), "utf-8")
    if not a.no_build:
        P1._run(["npm", "run", "build:scene3d"], "сборка движка", cwd=ROOT, shell=sys.platform == "win32")
    render = ["node", str(ROOT / "node" / "scene3d" / "render.mjs"), "--spec", str(sp), "--packs", str(P1.PACKS), "--gl", a.gl, "--workers", "1"]
    if a.inspect is not None:
        r = P1._run([*render, "--inspect", str(a.inspect)], "осмотр")
        print(r.stdout.strip().splitlines()[-1][:4000])
        return 0
    if a.preview or a.shots:
        times = a.preview or ",".join(f"{(s0 + s1) / 2:.2f}" for s0, s1, _ in SHOTS)
        P1.wait_ram(3.0)
        prev = work / "preview"
        shutil.rmtree(prev, ignore_errors=True)
        r = P1._run([*render, "--preview", times, "--preview-dir", str(prev)], "превью")
        print(r.stdout.strip().splitlines()[-1][:600])
        return 0
    raw = work / "scene.mp4"
    if not a.skip_render:
        P1.wait_ram(3.5)
        t0 = time.time()
        r = P1._run([*render, "--out", str(raw), "--crf", "14"], "рендер")
        report["render"] = json.loads(r.stdout.strip().splitlines()[-1])
        report["render_sec"] = round(time.time() - t0, 1)
    capped = work / "caps.mp4"
    captions(raw, words, capped, work)
    mixed = work / "final_mix.mp4"
    mix(capped, voice, mixed, work, report)
    final, tg = out_dir / f"{a.name}.mp4", out_dir / f"{a.name}_tg.mp4"
    encode(mixed, final, tg, work)
    times = [round(a0 + (b0 - a0) / 2, 2) for a0, b0, _ in SHOTS]
    P1.frame_sheet(final, times, [f"{t:.1f}s {n}" for t, (_, _, n) in zip(times, SHOTS)], out_dir / "contact_sheet.jpg", cols=8)
    report.update({"video": str(final), "tg": str(tg), "size_mb": round(final.stat().st_size / 2**20, 2), "tg_mb": round(tg.stat().st_size / 2**20, 2),
                   "duration": DUR, "caption_check": str(A6.caption_check(final, words, out_dir)), "whisper": check_words(final, work)})
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
