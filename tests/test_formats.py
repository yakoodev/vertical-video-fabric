"""Форматы вывода (app/formats.py): размеры, окно кропа, основа, раскладки, перекомпоновка
9:16 ↔ 16:9 без выхода за кадр, comp.json компоновщика 1920×1080."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.aspect import layer_size, reframe_between, reframe_plan
from app.formats import (
    comp_ref,
    format_of_size,
    frame_size,
    normalize_format,
    ref_size,
    settings_layers_format,
    settings_render_format,
    source_strategy,
)
from app.motion import normalize_camera, normalize_layers
from format_golden_cases import run_case

H_SRC = {"source_type": "upload", "width": 1920, "height": 1080}
V_SRC = {"source_type": "upload", "width": 1080, "height": 1920}


# ---------------------------------------------------------------- реестр


def test_registry_sizes_and_names():
    assert frame_size("9:16") == (1080, 1920) and ref_size("9:16") == (1080, 1920)
    assert frame_size("1:1") == (1080, 1080) and frame_size("4:5") == (1080, 1350)
    assert frame_size("16:9") == (1920, 1080) and ref_size("16:9") == (1920, 1080)
    assert normalize_format(None) == "9:16" and normalize_format("16x9") == "16:9"
    assert normalize_format("Горизонталь") == "16:9" and normalize_format("square") == "1:1"
    with pytest.raises(ValueError):
        normalize_format("21:9")
    assert format_of_size(1920, 1080) == "16:9" and format_of_size(720, 1280) is None
    # Старое поле aspect читается как format; format важнее.
    assert settings_render_format({"aspect": "4:5"}) == "4:5"
    assert settings_render_format({"aspect": "4:5", "format": "16:9"}) == "16:9"
    assert settings_render_format({"format": "мусор"}) == "9:16"
    assert settings_layers_format({}) == "9:16" and settings_layers_format({"layers_format": "16:9"}) == "16:9"
    # comp.json получает опорный кадр только у 16:9 — у остальных движок считает от 1080 сам.
    assert comp_ref("9:16") == {} and comp_ref("1:1") == {} and comp_ref("16:9") == {"refWidth": 1920, "refHeight": 1080}


def test_source_strategy():
    assert source_strategy("16:9", H_SRC) == "cover"
    assert source_strategy("16:9", V_SRC) == "fit"
    assert source_strategy("16:9", {"width": 1440, "height": 1080}) == "cover"  # 4:3 — режем
    assert source_strategy("16:9", {"width": 1080, "height": 1080}) == "fit"  # квадрат — вписываем
    # Обрезка полей учитывается: 16:9 с огромными полями по бокам — по сути вертикаль.
    assert source_strategy("16:9", {**H_SRC, "content_crop": {"x": 0.35, "y": 0, "w": 0.3, "h": 1}}) == "fit"
    for fmt in ("9:16", "1:1", "4:5"):
        assert source_strategy(fmt, V_SRC) == "cover" and source_strategy(fmt, H_SRC) == "cover"


def test_preset_with_output_per_format():
    from app.render import _preset_with_output

    base = {"id": 1, "output_width": 1080, "output_height": 1920}
    assert _preset_with_output(base, H_SRC, "9:16") is base  # прежний пресет как есть
    wide = _preset_with_output(base, H_SRC, "16:9")
    assert (wide["output_width"], wide["output_height"], wide["format"]) == (1920, 1080, "16:9")
    assert "base_fit" not in wide and wide["landscape"]
    assert _preset_with_output(base, V_SRC, "16:9")["base_fit"] == "fit"
    # Слои размечены в 16:9, а рендер 9:16 — пресет помнит формат разметки.
    assert _preset_with_output(base, H_SRC, "9:16", 0.0, "16:9")["layers_format"] == "16:9"


# ---------------------------------------------------------------- основа (ffmpeg)


def _graph(preset: dict, source: dict, segment: dict | None = None, source_crop=None) -> str:
    from app.render import _preset_for_segment, _render_filter, _segment_reframe_x

    segment = segment or {"start_sec": 0, "end_sec": 6}
    piece = _preset_for_segment(preset, segment)
    reframe = _segment_reframe_x(segment, piece, source)
    return _render_filter(int(preset["output_width"]), int(preset["output_height"]), 30, piece, None, source_crop, reframe)


def _out(fmt: str, source: dict, **extra) -> dict:
    from app.render import _preset_with_output

    return _preset_with_output({"id": 1, "output_width": 1080, "output_height": 1920, "smart_reframe": True,
                                "scale_mode": "cover", **extra}, source, fmt)


FOCUS = {"start_sec": 0, "end_sec": 6, "focus": [{"t": 0, "x": 0.2}, {"t": 3, "x": 0.8}]}


def test_horizontal_source_to_16x9_is_scale_without_crop():
    g = _graph(_out("16:9", H_SRC), H_SRC, FOCUS)
    assert "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080:(iw-1920)/2:(ih-1080)/2" in g
    assert "blur" not in g  # та же пропорция — ни подложки, ни движения окна


def test_wider_source_to_16x9_crops_with_focus():
    cinema = {"source_type": "upload", "width": 2560, "height": 1080}
    g = _graph(_out("16:9", cinema), cinema, FOCUS)
    assert "crop=1920:1080:clip(" in g and "iw-1920)" in g  # окно 16:9 едет за фокусом


@pytest.mark.parametrize("fmt", ["9:16", "1:1", "4:5"])
def test_horizontal_source_crop_window_has_format_proportion(fmt):
    w, h = frame_size(fmt)
    g = _graph(_out(fmt, H_SRC), H_SRC, FOCUS)
    m = re.search(r"scale=(\d+):(\d+):force_original_aspect_ratio=increase,crop=(\d+):(\d+):clip\(", g)
    assert m, g
    assert tuple(map(int, m.groups())) == (w, h, w, h)  # окно ровно пропорции формата
    assert f"iw-{w}" in g  # x(t) фокуса зажат по ширине окна этого формата


def test_vertical_source_to_16x9_fits_height_on_blurred_backdrop():
    g = _graph(_out("16:9", V_SRC), V_SRC)
    assert "boxblur" in g and "scale=1920:1080:force_original_aspect_ratio=decrease[fg]" in g
    # С хромакеем — поля цвета ключа, а не размытая зелень.
    from app.render import _keyed_render

    base, _motion = _keyed_render(_out("16:9", V_SRC), {"key": {"color": "#00ff00"}})
    g = _graph(base, V_SRC)
    assert "boxblur" not in g and "pad=1920:1080" in g and "0x00ff00" in g


def test_piece_fit_is_ignored_in_landscape():
    seg = {"start_sec": 0, "end_sec": 6, "fit": {"w": 0.6, "bg": "blur"}}
    assert "pbg" in _graph(_out("9:16", H_SRC), H_SRC, seg)  # в вертикали — «шире окна»
    assert "pbg" not in _graph(_out("16:9", H_SRC), H_SRC, seg)  # в 16:9 широкий план и так целиком


# ---------------------------------------------------------------- раскладки в 16:9


def test_layouts_in_landscape():
    from app.stream_layout import camera_regions, layout_for_format, normalize_layout

    split = normalize_layout({"mode": "split", "cam": {"x": 0.72, "y": 0.04, "w": 0.26, "h": 0.3}, "cam_pos": "top"})
    assert layout_for_format(split, "9:16", H_SRC) is split  # вертикаль — как есть
    pip = layout_for_format(split, "16:9", H_SRC)
    assert pip["mode"] == "pip_cam" and pip["pip"]["x"] > 0.8 and pip["pip"]["y"] < 0.25
    bottom = layout_for_format({**split, "cam_pos": "bottom"}, "16:9", H_SRC)
    assert bottom["pip"]["y"] > 0.75
    # Окошко целиком в кадре 1920×1080 с полями.
    rect = camera_regions(pip, 1920, 1080, 1920, 1080)["pinned"][0]
    assert rect["x"] > 0 and rect["y"] > 0 and rect["x"] + rect["w"] < 1920 and rect["y"] + rect["h"] < 1080
    assert 400 <= rect["w"] <= 560
    assert layout_for_format({"mode": "meme"}, "16:9", H_SRC) is None
    left = layout_for_format({"mode": "pip_cam", "pip": {"x": 0.2, "y": 0.8, "w": 0.42}}, "16:9", H_SRC)
    assert left["pip"]["x"] < 0.2 and left["pip"]["y"] > 0.75
    assert left["pip"]["w"] == pytest.approx(0.42 * 1080 / 1920, abs=1e-3)  # те же px, что в вертикали


def test_split_layout_renders_as_pip_in_16x9():
    from app.render import ClipRenderService

    preset = ClipRenderService._preset_with_render_overrides(
        None, {"id": 1, "output_width": 1080, "output_height": 1920, "scale_mode": "cover"},
        layout={"mode": "split", "cam_pos": "top"},
    )
    g = _graph(_out("16:9", H_SRC, layout=preset["layout"]), H_SRC)
    assert "vstack" not in g and "[lgame][lcam]overlay" in g and "scale=1920:1080" in g


# ---------------------------------------------------------------- перекомпоновка 9:16 ↔ 16:9


def _promo_9x16():
    return normalize_layers([
        {"id": "bg", "type": "shape", "start": 0, "end": 6, "x": 0.5, "y": 0.5,
         "props": {"kind": "rect", "w": 1080, "h": 1920, "fill": "#101020"}},
        {"id": "band", "type": "shape", "start": 0, "end": 6, "x": 0.5, "y": 0.14,
         "props": {"kind": "rect", "w": 1080, "h": 220, "fill": "#000000"}},
        {"id": "title", "type": "text", "start": 0, "end": 6, "x": 0.5, "y": 0.14,
         "props": {"text": "Задачи команды —\nв одном месте", "size": 80, "maxWidth": 960}},
        {"id": "phone", "type": "shape", "start": 0, "end": 6, "x": 0.5, "y": 0.52, "followCamera": True,
         "props": {"kind": "rect", "w": 560, "h": 1150, "fill": "#222222"}},
        {"id": "tap", "type": "shape", "start": 2, "end": 2.6, "x": 0.6, "y": 0.45,
         "props": {"kind": "ellipse", "w": 96, "h": 96}, "keys": {"opacity": [{"t": 0, "v": 1}, {"t": 0.6, "v": 0}]}},
        {"id": "cta", "type": "text", "start": 0, "end": 6, "x": 0.5, "y": 0.89,
         "props": {"text": "Попробовать →", "size": 60, "bg": {"color": "#ff6a2b", "padX": 50, "padY": 24}}},
    ])


def _box(layer, w, h):
    size = layer_size(layer)
    k = layer["scale"]
    return (layer["x"] * w - size[0] * k / 2, layer["y"] * h - size[1] * k / 2,
            layer["x"] * w + size[0] * k / 2, layer["y"] * h + size[1] * k / 2)


def test_reframe_9x16_to_16x9_keeps_everything_inside_without_new_overlaps():
    layers = _promo_9x16()
    camera = normalize_camera([{"t": 0, "zoom": 1}, {"t": 2, "zoom": 1.8, "x": 0.5, "y": 0.5}, {"t": 4, "zoom": 1}])
    out, cam = reframe_between(layers, camera, "9:16", "16:9")
    by = {layer["id"]: layer for layer in out}
    margin = 0.04 * 1080 - 1
    title, phone, cta = (_box(by[k], 1920, 1080) for k in ("title", "phone", "cta"))
    for b in (title, phone, cta):
        assert b[0] >= 0 and b[2] <= 1920 and b[1] >= margin and b[3] <= 1080 - margin, b
    assert title[3] < phone[1] and phone[3] < cta[1]  # не наехали друг на друга
    plan = reframe_plan(layers, 1920, 1080, camera=camera)
    s = plan["scale"]
    assert by["title"]["scale"] == by["phone"]["scale"] == by["cta"]["scale"] == round(s, 4)
    # Касание — на той же точке экрана телефона (смещение × масштаб, x — в px 16:9).
    assert (by["tap"]["x"] - by["phone"]["x"]) * 1920 == pytest.approx((0.6 - 0.5) * 1080 * s, abs=0.5)
    # Фон во всю ширину растянут до новой ширины (лента — только по ширине, фон — и по высоте).
    assert _box(by["band"], 1920, 1080)[2] - _box(by["band"], 1920, 1080)[0] >= 1920 - 1
    assert by["band"]["props"]["h"] == 220
    bg = _box(by["bg"], 1920, 1080)
    assert bg[0] <= 0 and bg[2] >= 1920 and bg[1] <= 0 and bg[3] >= 1080
    # Наезд не выталкивает телефон followCamera за кадр.
    zoomed = next(k for k in cam if k["zoom"] > 1.01)
    z, cx, cy = zoomed["zoom"], zoomed["x"] * 1920, zoomed["y"] * 1080
    top = (phone[1] - cy) * z + 540
    bottom = (phone[3] - cy) * z + 540
    assert top >= margin - 1 and bottom <= 1080 - margin + 1


def _landscape_layers():
    """Слои, размеченные в 16:9 (опорный 1920×1080): титр слева, телефон справа."""
    return normalize_layers([
        {"id": "title", "type": "text", "start": 0, "end": 5, "x": 0.3, "y": 0.45,
         "props": {"text": "Всё в одном месте", "size": 90, "maxWidth": 900}},
        {"id": "phone", "type": "shape", "start": 0, "end": 5, "x": 0.72, "y": 0.5,
         "props": {"kind": "rect", "w": 420, "h": 900, "fill": "#222222"}},
    ])


def test_reframe_16x9_to_9x16_fits_width():
    out, _cam = reframe_between(_landscape_layers(), [], "16:9", "9:16")
    for layer in out:
        b = _box(layer, 1080, 1920)
        assert b[0] >= -1 and b[2] <= 1081 and b[1] >= 0 and b[3] <= 1920, (layer["id"], b)
    # Тот же формат — как есть.
    layers = _landscape_layers()
    assert reframe_between(layers, [], "16:9", "16:9")[0] is layers


# ---------------------------------------------------------------- весь рендер: 16:9


def test_render_pipeline_16x9_comp_and_base(tmp_path, monkeypatch):
    got = run_case("crop_focus_motion", tmp_path, monkeypatch, extra={"format": "16:9"}, frame=(1920, 1080))
    assert got["status"] == "succeeded", got.get("error")
    base = got["calls"][0][1]
    graph = base[base.index("-filter_complex") + 1]
    assert "crop=1920:1080" in graph
    comp = next(c[1]["comp"] for c in got["calls"] if c[0] == "compositor")
    assert (comp["width"], comp["height"], comp["refWidth"], comp["refHeight"]) == (1920, 1080, 1920, 1080)
    hook = next(layer for layer in comp["layers"] if layer["id"] == "hook")
    assert hook["scale"] < 1  # перенесён из 9:16 одним масштабом
    frame_comp = next(c[1]["comp"] for c in got["frame_calls"] if c[0] == "compositor")
    assert (frame_comp["width"], frame_comp["height"], frame_comp["refWidth"]) == (1920, 1080, 1920)
    assert frame_comp["layers"] == comp["layers"]  # кадр агента — тот же перенос, что рендер


def test_render_pipeline_layers_in_16x9_render_vertical(tmp_path, monkeypatch):
    """Слои размечены в 16:9, а рендер 9:16 — перекомпоновка 16:9 → 9:16, без refWidth."""
    got = run_case("crop_focus_motion", tmp_path, monkeypatch, extra={"layers_format": "16:9"})
    comp = next(c[1]["comp"] for c in got["calls"] if c[0] == "compositor")
    assert (comp["width"], comp["height"]) == (1080, 1920) and "refWidth" not in comp
    hook = next(layer for layer in comp["layers"] if layer["id"] == "hook")
    assert hook["scale"] < 1


def test_render_settings_patch_keeps_format(tmp_path, monkeypatch):
    """Редактор, что не шлёт format/layers_format, их не сбрасывает; старый aspect — формат."""
    from test_api_pipeline import _client

    client, store = _client(tmp_path, monkeypatch)
    from app.settings import settings

    path = settings.source_dir / "src.mp4"
    path.write_bytes(b"x")
    source = store.create_source("upload", path, duration_sec=60, width=1920, height=1080, status="ready")
    from app.manual_clips import create_manual_clip

    plan = create_manual_clip(store, source["id"], "клип", [{"start_sec": 0.0, "end_sec": 8.0}], "")
    store.set_clip_plan_render_settings(plan["id"], {"format": "16:9", "layers_format": "4:5"})
    url = f"/api/clip-plans/{plan['id']}/render-settings"
    rs = client.patch(url, json={"settings": {"mirror": True}}).json()["render_settings"]
    assert (rs["format"], rs["layers_format"], rs["mirror"]) == ("16:9", "4:5", True)
    rs = client.patch(url, json={"settings": {"aspect": "1:1"}}).json()["render_settings"]
    assert (rs["format"], rs["layers_format"]) == ("1:1", "4:5")
    view = store.get_clip_plan(plan["id"])["render_settings"]
    assert view["format"] == "1:1" and "aspect" not in view


def test_agent_set_format_and_get_clip(tmp_path, monkeypatch):
    import json as _json

    from app.agent_tools import ToolContext, run_tool
    from app.manual_clips import create_manual_clip
    from app.settings import settings
    from test_render import _store

    store = _store(tmp_path, monkeypatch)
    path = settings.source_dir / "wide.mp4"
    path.write_bytes(b"x")
    source = store.create_source("upload", path, duration_sec=180, width=1920, height=1080, status="ready")
    plan = create_manual_clip(store, source["id"], "клип", [{"start_sec": 0.0, "end_sec": 12.0}], "")
    ctx = ToolContext(store=store, clip_plan_id=plan["id"])
    out, ok = run_tool(ctx, "set_format", {"format": "16:9"})
    assert ok and "1920×1080" in str(out), out
    out, ok = run_tool(ctx, "get_clip", {})
    assert ok, out
    text = out if isinstance(out, str) else _json.dumps(out, ensure_ascii=False)
    assert '"format": "16:9"' in text and '"frame": [1920, 1080]' in text and '"layers_format": "9:16"' in text
