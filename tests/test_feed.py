"""«Лента» (app/feed.py): работы, версии, варианты, вердикт, отметки «запощено», пути файлов, превью, API, инструменты."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app import feed as FEED
from app import feed_files as F
from app import feed_preview
from app.settings import settings
from test_api_pipeline import _client, _require_ffmpeg


def _video(path: Path, size: str = "320x180", seconds: float = 1.5, crf: int = 30, audio: bool = True) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=30"]
    if audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=500:sample_rate=44100"]
    cmd += ["-t", str(seconds), "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", "-crf", str(crf)]
    if audio:
        cmd += ["-c:a", "aac", "-shortest"]
    subprocess.run([*cmd, str(path)], capture_output=True, check=True)
    return path


@pytest.fixture
def env(tmp_path, monkeypatch):
    _require_ffmpeg()
    monkeypatch.delenv("FEED_DIR", raising=False)
    monkeypatch.delenv("FEED_HOST_MOUNTS", raising=False)
    monkeypatch.delenv("FEED_IMPORT_DIRS", raising=False)
    client, store = _client(tmp_path, monkeypatch)
    return client, store


def _server_file(name: str = "deliv/a_9x16.mp4", **kw) -> Path:
    return _video(settings.data_dir / "runtime" / name, **kw)


# --------------------------------------------------------------------------- #
# Хранилище
# --------------------------------------------------------------------------- #


def test_work_versions_status_and_filters(env):
    _client_, store = env
    a = _server_file("deliv/a.mp4")
    work = FEED.create_work(store, "BURN", kind="movie")
    assert work["latest"] is None and work["versions"] == []
    v1 = FEED.add_version(store, work["id"], note="первая", variants=[{"path": str(a), "label": "16:9"}], label="v4")
    v2 = FEED.add_version(store, work["id"], note="музыка тише", variants=[{"path": str(a)}])
    assert (v1["n"], v2["n"]) == (1, 2) and v1["name"] == "v4" and v2["name"] == "v2"
    assert v2["status"] == "review" and v2["variants"][0]["label"] == "16:9"  # подпись по кадру
    got = FEED.get_work(store, work["id"])
    assert [v["n"] for v in got["versions"]] == [2, 1]  # новые сверху
    assert got["latest"]["status"] == "review"

    other = FEED.create_work(store, "AMV", kind="amv")
    listed = FEED.list_feed(store)
    assert listed["items"][0]["id"] == other["id"]  # свежее событие сверху
    assert listed["counts"]["review"] == 1 and listed["counts"]["ready_unposted"] == 0
    assert [w["id"] for w in FEED.list_feed(store, filter="review")["items"]] == [work["id"]]
    assert [w["id"] for w in FEED.list_feed(store, kind="amv")["items"]] == [other["id"]]
    assert "versions" not in listed["items"][0]

    FEED.set_status(store, v2["id"], "accepted", "огонь, выкладывай")
    ready = FEED.list_feed(store, filter="ready_unposted")
    assert [w["id"] for w in ready["items"]] == [work["id"]]
    assert ready["items"][0]["id"] == work["id"] and FEED.list_feed(store)["items"][0]["id"] == work["id"]

    variant_id = v2["variants"][0]["id"]
    posting = FEED.mark_posted(store, variant_id, "tiktok", "https://www.tiktok.com/@me/video/1", "2026-11-16")
    assert posting["origin"] == "feed" and posting["removable"] and posting["posted_by"] == "owner"
    got = FEED.get_work(store, work["id"])
    assert got["posted_platforms"] == ["tiktok"] and not got["ready_unposted"]
    assert FEED.list_feed(store, filter="posted")["total"] == 1
    # Отметка — ручная публикация (сбор чисел тот же), без проекта и клипа она привязана к ленте.
    row = store.db.query_one("SELECT * FROM project_publications WHERE id = ?", (posting["id"],))
    assert row["feed_variant_id"] == variant_id and row["platform"] == "tiktok" and row["project_id"] is None
    FEED.unmark_posted(store, posting["id"])
    assert FEED.get_work(store, work["id"])["ready_unposted"]
    with pytest.raises(KeyError):
        FEED.unmark_posted(store, posting["id"])


def test_agent_needs_owner_words_to_accept(env):
    _c, store = env
    work = FEED.create_work(store, "Мем", kind="meme")
    version = FEED.add_version(store, work["id"], variants=[{"path": str(_server_file())}])
    with pytest.raises(ValueError, match="владельца"):
        FEED.set_status(store, version["id"], "accepted", by="agent")
    done = FEED.set_status(store, version["id"], "accepted", "«ок, норм» — владелец в TG", by="agent")
    assert done["status"] == "accepted" and done["status_by"] == "agent"
    assert FEED.set_status(store, version["id"], verdict="уточнил")["verdict"] == "уточнил"
    with pytest.raises(ValueError):
        FEED.set_status(store, version["id"], "maybe")


def test_bad_variant_refuses_whole_version(env):
    _c, store = env
    work = FEED.create_work(store, "X")
    good = _server_file("deliv/good.mp4")
    (settings.data_dir / "secret.txt").write_text("nope", encoding="utf-8")
    outside = _video(settings.data_dir.parent / "outside.mp4")
    for bad in ({"path": str(settings.data_dir / "secret.txt")}, {"path": str(outside)},
                {"path": str(settings.data_dir / "runtime" / ".." / ".." / "outside.mp4")},
                {"path": str(good), "clip_id": 1}, {}):
        with pytest.raises((ValueError, KeyError)):
            FEED.add_version(store, work["id"], variants=[{"path": str(good)}, bad])
    assert FEED.get_work(store, work["id"])["versions"] == []


def test_host_path_import_is_mapped_limited_and_moved(env, monkeypatch):
    _c, store = env
    monkeypatch.setenv("FEED_HOST_MOUNTS", "D:/vvf-data/runtime={data}/runtime")
    staged = _video(settings.data_dir / "runtime" / "feed_import" / "stay_9x16.mp4", size="180x320")
    assert F.container_path_of_host(r"D:\vvf-data\runtime\feed_import\stay_9x16.mp4") == staged
    assert F.container_path_of_host("/d/vvf-data/runtime/feed_import/stay_9x16.mp4") == staged
    with pytest.raises(F.FeedFileError, match="смонтирован"):
        F.container_path_of_host(r"C:\Users\someone\video.mp4")
    with pytest.raises(F.FeedFileError):
        F.import_path(r"D:\vvf-data\runtime\feed_import\..\..\x.mp4")
    _video(settings.data_dir / "runtime" / "elsewhere.mp4")
    with pytest.raises(F.FeedFileError, match="импорт только"):
        F.import_path(r"D:\vvf-data\runtime\elsewhere.mp4")

    work = FEED.create_work(store, "Stay Together", kind="movie")
    version = FEED.add_version(store, work["id"], variants=[
        {"host_path": r"D:\vvf-data\runtime\feed_import\stay_9x16.mp4", "label": "9:16"}])
    variant = store.db.query_one("SELECT * FROM feed_variants WHERE id = ?", (version["variants"][0]["id"],))
    assert not staged.exists() and Path(variant["file_path"]).exists() and variant["owned"] == 1
    assert F.inside_feed(variant["file_path"])
    assert version["variants"][0]["orientation"] == "vertical"


def test_delete_work_removes_only_feed_files(env):
    _c, store = env
    referenced = _server_file("deliv/keep.mp4")
    work = FEED.create_work(store, "Удаляемая")
    version = FEED.add_version(store, work["id"], variants=[{"path": str(referenced)}])
    tmp = F.tmp_dir() / "up.upload"
    _video(tmp.with_suffix(".mp4")).rename(tmp)
    uploaded = FEED.add_uploaded_variant(store, version["id"], tmp, "мой файл.mp4", "без музыки")
    owned_path = Path(store.db.query_one("SELECT file_path FROM feed_variants WHERE id = ?",
                                         (uploaded["id"],))["file_path"])
    assert uploaded["label"] == "без музыки" and uploaded["source"] == "file" and owned_path.exists()
    FEED.mark_posted(store, uploaded["id"], "youtube")
    out = FEED.delete_work(store, work["id"])
    assert out["deleted"] and referenced.exists() and not owned_path.exists()
    assert store.db.query_one("SELECT COUNT(*) AS n FROM project_publications")["n"] == 0


def test_clip_variant_shares_postings_with_clip_screen(env):
    client, store = env
    src = _video(Path(settings.tmp_dir) / "clip.mp4")
    with src.open("rb") as fh:
        up = client.post("/api/clips/upload", files={"file": ("clip.mp4", fh, "video/mp4")},
                         data={"title": "Клип"}).raise_for_status().json()
    work = FEED.create_work(store, "Промо", kind="promo")
    version = FEED.add_version(store, work["id"], variants=[{"clip_id": up["clip_id"], "label": "9:16"}])
    variant = version["variants"][0]
    assert variant["source"] == "clip" and variant["clip_id"] == up["clip_id"] and variant["file_present"]
    # Из ленты — назад на экран клипа (/clips/<план>).
    assert variant["clip_plan_id"] == up["clip_plan_id"]
    # Выложено с экрана клипа (мимо ленты) — видно в ленте, но снять отсюда нельзя.
    from app.projects_stats import add_manual_publication

    add_manual_publication(store, url="https://youtube.com/shorts/abc", clip_id=up["clip_id"],
                           clip_plan_id=up["clip_plan_id"], platform="youtube")
    posted = FEED.mark_posted(store, variant["id"], "instagram")
    got = FEED.get_variant(store, variant["id"])
    origins = sorted((p["platform"], p["origin"], p["removable"]) for p in got["postings"])
    assert origins == [("instagram", "feed", True), ("youtube", "clip", False)]
    # Отметка ленты по клипу видна и экрану клипа: публикация привязана к его файлу и плану.
    row = store.db.query_one("SELECT clip_id, clip_plan_id FROM project_publications WHERE id = ?", (posted["id"],))
    assert (row["clip_id"], row["clip_plan_id"]) == (up["clip_id"], up["clip_plan_id"])


def test_tool_variant_by_render_or_clip_plan_id(env):
    """clip_id ленты — id рендера, а у остальных инструментов clip_id — клип: агенту можно назвать рендер
    render_clip_id (как в render_status) или клип clip_plan_id — возьмётся его последний готовый рендер."""
    from app.agent_tools import ToolContext, run_tool

    client, store = env
    src = _video(Path(settings.tmp_dir) / "clip2.mp4", size="180x320")
    with src.open("rb") as fh:
        up = client.post("/api/clips/upload", files={"file": ("clip2.mp4", fh, "video/mp4")},
                         data={"title": "Клип"}).raise_for_status().json()
    ctx = ToolContext(store=store)
    work, ok = run_tool(ctx, "feed_create_work", {"title": "Сова 20", "kind": "series"}, validate=True)
    assert ok, work
    by_plan, ok = run_tool(ctx, "feed_add_version", {"work_id": work["work_id"], "note": "v1",
                                                     "variants": [{"clip_plan_id": up["clip_plan_id"]}]}, validate=True)
    assert ok and by_plan["variants"][0]["clip_id"] == up["clip_id"] and by_plan["variants"][0]["label"] == "9:16"
    by_render, ok = run_tool(ctx, "feed_add_version", {"work_id": work["work_id"], "variants": [
        {"label": "TikTok", "render_clip_id": up["clip_id"]}]}, validate=True)
    assert ok and by_render["variants"][0]["clip_id"] == up["clip_id"]
    both, ok = run_tool(ctx, "feed_add_version", {"work_id": work["work_id"], "variants": [
        {"render_clip_id": up["clip_id"], "clip_plan_id": up["clip_plan_id"]}]}, validate=True)
    assert not ok and "один источник" in both["error"]
    none, ok = run_tool(ctx, "feed_add_version", {"work_id": work["work_id"],
                                                  "variants": [{"clip_plan_id": up["clip_plan_id"] + 999}]}, validate=True)
    assert not ok and "нет готового рендера" in none["error"] and "render_clip" in none["error"]


# --------------------------------------------------------------------------- #
# Превью
# --------------------------------------------------------------------------- #


def _probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)["streams"][0]


def test_preview_job_makes_light_copy_and_poster(env):
    _c, store = env
    heavy = _server_file("deliv/heavy_9x16.mp4", size="1080x1920", seconds=1.2, crf=8)
    light = _server_file("deliv/light.mp4", size="320x180", seconds=1.2)
    work = FEED.create_work(store, "Превью")
    version = FEED.add_version(store, work["id"], variants=[{"path": str(heavy)}, {"path": str(light)}])
    assert all(v["preview"]["status"] == "queued" for v in version["variants"])
    assert feed_preview.process_pending(store) == 2
    heavy_v, light_v = FEED.get_version(store, version["id"])["variants"]
    assert heavy_v["preview"]["status"] == "ready" and not heavy_v["preview"]["same_as_file"]
    row = store.db.query_one("SELECT * FROM feed_variants WHERE id = ?", (heavy_v["id"],))
    assert _probe(Path(row["preview_path"])) == {"width": 720, "height": 1280}
    assert Path(row["poster_path"]).stat().st_size > 0 and heavy_v["poster_url"]
    assert light_v["preview"]["same_as_file"]  # лёгкий файл — сам себе превью
    assert FEED.get_work(store, work["id"])["cover_url"] == heavy_v["poster_url"]
    # Перезапуск посреди сборки: running → queued.
    store.db.execute("UPDATE feed_variants SET preview_status = 'running' WHERE id = ?", (light_v["id"],))
    assert feed_preview.recover(store) == 1


def test_preview_failure_is_reported(env):
    _c, store = env
    path = _server_file("deliv/gone.mp4")
    work = FEED.create_work(store, "Пропал")
    version = FEED.add_version(store, work["id"], variants=[{"path": str(path)}])
    path.unlink()
    feed_preview.process_pending(store)
    variant = FEED.get_version(store, version["id"])["variants"][0]
    assert variant["preview"]["status"] == "failed" and "нет" in variant["preview"]["error"]
    assert not variant["file_present"] and variant["download_url"] is None


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #


def test_api_flow_upload_status_postings_and_media(env):
    client, store = env
    work = client.post("/api/feed/works", json={"title": "AMV Chainsaw Man", "kind": "amv"}).raise_for_status().json()
    ref = _server_file("deliv/amv_16x9.mp4")
    version = client.post(f"/api/feed/works/{work['id']}/versions", json={
        "note": "кроп по источнику", "label": "v1", "variants": [{"path": str(ref), "label": "16:9"}],
    }).raise_for_status().json()
    assert version["made_by"] == "owner" and version["variants"][0]["label"] == "16:9"

    body = _video(Path(settings.tmp_dir) / "part1.mp4", size="180x320").read_bytes()
    uploaded = client.put(f"/api/feed/versions/{version['id']}/variants/upload?label=TikTok%20часть%201/2"
                          "&filename=part1.mp4", content=body,
                          headers={"Content-Type": "application/octet-stream"}).raise_for_status().json()
    assert uploaded["label"] == "TikTok часть 1/2" and uploaded["size_bytes"] == len(body)
    assert client.put(f"/api/feed/versions/{version['id']}/variants/upload", content=b"not a video").status_code == 400
    assert client.put("/api/feed/versions/999/variants/upload", content=body).status_code == 404
    assert not list(F.tmp_dir().iterdir())  # плохая загрузка не оставила мусора

    bad = client.post(f"/api/feed/versions/{version['id']}/variants", json={"path": "/etc/passwd"})
    assert bad.status_code == 400

    feed_preview.process_pending(store)
    status = client.post(f"/api/feed/versions/{version['id']}/status",
                         json={"status": "accepted", "verdict": "принято"}).raise_for_status().json()
    assert status["status"] == "accepted" and status["status_by"] == "owner"
    page = client.get("/api/feed", params={"filter": "ready_unposted"}).raise_for_status().json()
    assert page["total"] == 1 and page["items"][0]["kind_label"] == "AMV" and page["items"][0]["cover_url"]

    posting = client.post(f"/api/feed/variants/{uploaded['id']}/postings",
                          json={"platform": "tiktok", "url": "https://www.tiktok.com/@x/video/2"}).raise_for_status().json()
    assert posting["platform"] == "tiktok"
    assert client.post(f"/api/feed/variants/{uploaded['id']}/postings", json={"platform": "myspace"}).status_code == 400
    detail = client.get(f"/api/feed/works/{work['id']}").raise_for_status().json()
    assert detail["posted_platforms"] == ["tiktok"] and detail["versions"][0]["note"] == "кроп по источнику"
    assert client.delete(f"/api/feed/postings/{posting['id']}").raise_for_status().json()["deleted"]

    variant = detail["versions"][0]["variants"][1]
    preview = client.get(variant["preview"]["url"])
    assert preview.status_code == 200 and preview.headers["content-type"] == "video/mp4"
    ranged = client.get(variant["preview"]["url"], headers={"Range": "bytes=0-99"})
    assert ranged.status_code == 206 and len(ranged.content) == 100  # телефон листает видео кусками
    assert client.get(variant["poster_url"]).headers["content-type"] == "image/jpeg"
    download = client.get(variant["download_url"])
    assert download.status_code == 200 and download.content == body
    assert "attachment" in download.headers["content-disposition"]
    assert client.get("/media/feed/variants/999/file").status_code == 404

    assert client.patch(f"/api/feed/works/{work['id']}", json={"kind": "movie"}).json()["kind"] == "movie"
    assert client.delete(f"/api/feed/works/{work['id']}").json()["deleted"]
    assert client.get(f"/api/feed/works/{work['id']}").status_code == 404
    assert ref.exists()


def test_api_marks_token_requests_as_agent(env, monkeypatch):
    client, _store = env
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "api_token", "test-token-feed")
    headers = {"Authorization": "Bearer test-token-feed"}
    work = client.post("/api/feed/works", json={"title": "От агента"}, headers=headers).raise_for_status().json()
    assert work["made_by"] == "agent"
    ref = str(_server_file("deliv/agent.mp4"))
    refused = client.post(f"/api/feed/works/{work['id']}/versions", headers=headers,
                          json={"status": "accepted", "variants": [{"path": ref}]})
    assert refused.status_code == 400 and "владельца" in refused.json()["detail"]
    version = client.post(f"/api/feed/works/{work['id']}/versions", headers=headers,
                          json={"variants": [{"path": ref}]}).raise_for_status().json()
    assert version["made_by"] == "agent" and version["status"] == "review"
    assert client.get("/api/feed").status_code == 401


# --------------------------------------------------------------------------- #
# Инструменты
# --------------------------------------------------------------------------- #


def test_tools_create_add_status_mark_list(env):
    from app.agent_tools import ToolContext, run_tool

    _c, store = env
    ctx = ToolContext(store)
    ref = _server_file("deliv/tool.mp4")
    created, ok = run_tool(ctx, "feed_create_work", {"title": "Сова", "kind": "series"}, validate=True)
    assert ok and created["work_id"]
    added, ok = run_tool(ctx, "feed_add_version", {"work_id": created["work_id"], "note": "хук",
                                                    "variants": [{"path": str(ref), "label": "9:16"}]}, validate=True)
    assert ok and added["n"] == 1 and added["variants"][0]["preview"] == "queued"
    refused, ok = run_tool(ctx, "feed_set_status", {"version_id": added["version_id"], "status": "accepted"},
                           validate=True)
    assert not ok and "владельца" in refused["error"]
    done, ok = run_tool(ctx, "feed_set_status", {"version_id": added["version_id"], "status": "accepted",
                                                  "verdict": "владелец: «да»"}, validate=True)
    assert ok and done["status"] == "accepted"
    variant_id = added["variants"][0]["variant_id"]
    marked, ok = run_tool(ctx, "feed_mark_posted", {"variant_id": variant_id, "platform": "youtube"}, validate=True)
    assert ok and marked["posting"]["posted_by"] == "agent"
    listed, ok = run_tool(ctx, "feed_list", {"filter": "posted"}, validate=True)
    assert ok and listed["total"] == 1 and listed["works"][0]["posted"] == ["youtube"]
    unmarked, ok = run_tool(ctx, "feed_mark_posted", {"variant_id": variant_id, "platform": "youtube", "unmark": True},
                            validate=True)
    assert ok and unmarked["deleted"] == 1
    one, ok = run_tool(ctx, "feed_list", {"work_id": created["work_id"]}, validate=True)
    assert ok and one["ready_unposted"] and one["versions"][0]["verdict"] == "владелец: «да»"
    bad, ok = run_tool(ctx, "feed_add_version", {"work_id": created["work_id"], "variants": [{"path": "/etc/hosts"}]},
                       validate=True)
    assert not ok


def test_feed_tools_are_registered_for_mcp_and_chat():
    from app import agent_tools, chat_tools, modules

    names = {"feed_create_work", "feed_add_version", "feed_set_status", "feed_mark_posted", "feed_list"}
    assert names <= {t.name for t in agent_tools._project_tools()}
    assert names <= modules.CORE_TOOLS
    assert names <= set(chat_tools.group_tools("feed"))
    assert all(agent_tools.get_tool(n).scope == "global" for n in names)


# --------------------------------------------------------------------------- #
# Перенос старых работ (tools/feed_backfill.py)
# --------------------------------------------------------------------------- #


def test_backfill_copies_imports_and_is_idempotent(env, tmp_path):
    import importlib.util

    client, store = env
    spec = importlib.util.spec_from_file_location("feed_backfill",
                                                  Path(__file__).parent.parent / "tools" / "feed_backfill.py")
    backfill = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backfill)

    out = tmp_path / "offload" / "out"
    _video(out / "m_9x16.mp4", size="180x320")
    _video(out / "m_part1.mp4", size="180x320")
    import_root = settings.data_dir / "runtime" / "feed_import"
    mapping = {"works": [{"title": "Мувик", "kind": "movie", "dir": str(out), "versions": [
        {"label": "v1", "note": "первая", "status": "accepted", "verdict": "«норм»",
         "variants": [{"file": "m_9x16.mp4", "label": "9:16"}, {"file": "m_part1.mp4", "label": "TikTok часть 1/1"}]},
    ]}]}

    class FakeApi:
        def call(self, method, path, body=None):
            resp = client.request(method, path, json=body)
            assert resp.status_code == 200, resp.text
            return resp.json()

    works = backfill.plan(mapping)
    backfill.run(FakeApi(), works, import_root, str(import_root), dry_run=False, copy=True)
    backfill.run(FakeApi(), works, import_root, str(import_root), dry_run=False, copy=True)  # второй раз — ничего
    page = FEED.list_feed(store, with_versions=True)
    assert page["total"] == 1
    version = page["items"][0]["versions"][0]
    assert version["status"] == "accepted" and version["verdict"] == "«норм»" and len(version["variants"]) == 2
    assert all(v["file_present"] and v["source"] == "file" for v in version["variants"])
    assert not list(import_root.rglob("*.mp4"))  # папка импорта — только перевалка
    assert (out / "m_9x16.mp4").exists()  # исходники на месте
    with pytest.raises(SystemExit):
        backfill.plan({"works": [{"title": "x", "kind": "movie", "dir": str(out), "versions": [
            {"label": "v1", "variants": [{"file": "нет.mp4", "label": "9:16"}]}]}]})
