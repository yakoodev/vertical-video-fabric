"""Расписание публикации: время в UTC, как у очереди; пояс переводится; прошедшее время — ошибка, а не пост сразу."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app import publishing_api
from app.api_common import _normalize_schedule_text
from test_publish_duplicates import env  # noqa: F401 - клип, аккаунт TikTok, подмена площадки
from app.worker import JobWorker


def test_offset_is_converted_to_utc():
    assert _normalize_schedule_text("2031-01-01T20:00:00+03:00") == "2031-01-01 17:00:00"
    assert _normalize_schedule_text("2031-01-01T17:00:00.000Z") == "2031-01-01 17:00:00"
    assert _normalize_schedule_text("2031-01-01 17:00") == "2031-01-01 17:00:00"  # без пояса — уже UTC
    with pytest.raises(ValueError):
        _normalize_schedule_text("завтра")


def _post(env, when):  # noqa: F811
    return env["client"].post(f"/api/clips/{env['clip']['id']}/posts",
                              json={"title": "Т", "targets": [env["account"]["id"]], "scheduled_at": when})


def test_past_time_is_refused_instead_of_posting_now(env):  # noqa: F811
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
    response = _post(env, yesterday)
    assert response.status_code == 400 and "уже прошло" in response.json()["detail"]
    assert env["store"].list_jobs() == []


def test_scheduled_job_fires_once_and_only_when_due(env, monkeypatch):  # noqa: F811
    soon = datetime.now(timezone.utc) + timedelta(minutes=30)
    job = _post(env, soon.isoformat()).json()
    assert job["scheduled_at"] == soon.strftime("%Y-%m-%d %H:%M:%S")
    worker = JobWorker(env["store"])
    assert worker.process_once() is False and env["provider"].calls == []  # рано
    env["store"].db.execute("UPDATE jobs SET scheduled_at = ? WHERE id = ?",
                            ((datetime.now(timezone.utc) - timedelta(seconds=5)).strftime("%Y-%m-%d %H:%M:%S"), job["id"]))
    assert worker.process_once() is True
    assert worker.process_once() is False
    assert len(env["provider"].calls) == 1


def test_batch_spacing_counts_from_utc_now(env, monkeypatch):  # noqa: F811
    fixed = datetime(2031, 1, 1, 12, 0)
    monkeypatch.setattr(publishing_api, "_utc_now", lambda: fixed)
    second = env["store"].create_clip(env["clip"]["source_id"], clip_plan_id=env["clip"]["clip_plan_id"],
                                      status="succeeded")
    output = env["store"].get_clip(env["clip"]["id"])["output_path"]
    env["store"].db.execute("UPDATE clips SET output_path = ? WHERE id = ?", (output, second["id"]))
    response = env["client"].post("/api/clips/posts-batch", json={
        "clip_ids": [env["clip"]["id"], second["id"]], "targets": [env["account"]["id"]], "interval_minutes": 60})
    jobs = response.json()["jobs"]
    assert [j["scheduled_at"] for j in jobs] == ["", "2031-01-01 13:00:00"]
