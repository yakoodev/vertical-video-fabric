"""Навигация Проект → Видео → Клип: имя видео, счётчики панели, вопросы всех проектов, адреса."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from app import nav, projects as P, stream_questions as Q
from app.downloads import DownloadService
from app.settings import settings
from test_api_pipeline import _client


@pytest.fixture()
def client_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    return _client(tmp_path, monkeypatch)


@pytest.fixture()
def store(client_store):
    return client_store[1]


def add_source(store, filename: str, source_type: str = "upload", project_id: int | None = None) -> dict:
    source = store.create_source(source_type, settings.source_dir / filename, original_filename=filename, duration_sec=120)
    if project_id:
        P.set_source_project(store, source["id"], project_id)
    return store.get_source(source["id"])


def add_plan(store, source: dict, title: str) -> dict:
    analysis = store.create_ai_analysis(source["id"], "mock", status="succeeded")
    segment = store.create_ai_segment(
        analysis["id"],
        {"start_sec": 1, "end_sec": 30, "title": title, "description": "", "score": 0.5, "category": "x", "color": "#ffffff", "reason": ""},
    )
    return store.create_clip_plan(source["id"], analysis["id"], title, segment_ids=[segment["id"]])


@pytest.mark.parametrize(
    ("name", "kwargs", "want"),
    [
        ("Мура Minecraft", {}, "Мура Minecraft"),
        ("promo_final_v2.mp4", {}, "promo final v2"),
        ("twitch-2915543509.mp4", {"vod_title": "Майнкрафт с чибиками"}, "Майнкрафт с чибиками"),
        ("twitch-2915543509.mp4", {"plan_titles": ["Мура Minecraft 1/4", "Мура Minecraft 2/4", "Мура: мать года"]}, "Мура Minecraft"),
        ("twitch-2905714957.mp4", {"plan_titles": ["Кошка", "Паркуйтесь сами"]}, "Стрим 20.10"),
        ("e396f3f9827f4fa4bd63858819c45e6a.mp4", {"tag_title": "Видео с тегом"}, "Видео с тегом"),
        ("gromfaer-685246-s9-e1-Сыендук.mp4", {}, "Сезон 9, серия 1 (Сыендук)"),
        ("apexA_28 v3m Apex Legends 2026.05.22 - 20.49.07.mp4", {}, "Запись 22.05.2026, 20:49"),
        ("v3_31_m.mp4", {"plan_titles": ["B31 v3: Ватсон на 1 хп"]}, "Ватсон на 1 хп"),
        ("", {"tag_date": "2026-09-17T10:00:00Z"}, "Стрим 17.09"),
    ],
)
def test_video_title(name: str, kwargs: dict, want: str) -> None:
    source = {"original_filename": name, "source_type": "twitch_url", "created_at": "2026-10-20 12:59:05"}
    assert nav.video_title(source, **kwargs) == want


def test_owner_name_wins_over_hints() -> None:
    source = {"original_filename": "Мура 27.10", "source_type": "twitch_url", "created_at": "2026-10-20 12:59:05"}
    assert nav.video_title(source, plan_titles=["Что-то 1/2"], vod_title="Название VOD") == "Мура 27.10"


def test_titles_for_reads_plans_and_vod(store) -> None:
    project = P.create_project(store, "Мура", kind="stream_channel")
    with_parts = add_source(store, "twitch-1.mp4", "twitch_url", project["id"])
    add_plan(store, with_parts, "Мура Minecraft 1/4")
    bare = add_source(store, "twitch-2.mp4", "twitch_url", project["id"])
    names = nav.titles_for(store, [with_parts, bare])
    assert names[with_parts["id"]] == "Мура Minecraft"
    assert names[bare["id"]].startswith("Стрим ")


def test_nav_counts(store) -> None:
    mura = P.create_project(store, "Мура", kind="stream_channel")
    empty = P.create_project(store, "Пусто")
    source = add_source(store, "twitch-1.mp4", "twitch_url", mura["id"])
    add_source(store, "file.mp4")
    Q.add_questions(store, source["id"], [{"question": "Кто это?", "options": ["А", "Б"], "t_start": 1}])
    state = nav.nav_counts(store)
    by_id = {p["id"]: p for p in state["projects"]}
    assert by_id[mura["id"]]["videos"] == 1
    assert by_id[mura["id"]]["open_questions"] == 1
    assert by_id[empty["id"]]["videos"] == 0
    assert state["no_project_videos"] == 1
    assert state["inbox"]["questions"] == 1
    assert state["inbox"]["total"] == state["inbox"]["approvals"] + state["inbox"]["questions"] + state["inbox"]["memes"]


def test_project_videos_light_list(store) -> None:
    project = P.create_project(store, "Мура", kind="stream_channel")
    source = add_source(store, "twitch-1.mp4", "twitch_url", project["id"])
    add_plan(store, source, "Мура 27.10 1/4")
    outside = add_source(store, "other.mp4")
    data = nav.project_videos(store, project["id"])
    assert [v["id"] for v in data["videos"]] == [source["id"]]
    row = data["videos"][0]
    assert row["name"] == "Мура 27.10"
    assert row["kind"] == "Стрим"
    assert row["clips"] == 1 and row["no_render"] == 1
    assert row["has_file"] is False
    assert [v["id"] for v in nav.project_videos(store, None)["videos"]] == [outside["id"]]


def test_inbox_questions_all_projects(store) -> None:
    one = P.create_project(store, "Один", kind="stream_channel")
    two = P.create_project(store, "Два", kind="stream_channel")
    a = add_source(store, "twitch-1.mp4", "twitch_url", one["id"])
    b = add_source(store, "twitch-2.mp4", "twitch_url", two["id"])
    Q.add_questions(store, a["id"], [{"question": "Кто это?", "options": ["А", "Б"], "t_start": 1}])
    Q.add_questions(store, b["id"], [{"question": "Что это?", "options": ["А", "Б"], "t_start": 2}])
    everything = Q.inbox_state(store)
    assert {q["question"] for q in everything["questions"]} == {"Кто это?", "Что это?"}
    assert {s["project_name"] for s in everything["sources"]} == {"Один", "Два"}
    assert {p["name"]: p["open"] for p in everything["open_by_project"]} == {"Один": 1, "Два": 1}
    only = Q.inbox_state(store, project_id=one["id"])
    assert [q["question"] for q in only["questions"]] == ["Кто это?"]
    question = everything["questions"][0]
    Q.answer_question(store, question["id"], 0, "")
    assert len(Q.inbox_state(store)["questions"]) == 1
    assert len(Q.inbox_state(store, only_open=False)["questions"]) == 2


def test_list_clips_for_project_keeps_other_modes(store) -> None:
    project = P.create_project(store, "Мура", kind="stream_channel")
    inside = add_source(store, "twitch-1.mp4", "twitch_url", project["id"])
    outside = add_source(store, "other.mp4")
    own = store.create_clip(inside["id"], title="В проекте")
    other = store.create_clip(outside["id"], title="Снаружи")
    assert [c["id"] for c in store.list_clips(project_id=project["id"])] == [own["id"]]
    assert [c["id"] for c in store.list_clips(source_id=outside["id"])] == [other["id"]]
    assert {c["id"] for c in store.list_clips()} == {own["id"], other["id"]}


def test_download_lands_in_project(store) -> None:
    class Ingestor:
        def ingest_url(self, url: str, quality: str = "") -> dict:
            return add_source(store, "twitch-9.mp4", "twitch_url")

    project = P.create_project(store, "Мура", kind="stream_channel")
    service = DownloadService(Ingestor())
    seen: list[dict] = []

    def on_source(source: dict) -> None:
        P.set_source_project(store, source["id"], project["id"])
        seen.append(source)

    task = service.start("https://www.twitch.tv/videos/9", on_source=on_source)
    for _ in range(100):
        if service.get(task["id"])["status"] not in ("queued", "downloading"):
            break
        time.sleep(0.02)
    assert service.get(task["id"])["status"] == "succeeded"
    assert seen and store.get_source(seen[0]["id"])["project_id"] == project["id"]


def test_api_routes(client_store) -> None:
    c, store = client_store
    project = c.post("/api/projects", json={"name": "Мура", "kind": "stream_channel"}).json()
    source = add_source(store, "twitch-1.mp4", "twitch_url", project["id"])
    add_plan(store, source, "Мура Minecraft 1/4")

    state = c.get("/api/nav").json()
    assert state["projects"][0]["videos"] == 1
    videos = c.get(f"/api/nav/videos?project_id={project['id']}").json()["videos"]
    assert videos[0]["name"] == "Мура Minecraft"
    assert c.get(f"/api/sources/{source['id']}").json()["display_name"] == "Мура Minecraft"
    assert c.get("/api/sources?lite=1").json()[0]["display_name"] == "Мура Minecraft"
    assert c.get(f"/api/projects/{project['id']}").json()["sources"][0]["display_name"] == "Мура Minecraft"
    assert c.get(f"/api/projects/{project['id']}/clips").json() == []
    assert c.get("/api/projects/999/clips").status_code == 404
    assert c.get("/api/stream-questions").json()["questions"] == []


def test_canvas_can_start_in_project(client_store) -> None:
    c, store = client_store
    project = c.post("/api/projects", json={"name": "Промо", "kind": "promo"}).json()
    plan = c.post("/api/canvas-clips", json={"duration": 10, "title": "Холст", "project_id": project["id"]}).json()
    assert P.project_of_plan(store, plan["id"]) == project["id"]
    rows = nav.project_videos(store, project["id"])["generated"]
    assert [(g["kind"], g["plan_id"]) for g in rows] == [("Холст", plan["id"])]


def test_channel_soft_view_is_not_an_error(client_store) -> None:
    c, _store = client_store
    project = c.post("/api/projects", json={"name": "ИИ", "kind": "ai_video"}).json()
    assert c.get(f"/api/projects/{project['id']}/explainer-channel").status_code == 404
    soft = c.get(f"/api/projects/{project['id']}/explainer-channel?soft=1")
    assert soft.status_code == 200 and soft.json() == {"no_channel": True}
