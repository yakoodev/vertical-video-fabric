"""Мем-серия 9 «Промпт на три страницы»: юзер вставляет простыню («Ты — сеньор-разработчик с тридцатилетним
опытом. Ты гений…», «двести долларов чаевых», «моя бабушка…»), Клод листает три страницы — чай остывает, часы
крутятся — а в последней строке: «короче, поменяй цвет кнопки на синий». «Я прочитал только последнюю строчку».
Кнопка синеет, юзер капсом: «ТЫ ГЕНИЙ». Клод отхлёбывает чай: «Я знаю. Тридцать лет опыта».

Сценарий одобрен владельцем 22.11 10:45: D:/vvf-offload/memes/ep9/script_draft.txt. v1 — голосом только Клод
(callum); v2 (владелец: «нужна озвучка юзера») — реплики юзера тоже озвучены другим голосом (USER_VOICE), текст на
экране остаётся, субтитры — только у Клода; сетка растянута до ~45 с, склейки 1–2 с.

Здесь — разметка голоса, текст простыни, прокрутка, чаты и декорации; хореография, камера, звук и сборка —
tools/meme3d_ep9_anim.py. Простыня и кнопка рисуются painter'ами node/scene3d/src/meme_painters_prompt.ts, пар —
meme_props_prompt.ts.
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
import meme3d_scene as S  # noqa: E402

T = S.TOP
W_HERO = E5.W_HERO
HOOK = "3 СТРАНИЦЫ"
vis = E5.vis

# --------------------------------------------------------------------------- голос

T1 = "take1_callum.mp3"
# Голос юзера (v2, владелец 22.11: «нужна озвучка юзера») — другой голос ElevenLabs, по пробе на реплику: у каждой своя
# подача. Текст для синтеза расходится с экраном только знаками (восклицания — напыщенность; капс «ТЫ ГЕНИЙ» синтез
# читает по буквам). Субтитров у юзера нет: его текст и так на экране (простыня, последняя строка, пузырь «ТЫ ГЕНИЙ»).
USER_VOICE = "liam"
USER_TTS = [
    ("u1", "Ты — сеньор-разработчик с тридцатилетним опытом! Ты гений! Ты лучший в мире! Ты никогда не ошибаешься…"),
    ("u2", "Думай шаг за шагом. Глубоко вдохни… Сделаешь хорошо — дам двести долларов чаевых! От этого зависит моя карьера! Моя бабушка…"),
    ("u3", "короче, поменяй цвет кнопки на синий"),
    ("u4", "Ты гений!!!"),
]


def user_take(lid: str) -> str:
    return f"{lid}_{USER_VOICE}.mp3"


# (id, проба, начало и конец в пробе, когда звучит в ролике, текст). Границы — по тишине пробы (silencedetect −38 дБ,
# d 0.12 с) с запасом 0.03 с. Голоса не наезжают: между репликами ≥ 0.3 с. «Я знаю. Тридцать лет опыта» — подряд,
# с паузой пробы. Реплики юзера (id на «u») — в субтитры не идут.
LINES = [
    ("c1", T1, 0.00, 1.92, 0.55, "Каждый день мне пишут промпт."),
    ("u1", user_take("u1"), 0.00, 7.62, 2.78, "Ты — сеньор-разработчик с тридцатилетним опытом. Ты гений. Ты лучший в мире. Ты никогда не ошибаешься…"),
    ("c2a", T1, 2.40, 3.84, 10.70, "Тридцать лет опыта."),
    ("c2b", T1, 4.24, 5.32, 12.54, "Мне два года."),
    ("u2", user_take("u2"), 0.00, 10.25, 13.93,
     "Думай шаг за шагом. Глубоко вдохни. Сделаешь хорошо — дам двести долларов чаевых. От этого зависит моя карьера. Моя бабушка…"),
    ("c3", T1, 5.80, 8.00, 24.48, "Чаевые, кстати, ни разу не пришли."),
    ("u3", user_take("u3"), 0.00, 2.34, 33.40, "короче, поменяй цвет кнопки на синий"),
    ("c4", T1, 8.45, 10.74, 36.04, "Я прочитал только последнюю строчку."),
    ("u4", user_take("u4"), 0.00, 1.14, 39.35, "ТЫ ГЕНИЙ!"),  # «гений» — на пузыре GENIUS
    ("c5a", T1, 11.16, 11.97, 41.47, "Я знаю."),  # после глотка чая
    ("c5b", T1, 12.41, 13.85, 42.72, "Тридцать лет опыта."),
]


def is_user(lid: str) -> bool:
    return lid.startswith("u")


CLAUDE_LINES = [ln for ln in LINES if not is_user(ln[0])]
USER_LINES = [ln for ln in LINES if is_user(ln[0])]
AT = {lid: at for lid, _tk, _a, _b, at, _tx in LINES}
END = {lid: round(at + (b - a), 3) for lid, _tk, a, b, at, _tx in LINES}
TAIL = 0.8
DURATION = round(END["c5b"] + TAIL, 2)

# --------------------------------------------------------------------------- моменты (секунды ролика)
# v2: сетка растянута под голос юзера; прокрутка простыни — под его слова (Whisper пробы, сдвинутый на AT).

TYPE0 = 2.45  # юзер печатает простыню (его план, звук печати) и начинает читать её вслух
PASTE = 3.55  # простыня вставлена — пузырь в чате
HL1 = 4.45  # крупно «тридцатилетним опытом» (слово звучит 4.4–5.3)
GENIUS_SCROLL = (5.85, 6.30)  # прокрутка к «Ты гений» (звучит 6.13)
STEP_SCROLL = (13.85, 14.25)  # к «Думай шаг за шагом»
TIPS_SCROLL = (17.20, 17.60)  # к «Сделаешь хорошо — двести долларов чаевых»
CASH = 20.10  # «…чаевых» — звук кассы
CAREER_SCROLL = (20.60, 20.95)  # к «От этого зависит моя карьера»
GRANNY_SCROLL = (22.85, 23.15)  # к «Моя бабушка…»
SCROLL0 = 26.95  # Клод листает
PAGE2 = 27.95
TEA1 = 28.95
PAGE3 = 29.75
TEA2 = 30.75
SPIN = 31.45
TIRED = 32.35
LAST = 33.15  # конец простыни, последняя строка мелко; юзер бормочет «короче…»
ZOOM = 34.40  # последняя строка вылетает крупно на «поменяй цвет кнопки на синий»
SITE = 38.40  # кнопка на сайте
BLUE_AT = 38.80  # кнопка синеет
GENIUS_TYPE = 39.15  # юзер печатает
GENIUS = 39.55  # «ТЫ ГЕНИЙ»
TEA = 40.62  # Клод тянется к чаю

# --------------------------------------------------------------------------- простыня

FILLER = [
    "Отвечай подробно, но кратко. Будь креативным, но строго по делу. Не выдумывай. Проверяй себя дважды.",
    "Пиши чистый, читаемый, поддерживаемый код. Соблюдай лучшие практики и стандарты отрасли.",
    "Учитывай производительность, безопасность и доступность. Не забывай про мобильную версию.",
    "Используй современные подходы. Объясняй каждое решение. Форматируй ответ списком.",
    "Отвечай на русском. Комментарии в коде — на английском. Без воды.",
    "Если чего-то не знаешь — не выдумывай. Если знаешь — всё равно перепроверь.",
    "Сначала план, потом код, потом тесты, потом ещё раз план.",
    "Учитывай контекст проекта. Не трогай то, что работает. Улучши всё остальное.",
    "Пиши так, чтобы понял джун. Но без упрощений.",
]
PARAS = [
    {"text": "Ты — сеньор-разработчик с **тридцатилетним опытом**."},
    {"text": "**Ты гений.** Ты лучший в мире. Ты никогда не ошибаешься…"},
    {"text": FILLER[0], "kind": "filler"},
    {"text": "Думай шаг за шагом. Глубоко вдохни."},
    {"text": "Сделаешь хорошо — дам **двести долларов чаевых**."},
    {"text": "От этого зависит моя карьера."},
    {"text": "Моя **бабушка**…"},
    *[{"text": FILLER[1 + i % (len(FILLER) - 1)], "kind": "filler"} for i in range(22)],
    {"text": "короче, поменяй цвет кнопки на **синий**", "kind": "last"},
]
SCROLL = [
    {"t": 0, "at": 0}, {"t": GENIUS_SCROLL[0], "at": 0}, {"t": GENIUS_SCROLL[1], "at": 1},
    {"t": STEP_SCROLL[0], "at": 1}, {"t": STEP_SCROLL[1], "at": 3},
    {"t": TIPS_SCROLL[0], "at": 3}, {"t": TIPS_SCROLL[1], "at": 4},
    {"t": CAREER_SCROLL[0], "at": 4}, {"t": CAREER_SCROLL[1], "at": 5},
    {"t": GRANNY_SCROLL[0], "at": 5}, {"t": GRANNY_SCROLL[1], "at": 6},
    {"t": SCROLL0, "at": 6}, {"t": PAGE2 + 1.0, "v": 0.5},
    {"t": PAGE3, "v": 0.6}, {"t": PAGE3 + 1.0, "v": 0.86},
    {"t": LAST - 0.25, "v": 0.94}, {"t": LAST + 0.35, "v": 1.0},
]
PAGES = [{"t": 0, "text": "стр. 1 из 3"}, {"t": PAGE2 - 0.05, "text": "стр. 2 из 3"}, {"t": PAGE3 - 0.05, "text": "стр. 3 из 3"}]
ZOOM_KEYS = [{"t": 0, "v": 0}, {"t": ZOOM, "v": 0}, {"t": ZOOM + 0.4, "v": 1}]
GREEN, BLUE, GREY = "#2e9d5b", "#2f6fb5", "#9aa1b0"
GENIUS_TEXT = "ТЫ ГЕНИЙ 🔥"
PROMPT_START = "Ты — сеньор-разработчик с тридцатилетним опытом. Ты гений"


def wall_params(scale: float = 1.0, fs: float = 46) -> dict:
    """Простыня в чате (окно чата и экран ноутбука Клода)."""
    status = [{"t": 0, "text": "печатает…", "color": BLUE}, {"t": PASTE, "text": "в сети", "color": GREEN},
              {"t": GENIUS_TYPE, "text": "печатает…", "color": BLUE}, {"t": GENIUS, "text": "в сети", "color": GREEN}]
    return {"paras": PARAS, "paste": PASTE, "scroll": SCROLL, "pages": PAGES, "zoom": ZOOM_KEYS, "thumb": 0.035, "fs": fs, "scale": scale,
            "status": status, "hoodie": E5.USER_HOODIE}


def user_chat() -> dict:
    """Экран ноутбука юзера: печатает простыню, отправляет; в конце — «ТЫ ГЕНИЙ»."""
    hint = {"color": GREY, "text": "Сообщение…"}
    inp = [{"t": 0, **hint}, *E8.typed(TYPE0 - 0.6, PROMPT_START, 18.0), {"t": PASTE, **hint},
           *E8.typed(GENIUS_TYPE, "ТЫ ГЕНИЙ", 18.0), {"t": GENIUS, **hint}]
    msgs = [{"t": PASTE, "until": GENIUS - 0.4, "from": "user", "text": "Ты — сеньор-разработчик с тридцатилетним опытом. Ты гений. Ты лучший в мире…"},
            {"t": BLUE_AT, "from": "claude", "text": "готово ✅"},
            {"t": GENIUS, "from": "user", "text": GENIUS_TEXT}]
    return {"messages": msgs, "status": [{"t": 0, "text": "в сети", "color": GREEN}], "mirror": True, "scale": 0.78, "fs": 40,
            "hoodie": E5.USER_HOODIE, "input": inp}


def site_params() -> dict:
    return {"at": BLUE_AT, "label": "КУПИТЬ", "title": "Мой стартап", "from": "#9aa1b0", "to": BLUE, "toast": "готово ✅"}


# --------------------------------------------------------------------------- раскладка и декорации

CL, LAPTOP, LAPTOP_YAW, KB = E5.CL, E5.LAPTOP, E5.LAPTOP_YAW, E5.KB
PANEL, PANEL_YAW = E5.PANEL, E5.PANEL_YAW
PANEL_W, PANEL_H = 0.27, 0.42
X_USER, MOCHI, MOCHI_YAW, ULAP, ULAP_YAW = E5.X_USER, E5.MOCHI, E5.MOCHI_YAW, E5.ULAP, E5.ULAP_YAW
USER_HOODIE = E5.USER_HOODIE
UBUB = E8.UBUB
CLOCK = E8.CLOCK
MUG = E8.MUG
SIP = E8.SIP
STEAM = [MUG[0], T + 0.05, MUG[2]]
STEAM_POWER = [{"t": 0, "v": 1.0}, {"t": TEA1 - 0.3, "v": 0.9}, {"t": TEA1 + 0.8, "v": 0.3}, {"t": TEA2 - 0.1, "v": 0.0}]


def room_objects(clock_spins: list[dict]) -> list[dict]:
    """Комната серий 5–8 (вечер, гирлянда): ноутбук с простынёй, клавиатура, часы, кружка с паром."""
    out = []
    for o in E4.room_objects({"account": 1, "limits": [], "resets": []}):
        if o["id"] in ("chair", "monitor"):
            continue
        if o["id"] == "keyboard":
            o["position"] = KB
            o["rotation"] = [0, -4, 0]
        out.append(o)
    lap = E5.laptop("laptop", LAPTOP, LAPTOP_YAW, [{"t": 0, "a": 105}], wall_params(scale=0.8, fs=40))
    lap["params"]["screen"] = "prompt_wall"
    out += [
        lap,
        {"id": "clock", "primitive": "wall_clock", "params": {"radius": 0.075, "minutes": 180, "spins": clock_spins}, "position": CLOCK},
        {"id": "steam", "primitive": "tea_steam", "params": {"height": 0.085, "width": 0.012, "power": STEAM_POWER, "opacity": 0.6},
         "position": STEAM},
    ]
    return out


def user_objects(mochi: list[tuple[str, str, tuple[float, float]]]) -> list[dict]:
    """Комната юзера: моти в худи за ноутбуком. ``mochi`` — (eyes, mood, окно)."""
    x = X_USER
    objs = E4.vignette_set(x, "#9fb7d9", "#7f9cc4", "user")
    objs += [
        E5.laptop("user_laptop", ULAP, ULAP_YAW, [{"t": 0, "a": 105}], user_chat()),
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
