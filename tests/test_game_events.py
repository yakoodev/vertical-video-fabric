"""События геймплея (app/game_events*.py): шаблоны HUD на синтетических кадрах, лента убийств, звук,
бои, кэш и фон, инструмент MCP и ручки API — без настоящих записей и без ffmpeg."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from app import game_events as GE
from app import game_events_audio as GA
from app import game_events_hud as HUD
from app import game_events_job as JOB


def _tpl(name: str) -> np.ndarray:
    return dict((s.name, t) for s, t in HUD.load_templates("apex"))[name]


def _raw_tpl(name: str) -> np.ndarray:
    return cv2.imread(str(HUD.TEMPLATE_DIR / "apex" / f"{name}.png"), cv2.IMREAD_GRAYSCALE)


def _frame(seed: int = 0) -> np.ndarray:
    """Серый «игровой» кадр: плавный фон с шумом, без надписей."""
    rng = np.random.default_rng(seed)
    base = cv2.resize(rng.integers(40, 200, (9, 16), dtype=np.uint8), (HUD.W, HUD.H), interpolation=cv2.INTER_CUBIC)
    return np.clip(base.astype(np.int16) + rng.integers(-12, 12, base.shape), 0, 255).astype(np.uint8)


def _paste(gray: np.ndarray, img: np.ndarray, x: int, y: int) -> None:
    gray[y:y + img.shape[0], x:x + img.shape[1]] = img


def _knock_frame(seed: int = 0, word: str = "knock_ru", x: int = 782, chevron_x: int = 1082, chevron: bool = True) -> np.ndarray:
    g = _frame(seed)
    _paste(g, _raw_tpl(word), x, 762)
    if chevron:
        _paste(g, _raw_tpl("chevron"), chevron_x, 762)
    return g


def test_templates_exist_for_every_spec():
    names = {s.name for s, _ in HUD.load_templates("apex")}
    assert {s.name for s in HUD.SPECS["apex"]} | {"chevron"} == names
    for spec, tpl in HUD.load_templates("apex"):
        x0, y0, x1, y1 = spec.roi
        assert tpl.shape[0] * spec.shrink <= y1 - y0 and tpl.shape[1] * spec.shrink <= x1 - x0, spec.name


def test_knock_banner_is_found_and_needs_the_points_chevron():
    obs = HUD.analyze_frame(_knock_frame())
    assert obs["knock_ru"][0] > 0.95 and abs(obs["knock_ru"][1] - 782) <= 1
    assert obs["chevron"][0] > 0.95
    assert obs["wipe_ru"][0] < 0.6 and obs["kshield_ru"][0] < 0.6
    bare = HUD.analyze_frame(_knock_frame(chevron=False))
    assert bare["knock_ru"] == (0.0, 0)  # без значка очков слова плашек не сравниваются
    assert HUD.analyze_frame(_frame(3))["chevron"][0] < HUD.CHEVRON_THR


def test_banner_frames_become_one_event_and_name_change_splits():
    frames = [(10 + i * 0.25, HUD.analyze_frame(_knock_frame(i))) for i in range(8)]
    frames += [(12.0 + i * 0.25, {"chevron": (0.1, 0)}) for i in range(4)]
    # следующая плашка — другой ник, строка другой длины: слово сдвинулось
    frames += [(13.0 + i * 0.25, HUD.analyze_frame(_knock_frame(i, x=800, chevron_x=1100))) for i in range(3)]
    events = [e for e in HUD.hud_events(frames) if e["kind"] == "knock"]
    assert [e["t"] for e in events] == [10.0, 13.0]
    assert all(e["detail"] == "НОКАУТ" and 0.9 <= e["score"] <= 1.0 for e in events)
    # подряд без паузы, но со сдвигом слова — тоже две плашки
    back = [(t, HUD.analyze_frame(_knock_frame(i, x=782 if t < 11 else 760))) for i, t in enumerate(np.arange(10, 12, 0.25))]
    assert len([e for e in HUD.hud_events(back) if e["kind"] == "knock"]) == 2


def test_single_frame_flash_is_not_a_banner_at_4fps():
    frames = [(t, {"chevron": (0.1, 0)}) for t in np.arange(0, 3, 0.25)]
    frames[5] = (frames[5][0], HUD.analyze_frame(_knock_frame()))
    assert HUD.hud_events(frames) == []


def test_assist_banner_is_a_lower_score_knock_not_a_plain_knock():
    g = _frame()
    _paste(g, _raw_tpl("assist_ru"), 640, 762)
    _paste(g, _raw_tpl("knock_ru"), 860, 762)  # «СОДЕЙСТВИЕ В НОКАУТЕ» содержит «НОКАУТ»
    _paste(g, _raw_tpl("chevron"), 1200, 762)
    obs = HUD.analyze_frame(g)
    events = HUD.hud_events([(t, obs) for t in (5.0, 5.25, 5.5)])
    assert [(e["kind"], e["detail"]) for e in events] == [("knock", "СОДЕЙСТВИЕ В НОКАУТЕ (помощь)")]
    assert events[0]["score"] < 0.85


def test_wipe_level_and_win_banners():
    g = _frame(1)
    _paste(g, _raw_tpl("wipe_ru"), 740, 762)
    _paste(g, _raw_tpl("chevron"), 1107, 762)
    _paste(g, _raw_tpl("maxlevel_ru"), 776, 222)
    _paste(g, _raw_tpl("champion_ru"), 518, 418)
    obs = HUD.analyze_frame(g)
    kinds = sorted(e["kind"] for e in HUD.hud_events([(t, obs) for t in (1.0, 1.25, 1.5)]))
    assert kinds == ["max_level", "squad_wipe", "win"]  # «новый уровень» рядом с «макс.» — не отдельно


def test_state_fires_once_on_entry_and_ignores_flicker():
    g = _frame(2)
    _paste(g, _raw_tpl("kshield_ru"), 878, 879)
    on, off = HUD.analyze_frame(g), {"chevron": (0.0, 0)}
    seq = [on] * 8 + [off] + [on] * 8 + [off] * 4 + [on] * 2  # мигнула, потом 0.5 с — мало
    frames = [(20 + i * 0.25, o) for i, o in enumerate(seq)]
    deaths = [e for e in HUD.hud_events(frames) if e["kind"] == "death"]
    assert [(e["t"], e["detail"]) for e in deaths] == [(20.0, "сбит (Нокаут-щит)")]


# --------------------------------------------------------------------------- #
# Лента убийств
# --------------------------------------------------------------------------- #

ORANGE = tuple(int(c) for c in cv2.cvtColor(np.uint8([[[21, 170, 230]]]), cv2.COLOR_HSV2BGR)[0, 0])


def _feed_frame(row: int = 0, knock: bool = False, owner: str = "yakoodev", seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    img = np.full((HUD.H, HUD.W, 3), 120, np.uint8) + rng.integers(0, 20, (HUD.H, HUD.W, 1), dtype=np.uint8)
    yc = int(round(HUD.FEED_ROW0 + row * HUD.FEED_ROW_STEP))
    cv2.rectangle(img, (1380, yc - 14), (HUD.FEED_RIGHT + 4, yc + 14), (45, 45, 45), -1)  # плашка строки
    font = cv2.FONT_HERSHEY_SIMPLEX
    cv2.putText(img, owner, (1400, yc + 7), font, 0.65, ORANGE, 2, cv2.LINE_AA)
    (w, _), _ = cv2.getTextSize(owner, font, 0.65, 2)
    cv2.putText(img, "[gun]", (1400 + w + 10, yc + 7), font, 0.6, (240, 240, 240), 2, cv2.LINE_AA)
    if knock:
        pts = np.array([[1660, yc - 8], [1676, yc - 8], [1668, yc + 8]], np.int32)
        cv2.fillPoly(img, [pts], (20, 20, 235))
    (vw, _), _ = cv2.getTextSize("Victim", font, 0.6, 2)
    cv2.putText(img, "Victim", (HUD.FEED_RIGHT - vw, yc + 7), font, 0.6, (240, 240, 240), 2, cv2.LINE_AA)
    return img


def test_feed_row_with_player_on_the_left_kill_vs_knock():
    rows = HUD.feed_rows(_feed_frame(row=1))
    assert len(rows) == 1 and not rows[0]["knock"] and abs(rows[0]["y"] - 256) <= 2
    rows = HUD.feed_rows(_feed_frame(row=0, knock=True))
    assert len(rows) == 1 and rows[0]["knock"]
    # ник игрока справа (он жертва) или нет плашки ленты у правого края — не строка убийцы
    victim = np.full((HUD.H, HUD.W, 3), 40, np.uint8)
    cv2.putText(victim, "yakoodev", (HUD.FEED_RIGHT - 90, 230), cv2.FONT_HERSHEY_SIMPLEX, 0.65, ORANGE, 2)
    assert HUD.feed_rows(victim) == []
    blob = np.full((HUD.H, HUD.W, 3), 40, np.uint8)
    cv2.rectangle(blob, (1300, 214), (1500, 234), ORANGE, -1)  # жёлтая стена, а не буквы
    assert HUD.feed_rows(blob) == []


def test_feed_track_becomes_one_kill_and_knock_rows_do_not():
    frames = []
    for i in range(16):  # строка 4 с, съезжает вниз
        frames.append((30 + i * 0.25, {"feed": HUD.feed_rows(_feed_frame(row=min(3, i // 4), seed=i))}))
    kills = [e for e in HUD.hud_events(frames) if e["kind"] == "kill"]
    assert [e["t"] for e in kills] == [30.0] and kills[0]["score"] >= 0.85
    knocks = [(40 + i * 0.25, {"feed": HUD.feed_rows(_feed_frame(knock=True, seed=i))}) for i in range(8)]
    assert [e for e in HUD.hud_events(knocks) if e["kind"] == "kill"] == []
    lone = [(50.0, {"feed": HUD.feed_rows(_feed_frame())})]  # один кадр — пятно, не убийство
    assert HUD.hud_events(lone) == []


def test_kills_after_a_win_are_ignored():
    win = {"champion_ru": (0.95, 518), "chevron": (0.0, 0)}
    frames = [(t, dict(win)) for t in (60.0, 60.25, 60.5)]
    frames += [(70 + i * 0.25, {"feed": HUD.feed_rows(_feed_frame(seed=i))}) for i in range(6)]
    assert [e["kind"] for e in HUD.hud_events(frames)] == ["win"]


# --------------------------------------------------------------------------- #
# Звук
# --------------------------------------------------------------------------- #

def _noise(sec: float, level: float, seed: int = 0) -> np.ndarray:
    return (np.random.default_rng(seed).standard_normal(int(sec * GA.RATE)) * level).astype(np.float32)


def test_gunfire_burst_from_game_audio():
    from app.audio_map import frame_features

    x = _noise(8, 0.003)
    for k in range(6):  # 6 выстрелов с шагом 0.12 с на 3-й секунде
        i = int((3.0 + k * 0.12) * GA.RATE)
        x[i:i + 800] += _noise(0.05, 0.5, k)[:800] * np.exp(-np.arange(800) / 200)
    i = int(6.5 * GA.RATE)  # одиночный хлопок — не очередь
    x[i:i + 800] += _noise(0.05, 0.5, 9)[:800]
    bursts = GA.find_bursts(frame_features(x, GA.RATE), t0=100.0)
    assert len(bursts) == 1 and 102.9 <= bursts[0]["t"] <= 103.1 and bursts[0]["kind"] == "shot_burst"
    assert int(bursts[0]["detail"].split()[0]) >= GA.BURST_MIN_SHOTS


def test_mic_reaction_is_loud_voice_above_normal_speech():
    from app.audio_map import frame_features

    t = np.arange(int(10 * GA.RATE)) / GA.RATE
    speech = (0.02 * np.sin(2 * np.pi * 220 * t) * (np.sin(2 * np.pi * 3 * t) > 0)).astype(np.float32)
    loud = (t > 6) & (t < 7)
    speech[loud] = (0.4 * np.sin(2 * np.pi * 330 * t[loud])).astype(np.float32)
    reacts = GA.find_reactions(frame_features(speech, GA.RATE), t0=0.0)
    assert len(reacts) == 1 and 5.8 <= reacts[0]["t"] <= 6.2 and reacts[0]["kind"] == "reaction"
    assert GA.find_reactions(frame_features(np.zeros(GA.RATE * 3, np.float32), GA.RATE)) == []


def test_pcm_command_merges_tracks_into_one_stream():
    cmd = GA._pcm_cmd(GE.Path("v.mp4"), [0, 1], 5.0, 65.0)
    assert "-filter_complex" in cmd and "amerge=inputs=2" in " ".join(cmd) and cmd[-1] == "-"
    assert "-map" in GA._pcm_cmd(GE.Path("v.mp4"), [0], 0.0, 10.0)


# --------------------------------------------------------------------------- #
# Сборка, бои, дубли
# --------------------------------------------------------------------------- #

def _e(t, kind, score=0.9, detail="x"):
    return {"t": t, "kind": kind, "score": score, "detail": detail}


def test_fight_spans_group_contact_to_last_decisive_event():
    events = [_e(100, "shot_burst", 0.6), _e(104, "shot_burst", 0.6), _e(108, "knock"), _e(115, "kill"),
              _e(118, "shot_burst", 0.5), _e(200, "level_up"), _e(300, "shot_burst"), _e(400, "death")]
    spans = GE.fight_spans(events)
    assert spans[0]["start"] == 96.0 and spans[0]["end"] == 119.0 and spans[0]["knocks"] == 1 and spans[0]["kills"] == 1
    assert [s["start"] for s in spans] == [96.0, 396.0]  # один выстрел в пустоту — не бой; смерть — бой
    assert 0 < spans[1]["score"] < spans[0]["score"] <= 1.0


def test_dedupe_keeps_first_of_close_duplicates():
    out = GE.dedupe([_e(10, "level_up", 0.8, "НОВЫЙ УРОВЕНЬ"), _e(11, "level_up", 1.0, "НОВЫЙ УРОВЕНЬ"),
                     _e(10.5, "knock", 0.9, "НОКАУТ"), _e(13, "knock", 0.9, "НОКАУТ"), _e(14, "shot_burst")])
    assert [(e["t"], e["kind"], e["score"]) for e in out] == [(10, "level_up", 1.0), (10.5, "knock", 0.9),
                                                             (13, "knock", 0.9), (14, "shot_burst", 0.9)]


def test_flash_events_and_frame_command():
    assert [e["t"] for e in GE._flash_events([(1.0, 90), (1.25, 230), (1.5, 235), (5.0, 80), (6.0, 240)])] == [1.25]
    cmd = GE.frame_cmd(GE.Path("v.mp4"), 10, 70, True)
    assert cmd[cmd.index("-skip_frame") + 1] == "nokey" and "scale=1920:1080" in " ".join(cmd)
    assert "-skip_frame" not in GE.frame_cmd(GE.Path("v.mp4"), 0, 5, False)


def test_detect_game_events_contract(tmp_path, monkeypatch):
    video = tmp_path / "apex.mp4"
    video.write_bytes(b"x")
    monkeypatch.setattr(GE, "probe", lambda p: {"duration": 120.0, "width": 2560, "height": 1440, "audio_tracks": 2,
                                                "has_video": True, "keyframe_gap": 0.25})
    monkeypatch.setattr(GE, "hud_scan", lambda *a, **k: [_e(30, "knock", 0.95, "НОКАУТ"), _e(130, "kill")])
    monkeypatch.setattr(GA, "audio_events", lambda *a, **k: [_e(29.5, "shot_burst", 0.6, "4 выстр.")])
    events = GE.detect_game_events(video, start=10, end=100)
    assert [(e["t"], e["kind"]) for e in events] == [(29.5, "shot_burst"), (30, "knock")]
    assert all(set(e) == {"t", "kind", "score", "detail"} and 0 <= e["score"] <= 1 and e["kind"] in GE.KINDS for e in events)
    with pytest.raises(ValueError):
        GE.detect_game_events(video, game="valorant")
    with pytest.raises(FileNotFoundError):
        GE.detect_game_events(tmp_path / "nope.mp4")


# --------------------------------------------------------------------------- #
# Кэш, фон, MCP, API
# --------------------------------------------------------------------------- #

def _source(tmp_path, monkeypatch):
    from test_data_foundation import _store

    from app.settings import settings

    store = _store(tmp_path, monkeypatch)
    video = settings.source_dir / "apex.mp4"
    video.write_bytes(b"video")
    source = store.create_source("upload", video, original_filename="apex.mp4", duration_sec=60, status="ready")
    calls = []

    def fake_detect(path, *, game="apex", start=None, end=None, progress=None):
        calls.append(str(path))
        return [_e(12, "knock", 0.95, "НОКАУТ"), _e(10, "shot_burst", 0.6), _e(40, "reaction", 0.7, "крик")]

    monkeypatch.setattr(GE, "detect_game_events", fake_detect)
    monkeypatch.setattr(GE, "probe", lambda p: {"duration": 60.0})
    JOB._JOBS.clear()
    return store, source, video, calls


def test_job_scans_once_then_serves_cache_until_file_changes(tmp_path, monkeypatch):
    store, source, video, calls = _source(tmp_path, monkeypatch)
    out = JOB.game_events_job(store, source["id"], wait=10)
    assert out["status"] == "done" and not out["cached"] and len(out["events"]) == 3 and out["fights"]
    again = JOB.game_events_job(store, source["id"], wait=10)
    assert again["cached"] and len(calls) == 1
    win = JOB.window(again, 11, 30, ["knock"])
    assert [e["t"] for e in win["events"]] == [12] and win["counts"] == {"knock": 1} and "stamp" not in win
    video.write_bytes(b"video, but longer")  # пересобранный исходник — скан заново
    assert not JOB.game_events_job(store, source["id"], wait=10)["cached"] and len(calls) == 2


def test_job_reports_running_while_scan_is_slow(tmp_path, monkeypatch):
    import threading

    store, source, _video, _calls = _source(tmp_path, monkeypatch)
    gate = threading.Event()

    def slow(path, *, game="apex", start=None, end=None, progress=None):
        progress(30.0)
        gate.wait(5)
        return []

    monkeypatch.setattr(GE, "detect_game_events", slow)
    out = JOB.game_events_job(store, source["id"], wait=0.2)
    assert out["status"] == "running" and out["total_min"] == 1.0 and out["retry_after_sec"] >= 10
    gate.set()
    assert JOB.game_events_job(store, source["id"], wait=5)["status"] == "done"


def test_game_events_tool(tmp_path, monkeypatch):
    from app.agent_tools import ToolContext, get_tool, run_tool

    store, source, _video, _calls = _source(tmp_path, monkeypatch)
    out, ok = run_tool(ToolContext(store=store), "game_events", {"source_id": source["id"], "kinds": ["knock", "reaction"]},
                       validate=True)
    assert ok, out
    assert [e["kind"] for e in out["events"]] == ["knock", "reaction"] and "fights" in out and "note" in out
    out, ok = run_tool(ToolContext(store=store), "game_events", {"source_id": source["id"], "kinds": ["dance"]})
    assert not ok and "неизвестные виды" in out["error"]
    assert get_tool("game_events").scope == "global"


def test_game_events_api(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.game_events_api import build_game_events_router

    store, source, _video, _calls = _source(tmp_path, monkeypatch)
    app = FastAPI()
    from typing import Annotated

    from fastapi import Depends

    app.include_router(build_game_events_router(Annotated[None, Depends(lambda: None)], lambda: store))
    client = TestClient(app)
    url = f"/api/sources/{source['id']}/game-events"
    assert client.get(url).status_code == 404  # ещё не сканировали
    res = client.post(url, params={"wait": 5})
    assert res.status_code == 200 and res.json()["counts"] == {"knock": 1, "shot_burst": 1, "reaction": 1}
    got = client.get(url, params={"kinds": "knock", "from_sec": 5}).json()
    assert [e["kind"] for e in got["events"]] == ["knock"]
    assert client.post("/api/sources/999/game-events").status_code == 404
