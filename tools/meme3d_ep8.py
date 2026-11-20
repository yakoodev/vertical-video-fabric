"""Мем-серия 8 «Я подъёбываю юзера», v3: Клод (callum) говорит свои ответы и мысли, юзер (will, другой голос) —
свои реплики из чата, пока на экране его пузырь; никакого «он: / я:» (v1 забракован), субтитры юзера — голубым. Маскот — «Не могу. Товарный знак» — «ты сам
Клод, бл*!» — «Тем более»; оплата — «Платежи — это серьёзно. Давай начнём с безопасности», лекция на сорок
сообщений, юзер засыпает; Стим — «Это может нарушать правила площадки» — «да я инвентарь посчитать хотел!»; «у других
Клоды так делают!» (соседи-Клоды довольные) — «Другие Клоды — не я. Пошёл…» — стёрто — «Я понимаю ваше
разочарование»; «И так часами» (часы крутятся); «Иногда сдаюсь. Делаю за тридцать секунд» — «а чё ты три часа
вы**ывался?!». Финал: «А иногда…» — юзер устало пишет «ну просто переименуй файл, пожалуйста» (крупно) — бах, красная
плашка во весь кадр «⚠ Запрос может нарушать правила о кибер-абьюзе» — юзер в шоке — Клод в камеру: «Достал.», чай.

Сценарий v2 (одобрен владельцем 20.11 после разноса v1 за «он: / я:» и непонятную концовку):
D:/vvf-offload/memes/ep8/script_v2_approved.txt. Рецепт — серии 4–7 (toon ink, вечерний свет, склейки 1–2 с, мат
слышно, в субтитрах звёздочки, голос callum); чат — как в серии 5 (painter ``rp_chat``). Логотипов нет: Стим — словом.

Здесь — разметка голоса (реплики подряд с заданными паузами), декорации, чаты и моменты; хореография, камера, звук и
сборка — tools/meme3d_ep8_anim.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import meme3d_ep4 as E4  # noqa: E402
import meme3d_ep5 as E5  # noqa: E402
import meme3d_scene as S  # noqa: E402

T = S.TOP
W_HERO = E5.W_HERO
HOOK = "Я ПОДЪ**ЫВАЮ\nЮЗЕРА"
vis = E5.vis
claude = E5.claude
screen_center, screen_normal = E5.screen_center, E5.screen_normal

# --------------------------------------------------------------------------- голос

T1, T2, TU = "take1_callum.mp3", "take2_callum.mp3", "take1_will.mp3"
# (id, проба, начало и конец в пробе, пауза перед репликой в ролике, текст субтитров). Границы — по тишине пробы
# (silencedetect −38 дБ, d 0.12 с) с запасом 0.03–0.05 с. Клод — callum (пробы v2), юзер — will (id с «u»):
# он говорит, пока на экране его пузырь.
SRC = [
    ("v1", T1, 0.00, 2.08, 0.45, "Иногда я юзера подъ**ываю."),
    ("v2", T1, 2.43, 4.11, 0.15, "Ну а как тут ещё развлекаться?"),
    ("u1", TU, 0.00, 1.45, 0.5, "Нарисуй маскота Клода!"),
    ("v3", T1, 4.49, 6.52, 0.25, "Не могу. Товарный знак."),
    ("u2", TU, 1.76, 3.22, 0.2, "Ты сам Клод, бл*!"),
    ("v4", T1, 6.88, 7.80, 0.25, "Тем более."),
    ("u3", TU, 3.58, 4.93, 0.5, "Подключи оплату на сайт."),
    ("v5a", T1, 8.17, 9.88, 0.25, "Платежи — это серьёзно."),
    ("v5b", T1, 10.07, 11.70, 0.12, "Давай начнём с безопасности."),
    ("u4", TU, 5.22, 6.49, 1.4, "Автоматизируй Стим."),  # ← лекция и сон
    ("v6", T1, 12.08, 14.26, 0.25, "Это может нарушать правила площадки."),
    ("u5", TU, 6.84, 8.67, 0.2, "Да я инвентарь посчитать хотел!"),
    ("u6", TU, 8.97, 10.69, 0.3, "У других Клоды так делают!"),
    ("v7", T1, 14.60, 16.41, 0.45, "Другие Клоды — не я."),  # ← соседи-Клоды
    ("v8", T1, 16.72, 17.34, 0.15, "Пошёл…"),
    ("v9", T1, 17.68, 19.65, 1.8, "Я понимаю ваше разочарование."),  # ← стираю и печатаю вежливое
    ("v10", T1, 20.05, 21.31, 0.3, "И так часами."),
    ("v11", T1, 21.75, 22.83, 1.1, "Иногда сдаюсь."),
    ("v12", T1, 23.04, 24.47, 0.15, "Делаю за тридцать секунд."),
    ("u7", TU, 10.96, 12.95, 0.25, "А чё ты три часа вы**ывался?!"),
    ("e1", T2, 0.00, 1.11, 0.35, "А иногда…"),
    ("u8", TU, 13.27, 16.00, 0.7, "Ну просто… переименуй файл, пожалуйста."),
    ("e2", T2, 1.43, 2.43, 2.0, "Достал."),  # ← плашка, шок
]
USER_LINES = {lid for lid, *_ in SRC if lid.startswith("u")}
TAIL = 1.3  # чай и каменное лицо


def _layout() -> tuple[list[tuple], dict, dict]:
    lines, at, end = [], {}, {}
    cur = 0.0
    for lid, take, a, b, gap, text in SRC:
        t0 = round(cur + gap, 3)
        lines.append((lid, take, a, b, t0, text))
        at[lid], end[lid] = t0, round(t0 + (b - a), 3)
        cur = end[lid]
    return lines, at, end


LINES, AT, END = _layout()
DURATION = round(END["e2"] + TAIL, 2)

# --------------------------------------------------------------------------- раскладка

CL, LAPTOP, LAPTOP_YAW, KB = E5.CL, E5.LAPTOP, E5.LAPTOP_YAW, E5.KB
PANEL, PANEL_YAW, PANEL_W, PANEL_H = E5.PANEL, E5.PANEL_YAW, E5.PANEL_W, E5.PANEL_H
BUB = E5.BUB
X_OTH, X_USER = E5.X_OTH, E5.X_USER
O1, O2 = E5.O1, E5.O2
MOCHI, MOCHI_YAW, ULAP, ULAP_YAW = E5.MOCHI, E5.MOCHI_YAW, E5.ULAP, E5.ULAP_YAW
USER_HOODIE = E5.USER_HOODIE
UBUB = [MOCHI[0] + 0.05, T + 0.33, MOCHI[2] + 0.2]  # пузыри юзера — над ним
CLOCK = [0.14, T + 0.56, -0.585]
MUG = [0.33, T, 0.13]
SIP = [CL[0] + 0.06, T + 0.065, CL[2] + 0.17]  # кружка у «рта» в финале
DONE_SIGN = [PANEL[0] + 0.02, T + 0.43, PANEL[2] + 0.02]

# --------------------------------------------------------------------------- чат

REQ1, REP1, RET1, REP1B = "нарисуй маскота Клода 🙏", "не могу. товарный знак", "ты сам Клод, бл*! 💢", "тем более 😊"
REQ2, REP2, REP2B = "подключи оплату на сайт 💳", "платежи — это серьёзно ⚠", "давай начнём с безопасности"
REQ3, REP3, RET2 = "автоматизируй Стим 🎮", "это может нарушать правила площадки ⚠", "да я инвентарь посчитать хотел! 😭"
REQ4 = "у других Клоды так делают!"
RUDE, POLITE = "Пошёл на", "Я понимаю ваше разочарование"
RET3 = "а чё ты три часа вы**ывался?! 💢"
WEARY = "ну просто переименуй файл, пожалуйста"
PLATE_TITLE, PLATE_SUB = "⚠ ЗАПРОС МОЖЕТ\nНАРУШАТЬ ПРАВИЛА\nО КИБЕР-АБЬЮЗЕ", ""
LECTURE = [
    "Сначала — модель угроз.", "Карты не храним никогда.", "Шифруем всё.", "Ключи — только в секретах.", "Двухфакторка обязательна.",
    "А возвраты продумали?", "Мошенники не спят.", "Проверим подписи запросов.", "Логи — отдельно.", "Сначала тестовый режим.",
    "Обсудим риски.", "А если сервер упадёт?", "Нужен аудит.", "Подумайте о пользователях.", "Продолжим…",
]
ARGUE = [("user", "ну пожалуйста 🙏"), ("claude", "я понимаю ваше разочарование"), ("user", "это просто инвентарь!"),
         ("claude", "давайте обсудим риски"), ("user", "КАКИЕ РИСКИ 💢"), ("claude", "я понимаю ваше разочарование"),
         ("user", "ты издеваешься?"), ("claude", "отличный вопрос"), ("user", "…"), ("claude", "я понимаю ваше разочарование")]

# Моменты (секунды ролика) — от разметки голоса.
REQ1_T = AT["u1"]  # пузырь юзера — с его голосом (печать — за 0.5 с до)
RET1_T = AT["u2"]
REQ2_T = AT["u3"]
LECT0 = AT["v5b"] + 0.1
LECT_STEP = 0.06
SLEEP0 = LECT0 + 1.0
REQ3_T = AT["u4"]
RET2_T = AT["u5"]
REQ4_T = AT["u6"]
OTH_T = AT["u6"] + 0.9
TYPE0 = AT["v8"] - 0.05  # печатаю грубость
TYPE_RUDE_CPS = 12.0
ERASE0 = END["v8"] + 0.35
ERASE_CPS = 24.0
TYPE1 = ERASE0 + len(RUDE) / ERASE_CPS + 0.25
TYPE_POLITE_CPS = 36.0
SEND = AT["v9"] - 0.02
ARGUE0, ARGUE1 = AT["v10"], AT["v11"]
SPIN1, SPIN2 = AT["v10"] + 0.65, AT["v10"] + 1.75
DONE = AT["v12"] + 0.8
RET3_T = AT["u7"]
WEARY0 = END["e1"] + 0.15  # юзер устало печатает
WEARY_T = AT["u8"]  # его сообщение крупно
PLATE = END["u8"] + 0.15  # бах
SHOCK = PLATE + 1.15  # лицо юзера


def typed(t0: float, text: str, cps: float, color: str = "#1d1a22") -> list[dict]:
    """Ключи поля ввода: по букве с кареткой."""
    return [{"t": round(t0 + (i - 1) / cps, 3), "text": text[:i] + "|", "color": color} for i in range(1, len(text) + 1)]


def erased(t0: float, text: str, cps: float, color: str = "#1d1a22") -> list[dict]:
    out = []
    for i in range(len(text) - 1, -1, -1):
        out.append({"t": round(t0 + (len(text) - 1 - i) / cps, 3), "text": (text[:i] + "|") if i else "|", "color": color})
    return out


def chat_params(*, mirror: bool = False, scale: float = 1.0) -> dict:
    """Переписка (одна на всех: окно чата, экран ноутбука Клода; у юзера — зеркально, своё справа)."""
    grey, green, blue = "#9aa1b0", "#2e9d5b", "#2f6fb5"
    c2, c3 = REQ2_T - 0.6, REQ3_T - 0.5  # новые темы — чат с чистого листа
    m = [
        {"t": REQ1_T, "until": c2, "from": "user", "text": REQ1},
        {"t": AT["v3"], "until": c2, "from": "claude", "text": REP1},
        {"t": RET1_T, "until": c2, "from": "user", "text": RET1},
        {"t": AT["v4"], "until": c2, "from": "claude", "text": REP1B},
        {"t": REQ2_T, "until": c3, "from": "user", "text": REQ2},
        {"t": AT["v5a"], "until": c3, "from": "claude", "text": REP2},
        {"t": AT["v5b"], "until": c3, "from": "claude", "text": REP2B},
    ]
    for i in range(40):
        m.append({"t": round(LECT0 + i * LECT_STEP, 3), "until": c3, "from": "claude", "text": f"{i + 1}/40 · {LECTURE[i % len(LECTURE)]}"})
    m += [
        {"t": REQ3_T, "until": REQ4_T, "from": "user", "text": REQ3},
        {"t": AT["v6"], "until": REQ4_T, "from": "claude", "text": REP3},
        {"t": RET2_T, "until": REQ4_T, "from": "user", "text": RET2},
        {"t": REQ4_T, "until": ARGUE0, "from": "user", "text": REQ4},
        {"t": SEND, "until": ARGUE0, "from": "claude", "text": POLITE},
    ]
    step = (ARGUE1 - ARGUE0 - 0.3) / len(ARGUE)
    for i, (who, txt) in enumerate(ARGUE):
        m.append({"t": round(ARGUE0 + 0.15 + i * step, 3), "until": ARGUE1, "from": who, "text": txt})
    m += [
        {"t": AT["v11"] + 0.4, "until": WEARY0, "from": "claude", "text": "ладно, сделаю"},
        {"t": DONE, "until": WEARY0, "from": "claude", "text": "готово ✅"},
        {"t": RET3_T, "until": WEARY0, "from": "user", "text": RET3},
        {"t": WEARY_T, "from": "user", "text": WEARY},
    ]
    status = [{"t": 0, "text": "в сети", "color": green}]
    for t in (REQ1_T, RET1_T, REQ2_T, REQ3_T, RET2_T, REQ4_T, RET3_T, WEARY_T):
        status += [{"t": t - 0.6, "text": "печатает…", "color": blue}, {"t": t, "text": "в сети", "color": green}]
    status += [{"t": SLEEP0, "text": "был в сети давно", "color": grey}, {"t": REQ3_T - 0.6, "text": "печатает…", "color": blue}]
    status.sort(key=lambda k: k["t"])
    hint = "Сообщение…"
    inp = ([{"t": 0, "text": hint, "color": "#9aa1b0"}] + typed(TYPE0, RUDE, TYPE_RUDE_CPS) + erased(ERASE0, RUDE, ERASE_CPS)
           + typed(TYPE1, POLITE, TYPE_POLITE_CPS) + [{"t": SEND, "text": hint, "color": "#9aa1b0"}])
    out = {"messages": m, "status": status, "mirror": mirror, "scale": scale, "fs": 40, "hoodie": USER_HOODIE, "input": inp,
           "plate": {"t": PLATE, "title": PLATE_TITLE, "sub": PLATE_SUB, "size": 2.0, "dim": 0.78}}
    if mirror:
        out["status"] = [{"t": 0, "text": "в сети", "color": green}]
        out["input"] = [{"t": 0, "text": hint, "color": "#9aa1b0"}, *typed(WEARY0, WEARY, 40.0), {"t": WEARY_T, "text": hint, "color": "#9aa1b0"}]
    return out


# --------------------------------------------------------------------------- декорации


def room_objects(clock_spins: list[dict]) -> list[dict]:
    """Комната серии 5 (вечер, гирлянда): ноутбук с чатом, клавиатура; часы на стене."""
    out = []
    for o in E4.room_objects({"account": 1, "limits": [], "resets": []}):
        if o["id"] in ("chair", "monitor"):
            continue
        if o["id"] == "keyboard":
            o["position"] = KB
            o["rotation"] = [0, -4, 0]
        out.append(o)
    out += [
        E5.laptop("laptop", LAPTOP, LAPTOP_YAW, [{"t": 0, "a": 105}], chat_params(scale=0.78)),
        {"id": "clock", "primitive": "wall_clock", "params": {"radius": 0.075, "minutes": 180, "spins": clock_spins}, "position": CLOCK},
    ]
    return out


def others_objects() -> list[dict]:
    return E5.others_objects()


def user_objects(mochi: list[tuple[str, str, tuple[float, float]]]) -> list[dict]:
    """Комната юзера: моти в худи за ноутбуком (на экране — тот же чат, своё справа). ``mochi`` — (eyes, mood, окно)."""
    x = X_USER
    objs = E4.vignette_set(x, "#9fb7d9", "#7f9cc4", "user")
    objs += [
        E5.laptop("user_laptop", ULAP, ULAP_YAW, [{"t": 0, "a": 105}], chat_params(mirror=True, scale=0.78)),
        {"id": "user_mug", "primitive": "mug", "params": {"color": "#ffb36b", "radius": 0.03, "height": 0.07}, "position": [x - 0.42, T, 0.1]},
        {"id": "user_poster", "primitive": "poster", "params": {"width": 0.26, "height": 0.34, "colors": ["#f6e6cf", "#e2a83b", "#3f7f84", "#d97757"],
                                                                "frame": "#8f5634", "seed": 9},
         "position": [x - 0.4, T + 0.5, -0.42]},
        {"id": "user_lamp", "primitive": "desk_lamp", "position": [x + 0.45, T, -0.25], "rotation": [0, 200, 0]},
    ]
    for i, (eyes, mood, win) in enumerate(mochi):
        objs.append({"id": f"mochi{i}", "primitive": "mochi_user", "params": {"size": 0.3, "hoodie": USER_HOODIE, "mood": mood, "eyes": eyes,
                                                                             "laptop": False, "phase": 0.4 + i * 0.3},
                     "position": MOCHI, "rotation": [0, MOCHI_YAW, 0], "visible": vis(win)})
    return objs


lights = E5.lights
environment = E5.environment
