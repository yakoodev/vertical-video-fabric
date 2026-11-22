"""Мем-серия 9 «Промпт на три страницы» — хореография, камера, звук и сборка (разметка голоса, простыня, чаты,
декорации — meme3d_ep9.py).

    python tools/meme3d_ep9_anim.py D:/vvf-offload/memes/ep9 --shots --small          # по кадру с каждого плана
    python tools/meme3d_ep9_anim.py D:/vvf-offload/memes/ep9 --preview 0.3,24.5       # кадры PNG без субтитров
    python tools/meme3d_ep9_anim.py D:/vvf-offload/memes/ep9 --hands                  # проверка руки с кружкой
    python tools/meme3d_ep9_anim.py D:/vvf-offload/memes/ep9                          # ролик + копия для TG + лист

    python tools/meme3d_ep9_anim.py D:/vvf-offload/memes/ep9 --tts-user               # голос юзера (платно, кэш)

v2: Клод (callum) и юзер (E.USER_VOICE) — оба голосом, по очереди, не наезжая; музыка приседает под речь. Субтитры —
только у Клода: текст юзера и так на экране (простыня, последняя строка, пузырь). ~36 планов за ~45 с:
обложка «3 СТРАНИЦЫ» → «Каждый день мне пишут промпт» → юзер печатает и читает вслух, простыня в чате (маркер на
«тридцатилетним опытом», «Ты гений»), Клод молча смотрит → «Тридцать лет опыта. Мне два года» → юзер: «шаг за шагом»,
«двести долларов чаевых», «карьера», «бабушка» → «Чаевые, кстати, ни разу не пришли» → Клод листает: стр. 2, чай
остывает, стр. 3, пар кончился, часы крутятся → последняя строка мелко (юзер бормочет), вылетает крупно → «Я прочитал
только последнюю строчку» → кнопка синеет → юзер орёт «ТЫ ГЕНИЙ» → Клод отхлёбывает: «Я знаю. Тридцать лет опыта».
Рот Клода — виземы по его слогам и огибающая только его дорожки (на голосе юзера рот закрыт).
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

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import meme3d_ep6_anim as A6  # noqa: E402
import meme3d_ep7_anim as A7  # noqa: E402
import meme3d_ep8_anim as A8  # noqa: E402
import meme3d_ep9 as E  # noqa: E402
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
MOUTH = {"width": 0.28, "y": 0.125, "color": "#1b1214"}  # рисованный рот рэп-клипа (meme3d_rap_world.MOUTH_STYLES)

# --------------------------------------------------------------------------- планы

SHOT_STARTS = [
    (0.0, "cover"), (0.6, "c_cu"),
    # Юзер печатает и читает простыню вслух; Клод молча смотрит на «…никогда не ошибаешься».
    (2.5, "u_type"), (E.PASTE, "wall_in"), (E.HL1, "wall_hl1"), (E.GENIUS_SCROLL[0], "wall_b"), (7.05, "u_smug"), (8.3, "wall_b2"),
    (9.4, "c_stare"), (10.6, "c_dead"), (12.25, "c_ecu"),
    # Юзер: «шаг за шагом… вдохни… чаевых… карьера… бабушка».
    (E.STEP_SCROLL[0], "wall_c"), (15.5, "u_rub"), (E.TIPS_SCROLL[0], "wall_d"), (18.75, "wall_d2"), (E.CAREER_SCROLL[0], "wall_e"),
    (22.0, "u_beg"), (E.GRANNY_SCROLL[0], "wall_f"),
    (24.35, "c_side"), (25.6, "c_roll"),
    (E.SCROLL0, "scroll"), (E.PAGE2, "page2"), (E.TEA1, "tea1"), (E.PAGE3, "page3"), (E.TEA2, "tea2"), (E.SPIN, "clock"), (E.TIRED, "c_tired"),
    (E.LAST, "last"), (E.ZOOM - 0.05, "zoom"), (35.85, "c_weary"), (37.1, "c_ecu2"),
    (E.SITE, "site"), (E.AT["u4"], "u_genius"), (E.TEA, "tea_sip"), (42.2, "tea_cu"), (43.6, "c_end"),
]


STEP_FPS = 15  # герой шагает позами «по двойкам»: склейки — на сетке 1/15 с, иначе новая поза приходит на 2-м кадре плана


def on_grid(t: float) -> float:
    """Ближайший шаг сетки 1/15 с, усечённый вниз до 3 знаков: ключи позы округляются до 3 знаков, и округление вверх
    (29.86667 → 29.867) сдвинуло бы новую позу на шаг позже склейки."""
    return math.floor(round(t * STEP_FPS) / STEP_FPS * 1000) / 1000


def _shots() -> list[tuple[float, float, str]]:
    a = [(on_grid(t0), name) for t0, name in SHOT_STARTS]
    return [(t0, a[i + 1][0] if i + 1 < len(a) else DUR, name) for i, (t0, name) in enumerate(a)]


SHOTS = _shots()
SH = {name: (a, b) for a, b, name in SHOTS}
WALL_WINDOWS = [(SH["wall_in"][0], SH["wall_b"][1]), SH["wall_b2"], SH["wall_c"], (SH["wall_d"][0], SH["wall_e"][1]), SH["wall_f"],
                SH["page2"], SH["page3"], (SH["last"][0], SH["zoom"][1])]

# --------------------------------------------------------------------------- герой

TEA = E.TEA
SIP_UP, SIP_HOLD, SIP_DOWN = TEA + 0.35, TEA + 0.75, TEA + 0.85
SIP_LOW = [E.SIP[0], E.SIP[1] - 0.035, E.SIP[2]]  # кружка чуть ниже рта, пока говорит


def syllable_words(words: list[dict]) -> list[dict]:
    """Слова субтитров → слова с атаками слогов для визем: гласные слова поровну по его длительности."""
    out = []
    for w in words:
        n = max(1, sum(ch in H.VOWEL_VIS for ch in w["w"].lower()))
        d = max(0.05, w["t1"] - w["t0"])
        out.append({**w, "syl": [round(w["t0"] + d * i / n, 3) for i in range(n)]})
    return out


def room_claude(env: list[float], mouth_keys: list[dict]) -> dict:
    k = Keys(turn=-6, tilt=-4)
    # Обложка и «Каждый день мне пишут промпт» — ровно в камеру.
    k.f(0, "deadpan", [0.1, 0.05])
    k.p(AT["c1"], "inOutSine", tilt=0)
    # Молча смотрит, пока юзер дочитывает «…никогда не ошибаешься»; «Тридцать лет опыта.» — каменно;
    # «Мне два года.» — ещё каменнее, крупно.
    k.p(SH["c_stare"][0], "step", turn=-4, tilt=-3)
    k.f(SH["c_stare"][0], "stare", [0, 0])
    k.f(SH["c_ecu"][0], "meh", [0, 0])
    k.f(AT["c2b"] - 0.05, "deadpan", [0, 0])
    # «Чаевые, кстати, ни разу не пришли.» — в сторону, потом закатывает глаза.
    k.p(SH["c_side"][0], "step", turn=-14, tilt=4)
    k.f(SH["c_side"][0], "side", [-0.6, 0.05])
    k.f(SH["c_roll"][0], "roll", [0, 0.3])
    k.p(SH["c_roll"][0], "inOutSine", turn=-6, tilt=0)
    # Листает простыню: к ноутбуку, печатает/крутит; вянет.
    k.p(SH["scroll"][0], "step", turn=-26, lean=14, armL="type", armR="type", type=1)
    k.f(SH["scroll"][0], "focused", [-0.6, -0.3])
    k.f(SH["page3"][0], "meh", [-0.6, -0.3])
    k.p(SH["c_tired"][0], "step", type=0, armL="down", armR="down", lean=4, turn=-6, deflate=0.35, squash=0.95)
    k.f(SH["c_tired"][0], "doomed", [0, 0])
    # «Я прочитал только последнюю строчку.» — устало.
    k.p(SH["c_weary"][0], "step", deflate=0.3, squash=0.96, turn=-6, lean=2)
    k.f(SH["c_weary"][0], "sigh", [0, 0])
    k.f(SH["c_ecu2"][0], "doomed", [0, 0])
    # Кнопка: быстро печатает (за кадром).
    k.p(E.SITE - 0.3, "step", deflate=0, squash=1.0, turn=-26, lean=14, armL="type", armR="type", type=1)
    k.p(E.BLUE_AT + 0.2, "step", type=0, armL="rest", armR="rest", lean=4)
    # Чай: тянется, отхлёбывает, «Я знаю. Тридцать лет опыта» — довольно.
    # Разворот к камере — ровно на склейке (склейки на сетке шага поз, см. on_grid).
    k.p(SH["tea_sip"][0], "step", turn=-4, lean=-2, tilt=0)
    k.f(SH["tea_sip"][0], "smug", [0.1, 0.05])
    # Кружка у рта: обрубок держит её на уровне рта, на глоток — чуть выше; плавно (outQuad давал рывок 5.8 см за кадр).
    k.p(SIP_UP, "inOutSine", dur=0.45, armL=[-8, 85, 0.95, 0])
    k.p(SIP_HOLD, "inOutSine", dur=0.35, armL=[8, 85, 1.0, 0], lean=-6)
    k.p(SIP_DOWN + 0.2, "inOutSine", dur=0.3, armL=[-12, 85, 0.95, 0], lean=-2)
    k.f(AT["c5a"] - 0.05, "smug", [0, 0.05])
    k.p(AT["c5b"], "inOutSine", tilt=-6)
    pose, face = k.sorted()
    ch = H.hero(pose, face, env, None, position=E.CL, yaw=-6, width=E.W_HERO, label="Claude", char_id="claude", seed=7, glow=0.05,
                headset=True, mouth=True, mouth_keys=mouth_keys)
    ch["critter"]["mouth"] = dict(MOUTH)
    ch["autoBlink"] = True
    return ch


def mochi_windows() -> list[tuple[str, str, tuple[float, float]]]:
    # Напыщенно потирает ручки → «глубоко вдохни» (блаженно) → чаевые (снова потирает) → «карьера!» (паника, ручки вверх).
    marks = [(0.0, "smug", "type"), (E.PASTE, "smug", "rub"), (E.STEP_SCROLL[0], "happy", "idle"), (E.TIPS_SCROLL[0], "smug", "rub"),
             (SH["u_beg"][0] - 0.1, "shock", "rage"), (SH["c_side"][0], "normal", "idle"), (E.GENIUS_TYPE, "smug", "type"),
             (E.GENIUS, "happy", "rage")]
    return [(eyes, mood, (t0, marks[i + 1][0] if i + 1 < len(marks) else DUR + 1)) for i, (t0, eyes, mood) in enumerate(marks)]


# --------------------------------------------------------------------------- вещи


def objects(mug_keys: list[dict] | None = None) -> list[dict]:
    spins = [{"t0": E.SPIN + 0.05, "t1": E.SPIN + 0.85, "add": 300}]
    objs = E.room_objects(spins)
    objs.append({"id": "panel", "primitive": "chat_panel",
                 "params": {"width": E.PANEL_W, "height": E.PANEL_H, "screen": "prompt_wall", "screenParams": E.wall_params(), "phase": 0.7,
                            "px": 1000},
                 "position": E.PANEL, "rotation": [0, E.PANEL_YAW, 0], "visible": vis(*WALL_WINDOWS)})
    objs.append({"id": "site", "primitive": "chat_panel",
                 "params": {"width": E.PANEL_W, "height": E.PANEL_H, "screen": "button_site", "screenParams": E.site_params(), "phase": 1.3,
                            "px": 1000},
                 "position": E.PANEL, "rotation": [0, E.PANEL_YAW, 0], "visible": vis(SH["site"])})
    objs.append(A8.bubble("bub_genius", E.GENIUS_TEXT, E.GENIUS, TEA, user=True, pos=[E.UBUB[0] - 0.05, E.UBUB[1], E.UBUB[2]], width=0.3, h=0.13,
                          fs=0.34))
    objs.append({"id": "mug", "primitive": "mug", "params": {"color": "#f2efe8", "radius": 0.026, "height": 0.05}, "position": E.MUG,
                 "rotation": [0, -30, 0], "keys": mug_keys or default_mug_keys()})
    objs += E.user_objects(mochi_windows())
    return objs


GRIP = 0.036  # от центра кружки до ручки (по xz), м: радиус 0.026 + дуга ручки
GRIP_Y = 0.025  # высота ручки над дном
GRIP_DIR = (0.45, -0.9)  # ручка смотрит назад-вбок, к обрубку; чашка — перед корпусом, ближе к рту


def mug_keys_from_hands(samples: list[dict]) -> list[dict]:
    """Кружка в руке: с ``SIP_UP`` ручка кружки — в точке левого обрубка (по осмотру сцены покадрово), чашка перед
    корпусом; до того — на столе, к ``SIP_UP`` долетает в руку. ``samples`` — --inspect-list (t, characters.claude.hand_l)."""
    dx, dz = GRIP_DIR
    n = math.hypot(dx, dz)
    dx, dz = dx / n, dz / n
    yaw = round(math.degrees(math.atan2(-dz, dx)), 2)
    keys = [{"t": 0, "position": E.MUG, "rotation": [0, -30, 0]}, {"t": TEA, "position": E.MUG, "rotation": [0, -30, 0], "ease": "step"}]
    hold = [s for s in samples if s["t"] >= SIP_UP - 1e-6]
    for i, s in enumerate(hold):
        hx, hy, hz = s["characters"]["claude"]["hand_l"]
        k = {"t": round(s["t"], 3), "position": [round(hx - GRIP * dx, 4), round(hy - GRIP_Y, 4), round(hz - GRIP * dz, 4)], "rotation": [0, yaw, 0]}
        k["ease"] = "outQuad" if i == 0 else "linear"
        keys.append(k)
    return keys


def default_mug_keys() -> list[dict]:
    """Кружка летит к «руке» (обрубки до неё не дотягиваются — как в серии 8): вверх, глоток, чуть ниже рта."""
    return [{"t": 0, "position": E.MUG}, {"t": TEA, "position": E.MUG, "ease": "step"},
            {"t": SIP_UP, "position": E.SIP, "ease": "outQuad"},
            {"t": SIP_HOLD, "position": [E.SIP[0], E.SIP[1] + 0.012, E.SIP[2]], "ease": "inOutSine"},
            {"t": SIP_DOWN + 0.2, "position": SIP_LOW, "ease": "inOutSine"}]


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

    def along(c, n, d, up=0.0):
        return ([c[0] + n[0] * d, c[1] + n[1] * d + up, c[2] + n[2] * d], list(c))

    C_CU = ([cx + 0.02, fy + 0.04, cz + 1.0], [cx - 0.01, fy - 0.01, cz])
    C_CU_L = ([cx - 0.28, fy + 0.04, cz + 0.95], [cx - 0.02, fy - 0.01, cz])
    C_ECU = ([cx + 0.03, fy + 0.0, cz + 0.6], [cx, fy - 0.005, cz])
    C_SIDE = ([cx + 0.3, fy + 0.05, cz + 0.92], [cx + 0.01, fy - 0.01, cz])
    COVER = ([cx + 0.02, fy + 0.07, cz + 0.9], [cx, fy + 0.03, cz])
    px, py, pz = E.PANEL
    a = math.radians(E.PANEL_YAW)
    pn = [math.sin(a), 0.0, math.cos(a)]
    WALL = along([px, py + E.PANEL_H / 2, pz], pn, 0.83)
    WALL_CU = along([px, py + E.PANEL_H * 0.6, pz], pn, 0.66)
    WALL_LOW = along([px, py + E.PANEL_H * 0.32, pz], pn, 0.7)
    mx, my, mz = E.MOCHI
    xu = E.X_USER
    USER_W = ([xu - 0.02, T + 0.32, 1.3], [xu - 0.03, T + 0.15, 0.0])
    USER_CU = ([mx + 0.2, T + 0.27, mz + 1.02], [mx + 0.06, T + 0.16, mz])
    ux, uy, uz = E.UBUB
    U_RET = ([ux - 0.02, T + 0.3, uz + 1.15], [ux - 0.03, T + 0.25, uz - 0.15])
    cl = E.CLOCK
    CLOCK = ([cl[0] + 0.03, cl[1] - 0.02, cl[2] + 0.55], [cl[0], cl[1], cl[2]])
    gx, gy, gz = E.MUG
    TEA_CU = ([gx + 0.05, T + 0.1, gz + 0.34], [gx, T + 0.065, gz])
    SCROLL_TWO = ([cx + 0.17, fy + 0.07, cz + 0.98], [cx + 0.1, fy - 0.05, cz])
    TEA_C = ([cx + 0.05, fy + 0.06, cz + 1.05], [cx + 0.01, fy + 0.0, cz])
    END_C = ([cx + 0.04, fy + 0.03, cz + 0.78], [cx + 0.01, fy - 0.0, cz])

    shot("cover", COVER, push(COVER, 0.03), dof=0.25)
    shot("c_cu", C_CU, push(C_CU, 0.1), dof=0.45)
    shot("u_type", USER_W, push(USER_W, 0.08), fov=38, dof=0.25)
    shot("wall_in", WALL, push(WALL, 0.05), dof=0.2)
    shot("wall_hl1", WALL_CU, push(WALL_CU, 0.08), dof=0.2)
    shot("wall_b", WALL, push(WALL, 0.07), dof=0.2, roll=-1.5)
    shot("u_smug", USER_CU, push(USER_CU, 0.08), dof=0.45)
    shot("wall_b2", WALL_CU, push(WALL_CU, 0.1), dof=0.2, roll=1.5)
    shot("c_stare", C_ECU, push(C_ECU, 0.05), dof=0.5)
    shot("c_dead", C_CU, push(C_CU, 0.06), dof=0.45)
    shot("c_ecu", C_ECU, push(C_ECU, 0.1), dof=0.5)
    shot("wall_c", WALL, push(WALL, 0.06), dof=0.2)
    shot("u_rub", USER_CU, push(USER_CU, 0.1), dof=0.45, roll=-2)
    shot("wall_d", WALL, push(WALL, 0.07), dof=0.2)
    shot("wall_d2", WALL_CU, push(WALL_CU, 0.1), dof=0.2, roll=1.5)
    shot("wall_e", WALL, push(WALL, 0.08), dof=0.2)
    shot("u_beg", USER_CU, push(USER_CU, 0.14), dof=0.45, roll=2.5)
    shot("wall_f", WALL_CU, push(WALL_CU, 0.12), dof=0.2, roll=-1.5)
    shot("c_side", C_SIDE, push(C_SIDE, 0.08), dof=0.45)
    shot("c_roll", C_CU, push(C_CU, 0.08), dof=0.45)
    shot("scroll", SCROLL_TWO, push(SCROLL_TWO, 0.06), dof=0.35)
    shot("page2", WALL, push(WALL, 0.1), dof=0.2, roll=-2)
    shot("tea1", TEA_CU, push(TEA_CU, 0.06), dof=0.3)
    shot("page3", WALL, push(WALL, 0.12), dof=0.2, roll=2)
    shot("tea2", TEA_CU, push(TEA_CU, 0.08), dof=0.3)
    shot("clock", CLOCK, push(CLOCK, 0.12), dof=0.2, roll=-3)
    shot("c_tired", C_CU_L, push(C_CU_L, 0.06), dof=0.45)
    shot("last", WALL_LOW, push(WALL_LOW, 0.08), dof=0.2)
    shot("zoom", WALL, push(WALL, 0.14), dof=0.2)
    shot("c_weary", C_CU, push(C_CU, 0.06), dof=0.45)
    shot("c_ecu2", C_ECU, push(C_ECU, 0.08), dof=0.5)
    shot("site", WALL, push(WALL, 0.08), dof=0.2)
    shot("u_genius", U_RET, push(U_RET, 0.12), dof=0.35, roll=3)
    shot("tea_sip", TEA_C, push(TEA_C, 0.05), dof=0.45)
    shot("tea_cu", C_CU, push(C_CU, 0.06), dof=0.45)
    shot("c_end", END_C, push(END_C, 0.06), dof=0.5)
    if used != [n for _a, _b, n in SHOTS]:
        raise SystemExit(f"камера не совпадает с планами: {sorted(set(SH) ^ set(used))}")
    keys[-1]["t"] = DUR
    shake = [{"t": E.ZOOM + 0.02, "amp": 0.004, "duration": 0.35}, {"t": E.GENIUS, "amp": 0.004, "duration": 0.35}]
    trans = [{"kind": "whip", "t": SH["c_cu"][0], "dir": 1}, {"kind": "smash", "t": SH["wall_in"][0]}, {"kind": "whip", "t": SH["scroll"][0], "dir": -1},
             {"kind": "whip", "t": SH["page2"][0], "dir": 1}, {"kind": "whip", "t": SH["page3"][0], "dir": 1}, {"kind": "smash", "t": SH["zoom"][0]},
             {"kind": "smash", "t": SH["u_genius"][0]}]
    return {"fov": 34, "keys": keys, "shake": shake, "clamp": False}, trans


def build_spec(env_c: list[float], mouth_keys: list[dict], mug_keys: list[dict] | None = None) -> dict:
    cam, trans = camera()
    return {"width": 1080, "height": 1920, "fps": FPS, "duration": DUR, "environment": E.environment(trans), "lights": E.lights(),
            "objects": objects(mug_keys), "characters": [room_claude(env_c, mouth_keys)], "camera": cam,
            "quality": {"shadows": True, "shadowSize": 2048, "antialias": True}}


# --------------------------------------------------------------------------- голос, субтитры, звук


USER_GAIN_DB = 0.0  # голос юзера относительно Клода — выравнивается в assemble_voice по LUFS проб, это поправка сверху


def _lufs_of(x) -> float:
    """Громкость отрезка по RMS речи (без тишины), дБ — для выравнивания двух голосов между собой."""
    import numpy as np

    hop = SR // 50
    n = len(x) // hop * hop
    rms = np.sqrt(np.mean(np.asarray(x[:n], np.float32).reshape(-1, hop) ** 2, axis=1) + 1e-12)
    db = 20 * np.log10(rms)
    loud = db[db > db.max() - 30]
    return float(np.mean(loud)) if len(loud) else -60.0


def assemble_voice(work: Path) -> tuple[Path, Path, list[dict], list[dict], dict]:
    """Реплики проб → дорожка ролика целыми фразами (склейки только в тишине между фразами); слова Whisper сдвигаются
    вместе с репликами, начало слова — не раньше начала отрезка (Whisper ставит его раньше тишины).

    Возвращает (обе дорожки, только Клод — для рта, реплики Клода — для субтитров, все реплики, интервалы). Голос юзера
    выравнивается по громкости речи к Клоду (+USER_GAIN_DB), чтобы музыка под обоими приседала одинаково."""
    import numpy as np

    takes: dict = {}
    n = int(DUR * SR)
    both = np.zeros(n, dtype=np.float32)
    claude = np.zeros(n, dtype=np.float32)
    lines, spans = [], {}
    fade = int(0.012 * SR)
    for lid, take, a, b, at, text in E.LINES:
        if take not in takes:
            takes[take] = (P1.decode(work / take), json.loads((work / take).with_suffix(".words.json").read_text("utf-8")))
    # Одна поправка на все пробы юзера (по медиане их громкости): бормотание остаётся тише, крик — громче.
    user_db = float(np.median([_lufs_of(takes[t][0]) for _l, t, *_r in E.USER_LINES]))
    user_gain = 10 ** ((_lufs_of(takes[E.T1][0]) - user_db + USER_GAIN_DB) / 20)
    for lid, take, a, b, at, text in E.LINES:
        src, heard = takes[take]
        seg = src[int(a * SR):int(b * SR)].copy()
        if E.is_user(lid):
            seg *= user_gain
        seg[:fade] *= np.linspace(0, 1, fade)
        seg[-fade:] *= np.linspace(1, 0, fade)
        k = int(at * SR)
        m = min(len(seg), n - k)
        both[k:k + m] += seg[:m]
        if not E.is_user(lid):
            claude[k:k + m] += seg[:m]
        shift = at - a
        spans[lid] = (at, at + (b - a))
        got = [w for w in heard if a - 0.12 <= (w["t0"] + w["t1"]) / 2 <= b + 0.05]
        lines.append({"line": lid, "user": E.is_user(lid), "text": text,
                      "heard": [{**w, "t0": round(max(w["t0"], a) + shift, 3), "t1": round(min(w["t1"], b) + shift, 3)} for w in got],
                      "start": round(at, 3), "end": round(at + (b - a), 3)})
    path, path_c = work / "voice.wav", work / "voice_claude.wav"
    P1.write_wav(path, both)
    P1.write_wav(path_c, claude)
    return path, path_c, [ln for ln in lines if not ln["user"]], lines, spans


def caption_layers() -> list[dict]:
    return [
        {"id": "hook", "type": "text", "start": 0.0, "end": SH["cover"][1], "x": 0.5, "y": 0.15,
         # size 116 — одной строкой во всю ширину (150 переносил «3» отдельно)
         "props": {"text": E.HOOK, "font": "Montserrat", "weight": 900, "size": 116, "color": "#fff6e8", "align": "center", "uppercase": True,
                   "lineHeight": 1.0, "stroke": {"color": "#2b1810", "width": 16},
                   "bg": {"color": "#d8342c", "opacity": 1, "radius": 36, "padX": 44, "padY": 26}}},
        {"id": "top", "type": "text", "start": SH["cover"][1], "end": 2.4, "x": 0.5, "y": 0.105,
         "props": {"text": TOP_CARD, "font": "Montserrat", "weight": 800, "size": 46, "color": "#2b1810", "align": "center",
                   "bg": {"color": "#f6e6cf", "opacity": 1, "radius": 14, "padX": 30, "padY": 14}, "stroke": {"color": "#f6e6cf", "width": 0}},
         "out": {"type": "fade", "duration": 0.2}},
        {"id": "caps", "type": "captions", "start": 0.0, "end": DUR, "x": 0.5, "y": 0.8,
         "props": {"style": "hormozi", "size": 78, "activeColor": "#FFD23F", "uppercase": True, "maxWords": 1, "maxLines": 1,
                   "stroke": {"color": "#2b1810", "width": 11}}},
    ]


def captions(video: Path, words: list[dict], out: Path, work: Path) -> None:
    from app.motion_render_comp import build_compositor_args, write_comp

    comp = write_comp(work / "comp.json", width=1080, height=1920, fps=FPS, duration=DUR, layers=caption_layers(), camera=[], words=words)
    (work / "assets.json").write_text("{}", "utf-8")
    proc = subprocess.run(build_compositor_args(comp, work / "assets.json", input_path=video, output_path=out), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=False, stdin=subprocess.DEVNULL)
    if proc.returncode != 0 or not out.is_file():
        raise RuntimeError(f"субтитры: {proc.stderr.strip()[-1200:]}")


def sfx_list() -> list[dict]:
    """Только настоящие записи (freesound CC0, kenney); громкость — здесь, сведение ставит их под голос."""
    fs, ki = P1.PACKS / "freesound", P1.PACKS / "kenney-interface"
    out = [
        {"t": 0.02, "path": fs / "pop-cartoon.ogg", "volume": 0.35},
        {"t": SH["c_cu"][0] - 0.03, "path": fs / "whoosh-1.ogg", "volume": 0.28},
        # Юзер печатает простыню, вставляет.
        {"t": E.TYPE0, "path": fs / "typing-digital.ogg", "volume": 0.22, "len": E.PASTE - E.TYPE0},
        {"t": E.PASTE - 0.02, "path": ki / "maximize_004.ogg", "volume": 0.4},
        {"t": E.PASTE, "path": fs / "pop.ogg", "volume": 0.4},
        {"t": E.CASH, "path": fs / "cash.ogg", "volume": 0.18, "len": 0.8},
        {"t": END["c3"] + 0.05, "path": fs / "sad-trombone.ogg", "volume": 0.12, "len": 1.2},
        {"t": E.PAGE2 - 0.03, "path": ki / "select_002.ogg", "volume": 0.3},
        {"t": E.PAGE3 - 0.03, "path": ki / "select_003.ogg", "volume": 0.3},
        {"t": E.TIRED, "path": fs / "whoosh-3.ogg", "volume": 0.15},
        {"t": E.LAST - 0.05, "path": fs / "record-scratch.ogg", "volume": 0.2, "len": 0.5},
        {"t": E.ZOOM - 0.04, "path": fs / "whoosh-2.ogg", "volume": 0.22},
        {"t": E.ZOOM + 0.3, "path": fs / "impact-low.ogg", "volume": 0.3, "len": 1.0},
        {"t": E.BLUE_AT - 0.02, "path": ki / "click_002.ogg", "volume": 0.45},
        {"t": E.BLUE_AT + 0.1, "path": ki / "confirmation_002.ogg", "volume": 0.4},
        {"t": E.GENIUS_TYPE, "path": fs / "typing-digital.ogg", "volume": 0.2, "len": E.GENIUS - E.GENIUS_TYPE},
        {"t": E.GENIUS, "path": fs / "pop.ogg", "volume": 0.4},
        {"t": E.GENIUS + 0.03, "path": fs / "boing-2.ogg", "volume": 0.14, "len": 0.5},
        {"t": TEA + 0.32, "path": ki / "glass_001.ogg", "volume": 0.2, "len": 0.4},
    ]
    # Прокрутка простыни: короткие «щелчки колеса» на каждом её сдвиге.
    for t0, _t1 in (E.GENIUS_SCROLL, E.STEP_SCROLL, E.TIPS_SCROLL, E.CAREER_SCROLL, E.GRANNY_SCROLL):
        out.append({"t": t0, "path": ki / "scroll_001.ogg", "volume": 0.28, "len": 0.3})
    i = 0
    for a, b in ((E.SCROLL0, E.PAGE2 - 0.05), (E.PAGE2, E.TEA1 - 0.05), (E.PAGE3, E.TEA2 - 0.05), (E.LAST - 0.6, E.LAST - 0.1)):
        t = a + 0.05
        while t < b:
            out.append({"t": round(t, 3), "path": ki / f"scroll_00{1 + i % 5}.ogg", "volume": 0.2, "len": 0.2})
            t += 0.21
            i += 1
    # Часы крутятся: «вжух» и тиканье.
    out.append({"t": E.SPIN, "path": fs / "whip.ogg", "volume": 0.2, "len": 0.5})
    for j in range(7):
        out.append({"t": round(E.SPIN + 0.08 + j * 0.11, 3), "path": ki / "tick_002.ogg", "volume": 0.28, "len": 0.1})
    # Финал: только часы.
    t = END["c5b"] + 0.15
    while t < DUR - 0.2:
        out.append({"t": round(t, 3), "path": ki / "tick_001.ogg", "volume": 0.3, "len": 0.25})
        t += 1.0
    return out


MUSIC_GAP_LU, MUSIC_SPEECH_LU = -12.0, -18.0  # музыка относительно голоса: в паузах и под речью
DUCK_ATTACK, DUCK_RELEASE, DUCK_BRIDGE = 0.12, 0.35, 0.6  # с: присесть до речи, подняться после, паузы короче — не поднимать


def duck_curve(spans: list[tuple[float, float]], dur: float, gap_db: float, speech_db: float, rate: int = 100) -> np.ndarray:
    """Громкость музыки (дБ) по кадрам ``rate`` Гц: под речью (обоих голосов) — ``speech_db``, в паузах — ``gap_db``.
    Короткие паузы между фразами (< DUCK_BRIDGE) не поднимают музыку; приседает заранее (DUCK_ATTACK), поднимается
    плавно (DUCK_RELEASE) — без «качелей»."""
    sp = sorted(spans)
    merged: list[list[float]] = []
    for a, b in sp:
        if merged and a - merged[-1][1] < DUCK_BRIDGE:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    n = int(math.ceil(dur * rate)) + 1
    t = np.arange(n) / rate
    out = np.full(n, gap_db, dtype=np.float64)
    depth = gap_db - speech_db
    for a, b in merged:
        down = np.clip((t - (a - DUCK_ATTACK)) / DUCK_ATTACK, 0, 1)
        up = np.clip(1 - (t - b) / DUCK_RELEASE, 0, 1)
        w = np.where(t < a, down, np.where(t <= b, 1.0, up))
        out = np.minimum(out, gap_db - depth * w)
    return out


def ducked_music(music: Path, spans: list[tuple[float, float]], gain_db: float, out: Path) -> Path:
    """Музыка ролика одним файлом: 0…ZOOM, тишина на вылете последней строки, с SITE — снова с начала трека; громкость
    по ``duck_curve`` (+ ``gain_db`` — выравнивание трека к голосу). Стерео 48 кГц."""
    import wave

    sr = 48000
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(music), "-ac", "2", "-ar", str(sr), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    src = np.frombuffer(pcm, dtype=np.float32).reshape(-1, 2)
    n = int(DUR * sr)

    def take(k: int) -> np.ndarray:
        reps = int(math.ceil(k / max(1, len(src))))
        return np.tile(src, (reps, 1))[:k]

    y = np.zeros((n, 2), dtype=np.float32)
    a1, b0 = int(E.ZOOM * sr), int(E.SITE * sr)
    y[:a1] = take(a1)
    y[b0:] = take(n - b0)
    t = np.arange(n) / sr
    edge = np.ones(n)
    edge *= np.clip(t / 0.3, 0, 1)  # вход
    edge *= np.where(t < E.SITE, np.clip((E.ZOOM - t) / 0.25, 0, 1), np.clip((t - E.SITE) / 0.2, 0, 1))
    edge *= np.clip((DUR - t) / 0.6, 0, 1)  # уход
    curve = duck_curve(spans, DUR, MUSIC_GAP_LU, MUSIC_SPEECH_LU)
    db = np.interp(t, np.arange(len(curve)) / 100, curve) + gain_db
    y *= (edge * 10 ** (db / 20)).astype(np.float32)[:, None]
    q = (np.clip(y, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(q.tobytes())
    return out


def mix(video: Path, voice: Path, out: Path, work: Path, report: dict) -> None:
    """Сведение: оба голоса одной дорожкой; музыка приседает под речь (−18 LU под голосом, −12 в паузах), на вылете
    последней строки замолкает, на кнопке — снова."""
    from app.audio_mix_model import ebur128
    from app.motion import asset_key
    from app.motion_render import build_audio_layers_args, speech_level_db
    from app.stories_3d import voice_lufs

    music = P1.MUSIC_DIR / "lofi-beat.mp3"
    if not music.is_file():
        music = P1.MUSIC_DIR / "chill-keys.mp3"
    voice_db = voice_lufs(voice)
    m_lufs = float(ebur128(music)["i"])
    gain = round(voice_db - m_lufs, 2)
    spans = [(at, at + (b - a)) for _l, _tk, a, b, at, _tx in E.LINES]
    music_file = ducked_music(music, spans, gain, work / "music_ducked.wav")
    layers = [{"id": "voice", "type": "audio", "start": 0.0, "end": DUR, "props": {"asset_id": 9001, "mode": "track", "volume": 1.0, "role": "voice"}},
              {"id": "music", "type": "audio", "start": 0.0, "end": DUR,
               "props": {"asset_id": 9002, "mode": "track", "volume": 1.0, "gain_db": 0.0, "role": "music"}}]
    assets = {asset_key(9001): str(voice), asset_key(9002): str(music_file)}
    report["music"] = {"file": music.name, "lufs": m_lufs, "voice_lufs": voice_db, "under_speech_lu": MUSIC_SPEECH_LU, "in_gaps_lu": MUSIC_GAP_LU}
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


NUMS = {"30": "тридцать", "2": "два", "200": "двести"}


def _norm(s: str) -> str:
    import re

    w = re.sub(r"[^а-яa-z0-9]", "", s.lower().replace("ё", "е"))
    return NUMS.get(w, w)


def _tokens(text: str) -> list[str]:
    """Слова → нормализованные; Whisper пишет числа цифрами («30-летним», «200») и склеивает дефисы — приводим к словам."""
    import re

    text = re.sub(r"30\s*-?\s*летн", "тридцатилетн", text.lower())
    out = []
    for x in text.replace("—", " ").replace("-", " ").split():
        w = _norm(x)
        if w:
            out.append(w)
    return out


def script_words() -> list[str]:
    """Слова сценария (обоих голосов, по времени), нормализованные."""
    return [w for ln in sorted(E.LINES, key=lambda ln: ln[4]) for w in _tokens(ln[5])]


def check_words(final: Path, work: Path) -> dict:
    """faster-whisper по готовому ролику — сверка со сценарием (слова по порядку)."""
    import difflib

    import meme3d_voice as V

    probe = work / "final_check.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(final), "-ac", "1", "-ar", "16000", str(probe)], check=True)
    probe.with_suffix(".words.json").unlink(missing_ok=True)
    heard = [w for w in V.words(probe) if w["t0"] < END["c5b"] + 0.3 and w["t1"] - w["t0"] > 0.02]
    want = script_words()
    got = [w for h in heard for w in _tokens(h["w"])]
    sm = difflib.SequenceMatcher(a=want, b=got, autojunk=False)
    diffs = [(op, " ".join(want[i1:i2]), " ".join(got[j1:j2])) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal"]
    return {"ratio": round(sm.ratio(), 3), "script_words": len(want), "heard_words": len(got), "diffs": diffs, "text": " ".join(w["w"] for w in heard)}


def voice_joins(x, sr: int = SR) -> dict:
    """Непрерывность голоса по готовой дорожке: фразы не наезжают и не стыкуются без паузы, внутри фразы нет вставленной
    тишины длиннее 0.6 с и щелчков (скачок соседних сэмплов > 0.5)."""
    import numpy as np

    issues = []
    spans = [(at, at + (b - a), lid) for lid, _tk, a, b, at, _tx in E.LINES]
    for (_a0, a1, la), (b0, _b1, lb) in zip(spans, spans[1:]):
        if b0 < a1 + 0.05:
            issues.append(f"{la}→{lb}: наезд или стык без паузы ({b0 - a1:+.2f} с)")
    hop = sr // 100
    for a0, a1, lid in spans:
        seg = np.asarray(x[int(a0 * sr):int(a1 * sr)], dtype=np.float32)
        if len(seg) < hop * 12:
            issues.append(f"{lid}: пустая фраза")
            continue
        rms = np.sqrt(np.add.reduceat(seg[: len(seg) // hop * hop] ** 2, np.arange(0, len(seg) // hop * hop, hop)) / hop + 1e-12)
        db = 20 * np.log10(rms + 1e-9)[5:-5]
        run = best = 0
        for v in db:
            run = run + 1 if v < -55 else 0
            best = max(best, run)
        if best * 0.01 > 0.6:
            issues.append(f"{lid}: тишина {best * 0.01:.2f} с внутри фразы")
        jump = float(np.max(np.abs(np.diff(seg))))
        if jump > 0.5:
            issues.append(f"{lid}: щелчок {jump:.2f}")
    return {"phrases": len(spans), "issues": issues}


def frame_f0(seg, sr: int, lo: float = 70.0, hi: float = 350.0) -> list[float]:
    """Основной тон по кадрам 40 мс (автокорреляция, громкие кадры с явной периодичностью)."""
    seg = np.asarray(seg, dtype=np.float64)
    win, hop = int(0.04 * sr), int(0.02 * sr)
    if len(seg) < win:
        return []
    peak = float(np.max(np.abs(seg))) or 1.0
    lmin, lmax = int(sr / hi), int(sr / lo)
    out = []
    for i in range(0, len(seg) - win, hop):
        f = seg[i:i + win] * np.hanning(win)
        if np.sqrt(np.mean(f ** 2)) < 0.05 * peak:
            continue
        ac = np.correlate(f, f, mode="full")[win - 1:]
        if ac[0] <= 0:
            continue
        lag = lmin + int(np.argmax(ac[lmin:lmax]))
        if ac[lag] / ac[0] > 0.45:
            out.append(sr / lag)
    return out


def voice_identity(x, sr: int = SR) -> dict:
    """Непрерывность голосов по тону: медиана F0 каждой реплики; у каждого голоса реплики в пределах ±25% от его
    медианы, а голоса юзера и Клода различимы (медианы расходятся > 10%)."""
    per: dict[str, float] = {}
    for lid, _tk, a, b, at, _tx in E.LINES:
        f0 = frame_f0(np.asarray(x[int(at * sr):int((at + b - a) * sr)]), sr)
        if len(f0) >= 5:
            per[lid] = round(float(np.median(f0)), 1)
    issues = []
    med = {}
    for who, ids in (("claude", [k for k in per if not E.is_user(k)]), ("user", [k for k in per if E.is_user(k)])):
        if not ids:
            issues.append(f"{who}: нет тона")
            continue
        m = float(np.median([per[k] for k in ids]))
        med[who] = round(m, 1)
        for k in ids:
            if abs(per[k] / m - 1) > 0.25:
                issues.append(f"{k}: тон {per[k]} Гц далеко от {who} {m:.0f} Гц")
    if len(med) == 2 and abs(med["user"] / med["claude"] - 1) < 0.10:
        issues.append(f"голоса неразличимы по тону ({med})")
    return {"f0_per_line": per, "median": med, "issues": issues}


def project(p: list[float], cam: list[float], fwd: list[float], fov: float = 34.0, aspect: float = 9 / 16) -> tuple[float, float, float]:
    """Точка мира → доли кадра (x, y сверху) и глубина; камера без крена (крен планов ≤ 3° — в допуске)."""
    f = np.asarray(fwd, float)
    f /= np.linalg.norm(f)
    r = np.cross(f, [0.0, 1.0, 0.0])
    r /= np.linalg.norm(r)
    u = np.cross(r, f)
    d = np.asarray(p, float) - np.asarray(cam, float)
    z = float(d @ f)
    ty = math.tan(math.radians(fov) / 2)
    x = float(d @ r) / (max(z, 1e-6) * ty * aspect)
    y = float(d @ u) / (max(z, 1e-6) * ty)
    return 0.5 + x / 2, 0.5 - y / 2, z


def face_box(t: float, inspect: dict) -> tuple[float, float, float, float] | None:
    """Лицо Клода в кадре (x0, y0, x1, y1 долями) в момент осмотра: передняя грань корпуса от рта до макушки."""
    ch = (inspect.get("characters") or {}).get("claude")
    if not ch or not ch.get("shown", True):
        return None
    w = E.W_HERO
    x, y, z = E.CL
    lo, hi = y + (H.LEG_H + 0.1 * H.BODY_H) * w, y + (H.LEG_H + H.BODY_H) * w
    pts = [project([x + sx * w / 2, yy, z + 0.12 * w], inspect["camera"], inspect["cameraDir"]) for sx in (-1, 1) for yy in (lo, hi)]
    if any(p[2] <= 0.05 for p in pts):
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    box = (max(0.0, min(xs)), max(0.0, min(ys)), min(1.0, max(xs)), min(1.0, max(ys)))
    return box if box[0] < box[2] and box[1] < box[3] else None


TEXT_BANDS = {"caps": (0.2, 0.755, 0.8, 0.845), "hook": (0.08, 0.08, 0.92, 0.23)}


def text_over_face(samples: list[tuple[float, str, dict]]) -> list[dict]:
    """Для каждого момента с текстом (слово субтитров или крючок обложки) — пересекает ли полоса текста лицо Клода."""
    bad = []
    for t, band, insp in samples:
        fb = face_box(t, insp)
        if not fb:
            continue
        bx0, by0, bx1, by1 = TEXT_BANDS[band]
        ox, oy = min(bx1, fb[2]) - max(bx0, fb[0]), min(by1, fb[3]) - max(by0, fb[1])
        if ox > 0 and oy > 0.01:
            bad.append({"t": t, "band": band, "face": [round(v, 3) for v in fb], "overlap_y": round(oy, 3)})
    return bad


def _box_gap(p: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> tuple[float, float]:
    """Расстояние точки до коробки (0 внутри) и глубина внутри (0 снаружи)."""
    out = np.maximum(np.maximum(lo - p, p - hi), 0.0)
    gap = float(np.linalg.norm(out))
    inside = 0.0 if gap > 0 else float(np.min(np.minimum(p - lo, hi - p)))
    return gap, inside


def hand_checks(samples: list[dict], hold_from: float) -> dict:
    """Проверки tools/model_check (anim_checks) для руки с кружкой: рывки рук и кружки по кадрам (``jumps``) и контакт
    ближней руки с кружкой, пока держит (``hand_contact``). ``samples`` — вывод --inspect-list по кадрам."""
    sys.path.insert(0, str(ROOT / "tools" / "model_check"))
    import anim_checks as AC

    frames = [round(s["t"] * FPS) for s in samples]
    pos, gap, pen, weight = [], [], [], []
    for s in samples:
        ch = s["characters"]["claude"]
        box = s["objects"]["mug"]
        lo, hi = np.asarray(box["min"], float), np.asarray(box["max"], float)
        hands = [np.asarray(ch["hand_l"], float), np.asarray(ch["hand_r"], float)]
        pos.append([*hands, (lo + hi) / 2])
        g = [_box_gap(h, lo, hi) for h in hands]
        best = min(g, key=lambda x: x[0])
        gap.append(best[0])
        pen.append(best[1])
        weight.append(1.0 if s["t"] >= hold_from else 0.0)
    cuts = [round(a0 * FPS) for a0, _a1, _n in SHOTS]
    jumps = AC.jumps(np.asarray(pos), frames, cuts=cuts, labels=["hand_l", "hand_r", "mug"])
    contact = AC.hand_contact("mug", gap, pen, weight, frames)
    return {"jumps": jumps, "hand_contact": contact}


def tts_user(work: Path) -> int:
    """Реплики юзера — по пробе на реплику (E.USER_TTS), голос E.USER_VOICE; готовая проба не перегенерируется.
    Траты — в spend.log рядом с роликом."""
    import meme3d_voice as V

    log = work.parent / "spend.log"
    for lid, text in E.USER_TTS:
        out = work / E.user_take(lid)
        fresh = not out.is_file()
        info = V.tts(text, out, E.USER_VOICE)
        ws = V.words(out)
        print(lid, info["chars"], info["cost_rub"], " ".join(f"{w['w']}[{w['t0']:.2f}-{w['t1']:.2f}]" for w in ws))
        if fresh:
            with log.open("a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d')} v2 {out.name} {info['model']} {info['voice']} {info['chars']} chars "
                        f"{info['cost_rub']:.4f} RUB (balance {info['balance_before']} -> {info['balance_after']})\n")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--preview", help="кадры PNG сцены без субтитров: секунды через запятую")
    ap.add_argument("--shots", action="store_true", help="превью: по кадру из середины каждого плана")
    ap.add_argument("--inspect", help="положения героев и вещей в моменты t через запятую (JSON в work/inspect.json)")
    ap.add_argument("--fit-mug", action="store_true", help="кружка в руку по осмотру сцены (work/mug_keys.json)")
    ap.add_argument("--checks", action="store_true", help="текст не на лице Клода + рука с кружкой (work/checks.json)")
    ap.add_argument("--small", action="store_true", help="превью 540×960")
    ap.add_argument("--gl", default="gpu")
    ap.add_argument("--name", default="ep9")
    ap.add_argument("--skip-render", action="store_true")
    ap.add_argument("--no-build", action="store_true")
    ap.add_argument("--tts-user", action="store_true", help="озвучить реплики юзера (Polza, платно; готовые пробы не трогает)")
    a = ap.parse_args()
    os.environ.setdefault("MOTION_PACKS_DIR", str(P1.PACKS))
    out_dir = Path(a.out_dir).resolve()
    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    report: dict = {}
    if a.tts_user:
        return tts_user(work)
    voice, voice_c, lines, all_lines, spans = assemble_voice(work)
    words = P1.caption_words(lines)
    mouth = H.viseme_keys_from_words(syllable_words(words))
    mug_file = work / "mug_keys.json"
    mug_keys = json.loads(mug_file.read_text("utf-8")) if mug_file.is_file() else None
    spec = build_spec(P1.mouth_envelope(voice_c), mouth, mug_keys)
    if a.small:
        spec["width"], spec["height"] = 540, 960
        spec["quality"]["shadowSize"] = 1024
    sp = work / "scene.json"
    sp.write_text(json.dumps(spec, ensure_ascii=False), "utf-8")
    (work / "events.json").write_text(json.dumps({"spans": spans, "shots": SHOTS, "words": words}, ensure_ascii=False, indent=1), "utf-8")
    if not a.no_build:
        P1._run(["npm", "run", "build:scene3d"], "сборка движка", cwd=ROOT, shell=sys.platform == "win32")
    render = ["node", str(ROOT / "node" / "scene3d" / "render.mjs"), "--spec", str(sp), "--packs", str(P1.PACKS), "--gl", a.gl, "--workers", "1"]
    if a.inspect:
        r = P1._run([*render, "--inspect-list", a.inspect], "осмотр")
        (work / "inspect.json").write_text(r.stdout.strip().splitlines()[-1], "utf-8")
        print(work / "inspect.json")
        return 0
    if a.fit_mug:
        # Кружку — в руку: осмотр каждые 2 кадра чаепития, ключи кружки от положения левого обрубка.
        ts = [round(TEA + i * 2 / FPS, 3) for i in range(int((DUR - TEA) * FPS / 2) + 1)]
        r = P1._run([*render, "--inspect-list", ",".join(f"{t:.3f}" for t in ts)], "осмотр")
        keys = mug_keys_from_hands(json.loads(r.stdout.strip().splitlines()[-1])["inspect"])
        mug_file.write_text(json.dumps(keys, ensure_ascii=False), "utf-8")
        print(mug_file, len(keys))
        return 0
    if a.checks:
        # Текст против лица: середины слов субтитров и крючок обложки; рука с кружкой — каждый кадр чаепития.
        text_t = [(0.3, "hook")] + [(round((w["t0"] + w["t1"]) / 2, 3), "caps") for w in words]
        sip_t = [round(TEA + i / FPS, 3) for i in range(int((DUR - TEA) * FPS))]
        P1.wait_ram(3.0)
        r = P1._run([*render, "--inspect-list", ",".join(f"{t:.3f}" for t, _ in text_t + [(t, "") for t in sip_t])], "осмотр")
        got = {round(x["t"], 3): x for x in json.loads(r.stdout.strip().splitlines()[-1])["inspect"]}
        seen = [(t, band, got[t]) for t, band in text_t if t in got]
        res = {"text_over_face": text_over_face(seen), "text_samples": len(seen),
               "text_samples_with_face": sum(face_box(t, s) is not None for t, _b, s in seen),
               "hands": hand_checks([got[t] for t in sip_t if t in got], SIP_UP)}
        (work / "checks.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), "utf-8")
        print(json.dumps(res, ensure_ascii=False, indent=1)[:4000])
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
        P1.wait_ram(4.0)
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
                   "duration": DUR, "caption_check": str(A6.caption_check(final, words, out_dir)), "whisper": check_words(final, work),
                   "voice_joins": voice_joins(P1.decode(voice)), "voice_identity": voice_identity(P1.decode(voice)),
                   "user_subtitles": "нет: текст юзера на экране (простыня, последняя строка, пузырь «ТЫ ГЕНИЙ»)"})
    report["sub_sync"] = A8.sub_sync(words, work)
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
