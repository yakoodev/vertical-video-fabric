"""Мем на холсте одним монтажом (клип 582): звук video-слоя, режим «дорожка» у audio,
отрезок видео по ссылке, автофокус окна video-слоя, стоп-кадр в конце на клип."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

from app import motion
from app.layer_crop import crop_keys_from_track, crop_window, parse_aspect, track_shots
from app.motion_render import TRACK_EDGE_FADE, build_audio_layers_args, video_sound_item
from app.settings import settings


def _graph(args: list[str]) -> str:
    return args[args.index("-filter_complex") + 1]


# ---------------------------------------------------------------- 1–2. схема звука слоёв


def test_video_audio_props_are_optional_and_old_layers_unchanged():
    [old] = motion.normalize_layers([{"type": "video", "start": 0, "end": 2, "props": {"asset_id": 7}}])
    assert not {"audio", "volume", "gain_db"} & set(old["props"])  # старые клипы — немые, как были
    assert not motion.has_layer_sound(old)
    [loud] = motion.normalize_layers([{"type": "video", "start": 0, "end": 2,
                                       "props": {"asset_id": 7, "audio": True, "volume": 1.5, "gain_db": -6}}])
    assert loud["props"]["audio"] is True and motion.has_layer_sound(loud) and motion.is_track_audio(loud)
    problems = motion.validate_layers([{"type": "video", "start": 0, "end": 2,
                                        "props": {"asset_id": 7, "audio": True, "volume": 3, "gain_db": 20}}])
    assert any("volume" in p for p in problems) and any("gain_db" in p for p in problems)
    assert motion.has_layer_sound({**loud, "hidden": True}) is False


def test_audio_mode_track_and_effect_volume_agree_with_render():
    effect, track = motion.normalize_layers([
        {"type": "audio", "start": 0, "end": 1, "props": {"asset_id": 3, "volume": 1.6, "gain_db": 4}},
        {"type": "audio", "start": 0, "end": 1, "props": {"asset_id": 3, "mode": "track", "volume": 1.6, "gain_db": 4}},
    ])
    # Эффект: громче 1 рендер не делает — схема режет так же и говорит; gain_db у эффекта нет.
    assert effect["props"]["volume"] == 1 and "gain_db" not in effect["props"] and "mode" not in effect["props"]
    problems = motion.validate_layers([{"type": "audio", "start": 0, "end": 1,
                                        "props": {"asset_id": 3, "volume": 1.6, "gain_db": 4}}])
    assert any("не больше 1" in p and "track" in p for p in problems) and any("gain_db" in p for p in problems)
    assert track["props"]["mode"] == "track" and track["props"]["volume"] == 1.6 and track["props"]["gain_db"] == 4
    assert motion.track_gain(track["props"]) == pytest.approx(1.6 * 10 ** (4 / 20), rel=1e-6)
    assert motion.track_gain({"volume": 2, "gain_db": 12}) == pytest.approx(10 ** (12 / 20))  # потолок +12 дБ
    assert motion.validate_layers([track]) == [] and motion.normalize_layers([track]) == [track]
    summary = motion.schema_summary()
    assert summary["audio"]["modes"] == ["effect", "track"] and "props.audio true" in summary["video"]["audio"]


def test_track_audio_layer_skips_auto_level_and_gets_edge_fades(tmp_path, monkeypatch):
    import app.motion_render as mr

    monkeypatch.setattr(mr, "audio_levels", lambda *a, **k: (-40.0, -20.0))  # тихий файл: эффект подняли бы
    layers = motion.normalize_layers([
        {"type": "audio", "start": 0, "end": 4, "props": {"asset_id": 1, "mode": "track", "gain_db": -6}},
        {"type": "audio", "start": 0, "end": 1, "props": {"asset_id": 2, "volume": 0.5}},
    ])
    args = build_audio_layers_args(tmp_path / "c.mp4", tmp_path / "o.mp4", layers, {"1": "/a/song.wav", "2": "/a/fx.wav"},
                                   10.0, speech_db=-14.0)
    graph = _graph(args)
    track_chain = graph.split(";")[0]
    assert f"volume={10 ** (-6 / 20):.4f}" in track_chain  # ровно gain_db, без подгонки под голос
    assert f"afade=t=in:st=0:d={TRACK_EDGE_FADE:.3f}" in track_chain and "afade=t=out:st=3.985" in track_chain
    fx_chain = graph.split(";")[1]
    # Эффект подогнан: громкие места −40 → голос −14 − 10 = −24 → +16 дБ, потолок подъёма +12, × 0.5.
    assert f"volume={0.5 * 10 ** (12 / 20):.4f}" in fx_chain and "afade" not in fx_chain


# ---------------------------------------------------------------- 1. звук video-слоя в миксе


def _video(**props):
    return motion.normalize_layers([{"id": "v", "type": "video", "start": 2, "end": 6,
                                     "props": {"asset_id": 5, "audio": True, **props}}])[0]


def test_video_sound_item_follows_from_speed_loop_hold():
    item, note = video_sound_item(_video(**{"from": 1.5}), "/v.mp4", 30.0, 20.0)
    assert note is None and item["offset"] == 1.5 and item["start"] == 2 and item["len"] == 4
    assert item["read"] == 4 and item["tempo"] == 1 and item["loop"] is None and item["volume"] == 1
    # Файл кончается раньше слоя (без loop): звук — до конца файла, дальше тишина (freeze держит только кадр).
    item, _ = video_sound_item(_video(**{"from": 18.0}), "/v.mp4", 30.0, 20.0)
    assert item["len"] == pytest.approx(20.0 - 0.04 - 18.0, abs=1e-6)
    # loop: петля от from до конца файла, длина — весь слой.
    item, _ = video_sound_item(_video(**{"from": 18.0, "loop": True}), "/v.mp4", 30.0, 20.0)
    assert item["loop"] == pytest.approx(1.96) and item["read"] is None and item["len"] == 4
    # speed 1.5: читаем 6 с файла за 4 с клипа.
    item, _ = video_sound_item(_video(speed=1.5), "/v.mp4", 30.0, 20.0)
    assert item["tempo"] == 1.5 and item["read"] == pytest.approx(6.0)
    # Слой за концом клипа обрезается по клипу.
    item, _ = video_sound_item(_video(), "/v.mp4", 4.0, 20.0)
    assert item["len"] == 2
    # hold — стоп-кадр без звука; speed вне 0.5..2 — тишина с пометкой; нет дорожки — пометка.
    assert video_sound_item(_video(hold=True), "/v.mp4", 30.0, 20.0) == (None, None)
    item, note = video_sound_item(_video(speed=3), "/v.mp4", 30.0, 20.0)
    assert item is None and "speed 3" in note
    item, note = video_sound_item(_video(), "/v.mp4", 30.0, 20.0, has_audio=False)
    assert item is None and "нет звуковой дорожки" in note


def test_audio_args_mix_video_layers_with_atempo_aloop_delay(tmp_path):
    layers = [
        _video(**{"from": 0.73, "gain_db": -6}),
        {**_video(speed=1.5), "id": "fast", "start": 10, "end": 12},
        {**_video(**{"from": 1.0, "loop": True}), "id": "loop", "start": 12, "end": 20},
        {**_video(hold=True), "id": "hold"},
        motion.normalize_layers([{"type": "video", "start": 0, "end": 3, "props": {"asset_id": 5}}])[0],  # немое
    ]
    notes: list[str] = []
    probed = []
    args = build_audio_layers_args(tmp_path / "c.mp4", tmp_path / "o.mp4", layers, {"5": "/v/song.mp4"}, 30.0,
                                   clip_has_audio=False, probe=lambda p: probed.append(p) or (5.0, True), notes=notes)
    assert len(probed) == 1  # один файл — один замер
    assert args.count("/v/song.mp4") == 3  # hold и немое — без звука
    inputs = [i for i, a in enumerate(args) if a == "/v/song.mp4"]
    assert args[inputs[0] - 5:inputs[0]] == ["-ss", "0.730", "-t", "4.000", "-i"]
    assert args[inputs[1] - 3:inputs[1]] == ["-t", "3.000", "-i"]  # 2 с клипа × 1.5
    assert args[inputs[2] - 3:inputs[2]] == ["-ss", "1.000", "-i"]  # петля: без -t
    chains = _graph(args).split(";")
    assert f"volume={10 ** (-6 / 20):.4f}" in chains[0] and "adelay=delays=2000:all=1" in chains[0]
    assert f"afade=t=in:st=0:d={TRACK_EDGE_FADE:.3f}" in chains[0]
    assert "atempo=1.5000" in chains[1] and "atrim=0:2.000" in chains[1] and "adelay=delays=10000" in chains[1]
    assert "aloop=loop=-1:size=190080" in chains[2] and "atrim=0:8.000" in chains[2]  # (5 − 0.04 − 1) × 48000
    assert "amix=inputs=3:duration=longest" in chains[-1] and "apad" in chains[-1]
    assert notes == []


def _rms(path: Path, start: float, end: float) -> float:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{start}", "-to", f"{end}", "-i", str(path), "-vn",
         "-af", "astats=measure_overall=RMS_level:measure_perchannel=none", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    found = re.findall(r"RMS level dB: (-?[\d.]+|-inf)", proc.stderr)
    return float(found[-1]) if found and found[-1] != "-inf" else -200.0


def test_real_ffmpeg_video_layer_sound_lands_at_start(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("нет ffmpeg")
    clip = tmp_path / "canvas.mp4"  # холст: видео без звука
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=108x192:r=30:d=4",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(clip)], check=True)
    song = tmp_path / "song.mp4"  # ролик со звуком: тишина 1 с, потом тон
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=white:s=160x120:r=30:d=3",
                    "-f", "lavfi", "-i", "sine=f=440:r=48000:d=3",
                    "-af", "volume=enable='lt(t,1)':volume=0", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(song)], check=True)
    layer = motion.normalize_layers([{"id": "v", "type": "video", "start": 1.0, "end": 3.0,
                                      "props": {"asset_id": 9, "from": 1.0, "audio": True}}])[0]
    out = tmp_path / "out.mp4"
    args = build_audio_layers_args(clip, out, [layer], {"9": str(song)}, 4.0, clip_has_audio=False,
                                   probe=lambda p: (3.0, True))
    subprocess.run(args, check=True)
    assert _rms(out, 0.1, 0.9) < -60  # до start слоя — тишина
    assert _rms(out, 1.2, 2.8) > -30  # тон с секунды from=1 файла — сразу на start
    assert _rms(out, 3.3, 3.9) < -60  # слой кончился


# ---------------------------------------------------------------- 4. автофокус окна video-слоя


def test_crop_window_and_aspect_parse():
    assert parse_aspect(None) == pytest.approx(9 / 16) and parse_aspect("4:5") == 0.8 and parse_aspect(0.5) == 0.5
    with pytest.raises(ValueError):
        parse_aspect("abc")
    assert crop_window(1440, 1080, 9 / 16) == ("x", 0.4219, 1.0)  # 4:3 → окно 0.421875 ширины (клип 582)
    assert crop_window(1080, 1920, 9 / 16)[0] is None
    axis, w, h = crop_window(1080, 1920, 1.0)  # высокое видео в квадрат — ведём cropY
    assert axis == "y" and w == 1.0 and h == pytest.approx(0.5625)


def _track(shots: list[tuple[float, float, list[float]]], step: float = 1 / 3) -> list[dict]:
    """Синтетическая дорожка autofocus: [(t0, t1, [x на старте, x в конце])] — линейно, cut на стыках."""
    points = []
    for index, (t0, t1, (x0, x1)) in enumerate(shots):
        t = t0
        first = True
        while t < t1 - 1e-9:
            x = x0 + (x1 - x0) * (t - t0) / max(1e-9, t1 - t0)
            point = {"t": round(t, 3), "x": round(x, 4), "y": 0.5}
            if first and index:
                point["cut"] = True
            points.append(point)
            first = False
            t += step
    return points


def test_crop_keys_step_on_cuts_and_smooth_inside_shot():
    window = 0.421875
    points = _track([(0, 3, [0.3, 0.3]), (3, 7, [0.3, 0.7]), (7, 9, [0.8, 0.8])])
    # ffmpeg нашёл склейки точнее проб: 2.9 и 6.8 (секунды файла от from).
    out = crop_keys_from_track(points, axis="x", window=window, layer_len=9.0, scene_times=[2.9, 6.8, 8.5])
    keys = out["keys"]
    assert out["shots"] == 3
    times = [k["t"] for k in keys]
    assert times == sorted(times) and times[0] == 0
    assert 2.9 in times and 6.8 in times  # ключ ровно на склейке
    before_cut = [k for k in keys if k["t"] < 2.9][-1]
    assert before_cut["ease"] == "step" and before_cut["v"] == pytest.approx(0.3 - window / 2, abs=1e-3)
    assert [k for k in keys if k["t"] < 6.8][-1]["ease"] == "step"
    # Внутри второго плана окно ведётся (несколько ключей, линейно), а не стоит.
    inside = [k for k in keys if 2.9 <= k["t"] < 6.8]
    assert len(inside) >= 2 and inside[0]["ease"] == "linear" and inside[-1]["v"] > inside[0]["v"] + 0.2
    # Последний план у правого края: окно прижато (0..1 − w).
    assert keys[-1]["v"] == pytest.approx(1 - window, abs=1e-4)
    # Ровный план — одна рамка, без дрожи; прямая линия — два ключа (RDP).
    assert len([k for k in keys if k["t"] < 2.9]) == 1
    assert len(inside) == 2


def test_crop_keys_respect_speed_layer_len_deadband_and_limit():
    points = _track([(0, 4, [0.4, 0.42]), (4, 8, [0.6, 0.6])])  # дрожь 0.02 < deadband
    out = crop_keys_from_track(points, axis="x", window=0.5, speed=2.0, layer_len=3.0)
    # speed 2: склейка между пробами 3.667 и 4.0 с файла → середина 3.833 → 1.917 с слоя;
    # всё, что дальше 3 с слоя, отброшено.
    assert [k["t"] for k in out["keys"]] == [0, pytest.approx(1.917, abs=0.002)]
    assert out["keys"][0]["ease"] == "step"
    # Много планов, предел ключей — допуск растёт, в крайнем случае ключ на план и обрезка.
    many = _track([(i, i + 1, [0.2 + 0.6 * (i % 2), 0.8 - 0.6 * (i % 2)]) for i in range(30)], step=0.1)
    tight = crop_keys_from_track(many, axis="x", window=0.3, layer_len=30.0, max_keys=40)
    assert len(tight["keys"]) <= 40 and tight["tolerance"] > 0.015
    assert all(b["t"] > a["t"] for a, b in zip(tight["keys"], tight["keys"][1:]))
    assert track_shots([], "x", 0.5, 1.0, 3.0) == []
    assert crop_keys_from_track([], axis="x", window=0.5, layer_len=3.0)["keys"] == []


def test_crop_keys_loop_repeats_with_step_at_seam():
    points = _track([(0, 2, [0.2, 0.8])])
    out = crop_keys_from_track(points, axis="x", window=0.4, layer_len=5.0, loop_len=2.0)
    keys = out["keys"]
    assert [k["t"] for k in keys if k["t"] in (2.0, 4.0)] == [2.0, 4.0]
    assert [k for k in keys if k["t"] < 2.0][-1]["ease"] == "step"


def _fake_autofocus(monkeypatch, points: list[dict], calls: list):
    fake = types.ModuleType("app.autofocus")

    def compute_segment_focus(source, start, end, **kwargs):
        calls.append({"source": source, "start": start, "end": end, **kwargs})
        return [dict(p) for p in points]

    fake.compute_segment_focus = compute_segment_focus
    monkeypatch.setitem(sys.modules, "app.autofocus", fake)  # на хосте нет cv2


def test_auto_crop_layer_tool_sets_crop_and_keys(tmp_path, monkeypatch):
    from app import scene_cuts
    from app.agent_tools import run_tool
    from test_agent_tools import _ctx
    from test_render import _store

    store = _store(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "gemini_api_key", "")
    src = settings.source_dir / "s.mp4"
    src.write_bytes(b"src")
    source = store.create_source("upload", src, duration_sec=60, width=1080, height=1920, status="ready")
    analysis = store.create_ai_analysis(source["id"], "mock", status="succeeded")
    seg = store.create_ai_segment(analysis["id"], {"start_sec": 0, "end_sec": 40, "title": "A"})
    plan = store.create_clip_plan(source["id"], analysis["id"], "Мем", segment_ids=[seg["id"]])
    clip_file = tmp_path / "chorus.mp4"
    clip_file.write_bytes(b"x")
    asset = store.create_montage_asset(kind="video", label="припев", description="", tags="", file_path=str(clip_file),
                                       original_filename="chorus.mp4", mime_type="video/mp4", size_bytes=1,
                                       duration_sec=30.0, width=1440, height=1080, has_audio=True)
    ctx = _ctx(store, plan["id"])
    layer = {"id": "chorus_video", "type": "video", "start": 24.5, "end": 34.5,
             "props": {"asset_id": asset["id"], "from": 0.73, "w": 1080, "audio": True},
             "keys": {"cropW": [{"t": 0, "v": 0.5}]}}
    store.set_clip_plan_render_settings(plan["id"], {"layers": [layer]})
    calls: list = []
    _fake_autofocus(monkeypatch, _track([(0, 4, [0.3, 0.3]), (4, 10, [0.7, 0.7])]), calls)
    monkeypatch.setattr(scene_cuts, "detect_scene_cuts", lambda path, threshold=0.35: [0.5, 4.63, 40.0])
    res, ok = run_tool(ctx, "auto_crop_layer", {"layer_id": "chorus_video"})
    assert ok, res
    assert calls[0]["start"] == 0.73 and calls[0]["end"] == pytest.approx(10.73)
    assert calls[0]["strategy"] == "follow" and calls[0]["vlm_resolver"] is None
    assert calls[0]["source"]["local_path"] == str(clip_file)
    assert res["axis"] == "cropX" and res["shots"] == 2 and res["cuts"] == 1 and res["dropped_keys"] == ["cropW"]
    saved = next(l for l in store.get_clip_plan(plan["id"])["render_settings"]["layers"] if l["id"] == "chorus_video")
    crop = saved["props"]["crop"]
    assert crop["w"] == pytest.approx(0.4219, abs=1e-4) and crop["h"] == 1 and crop["x"] == pytest.approx(0.3 - 0.4219 / 2, abs=2e-3)
    keys = saved["keys"]["cropX"]
    assert "cropW" not in saved["keys"]
    # Склейка файла 4.63 − from 0.73 = 3.9 с слоя: ключ ровно там, перед ним — step.
    assert any(k["t"] == pytest.approx(3.9, abs=1e-3) for k in keys)
    assert [k for k in keys if k["t"] < 3.9][-1]["ease"] == "step"
    # Не video — понятный отказ; нет такого слоя — список есть.
    res, ok = run_tool(ctx, "auto_crop_layer", {"layer_id": "nope"})
    assert not ok and "chorus_video" in " ".join(res["problems"])


# ---------------------------------------------------------------- 3. отрезок видео по ссылке


def test_ytdlp_section_args_and_bounds():
    from app.video_import import build_ytdlp_section_args, section_bounds

    args = build_ytdlp_section_args("https://youtu.be/x", Path("/t/dl.%(ext)s"), 61.0, 79.5)
    assert args[args.index("--download-sections") + 1] == "*61.000-79.500"
    assert "--force-keyframes-at-cuts" in args and "--no-playlist" in args
    assert args[args.index("-f") + 1] == "bv*[height<=1080]+ba/b[height<=1080]/b" and args[-1] == "https://youtu.be/x"
    assert section_bounds(-3, 10) == (0.0, 10.0)
    for bad in ((5, 5.1), (0, 181), ("x", 3)):
        with pytest.raises(ValueError):
            section_bounds(*bad)


def test_import_video_from_url_uses_ingest_ytdlp_and_proxy_retry(tmp_path, monkeypatch):
    import app.ingest as ingest
    import app.montage_assets as montage_assets
    import app.proxy_config as proxy_config
    import app.render as render
    from app.video_import import import_video_from_url
    from test_render import _store

    store = _store(tmp_path, monkeypatch)
    runs: list[list[str]] = []
    proxies: list[str] = []

    def fake_ytdlp(args, *, proxy=""):  # без сети: первый раз «CDN режет», через прокси — файл
        runs.append(args)
        proxies.append(proxy)
        if not proxy:
            return subprocess.CompletedProcess(args, 1, "ERROR: unable to download video data: HTTP 403", "")
        template = Path(args[args.index("-o") + 1])
        (template.parent / "dl.mp4").write_bytes(b"section")
        return subprocess.CompletedProcess(args, 0, "[download] 100%\nVVF-TITLE:Anacondaz — Мне всё равно", "")

    encodes: list[list[str]] = []

    def fake_ffmpeg(args, timeout=None):
        encodes.append(args)
        Path(args[-1]).write_bytes(b"mp4")

    monkeypatch.setattr(ingest, "_run_ytdlp", fake_ytdlp)
    monkeypatch.setattr(proxy_config, "download_proxy", lambda: "http://u:p@proxy:8080")
    monkeypatch.setattr(render, "_run_ffmpeg", fake_ffmpeg)
    monkeypatch.setattr(montage_assets, "probe_asset",
                        lambda p: {"duration_sec": 18.5, "width": 1440, "height": 1080, "has_audio": True})
    asset = import_video_from_url(store, "https://www.youtube.com/watch?v=abc", 61, 79.5)
    assert len(runs) == 2 and runs[1][:2] == ["-S", "res,fps,+br"] and "--proxy" not in runs[1]
    assert proxies == ["", "http://u:p@proxy:8080"]
    assert "*61.000-79.500" in runs[0]
    assert encodes[0][encodes[0].index("-t") + 1] == "18.500" and "aac" in encodes[0]
    assert asset["kind"] == "video" and asset["has_audio"] and asset["width"] == 1440
    assert asset["label"].startswith("Anacondaz") and "61–80 с" in asset["label"]
    assert Path(asset["file_path"]).is_file() and "youtube.com" in asset["description"]
    assert not list(settings.tmp_dir.glob("video-url-*"))  # временная папка убрана

    # Не скачалось и через прокси — понятная ошибка с ERROR-строкой yt-dlp.
    monkeypatch.setattr(ingest, "_run_ytdlp",
                        lambda args, proxy="": subprocess.CompletedProcess(args, 1, "noise\nERROR: Video unavailable", ""))
    with pytest.raises(ValueError, match="Video unavailable"):
        import_video_from_url(store, "https://youtu.be/zzz", 0, 5)
    with pytest.raises(ValueError, match="http"):
        import_video_from_url(store, "file:///etc/passwd", 0, 5)


def test_add_video_from_url_tool_and_api(tmp_path, monkeypatch):
    import app.video_import as video_import
    from app.agent_tools import run_tool
    from app.agent_tools import ToolContext
    from test_api_pipeline import _client

    client, store = _client(tmp_path, monkeypatch)
    got: list = []

    def fake_import(store_, url, start, end, label=""):
        got.append((url, start, end, label))
        if url.endswith("bad"):
            raise ValueError("отрезок длиннее 180 с")
        return {"id": 77, "kind": "video", "label": label or "клип", "width": 1440, "height": 1080,
                "duration_sec": 18.5, "has_audio": True}

    monkeypatch.setattr(video_import, "import_video_from_url", fake_import)
    res, ok = run_tool(ToolContext(store=store), "add_video_from_url",
                       {"url": "https://youtu.be/x", "start": 61, "end": 79.5, "label": "припев"})
    assert ok and res["asset_id"] == 77 and res["has_audio"] and "props.audio: true" in res["hint"]
    res, ok = run_tool(ToolContext(store=store), "add_video_from_url", {"url": "https://youtu.be/bad", "start": 0, "end": 900})
    assert not ok and "180" in res["error"]
    body = client.post("/api/montage-assets/video-from-url",
                       json={"url": "https://youtu.be/x", "start": 1, "end": 5}).raise_for_status().json()
    assert body["id"] == 77 and got[-1] == ("https://youtu.be/x", 1.0, 5.0, "")
    assert client.post("/api/montage-assets/video-from-url",
                       json={"url": "https://youtu.be/bad", "start": 0, "end": 5}).status_code == 400


def test_vvf_asset_url_and_render_end_hold(monkeypatch, capsys):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import vvf

    sent: list = []

    def fake_call(method, path, body=None, **kwargs):
        sent.append((method, path, body))
        if path.endswith("video-from-url"):
            return {"id": 5, "label": "припев", "width": 1440, "height": 1080, "duration_sec": 18.5, "has_audio": True}
        return {"id": 9, "status": "succeeded", "duration_sec": 43.3, "qc": None}

    monkeypatch.setattr(vvf, "call", fake_call)
    vvf.main(["asset-url", "https://youtu.be/x", "--start", "61", "--end", "79.5", "-l", "припев"])
    assert sent[-1] == ("POST", "/api/montage-assets/video-from-url",
                        {"url": "https://youtu.be/x", "start": 61.0, "end": 79.5, "label": "припев"})
    assert "asset_id 5" in capsys.readouterr().out
    vvf.main(["render", "582", "--end-hold", "0"])
    assert sent[-1] == ("POST", "/api/clip-plans/582/render", {"end_hold_sec": 0.0})
    vvf.main(["render", "582"])
    assert sent[-1][2] == {}


# ---------------------------------------------------------------- 5. стоп-кадр в конце на клип


def test_end_hold_setting_override_and_default(monkeypatch):
    from app.clip_settings import normalize_end_hold, normalize_render_settings
    from app.render import ClipRenderService

    assert normalize_render_settings({})["end_hold_sec"] is None  # как у сервиса
    assert normalize_render_settings({"end_hold_sec": 0})["end_hold_sec"] == 0
    assert normalize_end_hold(9) == 1.5 and normalize_end_hold(-1) == 0 and normalize_end_hold("x") is None
    assert normalize_end_hold(True) is None and normalize_end_hold(float("nan")) is None
    monkeypatch.setattr(settings, "render_loudnorm", True)
    monkeypatch.setattr(settings, "render_end_hold_sec", 0.5)
    meme = {"render_settings": {"end_hold_sec": 0}}
    assert ClipRenderService._end_hold_sec() == 0.5
    assert ClipRenderService._end_hold_sec({"render_settings": {}}) == 0.5
    assert ClipRenderService._end_hold_sec(meme) == 0.0
    assert ClipRenderService._end_hold_sec(meme, 1.2) == 1.2  # параметр рендера сильнее настройки
    assert ClipRenderService._end_hold_sec({"render_settings": {"end_hold_sec": 1}}) == 1.0
    monkeypatch.setattr(settings, "render_loudnorm", False)  # без финального прохода стоп-кадра нет
    assert ClipRenderService._end_hold_sec({"render_settings": {"end_hold_sec": 1}}) == 0.0


def test_set_end_hold_tool_and_render_request_reach_render(tmp_path, monkeypatch):
    from app.agent_tools import run_tool
    from app.render import ClipRenderService
    from test_agent_tools import _ctx
    from test_render import _store

    store = _store(tmp_path, monkeypatch)
    src = settings.source_dir / "s.mp4"
    src.write_bytes(b"src")
    source = store.create_source("upload", src, duration_sec=60, width=1080, height=1920, status="ready")
    analysis = store.create_ai_analysis(source["id"], "mock", status="succeeded")
    seg = store.create_ai_segment(analysis["id"], {"start_sec": 0, "end_sec": 10, "title": "A"})
    plan = store.create_clip_plan(source["id"], analysis["id"], "Мем", segment_ids=[seg["id"]])
    ctx = _ctx(store, plan["id"])
    res, ok = run_tool(ctx, "set_end_hold", {"sec": 0})
    assert ok and res["end_hold_sec"] == 0 and res["effective_sec"] == 0
    assert store.get_clip_plan(plan["id"])["render_settings"]["end_hold_sec"] == 0
    res, ok = run_tool(ctx, "set_end_hold", {"sec": "много"})
    assert not ok
    res, ok = run_tool(ctx, "set_end_hold", {"reset": True})
    assert ok and res["end_hold_sec"] is None

    # Рендер берёт стоп-кадр из настроек клипа (или из параметра) и отдаёт его финальному проходу.
    run_tool(ctx, "set_end_hold", {"sec": 0})
    monkeypatch.setattr(settings, "render_loudnorm", True)
    seen: dict = {}
    service = ClipRenderService(store)

    def fake_segment(segment_id, **kwargs):
        seen["segment_hold"] = kwargs.get("end_hold_sec")
        return {"id": 1, "status": "succeeded", "title": "Мем", "description": ""}

    monkeypatch.setattr(service, "render_segment", fake_segment)
    for name in ("_apply_mute", "_apply_inserts"):
        monkeypatch.setattr(service, name, lambda clip, *a, **k: clip)
    monkeypatch.setattr(service, "_normalize_loudness", lambda clip, hold=None: seen.update(final_hold=hold) or clip)
    service.render_clip_plan(plan["id"])
    assert seen == {"segment_hold": 0.0, "final_hold": 0.0}
    service.render_clip_plan(plan["id"], end_hold_sec=1.0)
    assert seen == {"segment_hold": 1.0, "final_hold": 1.0}
