"""Стиль текста «мульт-жёлтый»: жёлтая заливка, толстая чёрная обводка, мультяшный шрифт, прыжок на входе и лёгкий
наклон — слова мем-эдита («Джампнул», «Видели?», «Рокет» в рефе владельца). Тот же стиль — в библиотеке текста
редактора (frontend/src/pages/workspace/motion/LibraryPanels.tsx, «Мульт-жёлтый»).

Опорные px формата разметки слоёв: 9:16 — 1080×1920, 16:9 — 1920×1080 (кегль одинаков: короткая сторона 1080).
"""

from __future__ import annotations

CARTOON_YELLOW = {
    "id": "cartoon-yellow",
    "label": "Мульт-жёлтый",
    "props": {"font": "Pangolin", "weight": 400, "size": 150, "color": "#FFD60A",
              "stroke": {"color": "#000000", "width": 16}, "align": "center", "maxWidth": 900, "lineHeight": 1.0},
    "shadow": {"color": "#000000", "blur": 0, "x": 6, "y": 9, "opacity": 0.9},
    "in": {"type": "pop", "duration": 0.28},
    "out": {"type": "fade", "duration": 0.12},
    "loop": {"type": "wiggle", "speed": 1.2, "amount": 0.25},
    "rotation": 6.0,  # ± по очереди: слово то вправо, то влево
}

# Где слово на кадре: под прицелом, в свободной части геймплея (не на субтитрах и не на HUD внизу).
PLACES = {
    "9:16": ((0.32, 0.62), (0.68, 0.58), (0.5, 0.66), (0.4, 0.55)),
    "16:9": ((0.3, 0.74), (0.68, 0.7), (0.42, 0.66), (0.6, 0.76)),
}
SIZE = {"9:16": 150, "16:9": 130}


def word_layer(text: str, start: float, end: float, aspect: str = "9:16", index: int = 0) -> dict:
    """Текстовый слой «мульт-жёлтого» слова на [start, end] (шкала clip)."""
    style = CARTOON_YELLOW
    places = PLACES.get(aspect, PLACES["9:16"])
    x, y = places[index % len(places)]
    sign = -1 if index % 2 else 1
    return {
        "type": "text", "name": f"Слово «{text}»", "start": round(start, 3), "end": round(end, 3),
        "x": x, "y": y, "track": 3, "rotation": sign * style["rotation"],
        "props": {**style["props"], "text": text, "size": SIZE.get(aspect, 150)},
        "shadow": dict(style["shadow"]), "in": dict(style["in"]), "out": dict(style["out"]), "loop": dict(style["loop"]),
    }
