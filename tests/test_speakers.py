"""Голоса клипа: сшивка реплик Gemini со словами whisper, строки без смешения голосов."""

import json

import numpy as np

from app.ai import speakers as S
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


def test_unheard_start_of_utterance_goes_before_first_matched_word():
    """Gemini поставил начало реплики позже, чем whisper услышал её середину:
    «Как устроена муха?» не должно стать «муха как устроена»."""
    whisper = [{"word": "муха", "start": 11.9, "end": 12.2}, {"word": "да", "start": 12.4, "end": 12.6}]
    merged = merge_voices(whisper, [{"s": "A", "start": 12.2, "end": 13.0, "text": "Как устроена муха? Да"}])
    assert [w["word"] for w in merged] == ["Как", "устроена", "муха?", "Да"]
    assert merged[1]["end"] <= 11.9 + 1e-9


def test_assign_voice_moves_words_and_rebuilds_lines():
    from app.ai.speakers import assign_voice

    store = _Store("ну что привет всем")  # слова в 0.0, 0.4, 0.8, 1.2
    out = assign_voice(store, 1, 0.8, 2.0, "B", "донат")
    assert out["moved"] == 2 and out["speakers"]["B"] == "донат"
    assert [l["text"] for l in store.subs["lines"]] == ["ну что", "привет всем"]
    assert [w["speaker"] for w in store.subs["words"]] == ["A", "A", "B", "B"]
    # Вернули всё главному — меток нет.
    assign_voice(store, 1, 0.0, 9.0, "A")
    assert not any("speaker" in w for w in store.subs["words"])


def _speech(count: int, start: float = 0.0, step: float = 0.5) -> list[dict]:
    """Разные слова подряд: «с0 с1 с2 …», повторов нет."""
    return [{"word": f"с{start:.0f}x{i}", "start": round(start + i * step, 2), "end": round(start + i * step + 0.4, 2)}
            for i in range(count)]


def _utter(s: str, words: list[dict], shift: float = 0.0) -> dict:
    return {"s": s, "start": words[0]["start"] + shift, "end": words[-1]["end"] + shift,
            "text": " ".join(w["word"] for w in words)}


def _coded(u: dict) -> dict:
    """Как пишет модель: 1:23.5 → 123.5."""
    def code(v):
        return int(v // 60) * 100 + v % 60
    return {**u, "start": code(u["start"]), "end": code(u["end"])}


def test_minute_notation_is_undone_by_whisper_words():
    # Клип Муры: «Потап, ты где» у whisper на 79.9 с, у модели на 119.6 — это 1:19.6.
    whisper = _speech(12, 10.0) + _speech(12, 80.0) + _speech(12, 150.0)
    utterances = [_coded(_utter("A", whisper[0:12])), _coded(_utter("B", whisper[12:24])), _coded(_utter("A", whisper[24:36]))]
    assert utterances[1]["start"] == 120.0 and utterances[2]["start"] == 230.0
    fixed, facts = S.fit_times(utterances, whisper, 170.0)
    assert facts["minutes"] is True
    assert [round(u["start"]) for u in fixed] == [10, 80, 150]
    words = merge_voices(whisper, fixed)
    assert len(words) == 36 and max(w["end"] for w in words) < 170


def test_minute_notation_in_short_clip():
    # Так бывает и на 70 с (source 337, 2:35): модель поставила реплики на 100.3 и 106.7 — это 1:00.3 и 1:06.7.
    utterances = [{"s": "A", "start": 57.8, "end": 59.5, "text": "вот сейчас я скажу"},
                  {"s": "B", "start": 100.3, "end": 101.8, "text": "ну и что дальше будет"},
                  {"s": "A", "start": 106.7, "end": 108.0, "text": "пошли копать прямо сейчас"}]
    whisper = _w("вот сейчас я скажу", 57.8) + _w("ну и что дальше будет", 60.3) + _w("пошли копать прямо сейчас", 66.7)
    fixed, facts = S.fit_times(utterances, whisper, 70.0)
    assert facts["minutes"] is True and max(u["end"] for u in fixed) <= 70.0
    assert [round(u["start"], 1) for u in fixed] == [57.8, 60.3, 66.7]


def test_correct_seconds_stay_untouched():
    whisper = _speech(20, 70.0)
    utterances = [_utter("A", whisper[:10]), _utter("B", whisper[10:])]
    fixed, facts = S.fit_times(utterances, whisper, 90.0)
    assert fixed == utterances and facts == {"minutes": False, "moved": 0, "dropped": 0}


def test_phrase_whisper_lost_keeps_its_time():
    # Фраза под чужим голосом: whisper её не слышал, сдвигать не за что.
    whisper = _speech(10, 5.0)
    lost = {"s": "B", "start": 6.0, "end": 7.0, "text": "этих слов нет у whisper"}
    fixed, facts = S.fit_times([_utter("A", whisper), lost], whisper, 20.0)
    assert lost in fixed and facts["moved"] == 0


def test_drifted_utterance_moves_to_words_and_beyond_end_is_dropped():
    whisper = _speech(12, 40.0)
    fixed, facts = S.fit_times([_utter("A", whisper, shift=7.0)], whisper, 60.0)
    assert facts["moved"] == 1 and round(fixed[0]["start"]) == 40
    gone, facts = S.fit_times([{"s": "A", "start": 90.0, "end": 92.0, "text": "нигде этого нет"}], whisper, 60.0)
    assert gone == [] and facts["dropped"] == 1


def test_parse_accepts_time_as_string():
    _about, utt = parse_utterances('{"utterances": [{"s": "A", "start": "1:23.5", "end": "1:24", "text": "да"}]}')
    assert (utt[0]["start"], utt[0]["end"]) == (83.5, 84.0)


def test_windows_cut_on_pause_and_cover_clip():
    words = _speech(400, 0.0, step=0.5)
    for w in words[280:]:  # пауза 3 с после 140 с — граница встанет в неё
        w["start"] += 3.0
        w["end"] += 3.0
    windows = S.plan_windows(words, 300.0)
    assert len(windows) == 2 and windows[0]["start"] == 0.0 and windows[-1]["end"] == 300.0
    assert 139.9 <= windows[0]["keep_to"] <= 143.0
    assert windows[0]["keep_to"] == windows[1]["keep_from"]
    assert windows[1]["start"] < windows[0]["end"]  # перекрытие
    assert all(w["end"] - w["start"] <= S.WINDOW_SEC + S.WINDOW_OVERLAP for w in windows)
    assert S.plan_windows(words, 100.0) == [{"start": 0.0, "end": 100.0, "keep_from": 0.0, "keep_to": 100.0}]


def test_lines_of_overlapping_voices_stay_whole():
    words = [{"word": "раз", "start": 0.0, "end": 0.4, "speaker": "A"}, {"word": "мать", "start": 0.1, "end": 0.5, "speaker": "B"},
             {"word": "два", "start": 0.42, "end": 0.8, "speaker": "A"}, {"word": "нас", "start": 0.5, "end": 0.9, "speaker": "B"},
             {"word": "три", "start": 0.82, "end": 1.2, "speaker": "A"}, {"word": "бросила", "start": 0.9, "end": 1.3, "speaker": "B"}]
    assert [(l["speaker"], l["text"]) for l in lines_from_words(words)] == [("A", "раз два три"), ("B", "мать нас бросила")]


def test_line_breaks_when_other_voice_cuts_in():
    words = [{"word": "раз", "start": 0.0, "end": 0.4, "speaker": "A"}, {"word": "да", "start": 0.42, "end": 0.6, "speaker": "B"},
             {"word": "два", "start": 0.62, "end": 1.0, "speaker": "A"}]
    assert [(l["speaker"], l["text"]) for l in lines_from_words(words)] == [("A", "раз"), ("B", "да"), ("A", "два")]


class _WindowClient:
    def __init__(self, answers):
        self.answers, self.calls = answers, 0

    def generate_content(self, model, payload):
        self.calls += 1
        return {"candidates": [{"content": {"parts": [{"text": json.dumps(self.answers[self.calls - 1], ensure_ascii=False)}]}}]}


def _phrases(words: list[dict], s: str, start: float) -> list[dict]:
    """Короткие реплики окна — со временем от начала окна, как отвечает модель."""
    local = [{**w, "start": w["start"] - start, "end": w["end"] - start} for w in words]
    return [_utter(s, local[i:i + 8]) for i in range(0, len(local), 8)]


def test_long_clip_is_heard_by_windows_with_one_letter_per_voice(monkeypatch):
    monkeypatch.setattr("app.ai.speakers._clip_audio", lambda store, pid: b"mp3")
    monkeypatch.setattr("app.ai.speakers._audio_piece", lambda audio, a, b: f"{a:.0f}-{b:.0f}".encode())
    monkeypatch.setattr("app.ai.speakers._voiceprints", lambda store, pid: None)
    mura = _speech(280, 0.0)
    guest = _speech(280, 145.0)  # после 140 с говорит гость
    store = _Store()
    store.subs = {"lines": [], "words": mura + guest}
    windows = S.plan_windows(mura + guest, 290.0)
    assert len(windows) == 2
    start = windows[1]["start"]
    one = [w for w in mura + guest if w["start"] < windows[0]["end"]]
    two = [w for w in mura + guest if w["start"] >= start]
    # Первое окно: Мура A, гость B. Второе: модель поменяла буквы, гость A, остаток Муры B.
    answer_one = {"speakers": {"A": "Мура", "B": "гость"},
                  "utterances": _phrases([w for w in one if w in mura], "A", 0.0) + _phrases([w for w in one if w in guest], "B", 0.0)}
    answer_two = {"speakers": {"A": "гость", "B": "Мура"},
                  "utterances": _phrases([w for w in two if w in guest], "A", start) + _phrases([w for w in two if w in mura], "B", start)}
    client = _WindowClient([answer_one, answer_two])
    out = separate_voices(store, 1, client)
    assert client.calls == 2 and out["windows"] == 2
    words = store.subs["words"]
    # Перекрытие не удвоило слова: каждая реплика осталась в одном окне.
    assert sorted(w["word"] for w in words) == sorted(w["word"] for w in mura + guest)
    letter = {w["word"]: w["speaker"] for w in words}
    assert {letter[w["word"]] for w in mura} == {"A"} or {letter[w["word"]] for w in mura} == {"B"}
    assert {letter[w["word"]] for w in guest} == {"A", "B"} - {letter[mura[0]["word"]]}
    assert set(store.subs["speakers"]) == {"A", "B"}


def test_long_clip_uses_voiceprints_when_overlap_says_nothing(monkeypatch):
    # Перекрытие без общих слов: буквы сводят отпечатки голосов.
    monkeypatch.setattr("app.ai.speakers._clip_audio", lambda store, pid: b"mp3")
    monkeypatch.setattr("app.ai.speakers._audio_piece", lambda audio, a, b: b"x")
    first = _speech(300, 0.0)
    second = _speech(300, 150.0)
    vectors = {"мура": np.array([1.0, 0.0]), "гость": np.array([0.0, 1.0])}
    seen = []

    def prints(words):
        seen.append(1)
        out = {}
        for w in words:
            person = "мура" if w["start"] < 150 else "гость"
            out[w["speaker"]] = vectors[person]
        return out

    monkeypatch.setattr("app.ai.speakers._voiceprints", lambda store, pid: prints)
    book = S._LetterBook(prints)
    assert book.assign([{"word": "а", "start": 1.0, "end": 2.0, "speaker": "A"}]) == {"A": "A"}
    # Во втором окне гость идёт буквой A, но отпечаток другой — ему новая буква.
    assert book.assign([{"word": "б", "start": 200.0, "end": 201.0, "speaker": "A"}]) == {"A": "B"}
    # Та же Мура буквой C — вернулась на A.
    assert book.assign([{"word": "в", "start": 2.0, "end": 3.0, "speaker": "C"}]) == {"C": "A"}
    assert seen


def test_letter_book_keeps_weak_bot_voice_on_its_letter():
    # Озвучка чата похожа на себя всего на 0.5, чужие голоса ниже — буква та же.
    vectors = {"A": np.array([1.0, 0.0, 0.0]), "C": np.array([0.0, 1.0, 0.0]), "new": np.array([0.0, 0.5, 0.866])}
    plan = iter([{"A": vectors["A"], "C": vectors["C"]}, {"C": vectors["new"]}])
    book = S._LetterBook(lambda words: next(plan))
    book.assign([{"word": "а", "start": 0.0, "end": 1.0, "speaker": "A"}, {"word": "б", "start": 1.0, "end": 2.0, "speaker": "C"}])
    assert book.assign([{"word": "в", "start": 9.0, "end": 10.0, "speaker": "C"}]) == {"C": "C"}


def test_answer_with_times_from_nowhere_changes_nothing(monkeypatch):
    # Слов модели хватает, но ни одно не совпало со словами whisper — субтитры не трогаем.
    monkeypatch.setattr("app.ai.speakers._clip_audio", lambda store, pid: b"mp3")
    store = _Store("раз два три четыре пять шесть")
    before = list(store.subs["words"])
    answer = {"speakers": {"A": "x"}, "utterances": [{"s": "A", "start": 0, "end": 2, "text": "совсем другие слова из другого звука"}]}
    out = separate_voices(store, 1, _WindowClient([answer]))
    assert out["changed"] is False and "warning" in out and store.subs["words"] == before
