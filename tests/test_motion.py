"""Моушн-слои: нормализация, проверка, файл клипа, порядок проходов рендера, звуки слоёв."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.clip_settings import normalize_render_settings
from app.motion import (
    MAX_CAMERA_KEYS,
    MAX_LAYERS,
    clip_timeline,
    emoji_ref,
    normalize_camera,
    normalize_layers,
    plan_motion,
    schema_summary,
    validate_camera,
    validate_layers,
    visual_motion,
)
from app.motion_render import build_audio_layers_args, build_motion_mux_args, resolve_layer_assets
from app.render import ClipRenderService
from app.settings import settings
from test_render import _store


# ---------------------------------------------------------------- нормализация


def test_normalize_fills_defaults_drops_junk_and_clamps():
    layers = normalize_layers(
        [
            {
                "type": "text",
                "start": 1,
                "end": 4,
                "scale": 50,
                "opacity": -1,
                "track": 12,
                "junk": "x",
                "props": {"text": "Привет", "size": 9999, "color": "#ABC", "stroke": {"color": "red"}, "nope": 1},
                "in": "pop",
                "loop": {"type": "pulse", "speed": 99},
            },
            {"type": "hologram", "start": 0},
            "not a layer",
        ]
    )
    assert len(layers) == 1
    layer = layers[0]
    assert layer["id"].startswith("l_")
    assert "junk" not in layer and "nope" not in layer["props"]
    assert (layer["scale"], layer["opacity"], layer["track"]) == (10, 0, 7)
    assert layer["props"]["size"] == 400
    assert layer["props"]["color"] == "#aabbcc"  # #rgb прощается
    assert layer["props"]["stroke"] == {"color": "#000000", "width": 6}  # плохой цвет → умолчание
    assert layer["in"] == {"type": "pop", "duration": 0.3}
    assert layer["out"] == {"type": "none", "duration": 0.3}
    assert layer["loop"] == {"type": "pulse", "speed": 5, "amount": 1}
    assert layer["props"]["font"] == "" and layer["props"]["weight"] == 800


def test_normalize_is_idempotent_and_normalized_layers_validate_clean():
    raw = [
        {"id": "a", "type": "text", "start": 0, "end": 0.02, "props": {"text": "x"}, "in": {"type": "words"}},
        {"id": "a", "type": "shape", "start": 2, "end": 5, "props": {"kind": "rect"}, "in": {"type": "draw"}},
        {"type": "image", "start": 1, "props": {"asset_id": 7}},
        {"type": "audio", "start": 1, "end": 2, "props": {"asset_id": "sfx:boom/hit.mp3", "volume": 3}},
        {"type": "chart", "props": {"data": [{"label": "A", "value": 3}], "panel": None}},
        {"type": "callout", "props": {"title": "Т", "skew": 99, "icon": "🔥"}},
        {"type": "text", "props": {"text": "k"}, "keys": {"scale": [{"t": 0.4, "v": 1}, {"t": 0, "v": 0, "ease": "outBack"}],
                                                             "color": [{"t": 0, "v": 1}]}},
    ]
    once = normalize_layers(raw)
    assert normalize_layers(once) == once
    assert validate_layers(once) == []
    ids = [layer["id"] for layer in once]
    assert ids[0] == "a" and ids[1] != "a" and len(set(ids)) == len(ids)
    assert once[0]["end"] == 0.1  # минимальная длина
    assert once[0]["in"]["duration"] == 0.05  # не больше половины слоя
    assert once[1]["in"]["type"] == "fade"  # draw — только у line/arrow/ellipse
    assert once[2]["props"]["asset_id"] == "7"  # id файла — строкой, как в движке
    assert once[3]["props"]["volume"] == 2
    assert once[4]["props"]["panel"] is None and "glow" not in once[4]["props"]  # Opt: нет — не подставляем
    assert once[5]["props"]["skew"] == 30
    assert [p["t"] for p in once[6]["keys"]["scale"]] == [0, 0.4]
    assert "color" not in once[6]["keys"]


def test_validate_reports_every_problem_in_russian_with_raw_indices():
    problems = validate_layers(
        [
            {"type": "weird"},
            {"id": "bad id!", "type": "text", "start": 3, "end": 2, "props": {"text": ""}},
            {"type": "image", "start": 9, "props": {"asset_id": 42}},
            {"type": "shape", "props": {"fill": "red"}, "keys": {"scale": [{"t": 0, "v": 1, "ease": "wobbly"}]}},
        ],
        clip_duration=5,
        asset_exists=lambda asset_id: False,
    )
    text = "\n".join(problems)
    assert "layers[0]: тип 'weird' неизвестен" in text
    assert "layers[1] (bad id!).id" in text
    assert "нужно end ≥ start + 0.1" in text
    assert "пустой текст" in text
    assert "layers[2]: начинается на 9 с — после конца клипа" in text
    assert "файла для монтажа 42 нет" in text
    assert "layers[3].props.fill" in text and "#rrggbb" in text
    assert "'wobbly' — нет такого easing" in text


def test_limits_and_camera_normalization():
    many = [{"type": "emoji", "props": {"char": "🔥"}} for _ in range(MAX_LAYERS + 5)]
    assert len(normalize_layers(many)) == MAX_LAYERS
    assert any("не больше 60 слоёв" in p for p in validate_layers(many))

    camera = normalize_camera(
        [
            {"t": 2, "zoom": 9, "x": 2},
            {"t": 1, "zoom": 1.2, "ease": "bezier(0.22, 1.25,0.78,0.85)", "shake": 0.5},
            {"t": 2, "zoom": 1.5},
            {"zoom": 2},
        ]
    )
    assert [k["t"] for k in camera] == [1, 2]
    assert camera[0]["ease"] == "bezier(0.22,1.25,0.78,0.85)" and camera[0]["shake"] == 0.5
    assert camera[1]["zoom"] == 1.5  # на одном t остаётся последний
    assert len(normalize_camera([{"t": i} for i in range(MAX_CAMERA_KEYS + 3)])) == MAX_CAMERA_KEYS
    problems = validate_camera([{"t": 1, "ease": "bezier(2,3,0,0)"}, {"t": 9}], clip_duration=5)
    assert any("bezier: x1=2 вне 0..1" in p for p in problems)
    assert any("после конца клипа" in p for p in problems)


def test_render_settings_carry_layers_and_camera():
    s = normalize_render_settings({})
    assert s["layers"] == [] and s["camera"] == []
    s = normalize_render_settings({"layers": [{"type": "text", "props": {"text": "x"}}], "camera": [{"t": 0}]})
    assert s["layers"][0]["type"] == "text" and s["camera"][0]["zoom"] == 1


def test_helpers_motion_split_timeline_emoji_and_schema():
    motion = plan_motion(
        {"layers": [{"type": "text", "hidden": True}, {"type": "audio", "props": {"asset_id": 1}}], "camera": []}
    )
    assert [layer["type"] for layer in motion["layers"]] == ["audio"]
    assert visual_motion(motion) is None  # одни звуки — картинку не трогаем
    assert plan_motion({"layers": [{"type": "text", "hidden": True}]}) is None

    starts, total = clip_timeline([(0, 5), (20, 25)], {"type": "fade", "duration": 0.5})
    assert starts == [0, 4.5] and total == 9.5
    assert emoji_ref("🔥") == "emoji:1f525"
    assert emoji_ref("❤️") == "emoji:2764"  # FE0F без ZWJ выбрасывается
    schema = schema_summary()
    assert "text" in schema["props"] and schema["limits"]["max_layers"] == MAX_LAYERS
    assert schema["props"]["chart"]["panel"]["optional"] is True


# ---------------------------------------------------------------- команды ffmpeg


def test_audio_layers_args_delay_fade_offset_and_duck(tmp_path):
    layers = normalize_layers(
        [
            {"type": "audio", "start": 1.5, "end": 3.0,
             "props": {"asset_id": 7, "volume": 0.8, "fadeIn": 0.2, "fadeOut": 0.5, "offset": 2, "duck": True}},
            {"type": "audio", "start": 9.5, "end": 12, "props": {"asset_id": "sfx:pack/boom.mp3"}},
            {"type": "audio", "start": 11, "end": 12, "props": {"asset_id": 8}},  # после конца — пропуск
        ]
    )
    assets = {"7": "/a/seven.mp3", "sfx:pack/boom.mp3": "/p/boom.mp3", "8": "/a/eight.mp3"}
    args = build_audio_layers_args(tmp_path / "c.mp4", tmp_path / "o.mp4", layers, assets, 10.0)
    joined = " ".join(args)
    assert args[args.index("/a/seven.mp3") - 5:args.index("/a/seven.mp3")] == ["-ss", "2.000", "-t", "1.500", "-i"]
    assert "/a/eight.mp3" not in args
    graph = args[args.index("-filter_complex") + 1]
    assert "volume=0.800" in graph
    assert "afade=t=in:st=0:d=0.200" in graph and "afade=t=out:st=1.000:d=0.500" in graph
    assert "adelay=delays=1500:all=1" in graph and "adelay=delays=9500:all=1" in graph
    assert "[0:a]aresample=48000,volume=enable='between(t,1.500,3.000)':volume=0.3[a0]" in graph
    assert "amix=inputs=3:duration=first" in graph
    assert "-c:v copy" in joined and args[-1].endswith("o.mp4")

    silent = build_audio_layers_args(tmp_path / "c.mp4", tmp_path / "o.mp4", layers[:1], assets, 10.0, clip_has_audio=False)
    graph = silent[silent.index("-filter_complex") + 1]
    assert "[0:a]" not in graph and "duration=longest" in graph and "apad" in graph
    assert build_audio_layers_args(tmp_path / "c.mp4", tmp_path / "o.mp4", layers, {}, 10.0) is None


def test_mux_args_copy_video_without_banner_and_overlay_with_banner(tmp_path):
    plain = build_motion_mux_args(tmp_path / "m.mp4", tmp_path / "b.mp4", tmp_path / "o.mp4", {}, None)
    assert plain[plain.index("-map") + 1] == "0:v:0" and "copy" in plain
    assert "1:a:0?" in plain
    banner = {"file_path": str(tmp_path / "banner.png"), "position": "bottom", "opacity": 1}
    over = build_motion_mux_args(
        tmp_path / "m.mp4", tmp_path / "b.mp4", tmp_path / "o.mp4",
        {"output_height": 1920, "banner_y_frac": 0.1, "extra_json": {"crf": 20}}, banner,
    )
    graph = over[over.index("-filter_complex") + 1]
    assert graph.startswith("[1:v]format=rgba") and "[0:v][banner]overlay=" in graph and ":192:" in graph
    assert "2:a:0?" in over and over[over.index("-crf") + 1] == "20"


def test_resolve_assets_skips_missing_and_uses_pack_resolver(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    png = tmp_path / "sticker.png"
    png.write_bytes(b"png")
    asset = store.create_montage_asset(kind="image", label="s", file_path=str(png))
    sfx = tmp_path / "boom.mp3"
    sfx.write_bytes(b"mp3")
    monkeypatch.setattr("app.motion_render.resolve_pack_ref", lambda ref: sfx if ref.startswith("sfx:") else None)
    layers = normalize_layers(
        [
            {"id": "img", "type": "image", "props": {"asset_id": asset["id"]}},
            {"id": "gone", "type": "image", "props": {"asset_id": 999}},
            {"id": "snd", "type": "audio", "props": {"asset_id": "sfx:p/boom.mp3"}},
            {"id": "emo", "type": "emoji", "props": {"char": "🔥"}},
            {"id": "call", "type": "callout", "props": {"title": "x", "icon": "🔥"}},
        ]
    )
    kept, assets, notes = resolve_layer_assets(store, layers)
    assert [layer["id"] for layer in kept] == ["img", "snd", "call"]
    assert assets == {str(asset["id"]): str(png), "sfx:p/boom.mp3": str(sfx)}
    assert any("gone" in n and "999" in n for n in notes)
    assert any("emo" in n and "emoji:1f525" in n for n in notes)
    assert any("call" in n and "значок" in n for n in notes)


# ---------------------------------------------------------------- порядок проходов рендера


class _Meta:
    duration_sec = 5.0
    width = 180
    height = 320
    fps = 25.0
    raw: dict = {}


def _render_fixture(tmp_path, monkeypatch, *, compositor_fails: bool = False):
    """Клип из одного куска, баннер и субтитры; ffmpeg/компоновщик подменены записью вызовов."""
    store = _store(tmp_path, monkeypatch)
    source_path = settings.source_dir / "s.mp4"
    source_path.write_bytes(b"src")
    source = store.create_source("upload", source_path, duration_sec=30, width=1280, height=720, status="ready")
    analysis = store.create_ai_analysis(source["id"], "mock", status="succeeded")
    seg = store.create_ai_segment(analysis["id"], {"start_sec": 0, "end_sec": 5, "title": "A"})
    plan = store.create_clip_plan(source["id"], analysis["id"], "План", segment_ids=[seg["id"]])
    banner_path = settings.banner_dir / "banner.png"
    banner_path.write_bytes(b"png")
    banner = store.create_banner("B", banner_path)
    preset = store.create_ffmpeg_preset("P", output_width=180, output_height=320, fps=25)
    profile = store.create_subtitle_profile("Subs", provider="mock")
    calls: list[tuple[str, list[str]]] = []
    compositor_file = tmp_path / "motion-render.mjs"
    compositor_file.write_text("// fake", encoding="utf-8")
    monkeypatch.setattr(settings, "motion_compositor", compositor_file)
    monkeypatch.setattr(settings, "motion_node", shutil.which("python") or __import__("sys").executable)
    comps: list[dict] = []

    def fake_ffmpeg(args, timeout, cwd=None):
        calls.append(("ffmpeg", list(args)))
        Path(args[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(args[-1]).write_bytes(b"video")

    def fake_compositor(args, timeout):
        calls.append(("compositor", list(args)))
        comps.append(json.loads(Path(args[args.index("--comp") + 1]).read_text(encoding="utf-8")))
        if compositor_fails:
            raise RuntimeError("canvas exploded")
        Path(args[args.index("--output") + 1]).write_bytes(b"motion")

    def fake_subs(*, clip_id, input_path, output_path, subtitle_profile_id, preset):
        calls.append(("subtitles", [str(input_path), str(output_path)]))
        Path(output_path).write_bytes(b"subbed")

    service = ClipRenderService(store)
    monkeypatch.setattr("app.render._run_ffmpeg", fake_ffmpeg)
    monkeypatch.setattr("app.render._run_compositor", fake_compositor)
    monkeypatch.setattr("app.render.probe_media", lambda path: _Meta())
    monkeypatch.setattr("app.render.run_qc", lambda *a, **k: None)
    monkeypatch.setattr("app.render._has_audio_stream", lambda path: True)
    monkeypatch.setattr(service, "_render_subtitles", fake_subs)
    kwargs = {"ffmpeg_preset_id": preset["id"], "banner_id": banner["id"], "subtitle_profile_id": profile["id"]}
    return store, service, plan, banner_path, calls, comps, kwargs


def test_motion_pass_runs_before_banner_and_subtitles(tmp_path, monkeypatch):
    store, service, plan, banner_path, calls, comps, kwargs = _render_fixture(tmp_path, monkeypatch)
    store.set_clip_plan_render_settings(
        plan["id"],
        {
            "layers": [{"id": "t1", "type": "text", "start": 1, "end": 3, "props": {"text": "БУМ"}},
                       {"id": "h", "type": "text", "hidden": True, "props": {"text": "скрыт"}}],
            "camera": [{"t": 1, "zoom": 1}, {"t": 1.3, "zoom": 1.4}],
        },
    )
    clip = service.render_clip_plan(plan["id"], **kwargs)

    assert clip["status"] == "succeeded", clip.get("error")
    assert not clip.get("error")
    assert [kind for kind, _ in calls] == ["ffmpeg", "compositor", "ffmpeg", "subtitles"]
    base_args = calls[0][1]
    assert str(banner_path) not in base_args  # баннер не в основе — иначе камера его зумит
    comp_args = calls[1][1]
    assert comp_args[comp_args.index("--input") + 1] == base_args[-1]
    assert comp_args[comp_args.index("--crf") + 1] == "18"
    assert "--fonts-dir" in comp_args and "--assets" in comp_args
    assert comps[0]["width"] == 180 and comps[0]["fps"] == 25 and comps[0]["duration"] == 5
    assert [layer["id"] for layer in comps[0]["layers"]] == ["t1"]  # скрытый не рисуется
    assert len(comps[0]["camera"]) == 2
    mux = calls[2][1]
    assert str(banner_path) in mux and "[0:v][banner]overlay" in mux[mux.index("-filter_complex") + 1]
    assert mux[mux.index("-i") + 1].endswith("motion.mp4")
    assert calls[3][1][0] == base_args[-1]  # субтитры жгутся по видео уже с камерой, слоями и баннером


def test_compositor_failure_keeps_clip_with_banner_and_says_why(tmp_path, monkeypatch):
    store, service, plan, banner_path, calls, _comps, kwargs = _render_fixture(
        tmp_path, monkeypatch, compositor_fails=True
    )
    store.set_clip_plan_render_settings(plan["id"], {"camera": [{"t": 0, "zoom": 2}]})
    clip = service.render_clip_plan(plan["id"], **kwargs)

    assert clip["status"] == "succeeded"
    assert "моушн не наложен" in clip["error"] and "canvas exploded" in clip["error"]
    assert [kind for kind, _ in calls] == ["ffmpeg", "compositor", "ffmpeg", "subtitles"]
    mux = calls[2][1]
    assert str(banner_path) in mux and mux[mux.index("-i") + 1] == calls[0][1][-1]  # баннер на основу


def test_missing_compositor_keeps_clip(tmp_path, monkeypatch):
    store, service, plan, _banner, calls, _comps, kwargs = _render_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "motion_compositor", tmp_path / "nope.mjs")
    store.set_clip_plan_render_settings(plan["id"], {"layers": [{"type": "emoji", "props": {"char": "x"}}],
                                                     "camera": [{"t": 0, "zoom": 2}]})
    clip = service.render_clip_plan(plan["id"], **kwargs)
    assert clip["status"] == "succeeded"
    assert "npm run build:motion" in clip["error"]
    assert "compositor" not in [kind for kind, _ in calls]


def test_no_layers_no_camera_means_no_change(tmp_path, monkeypatch):
    store, service, plan, banner_path, calls, _comps, kwargs = _render_fixture(tmp_path, monkeypatch)
    store.set_clip_plan_render_settings(plan["id"], {"layers": [{"type": "text", "hidden": True}], "camera": []})
    clip = service.render_clip_plan(plan["id"], **kwargs)
    with_hidden = [(kind, [a for a in args if "clips" not in a]) for kind, args in calls]

    calls.clear()
    store.set_clip_plan_render_settings(plan["id"], {})
    service.render_clip_plan(plan["id"], **kwargs)
    plain = [(kind, [a for a in args if "clips" not in a]) for kind, args in calls]

    assert clip["status"] == "succeeded" and not clip.get("error")
    assert [kind for kind, _ in calls] == ["ffmpeg", "subtitles"]
    assert str(banner_path) in calls[0][1]  # баннер, как раньше, в основе
    assert with_hidden == plain


def test_audio_only_layers_mix_last_and_keep_banner_in_base(tmp_path, monkeypatch):
    store, service, plan, banner_path, calls, _comps, kwargs = _render_fixture(tmp_path, monkeypatch)
    sound = tmp_path / "boom.mp3"
    sound.write_bytes(b"mp3")
    asset = store.create_montage_asset(kind="audio", label="boom", file_path=str(sound), has_audio=1)
    store.set_clip_plan_render_settings(
        plan["id"],
        {"layers": [{"type": "audio", "start": 1, "end": 2, "props": {"asset_id": asset["id"], "duck": True}},
                    {"id": "lost", "type": "audio", "start": 2, "props": {"asset_id": 999}}]},
    )
    clip = service.render_clip_plan(plan["id"], **kwargs)

    assert clip["status"] == "succeeded"
    assert [kind for kind, _ in calls] == ["ffmpeg", "subtitles", "ffmpeg"]
    assert str(banner_path) in calls[0][1]
    sfx = calls[2][1]
    assert str(sound) in sfx and "adelay=delays=1000:all=1" in sfx[sfx.index("-filter_complex") + 1]
    assert "lost" in clip["error"]  # пропавший файл — пометка, а не сбой


# ---------------------------------------------------------------- файл клипа и API


def test_clip_file_round_trip_and_rejects_broken_layers(tmp_path, monkeypatch):
    from test_api_pipeline import _require_ffmpeg
    from test_clip_spec import _setup

    _require_ffmpeg()
    client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    spec = client.get(f"/api/clip-plans/{plan['id']}/spec").raise_for_status().json()
    assert spec["render"]["layers"] == [] and spec["render"]["camera"] == []

    spec["render"]["layers"] = [{"id": "title", "type": "text", "start": 0.5, "end": 3, "props": {"text": "Хук"}}]
    spec["render"]["camera"] = [{"t": 1, "zoom": 1.3, "ease": "outBack"}]
    out = client.put(f"/api/clip-plans/{plan['id']}/spec", json=spec).raise_for_status().json()
    assert "настройки рендера" in out["changes"]
    fresh = out["spec"]
    assert fresh["render"]["layers"][0]["id"] == "title"
    assert fresh["render"]["layers"][0]["props"]["text"] == "Хук"
    assert fresh["render"]["camera"][0]["zoom"] == 1.3
    # Экспорт → PUT без правок ничего не меняет (форма стабильна).
    again = client.put(f"/api/clip-plans/{plan['id']}/spec", json=fresh).raise_for_status().json()
    assert again["changes"] == []

    broken = json.loads(json.dumps(fresh))
    broken["render"]["layers"].append({"type": "text", "start": 4, "end": 1, "props": {"text": ""}})
    broken["render"]["camera"].append({"t": 2, "zoom": 10})
    resp = client.put(f"/api/clip-plans/{plan['id']}/spec", json=broken)
    assert resp.status_code == 422
    problems = resp.json()["detail"]["problems"]
    assert any(p.startswith("render.layers[1].end") for p in problems)
    assert any(p.startswith("render.camera[1].zoom") for p in problems)
    assert len(store.get_clip_plan(plan["id"])["render_settings"]["layers"]) == 1  # ничего не записано

    # Ctrl+Z возвращает слои вместе с остальными настройками.
    client.patch(f"/api/clip-plans/{plan['id']}/render-settings", json={"settings": {**fresh["render"], "layers": []}})
    assert store.get_clip_plan(plan["id"])["render_settings"]["layers"] == []
    client.post(f"/api/clip-plans/{plan['id']}/undo").raise_for_status()
    assert store.get_clip_plan(plan["id"])["render_settings"]["layers"][0]["id"] == "title"


def test_motion_api_schema_validate_and_frame_without_compositor(tmp_path, monkeypatch):
    from test_api_pipeline import _client

    client, store = _client(tmp_path, monkeypatch)
    schema = client.get("/api/motion/schema").raise_for_status().json()
    assert "bezier" in schema["custom_easing"] and "callout" in schema["props"]

    source_path = settings.source_dir / "s.mp4"
    source_path.write_bytes(b"src")
    source = store.create_source("upload", source_path, duration_sec=30, width=1280, height=720, status="ready")
    analysis = store.create_ai_analysis(source["id"], "mock", status="succeeded")
    seg = store.create_ai_segment(analysis["id"], {"start_sec": 0, "end_sec": 6, "title": "A"})
    plan = store.create_clip_plan(source["id"], analysis["id"], "План", segment_ids=[seg["id"]])

    body = {"layers": [{"type": "image", "start": 8, "props": {"asset_id": 5}}], "camera": [{"t": 7}]}
    res = client.post(f"/api/clip-plans/{plan['id']}/layers/validate", json=body).raise_for_status().json()
    assert res["clip_duration"] == 6
    assert any("после конца клипа" in p for p in res["problems"])
    assert any("файла для монтажа 5 нет" in p for p in res["problems"])
    assert res["layers"][0]["props"]["asset_id"] == "5" and res["camera"][0]["t"] == 7
    assert client.post("/api/clip-plans/999/layers/validate").status_code == 404

    monkeypatch.setattr(settings, "motion_compositor", tmp_path / "missing.mjs")
    frame = client.post(f"/api/clip-plans/{plan['id']}/motion/frame?t=1")
    assert frame.status_code == 503 and "build:motion" in frame.json()["detail"]


# ---------------------------------------------------------------- настоящий компоновщик


def _real_compositor() -> Path:
    path = settings.root_dir / "node" / "motion-render.mjs"
    if not path.exists():
        pytest.skip("node/motion-render.mjs не собран (npm run build:motion)")
    for tool in ("node", "ffmpeg"):
        if not shutil.which(tool):
            pytest.skip(f"{tool} недоступен")
    return path


def test_real_compositor_renders_text_layer_and_camera(tmp_path, monkeypatch):
    compositor = _real_compositor()
    store = _store(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "motion_compositor", compositor)
    monkeypatch.setattr(settings, "motion_node", "node")
    source_path = settings.source_dir / "motion-src.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=size=320x180:rate=25",
         "-f", "lavfi", "-i", "sine=frequency=500:sample_rate=44100",
         "-t", "7", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-c:a", "aac", str(source_path)],
        check=True,
    )
    from app.ingest import probe_media

    meta = probe_media(source_path)
    source = store.create_source("upload", source_path, duration_sec=meta.duration_sec, width=meta.width,
                                 height=meta.height, fps=meta.fps, status="ready")
    analysis = store.create_ai_analysis(source["id"], "mock", status="succeeded")
    # Кусок короче 5 с не бывает (MIN_SEGMENT_DURATION_SEC) — рендерим минимальный.
    seg = store.create_ai_segment(analysis["id"], {"start_sec": 0, "end_sec": 5, "title": "A"})
    plan = store.create_clip_plan(source["id"], analysis["id"], "Моушн", segment_ids=[seg["id"]])
    preset = store.create_ffmpeg_preset("Small", output_width=180, output_height=320, fps=25, extra={"crf": 30})
    store.set_clip_plan_render_settings(
        plan["id"],
        {
            "layers": [{"type": "text", "start": 0.5, "end": 2, "props": {"text": "ПРИВЕТ", "size": 140},
                        "in": {"type": "pop", "duration": 0.3}}],
            "camera": [{"t": 0.5, "zoom": 1}, {"t": 1.0, "zoom": 1.5, "x": 0.5, "y": 0.4}],
        },
    )
    clip = ClipRenderService(store).render_clip_plan(plan["id"], ffmpeg_preset_id=preset["id"])

    assert clip["status"] == "succeeded", clip.get("error")
    assert "моушн не наложен" not in (clip.get("error") or ""), clip.get("error")
    out = probe_media(Path(clip["output_path"]))
    assert abs(out.duration_sec - 5.0) < 0.3
    assert (out.width, out.height) == (180, 320)


# ---------------------------------------------------------------- анимированные субтитры


def test_captions_layer_keeps_preset_defaults_open_and_sits_above_the_ui():
    from app.motion import DEFAULT_CAPTIONS_Y, MAX_TIME_SEC, layer_asset_refs

    [layer] = normalize_layers([{"type": "captions", "start": 0, "props": {"emoji": [{"i": 3, "char": "🔥"}]}}])
    assert layer["props"]["style"] == "hormozi"
    # Не заданное не заполняется умолчаниями — иначе пресет стиля не сработал бы.
    assert set(layer["props"]) == {"style", "emoji"}
    assert layer["y"] == DEFAULT_CAPTIONS_Y
    assert layer["end"] == MAX_TIME_SEC  # без end — до конца клипа
    assert layer_asset_refs(layer) == ["emoji:1f525"]

    problems = validate_layers(
        [{"type": "captions", "end": 9, "y": 0.5, "props": {
            "style": "tiktok", "maxWords": 12, "wordScale": 2, "animation": "spin",
            "activeBg": None, "stroke": None, "highlightWords": [{"i": 2, "color": "#00ff00"}],
        }}]
    )
    assert any("tiktok" in p for p in problems)
    assert any("maxWords" in p for p in problems)
    assert any("wordScale" in p for p in problems)
    assert any("animation" in p for p in problems)
    [clean] = normalize_layers([{"type": "captions", "end": 9, "y": 0.5, "props": {
        "maxWords": 12, "activeBg": None, "stroke": None, "highlightWords": [{"i": 2, "color": "#00FF00"}]}}])
    assert clean["y"] == 0.5 and clean["end"] == 9
    assert clean["props"]["maxWords"] == 8
    assert clean["props"]["activeBg"] is None and clean["props"]["stroke"] is None  # null — «выключено»
    assert clean["props"]["highlightWords"] == [{"i": 2, "color": "#00ff00"}]
    assert "captions" in schema_summary()["props"] and schema_summary()["captions"]["styles"][0] == "hormozi"


def test_caption_words_from_subtitles_and_transition_shift(tmp_path, monkeypatch):
    from app.motion import caption_words, has_captions
    from app.motion_render import attach_caption_words, plan_caption_words

    assert caption_words({"words": [{"word": " Привет ", "start": 1.2, "end": 1.5}, {"word": "", "start": 2, "end": 3},
                                    {"word": "мир", "start": "x", "end": 1}, {"word": "раз", "start": 0.4, "end": 0.6}]}) == [
        {"t0": 0.4, "t1": 0.6, "w": "раз"}, {"t0": 1.2, "t1": 1.5, "w": "Привет"},
    ]
    assert caption_words(None) == []
    assert has_captions([{"type": "captions"}]) and not has_captions([{"type": "captions", "hidden": True}])

    store = _store(tmp_path, monkeypatch)
    source = store.create_source("upload", settings.source_dir / "s.mp4", duration_sec=60, status="ready")
    analysis = store.create_ai_analysis(source["id"], "mock", status="succeeded")
    a = store.create_ai_segment(analysis["id"], {"start_sec": 0, "end_sec": 5, "title": "A"})
    b = store.create_ai_segment(analysis["id"], {"start_sec": 20, "end_sec": 25, "title": "B"})
    plan = store.create_clip_plan(source["id"], analysis["id"], "План", segment_ids=[a["id"], b["id"]])
    store.set_clip_plan_render_settings(plan["id"], {"transition": {"type": "fade", "duration": 0.5}})
    motion = {"layers": [{"type": "captions", "props": {}}], "camera": []}

    out = attach_caption_words(store, store.get_clip_plan(plan["id"]), motion)
    assert "words" not in out and "нет сохранённых субтитров" in out["caption_notes"][0]

    store.set_clip_plan_subtitles(plan["id"], {"lines": [{"start": 1, "end": 7, "text": "раз два"}],
                                               "words": [{"word": "раз", "start": 1, "end": 1.4},
                                                         {"word": "два", "start": 6, "end": 6.5}]})
    fresh = store.get_clip_plan(plan["id"])
    # Второе слово — во втором куске: переход съедает 0.5 с, как у прожигаемых субтитров.
    assert plan_caption_words(store, fresh) == [{"t0": 1.0, "t1": 1.4, "w": "раз"}, {"t0": 5.5, "t1": 6.0, "w": "два"}]
    assert attach_caption_words(store, fresh, motion)["words"][1]["t0"] == 5.5
    text_only = {"layers": [{"type": "text", "props": {"text": "x"}}], "camera": []}
    assert attach_caption_words(store, fresh, text_only) is text_only  # без субтитров-слоя ничего не трогаем


def test_captions_layer_gets_words_and_replaces_burned_subtitles(tmp_path, monkeypatch):
    store, service, plan, _banner, calls, comps, kwargs = _render_fixture(tmp_path, monkeypatch)
    store.set_clip_plan_render_settings(plan["id"], {"layers": [{"id": "cap", "type": "captions", "props": {"style": "box"}}]})
    store.set_clip_plan_subtitles(plan["id"], {"lines": [{"start": 0.5, "end": 2, "text": "всем привет"}],
                                               "words": [{"word": "всем", "start": 0.5, "end": 1},
                                                         {"word": "привет", "start": 1, "end": 2}]})
    clip = service.render_clip_plan(plan["id"], **kwargs)

    assert clip["status"] == "succeeded", clip.get("error")
    assert comps[0]["words"] == [{"t0": 0.5, "t1": 1.0, "w": "всем"}, {"t0": 1.0, "t1": 2.0, "w": "привет"}]
    assert comps[0]["layers"][0]["props"] == {"style": "box"}
    # Субтитры-слой вместо прожигаемых: второго текста поверх нет.
    assert "subtitles" not in [kind for kind, _ in calls]


def test_captions_without_saved_subtitles_keep_burned_ones_and_say_why(tmp_path, monkeypatch):
    store, service, plan, _banner, calls, comps, kwargs = _render_fixture(tmp_path, monkeypatch)
    store.set_clip_plan_render_settings(plan["id"], {"layers": [{"type": "captions"}]})
    clip = service.render_clip_plan(plan["id"], **kwargs)

    assert clip["status"] == "succeeded"
    assert "words" not in comps[0]
    assert "нет сохранённых субтитров" in clip["error"]
    assert "subtitles" in [kind for kind, _ in calls]
