"""Скорость страниц: сжатие текста, кэш сборки, картинки вместо видео в списках, лёгкий список."""

from __future__ import annotations

import gzip
import json

from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.testclient import TestClient

from app import thumbs
from app.compression import IMMUTABLE, ImmutableAssets, TextGzipMiddleware, _Gzip
from app.settings import settings
from test_api_pipeline import _client
from test_cover_variants import _no_compositor, _rendered, _require_ffmpeg

BIG = {"items": ["слово"] * 2000}


def _app(tmp_path) -> TestClient:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00" * 5000)
    page = tmp_path / "app.js"
    page.write_text("console.log(1);" * 500, encoding="utf-8")
    (tmp_path / "dist").mkdir(exist_ok=True)
    (tmp_path / "dist" / "index-abc.js").write_text("var a=1;" * 500, encoding="utf-8")

    async def events():
        yield b"data: 1\n\n"
        yield b"data: 2\n\n"

    async def lines():
        yield b'{"a":1}' * 300
        yield b'{"b":2}' * 300

    app = Starlette(routes=[
        Route("/big", lambda r: JSONResponse(BIG)),
        Route("/small", lambda r: JSONResponse({"a": 1})),
        Route("/fail", lambda r: JSONResponse(BIG, status_code=500)),
        Route("/video", lambda r: FileResponse(video, media_type="video/mp4")),
        Route("/js", lambda r: FileResponse(page, media_type="text/javascript")),
        Route("/text", lambda r: PlainTextResponse("текст " * 1000)),
        Route("/sse", lambda r: StreamingResponse(events(), media_type="text/event-stream")),
        Route("/stream", lambda r: StreamingResponse(lines(), media_type="application/json")),
        Route("/done", lambda r: Response("x" * 5000, media_type="text/html", headers={"Content-Encoding": "br"})),
        Mount("/assets", ImmutableAssets(directory=tmp_path / "dist")),
    ])
    app.add_middleware(TextGzipMiddleware)
    return TestClient(app)


def test_json_and_js_are_gzipped(tmp_path):
    client = _app(tmp_path)
    r = client.get("/big", headers={"Accept-Encoding": "gzip"})
    assert r.headers["content-encoding"] == "gzip"
    assert "accept-encoding" in r.headers["vary"].lower()
    assert r.json() == BIG
    assert int(r.headers["content-length"]) < len(json.dumps(BIG)) / 3
    js = client.get("/js", headers={"Accept-Encoding": "gzip"})
    assert js.headers["content-encoding"] == "gzip"
    assert js.text == "console.log(1);" * 500
    assert js.headers["etag"].startswith("W/")


def test_no_gzip_without_accept(tmp_path):
    client = _app(tmp_path)
    for value in ("identity", "br", "gzip;q=0"):
        r = client.get("/big", headers={"Accept-Encoding": value})
        assert "content-encoding" not in r.headers, value
        assert r.json() == BIG


def test_small_failed_and_encoded_stay_as_is(tmp_path):
    client = _app(tmp_path)
    headers = {"Accept-Encoding": "gzip"}
    assert "content-encoding" not in client.get("/small", headers=headers).headers
    assert "content-encoding" not in client.get("/fail", headers=headers).headers
    assert client.get("/done", headers=headers).headers["content-encoding"] == "br"


def test_video_and_range_keep_exact_bytes(tmp_path):
    client = _app(tmp_path)
    video = client.get("/video", headers={"Accept-Encoding": "gzip"})
    assert "content-encoding" not in video.headers
    assert len(video.content) == 5000
    part = client.get("/video", headers={"Accept-Encoding": "gzip", "Range": "bytes=0-99"})
    assert part.status_code == 206
    assert part.headers["content-range"] == "bytes 0-99/5000"
    assert len(part.content) == 100
    js = client.get("/js", headers={"Accept-Encoding": "gzip", "Range": "bytes=0-9"})
    assert js.status_code == 206
    assert "content-encoding" not in js.headers
    assert js.text == "console.lo"


def test_sse_is_not_compressed_and_json_stream_decodes(tmp_path):
    client = _app(tmp_path)
    sse = client.get("/sse", headers={"Accept-Encoding": "gzip"})
    assert "content-encoding" not in sse.headers
    assert sse.text == "data: 1\n\ndata: 2\n\n"
    stream = client.get("/stream", headers={"Accept-Encoding": "gzip"})
    assert stream.headers["content-encoding"] == "gzip"
    assert "content-length" not in stream.headers
    assert stream.text == '{"a":1}' * 300 + '{"b":2}' * 300


def test_built_assets_are_immutable_and_revalidate_by_etag(tmp_path):
    client = _app(tmp_path)
    r = client.get("/assets/index-abc.js", headers={"Accept-Encoding": "gzip"})
    assert r.headers["cache-control"] == IMMUTABLE
    assert r.headers["content-encoding"] == "gzip"
    assert r.text == "var a=1;" * 500
    again = client.get("/assets/index-abc.js", headers={"If-None-Match": r.headers["etag"], "Accept-Encoding": "gzip"})
    assert again.status_code == 304
    assert client.get("/assets/nope.js").status_code == 404


def test_sources_lite_drops_ffprobe_only(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, _store, source, _plan, _clip = _rendered(tmp_path, monkeypatch)
    full = {s["id"]: s for s in client.get("/api/sources").json()}[source["id"]]
    lite = {s["id"]: s for s in client.get("/api/sources?lite=1").json()}[source["id"]]
    assert len(full["metadata_json"]) > 50
    assert lite["metadata_json"] == ""
    assert {k: v for k, v in full.items() if k != "metadata_json"} == {k: v for k, v in lite.items() if k != "metadata_json"}


def test_service_compresses_api_and_keeps_media_ranges(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, _store, _source, _plan, clip = _rendered(tmp_path, monkeypatch)
    api = client.get("/api/modules", headers={"Accept-Encoding": "gzip"})
    assert api.headers["content-encoding"] == "gzip"
    assert api.json()
    media = client.get(f"/media/clips/{clip['id']}", headers={"Accept-Encoding": "gzip", "Range": "bytes=0-99"})
    assert media.status_code == 206
    assert "content-encoding" not in media.headers
    assert len(media.content) == 100


def _thumb_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storyboard_dir", tmp_path / "boards")


def test_source_and_clip_thumbs_are_small_cached_stills(tmp_path, monkeypatch):
    _require_ffmpeg()
    _thumb_dir(tmp_path, monkeypatch)
    client, _store, source, _plan, clip = _rendered(tmp_path, monkeypatch)
    for url in (f"/media/sources/{source['id']}/thumb", f"/media/clips/{clip['id']}/thumb"):
        first = client.get(url)
        assert first.status_code == 200, url
        assert first.headers["content-type"] == "image/jpeg"
        assert first.content[:2] == b"\xff\xd8"
        assert "max-age" in first.headers["cache-control"]
    calls = []
    monkeypatch.setattr(thumbs, "_ffmpeg_still", lambda *a, **k: calls.append(a) or False)
    assert client.get(f"/media/sources/{source['id']}/thumb").status_code == 200
    assert client.get(f"/media/clips/{clip['id']}/thumb").status_code == 200
    assert not calls


def test_clip_thumb_prefers_cover_and_follows_its_change(tmp_path, monkeypatch):
    _require_ffmpeg()
    _thumb_dir(tmp_path, monkeypatch)
    client, store, _source, _plan, clip = _rendered(tmp_path, monkeypatch)
    _no_compositor(monkeypatch)
    cover = client.post(f"/api/clips/{clip['id']}/covers", json={"count": 1}).raise_for_status().json()["covers"][0]
    client.post(f"/api/clips/{clip['id']}/covers/{cover['id']}/choose", json={"burn": False}).raise_for_status()
    path = store.get_clip_cover_path(clip["id"])
    assert path
    made = thumbs.clip_thumb(store.get_clip(clip["id"]), path)
    assert "_cover_" in made.name


def test_thumb_of_missing_or_unknown_is_404(tmp_path, monkeypatch):
    _thumb_dir(tmp_path, monkeypatch)
    client, store = _client(tmp_path, monkeypatch)
    assert client.get("/media/sources/9999/thumb").status_code == 404
    assert client.get("/media/clips/9999/thumb").status_code == 404
    assert client.get("/media/montage-assets/9999/thumb").status_code == 404
    gone = store.create_montage_asset(kind="video", label="нет файла", file_path=str(tmp_path / "gone.mp4"))
    assert client.get(f"/media/montage-assets/{gone['id']}/thumb").status_code == 404
    sound = tmp_path / "s.mp3"
    sound.write_bytes(b"123")
    audio = store.create_montage_asset(kind="audio", label="звук", file_path=str(sound))
    assert client.get(f"/media/montage-assets/{audio['id']}/thumb").status_code == 404


def test_asset_thumbs_small_image_as_is_big_one_shrunk(tmp_path, monkeypatch):
    _require_ffmpeg()
    import subprocess

    _thumb_dir(tmp_path, monkeypatch)
    client, store = _client(tmp_path, monkeypatch)
    small = tmp_path / "small.png"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=red:s=64x64", "-frames:v", "1", str(small)], check=True)
    one = store.create_montage_asset(kind="image", label="малая", file_path=str(small), mime_type="image/png")
    r = client.get(f"/media/montage-assets/{one['id']}/thumb")
    assert r.status_code == 200
    assert r.content == small.read_bytes()

    big = tmp_path / "big.png"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1920x1920", "-frames:v", "1", str(big)], check=True)
    assert big.stat().st_size > thumbs.KEEP_BYTES
    two = store.create_montage_asset(kind="image", label="большая", file_path=str(big), mime_type="image/png")
    r = client.get(f"/media/montage-assets/{two['id']}/thumb")
    assert r.status_code == 200
    assert len(r.content) < big.stat().st_size / 2

    clip = tmp_path / "m.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x240:d=1", "-pix_fmt", "yuv420p", str(clip)], check=True)
    vid = store.create_montage_asset(kind="video", label="видео", file_path=str(clip), mime_type="video/mp4")
    r = client.get(f"/media/montage-assets/{vid['id']}/thumb")
    assert r.status_code == 200
    assert r.content[:2] == b"\xff\xd8"


def test_gzip_stream_is_standard_gzip():
    # httpx распаковывает сам; сырые байты должны читаться и обычным gzip (браузер, curl --compressed).
    gz = _Gzip()
    raw = gz.push(b"first ", False) + gz.push(b"second", True)
    assert gzip.decompress(raw) == b"first second"
