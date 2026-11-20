"""3D-серия, ч.4 «Кто светит сверху»: продолжение с рюкзака под приоткрытым люком, рука-копия с фонариком, голос героя
сверху без губ, переписка с ответом мамы (вставкой во весь кадр), полная темнота, немая концовка — тело падает сквозь
люк, лучи двух фонариков мечутся по кабине. Поля новых дорожек — только у серий, где они есть."""
from __future__ import annotations

import math
import re

from app import series3d_blender as B
from app.series3d_blender_direction import chat_screen, dialog_lines, has_hero_lines, speech_labels
from app.series3d_blender_perform import LID_AJAR, LID_OPEN
from app.series3d_blender_plan import beat_plan


def _words(sentences: list[str]) -> tuple[list[dict], float]:
    out, t = [], 0.05
    for s in sentences:
        for w in s.split():
            d = 0.1 + 0.055 * len(re.sub(r"[^\wё]", "", w))
            out.append({"word": w, "start": round(t, 3), "end": round(t + d, 3)})
            t += d + 0.05
        t += 0.35
    return out, t + 1.5


LINES = [
    ("Если в блэкаут вам светят в люк вашим же фонариком, не смотрите в свет.",
     "на рюкзаке, люк приоткрыт, сверху фонарик; смотрит вверх, страх. План: лицо"),
    ("Крышка люка медленно поднялась.", "смотрит вверх, страх. План: деталь, люк"),
    ("Он посветил в ответ.", "светит вверх, страх. План: деталь, люк"),
    ("Сверху была только рука.", None),
    ("Сверху позвал будто он сам: «Даня, давай руку. Я уже выбрался».", "слушает, страх. План: лицо"),
    ("Телефон на две секунды поймал сеть.", "телефон, тревога. План: деталь"),
    ("Пришло сообщение от мамы: «Ты где? Ты же написал, что уже дома».", None),
    ("Он открыл переписку, а там его сообщение «Мам, всё ок, я дома», отправлено в 23:41.", "телефон, страх. План: деталь"),
    ("Он его не писал.", "смотрит вверх, страх. План: лицо"),
    ("Заряд 9%.", "вздрагивает, страх. План: лицо"),
    ("Кабина дёрнулась вниз, и свет пропал.", None),
]
OUTRO = [{"text": "Тот, кто был сверху, рухнул сквозь люк в кабину.", "sec": 2.8, "plan": "светит вверх, страх. План: деталь, люк"},
         {"text": "Заметались лучи двух фонариков по стенам.", "sec": 3.6, "plan": "светит, страх. План: сверху"}]


def _beats() -> list[dict]:
    return [{"i": i, "text": t, "sentence": i, "spec": {"action": b}} for i, (t, b) in enumerate(LINES) if b]


def _job() -> tuple[dict, dict]:
    script = {"title": "Кто светит сверху", "sentences": [{"text": t} for t, _ in LINES], "outro": OUTRO}
    words, total = _words([t for t, _ in LINES])
    job = B.build_job(script, words, total, gemini=False, beats=_beats(), render_backend="threejs")
    return job, B.perform(job)


def test_voice_from_above_is_his_voice_without_lips():
    text = "Сверху позвал будто он сам: «Даня, давай руку. Я уже выбрался»."
    labs = speech_labels(text)
    assert set(labs[5:]) == {"mimic"} and set(labs[:5]) == {"narrator"}
    assert set(speech_labels("А сверху его же голосом: «Открой люк»."))  >= {"mimic"}
    assert "hero" in speech_labels("Он крикнул: «Назовите номер вашей бригады»."), "сам герой в кабине — его губы"
    assert "other" in speech_labels("Голос сверху сказал: «Открой люк, я тебя вытащу»."), "чужой голос — рассказчик"
    sents = [{"text": text}]
    assert has_hero_lines(sents)
    assert any(d["speaker"] == "hero" and "руку" in d["text"] for d in dialog_lines(sents)), "озвучка — голосом героя"
    job, _perf = _job()
    assert job["speech"]["hero_windows"] == [], "рот героя не двигается: говорит копия сверху"


def test_start_state_from_first_beat():
    plan = beat_plan({"spec": {"action": LINES[0][1]}})
    assert plan["action"] == "look_ceiling" and plan["start"] == ["bag", "lid", "torch"], "«на рюкзаке» — не «ставит рюкзак»"
    job, perf = _job()
    assert job["start"] == ["bag", "lid", "torch"]
    assert perf["root_z"][0] == B.BAG_H and perf["bag"][0] == 1 and tuple(perf["root"][0]) == B.BAG_SPOT
    lt = perf["lights"]
    assert abs(lt[0]["hatch"] - LID_AJAR) < 1e-6 and lt[0]["latch"] == 1.0 and lt[0]["torch"] > 0.8
    lid_t = next(e["t"] for e in job["events"] if e["kind"] == "lid")
    assert abs(lt[int((lid_t + 2.0) * 30)]["hatch"] - LID_OPEN) < 1e-6, "приоткрытая крышка поднялась целиком"
    kinds = {x["kind"] for x in B.sfx_for(job, perf)}
    assert not {"bag", "bag_step", "blackout"} & kinds, "на рюкзак не встаёт заново, щелчка блэкаута нет"
    assert perf["torch"].get("arm"), "фонарик держит рука героя-копии"
    fl = next(s for s in perf["shots"] if s["action"] == "phone_light")
    f = int((fl["t0"] + fl["t1"]) / 2 * 30)
    hand = perf["hands"]["l"][f]
    assert hand and hand[0][2] > 0.55 + B.BAG_H * 0.9, "телефон в руке — на высоте рюкзака выше"


def test_chat_with_reply_screens():
    job, perf = _job()
    screens = [s["screen"] for s in perf["shots"] if str(s.get("screen") or "").startswith("msg|")]
    assert len(screens) == 2
    a, b = (x.split("|") for x in screens)
    assert a[1] == "Мама" and a[2] == "" and a[4] == "4G" and a[7].startswith("Ты где?")
    assert b[2] == "Мам, всё ок, я дома" and b[3] == "отправлено 23:41" and b[4] == "нет сети" and b[7] == a[7]
    assert chat_screen([{"text": "Он написал маме, что застрял.", "sentences": [0]}]) == {}


def test_wordless_ending_fall_and_beams():
    job, perf = _job()
    words, total = _words([t for t, _ in LINES])
    speech_end = words[-1]["end"]
    assert abs(job["duration"] - (speech_end + B.outro_sec({"outro": OUTRO}) + 2.0)) < 0.01, "концовка + 2 с на титр"
    n_out = sum(len(o["text"].split()) for o in OUTRO)
    assert job["speech"]["hero_windows"] == [] and len(job["words"]) == len(words) + n_out
    assert job["words"][len(words)]["start"] > speech_end, "немые слова — после речи (их нет в озвучке и субтитрах)"
    kinds = [e["kind"] for e in job["events"]]
    assert kinds.count("blackout") == 1 and {"lights_out", "body_drop", "beams", "msg_in", "jolt"} <= set(kinds)
    t_out = next(e["t"] for e in job["events"] if e["kind"] == "lights_out")
    t_drop = next(e["t"] for e in job["events"] if e["kind"] == "body_drop")
    t_beam = next(e["t"] for e in job["events"] if e["kind"] == "beams")
    assert speech_end < t_drop < t_beam < job["duration"] - 2.0, "падение и лучи — после последнего слова, до титра"
    lt = perf["lights"][int((t_out + 0.5) * 30)]
    assert lt["out"] == 1.0 and lt["red"] == 0.0 and lt["main"] == 0.0
    assert lt["torch"] > 0.8, "свет кабины погас, фонарики горят"
    assert perf["lights"][int((t_out - 0.5) * 30)]["red"] > 0.3, "до темноты — аварийный красный"
    fall = next(s for s in perf["shots"] if s["t0"] <= t_drop < s["t1"])
    assert fall["action"] == "phone_light" and fall["subject"] == "hatch", "падение снято снизу на люк, его луч туда"
    f = int((t_drop + 0.9) * 30)
    assert perf["phone"][f]["light"] > 0.9, "его фонарик светит, когда тело падает"
    # тело: сначала ноги сползают в проём, потом падает и ложится на пол — не на него
    z_top = perf["lights"][int((t_drop + 0.05) * 30)]["bp"][2]
    z_hung = perf["lights"][int((t_drop + 0.58) * 30)]["bp"][2]
    end = perf["lights"][-1]["bp"]
    assert z_top > B.H and z_hung < B.H and end[2] < 0.12 and abs(end[3]) > 1.2, "ноги в люке → на полу, лежит"
    assert math.dist(end[:2], perf["root"][-1]) > 0.25, "упал рядом, не в него"
    assert perf["lights"][int((t_drop - 0.1) * 30)]["body"] == 0.0 and "bp" not in perf["lights"][0]
    # чужой фонарик: в руке падающего, на полу крутится, потом луч замер; его луч шарит по стенам
    tps = [perf["lights"][f_].get("tp") for f_ in range(int((t_beam + 0.2) * 30), int((t_beam + 1.6) * 30))]
    assert all(tps) and len({round(math.atan2(tp[4] - tp[1], tp[3] - tp[0]), 1) for tp in tps}) > 8
    beams = [perf["phone"][f_]["beam"] for f_ in range(int((t_beam + 0.2) * 30), int((t_beam + 2.0) * 30))]
    assert max(math.dist(a, b) for a in beams for b in beams) > 1.0, "его луч мечется по кабине"
    t_hit = perf["body_drop"]["t"][0]
    assert abs(t_hit - (t_drop + perf["body_drop"]["hang"] + perf["body_drop"]["fall"])) < 1e-3
    assert perf["lights"][int((t_hit + 0.1) * 30)]["shake"] > 0.01
    sfx = B.sfx_for(job, perf)
    hit = [x for x in sfx if x["kind"] == "body_drop"]
    assert hit and abs(hit[0]["t"] - t_hit) < 0.01
    assert {"msg_in", "lights_out", "hatch_frame", "body_low", "body_floor", "lid", "lid_stop"} <= {x["kind"] for x in sfx}
    lid = next(x for x in sfx if x["kind"] == "lid")
    assert "metal-scrape" not in lid["ref"], "звук люка заменён (владелец 20.11)"
    bed = next(x for x in sfx if x["kind"] == "dark")
    assert bed["end"] <= t_out + 0.61, "после темноты — тишина, гул шахты стих"
    assert not [x for x in sfx if x["t"] > t_hit + 0.2], "после удара — ни звука"


def test_chat_with_reply_is_full_frame_insert():
    job, perf = _job()
    cams = {c["n"]: c for c in perf["cameras"]}
    chat = [s for s in perf["shots"] if str(s.get("screen") or "").count("|") >= 7]
    assert len(chat) == 2 and all(cams[s["n"]].get("insert") == "phone" for s in chat)
    fx = B.shot_fx(perf)
    assert not any(fx[s["n"]]["blur"] for s in chat), "вставка экрана — без размытия движения"
    assert not any(c.get("insert") for n, c in cams.items() if n not in {s["n"] for s in chat})


def test_outro_cleaned_and_kept_in_script():
    from app.stories_disaster import clean_outro

    got = clean_outro([{"text": "  Тело упало.  ", "sec": 30, "plan": "вздрагивает. План: сверху"}, {"text": ""}, "x",
                       {"text": "Лучи.", "sec": "abc"}])
    assert got == [{"text": "Тело упало.", "sec": 8.0, "plan": "вздрагивает. План: сверху"},
                   {"text": "Лучи.", "sec": 2.5, "plan": ""}]
    assert B.outro_sec({"outro": got}) == 10.5 and B.outro_sec({}) == 0.0


def test_outro_authored_camera():
    from app.stories_disaster import clean_outro

    cam = {"pos": [-0.72, -0.62, 0.85], "point": [0.3, 0.66, 1.2], "lens": 16, "fstop": 8, "face_w": 0, "kind": "detail",
           "junk": 1}
    got = clean_outro([{"text": "Тот, кто был сверху, рухнул сквозь люк в кабину.", "sec": 2.8,
                        "plan": "светит вверх, страх. План: деталь, люк", "cam": cam},
                       {"text": "Тишина.", "cam": {"pos": "x"}}])
    assert got[0]["cam"] == {"pos": [-0.72, -0.62, 0.85], "point": [0.3, 0.66, 1.2], "lens": 16.0, "fstop": 8.0,
                             "face_w": 0.0, "kind": "detail"} and "cam" not in got[1]
    script = {"title": "t", "sentences": [{"text": t} for t, _ in LINES], "outro": got[:1]}
    words, total = _words([t for t, _ in LINES])
    job = B.build_job(script, words, total, gemini=False, beats=_beats(), render_backend="threejs")
    perf = B.perform(job)
    last = perf["cameras"][-1]
    assert job["shots"][-1].get("cam") and last["lens"] == 16.0 and last["pos_a"] == (-0.72, -0.62, 0.85)


def test_old_series_untouched():
    script = {"title": "t", "sentences": [{"text": "Если блэкаут застал вас в лифте, не паникуйте."},
                                          {"text": "Свет погас. Полная темнота."}, {"text": "Даня упал на пол."}]}
    words, total = _words([s["text"] for s in script["sentences"]])
    job = B.build_job(script, words, total, gemini=False)
    perf = B.perform(job)
    assert "start" not in job and not {"lights_out", "msg_in"} & {e["kind"] for e in job["events"]}
    assert "out" not in perf["lights"][0] and "body" not in perf["lights"][0] and "torch" not in perf
    assert "dim" not in perf["phone"][-1]
