"""Декорация мем-канала «3D-анимация с мемами»: уютный стол, монитор с чатом, робот-помощник «Клод» (deskbot),
toon-пресет ``ink`` движка node/scene3d. Только раскладка и вещи — сюжет и тайминги собирает tools/meme3d_pilot.py.

Координаты в метрах, столешница — y = TOP. Камера 9:16, поэтому монитор стоит за роботом чуть левее и выше:
в общем плане оба в кадре друг над другом, на крупных — экран монитора или лицо робота.
"""

from __future__ import annotations

TOP = 0.75  # высота столешницы

PALETTE = {
    "ink": "#2b1810",
    "wall": "#86a9bd",
    "wall_low": "#6b8ea3",
    "desk": "#c98c58",
    "desk_edge": "#8f5634",
    "bot": "#d9774b",
    "bot_trim": "#a9512f",
    "plate": "#f6e6cf",
    "teal": "#3f7f84",
    "mustard": "#e2a83b",
    "leaf": "#6e9f6a",
    "cream": "#f4ead8",
}

BOT_POS = [0.22, TOP, 0.06]
MONITOR_POS = [-0.22, TOP, -0.3]
MONITOR_YAW = 16
SCREEN = {"width": 0.6, "height": 0.375, "standH": 0.13}


def screen_center() -> list[float]:
    """Центр экрана монитора в мире (для крупных планов)."""
    import math

    y = TOP + SCREEN["standH"] + (SCREEN["height"] + 0.05) / 2
    a = math.radians(MONITOR_YAW)
    dz = 0.022
    return [MONITOR_POS[0] + math.sin(a) * dz, y, MONITOR_POS[2] + math.cos(a) * dz]


def environment() -> dict:
    return {
        "background": "#86a9bd",
        "fog": False,
        "exposure": 0.9,
        "envIntensity": 0.0,
        "toneMapping": "neutral",
        "toon": {"preset": "ink", "outline": {"width": 6.0, "ref": 1.9}},
        "post": {"ao": 0, "bloom": False, "vignette": 0.22, "grain": 0.0,
                 "ca": 0.0, "grade": {"saturation": 1.04, "contrast": 1.03, "shadows": "#7a4a6a", "highlights": "#ffe2b8",
                                      "split": 0.25}},
    }


def lights() -> list[dict]:
    return [
        {"type": "ambient", "color": "#c79aa8", "intensity": 0.75},
        {"type": "hemisphere", "color": "#ffe6c8", "ground": "#8a5a72", "intensity": 0.25},
        {"type": "directional", "id": "key", "color": "#fff0d8", "intensity": 2.3, "position": [1.6, 2.8, 2.2],
         "target": [0, TOP, 0], "castShadow": True, "shadow": {"extent": 1.4, "size": 2048, "bias": -0.0006,
                                                                "normalBias": 0.015}},
        {"type": "point", "id": "lamp", "color": "#ffc98a", "intensity": 1.2, "distance": 2.0, "decay": 2,
         "position": [0.36, TOP + 0.28, -0.32]},
    ]


def lib(obj: dict, model: str, fit_max: list[float], tags: list[str] | None = None) -> dict:
    """Вещь из библиотеки моделей (docs/3d/models.md): модель категории ``model`` (вариант — от засева сцены), вписанная
    в ``fit_max``; нет библиотеки — тот же объект из своего примитива (``primitive``/``params``) или ``fallback``."""
    out = {**obj, "model": model, "fitMax": fit_max}
    if tags:
        out["modelTags"] = tags
    return out


BOOKS = [
    {"id": "book1", "primitive": "rounded_box", "params": {"size": [0.04, 0.2, 0.13], "color": PALETTE["teal"], "radius": 0.006},
     "position": [-0.68, TOP + 0.43, -0.52]},
    {"id": "book2", "primitive": "rounded_box", "params": {"size": [0.035, 0.17, 0.13], "color": PALETTE["mustard"], "radius": 0.006},
     "position": [-0.635, TOP + 0.43, -0.52]},
    {"id": "book3", "primitive": "rounded_box", "params": {"size": [0.045, 0.22, 0.13], "color": PALETTE["bot_trim"], "radius": 0.006},
     "position": [-0.59, TOP + 0.43, -0.52], "rotation": [0, 0, -8]},
]


def objects(browser: dict, monitor_state: list[dict], bubbles: list[dict]) -> list[dict]:
    P = PALETTE
    objs: list[dict] = [
        # Стена и плинтус-полоса: тёплый тон, нижняя половина темнее.
        {"id": "wall", "primitive": "box", "params": {"size": [4, 2.6, 0.05], "color": P["wall"]}, "position": [0, 0, -0.62]},
        {"id": "wall_low", "primitive": "box", "params": {"size": [4, 0.62, 0.06], "color": P["wall_low"]},
         "position": [0, 0.3, -0.6]},
        {"id": "window", "primitive": "window_frame", "params": {"width": 0.62, "height": 0.52},
         "position": [0.55, TOP + 0.38, -0.585]},
        {"id": "poster", "primitive": "poster", "params": {"width": 0.22, "height": 0.3, "colors": ["#f6e6cf", "#e2a83b",
                                                                                                   "#3f7f84", "#d9774b"],
                                                           "frame": "#8f5634", "seed": 4},
         "position": [-0.55, TOP + 0.52, -0.6]},
        {"id": "shelf", "primitive": "rounded_box", "params": {"size": [0.5, 0.03, 0.16], "color": P["desk"], "radius": 0.01},
         "position": [-0.52, TOP + 0.4, -0.52]},
        # Книги на полке: ряд книг из библиотеки (в длину полки), без неё — три корешка.
        {"id": "books", "model": "books", "modelTags": ["shelf"], "fitMax": [0.3, 0.22, 0.14], "position": [-0.6, TOP + 0.43, -0.52],
         "fallback": BOOKS},
        # Стол.
        {"id": "desk", "primitive": "rounded_box", "params": {"size": [1.9, 0.06, 0.95], "color": P["desk"], "radius": 0.02},
         "position": [0, TOP - 0.06, -0.12]},
        {"id": "desk_front", "primitive": "box", "params": {"size": [1.9, 0.7, 0.05], "color": P["desk_edge"]},
         "position": [0, 0, 0.33]},
        {"id": "monitor", "primitive": "monitor", "params": {**SCREEN, "color": P["cream"], "stand": "#c8b59b", "browser": browser,
                                                             "px": 1280, "notes": ["#ffe27a", "#ffb4a2"]},
         "position": MONITOR_POS, "rotation": [0, MONITOR_YAW, 0], "state": monitor_state},
        # Клавиатура, кружка, растение, лампа — модели библиотеки; без неё — свои примитивы (deskprops.ts).
        lib({"id": "keyboard", "primitive": "keyboard", "params": {"width": 0.4, "depth": 0.13}, "position": [-0.07, TOP, 0.08],
             "rotation": [0, 12, 0]}, "keyboard", [0.4, 0.05, 0.16]),
        lib({"id": "mug", "primitive": "mug", "params": {"color": P["teal"], "radius": 0.038, "height": 0.085},
             "position": [0.48, TOP, 0.08], "rotation": [0, -30, 0]}, "mug", [0.12, 0.1, 0.12]),
        lib({"id": "plant", "primitive": "plant", "params": {"pot": P["bot_trim"], "leaf": P["leaf"], "radius": 0.055, "height": 0.08,
                                                             "leaves": 8, "leafLen": 0.1},
             "position": [0.58, TOP, -0.3]}, "plant", [0.26, 0.3, 0.26], ["desk"]),
        lib({"id": "lamp", "primitive": "desk_lamp", "params": {"color": P["mustard"]}, "position": [0.4, TOP, -0.42],
             "rotation": [0, 200, 0]}, "lamp", [0, 0.45, 0]),
        {"id": "notes", "primitive": "rounded_box", "params": {"size": [0.09, 0.012, 0.09], "color": "#ffe27a", "radius": 0.003},
         "position": [-0.45, TOP, 0.08], "rotation": [0, 18, 0]},
    ]
    objs += bubbles
    return objs


def deskbot(pose: list[dict], face: list[dict], mouth: dict | None, blinks: list[float]) -> dict:
    P = PALETTE
    return {"id": "claude", "style": "deskbot", "height": 0.34, "position": BOT_POS, "yaw": -8,
            "deskbot": {"body": P["bot"], "trim": P["bot_trim"], "plate": P["plate"], "screen": "#2a1f2d",
                        "glow": "#fff1dc", "accent": "#ffc857"},
            "pose": pose, "face": face, "mouth": mouth or {}, "blinks": blinks, "stepFps": 15, "seed": 5}
