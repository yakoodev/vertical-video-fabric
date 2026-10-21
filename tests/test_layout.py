"""Раскладки стрима: нормализация, граф ffmpeg (split / pip_cam), single без изменений,
автопоиск вебки, файл клипа, ручки API и настоящий рендер split."""

from __future__ import annotations

import subprocess

import pytest

from app.clip_settings import normalize_render_settings
from app.ingest import probe_media
from app.render import (
    ClipRenderService,
    _render_filter,
    _segment_reframe_x,
    build_ffmpeg_render_args,
)
from app.settings import settings
from app.stream_layout import (
    facecam_from_faces,
    gameplay_height,
    layout_active,
    normalize_layout,
    split_geometry,
)
from test_render import _require_ffmpeg, _store

PRESET = {
    "id": 1,
    "output_width": 1080,
    "output_height": 1920,
    "fps": 30,
    "scale_mode": "cover",
    "crop_anchor": "center",
    "video_codec": "libx264",
    "audio_codec": "aac",
    "smart_reframe": True,
}
SPLIT = {"mode": "split", "cam": {"x": 0.7, "y": 0.0, "w": 0.3, "h": 0.3}, "ratio": 0.4}
PIP = {"mode": "pip_cam", "cam": {"x": 0.0, "y": 0.7, "w": 0.25, "h": 0.3}, "pip": {"x": 0.5, "y": 0.15, "w": 0.4}}


def _with_layout(layout, **extra):
    service = ClipRenderService.__new__(ClipRenderService)  # без стора: метод чистый
    return service._preset_with_render_overrides(dict(PRESET), layout=layout, **extra)


# ---------------------------------------------------------------- нормализация


def test_normalize_defaults_to_single_with_full_shape():
    layout = normalize_layout(None)
    assert layout["mode"] == "single"
    assert layout["cam"] == {"x": 0.7, "y": 0.02, "w": 0.28, "h": 0.3}
    assert layout["ratio"] == 0.38 and layout["cam_pos"] == "top"
    assert layout["pip"] == {"x": 0.5, "y": 0.12, "w": 0.42}
    assert layout["border"] == {"color": "#000000", "width": 0} and layout["gap"] == 0
    assert not layout_active(layout)
    # В настройках клипа раскладка есть всегда (single по умолчанию).
    assert normalize_render_settings({})["layout"]["mode"] == "single"


def test_normalize_clamps_and_drops_junk():
    layout = normalize_layout(
        {
            "mode": "split",
            "cam": {"x": 0.9, "y": -1, "w": 0.5, "h": 5},
            "ratio": 0.95,
            "cam_pos": "left",
            "pip": {"x": 3, "y": "nope", "w": 0.01},
            "border": {"color": "red", "width": 999},
            "gap": -5,
            "junk": 1,
        }
    )
    assert layout["mode"] == "split"
    cam = layout["cam"]
    assert cam["w"] == 0.5 and cam["h"] == 1.0
    assert cam["x"] + cam["w"] <= 1.0 and cam["y"] == 0.0  # окно целиком в кадре
    assert layout["ratio"] == 0.6 and layout["cam_pos"] == "top"
    assert layout["pip"] == {"x": 1.0, "y": 0.12, "w": 0.1}
    assert layout["border"] == {"color": "#000000", "width": 40}
    assert layout["gap"] == 0 and "junk" not in layout
    assert normalize_layout({"mode": "hologram"})["mode"] == "single"


def test_split_geometry_sums_to_frame_and_scales_gap():
    layout = normalize_layout({**SPLIT, "gap": 20})
    cam_h, gap, game_h = split_geometry(layout, 1920)
    assert (cam_h, gap) == (768, 20) and cam_h + gap + game_h == 1920
    small = split_geometry(layout, 320)
    assert sum(small) == 320 and all(v % 2 == 0 for v in small)
    assert gameplay_height(layout, 1080, 1920) == game_h
    assert gameplay_height(normalize_layout(PIP), 1080, 1920) == 1920
    assert gameplay_height(None, 1080, 1920) == 1920


# ---------------------------------------------------------------- single = прежний рендер


def test_single_layout_changes_nothing(tmp_path):
    base = dict(PRESET)
    assert _with_layout(None) == base
    assert _with_layout({"mode": "single", "ratio": 0.5}) == base
    assert "layout" not in _with_layout(normalize_layout(None), mirror=True)

    def args(preset):
        return build_ffmpeg_render_args(
            tmp_path / "s.mp4", tmp_path / "o.mp4", 0, 5, preset,
            source_crop={"x": 0.0, "y": 0.1, "w": 1.0, "h": 0.8}, reframe_x="(iw-1080)/2",
        )

    assert args(_with_layout({"mode": "single"})) == args(dict(PRESET))
    assert args(_with_layout({"mode": "single"}, mirror=True)) == args({**PRESET, "mirror": True})


def test_render_clip_plan_reads_layout_from_plan_settings(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    source_path = settings.source_dir / "s.mp4"
    source_path.write_bytes(b"x")
    source = store.create_source("upload", source_path, duration_sec=60, width=1920, height=1080, status="ready")
    analysis = store.create_ai_analysis(source["id"], "mock", status="succeeded")
    seg = store.create_ai_segment(analysis["id"], {"start_sec": 0, "end_sec": 6, "title": "A"})
    plan = store.create_clip_plan(source["id"], analysis["id"], "P", segment_ids=[seg["id"]])
    preset = store.create_ffmpeg_preset("V", output_width=1080, output_height=1920, fps=30, scale_mode="cover")
    graphs: list[str] = []

    def fake_ffmpeg(args, timeout, cwd=None):
        graphs.append(args[args.index("-filter_complex") + 1])
        raise RuntimeError("stop after the base pass")

    monkeypatch.setattr("app.render._run_ffmpeg", fake_ffmpeg)
    service = ClipRenderService(store)
    service.render_clip_plan(plan["id"], ffmpeg_preset_id=preset["id"])
    store.set_clip_plan_render_settings(plan["id"], {"layout": {"mode": "single"}})
    service.render_clip_plan(plan["id"], ffmpeg_preset_id=preset["id"])
    assert graphs[0] == graphs[1] and "vstack" not in graphs[0]
    store.set_clip_plan_render_settings(plan["id"], {"layout": SPLIT})
    service.render_clip_plan(plan["id"], ffmpeg_preset_id=preset["id"])
    assert "vstack=inputs=2" in graphs[2]


# ---------------------------------------------------------------- граф ffmpeg


def test_split_graph_stacks_cam_over_reframed_gameplay():
    layout = normalize_layout(SPLIT)
    preset = _with_layout(layout)
    cam_h, _gap, game_h = split_geometry(layout, 1920)
    graph = _render_filter(1080, 1920, 30, preset, None, None, "X_EXPR")
    assert "[0:v]crop=iw*0.30000:ih*0.30000:iw*0.70000:ih*0.00000" in graph
    assert f"scale=1080:{cam_h}:force_original_aspect_ratio=increase,crop=1080:{cam_h}" in graph
    # Окно игры следует x(t) рефрейма, но высотой game_h.
    assert f"scale=1080:{game_h}:force_original_aspect_ratio=increase,crop=1080:{game_h}:X_EXPR:" in graph
    assert "[lcam][lgame]vstack=inputs=2,fps=30,format=yuv420p[vbase]" in graph
    assert graph.endswith("[vbase]copy[vout]")
    bottom = _render_filter(1080, 1920, 30, _with_layout({**SPLIT, "cam_pos": "bottom"}), None, None, None)
    assert "[lgame][lcam]vstack" in bottom


def test_split_graph_gap_border_mirror_look_and_content_crop():
    preset = _with_layout(
        {**SPLIT, "gap": 12, "border": {"color": "#FF0000", "width": 6}}, mirror=True
    )
    preset["color_style"] = "warm"
    crop = {"x": 0.0, "y": 0.125, "w": 1.0, "h": 0.75}
    graph = _render_filter(1080, 1920, 30, preset, None, crop, None)
    assert "drawbox=x=0:y=0:w=iw:h=ih:color=0xff0000:t=6" in graph
    assert "pad=1080:780:0:0:color=0xff0000" in graph  # 768 + зазор 12, вебка сверху
    # Content crop режет только геймплей; вебка берётся из всего кадра.
    assert "[0:v]crop=iw*1.00000:ih*0.75000:iw*0.00000:ih*0.12500[vsrc]" in graph
    assert "[vsrc]scale=1080:1140" in graph
    # Зеркало и лук — после сборки, на весь кадр 9:16.
    assert "[vbase]hflip,colorbalance" in graph


def test_split_graph_with_blur_background_gameplay():
    preset = _with_layout(SPLIT)
    preset["scale_mode"] = "blur_background"
    graph = _render_filter(1080, 1920, 30, preset, None, None, None)
    assert "boxblur=24:2[lgamebg]" in graph and "setsar=1[lgame]" in graph


def test_pip_graph_overlays_cam_box_with_border():
    preset = _with_layout({**PIP, "border": {"color": "#ffffff", "width": 8}})
    graph = _render_filter(1080, 1920, 30, preset, None, None, "X_EXPR")
    assert "crop=1080:1920:X_EXPR:" in graph  # обычный 9:16 с рефреймом
    assert "[0:v]crop=iw*0.25000:ih*0.30000:iw*0.00000:ih*0.70000,scale=416:-2" in graph  # 432 - 2*8
    assert "pad=iw+16:ih+16:8:8:color=0xffffff[lcam]" in graph
    assert "[lgame][lcam]overlay=x=clip(0.5000*W-w/2\\,0\\,W-w):y=clip(0.1500*H-h/2\\,0\\,H-h)" in graph
    assert "vstack" not in graph


def test_banner_goes_over_the_composed_frame(tmp_path):
    preset = _with_layout(SPLIT)
    graph = _render_filter(1080, 1920, 30, preset, {"file_path": str(tmp_path / "b.png")}, None, None)
    assert "[vbase]copy" not in graph
    assert "[vbase][banner]overlay" in graph


def test_reframe_gate_uses_gameplay_aspect():
    seg = {"start_sec": 0, "end_sec": 5, "focus": [{"t": 0, "x": 0.3}]}
    wide = {"width": 1920, "height": 1080}
    assert _segment_reframe_x(seg, _with_layout(SPLIT), wide)
    # Почти квадратный исходник: у 9:16 есть запас по ширине, у окна игры split — нет.
    squarish = {"width": 1000, "height": 1080}
    assert _segment_reframe_x(seg, dict(PRESET), squarish)
    assert _segment_reframe_x(seg, _with_layout(SPLIT), squarish) is None


# ---------------------------------------------------------------- автопоиск вебки


def test_facecam_from_faces_picks_stable_corner_face():
    stable = (0.85, 0.06, 0.06, 0.107)  # лицо стримера справа сверху
    frames = []
    for i in range(20):
        faces = []
        if i % 5 != 0:  # 16 из 20 кадров
            faces.append((stable[0] + (i % 3) * 0.002, stable[1], stable[2], stable[3]))
        faces.append((0.1 + i * 0.03, 0.4 + (i % 4) * 0.05, 0.08, 0.14))  # лица в игре гуляют
        frames.append(faces)
    found = facecam_from_faces(frames, 16 / 9)
    cam = found["cam"]
    assert cam is not None and found["confidence"] >= 0.7
    assert cam["x"] + cam["w"] == pytest.approx(1.0, abs=1e-3)  # прижата к правому краю
    assert cam["y"] == 0.0
    assert cam["x"] <= stable[0] and cam["x"] + cam["w"] >= stable[0] + stable[2]


def test_facecam_from_faces_without_stable_face():
    assert facecam_from_faces([[] for _ in range(20)], 16 / 9)["cam"] is None
    wandering = [[(0.05 * i, 0.5, 0.06, 0.1)] for i in range(18)]
    assert facecam_from_faces(wandering, 16 / 9)["cam"] is None


def test_detect_facecam_api_saves_on_source(tmp_path, monkeypatch):
    from test_api_pipeline import _client

    client, store = _client(tmp_path, monkeypatch)
    path = settings.source_dir / "vod.mp4"
    path.write_bytes(b"x")
    source = store.create_source("upload", path, duration_sec=60, width=1920, height=1080, status="ready")
    cam = {"x": 0.72, "y": 0.0, "w": 0.28, "h": 0.33}
    monkeypatch.setattr("app.stream_layout.detect_facecam", lambda src: {"cam": cam, "confidence": 0.9, "frames": 20})
    out = client.post(f"/api/sources/{source['id']}/detect-facecam").raise_for_status().json()
    assert out["cam"] == cam and out["saved"] is True
    assert store.get_source(source["id"])["facecam"] == cam
    assert client.get(f"/api/sources/{source['id']}/facecam").json() == {"cam": cam}
    client.patch(f"/api/sources/{source['id']}/facecam", json={"cam": None}).raise_for_status()
    assert store.get_source(source["id"])["facecam"] is None
    assert client.post("/api/sources/99999/detect-facecam").status_code == 404


# ---------------------------------------------------------------- файл клипа


def test_clip_spec_round_trips_layout(tmp_path, monkeypatch):
    from app.clip_spec import apply_spec, export_spec

    store = _store(tmp_path, monkeypatch)
    path = settings.source_dir / "s.mp4"
    path.write_bytes(b"x")
    source = store.create_source("upload", path, duration_sec=60, width=1920, height=1080, status="ready")
    analysis = store.create_ai_analysis(source["id"], "mock", status="succeeded")
    seg = store.create_ai_segment(analysis["id"], {"start_sec": 0, "end_sec": 6, "title": "A"})
    plan = store.create_clip_plan(source["id"], analysis["id"], "P", segment_ids=[seg["id"]])
    spec = export_spec(store, plan["id"])
    assert spec["render"]["layout"]["mode"] == "single"
    spec["render"]["layout"] = {**SPLIT, "cam_pos": "bottom", "gap": 8}
    fresh, changes = apply_spec(store, plan["id"], spec)
    assert "настройки рендера" in changes
    assert fresh["render"]["layout"] == normalize_layout({**SPLIT, "cam_pos": "bottom", "gap": 8})
    assert store.get_clip_plan(plan["id"])["render_settings"]["layout"]["mode"] == "split"


# ---------------------------------------------------------------- настоящий рендер


def _make_source(path, seconds: int = 6) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25",
            "-f", "lavfi", "-i", "sine=frequency=500:sample_rate=44100",
            "-t", str(seconds), "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac", str(path),
        ],
        check=True,
    )


def test_split_smoke_render_is_1080x1920(tmp_path):
    _require_ffmpeg()
    source = tmp_path / "src.mp4"
    _make_source(source)
    out = tmp_path / "split.mp4"
    preset = _with_layout({**SPLIT, "gap": 10, "border": {"color": "#ffffff", "width": 4}}, mirror=True)
    preset["extra_json"] = '{"crf": 35}'
    reframe = "clip((0.4)*iw-540.0\\,0\\,iw-1080)"
    args = build_ffmpeg_render_args(source, out, 0.5, 6.0, preset, source_crop=None, reframe_x=reframe)
    args[args.index("libx264") + 1 : args.index("libx264") + 1] = ["-preset", "ultrafast"]
    subprocess.run(args, check=True, capture_output=True, timeout=300)
    meta = probe_media(out)
    assert (meta.width, meta.height) == (1080, 1920)
    assert meta.duration_sec == pytest.approx(5.5, abs=0.3)


def test_pip_and_split_montage_with_transition_render(tmp_path, monkeypatch):
    _require_ffmpeg()
    store = _store(tmp_path, monkeypatch)
    path = settings.source_dir / "vod.mp4"
    _make_source(path, seconds=13)
    meta = probe_media(path)
    source = store.create_source(
        "upload", path, duration_sec=meta.duration_sec, width=meta.width, height=meta.height, status="ready"
    )
    store.update_source(source["id"], content_crop={"x": 0.0, "y": 0.05, "w": 1.0, "h": 0.9})
    analysis = store.create_ai_analysis(source["id"], "mock", status="succeeded")
    a = store.create_ai_segment(analysis["id"], {"start_sec": 0, "end_sec": 5, "title": "A"})
    b = store.create_ai_segment(analysis["id"], {"start_sec": 7, "end_sec": 12, "title": "B"})
    store.update_ai_segment_focus(a["id"], [{"t": 0, "x": 0.2}, {"t": 4, "x": 0.7}])
    plan = store.create_clip_plan(source["id"], analysis["id"], "P", segment_ids=[a["id"], b["id"]])
    preset = store.create_ffmpeg_preset(
        "Tiny", output_width=180, output_height=320, fps=25, scale_mode="cover", extra={"crf": 35}
    )
    service = ClipRenderService(store)
    for layout in (SPLIT, PIP):
        store.set_clip_plan_render_settings(
            plan["id"], {"layout": layout, "mirror": True, "transition": {"type": "fade", "duration": 0.3}}
        )
        clip = service.render_clip_plan(
            plan["id"], ffmpeg_preset_id=preset["id"], mirror=True, transition={"type": "fade", "duration": 0.3}
        )
        assert clip["status"] == "succeeded", clip.get("error")
        assert (clip["width"], clip["height"]) == (180, 320)


def test_meme_layout_puts_whole_video_on_top(tmp_path):
    """meme: горизонтальное видео целиком сверху во всю ширину, ниже — однотонный фон."""
    import subprocess

    from app.render import build_ffmpeg_render_args
    from app.stream_layout import normalize_layout

    _require_ffmpeg()
    source = tmp_path / "wide.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=white:s=320x180:r=25",
                    "-f", "lavfi", "-i", "sine=frequency=500:sample_rate=44100", "-t", "2", "-pix_fmt", "yuv420p",
                    "-c:v", "libx264", "-c:a", "aac", str(source)], check=True)
    out = tmp_path / "meme.mp4"
    preset = {"output_width": 180, "output_height": 320, "fps": 25, "scale_mode": "cover",
              "layout": normalize_layout({"mode": "meme", "video_y": 0.1, "bg": "#000000"})}
    subprocess.run(build_ffmpeg_render_args(source, out, 0, 1.5, preset), check=True)
    frame = tmp_path / "f.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "0.5", "-i", str(out), "-frames:v", "1",
                    "-f", "rawvideo", "-pix_fmt", "gray", str(frame)], check=True)
    pixels = frame.read_bytes()
    row = lambda y: pixels[y * 180:(y + 1) * 180]  # noqa: E731
    assert max(row(10)) < 40          # над видео (верх 10% = 32 px) — фон
    assert min(row(60)) > 200         # видео 180×~101 с y=32 — белое во всю ширину
    assert max(row(250)) < 40         # ниже видео — фон под картинки
