"""Есть ли под сегментом распознавания речь: детектор голоса (Silero, тот же, что у faster-whisper) и громкость.

Whisper на тишине, шуме и музыке пишет текст из обучающих титров («Продолжение следует…», «Редактор субтитров …»),
а на хвосте тишины после речи дописывает их же или повторяет последнюю фразу (стенд tools/asr_bench, разбор —
docs/knowledge/youtube/skrytyj-sloj.md). Самому тексту верить нельзя: «Спасибо за просмотр» бывает и настоящим.
Здесь — улики из звука:

* ``vad_share`` — доля сегмента, где детектор голоса слышит речь (строгие настройки, без «подушек» VAD распознавания);
* ``is_silent`` (app/subtitles/phantoms.py) — под сегментом тишина по громкости (порог от шума дорожки);
* ``speech_end_cut`` — где кончается звук записи: хвост почти тишины длиннее нескольких секунд Whisper не отдаём.

Всё чистые функции над float32 16 кГц; звук не прочитался — улик нет (``None``), решение остаётся старым."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

RATE = 16000
# Строгий детектор: порог как у app/audio_map.vad_segments, «подушка» 30 мс — доля считается по самому голосу.
VAD_THRESHOLD = 0.5
VAD_MIN_SPEECH_MS = 150
VAD_MIN_SILENCE_MS = 150
VAD_PAD_MS = 30
# Хвост тишины: отрезаем, если после последнего звука ещё столько секунд почти тишины; оставляем запас.
TAIL_MIN_SEC = 3.0
TAIL_KEEP_SEC = 1.0


def load_audio(path: Any) -> Any | None:
    """Файл → float32 16 кГц моно (декодер faster-whisper, не вышло — ffmpeg). Нет файла / декодера — ``None``."""
    from pathlib import Path

    if not Path(str(path)).is_file():
        return None
    try:
        from faster_whisper.audio import decode_audio

        return decode_audio(str(path), sampling_rate=RATE)
    except Exception:  # noqa: BLE001 - локальный PyAV бывает несовместим (tools/viewer_ep.py) — ffmpeg
        pass
    try:
        import subprocess

        import numpy as np

        pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"],
                             capture_output=True, check=True, timeout=600).stdout
        return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    except Exception:  # noqa: BLE001 - улики необязательны: без них работает старая логика
        return None


def speech_spans(audio: Any, rate: int = RATE) -> list[tuple[float, float]] | None:
    """Отрезки речи по Silero VAD, секунды. Модели нет — ``None`` (не «речи нет»)."""
    try:
        from faster_whisper.vad import VadOptions, get_speech_timestamps

        opts = VadOptions(threshold=VAD_THRESHOLD, min_speech_duration_ms=VAD_MIN_SPEECH_MS,
                          min_silence_duration_ms=VAD_MIN_SILENCE_MS, speech_pad_ms=VAD_PAD_MS)
        found = get_speech_timestamps(audio, opts, sampling_rate=rate)
    except Exception:  # noqa: BLE001
        return None
    return [(s["start"] / rate, s["end"] / rate) for s in found]


def vad_share(start: float, end: float, spans: list[tuple[float, float]] | None) -> float | None:
    """Доля ``[start, end]``, покрытая речью детектора (0…1). Улик нет — ``None``."""
    if spans is None:
        return None
    length = max(1e-3, float(end) - float(start))
    covered = sum(max(0.0, min(end, b) - max(start, a)) for a, b in spans)
    return max(0.0, min(1.0, covered / length))


def speech_end_cut(levels: list[float], threshold: float, step: float = 0.02, tail_min: float = TAIL_MIN_SEC,
                   keep: float = TAIL_KEEP_SEC) -> float | None:
    """Секунда, где резать запись: после последнего кадра громче порога речи ещё ``tail_min`` с почти тишины.
    Звука нет совсем или хвост короткий — ``None`` (не резать)."""
    last = None
    for i in range(len(levels) - 1, -1, -1):
        if levels[i] is not None and levels[i] >= threshold:
            last = i
            break
    if last is None:
        return None
    end_sound = (last + 1) * step
    total = len(levels) * step
    if total - end_sound < tail_min:
        return None
    return round(end_sound + keep, 3)


@dataclass
class Evidence:
    """Улики записи для фильтра сегментов: отрезки речи детектора и громкость кадров."""

    spans: list[tuple[float, float]] | None
    levels: list[float]
    threshold: float

    @classmethod
    def of(cls, audio: Any, rate: int = RATE) -> "Evidence":
        import numpy as np

        from app.subtitles.phantoms import frame_levels, speech_threshold

        pcm = (np.clip(np.asarray(audio, dtype=np.float32), -1, 1) * 32767).astype("<i2").tobytes()
        levels = frame_levels(pcm, rate)
        return cls(spans=speech_spans(audio, rate), levels=levels,
                   threshold=speech_threshold(levels) if levels else 0.0)

    def share(self, start: float, end: float) -> float | None:
        return vad_share(start, end, self.spans)

    def silent(self, start: float, end: float) -> bool:
        from app.subtitles.phantoms import is_silent

        return bool(self.levels) and is_silent(self.levels, self.threshold, float(start), float(end))
