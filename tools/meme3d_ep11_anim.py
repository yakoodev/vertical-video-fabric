"""Мем-серия 11 «Дизайн отдел» — хореография (Клод-дизайнер, Кодекс с папкой правок), рот по слогам, камера по
склейкам оригинала и запуск (разметка и декорации — meme3d_ep11.py; субтитры, звук, проверки, выпуск —
meme3d_ep11_mix.py).

    python tools/meme3d_ep11_anim.py D:/vvf-offload/memes/ep11 --shots --small       # по кадру с каждого плана
    python tools/meme3d_ep11_anim.py D:/vvf-offload/memes/ep11 --preview 0.3,3.5      # кадры PNG без субтитров
    python tools/meme3d_ep11_anim.py D:/vvf-offload/memes/ep11 --checks               # папка в руке, текст и лица
    python tools/meme3d_ep11_anim.py D:/vvf-offload/memes/ep11                         # ролик + копия для TG + листы

Кто говорит — как в оригинале: Кодекс (клиент у двери) — «Пришли правочки», смех, «Там тот баннер надо на один размер
увеличить», «Да? Зае*ись, на один размер увеличить», «На один размер увеличь, бл*дь», «Я е*у?», «Я не и*у, на один
увеличить!», «Я на один размер увеличить!», «Я е*у!»; Клод (дизайнер) — «левочки», смех крупно, «В смысле на один
размер? У них размер экранов Full HD,», «Ты чё за х*йню несёшь, вонючую?», «В смысле на один размер, на*уй? 1921 на
1081 или чё, бл*дь?», «А я и*у?!», «На один на*уй как увеличить?!» (дважды), «А я…». Рот — виземы по слогам того, кто
говорит (огибающая звука только в его словах), у второго рот закрыт.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import meme3d_ep7_anim as A7  # noqa: E402
import meme3d_ep9_anim as A9  # noqa: E402
import meme3d_ep11 as E  # noqa: E402
import meme3d_hero_claude as H  # noqa: E402
import meme3d_pilot as P1  # noqa: E402

T = E.T
FPS = 30
DUR = E.DURATION
FY = H.face_height(E.W_HERO)
Keys = A7.Keys
SH = E.SH
MOUTH = A9.MOUTH
STEP_FPS = A9.STEP_FPS
KODEX_MOUTH = {"width": 0.28, "y": 0.165, "color": "#2a2226"}  # выше бирки «Кодекс»

# --------------------------------------------------------------------------- рот и голос


def laugh_words(env: list[float], who: str) -> list[dict]:
    """Смех «ха-ха»: слог на каждом пике огибающей в окне смеха (не чаще 0.11 с)."""
    out = []
    for w, a, b in E.LAUGHS:
        if w != who:
            continue
        i0, i1 = int(a * FPS), int(b * FPS)
        peaks = [i for i in range(max(1, i0), min(len(env) - 1, i1)) if env[i] >= env[i - 1] and env[i] > env[i + 1] and env[i] > 0.35]
        last = a - 0.12
        for t in [i / FPS for i in peaks] + [b + 0.2]:
            while t - last > 0.26 and last + 0.13 <= b:  # тихий смех (или смех под чужую реплику) — «ха» через 0.13 с
                last += 0.13
                out.append({"w": "ха", "t0": round(last - 0.03, 3), "t1": round(last + 0.09, 3)})
            if t - last >= 0.11 and t <= b:
                out.append({"w": "ха", "t0": round(t - 0.03, 3), "t1": round(t + 0.09, 3)})
                last = t
    return out


def speech_words(who: str, env: list[float]) -> list[dict]:
    """Слова для визем (произношение, не подпись) + смех; по времени."""
    ws = [{"w": w["say"], "t0": w["t0"], "t1": w["t1"]} for w in E.words(who)] + laugh_words(env, who)
    return sorted(ws, key=lambda w: w["t0"])


def syllables(ws: list[dict]) -> list[dict]:
    """Атаки слогов: гласные слова (произношения) поровну по его длительности (как в серии 9)."""
    out = []
    for w in ws:
        n = max(1, sum(ch in H.VOWEL_VIS for ch in w["w"].lower()))
        d = max(0.05, w["t1"] - w["t0"])
        out.append({**w, "syl": [round(w["t0"] + d * i / n, 3) for i in range(n)]})
    return out


def speaker_windows(who: str) -> list[tuple[float, float]]:
    """Где звучит этот герой: его слова и смех (с запасом 0.04 с) — огибающая рта только тут."""
    win = [(w["t0"] - 0.04, w["t1"] + 0.04) for w in E.words(who)] + [(a - 0.04, b + 0.04) for w_, a, b in E.LAUGHS if w_ == who]
    return sorted(win)


def masked(env: list[float], windows: list[tuple[float, float]]) -> list[float]:
    out = [0.0] * len(env)
    for t0, t1 in windows:
        for i in range(max(0, int(t0 * FPS)), min(len(env), int(math.ceil(t1 * FPS)) + 1)):
            out[i] = env[i]
    return out


def voices(work: Path) -> tuple[dict, dict]:
    """Огибающая оригинала → по герою (маска его слов) и виземы рта каждого."""
    wav = work / "ref_mono.wav"
    if not wav.is_file():
        P1._run(["ffmpeg", "-y", "-v", "error", "-i", str(E.REF), "-ac", "1", "-ar", str(P1.SR), str(wav)], "звук оригинала")
    env = P1.mouth_envelope(wav)
    envs = {w: masked(env, speaker_windows(w)) for w in ("C", "K")}
    mouths = {w: H.viseme_keys_from_words(syllables(speech_words(w, envs[w]))) for w in ("C", "K")}
    return envs, mouths


# --------------------------------------------------------------------------- Клод (дизайнер)


def bounce(k, t0: float, t1: float, amp: float = 0.05, step: float = 2 / STEP_FPS, lean: float | None = None) -> None:
    """Трясётся от смеха / от злости: корпус сжимается-тянется через шаг (на сетке поз)."""
    t, i = t0, 0
    while t < t1 - 1e-6:
        kw = {"squash": 1.0 - amp if i % 2 == 0 else 1.0 + amp * 0.6, "hop": 0.012 if i % 2 else 0.0}
        if lean is not None:
            kw["lean"] = lean + (3 if i % 2 else -2)
        k.p(round(t, 3), "step", **kw)
        t += step
        i += 1
    k.p(round(t1, 3), "step", squash=1.0, hop=0.0)


def gesture(k, t: float, dur: float = 0.16, **arms) -> None:
    k.p(t, "inOutSine", dur=dur, **arms)


def room_claude(env: list[float], mouth_keys: list[dict]) -> dict:
    k = Keys(turn=0, tilt=0)
    s = SH
    # Начало (вне кадра) — сидит, руки у клавиатуры.
    k.f(0, "smug", [-0.2, -0.1])
    # «левочки» — крупно снизу: ухмылка, глаза вниз на клиента-камеру.
    k.p(s["c_low"][0], "step", tilt=-6, lean=4, armL="rest", armR="rest")
    k.f(s["c_low"][0], "smug", [0.1, -0.5], blend=0)
    # Ржёт крупно: ^^, откинулся, держится за живот, трясётся.
    k.p(s["c_laugh"][0], "step", tilt=4, lean=-10, armL="clasp", armR="clasp")
    k.f(s["c_laugh"][0], "happy", [0, 0.2], blend=0)
    bounce(k, s["c_laugh"][0], s["c_laugh"][1] - 0.1, amp=0.06, lean=-10)
    # «В смысле на один размер?» — рука у подбородка, скепсис; «у них размер экранов» — объясняет; «Full HD» — ладонь.
    c0 = s["c_desk"][0]
    k.p(c0, "step", tilt=-5, lean=2, turn=-8, squash=1.0, hop=0.0, armL="rest", armR=[48, 78, 0.5, 0])
    k.f(c0, "meh", [-0.35, 0.05], blend=0)
    gesture(k, 5.52, armR=[40, 70, 0.45, 0], tilt=-9)
    k.f(5.55, "side", [-0.6, 0.05])
    gesture(k, 6.02, armR=[30, 40, 0.45, 0], tilt=0)
    gesture(k, 6.45, armR=[46, 30, 0.5, 0], armL=[18, 30, 0.3, 0])
    k.f(6.45, "smug", [-0.4, 0.0])
    gesture(k, 6.92, armR=[60, 20, 0.55, 0.5], tilt=4)
    gesture(k, 7.3, armR=[30, 30, 0.45, 0], armL="rest")
    # «Ты чё за х*йню несёшь, вонючую?» — ехидно, прищур, голова набок, ладонь вверх.
    c2 = s["c_desk2"][0]
    k.p(c2, "step", tilt=7, lean=-2, turn=-4, armL="rest", armR=[22, 40, 0.35, 0])
    k.f(c2, "fake", [-0.3, 0.05], blend=0)
    gesture(k, 9.9, armR=[40, 35, 0.45, 0])
    k.f(10.3, "smug", [-0.35, 0.0])
    # «В смысле на один размер, на*уй?» — машет обеими руками.
    c3 = s["c_wide"][0]
    k.p(c3, "step", tilt=0, lean=4, turn=-6, armL=[40, 45, 0.45, 0], armR=[22, 30, 0.35, 0])
    k.f(c3, "annoyed", [-0.3, 0.05], blend=0)
    for i, t in enumerate((12.66, 13.10, 13.70)):
        a, b = ([24, 32, 0.35, 0], [46, 48, 0.5, 0]) if i % 2 == 0 else ([46, 48, 0.5, 0], [24, 32, 0.35, 0])
        gesture(k, t, armL=a, armR=b)
    k.f(13.7, "surprised", [-0.3, 0.0])
    # «1921 на 1081 или чё, бл*дь?» — другой ракурс: ручкой «рисует» числа, на «или чё» разводит руками.
    c4 = s["c_angle"][0]
    k.p(c4, "step", tilt=-4, lean=6, turn=-10, armL=[30, 12, 0.4, 0], armR=[55, 40, 0.55, 0.4])
    k.f(c4, "smug", [-0.5, 0.05], blend=0)
    gesture(k, 15.45, armL=[50, 15, 0.5, 0.3], armR=[25, 30, 0.35, 0], tilt=4)
    gesture(k, 15.92, armL=[58, 18, 0.55, 0.4], armR=[40, 35, 0.45, 0])
    k.f(15.92, "fake", [-0.5, 0.05])
    gesture(k, 17.14, armL="shrug", armR="shrug", tilt=-6, lean=0)
    k.f(17.14, "roll", [-0.2, 0.4])
    k.f(18.1, "annoyed", [-0.4, 0.0])
    # «А я и*у?!» — подался вперёд, обе руки вверх, глаза круглые.
    c5 = s["c_shout"][0]
    k.p(c5, "step", lean=12, tilt=0, turn=-6, armL="up", armR="up")
    k.f(c5, "shocked", [-0.2, 0.0], blend=0)
    # «На один на*уй как увеличить?!» — вплотную, бешенство, трясётся.
    c6 = s["c_ecu"][0]
    k.p(c6, "step", lean=10, tilt=3, turn=-2, armL="fist", armR="fist")
    k.f(c6, "rage", [0, 0], blend=0)
    bounce(k, c6, s["c_ecu"][1] - 0.05, amp=0.03, lean=10)
    # То же за столом — машет руками.
    c7 = s["c_desk3"][0]
    k.p(c7, "step", lean=8, tilt=0, turn=-6, armL="flail", armR="flail")
    k.f(c7, "angry", [-0.3, 0], blend=0)
    # «А я…» — вплотную снизу: безумная улыбка.
    c8 = s["c_ecu2"][0]
    k.p(c8, "step", lean=10, tilt=-5, armL="up", armR="up", squash=1.04)
    k.f(c8, "happy", [0, -0.3], blend=0)
    pose, face = k.sorted()
    ch = H.hero(pose, face, env, None, position=E.CL, yaw=E.CLAUDE_YAW, width=E.W_HERO, label="Claude", char_id="claude", seed=7, glow=0.05,
                headset=True, mouth=True, mouth_keys=mouth_keys)
    ch["critter"]["mouth"] = dict(MOUTH)
    ch["autoBlink"] = True
    ch["plantFeet"] = True
    return ch


# --------------------------------------------------------------------------- Кодекс (клиент с папкой)

# Папка в правой ручке: [наружу, вверх, вперёд (м), поворот°, наклон к зрителю°] от кончика ручки (kodex2 ``fold``).
FOLD_SIDE = [0.045, 0.006, 0.02, 0, 0]  # у бока (низ папки над полом)
FOLD_CHEST = [0.055, 0.03, 0.04, 0, 6]  # показывает: приподнята у бока лицом к зрителю (перед корпусом закрыла бы морду и рот)
FOLD_THRUST = [0.06, 0.03, 0.15, 25, 18]  # тычет в камеру, морда видна


def kodex(env: list[float], mouth_keys: list[dict]) -> dict:
    pose: list[dict] = []
    face: list[dict] = []
    cur = {"armL": "rest", "armR": "down", "holdL": "none", "holdR": "folder", "lean": 0, "tilt": 0, "turn": 0, "squash": 1.0, "hop": 0,
           "fold": FOLD_SIDE}
    pose.append({"t": 0, "ease": "step", **cur})

    def go(t, dur=0.16, ease="inOutSine", **kw):
        pose.append({"t": round(t - dur, 3), "ease": "step", **{f: cur[f] for f in kw}})
        pose.append({"t": round(t, 3), "ease": ease, **kw})
        cur.update(kw)

    def cut(t, **kw):
        pose.append({"t": round(t, 3), "ease": "step", **kw})
        cur.update(kw)

    def f(t, expr, lk=None):
        face.append({"t": round(t, 3), "expr": expr, **({"look": lk} if lk is not None else {})})

    s = SH
    # «Пришли правочки,» — довольный, папка у бока.
    f(0, "smug", [0.3, 0.0])
    # Ржёт: запрокинул голову, трясётся.
    k0 = s["k_laugh"][0]
    cut(k0, lean=-16, tilt=-4, armL=[30, 30, 0.4, 0])
    f(k0, "happy", [0.2, 0.5])
    t, i = k0, 0
    while t < s["k_laugh"][1] - 0.05:
        cut(t, squash=0.95 if i % 2 == 0 else 1.03, hop=0.01 if i % 2 else 0.0)
        t += 2 / STEP_FPS
        i += 1
    # «Там тот баннер надо на один размер увеличить» — папка перед грудью, левой ручкой объясняет.
    k1 = s["k_folder"][0]
    cut(k1, lean=2, tilt=0, squash=1.0, hop=0.0, armR=[30, 62, 0.45, 0], fold=FOLD_CHEST, armL=[30, 40, 0.4, 0])
    f(k1, "neutral", [0.3, 0.0])
    go(3.64, armL=[45, 30, 0.5, 0])
    go(4.20, armL=[25, 45, 0.4, 0], tilt=-4)
    f(4.2, "smug", [0.3, 0.0])
    # «да? Зае*ись,» — разводит свободной ручкой, папка снова у бока.
    k2 = s["k_door2"][0]
    cut(k2, armR="down", fold=FOLD_SIDE, armL=[55, 15, 0.45, 0], tilt=5, lean=0)
    f(k2, "surprised", [0.3, 0.05])
    f(7.74, "happy", [0.3, 0.0])
    # «на один размер увеличить» — ближе, кивает, ручкой показывает «чуть-чуть».
    k3 = s["k_mid"][0]
    cut(k3, armL=[20, 58, 0.5, 0], tilt=-3, lean=4)
    f(k3, "smug", [0.3, 0.0])
    go(8.88, armL=[28, 50, 0.55, 0], tilt=3)
    # «На один размер увеличь, бл*дь» — тычет папкой в камеру, злится.
    k4 = s["k_thrust"][0]
    cut(k4, armR=[22, 82, 0.8, 0], fold=FOLD_THRUST, lean=10, tilt=0, armL=[40, 25, 0.5, 0])
    f(k4, "annoyed", [0.1, 0.0])
    go(11.40, armR=[30, 80, 0.85, 0], lean=14)
    f(11.98, "angry", [0.1, 0.0])
    # «Я е*у?» — пожимает плечами (папка в ручке).
    k5 = s["k_shrug"][0]
    cut(k5, armR="shrug", armL="shrug", fold=FOLD_SIDE, lean=0, tilt=-6)
    f(k5, "annoyed", [0.35, 0.0])
    # «Я не и*у, на один увеличить!» — злой, кулачки.
    k6 = s["k_angry"][0]
    cut(k6, armR=[24, 30, 0.3, 0.6], armL=[24, 30, 0.3, 0.6], fold=FOLD_SIDE, lean=8, tilt=0)
    f(k6, "angry", [0.3, 0.0])
    go(20.70, lean=12, tilt=4)
    # «Я на один размер увеличить!» — вплотную, трясётся.
    k7 = s["k_ecu"][0]
    cut(k7, lean=10, tilt=-3)
    f(k7, "rage", [0, 0])
    t, i = k7, 0
    while t < s["k_ecu"][1] - 0.05:
        cut(t, squash=0.97 if i % 2 == 0 else 1.02)
        t += 2 / STEP_FPS
        i += 1
    # «Я е*у!» — вплотную, отчаянно.
    k8 = s["k_ecu2"][0]
    cut(k8, squash=1.0, lean=8, tilt=5, armL="shrug", armR="shrug")
    f(k8, "shocked", [0.1, 0.0])
    pose.sort(key=lambda x: x["t"])
    face.sort(key=lambda x: x["t"])
    return {"id": "kodex", "style": "kodex2", "variant": "g", "width": E.W_HERO, "position": E.KODEX, "yaw": E.KODEX_YAW, "pose": pose,
            "face": face, "stepFps": STEP_FPS, "seed": 11, "voice": {"fps": FPS, "start": 0, "values": env, "gain": 1.0},
            "mouth": dict(KODEX_MOUTH), "mouthKeys": mouth_keys, "folder": dict(E.FOLDER), "plantFeet": True}


# --------------------------------------------------------------------------- камера


def camera(seed: int = 5) -> dict:
    """Планы по склейкам оригинала: у двери — общий/средний/вплотную, у стола — снизу, средний, общий, сбоку, вплотную.
    Вплотную и смех — «с рук»: камера дрожит (ключи каждые 0.1 с), как телефон в оригинале."""
    cx, cy, cz = E.CL
    fy = cy + FY
    kx, ky, kz = E.KODEX
    fk = ky + FY
    rnd = random.Random(seed)
    keys: list[dict] = []
    used: list[str] = []

    def shot(name, A, B=None, fov=34.0, dof=0.4, roll=0.0, hand=0.0):
        used.append(name)
        t0, t1 = SH[name]
        B = B or A
        n = max(1, int((t1 - t0) / 0.1)) if hand else 1
        for i in range(n + 1):
            k = i / n
            t = t0 + (t1 - 0.001 - t0) * k
            p = [A[0][j] + (B[0][j] - A[0][j]) * k for j in range(3)]
            g = [A[1][j] + (B[1][j] - A[1][j]) * k for j in range(3)]
            r = roll
            if hand and 0 < i:
                p = [p[0] + rnd.uniform(-hand, hand), p[1] + rnd.uniform(-hand, hand) * 0.7, p[2] + rnd.uniform(-hand, hand) * 0.5]
                r = roll + rnd.uniform(-3, 3)
            key = {"t": round(t, 3), "position": [round(v, 4) for v in p], "target": [round(v, 4) for v in g], "fov": fov, "dof": dof,
                   "roll": round(r, 2), "ease": "inOutSine"}
            if i == 0 and keys:
                key["cut"] = True
            keys.append(key)

    def push(A, k=0.06):
        p, t = A
        return ([p[i] + (t[i] - p[i]) * k for i in range(3)], t)

    # Кодекс у двери.
    K_W = ([kx + 0.03, fk + 0.09, kz + 1.32], [kx + 0.035, fk + 0.02, kz])
    K_M = ([kx - 0.04, fk + 0.05, kz + 1.15], [kx - 0.04, fk + 0.01, kz])
    K_CL = ([kx + 0.01, fk + 0.025, kz + 0.8], [kx + 0.015, fk + 0.0, kz])
    K_T = ([kx - 0.02, fk + 0.04, kz + 0.95], [kx - 0.03, fk + 0.0, kz])
    K_ECU = ([kx - 0.0, fk + 0.0, kz + 0.6], [kx - 0.01, fk - 0.0, kz])
    # Клод за столом.
    C_LOW = ([cx - 0.02, fy - 0.08, cz + 0.74], [cx - 0.03, fy + 0.01, cz])
    C_LAUGH = ([cx - 0.02, fy + 0.03, cz + 0.68], [cx - 0.03, fy + 0.01, cz])
    C_DESK = ([cx + 0.15, fy + 0.06, cz + 0.9], [cx - 0.03, fy - 0.03, cz])
    C_WIDE = ([cx + 0.12, fy + 0.1, cz + 1.15], [cx - 0.05, fy - 0.04, cz])
    C_ANGLE = ([cx + 0.16, fy + 0.035, cz + 0.86], [cx - 0.05, fy - 0.005, cz])
    C_ECU = ([cx - 0.02, fy + 0.01, cz + 0.58], [cx - 0.03, fy + 0.0, cz])
    C_ECU2 = ([cx - 0.01, fy - 0.05, cz + 0.6], [cx - 0.03, fy + 0.01, cz])

    shot("k_door", K_W, push(K_W, 0.04), dof=0.25)
    shot("c_low", C_LOW, push(C_LOW, 0.06), fov=38, dof=0.45, roll=3)
    shot("k_laugh", K_W, push(K_W, 0.03), dof=0.25)
    shot("c_laugh", C_LAUGH, push(C_LAUGH, 0.1), fov=38, dof=0.5, roll=-4, hand=0.008)
    shot("k_folder", K_M, push(K_M, 0.05), dof=0.3)
    shot("c_desk", C_DESK, push(C_DESK, 0.06), dof=0.4)
    shot("k_door2", K_W, push(K_W, 0.04), dof=0.25)
    shot("k_mid", K_CL, push(K_CL, 0.05), dof=0.35)
    shot("c_desk2", C_DESK, push(C_DESK, 0.08), dof=0.4)
    shot("k_thrust", K_T, push(K_T, 0.08), dof=0.35)
    shot("c_wide", C_WIDE, push(C_WIDE, 0.05), dof=0.35)
    shot("c_angle", C_ANGLE, push(C_ANGLE, 0.1), dof=0.45, roll=-2.5)
    shot("k_shrug", K_W, push(K_W, 0.04), dof=0.25)
    shot("c_shout", C_DESK, push(C_DESK, 0.1), dof=0.4)
    shot("k_angry", K_W, push(K_W, 0.06), dof=0.25)
    shot("c_ecu", C_ECU, push(C_ECU, 0.08), fov=38, dof=0.5, roll=4, hand=0.01)
    shot("k_ecu", K_ECU, push(K_ECU, 0.08), fov=38, dof=0.5, roll=-3, hand=0.01)
    shot("c_desk3", C_DESK, push(C_DESK, 0.1), dof=0.4)
    shot("k_ecu2", K_ECU, push(K_ECU, 0.1), fov=38, dof=0.5, roll=3, hand=0.008)
    shot("c_ecu2", C_ECU2, push(C_ECU2, 0.1), fov=40, dof=0.5, roll=-5, hand=0.01)
    if used != [n for _a, _b, n in E.SHOTS]:
        raise SystemExit(f"камера не совпадает с планами: {sorted(set(SH) ^ set(used))}")
    keys[-1]["t"] = DUR
    return {"fov": 34, "keys": keys, "clamp": False}


def build_spec(envs: dict, mouths: dict) -> dict:
    return {"width": 1080, "height": 1920, "fps": FPS, "duration": DUR, "environment": E.environment(), "lights": E.lights(),
            "objects": E.room_objects() + E.door_objects(), "camera": camera(),
            "characters": [room_claude(envs["C"], mouths["C"]), kodex(envs["K"], mouths["K"])],
            "quality": {"shadows": True, "shadowSize": 2048, "antialias": True}}


# --------------------------------------------------------------------------- запуск


def main() -> int:
    import meme3d_ep11_mix as M

    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--preview", help="кадры PNG сцены без субтитров: секунды через запятую")
    ap.add_argument("--shots", action="store_true", help="превью: по кадру из середины каждого плана")
    ap.add_argument("--inspect", help="положения героев и вещей в моменты t через запятую (JSON в work/inspect.json)")
    ap.add_argument("--checks", action="store_true", help="папка в руке, рывки рук (work/checks.json)")
    ap.add_argument("--small", action="store_true", help="превью 540×960")
    ap.add_argument("--gl", default="gpu")
    ap.add_argument("--name", default="ep11")
    ap.add_argument("--skip-render", action="store_true")
    ap.add_argument("--no-build", action="store_true")
    a = ap.parse_args()
    os.environ.setdefault("MOTION_PACKS_DIR", str(P1.PACKS))
    out_dir = Path(a.out_dir).resolve()
    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    envs, mouths = voices(work)
    spec = build_spec(envs, mouths)
    if a.small:
        spec["width"], spec["height"] = 540, 960
        spec["quality"]["shadowSize"] = 1024
    sp = work / "scene.json"
    sp.write_text(json.dumps(spec, ensure_ascii=False), "utf-8")
    (work / "events.json").write_text(json.dumps({"shots": E.SHOTS, "words": E.words(), "mouth_C": len(mouths["C"]), "mouth_K": len(mouths["K"])},
                                                 ensure_ascii=False, indent=1), "utf-8")
    if not a.no_build:
        P1._run(["npm", "run", "build:scene3d"], "сборка движка", cwd=ROOT, shell=sys.platform == "win32")
    render = ["node", str(ROOT / "node" / "scene3d" / "render.mjs"), "--spec", str(sp), "--packs", str(P1.PACKS), "--gl", a.gl, "--workers", "1"]
    if a.inspect:
        r = P1._run([*render, "--inspect-list", a.inspect], "осмотр")
        (work / "inspect.json").write_text(r.stdout.strip().splitlines()[-1], "utf-8")
        print(work / "inspect.json")
        return 0
    if a.checks:
        P1.wait_ram(3.0)
        res = M.scene_checks(render, work)
        print(json.dumps(res, ensure_ascii=False, indent=1)[:4000])
        return 0
    if a.preview or a.shots:
        times = a.preview or ",".join(f"{(s0 + s1) / 2:.2f}" for s0, s1, _ in E.SHOTS)
        P1.wait_ram(3.0)
        prev = work / "preview"
        shutil.rmtree(prev, ignore_errors=True)
        r = P1._run([*render, "--preview", times, "--preview-dir", str(prev)], "превью")
        print(r.stdout.strip().splitlines()[-1][:600])
        return 0
    return M.release(render, work, out_dir, a.name, skip_render=a.skip_render)


if __name__ == "__main__":
    sys.exit(main())
