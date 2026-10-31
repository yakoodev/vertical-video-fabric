"""listen глушит то же, что рендер (render.mute) — иначе им не проверить глушение мата."""

from __future__ import annotations

import wave


def test_listen_silences_mute_like_render(tmp_path):
    from app.clip_subtitles import mute_in_parts, silence_wav
    from app.timebase import ClipClock

    segs = [{"start_sec": 0, "end_sec": 10}, {"start_sec": 30, "end_sec": 40}]
    clock = ClipClock(segs, None, [{"start": 2.0, "end": 3.0}])
    # mute 4.0–4.5 (clip) → после выреза 1 с: 3.0–3.5 частей; listen с 2.5 — 0.5–1.0 от начала.
    assert mute_in_parts(clock, [{"start": 4.0, "end": 4.5, "by": "censor", "level": 1}], 2.5, 8.0) == [(0.5, 1.0)]
    assert mute_in_parts(clock, [{"start": 9.0, "end": 9.5}], 0.0, 5.0) == []

    path = tmp_path / "a.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x10\x00" * 16000)
    silence_wav(path, [(0.25, 0.5)])
    with wave.open(str(path), "rb") as r:
        data = r.readframes(r.getnframes())
    assert data[4000 * 2:8000 * 2] == bytes(8000) and data[:4000 * 2] == b"\x10\x00" * 4000
    assert data[8000 * 2:] == b"\x10\x00" * 8000
