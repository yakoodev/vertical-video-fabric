"""Заголовок и описание сами после рендера, варианты площадок, правила канала в запросе."""

from __future__ import annotations

import json

import pytest

from app import projects as P
from app.ai import publish_meta as M
from app.settings import settings
from test_api_pipeline import _client, _make_test_video, _require_ffmpeg

REPLY = {
    "title": "Стример спалился в прямом эфире",
    "description": "Он не заметил, что камера включена.",
    "hashtags": ["#стрим", "shorts", "#фейл"],
    "youtube": {"title": "Стример спалился в эфире: камера была включена", "description": "Полный момент."},
    "tiktok": {"title": "когда забыл выключить камеру 😅", "description": "жиза"},
    "cover_hooks": ["Камера была включена 😱", "Он не знал", "#shorts Спалился"],
}


class FakeGemini:
    def __init__(self, reply=None, fail=False):
        self.reply = reply or REPLY
        self.fail = fail
        self.prompts: list[str] = []

    def generate_content(self, model, payload):
        self.prompts.append(payload["contents"][0]["parts"][0]["text"])
        if self.fail:
            raise RuntimeError("Gemini недоступен")
        return {"candidates": [{"content": {"parts": [{"text": json.dumps(self.reply, ensure_ascii=False)}]}}]}


def test_parse_keeps_platform_variants_and_cover_hooks():
    meta = M.parse_metadata(json.dumps(REPLY, ensure_ascii=False))
    assert meta["platforms"]["youtube"]["title"].startswith("Стример спалился")
    assert meta["platforms"]["tiktok"]["title"] == "когда забыл выключить камеру"  # без эмодзи
    assert meta["cover_hooks"] == ["Камера была включена", "Он не знал", "Спалился"]
    plain = M.parse_metadata(json.dumps({"title": "T", "description": "", "hashtags": []}))
    assert "platforms" not in plain and "cover_hooks" not in plain


def _plan_with_clip(store, title="Момент"):
    source = store.create_source("upload", settings.source_dir / "s.mp4", original_filename="s.mp4",
                                 duration_sec=600, status="ready")
    analysis = store.create_ai_analysis(source["id"], "manual", status="succeeded")
    seg = store.create_ai_segment(analysis["id"], {"start_sec": 10, "end_sec": 25, "title": title})
    plan = store.create_clip_plan(source["id"], analysis["id"], title, segment_ids=[seg["id"]])
    clip = store.create_clip(source["id"], clip_plan_id=plan["id"], status="succeeded")
    return source, plan, store.get_clip(clip["id"])


@pytest.fixture
def auto_on(monkeypatch):
    monkeypatch.setattr(settings, "auto_publish_meta", True)
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")


def test_auto_meta_writes_once_and_new_render_inherits(tmp_path, monkeypatch, auto_on):
    _client_, store = _client(tmp_path, monkeypatch)
    source, plan, clip = _plan_with_clip(store)
    pid = P.create_project(store, "Канал", kind="stream_channel", source_ids=[source["id"]])["id"]
    P.add_rule(store, "не писать слово «кринж» в названиях", kind="dont", project_id=pid)
    gemini = FakeGemini()
    res = M.auto_meta_after_render(store, clip, client=gemini)
    assert res["status"] == "generated"
    fresh = store.get_clip(clip["id"])
    assert fresh["title"] == REPLY["title"]
    assert fresh["description"] == "Он не заметил, что камера включена.\n\n#стрим #shorts #фейл"
    meta = fresh["publish_meta"]
    assert meta["source"] == "auto" and meta["platforms"]["youtube"]["title"].endswith("включена")
    assert "кринж" in gemini.prompts[0] and "Канал" in gemini.prompts[0]

    # Уже есть — второй раз не спрашиваем.
    assert M.auto_meta_after_render(store, fresh, client=gemini)["status"] == "skipped"
    # Новый рендер того же клипа — без запроса, с тем же текстом.
    second = store.create_clip(source["id"], clip_plan_id=plan["id"], status="succeeded")
    res2 = M.auto_meta_after_render(store, store.get_clip(second["id"]), client=FakeGemini(fail=True))
    assert res2["status"] == "inherited" and res2["from_clip"] == clip["id"]
    assert store.get_clip(second["id"])["title"] == REPLY["title"]
    assert store.get_clip(second["id"])["publish_meta"]["inherited_from"] == clip["id"]


def test_manual_title_is_inherited_and_failures_never_raise(tmp_path, monkeypatch, auto_on):
    _client_, store = _client(tmp_path, monkeypatch)
    source, plan, clip = _plan_with_clip(store)
    store.update_clip(clip["id"], title="Мой заголовок")
    second = store.create_clip(source["id"], clip_plan_id=plan["id"], status="succeeded")
    res = M.auto_meta_after_render(store, store.get_clip(second["id"]), client=FakeGemini(fail=True))
    assert res["status"] == "inherited" and store.get_clip(second["id"])["title"] == "Мой заголовок"

    _s2, _p2, other = _plan_with_clip(store, "Другой")
    res = M.auto_meta_after_render(store, other, client=FakeGemini(fail=True))
    assert res["status"] == "failed" and "недоступен" in res["error"]
    assert store.get_clip(other["id"])["title"] == "Другой" and store.get_clip(other["id"])["publish_meta"] is None


def test_toggle_and_key_gate_auto_meta(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    _source, _plan, clip = _plan_with_clip(store)
    monkeypatch.setattr(settings, "auto_publish_meta", True)
    monkeypatch.setattr(settings, "gemini_api_key", "")
    assert M.auto_meta_after_render(store, clip, client=FakeGemini())["status"] == "skipped"
    monkeypatch.setattr(settings, "gemini_api_key", "k")
    data = client.get("/api/settings").json()
    assert data["auto_publish_meta"] is True and data["gemini_configured"] is True
    off = client.put("/api/settings/auto-publish-meta", json={"enabled": False}).json()
    assert off["auto_publish_meta"] is False
    assert M.auto_meta_after_render(store, clip, client=FakeGemini())["reason"] == "выключено"


def test_platform_variant_goes_out_only_for_unedited_text(tmp_path, monkeypatch, auto_on):
    _client_, store = _client(tmp_path, monkeypatch)
    _source, _plan, clip = _plan_with_clip(store)
    M.auto_meta_after_render(store, clip, client=FakeGemini())
    fresh = store.get_clip(clip["id"])
    job = {"clip_id": clip["id"], "title": fresh["title"], "description": fresh["description"]}
    title, desc = M.platform_texts(store, job, "tiktok")
    assert title == "когда забыл выключить камеру" and desc == "жиза\n\n#стрим #shorts #фейл"
    assert M.platform_texts(store, job, "instagram") == (job["title"], job["description"])
    edited = {**job, "title": "Свой заголовок"}
    assert M.platform_texts(store, edited, "youtube") == ("Свой заголовок", job["description"])
    assert M.platform_texts(store, {"title": "a", "description": "b"}, "youtube") == ("a", "b")


def test_render_writes_meta_and_never_fails_on_gemini(tmp_path, monkeypatch, auto_on):
    _require_ffmpeg()
    client, store = _client(tmp_path, monkeypatch)
    video = tmp_path / "pm-src.mp4"
    _make_test_video(video)
    with video.open("rb") as fileobj:
        source = client.post("/api/sources", files={"file": ("pm.mp4", fileobj, "video/mp4")}).raise_for_status().json()
    analysis = store.create_ai_analysis(source["id"], "mock", status="succeeded")
    seg = store.create_ai_segment(analysis["id"], {"start_sec": 0, "end_sec": 5, "title": "A"})
    plan = store.create_clip_plan(source["id"], analysis["id"], "План", segment_ids=[seg["id"]])
    preset = client.post("/api/ffmpeg-presets", json={"label": "pm", "output_width": 180, "output_height": 320,
                                                      "fps": 25, "scale_mode": "cover"}).raise_for_status().json()
    spec = client.get(f"/api/clip-plans/{plan['id']}/spec").json()
    spec["render"]["preset_id"] = preset["id"]
    client.put(f"/api/clip-plans/{plan['id']}/spec", json=spec).raise_for_status()

    broken = FakeGemini(fail=True)
    monkeypatch.setattr(M, "GeminiClient", lambda *a, **k: broken)
    clip = client.post(f"/api/clip-plans/{plan['id']}/render", json={}).raise_for_status().json()
    assert clip["status"] == "succeeded" and clip["title"] == "План" and broken.prompts

    good = FakeGemini()
    monkeypatch.setattr(M, "GeminiClient", lambda *a, **k: good)
    clip2 = client.post(f"/api/clip-plans/{plan['id']}/render", json={}).raise_for_status().json()
    assert clip2["status"] == "succeeded" and clip2["title"] == REPLY["title"]
    assert clip2["publish_meta"]["cover_hooks"][0] == "Камера была включена"
