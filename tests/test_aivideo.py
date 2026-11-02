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
    # Деньги в тестах — по прайсу ×2 (6 ₽ за кадр nano-banana), независимо от замеров.
    monkeypatch.setattr(PR, "MEASURED_IMAGE_PRICES", {})
    # Блокинг в этих тестах — 2D-холст (3D-движок — свои тесты ниже и в test_aivideo_blocking3d.py).
    monkeypatch.setenv("AIV_BLOCKING_ENGINE", "2d")
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
    assert out["title"] == "Кот-вор" and out["created"] == {"characters": ["pes"], "locations": ["yard"],
                                                            "looks": []}
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


# --------------------------------------------------------------------------- #
# Гонка исполнителей: запуск отправляется ОДИН раз (первый настоящий прогон: план 2 ушёл дважды)
# --------------------------------------------------------------------------- #


class GatedPaid(FakePaid):
    """Подделка для гонок: считает POST по цели; ``gate`` держит первый POST выбранной цели,
    ``fail_kind`` — цель этого вида падает исключением."""

    def __init__(self, hold: str = "", fail_kind: str = "", fail_exc: Exception | None = None,
                 fail_times: int = 10**6, money: float | None = None, charge_fail: float = 0.0):
        super().__init__()
        import threading

        self.lock = threading.Lock()
        self.by_target: dict[str, int] = {}
        self.hold = hold
        self.fail_kind = fail_kind
        self.fail_exc = fail_exc or RuntimeError("boom")
        self.fail_times = fail_times
        self.money = money  # баланс; None — провайдер его не отдаёт
        self.charge_fail = charge_fail  # сколько списать при сбое (ответ не дошёл, а деньги взяли)
        self.entered = threading.Event()
        self.gate = threading.Event()

    def balance(self):
        if self.money is None:
            raise RuntimeError("GET /balance: 404")
        return self.money

    def submit(self, params, refs):
        key = " / ".join(str(x) for x in params.get("_caption") or [])
        with self.lock:
            self.posts += 1
            n = self.posts
            self.by_target[key] = self.by_target.get(key, 0) + 1
            first = self.by_target[key] == 1
        if self.fail_kind and params.get("_kind") == self.fail_kind and self.fail_times > 0:
            self.fail_times -= 1
            if self.money is not None:
                self.money -= self.charge_fail
            raise self.fail_exc
        if self.hold and self.hold in key and first:
            self.entered.set()
            assert self.gate.wait(10), "тест: ворота не открылись"
        return f"req-{n}"


def _gated(monkeypatch, **kw):
    fake = GatedPaid(**kw)
    monkeypatch.setattr(PR, "get_image_provider", lambda name: fake if name == "polza" else PR.DryRunImages())
    monkeypatch.setattr(settings, "aivideo_enabled", True)
    return fake


def test_concurrent_runners_submit_each_job_once(env, monkeypatch):
    import threading

    _client_, store = env
    fake = _gated(monkeypatch)
    pid, _sid = _project(store, provider="polza")
    req = G.request_stage(store, pid, "refs")
    A.decide(store, req["approval_id"], "approve", actor="ui")
    errors = []

    def runner(fn):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=runner, args=(lambda: G.run_pending(store),)) for _ in range(3)]
    threads.append(threading.Thread(target=runner, args=(lambda: G.run_approved(store, req["approval_id"], True),)))
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert not errors, errors
    assert fake.posts == 9 and set(fake.by_target.values()) == {1}
    jobs = G.list_jobs(store, approval_id=req["approval_id"])
    assert {j["status"] for j in jobs} == {"done"} and all(j["worker_id"] for j in jobs)
    assert len(G.ledger(store, pid)) == 9
    for ref in V.list_refs(store, pid):
        assert ref["version"] == 1, ref


def _three_refs(store, pid):
    mira, kot = V.character_by_slug(store, pid, "mira"), V.character_by_slug(store, pid, "kot")
    loc = V.list_locations(store, pid)[0]
    req = G.request_stage(store, pid, "refs", items=[f"location:{loc['id']}", f"character:{mira['id']}:turnaround",
                                                     f"character:{kot['id']}:turnaround"])
    A.decide(store, req["approval_id"], "approve", actor="ui")
    jobs = {j["label"]: j["id"] for j in G.list_jobs(store, approval_id=req["approval_id"])}
    return req["approval_id"], jobs["локация «Кухня ночью»"], jobs["Мира: разворот"], jobs["Кот: разворот"]


def test_run_approved_while_worker_holds_a_job_does_not_resubmit_it(env, monkeypatch):
    """Сценарий первого прогона: работник ещё отправляет план, его же партия упала на другом
    запуске (стоп карточки), владелец/агент жмёт aiv_run_approved — план не уходит второй раз."""
    import threading

    _client_, store = env
    fake = _gated(monkeypatch, hold="Мира", fail_kind="location")
    pid, _sid = _project(store, provider="polza")
    card, j_loc, j_mira, j_kot = _three_refs(store, pid)

    worker = threading.Thread(target=G.run_jobs, args=(store, [j_mira]))
    worker.start()
    assert fake.entered.wait(10)
    # Вторая партия: локация падает → карточка остановлена; «Мира» в работе — её НЕ трогаем.
    out = G.run_jobs(store, [j_loc, j_mira, j_kot])
    assert j_mira not in out["held"] and out["held"] == [j_kot]
    assert G.get_job(store, j_mira)["status"] == "submitting"
    ran = G.run_approved(store, card, confirm=True)
    assert [d["job_id"] for d in ran["done"]] == [j_kot]
    assert [p["job_id"] for p in ran["in_progress"]] == [j_mira] and ran["in_progress"][0]["worker_id"]
    fake.gate.set()
    worker.join(30)
    assert fake.by_target == {"плита локации / Кухня ночью": 1, "разворот / Мира": 1, "разворот / Кот": 1}
    assert G.get_job(store, j_mira)["status"] == "done"
    mira = V.character_by_slug(store, pid, "mira")
    assert [r["version"] for r in V.list_refs(store, pid, "character", mira["id"], "turnaround")] == [1]
    assert sorted(l["job_id"] for l in G.ledger(store, pid)) == sorted([j_mira, j_kot])


def test_second_done_and_late_poll_are_ignored(paid, monkeypatch):
    (_client_, store), fake = paid
    monkeypatch.setattr(settings, "aivideo_enabled", True)
    pid, _sid = _project(store, provider="polza")
    req = G.request_stage(store, pid, "refs", items=["location:1"])
    A.decide(store, req["approval_id"], "approve", actor="ui")
    G.run_pending(store)
    [job] = G.list_jobs(store, approval_id=req["approval_id"])
    job = G.get_job(store, job["id"])
    assert job["status"] == "done" and len(G.ledger(store, pid)) == 1
    # Опрос по устаревшему снимку (статус уже done) не опрашивает и не пишет версию.
    checks = fake.checks
    assert G._finish(store, {**job, "status": "polling"}, fake, 0) is None and fake.checks == checks
    # Второй «готово» того же запуска: ни версии, ни траты; свой файл удалён.
    tmp = settings.tmp_dir / "dup.jpg"
    PR.DryRunImages().fetch({}, tmp, {"_kind": "location"})
    asset = G._save_asset(store, job, tmp, "image", "image/jpeg", "dup")
    assert G._done(store, {**job, "status": "polling"}, asset["id"], "p", 6.0, "provider") is None
    assert len(V.list_refs(store, pid, "location", 1, "location")) == 1 and len(G.ledger(store, pid)) == 1
    with pytest.raises(KeyError):
        store.get_montage_asset(asset["id"])


# --------------------------------------------------------------------------- #
# Сеть: до отправки — повтор сам; после — «под вопросом», сверка баланса, без авто-повтора
# --------------------------------------------------------------------------- #


def _httpx_errors():
    import httpx

    req = httpx.Request("POST", "https://polza.test/api/v1/media")
    return (httpx.ConnectError("TLS/SSL connection has been closed (EOF)", request=req),
            httpx.ReadTimeout("The read operation timed out", request=req))


@pytest.fixture
def no_wait(monkeypatch):
    monkeypatch.setattr(G, "RETRY_BACKOFF_SEC", (0.0,))
    monkeypatch.setattr(G, "BALANCE_SETTLE_SEC", 0.0)


def test_network_phase_classifies_httpx_errors():
    import httpx

    connect, read = _httpx_errors()
    assert PR.network_phase(connect) == "before" and PR.network_phase(read) == "after"
    assert PR.network_phase(httpx.RemoteProtocolError("peer closed")) == "after"
    assert PR.network_phase(httpx.ConnectTimeout("t")) == "before"
    assert PR.network_phase(RuntimeError("x")) is None and PR.network_phase(PR.ProviderError("4xx")) is None


def test_connect_error_before_send_is_retried_without_new_approval(env, monkeypatch, no_wait):
    _client_, store = env
    connect, _read = _httpx_errors()
    fake = _gated(monkeypatch, fail_kind="location", fail_exc=connect, fail_times=2)
    pid, _sid = _project(store, provider="polza")
    card, j_loc, j_mira, j_kot = _three_refs(store, pid)
    out = G.run_pending(store)
    assert len(out["done"]) == 3 and not out["failed"]
    assert fake.by_target["плита локации / Кухня ночью"] == 3  # две неудачи до отправки + успех
    assert G.get_job(store, j_loc)["attempts"] == 1 and len(G.ledger(store, pid)) == 3
    assert len(G.stage_approvals(store, pid)) == 1  # новой карточки нет


def test_connect_error_three_times_fails_unpaid_does_not_stop_batch_and_retries(env, monkeypatch, no_wait):
    _client_, store = env
    connect, _read = _httpx_errors()
    fake = _gated(monkeypatch, fail_kind="location", fail_exc=connect, fail_times=3)
    pid, _sid = _project(store, provider="polza")
    card, j_loc, j_mira, j_kot = _three_refs(store, pid)
    out = G.run_jobs(store, [j_loc, j_mira, j_kot])
    # Сеть — не стоп партии: остальные запуски выполнены.
    assert [d["job_id"] for d in out["done"]] == [j_mira, j_kot] and not out["held"]
    job = G.get_job(store, j_loc)
    assert job["status"] == "failed" and job["params"]["retry_free"] and "не оплачено" in job["error"]
    assert not any(l["job_id"] == j_loc for l in G.ledger(store, pid))
    assert G.stage_approvals(store, pid)[0]["retryable"] == 1
    # «Повторить» одним нажатием — та же карточка, без новой.
    res = G.retry_card(store, card, run=True)
    assert res["retried"] == [j_loc] and [d["job_id"] for d in res["done"]] == [j_loc]
    assert fake.by_target["плита локации / Кухня ночью"] == 4
    with pytest.raises(V.AivError):
        G.retry_job(store, j_loc)  # уже готово


def test_read_timeout_is_uncertain_balance_unchanged_allows_free_retry(env, monkeypatch, no_wait):
    _client_, store = env
    _connect, read = _httpx_errors()
    fake = _gated(monkeypatch, fail_kind="location", fail_exc=read, fail_times=1, money=100.0)
    pid, _sid = _project(store, provider="polza")
    card, j_loc, j_mira, j_kot = _three_refs(store, pid)
    out = G.run_jobs(store, [j_loc, j_mira, j_kot])
    assert len(out["done"]) == 2  # партия не остановлена
    job = G.get_job(store, j_loc)
    assert job["status"] == "uncertain" and fake.by_target["плита локации / Кухня ночью"] == 1  # не повторял сам
    assert job["params"]["balance_check"] == {"before": 100.0, "after": 100.0, "charged": 0.0}
    assert job["params"]["retry_free"] and "не списано" in job["error"]
    G.run_pending(store)  # работник «под вопросом» не трогает
    assert fake.by_target["плита локации / Кухня ночью"] == 1
    G.retry_job(store, j_loc)
    G.run_pending(store)
    assert G.get_job(store, j_loc)["status"] == "done" and fake.by_target["плита локации / Кухня ночью"] == 2


def test_read_timeout_with_charge_or_unknown_balance_needs_new_card(env, monkeypatch, no_wait):
    _client_, store = env
    _connect, read = _httpx_errors()
    fake = _gated(monkeypatch, fail_kind="location", fail_exc=read, fail_times=1, money=100.0, charge_fail=3.0)
    pid, _sid = _project(store, provider="polza")
    card, j_loc, _m, _k = _three_refs(store, pid)
    G.run_jobs(store, [j_loc])
    job = G.get_job(store, j_loc)
    assert job["status"] == "uncertain" and not job["params"]["retry_free"]
    assert job["cost_source"] == "balance" and job["cost_rub"] == pytest.approx(3.0)
    assert [(l["job_id"], l["source"], l["amount_rub"]) for l in G.ledger(store, pid)] == [(j_loc, "balance", 3.0)]
    with pytest.raises(G.BudgetError, match="aiv_regenerate"):
        G.retry_job(store, j_loc)

    # Баланс не узнать — «под вопросом», оценка запуска держится в сумме карточки.
    fake.money, fake.fail_times = None, 1
    pid2 = V.create_ai_project(store, "Второй", provider="polza")["project"]["id"]
    V.save_location(store, pid2, "Двор", "двор днём", slug="yard")
    V.save_location(store, pid2, "Крыша", "крыша ночью", slug="roof")
    req = G.request_stage(store, pid2, "refs", items=["location:" + str(V.location_by_slug(store, pid2, "yard")["id"]),
                                                      "location:" + str(V.location_by_slug(store, pid2, "roof")["id"])])
    A.decide(store, req["approval_id"], "approve", actor="ui")
    store.db.execute("UPDATE approvals SET payload_json = json_set(payload_json, '$.amount_rub', 6.0) WHERE id = ?",
                     (req["approval_id"],))
    fake.fail_kind = "location"
    out = G.run_pending(store)
    yard, roof = sorted(j["id"] for j in G.list_jobs(store, approval_id=req["approval_id"]))
    assert G.get_job(store, yard)["status"] == "uncertain" and "баланс не узнать" in G.get_job(store, yard)["error"]
    # одобрено 6 ₽: 6 «под вопросом» + 6 следующего > 6 — второй держится, а не тратит сверх
    assert G.get_job(store, roof)["status"] == "held" and out["held"] == [roof]


def test_poll_network_error_keeps_polling_without_new_post(paid, monkeypatch):
    (_client_, store), fake = paid
    monkeypatch.setattr(settings, "aivideo_enabled", True)
    _connect, read = _httpx_errors()
    pid, _sid = _project(store, provider="polza")
    req = G.request_stage(store, pid, "refs", items=["location:1"])
    A.decide(store, req["approval_id"], "approve", actor="ui")
    real_check = fake.check
    fake.check = lambda rid: (_ for _ in ()).throw(read)
    G.run_pending(store, poll_timeout=0)
    [job] = G.list_jobs(store, approval_id=req["approval_id"])
    assert job["status"] == "polling" and "сеть при опросе" in job["error"] and fake.posts == 1
    fake.check = real_check
    assert G.resume(store, poll_timeout=0)["finished"] == [job["id"]] and fake.posts == 1


def test_policy_refusal_still_stops_the_batch(env, monkeypatch, no_wait):
    _client_, store = env
    _gated(monkeypatch, fail_kind="location", fail_exc=PR.ProviderError("Polza 400: moderation", policy=True))
    pid, _sid = _project(store, provider="polza")
    card, j_loc, j_mira, j_kot = _three_refs(store, pid)
    out = G.run_jobs(store, [j_loc, j_mira, j_kot])
    assert G.get_job(store, j_loc)["status"] == "blocked_policy" and out["held"] == [j_mira, j_kot]


def test_measured_price_and_sync_media_url(monkeypatch):
    """Замер nano-banana 1K — 3 ₽, 2K — 4.5 ₽ (прайс ×1); не мерили (4K) — прайс ×2. Синхронный ответ
    POST /media с data.url (так часто отвечает видео-модель) — ссылка сразу, без requestId."""
    import httpx

    assert PR.image_price("google/gemini-nano-banana-2.1", "1K") == {"rub": 3.0, "list_rub": 3.0, "measured": True,
                                                                    "note": "замер"}
    assert PR.image_price("google/gemini-nano-banana-2.1", "2K")["rub"] == pytest.approx(4.5)
    assert PR.image_price("google/gemini-nano-banana-2.1", "4K")["rub"] == pytest.approx(13.5)

    class _Resp:
        status_code = 200
        content = b"{}"

        def json(self):
            return {"status": "completed", "data": {"url": "https://s3/v.mp4"}}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp())
    provider = PR.PolzaImages(api_key="k", base_url="https://polza.test/api/v1")
    assert provider.submit({"model": "grok-imagine-video-1-5", "prompt": "x"}, []) == "url:https://s3/v.mp4"
    assert provider.check("url:https://s3/v.mp4") == {"status": "completed", "url": "https://s3/v.mp4"}


# --------------------------------------------------------------------------- #
# Персонаж, который не говорит: speech sfx (звуки пака по эмоции) / none (молчит)
# --------------------------------------------------------------------------- #


def _sfx_pack(tmp_path, monkeypatch):
    """Пак freesound для теста (в рабочей копии паков нет — их ставит Docker): короткие файлы-заглушки."""
    import json
    import subprocess

    packs = tmp_path / "packs"
    (packs / "freesound").mkdir(parents=True)
    files = ["pop-soft.ogg", "pop.ogg", "sad-trombone.ogg", "whip.ogg"]
    for f in files:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "0.3",
                        str(packs / "freesound" / f)], check=True, capture_output=True)
    (packs / "packs.json").write_text(json.dumps({"version": 1, "packs": [
        {"id": "freesound", "kind": "sfx", "items": [{"file": f, "name": f, "tags": []} for f in files]}]}),
        encoding="utf-8")
    monkeypatch.setattr(settings, "motion_packs_dir", packs, raising=False)


def test_sfx_character_beeps_instead_of_tts_and_silent_one_has_no_lines(env, tmp_path, monkeypatch):
    _client_, store = env
    _sfx_pack(tmp_path, monkeypatch)
    pid, sid = _project(store)
    bip = V.save_character(store, pid, "Бип", "робот-пылесос, круглый, белый, синий глаз-диод", slug="bip",
                           notes="не говорит, только пищит", speech="sfx")
    assert bip["speech"] == "sfx" and bip["speech_captions"] is True
    V.save_character(store, pid, "Тихоня", "серая мышь", slug="tihonya", speech="none")
    with pytest.raises(V.AivError, match="speech"):
        V.save_character(store, pid, speech="mumble", character_id=bip["id"])
    with pytest.raises(V.AivError, match="voice.sfx"):
        V.save_character(store, pid, voice={"sfx": "https://example.com/beep.mp3"}, character_id=bip["id"])
    out = V.update_script(store, sid, {"shots": [
        {"duration": 3, "location": "kitchen", "characters": ["mira", "bip"], "camera": {"move": "static"},
         "dialogue": [{"who": "mira", "text": "Ты где?"}, {"who": "bip", "text": "Бип-бип?..", "emotion": "curious"}]},
        {"duration": 3, "location": "kitchen", "characters": ["bip", "tihonya"], "camera": {"move": "static"},
         "dialogue": [{"who": "bip", "text": "Бип!", "emotion": "радостно"},
                      {"who": "tihonya", "text": "Привет"}]}]})
    assert any("tihonya молчит" in w for w in out["warnings"])
    assert [d["who"] for d in out["shots"][1]["dialogue"]] == ["bip"]

    est = G.estimate(store, pid, "voice", sid)
    kinds = {(i["kind"], i.get("voice")) for i in est["items"]}
    assert ("sfx", "sfx:freesound/pop-soft.ogg") in kinds and ("sfx", "sfx:freesound/pop.ogg") in kinds
    assert all(i["estimate_rub"] == 0 for i in est["items"] if i["kind"] == "sfx")
    assert sum(1 for i in est["items"] if i["kind"] == "tts") == 1  # говорит только Мира

    G.request_stage(store, pid, "refs")
    G.request_stage(store, pid, "keyframes", sid)
    from app.aivideo_assemble import assemble

    res = assemble(store, sid)
    assert res["lines"] == 3
    sfx_jobs = [j for j in G.list_jobs(store, script_id=sid) if j["kind"] == "sfx"]
    assert {j["status"] for j in sfx_jobs} == {"done"} and all(j["provider"] == "none" for j in sfx_jobs)
    asset = store.get_montage_asset(V.get_ref(store, sfx_jobs[0]["result_ref_id"])["asset_id"])
    assert asset["original_filename"].startswith("freesound/")  # запись пака, не синтез
    texts = [l["text"] for l in store.get_clip_plan_subtitles(res["clip_id"])["lines"]]
    assert texts == ["Ты где?", "Бип-бип?..", "Бип!"]

    # Подписи к звукам выключены — в субтитрах только речь.
    V.save_character(store, pid, speech_captions=False, character_id=bip["id"])
    res = assemble(store, sid)
    assert [l["text"] for l in store.get_clip_plan_subtitles(res["clip_id"])["lines"]] == ["Ты где?"]
    assert res["lines"] == 3


def test_script_writer_knows_who_does_not_speak(env):
    import json

    _client_, store = env
    pid, sid = _project(store)
    V.save_character(store, pid, "Бип", "робот-пылесос", slug="bip", notes="не говорит, только пищит",
                     speech="sfx")
    V.save_character(store, pid, "Тихоня", "серая мышь", slug="tihonya", speech="none")
    from app.aivideo_script import RULES, script_context, write_script

    ctx = {c["id"]: c for c in script_context(store, sid)["characters"]}
    assert ctx["bip"]["speech"] == "sfx" and ctx["bip"]["notes"] == "не говорит, только пищит"
    assert ctx["tihonya"]["speech"] == "none" and "sfx — НЕ говорит" in RULES
    seen = {}

    class Fake:
        def generate_content(self, model, payload):
            seen["payload"] = payload
            text = json.dumps({"title": "t", "logline": "l", "shots": [
                {"duration": 3, "characters": [{"id": "bip"}, {"id": "tihonya"}], "camera": {"move": "static"},
                 "action": "робот кружит", "dialogue": [{"who": "bip", "text": "Бип?", "emotion": "curious"},
                                                       {"who": "tihonya", "text": "Пи"}]}]}, ensure_ascii=False)
            return {"candidates": [{"content": {"parts": [{"text": text}]}}]}

    out = write_script(store, sid, brief="робот", client=Fake())
    assert [d["who"] for d in out["shots"][0]["dialogue"]] == ["bip"]
    assert '"speech": "sfx"' in seen["payload"]["contents"][0]["parts"][1]["text"]


def test_define_character_tool_sets_speech(env):
    _client_, store = env
    from app.agent_tools import ToolContext, run_tool

    pid, _sid = _project(store)
    out, ok = run_tool(ToolContext(store=store), "aiv_define_character", {
        "project_id": pid, "name": "Бип", "identity_card": "робот", "speech": "sfx", "speech_captions": False,
        "voice": {"sfx": "sfx:freesound/pop-soft.ogg"}})
    assert ok, out
    c = out["character"]
    assert c["speech"] == "sfx" and c["speech_captions"] is False and c["voice"]["sfx"] == "sfx:freesound/pop-soft.ogg"
    assert G.sfx_for(c, "angry") == "sfx:freesound/pop-soft.ogg"  # свой звук важнее эмоции
    assert G.sfx_for({}, "очень зла") == "sfx:freesound/impact-low.ogg"
    assert G.sfx_for(None, "") == G.SFX_DEFAULT


# --------------------------------------------------------------------------- #
# Постоянство персонажей: все листы в референсах кадра, авто-проверка, «проверь» без авто-оплаты
# --------------------------------------------------------------------------- #


def test_keyframe_gets_all_character_sheets_and_plate_logged_in_params(env, monkeypatch):
    _client_, store = env
    pid, sid = _project(store)
    G.request_stage(store, pid, "refs")
    kf = G.request_stage(store, pid, "keyframes", sid)
    job = G.get_job(store, kf["run"]["done"][1]["job_id"])  # план 2: Мира в пижаме + Кот на кухне
    roles = job["params"]["ref_roles"]
    # Первым — кадр прошлого плана той же сцены (постоянство склейки), сразу за ним плита локации
    # (комната не «плывёт» — влезает в лимит всегда), дальше листы персонажей, стиль.
    assert roles == ["план 1: кадр", "плита локации «Кухня ночью»", "mira: разворот", "mira: эмоции",
                     "mira: облик pajamas", "kot: разворот", "kot: эмоции", "стиль"]
    # Без этапа blocking кадр рисуется без 3D-макета — это видно в журнале запуска.
    assert len(job["params"]["refs"]) == len(roles) and job["params"]["missing_refs"] == [
        "блокинг start (этап blocking, 0 ₽)"]
    assert ("Reference images in order: 1 — keyframe of the previous shot 1 (continuity); 2 — background plate "
            "of «Кухня ночью»; 3 — Мира — turnaround model sheet") in job["prompt"]
    assert "Reference 2 is the background plate of «Кухня ночью»" in job["prompt"]

    # Лимит модели: режем с конца по важности — плита и листы первых персонажей остаются, недостающее — в журнал.
    monkeypatch.setitem(PR.MAX_REFS, settings.aivideo_image_model, 4)
    shot2 = V.get_script(store, sid)["shots"][1]
    _p, refs, _c, info = G.build_request(store, {**job, "target_id": shot2["id"]})
    assert len(refs) == 4 and info["roles"] == roles[:4]
    assert "не влезло в лимит 4: kot: эмоции" in info["missing"]
    # Оценка кадров заранее говорит, что отрежется (блокинг + основа + листы + плита + стиль > лимита).
    notes = G.estimate(store, pid, "keyframes", sid, regenerate=True)["notes"]
    assert any("лимит референсов 4" in n and "план 2: не влезут" in n for n in notes), notes


class FakeVision:
    """Gemini-зрение: оценка персонажей кадра (scores {slug: score}) или сбой."""

    def __init__(self, scores=None, error=None):
        self.scores = scores or {}
        self.error = error
        self.calls = 0

    def generate_content(self, model, payload):
        import json

        self.calls += 1
        if self.error:
            raise self.error
        chars = [{"id": k, "score": v, "differences": "другая модель пылесоса" if v < 6 else ""}
                 for k, v in self.scores.items()]
        return {"candidates": [{"content": {"parts": [{"text": json.dumps({"characters": chars},
                                                                          ensure_ascii=False)}]}}]}


def _paid_keyframes(store, monkeypatch, vision):
    monkeypatch.setattr(settings, "aivideo_enabled", True)
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(G, "_vision_client", lambda: vision)
    pid, sid = _project(store, provider="polza")
    req = G.request_stage(store, pid, "refs")
    A.decide(store, req["approval_id"], "approve", actor="ui")
    G.run_pending(store)
    assert vision.calls == 0  # листы и плиты не проверяются — только кадры с персонажами
    req = G.request_stage(store, pid, "keyframes", sid)
    A.decide(store, req["approval_id"], "approve", actor="ui")
    G.run_pending(store)
    return pid, sid


def test_keyframe_auto_consistency_check_marks_needs_review_without_paid_regeneration(paid, monkeypatch):
    (_client_, store), fake = paid
    vision = FakeVision({"mira": 8, "kot": 4})
    pid, sid = _paid_keyframes(store, monkeypatch, vision)
    assert vision.calls == 3 and fake.posts == 9 + 3  # проверка бесплатна, перерисовки сама не было
    view = V.script_view(store, sid)
    flags = {s["idx"]: s["needs_review"] for s in view["shots"]}
    assert flags == {1: False, 2: True, 3: True}  # кот 4/10 в планах 2 и 3
    assert [r["idx"] for r in view["needs_review"]] == [2, 3]
    assert "kot 4/10: другая модель пылесоса" in view["needs_review"][0]["note"]
    sel = next(k for k in view["shots"][1]["keyframes"] if k["selected"])
    assert sel["needs_review"] and sel["score"]["min"] == 4
    assert G.list_jobs(store, script_id=sid)[0]["status"] == "done"
    # aiv_get_script отдаёт то же
    from app.agent_tools import ToolContext, run_tool

    out, ok = run_tool(ToolContext(store=store), "aiv_get_script", {"script_id": sid})
    assert ok and [r["idx"] for r in out["needs_review"]] == [2, 3]
    # Ручная перепроверка (кадр поправили/владелец доволен) снимает отметку.
    G.check_consistency(store, sel["id"], client=FakeVision({"mira": 9, "kot": 7}))
    assert not V.get_ref(store, sel["id"])["needs_review"]


def test_auto_check_failure_does_not_fail_the_job(paid, monkeypatch):
    (_client_, store), fake = paid
    vision = FakeVision(error=RuntimeError("User location is not supported"))
    pid, sid = _paid_keyframes(store, monkeypatch, vision)
    shots = V.script_view(store, sid)["shots"]
    assert all(s["keyframes"] and not s["needs_review"] for s in shots)
    assert "User location" in shots[0]["keyframes"][0]["score"]["error"]
    assert {j["status"] for j in G.list_jobs(store, script_id=sid)} == {"done"}


# --------------------------------------------------------------------------- #
# v2: видео планов, 504 → восстановление id из истории, без второго POST
# --------------------------------------------------------------------------- #


def _motion(store, sid, *idxs):
    for shot in V.get_script(store, sid)["shots"]:
        if shot["idx"] in idxs:
            V.update_shot(store, shot["id"], {"needs_motion": True, "motion_prompt": "кот медленно моргает",
                                              "sound": "гул холодильника"})


class FakeVideo:
    """Платное видео-подделка как у Polza: POST «504» (запрос ушёл, ответа нет), id — в истории."""

    name = "polza"
    paid = True

    def __init__(self, uncertain=True, hidden=0, cost=89.1):
        self.posts = 0
        self.checks = 0
        self.pending_polls = 0
        self.history = []
        self.uncertain = uncertain
        self.hidden = hidden  # столько раз история ещё «не видит» запрос
        self.cost = cost

    def submit(self, params, refs):
        self.posts += 1
        rid = f"vid-{self.posts}"
        self.history.append({"id": rid, "model": params["model"], "createdAt": G._utc_now()})
        self.last_params, self.last_refs = params, refs
        if self.uncertain:
            raise PR.SubmitUncertain("Polza 504: Gateway Time-out")
        return rid

    def recover(self, model, since, exclude):
        if self.hidden > 0:
            self.hidden -= 1
            return None
        for item in self.history:
            if item["model"] == model and item["id"] not in exclude:
                return {"id": item["id"], "status": "pending", "cost": None}
        return None

    def check(self, remote_id):
        self.checks += 1
        if self.pending_polls > 0:
            self.pending_polls -= 1
            return {"status": "pending"}
        return {"status": "completed", "url": "https://example.invalid/v.mp4", "cost_rub": self.cost}

    def fetch(self, result, dest, params):
        return PR.DryRunVideo().fetch(result, dest, params)


@pytest.fixture
def paid_video(env, monkeypatch):
    img, vid = FakePaid(), FakeVideo()
    monkeypatch.setattr(PR, "get_image_provider", lambda name: img if name == "polza" else PR.DryRunImages())
    monkeypatch.setattr(PR, "get_video_provider", lambda name: vid if name == "polza" else PR.DryRunVideo())
    monkeypatch.setattr(G, "RECOVER_INTERVAL_SEC", 0.0)
    monkeypatch.setattr(settings, "aivideo_enabled", True)
    return env, img, vid


def test_video_stage_dry_run_versions_and_regenerate(env):
    _client_, store = env
    pid, sid = _project(store)
    G.request_stage(store, pid, "refs")
    _motion(store, sid, 1, 3)
    # Без кадров видео не просим: элемент blocked, этап не запрошен.
    est = G.estimate(store, pid, "video", sid)
    assert est["blocked"] and any("нет стартового кадра" in p for p in est["problems"])
    with pytest.raises(V.AivError, match="нет стартового кадра"):
        G.request_stage(store, pid, "video", sid)
    kf = G.estimate(store, pid, "keyframes", sid)
    # Черновая модель H3 берёт только старт: конечные кадры не заказываются (видео к ним не приходит);
    # план 2 (статичный, продолжает план 1) стартует с ПОСЛЕДНЕГО кадра готового видео плана 1.
    assert [i["label"] for i in kf["items"]] == ["план 1: старт", "план 3: старт"]
    assert any("план 2: старт — последний кадр видео плана 1" in n for n in kf["notes"])
    assert any("конечные кадры не заказываю" in n for n in kf["notes"])
    G.request_stage(store, pid, "keyframes", sid)
    est = G.estimate(store, pid, "video", sid)
    # Черновая серия без своей модели — MiniMax H3 Max: от 5 с, только стартовый кадр (замер 02.11).
    assert [i["label"] for i in est["items"]] == ["план 1: видео 5 с", "план 3: видео 5 с"]
    assert est["model"] == "minimax/hailuo-3-max" and est["total_rub"] == 0 and est["modes"] == ["start"]
    out = G.request_stage(store, pid, "video", sid)
    assert out["status"] == "done" and len(out["run"]["done"]) == 2
    shot1 = V.get_script(store, sid)["shots"][0]
    [video] = V.list_refs(store, pid, "shot", shot1["id"], "shot_video")
    asset = store.get_montage_asset(video["asset_id"])
    assert asset["kind"] == "video" and asset["duration_sec"] == pytest.approx(5.0, abs=0.3)
    job = G.get_job(store, out["run"]["done"][0]["job_id"])
    req = job["params"]["request"]
    assert req["duration"] == "5" and req["resolution"] == "768p" and "generate_audio" not in req  # H3 звучит сам
    assert job["params"]["mode"] == "start" and len(job["params"]["refs"]) == 1
    assert "гул холодильника" in job["prompt"] and "No music, no speech" in job["prompt"]
    assert "Single continuous shot" in job["prompt"] and "Avoid: cuts" in job["prompt"]  # негатив — блоком
    # Старт плана 2 — последний кадр готового видео плана 1 (вынут ffmpeg'ом, помечен source=video_tail).
    shot2 = V.get_script(store, sid)["shots"][1]
    tail = V.selected_ref(store, pid, "shot", shot2["id"], "keyframe")
    assert tail["prompt"] == f"source=video_tail; video_ref={video['id']}"
    view = V.script_view(store, sid)
    assert view["shots"][1]["start_frame"]["id"] == tail["id"] and view["shots"][1]["start_shared_from"] == 1
    assert G.estimate(store, pid, "video", sid)["count"] == 0  # всё есть
    again = G.regenerate(store, ref_id=video["id"])
    assert again["status"] == "done" and again["estimate"]["stage"] == "video"
    versions = V.list_refs(store, pid, "shot", shot1["id"], "shot_video")
    assert [v["version"] for v in versions] == [1, 2] and versions[-1]["selected"]
    V.select_ref(store, versions[0]["id"])
    assert V.selected_ref(store, pid, "shot", shot1["id"], "shot_video")["version"] == 1
    # Правка движения плана — видео «устарело», кадры — нет.
    V.update_shot(store, shot1["id"], {"motion_prompt": "кот прыгает"})
    assert V.selected_ref(store, pid, "shot", shot1["id"], "shot_video")["stale"]
    assert not V.selected_ref(store, pid, "shot", shot1["id"], "keyframe")["stale"]
    # Правка конца плана — видео «устарело», старт — нет (конечного кадра у H3 нет вовсе).
    V.update_shot(store, shot1["id"], {"end_state": {"characters": [{"id": "mira", "pose": "standing"}]}})
    assert V.selected_ref(store, pid, "shot", shot1["id"], "endframe") is None
    assert not V.selected_ref(store, pid, "shot", shot1["id"], "keyframe")["stale"]
    # Старт плана 2 — хвост именно ВЫБРАННОЙ версии видео плана 1 (новая версия — свой хвост).
    shots_now = V.get_script(store, sid)["shots"]
    assert G.start_frame_ref(store, pid, shots_now, 1)[0]["prompt"] == f"source=video_tail; video_ref={versions[0]['id']}"
    V.select_ref(store, versions[1]["id"])
    assert G.start_frame_ref(store, pid, shots_now, 1)[0]["prompt"] == f"source=video_tail; video_ref={versions[1]['id']}"


def test_video_price_measured_and_project_setting(env):
    _client_, store = env
    pid, sid = _project(store, provider="polza")
    style = V.get_style(store, pid)
    # Без своей модели — по режиму: черновик → MiniMax H3 Max, качество → Seedance 2.0.
    assert style["video_model"] == "minimax/hailuo-3-max" and not style["video_model_set"]
    assert style["video_provider"] == "polza" and not style["video_caps"]["first_last"]
    assert G.video_model_for(style, "quality") == "bytedance/seedance-2"
    assert PR.video_price("bytedance/seedance-2-fast", "720p", 4)["rub"] == pytest.approx(89.1)
    assert PR.video_price("grok-imagine-video-1-5", "720p", 4)["rub"] == pytest.approx(12.15)
    # Не замерено — прайс видео ×1.25 (оба замера видео Polza = прайс ×1).
    assert PR.video_price("bytedance/seedance-2-fast", "480p", 4)["rub"] == pytest.approx(52.31, abs=0.01)
    assert PR.video_price("bytedance/seedance-2", "720p", 4)["rub"] == pytest.approx(138.38, abs=0.01)
    assert PR.video_price("bytedance/seedance-2", "1080p", 4)["rub"] == pytest.approx(344.25, abs=0.01)
    assert PR.video_price("bytedance/seedance-2-5", "720p", 4)["rub"] == pytest.approx(212.63, abs=0.01)
    h3 = PR.video_price("minimax/hailuo-3-max", "720p", 5)  # 720p у H3 — это 768p
    assert h3["rub"] == pytest.approx(56.97, abs=0.01) and "9.11526" in h3["note"]
    shot = V.get_script(store, sid)["shots"][0]
    # Длинные реплики → видео длиннее (до 8 с); короче минимума модели не бывает (H3 — от 5 с).
    assert G.video_seconds({**shot, "duration": 2}, "bytedance/seedance-2-fast") == 4
    assert G.video_seconds({**shot, "duration": 3}, "minimax/hailuo-3-max") == 5
    assert G.video_seconds({**shot, "duration": 3, "dialogue": [{"text": "x" * 90}]},
                           "bytedance/seedance-2-fast") == 8
    with pytest.raises(V.AivError, match="video_model"):
        V.update_style(store, pid, {"video_model": "kling/v3"})
    V.update_style(store, pid, {"video_model": "grok-imagine-video-1-5", "video_resolution": "480p"})
    assert V.get_style(store, pid)["video_model_set"]
    assert PR.video_params("grok-imagine-video-1-5", "p", 4, "480p").get("generate_audio") is None
    V.update_style(store, pid, {"video_resolution": "1080p"})
    with pytest.raises(ValueError, match="video_provider"):
        V.update_style(store, pid, {"video_provider": "kie"})  # такого адаптера нет
    with pytest.raises(ValueError, match="video_provider"):
        V.update_style(store, pid, {"video_provider": "genapi"})  # слот на будущее — не подключён


def _paid_frames(store, sid, pid, motion=(1,)):
    _motion(store, sid, *motion)
    for stage, kw in (("refs", {}), ("keyframes", {"script_id": sid})):
        req = G.request_stage(store, pid, stage, **kw)
        A.decide(store, req["approval_id"], "approve", actor="ui")
        G.run_pending(store)


def test_video_504_recovers_id_from_history_without_second_post(paid_video):
    (_client_, store), img, vid = paid_video
    pid, sid = _project(store, provider="polza")
    V.update_style(store, pid, {"video_model": "bytedance/seedance-2-fast"})
    _paid_frames(store, sid, pid)
    req = G.request_stage(store, pid, "video", sid)
    assert req["status"] == "awaiting_approval" and req["estimate"]["total_rub"] == pytest.approx(89.1)
    A.decide(store, req["approval_id"], "approve", actor="ui")
    out = G.run_pending(store)
    assert vid.posts == 1 and len(out["done"]) == 1, out
    [job] = [j for j in G.list_jobs(store, approval_id=req["approval_id"]) if j["kind"] == "video"]
    assert job["status"] == "done" and job["remote_id"] == "vid-1" and job["submitted_at"]
    led = [l for l in G.ledger(store, pid) if l["stage"] == "video"]
    assert len(led) == 1 and led[0]["amount_rub"] == pytest.approx(89.1)
    # Первый+последний кадр: два кадра, листов персонажей нет (у Seedance режимы взаимоисключающие).
    assert vid.last_params["duration"] == "4" and len(vid.last_refs) == 2
    assert "_reference_images" not in vid.last_params and vid.last_params["_end"]


def test_video_recovery_survives_restart_and_never_resubmits(paid_video, monkeypatch):
    (_client_, store), img, vid = paid_video
    pid, sid = _project(store, provider="polza")
    V.update_style(store, pid, {"video_model": "bytedance/seedance-2-fast"})
    shots = V.get_script(store, sid)["shots"]
    for shot in shots[1:]:  # без сцепки: оба видео уходят подряд, каждое — из своей пары кадров
        V.update_shot(store, shot["id"], {"continues_from_prev": False})
    _paid_frames(store, sid, pid, motion=(1, 2))
    vid.hidden = 10**6  # история ещё не показывает запрос
    req = G.request_stage(store, pid, "video", sid)
    A.decide(store, req["approval_id"], "approve", actor="ui")
    G.run_pending(store)
    jobs = sorted(G.list_jobs(store, approval_id=req["approval_id"]), key=lambda j: j["id"])
    assert [j["status"] for j in jobs] == ["recovering", "recovering"] and vid.posts == 2
    # «Перезапуск»: новый адаптер с той же историей; деньги уже зарезервированы, POST не повторяется.
    fresh = FakeVideo()
    fresh.history = list(vid.history)
    monkeypatch.setattr(PR, "get_video_provider", lambda name: fresh)
    res = G.resume(store)
    assert sorted(res["recovered"]) == [j["id"] for j in jobs] and fresh.posts == 0
    jobs = sorted(G.list_jobs(store, approval_id=req["approval_id"]), key=lambda j: j["id"])
    # Каждому запуску — свой id (ранний запрос — раннему запуску), оба готовы.
    assert [(j["status"], j["remote_id"]) for j in jobs] == [("done", "vid-1"), ("done", "vid-2")]
    # Запрос так и не появился у провайдера — после срока «под вопросом» (1.1 uncertain), без нового POST;
    # повтор без новой карточки нельзя (не подтверждено, что не оплачено).
    store.db.execute("UPDATE aiv_gen_jobs SET status = 'recovering', remote_id = '', submitted_at = ? WHERE id = ?",
                     ("2020-01-01T00:00:00", jobs[0]["id"]))
    fresh.history = []
    G.resume(store)
    lost = G.get_job(store, jobs[0]["id"])
    assert lost["status"] == "uncertain" and "не появился" in lost["error"] and fresh.posts == 0
    with pytest.raises(G.BudgetError):
        G.retry_job(store, jobs[0]["id"])
    # Исполнитель умер после записи времени отправки — ищем у провайдера (recovering), POST не повторяем.
    fresh.hidden = 10**6
    store.db.execute("UPDATE aiv_gen_jobs SET status = 'submitting', remote_id = '', submitted_at = ? WHERE id = ?",
                     (G._utc_now(), jobs[1]["id"]))
    G.resume(store)
    assert G.get_job(store, jobs[1]["id"])["status"] == "recovering" and fresh.posts == 0
    # Время не записано — как в 1.1: сам не повторяю, «проверь баланс».
    store.db.execute("UPDATE aiv_gen_jobs SET status = 'submitting', remote_id = '', submitted_at = '' WHERE id = ?",
                     (jobs[1]["id"],))
    assert G.resume(store)["lost"] == [jobs[1]["id"]]


def test_polza_video_adapter_504_and_history_lookup(monkeypatch, tmp_path):
    import httpx

    from app.aivideo_providers import PolzaVideo

    start = tmp_path / "start.jpg"
    start.write_bytes(b"\xff\xd8\xff\xd9")
    monkeypatch.setattr("app.aivideo_providers.start_frame_data_url", lambda p: "data:image/jpeg;base64,AA==")
    seen = {}

    class _Resp:
        def __init__(self, code, payload):
            self.status_code, self._payload = code, payload
            self.content = b"{}"

        def json(self):
            return self._payload

    def post_504(url, headers=None, json=None, timeout=None):
        seen["body"] = json
        return _Resp(504, {"error": {"message": "Gateway Time-out"}})

    monkeypatch.setattr(httpx, "post", post_504)
    adapter = PolzaVideo(api_key="k", base_url="https://polza.test/api/v1")
    params = PR.video_params("bytedance/seedance-2-fast", "кадр", 4, "720p")
    with pytest.raises(PR.SubmitUncertain):
        adapter.submit(params, [start])
    assert seen["body"]["input"]["duration"] == "4" and seen["body"]["input"]["generate_audio"] is True
    assert seen["body"]["input"]["images"] == [{"type": "base64", "data": "data:image/jpeg;base64,AA=="}]

    def post_down(url, headers=None, json=None, timeout=None):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "post", post_down)
    with pytest.raises(httpx.ConnectError) as info:
        adapter.submit(params, [start])
    assert PR.network_phase(info.value) == "before"  # не ушло — не списано: aivideo_gen повторит сам (1.1)

    items = [
        {"id": "new2", "model": "bytedance/seedance-2-fast", "status": "pending", "createdAt": "2026-11-01T12:00:40.5Z"},
        {"id": "new1", "model": "bytedance/seedance-2-fast", "status": "pending", "cost": "89.1",
         "createdAt": "2026-11-01T12:00:03Z"},
        {"id": "other", "model": "grok-imagine-video-1-5", "createdAt": "2026-11-01T12:00:05Z"},
        {"id": "old", "model": "bytedance/seedance-2-fast", "createdAt": "2026-11-01T11:00:00Z"},
    ]

    def fake_get(url, headers=None, params=None, timeout=None):
        seen["get"] = (url, params)
        return _Resp(200, {"items": items})

    monkeypatch.setattr(httpx, "get", fake_get)
    found = adapter.recover("bytedance/seedance-2-fast", "2026-11-01T12:00:00", set())
    assert found == {"id": "new1", "status": "pending", "cost": 89.1}
    assert seen["get"][0].endswith("/history/generations") and seen["get"][1]["requestType"] == "video"
    assert adapter.recover("bytedance/seedance-2-fast", "2026-11-01T12:00:00", {"new1"})["id"] == "new2"
    assert adapter.recover("bytedance/seedance-2-fast", "2026-11-01T12:01:00", set()) is None


# --------------------------------------------------------------------------- #
# v2/v3: постоянство склеек — состояние планов, пары ключевых кадров (конец N = старт N+1)
# --------------------------------------------------------------------------- #

STATE_SHOTS = [
    {"duration": 4, "location": "kitchen", "needs_motion": True, "characters": [{"id": "mira"}],
     "camera": {"move": "static"}, "action": "Мира сидит на полу и стягивает левый носок",
     "state": {"characters": [{"id": "mira", "pose": "sitting", "facing": "camera", "holding": "ничего",
                               "clothing": "оба носка надеты"}],
               "props": [{"name": "носок", "state": "на левой ноге"}]},
     "dialogue": [{"who": "mira", "text": "Жарко…"}]},
    {"duration": 4, "location": "kitchen", "needs_motion": True, "characters": [{"id": "mira"}],
     "camera": {"move": "push_in"}, "action": "Мира держит носок и оглядывается",
     "state": {"characters": [{"id": "mira", "pose": "standing", "facing": "camera", "holding": "носок",
                               "clothing": "один носок снят"}],
               "props": [{"name": "носок", "state": "в руке"}]},
     "end_state": {"characters": [{"id": "mira", "pose": "standing", "facing": "left", "holding": "носок"}],
                   "props": [{"name": "носок", "state": "в руке", "count": 1}]}, "dialogue": []},
    {"duration": 4, "location": "kitchen", "continues_from_prev": False, "characters": [{"id": "kot"}],
     "camera": {"move": "pan_left"}, "action": "кот смотрит из-под стола",
     "state": {"characters": [{"id": "kot", "pose": "lying", "facing": "left"}, {"id": "mira", "pose": "sitting"}]},
     "dialogue": []},
]


def _first_last(store, pid):
    """Пары ключевых кадров — у модели с первым+последним кадром (H3 по умолчанию — только старт)."""
    V.update_style(store, pid, {"video_model": "bytedance/seedance-2"}, owner=True)


def _state_project(store, provider="none"):
    pid, sid = _project(store, provider=provider)
    out = V.update_script(store, sid, {"shots": STATE_SHOTS})
    return pid, sid, out


def test_shot_state_defaults_and_warnings(env):
    _client_, store = env
    pid, sid, out = _state_project(store)
    _first_last(store, pid)  # пары кадров: модель с первым+последним кадром
    shots = out["shots"]
    assert [s["continues_from_prev"] for s in shots] == [False, True, False]  # первый — никогда; 3 — явно
    assert shots[0]["state"]["characters"][0] == {"id": "mira", "pose": "sitting", "facing": "camera",
                                                  "holding": "ничего", "clothing": "оба носка надеты"}
    assert shots[1]["state"]["props"] == [{"name": "носок", "state": "в руке"}]
    assert shots[1]["end_state"]["props"] == [{"name": "носок", "state": "в руке", "count": 1}]
    # Мира в state плана 3, но не в кадре — убрана с предупреждением.
    assert [c["id"] for c in shots[2]["state"]["characters"]] == ["kot"]
    warnings = out["warnings"]
    assert any("mira" in w and "в state" in w for w in warnings)
    # Поза sitting → standing в продолжении: подсказка «покажи это действием».
    assert any("план 2" in w and "поза «sitting» → «standing»" in w for w in warnings), warnings
    assert any("носок «на левой ноге» → «в руке»" in w for w in warnings)
    # Конец плана — старт продолжения: у плана 1 нет своего end_state — это state плана 2.
    assert V.end_state_of(shots[0], shots[1]) == shots[1]["state"]
    assert V.end_state_of(shots[1], shots[2]) == shots[1]["end_state"]  # план 3 не продолжает
    # Правка состояния — кадр «устарел»; первый план продолжать не может.
    G.request_stage(store, pid, "refs")
    G.request_stage(store, pid, "keyframes", sid)
    V.update_shot(store, shots[0]["id"], {"continues_from_prev": True})
    assert V.get_shot(store, shots[0]["id"])["continues_from_prev"] is False
    kf = V.selected_ref(store, pid, "shot", shots[2]["id"], "keyframe")
    V.update_shot(store, shots[2]["id"], {"state": {"characters": [{"id": "kot", "pose": "sitting"}]}})
    assert V.get_ref(store, kf["id"])["stale"]
    # Старт сцепленного плана 2 поменялся — это конец плана 1: его конечный кадр устарел.
    e1 = V.selected_ref(store, pid, "shot", shots[0]["id"], "endframe")
    V.update_shot(store, shots[1]["id"], {"state": {"characters": [{"id": "mira", "pose": "kneeling"}]}})
    assert V.get_ref(store, e1["id"])["stale"]
    from app.aivideo_script import script_schema

    shot_schema = script_schema()["properties"]["shots"]["items"]
    assert {"state", "end_state", "continues_from_prev", "needs_motion"} <= set(shot_schema["required"])
    assert "count" in shot_schema["properties"]["state"]["properties"]["props"]["items"]["properties"]


def test_keyframe_pairs_share_end_and_start_frames(env):
    """v3: старт и конец каждого плана рисует картинковая модель; конец плана N — он же старт
    сцепленного N+1 (N+1 кадров на цепочку); ничего не вынимается из видео — ошибки не копятся."""
    _client_, store = env
    pid, sid, out = _state_project(store)
    _first_last(store, pid)  # пары кадров: модель с первым+последним кадром
    shots = out["shots"]
    G.request_stage(store, pid, "refs")
    est = G.estimate(store, pid, "keyframes", sid)
    assert [i["label"] for i in est["items"]] == ["план 1: старт", "план 1: конец", "план 2: конец", "план 3"]
    assert any("план 2: старт — конечный кадр плана 1" in n for n in est["notes"])
    assert any("пары кадров: 2 конечных" in n for n in est["notes"])
    kf = G.request_stage(store, pid, "keyframes", sid)
    jobs = {(j["target_id"], j["ref_kind"]): j for j in (G.get_job(store, d["job_id"]) for d in kf["run"]["done"])}
    # Конец плана 1 = старт плана 2: рисуется по состоянию плана 2, первым референсом — старт плана 1.
    e1 = jobs[(shots[0]["id"], "endframe")]
    assert "LAST frame of shot 1" in e1["prompt"] and "END of this shot" in e1["prompt"]
    assert "pose standing" in e1["prompt"] and "holding носок" in e1["prompt"]
    assert e1["params"]["ref_roles"][0] == "план 1: старт"
    # Конец плана 2 рисуется со стартом плана 2 — а это конечный кадр плана 1.
    e2 = jobs[(shots[1]["id"], "endframe")]
    assert e2["params"]["ref_roles"][0] == "план 2: старт (конец плана 1)"
    assert "facing left" in e2["prompt"] and "exactly one носок" in e2["prompt"]
    # Кадр плана 3 (новый ракурс) — с конечным кадром плана 2 первым референсом и своим состоянием.
    s3 = jobs[(shots[2]["id"], "keyframe")]
    assert s3["params"]["ref_roles"][0] == "план 2: конечный кадр"
    assert "end frame of the previous shot 2" in s3["prompt"] and "pose lying" in s3["prompt"]
    view = V.script_view(store, sid)
    e1_ref = V.selected_ref(store, pid, "shot", shots[0]["id"], "endframe")
    assert view["shots"][1]["start_frame"]["id"] == e1_ref["id"] and view["shots"][1]["start_shared_from"] == 1
    assert view["shots"][1]["keyframes"] == []  # своего старта у сцепленного плана нет — общий кадр
    # Видео: модель интерполирует старт → конец; старт плана 2 — тот же кадр, что конец плана 1.
    V.update_style(store, pid, {"video_model": "bytedance/seedance-2"}, owner=True)  # H3 — только старт
    vid = G.request_stage(store, pid, "video", sid)
    labels = [d["label"] for d in vid["run"]["done"]]
    assert labels == ["план 1: видео 4 с", "план 2: видео 4 с"]  # Seedance 2 — от 4 с
    v1, v2 = (G.get_job(store, d["job_id"]) for d in vid["run"]["done"])
    assert v2["params"]["start_ref_id"] == e1_ref["id"] and v2["params"]["mode"] == "first_last"
    assert v1["params"]["end_ref_id"] == e1_ref["id"]
    assert "At the end of the shot" in v1["prompt"] and "pose standing" in v1["prompt"]
    assert not any(r["prompt"].startswith("chain:") for r in V.list_refs(store, pid, "shot"))
    # Новое видео плана 1 ничего не сцепляет: видео плана 2 не устарело.
    G.regenerate(store, ref_id=V.selected_ref(store, pid, "shot", shots[0]["id"], "shot_video")["id"])
    assert not V.selected_ref(store, pid, "shot", shots[1]["id"], "shot_video")["stale"]
    # Новый конечный кадр плана 1 — устарели видео плана 1 и плана 2 (это его старт).
    G.regenerate(store, target=f"shot:{shots[0]['id']}:endframe")
    assert V.selected_ref(store, pid, "shot", shots[0]["id"], "shot_video")["stale"]
    assert V.selected_ref(store, pid, "shot", shots[1]["id"], "shot_video")["stale"]
    assert G.estimate(store, pid, "video", sid)["count"] == 2


def test_episode_estimate_five_shots_seedance_vs_hailuo(env, monkeypatch):
    """Серия 5 × 4 с, все с видео, одна сцена. Черновик (H3 — только старт): кадр — один старт, конечные
    не заказываются (видео к ним не приходит), старты продолжений — хвосты готовых видео; качество
    (Seedance — первый+последний кадр): 6 кадров (старт + 5 концов)."""
    _client_, store = env
    pid, sid = _project(store, provider="polza")
    monkeypatch.setattr(PR, "MEASURED_IMAGE_PRICES", {"google/gemini-nano-banana-2.1": {"1K": 3.0, "2K": 4.5}})
    shots = [{"duration": 4, "location": "kitchen", "needs_motion": True, "characters": [{"id": "mira"}],
              "camera": {"move": "static"}, "action": f"действие {i}", "dialogue": []} for i in range(5)]
    V.update_script(store, sid, {"shots": shots})
    est = G.estimate(store, pid, "keyframes", sid)
    assert est["count"] == 1 and est["total_rub"] == pytest.approx(3.0)  # черновик 1K: 1 × 3 ₽, без концов
    assert not any(i["ref_kind"] == "endframe" for i in est["items"])
    assert sum("последний кадр видео плана" in n for n in est["notes"]) == 4
    first = V.get_script(store, sid)["shots"][0]
    asset = store.create_montage_asset(kind="image", label="k", file_path=str(Path(__file__)),
                                       original_filename="k.jpg", mime_type="image/jpeg", size_bytes=1)
    V.add_ref(store, pid, "shot", first["id"], "keyframe", asset["id"])
    # Видео: старт плана 1 — кадр, планов 2–5 — хвост видео прошлого плана из этой же партии (ждут его).
    draft = G.estimate(store, pid, "video", sid)
    assert draft["model"] == "minimax/hailuo-3-max" and draft["count"] == 5 and not draft["blocked"]
    assert [i["start"] for i in draft["items"]] == ["keyframe"] + ["video_tail"] * 4
    assert draft["total_rub"] == pytest.approx(5 * 56.97, abs=0.05)  # 5 с минимум × 9.115 ₽/с × 1.25
    # Только план 3 (видео плана 2 нет и не заказано) — нечего брать стартом: blocked.
    only3 = G.estimate(store, pid, "video", sid, items=[f"shot:{V.get_script(store, sid)['shots'][2]['id']}"])
    assert only3["blocked"] and "сначала видео плана 2" in only3["blocked"][0]
    for shot in V.get_script(store, sid)["shots"]:  # для оценки качества — пары кадров есть
        for kind in ("keyframe", "endframe"):
            asset = store.create_montage_asset(kind="image", label="k", file_path=str(Path(__file__)),
                                               original_filename="k.jpg", mime_type="image/jpeg", size_bytes=1)
            V.add_ref(store, pid, "shot", shot["id"], kind, asset["id"])
    V.update_script(store, sid, {"quality": "quality"})
    quality = G.estimate(store, pid, "video", sid)
    assert quality["model"] == "bytedance/seedance-2" and quality["modes"] == ["first_last"]
    assert quality["total_rub"] == pytest.approx(5 * 138.375, abs=0.05)  # 4 с × 27.675 ₽/с × 1.25
    assert G.estimate(store, pid, "keyframes", sid, regenerate=True)["total_rub"] == pytest.approx(27.0)  # 2K


def test_endframe_waits_for_its_start_frame_in_paid_run(paid_video, monkeypatch):
    (_client_, store), img, vid = paid_video
    vid.uncertain = False
    pid, sid, out = _state_project(store, provider="polza")
    V.update_style(store, pid, {"video_model": "bytedance/seedance-2-fast"})
    req = G.request_stage(store, pid, "refs")
    A.decide(store, req["approval_id"], "approve", actor="ui")
    G.run_pending(store)
    req = G.request_stage(store, pid, "keyframes", sid)
    A.decide(store, req["approval_id"], "approve", actor="ui")
    posts = img.posts
    img.pending_polls = 10**6  # старт плана 1 ещё рисуется
    first = G.run_pending(store, poll_timeout=0)
    # Конец плана 1 рисуется ПО старту — ждёт его; остальная партия тоже ждёт (без лишних POST).
    assert img.posts == posts + 1 and first["polling"] and len(first["waiting"]) == 3
    img.pending_polls = 0
    second = G.run_pending(store, poll_timeout=0)
    assert img.posts == posts + 4 and second["resumed"]["finished"]
    e1 = next(j for j in G.list_jobs(store, approval_id=req["approval_id"])
              if j["ref_kind"] == "endframe" and j["target_id"] == out["shots"][0]["id"])
    assert G.get_job(store, e1["id"])["params"]["ref_roles"][0] == "план 1: старт"
    req = G.request_stage(store, pid, "video", sid)
    # Два видео по 89.1 ₽ (seedance-2-fast 720p, замер), старт плана 2 — общий кадр, 0 ₽.
    assert req["estimate"]["total_rub"] == pytest.approx(178.2) and req["estimate"]["count"] == 2
    assert "первый+последний кадр" in A.get_approval(store, req["approval_id"])["question"]


def test_continuity_check_flags_mismatches_with_fake_gemini(env):
    _client_, store = env
    pid, sid, out = _state_project(store)
    _first_last(store, pid)  # пары кадров: модель с первым+последним кадром
    shots = out["shots"]
    V.update_shot(store, shots[1]["id"], {"continues_from_prev": False})  # план 2 — свой старт
    G.request_stage(store, pid, "refs")
    G.request_stage(store, pid, "keyframes", sid)
    import json

    seen = {}

    class Fake:
        def generate_content(self, model, payload):
            seen["parts"] = payload["contents"][0]["parts"]
            text = json.dumps({"checks": [
                {"idx": 1, "kind": "pair", "score": 9, "ok": True, "mismatches": []},
                {"idx": 2, "kind": "cut", "score": 4, "ok": False,
                 "mismatches": [{"what": "pose", "detail": "Мира сидит, а заявлено стоит"}]},
                {"idx": 2, "kind": "pair", "score": 8, "ok": True, "mismatches": []},
                {"idx": 3, "kind": "cut", "score": 9, "ok": True, "mismatches": []}]}, ensure_ascii=False)
            return {"candidates": [{"content": {"parts": [{"text": text}]}}]}

    res = G.check_continuity(store, sid, client=Fake())
    assert res["flagged"] == [2] and "план 2" in res["note"]
    images = [p for p in seen["parts"] if "inline_data" in p]
    assert len(images) == 8  # пары планов 1 и 2 + склейки 2 и 3, по два кадра
    assert any("kind=pair" in p.get("text", "") for p in seen["parts"])
    assert any("standing" in p.get("text", "") for p in seen["parts"])
    view = V.script_view(store, sid)
    cont = view["shots"][1]["continuity"]
    assert cont["mismatches"][0]["what"] == "pose" and cont["checks"] == ["cut", "pair"]
    flags = G.continuity_flags(store, sid)
    assert [(f["idx"], f["ok"], f["outdated"]) for f in flags] == [(1, True, False), (2, False, False),
                                                                   (3, True, False)]
    # Перерисовали старт плана 2 — проверка устарела; оценка видео показывает флаги.
    G.regenerate(store, target=f"shot:{shots[1]['id']}:keyframe")
    assert G.continuity_flags(store, sid)[1]["outdated"] is True
    assert G.estimate(store, pid, "video", sid)["continuity"][1]["idx"] == 2
    # Без Gemini — пропуск, этап video не блокируется.
    assert G.check_continuity(store, sid)["skipped"]


# --------------------------------------------------------------------------- #
# v2: сборка — видео планов слоями со звуком, склейки по длительностям, реплики, музыка
# --------------------------------------------------------------------------- #


def test_assembly_v2_video_layers_timing_lines_and_music(env):
    _client_, store = env
    tmp_path = settings.audio_dir
    tmp_path.mkdir(parents=True, exist_ok=True)
    pid, sid, out = _state_project(store)
    V.update_style(store, pid, {"video_model": "bytedance/seedance-2-fast"})
    shots = out["shots"]
    # План 1 — 1 реплика; план 2 — видео обрезано окном 0.5–3.0 с; план 3 — стоп-кадр (Ken Burns).
    V.update_shot(store, shots[1]["id"], {"video_in": 0.5, "video_out": 3.0,
                                          "dialogue": [{"who": "mira", "text": "Где второй?"}]})
    G.request_stage(store, pid, "refs")
    G.request_stage(store, pid, "keyframes", sid)
    G.request_stage(store, pid, "video", sid)
    import subprocess

    music = tmp_path / "music.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=15",
                    str(music)], check=True)
    track = store.create_audio_track("тест", music)
    from app.aivideo_assemble import LEAD_SEC, MUSIC_DUCK_DB, VIDEO_GAIN_DB, assemble

    res = assemble(store, sid, music_track_id=track["id"])
    assert res["video_shots"] == 2 and res["still_shots"] == 1, res
    plan = store.get_clip_plan(res["clip_id"])
    layers = {l["id"]: l for l in plan["render_settings"]["layers"]}
    v1, v2, s3 = layers["aiv_shot_1"], layers["aiv_shot_2"], layers["aiv_shot_3"]
    assert v1["type"] == "video" and v2["type"] == "video" and s3["type"] == "image"
    assert v1["props"]["audio"] is True and v1["props"]["gain_db"] == VIDEO_GAIN_DB and v1["props"]["freeze"]
    # Склейки по настоящим длительностям: видео 1 — 4 с целиком, видео 2 — окно 2.5 с с секунды 0.5.
    assert v1["start"] == 0 and v1["end"] == pytest.approx(4.0, abs=0.15)
    assert v2["props"]["from"] == pytest.approx(0.5)
    assert v2["start"] == pytest.approx(v1["end"]) and v2["end"] - v2["start"] == pytest.approx(2.5, abs=0.05)
    assert s3["start"] == pytest.approx(v2["end"]) and s3["end"] - s3["start"] == pytest.approx(4.0)
    assert "scale" in s3.get("keys", {}) or "x" in s3.get("keys", {})
    # Реплики — каждая своим слоем на своём времени плана; субтитры — те же окна.
    lines = [l for l in plan["render_settings"]["layers"] if l["id"].startswith("aiv_line_")]
    assert [l["id"] for l in lines] == ["aiv_line_1_1", "aiv_line_2_1"]
    assert lines[0]["start"] == pytest.approx(LEAD_SEC) and lines[1]["start"] == pytest.approx(v2["start"] + LEAD_SEC)
    assert all(l["props"]["mode"] == "track" for l in lines)
    subs = store.get_clip_plan_subtitles(res["clip_id"])["lines"]
    assert [s["text"] for s in subs] == ["Жарко…", "Где второй?"]
    assert subs[1]["start"] == pytest.approx(lines[1]["start"])
    assert any(l["type"] == "captions" for l in plan["render_settings"]["layers"])
    # Музыка под репликами приглушена.
    rs = plan["render_settings"]
    assert rs["music_on"] and rs["track_id"] == track["id"]
    ducks = rs["audio"]["music_duck"]
    assert len(ducks) == 2 and all(d["gain_db"] == MUSIC_DUCK_DB for d in ducks)
    assert ducks[0]["start"] <= lines[0]["start"] <= ducks[0]["end"]
    assert res["duration"] == pytest.approx(s3["end"], abs=0.01)

    from app.motion_render import CompositorMissing, compositor_command

    try:
        compositor_command()
    except CompositorMissing:
        pytest.skip("компоновщик не собран (npm run build:motion)")
    from app.dubbing import probe_duration
    from app.render import ClipRenderService

    preset = store.create_ffmpeg_preset("aiv v2", output_width=180, output_height=320, fps=15)
    clip = ClipRenderService(store).render_clip_plan(res["clip_id"], ffmpeg_preset_id=preset["id"])
    assert clip["status"] == "succeeded", clip.get("error")
    assert abs(probe_duration(Path(clip["output_path"])) - res["duration"]) < 1.0


def test_assembly_uses_shared_frame_and_explains_missing_end(env):
    _client_, store = env
    pid, sid, out = _state_project(store)
    _first_last(store, pid)  # пары кадров: модель с первым+последним кадром
    shots = out["shots"]
    G.request_stage(store, pid, "refs")
    # Только старт плана 1: у сцепленного плана 2 старта (конца плана 1) ещё нет.
    G.request_stage(store, pid, "keyframes", sid, items=[f"shot:{shots[0]['id']}:keyframe",
                                                          f"shot:{shots[2]['id']}:keyframe"])
    from app.aivideo_assemble import assemble

    with pytest.raises(V.AivError, match="старт — конечный кадр плана 1"):
        assemble(store, sid)
    G.request_stage(store, pid, "keyframes", sid)  # пары кадров — стоп-кадры собираются без видео
    res = assemble(store, sid)
    assert res["still_shots"] == 3
    layers = {l["id"]: l for l in store.get_clip_plan(res["clip_id"])["render_settings"]["layers"]}
    e1 = V.selected_ref(store, pid, "shot", shots[0]["id"], "endframe")
    assert int(layers["aiv_shot_2"]["props"]["asset_id"]) == int(e1["asset_id"])  # общий кадр


def test_v2_tools_and_rest_continuity(env):
    client, store = env
    from app import modules
    from app.agent_tools import ToolContext, list_tools, run_tool

    assert {"aiv_check_continuity", "aiv_check_video", "aiv_block_shot"} <= set(modules.MODULES_BY_ID["ai_video"].tools)
    tools = {t.name: t for t in list_tools(include_discovery=True, store=store)}
    assert {"video", "blocking"} <= set(tools["aiv_request_approval"].parameters["properties"]["stage"]["enum"])
    shot_props = tools["aiv_update_script"].parameters["properties"]["shots"]["items"]["properties"]
    assert {"state", "end_state", "continues_from_prev", "sound", "video_in", "video_out"} <= set(shot_props)
    assert "constraints" in tools["aiv_define_character"].parameters["properties"]
    pid, sid, _out = _state_project(store)
    ctx = ToolContext(store=store)
    res, ok = run_tool(ctx, "aiv_check_continuity", {"script_id": sid})
    assert ok and res["skipped"]
    res, ok = run_tool(ctx, "aiv_update_style", {"project_id": pid, "video_model": "grok-imagine-video-1-5"})
    assert ok and res["style"]["video_model"] == "grok-imagine-video-1-5"
    resp = client.post(f"/api/ai-video/scripts/{sid}/continuity")
    assert resp.status_code == 200 and resp.json()["skipped"]


# --------------------------------------------------------------------------- #
# Слияние 1.1 + v2: немые персонажи в сборке с видео, сеть видео/картинок
# --------------------------------------------------------------------------- #


def test_assembly_v2_with_sfx_character_and_video(env, tmp_path, monkeypatch):
    _client_, store = env
    _sfx_pack(tmp_path, monkeypatch)
    pid, sid = _project(store)
    V.save_character(store, pid, "Бип", "робот-пылесос, круглый, белый, синий глаз-диод", slug="bip", speech="sfx",
                     constraints="плоский диск на полу, без рук и ног; ровно один")
    V.save_character(store, pid, "Тихоня", "серая мышь", slug="tihonya", speech="none")
    V.update_script(store, sid, {"shots": [
        {"duration": 4, "location": "kitchen", "needs_motion": True, "characters": ["mira", "bip"],
         "camera": {"move": "static"}, "action": "Бип выезжает из-под стола",
         "dialogue": [{"who": "mira", "text": "Ты где?"}, {"who": "bip", "text": "Бип-бип?..", "emotion": "curious"}]},
        {"duration": 4, "location": "kitchen", "needs_motion": True, "characters": ["bip", "tihonya"],
         "camera": {"move": "orbit"}, "action": "Бип кружит",
         "dialogue": [{"who": "bip", "text": "Бип!", "emotion": "радостно"}, {"who": "tihonya", "text": "Привет"}]}]})
    G.request_stage(store, pid, "refs")
    G.request_stage(store, pid, "keyframes", sid)
    vid = G.request_stage(store, pid, "video", sid)
    assert [d["label"] for d in vid["run"]["done"]] == ["план 1: видео 5 с", "план 2: видео 5 с"]
    # Промпт видео: говорит голосом только Мира; звуки Бипа и молчание Тихони — не «рот». Физика Бипа —
    # дословно; облёт камеры заменён спокойной камерой.
    v2 = G.get_job(store, vid["run"]["done"][1]["job_id"])
    assert "talks" not in v2["prompt"] and "плоский диск на полу" in v2["prompt"]
    assert "exactly one Бип" in v2["prompt"] and "orbit" not in v2["prompt"]
    from app.aivideo_assemble import SFX_MIN_SEC, assemble

    res = assemble(store, sid)
    assert res["video_shots"] == 2 and res["lines"] == 3
    layers = store.get_clip_plan(res["clip_id"])["render_settings"]["layers"]
    lines = [l for l in layers if l["id"].startswith("aiv_line_")]
    assert [l["id"] for l in lines] == ["aiv_line_1_1", "aiv_line_1_2", "aiv_line_2_1"]
    sfx_assets = {store.get_montage_asset(l["props"]["asset_id"])["original_filename"] for l in lines[1:]}
    assert all(name.startswith("freesound/") for name in sfx_assets)  # звуки пака, не TTS
    assert lines[2]["end"] - lines[2]["start"] >= SFX_MIN_SEC  # окно подписи к звуку
    subs = [l["text"] for l in store.get_clip_plan_subtitles(res["clip_id"])["lines"]]
    assert subs == ["Ты где?", "Бип-бип?..", "Бип!"]
    V.save_character(store, pid, speech_captions=False, character_id=V.character_by_slug(store, pid, "bip")["id"])
    res = assemble(store, sid)
    assert [l["text"] for l in store.get_clip_plan_subtitles(res["clip_id"])["lines"]] == ["Ты где?"]


class RawTimeoutVideo(FakeVideo):
    """Адаптер с поиском, POST которого рвётся на чтении (httpx.ReadTimeout — как есть, 1.1)."""

    def submit(self, params, refs):
        import httpx

        self.posts += 1
        self.history.append({"id": f"vid-{self.posts}", "model": params["model"], "createdAt": G._utc_now()})
        raise httpx.ReadTimeout("The read operation timed out")


def test_video_read_timeout_recovers_via_history_and_recoverless_is_uncertain(paid_video, monkeypatch):
    (_client_, store), img, _vid = paid_video
    raw = RawTimeoutVideo()
    monkeypatch.setattr(PR, "get_video_provider", lambda name: raw)
    pid, sid = _project(store, provider="polza")
    _paid_frames(store, sid, pid)
    req = G.request_stage(store, pid, "video", sid)
    A.decide(store, req["approval_id"], "approve", actor="ui")
    G.run_pending(store)
    [job] = [j for j in G.list_jobs(store, approval_id=req["approval_id"]) if j["kind"] == "video"]
    # Обрыв после отправки у адаптера с поиском: id из истории, без второго POST и без «под вопросом».
    assert job["status"] == "done" and job["remote_id"] == "vid-1" and raw.posts == 1

    # Тот же обрыв у адаптера без поиска — 1.1: «под вопросом» со сверкой баланса, сам не повторяет.
    class NoRecover(RawTimeoutVideo):
        recover = None

    plain = NoRecover()
    monkeypatch.setattr(PR, "get_video_provider", lambda name: plain)
    shot1 = V.get_script(store, sid)["shots"][0]
    again = G.regenerate(store, target=f"shot:{shot1['id']}:shot_video")
    A.decide(store, again["approval_id"], "approve", actor="ui")
    G.run_pending(store)
    [job] = [j for j in G.list_jobs(store, approval_id=again["approval_id"]) if j["kind"] == "video"]
    assert job["status"] == "uncertain" and plain.posts == 1


# --------------------------------------------------------------------------- #
# v3: режимы видеомоделей — кадры и листы персонажей НИКОГДА вместе у Seedance/H3
# --------------------------------------------------------------------------- #


def test_polza_video_never_sends_frames_together_with_reference_images(monkeypatch, tmp_path):
    """Серия 3: seedance получил и images, и reference_images — у Seedance 2.x/H3 это разные режимы
    (KIE отклоняет 400 или молча роняет одну группу). Адаптер — последняя страховка до денег."""
    import httpx

    from app.aivideo_providers import PolzaVideo, video_params

    start, end, sheet = tmp_path / "start.jpg", tmp_path / "end.jpg", tmp_path / "sheet.jpg"
    for p in (start, end, sheet):
        p.write_bytes(b"\xff\xd8\xff\xd9")
    seen = {}

    class _Resp:
        status_code = 200
        content = b"{}"

        def json(self):
            return {"id": "gen_v", "status": "pending"}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen["body"] = json
        return _Resp()

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr("app.aivideo_providers.start_frame_data_url", lambda p: f"data:image/jpeg;base64,{p.stem}")
    adapter = PolzaVideo(api_key="k", base_url="https://polza.test/api/v1")
    for model in ("bytedance/seedance-2", "bytedance/seedance-2-fast", "bytedance/seedance-2-5"):
        params = video_params(model, "кадр", 4, "720p")
        adapter.submit({**params, "_reference_images": [str(sheet)]}, [start, end])
        fields = seen["body"]["input"]
        assert [i["data"] for i in fields["images"]] == ["data:image/jpeg;base64,start", "data:image/jpeg;base64,end"]
        assert "reference_images" not in fields and "_reference_images" not in fields, model
    # Grok берёт только старт: лишний кадр не уходит.
    adapter.submit(video_params("grok-imagine-video-1-5", "кадр", 4, "720p"), [start, end, sheet])
    assert len(seen["body"]["input"]["images"]) <= 7


def test_model_capability_routing_and_future_kling_slot(env):
    from app.aivideo_providers import (choose_video_mode, genapi_video_body, snap_seconds, video_capabilities,
                                       video_resolution_for)

    caps = {m: video_capabilities(m) for m in ("bytedance/seedance-2", "bytedance/seedance-2-5",
                                                "minimax/hailuo-3-max", "bytedance/seedance-2-fast",
                                                "grok-imagine-video-1-5", "kling/v4-reference")}
    assert all(c["first_last"] for m, c in caps.items()
               if m not in ("grok-imagine-video-1-5", "minimax/hailuo-3-max"))
    assert not caps["minimax/hailuo-3-max"]["first_last"] and caps["minimax/hailuo-3-max"]["images"] == 1
    assert all(c["exclusive"] for m, c in caps.items() if m != "kling/v4-reference")
    assert caps["kling/v4-reference"]["ref_images"] == 7 and not caps["kling/v4-reference"]["exclusive"]
    assert caps["minimax/hailuo-3-max"]["ref_images"] == 0 and caps["minimax/hailuo-3-max"]["min"] == 5
    assert video_resolution_for("minimax/hailuo-3-max", "720p") == "768p"
    assert video_resolution_for("bytedance/seedance-2-fast", "1080p") == "720p"
    assert video_resolution_for("bytedance/seedance-2", "1080p") == "1080p"
    assert snap_seconds("minimax/hailuo-3-max", 4) == 5 and snap_seconds("bytedance/seedance-2", 3) == 4
    # Есть оба кадра — первый+последний; Seedance/H3 никогда не смешивают кадры с листами.
    assert choose_video_mode("bytedance/seedance-2", True, True) == "first_last"
    assert choose_video_mode("bytedance/seedance-2", False, True) == "start"
    assert choose_video_mode("grok-imagine-video-1-5", True, True) == "start"
    assert choose_video_mode("kling/v4-reference", False, True) == "refs"
    assert PR.default_video_model("quality") == "bytedance/seedance-2"
    assert PR.default_video_model("draft") == "minimax/hailuo-3-max"
    # Слот Kling 4.0 (GenAPI/kie): старт, конец и элементы одним телом, один план; сети нет.
    body = genapi_video_body({"model": "kling/v4-reference", "prompt": "p", "_kind": "x"}, Path("s.jpg"),
                             Path("e.jpg"), [Path(f"c{i}.jpg") for i in range(9)])
    assert body["start_image_url"] == "s.jpg" and body["end_image_url"] == "e.jpg" and body["multi_shots"] is False
    assert len(body["elements"]) == 7 and "_kind" not in body
    with pytest.raises(PR.ProviderError, match="не подключён"):
        PR.FUTURE_CATALOGS["genapi"].video_adapter().submit({}, [])
    assert "genapi" not in V.PROVIDERS
    # Модель без первого+последнего кадра (grok): видео без конечного кадра не блокируется.
    _client_, store = env
    pid, sid = _project(store)
    V.update_style(store, pid, {"video_model": "grok-imagine-video-1-5"})
    _motion(store, sid, 1)
    G.request_stage(store, pid, "refs")
    G.request_stage(store, pid, "keyframes", sid, items=[f"shot:{V.get_script(store, sid)['shots'][0]['id']}:keyframe"])
    est = G.estimate(store, pid, "video", sid)
    assert est["count"] == 1 and not est["blocked"] and est["modes"] == ["start"]
    V.update_style(store, pid, {"video_model": "bytedance/seedance-2"})
    assert any("нет конечного кадра" in b for b in G.estimate(store, pid, "video", sid)["blocked"])


# --------------------------------------------------------------------------- #
# v3: проверка готового видео Gemini-зрением — флаг на раскадровке и в карточке, перегенерация
# --------------------------------------------------------------------------- #


class FakeVideoVision:
    """Gemini-зрение: проверка видео (по промпту VIDEO_QA_PROMPT) или постоянство кадров."""

    def __init__(self, issues=None):
        self.issues = issues or []
        self.video_calls = 0
        self.images_seen = 0

    def generate_content(self, model, payload):
        import json

        parts = payload["contents"][0]["parts"]
        if parts[0].get("text") == G.VIDEO_QA_PROMPT:
            self.video_calls += 1
            self.images_seen = sum(1 for p in parts if "inline_data" in p)
            body = {"score": 3 if self.issues else 9, "ok": not self.issues, "issues": self.issues}
        else:
            body = {"characters": [{"id": "mira", "score": 9, "differences": ""}]}
        return {"candidates": [{"content": {"parts": [{"text": json.dumps(body, ensure_ascii=False)}]}}]}


def test_video_qa_flags_storyboard_and_card_and_offers_regeneration(paid_video, monkeypatch):
    (_client_, store), img, vid = paid_video
    vid.uncertain = False
    vision = FakeVideoVision([{"what": "count", "detail": "печенье раздвоилось", "at": 2.5},
                              {"what": "floating", "detail": "пылесос висит над полом"}])
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(G, "_vision_client", lambda: vision)
    pid, sid = _project(store, provider="polza")
    V.update_style(store, pid, {"video_model": "bytedance/seedance-2-fast"})
    _paid_frames(store, sid, pid)
    req = G.request_stage(store, pid, "video", sid)
    A.decide(store, req["approval_id"], "approve", actor="ui")
    G.run_pending(store)
    assert vision.video_calls == 1 and 4 <= vision.images_seen - 2 <= 6  # 4–6 кадров видео + листы персонажей
    view = V.script_view(store, sid)
    shot = view["shots"][0]
    assert shot["video_qa"]["ok"] is False and shot["video_qa"]["issues"][0]["what"] == "count"
    assert [r["idx"] for r in view["video_review"]] == [1] and "печенье раздвоилось" in view["video_review"][0]["issues"][0]["detail"]
    assert G.video_qa_flags(store, sid)[0]["ok"] is False
    posts = vid.posts
    # Одна кнопка «перегенерировать» — своя маленькая карточка с флагами; без «Разрешить» не платит.
    again = G.regenerate(store, target=f"shot:{shot['id']}:shot_video")
    assert again["status"] == "awaiting_approval"
    card = A.get_approval(store, again["approval_id"])
    assert "Проверка видео" in card["question"] and "печенье раздвоилось" in card["question"]
    G.run_pending(store)
    assert vid.posts == posts
    # Ручная перепроверка (чистое видео) снимает флаг.
    ref = V.selected_ref(store, pid, "shot", shot["id"], "shot_video")
    res = G.check_video(store, ref["id"], client=FakeVideoVision())
    assert res["needs_review"] is False and not V.get_ref(store, ref["id"])["needs_review"]
    from app.agent_tools import ToolContext, run_tool

    out, ok = run_tool(ToolContext(store=store), "aiv_check_video", {"ref_id": ref["id"]})
    assert ok and out["skipped"] is not True if "skipped" in out else ok


def test_check_video_is_skipped_without_gemini_and_never_in_dry_run(env):
    _client_, store = env
    pid, sid = _project(store)
    _motion(store, sid, 1)
    G.request_stage(store, pid, "refs")
    G.request_stage(store, pid, "keyframes", sid)
    G.request_stage(store, pid, "video", sid)
    ref = V.selected_ref(store, pid, "shot", V.get_script(store, sid)["shots"][0]["id"], "shot_video")
    assert ref["score"] is None  # сухой прогон сам не проверяет
    assert G.check_video(store, ref["id"])["skipped"]
    frames = G.sample_video_frames(G._asset_path(store, ref), settings.tmp_dir / "qa-test", 5)
    assert len(frames) == 5 and frames[0][0] < 0.2 and frames[-1][0] > 4.0


# --------------------------------------------------------------------------- #
# v3: блокинг в нашем движке сцен, облики — img2img от листа
# --------------------------------------------------------------------------- #


def _need_compositor():
    from app.motion_render import CompositorMissing, compositor_command

    try:
        compositor_command()
    except CompositorMissing:
        pytest.skip("компоновщик не собран (npm run build:motion)")


def test_blocking_auto_spec_compiles_stand_ins(env):
    from app import aivideo_blocking as B

    _client_, store = env
    pid, sid, out = _state_project(store)
    V.save_character(store, pid, "Бип", "робот-пылесос, круглый", slug="bip", speech="sfx")
    assert B.stand_in(V.character_by_slug(store, pid, "bip")) == "disc"
    assert B.stand_in(V.character_by_slug(store, pid, "kot")) == "quadruped"
    assert B.stand_in(V.character_by_slug(store, pid, "mira")) == "human"
    shots = V.list_shots(store, sid)
    spec = B.auto_spec(store, pid, shots[0], shots[1])
    # Старт: Мира сидит лицом в камеру; конец плана 1 = старт плана 2 (стоит, носок в руке).
    assert spec["start"]["actors"][0]["pose"] == "sitting" and spec["start"]["actors"][0]["facing"] == 0
    assert spec["end"]["actors"][0]["pose"] == "standing"
    assert spec["end"]["props"][0]["holder"] == "mira"
    infos = B.cast_info(store, pid, shots[0])
    scene = B.compile_scene(spec, "end", shots[0], infos)
    assert scene["camera"]["type"] == "perspective" and len(scene["nodes"]) >= 6
    assert B.legend(store, pid, shots[0]) == "the slate-blue mannequin = Мира"
    clean = B.clean_spec({"start": {"actors": [{"id": "stranger", "pos": [0, 0]}, {"id": "mira", "pos": [1e9, 0],
                                                                                   "pose": "сидит", "facing": "away"}]}},
                         ["mira"])
    assert [a["id"] for a in clean["start"]["actors"]] == ["mira"]
    assert clean["start"]["actors"][0]["pos"][0] == 2000 and clean["start"]["actors"][0]["facing"] == 180


def test_blocking_stage_renders_free_and_feeds_keyframes(env):
    _need_compositor()
    import json

    from app import aivideo_blocking as B

    client, store = env
    pid, sid, out = _state_project(store)
    _first_last(store, pid)  # пары кадров: модель с первым+последним кадром
    shots = out["shots"]
    G.request_stage(store, pid, "refs")
    est = G.estimate(store, pid, "blocking", sid)
    # Старт у каждого плана, конец — у планов с видео (1, 2); всё 0 ₽.
    assert [i["label"] for i in est["items"]] == ["план 1: блокинг старт", "план 1: блокинг конец",
                                                  "план 2: блокинг старт", "план 2: блокинг конец",
                                                  "план 3: блокинг старт"]
    assert est["total_rub"] == 0 and not est["paid"]
    assert any("нет блокинга" in n for n in G.estimate(store, pid, "keyframes", sid)["notes"])

    class FakeBlocker:
        calls = 0

        def generate_content(self, model, payload):
            FakeBlocker.calls += 1
            body = {"locations": [{"id": "kitchen", "room": {"width": 420, "depth": 360, "height": 250},
                                   "objects": [{"id": "fridge", "kind": "box", "size": [70, 180, 65],
                                                "pos": [150, -140]}]}],
                    "shots": [{"idx": s["idx"],
                               "start": {"actors": [{"id": c["id"], "pos": [-50, 10], "facing": 0,
                                                     "pose": "sitting"} for c in s["cast"]]},
                               "end": {"actors": [{"id": c["id"], "pos": [-50, 10], "facing": 90,
                                                   "pose": "standing"} for c in s["cast"]],
                                       "props": [{"id": "sock", "kind": "box", "size": 12, "holder": "mira"}]}}
                              for s in V.list_shots(store, sid)]}
            return {"candidates": [{"content": {"parts": [{"text": json.dumps(body)}]}}]}

    written = B.write_blocking(store, sid, client=FakeBlocker())
    assert written["source"] == "gemini" and FakeBlocker.calls == 1 and len(written["shots"]) == 3
    res = G.request_stage(store, pid, "blocking", sid)
    assert res["status"] == "done" and len(res["run"]["done"]) == 5
    assert FakeBlocker.calls == 1  # раскладка уже есть — повторно не пишется
    b1 = V.selected_ref(store, pid, "shot", shots[0]["id"], "blocking_start")
    assert b1["prompt"].startswith("blocking:") and store.get_montage_asset(b1["asset_id"])["width"] == 540
    assert G.estimate(store, pid, "blocking", sid)["count"] == 0  # всё отрисовано
    view = B.view(store, V.get_shot(store, shots[0]["id"]))
    # Комната — раскладка локации (одна на все планы), не то, что Gemini придумал в ответе по планам.
    assert view["source"] == "gemini" and view["spec"]["objects"] == []
    assert "fridge" in [o["id"] for o in view["layout"]["objects"]]
    assert view["checks"]["start"]["ok"], view["checks"]
    # Кадры: блокинг — ПЕРВЫЙ референс (композиция), легенда манекенов — в промпте.
    kf = G.request_stage(store, pid, "keyframes", sid)
    jobs = [G.get_job(store, d["job_id"]) for d in kf["run"]["done"]]
    s1 = next(j for j in jobs if j["ref_kind"] == "keyframe" and j["target_id"] == shots[0]["id"])
    e1 = next(j for j in jobs if j["ref_kind"] == "endframe" and j["target_id"] == shots[0]["id"])
    assert s1["params"]["ref_roles"][0] == "блокинг: старт" and e1["params"]["ref_roles"][:2] == [
        "блокинг: конец", "план 1: старт"]
    assert "3D BLOCKING mock-up" in s1["prompt"] and "the slate-blue mannequin = Мира" in s1["prompt"]
    assert "ONLY for the camera angle" in s1["prompt"] and "Do NOT copy its look" in s1["prompt"]
    assert s1["params"]["blocking"] == {"used": True}
    # Своя раскладка агента: блокинг устарел → перерисовка (0 ₽), Gemini не трогает её при правке state.
    from app.agent_tools import ToolContext, run_tool

    spec = dict(view["spec"])
    spec["start"] = {"actors": [{"id": "mira", "pos": [80, 0], "facing": -90, "pose": "kneeling"}]}
    out_tool, ok = run_tool(ToolContext(store=store), "aiv_block_shot", {"shot_id": shots[0]["id"], "spec": spec})
    assert ok and out_tool["source"] == "agent" and len(out_tool["rendered"]) == 2
    b1_new = V.selected_ref(store, pid, "shot", shots[0]["id"], "blocking_start")
    assert b1_new["id"] != b1["id"] and b1_new["prompt"] != b1["prompt"]
    V.update_shot(store, shots[0]["id"], {"action": "Мира встаёт"})
    assert B.view(store, V.get_shot(store, shots[0]["id"]))["source"] == "agent"
    # REST: раскладка, сцены движка, правка владельцем.
    resp = client.get(f"/api/ai-video/shots/{shots[2]['id']}/blocking?scenes=true")
    assert resp.status_code == 200 and resp.json()["scenes"]["start"]["nodes"]
    resp = client.patch(f"/api/ai-video/shots/{shots[2]['id']}/blocking",
                        json={"spec": {"start": {"actors": [{"id": "kot", "pos": [0, 40], "facing": 0,
                                                             "pose": "lying"}]}}})
    assert resp.status_code == 200 and resp.json()["source"] == "owner" and resp.json()["frames"]["start"]["url"]


def test_look_is_img2img_variant_of_base_sheet(env):
    _client_, store = env
    pid, sid = _project(store)
    mira = V.character_by_slug(store, pid, "mira")
    # Без разворота облик не рисуется (он — правка листа), с разворотом в той же партии — можно.
    est = G.estimate(store, pid, "refs", items=[f"look:{mira['looks'][0]['id']}"])
    assert est["blocked"] and "сначала разворот" in est["blocked"][0]
    out = G.request_stage(store, pid, "refs")
    job = next(G.get_job(store, d["job_id"]) for d in out["run"]["done"] if "облик" in d["label"])
    assert job["params"]["ref_roles"][0] == "база: разворот"
    assert "img2img edit of the FIRST reference image" in job["prompt"] and "голубая пижама" in job["prompt"]
    assert job["params"]["aspect"] == "16:9"
    # Сценарист заводит облик серии — new_looks.
    from app.aivideo_script import apply_script

    res = apply_script(store, sid, {"title": "t", "logline": "l", "shots": SHOTS,
                                    "new_looks": [{"character": "mira", "id": "wet", "name": "Промокла",
                                                   "outfit": "мокрые волосы, капли на пижаме"}]})
    assert res["created"]["looks"] == ["mira:wet"]
    assert G.estimate(store, pid, "refs")["items"][0]["target_type"] == "look"


def test_look_and_expressions_wait_for_turnaround_in_paid_batch(paid, monkeypatch):
    (_client_, store), fake = paid
    monkeypatch.setattr(settings, "aivideo_enabled", True)
    pid, _sid = _project(store, provider="polza")
    req = G.request_stage(store, pid, "refs")
    A.decide(store, req["approval_id"], "approve", actor="ui")
    fake.pending_polls = 10**6
    first = G.run_pending(store, poll_timeout=0)
    # Стиль ×3, локация и разворот Миры ушли; эмоции Миры ждут разворот — и остальная партия тоже.
    assert first["waiting"] and fake.posts == 5
    fake.pending_polls = 0
    G.run_pending(store, poll_timeout=0)
    G.run_pending(store, poll_timeout=0)
    assert fake.posts == 9 and {j["status"] for j in G.list_jobs(store, approval_id=req["approval_id"])} == {"done"}


def test_polza_video_frames_go_as_storage_urls(monkeypatch, tmp_path):
    """Hailuo 3 Max отвергает кадры base64 — кадры видео уходят ссылкой из хранилища Polza."""
    import httpx

    from app.aivideo_providers import PolzaVideo

    frame = tmp_path / "start.jpg"
    frame.write_bytes(b"\xff\xd8\xff\xd9")
    calls = []

    class _Resp:
        def __init__(self, code, data):
            self.status_code, self._data, self.content = code, data, b"{}"

        def json(self):
            return self._data

    def fake_post(url, headers=None, json=None, files=None, data=None, timeout=None):
        calls.append((url, json))
        if url.endswith("/storage/upload"):
            return _Resp(200, {"id": "file_1", "url": "https://s3.polza.test/start.jpg"})
        return _Resp(200, {"id": "gen_v", "status": "pending"})

    monkeypatch.setattr(httpx, "post", fake_post)
    PolzaVideo(api_key="k", base_url="https://polza.test/api/v1").submit(
        {"model": "minimax/hailuo-3-max", "prompt": "x", "duration": "5"}, [frame])
    media = [body for url, body in calls if url.endswith("/media")][0]
    assert media["input"]["images"] == [{"type": "url", "data": "https://s3.polza.test/start.jpg"}]


# --------------------------------------------------------------------------- #
# 02.11: упавшие задачи не едят бюджет; повтор запроса/одобрения не платит дважды; локация не «плывёт»
# --------------------------------------------------------------------------- #


class FailingPaid(FakePaid):
    """Провайдер принял запрос, а задача упала: «все провайдеры вернули ошибку» (без суммы) или
    отказ со своей суммой списания (``charged``)."""

    def __init__(self, charged=None):
        super().__init__()
        self.charged = charged

    def check(self, remote_id):
        self.checks += 1
        out = {"status": "failed", "error": "Polza: all providers failed"}
        if self.charged is not None:
            out["cost_rub"] = self.charged
        return out


def test_failed_provider_jobs_release_reserve_and_are_not_spent(paid, monkeypatch):
    (_client_, store), fake = paid
    monkeypatch.setattr(settings, "aivideo_enabled", True)
    pid, sid = _project(store, provider="polza")
    req = G.request_stage(store, pid, "refs")
    A.decide(store, req["approval_id"], "approve", actor="ui")
    G.run_pending(store)
    # Серия-черновик: потолок 350 ₽, уже потрачено 330 — кадры (3 × 6 = 18 ₽) влезают впритык.
    store.db.execute("INSERT INTO aiv_ledger (project_id, script_id, stage, amount_rub, source) "
                     "VALUES (?, ?, 'keyframes', 330, 'provider')", (pid, sid))
    failing = FailingPaid()
    monkeypatch.setattr(PR, "get_image_provider", lambda name: failing)
    kf = G.request_stage(store, pid, "keyframes", sid)
    assert G.budget_view(store, pid, sid)["episode"]["reserved_rub"] == pytest.approx(18.0)
    A.decide(store, kf["approval_id"], "approve", actor="ui")
    out = G.run_pending(store)
    assert failing.posts == 3 and len(out["failed"]) == 3
    jobs = G.list_jobs(store, approval_id=kf["approval_id"])
    assert {j["status"] for j in jobs} == {"failed"}
    assert all(j["cost_rub"] == 0 and "не списано" in j["error"] for j in jobs)
    # Ни трат, ни резерва: серия не упирается в потолок, тот же этап просится снова без смены режима.
    bud = G.budget_view(store, pid, sid)
    assert bud["episode"]["spent_rub"] == pytest.approx(330.0) and bud["episode"]["reserved_rub"] == 0
    assert bud["month"]["reserved_rub"] == 0 and bud["all_projects_month"]["reserved_rub"] == 0
    assert not [l for l in G.ledger(store, pid) if l["stage"] == "keyframes" and l["job_id"]]
    again = G.request_stage(store, pid, "keyframes", sid)
    assert again["status"] == "awaiting_approval" and again["estimate"]["total_rub"] == pytest.approx(18.0)
    # Повторный запрос того же этапа не упирается в потолок резервом своей же ждущей карточки.
    third = G.request_stage(store, pid, "keyframes", sid)
    assert third["approval_id"] == again["approval_id"] and third.get("duplicate")
    # Провайдер САМ назвал списание за отказ — оно в журнале по его ответу.
    failing.charged = 2.5
    A.decide(store, again["approval_id"], "approve", actor="ui")
    G.run_pending(store)
    led = [l for l in G.ledger(store, pid) if l["approval_id"] == again["approval_id"]]
    assert len(led) == 3 and {l["source"] for l in led} == {"provider"}
    assert sum(l["amount_rub"] for l in led) == pytest.approx(7.5)
    # Старые базы: отказ без суммы, записанный по оценке, снимается при старте (один раз).
    job_id = jobs[0]["id"]
    store.db.execute("INSERT INTO aiv_ledger (project_id, script_id, job_id, approval_id, stage, amount_rub, source, "
                     "note) VALUES (?, ?, ?, ?, 'keyframes', 6, 'estimate', 'отказ провайдера — списание по оценке "
                     "(проверь баланс)')", (pid, sid, job_id, kf["approval_id"]))
    store.db.execute("UPDATE aiv_gen_jobs SET cost_rub = 6 WHERE id = ?", (job_id,))
    assert G.spent_script(store, sid) == pytest.approx(330.0 + 7.5 + 6.0)
    for _ in range(2):
        conn = store.db.connect()
        try:
            V.ensure_aiv_schema(conn)
            conn.commit()
        finally:
            conn.close()
    assert G.spent_script(store, sid) == pytest.approx(330.0 + 7.5)
    assert G.get_job(store, job_id)["cost_rub"] == 0
    assert sum(1 for l in G.ledger(store, pid) if "снято 02.11" in l["note"]) == 1


def test_repeated_request_and_approval_do_not_pay_twice(paid, monkeypatch):
    (client, store), fake = paid
    monkeypatch.setattr(settings, "aivideo_enabled", True)
    pid, sid = _project(store, provider="polza")
    # Двойной клик «Запросить»: вторая карточка не уходит — та же ждущая.
    first = G.request_stage(store, pid, "refs")
    second = G.request_stage(store, pid, "refs")
    assert second["status"] == "awaiting_approval" and second["approval_id"] == first["approval_id"]
    assert second.get("duplicate") and len(G.stage_approvals(store, pid, status="pending")) == 1
    # Повтор одобрения (повтор запроса после ошибки клиента) — тот же ответ, без второго запуска.
    url = f"/api/ai-video/approvals/{first['approval_id']}/decide"
    resp = client.post(url, json={"action": "approve"})
    assert resp.status_code == 200 and resp.json()["aiv"]["approved_jobs"] == 9, resp.text
    resp = client.post(url, json={"action": "approve"})
    assert resp.status_code == 200 and resp.json()["already"] and resp.json()["status"] == "approved", resp.text
    assert client.post(url, json={"action": "reject"}).status_code == 400  # противоположное — как раньше
    with pytest.raises(ValueError, match="уже решена"):
        A.decide(store, first["approval_id"], "approve", actor="tg")
    # Карточка одобрена, работник ещё не взял запуски — повторный запрос этапа ничего не заказывает.
    again = G.request_stage(store, pid, "refs")
    assert again["status"] == "in_progress" and again["approval_id"] == first["approval_id"]
    assert len(G.list_jobs(store, project_id=pid)) == 9
    G.run_pending(store)
    assert fake.posts == 9 and len(G.ledger(store, pid)) == 9
    assert G.request_stage(store, pid, "refs")["status"] == "nothing"
    # Кадр одним элементом (UI шлёт items без regenerate) дважды — один запуск.
    shot1 = V.get_script(store, sid)["shots"][0]
    one = G.request_stage(store, pid, "keyframes", sid, items=[f"shot:{shot1['id']}:keyframe"])
    two = G.request_stage(store, pid, "keyframes", sid, items=[f"shot:{shot1['id']}:keyframe"])
    assert one["status"] == "awaiting_approval" and two["status"] == "in_progress"
    assert two["approval_id"] == one["approval_id"] and "уже в работе" in "; ".join(two["estimate"]["notes"])
    # Карточка-дубль на цель, которую уже делает одобренная карточка: при одобрении запуск — «дубль».
    A.decide(store, one["approval_id"], "approve", actor="ui")
    item = {**G.plan_stage(store, pid, "keyframes", sid, [f"shot:{shot1['id']}:keyframe"])[0], "regenerate": False}
    dup = A.submit_card(store, pid, "other", "дубль?", {"action": G.CARD_ACTION, "stage": "keyframes",
                                                         "script_id": sid, "amount_rub": 6.0})
    dup_job = G._insert_job(store, pid, item, "awaiting", dup["id"], "agent")
    res = A.decide(store, dup["id"], "approve", actor="ui")
    assert res["aiv"]["duplicate_jobs"] == 1 and G.get_job(store, dup_job)["status"] == "cancelled"
    G.run_pending(store)
    assert fake.posts == 10  # кадр плана 1 — один раз
    # Явная перегенерация — как раньше: своя карточка, новая версия.
    ref = V.selected_ref(store, pid, "shot", shot1["id"], "keyframe")
    regen = G.regenerate(store, ref_id=ref["id"])
    assert regen["status"] == "awaiting_approval" and regen["approval_id"] != one["approval_id"]
    A.decide(store, regen["approval_id"], "approve", actor="ui")
    G.run_pending(store)
    assert fake.posts == 11
    assert [v["version"] for v in V.list_refs(store, pid, "shot", shot1["id"], "keyframe")] == [1, 2]


def test_location_plate_and_verbatim_text_in_every_shot_of_the_scene(env, monkeypatch):
    _client_, store = env
    pid, sid = _project(store)
    _first_last(store, pid)  # пары кадров: модель с первым+последним кадром
    V.save_location(store, pid, "Спальня", "узкая спальня, синие обои, окно слева, кровать у стены",
                    slug="bedroom")
    V.update_script(store, sid, {"shots": [
        {"duration": 4, "location": "kitchen", "needs_motion": True, "characters": [{"id": "mira"}],
         "camera": {"move": "static"}, "action": "Мира открывает холодильник", "dialogue": []},
        {"duration": 3, "location": "bedroom", "characters": [{"id": "mira"}], "camera": {"move": "static"},
         "action": "Мира лежит на кровати", "dialogue": []},
        {"duration": 3, "location": "kitchen", "characters": [{"id": "kot"}], "camera": {"shot": "wide"},
         "action": "кот сидит у холодильника", "dialogue": []},
    ]})
    G.request_stage(store, pid, "refs")
    kf = G.request_stage(store, pid, "keyframes", sid)
    shots = V.get_script(store, sid)["shots"]
    jobs = {(j["target_id"], j["ref_kind"]): j for j in (G.get_job(store, d["job_id"]) for d in kf["run"]["done"])}
    kitchen = G.location_text(V.location_by_slug(store, pid, "kitchen"))
    bedroom = G.location_text(V.location_by_slug(store, pid, "bedroom"))
    assert "маленькая кухня, ночь, свет из холодильника" in kitchen
    s1, e1 = jobs[(shots[0]["id"], "keyframe")], jobs[(shots[0]["id"], "endframe")]
    s2, s3 = jobs[(shots[1]["id"], "keyframe")], jobs[(shots[2]["id"], "keyframe")]
    plate = "плита локации «Кухня ночью»"
    # Все планы кухни: то же описание дословно и та же плита референсом — сразу после кадров
    # композиции/непрерывности, в лимите модели.
    for job in (s1, e1, s3):
        assert kitchen in job["prompt"] and bedroom not in job["prompt"]
        assert plate in job["params"]["ref_roles"][:3], job["params"]["ref_roles"]
        assert "background plate of «Кухня ночью»: this exact room" in job["prompt"]
    plate_file = {job["params"]["refs"][job["params"]["ref_roles"].index(plate)] for job in (s1, e1, s3)}
    assert len(plate_file) == 1  # один и тот же файл плиты
    # Конец плана — по старту этого же плана, за ним плита.
    assert e1["params"]["ref_roles"][:2] == ["план 1: старт", plate]
    # Спальня — своя плита; кадр из кухни (другая комната) референсом непрерывности НЕ идёт.
    assert bedroom in s2["prompt"] and kitchen not in s2["prompt"]
    assert s2["params"]["ref_roles"][0] == "плита локации «Спальня»"
    assert not any(r.startswith("план 1") for r in s2["params"]["ref_roles"])
    # Снова кухня после спальни: референс непрерывности — последний кадр той же кухни (конец плана 1).
    assert s3["params"]["ref_roles"][:2] == ["план 1: конечный кадр (та же локация)", plate]
    assert "earlier shot 1 in this same location" in s3["prompt"]
    assert not any("Спальня" in r for r in s3["params"]["ref_roles"])
    # Зависимость в платной партии — тоже от кадра той же локации, не от спальни.
    assert G._frame_deps(store, s3) == [(shots[0]["id"], "shot:endframe"), (shots[0]["id"], "shot:keyframe")]
    # Видео: то же описание локации дословно; модели, что берут референсы вместе с кадрами, — плита первой.
    monkeypatch.setitem(PR.VIDEO_LIMITS, "grok-imagine-video-1-5",
                        {**PR.VIDEO_LIMITS["grok-imagine-video-1-5"], "ref_images": 3, "exclusive": False})
    V.update_style(store, pid, {"video_model": "grok-imagine-video-1-5"})
    vid = G.request_stage(store, pid, "video", sid)
    [done] = vid["run"]["done"]
    vjob = G.get_job(store, done["job_id"])
    assert kitchen in vjob["prompt"] and "room never changes" in vjob["prompt"]
    assert vjob["params"]["mode"] == "refs"
    plate_name = Path(next(iter(plate_file))).name
    assert vjob["params"]["reference_sheets"][0] == plate_name and len(vjob["params"]["reference_sheets"]) <= 3
    assert "first reference image is the background plate" in vjob["prompt"]


# --------------------------------------------------------------------------- #
# Серия 3 «Утренний кофе»: подробный блокинг, пустая плита, старт продолжения из хвоста видео
# --------------------------------------------------------------------------- #

EP3_ROOM = ("уютная маленькая спальня, вечер, тёплый свет настольной лампы и гирлянды на стене, кровать с лавандовым "
            "пледом, пушистый ковёр, на полу разбросаны носки, у кровати станция зарядки робота-пылесоса, окно с синим "
            "вечерним небом")
EP3_MUG = "кружка с кофе"
EP3_SHOTS = [
    {"duration": 5, "location": "bedroom", "needs_motion": True,
     "characters": [{"id": "mira", "action": "ставит кружку и отворачивается"}, {"id": "beep"}],
     "camera": {"shot": "medium", "angle": "eye_level", "lens": 35, "move": "static"},
     "action": "Сонная Мира ставит кружку кофе на прикроватную тумбочку и поворачивается к окну потянуться",
     "state": {"characters": [{"id": "mira", "pose": "standing", "facing": "left", "position": "center foreground",
                               "holding": EP3_MUG},
                              {"id": "beep", "pose": "lying", "facing": "left", "position": "left background",
                               "holding": "ничего"}],
               "props": [{"name": EP3_MUG, "state": "в руках Миры", "count": 1}]},
     "end_state": {"characters": [{"id": "mira", "pose": "standing", "facing": "away", "position": "center foreground",
                                   "holding": "ничего"},
                                  {"id": "beep", "pose": "lying", "facing": "left", "position": "left background",
                                   "holding": "ничего"}],
                   "props": [{"name": EP3_MUG, "state": "стоит на прикроватной тумбочке", "count": 1}]},
     "dialogue": []},
    {"duration": 5, "location": "bedroom", "needs_motion": True, "continues_from_prev": True,
     "characters": [{"id": "mira"}, {"id": "beep", "action": "подкатывается к тумбочке"}],
     "camera": {"shot": "medium_close", "angle": "low", "lens": 50, "move": "push_in"},
     "action": "Бип подкатывается к тумбочке и слегка в неё врезается", "dialogue": []},
]


def _ep3(store, provider="none"):
    pid = V.create_ai_project(store, "Мира и Бип", style_kind="anime", provider=provider)["project"]["id"]
    V.save_character(store, pid, "Мира", "девушка 20 лет, короткое каре цвета мяты, круглые очки; рост средний",
                     slug="mira")
    V.save_character(store, pid, "Бип", "круглый робот-пылесос размером с тарелку; белый корпус, глаза-экраны",
                     slug="beep", speech="sfx")
    V.save_location(store, pid, "Спальня Миры вечером", EP3_ROOM, slug="bedroom")
    script = V.create_script(store, pid, brief="кофе", duration_target=10)
    from app.aivideo_script import apply_script

    apply_script(store, script["id"], {"title": "Утренний кофе", "logline": "кофе", "shots": EP3_SHOTS})
    return pid, script["id"]


def test_blocking_room_has_every_described_thing_once_per_location(env):
    """Комната блокинга — все вещи из описания локации (и мебель, которую называют планы), одна
    раскладка на локацию: все планы снимают одну и ту же комнату; кэш в локации."""
    from app import aivideo_blocking as B
    from app import aivideo_layout as L

    _client_, store = env
    pid, sid = _ep3(store)
    shots = V.list_shots(store, sid)
    layout = B.shot_layout(store, pid, shots[0])
    types = {o["type"] for o in layout["objects"]}
    assert {"bed", "lamp", "garland", "rug", "socks", "dock", "window"} <= types
    assert "nightstand" in types  # «ставит кружку на прикроватную тумбочку» + настольной лампе нужна опора
    by = {o["type"]: o for o in layout["objects"]}
    assert by["lamp"]["on"] == by["nightstand"]["id"] and by["lamp"]["y"] == pytest.approx(50)
    assert by["bed"]["accent"] == "#b8a9f0"  # лавандовый плед
    assert by["window"]["accent"] == "#2c3e7a"  # вечернее небо за окном
    assert by["bed"]["size"][1] < 60 and by["nightstand"]["size"][1] < 70  # узнаваемые пропорции, не столбы
    # Мебель не налезает друг на друга.
    solid = [o for o in layout["objects"] if L._on_floor(o)]
    for i, a in enumerate(solid):
        for b in solid[i + 1:]:
            assert not L._overlap(L._footprint(a), L._footprint(b), pad=0), (a["id"], b["id"])
    # Одна на локацию: оба плана — та же комната; кэш в локации, повторно не пересчитывается.
    assert B.shot_layout(store, pid, shots[1])["objects"] == layout["objects"]
    loc = V.location_by_slug(store, pid, "bedroom")
    assert L.cached(loc)["key"] == layout["key"]
    nodes0 = [n for n in B.scenes_of(store, shots[0])["start"]["nodes"] if n.get("type") == "group"]
    nodes1 = [n for n in B.scenes_of(store, shots[1])["start"]["nodes"] if n.get("type") == "group"]
    furniture = len(layout["objects"])
    assert nodes0[:furniture] == nodes1[:furniture]
    # Реквизит: кружка у Миры в руке на старте, на тумбочке — в конце.
    spec = B.current(store, shots[0])["spec"]
    assert spec["start"]["props"][0]["holder"] == "mira"
    assert spec["start"]["actors"][0]["arms"] == "holding"
    assert spec["end"]["props"][0]["on"] == by["nightstand"]["id"]
    assert spec["end"]["actors"][0]["arms"] == "up"  # «повернуться к окну потянуться»


def _assert_grounded(layout):
    """Ничего не висит: плоское — на полу (толщина ≤ 2 см), каждая вещь на полу или на поверхности вещи
    под ней (центр — над её площадкой), настенное — у стены."""
    from app import aivideo_layout as L

    room = layout["room"]
    w, d = room["width"], room["depth"]
    for o in layout["objects"]:
        y = float(o.get("y") or 0)
        if o["type"] in L.FLAT_TYPES:
            assert y == 0 and o["size"][1] <= 2, o
            continue
        if o["type"] in L.WALL_TYPES:
            x, z = o["pos"]
            assert min(abs(x + w / 2), abs(x - w / 2), abs(z + d / 2)) <= o["size"][2] / 2 + 3, o
            continue
        if y == 0:
            continue
        under = [s for s in layout["objects"] if s is not o and abs(L.top_of(s) - y) < 0.5]
        x, z = o["pos"]
        assert any(L._footprint(s)[0] <= x <= L._footprint(s)[2] and L._footprint(s)[1] <= z <= L._footprint(s)[3]
                   for s in under), f"{o['id']} висит на высоте {y}"


def test_blocking_room_is_grounded_with_real_proportions(env):
    """«Стол летает или ковёр высоко?» — ковёр и носки на полу и рисуются под ногами персонажей (сразу
    после пола), вещи стоят на полу или на поверхности, пропорции реальные."""
    from app import aivideo_blocking as B
    from app import aivideo_layout as L

    _client_, store = env
    pid, sid = _ep3(store)
    shot = V.list_shots(store, sid)[0]
    layout = B.shot_layout(store, pid, shot)
    _assert_grounded(layout)
    by = {o["type"]: o for o in layout["objects"]}
    assert by["bed"]["size"] == [140, 45, 200] and L.top_of(by["bed"]) <= 50  # кровать 1.4 × 2 м, высота 45
    assert by["nightstand"]["size"][1] == 50 and by["lamp"]["y"] == 50
    for desc in ("кухня: обеденный стол, два стула, холодильник, плита, на столе ноутбук, ковёр",
                 "гостиная: диван, журнальный столик, телевизор, торшер, полки с книгами, растение в горшке"):
        lay = L.auto_layout(desc)
        _assert_grounded(lay)
        for o in lay["objects"]:
            if o["type"] in ("desk", "table") and "журнальн" not in desc:
                assert o["size"][1] == 75
    # Плоское — слой сразу после пола: ноги персонажа рисуются поверх ковра, а не ковёр поверх ног.
    rug = next(n for n in B.scenes_of(store, shot)["start"]["nodes"] if n.get("order") == -1)
    assert rug["position"][1] == 0
    floor = B.scenes_of(store, shot)["start"]["nodes"][0]
    assert floor["order"] == -2 and all(c.get("order") is None for c in rug["children"])
    vis = L.from_vision([{"type": "rug", "x": 0.5, "depth": 0.3, "wall": "on", "on": "bed"},
                         {"type": "lamp", "x": 0.6, "depth": 0.9, "wall": "floor"},
                         {"type": "bed", "x": 0.3, "depth": 0.7, "wall": "back"}], EP3_ROOM)
    _assert_grounded(vis)  # зрение ошиблось («ковёр на кровати») — всё равно ничего не висит


def test_blocking_head_is_never_cut_and_framing_follows_shot_size(env):
    """Кадр блокинга показывает место и действие: все участники плана (и Бип на полу), вещь действия
    (тумбочка, на которую ставят кружку) и кусок комнаты — крупность плана только ориентир; голова
    главного целиком, запас сверху ≥ 5 % высоты кадра; самоконтроль ловит срез головы."""
    from app import aivideo_blocking as B

    _client_, store = env
    pid, sid = _ep3(store)
    shot = V.list_shots(store, sid)[0]
    infos = B.cast_info(store, pid, shot)
    layout = B.shot_layout(store, pid, shot)
    spec = B.clean_spec(B.current(store, shot)["spec"], ["mira", "beep"])
    for size in ("medium", "medium_close", "close"):
        for which in ("start", "end"):
            built = B.build(spec, which, {**shot, "camera": {"shot": size, "angle": "eye_level", "lens": 35}},
                            infos, layout)
            check = built["check"]
            assert check["ok"], (size, which, check["problems"])
            mira = next(b for b in built["boxes"] if b.get("main"))
            assert B.head_room(built["camera"], mira) >= B.HEAD_ROOM - 0.005, (size, which)
            assert check["shares"]["beep"] > 0  # Бип в cast — в кадре
            assert check["characters_share"] <= B.CROWD_MAX  # не манекен на весь кадр — видна комната
            focus = [b for b in built["boxes"] if b.get("focus")]
            assert [b["id"] for b in focus] == ["nightstand"]  # «ставит кружку на тумбочку»
    # Бип за кадром — ошибка самоконтроля, а не пометка.
    built = B.build(spec, "start", {**shot, "camera": {"shot": "close"}}, infos, layout)
    mira = next(b for b in built["boxes"] if b.get("main"))
    hx, hy, hz = mira["head"]
    tight = {"pos": [hx, hy, hz + 70], "target": [hx, hy - 5, hz], "fov": 40}
    check = B.check_frame(tight, built["boxes"], layout["room"], "close")
    assert not check["ok"] and any("«Бип» не в кадре" in p for p in check["problems"])
    assert any("тумбочка" in p and "не в кадре" in p for p in check["problems"])
    # Камера, срезающая голову, — самоконтроль это ловит.
    built = B.build(spec, "start", {**shot, "camera": {"shot": "medium"}}, infos, layout)
    mira = next(b for b in built["boxes"] if b.get("main"))
    hx, hy, hz = mira["head"]
    low = {"pos": [hx, hy - 40, hz + 90], "target": [hx, hy - 55, hz], "fov": 40}
    assert B.head_room(low, mira) < B.HEAD_ROOM
    check = B.check_frame(low, built["boxes"], layout["room"], "medium")
    assert not check["ok"] and any("голова «Мира» срезана" in p for p in check["problems"])


def test_blocking_room_follows_plate_via_gemini_vision_once(env):
    import json

    from app import aivideo_layout as L

    _client_, store = env
    pid, sid = _ep3(store)
    G.request_stage(store, pid, "refs")  # плита (сухой прогон)
    loc = V.location_by_slug(store, pid, "bedroom")

    class Vision:
        calls = 0

        def generate_content(self, model, payload):
            Vision.calls += 1
            parts = payload["contents"][0]["parts"]
            assert any("inline_data" in p for p in parts)  # плита — картинкой
            body = {"objects": [
                {"type": "bed", "x": 0.3, "depth": 0.7, "wall": "back", "color": "#c9b8e8"},
                {"type": "nightstand", "x": 0.62, "depth": 0.85, "wall": "back", "color": "#a07050"},
                {"type": "lamp", "x": 0.62, "depth": 0.85, "wall": "on", "on": "nightstand", "color": "#ffe0a0"},
                {"type": "desk", "x": 0.9, "depth": 0.6, "wall": "right", "color": "#b08860"},
                {"type": "rug", "x": 0.5, "depth": 0.4, "wall": "floor", "color": "#f4c6d0"}]}
            return {"candidates": [{"content": {"parts": [{"text": json.dumps(body)}]}}]}

    out = L.location_layout(store, pid, loc, vision=True, client=Vision())
    assert out["source"] == "vision" and Vision.calls == 1
    by = {o["type"]: o for o in out["objects"]}
    assert by["bed"]["pos"][0] < 0 < by["nightstand"]["pos"][0]  # как на плите: кровать слева, тумбочка правее
    assert by["desk"]["rot"] == -90 and by["rug"]["color"] == "#f4c6d0"
    assert by["lamp"]["on"] == by["nightstand"]["id"]
    assert {"garland", "dock", "window", "socks"} <= set(by)  # из описания — то, чего зрение не назвало
    again = L.location_layout(store, pid, V.location_by_slug(store, pid, "bedroom"), vision=True, client=Vision())
    assert Vision.calls == 1 and again["objects"] == out["objects"]  # кэш: одна раскладка на локацию


def test_blocking_mannequins_real_size_pose_and_camera_by_plan(env):
    """Манекены в реальном росте (человек ~165, робот-диск — низкий), руки в позе, кружка в руке; камера
    по плану (объектив → поле кадра), смотрит на персонажей, не внутри вещи; самоконтроль кадра."""
    from app import aivideo_blocking as B

    _client_, store = env
    pid, sid = _ep3(store)
    shot = V.list_shots(store, sid)[0]
    infos = {c["id"]: c for c in B.cast_info(store, pid, shot)}
    assert infos["mira"]["stand_in"] == "human" and infos["mira"]["size"] == 165
    assert infos["beep"]["stand_in"] == "disc" and infos["beep"]["size"] < 35
    assert infos["mira"]["color"] != infos["beep"]["color"]
    assert "orange" not in B.legend(store, pid, shot)
    built = B.built_of(store, shot)
    layout = B.shot_layout(store, pid, shot)
    room = layout["room"]
    boxes = [b for b in (B.L.object_box(o) for o in layout["objects"]) if b]
    for which in ("start", "end"):
        check, scene = built[which]["check"], built[which]["scene"]
        assert check["ok"], (which, check)
        assert check["shares"]["mira"] > 0.03 and check["shares"]["beep"] > 0  # все участники в кадре
        cam = scene["camera"]
        assert abs(cam["position"][0]) < room["width"] / 2 and cam["position"][1] < room["height"]
        assert not any(B._inside(b, cam["position"]) for b in boxes)
        assert 40 <= cam["fov"] <= 70  # 35 мм ≈ 54°, в тесной комнате — чуть шире
    # Манекен: голова, туловище, руки и ноги (группы суставов), рост ~165 см.
    rig = B._human_rig(165, "standing", "holding")
    hand = B._hand_local(rig, 1)
    assert 85 < hand[1] < 125 and hand[2] > 15  # рука с кружкой — перед собой на уровне пояса/груди
    up = B._hand_local(B._human_rig(165, "standing", "up"), 1)
    assert up[1] > 175  # руки над головой — «потянуться»
    sit = B._human_rig(165, "sitting", "down")
    assert sit["root"][1] < 0  # сидит — таз ниже, чем стоя
    _nodes, box = B._actor_parts({"pose": "lying"}, infos["beep"])
    assert box["height"] < 25  # робот-диск низкий, а не столб на весь кадр
    # Камера по плану: 50 мм — уже поле, чем 35 мм.
    assert B.lens_fov(50) < B.lens_fov(35) == pytest.approx(54.43, abs=0.05)


def test_blocking_self_check_rejects_bad_frame_and_keyframe_goes_without_it(env):
    """Кадр макета не прошёл самоконтроль (камера внутри шкафа, шкаф на весь кадр) — в кадр он не идёт:
    промпт без блокинга, пометка в params; хороший макет — идёт с указанием «только позиции и ракурс»."""
    from app import aivideo_blocking as B

    _client_, store = env
    pid, sid = _ep3(store)
    G.request_stage(store, pid, "refs")
    shot = V.list_shots(store, sid)[0]
    plate = V.selected_ref(store, pid, "location", V.location_by_slug(store, pid, "bedroom")["id"], "location")
    png = Path(store.get_montage_asset(plate["asset_id"])["file_path"])
    asset = store.create_montage_asset(kind="image", label="b", file_path=str(png), original_filename=png.name,
                                       mime_type="image/jpeg", size_bytes=png.stat().st_size)
    V.add_ref(store, pid, "shot", shot["id"], "blocking_start", asset["id"], prompt="blocking:test")
    job = {"project_id": pid, "ref_kind": "keyframe", "target_id": shot["id"], "model": "google/gemini-nano-banana-2.1",
           "provider": "none", "kind": "image"}
    prompt, _refs, _cap, info = G.build_request(store, job)
    assert info["blocking"] == {"used": True} and info["roles"][0] == "блокинг: старт"
    assert "ONLY for the camera angle" in prompt and "never draw mannequins" in prompt
    # Своя камера агента — внутри шкафа перед персонажами.
    B.set_blocking(store, shot["id"], spec={
        "objects": [{"id": "wardrobe", "type": "wardrobe", "pos": [0, 120], "size": [160, 220, 60]}],
        "camera": {"pos": [0, 120, 125], "target": [0, 90, 0], "fov": 54}})
    check = B.frame_check(store, V.get_shot(store, shot["id"]), "start")
    assert not check["ok"] and any("шкаф" in p for p in check["problems"]), check
    prompt, _refs, _cap, info = G.build_request(store, job)
    assert info["blocking"]["used"] is False and "шкаф" in info["blocking"]["rejected"]
    assert "3D BLOCKING" not in prompt and not any("блокинг" in r for r in info["roles"])
    assert any("отклонён самоконтролем" in m for m in info["missing"])
    # Шкаф между камерой и персонажами (камера снаружи) — перекрывает больше 40 % кадра.
    B.set_blocking(store, shot["id"], spec={
        "objects": [{"id": "wardrobe", "type": "wardrobe", "pos": [0, 140], "size": [160, 220, 40]}],
        "camera": {"pos": [0, 120, 240], "target": [0, 90, 0], "fov": 54}})
    check = B.frame_check(store, V.get_shot(store, shot["id"]), "start")
    assert not check["ok"] and any("перекрывает" in p for p in check["problems"]), check
    # Без своей камеры сервис ставит её сам: вещь стоит ровно там, где встала бы камера, — камера не
    # внутри неё (отодвинута), кадр проходит самоконтроль.
    data = V.get_shot(store, shot["id"])["blocking"]
    data["spec"].pop("camera")
    data["spec"]["objects"] = []
    B._save(store, shot["id"], data)
    free = B.built_of(store, V.get_shot(store, shot["id"]))["start"]["camera"]["pos"]
    data["spec"]["objects"] = [{"id": "plant", "type": "plant", "pos": [free[0], free[2]], "size": [40, 200, 40]}]
    B._save(store, shot["id"], data)
    built = B.built_of(store, V.get_shot(store, shot["id"]))["start"]
    plant = B.L.object_box(B.L.clean_object(data["spec"]["objects"][0], 0))
    assert not B._inside(plant, built["camera"]["pos"]) and built["check"]["ok"], built["check"]


def test_location_plate_prompt_is_empty_room_without_characters(env):
    """Плита — пустая комната: без людей, животных, роботов и персонажей проекта (иначе на плите уже
    робот-пылесос, и в кадрах вместо Бипа серый пылесос); негатив — тоже."""
    _client_, store = env
    pid, _sid = _ep3(store)
    loc = V.location_by_slug(store, pid, "bedroom")
    job = {"project_id": pid, "ref_kind": "location", "target_id": loc["id"], "model": "google/gemini-nano-banana-2.1",
           "provider": "none", "kind": "image", "slot": ""}
    prompt = G.build_request(store, job)[0]
    assert "NO people" in prompt and "NO animals" in prompt and "NO robots" in prompt
    assert "NO characters of any kind" in prompt
    assert "not Мира (" in prompt and "not Бип (круглый робот-пылесос" in prompt
    assert "nothing that looks like any of them" in prompt
    assert "draw only that thing, empty — its owner is not in the room" in prompt
    avoid = prompt.split("Avoid:", 1)[1]
    for word in ("people", "robot vacuum", "animal", "character", "mannequin"):
        assert word in avoid
    assert EP3_ROOM in prompt  # описание — дословно


def _fake_tail_video(dest):
    """Фейковое видео ffmpeg'ом: красное, последние 0.5 с — синие (последний кадр — синий)."""
    import subprocess

    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=144x256:d=1.5:r=12",
                    "-f", "lavfi", "-i", "color=c=blue:s=144x256:d=0.5:r=12", "-filter_complex",
                    "[0][1]concat=n=2:v=1:a=0,format=yuv420p", str(dest)], check=True, capture_output=True)
    return dest


def test_hailuo_no_endframes_next_start_is_tail_of_previous_video_in_order(paid_video, monkeypatch):
    """H3 берёт только стартовый кадр: конечные кадры не заказываются (смета дешевле), старт сцепленного
    плана — ПОСЛЕДНИЙ кадр готового видео прошлого плана (ffmpeg, keyframe c source=video_tail), видео
    следующего плана ждёт видео прошлого в очереди (без лишних POST)."""
    import cv2

    (_client_, store), img, vid = paid_video
    vid.uncertain, vid.cost = False, 50.0  # в пределах одобренной суммы (H3 ≈ 57 ₽ за план)
    monkeypatch.setattr(vid, "fetch", lambda result, dest, params: _fake_tail_video(dest))
    pid, sid = _ep3(store, provider="polza")
    shots = V.list_shots(store, sid)
    req = G.request_stage(store, pid, "refs")
    A.decide(store, req["approval_id"], "approve", actor="ui")
    G.run_pending(store)
    est = G.estimate(store, pid, "keyframes", sid)
    assert [i["label"] for i in est["items"]] == ["план 1: старт"]  # ни одного конечного кадра
    assert est["total_rub"] == pytest.approx(6.0)
    V.update_style(store, pid, {"video_model": "bytedance/seedance-2"}, owner=True)
    pairs = G.estimate(store, pid, "keyframes", sid)
    assert [i["ref_kind"] for i in pairs["items"]] == ["keyframe", "endframe", "endframe"]  # у Seedance — пары
    V.update_style(store, pid, {"video_model": "minimax/hailuo-3-max"}, owner=True)
    req = G.request_stage(store, pid, "keyframes", sid)
    A.decide(store, req["approval_id"], "approve", actor="ui")
    G.run_pending(store)
    assert V.selected_ref(store, pid, "shot", shots[0]["id"], "endframe") is None
    req = G.request_stage(store, pid, "video", sid)
    assert [i["start"] for i in req["estimate"]["items"]] == ["keyframe", "video_tail"]
    A.decide(store, req["approval_id"], "approve", actor="ui")
    vid.pending_polls = 10**6  # видео плана 1 ещё делается
    first = G.run_pending(store, poll_timeout=0)
    assert vid.posts == 1 and first["polling"] and len(first["waiting"]) == 1
    j2 = next(j for j in G.list_jobs(store, approval_id=req["approval_id"]) if j["target_id"] == shots[1]["id"])
    assert j2["status"] == "approved" and "жду видео прошлого плана" in j2["error"]
    vid.pending_polls = 0
    second = G.run_pending(store, poll_timeout=0)
    assert vid.posts == 2 and second["resumed"]["finished"]
    v1 = V.selected_ref(store, pid, "shot", shots[0]["id"], "shot_video")
    tail = V.selected_ref(store, pid, "shot", shots[1]["id"], "keyframe")
    assert tail["prompt"] == f"source=video_tail; video_ref={v1['id']}"
    j2 = G.get_job(store, j2["id"])
    assert j2["status"] == "done" and j2["params"]["start_ref_id"] == tail["id"] and j2["params"]["mode"] == "start"
    tail_file = store.get_montage_asset(tail["asset_id"])["file_path"]
    pixel = cv2.imread(tail_file).mean(axis=(0, 1))  # BGR
    assert pixel[0] > 150 and pixel[2] < 80  # последний кадр (синий), а не начало видео (красное)
    assert Path(vid.last_refs[0]).name == Path(tail_file).name


def _png(w: int = 54, h: int = 96) -> bytes:
    import struct
    import zlib

    raw = b"".join(b"\x00" + bytes([200, 180, 160]) * w for _ in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def test_blocking_3d_engine_renders_frames_and_legend(env, monkeypatch):
    from app import aivideo_blocking as B
    from app import aivideo_blocking3d as B3

    _client_, store = env
    monkeypatch.setenv("AIV_BLOCKING_ENGINE", "auto")
    real_available = B3.available
    monkeypatch.setattr(B3, "available", lambda: True)
    specs: list[dict] = []
    monkeypatch.setattr(B3, "render_png", lambda spec: specs.append(spec) or _png())
    pid, sid, out = _state_project(store)
    shot = V.get_shot(store, out["shots"][1]["id"])
    plans = B.frame_plans(store, shot)
    assert {p["engine"] for p in plans.values()} == {"3d"}
    assert sorted(B.needs_render(store, pid, shot)) == sorted(plans)
    ref = B.render(store, int(shot["id"]), "start")
    assert ref["prompt"].startswith("blocking3d:") and len(specs) == 1
    spec = specs[0]
    # Те же данные, что у 2D: камера плана, персонаж в позе из клипа, носок в руке — на сокете кисти.
    mira = next(c for c in spec["characters"] if c["id"] == "mira")
    assert mira["style"] == "flat" and mira["outfit"]["fit"] == "loose"
    assert mira["actions"][0]["clip"] == B3.POSE_CLIPS[("standing", "holding")][0]
    sock = next(o for o in spec["objects"] if o["id"].startswith("prop_"))
    assert sock["attach"][0]["to"] == "mira:hand_r"
    assert spec["camera"]["keys"][0]["position"] and spec["camera"]["clamp"] is False
    # Тот же кадр второй раз не рисуется; конец ещё нужен.
    assert B.render(store, int(shot["id"]), "start")["id"] == ref["id"] and len(specs) == 1
    assert B.needs_render(store, pid, shot) == ["end"]
    assert "= Мира" in B.legend(store, pid, shot) and "mannequin" not in B.legend(store, pid, shot)
    # Выключатель: AIV_BLOCKING_ENGINE=2d — снова холст.
    monkeypatch.setenv("AIV_BLOCKING_ENGINE", "2d")
    monkeypatch.setattr(B3, "available", real_available)
    assert {p["engine"] for p in B.frame_plans(store, shot).values()} == {"2d"}


def test_blocking_3d_failure_falls_back_to_canvas(env, monkeypatch):
    _need_compositor()
    from app import aivideo_blocking as B
    from app import aivideo_blocking3d as B3
    from app.scene3d import Scene3dError

    _client_, store = env
    monkeypatch.setattr(B3, "available", lambda: True)

    def boom(spec):
        raise Scene3dError("нет браузера")

    monkeypatch.setattr(B3, "render_png", boom)
    pid, sid, out = _state_project(store)
    ref = B.render(store, int(out["shots"][0]["id"]), "start")
    assert ref["prompt"].startswith("blocking:")


def test_blocking_auto_spec_keeps_places_between_shots(monkeypatch):
    from app import aivideo_blocking as B
    from app import aivideo_layout as L

    layout = L.auto_layout("спальня, кровать, прикроватная тумбочка с лампой, окно, ковёр")
    cast = [{"id": "mira", "action": "ставит кружку"}, {"id": "bip", "action": "стукается бампером о тумбочку"}]
    st = {"characters": [{"id": "mira", "pose": "standing", "position": "center foreground", "holding": "кружка"},
                         {"id": "bip", "pose": "lying", "position": "left background"}],
          "props": [{"name": "кружка", "state": "в руках Миры"}]}
    en = {"characters": [{"id": "mira", "pose": "standing", "position": "center foreground", "holding": "ничего"},
                         {"id": "bip", "pose": "lying", "position": "center foreground"}],
          "props": [{"name": "кружка", "state": "стоит на прикроватной тумбочке"}]}
    s1 = {"id": 1, "idx": 1, "cast": cast, "location": "bedroom", "action": "Мира ставит кружку на тумбочку",
          "state": st, "end_state": en, "continues_from_prev": False}
    s2 = {"id": 2, "idx": 2, "cast": cast, "location": "bedroom", "action": "Бип подкатывается к тумбочке",
          "state": en, "end_state": en, "continues_from_prev": True}
    infos = [{"id": "mira", "stand_in": "human"}, {"id": "bip", "stand_in": "disc"}]
    monkeypatch.setattr(B, "cast_info", lambda store, pid, shot: infos)
    a = B.auto_spec(None, 1, s1, s2, layout)
    b = B.auto_spec(None, 1, s2, None, layout, prev=[s1])
    stand = next(o for o in layout["objects"] if o["type"] == "nightstand")
    mira_a = {m: next(x for x in a[m]["actors"] if x["id"] == "mira")["pos"] for m in ("start", "end")}
    # Ставит кружку на тумбочку — стоит у тумбочки и в начале, и в конце.
    assert mira_a["start"] == mira_a["end"] == B._in_front(stand, 35.0)
    # Продолжение: начало плана 2 — там, где закончился план 1.
    assert next(x for x in b["start"]["actors"] if x["id"] == "mira")["pos"] == mira_a["end"]
    # Конец: Бип у вещи из своего действия («о тумбочку»).
    assert next(x for x in b["end"]["actors"] if x["id"] == "bip")["pos"] == B._in_front(stand, 25.0)
