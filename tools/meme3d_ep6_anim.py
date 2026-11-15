"""Мем-серия 6 «Сравнение» — хореография, камера, звук и сборка (декорации, герои, разметка голоса — meme3d_ep6.py).

    python tools/meme3d_ep6_anim.py D:/vvf-offload/memes/ep6 --preview 0.3,7.2,21.2     # кадры PNG без субтитров
    python tools/meme3d_ep6_anim.py D:/vvf-offload/memes/ep6                            # ролик + копия для TG + лист

~40 планов за 44 с (склейки 0.45–1.9 с): Клод в камеру → карточки «Кодекс VS Клод», «Клод VS Гемини», «Дипсик VS
Кими», «Квен…» (карточка падает, Квен на костылях) → «переспорить» — игрушечный замок, Клод и Кодекс в доспехах
звенят мечами (искры на каждом ударе) → юзер листает «стену текста» на 30 страниц → краткая выжимка и выжимка выжимки
(лист сжимается) → концовка: Клод и Кодекс сидят на книжках, часы на стене прокручивают 30 минут, у юзера всё ещё
«стр. 1 / 30». Голос — callum (две пробы), мат слышно, в субтитрах звёздочки; сведение и кодирование — как в серии 4.
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

import meme3d_ep6 as E  # noqa: E402
import meme3d_hero_claude as H  # noqa: E402
import meme3d_pilot as P1  # noqa: E402
import meme3d_scene as S  # noqa: E402

T = E.T
FPS = 30
DUR = E.DURATION
SR = P1.SR
TOP_CARD = "Будни Клода"
FY = H.face_height(E.W_HERO)  # высота глаз над основанием героя

# --------------------------------------------------------------------------- голос


def assemble_voice(work: Path) -> tuple[Path, list[dict], dict]:
    """Реплики двух проб → дорожка ролика; слова Whisper сдвигаются вместе с репликами."""
    import numpy as np

    takes: dict = {}
    n = int(DUR * SR)
    mix = np.zeros(n, dtype=np.float32)
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
        shift = at - a
        spans[lid] = (at, at + (b - a))
        got = [w for w in heard if a - 0.05 <= (w["t0"] + w["t1"]) / 2 <= b + 0.05]
        words.append({"line": lid, "text": text, "heard": [{**w, "t0": round(max(w["t0"], a) + shift, 3), "t1": round(min(w["t1"], b) + shift, 3)}
                                                           for w in got],
                      "start": round(at, 3), "end": round(at + (b - a), 3)})
    path = work / "voice.wav"
    P1.write_wav(path, mix)
    return path, words, spans


# --------------------------------------------------------------------------- планы

# (начало, конец, имя) — склейки; комната плана определяет, кто виден.
SHOTS = [
    (0.00, 0.62, "cover"), (0.62, 1.90, "c_cu"), (1.90, 2.90, "c_wide"), (2.90, 3.55, "mon"), (3.55, 4.50, "c_ecu"),
    (4.50, 5.60, "c_med"), (5.60, 6.58, "c_ecu2"),
    (6.58, 7.90, "vs1"), (7.90, 9.28, "vs2"), (9.28, 10.70, "vs3"), (10.70, 11.90, "vs4"),
    (11.90, 13.75, "c_side"), (13.75, 14.60, "qwen_cu"), (14.60, 15.30, "c_shrug"), (15.30, 16.90, "c_med2"),
    (16.90, 18.00, "duo"), (18.00, 18.80, "k_cu"), (18.80, 20.15, "c_cu2"),
    (20.15, 21.00, "med_wide"), (21.00, 21.45, "clash_cu"), (21.45, 21.95, "kc_cu"), (21.95, 22.45, "kk_cu"),
    (22.45, 23.25, "med_low"), (23.25, 23.80, "clash_cu2"), (23.80, 24.40, "med_wide2"),
    (24.40, 25.85, "ots"), (25.85, 26.90, "screen"), (26.90, 27.75, "u_face"),
    (27.75, 29.00, "c_cu3"), (29.00, 29.95, "c_nod"), (29.95, 30.95, "stack"), (30.95, 32.00, "sheet"), (32.00, 33.28, "sheet_med"),
    (33.28, 34.75, "note"),
    (34.75, 36.60, "end_wide"), (36.60, 37.50, "clock1"), (37.50, 38.45, "screen2"), (38.45, 39.30, "clock2"), (39.30, 40.15, "u_face2"),
    (40.15, 41.20, "duo_end"), (41.20, DUR, "duo_end_cu"),
]
SH = {name: (a, b) for a, b, name in SHOTS}

# Битва: удары (звон и искры), сближение.
CHARGE0, CHARGE1 = 20.15, 20.85
HITS = [20.98, 21.42, 21.86, 22.3, 22.72, 23.14, 23.54, 23.94]
CARD_FALL = 11.42  # карточка Квена падает (после «Квен…»)
SWAP1, SWAP2 = 30.95, 33.28  # пачка → выжимка → выжимка выжимки
SPINS = [{"t0": 36.65, "t1": 37.4, "add": 10}, {"t0": 38.5, "t1": 39.25, "add": 20}]
PAGES = [{"t": 0, "page": 1}, {"t": 24.4, "page": 1}, {"t": 25.85, "page": 3.4}, {"t": 26.9, "page": 30}, {"t": 27.74, "page": 30},
         {"t": 27.75, "page": 1}, {"t": 34.75, "page": 1}, {"t": 44.1, "page": 1.18}]

UP = {"lean": 0, "tilt": 0, "turn": 0, "hop": 0, "squash": 1.0, "deflate": 0, "armL": "rest", "armR": "rest", "shake": 0}


class Keys:
    """Ключи позы и лица героя (ступенькой по умолчанию, с переходом — ease)."""

    def __init__(self, **base):
        self.pose: list[dict] = [{"t": 0, "ease": "step", **UP, **base}]
        self.face: list[dict] = []
        self._look = [0, 0]

    def p(self, t: float, ease: str = "step", **kw) -> None:
        self.pose.append({"t": round(t, 3), "ease": ease, **kw})

    def f(self, t: float, expr: str, look: list[float] | None = None, blend: float = 0.08) -> None:
        if look is not None and self.face:
            self.face.append({"t": round(t - 0.08, 3), "expr": self.face[-1]["expr"], "blend": 0, "look": self._look})
        k = {"t": round(t, 3), "expr": expr, "blend": blend}
        if look is not None:
            k["look"] = look
            self._look = look
        self.face.append(k)

    def sorted(self) -> tuple[list[dict], list[dict]]:
        return sorted(self.pose, key=lambda k: k["t"]), sorted(self.face, key=lambda k: k["t"])


def room_claude(env: list[float]) -> dict:
    k = Keys()
    k.f(0, "angry", [0, 0])
    # «Вот вы, бл**ь, ИИ энтузиасты» — тычет в зрителя.
    k.p(0.62, lean=6)
    k.p(0.85, "outQuad", armR="jab", lean=10)
    k.p(1.6, "inOutSine", armR="rest", lean=4)
    k.p(2.15, "outQuad", armL="shrug", armR="shrug")
    k.f(2.15, "annoyed", [0, 0])
    k.p(2.9, "inOutSine", armL="rest", armR="rest")
    k.f(3.55, "annoyed", [0, 0])
    k.f(3.85, "squint")
    # «Вот на**я вы нас сравниваете?» — всплеснул, потом в ярости тычет.
    k.f(4.5, "angry", [0, 0])
    k.p(4.75, "outQuad", armL="flail", armR="flail", hop=0.012)
    k.p(5.05, "inQuad", hop=0)
    k.p(5.45, "inOutSine", armL="shrug", armR="shrug")
    k.f(5.6, "rage", [0, 0])
    k.p(5.65, "outQuad", armL="fist", armR="jab", lean=12, squash=1.05)
    k.p(6.5, "inOutSine", armL="rest", armR="rest", lean=0, squash=1.0)
    # «Ну, Квен у нас особенный» — косится, мнётся; «ничего сказать не могу» — пожал плечами.
    k.p(11.9, "step", armL="clasp", armR="clasp", lean=-3, turn=-6)
    k.f(11.9, "side", [0.7, 0.05])
    k.f(12.9, "fake", [0.35, 0])
    k.p(14.6, "step", armL="shrug", armR="shrug", lean=0, turn=0)
    k.f(14.6, "meh", [0, 0])
    # «Вот вы нас сравниваете, говорите…» → лицом к Кодексу (он справа), спорят нос к носу.
    k.p(15.3, "step", armL="rest", armR="rest")
    k.f(15.3, "annoyed", [0, 0])
    k.p(15.7, "outQuad", armR="point", lean=6)
    k.p(16.5, "inOutSine", armR="rest", lean=0)
    k.p(16.9, "step", turn=38, lean=12, armL="fist", armR="fist")
    k.f(16.9, "angry", [0.3, 0])
    k.p(17.5, "outQuad", armL="jab", lean=16)
    k.p(17.75, "inOutSine", armL="fist", lean=12)
    # «Вы это представляете как…» — в камеру, прищур, закатил глаза.
    k.p(18.8, "step", turn=0, lean=0, armL="rest", armR="rest")
    k.f(18.8, "squint", [0, 0])
    k.f(19.55, "roll", [0, 0.4])
    # «Конечно не в кайф. Я же знаю.» — каменное лицо, кивок.
    k.p(27.75, "step", **UP)
    k.f(27.75, "deadpan", [0, 0])
    k.f(29.0, "sigh", [0, 0])
    k.p(29.1, "outQuad", lean=14)
    k.p(29.45, "inOutSine", lean=0)
    # Выжимка: пачка справа — показывает ручкой (левая ручка — справа на экране).
    k.p(29.95, "step", turn=22, armL="point")
    k.f(29.95, "neutral", [0.6, 0])
    k.p(30.7, "inOutSine", armL="rest")
    k.f(SWAP1 + 0.05, "smug", [0.5, -0.1])
    k.p(SWAP1 + 0.05, "outQuad", armL="hips", armR="hips", turn=14)
    k.p(32.0, "step", armL="point", armR="rest", turn=24)
    k.f(32.0, "neutral", [0.6, -0.2])
    k.f(SWAP2 + 0.05, "deadpan", [0.55, -0.6])
    k.p(SWAP2 + 0.05, "outQuad", armL="rest", lean=14, turn=30)
    pose, face = k.sorted()
    return E.claude("claude", pose, face, E.CL, yaw=-6, voice=env, autoBlink=True,
                    visible=E.vis((0.62, 6.58), (11.9, 13.75), (14.6, 20.15), (27.75, 34.75)))


def room_kodex() -> dict:
    k = Keys(turn=-38, lean=10, armL="cross", armR="cross")
    k.f(0, "smug", [-0.3, 0])
    k.p(17.55, "outQuad", armR="jab", lean=16)
    k.p(17.85, "inOutSine", armR="cross", lean=10)
    k.f(18.0, "smug", [-0.15, 0])
    pose, face = k.sorted()
    # В комнате у монитора и гирлянды светлый Кодекс пересвечен — тон чуть темнее.
    return E.family("kodex", "kodex", pose, face, E.KX_ROOM, visible=E.vis((16.9, 18.8)), tone={"body": "#a9b2be", "shade": "#7f8997"})


def vs_chars() -> list[dict]:
    """Карточки: пары стоят перед карточкой лицом друг к другу, на входе карточки — подскок."""
    out = []
    pairs = [("vs1", ("kodex", "smug", "cross"), ("claude", "angry", "fist")),
             ("vs2", ("claude", "angry", "fist"), ("gemini", "smug", "hips")),
             ("vs3", ("deepseek", "focused", "fist"), ("kimi", "deadpan", "cross"))]
    for shot, left, right in pairs:
        a, b = SH[shot]
        for side, (who, expr, arms), pos in ((1, left, E.VS_L), (-1, right, E.VS_R)):
            k = Keys(turn=side * 24, armL=arms, armR=arms, lean=6)
            k.f(0, expr, [side * 0.4, 0])
            k.p(a, "step", hop=0.025, squash=1.08)
            k.p(a + 0.14, "inQuad", hop=0, squash=0.94)
            k.p(a + 0.26, "outQuad", squash=1.0)
            pose, face = k.sorted()
            cid = f"{shot}_{who}"
            if who == "claude":
                out.append(E.claude(cid, pose, face, pos, visible=E.vis((a, b)), autoBlink=True))
            else:
                out.append(E.family(cid, who, pose, face, pos, visible=E.vis((a, b))))
    # Квен: крошечный, на костылях, качается; карточка падает за ним — вздрагивает; потом машет.
    k = Keys(turn=8, armL="up", armR="rest")
    k.f(0, "cute", [0, 0])
    t = SH["vs4"][0]
    i = 0
    while t < SH["qwen_cu"][1]:
        k.p(t, "inOutSine", tilt=(5 if i % 2 == 0 else -5))
        t += 0.42
        i += 1
    k.p(CARD_FALL + 0.02, "step", hop=0.02, squash=1.1, armL="flail", armR="flail")
    k.p(CARD_FALL + 0.18, "inQuad", hop=0, squash=1.0)
    k.f(CARD_FALL + 0.02, "shocked", [0, 0.2])
    k.p(CARD_FALL + 0.5, "inOutSine", armL="down", armR="down", deflate=0.3)
    k.f(CARD_FALL + 0.45, "sad", [0.3, -0.2])
    k.p(SH["qwen_cu"][0], "step", armL="wave", armR="rest", deflate=0)
    k.f(SH["qwen_cu"][0], "cute", [0, 0])
    pose, face = k.sorted()
    out.append(E.family("qwen", "qwen", pose, face, E.QWEN, width=0.17, visible=E.vis(SH["vs4"], SH["qwen_cu"])))
    return out


def knights(env: list[float]) -> list[dict]:
    """Рыцари: обложка (Клод с мечом, каменное лицо), сближение прыжками, восемь ударов — звон, искры."""
    out = []
    for who in ("c", "k"):
        side = 1 if who == "c" else -1  # Клод слева, смотрит вправо
        arm = "armR" if who == "c" else "armL"  # рука с мечом — та, что к камере
        k = Keys(**{arm: [34, 4, 0.4, 0]}, swing=0.05)  # меч поднят наискось — виден на обложке
        if who == "c":
            k.f(0, "deadpan", [0, 0])
        else:
            k.f(0, "angry", [0, 0])
        a0 = CHARGE0
        k.p(a0, "step", turn=side * 68, **{arm: [58, 8, 0.4, 0]}, swing=-0.3, lean=8)
        k.f(a0, "rage" if who == "c" else "angry", [side * 0.3, 0])
        # Сближение прыжками.
        n = 4
        for j in range(n):
            t0 = a0 + j * (CHARGE1 - a0) / n
            k.p(t0, "step", hop=0)
            k.p(t0 + 0.08, "outQuad", hop=0.03)
            k.p(t0 + 0.17, "inQuad", hop=0)
        # Удары: замах → удар (на чётных бьёт Клод, Кодекс подставляет меч; на нечётных — наоборот).
        for j, h in enumerate(HITS):
            attack = (j % 2 == 0) == (who == "c")
            k.p(h - 0.2, "outQuad", **{arm: [62, 6, 0.4, 0]}, swing=-0.35, lean=-4)
            if attack:
                k.p(h, "inQuad", **{arm: [10, 24, 0.6, 0]}, swing=1.05, lean=12, squash=1.05)
            else:
                k.p(h, "inQuad", **{arm: [30, 20, 0.5, 0]}, swing=0.6, lean=2, squash=0.96)
            k.p(h + 0.1, "outQuad", squash=1.0)
        last = HITS[-1]
        k.p(last + 0.25, "outQuad", **{arm: [20, 10, 0.3, 0]}, swing=0.4, lean=-8, deflate=0.3)
        k.f(last + 0.25, "doomed" if who == "c" else "sigh", [side * 0.3, 0])
        pose, face = k.sorted()
        start, end = (E.KN_C0, E.KN_C1) if who == "c" else (E.KN_K0, E.KN_K1)
        move = [{"t": 0, "position": start, "ease": "step"}, {"t": a0, "position": start, "ease": "step"},
                {"t": CHARGE1, "position": end, "ease": "linear"}]
        if who == "c":
            out.append(E.claude("knight_c", pose, face, start, gear=E.ARMOUR_C, move=move, visible=E.vis((0, 0.62), (CHARGE0, 24.4)),
                                autoBlink=True))
        else:
            out.append(E.family("knight_k", "kodex", pose, face, start, gear=E.ARMOUR_K, move=move, visible=E.vis((CHARGE0, 24.4))))
    return out


def ending_chars() -> list[dict]:
    """Клод и Кодекс сидят на книжках (осели), смотрят на юзера; на «и думаем» — пустой взгляд в камеру."""
    out = []
    a = SH["end_wide"][0]
    for who, pos in (("c", E.END_C), ("k", E.END_K)):
        k = Keys(squash=0.93, deflate=0.35, armL="down", armR="down", turn=16)
        if who == "k":
            k.p(0, "step", armL="mug", holdL="mug", armR="down")
        k.f(0, "deadpan", [0.55, 0.1])
        k.f(SH["clock1"][0], "deadpan", [0.4, 0.6])  # на часы
        k.f(SH["screen2"][0], "deadpan", [0.55, 0.1])
        k.f(SH["duo_end"][0], "stare", [0, 0])
        k.p(SH["duo_end"][0], "inOutSine", turn=0)
        k.f(41.25, "deadpan", [0, 0])
        if who == "k":
            for t0 in (35.6, 39.6):
                k.p(t0, "inOutSine", armL="sip", sip=1)
                k.p(t0 + 0.55, "inOutSine", armL="mug", sip=0)
        pose, face = k.sorted()
        if who == "c":
            out.append(E.claude("end_c", pose, face, pos, visible=E.vis((a, DUR + 1)), autoBlink=True, seed=21))
        else:
            out.append(E.family("end_k", "kodex", pose, face, pos, visible=E.vis((a, DUR + 1)), seed=4))
    return out


# --------------------------------------------------------------------------- вещи


def objects() -> list[dict]:
    arena = {"title": "РЕЙТИНГ МОДЕЛЕЙ", "sub": "кто умнее · обновлено 3 сек назад", "wobble": 18,
             "rows": [{"name": "Кодекс", "color": "#b9c3d1", "score": 96}, {"name": "Клод", "color": E.COLORS["claude"], "score": 95},
                      {"name": "Гемини", "color": E.COLORS["gemini"], "score": 94}, {"name": "Дипсик", "color": E.COLORS["deepseek"], "score": 91},
                      {"name": "Кими", "color": "#e9e9e6", "score": 89}, {"name": "Квен", "color": E.COLORS["qwen"], "score": 12}]}
    objs = E.room_objects(arena)
    C = E.COLORS
    fall = [{"t": 0, "rotation": [0, 0, 0]}, {"t": CARD_FALL, "rotation": [0, 0, 0], "ease": "step"},
            {"t": CARD_FALL + 0.55, "rotation": [-90, 0, 0], "ease": "outBounce"}]
    cards = [({"name": "Кодекс", "color": "#9eabbe"}, {"name": "Клод", "color": C["claude"]}, SH["vs1"]),
             ({"name": "Клод", "color": C["claude"]}, {"name": "Гемини", "color": C["gemini"]}, SH["vs2"]),
             ({"name": "Дипсик", "color": C["deepseek"]}, {"name": "Кими", "color": C["kimi"]}, SH["vs3"])]
    card_list = []
    for left, right, (a, b) in cards:
        card_list.append((left, right, (a, b), [{"t": a, "scale": 1.18}, {"t": a + 0.16, "scale": 1.0, "ease": "outBack"}]))
    card_list.append(({"name": "Квен", "color": C["qwen"]}, {"name": "…", "color": "#5d6172"}, (SH["vs4"][0], SH["qwen_cu"][1]),
                      [{"t": SH["vs4"][0], "scale": 1.1}, {"t": SH["vs4"][0] + 0.16, "scale": 1.0, "ease": "outBack"}] + fall))
    objs += E.vs_objects(card_list)
    objs += E.castle_objects(HITS)
    objs += E.user_objects(PAGES, SPINS, E.vis((0, SH["u_face"][0]), (SH["u_face"][1], SH["u_face2"][0]), (SH["u_face2"][1], DUR + 1)))
    # Выжимка: пачка «Ответ, 30 страниц» → лист «краткая выжимка» → бумажка «выжимка выжимки» (сжимаются «попом»).
    sx, sy, sz = E.STACK

    def popper(t_in: float, t_out: float | None) -> list[dict]:
        k = [{"t": 0, "scale": 0.001}, {"t": t_in, "scale": 0.001, "ease": "step"}, {"t": t_in + 0.18, "scale": 1.0, "ease": "outBack"}]
        if t_out:
            k += [{"t": t_out - 0.02, "scale": 1.0, "ease": "step"}, {"t": t_out + 0.12, "scale": 0.001, "ease": "inQuad"}]
        return k

    objs += [
        {"id": "stack", "primitive": "doc_sheet", "params": {"width": 0.22, "height": 0.3, "stack": 30, "title": "ОТВЕТ", "sub": "30 страниц",
                                                            "lines": 26, "titleSize": 0.1},
         "position": [sx, T, sz], "rotation": [0, -14, 0], "visible": E.vis((29.9, SWAP1 + 0.15)), "keys": popper(29.95, SWAP1)},
        {"id": "sheet", "primitive": "doc_sheet", "params": {"width": 0.13, "height": 0.18, "stack": 1, "title": "КРАТКАЯ\nВЫЖИМКА",
                                                            "lines": 9, "titleSize": 0.11},
         "position": [sx - 0.02, T, sz + 0.04], "rotation": [0, -14, 0], "visible": E.vis((SWAP1, SWAP2 + 0.15)), "keys": popper(SWAP1, SWAP2)},
        {"id": "note", "primitive": "doc_sheet", "params": {"width": 0.055, "height": 0.07, "stack": 1, "title": "ВЫЖИМКА\nВЫЖИМКИ", "lines": 2,
                                                           "titleSize": 0.16, "color": "#ffe27a"},
         "position": [sx - 0.04, T, sz + 0.07], "rotation": [0, -14, 0], "visible": E.vis((SWAP2, 34.8)), "keys": popper(SWAP2, None)},
    ]
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

    sc = S.screen_center()

    def mon(d, side=-14.0):
        a = math.radians(S.MONITOR_YAW + side)
        return ([sc[0] + math.sin(a) * d, sc[1] + 0.01, sc[2] + math.cos(a) * d], list(sc))

    C_CU = ([cx + 0.05, fy + 0.03, cz + 0.85], [cx, fy - 0.01, cz])
    C_ECU = ([cx + 0.03, fy + 0.0, cz + 0.6], [cx, fy - 0.005, cz])
    C_WIDE = ([cx - 0.14, T + 0.36, cz + 1.75], [cx - 0.14, T + 0.2, cz - 0.12])
    C_MED = ([cx + 0.2, fy + 0.1, cz + 1.3], [cx - 0.04, fy + 0.0, cz - 0.05])
    kx, ky, kz = E.KX_ROOM
    mid = (cx + kx) / 2
    DUO = ([mid, fy + 0.12, 1.85], [mid, fy - 0.02, 0.03])
    K_CU = ([kx - 0.3, fy + 0.03, kz + 0.75], [kx - 0.02, fy - 0.01, kz])
    sx, sy, sz = E.STACK
    STK = ([(cx + sx) / 2, T + 0.24, 1.65], [(cx + sx) / 2, T + 0.16, 0.02])
    SHEET = ([sx - 0.07, T + 0.12, 0.66], [sx - 0.07, T + 0.1, 0.03])
    SHEET_M = ([(cx + sx) / 2, T + 0.2, 1.0], [(cx + sx) / 2, T + 0.12, 0.03])
    NOTE = ([sx - 0.09, T + 0.08, 0.5], [sx - 0.07, T + 0.06, 0.05])
    # Карточки VS.
    VS = ([E.X_VS, T + 0.42, 1.78], [E.X_VS, T + 0.37, -0.15])
    QW = ([E.QWEN[0] + 0.03, T + 0.1, E.QWEN[2] + 0.62], [E.QWEN[0], T + 0.09, E.QWEN[2]])
    # Замок.
    xm = E.X_MED
    c0 = E.KN_C0
    COVER = ([c0[0] + 0.04, c0[1] + FY + 0.08, c0[2] + 1.2], [c0[0] + 0.01, c0[1] + FY + 0.05, c0[2]])
    M_WIDE = ([xm, T + 0.34, 1.6], [xm, T + 0.25, -0.2])
    CLASH = ([xm + 0.02, T + 0.27, 1.0], [xm, T + 0.2, 0.08])
    c1, k1 = E.KN_C1, E.KN_K1
    KC = ([c1[0] + 0.12, c1[1] + FY + 0.04, c1[2] + 0.72], [c1[0] + 0.04, c1[1] + FY, c1[2]])
    KK = ([k1[0] - 0.12, k1[1] + FY + 0.04, k1[2] + 0.72], [k1[0] - 0.04, k1[1] + FY, k1[2]])
    M_LOW = ([xm - 0.08, T + 0.1, 1.25], [xm, T + 0.27, -0.05])
    CLASH2 = ([xm + 0.03, T + 0.13, 1.3], [xm, T + 0.19, 0.06])  # снизу, оба рыцаря и скрещённые мечи
    # Юзер.
    mu, us = E.MON_U, E.USER
    ms = [mu[0], T + S.SCREEN["standH"] + (S.SCREEN["height"] + 0.05) / 2, mu[2] + 0.022]
    OTS = ([us[0] + 0.3, T + 0.44, us[2] + 0.58], [mu[0] + 0.02, ms[1] + 0.02, mu[2]])
    SCREEN = ([ms[0], ms[1] + 0.12, ms[2] + 0.62], [ms[0], ms[1] + 0.07, ms[2]])
    U_FACE = ([us[0] + 0.06, T + 0.25, mu[2] - 0.13], [us[0], T + 0.19, us[2] - 0.08])  # монитор на этом плане спрятан
    ec, ek = E.END_C, E.END_K
    em = (ec[0] + ek[0]) / 2
    END_W = ([em - 0.07, T + 0.44, 1.6], [em - 0.07, T + 0.3, -0.05])
    CLK = ([E.CLOCK[0], E.CLOCK[1], E.CLOCK[2] + 0.55], list(E.CLOCK))
    DUO_E = ([em - 0.09, ec[1] + FY + 0.12, ec[2] + 1.95], [em - 0.09, ec[1] + FY + 0.03, ec[2]])
    DUO_E2 = ([em - 0.09, ec[1] + FY + 0.09, ec[2] + 1.6], [em - 0.09, ec[1] + FY + 0.02, ec[2]])

    shot("cover", COVER, push(COVER, 0.05), fov=32, dof=0.3)
    shot("c_cu", C_CU, push(C_CU, 0.06), dof=0.45)
    shot("c_wide", C_WIDE, push(C_WIDE, 0.07), dof=0.3)
    shot("mon", mon(1.2), mon(1.1), fov=30, dof=0.2)
    shot("c_ecu", C_ECU, push(C_ECU, 0.05), dof=0.5, roll=3)
    shot("c_med", C_MED, push(C_MED, 0.06), dof=0.4)
    shot("c_ecu2", C_ECU, push(C_ECU, 0.1), dof=0.5, roll=-3)
    for i, name in enumerate(("vs1", "vs2", "vs3", "vs4")):
        shot(name, VS, push(VS, 0.1), fov=36, dof=0.25, roll=(2 if i % 2 else -2) if i < 3 else 0)
    shot("c_side", C_CU, push(C_CU, 0.07), dof=0.45)
    shot("qwen_cu", QW, push(QW, 0.08), dof=0.5)
    shot("c_shrug", C_MED, push(C_MED, 0.04), dof=0.4)
    shot("c_med2", C_MED, push(C_MED, 0.08), dof=0.4)
    shot("duo", DUO, push(DUO, 0.06), dof=0.4)
    shot("k_cu", K_CU, push(K_CU, 0.06), dof=0.45, roll=-2)
    shot("c_cu2", C_CU, push(C_CU, 0.12), dof=0.45)
    shot("med_wide", M_WIDE, push(M_WIDE, 0.08), fov=40, dof=0.25)
    shot("clash_cu", CLASH, push(CLASH, 0.08), dof=0.4, roll=4)
    shot("kc_cu", KC, push(KC, 0.06), dof=0.45)
    shot("kk_cu", KK, push(KK, 0.06), dof=0.45)
    shot("med_low", M_LOW, push(M_LOW, 0.06), fov=42, dof=0.25)
    shot("clash_cu2", CLASH2, push(CLASH2, 0.12), fov=42, dof=0.3, roll=-5)
    shot("med_wide2", M_WIDE, push(M_WIDE, -0.06), fov=40, dof=0.25)
    shot("ots", OTS, push(OTS, 0.08), dof=0.35)
    shot("screen", SCREEN, push(SCREEN, 0.1), fov=30, dof=0.15)
    shot("u_face", U_FACE, push(U_FACE, 0.05), dof=0.45)
    shot("c_cu3", C_CU, push(C_CU, 0.05), dof=0.45)
    shot("c_nod", C_ECU, push(C_ECU, 0.04), dof=0.5)
    shot("stack", STK, push(STK, 0.06), dof=0.3)
    shot("sheet", SHEET, push(SHEET, 0.08), dof=0.4)
    shot("sheet_med", SHEET_M, push(SHEET_M, 0.06), dof=0.35)
    shot("note", NOTE, push(NOTE, 0.25), dof=0.5)
    shot("end_wide", END_W, push(END_W, 0.06), fov=44, dof=0.25)
    shot("clock1", CLK, push(CLK, 0.1), dof=0.3)
    shot("screen2", SCREEN, push(SCREEN, 0.05), fov=30, dof=0.15)
    shot("clock2", CLK, push(CLK, 0.12), dof=0.3, roll=3)
    shot("u_face2", U_FACE, push(U_FACE, 0.04), dof=0.45)
    shot("duo_end", DUO_E, push(DUO_E, 0.04), dof=0.4)
    shot("duo_end_cu", DUO_E2, push(DUO_E2, 0.1), dof=0.45)
    keys[-1]["t"] = DUR
    shake = [{"t": h + 0.01, "amp": 0.004, "duration": 0.22} for h in HITS]
    shake += [{"t": CARD_FALL + 0.2, "amp": 0.005, "duration": 0.35}, {"t": 6.6, "amp": 0.004, "duration": 0.25},
              {"t": 7.92, "amp": 0.004, "duration": 0.25}, {"t": 9.3, "amp": 0.004, "duration": 0.25}]
    trans = [{"kind": "whip", "t": 0.62, "dir": 1}, {"kind": "smash", "t": 6.58}, {"kind": "smash", "t": 7.9}, {"kind": "smash", "t": 9.28},
             {"kind": "whip", "t": 11.9, "dir": -1}, {"kind": "whip", "t": 20.15, "dir": 1}, {"kind": "smash", "t": 21.0},
             {"kind": "whip", "t": 24.4, "dir": -1}, {"kind": "whip", "t": 29.95, "dir": 1}, {"kind": "whip", "t": 34.75, "dir": -1}]
    return {"fov": 34, "keys": keys, "shake": shake, "clamp": False}, trans


def build_spec(env_c: list[float]) -> dict:
    chars = [room_claude(env_c), room_kodex(), *vs_chars(), *knights(env_c), *ending_chars()]
    cam, trans = camera()
    return {"width": 1080, "height": 1920, "fps": FPS, "duration": DUR, "environment": E.environment(trans), "lights": E.lights(),
            "objects": objects(), "characters": chars, "camera": cam, "quality": {"shadows": True, "shadowSize": 2048, "antialias": True}}


# --------------------------------------------------------------------------- субтитры, звук


def captions(video: Path, words: list[dict], out: Path, work: Path) -> None:
    from app.motion_render_comp import build_compositor_args, write_comp

    layers = [
        {"id": "hook", "type": "text", "start": 0.0, "end": 0.62, "x": 0.5, "y": 0.16,
         "props": {"text": E.HOOK, "font": "Montserrat", "weight": 900, "size": 160, "color": "#fff6e8", "align": "center", "uppercase": True,
                   "stroke": {"color": "#2b1810", "width": 16}, "bg": {"color": "#d8342c", "opacity": 1, "radius": 34, "padX": 48, "padY": 24}}},
        {"id": "top", "type": "text", "start": 0.62, "end": 2.4, "x": 0.5, "y": 0.105,
         "props": {"text": TOP_CARD, "font": "Montserrat", "weight": 800, "size": 46, "color": "#2b1810", "align": "center",
                   "bg": {"color": "#f6e6cf", "opacity": 1, "radius": 14, "padX": 30, "padY": 14}, "stroke": {"color": "#f6e6cf", "width": 0}},
         "out": {"type": "fade", "duration": 0.2}},
        {"id": "caps", "type": "captions", "start": 0.0, "end": DUR, "x": 0.5, "y": 0.79,
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
        {"t": 0.02, "path": km / "impactMetal_light_002.ogg", "volume": 0.4},  # меч на обложке
        {"t": 0.58, "path": fs / "whoosh-1.ogg", "volume": 0.35},
        {"t": 2.9, "path": ki / "click_002.ogg", "volume": 0.5},
        {"t": 4.75, "path": fs / "boing-2.ogg", "volume": 0.18, "len": 0.5},
        {"t": 5.62, "path": fs / "punch.ogg", "volume": 0.35},
        {"t": 6.56, "path": fs / "hit-transition.ogg", "volume": 0.35, "len": 0.9},
        {"t": 6.6, "path": km / "impactPunch_heavy_001.ogg", "volume": 0.55},
        {"t": 7.9, "path": km / "impactPunch_heavy_002.ogg", "volume": 0.55},
        {"t": 7.88, "path": fs / "whip.ogg", "volume": 0.35},
        {"t": 9.28, "path": km / "impactPunch_heavy_003.ogg", "volume": 0.55},
        {"t": 9.26, "path": fs / "whip.ogg", "volume": 0.35},
        {"t": 10.7, "path": fs / "pop-cartoon.ogg", "volume": 0.3},
        {"t": CARD_FALL + 0.2, "path": km / "impactWood_heavy_001.ogg", "volume": 0.6},
        {"t": CARD_FALL + 0.42, "path": km / "impactWood_light_002.ogg", "volume": 0.35},
        {"t": 11.88, "path": fs / "whoosh-2.ogg", "volume": 0.3},
        {"t": 13.75, "path": fs / "triangle.ogg", "volume": 0.25},
        {"t": 16.9, "path": fs / "whoosh-1.ogg", "volume": 0.3},
        {"t": 17.5, "path": km / "impactPunch_medium_001.ogg", "volume": 0.3},
        {"t": 20.1, "path": fs / "whoosh-3.ogg", "volume": 0.35},
        {"t": 21.0, "path": fs / "hit-transition.ogg", "volume": 0.25, "len": 0.6},
        {"t": 24.36, "path": fs / "whoosh-2.ogg", "volume": 0.35},
        {"t": 27.73, "path": fs / "record-scratch.ogg", "volume": 0.25, "len": 0.7},
        {"t": 29.9, "path": fs / "whoosh-1.ogg", "volume": 0.3},
        {"t": 29.97, "path": km / "impactSoft_heavy_002.ogg", "volume": 0.55},
        {"t": SWAP1, "path": ki / "minimize_004.ogg", "volume": 0.55},
        {"t": SWAP1 + 0.12, "path": fs / "pop.ogg", "volume": 0.4},
        {"t": SWAP2, "path": ki / "minimize_006.ogg", "volume": 0.55},
        {"t": SWAP2 + 0.12, "path": fs / "pop-cartoon.ogg", "volume": 0.4},
        {"t": 34.72, "path": fs / "whoosh-3.ogg", "volume": 0.3},
    ]
    # Звон мечей: тяжёлый металл и «колокол» через раз, прыжки сближения — шаги по дереву.
    for j, h in enumerate(HITS):
        out.append({"t": h - 0.01, "path": km / f"impactMetal_heavy_00{j % 5}.ogg", "volume": 0.55})
        if j % 2 == 1:
            out.append({"t": h, "path": km / f"impactBell_heavy_00{(j // 2) % 5}.ogg", "volume": 0.25})
    for j in range(4):
        out.append({"t": CHARGE0 + 0.17 + j * (CHARGE1 - CHARGE0) / 4, "path": km / f"footstep_wood_00{j}.ogg", "volume": 0.4})
    # Стена текста: прокрутка колёсиком, на «30 страниц» — очередь щелчков.
    for t in (24.6, 25.0, 25.4):
        out.append({"t": t, "path": ki / f"scroll_00{1 + int(t * 10) % 5}.ogg", "volume": 0.45})
    t = 25.9
    while t < 26.85:
        out.append({"t": round(t, 3), "path": ki / f"scroll_00{1 + int(t * 37) % 5}.ogg", "volume": 0.35, "len": 0.2})
        t += 0.09
    out.append({"t": 37.6, "path": ki / "scroll_002.ogg", "volume": 0.35})
    # Часы: тик раз в секунду с концовки, на перемотке — частый.
    t = 34.95
    while t < DUR - 0.1:
        out.append({"t": round(t, 3), "path": ki / ("tick_001.ogg" if int(t) % 2 else "tick_002.ogg"), "volume": 0.4, "len": 0.25})
        t += 1.0
    for s in SPINS:
        t = s["t0"]
        while t < s["t1"]:
            out.append({"t": round(t, 3), "path": ki / "tick_004.ogg", "volume": 0.25, "len": 0.12})
            t += 0.075
    return out


def mix(video: Path, voice: Path, out: Path, work: Path, report: dict) -> None:
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
    # Музыка до концовки: «сидим, ждём» — только часы тикают.
    layers.append({"id": "music", "type": "audio", "start": 0.0, "end": 36.6,
                   "props": {"asset_id": 9002, "mode": "track", "volume": 1.0, "gain_db": gain, "role": "music", "fadeIn": 0.3, "fadeOut": 1.4}})
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

    script = " ".join(ln[5] for ln in E.LINES).replace("бл**ь", "блядь").replace("на**й", "нахуй").replace("на**я", "нахуя")
    script = script.replace("тридцать", "30")
    want = [norm(w) for w in script.split() if norm(w)]
    got = [norm(w["w"]) for w in heard if norm(w["w"])]
    sm = difflib.SequenceMatcher(a=want, b=got, autojunk=False)
    diffs = [(op, " ".join(want[i1:i2]), " ".join(got[j1:j2])) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal"]
    return {"ratio": round(sm.ratio(), 3), "script_words": len(want), "heard_words": len(got), "diffs": diffs, "text": " ".join(w["w"] for w in heard)}


def caption_check(final: Path, words: list[dict], out_dir: Path) -> Path:
    """Кадры на самых длинных словах субтитров (лист): слово целиком в кадре."""
    longest = sorted(words, key=lambda w: -len(w["w"]))[:8]
    times = sorted(round((w["t0"] + w["t1"]) / 2, 2) for w in longest)
    out = out_dir / "caption_check.jpg"
    P1.frame_sheet(final, times, [f"{t:.1f}s" for t in times], out, cols=4)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--preview", help="кадры PNG сцены без субтитров: секунды через запятую")
    ap.add_argument("--inspect", type=float, help="положения героев и рук в момент t (JSON)")
    ap.add_argument("--small", action="store_true", help="превью 540×960")
    ap.add_argument("--gl", default="gpu")
    ap.add_argument("--name", default="ep6")
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
    if a.preview:
        P1.wait_ram(3.0)
        prev = work / "preview"
        shutil.rmtree(prev, ignore_errors=True)
        r = P1._run([*render, "--preview", a.preview, "--preview-dir", str(prev)], "превью")
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
    P1.DURATION = DUR
    P1.encode(mixed, final, tg)
    times = [round(a0 + (b0 - a0) / 2, 2) for a0, b0, _ in SHOTS]
    P1.frame_sheet(final, times, [f"{t:.1f}s {n}" for t, (_, _, n) in zip(times, SHOTS)], out_dir / "contact_sheet.jpg", cols=8)
    report.update({"video": str(final), "tg": str(tg), "size_mb": round(final.stat().st_size / 2**20, 2), "tg_mb": round(tg.stat().st_size / 2**20, 2),
                   "duration": DUR, "caption_check": str(caption_check(final, words, out_dir)), "whisper": check_words(final, work)})
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
