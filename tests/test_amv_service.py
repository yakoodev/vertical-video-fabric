"""AMV как работа сервиса: приоритеты референса в плане (tools/amv/style.py), рендер-помощники, карточка «Новое видео»
(app/amv.py), инструменты агента (app/amv_tools.py) и задания (app/amv_job.py).

Подпроцессы инструмента (скан исходников, рендер) подменены: сканы и рендер — синтетика; разбор песни настоящий
(щелчки в wav), клип регистрируется из крошечного mp4 ffmpeg."""

from __future__ import annotations

import json
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

from tools.amv import library, plan, render, style


# ----------------------------------------------------------------------------- синтетика
def make_song(bpm=120.0, dur=60.0, levels=None, drops=(), dip=None):
    p = 60 / bpm
    beats = list(np.round(np.arange(0.1, dur, p), 4))
    nb = len(beats) // 4
    lv = levels or [2] * nb
    bars = [{"t": beats[4 * i], "beat0": 4 * i, "level": lv[min(i, len(lv) - 1)],
             "end": beats[min(len(beats) - 1, 4 * i + 4)], "energy": 0.1 * lv[min(i, len(lv) - 1)]}
            for i in range(nb)]
    rms = [-12.0] * int(dur / 0.05)
    if dip:
        for k in range(int(dip[0] / 0.05), int(dip[1] / 0.05)):
            rms[k] = -30.0
    return {"beats": beats, "period": p, "bpm": bpm, "bars": bars, "duration": dur, "drops": list(drops),
            "onsets": [(b, 0.6 if k % 4 == 0 else 0.4) for k, b in enumerate(beats)],
            "kicks": [(b, 0.7) for b in beats[::2]], "rms_db": rms}


def clip(id_, film, t_in, dur=3.0, lab=(50, 0, 0), char=0.8, energy=0.6, impacts=(), shot=None, act=None):
    return {"id": id_, "film": film, "src": film + ".mp4", "t_in": t_in, "t_out": t_in + dur, "dur": dur,
            "t_peak": t_in + dur / 2, "score": 0.6, "energy": energy, "impact": 0.5, "char": char,
            "dir_in": (0, 0), "dir_out": (0, 0), "lab": list(lab), "impacts": list(impacts),
            "shot": shot or f"{film}:{int(t_in * 10)}", "act": act}


def library_two_colours(n=60):
    """Красные и синие кадры вперемешку по времени источника, плюс длинные планы (окна одного плана)."""
    out = []
    for i in range(n):
        lab = (45, 55, 35) if i % 2 else (45, 10, -45)
        out.append(clip(f"A:{i}:0", "A", 10.0 * i, lab=lab, impacts=(10.0 * i + 1.2,) if i % 5 == 0 else ()))
    for i in range(12):                      # длинные планы: по 3 окна на план
        for w in range(3):
            out.append(clip(f"B:{i}:{w}", "B", 1000 + 20.0 * i + 2.4 * w, lab=(60, 0, 20), shot=f"B:{i}"))
    return out


# ----------------------------------------------------------------------------- style: части, темп, вдохи
def test_sections_calm_story_loud_action_last_drop_climax_and_explicit_modes():
    song = make_song(levels=[0] * 5 + [3] * 5 + [1] * 5 + [3] * 15, drops=(10.1, 40.1))
    secs = style.sections(song, 0.0, 60.0)
    modes = [(s["mode"], round(s["t0"])) for s in secs]
    assert modes[0] == ("story", 0) and ("climax", 40) in modes
    assert all(a["t1"] == b["t0"] for a, b in zip(secs, secs[1:])) and secs[-1]["t1"] == 60.0
    beats = np.array(song["beats"])
    assert all(np.min(np.abs(beats - s["t0"])) < 1e-6 for s in secs[1:])  # границы на долях
    told = style.sections(song, 0.0, 60.0, story=[{"t0": 0, "t1": 20, "acts": ["meet"], "mode": "action",
                                                   "colour": "red"}])
    assert told[0]["mode"] == "action" and told[0]["acts"] == ["meet"] and told[0]["colour"] == "red"


def test_mode_grid_gives_pacing_contrast_and_half_time_view():
    period = 0.5
    story_cpb, story_gap = style.mode_grid("story", period)
    act_cpb, act_gap = style.mode_grid("action", period)
    assert story_cpb[0] == 1 and story_gap[0] >= 6.0          # раз в 2+ такта
    assert act_cpb[3] >= 2 and act_gap[3] <= 1.6
    fast = make_song(bpm=188.0, dur=30.0)
    view = style.musical(fast)
    assert view["half_time"] and view["bpm"] == pytest.approx(94.0) and len(view["beats"]) == len(fast["beats"][::2])
    assert len(view["bars"]) == len(fast["bars"]) // 2
    assert style.musical(make_song(bpm=120.0)) is not None and not style.musical(make_song(bpm=120.0)).get("half_time")


def test_black_breath_before_a_drop_and_a_blink_before_the_last_hit():
    song = make_song(drops=(20.1, 44.1), dip=(18.5, 20.0))
    blacks = style.black_breaths(song, 0.0, 60.0)
    breath = [b for b in blacks if b["kind"] == "breath"]
    assert len(breath) == 1 and breath[0]["t1"] == pytest.approx(20.1) and 18.4 <= breath[0]["t0"] <= 18.6
    blink = [b for b in blacks if b["kind"] == "blink"]
    assert len(blink) == 1 and blink[0]["t1"] == pytest.approx(44.1) and blink[0]["fade"] == 0.0
    # рендер: чернота нарастает за fade, держится до удара; моргание — один кадр в ноль
    assert render.black_factor(18.45, blacks) == 1.0
    assert 0.0 < render.black_factor(breath[0]["t0"] + 0.05, blacks) < 1.0
    assert render.black_factor(19.9, blacks) == 0.0 and render.black_factor(20.15, blacks) == 1.0
    assert render.black_factor(44.09, blacks) == 0.0


# ----------------------------------------------------------------------------- style: план
def test_reference_plan_native_speed_restraint_strobe_and_story_dissolves():
    song = make_song(levels=[0] * 4 + [2] * 6 + [3] * 5, dur=30.0, drops=(8.1, 20.1))
    p = plan.build_plan(song, library_two_colours(), 0.0, 30.0, style="reference")
    segs = p["segments"]
    assert p["style"] == "reference" and p["whips"] == [] and not any(s["clip"] is None for s in segs)
    st = style.stats(p, song)
    assert st["native_share"] >= 0.6 and st["flashes_per_min"] <= 6 and st["shake_frac"] <= 0.12
    names = {c["name"] for c in st["checks"]}
    assert {"colour_jump", "native_share", "shake_frac", "window_cpm_p90"} <= names
    strobe = [s for s in segs if s.get("strobe")]
    assert 3 <= len(strobe) <= 5 and all(s["dur"] < 0.1 for s in strobe)
    assert strobe[0]["t0"] == pytest.approx(20.1, abs=0.6)         # на последнем дропе
    for a, b in zip(segs, segs[1:]):
        if b.get("xfade"):
            assert b["mode"] == "story" and a.get("tail") == b["xfade"] and 0.2 <= b["xfade"] <= 1.0
    story = [s for s in segs if s["mode"] == "story"]
    assert story and story[0].get("hero") and story[0]["ramp"]["base"] == 0.5   # первый кадр тихой части — замедлен
    action = [s for s in segs if s["mode"] in ("action", "climax") and not s.get("strobe") and not s.get("hero")
              and not s.get("relaxed")]
    assert action and all(abs(s["ramp"]["base"] - 1.0) < 1e-9 for s in action)  # экшен — родная скорость
    assert p["vfade"][1] > 0 and any(b["kind"] == "blink" for b in p["blacks"])


def test_colour_runs_beat_a_colour_blind_choice():
    song = make_song(levels=[2] * 15, dur=30.0)
    lib = library_two_colours()
    ordered = style.stats(plan.build_plan(song, lib, 0.0, 30.0, style="reference", strobe="none"), song)
    blind = style.stats(plan.build_plan(song, lib, 0.0, 30.0, style="reference", strobe="none", colour_weight=0.0),
                        song)
    assert ordered["colour_jump"] < blind["colour_jump"] and ordered["colour_jump"] <= 0.65


def test_drop_anchors_the_shots_own_impact_and_skips_the_synthetic_flash():
    song = make_song(levels=[2] * 15, dur=30.0, drops=(10.1,))
    lib = [clip(f"A:{i}:0", "A", 10.0 * i, impacts=(10.0 * i + 2.0,)) for i in range(40)]
    p = plan.build_plan(song, lib, 0.0, 30.0, style="reference", strobe="none")
    drop = next(s for s in p["segments"] if s["drop"])
    assert drop.get("impact_shown")
    c = next(x for x in lib if x["id"] == drop["clip"])
    shown_at = drop["src_in"] + __import__("tools.amv.fx", fromlist=["fx"]).rate_integral(drop["t0"], drop["ta"],
                                                                                          drop["ramp"])
    assert shown_at == pytest.approx(c["impacts"][0], abs=0.02)   # свой удар — ровно на акценте
    assert not any(abs(f["t"] - drop["t0"]) < 0.05 for f in p["flashes"])


def test_merged_long_shots_and_motif_bonus():
    wins = [clip(f"B:1:{w}", "B", 100 + 2.4 * w, shot="B:1") for w in range(3)]
    merged = style.merged_shots(wins)
    assert len(merged) == 1 and merged[0]["t_in"] == 100 and merged[0]["t_out"] == pytest.approx(107.8)
    hooks = style.Hooks(wins, merged)
    prev = dict(clip("C:1:0", "C", 0), motif="hand")
    hand = dict(clip("D:1:0", "D", 0), motif="hand")
    other = clip("E:1:0", "E", 0)
    seg = {"mode": "action", "drop": False}
    assert hooks.score(seg, hand, prev) > hooks.score(seg, other, prev) + 0.5
    assert library.apply_overrides([clip("x:1:0", "x", 0)], {"motif": {"x:1:0": "coin"}})[0]["motif"] == "coin"


def test_source_impacts_find_flash_frames_and_short_white_outs():
    n = 120
    luma = np.full(n, 80.0, np.float32)
    luma[30:32] = 220.0                       # вспышка 2 кадра
    white = np.zeros(n, np.float32)
    white[70:76] = 0.6                        # белый кадр 6 кадров
    F = {"fps": np.float32(24.0), "t0": np.float32(0.0), "luma": luma, "white": white}
    t = library.source_impacts(F, 0, n)
    assert any(abs(x - 30 / 24) < 0.05 for x in t) and any(abs(x - 70 / 24) < 0.05 for x in t)


# ----------------------------------------------------------------------------- сервис
@pytest.fixture
def api(tmp_path, monkeypatch):
    from app import amv_job as J
    from app import projects as P
    from app.settings import settings
    from test_api_pipeline import _client

    client, store = _client(tmp_path, monkeypatch)
    monkeypatch.setattr("app.ai.editor_agent.client_factory", None)
    monkeypatch.setattr(J, "SYNC", True)
    src = settings.source_dir / "csm_trailer.mp4"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"\0")
    film = store.create_source("upload", src, original_filename="csm_trailer.mp4", duration_sec=90, status="ready")
    other = store.create_source("upload", settings.source_dir / "x.mp4", original_filename="x.mp4", duration_sec=60)
    pid = P.create_project(store, "Chainsaw Man", kind="series", source_ids=[film["id"]])["id"]
    song = settings.audio_dir / "pain.wav"
    song.parent.mkdir(parents=True, exist_ok=True)
    sr = 22050
    x = np.zeros(sr * 24, np.int16)
    for k, t in enumerate(np.arange(0.1, 24.0, 0.5)):   # 120 BPM, вторая половина громче
        n0 = int(t * sr)
        x[n0:n0 + 400] = (9000 if t > 12 else 3000) * np.sin(np.arange(400) / 3.0)
    with wave.open(str(song), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(sr), w.writeframes(x.tobytes())
    track = store.create_audio_track("Kenshi Yonezu - Pain", song, duration_sec=24.0)
    return client, store, pid, film, other, track


def test_card_creates_work_and_chat_with_pinned_amv_playbook(api):
    from app import amv as A
    from app import project_chat as C

    client, store, pid, film, other, track = api
    bad = client.post(f"/api/projects/{pid}/amv", json={"title": "x", "source_ids": [other["id"]],
                                                         "track_id": track["id"], "run_agent": False})
    assert bad.status_code == 400 and "не из этого проекта" in bad.text
    assert client.post(f"/api/projects/{pid}/amv",
                       json={"title": "x", "source_ids": [film["id"]], "run_agent": False}).status_code == 400
    assert client.post(f"/api/projects/{pid}/amv", json={"title": "x", "source_ids": [film["id"]], "aspect": "4:3",
                                                          "track_id": track["id"], "run_agent": False}).status_code == 400
    res = client.post(f"/api/projects/{pid}/amv", json={
        "title": "Бензопила · Pain", "source_ids": [film["id"]], "track_id": track["id"],
        "notes": "знакомство → сближение → бомба → бой", "run_agent": False})
    assert res.status_code == 200, res.text
    body = res.json()
    thread = C.get_thread(store, body["thread_id"])
    assert thread["playbook"] == "amv" and thread["project_id"] == pid
    m = A.load(body["amv_id"])
    assert m["source_ids"] == [film["id"]] and m["track_id"] == track["id"] and m["thread_id"] == thread["id"]
    assert f'amv_analyze_song(amv_id: "{body["amv_id"]}")' in body["task"] and f"#{film['id']}" in body["task"]
    assert "Один тайтл" in body["task"] and "знакомство" in body["task"] and "20-секундная проба" in body["task"]


def test_card_sends_the_first_task_and_playbook_pick(api, monkeypatch):
    from app import project_chat as C
    from app import project_chat_playbook as PB

    client, store, pid, film, _other, track = api
    sent = {}
    monkeypatch.setattr(C, "send_to_thread", lambda store, tid, text, **kw: sent.update(tid=tid, text=text, **kw))
    res = client.post(f"/api/projects/{pid}/amv", json={"title": "AMV", "source_ids": [film["id"]],
                                                         "track_id": track["id"], "aspect": "16:9"})
    assert res.status_code == 200 and res.json()["started"] is True and res.json()["aspect"] == "16:9"
    assert sent["tid"] == res.json()["thread_id"] and sent["attachments"] == [{"kind": "source", "id": film["id"]}]
    assert PB.pick("сделай AMV по Бензопиле") == "amv"
    assert PB.pick("смонтируй аниме-клип под песню") == "amv"       # не «клип на песню»
    assert PB.pick("клип на свою песню") == "music-video"
    assert PB.pick("ок, делай полную", pinned="amv") == "amv"


def _fake_make(tmp_lib):
    """Подмена подпроцесса tools.amv.make: lib — синтетические кадры; render — крошечный mp4; check — отчёт и лист."""
    def run(amv_id, kind, args, log_name, timeout):
        from app import amv as A

        folder = A.work_dir(amv_id)
        stage = args[0]
        if stage == "lib":
            srcs = json.loads(Path(args[2]).read_text(encoding="utf-8"))
            (folder / "lib").mkdir(parents=True, exist_ok=True)
            clips = []
            for s in srcs:
                for c in tmp_lib:
                    act = library.act_at(s.get("acts"), c["t_in"])
                    clips.append(dict(c, id=f"{s['film']}:{c['id']}", film=s["film"], src=s["path"], act=act,
                                      shot=f"{s['film']}:{c['shot']}"))
            (folder / "lib" / "clips.json").write_text(json.dumps(clips), encoding="utf-8")
            (folder / "lib" / "boxes.json").write_text(json.dumps({s["path"]: [0, 0, 64, 36] for s in srcs}),
                                                       encoding="utf-8")
        elif stage == "render":
            out = Path(args[args.index("--out") + 1])
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=72x128:rate=10",
                            "-t", "1", "-pix_fmt", "yuv420p", str(out)], check=True)
        elif stage == "check":
            import cv2

            video = Path(args[args.index("--video") + 1])
            cv2.imwrite(str(folder / f"{video.stem}_sheet.jpg"), np.full((40, 2400, 3), 128, np.uint8))
            (folder / f"{video.stem}_check.json").write_text(json.dumps(
                {"video": str(video), "frozen_stretches": [], "duration": 1.0}), encoding="utf-8")
    return run


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="нужен ffmpeg")
def test_tools_song_index_plan_render_status_end_to_end(api, monkeypatch):
    from app import amv as A
    from app import amv_job as J
    from app import feed as FEED
    from app.agent_tools import ImageResult, ToolContext, run_tool

    _client, store, pid, film, _other, track = api
    monkeypatch.setattr(J, "_make", _fake_make(library_two_colours()))
    ctx = ToolContext(store=store, project_hint=pid)
    out, ok = run_tool(ctx, "amv_analyze_song", {"project_id": pid, "track_id": track["id"],
                                                 "source_ids": [film["id"]], "title": "Pain"})
    assert ok and out["status"] == "done", out
    aid = out["amv_id"]
    assert out["song"]["bpm"] == pytest.approx(120, abs=3) and out["song"]["parts"]
    bad, ok = run_tool(ctx, "amv_index_sources", {"amv_id": aid, "sources": [{"source_id": film["id"],
                                                                               "acts": [[10, 5, "meet"]]}]})
    assert not ok and "конец раньше начала" in str(bad)
    out, ok = run_tool(ctx, "amv_index_sources", {"amv_id": aid, "sources": [{
        "source_id": film["id"], "acts": [[0, 300, "meet"], [300, 2000, "fight"]], "exclude": [[0, 2]]}]})
    assert ok and out["status"] == "done", out
    lib = out["library"]
    assert lib["clips"] == len(library_two_colours()) and lib["long_shots"] >= 6
    assert lib["films"][f"s{film['id']}"]["acts"]["meet"] > 0 and not lib["notes"][:1] == ["акты не размечены"]
    srcs = json.loads((A.work_dir(aid) / "sources.json").read_text(encoding="utf-8"))
    assert srcs[0]["exclude"] == [[0.0, 2.0]] and srcs[0]["film"] == f"s{film['id']}"
    out, ok = run_tool(ctx, "amv_plan", {"amv_id": aid, "test": True, "story": [
        {"t0": 0, "t1": 12, "acts": ["meet"], "mode": "story"}, {"t0": 12, "t1": 24, "acts": ["fight"]}],
        "overrides": {"ban": [f"s{film['id']}:A:3:0"], "motif": {f"s{film['id']}:A:5:0": "hand"}}})
    assert ok, out
    assert out["name"] == "test" and out["t1"] - out["t0"] == pytest.approx(20.0, abs=0.01)
    assert out["checks"] and out["shots"] and all(s["clip"] != f"s{film['id']}:A:3:0" for s in out["shots"])
    assert json.loads((A.work_dir(aid) / "overrides.json").read_text(encoding="utf-8"))["ban"] == [
        f"s{film['id']}:A:3:0"]
    out, ok = run_tool(ctx, "amv_render", {"amv_id": aid, "name": "test"})
    assert ok and out["status"] == "done", out
    clip_info = out["clip"]
    assert clip_info["final"] is False and "feed" not in clip_info and clip_info["size"] == "720x1280"
    from app import projects as P

    assert P.project_of_plan(store, clip_info["clip_plan_id"]) == pid
    again, ok = run_tool(ctx, "amv_render", {"amv_id": aid, "name": "test"})   # опрос — не новый рендер
    assert ok and again["clip"]["clip_id"] == clip_info["clip_id"]
    img, ok = run_tool(ctx, "amv_status", {"amv_id": aid, "sheet": "test"})
    assert ok and isinstance(img, ImageResult) and img.mime_type == "image/jpeg"
    # полная версия → клип и версия в ленте (вид AMV, на проверке)
    out, ok = run_tool(ctx, "amv_plan", {"amv_id": aid, "name": "full"})
    assert ok and out["t0"] == 0.0
    out, ok = run_tool(ctx, "amv_render", {"amv_id": aid, "name": "full"})
    assert ok and out["status"] == "done" and out["clip"]["size"] == "1080x1920", out
    feed = out["clip"]["feed"]
    work = FEED.get_work(store, feed["work_id"])
    assert work["kind"] == "amv" and work["versions"][0]["status"] == "review"
    status, ok = run_tool(ctx, "amv_status", {"amv_id": aid})
    assert ok and set(status["renders"]) == {"test_9x16", "full_9x16"} and status["feed_work_id"] == feed["work_id"]
    assert status["jobs"]["song"]["status"] == "done"
    missing, ok = run_tool(ctx, "amv_status", {"amv_id": "amv00000000"})
    assert not ok and "нет работы AMV" in str(missing)


def test_interrupted_job_after_restart(api):
    from app import amv as A
    from app import amv_job as J

    _client, store, pid, film, _other, track = api
    m = A.create(store, pid, title="x", source_ids=[film["id"]], track_id=track["id"])
    A.update(m["id"], lambda x: x["jobs"].update(index={"status": "running"}))
    assert J.job_state(A.load(m["id"]), "index")["status"] == "interrupted"
    assert J.test_window(make_song(dur=60.0, drops=(20.1, 44.1))) == (38.1, 58.1)
