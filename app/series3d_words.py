"""3D-серия: слова озвучки, кадры, события (из ``app/series3d.py``)."""

from __future__ import annotations

import re

from app.series3d_scene import DirectionError

SHOT_MIN_SEC = 1.4  # фраза короче — в один кадр со следующей
SHOT_SPLIT_SEC = 4.6  # фраза длиннее — два кадра
DETAIL_WORDS = r"^(табло|кнопк|люк|экран)"  # вещь крупно — вставка-деталь со склейкой на этом слове


# --------------------------------------------------------------------------- #
# Слова: фразы по времени, кадры, поиск слова
# --------------------------------------------------------------------------- #


def _wnorm(word: str) -> str:
    return re.sub(r"[^a-zа-я0-9]", "", str(word).lower().replace("ё", "е"))


def align_words(text: str, spoken: list[dict]) -> list[dict]:
    """Слова СЦЕНАРИЯ со временем распознанных (Whisper ошибается: «блокаут», «от облона» вместо «а табло»):
    совпавшие — время слова, заменённые — время куска поровну по длине, пропущенные в речи — короткий миг у
    следующего слова. Субтитры и режиссура идут по тексту сценария, а не по распознанному."""
    import difflib

    tokens = str(text or "").split()
    if not tokens or not spoken:
        return [dict(w) for w in spoken]
    a = [_wnorm(t) for t in tokens]
    b = [_wnorm(w["word"]) for w in spoken]
    out: list[dict | None] = [None] * len(tokens)
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                w = spoken[j1 + k]
                out[i1 + k] = {"word": tokens[i1 + k], "start": float(w["start"]), "end": float(w["end"])}
        elif tag == "replace":
            t0, t1 = float(spoken[j1]["start"]), float(spoken[j2 - 1]["end"])
            weights = [len(x) + 1 for x in a[i1:i2]]
            acc = t0
            for k, wt in enumerate(weights):
                d = (t1 - t0) * wt / sum(weights)
                out[i1 + k] = {"word": tokens[i1 + k], "start": round(acc, 3), "end": round(acc + d, 3)}
                acc += d
    # Пропущенные в речи (delete) — вплотную к соседям.
    for k in range(len(tokens)):
        if out[k] is None:
            prev_end = out[k - 1]["end"] if k and out[k - 1] else float(spoken[0]["start"])
            nxt = next((out[j]["start"] for j in range(k + 1, len(tokens)) if out[j]), prev_end + 0.12)
            out[k] = {"word": tokens[k], "start": round(prev_end, 3), "end": round(max(prev_end, min(nxt, prev_end + 0.12)), 3)}
    res: list[dict] = []
    last = 0.0
    for w in out:
        s = max(last, float(w["start"]))
        e = max(s + 0.02, float(w["end"]))
        res.append({"word": w["word"], "start": round(s, 3), "end": round(e, 3)})
        last = s
    return res


def sentence_spans(sentences: list[dict], words: list[dict], total: float) -> list[dict]:
    """Фраза → [t0, t1) по словам озвучки и диапазон слов [w0, w1]."""
    from app.explainer_gen import align

    if not words:
        raise DirectionError("нет слов озвучки")
    firsts = align([{"text": s["text"]} for s in sentences], words)
    out = []
    for n, s in enumerate(sentences):
        w0 = firsts[n]
        w1 = (firsts[n + 1] - 1) if n + 1 < len(sentences) else len(words) - 1
        w1 = max(w0, w1)
        t0 = 0.0 if n == 0 else float(words[w0]["start"])
        t1 = float(words[firsts[n + 1]]["start"]) if n + 1 < len(sentences) else float(total)
        out.append({"i": n, "text": s["text"], "role": s.get("role") or "", "hud": s.get("hud") or "", "t0": t0,
                    "t1": max(t0 + 0.2, t1), "w0": w0, "w1": w1})
    return out


def split_shots(spans: list[dict], words: list[dict]) -> list[dict]:
    """Кадры: фраза = кадр; короткая (< 1.4 с) — вместе со следующей; длинная (> 4.6 с) — два кадра, разрез
    на слове у середины (лучше после запятой)."""
    merged: list[dict] = []
    for sp in spans:
        if merged and merged[-1]["t1"] - merged[-1]["t0"] < SHOT_MIN_SEC:
            prev = merged[-1]
            prev.update({"t1": sp["t1"], "w1": sp["w1"], "text": f"{prev['text']} {sp['text']}",
                         "sentences": prev["sentences"] + [sp["i"]], "role": prev["role"] or sp["role"]})
            continue
        merged.append({**sp, "sentences": [sp["i"]]})
    shots: list[dict] = []
    for sp in merged:
        length = sp["t1"] - sp["t0"]
        inner = [k for k in range(sp["w0"] + 1, sp["w1"] + 1) if sp["t0"] + 1.6 < words[k]["start"] < sp["t1"] - 1.6]
        # Вещь крупно (табло, кнопки, люк) упомянута во второй половине — склейка на ней: вставка-деталь.
        detail = [k for k in range(sp["w0"] + 1, sp["w1"] + 1)
                  if sp["t0"] + 1.2 < words[k]["start"] < sp["t1"] - 1.0 and re.search(DETAIL_WORDS, _wnorm(words[k]["word"]))]
        if length > 2.8 and detail:
            k0 = detail[0]
            # Склейка — на начале фразы/словосочетания с вещью («А табло…»), не посреди него.
            while k0 - 1 > sp["w0"] and words[k0 - 1]["start"] > sp["t0"] + 1.2 and not \
                    str(words[k0 - 1]["word"]).rstrip().endswith((".", ",", "!", "?", "…", ":")) and \
                    len(_wnorm(words[k0 - 1]["word"])) <= 3:
                k0 -= 1
            inner = [k0]
        if (length > SHOT_SPLIT_SEC or (length > 2.8 and detail)) and inner:
            mid = sp["t0"] + length / 2

            def cost(k: int) -> float:
                comma = str(words[k - 1]["word"]).rstrip().endswith((",", "—", ":", ";", "."))
                return abs(words[k]["start"] - mid) - (0.6 if comma else 0.0)

            k = min(inner, key=cost)
            cut = float(words[k]["start"])
            first_text = " ".join(str(w["word"]) for w in words[sp["w0"]:k])
            second_text = " ".join(str(w["word"]) for w in words[k:sp["w1"] + 1])
            shots.append({**sp, "t1": cut, "w1": k - 1, "part": 0, "spoken": first_text})
            shots.append({**sp, "t0": cut, "w0": k, "part": 1, "spoken": second_text})
        else:
            shots.append({**sp, "part": 0, "spoken": " ".join(str(w["word"]) for w in words[sp["w0"]:sp["w1"] + 1])})
    for n, s in enumerate(shots):
        s["n"] = n
    return shots


def find_word(words: list[dict], w0: int, w1: int, pattern: str) -> int | None:
    """Индекс первого слова в [w0, w1], чья нормализованная форма подходит под ``pattern`` (основа)."""
    rx = re.compile(pattern)
    for k in range(max(0, w0), min(len(words) - 1, w1) + 1):
        if rx.search(_wnorm(words[k]["word"])):
            return k
    return None


# --------------------------------------------------------------------------- #
# Действия героя и события
# --------------------------------------------------------------------------- #

# (имя, основы слов, клип, место, точка интереса, разовый клип → потом покой)
ACTIONS = [
    ("phone_light", r"фонар|подсвет|экран\w* телефон|свет\w* телефон", "Idle_Lantern_Loop", "center", "doors", False),
    ("call", r"позвон|звонит|набра|номер", "Idle_TalkingPhone_Loop", "back", "doors", False),
    ("press", r"кнопк|нажа|нажим|жмет|жал|вызов|диспетчер", "Interact", "panel", "panel", True),
    # Стучит сам герой («стучит в двери»); «в двери постучали» снаружи — он слушает (listen).
    ("knock", r"(стучит|стучал|колотит|колотил|барабанит)\s+(в|по)", "Punch_Jab", "doors", "doors", True),
    ("look_up", r"потол|люк|сверху|наверх|над голов|крыш|трос", "Idle_Loop", "center", "hatch", False),
    ("listen", r"слыш|слуша|тишин|шорох|скреж|голос|шаг|постуч|стук", "Idle_Loop", "center", "doors", False),
    ("sit", r"\bсел\b|сел[аи]?\b|сидит|сидел|присел|на пол", "Crouch_Idle_Loop", "corner", "doors", False),
    ("jolt", r"тряс|дерн|рывок|тряхн|качну|пада|рухну|провали", "Hit_Chest", "center", "doors", True),
    ("display", r"табло|цифр|экран|показыва", "Idle_Loop", "center", "display", False),
    ("doors", r"двер|створ|раздвин|щел", "Push_Loop", "doors", "doors", False),
    ("wait", r"ждал|ждет|ждать|прошл\w* \d|минут|час", "Idle_FoldArms_Loop", "back", "doors", False),
]


# (событие, основы слов)
EVENTS = [
    ("blackout", r"^(погас|отключ|обесточ|темнот|блэкаут|блекаут|вырубил|отруб)"),
    ("flicker", r"^(мига|мерца|моргн|замига)"),
    ("scrape", r"^(за|про)?(скреж|скрип|скреб)"),
    ("jolt", r"^(за|по)?(тряс|дерн|рывок|тряхн|качнул|лязг|грохот|удар|пада|рухну|провали)"),
    ("flash", r"^(вспыш|искр|вспыхн)"),
    ("knock", r"^(стук|стуч|постуча)"),
    ("alarm", r"^(тревог|сирен|аварийн)"),
]


def _action_for(text: str, role: str) -> tuple:
    low = text.lower().replace("ё", "е")
    for act in ACTIONS:
        if re.search(act[1], low):
            return act
    return ("idle", "", "Idle_Loop", "center", None, False)


def detect_events(spans: list[dict], words: list[dict]) -> list[dict]:
    """События на словах: ``{kind, t, word, sentence}``; блэкаут из хука («Если блэкаут застал вас…») — только
    если дальше по сюжету свет не гаснет; цифры этажа на табло; акцент «Была лишь одна проблема»."""
    events: list[dict] = []
    for sp in spans:
        for k in range(sp["w0"], sp["w1"] + 1):
            w = _wnorm(words[k]["word"])
            for kind, pattern in EVENTS:
                if re.search(pattern, w):
                    events.append({"kind": kind, "t": float(words[k]["start"]), "word": words[k]["word"],
                                   "sentence": sp["i"], "role": sp["role"]})
                    break
        low = sp["text"].lower().replace("ё", "е")
        # Цифры на табло: «на 22 этаже», «табло показывало 23».
        for m in re.finditer(r"(\d{1,3})", low):
            tail, head = low[m.end():m.end() + 14], low[max(0, m.start() - 30):m.start()]
            unit = re.match(r"\s*(%|процент|градус|°|минут|секунд|час|метр|см|сантиметр|:)", tail)
            if unit or low[max(0, m.start() - 1):m.start()] == ":":
                continue   # «14% заряда», «−12 °C», «03:33» — не этаж на табло
            if re.search(r"^\s*(-?[а-я]{0,3})?\s*этаж", tail) or re.search(r"табло|экран|показыва|цифр", head):
                k = find_word(words, sp["w0"], sp["w1"], rf"^{m.group(1)}")
                t = float(words[k]["start"]) if k is not None else sp["t0"]
                events.append({"kind": "floor", "t": t, "value": m.group(1), "sentence": sp["i"], "role": sp["role"]})
        # Свет вернулся — только «свет загорелся/включили/дали» (а не «включил фонарик»).
        m = re.search(r"свет\w*\s+(снова\s+|опять\s+)?(загорел|включил|вернул|дали|появил)", low)
        if m:
            k = find_word(words, sp["w0"], sp["w1"], r"^(загорел|включил|вернул|дали|появил)")
            events.append({"kind": "light_back", "t": float(words[k]["start"]) if k is not None else sp["t0"],
                           "sentence": sp["i"], "role": sp["role"]})
        if re.search(r"одна проблем|лишь одн", low):
            k = find_word(words, sp["w0"], sp["w1"], r"^(был|одн|лишь)")
            events.append({"kind": "sting", "t": float(words[k]["start"]) if k is not None else sp["t0"],
                           "sentence": sp["i"], "role": sp["role"]})
    blackouts = [e for e in events if e["kind"] == "blackout"]
    story = [e for e in blackouts if e["role"] != "hook"]
    keep = (story or blackouts)[:1]
    events = [e for e in events if e["kind"] != "blackout" or e in keep]
    # Повторы одного события ближе 1.2 с — один.
    out: list[dict] = []
    for e in sorted(events, key=lambda x: x["t"]):
        if any(o["kind"] == e["kind"] and abs(o["t"] - e["t"]) < 1.2 and e["kind"] != "floor" for o in out):
            continue
        out.append(e)
    return out
