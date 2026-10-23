"""Лимитер по true peak: межвыборочные пики не пробивают потолок (app/render.py).

QC показал +1.3 dBTP при потолке −1.5: alimiter смотрит на сэмплы, а пик между ними
(и рост после пересэмплирования и AAC) пропускал. Теперь лимитер на ×4 частоте с
потолком −2 dBTP, а готовый файл проверяется замером и при перебое пересобирается.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from app.settings import settings


def _loud(i, tp="-3.0"):
    return {"input_i": i, "input_tp": tp, "input_lra": "0.0", "input_thresh": "-70.0", "target_offset": "0.0"}


def test_limiter_runs_oversampled_with_codec_headroom():
    from app.render import LOUDNESS_LIMITER, LOUDNESS_PEAK_DB, TRUE_PEAK_LIMIT_DB, true_peak_limiter

    assert LOUDNESS_PEAK_DB == -1.5 and -2.0 <= TRUE_PEAK_LIMIT_DB < LOUDNESS_PEAK_DB
    up, limiter, down = LOUDNESS_LIMITER.split(",")
    assert up == "aresample=192000" and down == "aresample=48000"  # ×4, как измеритель BS.1770
    assert limiter == "alimiter=limit=0.7943:attack=5:release=80:level=disabled"  # −2 dB
    assert "limit=0.5623" in true_peak_limiter(-5.0)
    assert "limit=0.5012" in true_peak_limiter(-9.0)  # не ниже пола −6 dB
    assert "limit=1.0000" in true_peak_limiter(+3.0)  # и не выше 0 dB


def test_loudness_filters_use_plan_ceiling():
    from app.render import LOUDNESS_LIMITER, loudness_filters, true_peak_limiter

    plan = {"gain_db": 2.0, "compressor": None, "makeup_db": 0.0}
    assert loudness_filters(plan) == ["volume=2.00dB", LOUDNESS_LIMITER]
    assert loudness_filters({**plan, "ceiling_db": -4.1})[-1] == true_peak_limiter(-4.1)


def test_retry_ceiling_from_measured_true_peak():
    from app.render import true_peak_retry_ceiling

    assert true_peak_retry_ceiling("-1.6") is None
    assert true_peak_retry_ceiling(-1.52) is None  # в допуске
    assert true_peak_retry_ceiling("-inf") is None and true_peak_retry_ceiling(None) is None
    # +1.3 dBTP из QC: перебор 2.8 дБ → потолок ниже на него и ещё на 0.3.
    assert true_peak_retry_ceiling("1.3") == -5.1
    assert true_peak_retry_ceiling("-1.0", -2.0) == -2.8
    assert true_peak_retry_ceiling("6.0") == -6.0  # пол
    assert true_peak_retry_ceiling("6.0", -6.0) is None  # ниже пола не идём


def test_peak_between_ceiling_and_minus_one_now_needs_pass():
    from app.render import loudnorm_needed

    assert loudnorm_needed(_loud("-14.2", "-1.2"), -14.0) is True  # раньше пропускалось (≤ −1.0)
    assert loudnorm_needed(_loud("-14.2", "-1.6"), -14.0) is False


def _store(tmp_path, monkeypatch):
    from test_render import _store as base

    return base(tmp_path, monkeypatch)


def _clip(store, tmp_path):
    src = settings.source_dir / "s.mp4"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"src")
    source = store.create_source("upload", src, duration_sec=5, width=1080, height=1920, status="ready")
    path = settings.clip_dir / "loud.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"video")
    clip = store.create_clip(source["id"])
    return store.finish_clip_render(clip["id"], status="succeeded", output_path=path, preview_path=path)


class _Meta:
    duration_sec = 10.0
    width, height = 1080, 1920
    raw = {"streams": [{"codec_type": "video", "duration": "10.0"}, {"codec_type": "audio", "duration": "10.0"}]}


def test_normalize_reencodes_with_lower_ceiling_when_file_overshoots(tmp_path, monkeypatch):
    """Готовый файл пробил потолок (+1.3 dBTP после AAC) — второй проход с лимитером ниже."""
    from app.render import ClipRenderService, true_peak_limiter

    store = _store(tmp_path, monkeypatch)
    clip = _clip(store, tmp_path)
    monkeypatch.setattr(settings, "render_loudnorm", True)
    monkeypatch.setattr(settings, "render_end_hold_sec", 0.0)
    monkeypatch.setattr("app.render._has_audio_stream", lambda p: True)
    monkeypatch.setattr("app.render.probe_media", lambda p: _Meta())
    monkeypatch.setattr(ClipRenderService, "_attach_qc", lambda self, c, p, m: c)
    # Замеры: исходник (−14 LUFS, пик −3 — громкость в норме), готовый файл: +1.3, потом −1.7.
    answers = iter([_loud("-14.1", "-3.0"), _loud("-14.0", "1.3"), _loud("-14.0", "-1.7")])
    monkeypatch.setattr("app.render._measure_loudness", lambda path, target, filters=None: next(answers))
    chains = []

    def fake_ffmpeg(args, timeout):
        chains.append(args[args.index("-af") + 1])
        (settings.clip_dir / "loud.loud.mp4").write_bytes(b"out")

    monkeypatch.setattr("app.render._run_ffmpeg", fake_ffmpeg)
    out = ClipRenderService(store)._normalize_loudness(clip)
    assert not (out.get("error") or "")
    assert len(chains) == 2
    assert "alimiter" not in chains[0]  # громкость в норме — только затухание
    assert "volume=0.00dB," + true_peak_limiter(-5.1) in chains[1]


def _require_ffmpeg():
    if not shutil.which("ffmpeg"):
        pytest.skip("нет ffmpeg")


def _true_peak(path) -> float:
    from app.render import _measure_loudness

    return float(_measure_loudness(path, -14.0)["input_tp"])


def test_real_transient_true_peak_stays_under_ceiling(tmp_path):
    """Синтетика: тон 11025 Гц с фазой 45° на 44.1 кГц (сэмплы на 3 дБ ниже настоящего
    пика) и резкие щелчки, громкость +3 дБ. Старый лимитер по сэмплам → AAC даёт пик выше
    нуля, новый — не выше −1.5 dBTP."""
    _require_ffmpeg()
    from app.render import LOUDNESS_LIMITER, LOUDNESS_PEAK_DB

    src = tmp_path / "src.wav"
    expr = ("0.9*sin(2*PI*11025*t+PI/4)*lt(mod(t,1),0.5)"
            "+if(lt(mod(t,0.25),0.0008),0.98*(1-mod(t,0.25)/0.0008),0)")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"aevalsrc='{expr}':s=44100:d=3:c=stereo",
                    str(src)], check=True)
    peaks = {}
    for name, limiter in (("old", "alimiter=limit=0.84:attack=5:release=80:level=disabled"),
                          ("new", LOUDNESS_LIMITER)):
        out = tmp_path / f"{name}.m4a"
        chain = f"volume=3dB,{limiter},aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-af", chain, "-c:a", "aac", "-b:a", "192k",
                        str(out)], check=True)
        peaks[name] = _true_peak(out)
    assert peaks["old"] > 0.0, json.dumps(peaks)  # та самая беда: пик за потолком
    assert peaks["new"] <= LOUDNESS_PEAK_DB + 0.05, json.dumps(peaks)
