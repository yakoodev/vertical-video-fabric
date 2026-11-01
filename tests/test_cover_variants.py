"""Варианты обложки готового клипа (app/cover_variants.py), их API, MCP и связь с автопилотом."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app import autopilot as A
from app import cover_variants as V
from app import projects as P
from app.agent_tools import ImageResult, ToolContext, run_tool
from app.publish_format import image_size
from test_api_pipeline import _client, _make_test_video, _require_ffmpeg

# --------------------------------------------------------------------------- #
# Чистые правила
# --------------------------------------------------------------------------- #


def test_hook_text_is_cleaned():
    assert V.clean_hook("Вот это поворот 😱🔥 #shorts") == "Вот это поворот"
    assert V.clean_hook("Смотри в TikTok и на YouTube Shorts") == "Смотри в и на"
    assert len(V.clean_hook("раз два три четыре пять шесть семь").split()) == 5
    assert V.clean_hook("🔥🔥🔥") == ""
    assert V.clean_hook("«Он» сказал: «нет»!") == "Он сказал: нет!"


def test_fallback_hooks_from_title_are_distinct():
    hooks = V.fallback_hooks("Стримерша паникует из-за запретки в чате #shorts", 3)
    assert len(hooks) == 3 and len({h.upper() for h in hooks}) == 3
    assert hooks[0] == "Стримерша паникует из-за запретки"
    assert all(len(h.split()) <= 5 for h in hooks)
    assert V.fallback_hooks("", 2) == list(V.FALLBACK_HOOKS[:2])


def test_text_layout_fits_the_frame():
    lay = V.text_layout("Вот это поворот", "9:16", 1080, 1920)
    assert lay["lines"] == ["ВОТ ЭТО", "ПОВОРОТ"]
    assert 100 <= lay["size"] <= 130
    long = V.text_layout("Невероятнейшее превращение", "9:16", 1080, 1920)
    assert max(len(line) for line in long["lines"]) * 0.76 * long["size"] <= 1080 * 0.86 + 1
    wide = V.text_layout("Вот это поворот", "16:9", 1280, 720)
    assert wide["lines"] == ["ВОТ ЭТО ПОВОРОТ"] and wide["size"] <= 720 * 0.16


def test_text_goes_where_faces_are_not():
    face_top = [[0.3, 0.12, 0.7, 0.4]]
    face_bottom = [[0.3, 0.55, 0.7, 0.85]]
    assert V.band_y(face_top, "9:16", 0.15) > 0.6
    assert V.band_y(face_bottom, "9:16", 0.15) < 0.35
    assert V.band_y([], "9:16", 0.15) == pytest.approx(0.24)
    # В безопасной зоне 9:16 (0.10–0.80) целиком.
    y = V.band_y(face_top, "9:16", 0.3)
    assert y + 0.15 <= 0.80 + 1e-6
    assert V.band_y([[0.1, 0.1, 0.5, 0.45]], "16:9", 0.16) > 0.6


def test_moments_are_strong_and_spread():
    cands = [{"t": t, "score": s, "faces": []} for t, s in [(1.0, 0.9), (1.5, 0.88), (2.0, 0.85), (6.0, 0.5),
                                                              (9.0, 0.4)]]
    picks = V.pick_moments(cands, 3, 10.0)
    times = sorted(p["t"] for p in picks)
    assert times[0] == 1.0 and 6.0 in times and len(picks) == 3
    assert V.sample_times(10.0, 5) == [0.6, 2.8, 5.0, 7.2, 9.4]
    assert V.sample_times(1.0) == [0.5]


def test_brand_colors_and_fonts():
    plate = V.style_spec("plate", {"colors": {"accent": "#ffd600"}})
    assert plate["box"] == "#ffd600" and plate["color"] == "#0b0d14"
    assert V.style_spec("plate")["color"] == "#ffffff"
    assert V.style_spec("accent", {"colors": {"accent": "#22c55e"}})["color"] == "#22c55e"
    assert V.default_styles({"colors": {}})[0] == "accent" and V.default_styles(None)[0] == "yellow"
    assert V.font_path("Golos Text").name == "GolosText-Black.ttf"
    assert V.font_path("Нет такого").name == "Montserrat-Black.ttf"


def test_overlay_args_have_no_absolute_paths_in_the_filter():
    lay = V.text_layout("Вот это поворот", "9:16", 1080, 1920)
    args = V.build_overlay_args("base.png", Path("out.jpg"), width=1080, height=1920, layout=lay, y_center=0.7,
                                spec=V.style_spec("plate"), textfiles=["a.txt", "b.txt"])
    vf = args[args.index("-vf") + 1]
    assert vf.count("drawtext=") == 2 and "textfile=a.txt" in vf and "expansion=none" in vf
    assert "box=1" in vf and "fontfile=font.ttf" in vf and ":\\" not in vf


# --------------------------------------------------------------------------- #
# На настоящем рендере
# --------------------------------------------------------------------------- #


def _rendered(tmp_path, monkeypatch, *, width=180, height=320):
    client, store = _client(tmp_path, monkeypatch)
    video = tmp_path / "cv-src.mp4"
    _make_test_video(video)
    with video.open("rb") as fileobj:
        source = client.post("/api/sources", files={"file": ("cv-src.mp4", fileobj, "video/mp4")}).raise_for_status().json()
    analysis = store.create_ai_analysis(source["id"], "mock", status="succeeded")
    seg = store.create_ai_segment(analysis["id"], {"start_sec": 0, "end_sec": 6, "title": "Момент"})
    plan = store.create_clip_plan(source["id"], analysis["id"], "Стример спалился на стриме", segment_ids=[seg["id"]])
    preset = client.post(
        "/api/ffmpeg-presets",
        json={"label": "cv", "output_width": width, "output_height": height, "fps": 25, "scale_mode": "cover",
              "extra": {"crf": 30}},
    ).raise_for_status().json()
    spec = client.get(f"/api/clip-plans/{plan['id']}/spec").json()
    spec["render"]["preset_id"] = preset["id"]
    client.put(f"/api/clip-plans/{plan['id']}/spec", json=spec).raise_for_status()
    clip = client.post(f"/api/clip-plans/{plan['id']}/render", json={}).raise_for_status().json()
    assert clip["status"] == "succeeded", clip.get("error")
    return client, store, source, plan, clip


def _no_compositor(monkeypatch):
    import app.motion_render as MR

    def missing(*_a, **_k):
        raise MR.CompositorMissing("нет компоновщика в тестах")

    monkeypatch.setattr(MR, "render_frame_png", missing)


def test_generate_choose_and_regenerate_covers(tmp_path, monkeypatch):
    _require_ffmpeg()
    _no_compositor(monkeypatch)
    client, store, _source, plan, clip = _rendered(tmp_path, monkeypatch)
    resp = client.post(f"/api/clips/{clip['id']}/covers",
                       json={"count": 3, "texts": ["Вот это поворот 😱 #shorts", "Он сделал это", "Жёстко"]})
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["format"] == "9:16" and out["size"] == [1080, 1920] and out["texts_source"] == "texts"
    covers = out["covers"]
    assert len(covers) == 3 and {c["text"] for c in covers} == {"ВОТ ЭТО ПОВОРОТ", "ОН СДЕЛАЛ ЭТО", "ЖЁСТКО"}
    assert [c["style"] for c in covers] == ["yellow", "plate", "white"]
    assert all(c["renderer"] == "render" and "path" not in c for c in covers)
    times = [c["t"] for c in covers]
    assert len(set(times)) == 3
    img = client.get(covers[0]["url"])
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    path = Path(V.get_cover(store, covers[0]["id"])["path"])
    assert image_size(path) == (1080, 1920)
    # Чужой клип — 404.
    assert client.get(f"/media/clips/{clip['id'] + 99}/covers/{covers[0]['id']}").status_code == 404

    chosen = client.post(f"/api/clips/{clip['id']}/covers/{covers[1]['id']}/choose", json={}).json()
    assert chosen["ok"] and chosen["render_cover"]["mode"] == "image" and chosen["render_cover"]["burn"] is True
    assert store.get_clip_cover_path(clip["id"]) == str(V.get_cover(store, covers[1]["id"])["path"])
    assert client.get(f"/media/clips/{clip['id']}/cover").status_code == 200
    rs = store.get_clip_plan(plan["id"])["render_settings"]
    assert rs["cover"]["mode"] == "image" and rs["cover"]["image"].endswith(".jpg")
    assert (V.settings.data_dir / "covers" / rs["cover"]["image"]).is_file()

    # Новые варианты заменяют прежние невыбранные; выбранный остаётся.
    again = client.post(f"/api/clips/{clip['id']}/covers", json={"count": 2, "texts": ["Раз", "Два"]}).json()
    ids = {c["id"] for c in again["covers"]}
    assert covers[1]["id"] in ids and covers[0]["id"] not in ids and len(ids) == 3
    assert not path.exists()
    listed = client.get(f"/api/clips/{clip['id']}/covers").json()["covers"]
    assert [c["chosen"] for c in listed].count(True) == 1

    # Без текстов и без ИИ — из заголовка клипа.
    monkeypatch.setattr(V.settings, "gemini_api_key", "")
    plain = client.post(f"/api/clips/{clip['id']}/covers", json={"count": 2}).json()
    assert plain["texts_source"] == "title"
    assert {c["text"] for c in plain["covers"] if c["id"] in plain["new"]} == {"СТРИМЕР СПАЛИЛСЯ НА СТРИМЕ",
                                                                               "СТРИМЕР СПАЛИЛСЯ НА"}
    bad = client.post(f"/api/clips/{clip['id']}/covers", json={"texts": ["😱"]})
    assert bad.status_code == 400


def test_horizontal_clip_gets_youtube_thumbnail_and_publish_uses_it(tmp_path, monkeypatch):
    _require_ffmpeg()
    _no_compositor(monkeypatch)
    client, store, _source, _plan, clip = _rendered(tmp_path, monkeypatch, width=320, height=180)
    out = client.post(f"/api/clips/{clip['id']}/covers", json={"count": 1, "texts": ["Как это вышло"]}).json()
    assert out["format"] == "16:9" and out["size"] == [1280, 720]
    cover = out["covers"][0]
    client.post(f"/api/clips/{clip['id']}/covers/{cover['id']}/choose", json={"apply_to_render": False}).raise_for_status()
    from app.publish_format import landscape_cover

    thumb = landscape_cover(store, store.get_clip(clip["id"]))
    assert thumb and image_size(thumb) == (1280, 720)
    # Клип удалён — файлы вариантов тоже.
    client.delete(f"/api/clips/{clip['id']}").raise_for_status()
    assert not Path(thumb).exists()
    assert store.db.query_one("SELECT COUNT(*) AS n FROM clip_covers")["n"] == 0


def test_compositor_base_keeps_only_picture_layers(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, store, _source, plan, clip = _rendered(tmp_path, monkeypatch)
    import app.motion_render as MR

    seen = {}
    png = tmp_path / "solid.png"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=0x336699:s=180x320", "-frames:v", "1",
                    str(png)], check=True)

    def fake_frame(_store, plan_id, t, layers=None, camera=None, **_kw):
        seen.update(plan_id=plan_id, t=t, types=[layer["type"] for layer in layers], camera=camera)
        return png.read_bytes()

    monkeypatch.setattr(MR, "render_frame_png", fake_frame)
    out = V.generate_covers(store, clip["id"], count=1, texts=["Проверка"])
    assert out["covers"][0]["renderer"] == "compositor"
    assert seen["plan_id"] == plan["id"] and seen["types"] == [] and seen["camera"] is None


def test_mcp_tools_and_autopilot_card(tmp_path, monkeypatch):
    _require_ffmpeg()
    _no_compositor(monkeypatch)
    client, store, source, plan, clip = _rendered(tmp_path, monkeypatch)
    ctx = ToolContext(store=store, clip_plan_id=plan["id"])
    result, ok = run_tool(ctx, "generate_covers", {"count": 2, "texts": ["Первый вариант", "Второй вариант"]})
    assert ok and isinstance(result, ImageResult) and result.mime_type == "image/jpeg"
    ids = [c["id"] for c in result.info["covers"]]
    assert len(ids) == 2 and result.info["render_id"] == clip["id"]
    listed, ok = run_tool(ctx, "list_covers", {"sheet": False})
    assert ok and [c["id"] for c in listed["covers"]] == ids
    bad, ok = run_tool(ToolContext(store=store, clip_plan_id=plan["id"] + 1000), "choose_cover", {"cover_id": ids[0]})
    assert not ok
    chosen, ok = run_tool(ctx, "choose_cover", {"cover_id": ids[1], "burn": False})
    assert ok and chosen["render_cover"] is None

    # Карточка одобрения: выбранная обложка и хэштеги метаданных клипа.
    P.create_project(store, "Канал", kind="stream_channel", source_ids=[source["id"]])
    store.set_clip_publish_meta(clip["id"], {"title": "T", "description": "D", "hashtags": ["#стрим", "#мем"]})
    card = A.submit_clip(store, plan["id"])
    assert card["cover_url"] == f"/media/clips/{clip['id']}/cover"
    assert card["tags"] == ["стрим", "мем"]
    raw = store.db.query_one("SELECT cover_path FROM approvals WHERE id = ?", (card["id"],))
    assert raw["cover_path"] == store.get_clip_cover_path(clip["id"])
