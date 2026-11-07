"""Черновик выпуска (status ``draft``): сценарий и биты лежат в сервисе, ничего не запускается и не тратится, пока владелец не
нажмёт «Запустить». Денег и сети нет: всё платное и Gemini — подделки или ловушки."""

from __future__ import annotations

import pytest

from app import agent_settings as AS
from app import aivideo_gen as G
from app import nav
from app import releases as R
from app import stories as S
from app import stories_gen as X
from app import stories_tools as ST
from app.agent_tools import ToolContext, run_tool
from test_stories import STORY, FakeGemini, _setup, env, paid  # noqa: F401 - env, paid — фикстуры

SHORT = {"title": "Лифт: часть вторая", "logline": "Трос держится на честном слове",
         "sentences": [{"text": "Кабина встала между этажами.", "hud": "22 / 23 ЭТАЖ", "hud_kind": "info", "role": "hook"},
                       {"text": "Заряд телефона тает на глазах.", "hud": "ЗАРЯД 9%", "hud_kind": "resource", "role": "problem"},
                       {"text": "А потом погас свет.", "role": "finale"}]}
AGENT = {"Authorization": "Bearer service-token"}


def _traps(monkeypatch) -> list[str]:
    """Всё, что могло бы потратить деньги или время: любой вызов записывается."""
    called: list[str] = []

    def trap(name):
        def fn(*_a, **_k):
            called.append(name)
            raise AssertionError(f"черновик дошёл до {name}")
        return fn

    for name in ("write_script", "plan_beats", "request_generation", "check_generation", "assemble", "gemini_client"):
        monkeypatch.setattr(X, name, trap(name))
    for name in ("request_stage", "run_jobs", "estimate"):
        monkeypatch.setattr(G, name, trap(name))
    return called


def _draft(store, ids, **extra) -> dict:
    options = extra.pop("options", None)
    return X.create_draft(store, ids["world_id"], "Лифт застрял", None, "", {**SHORT, **extra}, options, "agent")


def test_api_creates_draft_without_generation(env, monkeypatch):
    client, store, tmp = env
    called = _traps(monkeypatch)
    ids = _setup(store, tmp, music=False, sounds=False)
    r = client.post(f"/api/stories/worlds/{ids['world_id']}/episodes",
                    json={"premise": "Лифт застрял", "draft": True, "engine": "3d", "renderer": "blender", **SHORT})
    assert r.status_code == 200, r.text
    ep = r.json()
    assert ep["status"] == "draft" and ep["status_label"] == "только сценарий, не запущен"
    assert ep["script"]["sentences"][0]["text"] == SHORT["sentences"][0]["text"]
    assert ep["options"]["engine"] == "3d" and ep["options"]["renderer"] == "blender"
    assert ep["requested_by"] == "ui"
    assert called == []
    assert store.db.query_one("SELECT COUNT(*) AS n FROM aiv_gen_jobs")["n"] == 0
    assert store.db.query_one("SELECT COUNT(*) AS n FROM approvals")["n"] == 0
    # Без сценария черновика нет, и пустой строки в списке не остаётся.
    r = client.post(f"/api/stories/worlds/{ids['world_id']}/episodes", json={"premise": "Лифт застрял", "draft": True})
    assert r.status_code == 400 and len(S.list_episodes(store, ids["project_id"])) == 1
    # Агент тоже может положить сценарий: это не трата.
    r = client.post(f"/api/stories/worlds/{ids['world_id']}/episodes", headers=AGENT, json={"premise": "Лифт застрял", "draft": True, **SHORT})
    assert r.status_code == 200 and r.json()["requested_by"] == "agent"


def test_mcp_tool_creates_draft(env, monkeypatch):
    _client_, store, tmp = env
    called = _traps(monkeypatch)
    ids = _setup(store, tmp, music=False, sounds=False)
    out, ok = run_tool(ToolContext(store=store), "story_draft",
                       {"world_id": ids["world_id"], "engine": "3d", "renderer": "blender", "quality": "final", **SHORT})
    assert ok, out
    assert out["status"] == "draft" and out["release_url"] == f"/r/story-{out['episode_id']}"
    brief = out["episode"]
    # Завязку берём из логлайна; биты без авторских — разбивка по фразам, видна сразу.
    assert brief["premise"] == SHORT["logline"] and brief["options"]["quality"] == "final"
    assert [b["text"] for b in brief["beats"]] and " ".join(b["text"] for b in brief["beats"]).startswith("Кабина встала")
    assert "estimate" not in brief and "не потрачено" in brief["note"]
    status, ok = run_tool(ToolContext(store=store), "story_status", {"episode_id": out["episode_id"]})
    assert ok and status["status"] == "draft"
    assert called == []
    out, ok = run_tool(ToolContext(store=store), "story_draft", {"world_id": ids["world_id"], "premise": "без текста"})
    assert not ok and "сценарий" in str(out)


def test_worker_ignores_draft_for_many_cycles_and_nothing_paid_runs(env, monkeypatch):
    _client_, store, tmp = env
    ids = _setup(store, tmp, music=False, sounds=False)
    ep = _draft(store, ids, options={"engine": "3d", "renderer": "blender"})
    before = S.get_episode(store, ep["id"])
    called = _traps(monkeypatch)
    worker = X.StoriesWorker(store)
    for _ in range(6):
        assert worker.run_once() == []
    # Даже прямой вызов ведущего черновик не двигает.
    assert X.advance(store, ep["id"])["status"] == "draft"
    after = S.get_episode(store, ep["id"])
    assert called == []
    assert after["status"] == "draft" and after["updated_at"] == before["updated_at"] and after["script"] == before["script"]
    assert after["words"] == [] and after["clip_id"] is None and after["estimate_rub"] == 0
    for table in ("aiv_gen_jobs", "approvals", "render3d_jobs"):
        assert store.db.query_one(f"SELECT COUNT(*) AS n FROM {table}")["n"] == 0, table
    assert not S.narration_ref(store, after)


def test_release_page_reads_as_script_without_progress_or_error(env, monkeypatch):
    client, store, tmp = env
    called = _traps(monkeypatch)
    ids = _setup(store, tmp, music=False, sounds=False)
    ep = _draft(store, ids, new_characters=[{"key": "oleg", "name": "Олег", "identity_card": "a tall man, grey coat #667788"}],
                new_locations=[{"key": "cabin", "name": "Кабина", "description": "a small elevator cabin, one dim lamp"}],
                sentences=[{"text": SHORT["sentences"][0]["text"],
                            "shots": [{"location": "cabin", "characters": ["oleg"], "action": "Oleg looks up", "camera": "tilt_up",
                                       "sfx": "скрип"}]},
                           SHORT["sentences"][1]])
    key = f"story-{ep['id']}"
    r = client.get(f"/api/releases/{key}")
    assert r.status_code == 200, r.text
    view = r.json()
    assert view["status"] == "script" and view["status_label"] == "Только сценарий" and view["to"] == f"/r/{key}"
    assert view["error"] == "" and view["approval"] is None and view["spent_rub"] == 0 and view["clips"] == []
    states = {s["key"]: s["state"] for s in view["steps"]}
    assert states == {"topic": "done", "script": "done", "images": "todo", "voice": "todo", "build": "todo", "clip": "todo"}
    draft = view["script_draft"]
    assert draft["title"] == SHORT["title"] and draft["text"].startswith("Кабина встала")
    assert [s["text"] for s in draft["sentences"]] == [SHORT["sentences"][0]["text"], SHORT["sentences"][1]["text"]]
    first = draft["beats"][0]
    assert first["place"] == "Кабина" and first["who"] == ["Олег"] and first["action"] == "Oleg looks up"
    assert first["camera"] == "tilt_up" and first["sound"] == "скрип"
    # Списки и счётчики: строка выпуска без спиннера и без «ошибки», в счётчиках как обычный выпуск.
    row = next(i for i in R.list_for_project(store, ids["project_id"]) if i["key"] == key)
    assert row["status"] == "script" and row["status_label"] == "Только сценарий" and row["to"] == f"/r/{key}"
    assert R.project_counts(store)[ids["project_id"]]["releases"] >= 1
    assert any(g["key"] == key for g in nav.project_videos(store, ids["project_id"])["generated"])
    assert any(e["id"] == ep["id"] and e["status"] == "draft" for e in S.overview(store, ids["project_id"])["episodes"])
    assert called == []


def test_startup_recovery_leaves_draft_alone(env):
    _client_, store, tmp = env
    ids = _setup(store, tmp, music=False, sounds=False)
    ep = _draft(store, ids)
    before = S.get_episode(store, ep["id"])
    assert not any(store.recover_after_restart().values())
    after = S.get_episode(store, ep["id"])
    assert after["status"] == "draft" and after["error"] == "" and after["updated_at"] == before["updated_at"]


def test_owner_start_goes_through_normal_pipeline_and_card(paid):  # noqa: F811
    (client, store, tmp), fake = paid
    ids = _setup(store, tmp, provider="polza")
    r = client.post(f"/api/stories/worlds/{ids['world_id']}/episodes", json={"premise": "побег", "duration": 50, "draft": True, **STORY})
    assert r.status_code == 200 and r.json()["status"] == "draft"
    eid = r.json()["id"]
    assert not X.StoriesWorker(store).run_once()
    # Владелец в браузере: заголовка с токеном нет.
    r = client.post(f"/api/stories/episodes/{eid}/start")
    assert r.status_code == 200 and r.json()["status"] == "scripted"
    # Второе нажатие: уже не черновик, второго запуска нет.
    assert client.post(f"/api/stories/episodes/{eid}/start").status_code == 400
    X.StoriesWorker(store).run_once()
    ep = S.get_episode(store, eid)
    # Та же карточка, что у любого эпизода: без «Разрешить» ни одного платного вызова.
    assert ep["status"] == "awaiting_approval" and ep["approval_id"] and fake.posts == []
    cards = [a for a in G.stage_approvals(store, ids["project_id"]) if a["stage"] == S.STAGE]
    assert len(cards) == 1 and cards[0]["id"] == ep["approval_id"]


def test_agent_starts_only_when_project_allows_spending(env):
    client, store, tmp = env
    ids = _setup(store, tmp, music=False, sounds=False)
    ctx = ToolContext(store=store)
    ep = _draft(store, ids)
    eid = ep["id"]
    r = client.post(f"/api/stories/episodes/{eid}/start", headers=AGENT)
    assert r.status_code == 403 and "Запустить" in r.json()["detail"]
    out, ok = run_tool(ctx, "story_start", {"episode_id": eid})
    assert not ok and "владелец" in str(out)
    assert S.get_episode(store, eid)["status"] == "draft"
    # Обходных путей нет: ни «продолжить», ни отмена с продолжением, ни правка битов.
    assert client.post(f"/api/stories/episodes/{eid}/continue", json={}, headers=AGENT).status_code == 400
    assert client.post(f"/api/stories/episodes/{eid}/cancel", headers=AGENT).status_code == 400
    assert client.patch(f"/api/stories/episodes/{eid}/beats", json={"beats": [{"i": 0, "camera": "static"}]}).status_code == 400
    out, ok = run_tool(ctx, "story_continue", {"episode_id": eid})
    assert not ok and "черновик" in str(out)
    out, ok = run_tool(ctx, "story_cancel", {"episode_id": eid})
    assert not ok and "черновик" in str(out)
    assert S.get_episode(store, eid)["status"] == "draft"

    AS.save_settings(store, ids["project_id"], {"spend": {"enabled": True, "per_run_rub": 10, "per_month_rub": 100}})
    r = client.post(f"/api/stories/episodes/{eid}/start", headers=AGENT)
    assert r.status_code == 200 and r.json()["status"] == "scripted"
    second = _draft(store, ids)
    out, ok = run_tool(ctx, "story_start", {"episode_id": second["id"]})
    assert ok and out["status"] == "scripted"
    # Запуск не черновика и неизвестного эпизода — понятный отказ.
    assert client.post(f"/api/stories/episodes/{eid}/start", headers=AGENT).status_code == 400
    assert client.post("/api/stories/episodes/99999/start").status_code == 404


def test_saving_script_keeps_draft_and_cancelled(env, monkeypatch):
    client, store, tmp = env
    ids = _setup(store, tmp, music=False, sounds=False)
    draft = _draft(store, ids)
    changed = {**SHORT, "title": "Новое название", "sentences": SHORT["sentences"][:2]}
    r = client.put(f"/api/stories/episodes/{draft['id']}/script", json=changed)
    assert r.status_code == 200 and r.json()["status"] == "draft" and r.json()["title"] == "Новое название"
    out, ok = run_tool(ToolContext(store=store), "story_set_script", {"episode_id": draft["id"], **SHORT})
    assert ok and out["status"] == "draft"  # continue по умолчанию не двигает черновик

    # Отменённый: сценарий сохраняется, статус и отметка «отменено» остаются, работник не берёт.
    ep = X.create(store, ids["world_id"], "Лифт застрял")
    assert X.cancel(store, ep["id"])["status"] == "cancelled"
    r = client.put(f"/api/stories/episodes/{ep['id']}/script", json=SHORT)
    assert r.status_code == 200 and r.json()["status"] == "cancelled" and r.json()["error"] == "отменено"
    assert r.json()["script"]["sentences"][0]["text"] == SHORT["sentences"][0]["text"]
    assert ep["id"] not in X.StoriesWorker(store).run_once() and S.get_episode(store, ep["id"])["status"] == "cancelled"
    # Продолжают явно: с шага подбора сцен, сценарий заново не пишется.
    monkeypatch.setattr(X, "write_script", lambda *a, **k: (_ for _ in ()).throw(AssertionError("сценарий уже есть")))
    monkeypatch.setattr(X, "advance", lambda store_, eid, client=None: S.get_episode(store_, eid))
    assert X.resume(store, ep["id"])["status"] == "scripted"


def test_other_statuses_still_move_to_scripted(env):
    _client_, store, tmp = env
    ids = _setup(store, tmp, music=False, sounds=False)
    for status in ("new", "planned", "awaiting_approval", "generating", "done", "failed"):
        ep = X.create(store, ids["world_id"], "Лифт застрял")
        S.set_status(store, ep["id"], status)
        assert X.set_script(store, ep["id"], SHORT)["status"] == "scripted", status
    ep = X.create(store, ids["world_id"], "Лифт застрял")
    S.set_status(store, ep["id"], "assembling")
    with pytest.raises(S.StoryError):
        X.set_script(store, ep["id"], SHORT)


def test_cancel_while_gemini_writes_is_not_overwritten(env):
    _client_, store, tmp = env
    ids = _setup(store, tmp, music=False, sounds=False)
    ep = X.create(store, ids["world_id"], "Лифт застрял")

    class CancelsWhileWriting(FakeGemini):
        def generate_content(self, model, payload):
            X.cancel(store, ep["id"])
            return super().generate_content(model, payload)

    done = X.advance(store, ep["id"], client=CancelsWhileWriting())
    assert done["status"] == "cancelled" and done["error"] == "отменено" and done["script"]["text"]


def test_lost_race_between_read_and_write_keeps_cancelled(env, monkeypatch):
    _client_, store, tmp = env
    ids = _setup(store, tmp, music=False, sounds=False)
    ep = X.create(store, ids["world_id"], "Лифт застрял")
    real = X.normalize_script

    def cancel_after_read(*args, **kwargs):
        script = real(*args, **kwargs)
        S.set_status(store, ep["id"], "cancelled", None, error="отменено")
        return script

    import app.stories_gen_script as SGS

    monkeypatch.setattr(SGS, "normalize_script", cancel_after_read)
    assert X.set_script(store, ep["id"], SHORT)["status"] == "cancelled"
    assert S.get_episode(store, ep["id"])["script"]["text"]


def test_author_beats_survive_start_and_must_match_the_script(env):
    client, store, tmp = env
    ids = _setup(store, tmp, music=False, sounds=False)
    beats = [{"text": "Кабина встала", "action": "the cabin jolts", "camera": "push_in", "sfx": "скрежет"},
             {"text": "между этажами.", "action": "dark shaft"},
             {"text": "Заряд телефона тает на глазах. А потом погас свет."}]
    r = client.post(f"/api/stories/worlds/{ids['world_id']}/episodes",
                    json={"premise": "Лифт застрял", "draft": True, "engine": "3d", "beats": beats, **SHORT})
    assert r.status_code == 200, r.text
    ep = r.json()
    assert [b["text"] for b in ep["beats"]] == [b["text"] for b in beats]
    assert [b["sentence"] for b in ep["beats"]] == [0, 0, 1]
    view = R.view(store, f"story-{ep['id']}")["script_draft"]
    assert view["beats"][0]["action"] == "the cabin jolts" and view["beats"][0]["sound"] == "скрежет"
    # Запуск идёт по битам автора, а не режет сценарий заново.
    assert X.start_draft(store, ep["id"])["status"] == "scripted"
    with pytest.raises(S.StoryError):  # пока сцены не подобраны, биты не правят
        X.update_beats(store, ep["id"], [{"i": 0, "camera": "static"}])
    X.plan_beats(store, ep["id"])
    started = S.get_episode(store, ep["id"])
    assert started["status"] == "planned" and [b["text"] for b in started["beats"]] == [b["text"] for b in beats]

    r = client.post(f"/api/stories/worlds/{ids['world_id']}/episodes",
                    json={"premise": "Лифт застрял", "draft": True, **SHORT, "beats": [{"text": "Совсем другие слова"}]})
    assert r.status_code == 400 and "не совпадает" in r.json()["detail"]
    assert len(S.list_episodes(store, ids["project_id"])) == 1  # отказ не оставил пустого черновика

    # Новый сценарий без своих битов: разбивка снова по фразам.
    second = _draft(store, ids, beats=[{"text": s["text"]} for s in SHORT["sentences"]])
    assert len(second["beats"]) == 3
    assert X.set_script(store, second["id"], SHORT)["beats"] == []


def test_world_with_drafts_is_not_deleted_silently(env):
    client, store, tmp = env
    ids = _setup(store, tmp, music=False, sounds=False)
    ep = _draft(store, ids)
    r = client.delete(f"/api/stories/worlds/{ids['world_id']}")
    assert r.status_code == 400 and "черновики" in r.json()["detail"]
    assert client.delete(f"/api/stories/episodes/{ep['id']}").status_code == 200
    assert client.delete(f"/api/stories/worlds/{ids['world_id']}").status_code == 200


def test_tool_names_are_registered():
    from app import agent_tools, chat_tools, modules

    names = {t.name for t in agent_tools._project_tools()}
    for name in ("story_draft", "story_start"):
        assert name in ST.TOOL_NAMES and name in names
        assert modules.module_of_tool(name) == "stories" and name in chat_tools.group_tools("stories")

