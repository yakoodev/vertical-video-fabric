"""Расшифровка длинного стрима кусками: время каждого куска сдвигается на его начало."""

from app.ai import transcript


def test_long_source_is_transcribed_in_chunks_with_offsets(tmp_path, monkeypatch):
    src = tmp_path / "stream.mp4"
    src.write_bytes(b"x")
    monkeypatch.setattr(transcript, "_duration_sec", lambda path: 2.5 * transcript.CHUNK_SEC)
    calls = []

    def fake_piece(path, start, length, model, tracks=None):
        calls.append((start, length))
        return [{"start": round(start + 1.0, 2), "end": round(start + 2.0, 2), "text": f"кусок {len(calls)}"}]

    monkeypatch.setattr(transcript, "_transcribe_piece", fake_piece)
    cues = transcript.transcribe_source_cues(str(src))
    assert [c[0] for c in calls] == [0.0, transcript.CHUNK_SEC, 2 * transcript.CHUNK_SEC]
    assert all(length == transcript.CHUNK_SEC for _s, length in calls)
    assert [c["start"] for c in cues] == [1.0, transcript.CHUNK_SEC + 1.0, 2 * transcript.CHUNK_SEC + 1.0]


def test_short_source_is_one_piece(tmp_path, monkeypatch):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"x")
    monkeypatch.setattr(transcript, "_duration_sec", lambda path: 300.0)
    calls = []
    monkeypatch.setattr(transcript, "_transcribe_piece", lambda p, s, l, m, tracks=None: calls.append((s, l)) or [])
    transcript.transcribe_source_cues(str(src))
    assert calls == [(0.0, None)]
