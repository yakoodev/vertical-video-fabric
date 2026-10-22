"""Игровые записи (Apex Legends, NVIDIA DVR): две звуковые дорожки, речи нет, моменты по громкости.

Отзыв агента-монтажёра: сервис брал только первую дорожку, «речи нет» выглядело как ошибка,
громкие места искать было нечем, финальная плашка гасла до стоп-кадра, а камера зумила
окошко килфида вместе с игрой.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app import motion, motion_render
from app.agent_tools import ToolContext, run_tool
from app.clip_history import snapshot
from app.settings import settings
from app.source_audio import extract_args, mix_chain, normalize_tracks
from app.source_loudness import loud_spans, pcm_levels, summary, top_peaks
from test_api_pipeline import _client, _require_ffmpeg


def _ctx(store, plan_id=None):
    return ToolContext(store=store, clip_plan_id=plan_id,
                       on_first_change=(lambda: snapshot(store, plan_id, "тест")) if plan_id else None)


def _game_video(path: Path) -> None:
    """7 с: дорожка 0 — тихий гул «игры» с громким выстрелом на 3–4 с, дорожка 1 — «микрофон» 1 кГц."""
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=size=320x180:rate=25",
         "-f", "lavfi", "-i", "aevalsrc='0.01*sin(2*PI*300*t)+0.7*sin(2*PI*300*t)*between(t,3,4)':s=44100",
         "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=44100",
         "-map", "0:v", "-map", "1:a", "-map", "2:a", "-t", "7",
         "-pix_fmt", "yuv420p", "-c:v", "libx264", "-c:a", "aac", str(path)],
        check=True,
    )


def _game_setup(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    video = tmp_path / "apex.mp4"
    _game_video(video)
    with video.open("rb") as fileobj:
        source = client.post("/api/sources", files={"file": ("apex.mp4", fileobj, "video/mp4")}).raise_for_status().json()
    analysis = store.create_ai_analysis(source["id"], "mock", status="succeeded")
    a = store.create_ai_segment(analysis["id"], {"start_sec": 0, "end_sec": 5, "title": "A"})
    b = store.create_ai_segment(analysis["id"], {"start_sec": 1, "end_sec": 6, "title": "B"})
    plan = store.create_clip_plan(source["id"], analysis["id"], "Апекс", segment_ids=[a["id"], b["id"]])
    return client, store, source, plan


def _rms_db(path: Path) -> float:
    from app.speech_onsets import wav_pcm

    levels = pcm_levels(wav_pcm(path), 16000, 0.5)
    return max(levels) if levels else -90.0


# ------------------------------------------------------------------ 1. звуковые дорожки


def test_normalize_tracks_and_graphs():
    assert normalize_tracks(None) is None and normalize_tracks([]) is None
    assert normalize_tracks([1, 0, 1]) == [0, 1]
    assert normalize_tracks("0,1") == [0, 1] and normalize_tracks("[1]") == [1] and normalize_tracks(1) == [1]
    assert normalize_tracks(["x", -1, 99]) is None
    assert mix_chain(0, [1], "a") == "[0:a:1]anull[a]"
    assert mix_chain(2, [0, 1], "p2") == "[2:a:0][2:a:1]amix=inputs=2:duration=longest:dropout_transition=0[p2]"
    # По умолчанию — прежняя команда: первая дорожка напрямую.
    args = extract_args(Path("s.mp4"), Path("o.wav"), None, start=5, duration=2)
    assert args[args.index("-map") + 1] == "0:a:0" and args.index("-ss") < args.index("-i")


def test_render_and_recognition_use_the_source_tracks():
    from app.clip_subtitles import build_clip_audio_args
    from app.render import _audio_filter, _audio_map, _preset_with_source_audio

    plain = {"audio_mix_mode": "primary"}
    assert _audio_filter(plain) == "" and _audio_map(plain) == "0:a:0?"
    source = {"audio_tracks": [0, 1], "metadata_json": json.dumps(
        {"streams": [{"codec_type": "video"}, {"codec_type": "audio"}, {"codec_type": "audio"}]})}
    preset = _preset_with_source_audio(plain, source)
    assert preset["source_audio_tracks"] == [0, 1] and _audio_map(preset) == "[aout]"
    assert "[0:a:0][0:a:1]amix" in _audio_filter(preset) and _audio_filter(preset).endswith("[aout]")
    # Дорожки, которой в файле нет, не берём (запись заменили).
    assert _preset_with_source_audio(plain, {**source, "audio_tracks": [2]}) == plain
    args = build_clip_audio_args(Path("s.mp4"), [(0, 5), (1, 6)], Path("o.wav"), [0, 1])
    graph = args[args.index("-filter_complex") + 1]
    assert "[0:a:0][0:a:1]amix" in graph and "[1:a:0][1:a:1]amix" in graph and "concat=n=2" in graph
    old = build_clip_audio_args(Path("s.mp4"), [(0, 5)], Path("o.wav"))
    assert old[old.index("-filter_complex") + 1] == "[0:a]anull[out]"


def test_set_source_audio_rest_mcp_and_real_audio(tmp_path, monkeypatch):
    _require_ffmpeg()
    from app.clip_subtitles import build_clip_audio_args
    from app.source_audio import source_tracks

    client, store, source, plan = _game_setup(tmp_path, monkeypatch)
    view = client.get(f"/api/sources/{source['id']}/audio").json()
    assert view["tracks_total"] == 2 and view["selected"] == [0] and "set_source_audio" in view["note"]
    assert client.patch(f"/api/sources/{source['id']}/audio", json={"audio_tracks": [2]}).status_code == 400

    store.set_source_transcript(source["id"], [{"start": 0, "end": 1, "text": "старое"}], "small")
    out, ok = run_tool(_ctx(store), "set_source_audio", {"project_id": source["id"], "tracks": [1]})
    assert ok, out
    assert out["audio"]["selected"] == [1]
    assert store.get_source_transcript(source["id"]) == []  # звук другой — расшифровка сброшена
    # Встроенный монтажёр — без project_id, источник его клипа.
    out, ok = run_tool(_ctx(store, plan["id"]), "set_source_audio", {"tracks": [0, 1]})
    assert ok and out["audio"]["selected"] == [0, 1]

    clip = run_tool(_ctx(store, plan["id"]), "get_clip", {})[0]
    assert clip["source"]["audio"]["tracks_total"] == 2 and clip["source"]["audio"]["selected"] == [0, 1]
    projects = run_tool(_ctx(store), "list_projects", {})[0]["projects"]
    assert projects[0]["audio_tracks"] == {"total": 2, "selected": [0, 1]}

    # Настоящий звук: в первые 2 с дорожка 0 почти тихая, дорожка 1 — тон 1 кГц.
    src = store.get_source(source["id"])
    path = Path(src["local_path"])
    first, mic, both = tmp_path / "first.wav", tmp_path / "mic.wav", tmp_path / "both.wav"
    subprocess.run(build_clip_audio_args(path, [(0, 2)], first), check=True)
    subprocess.run(build_clip_audio_args(path, [(0, 2)], mic, [1]), check=True)
    subprocess.run(build_clip_audio_args(path, [(0, 2), (0, 2)], both, source_tracks(src)), check=True)
    assert _rms_db(first) < -38 and _rms_db(mic) > -26 and _rms_db(both) > -35


# ------------------------------------------------------------------ 2. честное «речи нет»


def test_source_transcript_without_speech_is_an_empty_answer(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, store, source, _plan = _game_setup(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr("app.ai.transcript.transcribe_source_cues",
                        lambda path, model=None, **kw: calls.append(kw) or [])
    out = client.get(f"/api/sources/{source['id']}/transcript").json()
    assert out["cues"] == [] and out["no_speech"] and "речи не найдено (звук есть)" in out["note"]
    out, ok = run_tool(_ctx(store), "get_source_transcript", {"project_id": source["id"]})
    assert ok and out["lines"] == [] and out["no_speech"]
    assert len(calls) == 1 and calls[0]["strict"]  # второй раз — из кэша, Whisper не гоняем


def test_whisper_without_words_is_no_speech_piece(monkeypatch):
    from app.ai import transcript

    monkeypatch.setattr("app.render._run_ffmpeg", lambda args, timeout=0: None)

    class Silent:
        def transcribe(self, *_a):
            raise RuntimeError("Whisper returned no word-level timestamps")

    monkeypatch.setattr("app.subtitles.whisper_local.WhisperSubtitleProvider", Silent)
    assert transcript._transcribe_piece(Path("x.mp4"), 0.0, 60.0, "small") == []


def test_listen_without_speech_returns_level(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client_, store, _source, plan = _game_setup(tmp_path, monkeypatch)

    class Silent:
        def transcribe(self, *_a):
            raise RuntimeError("Whisper returned no word-level timestamps")

    monkeypatch.setattr("app.clip_subtitles.get_subtitle_provider", lambda name: Silent())
    out, ok = run_tool(_ctx(store, plan["id"]), "listen", {"from_sec": 2.5, "to_sec": 4.5})
    assert ok, out
    assert out["words"] == [] and out["text"] == "" and "речи нет" in out["note"]
    assert out["level"]["max_db"] > -10 and out["level"]["rms_db"] < out["level"]["max_db"]  # выстрел 3–4 с


# ------------------------------------------------------------------ 3. громкость


def test_loudness_helpers_merge_and_pick_peaks():
    levels = [-40.0] * 10 + [-10.0, -12.0, -40.0, -11.0] + [-40.0] * 10 + [-5.0] + [-40.0] * 5
    spans = loud_spans(levels, 0.1, -30.0, gap=0.3)
    assert spans == [{"start": 1.0, "end": 1.4, "loudest_db": -10.0}, {"start": 2.4, "end": 2.5, "loudest_db": -5.0}]
    assert [p["t"] for p in top_peaks(levels, 0.1, top=3, min_sep=0.5, floor=-30)] == [2.45, 1.05]
    assert [p["t"] for p in top_peaks(levels, 0.1, top=3, min_sep=0.2, floor=-30)] == [2.45, 1.05, 1.35]
    info = summary(levels)
    assert info["median_db"] == -40.0 and info["loud_db"] == -34.0 and info["max_db"] == -5.0
    import struct

    loud = struct.pack("<4h", 16384, -16384, 16384, -16384)
    assert pcm_levels(loud, 4, 1.0) == [-6.0] and pcm_levels(b"\0\0" * 4, 4, 1.0) == [-90.0]


def test_source_loudness_finds_the_shot(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client_, store, source, _plan = _game_setup(tmp_path, monkeypatch)
    out, ok = run_tool(_ctx(store), "source_loudness", {"project_id": source["id"], "step": 0.1})
    assert ok, out
    assert out["step"] == 0.5  # шаг не мельче 0.5 с
    assert 3.0 <= out["peaks"][0]["t"] <= 4.0
    assert any(s["start"] <= 3.1 and s["end"] >= 3.9 for s in out["loud"])
    assert out["summary"]["loud_db"] == pytest.approx(out["summary"]["median_db"] + 6, abs=0.11)
    assert len(out["db"]) == 14  # 7 с по 0.5
    capped, ok = run_tool(_ctx(store), "source_loudness", {"project_id": source["id"], "from_sec": 1, "to_sec": 5000})
    assert ok and capped["to_sec"] <= 7.0


def test_source_loudness_reads_only_the_window(monkeypatch):
    from app import source_loudness as sl

    seen = {}

    def fake_read(source, start, length, rate=sl.RATE):
        seen.update(start=start, length=length)
        return b"\0\0" * int(rate * length)

    class Store:
        def get_source(self, _id):
            return {"id": 1, "duration_sec": 4 * 3600.0, "local_path": "x"}

    monkeypatch.setattr(sl, "read_window_pcm", fake_read)
    out = sl.source_loudness(Store(), 1, from_sec=3600, to_sec=3600 + 3 * 3600)
    assert seen == {"start": 3600.0, "length": 600.0} and "урезано" in out["note"]


def test_find_noises_without_subtitles_gives_merged_spans(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client_, store, _source, plan = _game_setup(tmp_path, monkeypatch)
    out, ok = run_tool(_ctx(store, plan["id"]), "find_noises", {})
    assert ok, out
    assert not out["speech_excluded"] and "субтитров нет" in out["note"]
    # Выстрел 3–4 с исходника есть в обоих кусках: [0,5] → 3–4 с клипа, [1,6] → 7–8 с.
    assert len(out["noises"]) == 2
    first, second = out["noises"]
    assert first["start"] == pytest.approx(3.0, abs=0.15) and first["end"] == pytest.approx(4.0, abs=0.15)
    assert second["start"] == pytest.approx(7.0, abs=0.15)
    assert out["threshold_db"] == pytest.approx(out["median_db"] + 6, abs=0.11)


# ------------------------------------------------------------------ 4. лист кадров исходника


def test_source_sheet_times_and_labels():
    assert motion_render.source_sheet_times(10, 20, 2, None) == [10, 12, 14, 16, 18, 20]
    assert len(motion_render.source_sheet_times(0, 600, 1, None)) == 24
    assert len(motion_render.source_sheet_times(0, 120, None, None)) == 12
    assert motion_render.source_sheet_times(0, 50, 10, 30.0)[-1] <= 29.9
    with pytest.raises(ValueError):
        motion_render.source_sheet_times(20, 10, None, None)
    assert motion_render.source_time_label(3723.5) == "1:02:03.5" and motion_render.source_time_label(65) == "1:05.0"


def test_source_contact_sheet_is_an_image(tmp_path, monkeypatch):
    _require_ffmpeg()
    from app.agent_tools import ImageResult

    _client_, store, source, _plan = _game_setup(tmp_path, monkeypatch)
    out, ok = run_tool(_ctx(store), "source_contact_sheet", {"project_id": source["id"], "from_sec": 1,
                                                                "to_sec": 5, "step": 1})
    assert ok and isinstance(out, ImageResult), out
    assert out.data[:2] == b"\xff\xd8" and out.info["times"] == [1, 2, 3, 4, 5]
    out, ok = run_tool(_ctx(store), "source_contact_sheet", {"project_id": source["id"], "from_sec": 0,
                                                                "to_sec": 6, "x": 0.8})
    assert ok and isinstance(out, ImageResult) and out.info["x"] == 0.8
    bad, ok = run_tool(_ctx(store), "source_contact_sheet", {"project_id": source["id"], "from_sec": 0,
                                                                "to_sec": 6, "x": 3})
    assert not ok


# ------------------------------------------------------------------ 5. пустые субтитры


def test_set_subtitle_words_empty_clears(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client_, store, _source, plan = _game_setup(tmp_path, monkeypatch)
    store.set_clip_plan_subtitles(plan["id"], {"words": [{"word": "фантом", "start": 1, "end": 2}],
                                               "lines": [{"start": 1, "end": 2, "text": "фантом"}]})
    out, ok = run_tool(_ctx(store, plan["id"]), "set_subtitle_words", {"words": []})
    assert ok and out["words"] == 0, out
    assert not (store.get_clip_plan_subtitles(plan["id"]) or {}).get("words")


# ------------------------------------------------------------------ 6. громкость эффектов без речи


def test_effect_level_in_a_clip_without_speech(monkeypatch):
    # Громкие места эффекта −12 дБ, пик −6.
    monkeypatch.setattr(motion_render, "audio_levels", lambda path, seconds=30.0: (-12.0, -6.0))
    # С речью: на 10 дБ тише громких мест (и не выше −20): −20 → поправка −8.
    assert motion_render.effect_gain_db("boom.ogg", speech_db=-8.0) == -8.0
    # Без речи (игра, громкие места −8): эффект на 4 дБ ниже них — −12, не тонет.
    assert motion_render.effect_gain_db("boom.ogg", speech_db=-8.0, speechless=True) == 0.0
    # И не громче пика: пик эффекта не выше −3 dBFS (поправка не больше +3).
    assert motion_render.effect_gain_db("boom.ogg", speech_db=0.0, speechless=True) == 3.0


def test_plan_is_speechless_needs_proof(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client_, store, source, plan = _game_setup(tmp_path, monkeypatch)
    plan = store.get_clip_plan(plan["id"])
    assert not motion_render.plan_is_speechless(store, plan)  # ничего не знаем — как с речью
    store.set_source_transcript(source["id"], [], "small|no_speech")
    assert motion_render.plan_is_speechless(store, plan)
    store.set_source_transcript(source["id"], [{"start": 2.0, "end": 3.0, "text": "го"}], "small")
    assert not motion_render.plan_is_speechless(store, plan)
    store.set_source_transcript(source["id"], [{"start": 100.0, "end": 101.0, "text": "далеко"}], "small")
    assert motion_render.plan_is_speechless(store, plan)
    store.set_clip_plan_subtitles(plan["id"], {"words": [{"word": "да", "start": 1, "end": 2}],
                                               "lines": [{"start": 1, "end": 2, "text": "да"}]})
    assert not motion_render.plan_is_speechless(store, plan)


# ------------------------------------------------------------------ 7. слой до конца и out


def test_final_layer_holds_on_the_end_freeze():
    layers = [
        {"id": "a", "type": "text", "start": 7.0, "end": 9.98, "out": {"type": "fade", "duration": 0.3}},
        {"id": "b", "type": "text", "start": 2.0, "end": 5.0, "out": {"type": "fade", "duration": 0.3}},
    ]
    held = motion_render.hold_final_layers(layers, 10.0)
    assert held[0]["out"]["type"] == "none" and held[1]["out"]["type"] == "fade"
    assert layers[0]["out"]["type"] == "fade"  # исходные слои не трогаем


def test_comp_drops_out_of_the_last_layer(tmp_path):
    comp = motion_render.write_comp(
        tmp_path / "c.json", width=1080, height=1920, fps=30, duration=6.0, camera=[],
        layers=[{"id": "end", "type": "text", "start": 4, "end": 6, "out": {"type": "zoom", "duration": 0.4}}],
    )
    data = json.loads(comp.read_text(encoding="utf-8"))
    assert data["layers"][0]["out"]["type"] == "none"


def test_out_zero_or_null_means_no_animation():
    base = {"type": "text", "start": 0, "end": 3, "props": {"text": "ИТОГ"}}
    for out in ({"duration": 0}, None, {"type": "fade", "duration": 0}, {"type": "none", "duration": 0}):
        assert motion.validate_layers([{**base, "out": out}], 10.0) == [], out
        assert motion.normalize_layers([{**base, "out": out}])[0]["out"]["type"] == "none"
    assert motion.validate_layers([{**base, "out": {"type": "fade", "duration": 0.01}}], 10.0)


# ------------------------------------------------------------------ 8. камера и раскладка


def test_camera_regions_for_layouts():
    from app.stream_layout import camera_regions

    assert camera_regions({"mode": "single"}, 1080, 1920) is None
    split = camera_regions({"mode": "split", "ratio": 0.3, "cam_pos": "top"}, 1080, 1920)
    assert split["box"] == {"x": 0, "y": 576, "w": 1080, "h": 1344} and split["pinned"] == []
    bottom = camera_regions({"mode": "split", "ratio": 0.3, "cam_pos": "bottom"}, 1080, 1920)
    assert bottom["box"]["y"] == 0 and bottom["box"]["h"] == 1344
    # Килфид Apex: угол исходника 1920×1080 → окошко pip шириной 0.5 кадра справа сверху.
    pip = camera_regions({"mode": "pip_cam", "cam": {"x": 0.70, "y": 0.185, "w": 0.245, "h": 0.155},
                          "pip": {"x": 0.75, "y": 0.2, "w": 0.5}}, 1080, 1920, 1920, 1080)
    rect = pip["pinned"][0]
    assert pip["box"] == {"x": 0, "y": 0, "w": 1080, "h": 1920}
    assert rect["x"] == 539 and rect["w"] == 541  # 540 px + пиксель запаса (справа край кадра)
    assert abs(rect["h"] - (round(540 / (0.245 * 1920 / (0.155 * 1080)) / 2) * 2 + 2)) <= 2
    mirrored = camera_regions({"mode": "pip_cam", "pip": {"x": 0.75, "y": 0.2, "w": 0.5}}, 1080, 1920, mirror=True)
    assert mirrored["pinned"][0]["x"] <= 1


def _compositor() -> Path:
    path = settings.root_dir / "node" / "motion-render.mjs"
    if not path.exists():
        pytest.skip("node/motion-render.mjs не собран (npm run build:motion)")
    for tool in ("node", "ffmpeg"):
        if not shutil.which(tool):
            pytest.skip(f"{tool} недоступен")
    return path


def _pixel(png: Path, x: int, y: int) -> tuple[int, int, int]:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(png), "-vf", f"crop=1:1:{x}:{y}", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return raw[0], raw[1], raw[2]


def test_compositor_camera_keeps_pip_window_in_place(tmp_path, monkeypatch):
    _require_ffmpeg()
    compositor = _compositor()
    monkeypatch.setattr(settings, "motion_compositor", compositor)
    monkeypatch.setattr(settings, "motion_node", "node")
    # Основа 180×320: синяя «игра», красное окошко pip в правом верхнем углу (120..176, 4..60).
    base = tmp_path / "base.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=180x320:r=10:d=1",
                    "-vf", "drawbox=x=120:y=4:w=56:h=56:color=red:t=fill", "-pix_fmt", "yuv420p",
                    "-c:v", "libx264", str(base)], check=True)
    camera = [{"t": 0, "zoom": 2.0, "x": 0.5, "y": 0.5}]
    assets = tmp_path / "assets.json"
    assets.write_text("{}", encoding="utf-8")

    def frame(name, **regions):
        comp = motion_render.write_comp(tmp_path / f"{name}.json", width=180, height=320, fps=10, duration=1.0,
                                        layers=[], camera=camera, **regions)
        png = tmp_path / f"{name}.png"
        subprocess.run(motion_render.build_compositor_args(comp, assets, input_path=base, frame=0.5, png_path=png),
                       check=True, capture_output=True)
        return png

    zoomed = frame("zoomed")  # прежнее поведение: зум 2× в центр — окошко уехало за край
    assert _pixel(zoomed, 148, 30)[0] < 100
    pinned = frame("pinned", camera_box={"x": 0, "y": 0, "w": 180, "h": 320},
                   pinned=[{"x": 119, "y": 3, "w": 58, "h": 58}])
    r, g, b = _pixel(pinned, 148, 30)
    assert r > 180 and b < 90  # окошко на месте и красное
    assert _pixel(pinned, 60, 200)[2] > 180  # игра вокруг — зум синего


def test_set_camera_notes_layout(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client_, store, _source, plan = _game_setup(tmp_path, monkeypatch)
    ctx = _ctx(store, plan["id"])
    run_tool(ctx, "set_layout", {"mode": "pip_cam", "cam": {"x": 0.70, "y": 0.185, "w": 0.245, "h": 0.155}})
    out, ok = run_tool(ctx, "set_camera", {"keys": [{"t": 1, "zoom": 1.0}, {"t": 1.3, "zoom": 1.3}]})
    assert ok and "окошко" in out["note"], out


# ------------------------------------------------------------------ 9. пресет по умолчанию


def test_get_clip_shows_effective_preset(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client_, store, _source, plan = _game_setup(tmp_path, monkeypatch)
    store.create_ffmpeg_preset("Вертикаль 1080")
    store.create_ffmpeg_preset("Другой")
    clip = run_tool(_ctx(store, plan["id"]), "get_clip", {})[0]
    effective = clip["render"]["effective_preset"]
    assert clip["render"]["preset_id"] is None
    assert effective["default"] and effective["id"] == min(p["id"] for p in store.list_ffmpeg_presets())
    from app.render import ClipRenderService

    assert ClipRenderService(store)._preset(None)["id"] == effective["id"]
