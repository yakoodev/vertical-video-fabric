"""Повторная загрузка того же ролика — версия прошлой без version_of (app/upload_versions.py)."""

from __future__ import annotations

import pytest

from app import feed as FEED
from app import projects as P
from app import upload_versions as U
from app.ingest import MediaMetadata
from test_api_pipeline import _client

BYTES = b"finished-clip-bytes" * 100


@pytest.fixture
def env(tmp_path, monkeypatch):
    import app.main as main_module

    client, store = _client(tmp_path, monkeypatch)
    size = {"w": 1080, "h": 1920}
    monkeypatch.setattr(main_module, "probe_media",
                        lambda path: MediaMetadata(duration_sec=8, width=size["w"], height=size["h"], fps=30, raw={}))
    return client, store, size


def _up(client, **data):
    r = client.post("/api/clips/upload", files={"file": (data.pop("name", "g.mp4"), BYTES, "video/mp4")}, data=data)
    assert r.status_code == 200, r.text
    return r.json()


def _members(store, plan_id):
    from app.clip_groups import group_members

    return [int(m["id"]) for m in group_members(store, plan_id)]


def test_upload_key_keeps_episode_numbers():
    assert U.upload_key("Гайд №5") == U.upload_key("Гайд №5 (v1b)") == U.upload_key("гайд_№5_v2.mp4") == "гайд 5"
    assert U.upload_key("Сова 24 — финал") == U.upload_key("Сова 24 ver.3") == "сова 24"
    assert U.upload_key("Гайд №5") != U.upload_key("Гайд №6")
    assert U.upload_key("Мура 1/4") != U.upload_key("Мура 2/4")
    assert U.upload_key("Проснулся в 3 часа") == "проснулся в 3 часа"  # «в 3» — не пометка версии


def test_same_title_same_project_becomes_version(env):
    client, store, _size = env
    pid = P.create_project(store, "Витубика")["id"]
    first = _up(client, title="Гайд №5", project_id=str(pid), pipeline="vtubika_guide")
    second = _up(client, title="Гайд №5 (v1b)", project_id=str(pid), pipeline="vtubika_guide")
    third = _up(client, name="гайд_5_v2.mp4", title="Гайд №5 v2", project_id=str(pid), pipeline="vtubika_guide")
    assert second["version_of"] == first["clip_plan_id"] and second["version_auto"] is True
    assert third["version_of"] == second["clip_plan_id"]
    assert _members(store, first["clip_plan_id"]) == [first["clip_plan_id"], second["clip_plan_id"], third["clip_plan_id"]]
    cards = client.get("/api/clip-cards", params={"project_id": pid}).json()["clips"]
    assert [c["id"] for c in cards] == [third["clip_plan_id"]] and cards[0]["versions"] == 3


def test_no_match_other_episode_project_pipeline_format(env):
    client, store, size = env
    pid = P.create_project(store, "Витубика")["id"]
    other = P.create_project(store, "Сова")["id"]
    first = _up(client, title="Гайд №5", project_id=str(pid))
    checks = [
        _up(client, title="Гайд №6", project_id=str(pid)),                       # другой выпуск
        _up(client, title="Гайд №5", project_id=str(other)),                     # другой проект
        _up(client, title="Гайд №5"),                                            # без проекта — правило молчит
        _up(client, title="Гайд №5", project_id=str(pid), pipeline="explainer"),  # другой конвейер
    ]
    for body in checks:
        assert "version_of" not in body and _members(store, body["clip_plan_id"]) == [body["clip_plan_id"]]
    size.update(w=1920, h=1080)
    wide = _up(client, title="Гайд №5", project_id=str(pid))
    assert "version_of" not in wide and "другом формате" in wide["version_note"]
    assert _members(store, first["clip_plan_id"]) == [first["clip_plan_id"]]


def test_published_previous_is_not_replaced(env):
    from app.projects_stats import add_manual_publication

    client, store, _size = env
    pid = P.create_project(store, "Сова")["id"]
    first = _up(client, title="Сова 24", project_id=str(pid))
    add_manual_publication(store, url="https://www.tiktok.com/@owl/video/1", clip_id=first["clip_id"],
                           clip_plan_id=first["clip_plan_id"], platform="tiktok")
    again = _up(client, title="Сова 24 v2", project_id=str(pid))
    assert "version_of" not in again and "уже выложен" in again["version_note"]
    assert _members(store, first["clip_plan_id"]) == [first["clip_plan_id"]]


def test_opt_out_and_explicit_version_of_wins(env):
    client, store, _size = env
    pid = P.create_project(store, "Витубика")["id"]
    first = _up(client, title="Гайд №5", project_id=str(pid))
    apart = _up(client, title="Гайд №5", project_id=str(pid), new_clip="true")
    assert "version_of" not in apart and _members(store, apart["clip_plan_id"]) == [apart["clip_plan_id"]]
    # Теперь похожих два: правило не угадывает, а явный version_of решает.
    unsure = _up(client, title="Гайд №5", project_id=str(pid))
    assert "version_of" not in unsure and "несколько" in unsure["version_note"]
    chosen = _up(client, title="Совсем другое имя", project_id=str(pid), version_of=str(apart["clip_plan_id"]))
    assert chosen["version_of"] == apart["clip_plan_id"] and not chosen.get("version_auto")
    assert first["clip_plan_id"] not in _members(store, chosen["clip_plan_id"])


def test_feed_card_gets_the_next_version(env):
    client, store, _size = env
    pid = P.create_project(store, "Витубика")["id"]
    first = _up(client, title="Гайд №5", project_id=str(pid))
    work = FEED.create_work(store, "Гайд №5", project_id=pid, kind="promo")
    FEED.add_version(store, work["id"], note="первая", variants=[{"clip_id": first["clip_id"], "label": "9:16"}])
    second = _up(client, title="Гайд №5 v2", project_id=str(pid))
    assert second["feed_work_id"] == work["id"] and second["feed_version"] == "v2"
    got = FEED.get_work(store, work["id"])
    assert [v["n"] for v in got["versions"]] == [2, 1]
    assert got["versions"][0]["variants"][0]["clip_id"] == second["clip_id"]
    assert got["versions"][0]["variants"][0]["label"] == "9:16" and got["versions"][0]["status"] == "review"
    # Агент по привычке добавляет ту же версию сам: второй v3 не появляется, его заметка ложится на v2.
    again = FEED.add_version(store, work["id"], note="музыка тише", variants=[{"clip_id": second["clip_id"]}],
                             made_by="agent")
    assert again["n"] == 2 and again["note"] == "музыка тише"
    assert len(FEED.get_work(store, work["id"])["versions"]) == 2
