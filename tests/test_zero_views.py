"""Тревога «0 просмотров» (app/zero_views.py): через N часов после публикации без просмотров — карточка
«внимание» с причинами и чек-листом, Telegram (замокан), снятие тревоги, значки «Статистика». Числа —
подделка fetcher'а, никакой сети."""

from __future__ import annotations

import pytest

from app import autopilot as A
from app import notify_tg
from app import projects as P
from app import zero_views as Z
from app.settings import settings
from test_api_pipeline import _client

TT = "https://www.tiktok.com/@/video/7300000000000000001"
TT2 = "https://www.tiktok.com/@/video/7300000000000000002"
YT = "https://www.youtube.com/shorts/abc123"


@pytest.fixture
def env(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    monkeypatch.delenv("AUTOPILOT_TG_TOKEN", raising=False)
    for name in ("ZERO_VIEWS_ALERT", "ZERO_VIEWS_HOURS", "ZERO_VIEWS_THRESHOLD", "ZERO_VIEWS_MAX_AGE_HOURS"):
        monkeypatch.delenv(name, raising=False)
    return client, store


def _post(store, hours_ago: float, urls=(TT,), original_url: str = "", title: str = "Сова про сон",
          privacy: str = "public", project: bool = True):
    source = store.create_source("upload", settings.source_dir / f"src-{len(urls)}-{hours_ago}.mp4",
                                 original_url=original_url, duration_sec=60, status="ready")
    if project:
        P.create_project(store, "Сова", kind="explainer", source_ids=[source["id"]])
    analysis = store.create_ai_analysis(source["id"], "mock", status="succeeded")
    seg = store.create_ai_segment(analysis["id"], {"start_sec": 0, "end_sec": 10, "title": title})
    plan = store.create_clip_plan(source["id"], analysis["id"], title, segment_ids=[seg["id"]])
    clip = store.create_clip(source["id"], clip_plan_id=plan["id"], status="succeeded")
    job_id = store.db.execute(
        "INSERT INTO jobs (clip_id, title, status, privacy, source_filename, source_path, source_sha256, "
        "source_size_bytes) VALUES (?, ?, 'succeeded', ?, 'c.mp4', '/x/c.mp4', '', 0)",
        (clip["id"], title, privacy)).lastrowid
    targets = []
    for n, url in enumerate(urls):
        platform = "youtube" if "youtube" in url else "tiktok"
        account = store.upsert_account(platform, f"{platform}-{n}", "sessionid=abc; tt-target-idc=x"
                                       if platform == "tiktok" else "SID=a; HSID=b; SSID=c; APISID=d; SAPISID=e")
        targets.append(store.db.execute(
            "INSERT INTO job_targets (job_id, account_id, platform, status, remote_id, remote_url, finished_at) "
            "VALUES (?, ?, ?, 'succeeded', 'x', ?, datetime('now', ?))",
            (job_id, account["id"], platform, url, f"-{int(hours_ago * 60)} minutes")).lastrowid)
    return plan, clip, targets


def _fetcher(counts: dict, calls: list):
    def fetch(store, target, ig):
        calls.append(target["remote_url"])
        if counts.get(target["remote_url"]) is None:
            raise RuntimeError("yt-dlp: unavailable")
        return {"views": counts[target["remote_url"]], "likes": 0, "comments": 0, "shares": 0}

    return fetch


def test_zero_views_after_two_hours_raises_alert_with_reasons(env):
    _c, store = env
    plan, clip, (tt_a, tt_b) = _post(store, 3, urls=(TT, TT2), original_url="https://www.twitch.tv/videos/1")
    _post(store, 0.5, urls=(YT,), title="Свежий")  # ещё не прошло 2 ч — не трогаем
    calls: list = []
    out = Z.check(store, fetcher=_fetcher({TT: 0, TT2: 5}, calls), sleep=lambda s: None)
    assert sorted(calls) == [TT, TT2]  # свежие снимки сняты сейчас, свежий пост — нет
    assert len(out["alerts"]) == 1 and out["alerts"][0]["target_id"] == tt_a
    alert = A.get_approval(store, out["alerts"][0]["approval_id"])
    assert alert["kind"] == "alert" and alert["kind_label"] == "внимание" and alert["status"] == "pending"
    assert alert["clip_plan_id"] == plan["id"] and alert["thumb_url"]
    assert alert["project_id"] == P.project_of_plan(store, plan["id"])
    assert [b["action"] for b in alert["buttons"]] == ["approve", "reject"]
    q = alert["question"]
    assert "0 просмотров через 2 ч" in q and "Не подходит для рекомендаций" in q and "Account Check" in q
    assert "одним заданием" in q  # тот же файл на 2 аккаунта TikTok
    assert "чужого видео" in q
    # Повторный проход: тревога одна на публикацию, за числами второй раз не ходим.
    calls.clear()
    again = Z.check(store, fetcher=_fetcher({TT: 0, TT2: 5}, calls), sleep=lambda s: None)
    assert not again["alerts"] and not calls
    # «Проверил» — тревога закрыта.
    A.decide(store, alert["id"], "approve")
    assert store.db.query_one("SELECT status FROM post_alerts WHERE target_id = ?", (tt_a,))["status"] == "checked"


def test_threshold_setting_failed_fetch_and_resolution(env):
    _c, store = env
    _plan, _clip, (target,) = _post(store, 5, project=False)
    Z.save_settings(store, {"hours": 4, "threshold": 10})
    assert Z.get_settings(store)["hours"] == 4
    calls: list = []
    # Площадка не ответила — тревоги нет, и до обычного сбора снова не ходим.
    out = Z.check(store, fetcher=_fetcher({}, calls), sleep=lambda s: None)
    assert not out["alerts"] and len(calls) == 1
    Z.check(store, fetcher=_fetcher({}, calls), sleep=lambda s: None)
    assert len(calls) == 1
    # Обычный сбор принёс 7 просмотров (< порога 10) — тревога; карточка без проекта.
    store.add_publication_stats(target, "tiktok", {"views": 7})
    out = Z.check(store, fetcher=_fetcher({}, calls), sleep=lambda s: None)
    assert len(out["alerts"]) == 1
    card = A.get_approval(store, out["alerts"][0]["approval_id"])
    assert card["project_id"] is None and "порог 10" in card["question"]
    badges = Z.clip_badges(store)
    assert badges[_clip["id"]]["alert"]["views"] == 7 and badges[_clip["id"]]["views"] == 7
    # Просмотры пошли — тревога прошла сама, карточка закрылась.
    store.add_publication_stats(target, "tiktok", {"views": 250})
    res = Z.check(store, fetcher=_fetcher({}, calls), sleep=lambda s: None)
    assert res["resolved"] and A.get_approval(store, card["id"])["status"] == "expired"
    assert Z.clip_badges(store)[_clip["id"]]["alert"] is None
    with pytest.raises(ValueError):
        Z.save_settings(store, {"hours": 0})


def test_disabled_and_old_posts_ignored(env):
    _c, store = env
    _post(store, 200)  # старше max_age_hours (72) — при включении не будим
    calls: list = []
    assert not Z.check(store, fetcher=_fetcher({TT: 0}, calls))["alerts"] and not calls
    _post(store, 3, urls=(TT2,))
    Z.save_settings(store, {"enabled": False})
    assert not Z.check(store, fetcher=_fetcher({TT2: 0}, calls))["alerts"] and not calls


def test_alert_goes_to_telegram(env, monkeypatch):
    _c, store = env
    sent: list = []
    monkeypatch.setattr(notify_tg, "is_enabled", lambda s: True)
    monkeypatch.setattr(notify_tg, "send_card", lambda s, aid: sent.append(aid) or {"ok": True})
    _post(store, 3)
    out = Z.check(store, fetcher=_fetcher({TT: 0}, []), sleep=lambda s: None)
    assert sent == [out["alerts"][0]["approval_id"]] and out["alerts"][0]["telegram"] is True


def test_api_settings_and_badges(env):
    client, store = env
    _plan, clip, (target,) = _post(store, 3)
    Z.check(store, fetcher=_fetcher({TT: 0}, []), sleep=lambda s: None)
    r = client.get("/api/stats/publications")
    assert r.status_code == 200
    body = r.json()
    assert body["alerts"][str(target)]["views"] == 0 and body["clips"][str(clip["id"])]["alert"]["views"] == 0
    r = client.put("/api/stats/zero-views", json={"hours": 3, "threshold": 2})
    assert r.status_code == 200 and r.json()["hours"] == 3
    assert client.get("/api/stats/zero-views").json()["threshold"] == 2
    assert client.put("/api/stats/zero-views", json={"hours": -1}).status_code == 400
