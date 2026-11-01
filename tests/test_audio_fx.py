"""Звуковые эффекты (app/audio_fx.py): цепочки → фильтры ffmpeg, которые настоящий ffmpeg принимает
на синтетике; слой/голос/музыка/отрезок с цепочкой; инструменты set_audio_chain, audio_fx_reference,
auto_sfx (только записи пака), audio_crossfade."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from test_api_pipeline import _require_ffmpeg
from test_audio_mix import owl  # noqa: F401 - фикстура холста «как сова»


def _run_af(src: str, af: str, seconds: float = 1.5) -> subprocess.CompletedProcess:
    return subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-f", "lavfi", "-t", f"{seconds}",
                           "-i", src, "-af", af, "-f", "null", "-"], capture_output=True, text=True, timeout=120)


def _duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


# --------------------------------------------------------------------------- нормализация


def test_normalize_chain_presets_clamps_and_problems():
    from app.audio_fx import chain_rate, has_sweep, normalize_chain

    problems: list[str] = []
    chain = normalize_chain(["voice_clean", {"fx": "reverb", "preset": "hall", "mix": 3},
                             {"fx": "nope"}, {"fx": "phone"}, {"fx": "pitch", "semitones": 40}], problems)
    kinds = [e["fx"] for e in chain]
    assert kinds[:3] == ["eq", "deesser", "compressor"] and "reverb" in kinds and "legacy" in kinds
    rev = next(e for e in chain if e["fx"] == "reverb")
    assert rev["mix"] == 1.0 and rev["preset"] == "hall"
    assert next(e for e in chain if e["fx"] == "pitch")["semitones"] == 12.0
    assert any("nope" in p for p in problems) and any("вне" in p for p in problems)
    assert normalize_chain("sweep_out")[0] == {"fx": "lowpass", "f": 18000.0, "to": 300.0}
    assert has_sweep(normalize_chain("sweep_in")) and not has_sweep(normalize_chain("hall"))
    assert chain_rate(normalize_chain([{"fx": "speed", "rate": 1.5}, {"fx": "tempo", "rate": 0.5}])) == 0.75
    assert len(normalize_chain(["voice_broadcast"] * 4)) == 8  # не больше MAX_CHAIN
    eq = normalize_chain([{"fx": "eq", "bands": [{"f": 50000, "gain": 3}, {"f": 200}]}], problems)[0]
    assert eq["bands"] == [{"f": 20000.0, "gain": 3.0, "q": 1.0}]


# --------------------------------------------------------------------------- ffmpeg принимает


def test_every_effect_and_preset_builds_a_valid_ffmpeg_graph():
    _require_ffmpeg()
    from app.audio_fx import EFFECTS, PRESETS, chain_filters, normalize_chain

    chains = {f"fx:{kind}": normalize_chain([{"fx": kind, **({"kind": "radio"} if kind == "legacy" else {}),
                                              **({"to": 1200} if kind in ("lowpass", "highpass") else {})}])
              for kind in EFFECTS}
    chains.update({f"preset:{name}": normalize_chain(name) for name in PRESETS})
    chains["eq-bands"] = normalize_chain([{"fx": "eq", "low_cut": 90, "high_cut": 9000, "low": 3, "presence": 4,
                                           "high": -2, "bands": [{"f": 250, "gain": -4, "q": 2}]}])
    bad = {}
    for name, chain in chains.items():
        assert chain, name
        af = chain_filters(chain, "t", 1.5)
        proc = _run_af("anoisesrc=c=pink:r=48000:a=0.3", af)
        if proc.returncode != 0:
            bad[name] = (af, proc.stderr[-400:])
    assert not bad, bad


def test_lowpass_and_sweep_change_the_sound():
    _require_ffmpeg()
    from app.audio_fx import chain_filters, normalize_chain
    from app.audio_mix import ebur128

    base = ebur128("anoisesrc=c=white:r=48000:a=0.3:d=3", pre=["-f", "lavfi"])
    dull = ebur128("anoisesrc=c=white:r=48000:a=0.3:d=3", pre=["-f", "lavfi"],
                   af=chain_filters(normalize_chain([{"fx": "lowpass", "f": 400}])))
    assert base["i"] - dull["i"] > 6
    # Свип 18 кГц → 300 Гц за 3 с: конец глуше начала.
    swept = ebur128("anoisesrc=c=white:r=48000:a=0.3:d=3", pre=["-f", "lavfi"],
                    af=chain_filters(normalize_chain("sweep_out"), "s", 3.0))
    m = swept["m"]
    assert m[5] - m[-3] > 8, (m[5], m[-3])


def test_pitch_keeps_length_speed_reads_more(tmp_path):
    _require_ffmpeg()
    from app.audio_fx import chain_filters, normalize_chain

    for chain, expect in ((normalize_chain([{"fx": "pitch", "semitones": -5}]), 2.0),
                          (normalize_chain([{"fx": "speed", "rate": 2.0}]), 1.0),
                          (normalize_chain([{"fx": "tempo", "rate": 0.5}]), 4.0)):
        out = tmp_path / "o.wav"
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                        "sine=f=440:r=48000:d=2", "-af", chain_filters(chain), str(out)], check=True)
        assert _duration(out) == pytest.approx(expect, abs=0.08), chain


def test_layer_item_with_fx_is_exactly_layer_long(tmp_path):
    """Слой 1.2 с со speed ×1.5 читает 1.8 с файла; на выходе ровно длина слоя."""
    _require_ffmpeg()
    from app.motion_render import audio_item_chain, audio_item_inputs, audio_layer_items

    src = tmp_path / "s.wav"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                    "sine=f=300:r=48000:d=5", str(src)], check=True)
    layer = {"id": "fx1", "type": "audio", "start": 0.5, "end": 1.7,
             "props": {"asset_id": "7", "mode": "track", "volume": 1.0,
                       "fx": [{"fx": "speed", "rate": 1.5}, {"fx": "reverb", "preset": "room", "mix": 0.3}]}}
    items = audio_layer_items([layer], {"7": str(src)}, 5.0)
    assert items[0]["read"] == pytest.approx(1.8) and items[0]["fx"]
    out = tmp_path / "o.wav"
    graph = audio_item_chain(0, items[0], delay=False) + "[o]"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-nostdin", "-v", "error", *audio_item_inputs(items[0]),
                    "-filter_complex", graph, "-map", "[o]", str(out)], check=True)
    assert _duration(out) == pytest.approx(1.2, abs=0.03)
    # Голос клипа (mix.voice_fx) дописывается к цепочке слоя-голоса.
    voice = {"id": "narr", "type": "audio", "start": 0, "end": 2, "props": {"asset_id": "7", "mode": "track"}}
    got = audio_layer_items([voice], {"7": str(src)}, 5.0, mix={"voice_fx": [{"fx": "deesser", "amount": 0.4}]})
    assert got[0]["fx"] == [{"fx": "deesser", "amount": 0.4}]


def test_span_chain_music_fx_and_voice_pass_graphs(tmp_path):
    _require_ffmpeg()
    from app.audio_edit import fx_graph, normalize_fx
    from app.audio_mix import normalize_mix
    from app.render import _music_filter, build_voice_fx_args

    fx = normalize_fx([{"start": 0.5, "end": 1.5, "fx": "chain", "preset": "hall"},
                       {"start": 1.6, "end": 2.4, "fx": "chain", "chain": [{"fx": "lowpass", "f": 300, "to": 6000},
                                                                          {"fx": "speed", "rate": 2}]}])
    assert fx[0]["chain"][0]["fx"] == "reverb" and [e["fx"] for e in fx[1]["chain"]] == ["lowpass"]  # без speed
    graph = ";".join(fx_graph("0:a", fx, "t", 0.0, "out", "u"))
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                           "anoisesrc=c=pink:r=48000:a=0.3:d=3", "-filter_complex", graph, "-map", "[out]", "-f", "null",
                           "-"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[-500:]

    mix = normalize_mix({"music_fx": ["lofi", {"fx": "tempo", "rate": 1.2}], "voice_fx": "voice_clean"})
    assert all(e["fx"] != "tempo" for e in mix["music_fx"]) and mix["voice_fx"][0]["fx"] == "eq"
    music = tmp_path / "m.wav"
    clip = tmp_path / "c.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                    "anoisesrc=c=pink:r=48000:a=0.3:d=6", str(music)], check=True)
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=black:s=32x32:r=10:d=3", "-f", "lavfi", "-i", "sine=f=220:r=48000:d=3", "-shortest",
                    "-c:v", "libx264", "-c:a", "aac", str(clip)], check=True)
    graph = _music_filter({"volume": 0.3, "duck": True, "duration_sec": 3, "fx": mix["music_fx"]})
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-i", str(clip), "-i", str(music),
                           "-filter_complex", graph, "-map", "[aout]", "-f", "null", "-"], capture_output=True, text=True)
    assert proc.returncode == 0 and "acrusher" in graph, proc.stderr[-500:]
    out = tmp_path / "v.mp4"
    subprocess.run(build_voice_fx_args(clip, out, mix["voice_fx"]), check=True)
    assert _duration(out) == pytest.approx(3.0, abs=0.1)


# --------------------------------------------------------------------------- инструменты


def test_set_audio_chain_layer_voice_music_and_reference(owl):  # noqa: F811
    from app.agent_tools import ToolContext, list_tools, run_tool
    from app.audio_mix import mix_report

    store, plan_id = owl["store"], owl["plan_id"]
    ctx = ToolContext(store=store, clip_plan_id=plan_id)
    names = {t.name for t in list_tools()}
    assert {"audio_fx_reference", "set_audio_chain", "auto_sfx", "audio_crossfade"} <= names
    ref, ok = run_tool(ctx, "audio_fx_reference", {})
    assert ok and "reverb" in ref["effects"] and "telephone" in ref["presets"] and ref["span_fx"]["kinds"]["chain"]

    before = mix_report(store, plan_id, use_render=False)["stems"]["voice"]["lufs"]
    out, ok = run_tool(ctx, "set_audio_chain", {"target": "layer", "layer_id": "narr",
                                                 "chain": [{"fx": "gain", "db": 6}]})
    assert ok and out["chain"] == [{"fx": "gain", "db": 6.0}] and "Усиление" in out["chain_text"]
    after = mix_report(store, plan_id, use_render=False)["stems"]["voice"]["lufs"]
    assert after - before == pytest.approx(6.0, abs=0.5)  # отчёт слышит цепочку слоя

    out, ok = run_tool(ctx, "set_audio_chain", {"target": "voice", "preset": "voice_clean"})
    assert ok and out["chain"][0]["fx"] == "eq"
    out, ok = run_tool(ctx, "set_audio_chain", {"target": "music", "preset": "lofi", "append": True})
    assert ok and any(e["fx"] == "bitcrush" for e in out["chain"])
    rs = store.get_clip_plan(plan_id)["render_settings"]
    assert rs["mix"]["voice_fx"] and rs["mix"]["music_fx"]
    out, ok = run_tool(ctx, "set_audio_chain", {"target": "layer", "layer_id": "narr", "chain": []})
    assert ok and out["chain"] == []
    bad, ok = run_tool(ctx, "set_audio_chain", {"target": "layer", "layer_id": "nope", "preset": "hall"})
    assert not ok
    bad, ok = run_tool(ctx, "set_audio_chain", {"target": "music", "preset": "no-such"})
    assert not ok and "не принята" in bad["error"]


def test_auto_sfx_only_pack_sounds_and_crossfade(owl, monkeypatch):  # noqa: F811
    from app import audio_tools
    from app.agent_tools import ToolContext, run_tool

    store, plan_id = owl["store"], owl["plan_id"]
    ctx = ToolContext(store=store, clip_plan_id=plan_id)
    rs = dict(store.get_clip_plan(plan_id)["render_settings"])
    rs["layers"] = rs["layers"] + [
        {"id": "img1", "type": "image", "start": 0.0, "end": 4.0, "props": {"src": "emoji:1f989"}},
        {"id": "img2", "type": "image", "start": 4.0, "end": 8.0, "props": {"src": "emoji:1f989"}},
        {"id": "img3", "type": "image", "start": 8.0, "end": 10.0, "props": {"src": "emoji:1f989"}},
        {"id": "t1", "type": "text", "start": 2.82, "end": 4.0, "props": {"text": "#1"}},
        {"id": "t2", "type": "text", "start": 6.0, "end": 7.0, "props": {"text": "#2"}},
    ]
    store.set_clip_plan_render_settings(plan_id, rs)
    # Пака нет — ничего не ставит и не выдумывает.
    monkeypatch.setattr(audio_tools, "_sound_ok", lambda ref: (False, 0.0))
    out, ok = run_tool(ctx, "auto_sfx", {"on": ["cuts", "badges"]})
    assert ok and out["count"] == 0 and "синтетику не подставляю" in out["warnings"][-1]
    monkeypatch.setattr(audio_tools, "_sound_ok", lambda ref: (True, 1.2))
    out, ok = run_tool(ctx, "auto_sfx", {"on": ["cuts", "badges"], "riser_at": [8.0], "dry_run": True})
    assert ok and not out["applied"]
    starts = {p["id"]: p["start"] for p in out["placed"]}
    assert starts["asfx_cut01"] == pytest.approx(3.95) and starts["asfx_cut02"] == pytest.approx(7.95)
    assert "asfx_riser01" in starts and starts["asfx_riser01"] == pytest.approx(6.8)
    # Плашка t1 на 2.82 — рядом уже стоит вжух wh01 (2.8): второй звук туда не ставится.
    assert all(abs(p["start"] - 2.82) > 0.2 for p in out["placed"] if p["id"].startswith("asfx_badge"))
    assert any(p["start"] == 6.0 for p in out["placed"])
    out, ok = run_tool(ctx, "auto_sfx", {"on": ["cuts"]})
    layers = store.get_clip_plan(plan_id)["render_settings"]["layers"]
    auto = [layer for layer in layers if layer["id"].startswith("asfx_")]
    assert ok and auto and all(layer["props"]["role"] == "sfx" and layer["props"]["asset_id"].startswith("sfx:freesound/")
                               for layer in auto)
    again, ok = run_tool(ctx, "auto_sfx", {"on": ["cuts"]})
    layers = store.get_clip_plan(plan_id)["render_settings"]["layers"]
    assert ok and len([x for x in layers if x["id"].startswith("asfx_")]) == len(auto)  # заменил, не дописал

    out, ok = run_tool(ctx, "audio_crossfade", {"from_layer": "narr", "to_layer": "wh01", "duration": 0.3})
    assert ok
    layers = {x["id"]: x for x in store.get_clip_plan(plan_id)["render_settings"]["layers"]}
    assert layers["narr"]["props"]["fadeOut"] == 0.3 and layers["wh01"]["props"]["fadeIn"] == 0.3
