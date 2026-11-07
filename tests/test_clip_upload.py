"""Загруженный готовый ролик — настоящий клип: план, экран /clips/<id плана>, строка в проекте. Файл не перекодируется."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from app import clip_groups as CG
from app import clip_upload as C
from app import nav
from app import projects as P
from app import releases as R
from app.ingest import MediaMetadata
from app.settings import settings
from test_api_pipeline import _client, _require_ffmpeg
from test_clip_groups import add_plan, add_render, add_source

ROOT = Path(__file__).resolve().parents[1]
BYTES = b"finished-clip-bytes" * 100


@pytest.fixture
def env(tmp_path, monkeypatch):
    import app.main as main_module

    client, store = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(main_module, "probe_media", lambda path: MediaMetadata(duration_sec=8, width=1080, height=1920, fps=30, raw={}))
    return client, store, tmp_path


def _upload(client, name="my-edit.mp4", **data):
    return client.post("/ui/clips/upload", files={"file": (name, BYTES, "video/mp4")}, data=data, follow_redirects=False)


def _foreign(store, count: int = 3) -> dict:
    """Чужие планы и рендеры: у загрузки номер рендера не совпадёт с номером плана, а по её номеру лежит чужой клип."""
    source = add_source(store)
    plan = add_plan(store, source, "Мура: чужой клип")
    for _ in range(count):
        add_render(store, plan)
    return plan


def test_redirect_goes_to_plan_when_clip_id_differs(env):
    client, store, _tmp = env
    foreign = _foreign(store)
    add_plan(store, add_source(store, "other.mp4"), "Ещё чужой")
    r = _upload(client, title="Мой ролик")
    assert r.status_code == 303
    plan_id = int(r.headers["location"].rsplit("/", 1)[1])
    clip = next(c for c in store.list_clips() if c["clip_plan_id"] == plan_id)
    assert clip["id"] != plan_id and clip["status"] == "succeeded" and clip["title"] == "Мой ролик"
    # Старый адрес был бы по номеру рендера: там другой экран. Новый адрес — этот клип.
    view = client.get(f"/api/clip-view/{plan_id}").json()
    assert view["name"] == "Мой ролик" and view["latest"]["id"] == clip["id"] and view["uploaded"] is True
    assert client.get(f"/api/clip-view/{clip['id']}").status_code == 404 or \
        client.get(f"/api/clip-view/{clip['id']}").json()["id"] != plan_id
    assert foreign["id"] != plan_id


def test_file_stays_byte_identical(env):
    client, store, _tmp = env
    r = _upload(client, title="Мой ролик")
    plan_id = int(r.headers["location"].rsplit("/", 1)[1])
    clip = next(c for c in store.list_clips() if c["clip_plan_id"] == plan_id)
    assert Path(clip["output_path"]).read_bytes() == BYTES
    assert Path(store.get_source(clip["source_id"])["local_path"]).read_bytes() == BYTES
    assert client.get(f"/media/clips/{clip['id']}").content == BYTES


def test_project_gets_the_clip_on_both_tabs(env):
    client, store, _tmp = env
    pid = P.create_project(store, "Канал")["id"]
    r = _upload(client, title="Мой ролик", project_id=str(pid))
    plan_id = int(r.headers["location"].rsplit("/", 1)[1])
    source_id = store.get_clip_plan(plan_id)["source_id"]
    # Скрытый источник не становится видео проекта.
    assert store.get_source(source_id)["project_id"] is None
    assert CG.clip_view(store, plan_id)["project"]["id"] == pid
    # Вкладка «Клипы»: обычная плитка, ни одного файла без клипа.
    cards = client.get("/api/clip-cards", params={"project_id": pid}).json()
    assert [c["id"] for c in cards["clips"]] == [plan_id] and cards["orphans"] == []
    assert cards["clips"][0]["uploaded"] is True and cards["clips"][0]["status"] == "ready"
    # Вкладка «Видео»: строка ведёт сразу на экран клипа.
    videos = client.get("/api/nav/videos", params={"project_id": pid}).json()
    row = next(g for g in videos["generated"] if g["head"] == plan_id)
    assert row["kind_key"] == "upload" and row["to"] == f"/clips/{plan_id}" and row["status"] == "ready"
    assert row["thumb_clip_id"] and row["name"] == "Мой ролик" and not videos["videos"]
    assert R.project_counts(store)[pid]["releases"] == 1
    assert nav.project_videos(store, pid)["generated"][0]["kind_label"] == "Загруженный клип"


def test_upload_without_project_and_with_unknown_project(env):
    client, store, _tmp = env
    r = _upload(client)
    assert r.status_code == 303
    plan_id = int(r.headers["location"].rsplit("/", 1)[1])
    assert CG.clip_view(store, plan_id)["project"] is None
    assert store.get_clip_plan(plan_id)["title"] == "my-edit"
    before = set(settings.source_dir.iterdir())
    plans = store.db.query_one("SELECT COUNT(*) AS n FROM clip_plans")["n"]
    r = _upload(client, project_id="99999")
    assert r.status_code == 404
    assert set(settings.source_dir.iterdir()) == before  # файл не записан
    assert store.db.query_one("SELECT COUNT(*) AS n FROM clip_plans")["n"] == plans
    assert _upload(client, project_id="не число").status_code == 400


def test_json_upload_for_the_page(env):
    client, store, _tmp = env
    _foreign(store)
    pid = P.create_project(store, "Канал")["id"]
    r = client.post("/api/clips/upload", files={"file": ("a.mp4", BYTES, "video/mp4")}, data={"project_id": str(pid)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["to"] == f"/clips/{body['clip_plan_id']}" and body["project_id"] == pid
    assert body["clip_id"] != body["clip_plan_id"] and store.get_clip(body["clip_id"])["clip_plan_id"] == body["clip_plan_id"]


def test_long_and_short_videos_get_a_plan(env, monkeypatch):
    import app.main as main_module

    client, store, _tmp = env
    for seconds in (1200.0, 0.8):
        monkeypatch.setattr(main_module, "probe_media", lambda path, s=seconds: MediaMetadata(duration_sec=s, width=1920, height=1080, fps=30, raw={}))
        r = _upload(client)
        assert r.status_code == 303, r.text
        view = CG.clip_view(store, int(r.headers["location"].rsplit("/", 1)[1]))
        assert view["duration_sec"] == seconds and view["format"] == "16:9"


def test_montage_actions_are_refused_with_explanation(env):
    client, store, _tmp = env
    r = _upload(client)
    plan_id = int(r.headers["location"].rsplit("/", 1)[1])
    view = CG.clip_view(store, plan_id)
    assert view["uploaded"] is True and view["stale"] is False and view["canvas"] is False
    assert view["status"] == "ready" and view["version_count"] == 1 and view["versions"][0]["status"] == "succeeded"
    r = client.post(f"/api/clip-plans/{plan_id}/render")
    assert r.status_code == 400 and "готовый загруженный файл" in r.json()["detail"]
    # Правка плана файл не делает устаревшим: рендерить тут нечего.
    store.update_clip_plan_status(plan_id, "candidate")
    assert CG.clip_view(store, plan_id)["stale"] is False
    # Другие конвейеры рендер не теряют: проверка только у загрузок.
    other = add_plan(store, add_source(store, "x.mp4"), "Обычный")
    assert C.is_upload_plan(store, other["id"]) is False and C.is_upload_plan(store, plan_id) is True


def test_title_and_description_save_on_the_clip_screen(env):
    client, store, _tmp = env
    r = _upload(client, title="Мой ролик", description="Описание ролика")
    plan_id = int(r.headers["location"].rsplit("/", 1)[1])
    assert CG.clip_view(store, plan_id)["meta"]["description"] == "Описание ролика"
    saved = CG.save_meta(store, plan_id, "Заголовок", "Новое", ["тег"])
    assert saved["title"] == "Заголовок"
    assert CG.clip_view(store, plan_id)["meta"]["hashtags"] == ["#тег"]


def test_render_with_settings_redirects_to_same_clip_as_version(env, monkeypatch):
    import app.main as main_module

    client, store, _tmp = env
    _foreign(store)
    r = _upload(client, title="Мой ролик")
    plan_id = int(r.headers["location"].rsplit("/", 1)[1])
    clip = next(c for c in store.list_clips() if c["clip_plan_id"] == plan_id)
    second = store.create_clip(clip["source_id"], clip_plan_id=plan_id, title="Мой ролик")
    monkeypatch.setattr(main_module.clip_render_service, "render_uploaded_clip", lambda clip_id, **kw: second)
    r = client.post(f"/ui/clips/{clip['id']}/render", data={}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/clips/{plan_id}"


def test_real_file_plays_as_version_and_render_with_settings_links_to_plan(env, tmp_path, monkeypatch):
    _require_ffmpeg()
    import app.main as main_module
    from app.ingest import probe_media as real_probe
    from app.render import ClipRenderService

    client, store, _tmp = env
    monkeypatch.setattr(main_module, "probe_media", real_probe)
    video = tmp_path / "real.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=108x192:r=10:d=2", "-f", "lavfi", "-i",
                    "anullsrc=r=48000:cl=stereo", "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(video)],
                   check=True, timeout=120)
    raw = video.read_bytes()
    r = client.post("/ui/clips/upload", files={"file": ("real.mp4", raw, "video/mp4")}, data={"title": "Настоящий"}, follow_redirects=False)
    assert r.status_code == 303, r.text
    plan_id = int(r.headers["location"].rsplit("/", 1)[1])
    view = client.get(f"/api/clip-view/{plan_id}").json()
    clip_id = view["latest"]["id"]
    assert client.get(view["versions"][0]["url"]).content == raw
    assert client.get(f"/media/clips/{clip_id}/thumb").status_code == 200
    made = ClipRenderService(store).render_uploaded_clip(clip_id)
    assert made["clip_plan_id"] == plan_id and made["id"] != clip_id
    view = client.get(f"/api/clip-view/{plan_id}").json()
    assert view["version_count"] == 2 and client.get(f"/media/clips/{clip_id}").content == raw


def test_old_planless_uploads_get_linked_without_touching_files(env):
    client, store, _tmp = env
    pid = P.create_project(store, "Канал")["id"]
    path = settings.source_dir / "old.mp4"
    path.write_bytes(BYTES)
    source = store.create_source("clip_upload", path, original_filename="old.mp4", duration_sec=8, status="ready")
    P.set_source_project(store, source["id"], pid)
    clip_path = settings.clip_dir / "old.mp4"
    clip_path.write_bytes(BYTES)
    clips = []
    for _ in range(2):
        made = store.create_clip(source["id"], title="Старая загрузка")
        clips.append(store.finish_clip_render(made["id"], status="succeeded", output_path=clip_path, preview_path=clip_path,
                                              duration_sec=8, width=1080, height=1920, size_bytes=len(BYTES)))
    # Рендер обычного видео без плана (план удалили) этот путь не трогает.
    normal = store.create_clip(add_source(store, "n.mp4")["id"], title="Обычный без плана")
    assert [c["id"] for c in C.planless_uploads(store)] == [c["id"] for c in clips]
    assert client.get("/api/clip-cards", params={"project_id": pid}).json()["orphans"]

    linked = C.link_planless(store, source["id"])
    plan_id = linked["clip_plan_id"]
    assert linked["clips"] == [c["id"] for c in clips] and C.planless_uploads(store) == []
    assert store.get_clip(normal["id"])["clip_plan_id"] is None
    assert clip_path.read_bytes() == BYTES
    cards = client.get("/api/clip-cards", params={"project_id": pid}).json()
    assert cards["orphans"] == [] and [c["id"] for c in cards["clips"]] == [plan_id]
    assert CG.clip_view(store, plan_id)["version_count"] == 2
    # Повторно ничего не меняется.
    assert C.link_planless(store, source["id"])["clip_plan_id"] is None
    assert store.db.query_one("SELECT COUNT(*) AS n FROM clip_plans WHERE source_id = ?", (source["id"],))["n"] == 1


def test_tool_dry_run_writes_nothing(env):
    _client_, store, _tmp = env
    path = settings.source_dir / "old2.mp4"
    path.write_bytes(BYTES)
    source = store.create_source("clip_upload", path, original_filename="old2.mp4", duration_sec=8, status="ready")
    made = store.create_clip(source["id"], title="Старая")
    run = subprocess.run([sys.executable, str(ROOT / "tools" / "link_uploaded_clips.py"), "--db", str(settings.data_dir / "app.sqlite")],
                         capture_output=True, text=True, encoding="utf-8", timeout=120, cwd=str(ROOT),
                         env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert run.returncode == 0, run.stderr
    assert f"файлы {made['id']}" in run.stdout and "--apply" in run.stdout
    assert store.get_clip(made["id"])["clip_plan_id"] is None
