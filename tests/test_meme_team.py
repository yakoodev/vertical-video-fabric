"""Шум вместо тиммейтов (app/meme_edit_team.py): Whisper на дорожке игры «слышит» речь в выстрелах («В ЛОВЫЙ ГОЛОВ
А») — такие реплики выбрасываются по уверенности декодера, словарю и наложению на микрофон; настоящие («Красавцы!»,
«Банга, я пушу», «На мне все») остаются. Уверенность сегментов доходит из Whisper (segment_stats)."""

from __future__ import annotations

from types import SimpleNamespace

from app import meme_edit_inputs as I
from app import meme_edit_team as T

LEX = T.Lexicon(set("""на мне все всё красавцы голов голова я в а дал голову окном есть тройка там уходите норм
спасибо поживешь понимаю хорошо ладно хотела слишком этой стороны""".split()))


def _line(text: str, start: float, *, lp: float = -0.2, ns: float = 0.05, cr: float = 1.2, p: float = 0.92,
          step: float = 0.3) -> list[dict]:
    return [{"word": w, "start": round(start + i * step, 3), "end": round(start + i * step + step - 0.03, 3),
             "lp": lp, "ns": ns, "cr": cr, "p": p} for i, w in enumerate(text.split())]


def _texts(words: list[dict]) -> list[str]:
    from app.meme_edit_fights import lines_from_words

    return [ln["text"] for ln in lines_from_words(words)]


def test_real_teammate_lines_kept():
    team = _line("Красавцы!", 1.0) + _line("Банга, я пушу.", 5.0) + _line("На мне все.", 9.0) \
        + _line("Голову дал окном.", 14.0, p=0.7)
    kept, dropped = T.filter_team(team, [], lex=LEX)
    assert _texts(kept) == ["Красавцы!", "Банга, я пушу.", "На мне все.", "Голову дал окном."] and dropped == []


def test_garbage_dropped_with_reason():
    team = (_line("В ЛОВЫЙ ГОЛОВ А", 1.0, lp=-0.55, p=0.55)  # незнакомые слова
            + _line("Деноса", 5.0, lp=-0.9, p=0.39)          # неуверенно (окно мусора под перестрелкой)
            + _line("на на на на на на на", 9.0, cr=2.9)     # повторы
            + _line("Ням, стеба.", 14.0, lp=-0.5, ns=0.7, p=0.37)  # не речь
            + _line("Пускового комплекса идут", 20.0, p=0.71))
    kept, dropped = T.filter_team(team, [], lex=LEX)
    why = {d["text"]: d["why"].split(":")[0] for d in dropped}
    assert why["В ЛОВЫЙ ГОЛОВ А"] == "незнакомые слова"
    assert why["Деноса"] == "неуверенно"
    assert why["на на на на на на на"] == "повторы"
    assert why["Ням, стеба."] == "не речь"
    assert "незнакомые слова" in why["Пускового комплекса идут"]  # уверенный, но слов таких нет
    assert kept == []


def test_unknown_words_rule_alone():
    # уверенность хорошая — решает словарь: «ловый» нет ни в словаре, ни в сленге; «голов» — есть (половина)
    kept, dropped = T.filter_team(_line("В ЛОВЫЙ ГОЛОВ А", 1.0, p=0.9), [], lex=LEX)
    assert not kept and dropped[0]["why"] == "незнакомые слова: ловый"
    kept, dropped = T.filter_team(_line("Голову дал, тройка там, уходите скрыжень", 1.0, p=0.9), [], lex=LEX)
    assert kept and not dropped  # одно незнакомое из пяти — реплика настоящая
    # сленг и мат — настоящая речь
    kept, _ = T.filter_team(_line("Ренокнул бангу, лутаю хилки", 1.0), [], lex=LEX)
    assert kept
    # словаря нет — правило пропускается
    kept, _ = T.filter_team(_line("ловый скрыжень голов", 1.0, p=0.9), [], lex=None)
    assert kept


def test_owner_speech_overlap_and_short_scraps():
    owner = _line("Я тебя стёр, слышишь", 2.0)
    team = _line("стёр слышишь", 2.3) + _line("Нок!", 6.0) + _line("Э", 9.0) + _line("Да", 12.0)
    kept, dropped = T.filter_team(team, owner, lex=LEX)
    assert _texts(kept) == ["Нок!", "Да"]
    reasons = {d["text"]: d["why"] for d in dropped}
    assert reasons == {"стёр слышишь": "речь игрока: эхо микрофона", "Э": "обрывок"}
    # поверх игрока, но другие слова: уверенно — настоящий тиммейт (пилот 1: «С этой стороны» поверх «Невозможно…
    # со спины сквад!»), неуверенно — каша голосов
    owner = _line("Невозможно со спины сквад", 20.0)
    team = _line("С этой стороны.", 20.2, p=0.94) + _line("Я ее понимаю", 23.0, p=0.66)
    owner += _line("Пытаюсь пытаюсь пытаюсь", 23.0)
    kept, dropped = T.filter_team(team, owner, lex=LEX)
    assert _texts(kept) == ["С этой стороны."]
    assert [d["why"] for d in dropped] == ["речь игрока: каша голосов"]


def test_words_without_confidence_pass_confidence_rules():
    team = [{"word": "Красавцы!", "start": 1.0, "end": 1.6}]  # старый кэш слов: только текст и время
    kept, dropped = T.filter_team(team, [], lex=LEX)
    assert kept == team and not dropped


def test_lexicon_stems_and_offline(monkeypatch, tmp_path):
    lex = T.Lexicon({"намудрил", "неприятель"})
    assert lex.knows("намудрили") and lex.knows("неприятеля") and not lex.knows("ловый")
    monkeypatch.setattr(T, "wordlist_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "_lexicon", None)
    monkeypatch.setattr(T, "_failed_at", 0.0)
    monkeypatch.setenv("WORDLIST_DOWNLOAD", "0")
    assert T.lexicon() is None  # нет файлов и качать нельзя
    for lang in ("ru", "en"):
        (tmp_path / f"{lang}_50k.txt").write_text("\n".join(f"слово{i} 5" for i in range(30000)), encoding="utf-8")
    monkeypatch.setattr(T, "_failed_at", 0.0)
    got = T.lexicon()
    assert got is not None and got.knows("слово17")


def test_whisper_words_carry_segment_confidence(monkeypatch, tmp_path):
    from app.subtitles import whisper_local as WL

    seg = SimpleNamespace(start=0.0, end=1.0, text=" Красавцы!", avg_logprob=-0.21, no_speech_prob=0.03,
                          compression_ratio=0.9,
                          words=[SimpleNamespace(word=" Красавцы!", start=0.1, end=0.8, probability=0.97)])
    info = SimpleNamespace(language="ru", language_probability=0.99)
    model = SimpleNamespace(transcribe=lambda *a, **k: (iter([seg]), info))
    provider = WL.WhisperSubtitleProvider(model_loader=lambda _size: model)
    result = provider._transcribe(tmp_path / "x.wav", {"language": "ru", "segment_stats": True}, "tiny")
    [stats] = result.response["segmentStats"]
    assert stats["avg_logprob"] == -0.21 and stats["words"][0]["p"] == 0.97 and stats["salvaged"] is False
    plain = provider._transcribe(tmp_path / "x.wav", {"language": "ru"}, "tiny")
    assert "segmentStats" not in plain.response  # обычные субтитры — без лишнего
    monkeypatch.setattr(WL.WhisperSubtitleProvider, "transcribe", lambda self, p, prof, m: provider._transcribe(
        p, prof, m))
    [word] = I._whisper(tmp_path / "x.wav")
    assert word == {"word": "Красавцы!", "start": 0.1, "end": 0.8, "p": 0.97, "lp": -0.21, "ns": 0.03, "cr": 0.9,
                    "sv": False}
