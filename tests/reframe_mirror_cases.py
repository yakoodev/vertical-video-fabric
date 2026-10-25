"""Случаи сверки перекомпоновки app/aspect.reframe_between с её зеркалом в превью
(frontend/src/motion/reframe.ts).

Фикстура tests/fixtures/reframe_cases.json — входы (слои, камера, размеры файлов, пара
форматов) и ответ Python. pytest (tests/test_reframe_mirror.py) проверяет, что Python
отвечает так же, node (``npm run test:motion``) — что TypeScript даёт те же числа.
Обновить после намеренной правки app/aspect.py:
``VVF_UPDATE_GOLDEN=1 pytest tests/test_reframe_mirror.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.aspect import reframe_between
from app.motion import normalize_camera, normalize_layers

FIXTURES = Path(__file__).parent / "fixtures"
CASES_PATH = FIXTURES / "reframe_cases.json"
PAIRS = (("9:16", "1:1"), ("9:16", "4:5"), ("9:16", "16:9"), ("16:9", "9:16"), ("16:9", "1:1"), ("1:1", "16:9"))


def _promo() -> dict:
    """Промо колонкой: фон и лента во всю ширину, титр, телефон followCamera, касание-вспышка, CTA."""
    layers = normalize_layers([
        {"id": "bg", "type": "shape", "start": 0, "end": 6, "x": 0.5, "y": 0.5,
         "props": {"kind": "rect", "w": 1080, "h": 1920, "fill": "#101020"}},
        {"id": "band", "type": "shape", "start": 0, "end": 6, "x": 0.5, "y": 0.14,
         "props": {"kind": "rect", "w": 1080, "h": 220, "fill": "#000000"}},
        {"id": "title", "type": "text", "start": 0, "end": 6, "x": 0.5, "y": 0.14,
         "props": {"text": "Задачи команды —\nв одном месте", "size": 80, "maxWidth": 960}},
        {"id": "phone", "type": "image", "start": 0, "end": 6, "x": 0.5, "y": 0.52, "followCamera": True,
         "props": {"asset_id": 7, "w": 560, "device": "phone"}},
        {"id": "tap", "type": "shape", "start": 2, "end": 2.6, "x": 0.6, "y": 0.45,
         "props": {"kind": "ellipse", "w": 96, "h": 96}, "keys": {"opacity": [{"t": 0, "v": 1}, {"t": 0.6, "v": 0}]}},
        {"id": "cta", "type": "text", "start": 0, "end": 6, "x": 0.5, "y": 0.89,
         "props": {"text": "Попробовать →", "size": 60, "bg": {"color": "#ff6a2b", "padX": 50, "padY": 24}}},
        {"id": "count", "type": "counter", "start": 4, "end": 6, "x": 0.3, "y": 0.7,
         "props": {"to": 12500, "suffix": " ₽", "size": 70}, "keys": {"y": [{"t": 0, "v": 0.75}, {"t": 0.5, "v": 0.7}]}},
        {"id": "sfx", "type": "audio", "start": 1, "end": 2, "props": {"asset_id": 3}},
    ])
    camera = normalize_camera([{"t": 0, "zoom": 1}, {"t": 2, "zoom": 1.8, "x": 0.5, "y": 0.5},
                               {"t": 3, "zoom": 1.8, "x": 0.5, "y": 0.5}, {"t": 4, "zoom": 1}])
    return {"name": "promo", "layers": layers, "camera": camera, "sizes": {"7": [1170, 2532]}}


def _scenes() -> dict:
    """Две сцены с разной рамкой и концовка с логотипом, ключи x/scale, эмодзи, плашка."""
    layers = normalize_layers([
        {"id": "t1", "type": "text", "start": 0, "end": 3, "x": 0.5, "y": 0.2,
         "props": {"text": "ПЕРВАЯ СЦЕНА", "size": 110, "uppercase": True, "letterSpacing": 4}},
        {"id": "e1", "type": "emoji", "start": 0.2, "end": 3, "x": 0.2, "y": 0.35, "scale": 1.2,
         "props": {"char": "🔥", "size": 180}, "keys": {"scale": [{"t": 0, "v": 0}, {"t": 0.3, "v": 1.1}]}},
        {"id": "c1", "type": "callout", "start": 3.2, "end": 6, "x": 0.5, "y": 0.6,
         "props": {"title": "Вторая", "subtitle": "сцена ниже", "w": 820}},
        {"id": "m1", "type": "shape", "start": 3.2, "end": 6, "x": 0.3, "y": 0.75,
         "props": {"kind": "rect", "w": 300, "h": 120}, "keys": {"x": [{"t": 0, "v": -0.2}, {"t": 0.4, "v": 0.3}]}},
        {"id": "logo", "type": "image", "start": 6.5, "end": 9, "x": 0.5, "y": 0.45,
         "props": {"asset_id": 12, "w": 640, "border": {"width": 6, "color": "#ffffff"}}},
        {"id": "end", "type": "text", "start": 6.5, "end": 9, "x": 0.5, "y": 0.62,
         "props": {"text": "t.me/app", "size": 64, "boxWidth": 700}},
        {"id": "hid", "type": "text", "start": 0, "end": 9, "x": 0.5, "y": 0.95, "hidden": True,
         "props": {"text": "скрытый", "size": 50}},
    ])
    return {"name": "scenes", "layers": layers, "camera": [], "sizes": {"12": [2048, 1356]}}


def _in_place() -> dict:
    """Презентация «титр слева, медиа справа» с наездом на медиа «на месте» (anchor in_place)."""
    layers = normalize_layers([
        {"id": "title", "type": "text", "start": 0, "end": 6, "x": 0.28, "y": 0.4,
         "props": {"text": "Задачи команды", "size": 64, "maxWidth": 700}},
        {"id": "media", "type": "image", "start": 0, "end": 6, "x": 0.72, "y": 0.5, "followCamera": True,
         "props": {"asset_id": 9, "w": 300}},
    ])
    camera = normalize_camera([{"t": 0, "zoom": 1, "anchor": "in_place", "x": 0.72, "y": 0.5},
                               {"t": 1, "zoom": 1.4, "anchor": "in_place", "x": 0.72, "y": 0.5},
                               {"t": 3, "zoom": 1.4, "anchor": "in_place", "x": 0.72, "y": 0.5},
                               {"t": 4, "zoom": 1}])
    return {"name": "in_place", "layers": layers, "camera": camera, "sizes": {"9": [1600, 1000]}}


def _scaffold() -> dict:
    """Настоящий клип промо-заготовки (клип 581: 40 слоёв, 18 ключей камеры)."""
    data = json.loads((FIXTURES / "clip581_scaffold.json").read_text(encoding="utf-8"))
    clip = data["clip"]
    sizes = {k: [v["width"], v["height"]] for k, v in data["assets"].items()}
    return {"name": "clip581", "layers": normalize_layers(clip["layers"]), "camera": normalize_camera(clip["camera"]),
            "sizes": sizes}


def build_cases() -> dict:
    """Входы (``bases``) и ответ Python по всем парам форматов (``cases``)."""
    bases, cases = {}, []
    for base in (_promo(), _scenes(), _scaffold(), _in_place()):
        name = base.pop("name")
        bases[name] = base
        sizes = {k: tuple(v) for k, v in base["sizes"].items()}
        for src, dst in PAIRS:
            layers, camera = reframe_between(base["layers"], base["camera"], src, dst, sizes)
            cases.append({"name": f"{name} {src}→{dst}", "base": name, "src": src, "dst": dst,
                          "expect": {"layers": layers, "camera": camera}})
    return {"bases": bases, "cases": cases}


def load_cases() -> dict:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))


def write_cases(data: dict) -> None:
    CASES_PATH.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
