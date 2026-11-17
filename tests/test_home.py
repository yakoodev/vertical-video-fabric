"""«Главная» (app/home.py, GET /api/home, инструмент home_summary): блоки на месте, числа из настоящих таблиц,
упавший источник не валит остальное."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app import home
from test_api_pipeline import _client

BLOCKS = ["waiting", "recent_works", "upcoming", "top_week", "working", "spend", "projects", "examples"]


def _utc(delta: timedelta = timedelta()) -> str:
    return (datetime.now(timezone.utc).replace(tzinfo=None) + delta).strftime("%Y-%m-%d %H:%M:%S")


@pytest.fixture
def env(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    # Модули автопилота, вопросов и чата по умолчанию могут быть выключены — сводке нужны все.
    monkeypatch.setattr(home, "_module_on", lambda _store, _module: True)
    return client, store


def _insert(store, sql: str, params: tuple) -> int:
    return int(store.db.execute(sql, params).lastrowid)


def _seed(store) -> dict:
    db = store.db
    project = _insert(store, "INSERT INTO projects (name, kind) VALUES (?, ?)", ("Мура", "series"))
    _insert(store, "INSERT INTO projects (name, kind) VALUES (?, ?)", ("Тихий", "other"))
    source = _insert(store, "INSERT INTO sources (status, source_type, local_path, project_id) VALUES (?, ?, ?, ?)",
                     ("ready", "upload", "/nope.mp4", project))
    plan = _insert(store, "INSERT INTO clip_plans (source_id, title) VALUES (?, ?)", (source, "Лучший момент"))
    ok_clip = _insert(store, "INSERT INTO clips (source_id, clip_plan_id, status, title) VALUES (?, ?, ?, ?)",
                      (source, plan, "succeeded", "Лучший момент"))
    # Упавший рендер без нового удачного файла — в «Ждёт тебя»; упавший, но переделанный — нет.
    lone = _insert(store, "INSERT INTO clips (source_id, status, title, error) VALUES (?, ?, ?, ?)",
                   (source, "failed", "Сломанный", "ffmpeg упал"))
    _insert(store, "INSERT INTO clips (source_id, clip_plan_id, status, title, error, id) VALUES (?, ?, ?, ?, ?, ?)",
            (source, plan, "failed", "Старый", "x", ok_clip + 100))
    _insert(store, "INSERT INTO clips (source_id, clip_plan_id, status, title, id) VALUES (?, ?, ?, ?, ?)",
            (source, plan, "succeeded", "Новый", ok_clip + 101))
    account = _insert(store, "INSERT INTO accounts (platform, label, encrypted_cookies) VALUES (?, ?, ?)",
                      ("tiktok", "yakoodev", "x"))

    def job(status: str, scheduled: str | None, title: str, error: str = "") -> int:
        jid = _insert(store, "INSERT INTO jobs (clip_id, status, title, scheduled_at, source_filename, source_path, "
                             "source_sha256, source_size_bytes, error) VALUES (?, ?, ?, ?, 'a.mp4', '/a.mp4', '', 1, ?)",
                      (ok_clip, status, title, scheduled, error))
        return jid

    later = job("queued", _utc(timedelta(hours=5)), "Завтрашний")
    sooner = job("queued", _utc(timedelta(hours=1)), "Скорый")
    job("queued", _utc(-timedelta(hours=1)), "Уже пора")  # время прошло — это не «скоро выйдет»
    for jid in (later, sooner):
        _insert(store, "INSERT INTO job_targets (job_id, account_id, platform) VALUES (?, ?, ?)", (jid, account, "tiktok"))
    done = job("succeeded", None, "Вышел")
    target = _insert(store, "INSERT INTO job_targets (job_id, account_id, platform, status, remote_url, finished_at) "
                            "VALUES (?, ?, ?, 'succeeded', ?, ?)",
                     (done, account, "tiktok", "https://tiktok.com/@y/video/1", _utc(-timedelta(days=2))))
    store.add_publication_stats(target, "tiktok", {"views": 1234, "likes": 50})
    old = job("succeeded", None, "Старый пост")
    old_target = _insert(store, "INSERT INTO job_targets (job_id, account_id, platform, status, remote_url, finished_at) "
                                "VALUES (?, ?, ?, 'succeeded', ?, ?)",
                         (old, account, "tiktok", "https://tiktok.com/@y/video/2", _utc(-timedelta(days=20))))
    store.add_publication_stats(old_target, "tiktok", {"views": 99999})
    failed_job = _insert(store, "INSERT INTO jobs (status, title, source_filename, source_path, source_sha256, "
                                "source_size_bytes, error) VALUES ('failed', 'Не вышло', 'a', '/a', '', 1, 'cookies')", ())

    # Лента: принятая и не выложенная, на проверке, принятый AMV (пример), черновик.
    def work(title: str, kind: str, status: str, project_id=None) -> int:
        wid = _insert(store, "INSERT INTO feed_works (title, kind, project_id, activity_at) VALUES (?, ?, ?, ?)",
                      (title, kind, project_id, _utc()))
        _insert(store, "INSERT INTO feed_versions (work_id, n, status, status_at) VALUES (?, 1, ?, ?)",
                (wid, status, _utc()))
        return wid

    movie = work("BURN мувик", "movie", "accepted", project)
    work("Мем про лифт", "meme", "review")
    amv = work("Pain AMV", "amv", "accepted")
    work("Черновик", "other", "draft")

    db.execute("INSERT INTO approvals (project_id, kind, title) VALUES (?, 'publish', 'Карточка 1')", (project,))
    return {"project": project, "plan": plan, "lone": lone, "sooner": sooner, "later": later, "failed_job": failed_job,
            "movie": movie, "amv": amv}


def test_empty_service_has_every_block(env):
    client, _store = env
    data = client.get("/api/home").json()
    assert data["errors"] == []
    for block in BLOCKS:
        assert block in data and "error" not in data[block], block
    assert data["waiting"]["total"] == 0
    assert data["recent_works"]["items"] == [] and data["upcoming"]["items"] == []
    assert data["top_week"]["items"] == [] and data["projects"]["items"] == []
    assert data["spend"]["gemini"]["calls"] == 0 and data["spend"]["polza"]["spent_rub"] == 0
    # Примеры — витрина: карточки есть и без работ, у каждой тип «Нового видео».
    assert {e["type"] for e in data["examples"]["items"]} >= {"frag", "amv", "mv", "expl", "3d"}


def test_blocks_count_real_rows(env):
    client, store = env
    ids = _seed(store)
    data = client.get("/api/home").json()
    assert data["errors"] == []

    waiting = data["waiting"]
    assert waiting["feed"] == {"ready_unposted": 2, "review": 1}
    assert waiting["approvals"]["count"] == 1 and waiting["approvals"]["first"][0]["project_name"] == "Мура"
    failed = {(i["kind"], i["id"]) for i in waiting["failed"]["items"]}
    assert ("clip", ids["lone"]) in failed and ("job", ids["failed_job"]) in failed
    assert all(i["label"] != "Старый" for i in waiting["failed"]["items"])  # переделан — не висит
    assert waiting["total"] == 2 + 1 + 1 + waiting["failed"]["count"]

    recent = data["recent_works"]
    assert recent["total"] == 4 and len(recent["items"]) == 4
    assert {"id", "title", "cover_url", "latest", "ready_unposted"} <= set(recent["items"][0])

    upcoming = data["upcoming"]
    assert [i["job_id"] for i in upcoming["items"]] == [ids["sooner"], ids["later"]]
    assert upcoming["items"][0]["targets"] == [{"platform": "tiktok", "account": "yakoodev"}]
    assert upcoming["items"][0]["project_name"] == "Мура" and upcoming["total"] == 2

    top = data["top_week"]
    assert [i["views"] for i in top["items"]] == [1234]  # 20-дневный пост в неделю не попал
    assert top["items"][0]["to"] == f"/clips/{ids['plan']}" and top["items"][0]["account"] == "yakoodev"

    projects = data["projects"]["items"]
    assert projects[0]["name"] == "Мура" and projects[0]["next"]["job_id"] == ids["sooner"]
    assert projects[0]["approvals"] == 1 and projects[0]["last_activity_at"]
    assert projects[1]["name"] == "Тихий" and projects[1]["next"] is None

    examples = {e["key"]: e for e in data["examples"]["items"]}
    assert examples["frag"]["example"]["to"] == f"/feed?work={ids['movie']}"
    assert examples["amv"]["example"]["work_id"] == ids["amv"]
    assert examples["mv"]["example"] is None  # клипа на песню в ленте нет, мувик сюда не перетянут


def test_failed_source_keeps_other_blocks(env, monkeypatch):
    client, store = env
    _seed(store)

    def boom(*_a, **_kw):
        raise RuntimeError("база статистики недоступна")

    monkeypatch.setattr(type(store), "latest_publication_stats", boom)
    data = client.get("/api/home").json()
    assert data["errors"] == ["top_week"]
    assert data["top_week"] == {"error": "база статистики недоступна"}
    assert data["upcoming"]["total"] == 2 and data["waiting"]["approvals"]["count"] == 1

    # Лента упала: её блоки с ошибкой, внутри «Ждёт тебя» — только её счётчик, остальное считается.
    from app import feed as FEED

    monkeypatch.setattr(FEED, "list_feed", boom)
    data = client.get("/api/home").json()
    assert set(data["errors"]) == {"top_week", "recent_works", "examples"}
    assert "error" not in data["waiting"] and data["waiting"]["feed"] is None
    assert "feed" in data["waiting"]["errors"] and data["waiting"]["approvals"]["count"] == 1
    assert data["projects"]["items"] and data["spend"]["polza"]["cap_rub"] > 0


def test_home_summary_tool(env):
    _client_, store = env
    _seed(store)
    from app.agent_tools import ToolContext, run_tool

    ctx = ToolContext(store=store)
    out, ok = run_tool(ctx, "home_summary", {"blocks": ["waiting", "upcoming"]}, validate=True)
    assert ok and set(out) >= {"waiting", "upcoming"} and "projects" not in out
    assert out["upcoming"]["total"] == 2
    bad, ok = run_tool(ctx, "home_summary", {"blocks": ["nope"]}, validate=False)
    assert not ok
