"""Мем-эдит (app/meme_edit*.py): раскладка по долям, мемы по смыслу без повторов, куски и вырезы, файл клипа обоих
форматов проходит проверку, неодобренный мем не берётся, запасной путь событий по звуку, API и инструмент."""

from __future__ import annotations

import math
import random
import sys
import types

import pytest

from app import meme_edit as ME
from app import meme_edit_events as EV
from app import meme_edit_plan as MP
from app import video_memes as VM
from app.agent_tools import ToolContext, run_tool
from app.clip_spec import validate_spec
from app.manual_clips import create_manual_clip
from test_api_pipeline import _client

PERIOD = 0.5
DURATION = 150.0


def _events() -> list[dict]:
    shots = [{"t": round(8 + k * 0.7, 2), "kind": "shot_burst", "score": 0.4 + (k % 3) * 0.1} for k in range(20)]
    shots += [{"t": round(70 + k * 0.9, 2), "kind": "shot_burst", "score": 0.5} for k in range(12)]
    return shots + [
        {"t": 15.3, "kind": "knock", "score": 0.8}, {"t": 22.1, "kind": "kill", "score": 0.9},
        {"t": 41.7, "kind": "reaction", "score": 0.7}, {"t": 60.2, "kind": "death", "score": 0.6},
        {"t": 78.4, "kind": "kill", "score": 0.7}, {"t": 95.0, "kind": "squad_wipe", "score": 1.0},
    ]


def _library() -> list[dict]:
    rows = [(1, "Ёбушки-воробушки", ["шок"], 1.4), (2, "Мы его поймали", ["победа", "хвастовство"], 2.2),
            (3, "Плачущий мальчик", ["фейл", "грусть"], 2.6), (4, "Хохот до слёз", ["смех"], 1.8),
            (5, "Это шедевр", ["уважение", "победа"], 1.2), (6, "Тиммейт тупит", ["тиммейт"], 2.0),
            (7, "Ни ху я", ["шок", "вау"], 0.9), (8, "Слишком длинный", ["победа"], 6.0)]
    return [{"meme_id": i, "id": i, "asset_id": 100 + i, "title": t, "tags": tags, "duration_sec": d,
             "description": "", "when_to_use": "", "transcript": "", "has_audio": True} for i, t, tags, d in rows]


def _timeline(target: float = 40.0, seed: int = 1) -> MP.Timeline:
    return MP.plan_timeline(_events(), duration=DURATION, period=PERIOD, target_sec=target, library=_library(),
                            seed=seed)


def _on_grid(x: float, period: float = PERIOD) -> bool:
    return abs(x / period - round(x / period)) < 1e-3


# ------------------------------------------------------------------ раскладка (чистые функции)


def test_windows_follow_beats_and_land_events_on_beats():
    tl = _timeline()
    assert tl.windows, tl.warnings
    assert 0.85 * 40 <= tl.total <= 1.15 * 40
    for prev, cur in zip(tl.windows, tl.windows[1:]):
        assert cur.start >= prev.end - 1e-6  # по времени и без нахлёста
    for w in tl.windows:
        assert _on_grid(w.length), (w.role, w.length)  # каждая склейка — в долю
        if w.role in ("hit", "voice"):
            assert _on_grid(float(w.event["t"]) - w.start)  # само событие — ровно на долю
    roles = [w.role for w in tl.windows]
    assert roles.count("chop") >= 4 and "meme" in roles and "voice" in roles
    for w in tl.windows:
        if w.role == "chop":
            assert MP.CHOP_MIN_SEC - 1e-6 <= w.length <= MP.CHOP_MAX_SEC + 1e-6


def test_memes_match_events_never_repeat_and_skip_long():
    tl = _timeline(60.0)
    memes = [w for w in tl.windows if w.role == "meme"]
    ids = [w.meme["meme_id"] for w in memes]
    assert len(ids) == len(set(ids)) >= 3
    assert 8 not in ids  # 6 с — длиннее полнокадровой врезки
    by_kind = {w.event["kind"]: w.meme for w in memes}
    if "death" in by_kind:
        assert "фейл" in by_kind["death"]["tags"]
    if "reaction" in by_kind:
        assert "смех" in by_kind["reaction"]["tags"]
    for w in memes:
        assert w.insert_sec <= w.length + 1e-6 and w.insert_sec <= MP.MEME_MAX_SEC


def test_seed_changes_variant_but_is_deterministic():
    a, b = _timeline(seed=3), _timeline(seed=3)
    assert MP.summary(a) == MP.summary(b)
    variants = {tuple((w.role, w.start) for w in _timeline(seed=s).windows) for s in range(6)}
    assert len(variants) > 1


def test_pieces_and_cuts_rebuild_the_timeline():
    tl = _timeline()
    pieces, cuts = MP.to_pieces(tl, DURATION)
    assert 1 <= len(pieces) <= MP.MAX_PIECES
    clip_total = sum(p["end_sec"] - p["start_sec"] for p in pieces)
    for p in pieces:
        assert MP.MIN_PIECE_SEC - 1e-6 <= p["end_sec"] - p["start_sec"] <= MP.MAX_PIECE_SEC
    for prev, cur in zip(cuts, cuts[1:]):
        assert prev["end"] <= cur["start"] + 1e-6
    assert all(c["end"] - c["start"] >= MP.MIN_CUT_SEC - 1e-6 and c["end"] <= clip_total + 1e-6 for c in cuts)
    kept = clip_total - sum(c["end"] - c["start"] for c in cuts)
    assert kept == pytest.approx(tl.total, abs=0.01)  # после вырезов остаются ровно окна
    for w in tl.windows:
        assert MP.source_to_clip(tl, w.start + 0.01) == pytest.approx(w.clip_start + 0.01, abs=1e-3)
        assert MP.final_to_clip(tl, w.final_start + 0.01) == pytest.approx(w.clip_start + 0.01, abs=1e-3)


def test_no_events_gives_honest_warning():
    tl = MP.plan_timeline([], duration=60, period=PERIOD, target_sec=30, library=_library())
    assert not tl.windows and tl.warnings


def test_pick_meme_returns_none_when_nothing_fits():
    lib = [m for m in _library() if "фейл" not in m["tags"] and "грусть" not in m["tags"]
           and "злость" not in m["tags"] and "кринж" not in m["tags"]]
    assert MP.pick_meme(lib, "death", set(), random.Random(0)) is None


# ------------------------------------------------------------------ события: адаптер и запасной путь


def test_events_normalize_and_cluster():
    raw = [{"t": "5", "kind": "KILL", "score": 2}, {"t": -1}, {"t": 999, "kind": "win"}, "junk", {"t": 6.5}]
    out = EV.normalize_events(raw, duration=100)
    assert [e["kind"] for e in out] == ["kill", "shot_burst"] and out[0]["score"] == 1.0
    assert EV.cluster_spans(out, gap=3, pad=1) == [(4.0, 7.5)]


def test_audio_fallback_finds_shots_peak_and_voice(monkeypatch, tmp_path):
    np = pytest.importorskip("numpy")
    rate = 16000

    def fake_pcm(_path, track, start, length):
        n = int(length * rate)
        x = np.random.default_rng(track).normal(0, 0.003, n).astype(np.float32)
        if track == 0:  # перестрелка: удары на 10–14 с, самый сильный на 12 с
            for t, amp in ((10, 0.3), (11, 0.35), (12, 0.9), (13, 0.3), (14, 0.25)):
                i = int((t - start) * rate)
                x[i:i + 1600] += amp * np.sin(np.linspace(0, 400, 1600)).astype(np.float32)
        else:  # крик в микрофон на 30 с
            i = int((30 - start) * rate)
            x[i:i + rate] += 0.5 * np.sin(np.linspace(0, 2 * math.pi * 300, rate)).astype(np.float32)
        return x

    monkeypatch.setattr(EV, "_game_events_module", lambda: None)
    events = EV.audio_events(tmp_path / "x.mp4", 60.0, 1, pcm=fake_pcm)
    kinds = {e["kind"] for e in events}
    assert {"shot_burst", "knock", "reaction"} <= kinds
    knock = next(e for e in events if e["kind"] == "knock")
    assert abs(knock["t"] - 12) < 0.4
    assert abs(next(e for e in events if e["kind"] == "reaction")["t"] - 30) < 0.5


def test_game_events_module_is_used_when_present(monkeypatch, tmp_path):
    video = tmp_path / "rec.mp4"
    video.write_bytes(b"x")
    fake = types.ModuleType("app.game_events")
    fake.detect_game_events = lambda path, game="apex": [{"t": 3, "kind": "kill", "score": 0.9, "detail": game}]
    fake.fight_spans = lambda events: [(1.0, 5.0)]
    monkeypatch.setitem(sys.modules, "app.game_events", fake)
    events, origin = EV.detect_events({"local_path": str(video), "duration_sec": 10})
    assert origin == "game_events" and events[0]["kind"] == "kill" and events[0]["detail"] == "apex"
    assert EV.fight_spans(events) == [(1.0, 5.0)]


# ------------------------------------------------------------------ файл клипа и база


@pytest.fixture
def env(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    from app.settings import settings

    source = store.create_source("upload", settings.source_dir / "apex.mp4", original_filename="apex.mp4",
                                 duration_sec=DURATION, width=1920, height=1080, status="ready")
    files = tmp_path / "memes"
    files.mkdir()
    lib = []
    for meme in _library():
        path = files / f"m{meme['meme_id']}.mp4"
        path.write_bytes(b"0")
        asset = store.create_montage_asset(kind="video", label=meme["title"], file_path=str(path),
                                           duration_sec=meme["duration_sec"], width=640, height=360, has_audio=True)
        row = VM.create_meme(store, int(asset["id"]), tags=meme["tags"], approved=meme["meme_id"] != 7,
                             transcript_status="off")
        lib.append(row)
    return client, store, int(source["id"]), lib


GRID = {"period": PERIOD, "bpm": 120, "start_sec": 0.0, "first_downbeat": 0.0}


def test_spec_for_both_aspects_validates(env):
    _client_, store, source_id, _lib = env
    prepared = ME.prepare(store, source_id, events=_events(), target_sec=40, seed=2, grid=GRID)
    base = ME._base_render(store)
    for aspect in ("9:16", "16:9"):
        spec = ME.spec_for(prepared, aspect, base, seed=2)
        plan = create_manual_clip(store, source_id, spec["title"],
                                  [{k: p[k] for k in ("start_sec", "end_sec")} for p in spec["pieces"]])
        ops = validate_spec(store, int(plan["id"]), spec)
        render = ops["render"]
        assert render["format"] == aspect and render["layers_format"] == aspect
        assert render["cuts"] and render["transition"]["type"] == "cut" and render["end_hold_sec"] == 0
        memes = [w for w in prepared["timeline"].windows if w.role == "meme"]
        assert len(render["inserts"]) == len(memes) >= 3
        # Звук клипа под мемом глушит не вставка (тогда пропала бы и музыка), а вырезы звука игры по дорожке.
        assert all(i["mode"] == "full" and not i["duck"] and "duck_volume" not in i and i.get("level") == "speech"
                   for i in render["inserts"])
        for ins in render["inserts"]:
            assert any(m.get("track") == 0 and m["start"] <= ins["at"] + 0.01
                       and m["end"] >= ins["at"] + ins["duration"] - 0.01 for m in render["mute"])
        kinds = [(layer["type"], layer["props"].get("kind")) for layer in render["layers"]]
        assert ("fx", "negate") in kinds
        negate = next(layer for layer in render["layers"] if layer["props"].get("kind") == "negate")
        assert negate["props"]["color"].lower() == ME.NEGATE_COLOR
        words = [layer for layer in render["layers"] if layer["type"] == "text"]
        assert 2 <= len(words) <= 4
        for layer in words:
            assert layer["props"]["font"] == "Dela Gothic One" and layer["props"]["stroke"]["width"] >= 12
            assert layer["start"] >= MP.final_to_clip(prepared["timeline"], MP.NO_TEXT_BEFORE_SEC) - 1e-3
        assert render["camera"] and any(k.get("shake") for k in render["camera"])
        if aspect == "9:16":
            assert all(p["focus"] == [{"t": 0.0, "x": 0.5}] for p in ops["pieces"])


def test_unapproved_meme_never_used(env):
    _client_, store, _source_id, lib = env
    library = ME.meme_library(store)
    unapproved = next(m for m in lib if not m["approved"])
    assert all(m["meme_id"] != unapproved["id"] for m in library)
    tl = MP.plan_timeline(_events(), duration=DURATION, period=PERIOD, target_sec=60, library=library, seed=5)
    assert all(w.meme["meme_id"] != unapproved["id"] for w in tl.windows if w.meme)


def test_build_meme_edit_api_two_clips(env):
    client, store, source_id, _lib = env
    res = client.post(f"/api/sources/{source_id}/meme-edit",
                      json={"events": _events(), "target_sec": 35, "render": False, "seed": 4})
    assert res.status_code == 200, res.text
    body = res.json()
    assert [c["format"] for c in body["clips"]] == ["9:16", "16:9"]
    for clip in body["clips"]:
        plan = store.get_clip_plan(clip["clip_id"])
        settings = plan["render_settings"]
        assert settings["format"] == clip["format"] and settings["inserts"] and settings["cuts"]
    assert body["memes"] and len(body["memes"]) == len(set(body["memes"]))
    dry = client.post(f"/api/sources/{source_id}/meme-edit", json={"events": _events(), "dry_run": True})
    assert dry.status_code == 200 and dry.json()["spec"]["schema"] == "vvf.clip/1"


def test_meme_edit_tool_dry_run_and_errors(env):
    _client_, store, source_id, _lib = env
    out, ok = run_tool(ToolContext(store), "meme_edit",
                       {"source_id": source_id, "events": _events(), "dry_run": True}, validate=True)
    assert ok, out
    assert out["spec"]["render"]["inserts"]
    out, ok = run_tool(ToolContext(store), "meme_edit", {"source_id": source_id, "events": []})
    assert not ok and "эдит" in out["error"]


# ------------------------------------------------------------------ кадр мема: поля и подрезка


def test_cropdetect_parse_widest_box_and_noise():
    from app.meme_frame import parse_cropdetect

    log = "[Parsed_cropdetect_0] x1:0 crop=1280:552:0:84\n[Parsed_cropdetect_0] crop=1276:548:2:86\n"
    assert parse_cropdetect(log, 1280, 720) == {"x": 0.0, "y": 0.1167, "w": 1.0, "h": 0.7667}
    assert parse_cropdetect("crop=1280:716:0:2", 1280, 720) is None  # 2 px — шум кодека, не поле
    assert parse_cropdetect("crop=100:80:600:300", 1280, 720) is None  # почти весь кадр чёрный — тёмная сцена


def test_framing_cover_when_cheap_blur_when_too_much_lost():
    from app.meme_frame import framing

    # 4:3 с вшитыми полями в 16:9 → в 16:9-клип: поля прочь, подрезка высоты 25 % — во весь кадр.
    crop, fill = framing({"x": 0.125, "y": 0.0, "w": 0.75, "h": 1.0}, 1920, 1080, 1920, 1080)
    assert fill == "cover" and crop["x"] == 0.125 and crop["w"] == 0.75 and crop["h"] == pytest.approx(0.75)
    # Широкий мем (2.35:1 в letterbox) → в вертикаль: подрезка ровно на 45 % ширины вокруг лица, вокруг — размытие.
    crop, fill = framing({"x": 0.0, "y": 0.12, "w": 1.0, "h": 0.76}, 1280, 720, 1080, 1920, face=(0.7, 0.5))
    assert fill == "blur" and crop["w"] == pytest.approx(0.55) and crop["y"] == 0.12 and crop["h"] == 0.76
    assert crop["x"] == pytest.approx(0.7 - 0.275, abs=1e-3)
    # Почти вертикальный мем в вертикаль — во весь кадр; квадрат в 16:9 — тоже (теряется 44 % высоты).
    assert framing(None, 900, 1400, 1080, 1920)[1] == "cover"
    assert framing(None, 1000, 1000, 1920, 1080)[1] == "cover"
    assert framing(None, 1920, 1080, 1920, 1080) == (None, "cover")


def test_insert_crop_reaches_the_render_graph():
    from app.montage_assets import full_insert_chain, normalize_inserts

    [ins] = normalize_inserts([{"asset_id": 1, "at": 1, "duration": 2, "fill": "cover",
                                "crop": {"x": 0.1, "y": 0.2, "w": 0.5, "h": 0.6}}])
    assert ins["crop"] == {"x": 0.1, "y": 0.2, "w": 0.5, "h": 0.6}
    chain = full_insert_chain("[1:v]", 1080, 1920, "cover", "1", crop=ins["crop"])
    assert chain.startswith("[1:v]crop=trunc(iw*0.5000/2)*2:trunc(ih*0.6000/2)*2:") and "scale=1080:1920" in chain
    assert "crop" not in normalize_inserts([{"asset_id": 1, "at": 1, "crop": {"x": 0, "y": 0, "w": 1, "h": 1}}])[0]


# ------------------------------------------------------------------ картинка записи: смерть и движение


def test_dead_spans_turn_last_knock_into_death():
    from app import meme_edit_visual as V

    times = [i * 0.5 for i in range(40)]
    sat = [0.35] * 30 + [0.04] * 8 + [0.35] * 2  # 15–19 с — серый экран
    prof = {"t": times, "sat": sat, "motion": [10.0] * 40, "banner": [60.0] * 40}
    spans = V.dead_spans(prof)
    assert spans == [(15.0, 19.0)]
    events = [{"t": 12.0, "kind": "knock", "score": 0.8, "detail": ""},
              {"t": 13.6, "kind": "shot_burst", "score": 0.5, "detail": ""},
              {"t": 16.0, "kind": "knock", "score": 0.9, "detail": ""},
              {"t": 17.0, "kind": "reaction", "score": 0.7, "detail": ""}]
    out = V.apply_deaths(events, spans)
    kinds = [(e["t"], e["kind"]) for e in out]
    assert (16.0, "knock") not in kinds  # на сером экране нока не бывает
    assert (12.0, "death") in kinds and (17.0, "reaction") in kinds  # сильнейший удар перед смертью — по игроку
    tl = MP.plan_timeline(out, duration=40, period=PERIOD, target_sec=20, library=_library(), seed=1,
                          ctx=MP.Context(avoid=spans))
    death = next(w for w in tl.windows if w.event and w.event["kind"] == "death" and w.role == "meme")
    assert set(death.meme["tags"]) & {"фейл", "грусть"}
    assert not any(w.word in MP.KIND_WORDS["knock"] + MP.KIND_WORDS["kill"] and _inside(w, spans) for w in tl.windows)


def _inside(w: MP.Window, spans) -> bool:
    return any(a <= w.start <= b for a, b in spans)


def test_chops_skip_intro_still_frames_and_dead_screen():
    still = (30.0, 40.0)
    dead = [(60.0, 66.0)]

    def motion(t: float) -> float:
        return 0.05 if still[0] <= t <= still[1] else 0.9

    events = [{"t": float(t), "kind": "shot_burst", "score": 0.5} for t in range(1, 100, 2)]
    events += [{"t": 4.0, "kind": "knock", "score": 0.9}, {"t": 42.0, "kind": "kill", "score": 0.9},
               {"t": 70.0, "kind": "knock", "score": 0.8}, {"t": 90.0, "kind": "kill", "score": 0.8}]
    tl = MP.plan_timeline(events, duration=100, period=PERIOD, target_sec=40, library=_library(), seed=2,
                          ctx=MP.Context(motion=motion, avoid=dead))
    chops = [w for w in tl.windows if w.role == "chop"]
    assert chops
    for w in chops:
        assert w.start >= MP.MIN_CHOP_START_SEC - 1e-6
        assert not (still[0] <= w.start <= still[1] and still[0] <= w.end <= still[1])
        assert not any(a <= w.start <= b or a <= w.end <= b for a, b in dead)


def test_real_game_events_spans_are_dicts_and_become_pairs(monkeypatch):
    fake = types.ModuleType("app.game_events")
    fake.detect_game_events = lambda path, game="apex": []
    fake.fight_spans = lambda events: [{"start": 2.0, "end": 9.5, "score": 0.8}]
    monkeypatch.setitem(sys.modules, "app.game_events", fake)
    assert EV.fight_spans([{"t": 3, "kind": "kill", "score": 1}]) == [(2.0, 9.5)]
