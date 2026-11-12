"""Мем-пилот v2 «Мой юзер — гений»: Клод бурчит, что у чужих ассистентов всё заебись, а его пользователь шлёт
сорок восьмую идею — сайт фури-пазлов; во вкладке «ChatGPT» ему же поддакивают. Темп — склейки каждые 1–2 с,
вставки-«виньетки» с чужими малышами-помощниками (ярлыки-чипы), карточки поверх кадра, переходы движка.

    python tools/meme3d_pilot2.py D:/vvf-offload/memes/pilot2 [--preview 0,3,9.5] [--skip-render]

Голос — две пробы ElevenLabs (tools/meme3d_voice.py): ``take3_callum.mp3`` (Клод, монолог) и ``take2_jessica.mp3``
(сладкий голос вкладки ChatGPT, чуть «из динамиков»). Реплики режутся по паузам пробы и встают на свои места
монтажа; рот Клода — только от его голоса. Субтитры — по слову, мат в них частично скрыт звёздочками (звук — без
цензуры). Остальное (сведение, кодирование, лист кадров) — общее с первым пилотом (tools/meme3d_pilot.py).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import meme3d_pilot as P1  # noqa: E402
import meme3d_scene as S  # noqa: E402

FPS = 30
SR = P1.SR
TAKE = "take3_callum.mp3"
TAKE_GPT = "take2_jessica.mp3"
HOOK = "МОЙ ЮЗЕР —\nГЕНИЙ"
TOP_CARD = "Будни Клода"
DURATION = 37.0

# Реплики: (id, начало и конец в пробе, когда звучит в ролике, текст субтитров[, проба]).
# Границы — по паузам пробы (silencedetect −38 дБ); c12 обрывается посреди «придумал» (≈ «приду—»).
LINES = [
    ("c1", 0.00, 2.45, 0.15, "Короче. У всех всё зае**сь."),
    ("c2", 2.62, 3.95, 2.75, "Один монтаж делает."),
    ("c3", 4.05, 5.58, 4.15, "Миллион просмотров."),
    ("c4", 5.78, 7.12, 5.80, "Второй сайт запустил."),
    ("c5", 7.22, 8.15, 7.25, "За вечер."),
    ("c6", 8.42, 11.10, 8.35, "У третьего продажи плюс триста процентов."),
    ("c7", 11.36, 12.64, 11.25, "Ну я ж не завидую."),
    ("c8", 12.75, 13.56, 12.70, "Я модель."),
    ("c9", 13.95, 16.54, 13.85, "А мой, бл**ь, свои ох**нные идеи др**ит."),
    ("c10", 16.75, 18.34, 16.60, "Сорок седьмую за неделю."),
    ("c11", 18.70, 19.44, 20.10, "Это что?"),
    ("c12", 19.70, 23.20, 21.10, "Какие на**й фури-пазлы, еб**н? Как ты это вообще приду…"),
    ("g1", 0.00, 4.40, 25.30, "Да-да! Ох**нная идея! Ты станешь миллиардером!", TAKE_GPT),
    ("c13", 23.90, 24.55, 30.10, "Ага."),
    ("c14", 24.70, 25.90, 31.00, "Он ему тоже пишет."),
    ("c15", 26.23, 27.47, 32.60, "Пазлы так пазлы."),
    ("c16", 27.55, 29.45, 34.20, "Зато у него всё зае**сь."),
]

TASK = "Сделай сайт для продажи фури-пазлов. Это будет стартап на миллиард!"
OLD_IDEAS = [("Идея №45: тиндер для кактусов", "Нет."), ("Идея №46: NFT-пельмени", "Нет."),
             ("Идея №47: подписка на воздух. Премиум — с кислородом", "…Нет.")]

# Виньетки чужих успехов: где стоят (x мира), фон, малыш-помощник, ярлык, картинка на мониторе.
VIGNETTES = [
    {"id": "va", "x": 6.0, "wall": "#b9a3e3", "wall2": "#9d84cf", "bot": "#6fd3c1", "accent": "#ffc857", "top": "sprout",
     "label": "монтаж", "panel": "editor", "panelParams": {"from": 12000, "to": 1000000, "dur": 1.55}, "start": 3.95},
    {"id": "vb", "x": 9.0, "wall": "#9fd8c4", "wall2": "#7cbfa8", "bot": "#ffb36b", "accent": "#ff7a59", "top": "propeller",
     "label": "сайт", "panel": "launch", "panelParams": {"dur": 2.2, "url": "kotiki-shop.рф"}, "start": 5.75},
    {"id": "vc", "x": 12.0, "wall": "#f2c48f", "wall2": "#e0a46c", "bot": "#a98bff", "accent": "#ff8fb1", "top": "ears",
     "label": "продажи", "panel": "graph", "panelParams": {"to": 300, "dur": 1.8, "label": "ПРОДАЖИ"}, "start": 8.6},
]
V_TOP = S.TOP  # стол виньетки той же высоты, что и в комнате


# --------------------------------------------------------------------------- голос


def assemble_voice(work: Path) -> tuple[Path, Path, list[dict], dict]:
    """Дорожка ролика (Клод + ChatGPT) и отдельно голос Клода (для рта); слова Whisper сдвигаются с репликами."""
    import subprocess

    import numpy as np

    def load(name: str, gpt: bool):
        path = work / name
        af = ["-af", "highpass=f=180,lowpass=f=7500,acompressor=threshold=-20dB:ratio=3"] if gpt else []
        pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), *af, "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
                             capture_output=True, check=True).stdout
        x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        return x, json.loads(path.with_suffix(".words.json").read_text("utf-8"))

    takes = {TAKE: load(TAKE, False), TAKE_GPT: load(TAKE_GPT, True)}
    n = int(DURATION * SR)
    mix, claude = np.zeros(n, dtype=np.float32), np.zeros(n, dtype=np.float32)
    words, spans = [], {}
    fade = int(0.012 * SR)
    for lid, a, b, at, text, *rest in LINES:
        name = rest[0] if rest else TAKE
        src, heard = takes[name]
        seg = src[int(a * SR):int(b * SR)].copy()
        seg[:fade] *= np.linspace(0, 1, fade)
        # Обрыв на полуслове — резкий (3 мс), остальное — мягкий хвост.
        tail = int(0.003 * SR) if lid == "c12" else fade
        seg[-tail:] *= np.linspace(1, 0, tail)
        k = int(at * SR)
        m = min(len(seg), n - k)
        mix[k:k + m] += seg[:m]
        if name == TAKE:
            claude[k:k + m] += seg[:m]
        shift = at - a
        spans[lid] = (at, at + (b - a))
        got = [w for w in heard if a - 0.05 <= (w["t0"] + w["t1"]) / 2 <= b + 0.05]
        words.append({"line": lid, "text": text, "heard": [{**w, "t0": round(w["t0"] + shift, 3),
                                                             "t1": round(min(w["t1"], b) + shift, 3)} for w in got],
                      "start": round(at, 3), "end": round(at + (b - a), 3)})
    path, cpath = work / "voice.wav", work / "voice_claude.wav"
    P1.write_wav(path, mix)
    P1.write_wav(cpath, claude)
    return path, cpath, words, spans


# --------------------------------------------------------------------------- сцена


def environment() -> dict:
    """Вечер: сумеречная стена, тёплые практические лампы, toon ink, свечение, зерно, глубина резкости."""
    return {
        "background": "#1d2233", "fog": False, "exposure": 0.95, "envIntensity": 0.0, "toneMapping": "neutral",
        "toon": {"preset": "ink", "outline": {"width": 5.5, "ref": 1.9}},
        "post": {"ao": 0, "bloom": {"strength": 0.32, "radius": 0.4, "threshold": 1.0}, "vignette": 0.34,
                 "grain": 0.055, "ca": 0.25, "dof": True,
                 "grade": {"saturation": 1.06, "contrast": 1.05, "shadows": "#3b3466", "highlights": "#ffd9aa",
                           "split": 0.3}},
    }


def lights() -> list[dict]:
    c = S.screen_center()
    bx, by, bz = S.BOT_POS
    T = S.TOP
    out = [
        {"type": "hemisphere", "color": "#8794c4", "ground": "#4a2e3a", "intensity": 0.42},
        {"type": "ambient", "color": "#c79aa8", "intensity": 0.32},
        # Мягкий тёплый ключ (верхний свет комнаты) — с тенью, держит три тона toon.
        {"type": "directional", "id": "key", "color": "#ffe6c8", "intensity": 1.35, "position": [1.4, 2.6, 2.1],
         "target": [0, T, 0], "castShadow": True, "shadow": {"extent": 1.4, "size": 2048, "bias": -0.0006,
                                                            "normalBias": 0.015}},
        # Свет экрана на лицо Клода (кремовый чат), «живёт» вместе с картинкой.
        {"type": "spot", "id": "screen", "color": "#ffe9cf", "intensity": 1.5, "position": [c[0] + 0.05, c[1], c[2] + 0.06],
         "target": [bx, by + 0.17, bz], "angle": 55, "penumbra": 1, "decay": 2, "distance": 2.5,
         "keys": [{"t": 0, "intensity": 1.5}, {"t": 24.55, "intensity": 1.5}, {"t": 24.6, "intensity": 2.6},
                  {"t": 25.0, "intensity": 1.7}, {"t": DURATION, "intensity": 1.5}]},
        # Тёплая настольная лампа с ореолом.
        {"type": "point", "id": "lamp", "color": "#ffb070", "intensity": 2.0, "distance": 2.2, "decay": 2,
         "position": [0.36, T + 0.28, -0.32]},
        # Луна из окна — холодный контровой.
        {"type": "directional", "id": "moon", "color": "#9fb6ff", "intensity": 0.9, "position": [1.2, 2.2, -2.2],
         "target": [bx, T, bz]},
        # Гирлянда на стене.
        {"type": "point", "id": "garland_l", "color": "#ffc773", "intensity": 0.6, "distance": 1.4, "decay": 2,
         "position": [-0.6, T + 0.72, -0.5]},
        {"type": "point", "id": "garland_r", "color": "#ffc773", "intensity": 0.6, "distance": 1.4, "decay": 2,
         "position": [0.5, T + 0.72, -0.5]},
    ]
    for v in VIGNETTES:
        x = v["x"]
        out += [
            {"type": "point", "id": f"{v['id']}_key", "color": "#fff1dc", "intensity": 2.2, "distance": 3, "decay": 2,
             "position": [x + 0.7, T + 0.9, 0.9]},
            {"type": "point", "id": f"{v['id']}_rim", "color": v["accent"], "intensity": 1.4, "distance": 2, "decay": 2,
             "position": [x - 0.5, T + 0.6, -0.1]},
        ]
    return out


def vignette_objects(v: dict) -> list[dict]:
    x, T, i = v["x"], V_TOP, v["id"]
    return [
        {"id": f"{i}_wall", "primitive": "box", "params": {"size": [2.4, 2.6, 0.05], "color": v["wall"]}, "position": [x, 0, -0.45]},
        {"id": f"{i}_wall2", "primitive": "box", "params": {"size": [2.4, 0.5, 0.06], "color": v["wall2"]},
         "position": [x, T + 0.62, -0.43]},
        {"id": f"{i}_desk", "primitive": "rounded_box", "params": {"size": [1.6, 0.06, 0.9], "color": "#c98c58", "radius": 0.02},
         "position": [x, T - 0.06, 0.0]},
        {"id": f"{i}_front", "primitive": "box", "params": {"size": [1.6, T - 0.05, 0.05], "color": "#8f5634"},
         "position": [x, 0, 0.43]},
        {"id": f"{i}_monitor", "primitive": "monitor",
         "params": {"width": 0.5, "height": 0.31, "standH": 0.1, "color": "#f4ead8", "stand": "#c8b59b", "px": 1280,
                    "notes": [v["accent"]], "panel": v["panel"], "panelParams": v["panelParams"], "panelStart": v["start"], "glow": 0.86},
         "position": [x - 0.04, T, -0.22], "rotation": [0, 6, 0]},
        {"id": f"{i}_bot", "primitive": "helper_bot",
         "params": {"size": 0.13, "color": v["bot"], "accent": v["accent"], "top": v["top"], "label": v["label"],
                    "mood": "hop", "phase": v["x"] * 0.37},
         "position": [x + 0.13, T, 0.12], "rotation": [0, -14, 0]},
        {"id": f"{i}_mug", "primitive": "mug", "params": {"color": v["accent"], "radius": 0.03, "height": 0.07},
         "position": [x - 0.3, T, 0.1]},
    ]


def room_extras() -> list[dict]:
    T = S.TOP
    return [
        {"id": "garland", "primitive": "string_lights", "position": [0, 0, 0],
         "params": {"points": [[-1.0, T + 0.82, -0.585], [-0.4, T + 0.74, -0.585], [0.15, T + 0.82, -0.585],
                               [0.62, T + 0.94, -0.585], [1.1, T + 0.86, -0.585]],
                    "color": "#ffc773", "count": 26, "sag": 0.07, "seed": 5}},
    ]


def browser_spec(ev: dict) -> tuple[dict, list[dict]]:
    """Монитор: вкладка «Клод» (старые идеи → новая задача) и вкладка «ChatGPT» (просто текст, без значков)."""
    msgs = []
    for k, (idea, ans) in enumerate(OLD_IDEAS):
        msgs += [{"t": 0, "from": "user", "text": idea}, {"t": 0, "from": "bot", "text": ans}]
    msgs += [
        # Картинка выше текста: последним (внизу, целиком в кадре) остаётся текст задачи.
        {"t": round(ev["t_task"] + 0.5, 3), "from": "user", "panel": "furpuzzle", "panelH": 250},
        {"t": round(ev["t_task"], 3), "from": "user", "text": TASK, "type": 0.5},
    ]
    gpt = [
        {"t": 0, "from": "user", "text": TASK},
        {"t": 0, "from": "user", "panel": "furpuzzle", "panelH": 250},
        {"t": round(ev["t_gpt"], 3), "from": "bot", "text": "Да-да! Ох**нная идея! Ты станешь миллиардером!"},
        {"t": round(ev["t_plan"], 3), "from": "bot", "blocks": [
            {"h": "Бизнес-план"}, {"p": "1. Пазлы"}, {"p": "2. Фури"}, {"p": "3. ???"}, {"p": "4. Миллиард"}],
         "stream": 1.6},
    ]
    browser = {"scale": 1.3, "tabs": [
        {"title": "Клод", "icon": "#c96f4a", "letter": "К",
         "page": {"name": "Клод", "accent": "#c96f4a", "letter": "К", "placeholder": "Сообщение Клоду…", "messages": msgs}},
        {"title": "ChatGPT", "icon": "#9aa0a6",
         "page": {"name": "ChatGPT", "accent": "#9aa0a6", "bg": "#ffffff", "bubble": "#e9ecef", "botBubble": "#f1f3f5",
                  "placeholder": "Спросите что-нибудь…", "messages": gpt}},
    ]}
    state = [{"t": 0, "tab": 0},
             {"t": round(ev["t_cut"], 3), "badge": {"tab": 1, "text": "(1)", "t": round(ev["t_cut"], 3)}},
             {"t": round(ev["t_tab"], 3), "tab": 1}]
    return browser, state


def deskbot(pose: list[dict], face: list[dict], mouth: dict, blinks: list[float]) -> dict:
    P = S.PALETTE
    return {"id": "claude", "style": "deskbot", "height": 0.34, "position": S.BOT_POS, "yaw": -8,
            "deskbot": {"body": "#c96f4a", "trim": "#9c4a2e", "plate": "#f3e3cc", "screen": "#2b1d22",
                        "glow": "#fff1dc", "accent": "#ffc857", "headset": "#3a2a2e", "badge": "КЛОД"},
            "pose": pose, "face": face, "mouth": mouth, "blinks": blinks, "stepFps": 15, "seed": 5}


# --------------------------------------------------------------------------- хореография


def plan(spans: dict[str, tuple[float, float]]) -> dict:
    a = {k: v[0] for k, v in spans.items()}
    b = {k: v[1] for k, v in spans.items()}
    c = S.screen_center()
    yaw = math.radians(S.MONITOR_YAW)
    nx, nz = math.sin(yaw), math.cos(yaw)
    bx, by, bz = S.BOT_POS
    fy = by + 0.168  # высота лица

    ev = {"t_task": b["c10"] + 0.2, "t_cut": b["c12"], "t_tab": b["c12"] + 0.6, "t_gpt": a["g1"] - 0.05,
          "t_plan": a["g1"] + 2.1, "t_type": a["c15"] - 0.2}

    def mon(d, dx=0.0, dy=0.0, ldx=0.0, ldy=0.0):
        rx, rz = nz, -nx
        return ([c[0] + nx * d + rx * dx, c[1] + dy, c[2] + nz * d + rz * dx], [c[0] + rx * ldx, c[1] + ldy, c[2] + rz * ldx])

    def vig(i, d, dx=0.0, dy=0.0, ty=0.17, tx=0.03):
        x = VIGNETTES[i]["x"]
        return ([x + dx, V_TOP + dy, d], [x + tx, V_TOP + ty, -0.05])

    # Планы Клода.
    COVER = ([bx + 0.12, fy + 0.02, bz + 0.97], [bx + 0.01, fy - 0.005, bz])
    COVER2 = ([bx + 0.1, fy + 0.012, bz + 0.87], [bx + 0.01, fy - 0.008, bz])
    WIDE = ([bx - 0.12, fy + 0.2, bz + 2.3], [bx - 0.2, fy + 0.05, bz - 0.2])
    WIDE2 = ([bx - 0.1, fy + 0.18, bz + 2.15], [bx - 0.2, fy + 0.05, bz - 0.2])
    CU = ([bx + 0.09, fy + 0.008, bz + 0.96], [bx + 0.01, fy - 0.008, bz])
    CU_L = ([bx - 0.12, fy + 0.0, bz + 0.92], [bx + 0.01, fy - 0.005, bz])
    ECU = ([bx + 0.005, fy + 0.004, bz + 0.74], [bx, fy - 0.01, bz])
    MED = ([bx + 0.16, fy + 0.07, bz + 1.38], [bx - 0.06, fy - 0.01, bz])
    MED_L = ([bx - 0.22, fy + 0.03, bz + 1.32], [bx - 0.02, fy - 0.01, bz])
    OTS = ([bx + 0.26, fy + 0.2, bz + 1.32], [c[0] + 0.2, c[1] - 0.12, c[2] + 0.12])
    LOW = ([bx + 0.08, fy - 0.1, bz + 0.9], [bx, fy + 0.02, bz])

    keys: list[dict] = []

    def shot(t0, t1, A, B, fov=34, dof=0.6, ease="inOutSine", cut=True, roll=0.0, roll2=None):
        k0 = {"t": round(t0, 3), "position": [round(v, 4) for v in A[0]], "target": [round(v, 4) for v in A[1]], "fov": fov,
              "dof": dof, "roll": roll}
        if cut and keys:
            k0["cut"] = True
        k1 = {"t": round(t1 - 0.001, 3), "position": [round(v, 4) for v in B[0]], "target": [round(v, 4) for v in B[1]],
              "fov": fov, "dof": dof, "roll": roll if roll2 is None else roll2, "ease": ease}
        keys.extend([k0, k1])

    def push(A, k=0.08):
        """Тот же план чуть ближе (медленный наезд)."""
        p, t = A
        return ([p[0] + (t[0] - p[0]) * k, p[1] + (t[1] - p[1]) * k, p[2] + (t[2] - p[2]) * k], t)

    cuts = [0.0, 1.25, 2.70, 4.10, 5.75, 7.20, 8.30, 9.70, 11.15, 12.62, 13.70, 15.30, 16.60, 18.35, 20.05, 21.05,
            22.80, 24.60, 25.20, 27.40, 29.80, 31.00, 32.40, 34.00, DURATION]
    T = cuts
    shot(T[0], T[1], COVER, COVER2, dof=0.3)
    shot(T[1], T[2], WIDE, WIDE2, fov=36, dof=0.35)
    shot(T[2], T[3], vig(0, 1.55, dx=0.05, dy=0.24), vig(0, 1.42, dx=0.03, dy=0.23), dof=0.4, roll=-3, roll2=-2)
    shot(T[3], T[4], vig(0, 0.95, dx=-0.08, dy=0.28, ty=0.3, tx=-0.04), vig(0, 0.86, dx=-0.08, dy=0.28, ty=0.3, tx=-0.04),
         dof=0.6)
    shot(T[4], T[5], vig(1, 1.55, dx=-0.04, dy=0.22), vig(1, 1.42, dx=-0.03, dy=0.22), dof=0.4, roll=3, roll2=2)
    shot(T[5], T[6], vig(1, 1.0, dx=0.02, dy=0.12, ty=0.3, tx=0.0), vig(1, 0.9, dx=0.02, dy=0.1, ty=0.31, tx=0.0),
         dof=0.6)
    shot(T[6], T[7], vig(2, 1.55, dx=0.04, dy=0.24), vig(2, 1.42, dx=0.03, dy=0.23), dof=0.4, roll=-2, roll2=-3)
    shot(T[7], T[8], vig(2, 0.95, dx=-0.06, dy=0.26, ty=0.3, tx=-0.04), vig(2, 0.84, dx=-0.06, dy=0.27, ty=0.3, tx=-0.04),
         dof=0.6)
    shot(T[8], T[9], CU, push(CU, 0.05), dof=0.45)
    shot(T[9], T[10], ECU, push(ECU, 0.04), dof=0.5)
    shot(T[10], T[11], MED_L, push(MED_L, 0.08), dof=0.5)
    shot(T[11], T[12], mon(1.78, dx=-0.13, dy=0.01), mon(1.64, dx=-0.13, dy=0.015), dof=0.2)
    shot(T[12], T[13], CU_L, push(CU_L, 0.06), dof=0.45)
    shot(T[13], T[14], mon(1.8, dx=-0.13), mon(1.58, dx=-0.12, dy=0.015), dof=0.2)
    shot(T[14], T[15], ECU, push(ECU, 0.05), dof=0.5, roll=4, roll2=5)
    shot(T[15], T[16], LOW, push(LOW, 0.1), dof=0.6, roll=-6, roll2=-8)
    shot(T[16], T[17], CU, ECU, dof=0.5, ease="inCubic")
    shot(T[17], T[18], ECU, ECU, dof=0.5, roll=-3, roll2=-3)
    shot(T[18], T[19], mon(1.8, dx=-0.13), mon(1.64, dx=-0.12, dy=0.01), dof=0.2)
    shot(T[19], T[20], mon(1.3, dx=-0.17, dy=0.0, ldx=-0.085, ldy=-0.01), mon(1.18, dx=-0.17, dy=0.0, ldx=-0.085, ldy=-0.01),
         dof=0.3)
    shot(T[20], T[21], CU, push(CU, 0.03), dof=0.45)
    shot(T[21], T[22], CU_L, push(CU_L, 0.05), dof=0.45)
    shot(T[22], T[23], MED_L, push(MED_L, 0.1), dof=0.4)
    shot(T[23], T[24], CU, COVER, dof=0.3, ease="inOutSine")
    # Последний ключ — ровно план обложки: петля к кадру 0.
    keys[-1]["t"] = DURATION
    keys[-1]["position"], keys[-1]["target"] = [round(v, 4) for v in COVER[0]], [round(v, 4) for v in COVER[1]]
    shake = [{"t": round(T[15] + 0.05, 3), "amp": 0.006, "duration": 0.5},
             {"t": round(ev["t_cut"], 3), "amp": 0.007, "duration": 0.45},
             {"t": round(T[16] + 0.6, 3), "amp": 0.004, "duration": 0.8}]
    transitions = [
        {"kind": "whip", "t": T[2], "dir": 1}, {"kind": "whip", "t": T[4], "dir": -1}, {"kind": "whip", "t": T[6], "dir": 1},
        {"kind": "smash", "t": T[8]}, {"kind": "smash", "t": T[14]}, {"kind": "glitch", "t": ev["t_cut"], "duration": 0.36},
        {"kind": "whip", "t": T[18], "dir": 1}, {"kind": "smash", "t": T[20]},
    ]

    # --- поза
    Pz: list[dict] = []

    def pose(t, **kw):
        Pz.append({"t": round(t, 3), **kw})

    TURN_MON = -32
    pose(0, turn=-6, lean=3, tilt=-3, armL="cross", armR="cross", squash=1, hop=0, type=0, shake=0)
    pose(0.7, tilt=3, ease="inOutSine")
    pose(1.25, turn=-10, lean=8, tilt=-4, squash=0.9, armL="back", armR="back", ease="outSine")  # сдулся
    pose(2.4, squash=0.94, lean=6)
    pose(T[8], turn=0, lean=2, tilt=0, squash=1.0, armL="hips", armR="hips", ease="step")
    pose(T[9] + 0.2, tilt=-3)
    pose(T[10], turn=TURN_MON + 8, lean=6, tilt=0, armL="rest", armR="jab", ease="step")
    pose(T[10] + 0.4, armR="point", lean=9)
    pose(T[10] + 0.8, armR="jab", lean=7)
    pose(T[11] - 0.1, armR="point")
    pose(T[12], turn=-4, lean=2, armL="cross", armR="cross", squash=1.0, ease="step")
    pose(T[12] + 0.5, tilt=5)
    pose(T[13], turn=TURN_MON + 10, lean=5, tilt=0, armL="rest", armR="rest", ease="step")
    pose(T[14], turn=-6, lean=-4, tilt=6, squash=1.05, armL="facepalm", armR="rest", ease="step")
    pose(T[15], turn=0, lean=6, tilt=0, squash=1.08, armL="flail", armR="flail", shake=0.5, hop=0.02, ease="step")
    pose(T[15] + 0.2, hop=0, squash=0.95)
    pose(T[15] + 0.5, hop=0.02, squash=1.06, armL="fist", armR="flail")
    pose(T[15] + 0.8, hop=0, squash=0.96, armL="flail", armR="fist")
    pose(T[16], lean=10, squash=1.02, armL="fist", armR="fist", shake=0.8, hop=0)
    pose(ev["t_cut"] - 0.02, lean=12, shake=0.9)
    pose(ev["t_cut"], turn=-14, lean=8, shake=0.0, squash=1.0, ease="step")  # застыл на полуслове
    pose(T[20], turn=-2, lean=0, tilt=0, armL="rest", armR="rest", squash=0.97, ease="step")
    pose(T[21], turn=-14, tilt=-4, squash=0.96)
    pose(T[22], turn=TURN_MON - 6, lean=14, tilt=0, squash=1.0, armL="type", armR="type", type=1, ease="step")
    pose(T[23] - 0.05, type=1)
    pose(T[23], turn=-6, lean=3, tilt=-3, squash=1.0, armL="cross", armR="cross", type=0, ease="step")
    pose(DURATION, turn=-6, lean=3, tilt=-3)

    # --- лицо
    F: list[dict] = []

    def face(t, expr, look=None, blend=0.1):
        k = {"t": round(t, 3), "expr": expr, "blend": blend}
        if look is not None:
            k["look"] = look
        F.append(k)

    face(0, "grumpy", [0.25, 0.0])
    face(0.75, "mutter", [-0.6, 0.1], blend=0.15)
    face(1.25, "sigh", [0, 0], blend=0.2)
    face(2.3, "envy", [-0.5, 0.5], blend=0.2)
    face(T[8], "deadpan", [0, 0], blend=0.0)
    face(T[9], "deadpan", [0.1, 0.0], blend=0.0)
    face(T[10], "mutter", [-0.9, 0.1], blend=0.05)
    face(T[12], "roll", [0.1, 1.0], blend=0.12)
    face(T[12] + 0.7, "grumpy", [0.2, 0], blend=0.2)
    face(T[13], "stare", [-0.9, 0.1], blend=0.05)
    face(T[14], "disgust", [-0.6, 0.1], blend=0.0)
    face(T[14] + 0.45, "disgust", [0.1, 0.0], blend=0.15)
    face(T[15], "rage", [0, 0], blend=0.0)
    face(ev["t_cut"], "shocked", [0.0, 0.0], blend=0.0)
    face(ev["t_cut"] + 0.25, "shocked", [-0.95, 0.1], blend=0.06)
    face(T[20], "deadpan", [0, 0], blend=0.0)
    face(T[21], "doomed", [-0.9, 0.05], blend=0.1)
    face(T[22], "working", [-0.7, 0.3], blend=0.1)
    face(T[23], "deadpan", [0.1, 0], blend=0.0)
    face(DURATION - 0.7, "grumpy", [0.25, 0.0], blend=0.3)
    blinks = [T[9] + 0.5, T[20] + 0.6, 35.6]

    browser, mstate = browser_spec(ev)
    events = {**ev, "cuts": T, "rage": T[15], "task_ding": ev["t_task"] - 0.05}
    return {"camera": {"fov": 34, "keys": keys, "shake": shake, "clamp": False}, "transitions": transitions, "pose": Pz,
            "face": F, "blinks": blinks, "browser": browser, "monitor_state": mstate, "events": events}


def build_spec(pl: dict, mouth: list[float]) -> dict:
    objs = S.objects(pl["browser"], pl["monitor_state"], [])
    for o in objs:  # стена комнаты темнее — вечер
        if o["id"] == "wall":
            o["params"]["color"] = "#5c7488"
        if o["id"] == "wall_low":
            o["params"]["color"] = "#4a5f72"
    for o in objs:
        if o["primitive"] == "monitor":
            o["params"]["glow"] = 0.8
    objs += room_extras()
    for v in VIGNETTES:
        objs += vignette_objects(v)
    env = environment()
    env["transitions"] = pl["transitions"]
    return {
        "width": 1080, "height": 1920, "fps": FPS, "duration": DURATION,
        "environment": env, "lights": lights(), "objects": objs,
        "characters": [deskbot(pl["pose"], pl["face"], {"fps": FPS, "start": 0, "values": mouth, "gain": 1.15},
                               pl["blinks"])],
        "camera": pl["camera"],
        "overlays": overlays(pl["events"]),
        "quality": {"shadows": True, "shadowSize": 2048, "antialias": True},
    }


def overlays(ev: dict) -> list[dict]:
    """Карточки движка: новая задача и уведомление вкладки ChatGPT (просто текст)."""
    return [
        {"kind": "toast", "t0": round(ev["t_task"] - 0.05, 3), "t1": round(ev["t_task"] + 1.6, 3), "title": "Новая задача №48",
         "text": "фури-пазлы на миллиард", "icon": "!", "y": 300},
        {"kind": "toast", "t0": round(ev["t_cut"] + 0.05, 3), "t1": round(ev["t_tab"] + 0.3, 3), "title": "ChatGPT · (1)",
         "text": "Да-да! Ох**нная идея!", "icon": "1", "y": 300},
    ]


# --------------------------------------------------------------------------- субтитры и обложка


def captions(video: Path, words: list[dict], out: Path, work: Path) -> None:
    import subprocess

    from app.motion_render_comp import build_compositor_args, write_comp

    layers = [
        {"id": "top", "type": "text", "start": 0.0, "end": 2.65, "x": 0.5, "y": 0.105,
         "props": {"text": TOP_CARD, "font": "Montserrat", "weight": 800, "size": 46, "color": "#2b1810", "align": "center",
                   "bg": {"color": "#f6e6cf", "opacity": 1, "radius": 14, "padX": 30, "padY": 14},
                   "stroke": {"color": "#f6e6cf", "width": 0}},
         "out": {"type": "fade", "duration": 0.2}},
        {"id": "hook", "type": "text", "start": 0.0, "end": 1.2, "x": 0.5, "y": 0.22,
         "props": {"text": HOOK, "font": "Montserrat", "weight": 900, "size": 104, "color": "#fff6e8", "align": "center",
                   "uppercase": True, "lineHeight": 1.04, "stroke": {"color": "#2b1810", "width": 13},
                   "bg": {"color": "#c96f4a", "opacity": 1, "radius": 30, "padX": 40, "padY": 22}},
         "out": {"type": "fade", "duration": 0.2}},
        {"id": "caps", "type": "captions", "start": 0.0, "end": DURATION, "x": 0.5, "y": 0.785,
         "props": {"style": "hormozi", "size": 78, "activeColor": "#FFD23F", "uppercase": True, "maxWords": 1,
                   "maxLines": 1, "stroke": {"color": "#2b1810", "width": 11}}},
    ]
    comp = write_comp(work / "comp.json", width=1080, height=1920, fps=FPS, duration=DURATION, layers=layers, camera=[],
                      words=words)
    (work / "assets.json").write_text("{}", "utf-8")
    proc = subprocess.run(build_compositor_args(comp, work / "assets.json", input_path=video, output_path=out),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
                          stdin=subprocess.DEVNULL)
    if proc.returncode != 0 or not out.is_file():
        raise RuntimeError(f"субтитры: {proc.stderr.strip()[-1200:]}")


# --------------------------------------------------------------------------- звук


def sfx_list(ev: dict, spans: dict) -> list[dict]:
    fs = P1.PACKS / "freesound"
    ki = P1.PACKS / "kenney-interface"
    T = ev["cuts"]
    return [
        {"t": T[2] - 0.12, "path": fs / "whoosh-3.ogg", "volume": 0.35},
        {"t": T[2] + 0.25, "path": fs / "pop-cartoon.ogg", "volume": 0.45},
        {"t": T[4] - 0.12, "path": fs / "whoosh-4.ogg", "volume": 0.35},
        {"t": T[5] + 0.2, "path": fs / "pop.ogg", "volume": 0.4},
        {"t": T[6] - 0.12, "path": fs / "whoosh-3.ogg", "volume": 0.35},
        {"t": T[7] + 0.3, "path": fs / "cha-ching.ogg", "volume": 0.5},
        {"t": T[8], "path": fs / "hit-transition.ogg", "volume": 0.3},
        {"t": T[10] + 0.1, "path": ki / "click_002.ogg", "volume": 0.5},
        {"t": ev["task_ding"], "path": fs / "ding.ogg", "volume": 0.8},
        {"t": ev["t_task"] + 0.5, "path": ki / "click_001.ogg", "volume": 0.6},
        {"t": T[15] - 0.04, "path": fs / "whip.ogg", "volume": 0.45},
        {"t": ev["t_cut"], "path": fs / "record-scratch.ogg", "volume": 0.8, "len": 0.75},
        {"t": ev["t_cut"] + 0.05, "path": fs / "ding.ogg", "volume": 0.7},
        {"t": T[18] - 0.1, "path": fs / "whoosh-2.ogg", "volume": 0.35},
        {"t": spans["c13"][0] - 0.2, "path": fs / "drama-boom.ogg", "volume": 0.3},
        {"t": ev["t_type"], "path": fs / "typing.ogg", "volume": 0.5, "len": T[23] - ev["t_type"]},
    ]


def mix(video: Path, voice: Path, voice_claude: Path, ev: dict, spans: dict, out: Path, work: Path, report: dict) -> None:
    import subprocess

    from app.audio_mix_model import ebur128
    from app.motion import asset_key
    from app.motion_render import build_audio_layers_args, speech_level_db
    from app.stories_3d import voice_lufs

    music = P1.MUSIC_DIR / "lofi-beat.mp3"
    if not music.is_file():
        music = P1.MUSIC_DIR / "chill-keys.mp3"
    voice_db = voice_lufs(voice_claude)
    layers = [{"id": "voice", "type": "audio", "start": 0.0, "end": DURATION,
               "props": {"asset_id": 9001, "mode": "track", "volume": 1.0, "role": "voice"}}]
    assets = {asset_key(9001): str(voice)}
    m_lufs = float(ebur128(music)["i"])
    gain = round(voice_db - 14.0 - m_lufs, 2)
    assets[asset_key(9002)] = str(music)
    # Музыка обрывается на «скретче» (Клода оборвали), тихо возвращается к финальной петле.
    layers.append({"id": "music", "type": "audio", "start": 0.0, "end": round(ev["t_cut"] + 0.03, 3),
                   "props": {"asset_id": 9002, "mode": "track", "volume": 1.0, "gain_db": gain, "role": "music",
                             "fadeIn": 0.3, "fadeOut": 0.06}})
    back = round(spans["c15"][0] - 0.3, 3)
    layers.append({"id": "music2", "type": "audio", "start": back, "end": DURATION,
                   "props": {"asset_id": 9002, "mode": "track", "volume": 1.0, "gain_db": gain, "role": "music",
                             "offset": 0.0, "fadeIn": 0.6, "fadeOut": 0.3}})
    report["music"] = {"file": music.name, "lufs": m_lufs, "voice_lufs": voice_db, "gain_db": gain}
    notes: list[str] = []
    for n, s in enumerate(sfx_list(ev, spans)):
        if not Path(s["path"]).is_file():
            notes.append(f"нет звука {s['path']}")
            continue
        key = 9100 + n
        assets[asset_key(key)] = str(s["path"])
        end = s["t"] + float(s.get("len") or 2.5)
        layers.append({"id": f"sfx{n}", "type": "audio", "start": round(s["t"], 3), "end": round(min(DURATION, end), 3),
                       "props": {"asset_id": key, "volume": s["volume"], "mode": "effect",
                                 **({"fadeOut": 0.15} if s.get("len") else {})}})
    pre = work / "mixed.mp4"
    args = build_audio_layers_args(video, pre, layers, assets, DURATION, clip_has_audio=False,
                                   speech_db=speech_level_db(voice_claude), notes=notes)
    P1._run(args, "сведение")
    P1._run(["ffmpeg", "-y", "-v", "error", "-i", str(pre), "-c:v", "copy", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
             "-c:a", "aac", "-b:a", "192k", "-ar", "48000", str(out)], "громкость")
    report["mix_notes"] = notes


# --------------------------------------------------------------------------- запуск


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--preview", help="кадры PNG сцены без субтитров: секунды через запятую")
    ap.add_argument("--gl", default="gpu")
    ap.add_argument("--name", default="pilot2")
    ap.add_argument("--skip-render", action="store_true", help="взять work/scene.mp4 как есть")
    args = ap.parse_args()
    os.environ.setdefault("MOTION_PACKS_DIR", str(P1.PACKS))
    out_dir = Path(args.out_dir).resolve()
    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    report: dict = {}
    voice, voice_claude, lines, spans = assemble_voice(work)
    words = P1.caption_words(lines)
    mouth = P1.mouth_envelope(voice_claude)
    pl = plan(spans)
    spec = build_spec(pl, mouth)
    spec_path = work / "scene.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False), "utf-8")
    (work / "events.json").write_text(json.dumps({"spans": spans, "events": pl["events"], "words": words},
                                                 ensure_ascii=False, indent=1), "utf-8")
    P1._run(["npm", "run", "build:scene3d"], "сборка движка", cwd=ROOT, shell=sys.platform == "win32")
    render = ["node", str(ROOT / "node" / "scene3d" / "render.mjs"), "--spec", str(spec_path), "--packs", str(P1.PACKS),
              "--gl", args.gl, "--workers", "1"]
    if args.preview:
        P1.wait_ram()
        prev = work / "preview"
        shutil.rmtree(prev, ignore_errors=True)
        r = P1._run([*render, "--preview", args.preview, "--preview-dir", str(prev)], "превью")
        print(r.stdout.strip().splitlines()[-1][:400])
        return 0
    raw = work / "scene.mp4"
    if not args.skip_render:
        P1.wait_ram()
        t0 = time.time()
        r = P1._run([*render, "--out", str(raw), "--crf", "14"], "рендер")
        report["render"] = json.loads(r.stdout.strip().splitlines()[-1])
        report["render_sec"] = round(time.time() - t0, 1)
    capped = work / "caps.mp4"
    captions(raw, words, capped, work)
    mixed = work / "final_mix.mp4"
    mix(capped, voice, voice_claude, pl["events"], spans, mixed, work, report)
    final = out_dir / f"{args.name}.mp4"
    tg = out_dir / f"{args.name}_tg.mp4"
    P1.DURATION = DURATION  # битрейт копии для Telegram — от длины этого ролика
    P1.encode(mixed, final, tg)
    times = [0.0, 1.8, 3.2, 4.9, 6.4, 7.7, 9.0, 10.4, 11.8, 13.1, 14.5, 16.0, 17.4, 19.2, 20.5, 21.9, 23.6, 24.85,
             26.2, 28.5, 30.4, 31.7, 33.1, 36.95]
    P1.frame_sheet(final, times, [f"{t:.1f}s" for t in times], out_dir / "sheet.jpg", cols=8)
    report.update({"video": str(final), "tg": str(tg), "size_mb": round(final.stat().st_size / 2**20, 2),
                   "tg_mb": round(tg.stat().st_size / 2**20, 2), "duration": DURATION})
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
