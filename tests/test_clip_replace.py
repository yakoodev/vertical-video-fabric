"""Новая версия совы и гайда заменяет невыложенную прошлую (app/clip_replace.py, app/feed_replace.py)."""

from __future__ import annotations

from app import clip_groups as CG
from app import clip_replace as RP
from app import explainer as E
from app import explainer_gen as X
from app import feed as FEED
from app import projects as P
from app.ingest import MediaMetadata
from app.projects_stats import add_manual_publication
from app.settings import settings
from test_explainer import env  # noqa: F401 - env — фикстура
from test_releases import _done_video, _render

BYTES = b"finished-clip-bytes" * 100


def _ids(store, plan_id):
    return [int(m["id"]) for m in CG.group_members(store, plan_id)]


def _finish(store, plan_id: int) -> dict:
    """Готовый файл нового плана — тем же путём, что у рендера (Store.finish_clip_render)."""
    row = store.db.query_one("SELECT source_id FROM clip_plans WHERE id = ?", (plan_id,))
    clip = store.create_clip(row["source_id"], clip_plan_id=plan_id, title="рендер")
    out = settings.clip_dir / f"r{clip['id']}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(BYTES)
    return store.finish_clip_render(clip["id"], status="succeeded", output_path=out, preview_path=out,
                                    duration_sec=40, width=1080, height=1920, size_bytes=len(BYTES))


def _reassemble(store, video):
    E.set_status(store, video["id"], "assembling", ("done",))
    again = X.advance(store, video["id"])
    assert again["status"] == "done", again.get("error")
    return again


def test_owl_rebuild_replaces_unpublished_draft_after_its_render(env):  # noqa: F811
    client, store, tmp = env
    _ids_, video = _done_video(store, tmp)
    first = video["clip_id"]
    old_render = _render(store, first)
    again = _reassemble(store, video)
    second = again["clip_id"]
    # Пока у новой сборки нет файла — ничего не прячем: прошлая версия на месте.
    assert _ids(store, first) == [first, second]
    _finish(store, second)
    assert _ids(store, second) == [second]
    plan = store.get_clip_plan(first, include_segments=False)
    assert plan["hidden"] and store.get_clip(old_render["id"])["status"] == "succeeded"  # файл не удалён
    view = client.get(f"/api/clip-view/{second}").json()
    assert all(v["plan_id"] == second for v in view["versions"])
    assert [r["plan_id"] for r in view["replaced"]] == [first]
    # Старая ссылка не ломается, и замену можно вернуть.
    assert client.get(f"/api/clip-view/{first}").status_code == 200
    RP.restore(store, first)
    assert _ids(store, second) == [first, second] and not store.get_clip_plan(first, include_segments=False)["hidden"]


def test_published_owl_version_stays(env):  # noqa: F811
    _client, store, tmp = env
    _ids_, video = _done_video(store, tmp)
    first = video["clip_id"]
    render = _render(store, first)
    add_manual_publication(store, url="https://www.tiktok.com/@owl/video/1", clip_id=render["id"], clip_plan_id=first,
                           platform="tiktok")
    second = _reassemble(store, video)["clip_id"]
    _finish(store, second)
    assert _ids(store, second) == [first, second]
    assert not store.get_clip_plan(first, include_segments=False)["hidden"]
    assert RP.protected_reason(store, first) == "выложен"


def test_owner_marks_and_accepted_feed_version_are_kept(env):  # noqa: F811
    _client, store, tmp = env
    _ids_, video = _done_video(store, tmp)
    first = video["clip_id"]
    render = _render(store, first)
    work = FEED.create_work(store, "Сова", kind="series")
    version = FEED.add_version(store, work["id"], variants=[{"clip_id": render["id"]}])
    FEED.set_status(store, version["id"], "accepted", "норм")
    assert RP.protected_reason(store, first) == "принят владельцем в ленте"
    second = _reassemble(store, video)["clip_id"]
    out = RP.supersede_older(store, second)
    assert out["replaced"] == [] and first in out["kept"]


def test_feed_version_gets_the_new_file_instead_of_v2(env):  # noqa: F811
    _client, store, tmp = env
    _ids_, video = _done_video(store, tmp)
    first = video["clip_id"]
    old = _finish(store, first)
    work = FEED.create_work(store, "Сова 25", kind="series")
    FEED.add_version(store, work["id"], note="первая сборка", variants=[{"clip_id": old["id"], "label": "9:16"}],
                     made_by="agent")
    second = _reassemble(store, video)["clip_id"]
    new = _finish(store, second)
    got = FEED.add_version(store, work["id"], note="поза на хуке другая", variants=[{"clip_id": new["id"]}],
                           made_by="agent")
    assert got["n"] == 1 and got["note"] == "поза на хуке другая"
    assert [v["clip_id"] for v in got["variants"]] == [new["id"]] and got["variants"][0]["label"] == "9:16"
    assert len(FEED.get_work(store, work["id"])["versions"]) == 1
    # Вердикт владельца у версии — новая сборка идёт новой версией, его слова на старой.
    FEED.set_status(store, got["id"], "rejected", "хук слабый")
    third = _reassemble(store, video)["clip_id"]
    newest = _finish(store, third)
    v2 = FEED.add_version(store, work["id"], variants=[{"clip_id": newest["id"]}], made_by="agent")
    assert v2["n"] == 2


def test_guide_upload_replaces_unpublished_previous_upload(env, monkeypatch):  # noqa: F811
    import app.main as main_module

    client, store, _tmp = env
    monkeypatch.setattr(main_module, "probe_media",
                        lambda path: MediaMetadata(duration_sec=8, width=1080, height=1920, fps=30, raw={}))
    pid = P.create_project(store, "Витубика")["id"]

    def up(**data):
        r = client.post("/api/clips/upload", files={"file": ("g.mp4", BYTES, "video/mp4")}, data=data)
        assert r.status_code == 200, r.text
        return r.json()

    first = up(title="Гайд №5", project_id=str(pid), pipeline="vtubika_guide")
    work = FEED.create_work(store, "Гайд №5", project_id=pid, kind="promo")
    FEED.add_version(store, work["id"], variants=[{"clip_id": first["clip_id"]}], made_by="agent")
    second = up(title="Гайд №5 v2", project_id=str(pid), pipeline="vtubika_guide")
    assert second["version_of"] == first["clip_plan_id"] and second["replaced"] == [first["clip_plan_id"]]
    assert _ids(store, second["clip_plan_id"]) == [second["clip_plan_id"]]
    assert second["feed_version"] == "v1"
    versions = FEED.get_work(store, work["id"])["versions"]
    assert len(versions) == 1 and versions[0]["variants"][0]["clip_id"] == second["clip_id"]
    # Обычная загрузка (без конвейера замены) по-прежнему копит версии.
    a = up(title="Ролик", project_id=str(pid))
    b = up(title="Ролик v2", project_id=str(pid))
    assert "replaced" not in b and _ids(store, b["clip_plan_id"]) == [a["clip_plan_id"], b["clip_plan_id"]]
