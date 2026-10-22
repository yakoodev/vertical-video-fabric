"""Расшифровка длинного стрима кусками: время каждого куска сдвигается на его начало.

Куски с нахлёстом: фраза на стыке распознаётся целиком в своём куске, следующий кусок
начинается после неё — без дыр и без дублей (агент стримов: дыра 3120–3156 с)."""

from app.ai import transcript


def test_long_source_is_transcribed_in_chunks_with_offsets(tmp_path, monkeypatch):
    src = tmp_path / "stream.mp4"
    src.write_bytes(b"x")
    monkeypatch.setattr(transcript, "_duration_sec", lambda path: 2.5 * transcript.CHUNK_SEC)
    calls = []

    def fake_piece(path, start, length, model, tracks=None, state=None):
        calls.append((start, length))
        return [{"start": round(start + 1.0, 2), "end": round(start + 2.0, 2), "text": f"кусок {len(calls)}"}]

    monkeypatch.setattr(transcript, "_transcribe_piece", fake_piece)
    cues = transcript.transcribe_source_cues(str(src))
    assert [c[0] for c in calls] == [0.0, transcript.CHUNK_SEC, 2 * transcript.CHUNK_SEC]
    # Звук куска длиннее на нахлёст — фраза на границе слышна целиком.
    assert all(length == transcript.CHUNK_SEC + transcript.CHUNK_OVERLAP_SEC for _s, length in calls)
    assert [c["start"] for c in cues] == [1.0, transcript.CHUNK_SEC + 1.0, 2 * transcript.CHUNK_SEC + 1.0]


def test_short_source_is_one_piece(tmp_path, monkeypatch):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"x")
    monkeypatch.setattr(transcript, "_duration_sec", lambda path: 300.0)
    calls = []
    monkeypatch.setattr(transcript, "_transcribe_piece",
                        lambda p, s, l, m, tracks=None, state=None: calls.append((s, l)) or [])
    transcript.transcribe_source_cues(str(src))
    assert calls == [(0.0, None)]


def test_phrase_on_the_chunk_boundary_is_kept_whole_and_not_doubled(tmp_path, monkeypatch):
    """Фраза 595–608 с при кусках по 600 с: целиком в первом куске; второй начинается с 608."""
    src = tmp_path / "stream.mp4"
    src.write_bytes(b"x")
    monkeypatch.setattr(transcript, "_duration_sec", lambda path: 1500.0)
    speech = [(100.0, 104.0, "раз"), (595.0, 608.0, "фраза на стыке"), (612.0, 615.0, "дальше"),
              (1199.0, 1203.0, "второй стык"), (1400.0, 1402.0, "конец")]
    calls = []

    def fake_piece(path, start, length, model, tracks=None, state=None):
        calls.append(start)
        end = start + length if length else 1e9
        # Whisper видит только свой звук: фраза, начатая до звука, — обрывок с её хвоста.
        out = []
        for a, b, text in speech:
            if b <= start or a >= end:
                continue
            out.append({"start": max(a, start), "end": min(b, end), "text": text if a >= start else "…хвост"})
        return out

    monkeypatch.setattr(transcript, "_transcribe_piece", fake_piece)
    cues = transcript.transcribe_source_cues(str(src), chunk_sec=600)
    assert [c["text"] for c in cues] == ["раз", "фраза на стыке", "дальше", "второй стык", "конец"]
    assert cues[1] == {"start": 595.0, "end": 608.0, "text": "фраза на стыке"}
    assert calls == [0.0, 608.0, 1208.0]  # следующий кусок — с конца фразы на стыке


def test_language_is_pinned_after_a_confident_chunk(tmp_path, monkeypatch):
    """Кусок, начавшийся с английского TTS-доната, не переключает распознавание на английский."""
    from types import SimpleNamespace

    from app.subtitles import whisper_local

    seen = []

    class FakeProvider:
        def transcribe(self, audio_path, profile, model):
            seen.append(profile.get("language"))
            return SimpleNamespace(language="ru", usage={"whisperLanguageProbability": 0.95},
                                   segments=[SimpleNamespace(start=1.0, end=2.0, text="привет")])

    monkeypatch.setattr(whisper_local, "WhisperSubtitleProvider", FakeProvider)
    monkeypatch.setattr("app.render._run_ffmpeg", lambda args, timeout=0: None)
    state: dict = {}
    first = transcript._transcribe_piece(tmp_path / "s.mp4", 0.0, 60.0, "small", state=state)
    transcript._transcribe_piece(tmp_path / "s.mp4", 60.0, 60.0, "small", state=state)
    assert first == [{"start": 1.0, "end": 2.0, "text": "привет"}]
    assert seen == ["", "ru"] and state["language"] == "ru"
