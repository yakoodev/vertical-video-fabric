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
    """Замер nano-banana 1K — 3 ₽ (прайс ×1); 2K не мерили — прайс ×2. Синхронный ответ POST /media
    с data.url (так часто отвечает видео-модель) — ссылка сразу, без requestId."""
    import httpx

    assert PR.image_price("google/gemini-nano-banana-2.1", "1K") == {"rub": 3.0, "list_rub": 3.0, "measured": True,
                                                                    "note": "замер"}
    assert PR.image_price("google/gemini-nano-banana-2.1", "2K")["rub"] == pytest.approx(9.0)

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
