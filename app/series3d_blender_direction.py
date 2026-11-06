"""3D-серия: режиссура по фразам — правила, Gemini, последовательность (из ``app/series3d_blender.py``)."""

from __future__ import annotations

import json
import re
from typing import Any

from app.series3d_blender_stage import LOCS, show_items, rule_location, decal_from_text, _low

# --------------------------------------------------------------------------- #
# Словари режиссуры (строгие enum'ы схемы Gemini)
# --------------------------------------------------------------------------- #

ACTIONS = {
    "approach": "подойти к цели (target) и встать лицом к ней",
    "press_button": "нажать кнопку на панели (button: номер 1–12, bell — вызов диспетчера, all — все подряд)",
    "knock": "стучать кулаком в двери",
    "pry_doors": "раздвигать двери руками",
    "sit_corner": "сесть на пол в углу",
    "stand_up": "встать с пола",
    "phone_out": "достать телефон и смотреть в экран",
    "phone_light": "светить фонариком телефона",
    "look_display": "посмотреть на табло этажа над дверью",
    "look_ceiling": "посмотреть на потолок/люк",
    "look_camera": "посмотреть в камеру — обращается к зрителю",
    "flinch": "вздрогнуть от испуга",
    "shout": "кричать/звать на помощь",
    "listen": "прислушиваться",
    "wait": "ждать",
}
TARGETS = ("panel", "doors", "corner", "center", "display")
BUTTONS = tuple(str(n) for n in range(1, 13)) + ("bell", "all")
EMOTIONS = {
    # брови вверх (−1..1), «домиком» (−1 — сведены, сердито .. 1), глаза шире (−1 прищур .. 1)
    "calm": (0.0, 0.0, 0.0),
    "worried": (0.25, 0.65, 0.12),
    "scared": (0.65, 0.9, 0.55),
    "startled": (1.0, 0.5, 1.0),
    "determined": (-0.45, -0.55, -0.1),
    "exhausted": (-0.15, 0.45, -0.4),
    "relieved": (0.35, 0.15, 0.0),
}
CAMERAS = {
    "ots": "за плечом героя на то, что он видит/делает",
    "face": "крупно лицо",
    "detail": "деталь: рука и кнопка с лицом в профиль, телефон, табло, руки на дверях",
    "overhead": "общий сверху из угла кабины",
}
# Предпочтения плана по действию (первый — лучший; повтор прошлого плана пропускается).
CAMERA_PREFS = {
    "approach": ("overhead", "face"),
    "press_button": ("detail", "overhead", "ots"),
    "knock": ("ots", "face", "overhead"),
    "pry_doors": ("ots", "detail", "overhead"),
    "sit_corner": ("overhead", "face"),
    "stand_up": ("overhead", "face"),
    "phone_out": ("detail", "face", "ots"),
    "phone_light": ("ots", "face", "overhead"),
    "look_display": ("detail", "ots", "face"),
    "look_ceiling": ("face", "overhead"),
    "look_camera": ("face", "overhead"),
    "flinch": ("face", "overhead"),
    "shout": ("face", "ots"),
    "listen": ("face", "ots", "overhead"),
    "wait": ("overhead", "face"),
}
HAND_ACTIONS = ("press_button", "knock", "pry_doors")
SIT_OK = ("sit_corner", "wait", "listen", "look_display", "look_ceiling", "look_camera", "phone_out", "phone_light",
          "flinch", "shout", "stand_up")


# --------------------------------------------------------------------------- #
# Кадры и выбор действий
# --------------------------------------------------------------------------- #

_RX = {
    "bell": r"вызов|диспетчер|колокол",
    "all_buttons": r"вс[еяю]\s+кнопк|кнопки подряд|подряд",
    "press": r"кнопк|нажа|нажм|нажим|жмет|жмите|жал\b|тыка",
    "pry": r"раздвин|раздвиг|разжа|разжим|двер\w*\s+рук|откры\w*\s+двер|тяну\w*\s+створ",
    "knock_self": r"(стучит|стучал|колотит|колотил|барабанит|барабанил|стукнул)\s+(в|по)",
    "shout": r"крич|закрич|зовет|звал|позвал|помогите|эй\b|ау\b",
    "sit": r"\bсел\b|сел[аи]?\b|сидит|сидел|присел|на пол\b|в угол",
    "stand": r"встал|встает|поднялся|поднимается",
    "light": r"фонар|подсвет|посвет",
    "phone": r"телефон|заряд|связи|сети нет|смартфон|звонит|позвон",
    "display": r"табло|цифр|этаж",
    "ceiling": r"потол|люк|сверху|наверх|над голов|трос|крыш",
    "listen": r"слыш|слуша|голос|шаг|тишин|шорох|постуча|стук",
    "flinch": r"дерн|тряс|рывок|тряхн|качну|опусти|провали|упал|рухну|грохн",
    "wait": r"ждал|ждет|ждать|минут|час|сберечь|сил",
    "viewer": r"\bвы\b|\bвас\b|\bвам\b|\bвами\b|\bне\s+\w+те\b|\bжмите\b|\bпытайтесь\b",
}


def button_from_text(text: str) -> str:
    low = _low(text)
    if re.search(_RX["bell"], low):
        return "bell"
    if re.search(_RX["all_buttons"], low):
        return "all"
    hit = re.search(r"кнопк\w*\s+(\d{1,2})|(\d{1,2})\s*-?\s*(?:й|ю|го)?\s*кнопк", low)
    if hit:
        return str(max(1, min(12, int(hit.group(1) or hit.group(2)))))
    return "1"


def rule_choice(shot: dict, events: list[dict], dark: bool) -> dict:
    """Действие/эмоция/план по словам фразы — запасной путь без Gemini."""
    low = _low(shot.get("spoken") or shot.get("text"))
    role = shot.get("role") or ""
    kinds = {e["kind"] for e in events}
    action, target, button = "wait", "center", "1"
    if re.search(_RX["pry"], low):
        action, target = "pry_doors", "doors"
    elif re.search(_RX["press"], low) or re.search(_RX["bell"], low):
        action, target, button = "press_button", "panel", button_from_text(low)
    elif re.search(_RX["knock_self"], low):
        action, target = "knock", "doors"
    elif re.search(_RX["shout"], low):
        action, target = "shout", "doors"
    elif re.search(_RX["sit"], low):
        action, target = "sit_corner", "corner"
    elif re.search(_RX["stand"], low):
        action = "stand_up"
    elif re.search(_RX["light"], low):
        action = "phone_light"
    elif re.search(_RX["phone"], low):
        action = "phone_out"
    elif "blackout" in kinds or "jolt" in kinds:
        action = "flinch"
    elif re.search(_RX["listen"], low) or "knock" in kinds:
        action, target = "listen", "doors"
    elif re.search(_RX["ceiling"], low) or "scrape" in kinds:
        action = "look_ceiling"
    elif re.search(_RX["display"], low):
        action, target = "look_display", "display"
    elif re.search(_RX["flinch"], low):
        action = "flinch"
    elif role == "hook" and re.search(_RX["viewer"], low):
        action = "look_camera"
    elif re.search(_RX["wait"], low):
        action = "wait"
    elif role == "hook":
        action = "look_camera"
    emotion = {"hook": "worried", "problem": "scared", "solution": "determined", "mistake": "determined",
               "finale": "scared"}.get(role, "worried")
    if "blackout" in kinds or "jolt" in kinds or action == "flinch":
        emotion = "startled"
    elif action in ("sit_corner", "wait") and dark:
        emotion = "exhausted"
    return {"action": action, "target": target, "button": button, "emotion": emotion,
            "camera": CAMERA_PREFS[action][0], "speaker": "narrator", "by": "rules"}


SPEAKERS = ("narrator", "hero", "other")
SPEECH_VERB = (r"сказал|сказала|крикнул|крикнула|закричал|закричала|позвал|позвала|прошептал|прошептала|ответил|"
               r"ответила|спросил|спросила|произн[её]с|заорал|завопил|шепнул|пробормотал|выдохнул|взмолился|повторил")
OTHER_SPEAKER = (r"голос|кто-то|кто то|спасател|диспетчер|сосед|мужчин|женщин|охранник|динамик|громкоговор|"
                 r"оператор|снаружи|за дверью|незнаком|ш[её]пот|телефон|раздал|послышал|донес|силуэт|фигур")
HERO_SUBJECT = r"\bон\b|\bпарень\b|\bя\b|\bмы\b|\bгерой\b"
_OPEN_Q, _CLOSE_Q = "«“„\"", "»”\""


def speech_labels(text: str, hero_names: tuple[str, ...] = ()) -> list[str]:
    """Кто говорит каждое слово фразы: ``narrator`` (закадровый рассказчик), ``hero`` (прямая речь героя) или
    ``other`` (реплика другого: голос за дверью, диспетчер). Реплика — в кавычках или после «сказал/крикнул…:»;
    кто сказал — по словам вне реплики (голос/спасатель/диспетчер… — другой, иначе — герой). Без реплик — всё
    рассказчик: рот героя под закадровый голос не двигается."""
    tokens = str(text or "").split()
    labels = ["narrator"] * len(tokens)
    spans: list[tuple[int, int]] = []
    start = None
    for i, t in enumerate(tokens):
        core = t.strip(".,!?…;:—–-")
        if not core:
            continue
        if start is None and core[:1] in _OPEN_Q:
            start = i
        if start is not None and (core[-1:] in _CLOSE_Q) and (i > start or len(core) > 1):
            spans.append((start, i))
            start = None
    if start is not None:
        spans.append((start, len(tokens) - 1))
    low = [_low(t) for t in tokens]
    dashes = [i for i, t in enumerate(tokens) if t in ("—", "–", "-")]
    if not spans and dashes and dashes[0] == 0:
        # диалог через тире: «— Эй! — позвал он.»
        nxt = next((i for i in dashes[1:] if re.search(SPEECH_VERB, " ".join(low[i:]))), None)
        if nxt is not None and nxt > 1:
            spans.append((1, nxt - 1))
    if not spans:
        for k, t in enumerate(tokens):
            if t.endswith(":") and k + 1 < len(tokens) and re.search(SPEECH_VERB, " ".join(low[: k + 1])):
                spans.append((k + 1, len(tokens) - 1))
                break
    if not spans:
        return labels
    inside = {i for a, b in spans for i in range(a, b + 1)}
    outside = " ".join(w for i, w in enumerate(low) if i not in inside)
    if not re.search(SPEECH_VERB, outside) and not any(t.endswith(":") for t in tokens):
        return labels          # цитата без «сказал» (надпись, табличка) — читает рассказчик
    names = "|".join(re.escape(_low(n_)[:max(3, len(n_) - 1)]) for n_ in hero_names if n_)
    hero_said = re.search(HERO_SUBJECT + (f"|{names}" if names else ""), outside)
    # герой — только если сказал он сам (он/парень/имя героя) и это не голос/шёпот/телефон/силуэт
    who = "hero" if hero_said and not re.search(OTHER_SPEAKER, outside) else "other"
    for i in inside:
        labels[i] = who
    return labels


def hero_names(script: dict | None) -> tuple[str, ...]:
    """Имена героя из сценария (new_characters: name/key) — по ним реплика приписывается герою."""
    out = []
    for c in (script or {}).get("new_characters") or []:
        for k in ("name", "key"):
            if c.get(k):
                out.append(str(c[k]))
    return tuple(out)


def dialog_lines(sentences: list[dict], names: tuple[str, ...] = ()) -> list[dict]:
    """Озвучка по ролям: ``[{speaker: narrator|hero, text}]`` подряд (реплики «других» читает рассказчик)."""
    out: list[dict] = []
    for sent in sentences:
        tokens = str(sent.get("text") or "").split()
        for tok, lab in zip(tokens, speech_labels(sent.get("text") or "", names)):
            who = "hero" if lab == "hero" else "narrator"
            if out and out[-1]["speaker"] == who:
                out[-1]["text"] += " " + tok
            else:
                out.append({"speaker": who, "text": tok})
    return out


def has_hero_lines(sentences: list[dict], names: tuple[str, ...] = ()) -> bool:
    return any(lab == "hero" for s_ in sentences for lab in speech_labels(s_.get("text") or "", names))


GEMINI_SCHEMA = {
    "type": "object",
    "properties": {"shots": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "n": {"type": "integer"},
            "action": {"type": "string", "enum": list(ACTIONS)},
            "target": {"type": "string", "enum": list(TARGETS)},
            "button": {"type": "string", "enum": list(BUTTONS)},
            "emotion": {"type": "string", "enum": list(EMOTIONS)},
            "camera": {"type": "string", "enum": list(CAMERAS)},
            "speaker": {"type": "string", "enum": list(SPEAKERS)},
            "location": {"type": "string", "enum": list(LOCS)},
            "props": {"type": "array", "items": {"type": "string"}},
            "decal": {"type": "string"},
        },
        "required": ["n", "action", "emotion", "camera", "speaker", "location"]}}},
    "required": ["shots"],
}


def gemini_prompt(title: str, shots: list[dict], events_by_shot: dict[int, list[dict]]) -> str:
    lines = [
        "Ты режиссёр короткой вертикальной 3D-серии. Герой один — игрушечный мальчик в жёлтом дождевике. Голос за кадром "
        "читает текст; на каждый кадр выбери МЕСТО (location — где герой по тексту, только из списка), вещи, которые текст "
        "называет и которые надо показать (props — из списка вещей этого места), надпись на стене, если текст её называет "
        "(decal: «1998», «23», до 14 знаков, иначе пусто), поставленное действие героя, эмоцию лица и план камеры.",
        "Места (location) и вещи в них:\n" + "\n".join(f"{k} — {v['label']}: {', '.join(v['props'])}" for k, v in LOCS.items()),
        "Место меняется только когда текст говорит, что герой туда перешёл (раздвинул двери и вышел в коридор, вылез через "
        "люк в шахту); иначе — там же, где был в прошлом кадре. Кнопки, табло, поручни — только в кабине.",
        f"Серия: «{title}».",
        "Действия (action): " + "; ".join(f"{k} — {v}" for k, v in ACTIONS.items()) + ".",
        "Цель (target) — для approach: " + ", ".join(TARGETS) + ". Кнопка (button) — для press_button.",
        "Эмоции (emotion): " + ", ".join(EMOTIONS) + ".",
        "Планы (camera): " + "; ".join(f"{k} — {v}" for k, v in CAMERAS.items()) + ".",
        "Правила: действие показывает то, о чём фраза (кнопки — press_button, «раздвинул двери» — pry_doors, стучат снаружи — "
        "listen, сам стучит — knock, сел — sit_corner); хук на «вы» — герой смотрит в камеру (look_camera) или показывает "
        "ошибку действием; один и тот же план камеры два кадра подряд нельзя; деталь (detail) — когда важна рука/вещь; "
        "общий сверху (overhead) — для пространства и одиночества; крупно лицо — на реакции. speaker — кто говорит "
        "реплику в кадре: narrator — закадровый рассказчик (почти всегда: рассказ в 3-м лице, обращение к зрителю), "
        "hero — только прямая речь самого героя (в кавычках или «— крикнул Даня»), other — реплика другого (голос за "
        "дверью, диспетчер). Рот героя двигается только на hero.",
        "Кадры (n, роль, время, события на словах, текст):",
    ]
    for s in shots:
        ev = ", ".join(sorted({e["kind"] for e in events_by_shot.get(s["n"], [])})) or "—"
        lines.append(f'{s["n"]}. [{s.get("role") or "—"}] {s["t1"] - s["t0"]:.1f} с; события: {ev}; «{s.get("spoken") or s["text"]}»')
    lines.append("Ответ — JSON по схеме: shots[] на каждый n.")
    return "\n".join(lines)


def gemini_choices(title: str, shots: list[dict], events_by_shot: dict[int, list[dict]], client: Any = None,
                   model: str | None = None) -> dict[int, dict]:
    """Выбор Gemini (бесплатный текст): ``{n: {action, target, button, emotion, camera, speaks}}``; ошибка — ``{}``."""
    from app.ai.gemini import GeminiClient, _extract_response_text
    from app.settings import settings

    if client is None and not settings.gemini_api_key:
        return {}
    payload = {"contents": [{"role": "user", "parts": [{"text": gemini_prompt(title, shots, events_by_shot)}]}],
               "generationConfig": {"temperature": 0.4, "responseMimeType": "application/json",
                                    "responseSchema": GEMINI_SCHEMA}}
    response = (client or GeminiClient(timeout=90)).generate_content(model or settings.gemini_text_model, payload)
    data = json.loads(_extract_response_text(response) or "{}")
    out: dict[int, dict] = {}
    for item in data.get("shots") or []:
        try:
            n = int(item["n"])
        except (KeyError, TypeError, ValueError):
            continue
        if item.get("action") not in ACTIONS or item.get("emotion") not in EMOTIONS or item.get("camera") not in CAMERAS:
            continue
        out[n] = {"action": item["action"], "target": item.get("target") if item.get("target") in TARGETS else "center",
                  "button": item.get("button") if item.get("button") in BUTTONS else "1",
                  "emotion": item["emotion"], "camera": item["camera"],
                  "speaker": item.get("speaker") if item.get("speaker") in SPEAKERS else "narrator", "by": "gemini",
                  "location": item.get("location") if item.get("location") in LOCS else "",
                  "props": [str(x)[:40] for x in (item.get("props") or [])][:4],
                  "decal": str(item.get("decal") or "").strip().upper()[:14]}
    return out


# Часы/телефон в тексте («на часах было 03:33», «телефон показывал 14%») — герой держит телефон, на экране значение.
SCREEN_CUE = (r"на час(ах|ы)\b|час\w* показ|телефон\w* показ|на экран|экран\w* (телефон|показ)|на телефон\w*\s+было|"
              r"посмотрел\w* на (часы|телефон)|датчик\w* .{0,20}час")
HOOK_FACE_SEC = 1.75     # крючок: сначала лицо героя с реакцией (под названием серии), потом названная вещь


def screen_value(text: str) -> str:
    """Что показать на экране телефона: время «03:33», температура «−12 °C» или процент «14%» — первое найденное."""
    raw = str(text or "")
    hit = re.search(r"(?<![\d:])(\d{1,2})[:.](\d{2})(?![\d:])", raw)
    if hit and int(hit.group(1)) < 24 and int(hit.group(2)) < 60:
        return f"{int(hit.group(1)):02d}:{hit.group(2)}"
    hit = re.search(r"([−-]\s?)?(\d{1,3})\s*°\s*[CcСс]?", raw)
    if hit:
        return f"{'−' if hit.group(1) else ''}{hit.group(2)} °C"
    hit = re.search(r"(\d{1,3})\s*%", raw)
    if hit:
        return f"{hit.group(1)}%"
    return ""


def screen_shots(shots: list[dict]) -> dict[int, str]:
    """Планы, где фраза называет часы/экран телефона: ``{индекс: значение на экране}``. Значения нет в самом плане —
    берём из его фразы (соседние планы той же фразы: «Датчик на часах упал | до −12 °C»)."""
    out: dict[int, str] = {}
    for k, s in enumerate(shots):
        if not re.search(SCREEN_CUE, _low(s.get("text"))):
            continue
        value = screen_value(s.get("text"))
        if not value:
            sent = set(s.get("sentences") or [])
            same = [x for x in shots if sent & set(x.get("sentences") or [])]
            value = next((v for v in (screen_value(x.get("text")) for x in same) if v), "")
        if value:
            out[k] = value
    return out


def hook_items(s: dict) -> list[dict]:
    """Вещи, которые названы в первой фразе (кроме места)."""
    loc = s.get("location") if s.get("location") in LOCS else "cabin"
    return [x for x in show_items(loc, s) if not str(x["item"]).startswith("место")]


def split_hook(shots: list[dict]) -> list[dict]:
    """Первый план — крупно лицо героя с реакцией (не табло). Если фраза называет вещь (табло, надпись), а план
    длинный — второй кусок той же фразы показывает её. Повторный вызов ничего не меняет (``hook``)."""
    if not shots or shots[0].get("hook"):
        return shots
    first = dict(shots[0])
    span = float(first.get("t1") or 0.0) - float(first.get("t0") or 0.0)
    items = hook_items(first)
    if first["action"] in ("look_display", "wait", "approach", "phone_light", "look_ceiling", *HAND_ACTIONS):
        first["action"] = "look_camera"
        first["target"] = "center"
    if first.get("emotion") in ("calm", "relieved", "determined", None):
        first["emotion"] = "worried"
    first["camera"] = "face"
    first["hook"] = 1
    if not items or span < HOOK_FACE_SEC + 1.0:
        return [{**first, "show_none": bool(items)}, *shots[1:]]
    cut = round(float(first["t0"]) + max(HOOK_FACE_SEC, min(span - 1.0, span * 0.55)), 3)
    second = {**dict(shots[0]), "t0": cut, "action": "look_display", "target": "display", "camera": "detail", "hook": 2}
    first = {**first, "t1": cut, "show_none": True}
    out = [first, second, *shots[1:]]
    for n_, s_ in enumerate(out):
        out[n_] = {**s_, "n": n_}
    return out


def fix_sequence(shots: list[dict]) -> list[dict]:
    """Выполнимость и монтаж: сидя — сначала встать (``stand_first``), стоя «встать» — ждать; кнопка — достижимая;
    план камеры не повторяет прошлый. Рот говорит только на прямой речи героя (``plan_shots``: ``speech``).
    Крючок — крупно лицо (``split_hook``); часы/экран в тексте — телефон в руке с этим значением (``screen``)."""
    shots = split_hook([dict(s) for s in shots])
    screens = screen_shots(shots)
    posture = "stand"
    prev_cam = ""
    out = []
    for k, s in enumerate(shots):
        s = dict(s)
        if k in screens and s["action"] not in HAND_ACTIONS and not s.get("hook"):
            s["screen"] = screens[k]
            s["action"] = "phone_out"
            s["camera"] = "detail"
        a = s["action"]
        s["stand_first"] = False
        if a == "stand_up" and posture == "stand":
            s["action"] = a = "wait"
        if a == "sit_corner" and posture == "sit":
            s["action"] = a = "wait"
        if posture == "sit" and a not in SIT_OK:
            s["stand_first"] = True
        loc = s.get("location") if s.get("location") in LOCS else "cabin"
        if out and out[-1].get("location") != loc:
            posture = "stand"     # другое место — склейкой, герой стоит
            s["stand_first"] = False
        s["location"] = loc
        if loc != "cabin":
            # кнопки и табло — только в кабине; двери лифта есть в коридоре и на площадке
            has_doors = "doors" in LOCS[loc]["spots"]
            if a == "press_button" or (a in ("pry_doors", "knock") and not has_doors):
                s["action"] = a = "approach" if has_doors else "look_display"
                s["target"] = "doors" if has_doors else "center"
            elif a == "sit_corner":
                s["action"] = a = "wait"
        if a == "press_button":
            s["target"] = "panel"
            s["button"] = s.get("button") if s.get("button") in BUTTONS else "1"
        elif a in ("knock", "pry_doors", "shout", "listen"):
            s["target"] = "doors"
        elif a == "sit_corner":
            s["target"] = "corner"
        if a == "sit_corner" or (posture == "sit" and a in SIT_OK and a != "stand_up"):
            posture = "sit"
        else:
            posture = "stand"
        cam = s.get("camera") if s.get("camera") in CAMERAS else CAMERA_PREFS[a][0]
        if cam not in CAMERA_PREFS[a] and a in ("look_camera", "sit_corner", "stand_up", "flinch", "look_ceiling"):
            cam = CAMERA_PREFS[a][0]   # спиной к объективу/деталь без детали — не тот план
        if cam == prev_cam:
            cam = next((c for c in CAMERA_PREFS[a] if c != prev_cam), None) or next(c for c in CAMERAS if c != prev_cam)
        if cam == "ots" and a == "approach":
            cam = "overhead" if prev_cam != "overhead" else "face"   # идёт от камеры — со спины лишь капюшон
        if cam == "face" and a == "press_button":
            # лицом к панели вбок — спереди виден только капюшон: деталь (рука, кнопка, профиль) или сверху
            cam = "detail" if prev_cam != "detail" else "overhead"
        if cam == "ots" and a in HAND_ACTIONS:
            # со спины руки не видно — рука, кнопка/двери и лицо сбоку (или сверху, если деталь только что была)
            cam = "detail" if prev_cam != "detail" else "overhead"
        if cam == "detail" and a not in ("press_button", "phone_out", "phone_light", "look_display", "knock", "pry_doors"):
            cam = next(c for c in (*CAMERA_PREFS[a], "face", "overhead") if c not in ("detail", prev_cam))
        if s.get("screen"):
            cam = "detail"        # экран телефона со значением — только деталью (даже после детали)
        if s.get("hook") == 1:
            cam = "face"          # крючок — лицо героя
        s["camera"] = cam
        s["posture"] = posture
        prev_cam = cam
        out.append(s)
    return out


def plan_shots(script: dict, words: list[dict], total: float, *, gemini: bool = True, client: Any = None) -> dict:
    """Сценарий + слова озвучки → ``{shots, events, words, director}``: кадры с действием, эмоцией, планом."""
    from app import series3d as S3

    sentences = [s for s in (script.get("sentences") or []) if str(s.get("text") or "").strip()]
    if not sentences:
        raise S3.DirectionError("в сценарии нет фраз")
    if not words:
        raise S3.DirectionError("нет слов озвучки")
    words = S3.align_words(" ".join(s["text"] for s in sentences), words)
    spans = S3.sentence_spans(sentences, words, total)
    shots = S3.split_shots(spans, words)
    for k, s in enumerate(shots):
        s["n"] = k
        s["t1"] = shots[k + 1]["t0"] if k + 1 < len(shots) else float(total)
    events = S3.detect_events(spans, words)
    by_shot: dict[int, list[dict]] = {}
    for e in events:
        k = next((s["n"] for s in shots if s["t0"] <= e["t"] < s["t1"]), len(shots) - 1)
        by_shot.setdefault(k, []).append(e)
    for sp in spans:
        low_ = _low(sp["text"])
        if re.search(r"двер|створ", low_) and re.search(r"схлопнул|захлопнул|закрыл|сомкнул|сдвинул", low_):
            k_ = S3.find_word(words, sp["w0"], sp["w1"], r"^(схлопнул|захлопнул|закрыл|сомкнул|сдвинул)")
            events.append({"kind": "doors_close", "t": float(words[k_]["start"]) if k_ is not None else sp["t0"],
                           "sentence": sp["i"], "role": sp["role"]})
    blackout = next((e["t"] for e in events if e["kind"] == "blackout"), None)
    choices: dict[int, dict] = {}
    director, error = "rules", ""
    if gemini:
        try:
            choices = gemini_choices(str(script.get("title") or ""), shots, by_shot, client=client)
            director = "gemini" if choices else "rules"
        except Exception as exc:  # noqa: BLE001 - сеть/квота/ответ не по схеме — правила
            error = f"{type(exc).__name__}: {str(exc)[:160]}"
    names = hero_names(script)
    labels = [lab for sent in sentences for lab in speech_labels(sent["text"], names)]
    labels = (labels + ["narrator"] * len(words))[: len(words)]
    planned = []
    where = "cabin"
    decal_at: dict[str, str] = {}
    for s in shots:
        dark = blackout is not None and s["t0"] + 0.3 >= blackout
        pick = choices.get(s["n"]) or rule_choice(s, by_shot.get(s["n"], []), dark)
        text_ = s.get("spoken") or s["text"]
        by_rules = rule_location(text_, where)
        loc = pick.get("location") if pick.get("location") in LOCS else by_rules
        if by_rules != where and loc == where:
            loc = by_rules       # текст явно назвал другое место — правило сильнее
        where = loc
        decal = pick.get("decal") or decal_from_text(text_)
        if re.fullmatch(r"\d{1,2}[:.]\d{2}", decal or ""):
            decal = ""            # время («03:33») — часы/телефон, не надпись на стене
        if decal and decal_at.setdefault(loc, decal) != decal:
            decal = ""            # на месте одна надпись — первая названная; другой в кадре не будет
        pick = {**pick, "location": loc, "decal": decal, "props": list(pick.get("props") or [])}
        idx = [k for k in range(len(words)) if s["t0"] <= float(words[k]["start"]) < s["t1"]]
        said = {labels[k] for k in idx} - {"narrator"}
        if said and pick.get("speaker") in ("hero", "other") and pick.get("by") == "gemini":
            for k in idx:   # Gemini уточняет, чья найденная реплика (кавычки/«сказал:» нашли правила)
                if labels[k] != "narrator":
                    labels[k] = pick["speaker"]
            said = {pick["speaker"]}
        speaker = "hero" if "hero" in said else ("other" if said else "narrator")
        planned.append({"n": s["n"], "t0": round(float(s["t0"]), 3), "t1": round(float(s["t1"]), 3),
                        "text": s.get("spoken") or s["text"], "role": s.get("role") or "",
                        "sentences": s.get("sentences") or [s.get("i", 0)], **pick,
                        "speaker": speaker, "speaks": speaker == "hero"})
    windows: list[list[float]] = []
    for k, lab in enumerate(labels):
        if lab != "hero":
            continue
        a_, b_ = float(words[k]["start"]), float(words[k]["end"])
        if windows and a_ - windows[-1][1] < 0.35:
            windows[-1][1] = b_
        else:
            windows.append([a_, b_])
    return {"shots": fix_sequence(planned), "events": events, "words": words, "director": director,
            "director_error": error, "speech": {"labels": labels, "hero_windows": [[round(a_, 3), round(b_, 3)]
                                                                                   for a_, b_ in windows]}}
