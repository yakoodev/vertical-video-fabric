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
    assert out[3]["camera"] in ("face", "overhead"), "в объектив — лицом (рот — только на речи героя)"
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
              "speaker": "narrator"} for i in range(n)]
    picks[2] = {"n": 2, "action": "dance", "emotion": "calm", "camera": "face", "speaker": "narrator"}  # не из словаря
    fake = FakeGemini(picks)
    plan = B.plan_shots(LIFT, words, total, gemini=True, client=fake)
    schema = fake.payload["generationConfig"]["responseSchema"]["properties"]["shots"]["items"]["properties"]
    assert schema["action"]["enum"] == list(B.ACTIONS) and schema["camera"]["enum"] == list(B.CAMERAS)
    assert schema["emotion"]["enum"] == list(B.EMOTIONS) and "bell" in schema["button"]["enum"]
    assert schema["speaker"]["enum"] == ["narrator", "hero", "other"]
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
    assert B.WALK_CLIP in clips, "короткий путь — шагом, не склейкой"
    r = perf["root"]
    assert math.dist(r[int(3.0 * 30)], r[int(6.5 * 30)]) > 0.25
    steps = [math.dist(r[i], r[i + 1]) for i in range(len(r) - 1)]
    assert max(steps) < 0.03, "корень едет плавно, без телепорта внутри кадра"


# --------------------------------------------------------------------------- #
# Куски рендера: кэш и возобновление (Blender подменён)
# --------------------------------------------------------------------------- #

PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 80 + b"\x00\x00\x00\x00IEND\xaeB`\x82")
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 80 + b"\xff\xd9"


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
                ext = c.get("ext", "png")
                (d / B.frame_name(f, ext)).write_bytes(JPG if ext == "jpg" else PNG)
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
    monkeypatch.setattr(B, "video_flat", lambda path, *a, **k: False)
    encoded = []
    monkeypatch.setattr(B.subprocess, "run", lambda args, **kw: encoded.append(args) or Path(args[-1]).write_bytes(b"mp4"))
    FakeBlender.calls, FakeBlender.fail_after = [], 50
    cache = tmp_path / "cache"
    with pytest.raises(B.RenderError):
        B.render_local(job, tmp_path / "a.mp4", work=tmp_path / "w", cache=cache, workers=1)
    first = sum(len(v) for v in FakeBlender.calls[0]["todo"].values())
    assert first == 180
    FakeBlender.fail_after = None
    progress = []
    res = B.render_local(job, tmp_path / "a.mp4", work=tmp_path / "w", cache=cache, workers=1,
                         progress=lambda p, n: progress.append(p))
    second = sum(len(v) for v in FakeBlender.calls[1]["todo"].values())
    assert second == 130, "дорисованы только недостающие кадры"
    assert res["frames"] == 180 and res["rendered"] == 130 and res["cached"] == 50 and progress[-1] == 1.0
    # третий запуск — всё из кэша, Blender не нужен
    res3 = B.render_local(job, tmp_path / "b.mp4", work=tmp_path / "w2", cache=cache)
    assert len(FakeBlender.calls) == 2 and res3["rendered"] == 0
    # оборванный кадр рисуется заново (черновик — JPEG)
    some = next(cache.iterdir()) / B.frame_name(1, "jpg")
    some.write_bytes(JPG[:40])
    assert not B.frame_ok(some)
    png = tmp_path / "x.png"
    png.write_bytes(PNG)
    assert B.frame_ok(png)
    png.write_bytes(PNG[:-6])
    assert not B.frame_ok(png)


def test_presets_effects_by_shot_and_cache_per_preset(tmp_path, monkeypatch):
    job = _job()
    assert job["quality"] == B.DEFAULT_QUALITY == "fast"
    assert B.preset({"quality": "нет такого"})["name"] == B.DEFAULT_QUALITY
    perf = B.perform(job)
    fx = B.shot_fx(perf, "auto")
    by = {s["n"]: s for s in perf["shots"]}
    # луч фонарика — объём; спокойный план при свете — без объёма; шаг/рывок — размытие; лицо крупно — ГРИП
    beam = [n for n, s in by.items() if s["phone"] and perf["phone"][int((s["t0"] + s["t1"]) / 2 * 30)]["light"] > 0.5]
    assert beam and all(fx[n]["volume"] for n in beam)
    lit_calm = [n for n, s in by.items() if s["t1"] < (perf["blackout"] or 0) - 1.5 and s["action"] in ("look_camera",)]
    assert lit_calm and not any(fx[n]["volume"] for n in lit_calm)
    flinch = [n for n, s in by.items() if s["action"] == "flinch"]
    assert flinch and all(fx[n]["blur"] for n in flinch)
    assert all(fx[c["n"]]["dof"] == (c["kind"] in ("face", "detail")) for c in perf["cameras"])
    assert all(v == {"volume": False, "blur": False, "dof": False} for v in B.shot_fx(perf, "none").values())
    assert all(all(v.values()) for v in B.shot_fx(perf, "all").values())
    # кэш — по пресету: другой пресет — другие ключи, тот же — те же; формат кадров по пресету
    k_fast = B.chunk_keys(job, perf)
    assert [c["key"] for c in k_fast] == [c["key"] for c in B.chunk_keys(job, B.perform(job))]
    job_final = {**job, "quality": "final"}
    k_final = B.chunk_keys(job_final, B.perform(job_final))
    assert not {c["key"] for c in k_fast} & {c["key"] for c in k_final}
    assert {c["ext"] for c in k_fast} == {"jpg"} and {c["ext"] for c in k_final} == {"png"}
    assert B.QUALITY["fast720"]["upscale"] and B.QUALITY["draft"]["fx"] == "none"


def test_parallel_blender_processes_split_frames(tmp_path, monkeypatch):
    job = _job(quality="draft")
    monkeypatch.setattr(B, "find_blender", lambda explicit=None: "blender")
    monkeypatch.setattr(B, "scene_config", lambda job, work, cache, packs=None: {"cache": str(cache), "out_dir": str(work)})
    monkeypatch.setattr(B.subprocess, "Popen", FakeBlender)
    monkeypatch.setattr(B, "video_flat", lambda path, *a, **k: False)
    encoded = []
    monkeypatch.setattr(B.subprocess, "run", lambda args, **kw: encoded.append(args) or Path(args[-1]).write_bytes(b"mp4"))
    FakeBlender.calls, FakeBlender.fail_after = [], None
    res = B.render_local(job, tmp_path / "a.mp4", work=tmp_path / "w", cache=tmp_path / "cache")
    assert res["processes"] == 2 and len(FakeBlender.calls) == 2
    sizes = [sum(len(v) for v in c["todo"].values()) for c in FakeBlender.calls]
    assert sum(sizes) == res["frames"] and min(sizes) > res["frames"] * 0.3, "кадры поровну между процессами"
    keys0 = set(FakeBlender.calls[0]["todo"])
    assert not keys0 & set(FakeBlender.calls[1]["todo"]), "кусок рисует один процесс"
    assert any("f_%05d.jpg" in str(a) for a in encoded[-1])
    groups = B.split_chunks([{"key": "a"}, {"key": "b"}, {"key": "c"}], {"a": [1] * 10, "b": [1] * 6, "c": [1] * 5}, 2)
    sizes = {"a": 10, "b": 6, "c": 5}
    assert sorted(sum(sizes[c["key"]] for c in g) for g in groups) == [10, 11]


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
    # название и «ЧАСТЬ N+1» — не на лице героя (полосы лица из плана камер)
    from app import stories_3d as S3D

    faces = B.face_bands(B.perform(payload))
    for lid in ("st_title", "st_part"):
        lay = next(lay for lay in layers if lay["id"] == lid)
        a0, a1 = S3D.layer_box(lay)
        for f0, f1 in S3D.face_in(faces, lay["start"], lay["end"]):
            assert S3D._overlap(a0, a1, f0, f1) == 0.0, f"{lid} на лице: {a0:.2f}–{a1:.2f} vs {f0:.2f}–{f1:.2f}"


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


def test_flat_video_detected(tmp_path):
    """Однотонный выход (зелёная основа холста без слоёв, пустые кадры) — брак, а не «готово»."""
    green = tmp_path / "green.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x00ff00:s=108x192:d=2:r=10",
                    "-pix_fmt", "yuv420p", str(green)], check=True)
    real = tmp_path / "real.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=108x192:d=2:r=10",
                    "-pix_fmt", "yuv420p", str(real)], check=True)
    assert B.video_flat(green) and not B.video_flat(real)
    assert B.video_flat(tmp_path / "нет.mp4"), "нечитаемое — тоже брак"



# --------------------------------------------------------------------------- #
# Кто говорит: рот героя — только на его прямой речи
# --------------------------------------------------------------------------- #

def test_speech_labels_quotes_colon_and_dash():
    lab = B.speech_labels
    assert set(lab("Он сел в угол, подальше от дверей.")) == {"narrator"}
    assert lab("«Помогите!» — крикнул Даня.") == ["hero", "narrator", "narrator", "narrator"]
    assert lab("Голос за дверью сказал: спасатели, держитесь.")[-2:] == ["other", "other"]
    assert lab("Даня закричал: «Эй, я здесь!»")[2:] == ["hero", "hero", "hero"]
    assert lab("— Эй! — позвал он.") == ["narrator", "hero", "narrator", "narrator", "narrator"]
    assert set(lab("На табличке было написано «не курить».")) == {"narrator"}, "надпись — не реплика"
    lines = B.dialog_lines([{"text": "В 23:24 постучали."}, {"text": "«Кто там?» — спросил Даня."}])
    assert [ln["speaker"] for ln in lines] == ["narrator", "hero", "narrator"]
    assert not B.has_hero_lines(LIFT["sentences"]), "в «Лифте» реплик героя нет"


def test_no_mouth_on_narration_only_oh_on_startle():
    job = _job()
    assert job["speech"]["hero_windows"] == [] and not any(s["speaks"] for s in job["shots"])
    assert {s["speaker"] for s in job["shots"]} <= {"narrator", "other"}
    perf = B.perform(job)
    starts = [t for t, _ in perf["startles"]]
    for f, fc in enumerate(perf["face"]):
        if fc["mouth"][0] > 0.05:
            t = f / 30
            assert any(0 <= t - ts < 2.5 for ts in starts), f"рот открыт на {t:.2f} с без испуга"


def test_lipsync_only_on_hero_line():
    script = {"title": "Лифт", "sentences": [
        {"text": "Свет погас, и Даня остался один.", "role": "problem"},
        {"text": "«Помогите, я в лифте!» — крикнул Даня.", "role": "problem"},
        {"text": "Голос снаружи ответил: держись.", "role": "solution"}]}
    text = " ".join(x["text"] for x in script["sentences"])
    words = __import__("app.explainer_gen", fromlist=["x"]).proportional_words(text, 9.0)
    job = B.build_job(script, words, 10.0, gemini=False)
    win = job["speech"]["hero_windows"]
    hero_words = [w for w, lab in zip(job["words"], job["speech"]["labels"]) if lab == "hero"]
    assert [w["word"] for w in hero_words] == ["«Помогите,", "я", "в", "лифте!»"]
    assert len(win) == 1 and abs(win[0][0] - hero_words[0]["start"]) < 1e-3
    perf = B.perform(job)
    opened = [f / 30 for f, fc in enumerate(perf["face"]) if fc["mouth"][0] > 0.15]
    assert opened and all(win[0][0] - 0.15 <= t <= win[0][1] + 0.15 or
                          any(0 <= t - ts < 2.5 for ts, _ in perf["startles"]) for t in opened)
    assert any(s["speaker"] == "hero" and s["speaks"] for s in job["shots"])


def test_gemini_tts_dialog_two_voices():
    from app.dubbing import GeminiTTS, hero_voice_for

    tts = GeminiTTS(api_key="k", voice="Charon")
    lines = [{"speaker": "narrator", "text": "Свет погас."}, {"speaker": "hero", "text": "«Помогите!»"}]
    pl = tts.dialog_payload(lines, {"narrator": "Charon", "hero": hero_voice_for("Charon")})
    cfgs = pl["generationConfig"]["speechConfig"]["multiSpeakerVoiceConfig"]["speakerVoiceConfigs"]
    assert [c["speaker"] for c in cfgs] == ["Рассказчик", "Герой"]
    assert [c["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] for c in cfgs] == ["Charon", "Puck"]
    text = pl["contents"][0]["parts"][0]["text"]
    assert "Рассказчик: Свет погас." in text and "Герой: «Помогите!»" in text
    assert hero_voice_for("Puck") != "Puck"



# --------------------------------------------------------------------------- #
# Ходьба: корень по опорной стопе, 1–3 шага, разгон/торможение, поворот до шага
# --------------------------------------------------------------------------- #

def _walk_job():
    plan = [(0.0, 2.0, "wait", "center"), (2.0, 6.5, "approach", "panel"), (6.5, 11.0, "approach", "doors"),
            (11.0, 14.0, "listen", "doors"), (14.0, 18.5, "approach", "center")]
    shots = [{"n": n, "t0": a, "t1": b, "action": act, "target": tg, "button": "1", "emotion": "calm",
              "camera": "overhead", "speaks": False, "stand_first": False} for n, (a, b, act, tg) in enumerate(plan)]
    return {"fps": 30, "duration": 18.5, "shots": shots, "events": [], "words": []}


def test_walk_feet_do_not_slide():
    perf = B.perform(_walk_job())
    walks = [sg for sg in perf["track"] if sg["clip"] == B.WALK_CLIP]
    assert len(walks) >= 2, "короткие подходы — шагом (в лифте 1–3 шага), длинные с разворотом — склейкой"
    for sg in walks:
        steps = (sg["end"] - sg["start"] - (1 - 0.45) * sg["ease_in"]) / (B.CLIP_LEN[B.WALK_CLIP] / 2)
        assert steps <= B.MAX_WALK_STEPS + 2.05, "1–3 шага (+ шаги разворота)"
        assert sg["ease_in"] > 0 and sg["ease_out"] > 0, "разгон и торможение"
    slide = B.foot_slide(perf)
    assert len(slide) > 100 and max(slide) < 10.0, f"стопа в контакте скользит {max(slide):.1f} мм/кадр"
    r = perf["root"]
    assert math.dist(r[int(6.4 * 30)], B.press_stand(["1"])[0]) < 0.01, "пришёл к панели"
    cuts = {int(round(sh["t0"] * 30)) - 1 for sh in perf["shots"]}   # на склейке герой может «переставиться»
    assert max(math.dist(r[i], r[i + 1]) for i in range(len(r) - 1) if i not in cuts) < 0.03, "корень без рывков"
    # курс: сначала поворот к цели (на месте или на первом шаге), идёт лицом вперёд
    f = int(3.5 * 30)
    move = (r[f + 3][0] - r[f][0], r[f + 3][1] - r[f][1])
    fx, fy = B.facing(perf["heading"][f])
    assert (move[0] * fx + move[1] * fy) / max(math.hypot(*move), 1e-9) > 0.85, "идёт лицом по ходу, не боком"


def test_constant_root_speed_would_slide():
    """Постоянная скорость корня под клип шага (как было) — опорная стопа едет на миллиметры–сантиметр за кадр."""
    const = sum(B.WALK_STANCE) / len(B.WALK_STANCE)
    assert max(abs(x - const) * B.S * 1000 for x in B.WALK_STANCE) > 5.0


def test_clip_ease_slows_start_and_end():
    sg = B.m.ClipSegment("Walking_C", 1.0, 1.6, ease_in=0.4, ease_out=0.4, end=3.0)
    assert sg.clip_seconds(1.0) == 0.0
    assert sg.clip_seconds(1.1) < 0.1 * 0.8, "на входе медленнее"
    assert abs((sg.clip_seconds(2.0) - sg.clip_seconds(1.9)) - 0.1) < 1e-6, "в середине — обычная скорость"
    assert sg.clip_seconds(3.0) - sg.clip_seconds(2.9) < 0.1 * 0.8, "на выходе медленнее"



def test_titles_move_off_the_face():
    from app import stories_3d as S3D

    faces = [{"n": 0, "t0": 0.0, "t1": 3.0, "y": 0.30, "h": 0.30}, {"n": 9, "t0": 40.0, "t1": 45.0, "y": 0.45, "h": 0.3}]
    title = {"id": "st_title", "type": "text", "start": 0.0, "end": 1.6, "y": 0.34,
             "props": {"text": "Если блэкаут застал вас в лифте", "size": 92, "maxWidth": 940, "lineHeight": 1.05,
                       "bg": {"padY": 14}}}
    part_t = {"id": "st_title_part", "type": "text", "start": 0.0, "end": 1.6, "y": 0.41, "props": {"text": "ЧАСТЬ 1", "size": 70}}
    part = {"id": "st_part", "type": "text", "start": 43.0, "end": 45.0, "y": 0.45,
            "props": {"text": "ЧАСТЬ 2 — КТО СТУЧАЛ", "size": 84, "maxWidth": 840, "lineHeight": 1.05, "bg": {"padY": 16}}}
    layers = [title, part_t, part]
    notes = S3D.place_titles(layers, faces, 0.74, "st_")
    for lay in (title, part):
        a0, a1 = S3D.layer_box(lay)
        for f0, f1 in S3D.face_in(faces, lay["start"], lay["end"]):
            assert S3D._overlap(a0, a1, f0, f1) == 0.0
    assert abs(part_t["y"] - title["y"] - 0.07) < 1e-6, "«ЧАСТЬ N» едет вместе с названием"
    assert S3D._overlap(*S3D.layer_box(title), 0.69, 0.79) == 0.0, "название не на субтитрах"
    assert notes == []
    # первый и последний планы — камера оставляет место под титры
    perf = B.perform(_job())
    cams = perf["cameras"]
    assert cams[0]["face_y"] > B.TITLE_Y + 0.15 and cams[-1]["face_y"] < B.PART_Y - 0.15
