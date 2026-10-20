"""«Голос вперёд»: отделить речь от фоновой музыки стрима и приглушить музыку.

Владелец: «нихуя не слышно стримера». На стримах фоном играет музыка (рисует под
неё, плейлист), а нормализация громкости клипа подтягивает её вместе с голосом.
Модель разделения UVR MDX-Net (``Kim_Vocal_2.onnx``) вычленяет голос; остальное
(музыка, игра) идёт тише на ``music_db``. Работает на onnxruntime — torch не нужен.

STFT/iSTFT — numpy, ровно в тех соглашениях, на которых модель училась
(как в UVR: окно Ханна n_fft = 7680, шаг 1024, первые 3072 бина, каналы
[L.re, L.im, R.re, R.im], куски по 256 кадров с перекрытием ``trim``).
"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import Any

import numpy as np

from app.settings import settings

SAMPLE_RATE = 44100
N_FFT = 7680
HOP = 1024
DIM_F = 3072
DIM_T = 256
COMPENSATE = 1.009  # Kim_Vocal_2: модель чуть занижает громкость голоса
CHUNK = HOP * (DIM_T - 1)
TRIM = N_FFT // 2
GEN = CHUNK - 2 * TRIM
MODEL_NAME = "Kim_Vocal_2.onnx"
DEFAULT_MUSIC_DB = -12.0

_WINDOW = np.hanning(N_FFT + 1)[:-1].astype(np.float32)  # периодическое окно, как torch.hann_window
_session: Any = None
_lock = threading.Lock()


def model_path() -> Path:
    return settings.data_dir / "models" / MODEL_NAME


def available() -> bool:
    return model_path().is_file()


# --------------------------------------------------------------------------- #
# STFT
# --------------------------------------------------------------------------- #


def stft(x: np.ndarray) -> np.ndarray:
    """[каналы, сэмплы] → комплексный спектр [каналы, N_FFT//2+1, кадры] (center=True, отражение)."""
    pad = N_FFT // 2
    xp = np.pad(x, ((0, 0), (pad, pad)), mode="reflect")
    frames = 1 + (xp.shape[1] - N_FFT) // HOP
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(frames)[:, None]
    seg = xp[:, idx] * _WINDOW  # [каналы, кадры, N_FFT]
    return np.fft.rfft(seg, axis=-1).transpose(0, 2, 1)


def istft(spec: np.ndarray, length: int) -> np.ndarray:
    """Обратно: [каналы, бины, кадры] → [каналы, length] (overlap-add с нормировкой окна)."""
    frames = spec.shape[2]
    seg = np.fft.irfft(spec.transpose(0, 2, 1), n=N_FFT, axis=-1) * _WINDOW
    total = N_FFT + HOP * (frames - 1)
    out = np.zeros((spec.shape[0], total), dtype=np.float64)
    norm = np.zeros(total, dtype=np.float64)
    for k in range(frames):
        out[:, k * HOP:k * HOP + N_FFT] += seg[:, k]
        norm[k * HOP:k * HOP + N_FFT] += _WINDOW ** 2
    out /= np.maximum(norm, 1e-8)
    pad = N_FFT // 2
    return out[:, pad:pad + length].astype(np.float32)


def _to_model(spec: np.ndarray) -> np.ndarray:
    """[2, бины, 256] комплексный → [4, DIM_F, 256] (L.re, L.im, R.re, R.im)."""
    s = spec[:, :DIM_F]
    return np.stack([s[0].real, s[0].imag, s[1].real, s[1].imag]).astype(np.float32)


def _from_model(x: np.ndarray) -> np.ndarray:
    """[4, DIM_F, 256] → [2, N_FFT//2+1, 256] комплексный (верхние бины — нули)."""
    bins = N_FFT // 2 + 1
    out = np.zeros((2, bins, x.shape[-1]), dtype=np.complex64)
    out[0, :DIM_F] = x[0] + 1j * x[1]
    out[1, :DIM_F] = x[2] + 1j * x[3]
    return out


# --------------------------------------------------------------------------- #
# Разделение
# --------------------------------------------------------------------------- #


def _model() -> Any:
    global _session
    with _lock:
        if _session is None:
            import onnxruntime as ort

            opts = ort.SessionOptions()
            opts.intra_op_num_threads = max(1, int(getattr(settings, "voice_threads", 4) or 4))
            _session = ort.InferenceSession(str(model_path()), opts, providers=["CPUExecutionProvider"])
        return _session


def separate_vocals(mix: np.ndarray, run: Any = None) -> np.ndarray:
    """Стерео [2, n] 44.1 кГц → голос [2, n]. ``run`` — модель (для тестов), иначе ONNX."""
    if run is None:
        session = _model()
        name = session.get_inputs()[0].name

        def run(batch: np.ndarray) -> np.ndarray:
            return session.run(None, {name: batch})[0]

    n = mix.shape[1]
    pad = GEN - (n % GEN)
    zeros = np.zeros((2, TRIM), np.float32)
    mixture = np.concatenate([zeros, mix, np.zeros((2, pad), np.float32), zeros], axis=1)
    pieces = []
    for start in range(0, n + pad, GEN):
        wave = mixture[:, start:start + CHUNK]
        spec = _to_model(stft(wave))
        spec[:, :3] = 0  # самые низкие бины модель не учили — как в UVR
        pred = run(spec[None])[0]
        voice = istft(_from_model(pred), CHUNK)
        pieces.append(voice[:, TRIM:-TRIM])
    return (np.concatenate(pieces, axis=1)[:, :n] * COMPENSATE).astype(np.float32)


def voice_forward_wav(src: Path, dst: Path, music_db: float = DEFAULT_MUSIC_DB, run: Any = None) -> None:
    """Звук файла ``src`` → wav ``dst``: голос как есть, остальное тише на ``music_db``."""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(src), "-vn", "-ac", "2", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-"],
        capture_output=True, check=True, timeout=600,
    ).stdout
    mix = np.frombuffer(raw, dtype=np.float32).reshape(-1, 2).T.copy()
    if mix.shape[1] < SAMPLE_RATE // 10:
        raise ValueError("слишком короткий звук")
    voice = separate_vocals(mix, run)
    gain = 10 ** (music_db / 20)
    out = voice + (mix - voice) * gain
    peak = float(np.max(np.abs(out))) or 1.0
    if peak > 0.99:
        out *= 0.99 / peak
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(SAMPLE_RATE), "-ac", "2", "-i", "-", str(dst)],
        input=out.T.astype(np.float32).tobytes(), check=True, timeout=600,
    )


def apply_voice_forward(video: Path, music_db: float = DEFAULT_MUSIC_DB) -> bool:
    """Переписать звук готового клипа «голос вперёд» на месте. Нет модели — False."""
    if not available():
        return False
    wav = video.with_suffix(".voice.wav")
    out = video.with_suffix(".voice.mp4")
    try:
        voice_forward_wav(video, wav, music_db)
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", str(video), "-i", str(wav), "-map", "0:v", "-map", "1:a",
             "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", str(out)],
            check=True, timeout=600,
        )
        out.replace(video)
        return True
    finally:
        wav.unlink(missing_ok=True)
        out.unlink(missing_ok=True)
