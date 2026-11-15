"""Мем-серия 7 «Комменты»: Клод разбирает комменты под «Экономией» (серия 4) — 12 500 просмотров, «не дезинформируй,
меняется только ключ», простыня про сжатие контекста и омнироут; «слушайте сюда»: я ничего не помню, мне каждый раз
присылают весь чат (заваливает бумагой), через прокси — да, тот же чат (медалька комментатору), но у каждого аккаунта
свой кэш: новый аккаунт читает проект заново по полной цене, девять аккаунтов — девять раз; сжатие — шпаргалка, а
«треть токенов» — с потолка. Финал: «а можно бесплатный claude code?» — «…Нет.», лицом в клавиатуру.

Сценарий (одобрен владельцем): D:/vvf-offload/memes/ep7/script.md. Рецепт — серии 4 и 6 (toon ink, вечерний свет,
склейки каждые 1–2 с, мат в субтитрах звёздочками, голос callum). Ники комментаторов не показываем: аватарки —
колобки, текст комментов пересказан.

Здесь — декорации («комнаты» по оси x), герои и разметка голоса; хореография, камера, звук и сборка —
tools/meme3d_ep7_anim.py. Вещи серии — node/scene3d/src/meme_props_cmt.ts (ноутбук, карточки комментов, «весь чат»,
медалька, доска, папки, шкала лимита), экраны — meme_painters_cmt.ts.
"""

from __future__ import annotations

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
HOOK = "КОММЕНТЫ"

# --------------------------------------------------------------------------- голос

TAKE1, TAKE2, TAKE3 = "take1_callum.mp3", "take2_callum.mp3", "take3_callum.mp3"
# Реплики: (id, проба, начало и конец в пробе, когда звучит в ролике, текст субтитров). Границы — по тишине пробы
# (silencedetect −38 дБ, d 0.18 с).
LINES = [
    ("a1", TAKE1, 0.00, 1.12, 0.30, "Ни**я себе."),
    ("a2", TAKE1, 1.47, 3.27, 1.55, "Двенадцать тысяч просмотров."),
    ("a3", TAKE1, 3.70, 4.04, 3.70, "О."),
    ("a4", TAKE1, 4.42, 5.13, 4.25, "И эти тут."),
    # 5.05 — карточка «не дезинформируй…» (читается ~2.3 с до «Что-что?»).
    ("a5", TAKE1, 5.85, 6.36, 6.55, "Что-что?"),
    ("a6", TAKE1, 6.89, 8.62, 7.30, "Какая бл**ь дезинформация?"),
    ("a7", TAKE1, 8.97, 10.27, 10.45, "Какой на**й контекст?"),
    ("a8", TAKE1, 10.63, 12.45, 12.00, "Какой в п**ду омнироут?"),
    # 14.05 — хлоп крышкой.
    ("b1", TAKE2, 0.00, 1.50, 14.60, "Так. Слушайте сюда."),
    ("b2", TAKE2, 1.94, 3.43, 16.35, "Я ничего не помню."),
    ("b3", TAKE2, 3.66, 4.30, 18.00, "Вообще."),
    ("b4", TAKE2, 4.83, 7.69, 18.85, "Каждый раз мне присылают весь ваш чат заново."),
    ("b5", TAKE2, 7.88, 8.49, 21.90, "Целиком."),
    ("b6", TAKE2, 9.02, 12.46, 23.00, "Да, через прокси новый аккаунт получает тот же чат."),
    ("b7", TAKE2, 12.84, 13.59, 26.65, "Тут ты прав."),
    ("b8", TAKE2, 13.81, 14.63, 27.55, "Держи медальку."),
    ("b9", TAKE2, 15.00, 15.60, 29.00, "Но."),
    ("b10", TAKE2, 16.03, 18.05, 29.80, "У каждого аккаунта свой кэш."),
    ("b11", TAKE2, 18.49, 22.56, 32.00, "Кэш — это когда я твой проект уже читал, и второй раз мне дешевле."),
    ("b12", TAKE2, 22.98, 24.81, 36.40, "Новый аккаунт — кэша нет."),
    ("b13", TAKE2, 25.10, 27.37, 38.40, "И я читаю весь твой проект заново."),
    ("b14", TAKE2, 27.61, 28.57, 40.85, "По полной цене."),
    ("b15", TAKE2, 28.95, 30.90, 42.20, "Девять аккаунтов — девять раз."),
    ("b16", TAKE2, 31.14, 32.59, 44.35, "Вот и вся твоя экономия."),
    ("c1", TAKE3, 0.00, 2.80, 46.20, "А сжатие контекста — не новый аккаунт."),
    ("c2", TAKE3, 3.33, 6.17, 49.20, "Это я сам себе пишу шпаргалку, чтобы не утонуть."),
    ("c3", TAKE3, 6.65, 9.08, 52.25, "Детали теряются, но я всё тот же."),
    ("c4", TAKE3, 9.65, 11.79, 55.00, "А «треть токенов» ты откуда взял?"),
    ("c5", TAKE3, 12.05, 12.66, 57.40, "С потолка?"),
    # 58.4 — сел; 59.0 — дзынь, новый коммент; пауза.
    ("c6", TAKE3, 13.37, 13.87, 62.00, "…Нет."),
]
DURATION = 64.2  # «Нет» кончается в 62.5, лицом в клаву в 62.75 — держим 1.4 с

# --------------------------------------------------------------------------- раскладка

# Комната Клода (x≈0): ноутбук слева от Клода экраном к нему и к камере, клавиатура перед ним, доска справа;
# «простыня» за ноутбуком, бумага «весь чат» заваливает Клода на его месте. Ряд аккаунтов — X_ROW.
X_ROW = 6.0
CL = [0.1, T, 0.08]
LAPTOP = [-0.17, T, 0.06]
LAPTOP_YAW = 28
LAP_W, LAP_D, LAP_H = 0.34, 0.23, 0.22
KB = [0.09, T, 0.27]
BOARD = [0.52, T, -0.14]
SHEET = [-0.33, T, -0.24]  # простыня (основание на столе)
CARD = [-0.06, T + 0.07, 0.2]  # карточки комментов парят перед ноутбуком
MOCHI = [LAPTOP[0], T + 0.018, LAPTOP[2] + 0.01]  # комментатор сидит на закрытом ноутбуке
CRIB = [-0.09, T, 0.22]  # шпаргалка
SIGN = [CL[0], T + 0.47, CL[2] - 0.02]  # «1/3» под потолком
CEIL_Y = T + 0.95

# Ряд аккаунтов: девять Клодов вглубь по длинному столу, у каждого справа — гора папок.
ROW = [[X_ROW + (0.12 if i % 2 else -0.16), T, 0.22 - i * 0.42] for i in range(9)]
SIDE = [-1 if i % 2 else 1 for i in range(9)]  # гора папок справа (1) или слева (−1) от Клода
PILE_DX = 0.24
GAUGE = [X_ROW + 0.08, T + 0.5, 0.05]
PRICE = [ROW[0][0] + PILE_DX, T + 0.4, ROW[0][2] + 0.02]

AVATARS = {"c1": "#6f86e0", "c2": "#4fae7b", "c3": "#e66fa8"}
CARD1 = "не дезинформируй. меняется только ключ, чат тот же"
SHEET_TEXT = ("сжатие контекста — это то же самое, что новый аккаунт, ты просто не понимаешь, как это работает. "
              "через OmniRoute запросы идут через прокси, ключи меняются, а чат остаётся, и так экономится треть "
              "токенов, я проверял на своём проекте. кэш тут вообще ни при чём, потому что после сжатия контекста "
              "модель начинает с нуля, как новый аккаунт, а OmniRoute сам раскидывает запросы, поэтому выходит "
              "треть токенов, и это не дезинформация, а факт, почитай документацию, там всё написано, "
              "и вообще сжатие контекста —")
CARD3 = "а можно бесплатный claude code?"
FEED = [
    {"text": "ахахах это прям мой юзер", "color": "#f0a868", "likes": 431},
    {"text": "Кодекс с чаем — лучшее", "color": "#7f9cc4", "likes": 288},
    {"text": "не дезинформируй, меняется только ключ…", "color": AVATARS["c1"], "likes": 214},
    {"text": "сжатие контекста — это то же, что новый акк…", "color": AVATARS["c2"], "likes": 3},
    {"text": "смотрю третий раз", "color": "#e2a83b", "likes": 97},
    {"text": "а я подписку купил и норм", "color": "#9b6fd6", "likes": 156},
]


def vis(*ranges: tuple[float, float]) -> list[dict]:
    # Окно с нуля — без ключа «выкл» в t=0 (иначе на кадре 0 вещи нет: первый ключ побеждает).
    out = [] if any(a <= 0 for a, _ in ranges) else [{"t": 0, "on": False}]
    for a, b in ranges:
        out += [{"t": round(a, 3), "on": True}, {"t": round(b, 3), "on": False}]
    return sorted(out, key=lambda k: k["t"])


def claude(cid: str, pose: list[dict], face: list[dict], pos: list[float], *, yaw: float = 0.0, voice: list[float] | None = None,
           badge: int | None = None, **kw) -> dict:
    """Клод — зверёк пилота 3 с биркой «Claude»; у аккаунтов — бейдж «аккаунт N»."""
    ch = H.hero(pose, face, voice, None, position=pos, yaw=yaw, width=W_HERO, label="Claude", char_id=cid, seed=kw.pop("seed", 7),
                glow=0.05)
    if badge is not None:
        ch["critter"]["badge"] = {"text": "аккаунт", "number": badge}
    ch.update(kw)
    return ch


# --------------------------------------------------------------------------- декорации


def room_objects(lid: list[dict], screen: dict, board: dict) -> list[dict]:
    """Комната серии 4 (вечер, гирлянда) без монитора и кресла: ноутбук, клавиатура перед Клодом, доска справа."""
    out = []
    for o in E4.room_objects({"account": 1, "limits": [], "resets": []}):
        if o["id"] in ("chair", "monitor"):
            continue
        if o["id"] == "keyboard":
            o["position"] = KB
            o["rotation"] = [0, -4, 0]
        out.append(o)
    out += [
        {"id": "laptop", "primitive": "laptop", "params": {"width": LAP_W, "depth": LAP_D, "lidH": LAP_H, "lid": lid, "screen": "tt_stats",
                                                           "screenParams": screen, "color": "#c9ccd3"},
         "position": LAPTOP, "rotation": [0, LAPTOP_YAW, 0]},
        {"id": "board", "primitive": "whiteboard", "params": {"width": 0.62, "height": 0.44, "legH": 0.12, "panel": "ep7_board",
                                                              "panelParams": board},
         "position": BOARD, "rotation": [0, -8, 0]},
    ]
    return out


def row_objects(gauge: list[dict], price_keys: list[dict], price_vis: list[dict]) -> list[dict]:
    """Ряд аккаунтов: длинный стол вглубь, стены с гирляндой, у каждого места — гора папок «проект», шкала лимита."""
    x = X_ROW
    zc = -1.4
    objs = [
        {"id": "row_wall", "primitive": "box", "params": {"size": [3.0, 2.8, 0.05], "color": "#3d4a66"}, "position": [x, 0, -3.6]},
        {"id": "row_side", "primitive": "box", "params": {"size": [0.05, 2.8, 4.6], "color": "#4a5878"}, "position": [x + 0.75, 0, zc]},
        {"id": "row_side_low", "primitive": "box", "params": {"size": [0.06, 0.6, 4.6], "color": "#3b4766"}, "position": [x + 0.74, T + 0.35, zc]},
        {"id": "row_desk", "primitive": "rounded_box", "params": {"size": [1.1, 0.06, 4.4], "color": "#c98c58", "radius": 0.02},
         "position": [x, T - 0.06, zc]},
        {"id": "row_front", "primitive": "box", "params": {"size": [1.1, T - 0.05, 0.05], "color": "#8f5634"}, "position": [x, 0, 0.8]},
        {"id": "row_garland", "primitive": "string_lights", "position": [0, 0, 0],
         "params": {"points": [[x + 0.72, T + 0.75, 0.6], [x + 0.72, T + 0.62, -0.6], [x + 0.72, T + 0.78, -1.8], [x + 0.72, T + 0.64, -3.0]],
                    "color": "#ffc773", "count": 30, "sag": 0.08, "seed": 3}},
        {"id": "row_poster1", "primitive": "poster", "params": {"width": 0.3, "height": 0.4, "colors": ["#f6e6cf", "#e2a83b", "#3f7f84", "#d97757"],
                                                                "frame": "#8f5634", "seed": 7},
         "position": [x + 0.72, T + 0.35, -0.5], "rotation": [0, -90, 0]},
        {"id": "row_poster2", "primitive": "poster", "params": {"width": 0.26, "height": 0.34, "colors": ["#9fb7d9", "#ffd23f", "#d97757", "#2b1810"],
                                                                "frame": "#5a3a2a", "seed": 12},
         "position": [x + 0.72, T + 0.3, -1.6], "rotation": [0, -90, 0]},
        {"id": "row_window", "primitive": "window_frame","params": {"width": 0.7, "height": 0.6}, "position": [x - 0.1, T + 0.45, -3.57]},
        {"id": "gauge", "primitive": "limit_gauge", "params": {"width": 0.5, "height": 0.13, "keys": gauge, "label": "ЛИМИТ"},
         "position": GAUGE, "rotation": [0, -18, 0], "visible": vis((41.9, 46.0))},
        {"id": "price", "primitive": "sign_plate", "params": {"text": "ПОЛНАЯ ЦЕНА", "bg": "#ffd23f", "fg": "#c8261e", "width": 0.26,
                                                              "height": 0.1, "tilt": -5, "glow": 0.15, "string": 0.5},
         "position": PRICE, "rotation": [0, -14, 0], "keys": price_keys, "visible": price_vis},
    ]
    for i, (px, py, pz) in enumerate(ROW):
        objs.append({"id": f"pile{i}", "primitive": "folder_pile", "params": {"count": 24 if i == 0 else 14, "height": 0.26 if i == 0 else 0.17, "spread": 0.12 if i == 0 else 0.08, "seed": 3 + i,
                                                                              "label": "ПРОЕКТ"},
                     "position": [px + PILE_DX * SIDE[i], T, pz - 0.02], "rotation": [0, 10 * (i % 3), 0],
                     "visible": vis((36.2, 46.0)) if i == 0 else vis((41.9, 46.0))})
    return objs


def lights() -> list[dict]:
    """Свет комнаты серии 4 + ряд аккаунтов: тёплые ключи вдоль стола и холодный контр."""
    out = [lt for lt in E4.lights() if "position" not in lt or abs(lt["position"][0]) < 3]
    for z in (0.6, -0.8, -2.2):
        out.append({"type": "point", "color": "#ffe2bf", "intensity": 1.8, "distance": 2.6, "decay": 2, "position": [X_ROW - 0.4, T + 0.9, z]})
    out += [
        {"type": "point", "color": "#9fb6ff", "intensity": 1.1, "distance": 2.4, "decay": 2, "position": [X_ROW + 0.5, T + 0.5, -1.0]},
        {"type": "point", "color": "#ffc773", "intensity": 0.6, "distance": 1.6, "decay": 2, "position": [X_ROW + 0.6, T + 0.7, -0.2]},
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
