"""Цветокоррекция по кускам (app/piece_color.py): нормализация, фильтр ffmpeg, модель подгонки,
инструменты set_piece_color / match_piece_colors, маршрут рендера и настоящий ffmpeg на
синтетике из двух кусков разного цвета — подгонка сводит яркость."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.agent_tools import ToolContext, clip_timeline, run_tool
from app.clip_history import snapshot
from app.cuts import plan_cuts
from app.piece_color import (
    attach, match_fix, measure, normalize_color_fix, normalize_fix, predict, preview_css, video_filter,
)
from app.render import build_ffmpeg_render_args
from test_api_pipeline import _client, _require_ffmpeg
from test_cuts import _mock_render


def _ctx(store, plan_id):
    return ToolContext(store=store, clip_plan_id=plan_id, on_first_change=lambda: snapshot(store, plan_id, "тест"))


def test_normalize_attach_and_filter():
    raw = [{"piece": 3, "exposure": 5, "contrast": 1, "tint": -0.4}, {"piece": 4, "saturation": 1},
           {"piece": "x"}, {"piece": 5, "temperature": 0.5}, {"piece": 5, "temperature": 0.25}]
    clean = normalize_color_fix(raw)
    assert clean == [{"piece": 3, "exposure": 2.0, "tint": -0.4}, {"piece": 5, "temperature": 0.25}]
    segs = attach([{"id": 3}, {"id": 4}, {"id": 5}], {"color_fix": clean})
    assert segs[0]["color_fix"] == {"exposure": 2.0, "tint": -0.4} and "color_fix" not in segs[1]
    chain = video_filter({"exposure": 1, "contrast": 1.2, "saturation": 0.5, "temperature": 0.5})
    assert chain.startswith("format=yuv420p,lutyuv=y='clip((val-16)*2.00000+16,0,255)'")
    assert "u='clip(val+-8.000,0,255)'" in chain and "v='clip(val+8.000,0,255)'" in chain
    assert chain.endswith("eq=contrast=1.200:saturation=0.500")
    assert video_filter({}) == "" and video_filter({"contrast": 1}) == ""
    assert preview_css({"exposure": 1, "temperature": 0.5}) == "brightness(2.000) sepia(0.150)"


def test_match_model_hits_target():
    target = {"y": 120.0, "u": 120.0, "v": 136.0, "sat": 20.0}
    stats = {"y": 60.0, "u": 132.0, "v": 124.0, "sat": 12.0}
    fix = match_fix(stats, target)
    got = predict(stats, fix)
    assert got["y"] == pytest.approx(120.0, abs=0.5)
    assert got["u"] == pytest.approx(120.0, abs=0.5) and got["v"] == pytest.approx(136.0, abs=0.5)
    assert got["sat"] == pytest.approx(20.0, abs=0.5)
    half = predict(stats, match_fix(stats, target, strength=0.5))
    assert 60 < half["y"] < 120
    # Кадр-ночь против дня: экспозиция упирается в предел, а не уводит в белое.
    assert normalize_fix(match_fix({"y": 18.0, "u": 128, "v": 128, "sat": 1}, target))["exposure"] == 1.5


def test_tools_store_fix_by_piece_id_and_render_routes_through_parts(tmp_path, monkeypatch):
    store, service, plan, preset, calls = _mock_render(tmp_path, monkeypatch, [(0, 6), (10, 16)])
    ctx = _ctx(store, plan["id"])
    out, ok = run_tool(ctx, "set_piece_color", {"piece": 1, "exposure": 0.5, "temperature": -0.3})
    assert ok, out
    seg_b = plan["segments"][1]["id"]
    assert store.get_clip_plan(plan["id"])["render_settings"]["color_fix"] == [
        {"piece": seg_b, "exposure": 0.5, "temperature": -0.3}]
    assert clip_timeline(store, plan["id"])["pieces"][1]["color_fix"] == {"exposure": 0.5, "temperature": -0.3}
    # Не переданное поле остаётся, вне рамок — ошибка, reset — снять.
    out, ok = run_tool(ctx, "set_piece_color", {"piece": 1, "saturation": 1.3})
    assert ok and out["color_fix"] == {"exposure": 0.5, "saturation": 1.3, "temperature": -0.3}
    assert not run_tool(ctx, "set_piece_color", {"piece": 1, "exposure": 3})[1]
    assert not run_tool(ctx, "set_piece_color", {"piece": 7, "exposure": 1})[1]

    # Вырезы: части куска несут его поправку.
    segs = store.get_clip_plan(plan["id"])["segments"]
    parts = plan_cuts(segs, [{"start": 8.0, "end": 9.0}], 25.0).parts
    assert [p.get("color_fix") for p in parts] == [None, segs[1]["color_fix"], segs[1]["color_fix"]]

    # Рендер: куски с поправкой идут путём частей, фильтр — только у второго, до лука.
    service.render_clip_plan(plan["id"], ffmpeg_preset_id=preset["id"])
    graphs = {i: c[c.index("-filter_complex") + 1] for i, c in
              ((i, next(c for c in calls if any(f"part-00{i}" in str(a) for a in c))) for i in (0, 1))}
    assert "lutyuv" not in graphs[0]
    assert "lutyuv=y='clip((val-16)*1.41421+16,0,255)'" in graphs[1] and "saturation=1.300" in graphs[1]

    out, ok = run_tool(ctx, "set_piece_color", {"piece": 1, "reset": True})
    assert ok and out["color_fix"] == {} and store.get_clip_plan(plan["id"])["render_settings"]["color_fix"] == []


def test_editor_autosave_keeps_color_fix(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import app.main as app_main
    from app.settings import settings

    store, _service, plan, _preset, _calls = _mock_render(tmp_path, monkeypatch, [(0, 6), (10, 16)])
    monkeypatch.setattr(settings, "auth_enabled", False)
    monkeypatch.setattr(app_main, "store", store)
    client = TestClient(app_main.app)
    resp = client.post(f"/api/clip-plans/{plan['id']}/piece-color", json={"piece": 0, "exposure": -0.5})
    assert resp.status_code == 200, resp.text
    resp = client.patch(f"/api/clip-plans/{plan['id']}/render-settings", json={"settings": {"mirror": True}})
    assert resp.status_code == 200, resp.text
    assert store.get_clip_plan(plan["id"])["segments"][0]["color_fix"] == {"exposure": -0.5}
    assert client.post(f"/api/clip-plans/{plan['id']}/piece-color", json={"piece": 0, "exposure": 9}).status_code == 422


# ------------------------------------------------------------------ настоящий ffmpeg


def _make_two_scenes(path: Path) -> None:
    """4 с: первые 2 с — тёмная тёплая сцена, следующие 2 с — светлая прохладная (+ тон для звука)."""
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=0x5a3a22:s=320x240:r=25:d=2",
         "-f", "lavfi", "-i", "color=c=0xa8b4c4:s=320x240:r=25:d=2",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=4",
         "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0,noise=alls=6:allf=t,format=yuv420p[v]",
         "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
         str(path)], check=True, capture_output=True)


def _frame_stats(path: Path, at: float) -> dict:
    return measure(path, at - 0.1, at + 0.1, count=1)


def test_real_ffmpeg_piece_filter_matches_prediction(tmp_path):
    _require_ffmpeg()
    src, out = tmp_path / "two.mp4", tmp_path / "b.mp4"
    _make_two_scenes(src)
    a, b = measure(src, 0.0, 2.0), measure(src, 2.0, 4.0)
    assert b["y"] - a["y"] > 50  # сцены и правда разные
    fix = match_fix(b, predict(a, {}))
    preset = {"output_width": 180, "output_height": 320, "fps": 25, "piece_color": fix}
    subprocess.run(build_ffmpeg_render_args(src, out, 2.0, 4.0, preset), check=True, capture_output=True)
    got, want = measure(out, 0.0, 2.0), predict(b, fix)
    # Фильтр делает ровно то, что обещает модель, и кусок B становится как A.
    for key in ("y", "u", "v"):
        assert abs(got[key] - want[key]) < 3, (key, got, want)
        assert abs(got[key] - a[key]) < 4, (key, got, a)


def test_match_piece_colors_on_real_clip_reduces_luma_gap(tmp_path, monkeypatch):
    """Весь путь: источник из двух сцен, клип из двух кусков, match_piece_colors к первому,
    рендер — разница яркости кусков в готовом ролике резко меньше, чем без подгонки."""
    _require_ffmpeg()
    from app.render import ClipRenderService

    client, store = _client(tmp_path, monkeypatch)
    video = tmp_path / "two-scenes.mp4"
    _make_two_scenes(video)
    with video.open("rb") as fileobj:
        source = client.post("/api/sources", files={"file": ("two-scenes.mp4", fileobj, "video/mp4")}).raise_for_status().json()
    analysis = store.create_ai_analysis(source["id"], "mock", status="succeeded")
    a = store.create_ai_segment(analysis["id"], {"start_sec": 0, "end_sec": 2, "title": "A"}, min_duration=1)
    b = store.create_ai_segment(analysis["id"], {"start_sec": 2, "end_sec": 4, "title": "B"}, min_duration=1)
    plan = store.create_clip_plan(source["id"], analysis["id"], "План", segment_ids=[a["id"], b["id"]])
    store.set_clip_plan_render_settings(plan["id"], {"end_hold_sec": 0})
    preset = store.create_ffmpeg_preset("S", output_width=180, output_height=320, fps=25)
    service = ClipRenderService(store)

    def gap() -> float:
        clip = service.render_clip_plan(plan["id"], ffmpeg_preset_id=preset["id"])
        assert clip["status"] == "succeeded", clip.get("error")
        path = Path(clip["output_path"])
        return abs(_frame_stats(path, 1.0)["y"] - _frame_stats(path, 3.0)["y"])

    before = gap()
    out, ok = run_tool(_ctx(store, plan["id"]), "match_piece_colors", {"reference": 0})
    assert ok, out
    assert out["pieces"][0]["piece"] == 1 and out["pieces"][0]["color_fix"]["exposure"] < 0
    assert out["pieces"][0]["luma_gap_after"] < out["pieces"][0]["luma_gap_before"] / 5
    after = gap()
    assert before > 50 and after < before / 5, (before, after)
