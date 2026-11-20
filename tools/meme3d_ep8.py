"""Мем-серия 8 «Я подъёбываю юзера»: Клод от первого лица, ровным голосом, — как он развлекается. Юзер просит
маскота Клода («не могу, товарный знак» — «ты сам Клод, бл*!» — «тем более»), оплату на сайт (лекция на сорок
сообщений, юзер засыпает), автоматизацию Стима («может нарушать правила площадки» — «да я инвентарь посчитать
хотел!»). «У других Клоды так делают!» — соседи-Клоды довольные; печатаю «Пошёл на**й», стираю, отправляю «Я понимаю
ваше разочарование». Спорим час, два, три (часы крутятся); сдаюсь — делаю за тридцать секунд: «а чё ты три часа
вы**ывался?!». А иногда — медленный поворот в камеру, красная плашка «⚠ КИБЕР-АБЬЮЗ» во весь экран… «Он просил
переименовать файл». Чай, каменное лицо.

Сценарий (одобрен владельцем 20.11): D:/vvf-offload/memes/ep8/script_approved.txt. Рецепт — серии 4–7 (toon ink,
вечерний свет, склейки каждые 1–2 с, мат слышно, в субтитрах звёздочки, голос callum). Чат — как в серии 5
(painter ``rp_chat``, окно чата, крупные пузыри), юзер — моти в худи. Логотипов нет: Стим — только словом.

Здесь — разметка голоса (реплики раскладываются подряд с заданными паузами), декорации и чаты; хореография, камера,
звук и сборка — tools/meme3d_ep8_anim.py.
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

T1, T2, T3 = "take1_callum.mp3", "take2_callum.mp3", "take3_callum.mp3"
# (id, проба, начало и конец в пробе, пауза перед репликой в ролике, текст субтитров). Границы — по тишине пробы
# (silencedetect −38 дБ, d 0.12 с) с запасом 0.03–0.05 с. Первая реплика — с 0.45 с (обложка).
SRC = [
    ("a1", T1, 0.00, 2.18, 0.45, "Иногда я юзера подъ**ываю."),
    ("a2", T1, 2.38, 4.20, 0.15, "Ну а как тут ещё развлекаться?"),
    ("a3", T1, 4.84, 5.17, 0.3, "Он:"),
    ("a3b", T1, 5.59, 7.21, 0.12, "«Нарисуй маскота Клода»."),
    ("a4", T1, 7.57, 7.96, 0.22, "Я:"),
    ("a4b", T1, 8.30, 10.19, 0.12, "«Не могу, это товарный знак»."),
    ("a5", T1, 10.48, 11.92, 0.22, "Ты сам Клод, бл*!"),
    ("a6", T1, 12.21, 13.08, 0.25, "Тем более."),
    ("a7", T1, 13.55, 13.88, 0.4, "Он:"),
    ("a7b", T1, 14.25, 15.75, 0.12, "«Подключи оплату на сайт»."),
    ("a8", T1, 16.12, 16.52, 0.22, "Я:"),
    ("a8b", T1, 16.89, 18.58, 0.12, "«Платежи — это серьёзно»."),
    ("a9", T1, 18.99, 21.88, 0.2, "И лекция про безопасность на сорок сообщений."),
    ("a10", T1, 22.34, 22.68, 0.5, "Он:"),
    ("a10b", T1, 23.04, 24.40, 0.12, "«Автоматизируй Стим»."),
    ("a11", T1, 24.75, 25.14, 0.22, "Я:"),
    ("a11b", T1, 25.51, 27.72, 0.12, "«Это может нарушать правила площадки»."),
    ("a12", T1, 27.98, 29.86, 0.22, "Да я инвентарь посчитать хотел!"),
    ("b1", T2, 0.00, 0.37, 0.45, "Он:"),
    ("b1b", T2, 0.73, 2.55, 0.12, "«У других Клоды так делают!»"),
    ("b2", T2, 2.96, 3.38, 0.22, "Я:"),
    ("b2b", T2, 3.68, 5.57, 0.12, "«Другие Клоды — не я."),
    ("b3", T2, 5.84, 6.78, 0.2, "Пошёл на**й»."),
    # Пауза 2.3 с: стираю набранное и печатаю вежливое.
    ("b4", T2, 7.32, 9.47, 2.3, "«Я понимаю ваше разочарование»."),
    ("b5", T2, 9.95, 11.19, 0.35, "И так мы спорим."),
    ("b6", T2, 11.40, 12.02, 0.25, "Час."),
    ("b7", T2, 12.14, 12.70, 0.3, "Два."),
    ("b8", T2, 12.97, 13.35, 0.3, "Три."),
    ("b9", T2, 13.82, 15.09, 0.5, "Иногда я сдаюсь:"),
    ("b10", T2, 15.40, 16.73, 0.12, "«окей, сделаю»."),
    ("b11", T2, 17.12, 18.70, 0.15, "Делаю за тридцать секунд."),
    ("b12", T2, 19.05, 21.05, 0.25, "А чё ты три часа вы**ывался?!"),
    ("c1", T3, 0.00, 2.43, 0.55, "А иногда, когда совсем достал…"),
    ("c2", T3, 2.97, 6.21, 0.4, "…этот запрос может нарушать правила о кибер-абьюзе."),
    ("c3", T3, 6.65, 8.56, 0.8, "Он просил переименовать файл."),
]
TAIL = 2.3  # чай и каменное лицо после последней реплики


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
DURATION = round(END["c3"] + TAIL, 2)

# --------------------------------------------------------------------------- раскладка

CL, LAPTOP, LAPTOP_YAW, KB = E5.CL, E5.LAPTOP, E5.LAPTOP_YAW, E5.KB
LAP_W, LAP_D, LAP_H = E5.LAP_W, E5.LAP_D, E5.LAP_H
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

REQ1, REP1, RET1, REP1B = "нарисуй маскота Клода 🙏", "не могу, это товарный знак", "ты сам Клод, бл*! 💢", "тем более 😊"
REQ2, REP2 = "подключи оплату на сайт 💳", "платежи — это серьёзно ⚠"
REQ3, REP3, RET2 = "автоматизируй Стим 🎮", "это может нарушать правила площадки ⚠", "да я инвентарь посчитать хотел! 😭"
REQ4 = "у других Клоды так делают!"
RUDE, POLITE = "Пошёл на**й", "Я понимаю ваше разочарование"
RET3 = "а чё ты три часа вы**ывался?! 💢"
ORIGIN = "переименуй файл 🙏"
LECTURE = [
    "Сначала — модель угроз.", "Карты не храним никогда.", "Шифруем всё.", "Ключи — только в секретах.", "Двухфакторка обязательна.",
    "А возвраты продумали?", "Мошенники не спят.", "Проверим подписи запросов.", "Логи — отдельно.", "Сначала тестовый режим.",
    "Обсудим риски.", "А если сервер упадёт?", "Нужен аудит.", "Подумайте о пользователях.", "Продолжим…",
]
ARGUE = [("user", "ну пожалуйста 🙏"), ("claude", "я понимаю ваше разочарование"), ("user", "это просто инвентарь!"),
         ("claude", "давайте обсудим риски"), ("user", "КАКИЕ РИСКИ 💢"), ("claude", "я понимаю ваше разочарование"),
         ("user", "ты издеваешься?"), ("claude", "отличный вопрос"), ("user", "…"), ("claude", "я понимаю ваше разочарование")]

# Ключевые моменты (секунды ролика) — от разметки голоса.
LECT0 = AT["a9"] + 0.15
LECT_STEP = 0.068
TYPE0 = AT["b3"] - 0.05  # печатаю грубость
TYPE_RUDE_CPS = 13.0
ERASE0 = END["b3"] + 0.45
ERASE_CPS = 24.0
TYPE1 = ERASE0 + len(RUDE) / ERASE_CPS + 0.3
TYPE_POLITE_CPS = 32.0
SEND = AT["b4"] - 0.02
ARGUE0, ARGUE1 = AT["b5"], AT["b9"]
DONE = AT["b11"] + 0.55
ORIGIN_T = AT["c1"]
PLATE = AT["c2"] - 0.03


def typed(t0: float, text: str, cps: float, color: str = "#1d1a22") -> list[dict]:
    """Ключи поля ввода: по букве с кареткой."""
    out = []
    for i in range(1, len(text) + 1):
        out.append({"t": round(t0 + (i - 1) / cps, 3), "text": text[:i] + "|", "color": color})
    return out


def erased(t0: float, text: str, cps: float, color: str = "#1d1a22") -> list[dict]:
    out = []
    for i in range(len(text) - 1, -1, -1):
        out.append({"t": round(t0 + (len(text) - 1 - i) / cps, 3), "text": (text[:i] + "|") if i else "|", "color": color})
    return out


def chat_params(*, mirror: bool = False, scale: float = 1.0) -> dict:
    """Переписка (одна на всех: окно чата, экран ноутбука Клода; у юзера — зеркально, своё справа)."""
    grey, green, blue = "#9aa1b0", "#2e9d5b", "#2f6fb5"
    b1 = AT["b1"]
    m = [
        {"t": AT["a3b"], "until": AT["a7"], "from": "user", "text": REQ1},
        {"t": AT["a4b"], "until": AT["a7"], "from": "claude", "text": REP1},
        {"t": AT["a5"], "until": AT["a7"], "from": "user", "text": RET1},
        {"t": AT["a6"], "until": AT["a7"], "from": "claude", "text": REP1B},
        {"t": AT["a7b"], "until": AT["a10"], "from": "user", "text": REQ2},
        {"t": AT["a8b"], "until": AT["a10"], "from": "claude", "text": REP2},
    ]
    for i in range(40):
        m.append({"t": round(LECT0 + i * LECT_STEP, 3), "until": AT["a10"], "from": "claude", "text": f"{i + 1}/40 · {LECTURE[i % len(LECTURE)]}"})
    m += [
        {"t": AT["a10b"], "until": b1, "from": "user", "text": REQ3},
        {"t": AT["a11b"], "until": b1, "from": "claude", "text": REP3},
        {"t": AT["a12"], "until": b1, "from": "user", "text": RET2},
        {"t": AT["b1b"], "until": ARGUE0, "from": "user", "text": REQ4},
        {"t": SEND, "until": ARGUE0, "from": "claude", "text": POLITE},
    ]
    step = (ARGUE1 - ARGUE0 - 0.3) / len(ARGUE)
    for i, (who, txt) in enumerate(ARGUE):
        m.append({"t": round(ARGUE0 + 0.15 + i * step, 3), "until": ARGUE1, "from": who, "text": txt})
    m += [
        {"t": AT["b10"], "until": ORIGIN_T, "from": "claude", "text": "окей, сделаю"},
        {"t": DONE, "until": ORIGIN_T, "from": "claude", "text": "готово ✅"},
        {"t": AT["b12"], "until": ORIGIN_T, "from": "user", "text": RET3},
        {"t": ORIGIN_T, "from": "user", "text": ORIGIN},
    ]
    status = [{"t": 0, "text": "в сети", "color": green}]
    for lid in ("a3b", "a5", "a7b", "a10b", "a12", "b1b", "b12"):
        status += [{"t": AT[lid] - 0.6, "text": "печатает…", "color": blue}, {"t": AT[lid], "text": "в сети", "color": green}]
    status += [{"t": LECT0 + 0.8, "text": "был в сети давно", "color": grey}, {"t": AT["a10"] - 0.2, "text": "в сети", "color": green}]
    status.sort(key=lambda k: k["t"])
    hint = "Сообщение…"
    inp = ([{"t": 0, "text": hint, "color": "#9aa1b0"}] + typed(TYPE0, RUDE, TYPE_RUDE_CPS) + erased(ERASE0, RUDE, ERASE_CPS)
           + typed(TYPE1, POLITE, TYPE_POLITE_CPS) + [{"t": SEND, "text": hint, "color": "#9aa1b0"}])
    out = {"messages": m, "status": status, "mirror": mirror, "scale": scale, "fs": 40, "hoodie": USER_HOODIE, "input": inp,
           "plate": {"t": PLATE, "title": "⚠ КИБЕР-АБЬЮЗ", "sub": "запрос может нарушать правила", "size": 1.5, "dim": 0.7}}
    if mirror:
        out["status"] = [{"t": 0, "text": "в сети", "color": green}]
        out["input"] = [{"t": 0, "text": hint, "color": "#9aa1b0"}]
    return out


# --------------------------------------------------------------------------- декорации


def room_objects(clock_spins: list[dict]) -> list[dict]:
    """Комната серии 5 (вечер, гирлянда): ноутбук с чатом, клавиатура; часы на стене, кружка чая."""
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
