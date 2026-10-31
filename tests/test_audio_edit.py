"""Звуковой монтаж: J/L-склейки, тишина/громкость одной дорожки, ключи громкости, эффекты на
отрезке, автоматизация музыки — графы ffmpeg, план склеек, обратная совместимость и инструменты."""

from __future__ import annotations

import subprocess

import pytest

from app.agent_tools import ToolContext, run_tool
from app.audio_edit import (
    FX_KINDS,
    JL_XFADE_SEC,
    handle_windows,
    jl_audio_filters,
    jl_speech_report,
    keys_db_expr,
    keys_value,
    music_volume_expr,
    normalize_audio,
    part_shifts,
    plan_jl,
    remap_audio,
    source_graph,
    wants_render_plan,
)
from app.audio_mute import normalize_gain, normalize_mute
from app.clip_history import snapshot
from app.clip_settings import normalize_render_settings
from test_api_pipeline import _require_ffmpeg
from test_cuts import _mock_render, _two_pieces

TWO_TRACKS = {"streams": [{"codec_type": "video"}, {"codec_type": "audio", "tags": {"title": "Игра"}},
                          {"codec_type": "audio", "tags": {"title": "Микрофон"}}]}


def _ctx(store, plan_id=None):
    return ToolContext(store=store, clip_plan_id=plan_id,
                       on_first_change=(lambda: snapshot(store, plan_id, "тест")) if plan_id else None)


def _expr_value(expr: str, t: float) -> float:
    """Посчитать выражение громкости ffmpeg в Python (clip/min/max/if/lt/lte/pow)."""
    env = {"clip": lambda v, lo, hi: min(hi, max(lo, v)), "min": min, "max": max, "pow": pow,
           "lt": lambda a, b: a < b, "lte": lambda a, b: a <= b, "t": t,
           "iff": lambda c, a, b: a if c else b}
    return eval(expr.replace("if(", "iff("), env)


# ------------------------------------------------------------------ нормализация, совместимость


def test_mute_and_gain_keep_track_and_merge_only_within_it():
    raw = [{"start": 1, "end": 2, "track": 1}, {"start": 1.5, "end": 3, "track": 1}, {"start": 1.2, "end": 1.8},
           {"start": 4, "end": 5, "track": "x"}, {"start": 6, "end": 7, "track": 9}]
    assert normalize_mute(raw) == [{"start": 1.0, "end": 3.0, "track": 1}, {"start": 1.2, "end": 1.8},
                                   {"start": 4.0, "end": 5.0}, {"start": 6.0, "end": 7.0}]
    # Мат одной дорожки — отдельно от мата всего звука на том же месте.
    tagged = normalize_mute([{"start": 1, "end": 2, "by": "censor", "level": 2, "track": 1},
                             {"start": 1, "end": 2, "by": "censor", "level": 2}])
    assert len(tagged) == 2 and tagged[0].get("track") is None and tagged[1]["track"] == 1
    gains = normalize_gain([{"start": 0, "end": 2, "gain_db": -6, "track": 0}, {"start": 1, "end": 3, "gain_db": 6}])
    assert gains == [{"start": 0.0, "end": 2.0, "gain_db": -6.0, "track": 0}, {"start": 1.0, "end": 3.0, "gain_db": 6.0}]
    # Старые отрезки без дорожки — ровно как раньше.
    assert normalize_mute([{"start": 1, "end": 2}, {"start": 1.5, "end": 2.5}]) == [{"start": 1.0, "end": 2.5}]


def test_old_plans_have_no_audio_edit():
    assert normalize_render_settings({})["audio"] == {}
    assert normalize_audio({"joins": [{"piece": 1, "lead": 0}], "fx": [{"start": 1, "end": 1.05, "fx": "echo"}]}) == {}
    assert not wants_render_plan(normalize_render_settings({"mute": [{"start": 1, "end": 2}]}))
    assert wants_render_plan({"mute": [{"start": 1, "end": 2, "track": 1}]})
    clean = normalize_audio({"joins": [{"piece": 4, "lag": 9}], "music_dips": [3.0, {"at": 0.2}],
                             "fx": [{"start": 0, "end": 30, "fx": "speed", "rate": 7}, {"start": 1, "end": 2, "fx": "x"}],
                             "music_keys": [{"t": 1, "gain_db": -99, "ease": "boing"}]})
    assert clean["joins"] == [{"piece": 4, "lag": 1.5}]
    assert clean["music_dips"] == [{"at": 0.2, "before": 0.2}, {"at": 3.0, "before": 0.5}]
    assert clean["fx"] == [{"start": 0.0, "end": 12.0, "fx": "speed", "rate": 2.0}]
    assert clean["music_keys"] == [{"t": 1.0, "gain_db": -60.0, "ease": "linear"}]


def test_render_commands_unchanged_without_audio_edit(tmp_path, monkeypatch):
    store, service, plan, preset, calls = _mock_render(tmp_path, monkeypatch, [(0, 5), (10, 16)])
    store.set_clip_plan_render_settings(plan["id"], {"mute": [{"start": 1, "end": 2}]})
    service.render_clip_plan(plan["id"], ffmpeg_preset_id=preset["id"])
    graphs = " ".join(" ".join(c) for c in calls)
    assert "a_src_t" not in graphs and "acrossfade" not in graphs and "handle-" not in graphs
    assert service._audio_plan(store.get_clip_plan(plan["id"])) is None


# ------------------------------------------------------------------ дорожки


def test_track_mute_goes_into_the_part_graph_not_the_final_pass(tmp_path, monkeypatch):
    store, service, plan, preset, calls = _mock_render(tmp_path, monkeypatch, [(0, 5), (10, 16)])
    store.update_source(plan["source_id"], metadata=TWO_TRACKS, audio_tracks=[0, 1])
    store.set_clip_plan_render_settings(plan["id"], {"mute": [{"start": 6.0, "end": 7.0, "track": 1}]})
    service.render_clip_plan(plan["id"], ffmpeg_preset_id=preset["id"])
    parts = [c for c in calls if "-frames:v" in c]
    first, second = (c[c.index("-filter_complex") + 1] for c in parts[:2])
    # Секунда клипа 6–7 = секунды исходника 11–12 (второй кусок), только дорожка 1, до смешивания.
    assert "[0:a:1]volume='(1-1.00000*min(clip(((t+10.000)-11.000)" in second
    assert "[0:a:0]anull" in second and "a_src_t" not in first
    assert not any("-af" in c and "volume='" in " ".join(c) for c in calls)  # финальный проход тишины не нужен


def test_track_mute_really_silences_one_track(tmp_path):
    _require_ffmpeg()
    from test_audio_mute import _rms

    src = tmp_path / "two.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=150:d=4", "-f", "lavfi", "-i",
                    "sine=f=5000:d=4", "-map", "0", "-map", "1", "-c:a", "pcm_s16le", str(src)], check=True)
    edit = {"ops": [{"kind": "vol", "track": 1, "start": 1.0, "end": 2.0, "factor": 0.0}], "src0": 0.0}
    out = tmp_path / "o.wav"
    graph = source_graph(0, [0, 1], edit, "a")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-filter_complex",
                    graph + ";[a]highpass=f=2500,highpass=f=2500,highpass=f=2500[h]", "-map", "[h]", str(out)], check=True)
    assert _rms(out, 1.2, 0.6) < _rms(out, 2.4, 0.6) - 30  # микрофон (5 кГц) молчит
    low = tmp_path / "low.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-filter_complex",
                    graph + ";[a]lowpass=f=500,lowpass=f=500,lowpass=f=500[l]", "-map", "[l]", str(low)], check=True)
    assert abs(_rms(low, 1.2, 0.6) - _rms(low, 2.4, 0.6)) < 1.5  # игра (150 Гц) звучит


def test_censor_can_mute_only_the_voice_track(tmp_path, monkeypatch):
    from app import censor

    store, _service, plan, _preset = _two_pieces(tmp_path, monkeypatch)
    store.update_source(plan["source_id"], metadata=TWO_TRACKS, audio_tracks=[0, 1], speech_tracks=[1])
    store.set_clip_plan_subtitles(plan["id"], {"lines": [{"start": 5.0, "end": 6.0, "text": "блять"}],
                                               "words": [{"word": "блять", "start": 5.0, "end": 5.6}]})
    out = censor.censor_plan(store, plan["id"])
    assert "track" not in out["words"][0] and 'track "voice"' in out["note"]
    out = censor.censor_plan(store, plan["id"], track="voice")
    assert out["track"] == 1 and out["words"][0]["track"] == 1
    mute = store.get_clip_plan(plan["id"])["render_settings"]["mute"]
    assert mute and all(m["track"] == 1 and m["by"] == "censor" for m in mute)
    # Слово, что протекает в игру, — целиком (verify передаёт full_words).
    out = censor.censor_plan(store, plan["id"], track="voice", full_words=[0])
    assert "track" not in store.get_clip_plan(plan["id"])["render_settings"]["mute"][0]
    with pytest.raises(ValueError):
        censor.censor_plan(store, plan["id"], track=5)
    # Без своей дорожки речи «voice» — весь звук, с пояснением.
    store.update_source(plan["source_id"], speech_tracks=[])
    out = censor.censor_plan(store, plan["id"], track="voice")
    assert "track" not in out and "неизвестна" in out["note"]


# ------------------------------------------------------------------ J/L


def test_plan_jl_keeps_total_length():
    durations = [5.0, 6.0, 4.0, 3.0]
    for overlaps in ([0, 0, 0], [0.3, 0, 0.25]):
        for shifts in ([0.5, -0.4, 0.0], [-1.5, 1.5, 0.3], [0, 0, 0]):
            for mode in ("smooth", "hard"):
                plan = plan_jl(durations, overlaps, shifts, mode)
                lengths = [d + h + t for d, h, t in zip(durations, plan["head"], plan["tail"])]
                assert sum(lengths) - sum(plan["xfade"]) == pytest.approx(sum(durations) - sum(overlaps), abs=1e-6)
    plan = plan_jl([5.0, 6.0], [0.0], [0.5])
    assert plan["tail"][0] == pytest.approx(0.5 + JL_XFADE_SEC / 2)
    assert plan["head"][1] == pytest.approx(JL_XFADE_SEC / 2 - 0.5) and plan["xfade"] == [JL_XFADE_SEC]
    # Не больше 45% соседнего куска, джамп-кат — без J/L.
    assert plan_jl([1.0, 6.0], [0.0], [1.5])["shift"] == [0.45]
    assert plan_jl([5.0, 6.0], [0.0], [0.5], jump_joins=[True])["shift"] == [0.0]


def test_part_shifts_by_piece_id_and_cut_parts():
    segs = [{"id": 7, "start_sec": 0, "end_sec": 5}, {"id": 8, "start_sec": 10, "end_sec": 16},
            {"id": 9, "start_sec": 20, "end_sec": 24}]
    joins = [{"piece": 7, "lag": 0.4}, {"piece": 9, "lead": 0.3}, {"piece": 8, "lag": 0.1}]
    assert part_shifts(segs, segs, joins) == [0.4, pytest.approx(-0.2)]
    # Куски с вырезами: J/L только на стыке разных кусков, джамп-кат внутри куска 1 — без них.
    parts = [{"piece": 0, "start_sec": 0, "end_sec": 5}, {"piece": 1, "start_sec": 10, "end_sec": 12},
             {"piece": 1, "start_sec": 13, "end_sec": 16}, {"piece": 2, "start_sec": 20, "end_sec": 24}]
    assert part_shifts(parts, segs, joins) == [0.4, 0.0, pytest.approx(-0.2)]


def test_jl_render_reads_handles_and_crossfades(tmp_path, monkeypatch):
    store, service, plan, preset, calls = _mock_render(tmp_path, monkeypatch, [(0, 5), (10, 16)])
    segs = plan["segments"]
    store.set_clip_plan_render_settings(plan["id"], {"audio": {"joins": [{"piece": segs[0]["id"], "lag": 0.5}]}})
    service.render_clip_plan(plan["id"], ffmpeg_preset_id=preset["id"])
    handle = next(c for c in calls if "handle-000-tail.wav" in c[-1])
    # Ручка L — звук исходника сразу после конца первого куска: 0.5 + половина кроссфейда.
    assert handle[handle.index("-ss") + 1] == "5.000" and handle[handle.index("-t") + 1] == "0.540"
    join = next(c for c in calls if "acrossfade=d=0.080" in " ".join(c))
    graph = join[join.index("-filter_complex") + 1]
    assert "atrim=start=0.460" in graph  # голова второго куска уступает место хвосту первого
    assert sum(1 for x in join if x == "-i") == 3  # две части + ручка
    assert "concat=n=2:v=1:a=0" in graph or "concat=n=2:v=1" in graph  # картинка встык, как раньше


def test_jl_moves_sound_in_a_real_join(tmp_path):
    """Картинка встык, L 0.5 с: на первых 0.5 с второго куска звучит тон первого (300 Гц), потом свой."""
    _require_ffmpeg()
    from app.transitions import build_join_args, normalize_transition
    from test_audio_mute import _rms

    def part(path, freq):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=64x64:d=2:r=25",
                        "-f", "lavfi", "-i", f"sine=f={freq}:d=2", "-shortest", "-c:v", "libx264", "-c:a", "aac",
                        "-ar", "48000", "-ac", "2", str(path)], check=True)

    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    part(a, 300)
    part(b, 1500)
    tail = tmp_path / "tail.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=300:d=0.54", "-ar", "48000", "-ac", "2",
                    str(tail)], check=True)
    plan = plan_jl([2.0, 2.0], [0.0], [0.5])
    out = tmp_path / "j.mp4"
    args = build_join_args([a, b], [2.0, 2.0], out, normalize_transition({"type": "cut"}), has_audio=True,
                           jl={"plan": plan, "handles": [(0, "tail", tail)]})
    subprocess.run(args, check=True)
    low, high = tmp_path / "low.wav", tmp_path / "high.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(out), "-af", "lowpass=f=600", str(low)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(out), "-af", "highpass=f=1000", str(high)], check=True)
    assert _rms(low, 2.1, 0.3) > _rms(high, 2.1, 0.3) + 15  # 2.1–2.4: ещё звук первого куска
    assert _rms(high, 2.7, 0.5) > _rms(low, 2.7, 0.5) + 15  # дальше — свой
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                str(out)], capture_output=True, text=True).stdout)
    assert dur == pytest.approx(4.0, abs=0.08)


def test_jl_filters_with_head_handle():
    plan = plan_jl([3.0, 3.0], [0.0], [-0.4])
    windows = handle_windows([{"start_sec": 0, "end_sec": 3}, {"start_sec": 10, "end_sec": 13}], plan)
    assert windows == [{"part": 1, "side": "head", "start": 9.56, "length": 0.44}]
    graph = ";".join(jl_audio_filters(2, [3.0, 3.0], plan, {(1, "head"): 2}, None, True))
    assert "[2:a]" in graph and "atrim=end=2.640" in graph and "acrossfade=d=0.080" in graph


def test_jl_report_names_hidden_words():
    segs = [{"id": 1, "start_sec": 0, "end_sec": 5}, {"id": 2, "start_sec": 10, "end_sec": 15}]
    words = [{"word": "конец", "start": 4.0, "end": 4.5}, {"word": "привет", "start": 5.1, "end": 5.4}]
    report = jl_speech_report(segs, [{"piece": 1, "lag": 0.5}], words)
    assert report[0]["kind"] == "L" and report[0]["lost_words"] == ["привет"]
    assert jl_speech_report(segs, [{"piece": 2, "lead": 0.3}], words)[0]["lost_words"] == []


# ------------------------------------------------------------------ ключи, музыка


def test_keys_expression_matches_python_value():
    keys = [{"t": 1.0, "gain_db": 0.0, "ease": "linear"}, {"t": 3.0, "gain_db": -12.0, "ease": "smooth"},
            {"t": 4.0, "gain_db": -12.0, "ease": "linear"}, {"t": 5.0, "gain_db": 0.0, "ease": "step"}]
    expr = keys_db_expr(keys)
    for t in (0.0, 1.0, 1.7, 2.0, 2.9, 3.5, 4.5, 4.99, 5.0, 7.0):
        assert _expr_value(expr, t) == pytest.approx(keys_value(keys, t), abs=1e-6), t
    assert keys_value(keys, 2.0) == pytest.approx(-6.0)
    assert keys_value(keys, 4.9) == -12.0 and keys_value(keys, 5.0) == 0.0  # step — скачком на ключе


def test_music_dip_is_silent_before_the_hit_and_back_on_it():
    expr = music_volume_expr({"keys": [], "duck": [{"start": 6, "end": 8, "gain_db": -12}],
                              "dips": [{"at": 3.0, "before": 0.5}]})
    assert _expr_value(expr, 2.0) == pytest.approx(1.0)
    assert _expr_value(expr, 2.7) == pytest.approx(0.0, abs=1e-6)
    assert _expr_value(expr, 3.02) == pytest.approx(1.0)  # удар — уже с музыкой
    assert _expr_value(expr, 7.0) == pytest.approx(10 ** (-12 / 20), abs=1e-4)


def test_music_pass_gets_automation_in_final_time(tmp_path, monkeypatch):
    from app.render import _music_filter

    store, service, plan, _preset = _two_pieces(tmp_path, monkeypatch, {"cuts": [{"start": 2.0, "end": 3.0}]})
    store.set_clip_plan_render_settings(plan["id"], {**plan["render_settings"],
                                                     "audio": {"music_dips": [{"at": 6.0, "before": 0.5}]}})
    plan = store.get_clip_plan(plan["id"])
    audio_plan = service._audio_plan(plan, service._cut_plan(plan))
    assert audio_plan["music"]["dips"] == [{"at": 5.0, "before": 0.5}]  # вырез 1 с — удар на 5.0 готового
    graph = _music_filter({"track_path": "m.mp3", "volume": 0.5, "automation": audio_plan["music"]})
    assert "volume='(1-1.00000*min(clip((t-4.500)/0.060" in graph


def test_keys_really_change_volume(tmp_path):
    _require_ffmpeg()
    from test_audio_mute import _rms

    src = tmp_path / "s.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=440:d=4", str(src)], check=True)
    keys = [{"t": 0.0, "gain_db": 0.0, "ease": "linear"}, {"t": 1.0, "gain_db": 0.0, "ease": "linear"},
            {"t": 1.1, "gain_db": -12.0, "ease": "linear"}]
    out = tmp_path / "o.wav"
    graph = source_graph(0, [0], {"keys": keys, "clip0": 0.0, "src0": 0.0}, "a")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-filter_complex", graph, "-map", "[a]", str(out)],
                   check=True)
    assert _rms(out, 0.2, 0.6) - _rms(out, 2.0, 1.0) == pytest.approx(12, abs=1.0)


# ------------------------------------------------------------------ эффекты


@pytest.mark.parametrize("fx", sorted(FX_KINDS))
def test_every_fx_renders_and_keeps_length(tmp_path, fx):
    _require_ffmpeg()
    src = tmp_path / "s.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anoisesrc=d=3:c=white:a=0.2", "-ar", "48000",
                    str(src)], check=True)
    edit = {"ops": [{"kind": "fx", "track": None, "start": 1.0, "end": 2.0, "fx": fx, "rate": 0.5}], "src0": 0.0}
    out = tmp_path / "o.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-filter_complex", source_graph(0, [0], edit, "a"),
                    "-map", "[a]", str(out)], check=True)
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                str(out)], capture_output=True, text=True).stdout)
    assert dur == pytest.approx(3.0, abs=0.05)


def test_muffle_and_phone_cut_the_highs(tmp_path):
    _require_ffmpeg()
    from test_audio_mute import _rms

    src = tmp_path / "s.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anoisesrc=d=3:c=white:a=0.2", "-ar", "48000",
                    str(src)], check=True)
    for fx in ("muffle", "phone"):
        edit = {"ops": [{"kind": "fx", "track": None, "start": 1.0, "end": 2.0, "fx": fx}], "src0": 0.0}
        out = tmp_path / f"{fx}.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-filter_complex",
                        source_graph(0, [0], edit, "a") + ";[a]highpass=f=6000[h]", "-map", "[h]", str(out)], check=True)
        assert _rms(out, 1.2, 0.6) < _rms(out, 0.2, 0.6) - 20, fx


# ------------------------------------------------------------------ перенос, инструменты, API


def test_audio_follows_trimmed_pieces():
    from app.piece_remap import remap_render

    old = [{"id": 1, "start_sec": 0, "end_sec": 10}, {"id": 2, "start_sec": 30, "end_sec": 40}]
    new = [{"id": 1, "start_sec": 2, "end_sec": 10}, {"id": 2, "start_sec": 30, "end_sec": 40}]
    audio = {"joins": [{"piece": 1, "lag": 0.3}], "music_keys": [{"t": 12, "gain_db": -6}],
             "music_dips": [{"at": 15, "before": 0.5}], "fx": [{"start": 12, "end": 13, "fx": "echo"}]}
    moved, _note = remap_render({"audio": audio}, old, new)
    assert moved["audio"]["joins"] == [{"piece": 1, "lag": 0.3}]
    assert moved["audio"]["music_keys"][0]["t"] == 10.0 and moved["audio"]["music_dips"][0]["at"] == 13.0
    assert moved["audio"]["fx"][0]["start"] == 10.0
    assert remap_audio({}, None) == {}


def test_audio_tools_round_trip(tmp_path, monkeypatch):
    store, _service, plan, _preset = _two_pieces(tmp_path, monkeypatch)
    ctx = _ctx(store, plan["id"])
    out, ok = run_tool(ctx, "set_audio_joins", {"joins": [{"piece": 0, "lag": 0.4}, {"piece": 0, "lead": 0.3}]})
    assert ok, out
    assert out["joins"] == [{"piece": 0, "lag": 0.4}] and any("первого" in w for w in out["warnings"])
    assert run_tool(ctx, "set_audio_joins", {"joins": [{"piece": 5, "lag": 0.4}]})[1] is False
    assert run_tool(ctx, "set_audio_joins", {"joins": [{"piece": 1, "lead": 3}]})[1] is False
    out, ok = run_tool(ctx, "set_audio_keys", {"target": "music", "keys": [{"t": 1, "gain_db": -6, "ease": "smooth"}]})
    assert ok and out["keys"] == [{"t": 1.0, "gain_db": -6.0, "ease": "smooth"}] and out["warnings"]
    assert run_tool(ctx, "set_audio_keys", {"target": "x", "keys": []})[1] is False
    out, ok = run_tool(ctx, "set_music_duck", {"dips": [{"at_word": {"text": "два"}, "before": 0.6}],
                                               "duck": [{"start": 2, "end": 4, "gain_db": -10}]})
    assert ok, out
    assert out["dips"] == [{"at": 11.5, "before": 0.6}]  # «два» — 11.5 на шкале clip (наплыв 0.5)
    out, ok = run_tool(ctx, "set_audio_fx", {"fx": [{"start": 1, "end": 2, "fx": "radio", "track": 1}]})
    assert ok and out["fx"][0]["fx"] == "radio" and out["warnings"]  # дорожки 1 нет в звуке ролика
    assert run_tool(ctx, "set_audio_fx", {"fx": [{"start": 1, "end": 2, "fx": "wah"}]})[1] is False
    clip, ok = run_tool(ctx, "get_clip", {})
    audio = clip["render"]["audio"]
    assert set(audio) == {"joins", "music_keys", "music_duck", "music_dips", "fx"}
    out, ok = run_tool(ctx, "mute_audio", {"ranges": [{"start": 1, "end": 2, "track": 1}]})
    assert ok and out["mute"] == [{"start": 1.0, "end": 2.0, "track": 1}] and out["warnings"]


def test_editor_autosave_without_audio_keeps_it(tmp_path, monkeypatch):
    from test_api_pipeline import _client

    from app.settings import settings

    client, store = _client(tmp_path, monkeypatch)
    path = settings.source_dir / "s.mp4"
    path.write_bytes(b"src")
    source = store.create_source("upload", path, duration_sec=60, width=1280, height=720, status="ready")
    analysis = store.create_ai_analysis(source["id"], "mock", status="succeeded")
    seg = store.create_ai_segment(analysis["id"], {"start_sec": 0, "end_sec": 10, "title": "P"})
    plan = store.create_clip_plan(source["id"], analysis["id"], "План", segment_ids=[seg["id"]])
    run_tool(_ctx(store, plan["id"]), "set_audio_fx", {"fx": [{"start": 1, "end": 2, "fx": "echo"}]})
    resp = client.patch(f"/api/clip-plans/{plan['id']}/render-settings", json={"settings": {"mirror": True}})
    assert resp.status_code == 200, resp.text
    assert store.get_clip_plan(plan["id"])["render_settings"]["audio"]["fx"][0]["fx"] == "echo"
    resp = client.patch(f"/api/clip-plans/{plan['id']}/render-settings", json={"settings": {"audio": {}}})
    assert store.get_clip_plan(plan["id"])["render_settings"]["audio"] == {}
