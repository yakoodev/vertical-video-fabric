"""Статистика площадок: сбор (сеть замокана), сводка «что работает», REST и MCP."""

from __future__ import annotations

import json
import subprocess

from app import platform_stats
from app.agent_tools import ToolContext, run_tool
from app.settings import settings
from test_api_pipeline import _client

YT = "https://www.youtube.com/shorts/abc123"
TT = "https://www.tiktok.com/@me/video/7300000000000000000"
IG = "https://www.instagram.com/reel/Cxyz/"


def _published(store, render_settings: dict | None = None, keyed: bool = False):
    """Клип с двумя кусками и публикация на четыре площадки (последняя — mock-ссылка)."""
    src_path = settings.source_dir / "stats-src.mp4"
    source = store.create_source("upload", src_path, duration_sec=60, status="ready")
    if keyed:
        store.update_source(source["id"], chroma_key={"color": "#00ff00"})
    analysis = store.create_ai_analysis(source["id"], "mock", status="succeeded")
    a = store.create_ai_segment(analysis["id"], {"start_sec": 0, "end_sec": 10, "title": "A"})
    b = store.create_ai_segment(analysis["id"], {"start_sec": 20, "end_sec": 28, "title": "B"})
    plan = store.create_clip_plan(source["id"], analysis["id"], "Смешной момент", segment_ids=[a["id"], b["id"]])
    if render_settings:
        store.set_clip_plan_render_settings(plan["id"], render_settings)
    clip = store.create_clip(source["id"], clip_plan_id=plan["id"], status="succeeded")
    yt = store.upsert_account("youtube", f"yt-{plan['id']}", "SID=a; HSID=b; SSID=c; APISID=d; SAPISID=e")
    tt = store.upsert_account("tiktok", f"tt-{plan['id']}", "sessionid=abc; tt-target-idc=useast2a")
    ig = store.upsert_account("instagram", f"ig-{plan['id']}", "csrftoken=x")  # без sessionid
    cur = store.db.execute(
        "INSERT INTO jobs (clip_id, title, status, source_filename, source_path, source_sha256, source_size_bytes) "
        "VALUES (?, 'Пост', 'succeeded', 'c.mp4', '/x/c.mp4', '', 0)",
        (clip["id"],),
    )
    job_id = cur.lastrowid
    targets = {}
    for account, url in ((yt, YT), (tt, TT), (ig, IG), (yt, "https://example.test/youtube/mock-1")):
        tcur = store.db.execute(
            "INSERT INTO job_targets (job_id, account_id, platform, status, remote_id, remote_url, finished_at) "
            "VALUES (?, ?, ?, 'succeeded', 'Cxyz', ?, datetime('now', '-2 days'))",
            (job_id, account["id"], account["platform"], url),
        )
        targets[url] = tcur.lastrowid
    # Неудачная публикация в сбор не попадает.
    store.db.execute(
        "INSERT INTO job_targets (job_id, account_id, platform, status, remote_url) VALUES (?, ?, 'youtube', 'failed', '')",
        (job_id, yt["id"]),
    )
    return plan, targets


def _fake_ytdlp(monkeypatch, counts: dict[str, dict], calls: list):
    def fake_run(args, **kw):
        assert args[0] == "yt-dlp", args
        calls.append(args)
        url = args[-1]
        if url not in counts:
            return subprocess.CompletedProcess(args, 1, "", "ERROR: Video unavailable\n")
        return subprocess.CompletedProcess(args, 0, json.dumps({"id": "x", **counts[url]}) + "\n", "")

    monkeypatch.setattr(platform_stats.subprocess, "run", fake_run)


def test_ytdlp_stats_args_and_parse(monkeypatch):
    calls: list = []
    _fake_ytdlp(monkeypatch, {TT: {"view_count": 1500, "like_count": 120, "comment_count": 7, "repost_count": 3}},
                calls)
    monkeypatch.setattr("app.proxy_config.download_proxy", lambda: "")
    assert platform_stats.fetch_ytdlp_stats(TT) == {"views": 1500, "likes": 120, "comments": 7, "shares": 3}
    for flag in ("--dump-json", "--skip-download", "--no-playlist"):
        assert flag in calls[0]
    try:
        platform_stats.fetch_ytdlp_stats(YT)
    except RuntimeError as exc:
        assert "unavailable" in str(exc)
    else:
        raise AssertionError("ошибка yt-dlp — исключение")
    assert platform_stats.platform_of_url("https://youtu.be/x") == "youtube"
    assert platform_stats.platform_of_url("https://example.test/x") == ""


def test_ytdlp_retries_through_download_proxy(monkeypatch):
    calls: list = []

    def fake_run(args, **kw):
        calls.append((args, kw.get("env")))
        if not kw.get("env"):
            return subprocess.CompletedProcess(args, 1, "", "ERROR: blocked\n")
        return subprocess.CompletedProcess(args, 0, json.dumps({"view_count": 5}), "")

    monkeypatch.setattr(platform_stats.subprocess, "run", fake_run)
    monkeypatch.setattr("app.proxy_config.download_proxy", lambda: "http://u:secret@proxy:1")
    assert platform_stats.fetch_ytdlp_stats(YT)["views"] == 5
    # Прокси — окружением процесса, не argv (креды видно в ps).
    assert len(calls) == 2 and calls[1][1]["HTTPS_PROXY"] == "http://u:secret@proxy:1"
    assert not any("secret" in arg or arg == "--proxy" for args, _env in calls for arg in args)


def test_collect_one_by_one_with_pause_and_skips(tmp_path, monkeypatch):
    _c, store = _client(tmp_path, monkeypatch)
    monkeypatch.setattr("app.proxy_config.download_proxy", lambda: "")
    _plan, targets = _published(store)
    calls: list = []
    _fake_ytdlp(monkeypatch, {YT: {"view_count": 1000, "like_count": 50, "comment_count": 4},
                              TT: {"view_count": 300, "like_count": 30, "comment_count": 2}}, calls)
    pauses: list = []
    result = platform_stats.collect_stats(store, sleep=pauses.append, pause=8)
    assert result["checked"] == 4 and result["ok"] == 2 and result["skipped"] == 2 and result["failed"] == 0
    assert len(calls) == 2  # mock-ссылка и Instagram без сессии — без сети
    assert pauses and all(p == 8 for p in pauses)

    latest = store.latest_publication_stats()
    assert latest[targets[YT]]["views"] == 1000 and latest[targets[YT]]["error"] == ""
    assert latest[targets[TT]]["likes"] == 30
    assert "sessionid" in latest[targets[IG]]["error"] and latest[targets[IG]]["views"] is None
    assert "не поддерживается" in latest[targets["https://example.test/youtube/mock-1"]]["error"]

    # Фоном — не чаще интервала, вручную — не чаще MIN_GAP_MINUTES: сразу второй раз никого не трогаем.
    assert platform_stats.collect_stats(store, sleep=pauses.append)["checked"] == 0
    assert platform_stats.collect_stats(store, force=True, sleep=pauses.append)["checked"] == 0
    assert len(calls) == 2

    # Повтор той же причины пропуска не плодит строк.
    store.add_publication_stats(targets[IG], "instagram", None, error=latest[targets[IG]]["error"])
    assert len(store.list_publication_stats(targets[IG])) == 1


def test_due_targets_respects_interval(monkeypatch):
    from datetime import datetime

    monkeypatch.setattr(settings, "stats_interval_hours", 6.0)
    now = datetime(2026, 10, 22, 12, 0, 0)
    targets = [{"id": 1, "last_attempt_at": "2026-10-22 07:00:00"},
               {"id": 2, "last_attempt_at": "2026-10-22 05:00:00"},
               {"id": 3, "last_attempt_at": None},
               {"id": 4, "last_attempt_at": "2026-10-22 11:55:00"}]
    assert [t["id"] for t in platform_stats.due_targets(targets, force=False, now=now)] == [2, 3]
    assert [t["id"] for t in platform_stats.due_targets(targets, force=True, now=now)] == [1, 2, 3]


def test_instagram_uses_session_client(tmp_path, monkeypatch):
    from app.providers import instagram

    _c, store = _client(tmp_path, monkeypatch)
    _plan, targets = _published(store)
    logins: list = []

    class Media:
        play_count = 4200
        like_count = 99
        comment_count = 5

    class FakeClient:
        def media_pk_from_code(self, code):
            assert code == "Cxyz"
            return 777

        def media_info(self, pk):
            assert pk == "777"
            return Media()

    def fake_session(cookies, proxy_url=""):
        logins.append(cookies)
        return FakeClient()

    monkeypatch.setattr(instagram, "session_client", fake_session)
    monkeypatch.setattr(platform_stats.subprocess, "run",
                        lambda args, **kw: subprocess.CompletedProcess(args, 1, "", "ERROR: offline"))
    platform_stats.collect_stats(store, sleep=lambda s: None)
    stats = store.latest_publication_stats()[targets[IG]]
    assert (stats["views"], stats["likes"], stats["comments"]) == (4200, 99, 5)
    assert len(logins) == 1


def test_session_client_without_sessionid_is_skip():
    from app.cookies import CookieRecord
    from app.providers import instagram

    try:
        instagram.session_client([CookieRecord(name="csrftoken", value="x", domain=".instagram.com")])
    except instagram.InstagramUnavailable as exc:
        assert "sessionid" in str(exc)
    else:
        raise AssertionError("без sessionid — пропуск")


def test_clip_performance_features_rest_and_mcp(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    monkeypatch.setattr("app.proxy_config.download_proxy", lambda: "")
    render = {
        "subs_on": True, "music_on": True, "track_id": 1,
        "layers": [
            {"type": "text", "start": 0.0, "end": 2.0, "x": 0.5, "y": 0.2, "props": {"text": "ОН ЭТО СДЕЛАЛ"}},
            {"type": "audio", "start": 3.0, "end": 4.0, "props": {"asset_id": "sfx:freesound/boom-low.ogg"}},
        ],
        "cuts": [{"start": 2.0, "end": 3.0}],
        "layout": {"mode": "meme"},
    }
    hit, hit_targets = _published(store, render, keyed=True)
    flop, flop_targets = _published(store)
    calls: list = []
    _fake_ytdlp(monkeypatch, {YT: {"view_count": 1000, "like_count": 100, "comment_count": 4},
                              TT: {"view_count": 500, "like_count": 50, "comment_count": 1}}, calls)
    platform_stats.collect_stats(store, sleep=lambda s: None)
    # Ссылки у обоих клипов одни и те же — числа «провала» задаём руками.
    store.db.execute("DELETE FROM publication_stats WHERE target_id IN (?, ?)", (flop_targets[YT], flop_targets[TT]))
    store.add_publication_stats(flop_targets[YT], "youtube", {"views": 10, "likes": 0, "comments": 0})

    rows = platform_stats.clip_performance(store)
    assert [r["clip_plan_id"] for r in rows] == [hit["id"], flop["id"]]
    top = rows[0]
    assert top["views"] == 1500 and top["likes"] == 150 and top["like_rate"] == 0.1
    assert top["age_days"] and top["views_per_day"] == round(1500 / max(1.0, top["age_days"]), 1)
    f = top["features"]
    assert f["hook_title"] and f["hook_text"] == "ОН ЭТО СДЕЛАЛ"
    assert f["subtitles"] and f["music"] and f["chroma_key"]
    assert f["layers"] == 1 and f["sfx"] == 1 and f["cuts"] == 1 and f["pieces"] == 2
    assert f["layout"] == "meme" and f["duration"] == 17.0  # 10 + 8 − вырез 1 с
    assert rows[1]["features"]["hook_title"] is False and rows[1]["views"] == 10

    body = client.get("/api/stats/clips").raise_for_status().json()
    assert body["clips"][0]["clip_plan_id"] == hit["id"] and "running" in body["status"]
    pubs = client.get("/api/stats/publications").raise_for_status().json()
    assert pubs["targets"][str(hit_targets[YT])]["views"] == 1000
    history = client.get(f"/api/stats/publications/{hit_targets[YT]}").raise_for_status().json()
    assert history[0]["views"] == 1000

    result, ok = run_tool(ToolContext(store), "get_performance", {"limit": 1})
    assert ok and result["clips_with_stats"] == 2
    assert result["top"][0]["clip_plan_id"] == hit["id"] and result["bottom"][0]["clip_plan_id"] == flop["id"]
    assert result["compare"]["hook_title"]["with_avg_views"] == 1500
    assert "мало" in result["note"].lower()


def test_refresh_endpoint_starts_background_collect(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    started: list = []
    monkeypatch.setattr(platform_stats, "collect_stats", lambda s, force=False, **kw: started.append(force))

    class SyncThread:
        def __init__(self, target, **kw):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(platform_stats.threading, "Thread", SyncThread)
    body = client.post("/api/stats/refresh").raise_for_status().json()
    assert body["started"] is True and started == [True]
