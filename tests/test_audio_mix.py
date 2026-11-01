"""Сведение звука (app/audio_mix.py): стемы как у рендера, голос против музыки под речью, звуки
против слов, итог по LUFS/true peak; MCP audio_mix_report и проверка lint_clip ``audio_mix``.

Звук синтетический (тестовый): «голос» — розовый шум фразами 1.2 с через 0.6 с паузы, «музыка» —
сплошной шум громче, «вжух» — короткий всплеск. Уровни известны — по ним сверяем отчёт.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from test_api_pipeline import _client, _require_ffmpeg


def _wav(path: Path, src: str, seconds: float, af: str = "") -> Path:
    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", src, "-t", f"{seconds}"]
    if af:
        args += ["-af", af]
    subprocess.run(args + ["-ac", "2", "-ar", "48000", str(path)], check=True)
    return path


def speech_like(path: Path, seconds: float = 9.0, amplitude: float = 0.1) -> Path:
    """Фразы 1.2 с через паузу 0.6 с (речь с паузами)."""
    return _wav(path, f"anoisesrc=c=pink:r=48000:a={amplitude}:seed=11", seconds,
                "volume='if(lt(mod(t,1.8),1.2),1,0)':eval=frame")


def music_like(path: Path, seconds: float = 20.0, amplitude: float = 0.3) -> Path:
    return _wav(path, f"anoisesrc=c=pink:r=48000:a={amplitude}:seed=22", seconds)


def burst(path: Path, amplitude: float = 0.9) -> Path:
    return _wav(path, f"anoisesrc=c=white:r=48000:a={amplitude}:seed=33", 0.4, "afade=t=out:st=0.1:d=0.3")


# --------------------------------------------------------------------------- чистые функции


def test_normalize_mix_and_layer_gain():
    from app.audio_mix import mix_layer_gain_db, normalize_mix

    assert normalize_mix(None) == {} and normalize_mix({"junk": 1}) == {}
    mix = normalize_mix({"target_lufs": -30, "music_db": -50, "sidechain": False, "duck_attack": 5,
                         "groups": {"sfx": -4, "bad": 3}, "layer_gains": {"wh01": -6, "x": 0}})
    assert mix["target_lufs"] == -24.0 and mix["music_db"] == -40.0 and mix["sidechain"] is False
    assert mix["duck_attack"] == 1.0 and mix["groups"] == {"sfx": -4.0} and mix["layer_gains"] == {"wh01": -6.0}
    assert mix_layer_gain_db(mix, "wh01", "sfx") == -10.0
    assert mix_layer_gain_db(mix, "narr", "voice") == 0.0
    assert mix_layer_gain_db({}, "wh01", "sfx") == 0.0


def test_layer_role_by_role_name_and_length():
    from app.audio_mix import layer_role

    assert layer_role({"id": "narr", "type": "audio", "props": {}}) == "voice"
    assert layer_role({"id": "a1", "name": "Реплика 1.2 (Сова)", "type": "audio", "props": {}}) == "voice"
    assert layer_role({"id": "x", "type": "audio", "props": {"role": "music"}}) == "music"
    assert layer_role({"id": "wh01", "type": "audio", "start": 1, "end": 1.6, "props": {"mode": "track"}}) == "sfx"
    assert layer_role({"id": "song", "type": "audio", "start": 0, "end": 30, "props": {"mode": "track"}}, 40) == "music"
    assert layer_role({"id": "v", "type": "video", "props": {"audio": True}}) == "video"


def test_parse_ebur128_frames_and_summary():
    from app.audio_mix import parse_ebur128

    log = ("[Parsed_ebur128_0 @ 0] t: 0.1  TARGET:-23 LUFS    M:-120.7 S:-120.7     I: -70.0 LUFS  LRA: 0.0 LU\n"
           "[Parsed_ebur128_0 @ 0] t: 0.2  TARGET:-23 LUFS    M: -18.5 S: -20.1     I: -19.0 LUFS  LRA: 0.0 LU\n"
           "[Parsed_ebur128_0 @ 0] Summary:\n\n  Integrated loudness:\n    I:         -19.0 LUFS\n"
           "    Threshold: -29.0 LUFS\n\n  Loudness range:\n    LRA:         2.5 LU\n"
           "    Threshold: -39.0 LUFS\n\n  True peak:\n    Peak:       -3.2 dBFS\n")
    got = parse_ebur128(log)
    assert got["m"] == [-120.0, -18.5] and got["s"][1] == -20.1
    assert got["i"] == -19.0 and got["lra"] == 2.5 and got["tp"] == -3.2


def test_speech_mask_and_ranges():
    from app.audio_mix import _ranges, speech_mask

    dialog = [-60.0] * 5 + [-20.0] * 10 + [-60.0] * 2 + [-21.0] * 5 + [-80.0] * 10
    mask = speech_mask(dialog, -20.0)
    assert sum(mask) == 15
    spans = _ranges(mask, merge=0.35)
    assert spans == [(0.5, 2.2)]  # пауза 0.2 с склеена


# --------------------------------------------------------------------------- клип-холст


@pytest.fixture
def owl(tmp_path, monkeypatch):
    """Холст как пилот 887: озвучка слоем, музыка клипа громче голоса, вжух на слове."""
    _require_ffmpeg()
    import app.canvas as canvas

    monkeypatch.setattr(canvas, "CANVAS_WIDTH", 108)
    monkeypatch.setattr(canvas, "CANVAS_HEIGHT", 192)
    client, store = _client(tmp_path, monkeypatch)
    media = tmp_path / "media"
    media.mkdir()
    voice = speech_like(media / "voice.wav")
    from app.settings import settings

    settings.audio_dir.mkdir(parents=True, exist_ok=True)
    music = music_like(settings.audio_dir / f"music-{tmp_path.name}.wav")
    whoosh = burst(media / "whoosh.wav")
    plan = client.post("/api/canvas-clips", json={"duration": 10, "background": "solid", "title": "Сова"}).json()
    voice_asset = store.create_montage_asset(kind="audio", label="озвучка", file_path=str(voice), has_audio=True,
                                             duration_sec=9.0)
    whoosh_asset = store.create_montage_asset(kind="audio", label="вжух", file_path=str(whoosh), has_audio=True,
                                              duration_sec=0.4)
    track = store.create_audio_track("Музыка", music, volume=0.5)
    rs = dict(store.get_clip_plan(plan["id"])["render_settings"])
    rs.update(music_on=True, track_id=track["id"], layers=[
        {"id": "narr", "type": "audio", "name": "Озвучка", "start": 0.0, "end": 9.0,
         "props": {"asset_id": str(voice_asset["id"]), "mode": "track", "volume": 1.0}},
        {"id": "wh01", "type": "audio", "start": 2.0, "end": 2.4,
         "props": {"asset_id": str(whoosh_asset["id"]), "mode": "track", "volume": 1.0}},
    ])
    store.set_clip_plan_render_settings(plan["id"], rs)
    return {"client": client, "store": store, "plan_id": plan["id"], "voice": voice, "music": music,
            "track": track, "tmp": tmp_path}


def _level(path: Path, af: str = "") -> dict:
    from app.audio_mix import ebur128

    return ebur128(path, af=af)


def test_report_catches_loud_music_under_speech(owl):
    from app.audio_mix import mix_report, power_mean

    store, plan_id = owl["store"], owl["plan_id"]
    report = mix_report(store, plan_id)
    assert report["ok"] and report["canvas"] and set(report["stems"]) >= {"voice", "music", "sfx"}
    # Ожидание по сырым файлам: голос во фразах против музыки × 0.5 (громкость трека, холст без речи
    # в основе — подгонки под голос нет).
    v = _level(owl["voice"])
    speech = power_mean([m for m in v["m"] if m > -40])
    music = _level(owl["music"], "volume=0.5")["i"]
    diff = report["voice_vs_music"]["diff_db"]
    assert diff == pytest.approx(speech - music, abs=1.5)
    assert diff < 12
    warn = [x for x in report["verdicts"] if x["check"] == "music" and x["severity"] == "warn"]
    assert warn and "музыка громче нормы" in warn[0]["message"] and "убавь музыку" in warn[0]["fix"]
    assert warn[0]["suggest"]["tool"] == "auto_mix"
    # Вжух на фразе громче голоса — слова тонут.
    assert any(x["check"] == "sfx" for x in report["verdicts"])
    row = next(r for r in report["layers"] if r["layer_id"] == "wh01")
    assert row["over_speech"] and row["excess_db"] > 0
    assert report["speech"]["seconds"] > 4 and report["predicted"]["final_gain_db"] is not None
    assert "music" in report["summary"] or "замечаний" in report["summary"]


def test_report_quiet_music_is_fine(owl):
    from app.audio_mix import mix_report

    store, plan_id = owl["store"], owl["plan_id"]
    store.update_audio_track(owl["track"]["id"], volume=0.02)  # −34 дБ к файлу: под речью тихо
    rs = dict(store.get_clip_plan(plan_id)["render_settings"])
    rs["layers"] = [layer for layer in rs["layers"] if layer["id"] == "narr"]
    store.set_clip_plan_render_settings(plan_id, rs)
    report = mix_report(store, plan_id)
    assert report["voice_vs_music"]["diff_db"] >= 15
    assert not [x for x in report["verdicts"] if x["severity"] == "warn" and x["check"] in ("music", "sfx")]


def test_tool_and_lint_check(owl):
    from app.agent_tools import ToolContext, list_tools, run_tool
    from app import quality_lint as Q

    store, plan_id = owl["store"], owl["plan_id"]
    assert "audio_mix_report" in {t.name for t in list_tools()}
    out, ok = run_tool(ToolContext(store=store, clip_plan_id=plan_id), "audio_mix_report", {})
    assert ok, out
    assert out["voice_vs_music"]["diff_db"] < 12 and "note" in out and "points" not in out["timeline"]
    full, ok = run_tool(ToolContext(store=store, clip_plan_id=plan_id), "audio_mix_report", {"timeline": True})
    assert ok and full["timeline"]["points"]

    lint = Q.lint_clip(store, plan_id, checks=["audio_mix"])
    assert lint["by_check"]["audio_mix"] >= 1 and lint["audio_mix"]["voice_vs_music_db"] < 12
    assert any(f["check"] == "audio_mix" and f["severity"] == "warn" for f in lint["findings"])
    assert "audio_mix" in Q.lint_clip(store, plan_id, checks=["mix"], media=False)["skipped"]
