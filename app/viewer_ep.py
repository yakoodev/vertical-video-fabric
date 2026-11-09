"""Серии соло-ДнД «Мира от зрителей» (проект 10): сценарий огонька → сцена PS1 (spec для node/vrm/src/psx/world.ts).
Чистые функции; озвучка, рендер, звук и проверки — tools/viewer_ep.py. Дизайн — docs/design/viewer-turn-game.md.

Переиспользуемое для следующих серий:
* ``SCENES`` — пресеты декораций (основа ``node/vrm/examples/psx_street.json`` + правки);
* ``wisp_orbit`` / ``wisp_keys`` — путь огонька (подлёт из тумана, круги вокруг героини, зависание у меню);
* ``creation_menu`` — меню создания персонажа из правил (app/viewer_dnd.py);
* ``roll_spec`` / ``stats_spec`` — окно броска d20 и окно героини для серий с риском;
* ``envelope`` — огибающая голоса → «огонёк говорит» (пульс света и размера).

Серия 0 «Вы попали в мир» (``ep0_script`` → ``ep0_scene``): помехи и темнота → туман, она лежит на асфальте и
просыпается (лёжа → сидя → стоя), из тумана подлетает огонёк и кружит, обращается к зрителям, меню создания
персонажа (имя / статы / предмет; курсор ходит за голосом), призыв, тень в тумане за ней и всплеск помех.
"""

from __future__ import annotations

import json
import math
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.viewer_dnd import ITEMS, PRESETS, SHORT, preset_line
from app.viewer_episode import caption_chunks, sentence_times

ROOT = Path(__file__).resolve().parent.parent
SCENES: dict[str, dict[str, Any]] = {
    # Туманная улица: разбитая дорога, рабица, профнастил, машины, мёртвые фонари, пепел.
    "street": {"base": "node/vrm/examples/psx_street.json", "overrides": {}},
    # Та же улица глубже в ночь: туман темнее, меньше пепла — для серий «после заката».
    "street_night": {"base": "node/vrm/examples/psx_street.json",
                     "overrides": {"psx": {"fog": {"color": "#33352f", "near": 0.6, "far": 9.0}}, "street": {"ash": 600}}},
}
# Голос огонька: бесплатный Gemini, не Aoede (Aoede — рассказчица Втубики).
WISP_VOICE = {"provider": "gemini", "voice": "Enceladus",
              # Без указания: gemini-3.8-flash-tts зачитывает его вслух (и русское, и английское — проверено 09.11);
              # Enceladus и так с придыханием, загадочный.
              "style": ""}
LEAD = 3.0  # сколько до первой фразы: помехи → туман → она лежит (с)
TAIL = 2.1  # после последней фразы: тень в тумане и помехи (с)
CTA_LINES = ["ПИШИТЕ В КОММЕНТАХ:", "ИМЯ, НОМЕР СТАТОВ", "И НОМЕР ПРЕДМЕТА", "НАПРИМЕР: ЛИСА 2 3", "",
             "САМЫЕ ЗАЛАЙКАННЫЕ —", "И ОНА ПОЙДЁТ ДАЛЬШЕ"]


def scene_base(name: str = "street") -> dict[str, Any]:
    """Основа spec декорации: JSON пресета + правки (глубокое слияние словарей)."""
    p = SCENES[name]
    base = json.loads((ROOT / p["base"]).read_text("utf-8"))
    return _merge(base, p["overrides"])


def _merge(a: dict, b: dict) -> dict:
    out = deepcopy(a)
    for k, v in b.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else deepcopy(v)
    return out


# --------------------------------------------------------------------------- сценарий серии 0

def ep0_script() -> list[dict]:
    """Реплики огонька: разговорно, без заголовков и обещаний. ``cue`` — слова, к которым привязаны события."""
    return [
        {"beat": "wake", "text": "Эй… Просыпайся. На асфальте тут долго не лежат."},
        {"beat": "wake", "text": "Вот так. Вставай."},
        {"beat": "wisp", "text": "Не пугайся, это я, огонёк."},
        {"beat": "wisp", "text": "Свечу, болтаю и немножко знаю этот город."},
        {"beat": "lore", "text": "Тут всегда туман, и всегда сыплется пепел."},
        {"beat": "lore", "text": "А ты ничего не помнишь. Даже имени, да?"},
        {"beat": "viewer", "text": "Зато на тебя смотрят. Вы, по ту сторону экрана. Привет."},
        {"beat": "rules", "text": "Дальше её ведёте вы: решает самый залайканный комментарий."},
        {"beat": "menu", "text": "Сначала соберём её. Имя придумайте сами.", "cues": ["имя"]},
        {"beat": "stats", "text": "Какая она: тяжёлая рука, тихая тень или холодная голова?",
         "cues": ["тяжелая", "тень", "холодная"]},
        {"beat": "items", "text": "И что у неё с собой: фонарик, ржавый лом или аптечка.",
         "cues": ["фонарик", "лом", "аптечка"]},
        {"beat": "cta", "text": "Пишите имя и два номера. Самые залайканные — и она пойдёт дальше."},
        {"beat": "tease", "text": "Только не тяните. Тут кто-то ходит."},
    ]


def cue_times(beat: dict) -> list[float | None]:
    """Начала слов-меток фразы (по словам озвучки): основа слова без «ё», первое совпадение по порядку."""
    out: list[float | None] = []
    pos = 0
    words = beat["words"]
    for cue in beat.get("cues") or []:
        t = None
        for i in range(pos, len(words)):
            if cue in words[i]["word"].casefold().replace("ё", "е"):
                t, pos = words[i]["start"], i + 1
                break
        out.append(t)
    return out


# Паузы перед фразой (с) по роли: она встаёт под тишину после «Вставай», «привет» зрителям и тень — с паузой.
PAUSE_BEFORE = {"wisp": 1.2, "viewer": 0.55, "menu": 0.45, "tease": 0.65}
PAUSE = 0.32


def pace(beats: list[dict], pause: float = PAUSE, before: dict[str, float] | None = None,
         pad: tuple[float, float] = (0.04, 0.1)) -> tuple[list[dict], list[dict]]:
    """Темп озвучки: фразы подряд с паузой ``pause`` (или ``before[beat]`` перед первой фразой роли) вместо
    паузы модели. Возвращает куски звука ``[{src: [от, до], at}]`` и фразы со сдвинутым временем слов."""
    before = PAUSE_BEFORE if before is None else before
    segs: list[dict] = []
    out: list[dict] = []
    t = 0.0
    prev_beat = None
    for i, b in enumerate(beats):
        a, z = max(0.0, b["t0"] - pad[0]), b["t1"] + pad[1]
        if i:
            gap = before.get(b["beat"], pause) if b["beat"] != prev_beat else pause
            t += gap
        shift = t - a
        segs.append({"src": [round(a, 3), round(z, 3)], "at": round(t, 3)})
        ws = [{**w, "start": round(w["start"] + shift, 3), "end": round(w["end"] + shift, 3)} for w in b["words"]]
        out.append({**b, "t0": round(b["t0"] + shift, 3), "t1": round(b["t1"] + shift, 3), "words": ws,
                    "cue_t": None if b.get("cue_t") is None else round(b["cue_t"] + shift, 3)})
        t += z - a
        prev_beat = b["beat"]
    return segs, out


MAX_TOTAL = 49.5  # длина серии не больше (с): иначе голос ускоряется (atempo, без смены высоты), не больше MAX_SPEED
MAX_SPEED = 1.1


def speed_for(speech: float, lead: float = LEAD, tail: float = TAIL, max_total: float = MAX_TOTAL) -> float:
    """Во сколько ускорить озвучку длиной speech, чтобы серия уложилась в max_total (1.0 — не нужно)."""
    room = max_total - lead - tail
    return round(min(MAX_SPEED, max(1.0, speech / room)), 3)


def scale_beats(beats: list[dict], k: float) -> list[dict]:
    """Время фраз и слов (от начала озвучки) после ускорения в k раз."""
    if k == 1.0:
        return beats
    f = lambda x: None if x is None else round(x / k, 3)  # noqa: E731
    return [{**b, "t0": f(b["t0"]), "t1": f(b["t1"]), "cue_t": f(b.get("cue_t")),
             "words": [{**w, "start": f(w["start"]), "end": f(w["end"])} for w in b["words"]]} for b in beats]


def shift_beats(beats: list[dict], lead: float) -> list[dict]:
    """Время фраз и слов — от начала ролика (озвучка начинается с lead)."""
    out = []
    for b in beats:
        ws = [{**w, "start": round(w["start"] + lead, 3), "end": round(w["end"] + lead, 3)} for w in b["words"]]
        out.append({**b, "t0": round(b["t0"] + lead, 3), "t1": round(b["t1"] + lead, 3), "words": ws,
                    "cue_t": None if b.get("cue_t") is None else round(b["cue_t"] + lead, 3)})
    return out


# --------------------------------------------------------------------------- огонёк

def wisp_orbit(t0: float, t1: float, center: tuple[float, float, float] = (0.0, 1.45, 0.0), radius: float = 0.9,
               speed: float = 1.05, phase: float = 0.6, step: float = 0.35, rise: float = 0.18) -> list[dict]:
    """Ключи кругов вокруг героини (против часовой, если смотреть сверху), высота плавно гуляет на ±rise."""
    keys = []
    n = max(2, int(math.ceil((t1 - t0) / step)) + 1)
    for i in range(n):
        t = t0 + (t1 - t0) * i / (n - 1)
        a = phase + speed * (t - t0)
        keys.append({"t": round(t, 3), "pos": [round(center[0] + radius * math.sin(a), 3),
                                               round(center[1] + rise * math.sin(a * 0.5 + 1.0), 3),
                                               round(center[2] + radius * math.cos(a), 3)]})
    return keys


def envelope(samples: list[float], rate: int, fps: int = 30, lead: float = 0.0, attack: float = 0.5,
             release: float = 0.18) -> list[float]:
    """Огибающая громкости голоса по кадрам видео (0..1): RMS окна кадра, норма по 95-му перцентилю, быстрая атака
    и медленный спад (пламя не мигает на каждом слоге). ``lead`` — тишина в начале ролика (с)."""
    per = max(1, int(rate / fps))
    rms = []
    for i in range(0, len(samples), per):
        w = samples[i:i + per]
        rms.append(math.sqrt(sum(x * x for x in w) / max(1, len(w))))
    if not rms:
        return []
    ref = sorted(rms)[int(0.95 * (len(rms) - 1))] or 1.0
    out, v = [0.0] * int(round(lead * fps)), 0.0
    for r in rms:
        x = min(1.0, r / ref)
        v = v + (x - v) * (attack if x > v else release)
        out.append(round(v, 3))
    return out


# --------------------------------------------------------------------------- интерфейс

def creation_menu(t0: float, name_t: float, stats_t: float, items_t: float, focus: list[dict], t1: float | None = None) -> dict:
    """Меню создания персонажа (menu.ts drawCreation) из правил: наборы статов и предметы с номерами."""
    return {
        "t0": round(t0, 3), **({"t1": round(t1, 3)} if t1 is not None else {}), "title": "СОЗДАНИЕ ПЕРСОНАЖА", "y": 34,
        "name": {"label": "ИМЯ", "value": "???", "hint": "напишите в комментах", "t": round(name_t, 3)},
        "groups": [
            {"label": "СТАТЫ", "t": round(stats_t, 3),
             "items": [{"n": n, "title": p["title"], "sub": preset_line(n)} for n, p in PRESETS.items()]},
            {"label": "СТАРТОВЫЙ ПРЕДМЕТ", "t": round(items_t, 3),
             "items": [{"n": n, "title": it["title"], "sub": it["sub"]} for n, it in ITEMS.items()]},
        ],
        "focus": focus,
    }


def roll_spec(result: dict, t0: float, action: str = "", y: int = 120) -> dict:
    """Окно броска d20 (menu.ts drawRoll) по итогу app.viewer_dnd.resolve."""
    return {"t0": round(t0, 3), "stat": result["stat"], "action": action.upper()[:24], "die": int(result["die"]),
            "bonus": int(result["bonus"]), "dc": int(result["dc"]), "spin": 1.4, "y": y}


def stats_spec(heroine: dict, t: float = 0.0) -> dict:
    """Окно героини (menu.ts drawStats): имя, HP, статы, предметы."""
    inv = [f"{i['title']}" + (f"×{i['charges']}" if i.get("charges") else "") for i in heroine.get("items") or []]
    return {"t": t, "name": heroine["name"].upper(), "hp": int(heroine["hp"]), "hpMax": int(heroine["hp_max"]),
            "stats": [[SHORT[k], int(v)] for k, v in heroine["stats"].items()], "inventory": inv}


# --------------------------------------------------------------------------- сцена серии 0

def _key(t, yaw, dist, height, target, fov=34):
    return {"t": round(t, 3), "yaw": yaw, "dist": dist, "height": height, "target": target, "fov": fov}


def ep0_scene(beats: list[dict], total: float) -> dict[str, Any]:
    """Планы, движение, огонёк, интерфейс и помехи серии 0 по времени фраз (уже от начала ролика)."""
    by: dict[str, list[dict]] = {}
    for b in beats:
        by.setdefault(b["beat"], []).append(b)
    first = lambda k: by[k][0]  # noqa: E731
    w1, w2 = by["wake"][0], by["wake"][1]
    t_up = w1["t1"] + 0.15  # садится к концу «…долго не лежат»
    up_dur, stand_dur = 2.0, 1.7
    t_stand = max(t_up + up_dur + 0.2, w2["t1"] - 0.4)
    t_wake_cut = w1["t1"] - 0.25  # склейка: сверху на неё → низкий ракурс, когда начинает садиться
    t_wisp = max(first("wisp")["t0"] - 0.2, t_stand + stand_dur - 0.1)  # голос огонька — ещё за кадром
    t_lore = first("lore")["t0"] - 0.15
    t_view = first("viewer")["t0"] - 0.1
    t_rules = first("rules")["t0"] - 0.1
    t_menu = first("menu")["t0"] - 0.15
    t_cta = first("cta")["t0"] - 0.1
    t_tease = first("tease")["t0"] - 0.15
    end = total
    name_c = cue_times(first("menu"))[0] or first("menu")["t0"] + 1.2
    st_c = [c or first("stats")["t0"] + 0.8 * (i + 1) for i, c in enumerate(cue_times(first("stats")))]
    it_c = [c or first("items")["t0"] + 0.8 * (i + 1) for i, c in enumerate(cue_times(first("items")))]
    # Тень в тумане: два мелькания после «кто-то ходит» и последнее — под всплеск помех в конце.
    g1 = first("tease")["t1"] + 0.15
    g2 = g1 + 0.55
    t_static_end = end - 0.75

    shots = [
        # Темнота и помехи → туман: она лежит (голова у z≈−0.1, ноги к +Z), камера сверху со стороны груди —
        # лицо сверху кадра, головой вверх; медленно опускается к лицу.
        {"name": "лежит", "t0": 0, "t1": t_wake_cut, "minHeight": 0.04, "camKeys": [
            _key(0, 8, 0.5, 2.4, [0.0, 0.15, 0.1], 46), _key(t_wake_cut, 4, 0.32, 1.3, [0.0, 0.15, 0.0], 42)]},
        # Низкий ракурс спереди-сбоку: садится, встаёт; камера поднимается вместе с ней.
        {"name": "встаёт", "t0": t_wake_cut, "t1": t_wisp, "minHeight": 0.1, "camKeys": [
            _key(0, 38, 3.0, 0.55, [0.0, 0.35, -0.3], 40), _key(t_stand - t_wake_cut, 30, 3.1, 0.75, [0.0, 0.6, -0.15], 40),
            _key(t_wisp - t_wake_cut, 22, 3.2, 1.25, [0.0, 1.0, 0.0], 38)]},
        # Через плечо: улица уходит в туман, оттуда подлетает огонёк.
        {"name": "огонёк", "t0": t_wisp, "t1": t_lore, "minHeight": 0.12, "camKeys": [
            _key(0, 184, 3.2, 1.65, [0.2, 1.2, 1.6], 38), _key(t_lore - t_wisp, 186, 2.8, 1.6, [0.25, 1.25, 1.3], 38)]},
        # Средний план, медленный облёт: огонёк кружит, она следит за ним.
        {"name": "кружит", "t0": t_lore, "t1": t_view, "minHeight": 0.2, "camKeys": [
            _key(0, 40, 3.4, 1.4, 1.05, 36), _key(t_view - t_lore, -10, 3.1, 1.35, 1.1, 36)]},
        # Крупно: смотрит в камеру — «на тебя смотрят».
        {"name": "зрителям", "t0": t_view, "t1": t_rules, "minHeight": 0.0, "camKeys": [
            _key(0, 6, 1.55, 1.44, 1.36, 32), _key(t_rules - t_view, 0, 1.38, 1.42, 1.37, 32)]},
        {"name": "правила", "t0": t_rules, "t1": t_menu, "minHeight": 0.2, "camKeys": [
            _key(0, -28, 2.6, 1.2, 1.05, 36), _key(t_menu - t_rules, -18, 2.5, 1.25, 1.08, 36)]},
        # Меню: она в нижней трети, окно сверху, огонёк справа у края окна.
        {"name": "меню", "t0": t_menu, "t1": t_tease, "minHeight": 0.12, "camKeys": [
            _key(0, 10, 2.8, 1.75, 1.98, 40), _key(t_tease - t_menu, 4, 2.6, 1.72, 1.95, 40)]},
        # Тень в тумане за ней.
        {"name": "кто-то", "t0": t_tease, "t1": end + 1, "minHeight": 0.15, "camKeys": [
            _key(0, 8, 3.3, 1.35, [0.0, 1.15, -1.0], 36), _key(end - t_tease, 4, 2.9, 1.35, [0.0, 1.2, -1.0], 36)]},
    ]
    cuts = [round(s["t0"], 3) for s in shots[1:]]

    # Огонёк: из тумана по улице к ней, круги, крупный план у лица, зависание справа у меню, жмётся к ней в конце.
    appear = t_wisp - 0.9
    keys = [{"t": 0, "pos": [0.4, 1.8, 13.0]}, {"t": round(appear, 3), "pos": [0.4, 1.8, 11.5]},
            {"t": round(t_wisp + 1.6, 3), "pos": [0.45, 1.65, 3.0]}, {"t": round(t_lore - 0.3, 3), "pos": [0.35, 1.65, 1.2]}]
    keys += wisp_orbit(t_lore, t_view - 0.4, radius=0.62, phase=0.5)
    keys += [{"t": round(t_view + 0.4, 3), "pos": [0.1, 1.6, 0.36]}, {"t": round(t_rules - 0.2, 3), "pos": [0.09, 1.64, 0.36]},
             {"t": round(t_rules + 1.0, 3), "pos": [-0.38, 1.5, 0.5]}, {"t": round(t_menu - 0.1, 3), "pos": [-0.3, 1.6, 0.5]},
             {"t": round(t_menu + 0.9, 3), "pos": [0.36, 1.7, 0.4]}, {"t": round(t_tease - 0.2, 3), "pos": [0.38, 1.66, 0.4]},
             {"t": round(g1 - 0.3, 3), "pos": [-0.3, 1.55, 0.45]}, {"t": round(end, 3), "pos": [-0.32, 1.45, 0.5]}]
    keys.sort(key=lambda k: k["t"])
    wisp = {"appear": round(appear, 3), "fadeIn": 1.4, "keys": keys,
            "dim": [[round(g1 - 0.05, 3), round(end + 1, 3), 0.55]]}

    # Взгляд: лёжа — нет; встаёт — вниз-вперёд; дальше — на огонёк (с запаздыванием) и в камеру.
    look = [{"t": -5, "target": [0, 1.2, 2.5], "head": 0.6}, {"t": round(t_stand, 3), "target": [0.3, 1.2, 2.5], "head": 0.6}]
    t = t_wisp + 0.3
    while t < t_view:
        p = _wisp_pos(keys, t + 0.25)
        look.append({"t": round(t, 3), "target": p, "head": 0.75})
        t += 0.6
    cam_view = [0.15, 1.42, 1.3]
    look += [{"t": round(t_view + 0.5, 3), "target": cam_view, "head": 0.55},
             {"t": round(t_rules + 0.6, 3), "target": [-0.38, 1.5, 0.5], "head": 0.6},
             {"t": round(t_menu + 0.4, 3), "target": [0.4, 2.0, 3.0], "head": 0.5},
             {"t": round(t_cta + 0.3, 3), "target": [0.0, 1.6, 3.2], "head": 0.5},
             # Услышала шаги — оглядывается через плечо туда, где мелькнула тень.
             {"t": round(g1 + 0.35, 3), "target": [-2.2, 1.3, -4.5], "head": 0.85},
             {"t": round(g2 + 0.7, 3), "target": [0.0, 1.45, 3.0], "head": 0.6}]

    focus = [{"t": round(name_c, 3), "group": "name"}]
    focus += [{"t": round(c, 3), "group": 0, "item": i} for i, c in enumerate(st_c)]
    focus += [{"t": round(c, 3), "group": 1, "item": i} for i, c in enumerate(it_c)]
    menu = creation_menu(t_menu + 0.25, name_c - 0.3, first("stats")["t0"] - 0.1, first("items")["t0"] - 0.1, focus,
                         t1=t_cta)
    # Призыв висит, пока огонёк договаривает, и уходит перед тенью: её не должно закрывать окно.
    cards = [{"t0": round(t_cta + 0.2, 3), "t1": round(g1 - 0.05, 3), "lines": CTA_LINES, "accent": [3, 5, 6], "y": 64}]

    spike = lambda t, a: [{"t": round(t - 0.05, 3), "static": 0.03}, {"t": round(t, 3), "static": a},  # noqa: E731
                          {"t": round(t + 0.1, 3), "static": a * 0.5}, {"t": round(t + 0.2, 3), "static": 0.03}]
    fx_keys = [{"t": 0, "black": 1.0, "static": 1.0}, {"t": 1.0, "static": 0.95}, {"t": 1.1, "black": 1.0},
               {"t": 2.5, "black": 0.0, "static": 0.18}, {"t": 2.9, "static": 0.05}]
    fx_keys += spike(3.05, 0.45)
    fx_keys += spike(g1 + 0.2, 0.6) + spike(g2 + 0.14, 0.5)
    fx_keys += [{"t": round(t_static_end - 0.05, 3), "static": 0.03}, {"t": round(t_static_end + 0.1, 3), "static": 1.0},
                {"t": round(end + 1, 3), "static": 1.0}]
    fx_keys.sort(key=lambda k: k["t"])

    return {
        "shots": shots, "cuts": cuts, "look_at": look,
        "actions": [{"id": "sit", "t": -10, "dur": 0.01}, {"id": "lie", "t": -10},
                    {"id": "situp", "t": round(t_up, 3), "dur": up_dur}, {"id": "stand", "t": round(t_stand, 3), "dur": stand_dur}],
        "eyesClosed": [[-10, round(w1["t0"] + 0.9, 3)]],
        "wisp": wisp, "creation": menu, "cards": cards, "fx_keys": fx_keys,
        # Идёт поперёк улицы у неё за спиной, справа от неё в кадре (узкий вертикальный кадр: левее — за ней).
        "stalker": {"shape": "collector", "pos": [0.85, 0.0, -9.2], "move": [0.05, 0.0, -8.8], "face": [-3, -6], "height": 2.8,
                    "show": [[round(g1, 3), round(g1 + 0.3, 3)], [round(g2, 3), round(g2 + 0.2, 3)],
                             [round(t_static_end - 0.1, 3), round(t_static_end + 0.08, 3)]]},
        # 0.05: первые кадры — сплошные помехи, каждый кадр — новый снег (scdet видит в них склейку).
        "glitch_cuts": [round(x, 3) for x in (0.05, 3.05, g1 + 0.2, g2 + 0.14, t_static_end + 0.1)],
        # Холодный свет сверху, пока огонька нет (иначе тёмная одежда на тёмном асфальте не читается); гаснет, когда
        # огонёк рядом — дальше ключевой свет он.
        "flashlight": {"pos": [0.25, 4.6, 0.5], "dir": [-0.05, -1, -0.08], "color": "#c4d0dc", "cone": 24, "range": 7.5,
                       "powerKeys": [[0, 0.0], [2.0, 0.0], [3.4, 1.15], [round(t_wisp + 1.2, 3), 1.0],
                                     [round(t_lore + 0.5, 3), 0.3]]},
        "events": {"wake_cut": t_wake_cut, "up": t_up, "stand": t_stand, "wisp": t_wisp, "appear": appear, "lore": t_lore,
                   "viewer": t_view, "rules": t_rules, "menu": t_menu, "stats": first("stats")["t0"],
                   "items": first("items")["t0"], "cta": t_cta, "tease": t_tease, "g1": g1, "g2": g2,
                   "static_end": t_static_end},
    }


def _wisp_pos(keys: list[dict], t: float) -> list[float]:
    """Положение огонька по ключам (линейно — для взгляда хватает)."""
    if t <= keys[0]["t"]:
        return list(keys[0]["pos"])
    for a, b in zip(keys, keys[1:]):
        if a["t"] <= t < b["t"]:
            k = (t - a["t"]) / max(1e-6, b["t"] - a["t"])
            return [round(a["pos"][i] + (b["pos"][i] - a["pos"][i]) * k, 3) for i in range(3)]
    return list(keys[-1]["pos"])


def build_ep0_spec(base: dict, beats: list[dict], words: list[dict], total: float, voice_env: list[float]) -> dict[str, Any]:
    """Полный spec серии 0 для world.ts: основа пресета + сцена + субтитры + огибающая голоса огонька.
    ``beats`` и ``words`` — уже от начала ролика (shift_beats)."""
    spec = {k: deepcopy(v) for k, v in base.items() if k not in ("shots", "stills", "look_at", "cuts", "duration")}
    scene = ep0_scene(beats, total)
    events = scene.pop("events")
    glitch = scene.pop("glitch_cuts")
    fx_keys = scene.pop("fx_keys")
    wisp = {**spec.get("wisp", {}), **scene.pop("wisp"), "voice": voice_env, "voiceFps": spec.get("fps", 30)}
    spec.update(scene)
    spec["wisp"] = wisp
    spec["fx"] = {**spec.get("fx", {}), "keys": fx_keys}
    spec["cuts"] = sorted(set(scene["cuts"]) | set(glitch))
    spec["duration"] = round(total, 3)
    spec["captions"] = caption_chunks(words)
    spec["captionY"] = 404
    spec["_events"] = {k: round(v, 3) for k, v in events.items()}
    return spec


def ep0_beats(script: list[dict], words: list[dict], lead: float = LEAD) -> tuple[list[dict], float, list[dict]]:
    """Слова озвучки → фразы → темп (``pace``) → ускорение, если не влезает в MAX_TOTAL → сдвиг на lead.
    Возвращает (куски звука для темпа, ускорение, фразы со временем от начала ролика)."""
    segs, paced = pace(sentence_times(script, words))
    k = speed_for(segs[-1]["at"] + segs[-1]["src"][1] - segs[-1]["src"][0], lead)
    return segs, k, shift_beats(scale_beats(paced, k), lead)
