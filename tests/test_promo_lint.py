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


def test_title_over_zoomed_phone_allowed_only_with_backdrop_on_scale_push():
    phone = {"id": "ph", "type": "video", "start": 0, "end": 6, "y": 0.6, "followCamera": True,
             "props": {"asset_id": 1, "w": 580, "device": "phone"}}
    title = {"id": "t", "type": "text", "start": 0, "end": 6, "y": 0.13, "props": {"text": "ЖМИ И ЗОВИ", "size": 60}}
    camera = motion.normalize_camera([{"t": 2, "zoom": 1}, {"t": 3, "zoom": 1.3, "x": 0.5, "y": 0.55}])
    plain = _lint(_layers([phone, title]), camera=camera, sizes={})
    assert any(w.startswith("t: титр") and "на экране телефона ph" in w for w in plain), plain
    # наезд камерой (followCamera) — и с затемнением титра: центр камеры можно опустить (прогон 581)
    shaded = {**title, "props": {**title["props"], "backdrop": True}}
    warned = [w for w in _lint(_layers([phone, shaded]), camera=camera, sizes={}) if w.startswith("t: титр")]
    assert warned and "наезде камеры" in warned[0] and "уменьши y камеры" in warned[0], warned
    # наезд ключом scale самого телефона — титр с затемнением может лечь поверх, так задумано
    pushed = {**phone, "followCamera": False, "keys": {"scale": [{"t": 2, "v": 1.0}, {"t": 3, "v": 1.5}]}}
    assert any(w.startswith("t: титр") for w in _lint(_layers([pushed, title]), camera=[], sizes={}))
    assert not any(w.startswith("t: титр") for w in _lint(_layers([pushed, shaded]), camera=[], sizes={}))
    # без наезда титр над телефоном и так не на экране
    assert not any(w.startswith("t: титр") for w in _lint(_layers([phone, title]), camera=[], sizes={}))


def test_record_window_over_own_phone_is_not_a_scene_overlap():
    phone = {"id": "ph", "type": "video", "start": 0, "end": 6, "y": 0.6,
             "props": {"asset_id": 5, "w": 520, "device": "phone"}}
    window = {"id": "win", "type": "video", "start": 3, "end": 6, "y": 0.55,
              "props": {"asset_id": 5, "w": 900, "crop": {"x": 0.04, "y": 0.3, "w": 0.92, "h": 0.3}}}
    assert not any("без разведения" in w for w in _lint(_layers([phone, window]), camera=[], sizes={}))
    # другая запись или картинка без crop поверх телефона — новая сцена внахлёст
    other = {**window, "props": {**window["props"], "asset_id": 6}}
    assert any("без разведения" in w for w in _lint(_layers([phone, other]), camera=[], sizes={}))


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
    # с 6 с на холсте один фон и финала нет — про весь клип, хоть новый слой и не про это
    assert "пустой кадр 6.0–10.0 с: нет слоёв — проверь, применились ли последние пачки" in res["warnings"]
    assert any(w.startswith("нет финала") for w in res["warnings"]), res["warnings"]
    clip, ok = run_tool(ctx, "get_clip", {})
    assert ok and any(w.startswith("пустой кадр 6.0–10.0 с") for w in clip["warnings"]), clip["warnings"]
    cta = {"type": "text", "start": 5.8, "end": 10, "y": 0.5, "props": {"text": "t.me/Collabikabot", "size": 60}}
    res, ok = run_tool(ctx, "add_layers", {"layers": [cta]})
    assert ok and not any(w.startswith(("пустой кадр", "нет финала")) for w in res["warnings"]), res["warnings"]


# ---------------------------------------------------------------- прогон 578: пустой финал, «\N» в титрах

FIXTURE_578 = json.loads((Path(__file__).parent / "fixtures" / "clip578_promo.json").read_text(encoding="utf-8"))


def _lint_578(layers):
    render = FIXTURE_578["render"]
    return lint_layers(layers, FIXTURE_578["duration"], canvas=True, backdrop=render["backdrop"],
                       camera=render["camera"])


def test_clip_578_titles_get_real_line_breaks():
    """Gemini писал перенос как "\\\\n" — в строке обратный слэш + n, капс в кадре давал «\\N»."""
    raw = FIXTURE_578["render"]["layers"]
    assert "\\n" in next(l for l in raw if l["id"] == "t1")["props"]["text"]
    texts = {l["id"]: l["props"]["text"] for l in motion.normalize_layers(copy.deepcopy(raw)) if l["type"] == "text"}
    assert texts["t1"] == "НАЙДИ НАПАРНИКА\nДЛЯ КОЛЛАБА"
    assert all("\\" not in t for t in texts.values()), texts


def test_unescape_text_fields_of_all_text_layers():
    assert motion.unescape_text("А\\\\nБ\\NВ\\r\\nГ\\tД") == "А\nБ\nВ\nГ Д"
    assert motion.unescape_text("без слэшей\nи так") == "без слэшей\nи так"
    layers = motion.normalize_layers([
        {"type": "counter", "props": {"prefix": "от\\n", "suffix": "\\t₽"}},
        {"type": "callout", "props": {"title": "ШАГ 1\\nВХОД", "subtitle": "a\\\\nb"}},
        {"type": "chart", "props": {"title": "Рост\\nза год", "data": [{"label": "Q1\\n2025", "value": 1}]}},
    ])
    assert layers[0]["props"]["prefix"] == "от\n" and layers[0]["props"]["suffix"] == " ₽"
    assert (layers[1]["props"]["title"], layers[1]["props"]["subtitle"]) == ("ШАГ 1\nВХОД", "a\nb")
    assert layers[2]["props"]["title"] == "Рост\nза год" and layers[2]["props"]["data"][0]["label"] == "Q1\n2025"


def test_clip_578_empty_tail_and_no_final_are_warned():
    warnings = _lint_578(motion.normalize_layers(copy.deepcopy(FIXTURE_578["render"]["layers"])))
    text = "\n".join(warnings)
    # титр t6 гаснет (out fade 0.2) к 19.9 с, телефон v5 уехал вниз — дальше один фон до 24 с
    assert "пустой кадр 19.9–24.0 с: нет слоёв — проверь, применились ли последние пачки" in warnings, text
    assert any(w.startswith("нет финала: в последних 4 с (20.0–24.0 с)") for w in warnings), text


def test_clip_578_with_the_lost_batch_has_no_gap():
    """Пачка финала без битого звука закрывает и дыру, и финал."""
    batch = [l for l in FIXTURE_578["failed_batch"] if "success" not in str(l["props"].get("asset_id"))]
    layers = motion.normalize_layers(copy.deepcopy(FIXTURE_578["render"]["layers"] + batch))
    warnings = _lint_578(layers)
    assert not any(w.startswith(("пустой кадр", "нет финала")) for w in warnings), warnings


def test_empty_frame_respects_opacity_keys_animations_and_backdrop_layers():
    def lint(raw, duration=6.0, canvas=True):
        return promo_lint.scene_warnings(motion.normalize_layers(raw), duration, canvas=canvas)

    title = {"id": "t", "type": "text", "start": 0, "end": 6, "props": {"text": "ПРИВЕТ"}}
    assert lint([title]) == []
    assert lint([title], canvas=False) == []  # не холст — пустых кадров не ищем
    # растворился ключом opacity на 2 с — дальше пусто
    faded = {**title, "keys": {"opacity": [{"t": 1.5, "v": 1, "ease": "linear"}, {"t": 2, "v": 0}]}}
    assert [w for w in lint([faded]) if w.startswith("пустой кадр")][0].startswith("пустой кадр 1.9–6.0 с")
    # картинка во всю ширину — фон, частицы и звук кадр не наполняют
    bg = {"id": "bg", "type": "shape", "start": 0, "end": 6, "props": {"kind": "rect", "w": 1080, "h": 1920}}
    sparks = {"id": "p", "type": "particles", "start": 0, "end": 6, "props": {"kind": "sparkle"}}
    assert lint([bg, sparks, {**title, "end": 2}])[0].startswith("пустой кадр 2.0–6.0 с")
    # дыра ровно 1 с и меньше — не шумим
    assert lint([{**title, "end": 2.5}, {**title, "id": "t2", "start": 3.5}]) == []


def test_no_final_only_for_phone_promo_and_logo_or_cta_counts():
    phone = {"id": "ph", "type": "video", "start": 0, "end": 4, "y": 0.6,
             "props": {"asset_id": 1, "w": 580, "device": "phone"}}
    date = {"id": "d", "type": "text", "start": 4, "end": 8, "props": {"text": "1 НОЯБРЯ, 19:00"}}

    def finals(*raw):
        return [w for w in promo_lint.scene_warnings(motion.normalize_layers(list(raw)), 8.0) if w.startswith("нет финала")]

    assert finals(phone, date)  # дата без ссылки и логотипа — не финал
    assert finals(phone, date, {"id": "l", "type": "image", "start": 5, "end": 8, "props": {"asset_id": 7, "w": 400}}) == []
    assert finals(phone, date, {**date, "id": "c", "props": {"text": "ЖМИ НА ССЫЛКУ"}}) == []
    assert finals(phone, {"id": "c", "type": "callout", "start": 5, "end": 8,
                          "props": {"title": "Регистрация открыта"}}) == []
    assert finals(date) == []  # без телефона это не промо приложения
