"""ИИ-видео (app/aivideo*.py): весь конвейер сухим прогоном (0 ₽), гейт денег и одобрения,
продолжение опроса после перезапуска без второй оплаты, инструменты MCP и REST.

Никаких настоящих платных вызовов: «платный» провайдер — подделка с тем же интерфейсом."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from app import aivideo as V
from app import aivideo_gen as G
from app import aivideo_providers as PR
from app import autopilot as A
from app.settings import settings
from test_api_pipeline import _client, _require_ffmpeg

SHOTS = [
    {"duration": 3, "location": "kitchen", "characters": [{"id": "mira", "action": "открывает холодильник"}],
     "camera": {"shot": "medium", "move": "push_in"}, "action": "свет холодильника на лице",
     "dialogue": [{"who": "mira", "text": "Опять пусто…", "emotion": "tired"}]},
    {"duration": 3, "location": "kitchen", "characters": [{"id": "mira", "look": "pajamas"}, {"id": "kot"}],
     "camera": {"shot": "wide", "move": "parallax_push"}, "action": "кот сидит на столе",
     "dialogue": [{"who": "kot", "text": "Мяу. Это не я."}]},
    {"duration": 2.5, "location": "kitchen", "characters": [{"id": "kot"}], "camera": {"move": "pan_left"},
     "action": "кот облизывается", "dialogue": [], "transition_in": "fade"},
]


@pytest.fixture
def env(tmp_path, monkeypatch):
    _require_ffmpeg()
    import app.canvas as canvas

    monkeypatch.setattr(canvas, "CANVAS_WIDTH", 108)
    monkeypatch.setattr(canvas, "CANVAS_HEIGHT", 192)
    monkeypatch.setattr(settings, "aivideo_enabled", False)
    monkeypatch.setattr(settings, "aivideo_month_cap_rub", 3000.0)
    monkeypatch.setattr(settings, "aivideo_max_budget_rub", 2500.0)
    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr(settings, "polza_api_key", "")
    client, store = _client(tmp_path, monkeypatch)
    return client, store


def _project(store, provider="none"):
    out = V.create_ai_project(store, "Мира и кот", style_kind="anime", passport_text="мягкий акварельный toon",
                              palette=["#ffcc88", "#223355"], provider=provider)
    pid = out["project"]["id"]
    mira = V.save_character(store, pid, "Мира", "девушка 20 лет, рыжее каре, зелёные глаза, рост 160, "
                                              "цвета #ff8844 #2a6f4e, родинка над губой")
    V.save_look(store, mira["id"], "Пижама", "голубая пижама в звёздочку, растрёпанные волосы", slug="pajamas")
    V.save_character(store, pid, "Кот", "толстый серый кот, жёлтые глаза, белое пятно на груди", slug="kot",
                     voice={"model": "openai/tts-1", "voice": "onyx"})
    V.save_location(store, pid, "Кухня ночью", "маленькая кухня, ночь, свет из холодильника", slug="kitchen")
    script = V.create_script(store, pid, brief="Мира ищет еду ночью", duration_target=12)
    from app.aivideo_script import apply_script

    apply_script(store, script["id"], {"title": "Пустой холодильник", "logline": "ночной жор", "shots": SHOTS})
    return pid, script["id"]


# --------------------------------------------------------------------------- #
# Сухой прогон: сценарий → референсы → кадры → сборка → настоящий рендер
# --------------------------------------------------------------------------- #


def test_dry_run_pipeline_end_to_end_renders_mp4(env):
    _client_, store = env
    pid, sid = _project(store)

    est = G.estimate(store, pid, "refs")
    # 3 стиля + 1 локация + 2 персонажа × (разворот, эмоции) + 1 облик = 9, всё бесплатно.
    assert est["count"] == 9 and est["total_rub"] == 0 and not est["paid"]
    refs = G.request_stage(store, pid, "refs")
    assert refs["status"] == "done" and len(refs["run"]["done"]) == 9, refs
    assert G.estimate(store, pid, "refs")["count"] == 0  # всё есть

    kf = G.request_stage(store, pid, "keyframes", sid)
    assert kf["status"] == "done" and len(kf["run"]["done"]) == 3
    # Ключевой кадр получил референсы: канон персонажей, облик, плиту локации, стиль.
    job = G.get_job(store, kf["run"]["done"][1]["job_id"])
    assert len(job["params"]["refs"]) >= 4
    assert "identity card (verbatim" in job["prompt"] and "голубая пижама" in job["prompt"]
    assert "родинка над губой" in job["prompt"]

    from app.aivideo_assemble import assemble

    out = assemble(store, sid)
    assert out["ok"] and out["shots"] == 3 and out["lines"] == 2
    plan = store.get_clip_plan(out["clip_id"])
    layers = plan["render_settings"]["layers"]
    images = [l for l in layers if l["type"] == "image"]
    assert [l["id"] for l in images] == ["aiv_shot_1", "aiv_shot_2", "aiv_shot_3"]
    assert images[0]["keys"]["scale"][-1]["v"] > images[0]["keys"]["scale"][0]["v"]  # наезд
    assert "x" in images[2]["keys"] and images[2]["in"]["type"] == "fade"  # панорама + наплыв
    assert any(l["type"] == "audio" for l in layers) and any(l["type"] == "captions" for l in layers)
    assert plan["render_settings"]["camera"], "parallax_push плана 2 — ключи 3D-камеры"
    subs = store.get_clip_plan_subtitles(out["clip_id"])
    assert [line["text"] for line in subs["lines"]] == ["Опять пусто…", "Мяу. Это не я."]
    assert V.get_script(store, sid)["clip_plan_id"] == out["clip_id"]
    assert G.ledger(store, pid) == []  # ни рубля

    # Настоящий рендер компоновщиком (если собран): короткий mp4.
    from app.motion_render import CompositorMissing, compositor_command

    try:
        compositor_command()
    except CompositorMissing:
        pytest.skip("компоновщик не собран (npm run build:motion)")
    from app.render import ClipRenderService

    preset = store.create_ffmpeg_preset("aiv test", output_width=180, output_height=320, fps=15)
    clip = ClipRenderService(store).render_clip_plan(out["clip_id"], ffmpeg_preset_id=preset["id"])
    assert clip["status"] == "succeeded", clip.get("error")
    path = Path(clip["output_path"])
    assert path.is_file() and path.stat().st_size > 10_000
    from app.dubbing import probe_duration

    assert abs(probe_duration(path) - out["duration"]) < 1.0
    keep = os.getenv("AIV_KEEP_MP4")
    if keep:
        Path(keep).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(path, keep)


# --------------------------------------------------------------------------- #
# Деньги: оценка → карточка → одобрение → запуск; потолки; выключено
# --------------------------------------------------------------------------- #


class FakePaid:
    """Платный провайдер-подделка: считает POST и опросы, ничего не тратит."""

    name = "polza"
    paid = True

    def __init__(self, pending_polls: int = 0, cost: float | None = 6.0):
        self.posts = 0
        self.checks = 0
        self.pending_polls = pending_polls
        self.cost = cost

    def submit(self, params, refs):
        self.posts += 1
        return f"req-{self.posts}"

    def check(self, remote_id):
        self.checks += 1
        if self.pending_polls > 0:
            self.pending_polls -= 1
            return {"status": "pending"}
        return {"status": "completed", "url": "https://example.invalid/x.jpg", "cost_rub": self.cost}

    def fetch(self, result, dest, params):
        return PR.DryRunImages().fetch(result, dest, params)


@pytest.fixture
def paid(env, monkeypatch):
    fake = FakePaid()
    monkeypatch.setattr(PR, "get_image_provider", lambda name: fake if name == "polza" else PR.DryRunImages())
    return env, fake


def test_paid_stage_needs_owner_approval_and_respects_amount(paid, monkeypatch):
    (_client_, store), fake = paid
    pid, _sid = _project(store, provider="polza")
    est = G.estimate(store, pid, "refs")
    # nano-banana 1K: прайс 3 ₽ × 2 = 6 ₽ за картинку, 9 картинок.
    assert est["paid"] and est["count"] == 9 and est["total_rub"] == pytest.approx(54.0)
    assert any("AIVIDEO_ENABLED" in p for p in est["problems"])

    req = G.request_stage(store, pid, "refs", requested_by="agent")
    assert req["status"] == "awaiting_approval" and fake.posts == 0
    card = A.get_approval(store, req["approval_id"])
    assert card["status"] == "pending" and "54.00 ₽" in card["question"]
    assert [b["action"] for b in card["buttons"]] == ["approve", "reject"]

    # Без одобрения работник ничего не запускает.
    G.run_pending(store)
    assert fake.posts == 0
    with pytest.raises(G.BudgetError):
        G.run_approved(store, req["approval_id"], confirm=True)

    # Владелец одобрил, но платная генерация выключена — запуск держится, не платит.
    A.decide(store, req["approval_id"], "approve", actor="ui")
    G.run_pending(store)
    assert fake.posts == 0
    held = G.list_jobs(store, approval_id=req["approval_id"])
    assert {j["status"] for j in held} == {"held"} and any("AIVIDEO_ENABLED" in j["error"] for j in held)

    # Включено — запускается ровно одобренное, траты в журнале по факту провайдера.
    monkeypatch.setattr(settings, "aivideo_enabled", True)
    out = G.run_approved(store, req["approval_id"], confirm=True)
    assert len(out["done"]) == 9 and fake.posts == 9
    led = G.ledger(store, pid)
    assert len(led) == 9 and sum(l["amount_rub"] for l in led) == pytest.approx(54.0)
    assert {l["source"] for l in led} == {"provider"}


def test_spending_over_approved_amount_stops_the_batch(paid, monkeypatch):
    (_client_, store), fake = paid
    monkeypatch.setattr(settings, "aivideo_enabled", True)
    fake.cost = 20.0  # провайдер списывает больше оценки (6 ₽)
    pid, _sid = _project(store, provider="polza")
    req = G.request_stage(store, pid, "refs")
    A.decide(store, req["approval_id"], "approve", actor="ui")
    out = G.run_pending(store)
    # 54 ₽ одобрено: 20 + 20 = 40, третья ещё влезает по оценке (40 + 6 ≤ 54) → 60; дальше стоп.
    assert fake.posts == 3 and len(out["done"]) == 3 and out["held"]
    assert G.spent_approval(store, req["approval_id"]) == pytest.approx(60.0)
    statuses = {j["status"] for j in G.list_jobs(store, approval_id=req["approval_id"])}
    assert statuses == {"done", "held"}


def test_caps_refuse_requests(paid, monkeypatch):
    (_client_, store), fake = paid
    pid, sid = _project(store, provider="polza")
    # Месячный потолок проекта (только владелец) ниже стоимости этапа — отказ.
    with pytest.raises(V.AivError):
        V.update_style(store, pid, {"month_cap_rub": 10})  # агент не может
    V.update_style(store, pid, {"month_cap_rub": 10}, owner=True)
    with pytest.raises(G.BudgetError, match="месяц проекта"):
        G.request_stage(store, pid, "refs")
    V.update_style(store, pid, {"month_cap_rub": 1000}, owner=True)
    # Потолок серии-черновика 350 ₽: качество 2K у gpt-image (7 × 2 = 14 ₽) × 3 плана = 42 — влезает,
    # а с уже потраченными 340 ₽ — нет.
    V.update_style(store, pid, {"image_model": "openai/gpt-image-2.5-flare"})
    assert G.estimate(store, pid, "keyframes", sid)["total_rub"] == pytest.approx(24.0)  # 1K: 4 × 2 × 3
    store.db.execute("INSERT INTO aiv_ledger (project_id, script_id, stage, amount_rub, source) "
                     "VALUES (?, ?, 'keyframes', 340, 'provider')", (pid, sid))
    with pytest.raises(G.BudgetError, match="потолка серии 350"):
        G.request_stage(store, pid, "keyframes", sid)
    # Общий потолок одного этапа.
    monkeypatch.setattr(settings, "aivideo_max_budget_rub", 20.0)
    with pytest.raises(G.BudgetError, match="AIVIDEO_MAX_BUDGET_RUB"):
        G.request_stage(store, pid, "refs")
    assert fake.posts == 0


def test_reject_cancels_and_regenerate_has_own_small_card(paid, monkeypatch):
    (_client_, store), fake = paid
    monkeypatch.setattr(settings, "aivideo_enabled", True)
    pid, sid = _project(store, provider="polza")
    req = G.request_stage(store, pid, "refs")
    A.decide(store, req["approval_id"], "reject", comment="дорого", actor="ui")
    assert {j["status"] for j in G.list_jobs(store, approval_id=req["approval_id"])} == {"cancelled"}
    G.run_pending(store)
    assert fake.posts == 0

    req = G.request_stage(store, pid, "refs")
    A.decide(store, req["approval_id"], "approve", actor="ui")
    G.run_pending(store)
    kf = G.request_stage(store, pid, "keyframes", sid)
    A.decide(store, kf["approval_id"], "approve", actor="tg")
    G.run_pending(store)
    ref = V.selected_ref(store, pid, "shot", V.get_script(store, sid)["shots"][0]["id"], "keyframe")
    before = fake.posts
    again = G.regenerate(store, ref_id=ref["id"])
    assert again["estimate"]["count"] == 1 and again["estimate"]["total_rub"] == pytest.approx(6.0)
    A.decide(store, again["approval_id"], "approve", actor="ui")
    G.run_pending(store)
    assert fake.posts == before + 1
    versions = V.list_refs(store, pid, "shot", ref["target_id"], "keyframe")
    assert [v["version"] for v in versions] == [1, 2] and versions[-1]["selected"]


def test_restart_resumes_polling_without_second_post(paid, monkeypatch):
    (_client_, store), fake = paid
    monkeypatch.setattr(settings, "aivideo_enabled", True)
    pid, _sid = _project(store, provider="polza")
    req = G.request_stage(store, pid, "refs", items=["location:1"])
    A.decide(store, req["approval_id"], "approve", actor="ui")
    fake.pending_polls = 10**6  # провайдер ещё рисует — опрос не дождался
    out = G.run_pending(store, poll_timeout=0)
    [job] = G.list_jobs(store, approval_id=req["approval_id"])
    assert job["status"] == "polling" and job["remote_id"] == "req-1" and out["polling"] == [job["id"]]
    assert fake.posts == 1

    # «Перезапуск»: свежий провайдер, тот же remote_id — опрос продолжается, POST не повторяется.
    fresh = FakePaid()
    monkeypatch.setattr(PR, "get_image_provider", lambda name: fresh)
    res = G.resume(store)
    assert res["finished"] == [job["id"]] and fresh.posts == 0 and fresh.checks >= 1
    assert G.get_job(store, job["id"])["status"] == "done"
    assert len(G.ledger(store, pid)) == 1

    # Оборвались между «отправляю» и ответом — сами не повторяем (могли заплатить).
    store.db.execute("UPDATE aiv_gen_jobs SET status = 'submitting', remote_id = '' WHERE id = ?", (job["id"],))
    res = G.resume(store)
    assert res["lost"] == [job["id"]] and fresh.posts == 0
    assert "проверь баланс" in G.get_job(store, job["id"])["error"]


def test_same_request_is_taken_from_cache(paid, monkeypatch):
    (_client_, store), fake = paid
    monkeypatch.setattr(settings, "aivideo_enabled", True)
    pid, _sid = _project(store, provider="polza")
    req = G.request_stage(store, pid, "refs")
    A.decide(store, req["approval_id"], "approve", actor="ui")
    G.run_pending(store)
    assert fake.posts == 9
    # Локацию пометили «устарело», но запрос тот же (описание и референсы не менялись):
    # input_hash совпал — файл из кэша, 0 ₽, без POST.
    loc = V.list_locations(store, pid)[0]
    V.mark_stale(store, pid, "location", loc["id"])
    req = G.request_stage(store, pid, "refs")
    assert req["estimate"]["count"] == 1
    A.decide(store, req["approval_id"], "approve", actor="ui")
    out = G.run_pending(store)
    assert fake.posts == 9 and len(out["done"]) == 1
    assert G.get_job(store, out["done"][0]["job_id"])["cost_source"] == "cache"
    assert len(G.ledger(store, pid)) == 9


def test_identity_change_marks_dependent_keyframes_stale(env):
    _client_, store = env
    pid, sid = _project(store)
    G.request_stage(store, pid, "refs")
    G.request_stage(store, pid, "keyframes", sid)
    kot = V.character_by_slug(store, pid, "kot")
    V.save_character(store, pid, identity_card="худой чёрный кот", character_id=kot["id"])
    shots = V.get_script(store, sid)["shots"]
    stale = {s["idx"]: V.selected_ref(store, pid, "shot", s["id"], "keyframe")["stale"] for s in shots}
    assert stale == {1: False, 2: True, 3: True}
    # Устаревшее снова в плане этапа — с ценой (здесь 0 ₽ сухого прогона).
    assert [i["label"] for i in G.estimate(store, pid, "keyframes", sid)["items"]] == ["план 2", "план 3"]


def test_shot_validation_explains_unknowns(env):
    _client_, store = env
    pid, sid = _project(store)
    with pytest.raises(V.AivError, match="нет персонажа 'vasya'"):
        V.update_script(store, sid, {"shots": [{"duration": 3, "characters": [{"id": "vasya"}],
                                                "camera": {"move": "static"}}]})
    out = V.update_script(store, sid, {"shots": [{"duration": 30, "characters": ["mira", "kot", "mira"],
                                                  "camera": {"move": "zoom_in"}, "dialogue": []}]})
    shot = out["shots"][0]
    assert shot["duration"] == V.SHOT_MAX_SEC and len(shot["cast"]) == 2 and shot["camera"]["move"] == "push_in"
    assert any("прижата" in w for w in out["warnings"])


def test_gemini_script_writer_with_fake_client(env):
    _client_, store = env
    pid, sid = _project(store)
    import json

    class Fake:
        def generate_content(self, model, payload):
            text = json.dumps({"title": "Кот-вор", "logline": "кот съел всё",
                               "new_characters": [{"id": "pes", "name": "Пёс", "identity_card": "рыжий корги"}],
                               "new_locations": [{"id": "yard", "name": "Двор", "description": "двор днём"}],
                               "shots": [{"duration": 4, "location": "yard", "characters": [{"id": "pes"}],
                                          "camera": {"move": "orbit"}, "action": "пёс лает",
                                          "dialogue": [{"who": "pes", "text": "Гав!"}]}]}, ensure_ascii=False)
            return {"candidates": [{"content": {"parts": [{"text": text}]}}]}

    from app.aivideo_script import write_script

    out = write_script(store, sid, brief="пёс во дворе", client=Fake())
    assert out["title"] == "Кот-вор" and out["created"] == {"characters": ["pes"], "locations": ["yard"]}
    assert out["shots"][0]["camera"]["move"] == "orbit"
    with pytest.raises(V.AivError, match="Gemini не настроен"):
        write_script(store, sid)


# --------------------------------------------------------------------------- #
# Модуль, MCP, REST
# --------------------------------------------------------------------------- #


def test_module_tools_and_agent_cannot_approve(env):
    _client_, store = env
    from app import modules
    from app.agent_tools import ToolContext, list_tools, run_tool

    mod = modules.MODULES_BY_ID["ai_video"]
    assert "series" in mod.depends and "paid" in mod.cost
    names = {t.name for t in list_tools(include_discovery=True, store=store)}
    assert {"aiv_create_project", "aiv_request_approval", "aiv_assemble"} <= names
    assert not any(n.startswith(("aiv_approve", "aiv_decide")) for n in names)

    ctx = ToolContext(store=store)
    out, ok = run_tool(ctx, "aiv_create_project", {"name": "Тест", "style_kind": "photoreal"})
    assert ok, out
    pid = out["project_id"]
    out, ok = run_tool(ctx, "aiv_update_style", {"project_id": pid, "month_cap_rub": 99999})
    assert not ok and "владелец" in out["error"]
    out, ok = run_tool(ctx, "aiv_define_character", {"project_id": pid, "name": "Ава",
                                                     "identity_card": "витубер, синие волосы", "origin": "owner"})
    assert ok and out["character"]["slug"] == "ava"
    out, ok = run_tool(ctx, "aiv_estimate", {"project_id": pid, "stage": "refs"})
    assert ok and out["total_rub"] == 0


def test_rest_overview_and_assemble_flow(env):
    client, store = env
    resp = client.post("/api/ai-video/projects", json={"name": "REST", "style_kind": "anime"})
    assert resp.status_code == 200, resp.text
    pid = resp.json()["project"]["id"]
    assert client.post(f"/api/projects/{pid}/ai-video/characters",
                       json={"name": "Мира", "identity_card": "рыжая"}).status_code == 200
    assert client.post(f"/api/projects/{pid}/ai-video/locations",
                       json={"name": "Кухня", "description": "ночь"}).status_code == 200
    sid = client.post(f"/api/projects/{pid}/ai-video/scripts", json={"brief": "x"}).json()["id"]
    resp = client.patch(f"/api/ai-video/scripts/{sid}", json={"shots": [
        {"duration": 3, "location": "kuhnya", "characters": ["mira"], "camera": {"move": "float"},
         "dialogue": [{"who": "mira", "text": "Привет"}]}]})
    assert resp.status_code == 200, resp.text
    assert client.patch(f"/api/projects/{pid}/ai-video", json={"month_cap_rub": 500}).json()["month_cap_rub"] == 500
    for stage, body in (("refs", {}), ("keyframes", {"script_id": sid})):
        resp = client.post(f"/api/projects/{pid}/ai-video/request", json={"stage": stage, **body})
        assert resp.status_code == 200 and resp.json()["status"] == "done", resp.text
    view = client.get(f"/api/ai-video/scripts/{sid}").json()
    assert view["shots"][0]["keyframes"][0]["url"].startswith("/media/montage-assets/")
    resp = client.post(f"/api/ai-video/scripts/{sid}/assemble", json={})
    assert resp.status_code == 200, resp.text
    assert resp.json()["clip_id"]
    over = client.get(f"/api/projects/{pid}/ai-video").json()
    assert over["budget"]["provider"] == "none" and over["scripts"][0]["status"] == "assembled"
    assert client.get("/api/projects/999/ai-video").status_code == 404


def test_polza_images_uses_media_endpoint_with_input(monkeypatch, tmp_path):
    """Параметры модели (aspect_ratio, image_resolution, референсы) — внутри input у POST /media:
    у /images/generations aspect_ratio игнорировался и приходил квадрат."""
    import httpx

    from app.aivideo_providers import PolzaImages, _result_url, model_params

    ref = tmp_path / "ref.jpg"
    ref.write_bytes(b"\xff\xd8\xff\xd9")
    seen = {}

    class _Resp:
        status_code = 200
        content = b"{}"

        def json(self):
            return {"id": "gen_1", "status": "pending"}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen["url"], seen["body"] = url, json
        return _Resp()

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr("app.aivideo_providers.ref_data_url", lambda p: "data:image/jpeg;base64,AA==")
    provider = PolzaImages(api_key="k", base_url="https://polza.test/api/v1")
    rid = provider.submit(model_params("google/gemini-nano-banana-2.1", "кадр", "9:16", "1K"), [ref])
    assert rid == "gen_1"
    assert seen["url"].endswith("/media")
    assert seen["body"]["model"] == "google/gemini-nano-banana-2.1"
    assert seen["body"]["input"]["aspect_ratio"] == "9:16"
    assert seen["body"]["input"]["images"] == [{"type": "base64", "data": "data:image/jpeg;base64,AA=="}]
    assert _result_url({"data": [{"url": "https://s3/x.jpg"}]}) == "https://s3/x.jpg"
    assert _result_url({"output": {"url": "https://s3/y.jpg"}}) == "https://s3/y.jpg"
