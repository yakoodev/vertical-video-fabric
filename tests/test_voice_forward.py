"""«Голос вперёд»: STFT/iSTFT без потерь и сборка кусков разделения (без настоящей модели)."""

import numpy as np

from app import voice_forward as vf


def test_stft_roundtrip_is_lossless():
    rng = np.random.default_rng(0)
    x = rng.standard_normal((2, vf.CHUNK)).astype(np.float32) * 0.3
    back = vf.istft(vf.stft(x), vf.CHUNK)
    assert np.max(np.abs(back - x)) < 1e-4
    spec = vf.stft(x)
    assert spec.shape == (2, vf.N_FFT // 2 + 1, vf.DIM_T)


def test_separation_with_identity_model_returns_band_limited_mix():
    """Модель «вернуть вход» = весь звук в полосе модели: длина и громкость сохраняются."""
    sr = vf.SAMPLE_RATE
    t = np.arange(int(sr * 13.3)) / sr
    tone = (0.4 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    mix = np.stack([tone, tone * 0.5])
    calls = []
    voice = vf.separate_vocals(mix, run=lambda batch: calls.append(batch.shape) or batch)
    assert voice.shape == mix.shape
    assert calls and all(c == (1, 4, vf.DIM_F, vf.DIM_T) for c in calls)
    middle = slice(sr, -sr)
    assert np.max(np.abs(voice[:, middle] / vf.COMPENSATE - mix[:, middle])) < 2e-3


def test_voice_forward_quietens_the_rest(tmp_path):
    from test_api_pipeline import _require_ffmpeg
    import subprocess

    _require_ffmpeg()
    src = tmp_path / "in.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=300:d=3", "-ac", "2", "-ar", "44100", str(src)], check=True)
    dst = tmp_path / "out.wav"
    # «Модель» не нашла голоса — всё остальное должно стать тише на 12 дБ.
    vf.voice_forward_wav(src, dst, -12.0, run=lambda batch: np.zeros_like(batch))

    def rms(path):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-"], capture_output=True, check=True).stdout
        a = np.frombuffer(raw, dtype=np.float32)
        return float(np.sqrt(np.mean(a ** 2)))

    ratio_db = 20 * np.log10(rms(dst) / rms(src))
    assert -13.0 < ratio_db < -11.0
