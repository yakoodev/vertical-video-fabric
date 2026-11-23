"""Звук 3D-серии от автора (app/series3d_blender_audio.py): фон места, звуки событий по умолчанию и их замена, свои
звуки фраз, фото в переписке — в задании ``build_job``. Без Blender."""

from __future__ import annotations

from app import series3d_blender as B
from app import series3d_blender_audio as BA
from app import series3d_stairs as ST
from test_series3d_stairs import _job, _words

LINES = [{"text": "Телефон: семь процентов."}, {"text": "Приходит сообщение от мамы:"},
         {"text": "«Даня, ты где? Я слышу тебя в твоей комнате»."}]


def _plain(extra: dict | None = None, sentences: list[dict] | None = None, **kw):
    sent = [dict(s) for s in (sentences or LINES)]
    words, total = _words(sent)
    script = {"title": "т", "sentences": sent, **(extra or {})}
    return B.build_job(script, words, total, gemini=False, render_backend="threejs", **kw)


def _starts(job):
    return BA.sentence_starts(job["shots"])


def test_old_script_job_has_no_new_fields():
    job = _plain(sentences=[{"text": "Кабина встала между этажами."}, {"text": "Свет мигает."}])
    assert not {"ambience", "event_sfx", "photos"} & set(job)
    assert not [e for e in job["events"] if e["kind"] in ("flick_click", "dark_hit", "sound", "vibrate")]


def test_message_also_vibrates_phone():
    job = _plain()
    msg = [e for e in job["events"] if e["kind"] == "msg_in"]
    vib = [e for e in job["events"] if e["kind"] == "vibrate"]
    assert len(msg) == 1 and vib == [{"kind": "vibrate", "t": round(msg[0]["t"], 3)}]
    perf = B.perform(job)
    f = int((msg[0]["t"] + 0.1) * 30)
    assert any(perf["phone"][k].get("vib") for k in range(f, f + 15))
    labels = [x["label"] for x in B.sfx_for(job, perf)]
    assert "пришло сообщение" in labels and "телефон вибрирует" in labels
    # автор выключил звук сообщения — вибрация осталась, дзынь нет
    muted = _plain({"event_sfx": {"msg_in": False}})
    labels = [x["label"] for x in B.sfx_for(muted, B.perform(muted))]
    assert "пришло сообщение" not in labels and "телефон вибрирует" in labels


def test_flashlight_flicker_clicks_and_darkness_hits():
    job = _job()
    sh = next(s for s in job["shots"] if isinstance(s.get("flicker"), dict))
    clicks = [e for e in job["events"] if e["kind"] == "flick_click"]
    want = [round(a, 3) for a, _b, v in ST.flicker_spans(sh["t0"] + 0.3, sh["t0"] + 1.2)[:-1] if v == 0.0]
    assert [e["t"] for e in clicks] == want and clicks[0]["sfx"] == BA.FLICKER_CLICK[0]
    hit = [e for e in job["events"] if e["kind"] == "dark_hit"]
    assert len(hit) == 1 and hit[0]["t"] == round(sh["t0"] + 1.2, 3)
    assert job["events"] == sorted(job["events"], key=lambda e: float(e["t"]))
    sfx = B.sfx_for(job, B.perform(job))
    assert sum(1 for x in sfx if x["kind"] == "flick_click") == len(clicks)
    # замена записи и выключение удара
    job["event_sfx"] = {"flick_click": {"ref": "sfx:kenney-interface/glitch_001.ogg", "volume": 0.3}, "dark_hit": False}
    sfx = B.sfx_for(job, B.perform(job))
    assert {x["ref"] for x in sfx if x["kind"] == "flick_click"} == {"sfx:kenney-interface/glitch_001.ogg"}
    assert not [x for x in sfx if x["kind"] == "dark_hit"]


def test_sentence_sounds_become_events():
    sent = [dict(LINES[0], sounds=[{"ref": "sfx:kenney-impact/impactSoft_medium_002.ogg", "at": 0.4, "volume": 0.5,
                                    "label": "шорох"}]),
            dict(LINES[1], sounds=[{"event": "knock", "at": 0.2}]), LINES[2]]
    job = _plain(sentences=sent)
    st = _starts(job)
    snd = next(e for e in job["events"] if e["kind"] == "sound")
    assert snd == {"kind": "sound", "t": round(st[0] + 0.4, 3), "sfx": "sfx:kenney-impact/impactSoft_medium_002.ogg",
                   "volume": 0.5, "label": "шорох", "sentence": 0}
    assert any(e["kind"] == "knock" and e["t"] == round(st[1] + 0.2, 3) for e in job["events"])
    labels = [x["label"] for x in B.sfx_for(job, B.perform(job))]
    assert "шорох" in labels and "стук" in labels


def test_author_ambience_replaces_lift_beds():
    amb = [{"bed": "stairwell", "to": 1, "to_at": 0.5, "fade_in": 0.6, "fade_out": 0.4},
           {"ref": "sfx:tension/bed-ominous-deep.ogg", "from": 2, "at": -0.5, "under_db": 18.0, "label": "низ"}]
    job = _plain({"ambience": amb})
    st = _starts(job)
    assert job["ambience"] == [
        {"ref": "sfx:tension/bed-space-drone.ogg", "t": 0.0, "end": round(st[1] + 0.5, 3), "under_db": 20.0,
         "fade_in": 0.6, "fade_out": 0.4, "label": "гул лестницы"},
        {"ref": "sfx:tension/bed-ominous-deep.ogg", "t": round(st[2] - 0.5, 3), "end": job["duration"], "under_db": 18.0,
         "fade_in": 0.6, "fade_out": 0.6, "label": "низ"}]
    beds = [x for x in B.sfx_for(job, B.perform(job)) if x.get("role") == "ambience"]
    assert [b["label"] for b in beds] == ["гул лестницы", "низ"]
    # пустой список — тишина: ни гула кабины, ни гула шахты
    quiet = _plain({"ambience": []})
    assert quiet["ambience"] == [] and not [x for x in B.sfx_for(quiet, B.perform(quiet)) if x.get("role") == "ambience"]


def test_photos_reach_job_and_chunk_keys(tmp_path):
    png = tmp_path / "room.png"
    png.write_bytes(b"x")
    job = _plain(photos={"room": str(png)})
    assert job["photos"] == {"room": str(png)}
    perf = B.perform(job)
    keys = {c["key"] for c in B.chunk_keys(job, perf)}
    job2 = dict(job, photos={"room": str(tmp_path / "other.png")})
    assert keys.isdisjoint({c["key"] for c in B.chunk_keys(job2, perf)})
