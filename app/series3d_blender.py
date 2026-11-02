"""3D-серия в Blender: сценарий → режиссура по фразам → задание рендера → кадры (кусками, с кэшем) → mp4.

Чистый Python без ``bpy``: эту же режиссуру считают сцена Blender (``tools/blender/series_scene.py``),
сервис (задание в очередь хост-воркера, ``app/render_queue.py``) и тесты.

**Режиссура.** Фразы озвучки → кадры (``app.series3d.split_shots``), события на словах
(``app.series3d.detect_events``: погас свет, дёрнулся, стук, табло…). На каждый кадр — поставленное действие из
фиксированного словаря ``ACTIONS`` (подойти, нажать кнопку N, стучать, раздвигать двери, сесть в угол/встать,
телефон, взгляд на табло/потолок/камеру, вздрогнуть, кричать, слушать, ждать) + эмоция лица ``EMOTIONS`` + план
камеры ``CAMERAS`` (за плечом / крупно лицо / деталь / общий сверху). Выбирает Gemini (бесплатно) по JSON-схеме
со строгими enum'ами; без ключа или при ошибке — правила по словам фразы. Потом общая правка
(``fix_sequence``): действие должно быть выполнимо (сидя — сначала встать), один план камеры подряд не
повторяется.

**Исполнение** (``perform``): покадровые дорожки — клипы KayKit с наплывами (``motion3d.ClipTrack``), путь
корня (шаги или склейка-«телепорт», если идти дольше половины кадра), поворот на месте шагами, цели рук для IK
(нажатия, стук, двери, телефон), взгляд, мимика (эмоция + испуг на событиях + моргание), рот — лип-синк по
таймингам слов (визем-группы гласных) с огибающей озвучки, свет (мигание, блэкаут, аварийный, табло), двери,
тряска камеры. Камеру по плану кадра ставит сцена Blender по настоящим костям героя.

**Рендер** (``render_local``): одна сцена лифта на всю серию, кадры — кусками по планам в кэш
``<cache>/<хэш плана>/``; хэш — от покадровых дорожек плана, поэтому правка одной фразы перерисует только её
кадры; упал — повторный запуск дорисует с того кадра, где остановился. Склейка → mp4 без звука (звук, субтитры,
HUD, титры кладёт холст сервиса — ``app/stories_3d.py``).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from app import motion3d as m

FPS = 30
SCENE_VERSION = 3          # меняй, когда меняется сцена Blender: кэш кусков станет недействительным
S = 0.5                    # масштаб героя KayKit (2.17 → 1.08 м)

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
    "approach": ("overhead", "ots", "face"),
    "press_button": ("detail", "ots", "face"),
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
LIPSYNC_MODES = ("speaking", "all", "off")

# --------------------------------------------------------------------------- #
# Лифт (общая геометрия для сцены Blender и планировщика; z — вверх, герой при курсе 0 смотрит в −Y, к дверям)
# --------------------------------------------------------------------------- #

W, D, H = 1.70, 1.80, 2.15
FRONT = -D / 2                     # стена с дверями
LEFT = -W / 2                      # стена с кнопочной панелью
PANEL_Y = -0.22
BTN_X = LEFT + 0.026               # лицевая поверхность кнопок
BTN_Z0, BTN_DZ, BTN_DY = 0.64, 0.056, 0.064
BELL = (BTN_X, PANEL_Y, 0.575)     # вызов диспетчера — внизу панели: чиби-рука выше ~0.85 м не достаёт
BTN_NORMAL = (1.0, 0.0, 0.0)
DOOR_FACE_Y = FRONT - 0.0325       # лицевая поверхность створок (утоплены за обрамлением)
DOOR_NORMAL = (0.0, 1.0, 0.0)
DOOR_W = 0.86
DISPLAY = (0.0, FRONT + 0.03, 1.93)
CEIL_LIGHT = (0.0, 0.05, H - 0.02)
HATCH = (0.25, 0.30, H)            # люк в потолке
EMERG = (-0.42, FRONT + 0.05, H - 0.07)
REACH = (0.242 + 0.259 + 0.074 + 0.10) * S
SHOULDER_R = (0.212 * S, 0.0, 1.107 * S)   # правое плечо от корня (x — вправо от взгляда, y — вперёд)
SHOULDER_L = (-0.212 * S, 0.0, 1.107 * S)
MAX_PRESS_Z = 0.83                 # выше чиби-рука не достаёт — берём кнопку ниже в том же столбце
FACE_Z = {"stand": 1.62 * S, "sit": 1.28 * S}
SIT_DROP = 0.17                    # насколько ниже грудь сидя (клип Sit_Floor_Idle)

# Длины клипов KayKit (с) и шаг — замер в Blender (сцена сверяет и пишет в отчёт расхождение).
CLIP_LEN = {"Idle_A": 1.067, "Idle_B": 2.133, "Walking_A": 1.067, "Walking_B": 1.067, "Sit_Floor_Down": 1.0,
            "Sit_Floor_Idle": 4.0, "Sit_Floor_StandUp": 1.133, "Hit_A": 0.667, "Hit_B": 0.867}
STRIDE = 0.6708            # ед/с клипа Walking_A без масштаба (×S = м/с)
CLIPS_NEEDED = tuple(CLIP_LEN)


def button_pos(button: str | int) -> tuple[float, float, float]:
    """Центр лицевой поверхности кнопки: 1–12 в два столбца снизу вверх, ``bell`` — вызов диспетчера."""
    if str(button) == "bell":
        return BELL
    n = max(1, min(12, int(button)))
    col, row = (n - 1) % 2, (n - 1) // 2
    return (BTN_X, PANEL_Y + 0.032 - col * BTN_DY, BTN_Z0 + row * BTN_DZ)


def reachable(button: str | int) -> str:
    """Кнопка, до которой герой дотянется: выше ``MAX_PRESS_Z`` — та же колонка ниже."""
    if str(button) == "bell":
        return "bell"
    n = max(1, min(12, int(button)))
    while n > 2 and button_pos(n)[2] > MAX_PRESS_Z:
        n -= 2
    return str(n)


def facing(heading: float) -> tuple[float, float]:
    return (math.sin(heading), -math.cos(heading))


def to_world(root: tuple[float, float], heading: float, local: tuple[float, float, float]) -> tuple[float, float, float]:
    """Точка в системе героя (x — вправо от взгляда, y — вперёд, z — вверх) → мир."""
    fx, fy = facing(heading)
    rx, ry = fy, -fx   # вправо от взгляда: при курсе 0 (взгляд −Y) правая рука — к −X
    return (root[0] + local[0] * rx + local[1] * fx, root[1] + local[0] * ry + local[1] * fy, local[2])


PRESS_TURN = math.radians(-22)     # к панели вполоборота: в профиль к камере у дверей
KNOCK_POINT = (-0.10, DOOR_FACE_Y, 0.60)


def press_stand(buttons: list[str]) -> tuple[tuple[float, float], float]:
    """Где встать и куда смотреть, чтобы нажать кнопки (середина набора)."""
    pts = [button_pos(b) for b in buttons]
    mid = tuple(sum(p[i] for p in pts) / len(pts) for i in range(3))
    fx, fy = facing(PRESS_TURN)
    st = m.stand_point(mid, BTN_NORMAL, SHOULDER_R, REACH, comfort=0.95, facing=(fx, fy, 0.0), ahead=0.08)
    return (round(st[0], 4), round(st[1], 4)), PRESS_TURN


def spot_for(name: str) -> tuple[tuple[float, float], float]:
    """Место героя (корень) и курс для цели."""
    if name == "panel":
        return press_stand(["1"])
    if name == "doors":
        return (0.0, FRONT + 0.36), 0.0
    if name == "knock":
        h = math.radians(14)
        fx, fy = facing(h)
        st = m.stand_point(KNOCK_POINT, DOOR_NORMAL, SHOULDER_R, REACH, comfort=0.96, facing=(fx, fy, 0.0), ahead=0.02)
        return (round(st[0], 4), round(st[1], 4)), h
    if name == "pry":
        return (0.0, FRONT + 0.31), 0.0
    if name == "corner":
        return (0.47, 0.50), m.heading_of((-0.55, -1.0))
    if name == "display":
        return (0.05, -0.25), 0.0
    return (0.0, 0.05), 0.0   # center


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


def _low(text: str) -> str:
    return str(text or "").lower().replace("ё", "е")


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
    speaks = action in ("look_camera", "shout")
    return {"action": action, "target": target, "button": button, "emotion": emotion,
            "camera": CAMERA_PREFS[action][0], "speaks": speaks, "by": "rules"}


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
            "speaks": {"type": "boolean"},
        },
        "required": ["n", "action", "emotion", "camera", "speaks"]}}},
    "required": ["shots"],
}


def gemini_prompt(title: str, shots: list[dict], events_by_shot: dict[int, list[dict]]) -> str:
    lines = [
        "Ты режиссёр короткой вертикальной 3D-серии. Локация одна: кабина лифта (двери спереди, кнопочная панель на левой "
        "стене, табло этажа над дверью, люк в потолке). Герой один — игрушечный мальчик в жёлтом дождевике. Голос за кадром "
        "читает текст; на каждый кадр выбери поставленное действие героя, эмоцию лица и план камеры.",
        f"Серия: «{title}».",
        "Действия (action): " + "; ".join(f"{k} — {v}" for k, v in ACTIONS.items()) + ".",
        "Цель (target) — для approach: " + ", ".join(TARGETS) + ". Кнопка (button) — для press_button.",
        "Эмоции (emotion): " + ", ".join(EMOTIONS) + ".",
        "Планы (camera): " + "; ".join(f"{k} — {v}" for k, v in CAMERAS.items()) + ".",
        "Правила: действие показывает то, о чём фраза (кнопки — press_button, «раздвинул двери» — pry_doors, стучат снаружи — "
        "listen, сам стучит — knock, сел — sit_corner); хук на «вы» — герой смотрит в камеру (look_camera) или показывает "
        "ошибку действием; один и тот же план камеры два кадра подряд нельзя; деталь (detail) — когда важна рука/вещь; "
        "общий сверху (overhead) — для пространства и одиночества; крупно лицо — на реакции. speaks=true — только когда "
        "текст звучит как реплика самого героя или он кричит (рот двигается под голос), иначе false.",
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
                  "emotion": item["emotion"], "camera": item["camera"], "speaks": bool(item.get("speaks")),
                  "by": "gemini"}
    return out


def fix_sequence(shots: list[dict]) -> list[dict]:
    """Выполнимость и монтаж: сидя — сначала встать (``stand_first``), стоя «встать» — ждать; кнопка — достижимая;
    план камеры не повторяет прошлый; рот говорит только в ``speaks``."""
    posture = "stand"
    prev_cam = ""
    out = []
    for s in shots:
        s = dict(s)
        a = s["action"]
        s["stand_first"] = False
        if a == "stand_up" and posture == "stand":
            s["action"] = a = "wait"
        if a == "sit_corner" and posture == "sit":
            s["action"] = a = "wait"
        if posture == "sit" and a not in SIT_OK:
            s["stand_first"] = True
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
        if a == "look_camera":
            s["speaks"] = True      # смотрит в объектив — говорит текст сам (рот под озвучку)
        cam = s.get("camera") if s.get("camera") in CAMERAS else CAMERA_PREFS[a][0]
        if cam not in CAMERA_PREFS[a] and a in ("look_camera", "sit_corner", "stand_up", "flinch", "look_ceiling"):
            cam = CAMERA_PREFS[a][0]   # спиной к объективу/деталь без детали — не тот план
        if cam == prev_cam:
            cam = next((c for c in CAMERA_PREFS[a] if c != prev_cam), None) or next(c for c in CAMERAS if c != prev_cam)
        if cam == "ots" and a in HAND_ACTIONS:
            # со спины руки не видно — рука, кнопка/двери и лицо сбоку (или сверху, если деталь только что была)
            cam = "detail" if prev_cam != "detail" else "overhead"
        if cam == "detail" and a not in ("press_button", "phone_out", "phone_light", "look_display", "knock", "pry_doors"):
            cam = next(c for c in (*CAMERA_PREFS[a], "face", "overhead") if c not in ("detail", prev_cam))
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
    blackout = next((e["t"] for e in events if e["kind"] == "blackout"), None)
    choices: dict[int, dict] = {}
    director, error = "rules", ""
    if gemini:
        try:
            choices = gemini_choices(str(script.get("title") or ""), shots, by_shot, client=client)
            director = "gemini" if choices else "rules"
        except Exception as exc:  # noqa: BLE001 - сеть/квота/ответ не по схеме — правила
            error = f"{type(exc).__name__}: {str(exc)[:160]}"
    planned = []
    for s in shots:
        dark = blackout is not None and s["t0"] + 0.3 >= blackout
        pick = choices.get(s["n"]) or rule_choice(s, by_shot.get(s["n"], []), dark)
        planned.append({"n": s["n"], "t0": round(float(s["t0"]), 3), "t1": round(float(s["t1"]), 3),
                        "text": s.get("spoken") or s["text"], "role": s.get("role") or "",
                        "sentences": s.get("sentences") or [s.get("i", 0)], **pick})
    return {"shots": fix_sequence(planned), "events": events, "words": words, "director": director,
            "director_error": error}


# --------------------------------------------------------------------------- #
# Лип-синк
# --------------------------------------------------------------------------- #

# Визем-группы: (открытие, округление, растяжка в ширину)
VISEMES = {
    "а": (1.0, 0.0, 0.35), "я": (0.95, 0.0, 0.35),
    "о": (0.75, 1.0, 0.0), "ё": (0.75, 1.0, 0.0),
    "у": (0.38, 1.0, 0.0), "ю": (0.4, 0.9, 0.0),
    "э": (0.62, 0.0, 0.75), "е": (0.55, 0.0, 0.75),
    "и": (0.32, 0.0, 1.0), "ы": (0.4, 0.0, 0.85),
    "м": (0.0, 0.0, 0.0), "б": (0.0, 0.0, 0.0), "п": (0.0, 0.0, 0.0),
    "ф": (0.1, 0.0, 0.3), "в": (0.1, 0.0, 0.3),
}
VOWELS = set("аяоёуюэеиы")
_DIGITS = {"0": "ноль", "1": "один", "2": "два", "3": "три", "4": "четыре", "5": "пять", "6": "шесть", "7": "семь",
           "8": "восемь", "9": "девять"}


def _letters(word: str) -> str:
    w = str(word).lower()
    w = "".join(_DIGITS.get(c, c) for c in w)
    return re.sub(r"[^а-яё]", "", w) or "а"


def viseme_keys(words: list[dict]) -> list[tuple[float, float, tuple[float, float, float]]]:
    """``[(t0, t1, (open, round, wide))]`` — буквы слова поровну по его длительности, гласные длиннее (×1.7);
    согласные — полуоткрыто с формой соседней гласной (коартикуляция), губные — закрыто."""
    keys = []
    for w in words:
        t0, t1 = float(w["start"]), float(w["end"])
        if t1 - t0 < 0.03:
            continue
        letters = _letters(w["word"])
        weights = [1.7 if c in VOWELS else 1.0 for c in letters]
        total = sum(weights)
        t = t0
        for i, c in enumerate(letters):
            d = (t1 - t0) * weights[i] / total
            if c in VISEMES:
                v = VISEMES[c]
            else:
                near = next((letters[j] for j in (i + 1, i - 1, i + 2) if 0 <= j < len(letters) and letters[j] in VOWELS), "")
                o, r, wd = VISEMES.get(near, (0.3, 0.0, 0.3))
                v = (0.22, r * 0.6, wd * 0.6)
            keys.append((t, t + d, v))
            t += d
    return keys


def lipsync(words: list[dict], n_frames: int, fps: int = FPS, envelope: list[float] | None = None,
            windows: list[tuple[float, float]] | None = None, gain: float = 1.0) -> list[tuple[float, float, float]]:
    """Покадрово ``(open, round, wide)`` 0..1: визем-цели по таймингам слов, между словами рот закрыт;
    атака быстрее отпускания; огибающая озвучки (если есть) — громче → шире. ``windows`` — где рот говорит
    (вне окон — закрыт, на краях — 0.12 с перехода)."""
    keys = viseme_keys(words)
    target = [(0.0, 0.0, 0.0)] * n_frames
    ki = 0
    for f in range(n_frames):
        t = f / fps
        while ki < len(keys) and keys[ki][1] <= t:
            ki += 1
        if ki < len(keys) and keys[ki][0] <= t < keys[ki][1]:
            target[f] = keys[ki][2]
    env = None
    if envelope:
        peak = sorted(envelope)[int(len(envelope) * 0.95)] or 1.0
        env = [min(1.2, max(0.0, e / peak)) for e in envelope]
    out = []
    o = r = w = 0.0
    dt = 1.0 / fps
    for f in range(n_frames):
        to, tr, tw = target[f]
        if env is not None and f < len(env):
            to *= 0.35 + 0.75 * env[f]
        to = min(1.0, to * gain)
        k_open = 1.0 - math.exp(-dt / (0.028 if to > o else 0.05))
        k_shape = 1.0 - math.exp(-dt / 0.06)
        o += (to - o) * k_open
        r += (tr - r) * k_shape
        w += (tw - w) * k_shape
        out.append((o, r, w))
    if windows is not None:
        def gate(t: float) -> float:
            best = 0.0
            for a, b in windows:
                if a - 0.12 <= t <= b + 0.12:
                    best = max(best, min(1.0, (t - (a - 0.12)) / 0.12, ((b + 0.12) - t) / 0.12))
            return best
        out = [(o * gate(f / fps), r, w) for f, (o, r, w) in enumerate(out)]
    return [(round(o, 4), round(r, 4), round(w, 4)) for o, r, w in out]


# --------------------------------------------------------------------------- #
# Исполнение: покадровые дорожки
# --------------------------------------------------------------------------- #

def _pulse(x: float, rise: float = 0.09, decay: float = 0.45) -> float:
    if x < 0:
        return 0.0
    if x < rise:
        return m.smoothstep(x / rise)
    return math.exp(-(x - rise) / decay)


def _ang(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


class _Track:
    """Ключи ``(t, значение)`` со сглаживанием, значение — число/кортеж/строка (строка — ступенькой)."""

    def __init__(self, default: Any):
        self.keys: list[tuple[float, Any]] = [(0.0, default)]

    def key(self, t: float, v: Any) -> None:
        self.keys.append((float(t), v))

    def curve(self, ease: str = "smooth") -> m.Curve:
        return m.Curve(sorted(self.keys, key=lambda k: k[0]), ease=ease)


def perform(job: dict, clip_len: dict | None = None, stride: float | None = None) -> dict:
    """Задание серии → покадровые дорожки исполнения (детерминированно; то же в Blender и в хэшах кусков)."""
    clip_len = {**CLIP_LEN, **(clip_len or {})}
    stride = float(stride or STRIDE)
    fps = int(job.get("fps") or FPS)
    dur = float(job["duration"])
    nf = int(round(dur * fps))
    times = [f / fps for f in range(nf)]
    shots = job["shots"]
    events = job.get("events") or []
    rnd = random.Random(int(job.get("seed") or 7))

    track = m.ClipTrack()
    walks: list[tuple[float, float, tuple, tuple]] = []      # (t0, t1, from, to) — корень едет по весу шага
    pos_keys: list[tuple[float, tuple[float, float]]] = []   # телепорт на склейке
    head_keys = _Track(0.0)
    gaze = _Track((0.0, FRONT, 1.0))
    reaches: list[dict] = []          # правая рука: нажатия/стук ({side, target(t), infl(t)} как Reach)
    holds: list[dict] = []            # руки в системе героя (телефон, двери)
    emo = _Track(EMOTIONS["calm"])
    lean = _Track(0.0)                # наклон груди: + вперёд, − назад
    roll = _Track(0.0)                # голова набок (слушает)
    startles: list[tuple[float, float]] = []
    phone_iv: list[list] = []         # [начало, конец|None] — телефон в левой руке
    phone_mode = _Track(0.0)          # 0 — экран, 1 — фонарик
    flash_track = _Track(0.0)         # фонарик включён
    contacts: list[dict] = []         # касания (для подгонки IK по мешу и звуков)
    door_keys = _Track(0.0)
    shout_bursts: list[tuple[float, float]] = []
    speak_windows: list[tuple[float, float]] = []
    seg_clip = None

    def add_clip(name: str, t: float, blend: float = 0.3, speed: float = 1.0, loop: bool = True, offset: float = 0.0):
        nonlocal seg_clip
        if track.segments and t < track.segments[-1].start + 0.05:
            t = track.segments[-1].start + 0.05
        track.add(m.ClipSegment(name, t, clip_len[name], blend=blend, speed=speed, loop=loop, offset=offset))
        seg_clip = name

    blackout = next((e["t"] for e in events if e["kind"] == "blackout"), None)
    light_back = next((e["t"] for e in events if e["kind"] == "light_back" and blackout is not None and e["t"] > blackout), None)

    def dark(t: float) -> bool:
        return blackout is not None and t >= blackout and (light_back is None or t < light_back)

    first_spot, first_head = spot_for(shots[0].get("target") if shots[0]["action"] == "approach" else "center")
    pos, heading, posture = first_spot, first_head, "stand"
    phone_out = False
    phone_hold: dict | None = None
    pos_keys.append((0.0, pos))
    head_keys.keys = [(0.0, heading)]
    add_clip("Idle_A", 0.0, blend=0.01)
    shot_out = []
    for k, s in enumerate(shots):
        t0, t1 = float(s["t0"]), float(s["t1"])
        a = s["action"]
        span = t1 - t0
        # куда и как стоять
        if a == "press_button":
            want = ["1", "3", "2", "4"] if s.get("button") == "all" else [reachable(s.get("button") or "1")]
            want = [reachable(b) for b in want]
            spot, hd = press_stand(want)
        elif a == "knock":
            spot, hd = spot_for("knock")
        elif a == "pry_doors":
            spot, hd = spot_for("pry")
        elif a == "sit_corner":
            spot, hd = spot_for("corner")
        elif a == "approach":
            spot, hd = spot_for(s.get("target") or "center")
        elif a in ("shout", "listen") and posture == "stand" and abs(_ang(heading)) > math.radians(35):
            spot, hd = pos, 0.0
        elif posture == "stand" and pos[1] < FRONT + 0.45 and a not in ("shout", "listen", "flinch"):
            # у самых дверей лицу некуда снимать — отходит в середину кабины
            spot, hd = spot_for("center")
        else:
            spot, hd = pos, heading
        moved = math.dist(spot, pos) > 0.04
        tt = t0
        # встать с пола (на камеру) или переставить на склейке
        if posture == "sit" and (s.get("stand_first") or a == "stand_up" or moved):
            if a == "stand_up" or (s.get("stand_first") and not moved):
                add_clip("Sit_Floor_StandUp", tt + 0.05, blend=0.2, loop=False)
                tt += clip_len["Sit_Floor_StandUp"] + 0.05
                add_clip("Idle_A", tt - 0.15, blend=0.25)
            else:
                add_clip("Idle_A", t0, blend=0.01)
            posture = "stand"
        walk_dist = math.dist(spot, pos)
        speed = stride * S
        need = m.walk_duration(walk_dist, speed * 1.35, clip_len["Walking_A"]) if moved else 0.0
        if moved and (k == 0 or walk_dist > 0.75 or need > 0.5 * span or a == "sit_corner" and walk_dist > 0.45):
            # склейка: герой уже на месте (переход между кадрами не показываем)
            pos_keys.append((t0, spot))
            head_keys.key(t0 - 1e-3, heading)
            head_keys.key(t0, hd)
            if posture == "stand" and seg_clip not in ("Idle_A",):
                add_clip("Idle_A", t0, blend=0.01)
            pos, heading = spot, hd
        elif moved:
            dirv = (spot[0] - pos[0], spot[1] - pos[1])
            walk_head = m.heading_of(dirv)
            turn = abs(_ang(walk_head - heading))
            t_turn = 0.18 + 0.25 * turn / math.pi
            head_keys.key(tt, heading)
            head_keys.key(tt + t_turn, heading + _ang(walk_head - heading))
            wdur = m.walk_duration(walk_dist, speed, clip_len["Walking_A"])
            cs = min(1.45, max(0.8, (walk_dist / max(wdur, 1e-6)) / speed))
            if wdur > 0.6 * span:
                wdur = max(clip_len["Walking_A"] / 2, walk_dist / (speed * 1.45))
                cs = (walk_dist / wdur) / speed
            add_clip("Walking_A", tt + 0.05, blend=0.25, speed=cs)
            walks.append((tt + 0.05, tt + 0.05 + wdur, pos, spot))
            arrive = tt + 0.05 + wdur
            pos_keys.append((arrive, spot))
            h_end = heading + _ang(walk_head - heading) + _ang(hd - walk_head)
            head_keys.key(arrive - 0.2, heading + _ang(walk_head - heading))
            head_keys.key(arrive + 0.35, h_end)
            add_clip("Idle_A", arrive, blend=0.3)
            heading, pos = h_end, spot
            tt = arrive + 0.15
        elif abs(_ang(hd - heading)) > math.radians(18):
            # поворот на месте — корень плавно, ноги переступают (клип шага медленно)
            dh = _ang(hd - heading)
            t_turn = 0.35 + 0.45 * abs(dh) / math.pi
            head_keys.key(tt + 0.05, heading)
            head_keys.key(tt + 0.05 + t_turn, heading + dh)
            add_clip("Walking_B", tt + 0.05, blend=0.15, speed=0.75)
            add_clip("Idle_A", tt + 0.05 + t_turn - 0.1, blend=0.25)
            heading += dh
            tt += t_turn
        a0 = min(tt + 0.05, t1 - 0.3)
        # эмоция кадра (наплыв 0.35 с)
        emo.key(t0 + 0.02, emo.keys[-1][1] if emo.keys else EMOTIONS["calm"])
        emo.key(t0 + 0.37, EMOTIONS.get(s.get("emotion") or "calm", EMOTIONS["calm"]))
        lean.key(t0, lean.keys[-1][1])
        roll.key(t0, roll.keys[-1][1])
        lean_v, roll_v = 0.0, 0.0
        shot_end = t1

        def gkey(t: float, v: Any) -> None:
            if t < shot_end - 0.05:
                gaze.key(max(t, t0), v)
        cam_subject = "face"
        # действие
        if a == "press_button":
            seq = ["1", "3", "2", "4"] if s.get("button") == "all" else [reachable(s.get("button") or "1")]
            seq = [reachable(b) for b in seq]
            per = 0.55 if len(seq) > 1 else 1.4
            if s.get("button") == "bell":
                seq = ["bell", "bell"]
                per = 0.9
            t = a0 + 0.1
            rest = None
            for b in seq:
                if t + per > t1 + 0.2:
                    break
                bp = button_pos(b)
                if len(seq) > 1:
                    r = {"button": bp, "normal": BTN_NORMAL, "t_start": t, "t_contact": t + per * 0.55,
                         "t_release": t + per * 0.72, "t_end": t + per * 1.02, "hover": 0.04, "depth": 0.006,
                         "id": b, "side": "r", "rest": rest}
                else:
                    r = {"button": bp, "normal": BTN_NORMAL, "t_start": t, "t_contact": t + 0.6, "t_release": t + 0.85,
                         "t_end": t + 1.4, "hover": 0.05, "depth": 0.006, "id": b, "side": "r", "rest": rest}
                reaches.append(r)
                contacts.append({"kind": "press", "t": r["t_contact"], "button": b, "point": bp, "normal": BTN_NORMAL})
                rest = m.v_add(bp, m.v_scale(BTN_NORMAL, 0.05))
                gkey(t - 0.15, bp)
                t += per * (0.75 if len(seq) > 1 else 1.0)
            gkey(t + 0.2, DISPLAY if s.get("button") != "bell" else (BELL[0], BELL[1], BELL[2] + 0.25))
            add_clip("Idle_A", a0, blend=0.3) if seg_clip != "Idle_A" else None
            cam_subject = "press"
        elif a == "knock":
            hits = [a0 + 0.35 + i * 0.38 for i in range(3)]
            hits = [h for h in hits if h < t1 - 0.15] or [a0 + 0.2]
            kp = KNOCK_POINT
            reaches.append({"button": kp, "normal": DOOR_NORMAL, "t_start": a0, "t_contact": hits[0],
                            "t_release": hits[-1] + 0.05, "t_end": hits[-1] + 0.6, "hover": 0.06, "depth": 0.004,
                            "id": "door", "side": "r", "hits": hits})
            for h in hits:
                contacts.append({"kind": "knock", "t": h, "point": kp, "normal": DOOR_NORMAL})
            gkey(a0 - 0.1, (kp[0], kp[1], 0.85))
            lean_v = 0.12
            cam_subject = "doors"
        elif a == "pry_doors":
            full = bool(re.search(r"раздвинул|открыл|разжал|распахнул", _low(s.get("text"))))
            t_grip = a0 + 0.45
            open_t = t_grip + 0.5
            holds.append({"side": "both", "t0": a0, "t1": t1 + 0.4, "grip": t_grip, "mode": "doors"})
            door_keys.key(open_t, door_keys.keys[-1][1])
            if full:
                door_keys.key(open_t + 1.1, 0.24)
            else:
                door_keys.key(open_t + 0.4, 0.025)
                door_keys.key(open_t + 0.8, 0.012)
                door_keys.key(min(t1, open_t + 1.5), 0.03)
                door_keys.key(min(t1 + 0.3, open_t + 2.0), 0.0)
            contacts.append({"kind": "doors", "t": open_t, "full": full})
            gkey(a0 - 0.1, (0.0, DOOR_FACE_Y, 0.75))
            # тужится — отворачивает лицо вбок (к камере детали, она с +X): лицо видно, а не капюшон
            gkey(t_grip + 0.35, (0.70, FRONT + 0.12, 0.85))
            if full:
                gkey(open_t + 1.3, (0.0, FRONT - 0.4, 0.45))     # в шахту: внизу край этажа
            lean_v = -0.16
            if phone_out:
                phone_iv[-1][1] = a0 + 0.3        # обе руки на дверях — телефон в карман
                if phone_hold is not None:
                    phone_hold["t1"] = a0 + 0.3
                phone_out = False
            cam_subject = "doors"
        elif a == "sit_corner":
            if posture != "sit":
                add_clip("Sit_Floor_Down", a0, blend=0.25, loop=False)
                add_clip("Sit_Floor_Idle", a0 + clip_len["Sit_Floor_Down"] - 0.1, blend=0.2)
            posture = "sit"
            gkey(a0 + 0.2, (0.0, FRONT, 0.55))
            cam_subject = "face"
        elif a == "stand_up":
            gkey(a0, DISPLAY)
        elif a in ("phone_out", "phone_light"):
            if not phone_out:
                phone_iv.append([a0, None])
                phone_hold = {"side": "l", "t0": a0, "t1": None, "mode": "phone", "from": "pocket"}
                holds.append(phone_hold)
                phone_out = True
            aim = a == "phone_light"
            phone_mode.key(a0 + 0.55, phone_mode.keys[-1][1])
            phone_mode.key(a0 + 0.8, 1.0 if aim else 0.0)
            if aim or dark(a0 + 0.5):
                flash_track.key(a0 + 0.85, flash_track.keys[-1][1])
                flash_track.key(a0 + 0.9, 1.0)
            gkey(a0 + 0.1, "phone")
            if aim:
                # светит туда, куда смотрит: на двери, потом ведёт лучом по кабине
                tgt = (0.0, DOOR_FACE_Y, 0.9) if rnd.random() < 0.5 else (DISPLAY[0], DISPLAY[1], DISPLAY[2] - 0.1)
                gkey(a0 + 0.9, tgt)
                gkey(a0 + 0.9 + max(0.6, (t1 - a0) * 0.45), (tgt[0] + 0.35, tgt[1] + 0.25, tgt[2] + 0.25))
            cam_subject = "phone"
        elif a == "look_display":
            gkey(a0, DISPLAY)
            cam_subject = "display"
        elif a == "look_ceiling":
            gkey(a0, HATCH)
            gkey(a0 + max(0.6, span * 0.55), (CEIL_LIGHT[0] - 0.15, CEIL_LIGHT[1] - 0.2, H))
            cam_subject = "face"
        elif a == "look_camera":
            gkey(a0, "camera")
        elif a == "flinch":
            ev = [e["t"] for e in events if t0 - 0.2 <= e["t"] < t1 and e["kind"] in ("blackout", "jolt", "knock", "scrape", "flash")]
            tf = ev[0] if ev else a0 + 0.15
            startles.append((tf + 0.05, 1.0))
            add_clip("Hit_A", max(tt, tf), blend=0.12, loop=False) if posture == "stand" else None
            add_clip("Idle_A", max(tt, tf) + clip_len["Hit_A"] - 0.05, blend=0.3) if posture == "stand" else None
            gkey(tf + 0.1, (CEIL_LIGHT[0], CEIL_LIGHT[1], H))
        elif a == "shout":
            gkey(a0, (0.0, DOOR_FACE_Y, 1.05))
            lean_v = 0.18
            if not s.get("speaks"):
                for i in range(2):
                    b0 = a0 + 0.2 + i * 1.0
                    if b0 + 0.4 < t1:
                        shout_bursts.append((b0, b0 + 0.45))
        elif a == "listen":
            gkey(a0, (0.05, DOOR_FACE_Y, 0.8))
            roll_v = 0.22
            if seg_clip == "Idle_A" and posture == "stand":
                add_clip("Idle_B", a0, blend=0.4, speed=0.6)
        elif a == "wait":
            if posture == "stand" and seg_clip != "Idle_B":
                add_clip("Idle_B", a0, blend=0.4, speed=0.7)
            gkey(a0, (0.1, FRONT + 0.3, 0.0) if rnd.random() < 0.5 else DISPLAY)
            gkey(a0 + span * 0.5, DISPLAY if dark(a0) else (0.3, FRONT, 0.6))
            lean_v = -0.05
        if phone_out and a not in ("phone_out", "phone_light"):
            # телефон уже в руке — светит туда, куда смотрит (экраном к себе — только когда смотрит в телефон)
            phone_mode.key(a0 + 0.1, phone_mode.keys[-1][1])
            phone_mode.key(a0 + 0.5, 1.0)
        if s.get("speaks"):
            speak_windows.append((t0, t1))
        lean.key(t0 + 0.4, lean_v)
        roll.key(t0 + 0.4, roll_v)
        shot_out.append({**s, "a0": round(a0, 3), "spot": [round(pos[0], 4), round(pos[1], 4)], "heading": round(heading, 4),
                         "posture_end": posture, "subject": cam_subject, "phone": phone_out})

    # события → испуг, взгляд на источник
    for e in events:
        if e["kind"] in ("blackout", "jolt", "knock", "scrape", "flash"):
            amp = {"blackout": 1.0, "jolt": 0.9, "knock": 0.55, "scrape": 0.5, "flash": 0.7}[e["kind"]]
            if not any(abs(t - e["t"]) < 0.5 for t, _ in startles):
                startles.append((e["t"] + 0.08, amp))
    startles.sort()

    # покадрово: корень
    tl = track
    pos_c = m.Curve(sorted(pos_keys, key=lambda k: k[0]), ease="step")
    head_c = head_keys.curve()
    root, heads = [], []
    walk_w = [dict((c, w) for c, _, w in tl.weights_at(t)).get("Walking_A", 0.0) for t in times]
    walk_at: dict[int, tuple[float, float]] = {}
    for (w0, w1, a_, b_) in walks:
        # корень едет со скоростью ∝ весу клипа шага: разгон/торможение — вместе с наплывом, стопа не скользит
        fr = [i for i in range(nf) if w0 - 0.4 <= times[i] <= w1 + 0.6]
        tot = sum(walk_w[i] for i in fr) or 1.0
        acc = 0.0
        for i in fr:
            acc += walk_w[i]
            walk_at[i] = m.lerp(a_, b_, min(1.0, acc / tot))
    for f, t in enumerate(times):
        p = walk_at.get(f) or pos_c.at(t)
        root.append((round(p[0], 5), round(p[1], 5)))
        heads.append(round(head_c.at(t), 5))

    # руки: правая (нажатия/стук), левая и обе (телефон, двери)
    hand = {"r": [None] * nf, "l": [None] * nf}
    for r in reaches:
        rc = m.Reach(button=tuple(r["button"]), normal=tuple(r["normal"]), t_start=r["t_start"], t_contact=r["t_contact"],
                     t_release=r["t_release"], t_end=r["t_end"], hover=r["hover"], depth=r["depth"],
                     rest=tuple(r["rest"]) if r.get("rest") else None)
        hits = r.get("hits")
        for f, t in enumerate(times):
            w = rc.influence(t)
            if w <= 0:
                continue
            p = rc.target_at(t)
            if hits:
                # стук: кулак отводится и бьёт на каждом ударе
                near = min(hits, key=lambda h: abs(h - t))
                x = (t - near) / 0.19
                back = 0.07 * (1.0 - math.exp(-(x * x) * 3.0)) if r["t_contact"] - 0.05 <= t <= r["t_release"] else 0.0
                p = m.v_add(p, m.v_scale(r["normal"], back))
            prev = hand["r"][f]
            if prev is None or prev[1] < w:
                hand["r"][f] = (tuple(round(c, 5) for c in p), round(w, 4), r["id"])
    pm_c = phone_mode.curve()
    door_c0 = door_keys.curve()
    for h in holds:
        for f, t in enumerate(times):
            if t < h["t0"] or (h["t1"] is not None and t > h["t1"]):
                continue
            rp, hd = root[f], heads[f]
            sit = 1.0 if _posture_at(shot_out, t) == "sit" else 0.0
            if h["mode"] == "phone":
                x = m.smootherstep((t - h["t0"]) / 0.65)
                look = (-0.035, 0.20, 0.50 - SIT_DROP * sit)
                light = (-0.07, 0.25, 0.57 - SIT_DROP * sit)
                pocket = (-0.13, 0.04, 0.32 - SIT_DROP * sit)
                mode = pm_c.at(t)
                held = m.lerp(look, light, mode)
                loc = m.lerp(pocket, held, x)
                w = m.smoothstep((t - h["t0"]) / 0.25)
                hand["l"][f] = (to_world(rp, hd, loc), round(w, 4), "phone")
            elif h["mode"] == "doors":
                op = door_c0.at(t)
                w = m.smoothstep((t - h["t0"]) / max(h["grip"] - h["t0"], 1e-3))
                if h["t1"] is not None:
                    w *= 1.0 - m.smoothstep((t - (h["t1"] - 0.4)) / 0.4)
                for side, sx in (("r", -1.0), ("l", 1.0)):
                    # створки разъезжаются — кисти на кромках (x: −1 — вправо от героя к −X мира)
                    p = (sx * (0.045 + op), DOOR_FACE_Y + 0.025, 0.62)
                    if w > 0 and (hand[side][f] is None or hand[side][f][1] < w):
                        hand[side][f] = (p, round(w, 4), "doors")
    # телефон виден в своих интервалах (в кармане — нет), левая рука держит его только тогда
    vis = [1.0 if any(a <= t < (b if b is not None else 1e9) for a, b in phone_iv) else 0.0 for t in times]
    for f in range(nf):
        if not vis[f] and hand["l"][f] and hand["l"][f][2] == "phone":
            hand["l"][f] = None

    # взгляд и мимика
    gaze_c = gaze.curve(ease="step")
    gz = []
    for t in times:
        g = gaze_c.at(t)
        for e in events:
            if e["kind"] in ("blackout", "scrape") and e["t"] + 0.15 <= t < e["t"] + 1.1:
                g = (CEIL_LIGHT[0], CEIL_LIGHT[1], H)
            elif e["kind"] == "knock" and e["t"] + 0.1 <= t < e["t"] + 1.2:
                g = (0.0, DOOR_FACE_Y, 0.85)
        gz.append(g if isinstance(g, str) else tuple(round(c, 4) for c in g))
    emo_c, lean_c, roll_c = emo.curve(), lean.curve(), roll.curve()
    blinks = m.blink_times(dur, seed=int(job.get("seed") or 7) + 4, min_gap=1.9, max_gap=4.0,
                           forced=[t for t, _ in startles])
    lip_mode = job.get("lipsync") or "speaking"
    windows = None if lip_mode == "all" else ([] if lip_mode == "off" else speak_windows)
    mouth = lipsync(job.get("words") or [], nf, fps, job.get("envelope"), windows)
    face, body = [], []
    for f, t in enumerate(times):
        b, wy, wd = emo_c.at(t)
        st = sum(amp * _pulse(t - ts) for ts, amp in startles if t >= ts - 0.01)
        st = min(1.0, st)
        b = max(b, st)
        wd = max(wd, st)
        o, r, w = mouth[f]
        # испуг — рот «о», крик — широко
        o = max(o, 0.7 * st)
        r = max(r, 0.8 * st if st > 0.3 else r)
        for a_, b_ in shout_bursts:
            if a_ <= t <= b_:
                x = (t - a_) / (b_ - a_)
                o = max(o, 0.95 * math.sin(math.pi * x))
                r = min(r, 0.3)
        face.append({"brow": round(b, 4), "worry": round(wy, 4), "wide": round(wd, 4),
                     "blink": round(m.blink_amount(t, blinks), 4), "mouth": (round(o, 4), round(r, 4), round(w, 4))})
        body.append({"startle": round(st, 4), "lean": round(lean_c.at(t), 4), "roll": round(roll_c.at(t), 4)})

    # свет, табло, двери, тряска
    lights = []
    floors = [e for e in events if e["kind"] == "floor"]
    start_floor = floors[0]["value"] if floors else "22"
    jolts = [e["t"] for e in events if e["kind"] in ("jolt", "scrape")]
    flick = [e["t"] for e in events if e["kind"] == "flicker"]
    flashes = [e["t"] for e in events if e["kind"] == "flash"]
    lit_at: dict[str, float] = {}
    for c in contacts:
        if c["kind"] == "press" and c["button"] != "bell":
            lit_at.setdefault(c["button"], c["t"])
    door_c = door_keys.curve()
    for t in times:
        main = 1.0
        if blackout is not None:
            if t >= blackout - 0.9 and (light_back is None or t < light_back):
                main = m.flicker(t, blackout - 0.9, blackout, seed=5)
            if light_back is not None and t >= light_back:
                main = m.smoothstep((t - light_back) / 0.15)
        for tf in flick:
            if tf <= t < tf + 0.9:
                main *= m.flicker(t, tf, tf + 10.0, seed=int(tf * 10)) * 0.8 + 0.2
        for tj in jolts:
            if tj <= t < tj + 0.35:
                main *= 0.35 + 0.65 * ((t - tj) / 0.35)
        red = 0.0
        if blackout is not None and t >= blackout + 0.25 and (light_back is None or t < light_back):
            x = t - blackout - 0.25
            red = m.smoothstep(x / 0.12) * (0.55 + 0.45 * m.smoothstep(x / 0.6)) * (0.9 + 0.1 * math.sin(2 * math.pi * x / 1.6))
            for tj in jolts:
                if tj <= t < tj + 0.3:
                    red *= 0.4 + 0.6 * ((t - tj) / 0.3)
        flash = max([math.exp(-(t - tf) / 0.12) for tf in flashes if t >= tf] or [0.0])
        disp_on = 1.0
        value = start_floor
        for e in floors[1:]:
            if t >= e["t"]:
                value = e["value"]
        if blackout is not None and t >= blackout + 0.04 and (light_back is None or t < light_back):
            disp_on = 0.0
            for e in floors[1:]:
                on_t = max(blackout + 1.0, e["t"] - 1.8)
                if e["t"] > blackout and t >= on_t:
                    disp_on = m.smoothstep((t - on_t) / 0.25)
        shake = 0.0
        for e in events:
            amp = {"blackout": 0.006, "jolt": 0.03, "scrape": 0.01, "knock": 0.004}.get(e["kind"], 0.0)
            ln = {"blackout": 0.6, "jolt": 0.9, "scrape": 1.2, "knock": 0.4}.get(e["kind"], 0.5)
            if amp and e["t"] <= t < e["t"] + ln:
                shake = max(shake, amp * (1.0 - (t - e["t"]) / ln))
        drop = 0.0
        for tj in [e["t"] for e in events if e["kind"] == "jolt"]:
            if t >= tj:
                drop += -0.05 * m.smoothstep((t - tj) / 0.12) * math.exp(-max(0.0, t - tj - 0.12) / 0.35)
        lit = sorted(b for b, tb in lit_at.items() if t >= tb)
        lights.append({"main": round(main, 4), "red": round(red, 4), "flash": round(flash, 4), "display": round(disp_on, 4),
                       "floor": value, "doors": round(door_c.at(t), 4), "shake": round(shake, 5), "drop": round(drop, 5),
                       "lit": lit, "dark": dark(t)})

    phone = []
    pm = phone_mode.curve()
    fl = flash_track.curve(ease="linear")
    beam_raw = []
    for f, t in enumerate(times):
        # луч — туда, куда смотрит герой (в объектив/на телефон — вперёд в пол: не слепить камеру)
        g = gz[f]
        if isinstance(g, tuple):
            beam_raw.append(g)
        else:
            fx, fy = facing(heads[f])
            beam_raw.append((root[f][0] + fx * 0.9, root[f][1] + fy * 0.9, 0.05))
    perf = {"fps": fps, "frames": nf, "duration": dur, "track": [
        {"clip": sg.clip, "start": round(sg.start, 4), "duration": sg.duration, "blend": sg.blend, "speed": round(sg.speed, 4),
         "loop": sg.loop, "offset": sg.offset} for sg in track.segments],
        "root": root, "heading": heads, "hands": hand, "gaze": gz, "face": face, "body": body, "lights": lights,
        "phone": phone, "contacts": contacts, "shots": shot_out, "startles": startles,
        "blackout": blackout, "light_back": light_back, "speak_windows": speak_windows}
    perf["cameras"] = camera_plan(perf, int(job.get("seed") or 7))
    # луч фонарика не в объектив: камера в конусе — увести луч вбок-вниз (иначе кадр — белая вспышка)
    beam_raw = _beam_off_camera(perf, beam_raw)
    beam_s = m.spring_follow(beam_raw, 1.0 / fps, 0.18)
    for f, t in enumerate(times):
        phone.append({"on": vis[f], "aim": round(pm.at(t) if vis[f] else 0.0, 4),
                      "light": round(fl.at(t) if vis[f] else 0.0, 4), "beam": tuple(round(c, 4) for c in beam_s[f])})
    perf["phone"] = phone
    return perf


def _beam_off_camera(perf: dict, beam: list[tuple]) -> list[tuple]:
    fps = perf["fps"]
    starts = [(int(round(c["t0"] * fps)), c) for c in perf["cameras"]]
    out = []
    for f, b in enumerate(beam):
        cam = next((c for f0, c in reversed(starts) if f >= f0), starts[0][1])
        posture = _posture_at(perf["shots"], f / fps)
        ph = to_world(perf["root"][f], perf["heading"][f], (-0.07, 0.25, 0.57 - (SIT_DROP if posture == "sit" else 0.0)))
        to_b = m.v_norm(m.v_sub(b, ph))
        to_c = m.v_norm(m.v_sub(cam["pos_a"], ph))
        if m.v_dot(to_b, to_c) > math.cos(math.radians(40)):
            # повернуть луч от камеры на ~70° по горизонтали и опустить к полу
            side = 1.0 if (to_b[0] * to_c[1] - to_b[1] * to_c[0]) >= 0 else -1.0
            d = _rot((to_c[0], to_c[1]), math.radians(70 * side))
            b = (ph[0] + d[0] * 0.9, ph[1] + d[1] * 0.9, 0.1)
        out.append(b)
    return out


# --------------------------------------------------------------------------- #
# Камера: план кадра → положение и цель (сцена Blender доводит цель по настоящей голове героя)
# --------------------------------------------------------------------------- #

CAM_BOUNDS = (LEFT + 0.06, -LEFT - 0.06, FRONT + 0.06, D / 2 - 0.06, 0.12, H - 0.1)


def cam_inside(p: tuple[float, float, float]) -> bool:
    x0, x1, y0, y1, z0, z1 = CAM_BOUNDS
    return x0 <= p[0] <= x1 and y0 <= p[1] <= y1 and z0 <= p[2] <= z1


def _rot(v: tuple[float, float], ang: float) -> tuple[float, float]:
    c, s_ = math.cos(ang), math.sin(ang)
    return (v[0] * c - v[1] * s_, v[0] * s_ + v[1] * c)


def face_point(root: tuple[float, float], heading: float, posture: str) -> tuple[float, float, float]:
    """Середина лица (оценка без Blender): над корнем, чуть впереди, по позе."""
    fx, fy = facing(heading)
    fwd = 0.07 if posture == "stand" else 0.02
    return (root[0] + fx * fwd, root[1] + fy * fwd, FACE_Z["sit" if posture == "sit" else "stand"])


def shot_camera(kind: str, shot: dict, root: tuple[float, float], heading: float, posture: str,
                aim_point: tuple[float, float, float] | None, rnd: random.Random,
                avoid: list[tuple] | None = None) -> dict | None:
    """Камера кадра: ``{kind, pos_a, pos_b, point, face_w, offset, lens, fstop, focus}``. ``point`` — во что целиться
    помимо лица, ``face_w`` — доля лица в цели (сцена ведёт её за настоящей головой). Ракурс не встаёт (камера
    в стене) — ``None``; ``avoid`` — положения прошлых кадров того же плана (повтор того же ракурса не берём)."""
    avoid = avoid or []

    def fresh(p) -> bool:
        return cam_inside(p) and all(math.dist(p, q) > 0.22 for q in avoid)
    fx, fy = facing(heading)
    fwd = (fx, fy)
    face = face_point(root, heading, posture)
    subject = shot.get("subject") or "face"
    side = 1.0 if rnd.random() < 0.5 else -1.0

    def out(kind_, pos, point, face_w, lens, fstop=2.4, focus="face", push=0.93, offset=(0.0, 0.0, 0.0)):
        pos = tuple(round(c, 4) for c in pos)
        if focus == "face":
            pivot = face
        else:
            pivot = point
        pos_b = tuple(round(pivot[i] + (pos[i] - pivot[i]) * push, 4) for i in range(3))
        return {"kind": kind_, "pos_a": pos, "pos_b": pos_b, "point": tuple(round(c, 4) for c in point),
                "face_w": face_w, "lens": lens, "fstop": fstop, "focus": focus, "offset": offset}

    def around(dist: float, angles: tuple[float, ...], dz: float, center=face) -> tuple | None:
        for a in angles:
            d = _rot(fwd, math.radians(a * side))
            for k in (1.0, 0.85, 0.72):
                p = (center[0] + d[0] * dist * k, center[1] + d[1] * dist * k, center[2] + dz)
                if fresh(p):
                    return p
        return None

    if kind == "detail" and subject == "press":
        pos = (LEFT + 0.17, FRONT + 0.12, 0.84)
        pt = tuple((button_pos("1")[i] * 0.55 + face[i] * 0.45) for i in range(3))
        return out("detail", pos, pt, 0.35, 19.0, fstop=3.2, focus="point", push=0.95)
    if kind == "detail" and subject == "phone":
        hand_l = to_world(root, heading, (-0.04, 0.22, 0.52 - (SIT_DROP if posture == "sit" else 0.0)))
        center = tuple((hand_l[i] + face[i]) / 2 for i in range(3))
        p = around(0.72, (-38, 38, -55, 55, 0), -0.08, center)
        if p:
            return out("detail", p, hand_l, 0.5, 26.0, fstop=2.4, focus="point")
    if kind == "detail" and subject == "display":
        p = (face[0] - fx * 0.70 + fy * 0.22 * side, face[1] - fy * 0.70 - fx * 0.22 * side, face[2] + 0.08)
        if cam_inside(p):
            return out("detail", p, DISPLAY, 0.0, 62.0, fstop=4.0, focus="point", push=0.95)
        return out("detail", (0.30, FRONT + 0.45, 1.20), DISPLAY, 0.0, 30.0, fstop=4.0, focus="point")
    if kind == "detail" and subject == "doors":
        # сбоку у дверей, почти анфас: руки на створках и лицо (герой стоит к дверям вплотную)
        # сбоку сверху у дверей: руки на створках, щель и лицо в профиль (вплотную к дверям анфаса нет)
        sx = 1.0   # с +X: туда герой отворачивает лицо, когда тужится; у стука правая рука — к −X
        pos = (sx * 0.58, FRONT + 0.13, 0.98)
        pt = (0.0, DOOR_FACE_Y + 0.10, 0.62)
        return out("detail", pos, pt, 0.75, 17.0, fstop=3.2, focus="face", push=0.96)
    if kind == "detail":
        return None
    if kind == "ots":
        tgt = aim_point or (root[0] + fx * 1.2, root[1] + fy * 1.2, 0.85)
        for sh, back in ((0.40 * side, 0.85), (-0.40 * side, 0.85), (0.34 * side, 0.66), (-0.34 * side, 0.6)):
            # вправо от взгляда: (fy, −fx); sh>0 — над правым плечом; выше головы — капюшон не на полкадра
            p = (face[0] - fx * back + fy * sh, face[1] - fy * back - fx * sh, face[2] + 0.36)
            if fresh(p):
                return out("ots", p, tgt, 0.12, 20.0, fstop=3.2, focus="point", push=0.94)
        return None
    if kind == "overhead":
        x0, x1, y0, y1, _z0, z1 = CAM_BOUNDS
        best, best_s = None, -1e9
        for cx in (x0 + 0.02, x1 - 0.02):
            for cy in (y0 + 0.02, y1 - 0.02):
                p = (cx, cy, min(1.86, face[2] + 0.95))
                if not fresh(p):
                    continue
                dx, dy = p[0] - face[0], p[1] - face[1]
                dist = math.hypot(dx, dy)
                score = (dx * fx + dy * fy) / max(dist, 1e-6) + dist * 0.6
                if score > best_s:
                    best, best_s = p, score
        if best is None:
            return None
        # из угла, но не дальше ~1.2 м по полу: в темноте мелкий герой не читается
        dx, dy = best[0] - face[0], best[1] - face[1]
        k = min(1.0, 0.95 / max(math.hypot(dx, dy), 1e-6))
        best = (face[0] + dx * k, face[1] + dy * k, best[2])
        pt = (face[0], face[1], face[2] - 0.22)
        return out("overhead", best, pt, 0.5, 17.0, fstop=4.0, focus="face", push=0.94)
    # крупно лицо: спереди под небольшим углом, чуть ниже глаз
    p = around(0.95, (22, -22, 40, -40, 0, 60, -60), -0.03)
    if p is None:
        return None
    return out("face", p, face, 1.0, 30.0, fstop=2.2, focus="face", push=0.9, offset=(0.0, 0.0, -0.06))


def camera_plan(perf: dict, seed: int = 7) -> list[dict]:
    """Камеры всех кадров (без повтора одинакового ракурса подряд — другой бок)."""
    rnd = random.Random(seed + 11)
    fps = perf["fps"]
    out = []
    for s in perf["shots"]:
        tm = min(s["t1"] - 0.05, max(s.get("a0", s["t0"]) + 0.3, (s["t0"] + s["t1"]) / 2))
        f = min(perf["frames"] - 1, int(tm * fps))
        posture = s.get("posture_end", "stand")
        g = perf["gaze"][f]
        aim = g if isinstance(g, (list, tuple)) else None
        prev = out[-1]["kind"] if out else ""
        cam = None
        for kind in dict.fromkeys((s["camera"], *CAMERA_PREFS.get(s["action"], ()), "face", "overhead", "ots")):
            if kind == prev:
                continue
            avoid = [c["pos_a"] for c in out if c["kind"] == kind][-2:]
            cam = shot_camera(kind, s, tuple(perf["root"][f]), perf["heading"][f], posture, aim, rnd, avoid) or                 shot_camera(kind, s, tuple(perf["root"][f]), perf["heading"][f], posture, aim, rnd)
            if cam is not None:
                break
        if cam is None:   # совсем тесно — общий сверху без проверки повтора
            cam = shot_camera("overhead", s, tuple(perf["root"][f]), perf["heading"][f], posture, aim, rnd)
        s["camera"] = cam["kind"]
        out.append({"n": s["n"], "t0": s["t0"], "t1": s["t1"], **cam})
    return out


def _posture_at(shots: list[dict], t: float) -> str:
    for s in shots:
        if s["t0"] <= t < s["t1"]:
            return s.get("posture_end", "stand") if s.get("action") != "sit_corner" or t >= s.get("a0", s["t0"]) + 0.6 else "stand"
    return shots[-1].get("posture_end", "stand") if shots else "stand"


def track_of(perf: dict) -> m.ClipTrack:
    tr = m.ClipTrack()
    for sg in perf["track"]:
        tr.add(m.ClipSegment(sg["clip"], sg["start"], sg["duration"], blend=sg["blend"], speed=sg["speed"],
                             loop=sg["loop"], offset=sg["offset"]))
    return tr


# --------------------------------------------------------------------------- #
# Звуки событий и действий (настоящие записи паков)
# --------------------------------------------------------------------------- #

SFX = {
    "press": ("sfx:kenney-interface/click_002.ogg", 0.55),
    "bell": ("sfx:kenney-interface/click_004.ogg", 0.5),
    "knock_hit": ("sfx:kenney-impact/impactMetal_medium_002.ogg", 0.55),
    "doors_strain": ("sfx:tension/metal-scrape.ogg", 0.55),
    "doors_open": ("sfx:tension/clank.ogg", 0.8),
}


def sfx_for(job: dict, perf: dict) -> list[dict]:
    """Звуки холста: события режиссуры (как ``series3d``) + щелчки нажатий, удары стука, скрежет створок,
    фон-петли гула кабины/шахты."""
    from app import series3d as S3

    total = float(job["duration"])
    out: list[dict] = []

    def add(ref_vol: tuple[str, float], t: float, label: str, kind: str) -> None:
        if t < total - 0.2:
            out.append({"t": round(max(0.0, t), 3), "ref": ref_vol[0], "volume": ref_vol[1], "label": label, "kind": kind})

    for e in job.get("events") or []:
        k, t = e["kind"], float(e["t"])
        if k == "blackout":
            add(S3.SFX["blackout"], t - 0.05, "блэкаут", "blackout")
            add(S3.SFX["power_down"], t, "гул глохнет", "power_down")
        elif k == "jolt":
            n = len([x for x in out if x["kind"] in ("jolt", "jolt2")])
            add(S3.SFX["jolt" if n % 2 == 0 else "jolt2"], t, "рывок", "jolt")
        elif k == "scrape":
            add(S3.SFX["scrape"], t, "скрежет", "scrape")
        elif k == "flash":
            add(S3.SFX["flash"], t, "вспышка", "flash")
        elif k == "knock":
            add(S3.SFX["knock"], t, "стук", "knock")
        elif k == "sting":
            add(S3.SFX["sting"], t, "акцент", "sting")
    for c in perf["contacts"]:
        if c["kind"] == "press":
            add(SFX["bell" if c.get("button") == "bell" else "press"], c["t"] - 0.01, "кнопка", "press")
        elif c["kind"] == "knock":
            add(SFX["knock_hit"], c["t"] - 0.01, "удар в дверь", "knock_hit")
        elif c["kind"] == "doors":
            add(SFX["doors_strain"], c["t"], "створки", "doors")
            if c.get("full"):
                add(SFX["doors_open"], c["t"] + 1.0, "двери разъехались", "doors_open")
    story = next((s for s in job["shots"] if s.get("role") not in ("hook", "")), None)
    if story:
        add(S3.SFX["tension"], story["t0"], "напряжение", "tension")
    b, back = perf.get("blackout"), perf.get("light_back")

    def bed(kind: str, t0: float, t1: float, label: str, fi: float, fo: float) -> None:
        if t1 - t0 >= 0.5:
            ref, under = S3.AMBIENCE[kind]
            out.append({"t": round(max(0.0, t0), 3), "ref": ref, "volume": 1.0, "label": label, "kind": kind,
                        "end": round(min(total, t1), 3), "role": "ambience", "under_db": under, "fade_in": fi, "fade_out": fo})

    if b is None:
        bed("hum", 0.0, total, "гул кабины", 0.4, 1.5)
    else:
        bed("hum", 0.0, b + 0.12, "гул кабины", 0.4, 0.18)
        bed("dark", b + 0.6, back or total, "гул шахты", 2.5, 1.0 if back else 2.0)
        if back:
            bed("hum", back, total, "гул кабины", 0.3, 1.5)
    return sorted(out, key=lambda x: x["t"])


# --------------------------------------------------------------------------- #
# Задание рендера, куски и кэш
# --------------------------------------------------------------------------- #

QUALITY = {"final": {"samples": 48, "scale": 100}, "preview": {"samples": 16, "scale": 50},
           "draft": {"samples": 4, "scale": 33}}


def build_job(script: dict, words: list[dict], total: float, *, gemini: bool = True, client: Any = None,
              envelope: list[float] | None = None, lipsync_mode: str = "speaking", quality: str = "final",
              look: dict | None = None, seed: int = 7) -> dict:
    """Всё, что нужно сцене Blender (JSON): кадры с режиссурой, события, слова, огибающая голоса, качество."""
    plan = plan_shots(script, words, total, gemini=gemini, client=client)
    return {"version": SCENE_VERSION, "fps": FPS, "duration": round(float(total), 3), "title": script.get("title") or "",
            "shots": plan["shots"], "events": plan["events"], "words": plan["words"], "director": plan["director"],
            "director_error": plan["director_error"], "envelope": [round(x, 4) for x in (envelope or [])],
            "lipsync": lipsync_mode if lipsync_mode in LIPSYNC_MODES else "speaking",
            "quality": quality if quality in QUALITY else "final", "look": look or {}, "seed": int(seed)}


def chunks(perf: dict) -> list[dict]:
    """Куски рендера = планы: ``[{n, f0, f1}]`` (кадры с 1, включительно)."""
    fps, nf = perf["fps"], perf["frames"]
    out = []
    for s in perf["shots"]:
        f0 = int(round(s["t0"] * fps)) + 1
        f1 = min(nf, int(round(s["t1"] * fps)))
        if f1 >= f0:
            out.append({"n": s["n"], "f0": f0, "f1": f1})
    return out


def _code_hash() -> str:
    root = Path(__file__).resolve().parent.parent
    h = hashlib.sha1()
    for p in (root / "tools" / "blender" / "series_scene.py", root / "tools" / "blender" / "hero_shot.py",
              root / "app" / "motion3d.py"):
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()[:12]


def chunk_keys(job: dict, perf: dict) -> list[dict]:
    """Хэш куска — от покадровых дорожек его кадров (+ соседей на наплыв), плана камеры, качества и кода сцены."""
    code = _code_hash()
    out = []
    shots = {s["n"]: s for s in perf["shots"]}
    for c in chunks(perf):
        a, b = max(0, c["f0"] - 1 - 3), min(perf["frames"], c["f1"] + 3)
        s = shots[c["n"]]
        c_n = c["n"]
        blob = {"v": SCENE_VERSION, "code": code, "q": job.get("quality"), "f": [c["f0"], c["f1"]],
                "shot": {k: s.get(k) for k in ("action", "camera", "subject", "spot", "heading", "target", "button", "emotion",
                                              "posture_end", "phone", "t0", "t1", "a0")},
                "root": perf["root"][a:b], "heading": perf["heading"][a:b], "gaze": perf["gaze"][a:b],
                "face": perf["face"][a:b], "body": perf["body"][a:b], "lights": perf["lights"][a:b],
                "phone": perf["phone"][a:b], "hands": [perf["hands"]["r"][a:b], perf["hands"]["l"][a:b]],
                "track": [sg for sg in perf["track"] if sg["start"] < b / perf["fps"] + 1 and
                          sg["start"] + 60 > a / perf["fps"] - 2],
                "camera": next((c for c in perf["cameras"] if c["n"] == c_n), None),
                "look": job.get("look") or {}}
        key = hashlib.sha1(json.dumps(blob, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]
        out.append({**c, "key": key})
    return out


def frame_ok(path: Path) -> bool:
    """PNG дописан до конца (IEND) — оборванный рендером кадр рисуем заново."""
    try:
        size = path.stat().st_size
        if size < 64:
            return False
        with path.open("rb") as fh:
            fh.seek(-12, os.SEEK_END)
            return fh.read(12)[4:8] == b"IEND"
    except OSError:
        return False


def frame_name(f: int) -> str:
    """Имя кадра в папке куска — по номеру кадра серии (так пишет рендер анимации Blender: ``f_#####``)."""
    return f"f_{int(f):05d}.png"


def missing_frames(cache: Path, keys: list[dict]) -> dict[str, list[int]]:
    """Каких кадров нет в кэше по кускам: ``{key: [номер кадра серии, …]}``."""
    out = {}
    for c in keys:
        d = cache / c["key"]
        need = [f for f in range(c["f0"], c["f1"] + 1) if not frame_ok(d / frame_name(f))]
        if need:
            out[c["key"]] = need
    return out


def audio_envelope(path: Path | str, fps: int = FPS) -> list[float]:
    """Огибающая озвучки (RMS на кадр) — для лип-синка; без ffmpeg/файла — пусто."""
    try:
        proc = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                              capture_output=True, check=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return []
    import array

    pcm = array.array("h")
    pcm.frombytes(proc.stdout[: len(proc.stdout) // 2 * 2])
    step = 8000 // fps
    out = []
    for i in range(0, len(pcm), step):
        chunk = pcm[i:i + step]
        if not chunk:
            break
        out.append(math.sqrt(sum(x * x for x in chunk) / len(chunk)) / 32768.0)
    return out


DEFAULT_BLENDER = ["D:/vvf-offload/tools/blender/blender-4.5.15-windows-x64/blender.exe", "/usr/bin/blender"]


def find_blender(explicit: str | None = None) -> str | None:
    for c in [explicit or "", os.environ.get("BLENDER_BIN", ""), *DEFAULT_BLENDER]:
        if c and Path(c).exists():
            return c
    return shutil.which("blender")


def packs_root() -> Path:
    from app.settings import settings

    return Path(settings.motion_packs_dir)


def scene_config(job: dict, work: Path, cache: Path, packs: Path | None = None) -> dict:
    packs = packs or packs_root()

    def need(rel: str) -> str:
        p = packs / rel
        if not p.is_file():
            raise FileNotFoundError(f"нет {rel} — python tools/fetch_packs.py --only {rel.split('/')[0]}")
        return str(p)

    return {"job": str(work / "job.json"), "cache": str(cache), "out_dir": str(work),
            "character": need("kaykit-adventurers/Rogue_Hooded.glb"),
            "animations": [need(f"kaykit-animations/{n}.glb") for n in ("Rig_Medium_General", "Rig_Medium_MovementBasic",
                                                                          "Rig_Medium_Simulation")],
            "samples": QUALITY[job.get("quality") or "final"]["samples"],
            "quality": job.get("quality") or "final",
            "font_display": ["C:/Windows/Fonts/bahnschrift.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf"],
            "font_ui": ["C:/Windows/Fonts/bahnschrift.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"],
            "font_symbols": ["C:/Windows/Fonts/seguisym.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]}


class RenderError(RuntimeError):
    pass


def render_local(job: dict, out_mp4: Path | str, *, work: Path | str, cache: Path | str | None = None,
                 blender: str | None = None, progress: Callable[[float, str], Any] | None = None,
                 only: list[int] | None = None, stills: list[int] | None = None, timeout: float = 4 * 3600,
                 should_stop: Callable[[], bool] | None = None) -> dict:
    """Рендер серии Blender'ом здесь: сцена один раз → недостающие кадры кусков в кэш (возобновляемо) → склейка
    в mp4 без звука. ``progress(доля 0..1, заметка)``; ``only`` — номера планов; ``stills`` — только эти кадры
    (номера с 1, PNG в ``work``) для проверки; ``should_stop()`` — True: остановить Blender (отмена задания)."""
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    cache = Path(cache or work / "cache")
    cache.mkdir(parents=True, exist_ok=True)
    exe = find_blender(blender)
    if not exe:
        raise RenderError("нет Blender: задай BLENDER_BIN (portable 4.5 LTS) — рендер 3D-серии идёт на компьютере с GPU")
    perf = perform(job)
    keys = chunk_keys(job, perf)
    if only is not None:
        keys = [c for c in keys if c["n"] in set(only)]
    todo = missing_frames(cache, keys)
    total_missing = sum(len(v) for v in todo.values())
    (work / "job.json").write_text(json.dumps(job, ensure_ascii=False), "utf-8")
    cfg = scene_config(job, work, cache)
    cfg["chunks"] = keys
    cfg["todo"] = todo
    if stills:
        cfg["stills"] = [int(x) for x in stills]
    (work / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=1), "utf-8")
    scene = Path(__file__).resolve().parent.parent / "tools" / "blender" / "series_scene.py"
    t0 = time.time()
    rendered = 0
    log_path = work / "blender.log"
    if total_missing or stills:
        if progress:
            progress(0.0, f"сцена Blender: {total_missing} кадров к рендеру из {perf['frames']}")
        with log_path.open("w", encoding="utf-8", errors="replace") as log:
            proc = subprocess.Popen([exe, "-b", "--factory-startup", "-P", str(scene), "--", "--config",
                                     str(work / "config.json")], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding="utf-8", errors="replace")
            assert proc.stdout is not None
            for line in proc.stdout:
                log.write(line)
                if line.startswith("PROGRESS "):
                    log.flush()
                    parts = line.split()
                    try:
                        rendered = int(parts[1])
                    except (IndexError, ValueError):
                        continue
                    if progress:
                        progress(min(0.99, rendered / max(1, total_missing)), f"кадр {rendered}/{total_missing}")
                if should_stop is not None and line.startswith('PROGRESS ') and should_stop():
                    proc.kill()
                    raise RenderError('рендер остановлен (задание отменено) — готовые кадры в кэше')
                if time.time() - t0 > timeout:
                    proc.kill()
                    raise RenderError("рендер дольше лимита — остановлен (кадры в кэше, повторный запуск продолжит)")
            code = proc.wait()
        if code != 0:
            tail = log_path.read_text("utf-8", errors="replace")[-3000:]
            raise RenderError(f"Blender упал (код {code}); кадры в кэше — повторный запуск продолжит. {tail[-600:]}")
    report_path = work / "report.json"
    report = json.loads(report_path.read_text("utf-8")) if report_path.is_file() else {}
    if stills:
        return {"ok": True, "stills": [str(work / f"still_{int(f):04d}.png") for f in stills], "report": report,
                "seconds": round(time.time() - t0, 1)}
    left = missing_frames(cache, keys)
    if left:
        raise RenderError(f"не дорисованы куски: {', '.join(list(left)[:5])} — повторный запуск продолжит")
    out = Path(out_mp4)
    out.parent.mkdir(parents=True, exist_ok=True)
    seq = work / "seq"
    if seq.exists():
        shutil.rmtree(seq)
    seq.mkdir()
    n = 0
    for c in keys:
        for f in range(c["f0"], c["f1"] + 1):
            n += 1
            src = cache / c["key"] / frame_name(f)
            dst = seq / f"f_{n:05d}.png"
            try:
                os.link(src, dst)
            except OSError:
                shutil.copyfile(src, dst)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-framerate", str(perf["fps"]), "-i", str(seq / "f_%05d.png"),
                    "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,noise=alls=3:allf=t,format=yuv420p", "-c:v", "libx264", "-preset", "slow", "-crf", "17",
                    "-profile:v", "high", "-movflags", "+faststart", str(out)], check=True)
    shutil.rmtree(seq, ignore_errors=True)
    if progress:
        progress(1.0, "склеено")
    return {"ok": True, "out": str(out), "frames": n, "rendered": total_missing, "cached": n - total_missing,
            "seconds": round(time.time() - t0, 1), "report": report, "chunks": len(keys)}


# --------------------------------------------------------------------------- #
# Для сборки холста (app/stories_3d.py)
# --------------------------------------------------------------------------- #

def result_for(job: dict, perf: dict | None = None) -> dict:
    """Режиссура в виде ``series3d.direct``: кадры (для обложек), события, звуки, локация."""
    perf = perf or perform(job)
    cam_map = {"overhead": "overhead", "face": "face", "ots": "ots", "detail": "detail"}
    shots = [{"n": s["n"], "t0": s["t0"], "t1": s["t1"], "text": s["text"], "role": s.get("role") or "",
              "action": s["action"], "camera": cam_map[s["camera"]], "spot": s.get("target") or "",
              "emotion": s.get("emotion"), "speaks": bool(s.get("speaks")), "by": s.get("by")} for s in job["shots"]]
    return {"shots": shots, "events": job.get("events") or [], "sfx": sfx_for(job, perf), "location": "elevator",
            "checks": [], "notes": [], "look": job.get("look") or {}, "director": job.get("director")}
