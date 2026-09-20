"""Субтитры клипа до рендера: сборка аудио, правка строк, сдвиг под переходы."""

from __future__ import annotations

import subprocess
from pathlib import Path

from app.clip_subtitles import (
    build_clip_audio_args,
    normalize_lines,
    payload_from_result,
    rebuild_words,
    result_from_payload,
    shift_for_transition,
)
from app.subtitles.contracts import SubtitleResult, SubtitleSegment, SubtitleWord
from test_api_pipeline import _require_ffmpeg


def test_normalize_lines_drops_junk_and_sorts():
    lines = normalize_lines(
        [
            {"start": 5, "end": 6, "text": "  второй   кусок "},
            {"start": 1, "end": 2, "text": "первый"},
            {"start": 3, "end": 3, "text": "нулевая длина"},
            {"start": 4, "end": 5, "text": "   "},
            "мусор",
        ]
    )
    assert [l["text"] for l in lines] == ["первый", "второй кусок"]


def test_rebuild_words_keeps_timing_of_untouched_lines():
    old = [
        {"word": "привет", "start": 0.0, "end": 0.5},
        {"word": "мир", "start": 0.6, "end": 1.0},
        {"word": "пока", "start": 2.0, "end": 2.4},
    ]
    lines = [
        {"start": 0.0, "end": 1.0, "text": "привет мир"},      # не менялась
        {"start": 2.0, "end": 2.6, "text": "пока совсем"},      # переписана
    ]
    words = rebuild_words(lines, old)
    assert words[:2] == [{"word": "привет", "start": 0.0, "end": 0.5}, {"word": "мир", "start": 0.6, "end": 1.0}]
    assert [w["word"] for w in words[2:]] == ["пока", "совсем"]
    assert words[2]["start"] >= 2.0 and words[-1]["end"] <= 2.7


def test_shift_for_transition_pulls_later_pieces_earlier():
    payload = {
        "lines": [{"start": 1.0, "end": 2.0, "text": "в первом"}, {"start": 11.0, "end": 12.0, "text": "во втором"}],
        "words": [{"word": "во", "start": 11.0, "end": 11.4}],
    }
    out = shift_for_transition(payload, [(100.0, 110.0), (200.0, 208.0)], overlap=0.5)
    assert out["lines"][0]["start"] == 1.0          # первый кусок не двигается
    assert out["lines"][1]["start"] == 10.5         # второй начинается на overlap раньше
    assert out["words"][0]["start"] == 10.5


def test_payload_roundtrip_keeps_karaoke_words():
    result = SubtitleResult(
        text="привет мир",
        language="ru",
        duration=1.2,
        segments=[SubtitleSegment(start=0.0, end=1.0, text="привет мир")],
        words=[SubtitleWord(word="привет", start=0.0, end=0.5), SubtitleWord(word="мир", start=0.6, end=1.0)],
    )
    payload = payload_from_result(result, "whisper", "small")
    assert payload["lines"] == [{"start": 0.0, "end": 1.0, "text": "привет мир"}]
    back = result_from_payload(payload)
    assert [w.word for w in back.words] == ["привет", "мир"]
    assert back.segments[0].text == "привет мир"


def test_clip_audio_concatenates_pieces(tmp_path):
    _require_ffmpeg()
    src = tmp_path / "src.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=12", str(src)],
        check=True,
        timeout=60,
    )
    out = tmp_path / "clip.wav"
    subprocess.run(build_clip_audio_args(src, [(1.0, 4.0), (8.0, 10.0)], out), check=True, timeout=60)
    dur = float(
        subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout.strip()
    )
    assert abs(dur - 5.0) < 0.2  # 3 s + 2 s, склеено встык
    assert Path(out).stat().st_size > 1000
