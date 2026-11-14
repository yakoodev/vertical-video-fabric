"""Стиль текста «мульт-жёлтый»: жёлтая заливка, толстая чёрная обводка, жирный мультяшный шрифт (Dela Gothic One, OFL —
самый «мультяшный» из жирных кириллических в vendor/fonts; рукописного жирного с кириллицей там нет), прыжок на входе и лёгкий
наклон — слова мем-эдита («Джампнул», «Видели?», «Рокет» в рефе владельца). Тот же стиль — в библиотеке текста
редактора (frontend/src/pages/workspace/motion/LibraryPanels.tsx, «Мульт-жёлтый»).

Опорные px формата разметки слоёв: 9:16 — 1080×1920, 16:9 — 1920×1080 (кегль одинаков: короткая сторона 1080).
"""

from __future__ import annotations

CARTOON_YELLOW = {
    "id": "cartoon-yellow",
    "label": "Мульт-жёлтый",
    "props": {"font": "Dela Gothic One", "weight": 400, "size": 130, "color": "#FFD60A",
              "stroke": {"color": "#000000", "width": 14}, "align": "center", "maxWidth": 900, "lineHeight": 1.0},
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
SIZE = {"9:16": 130, "16:9": 180}  # 16:9 — в 1.4 раза крупнее относительно кадра (опорный кегль по высоте 1080)
MAX_WIDTH = {"9:16": 900, "16:9": 1500}


def word_layer(text: str, start: float, end: float, aspect: str = "9:16", index: int = 0) -> dict:
    """Текстовый слой «мульт-жёлтого» слова на [start, end] (шкала clip)."""
    style = CARTOON_YELLOW
    places = PLACES.get(aspect, PLACES["9:16"])
    x, y = places[index % len(places)]
    sign = -1 if index % 2 else 1
    return {
        "type": "text", "name": f"Слово «{text}»", "start": round(start, 3), "end": round(end, 3),
        "x": x, "y": y, "track": 3, "rotation": sign * style["rotation"],
        "props": {**style["props"], "text": text, "size": SIZE.get(aspect, 130), "maxWidth": MAX_WIDTH.get(aspect, 900)},
        "shadow": dict(style["shadow"]), "in": dict(style["in"]), "out": dict(style["out"]), "loop": dict(style["loop"]),
    }
