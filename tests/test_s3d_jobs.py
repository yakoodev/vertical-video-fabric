"""3D-серия: задание рендера по шагам (голос → 3D → проверки → лист → холст-клип) с подменённым рендером,
платный голос только по карточке, пустые проверки — провал, REST и инструменты агента. Без сети, денег и браузера."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app import aivideo_gen as G
from app import aivideo_providers as PR
from app import autopilot as A
from app import projects as P
from app import s3d_render as R
from app import s3d_service as SVC
from app import s3d_store as D
from app import s3d_vrm as SV
from app import vtubika_vrm as VRM
from app.agent_tools import ToolContext, run_tool
from app.settings import settings
from test_api_pipeline import _client, _require_ffmpeg

SCRIPT = "Ника: Привет, это мой первый эфир!\nВ чате пока тихо.\nНика: Ну и ладно, я всё равно счастлива."


def _expand(spec: dict) -> dict:
    keys = []
    for m in spec["presets"].get("camera") or []:
        keys += [{"t": m["t0"], "position": [0, 1.4, 2.5], "target": m["target"], "fov": m["fov"], "roll": 0, "cut": True},
                 {"t": m["t1"], "position": [0, 1.4, 2.3], "target": m["target"], "fov": m["fov"], "roll": 0}]
    return {"camera": {"keys": keys}, "characters": [{"timeline": {}}], "lights": []}


class Render:
    """Подмена node: ролик-заглушка нужной длины и честные отчёты (присутствие, касания)."""

    def __init__(self, contact: dict | None = None, code: int = 0, error: str = ""):
        self.calls, self.contact, self.code, self.error = [], contact, code, error

    def render(self, spec_path: Path, out: Path) -> dict:
        import json

        spec = json.loads(Path(spec_path).read_text("utf-8"))
        self.calls.append(spec)
        if self.code:
            return {"code": self.code, "report": None, "error": self.error}
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=#203040:s=108x192:d={spec['duration']}:r=30",
                        "-pix_fmt", "yuv420p", str(out)], check=True)
        n = round(spec["duration"] * 30)
        need = VRM.hero_frames(spec)
        return {"code": 0, "error": "", "report": {"ok": True, "frames": n, "fps": 30, "seconds": 1.0, "black_frames": [],
                                                   "presence": {"frames": n, "expected": need, "missing": 0, "first_missing": []}}}

    def check(self, _spec_path: Path) -> dict | None:
        return self.contact


GOOD = {"ok": True, "frames": 100, "fingers": {"min_gap_mm": 4.4}, "chair": {"min_gap_mm": 9.0}, "hair": {"min_gap_mm": 3.0},
        "hands": {"left": {"min_gap_mm": 6.0}}, "hand_spans": 0, "empty": []}


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
    monkeypatch.setattr(SV, "expand_presets", _expand)
    fake = Render(GOOD)
    monkeypatch.setattr(R, "render_vrm", fake.render)
    monkeypatch.setattr(R, "check_vrm", fake.check)
    monkeypatch.setattr(R, "ensure_vrm_bundle", lambda: Path("vrm.js"))
    client, store = _client(tmp_path, monkeypatch)
    pid = P.create_project(store, "3D-серия", kind="series3d")["id"]
    return client, store, pid, fake


def _episode(store, pid) -> dict:
    return SVC.create(store, pid, script=SCRIPT, title="Первый эфир", cast=["nika"], author="test", client=None)


def test_dry_render_goes_end_to_end_to_a_clip(env):
    _client, store, pid, fake = env
    ep = _episode(store, pid)
    assert ep["plan"]["lines"][0]["who"] == "nika" and ep["estimate"]["total_rub"] == 0
    out = SVC.render(store, ep["id"], dry_run=True, run=True)
    job = D.get_job(store, out["job"]["id"])
    assert job["status"] == "done", (job["error"], job["note"])
    res = job["result"]
    assert res["clip_id"] and res["sheet_asset_id"] and res["video_asset_id"] and len(fake.calls) == 1
    checks = {c["id"]: c for c in res["checks"]}
    assert set(checks) == {"render", "black", "presence", "cut", "contact", "words", "sheet"}
    assert all(c["ok"] for c in checks.values()) and res["checks_ok"], checks
    spec = fake.calls[0]
    assert spec["timeline"]["speech"] and spec["timeline"]["mouthEnv"]["values"]  # рот по голосу героини
    assert D.get_episode(store, ep["id"])["status"] == "done" and D.get_episode(store, ep["id"])["clip_id"] == res["clip_id"]
    layers = store.get_clip_plan(res["clip_id"])["render_settings"]["layers"]
    kinds = {lay["type"] for lay in layers}
    assert {"video", "audio", "captions", "text"} <= kinds
    assert any(lay["type"] == "audio" and lay["props"].get("role") == "voice" for lay in layers)
    words = store.get_clip_plan_subtitles(res["clip_id"])["words"]
    assert any("эфир" in str(w.get("word") or w.get("text")) for w in words)
    # Повторный рендер — новая версия того же клипа; голос уже записан и не перезаказывается.
    jobs_before = store.db.query_one("SELECT COUNT(*) AS n FROM aiv_gen_jobs")["n"]
    again = D.get_job(store, SVC.render(store, ep["id"], dry_run=True, run=True)["job"]["id"])
    assert again["status"] == "done" and store.db.query_one("SELECT COUNT(*) AS n FROM aiv_gen_jobs")["n"] == jobs_before


def test_empty_checks_fail_the_job(env, monkeypatch):
    _client, store, pid, fake = env
    fake.contact = {"ok": False, "frames": 0, "fingers": {"min_gap_mm": None}, "chair": {}, "hair": {}, "hands": {}}
    ep = _episode(store, pid)
    job = D.get_job(store, SVC.render(store, ep["id"], dry_run=True, run=True)["job"]["id"])
    assert job["status"] == "failed" and "не выпускаю" in job["error"]
    bad = [c for c in job["result"]["checks"] if not c["ok"]]
    assert any(c["id"] == "contact" for c in bad) and not job["result"].get("clip_id")


def test_render_failure_shows_cut_check(env):
    _client, store, pid, fake = env
    fake.code, fake.error = 1, "кадр 40 (t=1.333): персонаж разрезан вертикалью x=540 на 31% высоты кадра"
    ep = _episode(store, pid)
    job = D.get_job(store, SVC.render(store, ep["id"], dry_run=True, run=True)["job"]["id"])
    assert job["status"] == "failed"
    checks = {c["id"]: c for c in job["result"]["checks"]}
    assert not checks["render"]["ok"] and not checks["cut"]["ok"] and "разрезан" in checks["cut"]["detail"]


def test_violations_still_make_a_clip_but_show_in_card(env):
    _client, store, pid, fake = env
    fake.contact = {**GOOD, "ok": False, "hand_spans": 2}
    ep = _episode(store, pid)
    job = D.get_job(store, SVC.render(store, ep["id"], dry_run=True, run=True)["job"]["id"])
    assert job["status"] == "done" and job["result"]["clip_id"] and not job["result"]["checks_ok"]
    contact = next(c for c in job["result"]["checks"] if c["id"] == "contact")
    assert not contact["ok"] and "2 отрезков" in contact["detail"]
    status, ok = run_tool(ToolContext(store=store), "series3d_status", {"episode_id": ep["id"]})
    assert ok and status["failed_checks"] and "проверки нашли" in status["summary"]


def test_paid_voice_waits_for_the_card(env, monkeypatch):
    _client, store, pid, fake = env
    from app.dubbing import DryRunTTS, Speech

    spoken = []

    class FakeLiam(DryRunTTS):  # не настоящий Polza: сеть и деньги тесту запрещены
        name, paid = "polza", True

        def price_rub_per_char(self):
            return 0.01

        def synthesize(self, text, path):
            spoken.append(text)
            return Speech(super().synthesize(text, path).path, 0.4)

    free = PR.get_tts
    monkeypatch.setattr(PR, "get_tts", lambda name, model=None, voice=None, style=None: (
        FakeLiam() if name == "polza" else free(name, model, voice, style)))
    D.seed_builtins(store, pid)
    D.save_item(store, pid, "character", {"type": "vrm", "vrm": {"base": "nika"}, "voice": {"provider": "polza", "voice": "lily"}},
                slug="nika", name="Ника")
    D.save_settings(store, pid, {"narrator_voice": {"provider": "none"}})
    ep = _episode(store, pid)
    est = SVC.estimate(store, ep["id"])
    assert est["total_rub"] > 0 and est["paid_lines"] == 2 and "Разрешить" in est["label"]
    job = D.get_job(store, SVC.render(store, ep["id"], run=True)["job"]["id"])
    assert job["status"] == "awaiting_approval" and job["approval_id"] and spoken == [] and not fake.calls
    card = A.get_approval(store, job["approval_id"])
    assert card["payload"]["amount_rub"] > 0 and "lily" in card["question"] and card["title"].startswith("3D-серия")
    # Пока карточка ждёт — ничего не делаем; «Разрешить» — голос и рендер.
    assert R.advance(store, job["id"])["status"] == "awaiting_approval"
    A.decide(store, job["approval_id"], "approve", actor="ui")
    monkeypatch.setattr(settings, "aivideo_enabled", True)
    G.run_approved(store, job["approval_id"], confirm=True)
    done = R.advance(store, job["id"])
    assert done["status"] == "done", (done["error"], done["note"])
    assert len(spoken) == 2 and len(fake.calls) == 1


def test_rejected_card_cancels(env, monkeypatch):
    _client, store, pid, _fake = env
    D.seed_builtins(store, pid)
    D.save_item(store, pid, "character", {"type": "vrm", "vrm": {"base": "nika"}, "voice": {"provider": "polza", "voice": "lily"}},
                slug="nika", name="Ника")
    ep = _episode(store, pid)
    job = D.get_job(store, SVC.render(store, ep["id"], run=True)["job"]["id"])
    A.decide(store, job["approval_id"], "reject", actor="ui")
    assert R.advance(store, job["id"])["status"] == "cancelled"
    assert D.get_episode(store, ep["id"])["status"] == "cancelled"


def test_rest_api_create_edit_render(env):
    client, store, pid, _fake = env
    r = client.post(f"/api/projects/{pid}/series3d/episodes", json={"script": SCRIPT, "title": "Эфир", "cast": ["nika"]})
    assert r.status_code == 200, r.text
    eid = r.json()["id"]
    o = client.get(f"/api/projects/{pid}/series3d").json()
    assert o["characters"] and o["locations"] and o["episodes"][0]["id"] == eid
    bad = client.put(f"/api/series3d/episodes/{eid}/plan", json={"changes": [{"op": "line", "id": "l1", "set": {"shots": [{"camera": "nope"}]}}]})
    assert bad.status_code == 400 and bad.json()["detail"]["problems"]
    ok = client.put(f"/api/series3d/episodes/{eid}/plan", json={"changes": [{"op": "set", "field": "title", "value": "Новое"}], "note": "имя"})
    assert ok.status_code == 200 and ok.json()["plan_version"] == 2 and ok.json()["versions"][0]["note"] == "имя"
    assert client.get(f"/api/series3d/episodes/{eid}/plan/1").json()["plan"]["title"] != "Новое"
    cat = client.get("/api/series3d/catalog").json()
    assert "face" in cat["framings"] and any(m["name"] == "slow_push" for m in cat["moves"])
    assert client.get(cat["moves"][0]["thumb"]).status_code in (200, 404)
    r = client.post(f"/api/series3d/episodes/{eid}/render", json={"dry_run": True})
    assert r.status_code == 200 and r.json()["job"]["status"] == "queued" and r.json()["estimate"]["total_rub"] == 0
    assert client.put(f"/api/series3d/episodes/{eid}/plan", json={"changes": []}).status_code == 400  # рендерится
    R.advance(store, r.json()["job"]["id"])
    full = client.get(f"/api/series3d/episodes/{eid}").json()
    assert full["job"]["status"] == "done" and full["job"]["sheet_url"].startswith("/media/montage-assets/")
    s = client.post(f"/api/projects/{pid}/series3d/library", json={"kind": "character", "slug": "mila", "name": "Мила",
                                                                   "type": "vrm", "vrm": {"base": "vera", "recolor": {"Hair": ["#000000", "#ff00ff"]}}})
    assert s.status_code == 200 and s.json()["vrm"]["recolor"]["Hair"][1] == "#ff00ff"
    assert client.post(f"/api/projects/{pid}/series3d/library", json={"kind": "character", "type": "vrm", "vrm": {"base": "zzz"}}).status_code == 400


def test_agent_tools(env):
    _client, store, pid, _fake = env
    ctx = ToolContext(store=store)
    out, ok = run_tool(ctx, "series3d_create_episode", {"project_id": pid, "script": SCRIPT, "cast": ["nika"]})
    assert ok, out
    eid = out["episode_id"]
    lst, ok = run_tool(ctx, "series3d_list", {"project_id": pid})
    assert ok and lst["episodes"][0]["id"] == eid and "Ракурсы" in lst["plan_schema"]
    upd, ok = run_tool(ctx, "series3d_update_plan", {"episode_id": eid, "changes": [
        {"op": "line", "id": "l2", "set": {"events": [{"kind": "comment", "user": "kot", "text": "привет", "card": True}]}}]})
    assert ok and upd["plan_version"] == 2
    bad, ok = run_tool(ctx, "series3d_update_plan", {"episode_id": eid, "changes": [{"op": "line", "id": "l2", "set": {
        "performance": [{"preset": "dancee"}]}}]})
    assert not ok and any("dancee" in p for p in bad["problems"])
    plan, ok = run_tool(ctx, "series3d_plan_from_script", {"project_id": pid, "script": "Раз. Два!", "cast": ["nika"]})
    assert ok and not plan["saved"] and plan["source"] == "rules"
    rnd, ok = run_tool(ctx, "series3d_render", {"episode_id": eid, "dry_run": True})
    assert ok and rnd["status"] == "queued"
    R.advance(store, rnd["job"]["id"])
    st, ok = run_tool(ctx, "series3d_status", {"episode_id": eid})
    assert ok and st["status"] == "done" and st["job"]["clip_id"] and "Готово" in st["summary"]
    item, ok = run_tool(ctx, "series3d_save_item", {"project_id": pid, "kind": "location", "slug": "warm", "name": "Тёплая",
                                                    "data": {"type": "plate", "plate": {"light": {"keyColor": "#ffe2c4"}}}})
    assert ok and item["item"]["plate"]["light"]["keyColor"] == "#ffe2c4"
