"""auto_focus по репликам: длинный план, где двое говорят по очереди, — кадр на реплику и
плавный поворот окна к ответившему (проверочный прогон «Рик и Морти»: окно стояло между
героями, в кадре рука Рика). Gemini и OpenCV не нужны — логика в app/focus_replies."""

from __future__ import annotations

from app.focus_replies import apply_answers, plan_units, split_replies


def _w(word, start, end, speaker=None):
    item = {"word": word, "start": start, "end": end}
    if speaker:
        item["speaker"] = speaker
    return item


DIALOGUE = [  # план 0..8 с: реплика Рика, пауза 0.9 с, ответ Морти, пауза, снова Рик
    _w("Морти,", 0.2, 0.6), _w("нам", 0.7, 0.9), _w("пора.", 1.0, 1.6),
    _w("Куда", 2.5, 2.8), _w("опять,", 2.9, 3.3), _w("Рик?", 3.4, 3.9),
    _w("В", 4.8, 4.9), _w("космос,", 5.0, 5.6), _w("Морти!", 5.7, 6.3),
]


def test_long_shot_splits_on_pauses():
    replies = split_replies(DIALOGUE, 0.0, 8.0)
    assert [r["text"] for r in replies] == ["Морти, нам пора.", "Куда опять, Рик?", "В космос, Морти!"]
    assert replies[1]["start"] == 2.5 and replies[1]["end"] == 3.9


def test_short_shot_or_single_phrase_stays_whole():
    assert split_replies(DIALOGUE, 0.0, 2.5) == []  # короче 3 с — один центр, как раньше
    assert split_replies(DIALOGUE[:3], 0.0, 8.0) == []  # одна фраза
    assert split_replies([], 0.0, 8.0) == []  # без субтитров


def test_speaker_change_splits_without_pause_and_one_voice_does_not():
    words = [_w("Ты", 0.2, 0.4, "A"), _w("где?", 0.45, 1.2, "A"), _w("Тут", 1.3, 1.6, "B"), _w("я.", 1.65, 2.4, "B")]
    replies = split_replies(words, 0.0, 4.0)
    assert [(r["text"], r["speaker"]) for r in replies] == [("Ты где?", "A"), ("Тут я.", "B")]
    # Все слова одним голосом — говорящий один, план не делим даже по паузам.
    one = [{**w, "speaker": "A"} for w in DIALOGUE]
    assert split_replies(one, 0.0, 8.0) == []


def test_tiny_reply_sticks_to_neighbour():
    words = [*DIALOGUE[:3], _w("Ага.", 2.3, 2.5), *DIALOGUE[6:]]  # «Ага» 0.2 с — камера не дёргается
    replies = split_replies(words, 0.0, 8.0)
    assert len(replies) == 2 and replies[0]["text"].endswith("Ага.")


def test_units_respect_frame_limit():
    shots = [(0.0, 8.0), (8.0, 10.0)]
    units = plan_units(shots, DIALOGUE, 24)
    assert [(u["shot"], u["reply"]) for u in units] == [(0, True), (0, True), (0, True), (1, False)]
    assert units[1]["hint"] == "Куда опять, Рик?"
    # Лимит кадров: каждому плану по кадру, реплики не влезли — план целиком одним кадром.
    tight = plan_units(shots, DIALOGUE, 3)
    assert [(u["shot"], u["reply"]) for u in tight] == [(0, False), (1, False)]
    assert "пора" in tight[0]["hint"]
    # Без субтитров — кадр на план, как раньше.
    assert [u["reply"] for u in plan_units(shots, None, 24)] == [False, False]


def _points():
    # Сэмплы OpenCV: план 0 (0..8 с), склейка в 8 с, план 1.
    pts = [{"t": t / 2, "x": 0.55, "y": 0.4} for t in range(16)]
    pts += [{"t": 8.0, "x": 0.3, "y": 0.5, "cut": True}, {"t": 9.0, "x": 0.3, "y": 0.5}]
    return pts


def test_answers_become_turns_inside_shot_and_cut_on_splice():
    points = _points()
    ranges = [(0, 16), (16, 18)]
    units = plan_units([(0.0, 8.0), (8.0, 10.0)], DIALOGUE, 24)
    out = apply_answers(points, ranges, units, [0.72, 0.4, 0.72, 0.25])
    first = [p for p in out if p["t"] < 8.0]
    # Рик справа → поворот к Морти слева к началу его реплики (2.5 с) за 0.35 с → снова Рик.
    assert [(p["t"], p["x"]) for p in first] == [(0.0, 0.72), (2.15, 0.72), (2.5, 0.4), (4.45, 0.4), (4.8, 0.72)]
    assert not any(p.get("cut") for p in first)  # внутри плана — плавно, не cut
    second = [p for p in out if p["t"] >= 8.0]
    assert second[0]["cut"] and all(p["x"] == 0.25 for p in second)


def test_same_speaker_answers_merge_and_missing_answer_keeps_cv():
    points = _points()
    ranges = [(0, 16), (16, 18)]
    units = plan_units([(0.0, 8.0), (8.0, 10.0)], DIALOGUE, 24)
    # Gemini везде ответил «тот же герой» — один центр на план, как раньше.
    same = apply_answers(points, ranges, units, [0.7, 0.72, 0.69, None])
    assert [p["x"] for p in same if p["t"] < 8.0] == [0.7]
    assert [p["x"] for p in same if p["t"] >= 8.0] == [0.3, 0.3]  # план без ответа — OpenCV
    # Реплика без ответа держит x плана из OpenCV.
    gap = apply_answers(points, ranges, units, [0.72, None, 0.72, 0.25])
    assert [p["x"] for p in gap if p["t"] < 8.0] == [0.72, 0.72, 0.55, 0.55, 0.72]
