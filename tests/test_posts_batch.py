"""Batch publishing: each clip under its own title, spread out in time."""

from __future__ import annotations

from datetime import datetime

from test_api_pipeline import _client, _make_test_video, _require_ffmpeg


def _two_rendered_clips(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    video = tmp_path / "batch-src.mp4"
    _make_test_video(video)
    with video.open("rb") as fileobj:
        source = client.post("/api/sources", files={"file": ("batch-src.mp4", fileobj, "video/mp4")}).raise_for_status().json()
    analysis = store.create_ai_analysis(source["id"], "mock", status="succeeded")
    preset = client.post(
        "/api/ffmpeg-presets",
        json={"label": "b", "output_width": 180, "output_height": 320, "fps": 25, "scale_mode": "cover", "extra": {"crf": 32}},
    ).raise_for_status().json()
    clips = []
    for title, (a, b) in (("Первый", (0, 5)), ("Второй", (1, 6))):
        seg = store.create_ai_segment(analysis["id"], {"start_sec": a, "end_sec": b, "title": title})
        plan = store.create_clip_plan(source["id"], analysis["id"], title, segment_ids=[seg["id"]])
        clip = client.post(f"/api/clip-plans/{plan['id']}/render", json={"ffmpeg_preset_id": preset["id"]}).raise_for_status().json()
        assert clip["status"] == "succeeded", clip.get("error")
        clips.append(clip)
    account = store.upsert_account("youtube", "batch-test", "SID=a; HSID=b; SSID=c; APISID=d; SAPISID=e")
    return client, store, clips, account


def test_batch_posts_each_clip_with_its_own_title_and_spacing(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, store, clips, account = _two_rendered_clips(tmp_path, monkeypatch)
    out = client.post(
        "/api/clips/posts-batch",
        json={"clip_ids": [c["id"] for c in clips], "targets": [account["id"]],
              "start_at": "2031-01-01 10:00", "interval_minutes": 90},
    ).raise_for_status().json()
    assert out["skipped"] == []
    jobs = out["jobs"]
    assert [j["title"] for j in jobs] == ["Первый", "Второй"]
    times = [datetime.strptime(j["scheduled_at"], "%Y-%m-%d %H:%M:%S") for j in jobs]
    assert times[0] == datetime(2031, 1, 1, 10, 0)
    assert (times[1] - times[0]).total_seconds() == 90 * 60


def test_batch_reports_unpostable_clips_instead_of_failing(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, _store, clips, account = _two_rendered_clips(tmp_path, monkeypatch)
    out = client.post(
        "/api/clips/posts-batch",
        json={"clip_ids": [clips[0]["id"], 999999], "targets": [account["id"]]},
    ).raise_for_status().json()
    assert len(out["jobs"]) == 1
    assert out["skipped"] and out["skipped"][0]["clip_id"] == 999999
    # No start and no interval: goes out right away.
    assert not out["jobs"][0].get("scheduled_at")


def test_batch_needs_accounts(tmp_path, monkeypatch):
    client, _store = _client(tmp_path, monkeypatch)
    resp = client.post("/api/clips/posts-batch", json={"clip_ids": [1], "targets": []})
    assert resp.status_code == 400
