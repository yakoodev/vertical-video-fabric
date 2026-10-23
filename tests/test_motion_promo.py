"""Промо приложения целиком в сервисе (клип 574): пределы, частицы, символы-декор, стоп-кадр видео,
строка статуса телефона, затемнение за текстом и перегруз на холсте."""

from __future__ import annotations

import json

import pytest

from app import agent_tools, motion
from app.motion_render import video_frame_plan

PHONE = {"id": "rec", "type": "video", "start": 0, "end": 4, "props": {"asset_id": 7, "w": 600, "device": "phone"}}


# ---------------------------------------------------------------- пределы


def test_limits_raised_and_overflow_is_loud():
    assert (motion.MAX_LAYERS, motion.MAX_CAMERA_KEYS, motion.MAX_KEYS_PER_PROP) == (200, 120, 200)
    layers = [{"type": "text", "start": 0, "end": 1, "props": {"text": f"т{i}"}} for i in range(113)]
    assert len(motion.normalize_layers(layers)) == 113  # промо 574 влезает целиком
    over = layers * 2  # 226
    problems = motion.validate_layers(over)
    assert any("слоёв 226, предел 200" in p and "layers[200] по layers[225]" in p and "НЕ попадут" in p
               for p in problems)
    camera = [{"t": i * 0.1} for i in range(125)]
    assert len(motion.normalize_camera(camera)) == 120
    assert any("ключей 125, предел 120" in p for p in motion.validate_camera(camera))
    keys = {"x": [{"t": i * 0.01, "v": 0.5} for i in range(205)]}
    [layer] = motion.normalize_layers([{"type": "text", "props": {"text": "x"}, "keys": keys}])
    assert len(layer["keys"]["x"]) == 200
    assert any("точек 205, предел 200" in p for p in motion.validate_layers([{"type": "text", "props": {"text": "x"},
                                                                             "keys": keys}]))
    assert motion.schema_summary()["limits"]["max_layers"] == 200


def test_add_layers_refuses_past_the_limit_instead_of_dropping(tmp_path, monkeypatch):
    from test_agent_tools import _ctx
    from test_api_pipeline import _require_ffmpeg
    from test_clip_spec import _setup

    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    ctx = _ctx(store, plan["id"])
    base = [{"id": f"s{i}", "type": "shape", "start": 0, "end": 1, "props": {"kind": "rect", "w": 10, "h": 10}}
            for i in range(motion.MAX_LAYERS - 1)]
    store.set_clip_plan_render_settings(plan["id"], {"layers": base})
    one = {"type": "text", "start": 0, "end": 1, "y": 0.4, "props": {"text": "ПОСЛЕДНИЙ"}}
    res, ok = agent_tools.run_tool(ctx, "add_layers", {"layers": [one, {**one, "props": {"text": "ЛИШНИЙ"}}]})
    assert not ok and "предел 200" in res["error"] and "ещё 1" in res["error"]
    assert any("particles" in p for p in res["problems"])
    assert len(store.get_clip_plan(plan["id"])["render_settings"]["layers"]) == 199  # ничего не потеряно
    res, ok = agent_tools.run_tool(ctx, "add_layers", {"layers": [one]})
    assert ok and res["layers_total"] == 200 and res["layers_limit"] == 200
    assert any("осталось 0" in w for w in res["warnings"])


# ---------------------------------------------------------------- частицы


def test_particles_layer_normalizes_and_validates():
    [layer] = motion.normalize_layers([{"type": "particles", "start": 1, "end": 3,
                                        "props": {"kind": "confetti", "count": 120, "colors": ["#FF2D55", "#fff"],
                                                  "gravity": 700, "seed": 42}}])
    props = layer["props"]
    assert props["kind"] == "confetti" and props["count"] == 120 and props["colors"] == ["#ff2d55", "#ffffff"]
    assert props["seed"] == 42 and props["gravity"] == 700 and props["repeat"] is False
    assert "duration" not in props and "shape" not in props  # умолчания по виду — у движка
    problems = motion.validate_layers([{"type": "particles", "props": {"count": 500, "kind": "fireworks",
                                                                       "shape": "blob"}}])
    assert any("count: 500 вне допустимого 1..200" in p for p in problems)
    assert any("fireworks" in p for p in problems) and any("blob" in p for p in problems)
    # Картинка вместо фигуры — файл для монтажа или пакет; это ресурс слоя для рендера.
    [pic] = motion.normalize_layers([{"type": "particles", "props": {"asset_id": 9}}])
    assert motion.layer_asset_refs(pic) == [9]
    [heart] = motion.normalize_layers([{"type": "particles", "props": {"src": "sticker:hearts/a.png"}}])
    assert motion.layer_asset_refs(heart) == ["sticker:hearts/a.png"]
    schema = motion.schema_summary()
    assert "particles" in schema["types"] and schema["particles"]["max_count"] == 200
    assert "star4" in schema["props"]["shape"]["kind"]["options"]
    json.dumps(schema, ensure_ascii=False)


def test_particles_are_decor_for_lint_and_agent_reference():
    ref = agent_tools.motion_reference()
    assert "particles:" in ref and "burst" in ref and "ПРЕДЕЛЫ" in ref and "≤ 200" in ref
    assert "hold" in ref and "statusBar" in ref and "backdrop" in ref and "✦" in ref
    summary = agent_tools._layer_summary({"type": "particles", "start": 2, "props": {"kind": "sparkle", "count": 80}})
    assert summary == "частицы sparkle ×80 на 2.0 с"


# ---------------------------------------------------------------- перегруз


def _text(i, **over):
    return {"id": f"t{i}", "type": "text", "start": 0, "end": 3, "y": 0.3 + i * 0.05,
            "props": {"text": f"ТИТР {i}", "size": 80}, **over}


def test_overload_ignores_decor_and_canvas_backgrounds():
    titles = [_text(i) for i in range(3)]
    over = lambda ws: [w for w in ws if "перегруз" in w]  # noqa: E731
    assert not over(agent_tools.lint_layers(titles, 10))
    decor = [
        {"id": "p1", "type": "particles", "start": 0, "end": 3, "props": {"kind": "sparkle"}},
        {"id": "bg", "type": "image", "start": 0, "end": 3, "behind": True, "props": {"asset_id": 1, "w": 1080}},
        {"id": "fade", "type": "image", "start": 0, "end": 3, "opacity": 0.3, "props": {"asset_id": 2, "w": 1080}},
    ]
    assert not over(agent_tools.lint_layers(titles + decor, 10))
    # Непрозрачная картинка во всю ширину — на обычном клипе это графика, на холсте — фон промо.
    wall = {"id": "wall", "type": "image", "start": 0, "end": 3, "props": {"asset_id": 3, "w": 1080}}
    assert over(agent_tools.lint_layers(titles + [wall], 10))
    assert not over(agent_tools.lint_layers(titles + [wall], 10, canvas=True))
    # Четвёртый титр — перегруз и на холсте.
    assert over(agent_tools.lint_layers(titles + [_text(3)], 10, canvas=True))
    assert agent_tools.is_decor_layer({"type": "particles"}) and not agent_tools.is_decor_layer(_text(0))


# ---------------------------------------------------------------- символы-декор и эмодзи


def test_strip_emoji_keeps_vector_decor_glyphs():
    layer = {"type": "text", "props": {"text": "✦ Новый уровень ✦ ★ 🔥 ♥️ → ✓"}}
    assert agent_tools._strip_emoji(layer)["props"]["text"] == "✦ Новый уровень ✦ ★  ♥ → ✓"
    for ch in "✦✧★☆♥→•":
        assert ch in motion.DECOR_GLYPHS
    parts = {"type": "particles", "props": {"src": "emoji:2728", "count": 10}}
    assert "src" not in agent_tools._strip_emoji(parts)["props"]


# ---------------------------------------------------------------- текст: затемнение


def test_text_backdrop_shorthand_and_object():
    [on] = motion.normalize_layers([{"type": "text", "props": {"text": "x", "backdrop": True}}])
    assert on["props"]["backdrop"] == {"color": "#000000", "opacity": 0.55}
    [custom] = motion.normalize_layers([{"type": "text", "props": {"text": "x",
                                                                   "backdrop": {"opacity": 0.8, "spread": 140}}}])
    assert custom["props"]["backdrop"] == {"color": "#000000", "opacity": 0.8, "spread": 140}
    [off] = motion.normalize_layers([{"type": "text", "props": {"text": "x", "backdrop": False}}])
    assert off["props"]["backdrop"] is None
    [old] = motion.normalize_layers([{"type": "text", "props": {"text": "x"}}])
    assert "backdrop" not in old["props"]  # старые слои не меняются


# ---------------------------------------------------------------- телефон: строка статуса, блик


def test_phone_status_bar_and_glare_props():
    norm = lambda **p: motion.normalize_layers([{**PHONE, "props": {**PHONE["props"], **p}}])[0]["props"]  # noqa: E731
    assert norm(statusBar=True)["statusBar"] == motion.STATUS_BAR_AUTO == 0.14
    assert norm(statusBar="auto")["statusBar"] == 0.14
    assert norm(statusBar=False)["statusBar"] == 0
    assert norm(statusBar=0.9)["statusBar"] == 0.4
    assert norm(statusBar=0.1, statusBarColor="#FFF", screenGlare=True) == {
        **norm(), "statusBar": 0.1, "statusBarColor": "#ffffff", "screenGlare": True}
    assert not {"statusBar", "statusBarColor", "screenGlare", "island"} & set(norm())  # по умолчанию их нет
    # Высота слоя для безопасных зон растёт на полосу статуса.
    base = agent_tools.video_height(PHONE["props"])
    assert agent_tools.video_height({**PHONE["props"], "statusBar": True}) == pytest.approx(base + 600 * 0.14)


# ---------------------------------------------------------------- видео: стоп-кадр


def test_video_hold_freezes_from_and_cuts_one_frame():
    t = motion.video_file_time
    assert t({"from": 3.2, "hold": True}, 0, 10) == 3.2
    assert t({"from": 3.2, "hold": True, "speed": 4, "loop": True}, 7.5, 10) == 3.2
    assert t({"from": 30, "hold": True}, 1, 10) == pytest.approx(10 - motion.VIDEO_TAIL)
    assert t({"from": 30, "hold": True}, 1, 0) == 30  # длина файла неизвестна
    [layer] = motion.normalize_layers([{**PHONE, "props": {**PHONE["props"], "from": 3.2, "hold": True}}])
    assert layer["props"]["hold"] is True
    plan = video_frame_plan(layer, 30, file_duration=60)
    assert plan == {"start": 3.2, "rate": None, "span": None, "count": 1}  # один кадр, не нарезка
    late = video_frame_plan({**layer, "props": {**layer["props"], "from": 99}}, 30, file_duration=8)
    assert late["count"] == 1 and late["start"] == pytest.approx(7.75)
    assert video_frame_plan(layer, 30, file_duration=60, at=2.0)["start"] == 3.2
    [old] = motion.normalize_layers([PHONE])
    assert "hold" not in old["props"]
