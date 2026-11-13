"""Субтитры стрима — только стример и живые люди (владелец 13.11, нарезка Муры: субтитры на репликах агентов Valorant).
Голоса игры и ролика separate_voices убирает сам; дорожка микрофона записи находится по названию и распознаётся одна."""

from app.ai import speakers as S
from app.ai.speakers import not_live_voices, separate_voices
from app import source_audio as SA


def _w(text: str, start: float, step: float = 0.4):
    return [{"word": t, "start": round(start + i * step, 2), "end": round(start + i * step + 0.3, 2)}
            for i, t in enumerate(text.split())]


class _Store:
    def __init__(self, words):
        self.subs = {"lines": [], "words": words}

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


ANSWER = ('{"speakers": {"A": "стример", "B": "человек: тиммейт", "C": "игра: голос агента"}, "utterances": ['
          '{"s": "A", "start": 0, "end": 1.2, "text": "ну что пошли"},'
          '{"s": "C", "start": 1.2, "end": 2.4, "text": "летят лимоны внимание"},'
          '{"s": "B", "start": 2.4, "end": 3.6, "text": "я справа стою"}]}')


def test_game_voice_is_dropped_from_subtitles_teammate_stays(monkeypatch):
    monkeypatch.setattr("app.ai.speakers._clip_audio", lambda store, pid: b"mp3")
    store = _Store(_w("ну что пошли", 0.0) + _w("летят лимоны внимание", 1.2) + _w("я справа стою", 2.4))
    out = separate_voices(store, 1, _Client(ANSWER))
    text = " ".join(w["word"] for w in store.subs["words"])
    assert "лимоны" not in text and "пошли" in text and "справа" in text
    assert set(out["dropped"]) == {"C"} and "агента" in out["dropped"]["C"]["about"]
    assert "C" not in out["speakers"] and out["counts"] == {"A": 3, "B": 3}
    assert all("лимоны" not in line["text"] for line in store.subs["lines"])
    # По просьбе владельца — все голоса обратно.
    back = separate_voices(store, 1, _Client(ANSWER), all_voices=True)
    assert "dropped" not in back and "лимоны" in " ".join(w["word"] for w in store.subs["words"])


def test_only_game_left_besides_streamer_removes_labels(monkeypatch):
    monkeypatch.setattr("app.ai.speakers._clip_audio", lambda store, pid: b"mp3")
    answer = ('{"speakers": {"A": "стример", "B": "диктор игры"}, "utterances": ['
              '{"s": "A", "start": 0, "end": 1.2, "text": "ну что пошли"},'
              '{"s": "B", "start": 1.2, "end": 2.4, "text": "раунд начинается сейчас"}]}')
    store = _Store(_w("ну что пошли", 0.0) + _w("раунд начинается сейчас", 1.2))
    out = separate_voices(store, 1, _Client(answer))
    assert set(out["dropped"]) == {"B"}
    assert [w["word"] for w in store.subs["words"]] == ["ну", "что", "пошли"]
    assert not any("speaker" in w for w in store.subs["words"])


def test_not_live_voices_rules():
    speakers = {"A": "голос из игры", "B": "озвучка доната (tts)", "C": "персонаж игры", "D": "гость в войсе"}
    assert set(not_live_voices(speakers)) == {"C"}  # стример A — никогда; TTS чата по плейбуку остаётся с «чат:»
    # Отдельный микрофон: голос, которого whisper микрофона не слышал, и не названный человеком — не из микрофона.
    utt = [{"s": "B", "start": 0, "end": 2, "text": "раз два три четыре"},
           {"s": "C", "start": 2, "end": 4, "text": "пять шесть семь восемь"}]
    mic = _w("раз два три четыре", 0.0, 0.5)
    got = not_live_voices({"A": "стример", "B": "голос", "C": "кто-то"}, utt, mic)
    assert set(got) == {"C"} and "микрофона" in got["C"]


def test_mic_track_found_by_stream_titles_and_used_for_speech():
    meta = {"streams": [{"codec_type": "video"},
                        {"codec_type": "audio", "tags": {"title": "Game"}},
                        {"codec_type": "audio", "tags": {"title": "Microphone"}}]}
    source = {"metadata_json": meta, "audio_tracks": [0, 1]}
    assert SA.mic_track(source) == 1
    assert SA.speech_mix(source) == [{"track": 1, "gain_db": 0.0}]
    assert SA.separate_speech(source) is True
    # Заданное владельцем speech_tracks важнее догадки; одна дорожка или без названий — не гадаем.
    assert SA.speech_mix({**source, "speech_tracks": [0]}) == [{"track": 0, "gain_db": 0.0}]
    plain = {"metadata_json": {"streams": [{"codec_type": "audio"}, {"codec_type": "audio"}]}}
    assert SA.mic_track(plain) is None
    assert S.MIC_HEARD_SHARE < 0.5
