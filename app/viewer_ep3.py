"""Серия 3 «Замри» (проект 10 «Мир от зрителей», TikTok @.psx53 «Выжить psx»): сценарий огонька → сцена PS1.
Чистые функции; озвучка, рендер, звук и проверки — tools/viewer_ep3.py. Продолжает серию 2 (app/viewer_ep2.py):
тот же зал вокзала, тот же онрё (одобренный облик, onryo.ts), камера в её руках.

Под серией 2 победил комментарий Neko_Sil Flood: замереть (они идут на шум, бегом догонят), а потом отойти
подальше и перепрятаться. Что в серии:
1. Кадр 0 — её лицо крупно, испуганное, титр «НЕ ДЫШИ / ВЫ РЕШИЛИ: ЗАМЕРЕТЬ» (обложка), помехи плавно.
2. Огонёк: «Вы решили — замереть», карточка-комментарий как в TikTok (ник только на карточке, вслух не читается).
3. Онрё медленно идёт к будке и встаёт в шаге от неё, наклоняется — слушает. Она закрывает глаза, не дышит.
4. Честный бросок d20: ВОЛЯ (выдержать страх, не выдать себя) против 12, строка ROLL_SEED — выпало 12 + 1 = 13,
   успех на волоске («Тринадцать. Еле-еле»).
5. Радиоузел оживает: гонг вокзала и голос по громкой связи «…Конечная» (её билет). Онрё выпрямляется,
   поворачивает голову на звук и уходит к проходу на платформы.
6. Она отходит назад, снимая проход (видоискатель, REC), — под ногой хрустит стекло, онрё в проёме останавливается
   и медленно поворачивает голову… потом уходит в свет.
7. Новое укрытие — за колонной в дальнем углу зала (тот же зал: world.ts сдвигает зал, hall.moves). Она
   снимает себя: «Кажется… ушёл». Огонёк (врёт, когда боится): «Да. Чисто». На плёнке за её спиной — он.
8. «Сара… не оборачивайся». Меню «1 — ОБЕРНУТЬСЯ / 2 — БЕЖАТЬ / 3 — ВСПЫШКА ОГОНЬКА / 4 — СВОЁ», титр, помехи.
"""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

from app.viewer_dnd import resolve
from app.viewer_ep import _key, _wisp_pos, cue_times, pace, scale_beats, shift_beats, speed_for
from app.viewer_ep1 import HER_VOICE  # noqa: F401 — её голос (Gemini Leda), как в сериях 1–2
from app.viewer_ep2 import (BOOTH, HALL, HALL_FX, HALL_PSX, LAMP, LENS, NUMS, ONRYO_NEAR, SPOTS, WISP_BACK, WISP_HIDE,
                            onryo, pov_key, static_ramp)
from app.viewer_episode import caption_chunks, sentence_times

NAME = "Сара"
# Комментарий-победитель под серией 2 (со слов владельца). Лайков не знаем — в карточке сердце без числа (likes −1):
# выдуманных счётчиков в кадре не бывает.
COMMENT = {"nick": "Neko_Sil Flood", "likes": -1,
           "text": "Думаю лучше будет замереть, так как они всё равно идут на шум и если мы побежим то думаю что они "
                   "нас догонят довольно легко, но всё же скорее всего будет лучше отойти немного подальше от этого места "
                   "и перепрятаться"}
ROLL_SEED = "серия 3|Сара|замереть и перепрятаться"  # открытая строка кубика (как в серии 2: серия|имя|действие)
ROLL_STAT, ROLL_DIFF = "ВОЛЯ", "normal"  # замереть рядом с ним и не выдать себя — выдержать страх (ВОЛЯ), обычная сложность
ROLL_ACTION = "НЕ ДЫШАТЬ"
HOOK = ["НЕ ДЫШИ", "ВЫ РЕШИЛИ: ЗАМЕРЕТЬ"]
CHOICE = ["ЧТО ДЕЛАТЬ?", "1 — ОБЕРНУТЬСЯ", "2 — БЕЖАТЬ", "3 — ВСПЫШКА ОГОНЬКА", "4 — СВОЁ"]
FINAL = ["ЧТО ДЕЛАЕТ САРА?", "— В КОММЕНТЫ"]
HER_TEXT = "Кажется… ушёл."
# Громкая связь вокзала (радиоузел «до сих пор вещает»): другой бесплатный голос Gemini, через «рупор» в сведении.
PA_TEXT = "Конечная."
PA_VOICE = {"provider": "gemini", "voice": "Charon", "style": ""}
PA_CHIME = 1.05  # гонг перед голосом (с)
DATE, STAMP = "09 НОЯ", "03:24"

# Новое укрытие: точка зала (после разворота, оси серии 2) за колонной (3.8, −6) в дальнем углу — с момента
# переезда она в начале координат (world.ts сдвигает зал), героиня лицом к колонне (+Z, проход за колонной).
HIDE = (3.8, -7.0)
COLUMN = [0.0, 1.0]  # колонна в осях укрытия (x, z); грани: x ±0.3, z 0.7…1.3
SIDE = [-1.2, 0.0, 0.5]  # сначала встаёт справа от неё, в шаге
STOP = [-0.55, 0.0, -0.8]  # потом медленно заходит ей за спину и наклоняется над правым плечом
DOOR = [-0.5, 0.0, 5.6]  # за турникетами, против света прохода — там он останавливается на хруст
GONE = [-0.25, 0.0, 10.2]  # в конце коридора, в бледном свете
BEHIND = [-1.32, 0.0, -1.62]  # в осях укрытия: за её спиной, в углу (видно в селфи над плечом)
LEAN = {"spine": [10, 0, 0], "chest": [10, 0, 0], "upperChest": [10, 0, 0], "neck": [12, 0, 0]}  # наклон к ней — слушает
CORNER_LAMP = ([-1.2, 3.1, -1.25], [0.0, -1, -0.12])  # лампа над углом укрытия (оси укрытия): мигает — и он виден


def ep3_roll() -> dict[str, Any]:
    """Бросок по правилам (app.viewer_dnd.resolve): Сара — статы серии 1 (ТЯЖЁЛАЯ РУКА: ВОЛЯ +1)."""
    from app.viewer_ep1 import ep1_roll

    hero = deepcopy(ep1_roll()["heroine"])
    hero["name"] = NAME
    hero["episode"] = 3
    res = resolve(hero, ROLL_STAT, ROLL_DIFF, ROLL_SEED)
    return {"seed": ROLL_SEED, "result": res, "heroine": hero, "preset": hero["preset"]}


def ep3_script(roll: dict[str, Any]) -> list[dict]:
    """Реплики огонька (её шёпот — HER_TEXT, громкая связь — PA_TEXT: другие голоса). Строка итога — по броску."""
    r = roll["result"]
    if not r["ok"]:
        raise ValueError("сценарий серии 3 написан под успех броска; провал — переписать сцену 4–5")
    close = r["total"] - r["dc"] <= 2
    said = f"{NUMS[r['total']]}. " + ("Еле-еле." if close else "Тебя не слышно.")
    return [
        {"beat": "intro", "text": "Вы решили — замереть."},
        {"beat": "reason", "text": "Они идут на шум. Побежишь — догонят.", "cues": ["шум", "побежишь", "догонят"]},
        {"beat": "still", "text": "Не шевелись. Не дыши.", "cues": ["дыши"]},
        {"beat": "result", "text": said},
        {"beat": "radio", "text": "Радиоузел… Кто-то вещает."},
        {"beat": "go1", "text": "Он идёт на звук."},
        {"beat": "go2", "text": "Теперь назад. Медленно.", "cues": ["назад"]},
        {"beat": "crunch", "text": "Замри."},
        {"beat": "on", "text": "Дальше. За колонну."},
        {"beat": "lie", "text": "Да. Чисто."},
        {"beat": "warn", "text": "Сара… не оборачивайся."},
        {"beat": "choice1", "text": "Обернуться? Бежать? Или мне вспыхнуть?", "cues": ["обернуться", "бежать", "вспыхнуть"]},
        {"beat": "choice2", "text": "Решайте за Сару.", "cues": ["решайте"]},
    ]


LEAD = 0.95  # до первой фразы: обложка, помехи нарастают и растворяются в её лице с титром (с)
TAIL = 2.3  # после последней фразы: титр-призыв, потом плавно — в помехи (с)
FPS = 30
HEAD_RAMP = (0.5, 0.58, 1.08)  # как в серии 2 v2: кадр 0 — обложка, помехи плавно нарастают и растворяются
END_RAMP = (0.62, 0.1)
SPIN = 1.5  # кубик крутится (с)
LEAN_T = 1.4  # наклон к ней (с)
BEHIND_T = 1.5  # заходит ей за спину (с)
CRUNCH_HOLD = 1.0  # после хруста до «Замри» (с): камера встала, он остановился
PAUSE_BEFORE = {"reason": 0.75, "still": 1.7, "go1": 0.45, "go2": 2.0, "on": 2.3, "warn": 2.5, "choice1": 1.0, "choice2": 0.45}


def ep3_beats(script: list[dict], words: list[dict], her_len: float, pa_len: float, lead: float = LEAD
              ) -> tuple[list[dict], float, list[dict]]:
    """Слова озвучки → фразы → темп с паузами серии: перед итогом — наклон и бросок, перед «Радиоузел» — громкая
    связь (гонг + голос), перед «Замри» — шаги назад и хруст, перед «Да. Чисто» — новое укрытие и её шёпот."""
    before = {**PAUSE_BEFORE, "result": 0.25 + BEHIND_T + LEAN_T * 0.4 + 0.25 + 0.3 + SPIN + 0.6,
              "radio": 0.45 + PA_CHIME + pa_len + 0.45, "crunch": 2.3 + CRUNCH_HOLD,
              "lie": 0.5 + 1.5 + 0.35 + her_len + 0.45}
    segs, paced = pace(sentence_times(script, words), before=before)
    k = speed_for(segs[-1]["at"] + segs[-1]["src"][1] - segs[-1]["src"][0], lead, max_total=999.0)
    return segs, k, shift_beats(scale_beats(paced, k), lead)


def timeline(beats: list[dict], her_len: float, pa_len: float, total: float) -> dict[str, float]:
    """Все моменты серии (с от начала ролика) по времени фраз."""
    ph = {b["beat"]: b for b in beats}
    cue = lambda b, i=0, d=0.5: cue_times(ph[b])[i] or ph[b]["t0"] + d  # noqa: E731
    ev: dict[str, float] = {}
    ev["card0"] = ph["intro"]["t1"] + 0.2
    ev["shum"] = cue("reason", 0)
    ev["run"] = cue("reason", 1)
    ev["catch"] = cue("reason", 2)
    ev["app0"] = ph["reason"]["t1"] + 0.35  # онрё трогается к будке — план «подходит»
    ev["card1"] = ev["app0"] - 0.05
    ev["eyes"] = cue("still", 0, 1.0) + 0.05  # «…не дыши» — закрывает глаза
    ev["close0"] = ph["still"]["t1"] + 0.25  # крупно: её профиль, он наклоняется
    ev["side"] = ev["close0"] - 0.1  # встал рядом, справа
    ev["stop"] = ev["close0"] + BEHIND_T  # зашёл за спину
    ev["lean1"] = ev["stop"] + LEAN_T
    ev["roll"] = ev["stop"] + LEAN_T * 0.4 + 0.25
    ev["land"] = ev["roll"] + 0.25 + 0.3 + SPIN
    ev["pa0"] = ph["result"]["t1"] + 0.45  # гонг
    ev["pa_voice"] = ev["pa0"] + PA_CHIME
    ev["pa1"] = ev["pa_voice"] + pa_len
    ev["turn0"] = ev["pa_voice"] + 0.15  # медленно поворачивает голову на звук
    ev["leave0"] = ph["radio"]["t1"] + 0.15  # план «уходит»: выпрямился, идёт к проходу
    ev["pov0"] = ph["go2"]["t1"] + 0.2  # видоискатель: она пятится и снимает проход
    ev["crunch"] = ph["crunch"]["t0"] - CRUNCH_HOLD  # хруст под ногой
    ev["look0"] = ev["crunch"] + 0.35  # он медленно поворачивает голову назад
    ev["resume"] = ph["on"]["t0"] - 0.35  # отвернулся — уходит в свет
    ev["col0"] = ph["on"]["t1"] + 0.5  # новое укрытие (зал сдвинут)
    ev["raise"] = ev["col0"] + 0.5  # поднимает камеру на себя
    ev["self0"] = ev["col0"] + 1.5  # селфи
    ev["her0"] = ev["self0"] + 0.35
    ev["her1"] = ev["her0"] + her_len
    ev["reveal"] = ph["lie"]["t1"] + 0.55  # срыв трекинга — после него за спиной стоит он
    ev["flick"] = ev["reveal"] + 0.6  # лампа над углом мигает
    ev["wide"] = ev["reveal"] + 1.15  # её глаза расширяются — увидела на экранчике
    ev["choice"] = ph["choice1"]["t0"] - 0.15
    ev["cta"] = cue("choice2", 0, 0.1) - 0.1
    ev["end"] = total
    ev["static_end"] = round(total - END_RAMP[0] - END_RAMP[1], 3)
    if ev["roll"] < ev["close0"] or ev["land"] > ph["result"]["t0"] - 0.2:
        raise ValueError(f"бросок не помещается до итога: {ev['land']:.2f} > {ph['result']['t0']:.2f}")
    if ev["pa1"] > ph["radio"]["t0"] - 0.2:
        raise ValueError("громкая связь налезает на «Радиоузел»")
    if ev["her1"] > ph["lie"]["t0"] - 0.2:
        raise ValueError("её шёпот налезает на «Да. Чисто»")
    if not ev["pov0"] + 1.0 < ev["crunch"] < ev["resume"] - 1.0:
        raise ValueError(f"хруст не по порядку: {ev['pov0']:.2f} {ev['crunch']:.2f} {ev['resume']:.2f}")
    return {k: round(v, 3) for k, v in ev.items()}


def _r(x: float) -> float:
    return round(x, 3)


def to_hide(p: list[float]) -> list[float]:
    """Точка зала (оси серии 2) → оси нового укрытия."""
    return [round(p[0] - HIDE[0], 3), p[1], round(p[2] - HIDE[1], 3)] if len(p) == 3 else [round(p[0] - HIDE[0], 3), round(p[1] - HIDE[1], 3)]


def sneak_keys(t0: float, t_stop: float, t_go: float, t1: float, look: list[float]) -> list[dict]:
    """Видоискатель: она пятится от будки к дальнему углу и снимает проход (look). Путь — плавная кривая, шаги —
    мягкое покачивание (высота и крен взгляда), на хрусте камера плавно встаёт и стоит до t_go. Ключи каждые 0.1 с
    (orbit.ts ведёт сплайном — без ступенек)."""
    path = [(-0.1, 0.2), (-0.22, -1.2), (-0.3, -2.6), (-0.25, -3.6), (-0.1, -4.4)]  # x < 0.08: будка не заслоняет проход

    def at(s: float) -> tuple[float, float]:  # s 0..1 вдоль ломаной (по длине)
        lens = [math.dist(path[i], path[i + 1]) for i in range(len(path) - 1)]
        d = s * sum(lens)
        for i, ln in enumerate(lens):
            if d <= ln or i == len(lens) - 1:
                u = min(1.0, d / ln)
                return path[i][0] + (path[i + 1][0] - path[i][0]) * u, path[i][1] + (path[i + 1][1] - path[i][1]) * u
            d -= ln
        return path[-1]

    # Пройденная доля пути: до хруста — 55 %, после — до конца; разгон и торможение плавные.
    def frac(t: float) -> float:
        if t <= t_stop:
            u = (t - t0) / max(1e-6, t_stop - t0)
            return 0.55 * (u * u * (3 - 2 * u) * 0.35 + u * 0.65) if u < 1 else 0.55
        if t <= t_go:
            return 0.55
        u = min(1.0, (t - t_go) / max(1e-6, t1 - t_go))
        return 0.55 + 0.45 * (u * u * (3 - 2 * u) * 0.5 + u * 0.5)

    keys = []
    n = int(round((t1 - t0) / 0.1))
    for i in range(n + 1):
        t = t0 + (t1 - t0) * i / n
        x, z = at(frac(t))
        # Покачивание шагов гаснет за 0.3 с после хруста и возвращается за 0.4 с, когда она снова пошла (без рывка).
        sm = lambda u: 0.0 if u <= 0 else 1.0 if u >= 1 else u * u * (3 - 2 * u)  # noqa: E731
        moving = 1.0 - sm((t - t_stop) / 0.3) if t < t_go else sm((t - t_go) / 0.4)
        walk = frac(t) * 22.0  # фаза шага от пройденного
        bob = 0.018 * moving * math.sin(walk * math.pi)
        side = 0.03 * moving * math.sin(walk * math.pi * 0.5)
        cam = [round(x, 3), round(1.4 + bob, 3), round(z, 3)]
        tgt = [round(look[0] + side, 3), round(look[1] + bob * 2, 3), look[2]]
        keys.append(pov_key(t - t0, cam, tgt, 38))
    return keys


def ep3_scene(beats: list[dict], total: float, roll: dict[str, Any], her_len: float, pa_len: float) -> dict[str, Any]:
    """Планы, онрё, огонёк, интерфейс, камера в руках, плёнка и помехи серии по времени фраз."""
    ph = {b["beat"]: b for b in beats}
    ev = timeline(beats, her_len, pa_len, total)
    end = total + 1
    me = [0.0, 0.0]

    def shot(name, t0, t1, keys, **kw):
        return {"name": name, "t0": _r(t0), "t1": _r(t1), "minHeight": 0.0, "camKeys": keys, **kw}

    near = ONRYO_NEAR  # там он стоял в конце серии 2
    door_look = [DOOR[0], 1.35, DOOR[2] + 0.4]
    shots = [
        # Обложка: её лицо крупно (в профиль на три четверти), испуганное; медленный наезд. Титр — сверху.
        shot("лицо", 0, ev["card0"], [_key(0, -28, 0.95, 1.5, 1.46, 30), _key(ev["card0"], -24, 0.85, 1.5, 1.46, 30)]),
        # Комментарий: она ниже в кадре, карточка сверху; за ней в глубине — он, на краю света лампы.
        shot("комментарий", ev["card0"], ev["app0"], [_key(0, -40, 1.45, 1.6, [0, 1.62, 0], 36),
                                                     _key(ev["app0"] - ev["card0"], -34, 1.35, 1.6, [0, 1.62, 0], 36)]),
        # Подходит: из-за её левого плеча — он медленно идёт к будке и встаёт в шаге от неё.
        shot("подходит", ev["app0"], ev["close0"], [pov_key(0, [0.42, 1.62, -0.62], [-0.85, 1.45, 1.7], 46),
                                                   pov_key(ev["close0"] - ev["app0"], [0.36, 1.6, -0.5], [-0.9, 1.5, 1.1], 44)],
             free=True),
        # Крупно: её профиль с закрытыми глазами, над ней — его голова в мокрых волосах, наклоняется. Бросок сверху.
        shot("рядом", ev["close0"], ev["pov0"], [pov_key(0, [0.0, 1.52, 1.05], [-0.08, 1.58, -0.1], 44),
                                                pov_key(ev["leave0"] - ev["close0"], [-0.02, 1.52, 0.92], [-0.08, 1.6, -0.1], 40),
                                                pov_key(ev["pov0"] - ev["close0"], [-0.03, 1.5, 0.84], [-0.1, 1.55, -0.1], 38)],
             free=True),
        # Видоискатель: она пятится и снимает проход; хруст — встала; он в проёме оборачивается… и уходит в свет.
        shot("видоискатель", ev["pov0"], ev["col0"], sneak_keys(ev["pov0"], ev["crunch"], ev["resume"] + 0.4, ev["col0"], door_look),
             pov=True, free=True),
        # Новое укрытие: за колонной (оси укрытия), сбоку — видно её, край колонны и тёмный угол за спиной.
        shot("колонна", ev["col0"], ev["self0"], [pov_key(0, [0.62, 1.52, 0.6], [-0.15, 1.4, -0.3], 50),
                                                 pov_key(ev["self0"] - ev["col0"], [0.58, 1.52, 0.58], [-0.15, 1.42, -0.3], 48)],
             free=True),
        # Селфи — запись её камеры: лицо, за плечом тёмный угол… где после срыва стоит он.
        shot("селфи", ev["self0"], end, [_key(0, 0, 1, 1.5, 1.45, 60)], selfie={"fov": 70, "back": 0.22}, noWisp=True),
    ]
    cuts = [s["t0"] for s in shots[1:]]

    # --- онрё: шаркает, когда переходит (walk), на месте стоит не шелохнувшись, голова движется медленно.
    lens = [LENS[0], LENS[2]]
    k = lambda t, pos, face, **kw: {"t": _r(t), "pos": pos, "face": face, **kw}  # noqa: E731
    door_face = [DOOR[0], DOOR[2] + 3]
    beh = BEHIND
    okeys = [k(-5, near, me, tilt=40, nod=0), k(ev["app0"], near, me, tilt=40, nod=0),
             k(ev["side"], SIDE, me, tilt=30, nod=4),  # встал рядом
             k(ev["side"] + 0.25, SIDE, me, tilt=30, nod=4),
             k(ev["stop"], STOP, me, tilt=32, nod=8),  # медленно зашёл за спину
             k(ev["lean1"], STOP, me, tilt=36, nod=16, bones=LEAN),  # наклонился — слушает
             k(ev["turn0"], STOP, me, tilt=38, nod=18, bones=LEAN),
             k(ev["turn0"] + 1.4, STOP, me, tilt=22, nod=6, turn=-40, bones=LEAN),  # голова — на звук
             k(ev["leave0"] - 0.05, STOP, me, tilt=20, nod=4, turn=-40),  # выпрямился
             k(ev["leave0"] + 1.4, SIDE, door_face, tilt=22, nod=2),  # обходит её справа, к проходу
             k(ev["crunch"], DOOR, door_face, tilt=24, nod=2),  # дошёл до проёма — хруст
             k(ev["look0"], DOOR, door_face, tilt=24, nod=2),
             k(ev["look0"] + 1.3, DOOR, [DOOR[0] + 1.0, DOOR[2] + 1.5], tilt=40, nod=-2, turn=70),  # медленно — назад
             k(ev["resume"], DOOR, [DOOR[0] + 1.0, DOOR[2] + 1.5], tilt=42, nod=-2, turn=72),
             k(ev["resume"] + 0.9, DOOR, door_face, tilt=26, nod=2, turn=0),  # отвернулся
             k(ev["col0"] - 0.05, GONE, door_face, tilt=26, nod=2),
             # Укрытие: за её спиной, в углу (оси укрытия), смотрит на неё; переставлен в срыв трекинга.
             k(ev["col0"], beh, me, tilt=34, nod=6),
             k(ev["wide"], beh, me, tilt=36, nod=8),
             k(end, beh, me, tilt=52, nod=4)]
    show = [[-5, ev["col0"]], [ev["reveal"] + 0.09, end]]
    rim = {"color": "#7a988a", "keys": [[0, 0.55], [_r(ev["col0"]), 0.55], [_r(ev["reveal"]), 0.35], [_r(ev["flick"] + 0.3), 0.9], [_r(end), 0.9]]}
    monster = onryo(okeys, show, rim=rim, walk={"stride": 0.5, "hip": 12, "knee": 15})

    # --- огонёк: у её лица на комментарии; потом за плечом, приглушён; в укрытии — у левого плеча.
    keys = [{"t": 0, "pos": [0.3, 1.78, 0.32]}, {"t": 1.2, "pos": [0.31, 1.76, 0.3]}]
    t_a, t_b = ev["card0"], ev["app0"]
    n = max(2, int((t_b - t_a) / 0.4) + 1)
    for i in range(n):
        t = t_a + (t_b - t_a) * i / (n - 1)
        a = 0.6 * (t - t_a)
        keys.append({"t": _r(t), "pos": [round(0.3 + 0.04 * math.sin(a * 1.7), 3), round(1.76 + 0.04 * math.sin(a), 3),
                                         round(0.3 + 0.04 * math.cos(a * 1.3), 3)]})
    keys += [{"t": _r(ev["app0"] + 0.8), "pos": WISP_BACK}, {"t": _r(ev["col0"] - 0.01), "pos": WISP_BACK},
             {"t": _r(ev["col0"]), "pos": [0.36, 1.62, -0.2]}, {"t": _r(end), "pos": [0.33, 1.58, -0.22]}]
    wisp = {**WISP_HIDE, "appear": -5, "fadeIn": 0.5, "keys": keys,
            "dim": [[_r(ev["app0"]), _r(ev["col0"]), 0.35], [_r(ev["col0"]), _r(ev["reveal"]), 0.8], [_r(ev["reveal"]), _r(end), 0.3]]}

    # --- взгляд: на него у лампы, на огонёк, на него (подходит), глаза закрыты; в укрытии — за колонну, в объектив.
    sel = [0.2, 1.5, 0.5]
    look = [{"t": -5, "target": [near[0], 1.7, near[2]], "head": 0.5},
            {"t": _r(ev["card0"] + 0.3), "target": _wisp_pos(keys, ev["card0"] + 0.5), "head": 0.35},
            {"t": _r(ev["app0"] - 0.3), "target": [SIDE[0], 1.75, SIDE[2] + 0.6], "head": 0.4},
            {"t": _r(ev["col0"]), "target": [-0.4, 1.45, 1.6], "head": 0.6},  # из-за колонны — на проход
            {"t": _r(ev["raise"] + 0.3), "target": sel, "head": 0.5},
            {"t": _r(ev["her1"] + 0.2), "target": [0.12, 1.42, 0.5], "head": 0.4},  # на экранчик
            {"t": _r(ev["wide"]), "target": [0.16, 1.47, 0.5], "head": 0.4}]
    eyes = [[_r(ev["eyes"]), _r(ev["leave0"] + 1.6)]]  # открывает, когда он уже прошёл мимо
    fear = {"surprised": 0.22, "sad": 0.35}
    expr_keys = [{"t": 0, "expr": fear}, {"t": _r(ev["card0"]), "expr": fear},
                 {"t": _r(ev["col0"]), "expr": {"sad": 0.4, "surprised": 0.1}},
                 {"t": _r(ev["wide"] - 0.2), "expr": {"sad": 0.4, "surprised": 0.1}},
                 {"t": _r(ev["wide"] + 0.35), "expr": {"surprised": 0.6, "sad": 0.3}}]

    # --- интерфейс
    r = roll["result"]
    card = {"t0": _r(ev["card0"]), "t1": _r(ev["card1"]), "nick": COMMENT["nick"], "likes": COMMENT["likes"], "text": COMMENT["text"],
            "style": "tiktok", "age": "1 д.", "y": 70,
            "hl": [{"w": "замереть", "t": _r(ph["intro"]["t1"] - 0.5)}, {"w": "шум", "t": _r(ev["shum"])},
                   {"w": "побежим", "t": _r(ev["run"])}, {"w": "догонят", "t": _r(ev["catch"])},
                   {"w": "перепрятаться", "t": _r(ev["catch"] + 0.4)}]}
    rolls = {"t0": _r(ev["roll"]), "t1": _r(ph["result"]["t1"] + 0.45), "stat": r["stat"], "action": ROLL_ACTION,
             "die": r["die"], "bonus": r["bonus"], "dc": r["dc"], "spin": SPIN, "y": 40}
    cards = [{"t0": _r(ev["choice"]), "t1": _r(ev["cta"] - 0.05), "lines": CHOICE, "accent": [0], "y": 196}]
    titles = [{"t0": 0, "t1": _r(ev["card0"]), "lines": ["НЕ ДЫШИ"], "colors": ["#f8d848"], "y": 56, "px": 24},
              {"t0": 0, "t1": _r(ev["card0"]), "lines": ["ВЫ РЕШИЛИ:", "ЗАМЕРЕТЬ"], "colors": ["#f8f8f8", "#f8f8f8"], "y": 96, "px": 16},
              {"t0": _r(ev["cta"]), "t1": _r(end), "lines": ["ЧТО ДЕЛАЕТ", "САРА?", "— В КОММЕНТЫ"],
               "colors": ["#f8f8f8", "#f8d848", "#f8f8f8"], "y": 176, "px": 16}]
    cam = [{"t0": _r(ev["pov0"]), "t1": _r(ev["col0"]), "mode": "rec", "stamp": STAMP, "sec0": 31, "date": DATE},
           {"t0": _r(ev["self0"]), "t1": _r(end), "mode": "rec", "stamp": STAMP, "sec0": 31 + int(ev["self0"] - ev["pov0"]), "date": DATE,
            "lowAt": _r(ev["self0"]), "lowText": "БАТАРЕЯ"}]

    # --- плёнка: видоискатель и селфи — картинка камеры; хруст и срыв — полосы; автофокус ищет его за спиной.
    def vwin(a, b, kk=1.0):
        return [[_r(a - 0.01), 0], [_r(a), kk], [_r(b - 0.01), kk], [_r(b), 0]]
    vk = [[0, 0]] + vwin(ev["pov0"], ev["col0"]) + [[_r(ev["self0"] - 0.01), 0], [_r(ev["self0"]), 1.0], [_r(end), 1.15]]
    vhs = {"keys": vk, "hunts": [_r(ev["pov0"] + 0.3), _r(ev["reveal"] - 0.35), _r(ev["flick"] + 0.2)],
           "tears": [[_r(ev["crunch"]), _r(ev["crunch"] + 0.1)], [_r(ev["reveal"]), _r(ev["reveal"] + 0.18)],
                     [_r(ev["choice"] - 0.35), _r(ev["choice"] - 0.2)]],
           "shake": 0.006}
    spike = lambda t, a: [{"t": _r(t - 0.05), "static": 0.0}, {"t": _r(t), "static": a},  # noqa: E731
                          {"t": _r(t + 0.1), "static": a * 0.5}, {"t": _r(t + 0.2), "static": 0.0}]
    se = ev["static_end"]
    fx_keys = [{"t": 0, "black": 0.0}] + static_ramp(0.0, HEAD_RAMP[0], 0.0, 1.0) + static_ramp(HEAD_RAMP[1], HEAD_RAMP[2], 1.0, 0.0)
    fx_keys += spike(ev["pov0"], 0.35) + spike(ev["self0"], 0.3) + spike(ev["reveal"] + 0.05, 0.4)
    fx_keys += static_ramp(se, se + END_RAMP[0], 0.0, 1.0) + [{"t": _r(end + 1), "static": 1.0}]
    fx_keys.sort(key=lambda kk: kk["t"])
    holds = {"object": "camcorder", "keys": [{"t": -5, "mode": "chest", "blend": 0.1}, {"t": _r(ev["raise"]), "mode": "selfie_side", "blend": 0.9}]}
    # Лампа зала: над ним у турникетов; гонг — мигнула. В укрытии — лампа над углом: тускло, на срыве мигает и горит.
    fl = {"pos": [LAMP[0], 4.3, LAMP[1]], "dir": [0, -1, 0.05], "color": "#d8ffe4", "cone": 34, "range": 7.0,
          "moves": [[_r(ev["col0"]), CORNER_LAMP[0], CORNER_LAMP[1]]],
          "powerKeys": [[0, 1.6], [_r(ev["pa0"]), 1.6], [_r(ev["pa0"] + 0.06), 0.5], [_r(ev["pa0"] + 0.14), 1.7],
                        [_r(ev["col0"] - 0.01), 1.7], [_r(ev["col0"]), 0.7], [_r(ev["reveal"]), 0.7], [_r(ev["reveal"] + 0.05), 0.15], [_r(ev["flick"]), 0.15], [_r(ev["flick"] + 0.05), 1.3],
                        [_r(ev["flick"] + 0.12), 0.2], [_r(ev["flick"] + 0.2), 2.4], [_r(ev["flick"] + 0.26), 0.4],
                        [_r(ev["flick"] + 0.4), 2.6], [_r(end), 2.6]]}
    # Её лицо рядом с ним — в свете экранчика камеры у груди (вместо лампы зала: свет-конус в сцене один).
    lcd_glow = [{"t0": _r(ev["close0"]), "t1": _r(ev["pov0"]), "color": "#9ccaff", "power": 1.5, "range": 2.4}]
    glitch = [_r(x) for x in (0.2, HEAD_RAMP[1], ev["pov0"], ev["crunch"], ev["self0"], ev["reveal"], ev["choice"] - 0.35, se + END_RAMP[0] * 0.5)]
    hall = {**deepcopy(HALL), "moves": [[_r(ev["col0"]), list(HIDE)]]}
    return {"shots": shots, "cuts": cuts, "look_at": look, "wisp": wisp, "comment": card, "roll": rolls, "cards": cards,
            "titles": titles, "cam": cam, "vhs": vhs, "fx_keys": fx_keys, "onryo": monster, "holds": holds, "flashlight": fl,
            "objects": [], "glitch_cuts": glitch, "lcdGlow": lcd_glow, "events": ev, "eyesClosed": eyes, "exprKeys": expr_keys, "hall": hall}


def base_spec(base: dict) -> dict[str, Any]:
    """Основа: пресет улицы без улицы → вокзал серии 2 вживую, свет и туман зала, тон."""
    spec = {k: deepcopy(v) for k, v in base.items() if k not in ("street", "shots", "stills", "look_at", "cuts", "duration")}
    spec["psx"] = {**spec.get("psx", {}), **deepcopy(HALL_PSX)}
    spec["hall"] = deepcopy(HALL)
    spec["fx"] = {**spec.get("fx", {}), **HALL_FX}
    return spec


def build_ep3_spec(base: dict, beats: list[dict], words: list[dict], total: float, voice_env: list[float], roll: dict[str, Any],
                   her_len: float, her_env: list[float], her_words: list[dict], pa_len: float, pa_words: list[dict]) -> dict[str, Any]:
    """Полный spec серии для world.ts. Слова её шёпота и громкой связи — от начала их аудио (сдвигаются на свои места)."""
    spec = base_spec(base)
    scene = ep3_scene(beats, total, roll, her_len, pa_len)
    events = scene.pop("events")
    glitch = scene.pop("glitch_cuts")
    fx_keys = scene.pop("fx_keys")
    wisp = {**spec.get("wisp", {}), **scene.pop("wisp"), "voice": voice_env, "voiceFps": spec.get("fps", 30)}
    spec.update(scene)
    spec["wisp"] = wisp
    spec["fx"] = {**spec.get("fx", {}), "keys": fx_keys}
    spec["cuts"] = sorted(set(scene["cuts"]) | set(glitch))
    spec["duration"] = round(total, 3)
    spec["mouth"] = [{"t0": events["her0"], "t1": events["her1"], "voice": her_env, "fps": 30}]
    shift = lambda ws, t: [{"word": w["word"], "start": round(w["start"] + t, 3), "end": round(w["end"] + t, 3)} for w in ws]  # noqa: E731
    spec["captions"] = caption_chunks(sorted(words + shift(her_words, events["her0"]) + shift(pa_words, events["pa_voice"]),
                                             key=lambda w: w["start"]))
    spec["captionY"] = 316
    spec["_events"] = events
    return spec


# --------------------------------------------------------------------------- без озвучки (Gemini недоступен)

def estimate_words(text: str, t0: float = 0.0, cps: float = 13.0, gap: float = 0.35) -> list[dict]:
    """Слова на прикидочном времени (≈ cps знаков в секунду, паузы на точках) — для проб кадров без озвучки."""
    out, t = [], t0
    for w in text.split():
        if not any(ch.isalnum() for ch in w):
            continue
        d = max(0.18, len(w) / cps)
        out.append({"word": w, "start": round(t, 3), "end": round(t + d, 3)})
        t += d + (gap if w.endswith((".", "?", "!", "…")) else 0.06)
    return out


def hide_points() -> dict[str, list[float]]:
    """Что где в осях укрытия (для проверок и листа): колонна, онрё за спиной, лампа над углом."""
    return {"column": COLUMN, "behind": BEHIND[::2], "lamp": CORNER_LAMP[0], "booth_from_hide": to_hide(list(BOOTH["pos"]))}


SPOT_NAMES = SPOTS  # noqa: F401 — точки зала серии 2 (для справки в отчёте)
