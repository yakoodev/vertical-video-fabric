"""3D-серия, ч.4 «Кто светит сверху»: продолжение с рюкзака под приоткрытым люком, рука-копия с фонариком, голос героя
сверху без губ, переписка с ответом мамы, полная темнота, тело падает в кабину. Поля новых дорожек — только у серий,
где они есть."""
from __future__ import annotations

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
    ("Заряд 9%.", "вздрагивает. План: сверху"),
    ("Кабина дёрнулась.", None),
    ("И свет пропал. Полная темнота.", "смотрит вверх, страх. План: лицо"),
    ("В темноте тот, кто был сверху, рухнул в кабину.", "вздрагивает, страх. План: сверху"),
    ("Тяжёлый удар об пол, прямо рядом с Даней.", None),
    ("Тишина.", None),
]


def _beats() -> list[dict]:
    return [{"i": i, "text": t, "sentence": i, "spec": {"action": b}} for i, (t, b) in enumerate(LINES) if b]


def _job() -> tuple[dict, dict]:
    script = {"title": "Кто светит сверху", "sentences": [{"text": t} for t, _ in LINES]}
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


def test_lights_out_and_body_drop():
    job, perf = _job()
    kinds = [e["kind"] for e in job["events"]]
    assert kinds.count("blackout") == 1 and "lights_out" in kinds and "body_drop" in kinds and "msg_in" in kinds
    drop_s = next(e["sentence"] for e in job["events"] if e["kind"] == "body_drop")
    assert not any(e["kind"] == "jolt" and e["sentence"] in (drop_s, drop_s + 1) for e in job["events"]), \
        "«рухнул», «удар об пол» — не рывок кабины"
    t_out = next(e["t"] for e in job["events"] if e["kind"] == "lights_out")
    lt = perf["lights"][int((t_out + 0.5) * 30)]
    assert lt["out"] == 1.0 and lt["red"] == 0.0 and lt["torch"] == 0.0 and lt["main"] == 0.0
    assert perf["lights"][int((t_out - 0.5) * 30)]["red"] > 0.3, "до темноты — аварийный красный"
    f = int((t_out + 0.5) * 30)
    assert perf["phone"][f]["light"] == 0.0
    t_hit = perf["body_drop"]["t"][0]
    assert perf["lights"][int((t_hit + 0.1) * 30)]["shake"] > 0.01 and perf["lights"][-1]["body"] == 1.0
    assert perf["lights"][0]["body"] == 0.0
    sfx = B.sfx_for(job, perf)
    hit = [x for x in sfx if x["kind"] == "body_drop"]
    assert hit and abs(hit[0]["t"] - t_hit) < 0.01
    assert {"msg_in", "lights_out", "body_low", "body_floor"} <= {x["kind"] for x in sfx}
    bed = next(x for x in sfx if x["kind"] == "dark")
    assert bed["end"] <= t_out + 0.61, "после полной темноты — тишина, гул шахты стих"


def test_old_series_untouched():
    script = {"title": "t", "sentences": [{"text": "Если блэкаут застал вас в лифте, не паникуйте."},
                                          {"text": "Свет погас. Полная темнота."}, {"text": "Даня упал на пол."}]}
    words, total = _words([s["text"] for s in script["sentences"]])
    job = B.build_job(script, words, total, gemini=False)
    perf = B.perform(job)
    assert "start" not in job and not {"lights_out", "msg_in"} & {e["kind"] for e in job["events"]}
    assert "out" not in perf["lights"][0] and "body" not in perf["lights"][0] and "torch" not in perf
    assert "dim" not in perf["phone"][-1]
