"""Мем-серия 10 «Сделай за ночь»: юзер перед сном — «Клод, сделай кнопку «Купить». Я спать». Клод — «Одна кнопка. За
ночь. Принято», и всю ночь (часы 23:00 → 01:00 → 03:00 → 05:00) строит: базу данных, микросервисы, очередь сообщений,
k8s, тридцать страниц документации — «Сорок семь файлов. Красота». В 07:00 в окне утро, юзер: «Ну что, где кнопка?» —
на сайте пустое место там, где должна быть кнопка. «Кнопка… в планах на второй спринт». Глоток остывшего чая.

Сценарий: D:/vvf-offload/memes/ep10/script.txt (из черновика script_draft.txt; ночь 22→23.11, «сюжеты свои»).
Рецепт и герой — серии 9 v2: оба голоса (Клод — callum, юзер — liam), субтитры у обоих (юзер — голубым, как в серии 8),
toon ink, склейки 1–2 с на сетке шага поз.

Здесь — разметка голоса, моменты, дерево файлов, чаты и декорации; хореография, камера и сборка —
tools/meme3d_ep10_anim.py, звук, проверки и выпуск — tools/meme3d_ep10_mix.py, синтез — tools/meme3d_ep10_voice.py.
Дерево файлов и пустое место кнопки — painter'ы node/scene3d/src/meme_painters_night.ts, стопка документации —
meme_props_night.ts.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import meme3d_ep4 as E4  # noqa: E402
import meme3d_ep5 as E5  # noqa: E402
import meme3d_ep8 as E8  # noqa: E402
import meme3d_ep10_voice as VO  # noqa: E402
import meme3d_scene as S  # noqa: E402

T = S.TOP
W_HERO = E5.W_HERO
HOOK = "47 ФАЙЛОВ"
vis = E5.vis
USER_VOICE = VO.USER_VOICE
T1 = VO.T1
user_take = VO.user_take

# --------------------------------------------------------------------------- голос
# (id, проба, начало и конец в пробе, когда звучит в ролике, текст субтитров). Границы — по тишине проб (silencedetect
# −38 дБ, d 0.12 с) с запасом 0.03 с. Клод — одна проба (id на «c»), юзер — по пробе на реплику (id на «u»). Голоса не
# наезжают: между репликами ≥ 0.3 с.
LINES = [
    ("u1", user_take("u1"), 0.00, 3.16, 0.70, "Клод, сделай кнопку «Купить»… Я спать."),
    ("c1a", T1, 0.00, 1.10, 4.35, "Одна кнопка."),
    ("c1b", T1, 1.45, 2.08, 5.75, "За ночь."),
    ("c1c", T1, 2.48, 3.00, 6.68, "Принято."),
    ("c2", T1, 3.44, 5.77, 8.50, "Кнопке, конечно, нужна база данных."),
    ("c3", T1, 6.17, 8.00, 11.85, "Базе нужны микросервисы."),
    ("c4", T1, 8.38, 10.74, 14.00, "Микросервисам — очередь сообщений."),
    ("c5a", T1, 11.13, 12.26, 17.45, "И документация."),
    ("c5b", T1, 12.63, 13.77, 18.85, "Тридцать страниц."),
    ("c5c", T1, 14.05, 15.21, 20.25, "Без неё никак."),
    ("c6a", T1, 15.53, 16.82, 22.45, "Сорок семь файлов."),
    ("c6b", T1, 17.19, 18.11, 24.00, "Красота."),
    ("u2", user_take("u2"), 0.00, 1.53, 26.20, "Ну что, где кнопка?"),
    ("c7a", T1, 18.52, 19.25, 31.00, "Кнопка…"),  # после паузы: смотрит на пустое место
    ("c7b", T1, 19.44, 20.95, 32.30, "в планах на второй спринт."),  # 19.44 — ещё тишина; «в» Whisper ставит раньше неё
]


def is_user(lid: str) -> bool:
    return lid.startswith("u")


CLAUDE_LINES = [ln for ln in LINES if not is_user(ln[0])]
USER_LINES = [ln for ln in LINES if is_user(ln[0])]
AT = {lid: at for lid, _tk, _a, _b, at, _tx in LINES}
END = {lid: round(at + (b - a), 3) for lid, _tk, a, b, at, _tx in LINES}
TAIL = 2.8  # глоток остывшего чая и каменное лицо
DURATION = round(END["c7b"] + TAIL, 2)

# --------------------------------------------------------------------------- моменты (секунды ролика)

TYPE_ASK = 0.75  # юзер печатает (сонно)
ASK = 2.0  # его сообщение в чате
SLEEP = 3.95  # уснул над ноутбуком — zzz
CLOCK_23, CLOCK_01, CLOCK_03, CLOCK_05, MORNING = 7.4, 10.933, 16.533, 21.466, 25.0  # на сетке шага поз 1/15 с
HOURS = [(CLOCK_23, "23:00"), (CLOCK_01, "01:00"), (CLOCK_03, "03:00"), (CLOCK_05, "05:00"), (MORNING, "07:00")]
JUMP = 0.45  # стрелки часов перескакивают на два часа за столько секунд
DOCS_LAND = (18.75, 19.85)  # тридцать листов падают на стол («Тридцать страниц»)
WAKE = 26.0  # юзер проснулся
LOOK = 27.8  # Клод медленно поворачивается к экрану
EMPTY = 28.9  # на сайте — пустое место
TEA = 33.866  # тянется к чаю (на сетке шага поз)

# --------------------------------------------------------------------------- дерево файлов
# Проект растёт пачками под реплики: «база данных» → db/, «микросервисы» → *-service/, api-gateway/, «очередь
# сообщений» → queue/ и k8s/, «документация» → docs/, к «Сорок семь файлов» — тесты, CI и файлы в корне. Файлов
# (не папок) ровно 47, последний появляется на слове «файлов». Кнопки («Купить», frontend/) в дереве нет.
PROJECT = "кнопка-купить"
_TREE = [
    # (момент, отступ, имя; «/» на конце — папка)
    (8.80, 0, "db/"), (9.00, 1, "schema.sql"), (9.20, 1, "migrations/"), (9.35, 2, "001_init.sql"), (9.50, 2, "002_users.sql"),
    (9.65, 2, "003_orders.sql"), (9.80, 2, "004_payments.sql"), (10.00, 1, "seed.sql"), (10.20, 1, "config.toml"),
    (11.95, 0, "auth-service/"), (12.10, 1, "main.go"), (12.25, 1, "auth.proto"), (12.40, 1, "Dockerfile"),
    (12.60, 0, "payments-service/"), (12.75, 1, "main.go"), (12.90, 1, "Dockerfile"),
    (13.10, 0, "cart-service/"), (13.25, 1, "main.go"), (13.40, 1, "Dockerfile"),
    (13.55, 0, "api-gateway/"), (13.70, 1, "gateway.ts"), (13.85, 1, "routes.ts"),
    (14.25, 0, "notifications-service/"), (14.40, 1, "main.go"), (14.55, 1, "Dockerfile"),
    (14.95, 0, "queue/"), (15.05, 1, "kafka.yaml"), (15.12, 1, "topics.yaml"), (15.19, 1, "producer.py"), (15.26, 1, "consumer.py"),
    (15.33, 0, "k8s/"), (15.40, 1, "deployment.yaml"), (15.47, 1, "service.yaml"), (15.54, 1, "ingress.yaml"), (15.80, 1, "hpa.yaml"),
    (15.95, 1, "helm/"), (16.10, 2, "values.yaml"),
    (17.60, 0, "docs/"), (17.75, 1, "architecture.md"), (17.90, 1, "api.md"), (18.05, 1, "adr/"), (18.20, 2, "0001-mikroservisy.md"),
    (18.35, 2, "0002-kafka.md"), (19.20, 1, "onboarding.md"), (19.50, 1, "glossary.md"), (19.80, 1, "roadmap.md"),
    (20.40, 0, "tests/"), (20.55, 1, "test_auth.py"), (20.70, 1, "test_payments.py"), (20.85, 1, "test_cart.py"), (21.00, 1, "test_queue.py"),
    (21.20, 0, ".github/"), (21.30, 1, "workflows/"), (21.40, 2, "ci.yml"),
    # «Сорок семь файлов» (22.45…23.0): последние восемь — в корне, счётчик добегает до 47 на слове «файлов».
    (22.48, 0, "docker-compose.yml"), (22.56, 0, "Makefile"), (22.64, 0, "README.md"), (22.72, 0, ".env.example"),
    (22.80, 0, "LICENSE"), (22.88, 0, "CHANGELOG.md"), (22.96, 0, "CONTRIBUTING.md"), (23.04, 0, "CODE_OF_CONDUCT.md"),
]
TREE = [{"t": t, "name": name, "depth": d, **({"dir": True} if name.endswith("/") else {})} for t, d, name in _TREE]
FILES_TOTAL = sum(1 for e in TREE if not e.get("dir"))
T47 = max(e["t"] for e in TREE if not e.get("dir"))  # последний файл — счётчик на 47
TREE_COLORS = {"db": "#4f9bff", "auth-service": "#c08cff", "payments-service": "#7ad18a", "cart-service": "#f2a65e", "api-gateway": "#38c6d9",
               "notifications-service": "#ff9ec7", "queue": "#ffb347", "k8s": "#4fd1c5", "docs": "#9be37a", "tests": "#f2c94c",
               ".github": "#b5bccd"}


def tree_params(scale: float = 1.0, fs: float = 40, follow: float = 0.94) -> dict:
    """Экран дерева. ``follow`` — где держится новая строка (доля окна): в окне чата — выше полосы субтитров."""
    return {"title": PROJECT, "entries": TREE, "colors": TREE_COLORS, "goal": FILES_TOTAL, "fs": fs, "scale": scale, "countWord": "файлов",
            "follow": follow}


def files_at(t: float) -> int:
    return sum(1 for e in TREE if not e.get("dir") and t >= e["t"])


# --------------------------------------------------------------------------- чат

GREEN, BLUE, GREY = "#2e9d5b", "#2f6fb5", "#9aa1b0"
ASK_TEXT = "сделай кнопку «Купить». я спать 😴"
OK_TEXT = "принято ✅"
WHERE_TEXT = "ну что, где кнопка? 👀"


def chat_params(*, mirror: bool = False, scale: float = 1.0) -> dict:
    """Переписка (окно чата у Клода; у юзера на ноутбуке — зеркально, своё справа)."""
    msgs = [{"t": ASK, "from": "user", "text": ASK_TEXT}, {"t": AT["c1c"], "from": "claude", "text": OK_TEXT},
            {"t": AT["u2"] + 0.9, "from": "user", "text": WHERE_TEXT}]
    status = [{"t": 0, "text": "в сети", "color": GREEN}, {"t": TYPE_ASK, "text": "печатает…", "color": BLUE}, {"t": ASK, "text": "в сети", "color": GREEN},
              {"t": SLEEP, "text": "не в сети", "color": GREY}, {"t": WAKE, "text": "в сети", "color": GREEN}]
    hint = {"text": "Сообщение…", "color": GREY}
    inp = [{"t": 0, **hint}]
    if mirror:
        status = [{"t": 0, "text": "в сети", "color": GREEN}]
        inp += [*E8.typed(TYPE_ASK, ASK_TEXT, 28.0), {"t": ASK, **hint}, *E8.typed(AT["u2"], WHERE_TEXT, 30.0), {"t": AT["u2"] + 0.9, **hint}]
    return {"messages": msgs, "status": status, "mirror": mirror, "scale": scale, "fs": 40, "hoodie": E5.USER_HOODIE, "input": inp}


def site_params() -> dict:
    return {"empty": True, "at": EMPTY, "title": "Мой стартап", "url": "мой-стартап.рф"}


# --------------------------------------------------------------------------- раскладка и декорации

CL, LAPTOP, LAPTOP_YAW, KB = E5.CL, E5.LAPTOP, E5.LAPTOP_YAW, E5.KB
PANEL, PANEL_YAW = E5.PANEL, E5.PANEL_YAW
PANEL_W, PANEL_H = 0.27, 0.42
X_USER, MOCHI, MOCHI_YAW, ULAP, ULAP_YAW = E5.X_USER, E5.MOCHI, E5.MOCHI_YAW, E5.ULAP, E5.ULAP_YAW
USER_HOODIE = E5.USER_HOODIE
CLOCK = E8.CLOCK
MUG = E8.MUG
SIP = E8.SIP
STEAM = [MUG[0], T + 0.05, MUG[2]]
# Чай остывает за ночь: к 05:00 пар кончился.
STEAM_POWER = [{"t": 0, "v": 1.0}, {"t": CLOCK_03, "v": 0.8}, {"t": CLOCK_05 - 0.1, "v": 0.0}]
PILE = [0.42, T, -0.11]
PILE_YAW = -18
WINDOW = [0.55, T + 0.38, -0.585]  # окно комнаты (meme3d_scene.objects)
WIN_W, WIN_H = 0.62, 0.52
NIGHT_SKY = {"sky": ["#0b1030", "#1b2858", "#333b74"], "stars": 40, "sun": {"x": 0.76, "y": 0.22, "r": 0.05, "color": "#eef0ff"},
             "city": "#1a1830", "lit": 0.35, "seed": 3}
MORNING_SKY = {"sky": ["#86bdf0", "#ffd9a6", "#ffb37a"], "stars": 0, "sun": {"x": 0.28, "y": 0.66, "r": 0.09, "color": "#fff3cf"},
               "city": "#6a5878", "lit": 0.0, "seed": 3}
CLOCK_START = 23 * 60 - 12 * 60  # 23:00 — минуты от 12:00


def clock_spins() -> list[dict]:
    """Стрелки перескакивают на два часа в каждом плане часов (01:00, 03:00, 05:00, 07:00)."""
    return [{"t0": round(t + 0.1, 3), "t1": round(t + 0.1 + JUMP, 3), "add": 120} for t, _h in HOURS[1:]]


def room_objects() -> list[dict]:
    """Комната серий 5–9 (гирлянда): ноутбук с деревом файлов, клавиатура, часы, кружка с паром, окно ночь → утро."""
    out = []
    for o in E4.room_objects({"account": 1, "limits": [], "resets": []}):
        if o["id"] in ("chair", "monitor"):
            continue
        if o["id"] == "keyboard":
            o["position"] = KB
            o["rotation"] = [0, -4, 0]
        if o["id"] == "window":
            o["params"] = {"width": WIN_W, "height": WIN_H, **NIGHT_SKY}
            o["visible"] = vis((0, MORNING))
        out.append(o)
    lap = E5.laptop("laptop", LAPTOP, LAPTOP_YAW, [{"t": 0, "a": 105}], tree_params(scale=0.8, fs=40))
    lap["params"]["screen"] = "file_tree"
    out += [
        lap,
        {"id": "window_am", "primitive": "window_frame", "params": {"width": WIN_W, "height": WIN_H, **MORNING_SKY}, "position": WINDOW,
         "visible": vis((MORNING, DURATION + 1))},
        {"id": "clock", "primitive": "wall_clock", "params": {"radius": 0.075, "minutes": CLOCK_START, "spins": clock_spins()}, "position": CLOCK},
        {"id": "steam", "primitive": "tea_steam", "params": {"height": 0.085, "width": 0.012, "power": STEAM_POWER, "opacity": 0.6},
         "position": STEAM},
        {"id": "docs", "primitive": "doc_pile", "params": {"count": 30, "width": 0.16, "depth": 0.22, "thick": 0.0035, "land": list(DOCS_LAND),
                                                           "drop": 0.07, "title": "ДОКУМЕНТАЦИЯ", "sub": "стр. {n} из {count}", "seed": 4},
         "position": PILE, "rotation": [0, PILE_YAW, 0]},
    ]
    return out


def user_objects(mochi: list[tuple[str, str, tuple[float, float]]]) -> list[dict]:
    """Комната юзера: моти в худи за ноутбуком; zzz, пока спит. ``mochi`` — (eyes, mood, окно)."""
    x = X_USER
    objs = E4.vignette_set(x, "#9fb7d9", "#7f9cc4", "user")
    objs += [
        E5.laptop("user_laptop", ULAP, ULAP_YAW, [{"t": 0, "a": 105}], chat_params(mirror=True, scale=0.78)),
        {"id": "user_mug", "primitive": "mug", "params": {"color": "#ffb36b", "radius": 0.03, "height": 0.07}, "position": [x - 0.42, T, 0.1]},
        {"id": "user_poster", "primitive": "poster", "params": {"width": 0.26, "height": 0.34, "colors": ["#f6e6cf", "#e2a83b", "#3f7f84", "#d97757"],
                                                                "frame": "#8f5634", "seed": 9},
         "position": [x - 0.4, T + 0.5, -0.42]},
        {"id": "user_lamp", "primitive": "desk_lamp", "position": [x + 0.45, T, -0.25], "rotation": [0, 200, 0]},
        {"id": "zzz", "primitive": "zzz", "params": {"size": 0.05, "color": "#e8eeff", "phase": 0.3}, "position": [MOCHI[0] + 0.12, T + 0.3, MOCHI[2]],
         "visible": vis((SLEEP, WAKE))},
    ]
    for i, (eyes, mood, win) in enumerate(mochi):
        objs.append({"id": f"mochi{i}", "primitive": "mochi_user", "params": {"size": 0.3, "hoodie": USER_HOODIE, "mood": mood, "eyes": eyes,
                                                                             "laptop": False, "phase": 0.4 + i * 0.3},
                     "position": MOCHI, "rotation": [0, MOCHI_YAW, 0], "visible": vis(win)})
    return objs


def lights() -> list[dict]:
    """Свет серий 5–9; ночью луна в окне, к 07:00 — тёплый луч солнца из окна (с лёгким столбом света) и утро у юзера."""
    out = []
    for lt in E5.lights():
        lt = dict(lt)
        if lt.get("id") == "moon":
            lt["keys"] = [{"t": 0, "intensity": lt["intensity"]}, {"t": MORNING - 0.01, "intensity": lt["intensity"]},
                          {"t": MORNING, "intensity": 0.15, "ease": "step"}]
        out.append(lt)
    wx, wy, wz = WINDOW
    on = [{"t": 0, "intensity": 0.0}, {"t": MORNING - 0.01, "intensity": 0.0}, {"t": MORNING, "intensity": 1.0, "ease": "step"}]
    out += [
        {"type": "spot", "id": "dawn", "color": "#ffc98f", "intensity": 9.0, "position": [wx + 0.25, wy + 0.75, wz - 0.9], "target": [CL[0], T + 0.05, CL[2]],
         "angle": 26, "penumbra": 0.7, "decay": 2, "distance": 4,
         "keys": [{**k, "intensity": k["intensity"] * 9.0} for k in on], "beam": {"length": 2.2, "radius": 0.3, "opacity": 0.07}},
        {"type": "point", "id": "user_sun", "color": "#ffd6a0", "intensity": 0.0, "distance": 3, "decay": 2, "position": [X_USER + 0.6, T + 0.8, 0.6],
         "keys": [{**k, "intensity": k["intensity"] * 2.4} for k in on]},
    ]
    return out


def environment(transitions: list[dict]) -> dict:
    """Окружение серий 5–9; ночь темнеет и синеет по часам, в 07:00 — тёплое утро (без пересвета: bloom прежний)."""
    env = E5.environment(transitions)
    env["phases"] = [
        {"t": CLOCK_01, "exposure": 0.9, "grade": {"saturation": 1.02, "contrast": 1.06, "shadows": "#2c2f66", "highlights": "#ffd9aa", "split": 0.35}},
        {"t": CLOCK_03, "exposure": 0.86, "grade": {"saturation": 1.0, "contrast": 1.07, "shadows": "#26296a", "highlights": "#f5d6b4", "split": 0.38}},
        {"t": CLOCK_05, "exposure": 0.9, "grade": {"saturation": 1.02, "contrast": 1.05, "shadows": "#353a72", "highlights": "#ffd7b0", "split": 0.32}},
        {"t": MORNING, "exposure": 1.02, "grade": {"saturation": 1.08, "contrast": 1.04, "shadows": "#5a4466", "highlights": "#ffe2b0", "split": 0.28}},
    ]
    return env
