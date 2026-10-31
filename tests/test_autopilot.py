"""Автопилот канала: очередь одобрения и доверие, расписание (слоты, пояса, лимиты), обучение на
ответах владельца (правило, отмена, противоречие), петля статистики, сводка, Telegram (замокан),
MCP и плейбук, чат проекта (замоканный Gemini)."""

from __future__ import annotations

import copy
import re
from datetime import datetime, timezone

import pytest

from app import autopilot as A
from app import notify_tg
from app import playbooks
from app import project_chat
from app import projects as P
from app.agent_api import mcp_call_tool
from app.agent_tools import list_tools
from app.settings import settings
from test_api_pipeline import _client

# Среда, 4 ноября 2026, 16:00 UTC = 19:00 по Москве.
WED = datetime(2026, 11, 4, 16, 0, tzinfo=timezone.utc)
COOKIE = "SID=a; HSID=b; SSID=c; APISID=d; SAPISID=e"


@pytest.fixture
def env(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(A, "now", lambda: WED)
    monkeypatch.delenv("AUTOPILOT_TG_TOKEN", raising=False)
    source = store.create_source("upload", settings.source_dir / "mura.mp4", original_filename="mura.mp4",
                                 duration_sec=3600, status="ready")
    pid = P.create_project(store, "Мура", kind="stream_channel", source_ids=[source["id"]])["id"]
    store.upsert_account("youtube", "yt-main", COOKIE)
    return client, store, source, pid


def _clip(store, source_id, title="Мура пугается", tags=()):
    analysis = store.create_ai_analysis(source_id, "manual", status="succeeded")
    seg = store.create_ai_segment(analysis["id"], {"start_sec": 10, "end_sec": 25, "title": title})
    plan = store.create_clip_plan(source_id, analysis["id"], title, segment_ids=[seg["id"]])
    clip = store.create_clip(source_id, clip_plan_id=plan["id"], status="succeeded")
    out = settings.clip_dir / f"clip-{clip['id']}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"\x00" * 1024)
    store.db.execute("UPDATE clips SET output_path = ? WHERE id = ?", (str(out), clip["id"]))
    if tags:
        P.set_clip_tags(store, plan["id"], add=list(tags))
    return plan


def _jobs(store):
    return store.db.query_all("SELECT * FROM jobs ORDER BY id")


# --------------------------------------------------------------------------- #
# Одобрение и доверие
# --------------------------------------------------------------------------- #


def test_nothing_published_without_approval_then_slot(env):
    _c, store, source, pid = env
    A.update_config(store, pid, {"slots": [{"days": [0, 1, 2, 3, 4], "times": ["18:00"]}], "platforms": ["youtube"]})
    plan = _clip(store, source["id"])
    card = A.submit_clip(store, plan["id"], title="Мура пугается", tags=["#мура", "vtuber"])
    assert card["status"] == "pending" and card["trust"] == "approve" and card["tags"] == ["мура", "vtuber"]
    assert not _jobs(store)  # без approved — никаких заданий публикации
    assert [b["action"] for b in card["buttons"]] == ["approve", "changes", "reject"]

    res = A.decide(store, card["id"], "approve", actor="ui")
    assert res["status"] == "approved" and res["placement"]["ok"]
    jobs = _jobs(store)
    assert len(jobs) == 1 and jobs[0]["status"] == "queued"
    # Ближайший слот после среды 19:00 МСК — четверг 18:00 МСК = 15:00 UTC.
    assert jobs[0]["scheduled_at"] == "2026-11-05 15:00:00"
    assert "#мура" in jobs[0]["description"] and "#vtuber" in jobs[0]["description"]
    got = A.get_approval(store, card["id"])
    assert got["scheduled_local"] == "2026-11-05 18:00" and got["publish_status"] == "scheduled"
    with pytest.raises(ValueError):
        A.decide(store, card["id"], "reject")  # решать можно один раз
    events = [e["event"] for e in A.list_events(store, pid)]
    assert {"submitted", "approved", "scheduled"} <= set(events)


def test_trust_auto_skips_approval_but_logs(env):
    _c, store, source, pid = env
    A.update_config(store, pid, {"trust": {"meme": "auto", "ad": "approve"}, "platforms": ["youtube"]})
    meme = _clip(store, source["id"], "Мем", tags=["type:meme"])
    ad = _clip(store, source["id"], "Реклама")
    auto = A.submit_clip(store, meme["id"], title="Мем")
    assert auto["content_type"] == "meme" and auto["status"] == "approved" and auto["decided_by"] == "auto"
    assert auto["publish_status"] == "scheduled" and auto["tg_state"] == ""
    assert "auto_approved" in [e["event"] for e in A.list_events(store, pid)]
    manual = A.submit_clip(store, ad["id"], title="Реклама", content_type="ad")
    assert manual["status"] == "pending" and manual["trust"] == "approve"
    assert len(_jobs(store)) == 1
    # Повторная отправка того же клипа: старая pending-карточка уходит в expired.
    again = A.submit_clip(store, ad["id"], title="Реклама v2", content_type="ad")
    assert A.get_approval(store, manual["id"])["status"] == "expired" and again["status"] == "pending"


def test_reject_and_changes_do_not_publish(env):
    _c, store, source, pid = env
    A.update_config(store, pid, {"platforms": ["youtube"]})
    one = A.submit_clip(store, _clip(store, source["id"])["id"], title="a")
    two = A.submit_clip(store, _clip(store, source["id"])["id"], title="b")
    A.decide(store, one["id"], "reject")
    A.decide(store, two["id"], "changes", comment="")
    assert not _jobs(store)
    assert A.approval_counts(store)["rejected"] == 1 and A.approval_counts(store)["changes_requested"] == 1


def test_missing_account_is_reported_not_raised(env):
    _c, store, source, pid = env
    card = A.submit_clip(store, _clip(store, source["id"])["id"], title="x", platforms=["tiktok"])
    res = A.decide(store, card["id"], "approve")
    assert res["placement"]["ok"] is False and "TikTok" in res["placement"]["error"]
    assert A.get_approval(store, card["id"])["publish_status"] == "failed" and not _jobs(store)


def test_expire_old_pending(env, monkeypatch):
    _c, store, source, pid = env
    A.update_config(store, pid, {"expire_hours": 24})
    card = A.submit_clip(store, _clip(store, source["id"])["id"], title="x")
    store.db.execute("UPDATE approvals SET created_at = '2026-11-02 10:00:00' WHERE id = ?", (card["id"],))
    assert A.expire_old(store) == 1 and A.get_approval(store, card["id"])["status"] == "expired"


# --------------------------------------------------------------------------- #
# Расписание
# --------------------------------------------------------------------------- #


def test_slots_timezones_and_daily_limit(env):
    _c, store, source, pid = env
    A.update_config(store, pid, {"timezone": "UTC+5", "slots": [{"days": [3, 4], "times": ["10:00", "21:00"]}],
                                 "per_day": {"default": 0, "youtube": 1}, "platforms": ["youtube"]})
    times = []
    for title in ("a", "b", "c"):
        card = A.submit_clip(store, _clip(store, source["id"], title)["id"], title=title)
        times.append(A.decide(store, card["id"], "approve")["placement"]["scheduled_local"])
    # UTC+5: среда 21:00 местного; лимит 1 в день на YouTube → чт, пт, затем следующий чт.
    assert times == ["2026-11-05 10:00", "2026-11-06 10:00", "2026-11-12 10:00"]
    schedule = A.get_schedule(store, pid)
    assert [u["scheduled_local"] for u in schedule["upcoming"]] == times
    assert schedule["timezone"] == "UTC+5"
    assert A.tz_of("Europe/Moscow").utcoffset(WED).total_seconds() == 3 * 3600
    with pytest.raises(ValueError):
        A.normalize_config({"timezone": "Марс/Олимп"})
    with pytest.raises(ValueError):
        A.normalize_config({"slots": [{"days": [7], "times": ["10:00"]}]})


def test_no_slots_publishes_now_and_limit_moves_to_tomorrow(env):
    _c, store, source, pid = env
    A.update_config(store, pid, {"per_day": {"default": 1}, "platforms": ["youtube"]})
    first = A.decide(store, A.submit_clip(store, _clip(store, source["id"])["id"], title="a")["id"], "approve")
    second = A.decide(store, A.submit_clip(store, _clip(store, source["id"])["id"], title="b")["id"], "approve")
    assert first["placement"]["scheduled_at"] == "2026-11-04 16:00:00"
    assert second["placement"]["scheduled_local"] == "2026-11-05 12:00"  # Москва, завтра 12:00


def test_reschedule_move_next_and_cancel(env):
    _c, store, source, pid = env
    A.update_config(store, pid, {"slots": [{"days": list(range(7)), "times": ["12:00"]}], "platforms": ["youtube"]})
    card = A.submit_clip(store, _clip(store, source["id"])["id"], title="x", when="2026-11-10 09:30")
    assert card["proposed_local"] == "2026-11-10 09:30"
    A.decide(store, card["id"], "approve")
    job_id = A.get_approval(store, card["id"])["job_id"]
    assert store.get_job(job_id)["scheduled_at"] == "2026-11-10 06:30:00"  # предложенное время, не слот

    A.reschedule(store, card["id"], "move", at="2026-11-11 20:00")
    assert store.get_job(job_id)["scheduled_at"] == "2026-11-11 17:00:00"
    moved = A.reschedule(store, card["id"], "next_slot")
    assert moved["scheduled_local"] == "2026-11-05 12:00" and store.get_job(job_id)["status"] == "cancelled"
    new_job = A.get_approval(store, card["id"])["job_id"]
    A.reschedule(store, card["id"], "cancel")
    assert store.get_job(new_job)["status"] == "cancelled"
    assert A.get_approval(store, card["id"])["publish_status"] == "cancelled"
    with pytest.raises(ValueError):
        A.reschedule(store, card["id"], "move", at="2026-11-01 10:00")  # в прошлое


def test_sync_publish_status(env):
    _c, store, source, pid = env
    A.update_config(store, pid, {"platforms": ["youtube"]})
    card = A.submit_clip(store, _clip(store, source["id"])["id"], title="x")
    A.decide(store, card["id"], "approve")
    job_id = A.get_approval(store, card["id"])["job_id"]
    store.db.execute("UPDATE jobs SET status = 'succeeded', result_json = ? WHERE id = ?",
                     ('{"targets": [{"remote_url": "https://youtu.be/x"}]}', job_id))
    changed = A.sync_publish_status(store)
    assert changed[0]["status"] == "published" and changed[0]["urls"] == ["https://youtu.be/x"]
    assert A.get_approval(store, card["id"])["publish_status"] == "published"


# --------------------------------------------------------------------------- #
# Обучение на ответах
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("text,intent,kind", [
    ("не бери политику", "rule", "dont"),
    ("Убери мат", "rule", "dont"),
    ("круто, что субтитры крупные — так и делай", "rule", "do"),
    ("круто, так и делай", "approve", None),
    ("да", "approve", None),
    ("в мусор", "reject", None),
    ("пересмотрел контент, правило про политику больше не работает", "revoke", None),
    ("забудь правило про музыку", "revoke", None),
    ("сегодня солнечно", "none", None),
])
def test_classify_reply(text, intent, kind):
    cls = A.classify_reply(text)
    assert cls["intent"] == intent
    if kind:
        assert cls["kind"] == kind and cls["confidence"] >= A.CONFIDENT


def test_classify_unsure_cases():
    one_off = A.classify_reply("убери мат в этом клипе на 0:15")
    assert one_off["intent"] == "rule" and one_off["one_off"] and one_off["confidence"] < A.CONFIDENT
    mixed = A.classify_reply("круто что быстро, но не ставь музыку поверх голоса?")
    assert mixed["confidence"] < A.CONFIDENT
    assert A.classify_reply("круто, что субтитры крупные — так и делай")["text"] == "Субтитры крупные"


def test_changes_comment_becomes_rule_with_quote(env):
    _c, store, source, pid = env
    card = A.submit_clip(store, _clip(store, source["id"])["id"], title="x")
    res = A.decide(store, card["id"], "changes", comment="не ставь музыку поверх голоса", actor="ui")
    learned = res["learned"]
    assert learned["action"] == "rule_added"
    rule = P.get_rule(store, learned["rule"]["id"])
    assert rule["kind"] == "dont" and rule["quote"] == "не ставь музыку поверх голоса"
    assert rule["origin_ref"] == f"approval#{card['id']}" and rule["project_id"] == pid
    # Тот же урок второй раз — не дубль.
    card2 = A.submit_clip(store, _clip(store, source["id"])["id"], title="y")
    again = A.decide(store, card2["id"], "reject", comment="Не ставь музыку поверх голоса")["learned"]
    assert again["action"] == "duplicate"


def test_unsure_reply_becomes_rule_proposal(env):
    _c, store, source, pid = env
    card = A.submit_clip(store, _clip(store, source["id"])["id"], title="x")
    learned = A.decide(store, card["id"], "changes", comment="убери мат в этом клипе на 0:15")["learned"]
    assert learned["action"] == "rule_proposed"
    proposal = A.get_approval(store, learned["proposal_id"])
    assert proposal["kind"] == "rule_proposal" and "на будущее" in proposal["question"]
    assert not P.list_rules(store, project_id=pid)
    res = A.decide(store, proposal["id"], "approve", actor="tg:@owner")
    assert res["rule"]["created"] and res["rule"]["rule"]["quote"] == "убери мат в этом клипе на 0:15"


def test_revoke_phrase_proposes_and_revokes(env):
    _c, store, _source, pid = env
    old = P.add_rule(store, "Не брать политику", kind="dont", project_id=pid, quote="без политики")["rule"]
    learned = A.learn_from_text(store, pid, "я пересмотрел контент, правило про политику больше не работает")
    assert learned["action"] == "revoke_proposed" and learned["rule_id"] == old["id"]
    assert P.get_rule(store, old["id"])["status"] == "active"  # без «да» не отменяем
    proposal = A.get_approval(store, learned["proposal_id"])
    assert [b["action"] for b in proposal["buttons"]] == ["approve", "reject"]
    A.decide(store, proposal["id"], "approve")
    rule = P.get_rule(store, old["id"])
    assert rule["status"] == "revoked" and "пересмотрел" in rule["revoke_quote"]
    assert "больше не работает" in rule["revoke_reason"]
    assert A.learn_from_text(store, pid, "забудь правило про котиков")["action"] == "revoke_not_found"


def test_contradiction_asks_owner(env):
    _c, store, _source, pid = env
    old = P.add_rule(store, "Брать политику", kind="do", project_id=pid, quote="политика заходит")["rule"]
    learned = A.learn_from_text(store, pid, "не брать политику")
    assert learned["action"] == "conflict_proposed"
    card = A.get_approval(store, learned["proposal_id"])
    assert [b["action"] for b in card["buttons"]] == ["approve", "both", "reject"]
    res = A.decide(store, card["id"], "approve")
    assert res["rule"]["created"] and res["rule"]["superseded"] == [old["id"]]
    assert P.get_rule(store, old["id"])["status"] == "superseded"


# --------------------------------------------------------------------------- #
# Петля статистики и сводка
# --------------------------------------------------------------------------- #


def _numbers(store, source_id, views_tagged, views_plain):
    for views in views_tagged:
        plan = _clip(store, source_id, "Хук", tags=["hook_question"])
        P.add_manual_publication(store, f"https://www.tiktok.com/@m/video/1{plan['id']:04d}", clip_plan_id=plan["id"],
                                 stats={"views": views}, published_at="2026-11-02")
    for views in views_plain:
        plan = _clip(store, source_id, "Без хука")
        P.add_manual_publication(store, f"https://www.tiktok.com/@m/video/2{plan['id']:04d}", clip_plan_id=plan["id"],
                                 stats={"views": views}, published_at="2026-11-02")


def test_stats_loop_cards_promote_and_review(env):
    _c, store, source, pid = env
    hyp = P.add_hypothesis(store, pid, "Хук-вопрос даёт больше просмотров", "hook_question", min_n=3,
                           rule_text="Начинать клип с хука-вопроса")
    _numbers(store, source["id"], [5000, 6000, 7000], [1000, 1500, 2000])
    loop = A.after_stats(store)
    assert len(loop["cards"]) == 1
    card = A.get_approval(store, loop["cards"][0])
    assert card["kind"] == "hypothesis_review" and card["payload"]["action"] == "promote"
    assert A.after_stats(store)["cards"] == []  # та же карточка второй раз не приходит
    res = A.decide(store, card["id"], "approve")
    rule = res["rule"]["rule"]
    assert rule["hypothesis_id"] == hyp["id"] and rule["text"] == "Начинать клип с хука-вопроса"

    store.db.execute("UPDATE project_publication_stats SET views = 100 WHERE publication_id IN "
                     "(SELECT id FROM project_publications WHERE url LIKE '%/video/1%')")
    review = A.get_approval(store, A.after_stats(store)["cards"][0])
    assert review["payload"] == {"action": "review_rule", "rule_id": rule["id"], "hypothesis_id": hyp["id"]}
    A.decide(store, review["id"], "reject")
    assert P.get_rule(store, rule["id"])["status"] == "revoked"


def test_collect_stats_runs_the_loop(env, monkeypatch):
    _c, store, _source, _pid = env
    from app import platform_stats

    called = []
    monkeypatch.setattr(A, "after_stats", lambda s: called.append(s) or {"projects": 0, "cards": [7]})
    out = platform_stats.collect_stats(store, sleep=lambda _s: None, pause=0)
    assert called and out["autopilot_cards"] == [7]


def test_weekly_digest(env):
    _c, store, source, pid = env
    P.add_hypothesis(store, pid, "Хук-вопрос даёт больше просмотров", "hook_question", min_n=3)
    _numbers(store, source["id"], [50000, 6000, 7000], [1000, 1500, 2000])
    P.evaluate_hypotheses(store, pid)
    digest = A.weekly_digest(store, pid)
    assert digest["published"] == 6 and digest["hits"] and len(digest["top"]) == 3
    text = digest["text"]
    assert "Неделя проекта «Мура»" in text and "Топ:" in text and "Антитоп:" in text
    assert "Подтвердились гипотезы" in text
    A.update_config(store, pid, {"digest": {"enabled": True, "weekday": 2, "hour": 10}})
    assert A.digest_due(store, pid)  # среда 19:00 МСК, сводка по средам с 10:00
    A.send_digest(store, pid)
    assert not A.digest_due(store, pid)


# --------------------------------------------------------------------------- #
# Telegram (замокан)
# --------------------------------------------------------------------------- #


class FakeTG:
    calls: list = []
    updates: list = []
    _next = 100

    def __init__(self, token, proxy=""):
        self.token = token

    def call(self, method, data=None, files=None, timeout=30):
        FakeTG.calls.append((method, copy.deepcopy(data or {}), sorted((files or {}).keys())))
        if method == "getUpdates":
            out, FakeTG.updates = FakeTG.updates, []
            return out
        if method == "createForumTopic":
            return {"message_thread_id": 77}
        if method.startswith("send"):
            FakeTG._next += 1
            return {"message_id": FakeTG._next}
        return True


@pytest.fixture
def tg(env, monkeypatch):
    FakeTG.calls, FakeTG.updates = [], []
    monkeypatch.setattr(notify_tg, "TelegramClient", FakeTG)
    _c, store, _source, _pid = env
    notify_tg.save(store, token="123456:SECRET-TOKEN", chat_id="-1001", thread_id="5")
    return env


def _msg(text, reply_to=None, thread=None, chat=-1001, user=42):
    message = {"message_id": 500 + len(FakeTG.calls), "chat": {"id": chat}, "from": {"id": user, "username": "owner"},
               "text": text}
    if reply_to:
        message["reply_to_message"] = {"message_id": reply_to}
    if thread:
        message["message_thread_id"] = thread
    return {"update_id": 1, "message": message}


def test_tg_off_without_token_cards_only_in_ui(env):
    _c, store, source, _pid = env
    assert not notify_tg.is_enabled(store)
    card = A.submit_clip(store, _clip(store, source["id"])["id"], title="x")
    assert card["tg_state"] == "" and A.tick(store)["expired"] == 0


def test_tg_secret_encrypted_and_masked(tg):
    _c, store, _s, _p = tg
    raw = store.db.query_one("SELECT value_json, encrypted FROM app_settings WHERE key = ?", (notify_tg.KEY,))
    assert raw["encrypted"] == 1 and "SECRET" not in raw["value_json"]
    status = notify_tg.status(store)
    assert status["token"] == "123456:•••" and status["enabled"] and "SECRET" not in str(status)
    with pytest.raises(ValueError):
        notify_tg.save(store, token="nocolon")


def test_tg_card_sent_as_video_with_buttons(tg):
    _c, store, source, pid = tg
    card = A.submit_clip(store, _clip(store, source["id"])["id"], title="Мура пугается", description="лучшее")
    assert card["tg_state"] == "queued"
    assert notify_tg.send_queued_cards(store) == 1
    method, data, files = next(c for c in FakeTG.calls if c[0] == "sendVideo")
    assert files == ["video"] and data["message_thread_id"] == 5 and "Мура пугается" in data["caption"]
    buttons = [b[0]["callback_data"] for b in data["reply_markup"]["inline_keyboard"]]
    assert buttons == [f"vvf:{card['id']}:approve", f"vvf:{card['id']}:changes", f"vvf:{card['id']}:reject"]
    assert A.get_approval(store, card["id"])["tg_state"] == "sent"


def test_tg_callback_approve_and_changes_with_reply(tg):
    _c, store, source, pid = tg
    A.update_config(store, pid, {"platforms": ["youtube"]})
    one = A.submit_clip(store, _clip(store, source["id"])["id"], title="a")
    two = A.submit_clip(store, _clip(store, source["id"])["id"], title="b")
    notify_tg.send_queued_cards(store)
    card_msg = {"message_id": 999, "chat": {"id": -1001}, "message_thread_id": 5}
    FakeTG.updates = [
        {"update_id": 10, "callback_query": {"id": "q1", "from": {"id": 42}, "message": card_msg,
                                             "data": f"vvf:{one['id']}:approve"}},
        {"update_id": 11, "callback_query": {"id": "q2", "from": {"id": 42}, "message": card_msg,
                                             "data": f"vvf:{two['id']}:changes"}},
    ]
    assert notify_tg.poll_once(store, timeout=0) == 2
    assert store.get_app_setting_value(notify_tg.OFFSET_KEY) == 12
    assert A.get_approval(store, one["id"])["status"] == "approved" and len(_jobs(store)) == 1
    assert A.get_approval(store, two["id"])["decided_by"] == "tg:42"
    prompt = [c for c in FakeTG.calls if c[0] == "sendMessage" and (c[1].get("reply_markup") or {}).get("force_reply")]
    assert prompt, "бот спрашивает, что переделать"
    prompt_id = store.db.query_one("SELECT message_id FROM tg_messages WHERE purpose = 'comment'")["message_id"]
    notify_tg.handle_update(store, _msg("не ставь музыку поверх голоса", reply_to=prompt_id))
    got = A.get_approval(store, two["id"])
    assert got["status"] == "changes_requested" and "музыку" in got["decision_comment"]
    rules = P.list_rules(store, project_id=pid)
    assert rules and rules[0]["quote"] == "не ставь музыку поверх голоса" and rules[0]["origin"] == "tg"


def test_tg_text_reply_yes_and_foreign_chat(tg):
    _c, store, source, pid = tg
    A.update_config(store, pid, {"platforms": ["youtube"]})
    card = A.submit_clip(store, _clip(store, source["id"])["id"], title="a")
    notify_tg.send_queued_cards(store)
    card_msg_id = store.db.query_one("SELECT message_id FROM tg_messages WHERE approval_id = ?",
                                     (card["id"],))["message_id"]
    assert notify_tg.handle_update(store, _msg("да", reply_to=card_msg_id, chat=-999)) == {"ignored": "foreign or empty"}
    assert A.get_approval(store, card["id"])["status"] == "pending"
    notify_tg.handle_update(store, _msg("да", reply_to=card_msg_id))
    assert A.get_approval(store, card["id"])["status"] == "approved"
    # Свободный текст в общем треде — урок проекту последней карточки.
    out = notify_tg.handle_update(store, _msg("никогда не бери политику"))
    assert out["learned"]["action"] == "rule_added"


def test_tg_project_topic_and_chat_reply(tg, monkeypatch):
    _c, store, _source, pid = tg
    res = notify_tg.create_project_topic(store, pid)
    assert res["mode"] == "topic" and res["thread_id"] == 77
    monkeypatch.setattr("app.ai.editor_agent.client_factory", lambda: Scripted([[{"text": "Привет, я агент Муры."}]]))
    notify_tg.handle_update(store, _msg("как дела у канала?", thread=77))
    assert project_chat.wait(pid, 10)
    sent = [c for c in FakeTG.calls if c[0] == "sendMessage" and c[1].get("text") == "Привет, я агент Муры."]
    assert sent and sent[0][1]["message_thread_id"] == 77
    assert [m["role"] for m in project_chat.list_messages(store, pid)] == ["user", "assistant"]


def test_tg_api_settings_and_decide(tg):
    client, store, source, pid = tg
    status = client.get("/api/settings/telegram").json()
    assert status["token"] == "123456:•••" and status["chat_id"] == "-1001"
    card = A.submit_clip(store, _clip(store, source["id"])["id"], title="a")
    res = client.post(f"/api/approvals/{card['id']}/decide", json={"action": "reject", "comment": "без мата"})
    assert res.status_code == 200 and res.json()["learned"]["action"] == "rule_added"
    assert client.post(f"/api/approvals/{card['id']}/decide", json={"action": "approve"}).status_code == 400
    assert client.get("/api/approvals/counts").json()["rejected"] == 1
    cfg = client.put(f"/api/projects/{pid}/autopilot", json={"trust": {"meme": "auto"}}).json()
    assert cfg["trust"] == {"default": "approve", "meme": "auto"}
    assert client.put(f"/api/projects/{pid}/autopilot", json={"trust": {"meme": "maybe"}}).status_code == 400


# --------------------------------------------------------------------------- #
# MCP и плейбук
# --------------------------------------------------------------------------- #


def test_mcp_tools(env):
    _c, store, source, pid = env
    A.update_config(store, pid, {"platforms": ["youtube"], "slots": [{"days": [3], "times": ["18:00"]}]})
    plan = _clip(store, source["id"])
    res = mcp_call_tool(store, "submit_for_approval", {"clip_id": plan["id"], "title": "Мура", "tags": ["мура"]})
    assert not res["isError"] and res["structuredContent"]["approval"]["status"] == "pending"
    listed = mcp_call_tool(store, "list_approvals", {"source_id": source["id"], "status": "pending"})
    assert len(listed["structuredContent"]["approvals"]) == 1
    sched = mcp_call_tool(store, "get_schedule", {"project_id": pid})["structuredContent"]
    assert sched["free_slots"][0]["local"] == "2026-11-05 18:00"
    prop = mcp_call_tool(store, "propose_rule", {"project_id": pid, "text": "Не брать политику", "kind": "dont",
                                                 "quote": "политику не бери"})
    assert prop["structuredContent"]["approval"]["kind"] == "rule_proposal" and not P.list_rules(store, project_id=pid)


def test_autopilot_playbook():
    book = playbooks.get_playbook("autopilot")
    text = book["text"]
    for section in ("## Шаги", "## Инструменты MCP", "## Чек-лист перед сдачей"):
        assert section in text
    for must in ("get_project_brief", "approved", "trust=auto", "цитат"):
        assert must in text
    known = {t.name for t in list_tools(include_discovery=True)} | {"censor_profanity"}
    block = text.split("## Инструменты MCP", 1)[1].split("##", 1)[0]
    named = set(re.findall(r"`([a-z_]+)`", block))
    assert named and named <= known, sorted(named - known)


# --------------------------------------------------------------------------- #
# Чат проекта (замоканный Gemini)
# --------------------------------------------------------------------------- #


class Scripted:
    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []

    def generate_content(self, model, payload):
        self.requests.append(copy.deepcopy(payload))
        parts = self.turns.pop(0) if self.turns else [{"text": "ок"}]
        return {"candidates": [{"content": {"role": "model", "parts": parts}}]}


def test_chat_uses_project_tools_and_context(env):
    _c, store, _source, pid = env
    P.add_rule(store, "Не брать политику", kind="dont", project_id=pid, quote="без политики")
    gemini = Scripted([[{"functionCall": {"name": "get_schedule", "args": {}}}],
                       [{"text": "Политику не берём, расписание пустое."}]])
    project_chat.send(store, pid, "что у нас в расписании?", client=gemini)
    assert project_chat.wait(pid, 10)
    view = project_chat.chat_view(store, pid)
    reply = view["messages"][-1]
    assert reply["status"] == "done" and reply["text"] == "Политику не берём, расписание пустое."
    assert reply["steps"][0]["tool"] == "get_schedule" and reply["steps"][0]["ok"]
    first = gemini.requests[0]
    context = first["contents"][-1]["parts"][0]["text"]
    assert "Не брать политику" in context and "approvals_open" in context
    names = {d["name"] for d in first["tools"][0]["functionDeclarations"]}
    assert {"get_project_brief", "add_rule", "submit_for_approval", "get_schedule", "get_clip"} <= names
    assert "add_layers" not in names  # монтаж клипа — в редакторе, не в чате проекта


def test_chat_rule_confirm_then_yes(env):
    _c, store, _source, pid = env
    project_chat.send(store, pid, "такое больше не бери — политику", client=Scripted([[{"text": "Записать?"}]]))
    project_chat.wait(pid, 10)
    assert not P.list_rules(store, project_id=pid)  # auto_rules=confirm — сначала спросить
    proposal = A.list_approvals(store, pid, kind="rule_proposal")[0]
    assert proposal["status"] == "pending" and proposal["submitted_by"] == "chat"
    project_chat.send(store, pid, "да", client=Scripted([[{"text": "Записал."}]]))
    project_chat.wait(pid, 10)
    rules = P.list_rules(store, project_id=pid)
    assert rules and rules[0]["quote"] == "такое больше не бери — политику"


def test_chat_auto_rules_and_no_ai(env, monkeypatch):
    _c, store, _source, pid = env
    A.update_config(store, pid, {"auto_rules": "auto"})
    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr("app.ai.editor_agent.client_factory", None)
    project_chat.send(store, pid, "не ставь музыку поверх голоса")
    project_chat.wait(pid, 10)
    reply = project_chat.list_messages(store, pid)[-1]
    assert "недоступен" in reply["text"] and "Запомнил правило" in reply["text"]
    assert P.list_rules(store, project_id=pid)[0]["origin"] == "chat"


def test_chat_history_is_summarized(env, monkeypatch):
    _c, store, _source, pid = env
    for i in range(40):
        project_chat.add_message(store, pid, "user" if i % 2 == 0 else "assistant", f"реплика {i}")
    assert project_chat.maybe_summarize(store, pid, client=None)
    state = A._state_row(store, pid)
    assert "реплика 0" in state["chat_summary"] and state["chat_summary_upto"] > 0
    gemini = Scripted([[{"text": "ок"}]])
    project_chat.send(store, pid, "напомни, о чём говорили", client=gemini)
    project_chat.wait(pid, 10)
    request = gemini.requests[0]
    assert "Резюме прежней переписки" in request["systemInstruction"]["parts"][0]["text"]
    history_text = " ".join(p.get("text", "") for c in request["contents"][:-1] for p in c["parts"])
    assert "реплика 0" not in history_text and "реплика 39" in history_text
