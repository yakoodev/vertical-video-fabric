"""Канал объяснялок (app/explainer_channel.py): маскот, очередь тем (Gemini предлагает, дубли отсеиваются,
владелец берёт/отклоняет), ролики к слотам расписания, карточка одобрения с готовым роликом, публикация в
слот по «Публиковать», «Переделать» — пересборка с замечанием, уроки владельца — в промпт тем и сценария.
Сеть и деньги — подделки: Gemini — FakeChannelGemini, рендер — заглушка, провайдер — сухой прогон."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app import autopilot as A
from app import explainer as E
from app import explainer_channel as C
from app import explainer_gen as X
from app import projects as P
from app.agent_api import mcp_call_tool
from app.settings import settings
from test_explainer import SCRIPT, FakeGemini, _setup
from test_explainer import env as _expl_env  # noqa: F401 - фикстура среды объяснялок

# Среда, 4 ноября 2026, 16:00 UTC = 19:00 по Москве.
WED = datetime(2026, 11, 4, 16, 0, tzinfo=timezone.utc)
COOKIE = "SID=a; HSID=b; SSID=c; APISID=d; SAPISID=e"

TOPICS = [
    {"topic": "Почему мы зеваем", "angle": "зевота заразна даже от собаки", "why": "удивляет"},
    {"topic": "Три ошибки сна", "angle": "", "why": "уже сделано — дубль"},
    {"topic": "Зачем нужен дневной сон", "angle": "двадцать минут, не больше", "why": "польза"},
    {"topic": "Почему мы зеваем?", "angle": "дубль в той же пачке", "why": ""},
    {"topic": "Как перестать просыпаться в три ночи", "angle": "", "why": "боль"},
]


class FakeChannelGemini(FakeGemini):
    """Темы — TOPICS (с дублями), сценарий и план — как у объяснялок; запоминает контексты."""

    def __init__(self, topics=None):
        super().__init__(SCRIPT)
        self.topics = topics if topics is not None else TOPICS
        self.contexts: dict[str, list[dict]] = {"topics": [], "script": []}

    def generate_content(self, model, payload):
        props = payload["generationConfig"]["responseSchema"]["properties"]
        context = json.loads(payload["contents"][0]["parts"][1]["text"])
        if "topics" in props:
            self.calls.append("topics")
            self.contexts["topics"].append(context)
            return self._wrap({"topics": self.topics})
        if "points" in props:
            self.contexts["script"].append(context)
        return super().generate_content(model, payload)


@pytest.fixture
def env(_expl_env, monkeypatch):
    client, store, tmp = _expl_env
    monkeypatch.setattr(A, "now", lambda: WED)
    monkeypatch.delenv("AUTOPILOT_TG_TOKEN", raising=False)
    store.upsert_account("youtube", "yt-owl", COOKIE)

    def fake_finish(store_, video_id, clip_id, cover=True):  # рендер — заглушка с готовым файлом
        plan = store_.get_clip_plan(int(clip_id), include_segments=False)
        clip = store_.create_clip(plan["source_id"], clip_plan_id=int(clip_id), status="succeeded")
        out = settings.clip_dir / f"clip-{clip['id']}.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00" * 1024)
        store_.db.execute("UPDATE clips SET output_path = ? WHERE id = ?", (str(out), clip["id"]))
        return {"render": {"clip": clip["id"], "status": "succeeded"}}

    monkeypatch.setattr(X, "finish_publish", fake_finish)
    return client, store, tmp


def _channel(store, tmp, **kw):
    ids = _setup(store, tmp, music=False)
    # Уже сделанный ролик маскота — его тему Gemini повторять не должен.
    store.db.execute("INSERT INTO expl_videos (project_id, mascot_id, topic, status) VALUES (?, ?, ?, 'done')",
                     (ids["project_id"], ids["mascot_id"], "3 ошибки сна"))
    view = C.setup_channel(store, mascot_id=ids["mascot_id"], niche="сон и привычки простыми словами",
                           enabled=True, schedule={"times": ["19:00"], "per_day": 1, "platforms": ["youtube"]},
                           **kw)
    return ids, view


def test_setup_binds_mascot_and_schedule(env):
    _c, store, tmp = env
    ids, view = _channel(store, tmp)
    assert view["channel"]["project_id"] == ids["project_id"] and view["channel"]["mascot_id"] == ids["mascot_id"]
    assert view["channel"]["enabled"] is True and view["mascot"]["name"] == "Ух"
    cfg = A.get_config(store, ids["project_id"])
    assert cfg["slots"] == [{"days": [0, 1, 2, 3, 4, 5, 6], "times": ["19:00"]}]
    assert cfg["per_day"]["default"] == 1 and cfg["platforms"] == ["youtube"]
    # Два дня вперёд: чт и пт 19:00 МСК свободны — нужно 2 ролика.
    assert view["capacity"]["free_slots"] == 2 and view["capacity"]["need"] == 2
    assert not [w for w in view["warnings"] if "расписан" in w or "площадк" in w]
    # Новый проект-канал с чужим маскотом: вид «канал объяснялок».
    other = C.setup_channel(store, mascot_id=ids["mascot_id"], name="Ух: космос")
    assert other["project"]["kind"] == "explainer" and other["channel"]["enabled"] is False
    with pytest.raises(ValueError):
        C.setup_channel(store, project_id=P.create_project(store, "Стрим", kind="stream_channel")["id"],
                        mascot_id=ids["mascot_id"])


def test_topics_proposed_deduplicated_and_card_decides(env):
    _c, store, tmp = env
    ids, _view = _channel(store, tmp)
    pid = ids["project_id"]
    P.add_rule(store, "не бери темы про лекарства", kind="dont", project_id=pid, quote="не бери темы про лекарства")
    fake = FakeChannelGemini()
    out = C.tick(store, client=fake, run=True)["channels"][0]
    assert out["proposed"] == 3 and not out["started"]  # тем ещё никто не одобрил
    ctx = fake.contexts["topics"][0]
    assert "3 ошибки сна" in ctx["done_topics"] and ctx["niche"].startswith("сон")
    assert {"kind": "dont", "text": "не бери темы про лекарства"} in ctx["owner_rules"]
    proposed = C.list_topics(store, pid, "proposed")
    assert [t["topic"] for t in proposed] == ["Почему мы зеваем", "Зачем нужен дневной сон",
                                              "Как перестать просыпаться в три ночи"]
    card = A.get_approval(store, proposed[0]["card_id"])
    assert card["status"] == "pending" and [b["action"] for b in card["buttons"]] == ["approve", "reject"]
    # Пока пачка ждёт владельца — новых тем не просим.
    C.tick(store, client=fake, run=True)
    assert fake.calls.count("topics") == 1

    # Одну тему владелец отклонил сам с причиной — правило; остальные — «Взять все».
    res = C.decide_topic(store, proposed[2]["id"], "reject", "никогда не бери темы про бессонницу", actor="ui")
    assert res["topic"]["status"] == "rejected" and res["learned"]["action"] in ("rule_added", "rule_proposed")
    A.decide(store, card["id"], "approve")
    assert [t["topic"] for t in C.list_topics(store, pid, "approved")] == ["Почему мы зеваем",
                                                                           "Зачем нужен дневной сон"]
    # Отклонённую тему повторно не предложат: дубль.
    assert C.find_duplicate(store, pid, "как перестать просыпаться в три часа ночи")


def test_videos_made_ahead_submitted_and_published_in_slot(env):
    _c, store, tmp = env
    ids, _view = _channel(store, tmp)
    pid = ids["project_id"]
    C.add_topics(store, pid, ["Почему мы зеваем", "Зачем нужен дневной сон", "Почему коты спят днём"])
    P.add_rule(store, "не упоминай кофе", kind="dont", project_id=pid, quote="не упоминай кофе")
    fake = FakeChannelGemini(topics=[])
    out = C.tick(store, client=fake, run=True)["channels"][0]
    assert len(out["started"]) == 2, out  # свободных слотов на 2 дня — 2, max_parallel 2
    # Сценарий видит правила канала и нишу.
    sctx = fake.contexts["script"][0]
    assert {"kind": "dont", "text": "не упоминай кофе"} in sctx["owner_rules"] and sctx["niche"].startswith("сон")
    review = C.list_topics(store, pid, "review")
    assert len(review) == 2, [(t["topic"], t["status"], t["reason"]) for t in C.list_topics(store, pid)]
    card = A.get_approval(store, review[0]["approval_id"])
    assert card["kind"] == "clip_publish" and card["status"] == "pending" and card["content_type"] == "explainer"
    assert card["media_url"] and card["project_id"] == pid
    video = E.get_video(store, review[0]["video_id"])
    assert P.project_of_plan(store, int(video["clip_id"])) == pid
    assert not store.db.query_all("SELECT id FROM jobs")  # без «Публиковать» — ничего
    # Слоты заняты роликами на одобрении — третий не начинаем.
    assert C.capacity(store, pid)["start"] == 0

    res = A.decide(store, card["id"], "approve")
    assert res["placement"]["ok"] and res["placement"]["scheduled_local"] == "2026-11-05 19:00"
    C.tick(store, client=fake, run=True)
    assert C.get_topic(store, review[0]["id"])["status"] == "scheduled"
    # Публикация прошла — тема «опубликовано».
    job_id = A.get_approval(store, card["id"])["job_id"]
    store.db.execute("UPDATE jobs SET status = 'succeeded' WHERE id = ?", (job_id,))
    A.sync_publish_status(store)
    C.tick(store, client=fake, run=True)
    assert C.get_topic(store, review[0]["id"])["status"] == "published"


def test_changes_requested_rebuilds_with_owner_feedback(env):
    _c, store, tmp = env
    ids, _view = _channel(store, tmp, config={"max_parallel": 1, "lead_days": 1})
    pid = ids["project_id"]
    C.add_topics(store, pid, ["Почему мы зеваем"])
    fake = FakeChannelGemini(topics=[])
    C.tick(store, client=fake, run=True)
    topic = C.list_topics(store, pid, "review")[0]
    res = A.decide(store, topic["approval_id"], "changes", "крючок длинный, сделай короче и без слова кофе")
    assert res["status"] == "changes_requested"
    C.tick(store, client=fake, run=True)
    again = C.get_topic(store, topic["id"])
    assert again["attempts"] == 2 and again["status"] == "review" and again["video_id"] != topic["video_id"]
    assert fake.contexts["script"][-1]["owner_feedback"].startswith("крючок длинный")
    # Вторая «Переделать» при max_attempts 2 — тема снята.
    A.decide(store, again["approval_id"], "changes", "всё равно не то")
    C.tick(store, client=fake, run=True)
    assert C.get_topic(store, topic["id"])["status"] == "dropped"


def test_failed_video_asks_retry_and_no_gemini_note(env, monkeypatch):
    _c, store, tmp = env
    ids, _view = _channel(store, tmp)
    pid = ids["project_id"]
    C.add_topics(store, pid, ["Почему мы зеваем"])
    monkeypatch.setattr(X, "gemini_client", lambda: None)
    out = C.tick(store, client=None, run=True)["channels"][0]
    # Без Gemini сценарий не пишется: ролик failed → карточка «Сделать заново?».
    topic = C.get_topic(store, out["started"][0]["topic_id"])
    assert topic["status"] == "failed" and "Gemini" in topic["reason"]
    retry = [a for a in A.list_approvals(store, pid, status="pending") if a["payload"].get("action") == C.CARD_RETRY]
    assert len(retry) == 1
    A.decide(store, retry[0]["id"], "approve")
    assert C.get_topic(store, topic["id"])["status"] == "approved"
    # Темы кончились и Gemini нет — одна заметка владельцу в день.
    C.delete_topic(store, topic["id"])
    store.db.execute("UPDATE expl_channels SET last_proposed_at = NULL")
    C.tick(store, run=True)
    C.tick(store, run=True)
    notes = [a for a in A.list_approvals(store, pid) if a["payload"].get("action") == C.CARD_NOTE]
    assert len(notes) == 1 and "кончились" in notes[0]["question"]


def test_mcp_tools_and_api(env):
    client, store, tmp = env
    ids, _view = _channel(store, tmp)
    pid = ids["project_id"]
    res = mcp_call_tool(store, "explainer_channel_add_topics", {"project_id": pid, "topics": ["Почему мы зеваем"]})
    assert not res.get("isError"), res
    topic = C.list_topics(store, pid)[0]
    assert topic["status"] == "proposed" and topic["origin"] == "agent"
    res = mcp_call_tool(store, "explainer_channel_status", {"project_id": pid})
    assert not res.get("isError")
    r = client.get(f"/api/projects/{pid}/explainer-channel")
    assert r.status_code == 200 and r.json()["topics"][0]["topic"] == "Почему мы зеваем"
    r = client.post(f"/api/explainer/topics/{topic['id']}/decide", json={"action": "approve"})
    assert r.status_code == 200 and r.json()["topic"]["status"] == "approved"
    r = client.post(f"/api/projects/{pid}/explainer-channel/topics", json={"topics": ["Зачем нужен дневной сон"]})
    assert r.status_code == 200 and r.json()["added"][0]["status"] == "approved"
    r = client.put(f"/api/projects/{pid}/explainer-channel",
                   json={"enabled": False, "schedule": {"times": ["10:00", "20:00"], "per_day": 2}})
    assert r.status_code == 200 and r.json()["schedule"]["slots"][0]["times"] == ["10:00", "20:00"]
    assert r.json()["channel"]["enabled"] is False
