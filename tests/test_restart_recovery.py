"""Перезапуск посреди работы: зависшее становится ошибкой с понятной причиной, живое и чужое не трогается."""

from __future__ import annotations

import asyncio

from app import main as app_main
from app import render_queue
from app.store import PUBLISH_INTERRUPTED_ERROR, PUBLISH_NOT_STARTED_ERROR, RENDER_INTERRUPTED_ERROR
from test_data_foundation import _store


def _dump(store) -> dict:
    """Все строки затронутых таблиц: что изменилось, видно целиком."""
    tables = ("clips", "clip_plans", "ai_segments", "ai_analyses", "sources", "subtitle_tracks", "jobs", "job_targets",
              "render3d_jobs")
    return {t: [dict(r) for r in store.db.query_all(f"SELECT * FROM {t} ORDER BY id")] for t in tables}


def _source(store, status="ready"):
    return store.create_source("upload", "s.mp4", original_filename="s.mp4", duration_sec=60, status=status)


def _plan(store, source, start=0):
    analysis = store.create_ai_analysis(source["id"], "manual", status="succeeded")
    segment = store.create_ai_segment(analysis["id"], {"start_sec": start, "end_sec": start + 6, "title": "м"})
    return store.create_clip_plan(source["id"], analysis["id"], f"план {start}", segment_ids=[segment["id"]]), segment


def _job(store, status, targets):
    cur = store.db.execute(
        "INSERT INTO jobs (status, title, source_filename, source_path, source_sha256, source_size_bytes) "
        "VALUES (?, 'клип', 'a.mp4', '/x/a.mp4', 'h', 1)", (status,))
    job_id = int(cur.lastrowid)
    for i, target in enumerate(targets):
        account = store.db.execute(
            "INSERT INTO accounts (platform, label, encrypted_cookies) VALUES ('youtube', ?, '')", (f"a{job_id}-{i}",))
        store.db.execute(
            "INSERT INTO job_targets (job_id, account_id, platform, status, remote_url) VALUES (?, ?, 'youtube', ?, ?)",
            (job_id, int(account.lastrowid), target, "https://y/1" if target == "succeeded" else ""))
    return job_id


def _targets(store, job_id):
    return [dict(r) for r in store.db.query_all("SELECT * FROM job_targets WHERE job_id = ? ORDER BY id", (job_id,))]


def test_stuck_renders_fail_with_reason_and_plans_return(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    source = _source(store)
    fresh_plan, fresh_segment = _plan(store, source, 0)
    done_plan, _ = _plan(store, source, 10)
    calm_plan, _ = _plan(store, source, 20)

    rendering = store.create_clip(source["id"], clip_plan_id=fresh_plan["id"])
    store.mark_clip_rendering(rendering["id"])
    queued = store.create_clip(source["id"], clip_plan_id=fresh_plan["id"])
    orphan = store.create_clip(source["id"])  # план удалён: у клипа 336 в рабочей базе так же
    store.mark_clip_rendering(orphan["id"])
    earlier = store.create_clip(source["id"], clip_plan_id=done_plan["id"], status="succeeded")
    second = store.create_clip(source["id"], clip_plan_id=done_plan["id"])
    store.mark_clip_rendering(second["id"])
    other_failed = store.create_clip(source["id"], clip_plan_id=calm_plan["id"], status="failed")
    track = store.create_subtitle_track(rendering["id"], provider="mock", status="running")

    store.update_clip_plan_status(fresh_plan["id"], "rendering")
    store.update_clip_plan_status(done_plan["id"], "rendering")
    store.update_ai_segment_status(fresh_segment["id"], "rendering")

    before = _dump(store)
    report = store.recover_after_restart()
    assert report["renders"] == {"clips": 4, "subtitle_tracks": 1, "plans": 2, "segments": 1}

    for clip_id in (rendering["id"], queued["id"], orphan["id"], second["id"]):
        clip = store.get_clip(clip_id)
        assert clip["status"] == "failed" and clip["error"] == RENDER_INTERRUPTED_ERROR and clip["finished_at"]
    assert store.get_clip(earlier["id"])["status"] == "succeeded"
    assert store.get_clip(other_failed["id"])["error"] == ""
    assert store.get_subtitle_track(track["id"])["status"] == "failed"
    assert store.get_clip_plan(fresh_plan["id"])["status"] == "failed"
    assert store.get_clip_plan(done_plan["id"])["status"] == "rendered"  # файл прошлого рендера на месте
    assert store.get_clip_plan(calm_plan["id"])["status"] == "candidate"
    assert store.get_ai_segment(fresh_segment["id"])["status"] == "candidate"

    # Ничего не удалено и лишнего не тронуто.
    after = _dump(store)
    assert {t: len(rows) for t, rows in after.items()} == {t: len(rows) for t, rows in before.items()}
    changed = {(t, a["id"]) for t in after for a, b in zip(after[t], before[t], strict=True) if a != b}
    assert changed == {("clips", rendering["id"]), ("clips", queued["id"]), ("clips", orphan["id"]), ("clips", second["id"]),
                       ("subtitle_tracks", track["id"]), ("clip_plans", fresh_plan["id"]), ("clip_plans", done_plan["id"]),
                       ("ai_segments", fresh_segment["id"])}

    # Повтор — ни одной правки, даже updated_at.
    assert not any(store.recover_after_restart().values())
    assert _dump(store) == after


def test_interrupted_publication_fails_and_is_never_requeued(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    mid = _job(store, "running", ["succeeded", "running", "queued"])
    untouched_start = _job(store, "running", ["queued"])
    all_done = _job(store, "running", ["succeeded"])  # площадки закончили, не успели записать итог
    scheduled = _job(store, "queued", ["queued"])
    finished = _job(store, "failed", ["failed"])
    before_scheduled, before_finished = store.get_job(scheduled), store.get_job(finished)

    report = store.recover_after_restart()
    assert report["publications"] == {"jobs": 3, "targets": 1}

    job = store.get_job(mid)
    assert job["status"] == "failed" and job["error"] == PUBLISH_INTERRUPTED_ERROR
    first, running, waiting = _targets(store, mid)
    assert first["status"] == "succeeded" and first["remote_url"] == "https://y/1"
    assert running["status"] == "failed" and running["error"] == PUBLISH_INTERRUPTED_ERROR
    assert waiting["status"] == "failed" and waiting["error"] == PUBLISH_NOT_STARTED_ERROR

    assert store.get_job(untouched_start)["status"] == "failed"
    assert store.get_job(all_done)["status"] == "succeeded"
    assert store.get_job(scheduled) == before_scheduled  # запланированная уйдёт как обычно
    assert store.get_job(finished) == before_finished
    # Работник берёт только запланированную: повторной отправки прерванных нет.
    assert store.next_queued_job()["id"] == scheduled

    snapshot = _dump(store)
    assert not any(store.recover_after_restart().values())
    assert _dump(store) == snapshot


def test_host_worker_3d_job_survives_restart(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    render_queue.enqueue(store, {"a": 1}, key="k1")
    queued = render_queue.enqueue(store, {"a": 2}, key="k2")
    job = render_queue.claim(store, "pc")
    before = _dump(store)["render3d_jobs"]

    store.recover_after_restart()

    assert _dump(store)["render3d_jobs"] == before
    # Воркер на компьютере продолжает то же задание, второе ждёт в очереди.
    assert render_queue.heartbeat(store, job["id"], "pc", 0.5) == {"status": "running", "stop": False}
    assert render_queue.get(store, queued["id"])["status"] == "queued"


def test_stuck_analysis_states(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    cancelling_source, queued_source, lost_source = (_source(store, status="analyzing") for _ in range(3))
    running = store.create_ai_analysis(cancelling_source["id"], "mock", status="queued")
    store.request_ai_analysis_cancel(running["id"])
    queued = store.create_ai_analysis(queued_source["id"], "mock", status="queued")

    assert store.recover_after_restart()["analyses"] == 2

    assert store.get_ai_analysis(running["id"])["status"] == "cancelled"
    assert store.get_ai_analysis(queued["id"])["status"] == "failed"
    for source in (cancelling_source, queued_source, lost_source):
        assert store.get_source(source["id"])["status"] == "ready"
    assert not any(store.recover_after_restart().values())


def test_failed_step_blocks_neither_other_steps_nor_startup(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    clip = store.create_clip(_source(store)["id"])
    store.mark_clip_rendering(clip["id"])

    def boom():
        raise RuntimeError("база занята")

    monkeypatch.setattr(store, "recover_interrupted_ai_analyses", boom)
    report = store.recover_after_restart()
    assert report["analyses"] is None and report["renders"]["clips"] == 1

    monkeypatch.setattr(store, "recover_interrupted_renders", boom)
    monkeypatch.setattr(store, "recover_interrupted_publications", boom)
    monkeypatch.setattr(app_main, "store", store)
    started = []
    monkeypatch.setattr(app_main.modules, "start_workers", lambda s: started.append(s))
    monkeypatch.setattr(app_main.modules, "stop_workers", lambda: None)

    async def boot():
        async with app_main.lifespan(app_main.app):
            pass

    asyncio.run(boot())
    assert started == [store]
