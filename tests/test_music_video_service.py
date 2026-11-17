"""«Клип на песню» как работа сервиса (app/music_video*.py): вход задания, разбор → план → оценка → карточка на
картинки → картинки → сцены → кадры → клип, рендер на компьютере через очередь, инструменты и плейбук.

Никаких настоящих платных вызовов, Whisper и Blender: разбор песни — синтетика, «платный» провайдер — подделка,
подпроцессы инструмента (make.py) — подделка, пишущая те же файлы. Настоящий прогон — docs/playbooks/music-video.md."""

from __future__ import annotations

import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from app import aivideo_gen as G
from app import aivideo_providers as PR
from app import autopilot as A
from app import music_video as M
from app import music_video_job as J
from app.settings import settings
from test_api_pipeline import _client, _require_ffmpeg

WORDS = ["Мишка", "косолапый", "по", "лесу", "идёт", "шишки", "собирает", "песенки", "поёт"]


class FakePaid:
    """«Платный» провайдер картинок: считает POST, картинку рисует сухим прогоном."""

    name = "polza"
    paid = True

    def __init__(self):
        self.posts = 0

    def submit(self, params, refs):
        self.posts += 1
        return f"req-{self.posts}"

    def check(self, remote_id):
        return {"status": "completed", "url": "https://example.invalid/x.jpg", "cost_rub": 3.0}

    def fetch(self, result, dest, params):
        return PR.DryRunImages().fetch(result, dest, params)


def _fake_analysis(store, job: dict) -> dict:
    """Вместо Whisper и demucs: слова на долях, атаки — на них же."""
    start, end = float(job["start_sec"]), float(job["end_sec"])
    beats = [round(start + 0.5 * i, 3) for i in range(int((end - start) / 0.5))]
    words = [{"w": w, "s": beats[2 + 2 * i], "e": beats[2 + 2 * i] + 0.3, "p": 1.0, "on": beats[2 + 2 * i],
              "syl": [beats[2 + 2 * i]], "snap": 0.0, "line": i // 5} for i, w in enumerate(WORDS)]
    an = {"song": "x", "start": start, "end": end, "duration": end - start, "bpm": 120.0, "period": 0.5,
          "beats": beats, "downbeats": beats[::4], "onsets": [[b, 1.0, -10.0] for b in beats], "words": words}
    out = M.job_dir(job)
    for path in (out / "analysis.json", out / "work" / "analysis.json"):
        path.write_text(json.dumps(an, ensure_ascii=False), encoding="utf-8")
    (out / "work" / "audio.wav").write_bytes(Path(store.get_audio_track(int(job["track_id"]))["file_path"]).read_bytes())
    return an


class FakeMake:
    """Подпроцессы make.py: пишут то, что пишет инструмент (scene.json, кадры, mp4, лист кадров)."""

    def __init__(self, nf: int = 4):
        self.calls: list[str] = []
        self.nf = nf

    def __call__(self, store, job, stages, extra, log_name, timeout, progress=None):
        self.calls.append(stages)
        out = M.job_dir(job)
        work = out / "work"
        if stages == "prep":
            assert sorted(p.stem for p in (work / "img").glob("*.jpg")) == sorted((M.load_plan(job) or {})["images"])
            (work / "scene.json").write_text(json.dumps({"nf": self.nf, "fps": 30, "scenes": []}), encoding="utf-8")
        elif stages == "build":
            (work / "frames").mkdir(exist_ok=True)
            for k in range(self.nf):
                (work / "frames" / f"f_{k:04d}.png").write_bytes(b"png")
        elif stages == "encode":
            _tiny_mp4(out / f"mv{job['id']}.mp4")
        elif stages == "check":
            (out / "sheet.jpg").write_bytes(b"jpg")


def _tiny_mp4(path: Path) -> Path:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=108x192:d=1", "-f", "lavfi",
                    "-i", "sine=frequency=440:duration=1", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", str(path)], check=True)
    return path


@pytest.fixture
def env(tmp_path, monkeypatch):
    _require_ffmpeg()
    monkeypatch.setattr(settings, "aivideo_enabled", False)
    monkeypatch.setattr(settings, "aivideo_month_cap_rub", 3000.0)
    monkeypatch.setattr(settings, "aivideo_max_budget_rub", 2500.0)
    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr(settings, "polza_api_key", "")
    monkeypatch.setattr(G, "BALANCE_SETTLE_SEC", 0.0)
    monkeypatch.setenv("MV_BLENDER", "")
    monkeypatch.setenv("MV_BLENDER_ROOT", str(tmp_path / "no-blender"))
    monkeypatch.setattr(J, "analyze", lambda store, job: _fake_analysis(store, job))
    fake_make = FakeMake()
    monkeypatch.setattr(J, "_make", fake_make)
    fake = FakePaid()
    monkeypatch.setattr(PR, "get_image_provider", lambda name: fake if name == "polza" else PR.DryRunImages())
    client, store = _client(tmp_path, monkeypatch)
    from app import projects as P

    pid = P.create_project(store, "Песни", kind="other")["id"]
    song = settings.audio_dir / "song.wav"
    song.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=12",
                    str(song)], check=True)
    track = store.create_audio_track("Автор - Мишка", song, duration_sec=12.0)
    return {"client": client, "store": store, "pid": pid, "track": track, "fake": fake, "make": fake_make,
            "tmp": tmp_path}


def _blender(env, monkeypatch) -> None:
    exe = env["tmp"] / "blender.exe"
    exe.write_bytes(b"")
    monkeypatch.setenv("MV_BLENDER", str(exe))


def _approvals(store, pid) -> list[dict]:
    return [dict(r) for r in store.db.query_all("SELECT * FROM approvals WHERE project_id = ?", (pid,))]


# ---------------------------------------------------------------- вход


def test_create_checks_song_segment_style_and_saves_lyrics(env):
    from app import frag_movie as F

    store, pid, track = env["store"], env["pid"], env["track"]
    with pytest.raises(M.MvError, match="нужна песня"):
        M.create_job(store, pid)
    with pytest.raises(M.MvError, match="style"):
        M.create_job(store, pid, track_id=track["id"], style="anime")
    with pytest.raises(M.MvError, match="короче"):
        M.create_job(store, pid, track_id=track["id"], start=10, end=11)
    job = M.create_job(store, pid, track_id=track["id"], style="paper", start=1, end=30, lyrics="Мишка косолапый\n",
                       images=4)
    assert job["end_sec"] == 12.0, "конец участка — не дальше конца песни"
    assert job["title"] == "Мишка" and job["style"] == "paper" and job["options"]["images"] == 4
    assert F.track_lyrics(store, track["id"])["text"] == "Мишка косолапый"


def test_words_from_alignment_lines_keep_syllables_and_strip_punctuation():
    lines = [{"i": 0, "unsure": False, "words": [
        {"w": "Мишка,", "t0": 1.2, "t1": 1.5, "how": "heard", "syl": [1.2, 1.35], "snap": 0.04},
        {"w": "идёт!", "t0": 1.6, "t1": 1.9, "how": "interp"}]},
        {"i": 1, "unsure": True, "words": [{"w": "за", "t0": 99.0, "t1": 99.2, "how": "heard"}]}]
    words = J.words_from_lines(lines, 1.0, 10.0)
    assert [w["w"] for w in words] == ["Мишка", "идёт"]
    assert words[0]["on"] == 1.2 and words[0]["syl"] == [1.2, 1.35] and words[0]["p"] == 1.0
    assert words[1]["syl"] == [1.6] and words[1]["p"] == 0.5


def test_draft_plan_gets_prompts_from_lyrics_and_validates(env):
    store, pid, track = env["store"], env["pid"], env["track"]
    job = M.create_job(store, pid, track_id=track["id"], start=0, end=12, images=3)
    an = _fake_analysis(store, job)
    plan = M.draft_plan(job, an)
    assert len(plan["images"]) == 3 and plan["scenes"]
    assert all("TODO" not in s["prompt"] for s in plan["images"].values())
    assert any("Мишка" in s["prompt"] or "лесу" in s["prompt"] for s in plan["images"].values())
    with pytest.raises(M.MvError, match="TODO"):
        M.validate_plan({**plan, "images": {"bg0": {"kind": "plate", "prompt": "TODO: x"}}})
    with pytest.raises(M.MvError, match="свои refs"):
        M.validate_plan({**plan, "images": {"a": {"kind": "cutout", "prompt": "bear"},
                                            "b": {"kind": "cutout", "prompt": "run", "refs": ["a"]},
                                            "c": {"kind": "cutout", "prompt": "jump", "refs": ["b"]}}})


# ---------------------------------------------------------------- деньги: ничего платного без «да»


def test_images_need_one_owner_card_nothing_paid_before_approval(env, monkeypatch):
    store, pid, track, fake = env["store"], env["pid"], env["track"], env["fake"]
    job = J.create(store, pid, track_id=track["id"], start=0, end=12, max_rub=60, images=3)
    out = J.advance(store, job["id"])
    assert out["status"] == "awaiting_approval" and fake.posts == 0
    card = A.get_approval(store, out["approval_id"])
    assert card["status"] == "pending" and card["payload"]["group"] == f"mv_job:{job['id']}"
    assert card["payload"]["amount_rub"] == pytest.approx(out["estimate_rub"]) and out["estimate_rub"] > 0
    assert "Клип на песню «Мишка»" in card["question"] and "картинок 3" in card["question"]

    # Работник ИИ-видео без «Разрешить» ничего не платит; задание ждёт.
    G.run_pending(store)
    assert fake.posts == 0
    assert J.advance(store, job["id"])["status"] == "awaiting_approval"

    # Одобрено, но платная генерация выключена — запуски держатся, задание ждёт (не падает).
    A.decide(store, out["approval_id"], "approve", actor="ui")
    G.run_pending(store)
    assert fake.posts == 0
    held = J.advance(store, job["id"])
    assert held["status"] == "generating" and "AIVIDEO_ENABLED" in held["message"]

    monkeypatch.setattr(settings, "aivideo_enabled", True)
    G.run_approved(store, out["approval_id"], confirm=True)
    assert fake.posts == 3
    ledger = G.ledger(store, pid)
    assert len(ledger) == 3 and sum(row["amount_rub"] for row in ledger) == pytest.approx(9.0)
    assert J.readiness(store, M.get_job(store, job["id"]))["all_ready"]


def test_estimate_over_job_budget_sends_no_card(env):
    store, pid, track, fake = env["store"], env["pid"], env["track"], env["fake"]
    job = J.create(store, pid, track_id=track["id"], start=0, end=12, max_rub=1, images=3)
    out = J.advance(store, job["id"])
    assert out["status"] == "failed" and "больше потолка задания" in out["error"]
    assert not _approvals(store, pid) and fake.posts == 0
    # Потолок подняли — продолжение с оценки, карточка уходит.
    again = J.resume(store, job["id"], max_rub=50)
    assert again["status"] == "planned"
    assert J.advance(store, job["id"])["status"] == "awaiting_approval" and fake.posts == 0


def test_spend_setting_approves_within_limits_and_reject_cancels(env, monkeypatch):
    from app import agent_settings as S

    store, pid, track, fake = env["store"], env["pid"], env["track"], env["fake"]
    monkeypatch.setattr(settings, "aivideo_enabled", True)
    S.save_settings(store, pid, {"spend": {"enabled": True, "per_run_rub": 20, "per_month_rub": 100}})
    job = J.create(store, pid, track_id=track["id"], start=0, end=12, max_rub=60, images=2)
    out = J.advance(store, job["id"])
    card = A.get_approval(store, out["approval_id"])
    assert card["status"] == "approved" and card["decided_by"] == S.SPEND_ACTOR and out["status"] == "generating"
    G.run_pending(store)
    assert fake.posts == 2
    assert all("настройка проекта" in row["note"] for row in G.ledger(store, pid))

    # Сумма больше «за раз» — карточка владельцу; «Отказ» — задание отменено, ничего не оплачено.
    big = J.create(store, pid, track_id=track["id"], start=0, end=12, max_rub=60, images=8)
    out = J.advance(store, big["id"])
    assert out["status"] == "awaiting_approval" and A.get_approval(store, out["approval_id"])["status"] == "pending"
    A.decide(store, out["approval_id"], "reject", actor="ui")
    assert J.advance(store, big["id"])["status"] == "cancelled" and fake.posts == 2


# ---------------------------------------------------------------- конвейер до клипа


def test_dry_run_goes_to_a_project_clip_without_cards(env, monkeypatch):
    store, pid, track, fake = env["store"], env["pid"], env["track"], env["fake"]
    _blender(env, monkeypatch)
    job = J.create(store, pid, track_id=track["id"], start=0, end=12, images=2, dry_run=True)
    out = J.advance(store, job["id"])
    assert out["status"] == "done", out["error"]
    assert env["make"].calls == ["prep", "build", "encode", "check"]
    assert not _approvals(store, pid) and fake.posts == 0 and not G.ledger(store, pid)
    plan = store.get_clip_plan(out["clip_plan_id"])
    from app import projects as P

    assert P.project_of_plan(store, int(plan["id"])) == pid
    clip = store.get_clip(out["clip_id"])
    assert clip["status"] == "succeeded" and Path(clip["output_path"]).is_file()
    view = J.view(store, job["id"])
    assert view["progress"] == 1.0 and view["clip_url"] == f"/clips/{out['clip_plan_id']}"
    assert "sheet.jpg" in view["files"] and all(i["ready"] for i in view["images"])


def test_finished_clip_goes_to_the_feed_once_per_job(env, monkeypatch):
    """Готовое — в «Ленту» (владелец 16.11: не 20 сообщений в Telegram): сухой прогон — нет, настоящий — версия работы."""
    from app import feed as FEED

    store, pid, track = env["store"], env["pid"], env["track"]
    _blender(env, monkeypatch)
    dry = J.create(store, pid, track_id=track["id"], start=0, end=12, images=1, dry_run=True)
    out = J.advance(store, dry["id"])
    assert out["status"] == "done" and not FEED.list_feed(store)["items"], "сухой прогон в ленту не идёт"
    job = M.get_job(store, dry["id"])
    first = J.to_feed(store, job, int(out["clip_id"]), "клип на песню, v1")
    M.update_job(store, job["id"], options={**(job.get("options") or {}), "feed": first})
    again = J.to_feed(store, M.get_job(store, job["id"]), int(out["clip_id"]), "v2")
    assert first["work_id"] == again["work_id"] and first["version_id"] != again["version_id"]
    work = FEED.get_work(store, first["work_id"])
    assert work["kind"] == "other" and work["project_id"] == pid and len(work["versions"]) == 2
    assert work["versions"][0]["status"] == "review" and work["versions"][0]["variants"][0]["label"] == "9:16"
    assert J.view(store, job["id"])["feed"]["url"] == f"/feed?work={first['work_id']}"


def test_render_resumes_from_the_first_missing_frame(env, monkeypatch):
    store, pid, track = env["store"], env["pid"], env["track"]
    _blender(env, monkeypatch)
    job = J.create(store, pid, track_id=track["id"], start=0, end=12, images=1, dry_run=True)
    seen: list[list[str]] = []
    real = env["make"]

    def make(store_, job_, stages, extra, *a, **k):
        if stages == "build":
            seen.append(list(extra))
        return real(store_, job_, stages, extra, *a, **k)

    monkeypatch.setattr(J, "_make", make)
    monkeypatch.setattr(J, "finish", lambda s, j: None)  # стоп после кадров
    J.advance(store, job["id"])
    frames = M.job_dir(job) / "work" / "frames"
    for k in (2, 3):
        (frames / f"f_{k:04d}.png").unlink()
    M.set_status(store, job["id"], "prepared")  # «перезапуск сервиса посреди рендера»
    J.advance(store, job["id"])
    assert "--frames" not in seen[0] and seen[1][seen[1].index("--frames") + 1] == "2-3"


def test_without_blender_frames_go_to_the_host_worker_queue(env, monkeypatch):
    from app import render_queue as RQ

    store, pid, track, client = env["store"], env["pid"], env["track"], env["client"]
    job = J.create(store, pid, track_id=track["id"], start=0, end=12, images=2, dry_run=True)
    out = J.advance(store, job["id"])
    assert out["status"] == "remote_render" and "render_worker.py" in out["message"]
    assert env["make"].calls == ["prep"], "кадры и mp4 — не здесь"
    assert RQ.claim(store, "old-worker") is None, "старый воркер (только 3D-серии) задание не берёт"
    taken = RQ.claim(store, "pc", kinds=("series3d", "music_video"))
    assert taken["kind"] == "music_video" and taken["payload"]["mv_job"] == job["id"]
    got = client.get(taken["payload"]["bundle"])
    assert got.status_code == 200
    bundle = env["tmp"] / "bundle.zip"
    bundle.write_bytes(got.content)
    names = set(zipfile.ZipFile(bundle).namelist())
    assert {"plan.json", "analysis.json", "work/audio.wav"} <= names
    assert {n for n in names if n.startswith("work/img/")} == {"work/img/bg0.jpg", "work/img/bg1.jpg"}
    assert J.advance(store, job["id"])["status"] == "remote_render"
    RQ.complete(store, taken["id"], "pc", _tiny_mp4(env["tmp"] / "remote.mp4"))
    done = J.advance(store, job["id"])
    assert done["status"] == "done" and done["clip_id"], done["error"]
    assert "encode" not in env["make"].calls and "check" in env["make"].calls


def test_worker_side_unpacks_only_its_own_paths(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("mv_remote_test", M.tools_dir() / "remote.py")
    remote = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(remote)
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("../evil.txt", "x")
    with pytest.raises(ValueError, match="чужой путь"):
        remote.unpack(bad, tmp_path / "out")


# ---------------------------------------------------------------- план агента, инструменты, REST


def test_plan_review_waits_for_the_agent_and_changed_prompt_redraws(env, monkeypatch):
    from app.agent_tools import ToolContext, run_tool

    store, pid, track, fake = env["store"], env["pid"], env["track"], env["fake"]
    monkeypatch.setattr(settings, "aivideo_enabled", True)
    ctx = ToolContext(store=store, project_hint=pid)
    out, ok = run_tool(ctx, "music_video_create", {"project_id": pid, "track_id": track["id"], "start": 0, "end": 12,
                                                   "images": 2, "review_plan": True, "max_rub": 30})
    assert ok, out
    jid = out["job_id"]
    assert J.advance(store, jid)["status"] == "plan_review" and not _approvals(store, pid)
    got, ok = run_tool(ctx, "music_video_plan", {"job_id": jid})
    assert ok and got["words_total"] == len(WORDS) and got["words"][0].startswith("0:Мишка@")
    plan = got["plan"]
    plan["characters"] = {"bear": "big chubby brown cartoon bear"}
    plan["images"]["bear"] = {"kind": "cutout", "key": "green", "chars": ["bear"], "prompt": "the bear waves"}
    plan["images"]["bear_run"] = {"kind": "cutout", "refs": ["bear"], "chars": ["bear"], "prompt": "the bear runs"}
    bad, ok = run_tool(ctx, "music_video_plan", {"job_id": jid, "plan": {"images": {}}})
    assert not ok and "images" in bad["error"]
    saved, ok = run_tool(ctx, "music_video_plan", {"job_id": jid, "plan": plan})
    assert ok and set(saved["images"]) == {"bg0", "bg1", "bear", "bear_run"}
    assert M.get_job(store, jid)["status"] == "plan_review", "правка плана сама дальше не ведёт"
    res, ok = run_tool(ctx, "music_video_continue", {"job_id": jid})
    assert ok and res["job"]["status"] == "planned"
    est = J.estimate(store, M.get_job(store, jid))
    kinds = [i["ref_kind"] for i in est["items"]]
    assert kinds.index(M.POSE_KIND) == len(kinds) - 1, "поза — после основы (она ждёт исходную картинку)"
    assert J.advance(store, jid)["status"] == "awaiting_approval"
    A.decide(store, M.get_job(store, jid)["approval_id"], "approve", actor="ui")
    G.run_pending(store)
    G.run_pending(store)  # поза ждала основу — дорисовывается следующим проходом
    assert fake.posts == 4
    pose_job = store.db.query_one("SELECT params_json FROM aiv_gen_jobs WHERE slot = 'bear_run'")
    assert json.loads(pose_job["params_json"])["ref_roles"] == ["bear: исходная картинка персонажа"]

    # Промпт фона поменяли — эта картинка устарела, остальные готовы; новая карточка только на неё.
    plan = M.load_plan(M.get_job(store, jid))
    plan["images"]["bg1"]["prompt"] = "a sunny glade with tall pines"
    M.set_status(store, jid, "images_ready")
    J.set_plan(store, jid, plan)
    state = J.readiness(store, M.get_job(store, jid))
    assert state["missing"] == ["bg1"]
    est = J.estimate(store, M.get_job(store, jid))
    assert [i["slot"] for i in est["items"]] == ["bg1"]

    other = run_tool(ToolContext(store=store, project_hint=pid + 999), "music_video_status", {"job_id": jid})
    assert not other[1] and "другого проекта" in other[0]["error"]
    listed, ok = run_tool(ctx, "music_video_status", {"project_id": pid})
    assert ok and listed["jobs"][0]["id"] == jid


def test_rest_create_status_continue_cancel_and_files(env):
    client, store, pid, track = env["client"], env["store"], env["pid"], env["track"]
    bad = client.post(f"/api/projects/{pid}/music-video", json={"style": "comic"})
    assert bad.status_code == 400 and "нужна песня" in bad.json()["detail"]
    r = client.post(f"/api/projects/{pid}/music-video", json={"track_id": track["id"], "start": 0, "end": 12,
                                                              "style": "comic", "max_rub": 30, "images": 2})
    assert r.status_code == 200, r.text
    jid = r.json()["id"]
    assert r.json()["status"] == "new" and r.json()["status_label"] == "разбор песни"
    J.advance(store, jid)
    assert client.get(f"/api/music-video/{jid}").json()["status"] == "awaiting_approval"
    assert client.get(f"/api/music-video/{jid}/file/plan.json").status_code == 200
    assert client.get(f"/api/music-video/{jid}/file/app.sqlite").status_code == 404, "только файлы проверки"
    jobs = client.get(f"/api/projects/{pid}/music-video").json()["jobs"]
    assert [j["id"] for j in jobs] == [jid]
    card_id = jobs[0]["approval_id"]
    cancelled = client.post(f"/api/music-video/{jid}/cancel").json()
    assert cancelled["status"] == "cancelled" and A.get_approval(store, card_id)["status"] == "expired"
    resumed = client.post(f"/api/music-video/{jid}/continue", json={}).json()
    assert resumed["status"] == "planned", "отменённое продолжается с оценки: разбор и план уже есть"


def test_module_playbook_and_chat_group():
    from app import chat_tools, modules
    from app.project_chat_playbook import pick

    assert modules.module_of_tool("music_video_create") == "music_video"
    assert "music_video" in modules.dependents("ai_video")
    assert set(chat_tools.GROUPS["music_video"][1]) >= {"music_video_create", "music_video_status",
                                                        "music_video_plan", "music_video_continue"}
    assert pick("сделай клип на песню про медведя") == "music-video"
    assert pick("мувик под песню из боёв") == "frag-movie"
