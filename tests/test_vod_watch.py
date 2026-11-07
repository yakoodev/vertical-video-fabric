"""Замкнутый автопилот: «Авто» только через одобрение (и перенос его настроек), наблюдатель
Twitch-VOD (новый VOD, без дублей, эфир не трогаем, мало места — карточка), вес чата в отборе.
yt-dlp, загрузка, нарезка и чат замоканы."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from app import autopilot as A
from app import chat_signal as cs
from app import projects as P
from app import vod_watch as W
from app.automation import AutomationService, inbox_project, migrate_publish_settings
from app.twitch_chat import insert_messages
from test_autopilot import WED, _clip, _jobs, env  # noqa: F401 - фикстура env

THUMB = "https://static-cdn.jtvnw.net/cf_vods/d1m7jfoe9zdc1j/abc/thumb/thumb0-320x180.jpg"
LIVE_THUMB = "https://vod-secure.twitch.tv/_404/404_processing_320x180.png"


def _vod(vod_id: str, title: str, duration: float, live: bool = False) -> dict:
    return {"_type": "url", "id": f"v{vod_id}", "url": f"https://www.twitch.tv/videos/{vod_id}", "title": title,
            "duration": duration, "thumbnail": LIVE_THUMB if live else THUMB}


class FakeDownloads:
    def __init__(self):
        self.tasks: dict[int, dict] = {}
        self.started: list[str] = []

    def start(self, url, quality="", selection=None):
        task_id = len(self.tasks) + 1
        self.tasks[task_id] = {"id": task_id, "status": "downloading", "message": "Скачивание 10%"}
        self.started.append(url)
        return dict(self.tasks[task_id])

    def get(self, task_id):
        return dict(self.tasks[task_id])


class FakeAutomation:
    def __init__(self):
        self.calls: list[dict] = []
        self.runs: dict[int, dict] = {}

    def start_run(self, **kwargs):
        self.calls.append(kwargs)
        run_id = len(self.calls)
        self.runs[run_id] = {"id": run_id, "status": "rendering", "message": "Рендерю"}
        return dict(self.runs[run_id])

    def get_run(self, run_id):
        return self.runs.get(run_id)


@pytest.fixture
def watch(env, monkeypatch):
    _client, store, source, pid = env
    clock = {"now": WED}
    monkeypatch.setattr(A, "now", lambda: clock["now"])
    listing: dict = {"entries": []}
    monkeypatch.setattr(W, "list_vods", lambda channel, limit=10: listing["entries"])
    monkeypatch.setattr(W, "ensure_free_space", lambda need: None)
    W.update_config(store, pid, {"enabled": True, "channel": "https://www.twitch.tv/sareamura/videos",
                                 "settle_minutes": 30})
    return SimpleNamespace(store=store, pid=pid, clock=clock, listing=listing, downloads=FakeDownloads(),
                           automation=FakeAutomation(), source=source, client=_client)


def _items(store, pid):
    return {i["vod_id"]: i for i in W.list_items(store, pid)}


# --------------------------------------------------------------------------- #
# Наблюдатель VOD
# --------------------------------------------------------------------------- #


def test_watch_off_by_default_and_only_stream_channel(env):
    _c, store, _source, pid = env
    cfg = W.get_config(store, pid)
    assert cfg["enabled"] is False and cfg["interval_hours"] == 2.0 and cfg["mode"] == "pipeline"
    other = P.create_project(store, "Сериал", kind="series")["id"]
    with pytest.raises(ValueError, match="стрим-канал"):
        W.update_config(store, other, {"enabled": True, "channel": "sareamura"})
    with pytest.raises(ValueError, match="polza"):
        W.update_config(store, pid, {"analysis": {"provider": "polza"}})
    assert W.channel_login("https://www.twitch.tv/SareaMura/videos?filter=archives") == "sareamura"


def test_new_vod_found_downloaded_cut_without_duplicates(watch):
    s, pid, d, auto = watch.store, watch.pid, watch.downloads, watch.automation
    s_tick = lambda: W.tick(s, d, auto)  # noqa: E731

    # Первое включение: то, что уже на канале, не качаем (baseline).
    watch.listing["entries"] = [_vod("2000000001", "Старый стрим", 10800)]
    assert W.check_project(s, pid)["baseline"] is True
    assert _items(s, pid)["2000000001"]["status"] == "baseline" and not d.started

    # Идёт эфир: VOD с заглушкой превью — не трогаем (записи эфира запрещены).
    watch.listing["entries"] = [_vod("2000000002", "Стрим сейчас", 3600, live=True), _vod("2000000001", "Старый", 10800)]
    W.check_project(s, pid)
    W.advance(s, d, auto)
    assert _items(s, pid)["2000000002"]["status"] == "live" and not d.started

    # Эфир кончился: ждём, пока длина устоится settle_minutes, и только потом качаем.
    watch.listing["entries"] = [_vod("2000000002", "Стрим", 9000), _vod("2000000001", "Старый", 10800)]
    W.check_project(s, pid)
    W.advance(s, d, auto)
    assert _items(s, pid)["2000000002"]["status"] == "seen" and not d.started
    watch.clock["now"] = WED + timedelta(minutes=31)
    s_tick()  # next_check_at подошёл раньше 2 ч: VOD ждал устояться
    item = _items(s, pid)["2000000002"]
    assert item["status"] == "downloading" and d.started == ["https://www.twitch.tv/videos/2000000002"]

    # Повторные проверки не плодят дублей и не качают второй раз.
    W.check_project(s, pid)
    W.check_project(s, pid)
    assert len(W.list_items(s, pid)) == 2 and len(d.started) == 1

    # Скачалось → источник в проекте → встроенная нарезка с настройками проекта → готово.
    new_source = s.create_source("twitch_url", s.get_source(watch.source["id"])["local_path"], original_filename="vod.mp4",
                                 duration_sec=9000, status="ready")
    d.tasks[item["download_task_id"]].update(status="succeeded", source_id=new_source["id"])
    W.advance(s, d, auto)
    assert P.project_of_source(s, new_source["id"]) == pid
    assert _items(s, pid)["2000000002"]["status"] == "cutting"
    call = auto.calls[0]
    assert call["source_id"] == new_source["id"] and call["project_id"] == pid
    assert call["publish"]["submit"] is True and call["look"]["censor_profanity"] is True
    auto.runs[1].update(status="done", message="Готово: клипов 2, на одобрении 2")
    W.advance(s, d, auto)
    assert _items(s, pid)["2000000002"]["status"] == "done"
    assert any(e["event"] == "vod_cut" for e in A.list_events(s, pid))


def test_low_space_refuses_with_card_and_retry(watch, monkeypatch):
    s, pid, d, auto = watch.store, watch.pid, watch.downloads, watch.automation
    W.update_config(s, pid, {"settle_minutes": 0, "backfill": 1})

    def no_space(need):
        raise ValueError("мало места для загрузки: свободно 3.0 ГБ, нужно ~14.0 ГБ")

    monkeypatch.setattr(W, "ensure_free_space", no_space)
    watch.listing["entries"] = [_vod("3000000001", "Большой стрим", 14400)]
    W.check_project(s, pid)  # backfill 1 — последний VOD берём сразу
    W.advance(s, d, auto)
    item = _items(s, pid)["3000000001"]
    assert item["status"] == "no_space" and not d.started
    card = A.get_approval(s, item["approval_id"])
    assert card["kind"] == "other" and "мало места" in card["question"]
    assert [b["action"] for b in card["buttons"]] == ["approve", "reject"]

    monkeypatch.setattr(W, "ensure_free_space", lambda need: None)
    A.decide(s, card["id"], "approve", actor="ui")  # «Повторить»
    W.advance(s, d, auto)
    assert _items(s, pid)["3000000001"]["status"] == "downloading" and len(d.started) == 1


def test_ask_mode_card_for_agent(watch):
    s, pid, d, auto = watch.store, watch.pid, watch.downloads, watch.automation
    W.update_config(s, pid, {"mode": "ask", "settle_minutes": 0})
    watch.listing["entries"] = []
    W.check_project(s, pid)  # пустой канал — baseline без элементов
    watch.listing["entries"] = [_vod("4000000001", "Коллаб", 7200)]
    W.check_project(s, pid)
    item = _items(s, pid)["4000000001"]
    assert item["status"] == "asking" and not d.started
    card = A.get_approval(s, item["approval_id"])
    assert [b["action"] for b in card["buttons"]] == ["approve", "both", "reject"]
    A.decide(s, card["id"], "both", actor="tg")  # «Скачать — нарежет агент»
    W.advance(s, d, auto)
    item = _items(s, pid)["4000000001"]
    assert item["status"] == "downloading" and item["cutter"] == "agent"
    source = s.create_source("twitch_url", s.get_source(watch.source["id"])["local_path"], original_filename="v.mp4",
                             duration_sec=7200, status="ready")
    d.tasks[item["download_task_id"]].update(status="succeeded", source_id=source["id"])
    W.advance(s, d, auto)
    assert _items(s, pid)["4000000001"]["status"] == "for_agent" and not auto.calls


def test_vod_watch_api(watch):
    client, pid = watch.client, watch.pid
    res = client.get(f"/api/projects/{pid}/vod-watch")
    assert res.status_code == 200 and res.json()["config"]["channel"] == "sareamura"
    res = client.put(f"/api/projects/{pid}/vod-watch", json={"interval_hours": 3, "look": {"layout": "split"}})
    assert res.status_code == 200
    cfg = res.json()["config"]
    assert cfg["interval_hours"] == 3 and cfg["look"]["layout"] == "split" and cfg["look"]["nameplate"] is True
    assert client.put(f"/api/projects/{pid}/vod-watch", json={"look": {"layout": "zzz"}}).status_code == 400


# --------------------------------------------------------------------------- #
# «Авто»: только через одобрение, перенос настроек
# --------------------------------------------------------------------------- #


def _service(store):
    render = SimpleNamespace(render_clip_plan=lambda plan_id, **k: store.list_clips_for_plan(plan_id)[0])
    return AutomationService(store, None, SimpleNamespace(run_analysis=lambda *a, **k: {}), render)


def test_auto_never_publishes_without_approval(env, monkeypatch):
    _c, store, source, pid = env
    plan = _clip(store, source["id"])
    monkeypatch.setattr(store, "ensure_clip_plans_for_source", lambda source_id: None)
    yt = store.list_accounts()[0]
    svc = _service(store)
    publish = {"targets": [yt["id"]], "privacy": "public", "interval_hours": 0, "max_clips": 0}
    run = svc._new_run(label="t")
    svc._run(run, "", {}, {"provider": "mock"}, {}, publish, source_id=source["id"])
    assert run["status"] == "done", run["error"]
    assert run["approvals"] == 1 and run["jobs"] == 0 and not _jobs(store)
    card = A.list_approvals(store, pid, status="pending")[0]
    assert card["clip_plan_id"] == plan["id"] and card["submitted_by"] == "auto" and card["platforms"] == ["youtube"]
    A.decide(store, card["id"], "approve")
    assert len(_jobs(store)) == 1  # публикация — только из approved

    # Настройка проекта «Публиковать самому» — сразу в расписание (это решает автопилот, не «Авто»).
    from app.agent_settings import save_settings

    save_settings(store, pid, {"publish": {"enabled": True, "account_ids": [yt["id"]]}})
    run = svc._new_run(label="t2")
    svc._run(run, "", {}, {"provider": "mock"}, {}, publish, source_id=source["id"])
    assert run["auto_approved"] == 1
    # Тот же клип на тот же аккаунт уже в очереди: второго задания нет, причина — в карточке.
    assert len(_jobs(store)) == 1
    again = A.list_approvals(store, pid, status="approved")[0]
    assert again["publish_status"] == "failed" and "уже в очереди" in again["publish_error"]


def test_auto_source_without_series_goes_to_inbox(env, monkeypatch):
    _c, store, _source, _pid = env
    loose = store.create_source("upload", store.get_source(_source["id"])["local_path"], original_filename="x.mp4",
                                duration_sec=60, status="ready")
    _clip(store, loose["id"])
    monkeypatch.setattr(store, "ensure_clip_plans_for_source", lambda source_id: None)
    svc = _service(store)
    run = svc._new_run(label="t")
    svc._run(run, "", {}, {"provider": "mock"}, {}, {"targets": [], "max_clips": 0}, source_id=loose["id"])
    inbox = inbox_project(store)
    assert P.project_of_source(store, loose["id"]) == inbox and run["project_id"] == inbox
    assert inbox_project(store) == inbox  # один общий проект, не по проекту на ссылку
    # Ни аккаунтов, ни площадок — как раньше «без публикации», карточек нет.
    assert "без публикации" in run["message"] and not A.list_approvals(store, inbox)


def test_auto_settings_migrate_without_loss(env):
    _c, store, _source, pid = env
    yt = store.list_accounts()[0]
    tt = store.upsert_account("tiktok", "tt-main", "sessionid=abc; sid_tt=abc")
    fresh = P.create_project(store, "Новый канал", kind="stream_channel")["id"]
    notes = migrate_publish_settings(store, fresh, {"targets": [yt["id"], tt["id"]], "privacy": "unlisted",
                                                    "interval_hours": 6, "start_at": WED})
    cfg = A.get_config(store, fresh)
    assert set(notes) == {"accounts", "platforms", "privacy", "slots"}
    assert cfg["accounts"] == {"youtube": [yt["id"]], "tiktok": [tt["id"]]}
    assert cfg["platforms"] == ["youtube", "tiktok"] and cfg["privacy"] == "unlisted"
    # Старт 19:00 МСК, шаг 6 ч → 01:00, 07:00, 13:00, 19:00 каждый день.
    assert cfg["slots"] == [{"days": [0, 1, 2, 3, 4, 5, 6], "times": ["01:00", "07:00", "13:00", "19:00"]}]

    # Настроенный проект: ничего своего не теряет, дополняются только пустые поля.
    A.update_config(store, pid, {"platforms": ["youtube"], "slots": [{"days": [5], "times": ["12:00"]}]})
    notes = migrate_publish_settings(store, pid, {"targets": [tt["id"]], "privacy": "private", "interval_hours": 2})
    cfg = A.get_config(store, pid)
    assert notes == ["accounts"] and cfg["platforms"] == ["youtube"] and cfg["privacy"] == "public"
    assert cfg["slots"] == [{"days": [5], "times": ["12:00"]}] and cfg["accounts"] == {"tiktok": [tt["id"]]}
    assert any(e["event"] == "auto_migrated" for e in A.list_events(store, pid))


# --------------------------------------------------------------------------- #
# Чат как мягкий сигнал моментов
# --------------------------------------------------------------------------- #


def _chat(burst_at: float = 600.0) -> list[tuple[float, str, str]]:
    msgs = [(float(t), f"u{t % 7}", "привет") for t in range(0, 1200, 10)]
    msgs += [(burst_at + i * 0.5, f"fan{i}", "KEKW ахахах") for i in range(40)]
    return msgs


def test_chat_signal_burst_laugh_donation_and_spam():
    sig = cs.build_signal(_chat())
    assert cs.range_score(sig, 590, 605) > 0.8  # с задержкой зрителей окно ловит всплеск
    assert cs.range_score(sig, 100, 130) < 0.3
    assert cs.blend(0.5, None) == 0.5 and cs.blend(0.5, 1.0) == pytest.approx(0.575)
    peak = cs.peaks(sig)[0]
    assert 585 <= peak["moment_t"] <= 600 and peak["laugh"] > 10
    # Живой чат (5 сообщений за 10 с): один зритель спамит — не всплеск; донат выше фона.
    busy = [(t + k, f"u{k}", "привет") for t in range(0, 1200, 10) for k in range(5)]
    sig = cs.build_signal(busy + [(300 + i * 0.2, "bot", "KEKW") for i in range(40)]
                          + [(600 + i * 0.5, f"fan{i}", "KEKW") for i in range(40)]
                          + [(900.0, "donationalerts", "Вася — 500 ₽: привет Муре")])
    assert cs.range_score(sig, 590, 605) == 1.0 and cs.range_score(sig, 290, 300) < 0.35
    assert cs.range_score(sig, 880, 890) > cs.range_score(sig, 100, 110)
    assert cs.is_donation("viewer", "Cheer500 молодец") and not cs.is_donation("viewer", "донатный стрим был?")


def test_chat_soft_weight_in_candidates(env):
    _c, store, source, _pid = env
    analysis = store.create_ai_analysis(source["id"], "manual", status="succeeded")
    plans = []
    for i, start in enumerate([100, 590, 200, 300, 400, 700, 800, 900, 1000, 1100]):
        seg = store.create_ai_segment(analysis["id"], {"start_sec": start, "end_sec": start + 15, "title": f"m{i}"})
        plans.append(store.create_clip_plan(source["id"], analysis["id"], f"m{i}", segment_ids=[seg["id"]]))
    listed = store.list_clip_plans(source_id=source["id"])
    assert all("chat_score" not in p for p in listed)  # без чата — как раньше
    assert cs.rerank(store, listed) == listed
    before = {p["id"]: p["quality"] for p in listed}

    insert_messages(store, source["id"], [{"msg_id": str(n), "offset_sec": t, "user_name": u, "user_login": u, "text": x}
                                          for n, (t, u, x) in enumerate(_chat(600.0))])
    listed = store.list_clip_plans(source_id=source["id"])
    hot = next(p for p in listed if p["title"] == "m1")
    cold = next(p for p in listed if p["title"] == "m0")
    assert hot["chat_score"] > 0.8 and hot["quality"] > before[hot["id"]]
    assert cold["quality"] <= before[cold["id"]]
    order = [p["title"] for p in cs.rerank(store, listed)]
    assert order[0] == "m1" and order[1] == "m0"  # мягко: горячий на одно место выше, не через весь список

    peaks = cs.audio_map_peaks(store, source["id"], 500, 700)
    assert peaks and 585 <= peaks[0]["moment_t"] <= 600
    to_clip = cs.clip_time_mapper(store.get_clip_plan(hot["id"]))
    assert to_clip(595) == pytest.approx(5.0) and to_clip(50) is None
    # Пики — отдельным полем карты моментов audio_map (в шкале ответа).
    from app.agent_tools import ToolContext
    from app.audio_tools import _with_chat_peaks

    out = _with_chat_peaks(ToolContext(store=store), {"time": "source", "project_id": source["id"],
                                                      "from_sec": 500, "to_sec": 700})
    assert out["chat_peaks"][0]["moment_t"] == peaks[0]["moment_t"] and "chat_note" in out
    out = _with_chat_peaks(ToolContext(store=store, clip_plan_id=hot["id"]), {"time": "clip"})
    assert out["chat_peaks"] and 0 <= out["chat_peaks"][0]["moment_t"] <= 15
