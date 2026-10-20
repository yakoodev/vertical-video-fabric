"""Разметка голосов в субтитрах: разбор ответа модели и запись меток в слова."""

from app.ai.speakers import label_speakers, parse_turns


def test_parse_turns_fills_gaps_and_folds_unknown_speakers():
    text = 'Вот: {"speakers": {"A": "стример", "B": "донат", "Z": "?"}, ' \
           '"turns": [{"from": 0, "to": 2, "s": "A"}, {"from": 5, "to": 6, "s": "B"}, {"from": 7, "to": 7, "s": "Z"}]}'
    speakers, labels = parse_turns(text, 8)
    assert speakers == {"A": "стример", "B": "донат"}
    # 3–4 — дыра: тянем соседнюю метку; Z — незнакомая буква, отдаём главному.
    assert labels == ["A", "A", "A", "A", "A", "B", "B", "A"]
    assert parse_turns("не json", 3) == ({}, ["", "", ""])


class _Store:
    def __init__(self):
        self.subs = {"lines": [], "words": [{"word": w, "start": i * 0.5, "end": i * 0.5 + 0.4}
                                          for i, w in enumerate("ну что привет всем".split())]}

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


def test_label_speakers_writes_labels_once_and_skips_single_voice(monkeypatch):
    monkeypatch.setattr("app.ai.speakers._clip_audio", lambda store, pid: b"mp3")
    store = _Store()
    client = _Client('{"speakers": {"A": "стример", "B": "гость"}, '
                     '"turns": [{"from": 0, "to": 1, "s": "A"}, {"from": 2, "to": 3, "s": "B"}]}')
    out = label_speakers(store, 1, client)
    assert out["counts"] == {"A": 2, "B": 2} and out["changed"]
    assert [w["speaker"] for w in store.subs["words"]] == ["A", "A", "B", "B"]
    # Те же слова — модель второй раз не зовём.
    assert label_speakers(store, 1, client)["changed"] is False and client.calls == 1

    single = _Store()
    label_speakers(single, 1, _Client('{"speakers": {"A": "стример"}, "turns": [{"from": 0, "to": 3, "s": "A"}]}'))
    assert not any("speaker" in w for w in single.subs["words"])


def test_caption_words_carry_speaker():
    from app.motion import caption_words

    words = caption_words({"words": [{"word": "да", "start": 1, "end": 2, "speaker": "B"},
                                     {"word": "нет", "start": 2, "end": 3}]})
    assert words == [{"t0": 1.0, "t1": 2.0, "w": "да", "s": "B"}, {"t0": 2.0, "t1": 3.0, "w": "нет"}]
