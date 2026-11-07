"""Один ролик не выходит на одном аккаунте дважды: двойной клик, повтор после «могло выйти», перезапуск, гонка потоков.

Сеть не нужна: площадка — подмена с записью вызовов."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import autopilot as A
from app import publish_master
from app import projects as P
from app.providers.base import ProviderResult
from app.settings import settings
from app.store import RepostNeedsConfirm
from app.worker import JobWorker
from test_api_pipeline import _client

COOKIE_TT = "sessionid=abc; tt-target-idc=useast2a"


class RecordingProvider:
    def __init__(self, result: ProviderResult | None = None) -> None:
        self.calls: list[dict] = []
        self.result = result or ProviderResult(status="succeeded", remote_id="1", remote_url="https://www.tiktok.com/@/video/7431234567890123456")

    def upload(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


@pytest.fixture
def env(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    source = store.create_source("upload", settings.source_dir / "s.mp4", original_filename="s.mp4", duration_sec=60,
                                 status="ready")
    analysis = store.create_ai_analysis(source["id"], "manual", status="succeeded")
    segment = store.create_ai_segment(analysis["id"], {"start_sec": 0, "end_sec": 8, "title": "момент"})
    plan = store.create_clip_plan(source["id"], analysis["id"], "Момент", segment_ids=[segment["id"]])
    clip = store.create_clip(source["id"], clip_plan_id=plan["id"], status="succeeded")
    out = settings.clip_dir / f"{clip['id']}.mp4"
    out.write_bytes(b"\x00" * 2048)
    store.db.execute("UPDATE clips SET output_path = ? WHERE id = ?", (str(out), clip["id"]))
    account = store.upsert_account("tiktok", "tt-main", COOKIE_TT)
    provider = RecordingProvider()
    monkeypatch.setattr("app.worker.get_provider", lambda platform: provider)
    monkeypatch.setattr(publish_master, "prepare", lambda path: publish_master.MasterResult(Path(path)))
    return {"client": client, "store": store, "clip": clip, "account": account, "provider": provider,
            "source": source, "plan": plan}


def _post(env, **extra):
    return env["client"].post(f"/api/clips/{env['clip']['id']}/posts",
                              json={"title": "Заголовок", "targets": [env["account"]["id"]], **extra})


def test_double_click_queues_one_job(env):
    first = _post(env)
    second = _post(env)
    assert first.status_code == 200
    assert second.status_code == 400 and "уже в очереди" in second.json()["detail"]
    assert len(env["store"].list_jobs()) == 1


def test_same_account_twice_in_one_request_gets_one_upload(env):
    response = _post(env, targets=[env["account"]["id"], env["account"]["id"]])
    assert response.status_code == 200
    assert len(response.json()["targets"]) == 1
    assert JobWorker(env["store"]).process_once()
    assert len(env["provider"].calls) == 1


def test_published_clip_needs_explicit_yes_to_go_out_again(env):
    assert _post(env).status_code == 200
    assert JobWorker(env["store"]).process_once()
    again = _post(env)
    assert again.status_code == 409 and "уже опубликован" in again.json()["detail"]
    confirmed = _post(env, allow_repost=True)
    assert confirmed.status_code == 200


def test_maybe_posted_blocks_a_silent_retry(env):
    env["provider"].result = ProviderResult(status="failed", maybe_posted=True, error="Связь оборвалась на самом посте")
    assert _post(env).status_code == 200
    assert JobWorker(env["store"]).process_once()
    job = env["store"].list_jobs()[0]
    assert job["targets"][0]["maybe_posted"] == 1
    assert job["error"] == "Связь оборвалась на самом посте"  # настоящая причина, а не «one or more targets failed»
    again = _post(env)
    assert again.status_code == 409 and "мог уже выйти" in again.json()["detail"]


def test_restart_mid_post_blocks_a_silent_retry(env):
    store = env["store"]
    job = store.create_clip_post_job(env["clip"]["id"], "", "", [env["account"]["id"]], "public", True)
    store.mark_job_running(job["id"])
    store.mark_target_running(job["targets"][0]["id"])
    store.recover_after_restart()
    with pytest.raises(RepostNeedsConfirm):
        store.create_clip_post_job(env["clip"]["id"], "", "", [env["account"]["id"]], "public", True)


def test_certain_failure_can_be_retried_at_once(env):
    env["provider"].result = ProviderResult(status="failed", error="Нет связи через прокси: ролик не отправлен")
    assert _post(env).status_code == 200
    assert JobWorker(env["store"]).process_once()
    assert _post(env).status_code == 200


def test_cancelled_job_does_not_block_and_cancel_is_owner_only(env):
    job = _post(env, scheduled_at="2099-01-01 10:00").json()
    agent = env["client"].post(f"/api/jobs/{job['id']}/cancel", headers={"Authorization": "Bearer t"})
    assert agent.status_code == 403
    cancelled = env["client"].post(f"/api/jobs/{job['id']}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["targets"][0]["status"] == "cancelled"
    assert env["client"].post(f"/api/jobs/{job['id']}/cancel").status_code == 409
    assert _post(env).status_code == 200


def test_worker_never_takes_a_job_someone_else_took(env):
    store = env["store"]
    job = store.create_clip_post_job(env["clip"]["id"], "", "", [env["account"]["id"]], "public", True)
    queued = store.next_queued_job()
    store.mark_job_running(job["id"])  # другой поток успел первым
    store.next_queued_job = lambda: queued
    assert JobWorker(store).process_once() is False
    assert env["provider"].calls == []


def test_target_with_a_result_is_not_uploaded_again(env):
    store = env["store"]
    job = store.create_clip_post_job(env["clip"]["id"], "", "", [env["account"]["id"]], "public", True)
    target = job["targets"][0]
    store.finish_target(target["id"], "succeeded", {"remote_url": "https://www.tiktok.com/@/video/1"})
    assert JobWorker(store).process_once()
    assert env["provider"].calls == []


def test_reschedule_never_leaves_two_jobs_when_the_worker_took_the_old_one(env, monkeypatch):
    store, plan = env["store"], env["plan"]
    monkeypatch.setattr(A, "now", lambda: datetime(2026, 11, 4, 16, 0, tzinfo=timezone.utc))
    pid = P.create_project(store, "Канал", kind="stream_channel", source_ids=[env["source"]["id"]])["id"]
    A.update_config(store, pid, {"platforms": ["tiktok"], "accounts": {"tiktok": [env["account"]["id"]]}})
    card = A.submit_clip(store, plan["id"], title="Момент")
    placed = A.decide(store, card["id"], "approve", actor="ui")["placement"]
    assert placed["ok"]
    store.mark_job_running(placed["job_id"])  # работник взял задание между проверкой и переносом
    result = A._place(store, card["id"], actor="ui", use_proposed=False)
    assert not result["ok"] and "уже идёт" in result["error"]
    assert [j["id"] for j in store.list_jobs()] == [placed["job_id"]]
