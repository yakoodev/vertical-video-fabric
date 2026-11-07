"""Уходит ровно выбранная версия и текст, который видел владелец; подменённый или пропавший файл не уходит."""

from __future__ import annotations

import json
from pathlib import Path

from app.settings import settings
from app.store_common import PUBLISH_FILE_CHANGED_ERROR, PUBLISH_FILE_MISSING_ERROR
from app.worker import JobWorker
from test_publish_duplicates import env  # noqa: F401 - клип, аккаунт TikTok, подмена площадки


def _queue(env, clip_id=None, **extra):  # noqa: F811
    response = env["client"].post(f"/api/clips/{clip_id or env['clip']['id']}/posts",
                                  json={"title": "Мой заголовок", "description": "Моё описание #тег",
                                        "targets": [env["account"]["id"]], **extra})
    assert response.status_code == 200, response.text
    return response.json()


def test_file_changed_after_queueing_is_not_published(env):  # noqa: F811
    job = _queue(env)
    Path(env["store"].get_clip(env["clip"]["id"])["output_path"]).write_bytes(b"\x01" * 4096)
    assert JobWorker(env["store"]).process_once()
    done = env["store"].get_job(job["id"])
    assert env["provider"].calls == []
    assert done["status"] == "failed" and done["targets"][0]["error"] == PUBLISH_FILE_CHANGED_ERROR


def test_missing_file_is_not_published(env):  # noqa: F811
    job = _queue(env)
    Path(env["store"].get_clip(env["clip"]["id"])["output_path"]).unlink()
    assert JobWorker(env["store"]).process_once()
    assert env["provider"].calls == []
    assert env["store"].get_job(job["id"])["targets"][0]["error"] == PUBLISH_FILE_MISSING_ERROR


def test_the_chosen_version_and_the_owner_text_go_out(env):  # noqa: F811
    store = env["store"]
    newer = store.create_clip(env["clip"]["source_id"], clip_plan_id=env["clip"]["clip_plan_id"], status="succeeded")
    newer_path = settings.clip_dir / f"{newer['id']}.mp4"
    newer_path.write_bytes(b"\x02" * 2048)
    store.db.execute("UPDATE clips SET output_path = ? WHERE id = ?", (str(newer_path), newer["id"]))

    job = _queue(env)  # старая версия, не последняя
    assert JobWorker(store).process_once()

    call = env["provider"].calls[0]
    assert Path(call["file_path"]) == Path(store.get_clip(env["clip"]["id"])["output_path"])
    assert (call["title"], call["description"]) == ("Мой заголовок", "Моё описание #тег")
    sent = json.loads(store.get_job(job["id"])["targets"][0]["response_json"])["sent"]
    assert sent == {"title": "Мой заголовок", "description": "Моё описание #тег"}
