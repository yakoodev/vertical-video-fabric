"""3D-серия в Blender (app/series3d_blender.py, app/render_queue.py): режиссура по фразам (Gemini по схеме —
подделка клиента, правила — запасной путь), выполнимость и монтаж планов, лип-синк по виземам, покадровое
исполнение, куски рендера с кэшем и возобновлением (Blender подменён), очередь хост-воркера и ручки, эпизод
«Историй» с renderer=blender: ждёт рендер → воркер отдал mp4 → клип собран. Без Blender и сети."""

from __future__ import annotations

import io
import json
import math
import subprocess
from pathlib import Path

import pytest

from app import render_queue as RQ
from app import series3d_blender as B
from app import stories as S
from app import stories_gen as X
from test_series3d import LIFT, _words
from test_stories import env  # noqa: F401 - фикстура
from test_stories import _setup

TOTAL = 52.0


def _job(**kw) -> dict:
    words = _words()
    return B.build_job(LIFT, words, float(words[-1]["end"]) + 2.0, gemini=False, **kw)


# --------------------------------------------------------------------------- #
# Режиссура
# --------------------------------------------------------------------------- #

def test_rules_pick_actions_from_words():
    def pick(text, role="problem", kinds=()):
        return B.rule_choice({"text": text, "role": role}, [{"kind": k} for k in kinds], dark=False)

    assert pick("Он нажал кнопку 7")["action"] == "press_button"
    assert pick("Он нажал кнопку 7")["button"] == "7"
    assert pick("а кнопка вызова диспетчера молчит")["button"] == "bell"
    assert pick("не жмите все кнопки подряд", "hook")["button"] == "all"
    assert pick("он раздвинул двери", "mistake")["action"] == "pry_doors"
    assert pick("в двери снаружи трижды постучали")["action"] == "listen"
    assert pick("он стучит в двери")["action"] == "knock"
    assert pick("Он сел в угол")["action"] == "sit_corner"
    assert pick("включил фонарик телефона")["action"] == "phone_light"
    assert pick("свет погас", kinds=("blackout",))["emotion"] == "startled"
    assert pick("Выжить просто, если не совершить одну ошибку", "hook")["action"] == "look_camera"
    for text in ("кричит: помогите", "табло горело", "сверху что-то заскрежетало"):
        c = pick(text)
        assert c["action"] in B.ACTIONS and c["emotion"] in B.EMOTIONS and c["camera"] in B.CAMERAS


def test_fix_sequence_feasible_and_no_repeated_plan():
    shots = [{"n": i, "action": a, "camera": c, "emotion": "calm", "speaks": False}
             for i, (a, c) in enumerate([("sit_corner", "face"), ("press_button", "face"), ("stand_up", "face"),
                                         ("look_camera", "ots"), ("wait", "face"), ("wait", "face")])]
    out = B.fix_sequence(shots)
    assert out[1]["stand_first"], "сидя к панели — сначала встать"
    assert out[2]["action"] == "wait", "уже стоит — «встать» не играть"
    assert out[3]["speaks"] and out[3]["camera"] in ("face", "overhead"), "в объектив — лицом и с лип-синком"
    assert all(a["camera"] != b["camera"] for a, b in zip(out, out[1:])), "один план подряд"
    assert out[1]["target"] == "panel"


class FakeGemini:
    def __init__(self, shots):
        self.payload = None
        self.shots = shots

    def generate_content(self, model, payload):
        self.payload = payload
        text = json.dumps({"shots": self.shots}, ensure_ascii=False)
        return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def test_gemini_choice_by_schema_with_rules_fallback():
    words = _words()
    total = float(words[-1]["end"]) + 2.0
    rules = B.plan_shots(LIFT, words, total, gemini=False)
    n = len(rules["shots"])
    picks = [{"n": i, "action": "listen", "target": "doors", "button": "1", "emotion": "scared", "camera": "face",
              "speaks": False} for i in range(n)]
    picks[2] = {"n": 2, "action": "dance", "emotion": "calm", "camera": "face", "speaks": False}  # не из словаря
    fake = FakeGemini(picks)
    plan = B.plan_shots(LIFT, words, total, gemini=True, client=fake)
    schema = fake.payload["generationConfig"]["responseSchema"]["properties"]["shots"]["items"]["properties"]
    assert schema["action"]["enum"] == list(B.ACTIONS) and schema["camera"]["enum"] == list(B.CAMERAS)
    assert schema["emotion"]["enum"] == list(B.EMOTIONS) and "bell" in schema["button"]["enum"]
    assert plan["director"] == "gemini"
    assert plan["shots"][0]["by"] == "gemini" and plan["shots"][2]["by"] == "rules", "неверный ответ — правила"
    assert all(a["camera"] != b["camera"] for a, b in zip(plan["shots"], plan["shots"][1:]))

    class Broken:
        def generate_content(self, *a):
            raise RuntimeError("429 quota")

    plan2 = B.plan_shots(LIFT, words, total, gemini=True, client=Broken())
    assert plan2["director"] == "rules" and "quota" in plan2["director_error"]
    assert [s["action"] for s in plan2["shots"]] == [s["action"] for s in rules["shots"]]


# --------------------------------------------------------------------------- #
# Лип-синк
# --------------------------------------------------------------------------- #

def test_lipsync_by_word_timings_and_visemes():
    words = [{"word": "мама", "start": 0.5, "end": 1.0}, {"word": "у", "start": 1.6, "end": 1.9},
             {"word": "ищи", "start": 2.5, "end": 2.9}]
    lip = B.lipsync(words, 120, 30)
    o = [x[0] for x in lip]
    assert max(o[:12]) < 0.05, "до речи рот закрыт"
    assert max(o[15:30]) > 0.5, "«а» — широко открыт"
    assert max(o[33:46]) < 0.12, "пауза между словами — закрыт"
    u = lip[int(1.8 * 30)]
    assert u[1] > 0.6 and u[0] < 0.6, "«у» — округлённо и неширoко"
    i_ = lip[int(2.75 * 30)]
    assert i_[2] > 0.6 and i_[1] < 0.2, "«и» — в ширину"
    # «м»/«б»/«п» смыкают губы внутри слова
    mm = B.viseme_keys([{"word": "мама", "start": 0.0, "end": 0.6}])
    assert mm[0][2][0] == 0.0 and mm[1][2][0] == 1.0
    # окна: вне реплики рот не двигается; огибающая — тише → меньше
    gated = B.lipsync(words, 120, 30, windows=[(2.4, 3.0)])
    assert max(x[0] for x in gated[:60]) < 0.02 and max(x[0] for x in gated[72:90]) > 0.25
    quiet = B.lipsync(words, 120, 30, envelope=[0.05] * 60 + [1.0] * 60)
    assert max(x[0] for x in quiet[15:30]) < max(o[15:30]) * 0.7
    assert B.viseme_keys([{"word": "22", "start": 0, "end": 0.5}]), "цифры читаются словами"


# --------------------------------------------------------------------------- #
# Исполнение и камера
# --------------------------------------------------------------------------- #

def test_perform_tracks_frames_hands_phone_and_cameras():
    job = _job()
    perf = B.perform(job)
    nf = int(round(job["duration"] * 30))
    assert perf["frames"] == nf and len(perf["root"]) == nf and len(perf["face"]) == nf and len(perf["lights"]) == nf
    assert B.perform(job)["root"] == perf["root"], "детерминированно"
    # нажатие: кисть у кнопки в момент касания
    press = [c for c in perf["contacts"] if c["kind"] == "press"]
    assert press
    for c in press:
        f = int(round(c["t"] * 30))
        h = perf["hands"]["r"][f]
        assert h and h[1] > 0.9 and math.dist(h[0], c["point"]) < 0.02
        assert B.button_pos(c["button"])[2] <= B.MAX_PRESS_Z + 1e-6, "кнопка в досягаемости"
    # блэкаут: свет гаснет, потом аварийный красный; табло — новая цифра в финале
    bo = perf["blackout"]
    assert bo is not None
    assert perf["lights"][int((bo - 1.5) * 30)]["main"] == 1.0 and perf["lights"][int((bo + 0.2) * 30)]["main"] == 0.0
    assert perf["lights"][int((bo + 1.5) * 30)]["red"] > 0.5
    assert perf["lights"][-1]["floor"] == "23" and perf["lights"][0]["floor"] == "22"
    # телефон: появился с «фонариком», рука держит его, пока виден
    on = [i for i, p in enumerate(perf["phone"]) if p["on"]]
    assert on and all(perf["hands"]["l"][i] for i in on[5:-5] if perf["hands"]["l"][i] is None or True)
    assert max(p["light"] for p in perf["phone"]) == 1.0
    # камеры: внутри кабины, план не повторяется подряд, лицо — по плану кадра
    cams = perf["cameras"]
    assert len(cams) == len(perf["shots"])
    assert all(B.cam_inside(c["pos_a"]) for c in cams)
    assert all(a["kind"] != b["kind"] for a, b in zip(cams, cams[1:]))
    # сидит в углу — корень в углу, голова ниже
    sit = next(s for s in perf["shots"] if s["action"] == "sit_corner")
    assert sit["posture_end"] == "sit" and sit["spot"][0] > 0.3
    # мимика в пределах
    assert all(-1.01 <= f["brow"] <= 1.01 and 0 <= f["blink"] <= 1 and 0 <= f["mouth"][0] <= 1 for f in perf["face"])


def test_turn_in_place_steps_and_walk_moves_root():
    shots = [{"n": 0, "t0": 0.0, "t1": 3.0, "action": "wait", "camera": "overhead", "emotion": "calm", "speaks": False},
             {"n": 1, "t0": 3.0, "t1": 7.0, "action": "approach", "target": "display", "camera": "ots", "emotion": "calm",
              "speaks": False},
             {"n": 2, "t0": 7.0, "t1": 10.0, "action": "listen", "camera": "face", "emotion": "calm", "speaks": False}]
    job = {"fps": 30, "duration": 10.0, "shots": B.fix_sequence(shots), "events": [], "words": []}
    perf = B.perform(job)
    clips = [sg["clip"] for sg in perf["track"]]
    assert "Walking_A" in clips, "короткий путь — шагом, не склейкой"
    r = perf["root"]
    assert math.dist(r[int(3.0 * 30)], r[int(6.5 * 30)]) > 0.25
    steps = [math.dist(r[i], r[i + 1]) for i in range(len(r) - 1)]
    assert max(steps) < 0.03, "корень едет плавно, без телепорта внутри кадра"


# --------------------------------------------------------------------------- #
# Куски рендера: кэш и возобновление (Blender подменён)
# --------------------------------------------------------------------------- #

PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 80 + b"\x00\x00\x00\x00IEND\xaeB`\x82")


def test_chunk_keys_change_only_for_edited_shot():
    job = _job()
    perf = B.perform(job)
    keys = B.chunk_keys(job, perf)
    assert [c["n"] for c in keys] == [s["n"] for s in perf["shots"]]
    assert keys[0]["f0"] == 1 and keys[-1]["f1"] == perf["frames"]
    assert all(a["f1"] + 1 == b["f0"] for a, b in zip(keys, keys[1:])), "куски встык"
    job2 = json.loads(json.dumps(job))
    last = job2["shots"][-1]
    last["emotion"] = "relieved" if last["emotion"] != "relieved" else "calm"
    keys2 = B.chunk_keys(job2, B.perform(job2))
    changed = [a["n"] for a, b in zip(keys, keys2) if a["key"] != b["key"]]
    assert changed and changed[-1] == last["n"] and len(changed) <= 2


class FakeBlender:
    """Вместо Blender: пишет PNG недостающих кадров из config.json и печатает PROGRESS; ``fail_after`` — упасть."""

    calls: list[dict] = []
    fail_after: int | None = None

    def __init__(self, args, stdout=None, stderr=None, text=None, encoding=None, errors=None):
        cfg = json.loads(Path(args[args.index("--config") + 1]).read_text("utf-8"))
        FakeBlender.calls.append(cfg)
        lines, n, self.code = [], 0, 0
        total = sum(len(v) for v in cfg["todo"].values())
        for c in cfg["chunks"]:
            for f in cfg["todo"].get(c["key"], []):
                if FakeBlender.fail_after is not None and n >= FakeBlender.fail_after:
                    self.code = 1
                    break
                d = Path(cfg["cache"]) / c["key"]
                d.mkdir(parents=True, exist_ok=True)
                (d / B.frame_name(f)).write_bytes(PNG)
                n += 1
                lines.append(f"PROGRESS {n} {total}\n")
        Path(cfg["out_dir"], "report.json").write_text("{}", "utf-8")
        self.stdout = io.StringIO("".join(lines))

    def wait(self):
        return self.code

    def kill(self):
        pass


def test_render_resumes_from_cache(tmp_path, monkeypatch):
    job = _job(quality="draft")
    job["duration"] = 6.0
    job["shots"] = [s for s in job["shots"] if s["t0"] < 6.0]
    job["shots"][-1]["t1"] = 6.0
    monkeypatch.setattr(B, "find_blender", lambda explicit=None: "blender")
    monkeypatch.setattr(B, "scene_config", lambda job, work, cache, packs=None: {"cache": str(cache), "out_dir": str(work)})
    monkeypatch.setattr(B.subprocess, "Popen", FakeBlender)
    encoded = []
    monkeypatch.setattr(B.subprocess, "run", lambda args, **kw: encoded.append(args) or Path(args[-1]).write_bytes(b"mp4"))
    FakeBlender.calls, FakeBlender.fail_after = [], 50
    cache = tmp_path / "cache"
    with pytest.raises(B.RenderError):
        B.render_local(job, tmp_path / "a.mp4", work=tmp_path / "w", cache=cache)
    first = sum(len(v) for v in FakeBlender.calls[0]["todo"].values())
    assert first == 180
    FakeBlender.fail_after = None
    progress = []
    res = B.render_local(job, tmp_path / "a.mp4", work=tmp_path / "w", cache=cache, progress=lambda p, n: progress.append(p))
    second = sum(len(v) for v in FakeBlender.calls[1]["todo"].values())
    assert second == 130, "дорисованы только недостающие кадры"
    assert res["frames"] == 180 and res["rendered"] == 130 and res["cached"] == 50 and progress[-1] == 1.0
    # третий запуск — всё из кэша, Blender не нужен
    res3 = B.render_local(job, tmp_path / "b.mp4", work=tmp_path / "w2", cache=cache)
    assert len(FakeBlender.calls) == 2 and res3["rendered"] == 0
    # оборванный PNG рисуется заново
    some = next(cache.iterdir()) / B.frame_name(1)
    some.write_bytes(PNG[:40])
    assert not B.frame_ok(some)


# --------------------------------------------------------------------------- #
# Очередь хост-воркера
# --------------------------------------------------------------------------- #

@pytest.fixture()
def qstore(tmp_path, monkeypatch):
    from app.crypto import CookieCipher
    from app.db import Database
    from app.settings import settings
    from app.store import AppStore

    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    store = AppStore(Database(tmp_path / "data" / "app.sqlite"), CookieCipher(tmp_path / "secret.key"))
    store.db.init()
    return store


def _mp4(path: Path) -> Path:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=#203040:s=108x192:d=2:r=10",
                    "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def test_queue_claim_heartbeat_complete_and_stale(qstore, tmp_path, monkeypatch):
    j = RQ.enqueue(qstore, {"x": 1}, key="k1", episode_id=5, title="серия")
    assert RQ.enqueue(qstore, {"x": 2}, key="k1")["id"] == j["id"], "то же задание дважды не ставится"
    assert not RQ.worker_online(qstore)
    label, note = RQ.wait_label(qstore, j)
    assert "ждёт компьютер" in label and "render_worker.py" in note
    got = RQ.claim(qstore, "pc", {"gpu": "4070"})
    assert got["id"] == j["id"] and got["status"] == "running" and got["payload"] == {"x": 1}
    assert RQ.claim(qstore, "pc2") is None, "одно задание — одному воркеру"
    assert RQ.worker_online(qstore)
    assert RQ.heartbeat(qstore, j["id"], "pc", 0.42, "кадр 10/20") == {"status": "running", "stop": False}
    assert RQ.wait_label(qstore, RQ.get(qstore, j["id"]))[0] == "рендер на компьютере… 42%"
    assert RQ.heartbeat(qstore, j["id"], "чужой", 0.9)["stop"]
    with pytest.raises(RQ.QueueError):
        RQ.complete(qstore, j["id"], "чужой", tmp_path / "x.mp4")
    # пульса нет — снова в очереди
    monkeypatch.setattr(RQ, "_now", lambda: 1e12)
    assert RQ.requeue_stale(qstore) == 1 and RQ.get(qstore, j["id"])["status"] == "queued"
    monkeypatch.undo()
    from app.settings import settings

    settings.data_dir = tmp_path / "data"
    again = RQ.claim(qstore, "pc")
    assert again["attempts"] == 2
    woke = []
    RQ.on_done(lambda: woke.append(1))
    done = RQ.complete(qstore, j["id"], "pc", _mp4(tmp_path / "x.mp4"), {"seconds": 12.5})
    assert done["status"] == "done" and RQ.result_file(done) and done["result"]["seconds"] == 12.5 and woke
    # упало с повтором → в очередь; без — failed; отмена останавливает воркера
    j2 = RQ.enqueue(qstore, {}, key="k2")
    RQ.claim(qstore, "pc")
    assert RQ.fail(qstore, j2["id"], "pc", "OSError", retry=True)["status"] == "queued"
    RQ.claim(qstore, "pc")
    assert RQ.fail(qstore, j2["id"], "pc", "сцена упала")["status"] == "failed"
    assert RQ.enqueue(qstore, {}, key="k2")["id"] != j2["id"], "упавшее ставится заново"
    j3 = RQ.enqueue(qstore, {}, key="k3")
    RQ.claim(qstore, "pc")
    RQ.cancel(qstore, j3["id"])
    assert RQ.heartbeat(qstore, j3["id"], "pc", 0.5)["stop"]


def test_render3d_api_with_bearer_token(env, tmp_path):  # noqa: F811
    client, store, _tmp = env
    job = RQ.enqueue(store, {"duration": 1}, key="api1", title="api")
    r = client.post("/api/render3d/claim", json={"worker": "pc", "info": {"blender": "4.5"}})
    assert r.status_code == 200 and r.json()["job"]["id"] == job["id"]
    assert client.post("/api/render3d/claim", json={"worker": "pc"}).json()["job"] is None
    hb = client.post(f"/api/render3d/jobs/{job['id']}/heartbeat", json={"worker": "pc", "progress": 0.5, "note": "кадр"})
    assert hb.json() == {"status": "running", "stop": False}
    st = client.get("/api/render3d/status").json()
    assert st["online"] and st["counts"]["running"] == 1 and st["workers"][0]["info"]["blender"] == "4.5"
    mp4 = _mp4(tmp_path / "r.mp4")
    with mp4.open("rb") as fh:
        r = client.post(f"/api/render3d/jobs/{job['id']}/complete", files={"file": ("r.mp4", fh, "video/mp4")},
                        data={"worker": "pc", "result": json.dumps({"frames": 20})})
    assert r.status_code == 200 and r.json()["status"] == "done" and r.json()["result"]["frames"] == 20
    assert client.post(f"/api/render3d/jobs/{job['id']}/fail", json={"worker": "pc"}).status_code == 409
    assert [j["id"] for j in client.get("/api/render3d/jobs").json()["jobs"]] == [job["id"]]


def test_route_requires_token(env, monkeypatch):  # noqa: F811
    client, _store, _tmp = env
    from app.settings import settings

    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "ensure_api_token", lambda: "secret-token")
    assert client.post("/api/render3d/claim", json={}).status_code == 401
    ok = client.post("/api/render3d/claim", json={"worker": "pc"}, headers={"Authorization": "Bearer secret-token"})
    assert ok.status_code == 200


# --------------------------------------------------------------------------- #
# «Истории»: 3D-серия в Blender ждёт хост-воркер
# --------------------------------------------------------------------------- #

def test_story_episode_waits_for_host_render(env, tmp_path, monkeypatch):  # noqa: F811
    _client, store, tmp = env
    monkeypatch.delenv("SERIES3D_BLENDER_LOCAL", raising=False)
    ids = _setup(store, tmp, music=False, sounds=False)
    S.save_world(store, world_id=ids["world_id"], format=S.FORMAT_DISASTER)
    with pytest.raises(ValueError):
        S.create_episode(store, ids["world_id"], "лифт", options={"engine": "3d", "renderer": "unreal"})
    ep = X.create(store, ids["world_id"], "блэкаут в лифте", None, script=LIFT,
                  options={"engine": "3d", "renderer": "blender"}, run=True)
    assert ep["status"] == "assembling", (ep["error"], ep.get("notes"))
    assert ep["status_label"] == "рендер: ждёт компьютер с Blender"
    assert any("render_worker.py" in str(n) for n in ep["notes"])
    job = RQ.claim(store, "pc")
    payload = job["payload"]
    assert payload["director"] == "rules" and payload["shots"] and payload["words"]
    assert {s["action"] for s in payload["shots"]} <= set(B.ACTIONS)
    RQ.heartbeat(store, job["id"], "pc", 0.42, "кадр 640/1520")
    ep = X.advance(store, int(ep["id"]))
    assert ep["status"] == "assembling" and ep["status_label"] == "рендер на компьютере… 42%"
    assert S.list_episodes(store, world_id=ids["world_id"])[0]["status_label"] == "рендер на компьютере… 42%"
    # ещё проход ожидания — то же задание, не новое
    X.advance(store, int(ep["id"]))
    assert len(RQ.list_jobs(store)) == 1
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"color=c=#203040:s=108x192:d={payload['duration']}:r=10", "-pix_fmt", "yuv420p",
                    str(tmp_path / "series.mp4")], check=True)
    RQ.complete(store, job["id"], "pc", tmp_path / "series.mp4", {"seconds": 900.0, "frames": 1500})
    ep = X.advance(store, int(ep["id"]))
    assert ep["status"] == "done", (ep["error"], ep.get("notes"))
    tl = ep["timeline"]
    assert tl["scene3d"]["renderer"] == "blender" and tl["scene3d"]["render_sec"] == 900.0
    assert "render3d" not in tl
    layers = store.get_clip_plan(ep["clip_id"])["render_settings"]["layers"]
    assert [lay for lay in layers if lay["type"] == "video"]
    sfx = [lay for lay in layers if lay["type"] == "audio" and lay["props"].get("role") == "sfx"]
    assert sfx, "звуки событий и нажатий"
    assert any(lay["type"] == "captions" for lay in layers)


def test_story_failed_host_render_then_continue(env, tmp_path, monkeypatch):  # noqa: F811
    _client, store, tmp = env
    monkeypatch.delenv("SERIES3D_BLENDER_LOCAL", raising=False)
    ids = _setup(store, tmp, music=False, sounds=False)
    ep = X.create(store, ids["world_id"], "блэкаут", None, script=LIFT, options={"engine": "3d", "renderer": "blender"},
                  run=True)
    job = RQ.claim(store, "pc")
    RQ.fail(store, job["id"], "pc", "Blender упал")
    ep = X.advance(store, int(ep["id"]))
    assert ep["status"] == "failed" and "Blender упал" in ep["error"]
    ep = X.resume(store, int(ep["id"]))
    assert ep["status"] == "assembling"
    jobs = RQ.list_jobs(store)
    assert len(jobs) == 2 and jobs[0]["status"] == "queued", "«Продолжить» ставит рендер заново"
