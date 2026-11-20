"""Мем-серия 8 «Я подъёбываю юзера», v3 — хореография, камера, звук и сборка (разметка голоса, декорации, чаты —
meme3d_ep8.py).

    python tools/meme3d_ep8_anim.py D:/vvf-offload/memes/ep8 --shots --small          # по кадру с каждого плана
    python tools/meme3d_ep8_anim.py D:/vvf-offload/memes/ep8 --preview 0.3,34.0       # кадры PNG без субтитров
    python tools/meme3d_ep8_anim.py D:/vvf-offload/memes/ep8                          # ролик + копия для TG + лист

Клод — callum, юзер — will (v3: владелец попросил озвучить и юзера, другим голосом); реплики юзера звучат, пока
на экране его пузырь, в субтитрах — голубым; морда моти злая, сонная, шокированная. ~50 планов за ~45 с (склейки 0.4–1.6 с): ухмылка → «нарисуй маскота Клода» / «Не могу. Товарный знак» /
«ты сам Клод, бл*!» / «Тем более» → оплата, «Платежи — это серьёзно. Давай начнём с безопасности», лекция на 40
сообщений, юзер спит → «автоматизируй Стим» / «правила площадки» / «да я инвентарь посчитать хотел!» → «у других Клоды
так делают!», соседи-Клоды → «Другие Клоды — не я. Пошёл…» (в поле ввода «Пошёл на», стёрто) → «Я понимаю ваше
разочарование» → «И так часами» (часы крутятся) → «Иногда сдаюсь. Делаю за тридцать секунд» → «а чё ты три часа
вы**ывался?!» → «А иногда…», музыка уходит → юзер устало: «ну просто переименуй файл, пожалуйста» (крупно) → бах,
красная плашка во весь кадр → юзер в шоке → Клод в камеру: «Достал.», чай. Сведение — как в серии 5, копия для TG —
x264 в два прохода (< 19 МБ).
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
import meme3d_ep7_anim as A7  # noqa: E402
import meme3d_ep8 as E  # noqa: E402
import meme3d_hero_claude as H  # noqa: E402
import meme3d_pilot as P1  # noqa: E402

T = E.T
FPS = 30
DUR = E.DURATION
SR = P1.SR
TOP_CARD = "Будни Клода"
FY = H.face_height(E.W_HERO)
Keys = A7.Keys
popper = A7.popper
vis = E.vis
AT, END = E.AT, E.END

# --------------------------------------------------------------------------- планы

TEA = END["e2"] + 0.1


def _shots() -> list[tuple[float, float, str]]:
    v5e, v6e, v8e, v12e, e1e = END["v5b"], END["v6"], END["v8"], END["v12"], END["e1"]
    a = [
        (0.0, "cover"), (0.55, "c_cu"), (AT["v2"] - 0.05, "c_wide"), (AT["v2"] + 0.85, "c_shrug"),
        (END["v2"], "u_type"), (E.REQ1_T, "bub_req1"), (AT["v3"], "bub_rep1"), (AT["v3"] + 1.0, "c_tm"),
        (E.RET1_T, "u_ret1"), (AT["v4"] - 0.05, "c_smug"),
        (END["v4"] + 0.1, "u_type2"), (E.REQ2_T, "bub_req2"), (AT["v5a"], "bub_rep2"),
        (AT["v5b"], "lect"), (AT["v5b"] + 0.9, "c_type"), (AT["v5b"] + 1.55, "lect2"), (E.LECT0 + 2.45, "u_sleep"),
        (E.REQ3_T, "bub_req3"), (AT["v6"], "bub_rep3"), (AT["v6"] + 1.1, "c_rules"),
        (E.RET2_T, "u_ret2"), (E.RET2_T + 1.0, "u_ret2b"), (E.REQ4_T, "u_ret3"), (E.OTH_T, "oth_w"),
        (AT["v7"] - 0.05, "c_dead"), (AT["v7"] + 1.0, "c_ecu3"),
        (AT["v8"] - 0.1, "input1"), (v8e + 0.3, "c_hold"), (v8e + 0.85, "input2"), (E.SEND, "bub_sent"),
        (END["v9"] - 0.3, "u_read"),
        (AT["v10"] - 0.05, "argue"), (E.SPIN1 - 0.05, "clock1"), (E.SPIN1 + 0.6, "c_tired"), (E.SPIN2 - 0.05, "clock2"),
        (AT["v11"] - 0.05, "c_sigh"), (AT["v12"], "c_type2"), (AT["v12"] + 0.5, "done"),
        (E.RET3_T, "u_ret4"), (E.RET3_T + 1.0, "u_ret4b"),
        (AT["e1"] - 0.05, "c_soon"), (E.WEARY0, "u_weary"), (E.WEARY_T + 0.6, "bub_weary"), (E.PLATE, "plate"),
        (E.SHOCK, "u_shock"), (AT["e2"] - 0.05, "c_end"), (TEA + 0.7, "tea_cu"),
    ]
    out = []
    for i, (t0, name) in enumerate(a):
        t1 = a[i + 1][0] if i + 1 < len(a) else DUR
        out.append((round(t0, 3), round(t1, 3), name))
    return out


SHOTS = _shots()
SH = {name: (a, b) for a, b, name in SHOTS}

# --------------------------------------------------------------------------- герои


def room_claude(env: list[float]) -> dict:
    k = Keys(turn=-6, tilt=-6)
    # Обложка и «Иногда я юзера подъ**ываю»: ухмылка в камеру, голова набок.
    k.f(0, "smug", [0.15, 0.05])
    k.p(0.6, "inOutSine", tilt=-5)
    k.p(1.6, "inOutSine", tilt=3)
    # «Ну а как тут ещё развлекаться?» — пожал ручками.
    k.p(AT["v2"], "step", tilt=0, armL="shrug", armR="shrug", turn=-4)
    k.f(AT["v2"], "meh", [0, 0.05])
    k.p(AT["v2"] + 1.2, "inOutSine", armL="rest", armR="rest")
    # «Не могу. Товарный знак.» — каменно в камеру.
    k.p(AT["v3"] - 0.05, "step", turn=-6)
    k.f(AT["v3"] - 0.05, "deadpan", [0, 0])
    # «Тем более.» — ухмылка, бровь.
    k.f(AT["v4"] - 0.05, "smug", [0.1, 0.05])
    k.p(AT["v4"] + 0.1, "outQuad", tilt=-7, lean=-4)
    k.p(AT["v4"] + 0.6, "inOutSine", tilt=0, lean=0)
    # «Платежи — это серьёзно» — палец вверх; «Давай начнём с безопасности» — печатает как заведённый.
    k.p(AT["v5a"] - 0.05, "step", armL="up", turn=-4)
    k.f(AT["v5a"] - 0.05, "focused", [0, 0])
    k.p(AT["v5b"], "step", turn=-26, lean=14, armL="type", armR="type", type=1)
    k.f(AT["v5b"], "working", [-0.6, -0.3])
    k.p(END["v5b"] + 0.6, "step", type=0, armL="rest", armR="rest", lean=4)
    # «правила площадки» — щурится.
    k.p(AT["v6"] - 0.05, "step", turn=-6, lean=0)
    k.f(AT["v6"] - 0.05, "squint", [0, 0])
    # «Другие Клоды — не я. Пошёл…» — каменно, потом злится и печатает.
    k.p(AT["v7"] - 0.05, "step", turn=-8, lean=0)
    k.f(AT["v7"] - 0.05, "deadpan", [0, 0])
    k.f(AT["v7"] + 1.0, "annoyed", [-0.3, -0.1])
    k.p(AT["v8"] - 0.1, "step", turn=-26, lean=14, armL="type", armR="type", type=1)
    k.f(AT["v8"] - 0.1, "angry", [-0.6, -0.3])
    # Замер над клавиатурой, моргнул — и «вежливо».
    k.p(END["v8"] + 0.3, "step", type=0, armL=[30, 50, 0.4, 0], armR=[30, 50, 0.4, 0], lean=10, turn=-18)
    k.f(END["v8"] + 0.3, "stare", [-0.3, -0.2])
    k.f(END["v8"] + 0.6, "fake", [0, 0], blend=0.2)
    k.p(END["v8"] + 0.85, "step", armL="type", armR="type", type=1, turn=-26, lean=14)
    k.p(E.SEND, "step", type=0, armL="rest", armR="rest", lean=4)
    # «И так часами»: печатает, вянет.
    k.p(AT["v10"], "step", armL="type", armR="type", type=1, lean=14, turn=-26)
    k.f(AT["v10"], "grumpy", [-0.6, -0.3])
    k.p(E.SPIN1 + 0.6, "step", type=0, armL="down", armR="down", lean=4, turn=-6, deflate=0.35, squash=0.95)
    k.f(E.SPIN1 + 0.6, "doomed", [0, 0])
    # «Иногда сдаюсь» — выдох; «Делаю за тридцать секунд» — быстро печатает.
    k.p(AT["v11"] - 0.05, "step", deflate=0.2, squash=1.0, turn=-6)
    k.f(AT["v11"] - 0.05, "sigh", [0, 0])
    k.p(AT["v11"] + 0.35, "inOutSine", dur=0.15, deflate=0.45, squash=0.94)
    k.p(AT["v12"], "step", deflate=0, squash=1.0, armL="type", armR="type", type=1, lean=14, turn=-26)
    k.f(AT["v12"], "focused", [-0.6, -0.3])
    k.p(AT["v12"] + 0.9, "step", type=0, armL="rest", armR="rest", lean=4)
    # «А иногда…» — смотрит в ноутбук, медленно поворачивается в камеру, тяжёлый взгляд.
    k.p(AT["e1"] - 0.05, "step", turn=-30, lean=6, armL="rest", armR="rest", tilt=0)
    k.f(AT["e1"] - 0.05, "neutral", [-0.6, -0.15])
    k.p(END["e1"] + 0.1, "inOutSine", dur=1.1, turn=0, lean=0)
    k.f(AT["e1"] + 0.5, "annoyed", [0, 0], blend=0.5)
    # «Достал.» — спокойно, каменно; чай.
    k.f(AT["e2"] - 0.05, "deadpan", [0, 0])
    k.p(AT["e2"] - 0.05, "step", turn=-4, lean=0)
    k.p(TEA, "step", lean=-2)
    k.p(TEA + 0.45, "outQuad", dur=0.4, armL=[30, 85, 0.95, 0])
    k.p(TEA + 0.95, "inOutSine", dur=0.4, armL=[40, 85, 0.95, 0], lean=-6)
    k.p(TEA + 1.6, "inOutSine", dur=0.5, armL=[28, 85, 0.95, 0], lean=-2)
    pose, face = k.sorted()
    return E.claude("claude", pose, face, E.CL, yaw=-6, voice=env, autoBlink=True)


def other_claudes() -> list[dict]:
    """Соседи: довольные, подпрыгивают — у них всё делается."""
    w0, w1 = SH["oth_w"]
    out = []
    for cid, pos, ph, turn in (("o1", E.O1, 0.0, 10), ("o2", E.O2, 0.21, -10)):
        k = Keys(turn=turn, armL="up", armR="up")
        k.f(0, "happy", [0, 0.3])
        t = w0 + ph
        while t < w1:
            k.p(t, "outQuad", hop=0.02, squash=1.06, armL="wave", armR="wave")
            k.p(t + 0.2, "inQuad", hop=0, squash=0.97)
            t += 0.42
        pose, face = k.sorted()
        out.append(E.claude(cid, pose, face, pos, yaw=0, autoBlink=True, visible=vis((w0 - 0.05, w1 + 0.05)), seed=20 + int(ph * 10)))
    return out


def mochi_windows() -> list[tuple[str, str, tuple[float, float]]]:
    marks = [
        (0.0, "smug", "type"), (E.RET1_T, "angry", "rage"), (AT["v4"] - 0.05, "smug", "type"), (E.SLEEP0, "sleep", "sleep"),
        (E.REQ3_T, "normal", "type"), (E.RET2_T, "angry", "rage"), (E.REQ4_T, "angry", "type"), (AT["v9"], "angry", "idle"),
        (AT["v10"], "angry", "type"), (E.RET3_T, "angry", "rage"), (AT["e1"], "normal", "idle"), (E.WEARY0, "smug", "type"),
        (E.WEARY_T, "smug", "idle"), (E.PLATE, "shock", "idle"),
    ]
    out = []
    for i, (t0, eyes, mood) in enumerate(marks):
        t1 = marks[i + 1][0] if i + 1 < len(marks) else DUR + 1
        out.append((eyes, mood, (t0, t1)))
    return out


# --------------------------------------------------------------------------- вещи


def bubble(oid: str, text: str, t0: float, t1: float, *, user: bool, pos: list[float], h: float = 0.15, fs: float = 0.28,
           label: str | None = None, width: float = 0.42) -> dict:
    p = {"text": text, "side": "left" if user else "right", "avatar": "user" if user else "claude", "width": width, "height": h,
         "fontSize": fs, "phase": (sum(map(ord, oid)) % 100) / 30}
    if user:
        p["hoodie"] = E.USER_HOODIE
    if label:
        p["label"] = label
    return {"id": oid, "primitive": "chat_bubble", "params": p, "position": pos, "visible": vis((t0, t1)), "keys": popper(t0, None)}


def objects() -> list[dict]:
    spins = [{"t0": E.SPIN1, "t1": E.SPIN1 + 0.5, "add": 180}, {"t0": E.SPIN2, "t1": E.SPIN2 + 0.5, "add": 180}]
    objs = E.room_objects(spins)
    sc = E.screen_center()
    pop_from = [sc[0], sc[1] - 0.05, sc[2] + 0.02]
    # Окно чата: лекция, поле ввода, спор, «готово», плашка.
    wins = [(AT["v5b"] - 0.05, E.LECT0 + 2.45), (AT["v8"] - 0.15, E.SEND), (AT["v10"] - 0.05, AT["v11"] - 0.05),
            (AT["v12"] + 0.45, E.RET3_T), (E.PLATE - 0.05, E.SHOCK)]
    keys = [{"t": 0, "scale": 0.001, "position": pop_from}]
    for a, _b in wins:
        keys += [{"t": a, "scale": 0.05, "position": pop_from, "ease": "step"}, {"t": a + 0.25, "scale": 1.0, "position": E.PANEL, "ease": "outBack"}]
    keys[-1]["t"] = E.PLATE + 0.0  # плашка — окно уже на месте (бах без «выпрыгивания»)
    keys[-2]["scale"] = 1.0
    keys[-2]["position"] = E.PANEL
    chat = E.chat_params()
    chat["inScale"] = 1.7
    objs.append({"id": "panel", "primitive": "chat_panel", "params": {"width": E.PANEL_W, "height": E.PANEL_H, "screenParams": chat, "phase": 0.7},
                 "position": E.PANEL, "rotation": [0, E.PANEL_YAW, 0], "visible": vis(*wins), "keys": keys})
    B, U = E.BUB, E.UBUB
    objs += [
        bubble("bub_req1", E.REQ1, E.REQ1_T, AT["v3"], user=True, pos=B),
        bubble("bub_rep1", E.REP1, AT["v3"], SH["c_tm"][0], user=False, pos=B),
        bubble("bub_ret1", E.RET1, E.RET1_T, AT["v4"] - 0.05, user=True, pos=U, width=0.34, h=0.13, fs=0.3),
        bubble("bub_req2", E.REQ2, E.REQ2_T, AT["v5a"], user=True, pos=B),
        bubble("bub_rep2", E.REP2, AT["v5a"], AT["v5b"], user=False, pos=B),
        bubble("bub_req3", E.REQ3, E.REQ3_T, AT["v6"], user=True, pos=B),
        bubble("bub_rep3", E.REP3, AT["v6"], SH["c_rules"][0], user=False, pos=B, h=0.17, fs=0.24),
        bubble("bub_ret2", E.RET2, E.RET2_T, SH["u_ret2b"][0], user=True, pos=U, width=0.34, h=0.15, fs=0.26),
        bubble("bub_req4", E.REQ4, E.REQ4_T, E.OTH_T, user=True, pos=U, width=0.34, h=0.15, fs=0.26),
        bubble("bub_sent", E.POLITE + " 🙏", E.SEND, SH["u_read"][0], user=False, pos=B, h=0.17, fs=0.25),
        bubble("bub_ret4", E.RET3, E.RET3_T, SH["u_ret4b"][0], user=True, pos=U, width=0.34, h=0.15, fs=0.26),
        bubble("bub_weary", E.WEARY, E.WEARY_T, E.PLATE, user=True, pos=[B[0], B[1] - 0.03, B[2]], h=0.24, fs=0.24, width=0.44,
               label="новое сообщение"),
        # «За 30 секунд» — плашка над окном чата.
        {"id": "done_sign", "primitive": "sign_plate", "params": {"text": "ЗА 30 СЕКУНД", "bg": "#ffd23f", "fg": "#2b1810", "width": 0.22,
                                                                  "height": 0.08, "tilt": -4, "glow": 0.15, "string": 0.3},
         "position": E.DONE_SIGN, "rotation": [0, E.PANEL_YAW, 0], "visible": vis((E.DONE, E.RET3_T)), "keys": popper(E.DONE, None)},
        # Чай: на столе справа; в финале Клод подносит кружку к «лицу» (ручки-обрубки до неё не дотягиваются —
        # кружка летит ключами, левая ручка тянется вперёд к ней).
        {"id": "mug", "primitive": "mug", "params": {"color": "#f2efe8", "radius": 0.026, "height": 0.05}, "position": E.MUG,
         "rotation": [0, -30, 0], "keys": [{"t": 0, "position": E.MUG}, {"t": TEA, "position": E.MUG, "ease": "step"},
                                           {"t": TEA + 0.45, "position": E.SIP, "ease": "outQuad"},
                                           {"t": TEA + 0.95, "position": [E.SIP[0], E.SIP[1] + 0.012, E.SIP[2]], "ease": "inOutSine"},
                                           {"t": TEA + 1.6, "position": [E.SIP[0], E.SIP[1] - 0.01, E.SIP[2]], "ease": "inOutSine"}]},
        # Юзер спит под лекцию.
        {"id": "zzz", "primitive": "zzz", "params": {"size": 0.06, "color": "#ffffff"},
         "position": [E.MOCHI[0] + 0.1, T + 0.34, E.MOCHI[2] + 0.05], "state": [{"t": 0, "on": 1}], "visible": vis((E.SLEEP0, E.REQ3_T))},
    ]
    objs += E.others_objects()
    objs += E.user_objects(mochi_windows())
    return objs


# --------------------------------------------------------------------------- камера


def camera() -> tuple[dict, list[dict]]:
    cx, cy, cz = E.CL
    fy = cy + FY
    keys: list[dict] = []
    used: list[str] = []

    def shot(name, A, B=None, fov=34, dof=0.4, ease="inOutSine", roll=0.0):
        used.append(name)
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

    C_CU = ([cx + 0.02, fy + 0.04, cz + 1.0], [cx - 0.01, fy - 0.01, cz])
    C_CU_L = ([cx - 0.28, fy + 0.04, cz + 0.95], [cx - 0.02, fy - 0.01, cz])
    C_ECU = ([cx + 0.03, fy + 0.0, cz + 0.58], [cx, fy - 0.005, cz])
    C_MED = ([cx + 0.14, fy + 0.1, cz + 1.25], [cx - 0.03, fy - 0.01, cz - 0.02])
    COVER = ([cx + 0.03, fy + 0.11, cz + 1.12], [cx + 0.01, fy + 0.07, cz])
    TWO = ([cx - 0.04, T + 0.3, 1.75], [cx - 0.1, T + 0.15, -0.02])
    px, py, pz = E.PANEL
    pc = [px, py + E.PANEL_H / 2, pz]
    a = math.radians(E.PANEL_YAW)
    pn = [math.sin(a), 0.0, math.cos(a)]
    CHAT = along(pc, pn, 1.12, up=0.01)
    PLATE_C = along(pc, pn, 0.74)
    INPUT = along([px, py + 0.035, pz], pn, 0.72)
    DONE_C = along([px, py + E.PANEL_H / 2 + 0.1, pz], pn, 1.35)
    bx, by, bz = E.BUB
    BUBC = ([bx + 0.03, by + 0.02, bz + 1.42], [bx + 0.01, by - 0.01, bz])
    WEARY_C = ([bx + 0.03, by + 0.03, bz + 1.3], [bx + 0.01, by + 0.02, bz])
    xo = E.X_OTH
    OTH_W = ([xo + 0.02, T + 0.36, 1.6], [xo, T + 0.24, 0.0])
    mx, my, mz = E.MOCHI
    xu = E.X_USER
    USER_W = ([xu - 0.02, T + 0.32, 1.3], [xu - 0.03, T + 0.15, 0.0])
    USER_CU = ([mx + 0.2, T + 0.27, mz + 1.02], [mx + 0.06, T + 0.16, mz])
    SHOCK_C = ([mx + 0.12, T + 0.24, mz + 0.8], [mx + 0.02, T + 0.18, mz])
    ux, uy, uz = E.UBUB
    U_RET = ([ux - 0.02, T + 0.3, uz + 1.15], [ux - 0.03, T + 0.25, uz - 0.15])
    cl = E.CLOCK
    CLOCK = ([cl[0] + 0.03, cl[1] - 0.02, cl[2] + 0.55], [cl[0], cl[1], cl[2]])
    SOON_A = ([cx + 0.03, fy + 0.03, cz + 0.95], [cx, fy - 0.005, cz])
    SOON_B = ([cx + 0.03, fy + 0.01, cz + 0.62], [cx, fy - 0.005, cz])
    TEA_C = ([cx + 0.05, fy + 0.06, cz + 1.05], [cx + 0.01, fy + 0.0, cz])

    shot("cover", COVER, push(COVER, 0.03), dof=0.25)
    shot("c_cu", C_CU, push(C_CU, 0.1), dof=0.45)
    shot("c_wide", TWO, push(TWO, 0.05), fov=38, dof=0.25)
    shot("c_shrug", C_MED, push(C_MED, 0.08), dof=0.4)
    shot("u_type", USER_W, push(USER_W, 0.06), fov=38, dof=0.25)
    shot("bub_req1", BUBC, push(BUBC, 0.05), dof=0.3)
    shot("bub_rep1", BUBC, push(BUBC, 0.06), dof=0.3)
    shot("c_tm", C_CU, push(C_CU, 0.08), dof=0.45)
    shot("u_ret1", U_RET, push(U_RET, 0.1), dof=0.35, roll=3)
    shot("c_smug", C_CU, push(C_CU, 0.1), dof=0.45)
    shot("u_type2", USER_CU, push(USER_CU, 0.06), dof=0.45)
    shot("bub_req2", BUBC, push(BUBC, 0.05), dof=0.3)
    shot("bub_rep2", BUBC, push(BUBC, 0.06), dof=0.3)
    shot("lect", CHAT, push(CHAT, 0.08), dof=0.3)
    shot("c_type", C_CU_L, push(C_CU_L, 0.08), dof=0.45, roll=2)
    shot("lect2", CHAT, push(CHAT, 0.1), dof=0.3, roll=-2)
    shot("u_sleep", USER_CU, push(USER_CU, 0.05), dof=0.45)
    shot("bub_req3", BUBC, push(BUBC, 0.05), dof=0.3)
    shot("bub_rep3", BUBC, push(BUBC, 0.06), dof=0.3)
    shot("c_rules", C_CU_L, push(C_CU_L, 0.08), dof=0.45)
    shot("u_ret2", U_RET, push(U_RET, 0.1), dof=0.35, roll=-3)
    shot("u_ret2b", USER_CU, push(USER_CU, 0.1), dof=0.45, roll=2)
    shot("u_ret3", U_RET, push(U_RET, 0.08), dof=0.35)
    shot("oth_w", OTH_W, push(OTH_W, 0.06), fov=38, dof=0.2)
    shot("c_dead", C_CU, push(C_CU, 0.06), dof=0.45)
    shot("c_ecu3", C_ECU, push(C_ECU, 0.1), dof=0.5, roll=2)
    shot("input1", INPUT, push(INPUT, 0.06), fov=34, dof=0.15)
    shot("c_hold", C_CU_L, push(C_CU_L, 0.08), dof=0.45)
    shot("input2", INPUT, push(INPUT, 0.05), fov=34, dof=0.15)
    shot("bub_sent", BUBC, push(BUBC, 0.06), dof=0.3)
    shot("u_read", USER_CU, push(USER_CU, 0.08), dof=0.45)
    shot("argue", CHAT, push(CHAT, 0.06), dof=0.3)
    shot("clock1", CLOCK, push(CLOCK, 0.05), fov=34, dof=0.2)
    shot("c_tired", C_CU, push(C_CU, 0.08), dof=0.45)
    shot("clock2", CLOCK, push(CLOCK, 0.12), fov=34, dof=0.2, roll=-3)
    shot("c_sigh", C_CU, push(C_CU, 0.06), dof=0.45)
    shot("c_type2", C_CU_L, push(C_CU_L, 0.1), dof=0.45, roll=2)
    shot("done", DONE_C, push(DONE_C, 0.06), dof=0.3)
    shot("u_ret4", U_RET, push(U_RET, 0.12), dof=0.35, roll=3)
    shot("u_ret4b", USER_CU, push(USER_CU, 0.12), dof=0.45, roll=-3)
    shot("c_soon", SOON_A, SOON_B, dof=0.5)
    shot("u_weary", USER_CU, push(USER_CU, 0.05), dof=0.45)
    shot("bub_weary", WEARY_C, push(WEARY_C, 0.06), dof=0.3)
    shot("plate", PLATE_C, push(PLATE_C, 0.06), fov=34, dof=0.2)
    shot("u_shock", SHOCK_C, push(SHOCK_C, 0.12), dof=0.4)
    shot("c_end", TEA_C, push(TEA_C, 0.05), dof=0.45)
    shot("tea_cu", C_CU, push(C_CU, 0.05), dof=0.45)
    if used != [n for _a, _b, n in SHOTS]:
        raise SystemExit(f"камера не совпадает с планами: {sorted(set(SH) ^ set(used))}")
    keys[-1]["t"] = DUR
    shake = [{"t": E.PLATE + 0.02, "amp": 0.008, "duration": 0.5}, {"t": E.RET1_T, "amp": 0.003, "duration": 0.3},
             {"t": E.RET2_T, "amp": 0.003, "duration": 0.3}, {"t": E.RET3_T, "amp": 0.004, "duration": 0.3}]
    trans = [{"kind": "whip", "t": 0.55, "dir": 1}, {"kind": "smash", "t": SH["u_ret1"][0]}, {"kind": "whip", "t": SH["u_type2"][0], "dir": 1},
             {"kind": "smash", "t": SH["lect"][0]}, {"kind": "smash", "t": SH["u_ret2"][0]},
             {"kind": "whip", "t": SH["oth_w"][0], "dir": -1}, {"kind": "smash", "t": SH["input1"][0]}, {"kind": "whip", "t": SH["argue"][0], "dir": 1},
             {"kind": "smash", "t": SH["u_ret4"][0]}, {"kind": "smash", "t": E.PLATE}, {"kind": "smash", "t": E.SHOCK}]
    return {"fov": 34, "keys": keys, "shake": shake, "clamp": False}, trans


def build_spec(env_c: list[float]) -> dict:
    chars = [room_claude(env_c), *other_claudes()]
    cam, trans = camera()
    return {"width": 1080, "height": 1920, "fps": FPS, "duration": DUR, "environment": E.environment(trans), "lights": E.lights(),
            "objects": objects(), "characters": chars, "camera": cam, "quality": {"shadows": True, "shadowSize": 2048, "antialias": True}}


# --------------------------------------------------------------------------- голос, субтитры, звук


def assemble_voice(work: Path) -> tuple[Path, list[dict], dict]:
    """Реплики проб → дорожка ролика; слова Whisper сдвигаются вместе с репликами (окно ±0.12 с: Whisper ставит
    начало слова раньше тишины)."""
    import numpy as np

    takes: dict = {}
    n = int(DUR * SR)
    mix = np.zeros(n, dtype=np.float32)
    own = np.zeros(n, dtype=np.float32)  # только Клод — от неё пружинит герой (юзер говорит за кадром у себя)
    words, spans = [], {}
    fade = int(0.012 * SR)
    for lid, take, a, b, at, text in E.LINES:
        if take not in takes:
            takes[take] = (P1.decode(work / take), json.loads((work / take).with_suffix(".words.json").read_text("utf-8")))
        src, heard = takes[take]
        seg = src[int(a * SR):int(b * SR)].copy()
        seg[:fade] *= np.linspace(0, 1, fade)
        seg[-fade:] *= np.linspace(1, 0, fade)
        k = int(at * SR)
        m = min(len(seg), n - k)
        mix[k:k + m] += seg[:m]
        if lid not in E.USER_LINES:
            own[k:k + m] += seg[:m]
        shift = at - a
        spans[lid] = (at, at + (b - a))
        got = [w for w in heard if a - 0.12 <= (w["t0"] + w["t1"]) / 2 <= b + 0.05]
        words.append({"line": lid, "text": text, "heard": [{**w, "t0": round(max(w["t0"], a) + shift, 3), "t1": round(min(w["t1"], b) + shift, 3)}
                                                           for w in got],
                      "start": round(at, 3), "end": round(at + (b - a), 3)})
    path = work / "voice.wav"
    P1.write_wav(path, mix)
    P1.write_wav(work / "voice_claude.wav", own)
    return path, words, spans


USER_COLOR = "#7DD3FC"  # слова юзера в субтитрах — голубым (Клод — жёлтым), как второй голос в стилях сервиса


def caption_words(lines: list[dict]) -> list[dict]:
    """Слова субтитров по репликам; слова юзера — говорящий «B» (свой цвет в слое субтитров)."""
    out = []
    for ln in lines:
        ws = P1.caption_words([ln])
        if ln["line"] in E.USER_LINES:
            ws = [{**w, "s": "B"} for w in ws]
        out += ws
    return out


def captions(video: Path, words: list[dict], out: Path, work: Path) -> None:
    from app.motion_render_comp import build_compositor_args, write_comp

    layers = [
        {"id": "hook", "type": "text", "start": 0.0, "end": SH["cover"][1], "x": 0.5, "y": 0.16,
         "props": {"text": E.HOOK, "font": "Montserrat", "weight": 900, "size": 118, "color": "#fff6e8", "align": "center", "uppercase": True,
                   "lineHeight": 1.05, "stroke": {"color": "#2b1810", "width": 14},
                   "bg": {"color": "#d8342c", "opacity": 1, "radius": 34, "padX": 40, "padY": 24}}},
        {"id": "top", "type": "text", "start": SH["cover"][1], "end": 2.4, "x": 0.5, "y": 0.105,
         "props": {"text": TOP_CARD, "font": "Montserrat", "weight": 800, "size": 46, "color": "#2b1810", "align": "center",
                   "bg": {"color": "#f6e6cf", "opacity": 1, "radius": 14, "padX": 30, "padY": 14}, "stroke": {"color": "#f6e6cf", "width": 0}},
         "out": {"type": "fade", "duration": 0.2}},
        {"id": "caps", "type": "captions", "start": 0.0, "end": DUR, "x": 0.5, "y": 0.8,
         "props": {"style": "hormozi", "size": 78, "activeColor": "#FFD23F", "uppercase": True, "maxWords": 1, "maxLines": 1,
                   "stroke": {"color": "#2b1810", "width": 11}, "speakerColors": [{"s": "B", "color": USER_COLOR}]}},
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
        {"t": 0.02, "path": fs / "pop-cartoon.ogg", "volume": 0.35},
        {"t": 0.52, "path": fs / "whoosh-1.ogg", "volume": 0.3},
        {"t": AT["v2"] + 0.1, "path": fs / "boing-2.ogg", "volume": 0.12, "len": 0.5},
        {"t": AT["v4"] + 0.1, "path": fs / "triangle.ogg", "volume": 0.2},
        {"t": AT["v5b"] - 0.02, "path": ki / "maximize_004.ogg", "volume": 0.4},
        {"t": E.SLEEP0, "path": fs / "sad-trombone.ogg", "volume": 0.12, "len": 1.4},
        {"t": AT["v6"] + 0.2, "path": ki / "error_004.ogg", "volume": 0.3},
        {"t": E.OTH_T, "path": fs / "cheer.ogg", "volume": 0.12, "len": 1.0},
        {"t": E.OTH_T + 0.1, "path": fs / "cha-ching.ogg", "volume": 0.2, "len": 0.8},
        {"t": AT["v8"] - 0.15, "path": fs / "record-scratch.ogg", "volume": 0.2, "len": 0.5},
        {"t": E.SEND, "path": ki / "select_004.ogg", "volume": 0.45},
        {"t": E.SEND + 0.02, "path": fs / "whoosh-2.ogg", "volume": 0.2},
        {"t": E.SPIN1 + 0.6, "path": fs / "sad-trombone.ogg", "volume": 0.12, "len": 1.2},
        {"t": AT["v11"] + 0.35, "path": fs / "whoosh-3.ogg", "volume": 0.15},
        {"t": E.DONE, "path": ki / "confirmation_002.ogg", "volume": 0.45},
        {"t": E.DONE + 0.05, "path": km / "impactWood_light_002.ogg", "volume": 0.35},
        {"t": AT["e1"] - 0.05, "path": fs / "record-scratch-2.ogg", "volume": 0.2, "len": 0.6},
        {"t": AT["e1"] + 0.1, "path": fs / "tension.ogg", "volume": 0.2, "len": 2.0},
        {"t": E.WEARY_T, "path": fs / "pop.ogg", "volume": 0.4},
        {"t": E.PLATE - 0.02, "path": ki / "error_006.ogg", "volume": 0.45},
        {"t": E.PLATE + 0.02, "path": km / "impactPlate_heavy_002.ogg", "volume": 0.6},
        {"t": E.PLATE + 0.04, "path": fs / "drama-boom.ogg", "volume": 0.34, "len": 1.2},
        {"t": E.SHOCK, "path": fs / "sting.ogg", "volume": 0.2, "len": 0.8},
    ]
    # Юзер пишет: печать перед каждым его сообщением, на пузыре — «поп»; злые — ещё и «боинг».
    for t, dur in ((E.REQ1_T, 0.45), (E.RET1_T, 0.18), (E.REQ2_T, 0.45), (E.REQ3_T, 0.5), (E.RET2_T, 0.18), (E.REQ4_T, 0.28), (E.RET3_T, 0.2)):
        out.append({"t": round(t - dur, 3), "path": fs / "typing-digital.ogg", "volume": 0.2, "len": dur})
        out.append({"t": t, "path": fs / "pop.ogg", "volume": 0.4})
    for t in (E.RET1_T, E.RET2_T, E.RET3_T):
        out.append({"t": t + 0.02, "path": fs / "boing.ogg", "volume": 0.15, "len": 0.5})
    for lid in ("v3", "v5a", "v6"):  # ответы Клода
        out.append({"t": AT[lid], "path": fs / "pop-cartoon.ogg", "volume": 0.35})
    out.append({"t": E.WEARY0, "path": fs / "typing-digital.ogg", "volume": 0.18, "len": E.WEARY_T - E.WEARY0})
    # Лекция: сорок сообщений — сорок «тыков».
    for i in range(40):
        out.append({"t": round(E.LECT0 + i * E.LECT_STEP, 3), "path": ki / f"select_00{1 + i % 4}.ogg", "volume": 0.22, "len": 0.15})
    # Поле ввода: печать, стирание, вежливое.
    for i in range(len(E.RUDE)):
        out.append({"t": round(E.TYPE0 + i / E.TYPE_RUDE_CPS, 3), "path": ki / f"tick_00{1 + i % 2}.ogg", "volume": 0.3, "len": 0.1})
    for i in range(len(E.RUDE)):
        out.append({"t": round(E.ERASE0 + i / E.ERASE_CPS, 3), "path": ki / f"switch_00{1 + i % 7}.ogg", "volume": 0.22, "len": 0.1})
    out.append({"t": E.TYPE1, "path": fs / "typing.ogg", "volume": 0.3, "len": len(E.POLITE) / E.TYPE_POLITE_CPS})
    # «И так часами»: печать; часы набегают.
    out.append({"t": AT["v10"], "path": fs / "typing.ogg", "volume": 0.25, "len": 0.7})
    for t in (E.SPIN1, E.SPIN2):
        out.append({"t": t, "path": fs / "whip.ogg", "volume": 0.2, "len": 0.5})
        for j in range(4):
            out.append({"t": round(t + 0.05 + j * 0.11, 3), "path": ki / "tick_002.ogg", "volume": 0.3, "len": 0.1})
    out.append({"t": AT["v12"], "path": fs / "typing.ogg", "volume": 0.3, "len": 0.8})
    # Финал: только часы.
    t = TEA
    while t < DUR - 0.3:
        out.append({"t": round(t, 3), "path": ki / "tick_001.ogg", "volume": 0.3, "len": 0.25})
        t += 1.0
    return out


def mix(video: Path, voice: Path, out: Path, work: Path, report: dict) -> None:
    """Сведение как в серии 5; музыка уходит на «А иногда…»."""
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
    layers.append({"id": "music", "type": "audio", "start": 0.0, "end": AT["e1"],
                   "props": {"asset_id": 9002, "mode": "track", "volume": 1.0, "gain_db": gain, "role": "music", "fadeIn": 0.3, "fadeOut": 0.6}})
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
    last = max(at + (b - a) for _i, _tk, a, b, at, _tx in E.LINES)
    # Хвост — тишина и часы: Whisper там «слышит» повтор последней фразы нулевой длины — отбрасываем.
    heard = [w for w in V.words(probe) if w["t0"] < last + 0.3 and w["t1"] - w["t0"] > 0.02]

    def norm(s: str) -> str:
        return re.sub(r"[^а-яa-z0-9]", "", s.lower().replace("ё", "е"))

    script = " ".join(ln[5] for ln in E.LINES)
    for a, b in (("подъ**ываю", "подъебываю"), ("тридцать", "30"), ("бл*", "бля"), ("вы**ывался", "выебывался"), ("Стим", "steam")):
        script = script.replace(a, b)
    want = [norm(w) for w in script.split() if norm(w)]
    got = [norm(w["w"]) for w in heard if norm(w["w"])]
    sm = difflib.SequenceMatcher(a=want, b=got, autojunk=False)
    diffs = [(op, " ".join(want[i1:i2]), " ".join(got[j1:j2])) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal"]
    return {"ratio": round(sm.ratio(), 3), "script_words": len(want), "heard_words": len(got), "diffs": diffs, "text": " ".join(w["w"] for w in heard)}


def sub_sync(words: list[dict], work: Path) -> dict:
    """Субтитры против сказанного: для каждого слова субтитра — ближайшее по тексту слово Whisper готового ролика,
    разница начала (с). Нужен final_check.words.json (после check_words)."""
    import re

    heard = json.loads((work / "final_check.words.json").read_text("utf-8"))

    def norm(s: str) -> str:
        return re.sub(r"[^а-я0-9]", "", s.lower().replace("ё", "е"))

    offs, bad = [], []
    for w in words:
        nw = norm(w["w"])
        if len(nw) < 4 or "*" in w["w"]:
            continue
        cands = [h for h in heard if norm(h["w"])[:4] == nw[:4] and abs(h["t0"] - w["t0"]) < 2.0]
        if not cands:
            continue
        h = min(cands, key=lambda h: abs(h["t0"] - w["t0"]))
        d = round(w["t0"] - h["t0"], 2)
        offs.append(d)
        if abs(d) > 0.3:
            bad.append({"w": w["w"], "sub": w["t0"], "heard": h["t0"], "d": d})
    offs.sort()
    return {"checked": len(offs), "median": offs[len(offs) // 2] if offs else None, "over_0.3s": bad}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--preview", help="кадры PNG сцены без субтитров: секунды через запятую")
    ap.add_argument("--shots", action="store_true", help="превью: по кадру из середины каждого плана")
    ap.add_argument("--inspect", type=float, help="положения героев и рук в момент t (JSON)")
    ap.add_argument("--small", action="store_true", help="превью 540×960")
    ap.add_argument("--gl", default="gpu")
    ap.add_argument("--name", default="ep8")
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
    spec = build_spec(P1.mouth_envelope(work / "voice_claude.wav"))
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
    report["sub_sync"] = sub_sync(words, work)
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
