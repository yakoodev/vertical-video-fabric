"""3D-серия, ч.3 «Кто на крыше»: блэкаут уже идёт, экран переписки, шаги сверху, рюкзак под люком, защёлка сама,
крышка приподнялась, фонарик в щели. Поля новых дорожек — только у серий, где они есть."""
from __future__ import annotations

import re

from app import series3d_blender as B
from app.series3d_blender_direction import message_screen
from app.series3d_blender_perform import LID_AJAR


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
    ("Если в блэкаут кто-то над кабиной знает ваше имя, не открывайте люк.", "смотрит вверх, тревога. План: лицо"),
    ("Он написал маме, что застрял на 23 этаже.", "телефон, тревога. План: деталь"),
    ("Сообщение не ушло.", "телефон, страх. План: деталь"),
    ("На экране: нет сети, заряд 11%.", None),
    ("Над головой послышались шаги.", "смотрит вверх, страх. План: лицо"),
    ("Он крикнул: «Назовите номер вашей бригады».", "кричит вверх, решительно. План: лицо, люк"),
    ("Даня снял рюкзак, поставил под люк и встал на него.", "ставит рюкзак, решительно. План: сверху"),
    ("Потянулся к защёлке, и она повернулась сама.", "тянется к люку, страх. План: деталь, люк"),
    ("Крышка медленно приподнялась.", "смотрит вверх, страх. План: деталь, люк"),
    ("В щель светил фонарик.", "смотрит вверх, страх. План: лицо"),
]


def _beats() -> list[dict]:
    return [{"i": i, "text": t, "sentence": i, "spec": {"action": b}} for i, (t, b) in enumerate(LINES) if b]


def _roof_job() -> tuple[dict, dict]:
    script = {"title": "Кто на крыше", "sentences": [{"text": t} for t, _ in LINES]}
    words, total = _words([t for t, _ in LINES])
    job = B.build_job(script, words, total, gemini=False, beats=_beats(), render_backend="threejs")
    return job, B.perform(job)


def test_dark_from_start_and_no_breaker_click():
    job, perf = _roof_job()
    assert next(e["t"] for e in job["events"] if e["kind"] == "blackout") == -1.0, "«в блэкаут» — свет погас до серии"
    assert perf["lights"][0]["red"] > 0.5 and perf["lights"][0]["main"] < 0.05, "первый кадр — уже аварийный красный"
    kinds = {x["kind"] for x in B.sfx_for(job, perf)}
    assert "blackout" not in kinds and "power_down" not in kinds
    assert {"steps", "latch", "lid", "bag_step"} <= kinds


def test_message_screen_states_and_status():
    job, perf = _roof_job()
    screens = [s.get("screen") for s in perf["shots"] if s.get("screen")]
    assert len(screens) == 2
    assert screens[0].split("|")[:4] == ["msg", "Мама", "застрял на 23 этаже", "отправка…"]
    assert screens[1].split("|")[3:6] == ["не доставлено", "нет сети", "11%"]
    assert any(p.get("screen", "").startswith("msg|") for p in perf["phone"])
    assert message_screen([{"text": "На табло 22.", "sentences": [0]}]) == {}


def test_bag_root_height_and_hand_reach():
    job, perf = _roof_job()
    stand = next(s for s in perf["shots"] if s["action"] == "stand_bag")
    f_end = len(perf["root"]) - 1
    assert perf["root_z"][f_end] == B.BAG_H and perf["root_z"][int(stand["t0"] * 30)] == 0.0
    assert perf["bag"][f_end] == 1 and perf["bag"][0] == 0
    assert tuple(perf["root"][f_end]) == B.BAG_SPOT, "на рюкзаке стоит под люком и никуда не уходит"
    reach = next(s for s in perf["shots"] if s["action"] == "open_hatch")
    up = [h for h in perf["hands"]["r"][int(reach["t0"] * 30):int(reach["t1"] * 30)] if h and h[2] == "hatch"]
    assert up and max(p[0][2] for p in up) > 1.0, "тянется вверх с рюкзака"
    assert not any(c["kind"] == "hatch" for c in perf["contacts"]), "защёлка сама: крышку он не толкает"


def test_latch_lid_and_torch_tracks():
    job, perf = _roof_job()
    lt = perf["lights"]
    assert lt[-1]["latch"] == 1.0 and abs(lt[-1]["hatch"] - LID_AJAR) < 1e-6, "крышка только приподнята"
    assert lt[0]["torch"] == 0.0 and lt[-1]["torch"] > 0.8
    assert perf["torch"]["pos"][2] > B.H and perf["torch"]["aim"][2] > 0.9, "фонарик над щелью светит в лицо на рюкзаке"
    cams = {c["n"]: c for c in perf["cameras"]}
    hatch_shots = [s for s in perf["shots"] if s.get("subject") == "hatch"]
    assert hatch_shots and all(cams[s["n"]]["kind"] == "detail" for s in hatch_shots)
    hero = [s for s in perf["shots"] if s.get("speaks")]
    assert len(hero) == 1 and "бригады" in hero[0]["text"]


def test_old_episode_fields_unchanged():
    script = {"title": "t", "sentences": [{"text": "Если блэкаут застал вас в лифте, не паникуйте."},
                                          {"text": "Даня нажал кнопку вызова."}]}
    words, total = _words([s["text"] for s in script["sentences"]])
    job = B.build_job(script, words, total, gemini=False)
    perf = B.perform(job)
    assert not {"root_z", "bag", "torch"} & set(perf), "у старых серий новых дорожек нет"
    assert "torch" not in perf["lights"][0] and next(e["t"] for e in job["events"] if e["kind"] == "blackout") > 0
