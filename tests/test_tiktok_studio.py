"""Аналитика TikTok Studio: разбор записанного ответа, ник → аккаунт, частота сбора, хранение,
проход с подменённым браузером (без сети), экран клипа, статистика и бриф проекта, REST."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from app import projects as P
from app import tiktok_studio as S
from app import tiktok_studio_collect as C
from test_api_pipeline import _client
from test_projects import _plan, _source

FIXTURE = Path(__file__).parent / "fixtures" / "tiktok_studio_session.json"
VIDEO = "7300000000000000042"
MISSING = "7300000000000000043"
PROXY = "http://user:s3cret@proxy.example:8080"


def _session() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _insight() -> dict:
    return _session()["videos"][VIDEO]["insight"]


# --------------------------------------------------------------------------- #
# Разбор
# --------------------------------------------------------------------------- #


def test_parse_recorded_insight():
    parsed = S.parse_insight(_insight())
    assert parsed["video_id"] == VIDEO and parsed["author"] == "fixture_owner"
    assert parsed["views"] == 474 and parsed["avg_watch_sec"] == 8.73 and parsed["finish_rate"] == 0.0295
    assert parsed["total_play_sec"] == 4443 and parsed["new_followers"] == 1
    assert parsed["duration_sec"] == 42.41
    # Источники по убыванию, нулевые отброшены.
    assert parsed["traffic"][0] == ["For You", 0.994]
    assert [k for k, _ in parsed["traffic"]] == ["For You", "Personal Profile", "Others"]
    # Удержание: по секунде до 15 с, дальше каждые 5 с и последняя точка.
    secs = [p[0] for p in parsed["retention"]]
    assert secs[:16] == [float(s) for s in range(16)]
    assert secs[16:] == [20.0, 25.0, 30.0, 35.0, 40.0, 42.0]
    assert parsed["retention"][0] == [0.0, 1.0] and parsed["retention"][2] == [2.0, 0.54]


def test_parse_without_numbers_is_error():
    try:
        S.parse_insight({"video_finish_rate_realtime": {"status": 2, "value": None}})
    except ValueError:
        pass
    else:
        raise AssertionError("ждали ValueError")


def test_brief_hook_and_plain_text():
    parsed = S.parse_insight(_insight())
    snap = {"id": 1, "captured_at": "2026-11-09 10:00:00", **parsed}
    brief = S.brief_of(snap)
    assert brief["hook"] == {"1": 0.74, "2": 0.54, "3": 0.46, "5": 0.35}
    # 0,54 на 2-й секунде, 0,46 на 3-й: половина ушла к 2,5 с.
    assert brief["half_gone_sec"] == 2.5
    assert brief["watch_share"] == round(8.73 / 42.41, 3)
    assert brief["main_source"] == {"key": "For You", "label": "рекомендации", "share": 0.994}
    assert len(brief["retention"]) == 16
    text = brief["text"]
    assert text.startswith("Половина уходит к 2,5 с") and "к 3-й секунде остаётся 46%" in text
    assert "смотрят в среднем 8,7 с из 42,4" in text and "досматривают 3%" in text
    assert "99% — рекомендации" in text and "+1 подписч." in text
    assert S.brief_of({"captured_at": None, "error": "сбой", "last_attempt_at": "x"})["error"] == "сбой"
    assert S.brief_of(None) is None


def test_downsample_short_video_and_keeps_last_point():
    points = [(i / 2, 1 - i / 40) for i in range(0, 21)]  # каждые 0,5 с до 10 с
    out = S.downsample_retention(points)
    assert [p[0] for p in out] == [float(s) for s in range(11)]


def test_links_video_id_and_handle():
    assert S.video_id_of("https://www.tiktok.com/@User.Name/video/7705397016121085204?lang=ru") == "7705397016121085204"
    assert S.video_id_of("https://www.tiktok.com/@/video/7705397016121085204") == "7705397016121085204"
    assert S.video_id_of("https://vm.tiktok.com/ZMabc/") == ""
    assert S.video_id_of("https://www.youtube.com/video/123456789") == ""
    assert S.handle_of("https://www.tiktok.com/@User.Name/video/1") == "user.name"
    assert S.handle_of("https://www.tiktok.com/@/video/1") == ""


# --------------------------------------------------------------------------- #
# Частота
# --------------------------------------------------------------------------- #


def test_due_cadence_daily_weekly_skip_and_order():
    now = datetime(2026, 11, 9, 12, 0, 0)

    def ago(**kw) -> str:
        return (now - timedelta(**kw)).strftime("%Y-%m-%d %H:%M:%S")

    items = [
        {"kind": "service", "ref": 1, "published_at": ago(days=2)},    # свежий, был 2 ч назад — рано
        {"kind": "service", "ref": 2, "published_at": ago(days=5)},    # свежий, был 21 ч назад — пора
        {"kind": "manual", "ref": 3, "published_at": ago(days=45)},    # месяц+, был 3 дня назад — рано
        {"kind": "manual", "ref": 4, "published_at": ago(days=45)},    # месяц+, был 8 дней назад — пора
        {"kind": "manual", "ref": 5, "published_at": ago(days=120)},   # старый — не собираем
        {"kind": "manual", "ref": 6, "published_at": ago(days=10)},    # ни разу — первым
        {"kind": "service", "ref": 7, "published_at": None},           # дата неизвестна — как свежий
    ]
    attempts = {("service", 1): ago(hours=2), ("service", 2): ago(hours=21), ("manual", 3): ago(days=3),
                ("manual", 4): ago(days=8), ("manual", 5): ago(days=30)}
    picked = C.due(items, attempts, now=now)
    assert [(i["kind"], i["ref"]) for i in picked] == [("service", 7), ("manual", 6), ("service", 2), ("manual", 4)]
    # Вручную — всем, кроме собранных меньше получаса назад.
    attempts[("service", 2)] = ago(minutes=10)
    forced = C.due(items, attempts, force=True, now=now)
    assert {i["ref"] for i in forced} == {1, 3, 4, 5, 6, 7}


# --------------------------------------------------------------------------- #
# Аккаунты, кандидаты, хранение
# --------------------------------------------------------------------------- #


def _tt_account(store, label, handle="", proxy=""):
    acc = store.upsert_account("tiktok", label, "sessionid=abc; tt-target-idc=useast2a", proxy_url=proxy,
                               user_agent="Mozilla/5.0 Test")
    if handle:
        store.db.execute("UPDATE accounts SET handle = ? WHERE id = ?", (handle, acc["id"]))
    return acc


def _service_post(store, plan_id, account, url, days_ago=3):
    clip = store.list_clips_for_plan(plan_id)[0]
    cur = store.db.execute(
        "INSERT INTO jobs (clip_id, title, status, source_filename, source_path, source_sha256, source_size_bytes) "
        "VALUES (?, 'Пост', 'succeeded', 'c.mp4', '/x/c.mp4', '', 0)",
        (clip["id"],),
    )
    tcur = store.db.execute(
        "INSERT INTO job_targets (job_id, account_id, platform, status, remote_id, remote_url, finished_at) "
        f"VALUES (?, ?, 'tiktok', 'succeeded', 'x', ?, datetime('now', '-{int(days_ago)} days'))",
        (cur.lastrowid, account["id"], url),
    )
    return tcur.lastrowid


def test_account_mapping_by_handle_and_label(tmp_path, monkeypatch):
    _c, store = _client(tmp_path, monkeypatch)
    sova = _tt_account(store, "сова", handle="user5260959022512")
    yakoo = _tt_account(store, "yakoodev", handle="yakoodev")
    store.upsert_account("youtube", "yt", "SID=a; HSID=b; SSID=c; APISID=d; SAPISID=e")
    accounts = C.tiktok_accounts(store)
    assert set(accounts) == {sova["id"], yakoo["id"]}
    assert C.account_for_handle(accounts, "@User5260959022512") == sova["id"]
    assert C.account_for_handle(accounts, "psx53") is None  # аккаунт DnD в сервис не подключён
    # Ника в ссылке нет — по подписи аккаунта (ник или название).
    assert C.account_for_handle(accounts, "", "@yakoodev") == yakoo["id"]
    assert C.account_for_handle(accounts, "", "Сова") == sova["id"]
    # Ник в ссылке есть, но чужой — подпись не спасает: аналитику читает только владелец ролика.
    assert C.account_for_handle(accounts, "psx53", "сова") is None


def test_candidates_service_and_manual(tmp_path, monkeypatch):
    _c, store = _client(tmp_path, monkeypatch)
    sova = _tt_account(store, "сова", handle="user5260959022512")
    source = _source(store)
    project = P.create_project(store, "Сова", source_ids=[source["id"]])
    plan = _plan(store, source["id"], "Клип")
    target = _service_post(store, plan["id"], sova, f"https://www.tiktok.com/@/video/{VIDEO}")
    mine = P.add_manual_publication(store, f"https://www.tiktok.com/@user5260959022512/video/{MISSING}",
                                    clip_plan_id=plan["id"])
    P.add_manual_publication(store, "https://www.tiktok.com/@psx53/video/7300000000000000099", project_id=project["id"])
    P.add_manual_publication(store, "https://vm.tiktok.com/ZMabc/", project_id=project["id"])
    items, unmapped = C.candidates(store)
    assert unmapped == 1
    assert [(i["kind"], i["ref"], i["video_id"], i["account_id"]) for i in items] == [
        ("service", target, VIDEO, sova["id"]), ("manual", mine["id"], MISSING, sova["id"])]


def test_snapshots_latest_and_error_dedupe(tmp_path, monkeypatch):
    _c, store = _client(tmp_path, monkeypatch)
    source = _source(store)
    plan = _plan(store, source["id"], "Клип")
    pub = P.add_manual_publication(store, f"https://www.tiktok.com/@x/video/{VIDEO}", clip_plan_id=plan["id"])
    S.add_snapshot(store, publication_id=pub["id"], video_id=VIDEO, parsed=S.parse_insight(_insight()))
    first = S.add_snapshot(store, publication_id=pub["id"], video_id=VIDEO, error="сбой")
    again = S.add_snapshot(store, publication_id=pub["id"], video_id=VIDEO, error="сбой")
    assert first["id"] == again["id"]  # та же причина — та же строка
    rows = store.db.query_all("SELECT * FROM tiktok_studio_stats")
    assert len(rows) == 2
    latest = S.latest_snapshots(store)[("manual", pub["id"])]
    assert latest["views"] == 474 and latest["error"] == "сбой" and latest["retention"][1] == [1.0, 0.74]
    try:
        S.add_snapshot(store, video_id=VIDEO, error="x")
    except ValueError:
        pass
    else:
        raise AssertionError("без публикации снимок не пишется")


def test_playwright_cookies_only_tiktok_domains():
    from app.cookies import CookieRecord

    out = C.playwright_cookies([
        CookieRecord("sessionid", "v1", ".tiktok.com", "/", True, True, 1999999999),
        CookieRecord("SID", "v2", ".google.com"),
        CookieRecord("tt_csrf_token", "v3", "", "/", False, False, None),
    ])
    assert out == [
        {"name": "sessionid", "value": "v1", "domain": ".tiktok.com", "path": "/", "secure": True, "httpOnly": True,
         "expires": 1999999999},
        {"name": "tt_csrf_token", "value": "v3", "domain": ".tiktok.com", "path": "/", "secure": False,
         "httpOnly": False},
    ]


# --------------------------------------------------------------------------- #
# Проход целиком: браузер подменён записанным ответом
# --------------------------------------------------------------------------- #


class FakeNode:
    def __init__(self, answer: dict | None = None, *, handle_only: dict | None = None, fail: str = ""):
        self.calls: list[tuple[list, dict]] = []
        self.answer = answer
        self.handle_only = handle_only or {"status": "ok", "handle": "fixture_owner", "login_required": False,
                                           "videos": {}}
        self.fail = fail

    def __call__(self, args, **kw):
        self.calls.append((list(args), dict(kw["env"])))
        if self.fail:
            return subprocess.CompletedProcess(args, 1, "", self.fail)
        ids = [i for i in args[-1].split(",") if i]
        answer = self.answer if ids else self.handle_only
        return subprocess.CompletedProcess(args, 0, json.dumps(answer) + "\n", "")


def _setup(tmp_path, monkeypatch, handle=""):
    client, store = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(C, "_handle_tries", {})
    monkeypatch.setenv("TIKTOK_STUDIO_PAUSE_SECONDS", "0")
    acc = _tt_account(store, "сова", handle=handle, proxy=PROXY)
    source = _source(store)
    project = P.create_project(store, "Сова", source_ids=[source["id"]])
    plan = _plan(store, source["id"], "Сова и лифт")
    target = _service_post(store, plan["id"], acc, f"https://www.tiktok.com/@/video/{VIDEO}")
    pub = P.add_manual_publication(store, f"https://www.tiktok.com/@fixture_owner/video/{MISSING}",
                                   clip_plan_id=plan["id"])
    return client, store, acc, project, plan, target, pub


def test_collect_learns_handle_saves_snapshots_and_hides_secrets(tmp_path, monkeypatch):
    client, store, acc, project, plan, target, pub = _setup(tmp_path, monkeypatch)
    node = FakeNode(_session())
    result = C.collect(store, runner=node, sleep=lambda s: None)
    assert result["handles"] == {str(acc["id"]): "fixture_owner"}
    assert store.get_account(acc["id"])["handle"] == "fixture_owner"
    # Две сессии: ник (без роликов), затем ролики аккаунта одной сессией — сервиса и отмеченный руками.
    assert len(node.calls) == 2
    args, env = node.calls[1]
    assert set(args[-1].split(",")) == {VIDEO, MISSING}
    assert env["TT_STUDIO_PROXY"] == PROXY and env["TT_STUDIO_UA"] == "Mozilla/5.0 Test"
    assert json.loads(env["TT_STUDIO_COOKIES"])[0]["name"] == "sessionid"
    # Секреты — только окружением: в аргументах их нет.
    assert not any("s3cret" in a or "sessionid" in a for a in args)
    assert result["ok"] == 1 and result["failed"] == 1 and result["checked"] == 2
    latest = S.latest_snapshots(store)
    assert latest[("service", target)]["avg_watch_sec"] == 8.73
    assert "не отдала числа" in latest[("manual", pub["id"])]["error"]

    # Через сутки не пора: второй проход ничего не открывает.
    again = FakeNode(_session())
    assert C.collect(store, runner=again, sleep=lambda s: None)["checked"] == 0 and not again.calls

    # Экран клипа: сводка Studio у публикации сервиса, ошибка — у ручной.
    view = client.get(f"/api/clip-view/{plan['id']}").json()
    by_kind = {p["kind"]: p for p in view["publications"]}
    assert by_kind["service"]["studio"]["half_gone_sec"] == 2.5
    assert by_kind["manual"]["studio"]["captured_at"] is None and by_kind["manual"]["studio"]["error"]

    # Статистика проекта и бриф агента: медианы и строка «половина уходит…».
    store.add_publication_stats(target, "tiktok", {"views": 470})  # публичные числа (yt-dlp)
    stats = client.get(f"/api/projects/{project['id']}/stats").json()
    assert stats["totals"]["studio"]["videos"] == 1
    assert "половина зрителей уходит к 2,5 с" in stats["totals"]["studio"]["text"]
    assert stats["best"][0]["studio"]["hook"]["3"] == 0.46 and "half_gone_sec" in stats["totals"]["studio"]["legend"]
    brief = client.get(f"/api/projects/{project['id']}/brief").json()
    assert brief["stats"]["totals"]["studio"]["median_finish_rate"] == 0.0295
    assert brief["stats"]["best"][0]["studio"]["text"].startswith("Половина уходит")

    # REST: состояние и сводки по публикациям.
    status = client.get("/api/tiktok-studio/status").json()
    assert status["videos"] == 2 and status["accounts"][0]["handle"] == "fixture_owner"
    pubs = client.get("/api/tiktok-studio/publications").json()
    assert pubs[f"service:{target}"]["views"] == 474


def test_login_required_and_node_failure_are_saved_masked(tmp_path, monkeypatch):
    _client_, store, acc, _project, _plan_, target, pub = _setup(tmp_path, monkeypatch, handle="fixture_owner")
    gone = FakeNode({"status": "ok", "handle": "", "login_required": True, "videos": {}})
    C.collect(store, runner=gone, sleep=lambda s: None)
    latest = S.latest_snapshots(store)
    assert latest[("service", target)]["error"] == C.LOGIN_ERROR
    assert len(gone.calls) == 1  # ник уже известен — сессии за ником нет

    broken = FakeNode(fail=f"Error: net::ERR_PROXY at {PROXY}\n")
    C.collect(store, runner=broken, force=True, sleep=lambda s: None)
    error = S.latest_snapshots(store)[("manual", pub["id"])]["error"]
    assert error and "s3cret" not in error


def test_collect_filters_by_video_and_respects_cap(tmp_path, monkeypatch):
    _client_, store, acc, *_rest = _setup(tmp_path, monkeypatch, handle="fixture_owner")
    monkeypatch.setenv("TIKTOK_STUDIO_MAX_PER_RUN", "1")
    node = FakeNode(_session())
    out = C.collect(store, runner=node, video_id=MISSING, sleep=lambda s: None)
    assert out["checked"] == 1 and node.calls[0][0][-1] == MISSING
    node = FakeNode(_session())
    C.collect(store, runner=node, force=True, sleep=lambda s: None)
    assert node.calls[0][0][-1] == VIDEO  # ни разу не собранный — первым, и только один за проход


def test_collect_endpoint_validates_and_starts(tmp_path, monkeypatch):
    client, _store = _client(tmp_path, monkeypatch)
    started = []
    monkeypatch.setattr(C, "start_collect", lambda store, **kw: started.append(kw) or {"started": True})
    assert client.post("/api/tiktok-studio/collect", json={"video_id": "abc"}).status_code == 400
    assert client.post("/api/tiktok-studio/collect", json={"account_id": "x"}).status_code == 400
    assert client.post("/api/tiktok-studio/collect", json={"account_id": 5, "video_id": VIDEO}).json()["started"]
    assert started == [{"account_id": 5, "video_id": VIDEO, "force": True}]


def test_background_gate(tmp_path, monkeypatch):
    _c, store = _client(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(C, "start_collect", lambda store, **kw: calls.append(kw) or {"started": True})
    monkeypatch.setitem(C._status, "finished_at", None)
    monkeypatch.setenv("TIKTOK_STUDIO_STATS", "0")
    assert C.maybe_start(store) is False
    monkeypatch.setenv("TIKTOK_STUDIO_STATS", "1")
    assert C.maybe_start(store) is True and calls == [{"force": False}]
    monkeypatch.setitem(C._status, "finished_at", datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))
    assert C.maybe_start(store) is False  # недавно был проход


def test_manual_tiktok_link_numbers_go_through_the_owner_account_proxy(tmp_path, monkeypatch):
    """С адреса сервера TikTok отвечает «IP blocked»: числа ручной ссылки — через прокси аккаунта по нику,
    чужой ник — через любой TikTok-аккаунт с прокси."""
    _c, store = _client(tmp_path, monkeypatch)
    _tt_account(store, "сова", handle="user5260959022512", proxy="http://u:p@10.0.0.1:1000")
    _tt_account(store, "yakoodev", handle="yakoodev", proxy="http://u:p@10.0.0.2:2000")
    source = _source(store)
    project = P.create_project(store, "Сова", source_ids=[source["id"]])
    seen = []
    monkeypatch.setattr("app.platform_stats.fetch_ytdlp_stats",
                        lambda url, account_proxy="": seen.append(account_proxy) or {"views": 5})
    mine = P.add_manual_publication(store, "https://www.tiktok.com/@yakoodev/video/7706134923484122389",
                                    project_id=project["id"])
    other = P.add_manual_publication(store, "https://www.tiktok.com/@.psx53/video/7706158002662903060",
                                     project_id=project["id"])
    assert P.refresh_manual_publication(store, mine["id"])["ok"]
    assert P.refresh_manual_publication(store, other["id"])["ok"]
    assert seen[0].endswith("10.0.0.2:2000") and seen[1]  # свой аккаунт; чужой ник — первый с прокси
