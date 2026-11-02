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
    assert out["moves"] and out["place_count"] == 2 and out["frames_with_avatar"] == 3
    assert out["cam"]["x"] < 0.70 and out["cam"]["x"] + out["cam"]["w"] <= 1.0
    assert [p["place"] for p in out["places"]] == [0, 1]  # все места, по времени
    assert avatar_box.place_avatar([None])["cam"] is None and avatar_box.place_avatar([None])["places"] == []
    single = avatar_box.place_avatar([corner, None, corner], [1.0, 2.0, 3.0])
    assert not single["moves"] and len(single["places"]) == 1 and single["places"][0]["src_from"] == 1.0


# ------------------------------------------------------------------ аватар в нескольких местах (QA 02.11, клип 892)

BIG = {"x0": 0.10, "y0": 0.05, "x1": 0.55, "y1": 0.98}     # до 281 с: крупно левее
CORNER = {"x0": 0.72, "y0": 0.45, "x1": 0.97, "y1": 0.98}  # потом: в углу справа


def test_place_avatar_returns_all_places_with_time_ranges():
    times = [279.9, 285.2, 290.6, 295.9, 301.2, 306.6]
    out = avatar_box.place_avatar([BIG, CORNER, CORNER, None, CORNER, CORNER], times)
    assert out["moves"] and out["place_count"] == 2
    assert out["cam"]["x"] > 0.6  # чаще всего — угол
    first, second = out["places"]
    assert first["cam"]["x"] < 0.2 and first["src_from"] == 279.9 and first["src_to"] == pytest.approx(282.55)
    assert second["src_from"] == pytest.approx(282.55) and second["src_to"] == 306.6 and second["frames"] == 4
    # Граница — на склейку плана между кадрами (ближайшую к середине), а не посередине.
    fixed = avatar_box.refine_boundaries(out["places"], [100.0, 281.0, 300.0])
    assert fixed[0]["src_to"] == 281.0 and fixed[1]["src_from"] == 281.0 and fixed[1]["from_shot_cut"]
    # Аватар вернулся на прежнее место — три отрезка, место 0 снова.
    back = avatar_box.place_avatar([CORNER, BIG, CORNER], [1.0, 2.0, 3.0])
    assert [p["place"] for p in back["places"]] == [0, 1, 0]


def test_avatar_cam_padding_stops_at_chat_overlay():
    # QA 02.11 (клип 892): запас PAD рамки захватил обрывки чата «SaZiann…» над аватаром в углу.
    text = json.dumps({"frames": [
        {**CORNER, "ui": [{"x0": 0.75, "y0": 0.30, "x1": 1.0, "y1": 0.44},     # чат над аватаром
                          {"x0": 0.975, "y0": 0.5, "x1": 1.0, "y1": 0.9},      # полоска интерфейса справа
                          {"x0": 0.8, "y0": 0.6, "x1": 0.9, "y1": 0.7},        # подпись на самой модели
                          {"x0": 0.0, "y0": 0.0, "x1": 0.2, "y1": 0.2}]},      # далеко — не мешает
        CORNER]})
    boxes = avatar_box.parse_boxes(text, 2)
    assert len(boxes[0]["ui"]) == 4 and "ui" not in boxes[1]
    plain = avatar_box.place_avatar([CORNER, CORNER])["cam"]
    cam = avatar_box.place_avatar(boxes)["cam"]
    assert plain["y"] < 0.44 and cam["y"] == 0.44  # верх — по низу чата, а не с запасом в чат
    assert round(cam["x"] + cam["w"], 4) == 0.975 < round(plain["x"] + plain["w"], 4)
    assert cam["x"] == plain["x"] and cam["y"] + cam["h"] == pytest.approx(plain["y"] + plain["h"], abs=1e-4)
    # Окно на самой модели внутрь аватара рамку не режет.
    (_x0, y0, _x1, _y1), sides = avatar_box.trim_ui((0.7, 0.4, 1.0, 1.0), CORNER, [{"x0": 0.7, "y0": 0.4, "x1": 1.0,
                                                                                     "y1": 0.8}])
    assert sides == [] and y0 == 0.4


def test_chat_over_model_head_is_cut_but_not_the_face():
    # QA 3 (клип 895): колонка чата справа сверху до y 0.581 лежит ПОВЕРХ головы модели (верх 0.457);
    # прежняя обрезка останавливалась на краю модели — нижние сообщения были в кадре весь ролик.
    model = {"x0": 0.711, "y0": 0.457, "x1": 0.95, "y1": 0.963}
    chat = {"x0": 0.81, "y0": 0.0, "x1": 0.998, "y1": 0.581}
    youtube_title = {"x0": 0.084, "y0": 0.88, "x1": 0.288, "y1": 0.94}  # далеко — не мешает
    text = json.dumps({"frames": [{**model, "ui": [chat, youtube_title],
                                   "face": {"x0": 0.76, "y0": 0.62, "x1": 0.9, "y1": 0.74}}] * 3})
    boxes = avatar_box.parse_boxes(text, 3)
    assert boxes[0]["face"]["y0"] == 0.62
    out = avatar_box.place_avatar(boxes)
    cam = out["cam"]
    # QA 5 (клип 897): над лицом — запас в полвысоты лица (0.62 − 0.06): край встаёт на него, а не на низ чата
    # (0.581); полоска чата 0.56–0.581 остаётся и названа в ui_left — лицо важнее.
    assert cam["y"] == 0.56 and out["face"]["y0"] == 0.62
    assert cam["x"] < 0.711 and cam["x"] + cam["w"] > 0.95  # бока модели не срезаны
    assert out["ui_left"] == [chat]
    # Сообщения чата отдельными рамками (как Gemini отвечает чаще) — та же рамка.
    bubbles = [{"x0": 0.865, "y0": 0.444, "x1": 0.985, "y1": 0.509}, {"x0": 0.892, "y0": 0.519, "x1": 0.999, "y1": 0.581},
               {"x0": 0.874, "y0": 0.519, "x1": 0.968, "y1": 0.581}, {"x0": 0.84, "y0": 0.3, "x1": 0.999, "y1": 0.354}]
    assert avatar_box.place_avatar([{**model, "ui": bubbles}] * 2)["cam"]["y"] == 0.581
    # Чат ниже, до глаз: в лицо рамку не режем — окно остаётся и названо в ui_left.
    deep = {"x0": 0.81, "y0": 0.0, "x1": 0.998, "y1": 0.66}
    kept = avatar_box.place_avatar([{**model, "ui": [deep], "face": {"x0": 0.76, "y0": 0.62, "x1": 0.9, "y1": 0.74}}])
    assert kept["cam"]["y"] <= avatar_box.face_zone(kept["face"])["y0"] and kept["ui_left"] == [deep]
    # Без лица (старый кэш) — не глубже четверти модели.
    (_x0, y0, _x1, _y1), sides = avatar_box.trim_ui((0.69, 0.43, 0.97, 0.99), model, [deep])
    assert sides == [] and y0 == 0.43


def test_scene_cuts_in_window_are_source_seconds(tmp_path):
    _require_ffmpeg()
    from app.scene_cuts import detect_scene_cuts

    video = tmp_path / "cut.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=160x90:r=25:d=4",
                    "-vf", "drawbox=x=0:y=0:w=160:h=90:color=white:t=fill:enable='gte(t,3)'",
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", str(video)], check=True)
    cuts = detect_scene_cuts(str(video), start=2.0, duration=1.8)
    assert cuts and abs(cuts[0] - 3.0) < 0.1


def _scene_change_source(path, dissolve: float):
    # Сцена A (0–2.6 с): «аватар» белым слева, справа «ютуб» с бегущей полосой; сцена B: наоборот.
    # Смена в 2.0 с: наплыв ``dissolve`` с (xfade) или жёсткая склейка (0).
    def scene(left: bool) -> str:
        x = 0 if left else 160
        other = 160 if left else 0
        return (f"color=c=0x202020:s=320x180:r=30:d=4,drawbox=x={x}:y=0:w=160:h=180:color=white:t=fill,"
                f"drawbox=x='{other}+mod(t*60\\,140)':y=60:w=20:h=60:color=red:t=fill")
    if dissolve:
        graph = f"{scene(True)}[a];{scene(False)}[b];[a][b]xfade=transition=fade:duration={dissolve}:offset=2.0"
    else:
        graph = f"{scene(True)}[a];{scene(False)}[b];[a]trim=0:2[a2];[b]trim=0:2,setpts=PTS-STARTPTS[b2];[a2][b2]concat"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-filter_complex", graph, "-pix_fmt", "yuv420p", "-c:v", "libx264",
                    str(path)], check=True)


def test_avatar_boundary_on_dissolve_is_its_middle_not_its_end(tmp_path):
    # QA 02.11 (клип 895): смена сцены в стриме — наплыв ≈0.5 с, граница мест стояла на его конце
    # (склейка ffmpeg), и рамка старого места 0.35 с показывала чужую сцену.
    _require_ffmpeg()
    from app.scene_cuts import transition_span

    video = tmp_path / "dissolve.mp4"
    _scene_change_source(video, 0.5)
    span = transition_span(str(video), 0.5, 3.5)
    assert span and not span["hard"]
    assert 1.95 <= span["start"] <= 2.12 and 2.38 <= span["end"] <= 2.55 and span["mid"] == pytest.approx(2.25, abs=0.06)
    assert transition_span(str(video), 0.5, 3.5, hint=2.5) == span  # склейка ffmpeg на конце — та же смена
    hard = tmp_path / "hard.mp4"
    _scene_change_source(hard, 0)
    cut = transition_span(str(hard), 0.5, 3.5)
    assert cut and cut["hard"] and cut["start"] == pytest.approx(2.0, abs=0.04)
    left, right = {"x0": 0.0, "y0": 0.0, "x1": 0.5, "y1": 1.0}, {"x0": 0.5, "y0": 0.0, "x1": 1.0, "y1": 1.0}
    places = avatar_box.place_avatar([left, right], [1.0, 3.2])["places"]
    snapped = avatar_box.refine_boundaries(places, [2.5])  # ffmpeg увидел только конец наплыва
    assert snapped[1]["src_from"] == 2.5
    finder = lambda lo, hi, hint: transition_span(str(video), lo, hi, hint=hint)  # noqa: E731
    settled = avatar_box.settle_transitions(snapped, finder)
    assert settled[0]["src_to"] == settled[1]["src_from"] == pytest.approx(2.25, abs=0.06)
    assert settled[1]["transition"]["start"] < settled[1]["src_from"] < settled[1]["transition"]["end"]
    # Наплыв ffmpeg не нашёл вовсе (порог на соседние кадры) — смену находим сами между кадрами мест.
    assert avatar_box.settle_transitions(places, finder)[1]["src_from"] == pytest.approx(2.25, abs=0.06)
    # Жёсткая склейка — граница на первом кадре новой сцены.
    hard_finder = lambda lo, hi, hint: transition_span(str(hard), lo, hi, hint=hint)  # noqa: E731
    assert avatar_box.settle_transitions(places, hard_finder)[1]["src_from"] == pytest.approx(2.0, abs=0.04)
    # Смены нет — граница как была.
    assert avatar_box.settle_transitions(snapped, lambda lo, hi, hint: None)[1]["src_from"] == 2.5


def test_layout_cams_normalize_and_spans_per_part():
    left = {"x": 0.1, "y": 0.0, "w": 0.45, "h": 1.0}
    right = {"x": 0.7, "y": 0.4, "w": 0.27, "h": 0.57}
    lay = normalize_layout({**AVATAR, "cam": right, "video_scale": 1.5, "cams": [
        {"from": 281, "to": 309.3, "cam": right}, {"from": 277.2, "to": 281, "cam": left, "video_scale": 1.1},
        {"from": 5, "to": 5.01, "cam": left}, {"from": 1, "to": 2}, "мусор"]})
    assert [c["from"] for c in lay["cams"]] == [277.2, 281]  # по порядку, пустое и без рамки — выброшены
    assert "cams" not in normalize_layout(AVATAR) and "cams" not in normalize_layout({"mode": "split", "cams": lay["cams"]})
    from app.stream_layout import avatar_cam_spans

    # Часть 277.2–290: больше в углу — основа угловой рамкой, левая — первые 3.8 с части.
    main, spans = avatar_cam_spans(lay, 277.2, 290.0)
    assert main["cam"] == right and main["video_scale"] == 1.5
    assert spans == [{"t0": 0.0, "t1": 3.8, "cam": left, "video_scale": 1.1}]
    main, spans = avatar_cam_spans(lay, 277.5, 280.0)  # вся часть до смены сцены
    assert main["cam"] == left and spans == []
    assert avatar_cam_spans(normalize_layout(AVATAR), 0, 5)[1] == []
    # Граф: одна рамка — ровно прежний; со сменой — второй кадр поверх на свои секунды.
    from app.render import _preset_for_segment

    piece = _preset_for_segment({"layout": lay}, {"start_sec": 277.2, "end_sec": 290.0})
    graph = _layout_filter(1080, 1920, 30, "cover", "center", normalize_layout(piece["layout"]), None, None,
                           avatar_spans=piece["avatar_spans"])
    assert "enable='between(t,0.000,3.800)'" in graph and graph.count("boxblur") == 2 and graph.endswith("[vbase]")
    plain = _preset_for_segment({"layout": normalize_layout(AVATAR)}, {"start_sec": 0, "end_sec": 2})
    assert "avatar_spans" not in plain


def _moving_avatar_source(path):
    # 0–1 с аватар (белый) справа, 1–2 с — слева; остальное — «ютуб» (чёрный).
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=320x180:r=25",
                    "-f", "lavfi", "-i", "sine=frequency=500:sample_rate=44100", "-t", "2",
                    "-vf", "drawbox=x=160:y=0:w=160:h=180:color=white:t=fill:enable='lt(t,1)',"
                           "drawbox=x=0:y=0:w=160:h=180:color=white:t=fill:enable='gte(t,1)'",
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", "-c:a", "aac", str(path)], check=True)


def _mid_row(tmp_path, video, t):
    frame = tmp_path / f"f{t}.raw"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(t), "-i", str(video), "-frames:v", "1",
                    "-f", "rawvideo", "-pix_fmt", "gray", str(frame)], check=True)
    pixels = frame.read_bytes()
    return pixels[160 * 180:161 * 180]


def test_avatar_cams_real_render_follows_avatar_inside_one_part(tmp_path):
    _require_ffmpeg()
    from app.render import _preset_for_segment

    source = tmp_path / "moving.mp4"
    _moving_avatar_source(source)
    right = {"x": 0.5, "y": 0.0, "w": 0.5, "h": 1.0}
    left = {"x": 0.0, "y": 0.0, "w": 0.5, "h": 1.0}
    base = {"output_width": 180, "output_height": 320, "fps": 25, "scale_mode": "cover"}
    one = normalize_layout({**AVATAR, "cam": right})
    both = normalize_layout({**AVATAR, "cam": right, "cams": [{"from": 0, "to": 1, "cam": right},
                                                              {"from": 1, "to": 2, "cam": left}]})
    seg = {"start_sec": 0.5, "end_sec": 1.9}
    for name, lay in (("one", one), ("both", both)):
        out = tmp_path / f"{name}.mp4"
        preset = _preset_for_segment({**base, "layout": lay}, seg)
        subprocess.run(build_ffmpeg_render_args(source, out, 0.5, 1.9, preset), check=True)
        assert min(_mid_row(tmp_path, out, 0.2)) > 200  # 0.7 с исходника — аватар справа, в кадре
        late = _mid_row(tmp_path, out, 1.1)            # 1.6 с исходника — аватар уже слева
        if name == "one":
            assert max(late) < 40  # одна рамка на весь клип: аватара нет (брак QA 02.11)
        else:
            assert min(late) > 200


def test_detect_avatar_places_go_to_layout_cams_and_named_seconds(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, source, plan, a, _b = _setup(tmp_path, monkeypatch)
    # Один кусок 0–5 с исходника: кадры по порядку времени.
    plan = store.create_clip_plan(source["id"], plan["analysis_id"], "Один кусок", segment_ids=[a["id"]])
    ctx = ToolContext(store=store, clip_plan_id=plan["id"])
    monkeypatch.setattr(agent_tools, "_subject_boxes", lambda c: {"union": None, "pieces": [], "screen": None})
    fake = FakeVision([BIG, BIG, CORNER, CORNER, CORNER, CORNER])
    real = avatar_box.detect_avatar
    monkeypatch.setattr(avatar_box, "detect_avatar", lambda store_, pid, client=None, refresh=False:
                        real(store_, pid, client=fake, refresh=refresh))
    out, ok = run_tool(ctx, "detect_avatar", {})
    assert ok, out
    assert len(out["places"]) >= 2 and out["places"][0]["cam"]["x"] < 0.2
    warning = " ".join(out["warnings"])
    assert "2 местах" in warning and f"{out['places'][0]['from_t']:.1f}–{out['places'][0]['to_t']:.1f} с" in warning
    assert "view_frame" in warning and "один cam" in warning

    out, ok = run_tool(ctx, "set_layout", {"mode": "avatar_full"})
    assert ok, out
    lay = out["layout"]
    assert len(lay["cams"]) >= 2 and lay["cams"][0]["cam"]["x"] < 0.2 and "по отрезкам" in out["cam_from"]
    assert all(c.get("video_scale") for c in lay["cams"])  # крупность — своя у каждой рамки
    assert len(out["places"]) >= 2 and "view_frame в КАЖДОМ" in out["note"]
    assert agent_tools.avatar_places(ctx)[0]["view_t"] < agent_tools.avatar_places(ctx)[-1]["view_t"]
    frame, ok = run_tool(ctx, "view_frame", {"t": 0.5})
    assert ok and frame.mime_type.startswith("image/")

    # Один cam руками — рамки по отрезкам сняты, но сервис предупредит, что аватар переезжает.
    out, ok = run_tool(ctx, "set_layout", {"mode": "avatar_full", "cam": {"x": 0.7, "y": 0.4, "w": 0.27, "h": 0.57}})
    assert ok and "cams" not in out["layout"] and any("места" in w or "местах" in w for w in out["warnings"])
    out, ok = run_tool(ctx, "set_layout", {"mode": "avatar_full", "cams": "auto"})
    assert ok and len(out["layout"]["cams"]) >= 2
    # Свои отрезки по шкале clip.
    out, ok = run_tool(ctx, "set_layout", {"mode": "avatar_full", "cams": [
        {"from_t": 0, "to_t": 1.0, "cam": {"x": 0.1, "y": 0, "w": 0.45, "h": 1}},
        {"from_t": 1.0, "to_t": 30, "cam": {"x": 0.7, "y": 0.4, "w": 0.27, "h": 0.57}}]})
    assert ok and out["layout"]["cams"][0]["from"] == 0.0 and out["layout"]["cams"][1]["from"] == 1.0


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
