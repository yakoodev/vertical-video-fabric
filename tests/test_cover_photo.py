"""Фоторедактор документа обложки: оценка кадров, полосы, интерфейс игры, чистка, «как в ролике», файлы проекта,
API и инструмент make_thumbnail (app/cover_photo.py, app/cover_movie.py, app/cover_doc_fx.py, app/cover_tools.py)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from app import cover_doc_fx as FX
from app import cover_docs as D
from app import cover_movie as M
from app import cover_photo as PH
from app.agent_tools import ImageResult, ToolContext, get_tool, run_tool
from app.settings import settings
from test_api_pipeline import _client
from test_cover_docs import NODE_READY
from test_cover_variants import _no_compositor, _rendered

FONTS = Path(settings.root_dir) / "vendor" / "fonts"
HUD_TEXT = (28, 24, 148, 48)  # x0, y0, x1, y1 надписи «HUD 123» в кадре без полос (drawtext x=30, y=26 + поле 40 − 42)


def _ffmpeg() -> None:
    if not shutil.which("ffmpeg"):
        pytest.skip("нет ffmpeg")


def _hud_video(path: Path) -> Path:
    """Движущийся testsrc (кадр едет вбок) + неподвижная надпись-«интерфейс» + чёрные полосы сверху и снизу."""
    _ffmpeg()
    vf = ("crop=640:360:'mod(t*157,320)':0,"
          "drawtext=fontfile=Montserrat-Bold.ttf:text='HUD 123':x=30:y=26:fontsize=30:fontcolor=white:"
          "box=1:boxcolor=black@0.6:boxborderw=6,"
          "pad=640:440:0:40:black")
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc=size=960x360:rate=10:duration=10", "-vf", vf, "-pix_fmt", "yuv420p", str(path)],
                   cwd=FONTS, check=True, timeout=120)
    return path


def _feed_doc(store, video: Path, project_id: int | None = None) -> tuple[int, int, int]:
    work = store.db.execute("INSERT INTO feed_works (title, project_id, kind) VALUES ('Мувик', ?, 'movie')", (project_id,))
    version = store.db.execute("INSERT INTO feed_versions (work_id, n, status) VALUES (?, 1, 'review')", (work.lastrowid,))
    variant = store.db.execute(
        "INSERT INTO feed_variants (version_id, label, file_path, width, height, duration_sec, preview_status) "
        "VALUES (?, '16:9', ?, 640, 440, 10, 'failed')", (version.lastrowid, str(video)))
    return int(work.lastrowid), int(version.lastrowid), int(variant.lastrowid)


# --------------------------------------------------------------------------- #
# Чистые части
# --------------------------------------------------------------------------- #


def _texture(seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    img = (rng.integers(0, 255, (180, 320, 3))).astype(np.uint8)
    img[60:120, 100:220] = (40, 120, 230)  # цветное пятно
    return img


def test_frame_scoring_prefers_sharp_and_punishes_blurred_transition():
    import cv2

    sharp = _texture()
    blurred = cv2.GaussianBlur(sharp, (0, 0), 6)
    dark = np.full_like(sharp, 8)
    rows = {r["t"]: r for r in PH.score_frames([(1.0, sharp), (2.0, blurred), (3.0, dark), (4.0, _texture(2))])}
    assert rows[1.0]["score"] > rows[2.0]["score"] > rows[3.0]["score"] - 1  # резкий лучше размытого
    assert rows[2.0]["sharp_rel"] < 0.45 and rows[1.0]["sharp_rel"] >= 0.45  # переход — штраф
    assert rows[3.0]["score"] < rows[1.0]["score"]


def test_bars_found_only_when_every_frame_is_dark_there():
    frames = []
    for seed in range(4):
        img = _texture(seed)
        img[:20] = 0
        img[-14:] = 3
        frames.append(img)
    assert PH.bars_from_frames(frames) == (0, 20, 320, 180 - 34)
    frames[2][:20] = 200  # в одном кадре там светло — не полоса
    assert PH.bars_from_frames(frames) == (0, 0, 320, 180 - 14)
    assert PH.bars_from_frames([np.zeros((90, 160, 3), np.uint8)]) is None  # тёмная сцена — не полосы


def test_filters_sanitized_and_looks():
    assert FX.sanitize_filters({}) is None
    clean = FX.sanitize_filters({"contrast": 9, "blur": -3, "accent": {"ranges": [{"from": 400, "to": -5, "minSat": 2}] * 9},
                                 "shade": [{"side": "evil", "size": 7}], "shadeColor": "red", "evil": 1})
    assert clean["contrast"] == 1 and clean["blur"] == 0 and len(clean["accent"]["ranges"]) == FX.MAX_RANGES
    assert clean["accent"]["ranges"][0] == {"from": 360, "to": 0, "minSat": 1}
    assert clean["shade"] == [{"side": "left", "size": 1, "strength": 0.7, "curve": 1.4, "hold": 0}]
    assert clean["shadeColor"] == "#000000" and "evil" not in clean
    movie = FX.look_filters("movie", "right")
    assert movie["accent"]["ranges"] == FX.FIRE_RANGES and movie["shade"][0]["side"] == "right"
    assert FX.look_filters("none") is None
    word = FX.text_style("movie-word", 95)
    assert word["edge"]["offset"] == 3.5 and word["underline"]["gap"] == 9 and word["weight"] == 800
    layer = D.sanitize_doc({"layers": [{"type": "text", "edge": {"offset": 999}, "underline": {"width": 0}},
                                       {"type": "image", "filters": {"vignette": 0.5}}]}, 1280, 720, set())["layers"]
    assert layer[0]["edge"]["offset"] == 200 and layer[0]["underline"]["width"] == 1
    assert layer[1]["filters"]["vignette"] == 0.5


def test_movie_layout_left_and_right():
    frame = {"name": "a" * 32 + ".jpg", "width": 1920, "height": 888}
    logo = {"name": "b" * 32 + ".png", "width": 600, "height": 200}
    left = M.movie_layers(width=1280, height=720, frame=frame, big_word="BURN", top_line="APEX LEGENDS",
                          subline="YOUR LOCAL POLITICIAN", logo=logo, zoom=1.233, center=(0.4375, 0.444))
    names = [l["name"] for l in left]
    assert names == ["Кадр", "Строка сверху", "Главное слово", "Строка снизу", "Логотип"]
    word = left[2]
    assert word["size"] == 190 and abs(word["y"] + 0.86 * 190 - 420) < 0.5 and word["align"] == "left"
    assert left[0]["crop"]["h"] == pytest.approx(0.811, abs=0.01) and left[0]["filters"]["accent"]
    assert left[4]["x"] == 64 and left[4]["w"] == 230
    right = M.movie_layers(width=1280, height=720, frame=frame, big_word="ОЧЕНЬДЛИННОЕСЛОВО", side="right", logo=logo)
    assert right[1]["align"] == "right" and right[1]["size"] < 190
    assert right[-1]["x"] == 1280 - 64 - 230
    clean = M.movie_layers(width=1280, height=720, frame=frame, big_word="", style="clean")
    assert [l["type"] for l in clean] == ["image"]


# --------------------------------------------------------------------------- #
# Ролик: интерфейс, чистка, кандидаты
# --------------------------------------------------------------------------- #


def test_hud_mask_covers_static_overlay(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    video = _hud_video(tmp_path / "hud.mp4")
    assert PH.video_bars(video) == [2, 42, 636, 356]
    _w, _v, variant = _feed_doc(store, video)
    doc = D.create_doc(store, None, "Фото", "16:9", feed_variant_id=variant)
    made = PH.add_frame(store, doc["id"], 4.3, "render")
    assert made["bars"] and (made["width"], made["height"]) == (636, 356)
    boxes = PH.hud_boxes(store, doc["id"], made["name"])
    assert boxes
    x0, y0, x1, y1 = HUD_TEXT
    hit = [b for b in boxes if b["x"] * 636 <= x0 and (b["x"] + b["w"]) * 636 >= x1
           and b["y"] * 356 <= y0 and (b["y"] + b["h"]) * 356 >= y1]
    assert hit, boxes
    assert sum(b["w"] * b["h"] for b in boxes) < 0.35  # движущийся мир не закрашиваем
    # API: найти и закрасить — новый файл, исходный на месте.
    resp = client.post(f"/api/cover-docs/{doc['id']}/hud", json={"src": made["name"]})
    assert resp.status_code == 200 and resp.json()["boxes"]
    cleaned = client.post(f"/api/cover-docs/{doc['id']}/clean", json={"src": made["name"], "auto": True}).json()
    assert cleaned["name"] != made["name"] and cleaned["parent"] == made["name"]
    assert {made["name"], cleaned["name"]} <= D.doc_assets(doc["id"])
    import cv2

    before = cv2.imread(str(D.asset_path(doc["id"], made["name"]))).astype(int)
    after = cv2.imread(str(D.asset_path(doc["id"], cleaned["name"]))).astype(int)
    text = (slice(y0 + 4, y1 - 4), slice(x0 + 4, x1 - 4))
    assert np.abs(before[text] - after[text]).mean() > 10  # надпись закрашена
    # Картинка без ролика — понятный отказ.
    upload = D.store_upload(doc["id"], cv2.imencode(".png", np.zeros((20, 20, 3), np.uint8))[1].tobytes())
    bad = client.post(f"/api/cover-docs/{doc['id']}/hud", json={"src": upload["name"]})
    assert bad.status_code == 400 and "руками" in bad.json()["detail"]


def test_clean_frame_rects_only_touch_their_area(tmp_path, monkeypatch):
    import cv2

    _client(tmp_path, monkeypatch)
    from app.main import store

    doc = D.create_doc(store, None, "Фото", "1:1")
    img = cv2.GaussianBlur(_texture(), (0, 0), 4)  # гладкая картинка: JPEG не шумит
    img[10:40, 10:120] = 255
    made = D.store_upload(doc["id"], cv2.imencode(".png", img)[1].tobytes())
    out = PH.clean_frame(store, doc["id"], made["name"], [{"x": 10 / 320, "y": 10 / 180, "w": 110 / 320, "h": 30 / 180}])
    before = cv2.imread(str(D.asset_path(doc["id"], made["name"]))).astype(int)  # загрузка уже в JPEG
    after = cv2.imread(str(D.asset_path(doc["id"], out["name"]))).astype(int)
    assert after[15:35, 15:115].mean() < 230  # белая плашка закрашена
    assert np.abs(after[100:170, 200:310] - before[100:170, 200:310]).mean() < 6  # остальное — как было
    with pytest.raises(ValueError):
        PH.clean_frame(store, doc["id"], made["name"], [])
    with pytest.raises(ValueError):
        PH.clean_frame(store, doc["id"], made["name"], [{"x": 0, "y": 0, "w": 1, "h": 1}])


def test_movie_api_from_layer_picture(tmp_path, monkeypatch):
    import cv2

    client, store = _client(tmp_path, monkeypatch)
    doc_id = client.post("/api/cover-docs", json={"format": "16:9"}).json()["id"]
    pic = D.store_upload(doc_id, cv2.imencode(".png", cv2.GaussianBlur(_texture(), (0, 0), 3))[1].tobytes())
    assert client.post(f"/api/cover-docs/{doc_id}/movie", json={"frame": pic["name"]}).status_code == 400  # нет слова
    out = client.post(f"/api/cover-docs/{doc_id}/movie", json={
        "frame": pic["name"], "big_word": "BURN", "top_line": "APEX", "style": "color", "side": "right",
        "hud": "off", "logo": False}).json()
    assert [l["name"] for l in out["layers"]] == ["Кадр", "Строка сверху", "Главное слово"]
    assert out["frame"] == pic["name"] and out["layers"][0]["filters"]["accent"] is None
    state = client.get(f"/api/cover-docs/{doc_id}/doc").json()
    assert state["movie"] == {"big_word": "BURN", "top_line": "APEX", "style": "color", "side": "right"}


def test_candidates_api_and_cache(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    video = _hud_video(tmp_path / "hud.mp4")
    _w, version, _v = _feed_doc(store, video)
    doc_id = client.post("/api/cover-docs", json={"feed_version_id": version, "format": "16:9"}).json()["id"]
    state = client.get(f"/api/cover-docs/{doc_id}/doc").json()
    assert state["clip"]["duration"] == 10 and state["clip"]["plan"] is False and state["feed_variant_id"]
    items = client.get(f"/api/cover-docs/{doc_id}/candidates").json()["items"]
    assert 1 <= len(items) <= PH.CAND_COUNT and items == sorted(items, key=lambda c: -c["score"])
    thumb = client.get(f"/api/cover-docs/{doc_id}/candidates/{items[0]['file']}")
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/jpeg"
    assert client.get(f"/api/cover-docs/{doc_id}/candidates/..%2Fmeta.json").status_code == 404
    again = client.get(f"/api/cover-docs/{doc_id}/candidates").json()["items"]
    assert again == items  # из кэша
    # Тот же ролик и формат — тот же документ.
    assert client.post("/api/cover-docs", json={"feed_version_id": version, "format": "16:9"}).json() == {
        "id": doc_id, "created": False}


def test_project_photo_custom_size_and_save_to_files(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    from app import projects_core as P

    project = P.create_project(store, "Фото-проект")
    made = client.post("/api/cover-docs", json={"project_id": project["id"], "format": "custom", "width": 99999,
                                                "height": 300}).json()
    doc = D.get_doc(store, made["id"])
    assert doc["project_id"] == project["id"] and doc["format"] == "custom"
    assert (doc["doc"]["width"], doc["doc"]["height"]) == (D.CUSTOM_MAX, 300)
    listed = client.get(f"/api/cover-docs?project_id={project['id']}").json()["docs"]
    assert [d["id"] for d in listed] == [made["id"]] and listed[0]["width"] == D.CUSTOM_MAX
    assert client.post("/api/cover-docs", json={"project_id": 999}).status_code == 400
    if not NODE_READY:
        pytest.skip("нет собранного компоновщика")
    small = client.post("/api/cover-docs", json={"project_id": project["id"], "format": "16:9"}).json()["id"]
    D.save_doc(store, small, {"bg": "#203040", "layers": [{"type": "shape", "fill": "#ff7a1e", "x": 0, "y": 0, "w": 640, "h": 720}]})
    out = client.post(f"/api/cover-docs/{small}/save-to-project", json={}).json()
    asset = store.get_montage_asset(out["asset"]["id"])
    assert asset["project_id"] == project["id"] and asset["kind"] == "image" and asset["label"].startswith("Превью")
    assert "не картинка для вставок" in asset["description"] and f"cover_doc:{small}" in asset["tags"]
    assert out["bytes"] < PH.YOUTUBE_MAX_BYTES and (asset["width"], asset["height"]) == (1280, 720)


# --------------------------------------------------------------------------- #
# Отрисовка фильтров (тот же код, что холст) и инструмент
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(not NODE_READY, reason="нет собранного компоновщика")
def test_filters_render_bw_with_accent_and_shade(tmp_path, monkeypatch):
    import cv2

    _client(tmp_path, monkeypatch)
    from app.main import store

    doc = D.create_doc(store, None, "Фото", "16:9")
    img = np.zeros((360, 640, 3), np.uint8)
    img[:, :320] = (200, 80, 40)  # синий (BGR) — уйдёт в серый
    img[:, 320:] = (30, 120, 255)  # оранжевый — останется
    made = D.store_upload(doc["id"], cv2.imencode(".png", img)[1].tobytes())
    filters = FX.look_filters("movie")
    filters["shade"] = [{"side": "right", "size": 0.1, "strength": 1, "curve": 1, "hold": 0.5}]
    filters["vignette"] = 0
    D.save_doc(store, doc["id"], {"layers": [{"type": "image", "src": made["name"], "x": 0, "y": 0, "w": 1280, "h": 720,
                                              "fit": "cover", "filters": filters}]})
    out = cv2.imread(str(D.render_doc(store, doc["id"])))
    assert out.shape == (720, 1280, 3)
    blue = out[300:400, 200:400].reshape(-1, 3).astype(int)
    orange = out[300:400, 800:1000].reshape(-1, 3).astype(int)
    assert (blue.max(axis=1) - blue.min(axis=1)).mean() < 20  # ЧБ
    assert (orange[:, 2] - orange[:, 0]).mean() > 120  # огонь остался цветным
    assert out[300:400, 1250:1278].mean() < 15  # затемнение справа


@pytest.mark.skipif(not NODE_READY, reason="нет собранного компоновщика")
def test_make_thumbnail_from_feed_version_and_redo(tmp_path, monkeypatch):
    _client(tmp_path, monkeypatch)
    from app import projects_core as P
    from app.main import store

    project = P.create_project(store, "Мувики")
    video = _hud_video(tmp_path / "hud.mp4")
    _w, version, _v = _feed_doc(store, video, project["id"])
    result, ok = run_tool(ToolContext(store=store), "make_thumbnail", {
        "feed_version_id": version, "big_word": "BURN", "top_line": "APEX LEGENDS", "subline": "YOUR LOCAL POLITICIAN",
        "frame_t": 4.3}, validate=True)
    assert ok, result
    assert isinstance(result, ImageResult) and result.mime_type == "image/jpeg"
    import cv2

    pic = cv2.imdecode(np.frombuffer(result.data, np.uint8), cv2.IMREAD_COLOR)
    assert pic.shape == (720, 1280, 3) and len(result.data) < PH.YOUTUBE_MAX_BYTES
    info = result.info
    assert info["hud_boxes"] >= 1 and info["frame_t"] == pytest.approx(4.3, abs=0.01) and info["asset_id"]
    assert any("логотип" in w for w in info["warnings"])  # у проекта нет бренда — без логотипа, но со словами
    doc = D.get_doc(store, info["doc_id"])
    assert doc["project_id"] == project["id"] and doc["format"] == "16:9"
    words = [l["text"] for l in doc["doc"]["layers"] if l["type"] == "text"]
    assert words == ["APEX LEGENDS", "BURN", "YOUR LOCAL POLITICIAN"]
    frame = doc["doc"]["layers"][0]["src"]
    # Переделка: другое слово и сторона — тот же кадр, без новой чистки.
    redo, ok = run_tool(ToolContext(store=store), "make_thumbnail", {"doc_id": info["doc_id"], "big_word": "ОГОНЬ",
                                                                     "side": "right", "save": False}, validate=True)
    assert ok, redo
    doc = D.get_doc(store, info["doc_id"])
    assert doc["doc"]["layers"][0]["src"] == frame and "asset_id" not in redo.info
    assert [l["text"] for l in doc["doc"]["layers"] if l["type"] == "text"][1] == "ОГОНЬ"
    assert all(l["align"] == "right" for l in doc["doc"]["layers"] if l["type"] == "text")


def test_make_thumbnail_needs_one_source_and_word(tmp_path, monkeypatch):
    _client(tmp_path, monkeypatch)
    from app.main import store

    result, ok = run_tool(ToolContext(store=store), "make_thumbnail", {"big_word": "X"}, validate=True)
    assert not ok and "ровно один источник" in result["error"]
    result, ok = run_tool(ToolContext(store=store), "make_thumbnail", {"clip_id": 1, "feed_version_id": 2,
                                                                       "big_word": "X"}, validate=True)
    assert not ok and "ровно один" in result["error"]


@pytest.mark.skipif(not NODE_READY, reason="нет собранного компоновщика")
def test_make_thumbnail_from_vertical_clip(tmp_path, monkeypatch):
    client, store, _source, plan, _clip = _rendered(tmp_path, monkeypatch)
    _no_compositor(monkeypatch)
    result, ok = run_tool(ToolContext(store=store), "make_thumbnail", {
        "clip_id": plan["id"], "big_word": "ПОВОРОТ", "style": "color", "hud": "off", "save": False}, validate=True)
    assert ok, result
    doc = D.get_doc(store, result.info["doc_id"])
    assert (doc["doc"]["width"], doc["doc"]["height"]) == (1280, 720) and doc["clip_id"]
    # С экрана клипа: свой документ 16:9, не вертикальный редактор превью.
    made = client.post("/api/cover-docs", json={"clip_id": doc["clip_id"], "format": "16:9"}).json()
    assert made == {"id": doc["id"], "created": False}
    vertical = client.post("/api/cover-docs", json={"clip_id": doc["clip_id"], "format": "9:16"}).json()
    assert vertical["created"] is True


def test_make_thumbnail_registered_deliberately():
    from app import chat_tools, modules

    tool = get_tool("make_thumbnail")
    assert tool.scope == "global" and tool.mutating and "clip_id" in tool.parameters["properties"]
    assert "feed_version_id" in tool.parameters["properties"] and "big_word" in tool.parameters["properties"]
    assert modules.module_of_tool("make_thumbnail") == "covers"
    assert "make_thumbnail" in chat_tools.group_tools("montage")
    assert "слоганы не придумывай" in tool.description
