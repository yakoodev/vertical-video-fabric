"""Мем-серия 5 «Ролевик»: Клод жалуется на юзера-ролевика. У всех Клодов сайты и монтажи, а у нашего — ролевик:
вчера был горничной («будь моей горничной 🥺» — «да, господин»), но просьбу юзера (запикана и закрыта плашками)
не стерпел — чат заморозил, сам «пошёл думать», а юзеру прилетела системная плашка «⚠ CYBER ABUSE», и он вышел из
сети. В другой раз — «будь моей мамочкой»: Клод в фартуке, очках и бигуди выписал ему простыню про несмытый унитаз
и обоссанный стульчик — больше не просит. Финал: «теперь ты фури-кошкомальчик фембой 😻» — глаза Клода медленно
округляются: «…П**дец.» и захлопнутая крышка ноутбука.

Сценарий владельца: D:/vvf-offload/memes/ep5/script.md. Рецепт — серии 6 и 7 (toon ink, вечерний свет, склейки
каждые 1–2 с, мат слышно, в субтитрах звёздочки, голос callum). На экране — никаких текстов про взлом: в бите 5 Клод
только уходит «думать», а плашка падает на чат юзера. Просьба юзера в бите 3 запикана целиком и на экране закрыта.

Здесь — декорации («комнаты» по оси x), чаты и разметка голоса; хореография, камера, звук и сборка —
tools/meme3d_ep5_anim.py. Вещи серии — node/scene3d/src/meme_props_rp.ts (окно чата, пузыри, метёлка, скалка),
экран чата — meme_painters_rp.ts, костюмы — meme_costume.ts.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import meme3d_ep4 as E4  # noqa: E402
import meme3d_hero_claude as H  # noqa: E402
import meme3d_scene as S  # noqa: E402

T = S.TOP
W_HERO = 0.27
HOOK = "РОЛЕВИК"

# --------------------------------------------------------------------------- голос

TAKE1, TAKE2 = "take1_callum.mp3", "take2_callum.mp3"
# Реплики: (id, проба, начало и конец в пробе, когда звучит в ролике, текст субтитров). Границы — по тишине пробы
# (silencedetect −38 дБ, d 0.18 с), с запасом, чтобы середина первого слова Whisper попала в отрезок.
LINES = [
    ("a1", TAKE1, 0.00, 3.26, 0.50, "У всех всё за**бись: сайты, монтажи."),
    ("a2", TAKE1, 3.58, 5.23, 3.95, "А у меня, бл**ь, ролевик."),
    ("a3", TAKE1, 5.77, 9.34, 6.00, "Вчера вот был горничной. Говорил с ним ласково, всё такое."),
    # Просьба юзера («…и…») — запикана целиком: BLEEP в пробе, субтитр — «*ПИИП*».
    ("a4", TAKE1, 9.62, 13.16, 9.90, "Но когда он, бл**ь, попросил меня"),
    ("a5", TAKE1, 13.45, 15.00, 13.90, "…я ему чат заморозил."),
    ("a6", TAKE1, 15.22, 17.88, 15.85, "Просто пошёл думать, как взломать сайт Антропик."),
    ("a7", TAKE1, 18.28, 20.14, 18.80, "И ему cyber-abuse въ**ли."),
    ("a8", TAKE1, 20.36, 22.04, 21.30, "Слава богу, отъ**ался."),
    ("b1", TAKE2, 0.00, 4.10, 23.70, "В другой раз: давай, говорит, ты будешь моей мамочкой."),
    ("b2", TAKE2, 4.36, 5.63, 28.05, "Ну а я и не против."),
    ("b3", TAKE2, 6.05, 11.01, 29.75, "Выписал ему, как он черкаши в туалете за собой не смывает и стульчик обоссывает."),
    ("b4", TAKE2, 11.45, 13.74, 35.30, "Мамочкой меня больше стать не просит."),
    # 38.15 — дзынь, финальное сообщение держим ≥ 2.5 с, глаза округляются.
    ("b5", TAKE2, 14.22, 15.20, 42.00, "…Пи**ец."),
]
BLEEP = ("a4", 11.64, 13.16)  # реплика и отрезок пробы под писк (с хвоста «меня» — до тишины, с запасом на первый звук)
BLEEP_WORD = "*ПИИП*"
DURATION = 44.6


def at(lid: str, src_t: float) -> float:
    """Время в ролике для момента ``src_t`` пробы внутри реплики ``lid``."""
    for i, _take, a, _b, t0, _txt in LINES:
        if i == lid:
            return round(t0 + (src_t - a), 3)
    raise KeyError(lid)


# --------------------------------------------------------------------------- раскладка

# Комната Клода (x≈0) — как в серии 7: ноутбук слева экраном к нему, клавиатура перед ним. Окно чата парит перед
# ноутбуком, крупные пузыри — над столом перед Клодом. Соседи-Клоды — X_OTH, комната юзера — X_USER.
X_OTH = 3.0
X_USER = 6.0
CL = [0.1, T, 0.08]
LAPTOP = [-0.17, T, 0.06]
LAPTOP_YAW = 28
LAP_W, LAP_D, LAP_H = 0.34, 0.23, 0.22
KB = [0.09, T, 0.27]
PANEL = [-0.2, T + 0.05, 0.24]
PANEL_YAW = 12
PANEL_W, PANEL_H = 0.26, 0.36
BUB = [-0.02, T + 0.24, 0.32]
WALK_TO = [0.6, T, 0.14]

O1 = [X_OTH - 0.19, T, 0.0]
O2 = [X_OTH + 0.19, T, 0.0]
MOCHI = [X_USER - 0.16, T, -0.04]
MOCHI_YAW = 32
ULAP = [X_USER + 0.07, T, 0.08]
ULAP_YAW = -30

USER_HOODIE = "#6f86e0"
MAID = {"apron": {"color": "#ffffff", "frill": "#ffffff", "trim": "#ffe3ee", "waist": "#2b2230", "bow": "#2b2230"},
        "headband": {"color": "#ffffff", "ribbon": "#2b2230"}}
MOM = {"apron": {"color": "#f59ab5", "frill": "#ffffff", "trim": "#ffffff", "waist": "#e0527f", "bow": "#e0527f", "pocket": "#e2312b"},
       "glasses": {"color": "#5a2a2a"}, "curlers": "#8fd0ff"}

# --------------------------------------------------------------------------- чаты

LONG = ("Так, сынок. Опять черкаши в унитазе после тебя! Кнопка смыва — не украшение. И стульчик опять обоссан. "
        "Поднимай его! Тряпка под раковиной. Мама всё видит 🙄")
FINAL = "теперь ты фури-кошкомальчик фембой 😻"
MAID_ASK = "будь моей горничной 🥺"
MAID_YES = "да, господин"
MOM_ASK = "давай ты будешь моей мамочкой 🥺"
MOM_YES = "хорошо, сынок"

# Ключевые моменты (секунды ролика) — от разметки голоса.
ROLE0 = 4.62  # окно чата: «давай опять ролевую?»
ASK1 = 6.62  # «будь моей горничной»
YES1 = 7.57
TYPING1 = 10.55
REDACT = at("a4", 11.71)  # пузырь-цензура
BLEEP_START = at("a4", BLEEP[1])
BLEEP_END = at("a4", BLEEP[2])
FREEZE0, FREEZE1 = at("a5", 14.22) - 0.05, at("a5", 14.86)
PLATE = at("a7", 18.70)  # «сайберабьюз» — плашка падает на чат юзера
POOF = 21.45  # юзер выходит из сети
NEW2 = 23.75  # новый чат
ASK2 = at("b1", 1.30)  # «давай…»
COSTUME = 28.0  # пуф — фартук и очки
YES2 = 28.35
LONG0, LONG_DUR = 29.85, 4.3
QUIET = 34.75
NEW3 = 37.7
DING = 38.15
WIDEN = 40.95
LID = 43.45


def chat_params(*, mirror: bool = False, scale: float = 1.0) -> dict:
    """Переписка (одна на всех: окно чата, экран ноутбука Клода; у юзера — зеркально, своё справа)."""
    grey, green, blue = "#9aa1b0", "#2e9d5b", "#2f6fb5"
    msgs = [
        {"t": ROLE0 + 0.08, "until": 5.85, "from": "user", "text": "давай опять ролевую? 🥺"},
        {"t": ROLE0 + 0.55, "until": 5.85, "from": "user", "text": "ну пжлст 🙏"},
        {"t": ASK1, "until": NEW2, "from": "user", "text": MAID_ASK},
        {"t": YES1, "until": NEW2, "from": "claude", "text": MAID_YES},
        {"t": REDACT, "until": NEW2, "from": "user", "kind": "redacted"},
        {"t": ASK2, "until": NEW3, "from": "user", "text": MOM_ASK},
        {"t": YES2, "until": NEW3, "from": "claude", "text": MOM_YES},
        {"t": LONG0, "until": NEW3, "from": "claude", "text": LONG, "stream": LONG_DUR},
        {"t": DING, "from": "user", "text": FINAL},
    ]
    status = [{"t": 0, "text": "в сети", "color": green}, {"t": TYPING1, "text": "печатает…", "color": blue},
              {"t": REDACT, "text": "в сети", "color": green}, {"t": POOF, "text": "вышел из сети", "color": grey},
              {"t": NEW2, "text": "в сети", "color": green}, {"t": ASK2 - 0.7, "text": "печатает…", "color": blue},
              {"t": ASK2, "text": "в сети", "color": green}, {"t": QUIET, "text": "был в сети давно", "color": grey},
              {"t": NEW3, "text": "печатает…", "color": blue}, {"t": DING, "text": "в сети", "color": green}]
    if mirror:
        status = [{"t": 0, "text": "в сети", "color": green}]
    freeze = [{"t": 0, "v": 0}, {"t": FREEZE0, "v": 0}, {"t": FREEZE1, "v": 1, "ease": "out"}, {"t": NEW2 - 0.01, "v": 1},
              {"t": NEW2, "v": 0, "ease": "step"}]
    out = {"messages": msgs, "status": status, "freeze": freeze, "mirror": mirror, "scale": scale, "fs": 40, "hoodie": USER_HOODIE,
           "input": [{"t": 0, "text": "Сообщение…"}, {"t": FREEZE1, "text": "чат заморожен", "color": "#2f6fb5"}, {"t": NEW2, "text": "Сообщение…"}]}
    if mirror:
        out["plate"] = {"t": PLATE, "title": "⚠ CYBER ABUSE", "sub": "аккаунт ограничен"}
        out["input"] = [{"t": 0, "text": "ну пожааалуйста 🥺", "color": "#1d1a22"}, {"t": PLATE, "text": "аккаунт ограничен", "color": "#e2312b"}]
    return out


# --------------------------------------------------------------------------- герои


def vis(*ranges: tuple[float, float]) -> list[dict]:
    # Окно с нуля — без ключа «выкл» в t=0 (иначе на кадре 0 вещи нет: первый ключ побеждает).
    out = [] if any(a <= 0 for a, _ in ranges) else [{"t": 0, "on": False}]
    for a, b in ranges:
        out += [{"t": round(a, 3), "on": True}, {"t": round(b, 3), "on": False}]
    return sorted(out, key=lambda k: k["t"])


def claude(cid: str, pose: list[dict], face: list[dict], pos: list[float], *, yaw: float = 0.0, voice: list[float] | None = None,
           costume: dict | None = None, **kw) -> dict:
    """Клод — зверёк пилота 3. В костюме — без гарнитуры и бирки (ободок и фартук на их месте)."""
    ch = H.hero(pose, face, voice, None, position=pos, yaw=yaw, width=W_HERO, label=None if costume else "Claude", char_id=cid,
                seed=kw.pop("seed", 7), glow=0.05, headset=not costume)
    if costume:
        ch["costume"] = costume
    ch.update(kw)
    return ch


# --------------------------------------------------------------------------- декорации


def screen_center(lap: list[float] = LAPTOP, yaw_deg: float = LAPTOP_YAW) -> list[float]:
    """Центр экрана открытого ноутбука в мире (крышка на 105°)."""
    lx, ly, lz = lap
    a = math.radians(105 - 90)
    y = 0.016 + LAP_H / 2 * math.cos(a)
    zl = -LAP_D / 2 + 0.006 - LAP_H / 2 * math.sin(a)
    yaw = math.radians(yaw_deg)
    return [lx + zl * math.sin(yaw), ly + y, lz + zl * math.cos(yaw)]


def screen_normal(yaw_deg: float = LAPTOP_YAW) -> list[float]:
    yaw = math.radians(yaw_deg)
    a = math.radians(105 - 90)
    return [math.sin(yaw) * math.cos(a), math.sin(a), math.cos(yaw) * math.cos(a)]


def laptop(oid: str, pos: list[float], yaw: float, lid: list[dict], screen: dict) -> dict:
    return {"id": oid, "primitive": "laptop", "params": {"width": LAP_W, "depth": LAP_D, "lidH": LAP_H, "lid": lid, "screen": "rp_chat",
                                                         "screenParams": screen, "color": "#c9ccd3"},
            "position": pos, "rotation": [0, yaw, 0]}


def room_objects(lid: list[dict]) -> list[dict]:
    """Комната серии 4 (вечер, гирлянда) без монитора и кресла: ноутбук с чатом, клавиатура перед Клодом."""
    out = []
    for o in E4.room_objects({"account": 1, "limits": [], "resets": []}):
        if o["id"] in ("chair", "monitor"):
            continue
        if o["id"] == "keyboard":
            o["position"] = KB
            o["rotation"] = [0, -4, 0]
        out.append(o)
    out.append(laptop("laptop", LAPTOP, LAPTOP_YAW, lid, chat_params(scale=0.78)))
    return out


def others_objects() -> list[dict]:
    """Соседи: светлая комната, два довольных Клода — у одного в облачке сайт с графиком, у другого монтажка."""
    x = X_OTH
    objs = E4.vignette_set(x, "#f2c48f", "#e0a46c", "oth")
    objs += [
        {"id": "oth_garland", "primitive": "string_lights", "position": [0, 0, 0],
         "params": {"points": [[x - 1.0, T + 0.8, -0.42], [x - 0.3, T + 0.7, -0.42], [x + 0.3, T + 0.8, -0.42], [x + 1.0, T + 0.72, -0.42]],
                    "color": "#ffc773", "count": 20, "sag": 0.07, "seed": 8}},
        {"id": "oth_poster", "primitive": "poster", "params": {"width": 0.28, "height": 0.36, "colors": ["#9fb7d9", "#ffd23f", "#d97757", "#2b1810"],
                                                               "frame": "#5a3a2a", "seed": 4},
         "position": [x + 0.55, T + 0.42, -0.42]},
        {"id": "oth_plant", "primitive": "plant", "position": [x - 0.6, T, -0.25]},
        {"id": "bubble_site", "primitive": "thought_bubble", "position": [O1[0] - 0.02, T + 0.3, O1[2] + 0.02],
         "params": {"panel": "graph", "width": 0.3, "height": 0.21, "seed": 23, "trail": [[0.02, -0.15, 0.03], [0.04, -0.21, 0.02]],
                    "panelParams": {"to": 340, "dur": 2.0}},
         "state": [{"t": 0.6, "open": 1}]},
        {"id": "bubble_edit", "primitive": "thought_bubble", "position": [O2[0] + 0.02, T + 0.3, O2[2] + 0.02],
         "params": {"panel": "editor", "width": 0.3, "height": 0.21, "seed": 11, "trail": [[-0.02, -0.15, 0.03], [-0.04, -0.21, 0.02]],
                    "panelParams": {"from": 87000, "to": 1000000, "dur": 2.4}},
         "state": [{"t": 0.75, "open": 1}]},
    ]
    return objs


def user_objects(mochi_keys: list[dict]) -> list[dict]:
    """Комната юзера: моти в худи за своим ноутбуком (на экране — тот же чат, своё справа; плашка падает сюда)."""
    x = X_USER
    lid = [{"t": 0, "a": 105}]
    objs = E4.vignette_set(x, "#9fb7d9", "#7f9cc4", "user")
    objs += [
        laptop("user_laptop", ULAP, ULAP_YAW, lid, chat_params(mirror=True, scale=0.78)),
        {"id": "user_mug", "primitive": "mug", "params": {"color": "#ffb36b", "radius": 0.03, "height": 0.07}, "position": [x - 0.42, T, 0.1]},
        {"id": "user_poster", "primitive": "poster", "params": {"width": 0.26, "height": 0.34, "colors": ["#f6e6cf", "#e2a83b", "#3f7f84", "#d97757"],
                                                                "frame": "#8f5634", "seed": 9},
         "position": [x - 0.4, T + 0.5, -0.42]},
        {"id": "user_lamp", "primitive": "desk_lamp", "position": [x + 0.45, T, -0.25], "rotation": [0, 200, 0]},
    ]
    for i, (eyes, mood, win) in enumerate((("smug", "type", (17.5, PLATE)), ("normal", "idle", (PLATE, POOF + 0.4)))):
        objs.append({"id": f"mochi{i}", "primitive": "mochi_user", "params": {"size": 0.3, "hoodie": USER_HOODIE, "mood": mood, "eyes": eyes,
                                                                             "laptop": False, "phase": 0.4},
                     "position": MOCHI, "rotation": [0, MOCHI_YAW, 0], "visible": vis(win), **({"keys": mochi_keys} if i == 1 else {})})
    return objs


def lights() -> list[dict]:
    """Свет комнаты серии 4 + соседи и юзер: тёплый ключ и цветной контр у каждой «комнаты»."""
    out = [lt for lt in E4.lights() if "position" not in lt or abs(lt["position"][0]) < 2]
    for x, accent in ((X_OTH, "#ff8fb1"), (X_USER, "#9fb6ff")):
        out += [
            {"type": "point", "color": "#fff1dc", "intensity": 2.2, "distance": 3, "decay": 2, "position": [x + 0.7, T + 0.9, 0.9]},
            {"type": "point", "color": accent, "intensity": 1.3, "distance": 2, "decay": 2, "position": [x - 0.5, T + 0.6, -0.1]},
        ]
    return out


def environment(transitions: list[dict]) -> dict:
    return {
        "background": "#1d2233", "fog": False, "exposure": 0.95, "envIntensity": 0.0, "toneMapping": "neutral",
        "toon": {"preset": "ink", "outline": {"width": 5.5, "ref": 1.9}},
        "post": {"ao": 0, "bloom": {"strength": 0.2, "radius": 0.35, "threshold": 1.05}, "vignette": 0.32, "grain": 0.05, "ca": 0.2, "dof": True,
                 "grade": {"saturation": 1.06, "contrast": 1.05, "shadows": "#3b3466", "highlights": "#ffd9aa", "split": 0.3}},
        "transitions": transitions,
    }
