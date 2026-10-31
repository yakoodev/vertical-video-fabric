"""Стили глушения мата (``style`` у отрезков ``by: censor``): чем заполнена середина слова.

Хранение и совместимость (отрезки без стиля — тишина), графы ffmpeg каждого стиля (громкость
финального прохода или эффект звукового монтажа), настоящий звук через ffmpeg, повторный запуск
цензуры не теряет стиль, рендер кладёт эффект в звук куска, listen слышит то же.
"""

from __future__ import annotations

import subprocess
import wave

import pytest

from app import censor
from app.agent_tools import run_tool
from app.audio_edit import (
    BEEP_HZ,
    CENSOR_FX,
    CENSOR_RAMP_SEC,
    censor_fx,
    fx_graph,
    normalize_fx,
    source_graph,
    source_ops,
    wants_render_plan,
)
from app.audio_mute import (
    DUCK_DB,
    MUTE_STYLES,
    VOLUME_STYLES,
    final_pass_mutes,
    is_fx_style,
    mute_volume_expr,
    normalize_mute,
    span_floor,
    span_style,
)
from app.timebase import ClipClock
from test_api_pipeline import _require_ffmpeg
from test_audio_edit import _expr_value
from test_audio_mute import _rms
from test_censor import _ctx, _plan
from test_cuts import _mock_render

FX_STYLES = [s for s in MUTE_STYLES if s not in VOLUME_STYLES]


# ------------------------------------------------------------------ хранение, совместимость


def test_styles_are_complete_and_split_between_volume_and_fx():
    assert list(MUTE_STYLES) == ["silence", "beep", "noise", "reverse", "lowpass", "stretch", "duck"]
    assert set(VOLUME_STYLES) == {"silence", "duck"}
    assert set(CENSOR_FX) == set(FX_STYLES)
    for style in FX_STYLES:
        assert normalize_fx([{"start": 0, "end": 1, **censor_fx(style)}]), style  # эффект — из set_audio_fx
    assert censor_fx("silence") is None and censor_fx("duck") is None
    assert span_floor({"style": "duck"}) == pytest.approx(10 ** (DUCK_DB / 20), abs=1e-4)


def test_style_kept_only_on_tagged_spans_and_old_spans_are_silence():
    raw = [{"start": 1, "end": 2, "by": "censor", "level": 2, "style": "beep"},
           {"start": 3, "end": 4, "by": "censor", "level": 1, "style": "boing"},
           {"start": 5, "end": 6, "by": "censor", "level": 1},
           {"start": 7, "end": 8, "style": "beep"}]
    out = normalize_mute(raw)
    assert out == [{"start": 1.0, "end": 2.0, "by": "censor", "level": 2, "style": "beep"},
                   {"start": 3.0, "end": 4.0, "by": "censor", "level": 1},
                   {"start": 5.0, "end": 6.0, "by": "censor", "level": 1},
                   {"start": 7.0, "end": 8.0}]
    assert [span_style(m) for m in out] == ["beep", "silence", "silence", "silence"]
    assert [is_fx_style(m) for m in out] == [True, False, False, False]
    # Старый клип без стилей: рендер ровно прежний — без звукового монтажа, всё в финальном проходе.
    old = [{"start": 5, "end": 6, "by": "censor", "level": 1}]
    assert not wants_render_plan({"mute": old})
    assert final_pass_mutes(old) == normalize_mute(old)


def test_final_pass_skips_fx_styles_unless_they_were_not_rendered():
    mute = [{"start": 1, "end": 2, "by": "censor", "style": "beep"},
            {"start": 3, "end": 4, "by": "censor", "style": "duck"},
            {"start": 5, "end": 6, "by": "censor", "style": "noise", "track": 1},
            {"start": 7, "end": 8}]
    assert wants_render_plan({"mute": mute[:1]})
    assert [m["start"] for m in final_pass_mutes(mute)] == [3.0, 7.0]
    # Холст: звукового монтажа нет — писк превращается в тишину, мат всё равно не звучит.
    fallback = final_pass_mutes(mute, fx_rendered=False)
    assert [(m["start"], span_style(m)) for m in fallback] == [(1.0, "silence"), (3.0, "duck"), (7.0, "silence")]


# ------------------------------------------------------------------ графы ffmpeg каждого стиля


def test_volume_styles_expression():
    expr = mute_volume_expr([(1.0, 2.0), (3.0, 4.0, VOLUME_STYLES["duck"])])
    assert _expr_value(expr, 1.5) == pytest.approx(0.0)
    assert _expr_value(expr, 3.5) == pytest.approx(10 ** (DUCK_DB / 20), abs=1e-4)
    assert _expr_value(expr, 2.5) == pytest.approx(1.0) and _expr_value(expr, 5.0) == pytest.approx(1.0)
    # Старые вызовы парами (a, b) — тишина, как раньше.
    assert mute_volume_expr([(1.0, 2.0)]) == "max(clip((1.000-t)/0.03,0,1),clip((t-2.000)/0.03,0,1))"


@pytest.mark.parametrize("style, marker", [
    ("beep", f"sine=frequency={BEEP_HZ}:sample_rate=48000:duration=0.300"),
    ("noise", "anoisesrc=color=pink"),
    ("reverse", "areverse"),
    ("lowpass", "lowpass=f=400:p=2"),
    ("stretch", "asetrate=28800"),
])
def test_fx_style_graph(style, marker):
    clock = ClipClock([{"start_sec": 10.0, "end_sec": 20.0}], None)
    render = {"mute": [{"start": 2.0, "end": 2.3, "by": "censor", "level": 1, "style": style}]}
    ops, notes = source_ops(render, clock, [0])
    assert not notes
    assert ops == [{**CENSOR_FX[style], "kind": "fx", "start": 12.0, "end": 12.3, "track": None,
                    "ramp": CENSOR_RAMP_SEC, "by": "censor"}]  # секунды исходника
    graph = source_graph(0, [0], {"ops": ops, "src0": 10.0}, "a")
    assert marker in graph
    assert f"/{CENSOR_RAMP_SEC:.3f}" in graph  # короткие края, слово не просачивается
    if style in ("beep", "noise"):
        assert "adelay=2000:all=1" in graph  # тон встаёт на место середины в потоке


def test_fx_style_on_voice_track_and_duck_track_volume():
    clock = ClipClock([{"start_sec": 0.0, "end_sec": 10.0}], None)
    render = {"mute": [{"start": 1, "end": 1.3, "by": "censor", "style": "beep", "track": 1},
                       {"start": 2, "end": 2.3, "by": "censor", "style": "duck", "track": 1},
                       {"start": 3, "end": 3.3, "by": "censor", "track": 1}]}
    ops, _notes = source_ops(render, clock, [0, 1])
    assert [(o["kind"], o["track"], o.get("fx"), o.get("factor")) for o in ops] == [
        ("fx", 1, "beep", None), ("vol", 1, None, VOLUME_STYLES["duck"]), ("vol", 1, None, 0.0)]
    graph = source_graph(0, [0, 1], {"ops": ops, "src0": 0.0}, "a")
    assert "[0:a:1]" in graph and "sine=frequency" in graph and "[0:a:0]anull" in graph


def test_gen_fx_outside_the_stream_passes_through():
    graph = fx_graph("x", [{"fx": "beep", "start": 1.0, "end": 2.0}], "(t+5.000)", 5.0, "y", "u")
    assert graph == ["[x]anull[y]"]  # отрезок до начала куска — нечего заменять


def _tone(path, freq=300, dur=3.0):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=f={freq}:d={dur}:sample_rate=48000",
                    "-af", "volume=6dB", "-ac", "2", "-c:a", "pcm_s16le", str(path)], check=True)


def _apply(src, out, graph):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-filter_complex", graph, "-map", "[a]",
                    "-c:a", "pcm_s16le", str(out)], check=True)


@pytest.mark.parametrize("style", list(MUTE_STYLES))
def test_style_really_sounds(tmp_path, style):
    """Звук каждого стиля на тоне 300 Гц: середина 1.0–2.0 с, вне её — как было, длина та же."""
    _require_ffmpeg()
    src = tmp_path / "tone.wav"
    _tone(src)
    out = tmp_path / f"{style}.wav"
    if style in VOLUME_STYLES:
        expr = mute_volume_expr([(1.0, 2.0, VOLUME_STYLES[style])])
        _apply(src, out, f"[0:a]volume='{expr}':eval=frame[a]")
    else:
        ops = [{**censor_fx(style), "kind": "fx", "start": 1.0, "end": 2.0, "track": None,
                "ramp": CENSOR_RAMP_SEC, "by": "censor"}]
        _apply(src, out, source_graph(0, [0], {"ops": ops, "src0": 0.0}, "a"))
    with wave.open(str(out), "rb") as w:
        assert abs(w.getnframes() / w.getframerate() - 3.0) < 0.05  # длина ролика не меняется
    before, inside, after = _rms(out, 0.2, 0.6), _rms(out, 1.1, 0.8), _rms(out, 2.2, 0.6)
    ref = _rms(src, 0.2, 0.6)
    assert abs(before - ref) < 1.0 and abs(after - ref) < 1.0  # вне середины — исходный звук
    if style == "silence":
        assert inside < -80
    elif style == "duck":
        assert inside - ref == pytest.approx(DUCK_DB, abs=1.5)  # приглушено, не в ноль
    elif style == "beep":
        assert -22 < inside < -20  # синус −18 dBFS пиком ≈ −21 dBFS RMS
        hp = tmp_path / "hp.wav"
        _apply(out, hp, "[0:a]highpass=f=700,highpass=f=700,highpass=f=700[a]")
        assert _rms(hp, 1.1, 0.8) > _rms(hp, 0.2, 0.6) + 25  # на месте — тон 900 Гц, а не речь
    elif style == "noise":
        assert -45 < inside < -20  # тихий сплошной шум, не тишина
    elif style == "lowpass":
        hi = tmp_path / "hi.wav"
        _tone(hi, freq=3000)
        _apply(hi, out, source_graph(0, [0], {"ops": [{**censor_fx(style), "kind": "fx", "start": 1.0, "end": 2.0,
                                                       "track": None, "ramp": CENSOR_RAMP_SEC}], "src0": 0.0}, "a"))
        assert _rms(out, 1.1, 0.8) < _rms(out, 0.2, 0.6) - 30  # 3 кГц «под водой» не слышны
    else:  # reverse, stretch — тот же тон, громкость на месте
        assert abs(inside - ref) < 4


# ------------------------------------------------------------------ цензура: стиль сохраняется


def _styles(store, plan_id):
    return [(m["start"], m.get("style")) for m in store.get_clip_plan(plan_id)["render_settings"]["mute"]
            if m.get("by") == "censor"]


def test_censor_style_saved_and_kept_on_rerun(tmp_path, monkeypatch):
    store, _s, plan, _p = _plan(tmp_path, monkeypatch)
    out = censor.censor_plan(store, plan["id"])  # новый клип — стиль по умолчанию
    assert out["style"] == censor.DEFAULT_STYLE
    assert [s for _a, s in _styles(store, plan["id"])] == [censor.DEFAULT_STYLE] * 2
    out = censor.censor_plan(store, plan["id"], style="beep")
    assert out["style"] == "beep" and all(w["style"] == "beep" for w in out["words"])
    assert "писк" in out["note"]
    censor.censor_plan(store, plan["id"], 2)  # повтор без style (уровни поменялись) — стиль тот же
    assert [s for _a, s in _styles(store, plan["id"])] == ["beep", "beep"]
    censor.censor_plan(store, plan["id"], {"4": 3}, style="duck")
    assert [s for _a, s in _styles(store, plan["id"])] == ["duck", "duck"]
    with pytest.raises(ValueError, match="style"):
        censor.censor_plan(store, plan["id"], style="whistle")


def test_censor_rerun_on_old_spans_without_style_stays_silence(tmp_path, monkeypatch):
    old = [{"start": 1.42, "end": 1.64, "by": "censor", "level": 2}, {"start": 8.0, "end": 9.0}]
    store, _s, plan, _p = _plan(tmp_path, monkeypatch, mute=old)
    monkeypatch.setattr(censor, "DEFAULT_STYLE", "beep")  # даже если умолчание сменится
    out = censor.censor_plan(store, plan["id"])
    rows = {w["i"]: (w["level"], w["style"]) for w in out["words"]}
    assert rows[1] == (2, "silence")  # уровень и звучание старого отрезка — как были
    assert rows[4] == (1, "silence")  # новое слово клипа — как остальные его слова
    assert {"start": 8.0, "end": 9.0} in store.get_clip_plan(plan["id"])["render_settings"]["mute"]


def test_tool_and_http_take_style(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import app.main as app_main
    from app.settings import settings

    store, _s, plan, _p = _plan(tmp_path, monkeypatch)
    out, ok = run_tool(_ctx(store, plan["id"]), "censor_profanity", {"verify": False, "style": "noise"})
    assert ok, out
    assert [s for _a, s in _styles(store, plan["id"])] == ["noise", "noise"]
    out, ok = run_tool(_ctx(store, plan["id"]), "censor_profanity", {"verify": False, "style": "boing"})
    assert not ok and "style" in str(out)
    monkeypatch.setattr(settings, "auth_enabled", False)
    monkeypatch.setattr(app_main, "store", store)
    client = TestClient(app_main.app)
    resp = client.post(f"/api/clip-plans/{plan['id']}/censor", json={"verify": False, "style": "reverse"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["style"] == "reverse"
    assert client.post(f"/api/clip-plans/{plan['id']}/censor",
                       json={"verify": False, "style": "x"}).status_code == 422


def test_verify_keeps_style_across_rounds_and_suggests_stronger(tmp_path, monkeypatch):
    from test_censor import _mock_rounds

    store, _s, plan, _p = _plan(tmp_path, monkeypatch)
    seen = []

    def hearing(n, mute):
        seen.append({m.get("style") for m in mute if m.get("by") == "censor"})
        return [{"word": "блять", "start": 1.35, "end": 1.65}]  # узнаётся всегда

    _mock_rounds(monkeypatch, store, plan, hearing)
    out = censor.verify_plan(store, plan["id"], style="duck")
    assert seen == [{"duck"}] * 3  # уровень растёт, стиль тот же
    word = next(w for w in out["words"] if w["i"] == 1)
    assert word["level"] == 3 and word["heard"] and "beep" in word["note"]


# ------------------------------------------------------------------ рендер и listen


def test_render_puts_fx_style_into_piece_audio(tmp_path, monkeypatch):
    store, service, plan, preset, calls = _mock_render(tmp_path, monkeypatch, [(0, 5), (10, 16)])
    store.set_clip_plan_render_settings(plan["id"], {"mute": [
        {"start": 6.0, "end": 6.3, "by": "censor", "level": 1, "style": "beep"},
        {"start": 1.0, "end": 1.3, "by": "censor", "level": 1, "style": "duck"}]})
    service.render_clip_plan(plan["id"], ffmpeg_preset_id=preset["id"])
    graphs = [c[c.index("-filter_complex") + 1] for c in calls if "-filter_complex" in c]
    assert any("sine=frequency=900" in g and "(t+10.000)-11.000" in g for g in graphs)  # второй кусок
    final = [c for c in calls if "-af" in c and "volume='" in c[c.index("-af") + 1]]
    assert len(final) == 1 and "0.05623+" in final[0][final[0].index("-af") + 1]  # duck — финальным проходом
    assert "11.000" not in final[0][final[0].index("-af") + 1]  # писк туда не попал


def test_listen_hears_styles_like_render(tmp_path):
    from app.clip_subtitles import mute_in_parts, silence_wav

    clock = ClipClock([{"start_sec": 0, "end_sec": 10}], None)
    mute = [{"start": 1.0, "end": 1.5, "by": "censor", "style": "beep"},
            {"start": 2.0, "end": 2.5, "by": "censor", "style": "duck"},
            {"start": 3.0, "end": 3.5, "by": "censor"}]
    spans = mute_in_parts(clock, mute, 0.0, 10.0)
    assert spans == [(2.0, 2.5, VOLUME_STYLES["duck"]), (3.0, 3.5)]  # писк — в самом звуке (ops)

    path = tmp_path / "a.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(1000)
        w.writeframes(b"\x00\x10" * 4000)
    silence_wav(path, spans)
    with wave.open(str(path), "rb") as r:
        data = r.readframes(r.getnframes())
    assert data[2100 * 2:2102 * 2] == int(4096 * VOLUME_STYLES["duck"]).to_bytes(2, "little") * 2
    assert data[3100 * 2:3102 * 2] == bytes(4) and data[:4] == b"\x00\x10" * 2
