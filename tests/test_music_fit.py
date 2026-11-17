"""Концовка трека на конец ролика (app/music_fit.py): поиск концовки, подбор старта, set_music, пересчёт перед
рендером, редактор без поля music_fit.

Раньше фон шёл с 0 с и обрывался там, где кончился ролик, — посреди фразы. Теперь старт подбирается так, чтобы
настоящая концовка трека (финальный удар, затухание, обрыв файла) легла на конец ролика."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app import music_fit as F
from app.settings import settings


def _require_ffmpeg():
    if not shutil.which("ffmpeg"):
        pytest.skip("нет ффмпег")


def _synth(path: Path, expr: str, duration: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"aevalsrc='{expr}':s=44100:d={duration}",
                    "-c:a", "libmp3lame", "-b:a", "128k", str(path)], check=True)
    return path


def _fade_track(path: Path) -> Path:
    """Тон 440 Гц 34 с, линейное затухание 34→38 с, 2 с тишины."""
    return _synth(path, "0.3*sin(2*PI*440*t)*if(lt(t,34),1,max(0,1-(t-34)/4))", 40)


def _hit_track(path: Path) -> Path:
    """Бочка 120 BPM и тон до 30 с, на 30 с — финальный удар со звоном ~1 с, дальше тишина до 33 с."""
    expr = ("if(lt(t,30),0.6*sin(2*PI*60*t)*exp(-25*mod(t,0.5))+0.1*sin(2*PI*440*t),"
            "0.9*sin(2*PI*220*t)*exp(-3*(t-30)))")
    return _synth(path, expr, 33)


def _cut_track(path: Path) -> Path:
    """Бочка и тон, на 25 с звук обрывается разом; в файле ещё секунда тишины."""
    return _synth(path, "if(lt(t,25),0.6*sin(2*PI*60*t)*exp(-25*mod(t,0.5))+0.1*sin(2*PI*440*t),0)", 26)


# ---------------------------------------------------------------- концовка


def test_sine_with_fade_end_is_a_fade_and_its_end_lands_on_clip_end(tmp_path):
    _require_ffmpeg()
    track = _fade_track(tmp_path / "fade.mp3")
    x = F.decode(track)
    ending = F.ending_from_envelope(F.envelope(x))
    assert ending["kind"] == "fade", ending
    # −30 дБ от тела при линейном затухании 4 с — около 37.9 с.
    assert 37.0 <= ending["anchor"] <= 38.1, ending
    fit = F.fit_for_track({"file_path": str(track)}, 20.0, 0.0)
    assert fit["ok"], fit
    assert abs(fit["lands_at"] - 20.0) <= 1.0 and fit["tail"] <= 1.0, fit
    assert abs(fit["start_sec"] - (ending["anchor"] - 20.0)) <= 1.0
    assert "затухание трека" in F.describe(fit)


def test_final_hit_lands_before_end_with_ring_and_start_on_beat(tmp_path):
    _require_ffmpeg()
    track = _hit_track(tmp_path / "hit.mp3")
    x = F.decode(track)
    ending = F.ending_from_envelope(F.envelope(x))
    assert ending["kind"] == "hit", ending
    assert abs(ending["anchor"] - 30.0) <= 0.05, ending
    assert 0.3 <= ending["ring"] <= 2.0, ending
    fit = F.fit_for_track({"file_path": str(track)}, 12.0, 0.0)
    assert fit["ok"], fit
    # Удар — до конца ролика, с хвостом 0.15…2 с; старт — на доле бочки (каждые 0.5 с).
    assert 0.15 <= fit["tail"] <= 2.0, fit
    assert fit["snapped"] in ("beat", "downbeat"), fit
    phase = fit["start_sec"] % 0.5
    assert min(phase, 0.5 - phase) <= 0.05, fit
    # Стоп-кадр в конце: удар ложится около его начала (хвост ≈ стоп-кадр 1.2 с, с точностью до доли).
    held = F.fit_for_track({"file_path": str(track)}, 12.0, 1.2)
    assert held["ok"] and 0.9 <= held["tail"] <= 1.75, held


def test_abrupt_file_end_is_a_cut_that_lands_on_or_after_clip_end(tmp_path):
    _require_ffmpeg()
    track = _cut_track(tmp_path / "cut.mp3")
    ending = F.ending_from_envelope(F.envelope(F.decode(track)))
    assert ending["kind"] == "cut" and abs(ending["anchor"] - 25.0) <= 0.1, ending
    fit = F.fit_for_track({"file_path": str(track)}, 15.0, 0.0)
    assert fit["ok"] and -0.5 <= fit["tail"] <= 0.0, fit  # обрыв не раньше конца ролика — его закроет затухание


def test_track_shorter_than_clip_or_intro_skip_gives_no_fit(tmp_path):
    _require_ffmpeg()
    track = _fade_track(tmp_path / "fade.mp3")
    assert not F.fit_for_track({"file_path": str(track)}, 45.0, 0.0)["ok"]
    out = F.fit_for_track({"file_path": str(track)}, 30.0, 0.0, min_start=12.0)
    assert not out["ok"] and "вступление" in out["reason"], out
    assert not F.fit_for_track({"file_path": str(tmp_path / "нет.mp3")}, 10.0, 0.0)["ok"]


def test_fit_start_prefers_downbeat_and_respects_track_end():
    ending = {"kind": "hit", "anchor": 100.0, "ring": 2.0, "duration": 104.0}
    grid = {"bpm": 120.0, "period": 0.5, "first_beat": 0.1, "first_downbeat": 0.1, "downbeat_every": 4}
    fit = F.fit_start(ending, 30.0, grid=grid)
    # Цель: удар за 1 с до конца → старт 71.0; ближайшая сильная доля 70.1 (такт 2 с) — хвост 0.1 мал,
    # 72.1 — хвост 2.1 > 2; берётся доля 71.1 (хвост 1.1).
    assert fit["ok"] and fit["snapped"] == "beat" and fit["start_sec"] == 71.1, fit
    # Трек кончается раньше ролика с этого старта — такой старт не годится (рендер зациклил бы трек).
    short = F.fit_start({**ending, "duration": 100.5}, 30.0, track_len=100.5)
    assert not short["ok"]
    assert F.fit_start(None, 10.0)["ok"] is False


def test_tail_for_hit_hold_and_fade():
    assert F.tail_for({"kind": "fade"}, 30, 0.5) == 0.0
    assert F.tail_for({"kind": "hit", "ring": 4.0}, 30, 0.0) == 1.0
    assert F.tail_for({"kind": "hit", "ring": 0.1}, 30, 0.0) == 0.4
    assert F.tail_for({"kind": "hit", "ring": 4.0}, 30, 1.5) == 1.5
    assert F.tail_for({"kind": "hit", "ring": 4.0}, 2.0, 1.5) == 0.6  # не больше трети короткого ролика


def test_real_pack_track_ending():
    """Настоящий трек пака сервиса (guitar.mp3, CC0): финальный удар около 159.6 с."""
    _require_ffmpeg()
    candidates = [os.environ.get("VVF_PACK_TRACK") or "", str(Path(settings.motion_packs_dir) / "music" / "guitar.mp3"),
                  r"D:\vvf-offload\tailfit_test\pack-guitar.mp3"]
    path = next((Path(p) for p in candidates if p and Path(p).is_file()), None)
    if path is None:
        pytest.skip("нет трека пака guitar.mp3 (VVF_PACK_TRACK)")
    ending = F.ending_from_envelope(F.envelope(F.decode(path)))
    assert ending["kind"] == "hit" and abs(ending["anchor"] - 159.6) <= 0.5, ending
    fit = F.fit_for_track({"file_path": str(path)}, 30.5, 0.5)
    assert fit["ok"] and 0.15 <= fit["tail"] <= 2.0, fit
    assert abs(fit["start_sec"] + 30.5 - fit["tail"] - ending["anchor"]) <= 0.05


# ---------------------------------------------------------------- настройки и редактор


def test_normalize_fit_and_settings_field():
    from app.clip_settings import normalize_render_settings

    assert F.normalize_fit(None) is None and F.normalize_fit("junk") is None
    assert F.normalize_fit("end") == {"mode": "end"}
    assert F.normalize_fit({"mode": "end", "min_start": "10.44", "end_at": -3, "x": 1}) == {"mode": "end",
                                                                                           "min_start": 10.44}
    assert normalize_render_settings({})["music_fit"] is None
    assert normalize_render_settings({"music_fit": {"mode": "off"}})["music_fit"] == {"mode": "off"}


def test_editor_save_rules():
    cur = {"music_on": True, "track_id": 1, "music_start_sec": 40.0, "music_fit": {"mode": "end", "start": 40.0,
                                                                                  "prev": 32.0}}
    # Новый трек, старт сброшен редактором в 0 — подберёт рендер.
    out = F.editor_save(cur, {"music_on": True, "track_id": 2, "music_start_sec": 0})
    assert out["music_fit"] == {"mode": "end"}
    # Владелец сдвинул старт — его выбор.
    out = F.editor_save(cur, {"music_on": True, "track_id": 1, "music_start_sec": 12.0})
    assert out["music_fit"] == {"mode": "off"} and out["music_start_sec"] == 12.0
    # Редактор открыт до пересчёта и прислал старый подобранный старт — это не выбор.
    out = F.editor_save(cur, {"music_on": True, "track_id": 1, "music_start_sec": 32.0})
    assert out["music_fit"]["mode"] == "end" and out["music_start_sec"] == 40.0
    # Ничего не меняли — как было; старый клип без поля — так и остаётся.
    assert F.editor_save(cur, {"music_on": True, "track_id": 1, "music_start_sec": 40.0})["music_fit"] == \
        F.normalize_fit(cur["music_fit"])
    legacy = {"music_on": True, "track_id": 1, "music_start_sec": 0.0}
    assert F.editor_save(legacy, dict(legacy))["music_fit"] is None


# ---------------------------------------------------------------- set_music и рендер


def _clip(tmp_path, monkeypatch):
    from test_api_pipeline import _require_ffmpeg as need
    from test_clip_spec import _setup

    need()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    return _client, store, plan


def _track(store, path: Path, label="Финал"):
    dest = settings.audio_dir / path.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(path, dest)
    from app.ingest import probe_media

    return store.create_audio_track(label, dest, duration_sec=probe_media(dest).duration_sec, volume=0.5)


def test_set_music_fits_new_track_and_explicit_start_turns_fit_off(tmp_path, monkeypatch):
    from app.agent_tools import ToolContext, run_tool

    _client, store, plan = _clip(tmp_path, monkeypatch)
    track = _track(store, _fade_track(tmp_path / "fade.mp3"))
    ctx = ToolContext(store=store, clip_plan_id=plan["id"], on_first_change=lambda: None)
    total, _hold = F.clip_total(store, store.get_clip_plan(plan["id"]))
    out, ok = run_tool(ctx, "set_music", {"track_id": track["id"]})
    assert ok, out
    assert out["music_fit"]["mode"] == "end" and out["music_fit"]["ending"]["kind"] == "fade", out
    assert abs(out["music_fit"]["lands_at"] - total) <= 1.0
    assert out["music_start_sec"] > 20, out
    rs = store.get_clip_plan(plan["id"])["render_settings"]
    assert rs["music_fit"]["mode"] == "end" and rs["music_fit"]["start"] == out["music_start_sec"]
    # Свой старт — подбор выключен и рендер его не двигает.
    out, ok = run_tool(ctx, "set_music", {"track_id": track["id"], "start_sec": 5})
    assert ok and out["music_start_sec"] == 5 and out["music_fit"]["mode"] == "off"
    out, ok = run_tool(ctx, "set_music", {"track_id": track["id"]})
    assert ok and out["music_start_sec"] == 5  # тот же трек без аргументов — как было
    # fit_end=true — подобрать заново; со start_sec одновременно — ошибка.
    out, ok = run_tool(ctx, "set_music", {"track_id": track["id"], "fit_end": True})
    assert ok and out["music_fit"]["mode"] == "end" and out["music_start_sec"] > 20
    out, ok = run_tool(ctx, "set_music", {"track_id": track["id"], "fit_end": True, "start_sec": 3})
    assert not ok
    # Без подбора — новый трек с начала.
    other = _track(store, _hit_track(tmp_path / "hit.mp3"), "Удар")
    out, ok = run_tool(ctx, "set_music", {"track_id": other["id"], "fit_end": False})
    assert ok and out["music_start_sec"] == 0.0 and out["music_fit"]["mode"] == "off"
    # Своя опора концовки (секунда финального удара).
    out, ok = run_tool(ctx, "set_music", {"track_id": other["id"], "track_end_sec": 20.0})
    assert ok and out["music_fit"]["ending"]["track_sec"] == 20.0, out
    assert abs(out["music_start_sec"] + total - 20.0) <= 2.1


def test_render_refits_start_to_new_length_and_persists_it(tmp_path, monkeypatch):
    from app.agent_tools import ToolContext, run_tool
    from app.render import ClipRenderService

    _client, store, plan = _clip(tmp_path, monkeypatch)
    track = _track(store, _fade_track(tmp_path / "fade.mp3"))
    ctx = ToolContext(store=store, clip_plan_id=plan["id"], on_first_change=lambda: None)
    out, ok = run_tool(ctx, "set_music", {"track_id": track["id"]})
    assert ok, out
    fitted = out["music_start_sec"]
    # Ролик стал на 2 с короче (вырез) — старт должен уехать на ~2 с позже.
    rs = dict(store.get_clip_plan(plan["id"])["render_settings"])
    rs["cuts"] = [{"start": 3.0, "end": 5.0}]
    store.set_clip_plan_render_settings(plan["id"], rs)
    seen: dict = {}

    def fake_montage(self, segment_ids, **kwargs):
        seen.update(kwargs)
        return {"id": 0, "status": "failed", "title": kwargs.get("title"), "description": kwargs.get("description")}

    def fake_cut(self, *args, **kwargs):
        seen.update(kwargs)
        return {"id": 0, "status": "failed"}

    monkeypatch.setattr(ClipRenderService, "render_montage", fake_montage)
    monkeypatch.setattr(ClipRenderService, "render_cut_plan", fake_cut, raising=False)
    try:
        ClipRenderService(store).render_clip_plan(plan["id"])
    except Exception:  # noqa: BLE001 - путь рендера с вырезами нам не нужен: важен пересчёт до него
        pass
    fresh = store.get_clip_plan(plan["id"])["render_settings"]
    assert fresh["music_fit"]["mode"] == "end"
    assert 1.0 <= fresh["music_start_sec"] - fitted <= 3.0, (fitted, fresh["music_start_sec"])
    assert fresh["music_fit"]["start"] == fresh["music_start_sec"] and fresh["music_fit"]["prev"] == fitted


def test_refit_leaves_explicit_legacy_and_lyric_clips_alone(tmp_path, monkeypatch):
    _client, store, plan = _clip(tmp_path, monkeypatch)
    track = _track(store, _fade_track(tmp_path / "fade.mp3"))
    base = {"music_on": True, "track_id": track["id"], "music_start_sec": 7.0}
    for extra in ({}, {"music_fit": {"mode": "off"}}):
        store.set_clip_plan_render_settings(plan["id"], {**base, **extra})
        p = store.get_clip_plan(plan["id"])
        same, fit = F.refit_plan(store, p)
        assert fit is None and same["render_settings"]["music_start_sec"] == 7.0
    store.set_clip_plan_render_settings(plan["id"], {**base, "music_fit": {"mode": "end"},
                                                     "layers": [{"id": "lyric-1", "type": "text", "start": 0,
                                                                 "end": 1, "props": {"text": "ля"}}]})
    p = store.get_clip_plan(plan["id"])
    if not F.has_lyrics(p["render_settings"]):
        pytest.skip("слой не сохранился в этой схеме")
    same, fit = F.refit_plan(store, p)
    assert fit and not fit["ok"] and "текст песни" in fit["reason"]
    assert same["render_settings"]["music_start_sec"] == 7.0


def test_editor_api_marks_new_track_for_fit(tmp_path, monkeypatch):
    client, store, plan = _clip(tmp_path, monkeypatch)
    track = _track(store, _fade_track(tmp_path / "fade.mp3"))
    current = store.get_clip_plan(plan["id"])["render_settings"] or {}
    payload = {**current, "music_on": True, "track_id": track["id"], "music_start_sec": 0}
    payload.pop("music_fit", None)
    client.patch(f"/api/clip-plans/{plan['id']}/render-settings", json={"settings": payload}).raise_for_status()
    assert store.get_clip_plan(plan["id"])["render_settings"]["music_fit"] == {"mode": "end"}
    payload["music_start_sec"] = 9.5
    client.patch(f"/api/clip-plans/{plan['id']}/render-settings", json={"settings": payload}).raise_for_status()
    rs = store.get_clip_plan(plan["id"])["render_settings"]
    assert rs["music_fit"] == {"mode": "off"} and rs["music_start_sec"] == 9.5
