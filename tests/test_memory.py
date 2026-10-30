"""Память процесса (OOM контейнера 25–26.10): Whisper не лежит в памяти без дела,
освобождённое после тяжёлой задачи отдаётся системе, RSS — в логе до и после."""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from types import SimpleNamespace

import numpy as np
import pytest

from app import memory
from app.heavy_queue import HEAVY, HeavyQueue
from app.settings import settings
from app.subtitles.whisper_local import WhisperSubtitleProvider


class _Model:
    def transcribe(self, *_a, **_k):
        seg = SimpleNamespace(no_speech_prob=0.1, avg_logprob=-0.2, text="да", start=0.0, end=0.5,
                              words=[SimpleNamespace(word="да", start=0.0, end=0.5, probability=0.9)])
        return iter([seg]), SimpleNamespace(language="ru", language_probability=0.99)


@pytest.fixture
def released(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(memory, "release", lambda: calls.append("release") or True)
    WhisperSubtitleProvider.unload()
    WhisperSubtitleProvider._models.clear()
    yield calls
    WhisperSubtitleProvider._cancel_unload()
    WhisperSubtitleProvider._models.clear()


def test_rss_is_read_from_proc_status(monkeypatch):
    import io

    status = "Name:\tuvicorn\nVmHWM:\t 4064304 kB\nVmRSS:\t 3702916 kB\nThreads:\t66\n"
    monkeypatch.setattr(memory, "open", lambda *a, **k: io.StringIO(status), raising=False)
    assert memory.rss_mb() == pytest.approx(3616.1, abs=0.1)
    assert memory.fmt(None) == "?" and memory.fmt(812.4) == "812"


def test_release_never_fails_without_glibc(monkeypatch):
    monkeypatch.setattr(memory, "_libc", False)  # Windows / musl: только gc
    assert memory.release() is False
    memory.limit_arenas()  # тихо ничего


def test_whisper_model_is_unloaded_after_idle_from_the_end_of_the_task(monkeypatch, released):
    monkeypatch.setattr(settings, "whisper_idle_unload_sec", 0.2)
    loads: list[str] = []
    provider = WhisperSubtitleProvider(model_loader=lambda size: loads.append(size) or _Model())
    provider.transcribe("a.wav", {}, "large-v3-turbo")
    provider.transcribe("b.wav", {}, "large-v3-turbo")
    assert loads == ["large-v3-turbo"] and WhisperSubtitleProvider._models  # пачка — одна загрузка
    after_tasks = len(released)  # очередь тяжёлых задач чистит после каждой
    time.sleep(0.5)
    assert not WhisperSubtitleProvider._models
    assert len(released) == after_tasks + 1  # выгрузка отдаёт память системе, а не только gc
    provider.transcribe("c.wav", {}, "large-v3-turbo")
    assert loads == ["large-v3-turbo", "large-v3-turbo"]


def test_only_one_whisper_model_in_memory(monkeypatch, released):
    """Расшифровка исходника (large-v3) и субтитры клипа (turbo) — не две модели сразу."""
    monkeypatch.setattr(settings, "whisper_idle_unload_sec", 60)
    events: list[str] = []

    def loader(size):
        events.append(f"load {size}:{len(WhisperSubtitleProvider._models)}")
        return _Model()

    monkeypatch.setattr(memory, "release", lambda: events.append("release") or True)
    provider = WhisperSubtitleProvider(model_loader=loader)
    provider.transcribe("a.wav", {}, "large-v3")
    provider.transcribe("b.wav", {}, "large-v3-turbo")
    # Старая модель выгружена и память отдана ДО загрузки новой.
    loads = [e for e in events if e.startswith("load")]
    assert loads == ["load large-v3:0", "load large-v3-turbo:0"]
    assert events[events.index("load large-v3-turbo:0") - 1] == "release"
    assert [key[0] for key in WhisperSubtitleProvider._models] == ["large-v3-turbo"]


def test_render_unloads_idle_whisper_but_nested_whisper_keeps_it(monkeypatch, released):
    monkeypatch.setattr(settings, "whisper_idle_unload_sec", 60)
    provider = WhisperSubtitleProvider(model_loader=lambda size: _Model())
    provider.transcribe("a.wav", {}, "large-v3-turbo")
    assert WhisperSubtitleProvider._models

    with HEAVY.slot("render", "клип 1"):
        # Рендер занял очередь — модель Whisper ему не нужна.
        assert not WhisperSubtitleProvider._models
        # Рендер сам распознаёт субтитры (вложенный вызов) — модель грузится и не выгружается.
        provider.transcribe("b.wav", {}, "large-v3-turbo")
        assert WhisperSubtitleProvider._models
    with HEAVY.slot("whisper", "c.wav"):
        assert WhisperSubtitleProvider._models  # свой род — не трогаем


def test_heavy_task_logs_rss_before_after_and_after_trim(monkeypatch, caplog):
    readings = iter([900.0, 2400.0, 1100.0])
    monkeypatch.setattr(memory, "rss_mb", lambda: next(readings))
    trims: list[int] = []
    monkeypatch.setattr(memory, "release", lambda: trims.append(1) or True)
    queue = HeavyQueue(1, track_memory=True)

    with caplog.at_level(logging.INFO, logger="app.heavy_queue"):
        with queue.slot("render", "клип 12"):
            with queue.slot("whisper", "вложенный"):  # вложенный — без своей очистки
                pass
            assert not trims
    assert trims == [1]
    line = next(r.getMessage() for r in caplog.records if "RSS" in r.getMessage())
    assert "render клип 12" in line and "до 900" in line and "после 2400" in line and "после очистки 1100" in line
    assert queue.status() == {"limit": 1, "running": [], "waiting": 0}


def test_memory_hooks_never_break_the_task(monkeypatch):
    def boom():
        raise RuntimeError("no /proc")

    monkeypatch.setattr(memory, "rss_mb", boom)
    queue = HeavyQueue(1, track_memory=True)
    with queue.slot("render", "x"):
        done = True
    assert done and queue.status()["running"] == []


def test_action_signals_streamed_by_blocks_match_whole_file_math():
    """Детектор экшна читает кадры и звук блоками — числа те же, что по всему файлу сразу."""
    from app.ai import action_detect

    rng = np.random.default_rng(7)
    pixels = action_detect._MOTION_W * action_detect._MOTION_H
    raw = rng.integers(0, 256, size=pixels * 37 + 100, dtype=np.uint8).tobytes()  # хвост — неполный кадр
    count = len(raw) // pixels
    frames = np.frombuffer(raw, dtype=np.uint8)[: count * pixels].reshape(count, pixels).astype(np.float32)
    whole = np.abs(np.diff(frames, axis=0)).mean(axis=1)
    blocks = [raw[i:i + pixels * 5] for i in range(0, len(raw), pixels * 5)]
    assert np.allclose(action_detect.motion_signal(blocks), whole)

    hop = action_detect._AUDIO_SR // action_detect._MOTION_FPS
    wav = rng.integers(-32768, 32767, size=hop * 9 + 123, dtype=np.int16).tobytes()
    audio = np.frombuffer(wav, dtype=np.int16).astype(np.float32) / 32768.0
    n = len(audio) // hop
    expected = np.sqrt((audio[: n * hop].reshape(n, hop) ** 2).mean(axis=1))
    chunks = [wav[i:i + hop * 2 * 2] for i in range(0, len(wav), hop * 2 * 2)]
    assert np.allclose(action_detect.loudness_signal(chunks), expected)
    assert len(action_detect.loudness_signal([])) == 0


def test_action_detection_reads_real_ffmpeg_output(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg не установлен")
    from app.ai import action_detect

    video = tmp_path / "a.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10:duration=40",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=40", "-shortest", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", "-c:a", "aac", str(video)],
        check=True, capture_output=True,
    )
    regions = action_detect.detect_action_regions(video, 40.0, min_len=5.0, max_len=20.0)
    assert regions and all(0 <= start < end <= 40.0 for _score, start, end in regions)
    with pytest.raises(RuntimeError, match="ffmpeg failed"):
        list(action_detect._ffmpeg_blocks(["-i", str(tmp_path / "missing.mp4"), "-f", "s16le", "-"], 4096))
