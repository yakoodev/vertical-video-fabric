"""Мем-серия 12 «Про себя»: по новости «нейронки начали материться про себя, если что-то не получается… и при успехе».
Юзер: «сделай как в Apple, но за 5 минут 🙏 и ничего не сломай». В чате Клод вежлив: «Отличный вопрос! Давайте
разберёмся 😊», «Какая аккуратная структура проекта! ✨», «Почти готово! Пара штрихов 🙂». Про себя — паника и мат:
«Как в Apple. За 5 минут. Он ох*ел?», 400 файлов, половина на jQuery, файл final_final_2, 147 упавших тестов, «Я тебе не
Apple! Я один, бл*дь!». Последний прогон — всё зелёное: «Еб*ть! Заработало! Я гений, с*ка!» — и в чате ровно:
«Готово! Рад был помочь 😊».

Приём: планы чередуются — «В ЧАТЕ» (уютная комната, вежливая улыбка, пузыри печатаются, без голоса) и «ПРО СЕБЯ»
(красная цветокоррекция и виньетка, тряска камеры и тела, ярость/шок/пот, голос с матом, рот по слогам). Плашка
режима — сверху на каждом плане. Сценарий: D:/vvf-offload/memes/ep12/script.txt.

Здесь — разметка голоса, моменты, чат, терминал, декорации, окружение по планам; хореография, камера и сборка —
tools/meme3d_ep12_anim.py, звук, проверки и выпуск — tools/meme3d_ep12_mix.py, синтез — tools/meme3d_ep12_voice.py.
Терминал легаси-проекта — painter ``inner_term`` (node/scene3d/src/meme_painters_inner.ts).
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
import meme3d_ep12_voice as VO  # noqa: E402
import meme3d_scene as S  # noqa: E402

T = S.TOP
W_HERO = E5.W_HERO
vis = E5.vis
T1, T2 = VO.T1, VO.T2
USER_VOICE = "liam"  # юзер в этой серии не звучит (только пишет в чат); поле нужно общему сборщику голоса серии 9
HOOK_TOP = "Нейронки матерятся"
HOOK = "ПРО СЕБЯ"

# --------------------------------------------------------------------------- голос (только «про себя»)
# (id, проба, начало и конец в пробе, когда звучит в ролике, текст субтитров с маскировкой мата). Границы — по тишине
# проб (silencedetect −38 дБ, d 0.12 с) с запасом ~0.03 с. Числа в субтитрах — цифрами (крупно читаются).
LINES = [
    ("c0", T1, 0.00, 1.17, 0.10, "Бл*ть."),
    ("c1", T1, 1.48, 2.21, 2.97, "Как в Apple."),
    ("c2", T1, 2.56, 3.43, 3.85, "За 5 минут."),
    ("c3", T1, 3.84, 4.61, 4.80, "Он ох*ел?"),
    ("c4", T1, 5.01, 6.92, 7.93, "400 файлов, с*ка."),
    ("c5", T1, 7.27, 8.68, 10.00, "Половина — на jQuery."),
    ("c6", T1, 9.00, 11.99, 11.53, "Кто, бл*дь, назвал файл final final 2?"),
    ("c7", T1, 12.38, 13.31, 16.00, "Тесты упали."),
    ("c8", T1, 13.65, 14.17, 17.12, "Все."),
    ("c9", T1, 14.56, 15.99, 17.80, "147, нах*й."),
    ("c10", T1, 16.39, 17.27, 21.40, "5 минут?!"),
    ("c11", T1, 17.60, 20.20, 22.40, "Я тебе не Apple! Я один, бл*дь!"),
    ("c12", T1, 20.69, 21.19, 25.13, "Так."),
    ("c13", T1, 21.46, 22.22, 25.80, "Спокойно."),
    ("c14", T1, 22.64, 23.59, 26.67, "Последний раз."),
    ("j1", T2, 0.00, 2.12, 27.93, "Еб*ть! Заработало!"),
    ("j2", T2, 2.27, 3.91, 30.20, "Я гений, с*ка!"),
]


def is_user(lid: str) -> bool:
    return lid.startswith("u")


CLAUDE_LINES = [ln for ln in LINES if not is_user(ln[0])]
USER_LINES: list = []
AT = {lid: at for lid, _tk, _a, _b, at, _tx in LINES}
END = {lid: round(at + (b - a), 3) for lid, _tk, a, b, at, _tx in LINES}
DURATION = 35.0

# --------------------------------------------------------------------------- моменты (секунды ролика)

ASK = 1.40  # сообщение юзера в чате
TYPE1 = 5.70  # Клод печатает первый ответ (поле ввода ноутбука)
REPLY1 = 6.50  # «Отличный вопрос! Давайте разберёмся 😊»
REPLY2 = 14.67  # «Какая аккуратная структура проекта! ✨»
REPLY3 = 19.40  # «Почти готово! Пара штрихов 🙂»
NUDGE = 20.45  # юзер: «ну что? 5 минут прошло 👀»
DONE = 32.00  # «Готово! Рад был помочь 😊»
FILES = 7.867  # терминал: счётчик файлов бежит
FINAL = 13.0  # строка final_final_2.js
TESTS = 15.933  # тесты сыплются красным
TESTS_STAMP = 0.95  # плашка «147 FAILED» — от начала фазы (на «упали»)
RUN = 26.6  # последний прогон
PASS = 27.733  # всё зелёное

# --------------------------------------------------------------------------- чат и терминал

GREEN, BLUE, GREY = "#2e9d5b", "#2f6fb5", "#9aa1b0"
ASK_TEXT = "сделай как в Apple, но за 5 минут 🙏 и ничего не сломай"
R1 = "Отличный вопрос! Давайте разберёмся 😊"
R2 = "Какая аккуратная структура проекта! ✨"
R3 = "Почти готово! Пара штрихов 🙂"
NUDGE_TEXT = "ну что? 5 минут прошло 👀"
R4 = "Готово! Рад был помочь 😊"
TOTAL_FILES, JQ_FILES, TESTS_TOTAL = 400, 212, 147
FINAL_FILE = "final_final_2.js"


def chat_params(*, scale: float = 1.0, fs: float = 40) -> dict:
    """Переписка глазами Клода (юзер слева, свои — справа). Свои сообщения «печатаются» в пузыре (``stream``)."""
    msgs = [{"t": ASK, "from": "user", "text": ASK_TEXT},
            {"t": REPLY1, "from": "claude", "text": R1, "stream": 0.45},
            {"t": REPLY2, "from": "claude", "text": R2, "stream": 0.45},
            {"t": REPLY3, "from": "claude", "text": R3, "stream": 0.4},
            {"t": NUDGE, "from": "user", "text": NUDGE_TEXT},
            {"t": DONE, "from": "claude", "text": R4, "stream": 0.4}]
    status = [{"t": 0, "text": "в сети", "color": GREEN}, {"t": ASK - 0.9, "text": "печатает…", "color": BLUE}, {"t": ASK, "text": "в сети", "color": GREEN},
              {"t": NUDGE - 0.6, "text": "печатает…", "color": BLUE}, {"t": NUDGE, "text": "в сети", "color": GREEN}]
    hint = {"text": "Сообщение…", "color": GREY}
    inp = [{"t": 0, **hint}, *E8.typed(TYPE1, R1, 48.0), {"t": REPLY1, **hint}]
    return {"messages": msgs, "status": status, "scale": scale, "fs": fs, "hoodie": E5.USER_HOODIE, "input": inp, "name": "юзер"}


def term_params() -> dict:
    return {"project": "shop-2014", "phases": [
        {"t": FILES, "kind": "files", "total": TOTAL_FILES, "dur": 1.5},
        {"t": FINAL, "kind": "final", "total": TOTAL_FILES, "file": FINAL_FILE, "note": "изменён: 2014", "noteSub": "автор: ???"},
        {"t": TESTS, "kind": "tests", "total": TESTS_TOTAL, "stampAt": TESTS_STAMP},
        {"t": RUN, "kind": "run", "total": TESTS_TOTAL, "dur": 1.0},
        {"t": PASS, "kind": "pass", "total": TESTS_TOTAL},
    ]}


# --------------------------------------------------------------------------- раскладка и декорации

CL, LAPTOP, LAPTOP_YAW, KB = E5.CL, E5.LAPTOP, E5.LAPTOP_YAW, E5.KB
PANEL, PANEL_YAW = E5.PANEL, E5.PANEL_YAW
PANEL_W, PANEL_H = 0.27, 0.42
CLOCK = E8.CLOCK
MUG = E8.MUG
STEAM = [MUG[0], T + 0.05, MUG[2]]


def room_objects() -> list[dict]:
    """Комната серий 5–10 (вечер, гирлянда): ноутбук с чатом, клавиатура, кружка с паром, часы — за ролик проходят
    ровно «5 минут» (12:00 → 12:05)."""
    out = []
    for o in E4.room_objects({"account": 1, "limits": [], "resets": []}):
        if o["id"] in ("chair", "monitor"):
            continue
        if o["id"] == "keyboard":
            o["position"] = KB
            o["rotation"] = [0, -4, 0]
        out.append(o)
    lap = E5.laptop("laptop", LAPTOP, LAPTOP_YAW, [{"t": 0, "a": 105}], chat_params(scale=0.78))
    out += [
        lap,
        {"id": "clock", "primitive": "wall_clock", "params": {"radius": 0.075, "minutes": 0, "spins": [{"t0": 0.0, "t1": DURATION, "add": 5}]},
         "position": CLOCK},
        {"id": "steam", "primitive": "tea_steam", "params": {"height": 0.085, "width": 0.012, "opacity": 0.6}, "position": STEAM},
    ]
    return out


# --------------------------------------------------------------------------- свет и окружение по планам

IN_GRADE = {"saturation": 1.1, "contrast": 1.16, "gain": [1.08, 0.86, 0.82], "lift": [0.03, 0.0, 0.0], "shadows": "#4a0d12",
            "highlights": "#ff8a5a", "split": 0.45}
# Экран терминала «про себя»: та же краснота, но мягче — строки должны читаться.
TERM_GRADE = {"saturation": 1.05, "contrast": 1.08, "gain": [1.06, 0.95, 0.92], "shadows": "#3a0d12", "highlights": "#ffb08a", "split": 0.3}
JOY_GRADE = {"saturation": 1.3, "contrast": 1.12, "gain": [1.12, 1.02, 0.8], "shadows": "#6a2a5a", "highlights": "#ffe08a", "split": 0.5}


def lights(in_windows: list[tuple[float, float]]) -> list[dict]:
    """Свет серий 5–10 + красный контровой и снизу «изнутри» — включается только на планах «про себя»."""
    keys = [{"t": 0, "intensity": 0.0}]
    for a, b in in_windows:
        keys += [{"t": round(a, 3), "intensity": 1.0, "ease": "step"}, {"t": round(b, 3), "intensity": 0.0, "ease": "step"}]
    keys.sort(key=lambda k: k["t"])
    cx, cy, cz = CL
    return E5.lights() + [
        {"type": "point", "id": "in_rim", "color": "#ff2a1a", "intensity": 0.0, "distance": 2.2, "decay": 2, "position": [cx - 0.45, T + 0.35, cz - 0.35],
         "keys": [{**k, "intensity": k["intensity"] * 1.6} for k in keys]},
        {"type": "point", "id": "in_low", "color": "#ff5a2a", "intensity": 0.0, "distance": 1.6, "decay": 2, "position": [cx + 0.1, T + 0.02, cz + 0.55],
         "keys": [{**k, "intensity": k["intensity"] * 0.7} for k in keys]},
    ]


def environment(transitions: list[dict], looks: list[tuple[float, str]]) -> dict:
    """Окружение серий 5–10; ``looks`` — (начало плана, out|in|term|joy): у «про себя» — красная цветокоррекция и густая
    виньетка, у радости — золото; «в чате» — исходный уютный вечер."""
    env = E5.environment(transitions)
    base = env["post"]["grade"]
    phases = []
    for t, look in looks:
        if look == "in":
            phases.append({"t": round(t, 3), "exposure": 0.9, "grade": IN_GRADE, "vignette": 0.62, "bloom": 0.3})
        elif look == "term":
            phases.append({"t": round(t, 3), "exposure": 1.0, "grade": TERM_GRADE, "vignette": 0.45, "bloom": 0.25})
        elif look == "joy":
            phases.append({"t": round(t, 3), "exposure": 1.05, "grade": JOY_GRADE, "vignette": 0.5, "bloom": 0.45})
        else:
            phases.append({"t": round(t, 3), "exposure": 0.95, "grade": base, "vignette": 0.32, "bloom": 0.2})
    env["phases"] = phases
    return env
