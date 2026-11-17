"""Графика мини-гайдов «Витубики» (app/vtubika_guide.py): пункты ``gfx`` фраз → слои моушна на словах озвучки."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import vtubika_guide as G


def _png(path: Path) -> str:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (40, 60), (255, 0, 0, 255)).save(path)
    return str(path)


def _plan(tmp: Path) -> dict:
    idle, talk = _png(tmp / "idle.png"), _png(tmp / "talk.png")
    return {
        "guide": {"badge": {"text": "МИНИ-ГАЙД №1", "from": 0.5}},
        "sentences": [
            {"text": "Молчишь одна говоришь другая.",
             "gfx": [{"kind": "scrim"}, {"kind": "pip"},
                     {"kind": "pngtuber", "idle": idle, "talk": talk, "voice_from": "говоришь"},
                     {"kind": "chip", "at": "говоришь", "text": "говорю"}]},
            {"text": "Камера не нужна. Запуск за час.",
             "gfx": [{"kind": "rows", "items": [{"icon": "mic", "text": "Камера не нужна", "at": "Камера"},
                                                 {"icon": "timer", "text": "За час", "at": "Запуск"}]},
                     {"kind": "image", "src": idle, "until": "end"}]},
        ],
    }


def _words(plan: dict) -> list[dict]:
    out, t = [], 0.0
    for s in plan["sentences"]:
        for w in s["text"].split():
            out.append({"word": w, "start": round(t, 3), "end": round(t + 0.4, 3)})
            t += 0.5
        t += 0.6
    return out


def test_layers_on_words(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    words = _words(plan)
    layers, assets = G.layers(plan, words, total=words[-1]["end"] + 1.0)
    by = {la["id"]: la for la in layers}
    assert by["gbadge"]["start"] == 0.5
    # Фраза 1: подложка — до начала фразы 2 (по умолчанию «до следующей фразы»).
    assert by["g0_0"]["type"] == "fx" and by["g0_0"]["end"] == pytest.approx(words[4]["start"])
    # Кружок ведущей — кадр самой сцены (ключ переходов) и рамка.
    assert by["g0_1"]["type"] == "video" and str(by["g0_1"]["props"]["asset_id"]) == G.PIP_ASSET
    # PNGtuber: до «говоришь» молчит, потом говорит на словах.
    png = [la for la in layers if la["id"].startswith("g0_2")]
    talk_key = next(k for k, v in assets.items() if v.endswith("talk.png"))
    talks = [la for la in png if str(la["props"]["asset_id"]) == talk_key]
    assert talks and min(la["start"] for la in talks) >= words[2]["start"] - 1e-6
    # Строки карточки — на своих словах, время от начала слоя.
    rows = by["g1_0"]["props"]["items"]
    t0 = by["g1_0"]["start"]
    assert rows[0]["at"] == pytest.approx(max(0.0, words[4]["start"] - 0.1 - t0), abs=1e-3)
    assert rows[1]["at"] == pytest.approx(words[7]["start"] - 0.1 - t0, abs=1e-3)
    # Картинки — числовыми id (компоновщик), путь в assets.json.
    assert all(k.isdigit() for k in assets)
    assert len(layers) < 200


def test_seq_is_one_apng_layer(tmp_path: Path) -> None:
    for i in range(1, 5):
        _png(tmp_path / "seq" / f"f_{i:03d}.png")
    plan = {"sentences": [{"text": "Поворот модели.", "gfx": [
        {"kind": "seq", "pattern": str(tmp_path / "seq" / "f_{:03d}.png"), "count": 4, "fps": 8}]}]}
    words = [{"word": "Поворот", "start": 0.0, "end": 0.5}, {"word": "модели.", "start": 0.6, "end": 1.2}]
    layers, assets = G.layers(plan, words, total=3.0)
    assert len(layers) == 1 and layers[0]["type"] == "image"
    assert next(iter(assets.values())).endswith(".apng")


def test_window_outside_short_timeline_is_skipped(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    words = _words(plan)
    # Кадр-превью (vtubika_stills): ролик длиной в кадр — пункты вне него пропускаются, а не падают.
    layers, _ = G.layers(plan, [{**w, "start": w["start"] - 5, "end": w["end"] - 5} for w in words], total=0.04)
    assert all(la["start"] < 0.04 for la in layers)


def test_plan_guide_model_types_is_valid() -> None:
    plan = json.loads((Path(__file__).resolve().parent.parent / "tools" / "vtubika_plans" / "guide_model_types.json").read_text("utf-8"))
    kinds = {g["kind"] for s in plan["sentences"] for g in s.get("gfx") or []}
    assert kinds <= {"scrim", "panel", "pip", "title", "rows", "ui", "chip", "image", "seq", "pngtuber", "text", "layer"}
    assert plan["vrm"]["heroine"] == "olya_host"
    # Владелец 17.11: английские названия в озвучке «уёбищные» — в речи только русские слова, названия — на экране.
    import re

    from app import vtubika_plan as P

    assert not re.search(r"[A-Za-z]", P.narration_text(plan)), P.narration_text(plan)
