"""«Мувик под песню» как работа сервиса: текст песни по слогам (app/lyrics_align.py, app/lyrics_audio.py), слой строк
(app/lyrics_layers.py, инструменты app/lyrics_tools.py), настройки работы проекта и старт из «Нового видео»
(app/frag_movie.py), закреплённый плейбук переписки, подписи частей и метаданных, пресет анализа «Фраг-мувик».

Whisper, demucs и Gemini не запускаются: слова «услышанного» — синтетика, звук — синтетический wav."""

from __future__ import annotations

import math
import wave
from pathlib import Path

import pytest

from app import frag_movie as F
from app import lyrics_align as LA
from app.lyrics_layers import build_lyric_layers

ROOT = Path(__file__).resolve().parents[1]


def _heard(pairs, start=10.0, step=0.4):
    out, t = [], start
    for word in pairs:
        if word is None:  # пауза
            t += 1.5
            continue
        out.append({"w": word, "s": round(t, 3), "e": round(t + step * 0.8, 3), "p": 0.95})
        t += step
    return out


# ---------------------------------------------------------------- текст владельца


def test_parse_lyrics_sections_repeats_and_punctuation():
    lines = LA.parse_lyrics("[Verse 1]\nHey, hey — hey!\nWill I feel safe? (x2)\n\nПрипев:\nНу и всё ×2\n")
    assert [l["text"] for l in lines] == ["Hey, hey — hey!", "Will I feel safe?", "Will I feel safe?",
                                          "Ну и всё", "Ну и всё"]
    assert lines[0]["words"] == ["Hey,", "hey —", "hey!"] and lines[0]["norm"] == ["hey", "hey", "hey"]
    assert lines[3]["stanza"] == 1 and lines[1]["stanza"] == 0
    assert LA.norm_word("Don't") == "dont" and LA.norm_word("Ёлка") == "елка"
    assert LA.syllables("together") == 3


def test_align_text_matches_typos_fills_gaps_and_flags_unsure_lines():
    lines = LA.parse_lyrics("stay together tonight\nwe never fall apart\nla la ignored words here")
    heard = _heard(["stay", "togather", "tonight", None, "we", "fall", "apart", "yeah"])
    out = LA.align_text(lines, heard)
    first, second, third = out["lines"]
    assert [w["how"] for w in first["words"]] == ["heard", "heard", "heard"]  # «togather» — то же слово
    assert first["words"][1]["t0"] == pytest.approx(10.4)
    never = second["words"][1]
    assert never["how"] == "interp" and second["words"][0]["t1"] <= never["t0"] <= second["words"][2]["t0"]
    assert third["unsure"] is True and all(w["how"] == "interp" for w in third["words"])
    assert out["stats"]["unsure_lines"] == 1 and out["stats"]["whisper_unused"] >= 1  # «yeah» лишнее


def test_lines_from_heard_split_on_pauses_and_punctuation():
    heard = _heard(["one", "two.", "three", None, "four", "five"])
    out = LA.lines_from_heard(heard)
    assert [l["text"] for l in out["lines"]] == ["one two", "three", "four five"]
    assert not any(l["unsure"] for l in out["lines"])


def test_vocal_envelope_finds_syllable_attacks_and_fixes_silent_starts():
    sr = 22050
    import numpy as np

    x = np.zeros(int(sr * 4.0), dtype=np.float32)
    for at in (1.0, 1.5, 2.6):  # три «слога»: тон 400 Гц по 0.3 с
        n0 = int(at * sr)
        t = np.arange(int(0.3 * sr)) / sr
        x[n0:n0 + len(t)] = 0.5 * np.sin(2 * math.pi * 400 * t) * np.hanning(len(t)) ** 0.2
    env = LA.vocal_envelope(x, sr, t0=100.0)
    times = [o[0] for o in env["onsets"]]
    for at in (101.0, 101.5, 102.6):
        assert any(abs(t - at) < 0.05 for t in times), (at, times)
    # Whisper растянул первое слово на паузу: 100.3–101.3 → начало переносится туда, где голос зазвучал.
    lines = [{"i": 0, "text": "go go now", "words": [
        {"w": "go", "t0": 100.3, "t1": 101.3, "how": "heard"}, {"w": "go", "t0": 101.45, "t1": 101.8, "how": "heard"},
        {"w": "now", "t0": 102.64, "t1": 102.9, "how": "heard"}]}]
    fixed = LA.fix_silent_starts(lines, env)
    assert fixed["moved"] >= 1 and lines[0]["words"][0]["t0"] == pytest.approx(101.0, abs=0.05)
    assert lines[0]["words"][2]["t0"] == 102.64  # начало уже в голосе — не трогаем
    snap = LA.snap_to_onsets(lines, env["onsets"])
    assert snap["snapped"] == 3
    assert [w["t0"] for w in lines[0]["words"]] == pytest.approx([101.0, 101.5, 102.6], abs=0.04)
    assert lines[0]["t0"] == lines[0]["words"][0]["t0"]


# ---------------------------------------------------------------- слой строк


def _line(i, words, t0, step=0.3, unsure=False):
    ws = [{"w": w, "t0": round(t0 + k * step, 3), "t1": round(t0 + k * step + 0.25, 3), "how": "heard"}
          for k, w in enumerate(words)]
    return {"i": i, "text": " ".join(words), "words": ws, "t0": ws[0]["t0"], "t1": ws[-1]["t1"], "unsure": unsure}


def test_lyric_layers_only_in_pauses_before_cuts_and_skip_unsure():
    lines = [_line(0, ["STAY", "TOGETHER"], 12.0), _line(1, ["WE", "RUN"], 15.0), _line(2, ["ALL", "NIGHT"], 18.0),
             _line(3, ["NEVER", "STOP"], 22.0), _line(4, ["WHAT", "WAS", "THAT"], 25.0, unsure=True),
             _line(5, ["LAST", "ONE"], 29.0)]
    out = build_lyric_layers(lines, song_offset=10.0, duration=40.0, fps=30, busy=[{"start": 4.6, "end": 6.0}],
                             avoid=[[8.0, 8.4]], cuts=[12.5, 30.0])
    placed = {p["i"]: p for p in out["placed"]}
    why = {s["i"]: s["why"] for s in out["skipped"]}
    assert set(placed) == {0, 5}
    assert "стрельба" in why[1] and "реплика" in why[2] and "склейка" in why[3] and "сомнительно" in why[4]
    assert placed[0]["end"] == pytest.approx(3.0)  # последнее слово 2.55 + 0.45
    first = [l for l in out["layers"] if l["id"].startswith("lyr_0_")]
    assert first and first[0]["start"] == pytest.approx(2.0 - 0.04) and first[0]["props"]["font"] == "Oswald"
    assert first[0]["props"]["uppercase"] is True and first[0]["in"]["type"] == "words"
    assert out["zone"] == "bottom" and 0.6 < first[0]["y"] < 0.8


def test_lyric_line_crossing_a_cut_ends_three_frames_before_it_or_is_skipped():
    line = _line(0, ["HEY", "HEY", "HEY"], 10.2, step=0.4)  # 0.2 … 1.25 с ролика при song_offset 10
    early = build_lyric_layers([line], song_offset=10.0, duration=20.0, fps=30, cuts=[1.3])
    assert early["placed"] and early["layers"][0]["end"] == pytest.approx(1.3 - 0.1)
    mid = build_lyric_layers([line], song_offset=10.0, duration=20.0, fps=30, cuts=[0.6])
    assert not mid["placed"] and "склейка" in mid["skipped"][0]["why"]
    wide = build_lyric_layers([line], song_offset=10.0, duration=20.0, ref=(1920.0, 1080.0))
    assert wide["zone"] == "left" and wide["layers"][0]["x"] == pytest.approx(0.27)


# ---------------------------------------------------------------- подписи и настройки работы


def test_caption_template_small_caps_and_parts():
    assert F.small_caps("Stay Together") == "ꜱᴛᴀʏ ᴛᴏɢᴇᴛʜᴇʀ"
    assert F.song_title("lolwtfitsjake - Stay Together.wav") == "Stay Together"
    assert F.song_title("RUZLXN — BURN YOUR LOCAL POLITICIAN!") == "BURN YOUR LOCAL POLITICIAN"
    assert F.render_caption(F.CAPTION_TEMPLATE, "lolwtfitsjake - Stay Together", 2, 3) == \
        "ʏᴀᴋᴏᴏᴅᴇᴠ ⨯ ᴀᴘᴇx ⨯ ꜱᴛᴀʏ ᴛᴏɢᴇᴛʜᴇʀ · 2/3"
    assert F.render_caption(F.CAPTION_TEMPLATE, "Stay Together") == "ʏᴀᴋᴏᴏᴅᴇᴠ ⨯ ᴀᴘᴇx ⨯ ꜱᴛᴀʏ ᴛᴏɢᴇᴛʜᴇʀ"
    assert F.render_caption(F.CAPTION_TEMPLATE, "", 1, 2) == "ʏᴀᴋᴏᴏᴅᴇᴠ ⨯ ᴀᴘᴇx · 1/2"
    wf = F.normalize_workflow({"kind": "frag_movie"})
    assert F.caption_text(wf, "Stay Together", 1, 2).splitlines()[1] == \
        "#apex #apexlegends #апекс #мувик #fragmovie #gaming"
    assert wf["tiktok_parts"] == {"max_song_sec": 58.0, "equal": True, "label": "Часть {n}/{m}",
                                  "end_text": "Часть {next} → в профиле"} and wf["freeze_check"] is True
    assert F.normalize_workflow({"kind": "other"}) == {} and F.normalize_workflow(None) == {}
    odd = F.normalize_workflow({"kind": "frag_movie", "hashtags": ["Apex", "#мувик", "bad tag!"],
                                "tiktok_parts": {"max_song_sec": 99}})
    assert odd["hashtags"] == ["#apex", "#мувик", "#badtag"] and odd["tiktok_parts"]["max_song_sec"] == 60.0


@pytest.fixture
def api(tmp_path, monkeypatch):
    from app import projects as P
    from app.settings import settings
    from test_api_pipeline import _client

    client, store = _client(tmp_path, monkeypatch)
    monkeypatch.setattr("app.ai.editor_agent.client_factory", None)
    rec = store.create_source("upload", settings.source_dir / "apex1.mp4", original_filename="apex1.mp4",
                              duration_sec=1200, status="ready")
    other = store.create_source("upload", settings.source_dir / "x.mp4", original_filename="x.mp4", duration_sec=60)
    pid = P.create_project(store, "Apex", kind="game_channel", source_ids=[rec["id"]])["id"]
    song = settings.audio_dir / "song.wav"
    song.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(song), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(8000), w.writeframes(b"\0\0" * 8000)
    track = store.create_audio_track("lolwtfitsjake - Stay Together", song, duration_sec=1.0)
    return client, store, pid, rec, other, track


def test_card_starts_chat_with_pinned_playbook_task_lyrics_and_project_settings(api):
    from app import project_chat as C
    from app.autopilot_config import get_config

    client, store, pid, rec, other, track = api
    bad = client.post(f"/api/projects/{pid}/frag-movie", json={"title": "x", "source_ids": [other["id"]],
                                                                "track_id": track["id"], "run_agent": False})
    assert bad.status_code == 400 and "не из этого проекта" in bad.text
    assert client.post(f"/api/projects/{pid}/frag-movie",
                       json={"title": "x", "source_ids": [rec["id"]], "run_agent": False}).status_code == 400
    res = client.post(f"/api/projects/{pid}/frag-movie", json={
        "title": "Stay Together", "source_ids": [rec["id"]], "track_id": track["id"], "lang": "en",
        "lyrics": "Stay together\nWe never fall apart", "run_agent": False})
    assert res.status_code == 200, res.text
    body = res.json()
    thread = C.get_thread(store, body["thread_id"])
    assert thread["playbook"] == "frag-movie" and thread["project_id"] == pid and body["to"] == f"/chat/{thread['id']}"
    assert "frag-movie" in body["task"] and f"align_lyrics(track_id: {track['id']}" in body["task"]
    assert f"#{rec['id']}" in body["task"] and "заставка с логотипом" in body["task"]
    assert F.track_lyrics(store, track["id"]) == {"text": "Stay together\nWe never fall apart", "lang": "en"}
    assert get_config(store, pid)["workflow"]["kind"] == "frag_movie"
    assert body["caption_example"].startswith("ʏᴀᴋᴏᴏᴅᴇᴠ ⨯ ᴀᴘᴇx ⨯ ꜱᴛᴀʏ ᴛᴏɢᴇᴛʜᴇʀ · 1/3")


def test_card_sends_the_first_task_to_the_agent(api, monkeypatch):
    from app import project_chat as C

    client, store, pid, rec, _other, track = api
    sent = {}
    monkeypatch.setattr(C, "send_to_thread", lambda store, tid, text, **kw: sent.update(tid=tid, text=text, **kw))
    res = client.post(f"/api/projects/{pid}/frag-movie",
                      json={"title": "Мувик", "source_ids": [rec["id"]], "track_id": track["id"]})
    assert res.status_code == 200 and res.json()["started"] is True
    assert sent["tid"] == res.json()["thread_id"] and sent["attachments"] == [{"kind": "source", "id": rec["id"]}]
    assert "текста нет" in sent["text"]


def test_pinned_and_keyword_playbook_pick():
    from app import project_chat_playbook as PB

    assert PB.pick("ок, делай полную версию", pinned="frag-movie") == "frag-movie"
    assert PB.pick("ок, делай полную версию") is None
    assert PB.pick("сделай мувик из вчерашних записей под мою песню") == "frag-movie"
    assert PB.pick("мувик с мемами") != "frag-movie"
    assert PB.pick("по плейбуку stream-shorts", pinned="frag-movie") == "stream-shorts"  # названный — сильнее


# ---------------------------------------------------------------- инструменты


def test_align_lyrics_tool_runs_in_background_caches_and_uses_saved_lyrics(api, monkeypatch):
    from app import lyrics_audio as A
    from app.agent_tools import ToolContext, get_tool, run_tool
    from app.modules import module_of_tool

    _client, store, _pid, _rec, _other, track = api
    F.save_track_lyrics(store, track["id"], "stay together", "en")
    calls = []

    def fake_align(path, lyrics="", lang="", start=0.0, end=None, model=None, hint=True, progress=None):
        calls.append((lyrics, lang))
        res = LA.align_text(LA.parse_lyrics(lyrics), _heard(["stay", "together"], start=1.0))
        return {"version": 1, "time": "track", "lang": lang, "model": model, "method": "band", "with_text": True,
                "lines": res["lines"], "heard": [], "stats": res["stats"], "notes": [], "elapsed_sec": 0.1}

    monkeypatch.setattr(A, "align_audio", fake_align)
    ctx = ToolContext(store=store)
    out, ok = run_tool(ctx, "align_lyrics", {"track_id": track["id"], "wait_sec": 10})
    assert ok, out
    assert out["status"] == "done" and calls == [("stay together", "en")] and "сохранённый" in out["notes"][0]
    assert out["lines"][0]["at"] == [1.0, 1.4] and out["lyrics_id"]
    again, ok = run_tool(ctx, "align_lyrics", {"track_id": track["id"]})
    assert ok and again["cached"] is True and len(calls) == 1
    assert A.load_result(out["lyrics_id"])["lines"][0]["text"] == "stay together"
    bad, ok = run_tool(ctx, "align_lyrics", {"audio_path": str(ROOT / "README.md")})
    assert not ok and "данных сервиса" in bad["error"]
    assert module_of_tool("align_lyrics") == "frag_movie" and get_tool("align_lyrics").mutating is False
    assert get_tool("lyric_layers").mutating is True and get_tool("lyric_layers").scope == "clip"


def test_lyric_layers_tool_places_and_replaces_layers(tmp_path, monkeypatch):
    from app import lyrics_audio as A
    from app.agent_tools import ToolContext, run_tool
    from test_cuts import _two_pieces

    store, _service, plan, _preset = _two_pieces(tmp_path, monkeypatch)  # 10 + 10 с, наплыв 0.5 → 19.5 с
    key = "abc123"
    lines = [_line(0, ["STAY", "TOGETHER"], 2.0), _line(1, ["WE", "RUN"], 9.0), _line(2, ["ALL", "NIGHT"], 14.0)]
    (A.cache_dir() / f"{key}.json").write_text(__import__("json").dumps({"lines": lines}), encoding="utf-8")
    ctx = ToolContext(store=store, clip_plan_id=plan["id"])
    dry, ok = run_tool(ctx, "lyric_layers", {"lyrics_id": key, "dry_run": True, "busy": []})
    assert ok, dry
    assert dry["dry_run"] and dry["placed"] == 2 and "склейка" in dry["skipped"][0]["why"]  # строка 1 — на стыке 9.5
    out, ok = run_tool(ctx, "lyric_layers", {"lyrics_id": key, "busy": []})
    assert ok, out
    ids = [l["id"] for l in store.get_clip_plan(plan["id"])["render_settings"]["layers"]]
    assert ids and all(i.startswith("lyr_") for i in ids) and out["layers_added"] == len(ids)
    again, ok = run_tool(ctx, "lyric_layers", {"lyrics_id": key, "busy": [], "only": [0]})
    assert ok and again["replaced"] == len(ids)
    assert all(l["id"].startswith("lyr_0_") for l in store.get_clip_plan(plan["id"])["render_settings"]["layers"])
    missing, ok = run_tool(ctx, "lyric_layers", {"lyrics_id": "nope"})
    assert not ok and "align_lyrics" in missing["error"]


def test_tiktok_parts_take_project_settings_and_captions(api, monkeypatch):
    from app import projects as P
    from app.agent_tools import ToolContext, run_tool
    from test_clip_groups import add_plan

    _client, store, pid, rec, _other, track = api
    F.set_workflow(store, pid)
    plan = add_plan(store, rec, "Мувик", start=0, end=100)
    store.set_clip_plan_render_settings(plan["id"], {"music_on": True, "track_id": track["id"]})
    assert P.project_of_plan(store, plan["id"]) == pid
    out, ok = run_tool(ToolContext(store=store, clip_plan_id=plan["id"]), "plan_tiktok_parts",
                       {"bpm": 120, "first_beat": 0.0, "song_start": 0, "song_end": 100, "events": []})
    assert ok, out
    assert out["count"] == 2 and [p["label"] for p in out["parts"]] == ["Часть 1/2", "Часть 2/2"]
    assert abs(out["parts"][0]["duration"] - out["parts"][1]["duration"]) <= 1.0  # одной длины
    assert out["parts"][0]["caption"].splitlines()[0] == "ʏᴀᴋᴏᴏᴅᴇᴠ ⨯ ᴀᴘᴇx ⨯ ꜱᴛᴀʏ ᴛᴏɢᴇᴛʜᴇʀ · 1/2"


def test_publish_meta_follows_project_caption_template(api):
    from app.ai.publish_meta import apply_workflow_preset
    from test_clip_groups import add_plan, add_render

    _client, store, pid, rec, _other, track = api
    plan = add_plan(store, rec, "Мувик · часть 2/3")
    store.set_clip_plan_render_settings(plan["id"], {"music_on": True, "track_id": track["id"]})
    clip = add_render(store, plan)
    meta = {"title": "t", "description": "", "hashtags": ["#рек"], "platforms": {"tiktok": {"title": "x"}}}
    assert apply_workflow_preset(store, clip, dict(meta)) == meta  # у проекта нет работы — как есть
    F.set_workflow(store, pid)
    got = apply_workflow_preset(store, clip, dict(meta))
    assert got["platforms"]["tiktok"] == {"title": "ʏᴀᴋᴏᴏᴅᴇᴠ ⨯ ᴀᴘᴇx ⨯ ꜱᴛᴀʏ ᴛᴏɢᴇᴛʜᴇʀ · 2/3", "description": ""}
    assert got["hashtags"][0] == "#apex"


def test_freeze_check_runs_after_render_only_for_the_workflow(api, monkeypatch):
    from app import quality_lint as Q
    from app import render_jobs as RJ
    from test_clip_groups import add_plan

    _client, store, pid, rec, _other, _track = api
    plan = add_plan(store, rec, "Мувик")
    assert RJ.workflow_freeze_check(store, plan["id"]) is None
    F.set_workflow(store, pid)
    monkeypatch.setattr(Q, "lint_clip", lambda store, cid, checks=None, media=None: {"findings": [
        {"check": "freezes", "severity": "warn", "message": "стоит 0.9 с", "final_t": 4.0}]})
    got = RJ.workflow_freeze_check(store, plan["id"])
    assert got["ok"] is False and got["count"] == 1 and "перерендерь" in got["note"]


# ---------------------------------------------------------------- пресет анализа и карточка


def test_frag_movie_analysis_preset_reaches_old_installs_without_touching_owner_edits(tmp_path, monkeypatch):
    import app.db as db_module
    from app.db import Database
    from app.default_prompts import (DEFAULT_PROMPT_SEED_KEY, FRAG_MOVIE_ANALYSIS_PROMPT, analysis_mode_instructions,
                                     analysis_output_rules)

    db = Database(tmp_path / "app.sqlite")
    db.init()
    row = db.query_one("SELECT prompt, is_default FROM prompt_presets WHERE task = 'analysis' AND label = 'Фраг-мувик'")
    assert row["prompt"] == FRAG_MOVIE_ANALYSIS_PROMPT and not row["is_default"]
    for words in ("squad wipe", "clutch", "ziplines", "knocked enemy", "meaningful", "emotional"):
        assert words in FRAG_MOVIE_ANALYSIS_PROMPT
    # Старая установка (v23, пресета нет) с правкой владельца в «Apex analysis»: новый пресет приходит, правка цела.
    db.execute("DELETE FROM prompt_presets WHERE label = 'Фраг-мувик'")
    db.execute("UPDATE prompt_presets SET prompt = prompt || ' мой текст' WHERE label = 'Apex analysis'")
    db.execute("UPDATE app_settings SET value_json = ? WHERE key = ?", ('"20261116-prompts-v23"', DEFAULT_PROMPT_SEED_KEY))
    db.init()
    assert db.query_one("SELECT 1 FROM prompt_presets WHERE label = 'Фраг-мувик'")
    assert db.query_one("SELECT prompt FROM prompt_presets WHERE label = 'Apex analysis'")["prompt"].endswith("мой текст")
    assert db_module.DEFAULT_PROMPT_SEED_VERSION == "20261116-prompts-v24"
    # Режим «список боёв»: свой контракт без порога 30 с.
    assert "no 30-second" in analysis_output_rules(FRAG_MOVIE_ANALYSIS_PROMPT)
    assert "one clip per fight" in analysis_mode_instructions(FRAG_MOVIE_ANALYSIS_PROMPT)
    assert "Length floor" in analysis_output_rules("Analyze this source")


def test_new_video_card_is_wired():
    page = (ROOT / "frontend" / "src" / "pages" / "release" / "NewVideoPage.tsx").read_text(encoding="utf-8")
    assert 'key: "frag"' in page and 'title: "Мувик под песню"' in page and 'module: "frag_movie"' in page
    api = (ROOT / "frontend" / "src" / "api" / "releases.ts").read_text(encoding="utf-8")
    assert "/frag-movie" in api
    kinds = (ROOT / "frontend" / "src" / "api" / "projects.ts").read_text(encoding="utf-8")
    assert '"game_channel"' in kinds
    playbook = (ROOT / "docs" / "playbooks" / "frag-movie.md").read_text(encoding="utf-8")
    assert "align_lyrics" in playbook and "lyric_layers" in playbook and "заставка с логотипом" in playbook.lower()
