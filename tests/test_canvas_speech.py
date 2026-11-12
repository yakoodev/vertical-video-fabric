"""Распознавание слышит озвучку холста (эксперимент 12.11): звук холста — слои audio, основа молчит. generate_subtitles,
listen, audio_map(clip_id) и сверка субтитров lint_clip работают по сведённым слоям (app/canvas_audio.py)."""

from __future__ import annotations

import math
import subprocess

import pytest

from app.agent_tools import ToolContext, run_tool
from app.subtitles.contracts import SubtitleResult, SubtitleSegment, SubtitleWord
from test_api_pipeline import _require_ffmpeg
from test_data_foundation import _store

VOICE_AT = (3.0, 5.0)  # где на холсте звучит «озвучка»


class _EarProvider:
    """Распознавание-заглушка: «слышит» слово в каждые полсекунды, где звук громче тишины."""

    calls: list[str] = []

    def transcribe(self, path, profile, model):
        from app.speech_onsets import wav_pcm

        pcm = wav_pcm(path)
        rate = 16000
        samples = [int.from_bytes(pcm[i:i + 2], "little", signed=True) for i in range(0, len(pcm) - 1, 2)]
        words = []
        step = rate // 2
        for n in range(0, len(samples) // step):
            chunk = samples[n * step:(n + 1) * step]
            rms = math.sqrt(sum(s * s for s in chunk) / max(1, len(chunk)))
            if rms > 300:
                words.append(SubtitleWord(word=f"слово{n}", start=n * 0.5 + 0.02, end=n * 0.5 + 0.45))
        _EarProvider.calls.append(str(path))
        if not words:
            raise RuntimeError("Whisper returned no word-level timestamps")
        text = " ".join(w.word for w in words)
        return SubtitleResult(text=text, language="ru", duration=len(samples) / rate,
                              segments=[SubtitleSegment(words[0].start, words[-1].end, text)], words=words)


@pytest.fixture
def canvas(tmp_path, monkeypatch):
    _require_ffmpeg()
    import app.canvas as canvas_mod
    import app.subtitles.registry as registry

    monkeypatch.setattr(canvas_mod, "CANVAS_WIDTH", 108)
    monkeypatch.setattr(canvas_mod, "CANVAS_HEIGHT", 192)
    store = _store(tmp_path, monkeypatch)
    store.create_subtitle_profile("Субтитры", provider="whisper")
    real = registry.get_subtitle_provider
    monkeypatch.setattr(registry, "get_subtitle_provider",
                        lambda name=None: _EarProvider() if str(name or "").lower() == "whisper" else real(name))
    for module in ("app.clip_subtitles", "app.speech_heard"):
        import importlib

        mod = importlib.import_module(module)
        if hasattr(mod, "get_subtitle_provider"):
            monkeypatch.setattr(mod, "get_subtitle_provider", registry.get_subtitle_provider)
    tone = tmp_path / "voice.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=330:duration=2:sample_rate=48000",
                    "-af", "volume=0.6", str(tone)], check=True, capture_output=True)
    asset = store.create_montage_asset(kind="audio", label="озвучка", file_path=str(tone), duration_sec=2.0,
                                       has_audio=True, size_bytes=tone.stat().st_size)
    out, ok = run_tool(ToolContext(store=store), "create_canvas_clip", {"duration": 8, "title": "Озвучка"})
    assert ok, out
    ctx = ToolContext(store=store, clip_plan_id=out["clip_id"])
    added, ok = run_tool(ctx, "add_layers", {"layers": [{
        "id": "voice1", "type": "audio", "start": VOICE_AT[0], "end": VOICE_AT[1],
        "props": {"asset_id": str(asset["id"]), "volume": 1.0}}]})
    assert ok, added
    _EarProvider.calls.clear()
    return store, out["clip_id"]


def _heard_times(words):
    return [round(float(w["start"]), 2) for w in words]


def test_generate_subtitles_hears_the_voice_layer(canvas):
    store, clip_id = canvas
    out, ok = run_tool(ToolContext(store=store, clip_plan_id=clip_id), "generate_subtitles", {}, validate=True)
    assert ok, out
    assert out["words"] >= 3
    words = (store.get_clip_plan_subtitles(clip_id) or {})["words"]
    # Слова — там, где звучит слой (3–5 с), а не где-то ещё.
    assert all(VOICE_AT[0] - 0.6 <= float(w["start"]) <= VOICE_AT[1] + 0.1 for w in words), _heard_times(words)


def test_listen_and_audio_map_hear_the_canvas(canvas):
    store, clip_id = canvas
    heard, ok = run_tool(ToolContext(store=store, clip_plan_id=clip_id), "listen", {"from_sec": 2, "to_sec": 7},
                         validate=True)
    assert ok, heard
    assert heard["words"] and "речи нет" not in heard["note"]
    assert all(VOICE_AT[0] - 0.6 <= w[1] <= VOICE_AT[1] + 0.1 for w in heard["words"]), heard["words"]
    assert heard["level"]

    # audio_map — глобальный инструмент: клип теперь можно назвать (MCP не подставляет clip_id).
    sound, ok = run_tool(ToolContext(store=store), "audio_map", {"clip_id": clip_id}, validate=True)
    assert ok, sound
    assert sound["time"] == "clip"
    # Громко — там, где слой: раньше основа холста молчала и карта была пустой тишиной.
    lufs, step = sound["loudness"]["lufs"], sound["loudness"]["step"]
    inside = [v for i, v in enumerate(lufs) if VOICE_AT[0] + 0.3 <= i * step <= VOICE_AT[1] - 0.3]
    outside = [v for i, v in enumerate(lufs) if i * step <= VOICE_AT[0] - 1.0]
    assert inside and outside and min(inside) > max(outside) + 20, (inside, outside)
    missing, ok = run_tool(ToolContext(store=store), "audio_map", {}, validate=True)
    assert not ok and "clip_id" in missing["error"]


def test_silent_canvas_says_why(tmp_path, monkeypatch):
    _require_ffmpeg()
    import app.canvas as canvas_mod

    monkeypatch.setattr(canvas_mod, "CANVAS_WIDTH", 108)
    monkeypatch.setattr(canvas_mod, "CANVAS_HEIGHT", 192)
    store = _store(tmp_path, monkeypatch)
    store.create_subtitle_profile("Субтитры", provider="whisper")
    out, ok = run_tool(ToolContext(store=store), "create_canvas_clip", {"duration": 6})
    got, ok = run_tool(ToolContext(store=store, clip_plan_id=out["clip_id"]), "generate_subtitles", {})
    assert not ok and "нет звука" in got["error"]


def test_lint_sync_has_no_false_no_voice_on_a_tts_canvas(canvas, monkeypatch):
    """Субтитры на месте озвучки — без «голоса на этом месте нет»; строка в тишине холста — с ним."""
    from app import audio_map
    from app.quality_lint_montage import sub_sync_findings
    from app.speech_onsets import FRAME_SEC, voiced_frames

    import numpy as np

    def energy_vad(x, rate=16000):  # тестовая тишина/тон: VAD по энергии, без модели Silero
        pcm = (np.clip(np.asarray(x, dtype=np.float32), -1, 1) * 32767).astype("<i2").tobytes()
        return audio_map._runs([bool(v) for v in voiced_frames(pcm, rate)], FRAME_SEC, min_len=0.15, gap=0.15), "energy"

    monkeypatch.setattr(audio_map, "vad_segments", energy_vad)
    store, clip_id = canvas
    run_tool(ToolContext(store=store, clip_plan_id=clip_id), "generate_subtitles", {})
    plan = store.get_clip_plan(clip_id)
    from app.agent_tools import plan_clock

    findings, info = sub_sync_findings(store, plan, plan_clock(store, plan))
    assert info.get("lines") and info.get("by_text") is True, info
    assert not [f for f in findings if "голоса на этом месте нет" in f["message"]], findings

    # Строка там, где холст молчит, — честная находка.
    from app.clip_subtitles import save_lines

    payload = store.get_clip_plan_subtitles(clip_id)
    save_lines(store, clip_id, [*payload["lines"], {"start": 6.5, "end": 7.5, "text": "тишина здесь"}],
               [*payload["words"], {"word": "тишина", "start": 6.5, "end": 7.0}, {"word": "здесь", "start": 7.0, "end": 7.5}])
    findings, _info = sub_sync_findings(store, store.get_clip_plan(clip_id), plan_clock(store, plan))
    assert [f for f in findings if "тишина" in f["message"]], findings
