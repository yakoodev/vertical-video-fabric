"""Заметка к клипу и публикации руками: несколько раз один клип, без ссылки, площадка и аккаунт,
счёт «опубликовано» у карточки и у файла (сервис + руками), фильтр, миграция старой базы, MCP."""

from __future__ import annotations

import sqlite3
from datetime import date

from app import clip_marks as M
from app import projects as P
from app.agent_tools import ToolContext, run_tool
from app.db import Database
from test_api_pipeline import _client
from test_projects import TT, YT, _plan, _publish, _source


def _card(client, source_id, plan_id):
    cards = client.get(f"/api/clip-cards?source_id={source_id}").json()["clips"]
    return next(c for c in cards if plan_id in c["plan_ids"])


def test_migration_adds_columns_and_keeps_old_publications(tmp_path):
    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    # Ручные публикации старой сборки: ссылка обязательна, ни аккаунта, ни файла, ни заметок клипа.
    conn.executescript(
        f"""
        CREATE TABLE project_publications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER, clip_plan_id INTEGER,
            url TEXT NOT NULL, platform TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '',
            published_at TEXT NOT NULL DEFAULT '', tags_json TEXT NOT NULL DEFAULT '[]', notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO project_publications (url, platform, title, published_at, notes)
        VALUES ('{YT}', 'youtube', 'Старый шортс', '2026-10-01', 'выложил с телефона');
        """
    )
    conn.commit()
    conn.close()
    db = Database(path)
    db.init()
    db.init()  # повторный запуск — без ошибок
    columns = {row["name"] for row in db.query_all("PRAGMA table_info(project_publications)")}
    assert {"account", "clip_id"} <= columns
    row = dict(db.query_one("SELECT * FROM project_publications"))
    assert row["url"] == YT and row["title"] == "Старый шортс" and row["notes"] == "выложил с телефона"
    assert row["published_at"] == "2026-10-01" and row["account"] == "" and row["clip_id"] is None
    tables = {r["name"] for r in db.query_all("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "clip_notes" in tables


def test_clip_publications_many_times_without_link_and_counts(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    source = _source(store)
    pid = P.create_project(store, "Мура", source_ids=[source["id"]])["id"]
    plan = _plan(store, source["id"], "Мать года")
    other = _plan(store, source["id"], "Не выложен")
    render = store.list_clips_for_plan(plan["id"])[0]
    _publish(store, plan["id"], "https://www.youtube.com/shorts/svc1")
    calls = []
    monkeypatch.setattr("app.platform_stats.fetch_ytdlp_stats", lambda url, account_proxy="": calls.append(url) or {"views": 10})

    # Без ссылки: площадка, аккаунт, дата — сегодня, пометка. Чисел не тянем.
    r = client.post(f"/api/clip-plans/{plan['id']}/publications",
                    json={"platform": "tiktok", "account": "@mura_clips", "notes": "с телефона", "fetch": True})
    assert r.status_code == 200, r.text
    first = r.json()
    assert first["url"] == "" and first["platform"] == "tiktok" and first["account"] == "@mura_clips"
    assert first["published_at"] == date.today().isoformat() and first["project_id"] == pid
    assert "fetch" not in first and calls == []
    # Тот же клип туда же ещё раз — можно; и на YouTube по ссылке.
    assert client.post(f"/api/clip-plans/{plan['id']}/publications",
                       json={"platform": "tiktok", "account": "@mura_clips", "published_at": "2026-11-01"}).status_code == 200
    yt = client.post(f"/api/clip-plans/{plan['id']}/publications",
                     json={"url": YT, "account": "Мура Shorts", "clip_id": render["id"]}).json()
    assert yt["platform"] == "youtube" and yt["clip_id"] == render["id"]

    # Проверки: площадка из списка, ссылка http(s), файл — версия этого клипа, ссылка не повторяется.
    other_render = store.list_clips_for_plan(other["id"])[0]
    for bad in ({"platform": "myspace"}, {"url": "не ссылка"}, {"platform": "vk", "clip_id": other_render["id"]},
                {"url": YT}):
        assert client.post(f"/api/clip-plans/{plan['id']}/publications", json=bad).status_code == 400, bad
    assert client.post("/api/clip-plans/99999/publications", json={"platform": "vk"}).status_code == 404

    # Карточка клипа: сервис 1 + руками 3, список с площадкой, аккаунтом и датой.
    card = _card(client, source["id"], plan["id"])
    assert card["published"] == 4
    assert {(p["kind"], p["platform"], p["account"]) for p in card["publications"]} == {
        ("service", "youtube", next(iter(store.list_accounts()))["label"]), ("manual", "tiktok", "@mura_clips"),
        ("manual", "youtube", "Мура Shorts")}
    assert card["publications"][0]["date"] >= card["publications"][-1]["date"]  # новые сверху
    assert _card(client, source["id"], other["id"])["published"] == 0
    # Файл: тот же счёт — ручные без файла относятся к последнему готовому файлу клипа.
    assert client.get(f"/api/clips/{render['id']}").json()["published_targets_count"] == 4
    page = client.get(f"/api/clip-renders?filter=published&clip_plan_id={plan['id']}").json()
    assert page["total"] == 1 and page["items"][0]["published_targets_count"] == 4

    # Экран клипа: список с числами и пометкой; правка и удаление.
    view = client.get(f"/api/clip-view/{plan['id']}").json()
    assert view["published"] == 4 and len(view["publications"]) == 4
    listed = client.get(f"/api/clip-plans/{plan['id']}/publications").json()
    assert [p["note"] for p in listed if p["kind"] == "manual" and p["id"] == first["id"]] == ["с телефона"]
    r = client.patch(f"/api/manual-publications/{first['id']}",
                     json={"platform": "vk", "account": "vk.com/mura", "url": "https://vk.com/clip-1_2", "notes": ""})
    assert r.status_code == 200 and r.json()["platform"] == "vk" and r.json()["url"].startswith("https://vk.com")
    assert client.patch(f"/api/manual-publications/{first['id']}", json={"platform": "?"}).status_code == 400
    assert client.delete(f"/api/manual-publications/{first['id']}").status_code == 200
    assert _card(client, source["id"], plan["id"])["published"] == 3

    # Без ссылки числа не собираются и ошибкой сбора это не считается.
    no_link = P.add_manual_publication(store, clip_plan_id=other["id"], platform="instagram")
    assert P.refresh_manual_publication(store, no_link["id"])["skipped"]
    assert P.get_manual_publication(store, no_link["id"])["stats"] is None
    assert all(p["id"] != no_link["id"] for p in P.due_manual_publications(store, force=True))

    # Статистика проекта по-прежнему считается: без ссылки — подпись площадки.
    P.add_manual_publication(store, project_id=pid, platform="vk")
    stats = P.project_stats(store, pid)
    assert stats["totals"]["publications"] == 3 + 1 + 1
    assert any(i["title"] == "VK" for i in stats["items"])

    # MCP: агент видит, где клип выложен; отметить может и без ссылки.
    out, ok = run_tool(ToolContext(store=store), "add_manual_publication",
                       {"clip": other["id"], "platform": "tiktok", "account": "@mura"})
    assert ok and out["publication"]["url"] == "" and "fetch" not in out["publication"]
    clip, ok = run_tool(ToolContext(store=store, clip_plan_id=plan["id"]), "get_clip", {})
    assert ok and clip["owner"]["published"] == 3
    assert {p["platform"] for p in clip["owner"]["publications"]} == {"youtube", "tiktok"}


def test_clip_note_per_clip_group(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    source = _source(store)
    v1 = _plan(store, source["id"], "Мура v1")
    v2 = _plan(store, source["id"], "Мура v2")
    lone = _plan(store, source["id"], "Отдельный")

    assert client.get(f"/api/clip-plans/{v1['id']}/note").json() == {"clip_id": v1["id"], "text": "",
                                                                     "updated_at": None}
    r = client.put(f"/api/clip-plans/{v1['id']}/note", json={"text": "Выложил в TikTok 05.11\nYouTube — ещё нет"})
    assert r.status_code == 200 and r.json()["text"] == "Выложил в TikTok 05.11\nYouTube — ещё нет"
    assert r.json()["updated_at"]
    M.set_clip_note(store, v2["id"], "переделать хук")
    assert _card(client, source["id"], lone["id"])["note"] == ""

    # Объединили версии в один клип: обе заметки видны одним текстом, сохранение кладёт его в главный план.
    store.db.execute("UPDATE clip_plans SET clip_group_id = ? WHERE id IN (?, ?)", (v1["id"], v1["id"], v2["id"]))
    card = _card(client, source["id"], v1["id"])
    assert card["id"] == v2["id"] and card["note"] == "Выложил в TikTok 05.11\nYouTube — ещё нет\n\nпеределать хук"
    assert client.get(f"/api/clip-view/{v1['id']}").json()["note"]["text"] == card["note"]
    saved = client.put(f"/api/clip-plans/{v1['id']}/note", json={"text": "всё выложено"}).json()
    assert saved == {**saved, "clip_id": v2["id"], "text": "всё выложено"}
    rows = store.db.query_all("SELECT clip_plan_id, text FROM clip_notes")
    assert [(r["clip_plan_id"], r["text"]) for r in rows] == [(v2["id"], "всё выложено")]
    assert client.put("/api/clip-plans/99999/note", json={"text": "x"}).status_code == 404

    clip, ok = run_tool(ToolContext(store=store, clip_plan_id=v2["id"]), "get_clip", {})
    assert ok and clip["owner"]["note"] == "всё выложено" and clip["owner"]["published"] == 0


def test_ready_clip_published_filter_counts_manual_only(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    source = _source(store)
    plan = _plan(store, source["id"], "Только руками")
    _plan(store, source["id"], "Ничего")
    P.add_manual_publication(store, TT, clip_plan_id=plan["id"])
    counts = client.get("/api/clip-renders").json()["counts"]
    assert counts["published"] == 1 and counts["all"] == 2
