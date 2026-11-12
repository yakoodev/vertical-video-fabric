"""Голос → формы ARKit (app/wav2arkit.py): без модели — None (лицо по гласным); кадры 30 к/с кусками, без швов."""

from __future__ import annotations

import json

import numpy as np

from app import wav2arkit as W


def test_no_model_means_none(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(W, "DEFAULT_DIR", tmp_path)  # пустой каталог
    assert W.model_dir() is None
    assert W.curves(np.zeros(16000, np.float32), 16000) is None


def test_chunks_give_30fps_frames(tmp_path, monkeypatch) -> None:
    names = [f"b{i}" for i in range(52)]
    (tmp_path / "config.json").write_text(json.dumps({"blendshape_names": names}), "utf-8")
    (tmp_path / "wav2arkit_cpu.onnx").write_bytes(b"")
    calls = []

    class Fake:
        def run(self, _out, feed):
            x = feed["audio_waveform"]
            calls.append(x.shape[1])
            n = int(np.ceil(30 * x.shape[1] / 16000))
            return [np.full((1, n, 52), 1.5, np.float32)]

    monkeypatch.setattr(W, "_session", lambda d: Fake())
    c = W.curves(np.zeros(48000 * 5, np.float32), 48000, chunk=2.0, d=tmp_path)
    assert len(c["frames"]) == 150 and len(c["frames"][0]) == 52 and c["names"] == names
    assert calls == [32000, 32000, 16000] and c["frames"][0][0] == 1.0  # кусками по 2 с, значения в [0, 1]
