"""▶ Смотреть монтаж: рендер 480p во время показа (app/stream_preview.py)."""

from __future__ import annotations

import json
import shutil
import struct
import subprocess
import threading
import time

import pytest

from app import stream_preview as sp
from app.render import _render_filter, build_ffmpeg_render_args
from app.transitions import join_picture_filters, normalize_transition, plan_joins
from test_api_pipeline import _require_ffmpeg
from test_clip_spec import _setup


# ---------------------------------------------------------------- чистая логика


def test_preview_size_keeps_the_frame_of_every_format():
    assert sp.preview_size(1080, 1920) == (480, 854)
    assert sp.preview_size(1920, 1080) == (854, 480)
    assert sp.preview_size(1080, 1080) == (480, 480)
    assert sp.preview_size(1080, 1350) == (480, 600)


def test_blur_of_the_backdrop_scales_with_the_frame():
    assert sp.scale_graph("crop=1:1,boxblur=24:2[bg]", 480 / 1080) == "crop=1:1,boxblur=11:2[bg]"


def _parts(*lengths):
    return [{"start_sec": 100.0 * i, "end_sec": 100.0 * i + n / 30, "frames": n, "speed": 1.0} for i, n in enumerate(lengths)]


def test_timeline_of_parts_and_crossfades():
    spans = sp.build_timeline(_parts(60, 60), [10], 30.0, [])
    assert [(s.kind, s.frames, s.part, s.first) for s in spans] == [("solid", 50, 0, 0), ("xfade", 10, 0, 0), ("solid", 50, 1, 10)]


def test_timeline_of_an_inserted_freeze_repeats_one_frame_and_pushes_the_rest():
    freeze = {"at": 1.0, "duration": 0.5, "audio": "silence", "layers": "freeze"}
    spans = sp.build_timeline(_parts(60), [], 30.0, [freeze])
    assert [(s.kind, s.frames) for s in spans] == [("solid", 30), ("hold", 15), ("solid", 30)]
    assert spans[1].source["first"] == 30
    assert spans[2].first == 30
    assert sum(s.frames for s in spans) == 75


def test_timeline_of_a_freeze_over_keeps_the_length():
    freeze = {"at": 1.0, "duration": 0.5, "audio": "continue", "layers": "freeze"}
    spans = sp.build_timeline(_parts(60), [], 30.0, [freeze])
    assert sum(s.frames for s in spans) == 60
    assert [(s.kind, s.frames) for s in spans] == [("solid", 30), ("hold", 15), ("solid", 15)]
    assert spans[2].first == 45


def test_chunk_edges_never_cut_a_crossfade_and_start_every_part():
    parts = _parts(120, 120)
    spans = sp.build_timeline(parts, [12], 30.0, [])
    edges = sp.chunk_edges(parts, spans, 30.0)
    zone = range(108 + 1, 120)
    assert edges[0] == 0 and edges[-1] == 228
    assert not set(edges) & set(zone)
    for a, b in zip(edges, edges[1:]):
        assert b - a <= int(sp.MAX_CHUNK_SEC * 30)


def test_trimming_the_head_of_a_part_keeps_its_inner_chunks():
    """Сетка — секунды записи: подрезали начало, а чанки дальше те же самые кадры записи."""
    long = {"start_sec": 30.0, "end_sec": 60.0, "frames": 900, "speed": 1.0}
    cut = {"start_sec": 31.5, "end_sec": 60.0, "frames": 855, "speed": 1.0}

    def src_edges(part):
        spans = sp.build_timeline([part], [], 30.0, [])
        return [round(part["start_sec"] + e / 30.0, 2) for e in sp.chunk_edges([part], spans, 30.0)]

    assert src_edges(long)[-6:] == src_edges(cut)[-6:]
    assert set(src_edges(cut)) <= set(src_edges(long)) | {31.5}


def test_short_chunks_are_merged():
    parts = _parts(10, 10, 10, 90)
    spans = sp.build_timeline(parts, [0, 0, 0], 30.0, [])
    edges = sp.chunk_edges(parts, spans, 30.0)
    assert all(b - a >= int(sp.MIN_CHUNK_SEC * 30) for a, b in zip(edges, edges[1:]))


def _box(kind, body=b""):
    return struct.pack(">I4s", 8 + len(body), kind) + body


def _fragment(samples=7, decode=12345):
    tfdt = _box(b"tfdt", bytes([1, 0, 0, 0]) + struct.pack(">Q", decode))
    trun = _box(b"trun", bytes(4) + struct.pack(">I", samples))
    mfhd = _box(b"mfhd", bytes(4) + struct.pack(">I", 3))
    moof = _box(b"moof", mfhd + _box(b"traf", tfdt + trun))
    return moof, moof + _box(b"mdat", b"xx")


def test_fragment_samples_and_tfdt_patch():
    moof, fragment = _fragment()
    assert sp.fragment_samples(moof) == 7
    patched = sp.patch_tfdt(fragment, 999, 5)
    assert len(patched) == len(fragment)
    assert struct.pack(">Q", 999) in patched and struct.pack(">Q", 12345) not in patched
    assert struct.pack(">I", 5) in patched


def test_stand_in_graph_does_not_touch_the_video_of_the_source(tmp_path):
    preset = {"output_width": 854, "output_height": 480, "fps": 30, "stand_in": True}
    assert "[0:v]" not in _render_filter(854, 480, 30.0, preset, None)
    args = build_ffmpeg_render_args(tmp_path / "src.mp4", tmp_path / "out.mp4", 1.0, 3.0, preset)
    graph = args[args.index("-filter_complex") + 1]
    assert graph.startswith("color=c=black:s=854x480") and "[vout]" in graph


def test_picture_of_the_join_is_the_same_graph_as_the_render():
    transition = normalize_transition({"type": "fade", "duration": 0.5, "smart": False})
    overlaps = plan_joins(transition, [5.0, 5.0], [None], None)
    filters = join_picture_filters(2, [5.0, 5.0], overlaps, transition)
    assert any("xfade=transition=fade:duration=0.500:offset=4.500" in f for f in filters)


def test_video_playlist_has_the_whole_clip_from_the_start():
    recipe = {"fps": 30.0, "chunks": [{"index": 0, "first": 0, "frames": 90}, {"index": 1, "first": 90, "frames": 60}], "width": 854, "height": 480}
    text = sp.video_playlist(recipe)
    assert "#EXT-X-PLAYLIST-TYPE:VOD" in text and "#EXT-X-ENDLIST" in text and '#EXT-X-MAP:URI="init.mp4"' in text
    assert "#EXTINF:3.00000,\nv/0.m4s\n#EXTINF:2.00000,\nv/1.m4s" in text
    master = sp.master_playlist(recipe)
    assert "AUDIO=" not in master and "RESOLUTION=854x480" in master


def test_preview_store_never_writes():
    class Boom:
        def get_source(self, source_id):
            return {"id": source_id, "local_path": "/x.mp4", "audio_tracks": [1]}

        def __getattr__(self, name):
            raise AssertionError(f"записал в настоящее хранилище: {name}")

    store = sp.PreviewStore(Boom(), {7: "/proxy.mp4"})
    assert store.update_ai_segment_status(1, "rendering") is None
    assert store.update_clip_plan_status(1, "rendering") is None
    clip = store.create_clip(7, segment_id=None)
    assert store.finish_clip_render(clip["id"], status="succeeded", output_path="a")["status"] == "succeeded"
    assert store.get_clip(clip["id"])["output_path"] == "a"
    assert store.get_source(7)["local_path"] == "/proxy.mp4" and store.get_source(7)["audio_tracks"] is None
    assert store.get_source(8)["local_path"] == "/x.mp4"


# ---------------------------------------------------------------- настоящая сборка


def _frames(path) -> int:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries", "stream=nb_read_packets",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True).stdout
    return int(out.strip().split(",")[0])


def _build(store, plan_id, tmp_path):
    key, override = sp.job_key(store, plan_id)
    recipe = sp.record(store, plan_id, key, threading.Event(), override)
    done = []
    missing = [c for c in recipe["chunks"] if not sp.chunk_ready(recipe, c["index"])]
    if missing:
        sp.run_chain(recipe, missing, threading.Event(), lambda c, _s: done.append(c["index"]))
    return recipe, done


def _join(recipe, tmp_path):
    out = tmp_path / f"joined-{recipe['key']}.mp4"
    with out.open("wb") as fh:
        fh.write(sp.init_path(recipe).read_bytes())
        for index in range(len(recipe["chunks"])):
            fh.write(sp.segment_bytes(recipe, index))
    return out


def test_stream_has_the_frames_of_the_real_render_and_no_database_writes(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    before = len(store.list_clips_for_plan(plan["id"]))
    status_before = store.get_clip_plan(plan["id"]).get("status")
    recipe, done = _build(store, plan["id"], tmp_path)
    assert len(store.list_clips_for_plan(plan["id"])) == before
    assert store.get_clip_plan(plan["id"]).get("status") == status_before
    assert done == [c["index"] for c in recipe["chunks"]]
    joined = _join(recipe, tmp_path)
    assert _frames(joined) == recipe["total_frames"]
    assert recipe["audio"] and (sp.Path(recipe["audio"]) / "audio.m3u8").is_file()
    clip = client.post(f"/api/clip-plans/{plan['id']}/render", json={}).raise_for_status().json()
    assert clip["status"] == "succeeded"
    assert _frames(clip["output_path"]) == recipe["total_frames"]  # столько же кадров, что у рендера


def test_edit_rebuilds_only_the_changed_chunks(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    client.patch(f"/api/clip-plans/{plan['id']}/render-settings", json={"settings": {
        "end_hold_sec": 0,
        "layers": [{"id": "t", "type": "text", "start": 1.0, "end": 2.0, "x": 0.5, "y": 0.5, "props": {"text": "РАЗ"}}],
    }}).raise_for_status()
    first, done = _build(store, plan["id"], tmp_path)
    assert done
    client.patch(f"/api/clip-plans/{plan['id']}/render-settings", json={"settings": {
        "layers": [{"id": "t", "type": "text", "start": 1.0, "end": 2.0, "x": 0.5, "y": 0.5, "props": {"text": "ДВА"}}],
    }}).raise_for_status()
    second, again = _build(store, plan["id"], tmp_path)
    assert second["key"] != first["key"]
    changed = {c["index"] for c, d in zip(first["chunks"], second["chunks"]) if c["hash"] != d["hash"]}
    assert changed and len(changed) < len(first["chunks"])
    assert set(again) == changed
    assert _frames(_join(second, tmp_path)) == second["total_frames"]


def test_crossfade_and_freeze_make_the_right_length(tmp_path, monkeypatch):
    _require_ffmpeg()
    from app.settings import settings

    monkeypatch.setattr(settings, "render_loudnorm", True)  # финальный стоп-кадр кладёт проход громкости
    client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    client.patch(f"/api/clip-plans/{plan['id']}/render-settings", json={"settings": {
        "end_hold_sec": 0.5,
        "transition": {"type": "fade", "duration": 0.5, "smart": False},
        "freezes": [{"at": 2.0, "duration": 1.0, "audio": "silence", "layers": "freeze"}],
    }}).raise_for_status()
    recipe, _done = _build(store, plan["id"], tmp_path)
    assert recipe["hold_frames"] > 0
    assert any(s["kind"] == "xfade" for c in recipe["chunks"] for s in c["spans"])
    assert any(s["kind"] == "hold" for c in recipe["chunks"] for s in c["spans"])
    clip = client.post(f"/api/clip-plans/{plan['id']}/render", json={}).raise_for_status().json()
    assert clip["status"] == "succeeded"
    assert _frames(_join(recipe, tmp_path)) == _frames(clip["output_path"])


def test_api_serves_hls_while_building_and_waits_for_a_chunk(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, _store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    deadline = time.monotonic() + 120
    status = {}
    while time.monotonic() < deadline:
        status = client.post(f"/api/clip-plans/{plan['id']}/stream", json={}).raise_for_status().json()
        if status.get("playlist"):
            break
        assert status["state"] != "failed", status
        time.sleep(0.2)
    assert status.get("playlist"), status
    assert status["width"] and status["height"]
    master = client.get(status["playlist"]).raise_for_status()
    assert "video.m3u8" in master.text and master.headers["X-Accel-Buffering"] == "no"
    base = status["playlist"].rsplit("/", 1)[0]
    video = client.get(f"{base}/video.m3u8").raise_for_status().text
    names = [line for line in video.splitlines() if line.startswith("v/")]
    assert len(names) == status["total"]
    got = None
    while time.monotonic() < deadline:
        got = client.get(f"{base}/{names[-1]}")
        if got.status_code == 200:
            break
        assert got.status_code == 503 and got.headers["Retry-After"]
        time.sleep(0.1)
    assert got is not None and got.status_code == 200 and got.content[4:8] == b"moof"
    assert client.get(f"{base}/init.mp4").raise_for_status().content[4:8] == b"ftyp"
    assert client.get(f"{base}/v/99999.m4s").status_code == 404
    assert client.get("/media/stream/nosuchkey/master.m3u8").status_code == 404
    sp.service.cancel_all()


def test_clip_without_a_recording_shows_the_render(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, store, source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    clip = client.post(f"/api/clip-plans/{plan['id']}/render", json={}).raise_for_status().json()
    assert clip["status"] == "succeeded"
    sp.Path(source["local_path"]).unlink()
    got = client.post(f"/api/clip-plans/{plan['id']}/stream", json={}).raise_for_status().json()
    assert got["state"] == "no_source" and got["render"]["clip_id"] == clip["id"]
    assert "Запись удалена" in got["message"]


def test_clip_without_a_recording_is_built_from_the_proxy(tmp_path, monkeypatch):
    _require_ffmpeg()
    from app import preview_proxy

    client, store, source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    proxy = preview_proxy.proxy_path(int(source["id"]))
    proxy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source["local_path"], proxy)
    sp.Path(source["local_path"]).unlink()
    recipe, done = _build(store, plan["id"], tmp_path)
    assert done and recipe["parts"][0]["source_path"] == str(proxy)
    assert _frames(_join(recipe, tmp_path)) == recipe["total_frames"]


def test_cancel_stops_the_chain_and_keeps_ready_chunks(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    key, override = sp.job_key(store, plan["id"])
    recipe = sp.record(store, plan["id"], key, threading.Event(), override)
    cancel = threading.Event()
    done: list[int] = []

    def on_chunk(chunk: dict, _sec: float) -> None:
        done.append(chunk["index"])
        cancel.set()

    with pytest.raises(sp.Cancelled):
        sp.run_chain(recipe, recipe["chunks"], cancel, on_chunk)
    assert done and len(done) < len(recipe["chunks"])
    assert sp.chunk_ready(recipe, done[0])


def test_start_of_a_run_does_not_tear_the_timestamps():
    """setpts обрезает до целого: t0/TB = 506.99999… даёт дырку в кадр посреди прохода, чанк выходит короче."""
    recipe = {"fps": 30.0, "width": 854, "height": 480}
    args = sp._encoder_args(recipe, 507, 90, [45, 45], "yuv420p", 0, sp.Path("."))  # noqa: SLF001
    graph = args[args.index("-filter_complex") + 1]
    assert "setpts=round(PTS+16.900000/TB)" in graph


def test_recipe_survives_as_json(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    recipe, _done = _build(store, plan["id"], tmp_path)
    loaded = sp.load_recipe(recipe["key"])
    assert loaded is not None
    assert json.dumps(loaded["chunks"]) == json.dumps(recipe["chunks"])
    # Новый процесс: готовые чанки подхватываются без нового прохода.
    assert all(sp.chunk_ready(loaded, c["index"]) for c in loaded["chunks"])
