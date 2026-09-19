"""Clip covers: settings, time mapping, upload, burn-in as the first frame."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.clip_settings import normalize_cover
from app.cover import cover_output_time
from test_api_pipeline import _client, _make_test_video, _require_ffmpeg


def test_cover_settings_reject_foreign_paths():
    assert normalize_cover({"mode": "image", "image": "../../etc/passwd.jpg"})["mode"] == "none"
    assert normalize_cover({"mode": "image", "image": "a" * 32 + ".png"})["mode"] == "none"
    ok = normalize_cover({"mode": "image", "image": "a" * 32 + ".jpg"})
    assert ok["mode"] == "image" and ok["burn"] is True and ok["burn_sec"] == 0.1
    assert normalize_cover({"mode": "frame", "burn_sec": 9})["burn_sec"] == 0.5
    assert normalize_cover(None)["mode"] == "none"


def test_cover_time_maps_piece_offset_onto_output():
    # Pieces 5 s + 5 s with a 0.5 s transition: piece 1 starts at 4.5 s of the output.
    assert cover_output_time([5.0, 5.0], 0.5, 1, 1.0) == pytest.approx(5.5)
    assert cover_output_time([5.0, 5.0], 0.0, 0, 2.0) == pytest.approx(2.0)
    # Clamped: past the end of the piece / the video, and a piece that doesn't exist.
    assert cover_output_time([5.0, 5.0], 0.0, 0, 99) == pytest.approx(4.95)
    assert cover_output_time([5.0], 0.0, 7, 1.0) == pytest.approx(1.0)


def _mean_rgb(video: Path, t: float) -> tuple[float, float, float]:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{t}", "-i", str(video), "-frames:v", "1",
         "-vf", "scale=8:8", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True,
    ).stdout
    n = len(raw) // 3
    return tuple(sum(raw[i::3]) / n for i in range(3))  # type: ignore[return-value]


def _setup(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    video = tmp_path / "cover-src.mp4"
    _make_test_video(video)
    with video.open("rb") as fileobj:
        source = client.post("/api/sources", files={"file": ("cover-src.mp4", fileobj, "video/mp4")}).raise_for_status().json()
    analysis = store.create_ai_analysis(source["id"], "mock", status="succeeded")
    a = store.create_ai_segment(analysis["id"], {"start_sec": 0, "end_sec": 5, "title": "A"})
    b = store.create_ai_segment(analysis["id"], {"start_sec": 1, "end_sec": 6, "title": "B"})
    plan = store.create_clip_plan(source["id"], analysis["id"], "Обложка", segment_ids=[a["id"], b["id"]])
    preset = client.post(
        "/api/ffmpeg-presets",
        json={"label": "cover", "output_width": 180, "output_height": 320, "fps": 25, "scale_mode": "cover", "extra": {"crf": 30}},
    ).raise_for_status().json()
    return client, store, plan, preset


def test_uploaded_cover_is_burned_in_as_the_first_frame(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, store, plan, preset = _setup(tmp_path, monkeypatch)
    red = tmp_path / "red.png"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=400x300", "-frames:v", "1", str(red)], check=True)
    with red.open("rb") as f:
        up = client.post("/api/covers", files={"file": ("red.png", f, "image/png")}).raise_for_status().json()
    assert client.get(up["url"]).status_code == 200

    spec = client.get(f"/api/clip-plans/{plan['id']}/spec").json()
    spec["render"]["preset_id"] = preset["id"]
    spec["render"]["cover"] = {"mode": "image", "image": up["image"], "burn": True, "burn_sec": 0.2}
    client.put(f"/api/clip-plans/{plan['id']}/spec", json=spec).raise_for_status()
    clip = client.post(f"/api/clip-plans/{plan['id']}/render", json={}).raise_for_status().json()
    assert clip["status"] == "succeeded", clip.get("error")
    assert clip["cover_url"] and client.get(clip["cover_url"]).status_code == 200

    out = Path(store.get_clip(clip["id"])["output_path"])
    r, g, bl = _mean_rgb(out, 0.0)
    assert r > 180 and g < 80 and bl < 80, (r, g, bl)  # first frame = the red cover
    r2, g2, b2 = _mean_rgb(out, 2.0)
    assert not (r2 > 180 and g2 < 80 and b2 < 80)  # the rest is the video
    # Burn-in must not change the length (sound copied, duration kept).
    assert clip["duration_sec"] == pytest.approx(10.0, abs=0.25)


def test_frame_cover_without_burn_keeps_video_untouched(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, _store, plan, preset = _setup(tmp_path, monkeypatch)
    spec = client.get(f"/api/clip-plans/{plan['id']}/spec").json()
    spec["render"]["preset_id"] = preset["id"]
    spec["render"]["cover"] = {"mode": "frame", "piece": 1, "offset": 1.5, "burn": False}
    client.put(f"/api/clip-plans/{plan['id']}/spec", json=spec).raise_for_status()
    clip = client.post(f"/api/clip-plans/{plan['id']}/render", json={}).raise_for_status().json()
    assert clip["status"] == "succeeded"
    cover = client.get(clip["cover_url"])
    assert cover.status_code == 200 and cover.headers["content-type"] == "image/jpeg"
    renders = client.get(f"/api/clip-plans/{plan['id']}/spec").json()["renders"]
    assert renders[0]["cover_url"] == clip["cover_url"]


def test_not_an_image_is_rejected(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, _store, _plan, _preset = _setup(tmp_path, monkeypatch)
    resp = client.post("/api/covers", files={"file": ("x.png", b"not an image at all", "image/png")})
    assert resp.status_code == 400
