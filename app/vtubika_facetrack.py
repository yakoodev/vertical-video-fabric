"""Мимика аватара «как ловит камера»: покадровое лицо и голова VRM для примера «вебка против айфона» (мини-гайд 5).

Два режима одной и той же модели (perfect-sync, 52 формы ARKit — Лёша, hinzka, ``app/vtubika_vrm_stage.py``):

* ``stiff`` — обычная вебка: рот еле приоткрывается и дёргается ступеньками (значения обновляются ~5 раз в секунду,
  как у трекинга по картинке), брови и щёки стоят, голова почти не двигается, моргает редко.
* ``lively`` — айфон с разблокировкой по лицу: рот по гласным во всю ширину, голова живая, брови, щёки, прищур и язык
  по событиям ``events`` (секунды от начала ряда кадров — их ставят на слова озвучки «брови, щёки, прищур, язык»).

Кадр — ``{"expr": {имя: вес}, "head": [x, y, z] градусы, "look": [dx, dy]}``; имена — выражения VRM модели:
гласные ``aa/ih/ou/ee/oh``, ``blink``, ARKit-группы (``browInnerUp``, ``cheekPuff``, ``tongueOut``…: у VRM0 они
с большой буквы, rig.apply находит оба). Кадры рисует ``node/vrm/stills.mjs`` (``tools/vtubika_guide_assets.py faces``).
"""

from __future__ import annotations

import math
from typing import Any

MODES = ("stiff", "lively")
# Что умеет показать живое лицо по событию: формы ARKit и их веса на пике.
FEATURES: dict[str, dict[str, float]] = {
    "brows": {"browInnerUp": 1.0, "browOuterUpLeft": 0.95, "browOuterUpRight": 0.95, "eyeWideLeft": 0.7, "eyeWideRight": 0.7},
    "cheeks": {"cheekPuff": 1.0, "mouthClose": 0.35},
    "squint": {"eyeSquintLeft": 0.95, "eyeSquintRight": 0.95, "cheekSquintLeft": 0.8, "cheekSquintRight": 0.8,
               "mouthSmileLeft": 0.55, "mouthSmileRight": 0.55},
    # Язык — с подмигиванием («тэхэпэро»): один язык на маленьком окне читается хуже.
    "tongue": {"tongueOut": 1.0, "jawOpen": 0.22, "eyeBlinkLeft": 1.0, "mouthSmileLeft": 0.35, "mouthSmileRight": 0.35},
    "smile": {"mouthSmileLeft": 0.8, "mouthSmileRight": 0.8, "cheekSquintLeft": 0.45, "cheekSquintRight": 0.45},
    "frown": {"browDownLeft": 0.85, "browDownRight": 0.85, "mouthFrownLeft": 0.4, "mouthFrownRight": 0.4},
}
# События, при которых рот не говорит (щёки надуты, язык наружу — гласные спорят с ними).
MOUTH_BUSY = {"cheeks", "tongue"}
VOWELS = ("aa", "oh", "ee", "ih", "ou")
# Пределы режима «вебка»: рот, голова (градусы) — проверка tests/test_vtubika_facetrack.py.
STIFF_MOUTH_MAX = 0.16
STIFF_HEAD_MAX = 1.2


def _env(t: float, t0: float, attack: float = 0.14, hold: float = 0.55, release: float = 0.28) -> float:
    """Огибающая события: нарастание, удержание, спад (0…1, гладко)."""
    x = t - t0
    if x < 0 or x > attack + hold + release:
        return 0.0
    if x < attack:
        u = x / attack
    elif x < attack + hold:
        u = 1.0
    else:
        u = 1.0 - (x - attack - hold) / release
    return 0.5 - 0.5 * math.cos(math.pi * max(0.0, min(1.0, u)))


def _syllable(t: float, rate: float, seed: int) -> tuple[str, float]:
    """Слог речи на t: гласная и открытие рта 0…1 (слоги ~rate в секунду, паузы между фразами)."""
    k = int(t * rate)
    ph = t * rate - k
    vowel = VOWELS[(k * 7 + seed * 3) % len(VOWELS)]
    # Каждый 6-й слог — пауза (вдох), открытие — синусом внутри слога, сила слога — своя.
    if (k + seed) % 6 == 5:
        return vowel, 0.0
    amp = 0.62 + 0.38 * math.sin(k * 2.399 + seed)  # 0.24…1
    return vowel, max(0.0, math.sin(math.pi * ph)) * amp


def _blinks(t: float, times: list[float], dur: float = 0.13) -> float:
    for b in times:
        x = t - b
        if 0 <= x <= dur:
            return math.sin(math.pi * x / dur)
    return 0.0


def frame(mode: str, t: float, events: list[dict] | None = None, seed: int = 1, blink_every: float | None = None) -> dict[str, Any]:
    """Лицо и голова на секунду t ряда кадров."""
    if mode not in MODES:
        raise ValueError(f"режим мимики {mode!r}: нужен один из {MODES}")
    expr: dict[str, float] = {}
    if mode == "stiff":
        # Трекинг по картинке: значения ступеньками (5 Гц), рот почти закрыт, лицо — одно выражение.
        q = math.floor(t * 5) / 5
        _v, open_ = _syllable(q, 4.2, seed)
        expr["aa"] = round(min(STIFF_MOUTH_MAX, 0.04 + open_ * 0.12), 3)
        every = blink_every or 4.6
        expr["blink"] = round(_blinks(t, [every * (i + 0.55) for i in range(int(t / every) + 2)], 0.2), 3)
        head = [round(0.5 * math.sin(2 * math.pi * q / 3.9), 2), round(0.9 * math.sin(2 * math.pi * q / 5.3 + 0.7), 2), 0.0]
        return {"expr": {k: v for k, v in expr.items() if v > 0.001}, "head": head, "look": [0.0, 0.0]}
    # Айфон: каждое событие — свои формы; рот говорит, пока не занят языком или щеками.
    busy = 0.0
    feats: dict[str, float] = {}
    for ev in events or []:
        e = _env(t, float(ev["t"]), hold=float(ev.get("hold", 0.55)))
        if e <= 0:
            continue
        what = ev["what"]
        if what not in FEATURES:
            raise ValueError(f"нет такой мимики {what!r}: {sorted(FEATURES)}")
        if what in MOUTH_BUSY:
            busy = max(busy, e)
        for k, w in FEATURES[what].items():
            feats[k] = max(feats.get(k, 0.0), w * e)
    vowel, open_ = _syllable(t, 4.6, seed)
    talk = open_ * (1.0 - busy)
    expr[vowel] = round(0.18 + 0.72 * talk if talk > 0.02 else 0.0, 3)
    # Лёгкая улыбка — фоном, поверх событий.
    expr["mouthSmileLeft"] = expr["mouthSmileRight"] = 0.22
    for k, w in feats.items():
        expr[k] = round(max(expr.get(k, 0.0), w), 3)
    every = blink_every or 2.7
    squint = feats.get("eyeSquintLeft", 0.0)
    expr["blink"] = round(_blinks(t, [every * (i + 0.35) for i in range(int(t / every) + 2)]) * (1 - squint), 3)
    head = [round(3.2 * math.sin(2 * math.pi * t / 2.3 + 1.0) - 2.5 * feats.get("browInnerUp", 0.0), 2),
            round(7.0 * math.sin(2 * math.pi * t / 3.1) + 11.0 * feats.get("cheekPuff", 0.0), 2),
            round(4.5 * math.sin(2 * math.pi * t / 3.7 + 0.5) + 3.0 * feats.get("tongueOut", 0.0), 2)]
    look = [round(0.06 * math.sin(2 * math.pi * t / 3.1 + 0.4), 3), round(0.03 * math.sin(2 * math.pi * t / 2.3), 3)]
    return {"expr": {k: v for k, v in expr.items() if v > 0.001}, "head": head, "look": look}


def track(mode: str, dur: float, fps: float = 15.0, events: list[dict] | None = None, seed: int = 1) -> list[dict[str, Any]]:
    """Ряд кадров длиной dur секунд: ``[{t, expr, head, look}, …]``."""
    n = max(1, round(dur * fps))
    return [{"t": round(i / fps, 4), **frame(mode, i / fps, events, seed)} for i in range(n)]


def word_events(times: list[list[dict]], n: int, words: dict[str, str], t0: float, lead: float = 0.05) -> list[dict]:
    """События мимики на словах фразы n: ``{"брови,": "brows", …}`` → ``[{t, what}]`` от начала ряда кадров t0."""
    from app import vtubika_plan as P

    return [{"t": round(P.anchor(times[n], w, n) - t0 - lead, 3), "what": what} for w, what in words.items()]
