"""Мем-эдит (app/meme_edit*.py) по правилам владельца (пилот 2, v1 → v4): бои целиком от первого контакта, внутри
вырезано только мёртвое время; мемы по смыслу сразу после реплики, «эхо» первым, ~1 на 22 с, без повторов, только
удар мема без пауз; всё в долю трека, шов музыки — на такте под стыком; переходы разные на каждом стыке и вырезе;
блок → файл клипа обоих форматов проходит проверку; задание фоном, API и инструмент."""

from __future__ import annotations

import pytest

from app import meme_edit as ME
from app import meme_edit_fights as F
from app import meme_edit_inputs as I
from app import meme_edit_job as J
from app import meme_edit_memes as M
from app import meme_edit_plan as P
from app import meme_trim as T
from app import video_memes as VM
from app.agent_tools import ToolContext, run_tool
from test_api_pipeline import _client

PERIOD = 0.4348  # 138 BPM, как песня владельца


def _w(word: str, a: float, b: float) -> dict:
    return {"word": word, "start": a, "end": b}


def _line(text: str, start: float, step: float = 0.32) -> list[dict]:
    return [_w(word, round(start + i * step, 3), round(start + i * step + step - 0.04, 3))
            for i, word in enumerate(text.split())]


# Бой 1 (запись 1): первые выстрелы 10.0, нок 18, вайп 22, реакция «Не надо, не надо!» 23.0; мёртвое время 30–38.
EVENTS_1 = [{"t": 18.0, "kind": "knock", "score": 0.9}, {"t": 22.0, "kind": "squad_wipe", "score": 0.95},
            {"t": 13.0, "kind": "shot_burst", "score": 0.7}, {"t": 39.0, "kind": "kill", "score": 0.9}]
SHOTS_1 = [[10.0, 10.8], [12.5, 14.0], [16.0, 18.2], [20.5, 22.1], [27.0, 29.5], [38.5, 40.0]]
OWNER_1 = _line("Не надо не надо!", 23.0) + _line("Альцгеймер какой-то", 41.0)
# Бой 2 (запись 2): смерть без киллов — фейл; бой 3: победа.
EVENTS_2 = [{"t": 8.0, "kind": "shot_burst", "score": 0.7}, {"t": 12.0, "kind": "death", "score": 0.9},
            {"t": 70.0, "kind": "shot_burst", "score": 0.8}, {"t": 74.0, "kind": "kill", "score": 0.9},
            {"t": 80.0, "kind": "win", "score": 0.95}]
SHOTS_2 = [[6.5, 7.5], [8.0, 9.5], [10.5, 12.0], [69.0, 70.5], [72.0, 74.5], [78.0, 79.5]]
OWNER_2 = _line("Ой летим", 12.6) + _line("Кулаком хорош!", 81.0)


def _fights() -> list[F.Fight]:
    return (F.find_fights(1, EVENTS_1, SHOTS_1, OWNER_1, [], 60.0)
            + F.find_fights(2, EVENTS_2, SHOTS_2, OWNER_2, [], 90.0))


def _library() -> list[dict]:
    rows = [(1, "Ой, летим!", "Ой летим", ["фейл", "смех"]), (2, "Ужасная болезнь", "Альцгеймер ужасная болезнь",
            ["непонимание"]), (3, "Я умный", "Я умный ни единой ошибки", ["хвастовство", "победа"]),
            (4, "Победный крик", "", ["победа", "радость"]), (5, "Не надо, дядя", "Не надо дядя", ["шок"]),
            (6, "Какой стыд", "Какой стыд", ["кринж", "фейл"])]
    out = []
    for i, title, said, tags in rows:
        out.append({"meme_id": i, "id": i, "asset_id": 100 + i, "title": title, "tags": tags, "duration_sec": 3.5,
                    "description": "", "when_to_use": "", "transcript": said, "has_audio": True,
                    "transcript_words": _line(said, 0.4, 0.45) if said else [], "voiced": [[0.4, 3.0]]})
    return out


# ------------------------------------------------------------------ бои


def test_fight_is_whole_from_first_contact_with_reaction_and_no_dead_time():
    [fight] = F.find_fights(1, EVENTS_1, SHOTS_1, OWNER_1, [], 60.0)
    assert fight.start == pytest.approx(10.0 - F.LEAD)  # первые выстрелы − запас, а не нок на 18 с
    assert fight.end >= OWNER_1[-1]["end"]  # реакция «Альцгеймер» после боя — часть боя
    assert fight.pieces[0][0] == fight.start and fight.pieces[-1][1] == fight.end
    # мёртвое время 30–38 (ни выстрелов, ни слов) вырезано, края с запасом
    gaps = [(a[1], b[0]) for a, b in zip(fight.pieces, fight.pieces[1:])]
    assert any(29.5 < g0 < 31.0 and 37.0 < g1 < 38.5 for g0, g1 in gaps)
    assert all(b - a >= F.MIN_PIECE - 1e-6 for a, b in fight.pieces)
    assert fight.result == "wipe" and fight.score > 3


def test_weak_fights_dropped_and_chosen_never_trimmed():
    weak = F.find_fights(3, [{"t": 5.0, "kind": "shot_burst", "score": 0.4}], [[4, 5], [6, 7], [8, 9]], [], [], 30.0)
    fights = _fights() + weak
    chosen, notes = F.select_fights(fights, 300.0)
    assert weak[0] not in chosen and any("слабых" in n for n in notes)
    small, notes = F.select_fights(_fights(), 12.0)
    assert len(small) == 1 and any("не влезли" in n for n in notes)
    assert all(f.pieces == g.pieces for f in small for g in _fights() if g.key == f.key)  # бой целиком


def test_order_short_first_win_last():
    ordered = F.order_fights(_fights())
    assert ordered[-1].result == "win"
    assert ordered[0].length <= 25.0


def test_vertical_best_of_fits_and_prefers_strong():
    best = F.pick_vertical(_fights(), max_sec=90, min_sec=60)
    assert 1 <= len(best) <= 3 and sum(F.with_memes(f.length) for f in best) <= 90


# ------------------------------------------------------------------ мемы


def test_echo_meme_after_setup_line_no_repeats_budget():
    fights = F.order_fights(_fights())
    picks, _notes = M.assign_memes(fights, _library(), 66.0)
    assert len(picks) == round(66 / M.MEME_EVERY_SEC)
    assert len({p.meme["meme_id"] for p in picks}) == len(picks)
    by_text = {p.cand.text: p for p in picks}
    # «Ой летим» → мем «Ой, летим!» (эхо), «Альцгеймер» → «ужасная болезнь»: мем сразу после конца реплики
    assert any(p.echo for p in picks)
    for p in picks:
        if p.cand.text:
            line_end = max(w["end"] for f in fights for ln in f.lines if ln["text"] == p.cand.text for w in ln["words"])
            assert p.cand.t == pytest.approx(line_end + 0.05, abs=1e-3)
    assert any(p.meme["meme_id"] == 1 for p in by_text.values()) or any(p.meme["meme_id"] == 2 for p in picks)
    per_fight: dict[str, int] = {}
    for p in picks:
        per_fight[p.cand.fight.key] = per_fight.get(p.cand.fight.key, 0) + 1
    assert max(per_fight.values()) <= M.MAX_PER_FIGHT


def test_meme_never_unapproved_and_none_when_no_meaning():
    lib = [{"meme_id": 9, "asset_id": 9, "title": "Танцы", "tags": ["танец"], "transcript": "", "duration_sec": 2}]
    picks, notes = M.assign_memes(F.order_fights(_fights()), lib, 60.0)
    assert picks == [] and any("мемов по смыслу" in n for n in notes)


def test_punch_parts_cut_silence_keep_echo():
    words = [_w("Ну", 0.1, 0.3), _w("это", 0.35, 0.6), _w("ужасная", 2.0, 2.5), _w("болезнь", 2.55, 3.1),
             _w("правда", 4.5, 4.9)]
    voiced = [[0.1, 0.6], [2.0, 3.1], [4.5, 4.9]]
    parts = T.punch_parts(words, voiced, 5.5, focus=["болезнь"])
    assert T.PUNCH_MIN_SEC <= T.total(parts) <= T.PUNCH_MAX_SEC
    assert any(a <= 2.6 <= b for a, b in parts)  # эхо-слово внутри
    assert all(not (0.8 < a < 1.9 and 0.8 < b < 1.9) for a, b in parts)  # пауза 0.6–2.0 вырезана
    short = T.punch_parts([_w("Нет", 1.0, 1.3)], [[1.0, 1.3]], 3.0)
    assert T.total(short) >= T.PUNCH_MIN_SEC - 1e-6
    assert T.punch_parts([], [], 6.0) == [[1.9, 4.1]]
    # мат мема вырезан из удара целиком
    swear = [_w("Ой", 0.2, 0.5), _w("бля", 0.6, 0.9), _w("летим", 1.0, 1.5)]
    cut = T.punch_parts(swear, [[0.2, 1.5]], 2.0, skip=T.swear_spans(swear))
    assert not any(a < 0.85 and b > 0.65 for a, b in cut)


def test_insert_parts_survive_normalize_and_reach_render(monkeypatch, tmp_path):
    from app import meme_trim
    from app.montage_assets import build_inserts_args, normalize_inserts

    [ins] = normalize_inserts([{"asset_id": 1, "at": 2, "duration": 1.74, "parts": [[0.5, 1.2], [2.0, 2.9]]}])
    assert ins["parts"] == [[0.5, 1.2], [2.0, 2.9]]
    assert "parts" not in normalize_inserts([{"asset_id": 1, "at": 2, "parts": [[2, 1]]}])[0]
    src = tmp_path / "m.mp4"
    src.write_bytes(b"0")
    trimmed = tmp_path / "trim.mp4"
    trimmed.write_bytes(b"0")
    seen = {}
    monkeypatch.setattr(meme_trim, "trimmed_file", lambda path, parts, length, has_audio=True: (
        seen.update(path=path, parts=parts, length=length) or trimmed))
    monkeypatch.setattr("app.motion_render.speech_level_db", lambda _p: -20.0)
    args = build_inserts_args(tmp_path / "clip.mp4", tmp_path / "out.mp4", [ins],
                              {1: {"kind": "video", "file_path": str(src), "has_audio": True}}, 1920, 1080, 10.0)
    assert str(trimmed) in args and str(src) not in args
    assert seen["parts"] == ins["parts"] and seen["length"] >= 1.74


# ------------------------------------------------------------------ доли и раскладка


def _plan(usable: float | None = None) -> dict:
    fights = F.order_fights(_fights())
    picks, _ = M.assign_memes(fights, _library(), 66.0)
    lengths = {int(p.meme["meme_id"]): 1.6 for p in picks}
    return P.plan_reel(fights, picks, lengths, period=PERIOD, usable_sec=usable, durations={1: 60.0, 2: 90.0},
                       words={1: (OWNER_1, []), 2: (OWNER_2, [])}, shots={1: SHOTS_1, 2: SHOTS_2})


def _on_beat(t: float) -> bool:
    return abs(t - P.frame(round(t / PERIOD) * PERIOD)) < 0.02


def test_every_cut_meme_and_join_on_beat_and_no_word_cut():
    plan = _plan()
    assert plan["blocks"] and plan["total"] > 30
    for b in plan["blocks"]:
        assert _on_beat(b["final_at"])
        for v in b["visible"]:
            end_final = b["final_at"] + v["clip_in"] + v["z"] - v["a"]
            assert _on_beat(end_final), (b["key"], v)
            assert not any(w["start"] < v["z"] < w["end"] - 0.03 for w in OWNER_1 + OWNER_2 if b["source_id"]), v
        for ins in b["inserts"]:
            assert _on_beat(b["final_at"] + ins["at"]) and _on_beat(b["final_at"] + ins["at"] + ins["slot"])
            assert ins["slot"] >= 1.6 - 0.12
        assert all(p["end_sec"] - p["start_sec"] >= P.MIN_PIECE - 1e-6 for p in b["pieces"])
        clip_len = sum(p["end_sec"] - p["start_sec"] for p in b["pieces"])
        assert clip_len == pytest.approx(b["total"], abs=0.02)
    # первый кусок каждого боя начинается с начала боя (начала не режем)
    starts = {f.key: f.start for f in _fights()}
    assert all(b["pieces"][0]["start_sec"] == pytest.approx(starts[b["key"]]) for b in plan["blocks"])


def test_transitions_on_every_join_and_cut_varied_flash_on_acts():
    plan = _plan()
    kinds = [tr["kind"] for b in plan["blocks"] for tr in b["transitions"]]
    joins = [tr for b in plan["blocks"][1:] for tr in b["transitions"] if tr["where"] == "join"]
    assert len(joins) == len(plan["blocks"]) - 1
    assert all(a != b for a, b in zip(kinds, kinds[1:]))
    assert set(kinds) <= set(P.TRANSITIONS) | {"flash"}
    cuts = [tr for b in plan["blocks"] for tr in b["transitions"] if tr["where"] == "cut"]
    assert cuts and all(_on_beat(tr["final"]) for tr in cuts + joins)
    assert P.transition_kinds(4, {2})[2] == "flash"


def test_music_loops_on_bar_at_a_block_join():
    plan = _plan(usable=40.0)
    assert plan["seams"]
    joins = [b["final_at"] for b in plan["blocks"]]
    bar = 4 * PERIOD
    for s in plan["seams"]:
        assert any(abs(s - j) < 0.02 for j in joins)
        assert abs(s / bar - round(s / bar)) * bar < 0.03  # шов на такте
    segs = P.music_segments(plan["total"], plan["seams"], 0.755, PERIOD, 40.0)
    assert segs[0]["t"] == 0 and len(segs) == len(plan["seams"]) + 1
    assert all(abs((s["pos"] - 0.755) / bar - round((s["pos"] - 0.755) / bar)) < 1e-3 for s in segs)
    assert all(s["len"] <= 40.0 + 1e-6 for s in segs[:-1])


def test_duck_curve_voice_meme_and_final_silence():
    from app.meme_edit_concat import duck_db

    grid, db = duck_db(10.0, [(1.0, 2.0)], [(5.0, 6.0)], silence=(8.0, 9.0))
    at = lambda t: float(db[int(round(t / 0.01))])  # noqa: E731
    assert at(1.5) == pytest.approx(-12, abs=0.5) and at(5.5) == pytest.approx(-16, abs=0.5)
    assert at(8.5) < -70 and abs(at(4.0)) < 0.5


def test_refine_grid_finds_tempo_and_phase():
    import numpy as np

    hop, period, phase = 0.01, 0.4348, 0.31
    onset = np.zeros(12000)
    for k in range(260):
        onset[int(round((phase + k * period) / hop))] = 1.0
    p, first = I.refine_grid(onset, hop, 0.437, 0.0, 115.0)
    assert p == pytest.approx(period, abs=0.0004) and first == pytest.approx(phase, abs=0.02)


# ------------------------------------------------------------------ сервис: файл клипа, задание, API, инструмент


@pytest.fixture
def env(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    from app.settings import settings

    ids = []
    for name, dur in (("a.mp4", 60.0), ("b.mp4", 90.0)):
        src = store.create_source("upload", settings.source_dir / name, original_filename=name, duration_sec=dur,
                                  width=1920, height=1080, status="ready")
        ids.append(int(src["id"]))
    files = tmp_path / "memes"
    files.mkdir()
    for meme in _library():
        path = files / f"m{meme['meme_id']}.mp4"
        path.write_bytes(b"0")
        asset = store.create_montage_asset(kind="video", label=meme["title"], file_path=str(path),
                                           duration_sec=meme["duration_sec"], width=640, height=360, has_audio=True)
        row = VM.create_meme(store, int(asset["id"]), tags=meme["tags"], approved=meme["meme_id"] != 6,
                             transcript_status="off")
        VM.set_transcript(store, int(row["id"]), status="done", text=meme["transcript"],
                          words=meme["transcript_words"])
    data = {ids[0]: (EVENTS_1, SHOTS_1, OWNER_1), ids[1]: (EVENTS_2, SHOTS_2, OWNER_2)}
    monkeypatch.setattr(I, "source_events", lambda _store, sid, game="apex": data[int(sid)][0])
    monkeypatch.setattr(I, "shot_spans", lambda source: data[int(source["id"])][1])
    monkeypatch.setattr(I, "track_words", lambda source, track, lo, hi: [
        w for w in data[int(source["id"])][2] if lo <= w["start"] <= hi] if track == 1 else [])
    monkeypatch.setattr(ME, "mic_track", lambda source: 1)
    monkeypatch.setattr(I, "song_grid", lambda path, start_hint=0.0: None)
    J.reset()
    monkeypatch.setattr(J, "SYNC", True)
    return client, store, ids


def test_block_specs_validate_both_formats(env):
    from app.clip_spec import validate_spec
    from app.manual_clips import create_manual_clip
    from app.meme_edit_blocks import spec_for

    _client_, store, ids = env
    edit = ME.plan_edit(store, source_ids=ids, target_sec=120, vertical=True)
    assert edit["formats"]["horizontal"]["plan"]["blocks"] and edit["formats"]["vertical"]["plan"]["blocks"]
    lib = edit["library"]
    assert 6 not in lib  # неодобренный мем не в библиотеке эдита
    base = ME._base_render(store)
    for name, fmt in edit["formats"].items():
        for i, block in enumerate(fmt["plan"]["blocks"]):
            spec = spec_for(block, fmt["aspect"], base, mic=1, library=lib, parts=fmt["parts"], index=i, count=3)
            plan = create_manual_clip(store, block["source_id"], spec["title"],
                                      [{k: p[k] for k in ("start_sec", "end_sec")} for p in spec["pieces"]])
            ops = validate_spec(store, int(plan["id"]), spec)
            render = ops["render"]
            assert render["format"] == fmt["aspect"] and render["music_on"] is False and render["end_hold_sec"] == 0
            for ins in render["inserts"]:
                assert ins["mode"] == "full" and ins.get("parts") and ins.get("level") == "speech"
                for track in (0, 1):
                    assert any(m.get("track") == track and m["start"] <= ins["at"] + 0.01
                               and m["end"] >= ins["at"] + ins["duration"] - 0.01 for m in render["mute"])
            caps = [layer for layer in render["layers"] if layer["type"] == "captions"]
            assert caps and caps[0]["props"]["color"].upper() == "#FFFFFF"
            sfx = [layer for layer in render["layers"] if layer["type"] == "audio"
                   and str(layer["props"].get("asset_id", "")).startswith("sfx:freesound/")]
            assert len(sfx) >= len(block["transitions"])


def test_team_swears_muted_whole_on_game_track():
    from app.meme_edit_blocks import mutes

    block = {"inserts": [], "words": [{"word": "сука", "start": 2.0, "end": 2.4, "speaker": "B"},
                                      {"word": "сука", "start": 5.0, "end": 5.4, "speaker": "A"}]}
    out = mutes(block, 1)
    assert out == [{"start": 1.97, "end": 2.43, "track": 0, "by": "meme_edit:team"}]


def test_job_dry_run_and_blocks_without_render(env):
    _client_, store, ids = env
    job = J.start(store, source_ids=ids, target_sec=120, dry_run=True)
    out = J.status(job["job_id"])
    assert out["status"] == "done", out.get("error")
    assert out["plan"]["formats"]["horizontal"]["blocks"] and out["plan"]["formats"]["vertical"]["blocks"]
    job = J.start(store, source_ids=ids, target_sec=120, render=False, formats=["horizontal"])
    st = J.status(job["job_id"])
    assert st["status"] == "done", st.get("error")
    items = st["blocks"]["horizontal"]
    assert items and "vertical" not in st["blocks"]
    for item in items:
        plan = store.get_clip_plan(item["clip_id"], include_segments=False)
        assert plan["hidden"]
        subs = store.get_clip_plan_subtitles(item["clip_id"]) or {}
        assert subs.get("words") and all(w.get("speaker") in ("A", "B") for w in subs["words"])
    J.reset()
    assert J.status(job["job_id"])["status"] == "done"  # с диска


def test_api_and_tool_start_and_status(env):
    client, store, ids = env
    res = client.post("/api/meme-edit", json={"source_ids": ids, "dry_run": True, "target_sec": 120})
    assert res.status_code == 200, res.text
    job_id = res.json()["job_id"]
    body = client.get(f"/api/meme-edit/jobs/{job_id}").json()
    assert body["status"] == "done", body.get("error")
    assert client.post(f"/api/sources/{ids[0]}/meme-edit", json={"dry_run": True}).status_code == 200
    assert client.get("/api/meme-edit/jobs/nope").status_code == 404
    out, ok = run_tool(ToolContext(store), "meme_edit", {"source_ids": ids, "dry_run": True}, validate=True)
    assert ok and out["job_id"].startswith("me"), out
    out, ok = run_tool(ToolContext(store), "meme_edit", {"job_id": out["job_id"]}, validate=True)
    assert ok and out["status"] in ("queued", "running", "done")
    out, ok = run_tool(ToolContext(store), "meme_edit", {})
    assert not ok and "записи" in out["error"]


# ------------------------------------------------------------------ кадр мема: поля и подрезка (app/meme_frame.py)


def test_cropdetect_parse_widest_box_and_noise():
    from app.meme_frame import parse_cropdetect

    log = "[Parsed_cropdetect_0] x1:0 crop=1280:552:0:84\n[Parsed_cropdetect_0] crop=1276:548:2:86\n"
    assert parse_cropdetect(log, 1280, 720) == {"x": 0.0, "y": 0.1167, "w": 1.0, "h": 0.7667}
    assert parse_cropdetect("crop=1280:716:0:2", 1280, 720) is None
    assert parse_cropdetect("crop=100:80:600:300", 1280, 720) is None


def test_framing_cover_when_cheap_blur_when_too_much_lost():
    from app.meme_frame import framing

    crop, fill = framing({"x": 0.125, "y": 0.0, "w": 0.75, "h": 1.0}, 1920, 1080, 1920, 1080)
    assert fill == "cover" and crop["x"] == 0.125 and crop["w"] == 0.75 and crop["h"] == pytest.approx(0.75)
    crop, fill = framing({"x": 0.0, "y": 0.12, "w": 1.0, "h": 0.76}, 1280, 720, 1080, 1920, face=(0.7, 0.5))
    assert fill == "blur" and crop["w"] == pytest.approx(0.55) and crop["y"] == 0.12 and crop["h"] == 0.76
    assert framing(None, 900, 1400, 1080, 1920)[1] == "cover"
    assert framing(None, 1920, 1080, 1920, 1080) == (None, "cover")


def test_insert_crop_reaches_the_render_graph():
    from app.montage_assets import full_insert_chain, normalize_inserts

    [ins] = normalize_inserts([{"asset_id": 1, "at": 1, "duration": 2, "fill": "cover",
                                "crop": {"x": 0.1, "y": 0.2, "w": 0.5, "h": 0.6}}])
    chain = full_insert_chain("[1:v]", 1080, 1920, "cover", "1", crop=ins["crop"])
    assert chain.startswith("[1:v]crop=trunc(iw*0.5000/2)*2:trunc(ih*0.6000/2)*2:") and "scale=1080:1920" in chain
