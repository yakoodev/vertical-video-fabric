"""Сведение звука (app/audio_mix.py): стемы как у рендера, голос против музыки под речью, звуки
против слов, итог по LUFS/true peak; MCP audio_mix_report и проверка lint_clip ``audio_mix``.

Звук синтетический (тестовый): «голос» — розовый шум фразами 1.2 с через 1.3 с паузы, «музыка» —
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


def speech_like(path: Path, seconds: float = 9.0, amplitude: float = 0.5) -> Path:
    """Фразы 1.2 с через паузу 1.3 с (речь с паузами)."""
    return _wav(path, f"anoisesrc=c=pink:r=48000:a={amplitude}:seed=11", seconds,
                "volume='if(lt(mod(t,2.5),1.2),1,0)':eval=frame")


def music_like(path: Path, seconds: float = 20.0, amplitude: float = 1.0) -> Path:
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
        {"id": "wh01", "type": "audio", "start": 2.8, "end": 3.2,
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


# --------------------------------------------------------------------------- автосведение


def test_auto_mix_fixes_loud_music_and_sfx(owl):
    from app.agent_tools import ToolContext, run_tool
    from app.audio_mix import mix_report

    store, plan_id = owl["store"], owl["plan_id"]
    ctx = ToolContext(store=store, clip_plan_id=plan_id)
    dry, ok = run_tool(ctx, "auto_mix", {"dry_run": True})
    assert ok, dry
    assert not dry["applied"] and not store.get_clip_plan(plan_id)["render_settings"].get("mix")
    assert dry["before"]["voice_vs_music_db"] < 12 <= dry["after"]["voice_vs_music_db"]

    out, ok = run_tool(ctx, "auto_mix", {})
    assert ok, out
    rs = store.get_clip_plan(plan_id)["render_settings"]
    mix = rs["mix"]
    assert mix["target_lufs"] == -14.0 and mix["sidechain"] is False and mix["music_db"] < -10
    assert mix["layer_gains"]["wh01"] < -10 and mix["auto"]["music_under_voice_db"] == -18.0
    assert rs["audio"]["music_duck"] and all(d["gain_db"] == -10.0 for d in rs["audio"]["music_duck"])
    assert any("музыка" in c for c in out["changes"])

    after = mix_report(store, plan_id, use_render=False)
    assert 15.0 <= after["voice_vs_music"]["diff_db"] <= 21.0, after["voice_vs_music"]
    bad = [v for v in after["verdicts"] if v["severity"] == "warn" and v["check"] in ("music", "sfx")]
    assert not bad, after["verdicts"]
    # Повтор не накапливает поправки — считает от исходной громкости.
    again, ok = run_tool(ctx, "auto_mix", {})
    assert ok and abs(again["mix"]["music_db"] - mix["music_db"]) <= 0.6
    assert abs(again["mix"]["layer_gains"]["wh01"] - mix["layer_gains"]["wh01"]) <= 0.6


def test_set_mix_tool_merges_and_resets(owl):
    from app.agent_tools import ToolContext, run_tool

    store, plan_id = owl["store"], owl["plan_id"]
    ctx = ToolContext(store=store, clip_plan_id=plan_id)
    out, ok = run_tool(ctx, "set_mix", {"music_db": -20, "layer_gains": {"wh01": -8, "ghost": -3}})
    assert ok and out["mix"]["music_db"] == -20 and out["mix"]["layer_gains"] == {"wh01": -8, "ghost": -3}
    assert "ghost" in out["warnings"][0]
    out, ok = run_tool(ctx, "set_mix", {"layer_gains": {"ghost": None}, "sidechain": False})
    assert ok and out["mix"]["layer_gains"] == {"wh01": -8} and out["mix"]["sidechain"] is False
    out, ok = run_tool(ctx, "set_mix", {"reset": True, "target_lufs": -16})
    assert ok and out["mix"] == {"target_lufs": -16.0}
    # Редактор шлёт настройки без mix — сведение не стирается.
    plan = store.get_clip_plan(plan_id)
    body = {k: v for k, v in plan["render_settings"].items() if k != "mix"}
    resp = owl["client"].patch(f"/api/clip-plans/{plan_id}/render-settings", json={"settings": body})
    assert resp.status_code == 200, resp.text
    assert store.get_clip_plan(plan_id)["render_settings"]["mix"] == {"target_lufs": -16.0}


def test_canvas_music_automation_reaches_render():
    """Холст: ducking музыки под озвучкой раньше молча терялся (render_plan холста был None)."""
    from app.audio_edit import music_volume_expr, render_plan
    from app.render import _music_filter, _music_mix_fields, _preset_with_music_automation, music_volume_for

    plan = {"segments": [{"id": 1, "start_sec": 0, "end_sec": 10}],
            "render_settings": {"audio": {"music_duck": [{"start": 1, "end": 3, "gain_db": -10}]},
                                "mix": {"duck_attack": 0.2, "duck_release": 0.5}}}
    got = render_plan(plan, {"source_type": "canvas"})
    assert got and got["music"]["duck"][0]["gain_db"] == -10 and got["music"]["ramp_in"] == 0.2
    assert "0.500" in music_volume_expr(got["music"])
    preset = _preset_with_music_automation({}, {**got, "mix": {"music_db": -22, "sidechain": False}})
    assert preset["mix"]["music_db"] == -22 and preset["music_automation"]
    fields = _music_mix_fields(preset["mix"])
    assert fields == {"fixed_db": -22.0, "sidechain": False}
    assert music_volume_for(Path("nope.mp4"), {"volume": 0.5, **fields}) == pytest.approx(10 ** (-22 / 20))
    graph = _music_filter({"volume": 0.1, "duck": True, **fields, "duration_sec": 10})
    assert "sidechaincompress" not in graph
    assert "sidechaincompress" in _music_filter({"volume": 0.1, "duck": True, "duration_sec": 10})


def test_auto_mix_measured_after_real_render_passes(owl):
    """После auto_mix — настоящие проходы рендера (музыка → звуки слоёв → громкость) на синтетике:
    итог −14 LUFS ±1 LU, true peak ≤ −1 dBTP; голос над музыкой под речью — в цели."""
    from app.agent_tools import ToolContext, run_tool
    from app.audio_edit import music_automation
    from app.audio_mix import ebur128, normalize_mix, power_mean
    from app.motion import plan_motion
    from app.motion_render import build_audio_layers_args, resolve_layer_assets
    from app.render import (_measure_loudness, _resolve_music_settings, build_ffmpeg_music_args, fit_loudness,
                            loudness_filters, music_volume_for)

    store, plan_id, tmp = owl["store"], owl["plan_id"], owl["tmp"]
    out, ok = run_tool(ToolContext(store=store, clip_plan_id=plan_id), "auto_mix", {})
    assert ok, out
    rs = store.get_clip_plan(plan_id)["render_settings"]
    mix = normalize_mix(rs["mix"])
    base = tmp / "base.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "color=c=black:s=64x64:r=10:d=10", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "10",
                    "-c:v", "libx264", "-c:a", "aac", "-shortest", str(base)], check=True)
    preset = {"music_track_id": rs["track_id"], "music_automation": music_automation(rs), "mix": mix}
    music = _resolve_music_settings(store, preset)
    assert music["fixed_db"] == mix["music_db"] and music["sidechain"] is False
    music.update(duration_sec=10.0, hold_sec=0.0)
    music["volume"] = music_volume_for(base, music)
    with_music = tmp / "music.mp4"
    subprocess.run(build_ffmpeg_music_args(base, with_music, music), check=True)
    layers, assets, _notes = resolve_layer_assets(store, plan_motion(rs)["layers"])
    mixed = tmp / "layers.mp4"
    subprocess.run(build_audio_layers_args(with_music, mixed, layers, assets, 10.0, mix=mix), check=True)
    target = mix["target_lufs"]
    measured = _measure_loudness(mixed, target)
    plan = fit_loudness(measured, target, lambda f: _measure_loudness(mixed, target, f))
    chain = ",".join(loudness_filters(plan))
    final = tmp / "final.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(mixed), "-c:v", "copy",
                    "-af", chain, "-c:a", "aac", "-b:a", "256k", str(final)], check=True)
    got = ebur128(final)
    assert got["i"] == pytest.approx(-14.0, abs=1.0)
    assert got["tp"] <= -1.0
    # Музыка отдельно той же цепочкой финала: под речью на 15–21 дБ тише голоса.
    music_m = ebur128(with_music, af=chain)["m"]
    voice_m = ebur128(owl["voice"], af=chain)["m"]
    frames = [i for i, v in enumerate(voice_m) if v > max(voice_m) - 12 and i < len(music_m)]
    diff = power_mean([voice_m[i] for i in frames]) - power_mean([music_m[i] for i in frames])
    assert 15.0 <= diff <= 21.0, diff
