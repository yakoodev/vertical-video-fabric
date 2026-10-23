"""Пустой холст: клип без исходника — анимированный фон, слои и звук (app/canvas.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from test_api_pipeline import _client, _require_ffmpeg


def test_base_command_is_silent_green_vertical():
    from app.canvas import base_ffmpeg_args

    args = base_ffmpeg_args(Path("base.mp4"), 180)
    joined = " ".join(args)
    assert "color=c=0x00ff00:s=1080x1920:r=30:d=180" in joined
    assert "anullsrc=r=48000:cl=stereo" in joined
    assert args[args.index("-t") + 1] == "180" and args[-1] == "base.mp4"


def test_duration_bounds():
    from app.canvas import normalize_duration

    assert normalize_duration(None) == 15.0
    assert normalize_duration("12.5") == 12.5
    for bad in (3, 500, "abc", float("nan")):
        with pytest.raises(ValueError):
            normalize_duration(bad)


@pytest.fixture
def canvas_api(tmp_path, monkeypatch):
    _require_ffmpeg()
    import app.canvas as canvas

    # Маленький кадр — тест быстрее; длина основы та же (максимум куска).
    monkeypatch.setattr(canvas, "CANVAS_WIDTH", 108)
    monkeypatch.setattr(canvas, "CANVAS_HEIGHT", 192)
    return _client(tmp_path, monkeypatch)


def test_canvas_clip_api_hidden_source_and_backdrop(canvas_api):
    client, store = canvas_api
    resp = client.post("/api/canvas-clips", json={"duration": 12, "background": "grid", "title": "Промо"})
    assert resp.status_code == 200, resp.text
    plan = resp.json()
    assert plan["title"] == "Промо"
    assert [(s["start_sec"], s["end_sec"]) for s in plan["segments"]] == [(0, 12)]
    assert plan["render_settings"]["backdrop"]["kind"] == "grid"
    assert plan["editor_url"] == f"/projects/{plan['source_id']}/candidates?clip={plan['id']}"

    source = store.get_source(plan["source_id"])
    assert source["source_type"] == "canvas" and source["chroma_key"]["color"] == "#00ff00"
    assert source["duration_sec"] >= 179.5 and Path(source["local_path"]).is_file()
    # В «Проектах» холста нет.
    assert all(s["id"] != source["id"] for s in client.get("/api/sources").json())

    # Второй холст — та же основа, свой фон.
    second = client.post("/api/canvas-clips", json={
        "duration": 30, "name": "Второй",
        "background": {"kind": "waves", "colors": ["#ff3366", "#2244ff"], "speed": 1.5}}).json()
    assert second["source_id"] == source["id"] and second["title"] == "Второй"
    assert second["render_settings"]["backdrop"]["colors"] == ["#ff3366", "#2244ff"]

    assert client.post("/api/canvas-clips", json={"duration": 2}).status_code == 400
    assert client.post("/api/canvas-clips", json={"duration": 999}).status_code == 400


def test_editor_sees_canvas_by_source_type(canvas_api):
    """Редактор узнаёт холст по ``source_type`` проекта (GET /api/sources/{id}) и рисует
    фон клипа из ``render_settings.backdrop`` вместо зелёных кадров основы."""
    client, _store = canvas_api
    plan = client.post("/api/canvas-clips", json={"duration": 9, "background": "waves"}).json()
    source = client.get(f"/api/sources/{plan['source_id']}")
    assert source.status_code == 200, source.text
    body = source.json()
    assert body["source_type"] == "canvas"
    got = next(p for p in body["clip_plans"] if p["id"] == plan["id"])
    assert got["render_settings"]["backdrop"]["kind"] == "waves"


def test_canvas_base_recreated_when_file_is_gone(canvas_api):
    from app.canvas import ensure_canvas_source

    _client_, store = canvas_api
    first = ensure_canvas_source(store)
    Path(first["local_path"]).unlink()
    again = ensure_canvas_source(store)
    assert again["id"] == first["id"] and Path(again["local_path"]).is_file()


def test_canvas_renders_through_keyed_backdrop(canvas_api):
    """Рендер холста — путь хромакея: ключ на всю основу, фон клипа в компоновщик."""
    from app.render import ClipRenderService, _keyed_render

    client, store = canvas_api
    plan = client.post("/api/canvas-clips", json={"duration": 8, "background": "bokeh"}).json()
    plan = store.get_clip_plan(plan["id"])
    visual = ClipRenderService(store)._with_chroma_key(plan, None)
    assert visual["key"]["color"] == "#00ff00" and visual["background"]["kind"] == "bokeh"
    base, motion = _keyed_render({"id": 1, "color_style": "warm"}, visual)
    assert base["key_fill"] == "#00ff00" and base["color_style"] == "none" and motion["key"]["grade"] is not None


def test_create_canvas_clip_tool_and_full_frame_images(canvas_api):
    """Инструмент агента/MCP: холст создаётся, картинка во весь кадр не «закрывает видео»."""
    from app.agent_tools import ToolContext, _cover_problems, list_tools, run_tool

    _client_, store = canvas_api
    assert "create_canvas_clip" in {t.name for t in list_tools(include_discovery=True)}
    out, ok = run_tool(ToolContext(store=store), "create_canvas_clip",
                       {"duration": 10, "background": "aurora", "title": "Картинки"})
    assert ok, out
    assert out["duration"] == 10 and out["backdrop"]["kind"] == "aurora" and out["project_id"]

    full = {"id": "bg", "type": "image", "props": {"w": 1080, "h": 1920}}
    assert _cover_problems(ToolContext(store=store, clip_plan_id=out["clip_id"]), [full]) == []
    got, ok = run_tool(ToolContext(store=store, clip_plan_id=out["clip_id"]), "get_clip", {})
    assert ok and got["render"]["canvas"] is True and "Пустой холст" in got["render"]["key_note"]

    bad, ok = run_tool(ToolContext(store=store), "create_canvas_clip", {"duration": 1})
    assert not ok and "холст не создан" in bad["error"]


def test_source_upload_api_returns_json_id(tmp_path, monkeypatch):
    """POST /api/sources multipart — тот же ingest, что у формы, ответ JSON с id; тело
    ``vvf.py upload`` (поток multipart) сервер принимает."""
    import importlib.util
    import sys

    from test_api_pipeline import _make_test_video

    _require_ffmpeg()
    client, store = _client(tmp_path, monkeypatch)
    video = tmp_path / "своё видео.mp4"
    _make_test_video(video)
    monkeypatch.setattr(sys, "stdout", sys.stdout)  # vvf при импорте перенастраивает вывод — вернём
    monkeypatch.setattr(sys, "stderr", sys.stderr)
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("vvf_cli", root / "tools" / "vvf.py")
    vvf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vvf)
    chunks, size, ctype = vvf._multipart_file(str(video))
    body = b"".join(chunks)
    assert len(body) == size
    resp = client.post("/api/sources", content=body, headers={"Content-Type": ctype})
    assert resp.status_code == 200, resp.text
    source = resp.json()
    assert source["id"] and source["original_filename"] == "своё видео.mp4" and source["duration_sec"] > 6
    assert store.get_source(source["id"])["source_type"] == "upload"
