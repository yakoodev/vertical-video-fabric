"""Надёжность базы: WAL, чтение не ждёт долгую работу, многошаговые записи проходят целиком."""

from __future__ import annotations

import gc
import shutil
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from app import aivideo as V
from app import aivideo_gen as G
from app import autopilot as A
from app import clip_groups as CG
from app import projects as P
from app import twitch_chat_activity as CA
from app import twitch_chat_store as CS
from app.db import Database, _mount_fstype
from app.db_backup import backup
from app.settings import settings
from test_api_pipeline import _client

WAIT = 20  # предел ожиданий в тестах: при зависании падаем, а не висим


@pytest.fixture
def store(tmp_path, monkeypatch):
    _client_, store = _client(tmp_path, monkeypatch)
    return store


@pytest.fixture
def db(tmp_path):
    db = Database(tmp_path / "t.sqlite")
    db.init()
    db.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    db.execute("INSERT INTO t (v) VALUES ('old')")
    return db


def _values(db) -> list[str]:
    return [r["v"] for r in db.query_all("SELECT v FROM t ORDER BY id")]


# ---------------------------------------------------------------- режим базы


def test_wal_and_connection_settings(db):
    conn = db.connect()
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


def test_no_wal_on_host_mounted_filesystem(tmp_path, monkeypatch):
    monkeypatch.setattr("app.db._filesystem_of", lambda path: "9p")
    db = Database(tmp_path / "t.sqlite")
    db.init()
    assert db.query_one("PRAGMA journal_mode")[0] == "delete"
    assert db.query_one("PRAGMA synchronous")[0] == 2  # без WAL только полная запись на диск безопасна
    db.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    with db.transaction():
        db.execute("INSERT INTO t (v) VALUES ('a')")
    assert _values(db) == ["a"]


def test_filesystem_lookup_takes_the_longest_mount():
    table = "overlay / overlay rw 0 0\n/dev/sde /data ext4 rw 0 0\nD:\\134 /data/clips 9p rw 0 0\n"
    assert _mount_fstype("/data/app.sqlite", table) == "ext4"
    assert _mount_fstype("/data/clips/app.sqlite", table) == "9p"
    assert _mount_fstype("/database.sqlite", table) == "overlay"


def test_connections_close_at_once_and_leave_one_file(db, tmp_path):
    """Иначе соединения дожидались бы сборщика мусора (у них цикл ссылок), а журнал не сбрасывался бы в файл базы."""
    gc.collect()
    gc.disable()
    try:
        db.execute("UPDATE t SET v = 'new'")
        assert db.query_one("SELECT v FROM t")["v"] == "new"
        with db.transaction():
            db.execute("UPDATE t SET v = 'newer'")
        assert not (tmp_path / "t.sqlite-wal").exists()
    finally:
        gc.enable()


# ---------------------------------------------------------------- чтение не ждёт


def test_read_does_not_wait_for_long_write(db):
    started, release = threading.Event(), threading.Event()
    other_writer_done = threading.Event()

    def long_write():
        with db.transaction():
            db.execute("UPDATE t SET v = 'new'")
            started.set()
            assert release.wait(WAIT)

    def second_writer():
        db.execute("INSERT INTO t (v) VALUES ('second')")
        other_writer_done.set()

    writer = threading.Thread(target=long_write)
    writer.start()
    assert started.wait(WAIT)
    t0 = time.perf_counter()
    assert _values(db) == ["old"]  # не ждёт и не видит незавершённое
    assert time.perf_counter() - t0 < 1.0
    follower = threading.Thread(target=second_writer)
    follower.start()
    assert not other_writer_done.wait(0.2)  # записи идут по одной
    release.set()
    writer.join(WAIT)
    follower.join(WAIT)
    assert other_writer_done.is_set()
    assert _values(db) == ["new", "second"]


def test_chat_scoring_blocks_neither_reads_nor_writes(store, monkeypatch):
    source = store.create_source("upload", settings.source_dir / "s.mp4", duration_sec=60, status="ready")
    CS.insert_messages(store, source["id"], [{"msg_id": f"m{i}", "offset_sec": i, "text": "KEKW"} for i in range(20)])
    scoring, release = threading.Event(), threading.Event()
    real = CA.score_activity

    def slow(messages, *args, **kwargs):
        rows = list(messages)
        scoring.set()
        assert release.wait(WAIT)
        return real(rows, *args, **kwargs)

    monkeypatch.setattr(CA, "score_activity", slow)
    CA._activity_cache.clear()
    worker = threading.Thread(target=CA.chat_activity, args=(store, source["id"]))
    worker.start()
    try:
        assert scoring.wait(WAIT)
        t0 = time.perf_counter()
        assert store.get_source(source["id"])["id"] == source["id"]
        store.update_source(source["id"], status="ready")
        assert time.perf_counter() - t0 < 2.0
    finally:
        release.set()
        worker.join(WAIT)


# ---------------------------------------------------------------- транзакция


def test_transaction_rolls_back_everything_and_keeps_working(db):
    with pytest.raises(RuntimeError):
        with db.transaction():
            db.execute("UPDATE t SET v = 'new'")
            db.execute("INSERT INTO t (v) VALUES ('more')")
            assert _values(db) == ["new", "more"]  # внутри своя запись видна
            raise RuntimeError("упало")
    assert _values(db) == ["old"]
    db.execute("UPDATE t SET v = 'after'")
    assert _values(db) == ["after"]


def test_nested_failure_caught_outside_undoes_only_itself(db):
    with db.transaction():
        db.execute("INSERT INTO t (v) VALUES ('outer')")
        with pytest.raises(ValueError):
            with db.transaction():
                db.execute("INSERT INTO t (v) VALUES ('inner')")
                raise ValueError("нет")
        with db.transaction():
            db.execute("INSERT INTO t (v) VALUES ('inner2')")
    assert _values(db) == ["old", "outer", "inner2"]


def test_after_commit_runs_only_after_commit(db):
    seen = []
    with db.transaction():
        db.execute("UPDATE t SET v = 'new'")
        db.after_commit(lambda: seen.append(_values(db)))
        assert seen == []
    assert seen == [["new"]]
    db.after_commit(lambda: seen.append("сразу"))
    assert seen[-1] == "сразу"
    with pytest.raises(RuntimeError):
        with db.transaction():
            db.after_commit(lambda: seen.append("откат"))
            raise RuntimeError
    assert "откат" not in seen


# ---------------------------------------------------------------- деньги


@pytest.fixture
def paid_job(store, monkeypatch):
    project = V.create_ai_project(store, "Деньги", style_kind="anime", passport_text="p", palette=["#fff"],
                                  provider="polza")["project"]["id"]
    path = settings.data_dir / "frame.bin"
    path.write_bytes(b"x")
    asset = store.create_montage_asset(kind="image", label="кадр", file_path=str(path), project_id=project,
                                       description="", tags="", original_filename=path.name, mime_type="",
                                       size_bytes=1, width=10, height=10)
    cur = store.db.execute(
        "INSERT INTO aiv_gen_jobs (project_id, stage, kind, target_type, target_id, ref_kind, label, provider, model, "
        "estimate_rub, status, remote_id) VALUES (?, 'frames', 'image', 'shot', 1, 'frame', 'кадр', 'polza', 'm', 6, "
        "'polling', 'r1')", (project,))
    monkeypatch.setattr(G, "_paid", lambda provider, kind: True)
    monkeypatch.setattr(G, "auto_check", lambda store, job, ref: None)
    return store, G.get_job(store, cur.lastrowid), asset["id"], project


def test_done_writes_status_ledger_and_version_together(paid_job):
    store, job, asset, project = paid_job
    assert G._done(store, job, asset, "промпт", 6.0, "provider") is not None
    fresh = G.get_job(store, job["id"])
    assert fresh["status"] == "done" and fresh["result_ref_id"]
    assert [row["amount_rub"] for row in G.ledger(store, project)] == [6.0]
    # Второй «готово» трату не повторяет.
    assert G._done(store, G.get_job(store, job["id"]), asset, "промпт", 6.0, "provider", own_asset=False) is None
    assert len(G.ledger(store, project)) == 1


@pytest.mark.parametrize("broken", ["add_ledger", "add_ref"])
def test_done_failure_leaves_job_to_be_read_again(paid_job, monkeypatch, broken):
    """Упало посреди записи: нет ни «готово» без траты, ни траты без версии; запуск дочитается без нового платежа."""
    store, job, asset, project = paid_job

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    with monkeypatch.context() as patch:
        patch.setattr(G if broken == "add_ledger" else V, broken, boom)
        with pytest.raises(sqlite3.OperationalError):
            G._done(store, job, asset, "промпт", 6.0, "provider")
    fresh = G.get_job(store, job["id"])
    assert fresh["status"] == "polling" and not fresh["cost_source"]
    assert G.ledger(store, project) == []
    assert G._done(store, fresh, asset, "промпт", 6.0, "provider") is not None
    assert len(G.ledger(store, project)) == 1


def test_refused_version_does_not_cancel_the_spend(paid_job, monkeypatch):
    """Версию нельзя записать по правилам, а деньги уже ушли: трата остаётся."""
    store, job, asset, project = paid_job

    def refuse(*args, **kwargs):
        raise V.AivError("чужой файл")

    monkeypatch.setattr(V, "add_ref", refuse)
    with pytest.raises(V.AivError):
        G._done(store, job, asset, "промпт", 6.0, "provider")
    assert G.get_job(store, job["id"])["status"] == "done"
    assert len(G.ledger(store, project)) == 1


def test_uncertain_answer_writes_spend_with_its_status(paid_job, monkeypatch):
    store, job, _asset, project = paid_job
    store.db.execute("UPDATE aiv_gen_jobs SET status = 'submitting', remote_id = '' WHERE id = ?", (job["id"],))
    monkeypatch.setattr(G, "BALANCE_SETTLE_SEC", 0)
    monkeypatch.setattr(G, "_balance", lambda provider: 90.0)

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(G, "add_ledger", boom)
    with pytest.raises(sqlite3.OperationalError):
        G._mark_uncertain(store, G.get_job(store, job["id"]), TimeoutError("нет ответа"), object(), 100.0)
    assert G.get_job(store, job["id"])["status"] == "submitting"


# ---------------------------------------------------------------- решения


def _card(store, action: str) -> dict:
    project = P.create_project(store, "Решения", kind="other")["id"]
    return A.submit_card(store, project, "other", "вопрос?", {"action": action})


def test_decision_rolls_back_when_atomic_hook_fails(store, monkeypatch):
    calls = []

    def hook(store_, approval, action, comment, actor):
        store_.db.execute("UPDATE approvals SET decision_comment = 'из обработчика' WHERE id = ?", (approval["id"],))
        calls.append(action)
        raise RuntimeError("упал посреди")

    monkeypatch.setitem(A.CARD_ACTIONS, "test_atomic", {"buttons": [], "on_decide": hook, "atomic": True})
    card = _card(store, "test_atomic")
    with pytest.raises(RuntimeError):
        A.decide(store, card["id"], "approve")
    fresh = A.get_approval(store, card["id"])
    assert calls == ["approve"] and fresh["status"] == "pending" and not fresh["decision_comment"]
    assert [e["event"] for e in A.list_events(store) if e["approval_id"] == card["id"]] == ["submitted"]
    # Карточку можно решить снова.
    monkeypatch.setitem(A.CARD_ACTIONS, "test_atomic", {"buttons": [], "on_decide": lambda *a, **k: {}, "atomic": True})
    assert A.decide(store, card["id"], "approve")["status"] == "approved"


def test_slow_hook_runs_after_decision_is_committed(store, monkeypatch):
    """Обработчики с сетью (скачать VOD, тема объяснялки) не держат запись: решение уже видно всем."""
    seen = []

    def hook(store_, approval, action, comment, actor):
        result = []
        thread = threading.Thread(target=lambda: result.append(A.get_approval(store_, approval["id"])["status"]))
        thread.start()
        thread.join(WAIT)
        seen.extend(result)
        return {}

    monkeypatch.setitem(A.CARD_ACTIONS, "test_slow", {"buttons": [], "on_decide": hook, "atomic": False})
    card = _card(store, "test_slow")
    A.decide(store, card["id"], "approve")
    assert seen == ["approved"]


def test_aivideo_worker_is_woken_after_commit(paid_job, monkeypatch):
    store, job, _asset, project = paid_job
    card = A.submit_card(store, project, "other", "платить?", {"action": G.CARD_ACTION})
    store.db.execute("UPDATE aiv_gen_jobs SET status = 'awaiting', approval_id = ? WHERE id = ?", (card["id"], job["id"]))
    seen = []

    def kick():
        result = []
        thread = threading.Thread(target=lambda: result.append(G.get_job(store, job["id"])["status"]))
        thread.start()
        thread.join(WAIT)
        seen.extend(result)

    monkeypatch.setattr(G, "kick", kick)
    A.decide(store, card["id"], "approve")
    assert seen == ["approved"]  # другой поток уже видит одобренный запуск


# ---------------------------------------------------------------- удаление и версии


def _source_with_clip(store) -> dict:
    src = settings.source_dir / "s.mp4"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"src")
    source = store.create_source("upload", src, duration_sec=60, width=1080, height=1920, status="ready")
    analysis = store.create_ai_analysis(source["id"], "manual", status="succeeded")
    seg = store.create_ai_segment(analysis["id"], {"start_sec": 0, "end_sec": 40, "title": "A"})
    plan = store.create_clip_plan(source["id"], analysis["id"], "Клип", segment_ids=[seg["id"]])
    out = settings.clip_dir / "out.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"clip")
    clip = store.create_clip(source["id"], clip_plan_id=plan["id"])
    clip = store.finish_clip_render(clip["id"], status="succeeded", output_path=out, preview_path=out)
    account = store.upsert_account("youtube", "a", "SID=a; HSID=b; SSID=c; APISID=d; SAPISID=e")
    job = store.create_clip_post_job(clip["id"], "T", "D", [account["id"]], "public", True)
    return {"source": source, "plan": plan, "clip": clip, "job": job, "src": src, "out": out}


def _fail_delete(*args, **kwargs):
    raise sqlite3.OperationalError("disk I/O error")


def test_delete_source_failure_keeps_rows_and_files(store, monkeypatch):
    world = _source_with_clip(store)
    with monkeypatch.context() as patch:
        patch.setattr(store, "_delete_record", _fail_delete)
        with pytest.raises(sqlite3.OperationalError):
            store.delete_source(world["source"]["id"])
    assert store.get_job(world["job"]["id"])["clip_id"] == world["clip"]["id"]  # отвязка откатилась
    assert world["src"].exists() and world["out"].exists()
    assert store.delete_source(world["source"]["id"])["deleted"]
    assert not world["src"].exists() and store.get_job(world["job"]["id"])["clip_id"] is None


def test_delete_clip_failure_keeps_job_link(store, monkeypatch):
    world = _source_with_clip(store)
    monkeypatch.setattr(store, "_delete_record", _fail_delete)
    with pytest.raises(sqlite3.OperationalError):
        store.delete_clip(world["clip"]["id"])
    assert store.get_job(world["job"]["id"])["clip_id"] == world["clip"]["id"]


def test_delete_plan_failure_keeps_history(store, monkeypatch):
    world = _source_with_clip(store)
    plan_id = world["plan"]["id"]
    store.push_clip_plan_history(plan_id, {"spec": {}}, "правка", depth=20)
    monkeypatch.setattr(store, "_delete_record", _fail_delete)
    with pytest.raises(sqlite3.OperationalError):
        store.delete_clip_plan(plan_id)
    assert store.count_clip_plan_history(plan_id) == 1


def test_new_version_failure_leaves_no_orphan_plan(store, monkeypatch):
    world = _source_with_clip(store)
    before = store.db.query_one("SELECT COUNT(*) AS n FROM clip_plans")["n"]

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(CG, "attach_new_plan", boom)
    with pytest.raises(sqlite3.OperationalError):
        CG.new_version(store, world["plan"]["id"])
    assert store.db.query_one("SELECT COUNT(*) AS n FROM clip_plans")["n"] == before


# ---------------------------------------------------------------- копия базы


def test_backup_sees_commits_that_a_plain_copy_misses(db, tmp_path):
    """Пока открыто другое соединение, свежие записи лежат только в -wal: cp их теряет, backup — нет."""
    holder = db.connect()
    holder.execute("SELECT 1 FROM t").fetchone()
    db.execute("INSERT INTO t (v) VALUES ('fresh')")
    plain = tmp_path / "plain.sqlite"
    shutil.copyfile(tmp_path / "t.sqlite", plain)
    conn = sqlite3.connect(plain)
    assert [r[0] for r in conn.execute("SELECT v FROM t")] == ["old"]  # свежая строка потеряна молча
    conn.close()

    dest = tmp_path / "bak" / "app.bak"
    dest.parent.mkdir()
    out = backup(tmp_path / "t.sqlite", dest)
    holder.close()
    assert out["quick_check"] == "ok" and not Path(str(dest) + ".tmp").exists()
    conn = sqlite3.connect(dest)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert [r[0] for r in conn.execute("SELECT v FROM t ORDER BY id")] == ["old", "fresh"]
    conn.close()
    assert sorted(p.name for p in dest.parent.iterdir()) == ["app.bak"]  # один файл


def test_backup_refuses_to_overwrite(db, tmp_path):
    dest = tmp_path / "app.bak"
    dest.write_bytes(b"prev")
    with pytest.raises(FileExistsError):
        backup(tmp_path / "t.sqlite", dest)
    assert dest.read_bytes() == b"prev"
