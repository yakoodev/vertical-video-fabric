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


def test_seq_count_auto_counts_frames_in_a_row(tmp_path: Path) -> None:
    for i in range(1, 7):
        _png(tmp_path / "seq" / f"f_{i:03d}.png")
    _png(tmp_path / "seq" / "f_009.png")  # после дыры — не считается
    plan = {"sentences": [{"text": "Мимика.", "gfx": [
        {"kind": "seq", "pattern": str(tmp_path / "seq" / "f_{:03d}.png"), "count": "auto", "fps": 15}]}]}
    words = [{"word": "Мимика.", "start": 0.0, "end": 0.5}]
    layers, assets = G.layers(plan, words, total=3.0)
    apng = next(iter(assets.values()))
    from PIL import Image

    assert getattr(Image.open(apng), "n_frames", 1) == 6
    plan["sentences"][0]["gfx"][0]["pattern"] = str(tmp_path / "none" / "f_{:03d}.png")
    with pytest.raises(G.GuideError):
        G.layers(plan, words, total=3.0)


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


def test_files_row_appears_on_words_with_marks(tmp_path: Path) -> None:
    plan = {"sentences": [{"text": "Внутри файлы модели и текстуры без риггинга.",
                           "gfx": [{"kind": "files", "y": 0.4, "size": 200, "gap": 40,
                                    "items": [{"ext": ".moc3", "name": "модель"},
                                              {"ext": ".png", "variant": "folder", "at": "текстуры"},
                                              {"ext": ".psd", "state": "bad", "state_at": "без", "at": "модели"}]}]}]}
    words = _words(plan)
    layers, _ = G.layers(plan, words, total=words[-1]["end"] + 1.0)
    files = [la for la in layers if la["props"].get("component") == "file"]
    assert [f["props"]["title"] for f in files] == [".moc3", ".png", ".psd"]
    # Ряд по центру кадра, слева направо; все до конца пункта.
    xs = [f["x"] for f in files]
    assert xs == sorted(xs) and abs((xs[0] + xs[-1]) / 2 - 0.5) < 0.01
    assert {f["end"] for f in files} == {files[0]["end"]}
    # Значок — на своём слове (−0.1 с), первый — с начала пункта.
    assert files[0]["start"] == pytest.approx(0.0)
    assert files[1]["start"] == pytest.approx(words[4]["start"] - 0.1, abs=1e-3)
    assert files[1]["props"]["variant"] == "folder"
    # Крест — на «без», время от начала своего значка.
    psd = files[2]
    assert psd["props"]["state"] == "bad"
    assert psd["start"] + psd["props"]["delay"] == pytest.approx(words[5]["start"] - 0.05, abs=1e-3)


def test_error_window_shakes_on_word() -> None:
    plan = {"sentences": [{"text": "Открываешь программу а она не видит файл.",
                           "gfx": [{"kind": "error", "app": "VTube Studio", "title": "Формат не поддерживается",
                                    "text": "model.vrm", "shake": "видит", "x": 0.5, "y": 0.64}]}]}
    words = _words(plan)
    layers, _ = G.layers(plan, words, total=words[-1]["end"] + 1.0)
    win = layers[0]
    assert win["props"]["component"] == "window" and win["props"]["variant"] == "error"
    assert win["props"]["label"] == "VTube Studio" and win["props"]["text"] == "model.vrm"
    xs = win["keys"]["x"]
    assert xs[0]["t"] == pytest.approx(words[5]["start"] - win["start"], abs=1e-3)
    assert xs[0]["v"] == 0.5 and xs[-1]["v"] == 0.5 and min(k["v"] for k in xs) < 0.5 < max(k["v"] for k in xs)


def test_files_without_items_is_an_error() -> None:
    plan = {"sentences": [{"text": "Пусто.", "gfx": [{"kind": "files", "items": []}]}]}
    with pytest.raises(G.GuideError):
        G.layers(plan, _words(plan), total=3.0)


def test_plan_guide_formats_is_valid() -> None:
    """Гайд 6 «не открывается»: крючок, окно ошибки с кадра 0, значки файлов, в речи — без английских слов и сумм."""
    import re

    from app import vtubika_plan as P

    plan = json.loads((Path(__file__).resolve().parent.parent / "tools" / "vtubika_plans" / "guide_formats.json").read_text("utf-8"))
    assert plan["hook"]["word"].upper() == "НЕ ОТКРЫВАЕТСЯ?"
    assert plan["vrm"]["heroine"] == "olya_host" and plan["voice"]["voice"] == "lily"
    first = plan["sentences"][0]["gfx"]
    err = next(g for g in first if g["kind"] == "error")
    assert err["dt"] < 0 and err["in"] == "none" and "не поддерживается" in err["title"]
    kinds = {g["kind"] for s in plan["sentences"] for g in s.get("gfx") or []}
    assert "files" in kinds and kinds <= {"scrim", "panel", "pip", "title", "rows", "ui", "chip", "image", "seq", "pngtuber",
                                          "text", "layer", "files", "error"}
    exts = {f["ext"] for s in plan["sentences"] for g in s.get("gfx") or [] if g["kind"] == "files" for f in g["items"]}
    assert {".model3.json", ".moc3", ".vrm", ".png", ".psd"} <= exts
    speech = P.narration_text(plan)
    assert not re.search(r"[A-Za-z]", speech), speech
    assert not re.search(r"\d|₽|рубл|скидк|процент", speech), speech
    # Free10 — только в концовке, как в гайде 5.
    assert "Free10" in plan["end_card"]["promo"]
    assert "Free10" not in json.dumps(plan["sentences"], ensure_ascii=False)
