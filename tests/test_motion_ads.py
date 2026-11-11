"""Рекламный пакет моушна: одометр у counter, облако пузырей, карусель, волна голоса (огибающая своего
звука), стена плиток, схема сделки, наклон группы по pivot (camera_preset screen_tilt) и шаблоны галереи
для агента. Движок — node/motion-ads.test.mjs."""

from __future__ import annotations

import json
import math
import shutil
import struct
import wave

import pytest

from app import agent_tools, motion
from app.aspect import layer_size, reframe_between
from app.camera3d import PresetError, apply_preset, describe
from app.layer_lint import cover_share, covering_problems, is_decor_layer
from app.motion_limits import BUBBLES_MAX
from app.motion_render import resolve_layer_assets
from app.quality_lint_zones import layer_box


def _one(raw: dict) -> dict:
    [layer] = motion.normalize_layers([{"start": 0, "end": 4, **raw}])
    return layer


def test_counter_roll_props_normalize():
    c = _one({"type": "counter", "props": {"to": 2000000, "roll": True, "stagger": 0.9, "laps": 9}})
    assert c["props"]["roll"] is True
    assert (c["props"]["stagger"], c["props"]["laps"]) == (0.5, 6)  # зажаты в рамки
    plain = _one({"type": "counter", "props": {"to": 5}})
    assert "roll" not in plain["props"] and "laps" not in plain["props"]  # старые счётчики не меняются
    problems = motion.validate_layers([{"type": "counter", "props": {"roll": True, "laps": 99}}])
    assert any("laps" in p for p in problems)


def test_ad_layers_normalize_and_required():
    b = _one({"type": "bubbles", "props": {"items": [{"label": "отзывов", "value": "48000", "icon": "star",
                                                       "image": 7, "junk": 1}] * 14, "focus": 3, "style": "neon"}})
    assert len(b["props"]["items"]) == BUBBLES_MAX
    assert b["props"]["items"][0] == {"label": "отзывов", "value": 48000.0, "icon": "star", "image": "7"}
    assert "style" in b["props"] and b["props"]["style"] == "glass"  # неизвестный вид — по умолчанию, с пометкой
    c = _one({"type": "carousel", "props": {"cards": [{"title": "Ника", "image": "sticker:pack/a.png"}],
                                            "switchAt": [1.5, 3], "sideScale": 5}})
    assert c["props"]["cards"][0]["image"] == "sticker:pack/a.png" and c["props"]["sideScale"] == 1
    w = _one({"type": "waveform", "props": {"source": "voice", "style": "wave", "bars": 500}})
    assert (w["props"]["source"], w["props"]["style"], w["props"]["bars"]) == ("voice", "wave", 128)
    t = _one({"type": "tile_wall", "props": {"images": [1, 2, "emoji:1f600"], "tiltX": 28}})
    assert t["props"]["images"][:2] == ["1", "2"]  # emoji не картинка плитки — отброшен с пометкой
    f = _one({"type": "flow_diagram", "props": {"nodes": [{"label": "А"}, {"label": "Б", "icon": "shield"}], "done": ""}})
    assert f["props"]["done"] == "" and f["props"]["nodes"][1]["icon"] == "shield"
    problems = motion.validate_layers([
        {"type": "bubbles", "props": {}},
        {"type": "carousel", "props": {"cards": []}},
        {"type": "flow_diagram", "props": {"nodes": [{"label": "один"}]}},
        {"type": "waveform", "props": {"audio_layer": "a1", "asset_id": 3}},
    ])
    assert any("items" in p for p in problems) and any("cards" in p for p in problems)
    assert any("2 узла" in p for p in problems) and any("audio_layer и asset_id" in p for p in problems)
    schema = motion.schema_summary()
    assert {"bubbles", "carousel", "waveform", "tile_wall", "flow_diagram"} <= set(schema["types"])
    assert "shield" in schema["ads"]["icons"] and schema["props"]["counter"]["roll"]["optional"]
    json.dumps(schema, ensure_ascii=False)
    ref = agent_tools.motion_reference()
    text = json.dumps(ref, ensure_ascii=False)
    assert "screen_tilt" in text and "flow_diagram" in text and "roll: true" in text


def test_pivot_field_and_asset_refs():
    layer = _one({"type": "text", "pivot": {"x": 0.5, "y": 9}, "props": {"text": "x"}})
    assert layer["pivot"] == {"x": 0.5, "y": 3}
    assert "pivot" not in _one({"type": "text", "props": {"text": "x"}})
    refs = motion.layer_asset_refs(_one({"type": "carousel", "props": {"cards": [{"image": 4}, {"image": 4}, {"title": "x"}]}}))
    assert refs == [4]
    assert motion.layer_asset_refs(_one({"type": "tile_wall", "props": {"images": [5, "sticker:p/x.png"]}})) == [5, "sticker:p/x.png"]

    class NoFiles:
        def get_montage_asset(self, _id):
            raise KeyError(_id)

    # Пропавшая картинка пузыря/плитки — украшение: слой остаётся, пометка есть.
    kept, _assets, notes = resolve_layer_assets(NoFiles(), [_one({"id": "tw", "type": "tile_wall", "props": {"images": [9]}})])
    assert [l["id"] for l in kept] == ["tw"] and notes
    assert motion.react_sources([_one({"type": "waveform", "props": {"source": "music"}}),
                                 _one({"type": "waveform", "props": {}})]) == ["music"]


def test_sizes_cover_and_safe_zones():
    car = _one({"type": "carousel", "y": 0.45, "props": {"cards": [{"title": "А"}], "cardH": 700}})
    assert layer_size(car) == (1080, 700)
    assert 0.18 < cover_share(car) < 0.25  # закрывает центральная карточка, а не вся лента
    wall = _one({"type": "tile_wall", "props": {}})
    assert cover_share(wall) == 1.0 and is_decor_layer(wall)
    assert covering_problems([wall]) and not covering_problems([_one({"type": "waveform", "props": {}})])
    assert cover_share(_one({"type": "bubbles", "props": {"items": [{"value": 1}]}})) == 0.0
    flow = _one({"type": "flow_diagram", "y": 0.9, "props": {"nodes": [{"label": "А"}, {"label": "Б"}]}})
    box = layer_box(flow, 1.0)
    assert box and box[3] > 1920  # низ схемы у края — линт зон его видит
    # Стена во весь кадр в 16:9 тянется по ширине (как фон), а не мельчает.
    layers, _ = reframe_between([wall], [], "9:16", "16:9")
    assert layers[0]["props"]["w"] >= 1920 and layers[0]["scale"] == 1.0


def test_camera_preset_screen_tilt_group():
    layers = motion.normalize_layers([
        {"id": "wp3_a", "type": "image", "start": 0, "end": 6, "x": 0.5, "y": 0.3, "props": {"asset_id": 5}},
        {"id": "wp3_b", "type": "text", "start": 2, "end": 6, "y": 0.6, "scale": 2, "props": {"text": "кнопка"}},
        {"id": "title", "type": "text", "start": 0, "end": 6, "props": {"text": "титр"}},
    ])
    res = apply_preset("screen_tilt", 1, 3, 1, layers=layers, layer_prefix="wp3_", point={"x": 0.5, "y": 0.6}, zoom=1.5)
    a, b, title = res["layers"]
    assert a["pivot"] == b["pivot"] == {"x": 0.5, "y": 0.6} and "pivot" not in title and not title["keys"]
    assert [k["v"] for k in a["keys"]["rotX"]] == [24, 10.8, 2.88] and a["keys"]["rotX"][0]["t"] == 1
    assert a["keys"]["scale"][-1]["v"] == 1.5
    # Слой начался позже отрезка: первая точка — в его начале, значение кривой в этот момент; scale — × база.
    assert b["keys"]["rotX"][0]["t"] == 0 and 2.88 < b["keys"]["rotX"][0]["v"] < 24
    assert b["keys"]["scale"][-1]["v"] == 3.0
    # Без точки — центр группы.
    res2 = apply_preset("screen_tilt", 0, None, 1, layers=layers, layer_ids=["wp3_a", "title"])
    assert res2["layers"][0]["pivot"] == {"x": 0.5, "y": 0.4}
    with pytest.raises(PresetError):
        apply_preset("screen_tilt", 0, layers=layers)
    assert any(p["id"] == "screen_tilt" and p["layer"] for p in describe())


def test_motion_templates_tools(monkeypatch):
    from app import motion_template_tools as T

    fake = [{"id": "deal-flow", "name": "Схема", "category": "Реклама", "description": "щит", "duration": 5,
             "params": [{"name": "done", "label": "Итог", "type": "text", "default": "Готово"}]},
            {"id": "title-pop", "name": "Заголовок", "category": "Титры", "description": "", "duration": 3, "params": []}]
    monkeypatch.setattr(T, "list_templates", lambda: fake)
    out = T.h_list_motion_templates(None, {})
    assert [t["id"] for t in out["templates"]] == ["deal-flow", "title-pop"] and "params" not in out["templates"][0]
    out = T.h_list_motion_templates(None, {"category": "реклама"})
    assert out["templates"][0]["params"][0]["name"] == "done"
    names = [t.name for t in agent_tools.list_tools()]
    assert names.index("add_motion_template") == names.index("list_motion_templates") + 1
    tool = agent_tools.get_tool("add_motion_template")
    assert tool.mutating and tool.parameters["required"] == ["template", "at"]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="нет ffmpeg")
def test_wave_tracks_follow_own_sound(tmp_path, monkeypatch):
    from app.settings import settings
    from app.waveform_levels import wave_tracks

    monkeypatch.setattr(settings, "tmp_dir", tmp_path)
    path = tmp_path / "vo.wav"
    rate = 8000
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        # 0–1 с тишина, 1–2 с тон, 2–3 с тишина.
        frames = [int(12000 * math.sin(2 * math.pi * 220 * i / rate)) if rate <= i < 2 * rate else 0
                  for i in range(3 * rate)]
        w.writeframes(b"".join(struct.pack("<h", v) for v in frames))

    class Store:
        def get_montage_asset(self, asset_id):
            assert asset_id == 5
            return {"file_path": str(path)}

    layers = motion.normalize_layers([
        {"id": "vo", "type": "audio", "start": 2, "end": 5, "props": {"asset_id": 5, "mode": "track"}},
        {"id": "w1", "type": "waveform", "start": 0, "end": 6, "props": {"audio_layer": "vo"}},
        {"id": "w2", "type": "waveform", "start": 1, "end": 4, "props": {"asset_id": 5}},
        {"id": "w3", "type": "waveform", "start": 0, "end": 4, "props": {"source": "voice"}},
    ])
    tracks, notes = wave_tracks(Store(), layers, 10)
    assert set(tracks) == {"wave:w1", "wave:w2"} and not notes
    w1 = tracks["wave:w1"]["v"]
    assert tracks["wave:w1"]["t0"] == 0 and len(w1) == 60
    # Звуковой слой с 2 с: тон файла (1–2 с) звучит в 3–4 с клипа.
    assert max(w1[31:39]) > 80 and max(w1[:20]) == 0
    w2 = tracks["wave:w2"]["v"]  # файл от начала волны (1 с): тон — 2–3 с клипа
    assert max(w2[11:19]) > 80 and max(w2[:8]) == 0
    missing, notes = wave_tracks(Store(), motion.normalize_layers([
        {"id": "w", "type": "waveform", "props": {"audio_layer": "nope"}}]), 10)
    assert not missing and "nope" in notes[0]
