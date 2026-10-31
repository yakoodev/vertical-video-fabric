"""Кинетическая типографика, эффект кадра ``fx`` и реакция слоёв на звук ``react``
(docs/motion/spec.md: «Кинетическая типографика», «Эффект кадра», «Реакция на звук»)."""

from __future__ import annotations

import math

import pytest

from app import motion
from app.audio_react import envelope, level_at, music_positions, plan_levels, power_sum, shift_levels
from app.motion import normalize_layers, validate_layers


def _text(**over):
    layer = {"id": "t", "type": "text", "start": 0, "end": 3, "props": {"text": "Привет мир"}}
    layer.update(over)
    return layer


# ------------------------------------------------------------------ нормализация


def test_new_animations_and_aliases_with_notes():
    layers = [
        _text(**{"in": "decode", "out": {"type": "glitch", "duration": 0.3}, "loop": "wave"}),
        {"id": "i", "type": "image", "start": 0, "end": 2, "props": {"src": "emoji:1f525"},
         "in": {"type": "scramble"}, "out": "stomp", "loop": {"type": "glitch"}},
        {"id": "s", "type": "shape", "start": 0, "end": 2, "props": {"kind": "rect"}, "in": "slam", "loop": "wave"},
    ]
    problems = validate_layers(layers)
    clean = normalize_layers(layers)
    assert clean[0]["in"]["type"] == "scramble" and clean[0]["out"]["type"] == "glitch"
    assert clean[0]["loop"]["type"] == "wave"
    assert any("'decode' — это 'scramble'" in p for p in problems)
    # Буквенные анимации у картинки — похожие: вход fade, цикл glitch → shake; удар — у любого.
    assert clean[1]["in"]["type"] == "fade" and clean[1]["out"]["type"] == "stomp"
    assert clean[1]["loop"]["type"] == "shake"
    assert clean[2]["in"]["type"] == "stomp" and clean[2]["loop"]["type"] == "float"
    assert any("«glitch» бывает только у текста — заменено на shake" in p for p in problems)
    assert any("«wave» бывает только у текста — заменено на float" in p for p in problems)
    # Нормализованное проверяется молча — правила не разъезжаются.
    assert validate_layers(clean) == []


def test_fx_layer_rules():
    raw = [{"id": "fx", "type": "fx", "start": 1, "end": 4, "props": {"kind": "crt", "intensity": 5},
            "in": "pop", "out": {"type": "fade", "duration": 0.2}, "loop": "pulse", "blend": "screen",
            "followCamera": True, "shadow": {"blur": 10}}]
    problems = validate_layers(raw)
    fx = normalize_layers(raw)[0]
    assert fx["props"]["kind"] == "scanlines" and fx["props"]["intensity"] == 1
    assert fx["props"]["blend"] == "screen" and fx["blend"] == "normal"
    assert fx["in"]["type"] == "fade" and fx["out"]["type"] == "fade" and fx["loop"]["type"] == "none"
    assert fx["followCamera"] is False and fx["shadow"] is None
    assert len(problems) == 7 and any("'crt' — это 'scanlines'" in p for p in problems)
    # Сила и наложение необязательны: не заданы — свои у вида (движок), в нормализацию не попадают.
    plain = normalize_layers([{"type": "fx", "start": 0, "end": 1, "props": {"kind": "grain_overlay"}}])[0]
    assert "intensity" not in plain["props"] and "blend" not in plain["props"] and plain["props"]["speed"] == 1
    bad = normalize_layers([{"type": "fx", "start": 0, "end": 1, "props": {"kind": "лава"}}])[0]
    assert bad["props"]["kind"] == "vhs"
    assert motion.layer_asset_refs(fx) == []
    schema = motion.schema_summary()
    assert "fx" in schema["types"] and schema["fx"]["kinds"] == list(motion.FX_KINDS)
    assert schema["props"]["fx"]["kind"]["options"] == list(motion.FX_KINDS)


def test_react_normalization():
    layers = [
        _text(react="speech"),
        _text(id="b", react={"source": "all", "prop": "jump", "amount": 9, "smooth": -1, "color": "#f0f"}),
        _text(id="c", react=True),
        {"id": "a", "type": "audio", "start": 0, "end": 1, "props": {"asset_id": "sfx:x/a.ogg"}, "react": True},
        _text(id="d"),
    ]
    problems = validate_layers(layers)
    clean = normalize_layers(layers)
    assert clean[0]["react"] == {"source": "voice", "prop": "scale", "amount": 1, "smooth": 0.15}
    assert clean[1]["react"] == {"source": "mix", "prop": "y", "amount": 3, "smooth": 0, "color": "#ff00ff"}
    assert clean[2]["react"]["source"] == "voice"
    assert "react" not in clean[3] and "react" not in clean[4]  # у звука — отброшено, без поля — нет поля
    assert any("реакция на звук отброшена" in p for p in problems)
    assert motion.react_sources(clean) == ["voice", "mix"]
    assert motion.react_sources([{**clean[1], "hidden": True}]) == []


def test_marker_highlight_keeps_text_color_and_caption_stomp():
    clean = normalize_layers([
        _text(props={"text": "очень важно", "color": "#00ff00", "highlight": {"words": [1], "style": "marker",
                                                                            "draw": 9}}),
        _text(id="b", props={"text": "а", "highlight": {"words": [0], "style": "marker", "color": "#ff0000"}}),
        {"id": "c", "type": "captions", "start": 0, "end": 3, "props": {"style": "stomp", "animation": "stomp"}},
    ])
    hl = clean[0]["props"]["highlight"]
    assert hl["style"] == "marker" and hl["color"] == "#00ff00" and hl["draw"] == 3
    assert clean[1]["props"]["highlight"]["color"] == "#ff0000"  # свой цвет букв — остаётся
    assert clean[2]["props"]["style"] == "stomp" and clean[2]["props"]["animation"] == "stomp"
    # Старая подсветка без style — как была (полей не прибавилось).
    old = normalize_layers([_text(props={"text": "а", "highlight": {"words": [0], "bg": "#000000"}})])[0]
    assert set(old["props"]["highlight"]) == {"words", "color", "bg"}


def test_fx_crowding_and_lint():
    from app.agent_tools import is_decor_layer, lint_layers

    fx = [{"id": f"f{i}", "type": "fx", "start": i * 0.5, "end": 3, "props": {"kind": "vhs"}} for i in range(3)]
    assert motion.fx_crowding(fx[:2]) == []
    warn = motion.fx_crowding(fx)
    assert warn and "одновременно 3" in warn[0]
    assert any("эффектов кадра одновременно" in w for w in lint_layers(normalize_layers(fx), duration=5))
    assert is_decor_layer(normalize_layers(fx)[0])


# ------------------------------------------------------------------ огибающая громкости


def test_envelope_floor_top_and_release():
    fps = 30.0
    lv = [-60.0] * 30 + [-20.0] * 15 + [-60.0] * 30 + [None] * 5
    env = envelope(lv, fps)
    assert env[:30] == [0] * 30  # шум — ноль
    assert env[30:45] == [100] * 15  # громкое — 100
    assert 0 < env[46] < 100 and env[70] == 0  # спад ~0.12 с, потом снова ноль
    # Шумодав: между фразами цифровой ноль, слышна только ровная речь — она громкая, а не «шум».
    assert envelope([None] * 10 + [-25.0] * 10, fps)[10:] == [100] * 10
    assert env[-1] == 0 and len(env) == len(lv)
    assert envelope([None, -90.0, None], fps) == [0, 0, 0]  # звука нет — тишина


def test_power_sum_music_positions_shift_and_level_at():
    assert power_sum(None, None) is None
    assert power_sum(-20.0, None) == pytest.approx(-20.0)
    assert power_sum(-20.0, -20.0) == pytest.approx(-20.0 + 10 * math.log10(2))
    # Трек 10 с с 8-й секунды: 0 с ролика → 8 с трека, 3 с → 1 с трека (по кругу с начала).
    pos = music_positions([0.0, 1.0, 3.0, None], 8.0, 10.0)
    assert pos[0] == (None, 8.0) and pos[1] == (None, 9.0) and pos[2][1] == pytest.approx(1.0) and pos[3] is None
    levels = {"voice": {"t0": 0.0, "fps": 10.0, "v": [10, 20, 30, 40]}}
    moved = shift_levels(levels, [{"at": 0.2, "duration": 0.3, "audio": "silence"}])
    assert moved["voice"]["v"] == [10, 20, 0, 0, 0, 30, 40]
    assert level_at(moved["voice"], 0.55) == 30 and level_at(moved["voice"], 9) == 0


def _clip(tmp_path, monkeypatch):
    from test_pngtuber import _plan_with_assets

    store, service, plan, _ids, comps, kwargs, calls = _plan_with_assets(tmp_path, monkeypatch)
    return store, service, plan, comps, kwargs


def test_plan_levels_voice_mix_music(tmp_path, monkeypatch):
    from test_pngtuber import _reader

    store, _service, plan, *_ = _clip(tmp_path, monkeypatch)
    seen: list = []

    def reader(store_, track, notes, silent=""):
        seen.append(track)
        return _reader(lambda t: 0.3 if 2.0 <= t < 3.0 else 0.003)

    monkeypatch.setattr("app.audio_react.source_reader", reader)
    levels, notes = plan_levels(store, plan, ["voice", "mix", "music"], 10.0, "clip")
    assert seen == ["speech", "mix"]
    voice = levels["voice"]
    assert voice["fps"] == 10.0 and len(voice["v"]) == 50  # клип 5 с
    assert level_at(voice, 2.5) == 100 and level_at(voice, 1.0) == 0
    assert levels["mix"]["v"] == voice["v"]  # музыки нет — смесь = звук источника
    assert levels["music"]["v"] == [0] * 50 and any("нет фоновой музыки" in n for n in notes)
    assert plan_levels(store, plan, [], 10.0) == ({}, [])


def test_render_and_api_pass_levels(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import app.main as main_module
    from test_pngtuber import _reader

    store, service, plan, comps, kwargs = _clip(tmp_path, monkeypatch)
    monkeypatch.setattr("app.audio_react.source_reader", lambda store_, track, notes, silent="": _reader(
        lambda t: 0.3 if 1.0 <= t < 2.0 else 0.0))
    store.set_clip_plan_render_settings(plan["id"], {"layers": [
        _text(end=5, react={"source": "voice", "prop": "glow"}),
        {"id": "fx", "type": "fx", "start": 0, "end": 5, "props": {"kind": "grain_overlay"}}]})
    clip = service.render_clip_plan(plan["id"], **kwargs)
    assert clip["status"] == "succeeded", clip.get("error")
    comp = comps[0]
    assert set(comp["levels"]) == {"voice"} and level_at(comp["levels"]["voice"], 1.5) == 100
    assert any(layer["type"] == "fx" for layer in comp["layers"])

    monkeypatch.setattr(main_module, "store", store)
    monkeypatch.setattr("app.settings.settings.auth_enabled", False)
    client = TestClient(main_module.app)
    res = client.post(f"/api/clip-plans/{plan['id']}/levels", json={"sources": ["voice", "junk"], "fps": 20})
    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body["levels"]) == {"voice"} and body["scale"] == "clip" and body["levels"]["voice"]["fps"] == 20
    saved = client.post(f"/api/clip-plans/{plan['id']}/levels", json={})
    assert set(saved.json()["levels"]) == {"voice"}  # нет sources — по react сохранённых слоёв
