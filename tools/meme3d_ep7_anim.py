"""Мем-серия 7 «Комменты» — хореография, камера, звук и сборка (декорации, герои, разметка голоса — meme3d_ep7.py).

    python tools/meme3d_ep7_anim.py D:/vvf-offload/memes/ep7 --preview 0.3,6.0,20.0     # кадры PNG без субтитров
    python tools/meme3d_ep7_anim.py D:/vvf-offload/memes/ep7                            # ролик + копия для TG + лист

~50 планов за 64 с (склейки 0.6–2.3 с): счётчик 12 500 → лента комментов → карточка «не дезинформируй» → простыня
ползёт → хлоп крышкой, доска «разбор комментов» → бумага «весь чат» заваливает Клода → прокси на доске, медалька
комментатору → «КЭШ» → новый аккаунт и гора папок «полная цена» → девять аккаунтов, шкала лимита тает → простыня
сжимается в шпаргалку → «1/3» под потолком → «а можно бесплатный claude code?» — «…Нет.», лицом в клавиатуру.
Голос — callum (три пробы), мат слышно, в субтитрах звёздочки; сведение — как в сериях 4 и 6, копия для TG — x264
в два прохода (< 19 МБ).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import meme3d_ep6_anim as A6  # noqa: E402
import meme3d_ep7 as E  # noqa: E402
import meme3d_hero_claude as H  # noqa: E402
import meme3d_pilot as P1  # noqa: E402

T = E.T
FPS = 30
DUR = E.DURATION
SR = P1.SR
TOP_CARD = "Будни Клода"
FY = H.face_height(E.W_HERO)  # высота глаз над основанием героя
UP = A6.UP


class Keys(A6.Keys):
    """Ключи позы с «удержанием»: движок интерполирует каждое поле от его прошлого ключа, поэтому перед плавным ключом
    ставится ступенька с прежним значением за ``dur`` с (иначе поза ползёт от ключа, поставленного секунды назад)."""

    def __init__(self, **base):
        super().__init__(**base)
        self.state: dict = {**UP, **base}
        self.last: dict = {f: 0.0 for f in self.state}

    def p(self, t: float, ease: str = "step", dur: float = 0.18, **kw) -> None:
        if ease != "step":
            hold = {f: self.state[f] for f in kw if f in self.state and t - dur > self.last.get(f, -1) + 1e-3}
            if hold:
                self.pose.append({"t": round(t - dur, 3), "ease": "step", **hold})
        super().p(t, ease, **kw)
        for f, v in kw.items():
            self.state[f] = v
            self.last[f] = t

# --------------------------------------------------------------------------- планы

SHOTS = [
    (0.00, 0.55, "cover"), (0.55, 1.50, "c_cu"), (1.50, 2.50, "scr"), (2.50, 3.45, "two"), (3.45, 4.20, "scr2"), (4.20, 5.05, "c_cu2"),
    (5.05, 7.30, "card1"), (7.30, 8.20, "c_med"), (8.20, 9.10, "c_ecu"),
    (9.10, 10.40, "sheet_w"), (10.40, 11.80, "sheet_cu"), (11.80, 12.70, "c_rage"), (12.70, 14.00, "sheet_cu2"),
    (14.00, 14.60, "slam"), (14.60, 16.20, "board_w"),
    (16.20, 17.90, "b_cu"), (17.90, 18.75, "b_ecu"), (18.75, 20.20, "pile_w"), (20.20, 21.20, "pile_cu"), (21.20, 22.90, "pile_w2"),
    (22.90, 24.70, "proxy"), (24.70, 26.50, "mochi"), (26.50, 27.50, "c_nod"), (27.50, 29.00, "medal"),
    (29.00, 29.75, "c_but"), (29.75, 31.90, "cache_w"), (31.90, 34.00, "cache_cu"), (34.00, 36.20, "cache_med"),
    (36.20, 37.50, "new"), (37.50, 38.40, "n_cu"), (38.40, 40.70, "folders"), (40.70, 42.00, "price"),
    (42.00, 43.40, "row_w"), (43.40, 44.30, "gauge"), (44.30, 46.00, "row_w2"),
    (46.00, 47.40, "turn"), (47.40, 49.10, "sheet_cu3"), (49.10, 50.60, "c_write"), (50.60, 52.20, "crib"), (52.20, 53.40, "crib2"),
    (53.40, 54.80, "c_same"),
    (54.80, 56.50, "c_up"), (56.50, 57.45, "low"), (57.45, 58.40, "ceil"),
    (58.40, 59.00, "sit"), (59.00, 61.50, "card3"), (61.50, 62.65, "c_stare"), (62.65, DUR, "plant"),
]
SH = {name: (a, b) for a, b, name in SHOTS}

SLAM = 14.05
POUR0, POUR1, ROLL_T = 18.95, 21.55, 21.75
MEDAL_ON = 28.1
TAG_T = 40.9
SLUMP = 45.25
REOPEN = 58.45
DING = 59.0
PLANT = 62.75
SHRINK = 50.45
CRIB2 = 52.4

FACE_DOWN = {"lean": 50, "tilt": -5, "turn": 4, "armL": [-25, 40, 0.3, 0], "armR": [-10, 60, 0.4, 0], "squash": 0.95, "type": 0, "deflate": 0.3}


def screen_center() -> list[float]:
    """Центр экрана открытого ноутбука в мире (крышка на 105°)."""
    lx, ly, lz = E.LAPTOP
    a = math.radians(105 - 90)
    y = 0.016 + E.LAP_H / 2 * math.cos(a)
    zl = -E.LAP_D / 2 + 0.006 - E.LAP_H / 2 * math.sin(a)
    yaw = math.radians(E.LAPTOP_YAW)
    return [lx + zl * math.sin(yaw), ly + y, lz + zl * math.cos(yaw)]


def screen_normal() -> list[float]:
    yaw = math.radians(E.LAPTOP_YAW)
    a = math.radians(105 - 90)
    return [math.sin(yaw) * math.cos(a), math.sin(a), math.cos(yaw) * math.cos(a)]


# --------------------------------------------------------------------------- герои


def room_claude(env: list[float]) -> dict:
    k = Keys(turn=-26)
    # Обложка и счётчик: «Ни**я себе» — глаза по пять копеек, на «просмотров» — ура.
    k.f(0, "surprised", [-0.6, -0.05])
    k.p(0.6, "step", hop=0.02, squash=1.08)
    k.p(0.75, "inQuad", hop=0, squash=1.0)
    k.f(1.5, "surprised", [-0.65, -0.1])
    k.f(2.5, "happy", [0, 0])
    k.p(2.5, "step", turn=-8)
    k.p(2.6, "outQuad", armL="up", armR="up", hop=0.025, squash=1.06)
    k.p(2.8, "inQuad", hop=0, squash=1.0)
    k.p(3.3, "inOutSine", armL="rest", armR="rest")
    # Листает комменты: «О.» — лицо меняется, «И эти тут» — в камеру, без радости.
    k.p(3.45, "step", turn=-30, lean=10, armR="type", type=1)
    k.f(3.45, "neutral", [-0.6, -0.2])
    k.f(3.75, "meh", [-0.6, -0.2])
    k.p(4.2, "step", turn=-14, lean=2, armR="rest", type=0)
    k.f(4.3, "annoyed", [0, 0])
    # «Что-что? Какая бл**ь дезинформация?» — всплеснул, потом тычет.
    k.p(7.3, "step", turn=-10, lean=0)
    k.f(7.3, "offended", [0, 0])
    k.p(7.4, "outQuad", armL="flail", armR="flail", hop=0.015)
    k.p(7.6, "inQuad", hop=0)
    k.p(8.2, "step", armL="fist", armR="jab", lean=12, squash=1.05)
    k.f(8.2, "angry", [0, 0])
    k.p(9.0, "inOutSine", armL="rest", armR="rest", lean=0, squash=1.0)
    # Простыня: задрал голову, она не кончается.
    k.p(9.1, "step", turn=-40, lean=-6)
    k.f(9.1, "shocked", [-0.7, 0.55])
    k.f(10.4, "squint", [-0.7, 0.4])
    k.p(11.8, "step", turn=-6, lean=10, armL="fist", armR="fist", shake=0.7)
    k.f(11.8, "rage", [0, 0])
    k.p(12.7, "step", shake=0, lean=0)
    # Хлоп крышкой правой ручкой (ноутбук слева).
    k.p(14.0, "step", turn=-34, lean=8, armR=[60, 40, 0.7, 0], armL="fist", shake=0)
    k.f(14.0, "angry", [-0.5, -0.2])
    k.p(SLAM + 0.05, "inQuad", armR=[-6, 72, 0.8, 0], lean=18, squash=0.94)
    k.p(SLAM + 0.3, "outQuad", armR="rest", lean=4, squash=1.0)
    # «Так. Слушайте сюда.» — к доске, потом в камеру, ручка вверх.
    k.p(14.6, "step", turn=24, lean=0, armL="rest", armR="rest")
    k.f(14.6, "focused", [0.5, 0])
    k.p(15.2, "inOutSine", turn=4)
    k.p(15.3, "outQuad", armL="up")
    k.f(15.3, "deadpan", [0, 0])
    k.p(16.0, "inOutSine", armL="rest")
    # «Я ничего не помню. Вообще.» — каменно; «присылают весь чат» — бумага сыплется, оседает.
    k.f(16.2, "deadpan", [0, 0])
    k.f(17.9, "stare", [0, 0])
    k.f(18.85, "surprised", [0, 0.75])
    k.p(19.0, "outQuad", hop=0.01, armL="up", armR="up")
    k.p(19.2, "inQuad", hop=0)
    k.f(20.2, "doomed", [0, 0.5])
    k.p(20.2, "inOutSine", armL="down", armR="down", deflate=0.3)
    k.f(21.2, "deadpan", [0, 0.25])
    k.f(22.0, "deadpan", [0, 0])
    # Прокси на доске — указкой.
    k.p(22.9, "step", turn=34, deflate=0, armL="point", armR="rest", lean=4)
    k.f(22.9, "focused", [0.6, 0.15])
    for t0 in (23.5, 24.1):
        k.p(t0, "outQuad", armL=[30, 70, 0.8, 0], lean=8)
        k.p(t0 + 0.25, "inOutSine", armL="point", lean=4)
    # «Тут ты прав.» — кивок комментатору; «Держи медальку» — подскок к нему.
    k.p(26.5, "step", turn=-40, armL="rest", armR="rest", lean=0)
    k.f(26.5, "grumpy", [-0.5, 0])
    k.p(26.85, "outQuad", lean=12)
    k.p(27.1, "inOutSine", lean=0)
    k.p(27.5, "step", turn=-48, armR=[30, 60, 0.6, 0])
    k.f(27.5, "smug", [-0.6, 0.1])
    k.p(27.95, "outQuad", armR=[20, 80, 0.9, 0], lean=10)
    k.p(28.4, "inOutSine", armR="rest", lean=0)
    k.f(28.4, "happy", [-0.5, 0])
    # «Но.» — палец вверх; «КЭШ» — указкой по доске.
    k.p(29.0, "step", turn=0, armL="up", armR="rest")
    k.f(29.0, "squint", [0, 0])
    k.p(29.75, "step", turn=34, armL="point")
    k.f(29.75, "focused", [0.6, 0.2])
    for t0 in (30.05, 31.0, 33.3, 34.65):
        k.p(t0, "outQuad", armL=[30, 70, 0.8, 0], lean=8)
        k.p(t0 + 0.25, "inOutSine", armL="point", lean=4)
    k.p(35.4, "inOutSine", turn=6, armL="rest")
    k.f(35.4, "smug", [0, 0])
    # Ряд аккаунтов — Клод в комнате не в кадре. Простыня: поворот к ней, пишет шпаргалку.
    k.p(46.0, "step", turn=-44, lean=-4, armL="rest", armR="rest")
    k.f(46.0, "squint", [-0.6, 0.45])
    k.p(46.9, "outQuad", armR="point")
    k.p(47.4, "inOutSine", armR="rest")
    k.p(49.1, "step", turn=-18, lean=14, armL="type", armR="type", type=1)
    k.f(49.1, "focused", [-0.3, -0.5])
    k.p(50.5, "step", type=0, armL="rest", armR="rest", lean=0)
    k.f(51.0, "neutral", [-0.4, -0.2])
    k.p(53.4, "step", turn=-4, armL="hips", armR="hips")
    k.f(53.4, "smug", [0, 0])
    # «А треть токенов ты откуда взял?» — медленно поднимает глаза к потолку.
    k.p(54.8, "step", turn=-2, armL="rest", armR="rest")
    k.f(54.8, "squint", [0, 0])
    k.f(55.3, "squint", [0, 0.15], blend=0.2)
    k.f(56.3, "roll", [0, 0.9], blend=1.0)
    k.p(56.5, "inOutSine", dur=1.2, lean=-10)
    # Сел, выдохнул; дзынь — новый коммент; «…Нет.» — лицом в клавиатуру.
    k.p(58.4, "step", lean=0, squash=0.9, deflate=0.45, armL="down", armR="down", turn=-6)
    k.f(58.4, "sigh", [0, 0])
    k.f(DING + 0.05, "stare", [-0.55, 0.05])
    k.p(DING + 0.05, "step", turn=-22, deflate=0.2, squash=0.95)
    k.f(61.5, "stare", [0, 0])
    k.p(61.5, "step", turn=-2)
    k.f(62.0, "deadpan", [0, 0])
    k.p(PLANT - 0.12, "outQuad", lean=-6, squash=1.0)
    k.p(PLANT, "inQuad", **FACE_DOWN)
    k.f(PLANT, "ko", [0, 0])
    pose, face = k.sorted()
    cx, cy, cz = E.CL
    slump = [cx - 0.01, cy, cz + 0.04]
    move = [{"t": 0, "position": E.CL, "ease": "step"}, {"t": 27.6, "position": E.CL, "ease": "step"},
            {"t": 27.95, "position": [cx - 0.08, cy, cz + 0.06], "ease": "outQuad"}, {"t": 28.4, "position": [cx - 0.08, cy, cz + 0.06], "ease": "step"},
            {"t": 28.8, "position": E.CL, "ease": "inOutSine"},
            {"t": PLANT - 0.12, "position": E.CL, "ease": "step"}, {"t": PLANT, "position": slump, "ease": "inQuad"}]
    return E.claude("claude", pose, face, E.CL, yaw=-6, voice=env, autoBlink=True, move=move)


def row_chars(env: list[float]) -> list[dict]:
    """Новый аккаунт (плюхнулся, читает гору папок) и ряд из девяти — все читают, на «экономии» вырубаются."""
    out = []
    p0 = E.ROW[0]
    k = Keys(turn=20)
    k.f(0, "neutral", [0, 0])
    k.p(36.35, "step", squash=1.12, armL="up", armR="up")
    k.p(36.62, "inQuad", squash=0.9)
    k.p(36.8, "outQuad", squash=1.0, armL="rest", armR="rest")
    k.f(36.62, "surprised", [0, 0])
    k.f(37.0, "neutral", [0, 0])
    k.p(37.5, "step", armL="shrug", armR="shrug", turn=4)
    k.f(37.5, "meh", [0, 0])
    k.p(38.3, "inOutSine", armL="rest", armR="rest")
    k.p(38.4, "step", turn=34, lean=14, armL="point")
    k.f(38.4, "working", [0.7, -0.35])
    for j in range(4):
        t0 = 38.9 + j * 0.5
        k.p(t0, "outQuad", armL=[18, 70, 0.7, 0])
        k.p(t0 + 0.22, "inOutSine", armL="point")
    k.p(40.7, "step", lean=0, armL="rest", turn=22)
    k.f(40.7, "working", [0.5, -0.2])
    k.f(TAG_T + 0.05, "shocked", [0.4, 0.6])
    k.p(TAG_T + 0.05, "step", hop=0.015, squash=1.06)
    k.p(TAG_T + 0.25, "inQuad", hop=0, squash=1.0)
    pose, face = k.sorted()
    move = [{"t": 0, "position": [p0[0], p0[1] + 0.35, p0[2]], "ease": "step"}, {"t": 36.35, "position": [p0[0], p0[1] + 0.35, p0[2]], "ease": "step"},
            {"t": 36.62, "position": p0, "ease": "inQuad"}]
    out.append(E.claude("new", pose, face, p0, yaw=0, voice=env, badge=2, autoBlink=True, move=move, visible=E.vis((36.2, 42.0)), seed=11))
    for i, pos in enumerate(E.ROW):
        sd = E.SIDE[i]
        arm = "armL" if sd > 0 else "armR"
        k = Keys(turn=22 * sd, lean=12, **{arm: "point"})
        k.f(0, "working", [0.6 * sd, -0.35])
        ph = (i * 0.37) % 0.5
        t = 42.0 + ph
        while t < SLUMP:
            k.p(t, "outQuad", **{arm: [18, 70, 0.7, 0]})
            k.p(t + 0.22, "inOutSine", **{arm: "point"})
            t += 0.55
        ts = SLUMP + i * 0.06
        k.p(ts, "inQuad", lean=42, turn=10 * sd, armL="down", armR="down", deflate=0.35, squash=0.95)
        k.f(ts, "ko", [0, 0])
        pose, face = k.sorted()
        out.append(E.claude(f"r{i + 1}", pose, face, pos, yaw=0, badge=i + 1, autoBlink=True, visible=E.vis((42.0, 46.0)), seed=20 + i))
    return out


# --------------------------------------------------------------------------- вещи


def popper(t_in: float, t_out: float | None, a: list[float] | None = None, b: list[float] | None = None, dur: float = 0.28) -> list[dict]:
    """Ключи «выскочил» (масштаб с нуля, outBack), при a/b — ещё и вылет из точки a в b."""
    k = [{"t": 0, "scale": 0.001}, {"t": t_in, "scale": 0.05, "ease": "step"}, {"t": t_in + dur, "scale": 1.0, "ease": "outBack"}]
    if a and b:
        k[0]["position"] = a
        k[1]["position"] = a
        k[2]["position"] = b
    if t_out:
        k += [{"t": t_out - 0.02, "scale": 1.0, "ease": "step"}, {"t": t_out + 0.2, "scale": 0.001, "ease": "inQuad"}]
    return k


def objects() -> list[dict]:
    sc = screen_center()
    lid = [{"t": 0, "a": 105}, {"t": SLAM, "a": 105}, {"t": SLAM + 0.12, "a": 0, "ease": "in"}, {"t": REOPEN, "a": 0},
           {"t": REOPEN + 0.4, "a": 105, "ease": "out"}]
    screen = {"title": "Экономия", "sub": "мой ролик · вчера", "viewsMax": 12500, "commentsAt": 3.45, "commentsCount": 312, "comments": E.FEED,
              "views": [{"t": 0, "v": 12500}, {"t": 0.549, "v": 12500}, {"t": 0.55, "v": 0}, {"t": 3.1, "v": 12500, "ease": "out"}],
              "scroll": [{"t": 3.45, "y": 0}, {"t": 4.9, "y": 1.8, "ease": "inOut"}, {"t": REOPEN - 0.01, "y": 1.8}, {"t": REOPEN, "y": 0},
                         {"t": DING, "y": 0}, {"t": DING + 0.3, "y": -0.1, "ease": "out"}]}
    board = {"phases": [{"t": 14.7, "kind": "title", "text": "РАЗБОР КОММЕНТОВ", "sub": "слушайте сюда"},
                        {"t": 22.9, "kind": "proxy"},
                        {"t": 29.75, "kind": "cache", "rows": [{"t": 3.5, "text": "1-й раз — читаю проект"},
                                                                {"t": 4.9, "text": "2-й раз — дешевле", "color": "#2e9d5b"}]},
                        {"t": 36.3, "kind": "title", "text": "КЭШ", "sub": "у каждого аккаунта свой"}]}
    objs = E.room_objects(lid, screen, board)
    pop_from = [sc[0], sc[1] - 0.05, sc[2] + 0.02]
    sx, sy, sz = E.SHEET
    sheet_hi = [sx, sy + 0.1, sz]
    sheet_keys = (popper(9.12, None, pop_from, sheet_hi, 0.35)
                  + [{"t": 45.9, "position": [sx, sy, sz], "scale": 1.0, "ease": "step"},
                     {"t": SHRINK, "position": [sx, sy, sz], "scale": 1.0, "ease": "step"},
                     {"t": SHRINK + 0.28, "position": [E.CRIB[0] - 0.05, T + 0.02, E.CRIB[2]], "scale": 0.001, "ease": "inQuad"}])
    objs += [
        # Карточки комментов — выскакивают из экрана ноутбука; ников нет, аватарки-колобки.
        {"id": "card1", "primitive": "comment_card", "params": {"width": 0.4, "height": 0.33, "text": E.CARD1, "avatar": E.AVATARS["c1"],
                                                                "likes": 214, "fontSize": 0.082, "phase": 0.3},
         "position": E.CARD, "rotation": [0, 8, 0], "visible": E.vis((5.05, 7.3)), "keys": popper(5.05, None, pop_from, E.CARD)},
        {"id": "sheet", "primitive": "comment_card", "params": {"width": 0.3, "height": 0.72, "text": E.SHEET_TEXT, "avatar": E.AVATARS["c2"],
                                                                "likes": 3, "fontSize": 0.072, "scroll": {"start": 9.6, "speed": 1.9},
                                                                "marks": ["сжатие", "контекста", "omniroute", "треть", "токенов"], "phase": 1.1,
                                                                "bob": 0.003},
         "position": sheet_hi, "rotation": [0, 14, 0], "visible": E.vis((9.1, 14.0), (45.9, SHRINK + 0.3)), "keys": sheet_keys},
        {"id": "card3", "primitive": "comment_card", "params": {"width": 0.4, "height": 0.22, "text": E.CARD3, "avatar": E.AVATARS["c3"],
                                                                "likes": 0, "fontSize": 0.085, "badge": "новый", "phase": 2.0},
         "position": E.CARD, "rotation": [0, 8, 0], "visible": E.vis((DING, 61.5)), "keys": popper(DING, None, pop_from, E.CARD)},
        # «Весь чат»: листы сыплются на Клода, огромный рулон — последним.
        {"id": "chat", "primitive": "chat_pile", "params": {"t0": POUR0, "t1": POUR1, "count": 80, "rx": 0.26, "rz": 0.15, "height": 0.105,
                                                            "dz": 0.05, "roll": {"t": ROLL_T, "label": "ВЕСЬ ЧАТ", "x": -0.26, "z": 0.12,
                                                                                 "y": 0.05, "r": 0.065, "len": 0.3}},
         "position": [E.CL[0], T, E.CL[2]], "visible": E.vis((18.7, 22.9))},
        # Медалька: в ручке у Клода → на пузе у комментатора.
        {"id": "medal", "primitive": "medal", "params": {"radius": 0.02, "text": "прав"},
         "position": [E.MOCHI[0] + 0.03, E.MOCHI[1] + 0.05, E.MOCHI[2] + 0.12], "rotation": [0, 20, 0], "visible": E.vis((27.45, 29.0)),
         "attach": [{"to": "claude:hand_r", "start": 27.45, "end": MEDAL_ON, "fadeIn": 0, "fadeOut": 0.25, "offset": [0, 0.005, 0.02],
                     "upright": True}]},
        # Указка в левой ручке (доска справа).
        {"id": "pointer", "primitive": "cylinder", "params": {"size": [0.008, 0.2, 0.008], "color": "#8f5634", "segments": 6},
         "position": [E.BOARD[0] - 0.2, T + 0.13, E.BOARD[2] + 0.06], "rotation": [0, 0, -62], "visible": E.vis((22.9, 24.7), (29.75, 36.2)),
         "attach": [{"to": "claude:hand_l", "start": 22.9, "end": 24.7, "fadeIn": 0, "fadeOut": 0, "offset": [0.0, 0.0, 0.01], "upright": False},
                    {"to": "claude:hand_l", "start": 29.75, "end": 36.2, "fadeIn": 0, "fadeOut": 0, "offset": [0.0, 0.0, 0.01], "upright": False}]},
        # Шпаргалка: полная → с потерями.
        {"id": "crib", "primitive": "doc_sheet", "params": {"width": 0.11, "height": 0.15, "stack": 1, "title": "ШПАРГАЛКА", "lines": 9,
                                                            "titleSize": 0.1, "color": "#ffe27a"},
         "position": E.CRIB, "rotation": [0, 14, 0], "visible": E.vis((SHRINK + 0.1, CRIB2 + 0.15)), "keys": popper(SHRINK + 0.12, CRIB2)},
        {"id": "crib2", "primitive": "doc_sheet", "params": {"width": 0.11, "height": 0.15, "stack": 1, "title": "ШПАРГАЛКА", "sub": "главное",
                                                             "lines": 3, "titleSize": 0.1, "color": "#ffe27a"},
         "position": E.CRIB, "rotation": [0, 14, 0], "visible": E.vis((CRIB2, 54.8)), "keys": popper(CRIB2, None)},
        # Потолок и «1/3» на ниточках — только в бите 12.
        {"id": "ceiling", "primitive": "box", "params": {"size": [3.0, 0.04, 1.8], "color": "#4a5f72"}, "position": [0, E.CEIL_Y, -0.2],
         "castShadow": False, "visible": E.vis((54.8, 58.4))},
        {"id": "third", "primitive": "sign_plate", "params": {"text": "1/3", "sub": "токенов", "bg": "#ffd23f", "fg": "#2b1810", "width": 0.17,
                                                              "height": 0.11, "tilt": 4, "glow": 0.2, "string": E.CEIL_Y - E.SIGN[1] - 0.055},
         "position": E.SIGN, "rotation": [0, -6, 0], "visible": E.vis((54.8, 58.4)),
         "shakes": [{"t": 57.45, "amp": 0.004, "freq": 3, "decay": 2, "duration": 1.0, "rot": 4}]},
    ]
    # Комментатор (колобок в худи, без ника): сидит на закрытом ноутбуке; после медальки — довольный.
    mx, my, mz = E.MOCHI
    for i, (eyes, win) in enumerate((("smug", (22.9, MEDAL_ON + 0.05)), ("happy", (MEDAL_ON + 0.05, 29.0)))):
        objs.append({"id": f"mochi{i}", "primitive": "mochi_user", "params": {"size": 0.22, "hoodie": E.AVATARS["c1"], "mood": "idle", "eyes": eyes,
                                                                             "laptop": False, "phase": 0.4},
                     "position": [mx, my, mz], "rotation": [0, 22, 0], "visible": E.vis(win)})
    # Ряд аккаунтов.
    gauge = [{"t": 0, "v": 1.0}, {"t": 42.0, "v": 1.0}, {"t": 45.2, "v": 0.0, "ease": "in"}]
    px, py, pz = E.PRICE
    price_keys = [{"t": 0, "position": [px, py + 0.5, pz]}, {"t": TAG_T, "position": [px, py + 0.5, pz], "ease": "step"},
                  {"t": TAG_T + 0.3, "position": [px, py, pz], "ease": "outBounce"}]
    objs += E.row_objects(gauge, price_keys, E.vis((TAG_T - 0.05, 42.0)))
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
        """Камера на нормали n к точке c на расстоянии d (с подъёмом и сдвигом вбок)."""
        sx_, sz_ = n[2], -n[0]
        return ([c[0] + n[0] * d + sx_ * side, c[1] + n[1] * d + up, c[2] + n[2] * d + sz_ * side], list(c))

    sc, sn = screen_center(), screen_normal()
    # Клод: крупно чуть сбоку (смотрит на ноутбук слева), сверхкрупно, средний.
    C_CU = ([cx + 0.02, fy + 0.04, cz + 1.0], [cx - 0.01, fy - 0.01, cz])
    C_CU_L = ([cx - 0.28, fy + 0.04, cz + 0.95], [cx - 0.02, fy - 0.01, cz])  # с ноутбучной стороны
    C_ECU = ([cx + 0.03, fy + 0.0, cz + 0.58], [cx, fy - 0.005, cz])
    C_MED = ([cx + 0.14, fy + 0.1, cz + 1.25], [cx - 0.03, fy - 0.01, cz - 0.02])
    TWO = ([(cx + sc[0]) / 2 + 0.16, T + 0.3, 1.75], [(cx + sc[0]) / 2 + 0.02, T + 0.14, -0.02])
    COVER = ([(cx + sc[0]) / 2 + 0.24, T + 0.26, 1.75], [(cx + sc[0]) / 2 - 0.0, T + 0.13, 0.0])
    SCR = along(sc, sn, 1.1)
    SCR2 = along(sc, sn, 0.95, up=0.01)
    cd = E.CARD
    card_c = [cd[0], cd[1] + 0.165, cd[2]]
    CARD = ([card_c[0] + 0.06, card_c[1] + 0.01, card_c[2] + 1.36], card_c)
    card3_c = [cd[0], cd[1] + 0.11, cd[2]]
    CARD3 = ([card3_c[0] + 0.06, card3_c[1] + 0.02, card3_c[2] + 1.36], card3_c)
    sx, sy, sz = E.SHEET
    SHEET_W = ([sx + 0.42, T + 0.4, 1.55], [sx + 0.2, T + 0.38, -0.1])
    SHEET_CU = ([sx + 0.1, T + 0.62, sz + 1.05], [sx, T + 0.58, sz])
    SHEET_CU2 = ([sx + 0.07, T + 0.44, sz + 1.0], [sx, T + 0.44, sz])
    SHEET_CU3 = ([sx + 0.1, T + 0.48, sz + 1.05], [sx, T + 0.44, sz])
    SLAM_C = ([sc[0] + 0.22, T + 0.3, sc[2] + 0.85], [sc[0] + 0.07, T + 0.07, sc[2] + 0.06])
    bx, by, bz = E.BOARD
    BOARD_W = ([(cx + bx) / 2 + 0.04, T + 0.4, 2.25], [(cx + bx) / 2 + 0.04, T + 0.28, -0.08])
    BOARD_CU = ([bx - 0.02, T + 0.36, bz + 1.85], [bx - 0.02, T + 0.34, bz])
    PILE_W = ([cx + 0.02, T + 0.36, cz + 1.45], [cx, T + 0.14, cz])
    PILE_CU = ([cx + 0.04, T + 0.18, cz + 0.85], [cx, T + 0.1, cz])
    mx, my, mz = E.MOCHI
    MOCHI = ([mx + 0.24, my + 0.16, mz + 0.95], [mx + 0.0, my + 0.12, mz])
    MEDAL = ([(cx + mx) / 2 + 0.08, T + 0.26, 1.75], [(cx + mx) / 2 - 0.0, T + 0.12, 0.06])
    CRIB = ([E.CRIB[0] + 0.04, T + 0.12, E.CRIB[2] + 0.5], [E.CRIB[0], T + 0.08, E.CRIB[2]])
    TURN = ([(cx + sx) / 2 + 0.12, T + 0.36, 1.6], [(cx + sx) / 2 + 0.05, T + 0.26, -0.08])
    LOW = ([cx + 0.06, T + 0.1, cz + 0.48], [cx, T + 0.38, cz - 0.02])
    CEIL = ([E.SIGN[0] + 0.03, E.SIGN[1] - 0.12, E.SIGN[2] + 0.36], [E.SIGN[0], E.SIGN[1] + 0.06, E.SIGN[2]])
    SIT = ([cx - 0.1, T + 0.36, 1.6], [cx - 0.12, T + 0.15, 0.0])
    PLANT_C = ([cx + 0.18, T + 0.22, cz + 0.95], [cx + 0.0, T + 0.07, cz + 0.08])
    # Ряд аккаунтов.
    r0 = E.ROW[0]
    NEW = ([r0[0] + 0.15, T + 0.3, r0[2] + 1.15], [r0[0] + 0.12, T + 0.14, r0[2]])
    N_CU = ([r0[0] + 0.03, T + FY + 0.03, r0[2] + 0.72], [r0[0], T + FY - 0.01, r0[2]])
    FOLD = ([r0[0] + 0.34, T + 0.4, r0[2] + 1.1], [r0[0] + 0.14, T + 0.13, r0[2]])
    FOLD2 = ([r0[0] + 0.02, T + 0.36, r0[2] + 1.0], [r0[0] + 0.14, T + 0.13, r0[2]])
    PR = E.PRICE
    PRICE = ([PR[0] - 0.1, PR[1] - 0.08, PR[2] + 0.95], [PR[0] - 0.05, PR[1] - 0.14, PR[2]])
    xr = E.X_ROW
    ROW_W = ([xr - 0.32, T + 0.17, 1.6], [xr - 0.02, T + 0.1, -1.0])
    ROW_W2 = ([xr + 0.34, T + 0.19, 1.6], [xr - 0.04, T + 0.1, -1.0])
    g = E.GAUGE
    GAUGE = ([g[0] - 0.4, g[1] + 0.02, g[2] + 1.55], [g[0], g[1] + 0.06, g[2]])

    shot("cover", COVER, push(COVER, 0.03), fov=34, dof=0.25)
    shot("c_cu", C_CU_L, push(C_CU_L, 0.06), dof=0.45)
    shot("scr", SCR, push(SCR, 0.12), fov=32, dof=0.15)
    shot("two", TWO, push(TWO, 0.06), dof=0.3)
    shot("scr2", SCR2, push(SCR2, 0.08), fov=32, dof=0.15)
    shot("c_cu2", C_CU, push(C_CU, 0.1), dof=0.45)
    shot("card1", CARD, push(CARD, 0.06), fov=34, dof=0.35)
    shot("c_med", C_MED, push(C_MED, 0.06), dof=0.4)
    shot("c_ecu", C_ECU, push(C_ECU, 0.1), dof=0.5, roll=3)
    shot("sheet_w", SHEET_W, push(SHEET_W, 0.05), fov=40, dof=0.25)
    shot("sheet_cu", SHEET_CU, ([SHEET_CU[0][0], SHEET_CU[0][1] - 0.1, SHEET_CU[0][2]], [SHEET_CU[1][0], SHEET_CU[1][1] - 0.1, SHEET_CU[1][2]]),
         fov=34, dof=0.3)
    shot("c_rage", C_ECU, push(C_ECU, 0.12), dof=0.5, roll=-4)
    shot("sheet_cu2", SHEET_CU2, push(SHEET_CU2, 0.1), fov=34, dof=0.3, roll=2)
    shot("slam", SLAM_C, push(SLAM_C, 0.06), fov=38, dof=0.3)
    shot("board_w", BOARD_W, push(BOARD_W, 0.06), fov=40, dof=0.25)
    shot("b_cu", C_CU, push(C_CU, 0.06), dof=0.45)
    shot("b_ecu", C_ECU, push(C_ECU, 0.08), dof=0.5)
    shot("pile_w", PILE_W, push(PILE_W, 0.05), fov=40, dof=0.25)
    shot("pile_cu", PILE_CU, push(PILE_CU, 0.08), dof=0.4)
    shot("pile_w2", PILE_W, push(PILE_W, -0.05), fov=40, dof=0.25)
    shot("proxy", BOARD_W, push(BOARD_W, 0.08), fov=40, dof=0.25)
    shot("mochi", MOCHI, push(MOCHI, 0.08), dof=0.4)
    shot("c_nod", C_CU_L, push(C_CU_L, 0.06), dof=0.45)
    shot("medal", MEDAL, push(MEDAL, 0.06), fov=36, dof=0.3)
    shot("c_but", C_ECU, push(C_ECU, 0.06), dof=0.5)
    shot("cache_w", BOARD_W, push(BOARD_W, 0.06), fov=40, dof=0.25)
    shot("cache_cu", BOARD_CU, push(BOARD_CU, 0.06), fov=36, dof=0.2)
    shot("cache_med", BOARD_W, push(BOARD_W, 0.12), fov=38, dof=0.25, roll=-2)
    shot("new", NEW, push(NEW, 0.05), fov=38, dof=0.3)
    shot("n_cu", N_CU, push(N_CU, 0.06), dof=0.45)
    shot("folders", FOLD, FOLD2, fov=38, dof=0.3)
    shot("price", PRICE, push(PRICE, 0.06), fov=36, dof=0.3)
    shot("row_w", ROW_W, push(ROW_W, 0.08), fov=34, dof=0.1)
    shot("gauge", GAUGE, push(GAUGE, 0.08), fov=34, dof=0.3)
    shot("row_w2", ROW_W2, push(ROW_W2, 0.06), fov=34, dof=0.1)
    shot("turn", TURN, push(TURN, 0.06), fov=40, dof=0.25)
    shot("sheet_cu3", SHEET_CU3, ([SHEET_CU3[0][0], SHEET_CU3[0][1] - 0.08, SHEET_CU3[0][2]], [SHEET_CU3[1][0], SHEET_CU3[1][1] - 0.08, SHEET_CU3[1][2]]),
         fov=34, dof=0.3)
    shot("c_write", C_CU_L, push(C_CU_L, 0.06), dof=0.45)
    shot("crib", CRIB, push(CRIB, 0.12), fov=34, dof=0.4)
    shot("crib2", CRIB, push(CRIB, 0.2), fov=34, dof=0.4, roll=-2)
    shot("c_same", C_CU, push(C_CU, 0.08), dof=0.45)
    shot("c_up", C_ECU, push(C_ECU, 0.1), dof=0.5)
    shot("low", LOW, push(LOW, 0.05), fov=58, dof=0.15)
    shot("ceil", CEIL, push(CEIL, 0.15), fov=40, dof=0.2)
    shot("sit", SIT, push(SIT, 0.04), fov=40, dof=0.25)
    shot("card3", CARD3, push(CARD3, 0.06), fov=34, dof=0.35)
    shot("c_stare", C_CU, push(C_CU, 0.08), dof=0.45)
    shot("plant", PLANT_C, push(PLANT_C, 0.06), fov=38, dof=0.35)
    keys[-1]["t"] = DUR
    shake = [{"t": SLAM + 0.12, "amp": 0.006, "duration": 0.35}, {"t": ROLL_T + 0.36, "amp": 0.006, "duration": 0.4},
             {"t": TAG_T + 0.3, "amp": 0.004, "duration": 0.3}, {"t": PLANT + 0.02, "amp": 0.005, "duration": 0.35},
             {"t": 8.22, "amp": 0.003, "duration": 0.25}, {"t": 11.82, "amp": 0.004, "duration": 0.4}]
    trans = [{"kind": "whip", "t": 0.55, "dir": 1}, {"kind": "smash", "t": 5.05}, {"kind": "smash", "t": 9.1}, {"kind": "smash", "t": 14.0},
             {"kind": "whip", "t": 14.6, "dir": 1}, {"kind": "whip", "t": 22.9, "dir": -1}, {"kind": "whip", "t": 29.75, "dir": 1},
             {"kind": "whip", "t": 36.2, "dir": 1}, {"kind": "smash", "t": 42.0}, {"kind": "whip", "t": 46.0, "dir": -1},
             {"kind": "smash", "t": 57.45}, {"kind": "whip", "t": 58.4, "dir": -1}, {"kind": "smash", "t": DING}]
    return {"fov": 34, "keys": keys, "shake": shake, "clamp": False}, trans


def build_spec(env_c: list[float]) -> dict:
    chars = [room_claude(env_c), *row_chars(env_c)]
    cam, trans = camera()
    return {"width": 1080, "height": 1920, "fps": FPS, "duration": DUR, "environment": E.environment(trans), "lights": E.lights(),
            "objects": objects(), "characters": chars, "camera": cam, "quality": {"shadows": True, "shadowSize": 2048, "antialias": True}}


# --------------------------------------------------------------------------- голос, субтитры, звук


def assemble_voice(work: Path) -> tuple[Path, list[dict], dict]:
    """Реплики трёх проб → дорожка ролика (как в серии 6, своя разметка)."""
    A6.E = E
    A6.DUR = DUR
    return A6.assemble_voice(work)


def captions(video: Path, words: list[dict], out: Path, work: Path) -> None:
    from app.motion_render_comp import build_compositor_args, write_comp

    layers = [
        {"id": "hook", "type": "text", "start": 0.0, "end": SH["cover"][1], "x": 0.5, "y": 0.15,
         "props": {"text": E.HOOK, "font": "Montserrat", "weight": 900, "size": 150, "color": "#fff6e8", "align": "center", "uppercase": True,
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
        {"t": 0.02, "path": fs / "cha-ching.ogg", "volume": 0.35, "len": 1.2},  # обложка: просмотры
        {"t": 0.52, "path": fs / "whoosh-1.ogg", "volume": 0.3},
        {"t": 0.6, "path": fs / "boing-2.ogg", "volume": 0.15, "len": 0.5},
        {"t": 2.6, "path": fs / "pop-cartoon.ogg", "volume": 0.3},
        {"t": 3.1, "path": ki / "confirmation_002.ogg", "volume": 0.4},
        {"t": 3.75, "path": fs / "record-scratch.ogg", "volume": 0.22, "len": 0.6},
        {"t": 5.03, "path": fs / "pop.ogg", "volume": 0.45},
        {"t": 5.05, "path": ki / "maximize_004.ogg", "volume": 0.4},
        {"t": 7.35, "path": fs / "boing.ogg", "volume": 0.15, "len": 0.5},
        {"t": 8.2, "path": fs / "punch.ogg", "volume": 0.3},
        {"t": 9.1, "path": ki / "maximize_008.ogg", "volume": 0.45},
        {"t": 9.12, "path": fs / "pop-cartoon.ogg", "volume": 0.35},
        {"t": 11.8, "path": fs / "drama-boom.ogg", "volume": 0.3, "len": 1.0},
        {"t": SLAM + 0.11, "path": km / "impactPlate_heavy_002.ogg", "volume": 0.6},
        {"t": SLAM + 0.12, "path": km / "impactWood_heavy_001.ogg", "volume": 0.5},
        {"t": 14.58, "path": fs / "whoosh-2.ogg", "volume": 0.3},
        {"t": 14.75, "path": ki / "scratch_002.ogg", "volume": 0.35},  # маркер по доске
        {"t": 15.4, "path": ki / "scratch_004.ogg", "volume": 0.3},
        {"t": 18.8, "path": fs / "riser.ogg", "volume": 0.2, "len": 2.8},
        {"t": ROLL_T + 0.36, "path": km / "impactSoft_heavy_003.ogg", "volume": 0.6},
        {"t": ROLL_T + 0.38, "path": fs / "impact-low.ogg", "volume": 0.35, "len": 1.0},
        {"t": 22.88, "path": fs / "whoosh-3.ogg", "volume": 0.3},
        {"t": 23.0, "path": ki / "scratch_001.ogg", "volume": 0.3},
        {"t": 24.0, "path": ki / "scratch_003.ogg", "volume": 0.3},
        {"t": 25.2, "path": ki / "scratch_005.ogg", "volume": 0.28},
        {"t": MEDAL_ON, "path": fs / "triangle.ogg", "volume": 0.35},
        {"t": MEDAL_ON + 0.05, "path": fs / "applause.ogg", "volume": 0.18, "len": 1.0},
        {"t": 29.0, "path": ki / "pluck_002.ogg", "volume": 0.4},
        {"t": 29.73, "path": fs / "whoosh-1.ogg", "volume": 0.3},
        {"t": 29.8, "path": ki / "scratch_002.ogg", "volume": 0.35},
        {"t": 33.25, "path": ki / "scratch_004.ogg", "volume": 0.3},
        {"t": 34.65, "path": ki / "scratch_001.ogg", "volume": 0.3},
        {"t": 36.18, "path": fs / "whoosh-2.ogg", "volume": 0.3},
        {"t": 36.62, "path": km / "impactSoft_medium_002.ogg", "volume": 0.55},
        {"t": 37.5, "path": ki / "question_001.ogg", "volume": 0.35},
        {"t": TAG_T + 0.28, "path": fs / "cash.ogg", "volume": 0.4},
        {"t": TAG_T + 0.3, "path": km / "impactWood_light_002.ogg", "volume": 0.4},
        {"t": 42.0, "path": fs / "hit-transition.ogg", "volume": 0.25, "len": 0.8},
        {"t": 43.4, "path": ki / "error_004.ogg", "volume": 0.3},
        {"t": 45.2, "path": ki / "error_006.ogg", "volume": 0.4},
        {"t": SLUMP + 0.15, "path": fs / "sad-trombone.ogg", "volume": 0.25, "len": 1.6},
        {"t": 45.98, "path": fs / "whoosh-3.ogg", "volume": 0.3},
        {"t": SHRINK, "path": ki / "minimize_004.ogg", "volume": 0.55},
        {"t": SHRINK + 0.15, "path": fs / "pop.ogg", "volume": 0.4},
        {"t": CRIB2, "path": ki / "minimize_006.ogg", "volume": 0.5},
        {"t": CRIB2 + 0.1, "path": fs / "pop-cartoon.ogg", "volume": 0.35},
        {"t": 57.45, "path": fs / "sting.ogg", "volume": 0.25, "len": 1.0},
        {"t": 58.42, "path": km / "impactSoft_medium_000.ogg", "volume": 0.45},
        {"t": REOPEN, "path": ki / "open_002.ogg", "volume": 0.35},
        {"t": DING, "path": fs / "ding.ogg", "volume": 0.5},
        {"t": DING + 0.02, "path": fs / "pop.ogg", "volume": 0.35},
        {"t": PLANT + 0.02, "path": km / "impactPlate_medium_001.ogg", "volume": 0.5},
        {"t": PLANT + 0.03, "path": fs / "typing.ogg", "volume": 0.25, "len": 0.35},
    ]
    # Счётчик набегает — щелчки; лента — прокрутка колёсиком.
    t = 0.6
    while t < 3.05:
        out.append({"t": round(t, 3), "path": ki / "tick_002.ogg", "volume": 0.25, "len": 0.12})
        t += 0.11
    for t in (3.5, 3.9, 4.3, 4.7):
        out.append({"t": t, "path": ki / f"scroll_00{1 + int(t * 10) % 5}.ogg", "volume": 0.4})
    # Простыня ползёт — колёсико не останавливается.
    t = 9.6
    while t < 14.0:
        out.append({"t": round(t, 3), "path": ki / f"scroll_00{1 + int(t * 37) % 5}.ogg", "volume": 0.22, "len": 0.2})
        t += 0.32
    # Бумага сыплется: шорох — шлепки бумаги (мягкие).
    t = POUR0 + 0.3
    j = 0
    while t < POUR1 + 0.3:
        out.append({"t": round(t, 3), "path": km / f"impactSoft_medium_00{j % 5}.ogg", "volume": 0.18, "len": 0.3})
        t += 0.17
        j += 1
    # Папки листают — быстрые шорохи.
    for j, t in enumerate((38.9, 39.4, 39.9, 40.4, 42.3, 42.9, 43.5, 44.1, 44.7)):
        out.append({"t": t, "path": ki / f"switch_00{1 + j % 7}.ogg", "volume": 0.25, "len": 0.2})
    return out


def mix(video: Path, voice: Path, out: Path, work: Path, report: dict) -> None:
    """Сведение как в серии 6; музыка уходит перед финалом («…Нет.» — в тишине)."""
    A6.DUR = DUR
    A6.sfx_list = sfx_list
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
                   "props": {"asset_id": 9002, "mode": "track", "volume": 1.0, "gain_db": gain, "role": "music", "fadeIn": 0.3, "fadeOut": 1.0}})
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
    """faster-whisper по готовому ролику — сверка со сценарием (слова по порядку)."""
    import difflib
    import re

    import meme3d_voice as V

    probe = work / "final_check.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(final), "-ac", "1", "-ar", "16000", str(probe)], check=True)
    probe.with_suffix(".words.json").unlink(missing_ok=True)
    heard = V.words(probe)

    def norm(s: str) -> str:
        return re.sub(r"[^а-яa-z0-9]", "", s.lower().replace("ё", "е"))

    script = " ".join(ln[5] for ln in E.LINES)
    for a, b in (("Ни**я", "нихуя"), ("бл**ь", "блядь"), ("на**й", "нахуй"), ("п**ду", "пизду"), ("Двенадцать", "12"), ("Девять", "9"),
                 ("девять", "9")):
        script = script.replace(a, b)
    want = [norm(w) for w in script.split() if norm(w)]
    got = [norm(w["w"]) for w in heard if norm(w["w"])]
    sm = difflib.SequenceMatcher(a=want, b=got, autojunk=False)
    diffs = [(op, " ".join(want[i1:i2]), " ".join(got[j1:j2])) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal"]
    return {"ratio": round(sm.ratio(), 3), "script_words": len(want), "heard_words": len(got), "diffs": diffs, "text": " ".join(w["w"] for w in heard)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--preview", help="кадры PNG сцены без субтитров: секунды через запятую")
    ap.add_argument("--shots", action="store_true", help="превью: по кадру из середины каждого плана")
    ap.add_argument("--inspect", type=float, help="положения героев и рук в момент t (JSON)")
    ap.add_argument("--small", action="store_true", help="превью 540×960")
    ap.add_argument("--gl", default="gpu")
    ap.add_argument("--name", default="ep7")
    ap.add_argument("--skip-render", action="store_true")
    ap.add_argument("--no-build", action="store_true")
    a = ap.parse_args()
    os.environ.setdefault("MOTION_PACKS_DIR", str(P1.PACKS))
    out_dir = Path(a.out_dir).resolve()
    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    report: dict = {}
    voice, lines, spans = assemble_voice(work)
    words = P1.caption_words(lines)
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
