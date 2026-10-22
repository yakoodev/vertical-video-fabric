"""Анимированные фоны (app/backgrounds.py): нормализация, совместимость, зоны, рендер."""

from app.backgrounds import (
    background_zones,
    default_background,
    fill_color,
    loop_seconds,
    needs_zones,
    normalize_background,
    phase,
    piece_zone,
)
from app.brand_kit import normalize_brand, resolved_backdrop
from app.render import _render_filter
from app.stream_layout import normalize_layout
from app.video_crop import normalize_fit
from test_motion import _render_fixture

AURORA = {"kind": "aurora", "colors": [], "speed": 1.0, "intensity": 0.7}


def test_default_is_animated_not_a_fill():
    assert normalize_background(None) == AURORA
    assert default_background() == AURORA
    # Старый цвет по умолчанию (фон никто не выбирал) — тоже анимированный.
    assert normalize_background({"color": "#0b0d14", "color2": ""}) == AURORA
    assert normalize_background("#0B0D14") == AURORA
    assert normalize_background({"kind": "plasma"}) == AURORA


def test_old_color_format_stays_a_fill():
    assert normalize_background({"color": "#FF0000"}) == {**AURORA, "kind": "solid", "colors": ["#ff0000"]}
    assert normalize_background({"color": "#112233", "color2": "#445566"}) == {
        **AURORA, "kind": "gradient", "colors": ["#112233", "#445566"]}
    assert normalize_background("#123456") == {**AURORA, "kind": "solid", "colors": ["#123456"]}


def test_new_format_clamps_and_filters():
    bg = normalize_background({"kind": "grid", "colors": ["#ABCDEF", "red", "#000000", "#111111", "#222222"],
                               "speed": 9, "intensity": -1})
    assert bg == {"kind": "grid", "colors": ["#abcdef", "#000000", "#111111"], "speed": 4.0, "intensity": 0.0}
    assert normalize_background("bokeh") == {**AURORA, "kind": "bokeh"}
    assert normalize_background({"kind": "solid"})["colors"] == ["#0b0d14"]
    assert normalize_background({"kind": "gradient", "colors": ["#ffffff"]})["colors"] == ["#ffffff", "#0b0d14"]
    assert fill_color({"kind": "aurora", "colors": []}) == "#0b0d14"
    assert fill_color({"kind": "grid", "colors": ["#101020", "#ff00aa"]}) == "#101020"


def test_loop_is_seamless_phase_function():
    for speed in (0.3, 1.0, 1.7, 4.0):
        bg = normalize_background({"kind": "waves", "speed": speed})
        period = loop_seconds(bg)
        for t in (0.0, 0.37, 3.21, 12.5):
            a, b = phase(bg, t), phase(bg, t + period)
            assert min(abs(a - b), 1 - abs(a - b)) < 1e-9
        assert abs(phase(bg, period / 2) - 0.5) < 1e-9


def test_layout_bg_and_fit_bg_accept_backgrounds():
    assert normalize_layout({"mode": "meme"})["bg"] == AURORA
    assert normalize_layout({"mode": "meme", "bg": "#123456"})["bg"]["kind"] == "solid"
    assert normalize_layout({"mode": "meme", "bg": {"kind": "grid"}})["bg"]["kind"] == "grid"
    assert normalize_fit({"w": 0.6, "bg": "grid"})["bg"] == {**AURORA, "kind": "grid"}
    assert normalize_fit({"w": 0.6, "bg": "#ABCDEF"})["bg"] == "#abcdef"
    assert normalize_fit({"w": 0.6, "bg": {"kind": "solid", "colors": ["#112233"]}})["bg"] == "#112233"
    assert normalize_fit({"w": 0.6, "bg": "rainbow"})["bg"] == "blur"
    assert normalize_fit(0.6)["bg"] == "blur"
    # ffmpeg-основа заливает место фона его первым цветом (компоновщик рисует поверх).
    graph = _render_filter(1080, 1920, 30, {"layout": normalize_layout({"mode": "meme", "bg": {
        "kind": "bokeh", "colors": ["#101020", "#ff00aa"]}})}, None)
    assert "color=c=0x101020" in graph
    fit = _render_filter(1080, 1920, 30, {"piece_fit": normalize_fit({"w": 0.6, "bg": "waves"})}, None)
    assert "color=c=0x0b0d14" in fit


SOURCE = {"width": 1920, "height": 1080, "content_crop": None}


def test_meme_zone_is_outside_the_video():
    layout = normalize_layout({"mode": "meme", "video_y": 0.04})
    zone = piece_zone(layout, {}, SOURCE, None, 1080, 1920)
    # Видео 1080×608 с y = 76 — дыра ужата на 2 px по внутренним краям.
    assert zone == {"bg": AURORA, "hole": {"x": 0, "y": 78, "w": 1080, "h": 604}}
    # Статичная заливка — рисует ffmpeg, зоны нет; видео во весь кадр — фона не видно.
    assert piece_zone(normalize_layout({"mode": "meme", "bg": "#123456"}), {}, SOURCE, None, 1080, 1920) is None
    tall = {"width": 1080, "height": 1920}
    assert piece_zone(normalize_layout({"mode": "meme", "video_y": 0}), {}, tall, None, 1080, 1920) is None


def test_zones_follow_pieces_and_joins():
    layout = normalize_layout({"mode": "meme"})
    parts = [
        {"start_sec": 0, "end_sec": 4},
        {"start_sec": 10, "end_sec": 13, "fit": {"w": 0.6, "bg": "grid"}},
        {"start_sec": 20, "end_sec": 25, "fit": {"w": 0.6, "bg": "blur"}},
    ]
    zones = background_zones(layout, parts, [0.4, 0.0], SOURCE, None, 1080, 1920)
    assert [(z["t0"], z["t1"], z["bg"]["kind"]) for z in zones] == [(0.0, 3.8, "aurora"), (3.8, 6.6, "grid")]
    # Кусок «шире окна»: полоса видео во всю ширину посередине.
    assert zones[1]["hole"] == {"x": 0, "y": 456, "w": 1080, "h": 1008}  # видео 1800×1012 по центру
    # Одинаковые соседние зоны сливаются, хвост покрывает стоп-кадр в конце.
    same = background_zones(layout, parts[:1] * 3, [0, 0], SOURCE, None, 1080, 1920)
    assert len(same) == 1 and same[0]["t0"] == 0 and same[0]["t1"] > 12
    assert needs_zones(layout, []) and needs_zones(None, parts)
    assert not needs_zones(normalize_layout({"mode": "meme", "bg": "#123456"}), [parts[2]])


def test_brand_backdrop_is_animated_in_brand_colors():
    brand = normalize_brand({"name": "B", "colors": {"accent": "#ff6b2c", "plate": "#101018", "highlight": "#22c55e"}})
    assert brand["backdrop"] == AURORA  # цвета [] — «цвета бренда»
    assert resolved_backdrop(brand)["colors"] == ["#101018", "#ff6b2c", "#22c55e"]
    # Старый {color, color2} бренда — тоже анимированный, его цвет — основа.
    old = normalize_brand({"name": "B", "colors": {"accent": "#ff6b2c"}, "backdrop": {"color": "#202030", "color2": "#000000"}})
    assert old["backdrop"]["kind"] == "aurora" and old["backdrop"]["colors"] == ["#202030", "#ff6b2c"]
    # Заливку бренд даёт, только если её выбрали явно.
    solid = normalize_brand({"name": "B", "backdrop": {"kind": "solid", "colors": ["#334455"]}})
    assert resolved_backdrop(solid) == {**AURORA, "kind": "solid", "colors": ["#334455"]}


def test_meme_without_layers_runs_compositor_with_zone(tmp_path, monkeypatch):
    store, service, plan, _banner, calls, comps, kwargs = _render_fixture(tmp_path, monkeypatch)
    store.set_clip_plan_render_settings(plan["id"], {"layout": {"mode": "meme", "video_y": 0.04}})
    clip = service.render_clip_plan(plan["id"], **kwargs)

    assert clip["status"] == "succeeded", clip.get("error")
    assert "compositor" in [kind for kind, _ in calls]
    zone = comps[0]["backgrounds"][0]
    assert zone["bg"]["kind"] == "aurora" and zone["t0"] == 0
    # Выход 180×320, исходник 1280×720: видео 180×102 с y = 12.
    assert zone["hole"] == {"x": 0, "y": 14, "w": 180, "h": 98}
    assert comps[0]["layers"] == [] and "key" not in comps[0]


def test_static_meme_fill_keeps_old_render(tmp_path, monkeypatch):
    store, service, plan, _banner, calls, _comps, kwargs = _render_fixture(tmp_path, monkeypatch)
    store.set_clip_plan_render_settings(plan["id"], {"layout": {"mode": "meme", "bg": "#123456"}})
    clip = service.render_clip_plan(plan["id"], **kwargs)

    assert clip["status"] == "succeeded", clip.get("error")
    assert "compositor" not in [kind for kind, _ in calls]


def test_agent_tools_take_background_objects(tmp_path, monkeypatch):
    from app.agent_tools import ToolContext, run_tool
    from test_cuts import _two_pieces

    store, _service, plan, _preset = _two_pieces(tmp_path, monkeypatch, pieces=((0, 8), (30, 38)))
    ctx = ToolContext(store=store, clip_plan_id=plan["id"])
    out, ok = run_tool(ctx, "set_backdrop", {"kind": "grid", "colors": ["#101020", "#22d3ee"], "speed": 1.5})
    assert ok and out["backdrop"] == {"kind": "grid", "colors": ["#101020", "#22d3ee"], "speed": 1.5, "intensity": 0.7}
    # Передано только то, что меняется — остальное от текущего фона.
    out, _ok = run_tool(ctx, "set_backdrop", {"intensity": 0.5})
    assert out["backdrop"]["kind"] == "grid" and out["backdrop"]["intensity"] == 0.5
    # Старый вызов color/color2 — явная заливка.
    out, _ok = run_tool(ctx, "set_backdrop", {"color": "#334455"})
    assert out["backdrop"]["kind"] == "solid" and out["backdrop"]["colors"] == ["#334455"]
    run_tool(ctx, "set_layout", {"mode": "meme", "bg": {"kind": "bokeh"}})
    assert store.get_clip_plan(plan["id"])["render_settings"]["layout"]["bg"]["kind"] == "bokeh"
    out, ok = run_tool(ctx, "set_piece_fit", {"piece": 0, "w": 0.6, "bg": "waves"})
    assert ok and out["fit"]["bg"]["kind"] == "waves", out
