"""Отзыв агентов-монтажёров стримов Twitch и игровых записей Apex с голосом владельца.

Звук: громкость каждой дорожки, своя дорожка для распознавания, громкость исходника по
отрезкам. Время: одна шкала clip (с наплывами) для слоёв, вырезов и слов; final — готовый
ролик. Чат Twitch в MCP. Раскладка split, подсветка слов по слову, переход не на речь,
пустая статистика, покадровый лист исходника, сверка субтитров по речи, цвет подсветки бренда.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.agent_tools import ToolContext, run_tool
from app.clip_history import snapshot
from app.timebase import ClipClock
from test_cuts import FADE, _mock_render, _two_pieces


def _ctx(store, plan_id=None):
    return ToolContext(store=store, clip_plan_id=plan_id,
                       on_first_change=(lambda: snapshot(store, plan_id, "тест")) if plan_id else None)


TWO_TRACKS = {"streams": [{"codec_type": "video"}, {"codec_type": "audio", "tags": {"title": "Игра"}},
                          {"codec_type": "audio", "tags": {"title": "Микрофон"}}]}


# ------------------------------------------------------------------ шкалы времени (п. 3, 13)


def test_clip_clock_translates_between_scales():
    segs = [{"start_sec": 0, "end_sec": 10}, {"start_sec": 30, "end_sec": 40}]
    clock = ClipClock(segs, FADE)
    assert clock.clip_starts == [0.0, 9.5] and clock.clip_duration == 19.5 == clock.final_duration
    # «два» на 12.0 кусков подряд — на шкале clip (с наплывом) 11.5, и обратно.
    assert clock.plain_to_clip(12.0) == 11.5 and clock.clip_to_plain(11.5) == 12.0
    # Зона наплыва 9.5–10.0 — два куска сразу: без подсказки — следующий, с piece — свой.
    assert clock.pieces_at_clip(9.8) == [0, 1]
    assert clock.clip_to_plain(9.8) == 10.3 and clock.clip_to_plain(9.8, piece=0) == 9.8
    # Отрезок через стык — две части, у каждой свой кусок.
    assert clock.plain_range_to_clip(9.6, 10.4) == [{"start": 9.6, "end": 10.0, "piece": 0},
                                                   {"start": 9.5, "end": 9.9, "piece": 1}]
    cut = ClipClock(segs, FADE, [{"start": 2.0, "end": 3.0}])
    assert cut.final_duration == 18.5
    assert cut.clip_to_final(5.0) == 4.0 and cut.final_to_clip(4.0) == 5.0
    assert cut.final_to_clip(10.0) == 11.0  # после выреза и наплыва


def test_get_clip_words_round_trip_through_set_subtitle_words(tmp_path, monkeypatch):
    """Слова из get_clip (шкала clip) обратно как есть — на месте, а не на 0.5 с раньше; голос s/speaker."""
    store, _service, plan, _preset = _two_pieces(tmp_path, monkeypatch)
    clip, ok = run_tool(_ctx(store, plan["id"]), "get_clip", {})
    assert ok, clip
    words = [w for line in clip["subtitles"]["lines"] for w in line["words"]]
    assert ["два", 11.5, 12.0] in words  # get_clip: шкала clip
    sent = [{"word": "раз", "start": 5.0, "end": 5.5},
            {"word": "два", "start": 11.5, "end": 12.0, "speaker": "B"}]
    out, ok = run_tool(_ctx(store, plan["id"]), "set_subtitle_words", {"words": sent})
    assert ok, out
    assert out["time"] == "clip" and out["speakers"] == ["B"]
    stored = store.get_clip_plan_subtitles(plan["id"])["words"]
    assert [(w["word"], w["start"], w.get("speaker")) for w in stored] == [("раз", 5.0, None), ("два", 12.0, "B")]
    again = run_tool(_ctx(store, plan["id"]), "get_clip", {})[0]
    assert ["два", 11.5, 12.0, "B"] in [w for line in again["subtitles"]["lines"] for w in line["words"]]


def test_cuts_in_clip_scale_round_trip(tmp_path, monkeypatch):
    store, _service, plan, _preset = _two_pieces(tmp_path, monkeypatch)
    # Вырез на шкале clip (как слои): 11.0–11.4 = кусок 1, кусками подряд 11.5–11.9.
    out, ok = run_tool(_ctx(store, plan["id"]), "apply_cuts", {"cuts": [{"start": 11.0, "end": 11.4}]})
    assert ok, out
    assert out["time"] == "clip" and out["finished_duration"] == pytest.approx(19.1, abs=0.05)
    stored = store.get_clip_plan(plan["id"])["render_settings"]["cuts"]
    assert stored[0]["start"] == pytest.approx(11.5) and stored[0]["end"] == pytest.approx(11.9, abs=0.04)
    clip = run_tool(_ctx(store, plan["id"]), "get_clip", {})[0]
    assert clip["time"] == "clip" and "final" in clip["time_scales"]
    assert clip["cuts"][0]["start"] == pytest.approx(11.0) and clip["cuts"][0]["piece"] == 1
    # Вырез в зоне наплыва с piece — в свой кусок (хвост первого).
    out, ok = run_tool(_ctx(store, plan["id"]), "apply_cuts", {"cuts": [{"start": 9.6, "end": 9.9, "piece": 0}]})
    assert ok, out
    assert store.get_clip_plan(plan["id"])["render_settings"]["cuts"][0]["start"] == pytest.approx(9.6)


def test_suggest_cuts_come_back_in_clip_scale(tmp_path, monkeypatch):
    store, _service, plan, _preset = _two_pieces(tmp_path, monkeypatch)
    store.set_clip_plan_subtitles(plan["id"], {
        "words": [{"word": w, "start": 10.5 + i * 0.4, "end": 10.8 + i * 0.4} for i, w in enumerate(["раз", "два"])]
        + [{"word": "три", "start": 12.7, "end": 13.0}, {"word": "четыре", "start": 13.1, "end": 13.4}],
        "lines": [{"start": 10.5, "end": 13.4, "text": "раз два три четыре"}]})
    out, ok = run_tool(_ctx(store, plan["id"]), "suggest_cuts", {"mode": "pauses"})
    assert ok, out
    assert out["time"] == "clip"
    pause = next(c for c in out["cuts"] if c["start"] > 10)
    # Пауза 11.2–12.7 кусков подряд (минус поля) — на шкале clip на 0.5 с раньше.
    assert pause["start"] == pytest.approx(11.2 + 0.12 - 0.5, abs=0.02)
    assert run_tool(_ctx(store, plan["id"]), "apply_cuts", {"cuts": out["cuts"]})[1]
    stored = store.get_clip_plan(plan["id"])["render_settings"]["cuts"]
    assert any(c["start"] == pytest.approx(11.32, abs=0.04) for c in stored)


def test_listen_uses_the_finished_timeline_with_overlaps(tmp_path, monkeypatch):
    """listen: последние 6 с ГОТОВОГО ролика (с наплывом), слова — там же; before_cuts — шкала clip."""
    from app.subtitles.contracts import SubtitleResult, SubtitleWord

    store, _service, plan, _preset = _two_pieces(tmp_path, monkeypatch)
    seen = []
    monkeypatch.setattr("app.render._run_ffmpeg", lambda args, timeout=0: seen.append(args))

    class Whisper:
        def transcribe(self, *_a):
            return SubtitleResult(text="да", language="ru", duration=1.0, segments=[],
                                  words=[SubtitleWord(word="да", start=0.5, end=0.9)])

    monkeypatch.setattr("app.clip_subtitles.get_subtitle_provider", lambda name: Whisper())
    out, ok = run_tool(_ctx(store, plan["id"]), "listen", {})
    assert ok, out
    assert out["time"] == "final" and out["clip_duration"] == 19.5 and (out["from"], out["to"]) == (13.5, 19.5)
    assert out["words"] == [["да", 14.0, 14.4]]  # не 14.5: наплыв учтён
    assert [seen[-1][i + 1] for i, a in enumerate(seen[-1]) if a == "-ss"] == ["34.000"]
    out, ok = run_tool(_ctx(store, plan["id"]), "listen", {"from_sec": 11.5, "to_sec": 13.5, "before_cuts": True})
    assert ok and out["time"] == "clip" and out["words"] == [["да", 12.0, 12.4]]


def test_contact_sheet_frames_follow_the_overlap(tmp_path, monkeypatch):
    from app import motion_render

    store, _service, plan, _preset = _two_pieces(tmp_path, monkeypatch, {"cuts": [{"start": 2.0, "end": 3.0}]})
    shots, labels = [], []
    monkeypatch.setattr(motion_render, "render_frame_png", lambda store, pid, t, **k: shots.append(t) or b"png")
    monkeypatch.setattr(motion_render, "tile_frames_jpeg", lambda paths, lab, width, cols=4: labels.extend(lab) or b"j")
    motion_render.contact_sheet_jpeg(store, plan["id"], times=[10.0])
    assert shots == [11.0] and labels == ["10.0 s"]  # готовый 10.0 → clip 11.0 (вырез 1 с, наплыв 0.5)
    shots.clear()
    motion_render.contact_sheet_jpeg(store, plan["id"], times=[11.0], before_cuts=True)
    assert shots == [11.0]


def test_view_frame_takes_finished_time(tmp_path, monkeypatch):
    store, _service, plan, _preset = _two_pieces(tmp_path, monkeypatch, {"cuts": [{"start": 2.0, "end": 3.0}]})
    seen = []
    monkeypatch.setattr("app.agent_tools.render_clip_frame",
                        lambda store, pid, t, with_layers=True, **_k: seen.append(t) or (b"x", "image/png", {}))
    result, ok = run_tool(_ctx(store, plan["id"]), "view_frame", {"t": 10.0, "final": True})
    assert ok and seen == [11.0] and result.info["time"] == "clip" and result.info["final_t"] == 10.0


# ------------------------------------------------------------------ звук: громкость дорожек (п. 11)


def test_track_gains_normalize_and_build_graphs():
    from app.clip_subtitles import build_clip_audio_args
    from app.source_audio import extract_args, is_default_mix, mix_chain, mix_storage, normalize_mix, normalize_tracks

    raw = [{"track": 1, "gain_db": 14}, 0, {"track": 1, "gain_db": 3}, {"track": "x"}]
    assert normalize_mix(raw) == [{"track": 0, "gain_db": 0.0}, {"track": 1, "gain_db": 14.0}]
    assert normalize_tracks(raw) == [0, 1]  # старое представление — номера
    assert normalize_mix([{"track": 1, "gain_db": 99}])[0]["gain_db"] == 30.0
    assert mix_storage(normalize_mix([0, 1])) == [0, 1]  # без усилений — прежний формат
    assert mix_storage(normalize_mix(raw)) == [0, {"track": 1, "gain_db": 14.0}] or \
        mix_storage(normalize_mix(raw)) == [{"track": 0, "gain_db": 0.0}, {"track": 1, "gain_db": 14.0}]
    assert is_default_mix(None) and is_default_mix([0]) and not is_default_mix([{"track": 0, "gain_db": 3}])
    graph = mix_chain(0, normalize_mix(raw), "a")
    assert graph == "[0:a:1]volume=14dB[a_g1];[0:a:0][a_g1]amix=inputs=2:duration=longest:dropout_transition=0[a]"
    assert mix_chain(0, [{"track": 1, "gain_db": 6}], "a") == "[0:a:1]volume=6dB[a]"
    assert mix_chain(0, [0, 1], "a") == "[0:a:0][0:a:1]amix=inputs=2:duration=longest:dropout_transition=0[a]"
    args = extract_args(Path("s.mp4"), Path("o.wav"), [{"track": 0, "gain_db": 4}])
    assert "volume=4dB" in args[args.index("-filter_complex") + 1]
    clip = build_clip_audio_args(Path("s.mp4"), [(0, 5), (1, 6)], Path("o.wav"), normalize_mix(raw))
    graph = clip[clip.index("-filter_complex") + 1]
    assert "[0:a:1]volume=14dB[p0_g1]" in graph and "[1:a:1]volume=14dB[p1_g1]" in graph


def test_set_source_audio_gains_speech_track_and_auto(tmp_path, monkeypatch):
    from app import source_audio
    from app.render import _audio_filter, _preset_with_source_audio

    store, _service, plan, _preset = _two_pieces(tmp_path, monkeypatch)
    source_id = plan["source_id"]
    store.update_source(source_id, metadata=TWO_TRACKS)
    store.set_source_transcript(source_id, [{"start": 0, "end": 1, "text": "старое"}], "small")

    out, ok = run_tool(_ctx(store), "set_source_audio",
                       {"project_id": source_id, "tracks": [0, {"track": 1, "gain_db": 14}]})
    assert ok, out
    assert out["audio"]["mix"] == [{"track": 0, "gain_db": 0.0}, {"track": 1, "gain_db": 14.0}]
    assert store.get_source_transcript(source_id) == []  # смесь для распознавания сменилась
    source = store.get_source(source_id)
    assert source["audio_tracks"] == [0, 1]  # прежнее поле — номера
    assert "volume=14dB" in _audio_filter(_preset_with_source_audio({}, source))

    # Дорожка речи: распознавание — по одному микрофону, рендер — по смеси.
    store.set_source_transcript(source_id, [{"start": 0, "end": 1, "text": "по смеси"}], "small")
    out, ok = run_tool(_ctx(store, plan["id"]), "set_source_audio", {"speech_tracks": [1]})
    assert ok, out
    assert out["audio"]["speech_tracks"] == [1] and out["audio"]["speech_separate"]
    assert store.get_source_transcript(source_id) == []
    source = store.get_source(source_id)
    assert source_audio.speech_mix(source) == [{"track": 1, "gain_db": 0.0}]
    assert source_audio.source_tracks(source) == [0, 1] and source_audio.separate_speech(source)
    # Только громкость рендера сменилась — расшифровка по микрофону остаётся.
    store.set_source_transcript(source_id, [{"start": 0, "end": 1, "text": "по микрофону"}], "small")
    run_tool(_ctx(store), "set_source_audio", {"project_id": source_id, "tracks": [0, {"track": 1, "gain_db": 10}]})
    assert store.get_source_transcript(source_id)
    assert run_tool(_ctx(store), "set_source_audio", {"project_id": source_id, "speech_tracks": [5]})[1] is False

    # «auto»: голос (тихий микрофон) — на 9 дБ над игрой в моменты речи, по замеру
    # (подробно — tests/test_auto_voice_gain.py).
    profiles = {0: {"db": [-20.0] * 40, "step": 0.25},
                1: {"db": [-60.0, -60.0, -36.0, -36.0] * 10, "step": 0.25}}
    monkeypatch.setattr(source_audio, "track_profile", lambda src, track, **k: profiles[track])
    out, ok = run_tool(_ctx(store), "set_source_audio",
                       {"project_id": source_id, "tracks": [0, {"track": 1, "gain_db": "auto"}]})
    assert ok, out
    # Цель −20 + 9 − (−36) = 25 дБ, потолок усиления — +24.
    assert out["auto_gain"] == {"1": 24.0} and out["audio"]["mix"][1]["gain_db"] == 24.0
    assert out["auto"]["1"]["voice_db"] == -36.0 and out["auto"]["1"]["others_db"] == -20.0
    # REST — то же.
    from fastapi.testclient import TestClient

    import app.main as app_main

    monkeypatch.setattr(app_main, "store", store)
    monkeypatch.setattr("app.settings.settings.auth_enabled", False)
    resp = TestClient(app_main.app).patch(f"/api/sources/{source_id}/audio", json={"speech_tracks": []})
    assert resp.status_code == 200, resp.text
    assert resp.json()["audio"]["speech_separate"] is False


def test_recognition_uses_the_speech_track(tmp_path, monkeypatch):
    from app.clip_subtitles import generate_for_plan
    from app.subtitles.contracts import SubtitleResult, SubtitleWord

    store, _service, plan, _preset = _two_pieces(tmp_path, monkeypatch)
    store.update_source(plan["source_id"], metadata=TWO_TRACKS, audio_tracks=[0, {"track": 1, "gain_db": 12}],
                        speech_tracks=[1])
    seen = []
    monkeypatch.setattr("app.render._run_ffmpeg", lambda args, timeout=0: seen.append(args))

    class Whisper:
        def transcribe(self, *_a):
            return SubtitleResult(text="да", language="ru", duration=1.0, segments=[],
                                  words=[SubtitleWord(word="да", start=0.5, end=0.9)])

    monkeypatch.setattr("app.clip_subtitles.get_subtitle_provider", lambda name: Whisper())
    monkeypatch.setattr("app.clip_subtitles._default_profile", lambda store: {"provider": "whisper"})
    generate_for_plan(store, plan["id"], provider="whisper")
    graph = seen[-1][seen[-1].index("-filter_complex") + 1]
    assert "[0:a:1]anull" in graph and "[0:a:0]" not in graph  # только микрофон, без игры


def test_track_gain_changes_real_audio(tmp_path):
    from test_api_pipeline import _require_ffmpeg
    from test_game_footage import _game_video, _rms_db

    _require_ffmpeg()
    from app.clip_subtitles import build_clip_audio_args

    video = tmp_path / "apex.mp4"
    _game_video(video)
    plain, quiet = tmp_path / "plain.wav", tmp_path / "quiet.wav"
    subprocess.run(build_clip_audio_args(video, [(0, 2)], plain, [1]), check=True)
    subprocess.run(build_clip_audio_args(video, [(0, 2)], quiet, [{"track": 1, "gain_db": -20}]), check=True)
    assert _rms_db(plain) - _rms_db(quiet) == pytest.approx(20, abs=1.5)


# ------------------------------------------------------------------ громкость исходника по отрезкам (п. 4)


def test_gain_ranges_normalize_and_expression():
    from app.audio_mute import build_source_volume_args, gain_volume_expr, normalize_gain
    from app.clip_settings import normalize_render_settings

    raw = [{"start": 3, "end": 5, "gain_db": 6}, {"start": 1, "end": 2, "gain_db": -40},
           {"start": 4, "end": 6, "gain_db": 3}, {"start": 7, "end": 7.05, "gain_db": 6},
           {"start": 8, "end": 9, "gain_db": 0}, {"start": "x", "end": 1, "gain_db": 1}, "junk"]
    assert normalize_gain(raw) == [{"start": 1.0, "end": 2.0, "gain_db": -30.0},
                                   {"start": 3.0, "end": 5.0, "gain_db": 6.0},
                                   {"start": 5.0, "end": 6.0, "gain_db": 3.0}]
    assert normalize_render_settings({"gain": raw})["gain"] == normalize_gain(raw)
    assert normalize_render_settings({})["gain"] == []
    expr = gain_volume_expr([(1.0, 2.0, 6.0)])

    def value(t):
        return eval(expr, {"clip": lambda v, lo, hi: min(hi, max(lo, v)), "min": min, "t": t})

    assert value(1.5) == pytest.approx(10 ** (6 / 20), abs=1e-3)
    assert value(0.5) == 1 and value(2.5) == 1 and 1 < value(1.02) < 1.99
    args = build_source_volume_args(Path("c.mp4"), Path("o.mp4"), [(0.0, 0.5)], [(1.0, 2.0, 6.0)])
    af = args[args.index("-af") + 1]
    assert "alimiter" in af and af.count("clip(") == 4  # тишина и громкость одним проходом
    assert build_source_volume_args(Path("c.mp4"), Path("o.mp4"), [], []) is None


def test_gain_pass_raises_only_the_range(tmp_path):
    from test_api_pipeline import _require_ffmpeg

    _require_ffmpeg()
    from app.audio_mute import build_source_volume_args
    from test_audio_mute import _rms

    src = tmp_path / "c.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=64x64:d=3",
                    "-f", "lavfi", "-i", "sine=f=440:d=3", "-af", "volume=0.05", "-shortest", "-c:v", "libx264",
                    "-c:a", "aac", str(src)], check=True)
    out = tmp_path / "g.mp4"
    subprocess.run(build_source_volume_args(src, out, [], [(1.0, 2.0, 12.0)]), check=True)
    assert _rms(out, 1.2, 0.6) - _rms(out, 0.2, 0.6) == pytest.approx(12, abs=1.5)


def test_set_source_gain_tool_and_render_pass(tmp_path, monkeypatch):
    store, service, plan, preset = _two_pieces(tmp_path, monkeypatch, {"cuts": [{"start": 2.0, "end": 3.0}]})
    out, ok = run_tool(_ctx(store, plan["id"]), "set_source_gain",
                       {"ranges": [{"start": 11.0, "end": 13.0, "gain_db": 9}]})
    assert ok, out
    assert out["gain"] == [{"start": 11.0, "end": 13.0, "gain_db": 9.0}] and out["time"] == "clip"
    assert run_tool(_ctx(store, plan["id"]), "get_clip", {})[0]["render"]["gain"] == out["gain"]
    assert run_tool(_ctx(store, plan["id"]), "set_source_gain", {"ranges": [{"start": 1, "end": 2}]})[1] is False
    # Рендер: громкость — тем же проходом, что тишина, на шкале готового ролика (вырез 1 с).
    calls = []
    monkeypatch.setattr("app.render._run_ffmpeg", lambda args, timeout=0, cwd=None: calls.append(args))
    from app.settings import settings

    clip_path = settings.clip_dir / "clip.mp4"
    clip_path.parent.mkdir(parents=True, exist_ok=True)
    clip_path.write_bytes(b"v")
    clip = store.create_clip(plan["source_id"], clip_plan_id=plan["id"], ffmpeg_preset_id=preset["id"])
    clip = store.update_clip(clip["id"], output_path=str(clip_path), status="succeeded")
    monkeypatch.setattr("pathlib.Path.replace", lambda self, target: None)
    plan = store.get_clip_plan(plan["id"])
    service._apply_mute(clip, plan, service._cut_plan(plan, preset["id"]))
    af = calls[-1][calls[-1].index("-af") + 1]
    assert "(t-10.000)" in af and "(12.000-t)" in af and "1.81838" in af


def test_gain_follows_trimmed_pieces():
    from app.piece_remap import remap_render

    old = [{"id": 1, "start_sec": 0, "end_sec": 10}, {"id": 2, "start_sec": 30, "end_sec": 40}]
    new = [{"id": 1, "start_sec": 2, "end_sec": 10}, {"id": 2, "start_sec": 30, "end_sec": 40}]
    moved, _note = remap_render({"gain": [{"start": 12.0, "end": 13.0, "gain_db": 6}],
                                 "mute": [{"start": 5.0, "end": 6.0}]}, old, new)
    assert moved["gain"] == [{"start": 10.0, "end": 11.0, "gain_db": 6}]
    assert moved["mute"] == [{"start": 3.0, "end": 4.0}]


def _run_node(script: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node не установлен")
    proc = subprocess.run([node, "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0 and "strip-types" in (proc.stderr or ""):
        pytest.skip("node без поддержки TypeScript (нужен ≥22.6)")
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_editor_gain_matches_server_and_survives_autosave():
    from app.audio_mute import gain_volume_expr, normalize_gain
    from app.settings import settings

    gain_ts = settings.root_dir / "frontend" / "src" / "api" / "gain.ts"
    cases = [[], [{"start": 3, "end": 5, "gain_db": 6}, {"start": 1, "end": 2, "gain_db": -40},
                  {"start": 4, "end": 6, "gain_db": 3}, {"start": 7, "end": 7.05, "gain_db": 6},
                  {"start": 8, "end": 9, "gain_db": 0}],
             [{"start": i, "end": i + 0.5, "gain_db": 2} for i in range(30)]]
    out = _run_node(f"""
import {{ normalizeGain, gainFactorAt, previewVolume }} from {json.dumps(gain_ts.as_uri())};
const spans = [{{ start: 1, end: 2, gain_db: 6 }}];
console.log(JSON.stringify({{
  normalized: {json.dumps(cases)}.map((c) => normalizeGain(c)),
  factor: [0.5, 1.02, 1.5, 2.5].map((t) => gainFactorAt(spans, t)),
  preview: [0.5, 1.5].map((t) => previewVolume(spans, t, 1)),
}}));
""")
    assert out["normalized"] == [normalize_gain(c) for c in cases]
    expr = gain_volume_expr([(1.0, 2.0, 6.0)])
    server = [eval(expr, {"clip": lambda v, lo, hi: min(hi, max(lo, v)), "min": min, "t": t}) for t in (0.5, 1.02, 1.5, 2.5)]
    assert out["factor"] == pytest.approx(server, abs=1e-4)
    # Превью: подъём слышен как баланс — остальное тише, кусок на полной.
    assert out["preview"][1] == pytest.approx(1.0, abs=1e-3) and out["preview"][0] == pytest.approx(10 ** (-6 / 20), abs=1e-3)
    # Редактор сохраняет настройки клипа целиком: gain обязан проходить загрузку и автосохранение.
    tab = (settings.root_dir / "frontend" / "src" / "pages" / "workspace" / "CandidatesTab.tsx").read_text(encoding="utf-8")
    assert "    mute,\n    gain,\n" in tab and "gain: s.gain ?? []," in tab and "setGain(s.gain ?? []);" in tab
    # Формат вывода (app/formats.py) — так же: в панели, при загрузке и в автосохранении
    # (старое поле aspect читается через clipFormat).
    assert "    gain,\n    format: aspect,\n" in tab and "format: clipFormat(s)," in tab and "setAspect(clipFormat(s));" in tab
    # Стоп-кадр в конце (render.end_hold_sec) — тоже: иначе автосохранение редактора стирало бы set_end_hold.
    # Формат разметки слоёв (layers_format) — так же: «Разметить слои в этом формате» сохраняет его.
    assert "    layers_format: layersFmt,\n" in tab and "layers_format: layersFormat(s)," in tab and "setLayersFmt(layersFormat(s));" in tab
    assert "    format: aspect,\n    layers_format: layersFmt,\n    end_hold_sec: endHoldSec,\n  });" in tab and "end_hold_sec: s.end_hold_sec ?? null," in tab
    assert "setEndHoldSec(s.end_hold_sec ?? null);" in tab


# ------------------------------------------------------------------ чат Twitch (п. 1)


def test_chat_tools_without_chat_say_not_to_import(tmp_path, monkeypatch):
    store, _service, plan, _preset = _two_pieces(tmp_path, monkeypatch)
    out, ok = run_tool(_ctx(store), "chat_activity", {"project_id": plan["source_id"]})
    assert ok and out["has_chat"] is False and "не импортируй" in out["note"] and "владельц" in out["note"]
    out, ok = run_tool(_ctx(store, plan["id"]), "get_chat", {"from_sec": 0, "to_sec": 60})
    assert ok and out["has_chat"] is False


def test_chat_activity_and_messages(tmp_path, monkeypatch):
    from app import twitch_chat as tc

    store, _service, plan, _preset = _two_pieces(tmp_path, monkeypatch)
    source_id = plan["source_id"]
    rows = [{"msg_id": f"q{i}", "offset_sec": i * 7.0, "user_name": "u", "text": "привет"} for i in range(80)]
    rows += [{"msg_id": f"k{i}", "offset_sec": 300 + i * 0.2, "user_name": f"v{i}", "text": "KEKW"} for i in range(60)]
    tc.insert_messages(store, source_id, rows)
    out, ok = run_tool(_ctx(store), "chat_activity", {"project_id": source_id})
    assert ok, out
    assert out["has_chat"] and out["time"] == "source" and out["bucket"] == 60.0
    assert out["windows"] and out["windows"][0]["start"] <= 300 < out["windows"][0]["end"]
    assert out["windows"][0]["top"] == "KEKW"
    near, ok = run_tool(_ctx(store), "chat_activity", {"project_id": source_id, "from_sec": 280, "to_sec": 340})
    assert ok and near["bucket"] == 10.0 and all(280 - 10 < b[0] < 340 for b in near["buckets"])
    msgs, ok = run_tool(_ctx(store), "get_chat", {"project_id": source_id, "from_sec": 300, "to_sec": 301})
    assert ok and msgs["messages"][0] == [300.0, "v0", "KEKW"] and len(msgs["messages"]) == 5
    assert run_tool(_ctx(store), "get_chat", {"project_id": source_id, "from_sec": 5, "to_sec": 1})[1] is False


# ------------------------------------------------------------------ раскладка split, хук (п. 6, 7)


def test_hook_positions_and_lint_respect_the_webcam_strip(tmp_path, monkeypatch):
    from app.agent_tools import lint_layers, text_band
    from app.auto_edit import build_auto_edit

    store, _service, plan, _preset = _two_pieces(tmp_path, monkeypatch)
    split = {"mode": "split", "cam_pos": "top", "ratio": 0.38}
    assert text_band({"layout": split}) == (0.395, 0.8)
    assert text_band({"layout": {**split, "cam_pos": "bottom"}}) == (0.1, 0.605)
    run_tool(_ctx(store, plan["id"]), "set_layout", split)
    clip = run_tool(_ctx(store, plan["id"]), "get_clip", {})[0]
    hook = clip["positions"]["hook"]
    assert hook["y"] >= 0.395 + 0.028 and hook["y_by_lines"]["2"] > hook["y"]
    assert clip["positions"]["cam_strip"] == {"y_from": 0.0, "y_to": 0.38, "note": clip["positions"]["cam_strip"]["note"]}
    title = {"id": "h", "type": "text", "start": 0, "end": 2, "x": 0.5, "y": 0.13,
             "props": {"text": "ХУК", "size": 96, "weight": 900}}
    assert any("полосе вебки" in w for w in lint_layers([title], 10, layout=split))
    assert not any("полосе вебки" in w for w in lint_layers([{**title, "y": hook["y"]}], 10, layout=split))
    assert not any("полосе вебки" in w for w in lint_layers([title], 10, layout={"mode": "single"}))
    # Автомонтаж ставит хук под полосу.
    built = build_auto_edit("Хук", [], [], 10.0, hook_top=0.395)
    assert next(l for l in built["layers"] if l["id"] == "auto_hook")["y"] >= 0.395 + 0.02


def test_split_facecam_warnings_and_game_focus(tmp_path, monkeypatch):
    from app.agent_tools import facecam_warnings

    store, _service, plan, _preset = _two_pieces(tmp_path, monkeypatch)
    source = store.get_source(plan["source_id"])  # 1280×720
    chat_cam = {"x": 0.63, "y": 0.4, "w": 0.37, "h": 0.6}
    warnings = facecam_warnings({"mode": "split", "cam": chat_cam, "ratio": 0.38}, source)
    assert any("вебка большая" in w for w in warnings) and any("до лба" in w for w in warnings)
    fitted = {"mode": "split", "cam": {"x": 0.7, "y": 0.02, "w": 0.28, "h": 0.3}, "ratio": 0.33}
    assert facecam_warnings(fitted, source) == []
    store.update_source(plan["source_id"], facecam=chat_cam)
    out, ok = run_tool(_ctx(store, plan["id"]), "set_layout", {"mode": "split", "game_x": 0.3})
    assert ok, out
    assert out["layout"]["cam"] == chat_cam and out["cam_from"] and out["warnings"]
    assert "только игру" in out["note"]
    pieces = run_tool(_ctx(store, plan["id"]), "get_clip", {})[0]["pieces"]
    assert [p["focus"] for p in pieces] == [[{"t": 0.0, "x": 0.3}]] * 2
    out, ok = run_tool(_ctx(store, plan["id"]), "set_layout", {"game_x": 0.7, "piece": 1})
    assert ok and [p["focus"][0]["x"] for p in run_tool(_ctx(store, plan["id"]), "get_clip", {})[0]["pieces"]] == [0.3, 0.7]


# ------------------------------------------------------------------ имена полей, подсветка, пресет (п. 8)


def test_piece_fields_are_start_sec_end_sec_everywhere(tmp_path, monkeypatch):
    store, _service, plan, _preset = _two_pieces(tmp_path, monkeypatch)
    piece = run_tool(_ctx(store, plan["id"]), "get_clip", {})[0]["pieces"][0]
    assert (piece["start_sec"], piece["end_sec"]) == (0.0, 10.0) == (piece["source_start"], piece["source_end"])
    out, ok = run_tool(_ctx(store, plan["id"]), "trim_piece", {"index": 0, "source_start": 1.0})
    assert ok, out
    assert run_tool(_ctx(store, plan["id"]), "get_clip", {})[0]["pieces"][0]["start_sec"] == 1.0
    created, ok = run_tool(_ctx(store), "create_clip", {"project_id": plan["source_id"], "title": "Новый",
                                                       "pieces": [{"source_start": 20, "source_end": 28}]})
    assert ok, created
    fresh = store.get_clip_plan(created["clip_id"])
    assert fresh["segments"][0]["start_sec"] == 20.0
    # Новый клип — сразу с пресетом по умолчанию числом, а не null.
    from app.render import effective_preset

    assert fresh["render_settings"]["preset_id"] == effective_preset(store, None)["id"]
    render = run_tool(_ctx(store, created["clip_id"]), "get_clip", {})[0]["render"]
    assert render["preset_id"] is not None and render["effective_preset"]["id"] == render["preset_id"]


def test_manual_pieces_from_1_5_sec_but_analysis_keeps_5(tmp_path, monkeypatch):
    """Клипы 583–585: широкий баннер «ЧЕМПИОН» отдельным куском запрещали 5 с. Рендеру длина
    куска не важна (переход ≤ 40% куска) — ручной монтаж от 1.5 с, хайлайты анализа — от 5 с."""
    from app.store import MIN_PIECE_DURATION_SEC, MIN_SEGMENT_DURATION_SEC
    from app.transitions import normalize_transition, plan_joins

    assert (MIN_PIECE_DURATION_SEC, MIN_SEGMENT_DURATION_SEC) == (1.5, 5)
    store, _service, plan, _preset = _two_pieces(tmp_path, monkeypatch)
    created, ok = run_tool(_ctx(store), "create_clip", {"project_id": plan["source_id"], "title": "Чемпион",
                                                       "pieces": [{"start_sec": 20, "end_sec": 28},
                                                                  {"start_sec": 28, "end_sec": 29.5}]})
    assert ok, created
    assert [round(s["end_sec"] - s["start_sec"], 2) for s in store.get_clip_plan(created["clip_id"])["segments"]] \
        == [8.0, 1.5]
    bad, ok = run_tool(_ctx(store), "create_clip", {"project_id": plan["source_id"], "title": "Мало",
                                                   "pieces": [{"start_sec": 20, "end_sec": 21.2}]})
    assert not ok and "1.5" in bad["error"]
    # Подрезка и файл клипа — тот же минимум.
    ctx = _ctx(store, created["clip_id"])
    out, ok = run_tool(ctx, "trim_piece", {"index": 0, "start_sec": 25.5, "end_sec": 27.5})
    assert ok, out
    out, ok = run_tool(ctx, "trim_piece", {"index": 0, "start_sec": 25.5, "end_sec": 26.5})
    assert not ok and "1.5" in str(out)
    # Анализ (ИИ-хайлайт) по-прежнему не меньше 5 с.
    analysis = store.create_ai_analysis(plan["source_id"], "mock", status="succeeded")
    with pytest.raises(ValueError, match="at least 5"):
        store.create_ai_segment(analysis["id"], {"start_sec": 0, "end_sec": 3, "title": "Обрывок"})
    # Переход на стыке с коротким куском не съедает больше 40% куска — рендеру безопасно.
    joins = plan_joins(normalize_transition({"type": "fade", "duration": 1.5}), [8.0, 1.5], [None])
    assert joins == [pytest.approx(0.6)]


def test_highlight_words_survive_rewritten_subtitles(tmp_path, monkeypatch):
    store, _service, plan, _preset = _two_pieces(tmp_path, monkeypatch)
    words = [{"word": w, "start": 1.0 + i, "end": 1.5 + i} for i, w in enumerate(["это", "очень", "дорого", "брат"])]
    run_tool(_ctx(store, plan["id"]), "set_subtitle_words", {"words": words})
    caps = {"id": "caps", "type": "captions", "start": 0, "end": 19, "x": 0.5, "y": 0.72,
            "props": {"style": "hormozi", "highlightWords": [{"text": "дорого", "color": "#ff3b30"}]}}
    out, ok = run_tool(_ctx(store, plan["id"]), "add_layers", {"layers": [caps]})
    assert ok, out
    mark = store.get_clip_plan(plan["id"])["render_settings"]["layers"][0]["props"]["highlightWords"][0]
    assert mark["i"] == 2 and mark["w"] == "дорого"
    # Перезапись слов: впереди добавилось слово — номер сдвигается, подсветка остаётся на «дорого».
    out, ok = run_tool(_ctx(store, plan["id"]), "set_subtitle_words", {"words": [
        {"word": "слушай,", "start": 0.2, "end": 0.8}, *words]})
    assert ok and out["highlights"]["kept"] == 1
    mark = store.get_clip_plan(plan["id"])["render_settings"]["layers"][0]["props"]["highlightWords"][0]
    assert mark["i"] == 3 and mark["w"] == "дорого"
    # Слово убрали — метка снимается, а не красит соседа.
    out, ok = run_tool(_ctx(store, plan["id"]), "set_subtitle_words", {"words": [words[0], words[1], words[3]]})
    assert ok and out["highlights"]["lost"] == 1
    assert store.get_clip_plan(plan["id"])["render_settings"]["layers"][0]["props"]["highlightWords"] == []
    assert run_tool(_ctx(store, plan["id"]), "add_layers", {"layers": [{
        **caps, "id": "c2", "props": {"style": "hormozi", "highlightWords": [{"text": "нет", "color": "#ff3b30"}]}}]})[1] is False


def test_render_rebinds_highlights_after_cuts():
    from app.caption_marks import rebind_layers
    from app.cuts import CutMap

    old = [{"w": "раз", "t": 1.0}, {"w": "ээ", "t": 2.2}, {"w": "дорого", "t": 3.0}]
    new = [{"w": "раз", "t": 1.0}, {"w": "дорого", "t": 2.0}]
    layers = [{"type": "captions", "props": {"highlightWords": [{"i": 2, "color": "#ff3b30"}, {"i": 1, "color": "#fff"}]}}]
    fresh, lost = rebind_layers(layers, old, new, time_map=CutMap([(2.0, 3.0)]))
    assert fresh[0]["props"]["highlightWords"] == [{"i": 1, "color": "#ff3b30", "w": "дорого", "t": 2.0}]
    assert lost == 1  # «ээ» вырезано


# ------------------------------------------------------------------ переход не на речь (п. 9)


def test_transition_over_speech_is_flagged_and_fitted(tmp_path, monkeypatch):
    store, _service, plan, _preset = _two_pieces(tmp_path, monkeypatch)
    store.set_clip_plan_subtitles(plan["id"], {
        "words": [{"word": "последние", "start": 9.0, "end": 9.5}, {"word": "слова", "start": 9.5, "end": 9.85},
                  {"word": "дальше", "start": 10.6, "end": 11.0}],
        "lines": [{"start": 9.0, "end": 11.0, "text": "последние слова дальше"}]})
    out, ok = run_tool(_ctx(store, plan["id"]), "set_transition", {"type": "fadeblack", "duration": 0.5})
    assert ok, out
    assert any("слова" in w and "уйдут под переход" in w and "fit_speech" in w for w in out["warnings"])
    assert any(w.startswith("Переход:") for w in run_tool(_ctx(store, plan["id"]), "get_clip", {})[0]["warnings"])
    out, ok = run_tool(_ctx(store, plan["id"]), "set_transition", {"fit_speech": True})
    assert ok and out["transition"]["duration"] == pytest.approx(0.1) and not out.get("warnings")
    # Речь вплотную к стыку — укоротить нельзя, подскажет подрезку куска.
    store.set_clip_plan_subtitles(plan["id"], {"words": [{"word": "впритык", "start": 9.5, "end": 9.99}],
                                               "lines": [{"start": 9.5, "end": 9.99, "text": "впритык"}]})
    out, ok = run_tool(_ctx(store, plan["id"]), "set_transition", {"duration": 0.4, "fit_speech": True})
    assert ok and "подрежь" in out["note"] and any("продли кусок 0" in w for w in out["warnings"])


# ------------------------------------------------------------------ статистика, лист исходника (п. 10, 14)


def test_get_performance_explains_empty_stats(tmp_path, monkeypatch):
    store, _service, _plan, _preset = _two_pieces(tmp_path, monkeypatch)
    out, ok = run_tool(_ctx(store), "get_performance", {})
    assert ok and out["clips_with_stats"] == 0 and "это нормально" in out["hint"] and out["top"] == []


def test_source_contact_sheet_steps_frame_by_frame():
    from app import motion_render

    times = motion_render.source_sheet_times(100.0, 100.5, 0.04, None)
    assert times[:3] == [100.0, 100.04, 100.08] and len(times) == 13
    assert motion_render.source_sheet_times(10, 20, 0.01, None)[1] == 10.04  # не мельче кадра
    assert motion_render.source_time_label(100.04, 2) == "1:40.04"
    assert motion_render.source_time_label(3723.5) == "1:02:03.5"


# ------------------------------------------------------------------ сверка субтитров по речи (п. 15)


def _env(pattern: list[tuple[float, float, float]], seconds: float = 12.0) -> list[float]:
    env = [30.0] * int(seconds * 100)
    for a, b, level in pattern:
        for k in range(int(a * 100), int(b * 100)):
            env[k] = level
    return env


def test_subtitle_sync_is_not_fooled_by_gunfire():
    from app.render_qc import offset_from_envelope

    words = [{"start": s, "end": s + 0.3} for s in (1.0, 1.4, 1.8, 2.2, 5.0, 5.4, 5.8, 6.2, 9.0, 9.4)]
    # Чистая речь в словах, тишина между ними — сверка работает и сдвига нет.
    voice = [(w["start"], w["end"], 3000.0) for w in words]
    assert offset_from_envelope(_env(voice), words) == 0.0
    # Голос опаздывает на 0.5 с — субтитры спешат.
    late = [(a + 0.5, b + 0.5, level) for a, b, level in voice]
    assert offset_from_envelope(_env(late), words) == pytest.approx(0.5, abs=0.03)
    # Игра: выстрелы между фразами громче голоса — по громкости не судим (раньше выходило «спешат»).
    shots = [(t, t + 0.25, 9000.0) for t in (3.0, 3.5, 4.0, 4.5, 7.0, 7.5, 8.0, 10.5, 11.0)]
    assert offset_from_envelope(_env(voice + shots), words) is None


def test_subtitle_sync_qc_uses_the_speech_track(tmp_path, monkeypatch):
    store, service, plan, preset = _two_pieces(tmp_path, monkeypatch)
    store.update_source(plan["source_id"], metadata=TWO_TRACKS, audio_tracks=[0, 1], speech_tracks=[1])
    seen = {}

    def fake_voice(pcm, rate, words):
        seen["words"] = words
        return 0.6

    monkeypatch.setattr("app.render_qc.estimate_offset_on_voice", fake_voice)
    monkeypatch.setattr("app.render_qc.estimate_subtitle_offset",
                        lambda path, words: pytest.fail("по громкости готового звука не сверяем"))
    clip = store.create_clip(plan["source_id"], clip_plan_id=plan["id"], ffmpeg_preset_id=preset["id"])
    qc = service._qc_subtitle_sync(store.get_clip(clip["id"]), Path("x.mp4"), {"ok": True, "issues": []})
    assert qc["subtitle_sync_basis"] == "speech_track" and qc["subtitle_offset_sec"] == 0.6
    assert "по дорожке речи" in qc["issues"][0]["text"]
    assert [w["start"] for w in seen["words"]] == [5.0, 12.0]  # время частей подряд, как звук дорожки


# ------------------------------------------------------------------ бренд: подсветка не цветом звучащего слова


def test_brand_highlight_color_contrasts_with_the_active_word():
    from app.agent_tools import caption_highlight_warnings, colors_close
    from app.brand_kit import caption_props, highlight_color, normalize_brand

    brand = normalize_brand({"name": "Магазин", "colors": {"accent": "#ffd600", "text": "#ffffff"}})
    color = highlight_color(brand, "#ffd600", "#ffffff")
    assert not colors_close(color, "#ffd600") and not colors_close(color, "#ffffff")
    props = caption_props({"style": "hormozi", "highlightWords": [{"i": 0, "color": "#22c55e"}]}, brand)
    assert props["activeColor"] == "#ffd600" and caption_highlight_warnings(props) == []
    # Второй цвет бренда — если он виден.
    own = normalize_brand({"name": "M", "colors": {"accent": "#ffd600", "highlight": "#00E5FF"}})
    assert own["colors"]["highlight"] == "#00e5ff"
    assert caption_props({"highlightWords": [{"i": 0, "color": "#fff"}]}, own)["highlightWords"][0]["color"] == "#00e5ff"
    # Цвет подсветки как у звучащего слова бренд не возьмёт.
    same = normalize_brand({"name": "M", "colors": {"accent": "#ffd600", "highlight": "#ffd000"}})
    assert caption_props({"highlightWords": [{"i": 0, "color": "#fff"}]}, same)["highlightWords"][0]["color"] != "#ffd000"
