"""Громкость промо под музыку (клип 575: −16.2 LUFS при пике −7.2 dBTP, цель −14).

Причина: готовый файл проверялся только на перебор пика. AAC 192k на плотной музыке добавлял к
пику +2.3 дБ сверх лимитера, повтор опускал потолок лимитера (до −5 dBTP) и терял громкость, а
недобор при запасе по пикам уже никто не возвращал. Плюс проход звуков слоёв сводил стерео
музыки в моно (звуки пака моно). Теперь: финальный AAC 256k (перебор ~0.2 дБ), готовый файл
тише цели при запасе по пикам — подъём после лимитера, звуки слоёв — в стерео.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from app.settings import settings
from test_true_peak import _Meta, _clip, _loud, _store


def test_finished_lift_fills_shortfall_within_peak_room():
    from app.render import finished_loudness_lift

    plan = {"gain_db": 12.3, "compressor": None, "makeup_db": 0.0, "aim_lufs": -14.0}
    assert finished_loudness_lift(_loud("-16.2", "-7.2"), plan, -14.0) == 2.2  # клип 575
    assert finished_loudness_lift(_loud("-16.2", "-2.5"), plan, -14.0) == 0.7  # до потолка −1.5 − 0.3
    assert finished_loudness_lift(_loud("-14.3", "-7.2"), plan, -14.0) == 0.0  # в допуске
    assert finished_loudness_lift(_loud("-16.2", "-1.6"), plan, -14.0) == 0.0  # запаса нет
    assert finished_loudness_lift(_loud("-16.2", "-7.2"), None, -14.0) == 0.0  # громкость была в норме
    # Тихая шумная запись: выше её потолка усиления (aim_lufs) не тянем.
    assert finished_loudness_lift(_loud("-20.0", "-12.0"), {**plan, "aim_lufs": -19.0}, -14.0) == 1.0
    assert finished_loudness_lift(_loud("-inf", "-inf"), plan, -14.0) == 0.0


def test_lift_goes_after_limiter():
    from app.render import LOUDNESS_LIMITER, loudness_filters

    chain = loudness_filters({"gain_db": 2.0, "compressor": None, "makeup_db": 0.0, "post_gain_db": 2.2})
    assert chain == ["volume=2.00dB", LOUDNESS_LIMITER, "volume=2.20dB"]


def test_normalize_restores_loudness_lost_on_lowered_ceiling(tmp_path, monkeypatch):
    """Как у клипа 575: первый проход пробил потолок после AAC → лимитер ниже → −16.2 LUFS
    при −7.2 dBTP → третий проход с подъёмом 2.2 дБ после лимитера."""
    from app.render import FINAL_AUDIO_BITRATE, ClipRenderService, true_peak_limiter

    store = _store(tmp_path, monkeypatch)
    clip = _clip(store, tmp_path)
    monkeypatch.setattr(settings, "render_loudnorm", True)
    monkeypatch.setattr(settings, "render_end_hold_sec", 0.0)
    monkeypatch.setattr("app.render._has_audio_stream", lambda p: True)
    monkeypatch.setattr("app.render.probe_media", lambda p: _Meta())
    monkeypatch.setattr(ClipRenderService, "_attach_qc", lambda self, c, p, m: c)
    plan = {"gain_db": 12.3, "compressor": None, "makeup_db": 0.0, "overshoot_db": 0.0, "aim_lufs": -14.0}
    monkeypatch.setattr("app.render.fit_loudness", lambda m, t, f: dict(plan))
    answers = iter([_loud("-26.9", "-14.0"), _loud("-14.0", "0.6"), _loud("-16.2", "-7.2"), _loud("-14.0", "-5.0")])
    monkeypatch.setattr("app.render._measure_loudness", lambda path, target, filters=None: next(answers))
    runs = []

    def fake_ffmpeg(args, timeout):
        runs.append(args)
        (settings.clip_dir / "loud.loud.mp4").write_bytes(b"out")

    monkeypatch.setattr("app.render._run_ffmpeg", fake_ffmpeg)
    out = ClipRenderService(store)._normalize_loudness(clip)
    assert not (out.get("error") or "")
    chains = [a[a.index("-af") + 1] for a in runs]
    assert len(chains) == 3
    assert true_peak_limiter(-4.4) in chains[1] and "volume=2.20dB" not in chains[1]
    assert true_peak_limiter(-4.4) + ",volume=2.20dB" in chains[2]  # подъём — после лимитера
    assert all(a[a.index("-b:a") + 1] == FINAL_AUDIO_BITRATE == "256k" for a in runs)


def _require_ffmpeg():
    if not shutil.which("ffmpeg"):
        pytest.skip("нет ffmpeg")


def test_real_quiet_intro_then_loud_part_reaches_target(tmp_path, monkeypatch):
    """Синтетика «тихое вступление 10 с + громкая часть» (тон + бочка + шум) через настоящий
    _normalize_loudness: −14 ± 0.7 LUFS, пик не выше −1.5 dBTP, вступление остаётся тише."""
    _require_ffmpeg()
    from app.render import LOUDNESS_PEAK_DB, ClipRenderService, _measure_loudness

    store = _store(tmp_path, monkeypatch)
    clip = _clip(store, tmp_path)
    path = settings.clip_dir / "loud.mp4"
    expr = ("0.3*(if(lt(t,10),0.03*sin(2*PI*330*t)+0.01*(random(0)-0.5),"
            "0.35*sin(2*PI*55*t)*exp(-12*mod(t,0.5))+0.08*sin(2*PI*440*t)+0.06*(random(1)-0.5)))")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x112:r=30:d=24",
                    "-f", "lavfi", "-i", f"aevalsrc='{expr}':s=48000:d=24:c=stereo", "-shortest",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-b:a", "192k", str(path)], check=True)
    monkeypatch.setattr(settings, "render_loudnorm", True)
    monkeypatch.setattr(settings, "render_end_hold_sec", 0.0)
    monkeypatch.setattr(ClipRenderService, "_attach_qc", lambda self, c, p, m: c)
    before = _measure_loudness(path, -14.0)
    assert float(before["input_i"]) < -28  # тихий клип: музыка без голоса
    out = ClipRenderService(store)._normalize_loudness(clip)
    assert not (out.get("error") or "")
    after = _measure_loudness(path, -14.0)
    assert abs(float(after["input_i"]) + 14.0) <= 0.7, after
    assert float(after["input_tp"]) <= LOUDNESS_PEAK_DB + 0.05, after
    intro = tmp_path / "intro.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-t", "9", str(intro)], check=True)
    assert float(_measure_loudness(intro, -14.0)["input_i"]) < float(after["input_i"]) - 6  # без «качелей»


def test_sound_layers_keep_music_stereo(tmp_path):
    """Звуки пака моно: amix сводил музыку клипа в моно (клип 575 вышел двойным моно)."""
    _require_ffmpeg()
    from app.motion import normalize_layers
    from app.motion_render import build_audio_layers_args

    clip = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x112:r=30:d=3",
                    "-f", "lavfi", "-i", "aevalsrc='0.2*sin(2*PI*220*t)|0.2*sin(2*PI*330*t)':s=48000:d=3",
                    "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(clip)], check=True)
    sfx = tmp_path / "pop.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "aevalsrc='0.5*sin(2*PI*900*t)':s=44100:d=0.3",
                    "-ac", "1", str(sfx)], check=True)
    layers = normalize_layers([{"type": "audio", "start": 1.0, "end": 1.3, "props": {"asset_id": 5, "volume": 0.5}}])
    out = tmp_path / "out.mp4"
    args = build_audio_layers_args(clip, out, layers, {"5": str(sfx)}, 3.0)
    assert "aformat=channel_layouts=stereo" in args[args.index("-filter_complex") + 1]
    subprocess.run(args, check=True)
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=channels",
                            "-of", "csv=p=0", str(out)], capture_output=True, text=True, check=True)
    assert probe.stdout.strip() == "2"
