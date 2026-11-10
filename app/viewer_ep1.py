"""Серия 1 соло-ДнД «Мира от зрителей» (проект 10, TikTok «Выжить psx»): сценарий огонька → сцена PS1 с «камерой»
героини. Чистые функции; озвучка, рендер, звук и проверки — tools/viewer_ep1.py. Опирается на серию 0
(app/viewer_ep.py: основа улицы, огонёк, окно броска и героини, темп озвучки).

Что в серии (владелец, 10.11):
1. Огонёк подлетает: под серией 0 пусто, никто ничего не выбрал → имени нет, будет «Никто» (ИМЯ: НИКТО в окне).
2. Бросок d20 («удача тумана») решает набор статов, выпадает предмет — КАМЕРА (ПРЕДМЕТ: КАМЕРА).
3. Она снимает себя: картинка — камера с VHS-эффектом (vhs.ts): REC, батарея, штамп 03:17.
4. На плёнке старая запись — она сама, тот же туман, её голос: «не ходи на вокзал…» — и рвётся в помехи.
5. Шаги, Сборщик идёт на звук (виден отчётливо), меню «1 вокзал / 2 спрятаться / 3 своё», «скажите ей имя».
Урок серии 0 (аналитика: половина уходит за 2 секунды): никаких помех в начале — первый кадр это лицо и крупный
крючок (он же обложка), без тёмной каши (экспозиция подтянута).
"""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

from app.viewer_dnd import PRESETS, new_heroine, roll_d20
from app.viewer_ep import _key, _wisp_pos, cue_times, pace, scale_beats, shift_beats, speed_for, stats_spec
from app.viewer_episode import caption_chunks, sentence_times

LEAD = 0.25  # до первой фразы: она уже в кадре, огонёк говорит почти сразу (с)
TAIL = 2.3  # после последней фразы: Сборщик ближе, батарея садится, помехи (с)
ROLL_SEED = "серия 1|под серией 0 нет комментариев"  # открытая строка кубика (зрители могут пересчитать)
HOOK = ["ЕЁ СУДЬБУ", "РЕШАЕШЬ ТЫ"]  # первый кадр = обложка
FINAL = ["ИМЯ И 1 ИЛИ 2", "— В КОММЕНТЫ"]
CHOICE = ["ЧТО ДЕЛАТЬ?", "1 — НА ВОКЗАЛ", "2 — СПРЯТАТЬСЯ", "3 — СВОЁ"]
CAMERA_ITEM = {"id": "camcorder", "title": "КАМЕРА", "charges": None}
# Её голос на старой записи (бесплатный Gemini, не голос огонька Enceladus и не рассказчица Aoede).
HER_VOICE = {"provider": "gemini", "voice": "Leda", "style": ""}
HER_TEXT = "Если ты это смотришь… не ходи на вокзал…"
STAMP = "03:17"  # часы и минуты стоят, секунды идут
DATE = "09 НОЯ"

# Паузы перед фразой роли (с): бросок крутится в тишине, запись играет её голосом — пауза под неё (react: см. ep1_beats).
PAUSE_BEFORE = {"roll": 0.45, "item": 1.8, "cam": 0.7, "old": 0.5, "steps": 0.8, "choice": 0.5}


def ep1_roll() -> dict[str, Any]:
    """Бросок на создание героини: d20 из открытой строки → набор статов (1–7, 8–14, 15–20 → наборы 1, 2, 3)."""
    die = roll_d20(ROLL_SEED)
    preset = (die - 1) // 7 + 1
    hero = new_heroine("Никто", preset, 1)
    hero["items"] = [dict(CAMERA_ITEM)]
    hero["episode"] = 1
    return {"seed": ROLL_SEED, "die": die, "preset": preset, "heroine": hero}


def ep1_script(roll: dict[str, Any]) -> list[dict]:
    """Реплики огонька (без реплики её старой записи — HER_TEXT, другой голос). ``cues`` — слова-метки событий."""
    say = PRESETS[roll["preset"]]["say"].capitalize()
    return [
        {"beat": "intro", "text": "Эй. Под прошлой серией — ни одного комментария."},
        {"beat": "intro", "text": "Значит, и имени у тебя нет. Будешь Никто.", "cues": ["никто"]},
        {"beat": "roll", "text": "Остальное решит кубик."},
        {"beat": "item", "text": f"{say}. И вот что выпало — камера!", "cues": ["выпало", "камера"]},
        {"beat": "cam", "text": "Включай. Снимай себя — вдруг что-то вспомнишь."},
        {"beat": "old", "text": "Стой. На плёнке уже что-то есть."},
        {"beat": "react", "text": "Это… ты? Когда ты успела?"},
        {"beat": "steps", "text": "Тихо. Слышишь? Шаги."},
        {"beat": "choice", "text": "На вокзал, спрятаться или своё? И скажите ей имя.", "cues": ["вокзал", "спрятаться", "свое"]},
    ]


def ep1_beats(script: list[dict], words: list[dict], her_len: float, lead: float = LEAD) -> tuple[list[dict], float, list[dict]]:
    """Слова озвучки → фразы → темп с паузами серии (под запись — her_len + 1.5 перед «Это… ты?») → сдвиг на lead."""
    before = {**PAUSE_BEFORE, "react": her_len + 1.5}
    segs, paced = pace(sentence_times(script, words), before=before)
    k = speed_for(segs[-1]["at"] + segs[-1]["src"][1] - segs[-1]["src"][0], lead)
    return segs, k, shift_beats(scale_beats(paced, k), lead)


def her_times(beats: list[dict], her_len: float) -> tuple[float, float, float, float]:
    """Когда она нажимает «воспроизвести» (old0), её голос начинается/кончается (her0, her1), запись рвётся — прямой эфир (old1):
    всё привязано к началу фразы «Это… ты?» — пауза перед ней (ep1_beats) рассчитана на длину её записи."""
    react = next(b for b in beats if b["beat"] == "react")
    old1 = react["t0"] - 0.35
    old0 = old1 - 0.45 - her_len - 0.25
    return old0, old0 + 0.25, old0 + 0.25 + her_len, old1


def ep1_scene(beats: list[dict], total: float, roll: dict[str, Any], her_len: float, her_env: list[float]) -> dict[str, Any]:
    """Планы, огонёк, интерфейс, камера (VHS), Сборщик и помехи серии 1 по времени фраз (от начала ролика)."""
    by: dict[str, list[dict]] = {}
    for b in beats:
        by.setdefault(b["beat"], []).append(b)
    first = lambda k: by[k][0]  # noqa: E731
    hero = roll["heroine"]
    intro2 = by["intro"][1]
    name_c = cue_times(intro2)[0] or intro2["t0"] + 1.4
    item = first("item")
    drop_c, cam_c = [c or item["t0"] + 1.0 * (i + 1) for i, c in enumerate(cue_times(item))]
    t_roll = first("roll")["t0"] - 0.2
    t_roll_end = item["t0"] - 0.25
    t_cam = first("cam")["t0"] - 0.15  # она подняла камеру: склейка в вид с камеры
    old0, her0, her1, old1 = her_times(beats, her_len)
    react = first("react")
    steps = first("steps")
    choice = first("choice")
    c0 = steps["t0"] - 0.3  # Сборщик выходит из тумана
    say_c = next((w["start"] for w in choice["words"] if "скажите" in w["word"].casefold()), choice["t1"] - 1.2)
    t_final = say_c - 0.1
    end = total
    t_static_end = end - 0.4
    pre_cam = old0 - 0.12  # склейка на запись — чуть раньше нажатия (чтобы кадр успел смениться)

    shots = [
        # Первый кадр: лицо крупно, чуть сбоку; медленный наезд. Это и обложка.
        {"name": "лицо", "t0": 0, "t1": intro2["t0"] - 0.1, "minHeight": 0.0, "camKeys": [
            _key(0, 7, 1.8, 1.47, 1.38, 32), _key(intro2["t0"] - 0.1, 3, 1.55, 1.46, 1.38, 32)]},
        # Огонёк облетает, над ней загорается окно с именем.
        {"name": "имя", "t0": intro2["t0"] - 0.1, "t1": t_roll - 0.1, "minHeight": 0.2, "camKeys": [
            _key(0, -24, 2.5, 1.5, 1.28, 36), _key(t_roll - intro2["t0"], -14, 2.3, 1.5, 1.3, 36)]},
        # Бросок: она в нижней половине, окно кубика над ней.
        {"name": "бросок", "t0": t_roll - 0.1, "t1": drop_c - 0.15, "minHeight": 0.12, "camKeys": [
            _key(0, 10, 2.9, 1.7, 1.9, 40), _key(drop_c - t_roll, 6, 2.7, 1.68, 1.88, 40)]},
        # Предмет парит перед ней.
        {"name": "предмет", "t0": drop_c - 0.15, "t1": t_cam, "minHeight": 0.2, "camKeys": [
            _key(0, 14, 2.4, 1.35, 1.25, 40), _key(t_cam - drop_c, 6, 2.1, 1.38, 1.27, 40)]},
        # Вид с камеры: она держит её на уровне груди перед собой, широкий объектив, горизонт по центру.
        {"name": "камера", "t0": t_cam, "t1": pre_cam, "minHeight": 0.0, "camKeys": [
            _key(0, 0, 0.95, 1.35, 1.3, 64), _key(pre_cam - t_cam, -3, 0.85, 1.35, 1.3, 62)]},
        # Старая запись: другой ракурс, на плёнке она ближе, тусклее.
        {"name": "запись", "t0": pre_cam, "t1": old1, "minHeight": 0.0, "camKeys": [
            _key(0, -12, 1.1, 1.36, 1.3, 68), _key(old1 - pre_cam, -9, 1.0, 1.36, 1.31, 66)]},
        # Снова прямой эфир; когда слышны шаги, камера отъезжает — слева из тумана идёт Сборщик.
        {"name": "эфир", "t0": old1, "t1": end + 1, "minHeight": 0.0, "camKeys": [
            _key(0, 2, 0.95, 1.35, 1.3, 64), _key(c0 - 0.2 - old1, 0, 0.9, 1.35, 1.3, 64),
            _key(c0 + 1.9 - old1, 0, 2.4, 1.45, 1.25, 52), _key(end - old1, 0, 2.0, 1.45, 1.25, 52)]},
    ]
    cuts = [round(s["t0"], 3) for s in shots[1:]]

    # Огонёк: уже в кадре у её лица (обложка), подлетает ближе, висит справа, у предмета, у камеры, у плеча.
    keys = [{"t": 0, "pos": [0.42, 1.78, 0.6]}, {"t": 0.9, "pos": [0.22, 1.7, 0.3]},
            {"t": round(intro2["t0"] - 0.1, 3), "pos": [0.24, 1.66, 0.3]}]
    # Перед ней из стороны в сторону (не за спину: иначе она провожает его взглядом и поворачивается затылком).
    n = max(2, int((t_roll - 0.1 - intro2["t0"] - 0.2) / 0.35) + 1)
    for i in range(n):
        t = intro2["t0"] + 0.2 + (t_roll - 0.1 - intro2["t0"] - 0.2) * i / (n - 1)
        a = 0.4 + 0.9 * (t - intro2["t0"])
        keys.append({"t": round(t, 3), "pos": [round(0.65 * math.sin(a), 3), round(1.55 + 0.15 * math.sin(a * 0.5 + 1.0), 3),
                                               round(0.55 + 0.2 * math.cos(a), 3)]})
    keys += [{"t": round(t_roll + 0.3, 3), "pos": [0.5, 1.75, 0.4]}, {"t": round(drop_c - 0.3, 3), "pos": [0.5, 1.7, 0.4]},
             {"t": round(drop_c + 0.4, 3), "pos": [0.55, 1.45, 0.8]}, {"t": round(t_cam - 0.2, 3), "pos": [0.5, 1.5, 0.75]},
             {"t": round(t_cam + 0.3, 3), "pos": [0.34, 1.52, 0.38]}, {"t": round(pre_cam, 3), "pos": [0.36, 1.55, 0.36]},
             {"t": round(old1 + 0.1, 3), "pos": [0.34, 1.52, 0.4]}, {"t": round(c0, 3), "pos": [0.33, 1.58, 0.4]},
             {"t": round(end, 3), "pos": [0.28, 1.5, 0.45]}]
    keys.sort(key=lambda k: k["t"])
    wisp = {"appear": -5, "fadeIn": 0.5, "keys": keys, "dim": [[round(c0 + 0.2, 3), round(end + 1, 3), 0.62]]}

    # Взгляд: огонёк / камера; на записи — прямо в объектив; при шагах оглядывается на Сборщика.
    cam_pt = [0.0, 1.47, 1.0]
    look = [{"t": -5, "target": cam_pt, "head": 0.6}, {"t": round(intro2["t0"] + 0.3, 3), "target": _wisp_pos(keys, intro2["t0"] + 0.6), "head": 0.7}]
    t = intro2["t0"] + 0.9
    while t < t_roll:
        look.append({"t": round(t, 3), "target": _wisp_pos(keys, t + 0.25), "head": 0.75})
        t += 0.6
    look += [{"t": round(t_roll + 0.5, 3), "target": [0.3, 2.0, 3.0], "head": 0.5},
             {"t": round(drop_c, 3), "target": [0.2, 1.2, 0.6], "head": 0.8},
             {"t": round(t_cam + 0.1, 3), "target": cam_pt, "head": 0.6},
             {"t": round(first("old")["t0"] + 0.2, 3), "target": [0.45, 1.5, 0.4], "head": 0.7},
             {"t": round(old0 + 0.1, 3), "target": cam_pt, "head": 0.55},
             {"t": round(her1 - 0.1, 3), "target": [-1.5, 1.2, 1.6], "head": 0.5},
             {"t": round(old1 + 0.15, 3), "target": [0.3, 1.55, 0.4], "head": 0.7},
             {"t": round(steps["t0"] + 0.2, 3), "target": [-2.5, 1.4, -0.8], "head": 0.55},
             {"t": round(choice["t0"] + 0.4, 3), "target": cam_pt, "head": 0.55}]

    hud_stats = stats_spec(hero, name_c - 0.05)
    hud_stats.update({"nameLabel": "ИМЯ", "invLabel": "ПРЕДМЕТ", "statsT": round(item["t0"] - 0.1, 3), "invT": round(cam_c - 0.05, 3),
                      "t1": round(t_cam - 0.05, 3), "w": 170})
    die = roll["die"]
    p = PRESETS[roll["preset"]]
    rolls = {"t0": round(t_roll, 3), "t1": round(t_roll_end, 3), "stat": "УДАЧА ТУМАНА", "action": "КТО ТЫ ТАКАЯ?", "die": die,
             "bonus": 0, "dc": 0, "spin": 1.4, "y": 100, "custom": [f"ВЫПАЛО {die}", p["title"]]}
    cards = [{"t0": round(drop_c, 3), "t1": round(t_cam - 0.3, 3), "lines": ["НАЙДЕН ПРЕДМЕТ:", "СТАРАЯ КАМЕРА"], "accent": [1], "y": 84},
             {"t0": round(choice["t0"] - 0.1, 3), "lines": CHOICE, "accent": [0], "y": 50}]
    titles = [{"t0": 0, "t1": 2.6, "lines": HOOK, "colors": ["#f8f8f8", "#f8d848"], "y": 52, "px": 24},
              {"t0": round(t_final, 3), "t1": round(t_static_end, 3), "lines": FINAL, "colors": ["#f8d848", "#f8f8f8"], "y": 300, "px": 16}]

    sec0 = 41
    cam = [{"t0": round(t_cam, 3), "t1": round(old0, 3), "mode": "rec", "stamp": STAMP, "sec0": sec0, "date": DATE},
           {"t0": round(old0, 3), "t1": round(old1, 3), "mode": "play", "stamp": STAMP, "sec0": 4, "date": DATE},
           {"t0": round(old1, 3), "mode": "rec", "stamp": STAMP, "sec0": sec0 + int(old0 - t_cam) + 2, "date": DATE,
            "lowAt": round(end - 4.0, 3)}]

    vhs = {"keys": [[0, 0], [round(t_cam - 0.01, 3), 0], [round(t_cam, 3), 1.0], [round(old0 - 0.01, 3), 1.0], [round(old0, 3), 1.9],
                    [round(old1 - 0.01, 3), 1.9], [round(old1, 3), 1.0], [round(end - 0.5, 3), 1.15], [round(end, 3), 1.5]],
           "hunts": [round(t_cam + 0.5, 3), round(old1 + 0.7, 3), round(c0, 3)],
           "tears": [[round(her1 - 0.05, 3), round(old1, 3)], [round(c0 - 0.05, 3), round(c0 + 0.3, 3)],
                     [round(t_static_end - 0.1, 3), round(end + 1, 3)]],
           "shake": 0.012}

    spike = lambda t, a: [{"t": round(t - 0.05, 3), "static": 0.0}, {"t": round(t, 3), "static": a},  # noqa: E731
                          {"t": round(t + 0.1, 3), "static": a * 0.5}, {"t": round(t + 0.2, 3), "static": 0.0}]
    fx_keys = [{"t": 0, "black": 0.0, "static": 0.0}]
    fx_keys += spike(t_cam, 0.55) + spike(pre_cam, 0.7)
    fx_keys += [{"t": round(her1 + 0.1, 3), "static": 0.0}, {"t": round(her1 + 0.3, 3), "static": 0.95},
                {"t": round(old1 - 0.05, 3), "static": 0.9}, {"t": round(old1 + 0.12, 3), "static": 0.0}]
    fx_keys += spike(c0 + 0.1, 0.35)
    fx_keys += [{"t": round(t_static_end - 0.05, 3), "static": 0.0}, {"t": round(t_static_end + 0.1, 3), "static": 1.0},
                {"t": round(end + 1, 3), "static": 1.0}]
    fx_keys.sort(key=lambda k: k["t"])

    # Сборщик: выходит из тумана слева-сзади и идёт на неё — крупный, виден весь рост всё время (без мигания).
    stalker = {"shape": "collector", "pos": [-0.95, 0.0, -4.6], "move": [-0.75, 0.0, -2.8], "face": [0, 3.0], "height": 2.8, "glow": "#9aa4a4",
               "show": [[round(c0, 3), round(end + 1, 3)]]}
    objects = [{"kind": "camcorder", "pos": [0.3, 0.45], "y": 1.3, "bob": 0.03, "rot": 0.25, "sway": 0.4, "scale": 1.7, "spawn": round(drop_c, 3),
                "until": round(t_cam, 3)}]
    mouth = [{"t0": round(her0, 3), "t1": round(her1, 3), "voice": her_env, "fps": 30}]
    events = {"wisp": 0.2, "roll": t_roll, "landed": t_roll + 1.7, "drop": drop_c, "cam": t_cam, "old0": old0, "her0": her0,
              "her1": her1, "old1": old1, "c0": c0, "steps": steps["t0"], "choice": choice["t0"], "final": t_final,
              "static_end": t_static_end, "name": name_c, "item": cam_c}
    return {"shots": shots, "cuts": cuts, "look_at": look, "wisp": wisp, "stats": hud_stats, "roll": rolls, "cards": cards,
            "titles": titles, "cam": cam, "vhs": vhs, "fx_keys": fx_keys, "stalker": stalker, "objects": objects, "mouth": mouth,
            "glitch_cuts": [round(x, 3) for x in (t_cam, pre_cam, her1 + 0.3, old1, c0 + 0.1, t_static_end + 0.1)],
            "events": events,
            # Холодный свет сверху держит лицо читаемым весь ролик (в серии 0 лицо тонуло в темноте).
            "flashlight": {"pos": [0.25, 4.2, 0.6], "dir": [-0.05, -1, -0.12], "color": "#d0dae4", "cone": 26, "range": 8.0,
                           "powerKeys": [[0, 1.05], [round(end, 3), 1.05]]}}


# Экспозиция выше, чем в серии 0 (она была тёмной и мутной): подъём яркости и чёрного, меньше виньетка.
FX_BASE = {"desat": 0.5, "tint": "#dcdfd2", "contrast": 1.05, "gain": 1.28, "lift": 7, "grain": 12, "vignette": 0.36, "staticBase": 0}


def build_ep1_spec(base: dict, beats: list[dict], words: list[dict], total: float, voice_env: list[float], roll: dict[str, Any],
                   her_len: float, her_env: list[float], her_words: list[dict]) -> dict[str, Any]:
    """Полный spec серии 1 для world.ts. ``beats`` и ``words`` — слова огонька от начала ролика; ``her_words`` — слова её
    записи от начала её аудио (сдвигаются на her0)."""
    spec = {k: deepcopy(v) for k, v in base.items() if k not in ("shots", "stills", "look_at", "cuts", "duration")}
    scene = ep1_scene(beats, total, roll, her_len, her_env)
    events = scene.pop("events")
    glitch = scene.pop("glitch_cuts")
    fx_keys = scene.pop("fx_keys")
    wisp = {**spec.get("wisp", {}), **scene.pop("wisp"), "voice": voice_env, "voiceFps": spec.get("fps", 30)}
    spec.update(scene)
    spec["wisp"] = wisp
    spec["fx"] = {**spec.get("fx", {}), **FX_BASE, "keys": fx_keys}
    spec["cuts"] = sorted(set(scene["cuts"]) | set(glitch))
    spec["duration"] = round(total, 3)
    her0 = her_times(beats, her_len)[1]
    mine = [{"word": w["word"], "start": round(w["start"] + her0, 3), "end": round(w["end"] + her0, 3)} for w in her_words]
    spec["captions"] = caption_chunks(sorted(words + mine, key=lambda w: w["start"]))
    spec["captionY"] = 404
    spec["_events"] = {k: round(v, 3) for k, v in events.items()}
    return spec
