"""Серия 7, «опять лаги в озвучке» и «витУбику»: паузы-«биты» режутся в тишине с наплывом (раньше — по времени
Whisper посреди слова), проверка звука ловит оборванное слово, стык не в тишине, провал голоса и рассинхрон;
переозвучка одной фразы вклеивается в прошлый дубль в тишине; ударение «Витубик» — в словаре произношения;
скрутка рук из проверок VRM — понятной причиной."""

import json

import numpy as np
import pytest

from app import vtubika_audio as A
from app import vtubika_revoice as R
from app import vtubika_say as SAY
from app import vtubika_vrm as VRM

SR = A.SR


def _tone(sec, f=180.0, amp=0.3):
    t = np.arange(int(sec * SR)) / SR
    # «Речь»: тон с огибающей слогов 4 Гц (внутри «слова» не уходит в ноль), фраза затухает и нарастает за 80 мс.
    env = np.minimum(1.0, np.minimum(t, sec - t) / 0.08)
    return (amp * env * np.sin(2 * np.pi * f * t) * (0.6 + 0.4 * np.abs(np.sin(2 * np.pi * 4 * t)))).astype(np.float32)


def _sil(sec):
    """Пауза голоса: шум комнаты около −60 дБ (не «цифровая тишина»)."""
    n = int(sec * SR)
    return (np.float32(1e-3) * np.random.default_rng(1).standard_normal(n)).astype(np.float32)


def _speech():
    """Две «фразы»: 1.0 с речи, 0.3 с тишины, 1.0 с речи."""
    return np.concatenate([_sil(0.2), _tone(1.0), _sil(0.3), _tone(1.0, 220), _sil(0.2)])


def test_quiet_point_finds_the_pause_not_the_whisper_midpoint():
    pcm = _speech()
    # Whisper ошибся: «граница» на 1.0 с (внутри первой фразы), окно поиска захватывает паузу 1.2–1.5 с.
    t = A.quiet_point(pcm, 0.8, 1.7)
    assert 1.25 < t < 1.45
    assert A.join_levels(pcm, [t])[0]["ok"]
    assert not A.join_levels(pcm, [0.7])[0]["ok"]  # посреди «слова» — не тишина


def test_insert_pauses_in_silence_has_no_word_cuts_but_cut_in_word_is_caught():
    pcm = _speech()
    good = A.insert_pauses(pcm, [(A.quiet_point(pcm, 0.8, 1.7), 0.3)])
    assert len(good) == len(pcm) + int(0.3 * SR)
    assert A.word_cuts(good) == []
    # Так резал старый шаг пауз: тишина посреди слова, без наплыва — слово оборвано.
    bad = np.concatenate([pcm[: int(0.7 * SR)], np.zeros(int(0.3 * SR), dtype=np.float32), pcm[int(0.7 * SR):]])
    cuts = A.word_cuts(bad)
    assert cuts and abs(cuts[0]["t"] - 0.7) < 0.02


def test_insert_pauses_fades_edges():
    pcm = _tone(1.0)
    out = A.insert_pauses(pcm, [(0.5, 0.2)])
    k = int(0.5 * SR)
    assert abs(out[k - 1]) < 0.02 and abs(out[k + int(0.2 * SR)]) < 0.02  # к нулям и от нулей — наплыв, не ступенька


def test_voice_in_mix_dropout_and_sync():
    voice = _speech()
    lead = 0.2
    mix = np.concatenate([np.zeros(int(lead * SR), dtype=np.float32), voice * 1.5]) + _sil(lead + len(voice) / SR)[: int(lead * SR) + len(voice)]
    ok = A.check(voice, mix, lead)
    assert ok["ok"], ok["problems"]
    assert all(abs(s["ms"]) <= A.SYNC_MAX_MS for s in ok["sync"])
    hole = mix.copy()
    hole[int((lead + 0.6) * SR): int((lead + 0.8) * SR)] *= 0.01  # пропал кусок слова в сведении
    res = A.check(voice, hole, lead)
    assert not res["ok"] and res["dropouts"] and abs(res["dropouts"][0]["t"] - 0.8) < 0.05
    late = np.concatenate([np.zeros(int(0.1 * SR), dtype=np.float32), mix])  # голос уехал на 100 мс
    assert any("рассинхрон" in p for p in A.check(voice, late, lead)["problems"])


def test_check_fails_on_empty_and_bad_joins():
    voice = _speech()
    res = A.check(voice[: SR // 2], voice[: SR // 4], 0.0)
    assert not res["ok"]
    bad_join = [{"t": 0.7, "before": -3.0, "after": -2.0, "ok": False}]
    assert any("стык паузы" in p for p in A.check(voice, voice, 0.0, joins=bad_join)["problems"])


def test_stress_mark_in_pronounce_dictionary():
    mapping = SAY.pronounce_map({})
    say = SAY.apply("Однажды ночью ты открываешь Витубику. Скидка на Витубике.", mapping)
    assert "Виту́бику" in say and "Виту́бике" in say
    # Whisper знак не пишет: слово сценария встаёт на своё время, с окончанием.
    heard = SAY.heard_as_script([{"word": "Витубику.", "start": 1.0, "end": 1.6}], mapping)
    assert heard[0]["word"].startswith("Витубику") and heard[0]["start"] == 1.0


def test_revoice_needs_only_changed_or_flagged_sentences():
    plan = {"sentences": [{"text": "a"}, {"text": "b", "revoice": True}, {"text": "c"}, {"text": "d"}]}
    base = ["раз", "Витубику", "на Витубике", "четыре"]
    new = ["раз", "Виту́бику", "на Виту́бике", "четыре!"]
    # Только ударение — фраза из прошлого дубля (прочитана верно); флаг revoice и другой текст — заново.
    assert R.needs(plan, base, new) == [1, 3]
    assert R.needs(plan, base[:3], new) is None


def test_find_base_and_splice_keep_timing(tmp_path):
    voice = {"provider": "polza", "voice": "will"}
    plan = {"sentences": [{"text": "раз два"}, {"text": "три", "revoice": True}, {"text": "четыре"}]}
    (tmp_path / "narration_aaa.wav").write_bytes(b"x")
    (tmp_path / "narration_aaa.json").write_text(json.dumps({"voice_spec": voice, "sentences": ["раз два", "три", "четыре"]}))
    (tmp_path / "narration_paced_bbb.json").write_text("[]")
    raw, meta, todo = R.find_base(plan, tmp_path, voice, ["раз два", "три", "четыре"])
    assert raw.name == "narration_aaa.wav" and todo == [1]
    assert R.find_base({**plan, "voice": {"patch": False}}, tmp_path, voice, ["раз два", "три", "четыре"]) is None
    assert R.find_base(plan, tmp_path, {**voice, "voice": "lily"}, ["раз два", "три", "четыре"]) is None
    # Склейка: фраза 2 (1.5–2.5 с) заменяется новой той же длины ±10 % — длина дубля почти не меняется, стыки в тишине.
    pcm = np.concatenate([_sil(0.2), _tone(1.0), _sil(0.3), _tone(1.0, 220), _sil(0.3), _tone(1.0, 260), _sil(0.2)])
    spans = [(0.2, 1.2), (1.5, 2.5), (2.8, 3.8)]
    line = np.concatenate([_sil(0.15), _tone(1.05, 300, 0.2), _sil(0.25)])
    out, info = R.splice(pcm, spans, {1: line}, sr=SR, work=tmp_path)
    assert abs(len(out) - len(pcm)) / SR < 0.06
    assert all(j["ok"] for j in info[0]["joins"])
    assert A.word_cuts(out) == []
    assert info[0]["tempo"] != 1.0  # подогнана по длине


def test_twist_problems_reason():
    assert VRM.twist_problems({"twist": {"frames": 10, "bad": 0}}) == []
    assert VRM.twist_problems({}) == ["скрутка рук: нет замеров"]
    bad = {"twist": {"frames": 10, "bad": 3, "first": [{"t": 9.0, "side": "left", "what": "wrist", "value": -167.9}]}}
    probs = VRM.twist_problems(bad)
    assert "3 нарушений" in probs[0] and "left wrist -167.9" in probs[1]


@pytest.mark.parametrize("name", ["lyosha"])
def test_short_armed_hero_pulls_hands_closer(name):
    from app import vtubika_vrm_stage as VS

    her = VS.HEROINES[name]
    # Руки мужской базы после масштаба короче, стол и кресло — героинь: запястья ближе, иначе рука выпрямлена.
    assert her["scale"] < 1 and 0.02 <= her["keys_pull"] <= 0.05
