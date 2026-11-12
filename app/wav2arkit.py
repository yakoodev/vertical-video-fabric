"""Голос → 52 формы лица ARKit (рот, челюсть, щёки, брови) офлайн на процессоре: Wav2ARKit (myned-ai, ONNX).

Модель — https://huggingface.co/myned-ai/wav2arkit_cpu, карточка: «License: apache-2.0» (кодировщик wav2vec2-base-960h
+ декодер LAM_audio2exp). Не в репозитории и не в образе: каталог ``WAV2ARKIT_DIR`` (по умолчанию
D:/vvf-offload/models/wav2arkit) с wav2arkit_cpu.onnx, wav2arkit_cpu.onnx.data (≈400 МБ) и config.json. Нет модели —
None, лицо по гласным (app/lipsync.py) как обычно.

Выход — ``timeline.arkit`` для node/vrm (facemix.ts): {fps: 30, start, names: [52 имени], frames: [[52 веса]…]}. Нужна
модель с «perfect sync» (52 выражения ARKit, например hinzka/52blendshapes-for-VRoid-face); у прочих моделей дорожку
некому принять, и facemix её не трогает.
"""

from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from typing import Any

import numpy as np

SR = 16000
DEFAULT_DIR = Path(os.environ.get("WAV2ARKIT_DIR") or "D:/vvf-offload/models/wav2arkit")
_sessions: dict[str, Any] = {}


def model_dir() -> Path | None:
    d = DEFAULT_DIR
    return d if (d / "wav2arkit_cpu.onnx").is_file() and (d / "config.json").is_file() else None


def _session(d: Path):
    key = str(d)
    if key not in _sessions:
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = max(1, min(4, (os.cpu_count() or 2) // 2))
        _sessions[key] = ort.InferenceSession(str(d / "wav2arkit_cpu.onnx"), opts, providers=["CPUExecutionProvider"])
    return _sessions[key]


def to16k(pcm: np.ndarray, sr: int) -> np.ndarray:
    x = np.asarray(pcm, dtype=np.float64).reshape(-1)
    if sr != SR:
        n = int(len(x) * SR / sr)
        x = np.interp(np.arange(n) * (sr / SR), np.arange(len(x)), x)
    return x.astype(np.float32)


def curves(pcm: np.ndarray, sr: int, *, chunk: float = 20.0, d: Path | None = None) -> dict[str, Any] | None:
    """Голос → {fps, names, frames, seconds}. Длинный голос — кусками по chunk с (стык — без перекрытия, 30 к/с
    ровно делят 16 кГц). Нет модели — None."""
    d = d or model_dir()
    if d is None:
        return None
    cfg = json.loads((d / "config.json").read_text("utf-8"))
    names = list(cfg["blendshape_names"])
    sess = _session(d)
    x = to16k(pcm, sr)
    t0 = time.time()
    step = int(chunk * SR)
    out = []
    for a in range(0, max(1, len(x)), step):
        seg = x[a:a + step]
        if len(seg) < SR // 10:
            seg = np.pad(seg, (0, SR // 10 - len(seg)))
        y = sess.run(None, {"audio_waveform": seg[None, :]})[0][0]
        out.append(y[: int(math.ceil(30 * len(x[a:a + step]) / SR))])
    frames = np.clip(np.concatenate(out), 0, 1) if out else np.zeros((0, len(names)))
    return {"fps": 30, "start": 0.0, "names": names, "frames": [[round(float(v), 3) for v in row] for row in frames],
            "seconds": round(time.time() - t0, 3), "audio": round(len(x) / SR, 3)}


def from_track(path: Any, *, offset: float = 0.0) -> dict[str, Any] | None:
    """Файл голоса → ``timeline.arkit`` (голос в ролике начинается с offset); нет модели или не вышло — None."""
    from app import series3d_voice as VF

    try:
        c = curves(VF.decode(path), VF.SR)
    except Exception:  # noqa: BLE001 - мимика по звуку — улучшение, без неё лицо по гласным
        return None
    if c:
        c["start"] = round(offset, 3)
    return c
