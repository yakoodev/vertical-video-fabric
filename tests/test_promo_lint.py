"""Линт промо (app/promo_lint.py) на реальном примере — клип 575 из проверочного прогона Gemini:
заголовки шагов на экране телефона, логотип поверх не ушедшего телефона, бледная ссылка в
финале, хук на все 18 с. Исправленная по чек-листу версия — без предупреждений."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from app import motion, promo_lint
from app.agent_tools import lint_layers

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "clip575_promo.json").read_text(encoding="utf-8"))
SIZES = {k: tuple(v) for k, v in FIXTURE["asset_sizes"].items()}


def _layers(raw=None):
    return motion.normalize_layers(copy.deepcopy(raw if raw is not None else FIXTURE["render"]["layers"]))


def _lint(layers, canvas=True, **kw):
    render = FIXTURE["render"]
    return lint_layers(layers, FIXTURE["duration"], canvas=canvas, backdrop=render["backdrop"],
                       camera=kw.pop("camera", render["camera"]), sizes=kw.pop("sizes", SIZES), **kw)


def _by_id(layers):
    return {l["id"]: l for l in layers}


def test_clip_575_warnings():
    warnings = _lint(_layers())
    text = "\n".join(warnings)
    # а) заголовки шагов на экране телефона — с советом, куда поднять
    for lid in ("l_a7ed49", "l_115f49", "l_2778ef"):
        assert any(w.startswith(f"{lid}: титр") and "на экране телефона" in w and "y ≤ 0.17" in w
                   and "зазор ≥ 24 px" in w for w in warnings), text
    # на наезде телефона (scale 1.2) над ним места нет; хук с затемнением (backdrop) телефон
    # накрывает только на наезде — это разрешено (за хук отвечает правило «висит долго»)
    assert any(w.startswith("l_630888: титр") and "места нет" in w for w in warnings), text
    assert not any(w.startswith("l_b9e706: титр") for w in warnings), text
    # в) логотип влетел поверх не ушедшего телефона
    assert any(w.startswith("l_15ad17 и l_e36211:") and "без разведения" in w for w in warnings), text
    # г) ссылка лавандовая на фоне с лавандовыми пятнами
    assert any(w.startswith("l_1af6db:") and "#bda7f7" in w and "сольётся" in w for w in warnings), text
    # хук висит 18.5 с поверх смен сцен
    assert any(w.startswith("l_b9e706: хук") and "18.5 с" in w and "2–4 с" in w for w in warnings), text
    # телефон на холсте — сцена, а не «графика поверх»: перегруза из-за двух телефонов на смене нет
    assert not any("перегруз" in w for w in warnings), text
    # въезд/выезд телефонов (≤ 0.5 с у краёв слоя) — намеренный, за кадр не считается
    assert not any("за краем кадра" in w for w in warnings), text


def _fixed_575():
    """Клип 575 по чек-листу: хук 3 с, заголовки над телефоном, телефон ниже и уходит до логотипа,
    наезд без вылета, ссылка крупная и белая на плашке."""
    layers = _by_id(_layers())
    layers["l_b9e706"]["end"] = 3.0
    for lid in ("l_a7ed49", "l_115f49", "l_2778ef"):
        layers[lid]["y"] = 0.12
    layers["l_630888"]["y"] = 0.14  # две строки
    layers["l_a7ed49"]["start"] = 3.0
    for lid in ("l_aef445", "l_6ca6c3", "l_2a2b06", "l_9d83e1", "l_15ad17"):
        layers[lid]["y"] = 0.6
    # наезд — не больше 1.08: корпус остаётся в кадре и под заголовком
    layers["l_9d83e1"]["keys"]["scale"][-1]["v"] = 1.08
    # телефон уходит к 18.4 с, логотип входит в 18.5 с
    phone = layers["l_15ad17"]
    phone["end"] = 18.4
    phone["keys"]["x"] = [{"t": 0.0, "v": 1.5}, {"t": 0.4, "v": 0.5, "ease": "outQuad"},
                          {"t": 3.9, "v": 0.5, "ease": "inQuad"}, {"t": 4.3, "v": -0.5}]
    link = layers["l_1af6db"]["props"]
    link.update({"color": "#ffffff", "size": 56, "bg": {"color": "#ff6a1f", "opacity": 1, "radius": 24,
                                                         "padX": 28, "padY": 14}})
    link["text"] = "t.me/Collabikabot"
    return list(layers.values())


def test_fixed_575_has_no_promo_warnings():
    warnings = _lint(_fixed_575())
    promo = [w for w in warnings if any(k in w for k in ("экране телефона", "за краем", "без разведения",
                                                         "в финале", "хук «"))]
    assert promo == [], "\n".join(warnings)


def test_zoom_off_frame_but_not_entries_and_fades():
    base = {"id": "ph", "type": "video", "start": 0, "end": 6, "x": 0.5, "y": 0.6,
            "props": {"asset_id": 1, "w": 580, "device": "phone"}}
    ok = _layers([{**base, "keys": {"x": [{"t": 0, "v": 1.5}, {"t": 0.4, "v": 0.5}, {"t": 5.6, "v": 0.5},
                                          {"t": 6.0, "v": -0.5}]}}])
    assert not any("за краем" in w for w in _lint(ok, sizes={}))
    zoom = _layers([{**base, "keys": {"scale": [{"t": 1, "v": 1.0}, {"t": 2, "v": 1.6}]}}])
    warn = [w for w in _lint(zoom, sizes={}) if "за краем" in w]
    assert len(warn) == 1 and warn[0].startswith("ph: на ") and "scale 1.60" in warn[0], warn
    # растворяется, уезжая, — намеренно
    fade = _layers([{**base, "keys": {"y": [{"t": 1, "v": 0.6}, {"t": 2, "v": 1.4}],
                                      "opacity": [{"t": 1, "v": 1}, {"t": 1.6, "v": 0}]}}])
    assert not any("за краем" in w for w in _lint(fade, sizes={}))


def test_follow_camera_zoom_counts_for_off_frame():
    phone = {"id": "ph", "type": "video", "start": 0, "end": 6, "x": 0.3, "y": 0.6, "followCamera": True,
             "props": {"asset_id": 1, "w": 520, "device": "phone"}}
    camera = [{"t": 0, "zoom": 1}, {"t": 2, "zoom": 1.8, "x": 0.5, "y": 0.5}]
    layers = _layers([phone])
    assert not any("за краем" in w for w in _lint(layers, camera=[], sizes={}))
    assert any("за краем" in w for w in _lint(layers, camera=motion.normalize_camera(camera), sizes={}))


def test_title_over_zoomed_phone_allowed_only_with_backdrop():
    phone = {"id": "ph", "type": "video", "start": 0, "end": 6, "y": 0.6, "followCamera": True,
             "props": {"asset_id": 1, "w": 580, "device": "phone"}}
    title = {"id": "t", "type": "text", "start": 0, "end": 6, "y": 0.13, "props": {"text": "ЖМИ И ЗОВИ", "size": 60}}
    camera = motion.normalize_camera([{"t": 2, "zoom": 1}, {"t": 3, "zoom": 1.3, "x": 0.5, "y": 0.55}])
    plain = _lint(_layers([phone, title]), camera=camera, sizes={})
    assert any(w.startswith("t: титр") and "на экране телефона ph" in w for w in plain), plain
    shaded = {**title, "props": {**title["props"], "backdrop": True}}
    assert not any(w.startswith("t: титр") for w in _lint(_layers([phone, shaded]), camera=camera, sizes={}))
    # без наезда титр над телефоном и так не на экране
    assert not any(w.startswith("t: титр") for w in _lint(_layers([phone, title]), camera=[], sizes={}))


def test_promo_rules_quiet_on_stream_clips_and_only_ids():
    """Без холста и телефона правила промо молчат; only_ids — про свои слои и пары с ними."""
    plain = [l for l in _layers() if not promo_lint.is_phone(l)]
    assert promo_lint.promo_warnings(plain, 24.0) == []
    layers = _layers()
    only_logo = promo_lint.promo_warnings(layers, 24.0, {"l_e36211"}, canvas=True,
                                          backdrop=FIXTURE["render"]["backdrop"], sizes=SIZES)
    assert len(only_logo) == 1 and only_logo[0].startswith("l_15ad17 и l_e36211:")


def test_final_text_size_and_contrast():
    dark = {"kind": "aurora", "colors": ["#0b1018", "#ff6a1f", "#bda7f7"]}

    def text(**props):
        return {"id": "cta", "type": "text", "start": 20, "end": 24, "y": 0.7,
                "props": {"text": "t.me/bot", "size": 56, **props}}

    def run(layer):
        return [w for _ids, w in promo_lint.final_text(_layers([layer]), 24.0, dark)]

    assert run(text(color="#ffffff")) == []
    assert any("шрифт 30px" in w for w in run(text(color="#ffffff", size=30)))
    assert any("контраст" in w for w in run(text(color="#2a2f3a")))  # тёмный на тёмном
    assert any("сольётся" in w for w in run(text(color="#bda7f7")))
    assert run(text(color="#bda7f7", bg={"color": "#0b1018", "opacity": 1})) == []  # плашка спасает
    assert any("на плашке" in w for w in run(text(color="#ffffff", bg={"color": "#f2f2f2", "opacity": 1})))
    assert run(text(color="#2a2f3a", stroke={"color": "#ffffff", "width": 6})) == []
    assert run({**text(color="#2a2f3a"), "start": 2, "end": 6}) == []  # не финал
    assert promo_lint.contrast("#ffffff", "#0b1018") > 15


def test_add_layers_on_canvas_warns_about_title_over_phone(tmp_path, monkeypatch):
    from app.agent_tools import ToolContext, run_tool
    from test_canvas import _client
    from test_api_pipeline import _require_ffmpeg

    _require_ffmpeg()
    import app.canvas as canvas

    monkeypatch.setattr(canvas, "CANVAS_WIDTH", 108)
    monkeypatch.setattr(canvas, "CANVAS_HEIGHT", 192)
    _c, store = _client(tmp_path, monkeypatch)
    out, ok = run_tool(ToolContext(store=store), "create_canvas_clip", {"duration": 10, "background": "aurora"})
    assert ok, out
    ctx = ToolContext(store=store, clip_plan_id=out["clip_id"], on_first_change=lambda: None)
    phone = {"id": "ph", "type": "video", "start": 0, "end": 6, "y": 0.57,
             "props": {"asset_id": "sfx:none", "w": 580, "device": "phone"}}
    store.set_clip_plan_render_settings(out["clip_id"], {**store.get_clip_plan(out["clip_id"])["render_settings"],
                                                         "layers": motion.normalize_layers([phone])})
    title = {"type": "text", "start": 0.5, "end": 3.5, "y": 0.28, "props": {"text": "ЛИСТАЙ ЛЕНТУ", "size": 55}}
    res, ok = run_tool(ctx, "add_layers", {"layers": [title]})
    assert ok, res
    assert any("на экране телефона ph" in w for w in res["warnings"]), res["warnings"]
