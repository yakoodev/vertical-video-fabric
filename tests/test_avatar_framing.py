"""Аватар стримера вместо людей с экрана (QA 01.11, клип 889 — «в кадре только аватар Муры»).

Рамка героя поймала человека из ютуб-клипа на экране стрима, агент кадрировал на него, аватара в
ролике не было. Теперь: рамки из экранного контента помечены и не считаются героем, аватар ищет
detect_avatar (Gemini по целым кадрам исходника — здесь фейк), раскладка avatar_full ставит его на
весь кадр, set_focus мимо аватара предупреждает."""

from __future__ import annotations

import json
import subprocess

import pytest

from app import agent_tools
from app.agent_tools import ToolContext, run_tool
from app.ai import avatar_box
from app.ai.subject_box import _parse_boxes, split_screen_boxes, text_positions
from app.backgrounds import piece_zone
from app.render import _layout_filter, build_ffmpeg_render_args
from app.stream_layout import (
    avatar_auto_scale,
    avatar_geometry,
    camera_regions,
    layout_active,
    layout_for_format,
    normalize_layout,
)
from test_api_pipeline import _require_ffmpeg
from test_clip_spec import _setup

AVATAR = {"mode": "avatar_full", "cam": {"x": 0.5, "y": 0.0, "w": 0.5, "h": 1.0}}

# ------------------------------------------------------------------ раскладка avatar_full


def test_avatar_full_normalizes_and_keeps_other_modes_unchanged():
    lay = normalize_layout({**AVATAR, "avatar_y": 0.95, "bg_blur": False})
    assert lay["mode"] == "avatar_full" and layout_active(lay)
    assert lay["avatar_y"] == 0.8 and lay["bg_blur"] is False
    assert normalize_layout(AVATAR)["bg_blur"] is True and normalize_layout(AVATAR)["avatar_y"] == 0.5
    assert "avatar_y" not in normalize_layout({"mode": "meme"}) and "bg_blur" not in normalize_layout({})
    assert camera_regions(lay, 1080, 1920) is None  # камера двигает весь кадр
    assert layout_for_format(lay, "16:9")["mode"] == "avatar_full"


def test_avatar_geometry_and_auto_scale():
    # cam 0.5×1 кадра 1920×1080 = 960×1080 px (пропорция 0.889): во всю ширину 1080 → высота 1216.
    g = avatar_geometry(AVATAR, 1080, 1920, 1920, 1080)
    assert (g["w"], g["h"], g["x"]) == (1080, 1216, 0) and g["y"] == int(960 - 608)
    big = avatar_geometry({**AVATAR, "video_scale": 1.5}, 1080, 1920, 1920, 1080)
    assert big["w"] == 1620 and big["x"] == -270  # шире кадра — края срезаются
    # Угол-вебка 0.28×0.30 (538×324 px): заполнить высоту — 5.9 ширин, берём не больше 1.6.
    assert avatar_auto_scale({"x": 0.7, "y": 0.0, "w": 0.28, "h": 0.3}, 1080, 1920, 1920, 1080) == 1.6
    assert avatar_auto_scale({"x": 0.4, "y": 0.0, "w": 0.2, "h": 1.0}, 1080, 1920, 1920, 1080) == 1.0


def test_avatar_full_graph_blur_or_background():
    blur = _layout_filter(1080, 1920, 30, "cover", "center", normalize_layout(AVATAR), None, None)
    assert "crop=iw*0.50000:ih*1.00000:iw*0.50000:ih*0.00000" in blur and "boxblur" in blur
    assert "overlay=x=(W-w)/2:y=trunc(0.5000*H-h/2)" in blur and blur.endswith("[vbase]")
    solid = _layout_filter(1080, 1920, 30, "cover", "center",
                           normalize_layout({**AVATAR, "bg_blur": False, "bg": "#112233"}), None, None)
    assert "boxblur" not in solid and "color=c=0x112233" in solid


def _avatar_source(path):
    # Слева «ютуб» (чёрный), справа аватар (белый).
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=320x180:r=25",
                    "-f", "lavfi", "-i", "sine=frequency=500:sample_rate=44100", "-t", "2",
                    "-vf", "drawbox=x=160:y=0:w=160:h=180:color=white:t=fill", "-pix_fmt", "yuv420p",
                    "-c:v", "libx264", "-c:a", "aac", str(path)], check=True)


@pytest.mark.parametrize("blur", [True, False])
def test_avatar_full_real_render_puts_avatar_in_frame(tmp_path, blur):
    _require_ffmpeg()
    source = tmp_path / "stream.mp4"
    _avatar_source(source)
    out = tmp_path / "avatar.mp4"
    layout = normalize_layout({**AVATAR, "bg_blur": blur, "bg": "#000000"})
    preset = {"output_width": 180, "output_height": 320, "fps": 25, "scale_mode": "cover", "layout": layout}
    subprocess.run(build_ffmpeg_render_args(source, out, 0, 1.5, preset), check=True)
    frame = tmp_path / "f.raw"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "0.5", "-i", str(out), "-frames:v", "1",
                    "-f", "rawvideo", "-pix_fmt", "gray", str(frame)], check=True)
    pixels = frame.read_bytes()
    row = lambda y: pixels[y * 180:(y + 1) * 180]  # noqa: E731
    g = avatar_geometry(layout, 180, 320, 320, 180)
    assert min(row(160)) > 200  # аватар (белый) во всю ширину посередине, «ютуба» нет
    assert g["y"] > 20
    if blur:
        assert min(row(5)) > 150  # сверху — размытый тот же аватар
    else:
        assert max(row(5)) < 40   # сверху — фон


def test_avatar_full_animated_background_zone_around_avatar():
    layout = normalize_layout({**AVATAR, "bg_blur": False, "bg": {"kind": "aurora"}})
    zone = piece_zone(layout, {"start_sec": 0, "end_sec": 2}, {"width": 1920, "height": 1080}, None, 1080, 1920)
    assert zone and zone["bg"]["kind"] == "aurora"
    assert zone["hole"]["w"] == 1080 and 340 < zone["hole"]["y"] < 360
    assert piece_zone(normalize_layout(AVATAR), {"start_sec": 0, "end_sec": 2}, {}, None, 1080, 1920) is None


# ------------------------------------------------------------------ рамки героя: экранный контент


def test_screen_boxes_are_not_the_hero():
    text = json.dumps({"frames": [
        {"x0": 0.3, "y0": 0.3, "x1": 0.6, "y1": 0.7, "kind": "screen"},  # человек из ютуба
        {"x0": 0.7, "y0": 0.5, "x1": 1.0, "y1": 0.9, "kind": "avatar"},
        {"x0": 0.3, "y0": 0.3, "x1": 0.6, "y1": 0.7, "kind": "screen"}]})
    boxes = _parse_boxes(text, 3)
    assert boxes[0]["screen"] is True and "screen" not in boxes[1]
    out = split_screen_boxes(boxes, [0, 0, 1], 2)
    assert out["union"] == {"x0": 0.7, "y0": 0.5, "x1": 1.0, "y1": 0.9}  # герой — аватар
    assert out["pieces"][0] == out["union"]
    assert out["pieces"][1]["screen"] is True  # в куске только экран — помечено
    assert out["screen"]["screen"] is True
    only_screen = split_screen_boxes([boxes[0]], [0], 1)
    assert only_screen["union"] is None and text_positions(only_screen["union"])["subject"] is None


# ------------------------------------------------------------------ detect_avatar (Gemini — фейк)


class FakeVision:
    def __init__(self, frames):
        self.frames = frames
        self.calls = []

    def generate_content(self, model, payload):
        self.calls.append(payload)
        n = sum(1 for p in payload["contents"][0]["parts"] if "inlineData" in p)
        text = json.dumps({"frames": (self.frames * n)[:n]})
        return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def test_place_avatar_most_common_place_with_padding():
    corner = {"x0": 0.70, "y0": 0.40, "x1": 0.95, "y1": 0.95}
    center = {"x0": 0.30, "y0": 0.10, "x1": 0.60, "y1": 0.90}
    out = avatar_box.place_avatar([corner, corner, center, None])
    assert out["moves"] and out["places"] == 2 and out["frames_with_avatar"] == 3
    assert out["cam"]["x"] < 0.70 and out["cam"]["x"] + out["cam"]["w"] <= 1.0
    assert avatar_box.place_avatar([None])["cam"] is None


def test_detect_avatar_tool_layout_and_focus_warning(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    ctx = ToolContext(store=store, clip_plan_id=plan["id"])
    monkeypatch.setattr(agent_tools, "_subject_boxes", lambda c: {"union": None, "pieces": [], "screen": None})

    # Без Gemini и facecam — avatar_full честно отказывает и говорит, что дать.
    from app.settings import settings

    monkeypatch.setattr(settings, "gemini_api_key", "")
    out, ok = run_tool(ctx, "set_layout", {"mode": "avatar_full"})
    assert not ok and "cam" in " ".join(out.get("problems") or [])
    assert "avatar_hint" in run_tool(ctx, "get_clip", {})[0]["framing"]

    fake = FakeVision([{"x0": 0.72, "y0": 0.45, "x1": 0.98, "y1": 0.98}])
    real = avatar_box.detect_avatar
    monkeypatch.setattr(avatar_box, "detect_avatar", lambda store_, pid, client=None, refresh=False:
                        real(store_, pid, client=fake, refresh=refresh))
    out, ok = run_tool(ctx, "detect_avatar", {})
    assert ok and out["cam"]["x"] < 0.72 and out["frames_with_avatar"] == out["frames"] > 0
    assert len(fake.calls) == 1
    parts = fake.calls[0]["contents"][0]["parts"]
    assert "НЕ аватар" in parts[0]["text"] and parts[1]["inlineData"]["mimeType"] == "image/jpeg"
    run_tool(ctx, "detect_avatar", {})
    assert len(fake.calls) == 1  # кэш по кускам

    clip = run_tool(ctx, "get_clip", {})[0]
    assert clip["framing"]["avatar"]["from"] == "detect_avatar" and "avatar_full" in clip["framing"]["avatar"]["note"]

    # Окно 9:16 на x=0.3 (человек на экране) — мимо аватара: предупреждение с правкой.
    out, ok = run_tool(ctx, "set_focus", {"piece": 0, "points": [{"t": 0, "x": 0.3}]})
    assert ok and any("не захватывает аватар" in w and "avatar_full" in w for w in out["warnings"]), out

    out, ok = run_tool(ctx, "set_layout", {"mode": "avatar_full"})
    assert ok, out
    lay = out["layout"]
    assert lay["mode"] == "avatar_full" and lay["cam"]["x"] < 0.72 and lay["bg_blur"] is True
    assert lay["video_scale"] > 1 and "view_frame" in out["note"] and "detect_avatar" in out["cam_from"]
    out, ok = run_tool(ctx, "set_focus", {"piece": 0, "points": [{"t": 0, "x": 0.3}]})
    assert ok and any("фокус окна в ней не виден" in w for w in out["warnings"])

    # Фон клипа (set_backdrop) задан — аватар на нём, а не на размытии.
    rs = store.get_clip_plan(plan["id"])["render_settings"]
    store.set_clip_plan_render_settings(plan["id"], {**rs, "layout": {"mode": "single"},
                                                     "backdrop": {"kind": "solid", "colors": ["#101010"]}})
    out, ok = run_tool(ctx, "set_layout", {"mode": "avatar_full"})
    assert ok and out["layout"]["bg_blur"] is False and out["layout"]["bg"]["colors"] == ["#101010"]

    # Кадр через настоящий компоновщик рендера — 1080×1920.
    frame, ok = run_tool(ctx, "view_frame", {"t": 1.0})
    assert ok and frame.mime_type.startswith("image/")


def test_positions_report_screen_box_separately(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    screen = {"x0": 0.2, "y0": 0.3, "x1": 0.5, "y1": 0.7, "screen": True}
    monkeypatch.setattr(agent_tools, "_subject_boxes",
                        lambda c: {"union": None, "pieces": [screen, screen], "screen": screen})
    clip = run_tool(ToolContext(store=store, clip_plan_id=plan["id"]), "get_clip", {})[0]
    assert clip["positions"]["subject_box"] is None and clip["positions"]["screen_box"] == screen
    assert "не герой" in clip["positions"]["screen_note"]
    assert clip["pieces"][0]["subject_box"]["screen"] is True
