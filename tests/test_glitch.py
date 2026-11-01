"""Глитч на отрезке (app/glitch.py, MCP add_glitch): слой fx стиля с ключами силы + звук из пака."""

from __future__ import annotations

import pytest

from app import motion
from app.agent_tools import run_tool
from app.glitch import GLITCH_SFX, GLITCH_STYLES, GlitchError, build_glitch, glitch_keys, glitch_style
from test_agent_tools import _ctx, _sfx_pack
from test_api_pipeline import _require_ffmpeg
from test_clip_spec import _setup


def test_styles_aliases_and_fx_kinds():
    for style in GLITCH_STYLES:
        assert style in motion.FX_KINDS
        assert GLITCH_SFX[style].startswith("sfx:freesound/")
    # Тот же набор, что у движка (шаблон «Глитч на отрезке»).
    import pathlib

    fx_ts = (pathlib.Path(__file__).resolve().parents[1] / "frontend/src/motion/layers/fx.ts").read_text(encoding="utf-8")
    for style, ref in GLITCH_SFX.items():
        assert f'{style}: "{ref}"' in fx_ts, style
    assert glitch_style("datamosh") == "datamosh_lite" and glitch_style("RGB-shift") == "rgb_split"
    with pytest.raises(GlitchError):
        glitch_style("лава")
    clean = motion.normalize_layers([{"type": "fx", "start": 0, "end": 1, "props": {"kind": "pixel_sort"}}])[0]
    assert clean["props"]["kind"] == "pixel_sort_lite"


def test_keys_envelope_mirrors_engine():
    keys = glitch_keys(0.8)
    # Тот же расчёт, что frontend/src/motion/layers/fx.ts glitchKeys (node-тест сверяет шаблон).
    assert keys == [
        {"t": 0, "v": 0.25, "ease": "linear"}, {"t": 0.05, "v": 1, "ease": "linear"},
        {"t": 0.24, "v": 0.45, "ease": "step"}, {"t": 0.336, "v": 1, "ease": "linear"},
        {"t": 0.56, "v": 0.6, "ease": "step"}, {"t": 0.656, "v": 0.95, "ease": "outCubic"}, {"t": 0.8, "v": 0.15}]
    short = glitch_keys(0.2)
    assert [k["v"] for k in short] == [0.3, 1, 0.2] and short[-1]["t"] == 0.2


def test_build_glitch_layers_and_limits():
    res = build_glitch(2.0, 0.6, "tear", 0.9, sfx=True)
    fx = res["fx"]
    assert fx["type"] == "fx" and fx["props"]["kind"] == "tear" and fx["props"]["intensity"] == 0.9
    assert (fx["start"], fx["end"]) == (2.0, 2.6) and fx["track"] == 0
    assert fx["keys"]["scale"] == glitch_keys(0.6)
    assert res["audio"]["props"]["asset_id"] == "sfx:freesound/record-scratch.ogg"
    assert res["audio"]["start"] == 2.0 and res["audio"]["end"] == 2.9
    assert build_glitch(1, sfx="sfx:freesound/chop.ogg")["audio"]["props"]["asset_id"] == "sfx:freesound/chop.ogg"
    assert build_glitch(1)["audio"] is None
    # Хвост клипа: отрезок обрезается по концу; после конца — ошибка.
    assert build_glitch(9.7, 1.0, clip_duration=10.0)["range"] == [9.7, 10.0]
    with pytest.raises(GlitchError):
        build_glitch(10.5, clip_duration=10.0)
    with pytest.raises(GlitchError):
        build_glitch(1, duration=30)
    # Нормализация слоя не теряет ключи силы и вид.
    clean = motion.normalize_layers([{**fx, "id": "g"}])[0]
    assert clean["keys"]["scale"][1]["v"] == 1 and clean["props"]["kind"] == "tear"


def test_add_glitch_tool(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    _sfx_pack(tmp_path, monkeypatch, files=("hit-transition.ogg", "bass-drop.ogg"))
    ctx = _ctx(store, plan["id"])
    out, ok = run_tool(ctx, "add_layers", {"layers": [
        {"type": "text", "start": 0, "end": 3, "props": {"text": "ТИТР"}}]})
    assert ok, out
    out, ok = run_tool(ctx, "add_glitch", {"at": 1.0, "duration": 0.7, "style": "rgb_split", "sfx": True})
    assert ok, out
    assert out["style"] == "rgb_split" and out["sfx"] == "sfx:freesound/hit-transition.ogg" and out["range"] == [1.0, 1.7]
    layers = store.get_clip_plan(plan["id"])["render_settings"]["layers"]
    # Глитч — первым на дорожке 0 (под титром), звук — в конце списка.
    assert layers[0]["id"] == out["id"] and layers[0]["type"] == "fx" and layers[0]["keys"]["scale"]
    assert layers[-1]["type"] == "audio" and layers[-1]["id"] == out["sfx_id"]
    # Нет записи в паке — глитч без звука, с пометкой; over_layers — над всеми.
    out, ok = run_tool(ctx, "add_glitch", {"at": 2.0, "style": "vhs", "sfx": True, "over_layers": True})
    assert ok and out["sfx"] is None and out["notes"], out
    layers = store.get_clip_plan(plan["id"])["render_settings"]["layers"]
    top = next(l for l in layers if l["id"] == out["id"])
    assert top["track"] >= 1 and top["props"]["kind"] == "vhs"
    out, ok = run_tool(ctx, "add_glitch", {"at": 1.0, "style": "lava"})
    assert not ok and "style" in out["error"]
    out, ok = run_tool(ctx, "add_glitch", {"at": 99})
    assert not ok
