"""Мем-серия 4 «Экономия» — анимация и сборка ролика (раскладка, герои и разметка голоса — tools/meme3d_ep4.py).

    python tools/meme3d_ep4_anim.py D:/vvf-offload/memes/ep4 [--kodex g] [--preview 0,9.5,17.0] [--inspect 37.45]
    python tools/meme3d_ep4_anim.py D:/vvf-offload/memes/ep4 --test-push      # 6 с вокруг толчка, 540×960, без субтитров

Хореография: ~48 планов за 41 с (склейки каждые 0.3–1.4 с), вставки экрана (код → красный «ЛИМИТ») между крупными
планами, толчок на «Пшёл на**й отсюда», полусонное «Ещё один… двести…» с горы, быстрая нарезка аккаунтов 3–8 (по
0.33 с), передача карандаша в ПРАВУЮ ручку Кодекса (в левой — чай). Аккаунты 1…9 — одни и те же герои от кресла до
горы спящих (ключи ``move``/``visible`` движка). Кодекс — kodex2.ts, вариант ``--kodex`` (f — кремовый, g — светло-
серый; владелец выбрал g). Голос: callum (тело пружинит у того, кто говорит в кадре), Кодекс — eric. Субтитры — как
в пилотах 2/3 (по слову, мат звёздочками, звук без цензуры); сведение и кодирование — общие (meme3d_pilot.py).
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

import meme3d_ep4 as E  # noqa: E402
import meme3d_hero_claude as H  # noqa: E402
import meme3d_pilot as P1  # noqa: E402
import meme3d_scene as S  # noqa: E402

T = E.T
FPS = 30
DUR = E.DURATION
SR = P1.SR
TOP_CARD = "Будни Клода"

# --------------------------------------------------------------------------- голос


def assemble_voice(work: Path) -> tuple[Path, Path, Path, list[dict], dict]:
    """Дорожка ролика + отдельно голос Клода и Кодекса (огибающие тел); слова Whisper сдвигаются с репликами."""
    import numpy as np

    def load(name: str, lowpass: int = 0):
        path = work / name
        af = ["-af", f"lowpass=f={lowpass}"] if lowpass else []
        pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), *af, "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
                             capture_output=True, check=True).stdout
        return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0, json.loads(path.with_suffix(".words.json").read_text("utf-8"))

    takes: dict = {}
    n = int(DUR * SR)
    mix, claude, kodex = (np.zeros(n, dtype=np.float32) for _ in range(3))
    words, spans = [], {}
    fade = int(0.012 * SR)
    for lid, a, b, at, text, take, *rest in E.LINES:
        fx = rest[0] if rest else {}
        key = (take, int(fx.get("lowpass", 0)))
        if key not in takes:
            takes[key] = load(*key)
        src, heard = takes[key]
        # Полусонное бурчание — тише и глуше (gain_db, lowpass).
        seg = src[int(a * SR):int(b * SR)].copy() * (10 ** (float(fx.get("gain_db", 0.0)) / 20))
        seg[:fade] *= np.linspace(0, 1, fade)
        seg[-fade:] *= np.linspace(1, 0, fade)
        k = int(at * SR)
        m = min(len(seg), n - k)
        mix[k:k + m] += seg[:m]
        (kodex if take == E.TAKE_K else claude)[k:k + m] += seg[:m]
        shift = at - a
        spans[lid] = (at, at + (b - a))
        got = [w for w in heard if a - 0.05 <= (w["t0"] + w["t1"]) / 2 <= b + 0.05]
        words.append({"line": lid, "text": text, "heard": [{**w, "t0": round(w["t0"] + shift, 3), "t1": round(min(w["t1"], b) + shift, 3)}
                                                           for w in got],
                      "start": round(at, 3), "end": round(at + (b - a), 3)})
    paths = work / "voice.wav", work / "voice_claude.wav", work / "voice_kodex.wav"
    for p, x in zip(paths, (mix, claude, kodex)):
        P1.write_wav(p, x)
    return *paths, words, spans


def masked(env: list[float], windows: list[tuple[float, float]], k: float = 1.0) -> list[float]:
    """Огибающая только в окнах (кто говорит в кадре) — остальное ноль; ``k`` — множитель (бурчание — слабее)."""
    out = [0.0] * len(env)
    for t0, t1 in windows:
        for i in range(max(0, int(t0 * FPS)), min(len(env), int(t1 * FPS) + 1)):
            out[i] = env[i] * k
    return out


# --------------------------------------------------------------------------- хореография

FACE_DOWN = {"lean": 50, "tilt": -5, "turn": 4, "armL": [-25, 40, 0.3, 0], "armR": [-10, 60, 0.4, 0], "squash": 0.95, "type": 0,
             "deflate": 0.3, "hop": 0, "shake": 0}
UPRIGHT = {"lean": 0, "tilt": 0, "turn": 0, "armL": "rest", "armR": "rest", "squash": 1.0, "type": 0, "deflate": 0, "hop": 0, "shake": 0}
TYPING = {**UPRIGHT, "lean": 12, "turn": -6, "armL": "type", "armR": "type", "type": 1}

T_PUSH = 16.72  # толчок — на «на**й»
T_LAND = T_PUSH + 0.5
EDGE_POS = [-0.17, T, 0.08]  # рядом с креслом слева (на столе) — оттуда Клод 2 спихивает первого
# Нарезка аккаунтов 3–8: начало слотов (по 0.33 с).
MONT0, MSLOT, MONT_END = 24.37, 0.33, 26.35
SLOTS = {k: round(MONT0 + (k - 3) * MSLOT, 3) for k in range(3, 9)}
# Бит 9: Клод 9 слезает, берёт карандаш, несёт Кодексу в правую ручку.
T_OFF, T_GIVE, T_FIN = 36.35, 37.45, 38.75
HAND9_POS = [0.54, T, -0.07]


class Track:
    """Ключи одного героя-игрушки: поза, лицо, где стоит, виден ли."""

    def __init__(self, pos0: list[float], visible0: bool):
        self.pose: list[dict] = []
        self.face: list[dict] = []
        self.move: list[dict] = [{"t": 0, "position": pos0, "ease": "step"}]
        self.vis: list[dict] = [{"t": 0, "on": visible0}]
        self._look = [0, 0]

    def p(self, t: float, ease: str = "step", **kw) -> None:
        self.pose.append({"t": round(t, 3), "ease": ease, **kw})

    def f(self, t: float, expr: str, look: list[float] | None = None, blend: float = 0.0) -> None:
        # Взгляд тянется от предыдущего ключа: перед сменой — ключ «держи» прежний взгляд.
        if look is not None and self.face:
            self.face.append({"t": round(t - 0.1, 3), "expr": self.face[-1]["expr"], "blend": 0, "look": self._look})
        k = {"t": round(t, 3), "expr": expr, "blend": blend}
        if look is not None:
            k["look"] = look
            self._look = look
        self.face.append(k)

    def at(self, t: float, pos: list[float], ease: str = "step") -> None:
        self.move.append({"t": round(t, 3), "position": [round(v, 4) for v in pos], "ease": ease})

    def show(self, t: float, on: bool) -> None:
        self.vis.append({"t": round(t, 3), "on": on})

    def hop_to(self, t0: float, t1: float, p0: list[float], p1: list[float], h: float = 0.016, steps: int | None = None) -> None:
        """Пропрыгать из p0 в p1: позиция линейно, подскоки по шагам (зверёк «ходит» прыжками)."""
        n = steps or max(1, round((t1 - t0) / 0.17))
        dt = (t1 - t0) / n
        self.at(t0, p0)
        for i in range(n):
            a = t0 + i * dt
            k = (i + 1) / n
            self.at(a + dt, [p0[j] + (p1[j] - p0[j]) * k for j in range(3)], ease="linear")
            self.p(a, "step", hop=0)
            self.p(a + dt * 0.45, "outQuad", hop=h)
            self.p(a + dt, "inQuad", hop=0)

    def spec(self, cid: str, num: int, yaw: float, voice: list[float] | None) -> dict:
        ch = E.critter(cid, num, sorted(self.pose, key=lambda k: k["t"]), sorted(self.face, key=lambda k: k["t"]), self.move[0]["position"],
                       yaw, voice=voice, autoBlink=True, stepFps=15)
        ch["move"] = sorted(self.move, key=lambda k: k["t"])
        ch["visible"] = sorted(self.vis, key=lambda k: k["t"])
        return ch


def pile_pose(k: int) -> tuple[list[float], dict]:
    pos, yaw, lay = E.pile_place(k)
    return pos, {**UPRIGHT, **lay, "turn": yaw - E.HERO_YAW, "armL": "down", "armR": "down", "deflate": 0.35}


def to_pile(tr: Track, t: float, k: int) -> None:
    pos, pose = pile_pose(k)
    tr.at(t, pos)
    tr.p(t, "step", **pose)
    tr.f(t, "sleep", [0, 0])


def slump_pos() -> list[float]:
    sx, sy, sz = E.SEAT
    return [sx - 0.01, sy - 0.035, sz + 0.035]


def faceplant(tr: Track, t: float, dur: float = 0.12) -> None:
    tr.p(t, "step", **{**UPRIGHT, "type": 0})
    tr.p(t + dur, "outQuad", **FACE_DOWN)
    tr.at(t, E.SEAT)
    tr.at(t + dur, slump_pos(), ease="outQuad")
    tr.f(t + dur * 0.6, "ko", [0, 0])


def claudes(env: list[float], spans: dict) -> tuple[list[dict], dict]:
    """Аккаунты 1–9: кресло → лимит → гора. Возвращает героев и моменты для сведения."""
    sp = spans
    slump = slump_pos()
    out = []
    ev: dict = {}
    # --- Аккаунт 1: обложка, работа, лимит; его спихивают — летит в гору и бурчит «Ещё один… двести…».
    a = Track(slump, True)
    a.p(0, **FACE_DOWN)
    a.f(0, "ko")
    a.at(0.85, E.SEAT)
    a.p(0.85, **UPRIGHT)
    a.f(0.85, "neutral", [0, 0])
    a.p(9.2, **TYPING)
    a.f(9.2, "working", [-0.2, -0.4])
    a.p(10.95, **{**TYPING, "shake": 0.45, "lean": 16, "squash": 1.04})
    a.f(10.95, "focused", [-0.3, -0.5])
    a.p(11.6, **{**UPRIGHT, "squash": 1.08, "hop": 0.01})
    a.f(11.6, "shocked", [0, 0.2])
    faceplant(a, 11.85)
    ev["plant1"] = 11.95
    a.at(T_PUSH, slump)
    a.at(T_PUSH + 0.25, [0.3, T + 0.24, -0.08], ease="outQuad")
    p0, pose0 = pile_pose(0)
    a.at(T_LAND, p0, ease="inQuad")
    a.p(T_PUSH, **FACE_DOWN)
    a.p(T_PUSH + 0.25, "linear", **{**UPRIGHT, "lean": -30, "tilt": -70, "armL": "flail", "armR": "flail"})
    a.p(T_LAND, "linear", **pose0)
    a.f(T_PUSH, "shocked", [0, 0.3])
    a.f(T_LAND, "ko", [0, 0])
    # Бурчит в полусне: глаза-«ковшики», чуть приоткрывает, снова засыпает.
    a.f(sp["m1"][0] - 0.1, "sigh", [0.2, 0.3])
    a.f(sp["m1"][0] + 1.0, "doomed", [0.1, 0.4])
    a.f(sp["m1"][1] + 0.05, "sleep", [0, 0])
    ev.update(push=T_PUSH, land1=T_LAND)
    out.append(a.spec("c1", 1, E.HERO_YAW, [max(x, y) for x, y in zip(masked(env, [(9.2, 11.6)]), masked(env, [sp["m1"]], 0.8))]))
    # --- Аккаунт 2: прискакал слева, залез на край кресла, «Пшёл на**й отсюда» — толчок, спросил, работал, лимит.
    a = Track([-0.42, T, 0.18], False)
    a.show(12.95, True)
    a.p(0, **UPRIGHT)
    a.p(12.95, **{**UPRIGHT, "turn": 70})
    a.hop_to(12.95, 13.75, [-0.48, T, 0.18], [-0.24, T, 0.1])
    a.at(13.75, [-0.24, T, 0.1])
    a.at(13.95, EDGE_POS, ease="outQuad")
    a.p(13.75, "step", hop=0)
    a.p(13.85, "outQuad", hop=0.03, turn=40)
    a.p(13.95, "inQuad", hop=0)
    a.f(12.95, "neutral", [0.6, 0])
    a.f(13.95, "deadpan", [0.8, -0.3])
    a.p(15.6, "step", lean=0, turn=40, armL="rest", armR="rest")
    a.p(16.2, "inOutSine", lean=-8, turn=50, armL="back", armR="back", squash=0.96)
    a.f(16.2, "angry", [0.9, -0.2])
    a.p(T_PUSH - 0.02, "outQuad", lean=18, turn=62, armL="jab", armR="jab", squash=1.08)
    a.p(T_PUSH + 0.35, "inOutSine", lean=0, turn=40, armL="rest", armR="rest", squash=1.0)
    a.at(17.3, EDGE_POS)
    a.at(17.6, E.SEAT, ease="inOutSine")
    a.p(17.3, "step", hop=0)
    a.p(17.45, "outQuad", hop=0.02, turn=20)
    a.p(17.6, "inQuad", hop=0, turn=0)
    a.f(17.6, "deadpan", [0.8, -0.3])
    a.f(sp["c10"][0], "neutral", [0, 0])
    a.p(sp["c10"][1] + 0.1, "inOutSine", tilt=4)
    a.f(21.35, "neutral", [-0.3, -0.2])
    a.p(23.05, **TYPING)
    a.f(23.05, "working", [-0.2, -0.4])
    a.p(24.2, **{**UPRIGHT, "squash": 1.08})
    a.f(24.2, "shocked", [0, 0.2])
    faceplant(a, 24.25, 0.08)
    to_pile(a, MONT0, 1)
    out.append(a.spec("c2", 2, E.HERO_YAW, masked(env, [sp["p1"], sp["c10"]])))
    # --- Аккаунты 3–8: быстрая нарезка (печатает → «Лимит» → лицом в клаву → в гору).
    for k in range(3, 9):
        s = SLOTS[k]
        a = Track(E.SEAT, False)
        a.p(0, **TYPING)
        a.f(0, "working", [-0.2, -0.4])
        a.show(s, True)
        a.p(s, **{**TYPING, "shake": 0.3})
        a.f(s + 0.15, "shocked", [0, 0.2])
        faceplant(a, s + 0.18, 0.06)
        to_pile(a, s + MSLOT if k < 8 else MONT_END, k - 1)
        out.append(a.spec(f"c{k}", k, E.HERO_YAW, None))
    # --- Аккаунт 9.
    a = Track([-0.4, T, 0.2], False)
    a.show(MONT_END, True)
    a.p(0, **{**UPRIGHT, "turn": 70})
    a.f(0, "neutral", [0.5, 0])
    a.hop_to(MONT_END, MONT_END + 0.75, [-0.4, T, 0.2], [-0.05, T, 0.12])
    a.at(MONT_END + 0.75, [-0.05, T, 0.12])
    a.at(MONT_END + 1.0, E.SEAT, ease="outQuad")
    a.p(MONT_END + 0.75, "step", hop=0)
    a.p(MONT_END + 0.88, "outQuad", hop=0.035, turn=20)
    a.p(MONT_END + 1.0, "inQuad", hop=0, turn=0)
    a.f(MONT_END + 1.0, "neutral", [0, 0])
    a.p(sp["c10b"][1] + 0.1, "inOutSine", tilt=4)
    a.p(30.2, **TYPING)
    a.f(30.2, "deadpan", [-0.2, -0.4])
    a.p(35.7, **{**UPRIGHT, "lean": 4})
    a.f(35.7, "deadpan", [0, 0])
    # Слез с кресла, взял карандаш из стакана, пропрыгал к Кодексу, протянул (левой ручкой — к его правой).
    a.p(T_OFF, **{**UPRIGHT, "turn": 60})
    a.f(T_OFF, "grumpy", [0.6, -0.2])
    a.at(T_OFF, E.SEAT)
    a.at(T_OFF + 0.2, [0.3, T, 0.15], ease="inQuad")
    a.p(T_OFF + 0.1, "outQuad", hop=0.02)
    a.p(T_OFF + 0.2, "inQuad", hop=0, squash=0.92)
    a.p(T_OFF + 0.28, "outQuad", squash=1.0)
    a.hop_to(T_OFF + 0.2, T_OFF + 0.38, [0.3, T, 0.15], [0.38, T, 0.16], steps=1)
    a.hop_to(T_OFF + 0.38, T_OFF + 0.85, [0.38, T, 0.16], HAND9_POS, steps=3)
    a.p(T_OFF + 0.9, "outQuad", turn=78, lean=-4, squash=1.05, armL=[70, 45, 0.8, 0])
    a.f(T_OFF + 0.85, "grumpy", [0.7, 0.4])
    a.p(T_GIVE + 0.15, "inOutSine", armL="rest", squash=1.0, lean=0)
    a.p(37.95, "outQuad", turn=10)
    a.f(37.95, "grumpy", [-0.5, 0])
    a.hop_to(38.1, 38.7, HAND9_POS, [0.4, T, 0.2], steps=3)
    to_pile(a, T_FIN, 8)
    out.append(a.spec("c9", 9, E.HERO_YAW, masked(env, [sp["c10b"], sp["c16"], sp["c17"], sp["c18"]])))
    ev["slots"] = SLOTS
    return out, ev


def kodex(env: list[float], variant: str) -> dict:
    """Кодекс (kodex2: тот же зверёк, круглее и светлее): чай в левой ручке, карандаш — в правую."""
    K = E.KODEX
    pose: list[dict] = []
    face: list[dict] = []
    cur = {"armL": "mug", "armR": "rest", "holdL": "mug", "holdR": "none", "lean": 0, "tilt": 0, "turn": 0, "sip": 0, "squash": 1.0}
    look = {"expr": "neutral", "look": [0, 0]}

    def go(t, dur=0.3, ease="inOutSine", **kw):
        """Сменить позу к моменту t за dur с (ключ-«держи» в t − dur: ключи тянутся от предыдущего ключа поля)."""
        pose.append({"t": round(t - dur, 3), "ease": "step", **{k: cur[k] for k in kw}})
        pose.append({"t": round(t, 3), "ease": ease, **kw})
        cur.update(kw)

    def f(t, expr, lk=None):
        face.append({"t": round(t - 0.12, 3), "expr": look["expr"], "look": look["look"]})
        face.append({"t": round(t, 3), "expr": expr, "look": lk if lk is not None else look["look"]})
        look.update(expr=expr, look=lk if lk is not None else look["look"])

    pose.append({"t": 0, "ease": "step", **cur})
    face.append({"t": 0, "expr": "neutral", "look": [0, 0]})
    # Пьёт чай всё это время (в фоне общих планов); «И пьёт чай» — крупно.
    for t0 in (21.65, 27.65, 32.65):
        go(t0 + 0.3, 0.3, armL="sip", sip=1, lean=-6)
        go(t0 + 0.8, 0.3, armL="mug", sip=0, lean=0)
    f(32.95, "dreamy", [-0.2, 0.3])
    f(33.4, "neutral", [-0.4, 0])
    # «А может, я что-нибудь сделаю?» — вежливо поднимает ручку.
    f(33.5, "surprised", [-0.5, 0.1])
    go(33.85, 0.35, armR=[60, 25, 0.5, 0], tilt=-6)
    go(35.4, 0.3, armR="rest", tilt=0)
    # «Нет.» — сник.
    f(35.8, "sigh")
    go(35.95, 0.15, squash=0.94, lean=6)
    f(36.3, "neutral", [-0.5, -0.2])
    go(36.5, 0.2, squash=1.0, lean=0)
    # Карандаш: поворачивается к Клоду, протягивает правую ручку, смотрит на карандаш.
    go(37.05, 0.3, turn=15)
    f(36.95, "surprised", [-0.6, -0.5])
    go(37.4, 0.3, armR=[20, 40, 0.6, 0], lean=6)
    go(T_GIVE + 0.35, 0.3, armR=[38, 62, 0.5, 0], lean=10)
    f(T_GIVE + 0.1, "cute", [-0.5, -0.6])
    f(38.3, "neutral", [-0.5, -0.7])
    # Финал: у листа, рисует (кружка — на столе).
    pose.append({"t": T_FIN, "ease": "step", "turn": 30, "holdR": "pencil", "holdL": "none", "lean": 22, "tilt": 0, "armL": "rest",
                 "armR": [-2, 58, 0.25, 0], "sip": 0, "squash": 1.0})
    face.append({"t": T_FIN - 0.01, "expr": "focused", "look": [0, -0.8]})
    face.append({"t": T_FIN, "expr": "focused", "look": [0, -0.8]})
    t, i = T_FIN, 0
    while t < DUR:
        t += 0.13
        pose.append({"t": round(t, 3), "ease": "inOutSine", "armR": [4, 62, 0.3, 0] if i % 2 == 0 else [-6, 52, 0.22, 0]})
        i += 1
    return {"id": "kodex", "style": "kodex2", "variant": variant, "width": E.W_HERO, "position": K, "yaw": -30, "pose": pose, "face": face,
            "stepFps": 15, "voice": {"fps": FPS, "start": 0, "values": env, "gain": 1.2},
            "move": [{"t": 0, "position": K, "ease": "step"}, {"t": T_FIN, "position": [E.PAPER[0] - 0.01, T, E.PAPER[2] - 0.17], "ease": "step"}]}


def session() -> dict:
    limits = [0.0, 11.6, 24.2] + [round(s + 0.16, 3) for s in SLOTS.values()]
    resets = [{"t": 8.0, "account": 1, "idle": 1.0, "speed": 8}, {"t": 20.0, "account": 2, "idle": 2.6, "speed": 8}]
    resets += [{"t": s, "account": k, "speed": 40} for k, s in SLOTS.items()]
    resets += [{"t": MONT_END, "account": 9, "idle": 3.8, "speed": 8}]
    return {"account": 1, "speed": 8, "limits": limits, "resets": resets}


def vis(*ranges: tuple[float, float]) -> list[dict]:
    out = [{"t": 0, "on": False}]
    for a, b in ranges:
        out += [{"t": round(a, 3), "on": True}, {"t": round(b, 3), "on": False}]
    return sorted(out, key=lambda k: k["t"])


def objects() -> list[dict]:
    sx, sy, sz = E.SEAT
    objs = E.room_objects(session())
    xu = E.X_USER
    objs += E.user_objects()
    objs += [
        # Подписка за 200 $ на стене у юзера — на «дорого» её перечёркивает красный крест.
        {"id": "sub_plate", "primitive": "sign_plate", "params": {"text": "200 $", "sub": "подписка в месяц", "bg": "#f6e6cf", "fg": "#2b1810",
                                                                  "width": 0.3, "height": 0.15, "tilt": 4, "glow": 0.1},
         "position": [xu + 0.34, T + 0.42, -0.4]},
    ]
    for i, rz in enumerate((38, -38)):
        objs.append({"id": f"sub_x{i}", "primitive": "rounded_box", "params": {"size": [0.36, 0.028, 0.012], "color": "#d8342c", "radius": 0.006,
                                                                                "emissive": "#d8342c", "emissiveIntensity": 0.3},
                     "position": [xu + 0.34, T + 0.42, -0.385], "rotation": [0, 0, rz], "visible": vis((5.72, DUR + 1)),
                     "keys": [{"t": 0, "position": [xu + 0.34, T + 0.42, -0.31]},
                              {"t": 5.72, "position": [xu + 0.34, T + 0.42, -0.31], "ease": "step"},
                              {"t": 5.82, "position": [xu + 0.34, T + 0.42, -0.385], "ease": "outBack"}]})
    shop, _ = E.shelf_objects()
    objs += shop
    objs += [
        {"id": "limit_sign", "primitive": "sign_plate", "params": {"text": "ЛИМИТ", "bg": "#d8342c", "fg": "#fff6ea", "width": 0.2, "height": 0.078,
                                                                   "tilt": -7, "glow": 0.35},
         "position": [sx - 0.01, T + 0.25, sz + 0.02], "rotation": [0, E.HERO_YAW, 0], "visible": vis((0, 0.85), (11.9, 12.9)),
         "keys": [{"t": 0, "position": [sx - 0.01, T + 0.25, sz + 0.02]},
                  {"t": 11.9, "position": [sx - 0.01, T + 0.4, sz + 0.02], "ease": "step"},
                  {"t": 12.05, "position": [sx - 0.01, T + 0.25, sz + 0.02], "ease": "outBack"}]},
        # Стакан с карандашом у клавиатуры (бит 9) и карандаш, который Клод 9 несёт Кодексу.
        {"id": "holder", "primitive": "cylinder", "params": {"size": [0.05, 0.07, 0.05], "color": "#3f7f84", "segments": 14},
         "position": [0.44, T, 0.25]},
        {"id": "pencilA", "primitive": "cylinder", "params": {"size": [0.016, 0.15, 0.016], "color": "#f2c14e", "segments": 6},
         "position": [0.44, T + 0.08, 0.25], "rotation": [0, 0, 12], "visible": [{"t": 0, "on": True}, {"t": T_GIVE, "on": False}],
         "attach": [{"to": "c9:hand_l", "start": T_OFF + 0.38, "end": T_GIVE, "fadeIn": 0.1, "fadeOut": 0, "offset": [0.0, 0.05, 0.02],
                     "upright": False}]},
        {"id": "pencilB", "primitive": "cylinder", "params": {"size": [0.016, 0.15, 0.016], "color": "#f2c14e", "segments": 6},
         "position": PENCIL_B, "rotation": [0, 0, 12], "visible": vis((T_GIVE, T_FIN)),
         "attach": [{"to": "kodex:hand_r", "start": T_GIVE, "end": T_FIN, "fadeIn": 0.12, "fadeOut": 0, "offset": [0.0, 0.04, 0.0], "upright": False}]},
        # Финал: лист с наброском, кружка Кодекса на столе.
        {"id": "paper", "primitive": "paper_sheet", "params": {"width": 0.15, "height": 0.2, "panelStart": T_FIN, "panelParams": {"start": 0, "dur": 2.0}},
         "position": E.PAPER, "rotation": [0, 8, 0], "visible": vis((T_FIN, DUR + 1))},
        {"id": "kodex_mug", "primitive": "mug", "params": {"color": "#f2efe8", "radius": 0.028, "height": 0.055},
         "position": [E.PAPER[0] + 0.17, T, E.PAPER[2] - 0.05], "visible": vis((T_FIN, DUR + 1))},
    ]
    # z z z над горой: появляются, когда «вырубленный» долетел (над первым — после бурчания).
    land = {0: 20.1, 2: SLOTS[4], 3: SLOTS[5], 5: SLOTS[7], 8: T_FIN}
    for k, t0 in land.items():
        pos, _ = pile_pose(k)
        objs.append({"id": f"zzz{k}", "primitive": "zzz", "params": {"size": 0.045, "phase": k * 0.7},
                     "position": [pos[0] + 0.04, pos[1] + 0.16, pos[2] + 0.05], "visible": vis((t0, DUR + 1))})
    return objs


# Где карандаш ждёт перехода в ручку Кодекса (рука Клода 9 в момент T_GIVE; уточняется --inspect).
PENCIL_B = [0.66, T + 0.18, -0.15]


def camera(ev: dict) -> tuple[dict, list[dict], list[float]]:
    sx, sy, sz = E.SEAT
    fy = sy + H.face_height(E.W_HERO)
    xu, xs = E.X_USER, E.X_SHELF
    c = S.screen_center()
    keys: list[dict] = []
    cuts: list[float] = []

    def shot(t0, t1, A, B=None, fov=34, dof=0.4, ease="inOutSine", roll=0.0):
        B = B or A
        k0 = {"t": round(t0, 3), "position": [round(v, 4) for v in A[0]], "target": [round(v, 4) for v in A[1]], "fov": fov, "dof": dof, "roll": roll}
        if keys:
            k0["cut"] = True
        keys.extend([k0, {"t": round(t1 - 0.001, 3), "position": [round(v, 4) for v in B[0]], "target": [round(v, 4) for v in B[1]],
                          "fov": fov, "dof": dof, "roll": roll, "ease": ease}])
        cuts.append(round(t0, 3))

    def push(A, k=0.08):
        p, t = A
        return ([p[i] + (t[i] - p[i]) * k for i in range(3)], t)

    def mon(d, side=-17.0):
        a = math.radians(S.MONITOR_YAW + side)
        return ([c[0] + math.sin(a) * d, c[1] + 0.01, c[2] + math.cos(a) * d], list(c))

    K = E.KODEX
    kf = (math.sin(math.radians(-30)), math.cos(math.radians(-30)))  # куда смотрит Кодекс
    kfy = T + H.face_height(E.W_HERO)
    COVER = ([sx + 0.12, T + 0.11, sz + 1.3], [sx, T + 0.15, sz + 0.1])
    U_CU = ([xu - 0.07, T + 0.21, 0.66], [xu - 0.1, T + 0.18, -0.1])
    U_MED = ([xu + 0.06, T + 0.26, 1.55], [xu + 0.0, T + 0.17, -0.04])
    LAP = ([xu - 0.27, T + 0.14, 0.34], [xu + 0.13, T + 0.08, -0.11])
    PLATE = ([xu + 0.3, T + 0.42, 0.55], [xu + 0.34, T + 0.42, -0.4])
    U_PL = ([xu + 0.16, T + 0.33, 1.55], [xu + 0.12, T + 0.27, -0.2])
    SHELF = ([xs, T + 0.46, 2.45], [xs, T + 0.35, -0.12])
    PRICE = ([xs + 0.12, T + 0.38, 0.75], [xs + 0.16, T + 0.355, 0.04])
    BOX = ([xs, T + 0.55, 0.95], [xs, T + 0.53, -0.12])
    MED = ([sx + 0.24, fy + 0.1, sz + 1.45], [sx - 0.12, fy + 0.02, sz - 0.05])
    ECU = ([sx + 0.05, fy + 0.0, sz + 0.78], [sx, fy - 0.01, sz])
    PLANT = ([sx + 0.08, T + 0.35, sz + 1.6], [sx - 0.17, T + 0.2, sz - 0.15])
    WIDE_L = ([sx - 0.2, T + 0.36, sz + 1.9], [sx - 0.12, T + 0.14, sz])
    efy = EDGE_POS[1] + H.face_height(E.W_HERO)
    EDGE = ([EDGE_POS[0] + 0.02, efy + 0.02, 1.0], [EDGE_POS[0] + 0.03, efy, 0.05])
    EDGE_X = ([EDGE_POS[0] - 0.02, efy + 0.0, 0.78], [EDGE_POS[0] + 0.02, efy - 0.01, 0.06])
    SLUMP = ([sx + 0.1, T + 0.13, sz + 1.05], [sx + 0.0, T + 0.13, sz + 0.1])
    # Двое на кресле (Клод 2 на левом краю, первый — лицом в клаву) и место, куда тот улетит.
    TWO = ([sx - 0.18, T + 0.3, sz + 1.8], [sx - 0.12, T + 0.13, sz - 0.1])
    pile0, _ = pile_pose(0)
    # Гора — справа-спереди сверху: кресло (спинка) не загораживает.
    PILE0 = ([pile0[0] + 0.5, T + 0.32, 0.3], [pile0[0], T + 0.06, pile0[2]])
    PILE0_X = ([pile0[0] + 0.42, T + 0.27, 0.1], [pile0[0], T + 0.06, pile0[2]])
    CU = ([sx + 0.1, fy + 0.03, sz + 1.15], [sx + 0.05, fy - 0.01, sz])
    MA = ([sx - 0.15, T + 0.3, sz + 1.5], [sx + 0.08, T + 0.15, sz - 0.1])
    MB = ([sx + 0.45, T + 0.32, sz + 1.45], [sx + 0.12, T + 0.14, sz - 0.1])
    WIDE9 = ([sx + 0.0, T + 0.42, sz + 1.95], [sx + 0.12, T + 0.15, sz - 0.1])
    DOLLY_A = ([sx + 0.1, T + 0.3, sz + 1.2], [0.42, T + 0.14, -0.3])
    DOLLY_B = ([0.55, T + 0.26, 0.95], [K[0], kfy, K[2]])
    K_MED = ([K[0] + kf[0] * 1.25, kfy + 0.08, K[2] + kf[1] * 1.25], [K[0], kfy - 0.02, K[2]])
    K_CU = ([K[0] + kf[0] * 0.85, kfy + 0.03, K[2] + kf[1] * 0.85], [K[0], kfy - 0.01, K[2]])
    DIAG = ([-0.16, T + 0.22, 1.0], [0.4, T + 0.15, 0.03])
    HAND_W = ([0.3, T + 0.38, 1.6], [0.45, T + 0.14, -0.02])
    HAND2 = ([0.62, T + 0.22, 0.95], [0.65, T + 0.13, -0.1])
    K_LOOK = ([K[0] + kf[0] * 0.8 - 0.12, kfy + 0.06, K[2] + kf[1] * 0.8], [K[0] - 0.05, kfy - 0.02, K[2]])
    FIN = ([E.PAPER[0] + 0.02, T + 0.36, E.PAPER[2] + 1.0], [E.PAPER[0] - 0.02, T + 0.14, E.PAPER[2] - 0.25])

    shot(0.0, 0.85, COVER, push(COVER, 0.06), fov=30, dof=0.3)
    shot(0.85, 1.9, U_CU, push(U_CU, 0.06), dof=0.5)
    shot(1.9, 3.0, U_MED, push(U_MED, 0.07), dof=0.35)
    shot(3.0, 4.05, LAP, push(LAP, 0.08), dof=0.3)
    shot(4.05, 5.0, PLATE, push(PLATE, 0.08), dof=0.3)
    shot(5.0, 6.45, U_PL, push(U_PL, 0.06), dof=0.35)
    shot(6.45, 7.55, SHELF, push(SHELF, 0.08), dof=0.35)
    shot(7.55, 8.5, PRICE, push(PRICE, 0.08), dof=0.4, roll=-3)
    shot(8.5, 9.2, BOX, push(BOX, 0.1), dof=0.5)
    shot(9.2, 10.25, MED, push(MED, 0.06), dof=0.4)
    shot(10.25, 10.95, mon(1.3), mon(1.18), fov=30, dof=0.2)
    shot(10.95, 11.6, ECU, push(ECU, 0.06), dof=0.5, roll=3)
    shot(11.6, 11.85, mon(1.2), mon(1.12), fov=30, dof=0.2)
    shot(11.85, 12.9, PLANT, push(PLANT, 0.03), dof=0.35)
    shot(12.9, 13.95, WIDE_L, push(WIDE_L, 0.06), dof=0.35)
    shot(13.95, 14.68, EDGE, push(EDGE, 0.05), dof=0.5)
    shot(14.68, 15.6, TWO, push(TWO, 0.04), dof=0.35)
    shot(15.6, 16.22, SLUMP, push(SLUMP, 0.05), dof=0.45)
    shot(16.22, 16.68, EDGE_X, push(EDGE_X, 0.05), dof=0.5, roll=-3)
    shot(16.68, 17.4, TWO, push(TWO, 0.03), dof=0.35)
    shot(17.4, 18.7, PILE0, push(PILE0, 0.06), dof=0.5)
    shot(18.7, 20.05, PILE0_X, push(PILE0_X, 0.06), dof=0.55, roll=4)
    shot(20.05, 21.35, CU, push(CU, 0.05), dof=0.45)
    shot(21.35, 22.35, PILE0, push(PILE0, 0.08), dof=0.5)
    shot(22.35, 23.05, mon(1.3), mon(1.2), fov=30, dof=0.2)
    shot(23.05, 24.2, MED, push(MED, 0.05), dof=0.4)
    shot(24.2, MONT0, mon(1.2), fov=30, dof=0.2)
    for i, s in enumerate(SLOTS.values()):
        A = MA if i % 2 == 0 else MB
        shot(s, min(s + MSLOT, MONT_END), A, push(A, 0.04), dof=0.35, roll=(-3 if i % 2 == 0 else 3))
    shot(MONT_END, 27.5, WIDE9, push(WIDE9, 0.06), dof=0.35)
    shot(27.5, 28.9, CU, push(CU, 0.04), dof=0.45)
    shot(28.9, 30.2, CU, push(CU, 0.05), dof=0.45)
    shot(30.2, 31.3, DOLLY_A, DOLLY_B, dof=0.3, ease="inOutCubic")
    shot(31.3, 32.55, K_MED, push(K_MED, 0.08), dof=0.35)
    shot(32.55, 33.45, K_CU, push(K_CU, 0.05), dof=0.45)
    shot(33.45, 34.6, K_CU, push(K_CU, 0.08), dof=0.45, roll=-2)
    shot(34.6, 35.7, DIAG, push(DIAG, 0.04), fov=48, dof=0.3)
    shot(35.7, T_OFF - 0.05, ECU, ECU, dof=0.5)
    shot(T_OFF - 0.05, 37.15, HAND_W, push(HAND_W, 0.05), dof=0.3)
    shot(37.15, 37.95, HAND2, push(HAND2, 0.06), dof=0.35)
    shot(37.95, T_FIN, K_LOOK, push(K_LOOK, 0.06), dof=0.45)
    shot(T_FIN, 39.85, FIN, push(FIN, 0.06), dof=0.3)
    shot(39.85, DUR, push(FIN, 0.1), push(FIN, 0.2), dof=0.3)
    keys[-1]["t"] = DUR
    shake = [{"t": 11.97, "amp": 0.007, "duration": 0.5}, {"t": ev["push"], "amp": 0.006, "duration": 0.4},
             {"t": ev["land1"], "amp": 0.004, "duration": 0.35}, {"t": 24.33, "amp": 0.004, "duration": 0.3}]
    shake += [{"t": round(s + 0.24, 3), "amp": 0.003, "duration": 0.2} for s in SLOTS.values()]
    trans = [{"kind": "whip", "t": 0.85, "dir": 1}, {"kind": "whip", "t": 6.45, "dir": -1}, {"kind": "smash", "t": 11.6},
             {"kind": "smash", "t": 16.68}, {"kind": "glitch", "t": 24.2, "duration": 0.17}, {"kind": "whip", "t": MONT_END, "dir": 1},
             {"kind": "smash", "t": 35.7}, {"kind": "whip", "t": T_FIN, "dir": -1}]
    return {"fov": 34, "keys": keys, "shake": shake, "clamp": False}, trans, cuts


def build_spec(env_c: list[float], env_k: list[float], spans: dict, variant: str) -> tuple[dict, dict]:
    chars, ev = claudes(env_c, spans)
    _, shop_chars = E.shelf_objects()
    for ch in shop_chars:
        if ch["id"] == "b2":  # «Нас.» — тот, что в коробке, смотрит в камеру без улыбки
            ch["face"] = [{"t": 0, "expr": "happy"}, {"t": 8.62, "expr": "deadpan", "blend": 0.05, "look": [0, 0]}]
            ch["autoBlink"] = False
    chars += shop_chars + [kodex(env_k, variant)]
    cam, trans, cuts = camera(ev)
    env = {
        "background": "#1d2233", "fog": False, "exposure": 0.95, "envIntensity": 0.0, "toneMapping": "neutral",
        "toon": {"preset": "ink", "outline": {"width": 5.5, "ref": 1.9}},
        "post": {"ao": 0, "bloom": {"strength": 0.2, "radius": 0.35, "threshold": 1.05}, "vignette": 0.34, "grain": 0.05, "ca": 0.2, "dof": True,
                 "grade": {"saturation": 1.06, "contrast": 1.05, "shadows": "#3b3466", "highlights": "#ffd9aa", "split": 0.3}},
        "transitions": trans,
    }
    overlays = [{"kind": "toast", "t0": 11.65, "t1": 12.85, "title": "Лимит исчерпан", "text": "аккаунт 1 · попробуйте через 5 ч", "icon": "!", "y": 300}]
    spec = {"width": 1080, "height": 1920, "fps": FPS, "duration": DUR, "environment": env, "lights": E.lights(), "objects": objects(),
            "characters": chars, "camera": cam, "overlays": overlays, "quality": {"shadows": True, "shadowSize": 2048, "antialias": True}}
    ev["cuts"] = cuts
    return spec, ev


# --------------------------------------------------------------------------- субтитры, звук


def captions(video: Path, words: list[dict], out: Path, work: Path) -> None:
    from app.motion_render_comp import build_compositor_args, write_comp

    layers = [
        {"id": "top", "type": "text", "start": 0.85, "end": 2.6, "x": 0.5, "y": 0.105,
         "props": {"text": TOP_CARD, "font": "Montserrat", "weight": 800, "size": 46, "color": "#2b1810", "align": "center",
                   "bg": {"color": "#f6e6cf", "opacity": 1, "radius": 14, "padX": 30, "padY": 14}, "stroke": {"color": "#f6e6cf", "width": 0}},
         "out": {"type": "fade", "duration": 0.2}},
        {"id": "hook", "type": "text", "start": 0.0, "end": 0.85, "x": 0.5, "y": 0.16,
         "props": {"text": E.HOOK, "font": "Montserrat", "weight": 900, "size": 150, "color": "#fff6e8", "align": "center", "uppercase": True,
                   "stroke": {"color": "#2b1810", "width": 16}, "bg": {"color": "#d8342c", "opacity": 1, "radius": 34, "padX": 44, "padY": 24}}},
        {"id": "caps", "type": "captions", "start": 0.0, "end": DUR, "x": 0.5, "y": 0.785,
         "props": {"style": "hormozi", "size": 78, "activeColor": "#FFD23F", "uppercase": True, "maxWords": 1, "maxLines": 1,
                   "stroke": {"color": "#2b1810", "width": 11}}},
    ]
    comp = write_comp(work / "comp.json", width=1080, height=1920, fps=FPS, duration=DUR, layers=layers, camera=[], words=words)
    (work / "assets.json").write_text("{}", "utf-8")
    proc = subprocess.run(build_compositor_args(comp, work / "assets.json", input_path=video, output_path=out), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=False, stdin=subprocess.DEVNULL)
    if proc.returncode != 0 or not out.is_file():
        raise RuntimeError(f"субтитры: {proc.stderr.strip()[-1200:]}")


def sfx_list(ev: dict) -> list[dict]:
    fs, ki = P1.PACKS / "freesound", P1.PACKS / "kenney-interface"
    out = [
        {"t": 0.8, "path": fs / "whoosh-3.ogg", "volume": 0.35},
        {"t": 3.0, "path": ki / "click_002.ogg", "volume": 0.5},
        {"t": 4.0, "path": fs / "whoosh-4.ogg", "volume": 0.3},
        {"t": 5.72, "path": fs / "punch.ogg", "volume": 0.45},
        {"t": 6.4, "path": fs / "whoosh-3.ogg", "volume": 0.35},
        {"t": 7.62, "path": fs / "cha-ching.ogg", "volume": 0.5},
        {"t": 8.55, "path": fs / "impact-low.ogg", "volume": 0.35},
        {"t": 9.2, "path": fs / "typing.ogg", "volume": 0.5, "len": 2.4},
        {"t": 11.6, "path": fs / "glitch.ogg", "volume": 0.45, "len": 0.3},
        {"t": 11.62, "path": fs / "ding.ogg", "volume": 0.6},
        {"t": ev["plant1"] - 0.03, "path": fs / "impact-big.ogg", "volume": 0.6},
        {"t": 13.0, "path": fs / "boing.ogg", "volume": 0.2, "len": 0.5},
        {"t": 13.9, "path": fs / "pop.ogg", "volume": 0.35},
        {"t": ev["push"] - 0.03, "path": fs / "punch.ogg", "volume": 0.6},
        {"t": ev["push"], "path": fs / "whoosh-2.ogg", "volume": 0.35},
        {"t": ev["land1"], "path": fs / "impact-low.ogg", "volume": 0.5},
        {"t": 22.35, "path": ki / "click_001.ogg", "volume": 0.5},
        {"t": 23.05, "path": fs / "typing.ogg", "volume": 0.45, "len": 1.15},
        {"t": 24.2, "path": fs / "ding.ogg", "volume": 0.55},
        {"t": 24.3, "path": fs / "impact-big.ogg", "volume": 0.45},
        {"t": MONT0, "path": fs / "riser.ogg", "volume": 0.3, "len": 2.0},
        {"t": MONT_END - 0.05, "path": fs / "whoosh-2.ogg", "volume": 0.35},
        {"t": MONT_END + 0.95, "path": fs / "pop.ogg", "volume": 0.35},
        {"t": 30.2, "path": fs / "whoosh-1.ogg", "volume": 0.3},
        {"t": 31.3, "path": fs / "triangle.ogg", "volume": 0.35},
        {"t": 35.7, "path": fs / "drama-boom.ogg", "volume": 0.3},
        {"t": T_OFF, "path": fs / "whoosh-4.ogg", "volume": 0.3},
        {"t": T_OFF + 0.38, "path": fs / "pop-cartoon.ogg", "volume": 0.35},
        {"t": T_GIVE, "path": fs / "pop.ogg", "volume": 0.45},
        {"t": T_FIN - 0.05, "path": fs / "whoosh-3.ogg", "volume": 0.3},
    ]
    for s in ev["slots"].values():
        out += [{"t": s, "path": ki / "click_002.ogg", "volume": 0.45}, {"t": s + 0.16, "path": fs / "ding.ogg", "volume": 0.35, "len": 0.3},
                {"t": s + 0.22, "path": fs / "impact-low.ogg", "volume": 0.35, "len": 0.4}]
    return out


def mix(video: Path, voice: Path, voice_claude: Path, ev: dict, out: Path, work: Path, report: dict) -> None:
    from app.audio_mix_model import ebur128
    from app.motion import asset_key
    from app.motion_render import build_audio_layers_args, speech_level_db
    from app.stories_3d import voice_lufs

    music = P1.MUSIC_DIR / "lofi-beat.mp3"
    if not music.is_file():
        music = P1.MUSIC_DIR / "chill-keys.mp3"
    voice_db = voice_lufs(voice_claude)
    layers = [{"id": "voice", "type": "audio", "start": 0.0, "end": DUR, "props": {"asset_id": 9001, "mode": "track", "volume": 1.0, "role": "voice"}}]
    assets = {asset_key(9001): str(voice), asset_key(9002): str(music)}
    m_lufs = float(ebur128(music)["i"])
    gain = round(voice_db - 14.0 - m_lufs, 2)
    # Музыка обрывается на «Лимите» (бум и тишина), возвращается со следующим «я».
    layers.append({"id": "music", "type": "audio", "start": 0.0, "end": 11.62,
                   "props": {"asset_id": 9002, "mode": "track", "volume": 1.0, "gain_db": gain, "role": "music", "fadeIn": 0.3, "fadeOut": 0.05}})
    layers.append({"id": "music2", "type": "audio", "start": 12.85, "end": DUR,
                   "props": {"asset_id": 9002, "mode": "track", "volume": 1.0, "gain_db": gain, "role": "music", "offset": 12.85, "fadeIn": 0.4,
                             "fadeOut": 0.5}})
    report["music"] = {"file": music.name, "lufs": m_lufs, "voice_lufs": voice_db, "gain_db": gain}
    notes: list[str] = []
    for n, s in enumerate(sfx_list(ev)):
        if not Path(s["path"]).is_file():
            notes.append(f"нет звука {s['path']}")
            continue
        key = 9100 + n
        assets[asset_key(key)] = str(s["path"])
        end = s["t"] + float(s.get("len") or 2.5)
        layers.append({"id": f"sfx{n}", "type": "audio", "start": round(s["t"], 3), "end": round(min(DUR, end), 3),
                       "props": {"asset_id": key, "volume": s["volume"], "mode": "effect", **({"fadeOut": 0.1} if s.get("len") else {})}})
    pre = work / "mixed.mp4"
    args = build_audio_layers_args(video, pre, layers, assets, DUR, clip_has_audio=False, speech_db=speech_level_db(voice_claude), notes=notes)
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

    script = " ".join(ln[4] for ln in E.LINES).replace("долбо**", "долбоеб").replace("на**й", "нахуй").replace("двести", "200")
    want = [norm(w) for w in script.split() if norm(w)]
    got = [norm(w["w"]) for w in heard if norm(w["w"])]
    sm = difflib.SequenceMatcher(a=want, b=got, autojunk=False)
    diffs = [(op, " ".join(want[i1:i2]), " ".join(got[j1:j2])) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal"]
    return {"ratio": round(sm.ratio(), 3), "script_words": len(want), "heard_words": len(got), "diffs": diffs, "text": " ".join(w["w"] for w in heard)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--kodex", choices=["f", "g"], default="g", help="тон Кодекса: f — кремовый, g — светло-серый (выбран владельцем)")
    ap.add_argument("--preview", help="кадры PNG сцены без субтитров: секунды через запятую")
    ap.add_argument("--inspect", type=float, help="положения героев и рук в момент t (JSON)")
    ap.add_argument("--test-push", action="store_true", help="6 с вокруг толчка (14.4–20.4), 540×960, без субтитров — проверить постановку")
    ap.add_argument("--gl", default="gpu")
    ap.add_argument("--name", default="ep4")
    ap.add_argument("--skip-render", action="store_true")
    ap.add_argument("--no-build", action="store_true")
    a = ap.parse_args()
    os.environ.setdefault("MOTION_PACKS_DIR", str(P1.PACKS))
    out_dir = Path(a.out_dir).resolve()
    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    report: dict = {"kodex": a.kodex}
    voice, voice_c, voice_k, lines, spans = assemble_voice(work)
    words = P1.caption_words(lines)
    spec, ev = build_spec(P1.mouth_envelope(voice_c), P1.mouth_envelope(voice_k), spans, a.kodex)
    if a.test_push:
        spec["width"], spec["height"] = 540, 960
        spec["quality"]["shadowSize"] = 1024
    sp = work / ("scene_test.json" if a.test_push else "scene.json")
    sp.write_text(json.dumps(spec, ensure_ascii=False), "utf-8")
    (work / "events.json").write_text(json.dumps({"spans": spans, "events": ev, "words": words}, ensure_ascii=False, indent=1), "utf-8")
    if not a.no_build:
        P1._run(["npm", "run", "build:scene3d"], "сборка движка", cwd=ROOT, shell=sys.platform == "win32")
    render = ["node", str(ROOT / "node" / "scene3d" / "render.mjs"), "--spec", str(sp), "--packs", str(P1.PACKS), "--gl", a.gl, "--workers", "1"]
    if a.inspect is not None:
        r = P1._run([*render, "--inspect", str(a.inspect)], "осмотр")
        print(r.stdout.strip().splitlines()[-1][:4000])
        return 0
    if a.preview:
        P1.wait_ram(4.0)
        prev = work / "preview"
        shutil.rmtree(prev, ignore_errors=True)
        r = P1._run([*render, "--preview", a.preview, "--preview-dir", str(prev)], "превью")
        print(r.stdout.strip().splitlines()[-1][:600])
        return 0
    if a.test_push:
        P1.wait_ram(4.0)
        t0, t1 = 14.4, 20.4
        raw = work / "test_push_raw.mp4"
        r = P1._run([*render, "--out", str(raw), "--crf", "20", "--frames", f"{int(t0 * FPS)}:{int(t1 * FPS)}"], "проба толчка")
        test = out_dir / "test_push.mp4"
        P1._run(["ffmpeg", "-y", "-v", "error", "-i", str(raw), "-ss", f"{t0}", "-t", f"{t1 - t0}", "-i", str(voice), "-map", "0:v", "-map", "1:a",
                 "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-shortest", str(test)], "звук пробы")
        print(json.dumps({"test": str(test), "render": json.loads(r.stdout.strip().splitlines()[-1])}, ensure_ascii=False)[:800])
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
    mix(capped, voice, voice_c, ev, mixed, work, report)
    final, tg = out_dir / f"{a.name}.mp4", out_dir / f"{a.name}_tg.mp4"
    P1.DURATION = DUR
    P1.encode(mixed, final, tg)
    times = [i + 0.5 for i in range(int(DUR))]
    P1.frame_sheet(final, times, [f"{t:.1f}s" for t in times], out_dir / "final_sheet.jpg", cols=8)
    report.update({"video": str(final), "tg": str(tg), "size_mb": round(final.stat().st_size / 2**20, 2), "tg_mb": round(tg.stat().st_size / 2**20, 2),
                   "duration": DUR, "whisper": check_words(final, work)})
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
