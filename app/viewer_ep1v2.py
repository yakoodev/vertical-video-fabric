"""Серия 1, версия 2 («Никто и камера»): сценарий огонька → сцена PS1. Чистые функции; озвучка, рендер, звук и проверки —
tools/viewer_ep1v2.py. Опирается на серию 0 (app/viewer_ep.py) и на версию 1 (app/viewer_ep1.py: бросок, её голос).

Что владелец забраковал в v1: «скомкано», запись на камере не читалась как запись (то же место, нет смены места), кубик
рисованный, камера парила в воздухе. Что изменено:
1. Темп: короткие фразы, настоящие паузы 0.5–1.4 с, ключевые моменты держатся (имя, кубик, камера, запись, Сборщик).
2. Кубик настоящий: d20 в сцене (die.ts) — подброшен огоньком, кувыркается, подпрыгивает, ложится выпавшей гранью вверх.
3. Камеру она держит сама (hold.ts): берёт у груди, поднимает на вытянутой руке — селфи: кадр «с камеры» идёт от
   самого объектива в её руке (дрожит с рукой), с эффектом старой камеры, REC.
4. Запись — другое место: вокзал (station.ts), холодный зелёный свет; сначала на откидном экранчике камеры в её руках
   (вложенный рендер в текстуру), потом на весь кадр: ▶ PLAY, штамп «08 НОЯ 03:16», сильный износ плёнки; она другая —
   с фонариком, испугана, оглядывается; за ней в проходе что-то движется. Рвётся в помехи — снова улица, лицо в свете
   экранчика, огонёк реагирует.
5. Меню выбора внизу, Сборщик виден целиком.
"""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

from app.viewer_dnd import PRESETS
from app.viewer_ep import _key, _wisp_pos, cue_times, pace, scale_beats, shift_beats, speed_for, stats_spec
from app.viewer_ep1 import CAMERA_ITEM, CHOICE, FINAL, HER_VOICE, HOOK, ROLL_SEED, ep1_roll  # noqa: F401
from app.viewer_episode import caption_chunks, sentence_times

LEAD = 0.25  # до первой фразы: она уже в кадре (первый кадр = обложка)
TAIL = 2.6  # после последней фразы: Сборщик ближе, батарея садится, помехи (с)
HER_TEXT = "Если ты это смотришь… не ходи на вокзал… Они идут на звук."
DATE_LIVE, DATE_TAPE = "09 НОЯ", "08 НОЯ"
STAMP_LIVE, STAMP_TAPE = "03:17", "03:16"

# Куски запись-воспроизведения (с): экранчик, потом весь кадр.
LCD_LEN = 2.4  # запись идёт на экранчике, потом наезд
PRE = 1.0  # на весь кадр: тишина до её голоса
POST = 1.2  # после её голоса: что-то движется за спиной, потом срыв
TEAR = 0.45  # помехи на обрыве
AFTER = 0.6  # после обрыва до реплики огонька
DIE_T = 2.1  # бросок кубика: подбросили → лёг (с)

NUMS = ["", "Один", "Два", "Три", "Четыре", "Пять", "Шесть", "Семь", "Восемь", "Девять", "Десять", "Одиннадцать", "Двенадцать",
        "Тринадцать", "Четырнадцать", "Пятнадцать", "Шестнадцать", "Семнадцать", "Восемнадцать", "Девятнадцать", "Двадцать"]

# Пауза перед фразой (с) по её имени-роли: у каждой фразы своя роль — паузы подобраны на слух, а не одинаковые.
PAUSE_BEFORE = {"intro2": 0.55, "intro3": 0.5, "name": 0.7, "roll": 1.3, "item1": 0.35 + DIE_T + 0.85, "item2": 0.8,
                "cam1": 1.5, "cam2": 0.45, "old": 2.0, "steps1": 1.0, "steps2": 0.5, "choice1": 0.9, "choice2": 0.6, "react2": 0.5}


def ep2_script(roll: dict[str, Any]) -> list[dict]:
    """Реплики огонька: короткие, по одной мысли; каждая со своей ролью (паузы — PAUSE_BEFORE)."""
    say = PRESETS[roll["preset"]]["say"].capitalize()
    return [
        {"beat": "intro1", "text": "Эй. Под прошлой серией — пусто."},
        {"beat": "intro2", "text": "Никто ничего не выбрал."},
        {"beat": "intro3", "text": "Значит, и имени у тебя нет."},
        {"beat": "name", "text": "Будешь… Никто.", "cues": ["никто"]},
        {"beat": "roll", "text": "Остальное решит кубик."},
        {"beat": "item1", "text": f"{NUMS[roll['die']]}. {say}."},
        {"beat": "item2", "text": "И ещё — камера!", "cues": ["камера"]},
        {"beat": "cam1", "text": "Включай. Снимай себя."},
        {"beat": "cam2", "text": "Вдруг что-то вспомнишь."},
        {"beat": "old", "text": "Стой… На плёнке что-то есть."},
        {"beat": "react1", "text": "Это… ты?"},
        {"beat": "react2", "text": "Когда ты успела?"},
        {"beat": "steps1", "text": "Тихо."},
        {"beat": "steps2", "text": "Слышишь? Шаги."},
        {"beat": "choice1", "text": "На вокзал, спрятаться или своё?", "cues": ["вокзал", "спрятаться", "свое"]},
        {"beat": "choice2", "text": "И скажите ей имя."},
    ]


def ep2_beats(script: list[dict], words: list[dict], her_len: float, lead: float = LEAD) -> tuple[list[dict], float, list[dict]]:
    """Слова озвучки → фразы → темп с паузами серии. Перед «Это… ты?» — вся запись: экранчик, весь кадр, её голос, срыв."""
    gap_react = 0.45 + LCD_LEN + PRE + her_len + POST + TEAR + AFTER
    segs, paced = pace(sentence_times(script, words), before={**PAUSE_BEFORE, "react1": gap_react})
    k = speed_for(segs[-1]["at"] + segs[-1]["src"][1] - segs[-1]["src"][0], lead, max_total=999.0)
    return segs, k, shift_beats(scale_beats(paced, k), lead)


def timeline(beats: list[dict], her_len: float, total: float) -> dict[str, float]:
    """Все моменты серии (с от начала ролика). Запись отсчитывается назад от «Это… ты?»."""
    ph = {b["beat"]: b for b in beats}
    name_c = cue_times(ph["name"])[0] or ph["name"]["t0"] + 0.7
    drop_c = cue_times(ph["item2"])[0] or ph["item2"]["t0"] + 0.8
    react0 = ph["react1"]["t0"]
    full1 = react0 - AFTER
    tear0 = full1 - TEAR
    her1 = tear0 - POST
    her0 = her1 - her_len
    full0 = her0 - PRE
    lcd0 = full0 - LCD_LEN
    throw = ph["roll"]["t1"] + 0.35
    ev = {"name": name_c, "throw": throw, "land": throw + DIE_T, "drop": drop_c,
          "take": ph["item2"]["t1"] + 0.3, "raise": ph["cam1"]["t0"] - 0.1, "self": ph["cam1"]["t0"] + 0.9,
          "lower": ph["old"]["t0"] - 0.75, "out": ph["old"]["t0"] - 0.1, "lcd0": lcd0, "full0": full0, "her0": her0, "her1": her1,
          "tear": tear0, "full1": full1, "react": react0, "c0": ph["steps1"]["t0"] - 0.3, "steps": ph["steps1"]["t0"],
          "choice": ph["choice1"]["t0"]}
    say_c = next((w["start"] for w in ph["choice2"]["words"] if "скажите" in w["word"].casefold()), ph["choice2"]["t1"] - 1.0)
    ev["final"] = say_c - 0.1
    ev["end"] = total
    ev["static_end"] = total - 0.4
    if lcd0 < ph["old"]["t1"] + 0.15:
        raise ValueError(f"запись началась раньше конца реплики «Стой…»: {lcd0:.2f} < {ph['old']['t1']:.2f}")
    return ev


def _rec(ev: dict, her_len: float, her_env: list[float], her_words: list[dict]) -> dict[str, Any]:
    """Запись на вокзале: всё во времени записи p = t − lcd0 (экранчик p ∈ [0, LCD_LEN], потом весь кадр)."""
    p = lambda t: round(t - ev["lcd0"], 3)  # noqa: E731
    her0, her1, tear = p(ev["her0"]), p(ev["her1"]), p(ev["tear"])
    glance = her0 + 1.4
    for w in her_words:  # оглядывается сразу после «смотришь…» — перед «не ходи на вокзал»
        if "смотришь" in w["word"].casefold():
            glance = her0 + w["end"] + 0.15
            break
    cam_pt = [0.0, 1.45, 3.0]
    end = tear + 1.0
    return {
        "t0": round(ev["lcd0"], 3), "lcd": [round(ev["lcd0"], 3), round(ev["full0"], 3)], "full": [round(ev["full0"], 3), round(ev["full1"], 3)],
        # Холодный болезненно-зелёный свет: резкий контраст с тёплой улицей.
        "psx": {"fog": {"color": "#3a5a4a", "near": 5.0, "far": 20.0},
                "light": {"dir": [0.1, 1, 0.3], "color": "#d0ffe0", "power": 0.9, "ambient": "#a0d0b0", "ambientPower": 1.35}},
        "fx": {"desat": 0.25, "tint": "#c8f0d6", "contrast": 1.08, "gain": 1.45, "lift": 12, "grain": 15, "vignette": 0.4},
        "station": {"clock": [3, 17], "sign": "ВОКЗАЛ", "seed": 29},
        # Что-то движется в проходе за спиной: сначала мелькнуло, потом стоит и идёт к ней.
        "stalker": {"shape": "collector", "pos": [1.0, 0.0, -7.3], "move": [0.75, 0.0, -4.9], "face": [0, 3.0], "height": 3.0,
                    "show": [[round(her0 + 1.0, 3), round(her0 + 1.3, 3)], [round(her1 - 0.9, 3), round(tear + 0.8, 3)]]},
        "shots": [
            {"name": "зал", "t0": 0, "t1": round(her0 - 0.35, 3), "minHeight": 0.0, "camKeys": [
                _key(0, 0, 3.7, 1.5, [0, 1.85, 0], 54), _key(her0 - 0.35, 0, 3.3, 1.5, [0, 1.8, 0], 52)]},
            {"name": "крупно", "t0": round(her0 - 0.35, 3), "t1": round(end + 1, 3), "minHeight": 0.0, "camKeys": [
                _key(0, 0, 3.0, 1.55, [0.1, 1.7, 0], 44), _key(end - her0 + 0.35, -3, 2.4, 1.52, [0.15, 1.6, 0], 40)]},
        ],
        "look_at": [{"t": -10, "target": cam_pt, "head": 0.6},
                    {"t": round(glance, 3), "target": [1.7, 1.5, -3.5], "head": 0.9},
                    {"t": round(glance + 1.0, 3), "target": cam_pt, "head": 0.6},
                    {"t": round(her1 - 0.8, 3), "target": [0.9, 1.7, -6.0], "head": 0.9}],
        "holds": {"object": "torch", "keys": [{"t": -1.0, "mode": "torch", "blend": 0.05}]},
        "mouth": [{"t0": her0, "t1": her1, "voice": her_env, "fps": 30}],
        "flashlight": {"pos": [0, 0, 0], "dir": [0, 1, 0], "color": "#ffeccc", "power": 1.8, "cone": 40, "range": 3.4},
        "stand": {"period": 3.1, "sway": 0.008},
        "lcdCrop": [112, 202],
    }


def ep2_scene(beats: list[dict], total: float, roll: dict[str, Any], her_len: float, her_env: list[float], her_words: list[dict]) -> dict[str, Any]:
    """Планы, огонёк, интерфейс, камера в руках, кубик, запись, Сборщик и помехи серии по времени фраз."""
    ph = {b["beat"]: b for b in beats}
    ev = timeline(beats, her_len, total)
    hero = roll["heroine"]
    end = total
    c0, steps, choice = ev["c0"], ph["steps1"]["t0"], ph["choice1"]["t0"]
    i2 = ph["intro2"]["t0"]
    t_stat = ph["item1"]["t0"] - 0.1

    def seg(t0, t1, name, keys, minh=0.0, **kw):
        return {"name": name, "t0": round(t0, 3), "t1": round(t1, 3), "minHeight": minh, "camKeys": keys, **kw}

    t_take_cut = ev["take"]
    shots = [
        # Первый кадр: лицо крупно, чуть сбоку; медленный наезд (он же обложка).
        seg(0, i2 - 0.1, "лицо", [_key(0, 7, 1.8, 1.47, 1.38, 32), _key(i2 - 0.1, 3, 1.55, 1.46, 1.38, 32)]),
        seg(i2 - 0.1, ev["throw"] - 0.25, "имя", [_key(0, -24, 2.5, 1.5, 1.28, 36), _key(ev["throw"] - i2, -14, 2.3, 1.5, 1.3, 36)], 0.2),
        # Бросок: камера сверху-спереди, кубик катится на асфальт перед ней, она — в верхней половине.
        seg(ev["throw"] - 0.25, ev["drop"] - 0.2, "кубик", [_key(0, 0, 3.4, 2.0, [0.05, 0.95, 0.45], 40),
                                                              _key(ev["drop"] - ev["throw"], 0, 3.2, 1.95, [0.05, 0.95, 0.45], 40)], 0.12),
        # Камера парит перед ней — и она берёт её в руки.
        seg(ev["drop"] - 0.2, ev["take"] + 0.5, "находка", [_key(0, 14, 2.4, 1.35, 1.25, 40), _key(ev["take"] + 0.7 - ev["drop"], 8, 2.1, 1.38, 1.27, 40)], 0.2),
        seg(ev["take"] + 0.5, ev["self"], "в руках", [_key(0, 22, 1.9, 1.45, [0.05, 1.2, 0.25], 34), _key(ev["self"] - ev["take"] - 0.5, 14, 1.7, 1.45, [0.05, 1.25, 0.25], 34)], 0.2),
        # Селфи: объектив — в её вытянутой руке, кадр дрожит вместе с рукой.
        seg(ev["self"], ev["out"], "селфи", [_key(0, 0, 1, 1.5, 1.45, 60)], 0.0, selfie={"fov": 70, "back": 0.22}),
        # Смотрит запись на экранчике: из-за плеча, наезд на экранчик.
        seg(ev["out"], ev["full0"], "экранчик", [_key(0, 178, 1.2, 1.75, [0.1, 1.2, 0.3], 36), _key(ev["lcd0"] - ev["out"], 174, 0.95, 1.65, [0.13, 1.18, 0.32], 32),
                                                _key(ev["full0"] - ev["out"], 175, 0.5, 1.38, [0.14, 1.17, 0.34], 26)], 0.0, free=True),
        {"name": "запись", "t0": round(ev["full0"], 3), "t1": round(ev["full1"], 3), "minHeight": 0.0, "free": True, "camKeys": [_key(0, 0, 3, 1.5, 1.4, 40)]},
        # Лицо в свете экранчика: огонёк реагирует.
        seg(ev["full1"], c0, "лицо в свете", [_key(0, 6, 1.7, 1.45, [0, 1.3, 0], 36), _key(c0 - ev["full1"], 3, 1.5, 1.42, [0, 1.3, 0], 35)], 0.0),
        # Шаги: камера отъезжает и поднимается — слева из тумана идёт Сборщик, виден весь рост.
        seg(c0, end + 1, "сборщик", [_key(0, 3, 1.2, 1.4, [0, 1.38, 0], 40), _key(1.7, 0, 2.7, 1.5, [0, 1.15, 0], 50), _key(end - c0, 0, 2.5, 1.5, [0, 1.12, 0], 50)], 0.0),
    ]
    cuts = [round(s["t0"], 3) for s in shots[1:]]

    # --- огонёк
    keys = [{"t": 0, "pos": [0.42, 1.78, 0.6]}, {"t": 0.9, "pos": [0.22, 1.7, 0.3]}, {"t": round(i2 - 0.1, 3), "pos": [0.24, 1.66, 0.3]}]
    t_a, t_b = i2 + 0.2, ev["throw"] - 0.3
    n = max(2, int((t_b - t_a) / 0.35) + 1)
    for i in range(n):
        t = t_a + (t_b - t_a) * i / (n - 1)
        a = 0.4 + 0.9 * (t - i2)
        keys.append({"t": round(t, 3), "pos": [round(0.65 * math.sin(a), 3), round(1.55 + 0.15 * math.sin(a * 0.5 + 1.0), 3), round(0.55 + 0.2 * math.cos(a), 3)]})
    keys += [{"t": round(ev["throw"], 3), "pos": [0.5, 1.7, 0.4]}, {"t": round(ev["land"], 3), "pos": [0.55, 1.5, 0.65]},
             {"t": round(ev["drop"] + 0.4, 3), "pos": [0.55, 1.45, 0.8]}, {"t": round(ev["take"], 3), "pos": [0.5, 1.55, 0.7]},
             {"t": round(ev["raise"], 3), "pos": [0.72, 1.75, 0.15]}, {"t": round(ev["out"] + 0.3, 3), "pos": [0.55, 1.58, 0.5]},
             {"t": round(ev["full1"] + 0.2, 3), "pos": [0.36, 1.56, 0.42]}, {"t": round(c0, 3), "pos": [0.33, 1.58, 0.4]},
             {"t": round(end, 3), "pos": [0.28, 1.5, 0.45]}]
    keys.sort(key=lambda k: k["t"])
    wisp = {"appear": -5, "fadeIn": 0.5, "keys": keys, "dim": [[round(c0 + 0.2, 3), round(end + 1, 3), 0.62]]}

    # --- взгляд (улица)
    cam_pt = [0.0, 1.47, 1.0]
    look = [{"t": -5, "target": cam_pt, "head": 0.6}, {"t": round(i2 + 0.3, 3), "target": _wisp_pos(keys, i2 + 0.6), "head": 0.7}]
    t = i2 + 0.9
    while t < ev["throw"] - 0.3:
        look.append({"t": round(t, 3), "target": _wisp_pos(keys, t + 0.25), "head": 0.75})
        t += 0.6
    look += [{"t": round(ev["throw"] - 0.2, 3), "target": [0.3, 1.9, 0.6], "head": 0.6},
             {"t": round(ev["throw"] + 0.5, 3), "target": [0.1, 0.3, 1.0], "head": 0.85},  # следит за кубиком
             {"t": round(ev["land"] + 0.3, 3), "target": [0.3, 1.7, 0.7], "head": 0.7},
             {"t": round(ev["drop"] + 0.5, 3), "target": [0.3, 1.3, 0.5], "head": 0.8},
             {"t": round(ev["take"] + 0.2, 3), "target": [0.05, 1.15, 0.4], "head": 0.8},
             {"t": round(ev["raise"] + 0.3, 3), "target": [0.2, 1.5, 0.6], "head": 0.6},  # в объектив
             {"t": round(ev["out"], 3), "target": [0.02, 1.1, 0.4], "head": 0.8},  # вниз, на экранчик
             {"t": round(ev["full1"] + 0.05, 3), "target": [0.0, 1.55, 2.0], "head": 0.6},
             {"t": round(steps + 0.2, 3), "target": [-2.5, 1.4, -0.8], "head": 0.55},
             {"t": round(choice + 0.4, 3), "target": cam_pt, "head": 0.55}]

    # --- интерфейс
    hud_stats = stats_spec(hero, ev["name"] - 0.05)
    hud_stats.update({"nameLabel": "ИМЯ", "invLabel": "ПРЕДМЕТ", "statsT": round(t_stat, 3), "invT": round(ev["drop"] - 0.05, 3),
                      "t1": round(ev["self"] - 0.1, 3), "w": 170})
    die = roll["die"]
    p = PRESETS[roll["preset"]]
    rolls = {"t0": round(ev["throw"] - 0.1, 3), "t1": round(ev["drop"] - 0.1, 3), "stat": "УДАЧА ТУМАНА", "action": "КТО ТЫ ТАКАЯ?", "die": die,
             "bonus": 0, "dc": 0, "spin": round(DIE_T + 0.1, 3), "y": 30, "compact": True, "custom": [f"ВЫПАЛО {die}", p["title"]]}
    cards = [{"t0": round(ev["drop"], 3), "t1": round(ev["take"] + 0.2, 3), "lines": ["НАЙДЕН ПРЕДМЕТ:", "СТАРАЯ КАМЕРА"], "accent": [1], "y": 84},
             {"t0": round(choice - 0.1, 3), "t1": round(ev["final"] - 0.05, 3), "lines": CHOICE, "accent": [0], "y": 268}]
    titles = [{"t0": 0, "t1": 2.6, "lines": HOOK, "colors": ["#f8f8f8", "#f8d848"], "y": 52, "px": 24},
              {"t0": round(ev["final"], 3), "t1": round(ev["static_end"], 3), "lines": FINAL, "colors": ["#f8d848", "#f8f8f8"], "y": 304, "px": 16}]
    cam = [{"t0": round(ev["self"], 3), "t1": round(ev["out"], 3), "mode": "rec", "stamp": STAMP_LIVE, "sec0": 41, "date": DATE_LIVE},
           {"t0": round(ev["full0"], 3), "t1": round(ev["full1"], 3), "mode": "play", "stamp": STAMP_TAPE, "sec0": 18, "date": DATE_TAPE}]

    # --- плёнка: чисто на улице; камера — только в селфи; запись — износ
    vhs = {"keys": [[0, 0], [round(ev["self"] - 0.01, 3), 0], [round(ev["self"], 3), 1.0], [round(ev["out"] - 0.01, 3), 1.0], [round(ev["out"], 3), 0],
                    [round(ev["full0"] - 0.01, 3), 0], [round(ev["full0"], 3), 1.9], [round(ev["full1"] - 0.01, 3), 1.9], [round(ev["full1"], 3), 0],
                    [round(ev["static_end"] - 0.5, 3), 0], [round(end, 3), 1.2]],
           "hunts": [round(ev["self"] + 0.6, 3)],
           "tears": [[round(ev["her0"] + 1.9, 3), round(ev["her0"] + 2.05, 3)], [round(ev["tear"] - 0.05, 3), round(ev["full1"], 3)],
                     [round(c0 - 0.05, 3), round(c0 + 0.25, 3)], [round(ev["static_end"] - 0.1, 3), round(end + 1, 3)]],
           "shake": 0.012}
    spike = lambda t, a: [{"t": round(t - 0.05, 3), "static": 0.0}, {"t": round(t, 3), "static": a},  # noqa: E731
                          {"t": round(t + 0.1, 3), "static": a * 0.5}, {"t": round(t + 0.2, 3), "static": 0.0}]
    fx_keys = [{"t": 0, "black": 0.0, "static": 0.0}]
    fx_keys += spike(ev["self"], 0.55) + spike(ev["out"], 0.4) + spike(ev["lcd0"], 0.3) + spike(ev["full0"], 0.7)
    fx_keys += [{"t": round(ev["tear"] - 0.05, 3), "static": 0.0}, {"t": round(ev["tear"] + 0.15, 3), "static": 0.95},
                {"t": round(ev["full1"] - 0.05, 3), "static": 0.9}, {"t": round(ev["full1"] + 0.12, 3), "static": 0.0}]
    fx_keys += spike(c0 + 0.1, 0.35)
    fx_keys += [{"t": round(ev["static_end"] - 0.05, 3), "static": 0.0}, {"t": round(ev["static_end"] + 0.1, 3), "static": 1.0},
                {"t": round(end + 1, 3), "static": 1.0}]
    fx_keys.sort(key=lambda k: k["t"])

    stalker = {"shape": "collector", "pos": [-0.95, 0.0, -4.6], "move": [-0.75, 0.0, -2.8], "face": [0, 3.0], "height": 2.8, "glow": "#9aa4a4",
               "show": [[round(c0, 3), round(end + 1, 3)]]}
    holds = {"object": "camcorder", "spawn": round(ev["drop"], 3), "keys": [
        {"t": round(ev["drop"], 3), "mode": "float", "blend": 0.1}, {"t": round(ev["take"], 3), "mode": "chest", "blend": 0.7},
        {"t": round(ev["raise"], 3), "mode": "selfie", "blend": 0.8}, {"t": round(ev["lower"], 3), "mode": "chest", "blend": 0.6}]}
    die_spec = {"t0": round(ev["throw"], 3), "t1": round(ev["land"], 3), "until": round(ev["drop"], 3), "die": die, "from": [0.5, 1.65, 0.4],
                "to": [0.08, 0.5], "r": 0.2, "turns": 4.5}
    lcd_glow = [{"t0": round(ev["lcd0"] - 0.3, 3), "t1": round(ev["full0"], 3), "color": "#7ab8ff", "power": 2.2, "range": 2.8},
                {"t0": round(ev["full1"], 3), "t1": round(end + 1, 3), "color": "#7ab8ff", "power": 3.0, "range": 2.8}]
    rec = _rec(ev, her_len, her_env, her_words)
    events = dict(ev)
    glitch_cuts = [round(x, 3) for x in (ev["self"], ev["out"], ev["full0"], ev["tear"] + 0.15, ev["full1"], c0 + 0.1, ev["static_end"] + 0.1)]
    return {"shots": shots, "cuts": cuts, "look_at": look, "wisp": wisp, "stats": hud_stats, "roll": rolls, "cards": cards, "titles": titles,
            "cam": cam, "vhs": vhs, "fx_keys": fx_keys, "stalker": stalker, "holds": holds, "die": die_spec, "lcdGlow": lcd_glow, "rec": rec,
            "objects": [], "glitch_cuts": glitch_cuts, "events": events,
            "flashlight": {"pos": [0.25, 4.2, 0.6], "dir": [-0.05, -1, -0.12], "color": "#ffdcb0", "cone": 26, "range": 8.0,
                           "powerKeys": [[0, 1.05], [round(end, 3), 1.05]]}}


# Улица теплее (натрий), запись — зелёная (rec.fx): место меняется с первого кадра. Экспозиция как в v1.
FX_BASE = {"desat": 0.38, "tint": "#e8dcc2", "contrast": 1.05, "gain": 1.3, "lift": 7, "grain": 12, "vignette": 0.36, "staticBase": 0}


def build_ep2_spec(base: dict, beats: list[dict], words: list[dict], total: float, voice_env: list[float], roll: dict[str, Any],
                   her_len: float, her_env: list[float], her_words: list[dict]) -> dict[str, Any]:
    """Полный spec серии для world.ts. ``her_words`` — слова её записи от начала её аудио (сдвигаются на her0)."""
    spec = {k: deepcopy(v) for k, v in base.items() if k not in ("shots", "stills", "look_at", "cuts", "duration")}
    scene = ep2_scene(beats, total, roll, her_len, her_env, her_words)
    events = scene.pop("events")
    glitch = scene.pop("glitch_cuts")
    fx_keys = scene.pop("fx_keys")
    wisp = {**spec.get("wisp", {}), **scene.pop("wisp"), "voice": voice_env, "voiceFps": spec.get("fps", 30)}
    spec.update(scene)
    spec["wisp"] = wisp
    spec["fx"] = {**spec.get("fx", {}), **FX_BASE, "keys": fx_keys}
    spec["cuts"] = sorted(set(scene["cuts"]) | set(glitch))
    spec["duration"] = round(total, 3)
    mine = [{"word": w["word"], "start": round(w["start"] + events["her0"], 3), "end": round(w["end"] + events["her0"], 3)} for w in her_words]
    spec["captions"] = caption_chunks(sorted(words + mine, key=lambda w: w["start"]))
    spec["captionY"] = 404
    spec["_events"] = {k: round(v, 3) for k, v in events.items()}
    return spec
