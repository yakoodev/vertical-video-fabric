import json

from app.agent_tools import covering_problems
from app.chroma_key import _screen_color, despill_type, normalize_backdrop, normalize_key
from app.clip_settings import normalize_render_settings
from app.motion import normalize_layers
from app.motion_render import write_comp
from app.render import _base_crop, _keyed_render, _render_filter
from app.stream_layout import normalize_layout


def test_normalize_key_clamps_and_rejects_junk():
    assert normalize_key({"color": "#00FE00", "similarity": 5, "blend": -1}) == {
        "color": "#00fe00", "similarity": 0.6, "blend": 0.0,
    }
    assert normalize_key({"color": "green"}) is None
    assert normalize_key({"color": "#00ff00", "off": True}) is None
    assert normalize_key(None) is None


def test_screen_color_needs_mostly_green_border():
    green = [(0, 254, 0)] * 90 + [(200, 200, 200)] * 10
    assert _screen_color(green) == "#00fe00"
    assert _screen_color([(0, 254, 0)] * 40 + [(90, 60, 40)] * 60) is None
    assert _screen_color([(10, 40, 230)] * 100) == "#0a28e6"
    assert despill_type("#0a28e6") == "blue"
    assert despill_type("#00fe00") == "green"


def test_backdrop_is_part_of_render_settings():
    assert normalize_backdrop({"color": "#FF0000", "color2": "bad"}) == {"color": "#ff0000", "color2": ""}
    assert normalize_render_settings({})["backdrop"] == {"color": "#0b0d14", "color2": ""}


def test_keyed_base_keeps_green_and_moves_look_to_compositor():
    preset = {"mirror": True, "color_style": "warm", "vignette": 0.5, "grain": 0.2,
              "layout": normalize_layout({"mode": "meme", "bg": "#123456"})}
    key = {"color": "#00fe00", "similarity": 0.25, "blend": 0.08}
    base, motion = _keyed_render(preset, {"layers": [], "camera": [], "key": key})
    assert base["color_style"] == "none" and not base["mirror"] and base["vignette"] == 0 and base["grain"] == 0
    assert base["layout"]["bg"] == "#00fe00"
    assert motion["key"]["grade"].startswith("hflip,colorbalance")
    assert "vignette" not in motion["key"]["grade"]
    # Без ключа — ровно прежний пресет.
    assert _keyed_render(preset, {"layers": []})[0] is preset


def test_keyed_source_trims_dark_edges_unless_cropped():
    assert _base_crop({"content_crop": None}, {"key": {"color": "#00fe00"}}) == {"x": 0.005, "y": 0.005, "w": 0.99, "h": 0.99}
    assert _base_crop({"content_crop": None}, {"layers": []}) is None
    crop = {"x": 0.1, "y": 0, "w": 0.8, "h": 1}
    assert _base_crop({"content_crop": crop}, {"key": {"color": "#00fe00"}}) is crop


def test_meme_video_scale_centers_wider_video():
    preset = {"layout": normalize_layout({"mode": "meme", "video_scale": 2.6, "video_y": 0.18})}
    graph = _render_filter(1080, 1920, 30, preset, None)
    assert "scale=2808:-2" in graph
    assert "overlay=x=-864:y=346" in graph
    assert normalize_layout({"mode": "meme", "video_scale": 9})["video_scale"] == 4.0


def test_contain_pad_uses_key_color():
    graph = _render_filter(1080, 1920, 30, {"scale_mode": "contain", "key_fill": "#00fe00"}, None)
    assert "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:0x00fe00" in graph


def test_comp_carries_key_and_backdrop(tmp_path):
    path = write_comp(
        tmp_path / "comp.json", width=1080, height=1920, fps=30, duration=2, layers=[], camera=[],
        key={"color": "#00fe00", "similarity": 0.3, "blend": 0.1, "grade": "eq=saturation=1.2"},
        background={"color": "#112233", "color2": "#445566"},
    )
    comp = json.loads(path.read_text(encoding="utf-8"))
    assert comp["key"] == {"color": "0x00fe00", "similarity": 0.3, "blend": 0.1, "despill": "green",
                           "grade": "eq=saturation=1.2"}
    assert comp["background"] == {"color": "#112233", "color2": "#445566"}


def test_behind_layers_only_cover_the_backdrop():
    big = {"id": "bg", "type": "image", "start": 0, "end": 2, "props": {"w": 1080, "h": 1920}}
    assert covering_problems([big])  # поверх видео — закрывает
    assert covering_problems([{**big, "behind": True}], keyed=True) == []
    assert "хромакеем" in covering_problems([{**big, "behind": True}], keyed=False)[0]
    assert normalize_layers([{**big, "behind": True}])[0]["behind"] is True
