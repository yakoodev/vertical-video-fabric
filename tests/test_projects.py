"""Проекты (серии и каналы): миграция, CRUD, бриф, правила (дубли, противоречия, отмена),
ручные публикации и статистика, гипотезы («мало данных», расчёт, правило на пересмотр), REST и MCP."""

from __future__ import annotations

import sqlite3

import pytest

from app import projects as P
from app.agent_api import mcp_call_tool, mcp_tool_list
from app.agent_tools import ToolContext, _takes_source, get_tool, run_tool
from app.db import Database
from app.settings import settings
from test_api_pipeline import _client

YT = "https://www.youtube.com/shorts/abc123"
TT = "https://www.tiktok.com/@mura/video/7300000000000000001"
IG = "https://www.instagram.com/reel/Cxyz/"


def _source(store, name="mura-stream.mp4"):
    return store.create_source("upload", settings.source_dir / name, original_filename=name, duration_sec=3600,
                               status="ready")


def _plan(store, source_id, title, pieces=((10, 25),), rendered=True):
    analysis = store.create_ai_analysis(source_id, "manual", status="succeeded")
    segs = [store.create_ai_segment(analysis["id"], {"start_sec": a, "end_sec": b, "title": title})
            for a, b in pieces]
    plan = store.create_clip_plan(source_id, analysis["id"], title, segment_ids=[s["id"] for s in segs])
    if rendered:
        store.create_clip(source_id, clip_plan_id=plan["id"], status="succeeded")
    return plan


def _publish(store, plan_id, url, days_ago=3):
    """Публикация клипа через сервис (job + target), как у test_platform_stats."""
    clip = store.list_clips_for_plan(plan_id)[0]
    account = store.upsert_account("youtube", f"yt-{plan_id}-{abs(hash(url)) % 10000}",
                                   "SID=a; HSID=b; SSID=c; APISID=d; SAPISID=e")
    cur = store.db.execute(
        "INSERT INTO jobs (clip_id, title, status, source_filename, source_path, source_sha256, source_size_bytes) "
        "VALUES (?, 'Пост', 'succeeded', 'c.mp4', '/x/c.mp4', '', 0)",
        (clip["id"],),
    )
    tcur = store.db.execute(
        "INSERT INTO job_targets (job_id, account_id, platform, status, remote_id, remote_url, finished_at) "
        f"VALUES (?, ?, 'youtube', 'succeeded', 'x', ?, datetime('now', '-{int(days_ago)} days'))",
        (cur.lastrowid, account["id"], url),
    )
    return tcur.lastrowid


# --------------------------------------------------------------------------- #
# Миграция и CRUD
# --------------------------------------------------------------------------- #


def test_migration_keeps_old_sources_without_project(tmp_path):
    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    # Таблица sources старой сборки — без project_id и без поздних колонок.
    conn.executescript(
        """
        CREATE TABLE sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT, status TEXT NOT NULL DEFAULT 'created',
            source_type TEXT NOT NULL, original_url TEXT NOT NULL DEFAULT '',
            original_filename TEXT NOT NULL DEFAULT '', local_path TEXT NOT NULL, sha256 TEXT NOT NULL DEFAULT '',
            size_bytes INTEGER NOT NULL DEFAULT 0, duration_sec REAL NOT NULL DEFAULT 0,
            width INTEGER NOT NULL DEFAULT 0, height INTEGER NOT NULL DEFAULT 0, fps REAL NOT NULL DEFAULT 0,
            metadata_json TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO sources (source_type, original_filename, local_path) VALUES ('upload', 'старый стрим.mp4', '/x.mp4');
        """
    )
    conn.commit()
    conn.close()
    db = Database(path)
    db.init()
    db.init()  # повторный запуск — без ошибок
    columns = {row["name"] for row in db.query_all("PRAGMA table_info(sources)")}
    assert "project_id" in columns
    row = db.query_one("SELECT original_filename, project_id FROM sources")
    assert row["original_filename"] == "старый стрим.mp4" and row["project_id"] is None
    tables = {r["name"] for r in db.query_all("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"projects", "project_rules", "project_publications", "project_publication_stats", "clip_tags",
            "project_hypotheses"} <= tables


def test_project_crud_and_sources(tmp_path, monkeypatch):
    _client_, store = _client(tmp_path, monkeypatch)
    a, b, c = _source(store, "s1.mp4"), _source(store, "s2.mp4"), _source(store, "s3.mp4")
    brand = store.create_brand("Мура")
    project = P.create_project(store, "  Мура  ", kind="stream_channel", description="Стримы",
                               links=["https://twitch.tv/sareamura"], brand_id=brand["id"], source_ids=[a["id"]])
    assert project["name"] == "Мура" and project["kind"] == "stream_channel"
    assert project["links"] == [{"label": "", "url": "https://twitch.tv/sareamura"}]
    assert project["brand"] == {"id": brand["id"], "name": "Мура"}
    assert [s["id"] for s in project["sources"]] == [a["id"]]
    assert store.get_source(a["id"])["project_id"] == project["id"]
    assert store.get_source(c["id"])["project_id"] is None

    with pytest.raises(ValueError):
        P.create_project(store, "x", kind="tv")
    with pytest.raises(ValueError):
        P.create_project(store, "")

    project = P.update_project(store, project["id"], name="Мура (Twitch)", source_ids=[b["id"], c["id"]])
    assert sorted(s["id"] for s in project["sources"]) == sorted([b["id"], c["id"]])
    assert store.get_source(a["id"])["project_id"] is None

    # Источник — только в одном проекте: перенос во второй убирает из первого.
    other = P.create_project(store, "Аниме", kind="series")
    P.set_source_project(store, b["id"], other["id"])
    assert [s["id"] for s in P.get_project(store, project["id"])["sources"]] == [c["id"]]
    listed = {p["id"]: p for p in P.list_projects(store)}
    assert listed[other["id"]]["sources_count"] == 1

    P.add_rule(store, "Без мата в титрах", kind="dont", project_id=other["id"])
    P.delete_project(store, other["id"])
    assert store.get_source(b["id"])["project_id"] is None  # источник жив, просто без проекта
    assert store.db.query_one("SELECT COUNT(*) AS n FROM project_rules")["n"] == 0
    with pytest.raises(KeyError):
        P.get_project(store, other["id"])


# --------------------------------------------------------------------------- #
# Бриф
# --------------------------------------------------------------------------- #


def test_brief_partial_update_schema_and_conflict(tmp_path, monkeypatch):
    _c, store = _client(tmp_path, monkeypatch)
    project = P.create_project(store, "Мура", kind="stream_channel")
    brief = P.get_brief(store, project["id"])
    assert brief["brief_rev"] == 0
    assert brief["brief"]["schema"] == P.BRIEF_SCHEMA and brief["brief"]["version"] == P.BRIEF_VERSION

    out = P.update_brief(store, project["id"], {
        "summary": "Витуберша Мура, стримы по выходным",
        "people": [{"name": "Влад", "who": "муж", "traits": "спит рядом"},
                   {"name": "Онигуру", "who": "друг из дискорда"}],
        "arcs": [{"title": "Мура vs джойстик", "about": "джойстик всегда отключается"}],
        "jokes": ["«я не тупая, это лаг»"],
        "taboo": [{"topic": "политика", "why": "просьба Муры"}],
    })
    assert out["brief_rev"] == 1
    people = {p["name"]: p for p in out["brief"]["people"]}
    assert people["Влад"]["who"] == "муж" and people["Онигуру"]["aliases"] == []
    assert out["brief"]["jokes"][0]["text"] == "«я не тупая, это лаг»"

    # Дополнить по ключу без учёта регистра: прочие поля не стираются; удалить — _delete.
    out = P.update_brief(store, project["id"], {
        "people": [{"name": "влад", "aliases": "Владик, муж"}, {"name": "Онигуру", "_delete": True}],
    }, base_rev=1)
    people = out["brief"]["people"]
    assert len(people) == 1 and people[0]["who"] == "муж" and people[0]["aliases"] == ["Владик", "муж"]
    assert out["brief"]["summary"].startswith("Витуберша")  # не прислали — не тронули

    out = P.update_brief(store, project["id"], {"arcs": [{"title": "Новая арка"}], "replace_sections": ["arcs"]})
    assert [a["title"] for a in out["brief"]["arcs"]] == ["Новая арка"]

    with pytest.raises(P.BriefConflict):
        P.update_brief(store, project["id"], {"summary": "x"}, base_rev=1)
    with pytest.raises(ValueError):
        P.update_brief(store, project["id"], {"lore": "?"})


def test_brief_includes_rules_covered_clips_and_draft_hook(tmp_path, monkeypatch):
    _c, store = _client(tmp_path, monkeypatch)
    source = _source(store)
    project = P.create_project(store, "Мура", source_ids=[source["id"]])
    done = _plan(store, source["id"], "Мура против джойстика", pieces=((100, 120), (130, 140)))
    _plan(store, source["id"], "Кандидат анализа", rendered=False)
    # Кандидат обычного анализа без рендера — ещё не нарезка.
    analysis = store.create_ai_analysis(source["id"], "mock", status="succeeded")
    seg = store.create_ai_segment(analysis["id"], {"start_sec": 300, "end_sec": 320, "title": "c"})
    store.create_clip_plan(source["id"], analysis["id"], "Кандидат", segment_ids=[seg["id"]])
    P.set_clip_tags(store, done["id"], add=["Мура vs джойстик", "hook question"])
    P.add_rule(store, "Не брать политику", kind="dont", project_id=project["id"], quote="политику не бери",
               said_at="2026-10-30")
    P.add_rule(store, "Субтитры всегда крупно", scope="global")
    revoked = P.add_rule(store, "Музыка только lofi", kind="preference", project_id=project["id"])["rule"]
    P.revoke_rule(store, revoked["id"], reason="пересмотрел")

    brief = P.project_brief(store, project["id"])
    titles = [c["title"] for c in brief["covered"]]
    assert "Мура против джойстика" in titles and "Кандидат" not in titles
    covered = next(c for c in brief["covered"] if c["title"] == "Мура против джойстика")
    assert covered["pieces"] == [[100.0, 120.0], [130.0, 140.0]]
    assert covered["tags"] == ["hook_question", "мура_vs_джойстик"]
    rules = {r["text"]: r for r in brief["rules"]}
    assert set(rules) == {"Не брать политику", "Субтитры всегда крупно"}  # отменённое не отдаётся
    assert rules["Не брать политику"]["quote"] == "политику не бери"
    assert brief["project"]["sources"][0]["source_id"] == source["id"]

    store.set_source_transcript(source["id"], [{"start": 0, "end": 2, "text": "Влад, не храпи"}])
    draft = P.draft_brief(store, project["id"])
    assert draft["available"] is False and "Влад, не храпи" in draft["material"]["sources"][0]["excerpt"]
    P.register_brief_drafter(lambda material: {"people": [{"name": "Влад", "who": "муж"}]})
    try:
        draft = P.draft_brief(store, project["id"], apply=True)
    finally:
        P.register_brief_drafter(None)
    assert draft["available"] and draft["brief"]["people"][0]["name"] == "Влад"


# --------------------------------------------------------------------------- #
# Правила
# --------------------------------------------------------------------------- #


def test_rule_normalization_and_similarity():
    assert P.rule_tokens("Не брать политику!")[1] is True
    assert P.normalize_rule_text("Не брать политику") == P.normalize_rule_text("брать политику")
    assert P.rule_similarity("Брать политику", "не брать политики") >= P.CONFLICT_SCORE
    assert P.rule_similarity("брать политику", "ставить музыку lofi") < P.SIMILAR_SCORE


def test_rules_duplicates_contradictions_supersede_revoke(tmp_path, monkeypatch):
    _c, store = _client(tmp_path, monkeypatch)
    project = P.create_project(store, "Мура")
    pid = project["id"]
    first = P.add_rule(store, "Не брать политику", kind="dont", project_id=pid, quote="политику не трогай",
                       origin="tg", origin_ref="22274:101", said_at="2026-10-01")
    assert first["created"] and first["rule"]["status"] == "active" and first["rule"]["said_at"] == "2026-10-01"

    dup = P.add_rule(store, "не брать политику!", kind="dont", project_id=pid)
    assert dup["created"] is False and dup["duplicate_of"]["id"] == first["rule"]["id"]

    clash = P.add_rule(store, "Брать политику", kind="do", project_id=pid, quote="бери политику, заходит")
    assert clash["created"] is False and clash["needs_confirmation"]
    assert clash["conflicts"][0]["id"] == first["rule"]["id"] and "противоречит" in clash["question"]
    assert len(P.list_rules(store, project_id=pid)) == 1

    # Владелец решил: новое заменяет старое — старое superseded, в истории.
    new = P.add_rule(store, "Брать политику", kind="do", project_id=pid, quote="бери политику, заходит",
                     replaces=[first["rule"]["id"]])
    assert new["created"] and new["superseded"] == [first["rule"]["id"]]
    old = P.get_rule(store, first["rule"]["id"])
    assert old["status"] == "superseded" and old["superseded_by"] == new["rule"]["id"]
    assert [r["id"] for r in P.list_rules(store, project_id=pid)] == [new["rule"]["id"]]
    history = P.list_rules(store, project_id=pid, status="all")
    assert {r["status"] for r in history} == {"active", "superseded"}

    # force — оставить оба, хоть и противоречат.
    forced = P.add_rule(store, "Не брать политику в шортсы", kind="dont", project_id=pid, force=True)
    assert forced["created"]

    # Отмена по тексту: «пересмотрел, старое не работает».
    out = P.revoke_rule(store, text="брать политику", project_id=pid, reason="пересмотрел, старое не работает",
                        quote="пересмотрел, старое не работает")
    assert out["revoked"] and out["rule"]["id"] == new["rule"]["id"]
    rule = P.get_rule(store, new["rule"]["id"])
    assert rule["status"] == "revoked" and rule["revoke_quote"] == "пересмотрел, старое не работает"
    assert rule["revoked_at"]
    assert P.revoke_rule(store, new["rule"]["id"])["revoked"] is False  # дважды не отменить
    assert P.revoke_rule(store, text="что-то совсем другое", project_id=pid)["ok"] is False

    with pytest.raises(ValueError):
        P.add_rule(store, "x", kind="maybe", project_id=pid)
    with pytest.raises(ValueError):
        P.add_rule(store, "без проекта")  # scope project без project_id
    with pytest.raises(ValueError):
        P.add_rule(store, "дата", project_id=pid, said_at="вчера")


# --------------------------------------------------------------------------- #
# Ручные публикации и статистика
# --------------------------------------------------------------------------- #


def test_manual_publications_and_project_stats(tmp_path, monkeypatch):
    _c, store = _client(tmp_path, monkeypatch)
    source = _source(store)
    project = P.create_project(store, "Мура", source_ids=[source["id"]])
    pid = project["id"]
    hit = _plan(store, source["id"], "Зашло")
    flop = _plan(store, source["id"], "Не зашло")
    target = _publish(store, hit["id"], "https://www.youtube.com/shorts/svc1")
    store.add_publication_stats(target, "youtube", {"views": 9000, "likes": 900, "avg_retention": 0.6})

    calls = []

    def fake_fetch(url):
        calls.append(url)
        return {"views": 1200, "likes": 60, "comments": 4, "shares": None}

    monkeypatch.setattr("app.platform_stats.fetch_ytdlp_stats", fake_fetch)
    pub_tt = P.add_manual_publication(store, TT, clip_plan_id=flop["id"], published_at="2026-10-20",
                                      tags=["hook question"])
    assert pub_tt["project_id"] == pid and pub_tt["platform"] == "tiktok" and pub_tt["tags"] == ["hook_question"]
    assert P.refresh_manual_publication(store, pub_tt["id"])["ok"] and calls == [TT]
    pub_ig = P.add_manual_publication(store, IG, project_id=pid, title="Рилс руками",
                                      stats={"views": "300", "likes": 30, "avg_retention": 45})
    assert pub_ig["stats"]["views"] == 300 and pub_ig["stats"]["avg_retention"] == 0.45
    skipped = P.refresh_manual_publication(store, pub_ig["id"])
    assert skipped["skipped"] and "вручную" in skipped["error"]
    assert P.get_manual_publication(store, pub_ig["id"])["stats"]["views"] == 300  # ручные числа не пропали

    with pytest.raises(ValueError):
        P.add_manual_publication(store, TT, project_id=pid)  # та же ссылка
    with pytest.raises(ValueError):
        P.add_manual_publication(store, "не ссылка", project_id=pid)
    with pytest.raises(ValueError):
        P.add_manual_stats(store, pub_ig["id"], {})

    stats = P.project_stats(store, pid, top=1)
    totals = stats["totals"]
    assert totals["items"] == 3 and totals["items_with_stats"] == 3 and totals["publications"] == 3
    assert totals["views"] == 9000 + 1200 + 300 and totals["median_views"] == 1200
    assert totals["avg_retention"] == round((0.6 + 0.45) / 2, 3)
    assert stats["best"][0]["clip_plan_id"] == hit["id"]
    assert stats["worst"][0]["title"] == "Рилс руками"
    assert stats["platforms"]["youtube"]["views"] == 9000 and stats["platforms"]["tiktok"]["publications"] == 1
    flop_item = next(i for i in stats["items"] if i["clip_plan_id"] == flop["id"])
    assert flop_item["tags"] == ["hook_question"]
    assert "Мало данных" in stats["note"]

    # Фоновый сбор подхватывает ручные YouTube/TikTok (не чаще интервала).
    calls.clear()
    assert P.collect_manual_stats(store, force=True)["checked"] == 0  # только что сняли — рано
    store.db.execute("UPDATE project_publication_stats SET captured_at = datetime('now', '-1 day')")
    assert P.collect_manual_stats(store, force=True)["ok"] == 1 and calls == [TT]


# --------------------------------------------------------------------------- #
# Гипотезы
# --------------------------------------------------------------------------- #


def _numbered_clips(store, source_id, pid, views_tagged, views_plain):
    for index, views in enumerate(views_tagged):
        plan = _plan(store, source_id, f"Хук {index}")
        P.set_clip_tags(store, plan["id"], add=["hook_question"])
        P.add_manual_publication(store, f"https://www.tiktok.com/@m/video/1{plan['id']:04d}", clip_plan_id=plan["id"],
                                 stats={"views": views})
    for index, views in enumerate(views_plain):
        plan = _plan(store, source_id, f"Без хука {index}")
        P.add_manual_publication(store, f"https://www.tiktok.com/@m/video/2{plan['id']:04d}", clip_plan_id=plan["id"],
                                 stats={"views": views})


def test_hypothesis_little_data_then_confirmed_then_rule_review(tmp_path, monkeypatch):
    _c, store = _client(tmp_path, monkeypatch)
    source = _source(store)
    pid = P.create_project(store, "Мура", source_ids=[source["id"]])["id"]
    hyp = P.add_hypothesis(store, pid, "Хук-вопрос в первые 2 с даёт больше просмотров", "hook_question",
                           min_n=3, rule_text="Начинать клип с хука-вопроса")
    assert hyp["group"] == {"tag": "hook_question"} and hyp["status"] == "testing"

    _numbered_clips(store, source["id"], pid, [5000, 7000], [1000, 1500, 2000])
    out = P.evaluate_hypotheses(store, pid)["hypotheses"][0]
    assert out["status"] == "testing" and out["conclusion"].startswith("мало данных")
    assert out["result"]["group"]["n"] == 2 and out["result"]["enough"] is False

    _numbered_clips(store, source["id"], pid, [6000], [])
    out = P.evaluate_hypotheses(store, pid)["hypotheses"][0]
    assert out["status"] == "confirmed" and out["result"]["group"]["median"] == 6000
    assert out["result"]["rest"]["median"] == 1500 and out["result"]["lift"] == 4.0
    assert "подтверждается" in out["conclusion"] and "n=3" in out["conclusion"] and out["suggest_rule"]

    promoted = P.promote_hypothesis(store, hyp["id"])
    assert promoted["created"] and promoted["rule"]["text"] == "Начинать клип с хука-вопроса"
    assert promoted["rule"]["hypothesis_id"] == hyp["id"] and promoted["rule"]["origin"] == "hypothesis"
    assert P.evaluate_hypotheses(store, pid)["hypotheses"][0]["suggest_rule"] is False

    # Новые клипы без хука заходят лучше — гипотеза отвергнута, правило — на пересмотр.
    store.db.execute("UPDATE project_publication_stats SET views = 100 WHERE publication_id IN "
                     "(SELECT id FROM project_publications WHERE url LIKE '%/video/1%')")
    out = P.evaluate_hypotheses(store, pid)["hypotheses"][0]
    assert out["status"] == "rejected" and out["rules_flagged"] == [promoted["rule"]["id"]]
    rule = P.get_rule(store, promoted["rule"]["id"])
    assert rule["needs_review"] and "больше не подтверждается" in rule["review_note"]
    assert P.project_brief(store, pid)["rules"][0]["needs_review"] is True

    # Ручной статус владельца пересчёт не перетирает.
    P.update_hypothesis(store, hyp["id"], status="inconclusive")
    assert P.evaluate_hypotheses(store, pid)["hypotheses"][0]["status"] == "inconclusive"
    with pytest.raises(ValueError):
        P.add_hypothesis(store, pid, "x", {"feature": "colour", "op": "=="})


def test_hypothesis_conditions_and_math():
    items = [{"views": v, "tags": ["a"] if i < 5 else [], "features": {"duration": 20 if i < 5 else 50}}
             for i, v in enumerate([10, 12, 14, 16, 18, 10, 11, 12, 13, 14])]
    hyp = {"group": P.normalize_condition({"feature": "duration", "op": "<", "value": 30}), "baseline": None,
           "metric": "views", "direction": "higher", "min_n": 5}
    res = P.evaluate_hypothesis(hyp, items)
    assert res["group"]["median"] == 14 and res["rest"]["median"] == 12 and res["status"] == "inconclusive"
    # Чем меньше, тем лучше (direction lower): короткие «хуже» → отвергнута.
    res = P.evaluate_hypothesis({**hyp, "direction": "lower"}, items)
    assert res["status"] == "rejected"
    cond = P.normalize_condition({"all": ["tag:a", {"not": {"feature": "duration", "op": ">", "value": 40}}]})
    assert P.matches(cond, items[0]) is True and P.matches(cond, items[6]) is False
    assert P.matches({"feature": "duration", "op": "<", "value": 1}, {"features": None}) is None


# --------------------------------------------------------------------------- #
# MCP и REST
# --------------------------------------------------------------------------- #


def test_mcp_project_tools(tmp_path, monkeypatch):
    _c, store = _client(tmp_path, monkeypatch)
    source = _source(store)
    plan = _plan(store, source["id"], "Клип")
    ctx = ToolContext(store=store)

    result, ok = run_tool(ctx, "get_project_brief", {"source_id": source["id"]})
    assert not ok and "не в проекте" in result["error"]
    result, ok = run_tool(ctx, "save_channel_project", {"name": "Мура", "kind": "stream_channel",
                                                        "add_source_ids": [source["id"]]})
    assert ok and result["sources"] == [source["id"]]
    pid = result["project_id"]
    listed, ok = run_tool(ctx, "list_projects", {})
    assert listed["projects"][0]["channel_project_id"] == pid

    result, ok = run_tool(ctx, "update_project_brief", {"source_id": source["id"],
                                                        "patch": {"people": [{"name": "Влад", "who": "муж"}]}})
    assert ok and result["brief_rev"] == 1
    result, ok = run_tool(ctx, "add_rule", {"project_id": pid, "text": "Не брать политику", "kind": "dont",
                                            "quote": "без политики", "said_at": "2026-10-31", "origin": "tg"})
    assert ok and result["created"]
    result, ok = run_tool(ctx, "add_rule", {"project_id": pid, "text": "брать политику"})
    assert ok and result["needs_confirmation"]

    # Встроенный монтажёр внутри клипа: проект — по источнику клипа; run_tool не подставляет
    # источник в project_id инструментам проектов.
    assert not _takes_source(get_tool("get_project_brief"))
    brief, ok = run_tool(ToolContext(store=store, clip_plan_id=plan["id"]), "get_project_brief", {})
    assert ok and brief["project"]["id"] == pid and brief["brief"]["people"][0]["name"] == "Влад"
    assert brief["rules"][0]["text"] == "Не брать политику"

    rules, ok = run_tool(ctx, "list_rules", {"project_id": pid})
    assert ok and len(rules["rules"]) == 1
    result, ok = run_tool(ctx, "revoke_rule", {"rule_id": rules["rules"][0]["id"], "reason": "пересмотрел"})
    assert ok and result["revoked"]

    tools = {t["name"]: t for t in mcp_tool_list()}
    for name in ("get_project_brief", "update_project_brief", "list_rules", "add_rule", "revoke_rule",
                 "list_hypotheses", "add_hypothesis", "evaluate_hypotheses", "tag_clip", "list_channel_projects"):
        assert name in tools
    assert "clip_id" in tools["tag_clip"]["inputSchema"]["required"]
    assert "clip_id" not in tools["get_project_brief"]["inputSchema"]["properties"]
    out = mcp_call_tool(store, "tag_clip", {"clip_id": plan["id"], "add": ["Хук вопрос"]})
    assert not out.get("isError") and P.get_clip_tags(store, plan["id"]) == ["хук_вопрос"]

    result, ok = run_tool(ctx, "add_hypothesis", {"project_id": pid, "statement": "Хук-вопрос заходит",
                                                  "group": "хук_вопрос", "min_n": 2})
    assert ok
    result, ok = run_tool(ctx, "evaluate_hypotheses", {"project_id": pid})
    assert ok and result["hypotheses"][0]["conclusion"].startswith("мало данных")
    result, ok = run_tool(ctx, "add_manual_publication", {"clip": plan["id"], "url": IG, "views": 50})
    assert ok and result["publication"]["stats"]["views"] == 50
    stats, ok = run_tool(ctx, "get_project_stats", {"project_id": pid})
    assert ok and stats["totals"]["views"] == 50 and "items" not in stats


def test_rest_api(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    source = _source(store)
    plan = _plan(store, source["id"], "Клип")
    res = client.post("/api/projects", json={"name": "Сериал", "kind": "series"})
    assert res.status_code == 200, res.text
    pid = res.json()["id"]
    assert client.patch(f"/api/sources/{source['id']}/project", json={"project_id": pid}).status_code == 200
    assert client.get("/api/sources").json()[0]["project_id"] == pid
    assert client.get(f"/api/projects/{pid}").json()["sources"][0]["id"] == source["id"]
    assert client.post("/api/projects", json={"name": "x", "kind": "tv"}).status_code == 400
    assert client.get("/api/projects/999").status_code == 404

    brief = client.patch(f"/api/projects/{pid}/brief", json={"patch": {"summary": "Аниме"}, "base_rev": 0})
    assert brief.status_code == 200 and brief.json()["brief_rev"] == 1
    stale = client.patch(f"/api/projects/{pid}/brief", json={"patch": {"summary": "?"}, "base_rev": 0})
    assert stale.status_code == 409
    assert client.get(f"/api/projects/{pid}/brief").json()["brief"]["summary"] == "Аниме"

    rule = client.post("/api/rules", json={"project_id": pid, "text": "Не спойлерить финал", "kind": "dont",
                                           "quote": "без спойлеров", "origin": "ui"}).json()
    assert rule["created"]
    revoked = client.post(f"/api/rules/{rule['rule']['id']}/revoke", json={"reason": "сезон кончился"}).json()
    assert revoked["revoked"]
    assert client.get("/api/rules", params={"project_id": pid}).json() == []
    assert len(client.get("/api/rules", params={"project_id": pid, "status": "all"}).json()) == 1

    pub = client.post(f"/api/projects/{pid}/publications",
                      json={"url": IG, "clip_plan_id": plan["id"], "stats": {"views": 10}}).json()
    assert pub["stats"]["views"] == 10
    assert client.post(f"/api/manual-publications/{pub['id']}/stats", json={"views": 25}).json()["views"] == 25
    assert client.get(f"/api/projects/{pid}/stats").json()["totals"]["views"] == 25
    assert client.put(f"/api/clip-plans/{plan['id']}/tags", json={"add": ["op"]}).json()["tags"] == ["op"]
    hyp = client.post(f"/api/projects/{pid}/hypotheses", json={"statement": "Опенинги заходят", "group": "op"})
    assert hyp.status_code == 200
    assert client.post(f"/api/projects/{pid}/hypotheses/evaluate").json()["hypotheses"][0]["status"] == "testing"
    assert client.post(f"/api/hypotheses/{hyp.json()['id']}/promote").status_code == 400
    assert client.delete(f"/api/projects/{pid}").json()["deleted"]
    assert client.get("/api/sources").json()[0]["project_id"] is None
