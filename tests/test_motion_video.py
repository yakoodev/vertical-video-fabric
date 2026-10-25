"""Видео-слой (`video`) и корпус телефона (`device: "phone"`): схема, время файла, нарезка кадров."""

from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path

import pytest

from app import motion
from app.motion_render import (
    VIDEO_FRAME_PATTERN,
    build_compositor_args,
    build_video_frames_args,
    prepare_video_frames,
    resolve_layer_assets,
    video_frame_plan,
    video_frame_width,
)

VIDEO = {"id": "rec", "type": "video", "start": 2, "end": 5, "props": {"asset_id": 7}}


# ---------------------------------------------------------------- схема


def test_video_layer_normalizes_with_defaults_and_crop_keys():
    [layer] = motion.normalize_layers([{**VIDEO, "keys": {"cropY": [{"t": 0, "v": 0}, {"t": 1, "v": 0.3}]}}])
    props = layer["props"]
    assert props["asset_id"] == "7"
    assert (props["from"], props["speed"], props["loop"], props["freeze"]) == (0, 1, False, True)
    assert props["w"] == 600 and props["crop"] is None and "device" not in props
    assert layer["keys"]["cropY"][1]["v"] == 0.3
    # Зажатие: скорость 0.25..4, from ≥ 0.
    problems = motion.validate_layers([{**VIDEO, "props": {"asset_id": 7, "speed": 10, "from": -3}}])
    assert any("speed" in p for p in problems) and any("from" in p for p in problems)
    [fixed] = motion.normalize_layers([{**VIDEO, "props": {"asset_id": 7, "speed": 10, "from": -3}}])
    assert fixed["props"]["speed"] == 4 and fixed["props"]["from"] == 0


def test_video_layer_needs_asset_and_crop_keys_stay_on_picture_types():
    problems = motion.validate_layers([{"type": "video", "start": 0, "end": 2, "props": {}}])
    assert any("у видео нужен asset_id" in p for p in problems)
    problems = motion.validate_layers([{"type": "text", "start": 0, "end": 2, "props": {"text": "x"},
                                        "keys": {"cropY": [{"t": 0, "v": 0.5}]}}])
    assert any("image/video" in p for p in problems)
    assert not motion.validate_layers([{**VIDEO, "keys": {"cropX": [{"t": 0, "v": 0.5}]}}])
    assert motion.layer_asset_refs(motion.normalize_layers([VIDEO])[0]) == [7]


def test_device_phone_is_optional_and_old_image_layers_unchanged():
    old = {"id": "img", "type": "image", "start": 0, "end": 2, "props": {"asset_id": 3, "w": 500, "radius": 40}}
    [layer] = motion.normalize_layers([old])
    assert set(layer["props"]) == {"asset_id", "src", "w", "radius", "border", "crop", "flipX"}
    phone = {**old, "props": {**old["props"], "device": "phone", "deviceColor": "#8A857C", "island": False}}
    [layer] = motion.normalize_layers([phone])
    assert layer["props"]["device"] == "phone" and layer["props"]["deviceColor"] == "#8a857c"
    assert layer["props"]["island"] is False
    # null — «без корпуса», неизвестное устройство — ошибка и без корпуса.
    [off] = motion.normalize_layers([{**old, "props": {**old["props"], "device": None}}])
    assert off["props"]["device"] is None
    assert any("tablet" in p for p in motion.validate_layers([{**old, "props": {"asset_id": 3, "device": "tablet"}}]))
    schema = motion.schema_summary()
    assert "video" in schema["types"] and schema["devices"] == ["phone"]
    assert schema["props"]["video"]["speed"]["min"] == 0.25
    assert schema["props"]["image"]["device"]["optional"] is True
    json.dumps(schema, ensure_ascii=False)


def test_video_file_time_matches_engine_rules():
    t = motion.video_file_time
    assert t({}, 0, 10) == 0
    assert t({"from": 2}, 1.5, 10) == 3.5
    assert t({"from": 1, "speed": 2}, 1.5, 10) == 4
    assert t({"speed": 100}, 1, 10) == 4  # зажата
    assert t({"from": 8}, 5, 10) == pytest.approx(10 - motion.VIDEO_TAIL)  # стоп-кадр
    assert t({"from": 8, "freeze": False}, 5, 10) is None
    span = 10 - motion.VIDEO_TAIL - 4
    assert t({"from": 4, "loop": True}, span + 1, 10) == pytest.approx(5)
    assert t({"from": 3, "freeze": False}, 100, None) == 103  # длина неизвестна


# ---------------------------------------------------------------- нарезка кадров


def test_frame_width_follows_layer_size_scale_and_crop():
    layer = motion.normalize_layers([{**VIDEO, "props": {"asset_id": 7, "w": 600}}])[0]
    assert video_frame_width(layer, 1080) == 690  # 600 × 1.15
    assert video_frame_width(layer, 720) == 460  # выход меньше — кадры меньше
    zoom = {**layer, "keys": {"scale": [{"t": 0, "v": 1}, {"t": 1, "v": 1.4}]}}
    assert video_frame_width(zoom, 1080) == 966
    window = {**layer, "props": {**layer["props"], "crop": {"x": 0, "y": 0, "w": 0.5, "h": 0.5}}}
    # Окно в полкадра — весь кадр файла вдвое шире (видимая часть 690 px остаётся резкой).
    assert video_frame_width(window, 1080) == 1380
    assert video_frame_width(layer, 1080, natural=(480, 270)) == 480  # не больше исходника
    assert video_frame_width(layer, 1080) % 2 == 0


def test_frame_width_cap_follows_output_format():
    """Предел — ширина кадра вывода: 1080 у 9:16, 1920 у 16:9 (окно ~1040 px из 4K не мылится);
    окно crop из 4K — с запасом, но площадь кадра и всех кадров слоя ограничены."""
    from app.motion_render import (
        VIDEO_MAX_FRAME_PIXELS,
        VIDEO_MAX_TOTAL_PIXELS,
        video_max_frame_width,
    )

    assert video_max_frame_width("9:16") == 1080 and video_max_frame_width(None) == 1080
    assert video_max_frame_width("1:1") == 1080 and video_max_frame_width("16:9") == 1920
    uhd = (3840, 2160)
    wide = motion.normalize_layers([{**VIDEO, "props": {"asset_id": 7, "w": 1040}}])[0]
    # 9:16 — как раньше (≤ 1080), 16:9 — 1040 × 1.15 = 1196 (опорные px 16:9 — px кадра 1920).
    assert video_frame_width(wide, 1080, uhd) == 1080
    assert video_frame_width(wide, 1080, uhd, "16:9") == 1196
    full = motion.normalize_layers([{**VIDEO, "props": {"asset_id": 7, "w": 1920}}])[0]
    assert video_frame_width(full, 1080, uhd, "16:9") == 1920  # во весь кадр — не шире кадра
    # Окно 40 % 4K-записи на 1040 px: весь кадр 1196 / 0.4 = 2990 → потолок площади 2560 (16:9).
    window = {**wide, "props": {**wide["props"], "crop": {"x": 0.3, "y": 0.3, "w": 0.4, "h": 0.4}}}
    got = video_frame_width(window, 1080, uhd, "16:9")
    assert got == 2560 and got * got * 9 / 16 <= VIDEO_MAX_FRAME_PIXELS
    assert video_frame_width(window, 1080, (1920, 1080), "16:9") == 1920  # не больше исходника
    # Длинный слой: все кадры — в бюджете площади, но не мельче прежних 1080.
    assert video_frame_width(window, 1080, uhd, "16:9", count=9000) == 1920  # 5 мин — кадры 1920×1080
    assert video_frame_width(window, 1080, uhd, "16:9", count=40000) == 1080  # пол — прежний предел
    mid = video_frame_width(window, 1080, uhd, "16:9", count=6000)
    assert 1920 < mid < 2560 and 6000 * mid * mid * 9 / 16 <= VIDEO_MAX_TOTAL_PIXELS * 1.01


def test_wide_frames_cap_area_in_ffmpeg_by_real_aspect(tmp_path):
    """Кадры шире 1080 — площадь ограничена и в ffmpeg (по пропорции после поворота):
    9:16 и прежние ширины — та же команда, что раньше."""
    plan = {"start": 0.0, "rate": None, "span": None, "count": 1}
    old = build_video_frames_args(Path("a.mp4"), tmp_path, plan, 1080)
    assert "scale=1080:-2:flags=lanczos" in old
    args = build_video_frames_args(Path("a.mp4"), tmp_path, plan, 2560)
    vf = args[args.index("-vf") + 1]
    assert vf.startswith("scale='min(2560,trunc(sqrt(") and "*iw/ih)/2)*2)':-2" in vf
    _ffmpeg_or_skip()
    for size, want in (("3840x2160", (2560, 1440)), ("1080x1920", (1440, 2560))):
        src = _synthetic(tmp_path / f"{size}.mp4", 0.2, size)
        out = tmp_path / size
        out.mkdir()
        subprocess.run(build_video_frames_args(src, out, plan, 2560), check=True, capture_output=True)
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "json",
                                str(out / "00000.jpg")], capture_output=True, text=True, check=True)
        stream = json.loads(probe.stdout)["streams"][0]
        assert stream["width"] * stream["height"] <= 2560 * 1440 * 1.01, (size, stream)
        assert abs(stream["width"] - min(2560, want[0])) <= 2, (size, stream)


def test_frame_plan_samples_file_at_fps_over_speed():
    layer = motion.normalize_layers([{**VIDEO, "props": {"asset_id": 7, "from": 1.5, "speed": 2}}])[0]
    plan = video_frame_plan(layer, 30, file_duration=60)
    assert plan["start"] == 1.5 and plan["rate"] == 15  # кадр выхода = один файл
    assert plan["span"] == pytest.approx(3 * 2 + 2 / 15, abs=1e-3)
    assert plan["count"] == 92  # ≈ 3 с × 30 к/с
    # Файл короче — режем до его конца (дальше стоп-кадр/loop по уже нарезанному).
    short = video_frame_plan(layer, 30, file_duration=4)
    assert short["span"] == pytest.approx(2.5) and short["count"] == 38
    # from за концом файла — один последний кадр.
    late = video_frame_plan({**layer, "props": {**layer["props"], "from": 20}}, 30, file_duration=4)
    assert late["count"] == 1 and late["rate"] is None and late["start"] == pytest.approx(3.75)
    # Кадр для агента: один кадр, секунда файла в момент at.
    one = video_frame_plan(layer, 30, file_duration=60, at=3.0)
    assert one == {"start": 3.5, "rate": None, "span": None, "count": 1}
    assert video_frame_plan(layer, 30, file_duration=60, at=9.0) is None  # слой не виден


def test_frames_command_is_one_ffmpeg_cut(tmp_path):
    args = build_video_frames_args(Path("rec.mp4"), tmp_path, {"start": 1.5, "rate": 15, "span": 6.133, "count": 92}, 690)
    assert args[:6] == ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss"]
    assert args[args.index("-ss") + 1] == "1.500" and args[args.index("-t") + 1] == "6.133"
    assert args.index("-ss") < args.index("-i")  # быстрая перемотка до входа
    assert "-an" in args and args[args.index("-vf") + 1] == "fps=15,scale=690:-2:flags=lanczos"
    assert args[args.index("-frames:v") + 1] == "92" and args[-1] == str(tmp_path / VIDEO_FRAME_PATTERN)
    single = build_video_frames_args(Path("rec.mp4"), tmp_path, {"start": 3.5, "rate": None, "span": None, "count": 1}, 400)
    assert "-t" not in single and single[single.index("-vf") + 1] == "scale=400:-2:flags=lanczos"


def test_prepare_writes_manifest_and_survives_a_broken_layer(tmp_path):
    layers = motion.normalize_layers([
        {**VIDEO, "props": {"asset_id": 7, "from": 1}},
        {**VIDEO, "id": "bad", "props": {"asset_id": 8}},
        {**VIDEO, "id": "nofile", "props": {"asset_id": 9}},
        {"id": "t", "type": "text", "start": 0, "end": 1, "props": {"text": "x"}},
    ])
    calls = []

    def run(args, timeout):
        calls.append(args)
        if "bad.mp4" in args:
            raise RuntimeError("moov atom not found")
        out = Path(args[-1]).parent
        for i in range(int(args[args.index("-frames:v") + 1])):
            (out / f"{i:05d}.jpg").write_bytes(b"jpg")

    path, notes = prepare_video_frames(
        layers, {"7": "rec.mp4", "8": "bad.mp4"}, tmp_path, fps=30, out_width=1080, clip_duration=10,
        run=run, probe=lambda p: (60.0, (1080, 2340)),
    )
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert list(manifest) == ["rec"]
    assert manifest["rec"]["start"] == 1 and manifest["rec"]["rate"] == 30 and manifest["rec"]["count"] == 92
    assert manifest["rec"]["duration"] == 60 and manifest["rec"]["pattern"] == VIDEO_FRAME_PATTERN
    assert Path(manifest["rec"]["dir"]).parent == tmp_path / "video-frames"
    assert any("bad" in n and "не нарезалось" in n for n in notes)
    assert len(calls) == 2  # текст и слой без файла — без ffmpeg
    args = build_compositor_args(tmp_path / "comp.json", tmp_path / "assets.json", output_path=tmp_path / "o.mp4",
                                 command=["node", "c.mjs"], videos_path=path)
    assert args[args.index("--videos") + 1] == str(path)
    assert "--videos" not in build_compositor_args(tmp_path / "c.json", tmp_path / "a.json", command=["node", "c.mjs"])
    # Видео-слоёв нет — videos.json не нужен.
    assert prepare_video_frames(layers[3:], {}, tmp_path, fps=30, out_width=1080, run=run)[0] is None


def test_resolve_assets_gives_video_layers_only_videos(tmp_path):
    files = {}
    for asset_id, kind in ((7, "video"), (8, "image"), (9, "gif")):
        f = tmp_path / f"a{asset_id}"
        f.write_bytes(b"x")
        files[asset_id] = {"id": asset_id, "kind": kind, "file_path": str(f)}

    class Store:
        def get_montage_asset(self, asset_id):
            return files[int(asset_id)]

    layers = motion.normalize_layers([
        {**VIDEO, "props": {"asset_id": 7}},
        {**VIDEO, "id": "img", "props": {"asset_id": 8}},
        {**VIDEO, "id": "gif", "props": {"asset_id": 9}},
    ])
    kept, assets, notes = resolve_layer_assets(Store(), layers)
    assert [layer["id"] for layer in kept] == ["rec", "gif"]
    assert set(assets) == {"7", "9"} and any("img" in n for n in notes)


def _ffmpeg_or_skip() -> None:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        pytest.skip("нет ffmpeg")


def _synthetic(path: Path, seconds: float = 2.0, size: str = "320x640") -> Path:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30",
                    "-t", f"{seconds}", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", str(path)],
                   check=True, capture_output=True)
    return path


def test_prepare_real_ffmpeg_cuts_only_the_layer_range(tmp_path):
    _ffmpeg_or_skip()
    src = _synthetic(tmp_path / "rec.mp4", 2.0)
    layers = motion.normalize_layers([
        {**VIDEO, "start": 0, "end": 0.5, "props": {"asset_id": 7, "from": 0.5, "w": 200}},
        {**VIDEO, "id": "slow", "start": 0, "end": 1, "props": {"asset_id": 7, "from": 1.0, "speed": 0.5, "w": 200}},
    ])
    path, notes = prepare_video_frames(layers, {"7": str(src)}, tmp_path, fps=30, out_width=1080)
    assert not notes, notes
    manifest = json.loads(path.read_text(encoding="utf-8"))
    rec, slow = manifest["rec"], manifest["slow"]
    assert 15 <= rec["count"] <= 18  # 0.5 с × 30 к/с (+ запас), а не весь файл
    assert rec["duration"] == pytest.approx(2.0, abs=0.1)
    assert slow["rate"] == 60 and 30 <= slow["count"] <= 34  # 0.5× — 1 с слоя = 0.5 с файла
    first = Path(rec["dir"]) / "00000.jpg"
    assert first.read_bytes()[:2] == b"\xff\xd8"  # JPEG
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "json", str(first)],
                           capture_output=True, text=True, check=True)
    stream = json.loads(probe.stdout)["streams"][0]
    assert stream["width"] == 230 and stream["height"] == 460  # 200 × 1.15, пропорции исходника
    # Кадр для агента: один файл на слой.
    one_dir = tmp_path / "one"
    one_dir.mkdir()
    path, _ = prepare_video_frames(layers, {"7": str(src)}, one_dir, fps=30, out_width=1080, at=0.2)
    one = json.loads(path.read_text(encoding="utf-8"))
    assert one["rec"]["count"] == 1 and one["rec"]["start"] == pytest.approx(0.7)
    # Стоп-кадр за концом файла тоже нарезается (перемотка к самому концу).
    late = motion.normalize_layers([{**VIDEO, "start": 0, "end": 5, "props": {"asset_id": 7, "from": 1.9}}])
    path, notes = prepare_video_frames(late, {"7": str(src)}, tmp_path / "late", fps=30, out_width=540, at=4.0)
    assert path and not notes


# ---------------------------------------------------------------- инструменты агента


def test_agent_cover_and_box_know_video_layers():
    from app import agent_tools

    phone = {"type": "video", "props": {"w": 600, "device": "phone"}}
    assert agent_tools.video_height(phone["props"]) == pytest.approx(600 * 19.5 / 9 + 600 * 0.094)
    assert agent_tools.cover_share(phone) == pytest.approx(600 * agent_tools.video_height(phone["props"]) / (1080 * 1920))
    big = {"type": "video", "props": {"w": 1080}}
    assert agent_tools.cover_share(big) > agent_tools.MAX_COVER_SHARE
    assert agent_tools.covering_problems([big])  # на обычном клипе видео-слой — тоже «картинка»
    assert "видео в телефоне #7" in agent_tools._layer_summary({"type": "video", "start": 1,
                                                                "props": {"asset_id": "7", "device": "phone"}})
    ref = agent_tools.motion_reference()
    assert "video:" in ref and "device" in ref


def test_upload_video_and_kind_check(tmp_path, monkeypatch):
    from app.agent_tools import ToolError, h_add_video_from_source, h_upload_video, run_tool
    from test_agent_tools import _ctx
    from test_clip_spec import _setup

    _ffmpeg_or_skip()
    _client, store, source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    ctx = _ctx(store, plan["id"])
    clip = _synthetic(tmp_path / "screen.mp4", 1.0)
    out = h_upload_video(ctx, {"data_base64": base64.b64encode(clip.read_bytes()).decode(), "filename": "screen.mp4",
                               "label": "Экран"})
    assert out["ok"] and out["kind"] == "video" and out["width"] == 320 and out["duration_sec"] > 0.5
    for bad, name in ((base64.b64encode(b"nope").decode(), "x.mp4"), ("AAAA", "x.png")):
        with pytest.raises(ToolError):
            h_upload_video(ctx, {"data_base64": bad, "filename": name})
    image = store.create_montage_asset(kind="image", label="pic", description="", tags="", file_path=str(clip),
                                       original_filename="p.png", mime_type="", size_bytes=1)
    res, ok = run_tool(ctx, "add_layers", {"layers": [{"type": "video", "start": 0, "end": 2,
                                                       "props": {"asset_id": image["id"]}}]})
    assert not ok and any("нужно видео" in p for p in res["problems"])
    res, ok = run_tool(ctx, "add_layers", {"layers": [{"type": "video", "start": 0, "end": 2, "x": 0.5, "y": 0.5,
                                                       "props": {"asset_id": out["asset_id"], "w": 300,
                                                                 "device": "phone"}}]})
    assert ok, res
    # Кусок исходника → видео в библиотеку: со звуком (слой играет его только с props.audio), audio:false — немое.
    cut = h_add_video_from_source(ctx, {"source_id": source["id"], "start": 1, "end": 2.5})
    asset = store.get_montage_asset(cut["asset_id"])
    assert asset["kind"] == "video" and 1.3 <= asset["duration_sec"] <= 1.7 and asset["has_audio"]
    assert cut["has_audio"] and "props.audio" in cut["hint"]
    mute = h_add_video_from_source(ctx, {"source_id": source["id"], "start": 1, "end": 2.5, "audio": False})
    assert not store.get_montage_asset(mute["asset_id"])["has_audio"] and not mute["has_audio"]
    # end за концом исходника — до конца; пустой кусок и чужой id — понятная ошибка.
    whole = h_add_video_from_source(ctx, {"source_id": source["id"], "start": 5, "end": 500})
    assert whole["duration_sec"] == pytest.approx(source["duration_sec"] - 5, abs=0.3)
    for bad in ({"source_id": source["id"], "start": 3, "end": 3.1}, {"source_id": 999999}):
        with pytest.raises(ToolError):
            h_add_video_from_source(ctx, bad)
