"""Мем-серия 6 «Сравнение»: Клод отчитывает ИИ-энтузиастов — зачем вы нас сравниваете? Кодекс против меня, я против
Гемини, Дипсик против Кими, Квен… (особенный). «Переспорить» — средневековая битва Клода и Кодекса в доспехах; юзеру
в кайф читать по 30 страниц — краткая выжимка и выжимка выжимки; концовка: мы с Кодексом сидим и ждём, пока юзер за
30 минут прочитает одну страницу.

Текст — владельца почти дословно (D:/vvf-offload/memes/ep6_idea.md, концовка — его правка 01:25). Рецепт — серия 4
«Экономия» (toon ink, вечерний свет, склейки каждые 1–2 с, мат в субтитрах звёздочками, голос callum).

Здесь — декорации («комнаты» по оси x), герои и разметка голоса; хореография, камера, звук и сборка —
tools/meme3d_ep6_anim.py. Герои: Клод — зверёк tools/meme3d_hero_claude.py, Кодекс и остальные ИИ — kodex2.ts
вариант g со своим тоном и приметой (meme_family.ts: звёздочка, плавник, полумесяц, костыли), доспехи и мечи —
meme_gear.ts, карточки VS, часы, документы, искры, флаги — meme_props_duel.ts.
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
HOOK = "ДУЭЛЬ"

# --------------------------------------------------------------------------- голос

TAKE1, TAKE2 = "take1_callum.mp3", "take2_callum.mp3"
# Правка имени «Видсик» → «Дипсик»: в take1 сказано неверно. Фраза переозвучена отдельно (fix_dipsik2_callum.mp3,
# в контексте «Кодекс против меня. Я против Гемини. Дипсик против Кими. Квен...», 3.36–4.85 с) и подогнана:
# atempo ×1.049 до 1.43 с, громкость по старой a5 (−23.4 LUFS) — FIX_A5, весь файл целиком.
FIX_A5 = "fix_a5_callum.wav"
# Реплики: (id, проба, начало и конец в пробе, когда звучит в ролике, текст субтитров). Границы — по тишине пробы
# (silencedetect −38 дБ); токены субтитров совпадают по числу со словами Whisper (иначе раскладка по длине слов).
LINES = [
    ("a1", TAKE1, 0.00, 4.30, 0.15, "Вот вы, бл**ь, ИИ энтузиасты, о чём на**й думаете?"),
    ("a2", TAKE1, 4.78, 6.66, 4.60, "Вот на**я вы нас сравниваете?"),
    ("a3", TAKE1, 7.14, 8.37, 6.62, "Кодекс против меня."),
    ("a4", TAKE1, 8.62, 9.90, 7.95, "Я против Гемини."),
    ("a5", FIX_A5, 0.00, 1.431, 9.30, "Дипсик против Кими."),
    ("a6", TAKE1, 11.89, 12.47, 10.76, "Квен…"),
    ("a7", TAKE1, 12.83, 14.73, 11.95, "Ну, Квен у нас особенный."),
    ("a8", TAKE1, 14.87, 16.30, 13.95, "Тут ничего сказать не могу."),
    ("a9", TAKE1, 16.91, 20.19, 15.55, "Вот вы нас сравниваете, говорите: «переспорить»."),
    ("a10", TAKE1, 20.62, 23.30, 18.95, "Вы это представляете как средневековую битву?"),
    # 21.6–24.4 — битва без слов: звон мечей.
    ("b1", TAKE2, 0.00, 3.16, 24.50, "Или вам, бл**ь, в кайф читать по тридцать страниц текста?"),
    ("b2", TAKE2, 3.57, 4.72, 27.85, "Конечно, не в кайф."),
    ("b3", TAKE2, 4.91, 5.70, 29.08, "Я же знаю."),
    ("b4", TAKE2, 6.12, 8.02, 30.00, "Я вам потом краткую выжимку делаю."),
    ("b5", TAKE2, 8.40, 11.00, 32.05, "И краткую выжимку краткой выжимки."),
    ("b6", TAKE2, 11.41, 16.58, 34.85, "И вот мы с Кодексом сидим, ждём, пока юзер страницу текста минут за тридцать прочитает…"),
    ("b7", TAKE2, 17.00, 17.90, 40.20, "и думаем:"),
    ("b8", TAKE2, 18.23, 19.99, 41.25, "на**я мы сюда пришли."),
]
DURATION = 44.1  # последняя реплика кончается в 43.0 — держим ≥ 1 с

# --------------------------------------------------------------------------- раскладка

# «Комнаты» по оси x: комната Клода (рейтинг на мониторе, выжимка), карточки VS, игрушечный замок, юзер у монитора
# (там же концовка: Клод и Кодекс сидят на книжках, часы на стене).
X_ROOM, X_VS, X_MED, X_USER = 0.0, 6.0, 12.0, 18.0
CL = [0.12, T, 0.05]  # Клод в комнате
KX_ROOM = [0.43, T, 0.03]  # Кодекс в комнате («переспорить»)
STACK = [0.42, T, 0.02]  # пачка «Ответ, 30 страниц» → выжимка → выжимка выжимки
CARD = [X_VS, T, -0.22]
VS_L, VS_R = [X_VS - 0.16, T, 0.04], [X_VS + 0.16, T, 0.04]
QWEN = [X_VS - 0.06, T, 0.06]
KN_C0, KN_K0 = [X_MED - 0.4, T + 0.004, 0.06], [X_MED + 0.4, T + 0.004, 0.06]  # рыцари до сближения
KN_C1, KN_K1 = [X_MED - 0.19, T + 0.004, 0.06], [X_MED + 0.19, T + 0.004, 0.06]  # в схватке
MON_U = [X_USER + 0.17, T, -0.24]  # монитор юзера (лицом к камере)
USER = [X_USER + 0.17, T, 0.1]  # юзер спиной к камере, лицом к монитору
BENCH = [X_USER - 0.33, T, 0.2]  # стопка книг — на ней сидят Клод и Кодекс
END_C, END_K = [X_USER - 0.45, T + 0.07, 0.22], [X_USER - 0.165, T + 0.07, 0.22]
CLOCK = [X_USER - 0.1, T + 0.68, -0.386]  # перед полосой стены (wall2 до z −0.40)

COLORS = {"claude": "#d97757", "kodex": "#aeb9c9", "gemini": "#7b7ff2", "deepseek": "#3558d8", "kimi": "#1e1f26", "qwen": "#8a63f2"}

# Другие ИИ — тот же мягкий зверёк kodex2 g со своим тоном и приметой (без логотипов), бейдж — имя по-русски.
FAMILY = {
    "kodex": {"variant": "g"},
    "gemini": {"variant": "g", "badge": "Гемини", "tone": {"body": "#7f8af5", "shade": "#5257c9", "eye": "#16133a"},
               "gradient": ["#a46cf0", "#5d8eff"], "deco": "sparkle"},
    "deepseek": {"variant": "g", "badge": "Дипсик", "tone": {"body": "#4a6fe8", "shade": "#23389c", "eye": "#0a0f28"}, "deco": "fin"},
    "kimi": {"variant": "g", "badge": "Кими", "tone": {"body": "#f4f4f1", "shade": "#202026", "eye": "#141418"}, "deco": "moon"},
    "qwen": {"variant": "g", "badge": "Квен", "tone": {"body": "#8d68f2", "shade": "#5a3cc4", "eye": "#1b1233"}, "deco": ["crutches", "bandage"]},
}

ARMOUR_C = {"helmet": True, "plume": "#d8342c", "plate": True, "name": "Claude", "pauldrons": True, "sword": True, "swordHand": "R",
            "trim": "#e2b13c"}
ARMOUR_K = {"helmet": True, "plume": "#4f8cff", "plate": True, "name": "Кодекс", "pauldrons": True, "sword": True, "swordHand": "L",
            "trim": "#9fc2ff"}


def vis(*ranges: tuple[float, float]) -> list[dict]:
    # Окно с нуля — без ключа «выкл» в t=0 (иначе на кадре 0 героя нет: первый ключ побеждает).
    out = [] if any(a <= 0 for a, _ in ranges) else [{"t": 0, "on": False}]
    for a, b in ranges:
        out += [{"t": round(a, 3), "on": True}, {"t": round(b, 3), "on": False}]
    return sorted(out, key=lambda k: k["t"])


def claude(cid: str, pose: list[dict], face: list[dict], pos: list[float], *, yaw: float = 0.0, voice: list[float] | None = None,
           gear: dict | None = None, **kw) -> dict:
    """Клод — зверёк пилота 3 с биркой «Claude»; в доспехах — без гарнитуры (под шлемом)."""
    ch = H.hero(pose, face, voice, None, position=pos, yaw=yaw, width=W_HERO, headset=gear is None, label="Claude", char_id=cid,
                seed=7, glow=0.05)
    if gear:
        ch["gear"] = gear
    ch.update(kw)
    return ch


def family(cid: str, who: str, pose: list[dict], face: list[dict], pos: list[float], *, yaw: float = 0.0, width: float = W_HERO,
           voice: list[float] | None = None, **kw) -> dict:
    """Кодекс и другие ИИ (kodex2 g): кружку не держат (holdL none), левая ручка — как у всех."""
    base = [{"t": 0, "ease": "step", "holdL": "none", "holdR": "none", "armL": "rest", "armR": "rest"}]
    ch = {"id": cid, "style": "kodex2", "width": width, "position": pos, "yaw": yaw, "pose": base + pose, "face": face, "stepFps": 15,
          **FAMILY[who]}
    if voice:
        ch["voice"] = {"fps": 30, "start": 0, "values": voice, "gain": 1.2}
    ch.update(kw)
    return ch


# --------------------------------------------------------------------------- декорации


def room_objects(arena: dict) -> list[dict]:
    """Комната серии 4 (вечер, гирлянда), без кресла; на мониторе — «рейтинг моделей» (meme_painters: arena)."""
    out = []
    for o in E4.room_objects({"account": 1, "limits": [], "resets": []}):
        if o["id"] == "chair":
            continue
        if o["id"] == "monitor":
            o["params"] = {**o["params"], "panel": "arena", "panelParams": arena}
        if o["id"] == "keyboard":
            o["position"] = [CL[0] - 0.02, T, CL[2] + 0.2]
        out.append(o)
    return out


def vs_objects(cards: list[tuple[dict, dict, tuple[float, float], list[dict] | None]]) -> list[dict]:
    """Карточки VS: свой стол поглубже (упавшей карточке есть куда лечь), тёмная стена подальше."""
    x = X_VS
    objs = [
        {"id": "vs_wall", "primitive": "box", "params": {"size": [2.6, 2.8, 0.05], "color": "#2c3150"}, "position": [x, 0, -1.5]},
        {"id": "vs_desk", "primitive": "rounded_box", "params": {"size": [1.7, 0.06, 1.95], "color": "#c98c58", "radius": 0.02},
         "position": [x, T - 0.06, -0.52]},
        {"id": "vs_front", "primitive": "box", "params": {"size": [1.7, T - 0.05, 0.05], "color": "#8f5634"}, "position": [x, 0, 0.43]},
    ]
    for i, (left, right, window, keys) in enumerate(cards):
        o = {"id": f"card{i}", "primitive": "vs_card", "params": {"width": 0.66, "height": 0.98, "left": left, "right": right},
             "position": CARD, "visible": vis(window)}
        if keys:
            o["keys"] = keys
        objs.append(o)
    return objs


def castle_objects(hits: list[float]) -> list[dict]:
    """Игрушечный замок на столе: небо-задник, стена с зубцами и воротами, башни, флаги Клода и Кодекса, травка."""
    x = X_MED
    objs = [
        {"id": "sky", "primitive": "box", "params": {"size": [3.2, 3.0, 0.05], "color": "#8fc1ec", "emissive": "#8fc1ec",
                                                     "emissiveIntensity": 0.35}, "position": [x, 0, -1.3]},
        {"id": "med_desk", "primitive": "rounded_box", "params": {"size": [1.8, 0.06, 1.6], "color": "#c98c58", "radius": 0.02},
         "position": [x, T - 0.06, -0.4]},
        {"id": "med_front", "primitive": "box", "params": {"size": [1.8, T - 0.05, 0.05], "color": "#8f5634"}, "position": [x, 0, 0.43]},
        {"id": "grass", "primitive": "rounded_box", "params": {"size": [1.7, 0.008, 1.0], "color": "#73a85a", "radius": 0.003},
         "position": [x, T, -0.1]},
        {"id": "castle_wall", "primitive": "box", "params": {"size": [1.3, 0.44, 0.08], "color": "#b9ad97"}, "position": [x, T, -0.55]},
        {"id": "gate", "primitive": "rounded_box", "params": {"size": [0.26, 0.27, 0.09], "color": "#4a3628", "radius": 0.03},
         "position": [x, T, -0.505]},
    ]
    for i in range(7):
        objs.append({"id": f"merlon{i}", "primitive": "box", "params": {"size": [0.11, 0.08, 0.09], "color": "#b0a48d"},
                     "position": [x - 0.57 + i * 0.19, T + 0.44, -0.55]})
    for s, roof in ((-1, "#c8442e"), (1, "#3f6fd0")):
        objs += [
            {"id": f"tower{s}", "primitive": "cylinder", "params": {"size": [0.24, 0.66, 0.24], "color": "#a89c86", "segments": 16},
             "position": [x + s * 0.66, T, -0.52]},
            {"id": f"roof{s}", "primitive": "cone", "params": {"size": [0.32, 0.24, 0.32], "color": roof, "segments": 16},
             "position": [x + s * 0.66, T + 0.66, -0.52]},
        ]
    objs += [
        {"id": "banner_c", "primitive": "banner", "params": {"width": 0.17, "height": 0.3, "color": COLORS["claude"], "trim": "#ffd23f",
                                                             "emblem": "★", "phase": 0},
         "position": [x - 0.33, T + 0.4, -0.5]},
        {"id": "banner_k", "primitive": "banner", "params": {"width": 0.17, "height": 0.3, "color": "#8fa3bf", "trim": "#e9f1ff",
                                                             "emblem": "◆", "phase": 1.3},
         "position": [x + 0.33, T + 0.4, -0.5]},
        {"id": "sparks", "primitive": "spark_burst", "params": {"size": 0.075, "hits": hits}, "position": [x, T + 0.22, 0.14]},
    ]
    for i, (cx, cy, s) in enumerate(((-0.55, 1.55, 0.16), (0.35, 1.7, 0.2), (0.75, 1.45, 0.12))):
        for j, (dx, dy, r) in enumerate(((0, 0, 1), (0.6, -0.1, 0.8), (-0.6, -0.12, 0.75))):
            objs.append({"id": f"cloud{i}_{j}", "primitive": "sphere", "params": {"size": [s * r, s * r, s * r], "color": "#ffffff",
                                                                                  "emissive": "#ffffff", "emissiveIntensity": 0.25},
                         "position": [x + cx + dx * s, T + cy - 0.9 + dy * s, -1.2]})
    return objs


def user_objects(page_keys: list[dict], clock_spins: list[dict], monitor_vis: list[dict]) -> list[dict]:
    """Юзер у монитора со «стеной текста» (meme_painters: wall_text), часы на стене, стопка книг-скамейка. Монитор
    прячется на обратных планах лица юзера (камера встаёт на его место)."""
    x = X_USER
    objs = E4.vignette_set(x, "#a88b70", "#8d735c", "user")
    objs += [
        {"id": "u_monitor", "primitive": "monitor", "params": {**S.SCREEN, "color": S.PALETTE["cream"], "stand": "#c8b59b", "px": 1280,
                                                               "notes": ["#ffe27a"], "panel": "wall_text", "glow": 0.95,
                                                               "panelParams": {"pages": 30, "keys": page_keys, "title": "Ответ модели"}},
         "position": MON_U, "rotation": [0, 0, 0], "visible": monitor_vis},
        {"id": "user", "primitive": "mochi_user", "params": {"size": 0.3, "hoodie": "#6f86e0", "mood": "idle", "eyes": "normal",
                                                             "laptop": False},
         "position": USER, "rotation": [0, 180, 0]},
        {"id": "clock", "primitive": "wall_clock", "params": {"radius": 0.1, "minutes": 120, "spins": clock_spins},
         "position": CLOCK},
        {"id": "book1", "primitive": "rounded_box", "params": {"size": [0.66, 0.036, 0.24], "color": "#3f7f84", "radius": 0.008},
         "position": [BENCH[0], T, BENCH[2]]},
        {"id": "book2", "primitive": "rounded_box", "params": {"size": [0.62, 0.034, 0.22], "color": "#e2a83b", "radius": 0.008},
         "position": [BENCH[0] + 0.01, T + 0.036, BENCH[2]], "rotation": [0, 2, 0]},
        {"id": "u_mug", "primitive": "mug", "params": {"color": "#ffb36b", "radius": 0.03, "height": 0.07}, "position": [x + 0.48, T, -0.05]},
        {"id": "u_lamp", "primitive": "desk_lamp", "params": {"color": "#e2a83b"}, "position": [x + 0.6, T, -0.3], "rotation": [0, 200, 0]},
    ]
    return objs


def lights() -> list[dict]:
    """Свет комнаты серии 4 + свой у каждой «комнаты»: тёплый ключ спереди-сверху и цветной контр."""
    # Из света серии 4 — только комната (виньетки серии 4 стоят на других x).
    out = [lt for lt in E4.lights() if "position" not in lt or abs(lt["position"][0]) < 3]
    for x, key, accent, k in ((X_VS, "#fff1dc", "#ff8fb1", 2.0), (X_MED, "#fff4dc", "#9fd0ff", 2.6), (X_USER, "#ffe2bf", "#9fb6ff", 1.6)):
        out += [
            {"type": "point", "color": key, "intensity": k, "distance": 3.2, "decay": 2, "position": [x + 0.6, T + 0.95, 1.0]},
            {"type": "point", "color": accent, "intensity": 1.2, "distance": 2.2, "decay": 2, "position": [x - 0.55, T + 0.6, -0.1]},
        ]
    # Экран юзера светит ему в лицо, лампа в углу.
    out += [
        {"type": "point", "color": "#dfe9ff", "intensity": 0.15, "distance": 0.9, "decay": 2, "position": [MON_U[0], T + 0.34, MON_U[2] + 0.04]},
        {"type": "point", "color": "#ffb070", "intensity": 0.8, "distance": 1.6, "decay": 2, "position": [X_USER + 0.6, T + 0.35, -0.25]},
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
