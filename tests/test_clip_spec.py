"""The clip file (vvf.clip/1): export, all-or-nothing apply, copy-on-write, render."""

from __future__ import annotations

import pytest

from test_api_pipeline import _client, _make_test_video, _require_ffmpeg


def _setup(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    video = tmp_path / "spec-source.mp4"
    _make_test_video(video)  # 7 s
    with video.open("rb") as fileobj:
        source = client.post(
            "/api/sources", files={"file": ("spec-source.mp4", fileobj, "video/mp4")}
        ).raise_for_status().json()
    analysis = store.create_ai_analysis(source["id"], "mock", status="succeeded")
    a = store.create_ai_segment(analysis["id"], {"start_sec": 0, "end_sec": 5, "title": "A"})
    b = store.create_ai_segment(analysis["id"], {"start_sec": 1, "end_sec": 6, "title": "B"})
    plan = store.create_clip_plan(source["id"], analysis["id"], "План", segment_ids=[a["id"], b["id"]])
    return client, store, source, plan, a, b


def test_export_has_pieces_render_defaults_and_context(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, _store, source, plan, a, b = _setup(tmp_path, monkeypatch)
    spec = client.get(f"/api/clip-plans/{plan['id']}/spec").raise_for_status().json()
    assert spec["schema"] == "vvf.clip/1"
    assert [p["segment_id"] for p in spec["pieces"]] == [a["id"], b["id"]]
    assert spec["source"]["id"] == source["id"]
    assert spec["render"]["transition"]["type"] == "cut"
    assert spec["renders"] == []


def test_apply_reorders_retimes_adds_and_removes(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, store, _source, plan, a, b = _setup(tmp_path, monkeypatch)
    spec = client.get(f"/api/clip-plans/{plan['id']}/spec").json()
    spec["title"] = "Новое название"
    spec["notes"] = "агент: сначала реакция, потом повод"
    # B first (retimed), A dropped, a brand-new piece at the end.
    spec["pieces"] = [
        {**spec["pieces"][1], "start_sec": 1.5, "end_sec": 6.5},
        {"start_sec": 2, "end_sec": 7, "title": "Новый"},
    ]
    spec["render"]["transition"] = {"type": "fadeblack", "duration": 0.4}
    out = client.put(f"/api/clip-plans/{plan['id']}/spec", json=spec).raise_for_status().json()

    fresh = out["spec"]
    assert fresh["title"] == "Новое название"
    assert fresh["notes"].startswith("агент")
    assert fresh["pieces"][0]["segment_id"] == b["id"]
    assert fresh["pieces"][0]["start_sec"] == 1.5
    assert fresh["pieces"][1]["title"] == "Новый"
    assert a["id"] not in [p["segment_id"] for p in fresh["pieces"]]
    assert fresh["render"]["transition"]["type"] == "fadeblack"
    assert "состав кусков" in out["changes"]
    assert store.get_clip_plan(plan["id"])["render_settings"]["transition"]["type"] == "fadeblack"


def test_invalid_file_reports_every_problem_and_changes_nothing(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    before = client.get(f"/api/clip-plans/{plan['id']}/spec").json()
    bad = {
        **before,
        "title": "Не должно сохраниться",
        "pieces": [
            {"segment_id": 999999, "start_sec": 0, "end_sec": 5},
            {"start_sec": 0, "end_sec": 2},
            {"start_sec": 3, "end_sec": 30},
        ],
    }
    resp = client.put(f"/api/clip-plans/{plan['id']}/spec", json=bad)
    assert resp.status_code == 422
    problems = resp.json()["detail"]["problems"]
    assert len(problems) == 3, problems
    after = client.get(f"/api/clip-plans/{plan['id']}/spec").json()
    assert after["title"] == before["title"]
    assert after["pieces"] == before["pieces"]
    check = client.post(f"/api/clip-plans/{plan['id']}/spec/validate", json=bad).json()
    assert check["ok"] is False and len(check["problems"]) == 3


def test_retiming_a_shared_piece_copies_it(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, store, source, plan, a, b = _setup(tmp_path, monkeypatch)
    other = store.create_clip_plan(source["id"], plan["analysis_id"], "Другой", segment_ids=[a["id"]])
    spec = client.get(f"/api/clip-plans/{plan['id']}/spec").json()
    spec["pieces"][0] = {**spec["pieces"][0], "start_sec": 0.5, "end_sec": 5.5}
    out = client.put(f"/api/clip-plans/{plan['id']}/spec", json=spec).raise_for_status().json()
    new_first = out["spec"]["pieces"][0]
    assert new_first["segment_id"] != a["id"]
    assert new_first["start_sec"] == 0.5
    # The other clip still has the original piece, untouched.
    untouched = store.get_clip_plan(other["id"])["segments"][0]
    assert untouched["id"] == a["id"] and float(untouched["start_sec"]) == 0.0


def test_render_uses_the_file_and_reports_qc(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, _store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    preset = client.post(
        "/api/ffmpeg-presets",
        json={"label": "spec", "output_width": 180, "output_height": 320, "fps": 25,
              "scale_mode": "cover", "extra": {"crf": 30}},
    ).raise_for_status().json()
    spec = client.get(f"/api/clip-plans/{plan['id']}/spec").json()
    spec["render"]["preset_id"] = preset["id"]
    spec["render"]["transition"] = {"type": "fade", "duration": 0.5}
    client.put(f"/api/clip-plans/{plan['id']}/spec", json=spec).raise_for_status()

    clip = client.post(f"/api/clip-plans/{plan['id']}/render", json={}).raise_for_status().json()
    assert clip["status"] == "succeeded", clip.get("error")
    assert clip["duration_sec"] == pytest.approx(9.5, abs=0.2)
    assert clip["qc"] is not None
    assert clip["qc"]["loudness_lufs"] is not None
    # Test preset is 180×320, the report must say so.
    assert any(i["code"] == "size" for i in clip["qc"]["issues"])
    renders = client.get(f"/api/clip-plans/{plan['id']}/spec").json()["renders"]
    assert renders[0]["clip_id"] == clip["id"] and renders[0]["qc"] is not None
