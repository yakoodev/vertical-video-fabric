"""Где у трека энергия (app/music_energy.py) и подсказки агенту про тихое вступление.

Проверочный прогон Gemini (клип 575): «Бодрый фон» поставлен с 0 с, а барабаны — около 10 с;
первые 10 с промо шли под тихое вступление."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from app import music_energy as E
from app.settings import settings
from test_canvas import canvas_api  # noqa: F401 - фикстура холста


def _require_ffmpeg():
    if not shutil.which("ffmpeg"):
        pytest.skip("нет ffmpeg")


# ---------------------------------------------------------------- огибающая


def test_marks_on_envelope_quiet_intro_then_loud():
    levels = [-32.0, -30.0] * 10 + [-15.0, -17.0] * 20  # 10 с тихо, дальше громко
    marks = E.energy_marks(levels)
    assert marks == {"energy_start_sec": 10.0, "drop_sec": 10.0}


def test_marks_energetic_from_start_and_no_drop():
    levels = [-14.0, -16.0] * 30
    assert E.energy_marks(levels) == {"energy_start_sec": 0.0, "drop_sec": None}
    assert E.energy_marks([]) == {"energy_start_sec": None, "drop_sec": None}
    assert E.energy_marks([-120.0] * 10)["energy_start_sec"] is None  # тишина


def test_single_loud_hit_in_intro_is_not_energy():
    """Одинокий удар во вступлении — не начало энергичной части: громкость должна держаться."""
    levels = [-32.0] * 8 + [-12.0] + [-32.0] * 11 + [-15.0] * 40
    assert E.energy_start(levels) == 10.0


def test_quiet_intro_hint_needs_marks_and_threshold():
    track = {"energy_version": E.ENERGY_VERSION, "energy_start_sec": 10.5}
    assert E.quiet_intro_sec(track, 0.0) == 10.5
    assert E.quiet_intro_sec(track, 9.0) == 0.0  # меньше 2 с вступления — не шумим
    assert E.quiet_intro_sec(track, 12.0) == 0.0
    assert E.quiet_intro_sec({"energy_start_sec": 10.5}, 0.0) == 0.0  # не размечен
    assert E.energy_view({**track, "drop_sec": 29.5, "bpm": None}) == {"energy_start_sec": 10.5, "drop_sec": 29.5}


def _synth_track(path, bpm=120.0):
    """10 с тихого тона, потом бочка на каждую долю ``bpm`` и громкий тон."""
    beat = 60.0 / bpm
    expr = (f"if(lt(t,10),0.02*sin(2*PI*330*t),"
            f"0.8*sin(2*PI*60*t)*exp(-25*mod(t,{beat:.4f}))+0.12*sin(2*PI*440*t))")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"aevalsrc='{expr}':s=44100:d=40:c=stereo",
                    "-c:a", "libmp3lame", "-b:a", "128k", str(path)], check=True)


def test_analyze_real_file_finds_drums_and_tempo(tmp_path):
    _require_ffmpeg()
    path = tmp_path / "upbeat.mp3"
    _synth_track(path, bpm=120.0)
    marks = E.analyze_track(path)
    assert 9.5 <= marks["energy_start_sec"] <= 10.5, marks
    assert 9.0 <= marks["drop_sec"] <= 11.0, marks
    assert marks["bpm"] is not None and abs(marks["bpm"] - 120.0) <= 3, marks


# ---------------------------------------------------------------- хранение и ленивая разметка


def _store(tmp_path, monkeypatch):
    from test_render import _store as base

    return base(tmp_path, monkeypatch)


def _track(store, name="t.mp3", label="Бодрый фон"):
    path = settings.audio_dir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"audio")
    return store.create_audio_track(label, path, duration_sec=59.0, volume=0.5)


def test_energy_columns_and_lazy_backfill_one_by_one(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    a, b = _track(store, "a.mp3", "A"), _track(store, "b.mp3", "B")
    assert a["energy_version"] == 0 and a["energy_start_sec"] is None
    calls = []

    def fake_fields(path):
        calls.append(str(path))
        return {"energy_start_sec": 10.4, "drop_sec": 29.5, "bpm": 99.0, "energy_version": E.ENERGY_VERSION}

    monkeypatch.setattr(E, "track_fields", fake_fields)
    # Бюджет кончился — второй трек разметится в следующий раз.
    clock = iter([0.0, 0.0, 100.0, 100.0])
    monkeypatch.setattr(E.time, "monotonic", lambda: next(clock))
    out = E.ensure_energy(store, budget_sec=20.0)
    assert len(calls) == 1 and sum(1 for t in out if t["energy_version"]) == 1
    monkeypatch.setattr(E.time, "monotonic", lambda: 0.0)
    out = E.ensure_energy(store)
    assert len(calls) == 2 and all(t["energy_version"] == E.ENERGY_VERSION for t in out)
    E.ensure_energy(store)
    assert len(calls) == 2  # размеченные не пересчитываются
    fresh = store.get_audio_track(a["id"])
    assert (fresh["energy_start_sec"], fresh["drop_sec"], fresh["bpm"]) == (10.4, 29.5, 99.0)
    assert store.get_audio_track(b["id"])["energy_version"] == E.ENERGY_VERSION


def test_broken_file_is_marked_once_without_marks(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    track = _track(store)

    def boom(path):
        raise ValueError("битый файл")

    monkeypatch.setattr(E, "analyze_track", boom)
    marked = E.mark_track(store, track)
    assert marked["energy_version"] == E.ENERGY_VERSION and marked["energy_start_sec"] is None
    assert E.energy_view(marked) == {}


def test_import_from_url_marks_new_track(tmp_path, monkeypatch):
    from app import music_import

    store = _store(tmp_path, monkeypatch)

    class Proc:
        returncode, stdout, stderr = 0, "Upbeat\n", ""

    def fake_run(args, **kwargs):
        out = args[args.index("-o") + 1].replace("%(ext)s", "mp3")
        open(out, "wb").write(b"mp3")
        return Proc()

    class Meta:
        duration_sec = 59.0

    monkeypatch.setattr(music_import.subprocess, "run", fake_run)
    monkeypatch.setattr("app.ingest.probe_media", lambda p: Meta())
    monkeypatch.setattr(E, "track_fields", lambda p: {"energy_start_sec": 10.4, "drop_sec": 29.5, "bpm": None,
                                                     "energy_version": E.ENERGY_VERSION})
    track = music_import.import_music_from_url(store, "https://example.com/v", "Бодрый")
    assert track["energy_start_sec"] == 10.4 and track["energy_version"] == E.ENERGY_VERSION


# ---------------------------------------------------------------- агент: list_music / set_music / get_clip


def _marked(monkeypatch, start=10.4):
    monkeypatch.setattr(E, "track_fields", lambda p: {"energy_start_sec": start, "drop_sec": 29.5, "bpm": 99.0,
                                                     "energy_version": E.ENERGY_VERSION})


def test_canvas_music_hint_and_quiet_intro_warning(canvas_api, monkeypatch):  # noqa: F811
    from app.agent_tools import ToolContext, run_tool

    _client, store = canvas_api
    _marked(monkeypatch)
    track = _track(store)
    out, ok = run_tool(ToolContext(store=store), "create_canvas_clip", {"duration": 24, "background": "aurora"})
    assert ok, out
    ctx = ToolContext(store=store, clip_plan_id=out["clip_id"], on_first_change=lambda: None)

    listed, ok = run_tool(ctx, "list_music", {})
    assert ok and listed["tracks"][0]["energy_start_sec"] == 10.4 and listed["tracks"][0]["bpm"] == 99.0
    assert "energy_start_sec" in listed["note"]

    res, ok = run_tool(ctx, "set_music", {"track_id": track["id"]})
    assert ok and res["music_start_sec"] == 0.0  # сами не меняем — только подсказка
    assert res["track_energy"] == {"energy_start_sec": 10.4, "drop_sec": 29.5, "bpm": 99.0}
    assert "set_music(start_sec=10.4)" in res["hint"]
    got, ok = run_tool(ctx, "get_clip", {})
    assert any("первые 10.4 с музыки тихие" in w for w in got["warnings"]), got["warnings"]
    assert got["render"]["music_start_sec"] == 0.0 and got["render"]["track_id"] == track["id"]

    res, ok = run_tool(ctx, "set_music", {"track_id": track["id"], "start_sec": 10.4})
    assert ok and "hint" not in res
    got, ok = run_tool(ctx, "get_clip", {})
    assert not any("музыки тихие" in w for w in got["warnings"])


def test_stream_clip_gets_marks_but_no_intro_nagging(tmp_path, monkeypatch):
    """Клип со стрима: музыка под голосом — тихое вступление не беда, подсказки нет."""
    from app.agent_tools import ToolContext, run_tool
    from test_api_pipeline import _require_ffmpeg as need
    from test_clip_spec import _setup

    need()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    _marked(monkeypatch)
    track = _track(store)
    ctx = ToolContext(store=store, clip_plan_id=plan["id"], on_first_change=lambda: None)
    res, ok = run_tool(ctx, "set_music", {"track_id": track["id"]})
    assert ok and res["track_energy"]["energy_start_sec"] == 10.4 and "hint" not in res
    got, ok = run_tool(ctx, "get_clip", {})
    assert not any("музыки тихие" in w for w in got["warnings"])
