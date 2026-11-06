"""«Как работает агент» (app/agent_settings.py): четыре настройки проекта в обе стороны, значения по умолчанию,
деньги в пределах и сверх сумм, только владелец меняет, автопилот слушается, публикации нет без явного выбора.

Никаких настоящих вызовов: платный провайдер — подделка, публикует только ``JobWorker``, а он здесь не запускается."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import agent_settings as S
from app import aivideo_gen as G
from app import aivideo_providers as PR
from app import autopilot as A
from app import chat_tools as CT
from app import explainer_channel as EC
from app import project_chat as C
from app import projects as P
from app import stream_questions as Q
from app import vod_watch as W
from app.agent_tools import ToolContext, run_tool
from app.automation import AutomationService
from app.manual_clips import create_manual_clip
from app.settings import settings
from test_aivideo import FakePaid, _project
from test_api_pipeline import _client

BEARER = {"Authorization": "Bearer whatever"}
COOKIE_YT = "SID=a; HSID=b; SSID=c; APISID=d; SAPISID=e"
EDIT = ("set_end_hold", {"sec": 0})


def _source(store, name="a.mp4"):
    return store.create_source("upload", settings.source_dir / name, original_filename=name, duration_sec=600, status="ready")


@pytest.fixture
def env(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    monkeypatch.delenv("AUTOPILOT_TG_TOKEN", raising=False)
    source = _source(store)
    pid = P.create_project(store, "Мура", kind="stream_channel", source_ids=[source["id"]])["id"]
    other_source = _source(store, "b.mp4")
    other = P.create_project(store, "Apex", kind="stream_channel", source_ids=[other_source["id"]])["id"]
    return SimpleNamespace(client=client, store=store, sid=source["id"], pid=pid, other_sid=other_source["id"], other=other)


def _draft(store, source_id, title="Нашёл анализ"):
    """Черновик из анализа: тот самый «момент», строка clip_plans без готового файла."""
    analysis = store.create_ai_analysis(source_id, "gemini", status="succeeded")
    seg = store.create_ai_segment(analysis["id"], {"start_sec": 10, "end_sec": 25, "title": title})
    return store.create_clip_plan(source_id, analysis["id"], title, segment_ids=[seg["id"]])


def _rendered(store, source_id, title="Готовый клип", tags=()):
    plan = create_manual_clip(store, source_id, title, [{"start_sec": 1, "end_sec": 20}])
    clip = store.create_clip(source_id, clip_plan_id=plan["id"], status="succeeded")
    out = settings.clip_dir / f"clip-{clip['id']}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"\x00" * 1024)
    store.db.execute("UPDATE clips SET output_path = ? WHERE id = ?", (str(out), clip["id"]))
    if tags:
        P.set_clip_tags(store, plan["id"], add=list(tags))
    return plan


def _ask(store, source_id):
    return Q.add_questions(store, source_id, [{"kind": "context", "t_start": 2, "t_end": 5, "question": "Что тут происходит?",
                                               "options": ["Шутка", "Серьёзно"]}])["added"][0]


def _edit(store, plan_id):
    return run_tool(ToolContext(store=store, clip_plan_id=plan_id), *EDIT)


def _jobs(store):
    return [dict(r) for r in store.db.query_all("SELECT * FROM jobs ORDER BY id")]


# --------------------------------------------------------------------------- #
# Значения по умолчанию
# --------------------------------------------------------------------------- #


def test_defaults_are_the_strict_ones_and_nothing_is_written(env):
    data = S.get_settings(env.store, env.pid)
    assert data["wait_answers"] is True and data["stop_at_clip_list"] is True
    assert data["spend"] == {"enabled": False, "per_run_rub": 0.0, "per_month_rub": 0.0}
    assert data["publish"]["enabled"] is False and data["publish"]["account_ids"] == []
    assert not env.store.db.query_all("SELECT * FROM project_agent_settings")
    # Испорченная запись не делает проект свободнее.
    env.store.db.execute("INSERT INTO project_agent_settings (project_id, settings_json) VALUES (?, ?)",
                         (env.pid, '{"publish": {"enabled": true, "privacy": "неведомо"}}'))
    assert S.get_settings(env.store, env.pid)["publish"]["enabled"] is False
    # Без проекта: как раньше, ничего сверх пауз.
    assert S.get_settings(env.store, None) == S.defaults()


def test_save_merges_and_validates(env):
    S.save_settings(env.store, env.pid, {"spend": {"enabled": True, "per_run_rub": 50, "per_month_rub": 200}})
    assert S.get_settings(env.store, env.pid)["wait_answers"] is True
    assert S.get_settings(env.store, env.other) == S.defaults()  # другой проект не задет
    with pytest.raises(ValueError):
        S.save_settings(env.store, env.pid, {"spend": {"enabled": True, "per_run_rub": 0, "per_month_rub": 0}})
    with pytest.raises(ValueError):
        S.save_settings(env.store, env.pid, {"spend": {"enabled": True, "per_run_rub": 300, "per_month_rub": 200}})
    with pytest.raises(ValueError):
        S.save_settings(env.store, env.pid, {"publish": {"privacy": "кому угодно"}})
    events = [e for e in A.list_events(env.store, env.pid) if e["event"] == "agent_settings"]
    assert events and S.NAME_SPEND in events[0]["note"]


# --------------------------------------------------------------------------- #
# Ждать ответов
# --------------------------------------------------------------------------- #


def test_wait_answers_both_ways_and_refusal_names_the_setting(env):
    store = env.store
    _ask(store, env.sid)
    _ask(store, env.other_sid)
    refusal = Q.montage_refusal(store, env.sid)
    assert refusal["setting"] == "wait_answers" and S.NAME_WAIT in refusal["error"] and "Как работает агент" in refusal["error"]
    result, ok = run_tool(ToolContext(store=store), "create_clip", {"source_id": env.sid, "title": "x",
                                                                    "pieces": [{"start_sec": 1, "end_sec": 9}]})
    assert not ok and result["paused"] == "open_questions"
    denied = env.client.post(f"/api/sources/{env.sid}/analyze", json={}, headers=BEARER)
    assert denied.status_code == 409 and S.NAME_WAIT in denied.json()["detail"]

    S.save_settings(store, env.pid, {"wait_answers": False})
    gate = Q.montage_gate(store, env.sid)
    assert gate["paused"] is False and gate["waits"] is False and gate["open"] == 1
    assert Q.montage_refusal(store, env.sid) is None
    assert "не ждёт ответов" in Q.answers_brief(store, env.sid)["montage"]["note"]
    # Другой проект по-прежнему ждёт: настройка у каждого своя.
    assert Q.montage_gate(store, env.other_sid)["paused"] is True

    S.save_settings(store, env.pid, {"wait_answers": True})
    assert Q.montage_gate(store, env.sid)["paused"] is True


def test_source_without_project_still_pauses_as_before(env):
    loose = _source(env.store, "loose.mp4")
    _ask(env.store, loose["id"])
    assert Q.montage_gate(env.store, loose["id"])["paused"] is True


# --------------------------------------------------------------------------- #
# Список клипов
# --------------------------------------------------------------------------- #


def test_stop_at_list_both_ways(env):
    store = env.store
    draft = _draft(store, env.sid)
    result, ok = _edit(store, draft["id"])
    assert not ok and result["paused"] == "clip_list" and S.NAME_STOP in result["error"] and "Как работает агент" in result["error"]
    # Смотреть можно: стоп только на правке.
    assert run_tool(ToolContext(store=store, clip_plan_id=draft["id"]), "get_clip", {})[1] is True

    S.save_settings(store, env.pid, {"stop_at_clip_list": False})
    assert S.clip_list_refusal(store, draft["id"]) is None
    assert _edit(store, draft["id"])[1] is True

    S.save_settings(store, env.pid, {"stop_at_clip_list": True})
    assert _edit(store, draft["id"])[1] is False
    # Другой проект: своя настройка.
    S.save_settings(store, env.other, {"stop_at_clip_list": False})
    assert S.clip_list_refusal(store, _draft(store, env.other_sid)["id"]) is None


def test_only_owner_picks_a_draft(env):
    store, client = env.store, env.client
    draft = _draft(store, env.sid)
    denied = client.post(f"/api/clip-plans/{draft['id']}/auto-edit", json={}, headers=BEARER)
    assert denied.status_code == 409 and S.NAME_STOP in denied.json()["detail"]
    assert client.post(f"/api/clip-plans/{draft['id']}/render", json={}, headers=BEARER).status_code == 409
    assert client.put(f"/api/clip-plans/{draft['id']}/render-settings", json={}, headers=BEARER).status_code == 409
    # Смотреть по токену можно, а ★ агента выбором не считается.
    assert client.get(f"/api/clip-plans/{draft['id']}/timeline", headers=BEARER).status_code == 200
    assert client.patch(f"/api/clip-plans/{draft['id']}/flags", json={"favorite": True}, headers=BEARER).status_code == 200
    assert S.clip_list_refusal(store, draft["id"]) is not None
    assert client.post(f"/api/clip-plans/{draft['id']}/auto-edit", json={}, headers=BEARER).status_code == 409

    # ★ владельца — выбор; снял ★, выбор снят.
    assert client.patch(f"/api/clip-plans/{draft['id']}/flags", json={"favorite": True}).status_code == 200
    assert S.clip_list_refusal(store, draft["id"]) is None
    assert _edit(store, draft["id"])[1] is True
    assert client.patch(f"/api/clip-plans/{draft['id']}/flags", json={"favorite": False}).status_code == 200
    assert S.clip_list_refusal(store, draft["id"]) is not None


def test_segment_edit_is_a_clip_edit(env):
    store, client = env.store, env.client
    draft = _draft(store, env.sid)
    segment = store.get_clip_plan(draft["id"])["segments"][0]["id"]
    url = f"/api/segments/{segment}/timecodes"
    assert client.patch(url, json={"start_sec": 11, "end_sec": 24}, headers=BEARER).status_code == 409
    assert client.patch(url, json={"start_sec": 11, "end_sec": 24}).status_code == 200
    assert S.clip_list_refusal(store, draft["id"]) is None


def test_owner_edit_takes_the_draft_into_work(env):
    store, client = env.store, env.client
    draft = _draft(store, env.sid)
    client.post(f"/api/clip-plans/{draft['id']}/auto-edit", json={})
    assert S.clip_list_refusal(store, draft["id"]) is None
    other = _draft(store, env.sid, "Просмотр не выбор")
    client.post(f"/api/clip-plans/{other['id']}/levels", json={})
    assert S.clip_list_refusal(store, other["id"]) is not None


def test_manual_clip_and_rendered_clip_work_as_before(env):
    store = env.store
    manual = create_manual_clip(store, env.sid, "По заданию", [{"start_sec": 1, "end_sec": 20}])
    assert S.clip_list_refusal(store, manual["id"]) is None and _edit(store, manual["id"])[1] is True
    draft = _draft(store, env.sid)
    store.create_clip(env.sid, clip_plan_id=draft["id"], status="succeeded")
    assert S.clip_list_refusal(store, draft["id"]) is None


def test_batch_and_compilation_refuse_unpicked_drafts_for_agents(env):
    store, client = env.store, env.client
    draft = _draft(store, env.sid)
    body = {"clip_plan_ids": [draft["id"]]}
    assert client.post(f"/api/sources/{env.sid}/render-plans", json=body, headers=BEARER).status_code == 409
    assert client.post(f"/api/sources/{env.sid}/ai-montage-batch", json=body, headers=BEARER).status_code == 409
    result, ok = run_tool(ToolContext(store=store), "make_compilation", {"clip_ids": [draft["id"]], "title": "ТОП"})
    assert not ok and result["paused"] == "clip_list"
    result, ok = run_tool(ToolContext(store=store), "create_clip", {"source_id": env.sid, "version_of": draft["id"], "title": "v2",
                                                                    "pieces": [{"start_sec": 1, "end_sec": 9}]})
    assert not ok and result["paused"] == "clip_list"


def test_chat_turns_the_stop_into_owners_button(env):
    store = env.store
    draft = _draft(store, env.sid)
    args = {"clip_id": draft["id"], "sec": 0}
    assert CT.is_dangerous(store, "set_end_hold", args, env.pid) is True
    assert CT.danger_label("set_end_hold") == "Взять черновик в работу"
    assert CT.is_dangerous(store, "get_clip", {"clip_id": draft["id"]}, env.pid) is False
    thread = C.default_thread(store, env.pid)
    message = C.add_thread_message(store, thread["id"], "assistant", "жду")
    cur = store.db.execute("INSERT INTO chat_confirmations (thread_id, message_id, tool, args_json, label) VALUES (?, ?, ?, ?, ?)",
                           (thread["id"], message, "set_end_hold", C._dumps(args), "Взять черновик в работу"))
    out = C.decide_confirmation(store, int(cur.lastrowid), True)
    assert out["confirmation"]["status"] == "done"
    assert S.clip_list_refusal(store, draft["id"]) is None
    assert CT.is_dangerous(store, "set_end_hold", args, env.pid) is False


def test_drafts_are_clips_with_a_status(env):
    store = env.store
    draft = _draft(store, env.sid)
    card = next(c for c in env.client.get(f"/api/clip-cards?source_id={env.sid}").json()["clips"] if c["id"] == draft["id"])
    assert card["status"] == "no_render" and card["picked"] is False
    env.client.patch(f"/api/clip-plans/{draft['id']}/flags", json={"favorite": True})
    card = next(c for c in env.client.get(f"/api/clip-cards?project_id={env.pid}").json()["clips"] if c["id"] == draft["id"])
    assert card["picked"] is True
    listed = run_tool(ToolContext(store=store), "list_clips", {"source_id": env.sid})[0]["clips"]
    assert listed[0]["draft"] is True and listed[0]["waits_owner"] is False


# --------------------------------------------------------------------------- #
# Траты
# --------------------------------------------------------------------------- #


@pytest.fixture
def paid(tmp_path, monkeypatch):
    fake = FakePaid()
    monkeypatch.setattr(G, "BALANCE_SETTLE_SEC", 0.0)  # подделка списывает сразу: паузу не ждём
    monkeypatch.setattr(settings, "aivideo_enabled", True)
    monkeypatch.setattr(settings, "aivideo_month_cap_rub", 3000.0)
    monkeypatch.setattr(settings, "aivideo_max_budget_rub", 2500.0)
    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr(settings, "polza_api_key", "")
    monkeypatch.setattr(PR, "MEASURED_IMAGE_PRICES", {})
    monkeypatch.setattr(PR, "get_image_provider", lambda name: fake if name == "polza" else PR.DryRunImages())
    _client_, store = _client(tmp_path, monkeypatch)
    return store, fake


def _spend(store, pid, per_run, per_month):
    S.save_settings(store, pid, {"spend": {"enabled": True, "per_run_rub": per_run, "per_month_rub": per_month}})


def test_spend_off_is_the_card_as_today(paid):
    store, fake = paid
    pid, _sid = _project(store, provider="polza")
    req = G.request_stage(store, pid, "refs", requested_by="agent")
    assert req["status"] == "awaiting_approval" and S.NAME_SPEND in req["note"] and "Как работает агент" in req["note"]
    assert A.get_approval(store, req["approval_id"])["status"] == "pending"
    G.run_pending(store)
    assert fake.posts == 0


def test_spend_within_amount_runs_through_the_same_guard_and_is_recorded(paid):
    store, fake = paid
    pid, _sid = _project(store, provider="polza")
    _spend(store, pid, 60, 100)
    req = G.request_stage(store, pid, "refs", requested_by="agent")  # 54 ₽
    assert req["status"] == "approved" and req["approved_by_setting"] is True and S.NAME_SPEND in req["note"]
    card = A.get_approval(store, req["approval_id"])
    assert card["status"] == "approved" and card["decided_by"] == S.SPEND_ACTOR and S.NAME_SPEND in card["decision_comment"]
    assert card["tg_state"] == ""
    out = G.run_pending(store)
    assert len(out["done"]) == 9 and fake.posts == 9
    led = G.ledger(store, pid)
    assert len(led) == 9 and sum(l["amount_rub"] for l in led) == pytest.approx(54.0)
    assert all("настройка проекта" in l["note"] for l in led)
    view = S.view(store, pid)
    assert view["spend_state"]["spent_month_rub"] == pytest.approx(54.0)
    assert view["spend_state"]["recent"][0]["approval_id"] == req["approval_id"]


def test_spend_above_the_amount_gets_the_card_and_says_why(paid):
    store, fake = paid
    pid, _sid = _project(store, provider="polza")
    _spend(store, pid, 10, 100)
    req = G.request_stage(store, pid, "refs", requested_by="agent")
    assert req["status"] == "awaiting_approval" and "больше суммы за раз" in req["note"]
    assert "больше суммы за раз" in A.get_approval(store, req["approval_id"])["question"]
    G.run_pending(store)
    assert fake.posts == 0


def test_spend_stops_at_the_month_amount_in_one_project_not_another(paid):
    store, fake = paid
    pid, _sid = _project(store, provider="polza")
    other, _other_sid = _project(store, provider="polza")
    _spend(store, pid, 60, 60)
    _spend(store, other, 60, 600)
    first = G.request_stage(store, pid, "refs", requested_by="agent")
    assert first["status"] == "approved"
    G.run_pending(store)
    assert fake.posts == 9
    # Новая версия: ещё 6 ₽ сверх 60 за месяц (54 + 6 влезает), а вторая уже нет.
    ref = store.db.query_one("SELECT id FROM aiv_refs WHERE project_id = ? AND kind = 'style' LIMIT 1", (pid,))
    fits = G.regenerate(store, ref_id=int(ref["id"]))
    assert fits["status"] == "approved"
    G.run_pending(store)
    over = G.regenerate(store, ref_id=int(ref["id"]))
    assert over["status"] == "awaiting_approval" and "за месяц" in over["note"]
    # Другой проект своим лимитом не задет.
    assert G.request_stage(store, other, "refs", requested_by="agent")["status"] == "approved"


def test_month_cap_of_the_guard_holds_regardless_of_the_setting(paid, monkeypatch):
    store, fake = paid
    pid, _sid = _project(store, provider="polza")
    monkeypatch.setattr(settings, "aivideo_month_cap_rub", 40.0)
    _spend(store, pid, 1000, 1000)
    with pytest.raises(G.BudgetError):
        G.request_stage(store, pid, "refs", requested_by="agent")
    assert fake.posts == 0 and not store.db.query_all("SELECT * FROM aiv_gen_jobs")


def test_turning_it_off_after_approval_holds_the_runs(paid):
    store, fake = paid
    pid, _sid = _project(store, provider="polza")
    _spend(store, pid, 60, 100)
    req = G.request_stage(store, pid, "refs", requested_by="agent")
    S.save_settings(store, pid, {"spend": {"enabled": False}})
    out = G.run_pending(store)
    assert fake.posts == 0 and out["held"]
    held = G.list_jobs(store, approval_id=req["approval_id"])
    assert {j["status"] for j in held} == {"held"} and any(S.NAME_SPEND in j["error"] for j in held)


def test_paid_generation_switched_off_still_gets_the_card(paid, monkeypatch):
    store, _fake = paid
    pid, _sid = _project(store, provider="polza")
    _spend(store, pid, 60, 100)
    monkeypatch.setattr(settings, "aivideo_enabled", False)
    assert G.request_stage(store, pid, "refs", requested_by="agent")["status"] == "awaiting_approval"


# --------------------------------------------------------------------------- #
# Публикация
# --------------------------------------------------------------------------- #


def test_no_publish_without_explicit_account(env):
    store = env.store
    store.upsert_account("youtube", "yt-one", COOKIE_YT)
    A.update_config(store, env.pid, {"platforms": ["youtube"], "trust": {"default": "auto"}})
    plan = _rendered(store, env.sid)
    # Единственный аккаунт и даже старое доверие auto: не явный выбор, значит карточка.
    card = A.submit_clip(store, plan["id"], title="x", submitted_by="agent")
    assert card["status"] == "pending" and not _jobs(store) and S.NAME_PUBLISH in card["publish_decision"]
    with pytest.raises(ValueError):
        S.save_settings(store, env.pid, {"publish": {"enabled": True, "account_ids": []}})
    with pytest.raises(ValueError):
        S.save_settings(store, env.pid, {"publish": {"enabled": True, "account_ids": [9999]}})
    assert not _jobs(store)


def test_publish_only_to_chosen_account_with_chosen_privacy(env):
    store = env.store
    chosen = store.upsert_account("youtube", "yt-main", COOKIE_YT)
    store.upsert_account("youtube", "yt-second", COOKIE_YT)
    store.upsert_account("tiktok", "tt-main", "sessionid=a; sid_tt=b")
    S.save_settings(store, env.pid, {"publish": {"enabled": True, "account_ids": [chosen["id"]], "privacy": "unlisted"}})
    plan = _rendered(store, env.sid)
    card = A.submit_clip(store, plan["id"], title="Клип", submitted_by="agent")
    assert card["status"] == "approved" and card["decided_by"] == S.PUBLISH_ACTOR and card["platforms"] == ["youtube"]
    jobs = _jobs(store)
    assert len(jobs) == 1 and jobs[0]["made_by"] == S.MADE_BY_AGENT and jobs[0]["privacy"] == "unlisted"
    targets = store.get_job(jobs[0]["id"])["targets"]
    assert [t["account_id"] for t in targets] == [chosen["id"]]
    listed = env.client.get("/api/jobs").json()
    assert listed[0]["made_by"] == "agent"
    # Другая площадка не выбрана: карточка, а не публикация.
    other = _rendered(store, env.sid, "Другой")
    asked = A.submit_clip(store, other["id"], title="Другой", platforms=["tiktok"], submitted_by="agent")
    assert asked["status"] == "pending" and len(_jobs(store)) == 1 and "tiktok" in asked["publish_decision"]
    # Владелец сам отправил карточку: решает кнопкой, не настройка.
    own = _rendered(store, env.sid, "Свой")
    assert A.submit_clip(store, own["id"], title="Свой", submitted_by="ui")["status"] == "pending"
    # Тип «всегда спрашивать» сильнее настройки.
    A.update_config(store, env.pid, {"trust": {"ad": "approve"}})
    ad = _rendered(store, env.sid, "Реклама")
    assert A.submit_clip(store, ad["id"], title="Реклама", content_type="ad", submitted_by="agent")["status"] == "pending"
    assert len(_jobs(store)) == 1
    # Настройка в другом проекте ничего не разрешает здесь и наоборот.
    foreign = _rendered(store, env.other_sid, "Чужой")
    assert A.submit_clip(store, foreign["id"], title="Чужой", platforms=["youtube"], submitted_by="agent")["status"] == "pending"


def test_turning_publish_off_cancels_what_agent_queued(env):
    store = env.store
    chosen = store.upsert_account("youtube", "yt-main", COOKIE_YT)
    S.save_settings(store, env.pid, {"publish": {"enabled": True, "account_ids": [chosen["id"]]}})
    card = A.submit_clip(store, _rendered(store, env.sid)["id"], title="Клип", submitted_by="agent")
    view = S.save_settings(store, env.pid, {"publish": {"enabled": False}})
    assert view["cancelled_publications"] == 1
    assert A.get_approval(store, card["id"])["publish_status"] == "cancelled"
    assert _jobs(store)[0]["status"] == "cancelled"
    fresh = A.submit_clip(store, _rendered(store, env.sid, "Новый")["id"], title="Новый", submitted_by="agent")
    assert fresh["status"] == "pending" and len(_jobs(store)) == 1


def test_deleted_account_means_no_publish(env):
    store = env.store
    chosen = store.upsert_account("youtube", "yt-main", COOKIE_YT)
    S.save_settings(store, env.pid, {"publish": {"enabled": True, "account_ids": [chosen["id"]]}})
    store.delete_account(chosen["id"])
    card = A.submit_clip(store, _rendered(store, env.sid)["id"], title="Клип", submitted_by="agent")
    assert card["status"] == "pending" and not _jobs(store)


def test_per_day_limit_moves_the_second_clip_to_another_day(env, monkeypatch):
    from datetime import datetime, timezone

    store = env.store
    monkeypatch.setattr(A, "now", lambda: datetime(2026, 11, 4, 9, 0, tzinfo=timezone.utc))
    chosen = store.upsert_account("youtube", "yt-main", COOKIE_YT)
    S.save_settings(store, env.pid, {"publish": {"enabled": True, "account_ids": [chosen["id"]], "per_day": 1}})
    one = A.submit_clip(store, _rendered(store, env.sid, "Первый")["id"], title="Первый", submitted_by="agent")
    two = A.submit_clip(store, _rendered(store, env.sid, "Второй")["id"], title="Второй", submitted_by="agent")
    assert one["scheduled_at"][:10] == "2026-11-04" and two["scheduled_at"][:10] == "2026-11-05"


# --------------------------------------------------------------------------- #
# Только владелец
# --------------------------------------------------------------------------- #


def test_agents_can_read_but_not_change(env):
    client, store = env.client, env.store
    url = f"/api/projects/{env.pid}/agent-settings"
    assert client.get(url, headers=BEARER).status_code == 200
    denied = client.put(url, json={"wait_answers": False}, headers=BEARER)
    assert denied.status_code == 403 and "владелец" in denied.json()["detail"]
    assert S.get_settings(store, env.pid)["wait_answers"] is True
    # Тот же маршрут без токена (браузер) меняет; токен с куки входа — тоже владелец.
    assert client.put(url, json={"wait_answers": False}).status_code == 200
    assert S.get_settings(store, env.pid)["wait_answers"] is False
    client.cookies.set(settings.auth_cookie_name, "t")
    try:
        assert client.put(url, json={"wait_answers": True}, headers=BEARER).status_code == 200
    finally:
        client.cookies.clear()
    assert S.get_settings(store, env.pid)["wait_answers"] is True
    assert client.get("/api/projects/999999/agent-settings").status_code == 404


def test_agents_cannot_go_around_the_settings(env):
    client, store = env.client, env.store
    assert client.put(f"/api/projects/{env.pid}/autopilot", json={"trust": {"default": "auto"}}, headers=BEARER).status_code == 403
    plan = _rendered(store, env.sid)
    card = A.submit_clip(store, plan["id"], title="x", platforms=["youtube"], submitted_by="agent")
    assert client.post(f"/api/approvals/{card['id']}/decide", json={"action": "approve"}, headers=BEARER).status_code == 403
    assert client.post(f"/api/approvals/{card['id']}/schedule", json={"action": "cancel"}, headers=BEARER).status_code == 403
    assert client.post(f"/api/ai-video/approvals/{card['id']}/decide", json={"action": "approve"}, headers=BEARER).status_code == 403
    assert client.post("/api/chat/confirmations/1", json={"approve": True}, headers=BEARER).status_code == 403
    clip = store.list_clips_for_plan(plan["id"])[0]
    body = {"title": "x", "description": "", "targets": [1], "privacy": "public"}
    denied = client.post(f"/api/clips/{clip['id']}/posts", json=body, headers=BEARER)
    assert denied.status_code == 403 and S.NAME_PUBLISH in denied.json()["detail"]
    assert client.post("/api/clips/posts-batch", json={"clip_ids": [clip["id"]], "targets": [1]}, headers=BEARER).status_code == 403
    assert client.post("/ui/posts", headers=BEARER).status_code == 403
    assert not _jobs(store)
    # Агент по REST: карточка от его имени, а не от имени владельца.
    sent = client.post("/api/approvals", json={"clip_plan_id": plan["id"], "title": "x"}, headers=BEARER).json()
    assert sent["submitted_by"] == "agent" and sent["status"] == "pending"


def test_wrong_token_gets_401_not_the_settings_text(env, monkeypatch):
    client, store = env.client, env.store
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "api_token", "secret-token", raising=False)
    monkeypatch.setattr(settings, "ensure_api_token", lambda: "secret-token")
    draft = _draft(store, env.sid)
    wrong = {"Authorization": "Bearer nope"}
    assert client.post(f"/api/clip-plans/{draft['id']}/auto-edit", json={}, headers=wrong).status_code == 401
    assert client.put(f"/api/projects/{env.pid}/agent-settings", json={}, headers=wrong).status_code == 401
    good = {"Authorization": "Bearer secret-token"}
    assert client.post(f"/api/clip-plans/{draft['id']}/auto-edit", json={}, headers=good).status_code == 409
    assert client.put(f"/api/projects/{env.pid}/agent-settings", json={}, headers=good).status_code == 403


# --------------------------------------------------------------------------- #
# Автопилот
# --------------------------------------------------------------------------- #


def _service(store, analysis=None):
    renders: list[int] = []

    def render_clip_plan(plan_id, **_kwargs):
        renders.append(plan_id)
        clip = store.create_clip(store.get_clip_plan(plan_id, include_segments=False)["source_id"], clip_plan_id=plan_id,
                                 status="succeeded")
        out = settings.clip_dir / f"clip-{clip['id']}.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00" * 1024)
        store.db.execute("UPDATE clips SET output_path = ? WHERE id = ?", (str(out), clip["id"]))
        return store.get_clip(clip["id"])

    service = AutomationService(store, None, SimpleNamespace(run_analysis=analysis or (lambda *a, **k: {})),
                                SimpleNamespace(render_clip_plan=render_clip_plan))
    return service, renders


def _run(service, sid, **kwargs):
    run = service._new_run(label="t")
    service._run(run, "", {}, {"provider": "mock"}, {}, {"targets": [], "max_clips": 0, "submit": True}, source_id=sid, **kwargs)
    return run


def test_pipeline_stops_at_the_list_by_default_and_names_the_setting(env):
    store = env.store
    draft = _draft(store, env.sid)
    service, renders = _service(store)
    run = _run(service, env.sid)
    assert run["status"] == "done" and run["stopped_at_list"] is True and not renders
    assert S.NAME_STOP in run["message"] and "Как работает агент" in run["message"]
    assert not store.list_clips_for_plan(draft["id"]) and not A.list_approvals(store, env.pid)
    assert "clip_list_stop" in [e["event"] for e in A.list_events(store, env.pid)]


def test_pipeline_goes_on_when_switched_off_and_still_only_cards(env):
    store = env.store
    draft = _draft(store, env.sid)
    A.update_config(store, env.pid, {"platforms": ["youtube"]})
    S.save_settings(store, env.pid, {"stop_at_clip_list": False})
    service, renders = _service(store)
    run = _run(service, env.sid)
    assert run["status"] == "done" and renders == [draft["id"]] and not run.get("stopped_at_list")
    assert A.list_approvals(store, env.pid, status="pending")[0]["clip_plan_id"] == draft["id"]
    assert not _jobs(store)


def test_owner_started_run_goes_on_and_picks(env):
    store = env.store
    draft = _draft(store, env.sid)
    service, renders = _service(store)
    run = _run(service, env.sid, owner_started=True)
    assert run["status"] == "done" and renders == [draft["id"]]
    assert S.clip_list_refusal(store, draft["id"]) is None


def test_pipeline_waits_for_answers(env):
    store = env.store
    _draft(store, env.sid)
    _ask(store, env.sid)
    called = []
    service, _renders = _service(store, analysis=lambda *a, **k: called.append(1) or {})
    run = _run(service, env.sid)
    assert run["status"] == "failed" and S.NAME_WAIT in run["error"] and not called
    S.save_settings(store, env.pid, {"wait_answers": False})
    run = _run(service, env.sid)
    assert run["status"] == "done" and called


def test_vod_watcher_holds_the_recording_until_answered(env):
    store = env.store
    _ask(store, env.sid)
    store.db.execute("INSERT INTO vod_watch_items (project_id, vod_id, url, status, source_id) VALUES (?, ?, ?, ?, ?)",
                     (env.pid, "v1", "https://twitch.tv/videos/1", "downloaded", env.sid))
    started = []
    automation = SimpleNamespace(start_run=lambda **k: started.append(k) or {"id": 1}, get_run=lambda _id: None)
    W.advance(store, SimpleNamespace(), automation)
    item = dict(store.db.query_one("SELECT * FROM vod_watch_items WHERE vod_id = 'v1'"))
    assert not started and item["status"] == "downloaded" and S.NAME_WAIT in item["detail"]
    S.save_settings(store, env.pid, {"wait_answers": False})
    W.advance(store, SimpleNamespace(), automation)
    assert started and "owner_started" not in started[0]  # наблюдатель не выбор владельца


def test_topics_channel_respects_the_list(env, monkeypatch):
    store = env.store
    monkeypatch.setattr(EC, "get_channel", lambda _store, _pid: {"config": {"auto_approve_topics": True}})
    captured = {}

    def fake_add(_store, _pid, raw, origin="", status="", batch="", actor=""):
        captured["status"] = status
        return {"added": [], "duplicates": []}

    monkeypatch.setattr(EC, "add_topics", fake_add)
    monkeypatch.setattr(EC, "topics_context", lambda *a, **k: "")
    monkeypatch.setattr("app.explainer_gen._ask", lambda *a, **k: {"topics": []})
    EC.propose_topics(store, env.pid, client=object(), count=1)
    assert captured["status"] == "proposed"
    S.save_settings(store, env.pid, {"stop_at_clip_list": False})
    EC.propose_topics(store, env.pid, client=object(), count=1)
    assert captured["status"] == "approved"


# --------------------------------------------------------------------------- #
# Что читает агент
# --------------------------------------------------------------------------- #


def test_agents_are_told_what_the_project_allows(env):
    store = env.store
    context = C.project_context(store, env.pid)
    assert '"agent_settings"' in context and "Как работает агент" in context
    assert context.index("agent_settings") < context.index('"brief"')  # обрезка длинного среза его не съест
    brief = run_tool(ToolContext(store=store), "get_project_brief", {"project_id": env.pid})[0]
    assert brief["agent_settings"]["stop_at_clip_list"] is True
    read, ok = run_tool(ToolContext(store=store), "get_agent_settings", {"project_id": env.pid})
    assert ok and any("Сам не публикуешь" in line for line in read["allows"])
    chosen = store.upsert_account("youtube", "yt-main", COOKIE_YT)
    S.save_settings(store, env.pid, {"wait_answers": False, "stop_at_clip_list": False,
                                     "spend": {"enabled": True, "per_run_rub": 50, "per_month_rub": 300},
                                     "publish": {"enabled": True, "account_ids": [chosen["id"]], "privacy": "public"}})
    lines = " ".join(S.agent_brief(store, env.pid)["allows"])
    assert "не ждёт ответов" in lines and "до 50 ₽" in lines and "yt-main" in lines
    # Агенту-монтажёру в редакторе то же самое.
    from app.ai.editor_agent import owner_settings_note

    plan = _rendered(store, env.sid)
    assert "yt-main" in owner_settings_note(store, plan["id"])
    assert owner_settings_note(store, _rendered(store, _source(store, "loose.mp4")["id"])["id"]) == ""
