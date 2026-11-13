# -*- coding: utf-8 -*-
"""Послушать короткие окна вокального стема Whisper'ом без подсказки — проверить, какие слова где.

    python tools/music_video/probe.py OUT 12.0-16.5 16.0-20.5 ...   (секунды песни)"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from audio import load_wav  # noqa: E402


def main(out: Path, windows: list[tuple[float, float]], lang: str = "ru",
         model: str = "mobiuslabsgmbh/faster-whisper-large-v3-turbo") -> list[str]:
    from faster_whisper import WhisperModel
    an = json.load(open(out / "analysis.json", encoding="utf-8"))
    x, sr = load_wav(str(out / "work" / "sep" / "htdemucs" / "mix44" / "vocals.wav"))
    t0 = an["start"]
    m = WhisperModel(model, device="cpu", compute_type="int8")
    lines = []
    for a, b in windows:
        seg = x[int((a - t0) * sr):int((b - t0) * sr)]
        n16 = int(len(seg) * 16000 / sr)
        seg16 = np.interp(np.linspace(0, len(seg) - 1, n16), np.arange(len(seg)), seg).astype(np.float32)
        seg16 = np.concatenate([np.zeros(8000, np.float32), seg16, np.zeros(8000, np.float32)])
        segs, _ = m.transcribe(seg16, language=lang, word_timestamps=True, vad_filter=False, beam_size=5,
                               condition_on_previous_text=False)
        ws = [f"{w.word.strip()}@{a + w.start - 0.5:.2f}" for s in segs for w in s.words]
        line = f"[{a:.2f}-{b:.2f}] " + " ".join(ws)
        print(line, flush=True)
        lines.append(line)
    return lines


if __name__ == "__main__":
    wins = [tuple(float(v) for v in w.split("-")) for w in sys.argv[2:]]
    main(Path(sys.argv[1]), wins)
