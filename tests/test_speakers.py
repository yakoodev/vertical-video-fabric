"""Голоса клипа: сшивка реплик Gemini со словами whisper, строки без смешения голосов."""

from app.ai.speakers import lines_from_words, merge_voices, parse_utterances, separate_voices


def _w(text: str, start: float, step: float = 0.4):
    return [{"word": t, "start": round(start + i * step, 2), "end": round(start + i * step + 0.3, 2)}
            for i, t in enumerate(text.split())]


def test_merge_restores_words_whisper_lost_under_another_voice():
    # Whisper услышал ролик и часть фразы стримерши: «смотрю» и «думаю» потеряны под голосом ролика.
    whisper = _w("ки передач", 0.0) + _w("я вот сейчас на это и", 1.0)
    utterances = [
        {"s": "B", "start": 0.0, "end": 0.8, "text": "ки передач"},
        {"s": "A", "start": 1.0, "end": 4.2, "text": "я вот сейчас на это смотрю и думаю"},
    ]
    merged = merge_voices(whisper, utterances)
    assert [w["word"] for w in merged if w["speaker"] == "A"] == "я вот сейчас на это смотрю и думаю".split()
    assert [w["word"] for w in merged if w["speaker"] == "B"] == ["ки", "передач"]
    # Совпавшие слова — со временем whisper, вставленные — между соседями.
    by = {w["word"]: w for w in merged}
    assert by["вот"]["start"] == 1.4
    assert by["это"]["end"] <= by["смотрю"]["start"] < by["и"]["start"]
    assert by["думаю"]["start"] >= by["и"]["end"] - 1e-9


def test_merge_keeps_whisper_words_outside_utterances_and_drops_misheard_inside():
    whisper = _w("привет мир", 0.0) + _w("кривое", 5.0) + _w("хвост", 9.0)
    utterances = [{"s": "A", "start": 0.0, "end": 1.0, "text": "привет мир"},
                  {"s": "B", "start": 4.8, "end": 5.6, "text": "красивое"}]
    words = [w["word"] for w in merge_voices(whisper, utterances)]
    assert words == ["привет", "мир", "красивое", "хвост"]


def test_lines_split_on_voice_change():
    words = [{"word": "ну", "start": 0.0, "end": 0.3, "speaker": "A"},
             {"word": "да", "start": 0.3, "end": 0.6, "speaker": "A"},
             {"word": "привет", "start": 0.6, "end": 0.9, "speaker": "B"}]
    lines = lines_from_words(words)
    assert [(l["text"], l["speaker"]) for l in lines] == [("ну да", "A"), ("привет", "B")]


def test_parse_utterances_folds_unknown_speaker_and_skips_garbage():
    speakers, utt = parse_utterances(
        '{"speakers": {"A": "стример", "Z": "?"}, "utterances": ['
        '{"s": "Z", "start": 1, "end": 2, "text": "раз"}, {"s": "A", "start": 0, "end": 1, "text": "ноль"},'
        '{"s": "A", "start": "x", "end": 1, "text": "мусор"}]}'
    )
    assert speakers == {"A": "стример"}
    assert [(u["s"], u["text"]) for u in utt] == [("A", "ноль"), ("A", "раз")]
    assert parse_utterances("не json") == ({}, [])


class _Store:
    def __init__(self, text="ну что привет всем"):
        self.subs = {"lines": [], "words": _w(text, 0.0)}

    def get_clip_plan_subtitles(self, _id):
        return self.subs

    def set_clip_plan_subtitles(self, _id, payload):
        self.subs = payload


class _Client:
    def __init__(self, answer):
        self.answer, self.calls = answer, 0

    def generate_content(self, model, payload):
        self.calls += 1
        return {"candidates": [{"content": {"parts": [{"text": self.answer}]}}]}


def test_separate_voices_writes_labels_once_and_skips_single_voice(monkeypatch):
    monkeypatch.setattr("app.ai.speakers._clip_audio", lambda store, pid: b"mp3")
    store = _Store()
    client = _Client('{"speakers": {"A": "стример", "B": "гость"}, "utterances": ['
                     '{"s": "A", "start": 0, "end": 0.8, "text": "ну что"},'
                     '{"s": "B", "start": 0.8, "end": 1.6, "text": "привет всем"}]}')
    out = separate_voices(store, 1, client)
    assert out["counts"] == {"A": 2, "B": 2} and out["changed"]
    assert [w["speaker"] for w in store.subs["words"]] == ["A", "A", "B", "B"]
    assert [l["text"] for l in store.subs["lines"]] == ["ну что", "привет всем"]
    # Те же слова — модель второй раз не зовём.
    assert separate_voices(store, 1, client)["changed"] is False and client.calls == 1

    single = _Store()
    separate_voices(single, 1, _Client('{"speakers": {"A": "стример"}, "utterances": ['
                                       '{"s": "A", "start": 0, "end": 1.6, "text": "ну что привет всем"}]}'))
    assert not any("speaker" in w for w in single.subs["words"])


def test_separate_voices_leaves_subtitles_when_model_heard_too_little(monkeypatch):
    monkeypatch.setattr("app.ai.speakers._clip_audio", lambda store, pid: b"mp3")
    store = _Store("раз два три четыре пять шесть семь восемь")
    before = list(store.subs["words"])
    out = separate_voices(store, 1, _Client('{"speakers": {"A": "x"}, "utterances": ['
                                            '{"s": "A", "start": 0, "end": 1, "text": "раз"}]}'))
    assert out["changed"] is False and "warning" in out and store.subs["words"] == before


def test_caption_words_carry_speaker():
    from app.motion import caption_words

    words = caption_words({"words": [{"word": "да", "start": 1, "end": 2, "speaker": "B"},
                                     {"word": "нет", "start": 2, "end": 3}]})
    assert words == [{"t0": 1.0, "t1": 2.0, "w": "да", "s": "B"}, {"t0": 2.0, "t1": 3.0, "w": "нет"}]
